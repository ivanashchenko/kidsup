"""Контроль соблюдения правил посещения (docs/pravila_kidsup.html).

Правила, принятые 20.08.2026, живут не в голове администратора, а в данных
МойКласс. Каждое из них имеет отражение в API — и значит проверяемо:

  правило                          где видно в МойКласс
  ─────────────────────────────────────────────────────────────────────
  заморозка 2 недели за год        userSubscription.freezeFrom/freezeTo
  заявление ДО начала заморозки    freezeFrom против даты создания записи
  отработка в течение месяца       lessonRecord.missedLessonRecordId + даты
  при неявке отработка сгорает     повторная отработка того же пропуска
  скидки максимум 10%              price против originalPrice
  скидки не суммируются            discount + extraDiscount одновременно
  перенос 100% только по справке   goodReason + useLeftovers

Что показала первая сверка (учебный год 2025/26, 2622 абонемента):
  · заморозок, оформленных полями freezeFrom — 0. Все 30 живут текстом
    в комментарии, поэтому «две недели за год» посчитать нельзя;
  · 431 отработка, из них 53 закрыты позже месяца (до 92 дней),
    64 — в той же группе, где был пропуск;
  · 124 абонемента проданы дешевле чем −10%, и у 105 из них в комментарии
    нет ни слова о причине (Лена 60, Лиза 36).

Поэтому проверки бьют не по людям, а по двум вещам: правило нарушено ИЛИ
правило применено, но нигде не зафиксировано основание. Второе для нас
важнее — необъяснённая скидка неотличима от увода денег.

Запуск:
    python -m app.rules check          — прогнать все проверки
    python -m app.rules report         — свод по администраторам
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import db
from .audit import HIGH, LOW, MID, MANAGERS, add_flag

log = logging.getLogger("kidsup.rules")

# ── пороги из правил ──────────────────────────────────────────────────────
# Правила приняты 20.08.2026. Задним числом никого не судим: флаги ставим
# только на события с этой даты, а историю показываем отдельной справкой
# (baseline) — она нужна, чтобы понимать масштаб, а не чтобы предъявлять.
RULES_FROM = "2026-08-20"

FREEZE_DAYS_YEAR = 14      # две недели за учебный год
FREEZE_MIN_DAYS = 7        # минимальный срок заморозки — неделя
MAKEUP_DAYS = 31           # отработка в течение месяца
MAX_DISCOUNT = 10.5        # максимум 10% (+0.5 на округление копеек)
COMPENSATION = 50          # компенсация 50% при покупке абонемента подряд
GAP_FOR_STREAK = 7         # «подряд» = разрыв между абонементами не больше недели

# Комментарий должен объяснять причину, а не просто называть месяц.
# «Апрель» — не объяснение. «Абонемент на 3 занятия, заморозка с 13.04» — да.
_REASON = re.compile(
    r"заморо|болел|болезн|справк|стацио|отработк|перенос|праздн|выходн|"
    r"многодет|сво\b|второй\s+(реб|предмет)|компенсац|пересч[её]т|"
    r"старым?\s+цен|прошлого\s+года|маткапитал|мат\.?\s*капитал|сотрудник|"
    r"не\s+попал|наша\s+ошибк|по\s+вине|отмен\w+\s+нами|занятий?\s+\d", re.I)
_FREEZE_WORD = re.compile(r"заморо|заморож", re.I)


def _year_start(d: date) -> date:
    """Учебный год: 1 сентября — 31 августа."""
    return date(d.year if d.month >= 9 else d.year - 1, 9, 1)


def _subs(since: str | None = None) -> list[dict]:
    """Абонементы из локальной копии, распакованные из raw."""
    out = []
    with db.get_conn() as conn:
        if not conn.execute("SELECT COUNT(*) FROM sqlite_master "
                            "WHERE name='user_subscriptions'").fetchone()[0]:
            return []
        for (raw,) in conn.execute("SELECT raw FROM user_subscriptions"):
            try:
                d = json.loads(raw)
            except Exception:
                continue
            if since and (d.get("sellDate") or "") < since:
                continue
            out.append(d)
    return out


def _names() -> dict[int, str]:
    with db.get_conn() as conn:
        return {r[0]: r[1] for r in conn.execute("SELECT id, name FROM users")}


def _who(mid) -> str:
    return MANAGERS.get(mid) or (f"менеджер {mid}" if mid else "без менеджера")


# ── заморозка ─────────────────────────────────────────────────────────────
def freeze_offbook(since: str | None = None) -> list[dict]:
    """Заморозка есть в комментарии, но не оформлена полями freezeFrom/freezeTo.

    Это главная дыра в правиле о двух неделях: пока заморозка живёт текстом,
    её нельзя сложить за год. Администратор пересчитывает цену абонемента
    вручную — и заморозка внешне неотличима от скидки, которую он дал сам.
    """
    since = since or RULES_FROM
    out = []
    for d in _subs(since):
        cm = d.get("comment") or ""
        if not _FREEZE_WORD.search(cm) or d.get("freezeFrom"):
            continue
        out.append({"id": d["id"], "user_id": d.get("userId"),
                    "manager_id": d.get("managerId"), "comment": cm[:120]})
        add_flag("правила", f"freeze-offbook:{d['id']}", MID,
                 "Заморозка не оформлена в системе",
                 f"{_who(d.get('managerId'))} · абонемент {d['id']} от "
                 f"{d.get('sellDate')} · «{cm[:110]}». Заморозку надо ставить "
                 f"полями «заморозить с/по», иначе две недели за год не "
                 f"посчитать и это выглядит как скидка.",
                 user_id=d.get("userId"), manager_id=d.get("managerId"),
                 day=d.get("sellDate"))
    return out


def freeze_overrun(since: str | None = None) -> list[dict]:
    """Суммарная заморозка за учебный год больше двух недель."""
    ys = _year_start(date.today())
    per_user: dict[int, list[tuple]] = defaultdict(list)
    for d in _subs():
        f1, f2 = d.get("freezeFrom"), d.get("freezeTo")
        if not f1 or not f2:
            continue
        try:
            a, b = date.fromisoformat(f1[:10]), date.fromisoformat(f2[:10])
        except ValueError:
            continue
        if b < ys:
            continue
        per_user[d.get("userId")].append((a, b, d))
    out, names = [], _names()
    for uid, spans in per_user.items():
        days = sum((b - a).days + 1 for a, b, _ in spans)
        if days <= FREEZE_DAYS_YEAR:
            continue
        last = max(spans, key=lambda s: s[1])[2]
        out.append({"user_id": uid, "days": days, "spans": len(spans)})
        add_flag("правила", f"freeze-over:{uid}:{ys.year}", MID,
                 f"Заморозка {days} дней за год — норма 14",
                 f"{names.get(uid, uid)} · {len(spans)} заморозок с "
                 f"{ys.isoformat()} на {days} дней. Оформил "
                 f"{_who(last.get('managerId'))}. Сверх нормы — только "
                 f"по справке о стационаре.",
                 user_id=uid, manager_id=last.get("managerId"))
    return out


def freeze_backdated(since: str | None = None) -> list[dict]:
    """Заморозка оформлена задним числом — заявления «до начала» не было."""
    since = since or RULES_FROM
    out, names = [], _names()
    for d in _subs(since):
        f1 = d.get("freezeFrom")
        sell = d.get("sellDate")
        if not f1 or not sell:
            continue
        try:
            start, made = date.fromisoformat(f1[:10]), date.fromisoformat(sell[:10])
        except ValueError:
            continue
        if made <= start:
            continue
        out.append({"id": d["id"], "user_id": d.get("userId"),
                    "late": (made - start).days})
        add_flag("правила", f"freeze-back:{d['id']}", MID,
                 f"Заморозка проведена на {(made - start).days} дн. позже начала",
                 f"{names.get(d.get('userId'), d.get('userId'))} · заморозка "
                 f"с {start}, а оформлена {made}. По правилам заявление "
                 f"пишется ДО начала. Оформил {_who(d.get('managerId'))}.",
                 user_id=d.get("userId"), manager_id=d.get("managerId"),
                 day=sell)
    return out


# ── отработки ─────────────────────────────────────────────────────────────
def _makeups() -> list[dict]:
    """Пары «пропуск → отработка» с датами и группами."""
    with db.get_conn() as conn:
        if not conn.execute("SELECT COUNT(*) FROM sqlite_master "
                            "WHERE name='lesson_records'").fetchone()[0]:
            return []
        les = {r[0]: (r[1], r[2]) for r in
               conn.execute("SELECT id, date, class_id FROM lessons")}
        cls = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM classes")}
        rec = {}
        for rid, lid, uid, visit, raw in conn.execute(
                "SELECT id, lesson_id, user_id, visit, raw FROM lesson_records"):
            try:
                rec[rid] = (lid, uid, visit, json.loads(raw))
            except Exception:
                continue
    out = []
    for rid, (lid, uid, visit, d) in rec.items():
        src_id = d.get("missedLessonRecordId")
        if not src_id:
            continue
        src = rec.get(src_id)
        d_new, c_new = les.get(lid, (None, None))
        d_old, c_old = les.get(src[0], (None, None)) if src else (None, None)
        out.append({"id": rid, "src_id": src_id, "user_id": uid,
                    "visited": bool(visit), "date": d_new, "src_date": d_old,
                    "group": cls.get(c_new), "src_group": cls.get(c_old),
                    "good_reason": bool(d.get("goodReason")),
                    "created": (d.get("createdAt") or "")[:10]})
    return out


def makeup_late(since: str | None = None) -> list[dict]:
    """Отработка позже месяца после пропуска."""
    since = since or RULES_FROM
    out, names = [], _names()
    for m in _makeups():
        if not m["date"] or not m["src_date"] or m["date"] < since:
            continue
        try:
            gap = (date.fromisoformat(m["date"])
                   - date.fromisoformat(m["src_date"])).days
        except ValueError:
            continue
        if gap <= MAKEUP_DAYS:
            continue
        out.append({**m, "gap": gap})
        add_flag("правила", f"makeup-late:{m['id']}", LOW,
                 f"Отработка через {gap} дней — норма 31",
                 f"{names.get(m['user_id'], m['user_id'])} · пропуск "
                 f"{m['src_date']} «{m['src_group']}», отработка {m['date']} "
                 f"«{m['group']}». Просроченная отработка сгорает — если её "
                 f"всё же дали, это решение должно быть согласовано.",
                 user_id=m["user_id"], day=m["date"])
    return out


def makeup_after_noshow(since: str | None = None) -> list[dict]:
    """Второй раз отработали один и тот же пропуск.

    Правило: записались на отработку и не пришли — она сгорает. Значит на
    один missedLessonRecordId может быть ровно одна запись-отработка.
    """
    since = since or RULES_FROM
    by_src: dict[int, list[dict]] = defaultdict(list)
    for m in _makeups():
        by_src[m["src_id"]].append(m)
    out, names = [], _names()
    for src_id, ms in by_src.items():
        if len(ms) < 2:
            continue
        ms.sort(key=lambda x: x["date"] or "")
        if (ms[-1]["date"] or "") < since:
            continue
        out.append({"src_id": src_id, "count": len(ms),
                    "user_id": ms[0]["user_id"]})
        add_flag("правила", f"makeup-repeat:{src_id}", MID,
                 f"Один пропуск отработан {len(ms)} раза",
                 f"{names.get(ms[0]['user_id'], ms[0]['user_id'])} · пропуск "
                 f"{ms[0]['src_date']} закрыт отработками "
                 f"{', '.join(str(x['date']) for x in ms)}. По правилам "
                 f"неявка на отработку её сжигает.",
                 user_id=ms[0]["user_id"])
    return out


def makeup_unbooked(since: str | None = None) -> list[dict]:
    """Отработка отмечена задним числом — предварительной записи не было.

    «Только по предварительной записи» проверяется просто: запись-отработка
    должна появиться в системе ДО дня занятия. Если её завели после — значит
    ребёнок пришёл, а оформили это потом, и правило про сгорание не работает.
    """
    since = since or RULES_FROM
    out, names = [], _names()
    for m in _makeups():
        if not m["date"] or not m["created"] or m["date"] < since:
            continue
        if m["created"] <= m["date"]:
            continue
        out.append(m)
        add_flag("правила", f"makeup-post:{m['id']}", LOW,
                 "Отработка оформлена задним числом",
                 f"{names.get(m['user_id'], m['user_id'])} · занятие "
                 f"{m['date']} «{m['group']}», а запись создана "
                 f"{m['created']}. Отработка бывает только по "
                 f"предварительной записи.",
                 user_id=m["user_id"], day=m["date"])
    return out


# ── деньги: скидки и компенсации ──────────────────────────────────────────
def _pct(d: dict) -> float:
    op, p = d.get("originalPrice") or 0, d.get("price") or 0
    if not op or p >= op:
        return 0.0
    return round(100 * (op - p) / op, 1)


def _streak(uid: int, begin: str, subs_by_user: dict) -> bool:
    """Был ли предыдущий абонемент, закончившийся без перерыва."""
    try:
        b = date.fromisoformat((begin or "")[:10])
    except ValueError:
        return False
    for prev in subs_by_user.get(uid, []):
        end = prev.get("endDate") or ""
        try:
            e = date.fromisoformat(end[:10])
        except ValueError:
            continue
        if 0 <= (b - e).days <= GAP_FOR_STREAK:
            return True
    return False


def discount_unexplained(since: str | None = None) -> list[dict]:
    """Скидка больше 10% без причины в комментарии.

    Не каждая большая скидка — нарушение: пересчёт за неполный месяц,
    заморозка, компенсация 50% дают законные −20…−50%. Но основание обязано
    быть написано. Лена пишет его всегда, и её абонементы читаются; там, где
    в комментарии стоит только «Апрель», отличить пересчёт от подарка нельзя.
    """
    since = since or RULES_FROM
    out, names = [], _names()
    for d in _subs(since):
        pct = _pct(d)
        if pct <= MAX_DISCOUNT:
            continue
        cm = d.get("comment") or ""
        if _REASON.search(cm):
            continue
        level = HIGH if pct >= 30 else MID
        out.append({"id": d["id"], "user_id": d.get("userId"), "pct": pct,
                    "manager_id": d.get("managerId"), "comment": cm[:80]})
        add_flag("правила", f"disc-noreason:{d['id']}", level,
                 f"Скидка {pct:.0f}% без объяснения",
                 f"{names.get(d.get('userId'), d.get('userId'))} · "
                 f"{d.get('price'):,.0f} ₽ вместо {d.get('originalPrice'):,.0f} ₽ "
                 f"({d.get('sellDate')}) · оформил {_who(d.get('managerId'))} · "
                 f"комментарий: «{cm[:60] or 'пусто'}». Скидка выше 10% "
                 f"допустима только как пересчёт или компенсация — и причина "
                 f"должна быть написана в абонементе."
                 .replace(",", " "),
                 user_id=d.get("userId"), manager_id=d.get("managerId"),
                 day=d.get("sellDate"))
    return out


def discount_stacked(since: str | None = None) -> list[dict]:
    """Две скидки на одном абонементе — правило говорит «не суммируются»."""
    since = since or RULES_FROM
    out, names = [], _names()
    for d in _subs(since):
        if not (d.get("discount") and d.get("extraDiscount")):
            continue
        out.append({"id": d["id"], "user_id": d.get("userId")})
        add_flag("правила", f"disc-stack:{d['id']}", MID,
                 "Две скидки на одном абонементе",
                 f"{names.get(d.get('userId'), d.get('userId'))} · "
                 f"{d.get('discount')} + {d.get('extraDiscount')} "
                 f"({d.get('sellDate')}) · {_who(d.get('managerId'))}. "
                 f"Скидки не суммируются — действует одна, наибольшая.",
                 user_id=d.get("userId"), manager_id=d.get("managerId"),
                 day=d.get("sellDate"))
    return out


def compensation_without_streak(since: str | None = None) -> list[dict]:
    """Компенсация ~50% дана, но следующий абонемент куплен не подряд.

    Правило: 50% за пропущенные занятия начисляются только если следующий
    абонемент куплен подряд, без перерыва. Если перерыв был — компенсации
    быть не должно, пропуски сгорают.
    """
    since = since or RULES_FROM
    by_user: dict[int, list[dict]] = defaultdict(list)
    for d in _subs():
        by_user[d.get("userId")].append(d)
    out, names = [], _names()
    for d in _subs(since):
        pct = _pct(d)
        if not (COMPENSATION - 8 <= pct <= COMPENSATION + 8):
            continue
        cm = d.get("comment") or ""
        if not re.search(r"компенсац|50\s*%|пропуск", cm, re.I):
            continue
        peers = [p for p in by_user.get(d.get("userId"), []) if p["id"] != d["id"]]
        if _streak(d.get("userId"), d.get("beginDate"), {d.get("userId"): peers}):
            continue
        out.append({"id": d["id"], "user_id": d.get("userId"), "pct": pct})
        add_flag("правила", f"comp-nostreak:{d['id']}", MID,
                 f"Компенсация {pct:.0f}% без непрерывности",
                 f"{names.get(d.get('userId'), d.get('userId'))} · "
                 f"{d.get('sellDate')} · {_who(d.get('managerId'))}. "
                 f"Предыдущего абонемента, закончившегося без перерыва, "
                 f"не нашлось — компенсация 50% полагается только при "
                 f"покупке подряд.",
                 user_id=d.get("userId"), manager_id=d.get("managerId"),
                 day=d.get("sellDate"))
    return out


# ── сводка по администраторам ─────────────────────────────────────────────
# ── деньги: абонементы и посещаемость ─────────────────────────────────────
# Как в МойКласс вообще появляется долг — это не очевидно, и до 21.08 мы это
# не проверяли. Механика такая:
#
#   1. Администратор заводит абонемент. Он может создать его, не проведя
#      оплату — карточка выглядит нормально, ребёнок ходит.
#   2. На каждом занятии отметка посещения списывает одно занятие с этого
#      абонемента и ставит записи paid=true. «Оплачено» здесь значит
#      «списано с абонемента», а НЕ «деньги получены».
#   3. Если абонемент не оплачен, каждое списание уводит баланс клиента
#      в минус. Никакой ошибки на экране не появляется.
#
# Поэтому долг всегда рождается на шаге 1 и растёт молча. Ловить его надо
# по абонементам, а не по занятиям: посещений с paid=false у нас ноль,
# и искать дыру там бессмысленно.

def unpaid_subscriptions(since: str | None = None, min_debt: float = 1000) -> list[dict]:
    """Абонемент выдан и расходуется, а деньги за него не пришли."""
    since = since or _year_start(date.today()).isoformat()
    out, names = [], _names()
    for d in _subs(since):
        price, payed = d.get("price") or 0, d.get("payed") or 0
        debt = price - payed
        if price <= 0 or debt < min_debt:
            continue
        used = d.get("visitedCount") or 0
        if d.get("statusId") == 1:            # отменённый абонемент — не долг
            continue
        level = HIGH if (debt >= 10000 or used >= 3) else MID
        out.append({"id": d["id"], "user_id": d.get("userId"), "debt": debt,
                    "used": used, "manager_id": d.get("managerId")})
        add_flag("деньги", f"sub-unpaid:{d['id']}", level,
                 f"Абонемент не оплачен: долг {debt:,.0f} ₽".replace(",", " "),
                 f"{names.get(d.get('userId'), d.get('userId'))} · продан "
                 f"{d.get('sellDate')} за {price:,.0f} ₽, оплачено {payed:,.0f} ₽, "
                 f"уже использовано занятий: {used}. Оформил "
                 f"{_who(d.get('managerId'))}. Каждое следующее занятие "
                 f"увеличивает долг — либо берём оплату, либо ставим "
                 f"абонемент на паузу.".replace(",", " "),
                 user_id=d.get("userId"), manager_id=d.get("managerId"),
                 day=d.get("sellDate"))
    return out


def negative_balance(threshold: float = -3000) -> list[dict]:
    """Клиенты, ушедшие в минус. Итог всех незакрытых абонементов."""
    out = []
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, balance FROM users WHERE balance < ? ORDER BY balance",
            (threshold,)).fetchall()
        for r in rows:
            out.append({"id": r[0], "name": r[1], "balance": r[2]})
            add_flag("деньги", f"neg-balance:{r[0]}:{date.today():%Y-%m}",
                     HIGH if r[2] <= -20000 else MID,
                     f"Долг клиента {abs(r[2]):,.0f} ₽".replace(",", " "),
                     f"{r[1]} · баланс {r[2]:,.0f} ₽. Разобрать: это "
                     f"неоплаченный абонемент, ошибка проведения или "
                     f"реальная задолженность семьи.".replace(",", " "),
                     user_id=r[0])
    return out


def free_marks(days: int = 30) -> list[dict]:
    """Занятия, отмеченные бесплатными вне пробных.

    Отметка «бесплатно» не списывает занятие с абонемента и не создаёт счёт —
    то есть это подарок за счёт центра. Для первого условно-бесплатного
    занятия так и надо. Для всех остальных случаев должно быть основание."""
    since = (date.today() - timedelta(days=days)).isoformat()
    out = []
    with db.get_conn() as conn:
        if not conn.execute("SELECT COUNT(*) FROM sqlite_master "
                            "WHERE name='lesson_records'").fetchone()[0]:
            return []
        rows = conn.execute("""
            SELECT lr.user_id, u.name, l.date, c.name cls, COUNT(*) n
            FROM lesson_records lr
            JOIN lessons l ON l.id = lr.lesson_id
            LEFT JOIN classes c ON c.id = l.class_id
            LEFT JOIN users u ON u.id = lr.user_id
            WHERE lr.visit = 1 AND l.date >= ?
              AND json_extract(lr.raw, '$.free') = 1
            GROUP BY lr.user_id HAVING n >= 2
            ORDER BY n DESC""", (since,)).fetchall()
        for r in rows:
            out.append({"user_id": r[0], "name": r[1], "count": r[4]})
            add_flag("деньги", f"free-marks:{r[0]}:{since}", MID,
                     f"{r[4]} бесплатных занятий за {days} дней",
                     f"{r[1] or r[0]} · «{(r[3] or '')[:40]}». Бесплатным бывает "
                     f"только первое условно-бесплатное занятие. Дальше — либо "
                     f"абонемент, либо отработка, либо письменное основание.",
                     user_id=r[0])
    return out


def absences_not_billed(days: int = 30) -> list[dict]:
    """Пропуски, не списанные с абонемента и без уважительной причины.

    По правилам пропуск либо отрабатывается, либо сгорает. Пропуск, который
    просто не списали, — это занятие, подаренное задним числом."""
    since = (date.today() - timedelta(days=days)).isoformat()
    out = []
    with db.get_conn() as conn:
        if not conn.execute("SELECT COUNT(*) FROM sqlite_master "
                            "WHERE name='lesson_records'").fetchone()[0]:
            return []
        rows = conn.execute("""
            SELECT lr.user_id, u.name, COUNT(*) n
            FROM lesson_records lr
            JOIN lessons l ON l.id = lr.lesson_id
            LEFT JOIN users u ON u.id = lr.user_id
            WHERE l.date >= ? AND lr.visit = 0
              AND json_extract(lr.raw, '$.paid') = 0
              AND json_extract(lr.raw, '$.free') = 0
              AND json_extract(lr.raw, '$.goodReason') = 0
              AND json_extract(lr.raw, '$.missedLessonRecordId') IS NULL
            GROUP BY lr.user_id HAVING n >= 3
            ORDER BY n DESC LIMIT 40""", (since,)).fetchall()
        for r in rows:
            out.append({"user_id": r[0], "name": r[1], "count": r[2]})
            add_flag("деньги", f"absence-free:{r[0]}:{since}", MID,
                     f"{r[2]} пропусков не списаны с абонемента",
                     f"{r[1] or r[0]} · пропуски без отработки, без справки "
                     f"и без списания. Либо оформить отработку, либо списать: "
                     f"сейчас это занятия за счёт центра.",
                     user_id=r[0])
    return out


def visits_without_bill(days: int = 30) -> list[dict]:
    """Ребёнок был на занятии, а счёта нет — занятие не привязано ни к
    абонементу, ни к разовой оплате. Чаще всего это забытый абонемент."""
    since = (date.today() - timedelta(days=days)).isoformat()
    out = []
    with db.get_conn() as conn:
        if not conn.execute("SELECT COUNT(*) FROM sqlite_master "
                            "WHERE name='lesson_records'").fetchone()[0]:
            return []
        rows = conn.execute("""
            SELECT lr.user_id, u.name, l.date, c.name cls
            FROM lesson_records lr
            JOIN lessons l ON l.id = lr.lesson_id
            LEFT JOIN classes c ON c.id = l.class_id
            LEFT JOIN users u ON u.id = lr.user_id
            WHERE l.date >= ? AND lr.visit = 1
              AND json_extract(lr.raw, '$.free') = 0
              AND json_extract(lr.raw, '$.bill') IS NULL
            ORDER BY l.date DESC LIMIT 40""", (since,)).fetchall()
        for r in rows:
            out.append({"user_id": r[0], "name": r[1], "date": r[2]})
            add_flag("деньги", f"visit-nobill:{r[0]}:{r[2]}", MID,
                     "Занятие посещено, но не оплачено ничем",
                     f"{r[1] or r[0]} · {r[2]} «{(r[3] or '')[:40]}». Ни "
                     f"абонемента, ни разовой оплаты. Проверить: либо провести "
                     f"оплату, либо привязать к абонементу.",
                     user_id=r[0], day=r[2])
    return out


def money_check() -> dict:
    """Прогон денежных проверок: абонементы, балансы, отметки посещений."""
    res = {"unpaid_subs": unpaid_subscriptions(),
           "negative_balance": negative_balance(),
           "free_marks": free_marks(),
           "absences_not_billed": absences_not_billed(),
           "visits_without_bill": visits_without_bill()}
    log.info("деньги: %s", ", ".join(f"{k}={len(v)}" for k, v in res.items()))
    return res


def check(since: str | None = None) -> dict:
    """Один прогон всех проверок правил."""
    res = {
        "freeze_offbook": freeze_offbook(since),
        "freeze_overrun": freeze_overrun(since),
        "freeze_backdated": freeze_backdated(since),
        "makeup_late": makeup_late(),
        "makeup_repeat": makeup_after_noshow(),
        "makeup_unbooked": makeup_unbooked(),
        "discount_unexplained": discount_unexplained(since),
        "discount_stacked": discount_stacked(since),
        "compensation": compensation_without_streak(since),
    }
    log.info("правила: %s", ", ".join(f"{k}={len(v)}" for k, v in res.items()))
    return res


def baseline(since: str = "2025-09-01") -> dict:
    """Как жил центр ДО правил — считаем, но никого не флагаем.

    Нужно, чтобы понимать масштаб привычки, а не чтобы предъявлять: до
    20.08.2026 этих правил просто не было, и администратор, давший скидку
    без комментария в октябре, не нарушал ничего.
    """
    subs = _subs(since)
    big = [d for d in subs if _pct(d) > MAX_DISCOUNT]
    mute = [d for d in big if not _REASON.search(d.get("comment") or "")]
    frz_text = [d for d in subs if _FREEZE_WORD.search(d.get("comment") or "")]
    mk = _makeups()
    late = same = 0
    for m in mk:
        if m["date"] and m["src_date"]:
            try:
                if (date.fromisoformat(m["date"])
                        - date.fromisoformat(m["src_date"])).days > MAKEUP_DAYS:
                    late += 1
            except ValueError:
                pass
        if m["group"] and m["group"] == m["src_group"]:
            same += 1
    by_mgr: dict[str, int] = defaultdict(int)
    for d in mute:
        by_mgr[_who(d.get("managerId"))] += 1
    return {"since": since, "subs": len(subs),
            "discount_big": len(big), "discount_mute": len(mute),
            "discount_mute_by_manager": dict(sorted(
                by_mgr.items(), key=lambda x: -x[1])),
            "freeze_in_text": len(frz_text),
            "freeze_in_system": sum(1 for d in subs if d.get("freezeFrom")),
            "makeups": len(mk), "makeup_late": late, "makeup_same_course": same}


def by_manager(days: int = 30) -> list[dict]:
    """Кто сколько нарушений набрал — для утренней планёрки и разбора."""
    since = (date.today() - timedelta(days=days)).isoformat()
    rows: dict[int, dict] = {}
    with db.get_conn() as conn:
        if not conn.execute("SELECT COUNT(*) FROM sqlite_master "
                            "WHERE name='audit_flags'").fetchone()[0]:
            return []
        for r in conn.execute(
                "SELECT manager_id, level, COUNT(*) c FROM audit_flags "
                "WHERE kind='правила' AND created_at >= ? "
                "GROUP BY manager_id, level", (since,)):
            m = rows.setdefault(r[0], {"manager_id": r[0], "name": _who(r[0]),
                                       "high": 0, "mid": 0, "low": 0})
            m[r[1]] = r[2]
    out = sorted(rows.values(), key=lambda x: -(x["high"] * 3 + x["mid"]))
    return out


def open_flags(limit: int = 100) -> list[dict]:
    with db.get_conn() as conn:
        if not conn.execute("SELECT COUNT(*) FROM sqlite_master "
                            "WHERE name='audit_flags'").fetchone()[0]:
            return []
        return [dict(r) for r in conn.execute(
            "SELECT * FROM audit_flags WHERE kind='правила' AND status='new' "
            "ORDER BY CASE level WHEN 'high' THEN 0 WHEN 'mid' THEN 1 ELSE 2 END, "
            "id DESC LIMIT ?", (limit,))]


def summary() -> dict:
    flags = open_flags(1000)
    by_kind: dict[str, int] = defaultdict(int)
    for f in flags:
        by_kind[(f["key"] or "").split(":")[0]] += 1
    return {"open": len(flags),
            "high": sum(1 for f in flags if f["level"] == HIGH),
            "mid": sum(1 for f in flags if f["level"] == MID),
            "low": sum(1 for f in flags if f["level"] == LOW),
            "by_rule": dict(by_kind),
            "managers": by_manager()}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Контроль правил KidsUP")
    ap.add_argument("command", choices=["check", "report", "baseline"])
    ap.add_argument("--since")
    args = ap.parse_args()
    if args.command == "check":
        r = check(args.since)
        for k, v in r.items():
            print(f"{k:24s} {len(v)}")
    elif args.command == "baseline":
        print(json.dumps(baseline(args.since or "2025-09-01"),
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps(summary(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
