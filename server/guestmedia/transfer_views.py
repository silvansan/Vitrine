"""Settings → Move to another server: download everything, or bring it in on the new server."""
import os
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import transfer
from .orgs import org_view


def _superuser(request):
    if not request.user.is_superuser:
        raise Http404()


def _back():
    return redirect(reverse("manage-settings") + "#move")


def context(request):
    """Template context for the card on the Settings page."""
    from django.utils.dateparse import parse_datetime

    export = transfer.current_export()
    if export and export.get("state") == "done":
        export["url"] = request.build_absolute_uri(reverse("manage-move-download", args=[export["token"], export["file"]]))
        export["expires_at"] = parse_datetime(export.get("expires", "") or "")
    imported = transfer.import_status()
    if imported and imported.get("manifest"):
        imported["created_at"] = parse_datetime(imported["manifest"].get("created", "") or "")
    return {"move_export": export, "move_import": imported, "move_link_hours": transfer.LINK_HOURS}


@org_view
@require_POST
def move(request):
    _superuser(request)
    action = request.POST.get("action")
    busy = transfer.import_running() or (transfer.current_export() or {}).get("state") == "running"
    if action == "export":
        if busy:
            messages.error(request, "An export or import is already running.")
        else:
            transfer.start_export()
            messages.success(request, "Preparing the archive. This page shows the progress.")
    elif action == "export_delete":
        transfer.delete_exports()
        messages.success(request, "Archive deleted; its link no longer works.")
    elif action == "import_link":
        url = request.POST.get("url", "").strip()
        if request.POST.get("confirm", "").strip().upper() != "REPLACE":
            messages.error(request, "Type REPLACE to confirm.")
        elif not url.startswith(("https://", "http://")) or "/manage/move/download/" not in url:
            messages.error(request, "Paste the archive link from the old server (Settings → Move to another server → Copy link).")
        elif busy:
            messages.error(request, "An export or import is already running.")
        else:
            transfer.start_import(url=url, by=request.user.get_username())
            messages.success(request, "Import started. You'll be signed out when the database is replaced; then sign in with an account from the old server.")
    elif action == "import_clear":
        if not transfer.import_running():
            transfer.import_status_path().unlink(missing_ok=True)
    return _back()


@org_view
def move_status(request):
    _superuser(request)
    export = transfer.current_export() or {}
    imported = transfer.import_status() or {}
    keys = ("state", "step", "done_bytes", "total_bytes")
    return JsonResponse({"export": {k: export.get(k) for k in keys}, "import": {k: imported.get(k) for k in keys}})


def move_download(request, token, filename):
    """The archive, for a signed-in superuser or anyone with the link (the token is the secret)."""
    path = transfer.export_file(token)
    if path is None or path.name != filename:
        raise Http404("This link has expired or the archive was deleted.")
    if settings.VITRINE_X_ACCEL:
        response = HttpResponse(content_type="application/x-tar")
        response["X-Accel-Redirect"] = settings.MEDIA_URL + quote(f"_exports/{token}/{path.name}")
        response["X-Accel-Buffering"] = "no"
    else:
        response = FileResponse(open(path, "rb"), content_type="application/x-tar")
        response["Content-Length"] = str(os.path.getsize(path))
    response["Content-Disposition"] = f'attachment; filename="{path.name}"'
    response["Cache-Control"] = "no-store"
    return response
