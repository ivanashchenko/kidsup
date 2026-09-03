# -*- coding: utf-8 -*-
"""Итог трёх дней недели открытых уроков (31.08–02.09): записались → пришли →
купили, по направлениям и группам; заполненность групп; источники."""
import sys, json, time, re, datetime as dt, collections
sys.path.insert(0, "/home/user/kidsup/docs/rabota")
from napominaniya import _mk, subj

DAYS = ["2026-08-31", "2026-09-01", "2026-09-02"]
mk = _mk()
c = mk.get("/v1/company/classes", params={"limit": 500})
classes = {x["id"]: x for x in ((c.get("classes") if isinstance(c, dict) else c) or [])}

# 1. все записи на занятия за три дня
recs = []
for d in DAYS:
    r = mk.get("/v1/company/lessons", params={"date": [d, d], "includeRecords": "true", "limit": 500})
    for l in (r.get("lessons") if isinstance(r, dict) else r) or []:
        cl = classes.get(l.get("classId")) or {}
        for rec in l.get("records") or []:
            recs.append({"day": d, "time": (l.get("beginTime") or "")[:5], "classId": l.get("classId"),
                         "group": cl.get("name") or "?", "subject": subj(cl.get("name") or ""),
                         "uid": rec["userId"], "test": bool(rec.get("test")),
                         "visit": bool(rec.get("visit")), "skip": bool(rec.get("skip"))})
    time.sleep(0.3)
print("записей на занятия за 3 дня:", len(recs), "| пробных:", sum(1 for r in recs if r["test"]))

# 2. оплаты за период (плюс день до — авансовые)
pays = collections.defaultdict(list)
off = 0
while True:
    r = mk.get("/v1/company/payments", params={"limit": 500, "offset": off, "date": ["2026-08-30", "2026-09-02"]})
    ps = (r.get("payments") if isinstance(r, dict) else r) or []
    for p in ps:
        if p.get("userId") and (p.get("summa") or 0) > 0 and p.get("optype") == "income":
            pays[p["userId"]].append({"date": p["date"], "summa": p["summa"], "comment": p.get("comment") or ""})
    if len(ps) < 500: break
    off += 500
print("плательщиков за 30.08–02.09:", len(pays), "| сумма:", int(sum(x["summa"] for v in pays.values() for x in v)))

# 3. по каждому ребёнку с пробным: карточка, будущие записи, источник
trial_uids = sorted({r["uid"] for r in recs if r["test"]})
users = {}
for uid in trial_uids:
    try:
        u = mk.get(f"/v1/company/users/{uid}")
    except Exception:
        u = {}
    time.sleep(0.25)
    rr = mk.get("/v1/company/lessonRecords", params={"userId": uid, "date": ["2026-09-03", "2026-10-15"],
                                                     "includeLessons": "true", "limit": 30})
    fut = sorted((y.get("lesson") or {}).get("date") for y in ((rr.get("lessonRecords") if isinstance(rr, dict) else rr) or [])
                 if (y.get("lesson") or {}).get("date"))
    time.sleep(0.25)
    # были ли оплаты до 30.08 — старый клиент?
    old = mk.get("/v1/company/payments", params={"userId": uid, "limit": 3, "date": ["2020-01-01", "2026-08-29"]})
    old = (old.get("payments") if isinstance(old, dict) else old) or []
    time.sleep(0.25)
    users[uid] = {"name": u.get("name"), "phone": u.get("phone"), "state": u.get("clientStateId"),
                  "created": (u.get("createdAt") or "")[:10], "advSource": u.get("advSourceId"),
                  "createSource": u.get("createSourceId"),
                  "old_client": any((o.get("summa") or 0) > 0 for o in old),
                  "next": fut[0] if fut else None, "paid": pays.get(uid, [])}
print("карточек пробных:", len(users))

# 4. заполненность открытых групп 2026/27
groups = []
for cid, cl in classes.items():
    nm = cl.get("name") or ""
    if cl.get("status") != "opened" or not nm.startswith("2627_") or "Заявки" in nm:
        continue
    j = mk.get("/v1/company/joins", params={"classId": cid, "limit": 500})
    joins = (j.get("joins") if isinstance(j, dict) else j) or []
    by = collections.Counter(x.get("statusId") for x in joins)
    paid_here = sum(1 for x in joins if x.get("statusId") in (2, 58131, 58132) and x["userId"] in pays)
    groups.append({"id": cid, "name": nm, "subject": subj(nm), "max": cl.get("maxStudents"),
                   "учится": by.get(2, 0), "посетил": by.get(58131, 0), "записался": by.get(58132, 0),
                   "заявка": by.get(50509, 0), "оплатили_за_3дня": paid_here})
    time.sleep(0.3)
print("групп 2026/27:", len(groups))
mk.close()
json.dump({"recs": recs, "users": {str(k): v for k, v in users.items()}, "pays": {str(k): v for k, v in pays.items()},
           "groups": groups}, open("/home/user/kidsup/docs/rabota/itog_3dnya.json", "w"), ensure_ascii=False, indent=1)
print("saved")
