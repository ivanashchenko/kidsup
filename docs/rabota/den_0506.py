# -*- coding: utf-8 -*-
"""Данные для плана на сб 05.09 и вс 06.09: расписание обоих дней, живые записи без оплаты
(дожим), заявки в буферах, реактивация."""
import sys, json, time, collections, datetime as dt
sys.path.insert(0, "/home/user/kidsup/docs/rabota")
from napominaniya import _mk, subj
mk = _mk()
c = mk.get("/v1/company/classes", params={"limit": 500}); classes = {x["id"]: x for x in ((c.get("classes") if isinstance(c, dict) else c) or [])}
cname = lambda cid: (classes.get(cid) or {}).get("name") or "?"
cards = {}
def card(uid):
    if uid not in cards:
        try: u = mk.get(f"/v1/company/users/{uid}")
        except Exception: u = {}
        bd = next((a.get("value") for a in u.get("attributes") or [] if a.get("attributeAlias") == "birthday"), None)
        age = None
        if bd:
            try: y, m, d = map(int, bd[:10].split("-")); age = round((dt.date(2026, 9, 5) - dt.date(y, m, d)).days / 365.25, 1)
            except Exception: pass
        cards[uid] = {"uid": uid, "name": u.get("name") or str(uid), "phone": u.get("phone") or "", "state": u.get("clientStateId"), "age": age}
        time.sleep(0.18)
    return cards[uid]
# 1. расписание сб и вс
days = {}
for d in ("2026-09-05", "2026-09-06"):
    r = mk.get("/v1/company/lessons", params={"date": [d, d], "includeRecords": "true", "limit": 500})
    L = []
    for l in sorted((r.get("lessons") if isinstance(r, dict) else r) or [], key=lambda x: (x.get("beginTime") or "", x.get("classId") or 0)):
        L.append({"time": (l.get("beginTime") or "")[:5], "group": cname(l.get("classId")), "subject": subj(cname(l.get("classId"))),
                  "kids": [{**card(x["userId"]), "test": bool(x.get("test"))} for x in l.get("records") or []]})
    days[d] = L
    print(d, "занятий", len(L), "детей", sum(len(l["kids"]) for l in L), "пробных", sum(1 for l in L for k in l["kids"] if k["test"]))
# 2. оплаты с 17.08 — кто платил
paid = set(); off = 0
while True:
    r = mk.get("/v1/company/payments", params={"limit": 500, "offset": off, "date": ["2026-08-17", "2026-09-05"]}); ps = (r.get("payments") if isinstance(r, dict) else r) or []
    for p in ps:
        if p.get("optype") == "income" and (p.get("summa") or 0) > 0 and p.get("userId"): paid.add(p["userId"])
    if len(ps) < 500: break
    off += 500
# 3. живые записи без оплаты
unpaid = []; groups = []
for cid, cl in classes.items():
    nm = cl.get("name") or ""
    if cl.get("status") != "opened" or not nm.startswith("2627_") or "Заявки" in nm: continue
    j = mk.get("/v1/company/joins", params={"classId": cid, "limit": 500}); time.sleep(0.2)
    js = (j.get("joins") if isinstance(j, dict) else j) or []
    live = [x for x in js if x.get("statusId") in (2, 58131, 58132)]
    groups.append({"name": nm, "subject": subj(nm), "max": cl.get("maxStudents") or 0, "live": len(live)})
    for x in live:
        if x["userId"] in paid or subj(nm) == "логопед": continue
        unpaid.append({**card(x["userId"]), "group": nm, "subject": subj(nm), "status": x.get("statusId"), "created": (x.get("createdAt") or "")[:10]})
print("живых без оплаты (без логопеда):", len(unpaid), collections.Counter(u["subject"] for u in unpaid))
# ближайшее занятие для каждого неоплатившего
for u in unpaid:
    rr = mk.get("/v1/company/lessonRecords", params={"userId": u["uid"], "date": ["2026-08-31", "2026-09-20"], "includeLessons": "true", "limit": 40}); time.sleep(0.18)
    recs = [((y.get("lesson") or {}).get("date"), ((y.get("lesson") or {}).get("beginTime") or "")[:5], bool(y.get("visit")), bool(y.get("test"))) for y in ((rr.get("lessonRecords") if isinstance(rr, dict) else rr) or []) if (y.get("lesson") or {}).get("classId") in {cid for cid, cl in classes.items() if cl.get("name") == u["group"]}]
    u["visited"] = any(v for _, _, v, _ in recs)
    fut = sorted(d for d, _, _, _ in recs if d and d >= "2026-09-05")
    u["next"] = fut[0] if fut else None
    past = sorted(d for d, _, v, _ in recs if d and d < "2026-09-05")
    u["last"] = past[-1] if past else None
mk.close()
json.dump({"days": days, "unpaid": unpaid, "groups": groups}, open("/home/user/kidsup/docs/rabota/den_0506.json", "w"), ensure_ascii=False, indent=1)
print("saved")
