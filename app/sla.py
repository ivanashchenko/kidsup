"""Дисциплина по задачам: SLA, эскалация, проверка закрытия, потолок нагрузки.

Три правила, которых нам не хватило дважды за два дня:
  1. У каждого вида задач свой срок. Просрочка вдвое → задача уходит второму
     администратору, Борису — короткое сообщение.
  2. Закрытие только с результатом: через 10 минут после закрытия проверяем,
     был ли звонок в Mango или сообщение в Wazzup по этому клиенту. Нет —
     задача открывается заново с пометкой.
  3. Потолок нагрузки: не больше N звонков и M сообщений в день на человека,
     остальное остаётся в очереди.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from . import db
from .autopilot import (CAT_CALL, CAT_CHAT, CAT_ORG, CAT_PUSH, CAT_URGENT,
                        _admins_today, _client, _mark, _now)

log = logging.getLogger("kidsup.sla")

# сколько минут даётся на задачу с момента её начала
SLA_MINUTES = {
    CAT_URGENT: 15,      # 🔥 срочно
    CAT_CHAT: 30,        # 💬 чат
    CAT_PUSH: 24 * 60,   # 🔁 дожим — сутки
    CAT_CALL: 10 * 60,   # 📞 прозвон — рабочий день
    CAT_ORG: 3 * 24 * 60,
}
CAT_NAME = {CAT_URGENT: "🔥 срочно", CAT_CHAT: "💬 чат", CAT_PUSH: "🔁 дожим",
            CAT_CALL: "📞 прозвон", CAT_ORG: "🏢 орг"}

# потолок нагрузки на человека в день
CAP_CALLS = 25
CAP_CHATS = 20
CAP_NEWBIE_FACTOR = 0.5      # новичку первую неделю — половина нормы


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone(_now().tzinfo) if d.tzinfo else d.replace(tzinfo=_now().tzinfo)


def _open_tasks(mk) -> list[dict]:
    out, off = [], 0
    while True:
        d = mk.get("/v1/company/tasks", {"isComplete": "false", "limit": 100,
                                         "offset": off})
        batch = d.get("tasks") if isinstance(d, dict) else d
        if not batch:
            break
        out += batch
        if len(batch) < 100:
            break
        off += 100
        if off > 2000:
            break
    return out


def _update(mk, task: dict, **over) -> None:
    body = {k: task.get(k) for k in ("name", "body", "userId", "categoryId")}
    body["managerIds"] = task.get("managerIds") or []
    body["beginDate"] = task["beginDate"]
    body["endDate"] = task["endDate"]
    body.update(over)
    mk.post(f"/v1/company/tasks/{task['id']}", {k: v for k, v in body.items()
                                                if v is not None})


def escalate_overdue() -> dict:
    """Просрочка вдвое → задача переходит на второго админа смены."""
    mk = _client()
    moved, alerts = 0, []
    try:
        admins = [a["managerId"] for a in _admins_today()]
        if len(admins) < 2:
            admins += [a["managerId"] for a in _admins_today()]     # некому — оставим себе
        now = _now()
        for t in _open_tasks(mk):
            cat = t.get("categoryId")
            limit = SLA_MINUTES.get(cat)
            begin = _parse(t.get("beginDate"))
            if not limit or not begin:
                continue
            if now - begin < timedelta(minutes=2 * limit):
                continue
            if not _mark("sla_esc", str(t["id"])):
                continue                                   # уже эскалировали
            cur = (t.get("managerIds") or [None])[0]
            other = next((a for a in admins if a != cur), None)
            if other:
                _update(mk, t, managerIds=[other])
                moved += 1
            alerts.append(f"{CAT_NAME.get(cat, cat)}: {(t.get('body') or '')[:60]}")
    finally:
        mk.close()
    if alerts:
        log.warning("SLA: просрочено вдвое — %d задач, переназначено %d",
                    len(alerts), moved)
        db.set_setting("sla_last_alert", json.dumps(
            {"ts": _now().isoformat(timespec="seconds"), "moved": moved,
             "items": alerts[:10]}, ensure_ascii=False))
    return {"overdue": len(alerts), "moved": moved}


def _had_action(user_id: int | None, since: datetime) -> bool:
    """Был ли по клиенту звонок или сообщение после указанного момента."""
    if not user_id:
        return True                       # задача без клиента — не проверяем
    with db.get_conn() as conn:
        row = conn.execute("SELECT phone FROM users WHERE id=?", (user_id,)).fetchone()
        phone = (row[0] if row else "") or ""
        p = "".join(ch for ch in phone if ch.isdigit())[-10:]
        if len(p) < 10:
            return True
        s = since.isoformat(timespec="seconds")
        for sql in ("SELECT 1 FROM wazzup_outbox WHERE substr(phone,-10)=? AND ts>=? LIMIT 1",
                    "SELECT 1 FROM mango_calls WHERE substr(phone,-10)=? AND ts>=? LIMIT 1"):
            try:
                if conn.execute(sql, (p, s)).fetchone():
                    return True
            except Exception:
                continue
    return False


def verify_closed() -> dict:
    """Закрытые за последний час задачи без единого действия — открываем заново."""
    mk = _client()
    reopened = 0
    try:
        since = _now() - timedelta(hours=1)
        d = mk.get("/v1/company/tasks", {"isComplete": "true", "limit": 100})
        tasks = d.get("tasks") if isinstance(d, dict) else d
        for t in tasks or []:
            begin = _parse(t.get("beginDate"))
            if not begin or begin < since - timedelta(days=1):
                continue
            if not _mark("sla_verify", str(t["id"])):
                continue
            if _had_action(t.get("userId"), begin):
                continue
            body = ("⚠️ Закрыта без действия (нет ни звонка, ни сообщения). "
                    + (t.get("body") or ""))[:250]
            _update(mk, t, isComplete=False, body=body)
            reopened += 1
    finally:
        mk.close()
    if reopened:
        log.warning("SLA: открыто заново %d задач, закрытых без действия", reopened)
    return {"reopened": reopened}


def load_caps() -> dict:
    """Сколько задач сегодня у каждого админа и не пробит ли потолок."""
    mk = _client()
    try:
        tasks = _open_tasks(mk)
    finally:
        mk.close()
    today = _now().date().isoformat()
    per: dict[int, dict[str, int]] = {}
    for t in tasks:
        if (t.get("beginDate") or "")[:10] != today:
            continue
        for m in (t.get("managerIds") or []):
            p = per.setdefault(m, {"calls": 0, "chats": 0, "other": 0})
            cat = t.get("categoryId")
            if cat in (CAT_CALL, CAT_PUSH):
                p["calls"] += 1
            elif cat == CAT_CHAT:
                p["chats"] += 1
            else:
                p["other"] += 1
    out = {"date": today, "caps": {"calls": CAP_CALLS, "chats": CAP_CHATS},
           "load": per,
           "over": {m: v for m, v in per.items()
                    if v["calls"] > CAP_CALLS or v["chats"] > CAP_CHATS}}
    return out
