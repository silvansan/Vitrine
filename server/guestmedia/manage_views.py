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
from django.urls import reverse
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
from . import config as vitrine_config
from .orgs import check_org, org_view
from .notify import notify_agents
from .signing import sign_pairing
from .tasks import delete_asset_files, process_asset


class EventForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = ["name", "starts_on", "ends_on", "location", "is_active", "auto_approve"]
        widgets = {
            "starts_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "ends_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        labels = {"is_active": "Active", "starts_on": "From", "ends_on": "Until"}

    def clean(self):
        data = super().clean()
        start, end = data.get("starts_on"), data.get("ends_on")
        if start and end and end < start:
            self.add_error("ends_on", "The end is before the start.")
        return data

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
    today = timezone.localdate()
    return sorted(org_events(org).not_over(yesterday), key=lambda e: e.sort_key(today))


def add_to_event(person, event):
    guest, created = Guest.objects.get_or_create(service=event, person=person)
    guest.get_set()
    return guest, created


def _date_or_none(text):
    from datetime import date

    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        return None


def duplicate_event(event, name, starts_on, ends_on, with_people=True, with_files=True):
    """A new event like this one: same location and options, optionally its people and their sets."""
    from django.db import transaction

    with transaction.atomic():
        copy = Service.objects.create(
            name=name, starts_on=starts_on, ends_on=ends_on if (not starts_on or not ends_on or ends_on >= starts_on) else starts_on,
            location=event.location, is_active=event.is_active, auto_approve=event.auto_approve,
        )
        if with_people:
            for guest in event.guests.select_related("person"):
                new_guest, _ = add_to_event(guest.person, copy)
                if with_files:
                    old_set = MediaSet.objects.filter(service=event, guest=guest).first()
                    if old_set:
                        new_set = new_guest.get_set()
                        new_set.title = old_set.title
                        new_set.save(update_fields=["title"])
                        SetItem.objects.bulk_create(
                            SetItem(media_set=new_set, asset_id=item.asset_id, position=item.position)
                            for item in old_set.items.all()
                        )
    return copy


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
        today = timezone.localdate()
        form = EventForm(org=org, initial={"location": org.locations.first(), "starts_on": today, "ends_on": today})
    events = org_events(org).annotate(guest_count=Count("guests", distinct=True))
    location = request.GET.get("location", "")
    if location.isdigit():
        events = events.filter(location_id=int(location))
    today = timezone.localdate()
    events = list(events[:120])
    for e in events:
        e.on_today = e.is_on(today) and e.starts_on is not None
        e.past = e.is_past(today)
    # Running and coming events first (today's on top), then past ones, newest first.
    current = sorted([e for e in events if not e.past], key=lambda e: e.sort_key(today))
    events = current + [e for e in events if e.past]
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
            "events": events,
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
        elif action == "duplicate":
            copy = duplicate_event(
                event,
                name=request.POST.get("name", "").strip()[:120] or f"{event.name} (copy)",
                starts_on=_date_or_none(request.POST.get("starts_on")),
                ends_on=_date_or_none(request.POST.get("ends_on")),
                with_people=request.POST.get("with_people") == "on",
                with_files=request.POST.get("with_files") == "on",
            )
            messages.success(request, f"{copy.name} created. Check its dates and people.")
            return redirect("manage-service", copy.pk)
        elif action == "delete_event":
            if request.POST.get("confirm_name", "").strip() != event.name.strip():
                messages.error(request, "Type the event's name to delete it.")
                return redirect(reverse("manage-service", args=[event.pk]) + "#delete")
            name = event.name
            event.delete()
            messages.success(request, f"{name} deleted. Its people, their links and all files are kept.")
            return redirect("manage-dashboard")
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
    upcoming_guests = Guest.objects.filter(person__in=rows).filter(
        Q(service__ends_on__isnull=True) | Q(service__ends_on__gte=yesterday)
    ).select_related("service__location").order_by("service__starts_on")
    for g in upcoming_guests:
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
            uploads = MediaAsset.all_objects.filter(uploaded_by=person)
            count = uploads.count()
            # Their files stay in the organization's library (banners and the like): files they had
            # marked private become normal library files, since nobody else could ever pick them.
            uploads.filter(private=True).update(private=False)
            person.delete()
            if count:
                messages.success(request, f"{name} deleted. Their {count} file{'s' if count != 1 else ''} stay in the library, under the name {name}.")
            else:
                messages.success(request, f"{name} deleted.")
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
            "upload_events": [g.service for g in person.upcoming_participations()],
            "max_upload_mb": settings.VITRINE_MAX_UPLOAD_MB,
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
        elif action == "add_to_set":
            guest = Guest.objects.filter(pk=request.POST.get("guest_id") or 0, service__location__organization=org).select_related("person", "service").first()
            if guest is None:
                messages.error(request, "Choose whose set to add them to.")
            else:
                media_set = guest.get_set()
                last = media_set.items.order_by("-position").first()
                position = (last.position + 1) if last else 0
                added = 0
                for asset in selected.filter(deleted_at__isnull=True).order_by("created"):
                    _, created = SetItem.objects.get_or_create(media_set=media_set, asset=asset, defaults={"position": position})
                    if created:
                        position += 1
                        added += 1
                messages.success(request, f"Added {added} file(s) to {guest.person.name}'s set in {guest.service.name}.")
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
            "upload_people": Person.objects.filter(organization=org).order_by("name"),
            "upload_events": upcoming_events(org),
            "set_targets": Guest.objects.filter(service__in=upcoming_events(org)).select_related("service__location", "person")
            .order_by("service__starts_on", "service__name", "person__name"),
            "max_upload_mb": settings.VITRINE_MAX_UPLOAD_MB,
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
        elif action == "telegram_connect":
            from . import telegram

            try:
                telegram.bot_info()
            except telegram.TelegramError as exc:
                messages.error(request, f"Telegram isn't ready: {exc}")
                return redirect(reverse("manage-organization") + "#alerts")
            request.session["vitrine_tg_code"] = telegram.new_link_code(org)
            return redirect(reverse("manage-organization") + "#alerts")
        elif action == "telegram_cancel":
            request.session.pop("vitrine_tg_code", None)
            return redirect(reverse("manage-organization") + "#alerts")
        elif action == "telegram_disconnect":
            org.notify_telegram_chat = ""
            org.notify_telegram_title = ""
            org.save(update_fields=["notify_telegram_chat", "notify_telegram_title"])
            messages.success(request, "Telegram chat disconnected.")
            return redirect(reverse("manage-organization") + "#alerts")
        elif action == "notifications":
            if "notify_telegram_chat" in request.POST:  # typed by hand
                chat = request.POST.get("notify_telegram_chat", "").strip()[:64]
                if chat != org.notify_telegram_chat:
                    org.notify_telegram_chat = chat
                    org.notify_telegram_title = ""
            org.notify_emails = request.POST.get("notify_emails", "").strip()
            for field in ("notify_uploads", "notify_offline", "notify_sync", "notify_backup"):
                setattr(org, field, request.POST.get(field) == "on")
            org.save()
            if request.POST.get("test") == "1":
                from . import alerts

                if not alerts.can_send(org):
                    messages.error(request, "Nothing to send to: add a Telegram chat id or an email address here, and set up Telegram or email under Settings.")
                else:
                    errors = alerts.notify(org, "Test message", f"Alerts for {org.name} work.")
                    if errors:
                        messages.error(request, "Test failed: " + "; ".join(errors))
                    else:
                        messages.success(request, "Test message sent.")
            else:
                messages.success(request, "Saved.")
        elif action == "delete_org" and request.user.is_superuser:
            left = []
            if Organization.objects.count() <= 1:
                left.append("it is the only organization")
            if org.locations.exists():
                left.append(f"{org.locations.count()} location(s)")
            if org.people.exists():
                left.append(f"{org.people.count()} people")
            if MediaAsset.all_objects.filter(organization=org).exists():
                left.append(f"{MediaAsset.all_objects.filter(organization=org).count()} file(s) in its library or trash")
            if request.POST.get("confirm_name", "").strip() != org.name:
                messages.error(request, "Type the organization's name to delete it.")
            elif left:
                messages.error(request, "Can't delete it yet: " + ", ".join(left) + ". Delete those first (files: Library → Move to trash → Delete for good).")
            else:
                name = org.name
                org.delete()
                request.session.pop("vitrine_org", None)
                messages.success(request, f"Organization {name} deleted.")
            return redirect("manage-organization")
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
            "telegram_ready": bool(vitrine_config.telegram_token()),
            "email_ready": bool(vitrine_config.email_config()),
            **_telegram_link_context(request, org),
        },
    )


def _telegram_link_context(request, org):
    """The bot's name and, while a link is being made, the t.me links for it."""
    from . import telegram

    if not vitrine_config.telegram_token():
        return {}
    try:
        bot = telegram.bot_info()
    except telegram.TelegramError as exc:
        return {"telegram_error": str(exc)}
    out = {"bot": bot}
    code = request.session.get("vitrine_tg_code")
    if code:
        out["tg_code"] = code
        out["tg_private"] = f"https://t.me/{bot['username']}?start={code}"
        out["tg_group"] = f"https://t.me/{bot['username']}?startgroup={code}"
        out["tg_qr"] = out["tg_private"]
    return out


@org_view
def telegram_check(request):
    """Polled while a Telegram link is open: has the chat sent its /start code yet?"""
    from django.http import JsonResponse

    from . import telegram

    code = request.session.get("vitrine_tg_code")
    if not code:
        return JsonResponse({"status": "none"})
    try:
        result = telegram.link_status(code)
    except telegram.TelegramError as exc:
        return JsonResponse({"status": "error", "error": str(exc)})
    if not result:
        return JsonResponse({"status": "waiting"})
    request.session.pop("vitrine_tg_code", None)
    if result.get("expired"):
        return JsonResponse({"status": "expired"})
    messages.success(request, f"Telegram connected: alerts go to {result['title']}.")
    return JsonResponse({"status": "linked", "title": result["title"]})


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
    return redirect(reverse("manage-settings") + "#backups")


SECRET_MASK = "••••••••"

# Quick-fill for common mail providers on the Settings page (nothing is stored but the fields).
EMAIL_PRESETS = {
    "brevo": {"label": "Brevo", "host": "smtp-relay.brevo.com", "port": 587, "security": "starttls",
              "hint": "User: the SMTP login from Brevo → Settings → SMTP & API (an address like 123abc@smtp-brevo.com, or your account email). "
                      "Password: an SMTP key from the same page, not an API key. From: a sender you verified in Brevo (Senders, domains & dedicated IPs)."},
    "infomaniak": {"label": "Infomaniak", "host": "mail.infomaniak.com", "port": 587, "security": "starttls",
                   "hint": "User: the full email address. Password: that mailbox's password."},
    "gmail": {"label": "Gmail / Google Workspace", "host": "smtp.gmail.com", "port": 587, "security": "starttls",
              "hint": "User: the Gmail address. Password: an app password (Google account → Security → 2-Step Verification → App passwords), not the normal password."},
    "microsoft": {"label": "Microsoft 365 / Outlook", "host": "smtp.office365.com", "port": 587, "security": "starttls",
                  "hint": "User: the mailbox address. SMTP AUTH must be allowed for it in the Microsoft 365 admin center."},
}


def transfer_context(request):
    from . import transfer_views

    return transfer_views.context(request)


def _bot_status():
    from . import telegram

    if not config_telegram_token():
        return {}
    try:
        return {"bot": telegram.bot_info()}
    except telegram.TelegramError as exc:
        return {"bot_error": str(exc)}


def config_telegram_token():
    return vitrine_config.telegram_token()


@org_view
def site_settings(request):
    """Server-wide settings (superusers): alert delivery and backups."""
    from . import backup, config
    from .models import BackupRun, SiteSettings

    if not request.user.is_superuser:
        raise Http404()
    s = SiteSettings.load()
    if request.method == "POST":
        action = request.POST.get("action")
        P = request.POST

        def keep_secret(new, old):
            return old if new in ("", SECRET_MASK) else new

        if action == "alerts":
            s.telegram_bot_token = keep_secret(P.get("telegram_bot_token", "").strip(), s.telegram_bot_token)
            if P.get("clear_telegram") == "on":
                s.telegram_bot_token = ""
            s.email_host = P.get("email_host", "").strip()
            s.email_port = int(P["email_port"]) if P.get("email_port", "").isdigit() else None
            s.email_user = P.get("email_user", "").strip()
            s.email_password = keep_secret(P.get("email_password", ""), s.email_password)
            s.email_security = P.get("email_security", "starttls")
            s.email_from = P.get("email_from", "").strip()
            s.offline_alert_minutes = int(P["offline_alert_minutes"]) if P.get("offline_alert_minutes", "").isdigit() else None
            s.sync_check_time = P.get("sync_check_time", "").strip()[:5]
            s.save()
            messages.success(request, "Saved.")
            if s.telegram_bot_token and P.get("telegram_bot_token", "").strip() not in ("", SECRET_MASK):
                from . import telegram

                try:
                    bot = telegram.bot_info(s.telegram_bot_token, fresh=True)
                    messages.success(request, f"Telegram bot @{bot['username']} is ready. Connect your team's chat under Organization.")
                except telegram.TelegramError as exc:
                    messages.error(request, f"Telegram doesn't accept this token: {exc}")
            if P.get("test_email") == "1":
                from . import alerts

                to = P.get("test_to", "").strip() or request.user.email
                if not config.email_config():
                    messages.error(request, "Email isn't set up yet.")
                elif "@" not in to:
                    messages.error(request, "Enter an address to send the test to.")
                else:
                    try:
                        alerts.send_email([to], "[Vitrine] Test email", "Email from Vitrine works.")
                        messages.success(request, f"Test email sent to {to}.")
                    except Exception as exc:
                        messages.error(request, f"Test email failed: {exc}")
            return redirect(reverse("manage-settings") + "#alerts")
        if action in ("backup", "backup_test"):
            kind = P.get("backup_kind", "off")
            if kind and kind not in dict(SiteSettings.BACKUP_KINDS):
                kind = "off"  # "" keeps the stack environment's target
            old = s.backup_options or {}
            opts = {}
            for name, _label, _hint, extra in config.BACKUP_FIELDS.get(kind, []):
                field = f"{kind}__{name}"
                if extra.get("checkbox"):
                    opts[name] = P.get(field) == "on"
                elif extra.get("secret"):
                    value = P.get(field, "")
                    opts[name] = old.get(name, "") if (value.strip() in ("", SECRET_MASK) and s.backup_kind == kind) else value.strip()
                else:
                    opts[name] = P.get(field, "").strip()
            s.backup_kind = kind
            s.backup_options = opts
            s.backup_path = P.get("backup_path", "").strip()
            s.backup_time = P.get("backup_time", "").strip()[:5]
            s.backup_keep_days = int(P["backup_keep_days"]) if P.get("backup_keep_days", "").isdigit() else None
            s.save()
            if action == "backup_test":
                ok, log = backup.test_connection()
                request.session["vitrine_backup_test"] = {"ok": ok, "log": log}
            else:
                messages.success(request, "Saved.")
            return redirect(reverse("manage-settings") + "#backups")
        if action == "run":
            if not backup.enabled():
                messages.error(request, "Choose where backups go first.")
            else:
                backup.run_backup.delay("started from /manage/")
                messages.success(request, "Backup started. Refresh this page in a minute.")
            return redirect(reverse("manage-settings") + "#backups")
    try:
        plan = config.backup_plan()
    except Exception as exc:
        plan = {"label": f"(problem: {exc})"}
    fields = []
    for kind, rows in config.BACKUP_FIELDS.items():
        for name, label, hint, extra in rows:
            value = (s.backup_options or {}).get(name, "") if s.backup_kind == kind else ""
            if extra.get("secret") and value:
                value = SECRET_MASK
            fields.append({"kind": kind, "name": f"{kind}__{name}", "label": label, "hint": hint, "value": value,
                           "secret": extra.get("secret"), "textarea": extra.get("textarea"),
                           "checkbox": extra.get("checkbox"), "choices": extra.get("choices")})
    return render(
        request,
        "guestmedia/manage/settings.html",
        {
            "s": s,
            "mask": SECRET_MASK,
            "kinds": SiteSettings.BACKUP_KINDS,
            "current_kind": s.backup_kind or ("env" if config.env_backup_target() else "off"),
            "env_target": config.env_backup_target(),
            "fields": fields,
            "path_hints": config.PATH_HINT,
            "plan": plan,
            "backup_time": config.backup_time(),
            "keep_days": config.backup_keep_days(),
            "runs": BackupRun.objects.all()[:20],
            "last_ok": backup.last_success(),
            "stale": backup.is_stale(),
            "test": request.session.pop("vitrine_backup_test", None),
            "telegram_from_env": bool(settings.VITRINE_TELEGRAM_BOT_TOKEN) and not s.telegram_bot_token,
            **_bot_status(),
            **transfer_context(request),
            "email_presets": EMAIL_PRESETS,
            "email_from_env": bool(settings.EMAIL_HOST) and not s.email_host,
            "offline_minutes": config.offline_alert_minutes(),
            "sync_time": config.sync_check_time(),
        },
    )
