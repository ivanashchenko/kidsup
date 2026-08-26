"""Персональное письмо тем, кто записался, но ещё не оплатил.

Зачем. 25.08 выяснилось: на новый учебный год записано 128 семей,
а деньги внесли 32. Остальные 96 — намерение, которое легко растворится
в сентябрьской суете. При среднем абонементе это около 770 000 ₽,
висящих в воздухе.

Почему письмо честное, а не давящее. У нас есть настоящий повод, а не
выдуманный дедлайн: до 30 августа сентябрь идёт по ценам прошлого года,
с 1 сентября — по новым. Мы не придумываем срочность, мы предупреждаем
о том, что и так произойдёт. Человек, узнавший о повышении постфактум,
обижается сильнее, чем тот, кого предупредили.

Что делает письмо персональным. Не «оплатите абонемент», а «Софию мы
записали на подготовку к школе, вторник и пятница в 19:00; восемь занятий
сейчас 8 200 ₽, с 1 сентября 8 550 ₽». Человек видит свою группу, своё
время и свою цену — и ему не надо ничего вспоминать и уточнять.

Запуск:
    python -m app.akciya            — кому уйдёт и примеры писем
    python -m app.akciya queue      — поставить в очередь отправки
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict

from . import db, sync, taskguard, wazzup
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.akciya")

ACTIVE_JOIN = {2, 50509, 58131, 58132, 83760}
SKIP_STATE = {146328, 125954, 125957}
# Владелец 26.08 назвал 31-е — и это совпадает с тем, что администраторы
# уже говорят в разговорах и что написано на сайте. Держим одну дату везде:
# разъехавшийся на день дедлайн выглядит как обман, а не как мелочь.
DEADLINE = "31 августа"

# Цены сверены с прайсом на 25.08.2026: слева цена прошлого учебного года
# (действует по 30 августа), справа — с 1 сентября. Восемь занятий, то есть
# два раза в неделю: это самый частый абонемент, с него и начинаем разговор.
PRICE = {
    # Сверено с боевым прайсом (app/main.py, PRICES) 25.08.2026. Свои
    # числа держать здесь нельзя: в первой версии я выписал их по памяти
    # и получил 8 550 ₽ там, где в прайсе и на сайте 8 400 ₽. Девяносто два
    # письма уже стояли в очереди — клиент увидел бы в письме одну цену,
    # на сайте другую, и завышенную именно в «страшной» половине.
    "Подготовка к школе": (8200, 8400),
    "Английский": (8200, 8400),
    "Раннее развитие": (7600, 7800),        # «Первая школа»
    "Музыка и речь": (8200, 8600),
    "Лицей для малышей": (7600, 7800),
    "ИЗО-студия": (6800, 7000),
    "Ментальная арифметика": (8000, 8600),  # 4 занятия по 90 минут
    "Шахматы": (6800, 7000),
}
# У сада и логопеда другая механика оплаты, цену в письме не называем:
# у мини-сада абонемент считается по числу дней, у логопеда — по факту
# диагностики. Обещать им «8 200 за восемь занятий» было бы враньём.
NO_PRICE = {"Мини-сад", "Нулевой класс", "Логопед"}

_SUBJ = (("_ПШ_", "Подготовка к школе"), ("_АЯ_", "Английский"),
         ("Первая школа", "Раннее развитие"), ("Музыка и речь", "Музыка и речь"),
         ("Лицей", "Лицей для малышей"), ("ини-сад", "Мини-сад"),
         ("_НК_", "Нулевой класс"), ("нулев", "Нулевой класс"),
         ("ИЗО", "ИЗО-студия"), ("ШАХ", "Шахматы"),
         ("_МА_", "Ментальная арифметика"), ("ЛГ", "Логопед"))


def subject(name: str) -> str:
    for key, label in _SUBJ:
        if key.lower() in name.lower():
            return label
    return "занятия"


def when(name: str) -> str:
    """Дни и время из названия группы: «2627_ПШ_вт-пт_19:00_…» → «вт-пт, 19:00»."""
    days = re.search(r"(пн|вт|ср|чт|пт|сб|вс)\s*[-–]\s*(пн|вт|ср|чт|пт|сб|вс)"
                     r"|(пн|вт|ср|чт|пт|сб|вс)\s*\d{1,2}:\d{2}\s*\+\s*"
                     r"(пн|вт|ср|чт|пт|сб|вс)\s*\d{1,2}:\d{2}"
                     r"|(?<=_)(пн|вт|ср|чт|пт|сб|вс)(?=_)", name, re.I)
    time = re.search(r"(\d{1,2}:\d{2})", name)
    out = []
    if days:
        out.append(re.sub(r"\s*[-–]\s*", "–", days.group(0)).replace("_", " ").strip())
    if time:
        out.append(time.group(1))
    return ", ".join(out)


def _child_for(full: str) -> str:
    """«для Виктории», «для Льва» — или «для вашего ребёнка»."""
    from .autopilot import _genitive
    child = _first_name(full)
    if not child or len(child) < 2 or any(c.isdigit() for c in child):
        return "вашего ребёнка"
    if not any("а" <= c.lower() <= "я" for c in child):
        return child
    return _genitive(child)


def _first_name(full: str) -> str:
    parts = [w for w in (full or "").split("(")[0].split() if w]
    if len(parts) >= 2 and parts[1][:1].isupper():
        return parts[1]
    return parts[0] if parts else ""


def collect() -> list[dict]:
    """Кто записан на 2026/27 и не внёс денег с 1 августа."""
    mk = MoyklassClient(sync.get_api_key())
    try:
        joins = taskguard.pull_all(mk, "/v1/company/joins", "joins")
        subs = taskguard.pull_all(mk, "/v1/company/userSubscriptions",
                                  "subscriptions", cache_hours=6)
        users = {u["id"]: u for u in
                 taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=2)}
        rc = mk.get("/v1/company/classes", {"limit": 500})
        cls = {c["id"]: (c.get("name") or "")
               for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
    finally:
        mk.close()

    mine: dict = defaultdict(list)
    for j in joins:
        nm = cls.get(j.get("classId"), "")
        if not nm.startswith("2627") or "аявк" in nm.lower():
            continue
        if j.get("statusId") in ACTIVE_JOIN:
            mine[j["userId"]].append(nm)
    paid = {s["userId"] for s in subs
            if (s.get("stats") or {}).get("totalPayed", 0) > 0
            and (s.get("beginDate") or "")[:10] >= "2026-08-01"}

    out = []
    for uid, groups in mine.items():
        if uid in paid:
            continue
        u = users.get(uid)
        if not u or u.get("clientStateId") in SKIP_STATE:
            continue
        phone = "".join(c for c in str(u.get("phone") or "") if c.isdigit())[-10:]
        if len(phone) != 10:
            continue
        groups = list(dict.fromkeys(groups))
        out.append({"uid": uid, "phone": phone,
                    "name": (u.get("name") or "").strip(),
                    "groups": groups})
    # Семья с двумя детьми — это два клиента в CRM и ОДИН телефон.
    # 25.08 предпросмотр показал, что маме ушли бы два письма подряд,
    # почти одинаковых, — ровно то, на что в этот же день жаловались
    # клиенты. Склеиваем в одно письмо с детьми списком.
    by_phone: dict = {}
    for r in out:
        cur = by_phone.get(r["phone"])
        if cur is None:
            r["kids"] = [{"name": r["name"], "groups": r["groups"]}]
            by_phone[r["phone"]] = r
        else:
            cur["kids"].append({"name": r["name"], "groups": r["groups"]})
            cur["groups"] = list(dict.fromkeys(cur["groups"] + r["groups"]))
    return list(by_phone.values())


def text_for(row: dict) -> str:
    """Письмо про свою группу и свою цену."""
    kids = row.get("kids") or [{"name": row["name"], "groups": row["groups"]}]
    if len(kids) == 1:
        who = _child_for(row["name"])
        lines = [f"• {subject(g)}" + (f" — {when(g)}" if when(g) else "")
                 for g in row["groups"][:3]]
    else:
        # двое и больше — пишем по ребёнку, иначе родитель не поймёт,
        # кто из детей куда записан
        who = "ваших детей"
        lines = []
        for k in kids:
            nm = _first_name(k["name"]) or "ребёнок"
            for g in k["groups"][:2]:
                w = when(g)
                lines.append(f"• {nm} — {subject(g)}" + (f", {w}" if w else ""))
    what = "\n".join(lines)

    # цену называем только там, где она однозначна
    subs = [subject(g) for g in row["groups"]]
    money = ""
    known = [s for s in subs if s in PRICE]
    if known and not any(s in NO_PRICE for s in subs):
        subj_name = known[0]
        old, new = PRICE[subj_name]
        # у ментальной арифметики абонемент на 4 занятия по 90 минут,
        # у остальных — 8 занятий по два раза в неделю
        pack = ("4 занятия" if subj_name == "Ментальная арифметика"
                else "8 занятий (два раза в неделю)")
        money = (f"\n\nАбонемент на {pack} сейчас "
                 f"{old:,} ₽ — это цена прошлого учебного года. "
                 f"С 1 сентября — {new:,} ₽.".replace(",", " "))
    if len(kids) > 1 and money:
        # скидка на второго ребёнка — 10% (решение владельца 18.08),
        # у семьи с двумя детьми это прямая причина оплатить сразу обоих
        money += ("\n\nНа второго ребёнка действует скидка 10% — "
                  "посчитаем при оплате.")
    elif any(s in NO_PRICE for s in subs):
        money = ("\n\nПо стоимости всё расскажем при оплате — у этого "
                 "направления абонемент считается индивидуально.")

    return (f"Здравствуйте! Место для {who} на новый учебный год мы держим:\n\n"
            f"{what}\n\n"
            f"Занятия начинаются 31 августа."
            f"{money}\n\n"
            f"Если оплатить до {DEADLINE} включительно, сентябрь пойдёт "
            f"по старой цене. Оплатить можно в центре или переводом — "
            f"напишите сюда, и мы пришлём реквизиты.\n\n"
            f"Если планы изменились, тоже просто напишите: снимем бронь "
            f"и отдадим место — очередь есть.")


def to_queue() -> dict:
    """Поставить письма в общую очередь отправки — тем же спокойным темпом."""
    rows = collect()
    paid_ever = ever_paid_ids()
    done = {str(x) for x in json.loads(db.get_setting("nabormail_done", "[]") or "[]")}
    queue = json.loads(db.get_setting("nabormail_queue", "[]") or "[]")
    have = {f"{r.get('kind') or 'nabor'}:{r['uid']}" for r in queue}
    fresh = []
    for r in rows:
        key = f"akciya:{r['uid']}"
        if key in done or key in have:
            continue
        fresh.append({"uid": r["uid"], "phone": r["phone"], "name": r["name"],
                      "seg": "?", "paid": True, "kind": "akciya",
                      "text": text_for(r), "sms": r["uid"] in paid_ever,
                      "sms_text": sms_text(r),
                      "msgr": wazzup.channels_for(r["phone"], uid=r["uid"])})
    # в самое начало: у письма есть срок, и он ближе всех остальных
    db.set_setting("nabormail_queue",
                   json.dumps(fresh + queue, ensure_ascii=False))
    log.info("акция: поставлено %d писем", len(fresh))
    return {"записаны без оплаты": len(rows), "поставлено в очередь": len(fresh)}


def sms_text(row: dict) -> str:
    """Короткая СМС вдогонку — два сегмента вместо пяти.

    Дублировать мессенджер целиком нельзя: длинный текст кириллицей это
    пять сегментов и 12 ₽ за штуку. Оставляем только то, без чего человек
    не дойдёт: что место держим, до какого числа старая цена и куда звонить."""
    return ("KidsUP: место для вашего ребёнка держим. "
            f"До {DEADLINE} сентябрь по цене прошлого года. "
            "Оплата в центре или переводом: 4951209024")


def ever_paid_ids() -> set:
    """Кто хоть когда-то у нас платил. СМС уходит только им — решение
    владельца от 25.08: тем, кто не платил, СМС не шлём никогда, это
    подпадает под закон о рекламе."""
    mk = MoyklassClient(sync.get_api_key())
    try:
        subs = taskguard.pull_all(mk, "/v1/company/userSubscriptions",
                                  "subscriptions", cache_hours=6)
    finally:
        mk.close()
    return {s["userId"] for s in subs
            if (s.get("stats") or {}).get("totalPayed", 0) > 0}


def main():
    import sys
    from collections import Counter
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "queue" in sys.argv:
        print(to_queue())
        return
    rows = collect()
    print(f"записаны и не оплатили: {len(rows)}")
    c = Counter(subject(r["groups"][0]) for r in rows)
    for k, n in c.most_common():
        print(f"   {k:26} {n}")
    for r in rows[:3]:
        print("\n" + "=" * 64)
        print(f"{r['name']} +7{r['phone']}")
        print("=" * 64)
        print(text_for(r))


if __name__ == "__main__":
    main()
