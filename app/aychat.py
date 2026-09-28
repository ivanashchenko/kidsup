"""Точечная рассылка утверждённым текстом по явному списку номеров.

21.09 владелец попросил повторно напомнить семьям английского про личный
кабинет и чат педагога — и сначала показать тексты. Обычная кампания
(/api/broadcast) для этого не годится: там один текст на всех, а здесь
половине адресатов в сообщение подставляется их собственная почта.
Очередь с темпом тоже не нужна — адресатов два десятка, они уходят сразу.

Отправка идёт только отсюда, с сервера: в контейнере Клода локальная база
может стоять с wazzup_dry_run=0, и вызов функций отправки «на пробу» уже
приводил к настоящей рассылке (03.09, 40 сообщений).

СМС уходит параллельно мессенджеру и только тем, кто у нас платил:
рекламная СМС человеку, который никогда не покупал, — риск штрафа по
закону о рекламе (решение владельца 24.08).
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from . import db

log = logging.getLogger("kidsup.aychat")


def _p10(x) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())[-10:]


def _paid_before(phone: str) -> bool:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM users u JOIN payments p ON p.user_id = u.id "
            "WHERE substr(u.phone,-10)=? AND p.summa > 0 LIMIT 1", (_p10(phone),)).fetchone()
    return bool(row)


def send(items: list[dict], kind: str = "ay_chat", dry: bool = True,
         sms: bool = False, limit: int = 30) -> dict:
    """items = [{"phone": "79…", "text": "…", "sms": "…"}]. dry=True ничего не шлёт."""
    from . import mango, wazzup
    from .autopilot import _now
    now = _now()
    if not (9 <= now.hour < 20):
        return {"ok": False, "error": "вне окна 9:00–20:00", "sent": 0}
    sms_on = db.get_setting("sms_on", "0") == "1"
    out, errs = [], []
    for it in (items or [])[:max(1, min(60, int(limit)))]:
        phone, text = str(it.get("phone") or ""), str(it.get("text") or "")
        if len(_p10(phone)) != 10 or not text:
            errs.append({"phone": phone, "error": "нет номера или текста"})
            continue
        row = {"phone": phone, "каналы": [], "смс": None}
        if dry:
            row["каналы"] = wazzup.channels_for(phone)
            row["смс"] = "ушла бы" if (sms and sms_on and it.get("sms") and _paid_before(phone)) else "нет"
            out.append(row)
            continue
        try:
            row["каналы"] = wazzup.send_smart(phone, text, dry_run=False, mass=False, kind=kind)
            ok = any("ok" in x for x in row["каналы"])
        except Exception as e:  # noqa: BLE001
            ok, row["каналы"] = False, [str(e)[:150]]
        if sms and sms_on and it.get("sms") and _paid_before(phone):
            try:
                row["смс"] = "отправлена" if mango.send_sms(phone, str(it["sms"])) else "не принята"
            except Exception as e:  # noqa: BLE001
                row["смс"] = f"ошибка: {str(e)[:80]}"
        (out if ok else errs).append(row)
    log.info("aychat.send(%s): отправлено %d, ошибок %d, dry=%s", kind, len(out), len(errs), dry)
    return {"ok": True, "dry_run": dry, "отправлено": len(out), "ошибок": len(errs),
            "детали": out, "ошибки": errs}


# ------------------------------------------------------------ отложенная отправка
# 28.09, 20:15: Борис «запроси у них почту ещё раз», а окно отправки уже
# закрыто (9–20). Разовые сообщения на завтрашнее утро кладём сюда, минутный
# цикл автопилота отправляет их в срок — по одному, раз в 1–2 минуты.

def _otl(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS otlozh (
        id INTEGER PRIMARY KEY AUTOINCREMENT, due TEXT, item TEXT, kind TEXT,
        sms INTEGER DEFAULT 0, status TEXT DEFAULT 'pending', result TEXT, sent TEXT)""")


def otlozhit(items: list[dict], due: str, kind: str = "razovoe", sms: bool = False) -> dict:
    """items как у send(); due — «YYYY-MM-DDTHH:MM» по Москве."""
    with db.get_conn() as conn:
        _otl(conn)
        for it in items or []:
            conn.execute("INSERT INTO otlozh (due, item, kind, sms) VALUES (?,?,?,?)",
                         (due, json.dumps(it, ensure_ascii=False), kind, 1 if sms else 0))
        n = conn.execute("SELECT COUNT(*) FROM otlozh WHERE status='pending'").fetchone()[0]
    return {"ok": True, "поставлено": len(items or []), "в_очереди": n, "когда": due}


def otlozh_tick() -> dict | None:
    import random
    from .autopilot import _now
    now = _now()
    if not (10 <= now.hour < 20):
        return None
    if (db.get_setting("otlozh_next", "") or "") > now.isoformat(timespec="seconds"):
        return None
    with db.get_conn() as conn:
        _otl(conn)
        row = conn.execute("SELECT id, item, kind, sms FROM otlozh WHERE status='pending' AND due<=? "
                           "ORDER BY due, id LIMIT 1", (now.isoformat(timespec="minutes"),)).fetchone()
    if not row:
        return None
    oid, item, kind, sms = row
    res = send([json.loads(item)], kind=kind, dry=False, sms=bool(sms), limit=1)
    ok = bool(res.get("отправлено"))
    with db.get_conn() as conn:
        conn.execute("UPDATE otlozh SET status=?, result=?, sent=? WHERE id=?",
                     ("sent" if ok else "fail", json.dumps(res.get("детали") or res.get("ошибки") or res,
                                                          ensure_ascii=False)[:500],
                      now.isoformat(timespec="seconds"), oid))
    db.set_setting("otlozh_next", (now + timedelta(seconds=random.randint(60, 120))).isoformat(timespec="seconds"))
    return {"id": oid, "ok": ok}


def otlozh_status() -> list[dict]:
    with db.get_conn() as conn:
        _otl(conn)
        return [{"id": r[0], "когда": r[1], "телефон": json.loads(r[2]).get("phone"), "статус": r[3],
                 "итог": (r[4] or "")[:200]} for r in conn.execute(
            "SELECT id, due, item, status, result FROM otlozh ORDER BY id DESC LIMIT 50")]
