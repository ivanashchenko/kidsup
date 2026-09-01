# -*- coding: utf-8 -*-
"""Кто идёт во вторник 01.09 + что осталось незакрытым."""
import sys, json, re, time, collections
sys.path.insert(0,"/home/user/kidsup")
from app import db
from app.moyklass_client import MoyklassClient
mk=None
for a in range(6):
    try:
        mk=MoyklassClient(db.get_setting("moyklass_api_key")); mk.authenticate(); break
    except Exception: time.sleep(5)
JST={x["id"]:x["name"] for x in mk.get("/v1/company/joinStatuses")}
LIVE={"Учится","3. Записался на пробное","5. Посетил пробное"}
DAYS=["пн","вт","ср","чт","пт","сб","вс"]
def days_of(n):
    s=re.sub(r"[_\-–]"," ",(n or "").lower())
    return [d for d in DAYS if re.search(rf"(?<![а-я]){d}(?![а-я])", s)]
UC={}
def uname(u):
    if u not in UC:
        try: UC[u]=(mk.get(f"/v1/company/users/{u}").get("name") or "").strip()
        except Exception: UC[u]="?"
    return UC[u]
r=mk.get("/v1/company/classes", params={"limit":500})
cls=(r.get("classes") if isinstance(r,dict) else r) or []
rows=[]
for c in cls:
    nm=c.get("name","")
    if not nm.startswith("2627_") or "аявк" in nm: continue
    dd=days_of(nm)
    daily = (not dd) and any(k in nm for k in ("Мини-сад","Нулевой класс"))
    if "вт" not in dd and not daily: continue
    j=mk.get("/v1/company/joins", params={"classId":c["id"],"limit":200})
    kids=[]
    for x in ((j.get("joins") if isinstance(j,dict) else j) or []):
        st=JST.get(x.get("statusId"),"")
        if st not in LIVE: continue
        n=uname(x.get("userId"))
        if "аявк" in n: continue
        kids.append({"name":n,"st":st,"uid":x.get("userId")})
    if kids:
        m=re.search(r"(\d{1,2}:\d{2})", nm)
        rows.append({"name":nm[5:],"time":m.group(1) if m else ("09:00" if daily else ""),
                     "max":c.get("maxStudents"),"kids":kids,
                     "daily":daily,"first": (dd and min(dd,key=DAYS.index)=="вт")})
mk.close()
def tk(t):
    if not t: return 9999
    h,m=t.split(":"); return int(h)*60+int(m)
rows.sort(key=lambda x:tk(x["time"]))
json.dump(rows, open("/home/user/kidsup/docs/rabota/vtornik.json","w"), ensure_ascii=False)
tot=sum(len(r0["kids"]) for r0 in rows)
print(f"групп во вторник: {len(rows)}, детей: {tot}\n")
for r0 in rows:
    mark = "НОВАЯ ГРУППА" if r0["first"] else ("ежедневная" if r0["daily"] else "продолжение")
    print(f'{r0["time"]:>5}  {len(r0["kids"]):2d}/{r0["max"]}  {mark:13s} {r0["name"][:52]}')
