# -*- coding: utf-8 -*-
"""Воронка недели открытых уроков по схеме наставника: доходимость и 4 группы.
1 купили · 2 пришли и записались дальше · 3 пришли и не записались · 4 не дошли."""
import sys, json, time, collections
sys.path.insert(0, "/home/user/kidsup")
from napominaniya import _mk, subj
DAYS = ["2026-08-31", "2026-09-01"]
mk = _mk()
classes = {}
c = mk.get("/v1/company/classes", params={"limit": 500})
for x in (c.get("classes") if isinstance(c, dict) else c) or []:
    classes[x["id"]] = x.get("name") or "?"
# 1. записи на пробные за дни мероприятия
trial = {}
for d in DAYS:
    r = mk.get("/v1/company/lessons", params={"date": [d, d], "includeRecords": "true", "limit": 500})
    for l in (r.get("lessons") if isinstance(r, dict) else r) or []:
        for rec in l.get("records") or []:
            if not rec.get("test"):
                continue
            u = rec["userId"]
            t = trial.setdefault(u, {"dates": [], "visit": False, "groups": set()})
            t["dates"].append(d); t["groups"].add(subj(classes.get(l.get("classId"), "")))
            if rec.get("visit"):
                t["visit"] = True
# 2. оплаты
pays = collections.defaultdict(list)
off = 0
while True:
    r = mk.get("/v1/company/payments", params={"limit": 500, "offset": off,
                                               "date": ["2026-08-31", "2026-09-02"]})
    ps = (r.get("payments") if isinstance(r, dict) else r) or []
    for p in ps:
        if p.get("userId") and (p.get("summa") or 0) > 0 and p.get("optype") == "income":
            pays[p["userId"]].append(p)
    if len(ps) < 500:
        break
    off += 500
# 3. будущие записи
out = {"купили": [], "пришли_записались": [], "пришли_не_записались": [], "не_дошли": [],
       "не_дошли_записались": []}
for uid, t in trial.items():
    u = mk.get(f"/v1/company/users/{uid}"); time.sleep(0.25)
    nm = u.get("name") or str(uid)
    rr = mk.get("/v1/company/lessonRecords", params={
        "userId": uid, "date": ["2026-09-02", "2026-10-31"], "includeLessons": "true", "limit": 50})
    future = [(y.get("lesson") or {}).get("date") for y in
              ((rr.get("lessonRecords") if isinstance(rr, dict) else rr) or [])]
    future = sorted(x for x in future if x)
    time.sleep(0.25)
    item = {"name": nm, "phone": u.get("phone"), "when": min(t["dates"]),
            "subj": ", ".join(t["groups"]), "next": future[0] if future else None}
    if uid in pays:
        out["купили"].append(item)
    elif t["visit"]:
        out["пришли_записались" if future else "пришли_не_записались"].append(item)
    else:
        out["не_дошли_записались" if future else "не_дошли"].append(item)
mk.close()
prishli = len(out["купили"]) + len(out["пришли_записались"]) + len(out["пришли_не_записались"])
vsego = len(trial)
print(f"записаны на пробное 31.08–01.09: {vsego}")
print(f"доходимость: {prishli}/{vsego} = {round(100*prishli/vsego)}%")
for k, v in out.items():
    print(f"{k}: {len(v)}")
    for i in v:
        print(f'   {i["when"][5:]} {(i["name"] or "")[:28]:28s} {i["phone"] or "":11s} {i["subj"][:22]:22s} след.: {i["next"] or "нет"}')
json.dump(out, open("/home/user/kidsup/docs/rabota/voronka_probnyh.json", "w"), ensure_ascii=False, indent=1)
