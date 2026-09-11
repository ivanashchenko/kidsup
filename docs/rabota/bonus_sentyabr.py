# -*- coding: utf-8 -*-
"""Бонусы админов за сентябрь по тарифу от 20.08 (без строки «разговоры» — отменена 11.09).
Период 01.09–11.09. Пробное засчитывается по дате СОСТОЯВШЕГОСЯ занятия, поэтому записи
берём с 17.08 (начало тарифа). Оплаты, вторые предметы — по дате в сентябре."""
import sys, json, time, collections, datetime as dt
sys.path.insert(0, "/home/user/kidsup/docs/rabota"); sys.path.insert(0, "/home/user/kidsup")
from napominaniya import _mk, subj
from app import db

MGR = {232805: "Аня", 232763: "Ира", 202856: "Лена"}
JOIN_FROM = "2026-08-17"          # записи с начала тарифа
P0, P1 = "2026-09-01", "2026-09-11"   # сентябрьский период начисления

mk = _mk()
c = mk.get("/v1/company/classes", params={"limit": 500})
classes = {x["id"]: x for x in ((c.get("classes") if isinstance(c, dict) else c) or [])}
work = [cid for cid, cl in classes.items()
        if (cl.get("name") or "").startswith("2627_") and cl.get("status") == "opened"
        and "Заявки" not in cl["name"]]

# 1. записи менеджеров
joins = []
for cid in work:
    j = mk.get("/v1/company/joins", params={"classId": cid, "limit": 500}); time.sleep(0.25)
    for x in (j.get("joins") if isinstance(j, dict) else j) or []:
        if x.get("managerId") in MGR and (x.get("createdAt") or "")[:10] >= JOIN_FROM and x.get("statusId") != 1:
            joins.append({"uid": x["userId"], "cid": cid, "cls": classes[cid]["name"],
                          "subject": subj(classes[cid]["name"]), "who": MGR[x["managerId"]],
                          "created": x["createdAt"][:10], "status": x.get("statusId")})
print("записей менеджеров с 17.08:", collections.Counter(j["who"] for j in joins))

# 2. состоявшиеся пробные в сентябрьском периоде
recs_cache = {}
def recs(uid):
    if uid not in recs_cache:
        rr = mk.get("/v1/company/lessonRecords",
                    params={"userId": uid, "date": [JOIN_FROM, "2026-09-30"],
                            "includeLessons": "true", "limit": 100}); time.sleep(0.22)
        recs_cache[uid] = [{"date": (y.get("lesson") or {}).get("date"),
                            "cid": (y.get("lesson") or {}).get("classId"),
                            "test": bool(y.get("test")), "visit": bool(y.get("visit"))}
                           for y in ((rr.get("lessonRecords") if isinstance(rr, dict) else rr) or [])]
    return recs_cache[uid]

names = {}
def uname(uid):
    if uid not in names:
        u = mk.get(f"/v1/company/users/{uid}"); time.sleep(0.2)
        names[uid] = u.get("name")
    return names[uid]

trials = []      # состоявшиеся пробные в периоде
seen_trial = set()
for j in joins:
    mine = [r for r in recs(j["uid"]) if r["cid"] == j["cid"]]
    done = [r for r in mine if r["test"] and r["visit"] and r["date"] and P0 <= r["date"] <= P1]
    if done and (j["uid"], j["cid"]) not in seen_trial:
        seen_trial.add((j["uid"], j["cid"]))
        trials.append({"uid": j["uid"], "name": uname(j["uid"]), "who": j["who"],
                       "subject": j["subject"], "date": min(r["date"] for r in done)})
    j["upcoming"] = any(r["test"] and not r["visit"] and r["date"] and r["date"] > P1 for r in mine)
    j["noshow"] = bool([r for r in mine if r["test"]]) and not any(r["visit"] for r in mine) \
        and all((r["date"] or "") <= P1 for r in mine if r["test"])
print("состоявшихся пробных 01–11.09:", collections.Counter(t["who"] for t in trials))
print("пробные впереди:", collections.Counter(j["who"] for j in joins if j["upcoming"]))

# 3. оплаты в периоде
pays = []; off = 0
while True:
    r = mk.get("/v1/company/payments", params={"limit": 500, "offset": off, "date": [P0, P1]})
    ps = (r.get("payments") if isinstance(r, dict) else r) or []
    for p in ps:
        if p.get("optype") == "income" and (p.get("summa") or 0) > 0 and p.get("userId"):
            pays.append({"uid": p["userId"], "who": MGR.get(p.get("managerId")),
                         "date": p["date"], "summa": p["summa"]})
    if len(ps) < 500: break
    off += 500
total_pays = len(pays)
noman = sum(1 for p in pays if not p["who"])
pays = [p for p in pays if p["who"]]
print(f"оплат в периоде {total_pays}, без менеджера {noman}, по менеджерам:",
      collections.Counter(p["who"] for p in pays))

hist = {}
def history(uid):
    if uid not in hist:
        r = mk.get("/v1/company/payments",
                   params={"userId": uid, "limit": 200, "date": ["2020-01-01", "2026-08-31"]})
        time.sleep(0.22)
        hist[uid] = sorted(p["date"] for p in ((r.get("payments") if isinstance(r, dict) else r) or [])
                           if p.get("optype") == "income" and (p.get("summa") or 0) > 0)
    return hist[uid]

fam = {}
for p in pays:
    f = fam.setdefault((p["uid"], p["who"]),
                       {"uid": p["uid"], "who": p["who"], "sum": 0, "dates": []})
    f["sum"] += p["summa"]; f["dates"].append(p["date"])
for f in fam.values():
    h = history(f["uid"])
    f["kind"] = "новый" if not h else ("возврат спящего" if h[-1] < "2026-05-01" else "продление")
    f["name"] = uname(f["uid"])
print("семьи по типу:", collections.Counter((f["who"], f["kind"]) for f in fam.values()))

# 4. второй предмет
second = []
for f in fam.values():
    j = mk.get("/v1/company/joins", params={"userId": f["uid"], "limit": 50}); time.sleep(0.22)
    js = [x for x in ((j.get("joins") if isinstance(j, dict) else j) or [])
          if x.get("statusId") == 2 and x.get("classId") in classes
          and (classes[x["classId"]].get("name") or "").startswith("2627_")]
    subs = {}
    for x in sorted(js, key=lambda x: x.get("createdAt") or ""):
        subs.setdefault(subj(classes[x["classId"]]["name"]), x)
    if len(subs) >= 2:
        for s, x in list(subs.items())[1:]:
            d = (x.get("createdAt") or "")[:10]
            if P0 <= d <= P1 and x.get("managerId") in MGR:
                second.append({"uid": f["uid"], "name": f["name"],
                               "who": MGR[x["managerId"]], "subject": s, "date": d})
print("вторые предметы:", collections.Counter(s["who"] for s in second))

# 5. смены: плановый график + фактическая активность в CRM
sched = json.loads(db.get_setting("admin_schedule") or "{}")
NAME = {232805: "Аня", 202856: "Лена", 232763: "Ира", 154181: "Лиза"}
act = collections.defaultdict(set)
for j in joins:
    if P0 <= j["created"] <= P1: act[j["created"]].add(j["who"])
for p in pays:
    if P0 <= p["date"] <= P1: act[p["date"]].add(p["who"])
plan = collections.defaultdict(list)
d = dt.date.fromisoformat(P0)
while d <= dt.date.fromisoformat(P1):
    k = d.isoformat()
    plan[k] = [NAME.get(x, str(x)) for x in sched.get(k, [])]
    d += dt.timedelta(days=1)
shifts_plan = collections.Counter()
shifts_fact = collections.Counter()
for k in plan:
    for n in plan[k]: shifts_plan[n] += 1
    for n in act.get(k, ()): shifts_fact[n] += 1
print("смен по графику:", dict(shifts_plan), "| дней с активностью в CRM:", dict(shifts_fact))

mk.close()
out = {"trials": trials, "fam": list(fam.values()), "second": second,
       "shifts_plan": dict(shifts_plan), "shifts_fact": dict(shifts_fact),
       "plan": dict(plan), "act": {k: sorted(v) for k, v in act.items()},
       "pays_total": total_pays, "pays_no_manager": noman,
       "upcoming": [j for j in joins if j["upcoming"]]}
json.dump(out, open("/home/user/kidsup/docs/rabota/bonus_sentyabr.json", "w"),
          ensure_ascii=False, indent=1)

# 6. итог по тарифу
print("\n=== СДЕЛЬНАЯ 01–11.09 ===")
for who in ("Аня", "Ира", "Лена"):
    t = sum(1 for x in trials if x["who"] == who)
    n = sum(1 for f in fam.values() if f["who"] == who and f["kind"] == "новый")
    v = sum(1 for f in fam.values() if f["who"] == who and f["kind"] == "возврат спящего")
    s = sum(1 for x in second if x["who"] == who)
    total = t * 300 + n * 800 + v * 500 + s * 400
    print(f"{who}: пробные {t}×300={t*300} · новые {n}×800={n*800} · "
          f"возвраты {v}×500={v*500} · второй предмет {s}×400={s*400} → {total} ₽ "
          f"| смен план {shifts_plan.get(who,0)}, факт {shifts_fact.get(who,0)} "
          f"→ гарантия {shifts_plan.get(who,0)*1900} ₽")
print("saved")
