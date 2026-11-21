#!/bin/sh
set -e

case "$1" in
  web)
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
    if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
      python manage.py createsuperuser --noinput >/dev/null 2>&1 || true
    fi
    exec daphne -b 0.0.0.0 -p 8000 --proxy-headers vitrine_server.asgi:application
    ;;
  worker)
    # One job at a time: video conversion uses every core on its own.
    exec celery -A vitrine_server worker -Q celery --loglevel=info --concurrency=1
    ;;
  scheduler)
    # Timers (alerts, backups, trash) and the light jobs they start, apart from video conversion.
    exec celery -A vitrine_server worker --beat -Q light --loglevel=info --concurrency=2 --schedule=/tmp/celerybeat-schedule
    ;;
  *)
    exec "$@"
    ;;
esac
