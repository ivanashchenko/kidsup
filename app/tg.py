# -*- coding: utf-8 -*-
"""Telegram: рабочие группы админов → сервер, чтение и ответ.

03.10.2026, Борис: «Как тебе сделать доступ в наши рабочие группы Телеграм?»
Путь: бот через @BotFather → токен в настройку tg_bot_token → в BotFather у бота
выключить Group Privacy (иначе он видит только команды и ответы себе) → бота
добавить в группы → POST /api/tg/setup ставит webhook на
https://app.kidsup.ru/api/tg/webhook/<секрет>. Telegram присылает каждое
сообщение групп сюда, мы складываем их в tg_messages. Клод читает
GET /api/tg/messages и отвечает POST /api/tg/send — только когда его просят.
Историю до появления бота в группе Telegram ботам не отдаёт.
"""
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

import httpx

from . import db

log = logging.getLogger("kidsup.tg")
API = "https://api.telegram.org/bot{token}/{method}"
MSK = timezone(timedelta(hours=3))


def _token() -> str:
    return db.get_setting("tg_bot_token") or ""


def _secret() -> str:
    s = db.get_setting("tg_webhook_secret")
    if not s:
        s = secrets.token_urlsafe(24)
        db.set_setting("tg_webhook_secret", s)
    return s


def _init(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS tg_messages (
        chat_id INTEGER, msg_id INTEGER, chat_title TEXT, from_id INTEGER, from_name TEXT,
        username TEXT, date TEXT, text TEXT, reply_to INTEGER, kind TEXT, raw TEXT,
        PRIMARY KEY (chat_id, msg_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS tg_messages_date ON tg_messages(date)")


def call(method: str, **params):
    tok = _token()
    if not tok:
        raise RuntimeError("не задан tg_bot_token — владелец вносит его через /api/settings")
    r = httpx.post(API.format(token=tok, method=method), json=params, timeout=30)
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError(f"Telegram {method}: {j.get('description')}")
    return j["result"]


def setup(base_url: str = "https://app.kidsup.ru") -> dict:
    """Проверить бота и поставить webhook. Секрет в пути и в заголовке —
    чужой POST на этот адрес отбрасывается."""
    me = call("getMe")
    s = _secret()
    call("setWebhook", url=f"{base_url}/api/tg/webhook/{s}", secret_token=s,
         allowed_updates=["message", "edited_message", "channel_post", "my_chat_member"])
    info = call("getWebhookInfo")
    return {"bot": me.get("username"), "webhook": info.get("url"),
            "pending": info.get("pending_update_count"), "last_error": info.get("last_error_message")}


def prinyat(update: dict, secret_path: str, header_secret: str | None) -> dict:
    s = _secret()
    if secret_path != s or header_secret != s:
        raise PermissionError("секрет не совпал")
    if update.get("my_chat_member"):
        ch = update["my_chat_member"]
        log.info("tg: бот в чате %s (%s) → %s", ch.get("chat", {}).get("title"),
                 ch.get("chat", {}).get("id"), ch.get("new_chat_member", {}).get("status"))
        return {"ok": True, "chat_member": True}
    msg = update.get("message") or update.get("edited_message") or update.get("channel_post")
    if not msg:
        return {"ok": True, "skip": True}
    chat = msg.get("chat") or {}
    frm = msg.get("from") or {}
    text = msg.get("text") or msg.get("caption") or ""
    kind = "text" if msg.get("text") else next(
        (k for k in ("photo", "document", "voice", "video", "sticker", "audio", "video_note", "contact", "location")
         if msg.get(k)), "other")
    if not text:
        text = f"[{kind}]"
    name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x) or frm.get("username") or ""
    when = datetime.fromtimestamp(int(msg.get("date") or 0), tz=MSK).strftime("%Y-%m-%dT%H:%M:%S")
    with db.get_conn() as conn:
        _init(conn)
        conn.execute("INSERT OR REPLACE INTO tg_messages VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (chat.get("id"), msg.get("message_id"), chat.get("title") or name, frm.get("id"), name,
                      frm.get("username"), when, text[:4000],
                      (msg.get("reply_to_message") or {}).get("message_id"), kind,
                      json.dumps(update, ensure_ascii=False)[:20000]))
    return {"ok": True}


def chats() -> list[dict]:
    with db.get_conn() as conn:
        _init(conn)
        rows = conn.execute("SELECT chat_id, chat_title, COUNT(*) AS n, MAX(date) AS last "
                            "FROM tg_messages GROUP BY chat_id ORDER BY last DESC").fetchall()
    return [dict(r) for r in rows]


def messages(chat_id: int | None = None, since: str = "", limit: int = 200, q: str = "") -> list[dict]:
    sql, args = "SELECT chat_id, chat_title, msg_id, from_name, username, date, text, reply_to, kind FROM tg_messages WHERE 1=1", []
    if chat_id:
        sql += " AND chat_id=?"; args.append(chat_id)
    if since:
        sql += " AND date>=?"; args.append(since)
    if q:
        sql += " AND text LIKE ?"; args.append(f"%{q}%")
    sql += " ORDER BY date DESC, msg_id DESC LIMIT ?"; args.append(max(1, min(int(limit), 1000)))
    with db.get_conn() as conn:
        _init(conn)
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows][::-1]


def send(chat_id: int, text: str, reply_to: int | None = None) -> dict:
    params = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
    if reply_to:
        params["reply_to_message_id"] = reply_to
    r = call("sendMessage", **params)
    return {"ok": True, "message_id": r.get("message_id")}
