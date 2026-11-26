"""Telegram bot: check the token, and link an organization's chat without typing ids.

Linking: /manage/organization/ shows a link t.me/<bot>?start=<code> (private chat) or
?startgroup=<code> (a group). Opening it sends "/start <code>" to the bot. The server
then reads the bot's updates (getUpdates, no webhook needed), finds the code, and saves
that chat for the organization the code was made for. Codes live 15 minutes in the cache.
"""
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

from django.core.cache import cache

from . import config

CODE_TTL = 15 * 60
START_RE = re.compile(r"^/start(?:@\w+)?\s+([A-Za-z0-9_-]{6,64})\s*$")


class TelegramError(Exception):
    def __init__(self, message, parameters=None):
        super().__init__(message)
        self.parameters = parameters or {}


def api(method, token=None, files=None, **params):
    """Call the Bot API. files={"photo": (filename, bytes)} sends multipart (a photo upload)."""
    token = token or config.telegram_token()
    if not token:
        raise TelegramError("No bot token: add it under Settings.")
    url = f"https://api.telegram.org/bot{token}/{method}"
    fields = {k: json.dumps(v) if isinstance(v, (list, dict)) else str(v) for k, v in params.items()}
    if files:
        data, content_type = _multipart(fields, files)
        request = urllib.request.Request(url, data=data, headers={"Content-Type": content_type})
    else:
        request = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode())
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b"{}")
        except ValueError:
            raise TelegramError(f"Telegram answered {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise TelegramError(f"Can't reach Telegram: {exc.reason}") from exc
    if not body.get("ok"):
        raise TelegramError(body.get("description", "Telegram refused the request"), body.get("parameters"))
    return body.get("result")


def _multipart(fields, files):
    boundary = "vitrine" + secrets.token_hex(12)
    out = []
    for name, value in fields.items():
        out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, (filename, content) in files.items():
        out.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n".encode() + content + b"\r\n"
        )
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


def bot_info(token=None, fresh=False):
    """{'username', 'name'} of the bot, cached for 10 minutes. Raises TelegramError."""
    token = token or config.telegram_token()
    key = "vitrine:tg:me:" + token[-12:]
    info = None if fresh else cache.get(key)
    if info is None:
        try:
            me = api("getMe", token=token)
        except TelegramError as exc:
            cache.set(key, {"error": str(exc)}, 60)  # don't make every page wait on a down Telegram
            raise
        info = {"username": me.get("username", ""), "name": me.get("first_name", "")}
        cache.set(key, info, 600)
    if "error" in info:
        raise TelegramError(info["error"])
    return info


def send(chat_id, text):
    """Send a message; returns the chat id actually used (groups move when they become supergroups)."""
    try:
        api("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview="true")
        return str(chat_id)
    except TelegramError as exc:
        new_id = exc.parameters.get("migrate_to_chat_id")
        if not new_id:
            raise
        api("sendMessage", chat_id=new_id, text=text, disable_web_page_preview="true")
        return str(new_id)


def new_link_code(org):
    code = secrets.token_urlsafe(9).replace("-", "x").replace("_", "y")
    cache.set(_code_key(code), org.pk, CODE_TTL)
    pending = [c for c in (cache.get("vitrine:tg:pending") or []) if cache.get(_code_key(c))]
    cache.set("vitrine:tg:pending", (pending + [code])[-50:], CODE_TTL)
    return code


def _code_key(code):
    return "vitrine:tg:code:" + code


def _chat_title(chat):
    if chat.get("title"):
        return chat["title"]
    name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
    if chat.get("username"):
        name = f"{name} (@{chat['username']})" if name else "@" + chat["username"]
    return name or str(chat.get("id"))


def check_links():
    """Read the bot's new messages and button presses (see process_updates); returns the codes linked now."""
    return process_updates().get("linked", {})


def process_updates():
    """Handle everything the bot received since last time, once (a lock keeps two processes apart):
    - "/start <code>" from a chat that is being linked (Organization → Connect Telegram);
    - presses on the Approve / Reject buttons under upload alerts.
    """
    if not cache.add("vitrine:tg:lock", 1, 30):
        return {}
    try:
        return _process_updates()
    finally:
        cache.delete("vitrine:tg:lock")


def _process_updates():
    from .models import Organization

    pending = set(cache.get("vitrine:tg:pending") or [])
    updates = api("getUpdates", timeout=0, allowed_updates=["message", "callback_query"])
    linked = {}
    last = None
    for update in updates:
        last = update.get("update_id", last)
        if update.get("callback_query"):
            try:
                handle_button(update["callback_query"])
            except Exception:  # one bad press must not block the others
                import logging

                logging.getLogger(__name__).exception("telegram button")
            continue
        message = update.get("message") or {}
        match = START_RE.match(message.get("text") or "")
        if not match or match.group(1) not in pending:
            continue
        code = match.group(1)
        org_id = cache.get(_code_key(code))
        org = Organization.objects.filter(pk=org_id).first() if org_id else None
        if org is None:
            continue
        chat = message.get("chat") or {}
        title = _chat_title(chat)
        try:
            chat_id = send(chat["id"], f"✅ Vitrine alerts for {org.name} will arrive in this chat.")
        except TelegramError:
            chat_id = str(chat["id"])
        org.notify_telegram_chat = chat_id
        org.notify_telegram_title = title[:120]
        org.save(update_fields=["notify_telegram_chat", "notify_telegram_title"])
        cache.delete(_code_key(code))
        cache.set("vitrine:tg:done:" + code, {"chat_id": chat_id, "title": title}, CODE_TTL)
        linked[code] = {"chat_id": chat_id, "title": title}
    if last is not None:
        api("getUpdates", offset=last + 1, timeout=0)  # mark them read
    return {"linked": linked}


# ---------- uploads with Approve / Reject buttons ----------

def approval_buttons(asset):
    from . import alerts

    return {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"a:{asset.pk}"},
        {"text": "❌ Reject", "callback_data": f"r:{asset.pk}"},
        {"text": "Open", "url": alerts.manage_url(f"/manage/asset/{asset.pk}/")},
    ]]}


def upload_caption(asset):
    parts = [f"📥 {asset.owner_name}: {asset.original_name}"]
    details = [asset.get_kind_display() if hasattr(asset, "get_kind_display") else asset.kind]
    if asset.duration_ms:
        seconds = asset.duration_ms // 1000
        details.append(f"{seconds // 60}:{seconds % 60:02d}")
    if asset.service_id:
        details.append(f"for {asset.service.name}")
    parts.append(" · ".join(str(d) for d in details if d))
    parts.append("Waiting for approval.")
    return "\n".join(parts)[:1000]


def send_upload(chat_id, asset):
    """One upload as a photo (or text) with Approve / Reject / Open buttons."""
    markup = approval_buttons(asset)
    caption = upload_caption(asset)
    thumb = None
    try:
        if asset.thumb:
            with asset.thumb.open("rb") as f:
                thumb = f.read()
    except OSError:
        thumb = None
    if thumb:
        return api("sendPhoto", chat_id=chat_id, caption=caption, reply_markup=markup,
                   files={"photo": ("thumb.jpg", thumb)})
    return api("sendMessage", chat_id=chat_id, text=caption, reply_markup=markup)


def send_control_request(chat_id, guest, text):
    """A guest asks for control of the screen: Allow / Refuse buttons (answered on the vMix PC)."""
    set_id = guest.get_set().pk
    markup = {"inline_keyboard": [[
        {"text": "✅ Allow", "callback_data": f"ca:{set_id}"},
        {"text": "❌ Refuse", "callback_data": f"cr:{set_id}"},
    ]]}
    return api("sendMessage", chat_id=chat_id, text=text[:1000], reply_markup=markup)


def handle_control_button(press, action, set_id, chat_id, who, message):
    from . import relay
    from .models import MediaSet

    media_set = MediaSet.objects.select_related("service__location__organization", "service__location__agent").filter(
        pk=int(set_id) if set_id.isdigit() else 0
    ).first()
    location = media_set.service.location if media_set and media_set.service_id else None
    if location is None or location.organization is None:
        api("answerCallbackQuery", callback_query_id=press["id"], text="That request doesn't exist any more.")
        return
    if str(location.organization.notify_telegram_chat) != chat_id:
        api("answerCallbackQuery", callback_query_id=press["id"], text="Not allowed from this chat.", show_alert="true")
        return
    allow = action == "ca"
    result = {"ok": False, "error": "This location has no vMix PC."}
    if location.agent_id:
        result = relay.send_command(location.agent, {"action": "desk_control", "set_id": media_set.pk, "on": allow,
                                                     "by": f"{who} in Telegram"})
    if result.get("ok"):
        outcome = f"✅ Allowed by {who}" if allow else f"❌ Refused by {who}"
        done = True
    else:
        error = result.get("error") or "the vMix PC didn't answer"
        if "unknown" in error.lower() or "no control" in error.lower():
            error = "update the Vitrine agent on the vMix PC to answer from Telegram"
        outcome = f"⚠️ Couldn't {'allow' if allow else 'refuse'}: {error}"
        done = "already has control" in error
    api("answerCallbackQuery", callback_query_id=press["id"], text=outcome[:190])
    if not done and not result.get("ok"):
        return  # keep the buttons: they can try again
    text = (message.get("text") or "").rstrip()
    try:
        api("editMessageText", chat_id=chat_id, message_id=message.get("message_id"),
            reply_markup={"inline_keyboard": []}, text=f"{text}\n{outcome}"[:1000])
    except TelegramError:
        pass


def handle_button(press):
    """A press on Approve / Reject (uploads) or Allow / Refuse (control requests).
    Only counts from the organization's own linked chat."""
    from .models import MediaAsset

    data = press.get("data") or ""
    message = press.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    who = (press.get("from") or {}).get("first_name") or "someone"
    action, _, pk = data.partition(":")
    if action in ("ca", "cr"):
        handle_control_button(press, action, pk, chat_id, who, message)
        return
    asset = MediaAsset.all_objects.filter(pk=int(pk) if pk.isdigit() else 0).select_related("organization").first()
    if action not in ("a", "r") or asset is None or asset.organization is None:
        api("answerCallbackQuery", callback_query_id=press["id"], text="This file doesn't exist any more.")
        return
    if str(asset.organization.notify_telegram_chat) != chat_id:
        api("answerCallbackQuery", callback_query_id=press["id"], text="Not allowed from this chat.", show_alert="true")
        return
    if asset.deleted_at:
        result = "🗑 It was deleted meanwhile."
    else:
        asset.approval = MediaAsset.APPROVAL_APPROVED if action == "a" else MediaAsset.APPROVAL_REJECTED
        asset.save(update_fields=["approval"])
        result = f"✅ Approved by {who}" if action == "a" else f"❌ Rejected by {who}"
    api("answerCallbackQuery", callback_query_id=press["id"], text=result)
    caption = (message.get("caption") or message.get("text") or "").replace("Waiting for approval.", "").rstrip()
    edit = "editMessageCaption" if message.get("photo") else "editMessageText"
    field = "caption" if message.get("photo") else "text"
    try:
        api(edit, chat_id=chat_id, message_id=message.get("message_id"), reply_markup={"inline_keyboard": []},
            **{field: f"{caption}\n{result}"[:1000]})
    except TelegramError:
        pass


try:
    from celery import shared_task
except ImportError:  # pragma: no cover
    shared_task = None

if shared_task:
    @shared_task
    def poll_updates():
        """Every few seconds (scheduler): pick up button presses without a public webhook."""
        if not config.telegram_token():
            return
        try:
            process_updates()
        except TelegramError:
            pass


def link_status(code):
    """None (still waiting), or {"chat_id", "title"} once the chat is linked."""
    done = cache.get("vitrine:tg:done:" + code)
    if done:
        return done
    if not cache.get(_code_key(code)):
        return {"expired": True}
    return check_links().get(code)
