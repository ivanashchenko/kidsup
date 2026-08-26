"""Публичная страница трёх событий открытия сезона: /vstrechi.

Зачем. В рассылках мы зовём на праздник 29 августа, День открытых дверей
30-го и Неделю открытых уроков 31.08–06.09, но вести родителя было некуда:
на сайте страниц под эти события нет, а kidsup.ru/schedule показывает
обычное расписание и про события молчит. Ссылка «подробности» в письме,
ведущая на общий сайт, работает хуже, чем её отсутствие: человек не находит
того, за чем пришёл, и закрывает вкладку.

Что на странице. Три события подряд, у каждого — дата, время, что будет
и нужна ли запись. Ниже живое расписание открытых уроков из CRM: родитель
видит, в какой день и час идёт нужный ему предмет, и приходит именно туда.

Почему у нас, а не на сайте. Расписание меняется, а страница собирается
из тех же данных, что и /enrollment: правок руками не требует и не
разойдётся с реальностью. Домен app.kidsup.ru — наш поддомен, для ссылки
в WABA-шаблоне это допустимо.

Запуск:
    python -m app.events    — собрать docs/vstrechi.html
"""

from __future__ import annotations

import html as H
import logging
import re
from collections import defaultdict
from datetime import date

from . import socialfactory as sf

log = logging.getLogger("kidsup.events")
OUT = "docs/vstrechi.html"

DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
# Проверено по календарю 2026: понедельник — 31 августа, воскресенье —
# 6 сентября. Прежняя раскладка сдвигала всю неделю на день, и
# подтверждения называли клиенту не тот день.
WEEK = {"пн": "понедельник 31 августа", "вт": "вторник 1 сентября",
        "ср": "среда 2 сентября", "чт": "четверг 3 сентября",
        "пт": "пятница 4 сентября", "сб": "суббота 5 сентября",
        "вс": "воскресенье 6 сентября"}

EVENTS = [
    {"when": "29 августа, суббота, 11:00", "tag": "Праздник",
     "title": "Открытие сезона",
     "text": "Праздник для всей семьи: игры, мастер-классы, знакомство "
             "с педагогами. Можно прийти просто посмотреть центр изнутри "
             "и понять, нравится ли здесь ребёнку.",
     "note": "Вход свободный, запись не нужна"},
    {"when": "30 августа, воскресенье, 11:00–15:00",
     "tag": "День открытых дверей",
     "title": "Знакомство с программами",
     "text": "Показываем, как устроены занятия: подготовка к школе, "
             "английский по кембриджской программе, раннее развитие, "
             "мини-сад и нулевой класс. Отвечаем на вопросы про уровни, "
             "расписание и цены.",
     "note": "Вход свободный, но лучше записаться — подберём удобное время",
     "link": ("/day", "Программа дня и запись")},
    {"when": "31 августа — 6 сентября", "tag": "Неделя открытых уроков",
     "title": "Настоящее занятие, а не показательное",
     "text": "Учебный год начинается 31 августа, и всю первую неделю можно "
             "прийти на любой урок по расписанию. Ребёнок занимается вместе "
             "с группой, родитель смотрит, как идёт занятие и как работает "
             "педагог.",
     "note": "Первое занятие условно-бесплатное: не понравится — платить "
             "не нужно, понравится — войдёт в первый абонемент",
     "link": ("/week", "Как это устроено и выбор занятия")},
]


def _slot(when: str, name: str) -> tuple[list[str], str]:
    src = f"{when} {name}".lower().replace("_", " ").replace("-", " ")
    days = [d for d in DAYS if re.search(rf"\b{d}\b", src)]
    m = re.search(r"(\d{1,2}:\d{2})", src)
    return days, (m.group(1) if m else "")


def week_grid() -> str:
    """Расписание открытых уроков по дням недели — из живых данных CRM."""
    f = sf.facts(force=True)
    by = defaultdict(list)
    for g in f["группы"]:
        if g["subject"] == "логопед":       # индивидуальные, не для визита
            continue
        days, t = _slot(g.get("when") or "", g.get("name") or "")
        if not t:
            continue
        for d in days:
            by[d].append((t, g["subject"], g.get("free", 0)))
    if not by:
        return ""
    out = []
    for d in DAYS:
        items = sorted(set(by.get(d, [])))
        if not items:
            continue
        rows = "".join(
            f'<div class="ln"><b>{H.escape(t)}</b>'
            f'<span>{H.escape(s if s.isupper() or s == "ИЗО" else s.capitalize())}</span>'
            f'<i>{"есть места" if fr else "мест нет"}</i></div>'
            for t, s, fr in items)
        out.append(f'<div class="day"><h3>{H.escape(WEEK.get(d, d))}</h3>{rows}</div>')
    return f'<div class="week">{"".join(out)}</div>'


def build() -> str:
    grid = week_grid()
    def _card(e: dict) -> str:
        # У ДОД и Недели есть свои лендинги с программой и записью — с общей
        # страницы человек должен попадать туда, а не искать их заново.
        link = ""
        if e.get("link"):
            href, label = e["link"]
            link = f'<a class="more" href="{href}">{H.escape(label)} →</a>'
        return f"""<article class="ev">
  <div class="tag">{H.escape(e["tag"])}</div>
  <div class="when">{H.escape(e["when"])}</div>
  <h2>{H.escape(e["title"])}</h2>
  <p>{H.escape(e["text"])}</p>
  <div class="note">{H.escape(e["note"])}</div>
  {link}
</article>"""

    cards = "".join(_card(e) for e in EVENTS)

    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KidsUP — открытие сезона 2026/27</title>
<meta name="description" content="Праздник открытия сезона 29 августа,
День открытых дверей 30 августа и Неделя открытых уроков 31 августа —
6 сентября в детском центре KidsUP на бульваре Рокоссовского.">
<style>
:root{{--indigo:#312783;--blue:#1DA7E0;--green:#7DB928;--amber:#F59C00;
 --ink:#1B1D2B;--muted:#5F6478;--line:#E3E1DA;--fill:#F6F5F1}}
*{{box-sizing:border-box}}
body{{margin:0;background:#fff;color:var(--ink);
 font:16px/1.6 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
.wrap{{max-width:52rem;margin:0 auto;padding:1.6rem 1.1rem 3rem}}
header{{border-bottom:3px solid var(--indigo);padding-bottom:1rem;margin-bottom:1.4rem}}
h1{{font-size:1.9rem;line-height:1.2;font-weight:800;margin:.2rem 0 .4rem;
 color:var(--indigo);text-wrap:balance}}
.sub{{color:var(--muted);font-size:1rem;margin:0}}
.ev{{border:1px solid var(--line);border-radius:.7rem;padding:1rem 1.1rem;
 margin-bottom:1rem}}
.tag{{display:inline-block;background:var(--indigo);color:#fff;font-size:.72rem;
 font-weight:750;letter-spacing:.04em;text-transform:uppercase;
 padding:.2rem .5rem;border-radius:.3rem}}
.when{{color:var(--blue);font-weight:750;margin-top:.5rem;font-size:1.02rem}}
.ev h2{{font-size:1.2rem;font-weight:770;margin:.15rem 0 .45rem}}
.ev p{{margin:0 0 .6rem;color:#33364a}}
.note{{background:var(--fill);border-left:3px solid var(--green);
 padding:.5rem .7rem;font-size:.92rem;color:#33364a;border-radius:0 .3rem .3rem 0}}
.more{{display:inline-block;margin-top:.7rem;color:var(--indigo);font-weight:750;
 text-decoration:none;border-bottom:2px solid var(--blue);padding-bottom:.1rem}}
.more:hover{{border-bottom-color:var(--indigo)}}
h2.sec{{font-size:1.3rem;font-weight:780;margin:2rem 0 .3rem;color:var(--indigo)}}
.lead{{color:var(--muted);margin:0 0 1rem;font-size:.96rem}}
.week{{display:grid;gap:.8rem}}
.day{{border:1px solid var(--line);border-radius:.6rem;padding:.7rem .9rem}}
.day h3{{margin:0 0 .4rem;font-size:1rem;font-weight:750;color:var(--indigo)}}
.ln{{display:flex;align-items:baseline;gap:.6rem;padding:.22rem 0;
 border-bottom:1px solid var(--line)}}
.ln:last-child{{border-bottom:0}}
.ln b{{font-variant-numeric:tabular-nums;width:3.6rem;flex:none;font-weight:700}}
.ln span{{flex:1}}
.ln i{{font-style:normal;font-size:.8rem;color:var(--green);white-space:nowrap}}
.cta{{margin-top:2rem;background:var(--indigo);color:#fff;border-radius:.8rem;
 padding:1.2rem 1.3rem}}
.cta h2{{margin:0 0 .4rem;font-size:1.25rem;color:#fff}}
.cta p{{margin:0 0 .8rem;opacity:.92}}
.cta a{{display:inline-block;background:var(--green);color:#fff;
 text-decoration:none;font-weight:750;padding:.6rem 1.1rem;border-radius:.5rem}}
.addr{{margin-top:1.4rem;color:var(--muted);font-size:.92rem;
 border-top:1px solid var(--line);padding-top:.9rem}}
@media (prefers-color-scheme: dark){{
 :root:not([data-theme="light"]){{--ink:#EDEEF3;--muted:#A5A9BC;--line:#33364A;
  --fill:#222432;--indigo:#8E86E8;--blue:#57C4F2}}
 :root:not([data-theme="light"]) body{{background:#161826;color:var(--ink)}}
 :root:not([data-theme="light"]) .ev p{{color:var(--ink)}}
 :root:not([data-theme="light"]) .note{{color:var(--ink)}}
}}
</style></head><body><div class="wrap">
<header>
  <h1>Открытие сезона 2026/27</h1>
  <p class="sub">Детский центр и английский сад KidsUP · бульвар Маршала
  Рокоссовского, 6к1В — напротив ТЦ «Янтарь», 2 минуты от метро</p>
</header>

{cards}

<h2 class="sec">Расписание открытых уроков</h2>
<p class="lead">С 31 августа по 6 сентября занятия идут по обычному
расписанию — можно прийти на любое. Выберите день и время, а мы подскажем,
в какую группу лучше по возрасту и уровню.</p>
{grid or "<p class='lead'>Расписание уточняется — напишите, подберём время.</p>"}

<div class="cta">
  <h2>Записаться или спросить</h2>
  <p>Скажите возраст ребёнка и удобные дни — подберём группу и время.</p>
  <a href="https://wa.me/79199683507">Написать в WhatsApp</a>
</div>

<div class="addr">
  Учебный год: 31 августа 2026 — 31 мая 2027<br>
  б-р Маршала Рокоссовского, 6к1В · <a href="https://kidsup.ru">kidsup.ru</a><br>
  Данные о занятости обновлены {date.today().strftime('%d.%m.%Y')}
</div>
</div></body></html>"""


def main():
    import os
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    os.makedirs("docs", exist_ok=True)
    html = build()
    open(OUT, "w").write(html)
    print(f"{OUT}: собрано, {len(html)} байт")


if __name__ == "__main__":
    main()
