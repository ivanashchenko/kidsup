"""Печатные бланки для обзвона: расписание-сетка, цены, контакты.

Для Надежды, которая звонит по нашей базе из Люберец. У неё нет доступа
к CRM и нет времени в ней разбираться, поэтому всё, что нужно для
разговора, лежит на бумаге: где какие занятия, сколько стоит, что
отвечать на частые вопросы и куда передать результат.

Почему сетка, а не список. В разговоре родитель говорит «нам удобно
во вторник вечером» — и нужно за секунду увидеть, что есть во вторник
вечером по всем предметам сразу. Список групп такого не даёт, таблица
день × время даёт. В свободные клетки вписывается имя ребёнка: лист
работает и как справочник, и как черновик записи.

Что печатать:
  · по листу расписания на каждый предмет, где есть группы;
  · лист цен — без него разговор упирается в «я уточню и перезвоню»;
  · лист контактов и ответов на частые вопросы.

Занятость берётся из CRM: занятые места отмечены, свободные пустые —
видно, куда реально можно записать.

Запуск:
    python -m app.blanki      — собрать docs/blanki_obzvona.html
"""

from __future__ import annotations

import html as H
import logging
import re
from collections import defaultdict
from datetime import date

from . import socialfactory as sf

log = logging.getLogger("kidsup.blanki")
OUT = "docs/blanki_obzvona.html"

DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
# Порядок предметов: сначала то, ради чего звоним.
SUBJ_ORDER = ["подготовка к школе", "английский", "музыка и речь",
              "раннее развитие", "ментальная арифметика", "ИЗО", "шахматы",
              "нулевой класс", "мини-сад", "логопед"]

CONTACTS = [
    ("Адрес", "б-р Маршала Рокоссовского, 6 к1В — напротив ТЦ «Янтарь», "
              "2 минуты пешком от м. Бульвар Рокоссовского"),
    ("Сайт", "kidsup.ru"),
    ("Кому передать запись", "Лиза — переписка и оплаты; администратор "
                             "на смене — звонки и запись в группы"),
    ("Учебный год", "с 31 августа 2026 по 31 мая 2027"),
    ("События", "29.08 сб 11:00 — праздник открытия сезона, вход свободный, "
                "запись не нужна · 30.08 вс — День открытых дверей · "
                "31.08–06.09 — Неделя открытых уроков"),
]

FAQ = [
    ("Первое занятие бесплатное?",
     "Условно-бесплатное: не понравится — платить не нужно, понравится — "
     "занятие входит в первый абонемент. Так и говорим, слово «бесплатное» "
     "отдельно не используем."),
    ("А кто ведёт?",
     "Подготовку к школе в этом году ведут Татьяна и Елена. Познакомиться "
     "можно 30 августа на Дне открытых дверей или на первом занятии."),
    ("Сколько детей в группе?",
     "До восьми. В раннем развитии и музыке — меньше, там нужен близкий "
     "контакт с педагогом."),
    ("Что если ребёнок не читает?",
     "Это и есть ПШ1 — группа для нечитающих. Гарантия: с нуля читает "
     "трёхбуквенные слова за три месяца, иначе занимается бесплатно, пока "
     "не зачитает. Условия: диагностика на первом занятии, посещаемость "
     "от 80%, домашние задания."),
    ("Какие скидки?",
     "Все по 10%: первый абонемент при оплате в день пробного (только новым), "
     "второй предмет, второй ребёнок, многодетным и семьям участников СВО. "
     "Скидки НЕ суммируются — действует одна."),
    ("А можно позже начать?",
     "Можно, но группа набирается сейчас, и в сентябре мест будет меньше. "
     "До 31 августа включительно сентябрь идёт по ценам прошлого года."),
    ("Нам неудобно это время",
     "Спросить, какое удобно, и посмотреть по сетке другие дни. Если ничего "
     "не подходит — записать пожелание и передать: возможно, откроем группу."),
    ("Мы уже ходим в другой центр",
     "Не спорить. Предложить прийти на Неделю открытых уроков и сравнить — "
     "это ни к чему не обязывает."),
]


def _slot(when: str) -> tuple[list[str], str]:
    """«вт-чт 17:00» → (['вт','чт'], '17:00')."""
    if not when:
        return [], ""
    m = re.search(r"(\d{1,2}:\d{2})", when)
    t = m.group(1) if m else ""
    days = [d for d in DAYS if d in when.lower()]
    return days, t


def grid(subject: str, groups: list[dict]) -> str:
    """Сетка день × время с клетками под имена."""
    cells = defaultdict(list)
    times = set()
    for g in groups:
        ds, t = _slot(g["when"])
        if not t:
            t = "по записи"
        times.add(t)
        for d in (ds or ["—"]):
            cells[(d, t)].append(g)
    if not times:
        return ""
    order = sorted(times, key=lambda x: (x == "по записи", x))
    head = "".join(f"<th>{d}</th>" for d in DAYS)
    rows = []
    for t in order:
        tds = []
        for d in DAYS:
            gs = cells.get((d, t)) or []
            if not gs:
                tds.append('<td class="off"></td>')
                continue
            g = gs[0]
            lines = "".join(
                f'<div class="ln{" busy" if i < g["busy"] else ""}">'
                f'{i + 1}.</div>' for i in range(g["cap"]))
            lvl = ""
            m = re.search(r"(ПШ[12]|Pre-A1|A1-A2|Starters|Movers|Flyers|"
                          r"нечитающие|читающие|[\d,\.]+\s*-\s*[\d,\.]+\s*лет)",
                          g["name"])
            if m:
                lvl = f'<div class="lvl">{H.escape(m.group(1))}</div>'
            tds.append(f'<td>{lvl}<div class="slots">{lines}</div>'
                       f'<div class="free">свободно {g["free"]} из {g["cap"]}</div></td>')
        rows.append(f'<tr><th class="t">{H.escape(t)}</th>{"".join(tds)}</tr>')
    return (f'<table class="grid"><tr><th class="t"></th>{head}</tr>'
            + "".join(rows) + "</table>")


def prices() -> list[tuple[str, list[tuple[str, str, str]]]]:
    """Прайс из CRM, сгруппированный по предмету.

    Цены не выдумываем и не держим в коде: тарифы живут в МойКласс,
    и лист должен показывать то же, что увидит администратор при оплате."""
    from . import sync
    from .moyklass_client import MoyklassClient
    mk = MoyklassClient(sync.get_api_key())
    try:
        r = mk.get("/v1/company/subscriptions", {"limit": 200})
        subs = (r.get("subscriptions") if isinstance(r, dict) else r) or []
    except Exception:
        return []
    finally:
        mk.close()
    # Летний клуб закончился 28 августа: его недельные тарифы в листе
    # для набора на учебный год только путают — родитель слышит «42 500»
    # и кладёт трубку, хотя занятие в группе стоит тысячу.
    SUMMER = re.compile(r"недел|полдня|полный день|лагер|ПРОБНАЯ", re.I)
    groups = defaultdict(list)
    seen = set()
    for x in subs:
        nm = x.get("name") or ""
        price = x.get("price") or 0
        if not price or x.get("isDeleted") or SUMMER.search(nm):
            continue
        n = x.get("visitCount") or 0
        subj = sf._subject(nm) or _price_subject(nm)
        if not subj:
            continue
        per = f"{round(price / n):,} ₽".replace(",", " ") if n else "—"
        label = nm.split("_", 1)[-1].replace("_", " ").strip()
        key = (subj, label)
        if key in seen:           # один и тот же тариф двумя строками
            continue
        seen.add(key)
        groups[subj].append((label, f"{price:,} ₽".replace(",", " "), per))
    order = [s for s in SUBJ_ORDER if s in groups] + \
            [s for s in groups if s not in SUBJ_ORDER]
    return [(s, sorted(groups[s], key=lambda x: len(x[0]))) for s in order]


def _price_subject(name: str) -> str | None:
    """Названия тарифов не совпадают с названиями групп: в CRM это
    «Подготовка к школе_8 занятий», а не «2627_ПШ_вт-чт…»."""
    n = (name or "").lower()
    if "подготовка к школе" in n:
        return "подготовка к школе"
    if "английск" in n and "сад" in n:
        return "мини-сад"
    if "английск" in n or "нейро" in n:
        return "английский"
    if "раннее развитие" in n or "музык" in n:
        return "раннее развитие"
    if "изо" in n:
        return "ИЗО"
    if "ментальн" in n:
        return "ментальная арифметика"
    if "шахмат" in n:
        return "шахматы"
    if "логопед" in n:
        return "логопед"
    if "нулев" in n:
        return "нулевой класс"
    if "танц" in n or "хорео" in n:
        return "танцы"
    return None


def build() -> str:
    f = sf.facts(force=True)
    by = defaultdict(list)
    for g in f["группы"]:
        by[g["subject"]].append(g)

    sheets = []
    for s in SUBJ_ORDER:
        gs = by.get(s)
        if not gs:
            continue
        table = grid(s, gs)
        if not table:
            continue
        total_free = sum(g["free"] for g in gs)
        sheets.append(f"""<section class="sheet">
  <div class="sh"><h2>{H.escape(s if s.isupper() or s == "ИЗО" else s.capitalize())}</h2>
    <span>всего свободно {total_free} мест · {len(gs)} групп</span></div>
  {table}
  <div class="hint">Закрашенная клетка — место занято. В пустые вписывайте
  имя ребёнка и телефон, потом передайте в центр.</div>
</section>""")

    ptab = []
    for subj, items in prices():
        rows = "".join(f"<tr><td>{H.escape(a)}</td><td class='p'>{H.escape(b)}</td>"
                       f"<td class='p m'>{H.escape(c)}</td></tr>" for a, b, c in items)
        ptab.append(f"<tr class='sub'><td colspan='3'>{H.escape(subj)}</td></tr>{rows}")
    price_tbl = ("<table class='c pr'><tr><th>абонемент</th><th class='p'>цена</th>"
                 "<th class='p'>за занятие</th></tr>" + "".join(ptab) + "</table>"
                 ) if ptab else "<p>Прайс не загрузился — уточните в центре.</p>"

    faq = "".join(f"<div class='q'><b>{H.escape(q)}</b><p>{H.escape(a)}</p></div>"
                  for q, a in FAQ)
    cont = "".join(f"<tr><td class='k'>{H.escape(k)}</td><td>{H.escape(v)}</td></tr>"
                   for k, v in CONTACTS)

    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Бланки для обзвона</title>
<style>
:root{{--ink:#1B1D2B;--muted:#5F6478;--line:#C9C4B6;--fill:#F3F1E9;
 --indigo:#312783;--green:#5C8C1E;--amber:#B26F00}}
*{{box-sizing:border-box}}
body{{background:#fff;color:var(--ink);margin:0;
 font:14px/1.5 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
.wrap{{max-width:64rem;margin:0 auto;padding:1.6rem 1rem 3rem}}
h1{{font-size:1.6rem;font-weight:800;margin:.2rem 0 .3rem}}
h2{{font-size:1.2rem;font-weight:770;margin:0}}
.lead{{color:var(--muted);max-width:44rem;font-size:.95rem}}
.sheet{{margin-top:1.6rem;padding-top:1.1rem;border-top:2px solid var(--line);
 break-inside:avoid;page-break-inside:avoid}}
.sh{{display:flex;justify-content:space-between;align-items:baseline;
 gap:.7rem;flex-wrap:wrap;margin-bottom:.5rem}}
.sh span{{font-size:.82rem;color:var(--muted)}}
table.grid{{border-collapse:collapse;width:100%;table-layout:fixed}}
table.grid th,table.grid td{{border:1px solid var(--line);vertical-align:top;
 padding:.25rem .3rem}}
table.grid th{{background:var(--fill);font-size:.8rem;font-weight:750;text-align:center}}
table.grid th.t{{width:4.2rem;font-variant-numeric:tabular-nums;
 background:var(--fill);text-align:left}}
table.grid td{{height:4.6rem}}
table.grid td.off{{background:repeating-linear-gradient(45deg,#fff,#fff 5px,#FAFAF7 5px,#FAFAF7 10px)}}
.lvl{{font-size:.68rem;color:var(--indigo);font-weight:700;margin-bottom:.15rem;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.slots{{display:flex;flex-wrap:wrap;gap:1px}}
.ln{{width:calc(50% - 1px);border-bottom:1px solid var(--line);height:.95rem;
 font-size:.6rem;color:#B9B3A4}}
.ln.busy{{background:var(--fill);border-bottom-color:#9C978A}}
.free{{font-size:.62rem;color:var(--green);margin-top:.15rem}}
.hint{{font-size:.75rem;color:var(--muted);margin-top:.35rem}}
.q{{break-inside:avoid;margin:.6rem 0}}
.q b{{font-size:.92rem}} .q p{{margin:.1rem 0 0;font-size:.88rem;color:var(--muted)}}
table.c{{border-collapse:collapse;width:100%;font-size:.9rem;margin:.6rem 0}}
table.c td{{border-top:1px solid var(--line);padding:.4rem .5rem;vertical-align:top}}
table.c td.k{{font-weight:730;white-space:nowrap;width:11rem}}
table.pr th{{background:var(--fill);text-align:left;font-size:.75rem;
 text-transform:uppercase;letter-spacing:.04em;color:var(--muted);padding:.35rem .5rem}}
table.pr td.p{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
table.pr td.m{{color:var(--muted);font-size:.85rem}}
table.pr tr.sub td{{background:var(--fill);font-weight:750;padding-top:.5rem}}
.note{{border-left:3px solid var(--amber);background:#FBF0DC;padding:.6rem .85rem;
 border-radius:0 8px 8px 0;margin:.8rem 0;font-size:.9rem}}
.foot{{margin-top:2rem;padding-top:.9rem;border-top:1px solid var(--line);
 color:var(--muted);font-size:.8rem}}
@media print{{
  .wrap{{padding:0;max-width:100%}}
  .sheet{{page-break-before:always}}
  .sheet:first-of-type{{page-break-before:auto}}
  @page{{size:A4 landscape;margin:10mm}}
}}
</style></head>
<body><div class="wrap">

<h1>Бланки для обзвона</h1>
<p class="lead">Печатать в альбомной ориентации. По листу на предмет плюс
цены и контакты. Данные о занятости — из CRM на {date.today().strftime('%d.%m.%Y')},
поэтому лист живёт примерно неделю: дальше лучше распечатать заново.</p>

<div class="note"><b>Как пользоваться.</b> Родитель называет удобный день
и время — вы находите клетку на пересечении и видите, есть ли там занятие
и остались ли места. Записали ребёнка карандашом в свободную строку,
после разговора передали в центр: имя, возраст, телефон, предмет и время.
Пока запись не внесена в систему, места за ребёнком нет.</div>

{"".join(sheets)}

<section class="sheet">
<div class="sh"><h2>Цены</h2><span>из CRM · до 31 августа сентябрь по ценам прошлого года</span></div>
{price_tbl}
<div class="note"><b>Если по одному предмету две цены</b> — значит в CRM
остался тариф прошлого года. Называйте меньшую и уточните у Лизы, какая
действует: ошибиться в цене хуже, чем взять паузу на минуту.</div>
<div class="note"><b>Скидки — все по 10%, и только одна.</b> Первый абонемент
при оплате в день пробного (только новым), второй предмет, второй ребёнок,
многодетным и семьям участников СВО. Скидки НЕ суммируются: действует одна,
самая выгодная. Говорить об этом сразу, чтобы потом не было «а нам обещали».</div>
</section>

<section class="sheet">
<div class="sh"><h2>Ответы на частые вопросы</h2></div>
{faq}
</section>

<section class="sheet">
<div class="sh"><h2>Контакты и главное</h2></div>
<table class="c">{cont}</table>
<div class="note"><b>Чего не говорить.</b> Не называть цену «на глаз» —
только по листу цен. Не обещать конкретного педагога, кроме Татьяны
и Елены на подготовке. Не говорить «осталось два места», если по сетке
это не так.</div>
</section>

<div class="foot">🤖 Клод, ИИ-сотрудник KidsUP · {date.today().strftime('%d.%m.%Y')}.
Занятость групп меняется каждый день — перед новым обзвоном соберите лист заново.</div>

</div></body></html>"""


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    open(OUT, "w").write(build())
    print(f"{OUT}: собрано")


if __name__ == "__main__":
    main()
