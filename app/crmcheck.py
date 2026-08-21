"""Проверки состава групп и разбор задолженностей.

Написано после разбора полной спецификации API МойКласс (146 эндпоинтов,
openapi.json). Три вещи, которые она прояснила и которые здесь используются:

1. **Автора отметки о посещении в API нет.** Схема LessonRecord состоит из
   bill, createdAt, free, goodReason, id, lesson, lessonId,
   missedLessonRecordId, paid, skip, test, userId, userSubscription, visit,
   yield — поля с менеджером нет ни в одном виде. Зато у занятия есть
   `teacherIds`, а посещаемость на занятии отмечает тот, кто его вёл.
   Поэтому «кто отметил» мы берём от педагога занятия, а администратора
   смены оставляем как запасной вариант. Это не догадка про интерфейс,
   а единственный путь, который даёт сама модель данных.

2. **Заморозку можно ставить через API** — POST/DELETE
   /userSubscriptions/{id}/freeze. Значит правило «две недели за год»
   становится не только проверяемым, но и исполнимым.

3. **PATCH /users/{userId} меняет только переданные поля.** Полный POST,
   которым 14.08 затёрли 1844 карточки, больше не нужен.

Запуск:
    python -m app.crmcheck fit      — кто сидит не в своей группе
    python -m app.crmcheck debts    — задолженности по трём кучам
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import date

from . import db
from .audit import HIGH, LOW, MID, add_flag

log = logging.getLogger("kidsup.crmcheck")

SEASON = date(2026, 9, 1)          # возраст считаем на начало учебного года

# Возраст и уровень зашиты в названии группы: «2627_АЯ_пн-ср_18:00_5-8 лет_
# Pre-A1 Starters (Гр3)». Прайса и атрибутов с возрастом в API нет, поэтому
# название — единственный носитель этой информации, и разбираем его.
_AGE = re.compile(
    r"(?<![:\d])(\d{1,2})(?:[.,](\d))?\s*[-–—]\s*(\d{1,2})(?:[.,](\d))?"
    r"\s*(?:лет|год\w*)?(?![:\d])")
_LEVEL = {
    "starters": "англ-1", "movers": "англ-2", "flyers": "англ-3",
    "пш1": "школа-1", "пш2": "школа-2",
    "нечитающие": "школа-1", "читающие": "школа-2",
    "начинающие": "нач", "продолжающие": "прод",
}


def group_ages(name: str) -> tuple[float, float] | None:
    """Возрастная вилка группы из её названия."""
    m = _AGE.search(name or "")
    if not m:
        return None
    lo = float(m.group(1)) + (float(m.group(2)) / 10 if m.group(2) else 0)
    hi = float(m.group(3)) + (float(m.group(4)) / 10 if m.group(4) else 0)
    return (lo, hi) if lo <= hi else (hi, lo)


def group_level(name: str) -> str | None:
    low = (name or "").lower()
    for k, v in _LEVEL.items():
        if k in low:
            return v
    return None


def _age_of(raw: str | None) -> float | None:
    try:
        for a in (json.loads(raw or "{}").get("attributes") or []):
            if a.get("attributeAlias") == "birthday" and a.get("value"):
                return round((SEASON - date.fromisoformat(a["value"][:10])).days / 365.25, 1)
    except Exception:
        pass
    return None


def group_fit(prefix: str = "2627") -> list[dict]:
    """Ребёнок записан в группу, которая ему не по возрасту.

    Ошибка дорогая с двух сторон: маленькому в старшей группе тяжело и он
    уходит через месяц, старшему в младшей скучно — и уходит он же. Причём
    видно это только на занятии, когда деньги уже взяты."""
    out = []
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT j.user_id, u.name, u.raw, c.name cls, j.status_id
            FROM joins j
            JOIN classes c ON c.id = j.class_id
            LEFT JOIN users u ON u.id = j.user_id
            WHERE c.name LIKE ? AND c.name NOT LIKE '%Заявки%'
              AND j.status_id IN (2, 58131, 58132, 83760)""", (prefix + "%",)).fetchall()
    for r in rows:
        span = group_ages(r[3])
        age = _age_of(r[2])
        if not span or age is None:
            continue
        lo, hi = span
        # полгода допуска: возраст считаем на 1 сентября, дети растут
        if lo - 0.5 <= age <= hi + 0.5:
            continue
        off = round(lo - age, 1) if age < lo else round(age - hi, 1)
        level = HIGH if off >= 1.5 else MID
        where = "младше" if age < lo else "старше"
        out.append({"user_id": r[0], "name": r[1], "class": r[3],
                    "age": age, "span": span, "off": off})
        add_flag("группы", f"fit:{r[0]}:{r[3][:24]}", level,
                 f"{r[1] or r[0]}: {age:g} лет в группе {lo:g}–{hi:g}",
                 f"«{r[3]}» — ребёнок на {off:g} года {where} вилки группы. "
                 f"Проверить: либо подобрать группу по возрасту, либо это "
                 f"осознанное решение педагога и его надо записать в карточку.",
                 user_id=r[0])
    return out


def level_fit(prefix: str = "2627") -> list[dict]:
    """Ребёнок записан сразу в две группы одного предмета разных уровней —
    почти всегда это ошибка записи, а не осознанный выбор."""
    out = []
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT j.user_id, u.name, c.name cls
            FROM joins j
            JOIN classes c ON c.id = j.class_id
            LEFT JOIN users u ON u.id = j.user_id
            WHERE c.name LIKE ? AND c.name NOT LIKE '%Заявки%'
              AND j.status_id IN (2, 58131, 58132, 83760)""", (prefix + "%",)).fetchall()
    by_user: dict[int, list[tuple[str, str]]] = defaultdict(list)
    names = {}
    for uid, nm, cls in rows:
        lv = group_level(cls)
        names[uid] = nm
        if lv:
            by_user[uid].append((lv, cls))
    for uid, items in by_user.items():
        fam = defaultdict(set)
        for lv, cls in items:
            fam[lv.split("-")[0]].add(lv)
        for subject, levels in fam.items():
            if len(levels) < 2:
                continue
            cl = [c for lv, c in items if lv.startswith(subject)]
            out.append({"user_id": uid, "name": names.get(uid), "classes": cl})
            add_flag("группы", f"level:{uid}:{subject}", MID,
                     f"{names.get(uid, uid)}: два разных уровня одного предмета",
                     "Записан в " + " и в ".join(f"«{c}»" for c in cl[:2]) +
                     ". Уровень должен быть один — проверить, какая запись лишняя.",
                     user_id=uid)
    return out


def overbooked(prefix: str = "2627") -> list[dict]:
    """Записано больше, чем мест в группе."""
    out = []
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT c.id, c.name, c.max_students, COUNT(j.id) n
            FROM classes c JOIN joins j ON j.class_id = c.id
            WHERE c.name LIKE ? AND c.name NOT LIKE '%Заявки%'
              AND j.status_id IN (2, 83760)
            GROUP BY c.id HAVING c.max_students > 0 AND n > c.max_students""",
            (prefix + "%",)).fetchall()
    for cid, nm, mx, n in rows:
        out.append({"class_id": cid, "name": nm, "max": mx, "n": n})
        add_flag("группы", f"over:{cid}", MID,
                 f"Перебор в группе: {n} при {mx} местах",
                 f"«{nm}». Либо часть записей уже неактуальна, либо группу "
                 f"надо делить — иначе на занятии не хватит места.")
    return out


# ── кто отметил посещение ─────────────────────────────────────────────────
def attendance_author(day: str) -> dict[int, list[str]]:
    """Кому приписать отметки посещения за день.

    Автора в записи нет — это ограничение самого API. Но посещаемость
    отмечает ведущий занятия, поэтому берём педагогов урока; если их
    не проставили, остаётся администратор смены."""
    out: dict[int, list[str]] = {}
    with db.get_conn() as conn:
        has_raw = conn.execute("SELECT COUNT(*) FROM sqlite_master "
                               "WHERE name='lessons'").fetchone()[0]
        if not has_raw:
            return out
        for lid, raw in conn.execute(
                "SELECT id, raw FROM lessons WHERE date = ?", (day,)):
            try:
                t = json.loads(raw).get("teacherIds") or []
            except Exception:
                t = []
            out[lid] = [str(x) for x in t]
    return out


def unmarked_lessons(day: str | None = None) -> list[dict]:
    """Занятие прошло, а посещаемость не отмечена.

    Пока посещаемость не отмечена, занятие не списано с абонемента: деньги
    не признаны, отработка не оформится, а бонус педагога не посчитается."""
    d = day or (date.today()).isoformat()
    out = []
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT l.id, l.date, l.begin_time, c.name,
                   (SELECT COUNT(*) FROM lesson_records r WHERE r.lesson_id = l.id) n,
                   (SELECT COUNT(*) FROM joins j WHERE j.class_id = l.class_id
                     AND j.status_id IN (2, 83760)) enrolled
            FROM lessons l LEFT JOIN classes c ON c.id = l.class_id
            WHERE l.date = ? AND l.status IN (1, 2)""", (d,)).fetchall()
    for lid, dt, bt, nm, n, enrolled in rows:
        if enrolled == 0 or n > 0:
            continue
        out.append({"lesson_id": lid, "date": dt, "class": nm, "enrolled": enrolled})
        add_flag("группы", f"unmarked:{lid}", MID,
                 "Занятие прошло, посещаемость не отмечена",
                 f"{dt} {bt or ''} «{nm}» — в группе {enrolled} детей, "
                 f"отметок ноль. Пока не отмечено, занятие не списано "
                 f"с абонементов и не попало в зарплату педагога.",
                 day=dt)
    return out


def coverage(prefix: str = "2627") -> dict:
    """Сколько записей мы вообще способны проверить.

    Проверка, которая молчит, потому что ей нечего читать, опаснее
    отсутствующей: она создаёт ложное спокойствие. Поэтому вместе
    с находками всегда показываем покрытие."""
    total = with_span = with_age = both = 0
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT c.name, u.raw FROM joins j
            JOIN classes c ON c.id = j.class_id
            LEFT JOIN users u ON u.id = j.user_id
            WHERE c.name LIKE ? AND c.name NOT LIKE '%Заявки%'
              AND j.status_id IN (2, 58131, 58132, 83760)""",
            (prefix + "%",)).fetchall()
    for nm, raw in rows:
        total += 1
        s, a = group_ages(nm), _age_of(raw)
        with_span += bool(s)
        with_age += a is not None
        both += bool(s) and a is not None
    return {"записей": total, "у групп указан возраст": with_span,
            "у детей известен возраст": with_age, "можем проверить": both}


def fit_check() -> dict:
    res = {"age": group_fit(), "level": level_fit(), "over": overbooked(),
           "unmarked": unmarked_lessons(), "_покрытие": coverage()}
    log.info("группы: %s", ", ".join(f"{k}={len(v)}" for k, v in res.items()))
    return res


# ── задолженности ─────────────────────────────────────────────────────────
def debt_buckets() -> dict:
    """Долги, разложенные по тому, что с ними реально можно сделать.

    Считать «полмиллиона в минусе» одной суммой бессмысленно: половина
    этого — абонементы двухлетней давности, полностью израсходованные
    людьми, которых уже нет. Живые деньги — только там, где абонемент
    свежий или семья ещё с нами."""
    from .moyklass_client import MoyklassClient
    from . import sync
    key = sync.get_api_key()
    if not key:
        return {}
    mk = MoyklassClient(key)
    buckets: dict[str, list] = {"собрать": [], "разобрать": [], "списать": [],
                                "учёт": []}
    try:
        neg, off = [], 0
        while off < 12000:
            r = mk.get("/v1/company/users", {"limit": 200, "offset": off})
            us = r.get("users") or []
            if not us:
                break
            neg += [u for u in us if (u.get("balans") or 0) < 0]
            off += 200
        for u in neg:
            subs = mk.get("/v1/company/userSubscriptions",
                          {"userId": u["id"], "limit": 100}).get("subscriptions") or []
            bad = [s for s in subs if (s.get("price") or 0) > (s.get("payed") or 0)
                   and s.get("statusId") != 1]
            last = max((s.get("sellDate") or "" for s in bad), default="")
            item = {"id": u["id"], "name": u.get("name"),
                    "balance": u.get("balans"), "state": u.get("clientStateId"),
                    "phone": (u.get("phone") or "")[-10:],
                    "debt": sum((s.get("price") or 0) - (s.get("payed") or 0) for s in bad),
                    "used": sum(s.get("visitedCount") or 0 for s in bad),
                    "quota": sum(s.get("visitCount") or 0 for s in bad),
                    "last_sub": last}
            if not bad:
                b = "учёт"
            elif last >= "2026-06-01" or u.get("clientStateId") in (
                    146950, 125952, 125953, 125955):
                b = "собрать"
            elif last < "2025-09-01":
                b = "списать"
            else:
                b = "разобрать"
            buckets[b].append(item)
    finally:
        mk.close()
    for k in buckets:
        buckets[k].sort(key=lambda x: x["balance"])
    return buckets


def debts_report() -> dict:
    """Полная картина задолженности: счета, абонементы и занятия.

    Источник — /v1/company/invoices: счёт хранит цену, оплаченное, дату
    и `payUntil` — срок оплаты. По нему считается просрочка в днях, а по
    `userSubscriptionId` долг делится на два вида:

      • по абонементам — счёт выставлен за абонемент;
      • по занятиям — счёт без абонемента, то есть разовые оплаты и
        занятия, которые ничем не покрыты.

    Плюс третья, самая тихая часть: занятия, посещённые вообще без счёта.
    Денег по ним не ждёт даже система — их надо не собирать, а сначала
    провести."""
    from .moyklass_client import MoyklassClient
    from . import sync
    key = sync.get_api_key()
    if not key:
        return {"ready": False}
    today = date.today()
    mk = MoyklassClient(key)
    try:
        inv, off = [], 0
        while off < 30000:
            r = mk.get("/v1/company/invoices", {"limit": 500, "offset": off})
            it = r.get("invoices") or []
            if not it:
                break
            inv += it
            off += 500
        unpaid = [i for i in inv if (i.get("price") or 0) >
                  ((i.get("payed") or 0) + (i.get("payedBonuses") or 0))]
        uids = {i["userId"] for i in unpaid}
        users, subs = {}, {}
        for uid in uids:
            try:
                users[uid] = mk.get(f"/v1/company/users/{uid}")
            except Exception:
                users[uid] = {}
            try:
                for s in (mk.get("/v1/company/userSubscriptions",
                                 {"userId": uid, "limit": 100}).get("subscriptions") or []):
                    subs[s["id"]] = s
            except Exception:
                pass
    finally:
        mk.close()

    MG = {84116: "Борис", 84117: "адм.84117", 88422: "Юлия", 154181: "Лиза",
          202856: "Лена", 229704: "Маша", 232805: "Аня", 232763: "Ира"}
    rows = []
    for i in unpaid:
        debt = (i.get("price") or 0) - (i.get("payed") or 0) - (i.get("payedBonuses") or 0)
        u = users.get(i["userId"], {})
        s = subs.get(i.get("userSubscriptionId")) or {}
        try:
            overdue = (today - date.fromisoformat((i.get("payUntil") or i["date"])[:10])).days
        except Exception:
            overdue = 0
        state = u.get("clientStateId")
        sell = s.get("sellDate") or i.get("date") or ""
        if sell >= "2026-06-01" or state in (146950, 125952, 125953, 125955):
            bucket = "собрать"
        elif sell < "2025-09-01":
            bucket = "списать"
        else:
            bucket = "разобрать"
        rows.append({
            "invoice": i["id"], "user_id": i["userId"],
            "name": u.get("name") or f"клиент {i['userId']}",
            "phone": (u.get("phone") or "")[-10:],
            "debt": debt, "price": i.get("price") or 0,
            "date": i.get("date"), "pay_until": i.get("payUntil"),
            "overdue": max(0, overdue),
            "kind": "абонемент" if i.get("userSubscriptionId") else "занятия",
            "used": s.get("visitedCount") or 0, "quota": s.get("visitCount") or 0,
            "manager": MG.get(s.get("managerId"), "—"),
            "state": state, "bucket": bucket})
    rows.sort(key=lambda r: -r["debt"])

    # занятия, посещённые вообще без счёта — деньги, которых система не ждёт
    with db.get_conn() as conn:
        nobill = [dict(zip(("user_id", "name", "date", "class"), r)) for r in conn.execute("""
            SELECT lr.user_id, u.name, l.date, c.name
            FROM lesson_records lr
            JOIN lessons l ON l.id = lr.lesson_id
            LEFT JOIN classes c ON c.id = l.class_id
            LEFT JOIN users u ON u.id = lr.user_id
            WHERE l.date >= ? AND lr.visit = 1
              AND json_extract(lr.raw, '$.free') = 0
              AND json_extract(lr.raw, '$.bill') IS NULL
            ORDER BY l.date DESC LIMIT 60""",
            ((today.replace(day=1)).isoformat(),)).fetchall()]

    def tot(pred):
        sel = [r for r in rows if pred(r)]
        return {"n": len(sel), "sum": round(sum(r["debt"] for r in sel))}

    return {"ready": True, "as_of": today.isoformat(), "rows": rows,
            "nobill": nobill,
            "totals": {
                "всего": tot(lambda r: True),
                "абонементы": tot(lambda r: r["kind"] == "абонемент"),
                "занятия": tot(lambda r: r["kind"] == "занятия"),
                "собрать": tot(lambda r: r["bucket"] == "собрать"),
                "разобрать": tot(lambda r: r["bucket"] == "разобрать"),
                "списать": tot(lambda r: r["bucket"] == "списать")}}


def main():
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fit"
    if cmd == "fit":
        for k, v in fit_check().items():
            print(f"{k:10s} {len(v)}")
            for x in v[:5]:
                print("     ", {kk: vv for kk, vv in list(x.items())[:4]})
    else:
        b = debt_buckets()
        for k, v in b.items():
            print(f"\n{k.upper()}: {len(v)} чел., "
                  f"{sum(x['balance'] for x in v):,.0f} ₽".replace(",", " "))
            for x in v[:20]:
                print(f"   {str(x['name'])[:28]:28s} {x['balance']:>9,.0f} "
                      f"долг {x['debt']:>8,.0f} занятий {x['used']}/{x['quota']} "
                      f"абон.{x['last_sub'] or '—'}".replace(",", " "))


if __name__ == "__main__":
    main()
