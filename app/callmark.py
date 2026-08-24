"""След звонка в карточке клиента — чтобы обзвон был виден в CRM.

Зачем. 23.08 по листу обзвона набрали 56 номеров: 24 разговора, 32 без
разговора. Разговоры разбирались ежечасно и ложились в карточки, а
недозвоны не оставляли следа нигде. Завтра администратор открывает
карточку и не видит, что вчера сюда звонили дважды: ни отметки, ни
статуса, ни задачи. Родителя набирают третий раз подряд, а семья, до
которой не дошли руки, так и лежит без движения.

Что делает. По каждому исходящему за день пишет в карточку строку с
временем, кто звонил и чем кончилось; недозвону ставит статус «Недозвон»
и заводит задачу дежурному на следующий день. Разговоры не трогает —
их разбирает ежечасный проход с расшифровкой, и его запись подробнее.

Запуск:
    python -m app.callmark            — показать, что будет сделано
    python -m app.callmark apply      — записать в CRM
"""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import db, mango, sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.callmark")

# Кто звонит с какого аппарата — в карточке должно быть видно имя, а не
# номер добавочного. Раскладка меняется по сменам (23.08 на доб. 10 была
# Надежда, 24.08 — Ира), поэтому живёт в настройке ext_names, а здесь
# только запасной вариант на случай пустой настройки.
_WHO_DEFAULT = {"10": "Ира", "12": "Лена", "15": "Аня", "20": "Админ Бураковых"}


def _who() -> dict:
    import json as _json
    try:
        raw = db.get_setting("mango_ext_admins", "")
        if raw:
            return {str(k): str(v) for k, v in _json.loads(raw).items()}
    except Exception:
        pass
    return _WHO_DEFAULT


WHO = _who()

ST_NEDOZVON = 345768
# Смена статуса на «отказ» и «новый лид» без причины даёт 400 —
# у этих статусов причина обязательна. Недозвон проходит и без неё.
REASON_NABOR = 313608
# Статусы, которые недозвон перебивать не должен: записавшегося клиента
# нельзя откатывать в недозвон из-за одного неотвеченного звонка, а
# «не писать» и «некачественный» вообще не наши адресаты.
KEEP = {125952, 125954, 125957, 146328}

DUTY_FALLBACK = 232805
TASK_CALL = 104576


def _duty() -> int:
    """Дежурный на сегодня — задача должна лечь на того, кто в смене."""
    try:
        raw = db.get_setting("duty_today", "")
        if raw.isdigit():
            return int(raw)
    except Exception:
        pass
    return DUTY_FALLBACK


def _phone_index() -> dict[str, int]:
    with sqlite3.connect("data/kidsup.db") as conn:
        out = {}
        for uid, phone in conn.execute("SELECT id, phone FROM users WHERE phone IS NOT NULL"):
            p = "".join(ch for ch in str(phone) if ch.isdigit())[-10:]
            if len(p) == 10:
                out.setdefault(p, uid)
        return out


def collect(day: str | None = None) -> list[dict]:
    """Исходящие за день, свёрнутые по номеру собеседника."""
    now = datetime.now()
    d = datetime.strptime(day, "%Y-%m-%d") if day else now.replace(
        hour=0, minute=0, second=0, microsecond=0)
    rows = mango.calls(d, min(d + timedelta(days=1), now))
    agg: dict[str, dict] = defaultdict(
        lambda: {"tries": 0, "talk": 0, "who": set(), "first": 0, "last": 0})
    for r in rows:
        if not r["from_ext"]:
            continue
        num = (r["to_num"] or "")[-10:]
        if len(num) != 10:
            continue
        a = agg[num]
        a["tries"] += 1
        a["who"].add(WHO.get(r.get("ext"), "админ"))
        a["first"] = a["first"] or r["start"]
        a["last"] = r["start"]
        if r["answer"]:
            a["talk"] = max(a["talk"], r["finish"] - r["answer"])
    idx = _phone_index()
    out = []
    for num, a in agg.items():
        out.append({"phone": num, "uid": idx.get(num), **a,
                    "who": sorted(a["who"]),
                    "spoke": a["talk"] >= mango.TALK_MIN})
    return sorted(out, key=lambda x: x["first"])


def _text(c: dict) -> str:
    when = datetime.fromtimestamp(c["first"] + 3 * 3600).strftime("%H:%M")
    who = " и ".join(c["who"])
    if c["spoke"]:
        return (f"Звонок {when} МСК, исходящий ({who}), "
                f"{c['talk'] // 60} мин {c['talk'] % 60:02d} сек.")
    if c["talk"]:
        return (f"Набирали {when} МСК ({who}), попыток {c['tries']}. "
                f"Трубку сняли и сразу положили ({c['talk']} сек) — "
                f"поговорить не удалось.")
    return (f"Набирали {when} МСК ({who}), попыток {c['tries']}. "
            f"Не ответили.")


def apply(day: str | None = None, dry: bool = True) -> dict:
    """Пишет отметки в CRM. dry=True — только показать.

    Разговоры пропускаем: их уже разобрал ежечасный проход, и его запись
    содержит суть и обещания. Дважды писать в одну карточку про один
    звонок — засорять историю."""
    calls = collect(day)
    today = (day or date.today().isoformat())
    mk = MoyklassClient(sync.get_api_key())
    stat = {"seen": len(calls), "comment": 0, "status": 0, "task": 0,
            "skip_spoke": 0, "no_card": 0, "already": 0}
    try:
        cm = mk.get("/v1/company/userComments",
                    {"createdAt": [today, today], "limit": 300})
        marked = {x.get("userId") for x in
                  ((cm.get("userComments") if isinstance(cm, dict) else cm) or [])}
        open_tasks = set()
        for mid in (232805, 154181, 232763, 202856):
            for t in taskguard.all_tasks(mk, mid):
                if not (t.get("isComplete") or t.get("isCompleted")) and t.get("userId"):
                    open_tasks.add(t["userId"])
        duty = _duty()
        for c in calls:
            if c["spoke"]:
                stat["skip_spoke"] += 1
                continue
            if not c["uid"]:
                stat["no_card"] += 1
                continue
            if c["uid"] in marked:
                stat["already"] += 1
                continue
            if dry:
                stat["comment"] += 1
                continue
            mk.post("/v1/company/userComments",
                    {"userId": c["uid"], "comment": _text(c), "showToUser": False})
            stat["comment"] += 1
            cur = mk.get(f"/v1/company/users/{c['uid']}").get("clientStateId")
            if cur not in KEEP and cur != ST_NEDOZVON:
                mk.post(f"/v1/company/users/{c['uid']}/status",
                        {"statusId": ST_NEDOZVON})
                stat["status"] += 1
            # Задача только тем, у кого её ещё нет: у половины листа
            # администратор уже держит свою, и вторая создаст ту же
            # путаницу, из-за которой семье звонят дважды.
            if c["uid"] not in open_tasks:
                nxt = (date.fromisoformat(today) + timedelta(days=1))
                mk.post("/v1/company/tasks", {
                    "userId": c["uid"], "managerIds": [duty],
                    "categoryId": TASK_CALL,
                    "beginDate": f"{nxt}T06:00:00+00:00",
                    "endDate": f"{nxt}T16:00:00+00:00",
                    "body": (f"Не дозвонились {today} ({c['tries']} "
                             f"{'попытка' if c['tries'] == 1 else 'попыток'}). "
                             f"Набрать ещё раз; если снова тишина — написать "
                             f"в переписке.")[:250]})
                open_tasks.add(c["uid"])
                stat["task"] += 1
    finally:
        mk.close()
    return stat


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dry = "apply" not in sys.argv
    calls = collect()
    spoke = [c for c in calls if c["spoke"]]
    print(f"исходящих номеров за день: {len(calls)}")
    print(f"   поговорили      : {len(spoke)}")
    print(f"   без разговора   : {len(calls) - len(spoke)}")
    st = apply(dry=dry)
    print(("ЧТО БУДЕТ" if dry else "СДЕЛАНО") + f": {st}")


if __name__ == "__main__":
    main()
