# -*- coding: utf-8 -*-
"""Третий список: платившие летом/в прошлом году, не записанные; недозвоны и думающие."""
import sys, json, time, collections
sys.path.insert(0,"/home/user/kidsup")
from app import db
from app.moyklass_client import MoyklassClient
mk=None
for a in range(6):
    try:
        mk=MoyklassClient(db.get_setting("moyklass_api_key")); mk.authenticate(); break
    except Exception: time.sleep(5)
aud=json.load(open("/home/user/kidsup/docs/rabota/audit_zapisey.json"))
LIVE={"Учится","3. Записался на пробное","5. Посетил пробное"}
inreal={k["uid"] for g in aud["groups"] if not g["zayavki_group"]
        for k in g["kids"] if k["jstname"] in LIVE}
STOP={146328,215202,125957}
NAMES={125951:"новый лид",345768:"недозвон",146950:"думает",125952:"записался",
       345759:"архив набора"}
TARGET={345768,146950}          # недозвон + думает
out=[]; page=0
while True:
    r=mk.get("/v1/company/users", params={"limit":300,"offset":page*300})
    us=(r.get("users") if isinstance(r,dict) else r) or []
    if not us: break
    for x in us:
        st=x.get("clientStateId")
        if st in TARGET and x["id"] not in inreal and st not in STOP:
            tg=[t.get("id") if isinstance(t,dict) else t for t in (x.get("tags") or [])]
            out.append({"uid":x["id"],"name":(x.get("name") or "").strip(),
                        "phone":x.get("phone") or "","state":st,"stname":NAMES.get(st,str(st)),
                        "prazdnik":118871 in tg,
                        "changed":str(x.get("stateChangedAt") or "")[:10]})
    if len(us)<300: break
    page+=1
    if page>40: break
mk.close()
out.sort(key=lambda x:(x["state"], x["changed"]))
json.dump(out, open("/home/user/kidsup/docs/rabota/spisok3.json","w"), ensure_ascii=False)
c=collections.Counter(x["stname"] for x in out)
print("ИТОГО:", len(out), dict(c))
print("из них с тегом Праздник:", sum(1 for x in out if x["prazdnik"]))
