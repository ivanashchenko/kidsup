"""Сверка договорённости из звонка с тем, что завели в CRM.

Зачем. 26.08 за один день два клиента получили письма, противоречащие
их же телефонному разговору:

  • Сутулову по телефону назвали 4 800 ₽ за 4 занятия (один день в
    неделю — у мальчика футбол пять раз в неделю и свободен только
    понедельник). Письмо акции объявило 8 200 ₽ за 8 занятий два раза
    в неделю, потому что взяло цену из расписания группы. Отказ пришёл
    через сорок две минуты.
  • Софию записали в группу читающих, хотя в разговоре мама сказала
    «буквы знает, но не читает», и договорились на вторник-четверг —
    а запись ушла в группу вторник-пятница.

Общее у обоих: договорённость звучала голосом и нигде не сохранялась.
Запись в группу — единственный след, и заводят её руками, на слух,
между звонками. Ошибку видно только когда клиент отвечает «мы так не
договаривались», то есть когда он уже уходит.

Что делает модуль. Разбирает расшифровку разговора, достаёт из неё
уровень (читает / не читает), дни, время и частоту, сравнивает с
записью, созданной по этому клиенту в тот же день, и при расхождении
ставит срочную задачу тому, кто звонил. Договорённость в любом случае
ложится комментарием в карточку — чтобы следующий сценарий, письмо или
администратор видели её раньше, чем клиент напомнит сам.

Разбор намеренно консервативен: если фраза читается двояко, модуль
молчит. Ложная тревога дороже пропуска — администратор перестаёт
читать задачи, которые в половине случаев ни о чём.
"""

from __future__ import annotations

import logging
import re
from datetime import date

log = logging.getLogger("kidsup.sverka")

CAT_URGENT = 44337

DAYS = {"понедельник": "пн", "вторник": "вт", "среда": "ср", "среду": "ср",
        "четверг": "чт", "пятница": "пт", "пятницу": "пт",
        "суббота": "сб", "субботу": "сб", "воскресенье": "вс"}

# «читает», «уже читает», «читает и считает» — против «не читает»,
# «буквы знает, но не читает», «пока не читает».
NOT_READING = re.compile(
    r"(не\s+чита|пока\s+не\s+чита|букв[ыу]\s+знает[^.]{0,20}не\s+чита"
    r"|нечита)", re.I)
READING = re.compile(r"(?<!не\s)(уже\s+чита|хорошо\s+чита|читает\s+и\s+счита)", re.I)

# «один раз в неделю», «один день в неделю», «раз в неделю»
ONCE = re.compile(r"(один\s+раз\s+в\s+недел|один\s+день\s+в\s+недел"
                  r"|раз\s+в\s+недел|одн[оу]\s+заняти[ея]\s+в\s+недел)", re.I)
TWICE = re.compile(r"(два\s+раза\s+в\s+недел|дважды\s+в\s+недел)", re.I)

PRICE_SAID = re.compile(r"(\d[\s\d]{2,6})\s*(?:руб|₽|рублей)?", re.I)


def level_from(text: str) -> str | None:
    """«читающие» / «нечитающие» / None, если из разговора не следует."""
    t = text or ""
    if NOT_READING.search(t):
        return "нечитающие"
    if READING.search(t):
        return "читающие"
    return None


def days_from(text: str) -> set[str]:
    """Дни недели, названные в разговоре."""
    found = set()
    low = (text or "").lower()
    for word, short in DAYS.items():
        if word in low:
            found.add(short)
    return found


def frequency_from(text: str) -> str | None:
    """«1/нед», «2/нед» или None."""
    t = text or ""
    if ONCE.search(t):
        return "1/нед"
    if TWICE.search(t):
        return "2/нед"
    return None


def group_level(name: str) -> str | None:
    n = (name or "").lower()
    if "нечит" in n:
        return "нечитающие"
    if "чит" in n:
        return "читающие"
    return None


def group_days(name: str) -> set[str]:
    """Дни из названия группы: «2627_ПШ_вт-чт_19:00…» → {вт, чт}."""
    out = set()
    for m in re.finditer(r"\b(пн|вт|ср|чт|пт|сб|вс)\b", (name or "").lower()):
        out.add(m.group(1))
    return out


def compare(text: str, group_name: str) -> list[str]:
    """Чем запись расходится с разговором. Пустой список — расхождений нет."""
    problems = []
    lvl, glvl = level_from(text), group_level(group_name)
    if lvl and glvl and lvl != glvl:
        problems.append(f"в разговоре ребёнок {lvl}, а группа — {glvl}")
    said, got = days_from(text), group_days(group_name)
    # Сравниваем только когда в разговоре названы дни: во многих звонках
    # время обсуждают без дней, и требовать совпадения там не за что.
    if said and got and not (said & got):
        problems.append(f"договорились на {'/'.join(sorted(said))}, "
                        f"а записан на {'/'.join(sorted(got))}")
    return problems


def summary(text: str, phone: str = "", when: str = "") -> str:
    """Что именно обещали — строкой для комментария в карточку."""
    bits = []
    lvl = level_from(text)
    if lvl:
        bits.append(f"уровень: {lvl}")
    d = days_from(text)
    if d:
        bits.append("дни: " + "/".join(sorted(d)))
    f = frequency_from(text)
    if f:
        bits.append(f"частота: {f}")
    m = re.search(r"абонемент[^.]{0,40}?(\d[\s\d]{2,6})", text or "", re.I)
    if m:
        bits.append("названа цена: " + m.group(1).strip() + " ₽")
    if "пробное" in (text or "").lower():
        bits.append("зовут на пробное")
    return "; ".join(bits) or "конкретных договорённостей не прозвучало"


def check_and_flag(mk, uid: int, text: str, manager_id: int,
                   phone: str = "", when: str = "") -> dict:
    """Записать договорённость в карточку и поднять тревогу при расхождении.

    Возвращает {"комментарий": …, "расхождения": [...], "задача": id|None}.
    """
    out = {"комментарий": "", "расхождения": [], "задача": None}
    line = summary(text, phone, when)
    body = f"Договорённость из звонка {when or date.today().isoformat()}: {line}."
    try:
        mk.post("/v1/company/userComments",
                {"userId": uid, "comment": body[:900], "showToUser": False})
        out["комментарий"] = body
    except Exception as e:
        log.warning("комментарий не записался: %s", str(e)[:80])

    # Записи, созданные по этому клиенту сегодня — их и сверяем.
    try:
        j = mk.get("/v1/company/joins", params={"userId": uid, "limit": 50})
        joins = j.get("joins") if isinstance(j, dict) else j
    except Exception:
        return out
    today = date.today().isoformat()
    fresh = [x for x in (joins or []) if str(x.get("createdAt", ""))[:10] == today]
    if not fresh:
        return out
    try:
        c = mk.get("/v1/company/classes", params={"limit": 500})
        classes = {x["id"]: x.get("name") for x in
                   (c.get("classes") if isinstance(c, dict) else c)}
    except Exception:
        return out
    for x in fresh:
        name = classes.get(x.get("classId")) or ""
        bad = compare(text, name)
        if not bad:
            continue
        out["расхождения"] = bad
        why = "; ".join(bad)
        task = (f"СРОЧНО, запись не совпадает с разговором. "
                f"{('+7' + phone) if phone else ''} — {why}. "
                f"Записан в «{name}». Проверить и перевести.")
        try:
            r = mk.post("/v1/company/tasks", {
                "body": task[:250], "userId": uid,
                "managerIds": [manager_id], "categoryId": CAT_URGENT,
                "isAllDay": True,
                "beginDate": f"{today}T09:00:00+03:00",
                "endDate": f"{today}T20:00:00+03:00"})
            out["задача"] = r.get("id")
            log.warning("расхождение по клиенту %s: %s", uid, why)
        except Exception as e:
            log.warning("задача о расхождении не создалась: %s", str(e)[:80])
    return out
