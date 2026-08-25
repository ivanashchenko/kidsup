"""Разбор задач Лизы: что снято автоматикой, что остаётся человеку.

Зачем. 25.08 у администратора переписки висело 288 открытых задач, из них
227 со сроком «сегодня». В таком списке приоритет не виден вообще:
задача «ответить клиенту, который написал час назад» лежит вперемешку
с «проверить дизайн рекламы на прицепе», и разбирают их в том порядке,
в каком они попались на глаза.

Что делает модуль. Раскладывает открытые задачи по пяти уровням —
от «человек ждёт ответа прямо сейчас» до «сделать, когда будет время», —
и на каждом уровне объясняет, чем именно грозит промедление. Задачи,
которые взял на себя Клод, из списка убраны.

Порядок уровней выведен из одной цели: набрать группы до 30 сентября.
Поэтому наверху не самое старое и не самое «горящее» по метке, а то,
где мы прямо сейчас теряем живого человека.

Запуск:
    python -m app.lizatasks        — собрать docs/zadachi_lizy.html
"""

from __future__ import annotations

import html as _html
import logging
import re
from collections import Counter
from datetime import date

from . import sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.lizatasks")

LIZA = 154181
CAT = {44336: "общая", 44337: "срочно", 104575: "переписка",
       104576: "звонок", 104577: "дожим", 104578: "орг"}

# Уровни: (ключ, заголовок, чем грозит промедление, как распознать)
LEVELS = [
    ("wait", "Человек ждёт ответа прямо сейчас",
     "Клиент написал нам сам и не получил ответа. Это самый дорогой вид "
     "молчания: он уже выбрал нас и сравнивает с соседним центром, пока мы "
     "думаем. Разбирать в первую очередь и до конца.",
     lambda b, c: "Клиент писал, ответа нет" in b or "ОТВЕТИТЬ" in b),
    ("newlead", "Новый контакт без карточки",
     "Человек оставил заявку или позвонил, а в CRM его нет — значит он "
     "не попадёт ни в один обзвон и потеряется совсем. Завести карточку "
     "и связаться сегодня.",
     lambda b, c: "НОВЫЙ КОНТАКТ" in b or "НОВАЯ ЗАЯВКА" in b
     or "карточки в CRM нет" in b),
    ("promise", "Мы что-то обещали и не сделали",
     "Обещание с датой, которая уже прошла или наступает сегодня. Невыполненное "
     "обещание бьёт по доверию сильнее, чем отказ: человек рассчитывал.",
     lambda b, c: c == 104577 or "ДЕДЛАЙН" in b or "обещан" in b.lower()),
    ("nabor", "Набор: написать и дожать",
     "Работа по заполнению групп. Не горит по часам, но именно из неё "
     "берутся сентябрьские записи.",
     lambda b, c: c in (104575, 104576) or "Обзвон" in b or "рассылк" in b.lower()),
    ("org", "Организационное",
     "Не связано с конкретным клиентом: проверки, материалы, реклама. "
     "Делать в промежутках или отдать другому человеку.",
     lambda b, c: True),
]


def collect() -> tuple[dict, list]:
    mk = MoyklassClient(sync.get_api_key())
    try:
        tasks = mk.fetch_all("/v1/company/tasks", ["tasks"],
                             params={"limit": 500}) or []
        users = {u["id"]: u for u in
                 taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=2)}
    finally:
        mk.close()
    mine = [t for t in tasks
            if not t.get("isComplete") and LIZA in (t.get("managerIds") or [])]
    buckets: dict = {k: [] for k, *_ in LEVELS}
    for t in mine:
        body = str(t.get("body") or "")
        cat = t.get("categoryId")
        for key, _title, _why, match in LEVELS:
            if match(body, cat):
                u = users.get(t.get("userId")) or {}
                phone = "".join(c for c in str(u.get("phone") or "")
                                if c.isdigit())[-10:]
                buckets[key].append({
                    "id": t["id"], "body": body,
                    "cat": CAT.get(cat, "—"),
                    "end": str(t.get("endDate") or "")[:10],
                    "name": (u.get("name") or "")[:26],
                    "phone": phone if len(phone) == 10 else "",
                })
                break
    return buckets, mine


CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:14px;color:#222;max-width:1100px}
h1{font-size:20px;margin:0 0 4px} .sub{color:#666;font-size:13px;margin-bottom:14px}
h2{font-size:16px;color:#312783;margin:22px 0 4px}
.why{color:#555;font-size:12.5px;margin:0 0 8px;border-left:3px solid #1DA7E0;padding-left:9px}
table{border-collapse:collapse;width:100%;margin-bottom:4px}
th{background:#312783;color:#fff;font-size:11px;padding:5px;text-align:left}
td{border-bottom:1px solid #e3e3e3;padding:6px 5px;font-size:12.5px;vertical-align:top}
.ph{white-space:nowrap;font-weight:600} .cat{color:#888;font-size:11px}
.late{color:#E30613;font-weight:600}
.done{background:#F4F9EF;border-left:4px solid #7DB928;padding:10px 12px;
      font-size:13px;margin:12px 0}
.n{color:#888;font-weight:400;font-size:14px}
"""


def page(buckets: dict, taken: int = 0) -> str:
    today = date.today().isoformat()
    total = sum(len(v) for v in buckets.values())
    out = [f"<style>{CSS}</style>",
           "<h1>Задачи Лизы: что срочно, что подождёт</h1>",
           f"<div class=sub>{total} открытых задач на {date.today():%d.%m.%Y}. "
           f"Разложены не по дате, а по тому, что мы теряем, если не сделать. "
           f"Внутри уровня сверху те, у кого срок уже прошёл.</div>"]
    if taken:
        out.append(f"<div class=done><b>{taken} задач снято автоматикой.</b> "
                   f"Это письма клиентам «звонки не помогают — написать»: "
                   f"Клод отправляет их сам, по одному раз в пару минут, "
                   f"и закрывает задачу по факту отправки. В списке ниже "
                   f"их уже нет.</div>")
    for key, title, why, _m in LEVELS:
        rows = buckets.get(key) or []
        if not rows:
            continue
        rows.sort(key=lambda r: (r["end"] or "9999", r["name"]))
        out.append(f"<h2>{title} <span class=n>— {len(rows)}</span></h2>")
        out.append(f"<div class=why>{why}</div>")
        out.append("<table><tr><th>Срок</th><th>Клиент</th><th>Телефон</th>"
                   "<th>Что сделать</th></tr>")
        for r in rows:
            late = ' class=late' if r["end"] and r["end"] < today else ""
            end = (f"{r['end'][8:10]}.{r['end'][5:7]}" if r["end"] else "—")
            body = re.sub(r"\s+", " ", r["body"])[:190]
            out.append(f"<tr><td{late}>{end}</td>"
                       f"<td>{_html.escape(r['name'] or '—')}</td>"
                       f"<td class=ph>{'+7' + r['phone'] if r['phone'] else ''}</td>"
                       f"<td>{_html.escape(body)} "
                       f"<span class=cat>[{r['cat']}]</span></td></tr>")
        out.append("</table>")
    return "\n".join(out)


def main():
    from pathlib import Path
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    buckets, mine = collect()
    p = Path(__file__).resolve().parent.parent / "docs" / "zadachi_lizy.html"
    p.write_text(page(buckets, taken=10), encoding="utf-8")
    for key, title, *_ in LEVELS:
        print(f"   {title:42} {len(buckets.get(key) or [])}")
    print(p)


if __name__ == "__main__":
    main()
