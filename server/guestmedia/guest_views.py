"""The page a guest opens from their link: My set, Upload, Library."""
import json

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import pins
from .files import serve_field
from .models import Guest, Marker, MediaAsset, MediaSet, Person, PersonAlias, SetItem


def load_person(token, request=None, check_pin=True):
    """The person behind a link. With a request, also checks this device entered the PIN (pins.PinRequired)."""
    person = Person.objects.select_related("organization").filter(token=token).first()
    if person is None:
        alias = PersonAlias.objects.select_related("person__organization").filter(token=token).first()
        if alias is None:
            raise Http404("This link doesn't work (any more). Ask the production team for a new one.")
        person = alias.person
    if person.is_expired:
        raise Http404("This link has expired")
    if request is not None and check_pin:
        pins.require(request, person)
    return person


def load_guest(request, token, required=True, check_pin=True):
    """The person's participation in the event the page is about (?e=<event id>, else the current one)."""
    person = load_person(token, request, check_pin)
    event_id = request.GET.get("e", "")
    guest = None
    if event_id.isdigit():
        guest = person.guests.select_related("service", "service__location", "person__organization").filter(service_id=int(event_id)).first()
    if guest is None:
        guest = person.current_participation()
    if guest is None and required:
        raise Http404("You aren't in an event yet. The production team will add you.")
    return person, guest


def read_json(request):
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return {}


def event_label(event):
    where = f" · {event.location.name}" if event.location_id else ""
    return f"{event.name} · {event.date:%d %b}{where}"


def asset_for_guest(asset, guest):
    person = guest.person
    if asset.uploaded_by_id == person.id:
        when = "Your upload"
        group = "mine"
    elif asset.organization_id and asset.organization_id != person.organization_id:
        when = f"Shared by {asset.organization.name}"
        group = "shared"
    elif asset.service_id == guest.service_id:
        when = "This event"
        group = "today"
    else:
        service = asset.service.name if asset.service else "the library"
        when = f"From {service}"
        group = "earlier"
    q = f"?e={guest.service_id}"
    return {
        "id": asset.pk,
        "kind": asset.kind or "processing",
        "owner": asset.owner_name,
        "name": asset.original_name,
        "status": asset.status,
        "approval": asset.approval,
        "private": asset.private,
        "mine": asset.uploaded_by_id == person.id,
        "duration_ms": asset.duration_ms,
        "thumb": f"/g/{person.token}/thumb/{asset.pk}{q}" if asset.thumb else "",
        "file": f"/g/{person.token}/file/{asset.pk}{q}" if asset.processed and asset.status == MediaAsset.STATUS_READY else "",
        "when": when,
        "group": group,
        "markers": [
            {"id": m.pk, "label": m.label, "t_ms": m.t_ms, "mine": m.created_by == person.name}
            for m in asset.markers.all()
        ],
    }


@require_http_methods(["GET", "POST"])
def page(request, token):
    person = load_person(token)
    if not pins.is_authorized(request, person):
        return pin_page(request, person)
    person, guest = load_guest(request, token, required=False)
    if guest is None:
        return render(request, "guestmedia/guest_waiting.html", {"person": person}, status=200)
    return render(
        request,
        "guestmedia/guest.html",
        {
            "guest": guest,
            "person": person,
            "service": guest.service,
            "event_label": event_label(guest.service),
            "events": [
                {"id": g.service_id, "label": event_label(g.service), "current": g.pk == guest.pk}
                for g in person.participations()
                if g.service.date >= timezone.localdate() - timedelta(days=1) or g.pk == guest.pk
            ],
            "max_upload_mb": settings.VITRINE_MAX_UPLOAD_MB,
            "on_device": pins.device_for(request, person) is not None,
        },
    )


def pin_page(request, person):
    """Ask for the PIN; on success remember this device and show the page."""
    error = ""
    choosing = not person.has_pin
    if request.method == "POST":
        if choosing:
            ok, error = pins.choose_pin(request, person, request.POST.get("pin", ""), request.POST.get("pin2", ""))
        else:
            ok, error = pins.check_pin(request, person, request.POST.get("pin", ""))
        if ok:
            target = request.get_full_path()
            return pins.remember_device(request, HttpResponseRedirect(target), person)
    return render(
        request,
        "guestmedia/guest_pin.html",
        {"person": person, "error": error, "locked": person.is_locked, "choosing": choosing,
         "min_len": pins.MIN_LEN, "max_len": pins.MAX_LEN},
        status=401 if error else 200,
    )


@require_http_methods(["GET", "POST"])
def change_pin(request, token):
    """A signed-in person picks a new PIN (needs the current one)."""
    person = load_person(token, request)
    error = ""
    done = False
    if request.method == "POST":
        if person.has_pin and not person.check_pin_value(request.POST.get("current", "").strip()):
            error = "Your current PIN isn't right."
        else:
            ok, error = pins.choose_pin(request, person, request.POST.get("pin", ""), request.POST.get("pin2", ""))
            done = ok
    return render(
        request,
        "guestmedia/guest_pin_change.html",
        {"person": person, "error": error, "done": done, "min_len": pins.MIN_LEN, "max_len": pins.MAX_LEN},
    )


@require_POST
def sign_out_device(request, token):
    person = load_person(token)
    return pins.forget_device(request, HttpResponseRedirect(f"/g/{token}/"), person)


@require_GET
def tablet_help(request, token):
    """How the pulpit tablet works, with screenshots, so guests can prepare. Needs no PIN."""
    person, guest = load_guest(request, token, required=False, check_pin=False)
    return render(request, "guestmedia/tablet_help.html", {"person": person, "guest": guest})


def previous_set(guest):
    """The person's most recent other non-empty set, to start this one from."""
    return (
        MediaSet.objects.filter(guest__person=guest.person)
        .exclude(guest=guest)
        .filter(items__isnull=False)
        .select_related("service")
        .order_by("-service__date", "-service_id")
        .distinct()
        .first()
    )


@require_GET
def api_state(request, token):
    person, guest = load_guest(request, token)
    media_set = guest.get_set()
    in_set = media_set.asset_ids()
    set_assets = {
        a.pk: a
        for a in MediaAsset.objects.filter(pk__in=in_set).select_related("service", "organization").prefetch_related("markers")
    }
    my_set = [asset_for_guest(set_assets[i], guest) for i in in_set if i in set_assets and set_assets[i].visible_to(person)]

    library = []
    if person.can_browse:
        candidates = (
            MediaAsset.library_for(person.organization)
            .exclude(pk__in=in_set)
            .select_related("service", "organization")
            .prefetch_related("markers")[:400]
        )
        library = [asset_for_guest(a, guest) for a in candidates]
    own_extra = (
        MediaAsset.objects.filter(uploaded_by=person)
        .exclude(pk__in=in_set)
        .exclude(approval=MediaAsset.APPROVAL_REJECTED)
        .select_related("service", "organization")
        .prefetch_related("markers")
    )
    library_ids = {item["id"] for item in library}
    for asset in own_extra:
        if asset.pk not in library_ids:
            library.insert(0, asset_for_guest(asset, guest))

    previous = previous_set(guest) if not in_set else None
    return JsonResponse(
        {
            "guest": {"name": person.name, "can_upload": person.can_upload, "can_browse": person.can_browse},
            "service": {"id": guest.service_id, "name": guest.service.name, "date": guest.service.date.isoformat()},
            "set": my_set,
            "library": library,
            "previous_set": (
                {"id": previous.pk, "label": event_label(previous.service), "count": previous.items.count()}
                if previous else None
            ),
        }
    )


@csrf_exempt
@require_POST
def api_set_copy(request, token):
    """Start this event's set from the person's set of another event."""
    person, guest = load_guest(request, token)
    source = get_object_or_404(MediaSet, pk=read_json(request).get("set_id"), guest__person=person)
    media_set = guest.get_set()
    with transaction.atomic():
        last = media_set.items.order_by("-position").first()
        position = (last.position + 1) if last else 0
        for item in source.items.select_related("asset").order_by("position", "id"):
            if not item.asset.visible_to(person):
                continue
            _, created = SetItem.objects.get_or_create(media_set=media_set, asset=item.asset, defaults={"position": position})
            if created:
                position += 1
    return api_state(request_as_get(request), token)


@csrf_exempt
@require_POST
def api_set_add(request, token):
    person, guest = load_guest(request, token)
    ids = [int(i) for i in read_json(request).get("asset_ids", []) if str(i).isdigit()]
    media_set = guest.get_set()
    with transaction.atomic():
        last = media_set.items.order_by("-position").first()
        position = (last.position + 1) if last else 0
        for asset in MediaAsset.objects.filter(pk__in=ids):
            if not asset.visible_to(person):
                continue
            _, created = SetItem.objects.get_or_create(media_set=media_set, asset=asset, defaults={"position": position})
            if created:
                position += 1
    return api_state(request_as_get(request), token)


@csrf_exempt
@require_POST
def api_set_remove(request, token):
    person, guest = load_guest(request, token)
    asset_id = read_json(request).get("asset_id")
    guest.get_set().items.filter(asset_id=asset_id).delete()
    return api_state(request_as_get(request), token)


@csrf_exempt
@require_POST
def api_set_order(request, token):
    person, guest = load_guest(request, token)
    ids = [int(i) for i in read_json(request).get("asset_ids", []) if str(i).isdigit()]
    media_set = guest.get_set()
    with transaction.atomic():
        for position, asset_id in enumerate(ids):
            media_set.items.filter(asset_id=asset_id).update(position=position)
    return api_state(request_as_get(request), token)


@csrf_exempt
@require_POST
def api_marker_add(request, token):
    person, guest = load_guest(request, token)
    body = read_json(request)
    asset = get_object_or_404(MediaAsset, pk=body.get("asset_id"))
    if not asset.visible_to(person) or not asset.is_timed:
        raise Http404()
    label = str(body.get("label", "")).strip()[:60]
    try:
        t_ms = max(0, int(body.get("t_ms", 0)))
    except (TypeError, ValueError):
        t_ms = 0
    if asset.duration_ms:
        t_ms = min(t_ms, asset.duration_ms)
    if label:
        Marker.objects.create(asset=asset, label=label, t_ms=t_ms, created_by=person.name)
    return api_state(request_as_get(request), token)


@csrf_exempt
@require_POST
def api_marker_delete(request, token):
    person, guest = load_guest(request, token)
    marker = get_object_or_404(Marker, pk=read_json(request).get("marker_id"))
    if marker.created_by == person.name or marker.asset.uploaded_by_id == person.id:
        marker.delete()
    return api_state(request_as_get(request), token)


@csrf_exempt
@require_POST
def api_private(request, token):
    person, guest = load_guest(request, token)
    body = read_json(request)
    asset = get_object_or_404(MediaAsset, pk=body.get("asset_id"), uploaded_by=person)
    asset.private = bool(body.get("private"))
    asset.save(update_fields=["private"])
    return api_state(request_as_get(request), token)


def thumb(request, token, asset_id):
    person = load_person(token, request)
    asset = get_object_or_404(MediaAsset, pk=asset_id)
    if not asset.visible_to(person):
        raise Http404()
    return serve_field(request, asset.thumb)


def request_as_get(request):
    request.method = "GET"
    return request


def media_file(request, token, asset_id):
    """The converted file, for previewing a video and placing markers."""
    person = load_person(token, request)
    asset = get_object_or_404(MediaAsset, pk=asset_id)
    if not asset.visible_to(person) or asset.status != MediaAsset.STATUS_READY:
        raise Http404()
    return serve_field(request, asset.processed, asset.local_name)
