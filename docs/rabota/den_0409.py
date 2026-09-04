# -*- coding: utf-8 -*-
"""Данные для плана на пятницу 04.09 (Аня + Ира): воронка четырёх дней 31.08–03.09,
расписание сегодня, заявки по робототехнике (1 или 2 раза в неделю), записи на шахматы,
заполненность групп."""
import sys, json, time, datetime as dt, collections
sys.path.insert(0, "/home/user/kidsup/docs/rabota")
from napominaniya import _mk, subj

DAYS = ["2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03"]
TODAY = "2026-09-04"
mk = _mk()
c = mk.get("/v1/company/classes", params={"limit": 500})
classes = {x["id"]: x for x in ((c.get("classes") if isinstance(c, dict) else c) or [])}
cname = lambda cid: (classes.get(cid) or {}).get("name") or "?"

cards = {}
def card(uid):
    if uid not in cards:
        try:
            u = mk.get(f"/v1/company/users/{uid}")
        except Exception:
            u = {}
        bd = next((a.get("value") for a in u.get("attributes") or [] if a.get("attributeAlias") == "birthday"), None)
        age = None
        if bd:
            try:
                y, m, d = map(int, bd[:10].split("-")); age = round((dt.date(2026, 9, 4) - dt.date(y, m, d)).days / 365.25, 1)
            except Exception: pass
        cards[uid] = {"name": u.get("name") or str(uid), "phone": u.get("phone") or "", "state": u.get("clientStateId"),
                      "created": (u.get("createdAt") or "")[:10], "age": age}
        time.sleep(0.22)
    return cards[uid]

# 1. записи на занятия за 4 дня
recs = []
for d in DAYS:
    r = mk.get("/v1/company/lessons", params={"date": [d, d], "includeRecords": "true", "limit": 500})
    for l in (r.get("lessons") if isinstance(r, dict) else r) or []:
        for rec in l.get("records") or []:
            recs.append({"day": d, "time": (l.get("beginTime") or "")[:5], "classId": l.get("classId"),
                         "group": cname(l.get("classId")), "subject": subj(cname(l.get("classId"))),
                         "uid": rec["userId"], "test": bool(rec.get("test")),
                         "visit": bool(rec.get("visit")), "skip": bool(rec.get("skip"))})
    time.sleep(0.3)
print("записей за 4 дня:", len(recs), "| пробных:", sum(1 for r in recs if r["test"]))

# 2. оплаты 30.08–03.09
pays = collections.defaultdict(list); off = 0
while True:
    r = mk.get("/v1/company/payments", params={"limit": 500, "offset": off, "date": ["2026-08-30", "2026-09-03"]})
    ps = (r.get("payments") if isinstance(r, dict) else r) or []
    for p in ps:
        if p.get("userId") and (p.get("summa") or 0) > 0 and p.get("optype") == "income":
            pays[p["userId"]].append({"date": p["date"], "summa": p["summa"], "comment": p.get("comment") or ""})
    if len(ps) < 500: break
    off += 500
print("плательщиков 30.08–03.09:", len(pays), "| сумма:", int(sum(x["summa"] for v in pays.values() for x in v)))

# 3. карточки пробных + будущие записи
users = {}
for uid in sorted({r["uid"] for r in recs if r["test"]}):
    u = card(uid)
    rr = mk.get("/v1/company/lessonRecords", params={"userId": uid, "date": [TODAY, "2026-10-15"], "includeLessons": "true", "limit": 30})
    fut = sorted((y.get("lesson") or {}).get("date") for y in ((rr.get("lessonRecords") if isinstance(rr, dict) else rr) or []) if (y.get("lesson") or {}).get("date"))
    time.sleep(0.22)
    users[uid] = {**u, "next": fut[0] if fut else None, "paid": pays.get(uid, [])}
print("карточек пробных:", len(users))

# 4. расписание сегодня
r = mk.get("/v1/company/lessons", params={"date": [TODAY, TODAY], "includeRecords": "true", "limit": 500})
lessons = []
for l in sorted((r.get("lessons") if isinstance(r, dict) else r) or [], key=lambda x: (x.get("beginTime") or "", x.get("classId") or 0)):
    lessons.append({"lessonId": l.get("id"), "classId": l.get("classId"), "time": (l.get("beginTime") or "")[:5], "group": cname(l.get("classId")),
                    "subject": subj(cname(l.get("classId"))), "status": l.get("status"),
                    "kids": [{**card(x["userId"]), "uid": x["userId"], "test": bool(x.get("test"))} for x in l.get("records") or []]})
print("занятий сегодня:", len(lessons), "| детей:", sum(len(l["kids"]) for l in lessons), "| пробных:", sum(1 for l in lessons for k in l["kids"] if k["test"]))

# 5. робототехника и шахматы: joins по группам
def joins_of(cid):
    j = mk.get("/v1/company/joins", params={"classId": cid, "limit": 500}); time.sleep(0.3)
    return (j.get("joins") if isinstance(j, dict) else j) or []
robo = []
for cid, cl in classes.items():
    if "робот" in (cl.get("name") or "").lower() and cl.get("status") == "opened":
        for j in joins_of(cid):
            robo.append({**card(j["userId"]), "uid": j["userId"], "status": j.get("statusId"), "group": cl["name"], "comment": j.get("comment") or ""})
chess = []
for cid, cl in classes.items():
    if "ШАХ" in (cl.get("name") or "") and cl.get("status") == "opened":
        for j in joins_of(cid):
            chess.append({**card(j["userId"]), "uid": j["userId"], "status": j.get("statusId"), "group": cl["name"]})
        rr = mk.get("/v1/company/lessonRecords", params={"classId": cid, "date": [TODAY, "2026-09-13"], "includeLessons": "true", "limit": 100}); time.sleep(0.3)
        for y in (rr.get("lessonRecords") if isinstance(rr, dict) else rr) or []:
            chess.append({**card(y["userId"]), "uid": y["userId"], "status": "record", "group": cl["name"], "date": (y.get("lesson") or {}).get("date"),
                          "time": ((y.get("lesson") or {}).get("beginTime") or "")[:5]})
print("робототехника заявок:", len(robo), "| шахматы joins+records:", len(chess))

# 6. заполненность групп 2026/27
groups = []
for cid, cl in classes.items():
    nm = cl.get("name") or ""
    if cl.get("status") != "opened" or not nm.startswith("2627_") or "Заявки" in nm: continue
    by = collections.Counter(x.get("statusId") for x in joins_of(cid))
    groups.append({"id": cid, "name": nm, "subject": subj(nm), "max": cl.get("maxStudents"),
                   "учится": by.get(2, 0), "посетил": by.get(58131, 0), "записался": by.get(58132, 0), "заявка": by.get(50509, 0), "не_пришёл": by.get(99336, 0)})
print("групп 2026/27:", len(groups))
mk.close()
json.dump({"recs": recs, "pays": {str(k): v for k, v in pays.items()}, "users": {str(k): v for k, v in users.items()},
           "lessons": lessons, "robo": robo, "chess": chess, "groups": groups},
          open("/home/user/kidsup/docs/rabota/den_0409.json", "w"), ensure_ascii=False, indent=1)
print("saved")
