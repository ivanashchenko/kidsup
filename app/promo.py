"""Эффективность промоутеров: воронка по тегам «Промо: …» и общий канал.

Считает по локальной синхронизированной базе (users.raw содержит tags,
clientStateId, advSourceId, joins, createdAt) + payments.
"""
import json
from collections import defaultdict
from datetime import date, timedelta

from . import db
from .promo_registry import REGISTRY, promoter_for

# Статусы воронки набора
ST_NEW = 347075        # 1.1. От промоутера
ST_NEDOZVON = 345768   # 2. Недозвон (в работе)
ST_THINKS = 146950     # 3. Думает
ST_BOOKED = 125952     # 4. Записался на пробное
ST_VISITED = 125953    # 5. Посетил пробное
ST_THINKS2 = 345767    # 6. Думает после пробного
ST_REFUSED = 125957    # Отказ
ADV_PROMO = 376242     # источник «Промоутер 26/27»
ADV_OTHER = 158834     # «Иное» (куда сейчас ошибочно пишут)

PROMO_TAG_PREFIX = "Промо:"
SEASON_START = "2026-08-01"

# Оплата промоутеров (правила Бориса, 17.08 вечер):
#   600 ₽ — ТОЛЬКО за подтверждённый новый контакт (дозвонились, контакт
#            настоящий и готов записаться: записался/пришёл/оплатил);
#   200 ₽ — контакт уже был в базе;
#   400 ₽/час — за часы работы;
#   фейк или «не готов» — 0 ₽.
# Руслан и Виталий работают вместе — деньги считаются на двоих одним счётом.
RATE_NEW, RATE_OLD, RATE_HOUR = 600, 200, 400
PROMO_HOURS = {
    "Промо: Руслан": 2,
    "Промо: Виталий": 2,
    "Промо: Маша": 4,
    "Промо: Алиса": 0,      # первый выход 19.08 — часы проставить по факту
}
PAY_GROUPS = [
    {"label": "Руслан и Виталий (работают вместе)",
     "tags": ["Промо: Руслан", "Промо: Виталий"], "paid": 23_200},
    {"label": "Маша", "tags": ["Промо: Маша"], "paid": 3_400},
    {"label": "Алиса", "tags": ["Промо: Алиса"], "paid": 0},
]

# Статусы воронки набора: свежая карточка с источником «Иное» в одном из них —
# почти наверняка промо-лид, которому админ уже сменил статус (ловим их тоже,
# чтобы они попали в список «без тега» на разметку).
NABOR_STATES = {125951, ST_NEDOZVON, ST_THINKS, ST_BOOKED, ST_VISITED,
                ST_THINKS2, ST_REFUSED}


def _promo_users():
    """Все карточки промо-канала сезона: тег «Промо: …» ИЛИ статус «От
    промоутера» ИЛИ источник «Промоутер 26/27», созданные с 1 августа."""
    out = []
    old_clients = defaultdict(int)  # тег -> сколько старых клиентов принёс
    old_phones: set[str] = set()    # их телефоны — чтобы не считать дважды
    with db.get_conn() as conn:
        for (raw,) in conn.execute("SELECT raw FROM users"):
            try:
                u = json.loads(raw)
            except Exception:
                continue
            created = (u.get("createdAt") or "")[:10]
            # Реестр бумажных листов — источник истины: теги в МойКласс
            # периодически слетают при правках карточки в интерфейсе.
            reg = promoter_for(u.get("phone"))
            if reg:
                promo_tags = [reg]
            else:
                tags = [t.get("name", "") for t in (u.get("tags") or [])]
                promo_tags = [t for t in tags if t.startswith(PROMO_TAG_PREFIX)]
            if created < SEASON_START:
                # контакт промоутера, который уже был нашим клиентом
                for t in promo_tags:
                    old_clients[t] += 1
                    old_phones.add((u.get("phone") or "")[-10:])
                continue
            st = u.get("clientStateId")
            # реестр листов полный, поэтому критерий строгий: тег/реестр,
            # статус «От промоутера» или источник «Промоутер 26/27».
            # Широкую эвристику («Иное» + статус воронки) убрали 17.08 —
            # она затягивала лиды рассылок и сайта в промо-статистику.
            is_promo = (promo_tags
                        or st == ST_NEW
                        or u.get("advSourceId") == ADV_PROMO)
            # промо без листа и без тега — в «не распределено» до появления листа
            if is_promo and not promo_tags:
                promo_tags = ["Промо: не распределено"]
            if not is_promo:
                continue
            u["_promo_tags"] = promo_tags
            u["_created"] = created
            out.append(u)
    # дедуп: один телефон = один контакт (админы иногда заводят карточку дважды)
    best: dict[str, dict] = {}
    no_phone = []
    for u in out:
        ph = (u.get("phone") or "")[-10:]
        if ph and ph in old_phones:
            continue  # такой же телефон уже посчитан как «был в базе»
        if not ph:
            no_phone.append(u)
            continue
        prev = best.get(ph)
        if not prev:
            best[ph] = u
            continue
        # оставляем карточку с более «продвинутым» статусом, при равенстве — раннюю
        rank = {ST_NEW: 0, ST_NEDOZVON: 1, ST_THINKS: 2, ST_THINKS2: 2,
                ST_BOOKED: 3, ST_VISITED: 4, ST_REFUSED: 1}
        if (rank.get(u.get("clientStateId"), 1), prev["_created"]) > \
           (rank.get(prev.get("clientStateId"), 1), u["_created"]):
            best[ph] = u
    return list(best.values()) + no_phone, old_clients


def _funnel(users, paid_ids):
    f = {"total": 0, "new": 0, "nedozvon": 0, "thinks": 0, "booked": 0,
         "visited": 0, "paid": 0, "refused": 0}
    for u in users:
        f["total"] += 1
        st = u.get("clientStateId")
        if u["id"] in paid_ids:
            f["paid"] += 1
        elif st == ST_NEW:
            f["new"] += 1
        elif st == ST_NEDOZVON:
            f["nedozvon"] += 1
        elif st in (ST_THINKS, ST_THINKS2):
            f["thinks"] += 1
        elif st == ST_BOOKED:
            f["booked"] += 1
        elif st == ST_VISITED:
            f["visited"] += 1
        elif st == ST_REFUSED:
            f["refused"] += 1
        else:
            f["thinks"] += 1  # прочие живые статусы считаем «в работе»
    reached = f["booked"] + f["visited"] + f["paid"]
    f["reach_pct"] = round(100 * reached / f["total"], 1) if f["total"] else 0.0
    return f


def stats():
    users, old_clients = _promo_users()
    ids = [u["id"] for u in users]
    paid_ids = set()
    if ids:
        with db.get_conn() as conn:
            q = ",".join("?" * len(ids))
            for (uid,) in conn.execute(
                    f"SELECT DISTINCT user_id FROM payments WHERE user_id IN ({q}) "
                    "AND optype='income' AND date >= ?", ids + [SEASON_START]):
                paid_ids.add(uid)

    by_tag = defaultdict(list)
    untagged = []
    for u in users:
        if u["_promo_tags"]:
            for t in u["_promo_tags"]:
                by_tag[t].append(u)
        else:
            untagged.append(u)

    # переписка: кому писали и кто ответил (по последним 10 цифрам телефона)
    with db.get_conn() as conn:
        try:
            out_phones = {p for (p,) in conn.execute(
                "SELECT DISTINCT substr(phone,-10) FROM wazzup_outbox")}
            in_phones = {p for (p,) in conn.execute(
                "SELECT DISTINCT substr(phone,-10) FROM wazzup_inbox")}
        except Exception:
            out_phones, in_phones = set(), set()

    promoters = []
    for tag, us in sorted(by_tag.items()):
        f = _funnel(us, paid_ids)
        old = old_clients.get(tag, 0)
        confirmed = f["booked"] + f["visited"] + f["paid"]     # 600 ₽ за каждого
        pending = f["new"] + f["nedozvon"] + f["thinks"]       # ещё могут подтвердиться
        reached = f["thinks"] + confirmed + f["refused"]
        written = sum(1 for u in us if (u.get("phone") or "")[-10:] in out_phones)
        replied = sum(1 for u in us if (u.get("phone") or "")[-10:] in in_phones)
        sheet_total = sum(1 for t in REGISTRY.values() if t == tag)
        in_base = f["total"] + old
        promoters.append({
            "name": tag, "funnel": f, "old_clients": old,
            "confirmed": confirmed, "pending": pending, "zero": f["refused"],
            "called": f["total"] - f["new"], "reached": reached,
            "written": written, "replied": replied,
            "sheet_total": sheet_total, "in_base": in_base,
            "missing": max(0, sheet_total - in_base),
        })

    # денежные группы (Руслан и Виталий — общий счёт на двоих)
    pmap = {p["name"]: p for p in promoters}
    pay_groups = []
    for g in PAY_GROUPS:
        ps = [pmap[t] for t in g["tags"] if t in pmap]
        confirmed = sum(p["confirmed"] for p in ps)
        old = sum(p["old_clients"] for p in ps)
        pending = sum(p["pending"] for p in ps)
        hours = sum(PROMO_HOURS.get(t, 0) for t in g["tags"])
        accrued = confirmed * RATE_NEW + old * RATE_OLD + hours * RATE_HOUR
        due = accrued - g["paid"]
        pay_groups.append({
            "label": g["label"], "confirmed": confirmed, "old": old,
            "pending": pending, "hours": hours,
            "accrued": accrued, "paid": g["paid"], "due": due,
            "need_confirm": (-(due // RATE_NEW)) if due < 0 else 0,
            "detail": f"{confirmed} подтв.×{RATE_NEW} + {old} в базе×{RATE_OLD} + {hours} ч×{RATE_HOUR}",
        })
    total_fee = sum(g["accrued"] for g in pay_groups)

    # по дням (последние 14) — сколько контактов собрано
    days = []
    today = date.today()
    per_day = defaultdict(lambda: defaultdict(int))
    for u in users:
        per_day[u["_created"]][u["_promo_tags"][0] if u["_promo_tags"] else "без тега"] += 1
    for i in range(13, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        days.append({"date": d, "counts": dict(per_day.get(d, {})),
                     "total": sum(per_day.get(d, {}).values())})

    bad_source = sum(1 for u in users if u.get("advSourceId") != ADV_PROMO)
    return {
        "total_fee": total_fee,
        "pay_groups": pay_groups,
        "rates": {"new": RATE_NEW, "old": RATE_OLD, "hour": RATE_HOUR},
        "promoters": promoters,
        "untagged": _funnel(untagged, paid_ids),
        "untagged_list": [{"id": u["id"], "name": u.get("name") or "",
                           "phone": u.get("phone") or "", "created": u["_created"]}
                          for u in sorted(untagged, key=lambda x: x["_created"], reverse=True)[:120]],
        "total": _funnel(users, paid_ids),
        "days": days,
        "bad_source": bad_source,
    }
