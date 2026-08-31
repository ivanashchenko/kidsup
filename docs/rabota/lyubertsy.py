# -*- coding: utf-8 -*-
"""Поиск контактов люберецкого клуба сети в нашей базе."""
import sys, json, time, re, collections
sys.path.insert(0,"/home/user/kidsup")
from app import db
from app.moyklass_client import MoyklassClient
mk=None
for a in range(6):
    try:
        mk=MoyklassClient(db.get_setting("moyklass_api_key")); mk.authenticate(); break
    except Exception: time.sleep(5)
KEYS=re.compile(r"люберц|люберец|8 марта|8марта|октябрьск|томилино|красков|"
                r"не наш филиал|другой клуб|другой филиал|не тот адрес|перепутал.{0,12}адрес",
                re.I)
hits={}
# 1) по именам карточек
off=0
while True:
    r=mk.get("/v1/company/users", params={"limit":300,"offset":off})
    us=(r.get("users") if isinstance(r,dict) else r) or []
    if not us: break
    for x in us:
        if KEYS.search(str(x.get("name") or "")):
            hits[x["id"]]={"name":x.get("name"),"phone":x.get("phone"),
                           "state":x.get("clientStateId"),"why":"имя карточки"}
    if len(us)<300: break
    off+=300
    if off>12000: break
print("по именам:", len(hits))
# 2) по комментариям
off=0; seen=0
while True:
    try:
        r=mk.get("/v1/company/userComments", params={"limit":500,"offset":off})
    except Exception as e:
        print("comments ERR", str(e)[:80]); break
    cs=(r.get("comments") if isinstance(r,dict) else r) or []
    if not cs: break
    seen+=len(cs)
    for c in cs:
        if KEYS.search(str(c.get("comment") or "")):
            u=c.get("userId")
            if u and u not in hits:
                hits[u]={"name":None,"phone":None,"state":None,
                         "why":"комментарий: "+str(c.get("comment"))[:110]}
    if len(cs)<500: break
    off+=500
    if off>60000: break
print("просмотрено комментариев:", seen, "| всего кандидатов:", len(hits))
# добираем имена/телефоны
for u,v in hits.items():
    if not v["phone"]:
        try:
            x=mk.get(f"/v1/company/users/{u}")
            v["name"]=(x.get("name") or "").strip(); v["phone"]=x.get("phone")
            v["state"]=x.get("clientStateId")
        except Exception: pass
mk.close()
json.dump(hits, open("/home/user/kidsup/docs/rabota/lyubertsy.json","w"), ensure_ascii=False)
for u,v in hits.items():
    print(f'   {u}  {str(v["name"])[:28]:28s} {str(v["phone"]):>12s}  ст.{v["state"]}  {v["why"][:70]}')
