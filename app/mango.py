"""Mango Office: отчёт по звонкам администраторов.

Считает по каждому сотруднику (добавочному): попытки, дозвоны, минуты
разговора, уникальные номера. Основа вечерней сводки обзвона.

Запуск:
    python -m app.mango users                    — сотрудники АТС
    python -m app.mango report                   — отчёт за сегодня
    python -m app.mango report --date 2026-08-10
"""

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timedelta

import httpx

from . import db

API = "https://app.mango-office.ru/vpbx/"

# Добавочные ДРУГОГО центра (Люберцы, «Детский клуб Буракова») на общей АТС:
# 20 — их рабочее место, 21 — мобильный их сотрудника. Их звонки не анализируем,
# не показываем в отчётах и не создаём по ним задачи.
FOREIGN_EXTS = {"21"}

# Аппарат в CDR надёжнее опознаётся по SIP-логину, чем по from_extension.
# 23.08 звонки трубки 10 (мама, обзвон по листу) поехали в отчёт с добавочным
# 12: в статистике вышло, что один администратор ведёт два разговора
# одновременно, а трубка 10 за день не набрала никого. Логин при этом
# оставался свой — user1, — и по нему потоки разделяются точно.
# Соответствие снято с 16-20.08, когда добавочный ещё проставлялся верно.
SIP_EXT = {"user1": "10", "user5": "12", "user3": "15", "user2": "20"}
_SIP_RE = re.compile(r"sip:(user\d+)@")


def _sip_login(num: str) -> str:
    m = _SIP_RE.search(num or "")
    return m.group(1) if m else ""
# 20 — Надежда Иванащенко, звонит по нашей базе из Клуба Буракова.
PARTNER_EXTS = {"20"}


def _call(path: str, payload: dict) -> httpx.Response:
    key = db.get_setting("mango_key")
    salt = db.get_setting("mango_salt")
    j = json.dumps(payload)
    sign = hashlib.sha256((key + j + salt).encode()).hexdigest()
    return httpx.post(API + path, data={"vpbx_api_key": key, "sign": sign, "json": j},
                      timeout=60)


def users() -> dict[str, str]:
    """extension -> имя сотрудника."""
    r = _call("config/users/request", {})
    out = {}
    for u in r.json().get("users", []):
        ext = (u.get("telephony") or {}).get("extension")
        if ext:
            out[str(ext)] = u["general"]["name"]
    return out


def calls(date_from: datetime, date_to: datetime) -> list[dict]:
    """История звонков через stats/request → stats/result (CSV)."""
    req = _call("stats/request", {
        "date_from": str(int(date_from.timestamp())),
        "date_to": str(int(date_to.timestamp())),
        "fields": "records, start, finish, answer, from_extension, from_number, "
                  "to_extension, to_number, disconnect_reason",
    })
    key = req.json().get("key")
    if not key:
        raise RuntimeError(f"stats/request: {req.text[:200]}")
    for _ in range(20):
        res = _call("stats/result", {"key": key})
        if res.status_code == 200 and res.text.strip():
            break
        time.sleep(2)  # 204 — отчёт ещё готовится
    else:
        raise RuntimeError("stats/result: отчёт не готов")
    rows = []
    for line in res.text.strip().splitlines():
        p = [x.strip("[]") for x in line.split(";")]
        if len(p) < 9:
            continue
        if p[4] in FOREIGN_EXTS or p[6] in FOREIGN_EXTS:
            continue  # звонки другого центра (Люберцы) — не наши
        # Добавочный 20 раньше считался чужим целиком. С 21.08 с него звонит
        # Надежда Иванащенко (Клуб Буракова, Люберцы) по НАШЕЙ базе, и такие
        # звонки — наши: их надо разбирать и заводить записи. Чей звонок,
        # решает не добавочный, а собеседник: есть ли он в нашей CRM.
        sip = _sip_login(p[5] if p[4] else p[7])
        rows.append({
            "start": int(p[1] or 0), "finish": int(p[2] or 0), "answer": int(p[3] or 0),
            "from_ext": p[4], "from_num": p[5], "to_ext": p[6], "to_num": p[7],
            "reason": p[8], "sip": sip, "ext": SIP_EXT.get(sip) or p[4],
            # идентификатор записи разговора: без него ежечасный разбор не
            # может скачать mp3 и сверяться с реестром уже обработанных
            "rec": p[0],
        })
    return rows


def _day_calls(day: str | None) -> list[dict]:
    now = datetime.now()
    d = datetime.strptime(day, "%Y-%m-%d") if day else now.replace(
        hour=0, minute=0, second=0, microsecond=0)
    # Манго отвергает date_to в будущем («must be not more than the current
    # date»), поэтому для текущего дня границей служит сейчас, а не полночь
    # следующего. Раньше сводка за сегодня просто падала с 400.
    return calls(d, min(d + timedelta(days=1), now))


def report(day: str | None = None, rows: list[dict] | None = None) -> list[dict]:
    # rows можно передать снаружи: stats/request у Mango с жёстким rate-limit,
    # поэтому историю за день выгружаем один раз и переиспользуем
    rows = rows if rows is not None else _day_calls(day)
    names = users()
    stats: dict[str, dict] = {}
    for r in rows:
        if not r["from_ext"]:  # входящие в разрезе исходящих не считаем
            continue
        ext = r.get("ext") or r["from_ext"]
        s = stats.setdefault(ext, {
            "admin": names.get(ext, f"доб. {ext}"), "attempts": 0, "answered": 0,
            "talk_sec": 0, "numbers": set()})
        s["attempts"] += 1
        s["numbers"].add(r["to_num"])
        if r["answer"]:
            s["answered"] += 1
            s["talk_sec"] += max(0, r["finish"] - r["answer"])
    out = []
    for s in stats.values():
        s["unique"] = len(s.pop("numbers"))
        s["talk_min"] = round(s.pop("talk_sec") / 60, 1)
        out.append(s)
    return sorted(out, key=lambda x: -x["attempts"])


# Сколько секунд разговора считать состоявшимся контактом. Ниже этого
# человек успевает сказать «алло» и сбросить — поговорить мы не поговорили.
TALK_MIN = 20


def missed(day: str | None = None, rows: list[dict] | None = None) -> list[dict]:
    """Недозвоны за день: с кем так и не поговорили (для WhatsApp-догона).

    Раньше достаточно было факта ответа: answer != 0 — значит дозвонились,
    догон не нужен. 23.08 по листу обзвона так выпали 22 семьи из 32: они
    сняли трубку на шесть-семь секунд и сбросили, и в отчёте это выглядело
    как разговор. Ни звонка, ни сообщения им в тот день не досталось.
    Теперь короткий сброс — тоже недозвон."""
    rows = rows if rows is not None else _day_calls(day)
    seen: dict[str, int] = {}
    talked = set()
    for r in rows:
        if not r["from_ext"]:
            continue
        num = r["to_num"]
        if r["answer"] and (r["finish"] - r["answer"]) >= TALK_MIN:
            talked.add(num)
        else:
            seen[num] = seen.get(num, 0) + 1
    return [{"phone": n, "attempts": c} for n, c in seen.items() if n not in talked]


def main():
    ap = argparse.ArgumentParser(description="Отчёт по звонкам Mango")
    ap.add_argument("command", choices=["users", "report", "missed"])
    ap.add_argument("--date", default=None)
    args = ap.parse_args()

    if args.command == "users":
        for ext, name in users().items():
            print(f"доб. {ext:4s} {name}")
    elif args.command == "report":
        rows = report(args.date)
        if not rows:
            print("Звонков нет.")
        print(f"{'администратор':30s} {'попыток':>8s} {'дозвонов':>9s} {'уник.':>6s} {'мин':>7s}")
        for s in rows:
            print(f"{s['admin'][:30]:30s} {s['attempts']:8d} {s['answered']:9d} "
                  f"{s['unique']:6d} {s['talk_min']:7.1f}")
    elif args.command == "missed":
        for m in missed(args.date):
            print(f"{m['phone']}  попыток: {m['attempts']}")


if __name__ == "__main__":
    main()


# --- реестр разобранных записей ------------------------------------------
# Лежал в /tmp и исчезал при каждом перезапуске контейнера: 23.08 это
# случилось трижды, и после каждого раза ежечасный разбор был готов заново
# скачать те же разговоры и второй раз написать те же комментарии в
# карточки клиентов. Реестр живёт в той же базе, что и остальное состояние.
_DONE_KEY = "calls_parsed"
_DONE_MAX = 4000  # хватает на пару недель; старые записи всё равно не всплывут


def parsed() -> set[str]:
    """Записи, которые ежечасный разбор уже обработал."""
    from . import db
    return {x for x in db.get_setting(_DONE_KEY, "").split(",") if x}


def mark_parsed(recs) -> int:
    """Добавляет записи в реестр, отдаёт его новый размер."""
    from . import db
    cur = sorted(parsed() | {r for r in recs if r})[-_DONE_MAX:]
    db.set_setting(_DONE_KEY, ",".join(cur))
    return len(cur)
