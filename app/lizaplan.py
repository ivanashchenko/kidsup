"""Разбор задач Лизы — по другим правилам, чем у звонящих.

Почему отдельный модуль. У Иры, Ани и Лены в списке однотипная работа:
позвонить и записать. Там дубль — это просто лишний пункт, его достаточно
закрыть. У Лизы иначе: она ведёт переписку и деньги, и по одному клиенту
у неё копится ЦЕПОЧКА — «клиент написал», «клиент ждёт ответа», «обещали
расчёт», «проверить оплату». Каждая задача правдива, но по отдельности
бессмысленна: чтобы понять, что делать, надо прочитать все четыре.

Пример из живого списка. Пушкарёва Есения — четыре задачи: «готовы
оплатить», «расчёт отправлен вчера?», «её вопрос про мастер-класс без
ответа», «клиент ждёт ответа». Закрыть три из них — потерять смысл.
Правильно — свести в одну, где видно всю историю и один следующий шаг.

Что делает разбор:

  СВЕСТИ    несколько задач по одному клиенту → одна сводная с историей
            по датам и одним следующим шагом; остальные закрываются
            ссылкой на неё;
  ОСТЫЛО    «отреагировал на рассылку смайликом» недельной давности —
            это уже не горячий сигнал; закрываем, а клиента возвращаем
            в обычный обзвон;
  ЗАКРЫТЬ   клиент уже записан, а задача не про деньги;
  ОСТАВИТЬ  деньги, свежая переписка, организационное.

Запуск:
    python -m app.lizaplan show    — показать решения
    python -m app.lizaplan apply   — применить
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from datetime import date, timedelta

from . import sync
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.lizaplan")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"

LIZA = 154181
CALL_RING = [232763, 232805, 202856]     # кому отдать остывшие реакции

MONEY = re.compile(
    r"оплат|возврат|чек|долг|абонемент|счёт|счет|баланс|соцфонд|маткапитал|"
    r"рассроч|платёж|платеж|компенсац", re.I)
# Реакция на рассылку: смайлик, сердечко, лайк. Живёт несколько дней.
REACTION = re.compile(r"смайлик|сердечк|лайк|отреагировал", re.I)
COLD_AFTER = 5                            # дней, после которых реакция остыла


def _client() -> MoyklassClient:
    return MoyklassClient(sync.get_api_key())


def _next_step(bodies: list[str]) -> str:
    """Один следующий шаг из цепочки. Приоритет: деньги → ответ → остальное."""
    for b in bodies:
        if MONEY.search(b):
            return "закрыть денежный вопрос"
    for b in bodies:
        if re.search(r"ждёт ответ|ответа нет|написал", b, re.I):
            return "ответить в чате и назначить следующий шаг"
    return "связаться и довести до результата"


def decide() -> list[dict]:
    ts = json.load(open(f"{SP}/liza2.json"))
    tp = json.load(open(f"{SP}/taskplan.json"))
    clients = tp["clients"]
    today = date.today()
    cold_edge = (today - timedelta(days=COLD_AFTER)).isoformat()

    by_user: dict[int, list] = defaultdict(list)
    for t in ts:
        if t.get("userId"):
            by_user[t["userId"]].append(t)

    out = []
    for t in ts:
        uid = t.get("userId")
        body = (t.get("body") or "").strip()
        created = (t.get("createdAt") or "")[:10]
        c = clients.get(str(uid), {}) if uid else {}
        mine = by_user.get(uid, []) if uid else []
        act, why, extra = "оставить", "", None

        # 1. остывшая реакция на рассылку — уже не горячий сигнал
        if REACTION.search(body) and created and created < cold_edge:
            act = "остыло"
            why = f"реакция от {created[8:10]}.{created[5:7]} — прошло больше "\
                  f"{COLD_AFTER} дней"

        # 2. клиент уже записан, а задача не про деньги
        elif c.get("enrolled") and not MONEY.search(body):
            act = "закрыть"
            why = "уже записан: " + (c["enrolled"][0][:38])

        # 3. цепочка по одному клиенту — свести в одну
        elif len(mine) >= 2:
            keep = max(mine, key=lambda x: (x.get("createdAt") or "", x["id"]))
            if t["id"] == keep["id"]:
                chain = sorted(mine, key=lambda x: (x.get("createdAt") or ""))
                step = _next_step([x.get("body") or "" for x in chain])
                name = (c.get("name") or f"клиент {uid}")[:24]
                phone = c.get("phone") or ""
                head = f"📋 {name}" + (f" +7{phone}" if phone else "")
                tail = f" СДЕЛАТЬ: {step}."
                # В тело задачи влезает 250 символов. Считаем, сколько
                # осталось под историю, и берём последние события — они
                # важнее первых: по ним видно, на чём всё встало.
                room = 250 - len(head) - len(tail) - len(". Было: ")
                events = []
                for x in reversed(chain):
                    d = (x.get("createdAt") or "")[:10]
                    txt = re.sub(r"^[🔥⚠️🤖📞💰\s]+", "",
                                 (x.get("body") or "")).replace("\n", " ")
                    txt = re.sub(r"\s{2,}", " ", txt).strip()
                    piece = f"{d[8:10]}.{d[5:7]} {txt[:58]}"
                    if sum(len(e) + 2 for e in events) + len(piece) > room:
                        break
                    events.insert(0, piece)
                if not events:
                    events = [f"{len(chain)} задач по клиенту"]
                act = "свести"
                why = f"цепочка из {len(mine)} задач"
                extra = (head + ". Было: " + "; ".join(events) + "." + tail)[:250]
            else:
                act = "закрыть"
                why = f"сведено в задачу {keep['id']}"

        out.append({"id": t["id"], "uid": uid, "name": c.get("name"),
                    "phone": c.get("phone"), "created": created,
                    "act": act, "why": why, "new_body": extra,
                    "body": body[:110]})
    json.dump(out, open(f"{SP}/liza_decisions.json", "w"), ensure_ascii=False)
    return out


def apply() -> dict:
    dec = json.load(open(f"{SP}/liza_decisions.json"))
    mk = _client()
    stat: dict[str, int] = defaultdict(int)
    ring = 0
    try:
        for it in dec:
            if it["act"] == "оставить":
                stat["оставлено"] += 1
                continue
            try:
                t = mk.get(f"/v1/company/tasks/{it['id']}")
            except Exception:
                stat["ошибка"] += 1
                continue
            if t.get("isComplete") or t.get("isCompleted"):
                continue
            b = {k: t.get(k) for k in ("categoryId", "userId", "classIds",
                                       "filialIds", "ownerId", "reminds")}
            b = {k: v for k, v in b.items() if v is not None}
            b["isAllDay"] = False
            body = t.get("body") or ""

            if it["act"] == "закрыть":
                b["body"] = f"[закрыто: {it['why']}] {body}"[:250]
                b["beginDate"] = t.get("beginDate")
                b["endDate"] = t.get("endDate")
                b["managerIds"] = t.get("managerIds") or [LIZA]
                b["isComplete"] = True
            elif it["act"] == "свести":
                b["body"] = it["new_body"] or body[:250]
                b["beginDate"] = t.get("beginDate")
                b["endDate"] = t.get("endDate")
                b["managerIds"] = [LIZA]
            else:                                   # остыло → в обзвон
                b["body"] = ("📞 " + re.sub(r"^[🔥⚠️🤖\s]+", "", body)
                             + " Реакция остыла — звонить как обычному "
                               "контакту, звать на 29–30.08.")[:250]
                b["managerIds"] = [CALL_RING[ring % len(CALL_RING)]]
                ring += 1
                b["categoryId"] = 104576
                day = date.today().isoformat()
                b["beginDate"] = f"{day}T14:00:00+03:00"
                b["endDate"] = f"{day}T20:00:00+03:00"
            try:
                mk.post(f"/v1/company/tasks/{it['id']}", b)
                stat[it["act"]] += 1
            except Exception:
                stat["ошибка"] += 1
    finally:
        mk.close()
    return dict(stat)


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "apply":
        print(apply())
    else:
        dec = decide()
        from collections import Counter
        print("решения:", dict(Counter(x["act"] for x in dec)))
        for act in ("свести", "остыло", "закрыть"):
            sel = [x for x in dec if x["act"] == act]
            if not sel:
                continue
            print(f"\n=== {act.upper()}: {len(sel)}")
            for x in sel[:4]:
                print(f"   {str(x['name'])[:22]:22s} {x['why']}")
                if x.get("new_body"):
                    print(f"      → {x['new_body'][:150]}")


if __name__ == "__main__":
    main()
