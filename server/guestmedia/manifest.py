"""What an agent needs to know: active services, their sets, and the files in them."""
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
        "markers": [{"label": m.label, "t_ms": m.t_ms} for m in asset.markers.all()],
    }
    return entry


def build_manifest(agent):
    services = list(Service.objects.filter(is_active=True).order_by("date", "id"))
    sets = (
        MediaSet.objects.filter(service__in=services)
        .select_related("guest")
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
                "guest": media_set.guest.name if media_set.guest else media_set.title,
                "items": playable,
            }
        )

    assets = MediaAsset.objects.filter(pk__in=asset_ids).prefetch_related("markers")
    revoked = list(ScreenGrant.objects.filter(agent=agent, revoked=True).values_list("nonce", flat=True))

    return {
        "version": agent.manifest_version,
        "server_time": timezone.now().isoformat(),
        "agent": {"id": agent.pk, "name": agent.name},
        "pubkey": public_key_b64(),
        "services": [
            {"id": s.pk, "name": s.name, "date": s.date.isoformat()} for s in services
        ],
        "sets": set_entries,
        "assets": [asset_entry(a) for a in assets],
        "revoked_screens": revoked,
    }
