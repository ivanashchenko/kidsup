"""Разбор звонков Надежды Иванащенко по нашей базе.

Надежда — директор Клуба Буракова в Люберцах и мама владельца. Она звонит
по базе KidsUP со своего добавочного 20, который до 21.08 считался чужим
целиком: за неделю 46 исходящих просто выбрасывались из разбора.

Чей звонок — решает не добавочный, а собеседник. Если номер есть в нашей
CRM, разговор наш: его надо разобрать, записать в карточку и превратить
в запись на занятие. Если номера у нас нет — это её люберецкий клиент,
мы туда не лезем.

Что делает модуль после расшифровки:

  ЗАПИСЫВАЕТ САМ — только когда сомнений нет: в разговоре названы предмет
    И день с временем, и по ним однозначно находится ровно одна группа
    нового сезона, где есть места. Одна подходящая группа — записываем,
    две и больше — не угадываем.
  ОТДАЁТ ВЛАДЕЛЬЦУ — во всех остальных случаях: договорённость есть,
    а куда именно записывать, из разговора не следует. Такие идут
    в отчёт с цитатой и ссылкой на карточку, чтобы решение занимало
    минуту, а не выяснение заново.

Комментарий в карточку пишется всегда, даже когда записи не выходит:
следующий, кто откроет карточку, должен видеть, что с человеком уже
говорили и о чём.

Запуск:
    python -m app.nadezhda pull      — какие звонки за сегодня наши
    python -m app.nadezhda run       — разобрать и оформить
    python -m app.nadezhda report    — отчёт владельцу
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

from . import calltools, sync
from . import taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.nadezhda")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"
MSK = timezone(timedelta(hours=3))

EXT = "20"
OWNER = 84116
CAT_URGENT, CAT_CALL = 44337, 104576
ACTIVE = {2, 50509, 58131, 58132, 83760}
MIN_TALK = 20          # короче — разговора не было, разбирать нечего

# «Некачественный лид» — статус, в который мы складывали тех, кто звонил
# не нам, а в Клуб Буракова. Номер есть в нашей CRM, но клиент не наш:
# такие разговоры Надежды разбирать не нужно и тем более нельзя записывать
# в наши группы. Это единственный статус-исключение: «отказ» и «архив»
# означают наших клиентов, просто остывших, и их возвращение — как раз
# то, ради чего она звонит.
NOT_OURS = {125954}

# Предмет из речи. Порядок важен: «подготовка к школе» ловится раньше «школы».
SUBJECT_WORDS = [
    ("подготовка к школе", r"подготовк\w*\s+к\s+школ|\bпэшэ\b|\bпш\b|"
                           r"готов\w*\s+к\s+школ|букв\w*\s+и\s+цифр"),
    ("нулевой класс", r"нулев\w*\s+класс"),
    ("английский", r"английск|инглиш|english"),
    ("музыка и речь", r"музык\w*\s+и\s+реч|музыкальн\w*\s+развит"),
    ("раннее развитие", r"ранн\w*\s+развит|развивашк"),
    ("мини-сад", r"мини[- ]?сад|садик"),
    ("логопед", r"логопед"),
    ("ментальная арифметика", r"ментальн|ментал\b"),
    ("шахматы", r"шахмат"),
    ("ИЗО", r"\bизо\b|рисован|живопис"),
]
DAY_WORDS = {"понедельник": "пн", "вторник": "вт", "сред": "ср", "четверг": "чт",
             "пятниц": "пт", "суббот": "сб", "воскресен": "вс"}
TIME_RE = re.compile(r"\b(\d{1,2})[:.\s]?(\d{2})?\s*(?:час\w*|:00)?\b")
# Родители называют время словами чаще, чем цифрами: «в пять», «в полшестого».
# Вечерние часы разворачиваем в 24-часовые — занятий в 5 утра у нас нет.
WORD_HOUR = {"час": 13, "два": 14, "три": 15, "четыре": 16, "пять": 17,
             "шесть": 18, "семь": 19, "восемь": 20, "девять": 9, "десять": 10,
             "одиннадцать": 11, "двенадцать": 12}
DONE_WORDS = re.compile(
    r"записыва|записал|запишите|запишем|запиш\w+ вас|придём|придем|приведу|"
    r"будем ходить|нам подходит|нас устраивает|давайте", re.I)
REFUSE = re.compile(r"не интересн|не актуальн|не будем|отказ|не подход|"
                    r"мы уже ходим|нашли другой", re.I)


def _digits(x) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())[-10:]


def our_calls(day: str | None = None) -> list[dict]:
    """Звонки Надежды, где собеседник есть в нашей базе."""
    from . import mango
    d = day or date.today().isoformat()
    start = datetime.fromisoformat(d).replace(tzinfo=MSK)
    rows = mango.calls(start, min(start + timedelta(days=1), datetime.now(MSK)))
    mine = []
    for r in rows:
        if r.get("from_ext") != EXT and r.get("to_ext") != EXT:
            continue
        talk = (r["finish"] - r["answer"]) if r.get("answer") else 0
        num = _digits(r["to_num"] if r.get("from_ext") == EXT else r["from_num"])
        if len(num) < 10:
            continue
        mine.append({"num": num, "talk": talk,
                     "ts": datetime.fromtimestamp(r["start"], MSK).strftime("%H:%M"),
                     "dir": "out" if r.get("from_ext") == EXT else "in"})
    if not mine:
        return []
    mk = MoyklassClient(sync.get_api_key())
    out = []
    try:
        for c in mine:
            try:
                r = mk.get("/v1/company/users", {"phone": c["num"], "limit": 2})
                us = r.get("users") or []
            except Exception:
                us = []
            if not us:
                # Номера нет у нас — это её люберецкий клиент, не наше дело.
                continue
            u = us[0]
            if u.get("clientStateId") in NOT_OURS:
                log.info("+7%s — «некачественный лид»: это клиент Клуба "
                         "Буракова, разбор пропускаю", c["num"])
                continue
            c.update(uid=u["id"], name=u.get("name") or "",
                     state=u.get("clientStateId"))
            out.append(c)
            time.sleep(0.32)
    finally:
        mk.close()
    json.dump(out, open(f"{SP}/nadezhda_calls.json", "w"), ensure_ascii=False)
    return out


def parse(text: str) -> dict:
    """Что из разговора можно понять наверняка."""
    t = (text or "").lower()
    subs = [name for name, pat in SUBJECT_WORDS if re.search(pat, t)]
    days = sorted({v for k, v in DAY_WORDS.items() if k in t})
    times = []
    for m in TIME_RE.finditer(t):
        h = int(m.group(1))
        if 9 <= h <= 20:
            times.append(f"{h:02d}:{m.group(2) or '00'}")
    for word, h in WORD_HOUR.items():
        if re.search(rf"\bв\s+{word}\b|\b{word}\s+час", t):
            times.append(f"{h:02d}:00")
    return {"предметы": subs, "дни": days, "время": sorted(set(times)),
            "договорились": bool(DONE_WORDS.search(t)),
            "отказ": bool(REFUSE.search(t))}


def match_group(mk: MoyklassClient, got: dict) -> list[dict]:
    """Группы нового сезона, подходящие под сказанное в разговоре."""
    if not got["предметы"]:
        return []
    from . import socialfactory as sf
    f = sf.facts()
    cand = [g for g in f["группы"]
            if g["subject"] in got["предметы"] and g["free"] > 0]
    if got["дни"]:
        cand = [g for g in cand
                if any(d in (g["when"] or "").lower() for d in got["дни"])] or cand
    if got["время"]:
        exact = [g for g in cand if any(t in (g["when"] or "") for t in got["время"])]
        cand = exact or cand
    return cand


def run(day: str | None = None, apply: bool = True) -> dict:
    calls = our_calls(day)
    if not calls:
        log.info("звонков Надежды по нашей базе за день нет")
        return {"звонков": 0}
    rows = calltools.pull(minutes=24 * 60)
    by_num = {}
    for r in rows:
        n = _digits(r.get("to") or r.get("from"))
        by_num.setdefault(n, r)
    texts = {}
    got = [c for c in calls if c["talk"] >= MIN_TALK]
    if got:
        try:
            items = calltools.transcribe(calltools.fetch(min_dur=MIN_TALK))
            for it in items:
                texts[_digits(it.get("phone"))] = it.get("text") or ""
        except Exception:
            log.warning("расшифровка не удалась — разберу по метаданным")

    mk = MoyklassClient(sync.get_api_key())
    stat = {"звонков": len(calls), "записал": 0, "владельцу": 0, "коротких": 0}
    report = []
    try:
        for c in calls:
            if c["talk"] < MIN_TALK:
                stat["коротких"] += 1
                continue
            text = texts.get(c["num"], "")
            info = parse(text)
            cand = match_group(mk, info) if info["договорились"] else []
            note = (f"📞 {date.today().strftime('%d.%m')} {c['ts']}, "
                    f"{'исходящий' if c['dir'] == 'out' else 'входящий'}, "
                    f"{c['talk'] // 60} мин {c['talk'] % 60} с. "
                    f"Звонила Надежда (Клуб Буракова) по нашей базе. ")
            if info["отказ"]:
                note += "Прозвучал отказ — в воронку не возвращаем."
            elif info["договорились"]:
                note += ("Договорённость есть. Предмет: "
                         + (", ".join(info["предметы"]) or "не назван")
                         + ". Время: "
                         + (" ".join(info["дни"] + info["время"]) or "не названо") + ".")
            else:
                note += "Явной договорённости в разговоре нет."
            if apply:
                try:
                    mk.post("/v1/company/userComments",
                            {"userId": c["uid"], "showToUser": False,
                             "comment": (note + "\n\nРасшифровка: " + text)[:995]})
                except Exception:
                    log.warning("комментарий не записался: %s", c["uid"])

            if len(cand) == 1 and info["договорились"] and not info["отказ"]:
                g = cand[0]
                if apply:
                    try:
                        mk.post("/v1/company/joins",
                                {"userId": c["uid"], "classId": g["id"],
                                 "statusId": 58132})   # записался на пробное
                        stat["записал"] += 1
                    except Exception:
                        log.warning("запись не создалась: %s → %s", c["uid"], g["name"])
                        cand = []
                report.append({"кто": c["name"], "тел": c["num"], "uid": c["uid"],
                               "итог": "записан", "группа": g["name"],
                               "цитата": text[:300]})
            elif not info["отказ"]:
                stat["владельцу"] += 1
                why = ("несколько подходящих групп" if len(cand) > 1
                       else "не названы предмет или время"
                       if not info["предметы"] or not (info["дни"] or info["время"])
                       else "нет свободной группы под сказанное")
                report.append({"кто": c["name"], "тел": c["num"], "uid": c["uid"],
                               "итог": "решает владелец", "почему": why,
                               "варианты": [g["name"] for g in cand[:4]],
                               "цитата": text[:300]})
                if apply:
                    body = (f"Надежда говорила с {c['name'] or c['num']} "
                            f"(+7{c['num']}) в {c['ts']}. Договорённость есть, "
                            f"но {why}. Куда записать — реши и оформи, "
                            f"расшифровка в карточке.")
                    try:
                        mk.post("/v1/company/tasks",
                                {"managerIds": [OWNER], "userId": c["uid"],
                                 "categoryId": CAT_URGENT, "isAllDay": False,
                                 "beginDate": f"{date.today()}T09:00:00+03:00",
                                 "endDate": f"{date.today()}T20:00:00+03:00",
                                 "body": body[:250]})
                    except Exception:
                        log.warning("задача владельцу не создалась")
    finally:
        mk.close()
    json.dump(report, open(f"{SP}/nadezhda_report.json", "w"), ensure_ascii=False)
    log.info("Надежда: %s", stat)
    return stat


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pull"
    if cmd == "pull":
        cs = our_calls()
        print(f"звонков Надежды по нашей базе: {len(cs)}")
        for c in cs:
            print(f"   {c['ts']} {c['dir']} {c['talk']:4d}с  "
                  f"{c['name'][:28]:30s} +7{c['num']}")
    elif cmd == "run":
        print(run())
    else:
        try:
            rep = json.load(open(f"{SP}/nadezhda_report.json"))
        except Exception:
            rep = []
        for r in rep:
            print(f"\n{r['кто']} +7{r['тел']} — {r['итог']}")
            if r.get("группа"):
                print("   группа:", r["группа"])
            if r.get("почему"):
                print("   почему владельцу:", r["почему"])
            if r.get("варианты"):
                print("   варианты:", "; ".join(r["варианты"]))


if __name__ == "__main__":
    main()
