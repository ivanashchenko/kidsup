"""Финальная доводка задач: дубли, шаблонные тексты, ровная нагрузка.

После разбора и обогащения в списках осталось три вещи, каждая из которых
стоит администратору времени на ровном месте.

  1. ДУБЛИ — 55 задач у 27 клиентов. Часть появилась заново: разбор звонков
     ставит задачу на обещание, а старая задача по тому же человеку остаётся.
     Оставляем одну, самую содержательную, остальные закрываем.

  2. ШАБЛОННЫЕ ТЕКСТЫ — 77 задач вида «Обзвон набора 2026/27: открой карточку,
     прочитай подсказку». Обогащение прошло по первой задаче каждого клиента,
     а вторые и третьи остались как были. Заменяем на строку с именем,
     возрастом, историей и тем, что предлагать.

  3. НЕРОВНАЯ НАГРУЗКА — на 26 августа приходится 139 задач, на 27-е всего 26,
     на 28-е 19. При этом 27-е и 28-е — последние дни перед событиями, когда
     нужны подтверждения приходов. Переносим лишнее с перегруженных дней
     на пустые, не трогая задачи с названным сроком.

Запуск:
    python -m app.taskfinish show
    python -m app.taskfinish apply
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import date

from . import hint, sync
from . import taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.taskfinish")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"

STAFF = {232763: "Ира", 232805: "Аня", 202856: "Лена",
         154181: "Лиза", 84116: "Борис", 229704: "Маша"}
SHIFTS = {232763: ["2026-08-21", "2026-08-25", "2026-08-26", "2026-08-27"],
          232805: ["2026-08-22", "2026-08-23", "2026-08-24", "2026-08-26",
                   "2026-08-27"],
          202856: ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27"],
          154181: ["2026-08-21", "2026-08-22", "2026-08-23", "2026-08-24",
                   "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"]}
CAP = {232763: 45, 232805: 45, 202856: 45, 154181: 40}

TEMPLATE = re.compile(
    r"^(?:\[[^\]]*\]\s*)?(?:⚠️[^.]*\.\s*)?"
    r"(?:Обзвон набора|Продолжение занятий|Продление:|🔥 Летний лагерь)", re.I)
# Задача с названным сроком двигать нельзя — клиенту обещали конкретный день.
HAS_DEADLINE = re.compile(
    r"(?:перезвон\w*|позвон\w*|напомн\w*|подтверд\w*|встрет\w*)[^.]{0,40}?"
    r"\b\d{1,2}[.\-/]?(?:0?[89])?\b", re.I)
# Чем содержательнее текст, тем выше шанс, что задача останется при дедупе.
# «записан» пишется без отрицания намеренно: 21.08 дедуп из-за «НЕ записан»
# счёл старую задачу обзвона содержательнее свежей записи разговора и закрыл
# «мама подтвердила сентябрь», оставив «на новый год НЕ записан» — то есть
# стёр результат состоявшегося звонка и оставил висеть неправду.
RICH = re.compile(r"обещал|просил|ждёт|подтвердил|(?<!не )(?<!НЕ )записан|"
                  r"оплат|📋|📞|перезвонить \d", re.I)


def _weight(t: dict) -> tuple:
    """Какая задача из дублей переживает остальные.

    Свежесть идёт первым разрядом: задача, заведённая сегодня, почти всегда
    несёт итог сегодняшнего разговора, а старая — общий план обзвона.
    Оставить старую значит потерять то, что клиент сказал час назад."""
    body = t.get("body") or ""
    today = date.today().isoformat()
    return (
        (t.get("createdAt") or "")[:10] == today,
        bool(RICH.search(body)),
        len(body),
        t.get("id", 0),
    )


def _mk() -> MoyklassClient:
    return MoyklassClient(sync.get_api_key())


def _subject_of(name: str) -> str | None:
    n = name or ""
    if "_ПШ" in n or n.startswith("ПШ") or "одготовка" in n:
        return "подготовку к школе"
    if "_АЯ" in n or n.startswith("АЯ") or "_ЛК" in n:
        return "английский"
    if "ини-сад" in n or "нулевой" in n.lower():
        return "детский сад"
    if ("МсМ" in n or "узыка и речь" in n or "_РР" in n or n.startswith("РР")
            or "азвити" in n or "ицей" in n):
        return "раннее развитие"
    if "_ЛГ" in n or "огопед" in n:
        return "логопеда"
    if "ШАХ" in n:
        return "шахматы"
    if "ИЗО" in n:
        return "ИЗО"
    return None


def collect(mk: MoyklassClient) -> list[dict]:
    out = []
    for mid in STAFF:
        out += [t for t in taskguard.all_tasks(mk, mid)
                if not t.get("isComplete")
                and not t.get("isCompleted")]
    return list({t["id"]: t for t in out}.values())


def decide(tasks: list[dict]) -> list[dict]:
    try:
        clients = json.load(open(f"{SP}/taskplan.json"))["clients"]
    except Exception:
        clients = {}
    today = date.today().isoformat()
    by_user: dict[int, list] = defaultdict(list)
    for t in tasks:
        if t.get("userId"):
            by_user[t["userId"]].append(t)

    out = []
    for t in tasks:
        uid, body = t.get("userId"), (t.get("body") or "").strip()
        cur = (t.get("beginDate") or "")[:10]
        mine = by_user.get(uid, []) if uid else []
        act, why, new_body = "оставить", "", None

        # 1. дубли: оставляем самую содержательную, при равенстве — свежую
        if len(mine) >= 2:
            best = max(mine, key=_weight)
            if t["id"] != best["id"]:
                act = "закрыть"
                why = f"дубль, работаем по задаче {best['id']}"

        # 2. шаблонный текст — заменить на человеческий
        if act == "оставить" and TEMPLATE.search(body) and uid:
            c = clients.get(str(uid)) or {}
            if c.get("name"):
                was = [s for s in (_subject_of(x)
                                   for x in c.get("was_classes", [])) if s]
                new_body = hint.short(
                    c["name"], birthday=c.get("birthday"), was=was or None,
                    last_visit=c.get("last_visit"))
                act, why = "переписать", "шаблонный текст"

        out.append({"id": t["id"], "uid": uid, "cur": cur,
                    "mgr": (t.get("managerIds") or [None])[0],
                    "act": act, "why": why, "new_body": new_body,
                    "fixed": bool(HAS_DEADLINE.search(body)),
                    "body": body[:90]})

    # 3. выравнивание: снимаем лишнее с перегруженных дней на пустые
    live = [x for x in out if x["act"] != "закрыть"]
    for mid, days in SHIFTS.items():
        mine = [x for x in live if x["mgr"] == mid and x["cur"] in days]
        load = Counter(x["cur"] for x in mine)
        cap = CAP[mid]
        for day in days:
            while load[day] > cap:
                # двигаем только то, чему не назначен срок в тексте
                movable = [x for x in mine
                           if x["cur"] == day and not x["fixed"]
                           and x["act"] in ("оставить", "переписать")]
                target = min((d for d in days if load[d] < cap),
                             key=lambda d: load[d], default=None)
                if not movable or not target:
                    break
                x = movable[-1]
                load[x["cur"]] -= 1
                x["cur"], x["move_to"] = target, target
                load[target] += 1
                if x["act"] == "оставить":
                    x["act"] = "перенести"
                x["why"] = (x["why"] + "; " if x["why"] else "") + \
                    f"выравнивание нагрузки → {target[8:10]}.{target[5:7]}"
    json.dump(out, open(f"{SP}/finish.json", "w"), ensure_ascii=False)
    return out


def apply() -> dict:
    dec = json.load(open(f"{SP}/finish.json"))
    mk = _mk()
    stat: dict[str, int] = defaultdict(int)
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
                                       "filialIds", "ownerId", "reminds",
                                       "managerIds")}
            b = {k: v for k, v in b.items() if v is not None}
            b["isAllDay"] = False
            day = it.get("move_to") or (t.get("beginDate") or "")[:10]
            hour = taskguard.msk_hour(t.get("beginDate"))
            b["beginDate"] = f"{day}T{hour}:00+03:00"
            b["endDate"] = f"{day}T20:00:00+03:00"
            if it["act"] == "закрыть":
                b["body"] = f"[{it['why']}] {t.get('body') or ''}"[:250]
                b["isComplete"] = True
            else:
                b["body"] = (it.get("new_body") or t.get("body") or "")[:250]
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
        return
    mk = _mk()
    try:
        tasks = collect(mk)
    finally:
        mk.close()
    dec = decide(tasks)
    print("задач всего:", len(tasks))
    print("решения:", dict(Counter(x["act"] for x in dec)))
    for act in ("переписать", "закрыть", "перенести"):
        sel = [x for x in dec if x["act"] == act]
        if not sel:
            continue
        print(f"\n=== {act.upper()}: {len(sel)}")
        for x in sel[:4]:
            print(f"   {x['why']}")
            print(f"      было:  {x['body'][:74]}")
            if x.get("new_body"):
                print(f"      стало: {x['new_body'][:74]}")
    load = Counter(x["cur"] for x in dec if x["act"] != "закрыть")
    print("\nнагрузка после выравнивания:", dict(sorted(load.items())[:9]))


if __name__ == "__main__":
    main()
