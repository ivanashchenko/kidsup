# -*- coding: utf-8 -*-
"""Кто платил в 2025/26 и летом 2026 — для среза «летние и прошлогодние»."""
import sys, json, time
sys.path.insert(0,"/home/user/kidsup")
from app import db
from app.moyklass_client import MoyklassClient
mk=None
for a in range(6):
    try:
        mk=MoyklassClient(db.get_setting("moyklass_api_key")); mk.authenticate(); break
    except Exception: time.sleep(5)
payers=set(); off=0
while True:
    r=mk.get("/v1/company/payments", params={"limit":500,"offset":off,
             "date":["2025-09-01","2026-08-31"]})
    ps=(r.get("payments") if isinstance(r,dict) else r) or []
    if not ps: break
    for p in ps:
        if p.get("userId") and (p.get("summa") or 0) > 0: payers.add(p["userId"])
    if len(ps)<500: break
    off+=500
    if off>40000: break
mk.close()
json.dump(sorted(payers), open("/home/user/kidsup/docs/rabota/payers.json","w"))
print("плательщиков за 2025/26 + лето 2026:", len(payers))
S3=json.load(open("/home/user/kidsup/docs/rabota/spisok3.json"))
hit=[x for x in S3 if x["uid"] in payers]
print("из 436 недозвонов/думающих платили раньше:", len(hit))
print("  недозвон:", sum(1 for x in hit if x["state"]==345768),
      "| думает:", sum(1 for x in hit if x["state"]==146950))
json.dump(hit, open("/home/user/kidsup/docs/rabota/spisok3_payers.json","w"), ensure_ascii=False)
