"""What an agent needs to know: its location's events, their sets, and the files in them."""
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .models import MediaAsset, MediaSet, ScreenGrant, Service
from .signing import public_key_b64


def asset_entry(asset):
    entry = {
        "id": asset.pk,
        "kind": asset.kind,
        "owner": asset.owner_name,
        "owner_slug": asset.owner_slug,
        "name": asset.original_name,
        "local_name": asset.local_name,
        "sha256": asset.sha256,
        "size": asset.processed_size,
        "duration_ms": asset.duration_ms,
        "width": asset.width,
        "height": asset.height,
        "file": reverse("agent-file", args=[asset.pk, "processed"]),
        "thumb": reverse("agent-file", args=[asset.pk, "thumb"]),
        "filmstrip": reverse("agent-file", args=[asset.pk, "filmstrip"]) if asset.filmstrip else "",
        "filmstrip_count": asset.filmstrip_count,
        "filmstrip_cols": asset.filmstrip_cols,
        "filmstrip_interval_ms": asset.filmstrip_interval_ms,
        "markers": [{"id": m.pk, "label": m.label, "t_ms": m.t_ms} for m in asset.markers.all()],
    }
    return entry


def events_for(agent):
    """Active events of the agent's location, from yesterday up to VITRINE_PRESYNC_DAYS ahead.

    Today's (or the next) event comes first: the agent shows the first one unless
    someone picks another on the back tablet.
    """
    location = getattr(agent, "location", None)
    if location is None:
        return []
    today = timezone.localdate()
    events = Service.objects.filter(location=location, is_active=True).overlapping(
        today - timedelta(days=1), today + timedelta(days=settings.VITRINE_PRESYNC_DAYS)
    )
    return sorted(events, key=lambda e: e.sort_key(today))


def build_manifest(agent):
    services = list(events_for(agent))
    sets = (
        MediaSet.objects.filter(service__in=services)
        .select_related("guest__person")
        .prefetch_related("items__asset")
        .order_by("service_id", "title")
    )

    set_entries = []
    asset_ids = set()
    for media_set in sets:
        playable = []
        for item in media_set.items.all():
            if item.asset.is_playable:
                playable.append(item.asset_id)
                asset_ids.add(item.asset_id)
        set_entries.append(
            {
                "id": media_set.pk,
                "service_id": media_set.service_id,
                "title": media_set.title,
                "guest": media_set.guest.person.name if media_set.guest else media_set.title,
                "items": playable,
            }
        )

    assets = MediaAsset.objects.filter(pk__in=asset_ids).prefetch_related("markers")
    revoked = list(ScreenGrant.objects.filter(agent=agent, revoked=True).values_list("nonce", flat=True))

    return {
        "version": agent.current_manifest_version,
        "server_time": timezone.now().isoformat(),
        "agent": {"id": agent.pk, "name": agent.name},
        "location": (
            {"id": agent.location.pk, "name": agent.location.name, "organization": agent.location.organization.name}
            if getattr(agent, "location", None) else None
        ),
        "pubkey": public_key_b64(),
        "services": [
            {
                "id": s.pk,
                "name": s.name,
                # "date" is the start, kept for agents before 0.1.9.
                "date": s.starts_on.isoformat() if s.starts_on else "",
                "ends_on": s.ends_on.isoformat() if s.ends_on else "",
                "when": s.when,
                "short_when": s.short_when,
            }
            for s in services
        ],
        "sets": set_entries,
        "assets": [asset_entry(a) for a in assets],
        "revoked_screens": revoked,
    }
