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


def notify(org, subject, text):
    """Send to the organization's Telegram chat and email addresses. Returns a list of errors."""
    errors = []
    if config.telegram_token() and org.notify_telegram_chat:
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
