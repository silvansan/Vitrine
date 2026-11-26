"""Tell staff when something needs them: Telegram and/or email, per organization.

- Uploads waiting for approval (grouped, one message per organization).
- A vMix PC goes offline (and comes back) while its location has an event today or tomorrow.
- The day before an event: are all its files on the vMix PC?
- A backup failed.
"""
import logging
import re

from django.conf import settings
from django.core.mail import EmailMessage, get_connection

from . import config

log = logging.getLogger(__name__)


def emails_of(org):
    return [e for e in re.split(r"[\s,;]+", org.notify_emails or "") if "@" in e]


def can_send(org):
    telegram = bool(config.telegram_token() and org.notify_telegram_chat)
    email = bool(config.email_config() and emails_of(org))
    return telegram or email


def send_telegram(chat_id, text):
    """Returns the chat id used (it changes when a group becomes a supergroup)."""
    from . import telegram

    return telegram.send(chat_id, text)


def notify(org, subject, text, telegram=True):
    """Send to the organization's Telegram chat and email addresses. Returns a list of errors.
    telegram=False: email only (the caller already sent its own Telegram messages)."""
    errors = []
    if telegram and config.telegram_token() and org.notify_telegram_chat:
        try:
            used = send_telegram(org.notify_telegram_chat, f"{subject}\n\n{text}")
            if used and used != str(org.notify_telegram_chat):
                org.notify_telegram_chat = used
                org.save(update_fields=["notify_telegram_chat"])
        except Exception as exc:  # network, wrong chat id, …
            log.warning("telegram alert for %s failed: %s", org, exc)
            errors.append(f"Telegram: {exc}")
    recipients = emails_of(org)
    if config.email_config() and recipients:
        try:
            send_email(recipients, f"[Vitrine] {subject}", text)
        except Exception as exc:
            log.warning("email alert for %s failed: %s", org, exc)
            errors.append(f"Email: {exc}")
    return errors


def send_email(recipients, subject, text):
    cfg = config.email_config()
    if settings.EMAIL_BACKEND.endswith("locmem.EmailBackend"):  # tests
        connection = get_connection()
    else:
        connection = get_connection(
            "django.core.mail.backends.smtp.EmailBackend", host=cfg["host"], port=cfg["port"],
            username=cfg["username"], password=cfg["password"], use_tls=cfg["use_tls"], use_ssl=cfg["use_ssl"],
            timeout=15,
        )
    EmailMessage(subject, text, cfg["from"], recipients, connection=connection).send()


def manage_url(path):
    base = settings.VITRINE_PUBLIC_URL
    return f"{base}{path}" if base else path


# ---------- one-off alerts (each can be switched off per organization) ----------

def _safe_notify(org, subject, text):
    """Alerts must never break the action that caused them."""
    try:
        if org is not None and can_send(org):
            notify(org, subject, text)
    except Exception:
        log.exception("alert %r for %s failed", subject, org)


def control_requested(person, event):
    """A guest asked for control of the screen from their own tablet view (at most every 10 minutes)."""
    from django.core.cache import cache

    org = event.location.organization if event.location_id else person.organization
    if not (org and org.notify_control):
        return
    if not cache.add(f"vitrine:alert:control:{person.pk}:{event.pk}", 1, 600):
        return
    where = event.location.name if event.location_id else "the screen"
    subject = f"{person.name} asks to control {where}"
    text = f"{person.name} wants to project from their own tablet ({event.name}, {event.when})."
    # In Telegram with Allow / Refuse buttons; by email (or if that fails) as text.
    sent = False
    guest = event.guests.filter(person=person).first()
    if guest is not None and event.location_id and event.location.agent_id:
        from . import config, telegram

        if config.telegram_token() and org.notify_telegram_chat:
            try:
                telegram.send_control_request(org.notify_telegram_chat, guest, subject + "\n" + text)
                sent = True
            except Exception:
                log.exception("telegram control request for %s failed", org)
    try:
        if can_send(org):
            notify(org, subject, text + "\nAccept or refuse it on the back tablet.", telegram=not sent)
    except Exception:
        log.exception("alert %r for %s failed", subject, org)


def conversion_failed(asset):
    org = asset.organization
    if not (org and org.notify_failed):
        return
    last = (asset.error or "").strip().splitlines()[-1:] or ["unknown error"]
    _safe_notify(
        org,
        f"Couldn't convert {asset.original_name}",
        f"The file from {asset.owner_name} couldn't be made ready for vMix.\n"
        f"Error: {last[0][:300]}\n\n"
        f"Open it: {manage_url(f'/manage/asset/{asset.pk}/')}\n"
        "To try again: Library → select it → Process again. Or ask them for another copy.",
    )


def first_upload(asset):
    """The first file someone ever uploaded (useful to check that a new link works)."""
    from django.core.cache import cache

    from .models import MediaAsset

    person, org = asset.uploaded_by, asset.organization
    if person is None or not (org and org.notify_first_upload):
        return
    if MediaAsset.all_objects.filter(uploaded_by=person).exclude(pk=asset.pk).exists():
        return
    if not cache.add(f"vitrine:alert:first:{person.pk}", 1, 3600):  # several files at once
        return
    event = f" for {asset.service.name}" if asset.service_id else ""
    _safe_notify(
        org,
        f"First upload from {person.name}",
        f"{person.name} just uploaded for the first time{event}: {asset.original_name}.\n"
        f"Their files: {manage_url(f'/manage/people/{person.pk}/')}",
    )
