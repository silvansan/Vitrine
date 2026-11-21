"""Nightly backup: database dump + media + pairing key, copied with rclone.

Where to: /manage/settings/ (folder, S3, Azure, Google Drive, OpenDrive, FTP, SFTP,
SMB or any rclone remote), else VITRINE_BACKUP_TARGET from the stack environment.

Layout at the target:
    db/vitrine-YYYYMMDD-HHMM.dump   PostgreSQL custom-format dump (pg_restore), kept N days
                                    (vitrine-….sqlite3 when the server runs on SQLite)
    media/                          mirror of /data/media (uploads, converted files, thumbnails)
    media-removed/YYYY-MM-DD/       files that left media/ that night, kept 30 days
    keys/                           the pairing key (tablets' QR codes are signed with it)
"""
import logging
import os
import subprocess
from contextlib import contextmanager

from celery import shared_task
from django.conf import settings
from django.db import connection
from django.utils import timezone

from . import config
from .models import BackupRun, SiteSettings

log = logging.getLogger(__name__)
REMOVED_KEEP_DAYS = 30


def enabled():
    return config.backup_plan() is not None


@contextmanager
def rclone_config(plan):
    """Path of an rclone.conf for this plan (a temporary file for remotes set in /manage/), or None."""
    if not plan.get("config"):
        env_conf = config.rclone_env_config_path()
        yield env_conf if os.path.exists(env_conf) else None
        return
    folder = settings.VITRINE_BACKUP_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rclone-{os.getpid()}.conf"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(plan["config"])
    try:
        yield str(path)
    finally:
        path.unlink(missing_ok=True)


class Runner:
    def __init__(self, conf_path, lines):
        self.conf = conf_path
        self.lines = lines

    def __call__(self, *args, ok_codes=(0,), timeout=6 * 3600):
        cmd = ["rclone", *(["--config", self.conf] if self.conf else []), *args]
        self.lines.append("$ rclone " + " ".join(args))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (result.stdout + result.stderr).strip()
        if out:
            self.lines.append(out[-4000:])
        if result.returncode not in ok_codes:
            raise RuntimeError(f"rclone {args[0]} failed ({result.returncode})")
        return result.stdout


def join(base, *parts):
    joined = "/".join(p.strip("/") for p in parts if p)
    if not joined:
        return base
    return f"{base}/{joined}" if not base.endswith(":") else f"{base}{joined}"


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


def test_connection():
    """(ok, log): create the target folder and list it."""
    plan = config.backup_plan()
    if plan is None:
        return False, "Backups are off."
    lines = [f"Testing {plan['label']}"]
    try:
        with rclone_config(plan) as conf:
            rclone = Runner(conf, lines)
            rclone("mkdir", plan["target"], timeout=120)
            rclone("lsf", plan["target"], "--max-depth", "1", timeout=120)
        lines.append("OK: the target can be reached and written to.")
        return True, "\n".join(lines)
    except Exception as exc:
        lines.append(f"FAILED: {exc}")
        return False, "\n".join(lines)


@shared_task
def run_backup(reason="schedule"):
    """Back up now. Returns the BackupRun id."""
    try:
        plan = config.backup_plan()
    except Exception as exc:
        plan = None
        BackupRun.objects.create(target="?", finished=timezone.now(), ok=False, log=f"Settings problem: {exc}")
    if plan is None:
        return None
    run = BackupRun.objects.create(target=plan["label"])
    lines = [f"Backup ({reason}) to {plan['label']}"]
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    today = timezone.localdate().isoformat()
    tmp = settings.VITRINE_BACKUP_DIR
    target = plan["target"]
    try:
        with rclone_config(plan) as conf:
            rclone = Runner(conf, lines)
            dump = dump_database(tmp, stamp, lines)
            run.database_file = dump.name
            run.database_bytes = dump.stat().st_size
            rclone("copyto", str(dump), join(target, "db", dump.name))
            dump.unlink(missing_ok=True)
            # Exit code 3 = nothing there yet (first run).
            rclone("delete", join(target, "db"), "--min-age", f"{config.backup_keep_days()}d", ok_codes=(0, 3))

            media = settings.MEDIA_ROOT
            incoming = settings.UPLOAD_TMP_ROOT
            excludes = []
            if incoming.is_relative_to(media):
                excludes = ["--exclude", f"/{incoming.relative_to(media).as_posix()}/**"]
            rclone("sync", str(media), join(target, "media"), "--backup-dir", join(target, "media-removed", today),
                   "--fast-list", "--transfers", "4", *excludes)
            rclone("delete", join(target, "media-removed"), "--min-age", f"{REMOVED_KEEP_DAYS}d", "--rmdirs", ok_codes=(0, 3))

            key = settings.VITRINE_KEY_PATH
            if key.exists():
                rclone("copyto", str(key), join(target, "keys", key.name))
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


@shared_task
def check_backup_due():
    """Every few minutes: start tonight's backup once its time has come (settings can change any time)."""
    if not enabled():
        return
    now = timezone.localtime()
    hour, minute = config.parse_time(config.backup_time(), (3, 30))
    if (now.hour, now.minute) < (hour, minute):
        return
    s = SiteSettings.load()
    if s.last_backup_day == now.date():
        return
    SiteSettings.objects.filter(pk=s.pk).update(last_backup_day=now.date())
    run_backup("schedule")


def notify_failure(run):
    from . import alerts
    from .models import Organization

    for org in Organization.objects.filter(notify_backup=True):
        if alerts.can_send(org):
            tail = run.log.strip().splitlines()[-1] if run.log else ""
            alerts.notify(org, "Backup failed", f"The backup to {run.target} failed at {timezone.localtime(run.started):%a %d %b %H:%M}.\n{tail}\n\nDetails: {alerts.manage_url('/manage/settings/')}")


def last_success():
    return BackupRun.objects.filter(ok=True).first()


def is_stale():
    """No successful backup in the last 36 hours (while backups are on)."""
    if not enabled():
        return False
    last = last_success()
    return last is None or (timezone.now() - last.started).total_seconds() > 36 * 3600
