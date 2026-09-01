# -*- coding: utf-8 -*-
"""Расписание на 02.09: все занятия с числом записанных и пробных."""
import sys, json, time
sys.path.insert(0, "/home/user/kidsup")
from app import db
from app.moyklass_client import MoyklassClient
mk=None
for a in range(6):
    try: mk=MoyklassClient(db.get_setting("moyklass_api_key")); mk.authenticate(); break
    except Exception: time.sleep(5)
_c=mk.get("/v1/company/classes", params={"limit":500})
classes={c["id"]:c for c in ((_c.get("classes") if isinstance(_c,dict) else _c) or [])}
_names={}
def nm(uid):
    if uid not in _names:
        try: u=mk.get(f"/v1/company/users/{uid}"); _names[uid]=(u.get("name") or str(uid), u.get("phone") or "", u.get("clientStateId"))
        except Exception: _names[uid]=(str(uid),"",None)
        time.sleep(0.25)
    return _names[uid]
r=mk.get("/v1/company/lessons", params={"date":["2026-09-02","2026-09-02"],"includeRecords":"true","limit":500})
out=[]
for l in (r.get("lessons") if isinstance(r,dict) else r) or []:
    recs=l.get("records") or []
    out.append({"time":f'{l.get("beginTime")}–{l.get("endTime")}', "group":(classes.get(l.get("classId")) or {}).get("name") or "?",
                "n":len(recs), "test":sum(1 for x in recs if x.get("test")), "status":l.get("status"),
                "kids":[{"name":nm(x["userId"])[0],"phone":nm(x["userId"])[1],"state":nm(x["userId"])[2],"test":bool(x.get("test"))} for x in recs]})
out.sort(key=lambda x:(x["time"],x["group"]))
mk.close()
json.dump(out, open("/home/user/kidsup/docs/rabota/raspisanie_0209.json","w"), ensure_ascii=False, indent=1)
for o in out: print(o["time"], o["n"], "зап.", o["test"], "проб.", o["group"])
