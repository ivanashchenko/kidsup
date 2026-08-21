"""Тексты задач по-человечески: имя, возраст, история, что предлагать.

Проблема, которую это решает. После разбора у Иры в списке сверху идут пять
одинаковых строк «Обзвон набора 2026/27: открой карточку, прочитай подсказку».
Чтобы понять, кому звонит и что предлагать, администратор каждый раз лезет
в карточку, читает историю и сам соображает, какой предмет подойдёт по
возрасту. Это полминуты на звонок и тридцать секунд сомнений — а звонков
сорок за смену.

Что делаем. Всё, что нужно для разговора, кладём прямо в текст задачи:
кого зовут, сколько лет на 1 сентября, чем занимался и до какого месяца,
что предлагать по возрасту, и один короткий повод начать разговор.
Данные уже собраны в taskplan.json — второй раз API не дёргаем.

Ограничение МойКласс: тело задачи не длиннее 250 символов, поэтому текст
собирается плотно и без вежливых оборотов.

Запуск:
    python -m app.taskenrich show     — показать, что получится
    python -m app.taskenrich apply    — записать в CRM
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date

from . import sync
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.taskenrich")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"

# Шаблонные тексты, которые не несут информации и подлежат замене.
TEMPLATE = re.compile(
    r"^(?:⚠️[^.]*\.\s*)?(?:Обзвон набора 2026/27|Продолжение занятий 2026/27|"
    r"Продление:|🔥 Летний лагерь закончился)", re.I)

SEASON = date(2026, 9, 1)


def _age(bd: str | None) -> float | None:
    if not bd:
        return None
    try:
        return round((SEASON - date.fromisoformat(bd[:10])).days / 365.25, 1)
    except ValueError:
        return None


def _subject_of(name: str) -> str | None:
    n = name or ""
    if n.startswith(("ДОД", "МК")):
        return None
    if "_ПШ" in n or n.startswith("ПШ") or "одготовка" in n:
        return "подготовку к школе"
    if "_АЯ" in n or n.startswith("АЯ") or "_ЛК" in n:
        return "английский"
    if "ини-сад" in n or "нулевой" in n.lower():
        return "сад"
    if ("МсМ" in n or "узыка и речь" in n or "_РР" in n or n.startswith("РР")
            or "азвити" in n or "ицей" in n):
        return "раннее развитие"
    if "_ЛГ" in n or "огопед" in n:
        return "логопеда"
    if "ШАХ" in n:
        return "шахматы"
    if "ИЗО" in n:
        return "ИЗО"
    if "МА" in n:
        return "ментальную арифметику"
    return None


def offer_for(age: float | None, was: set[str]) -> str:
    """Что предлагать в новом году с учётом того, что ребёнок вырос."""
    a = age or 0
    if "подготовку к школе" in was:
        return "английский по уровню" if a >= 7.3 else "ПкШ + гарантия чтения"
    if "английский" in was:
        return "английский, уровень по чтению"
    if ("сад" in was or "раннее развитие" in was) and a >= 4.2:
        return "ПкШ + гарантия чтения"
    if "раннее развитие" in was:
        return "раннее развитие по возрасту"
    if "логопеда" in was:
        return "логопеда (мест мало)"
    if 4.5 <= a <= 11:
        return "английский или ПкШ"
    if a and a < 3:
        return "раннее развитие"
    return "подобрать по возрасту"


def build(c: dict, calls: dict, phone: str) -> str | None:
    """Собрать человеческий текст задачи. None — если данных мало."""
    name = (c.get("name") or "").strip()
    if not name or name.startswith("7") or name.startswith("+7"):
        return None
    child = name.split("(")[0].strip()
    age = _age(c.get("birthday"))
    was = {s for s in (_subject_of(x) for x in c.get("was_classes", [])) if s}
    parts = [child]
    if age:
        parts.append(f"{age:g} л.")
    if was:
        parts.append("ходил на " + ", ".join(sorted(was)[:2]))
    if c.get("last_visit"):
        parts.append("до " + c["last_visit"][:7].replace("2026-", "").replace("2025-", "")
                     .replace("01", "января").replace("02", "февраля")
                     .replace("03", "марта").replace("04", "апреля")
                     .replace("05", "мая").replace("06", "июня")
                     .replace("07", "июля").replace("08", "августа"))
    head = ", ".join(parts)
    offer = offer_for(age, was)
    hist = calls.get(phone) or []
    talks = [x for x in hist if x["dur"] >= 10]
    if not hist:
        tail = "Ни разу не звонили."
    elif not talks:
        tail = f"Набирали {len(hist)}, не дозвонились."
    else:
        tail = f"Говорили {max(x['day'] for x in talks)[5:]}."
    body = (f"📞 {head}. Предложить: {offer}. {tail} "
            f"Зови на 29–30.08 и Неделю уроков 31.08–06.09.")
    return body[:250]


def prepare() -> list[dict]:
    """Что и чем заменим. Ничего не меняет."""
    d = json.load(open(f"{SP}/taskplan.json"))
    clients, calls = d["clients"], d["calls"]
    out = []
    for t in d["tasks"]:
        uid = str(t.get("userId") or "")
        c = clients.get(uid)
        if not c:
            continue
        body = (t.get("body") or "").strip()
        if not TEMPLATE.search(body):
            continue
        new = build(c, calls, c.get("phone") or "")
        if new and new != body:
            out.append({"id": t["id"], "uid": uid, "old": body[:70], "new": new})
    json.dump(out, open(f"{SP}/enrich.json", "w"), ensure_ascii=False)
    return out


def apply() -> dict:
    items = json.load(open(f"{SP}/enrich.json"))
    mk = MoyklassClient(sync.get_api_key())
    done = err = 0
    try:
        for it in items:
            try:
                t = mk.get(f"/v1/company/tasks/{it['id']}")
            except Exception:
                err += 1
                continue
            if t.get("isComplete") or t.get("isCompleted"):
                continue
            b = {k: t.get(k) for k in ("categoryId", "userId", "classIds",
                                       "filialIds", "ownerId", "reminds",
                                       "beginDate", "endDate", "managerIds")}
            b = {k: v for k, v in b.items() if v is not None}
            b["isAllDay"] = False
            b["body"] = it["new"]
            try:
                mk.post(f"/v1/company/tasks/{it['id']}", b)
                done += 1
            except Exception:
                err += 1
    finally:
        mk.close()
    return {"обновлено": done, "ошибок": err}


def main():
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if cmd == "apply":
        print(apply())
    else:
        items = prepare()
        print(f"заменим текстов: {len(items)}\n")
        for it in items[:12]:
            print(f"  было:  {it['old']}")
            print(f"  стало: {it['new']}\n")


if __name__ == "__main__":
    main()
