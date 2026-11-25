"""Move Vitrine to another server: one archive with everything, and importing it.

Export (Settings → Move to another server, superusers): a background task writes
    /data/media/_exports/<token>/vitrine-full-YYYYMMDD-HHMM.tar
an uncompressed tar (photos and videos are already compressed) holding, in this order:
    manifest.json    format, version, when, counts
    db.dump          the database (pg_dump custom format; db.sqlite3 on SQLite)
    keys/…           the pairing key (tablets stay paired)
    media/…          every upload, converted file and thumbnail
It can be downloaded by a signed-in superuser, or by anyone holding its link for 48 hours
(the token is the secret), which lets the new server fetch it directly.

Import (on the new server): from that link, or from the .tar uploaded in the browser (tus,
resumable). The archive is read as a stream: files are written as they arrive, the
database is restored last (in one transaction, so a broken archive leaves it untouched),
then the pairing key. Progress lives in a JSON file next to the data, because the database
itself is replaced half-way.
"""
import io
import json
import logging
import os
import secrets
import shutil
import subprocess
import tarfile
import time
import urllib.request
from datetime import timedelta
from pathlib import Path, PurePosixPath

from celery import shared_task
from django.conf import settings
from django.db import connection
from django.utils import timezone

from .backup import dump_database

log = logging.getLogger(__name__)

FORMAT = "vitrine-full"
VERSION = 1
LINK_HOURS = 48


# ---------- where things live ----------

def exports_root():
    return Path(settings.MEDIA_ROOT) / "_exports"


def imports_root():
    return Path(settings.MEDIA_ROOT).parent / "imports"


def import_status_path():
    return Path(settings.MEDIA_ROOT).parent / "import-status.json"


def skipped_media_dirs():
    """Folders inside MEDIA_ROOT that are never part of an archive."""
    media = Path(settings.MEDIA_ROOT)
    skip = {exports_root()}
    incoming = Path(settings.UPLOAD_TMP_ROOT)
    if incoming.is_relative_to(media):
        skip.add(incoming)
    return skip


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, default=str))
    os.replace(tmp, path)


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


# ---------- export ----------

def current_export():
    """The newest export: {"token", "state", "step", "file", "bytes", "done_bytes", "total_bytes", "expires", ...} or None."""
    root = exports_root()
    if not root.exists():
        return None
    found = []
    for folder in root.iterdir():
        status = _read_json(folder / "status.json")
        if status:
            status["token"] = folder.name
            found.append(status)
    if not found:
        return None
    found.sort(key=lambda s: s.get("started", ""), reverse=True)
    export = found[0]
    if export.get("state") == "done" and export.get("expires") and export["expires"] < timezone.now().isoformat():
        export["state"] = "expired"
    return export


def export_file(token):
    """Path of a finished, unexpired export for this token, or None."""
    if not token or "/" in token or token.startswith("."):
        return None
    status = _read_json(exports_root() / token / "status.json")
    if not status or status.get("state") != "done" or status.get("expires", "") < timezone.now().isoformat():
        return None
    path = exports_root() / token / status["file"]
    return path if path.exists() else None


def delete_exports():
    shutil.rmtree(exports_root(), ignore_errors=True)


def media_size():
    total = 0
    count = 0
    skip = skipped_media_dirs()
    for path in _media_files(skip):
        try:
            total += path.stat().st_size
            count += 1
        except OSError:
            pass
    return total, count


def _media_files(skip):
    media = Path(settings.MEDIA_ROOT)
    if not media.exists():
        return
    for dirpath, dirnames, filenames in os.walk(media):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if (here / d) not in skip)
        for name in sorted(filenames):
            yield here / name


def start_export():
    """Make a folder for a new export and queue the task. Returns the token."""
    delete_exports()  # one at a time: archives are as big as all media together
    token = secrets.token_urlsafe(24)
    folder = exports_root() / token
    _write_json(folder / "status.json", {"state": "running", "step": "Starting…", "started": timezone.now().isoformat()})
    build_export.delay(token)
    return token


@shared_task
def build_export(token):
    folder = exports_root() / token
    status_path = folder / "status.json"
    status = _read_json(status_path) or {"started": timezone.now().isoformat()}

    def update(**changes):
        status.update(changes)
        _write_json(status_path, status)

    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    lines = []
    try:
        total, count = media_size()
        free = shutil.disk_usage(folder).free
        if free < total * 1.05 + 200 * 1024 * 1024:
            raise RuntimeError(
                f"Not enough free disk space: the archive needs about {_gb(total)} and {_gb(free)} is free. "
                "Free some space (old Docker images: docker image prune -a) or move the files another way."
            )
        update(step="Saving the database…", total_bytes=total, done_bytes=0)
        dump = dump_database(folder, stamp, lines)
        db_name = "db.dump" if dump.suffix == ".dump" else "db.sqlite3"

        archive = folder / f"vitrine-full-{stamp}.tar"
        manifest = {
            "format": FORMAT,
            "version": VERSION,
            "created": timezone.now().isoformat(),
            "server": os.environ.get("VITRINE_PUBLIC_URL", ""),
            "database": db_name,
            "media_files": count,
            "media_bytes": total,
        }
        done = 0
        last_report = 0.0
        with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as tar:
            data = json.dumps(manifest, indent=2).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
            tar.add(dump, arcname=db_name)
            key = Path(settings.VITRINE_KEY_PATH)
            if key.exists():
                tar.add(key, arcname=f"keys/{key.name}")
            update(step="Packing the files…")
            media = Path(settings.MEDIA_ROOT)
            for path in _media_files(skipped_media_dirs()):
                try:
                    tar.add(path, arcname="media/" + path.relative_to(media).as_posix(), recursive=False)
                except OSError as exc:
                    lines.append(f"skipped {path.name}: {exc}")
                    continue
                done += path.stat().st_size
                if time.monotonic() - last_report > 2:
                    last_report = time.monotonic()
                    update(done_bytes=done)
        dump.unlink(missing_ok=True)
        update(
            state="done",
            step="Ready",
            file=archive.name,
            bytes=archive.stat().st_size,
            done_bytes=total,
            finished=timezone.now().isoformat(),
            expires=(timezone.now() + timedelta(hours=LINK_HOURS)).isoformat(),
            notes=lines[-20:],
        )
    except Exception as exc:
        log.exception("export failed")
        for leftover in folder.glob("vitrine-*"):
            leftover.unlink(missing_ok=True)
        for leftover in folder.glob("*.tar"):
            leftover.unlink(missing_ok=True)
        update(state="failed", step="Failed", error=str(exc)[-2000:], finished=timezone.now().isoformat())


def _gb(n):
    return f"{n / 1024 ** 3:.1f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


# ---------- import ----------

def import_status():
    return _read_json(import_status_path())


def import_running():
    status = import_status()
    if not status or status.get("state") != "running":
        return False
    # A task that died (container restarted) stops counting as running after 15 min without progress.
    return status.get("updated", "") > (timezone.now() - timedelta(minutes=15)).isoformat()


def start_import(url=None, path=None, by=""):
    _write_json(import_status_path(), {
        "state": "running", "step": "Starting…", "started": timezone.now().isoformat(),
        "updated": timezone.now().isoformat(), "source": "link" if url else "upload", "by": by,
    })
    run_import.delay(url=url, path=str(path) if path else None)


class _Counter:
    """Wraps a stream and reports how much was read."""

    def __init__(self, raw, report):
        self.raw = raw
        self.report = report
        self.count = 0

    def read(self, n=-1):
        data = self.raw.read(n)
        self.count += len(data)
        self.report(self.count)
        return data


@shared_task
def run_import(url=None, path=None):
    status = import_status() or {}
    last = [0.0]

    def update(**changes):
        status.update(changes)
        status["updated"] = timezone.now().isoformat()
        _write_json(import_status_path(), status)

    def progress(count):
        if time.monotonic() - last[0] > 2:
            last[0] = time.monotonic()
            update(done_bytes=count)

    work = imports_root()
    work.mkdir(parents=True, exist_ok=True)
    db_file = None
    response = None
    raw = None
    try:
        if url:
            update(step="Downloading from the old server…")
            request = urllib.request.Request(url, headers={"User-Agent": "Vitrine-import"})
            response = urllib.request.urlopen(request, timeout=120)
            total = int(response.headers.get("Content-Length") or 0)
            raw = response
        else:
            update(step="Reading the uploaded archive…")
            total = os.path.getsize(path)
            raw = open(path, "rb")
        update(total_bytes=total, done_bytes=0)
        stream = _Counter(raw, progress)

        media = Path(settings.MEDIA_ROOT)
        manifest = None
        key_data = {}
        files = 0
        with tarfile.open(fileobj=stream, mode="r|") as tar:
            for member in tar:
                name = PurePosixPath(member.name)
                if member.name == "manifest.json":
                    manifest = json.load(tar.extractfile(member))
                    if manifest.get("format") != FORMAT or int(manifest.get("version", 0)) > VERSION:
                        raise RuntimeError("This isn't a Vitrine full backup (or it's from a newer version).")
                    update(step="Copying the files…", manifest=manifest)
                    continue
                if manifest is None:
                    raise RuntimeError("This isn't a Vitrine full backup: manifest.json is missing.")
                if not member.isfile() or name.is_absolute() or ".." in name.parts:
                    continue
                if member.name in ("db.dump", "db.sqlite3"):
                    db_file = work / member.name
                    with tar.extractfile(member) as src, open(db_file, "wb") as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
                elif name.parts[0] == "keys" and len(name.parts) == 2:
                    key_data[name.parts[1]] = tar.extractfile(member).read()
                elif name.parts[0] == "media" and len(name.parts) > 1:
                    target = media.joinpath(*name.parts[1:])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tmp = target.with_name(target.name + ".importing")
                    with tar.extractfile(member) as src, open(tmp, "wb") as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
                    os.replace(tmp, target)
                    files += 1
        if manifest is None or db_file is None:
            raise RuntimeError("The archive is incomplete (no database in it). Nothing was changed in the database.")

        update(step="Restoring the database…", files=files)
        restore_database(db_file)

        key_path = Path(settings.VITRINE_KEY_PATH)
        if key_path.name in key_data:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_bytes(key_data[key_path.name])
            key_path.chmod(0o600)
        update(state="done", step="Done", finished=timezone.now().isoformat(), done_bytes=total or status.get("done_bytes"))
    except Exception as exc:
        log.exception("import failed")
        update(state="failed", step="Failed", error=str(exc)[-2000:], finished=timezone.now().isoformat())
    finally:
        if raw is not None:
            try:
                raw.close()
            except Exception:
                pass
        if db_file is not None:
            db_file.unlink(missing_ok=True)
        if path:
            Path(path).unlink(missing_ok=True)


def restore_database(dump):
    db = settings.DATABASES["default"]
    if db["ENGINE"].endswith("postgresql"):
        env = {**os.environ, "PGPASSWORD": db.get("PASSWORD") or ""}
        cmd = ["pg_restore", "-h", str(db["HOST"]), "-p", str(db.get("PORT") or 5432), "-U", db["USER"], "-d", db["NAME"],
               "--clean", "--if-exists", "--no-owner", "--no-privileges", "--single-transaction", "--exit-on-error", str(dump)]
        connection.close()
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=3600)
        if result.returncode != 0:
            raise RuntimeError("Restoring the database failed (nothing was changed in it): "
                               + (result.stderr or result.stdout).strip()[-1500:])
    else:
        if dump.suffix != ".sqlite3":
            raise RuntimeError("This server uses SQLite but the backup has a PostgreSQL database.")
        import sqlite3

        connection.ensure_connection()
        source = sqlite3.connect(dump)
        try:
            source.backup(connection.connection)
        finally:
            source.close()
