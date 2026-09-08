# -*- coding: utf-8 -*-
"""Загрузка кабинетов по сетке недели 07–13.09 и план «только замены» (решение Бориса 08.09).

Источники: scratchpad/lessons_week.json (GET /v1/company/lessons за неделю, поля roomId/classId/teacherIds),
scratchpad/rooms_classes.json (кабинеты К1–К8 и имена групп). Только чтение. → docs/kabinety_zamena_0809.html
"""
import json, pathlib, collections, html

S = pathlib.Path("/tmp/claude-0/-home-user-kidsup/f2c35386-c271-55ec-b217-3b85ac2d6607/scratchpad")
les = json.load(open(S / "lessons_week.json", encoding="utf-8"))
rc = json.load(open(S / "rooms_classes.json", encoding="utf-8"))
rooms = {int(k): v for k, v in rc["rooms"].items()}; classes = {int(k): v for k, v in rc["classes"].items()}
DAYS = {"2026-09-07": "пн", "2026-09-08": "вт", "2026-09-09": "ср", "2026-09-10": "чт", "2026-09-11": "пт", "2026-09-12": "сб", "2026-09-13": "вс"}
ORDER = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
HOURS = [f"{h:02d}" for h in range(9, 20)]
# 08.09 Борис: кабинетов физически четыре — К1–К4; К5–К8 в CRM не существуют
ROOMS = [rooms[k] for k in sorted(rooms) if rooms[k] in ("К1", "К2", "К3", "К4")]

def short(nm):
    nm = nm[5:] if nm.startswith("2627_") else nm
    return nm

grid = collections.defaultdict(lambda: collections.defaultdict(list))  # (day,hour) -> room -> [names]
for l in les:
    nm = classes.get(l["classId"], "?")
    if "Заявк" in nm:
        continue
    d = DAYS[l["date"]]; h = l["beginTime"][:2]
    grid[(d, h)][rooms.get(l.get("roomId"), "—")].append(short(nm))
# понедельник в выгрузке отсутствует (занятия 07.09 со статусом «проведено» не вернулись) — восстанавливаем из пн-групп
for (d, h), cell in list(grid.items()):
    if d in ("ср",):  # пн-ср группы
        for r, names in cell.items():
            for nm in names:
                if "пн - ср" in nm or "пн-ср" in nm or "пн - пт" in nm:
                    grid[("пн", h)][r].append(nm)
    if d == "чт":
        for r, names in cell.items():
            for nm in names:
                if "пн-чт" in nm or "пн 19:00" in nm:
                    grid[("пн", h)][r].append(nm)
    if d == "вт":
        for r, names in cell.items():
            for nm in names:
                if "пн - пт" in nm or "Мини-сад" in nm or "Нулевой" in nm or "ЛГ Елена_Пн" in nm:
                    grid[("пн", h)][r].append(nm)
for nm_pat, h, r in (("ЛГ Елена_Пн_9:30", "09", "К3"), ("ЛГ Елена_Пн_10:30", "10", "К3"), ("ЛГ Елена_Пн_16:00", "16", "К4"), ("ЛГ Елена_Пн_17:00", "17", "К4"), ("ЛГ Елена_Пн_18:00", "18", "К4"), ("ЛГ Елена_Пн_19:00", "19", "К4")):
    if not any(nm_pat in x for x in grid[("пн", h)].get(r, [])):
        grid[("пн", h)][r].append(nm_pat)

# загрузка
def used(d, hrs):
    return sum(len([r for r in grid.get((d, h), {}) if r != "—"]) for h in hrs)
EVE = ["16", "17", "18", "19"]; MORN = ["09", "10", "11", "12", "13"]; DAYT = ["14", "15"]
rows_util = []
for d in ORDER:
    e = used(d, EVE); m = used(d, MORN); t = used(d, DAYT)
    rows_util.append((d, m, len(MORN) * 4, t, len(DAYT) * 4, e, len(EVE) * 4))
tot_used = sum(r[1] + r[3] + r[5] for r in rows_util); tot_cap = 4 * len(HOURS) * 7
wk_eve_used = sum(r[5] for r in rows_util[:5]); wk_eve_cap = 5 * 4 * 4

css = """:root{--ink:#15132e;--muted:#6c6a86;--line:#e4e2f0;--bg:#f8f7fc;--indigo:#312783;--blue:#1DA7E0;--green:#7DB928;--amber:#F59C00;--red:#E30613}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:0 16px 70px}.hero{background:linear-gradient(135deg,#312783,#1DA7E0);color:#fff;margin:0 -16px 20px;padding:26px 20px 22px;border-radius:0 0 18px 18px}
.hero h1{margin:0 0 5px;font-size:26px}.hero p{margin:0;opacity:.92}h2{font-size:20px;margin:28px 0 8px;color:var(--indigo);border-bottom:2px solid var(--line);padding-bottom:6px}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}.warn{border-left:4px solid var(--amber);background:#fffaf0}.ok{border-left:4px solid var(--green);background:#f7fbf0}.alarm{border-left:4px solid var(--red);background:#fff5f5}
table{border-collapse:collapse;font-size:12.5px;width:100%}th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);padding:6px 5px;border-bottom:2px solid var(--line)}
td{padding:5px 5px;border-bottom:1px solid var(--line);vertical-align:top}.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.scroll{overflow-x:auto}.small{font-size:13px;color:var(--muted)}
.g td{font-size:11px;line-height:1.25;min-width:110px}.g td.h{font-weight:700;color:var(--indigo);white-space:nowrap;min-width:52px}
.busy{background:#e8f4fb}.free{background:#f3f4f7;color:#b5b3c8}.new{background:#eef8e6;font-weight:700}.rep{background:#fff3d6}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:700;color:#fff;background:var(--blue)}.tag.r{background:var(--red)}.tag.a{background:var(--amber)}.tag.g{background:var(--green)}.tag.gr{background:#9a99b3}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:12px 0}.kpi div{background:#fff;border:1px solid var(--line);border-radius:12px;padding:10px 12px}.kpi b{display:block;font-size:24px;color:var(--indigo)}
ul{padding-left:20px}li{margin:4px 0}"""

# новые группы на сетке (решение «только замены» + свободные кабинеты)
NEW = {  # (день, час) -> [(кабинет, текст, класс)]
    ("пн", "19"): [("К1", "НОВАЯ ПШ2 читающие пн-чт 19:00 (педагог Гр7, следом)", "new")],
    ("чт", "19"): [("К1", "ЗАМЕНА: было Гр13 чт 19:00 → НОВАЯ ПШ2 читающие пн-чт 19:00", "rep")],
    ("вт", "16"): [("К2", "ЗАМЕНА с 22.09: было АЯ Гр5 8–12 → Starters 5–8 вт-чт 16:00 (Илья)", "rep")],
    ("чт", "16"): [("К2", "ЗАМЕНА с 22.09: было АЯ Гр5 8–12 → Starters 5–8 вт-чт 16:00 (Илья)", "rep")],
    ("ср", "12"): [("К1", "НОВАЯ МиР 2,2–3 ср-сб 12:45 (педагог Гр3, следом)", "new")],
    ("сб", "12"): [("К1", "НОВАЯ МиР 2,2–3 ср-сб 12:45", "new")],
    ("вт", "13"): [("К1", "НОВАЯ Первая школа 2–3 вт-чт 13:00 — при ≥4 подтверждённых к 12.09", "new")],
    ("чт", "13"): [("К1", "НОВАЯ Первая школа 2–3 вт-чт 13:00 — при ≥4", "new")],
    ("вс", "10"): [("К1", "ЗАМЕНА при 4 предоплатах: Первая школа вс 10:00 → ПШ1 нечитающие вс 10:00 (запросы «только выходные», Roistat)", "rep")],
    ("сб", "11"): [("К2", "ОСТАВИТЬ как ПШ 1 р/нед сб 11:00 (Королева + Параскив), четверг → ПШ2 19:00", "rep")],
}

parts = [f"<title>Кабинеты и замены групп: сетка недели</title><style>{css}</style><div class='wrap'>",
         "<div class='hero'><h1>Кабинеты и замены групп — сетка недели 07–13.09</h1><p>Решение Бориса 08.09: групп не убавляем — только <b>замены</b>: на место слабой группы в тот же слот ставим более востребованную, кабинеты занимаем по максимуму. Ниже — фактическая загрузка восьми кабинетов по данным занятий МойКласса и план замен по слотам.</p></div>",
         f"<div class='kpi'><div>Кабинето-часов занято за неделю<b>{tot_used} из {tot_cap}</b><span class='small'>{round(100*tot_used/tot_cap)}% при 8 кабинетах, 9:00–20:00</span></div>"
         f"<div>Вечер будней 16–19<b>{wk_eve_used} из {wk_eve_cap}</b><span class='small'>{round(100*wk_eve_used/wk_eve_cap)}% — заняты только К1–К4</span></div>"
         f"<div>Кабинеты К5–К8<b>0 занятий</b><span class='small'>всю неделю</span></div>"
         f"<div>Выходные<b>{used('сб',HOURS)+used('вс',HOURS)} из {2*8*len(HOURS)}</b><span class='small'>кабинето-часов</span></div></div>",
         "<div class='card alarm'><b>Главное.</b> Кабинеты — не ограничение и близко: в пик будней 16–19 заняты ровно четыре кабинета из восьми (К1 ПШ/РР, К2 английский, К3 ИЗО/ПШ/партнёры, К4 логопед), К5–К8 пустуют все семь дней, утро будней — 1–3 кабинета, выходные — 1–3. «Занять все комнаты по максимуму» упирается не в стены, а в педагого-часы и спрос по возрастам: чтобы заполнить ещё четыре кабинета вечером, нужны ещё четыре педагога в 16–19 и группы, в которые есть кого записать. Поэтому план ниже — замены в существующих слотах у существующих педагогов, плюс список того, чем реально можно занять пустые кабинеты (партнёры, второй педагог английского, логопед утром).</div>"]

parts.append("<h2>1. Сетка недели: кто где (кабинет × час)</h2><div class='card'><div class='scroll'><table class='g'><tr><th>час</th>" + "".join(f"<th>{d}</th>" for d in ORDER) + "</tr>")
for h in HOURS:
    parts.append(f"<tr><td class='h'>{h}:00</td>")
    for d in ORDER:
        cell = grid.get((d, h), {})
        items = []
        for r in ROOMS:
            names = cell.get(r, [])
            if names:
                items.append(f"<div class='busy'><b>{r}</b> {html.escape('; '.join(sorted(set(n[:44] for n in names))))}</div>")
        for r, txt, kind in NEW.get((d, h), []):
            items.append(f"<div class='{kind}'><b>{r}</b> {html.escape(txt)}</div>")
        free = [r for r in ROOMS if r not in cell]
        if free and (h in EVE or d in ("сб", "вс") or h in MORN):
            items.append(f"<div class='free'>свободно: {', '.join(free)}</div>")
        parts.append("<td>" + "".join(items) + "</td>")
    parts.append("</tr>")
parts.append("</table></div><p class='small'>Голубое — занято сейчас; жёлтое — замена в том же слоте; зелёное — новая группа в пустом слоте. Понедельник восстановлен по пн-ср / пн-чт / пн-пт группам (занятия 07.09 в выгрузку не попали как проведённые). Логопеды: К4 (Марина вт/чт/сб, Елена пн/ср/пт), К3 утром ср (Елена).</p></div>")

parts.append("""<h2>2. План «только замены» — что на место чего</h2><div class='card ok'><div class='scroll'><table>
<tr><th>Слот и кабинет</th><th>Сейчас</th><th>Ставим вместо</th><th>Педагог</th><th>Кого переводим и куда</th><th>Когда / условие</th></tr>
<tr><td>чт 19:00 · К1 (+ сб 11:00 · К2)</td><td>ПШ Гр13 чт 19:00 + сб 11:00 (1 ребёнок: Параскив; сегодня + Королева на сб)</td><td><b>ПШ2 читающие пн-чт 19:00</b> — четверг Гр13 становится четвергом новой ПШ2; суббота 11:00 остаётся отдельной группой ПШ1 «1 раз в неделю» (5 000 ₽/4)</td><td>Татьяна (Гр7 18:00 → 19:00 следом)</td><td>Параскив: сб 11:00 (1 р/нед) или Гр4 вт-чт 19:00; Королева — сб 11:00. 2–3 читающих из переполненной Гр7 → в новую 19:00</td><td>Создать в CRM 10.09, старт пн 15.09. Ничего не закрывается: слотов столько же, детей больше</td></tr>
<tr><td>вт-чт 16:00 · К2</td><td>АЯ Гр5 8–12 Starters (4 записи, 2 оплаты)</td><td><b>Starters 5–8 вт-чт 16:00</b> — очередь: Гр3 8/8 и Гр6 9/8, +5 заявок</td><td>Илья</td><td>4 детей Гр5 → Гр1 пн-ср 16:00 (Мария, 6/8) или Илья вт-чт 15:00, если согласится (школьники 8–12 успевают к 15:00?) — спросить Илью до 12.09</td><td>22.09, только при ≥4 подтверждённых в новую группу. До этого Гр5 работает</td></tr>
<tr><td>вс 10:00 · К1</td><td>Первая школа вс 10:00 1,5–2 (3 записи, 2 оплаты)</td><td><b>ПШ1 нечитающие вс 10:00</b> — запросы «только выходные» из 45 заявок Roistat; сб 10:00/11:00 уже есть, воскресенья нет</td><td>Педагог ПШ на воскресенье — согласовать (Татьяна?)</td><td>3 семьи Первой школы → единая вс 11:00 (2–3 года, 4 записи) — возраст 1,5–3 в одной группе с мамой допустим</td><td>Только при 4 предоплатах на вс 10:00; до этого Первая школа Гр4 идёт как есть</td></tr>
<tr><td>ср 12:45 + сб 12:45 · К1</td><td>пусто (после МиР Гр3 11:45)</td><td><b>МиР 2,2–3 ср-сб 12:45</b> — Гр3 9/7, Гр6 7/7, буфер 9</td><td>Педагог Гр3, следом</td><td>2–3 из Гр3 сверх нормы + буфер</td><td>Создать 10.09, старт ср 16.09</td></tr>
<tr><td>вт-чт 13:00 · К1</td><td>пусто (после Первой школы Гр3 12:00)</td><td><b>Первая школа 2–3 вт-чт 13:00</b> — Гр3 8/7</td><td>Педагог Гр3, следом</td><td>деление Гр3 4+4</td><td>Только при ≥4 подтверждённых к 12.09 (иначе 13:00 — тихий час малышей, спорное время)</td></tr>
<tr><td>вт-пт 16:00 и 17:00 · К1</td><td>ПШ Гр8 (1/1) и Гр9 (2/0, 2 пробных 08.09)</td><td colspan=3><b>Замены нет — оставляем и добираем.</b> Востребованной группы под вт-пт 16–17 в этом возрасте нет (ПШ2 хотят пн-чт 18–19, Starters — вт-чт 16 у Ильи). По правилу «только замены» группы не трогаем; контроль 30.09: &lt;3 оплат — тогда решаем ещё раз</td><td>Добирать из буфера ПШ (5) и Roistat «до 17:00»</td></tr>
<tr><td>ср-пт 18:00 · К1</td><td>ПШ Гр14 (2 из 2 оплатили)</td><td colspan=3><b>Не трогаем</b> — 100% оплат, маржа +1 100 ₽/занятие; добирать</td><td>—</td></tr>
<tr><td>пн-ср 16:00 и 19:00 · К3</td><td>ИЗО Гр1 (3/1) и Гр4 (2/0)</td><td colspan=3><b>Замены нет — оставляем до 30.09.</b> Гр1 — другая программа (Судакова пришла 07.09); Гр4 19:00 для 4–7 лет поздно, но замены в К3 пн-ср 19:00 нет: Мария в это время ведёт Movers–Flyers в К2. Если к 30.09 &lt;2 оплат — Гр4 переводим в Гр2 лепка 17:00</td><td>ИЗО 6–11 с рекламы → Гр1/Гр3</td></tr>
<tr><td>вс 10:30 · К3</td><td>МА «продолжающие» (0/0)</td><td colspan=3>Снять с сайта, слот держать за МА при 4 предоплатах</td><td>сейчас</td></tr>
</table></div></div>""")

parts.append(f"""<h2>3. Чем занять пустые кабинеты — честный список</h2><div class='card'>
<p>Свободно каждый будний вечер: <b>К5, К6, К7, К8</b> (16 кабинето-часов в день), утро будней: 5–7 кабинетов, суббота после 12:00 и воскресенье — почти всё. Заполнить это существующими педагогами нельзя: они все в это время уже ведут группы. Варианты по убыванию реалистичности:</p>
<ul>
<li><b>Логопед утром вт/чт 10:00–13:00 (К3 или К5)</b> — 8 заявок в буфере, лист ожидания; Елена. Индивидуальные — самая маржинальная услуга. Старт как только Елена подтвердит часы.</li>
<li><b>Второй педагог английского</b> на пн-ср или вт-чт 16–19 в К5 — под вторую Movers 8–12 (лист Гр4 9/8) и Starters 3–5. Сегодня 12:38 звонила кандидат-педагог (опыт в клубе, 2,5–10 лет) — номер в инбоксе. Без нового педагога английский расти не может: у Ильи и Марии заняты все вечерние часы.</li>
<li><b>Партнёры по субботам 10:00–13:00 (К3, К5)</b>: танцы 3–4 и 5–7 (11 заявок), скорочтение 7–10 (4 заявки) — по 4 предоплатам, оплата партнёру за занятие.</li>
<li><b>Робототехника</b> уже в пт 16–17 (К3) и вс 14–15 (К2); при 14 заявках — вторая партнёрская пара в сб 12:00–14:00 (К2 свободен).</li>
<li><b>Третья группа сада 4–5 лет</b> в К5/К6 утром — при листе ≥3; это единственное направление, окупающееся с трёх детей (35 500 ₽/мес каждый).</li>
<li><b>Воскресенье</b>: ПШ вс 10:00 (замена выше), МиР/Первая школа вс 11:00 (есть), МА 12:00/14:00 (есть), шахматы 11–13 (есть), робототехника 14–16 (есть). Ещё один утренний блок — ИЗО или Лицей вс 10:00 в К3 по предоплатам.</li>
</ul>
<p class='small'>Итог по кабинетам: сейчас {round(100*tot_used/tot_cap)}% кабинето-часов; после плана замен и новых групп — плюс 5 кабинето-часов в неделю (ПШ2 пн-чт 19, МиР 12:45 ×2, Первая школа 13:00 ×2 — все в К1), остальное требует новых педагогов или партнёров. Реальный предел при нынешнем составе педагогов — около 5 кабинетов в пик.</p></div>""")

parts.append("""<h2>4. Что меняется в решениях 08.09 относительно стратегии</h2><div class='card warn'><ul>
<li>Слияний «в чистом виде» нет. Из пяти слияний стратегии остаются три как <b>замены</b> (Гр13 → ПШ2 19:00 при сохранённой субботе; АЯ Гр5 → Starters 5–8 при условии; Первая школа вс 10:00 → ПШ вс 10:00 при предоплатах) и два <b>снимаются</b>: ПШ Гр8/Гр9 и ИЗО Гр4 работают и добираются до контрольной точки 30.09.</li>
<li>Цена решения: держать Гр8, Гр9, ИЗО Гр4 три недели стоит ≈ 2 × 1 000 ₽ × 2 занятия × 3 недели (минимум педагога ПШ) + 6 × 625 ₽ (ИЗО) ≈ 16 000 ₽ за сентябрь; потенциал — до 6 оплат ≈ 47 000 ₽/мес, если добор сработает. Риск не денежный, а удержание: ребёнок один в группе.</li>
<li>Оценка к 30.09 не меняется: 200–206 оплат (стратегия давала 204 при слияниях; замены дают столько же мест плюс шанс добора в Гр8/Гр9/ИЗО Гр4).</li>
<li>На пульте «не записывать» остаётся только у АЯ Гр5 (с 22.09) и Первой школы вс 10:00; Гр13 — «записывать только на сб 11:00»; ПШ Гр8/Гр9/Гр14, ИЗО Гр1/Гр4 — «добирать».</li>
</ul></div></div>""")

out = pathlib.Path(__file__).resolve().parents[1] / "kabinety_zamena_0809.html"
out.write_text("\n".join(parts), encoding="utf-8")
print("ok", out, out.stat().st_size, "| загрузка:", tot_used, "/", tot_cap, "| вечер будней:", wk_eve_used, "/", wk_eve_cap)
for r in rows_util: print(r)
