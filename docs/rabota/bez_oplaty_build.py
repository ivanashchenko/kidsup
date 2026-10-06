#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""«Ходят без оплаты за октябрь» — расчёт по свежим выгрузкам МойКласса.

Запуск: python3 docs/rabota/bez_oplaty_build.py <папка с *.json из /export/raw> [месяц YYYY-MM]
Пишет <папка>/bez_oplaty_rows.json и печатает сводку. Страницу собирает bez_oplaty_page.py.

Логика (06.10.2026, после замечания Бориса «актуализируй внимательно»):
  * берём детей со статусом «Учится» (join.statusId == 2) в учебных группах сезона 2627_*
    (не «Заявки»), у которых есть записи на занятия месяца;
  * занятие месяца считается оплаченным, если запись привязана к абонементу
    (lesson_record.userSubscription) ИЛИ есть абонемент ребёнка, который действует в месяце,
    покрывает эту группу/курс (classIds/courseIds/mainClassId; пустой список = любой) и оплачен
    (payed >= price − 1 ₽). Годовые и мультигрупповые абонементы (Козловский) так учитываются;
  * пара «ребёнок — группа» попадает в список, если в месяце есть хотя бы одна запись без оплаты;
  * теги: bal — на балансе хватает на абонемент (маткапитал и пр.) → завести абонемент;
          inv — абонемент/счёт на месяц есть, но не оплачен → напомнить со ссылкой;
          ask — уже был на занятиях месяца без оплаты → спросить/собрать (после закрытия явки);
          nov — в месяце ещё не был → выяснить, ходит ли, напомнить;
          next — в месяце уже был по старому абонементу, остаток месяца не оплачен → напомнить;
          debt — отрицательный баланс.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
MONTH = sys.argv[2] if len(sys.argv) > 2 else "2026-10"
M_FROM, M_TO = f"{MONTH}-01", f"{MONTH}-31"
TODAY = __import__("datetime").date.today().isoformat()


def load(name):
    return json.load(open(SRC / f"{name}.json", encoding="utf-8"))


users = {u["id"]: u for u in load("users")}
classes = {c["id"]: c for c in load("classes")}
courses = {c["id"]: c for c in load("courses")}
lessons = {l["id"]: l for l in load("lessons")}
records = load("lesson_records")
joins = load("joins")
subs = load("user_subscriptions")
payments = load("payments")
invoices = load("invoices")


def teacher(cname: str) -> str:
    n = cname
    if "Мини-сад" in n or "Лицей" in n:
        return "Татьяна Дубровская"
    if "Нулевой" in n:
        return "Екатерина Чепурнова"
    if "_ПШ_" in n:
        return "Екатерина Чепурнова" if "вт-чт" in n else "Татьяна Першина"
    if "_АЯ_" in n:
        return "Илья Ярославцев" if "вт-чт" in n else "Мария Колотушкина"
    if "Музыка и речь" in n:
        return "Елена Казанцева"
    if "Первая школа" in n:
        return "Ирина Семёнова"
    if "ИЗО" in n:
        return "Виктория Короткова"
    if "_МА_" in n:
        return "Дарина Габдуллина"
    if "ЛГ Марина" in n:
        return "Марина Буракова (логопед)"
    if "ЛГ Елена" in n:
        return "Елена Попова (логопед)"
    if "ШАХ" in n:
        return "Шахматная школа (партнёр)"
    if "Робот" in n:
        return "Айтроника (робототехника)"
    return "Прочее"


def real_class(cid) -> bool:
    n = (classes.get(cid) or {}).get("name") or ""
    return n.startswith("2627_") and "аявк" not in n.lower()


# --- абонементы ребёнка, действующие в месяце и оплаченные
def sub_covers(s, cid, course_id) -> bool:
    if s.get("statusId") == 4 and (s.get("endDate") or "") < M_FROM:
        return False
    b, e = s.get("beginDate") or "", s.get("endDate") or "9999-12-31"
    if not (b <= M_TO and e >= M_FROM):
        return False
    cls_ok = (not s.get("classIds") and not s.get("courseIds")) or cid in (s.get("classIds") or []) \
        or s.get("mainClassId") == cid or (course_id and course_id in (s.get("courseIds") or []))
    return cls_ok


def sub_paid(s) -> bool:
    return float(s.get("payed") or 0) >= float(s.get("price") or 0) - 1


subs_by_user = defaultdict(list)
for s in subs:
    subs_by_user[s["userId"]].append(s)
pays_by_user = defaultdict(list)
for p in payments:
    pays_by_user[p["userId"]].append(p)
inv_by_user = defaultdict(list)
for i in invoices:
    inv_by_user[i["userId"]].append(i)

# --- учится: (uid, cid) со статусом 2
uchitsya = {(j["userId"], j["classId"]): j for j in joins if j.get("statusId") == 2 and real_class(j["classId"])}

# --- записи месяца по парам
pairs = defaultdict(lambda: {"recs": 0, "covered": 0, "visited": [], "visited_uncovered": 0, "future": 0})
for r in records:
    if r.get("test"):
        continue
    l = lessons.get(r["lessonId"])
    if not l or not (M_FROM <= (l.get("date") or "") <= M_TO) or l.get("status") == 2:
        continue
    key = (r["userId"], l["classId"])
    if key not in uchitsya:
        continue
    p = pairs[key]
    p["recs"] += 1
    cov = bool(r.get("userSubscription")) or bool(r.get("paid"))
    if cov:
        p["covered"] += 1
    if r.get("visit"):
        p["visited"].append(l["date"][8:10])
        if not cov:
            p["visited_uncovered"] += 1
    if l["date"] > TODAY:
        p["future"] += 1

rows = []
for (uid, cid), p in pairs.items():
    u = users.get(uid) or {}
    c = classes.get(cid) or {}
    cname_full = c.get("name") or ""
    cname = cname_full[5:]
    course_id = c.get("courseId") or uchitsya[(uid, cid)].get("courseId")
    my_subs = subs_by_user.get(uid, [])
    covering = [s for s in my_subs if sub_covers(s, cid, course_id)]
    paid_cov = [s for s in covering if sub_paid(s)]
    if p["covered"] == p["recs"] or paid_cov:
        continue                                   # месяц оплачен
    unpaid_sub = sorted((s for s in covering if not sub_paid(s)), key=lambda s: s.get("beginDate") or "")
    j = uchitsya[(uid, cid)]
    # ожидаемая цена: цена вступления, иначе последний абонемент по этой группе, иначе 0
    price = float(j.get("price") or 0)
    if not price:
        same = [s for s in my_subs if cid in (s.get("classIds") or []) or s.get("mainClassId") == cid]
        same.sort(key=lambda s: s.get("beginDate") or "")
        price = float((same[-1].get("price") if same else 0) or 0)
    if not price:
        others = [float(s.get("price") or 0) for s in subs if cid in (s.get("classIds") or []) and (s.get("beginDate") or "") >= "2026-09-01" and float(s.get("price") or 0) > 0]
        if others:
            others.sort(); price = others[len(others) // 2]
    if not price and course_id:
        tpl = [float(t.get("price") or 0) for t in load("subscriptions") if course_id in (t.get("courses") or []) and float(t.get("price") or 0) > 0]
        if tpl:
            price = min(tpl)
    if unpaid_sub:
        price = float(unpaid_sub[-1].get("price") or price)
    bal = float(u.get("availableBalance") or u.get("balans") or 0)
    last_sub = sorted(my_subs, key=lambda s: s.get("beginDate") or "")
    last_sub = last_sub[-1] if last_sub else None
    pays = sorted((x for x in pays_by_user.get(uid, []) if float(x.get("summa") or 0) > 0), key=lambda x: x.get("date") or "")
    last_pay = pays[-1] if pays else None
    tags = []
    if bal < 0:
        tags.append("debt")
    if unpaid_sub:
        tags.append("inv")
    if bal > 0 and price and bal >= price * 0.95:
        tags.append("bal")
    if p["visited_uncovered"]:
        tags.append("ask")
    elif not p["visited"]:
        tags.append("nov")
    else:
        tags.append("next")                      # ходит по старому абонементу, остаток месяца не оплачен
    rows.append({
        "uid": uid, "name": u.get("name") or str(uid), "phone": u.get("phone") or "",
        "class_id": cid, "group": cname, "teacher": teacher(cname_full),
        "recs": p["recs"], "covered": p["covered"], "visited": sorted(set(p["visited"])),
        "visited_uncovered": p["visited_uncovered"], "future": p["future"],
        "price": round(price), "balance": round(bal),
        "last_sub": (f"{last_sub.get('beginDate')}–{last_sub.get('endDate') or '…'} {int(float(last_sub.get('price') or 0))} ₽"
                     + ("" if sub_paid(last_sub) else " (не оплачен)")) if last_sub else "—",
        "unpaid_sub": [{"id": s["id"], "price": s.get("price"), "payed": s.get("payed"), "begin": s.get("beginDate"), "end": s.get("endDate")} for s in unpaid_sub],
        "last_pay": f"{last_pay.get('date')} {int(float(last_pay.get('summa') or 0))} ₽" if last_pay else "—",
        "tags": tags,
    })

rows.sort(key=lambda r: (r["teacher"], r["group"], r["name"]))
import datetime as _dt
soon_to = (_dt.date.today() + _dt.timedelta(days=10)).isoformat()
ending = []
for (uid, cid), j in uchitsya.items():
    my = subs_by_user.get(uid, [])
    cur = [s for s in my if s.get("statusId") in (1, 2) and sub_paid(s) and (s.get("endDate") or "") and TODAY <= s["endDate"] <= soon_to
           and (not s.get("classIds") or cid in s["classIds"] or s.get("mainClassId") == cid)]
    if not cur:
        continue
    nxt = [s for s in my if (s.get("beginDate") or "") > cur[0]["endDate"] and (not s.get("classIds") or cid in s["classIds"] or s.get("mainClassId") == cid)]
    if nxt:
        continue
    u = users.get(uid) or {}
    ending.append({"uid": uid, "name": u.get("name"), "phone": u.get("phone"), "group": (classes[cid]["name"] or "")[5:],
                   "teacher": teacher(classes[cid]["name"] or ""), "end": cur[0]["endDate"], "price": cur[0].get("price"),
                   "left": (cur[0].get("visitCount") or 0) - (cur[0].get("visitedCount") or 0), "balance": round(float(u.get("availableBalance") or 0))})
ending.sort(key=lambda r: (r["end"], r["teacher"]))
json.dump(ending, open(SRC / "bez_oplaty_ending.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump(rows, open(SRC / "bez_oplaty_rows.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

from collections import Counter
print("пар ребёнок—группа:", len(rows), " детей:", len({r['uid'] for r in rows}))
print("ожидается ₽:", round(sum(r["price"] for r in rows)))
print("теги:", Counter(t for r in rows for t in r["tags"]))
print("по педагогам:", Counter(r["teacher"] for r in rows))
print("были в месяце без оплаты (ask):", sum(1 for r in rows if "ask" in r["tags"]))
print("абонементы кончаются в 10 дней без следующего:", len(ending))
for e in ending: print(f"   до {e['end']} {e['name'][:26]:26s} {e['group'][:36]:36s} ост.{e['left']} бал {e['balance']} цена {e['price']}")
for r in rows:
    print(f"  {r['teacher'][:18]:18s} {r['group'][:38]:38s} {r['name'][:26]:26s} зап{r['recs']:2d} покр{r['covered']:2d} был {','.join(r['visited']) or '—':8s} бал {r['balance']:>7} цена {r['price']:>6} {','.join(r['tags'])}")
