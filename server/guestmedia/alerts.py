"""Tell staff when something needs them: Telegram and/or email, per organization.

- Uploads waiting for approval (grouped, one message per organization).
- A vMix PC goes offline (and comes back) while its location has an event today or tomorrow.
- The day before an event: are all its files on the vMix PC?
- A backup failed.
"""
import json
import logging
import re
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.mail import send_mail

log = logging.getLogger(__name__)


def emails_of(org):
    return [e for e in re.split(r"[\s,;]+", org.notify_emails or "") if "@" in e]


def can_send(org):
    telegram = bool(settings.VITRINE_TELEGRAM_BOT_TOKEN and org.notify_telegram_chat)
    email = bool(settings.EMAIL_HOST and emails_of(org))
    return telegram or email


def send_telegram(chat_id, text):
    url = f"https://api.telegram.org/bot{settings.VITRINE_TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15) as response:
        body = json.loads(response.read() or b"{}")
    if not body.get("ok"):
        raise RuntimeError(body.get("description", "Telegram refused the message"))


def notify(org, subject, text):
    """Send to the organization's Telegram chat and email addresses. Returns a list of errors."""
    errors = []
    if settings.VITRINE_TELEGRAM_BOT_TOKEN and org.notify_telegram_chat:
        try:
            send_telegram(org.notify_telegram_chat, f"{subject}\n\n{text}")
        except Exception as exc:  # network, wrong chat id, …
            log.warning("telegram alert for %s failed: %s", org, exc)
            errors.append(f"Telegram: {exc}")
    recipients = emails_of(org)
    if settings.EMAIL_HOST and recipients:
        try:
            send_mail(f"[Vitrine] {subject}", text, settings.DEFAULT_FROM_EMAIL, recipients)
        except Exception as exc:
            log.warning("email alert for %s failed: %s", org, exc)
            errors.append(f"Email: {exc}")
    return errors


def manage_url(path):
    base = settings.VITRINE_PUBLIC_URL
    return f"{base}{path}" if base else path
