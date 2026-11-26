"""The page a guest opens from their link: My set, Upload, Library."""
import json

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import config as vitrine_config
from . import pins
from .files import serve_field
from .models import Favorite, Guest, Marker, MediaAsset, MediaSet, Person, PersonAlias, SetItem


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
    return f"{event.name} · {event.short_when}{where}"


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
    favs = getattr(guest, "_fav_ids", None)
    if favs is None:
        favs = guest._fav_ids = set(Favorite.objects.filter(person=person).values_list("asset_id", flat=True))
    return {
        "id": asset.pk,
        "fav": asset.pk in favs,
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
                for g in person.upcoming_participations()
            ] + ([] if any(g.pk == guest.pk for g in person.upcoming_participations())
                 else [{"id": guest.service_id, "label": event_label(guest.service), "current": True}]),
            "max_upload_mb": vitrine_config.max_upload_mb(),
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
        .order_by("-service__starts_on", "-service_id")
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
    # One entry per set item: a file, or a clip of a video (the same video can come several times).
    my_set = []
    for item in media_set.items.order_by("position", "id"):
        asset = set_assets.get(item.asset_id)
        if asset is None or not asset.visible_to(person):
            continue
        entry = asset_for_guest(asset, guest)
        entry["item_id"] = item.pk
        entry["clip"] = item.clip_json()
        my_set.append(entry)

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
    favorites = []
    for fav in Favorite.objects.filter(person=person).select_related("asset__service", "asset__organization").prefetch_related("asset__markers"):
        if fav.asset.visible_to(person):
            item = asset_for_guest(fav.asset, guest)
            item["in_set"] = fav.asset_id in in_set
            favorites.append(item)
    for asset in own_extra:
        if asset.pk not in library_ids:
            library.insert(0, asset_for_guest(asset, guest))

    previous = previous_set(guest) if not in_set else None
    return JsonResponse(
        {
            "guest": {"name": person.name, "can_upload": person.can_upload, "can_browse": person.can_browse},
            "service": {
                "id": guest.service_id,
                "name": guest.service.name,
                "starts_on": guest.service.starts_on.isoformat() if guest.service.starts_on else None,
                "ends_on": guest.service.ends_on.isoformat() if guest.service.ends_on else None,
                "when": guest.service.when,
            },
            "set": my_set,
            "library": library,
            "favorites": favorites,
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
            same = media_set.items.filter(asset=item.asset, in_ms=item.in_ms, out_ms=item.out_ms)
            if same.exists():
                continue
            SetItem.objects.create(media_set=media_set, asset=item.asset, position=position, in_ms=item.in_ms,
                                   out_ms=item.out_ms, label=item.label, at_end=item.at_end)
            position += 1
    return api_state(request_as_get(request), token)


@csrf_exempt
@require_POST
def api_set_add(request, token):
    person, guest = load_guest(request, token)
    ids = [int(i) for i in read_json(request).get("asset_ids", []) if str(i).isdigit()]
    media_set = guest.get_set()
    with transaction.atomic():
        for asset in MediaAsset.objects.filter(pk__in=ids):
            if asset.visible_to(person):
                media_set.add_asset(asset)
    return api_state(request_as_get(request), token)


@csrf_exempt
@require_POST
def api_set_remove(request, token):
    person, guest = load_guest(request, token)
    body = read_json(request)
    items = guest.get_set().items
    if body.get("item_id"):
        items.filter(pk=body.get("item_id")).delete()
    else:  # older pages: the file and all its clips
        items.filter(asset_id=body.get("asset_id")).delete()
    return api_state(request_as_get(request), token)


@csrf_exempt
@require_POST
def api_set_order(request, token):
    person, guest = load_guest(request, token)
    body = read_json(request)
    media_set = guest.get_set()
    with transaction.atomic():
        if "item_ids" in body:
            ids = [int(i) for i in body.get("item_ids", []) if str(i).isdigit()]
            for position, item_id in enumerate(ids):
                media_set.items.filter(pk=item_id).update(position=position)
        else:  # older pages
            ids = [int(i) for i in body.get("asset_ids", []) if str(i).isdigit()]
            for position, asset_id in enumerate(ids):
                media_set.items.filter(asset_id=asset_id).update(position=position)
    from .notify import notify_agents

    notify_agents()  # .update() sends no signal
    return api_state(request_as_get(request), token)


def save_clip(media_set, body):
    """Shared by the guest page and the back tablet. body: item_id, in_ms, out_ms, label, at_end and
    mode: "update" (this entry), "new" (a new clip right after it), "clear" (the whole file again) or
    "restore" (put a removed entry back: asset_id and position). Returns the item. Raises ValueError."""
    mode = body.get("mode") or "update"
    if mode == "restore":
        asset = MediaAsset.objects.filter(pk=body.get("asset_id")).first()
        if asset is None:
            raise ValueError("That file doesn't exist any more.")
        item = SetItem(media_set=media_set, asset=asset, position=int(body.get("position") or 0))
    else:
        item = media_set.items.select_related("asset").filter(pk=body.get("item_id")).first()
        if item is None:
            raise ValueError("That entry isn't in the set any more.")
    if mode == "clear":
        item.set_clip(None, None)
        item.save()
        return item
    if mode == "new":
        item = SetItem(media_set=media_set, asset=item.asset, position=item.position + 1)
    item.set_clip(body.get("in_ms"), body.get("out_ms"), body.get("label", ""), body.get("at_end", SetItem.AT_END_HOLD))
    with transaction.atomic():
        if item.pk is None:  # make room after the source entry
            media_set.items.filter(position__gte=item.position).update(position=F("position") + 1)
        item.save()
    return item


@csrf_exempt
@require_POST
def api_set_clip(request, token):
    """Mark in / mark out on an entry of the set (see save_clip)."""
    person, guest = load_guest(request, token)
    body = read_json(request)
    media_set = guest.get_set()
    if body.get("mode") == "restore":
        asset = MediaAsset.objects.filter(pk=body.get("asset_id")).first()
        if asset is None or not asset.visible_to(person):
            raise Http404()
    try:
        save_clip(media_set, body)
    except (ValueError, TypeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
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


# ---------- the guest's own tablet view (practice, or live once the desk gives control) ----------


def _live_for(guest):
    """What this guest may know about the projector, and whether they have control."""
    from . import relay

    event = guest.service
    agent = event.location.agent if event.location_id else None
    data = relay.live_state(agent) if agent else None
    set_id = guest.get_set().pk
    out = {
        "set_id": set_id,
        "online": bool(data),
        "here": bool(data) and data.get("service_id") == event.pk,
        "control": False,
        "requested": False,
        "live": None,
        "audio": None,
        "other_on_screen": False,
    }
    if not data:
        return out
    out["control"] = out["here"] and set_id in (data.get("controls") or [])
    out["requested"] = out["here"] and set_id in (data.get("requests") or [])
    theirs = set(guest.get_set().asset_ids())
    engine = data.get("engine") or {}
    for key, src in (("live", engine), ("audio", engine.get("audio") or {})):
        asset_id = src.get("asset_id") or 0
        pending = src.get("pending_id") or 0
        if asset_id in theirs or pending in theirs:
            out[key] = {
                "asset_id": asset_id if asset_id in theirs else 0,
                "pending_id": pending if pending in theirs else 0,
                "kind": src.get("kind", "audio" if key == "audio" else ""),
                "position_ms": src.get("position_ms", 0),
                "duration_ms": src.get("duration_ms", 0),
                "paused": bool(src.get("paused")),
                # Which clip is on screen (0: the whole file), and its marks.
                "entry_id": src.get("entry_id", 0),
                "pending_entry": src.get("pending_entry", 0),
                "in_ms": src.get("in_ms", 0),
                "out_ms": src.get("out_ms", 0),
            }
        elif asset_id:
            out["other_on_screen"] = True  # someone else's file: no details
    return out


@require_GET
def screen(request, token):
    """The guest's tablet view: their set in the pulpit layout."""
    person, guest = load_guest(request, token)
    return render(request, "guestmedia/guest_screen.html", {"person": person, "guest": guest, "service": guest.service})


@require_GET
def api_live(request, token):
    person, guest = load_guest(request, token)
    return JsonResponse(_live_for(guest))


LIVE_ACTIONS = {"take", "clear", "pause", "play", "seek", "stop_audio", "request_access", "cancel_request"}


@csrf_exempt
@require_POST
def api_live_action(request, token, action):
    from . import relay

    person, guest = load_guest(request, token)
    if action not in LIVE_ACTIONS:
        raise Http404()
    event = guest.service
    agent = event.location.agent if event.location_id else None
    if agent is None or relay.live_state(agent) is None:
        return JsonResponse({"ok": False, "error": "The vMix PC isn't connected right now."}, status=409)
    state = _live_for(guest)
    if not state["here"]:
        return JsonResponse({"ok": False, "error": "The tablets are on another event right now."}, status=409)
    if action not in ("request_access", "cancel_request") and not state["control"]:
        return JsonResponse({"ok": False, "error": "Ask for access first: the production desk hasn't given you control."}, status=403)
    body = read_json(request)
    payload = {
        "action": action,
        "set_id": state["set_id"],
        "asset_id": int(body.get("asset_id") or 0),
        "entry_id": int(body.get("entry_id") or 0),
        "start_ms": int(body.get("start_ms") or 0),
        "position_ms": int(body.get("position_ms") or 0),
        "by": person.name,
    }
    if action == "take" and payload["asset_id"] not in guest.get_set().asset_ids():
        return JsonResponse({"ok": False, "error": "That file isn't in your set."}, status=403)
    if action == "take" and payload["entry_id"] and not guest.get_set().items.filter(
        pk=payload["entry_id"], asset_id=payload["asset_id"]
    ).exists():
        return JsonResponse({"ok": False, "error": "That clip isn't in your set."}, status=403)
    result = relay.send_command(agent, payload)
    if action == "request_access" and result.get("ok"):
        from . import alerts

        alerts.control_requested(person, event)
    return JsonResponse({"ok": bool(result.get("ok")), "error": result.get("error", ""), "live": _live_for(guest)},
                        status=200 if result.get("ok") else 409)


# ---------- permanent tablet link per location ----------


@require_GET
def location_tablet(request, slug):
    """/t/<slug>/: open this location's tablet view on its vMix PC (paired tablets land signed in)."""
    from .models import Location

    location = get_object_or_404(Location.objects.select_related("agent"), tablet_slug=slug)
    agent = location.agent
    if agent and agent.tablet_base_url:
        return HttpResponseRedirect(f"{agent.tablet_base_url}/screen")
    return render(request, "guestmedia/tablet_offline.html", {"location": location}, status=503)


@csrf_exempt
@require_POST
def api_favorite(request, token):
    """♥ a file or take the ♥ away: {"asset_id": 12, "on": true}."""
    person, guest = load_guest(request, token)
    data = read_json(request)
    asset = MediaAsset.objects.filter(pk=data.get("asset_id") or 0).first()
    if asset is not None and asset.visible_to(person):
        if data.get("on"):
            Favorite.objects.get_or_create(person=person, asset=asset)
        else:
            Favorite.objects.filter(person=person, asset=asset).delete()
    return api_state(request_as_get(request), token)
