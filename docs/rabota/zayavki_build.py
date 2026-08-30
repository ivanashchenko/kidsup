# -*- coding: utf-8 -*-
import sys, json, time, collections
sys.path.insert(0,"/home/user/kidsup")
from app import db
from app.moyklass_client import MoyklassClient
d=json.load(open("/home/user/kidsup/docs/rabota/audit_zapisey.json"))
REAL=[g for g in d["groups"] if not g["zayavki_group"]]
BUF=[g for g in d["groups"] if g["zayavki_group"]]
items=[]
for g in BUF:
    for k in g["kids"]:
        if not k["dead"]:
            items.append(dict(k, grp=g["name"], subj=g["subj"], kind="буфер"))
for g in REAL:
    for k in g["kids"]:
        if k["jstname"]=="1. Новая заявка":
            items.append(dict(k, grp=g["name"], subj=g["subj"], kind="в группе"))
mk=None
for a in range(6):
    try:
        mk=MoyklassClient(db.get_setting("moyklass_api_key")); mk.authenticate(); break
    except Exception: time.sleep(5)
PH={}
for it in items:
    u=it["uid"]
    if u not in PH:
        try:
            x=mk.get(f"/v1/company/users/{u}")
            PH[u]={"phone":x.get("phone") or "","state":x.get("clientStateId")}
        except Exception: PH[u]={"phone":"","state":None}
    it.update(PH[u])
mk.close()
items.sort(key=lambda x:(x["created"], x["subj"]))
json.dump(items, open("/home/user/kidsup/docs/rabota/zayavki_open.json","w"), ensure_ascii=False)
print("заявок:", len(items), "| без телефона:", sum(1 for i in items if not i["phone"]))
c=collections.Counter(i["subj"] for i in items)
for k,v in c.most_common(): print(f"   {v:3d}  {k}")
print("\nсамые старые:")
for i in items[:8]:
    print(f'   {i["created"]}  {i["name"][:26]:26s} {i["phone"]:>12s}  {i["subj"][:18]:18s} ({i["mgr"]})')
