"""PIN protection for people's links.

A person's link alone is not enough: the first time a phone or computer opens
it, the person types their PIN. That device then gets a cookie and is
remembered until someone signs it out (on the person's page in /manage/).
Staff signed in to /manage/ for the person's organization skip the PIN.
"""
import hashlib
import re
import secrets
from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from .models import PersonDevice

COOKIE_MAX_AGE = 10 * 365 * 24 * 3600  # "until signed out"


class PinRequired(PermissionDenied):
    """This device hasn't entered the person's PIN yet."""


def cookie_name(person):
    return f"vitrine_p{person.pk}"


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def staff_can_open(request, person):
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated and user.is_staff):
        return False
    return user.is_superuser or person.organization.staff.filter(pk=user.pk).exists()


def device_for(request, person):
    raw = request.COOKIES.get(cookie_name(person), "")
    if not raw:
        return None
    return PersonDevice.objects.filter(person=person, token_hash=_hash(raw)).first()


def is_authorized(request, person):
    if staff_can_open(request, person):
        return True
    device = device_for(request, person)
    if device is None:
        return False
    if timezone.now() - device.last_seen > timedelta(minutes=10):
        PersonDevice.objects.filter(pk=device.pk).update(last_seen=timezone.now())
    return True


def require(request, person):
    if not is_authorized(request, person):
        raise PinRequired("Enter your PIN first.")


def describe_device(request):
    """A short "Chrome on Android" label from the user agent."""
    ua = request.META.get("HTTP_USER_AGENT", "")
    browser = next(
        (name for pattern, name in [
            (r"Edg/", "Edge"), (r"OPR/", "Opera"), (r"SamsungBrowser", "Samsung Internet"),
            (r"Firefox/", "Firefox"), (r"Chrome/", "Chrome"), (r"Safari/", "Safari"),
        ] if re.search(pattern, ua)),
        "Browser",
    )
    system = next(
        (name for pattern, name in [
            (r"iPhone", "iPhone"), (r"iPad", "iPad"), (r"Android", "Android"),
            (r"Windows", "Windows"), (r"Mac OS X", "Mac"), (r"Linux", "Linux"),
        ] if re.search(pattern, ua)),
        "",
    )
    return f"{browser} on {system}" if system else browser


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    return ip or None


def check_pin(request, person, pin):
    """Returns (ok, message). Locks the link for a while after too many wrong PINs."""
    if person.is_locked:
        minutes = max(1, int((person.locked_until - timezone.now()).total_seconds() // 60) + 1)
        return False, f"Too many wrong PINs. Try again in {minutes} minute{'s' if minutes != 1 else ''}, or ask the production team."
    pin = re.sub(r"\D", "", pin or "")
    if pin and person.check_pin_value(pin):
        if person.failed_pins:
            person.failed_pins = 0
            person.locked_until = None
            person.save(update_fields=["failed_pins", "locked_until"])
        return True, ""
    person.failed_pins += 1
    left = person.MAX_PIN_TRIES - person.failed_pins
    if left <= 0:
        person.locked_until = timezone.now() + timedelta(minutes=person.LOCK_MINUTES)
        person.failed_pins = 0
        person.save(update_fields=["failed_pins", "locked_until"])
        return False, f"Too many wrong PINs. The link is locked for {person.LOCK_MINUTES} minutes."
    person.save(update_fields=["failed_pins"])
    return False, f"That PIN isn't right. {left} tr{'ies' if left != 1 else 'y'} left."


MIN_LEN, MAX_LEN = 4, 6


def choose_pin(request, person, pin, again):
    """First opening: the person picks their PIN. Returns (ok, message)."""
    pin = re.sub(r"\D", "", pin or "")
    again = re.sub(r"\D", "", again or "")
    if not MIN_LEN <= len(pin) <= MAX_LEN:
        return False, f"Choose {MIN_LEN} to {MAX_LEN} digits."
    if len(set(pin)) == 1 or pin in "0123456789" or pin in "9876543210":
        return False, "That PIN is too easy to guess. Try another."
    if pin != again:
        return False, "The two PINs aren't the same."
    person.choose_pin(pin, describe_device(request))
    person.save(update_fields=["pin", "pin_hash", "pin_set_at", "pin_set_from", "failed_pins", "locked_until"])
    return True, ""


def remember_device(request, response, person):
    raw = secrets.token_urlsafe(32)
    PersonDevice.objects.create(
        person=person, token_hash=_hash(raw), label=describe_device(request)[:160], ip=client_ip(request)
    )
    response.set_cookie(
        cookie_name(person), raw, max_age=COOKIE_MAX_AGE, httponly=True, samesite="Lax",
        secure=request.is_secure(), path="/g/",
    )
    return response


def forget_device(request, response, person):
    device = device_for(request, person)
    if device:
        device.delete()
    response.delete_cookie(cookie_name(person), path="/g/")
    return response
