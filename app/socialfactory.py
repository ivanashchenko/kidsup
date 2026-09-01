"""Фабрика контента для соцсетей: готовые тексты, а не темы.

Чем отличается от app/content.py. Тот строит план РУБРИК на неделю —
«вторник, ВКонтакте, Польза». Дальше человек садится и пишет пост сам,
и на этом контент обычно заканчивается: план есть, публикаций нет.
Здесь выдаются готовые тексты, которые остаётся скопировать и выложить.

Откуда берутся факты. Свободные места, названия групп и расписание —
из CRM, а не из головы: пост «осталось 3 места в группе вт-чт 17:00»
должен быть правдой в момент публикации. Если данных нет, шаблон
не используется вовсе — лучше промолчать, чем выдумать цифру.

Что учтено из маркетинг-плана набора (цель воронки: 130 заявок → 80
ответов → 60 приходов → 55 покупок, соцсети дают 10 заявок из 130):
  · оффер месяца с дедлайном — он же двигатель поста-продажи;
  · анонсы в соцсетях по вторникам, отдельно посты и сторис;
  · три запускающих события — праздник 29.08, День открытых дверей 30.08
    и Неделя открытых уроков 31.08–06.09;
  · реферальная программа и партнёрский кэшбек как поводы для постов;
  · школьные закладки, расписание и бланки с ребусами — физические
    носители, которые ребёнок уносит домой, а родитель видит весь год.

Соотношение рубрик держится сознательно: продажа занимает не больше
одного поста из пяти. Лента, где каждый пост продаёт, перестаёт
собирать охваты, и тогда не работает ни один из пяти.

Запуск:
    python -m app.socialfactory plan      — календарь публикаций
    python -m app.socialfactory texts     — готовые тексты
    python -m app.socialfactory page      — собрать страницу
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from datetime import date, timedelta

from . import sync
from . import taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.socialfactory")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"

SEASON_START = date(2026, 8, 31)
HOLIDAY = date(2026, 8, 29)          # праздник открытия сезона
OPEN_DOORS = date(2026, 8, 30)       # День открытых дверей
OPEN_WEEK = (date(2026, 8, 31), date(2026, 9, 6))
PRICE_DEADLINE = date(2026, 8, 31)   # включительно — сентябрь по ценам года

CENTER = ("детский центр и английский сад KidsUP, б-р Маршала Рокоссовского 6к1В "
          "(напротив ТЦ «Янтарь», 5 минут от м. Бульвар Рокоссовского)")

ACTIVE = {2, 50509, 58131, 58132, 83760}


def _subject(name: str) -> str | None:
    n = name or ""
    if re.search(r"Заявк|Roistat|ДОД|^МК_", n, re.I):
        return None
    if re.search(r"_ПШ|^ПШ|одготовк", n, re.I):
        return "подготовка к школе"
    if re.search(r"нулев", n, re.I):
        return "нулевой класс"
    if re.search(r"_АЯ|^АЯ", n, re.I):
        return "английский"
    # «Музыка и речь», «Первая школа», «Лицей для малышей» — это программы
    # внутри раннего развития, а не отдельные предметы: в прайсе они идут
    # одним разделом, и на листе обзвона делить их незачем — конкретная
    # программа видна в названии группы.
    if re.search(r"МсМ|узыка и речь|_РР|^РР|аннее развит|ицей|ервая школа",
                 n, re.I):
        return "раннее развитие"
    if re.search(r"ини-сад", n, re.I):
        return "мини-сад"
    if "ШАХ" in n:
        return "шахматы"
    if "ИЗО" in n:
        return "ИЗО"
    if re.search(r"_МА|ентальн", n, re.I):
        return "ментальная арифметика"
    if re.search(r"_ЛГ|огопед", n, re.I):
        return "логопед"
    return None


def _time_of(name: str) -> str:
    """Расписание из названия группы: «2627_ПШ_вт-чт_17:00_…» → «вт-чт 17:00»."""
    m = re.search(r"_((?:пн|вт|ср|чт|пт|сб|вс)[^_]*?)_(\d{1,2}:\d{2})", name or "")
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m = re.search(r"(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})", name or "")
    return m.group(1) if m else ""


def facts(force: bool = False) -> dict:
    """Живая картина набора: где есть места, где почти закрыто."""
    cache = f"{SP}/social_facts.json"
    if not force and os.path.exists(cache):
        try:
            d = json.load(open(cache))
            if d.get("день") == date.today().isoformat():
                return d
        except Exception:
            pass
    mk = MoyklassClient(sync.get_api_key())
    try:
        rc = mk.get("/v1/company/classes", {"limit": 500})
        classes = rc.get("classes") if isinstance(rc, dict) else rc
        joins = taskguard.pull_all(mk, "/v1/company/joins", "joins")
    finally:
        mk.close()

    taken = defaultdict(int)
    for j in joins:
        if j.get("statusId") in ACTIVE:
            taken[j.get("classId")] += 1

    groups = []
    for c in classes:
        nm = c.get("name") or ""
        if not nm.startswith("2627"):
            continue
        s = _subject(nm)
        if not s:
            continue
        cap = c.get("maxStudents") or 0
        if not cap:
            continue
        busy = taken.get(c["id"], 0)
        groups.append({"id": c["id"], "subject": s, "name": nm,
                       "when": _time_of(nm), "cap": cap, "busy": busy,
                       "free": max(0, cap - busy)})

    by_subj = defaultdict(lambda: {"free": 0, "cap": 0, "groups": []})
    for g in groups:
        b = by_subj[g["subject"]]
        b["free"] += g["free"]
        b["cap"] += g["cap"]
        b["groups"].append(g)
    out = {"день": date.today().isoformat(),
           "группы": groups,
           "предметы": {k: v for k, v in by_subj.items()},
           # Почти закрытые группы — единственное честное основание для слов
           # «осталось N мест». Индивидуальные занятия (вместимость 1-3)
           # сюда не попадают: у логопеда «одно место» — это не кончающийся
           # набор, а формат работы, и пост про это ввёл бы в заблуждение.
           "горящие": sorted([g for g in groups
                              if g["cap"] >= 4 and 0 < g["free"] <= 3],
                             key=lambda g: g["free"])[:8]}
    try:
        json.dump(out, open(cache, "w"), ensure_ascii=False)
    except Exception:
        pass
    return out


# --- тексты -----------------------------------------------------------------
# Каждый шаблон — функция от фактов. Возвращает None, если данных не хватает:
# пост, придуманный без опоры, стоит дороже пропущенного дня.

def _plural_mest(n: int) -> str:
    if 11 <= n % 100 <= 14:
        return "мест"
    return {1: "место", 2: "места", 3: "места", 4: "места"}.get(n % 10, "мест")


def post_guarantee(f: dict) -> dict | None:
    g = f["предметы"].get("подготовка к школе")
    if not g:
        return None
    return {
        "рубрика": "Продукт",
        "заголовок": "Научим читать за 3 месяца — или занимаемся бесплатно",
        "текст": (
            "Мы не обещаем «ребёнку понравится». Мы обещаем результат и пишем "
            "условия прямо.\n\n"
            "Ребёнок приходит не читающим — через 3 месяца читает трёхбуквенные "
            "слова. Не зачитал — продолжает заниматься бесплатно, пока не "
            "зачитает.\n\n"
            "Условия честные, и мы называем их сразу:\n"
            "· диагностика на первом занятии — с неё считается старт;\n"
            "· посещаемость от 80%;\n"
            "· выполненные домашние задания.\n\n"
            "Работаем по технологии Бураковой: пошаговая методика, где заранее "
            "известно, в каком порядке даются звуки и слоги и где ребёнок обычно "
            "спотыкается.\n\n"
            "Подготовка к школе — вт-чт, пн-чт, вт-пт и ср-пт, вечерние часы. "
            "Есть варианты с субботой.\n\n"
            "Первое занятие условно-бесплатное: не понравится — платить не нужно, "
            "понравится — оно входит в первый абонемент.\n\n"
            "Напишите в сообщения — подберём время и уровень: ПШ1 для нечитающих, "
            "ПШ2 для читающих."),
    }


def post_diagnostics(f: dict) -> dict:
    return {
        "рубрика": "Польза",
        "заголовок": "Что вы узнаете о ребёнке на первом занятии",
        "текст": (
            "Обычно родитель приводит ребёнка «посмотреть». Сидит в коридоре, "
            "потом спрашивает: «Ну как?» — и слышит «всё хорошо».\n\n"
            "У нас первое занятие устроено иначе. Педагог проводит диагностику "
            "и после занятия говорит конкретно:\n\n"
            "· что у ребёнка уже получается — и это чаще всего приятно удивляет;\n"
            "· что стоит подтянуть до школы;\n"
            "· как именно занятия с этим помогут и сколько времени займёт.\n\n"
            "Вы уходите с пониманием, а не с ощущением «сходили посмотрели». "
            "И даже если решите не заниматься у нас — эта информация останется "
            "у вас.\n\n"
            "Первое занятие условно-бесплатное: не понравится — платить не нужно.\n\n"
            "Записаться можно в сообщениях: напишите возраст ребёнка и что "
            "хотите подтянуть."),
    }


def post_events(f: dict, on: date | None = None) -> dict | None:
    on = on or date.today()
    if on > OPEN_WEEK[1]:
        return None          # события прошли, звать на них уже нельзя
    return {
        "рубрика": "Событие",
        "заголовок": "Три повода прийти к нам до старта учебного года",
        "текст": (
            f"29 августа, суббота — праздник открытия сезона. Приходите семьёй: "
            f"познакомимся, поиграем, покажем центр изнутри.\n\n"
            f"30 августа, воскресенье — День открытых дверей. Здесь можно "
            f"поговорить с педагогами лично и задать им любые вопросы: кто ведёт, "
            f"как устроены занятия, что будет с вашим ребёнком за год.\n\n"
            f"31 августа — 6 сентября — Неделя открытых уроков. Ребёнок занимается "
            f"в настоящей группе, а педагог после урока рассказывает вам, что "
            f"у него получается и что стоит подтянуть.\n\n"
            f"Занятия начинаются 31 августа.\n\n"
            f"Выберите, что вам ближе: прийти всей семьёй на праздник, "
            f"поговорить с педагогом или сразу привести ребёнка на урок. "
            f"Напишите нам — подскажем время под возраст."),
    }


def post_scarce(f: dict) -> dict | None:
    """«Осталось N мест» — только если это правда по CRM."""
    hot = f.get("горящие") or []
    if not hot:
        return None
    lines = []
    for g in hot[:5]:
        when = g["when"] or "уточните время"
        lines.append(f"· {g['subject']}, {when} — {g['free']} "
                     f"{_plural_mest(g['free'])}")
    return {
        "рубрика": "Продажа",
        "заголовок": "Где осталось меньше всего мест",
        "текст": ("Группы набираются, и по нескольким направлениям места "
                  "заканчиваются:\n\n" + "\n".join(lines) +
                  "\n\nЭто не приём «успейте купить» — просто в группе "
                  "ограниченное число детей, и когда оно набрано, мы закрываем "
                  "набор.\n\nЕсли ваше время в списке — напишите сегодня, "
                  "подберём вариант или поставим в лист ожидания на случай, "
                  "если место освободится."),
    }


def post_price_deadline(f: dict, on: date | None = None) -> dict | None:
    on = on or date.today()
    if on > PRICE_DEADLINE:
        return None
    left = (PRICE_DEADLINE - on).days
    when = "сегодня последний день" if left == 0 else f"осталось {left} дн."
    return {
        "рубрика": "Продажа",
        "заголовок": f"До 31 августа сентябрь по ценам прошлого года ({when})",
        "текст": (
            "Мы держим прошлогоднюю цену на сентябрь для тех, кто определится "
            "до 31 августа включительно.\n\n"
            "Скидки, которые есть у нас:\n"
            "· −10% на первый абонемент, если оформить в день пробного занятия "
            "(для тех, кто у нас впервые);\n"
            "· −10% на второй предмет;\n"
            "· −10% на второго ребёнка;\n"
            "· −10% многодетным семьям и семьям участников СВО.\n\n"
            "Скидки не суммируются — действует одна, самая выгодная для вас. "
            "Мы говорим об этом сразу, чтобы не было разговора «а нам обещали».\n\n"
            "Напишите, какое направление интересует, — посчитаем и назовём "
            "точную сумму."),
    }


def post_subject(f: dict, subject: str, hook: str, body: str) -> dict | None:
    s = f["предметы"].get(subject)
    if not s:
        return None
    times = [g["when"] for g in s["groups"] if g["when"] and g["free"] > 0][:4]
    when = ("Расписание: " + "; ".join(times) + ".\n\n") if times else ""
    return {"рубрика": "Продукт", "заголовок": hook,
            "текст": body + "\n\n" + when +
            "Первое занятие условно-бесплатное: не понравится — платить не нужно. "
            "Напишите нам возраст ребёнка, и мы подскажем группу и уровень."}


def post_referral(f: dict) -> dict:
    return {
        "рубрика": "Механика",
        "заголовок": "Приведите друга — и оба получите бонус",
        "текст": (
            "Самые тёплые новые семьи приходят по рекомендации. Поэтому "
            "запускаем простую вещь к началу учебного года.\n\n"
            "Расскажите о нас знакомым, у кого ребёнок подходящего возраста. "
            "Друг приходит на первое занятие и остаётся — бонус получаете вы оба.\n\n"
            "Никаких условий мелким шрифтом: бонус начисляется после того, "
            "как друг оформит первый абонемент.\n\n"
            "Напишите нам в сообщения имя и телефон того, кому это может быть "
            "интересно, — мы позвоним сами, аккуратно и без навязчивости."),
        "черновик": True,   # механику и размер бонуса утверждает владелец
    }


def post_partners(f: dict) -> dict:
    return {
        "рубрика": "Механика",
        "заголовок": "Семейный кэшбек: наши занятия и подарки от соседей",
        "текст": (
            "Собираем программу с местами, куда семьи из нашего района ходят "
            "и так: кофейни, детские магазины, студии, спорт.\n\n"
            "Как это работает: вы занимаетесь у нас — получаете подарки "
            "и скидки у партнёров. Партнёры получают наших родителей.\n\n"
            "Если у вас свой бизнес рядом с Бульваром Рокоссовского и вам "
            "интересно — напишите в сообщения, обсудим условия."),
        "черновик": True,   # список партнёров пока не согласован
    }


def stories(f: dict) -> list[dict]:
    """Сторис живут сутки, поэтому текст в них короткий и с одним действием."""
    out = [
        {"кадр": "Приглашение", "текст":
         "29.08 — праздник открытия сезона\n30.08 — День открытых дверей\n"
         "31.08–06.09 — Неделя открытых уроков\n\nСвайп вверх или сообщение — "
         "запишем на удобное время"},
        {"кадр": "Гарантия", "текст":
         "Научим читать за 3 месяца\n\nИли занимается бесплатно, пока не "
         "зачитает\n\nУсловия: диагностика, посещаемость 80%, домашние задания"},
        {"кадр": "Диагностика", "текст":
         "На первом занятии педагог скажет:\n• что у ребёнка уже хорошо\n"
         "• что стоит подтянуть\n• как занятия помогут\n\nПервое занятие "
         "условно-бесплатное"},
        {"кадр": "Вопрос", "текст":
         "Ребёнок идёт в школу через год?\n\nОтветьте на сообщение — пришлём "
         "чек-лист готовности к школе"},
    ]
    hot = f.get("горящие") or []
    if hot:
        g = hot[0]
        out.append({"кадр": "Места", "текст":
                    f"{g['subject'].capitalize()}\n{g['when']}\n\n"
                    f"осталось {g['free']} {_plural_mest(g['free'])}\n\n"
                    f"Напишите — забронируем"})
    return out


SUBJECT_POSTS = [
    ("английский", "Почему ребёнок учит английский три года и не говорит",
     "Чаще всего причина одна: язык учат как предмет, а не как способ "
     "общаться. Ребёнок знает слова, но не пробовал ими пользоваться.\n\n"
     "Мы работаем по уровням Cambridge — Starters, Movers, Flyers. Это значит, "
     "что у ребёнка есть понятная лестница и внешняя точка отсчёта, а не "
     "«занимаемся и посмотрим».\n\n"
     "На занятии дети говорят с первого дня: не переводят, а отвечают, "
     "спрашивают и играют на английском."),
    ("музыка и речь", "Ребёнку два, а он почти не говорит — ждать или идти?",
     "Ждать до трёх «а вдруг само» — самая частая и самая дорогая ошибка. "
     "Речь запускается через ритм, движение и подражание, и в два-три года "
     "это работает быстрее всего.\n\n"
     "На «Музыке и речи» дети поют, отбивают ритм, играют на простых "
     "инструментах и повторяют за педагогом. Малыши до 2,2 занимаются "
     "вместе с мамой — так спокойнее и ребёнку, и родителю."),
    ("подготовка к школе", "Чек-лист: готов ли ребёнок к первому классу",
     "Проверьте по пунктам:\n\n"
     "· удерживает внимание 15–20 минут на одном задании;\n"
     "· знает буквы и складывает их в слоги;\n"
     "· считает до десяти и обратно;\n"
     "· держит карандаш правильно, не устаёт от письма;\n"
     "· может пересказать короткую историю;\n"
     "· умеет ждать своей очереди и работать в группе.\n\n"
     "Если минусов больше двух — год до школы стоит потратить на подготовку. "
     "Если минусов нет — есть ПШ2 для читающих, там другая программа."),
    ("ментальная арифметика", "Считает в уме быстрее, чем на калькуляторе",
     "Ментальная арифметика — не про то, чтобы удивлять гостей скоростью "
     "счёта. Она тренирует концентрацию и рабочую память, и это заметно "
     "по всем предметам, а не только по математике.\n\n"
     "Ребёнок учится держать в голове несколько шагов сразу — навык, "
     "который в школе нужен каждый день."),
    ("логопед", "Пять признаков, что пора к логопеду",
     "· после трёх лет речь понятна только близким;\n"
     "· пропускает или переставляет слоги в словах;\n"
     "· не выговаривает больше двух-трёх звуков;\n"
     "· говорит короткими фразами, не строит предложения;\n"
     "· заикается или «застревает» на первом звуке.\n\n"
     "Один-два пункта — повод показаться специалисту, а не ждать. "
     "Занятия индивидуальные, программа под конкретного ребёнка."),
]


def all_posts(f: dict | None = None) -> list[dict]:
    f = f or facts()
    out = []
    for fn in (post_guarantee, post_diagnostics, post_events, post_scarce,
               post_price_deadline, post_referral, post_partners):
        p = fn(f)
        if p:
            out.append(p)
    for subj, hook, body in SUBJECT_POSTS:
        p = post_subject(f, subj, hook, body)
        if p:
            out.append(p)
    return out


def post_today_event(f: dict, on: date) -> dict | None:
    """В день события лента говорит про событие, а не про методику."""
    if on == HOLIDAY:
        return {"рубрика": "Событие", "заголовок": "Сегодня праздник открытия сезона",
                "текст": ("Сегодня ждём вас на празднике открытия сезона! "
                          "Приходите семьёй — познакомимся, поиграем, покажем "
                          "центр изнутри и ответим на любые вопросы.\n\n"
                          "Завтра, 30 августа, — День открытых дверей: там можно "
                          "поговорить с педагогами лично.\n\n"
                          "А с 31 августа начинается учебный год и Неделя "
                          "открытых уроков.\n\nМы на бульваре Маршала "
                          "Рокоссовского, 6к1В — напротив ТЦ «Янтарь», пять "
                          "минут от метро. До встречи!")}
    if on == OPEN_DOORS:
        return {"рубрика": "Событие", "заголовок": "Сегодня День открытых дверей",
                "текст": ("Сегодня можно прийти и поговорить с педагогами лично: "
                          "кто ведёт вашу группу, как устроены занятия, что будет "
                          "с ребёнком за год.\n\nЗавтра начинается учебный год, "
                          "а с ним Неделя открытых уроков: ребёнок занимается "
                          "в настоящей группе, а педагог после урока рассказывает "
                          "вам, что у него уже получается и что стоит подтянуть.\n\n"
                          "Сегодня же последний день, когда сентябрь идёт "
                          "по ценам прошлого года.\n\nЖдём вас на бульваре "
                          "Маршала Рокоссовского, 6к1В.")}
    if on == SEASON_START:
        return {"рубрика": "Событие", "заголовок": "Учебный год начался",
                "текст": ("Сегодня первый день занятий — и первый день Недели "
                          "открытых уроков, она идёт до 6 сентября.\n\n"
                          "Если вы ещё выбираете, это лучшая неделя, чтобы "
                          "прийти: ребёнок занимается в настоящей группе с теми "
                          "детьми, с которыми будет заниматься весь год, а "
                          "педагог после урока говорит вам, что у него "
                          "получается и над чем стоит поработать.\n\n"
                          "Первое занятие условно-бесплатное. Напишите возраст "
                          "ребёнка — подскажем, в какой день прийти.")}
    return None


def plan(days: int = 40) -> list[dict]:
    """Календарь: что и когда публиковать.

    Актуальность проверяется на ДАТУ ПУБЛИКАЦИИ, а не на сегодня: иначе
    в план на 18 сентября попадает пост «до 30 августа цены прошлого года»,
    а на 10-е — приглашение на события, которые прошли."""
    f = facts()
    st = stories(f)
    out = []
    today = date.today()
    evergreen = [p for p in
                 [post_guarantee(f), post_diagnostics(f), post_referral(f),
                  post_partners(f)] if p]
    for subj, hook, body in SUBJECT_POSTS:
        p = post_subject(f, subj, hook, body)
        if p:
            evergreen.append(p)
    ei = 0
    for i in range(days):
        d = today + timedelta(days=i)
        if d > OPEN_DOORS and i % 2:
            continue                      # после событий — через день
        # 1) день события важнее любой рубрики
        p = post_today_event(f, d)
        # 2) каждый пятый — продающий, если он в этот день ещё правдив
        if p is None and len(out) % 5 == 4:
            p = post_price_deadline(f, d) or post_scarce(f) or post_events(f, d)
        # 3) обычный день — вечнозелёная рубрика
        if p is None:
            p = evergreen[ei % len(evergreen)]
            ei += 1
        out.append({
            "дата": d.isoformat(),
            "день": ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][d.weekday()],
            "канал": "ВКонтакте + Telegram",
            "рубрика": p["рубрика"],
            "заголовок": p["заголовок"],
            "текст": p["текст"],
            "черновик": p.get("черновик", False),
            "сторис": st[len(out) % len(st)],
            "повод": ("праздник открытия сезона" if d == HOLIDAY else
                      "День открытых дверей" if d == OPEN_DOORS else
                      "старт учебного года" if d == SEASON_START else
                      "Неделя открытых уроков"
                      if OPEN_WEEK[0] <= d <= OPEN_WEEK[1] else ""),
        })
    return out


OUT_PAGE = "docs/kontent_plan.html"


def build_page() -> str:
    import html as H
    rows = plan()
    f = facts()
    cards = []
    for r in rows:
        draft = ('<span class="tag draft">черновик — утвердить механику</span>'
                 if r["черновик"] else "")
        why = (f'<span class="tag ev">{H.escape(r["повод"])}</span>'
               if r["повод"] else "")
        body = H.escape(r["текст"]).replace("\n\n", "</p><p>").replace("\n", "<br>")
        st = r["сторис"]
        cards.append(f"""<article class="card" id="d{r['дата']}">
  <header>
    <div class="when">{r['дата'][8:]}.{r['дата'][5:7]} · {r['день']}</div>
    <div class="tags"><span class="tag r{r['рубрика'][:4]}">{H.escape(r['рубрика'])}</span>{why}{draft}</div>
  </header>
  <h3>{H.escape(r['заголовок'])}</h3>
  <div class="post"><p>{body}</p></div>
  <button class="copy" data-t="{H.escape(r['текст'])}">Скопировать текст поста</button>
  <div class="story"><em>Сторис · {H.escape(st['кадр'])}</em>{H.escape(st['текст']).replace(chr(10),'<br>')}</div>
</article>""")

    free = sorted(f["предметы"].items(), key=lambda x: -x[1]["free"])[:6]
    tiles = "".join(
        f'<div class="num"><b>{v["free"]}</b><span>свободно<br>{H.escape(k)}</span></div>'
        for k, v in free)

    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Контент-план соцсетей</title>
<style>
:root{{--paper:#FCFBF7;--ink:#1B1D2B;--muted:#5F6478;--line:#E7E4DA;--card:#FFF;
 --indigo:#312783;--sky:#1DA7E0;--green:#5C8C1E;--amber:#B26F00;--red:#B4131C;
 --sky-soft:#E4F2FB;--green-soft:#EDF4E1;--amber-soft:#FBF0DC;--indigo-soft:#EAE8F5;--fill:#F3F1E9}}
@media (prefers-color-scheme:dark){{:root{{--paper:#14151D;--ink:#E7E6EC;--muted:#9A9EAE;
 --line:#292B37;--card:#1B1D26;--indigo:#A79EEE;--sky:#5EC0EC;--green:#9FD055;--amber:#E5A63F;
 --red:#EC7C7C;--sky-soft:#13252F;--green-soft:#1C2416;--amber-soft:#2C2313;
 --indigo-soft:#201E38;--fill:#20222C}}}}
*{{box-sizing:border-box}}
body{{background:var(--paper);color:var(--ink);margin:0;
 font:16px/1.62 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
.wrap{{max-width:50rem;margin:0 auto;padding:1.7rem 1rem 4rem}}
h1{{font-size:1.85rem;font-weight:800;letter-spacing:-.02em;margin:.3rem 0 .4rem}}
h3{{font-size:1.12rem;font-weight:760;margin:.5rem 0 .4rem;text-wrap:balance}}
p{{margin:.5rem 0}} .sub{{color:var(--muted)}}
.kicker{{font-size:.72rem;font-weight:750;letter-spacing:.1em;text-transform:uppercase;color:var(--indigo)}}
.nums{{display:grid;gap:.55rem;grid-template-columns:repeat(auto-fit,minmax(8rem,1fr));margin:1rem 0 1.6rem}}
.num{{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:.7rem .85rem}}
.num b{{display:block;font-size:1.5rem;font-weight:800;color:var(--indigo);line-height:1.05;
 font-variant-numeric:tabular-nums}}
.num span{{display:block;font-size:.78rem;color:var(--muted);margin-top:.15rem}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:13px;
 padding:1rem 1.15rem;margin:.9rem 0}}
.card header{{display:flex;justify-content:space-between;gap:.6rem;flex-wrap:wrap;align-items:center}}
.when{{font-weight:750;font-variant-numeric:tabular-nums}}
.tags{{display:flex;gap:.35rem;flex-wrap:wrap}}
.tag{{font-size:.7rem;font-weight:700;border-radius:5px;padding:.12rem .45rem;
 background:var(--fill);color:var(--muted)}}
.tag.rПрод{{background:var(--indigo-soft);color:var(--indigo)}}
.tag.rПоль{{background:var(--green-soft);color:var(--green)}}
.tag.rСобы{{background:var(--sky-soft);color:var(--sky)}}
.tag.rМеха{{background:var(--amber-soft);color:var(--amber)}}
.tag.ev{{background:var(--sky-soft);color:var(--sky)}}
.tag.draft{{background:var(--amber-soft);color:var(--amber)}}
.post{{font-size:.95rem}} .post p{{margin:.45rem 0}}
.copy{{font:inherit;font-size:.83rem;font-weight:700;border:1px solid var(--line);
 background:var(--fill);color:var(--ink);border-radius:99px;padding:.3rem .8rem;cursor:pointer;margin-top:.5rem}}
.copy.ok{{background:var(--green);border-color:var(--green);color:#fff}}
.story{{margin-top:.8rem;padding:.6rem .8rem;border-left:3px solid var(--sky);
 background:var(--sky-soft);border-radius:0 8px 8px 0;font-size:.88rem}}
.story em{{display:block;font-style:normal;font-size:.7rem;text-transform:uppercase;
 letter-spacing:.06em;color:var(--muted);margin-bottom:.25rem}}
.note{{border-left:3px solid var(--indigo);background:var(--indigo-soft);padding:.75rem 1rem;
 border-radius:0 9px 9px 0;margin:1rem 0}}
.foot{{margin-top:2.5rem;padding-top:1.1rem;border-top:1px solid var(--line);
 color:var(--muted);font-size:.85rem}}
</style></head>
<body><div class="wrap">
<div class="kicker">Соцсети · план и готовые тексты</div>
<h1>Контент-план набора</h1>
<p class="sub">Тексты готовы к публикации: нажмите «Скопировать» и выложите
во ВКонтакте и Telegram. Цифры о местах подставляются из CRM в момент сборки
страницы — если группа заполнится, пост про неё исчезнет сам.</p>

<div class="nums">{tiles}</div>

<div class="note"><b>Два поста помечены черновиками.</b> Реферальная программа
и партнёрский кэшбек — механики, которых у нас ещё нет: не назван размер бонуса
и не согласованы партнёры. Публиковать их можно только после того, как условия
утверждены, иначе придётся отвечать на вопросы, ответов на которые нет.</div>

{"".join(cards)}

<div class="foot">🤖 Клод, ИИ-сотрудник KidsUP · собрано {date.today().strftime('%d.%m.%Y')}.
Продающий пост — не чаще одного из пяти: лента, где продаёт каждый, теряет охваты.</div>
</div>
<script>
document.querySelectorAll('.copy').forEach(function(b){{
  b.addEventListener('click',function(){{
    navigator.clipboard.writeText(b.dataset.t).then(function(){{
      b.classList.add('ok'); b.textContent='Скопировано';
      setTimeout(function(){{b.classList.remove('ok');b.textContent='Скопировать текст поста';}},1800);
    }});
  }});
}});
</script>
</body></html>"""


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "plan"
    f = facts()
    if cmd == "facts":
        print(f"группы 2026/27: {len(f['группы'])}")
        for s, v in sorted(f["предметы"].items(), key=lambda x: -x[1]["free"]):
            print(f"   {s:24s} свободно {v['free']:3d} из {v['cap']:3d}")
        print("\nгорящие группы:", [(g["subject"], g["when"], g["free"])
                                     for g in f["горящие"]])
    elif cmd == "texts":
        for p in all_posts(f):
            print("=" * 70)
            print(f"[{p['рубрика']}] {p['заголовок']}"
                  + ("   ⚠ ЧЕРНОВИК" if p.get("черновик") else ""))
            print(p["текст"])
            print()
    elif cmd == "page":
        open(OUT_PAGE, "w").write(build_page())
        print(f"{OUT_PAGE}: собрано")
    else:
        for row in plan():
            mark = " ⚠" if row["черновик"] else ""
            print(f"{row['дата']} {row['день']} · {row['рубрика']:12s} "
                  f"{row['заголовок'][:52]}{mark}"
                  + (f"   ({row['повод']})" if row["повод"] else ""))


if __name__ == "__main__":
    main()
