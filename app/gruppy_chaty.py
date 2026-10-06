# -*- coding: utf-8 -*-
"""Рабочие группы Telegram через Wazzup: «Администраторы», «KidsUP Team» и др.

03.10.2026 Борис подключил групповые чаты Telegram к каналу Wazzup. Они
приходят в тот же вебхук, что и переписка с клиентами, но chatType у них
«telegroup» (у WhatsApp — «whatsgroup»), chatId — id группы, а в contact —
название группы и username автора (если у него есть username; имени автора
вебхук не даёт, аватар — групповой). До этого такие сообщения падали в
wazzup_inbox как «клиент написал» без автора, и автоматика могла поставить
по ним пункт «клиент ждёт ответа» или прислать клиентский автоответ в
рабочий чат. Теперь группы живут в своей таблице и из клиентских
сценариев исключены.

Время сообщения — из dateTime вебхука (UTC → МСК): при подключении группы
Wazzup выгрузил историю одной пачкой, и «время получения» у сотни старых
сообщений оказалось 13:25 одного дня. Автора без username восстанавливаем
по словарю gruppy_avtory {username → имя}, который пополняет импорт истории
с экрана Wazzup (там имена видны). Медиа (фото и видео педагогов — для
сториз) — contentUri в media_url.
"""
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from . import db

log = logging.getLogger("kidsup.gruppy")
GROUP_TYPES = ("telegroup", "whatsgroup")
MSK = ZoneInfo("Europe/Moscow")
# известные группы: chatId → название (на случай, если contact.name не пришёл)
KNOWN = {"4430280960": "Администраторы", "5256098255": "Английский язык в KidsUP",
         "4368234920": "Подготовке к школе", "4378505637": "KidsUP Team", "5458121214": "Администраторы new"}


def _init(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS gruppy_chaty (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, chat_id TEXT, chat_name TEXT,
        chat_type TEXT, author TEXT, username TEXT, text TEXT, kind TEXT,
        is_echo INTEGER, message_id TEXT UNIQUE)""")
    conn.execute("CREATE INDEX IF NOT EXISTS gruppy_chaty_ts ON gruppy_chaty(chat_id, ts)")
    for ddl in ("ALTER TABLE gruppy_chaty ADD COLUMN media_url TEXT",
                "ALTER TABLE gruppy_chaty ADD COLUMN raw TEXT"):
        try:
            conn.execute(ddl)
        except Exception:
            pass


def is_group(msg: dict) -> bool:
    return (msg.get("chatType") or "").lower() in GROUP_TYPES


def _ts(msg: dict) -> str:
    """dateTime вебхука (UTC, «2026-10-03T12:07:25.000Z») → МСК; нет — сейчас."""
    s = msg.get("dateTime")
    if s:
        try:
            t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return t.astimezone(MSK).isoformat(timespec="seconds")
        except Exception:
            pass
    from . import autopilot
    return autopilot._now().isoformat(timespec="seconds")


def avtory() -> dict:
    try:
        return json.loads(db.get_setting("gruppy_avtory") or "{}")
    except Exception:
        return {}


def _remember_avtory(pairs: dict) -> None:
    """username → имя (как видно на экране Wazzup)."""
    if not pairs:
        return
    cur = avtory()
    changed = False
    for u, n in pairs.items():
        if u and n and cur.get(u) != n:
            cur[u] = n
            changed = True
    if changed:
        db.set_setting("gruppy_avtory", json.dumps(cur, ensure_ascii=False))


def store(payload: dict) -> int:
    """Сообщения групп из вебхука Wazzup — в gruppy_chaty. Возвращает число новых."""
    rows, edits, deletes = [], [], []
    for msg in payload.get("messages") or []:
        if not is_group(msg):
            continue
        contact = msg.get("contact") or {}
        mid = str(msg.get("messageId") or "")
        text = (msg.get("text") or "").strip()
        kind = str(msg.get("type") or "text")
        if msg.get("isDeleted"):
            deletes.append(mid)
            continue
        if msg.get("isEdited") and mid:
            edits.append((text, mid))
        echo = bool(msg.get("isEcho"))
        chat_id = str(msg.get("chatId") or "")
        chat_name = KNOWN.get(chat_id) or str(contact.get("name") or "")
        username = str(contact.get("username") or "")
        author = str(msg.get("authorName") or "") if echo else ""
        if not author and username:
            author = avtory().get(username, "")
        rows.append((_ts(msg), chat_id, chat_name, (msg.get("chatType") or "").lower(),
                     author, username, (text or f"[{kind}]")[:4000], kind, 1 if echo else 0, mid,
                     str(msg.get("contentUri") or ""), json.dumps(msg, ensure_ascii=False)[:3000]))
    if not rows and not edits and not deletes:
        return 0
    with db.get_conn() as conn:
        _init(conn)
        n = 0
        for r in rows:
            cur = conn.execute("INSERT OR IGNORE INTO gruppy_chaty (ts, chat_id, chat_name, chat_type, author, "
                               "username, text, kind, is_echo, message_id, media_url, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", r)
            n += cur.rowcount
        for text, mid in edits:
            if text:
                conn.execute("UPDATE gruppy_chaty SET text=? WHERE message_id=?", (text[:4000], mid))
        for mid in deletes:
            conn.execute("UPDATE gruppy_chaty SET kind='deleted' WHERE message_id=?", (mid,))
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
    [{chat_id, chat_name, ts, author, username, text, message_id, is_echo, kind, media_url}].
    Если сообщение уже есть (пришло вебхуком), дополняем его тем, чего в вебхуке
    не было: точное время, автор, медиа-ссылка."""
    n, upd = 0, 0
    pairs = {}
    with db.get_conn() as conn:
        _init(conn)
        for r in rows:
            mid = str(r.get("message_id") or "") or f"wz-{r.get('chat_id')}-{r.get('ts')}-{abs(hash(r.get('text') or ''))}"
            author, username = str(r.get("author") or "").strip(), str(r.get("username") or "").strip()
            if author and username:
                pairs[username] = author
            cur = conn.execute("INSERT OR IGNORE INTO gruppy_chaty (ts, chat_id, chat_name, chat_type, author, username, "
                               "text, kind, is_echo, message_id, media_url) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (str(r.get("ts") or ""), str(r.get("chat_id") or ""), str(r.get("chat_name") or ""),
                                str(r.get("chat_type") or "telegroup"), author, username, str(r.get("text") or "")[:4000],
                                str(r.get("kind") or "text"), 1 if r.get("is_echo") else 0, mid, str(r.get("media_url") or "")))
            if cur.rowcount:
                n += 1
            else:
                cur = conn.execute(
                    "UPDATE gruppy_chaty SET ts=COALESCE(NULLIF(?,''), ts), author=CASE WHEN author='' OR author IS NULL THEN ? ELSE author END, "
                    "username=CASE WHEN username='' OR username IS NULL THEN ? ELSE username END, "
                    "media_url=CASE WHEN media_url='' OR media_url IS NULL THEN ? ELSE media_url END, "
                    "kind=CASE WHEN ?!='' AND ?!='text' THEN ? ELSE kind END WHERE message_id=?",
                    (str(r.get("ts") or ""), author, username, str(r.get("media_url") or ""),
                     str(r.get("kind") or ""), str(r.get("kind") or ""), str(r.get("kind") or ""), mid))
                upd += cur.rowcount
        # у строк, пришедших через вебхук без названия чата, проставим его
        for cid, name in {str(r.get("chat_id")): str(r.get("chat_name")) for r in rows if r.get("chat_name")}.items():
            conn.execute("UPDATE gruppy_chaty SET chat_name=? WHERE chat_id=? AND (chat_name IS NULL OR chat_name='')", (name, cid))
    _remember_avtory(pairs)
    pochinit()
    return {"ok": True, "dobavleno": n, "obnovleno": upd, "vsego": len(rows), "avtorov": len(avtory())}


def pochinit() -> dict:
    """Починка задним числом: время из dateTime в журнале сырых вебхуков
    (wazzup_raw хранит последние 300 событий) и авторы по словарю username → имя."""
    fixed_ts = fixed_author = 0
    names = avtory()
    with db.get_conn() as conn:
        _init(conn)
        try:
            raws = conn.execute("SELECT body FROM wazzup_raw ORDER BY id").fetchall()
        except Exception:
            raws = []
        for (body,) in raws:
            try:
                payload = json.loads(body)
            except Exception:
                continue
            for msg in payload.get("messages") or []:
                if not is_group(msg) or not msg.get("dateTime") or not msg.get("messageId"):
                    continue
                ts = _ts(msg)
                cur = conn.execute("UPDATE gruppy_chaty SET ts=? WHERE message_id=? AND ts > ?",
                                   (ts, str(msg["messageId"]), ts))
                fixed_ts += cur.rowcount
                if msg.get("contentUri"):
                    conn.execute("UPDATE gruppy_chaty SET media_url=? WHERE message_id=? AND (media_url IS NULL OR media_url='')",
                                 (str(msg["contentUri"]), str(msg["messageId"])))
        for u, nm in names.items():
            cur = conn.execute("UPDATE gruppy_chaty SET author=? WHERE username=? AND (author IS NULL OR author='')", (nm, u))
            fixed_author += cur.rowcount
    return {"ok": True, "ts": fixed_ts, "avtory": fixed_author}


def chats() -> list[dict]:
    with db.get_conn() as conn:
        _init(conn)
        rows = conn.execute("SELECT chat_id, MAX(CASE WHEN chat_name!='' THEN chat_name END) AS name, chat_type, "
                            "COUNT(*) AS n, MAX(ts) AS last FROM gruppy_chaty GROUP BY chat_id ORDER BY last DESC").fetchall()
    return [dict(r) for r in rows]


def messages(chat_id: str = "", since: str = "", limit: int = 200, q: str = "", media_only: bool = False) -> list[dict]:
    sql, args = ("SELECT ts, chat_id, chat_name, author, username, text, kind, is_echo, message_id, media_url "
                 "FROM gruppy_chaty WHERE kind != 'deleted'"), []
    if chat_id:
        sql += " AND chat_id LIKE ?"; args.append(f"%{chat_id}")
    if since:
        sql += " AND ts >= ?"; args.append(since)
    if q:
        sql += " AND text LIKE ?"; args.append(f"%{q}%")
    if media_only:
        sql += " AND media_url != '' AND media_url IS NOT NULL"
    sql += " ORDER BY ts DESC, id DESC LIMIT ?"; args.append(max(1, min(int(limit), 2000)))
    with db.get_conn() as conn:
        _init(conn)
        rows = conn.execute(sql, args).fetchall()
    names = avtory()
    out = []
    for r in rows:
        d = dict(r)
        if not d.get("author") and d.get("username"):
            d["author"] = names.get(d["username"], d["username"])
        out.append(d)
    return out[::-1]


PODPIS = "🤖 Клод (ассистент Бориса)"


def send(chat_id: str, text: str, chat_type: str = "telegroup") -> dict:
    """Написать в группу от имени канала Wazzup (Telegram-аккаунт центра).

    06.10.2026, Борис: «пиши в чат администраторов, только чтобы было понятно,
    что это ты». Сообщение уходит с аккаунта центра, и без подписи его читают
    как слова Бориса. Поэтому каждое сообщение начинается с PODPIS — всегда,
    а не по памяти того, кто его составляет."""
    from . import wazzup
    text = (text or "").strip()
    if not text.startswith("🤖"):
        text = f"{PODPIS}:\n{text}"
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
