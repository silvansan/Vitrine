# Moving Vitrine into Integr8

The server side was written as one Django app, `guestmedia`, so it can move into Integr8 with little change:

1. Copy `server/guestmedia/` into the Integr8 project and add `"guestmedia"` and `"channels"` to `INSTALLED_APPS`.
2. Add the settings the app reads (see `server/vitrine_server/settings.py`): `VITRINE_PUBLIC_URL`, `VITRINE_KEY_PATH`, `VITRINE_TRASH_DAYS`, `VITRINE_VIDEO_FPS`, `VITRINE_FILMSTRIP_INTERVAL`, `VITRINE_MAX_UPLOAD_MB`, `VITRINE_X_ACCEL`, `UPLOAD_TMP_ROOT`, plus `MEDIA_ROOT` / `MEDIA_URL = "/protected/"`.
3. Include `guestmedia.urls` (optionally under a prefix) and add `guestmedia.routing.websocket_urlpatterns` to Integr8's ASGI router.
4. Register the Celery tasks (`guestmedia.tasks.process_asset`, nightly `purge_trash`) with Integr8's worker; the worker image needs ffmpeg.
5. Point nginx's `/protected/` internal location at the same media volume.
6. Run `python manage.py migrate guestmedia`.

The agent only needs `server_url` changed. Pairing keys: copy `/data/keys/pairing_ed25519.pem` to keep existing tablet codes valid (paired tablets keep working regardless, their sessions live on the agent).

Natural links with Integr8 later: create Vitrine services from Integr8's service program, and show which guest media was used in the archive entry of that service.
