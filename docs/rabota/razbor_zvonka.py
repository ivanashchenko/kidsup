# -*- coding: utf-8 -*-
"""Скачивание записи Манго + расшифровка."""
import sys, json, time, hashlib, datetime, os
sys.path.insert(0,"/home/user/kidsup")
import httpx
from app import db
key=db.get_setting("mango_key"); salt=db.get_setting("mango_salt")
def call(url, data):
    j=json.dumps(data, separators=(",",":"))
    sign=hashlib.sha256((key+j+salt).encode()).hexdigest()
    return httpx.post(url, data={"vpbx_api_key":key,"sign":sign,"json":j},
                      timeout=90, follow_redirects=True)
rec=sys.argv[1]; out=f"/tmp/{rec}.mp3"
r=call("https://app.mango-office.ru/vpbx/queries/recording/post/",
       {"recording_id":rec,"action":"download"})
print("статус", r.status_code, "байт", len(r.content))
if r.status_code==200 and len(r.content)>1000:
    open(out,"wb").write(r.content); print("сохранено", out)
else:
    print("тело:", r.text[:200]); sys.exit(1)
from faster_whisper import WhisperModel
m=WhisperModel("small", device="cpu", compute_type="int8")
segs,info=m.transcribe(out, language="ru", vad_filter=True, beam_size=1)
txt=" ".join(s.text.strip() for s in segs)
print("\n=== РАСШИФРОВКА ===\n", txt)
open(f"/tmp/{rec}.txt","w",encoding="utf-8").write(txt)
