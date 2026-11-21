"""A small tus 1.0 server (core + creation + termination) for guest uploads.

Phone uploads over venue Wi-Fi drop all the time; tus lets the browser resume
where it stopped instead of starting a 300 MB video again.
"""
import base64
import os
import uuid
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from .models import MediaAsset, SetItem, Upload
from .tasks import process_asset

TUS_VERSION = "1.0.0"


def tus_response(status=204, **headers):
    response = HttpResponse(status=status)
    response["Tus-Resumable"] = TUS_VERSION
    response["Cache-Control"] = "no-store"
    for key, value in headers.items():
        response[key.replace("_", "-")] = str(value)
    return response


def parse_metadata(header):
    meta = {}
    for pair in (header or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        if " " in pair:
            key, value = pair.split(" ", 1)
            try:
                meta[key] = base64.b64decode(value).decode("utf-8", "replace")
            except Exception:
                meta[key] = ""
        else:
            meta[pair] = ""
    return meta


def safe_filename(name):
    name = os.path.basename(name or "upload").replace("\x00", "")
    return name[:200] or "upload"


def guest_for(request, token):
    """The participation uploads go to (?e=<event id> on the upload endpoint), if the person may upload."""
    from django.core.exceptions import PermissionDenied
    from django.http import Http404

    from .guest_views import load_guest

    try:
        person, guest = load_guest(request, token)
    except (Http404, PermissionDenied):
        return None
    if not person.can_upload:
        return None
    return guest


@csrf_exempt
def collection(request, token):
    guest = guest_for(request, token)
    if guest is None:
        return tus_response(403)
    max_size = settings.VITRINE_MAX_UPLOAD_MB * 1024 * 1024

    if request.method == "OPTIONS":
        return tus_response(
            204,
            Tus_Version=TUS_VERSION,
            Tus_Extension="creation,termination",
            Tus_Max_Size=max_size,
        )
    if request.method != "POST":
        return tus_response(405)

    try:
        length = int(request.headers.get("Upload-Length", ""))
    except ValueError:
        return tus_response(400)
    if length <= 0 or length > max_size:
        return tus_response(413)

    meta = parse_metadata(request.headers.get("Upload-Metadata"))
    upload = Upload.objects.create(
        id=uuid.uuid4(),
        guest=guest,
        filename=safe_filename(meta.get("filename") or meta.get("name")),
        mime=(meta.get("filetype") or meta.get("type") or "")[:120],
        length=length,
    )
    settings.UPLOAD_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    upload.temp_path.touch()
    location = request.build_absolute_uri(f"{upload.id}")
    return tus_response(201, Location=location, Upload_Offset=0)


@csrf_exempt
def resource(request, token, upload_id):
    from .guest_views import load_person

    try:
        person = load_person(token, request)
    except Exception:
        return tus_response(403)
    if not person.can_upload:
        return tus_response(403)
    upload = Upload.objects.filter(pk=upload_id, guest__person=person).first()
    if upload is None:
        return tus_response(404)

    if request.method == "HEAD":
        return tus_response(200, Upload_Offset=upload.offset, Upload_Length=upload.length)

    if request.method == "DELETE":
        Path(upload.temp_path).unlink(missing_ok=True)
        upload.delete()
        return tus_response(204)

    if request.method != "PATCH":
        return tus_response(405)
    if request.headers.get("Content-Type") != "application/offset+octet-stream":
        return tus_response(415)
    try:
        offset = int(request.headers.get("Upload-Offset", ""))
    except ValueError:
        return tus_response(400)
    if offset != upload.offset or upload.completed:
        return tus_response(409, Upload_Offset=upload.offset)

    data = request.body
    if upload.offset + len(data) > upload.length:
        return tus_response(400)
    with open(upload.temp_path, "r+b") as handle:
        handle.seek(offset)
        handle.write(data)
    upload.offset = offset + len(data)
    upload.save(update_fields=["offset"])

    if upload.offset == upload.length:
        finish_upload(upload)
    return tus_response(204, Upload_Offset=upload.offset)


def finish_upload(upload):
    """Move the finished file into the library and start processing."""
    guest = upload.guest
    relative = f"originals/{upload.id.hex}/{upload.filename}"
    target = Path(settings.MEDIA_ROOT) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(upload.temp_path, target)

    asset = MediaAsset.objects.create(
        owner_name=guest.person.name,
        uploaded_by=guest.person,
        organization=guest.person.organization,
        service=guest.service,
        original_name=upload.filename,
        original_size=upload.length,
    )
    asset.original.name = relative
    asset.save(update_fields=["original"])

    media_set = guest.get_set()
    last = media_set.items.order_by("-position").first()
    SetItem.objects.get_or_create(
        media_set=media_set,
        asset=asset,
        defaults={"position": (last.position + 1) if last else 0},
    )

    upload.completed = True
    upload.asset = asset
    upload.save(update_fields=["completed", "asset"])
    process_asset.delay(asset.pk)
    return asset
