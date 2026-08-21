"""Уборка задач: то, что осталось после разбора и что я же сам и создал.

Проверка вечером 21.08 нашла шесть вещей. Пять из них — следы моих
собственных правок, и это важнее признать, чем красиво описать.

  A. ЗАДАЧИ-ПРИЗРАКИ. Проверка «закрыта без действия» видит закрытую задачу,
     не находит по клиенту ни звонка, ни сообщения и открывает её заново.
     Но задачу закрываю не только администратор: дубли и цепочки схлопываю
     я. Звонка по ним и не должно было быть — работа идёт по оставшейся
     задаче. Родилось 9 призраков вида «⚠️ Закрыта без действия … [дубль,
     работаем по задаче N]». Причина закрыта в app/sla.py (MY_CLOSURE),
     здесь убираем уже созданное.

  B. СРОЧНЫЕ, УЕХАВШИЕ В БУДУЩЕЕ. Выравнивание нагрузки сдвигало любую
     задачу без названного в тексте срока — и утащило 25 пропущенных
     звонков и новых заявок на 24–26 августа. «Перезвонить в течение
     15 минут» с датой через четыре дня — это не задача, это насмешка.
     Срочные возвращаются на сегодня, а текст переписывается честно:
     звонок был N дней назад, перезваниваем как по обычному контакту.

  C. ПРОТУХШИЕ СВОДКИ. «ПРИОРИТЕТ №1 на 18.08», «ЧЕТВЕРГ: 27 недозвонов
     вчера» — сводка живёт один день, к своей дате она уже неправда.

  D. ПОВТОРЫ БЕЗ КЛИЕНТА. Дедуп искал повторы по клиенту, а у сводок и
     напоминаний клиента нет — 26 одинаковых текстов разъехались по дням.

  E. ДРЕВНИЕ. Четыре задачи 2024 года: «записать на изо», «перезвонить
     насчёт рр». Двухлетняя задача — не работа, а археология; клиента
     возвращаем в обзвон с нормальной подсказкой.

  F. ПУСТЫЕ СМЕНЫ. 27 августа на смене трое — задач ноль. 28-го Ира одна —
     ноль. Это последние дни перед праздником и Днём открытых дверей, когда
     как раз подтверждают приходы. Дни были пусты потому, что в раскладке
     у Иры не значилось 28-е, а у Ани 29-е и 30-е: перенести туда было
     некуда. График взят из смен, названных Борисом.

Запуск:
    python -m app.taskclean show
    python -m app.taskclean apply
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

from . import sync
from . import taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.taskclean")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"

STAFF = {232763: "Ира", 232805: "Аня", 202856: "Лена",
         154181: "Лиза", 84116: "Борис", 229704: "Маша"}
CALLERS = (232763, 232805, 202856)

# Смены, названные Борисом на 21–30 августа.
SHIFTS = {
    232763: ["2026-08-21", "2026-08-25", "2026-08-26", "2026-08-27",
             "2026-08-28"],
    232805: ["2026-08-22", "2026-08-23", "2026-08-24", "2026-08-26",
             "2026-08-27", "2026-08-29", "2026-08-30"],
    202856: ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27"],
    154181: [f"2026-08-{d}" for d in range(21, 31)],
}
# 29 и 30 августа администратор на площадке: праздник и День открытых
# дверей. Телефонных задач туда не кладём — только встреча гостей.
EVENT_DAYS = {"2026-08-29", "2026-08-30"}
NORM = 30                       # сколько задач за смену реально делается

CAT_CALL, CAT_URGENT, CAT_ORG = 104576, 44337, 104578

GHOST = re.compile(r"Закрыта без действия")
MY_CLOSURE = re.compile(r"\[(дубль|закрыто|сведено|остыло)", re.I)
URGENT = re.compile(r"ПРОПУЩЕННЫЙ ЗВОНОК|в течение 15 минут|"
                    r"в течение 5 минут|НОВАЯ ЗАЯВКА", re.I)
# Сводка привязана к своему дню — днём недели, датой или окном «за N дней».
STALE = re.compile(r"на 1[0-9]\.08|ПРИОРИТЕТ №\d на \d|"
                   r"^(?:[🤖🔥🎧]+ ?(?:Клод: )?)?(?:ПОНЕДЕЛЬНИК|ВТОРНИК|СРЕДА|"
                   r"ЧЕТВЕРГ|ПЯТНИЦА|СУББОТА|ВОСКРЕСЕНЬЕ|УТРО [\w ]+?)"
                   r"\s*(?:[,(:—-]|$)|"
                   r"качество карточек за \d дня|вчера \d+ оплат", re.I)
# Сводка сводке рознь. Одни пересчитываются заново каждое утро — «качество
# карточек за 3 дня», «вчера 52 звонка дали 4 записи»: их вчерашний экземпляр
# бесполезен. Другие несут незакрытое обязательство — сумму возврата, имя,
# решение, которого ждут. Такую закрыть по признаку «в заголовке четверг» —
# значит потерять деньги: под это правило попали «возврат 33 400 ₽»
# и «решение по ценам лагеря». Им снимаем привязку ко дню и оставляем в работе.
COUNTED_ANEW = re.compile(r"качество карточек|вчера \d+ оплат|"
                          r"вчера \d+ звонк|дали \d+ запис|"
                          r"недозвон\w* вчера|НЕ обработаны ни разу", re.I)
LIVE_DEBT = re.compile(r"\d[\d  ]{2,}\s*₽|возврат|решение по|согласова|"
                       r"\+7\d{10}|прислать|отправить", re.I)
# Заголовок не вырезаем, а заменяем на «СЕГОДНЯ»: попытка отрезать всё
# до первого двоеточия съедала время внутри текста — «УТРО дня (до 8:30):»
# превращалось в «30):».
WEEKDAY_HEAD = re.compile(r"^((?:[🤖🔥🎧]+ ?(?:Клод: )?)?)"
                          r"(?:УТРО [А-ЯЁA-Z]+|ПОНЕДЕЛЬНИК|ВТОРНИК|СРЕДА|"
                          r"ЧЕТВЕРГ|ПЯТНИЦА|СУББОТА|ВОСКРЕСЕНЬЕ)", re.I)
# «вчера» в задаче, пролежавшей три дня, указывает не туда. Ставим дату.
YESTERDAY = re.compile(r"\bвчера\b", re.I)
WEEKDAYS = ["ПОНЕДЕЛЬНИК", "ВТОРНИК", "СРЕДА", "ЧЕТВЕРГ", "ПЯТНИЦА",
            "СУББОТА", "ВОСКРЕСЕНЬЕ"]
HAS_DEADLINE = re.compile(
    r"(?:перезвон\w*|позвон\w*|напомн\w*|подтверд\w*|встрет\w*)[^.]{0,40}?"
    r"\b\d{1,2}[.\-/]?(?:0?[89])?\b", re.I)
PHONE = re.compile(r"\+?7(\d{10})")


def _mk() -> MoyklassClient:
    return MoyklassClient(sync.get_api_key())


def collect(mk: MoyklassClient) -> list[dict]:
    today = date.today().isoformat()
    out = []
    for mid in STAFF:
        out += [t for t in taskguard.all_tasks(mk, mid)
                if not t.get("isComplete")
                and not t.get("isCompleted")]
    tasks = list({t["id"]: t for t in out}.values())
    json.dump(tasks, open(f"{SP}/clean_tasks.json", "w"), ensure_ascii=False)
    return tasks


def _days_ago(body: str, created: str) -> int:
    try:
        return (date.today() - date.fromisoformat(created[:10])).days
    except Exception:
        return 0


def _urgent_rewrite(body: str, created: str) -> str:
    """Срочность живёт часы. Через день «перезвонить в течение 15 минут» —
    уже не задача, а укор: сделать вовремя нельзя, а бросать ради неё
    текущую работу незачем. Текст переписываем честно, чтобы администратор
    видел настоящий возраст обращения и говорил по нему уместно.

    Пропущенный звонок и оставленная заявка — разные поводы, и здороваться
    по ним надо по-разному: в первом случае человек звонил сам, во втором
    оставил номер на сайте и звонка ждёт."""
    n = _days_ago(body, created)
    if n <= 0:
        return body[:250]
    when = "вчера" if n == 1 else f"{n} дн. назад"
    if re.search(r"НОВАЯ ЗАЯВКА", body, re.I):
        return (f"📞 Заявка с сайта, оставлена {when} — позвонить сегодня. "
                f"Извиниться за задержку («были на занятиях»), выяснить "
                f"возраст и запрос, позвать на 29.08, 30.08 или Неделю "
                f"открытых уроков.")[:250]
    ph = PHONE.search(body)
    num = (" с +7" + ph.group(1)) if ph else ""
    return (f"📞 Пропущенный звонок{num} — был {when}, перезвонить сегодня. "
            f"Человек звонил сам, значит интерес был: спросить, что искали, "
            f"и позвать на 29.08, 30.08 или Неделю открытых уроков.")[:250]


def _undate(body: str, created: str) -> str:
    """Снять с текста привязку ко дню, когда он был написан."""
    out = WEEKDAY_HEAD.sub(r"\1СЕГОДНЯ", body)
    try:
        was = date.fromisoformat(created[:10]) - timedelta(days=1)
        out = YESTERDAY.sub(f"{was.day:02d}.{was.month:02d}", out)
    except Exception:
        pass
    return out.strip()


def decide(tasks: list[dict]) -> list[dict]:
    today = date.today().isoformat()
    out = []

    # D: повторы среди задач без клиента — ключом служит текст с забитыми
    # цифрами, иначе «без даты рождения 40» и «…34» читаются как разные.
    nu = [t for t in tasks if not t.get("userId")]
    seen_key: dict[str, int] = {}
    for t in sorted(nu, key=lambda x: ((x.get("beginDate") or ""), x["id"])):
        k = re.sub(r"\d", "#", (t.get("body") or ""))[:60]
        seen_key.setdefault(k, t["id"])

    for t in tasks:
        body = (t.get("body") or "").strip()
        created = (t.get("createdAt") or "")
        cur = (t.get("beginDate") or "")[:10]
        mgr = (t.get("managerIds") or [None])[0]
        act, why, new_body, new_day, new_cat = "оставить", "", None, None, None

        if GHOST.search(body) and MY_CLOSURE.search(body):
            act, why = "закрыть", "призрак поверх моего же закрытия"

        elif STALE.search(body):
            # Сводка на сегодняшний день недели — не протухшая, а текущая.
            if re.match(r"^(?:[🤖🔥🎧]+ ?(?:Клод: )?)?" + WEEKDAYS[date.today().weekday()],
                        body, re.I):
                pass
            elif COUNTED_ANEW.search(body) and not LIVE_DEBT.search(body):
                act, why = "закрыть", "сводка пересчитывается заново каждый день"
            elif LIVE_DEBT.search(body):
                act, why = "срочное", "в сводке незакрытый вопрос — снял привязку ко дню"
                new_body = _undate(body, created)[:250]
                new_day = today
            else:
                act, why = "закрыть", "сводка привязана к прошедшему дню"

        elif created[:4] <= "2025":
            act, why = "закрыть", f"задача от {created[:10]} — устарела"

        elif URGENT.search(body):
            if cur > today:
                act, why = "срочное", f"срочная задача стояла на {cur[8:10]}.08"
                new_body, new_day = _urgent_rewrite(body, created), today
                new_cat = CAT_CALL if _days_ago(body, created) > 0 else CAT_URGENT
            elif _days_ago(body, created) > 1 and cur == today:
                act, why = "срочное", "звонок был не сегодня — текст врал"
                new_body, new_cat = _urgent_rewrite(body, created), CAT_CALL

        elif not t.get("userId"):
            keep = seen_key.get(re.sub(r"\d", "#", body)[:60])
            if keep is not None and keep != t["id"]:
                act = "закрыть"
                why = f"такой же текст, работаем по задаче {keep}"

        if act == "оставить" and not t.get("categoryId"):
            act, why, new_cat = "категория", "задача была без категории", (
                CAT_CALL if t.get("userId") else CAT_ORG)

        out.append({"id": t["id"], "uid": t.get("userId"), "mgr": mgr,
                    "cur": cur, "act": act, "why": why, "new_body": new_body,
                    "new_day": new_day, "new_cat": new_cat,
                    "fixed": bool(HAS_DEADLINE.search(body))
                             or bool(URGENT.search(body)),
                    "body": body[:90]})

    _balance(out, today)
    json.dump(out, open(f"{SP}/clean.json", "w"), ensure_ascii=False)
    return out


def _balance(out: list[dict], today: str) -> None:
    """F: разложить задачи звонящих ровно по их сменам.

    Сегодняшний день не трогаем — он уже идёт, администратор работает по
    своему списку. Дни событий получают только организационное."""
    live = [x for x in out if x["act"] not in ("закрыть",)]
    for mid in CALLERS:
        days = [d for d in SHIFTS[mid] if d >= today and d not in EVENT_DAYS]
        if len(days) < 2:
            continue
        mine = [x for x in live if x["mgr"] == mid and x["cur"] in days]
        load = Counter(x["cur"] for x in mine)
        movable = [x for x in mine
                   if not x["fixed"] and x["cur"] != today
                   and x["act"] in ("оставить", "категория")]
        # сначала снимаем с перегруженных, потом добираем пустые до нормы
        for _ in range(400):
            # Самый нагруженный день — часто сегодняшний, а сегодня трогать
            # нельзя: смена идёт, администратор работает по своему списку.
            # Поэтому донором может быть только день, откуда есть что снять,
            # иначе цикл упирается в сегодня и встаёт, оставляя дальние
            # смены пустыми — ровно это и случилось с 27 и 28 августа.
            donors = {x["cur"] for x in movable}
            if not donors:
                break
            hi = max(donors, key=lambda d: load[d])
            lo = min(days, key=lambda d: load[d])
            if load[hi] - load[lo] <= 3:
                break
            cand = [x for x in movable if x["cur"] == hi]
            if not cand:
                break
            x = cand.pop()
            movable.remove(x)
            load[hi] -= 1
            load[lo] += 1
            x["cur"], x["new_day"] = lo, lo
            if x["act"] == "оставить":
                x["act"] = "перенести"
            x["why"] = (x["why"] + "; " if x["why"] else "") + \
                f"смена {lo[8:10]}.08 была пустой"


def apply() -> dict:
    dec = json.load(open(f"{SP}/clean.json"))
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
            b = {k: t.get(k) for k in ("userId", "classIds", "filialIds",
                                       "ownerId", "reminds", "managerIds")}
            b = {k: v for k, v in b.items() if v is not None}
            b["categoryId"] = it.get("new_cat") or t.get("categoryId") or CAT_CALL
            b["isAllDay"] = False
            day = it.get("new_day") or (t.get("beginDate") or "")[:10]
            hour = taskguard.msk_hour(t.get("beginDate"))
            b["beginDate"] = f"{day}T{hour}:00+03:00"
            b["endDate"] = f"{day}T20:00:00+03:00"
            if it["act"] == "закрыть":
                b["body"] = f"[убрано: {it['why']}] {t.get('body') or ''}"[:250]
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
    for act in ("закрыть", "срочное", "перенести", "категория"):
        sel = [x for x in dec if x["act"] == act]
        if not sel:
            continue
        print(f"\n=== {act.upper()}: {len(sel)}")
        for x in sel[:5]:
            print(f"   {x['why']}")
            print(f"      было:  {x['body'][:74]}")
            if x.get("new_body"):
                print(f"      стало: {x['new_body'][:74]}")
    print("\nнагрузка по сменам после уборки:")
    live = [x for x in dec if x["act"] != "закрыть"]
    for mid in CALLERS:
        c = Counter(x["cur"] for x in live if x["mgr"] == mid)
        print(f"   {STAFF[mid]:5s}", {d: c[d] for d in SHIFTS[mid] if c[d]})


if __name__ == "__main__":
    main()
