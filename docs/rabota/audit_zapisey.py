# -*- coding: utf-8 -*-
"""Полный аудит записей 2026/27: реальные дети, карточки-заявки, дубли, перекомплект."""
import sys, json, re, time, collections
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

r=mk.get("/v1/company/classes", params={"limit":500})
cls=(r.get("classes") if isinstance(r,dict) else r) or []
DAYS=["пн","вт","ср","чт","пт","сб","вс"]
def days_of(n):
    s=re.sub(r"[_\-–]"," ",(n or "").lower())
    return [d for d in DAYS if re.search(rf"(?<![а-я]){d}(?![а-я])", s)]
def subj(n):
    n=n[5:] if n.startswith("2627_") else n
    h=n.split("_")[0].strip()
    if h.startswith("РР"): return "Раннее развитие"
    return {"АЯ":"Английский","ПШ":"Подготовка к школе","МА":"Ментальная арифметика",
            "ЛГ":"Логопед","ИЗО":"ИЗО","ШХ":"Шахматы","РБ":"Робототехника",
            "СЧ":"Скорочтение","КЛ":"Каллиграфия","ТЦ":"Танцы"}.get(h.split()[0], h)
MGR={232763:"Ира",232805:"Аня",202856:"Лена",154181:"Лиза"}
UC={}
def uname(u):
    if u not in UC:
        try: UC[u]=(mk.get(f"/v1/company/users/{u}").get("name") or "").strip()
        except Exception: UC[u]="?"
    return UC[u]

groups=[]; allj=[]
for c in cls:
    nm=c.get("name","")
    if not nm.startswith("2627_"): continue
    j=mk.get("/v1/company/joins", params={"classId":c["id"],"limit":300})
    js=(j.get("joins") if isinstance(j,dict) else j) or []
    kids=[]
    for x in js:
        st=x.get("statusId")
        u=x.get("userId"); n=uname(u)
        rec={"uid":u,"name":n,"jst":st,"jstname":JST.get(st,str(st)),
             "mgr":MGR.get(x.get("managerId"),"—"),"created":str(x.get("createdAt"))[:10],
             "zayavka":("аявк" in n) or (not n.strip()),
             "dead": st in (1,4)}
        kids.append(rec); allj.append(dict(rec, cls=nm))
    m=re.search(r"(\d{1,2}:\d{2})", nm)
    groups.append({"id":c["id"],"name":nm[5:],"raw":nm,"time":m.group(1) if m else "",
                   "max":c.get("maxStudents"),"days":days_of(nm),
                   "subj":subj(nm),"zayavki_group":"Заявки" in nm,"kids":kids})
mk.close()
json.dump({"groups":groups}, open("/home/user/kidsup/docs/rabota/audit_zapisey.json","w"),
          ensure_ascii=False)
print("групп:", len(groups), " строк-записей:", len(allj))
