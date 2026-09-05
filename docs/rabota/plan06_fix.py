# -*- coding: utf-8 -*-
"""Коррекция плана на воскресенье 06.09 по фактам субботы: звонки Манго по добавочным,
покрытие списков плана, оплаты и записи в CRM, явка, инбокс, чаты без ответа."""
import re, json, html as H, collections, urllib.request, base64
B = "/home/user/kidsup"
esc = lambda x: H.escape(str(x or ""))
p05 = open(f"{B}/docs/plan_05sen.html", encoding="utf-8").read()
p06 = open(f"{B}/docs/plan_06sen.html", encoding="utf-8").read()
CSS = re.search(r"<style>.*?</style>", p05, re.S).group(0).replace("</style>", ".w-i{background:#E30613}.pill{white-space:nowrap}.fact td{font-size:14px}</style>")
A_ = "<span class='w w-a'>Аня</span>"; I_ = "<span class='w w-i'>Ира</span>"; B_ = "<span class='w w-b'>Борис</span>"; L_ = "<span class='w w-l'>Лиза</span>"

def sections(s):
    body = s.split("</style>", 1)[1]
    out = collections.OrderedDict()
    for sec in re.split(r"(?=<h2)", body):
        m = re.match(r"<h2[^>]*>(.*?)</h2>", sec, re.S)
        if m: out[H.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()] = sec
    return out
S05, S06 = sections(p05), sections(p06)
def rows(sec): return [r for r in re.findall(r"<tr>.*?</tr>", sec, re.S) if re.search(r"7\d{10}", r)]
def phone(r): return re.search(r"(7\d{10})", r).group(1)[-10:]
def header(sec): return re.search(r"<tr>(?:(?!7\d{10}).)*?<th.*?</tr>", sec, re.S).group(0)

calls = json.load(open(f"{B}/docs/rabota/calls_0509.json"))
out_calls = [c for c in calls if c["dir"] == "out" and "sip:user1" not in c["ext"]]
dialed = collections.defaultdict(list)
for c in out_calls: dialed[c["phone"][-10:]].append(c)
talked = {p for p, v in dialed.items() if max(x["dur"] for x in v) >= 30}
lessons = json.load(open(f"{B}/docs/rabota/lessons_0609.json"))
pays = json.load(open(f"{B}/docs/rabota/pays_0509.json"))
crm = json.load(open(f"{B}/docs/rabota/crm_0509.json"))
req = urllib.request.Request("https://app.kidsup.ru/api/plan/inbox?day=2026-09-05", headers={"Authorization": "Basic " + base64.b64encode(b"admin:CGWstart8*").decode()})
ib = json.load(urllib.request.urlopen(req, timeout=30)); ib = ib if isinstance(ib, list) else ib.get("items", [])
open_ib = [x for x in ib if not x.get("done") and x.get("who") in ("Аня", "Ира", "Лиза")]

by_ext = {}
for e in ("15", "12"):
    g = [c for c in out_calls if c["ext"] == e]
    by_ext[e] = {"n": len(g), "talks": sum(1 for c in g if c["dur"] >= 30), "min": sum(c["dur"] for c in g if c["dur"] >= 30) // 60,
                 "first": min(c["t"][11:16] for c in g), "last": max(c["t"][11:16] for c in g)}
hot_rows = rows(S05["Дожим Ани — 16 «были и не оплатили» Аня"]); base_rows = rows(S05["База — семьи 81–160 из бывших плательщиков (80) Ира"])
def split(rs):
    nd = [r for r in rs if phone(r) not in dialed]; nt = [r for r in rs if phone(r) in dialed and phone(r) not in talked]; t = [r for r in rs if phone(r) in talked]
    return nd, nt, t
h_nd, h_nt, h_t = split(hot_rows); b_nd, b_nt, b_t = split(base_rows)
paid_sum = f"{sum(v[2] for v in pays.values()):,}".replace(",", " ")

h = [CSS, "<div class='wrap'>",
     "<div class='hero'><h1>Воскресенье 6 сентября — Аня и Ира, 10:00–18:00</h1>"
     "<p>План скорректирован по фактам субботы. Сначала порядок (инбокс, явка, чаты) — до 12:00, потом телефон без пауз. "
     f"Занятий сегодня {len(lessons)}, детей {sum(len(l['kids']) for l in lessons)}, первых занятий {sum(1 for l in lessons for k in l['kids'] if k['test'])}.</p></div>"]

# --- факты субботы -----------------------------------------------------------
h.append("<div class='card alarm'><b>Суббота по фактам — план выполнен примерно на треть.</b>"
         "<div class='scroll'><table class='fact'><tr><th>Показатель</th><th>План</th><th>Факт</th><th>Комментарий</th></tr>"
         f"<tr><td>Наборов с ноутбука (доб. 15)</td><td class='num'>80</td><td class='num'>{by_ext['15']['n']}</td><td class='small'>первый звонок {by_ext['15']['first']}, последний {by_ext['15']['last']}; разговоров ≥30 с — {by_ext['15']['talks']}, {by_ext['15']['min']} мин</td></tr>"
         f"<tr><td>Наборов с трубки (доб. 12)</td><td class='num'>80</td><td class='num'>{by_ext['12']['n']}</td><td class='small'>первый звонок {by_ext['12']['first']}, последний {by_ext['12']['last']}; разговоров ≥30 с — {by_ext['12']['talks']}, {by_ext['12']['min']} мин</td></tr>"
         f"<tr><td>Дожим «записаны и не оплатили»</td><td class='num'>{len(hot_rows)} семей</td><td class='num'>{len(hot_rows)-len(h_nd)} набрано, {len(h_t)} разговоров</td><td class='small'>{len(h_nd)} семей не набирали вовсе; список набирал ноутбук, а не трубка</td></tr>"
         f"<tr><td>База 81–160</td><td class='num'>{len(base_rows)}</td><td class='num'>{len(base_rows)-len(b_nd)} набрано, {len(b_t)} разговоров</td><td class='small'>{len(b_nd)} не набраны, {len(b_nt)} недозвон — догон ушёл автоматически</td></tr>"
         f"<tr><td>Оплаты</td><td class='num'>10 · 80 000 ₽</td><td class='num'>{len(pays)} · {paid_sum} ₽</td><td class='small'>почти всё — разовые оплаты логопеда и абонементы РР у тех, кто пришёл на занятие (Карюков 7 560, Колесникова, Спицын, Слатин по 5 200). Из списка дожима по телефону — только Панкратова 2 000</td></tr>"
         f"<tr><td>Явка в CRM</td><td class='num'>26 отметок</td><td class='num'>0</td><td class='small'>ни одна из 26 записей субботы не отмечена «пришёл/пропуск» — пробные Ситковского, Гунта и Анисенковой без визита, дожим после пробного не запустится</td></tr>"
         f"<tr><td>Инбокс закрыт к 18:00</td><td class='num'>все</td><td class='num'>{len([x for x in ib if x.get('done')])} из {len(ib)}</td><td class='small'>{len(open_ib)} пунктов открыты, часть — обещания клиентам «пришлём ссылку сейчас»</td></tr>"
         "<tr><td>Чаты без ответа</td><td class='num'>0</td><td class='num'>3</td><td class='small'>Карюков «Оплатила, спасибо» (13:08), 79137115595 «Да, напишите» (13:31), 1325431252 «Да, пожалуйста» на перенос (10:38)</td></tr>"
         "<tr><td>Начало обзвона</td><td class='num'>10:15</td><td class='num'>11:21 и 11:59</td><td class='small'>первые полтора-два часа смены телефон молчал; в 13:30–14:00 обе на паузе; в 14–15 ч трубка молчала</td></tr>"
         "</table></div>"
         "<p class='small'>Что было хорошо: 22 разговора по базе с конкретными датами (Шевякова вт 12:00, Ефимов вт 19:00, Офицерова вт 19:20), четыре семьи оплатили на выходе, Карюкову дали обратную связь педагога в день пробного. "
         "Записи в CRM оформлялись сразу (13 записей за день, 8 с флажком «пробное»).</p></div>")

# --- расписание --------------------------------------------------------------
h.append("<h2>Расписание воскресенья — кто приходит и что сделать у двери</h2><div class='scroll'><table><tr><th>Время</th><th>Группа</th><th>Дети</th><th>Что сделать</th></tr>")
TASK = {"10:00": "Каранзин — первое занятие, флажка «пробное» нет: поставить до 10:00. Нуралимова подтверждена. После занятия — обратная связь Ирины и оплата на стойке (−10% сегодня).",
        "11:00 РР": "Спицын оплачен вчера. Явку отметить сразу.",
        "11:00 ШАХ": "Гасанова не подтверждена («я напишу») — не ждать без сообщения. Кузина отменила (просит пт 19:00). Прокопович, Боброва, Гунт — пробные: после занятия оплата абонемента 7 000 ₽ на стойке. Педагог — Владимир Лучко (EduChess).",
        "12:00": "Прокопович Владимир — пробное, начинающие. Если один — занятие всё равно проводим.",
        "12:30": "Осетрова — пробное: встретить, после занятия оплата 8 600 ₽ (−10% сегодня). Дарина Габдуллина ведёт первый день — познакомить родителей с педагогом.",
        "14:00": "Артамонов не оплатил — оплата на выходе. Карточка «Аня» — узнать фамилию ребёнка и переименовать до занятия. Загородный — пробное."}
for l in lessons:
    key = l["time"] if l["time"] not in ("11:00",) else ("11:00 ШАХ" if "ШАХ" in l["group"] else "11:00 РР")
    kids = ", ".join(f"<b>{esc(k['name'])}</b>" + (" <span class='pill p-blue'>первое</span>" if k["test"] else "") + ("" if k["paid"] else " <span class='pill p-amber'>не опл.</span>") for k in l["kids"])
    h.append(f"<tr><td class='ph'><b>{l['time']}</b></td><td class='small'>{esc(l['group'][:48])}</td><td class='kids'>{kids}</td><td class='small'>{TASK.get(key,'')}</td></tr>")
h.append("</table></div>")

# --- порядок до 12:00 ---------------------------------------------------------
h.append(f"<h2>Сначала порядок — до 12:00 {I_} {A_}</h2><div class='card warn'><ol>"
         f"<li>{I_} <b>Явка за субботу</b>: 26 записей без отметки — открыть 12 занятий субботы и проставить «пришёл/пропуск». Пробные: Ситковский 13:20, Гунт 14:10, Анисенкова 10:00 — обязательно, иначе не будет дожима после пробного.</li>"
         f"<li>{A_} <b>Три чата без ответа со вчера</b>: Карюков Феликс (79162677703) — «спасибо, ждём в понедельник 19:00»; 79137115595 — написать про «Музыку с мамой» и «Первую школу» и позвонить после 11:00; 1325431252 — подтвердить перенос пробного РР на сб 12.09 10:45 и оформить запись.</li>"
         f"<li>{A_} <b>Флажки «пробное»</b>: Каранзин (сегодня 10:00), Русанов Максим (три записи), Офицерова Майя (вт 19:20). Без флажка не уходит напоминание и подтверждение.</li>"
         f"<li>{A_} <b>Инбокс субботы — {len(open_ib)} открытых пунктов</b> (ниже). Сначала обещания клиентам: ссылки на оплату Нистратовой и Раевскому, информация Орловой и Офицеровой, Лукьянец/Червоный по робототехнике. Сделанное — «готово», несделанное — причина.</li>"
         f"<li>{I_} <b>Явку сегодня</b> отмечать сразу после каждого занятия, не вечером.</li></ol>"
         "<details><summary class='small'>Открытые пункты инбокса субботы</summary><ul class='small'>" +
         "".join(f"<li><b>{esc(x.get('who'))}</b> · {esc(x.get('source') or '')} — {esc(x['text'][:180])}</li>" for x in open_ib) + "</ul></details></div>")

# --- Борису -----------------------------------------------------------------
h.append(f"<div class='card'><b>{B_} Решения и настройки</b><ul class='small'>"
         "<li>Шахматы: идёт ли расписание пт 18/19 + вс 11/12 с этой недели. Сегодня 11:00 ждём троих (Прокопович, Боброва, Гунт), 12:00 — одного. Кузина и Русанов просят пятницу 19:00.</li>"
         "<li>Чёрный список Манго: 79044500230 (231 звонок за утро, перепутал нас с приложением KidsApp) и 79099301750 (110 звонков 18:15–18:51 после закрытия). Оба забивают статистику пропущенных.</li>"
         "<li>Выключить авто-уведомления МойКласса клиентам (уходят в 8:01 и 8:45 в выходной, родители считают их нашими).</li>"
         "<li>Робототехника: группы в CRM так и не созданы — записать Горина (вс 13.09 13:00), Бахтимирова (пт 11.09 17:00) и лид 79264467809 (пт 16:00) некуда.</li></ul></div>")

# --- дожим Аня ----------------------------------------------------------------
pill = lambda t, c: f"<span class='pill {c}'>{t}</span>"
def mark(r, p): return r.replace("</tr>", f"<td>{p}</td></tr>", 1)
hdr = header(S05["Дожим Ани — 16 «были и не оплатили» Аня"]).replace("</tr>", "<th>Вчера</th></tr>")
h.append(f"<h2>Дожим «записаны и не оплатили» — остаток {len(h_nd)+len(h_nt)} семей {A_}</h2>"
         "<div class='q'>«Здравствуйте, это Аня из KidsUP. [Имя] записан(а) к нам на [группа], занятия уже идут. Мы держим место до понедельника — закрепим абонементом? Ссылку на оплату пришлю сейчас в WhatsApp, при оплате сегодня −10%». Ссылка уходит в этом же разговоре, не «потом».</div>"
         "<div class='card ok'><b>Норма на день: 60 наборов, 25 разговоров, 8 оплат.</b> Звонки с 12:00 до 17:30 без пауз длиннее 15 минут; телефон у Ани — трубка доб. 12, а не ноутбук. В 15:00–17:00 — подтверждение первых занятий понедельника (список ниже).</div>"
         f"<div class='scroll'><table>{hdr}" + "".join(mark(r, pill("не набирали", "p-red")) for r in h_nd) + "".join(mark(r, pill("недозвон", "p-amber")) for r in h_nt) + "</table></div>")
# --- первые занятия понедельника (из старого плана) -------------------------
mon = S06["Первые занятия понедельника — подтвердить голосом сегодня 15:00–17:00 Аня"]
h.append(mon)
# --- база Ира ---------------------------------------------------------------
hdrb = header(S05["База — семьи 81–160 из бывших плательщиков (80) Ира"]).replace("</tr>", "<th>Вчера</th></tr>")
h.append(f"<h2>База — сначала недобранные из вчерашних ({len(b_nd)+len(b_nt)}), затем семьи 161–206 {I_}</h2>"
         "<div class='card ok'><b>Норма на день: 70 наборов, 30 разговоров, 8 записей на первое занятие.</b> Два варианта времени в конкретную полупустую группу, не «хотите ли». Каждое «да» — сразу запись в CRM с флажком «пробное» и комментарий. Недозвон — комментарий одной строкой, догон шлёт автопилот; второй раз в тот же день не набирать.</div>"
         f"<div class='scroll'><table>{hdrb}" + "".join(mark(r, pill("не набирали", "p-red")) for r in b_nd) + "".join(mark(r, pill("недозвон", "p-amber")) for r in b_nt) + "</table></div>")
h.append(S06["База — семьи 161–206 из бывших плательщиков (46) Ира"].replace("<h2>", "<h3>", 1).replace("</h2>", "</h3>", 1))
h.append(S06["Заявки из буферов — 75 семей, экскурсии и первые занятия Ира"])
h.append(S06["Куда зовём — 11 групп с 0–2 детьми"])
rules = S06["Правила выходных"].replace("<ul class='small'>", "<ul class='small'><li><b>Телефон включается в 10:15</b>, не в 12:00. Две паузы по 15 минут, обед 30 минут по очереди, чтобы линия не молчала.</li><li><b>Явка отмечается в CRM сразу после занятия.</b> Вечером — проверка: все записи дня с отметкой.</li><li><b>Обещал «пришлю сейчас» — шлёшь до следующего звонка.</b></li>", 1)
h.append(rules)
h.append(S06["Итог дня — в инбокс в 18:00"])
h.append("</div>")
open(f"{B}/docs/plan_06sen.html", "w", encoding="utf-8").write("\n".join(h))
print("ok", len("\n".join(h)), "| дожим остаток", len(h_nd), len(h_nt), "| база остаток", len(b_nd), len(b_nt), "| инбокс открыт", len(open_ib))
