# -*- coding: utf-8 -*-
"""Признаки контактов из выгрузки МойКласса ($SP/oct4, снимок 07.10 23:20) для
ранжирования сегментов обзвона. Запуск: python3 -I build_features.py <oct4_dir> <out.json>"""
import json, sys, collections, datetime as dt
SRC, OUT = sys.argv[1], sys.argv[2]
L = lambda n: json.load(open(f"{SRC}/{n}.json"))
users, joins, classes, courses = L("users"), L("joins"), L("classes"), L("courses")
lessons, lr, subs, pays = L("lessons"), L("lesson_records"), L("user_subscriptions"), L("payments")
TODAY = dt.date(2026, 10, 8)
cmap = {c["id"]: c for c in classes}; crs = {c["id"]: c["name"] for c in courses}
season = {c["id"] for c in classes if c["name"].startswith("2627")}
camp = {c["id"] for c in classes if ("лагер" in c["name"].lower() or "летн" in c["name"].lower()) and (c.get("beginDate") or "") >= "2026-05"}
les = {l["id"]: l for l in lessons}
ST = {125951:"новый лид",345768:"недозвон",146950:"думает",125952:"записался",125953:"был на пробном",125955:"клиент",
      125957:"отказ",353124:"переехали",146328:"не писать",345759:"архив",125954:"?125954",125956:"?125956",146513:"?146513",215202:"?215202",345767:"?345767",349497:"?349497",345689:"?345689"}
JS = {2:"учится",83760:"подтвердил",58132:"записался на пробное",58131:"посетил пробное",50509:"новая заявка",1:"?1",3:"?3",4:"?4",5:"?5"}
by_user_j = collections.defaultdict(list)
for j in joins: by_user_j[j["userId"]].append(j)
by_user_lr = collections.defaultdict(list)
for r in lr:
    if (r.get("createdAt") or "") >= "2026-06": by_user_lr[r["userId"]].append(r)
last_visit = {}
for r in lr:
    if r.get("visit"):
        l = les.get(r["lessonId"]); d = (l or {}).get("date") or (l or {}).get("beginTime","")[:10]
        if d and d > last_visit.get(r["userId"], ""): last_visit[r["userId"]] = d
by_user_s = collections.defaultdict(list)
for s in subs: by_user_s[s["userId"]].append(s)
by_user_p = collections.defaultdict(list)
for p in pays:
    if p.get("optype") == "income" and (p.get("summa") or 0) > 0: by_user_p[p["userId"]].append(p)
def age(u):
    for a in u.get("attributes") or []:
        if a.get("attributeAlias") == "birthday" and a.get("value"):
            try:
                b = dt.date.fromisoformat(a["value"][:10]); m = (TODAY.year-b.year)*12 + TODAY.month-b.month - (TODAY.day < b.day)
                return f"{m//12},{m%12}"
            except Exception: return ""
    return ""
def attr(u, alias):
    for a in u.get("attributes") or []:
        if a.get("attributeAlias") == alias: return a.get("value")
    return None
out = []
for u in users:
    uid = u["id"]; jj = by_user_j.get(uid, [])
    sj = [{"class": cmap[j["classId"]]["name"].replace("2627_",""), "course": crs.get(j["courseId"]), "status": JS.get(j["statusId"], j["statusId"]),
           "created": (j.get("createdAt") or "")[:10], "changed": (j.get("stateChangedAt") or "")[:10], "visits": (j.get("stats") or {}).get("visits"),
           "next": (j.get("stats") or {}).get("nextRecord"), "comment": (j.get("comment") or "")[:120], "adv": j.get("advSourceId")}
          for j in jj if j["classId"] in season]
    camp26 = [cmap[j["classId"]]["name"] for j in jj if j["classId"] in camp]
    trials = []
    for r in by_user_lr.get(uid, []):
        if r.get("test"):
            l = les.get(r["lessonId"]) or {}
            trials.append({"date": l.get("date") or l.get("beginTime","")[:10], "class": (cmap.get(l.get("classId")) or {}).get("name","").replace("2627_",""), "visit": bool(r.get("visit"))})
    ss = by_user_s.get(uid, [])
    act = [s for s in ss if (s.get("endDate") or "") >= "2026-10-01" and (s.get("beginDate") or "") <= "2026-10-31" and s.get("statusId") in (2,4)]
    pp = by_user_p.get(uid, []); pp.sort(key=lambda p: p["date"])
    paid_before = sum(p["summa"] for p in pp if p["date"] < "2026-06-01")
    paid_season = sum(p["summa"] for p in pp if p["date"] >= "2026-08-01")
    out.append({"id": uid, "name": u["name"], "phone": u.get("phone") or "", "phone2": attr(u, "telefon_2"), "state": ST.get(u["clientStateId"], u["clientStateId"]),
                "state_id": u["clientStateId"], "reason": u.get("statusChangeReasonId"), "state_changed": (u.get("stateChangedAt") or "")[:10],
                "created": (u.get("createdAt") or "")[:10], "updated": (u.get("updatedAt") or "")[:10], "age": age(u), "parent": attr(u, "parent1"),
                "tags": [t.get("name") if isinstance(t, dict) else t for t in (u.get("tags") or [])], "balance": u.get("balans"), "adv": u.get("advSourceId"),
                "season_joins": sj, "camp2026": camp26, "trials2026": trials, "last_visit": last_visit.get(uid),
                "active_sub_oct": [{"class": (cmap.get(s.get("mainClassId")) or {}).get("name","").replace("2627_",""), "end": s.get("endDate"), "payed": s.get("payed")} for s in act],
                "paid_before_2026_06": paid_before, "paid_season": paid_season, "last_pay": (pp[-1]["date"] if pp else None),
                "n_pays": len(pp), "responsibles": u.get("responsibles")})
json.dump(out, open(OUT, "w"), ensure_ascii=False)
print("users:", len(out))
c = collections.Counter(x["state"] for x in out); print("states:", c.most_common())
print("with season joins:", sum(1 for x in out if x["season_joins"]), "| camp2026:", sum(1 for x in out if x["camp2026"]),
      "| trials2026:", sum(1 for x in out if x["trials2026"]), "| active oct sub:", sum(1 for x in out if x["active_sub_oct"]))
print("join statuses season:", collections.Counter(j["status"] for x in out for j in x["season_joins"]).most_common())
