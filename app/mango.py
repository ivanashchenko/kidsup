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
import time
from datetime import datetime, timedelta

import httpx

from . import db

API = "https://app.mango-office.ru/vpbx/"

# Добавочные ДРУГОГО центра (Люберцы, «Детский клуб Буракова») на общей АТС:
# 20 — их рабочее место, 21 — мобильный их сотрудника. Их звонки не анализируем,
# не показываем в отчётах и не создаём по ним задачи.
FOREIGN_EXTS = {"20", "21"}


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
        rows.append({
            "start": int(p[1] or 0), "finish": int(p[2] or 0), "answer": int(p[3] or 0),
            "from_ext": p[4], "from_num": p[5], "to_ext": p[6], "to_num": p[7],
            "reason": p[8],
        })
    return rows


def _day_calls(day: str | None) -> list[dict]:
    d = datetime.strptime(day, "%Y-%m-%d") if day else datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0)
    return calls(d, d + timedelta(days=1))


def report(day: str | None = None, rows: list[dict] | None = None) -> list[dict]:
    # rows можно передать снаружи: stats/request у Mango с жёстким rate-limit,
    # поэтому историю за день выгружаем один раз и переиспользуем
    rows = rows if rows is not None else _day_calls(day)
    names = users()
    stats: dict[str, dict] = {}
    for r in rows:
        ext = r["from_ext"]
        if not ext:  # входящие без добавочного пропускаем в разрезе исходящих
            continue
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


def missed(day: str | None = None, rows: list[dict] | None = None) -> list[dict]:
    """Недозвоны за день: исходящие без ответа (для WhatsApp-догона)."""
    rows = rows if rows is not None else _day_calls(day)
    seen, answered = {}, set()
    for r in rows:
        if not r["from_ext"]:
            continue
        num = r["to_num"]
        if r["answer"]:
            answered.add(num)
        else:
            seen[num] = seen.get(num, 0) + 1
    return [{"phone": n, "attempts": c} for n, c in seen.items() if n not in answered]


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
