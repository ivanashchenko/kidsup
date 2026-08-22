"""Рассылка по последней неделе лагеря: 24–28 августа.

Зачем. За лето в клубе побывали 175 детей. Последнюю неделю купил ОДИН.
Динамика по неделям: с 3 августа стартовало 15 детей, с 10-го — 6,
с 17-го — 5, на последнюю — один. Неделя рассыпалась, а программа на неё
сделана и педагоги заняты.

Кому пишем. 174 семьи, которые были у нас этим летом и последнюю неделю
не взяли. Это самая тёплая аудитория, какая бывает: они знают центр,
уже платили, ребёнок адаптирован и знаком с педагогами. Им не надо
объяснять, кто мы.

Куда пишем. Туда, где семья уже переписывается: Telegram, MAX, WhatsApp.
WABA — только тем, кого нигде больше нет: там каждое сообщение платное.

Тон. Это не «успейте купить». Ребёнок вернётся в школу через неделю,
и последние летние дни — сами по себе повод. Программа сильнее скидки,
поэтому в тексте сначала что будет, а потом сколько стоит.

Запуск:
    python -m app.campmail show    — кому и что уйдёт
    python -m app.campmail send    — отправить
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter, defaultdict

from . import sync, taskguard, wazzup
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.campmail")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"

# Лагерь всех лет: 2526_ЛК — лето 2026, OLD_ЛК и 2024_ЛК — прошлые годы.
CAMP_RE = re.compile(r"_ЛК|летний клуб|лагер", re.I)
CAMP_THIS = re.compile(r"2526_ЛК", re.I)      # только этим летом
LAST_WEEK = ("2026-08-24", "2026-08-28")

# Программа — из афиши смены. Не пересказываем своими словами: у неё уже
# есть язык, который родители видели в соцсетях и на сайте.
PROGRAMME = (
    "🌟 24–28 августа — «ШОУ-БИЗНЕС: В ПОГОНЕ ЗА ПРОДЮСЕРОМ»\n\n"
    "На пять дней центр превращается в фабрику звёзд:\n"
    "👑 Баттлы лейблов — команды создают свои бренды\n"
    "👗 Fashion Night — стиль, грим и модный показ\n"
    "🎤 Голос и PRO-движение — караоке и танцевальные баттлы\n"
    "🎬 Блогинг и кинофестиваль — снимаем ролики, выходим на красную дорожку\n"
    "💰 Большая экономическая игра — продюсеры, пиарщики, предприниматели\n\n"
    "🏆 Финал — гала-концерт и премия «Грэмми Академии»"
)


def _was(name: str) -> str:
    """«был» или «была». Ошибка тут заметна с первого слова: «Вероника был
    у нас» читается как машинная рассылка, и дальше текст уже не спасти."""
    from .hint import is_female
    return "была" if is_female(name) else "был"


def _first_name(full: str) -> str:
    parts = [w for w in (full or "").split("(")[0].split() if w]
    if len(parts) >= 2 and parts[1][:1].isupper():
        return parts[1]
    return parts[0] if parts else ""


def text_past(name: str) -> str:
    """Для тех, кто был в лагере в прошлые годы, но этим летом не приходил.

    Им нельзя писать «вы были у нас этим летом» — это неправда, и они
    это заметят первым же словом. Заход другой: мы помним, зовём вернуться,
    и повод — самая яркая смена сезона."""
    child = _first_name(name)
    who = f"{child} {_was(child)} у нас в лагере" if child \
        else "Ваш ребёнок был у нас в лагере"
    return (
        f"Здравствуйте! {who} — и мы помним. Этим летом вас не было, "
        f"а последняя смена как раз такая, ради которой стоит вернуться.\n\n"
        f"{PROGRAMME}\n\n"
        f"Пять дней перед школой: съёмки, сцена, своя команда. Хороший "
        f"способ закончить лето громко, а не на «уже пора собирать портфель».\n\n"
        f"Есть полный день и полдня.\n"
        f"Первый день бесплатный — можно прийти и посмотреть, ни к чему "
        f"не обязывает. Пробная неделя — 15 800 ₽.\n\n"
        f"Подробности: kidsup.ru/summercamp\n"
        f"Напишите, если интересно, — расскажу про места и время."
    )


def text_for(name: str) -> str:
    """Текст для семьи, которая была у нас этим летом."""
    child = _first_name(name)
    hello = f"Здравствуйте! {child} {_was(child)} у нас этим летом" if child \
        else "Здравствуйте! Ваш ребёнок был у нас этим летом"
    return (
        f"{hello} — и последнюю неделю августа мы придумали так, чтобы "
        f"уходить в школу было не грустно.\n\n"
        f"{PROGRAMME}\n\n"
        f"Это последние летние дни, и они получаются шумные: съёмки, сцена, "
        f"своя команда. Хороший способ вернуться к школе на подъёме, а не "
        f"с ощущением, что лето кончилось.\n\n"
        f"Есть полный день и полдня — как вам удобнее.\n"
        f"Пробная неделя — 15 800 ₽. Первый день бесплатный: можно прийти "
        f"и посмотреть, ни к чему не обязывает.\n\n"
        f"Подробности: kidsup.ru/summercamp\n"
        f"Напишите, если интересно, — расскажу про свободные места и время."
    )


def collect() -> list[dict]:
    mk = MoyklassClient(sync.get_api_key())
    try:
        rc = mk.get("/v1/company/classes", {"limit": 500})
        cls = {c["id"]: (c.get("name") or "")
               for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
        camp_ids = {cid for cid, nm in cls.items() if CAMP_RE.search(nm)}
        subs = taskguard.pull_all(mk, "/v1/company/userSubscriptions",
                                  "subscriptions", cache_hours=6)
        camp = [s for s in subs
                if set(s.get("classIds") or []) & camp_ids and s.get("userId")]
        this_ids = {cid for cid, nm in cls.items() if CAMP_THIS.search(nm)}
        by_year = defaultdict(set)
        for x in camp:
            y = (x.get("beginDate") or "")[:4]
            if y:
                by_year[y].add(x["userId"])
        summer = {x["userId"] for x in camp
                  if set(x.get("classIds") or []) & this_ids}
        took_last = {x["userId"] for x in camp
                     if LAST_WEEK[0] <= (x.get("beginDate") or "")[:10] <= LAST_WEEK[1]}
        past = (by_year.get("2025", set()) | by_year.get("2024", set())) - summer
        target = [(uid, "этим летом") for uid in sorted(summer - took_last)] + \
                 [(uid, "в прошлые годы") for uid in sorted(past)]

        # Карточки берём одной выборкой, а не по одной: 174 отдельных запроса
        # к API — это несколько минут ожидания на ровном месте.
        # Карточки берём из локальной копии базы: выгрузка 5800 клиентов
        # через API занимает минуты, а нам нужны только имя, телефон
        # и статус — они синхронизируются и лежат рядом.
        users = {}
        from . import db
        with db.get_conn() as conn:
            for uid, name, phone, state in conn.execute(
                    "SELECT id, name, phone, client_state_id FROM users"):
                users[uid] = {"id": uid, "name": name or "", "phone": phone or "",
                              "clientStateId": state}
        out = []
        for uid, wave in target:
            u = users.get(uid)
            if not u:
                continue
            # Кому мы сознательно не пишем: отказ, некачественный, «не писать».
            if u.get("clientStateId") in {146328, 125954, 125957}:
                continue
            phone = "".join(c for c in str(u.get("phone") or "") if c.isdigit())
            if len(phone) < 11:
                continue
            out.append({"uid": uid, "wave": wave, "name": u.get("name") or "",
                        "phone": phone[-11:],
                        "channel": wazzup.best_channel(phone, uid) or "wapi"})
    finally:
        mk.close()
    json.dump(out, open(f"{SP}/campmail.json", "w"), ensure_ascii=False)
    return out


def send(dry_run: bool = True, limit: int = 0) -> dict:
    rows = json.load(open(f"{SP}/campmail.json"))
    if limit:
        rows = rows[:limit]
    stat: Counter = Counter()
    for r in rows:
        txt = text_past(r["name"]) if r.get("wave") == "в прошлые годы" \
            else text_for(r["name"])
        try:
            wazzup.send(r["phone"], txt, mode="cascade", dry_run=dry_run,
                        transports=[r["channel"]])
            stat[r["channel"]] += 1
        except Exception:
            stat["ошибка"] += 1
        time.sleep(0.5 if not dry_run else 0)
    return dict(stat)


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "send":
        dry = "--real" not in sys.argv
        lim = next((int(a) for a in sys.argv[2:] if a.isdigit()), 0)
        print(("ПРОБНЫЙ ПРОГОН: " if dry else "ОТПРАВКА: "), send(dry, lim))
        return
    rows = collect()
    print(f"кому уйдёт: {len(rows)} семей")
    print("по волнам :", dict(Counter(r["wave"] for r in rows)))
    print("по каналам:", dict(Counter(r["channel"] for r in rows)))
    this = next((r for r in rows if r["wave"] == "этим летом"), None)
    past = next((r for r in rows if r["wave"] == "в прошлые годы"), None)
    if this:
        print("\n───── БЫЛИ ЭТИМ ЛЕТОМ ─────\n")
        print(text_for(this["name"]))
    if past:
        print("\n───── БЫЛИ В ПРОШЛЫЕ ГОДЫ ─────\n")
        print(text_past(past["name"]))


if __name__ == "__main__":
    main()
