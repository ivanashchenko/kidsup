"""Лист обзвона по подготовке к школе — для владельца.

Откуда взялся. 21.08 выяснилось, что в 14 групп ПШ нового сезона (112 мест)
не записан НИ ОДИН ребёнок, при этом в ПШ и нулевом классе прошлых сезонов
занимались 245 детей, и 240 из них никуда на 2026/27 не записаны. Это самая
тёплая база, какая бывает: родитель уже выбирал у нас именно этот продукт,
уже платил и знает центр. До неё просто не дошли руки.

Что собирает: возраст на 1 сентября, что посещал, когда был в последний раз,
телефон, статус, и не занят ли клиент уже задачей у администратора — чтобы
владелец и админ не позвонили одному человеку в один день.

Сегменты по возрасту на 1 сентября:
  5.5–7.0  — прямое попадание в ПШ, звонить первыми;
  4.0–5.5  — младшая ПШ1, звонить вторыми;
  7.0+     — ребёнок ушёл в школу, ПШ не нужен: английский, ментальная
             арифметика, скорочтение. Отдельная волна, не мешать с первыми.

Запуск:
    python -m app.pshlist collect   — собрать (несколько минут, ~250 карточек)
    python -m app.pshlist show      — показать сводку
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import date

from . import sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.pshlist")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"
OUT = f"{SP}/psh_list.json"

SEASON = date(2026, 9, 1)
# Кому не звоним ни при каких обстоятельствах: не писать, некачественный, отказ.
NO_CALL = {146328, 125954, 125957}
ACTIVE_OLD = {2, 4, 50509, 58131, 58132, 83760}
ACTIVE_NEW = {2, 50509, 58131, 58132, 83760}
IS_PS = re.compile(r"ПШ|одготовк|нулев", re.I)


def _subject(name: str) -> str | None:
    n = name or ""
    if IS_PS.search(n):
        return "подготовка к школе"
    if "_АЯ" in n or n.startswith("АЯ") or "_ЛК" in n:
        return "английский"
    if "МсМ" in n or "узыка и речь" in n or "_РР" in n or n.startswith("РР"):
        return "раннее развитие"
    if "ШАХ" in n:
        return "шахматы"
    if "ИЗО" in n:
        return "ИЗО"
    if "МА" in n or "ентальн" in n:
        return "ментальная арифметика"
    if "ини-сад" in n:
        return "мини-сад"
    return None


def collect() -> list[dict]:
    mk = MoyklassClient(sync.get_api_key())
    try:
        joins = taskguard.pull_all(mk, "/v1/company/joins", "joins")
        rc = mk.get("/v1/company/classes", {"limit": 500})
        cls = {c["id"]: (c.get("name") or "")
               for c in (rc.get("classes") if isinstance(rc, dict) else rc)}

        def is_ps(cid):
            return bool(IS_PS.search(cls.get(cid, "")))

        def is_new(cid):
            return cls.get(cid, "").startswith("2627")

        old_ps = {j["userId"] for j in joins
                  if is_ps(j.get("classId")) and not is_new(j.get("classId"))
                  and j.get("statusId") in ACTIVE_OLD and j.get("userId")}
        booked = {j["userId"] for j in joins
                  if is_new(j.get("classId"))
                  and j.get("statusId") in ACTIVE_NEW and j.get("userId")}
        back = sorted(old_ps - booked)
        log.info("были в ПШ: %d, из них не записаны на новый сезон: %d",
                 len(old_ps), len(back))

        was = defaultdict(set)
        for j in joins:
            if j.get("userId") in old_ps:
                s = _subject(cls.get(j.get("classId"), ""))
                if s:
                    was[j["userId"]].add(s)

        busy = set()
        for mid in (232763, 232805, 202856, 154181):
            for t in taskguard.all_tasks(mk, mid):
                if not (t.get("isComplete") or t.get("isCompleted")) \
                        and t.get("userId"):
                    busy.add(t["userId"])

        out = []
        for i, uid in enumerate(back):
            try:
                u = mk.get(f"/v1/company/users/{uid}")
            except Exception:
                continue
            if u.get("clientStateId") in NO_CALL:
                continue
            bd = None
            for a in (u.get("attributes") or []):
                if a.get("attributeAlias") == "birthday" and a.get("value"):
                    bd = a["value"][:10]
            age = None
            if bd:
                try:
                    age = round((SEASON - date.fromisoformat(bd)).days / 365.25, 1)
                except ValueError:
                    pass
            last = ""
            try:
                lr = mk.get("/v1/company/lessonRecords",
                            {"userId": uid, "limit": 120,
                             "includeLessons": True}).get("lessonRecords") or []
                for r in lr:
                    if r.get("visit"):
                        d = ((r.get("lesson") or {}).get("date") or "")[:10]
                        if d > last:
                            last = d
            except Exception:
                pass
            out.append({"uid": uid, "name": u.get("name") or "",
                        "phone": (u.get("phone") or "")[-10:], "age": age,
                        "state": u.get("clientStateId"), "last": last,
                        "busy": uid in busy,
                        "was": sorted(was.get(uid, set()))[:4]})
            if i % 25 == 0:
                log.info("... %d/%d", i, len(back))
            time.sleep(0.32)
    finally:
        mk.close()
    json.dump(out, open(OUT, "w"), ensure_ascii=False)
    log.info("собрано карточек: %d", len(out))
    return out


def segment(c: dict) -> str:
    a = c.get("age")
    if a is None:
        return "возраст неизвестен"
    if a >= 7.0:
        return "школьники 7+"
    if a >= 5.5:
        return "ПШ сейчас (5,5-7)"
    if a >= 4.0:
        return "младшая ПШ1 (4-5,5)"
    return "малыши до 4"


ORDER = {"ПШ сейчас (5,5-7)": 0, "младшая ПШ1 (4-5,5)": 1,
         "возраст неизвестен": 2, "школьники 7+": 3, "малыши до 4": 4}


def ranked() -> list[dict]:
    """Первыми — целевой возраст и СВЕЖИЙ последний визит.

    Свежесть решает: у семьи, которая была у нас весной, разговор начинается
    с «мы вас помним», а у той, что ушла в 2023-м, ребёнку тогда было три года
    и половину контекста придётся восстанавливать заново. Дата сортируется
    по убыванию — первая версия сортировала по возрастанию и поднимала наверх
    визиты 2023 года, то есть самых холодных.

    Занятые задачей администратора уходят в конец: звонить туда владельцу
    значит продублировать чужой звонок в тот же день."""
    data = json.load(open(OUT))
    for c in data:
        c["seg"] = segment(c)
    return sorted(data, key=lambda c: (ORDER.get(c["seg"], 9),
                                       bool(c.get("busy")),
                                       _newest_first(c.get("last") or "")))


def _newest_first(d: str) -> str:
    """Ключ, который внутри обычной возрастающей сортировки ставит свежую
    дату раньше старой, а пустую — в самый конец. Каждая цифра заменяется
    на дополнение до девяти, поэтому «2025-05-13» становится меньше, чем
    «2024-…», и свежие поднимаются наверх. Пустая дата даёт тильду —
    она больше любой цифры, значит клиент без единого визита оказывается
    последним, и это правильно: про него нечего сказать в разговоре."""
    if not d:
        return "~"
    return "".join(chr(ord("9") - int(ch)) if ch.isdigit() else ch for ch in d)


def main():
    import sys
    from collections import Counter
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "collect" in sys.argv:
        collect()
        return
    data = ranked()
    print("всего в листе:", len(data))
    print("по сегментам:", dict(Counter(c["seg"] for c in data)))
    print("уже есть задача у админа:", sum(1 for c in data if c["busy"]))
    for c in data[:12]:
        print(f"   {c['seg']:20s} {c['name'][:26]:28s} {c['age']} +7{c['phone']} "
              f"посл.визит {c['last'] or '—'}")


if __name__ == "__main__":
    main()
