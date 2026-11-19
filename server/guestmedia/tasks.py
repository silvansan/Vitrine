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

    try:
        result = process_file(asset.original.path, asset.original_name)
    except Exception as exc:
        logger.exception("Processing failed for asset %s", asset_id)
        asset.status = MediaAsset.STATUS_FAILED
        asset.error = str(exc)[-2000:]
        asset.save(update_fields=["status", "error"])
        return

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
