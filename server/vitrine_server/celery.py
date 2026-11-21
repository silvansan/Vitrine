import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vitrine_server.settings")

app = Celery("vitrine_server")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.autodiscover_tasks(["guestmedia"], related_name="backup")


@app.on_after_finalize.connect
def setup_schedule(sender, **kwargs):
    # Times of the backup and the "ready for tomorrow?" check are settings staff can change
    # in /manage/settings/, so these run every few minutes and start the job when it is due.
    sender.conf.beat_schedule = {
        "purge-trash-daily": {"task": "guestmedia.tasks.purge_trash", "schedule": crontab(hour=3, minute=17)},
        "uploads-waiting": {"task": "guestmedia.tasks.check_pending_uploads", "schedule": 120.0},
        "vmix-pcs-offline": {"task": "guestmedia.tasks.check_agents", "schedule": 120.0},
        "files-ready-for-tomorrow": {"task": "guestmedia.tasks.check_sync_due", "schedule": 300.0},
        "backup": {"task": "guestmedia.backup.check_backup_due", "schedule": 300.0},
    }
