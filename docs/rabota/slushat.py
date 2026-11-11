# -*- coding: utf-8 -*-
"""Прослушивание голосовых: рабочие чаты и переписка с клиентами.

11.11.2026 Борис: «Ты должен всё слушать во всех рабочих чатах и с клиентами».
До этого аудио в чатах оставалось строкой «[audio]»: в тот вечер педагог
наговорила четыре голосовых про неделю открытых уроков (заявка на младшую
сестру ученика, предложение на декабрь, пожелания родителей) — и проверка
чатов прошла мимо со словами «не ко мне».

Что слушаем:
  • пять рабочих групп (/api/gruppy/soobsheniya) — аудио и голосовые; видео
    только в чатах администраторов (в чатах педагогов видео — это занятия
    для сториз, речи для разбора там нет);
  • личную переписку с клиентами (/api/wazzup/raw — последние 300 событий
    вебхука, это примерно сутки): аудио в обе стороны и входящие видео.

Ссылки на файлы ведут на store.wazzup24.com — качаем БЕЗ авторизации: пароль
портала уходит только на app.kidsup.ru (11.11 он один раз ушёл чужому хосту
заголовком — больше так нельзя).

Расшифровки с именами в репозиторий не попадают (он публичный): тексты лежат
в каталоге KIDSUP_AUDIO_DIR, а рядом со скриптом, в audio_done.json, — только
номера уже прослушанных сообщений, чтобы после отката контейнера не слушать
всё заново (так же устроен calls_done.json у разбора звонков).

  KIDSUP_ADMIN_PASS=… python3.13 docs/rabota/slushat.py [--since YYYY-MM-DDTHH:MM]

Печатает по строке «🎧 …» на каждое новое сообщение; если нового нет — молчит.
"""
import datetime
import json
import os
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

import httpx

SERVER = "https://app.kidsup.ru"
AUTH = ("admin", os.environ.get("KIDSUP_ADMIN_PASS", ""))
DIR = pathlib.Path(os.environ.get("KIDSUP_AUDIO_DIR", "/tmp/kidsup_audio"))
TEXTS = DIR / "done.json"
DONE = pathlib.Path(__file__).with_name("audio_done.json")
MSK = datetime.timezone(datetime.timedelta(hours=3))
CHATS = {"4430280960": "Администраторы", "4378505637": "KidsUP Team",
         "5256098255": "Английский", "4368234920": "ПШ",
         "5458121214": "Администраторы new"}
ADMIN_CHATS = {"4430280960", "5458121214"}
AUDIO = ("audio", "voice", "ptt")
GROUPS = ("telegroup", "whatsgroup")
MAX_BYTES = 60 * 1024 * 1024

if not AUTH[1]:
    sys.exit("не задан KIDSUP_ADMIN_PASS")


def _req(method: str, path: str, **kw):
    """Запрос к порталу. Прокси часто рвёт первое соединение — повторяем."""
    for i in range(5):
        try:
            r = httpx.request(method, SERVER + path, auth=AUTH, timeout=90, **kw)
            if r.status_code == 200:
                return r.json()
        except httpx.TransportError:
            pass
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"{method} {path}: сервер не ответил")


def done_ids() -> set:
    try:
        return set(json.loads(DONE.read_text()))
    except Exception:
        return set()


def mark_done(ids: set) -> None:
    DONE.write_text(json.dumps(sorted(ids)[-3000:]))


def texts() -> dict:
    try:
        return json.loads(TEXTS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def download(url: str, path: pathlib.Path) -> int:
    """Файл из хранилища Wazzup. Без Authorization — это не наш хост."""
    if (urllib.parse.urlparse(url).hostname or "").endswith("kidsup.ru"):
        raise ValueError("ссылка на портал — такие файлы здесь не качаем")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("файл больше 60 МБ")
    path.write_bytes(data)
    return len(data)


def card_name(chat_id: str) -> str:
    digits = re.sub(r"\D", "", chat_id)[-10:]
    if len(digits) != 10:
        return ""
    try:
        r = _req("GET", "/api/crm/poisk", params={"q": digits})
        cards = r.get("карточки") or []
        return cards[0]["имя"] if cards else ""
    except Exception:
        return ""


def collect(since: str) -> dict:
    out = {}
    for cid, name in CHATS.items():
        try:
            msgs = _req("GET", "/api/gruppy/soobsheniya",
                        params={"chat": cid, "since": since, "limit": 300}).get("messages") or []
        except Exception as e:                                  # noqa: BLE001
            print(f"{name}: {e}", file=sys.stderr)
            continue
        for m in msgs:
            kind = m.get("kind")
            if not m.get("media_url"):
                continue
            if kind in AUDIO or (kind == "video" and cid in ADMIN_CHATS):
                out[m["message_id"]] = {
                    "ts": m["ts"], "where": name, "kind": kind, "url": m["media_url"],
                    "who": m.get("author") or m.get("username") or ("мы" if m.get("is_echo") else "")}
    try:
        events = _req("GET", "/api/wazzup/raw", params={"limit": 300}).get("events") or []
    except Exception as e:                                      # noqa: BLE001
        print(f"вебхук: {e}", file=sys.stderr)
        events = []
    for e in events:
        if e["ts"][:16] < since:
            continue
        try:
            body = json.loads(e["body"])
        except Exception:
            continue
        for m in body.get("messages") or []:
            if not isinstance(m, dict) or not m.get("contentUri") or m.get("messageId") in out:
                continue
            ct, kind, echo = str(m.get("chatType") or "").lower(), m.get("type"), bool(m.get("isEcho"))
            if ct in GROUPS:
                cid = str(m.get("chatId"))
                if not (kind in AUDIO or (kind == "video" and cid in ADMIN_CHATS)):
                    continue
                where, who = CHATS.get(cid, f"группа {cid}"), ("мы" if echo else "")
            else:
                # наши видео клиентам — это ролики с занятий, их слушать незачем
                if not (kind in AUDIO or (kind == "video" and not echo)):
                    continue
                cid = str(m.get("chatId") or "")
                name = card_name(cid) or str((m.get("contact") or {}).get("name") or "")
                where = f"клиент · {ct} · …{cid[-4:]}" + (f" · {name}" if name else " · карточки нет")
                who = "мы → клиенту" if echo else "клиент"
            out[m["messageId"]] = {"ts": e["ts"], "where": where, "kind": kind,
                                   "url": m["contentUri"], "who": who}
    return out


def main() -> None:
    if "--since" in sys.argv:
        since = sys.argv[sys.argv.index("--since") + 1]
    else:
        since = (datetime.datetime.now(MSK) - datetime.timedelta(hours=26)).strftime("%Y-%m-%dT%H:%M")
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / "dl").mkdir(exist_ok=True)
    saved = texts()
    seen = done_ids() | set(saved)
    todo = {k: v for k, v in collect(since).items() if k not in seen}
    if not todo:
        return
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8")
    for mid, it in sorted(todo.items(), key=lambda x: x[1]["ts"]):
        path = DIR / "dl" / (re.sub(r"[^0-9a-zA-Z-]", "", mid) + (".mp4" if it["kind"] == "video" else ".mp3"))
        try:
            download(it["url"], path)
            # на английском педагоги могут говорить по-английски — язык определяем сами
            lang = None if it["where"] == "Английский" else "ru"
            segs, info = model.transcribe(str(path), language=lang, vad_filter=True, beam_size=5)
            it["text"] = " ".join(s.text.strip() for s in segs).strip()
            it["dur"] = round(info.duration)
        except Exception as e:                                  # noqa: BLE001
            it["text"], it["err"] = "", f"{type(e).__name__}: {e}"[:200]
        if it.get("err"):
            # не помечаем прослушанным: ссылка могла не открыться с первого раза
            print(f"🎧 {it['ts'][5:16]} · {it['where']} · {it['who']} · {it['kind']}: "
                  f"НЕ ПРОСЛУШАНО — {it['err']}", flush=True)
            continue
        saved[mid] = {k: v for k, v in it.items() if k != "url"}
        TEXTS.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
        seen.add(mid)
        body = it["text"] or "(речи нет)"
        print(f"🎧 {it['ts'][5:16].replace('T', ' ')} · {it['where']} · {it['who']} · "
              f"{it['kind']} {it.get('dur', '?')} с: {body}", flush=True)
    mark_done(seen)


if __name__ == "__main__":
    main()
