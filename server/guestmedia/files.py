"""Serving protected files.

In Docker, nginx does the actual sending (X-Accel-Redirect), including Range
requests, so resumable downloads by the agent are cheap. Without nginx (tests,
local development) Django streams the file itself and honours a single Range.
"""
import mimetypes
import os
import re
from urllib.parse import quote

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _iter_file(path, start, length, chunk=1024 * 1024):
    with open(path, "rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            data = handle.read(min(chunk, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


def serve_field(request, field, download_name=None):
    if not field:
        raise Http404("No such file")
    path = field.path
    if not os.path.exists(path):
        raise Http404("File missing on disk")
    content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"

    if settings.VITRINE_X_ACCEL:
        response = HttpResponse(content_type=content_type)
        response["X-Accel-Redirect"] = settings.MEDIA_URL + quote(field.name)
        response["X-Accel-Buffering"] = "no"
        if download_name:
            response["Content-Disposition"] = f'inline; filename="{download_name}"'
        return response

    size = os.path.getsize(path)
    range_header = request.headers.get("Range", "")
    match = RANGE_RE.fullmatch(range_header.strip()) if range_header else None
    if match:
        start_text, end_text = match.groups()
        if start_text == "":
            length = int(end_text or 0)
            start = max(0, size - length)
            end = size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            return response
        length = end - start + 1
        response = StreamingHttpResponse(_iter_file(path, start, length), status=206, content_type=content_type)
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
        response["Content-Length"] = str(length)
    else:
        response = FileResponse(open(path, "rb"), content_type=content_type)
        response["Content-Length"] = str(size)
    response["Accept-Ranges"] = "bytes"
    if download_name:
        response["Content-Disposition"] = f'inline; filename="{download_name}"'
    return response
