"""Ежедневный сторож задач: следит, чтобы списки не зарастали заново.

За один день 21.08 я дважды находил в задачах беспорядок, и оба раза его
создал сам: сначала перенос по сменам сломал сроки, потом выравнивание
нагрузки утащило срочные задачи на четыре дня вперёд. Оба раза чинил
руками. Это и есть проблема: «сейчас чисто» держится ровно до следующей
моей правки, а заметить её можно только если вспомнить проверить.

Поэтому проверка становится постоянной и идёт сама — утром и вечером.

Что сторож ЧИНИТ молча (случаи, где смысл потерять нельзя):
  · просроченную задачу поднимает на сегодня — невидимая задача не делается;
  · проставляет категорию, без неё задача выпадает из фильтров;
  · закрывает задачу-призрак, рождённую проверкой поверх моего же закрытия;
  · закрывает задачу на номер с несуществующим кодом — это автообзвон;
  · возвращает на сегодня срочную задачу, уехавшую в будущее.

Что сторож НЕ трогает, а только сообщает:
  · шаблонные тексты, дубли, перекос нагрузки, пустые смены.
Их починка требует истории клиента и решения, а автоматика, принимающая
такие решения без присмотра, ровно этим и наломала дров. Тут она считает
и показывает, а разбираю я.

Отчёт кладётся в настройку task_guard и виден на /pravila-kontrol.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date

from . import db
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.taskguard")

STAFF = {232763: "Ира", 232805: "Аня", 202856: "Лена",
         154181: "Лиза", 84116: "Борис", 229704: "Маша"}
CAT_CALL, CAT_ORG = 104576, 104578

GHOST = re.compile(r"Закрыта без действия")
MY_CLOSURE = re.compile(r"\[(дубль|закрыто|сведено|остыло|убрано)", re.I)
URGENT_TEXT = re.compile(r"в течение \d+ минут", re.I)
TEMPLATE = re.compile(r"^(?:\[[^\]]*\]\s*)?(?:⚠️[^.]*\.\s*)?"
                      r"(?:Обзвон набора|Продолжение занятий|Продление:)", re.I)
PHONE = re.compile(r"\+7(\d{10})")


def _open_tasks(mk: MoyklassClient) -> list[dict]:
    today = date.today().isoformat()
    out = []
    for mid in STAFF:
        off = 0
        while off < 900:
            r = mk.get("/v1/company/tasks",
                       {"date": today, "limit": 100, "offset": off,
                        "managerId": mid})
            ts = r.get("tasks") or []
            if not ts:
                break
            out += [t for t in ts
                    if not t.get("isComplete") and not t.get("isCompleted")]
            off += 100
    return list({t["id"]: t for t in out}.values())


def _rewrite(mk: MoyklassClient, t: dict, *, day: str | None = None,
             cat: int | None = None, close_why: str | None = None) -> bool:
    """Задача в МойКласс обновляется полной заменой: поле, которого нет
    в теле запроса, стирается. Поэтому собираем из существующей задачи."""
    b = {k: t.get(k) for k in ("userId", "classIds", "filialIds", "ownerId",
                              "reminds", "managerIds")}
    b = {k: v for k, v in b.items() if v is not None}
    b["categoryId"] = cat or t.get("categoryId") or CAT_CALL
    b["isAllDay"] = False
    d = day or (t.get("beginDate") or "")[:10] or date.today().isoformat()
    hour = (t.get("beginDate") or "")[11:16] or "09:00"
    b["beginDate"] = f"{d}T{hour}:00+03:00"
    b["endDate"] = f"{d}T20:00:00+03:00"
    if close_why:
        b["body"] = f"[убрано: {close_why}] {t.get('body') or ''}"[:250]
        b["isComplete"] = True
    else:
        b["body"] = (t.get("body") or "")[:250]
    try:
        mk.post(f"/v1/company/tasks/{t['id']}", b)
        return True
    except Exception:
        log.warning("taskguard: не удалось обновить задачу %s", t.get("id"))
        return False


def check(mk: MoyklassClient, fix: bool = True) -> dict:
    from .autopilot import _real_number

    today = date.today().isoformat()
    tasks = _open_tasks(mk)
    fixed: Counter = Counter()
    seen_phone: dict[str, int] = {}

    for t in tasks:
        body = (t.get("body") or "").strip()
        cur = (t.get("beginDate") or "")[:10]

        ph = PHONE.search(body)
        if ph and not _real_number(ph.group(1)):
            if fix and _rewrite(mk, t, day=today,
                                close_why=f"кода {ph.group(1)[:3]} не существует"):
                fixed["фиктивный номер"] += 1
            continue

        if GHOST.search(body) and MY_CLOSURE.search(body):
            if fix and _rewrite(mk, t, day=today,
                                close_why="призрак поверх моего же закрытия"):
                fixed["призрак"] += 1
            continue

        # Срочность живёт часы. Стоящая в будущем срочная задача — либо
        # уже не срочная, либо её незачем было помечать срочной.
        if URGENT_TEXT.search(body) and cur > today:
            if fix and _rewrite(mk, t, day=today):
                fixed["срочная из будущего"] += 1
            continue

        if cur and cur < today:
            if fix and _rewrite(mk, t, day=today):
                fixed["просрочка"] += 1
            continue

        if not t.get("categoryId"):
            if fix and _rewrite(mk, t, cat=CAT_CALL if t.get("userId")
                                else CAT_ORG):
                fixed["без категории"] += 1

    # Считаем то, что чинить автоматически нельзя.
    live = _open_tasks(mk) if fix and fixed else tasks
    dups = sum(v - 1 for v in
               Counter(t["userId"] for t in live if t.get("userId")).values()
               if v > 1)
    templates = sum(1 for t in live if TEMPLATE.search((t.get("body") or "")))
    load: dict[str, Counter] = defaultdict(Counter)
    for t in live:
        mid = (t.get("managerIds") or [None])[0]
        load[STAFF.get(mid, "?")][(t.get("beginDate") or "")[:10]] += 1

    report = {
        "когда": date.today().isoformat(),
        "открытых": len(live),
        "починено": dict(fixed),
        "требует_меня": {"дублей": dups, "шаблонных": templates},
        "нагрузка": {who: dict(sorted(c.items())) for who, c in load.items()},
    }
    try:
        db.set_setting("task_guard", json.dumps(report, ensure_ascii=False)[:4000])
    except Exception:
        log.warning("taskguard: отчёт не сохранился")
    if fixed:
        log.info("taskguard: починено %s", dict(fixed))
    if dups or templates:
        log.warning("taskguard: разобрать руками — дублей %d, шаблонных %d",
                    dups, templates)
    return report


def main():
    import sys
    from . import sync
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    mk = MoyklassClient(sync.get_api_key())
    try:
        r = check(mk, fix="show" not in sys.argv)
    finally:
        mk.close()
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
