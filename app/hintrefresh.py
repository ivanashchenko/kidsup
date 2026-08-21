"""Обновление подсказок во всех карточках обзвона.

Зачем. В карточках лежат подсказки двух поколений: от 10.08 («ПОДСКАЗКА ДЛЯ
ЗВОНКА» — с аргументом и иерархией предложения, но без событий и без
диагностики) и от 16.08 («ЧТО СКАЗАТЬ ПРИ ЗВОНКЕ» — с фактами и событиями,
но вовсе без аргумента). Обе устарели: в них нет ни трёх запускающих
мероприятий, ни главного — того, что на первом занятии педагог проводит
диагностику и говорит родителю, что у ребёнка хорошо, что стоит подтянуть
и как наши занятия с этим помогут.

Что делает. Для каждого клиента, по которому есть открытая задача обзвона,
собирает свежую подсказку (app.hint) и кладёт её комментарием в карточку.
Старые подсказки не удаляет: комментарии в МойКласс — это история, а не
доска объявлений; новая просто ложится сверху и видна первой. Заодно
обновляет тело задачи короткой строкой.

Данные берутся из taskplan.json, собранного разбором задач, — второй раз
API не опрашиваем.

Запуск:
    python -m app.hintrefresh show    — показать примеры, ничего не меняя
    python -m app.hintrefresh apply   — записать в CRM
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date

from . import hint, sync
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.hintrefresh")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"

# Задачи обзвона: только по ним подсказка имеет смысл.
CALL_TASK = re.compile(
    r"обзвон|позвони|перезвон|продолжение занятий|дожать|дожим|пригласи|"
    r"продающий звонок|📞", re.I)
# Уже стоящая свежая подсказка — чтобы не плодить одинаковые.
FRESH = "🎯 ПОДСКАЗКА ДЛЯ ЗВОНКА"


def _subject_of(name: str) -> str | None:
    n = name or ""
    if n.startswith(("ДОД", "МК")):
        return None
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


def prepare() -> list[dict]:
    """Кому и что запишем. Ничего не меняет."""
    d = json.load(open(f"{SP}/taskplan.json"))
    clients, calls = d["clients"], d.get("calls", {})
    seen: set[str] = set()
    out = []
    for t in d["tasks"]:
        uid = str(t.get("userId") or "")
        c = clients.get(uid)
        if not c or uid in seen:
            continue
        if not CALL_TASK.search(t.get("body") or ""):
            continue
        seen.add(uid)
        was = [s for s in (_subject_of(x) for x in c.get("was_classes", []))
               if s] or None
        ph = c.get("phone") or ""
        hist = calls.get(ph) or []
        talks = [x for x in hist if x["dur"] >= 10]
        today = date.today().isoformat()
        last_talk = max((x["day"] for x in talks), default="")
        tried = ("Ни разу не звонили." if not hist else
                 f"Набирали {len(hist)}, не дозвонились." if not talks else
                 "Говорили сегодня." if last_talk == today else
                 f"Говорили {last_talk[8:10]}.{last_talk[5:7]}.")
        full = hint.build(
            c.get("name") or "", birthday=c.get("birthday"), was=was,
            last_visit=c.get("last_visit"), enrolled=c.get("enrolled") or None)
        brief = hint.short(
            c.get("name") or "", birthday=c.get("birthday"), was=was,
            last_visit=c.get("last_visit"), tried=tried)
        out.append({"uid": uid, "task": t["id"], "name": c.get("name"),
                    "hint": full, "brief": brief,
                    "old_task": (t.get("body") or "")[:70]})
    json.dump(out, open(f"{SP}/hints.json", "w"), ensure_ascii=False)
    return out


def apply(limit: int = 0) -> dict:
    items = json.load(open(f"{SP}/hints.json"))
    if limit:
        items = items[:limit]
    mk = MoyklassClient(sync.get_api_key())
    hints = tasks = err = skip = 0
    try:
        for it in items:
            # не дублируем подсказку, если сегодня уже клали
            try:
                cm = mk.get("/v1/company/userComments",
                            {"userId": int(it["uid"]), "limit": 5})
                cm = cm.get("comments") or cm.get("userComments") or []
            except Exception:
                cm = []
            today = date.today().isoformat()
            already = any(FRESH in (c.get("comment") or "")
                          and (c.get("createdAt") or "").startswith(today)
                          for c in cm)
            if already:
                skip += 1
            else:
                try:
                    mk.post("/v1/company/userComments",
                            {"userId": int(it["uid"]), "showToUser": False,
                             "comment": it["hint"]})
                    hints += 1
                except Exception:
                    err += 1
            # тело задачи — короткой строкой
            try:
                t = mk.get(f"/v1/company/tasks/{it['task']}")
                if t.get("isComplete") or t.get("isCompleted"):
                    continue
                b = {k: t.get(k) for k in
                     ("categoryId", "userId", "classIds", "filialIds",
                      "ownerId", "reminds", "beginDate", "endDate", "managerIds")}
                b = {k: v for k, v in b.items() if v is not None}
                b["isAllDay"] = False
                b["body"] = it["brief"]
                mk.post(f"/v1/company/tasks/{it['task']}", b)
                tasks += 1
            except Exception:
                err += 1
    finally:
        mk.close()
    return {"подсказок": hints, "задач": tasks, "пропущено": skip, "ошибок": err}


def main():
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if cmd == "apply":
        print(apply(int(sys.argv[2]) if len(sys.argv) > 2 else 0))
    else:
        items = prepare()
        print(f"карточек к обновлению: {len(items)}\n")
        for it in items[:3]:
            print("=" * 70)
            print(it["hint"])
            print("\nЗАДАЧА:", it["brief"], "\n")


if __name__ == "__main__":
    main()
