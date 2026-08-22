"""Что дают задачи: измерение результата, а не активности.

Зачем. Задачи ставились, переставлялись и закрывались, но никто не знал,
приводят ли они к записи. «Закрыто 122 задачи» звучит как работа, хотя
может означать, что сто двадцать две задачи были никому не нужны.
Без этого измерения нельзя ответить на главный вопрос — какие задачи
стоит ставить дальше, а какие только занимают смену.

Как считаем. По каждой закрытой задаче смотрим, что произошло с клиентом
ПОСЛЕ её закрытия: появилась запись в группу, появилась оплата, сменился
статус на «записался». Окно — WINDOW дней: запись через две недели после
звонка к этому звонку уже мало относится.

Чего это НЕ доказывает. Совпадение по времени не равно причине: клиент
мог записаться сам, увидев пост. Поэтому цифры сравниваются между собой —
не «эта задача сработала», а «задачи такого типа дают запись вдвое чаще
других». На разнице видно, куда вкладывать смену.

Запуск:
    python -m app.taskresult          — сводка по типам задач
    python -m app.taskresult days 14  — за другой период
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from . import sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.taskresult")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"

STAFF = {232763: "Ира", 232805: "Аня", 202856: "Лена",
         154181: "Лиза", 84116: "Борис", 229704: "Маша"}
ACTIVE = {2, 50509, 58131, 58132, 83760}
WINDOW = 3          # дней после закрытия, в которые засчитываем результат

# Тип задачи по её тексту — то, ЧТО мы просили сделать.
KINDS = [
    ("возврат прошлогодних", r"ходил в прошлом году|занимался давно|был летом|"
                             r"продолжение занятий|продление"),
    ("новая заявка", r"НОВАЯ ЗАЯВКА|заявка с сайта|новый контакт"),
    ("пропущенный звонок", r"пропущенн\w+ звон"),
    ("клиент написал", r"клиент писал|клиент ждёт|ответа нет"),
    ("промоутер", r"промоутер|промо-контакт"),
    ("деньги", r"оплат|счёт|счет|возврат|долг|ссылк\w* на оплату"),
    ("подтверждение прихода", r"подтвердить|напомнить о занятии|приход"),
    ("дожим после пробного", r"после пробного|дожать|не дошёл|не пришёл"),
]


def kind_of(body: str) -> str:
    b = body or ""
    for name, pat in KINDS:
        if re.search(pat, b, re.I):
            return name
    return "прочее"


def _closed_at(t: dict) -> str:
    """У задачи нет времени закрытия — берём день, на который она стояла.

    Это приближение: закрыть могли и позже. Но для сравнения типов между
    собой сдвиг одинаков и картину не искажает."""
    return (t.get("beginDate") or "")[:10]


def measure(days: int = 14) -> dict:
    mk = MoyklassClient(sync.get_api_key())
    try:
        tasks = []
        for mid in STAFF:
            tasks += taskguard.all_tasks(mk, mid)
        tasks = list({t["id"]: t for t in tasks}.values())

        joins = taskguard.pull_all(mk, "/v1/company/joins", "joins")
        pays = taskguard.pull_all(mk, "/v1/company/payments", "payments")
    finally:
        mk.close()

    since = (date.today() - timedelta(days=days)).isoformat()
    # что и когда случилось у клиента
    signed = defaultdict(list)
    for j in joins:
        if j.get("statusId") in ACTIVE and j.get("userId") and j.get("createdAt"):
            signed[j["userId"]].append(j["createdAt"][:10])
    paid = defaultdict(list)
    for p in pays:
        if (p.get("optype") or "") == "income" and p.get("userId") \
                and (p.get("price") or 0) > 0:
            paid[p["userId"]].append((p.get("createdAt") or p.get("date") or "")[:10])

    stat = defaultdict(lambda: {"задач": 0, "записей": 0, "оплат": 0})
    examples = defaultdict(list)
    for t in tasks:
        if not (t.get("isComplete") or t.get("isCompleted")):
            continue
        d = _closed_at(t)
        if not d or d < since:
            continue
        body = t.get("body") or ""
        # Служебные закрытия — это моя уборка, а не работа администратора.
        if re.match(r"^\[(убрано|дубль|закрыто|сведено)", body):
            continue
        uid = t.get("userId")
        k = kind_of(body)
        s = stat[k]
        s["задач"] += 1
        if not uid:
            continue
        edge = (date.fromisoformat(d) + timedelta(days=WINDOW)).isoformat()
        if any(d <= x <= edge for x in signed.get(uid, [])):
            s["записей"] += 1
            if len(examples[k]) < 3:
                examples[k].append(body[:80])
        if any(d <= x <= edge for x in paid.get(uid, [])):
            s["оплат"] += 1

    out = {"период": f"{since} — {date.today().isoformat()}",
           "окно": WINDOW, "типы": {}}
    for k, v in sorted(stat.items(), key=lambda x: -x[1]["задач"]):
        conv = round(v["записей"] * 100 / v["задач"]) if v["задач"] else 0
        out["типы"][k] = {**v, "конверсия_в_запись": conv,
                          "примеры": examples.get(k, [])}
    try:
        json.dump(out, open(f"{SP}/taskresult.json", "w"), ensure_ascii=False)
    except Exception:
        pass
    return out


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    days = 14
    if "days" in sys.argv:
        i = sys.argv.index("days")
        if len(sys.argv) > i + 1:
            days = int(sys.argv[i + 1])
    r = measure(days)
    print(f"период {r['период']}, результат засчитан в течение {r['окно']} дн.\n")
    print(f"  {'тип задачи':26s} {'задач':>6} {'записей':>8} {'оплат':>6} {'конверсия':>10}")
    for k, v in r["типы"].items():
        print(f"  {k:26s} {v['задач']:6d} {v['записей']:8d} {v['оплат']:6d} "
              f"{str(v['конверсия_в_запись']) + '%':>10}")


if __name__ == "__main__":
    main()
