from collections import defaultdict
import logging
import shutil
from datetime import timedelta
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.core.files import File
from django.utils import timezone

from .models import MediaAsset
from .processing import process_file

logger = logging.getLogger(__name__)


def _store(asset, field_name, path, name):
    with open(path, "rb") as handle:
        getattr(asset, field_name).save(name, File(handle), save=False)


@shared_task(bind=True, max_retries=1)
def process_asset(self, asset_id):
    asset = MediaAsset.all_objects.filter(pk=asset_id).first()
    if asset is None or not asset.original:
        return
    asset.status = MediaAsset.STATUS_PROCESSING
    asset.error = ""
    asset.save(update_fields=["status", "error"])

    from . import config, progress

    reporter = progress.Reporter(asset.pk)
    try:
        result = process_file(asset.original.path, asset.original_name, asset.owner_name,
                              progress=reporter, preset=config.video_preset())
    except Exception as exc:
        reporter.done()
        logger.exception("Processing failed for asset %s", asset_id)
        asset.status = MediaAsset.STATUS_FAILED
        asset.error = str(exc)[-2000:]
        asset.save(update_fields=["status", "error"])
        from . import alerts

        alerts.conversion_failed(asset)
        return

    reporter.done()
    try:
        base = f"library/{asset.pk}"
        for old in (asset.processed, asset.thumb, asset.filmstrip):
            if old:
                old.delete(save=False)
        asset.kind = result["kind"]
        _store(asset, "processed", result["processed"], f"{base}/{asset.local_name}")
        _store(asset, "thumb", result["thumb"], f"{base}/thumb.jpg")
        if result.get("filmstrip"):
            _store(asset, "filmstrip", result["filmstrip"], f"{base}/filmstrip.jpg")
            asset.filmstrip_count = result["filmstrip_count"]
            asset.filmstrip_cols = result["filmstrip_cols"]
            asset.filmstrip_interval_ms = result.get("filmstrip_interval_ms") or settings.VITRINE_FILMSTRIP_INTERVAL * 1000
        asset.processed_size = result["size"]
        asset.sha256 = result["sha256"]
        asset.width = result["width"]
        asset.height = result["height"]
        asset.duration_ms = result["duration_ms"]
        asset.status = MediaAsset.STATUS_READY
        if asset.service and asset.service.auto_approve and asset.approval == MediaAsset.APPROVAL_PENDING:
            asset.approval = MediaAsset.APPROVAL_APPROVED
        asset.save()
    finally:
        shutil.rmtree(result["out_dir"], ignore_errors=True)


def delete_asset_files(asset):
    for field in (asset.original, asset.processed, asset.thumb, asset.filmstrip):
        if field:
            field.delete(save=False)
    folder = Path(settings.MEDIA_ROOT) / "library" / str(asset.pk)
    shutil.rmtree(folder, ignore_errors=True)


@shared_task
def purge_trash():
    """Remove files that have been in the trash longer than VITRINE_TRASH_DAYS."""
    cutoff = timezone.now() - timedelta(days=settings.VITRINE_TRASH_DAYS)
    expired = MediaAsset.all_objects.filter(deleted_at__lt=cutoff)
    count = 0
    for asset in expired:
        delete_asset_files(asset)
        asset.delete()
        count += 1
    logger.info("Purged %s files from the trash", count)
    return count


# ---------- alerts to staff ----------


@shared_task
def check_pending_uploads():
    """One message per organization about new files waiting for approval."""
    from collections import defaultdict

    from . import alerts
    from .models import MediaAsset

    waiting = (
        MediaAsset.objects.filter(
            approval=MediaAsset.APPROVAL_PENDING,
            status=MediaAsset.STATUS_READY,
            pending_notified=False,
            organization__isnull=False,
        )
        .select_related("organization")
        .order_by("created")
    )
    by_org = defaultdict(list)
    for asset in waiting:
        by_org[asset.organization].append(asset)
    for org, assets in by_org.items():
        if org.notify_uploads and alerts.can_send(org):
            people = defaultdict(int)
            for a in assets:
                people[a.owner_name] += 1
            lines = [f"- {name}: {count} file{'s' if count != 1 else ''}" for name, count in people.items()]
            subject = f"{len(assets)} upload{'s' if len(assets) != 1 else ''} waiting for approval"
            link = alerts.manage_url('/manage/library/?approval=pending')
            buttons_sent = _send_upload_buttons(org, assets)
            alerts.notify(
                org, subject, "\n".join(lines) + f"\n\nApprove them: {link}", telegram=not buttons_sent,
            )
        MediaAsset.objects.filter(pk__in=[a.pk for a in assets]).update(pending_notified=True)


TELEGRAM_BUTTONS_MAX = 10


def _send_upload_buttons(org, assets):
    """In Telegram, one message per upload with Approve / Reject buttons (the first 10; then a link
    for the rest). Returns False when Telegram isn't set up or failed, so the caller sends plain text."""
    from . import alerts, config, telegram

    if not (config.telegram_token() and org.notify_telegram_chat):
        return False
    try:
        for asset in assets[:TELEGRAM_BUTTONS_MAX]:
            telegram.send_upload(org.notify_telegram_chat, asset)
        rest = len(assets) - TELEGRAM_BUTTONS_MAX
        if rest > 0:
            telegram.send(
                org.notify_telegram_chat,
                f"…and {rest} more waiting: {alerts.manage_url('/manage/library/?approval=pending')}",
            )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("telegram upload buttons for %s failed: %s", org, exc)
        return False
    return True


def _event_soon(location):
    from .models import Service

    today = timezone.localdate()
    events = Service.objects.filter(location=location, is_active=True).overlapping(today, today + timedelta(days=1))
    return min(events, key=lambda e: e.sort_key(today), default=None)


@shared_task
def check_agents():
    """Tell staff when a vMix PC goes offline (and when it's back) around an event."""
    from . import alerts
    from .models import Location

    from . import config

    limit = timezone.now() - timedelta(minutes=config.offline_alert_minutes())
    for location in Location.objects.select_related("agent", "organization").filter(agent__isnull=False):
        agent, org = location.agent, location.organization
        offline = agent.last_seen is not None and agent.last_seen < limit
        if offline and not agent.offline_notified:
            event = _event_soon(location)
            if event and org.notify_offline and alerts.can_send(org):
                alerts.notify(
                    org,
                    f"{location.name}: vMix PC offline",
                    f"The Vitrine agent at {location.name} hasn't been in touch since "
                    f"{timezone.localtime(agent.last_seen):%H:%M} ({event.name}, {event.when}).\n"
                    "Tablets keep working on the local network, but new uploads won't arrive. "
                    "Check that the PC is on and has internet.",
                )
                agent.offline_notified = True
                agent.save(update_fields=["offline_notified"])
        elif not offline and agent.offline_notified:
            if org.notify_offline and alerts.can_send(org):
                alerts.notify(org, f"{location.name}: vMix PC back online", f"The Vitrine agent at {location.name} is back.")
            agent.offline_notified = False
            agent.save(update_fields=["offline_notified"])


def sync_report(location, event):
    """(ok, text) about whether the event's files are on the location's vMix PC."""
    from .models import MediaAsset, SetItem

    agent = location.agent
    ids = set(
        SetItem.objects.filter(media_set__service=event, asset__status=MediaAsset.STATUS_READY,
                               asset__approval=MediaAsset.APPROVAL_APPROVED, asset__deleted_at__isnull=True)
        .values_list("asset_id", flat=True)
    )
    pending = MediaAsset.objects.filter(
        set_items__media_set__service=event, approval=MediaAsset.APPROVAL_PENDING
    ).distinct().count()
    when = f"{event.name}, {event.when}"
    if agent is None:
        return False, f"{when}: {location.name} has no vMix PC (agent) yet, so nothing is downloaded."
    if not agent.is_online:
        seen = f"since {timezone.localtime(agent.last_seen):%a %H:%M}" if agent.last_seen else "never seen"
        return False, f"{when}: the vMix PC at {location.name} is offline ({seen}). {len(ids)} file(s) to download."
    missing = set((agent.status or {}).get("sync", {}).get("missing") or [])
    missing_here = ids & missing
    extra = f"\n{pending} upload(s) still wait for approval and won't be shown until approved." if pending else ""
    if not ids:
        return True, f"{when}: no files in the sets yet.{extra}"
    if missing_here:
        return False, f"{when}: {len(ids) - len(missing_here)} of {len(ids)} files are on the vMix PC, {len(missing_here)} still downloading.{extra}"
    return True, f"{when}: all {len(ids)} files are on the vMix PC at {location.name}.{extra}"


@shared_task
def check_sync():
    """The day before: are all the files of tomorrow's events on their vMix PC?"""
    from . import alerts
    from .models import Service

    tomorrow = timezone.localdate() + timedelta(days=1)
    # Events that start tomorrow (a several-day event is checked the day before it starts).
    events = Service.objects.filter(is_active=True, starts_on=tomorrow, location__isnull=False).select_related(
        "location__agent", "location__organization"
    )
    for event in events:
        org = event.location.organization
        if not (org.notify_sync and alerts.can_send(org)):
            continue
        ok, text = sync_report(event.location, event)
        alerts.notify(org, f"{event.location.name}: {'ready for' if ok else 'not ready for'} tomorrow", text)


@shared_task
def check_sync_due():
    """Every few minutes: run the "ready for tomorrow?" check once a day at its time."""
    from . import config
    from .models import SiteSettings

    now = timezone.localtime()
    hour, minute = config.parse_time(config.sync_check_time(), (18, 0))
    if (now.hour, now.minute) < (hour, minute):
        return
    s = SiteSettings.load()
    if s.last_sync_check_day == now.date():
        return
    SiteSettings.objects.filter(pk=s.pk).update(last_sync_check_day=now.date())
    check_sync()


@shared_task
def check_summary_due():
    """Every few minutes: send the morning summary once a day at its time."""
    from . import config
    from .models import SiteSettings

    now = timezone.localtime()
    hour, minute = config.parse_time(config.summary_time(), (8, 0))
    if (now.hour, now.minute) < (hour, minute):
        return
    s = SiteSettings.load()
    if s.last_summary_day == now.date():
        return
    SiteSettings.objects.filter(pk=s.pk).update(last_summary_day=now.date())
    morning_summary()


def summary_text(event):
    """The summary of one event today: vMix PC, who has a set, what waits, what failed."""
    from .models import MediaAsset, SetItem

    lines = [f"📅 {event.name} · {event.location.name} ({event.when})"]
    _ok, sync = sync_report(event.location, event)
    lines.append(sync.split(": ", 1)[-1].split("\n")[0])
    counts = defaultdict(int)
    for name in SetItem.objects.filter(
        media_set__service=event, asset__deleted_at__isnull=True
    ).values_list("media_set__guest__person__name", flat=True):
        counts[name or "Team"] += 1
    guests = list(event.guests.select_related("person"))
    with_files = [f"{name} ({n})" for name, n in sorted(counts.items())]
    without = [g.person.name for g in guests if g.person.name not in counts]
    lines.append("Sets: " + (", ".join(with_files) if with_files else "nobody has files yet"))
    if without:
        lines.append("No files yet: " + ", ".join(without))
    assets = MediaAsset.objects.filter(set_items__media_set__service=event).distinct()
    pending = assets.filter(approval=MediaAsset.APPROVAL_PENDING).count()
    failed = assets.filter(status=MediaAsset.STATUS_FAILED).count()
    if pending:
        lines.append(f"⏳ {pending} waiting for approval")
    if failed:
        lines.append(f"⚠️ {failed} couldn't be converted")
    return "\n".join(lines)


@shared_task
def morning_summary():
    """On event days: one message per organization about today's events."""
    from . import alerts
    from .models import Service

    today = timezone.localdate()
    events = (
        Service.objects.filter(is_active=True, location__isnull=False)
        .overlapping(today, today)
        .select_related("location__agent", "location__organization")
    )
    by_org = defaultdict(list)
    for event in events:
        by_org[event.location.organization].append(event)
    for org, org_events in by_org.items():
        if not (org.notify_summary and alerts.can_send(org)):
            continue
        org_events.sort(key=lambda e: e.sort_key(today))
        text = "\n\n".join(summary_text(e) for e in org_events)
        title = f"Today: {len(org_events)} event{'s' if len(org_events) != 1 else ''}"
        alerts.notify(org, title, text + f"\n\n{alerts.manage_url('/manage/')}")
