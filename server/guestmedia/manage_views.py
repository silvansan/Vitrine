"""Staff backend: services, guest links, approvals, library, agents, tablet pairing."""
import io
from datetime import timedelta
from urllib.parse import urlencode

import qrcode
import qrcode.image.svg
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import models
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .files import serve_field
from .models import (
    Agent,
    Guest,
    Location,
    Marker,
    MediaAsset,
    MediaSet,
    Organization,
    Person,
    ScreenGrant,
    Service,
    SetItem,
    new_guest_token,
)
from .orgs import check_org, org_view
from .notify import notify_agents
from .signing import sign_pairing
from .tasks import delete_asset_files, process_asset


class EventForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = ["name", "date", "location", "is_active", "auto_approve"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}
        labels = {"is_active": "Active"}

    def __init__(self, *args, org=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = Location.objects.filter(organization=org)
        self.fields["location"].required = True


class PersonForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = ["name", "can_upload", "can_browse", "expires_at"]
        widgets = {"expires_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}


def org_events(org):
    return Service.objects.filter(location__organization=org).select_related("location")


def upcoming_events(org):
    yesterday = timezone.localdate() - timedelta(days=1)
    return org_events(org).filter(date__gte=yesterday).order_by("date", "id")


def add_to_event(person, event):
    guest, created = Guest.objects.get_or_create(service=event, person=person)
    guest.get_set()
    return guest, created


def parse_time(text):
    """'1:12' or '72' -> milliseconds."""
    text = (text or "").strip()
    if not text:
        return 0
    parts = text.split(":")
    try:
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + float(part)
    except ValueError:
        return 0
    return int(seconds * 1000)


@org_view
def switch_org(request):
    org_id = request.POST.get("org") or request.GET.get("org")
    if org_id and int(org_id) in {o.pk for o in request.orgs}:
        request.session["vitrine_org"] = int(org_id)
    return redirect(request.POST.get("next") or "manage-dashboard")


@org_view
def dashboard(request):
    org = request.org
    if request.method == "POST":
        form = EventForm(request.POST, org=org)
        if form.is_valid():
            event = form.save()
            messages.success(request, f"Created {event}.")
            return redirect("manage-service", event.pk)
    else:
        form = EventForm(org=org, initial={"location": org.locations.first()})
    events = org_events(org).annotate(guest_count=Count("guests", distinct=True))
    location = request.GET.get("location", "")
    if location.isdigit():
        events = events.filter(location_id=int(location))
    unplaced = Service.objects.filter(location__isnull=True) if request.user.is_superuser else Service.objects.none()
    pending = (
        MediaAsset.objects.filter(approval=MediaAsset.APPROVAL_PENDING, organization=org)
        .exclude(status=MediaAsset.STATUS_FAILED)
        .count()
    )
    from . import backup

    return render(
        request,
        "guestmedia/manage/dashboard.html",
        {
            "backup_warning": request.user.is_superuser and (not backup.enabled() or backup.is_stale()),
            "backup_enabled": backup.enabled(),
            "events": events[:80],
            "unplaced": unplaced,
            "form": form,
            "pending": pending,
            "locations": org.locations.select_related("agent"),
            "location_filter": location,
            "today": timezone.localdate(),
        },
    )


@org_view
def service_detail(request, pk):
    event = get_object_or_404(Service.objects.select_related("location__organization"), pk=pk)
    check_org(request, event.organization)
    org = event.organization or request.org
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update":
            form = EventForm(request.POST, instance=event, org=org)
            if form.is_valid():
                form.save()
                messages.success(request, "Saved.")
            else:
                messages.error(request, "Please check the event settings.")
        elif action == "add_person":
            person = None
            if request.POST.get("person_id", "").isdigit():
                person = get_object_or_404(Person, pk=request.POST["person_id"], organization=org)
            elif request.POST.get("name", "").strip():
                person = Person.objects.create(organization=org, name=request.POST["name"].strip()[:120])
            if person:
                _, created = add_to_event(person, event)
                messages.success(request, f"{person.name} is in this event." if created else f"{person.name} was already in this event.")
        elif action == "remove_guest":
            guest = get_object_or_404(Guest, pk=request.POST.get("guest_id"), service=event)
            guest.delete()
            messages.success(request, f"{guest.person.name} removed from this event. Their link and uploads stay.")
        elif action == "rename_set":
            media_set = get_object_or_404(MediaSet, pk=request.POST.get("set_id"), service=event)
            media_set.title = request.POST.get("title", media_set.title)[:200]
            media_set.save(update_fields=["title"])
        return redirect("manage-service", event.pk)

    guests = list(event.guests.select_related("person"))
    for guest in guests:
        guest.public_link = guest.person.link_for(request)
        guest.other_events = guest.person.guests.exclude(pk=guest.pk).count()
    in_event = {g.person_id for g in guests}
    sets = MediaSet.objects.filter(service=event).select_related("guest__person").prefetch_related("items__asset")
    pending = (
        MediaAsset.objects.filter(approval=MediaAsset.APPROVAL_PENDING)
        .filter(Q(service=event) | Q(set_items__media_set__service=event))
        .exclude(status=MediaAsset.STATUS_FAILED)
        .distinct()
    )
    return render(
        request,
        "guestmedia/manage/service.html",
        {
            "service": event,
            "service_form": EventForm(instance=event, org=org),
            "guests": guests,
            "people": Person.objects.filter(organization=org).exclude(pk__in=in_event),
            "sets": sets,
            "pending": pending,
        },
    )


@org_view
def people(request):
    org = request.org
    if request.method == "POST":
        form = PersonForm(request.POST)
        if form.is_valid():
            person = form.save(commit=False)
            person.organization = org
            person.save()
            event_id = request.POST.get("event_id", "")
            if event_id.isdigit():
                add_to_event(person, get_object_or_404(org_events(org), pk=event_id))
            messages.success(request, f"Link created for {person.name}.")
            return redirect("manage-person", person.pk)
    else:
        form = PersonForm()
    q = request.GET.get("q", "").strip()
    everyone = Person.objects.filter(organization=org).annotate(
        event_count=Count("guests", distinct=True), upload_count=Count("uploads", distinct=True)
    )
    if q:
        everyone = everyone.filter(name__icontains=q)
    yesterday = timezone.localdate() - timedelta(days=1)
    rows = list(everyone[:300])
    upcoming = {}
    for g in Guest.objects.filter(person__in=rows, service__date__gte=yesterday).select_related("service__location").order_by("service__date"):
        upcoming.setdefault(g.person_id, []).append(g.service)
    for person in rows:
        person.upcoming = upcoming.get(person.pk, [])
    return render(
        request,
        "guestmedia/manage/people.html",
        {"people": rows, "form": form, "events": upcoming_events(org), "q": q},
    )


@org_view
def person_detail(request, pk):
    person = get_object_or_404(Person.objects.select_related("organization"), pk=pk)
    check_org(request, person.organization)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update":
            form = PersonForm(request.POST, instance=person)
            if form.is_valid():
                form.save()
                MediaAsset.objects.filter(uploaded_by=person).update(owner_name=person.name)
                messages.success(request, "Saved.")
        elif action == "add_event":
            event = get_object_or_404(org_events(person.organization), pk=request.POST.get("event_id"))
            add_to_event(person, event)
            messages.success(request, f"Added to {event}. Its agent will download {person.name}'s files ahead of time.")
        elif action == "remove_event":
            Guest.objects.filter(pk=request.POST.get("guest_id"), person=person).delete()
            messages.success(request, "Removed from the event.")
        elif action == "new_link":
            person.token = new_guest_token()
            person.save(update_fields=["token"])
            person.aliases.all().delete()
            person.devices.all().delete()
            messages.success(request, "New link created; the old one no longer works and every device has to enter the PIN again.")
        elif action == "new_pin":
            person.set_staff_pin()
            person.save()
            messages.success(request, f"New PIN: {person.pin}. Devices already signed in stay signed in unless you sign them out.")
        elif action == "reset_pin":
            person.reset_pin()
            person.save()
            person.devices.all().delete()
            messages.success(request, f"PIN cleared and every device signed out. {person.name} chooses a new PIN the next time they open their link.")
        elif action == "unlock":
            person.failed_pins = 0
            person.locked_until = None
            person.save(update_fields=["failed_pins", "locked_until"])
            messages.success(request, "Unlocked.")
        elif action == "sign_out_device":
            person.devices.filter(pk=request.POST.get("device_id")).delete()
            messages.success(request, "That device is signed out.")
        elif action == "sign_out_all":
            count = person.devices.count()
            person.devices.all().delete()
            messages.success(request, f"Signed out {count} device{'s' if count != 1 else ''}.")
        elif action == "delete":
            name = person.name
            person.delete()
            messages.success(request, f"{name} deleted. Their uploads stay in the library.")
            return redirect("manage-people")
        return redirect("manage-person", person.pk)
    participations = person.participations().prefetch_related("sets__items")
    taken = {g.service_id for g in participations}
    return render(
        request,
        "guestmedia/manage/person.html",
        {
            "person": person,
            "form": PersonForm(instance=person),
            "link": person.link_for(request),
            "participations": participations,
            "events": [e for e in upcoming_events(person.organization) if e.pk not in taken],
            "uploads": MediaAsset.objects.filter(uploaded_by=person)[:60],
            "devices": person.devices.all(),
            "today": timezone.localdate(),
        },
    )


@org_view
@require_POST
def approve(request, pk):
    asset = get_object_or_404(MediaAsset.all_objects, pk=pk)
    check_org(request, asset.organization)
    decision = request.POST.get("decision")
    if decision == "approve":
        asset.approval = MediaAsset.APPROVAL_APPROVED
    elif decision == "reject":
        asset.approval = MediaAsset.APPROVAL_REJECTED
    asset.save(update_fields=["approval"])
    return redirect(request.POST.get("next") or "manage-library")


@org_view
def library(request):
    org = request.org
    show_trash = request.GET.get("trash") == "1"
    scope = request.GET.get("scope", "ours")
    assets = MediaAsset.all_objects.all() if show_trash else MediaAsset.objects.all()
    if show_trash:
        assets = assets.filter(deleted_at__isnull=False)
    if scope == "shared":
        # Files other organizations share with us (read only here).
        assets = MediaAsset.library_for(org).exclude(organization=org).exclude(organization__isnull=True)
    else:
        own = Q(organization=org)
        if request.user.is_superuser:
            own |= Q(organization__isnull=True)
        assets = assets.filter(own)

    q = request.GET.get("q", "").strip()
    if q:
        assets = assets.filter(Q(original_name__icontains=q) | Q(owner_name__icontains=q))
    kind = request.GET.get("kind", "")
    if kind in ("photo", "video", "audio"):
        assets = assets.filter(kind=kind)
    approval = request.GET.get("approval", "")
    if approval in ("pending", "approved", "rejected"):
        assets = assets.filter(approval=approval)
    unused = request.GET.get("unused", "")
    if unused.isdigit():
        cutoff = timezone.now() - timedelta(days=int(unused))
        assets = assets.filter(Q(last_used__lt=cutoff) | Q(last_used__isnull=True, created__lt=cutoff))

    if request.method == "POST":
        ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
        selected = MediaAsset.all_objects.filter(pk__in=ids, organization=org)
        action = request.POST.get("action")
        in_active = SetItem.objects.filter(asset__in=selected, media_set__service__is_active=True)
        if action == "trash":
            if in_active.exists() and request.POST.get("confirm") != "1":
                names = ", ".join(sorted({i.asset.original_name for i in in_active.select_related("asset")})[:8])
                messages.warning(
                    request,
                    f"Some files are in sets of active services ({names}). Tick 'I know' and try again to delete them anyway.",
                )
            else:
                count = selected.update(deleted_at=timezone.now())
                SetItem.objects.filter(asset__in=ids).delete()
                notify_agents()
                messages.success(request, f"Moved {count} file(s) to the trash.")
        elif action == "restore":
            selected.update(deleted_at=None)
            notify_agents()
            messages.success(request, "Restored.")
        elif action == "purge":
            for asset in selected.filter(deleted_at__isnull=False):
                delete_asset_files(asset)
                asset.delete()
            messages.success(request, "Deleted for good.")
        elif action in ("approve", "reject"):
            for asset in selected:
                asset.approval = MediaAsset.APPROVAL_APPROVED if action == "approve" else MediaAsset.APPROVAL_REJECTED
                asset.save(update_fields=["approval"])
            messages.success(request, "Updated.")
        elif action in ("private", "public"):
            for asset in selected:
                asset.private = action == "private"
                asset.save(update_fields=["private"])
        elif action in ("share", "unshare"):
            for asset in selected:
                asset.shared = action == "share"
                asset.save(update_fields=["shared"])
            messages.success(request, "Updated sharing.")
        elif action == "reprocess":
            for asset in selected:
                process_asset.delay(asset.pk)
            messages.success(request, "Processing again.")
        return redirect(request.get_full_path())

    totals = assets.aggregate(size=Sum("processed_size"), count=Count("id"))
    return render(
        request,
        "guestmedia/manage/library.html",
        {
            "assets": assets.select_related("service", "organization")[:300],
            "totals": totals,
            "scope": scope,
            "org": org,
            "show_trash": show_trash,
            "filters": request.GET,
            "trash_days": settings.VITRINE_TRASH_DAYS,
        },
    )


@org_view
def asset_detail(request, pk):
    asset = get_object_or_404(MediaAsset.all_objects.select_related("organization"), pk=pk)
    readonly = asset.organization_id is not None and asset.organization_id not in {o.pk for o in request.orgs}
    if readonly and not asset.is_shared_with(request.org):
        raise Http404()
    if request.method == "POST" and not readonly:
        action = request.POST.get("action")
        if action == "update":
            asset.owner_name = request.POST.get("owner_name", asset.owner_name)[:120]
            asset.private = request.POST.get("private") == "on"
            asset.shared = request.POST.get("shared") == "on"
            asset.save(update_fields=["owner_name", "private", "shared"])
        elif action == "add_marker":
            label = request.POST.get("label", "").strip()[:60]
            if label:
                Marker.objects.create(
                    asset=asset, label=label, t_ms=parse_time(request.POST.get("time")), created_by="staff"
                )
        elif action == "delete_marker":
            Marker.objects.filter(asset=asset, pk=request.POST.get("marker_id")).delete()
        return redirect("manage-asset", asset.pk)
    sets = MediaSet.objects.filter(items__asset=asset).select_related("service")
    return render(request, "guestmedia/manage/asset.html", {"asset": asset, "sets": sets, "readonly": readonly})


@staff_member_required
def asset_file(request, pk, variant):
    asset = get_object_or_404(MediaAsset.all_objects, pk=pk)
    field = {"processed": asset.processed, "thumb": asset.thumb, "filmstrip": asset.filmstrip, "original": asset.original}.get(variant)
    if field is None:
        raise Http404()
    return serve_field(request, field)


@org_view
def agents(request):
    """Locations of the current organization, each with its agent (vMix PC) and tablets."""
    org = request.org
    new_token = None
    pairing = None

    def own_agent(agent_id):
        agent = get_object_or_404(Agent, pk=agent_id)
        location = getattr(agent, "location", None)
        if location is None:
            if not request.user.is_superuser and agent.pk not in unassigned_ids():
                raise Http404()
        else:
            check_org(request, location.organization)
        return agent

    def unassigned_ids():
        return set(Agent.objects.filter(location__isnull=True).values_list("pk", flat=True))

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create_location":
            name = request.POST.get("name", "").strip() or "Main hall"
            Location.objects.create(organization=org, name=name[:120])
            messages.success(request, f"Location {name} created.")
            return redirect("manage-agents")
        elif action == "rename_location":
            location = get_object_or_404(Location, pk=request.POST.get("location_id"), organization=org)
            location.name = request.POST.get("name", location.name).strip()[:120] or location.name
            location.save(update_fields=["name"])
            return redirect("manage-agents")
        elif action == "delete_location":
            location = get_object_or_404(Location, pk=request.POST.get("location_id"), organization=org)
            location.delete()
            messages.success(request, "Location deleted. Its events stay, without a location.")
            return redirect("manage-agents")
        elif action == "create":
            location = get_object_or_404(Location, pk=request.POST.get("location_id"), organization=org)
            if location.agent_id:
                messages.error(request, f"{location.name} already has an agent. Use New token to replace its PC.")
                return redirect("manage-agents")
            agent, new_token = Agent.create_with_token(location.name)
            location.agent = agent
            location.save(update_fields=["agent"])
            messages.success(request, f"Agent for {location.name} created. Copy the token now; it is shown only once.")
        elif action == "assign":
            location = get_object_or_404(Location, pk=request.POST.get("location_id"), organization=org)
            agent = own_agent(request.POST.get("agent_id"))
            Location.objects.filter(agent=agent).update(agent=None)
            location.agent = agent
            location.save(update_fields=["agent"])
            agent.manifest_version += 1
            agent.save(update_fields=["manifest_version"])
            messages.success(request, f"{agent.name} now serves {location.name}.")
            return redirect("manage-agents")
        elif action == "unassign":
            location = get_object_or_404(Location, pk=request.POST.get("location_id"), organization=org)
            location.agent = None
            location.save(update_fields=["agent"])
            return redirect("manage-agents")
        elif action == "rotate":
            agent = own_agent(request.POST.get("agent_id"))
            new_token = agent.rotate_token()
            messages.success(request, f"New token for {agent.name}. Update the agent's connection settings.")
        elif action == "delete":
            own_agent(request.POST.get("agent_id")).delete()
            return redirect("manage-agents")
        elif action == "pair":
            agent = own_agent(request.POST.get("agent_id"))
            role = request.POST.get("role") if request.POST.get("role") in ("back", "pulpit") else "pulpit"
            name = request.POST.get("name", "").strip() or ("Pulpit tablet" if role == "pulpit" else "Back tablet")
            grant = ScreenGrant.objects.create(
                agent=agent, role=role, name=name[:120], expires_at=timezone.now() + timedelta(minutes=15)
            )
            token = sign_pairing(grant)
            base = agent.tablet_base_url or "http://<vmix-pc-ip>:8090"
            pairing = {"grant": grant, "url": f"{base}/pair?{urlencode({'t': token})}", "agent": agent}
        elif action == "revoke":
            grant = get_object_or_404(ScreenGrant, pk=request.POST.get("grant_id"))
            own_agent(grant.agent_id)
            grant.revoked = True
            grant.save(update_fields=["revoked"])
            messages.success(request, f"{grant.name} will be unpaired as soon as the agent syncs.")
            return redirect("manage-agents")
    locations = org.locations.select_related("agent").prefetch_related("agent__grants")
    return render(
        request,
        "guestmedia/manage/agents.html",
        {
            "locations": locations,
            "unassigned": Agent.objects.filter(location__isnull=True),
            "new_token": new_token,
            "pairing": pairing,
        },
    )


@org_view
def organization(request):
    """The current organization: name, sharing, staff; superusers can add organizations."""
    org = request.org
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update":
            org.name = request.POST.get("name", org.name).strip()[:120] or org.name
            org.share_all = request.POST.get("share_all") == "on"
            org.save(update_fields=["name", "share_all"])
            ids = [int(i) for i in request.POST.getlist("shares_with") if i.isdigit()]
            org.shares_with.set(Organization.objects.filter(pk__in=ids).exclude(pk=org.pk))
            if request.user.is_superuser:
                from django.contrib.auth import get_user_model

                staff_ids = [int(i) for i in request.POST.getlist("staff") if i.isdigit()]
                org.staff.set(get_user_model().objects.filter(pk__in=staff_ids, is_staff=True))
            Agent.objects.filter(location__organization__in=org.shares_with.all()).update(
                manifest_version=models.F("manifest_version") + 1
            )
            messages.success(request, "Saved.")
        elif action == "notifications":
            org.notify_telegram_chat = request.POST.get("notify_telegram_chat", "").strip()[:64]
            org.notify_emails = request.POST.get("notify_emails", "").strip()
            for field in ("notify_uploads", "notify_offline", "notify_sync", "notify_backup"):
                setattr(org, field, request.POST.get(field) == "on")
            org.save()
            if request.POST.get("test") == "1":
                from . import alerts

                if not alerts.can_send(org):
                    messages.error(request, "Nothing to send to: add a Telegram chat id (with VITRINE_TELEGRAM_BOT_TOKEN set) or an email address (with EMAIL_HOST set).")
                else:
                    errors = alerts.notify(org, "Test message", f"Alerts for {org.name} work.")
                    if errors:
                        messages.error(request, "Test failed: " + "; ".join(errors))
                    else:
                        messages.success(request, "Test message sent.")
            else:
                messages.success(request, "Saved.")
        elif action == "create" and request.user.is_superuser:
            name = request.POST.get("new_name", "").strip()[:120]
            if name and not Organization.objects.filter(name=name).exists():
                new = Organization.objects.create(name=name)
                new.staff.add(request.user)
                request.session["vitrine_org"] = new.pk
                messages.success(request, f"Organization {name} created. Add its locations next.")
                return redirect("manage-agents")
            messages.error(request, "Give the organization a new, unique name.")
        return redirect("manage-organization")
    from django.contrib.auth import get_user_model

    return render(
        request,
        "guestmedia/manage/organization.html",
        {
            "org": org,
            "others": Organization.objects.exclude(pk=org.pk),
            "sharing_ids": set(org.shares_with.values_list("pk", flat=True)),
            "shared_from": org.shared_from.all(),
            "users": get_user_model().objects.filter(is_staff=True).order_by("username") if request.user.is_superuser else [],
            "staff_ids": set(org.staff.values_list("pk", flat=True)),
            "telegram_ready": bool(settings.VITRINE_TELEGRAM_BOT_TOKEN),
            "email_ready": bool(settings.EMAIL_HOST),
        },
    )


@staff_member_required
def qr_svg(request):
    data = request.GET.get("d", "")
    if not data or len(data) > 2000:
        raise Http404()
    image = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=2)
    buffer = io.BytesIO()
    image.save(buffer)
    return HttpResponse(buffer.getvalue(), content_type="image/svg+xml")


@org_view
def backups(request):
    """Backup status and history; superusers can start one."""
    from . import backup
    from .models import BackupRun

    if not request.user.is_superuser:
        raise Http404()
    if request.method == "POST" and request.POST.get("action") == "run":
        if not backup.enabled():
            messages.error(request, "Backups are off: set VITRINE_BACKUP_TARGET in the stack.")
        else:
            backup.run_backup.delay("started from /manage/")
            messages.success(request, "Backup started. Refresh this page in a minute.")
        return redirect("manage-backups")
    return render(
        request,
        "guestmedia/manage/backups.html",
        {
            "enabled": backup.enabled(),
            "target": settings.VITRINE_BACKUP_TARGET,
            "when": settings.VITRINE_BACKUP_TIME,
            "keep": settings.VITRINE_BACKUP_KEEP_DAYS,
            "runs": BackupRun.objects.all()[:30],
            "last_ok": backup.last_success(),
            "stale": backup.is_stale(),
        },
    )
