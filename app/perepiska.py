"""Ответы в переписке за администратора — на вопросы с однозначным ответом.

Что это. У Лизы 238 открытых задач, из них 149 — переписка, и добрая
половина это «пришлите расписание», «сколько стоит», «где вы находитесь».
Ответ на такой вопрос не требует человека: он целиком лежит в прайсе и в
списке групп. Пока задача ждёт своей очереди, родитель ждёт ответа —
23.08 пятеро написали вечером и не получили ничего до утра.

Чего этот модуль НЕ делает. Не торгуется, не обещает мест, не отвечает
про оплаты, возвраты, переносы и жалобы — всё это уходит человеку.
Не пишет, если последним в диалоге было наше сообщение: значит с семьёй
уже говорят. Не пишет ночью.

Откуда берутся цифры. Цены — только из PRICES (та же константа, по
которой печатается прайс и работает /enrollment). Расписание — только из
живых групп 2627 в CRM. Ни одна цифра не сочиняется: если для вопроса
нет точных данных, ответ не отправляется, а задача остаётся человеку.

Запуск:
    python -m app.perepiska            — показать, что будет отправлено
    python -m app.perepiska send       — отправить и записать в CRM
"""

from __future__ import annotations

import html as _html
import logging
import re
from datetime import date, datetime
from collections import defaultdict

import httpx

from . import db, sync, taskguard, wazzup
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.perepiska")

ADDRESS = ("б-р Маршала Рокоссовского, 6к1В (напротив ТЦ «Янтарь»), "
           "2 минуты от метро Бульвар Рокоссовского")
CHAT_ADMIN = 154181
# Статус записи «Пробное занятие» — в этом статусе ребёнок попадает в
# группу как гость, а не как оплативший ученик.
JOIN_TRIAL = 58132
DUTY = 232805
# Часы работы центра по Москве: с 9:00 до 20:00. Сообщение, пришедшее
# в девять вечера, читается как беспокойство, а ответить на него всё
# равно некому — администратор уже ушёл.
HOUR_FROM, HOUR_TO = 9, 20

# Что мы умеем отвечать сами. Порядок важен: проверяем сверху вниз,
# первое совпадение и определяет тему.
TOPICS = [
    ("оплата", re.compile(r"оплат|счёт|счет|перев[оё]д|реквизит|верн[иу]те|возврат|"
                          r"рассроч|долг|задолж", re.I)),
    ("перенос", re.compile(r"перенес|перенос|отмен|заморозк|пропуст", re.I)),
    ("жалоба", re.compile(r"жалоб|претенз|недоволен|недовольна|плохо\s+занима|"
                          r"верните\s+деньги", re.I)),
    ("цена", re.compile(r"стоимост|сколько\s+стоит|цен[аыу]|прайс|почём|почем", re.I)),
    ("расписание", re.compile(r"расписан|во\s?сколько|какое\s+время|когда\s+занят|"
                              r"график|дни\s+недел", re.I)),
    ("адрес", re.compile(r"адрес|где\s+вы|как\s+добраться|как\s+найти|метро", re.I)),
]
# Темы, которые всегда уходят человеку, даже если совпало что-то ещё.
HUMAN_ONLY = {"оплата", "перенос", "жалоба"}

# Прайс и разбор названий групп говорят о предметах разными словами:
# в PRICES это «Подготовка к школе», в разборе названий групп —
# «подготовка к школе». Из-за расхождения расписание молча возвращалось
# пустым, и ответ не отправлялся вовсе.
TO_GROUP = {
    "Подготовка к школе": "подготовка к школе",
    "Английский язык": "английский",
    "Английский детский сад": "мини-сад",
    "Раннее развитие": "раннее развитие",
    "ИЗО-студия": "ИЗО",
    "Шахматы": "шахматы",
    "Ментальная арифметика": "ментальная арифметика",
}

SUBJ_HINT = [
    ("Подготовка к школе", re.compile(r"подготовк|пш\b|школ", re.I)),
    ("Английский язык", re.compile(r"английск|англ\b|cambridge", re.I)),
    ("Английский детский сад", re.compile(r"мини-?сад|нулев|садик|гкп", re.I)),
    ("Раннее развитие", re.compile(r"раннее|лицей|музыка\s+и\s+речь|первая\s+школа", re.I)),
    ("ИЗО-студия", re.compile(r"изо|рисован|рисует|шедевр", re.I)),
    ("Шахматы", re.compile(r"шахмат", re.I)),
    ("Ментальная арифметика", re.compile(r"ментальн|арифметик", re.I)),
]

# «Записывайте на вторник в 17:00» — родитель назвал день и время.
DAYS = {"понедельник": "пн", "вторник": "вт", "среда": "ср", "среду": "ср",
        "четверг": "чт", "пятница": "пт", "пятницу": "пт", "суббота": "сб",
        "субботу": "сб", "воскресенье": "вс"}
TIME_RE = re.compile(r"\b(\d{1,2})[:.\s]?(\d{2})?\s*(?:час|ч\b)?")
BOOK_RE = re.compile(r"запиш|записыва|записать|давайте\s+на|берём|берем|"
                     r"придём|придем|подходит", re.I)


def _dialogs(day: str | None = None) -> list[dict]:
    day = day or date.today().isoformat()
    r = httpx.get(f"https://app.kidsup.ru/api/dialogs?day={day}",
                  auth=("admin", db.get_setting("admin_pass", "CGWstart8*")),
                  timeout=120)
    d = r.json()
    return d if isinstance(d, list) else (d.get("dialogs") or [])


def _yesterday_subjects() -> dict[str, str]:
    """Предмет из вчерашней переписки по номеру.

    Разговор живёт дольше суток: 23.08 Артамонов написал «планируем на
    ментальную арифметику», 24.08 — «какая цена будет?». Без вчерашнего
    контекста ответ переспрашивал бы то, что клиент уже сказал, и выглядел
    бы как разговор с автоматом, который ничего не помнит."""
    from datetime import timedelta
    out: dict[str, str] = {}
    try:
        prev = (date.today() - timedelta(days=1)).isoformat()
        for d in _dialogs(prev):
            phone = "".join(c for c in str(d.get("phone") or "") if c.isdigit())[-10:]
            if len(phone) != 10:
                continue
            text = " ".join(m["text"] for m in (d.get("messages") or [])
                            if m.get("dir") == "in")
            subj = next((n for n, rx in SUBJ_HINT if rx.search(text)), None)
            if subj:
                out[phone] = subj
    except Exception as e:
        log.warning("вчерашние диалоги недоступны: %s", e)
    return out


def _price_block(subject: str) -> str:
    """Строки прайса по предмету. Две цены: до 30.08 и после."""
    from .main import PRICES
    block = PRICES.get(subject)
    if not block:
        return ""
    early = date.today() <= date(2026, 8, 30)
    out = [f"{subject}:"]
    for name, p_early, p_late in block["lines"]:
        out.append(f"- {name} — {(p_early if early else p_late):,} ₽".replace(",", " "))
    if early:
        out.append("До 30 августа действуют цены прошлого года.")
    return "\n".join(out)


def _schedule(mk, subject: str) -> str:
    """Живые группы нового сезона по предмету — без выдумок."""
    from . import prozvon
    rc = mk.get("/v1/company/classes", {"limit": 500})
    classes = rc.get("classes") if isinstance(rc, dict) else rc
    rows = []
    for c in classes:
        nm = c.get("name") or ""
        if not nm.startswith("2627") or re.search(r"аявк", nm, re.I):
            continue
        if prozvon._subject(nm) != TO_GROUP.get(subject, subject):
            continue
        rows.append(re.sub(r"^2627_[^_]*_", "", nm).replace("_", " · "))
    return "\n".join(f"- {r}" for r in sorted(set(rows))[:9])


def classify(text: str) -> tuple[str, str | None]:
    """Тема вопроса и предмет, если он назван."""
    topic = next((n for n, rx in TOPICS if rx.search(text)), "")
    subj = next((n for n, rx in SUBJ_HINT if rx.search(text)), None)
    return topic, subj


def compose(mk, topic: str, subject: str | None, name: str = "") -> str:
    """Текст ответа. Пусто — значит данных не хватает и пишет человек."""
    if topic == "адрес":
        return (f"Здравствуйте! Мы находимся по адресу: {ADDRESS}. "
                f"Работаем ежедневно с 8:00 до 20:00 — заходите, всё покажем.")
    if not subject:
        # Предмет не назван — прайс целиком высылать нельзя: у нас
        # одиннадцать направлений, и стена цифр на вопрос «сколько стоит»
        # отпугивает сильнее молчания. Но и ждать человека полдня незачем:
        # отвечаем сразу и одним вопросом сужаем разговор до нужного.
        if topic in ("цена", "расписание"):
            return ("Здравствуйте! Скажите, пожалуйста, сколько лет ребёнку "
                    "и что интересует — подготовка к школе, английский, "
                    "мини-сад, раннее развитие, ИЗО, шахматы или ментальная "
                    "арифметика? Пришлю точное расписание и стоимость именно "
                    "по этому направлению.\n\n"
                    "Занятия начинаются 31 августа. Первое занятие "
                    "условно-бесплатное, и на нём же бесплатная диагностика: "
                    "педагог посмотрит уровень и скажет, с чего начинать.")
        return ""
    if topic == "цена":
        block = _price_block(subject)
        if not block:
            return ""
        return (f"Здравствуйте! Вот стоимость:\n\n{block}\n\n"
                f"Первое занятие условно-бесплатное: не понравится — платить "
                f"не нужно, понравится — оно входит в первый абонемент. "
                f"И на первом же занятии — бесплатная диагностика: педагог "
                f"посмотрит уровень ребёнка и скажет, что уже хорошо и над "
                f"чем поработать. Написать, какие есть время и дни?")
    if topic == "расписание":
        sched = _schedule(mk, subject)
        if not sched:
            return ""
        return (f"Здравствуйте! {subject} на новый учебный год — занятия "
                f"с 31 августа:\n\n{sched}\n\n"
                f"Первое занятие условно-бесплатное, и на нём же бесплатная "
                f"диагностика уровня. Скажите возраст ребёнка — подскажу, "
                f"какая группа подойдёт, и запишу на удобное время.")
    return ""


def parse_booking(text: str) -> tuple[str | None, str | None]:
    """День и время из фразы «запишите на вторник в 17:00»."""
    if not BOOK_RE.search(text):
        return None, None
    day = next((v for k, v in DAYS.items() if k in text.lower()), None)
    t = None
    for m in TIME_RE.finditer(text):
        h = int(m.group(1))
        if 8 <= h <= 21:
            t = f"{h}:{m.group(2) or '00'}"
            break
    return day, t


def find_class(mk, subject: str, day: str | None, time: str | None):
    """Группа 2627, попадающая по предмету, дню и времени. Одна и только
    одна — если подходит несколько, выбирать должен человек."""
    from . import prozvon
    rc = mk.get("/v1/company/classes", {"limit": 500})
    classes = rc.get("classes") if isinstance(rc, dict) else rc
    hits = []
    for c in classes:
        nm = c.get("name") or ""
        if not nm.startswith("2627") or re.search(r"аявк", nm, re.I):
            continue
        if subject and prozvon._subject(nm) != TO_GROUP.get(subject, subject):
            continue
        if day and day not in nm.lower().replace(" ", ""):
            continue
        if time and time.replace(":00", "") not in nm.replace(":00", ""):
            continue
        hits.append(c)
    return hits[0] if len(hits) == 1 else None


def scan(day: str | None = None) -> list[dict]:
    """Диалоги, где клиент задал вопрос и ответа от нас ещё не было."""
    out = []
    yesterday = _yesterday_subjects()
    for d in _dialogs(day):
        msgs = d.get("messages") or []
        ins = [m for m in msgs if m["dir"] == "in"]
        if not ins:
            continue
        last = ins[-1]
        if any(m["dir"] == "out" and m["ts"] > last["ts"] for m in msgs):
            continue                      # уже ответили
        phone = "".join(c for c in str(d.get("phone") or "") if c.isdigit())[-10:]
        if len(phone) != 10:
            continue                      # служебные номера Wazzup
        text = " ".join(m["text"] for m in ins[-3:])
        topic, subj = classify(text)
        if not subj:
            subj = yesterday.get(phone)
        # Ответ на наш уточняющий вопрос: клиент пишет «ментальная
        # арифметика» или «5 лет» без слов «цена/расписание» — тема не
        # распознаётся, и запрос повисает (24.08 Юрченко ждала весь день).
        # Если последнее наше сообщение было уточнением, ответ и есть
        # предмет, а тема — та, ради которой уточняли.
        outs = [m for m in msgs if m["dir"] == "out" and m["ts"] < last["ts"]]
        if not topic and outs and "что интересует" in outs[-1]["text"]:
            topic = "цена"
            if not subj:
                subj = next((n for n, rx in SUBJ_HINT if rx.search(last["text"])), None)
        out.append({"phone": phone, "name": d.get("name") or "", "text": text,
                    "last_ts": last["ts"], "topic": topic, "subject": subj})
    return out


def run(day: str | None = None, dry: bool = True) -> dict:
    """Отвечает на то, что умеет; остальное оставляет человеку."""
    items = scan(day)
    now = datetime.now()
    msk_hour = (now.hour + 3) % 24
    night = not (HOUR_FROM <= msk_hour < HOUR_TO)
    mk = MoyklassClient(sync.get_api_key())
    report = {"всего": len(items), "ответили": 0, "человеку": 0,
              "ночь": night, "строки": []}
    try:
        users = taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=2)
        idx = {}
        for u in users:
            p = "".join(c for c in str(u.get("phone") or "") if c.isdigit())[-10:]
            if len(p) == 10:
                idx.setdefault(p, u)
        for it in items:
            u = idx.get(it["phone"])
            it["uid"] = u["id"] if u else None
            text = ""
            if it["topic"] and it["topic"] not in HUMAN_ONLY:
                text = compose(mk, it["topic"], it["subject"], it["name"])
            # «Запишите на вторник в 17:00» — если предмет, день и время
            # сходятся ровно на одной группе, оформляем запись сами.
            # Если подходит несколько или чего-то не хватает, решает
            # человек: записать не в ту группу дороже, чем подождать.
            it["booked"] = ""
            day_, time_ = parse_booking(it["text"])
            if it["uid"] and it["subject"] and (day_ or time_):
                cl = find_class(mk, it["subject"], day_, time_)
                if cl:
                    it["booked"] = cl["name"]
                    if not dry:
                        try:
                            mk.post("/v1/company/joins",
                                    {"userId": it["uid"], "classId": cl["id"],
                                     "statusId": JOIN_TRIAL})
                            text = (f"Записали! {cl['name']} — ждём вас. "
                                    f"Адрес: {ADDRESS}. Первое занятие "
                                    f"условно-бесплатное: не понравится — "
                                    f"платить не нужно.")
                        except Exception as e:
                            log.warning("запись не создана: %s", e)
                            it["booked"] = ""
                    else:
                        text = f"[записал бы в {cl['name']}]"
            it["answer"] = text
            if not text:
                report["человеку"] += 1
                report["строки"].append({**it, "action": "человеку"})
                continue
            if night or dry:
                report["строки"].append({**it, "action": "готово, ждёт отправки"})
                report["ответили"] += 1
                continue
            wazzup.send_smart(it["phone"], text, uid=it["uid"], dry_run=False)
            if it["uid"]:
                mk.post("/v1/company/userComments", {
                    "userId": it["uid"], "showToUser": False,
                    "comment": (f"Ответ в переписке ({it['topic']}): "
                                f"клиент спросил «{it['text'][:90]}». "
                                f"Отправлено: {text[:400]}")[:1000]})
            report["ответили"] += 1
            report["строки"].append({**it, "action": "отправлено"})
    finally:
        mk.close()
    return report


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    r = run(dry="send" not in sys.argv)
    print(f"диалогов без ответа: {r['всего']} | отвечаю сам: {r['ответили']} | "
          f"человеку: {r['человеку']}" + ("  (ночь — отправка отложена)" if r["ночь"] else ""))
    for s in r["строки"]:
        print(f"\n— {s['name'][:24]} +7{s['phone']}  [{s['topic'] or 'без темы'}"
              f"{'/' + s['subject'] if s.get('subject') else ''}]  {s['action']}")
        print(f"   спросили: {s['text'][:120]}")
        if s.get("answer"):
            print(f"   ответ   : {s['answer'][:200]}")


if __name__ == "__main__":
    main()
