"""Nightly backup: database dump + media + pairing key, copied with rclone.

Layout at VITRINE_BACKUP_TARGET:
    db/vitrine-YYYYMMDD-HHMM.dump   PostgreSQL custom-format dump (pg_restore), kept KEEP_DAYS
                                    (vitrine-….sqlite3 when the server runs on SQLite)
    media/                          mirror of /data/media (uploads, converted files, thumbnails)
    media-removed/YYYY-MM-DD/       files that left media/ that night, kept 30 days
    keys/                           the pairing key (tablets' QR codes are signed with it)
"""
import logging
import os
import subprocess
from datetime import datetime

from celery import shared_task
from django.conf import settings
from django.db import connection
from django.utils import timezone

from .models import BackupRun

log = logging.getLogger(__name__)
REMOVED_KEEP_DAYS = 30


def enabled():
    return bool(settings.VITRINE_BACKUP_TARGET)


def target(*parts):
    base = settings.VITRINE_BACKUP_TARGET
    joined = "/".join(p.strip("/") for p in parts if p)
    return f"{base}/{joined}" if joined else base


def rclone(*args, lines, ok_codes=(0,)):
    """Run rclone; append its output to lines; raise on failure.

    Remotes come from RCLONE_CONFIG_<NAME>_<OPTION> variables, or from an rclone.conf
    at RCLONE_CONFIG (default /data/rclone.conf) if that file exists.
    """
    conf = os.environ.get("RCLONE_CONFIG", "/data/rclone.conf")
    cmd = ["rclone", *(["--config", conf] if os.path.exists(conf) else []), *args]
    lines.append("$ " + " ".join(args))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=6 * 3600)
    out = (result.stdout + result.stderr).strip()
    if out:
        lines.append(out[-4000:])
    if result.returncode not in ok_codes:
        raise RuntimeError(f"rclone {args[0]} failed ({result.returncode})")


def dump_database(folder, stamp, lines):
    db = settings.DATABASES["default"]
    folder.mkdir(parents=True, exist_ok=True)
    if db["ENGINE"].endswith("postgresql"):
        path = folder / f"vitrine-{stamp}.dump"
        env = {**os.environ, "PGPASSWORD": db.get("PASSWORD") or ""}
        cmd = ["pg_dump", "-h", str(db["HOST"]), "-p", str(db.get("PORT") or 5432), "-U", db["USER"], "-d", db["NAME"],
               "-Fc", "-f", str(path)]
        lines.append("$ pg_dump … " + path.name)
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=3600)
        if result.returncode != 0:
            raise RuntimeError("pg_dump failed: " + (result.stderr or result.stdout).strip()[-1000:])
    else:
        import sqlite3

        path = folder / f"vitrine-{stamp}.sqlite3"
        connection.ensure_connection()
        target_db = sqlite3.connect(path)
        try:
            connection.connection.backup(target_db)  # consistent copy while the server runs
        finally:
            target_db.close()
        lines.append("copied SQLite database " + path.name)
    return path


@shared_task
def run_backup(reason="schedule"):
    """Back up now. Returns the BackupRun id."""
    if not enabled():
        return None
    run = BackupRun.objects.create(target=settings.VITRINE_BACKUP_TARGET)
    lines = [f"Backup ({reason}) to {settings.VITRINE_BACKUP_TARGET}"]
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    today = timezone.localdate().isoformat()
    tmp = settings.VITRINE_BACKUP_DIR
    try:
        dump = dump_database(tmp, stamp, lines)
        run.database_file = dump.name
        run.database_bytes = dump.stat().st_size
        rclone("copyto", str(dump), target("db", dump.name), lines=lines)
        dump.unlink(missing_ok=True)
        # Exit code 3 = nothing there yet (first run).
        rclone("delete", target("db"), "--min-age", f"{settings.VITRINE_BACKUP_KEEP_DAYS}d", lines=lines, ok_codes=(0, 3))

        media = settings.MEDIA_ROOT
        incoming = settings.UPLOAD_TMP_ROOT
        excludes = []
        if incoming.is_relative_to(media):
            excludes = ["--exclude", f"/{incoming.relative_to(media).as_posix()}/**"]
        rclone("sync", str(media), target("media"), "--backup-dir", target("media-removed", today),
               "--fast-list", "--transfers", "4", *excludes, lines=lines)
        rclone("delete", target("media-removed"), "--min-age", f"{REMOVED_KEEP_DAYS}d", "--rmdirs", lines=lines, ok_codes=(0, 3))

        key = settings.VITRINE_KEY_PATH
        if key.exists():
            rclone("copyto", str(key), target("keys", key.name), lines=lines)
        run.ok = True
        lines.append("Done.")
    except Exception as exc:  # report every failure to staff
        log.exception("backup failed")
        lines.append(f"FAILED: {exc}")
        run.ok = False
    finally:
        for leftover in tmp.glob(f"vitrine-{stamp}.*") if tmp.exists() else []:
            leftover.unlink(missing_ok=True)
        run.finished = timezone.now()
        run.log = "\n".join(lines)[-20000:]
        run.save()
    if not run.ok:
        notify_failure(run)
    return run.pk


def notify_failure(run):
    from . import alerts
    from .models import Organization

    for org in Organization.objects.filter(notify_backup=True):
        if alerts.can_send(org):
            tail = run.log.strip().splitlines()[-1] if run.log else ""
            alerts.notify(org, "Backup failed", f"The backup to {run.target} failed at {timezone.localtime(run.started):%a %d %b %H:%M}.\n{tail}\n\nDetails: {alerts.manage_url('/manage/backups/')}")


def parse_time(value, default=(3, 30)):
    try:
        hour, minute = (int(x) for x in value.split(":", 1))
        return hour, minute
    except (ValueError, AttributeError):
        return default


def last_success():
    return BackupRun.objects.filter(ok=True).first()


def is_stale():
    """No successful backup in the last 36 hours (while backups are on)."""
    if not enabled():
        return False
    last = last_success()
    return last is None or (timezone.now() - last.started).total_seconds() > 36 * 3600


__all__ = ["run_backup", "enabled", "last_success", "is_stale", "parse_time", "datetime"]
