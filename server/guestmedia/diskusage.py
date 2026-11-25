"""How full the server is: Settings → Storage, the warning on Events, and an alert.

Two disks matter:
- the data disk, where /data lives (uploads, converted files, thumbnails, archives). On a
  Hetzner server this can be a separate volume (VITRINE_DATA_PATH in the stack);
- the system disk (Docker images, container logs, and the database unless VITRINE_DB_PATH
  moves it too). When /data is on the same disk, both are one.
"""
import os
import shutil
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.models import Count, Sum

LOW_PERCENT = 10          # warn below 10 % free …
LOW_BYTES = 5 * 1024 ** 3  # … or below 5 GB free
ALERT_EVERY = 12 * 3600

LABELS = {
    "originals": "Uploads as received",
    "library": "Converted for vMix, thumbnails, filmstrips",
    "incoming": "Uploads in progress",
    "_exports": "Server-move archive (Settings → Move to another server)",
}


def disk(path):
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {
        "total": usage.total,
        "used": usage.total - usage.free,
        "free": usage.free,
        "percent": round((usage.total - usage.free) * 100 / usage.total) if usage.total else 0,
        "low": usage.total > 0 and (usage.free * 100 / usage.total < LOW_PERCENT or usage.free < LOW_BYTES),
    }


def _folder_size(path):
    total = 0
    count = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
                count += 1
            except OSError:
                pass
    return total, count


def media_breakdown(fresh=False):
    """Size of each top-level folder of /data/media, cached 10 minutes (walking can take a while)."""
    key = "vitrine:disk:media"
    data = None if fresh else cache.get(key)
    if data is None:
        media = Path(settings.MEDIA_ROOT)
        rows = []
        if media.exists():
            for child in sorted(media.iterdir()):
                if child.is_dir():
                    size, count = _folder_size(child)
                else:
                    size, count = child.stat().st_size, 1
                rows.append({"name": child.name, "label": LABELS.get(child.name, child.name), "bytes": size, "files": count})
        rows.sort(key=lambda r: -r["bytes"])
        data = {"rows": rows, "bytes": sum(r["bytes"] for r in rows), "files": sum(r["files"] for r in rows)}
        cache.set(key, data, 600)
    return data


def database_bytes():
    try:
        if connection.vendor == "postgresql":
            with connection.cursor() as cur:
                cur.execute("SELECT pg_database_size(current_database())")
                return cur.fetchone()[0]
        name = settings.DATABASES["default"]["NAME"]
        return os.path.getsize(name) if isinstance(name, (str, Path)) and os.path.exists(name) else 0
    except Exception:
        return 0


def same_disk(a, b):
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return True


def report(fresh=False):
    from .models import MediaAsset

    data_path = Path(settings.MEDIA_ROOT).parent
    data_path = data_path if data_path.exists() else Path(settings.MEDIA_ROOT)
    separate = not same_disk(data_path, "/")
    by_org = list(
        MediaAsset.all_objects.values("organization__name")
        .annotate(bytes=Sum("original_size"), files=Count("id"))
        .order_by("-bytes")[:12]
    )
    trash = MediaAsset.all_objects.filter(deleted_at__isnull=False).aggregate(bytes=Sum("original_size"), files=Count("id"))
    return {
        "data": disk(data_path),
        "system": disk("/") if separate else None,
        "separate": separate,
        "media": media_breakdown(fresh),
        "database": database_bytes(),
        "by_org": by_org,
        "trash": {"bytes": trash["bytes"] or 0, "files": trash["files"] or 0},
        "trash_days": settings.VITRINE_TRASH_DAYS,
    }


def low_disks():
    """[(label, disk)] for the disks that are nearly full."""
    data_path = Path(settings.MEDIA_ROOT).parent
    out = []
    data = disk(data_path)
    if data and data["low"]:
        out.append(("files", data))
    if not same_disk(data_path, "/"):
        system = disk("/")
        if system and system["low"]:
            out.append(("system", system))
    return out


@shared_task
def check_disk_space():
    """Every 30 minutes: alert the organizations that want backup alerts when a disk is nearly full."""
    from .alerts import notify
    from .models import Organization

    low = low_disks()
    if not low or cache.get("vitrine:disk:alerted"):
        return
    cache.set("vitrine:disk:alerted", True, ALERT_EVERY)
    lines = []
    for which, d in low:
        name = "The disk with the uploads" if which == "files" else "The server's system disk"
        lines.append(f"{name} is {d['percent']} % full: {_gb(d['free'])} free of {_gb(d['total'])}.")
    lines.append("When it is full, uploads fail and the database stops. Free space (Settings → Storage shows what "
                 "takes it; old Docker images: docker image prune -a) or give the server a bigger volume.")
    for org in Organization.objects.filter(notify_backup=True):
        notify(org, "[Vitrine] Disk nearly full", "\n".join(lines))


def _gb(n):
    return f"{n / 1024 ** 3:.1f} GB"
