"""Четыре доводки задач, которые видно только на объёме.

Найдено аудитом 22.08 на 439 открытых задачах.

  1. ЦЕЛЬ УЖЕ ДОСТИГНУТА. Десять задач «позвонить и предложить» стоят
     по клиентам, которые УЖЕ записались. Администратор звонит и предлагает
     то, на что человек записан, — выглядит так, будто мы не помним своих
     же клиентов. Но закрывать не всегда верно: половина записана на ДРУГОЕ,
     и это не мёртвая задача, а допродажа второго предмета со скидкой.

  2. СРОЧНОСТЬ ОБЕСЦЕНЕНА. Каждая четвёртая задача помечена «срочно» —
     103 из 439, и половина из них висит третьи сутки. Срочность, которая
     не сработала за три дня, срочностью не была: она приучает не реагировать
     на красную метку вообще, и тогда настоящая срочная задача тонет.

  3. ДОЛГОЖИТЕЛИ. 68 задач старше восьми дней, 46 из них — «позвонить».
     Восемь дней звонков без результата означают не «надо позвонить ещё
     раз», а «телефон не работает, пробуй другой канал».

  4. ЗАДАЧА БЕЗ КАРТОЧКИ. 118 задач не привязаны к клиенту, у 22 из них
     телефон есть прямо в тексте. Непривязанная задача не показывает
     историю, и админ звонит вслепую.

Запуск:
    python -m app.tasksmart show
    python -m app.tasksmart apply
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter, defaultdict
from datetime import date

from . import sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.tasksmart")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"

STAFF = {232763: "Ира", 232805: "Аня", 202856: "Лена",
         154181: "Лиза", 84116: "Борис", 229704: "Маша"}
ACTIVE = {2, 50509, 58131, 58132, 83760}
CAT_URGENT, CAT_CALL, CAT_CHAT, CAT_PUSH = 44337, 104576, 104575, 104577

CALL_TASK = re.compile(r"обзвон|позвонить|перезвонить|📞|предложить|дожать|"
                       r"пригласи|набрать", re.I)
PHONE_IN_TEXT = re.compile(r"\+7(\d{10})")
# Сколько дней срочность остаётся срочностью.
URGENT_LIVES = 3
# После скольких дней в звонках менять канал.
CALL_GIVES_UP = 8


def _subject(name: str) -> str:
    n = name or ""
    if re.search(r"_ПШ|^ПШ|одготовк", n, re.I):
        return "подготовка к школе"
    if re.search(r"нулев", n, re.I):
        return "нулевой класс"
    if re.search(r"_АЯ|^АЯ", n, re.I):
        return "английский"
    if re.search(r"МсМ|узыка и речь", n, re.I):
        return "музыка и речь"
    if re.search(r"_РР|^РР|ицей|аннее развит", n, re.I):
        return "раннее развитие"
    if re.search(r"ини-сад", n, re.I):
        return "мини-сад"
    if "ШАХ" in n:
        return "шахматы"
    if "ИЗО" in n:
        return "ИЗО"
    if re.search(r"_МА|ентальн", n, re.I):
        return "ментальная арифметика"
    if re.search(r"_ЛГ|огопед", n, re.I):
        return "логопед"
    return ""


def _talked_recently(body: str, talked: set) -> bool:
    m = PHONE_IN_TEXT.search(body or "")
    return bool(m and m.group(1) in talked)


def _age(t: dict) -> int:
    c = (t.get("createdAt") or "")[:10]
    try:
        return (date.today() - date.fromisoformat(c)).days
    except Exception:
        return 0


def recent_talks(days: int = 4) -> set:
    """Номера, с которыми РЕАЛЬНО поговорили за последние дни.

    Без этой проверки правило «восемь дней в обзвоне — меняй канал»
    срабатывает на задачах, где вчера состоялся разговор: у Пахомова
    Егора задача была заведена десять дней назад, но вчера Ира дозвонилась
    и он записался на пробное. Возраст задачи и возраст контакта — разные
    вещи, и решение принимается по второму."""
    out = set()
    try:
        from . import mango
        from datetime import datetime, timedelta, timezone
        msk = timezone(timedelta(hours=3))
        now = datetime.now(msk)
        rows = mango.calls(now - timedelta(days=days), now)
    except Exception as e:  # noqa: BLE001
        log.warning("tasksmart: журнал звонков недоступен (%s)", type(e).__name__)
        return out
    for r in rows:
        talk = (r.get("finish", 0) - r.get("answer", 0)) if r.get("answer") else 0
        if talk < 20:
            continue          # гудки и автоответчики разговором не считаем
        for f in ("from_num", "to_num"):
            d = "".join(c for c in str(r.get(f) or "") if c.isdigit())[-10:]
            if len(d) == 10:
                out.add(d)
    return out


def collect(mk: MoyklassClient) -> dict:
    tasks = []
    for mid in STAFF:
        tasks += [t for t in taskguard.all_tasks(mk, mid)
                  if not (t.get("isComplete") or t.get("isCompleted"))]
    tasks = list({t["id"]: t for t in tasks}.values())

    joins = taskguard.pull_all(mk, "/v1/company/joins", "joins")
    rc = mk.get("/v1/company/classes", {"limit": 500})
    cls = {c["id"]: (c.get("name") or "")
           for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
    booked = defaultdict(list)
    for j in joins:
        nm = cls.get(j.get("classId"), "")
        if nm.startswith("2627") and not re.search(r"Заявк|Roistat", nm, re.I) \
                and j.get("statusId") in ACTIVE and j.get("userId"):
            booked[j["userId"]].append(nm)
    return {"tasks": tasks, "booked": dict(booked),
            "talked": recent_talks()}


def decide(data: dict) -> list[dict]:
    out = []
    for t in data["tasks"]:
        body = (t.get("body") or "").strip()
        uid = t.get("userId")
        age = _age(t)
        act, why, new_body, new_cat = "оставить", "", None, None

        groups = data["booked"].get(uid) or []
        if groups and CALL_TASK.search(body):
            subs = {_subject(g) for g in groups if _subject(g)}
            # Предлагаем то, на что он уже записан?
            same = any(s and s in body.lower() for s in subs)
            if same:
                act = "закрыть"
                why = f"уже записан: {groups[0][:44]}"
            else:
                # Записан на другое — это не мёртвая задача, а допродажа.
                act = "допродажа"
                why = "записан на другое — предложить вторым предметом"
                where = ", ".join(sorted(subs)) or groups[0][:40]
                new_body = (f"💡 ВТОРОЙ ПРЕДМЕТ. Уже ходит на {where}. "
                            f"{body[:150]} Скидка 10% на второй предмет "
                            f"(скидки не суммируются).")[:250]
                new_cat = CAT_PUSH

        elif t.get("categoryId") == CAT_URGENT and age >= URGENT_LIVES:
            # Срочность, не сработавшая за трое суток, приучает не реагировать
            # на красную метку. Понижаем, но задачу не теряем.
            act = "остыло"
            why = f"помечена срочной {age} дн. назад — срочностью уже не является"
            new_cat = CAT_CALL if CALL_TASK.search(body) else CAT_CHAT
            new_body = re.sub(r"^(🔥+\s*|СРОЧНО[,:]?\s*)", "", body)[:250]

        elif age >= CALL_GIVES_UP and t.get("categoryId") == CAT_CALL \
                and not _talked_recently(body, data.get("talked") or set()):
            # Восемь дней дозвона без результата — сигнал сменить канал,
            # а не звонить в девятый раз. Но только если разговора
            # действительно не было: задача может быть старой, а контакт
            # свежим.
            act = "сменить канал"
            why = f"{age} дн. в обзвоне без разговора"
            new_body = ("✍️ ЗВОНКИ НЕ ПОМОГАЮТ — НАПИСАТЬ. " + body)[:250]
            new_cat = CAT_CHAT

        elif not uid:
            m = PHONE_IN_TEXT.search(body)
            if m:
                act, why = "привязать", f"телефон +7{m.group(1)} есть в тексте"
                new_body = m.group(1)   # сюда кладём номер для поиска карточки

        out.append({"id": t["id"], "uid": uid, "act": act, "why": why,
                    "new_body": new_body, "new_cat": new_cat,
                    "body": body[:90], "age": age})
    json.dump(out, open(f"{SP}/tasksmart.json", "w"), ensure_ascii=False)
    return out


def apply() -> dict:
    dec = json.load(open(f"{SP}/tasksmart.json"))
    mk = MoyklassClient(sync.get_api_key())
    stat: Counter = Counter()
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

            b = {k: t.get(k) for k in ("userId", "classIds", "filialIds",
                                       "ownerId", "reminds", "managerIds")}
            b = {k: v for k, v in b.items() if v is not None}
            b["categoryId"] = it.get("new_cat") or t.get("categoryId") or CAT_CALL
            b["isAllDay"] = False
            d = (t.get("beginDate") or "")[:10] or date.today().isoformat()
            b["beginDate"] = f"{d}T{taskguard.msk_hour(t.get('beginDate'))}:00+03:00"
            b["endDate"] = f"{d}T20:00:00+03:00"

            if it["act"] == "закрыть":
                b["body"] = f"[убрано: {it['why']}] {t.get('body') or ''}"[:250]
                b["isComplete"] = True
            elif it["act"] == "привязать":
                # new_body здесь — номер телефона, ищем по нему карточку
                try:
                    r = mk.get("/v1/company/users",
                               {"phone": it["new_body"], "limit": 2})
                    us = r.get("users") or []
                except Exception:
                    us = []
                if not us:
                    stat["карточка не найдена"] += 1
                    continue
                b["userId"] = us[0]["id"]
                b["body"] = (t.get("body") or "")[:250]
            else:
                b["body"] = (it.get("new_body") or t.get("body") or "")[:250]

            try:
                mk.post(f"/v1/company/tasks/{it['id']}", b)
                stat[it["act"]] += 1
            except Exception:
                stat["ошибка"] += 1
            time.sleep(0.2)
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
    mk = MoyklassClient(sync.get_api_key())
    try:
        data = collect(mk)
    finally:
        mk.close()
    dec = decide(data)
    print("задач:", len(dec))
    print("решения:", dict(Counter(x["act"] for x in dec)))
    for act in ("закрыть", "допродажа", "остыло", "сменить канал", "привязать"):
        sel = [x for x in dec if x["act"] == act]
        if not sel:
            continue
        print(f"\n=== {act.upper()}: {len(sel)}")
        for x in sel[:3]:
            print(f"   {x['why']}")
            print(f"      было:  {x['body'][:72]}")
            if x.get("new_body") and act != "привязать":
                print(f"      стало: {x['new_body'][:72]}")


if __name__ == "__main__":
    main()
