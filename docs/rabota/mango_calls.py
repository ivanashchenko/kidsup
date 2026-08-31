# -*- coding: utf-8 -*-
"""Выгрузка звонков Манго за сегодня. Узкие окна отдают пустоту — берём весь день."""
import sys, json, time, hashlib, datetime
sys.path.insert(0,"/home/user/kidsup")
import httpx
from app import db
key=db.get_setting("mango_key"); salt=db.get_setting("mango_salt")
def call(url, data):
    j=json.dumps(data, separators=(",",":"))
    sign=hashlib.sha256((key+j+salt).encode()).hexdigest()
    return httpx.post(url, data={"vpbx_api_key":key,"sign":sign,"json":j}, timeout=60)
msk=datetime.timezone(datetime.timedelta(hours=3))
now=datetime.datetime.now(msk); day0=now.replace(hour=0,minute=0,second=0,microsecond=0)
r=call("https://app.mango-office.ru/vpbx/stats/request",
  {"date_from":int(day0.timestamp()),"date_to":int(now.timestamp()),
   "fields":"records,start,finish,answer,from_extension,from_number,to_extension,to_number,disconnect_reason"})
k=r.json().get("key"); rows=None
for a in range(30):
    time.sleep(4)
    rr=call("https://app.mango-office.ru/vpbx/stats/result", {"key":k})
    if rr.status_code==200 and rr.text.strip(): rows=rr.text.strip().split("\n"); break
out=[]
for line in rows or []:
    p=line.split(";")
    if len(p)<9: continue
    rec,start,finish,answer,fe,fn,te,tn,reason=p[:9]
    try: start=int(start or 0); finish=int(finish or 0); answer=int(answer or 0)
    except: continue
    out.append({"rec":rec.strip("[]").split(",")[0],"start":start,"dur":(finish-answer) if answer else 0,"answer":answer,
                "dir":"in" if not fe.strip() else "out","from":fn,"to":tn,"reason":reason,
                "t":datetime.datetime.fromtimestamp(start,msk).strftime("%H:%M")})
json.dump(out, open("/tmp/calls_day.json","w"), ensure_ascii=False)
cut=int((now-datetime.timedelta(minutes=95)).timestamp())
last=[c for c in out if c["start"]>=cut]
print(f"сейчас {now:%H:%M} МСК · за день {len(out)} · за последние 95 мин {len(last)}")
for c in last:
    print(f'  {c["t"]} {c["dir"]:3s} {c["from"]:>13s} -> {c["to"]:>13s} dur={c["dur"]:4d} '
          f'rec={"да" if c["rec"] else "-"} {c["reason"]}')
