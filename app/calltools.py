"""Инструменты ежечасного разбора звонков.

Живут в репозитории, а не в scratchpad, потому что временный каталог
контейнера регулярно очищается: за 20.08 он обнулялся шесть раз, и каждый
раз разбор начинался с переписывания одних и тех же скриптов.

Состояние (какие записи уже разобраны) хранится в настройке прода
`calls_done` — она переживает и очистку каталога, и передеплой.

Запуск:
    python -m app.calltools pull 90        — звонки за последние 90 минут
    python -m app.calltools fetch          — скачать записи ≥10 секунд
    python -m app.calltools done           — отметить разобранные
    python -m app.calltools day            — сводка исходящих за день
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from . import db

MSK = ZoneInfo("Europe/Moscow")
API = "https://app.mango-office.ru/vpbx"
PORTAL = "https://app.kidsup.ru"
AUTH = ("admin", os.environ.get("APP_PASSWORD", "CGWstart8*"))

# Добавочные 20 и 21 — «Клуб Буракова», чужой центр в том же здании.
# Их разговоры не наши: не скачиваем, не расшифровываем, не считаем.
FOREIGN = {"20", "21"}
EXT = {"10": "Ира", "12": "Лена", "15": "Аня"}

SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"
os.makedirs(SP, exist_ok=True)


# ── Mango ─────────────────────────────────────────────────────────────────
def _creds() -> tuple[str, str]:
    return db.get_setting("mango_key", ""), db.get_setting("mango_salt", "")


def _post(path: str, payload: dict) -> httpx.Response:
    key, salt = _creds()
    body = json.dumps(payload, separators=(",", ":"))
    sign = hashlib.sha256((key + body + salt).encode()).hexdigest()
    return httpx.post(f"{API}{path}", timeout=180,
                      data={"vpbx_api_key": key, "sign": sign, "json": body})


def pull(minutes: int = 90) -> list[dict]:
    """Журнал звонков за последние N минут.

    ВАЖНО про АТС: один входящий вызов она раскладывает по добавочным, и
    каждая попытка приходит отдельной строкой. Поэтому «строк» и «звонков» —
    разные величины; 17.08 два номера дали 594 строки из 617."""
    now = datetime.now(MSK)
    start = now - timedelta(minutes=minutes)
    r = _post("/stats/request", {
        "date_from": int(start.timestamp()), "date_to": int(now.timestamp()),
        "fields": "records,start,finish,answer,from_extension,from_number,"
                  "to_extension,to_number,disconnect_reason"})
    if r.status_code not in (200, 202):
        raise RuntimeError(f"stats/request: {r.status_code} {r.text[:200]}")
    key = r.json().get("key")
    for _ in range(40):
        time.sleep(4)
        rr = _post("/stats/result", {"key": key})
        if rr.status_code == 200 and rr.text.strip():
            break
    else:
        return []
    rows = []
    for line in rr.text.strip().splitlines():
        p = line.split(";")
        if len(p) < 9:
            continue
        rec, st, fin, ans, fext, fnum, tex, tnum, reason = p[:9]
        if fext in FOREIGN or tex in FOREIGN:
            continue
        try:
            st, fin, ans = int(st or 0), int(fin or 0), int(ans or 0)
        except ValueError:
            continue
        rows.append({"rec": rec.strip("[]"), "start": st, "answer": ans,
                     "finish": fin, "dur": (fin - ans) if ans else 0,
                     "from_ext": fext, "from": fnum, "to_ext": tex, "to": tnum,
                     "reason": reason, "dir": "in" if not fext else "out",
                     "ts": datetime.fromtimestamp(st, MSK).strftime("%H:%M")})
    rows.sort(key=lambda x: x["start"])
    json.dump(rows, open(f"{SP}/calls_raw.json", "w"), ensure_ascii=False)
    return rows


# ── что уже разобрано ─────────────────────────────────────────────────────
def done_ids() -> set[str]:
    """Разобранные записи. Читаем с прода: локальный каталог не переживает
    перезапуск контейнера, а настройка переживает."""
    try:
        r = httpx.get(f"{PORTAL}/api/settings", auth=AUTH, timeout=90)
        return set(json.loads(r.json().get("calls_done") or "[]"))
    except Exception:
        try:
            return set(json.load(open(f"{SP}/calls_done.json")))
        except Exception:
            return set()


def mark_done(ids) -> int:
    cur = done_ids() | {str(i) for i in ids if i}
    out = sorted(cur)
    json.dump(out, open(f"{SP}/calls_done.json", "w"))
    httpx.post(f"{PORTAL}/api/settings", auth=AUTH, timeout=90,
               json={"key": "calls_done", "value": json.dumps(out)})
    return len(out)


# ── записи разговоров ─────────────────────────────────────────────────────
def fetch(rows: list[dict] | None = None, min_dur: int = 10) -> list[dict]:
    """Скачать mp3 разговоров длиннее min_dur, которых ещё нет в разобранных."""
    key, salt = _creds()
    rows = rows if rows is not None else json.load(open(f"{SP}/calls_raw.json"))
    seen = done_ids()
    todo = [r for r in rows if r["dur"] >= min_dur and r["rec"] and r["rec"] not in seen]
    got = []
    for i, r in enumerate(todo):
        phone = (r["from"] if r["dir"] == "in" else r["to"])[-10:]
        who = EXT.get(r["from_ext"] or r["to_ext"], "?")
        name = f"{SP}/r{i}_{phone}_{who}_{r['dir']}_{r['dur']}s.mp3"
        body = json.dumps({"recording_id": r["rec"], "action": "download"},
                          separators=(",", ":"))
        sign = hashlib.sha256((key + body + salt).encode()).hexdigest()
        try:
            resp = httpx.post(f"{API}/queries/recording/post/", timeout=180,
                              follow_redirects=True,
                              data={"vpbx_api_key": key, "sign": sign, "json": body})
            if resp.status_code == 200 and len(resp.content) > 2000:
                open(name, "wb").write(resp.content)
                got.append({**r, "file": name, "phone": phone, "who": who})
        except Exception:
            continue
    json.dump(got, open(f"{SP}/to_transcribe.json", "w"), ensure_ascii=False)
    return got


def transcribe(items: list[dict] | None = None) -> list[dict]:
    """Расшифровка. Пакет faster-whisper в контейнере иногда пропадает —
    ставим на лету, это дешевле, чем ронять разбор."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        os.system("pip install -q faster-whisper")
        from faster_whisper import WhisperModel
    items = items if items is not None else json.load(open(f"{SP}/to_transcribe.json"))
    if not items:
        return []
    m = WhisperModel("small", device="cpu", compute_type="int8")
    for it in items:
        segs, _ = m.transcribe(it["file"], language="ru", vad_filter=True, beam_size=1)
        it["text"] = " ".join(s.text.strip() for s in segs).strip()
        open(it["file"].replace(".mp3", ".txt"), "w").write(it["text"])
    json.dump(items, open(f"{SP}/transcribed.json", "w"), ensure_ascii=False)
    return items


# ── сводка за день ────────────────────────────────────────────────────────
def day_report(rows: list[dict] | None = None) -> dict:
    """Исходящие по администраторам. Отдельно «не взяли трубку» и «взяли и
    сразу разорвали»: первое — про базу, второе — про манеру набирать."""
    rows = rows if rows is not None else json.load(open(f"{SP}/calls_raw.json"))
    st = defaultdict(lambda: {"нет_ответа": 0, "сброс": 0, "разговор": 0,
                              "сек": 0, "входящих": 0, "вх_сек": 0})
    for r in rows:
        if r["dir"] == "out" and r["from_ext"] in EXT:
            k = st[EXT[r["from_ext"]]]
            if not r["answer"]:
                k["нет_ответа"] += 1
            elif r["dur"] < 10:
                k["сброс"] += 1
            else:
                k["разговор"] += 1
                k["сек"] += r["dur"]
        elif r["dir"] == "in" and r["to_ext"] in EXT and r["dur"] >= 10:
            k = st[EXT[r["to_ext"]]]
            k["входящих"] += 1
            k["вх_сек"] += r["dur"]
    return dict(st)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pull"
    if cmd == "pull":
        rows = pull(int(sys.argv[2]) if len(sys.argv) > 2 else 90)
        print(f"строк: {len(rows)}")
        for r in rows:
            who = EXT.get(r["from_ext"] or r["to_ext"], "?")
            ph = (r["from"] if r["dir"] == "in" else r["to"])[-10:]
            print(f"  {r['ts']} {r['dir']:3s} {r['dur']:4d}с {who:4s} {ph:11s} {r['reason']}")
    elif cmd == "fetch":
        got = fetch()
        print(f"скачано: {len(got)}")
        for g in got:
            print(f"  {g['ts']} {g['who']} {g['phone']} {g['dur']}с")
    elif cmd == "transcribe":
        for it in transcribe():
            print(f"━━━ {it['ts']} {it['dir']} {it['phone']} {it['who']} {it['dur']}с ━━━")
            print(it.get("text") or "(тишина)")
            print()
    elif cmd == "done":
        rows = json.load(open(f"{SP}/calls_raw.json"))
        n = mark_done(r["rec"] for r in rows if r["dur"] >= 10)
        print("разобранных записей всего:", n)
    elif cmd == "day":
        for who, v in sorted(day_report().items()):
            tot = v["нет_ответа"] + v["сброс"] + v["разговор"]
            print(f"{who:5s} набрано {tot:3d} → не взяли {v['нет_ответа']:3d} · "
                  f"сброс<10с {v['сброс']:3d} · разговоров {v['разговор']:3d} "
                  f"({v['сек'] // 60} мин) · входящих {v['входящих']} ({v['вх_сек'] // 60} мин)")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
