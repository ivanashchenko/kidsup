# -*- coding: utf-8 -*-
"""Кто работал по дням с 10.08: график из настройки + фактическая активность в CRM."""
import sys, json, time, collections, datetime
sys.path.insert(0,"/home/user/kidsup")
from app import db
from app.moyklass_client import MoyklassClient
mk=None
for a in range(6):
    try:
        mk=MoyklassClient(db.get_setting("moyklass_api_key")); mk.authenticate(); break
    except Exception: time.sleep(5)
N={232805:"Аня",202856:"Лена",232763:"Ира",154181:"Лиза"}
SINCE="2026-08-10"
act=collections.defaultdict(lambda: collections.Counter())
r=mk.get("/v1/company/classes", params={"limit":500})
cls=(r.get("classes") if isinstance(r,dict) else r) or []
for c in cls:
    j=mk.get("/v1/company/joins", params={"classId":c["id"],"limit":300})
    for x in ((j.get("joins") if isinstance(j,dict) else j) or []):
        d=str(x.get("createdAt") or "")[:10]
        if d>=SINCE and x.get("managerId") in N:
            act[d][N[x["managerId"]]]+=1
mk.close()
sched=json.loads(db.get_setting("admin_schedule") or "{}")
DOW=["пн","вт","ср","чт","пт","сб","вс"]
d0=datetime.date(2026,8,10); d1=datetime.date(2026,8,31)
rows=[]; d=d0
while d<=d1:
    k=d.isoformat()
    rows.append({"date":k,"dow":DOW[d.weekday()],
                 "plan":[N.get(x,str(x)) for x in sched.get(k,[])],
                 "fact":dict(act.get(k, collections.Counter()).most_common())})
    d+=datetime.timedelta(days=1)
json.dump(rows, open("/home/user/kidsup/docs/rabota/grafik_avgust.json","w"), ensure_ascii=False)
print(f'{"дата":12s}{"дн":4s}{"в графике":22s}{"записей в группы по админам"}')
for r0 in rows:
    f=" · ".join(f"{k} {v}" for k,v in r0["fact"].items()) or "—"
    print(f'{r0["date"]:12s}{r0["dow"]:4s}{(", ".join(r0["plan"]) or "не задан"):22s}{f}')
