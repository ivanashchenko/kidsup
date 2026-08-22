"""Лист обзвона для Надежды: дети возраста подготовки к школе.

Кто звонит. Надежда Иванащенко — опытный человек, звонит по нашей базе
из Люберец, доступа к CRM у неё нет. Значит лист должен быть
самодостаточным: имя, возраст, телефон, что у семьи было с нами раньше
и когда — всё на одном экране, без переходов в карточку.

Кого берём. Всех детей, кому к 1 сентября 4–7,5 лет: это и есть ПШ1
(нечитающие) и ПШ2. Не только бывших учеников подготовки — их всего 245,
а подходящих по возрасту в базе больше тысячи, и половина из них никогда
не пробовала у нас именно этот предмет.

Кого не берём:
  · «некачественный лид» (125954) — туда складывали звонивших в Клуб
    Буракова, это не наши клиенты, и Надежда будет звонить своим же;
  · «не писать» (146328) и «отказ» (125957) — люди попросили не беспокоить;
  · уже записанных на 2026/27 — им звонить не о чем.

Порядок. Сначала тёплые: «думает» и «недозвон» — человек в разговоре
с нами уже был, разговор начинается не с нуля. Потом архив, внутри него
недавние выше давних: чем свежее занятия, тем лучше нас помнят.

Запуск:
    python -m app.nadezhdalist         — собрать и записать страницу
"""

from __future__ import annotations

import html as H
import json
import logging
import os
import re
from collections import defaultdict
from datetime import date

from . import sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.nadezhdalist")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"
OUT = "docs/obzvon_nadezhda.html"

SEASON = date(2026, 9, 1)
AGE_LO, AGE_HI = 4.0, 7.5
# 125954 «некачественный лид» — контакты Клуба Буракова: Надежда звонит
# по нашей базе, и её собственные клиенты в этом листе быть не должны
SKIP_STATE = {125954, 146328, 125957}
STATE_NAME = {125951: "новый лид", 345768: "недозвон", 146950: "думает",
              125952: "записался", 345759: "архив", 215202: "архив",
              125955: "архив", 146513: "архив", 347075: "архив"}

SUBJ = [("подготовка к школе", r"_ПШ|подготовк"), ("английский", r"_АЯ|английск"),
        ("мини-сад", r"_МС|мини-сад|детский сад"), ("нулевой класс", r"_НК|нулев"),
        ("лагерь", r"_ЛК|лагер|летний клуб"), ("ИЗО", r"_ИЗО"),
        ("шахматы", r"_ШХ|шахмат"), ("ментальная арифметика", r"_МА|ментальн"),
        ("раннее развитие", r"_РР|раннее|музыка и речь"),
        ("скорочтение", r"_СЧ|скорочт"), ("логопед", r"_ЛГ|логопед")]


def _subject(name: str) -> str | None:
    for label, pat in SUBJ:
        if re.search(pat, name or "", re.I):
            return label
    return None


def _age(u: dict) -> float | None:
    """Возраст на 1 сентября 2026 — то, что важно для набора в группу."""
    for a in (u.get("attributes") or []):
        if a.get("attributeAlias") == "birthday" and a.get("value"):
            try:
                bd = date.fromisoformat(str(a["value"])[:10])
            except ValueError:
                return None
            return round((SEASON - bd).days / 365.25, 1)
    return None


def collect() -> list[dict]:
    mk = MoyklassClient(sync.get_api_key())
    try:
        # Карточки берём одной выборкой: по одной это 1300+ запросов и
        # больше десяти минут ожидания там, где хватает трёх.
        users = taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=6)
        joins = taskguard.pull_all(mk, "/v1/company/joins", "joins")
        subs = taskguard.pull_all(mk, "/v1/company/userSubscriptions",
                                  "subscriptions", cache_hours=6)
        rc = mk.get("/v1/company/classes", {"limit": 500})
        cls = {c["id"]: (c.get("name") or "")
               for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
        # Кем уже занят администратор — чтобы не звонить одному человеку дважды
        busy = set()
        for mid in (232763, 232805, 202856, 154181):
            for t in taskguard.all_tasks(mk, mid):
                if not (t.get("isComplete") or t.get("isCompleted")) and t.get("userId"):
                    busy.add(t["userId"])
    finally:
        mk.close()

    signed = {j["userId"] for j in joins
              if str(cls.get(j.get("classId"), "")).startswith("2627")}
    was = defaultdict(set)
    for j in joins:
        s = _subject(cls.get(j.get("classId"), ""))
        if s and j.get("userId"):
            was[j["userId"]].add(s)
    last = defaultdict(str)
    for s in subs:
        uid, d = s.get("userId"), (s.get("endDate") or s.get("beginDate") or "")[:10]
        if uid and d > last[uid]:
            last[uid] = d

    out = []
    for u in users:
        if u.get("clientStateId") in SKIP_STATE or u["id"] in signed:
            continue
        phone = "".join(c for c in str(u.get("phone") or "") if c.isdigit())
        if len(phone) < 11:
            continue
        age = _age(u)
        if age is None or not (AGE_LO <= age <= AGE_HI):
            continue
        out.append({
            "uid": u["id"], "name": (u.get("name") or "").strip(),
            "phone": "+" + phone[-11:], "age": age,
            "state": STATE_NAME.get(u.get("clientStateId"), "архив"),
            "state_id": u.get("clientStateId"),
            "was": sorted(was.get(u["id"], set()))[:3],
            "last": last.get(u["id"], ""),
            "busy": u["id"] in busy,
        })
    out.sort(key=_order)
    json.dump(out, open(f"{SP}/nadezhda_list.json", "w"), ensure_ascii=False)
    log.info("в листе: %d детей", len(out))
    return out


def _order(c: dict) -> tuple:
    """Тёплые вперёд, внутри — те, кто был у нас недавно.

    «Думает» и «недозвон» значат, что разговор с нами уже начинался:
    такой звонок стоит дешевле и закрывается чаще, чем холодный."""
    warm = {"думает": 0, "недозвон": 1, "новый лид": 2}.get(c["state"], 3)
    return (warm, _flip(c.get("last") or ""), c["name"])


def _flip(d: str) -> str:
    """Сортировка по дате от новых к старым при возрастающем порядке."""
    return "".join(chr(ord("9") - int(ch)) if ch.isdigit() else ch for ch in d)


def build(rows: list[dict] | None = None) -> str:
    rows = rows if rows is not None else collect()
    by_age = defaultdict(list)
    for c in rows:
        by_age[int(c["age"])].append(c)

    blocks = []
    for a in sorted(by_age):
        items = by_age[a]
        trs = []
        for c in items:
            was = ", ".join(c["was"]) or "—"
            last = c["last"][:7] if c["last"] else "—"
            mark = ' <span class="busy">админ звонит</span>' if c["busy"] else ""
            trs.append(
                f"<tr><td class='nm'>{H.escape(c['name'])}{mark}</td>"
                f"<td class='ag'>{c['age']:g}</td>"
                f"<td class='ph'>{H.escape(c['phone'])}</td>"
                f"<td>{H.escape(was)}</td>"
                f"<td class='dt'>{H.escape(last)}</td>"
                f"<td class='st'>{H.escape(c['state'])}</td>"
                f"<td class='res'></td></tr>")
        blocks.append(f"""<section class="sheet">
  <div class="sh"><h2>{a} лет к 1 сентября</h2><span>{len(items)} детей</span></div>
  <table class="c"><tr><th>ребёнок</th><th>возр.</th><th>телефон</th>
    <th>что было у нас</th><th>когда</th><th>статус</th>
    <th class="res">итог разговора</th></tr>{"".join(trs)}</table>
</section>""")

    warm = sum(1 for c in rows if c["state"] in ("думает", "недозвон", "новый лид"))
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Обзвон: подготовка к школе</title>
<style>
:root{{--ink:#1B1D2B;--muted:#5F6478;--line:#C9C4B6;--fill:#F3F1E9;
 --indigo:#312783;--green:#5C8C1E}}
*{{box-sizing:border-box}}
body{{background:#fff;color:var(--ink);margin:0;
 font:14px/1.5 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
.wrap{{max-width:70rem;margin:0 auto;padding:1.6rem 1rem 3rem}}
h1{{font-size:1.6rem;font-weight:800;margin:.2rem 0 .3rem}}
h2{{font-size:1.15rem;font-weight:770;margin:0}}
.lead{{color:var(--muted);max-width:46rem;font-size:.95rem}}
.sheet{{margin-top:1.5rem;padding-top:1rem;border-top:2px solid var(--line);
 break-before:page}}
.sheet:first-of-type{{break-before:auto}}
.sh{{display:flex;align-items:baseline;gap:.7rem;margin-bottom:.5rem}}
.sh span{{font-size:.82rem;color:var(--muted)}}
table.c{{border-collapse:collapse;width:100%;font-size:.85rem}}
table.c th,table.c td{{border:1px solid var(--line);padding:.3rem .4rem;
 text-align:left;vertical-align:top}}
table.c th{{background:var(--fill);font-size:.78rem;font-weight:750}}
.nm{{font-weight:650;width:15rem}}
.ag,.dt{{font-variant-numeric:tabular-nums;white-space:nowrap}}
.ag{{width:3rem;text-align:center}}
.ph{{font-variant-numeric:tabular-nums;white-space:nowrap;width:9rem}}
.st{{width:6rem;color:var(--muted);font-size:.8rem}}
.res{{width:13rem}}
.busy{{font-size:.68rem;color:#B03A2E;font-weight:700;white-space:nowrap}}
.note{{background:var(--fill);border-left:3px solid var(--indigo);
 padding:.5rem .7rem;margin:.7rem 0;font-size:.86rem;break-inside:avoid}}
@media print{{
 .wrap{{max-width:none;padding:0}}
 table.c{{font-size:8pt}} .nm{{width:auto}}
 @page{{size:A4 landscape;margin:10mm}}
}}
</style></head><body><div class="wrap">
<h1>Обзвон: подготовка к школе</h1>
<p class="lead">Дети, которым к 1 сентября от 4 до 7 лет — это возраст ПШ1
(нечитающие) и ПШ2. Всего {len(rows)} семей, из них {warm} уже разговаривали
с нами раньше — они идут первыми в каждом разделе. Данные из CRM
на {date.today().strftime('%d.%m.%Y')}.</p>

<div class="note"><b>Что предлагаем.</b> Подготовку к школе: занятия
с 31 августа, группы по 8 детей, два раза в неделю. Если ребёнок не читает —
это ПШ1, там гарантия: читает трёхбуквенные слова за три месяца, иначе
занимается бесплатно, пока не зачитает. Первое занятие условно-бесплатное:
не понравится — платить не нужно, понравится — войдёт в первый абонемент.</div>

<div class="note"><b>Пометка «админ звонит»</b> — по этой семье уже стоит
задача у нашего администратора. Такую лучше пропустить, чтобы человеку
не позвонили дважды за день.</div>

<div class="note"><b>Что записывать в последней колонке.</b> Записался (какой
день и время) · перезвонить (когда) · не актуально (почему) · не дозвонилась.
Лист вернуть в центр — по нему заведут записи в CRM.</div>

{"".join(blocks)}
</div></body></html>"""


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rows = collect()
    os.makedirs("docs", exist_ok=True)
    open(OUT, "w").write(build(rows))
    print(f"{OUT}: собрано, {len(rows)} детей")


if __name__ == "__main__":
    main()
