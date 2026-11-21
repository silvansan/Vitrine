import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vitrine_server.settings")

app = Celery("vitrine_server")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.autodiscover_tasks(["guestmedia"], related_name="backup")


def _at(value, default):
    try:
        hour, minute = (int(x) for x in value.split(":", 1))
        return crontab(hour=hour, minute=minute)
    except (ValueError, AttributeError):
        return crontab(hour=default[0], minute=default[1])


def _schedule():
    from django.conf import settings

    schedule = {
        "purge-trash-daily": {"task": "guestmedia.tasks.purge_trash", "schedule": crontab(hour=3, minute=17)},
        "uploads-waiting": {"task": "guestmedia.tasks.check_pending_uploads", "schedule": 120.0},
        "vmix-pcs-offline": {"task": "guestmedia.tasks.check_agents", "schedule": 120.0},
        "files-ready-for-tomorrow": {
            "task": "guestmedia.tasks.check_sync",
            "schedule": _at(settings.VITRINE_SYNC_CHECK_TIME, (18, 0)),
        },
    }
    if settings.VITRINE_BACKUP_TARGET:
        schedule["backup"] = {
            "task": "guestmedia.backup.run_backup",
            "schedule": _at(settings.VITRINE_BACKUP_TIME, (3, 30)),
        }
    return schedule


@app.on_after_finalize.connect
def setup_schedule(sender, **kwargs):
    sender.conf.beat_schedule = _schedule()
