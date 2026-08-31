# -*- coding: utf-8 -*-
"""Три списка на 31.08: ДОД-недожатые, Праздник, летние/прошлогодние."""
import sys, json, time, collections
sys.path.insert(0,"/home/user/kidsup")
from app import db
from app.moyklass_client import MoyklassClient
TAG_PRAZDNIK=118871
STOP={146328,215202,125957}          # не писать, отказ
mk=None
for a in range(6):
    try:
        mk=MoyklassClient(db.get_setting("moyklass_api_key")); mk.authenticate(); break
    except Exception: time.sleep(5)
aud=json.load(open("/home/user/kidsup/docs/rabota/audit_zapisey.json"))
REAL=[g for g in aud["groups"] if not g["zayavki_group"]]
BUF=[g for g in aud["groups"] if g["zayavki_group"]]
LIVE={"Учится","3. Записался на пробное","5. Посетил пробное"}
inreal={}; grpof=collections.defaultdict(list)
for g in REAL:
    for k in g["kids"]:
        if k["jstname"] in LIVE:
            grpof[k["uid"]].append(g["name"])
            if inreal.get(k["uid"])!="Учится": inreal[k["uid"]]=k["jstname"]
inbuf={k["uid"] for g in BUF for k in g["kids"] if not k["dead"]}

UC={}
def user(u):
    if u not in UC:
        try: UC[u]=mk.get(f"/v1/company/users/{u}")
        except Exception: UC[u]={}
    return UC[u]

# ── 1) ДОД
dod=json.load(open("/home/user/kidsup/docs/rabota/dod_merged.json"))
L1=[]
for d in dod:
    u=d.get("uid"); st=inreal.get(u)
    L1.append({"name":d.get("child") or d.get("crm_name"),"phone":d.get("phone"),
               "slot":d.get("slot"),"course":d.get("course"),"uid":u,
               "status": "Учится" if st=="Учится" else ("на пробном" if st else "нет записи"),
               "grp": ", ".join(grpof.get(u,[]))[:60]})
L1.sort(key=lambda x:{"нет записи":0,"на пробном":1,"Учится":2}[x["status"]])

# ── 2) Праздник: тег 118871, ещё не в группе
prz=[]
page=1
while True:
    r=mk.get("/v1/company/users", params={"limit":300,"offset":(page-1)*300})
    us=(r.get("users") if isinstance(r,dict) else r) or []
    if not us: break
    for x in us:
        tg=[t.get("id") if isinstance(t,dict) else t for t in (x.get("tags") or [])]
        if TAG_PRAZDNIK in tg:
            u=x["id"]
            if u in inreal or x.get("clientStateId") in STOP: continue
            prz.append({"uid":u,"name":(x.get("name") or "").strip(),
                        "phone":x.get("phone") or "","state":x.get("clientStateId"),
                        "buf": u in inbuf})
    if len(us)<300: break
    page+=1
    if page>30: break
mk.close()
out={"dod":L1,"prazdnik":prz}
json.dump(out, open("/home/user/kidsup/docs/rabota/spiski_31.json","w"), ensure_ascii=False)
print(f"ДОД: {len(L1)} — без записи {sum(1 for x in L1 if x['status']=='нет записи')}, "
      f"на пробном {sum(1 for x in L1 if x['status']=='на пробном')}, "
      f"учится {sum(1 for x in L1 if x['status']=='Учится')}")
print(f"Праздник без записи в группу: {len(prz)}")
