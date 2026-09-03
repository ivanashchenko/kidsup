# -*- coding: utf-8 -*-
"""План на четверг 03.09 — из стратегии итога недели открытых уроков.
Лена — дожим и оплаты, Аня — встреча, проводы и окно реактивации 13:00–16:00."""
import json, re, html as H, collections
BASE = "/home/user/kidsup/docs/rabota"
I = json.load(open(f"{BASE}/itog_3dnya.json"))
Dn = json.load(open(f"{BASE}/den_0309.json"))
U = {int(k): v for k, v in I["users"].items()}
CSS = re.search(r"<style>.*?</style>", open("/home/user/kidsup/docs/plan_02sen.html", encoding="utf-8").read(), re.S).group(0)
esc = lambda x: H.escape(str(x or ""))
short = lambda g: re.sub(r"^2627_", "", g or "")

# --- воронка трёх дней (свежая) ------------------------------------------
trial = [r for r in I["recs"] if r["test"]]
by = {}
for r in sorted(trial, key=lambda r: (r["day"], r["time"])):
    t = by.setdefault(r["uid"], {"day": r["day"], "time": r["time"], "subj": set(), "visit": False})
    t["subj"].add(r["subject"]); t["visit"] |= r["visit"]
came = lambda u: by[u]["visit"] or bool(U[u]["paid"])
paid = lambda u: bool(U[u]["paid"])
N = len(by); C = sum(1 for u in by if came(u)); P = sum(1 for u in by if paid(u))
hot = [u for u in by if came(u) and not paid(u) and not U[u]["next"]]          # пришли, не купили, записи нет
warm = [u for u in by if came(u) and not paid(u) and U[u]["next"]]              # пришли, не купили, записаны дальше
lost = [u for u in by if not came(u) and not U[u]["next"]]                      # не дошли, записи нет
unmarked = sum(1 for u in by if not by[u]["visit"] and not paid(u))

# --- сегодня -------------------------------------------------------------
L = Dn["lessons"]
n_kids = sum(len(l["kids"]) for l in L); trials_today = [(l["time"], l["group"], k) for l in L for k in l["kids"] if k["test"]]
first_uniq = {(k["name"], k["phone"]) for _, _, k in trials_today}

# --- реактивация: приоритет ---------------------------------------------
R = Dn["react"]
def prio(x):
    a = x["age"] or 0
    s1 = 0 if x["state"] == 146950 else 1                       # «думает» раньше «недозвона»
    s2 = 0 if 4 <= a <= 7 else (1 if 1.3 <= a <= 4 else 2)      # возраст ПШ → полупустые группы
    return (s2, s1, x["name"] or "")
R40 = sorted([x for x in R if x["phone"]], key=prio)[:40]

h = [CSS, "<div class='wrap'>",
     "<div class='hero'><h1>Четверг 3 сентября — по стратегии</h1>"
     "<p>Работаем 9:00–20:00 · Лена — дожим и оплаты · Аня — встреча, проводы и окно реактивации 13:00–16:00 · Лиза — переписка<br>"
     f"Сегодня {sum(1 for l in L if l['kids'])} групп, {n_kids} детей, из них {len(first_uniq)} на первом занятии</p></div>"]

h.append("<div class='card alarm'><b>Три вещи, которые нельзя упустить сегодня.</b> "
         "1) <b>Сорокина Полина</b> (79055389799) приходит с младшим на подготовку и планирует оплатить оба направления сразу — посчитать со скидкой 10% на второго ребёнка заранее, английский вт-чт 16:00 с 8.09 оформить. "
         "2) <b>Софья, 2 г. 5 мес.</b> (мама Юлия, 79104255162) — первое занятие в 12:00, раннее развитие с Ириной; записал я вчера сам, админы её не видели. "
         "3) <b>Козлова Ангелина</b> (79687111684) со вчерашних 13:24 ждёт ответа: «есть ли группы обучения чтению?» — это ПШ2 читающие, и это мама двоих детей.</div>")

# метрика
h.append("<h2>Где мы после трёх дней</h2>")
h.append("<div class='kpi'>"
         f"<div class='k'><div class='v'>{N}</div><div class='l'>записаны на пробное 31.08–02.09</div></div>"
         f"<div class='k'><div class='v good'>{round(100*C/N)}%</div><div class='l'>дошли: {C}. Норма — выше 50%</div></div>"
         f"<div class='k'><div class='v good'>{round(100*P/C)}%</div><div class='l'>купили из дошедших: {P}. Порог — 40%</div></div>"
         f"<div class='k'><div class='v'>{len(hot)+len(warm)}</div><div class='l'>дошли и не купили — дожим Лены</div></div>"
         f"<div class='k'><div class='v'>{len(lost)}</div><div class='l'>не дошли, записи нет — Аня</div></div>"
         f"<div class='k'><div class='v'>{unmarked}</div><div class='l'>записей без отметки явки</div></div>"
         "</div>")
h.append("<div class='card'><a href='/base/itog_3dnya'>Полный итог трёх дней и стратегия на сентябрь</a> — там же заполненность всех групп.</div>")

# роли
h.append("<h2>Кто что делает</h2><div class='who'>")
h.append("<div class='wcard lena'><div class='nm'>Лена — только дожим и оплаты</div><div class='rl'>Пункт 1 стратегии: довести и закрыть тех, кто уже в воронке. Один дожим = 600 ₽ бонуса</div>"
         "<div class='tl'>"
         "<b class='t'>9:00–11:00</b><span>Кто ждёт ответа со вчера: Козлова (чтение — ПШ2), Цуцкова (не успевает к 16:00 — предложить 18:00 или субботу), Алимов («перезапишите» — назвать дату и записать). Расчёт для Сорокиной.</span>"
         f"<b class='t'>11:00–13:00</b><span>Подтвердить {len(first_uniq)} детей на первом занятии сегодня — список ниже. В сообщении: −10% в день первого занятия, оплата на стойке.</span>"
         f"<b class='t'>13:00–16:00</b><span>Дожим по единой форме — {len(hot)} семей «пришли, не купили» (звонок с наблюдением педагога, скидка до конца недели) и {len(warm)} «записаны дальше» (подтвердить дату, на занятии закрывать).</span>"
         "<b class='t'>16:00–20:00</b><span><b>Только разговоры с родителями на выходе.</b> Девять полей по каждому, оплата здесь и сейчас, ответ семьи — в CRM.</span>"
         "</div></div>")
h.append("<div class='wcard anya'><div class='nm'>Аня — встреча, проводы и реактивация</div><div class='rl'>Пункт 2 стратегии: вернуть тех, кто уже платил. Телефон и дверь — её</div>"
         "<div class='tl'>"
         "<b class='t'>9:00–13:00</b><span>Проверить отметки явки за вчерашний вечер (18:00–19:00) — шестеро числятся «не дошли», часть просто не отметили. Хвосты: Рахманов — оформить перенос на пн (лепка 17:00 + пробное 18:00); Попов Артём — развести однофамильцев; запись 79150830157 на МА снять (семья не в Москве).</span>"
         "<b class='t'>13:00–16:00</b><span><b>Окно реактивации — 40 семей, которые платили нам раньше</b> (список ниже, отсортирован: сначала возраст 4–7 под полупустые группы подготовки, сначала «думает»). Не продавать центр — назначать день. Недозвон закрывать задачей, догон шлёт автопилот.</span>"
         f"<b class='t'>16:00–20:00</b><span>Встречает, провожает, <b>отмечает явку сразу после занятия</b>. Родителей первого занятия подводит к Лене. Не дошедших вчера — {len(lost)} — звонит в паузах: одно предложение с днём и временем.</span>"
         "</div></div>")
h.append("<div class='wcard' style='border-top-color:var(--blue)'><div class='nm'>Лиза — переписка</div><div class='rl'>Час днём, 15 минут с 16:00 до 20:00</div>"
         "<ol class='small'><li>Владимиру (Каранзин, 79998569433) — обещали стоимость раннего развития в WhatsApp: 7 800 за 8, 5 000 за 4, −10% в день первого занятия</li>"
         "<li>Сеничева (79265516487) — держать в заявках на танцы, ответить, как только будет расписание</li>"
         "<li>Никому не писать «это автоматическая рассылка, не обращайте внимания». Отвечать по существу: «да, это наше напоминание, ваша запись в силе»</li></ol></div>")
h.append("</div>")

# сегодня на первом занятии
h.append(f"<h2>Сегодня на первом занятии — {len(first_uniq)}</h2>")
h.append("<div class='q'>«Доброе утро! Это KidsUP. Сегодня в ЧЧ:ММ ждём [имя] на первое занятие — оно условно-бесплатное и с диагностикой. Бульвар Рокоссовского 6 к1В, 7-й подъезд, домофон 12, 2 этаж, из лифта налево, код 667788#. Ориентир — магазин «Дикси», от него по лестнице наверх. Приходите за 10 минут. В день первого занятия −10% на абонемент, оплатить можно на стойке. Будете?»</div>")
h.append("<div class='scroll'><table><tr><th></th><th>Время</th><th>Группа</th><th>Ребёнок</th><th>Телефон</th></tr>")
seen = set()
for tm, g, k in trials_today:
    if (k["name"], tm) in seen: continue
    seen.add((k["name"], tm))
    h.append(f"<tr><td><span class='chk'></span></td><td><b>{esc(tm)}</b></td><td class='small'>{esc(short(g))}</td><td><b>{esc(k['name'])}</b></td><td class='ph'>{esc(k['phone'])}</td></tr>")
h.append("</table></div>")

# дожим Лены
def trow(u):
    t = by[u]; x = U[u]
    return (f"<tr><td><span class='chk'></span></td><td>{t['day'][5:]} {t['time']}</td><td><b>{esc(x['name'])}</b></td>"
            f"<td class='ph'>{esc(x['phone'])}</td><td class='small'>{esc('/'.join(sorted(t['subj'])))}</td></tr>")
h.append(f"<h2>Дожим Лены: пришли и не купили — {len(hot)}</h2>")
h.append("<div class='card warn'><b>Перед звонком — у педагога:</b> реакция ребёнка и рекомендация. Это пункты 5 и 6 единой формы, без них звонок превращается в «ну что решили».</div>")
h.append("<div class='scroll'><table><tr><th></th><th>Были</th><th>Ребёнок</th><th>Телефон</th><th>Направление</th></tr>" + "".join(trow(u) for u in hot) + "</table></div>")
h.append("<div class='q'>«Здравствуйте! Это Лена из KidsUP. [Имя] был у нас на первом занятии — педагог отметил, что … Мы держим место в группе до [день]; закрепить абонементом? Скидка 10% действует до конца недели, оплатить можно по ссылке или на стойке».</div>")
h.append(f"<h3>Записаны дальше, оплаты нет — {len(warm)}</h3><div class='card'><span class='small'>" +
         " · ".join(f"<b>{esc(U[u]['name'])}</b> {esc(U[u]['phone'])} → {esc(U[u]['next'])}" for u in warm) +
         ". Подтвердить дату, предупредить про −10% в день занятия, на занятии закрывать.</span></div>")

# реактивация Ани
h.append(f"<h2>Окно реактивации 13:00–16:00 — {len(R40)} семей, которые платили нам раньше</h2>")
h.append("<div class='card'><span class='small'>Из 206 бывших плательщиков в статусах «недозвон» и «думает». Отбор: сначала дети 4–7 лет — под полупустые группы подготовки к школе (15 групп, заполнены на 48%), внутри — сначала «думает». "
         "Скрипт: «Здравствуйте, это Аня из KidsUP. [Имя] ходил к нам в прошлом году на …; в этом году группы стартовали, для его возраста есть … в … Записать на первое занятие на этой неделе — четверг 18:00 или суббота 11:00?» Недозвон — закрыть задачу «недозвон», WhatsApp и СМС уйдут сами.</span></div>")
h.append("<div class='scroll'><table><tr><th></th><th>Ребёнок</th><th>Возраст</th><th>Телефон</th><th>Статус</th><th>Куда звать</th></tr>")
ST = {345768: "недозвон", 146950: "думает"}
for x in R40:
    a = x["age"]
    where = "подготовка к школе / английский" if a and 4 <= a <= 7 else ("раннее развитие / мини-сад" if a and a < 4 else ("английский / скорочтение" if a and a > 7 else "уточнить возраст"))
    h.append(f"<tr><td><span class='chk'></span></td><td><b>{esc(x['name'])}</b></td><td>{esc(a if a is not None else '—')}</td><td class='ph'>{esc(x['phone'])}</td><td><span class='pill p-gray'>{ST.get(x['state'], '')}</span></td><td class='small'>{where}</td></tr>")
h.append("</table></div>")

# не дошли
h.append(f"<h2>Не дошли на пробное и никуда не записаны — {len(lost)}</h2>")
h.append("<div class='card'><span class='small'>Аня, в паузах вечером или после окна. Сначала — сверить отметку явки: шестеро из вчерашних 18:00–19:00 могли просто остаться без отметки. Остальным — одно конкретное предложение: открытый урок на этой неделе, диагностика или экскурсия.</span></div>")
h.append("<div class='scroll'><table><tr><th></th><th>Были записаны</th><th>Ребёнок</th><th>Телефон</th><th>Направление</th></tr>" + "".join(trow(u) for u in lost) + "</table></div>")

# расписание
h.append(f"<h2>Кто идёт сегодня — {sum(1 for l in L if l['kids'])} групп, {n_kids} детей</h2>")
h.append("<div class='scroll'><table><tr><th>Время</th><th>Группа</th><th class='num'>Детей</th><th>Кого ждём (★ — первое занятие)</th></tr>")
for l in L:
    if not l["kids"]: continue
    kids = ", ".join(("★ " if k["test"] else "") + esc(k["name"]) for k in l["kids"])
    pill = "p-amber" if any(k["test"] for k in l["kids"]) else "p-gray"
    h.append(f"<tr><td><b>{esc(l['time'])}</b></td><td class='small'>{esc(short(l['group']))}</td><td class='num'><span class='pill {pill}'>{len(l['kids'])}</span></td><td class='kids'>{kids}</td></tr>")
h.append("</table></div>")

h.append("<h2>Итог дня — записать в 20:00</h2><div class='card'><ul class='small'>"
         "<li>Первое занятие: сколько было · оплатили на месте · назвали день · отказы и почему</li>"
         "<li>Реактивация: сколько наборов из 40 · дозвонились · записали на пробное</li>"
         "<li>Дожим Лены: из 5 «пришли, не купили» — сколько оплатили</li>"
         "<li>Отметка явки стоит у всех сегодняшних занятий — да/нет</li></ul></div>")
h.append("</div>")
open("/home/user/kidsup/docs/plan_03sen.html", "w", encoding="utf-8").write("".join(h))
print("plan_03sen.html", len("".join(h)), "байт | первых", len(first_uniq), "| дожим", len(hot), "+", len(warm), "| не дошли", len(lost), "| реактивация", len(R40))
