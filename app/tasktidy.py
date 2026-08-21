"""Порядок в задачах МойКласс — по правилам, которые назвала Ира 21.08.

Она работала со списком из 160+ задач и сформулировала пять вещей, каждая
из которых стоит ей времени каждый день:

  1. Задача сделана, в комментарии «написала в чат» — а ответа нет. Такая
     задача не должна висеть на сегодня: её надо двигать на вечер или
     на завтра, потому что делать по ней сейчас нечего.
  2. Задачи про оплаты — Лизе, не звонящим администраторам.
  3. Задача, которую уже переносили, на следующий день идёт ПЕРВОЙ,
     а не уходит в хвост: иначе она кочует неделями.
  4. По клиенту, помеченному «некачественный», задачу предлагать не надо.
  5. Если в тексте задачи стоит срок («перезвонить 24-го»), задача должна
     стоять на эту дату, а не на сегодня.

Пятое — следствие моего массового переноса 21.08: я разложил задачи по дням
смен, не посмотрев, что у части из них уже был осмысленный срок внутри текста.
41 задача уехала не туда. Здесь это лечится и больше не повторится.

Запуск:
    python -m app.tasktidy check    — что не так, без изменений
    python -m app.tasktidy apply    — привести в порядок
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from .moyklass_client import MoyklassClient
from . import sync
from . import taskguard

log = logging.getLogger("kidsup.tasktidy")

CHAT_ADMIN = 154181                       # Лиза — деньги, переписка, документы
CALL_ADMINS = {232763, 232805, 202856}    # Ира, Аня, Лена — только запись в группы
NAMES = {84116: "Борис", 154181: "Лиза", 202856: "Лена",
         229704: "Маша", 232763: "Ира", 232805: "Аня"}

# Клиент, которому мы сознательно не звоним: задача по нему бессмысленна.
# «Архив набора» (345759) сюда НЕ входит намеренно: этот статус мы ставили
# массово сами, и под ним лежит тёплая база прошлого года — та самая, которую
# сейчас обзваниваем. Закрывать по нему задачи значит выкинуть работу.
DEAD_STATES = {125954, 125957, 146328}

MONEY = re.compile(
    r"абонемент|не оплач|оплат|долг|чек|возврат|счёт|счет|баланс|платёж|платеж|"
    r"соцфонд|маткапитал|касс|рассроч|скидк|компенсац", re.I)

# «Написала в чат, жду ответа» — делать сейчас нечего, ждём человека.
WAITING = re.compile(
    r"написал[аи]? в (чат|whatsapp|telegram|max)|ответа нет|ждём ответ|ждем ответ|"
    r"жду ответ|отправил[аи]? (сообщение|информацию)|написано в чат", re.I)

# Явный срок внутри текста: рядом с датой стоит действие. «До 30.08 сентябрь
# по старым ценам» — это скрипт продажи, а не срок, и не ловится.
_VERB = (r"(?:перезвон\w*|позвон\w*|созвон\w*|напомн\w*|связаться|набрать|"
         r"написать|выслать|прислать|отправить|проверить|подтвердить|ждём|ждёт)")
DEADLINE = [
    re.compile(_VERB + r"[^.]{0,40}?\b(\d{1,2})[.\-/](0?[89])\b", re.I),
    re.compile(_VERB + r"[^.]{0,40}?\bна\s+(\d{1,2})\s*(?:-?е|число|числа)?\b", re.I),
    re.compile(_VERB + r"[^.]{0,40}?\b(\d{1,2})\s*-?\s*(?:го|е)\b", re.I),
    re.compile(r"\b(\d{1,2})[.\-/](0?[89])\b[^.]{0,25}?" + _VERB, re.I),
]
# Мои служебные пометки, которые засоряют текст при переносах.
LABEL = re.compile(r"\[(?:перенесено в сентябрь[^\]]*|в сентябрь[^\]]*|"
                   r"передано с [^\]]*)\]\s*")
# Сколько раз задачу уже двигали — считаем по этой метке в тексте.
CARRIED = re.compile(r"\(перенос ×(\d+)\)")


def deadline_in_text(body: str, year: int = 2026) -> str | None:
    """Срок, названный внутри задачи. None, если срока нет."""
    for rx in DEADLINE:
        m = rx.search(body or "")
        if not m:
            continue
        g = m.groups()
        try:
            day = int(g[0])
            mon = int(g[1]) if len(g) > 1 and g[1] else 8
        except (TypeError, ValueError):
            continue
        if 1 <= day <= 31 and mon in (8, 9):
            return f"{year}-{mon:02d}-{day:02d}"
    return None


def _client() -> MoyklassClient:
    return MoyklassClient(sync.get_api_key())


def _open_tasks(mk: MoyklassClient, managers) -> list[dict]:
    out = []
    for mid in managers:
        off = 0
        while off < 1200:
            r = mk.get("/v1/company/tasks",
                       {"date": date.today().isoformat(), "limit": 100,
                        "offset": off, "managerId": mid})
            ts = r.get("tasks") or []
            if not ts:
                break
            out += [t for t in ts
                    if not t.get("isComplete") and not t.get("isCompleted")]
            off += 100
    return out


def plan(dry: bool = True) -> dict:
    """Разобрать задачи по пяти правилам. dry=True — только показать."""
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    mk = _client()
    acts = {"срок из текста": [], "оплаты → Лизе": [], "ждём ответа": [],
            "клиент неактуален": [], "перенос ×N — вперёд": []}
    states: dict[int, int] = {}
    try:
        tasks = _open_tasks(mk, list(CALL_ADMINS) + [CHAT_ADMIN])
        for t in tasks:
            body = t.get("body") or ""
            cur = (t.get("beginDate") or "")[:10]
            mgrs = t.get("managerIds") or []
            uid = t.get("userId")
            new_day, new_mgr, note = None, None, None

            # 5. срок внутри текста важнее любой раскладки по дням
            want = deadline_in_text(body)
            if want and want != cur and want >= today:
                new_day, note = want, "срок из текста"

            # 2. деньги — не работа звонящего администратора
            if MONEY.search(body) and any(m in CALL_ADMINS for m in mgrs):
                new_mgr, note = CHAT_ADMIN, "оплаты → Лизе"

            # 1. написали в чат и ждём — сегодня делать нечего
            if not new_day and WAITING.search(body) and cur <= today:
                new_day, note = tomorrow, "ждём ответа"

            # 4. клиент, которому мы не звоним
            if uid:
                if uid not in states:
                    try:
                        states[uid] = mk.get(f"/v1/company/users/{uid}") \
                            .get("clientStateId")
                    except Exception:
                        states[uid] = None
                if states[uid] in DEAD_STATES:
                    acts["клиент неактуален"].append(
                        {"id": t["id"], "body": body[:80],
                         "state": states[uid], "action": "закрыть"})
                    continue

            if note:
                acts[note].append({"id": t["id"], "cur": cur, "day": new_day,
                                   "mgr": new_mgr, "body": body[:80]})
            # 3. переносившаяся задача идёт первой в своём дне
            m = CARRIED.search(body)
            if m and int(m.group(1)) >= 1 and taskguard.msk_hour(t.get("beginDate")) > "09:00":
                acts["перенос ×N — вперёд"].append(
                    {"id": t["id"], "cur": cur, "body": body[:80]})
        log.info("tasktidy: %s", {k: len(v) for k, v in acts.items()})
        if dry:
            return acts
        # применяем
        applied = 0
        for kind, items in acts.items():
            for it in items:
                try:
                    t = mk.get(f"/v1/company/tasks/{it['id']}")
                except Exception:
                    continue
                b = {k: t.get(k) for k in ("categoryId", "userId", "classIds",
                                           "filialIds", "ownerId", "reminds")}
                b = {k: v for k, v in b.items() if v is not None}
                body = LABEL.sub("", t.get("body") or "").strip()
                b["managerIds"] = ([it["mgr"]] if it.get("mgr")
                                   else (t.get("managerIds") or []))
                b["isAllDay"] = False
                if it.get("action") == "закрыть":
                    b["body"] = body[:250]
                    b["beginDate"] = t.get("beginDate")
                    b["endDate"] = t.get("endDate")
                    b["isComplete"] = True
                else:
                    day = it.get("day") or (t.get("beginDate") or "")[:10]
                    n = int((CARRIED.search(body) or [0, 0])[1] or 0) + \
                        (1 if it.get("day") else 0)
                    if n and not CARRIED.search(body):
                        body = f"{body} (перенос ×{n})"
                    elif n:
                        body = CARRIED.sub(f"(перенос ×{n})", body)
                    hour = "09:00" if n else taskguard.msk_hour(t.get("beginDate"))
                    b["body"] = body[:250]
                    b["beginDate"] = f"{day}T{hour}:00+03:00"
                    b["endDate"] = f"{day}T20:00:00+03:00"
                try:
                    mk.post(f"/v1/company/tasks/{it['id']}", b)
                    applied += 1
                except Exception:
                    pass
        acts["_применено"] = applied
        return acts
    finally:
        mk.close()


def main():
    import json
    import sys
    dry = (sys.argv[1] if len(sys.argv) > 1 else "check") != "apply"
    r = plan(dry=dry)
    for k, v in r.items():
        if k.startswith("_"):
            print(f"{k}: {v}")
            continue
        print(f"\n{k}: {len(v)}")
        for x in v[:6]:
            print("   ", json.dumps(x, ensure_ascii=False)[:150])


if __name__ == "__main__":
    main()
