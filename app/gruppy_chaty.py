# -*- coding: utf-8 -*-
"""Рабочие группы Telegram через Wazzup: «Администраторы», «KidsUP Team» и др.

03.10.2026 Борис подключил групповые чаты Telegram к каналу Wazzup. Они
приходят в тот же вебхук, что и переписка с клиентами, но chatType у них
«telegroup» (у WhatsApp — «whatsgroup»), chatId — id группы, а автор
сообщения — в contact.username / contact.name. До этого такие сообщения
падали в wazzup_inbox как «клиент написал» без автора, и автоматика могла
поставить по ним пункт «клиент ждёт ответа» или прислать клиентский
автоответ в рабочий чат. Теперь группы живут в своей таблице и из
клиентских сценариев исключены.
"""
import json
import logging

import httpx

from . import db

log = logging.getLogger("kidsup.gruppy")
GROUP_TYPES = ("telegroup", "whatsgroup")


def _init(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS gruppy_chaty (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, chat_id TEXT, chat_name TEXT,
        chat_type TEXT, author TEXT, username TEXT, text TEXT, kind TEXT,
        is_echo INTEGER, message_id TEXT UNIQUE)""")
    conn.execute("CREATE INDEX IF NOT EXISTS gruppy_chaty_ts ON gruppy_chaty(chat_id, ts)")


def is_group(msg: dict) -> bool:
    return (msg.get("chatType") or "").lower() in GROUP_TYPES


def store(payload: dict) -> int:
    """Сообщения групп из вебхука Wazzup — в gruppy_chaty. Возвращает число новых."""
    from . import autopilot
    rows = []
    for msg in payload.get("messages") or []:
        if not is_group(msg):
            continue
        contact = msg.get("contact") or {}
        text = (msg.get("text") or "").strip() or f"[{msg.get('type') or 'вложение'}]"
        echo = bool(msg.get("isEcho"))
        author = (msg.get("authorName") if echo else (contact.get("name") if not contact.get("username") else "")) or ""
        rows.append((autopilot._now().isoformat(timespec="seconds"), str(msg.get("chatId") or ""),
                     str(contact.get("name") or ""), (msg.get("chatType") or "").lower(),
                     author, str(contact.get("username") or ""), text[:4000],
                     str(msg.get("type") or "text"), 1 if echo else 0, str(msg.get("messageId") or "")))
    if not rows:
        return 0
    with db.get_conn() as conn:
        _init(conn)
        n = 0
        for r in rows:
            cur = conn.execute("INSERT OR IGNORE INTO gruppy_chaty (ts, chat_id, chat_name, chat_type, author, "
                               "username, text, kind, is_echo, message_id) VALUES (?,?,?,?,?,?,?,?,?,?)", r)
            n += cur.rowcount
    return n


def migrate() -> int:
    """Разовый перенос: групповые сообщения, уже осевшие в wazzup_inbox до 03.10,
    перекладываем сюда (без автора — его там не было) и убираем из клиентского ящика."""
    with db.get_conn() as conn:
        _init(conn)
        try:
            rows = conn.execute("SELECT ts, phone, chat_type, text, message_id FROM wazzup_inbox "
                                "WHERE chat_type IN ('telegroup','whatsgroup')").fetchall()
        except Exception:
            return 0
        for ts, phone, ct, text, mid in rows:
            conn.execute("INSERT OR IGNORE INTO gruppy_chaty (ts, chat_id, chat_name, chat_type, author, username, "
                         "text, kind, is_echo, message_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (ts, phone, "", ct, "", "", text, "text", 0, mid))
        conn.execute("DELETE FROM wazzup_inbox WHERE chat_type IN ('telegroup','whatsgroup')")
    return len(rows)


def import_rows(rows: list[dict]) -> dict:
    """Загрузить историю, снятую с экрана Wazzup (у API истории нет):
    [{chat_id, chat_name, ts, author, username, text, message_id, is_echo}]."""
    n = 0
    with db.get_conn() as conn:
        _init(conn)
        for r in rows:
            mid = str(r.get("message_id") or "") or f"wz-{r.get('chat_id')}-{r.get('ts')}-{abs(hash(r.get('text') or ''))}"
            cur = conn.execute("INSERT OR IGNORE INTO gruppy_chaty (ts, chat_id, chat_name, chat_type, author, username, "
                               "text, kind, is_echo, message_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (str(r.get("ts") or ""), str(r.get("chat_id") or ""), str(r.get("chat_name") or ""),
                                str(r.get("chat_type") or "telegroup"), str(r.get("author") or ""),
                                str(r.get("username") or ""), str(r.get("text") or "")[:4000],
                                str(r.get("kind") or "text"), 1 if r.get("is_echo") else 0, mid))
            n += cur.rowcount
        # у строк, пришедших через вебхук до 03.10 без названия чата, проставим его
        for cid, name in {str(r.get("chat_id")): str(r.get("chat_name")) for r in rows if r.get("chat_name")}.items():
            conn.execute("UPDATE gruppy_chaty SET chat_name=? WHERE chat_id=? AND (chat_name IS NULL OR chat_name='')", (name, cid))
    return {"ok": True, "dobavleno": n, "vsego": len(rows)}


def chats() -> list[dict]:
    with db.get_conn() as conn:
        _init(conn)
        rows = conn.execute("SELECT chat_id, MAX(CASE WHEN chat_name!='' THEN chat_name END) AS name, chat_type, "
                            "COUNT(*) AS n, MAX(ts) AS last FROM gruppy_chaty GROUP BY chat_id ORDER BY last DESC").fetchall()
    return [dict(r) for r in rows]


def messages(chat_id: str = "", since: str = "", limit: int = 200, q: str = "") -> list[dict]:
    sql, args = ("SELECT ts, chat_id, chat_name, author, username, text, kind, is_echo, message_id "
                 "FROM gruppy_chaty WHERE 1=1"), []
    if chat_id:
        sql += " AND chat_id LIKE ?"; args.append(f"%{chat_id}")
    if since:
        sql += " AND ts >= ?"; args.append(since)
    if q:
        sql += " AND text LIKE ?"; args.append(f"%{q}%")
    sql += " ORDER BY ts DESC, id DESC LIMIT ?"; args.append(max(1, min(int(limit), 1000)))
    with db.get_conn() as conn:
        _init(conn)
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows][::-1]


def send(chat_id: str, text: str, chat_type: str = "telegroup") -> dict:
    """Написать в группу от имени канала Wazzup (Telegram-аккаунт центра)."""
    from . import wazzup
    chans = wazzup.channels()
    ch = wazzup._pick(chans, "tgapi" if chat_type == "telegroup" else "whatsapp")
    if not ch:
        raise RuntimeError("нет канала Wazzup для " + chat_type)
    r = httpx.post(f"{wazzup.API}/message", headers=wazzup._headers(), timeout=30,
                   json={"channelId": ch["channelId"], "chatType": chat_type, "chatId": str(chat_id), "text": text[:4000]})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Wazzup HTTP {r.status_code}: {r.text[:200]}")
    try:
        mid = r.json().get("messageId")
    except Exception:
        mid = None
    return {"ok": True, "message_id": mid}
