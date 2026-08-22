"""Бонусы администраторам за записи: сколько заработано и сколько на подходе.

Схема, утверждённая владельцем 22.08 и действующая до 15 октября:
  · ребёнок пришёл на пробное — 300 ₽
  · купил абонемент в день пробного — ещё 300 ₽
  · купил позже, в течение двух недель после пробного — 200 ₽

Кому начисляем. Тому, кто ЗАПИСАЛ ребёнка на пробное. Определяем
по автору записи в CRM: в объекте записи есть managerId, и это
единственная надёжная привязка — автор комментария или звонка
ей не равен.

Зачем считать «на подходе». Заработанное видно и так, а вот сумма,
которая придёт, если все записанные дойдут, — это то, ради чего
администратор работает сегодня. Она показывает цену не дошедшего
ребёнка: каждый, кого не подтвердили накануне, стоит конкретных
рублей, и это понятнее любого разговора о конверсии.

Запуск:
    python -m app.bonus            — сводка по всем
    python -m app.bonus who 232763 — детально по одному
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from . import sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.bonus")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"

STAFF = {232763: "Ира", 232805: "Аня", 202856: "Лена", 154181: "Лиза"}
PROGRAM_FROM = "2026-08-01"   # с начала августа: набор шёл весь месяц
PROGRAM_UNTIL = "2026-10-15"

CAME = 300          # пришёл на пробное
BOUGHT_SAME_DAY = 300   # купил в день пробного
BOUGHT_LATER = 200      # купил в течение двух недель
LATER_DAYS = 14

# Бонус платится за ПРОБНОЕ, поэтому считаем только записи на пробное
# и их исходы. Статус «учится» сюда не входит: под него попадают все
# действующие ученики, и тогда в лидеры выходит тот, кто просто числится
# автором старых записей.
BOOKED = {58132}          # записан на пробное — ещё не дошёл
VISITED = {58131, 83760}  # посетил пробное / подтвердил


def collect() -> dict:
    mk = MoyklassClient(sync.get_api_key())
    try:
        joins = taskguard.pull_all(mk, "/v1/company/joins", "joins")
        pays = taskguard.pull_all(mk, "/v1/company/payments", "payments")
    finally:
        mk.close()

    paid_days = defaultdict(list)
    for p in pays:
        if (p.get("optype") or "") != "income" or not p.get("userId"):
            continue
        # Поле суммы в оплатах называется summa, а не price: из-за этого
        # первая версия отсекала все оплаты и показывала нули.
        if (p.get("summa") or 0) <= 0:
            continue
        d = (p.get("date") or p.get("createdAt") or "")[:10]
        if d:
            paid_days[p["userId"]].append(d)

    rows = []
    for j in joins:
        mid = j.get("managerId")
        uid = j.get("userId")
        if mid not in STAFF or not uid:
            continue
        made = (j.get("createdAt") or "")[:10]
        if not made or not (PROGRAM_FROM <= made <= PROGRAM_UNTIL):
            continue
        st = j.get("statusId")
        if st not in BOOKED | VISITED:
            continue
        came = st in VISITED
        # День пробного точно не известен: берём день записи как опорный.
        # Для бонуса важна не дата урока, а факт оплаты рядом с ним.
        same_day = any(d == made for d in paid_days.get(uid, []))
        edge = (date.fromisoformat(made) + timedelta(days=LATER_DAYS)).isoformat()
        later = any(made < d <= edge for d in paid_days.get(uid, []))
        rows.append({"manager": mid, "who": STAFF[mid], "uid": uid,
                     "made": made, "came": came,
                     "paid_same_day": same_day, "paid_later": later and not same_day})
    json.dump(rows, open(f"{SP}/bonus.json", "w"), ensure_ascii=False)
    return {"rows": rows}


def summary(rows: list[dict] | None = None) -> dict:
    if rows is None:
        rows = json.load(open(f"{SP}/bonus.json"))
    out = {}
    for mid, name in STAFF.items():
        mine = [r for r in rows if r["manager"] == mid]
        if not mine:
            continue
        came = [r for r in mine if r["came"]]
        same = [r for r in mine if r["paid_same_day"]]
        later = [r for r in mine if r["paid_later"]]
        waiting = [r for r in mine if not r["came"]]
        earned = len(came) * CAME + len(same) * BOUGHT_SAME_DAY \
            + len(later) * BOUGHT_LATER
        # Если дойдут все записанные и купят в день пробного — верхняя оценка.
        potential = earned + len(waiting) * (CAME + BOUGHT_SAME_DAY)
        out[name] = {
            "записал на пробное": len(mine),
            "дошли": len(came),
            "купили в день пробного": len(same),
            "купили позже": len(later),
            "ещё не дошли": len(waiting),
            "заработано": earned,
            "на подходе": potential - earned,
            "потенциал всего": potential,
        }
    return out


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    data = collect()
    s = summary(data["rows"])
    print(f"Бонусная программа до {PROGRAM_UNTIL}: "
          f"пробное +{CAME} ₽, оплата в день пробного +{BOUGHT_SAME_DAY} ₽, "
          f"оплата в течение {LATER_DAYS} дней +{BOUGHT_LATER} ₽\n")
    print(f"  {'кто':6s} {'записал':>8} {'дошли':>7} {'в день':>7} {'позже':>7} "
          f"{'ждём':>6} {'заработано':>12} {'на подходе':>12}")
    for who, v in sorted(s.items(), key=lambda x: -x[1]["заработано"]):
        print(f"  {who:6s} {v['записал на пробное']:8d} {v['дошли']:7d} "
              f"{v['купили в день пробного']:7d} {v['купили позже']:7d} "
              f"{v['ещё не дошли']:6d} {str(v['заработано']) + ' ₽':>12} "
              f"{str(v['на подходе']) + ' ₽':>12}")
    if len(sys.argv) > 2 and sys.argv[1] == "who":
        mid = int(sys.argv[2])
        print(f"\nдетально по {STAFF.get(mid, mid)}:")
        for r in data["rows"]:
            if r["manager"] == mid:
                mark = "дошёл" if r["came"] else "ждём"
                pay = "оплата в день" if r["paid_same_day"] else \
                    ("оплата позже" if r["paid_later"] else "без оплаты")
                print(f"   {r['made']} uid={r['uid']:9d} {mark:6s} {pay}")


if __name__ == "__main__":
    main()
