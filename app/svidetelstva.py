"""Доказательства по каждому делу пульта: сделано оно или ещё нет.

23.09.2026, Борис: «Собери пульт на завтра — перепроверь каждую задачу, что
она ещё актуальная и не сделанная!!» Проверять «на глаз» по тексту нельзя:
текст пункта говорит, что надо было сделать, а не что сделали. Сделанное
оставляет следы — комментарий администратора в карточке, звонок, сообщение,
оплату, визит, запись на занятие. Здесь по каждому делу дня собираются все
эти следы по всем телефонам, которые в нём упомянуты, — только чтение.

Решение «сделано / не сделано / переписать» принимает не этот модуль, а тот,
кто читает выгрузку: следы бывают косвенными (переписка была, но про другое),
и их надо читать глазами.

  GET /api/pult/svidetelstva?day=2026-09-23&offset=0&limit=25
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from . import db

STATUS = {125951: "новый лид", 345768: "недозвон", 146950: "думает",
          125952: "записался на пробное", 125953: "посетил пробное",
          125955: "клиент", 125956: "неактивный клиент", 125957: "отказ",
          345759: "архив набора", 146328: "не писать/не звонить",
          215202: "не работаем", 125954: "некачественный лид",
          347075: "от промоутера", 353124: "холодный обзвон"}
JOIN = {2: "учится", 58132: "записан на пробное", 83760: "подтвердил",
        58131: "был на пробном", 50509: "новая заявка", 1: "отчислен", 4: "архив"}

PHONE_RE = re.compile(r"(?<!\d)(?:\+?7|8)?[\s(-]*(9\d{2})[\s)-]*(\d{3})[\s-]*(\d{2})[\s-]*(\d{2})(?!\d)")


def _p10(x) -> str:
    d = "".join(c for c in str(x or "") if c.isdigit())
    return d[-10:] if len(d) >= 10 else ""


def _telefony(text: str, phone: str = "") -> list[str]:
    out: list[str] = []
    p = _p10(phone)
    if p:
        out.append(p)
    for m in PHONE_RE.finditer(text or ""):
        q = "".join(m.groups())
        if q not in out:
            out.append(q)
    return out[:8]


def _dela(conn, day: str) -> list[dict]:
    """Всё, что окажется на пульте в этот день: задачи смены, пункты дня
    и незакрытый хвост прошлых дней."""
    out: list[dict] = []
    for r in conn.execute("SELECT id, who, t, text, done, ts FROM pult_tasks "
                          "WHERE day=? ORDER BY who, t, id", (day,)).fetchall():
        if r["done"]:
            continue
        out.append({"вид": "задача", "id": r["id"], "кто": r["who"], "день": day,
                    "время": r["t"], "создано": r["ts"] or "", "текст": r["text"] or "",
                    "телефон": ""})
    for r in conn.execute("SELECT id, day, ts, who, text, phone, source FROM plan_inbox "
                          "WHERE done=0 AND day<=? ORDER BY (day<?) , day DESC, id",
                          (day, day)).fetchall():
        out.append({"вид": "пункт", "id": r["id"], "кто": r["who"], "день": r["day"],
                    "время": (r["ts"] or "")[11:16], "создано": r["ts"] or "",
                    "текст": r["text"] or "", "телефон": r["phone"] or "",
                    "источник": r["source"] or "",
                    "хвост": (r["day"] or day) < day})
    return out


def _sledy(conn, p10: str, since: str, day: str) -> dict:
    """Все следы работы по одному телефону начиная с since."""
    from .voronka import MANAGERS, _robot
    like = f"%{p10}"
    users = conn.execute(
        "SELECT id, name, client_state_id FROM users WHERE "
        "substr(replace(replace(replace(COALESCE(phone,''),'+',''),'-',''),' ',''),-10)=?",
        (p10,)).fetchall()
    uids = [u["id"] for u in users]
    q = ",".join("?" * len(uids)) or "NULL"
    res: dict = {"телефон": p10,
                 "карточки": [{"id": u["id"], "имя": u["name"],
                               "статус": STATUS.get(u["client_state_id"], u["client_state_id"])}
                              for u in users]}

    def _try(fn, default):
        try:
            return fn()
        except Exception:
            return default

    res["комментарии"] = _try(lambda: [
        {"когда": r["ts"][:16], "кто": (MANAGERS.get(r["manager_id"], "автоматика")
                                       if not _robot(r["text"]) else "автоматика/Клод"),
         "текст": (r["text"] or "")[:260]}
        for r in conn.execute(f"SELECT ts, manager_id, text FROM crm_comments WHERE user_id IN ({q}) "
                              f"AND ts>=? ORDER BY ts DESC LIMIT 6", (*uids, since)).fetchall()], [])
    res["оплаты"] = _try(lambda: [
        {"дата": r["date"][:10], "сумма": r["summa"]}
        for r in conn.execute(f"SELECT date, summa FROM payments WHERE user_id IN ({q}) "
                              f"AND date>=? ORDER BY date DESC LIMIT 5", (*uids, since[:10])).fetchall()], [])
    classes = {}

    def _cls(cid):
        if cid not in classes:
            row = conn.execute("SELECT name FROM classes WHERE id=?", (cid,)).fetchone()
            classes[cid] = (row["name"] if row else str(cid)).replace("2627_", "")[:48]
        return classes[cid]

    res["был_на_занятиях"] = _try(lambda: [
        f"{r['date'][:10]} {_cls(r['class_id'])}"
        for r in conn.execute(f"SELECT l.date, l.class_id FROM lesson_records r JOIN lessons l "
                              f"ON l.id=r.lesson_id WHERE r.user_id IN ({q}) AND r.visit=1 "
                              f"AND l.date>=? ORDER BY l.date DESC LIMIT 5",
                              (*uids, since[:10])).fetchall()], [])
    res["записи_впереди"] = _try(lambda: [
        f"{r['date'][:10]} {(r['begin_time'] or '')[:5]} {_cls(r['class_id'])}"
        for r in conn.execute(f"SELECT DISTINCT l.date, l.begin_time, l.class_id FROM lesson_records r "
                              f"JOIN lessons l ON l.id=r.lesson_id WHERE r.user_id IN ({q}) "
                              f"AND l.date>=? ORDER BY l.date LIMIT 6",
                              (*uids, day)).fetchall()], [])
    res["группы"] = _try(lambda: [
        f"{_cls(r['class_id'])} — {JOIN.get(r['status_id'], r['status_id'])}"
        for r in conn.execute(f"SELECT class_id, status_id FROM joins WHERE user_id IN ({q}) "
                              f"AND status_id NOT IN (1,4) LIMIT 6", uids).fetchall()], [])
    res["звонки"] = _try(lambda: [
        {"когда": r[0][:16], "направление": r[1], "итог": r[2], "сек": r[3]}
        for r in conn.execute("SELECT ts, direction, state, COALESCE(secs, -1) FROM mango_calls "
                              "WHERE phone LIKE ? AND ts>=? ORDER BY ts DESC LIMIT 6",
                              (like, since)).fetchall()], [])
    res["мы_писали"] = _try(lambda: [
        {"когда": r[0][:16], "кто": r[2] or "", "текст": (r[1] or "")[:170]}
        for r in conn.execute("SELECT ts, text, COALESCE(author_name,'') FROM wazzup_outbox "
                              "WHERE phone LIKE ? AND ts>=? ORDER BY ts DESC LIMIT 5",
                              (like, since)).fetchall()], [])
    res["нам_писали"] = _try(lambda: [
        {"когда": r[0][:16], "текст": (r[1] or "")[:170]}
        for r in conn.execute("SELECT ts, text FROM wazzup_inbox WHERE phone LIKE ? AND ts>=? "
                              "ORDER BY ts DESC LIMIT 5", (like, since)).fetchall()], [])
    return res


def vygruzka(day: str = "", offset: int = 0, limit: int = 25) -> dict:
    from . import pult
    day = day or pult.today()
    with db.get_conn() as conn:
        dela = _dela(conn, day)
        kusok = dela[offset:offset + limit]
        for d in kusok:
            # следы ищем с суток до появления дела: ответ клиента мог прийти
            # раньше, чем автоматика положила пункт, и тогда пункт уже лишний
            try:
                base = date.fromisoformat((d["создано"] or d["день"])[:10])
            except ValueError:
                base = date.fromisoformat(d["день"])
            since = (base - timedelta(days=1)).isoformat()
            d["следы"] = [_sledy(conn, p, since, day)
                          for p in _telefony(d["текст"], d.get("телефон", ""))]
    return {"день": day, "всего": len(dela), "offset": offset, "limit": limit,
            "по_видам": {"задачи": sum(1 for x in dela if x["вид"] == "задача"),
                         "пункты_дня": sum(1 for x in dela if x["вид"] == "пункт" and not x.get("хвост")),
                         "хвост": sum(1 for x in dela if x.get("хвост"))},
            "дела": kusok}
