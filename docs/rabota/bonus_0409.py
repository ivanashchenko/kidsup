# -*- coding: utf-8 -*-
"""Бонусы Ани и Иры по тарифу от 20.08 (docs/bonusy_adminov.html), период 17.08–04.09,
плюс потенциал до 10.09. Всё из CRM: записи по менеджеру (join.managerId), явка по
lessonRecords, оплаты по payments.managerId. Звонки >1 мин за август — из Манго по добавочным."""
import sys, json, time, collections, datetime as dt, hashlib
sys.path.insert(0, "/home/user/kidsup/docs/rabota"); sys.path.insert(0, "/home/user/kidsup")
from napominaniya import _mk, subj
MGR = {232805: "Аня", 232763: "Ира", 202856: "Лена"}
P0, P1, PMAX = "2026-08-17", "2026-09-04", "2026-09-10"
mk = _mk()
c = mk.get("/v1/company/classes", params={"limit": 500})
classes = {x["id"]: x for x in ((c.get("classes") if isinstance(c, dict) else c) or [])}
work = [cid for cid, cl in classes.items() if (cl.get("name") or "").startswith("2627_") and cl.get("status") == "opened" and "Заявки" not in cl["name"]]

# 1. записи (joins) по менеджеру
joins = []
for cid in work:
    j = mk.get("/v1/company/joins", params={"classId": cid, "limit": 500}); time.sleep(0.25)
    for x in (j.get("joins") if isinstance(j, dict) else j) or []:
        if x.get("managerId") in MGR and (x.get("createdAt") or "")[:10] >= P0 and x.get("statusId") != 1:
            joins.append({"uid": x["userId"], "cid": cid, "cls": classes[cid]["name"], "subject": subj(classes[cid]["name"]),
                          "who": MGR[x["managerId"]], "created": x["createdAt"][:10], "status": x.get("statusId")})
print("записей по менеджерам с 17.08:", collections.Counter(j["who"] for j in joins))

# 2. явка на пробные по этим записям
recs_cache = {}
def recs(uid):
    if uid not in recs_cache:
        rr = mk.get("/v1/company/lessonRecords", params={"userId": uid, "date": [P0, "2026-09-30"], "includeLessons": "true", "limit": 100}); time.sleep(0.22)
        recs_cache[uid] = [{"date": (y.get("lesson") or {}).get("date"), "cid": (y.get("lesson") or {}).get("classId"), "test": bool(y.get("test")), "visit": bool(y.get("visit"))}
                           for y in ((rr.get("lessonRecords") if isinstance(rr, dict) else rr) or [])]
    return recs_cache[uid]
for j in joins:
    mine = [r for r in recs(j["uid"]) if r["cid"] == j["cid"]]
    tests = [r for r in mine if r["test"]]
    j["trial_dates"] = sorted(r["date"] for r in tests)
    j["attended"] = any(r["visit"] for r in tests) or any(r["visit"] for r in mine)
    j["first_visit"] = min((r["date"] for r in mine if r["visit"]), default=None)
    j["upcoming"] = any(r["date"] > P1 for r in tests) and not j["attended"]
    j["past_noshow"] = bool(tests) and not j["attended"] and all(r["date"] <= P1 for r in tests)
print("дошли:", collections.Counter(j["who"] for j in joins if j["attended"]), "| ждём:", collections.Counter(j["who"] for j in joins if j["upcoming"]), "| не дошли:", collections.Counter(j["who"] for j in joins if j["past_noshow"]))

# 3. оплаты по менеджеру
pays = []; off = 0
while True:
    r = mk.get("/v1/company/payments", params={"limit": 500, "offset": off, "date": [P0, P1]}); ps = (r.get("payments") if isinstance(r, dict) else r) or []
    for p in ps:
        if p.get("optype") == "income" and (p.get("summa") or 0) > 0 and p.get("managerId") in MGR and p.get("userId"):
            pays.append({"uid": p["userId"], "who": MGR[p["managerId"]], "date": p["date"], "summa": p["summa"], "comment": p.get("comment") or ""})
    if len(ps) < 500: break
    off += 500
print("оплат по менеджерам:", collections.Counter(p["who"] for p in pays), "сумма", collections.Counter())
hist = {}
def history(uid):
    if uid not in hist:
        r = mk.get("/v1/company/payments", params={"userId": uid, "limit": 200, "date": ["2020-01-01", "2026-08-16"]}); time.sleep(0.22)
        ds = sorted(p["date"] for p in ((r.get("payments") if isinstance(r, dict) else r) or []) if p.get("optype") == "income" and (p.get("summa") or 0) > 0)
        hist[uid] = ds
    return hist[uid]
fam = {}
for p in pays:
    f = fam.setdefault((p["uid"], p["who"]), {"uid": p["uid"], "who": p["who"], "sum": 0, "dates": []})
    f["sum"] += p["summa"]; f["dates"].append(p["date"])
for f in fam.values():
    h = history(f["uid"])
    f["kind"] = "новый" if not h else ("возврат спящего" if h[-1] < "2026-05-01" else "продление")
    u = mk.get(f"/v1/company/users/{f['uid']}"); time.sleep(0.2)
    f["name"] = u.get("name")
print("семьи по типу:", collections.Counter((f["who"], f["kind"]) for f in fam.values()))

# 4. второй предмет: у плательщика ≥2 предметов «учится», вторая запись создана менеджером в периоде
second = []
for f in fam.values():
    j = mk.get("/v1/company/joins", params={"userId": f["uid"], "limit": 50}); time.sleep(0.22)
    js = [x for x in ((j.get("joins") if isinstance(j, dict) else j) or []) if x.get("statusId") == 2 and x.get("classId") in classes and (classes[x["classId"]].get("name") or "").startswith("2627_")]
    subs = {}
    for x in sorted(js, key=lambda x: x.get("createdAt") or ""):
        subs.setdefault(subj(classes[x["classId"]]["name"]), x)
    if len(subs) >= 2:
        for s, x in list(subs.items())[1:]:
            if (x.get("createdAt") or "")[:10] >= P0 and x.get("managerId") in MGR:
                second.append({"uid": f["uid"], "name": f["name"], "who": MGR[x["managerId"]], "subject": s})
print("вторые предметы:", collections.Counter(s["who"] for s in second))

# 5. звонки дольше минуты за 17–31.08 по добавочным (Манго)
calls = collections.Counter()
try:
    import httpx
    from app import db
    key = db.get_setting("mango_key"); salt = db.get_setting("mango_salt")
    def mcall(url, data):
        j = json.dumps(data, separators=(",", ":")); sign = hashlib.sha256((key + j + salt).encode()).hexdigest()
        return httpx.post(url, data={"vpbx_api_key": key, "sign": sign, "json": j}, timeout=60)
    msk = dt.timezone(dt.timedelta(hours=3))
    for d0, d1 in (("2026-08-17", "2026-08-24"), ("2026-08-24", "2026-09-01")):
        t0 = int(dt.datetime.fromisoformat(d0).replace(tzinfo=msk).timestamp()); t1 = int(dt.datetime.fromisoformat(d1).replace(tzinfo=msk).timestamp())
        r = mcall("https://app.mango-office.ru/vpbx/stats/request", {"date_from": t0, "date_to": t1, "fields": "records,start,finish,answer,from_extension,from_number,to_extension,to_number,disconnect_reason"})
        k = r.json().get("key"); rows = None
        for a in range(30):
            time.sleep(4)
            rr = mcall("https://app.mango-office.ru/vpbx/stats/result", {"key": k})
            if rr.status_code == 200 and rr.text.strip(): rows = rr.text.strip().split("\n"); break
            if rr.status_code == 204: continue
        for line in rows or []:
            p = line.split(";")
            if len(p) < 9: continue
            rec, start, finish, answer, fe, fn, te, tn, reason = p[:9]
            try: start = int(start or 0); finish = int(finish or 0); answer = int(answer or 0)
            except Exception: continue
            dur = (finish - answer) if answer else 0
            if dur >= 60:
                ext = (fe or te or "").strip()
                day = dt.datetime.fromtimestamp(start, msk).strftime("%Y-%m-%d")
                calls[(ext, day)] += 1
    print("звонков >1 мин по доб.:", sum(calls.values()))
except Exception as e:
    print("Манго не считался:", str(e)[:120])
mk.close()
json.dump({"joins": joins, "fam": list(fam.values()), "second": second, "calls": [[k[0], k[1], v] for k, v in calls.items()]},
          open("/home/user/kidsup/docs/rabota/bonus_0409.json", "w"), ensure_ascii=False, indent=1)
print("saved")
