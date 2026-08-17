"""Эффективность промоутеров: воронка по тегам «Промо: …» и общий канал.

Считает по локальной синхронизированной базе (users.raw содержит tags,
clientStateId, advSourceId, joins, createdAt) + payments.
"""
import json
from collections import defaultdict
from datetime import date, timedelta

from . import db

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

# Статусы воронки набора: свежая карточка с источником «Иное» в одном из них —
# почти наверняка промо-лид, которому админ уже сменил статус (ловим их тоже,
# чтобы они попали в список «без тега» на разметку).
NABOR_STATES = {125951, ST_NEDOZVON, ST_THINKS, ST_BOOKED, ST_VISITED,
                ST_THINKS2, ST_REFUSED}


def _promo_users():
    """Все карточки промо-канала сезона: тег «Промо: …» ИЛИ статус «От
    промоутера» ИЛИ источник «Промоутер 26/27», созданные с 1 августа."""
    out = []
    with db.get_conn() as conn:
        for (raw,) in conn.execute("SELECT raw FROM users"):
            try:
                u = json.loads(raw)
            except Exception:
                continue
            created = (u.get("createdAt") or "")[:10]
            if created < SEASON_START:
                continue
            tags = [t.get("name", "") for t in (u.get("tags") or [])]
            promo_tags = [t for t in tags if t.startswith(PROMO_TAG_PREFIX)]
            st = u.get("clientStateId")
            is_promo = (promo_tags
                        or st == ST_NEW
                        or u.get("advSourceId") == ADV_PROMO
                        or (u.get("advSourceId") == ADV_OTHER
                            and st in NABOR_STATES))
            if not is_promo:
                continue
            u["_promo_tags"] = promo_tags
            u["_created"] = created
            out.append(u)
    return out


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
    users = _promo_users()
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

    promoters = [{"name": tag, "funnel": _funnel(us, paid_ids)}
                 for tag, us in sorted(by_tag.items())]

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
        "promoters": promoters,
        "untagged": _funnel(untagged, paid_ids),
        "untagged_list": [{"id": u["id"], "name": u.get("name") or "",
                           "phone": u.get("phone") or "", "created": u["_created"]}
                          for u in sorted(untagged, key=lambda x: x["_created"], reverse=True)[:120]],
        "total": _funnel(users, paid_ids),
        "days": days,
        "bad_source": bad_source,
    }
