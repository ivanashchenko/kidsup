# -*- coding: utf-8 -*-
"""Свежий срез по группам английского."""
import sys, json, re, time
sys.path.insert(0,"/home/user/kidsup")
from app import db
from app.moyklass_client import MoyklassClient
mk=None
for a in range(6):
    try:
        mk=MoyklassClient(db.get_setting("moyklass_api_key")); mk.authenticate(); break
    except Exception: time.sleep(5)
JST={}
try:
    for x in mk.get("/v1/company/joinStatuses"): JST[x["id"]]=x["name"]
except Exception: pass
LIVE={"Учится","3. Записался на пробное","5. Посетил пробное"}
r=mk.get("/v1/company/classes", params={"limit":500})
cls=(r.get("classes") if isinstance(r,dict) else r) or []
UC={}
def uname(u):
    if u not in UC:
        try: UC[u]=(mk.get(f"/v1/company/users/{u}").get("name") or "").strip()
        except Exception: UC[u]="?"
    return UC[u]
rows=[]
for c in cls:
    nm=c.get("name","")
    if not nm.startswith("2627_АЯ"): continue
    j=mk.get("/v1/company/joins", params={"classId":c["id"],"limit":200})
    kids=[]
    for x in ((j.get("joins") if isinstance(j,dict) else j) or []):
        st=JST.get(x.get("statusId"), str(x.get("statusId")))
        if st not in LIVE and st!="1. Новая заявка": continue
        n=uname(x.get("userId"))
        kids.append({"name":n,"st":st,"zay":("аявк" in n) or not n.strip()})
    m=re.search(r"(\d{1,2}:\d{2})", nm)
    days=re.search(r"_((?:пн|вт|ср|чт|пт|сб|вс)(?:-(?:пн|вт|ср|чт|пт|сб|вс))?)_", nm)
    age=re.search(r"_(\d+-\d+ лет)_", nm)
    lvl=re.search(r"лет_(.+?)(?:\s*\(|$)", nm)
    rows.append({"name":nm[5:], "time":m.group(1) if m else "",
                 "days":days.group(1) if days else "", "age":age.group(1) if age else "",
                 "level":lvl.group(1).strip() if lvl else "", "max":c.get("maxStudents"),
                 "kids":kids})
mk.close()
def tk(t):
    if not t: return 9999
    h,mi=t.split(":"); return int(h)*60+int(mi)
rows.sort(key=lambda x:(x["days"], tk(x["time"])))
json.dump(rows, open("/home/user/kidsup/docs/rabota/angliyskiy.json","w"), ensure_ascii=False)
tot=sum(len([k for k in r0["kids"] if not k["zay"] and k["st"]!="1. Новая заявка"]) for r0 in rows)
seats=sum(r0["max"] or 0 for r0 in rows)
print(f'{"группа":52s}{"дни":8s}{"время":7s}{"зап":>4}{"мест":>5}{"уч":>4}')
for r0 in rows:
    real=[k for k in r0["kids"] if not k["zay"] and k["st"]!="1. Новая заявка"]
    uch=sum(1 for k in real if k["st"]=="Учится")
    print(f'{r0["name"][:52]:52s}{r0["days"]:8s}{r0["time"]:7s}{len(real):>4}{str(r0["max"]):>5}{uch:>4}')
print(f'\nИТОГО: {tot} записей из {seats} мест ({round(tot/seats*100) if seats else 0}%), групп {len(rows)}')
