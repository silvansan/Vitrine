import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vitrine_server.settings")

app = Celery("vitrine_server")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "purge-trash-daily": {
        "task": "guestmedia.tasks.purge_trash",
        "schedule": crontab(hour=3, minute=17),
    },
}
