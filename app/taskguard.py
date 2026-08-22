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
from datetime import date, datetime, timedelta, timezone

from . import brain
from . import db
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.taskguard")

STAFF = {232763: "Ира", 232805: "Аня", 202856: "Лена",
         154181: "Лиза", 84116: "Борис", 229704: "Маша"}
CAT_CALL, CAT_ORG = 104576, 104578
# Звонящие администраторы: у них есть график смен, и задача
# в их выходной бесполезна. У Лизы графика нет — она онлайн каждый день.
CALLERS = (232763, 232805, 202856)

# Владелец не должен получать работу администратора. Фильтр по смыслу стоял
# только в autopilot._task(), а половина задач создаётся напрямую через API —
# мимо него. 21.08 у Бориса из 20 задач владельческими были шесть: остальное —
# «позвонить по заявке», «прислать программу в WhatsApp», «отправить ссылку
# на оплату» и даже «вы на переписке одна» — текст, написанный для Лизы.
# Сторож смотрит на результат, а не на точку создания, поэтому ловит всё.
OWNER_ID = 84116
CHAT_ADMIN = 154181                     # Лиза: переписка и деньги
# Требует решения владельца: доступы, деньги компании, люди, обязательства.
# Только то, чего администратор физически не может сделать: доступы, деньги
# компании, люди, обязательства. Слабые слова сюда не годятся — «договор»
# ловил «договорились», «кто ведёт» перевешивало «прислать программу»,
# и задача «отправь клиенту программу и имя педагога» оставалась у владельца,
# хотя отправляет её Лиза. Правило простое: получает тот, кто ИСПОЛНЯЕТ,
# а недостающий факт вписывается в текст задачи.
OWNER_WORK = re.compile(
    r"доступ|логин|парол|токен|аренд|реклам|бюджет|нанять|найм|уволь|закуп|"
    r"списать|учредител|юрлиц|лиценз|партнёр|стратег|на сайте|сайт вводит|"
    r"правк\w+ сайта|домен|тариф|подписк|нов\w+ педагог|второй человек|"
    r"^Решение:|внедрить",
    re.I)
# «сайт» голым словом сюда не годится: «заявка с сайта» — обычный лид
# для обзвона, а не правка сайта владельцем. Ловим только формулировки
# про сам сайт: «на сайте написано», «сайт вводит в заблуждение».
# Работа администратора: звонок, переписка, оформление в CRM.
ADMIN_WORK = re.compile(
    r"позвонить|перезвонить|обзвон|📞|набрать|прислать|отправить|написать в|"
    r"ссылк\w* на оплату|занести в CRM|перенести в карточк|записать [вна]|"
    r"подтвердить приход|держать ответ|вы на переписке|ответить|"
    r"клиент ждёт ответа|клиент писал|обещали", re.I)
# Что уходит Лизе, а не звонящему: деньги и всё, что делается текстом.
TO_CHAT = re.compile(r"оплат|возврат|счёт|счет|долг|абонемент|прайс|цен[аыу]|"
                     r"написать|переписк|WhatsApp|Telegram|телеграм|чат|MAX",
                     re.I)

GHOST = re.compile(r"Закрыта без действия")
MY_CLOSURE = re.compile(r"\[(дубль|закрыто|сведено|остыло|убрано)", re.I)
URGENT_TEXT = re.compile(r"в течение \d+ минут", re.I)
TEMPLATE = re.compile(r"^(?:\[[^\]]*\]\s*)?(?:⚠️[^.]*\.\s*)?"
                      r"(?:Обзвон набора|Продолжение занятий|Продление:)", re.I)
PHONE = re.compile(r"\+7(\d{10})")


# Предел пагинации. На 21.08 самый нагруженный список — 408 задач у Лизы,
# так что запас четырёхкратный. Достижение предела логируется: молча
# обрезанная выборка выглядит как «задач стало меньше» и лечится не тем.
PAGE_CAP = 2000


def pull_all(mk: MoyklassClient, path: str, key: str, params: dict | None = None,
             cap: int = 40000) -> list[dict]:
    """Выкачать эндпоинт целиком, а не первые N страниц.

    21.08 выборка joins была ограничена тремя тысячами при 8082 записях
    в CRM — две трети данных не дошли, и я сказал владельцу «в группах ПШ
    ноль записей», хотя их был двадцать один. Молчаливое обрезание опаснее
    ошибки: цифра выглядит правдоподобно и её никто не перепроверяет.
    Поэтому предел здесь заведомо избыточный, а его достижение — громкое."""
    out, off = [], 0
    while off < cap:
        q = dict(params or {})
        q.update({"limit": 100, "offset": off})
        r = mk.get(path, q)
        rows = (r.get(key) if isinstance(r, dict) else r) or []
        if not rows:
            return out
        out += rows
        off += 100
    log.error("pull_all: %s отдал больше %d строк — выборка обрезана, "
              "цифрам по ней доверять нельзя", path, cap)
    return out


def all_tasks(mk: MoyklassClient, manager_id: int) -> list[dict]:
    """Все задачи менеджера, открытые и закрытые.

    Параметр `date` у /v1/company/tasks НЕ ФИЛЬТРУЕТ — проверено 21.08:
    date=2026-08-21, date=2020-01-01, date="zzz" и запрос вообще без date
    возвращают один и тот же набор из 171 задачи. Поэтому его здесь нет,
    а день выбирается из поля beginDate самой задачи. Раньше он стоял
    в запросах и создавал ложное впечатление фильтра по дню."""
    out, off = [], 0
    while off < PAGE_CAP:
        r = mk.get("/v1/company/tasks",
                   {"limit": 100, "offset": off, "managerId": manager_id})
        ts = r.get("tasks") or []
        if not ts:
            break
        out += ts
        off += 100
    else:
        log.warning("taskguard: у менеджера %s больше %d задач — выборка "
                    "обрезана, подними PAGE_CAP", manager_id, PAGE_CAP)
    return out


def _open_tasks(mk: MoyklassClient) -> list[dict]:
    out = []
    for mid in STAFF:
        out += [t for t in all_tasks(mk, mid)
                if not t.get("isComplete") and not t.get("isCompleted")]
    return list({t["id"]: t for t in out}.values())


MSK = timezone(timedelta(hours=3))


def msk_hour(begin: str | None, default: str = "09:00") -> str:
    """Час задачи в московском времени.

    API отдаёт beginDate в UTC: «2026-08-21T08:00:00+00:00» — это 11:00 МСК.
    Раньше час брался срезом [11:16], то есть читался UTC-час, и записывался
    обратно с «+03:00» — каждое касание сдвигало задачу на три часа назад.
    К вечеру 21.08 в CRM уже было 58 задач на 06:00 и 21 задача на 03:00 ночи,
    а сторож, работающий дважды в день, продолжал их утаскивать. Уехав за
    полночь, задача становится «вчерашней» и попадает под правило просрочки —
    круг замыкается сам на себя."""
    if not begin:
        return default
    try:
        dt = datetime.fromisoformat(begin.replace("Z", "+00:00"))
    except ValueError:
        return default
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK).strftime("%H:%M")


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
    hour = msk_hour(t.get("beginDate"))
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


def next_workday(manager_id: int, not_before: str | None = None) -> str:
    """Ближайший рабочий день сотрудника, начиная с сегодня.

    Правило «просрочка поднимается на сегодня» без этой проверки создаёт
    круг: задача Иры уезжает на субботу, когда работает Аня, к утру
    воскресенья снова становится просроченной и снова поднимается —
    и так до её выхода. 22.08 так набежало 39 задач у человека,
    который выходит 25-го.

    Если график на сотрудника не заведён, возвращаем сегодня: лучше
    показать задачу лишний раз, чем спрятать её от всех."""
    today = not_before or date.today().isoformat()
    try:
        sched = json.loads(db.get_setting("admin_schedule") or "{}")
    except Exception:
        sched = {}
    if not sched:
        return today
    days = []
    for day, who in sched.items():
        ids = who if isinstance(who, list) else [who]
        if manager_id in ids and day >= today:
            days.append(day)
    return min(days) if days else today


def _duty() -> int | None:
    """Кто сегодня на звонках. Берём из живого расписания смен, а не из
    памяти: администратор в отпуске не должен получать сегодняшний обзвон."""
    try:
        from .autopilot import _admins_today
        a = _admins_today()
        return a[0]["managerId"] if a else None
    except Exception:
        return None


def _reassign(mk: MoyklassClient, t: dict, to: int) -> bool:
    b = {k: t.get(k) for k in ("userId", "classIds", "filialIds", "ownerId",
                              "reminds")}
    b = {k: v for k, v in b.items() if v is not None}
    b["managerIds"] = [to]
    b["categoryId"] = t.get("categoryId") or CAT_CALL
    b["isAllDay"] = False
    d = (t.get("beginDate") or "")[:10] or date.today().isoformat()
    b["beginDate"] = f"{d}T{msk_hour(t.get('beginDate'))}:00+03:00"
    b["endDate"] = f"{d}T20:00:00+03:00"
    b["body"] = (t.get("body") or "")[:250]
    try:
        mk.post(f"/v1/company/tasks/{t['id']}", b)
        return True
    except Exception:
        log.warning("taskguard: не удалось передать задачу %s", t.get("id"))
        return False


def check(mk: MoyklassClient, fix: bool = True) -> dict:
    from .autopilot import _real_number

    today = date.today().isoformat()
    tasks = _open_tasks(mk)
    fixed: Counter = Counter()
    unclear: list[dict] = []

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
            mid = (t.get("managerIds") or [None])[0]
            # Поднимаем не на «сегодня», а на ближайшую смену исполнителя:
            # задача, поставленная на выходной сотрудника, к утру снова
            # станет просроченной, и так по кругу.
            day = next_workday(mid, today) if mid in CALLERS else today
            if fix and _rewrite(mk, t, day=day):
                fixed["просрочка"] += 1
            continue

        # Задача владельцу, которая на самом деле — работа администратора.
        if OWNER_ID in (t.get("managerIds") or []) and ADMIN_WORK.search(body):
            if OWNER_WORK.search(body):
                # Признаки обоих сразу — правило по словам тут врёт:
                # «заявка с сайта» попадала во владельческое из-за слова
                # «сайт», «договорились» — из-за «договор». Спрашиваем
                # модель: она читает задачу целиком и отвечает, что здесь
                # надо сделать и кто это делает.
                verdict = brain.route_task(body) if brain.enabled() else None
                if verdict and verdict.get("уверенность") == "высокая" \
                        and verdict.get("кому") in ("лиза", "дежурный"):
                    to = CHAT_ADMIN if verdict["кому"] == "лиза" \
                        else (_duty() or CHAT_ADMIN)
                    if fix and _reassign(mk, t, to):
                        fixed["разобрано моделью"] += 1
                        log.info("brain: задача %s → %s (%s)", t["id"],
                                 verdict["кому"], verdict.get("почему", ""))
                    continue
                # Модель недоступна, не уверена или считает задачу
                # владельческой — оставляем человеку, как и раньше.
                unclear.append({"id": t["id"], "body": body[:160],
                                "модель": (verdict or {}).get("почему", "")})
                continue
            to = CHAT_ADMIN if TO_CHAT.search(body) else (_duty() or CHAT_ADMIN)
            if fix and _reassign(mk, t, to):
                fixed["передано администратору"] += 1
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
        "требует_меня": {"дублей": dups, "шаблонных": templates,
                         "спорных": len(unclear)},
        "спорные": unclear[:20],
        "нагрузка": {who: dict(sorted(c.items())) for who, c in load.items()},
    }
    try:
        db.set_setting("task_guard", json.dumps(report, ensure_ascii=False)[:4000])
    except Exception:
        log.warning("taskguard: отчёт не сохранился")
    if fixed:
        log.info("taskguard: починено %s", dict(fixed))
    if dups or templates or unclear:
        log.warning("taskguard: разобрать руками — дублей %d, шаблонных %d, "
                    "спорных %d", dups, templates, len(unclear))
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
