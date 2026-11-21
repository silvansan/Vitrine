"""Django settings for the Vitrine server.

Everything that differs between environments comes from environment variables,
see deploy/.env.example. Without POSTGRES_HOST / REDIS_URL the server falls back
to SQLite, in-memory channels and eager Celery, which is what the tests use.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Markdown knowledge base shown at /docs/ (the image copies the repo's docs/ here).
VITRINE_DOCS_DIR = os.environ.get("VITRINE_DOCS_DIR", "")


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "*")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")
# Behind Nginx Proxy Manager: trust its forwarded scheme and host.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# Public address guests use, e.g. https://vitrine.yourdomain.org (no trailing slash).
# Leave it empty to build guest links from the address you opened /manage/ with.
VITRINE_PUBLIC_URL = os.environ.get("VITRINE_PUBLIC_URL", "").strip().rstrip("/")
if "example.org" in VITRINE_PUBLIC_URL or "example.com" in VITRINE_PUBLIC_URL:
    # The value from .env.example was never changed: ignore it.
    VITRINE_PUBLIC_URL = ""
# Where the Ed25519 key used to sign tablet pairing codes is kept.
VITRINE_KEY_PATH = Path(os.environ.get("VITRINE_KEY_PATH", BASE_DIR / "data" / "keys" / "pairing_ed25519.pem"))
# Days a deleted file stays in the trash before it is removed for good.
VITRINE_TRASH_DAYS = int(os.environ.get("VITRINE_TRASH_DAYS", "30"))
# An agent receives the active events of its location from yesterday up to this
# many days ahead, and downloads their files before the day.
VITRINE_PRESYNC_DAYS = int(os.environ.get("VITRINE_PRESYNC_DAYS", "14"))
# Frame rate of the vMix project; videos are converted to this constant frame rate.
VITRINE_VIDEO_FPS = int(os.environ.get("VITRINE_VIDEO_FPS", "25"))
# Seconds between filmstrip frames used by the cue panel.
VITRINE_FILMSTRIP_INTERVAL = int(os.environ.get("VITRINE_FILMSTRIP_INTERVAL", "5"))
# Largest single upload, in megabytes.
VITRINE_MAX_UPLOAD_MB = int(os.environ.get("VITRINE_MAX_UPLOAD_MB", "4096"))
# When true, nginx serves protected files through X-Accel-Redirect (Docker setup).
VITRINE_X_ACCEL = env_bool("VITRINE_X_ACCEL", False)

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "guestmedia",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "vitrine_server.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Overrides for the Django admin look (templates/admin/).
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = None
ASGI_APPLICATION = "vitrine_server.asgi.application"

if os.environ.get("POSTGRES_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": os.environ["POSTGRES_HOST"],
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "NAME": os.environ.get("POSTGRES_DB", "vitrine"),
            "USER": os.environ.get("POSTGRES_USER", "vitrine"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "data" / "db.sqlite3",
        }
    }

REDIS_URL = os.environ.get("REDIS_URL", "")
if REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [REDIS_URL]},
        }
    }
    # Live state the agents report (for guests controlling from their phone).
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}}
    CELERY_BROKER_URL = REDIS_URL
    CELERY_RESULT_BACKEND = None
else:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_BROKER_URL = "memory://"

# Video conversion runs on the "celery" queue (worker); alerts and backups on "light"
# (scheduler service), so a long conversion never delays them.
CELERY_TASK_ROUTES = {
    "guestmedia.tasks.check_*": {"queue": "light"},
    "guestmedia.tasks.purge_trash": {"queue": "light"},
    "guestmedia.backup.*": {"queue": "light"},
}
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TZ", "Europe/Zurich")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", BASE_DIR / "data" / "static"))
MEDIA_URL = "/protected/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "data" / "media"))
# Incoming resumable uploads are assembled here before processing.
UPLOAD_TMP_ROOT = Path(os.environ.get("UPLOAD_TMP_ROOT", MEDIA_ROOT / "incoming"))

DATA_UPLOAD_MAX_MEMORY_SIZE = 64 * 1024 * 1024

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/manage/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}

# ---------- alerts to staff (guestmedia/alerts.py) ----------
# Telegram: create a bot with @BotFather; each organization sets its chat id on /manage/organization/.
VITRINE_TELEGRAM_BOT_TOKEN = os.environ.get("VITRINE_TELEGRAM_BOT_TOKEN", "")
# Email (any SMTP server). Leave EMAIL_HOST empty to not send email.
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "true").lower() in ("1", "true", "yes")
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "false").lower() in ("1", "true", "yes")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "vitrine@localhost")
EMAIL_TIMEOUT = 15
# A vMix PC counts as offline after this many minutes without a heartbeat.
VITRINE_OFFLINE_ALERT_MINUTES = int(os.environ.get("VITRINE_OFFLINE_ALERT_MINUTES", "10"))
# When to check that tomorrow's files are on each vMix PC (HH:MM, local time).
VITRINE_SYNC_CHECK_TIME = os.environ.get("VITRINE_SYNC_CHECK_TIME", "18:00")

# ---------- backups (guestmedia/backup.py) ----------
# Where to copy the database dump and the media: anything rclone understands, e.g.
#   /backup                 a folder mounted into the container (NAS over SMB/NFS, USB disk)
#   s3:my-bucket/vitrine     S3-compatible (Hetzner, AWS, MinIO, Wasabi, Backblaze B2 …)
#   azure:container/vitrine  Azure Blob Storage
#   ftp:backups/vitrine, sftp:backups/vitrine, nas:share/vitrine (SMB)
# Remotes are configured with RCLONE_CONFIG_<NAME>_<OPTION> variables (see docs/server.md).
# Empty: no backups.
VITRINE_BACKUP_TARGET = os.environ.get("VITRINE_BACKUP_TARGET", "").rstrip("/")
VITRINE_BACKUP_TIME = os.environ.get("VITRINE_BACKUP_TIME", "03:30")
VITRINE_BACKUP_KEEP_DAYS = int(os.environ.get("VITRINE_BACKUP_KEEP_DAYS", "14"))
VITRINE_BACKUP_DIR = Path(os.environ.get("VITRINE_BACKUP_DIR", MEDIA_ROOT.parent / "backup-tmp"))
