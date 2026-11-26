"""HTTP API used by the agent on the vMix PC (Bearer token auth)."""
import json
from datetime import timedelta
from functools import wraps

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .files import serve_field
from .manifest import build_manifest
from .models import Agent, Marker, MediaAsset, ScreenGrant, hash_token
from .signing import sign_pairing


def agent_from_request(request):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    raw = header[7:].strip()
    return Agent.objects.filter(token_hash=hash_token(raw)).first()


def agent_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        agent = agent_from_request(request)
        if agent is None:
            return JsonResponse({"error": "unauthorized"}, status=401)
        request.agent = agent
        return view(request, *args, **kwargs)

    return wrapper


@require_GET
@agent_required
def manifest(request):
    return JsonResponse(build_manifest(request.agent))


@csrf_exempt
@require_POST
@agent_required
def heartbeat(request):
    agent = request.agent
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        body = {}
    agent.last_seen = timezone.now()
    agent.lan_ip = str(body.get("lan_ip", ""))[:64]
    try:
        agent.listen_port = int(body.get("listen_port") or agent.listen_port)
    except (TypeError, ValueError):
        pass
    agent.version = str(body.get("version", ""))[:40]
    agent.status = body
    agent.save(update_fields=["last_seen", "lan_ip", "listen_port", "version", "status"])

    used = [int(i) for i in (body.get("used_assets") or []) if str(i).isdigit()]
    if used:
        for asset in MediaAsset.objects.filter(pk__in=used):
            asset.last_used = timezone.now()
            asset.save(update_fields=["last_used"])
    return JsonResponse({"manifest_version": agent.current_manifest_version})


@require_GET
@agent_required
def file(request, asset_id, variant):
    asset = get_object_or_404(MediaAsset, pk=asset_id)
    if variant == "processed":
        if not asset.is_playable:
            raise Http404()
        return serve_field(request, asset.processed, asset.local_name)
    if variant == "thumb":
        return serve_field(request, asset.thumb)
    if variant == "filmstrip":
        return serve_field(request, asset.filmstrip)
    raise Http404()


@csrf_exempt
@require_POST
@agent_required
def pairing(request):
    """Issue a pairing code from the tray companion (while online)."""
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        body = {}
    role = body.get("role") if body.get("role") in (ScreenGrant.ROLE_BACK, ScreenGrant.ROLE_PULPIT) else ScreenGrant.ROLE_PULPIT
    name = str(body.get("name") or ("Pulpit tablet" if role == "pulpit" else "Back tablet"))[:120]
    grant = ScreenGrant.objects.create(
        agent=request.agent, role=role, name=name, expires_at=timezone.now() + timedelta(minutes=15)
    )
    return JsonResponse({"token": sign_pairing(grant), "expires_at": grant.expires_at.isoformat()})


@csrf_exempt
@require_POST
@agent_required
def markers(request):
    """Markers added or removed on a tablet ("Add marker here")."""
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        body = {}
    added = []
    for item in body.get("add", []):
        asset = MediaAsset.objects.filter(pk=item.get("asset_id"), kind__in=MediaAsset.TIMED_KINDS).first()
        label = str(item.get("label", "")).strip()[:60]
        if asset is None or not label:
            continue
        try:
            t_ms = max(0, int(item.get("t_ms", 0)))
        except (TypeError, ValueError):
            continue
        if asset.duration_ms:
            t_ms = min(t_ms, asset.duration_ms)
        marker = Marker.objects.create(asset=asset, label=label, t_ms=t_ms, created_by=str(item.get("by", "tablet"))[:120])
        added.append(marker.pk)
    ids = [int(i) for i in body.get("delete", []) if str(i).isdigit()]
    deleted = Marker.objects.filter(pk__in=ids).delete()[0] if ids else 0
    return JsonResponse({"added": added, "deleted": deleted})


@csrf_exempt
@require_POST
@agent_required
def clips(request):
    """Mark in / mark out set on the back tablet (same rules as the guest page)."""
    from .guest_views import save_clip
    from .models import MediaSet

    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        body = {}
    agent = request.agent
    location = getattr(agent, "location", None)
    media_set = MediaSet.objects.filter(pk=body.get("set_id"), service__location=location).first() if location else None
    if media_set is None:
        return JsonResponse({"ok": False, "error": "That set isn't in an event of this location."}, status=404)
    if body.get("mode") == "delete":
        media_set.items.filter(pk=body.get("item_id")).delete()
        return JsonResponse({"ok": True})
    try:
        item = save_clip(media_set, body)
    except (ValueError, TypeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "item_id": item.pk})
