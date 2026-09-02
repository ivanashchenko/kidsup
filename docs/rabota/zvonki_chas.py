# -*- coding: utf-8 -*-
"""Ежечасный разбор звонков: выгрузка из Манго, отбор новых записей, расшифровка.

Живёт в репозитории, а не в scratchpad: контейнер регулярно откатывается на
старый коммит и всё во временных каталогах пропадает вместе со списком уже
разобранных записей.

  python3 docs/rabota/zvonki_chas.py [минут]     — список звонков и расшифровки
  python3 docs/rabota/zvonki_chas.py 65 --list   — только список, без расшифровки
"""
import sys, json, time, hashlib, datetime, csv, io, os, pathlib
sys.path.insert(0, "/home/user/kidsup")
import httpx
from app import db

DONE = pathlib.Path("/home/user/kidsup/docs/rabota/calls_done.json")
TALK_MIN = 10          # короче — не расшифровываем (см. инструкцию рутины)
KEY = db.get_setting("mango_key")
SALT = db.get_setting("mango_salt")


def _call(url, data):
    j = json.dumps(data, separators=(",", ":"))
    sign = hashlib.sha256((KEY + j + SALT).encode()).hexdigest()
    return httpx.post(url, data={"vpbx_api_key": KEY, "sign": sign, "json": j},
                      timeout=120, follow_redirects=True)


def calls(minutes: int) -> list[dict]:
    now = int(time.time())
    r = _call("https://app.mango-office.ru/vpbx/stats/request",
              {"date_from": now - minutes * 60, "date_to": now,
               "fields": ("records,start,finish,answer,from_extension,from_number,"
                          "to_extension,to_number,disconnect_reason")})
    k = r.json().get("key")
    txt = ""
    for _ in range(15):
        time.sleep(4)
        rr = _call("https://app.mango-office.ru/vpbx/stats/result", {"key": k})
        if rr.status_code == 200 and rr.text.strip():
            txt = rr.text
            break
    out = []
    for x in (row for row in csv.reader(io.StringIO(txt), delimiter=";") if row):
        rec, start, finish, answer, fe, fn, te, tn, reason = (x + [""] * 9)[:9]
        dur = (int(finish) - int(answer)) if answer and answer != "0" else 0
        # Манго отдаёт метки в UTC, центр живёт по Москве
        t = datetime.datetime.fromtimestamp(int(start) + 3 * 3600).strftime("%H:%M")
        out.append({"rec": rec.strip("[]"), "t": t, "dir": "out" if fe else "in",
                    "phone": tn if fe else fn, "ext": fe or te,
                    "dur": dur, "reason": reason})
    return out


def done_ids() -> set:
    try:
        return set(json.loads(DONE.read_text()))
    except Exception:
        return set()


def mark_done(ids):
    DONE.write_text(json.dumps(sorted(done_ids() | set(ids))))


def transcribe(rows: list[dict]) -> None:
    from faster_whisper import WhisperModel
    m = WhisperModel("small", device="cpu", compute_type="int8")
    ok = []
    for x in rows:
        f = f"/tmp/{x['rec'][:12]}.mp3"
        r = _call("https://app.mango-office.ru/vpbx/queries/recording/post/",
                  {"recording_id": x["rec"], "action": "download"})
        if r.status_code != 200 or len(r.content) < 1000:
            print(x["t"], x["phone"], "запись недоступна", r.status_code, flush=True)
            continue
        pathlib.Path(f).write_bytes(r.content)
        segs, _ = m.transcribe(f, language="ru", vad_filter=True, beam_size=1)
        txt = " ".join(s.text.strip() for s in segs)
        print(f"\n===== {x['t']} МСК {'ИСХ' if x['dir'] == 'out' else 'ВХ'} "
              f"{x['phone']} ({x['dur']}с)\n{txt}\n", flush=True)
        os.remove(f)
        ok.append(x["rec"])
    mark_done(ok)
    print("ГОТОВО, разобрано:", len(ok))


if __name__ == "__main__":
    mins = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 95
    rows = calls(mins)
    done = done_ids()
    for x in rows:
        mark = "уже" if x["rec"] in done else ("НОВ" if x["dur"] >= TALK_MIN and x["rec"] else "")
        print(f'{x["t"]} {"ИСХ" if x["dir"] == "out" else "ВХ "} {x["phone"]:12s} '
              f'{x["dur"]:4d}с reason={x["reason"]} {mark}')
    new = [x for x in rows if x["dur"] >= TALK_MIN and x["rec"] and x["rec"] not in done]
    print(f"\nвсего строк {len(rows)}, к разбору {len(new)}")
    if new and "--list" not in sys.argv:
        transcribe(new)
