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


def api(method, token=None, **params):
    token = token or config.telegram_token()
    if not token:
        raise TelegramError("No bot token: add it under Settings.")
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(
        {k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in params.items()}
    ).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15) as response:
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
    """Read the bot's new messages and link every chat that sent a pending code.

    Returns {code: {"chat_id", "title"}} for the codes linked now.
    """
    from .models import Organization

    pending = set(cache.get("vitrine:tg:pending") or [])
    if not pending:
        return {}
    updates = api("getUpdates", timeout=0, allowed_updates=["message"])
    linked = {}
    last = None
    for update in updates:
        last = update.get("update_id", last)
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
    return linked


def link_status(code):
    """None (still waiting), or {"chat_id", "title"} once the chat is linked."""
    done = cache.get("vitrine:tg:done:" + code)
    if done:
        return done
    if not cache.get(_code_key(code)):
        return {"expired": True}
    return check_links().get(code)
