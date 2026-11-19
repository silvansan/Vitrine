"""The page a guest opens from their link: My set, Upload, Library."""
import json

from django.conf import settings
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .files import serve_field
from .models import Guest, Marker, MediaAsset, SetItem


def load_guest(token):
    guest = get_object_or_404(Guest.objects.select_related("service"), token=token)
    if guest.is_expired:
        raise Http404("This link has expired")
    return guest


def read_json(request):
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return {}


def asset_for_guest(asset, guest):
    if asset.uploaded_by_id == guest.id:
        when = "Your upload"
        group = "mine"
    elif asset.service_id == guest.service_id:
        when = "This service"
        group = "today"
    else:
        service = asset.service.name if asset.service else "the library"
        when = f"From {service}"
        group = "earlier"
    return {
        "id": asset.pk,
        "kind": asset.kind or "processing",
        "owner": asset.owner_name,
        "name": asset.original_name,
        "status": asset.status,
        "approval": asset.approval,
        "private": asset.private,
        "mine": asset.uploaded_by_id == guest.id,
        "duration_ms": asset.duration_ms,
        "thumb": f"/g/{guest.token}/thumb/{asset.pk}" if asset.thumb else "",
        "when": when,
        "group": group,
        "markers": [
            {"id": m.pk, "label": m.label, "t_ms": m.t_ms, "mine": m.created_by == guest.name}
            for m in asset.markers.all()
        ],
    }


def page(request, token):
    guest = load_guest(token)
    return render(
        request,
        "guestmedia/guest.html",
        {
            "guest": guest,
            "service": guest.service,
            "max_upload_mb": settings.VITRINE_MAX_UPLOAD_MB,
        },
    )


@require_GET
def api_state(request, token):
    guest = load_guest(token)
    media_set = guest.get_set()
    in_set = media_set.asset_ids()
    set_assets = {
        a.pk: a
        for a in MediaAsset.objects.filter(pk__in=in_set).select_related("service").prefetch_related("markers")
    }
    my_set = [asset_for_guest(set_assets[i], guest) for i in in_set if i in set_assets and set_assets[i].visible_to(guest)]

    library = []
    if guest.can_browse:
        candidates = (
            MediaAsset.objects.filter(status=MediaAsset.STATUS_READY, approval=MediaAsset.APPROVAL_APPROVED, private=False)
            .exclude(pk__in=in_set)
            .select_related("service")
            .prefetch_related("markers")[:400]
        )
        library = [asset_for_guest(a, guest) for a in candidates]
    own_extra = (
        MediaAsset.objects.filter(uploaded_by=guest)
        .exclude(pk__in=in_set)
        .exclude(approval=MediaAsset.APPROVAL_REJECTED)
        .select_related("service")
        .prefetch_related("markers")
    )
    library_ids = {item["id"] for item in library}
    for asset in own_extra:
        if asset.pk not in library_ids:
            library.insert(0, asset_for_guest(asset, guest))

    return JsonResponse(
        {
            "guest": {"name": guest.name, "can_upload": guest.can_upload, "can_browse": guest.can_browse},
            "service": {"name": guest.service.name, "date": guest.service.date.isoformat()},
            "set": my_set,
            "library": library,
        }
    )


@csrf_exempt
@require_POST
def api_set_add(request, token):
    guest = load_guest(token)
    ids = [int(i) for i in read_json(request).get("asset_ids", []) if str(i).isdigit()]
    media_set = guest.get_set()
    with transaction.atomic():
        last = media_set.items.order_by("-position").first()
        position = (last.position + 1) if last else 0
        for asset in MediaAsset.objects.filter(pk__in=ids):
            if not asset.visible_to(guest):
                continue
            _, created = SetItem.objects.get_or_create(media_set=media_set, asset=asset, defaults={"position": position})
            if created:
                position += 1
    return api_state(request_as_get(request), token)


@csrf_exempt
@require_POST
def api_set_remove(request, token):
    guest = load_guest(token)
    asset_id = read_json(request).get("asset_id")
    guest.get_set().items.filter(asset_id=asset_id).delete()
    return api_state(request_as_get(request), token)


@csrf_exempt
@require_POST
def api_set_order(request, token):
    guest = load_guest(token)
    ids = [int(i) for i in read_json(request).get("asset_ids", []) if str(i).isdigit()]
    media_set = guest.get_set()
    with transaction.atomic():
        for position, asset_id in enumerate(ids):
            media_set.items.filter(asset_id=asset_id).update(position=position)
    return api_state(request_as_get(request), token)


@csrf_exempt
@require_POST
def api_marker_add(request, token):
    guest = load_guest(token)
    body = read_json(request)
    asset = get_object_or_404(MediaAsset, pk=body.get("asset_id"))
    if not asset.visible_to(guest) or asset.kind != MediaAsset.KIND_VIDEO:
        raise Http404()
    label = str(body.get("label", "")).strip()[:60]
    try:
        t_ms = max(0, int(body.get("t_ms", 0)))
    except (TypeError, ValueError):
        t_ms = 0
    if asset.duration_ms:
        t_ms = min(t_ms, asset.duration_ms)
    if label:
        Marker.objects.create(asset=asset, label=label, t_ms=t_ms, created_by=guest.name)
    return api_state(request_as_get(request), token)


@csrf_exempt
@require_POST
def api_marker_delete(request, token):
    guest = load_guest(token)
    marker = get_object_or_404(Marker, pk=read_json(request).get("marker_id"))
    if marker.created_by == guest.name or marker.asset.uploaded_by_id == guest.id:
        marker.delete()
    return api_state(request_as_get(request), token)


@csrf_exempt
@require_POST
def api_private(request, token):
    guest = load_guest(token)
    body = read_json(request)
    asset = get_object_or_404(MediaAsset, pk=body.get("asset_id"), uploaded_by=guest)
    asset.private = bool(body.get("private"))
    asset.save(update_fields=["private"])
    return api_state(request_as_get(request), token)


def thumb(request, token, asset_id):
    guest = load_guest(token)
    asset = get_object_or_404(MediaAsset, pk=asset_id)
    if not asset.visible_to(guest):
        raise Http404()
    return serve_field(request, asset.thumb)


def request_as_get(request):
    request.method = "GET"
    return request
