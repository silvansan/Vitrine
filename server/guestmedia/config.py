"""Effective server settings: what staff set in /manage/settings/, else the stack environment."""
import json
import os
import subprocess

from django.conf import settings as django_settings

from .models import SiteSettings


def site():
    return SiteSettings.load()


# ---------- alerts ----------


def telegram_token():
    return site().telegram_bot_token or django_settings.VITRINE_TELEGRAM_BOT_TOKEN


def email_config():
    """dict for django.core.mail.get_connection, or None when email isn't set up."""
    s = site()
    if s.email_host:
        security = s.email_security or "starttls"
        return {
            "host": s.email_host,
            "port": s.email_port or (465 if security == "ssl" else 587),
            "username": s.email_user,
            "password": s.email_password,
            "use_tls": security == "starttls",
            "use_ssl": security == "ssl",
            "from": s.email_from or s.email_user or "vitrine@localhost",
        }
    if django_settings.EMAIL_HOST:
        return {
            "host": django_settings.EMAIL_HOST,
            "port": django_settings.EMAIL_PORT,
            "username": django_settings.EMAIL_HOST_USER,
            "password": django_settings.EMAIL_HOST_PASSWORD,
            "use_tls": django_settings.EMAIL_USE_TLS,
            "use_ssl": django_settings.EMAIL_USE_SSL,
            "from": django_settings.DEFAULT_FROM_EMAIL,
        }
    return None


def offline_alert_minutes():
    return site().offline_alert_minutes or django_settings.VITRINE_OFFLINE_ALERT_MINUTES


def sync_check_time():
    return site().sync_check_time or django_settings.VITRINE_SYNC_CHECK_TIME


def parse_time(value, default=(3, 30)):
    try:
        hour, minute = (int(x) for x in str(value).split(":", 1))
        if 0 <= hour < 24 and 0 <= minute < 60:
            return hour, minute
    except (ValueError, AttributeError):
        pass
    return default


# ---------- backups ----------

# Fields shown in /manage/settings/ for each kind; "secret": stored, never shown again;
# "obscure": rclone wants it obscured in its configuration.
BACKUP_FIELDS = {
    "folder": [],
    "s3": [
        ("provider", "Provider", "", {"choices": ["Other", "AWS", "Minio", "Wasabi", "Cloudflare", "DigitalOcean", "Scaleway", "IDrive"]}),
        ("endpoint", "Endpoint", "e.g. https://fsn1.your-objectstorage.com (empty for AWS)", {}),
        ("region", "Region", "optional, e.g. eu-central-1", {}),
        ("access_key_id", "Access key", "", {}),
        ("secret_access_key", "Secret key", "", {"secret": True}),
    ],
    "azure": [
        ("account", "Storage account name", "", {}),
        ("key", "Account key", "", {"secret": True}),
    ],
    "gdrive": [
        ("token", "Token from rclone authorize", 'Run  rclone authorize "drive"  on a computer with a browser and paste the {...} it prints', {"secret": True, "textarea": True}),
        ("service_account_credentials", "Or: service account JSON", "For a shared drive / Workspace: the key file of a service account", {"secret": True, "textarea": True}),
        ("root_folder_id", "Folder id (optional)", "The id at the end of the folder's Drive address; empty = My Drive", {}),
        ("team_drive", "Shared drive id (optional)", "", {}),
    ],
    "opendrive": [
        ("username", "OpenDrive username (email)", "", {}),
        ("password", "Password", "", {"secret": True, "obscure": True}),
    ],
    "ftp": [
        ("host", "Host", "", {}),
        ("port", "Port", "21", {}),
        ("user", "User", "", {}),
        ("pass", "Password", "", {"secret": True, "obscure": True}),
        ("explicit_tls", "Use TLS (FTPS)", "", {"checkbox": True}),
    ],
    "sftp": [
        ("host", "Host", "", {}),
        ("port", "Port", "22", {}),
        ("user", "User", "", {}),
        ("pass", "Password", "", {"secret": True, "obscure": True}),
    ],
    "smb": [
        ("host", "Host / IP", "e.g. 192.168.1.20", {}),
        ("user", "User", "", {}),
        ("pass", "Password", "", {"secret": True, "obscure": True}),
        ("domain", "Domain (optional)", "WORKGROUP", {}),
    ],
    "custom": [
        ("config", "rclone configuration", "[mydrive]\ntype = …\n… (as in rclone.conf; the remote name goes in the path, e.g. mydrive:vitrine)", {"secret": True, "textarea": True}),
    ],
}

RCLONE_TYPE = {"s3": "s3", "azure": "azureblob", "gdrive": "drive", "opendrive": "opendrive", "ftp": "ftp", "sftp": "sftp", "smb": "smb"}

PATH_HINT = {
    "folder": "Absolute path inside the scheduler container, e.g. /backup (mount it in the compose file)",
    "s3": "bucket/folder, e.g. vitrine-backup or my-bucket/vitrine",
    "azure": "container/folder, e.g. backups/vitrine",
    "gdrive": "Folder in the Drive, e.g. Vitrine backups",
    "opendrive": "Folder, e.g. Vitrine backups",
    "ftp": "Folder on the server, e.g. backups/vitrine",
    "sftp": "Folder on the server, e.g. backups/vitrine",
    "smb": "share/folder, e.g. backups/vitrine",
    "custom": "remote:folder, e.g. mydrive:vitrine",
}


def obscure(value):
    result = subprocess.run(["rclone", "obscure", value], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError("rclone obscure failed: " + result.stderr.strip())
    return result.stdout.strip()


def backup_plan():
    """None when backups are off, else {"target", "config" (rclone.conf text or None), "label"}."""
    s = site()
    kind = s.backup_kind
    if not kind:
        target = django_settings.VITRINE_BACKUP_TARGET
        return {"target": target, "config": None, "label": f"{target} (stack environment)"} if target else None
    if kind == "off":
        return None
    path = s.backup_path.strip().strip("/") if kind != "folder" else s.backup_path.strip().rstrip("/")
    opts = s.backup_options or {}
    if kind == "folder":
        return {"target": path, "config": None, "label": path} if path else None
    if kind == "custom":
        return {"target": s.backup_path.strip(), "config": opts.get("config", ""), "label": s.backup_path.strip()}
    lines = ["[vitrine]", f"type = {RCLONE_TYPE[kind]}"]
    for name, _label, _hint, extra in BACKUP_FIELDS[kind]:
        value = opts.get(name)
        if value in (None, "", False):
            continue
        if extra.get("checkbox"):
            value = "true"
        elif extra.get("obscure"):
            value = obscure(str(value))
        elif extra.get("textarea"):
            # rclone wants JSON on one line.
            try:
                value = json.dumps(json.loads(value), separators=(",", ":"))
            except (ValueError, TypeError):
                value = " ".join(str(value).split())
        lines.append(f"{name} = {value}")
    if kind == "s3":
        lines.append("acl = private")
        if not opts.get("provider"):
            lines.append("provider = Other")
    if kind == "gdrive":
        lines.append("scope = drive")
    return {"target": f"vitrine:{path}", "config": "\n".join(lines) + "\n", "label": f"{dict(SiteSettings.BACKUP_KINDS)[kind]}: {path}"}


def backup_time():
    return site().backup_time or django_settings.VITRINE_BACKUP_TIME


def backup_keep_days():
    return site().backup_keep_days or django_settings.VITRINE_BACKUP_KEEP_DAYS


def env_backup_target():
    return django_settings.VITRINE_BACKUP_TARGET


def rclone_env_config_path():
    return os.environ.get("RCLONE_CONFIG", "/data/rclone.conf")
