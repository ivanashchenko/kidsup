# -*- coding: utf-8 -*-
"""Итог недели открытых уроков 31.08–02.09 и стратегия набора на сентябрь."""
import json, re, html as H, collections
BASE = "/home/user/kidsup/docs/rabota"
D = json.load(open(f"{BASE}/itog_3dnya.json"))
U = {int(k): v for k, v in D["users"].items()}
pays = {int(k): v for k, v in D["pays"].items()}
G = D["groups"]
CSS = re.search(r"<style>.*?</style>", open("/home/user/kidsup/docs/plan_01sen.html", encoding="utf-8").read(), re.S).group(0)
CSS = CSS.replace("</style>", """
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:14px 0}
.k{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
.k .v{font-size:32px;font-weight:800;line-height:1;color:var(--indigo)}.k .v.good{color:var(--green)}.k .v.bad{color:var(--red)}
.k .l{font-size:13px;color:var(--muted);margin-top:4px}
.bar{height:10px;background:var(--line);border-radius:6px;overflow:hidden}.bar i{display:block;height:100%;background:var(--green)}
.bar i.mid{background:var(--amber)}.bar i.low{background:var(--red)}
.strat{counter-reset:s}.strat .card{border-left:4px solid var(--indigo);margin:12px 0}
.strat .card b.h{display:block;font-size:17px;margin-bottom:6px}
.strat .money{display:inline-block;background:#eef7e0;color:#3e6410;border-radius:8px;padding:2px 10px;font-weight:700;font-size:13px;margin-left:6px}
</style>""")
esc = lambda x: H.escape(str(x or ""))

# --- воронка -------------------------------------------------------------
trial = [r for r in D["recs"] if r["test"]]
by = {}
for r in sorted(trial, key=lambda r: (r["day"], r["time"])):
    t = by.setdefault(r["uid"], {"day": r["day"], "time": r["time"], "subj": set(), "visit": False, "skip": False})
    t["subj"].add(r["subject"]); t["visit"] |= r["visit"]; t["skip"] |= r["skip"]
def came(uid): return by[uid]["visit"] or bool(U[uid]["paid"])
def paid(uid): return bool(U[uid]["paid"])
N = len(by); C = sum(1 for u in by if came(u)); P = sum(1 for u in by if paid(u))
no_show_lost = [u for u in by if not came(u) and not U[u]["next"]]
no_show_re = [u for u in by if not came(u) and U[u]["next"]]
came_nopay_lost = [u for u in by if came(u) and not paid(u) and not U[u]["next"]]
came_nopay_re = [u for u in by if came(u) and not paid(u) and U[u]["next"]]
unmarked = sum(1 for u in by if not by[u]["visit"] and not by[u]["skip"] and not paid(u))
money_trial = sum(x["summa"] for u in by if paid(u) for x in U[u]["paid"])
money_all = sum(x["summa"] for v in pays.values() for x in v)
avg = money_trial / max(1, P)

h = [CSS, "<div class='wrap'>",
     "<div class='hero'><h1>Неделя открытых уроков: три дня в цифрах</h1>"
     "<p>31 августа — 2 сентября · записались → пришли → купили · заполненность групп · что делать в сентябре</p></div>"]

h.append("<h2>Воронка пробных занятий</h2>")
h.append("<div class='kpi'>"
         f"<div class='k'><div class='v'>{N}</div><div class='l'>детей записаны на пробное</div></div>"
         f"<div class='k'><div class='v good'>{round(100*C/N)}%</div><div class='l'>доходимость: пришли {C} из {N}. Норма — выше 50%</div></div>"
         f"<div class='k'><div class='v good'>{round(100*P/C)}%</div><div class='l'>купили из пришедших: {P} из {C}. Порог — 40%</div></div>"
         f"<div class='k'><div class='v'>{int(avg):,}</div><div class='l'>₽ средний первый чек</div></div>".replace(",", " ") +
         f"<div class='k'><div class='v bad'>{len(no_show_lost)}</div><div class='l'>не дошли и никуда не перезаписаны</div></div>"
         f"<div class='k'><div class='v bad'>{len(came_nopay_lost)}</div><div class='l'>пришли, не купили, записи дальше нет</div></div>"
         "</div>")
h.append(f"<div class='card warn'><b>{unmarked} записей из {N} без отметки явки.</b> Они посчитаны как «не дошли». "
         "Часть из них — сегодняшние вечерние группы, где отметку могли ещё не поставить. Пока явку не отмечают сразу после занятия, "
         "все цифры ниже — нижняя граница, и дожим делается по неполному списку.</div>")

# по дням
h.append("<h3>По дням</h3><div class='scroll'><table><tr><th>День</th><th class='num'>Записаны</th><th class='num'>Пришли</th><th class='num'>Доходимость</th><th class='num'>Купили</th><th class='num'>Конверсия</th></tr>")
for d, lab in [("2026-08-31", "Пн 31.08"), ("2026-09-01", "Вт 01.09"), ("2026-09-02", "Ср 02.09")]:
    ids = [u for u in by if by[u]["day"] == d]; c = sum(1 for u in ids if came(u)); p = sum(1 for u in ids if paid(u))
    h.append(f"<tr><td><b>{lab}</b></td><td class='num'>{len(ids)}</td><td class='num'>{c}</td><td class='num'>{round(100*c/len(ids))}%</td><td class='num'>{p}</td><td class='num'>{round(100*p/c) if c else 0}%</td></tr>")
h.append("</table></div>")

# по направлениям
S = collections.defaultdict(lambda: {"з": 0, "д": 0, "к": 0})
for u, t in by.items():
    for s in t["subj"]:
        S[s]["з"] += 1; S[s]["д"] += int(came(u)); S[s]["к"] += int(paid(u))
h.append("<h3>По направлениям</h3><div class='scroll'><table><tr><th>Направление</th><th class='num'>Записаны</th><th class='num'>Пришли</th><th class='num'>Доходимость</th><th class='num'>Купили</th></tr>")
for s, v in sorted(S.items(), key=lambda kv: -kv[1]["з"]):
    h.append(f"<tr><td>{esc(s)}</td><td class='num'>{v['з']}</td><td class='num'>{v['д']}</td><td class='num'>{round(100*v['д']/v['з'])}%</td><td class='num'>{v['к']}</td></tr>")
h.append("</table></div>")

# по времени
T = collections.defaultdict(lambda: {"з": 0, "д": 0, "к": 0})
for u, t in by.items():
    hh = t["time"][:2] + ":00"; T[hh]["з"] += 1; T[hh]["д"] += int(came(u)); T[hh]["к"] += int(paid(u))
h.append("<h3>По времени пробного</h3><div class='scroll'><table><tr><th>Время</th><th class='num'>Записаны</th><th class='num'>Пришли</th><th class='num'>Купили</th></tr>")
for hh, v in sorted(T.items()):
    h.append(f"<tr><td><b>{hh}</b></td><td class='num'>{v['з']}</td><td class='num'>{v['д']}</td><td class='num'>{v['к']}</td></tr>")
h.append("</table></div>")
h.append("<div class='card'>Вечер 16:00–19:00 даёт 45 записей из 60 и 22 оплаты из 31. Утро (9:00–12:00) — 15 записей, но доходимость 67% и конверсия 90%: кто записался утром, тот пришёл и купил. Утренние группы недогружены не из-за спроса, а из-за того, что туда почти не записывают.</div>")

# источники
ADV = {158831: "Баннер в метро", 158832: "Порекомендовали", 158833: "Знаем вас", 158834: "Иное", 178189: "Листовка", 178190: "Автобаннер", 232402: "Roistat (сайт/реклама)", 232403: "Wazzup", 376242: "Промоутер"}
src = collections.Counter(ADV.get(U[u]["advSource"], "не указан") for u in by)
old = sum(1 for u in by if U[u]["old_client"])
h.append("<h3>Откуда пришли 60 семей</h3><div class='kpi'>")
for k, v in src.most_common():
    h.append(f"<div class='k'><div class='v'>{v}</div><div class='l'>{esc(k)}</div></div>")
h.append("</div>")
h.append(f"<div class='card ok'><b>{old} из {N} — наши бывшие клиенты</b> (платили до 30 августа), {N-old} — новые. "
         "«Знаем вас» и «Порекомендовали» вместе — 20 семей, промоутер — 9, весь платный трафик (Roistat, метро, листовка) — 8. "
         "Сарафан и промоутер дают в три раза больше, чем реклама. 46 из 60 карточек созданы вручную по звонку или визиту — сайт и формы почти не участвуют.</div>")

# деньги
h.append("<h2>Деньги за 30.08–02.09</h2><div class='kpi'>"
         f"<div class='k'><div class='v good'>{int(money_all):,}</div><div class='l'>₽ всего оплат от {len(pays)} семей</div></div>"
         f"<div class='k'><div class='v'>{int(money_trial):,}</div><div class='l'>₽ от {P} семей с пробного этой недели</div></div>"
         f"<div class='k'><div class='v'>{int(money_all-money_trial):,}</div><div class='l'>₽ продления и остальные</div></div>"
         "</div>".replace(",", " "))

# заполненность
h.append("<h2>Заполненность групп 2026/27</h2>")
F = collections.defaultdict(lambda: {"г": 0, "м": 0, "у": 0, "ж": 0, "пуст": 0, "полн": 0})
for g in G:
    s = g["subject"]; live = g["учится"] + g["посетил"] + g["записался"]; mx = g["max"] or 0
    F[s]["г"] += 1; F[s]["м"] += mx; F[s]["у"] += g["учится"]; F[s]["ж"] += live
    F[s]["пуст"] += int(live == 0); F[s]["полн"] += int(bool(mx) and live >= mx)
h.append("<div class='scroll'><table><tr><th>Направление</th><th class='num'>Групп</th><th class='num'>Мест</th><th class='num'>Учится</th><th class='num'>Живых записей</th><th>Заполнено</th><th class='num'>Полных</th></tr>")
tm = tl = tu = 0
for s, v in sorted(F.items(), key=lambda kv: -kv[1]["м"]):
    pct = round(100 * v["ж"] / v["м"]) if v["м"] else 0
    cls = "" if pct >= 75 else ("mid" if pct >= 50 else "low")
    tm += v["м"]; tl += v["ж"]; tu += v["у"]
    h.append(f"<tr><td>{esc(s)}</td><td class='num'>{v['г']}</td><td class='num'>{v['м']}</td><td class='num'>{v['у']}</td><td class='num'>{v['ж']}</td>"
             f"<td style='min-width:140px'><div class='bar'><i class='{cls}' style='width:{min(100,pct)}%'></i></div><span class='small'>{pct}%</span></td><td class='num'>{v['полн']}</td></tr>")
h.append(f"<tr><td><b>Итого</b></td><td class='num'><b>{sum(v['г'] for v in F.values())}</b></td><td class='num'><b>{tm}</b></td><td class='num'><b>{tu}</b></td><td class='num'><b>{tl}</b></td><td><b>{round(100*tl/tm)}%</b></td><td class='num'>{sum(v['полн'] for v in F.values())}</td></tr></table></div>")
nolg = {k: v for k, v in F.items() if k != "логопед"}
m2 = sum(v["м"] for v in nolg.values()); l2 = sum(v["ж"] for v in nolg.values())
h.append(f"<div class='card warn'><b>Без логопеда (там индивидуальные слоты): {m2} мест в группах, {l2} живых записей — {round(100*l2/m2)}%. До ста процентов не хватает {m2-l2} детей.</b> "
         "«Живая запись» — это учится, посетил пробное или записан на пробное; реально оплатили из них далеко не все.</div>")

full = [g for g in G if g["max"] and g["учится"] + g["посетил"] + g["записался"] >= g["max"] and "ЛГ" not in g["name"]]
risk = [g for g in G if 1 <= g["учится"] + g["посетил"] + g["записался"] <= 2 and "ЛГ" not in g["name"]]
h.append(f"<h3>Полные и переполненные — {len(full)}</h3><div class='card'><span class='small'>" +
         " · ".join(f"<b>{g['учится']+g['посетил']+g['записался']}/{g['max']}</b> {esc(re.sub('^2627_','',g['name']))}" for g in full) + "</span></div>")
h.append(f"<h3>С одним-двумя детьми — {len(risk)}, педагог работает почти впустую</h3><div class='card alarm'><span class='small'>" +
         " · ".join(f"<b>{g['учится']+g['посетил']+g['записался']}/{g['max']}</b> {esc(re.sub('^2627_','',g['name']))}" for g in sorted(risk, key=lambda g: g['name'])) + "</span></div>")

# кого догонять
def row(u):
    t = by[u]; x = U[u]
    return (f"<tr><td><span class='chk'></span></td><td>{t['day'][5:]} {t['time']}</td><td><b>{esc(x['name'])}</b></td>"
            f"<td class='ph'>{esc(x['phone'])}</td><td class='small'>{esc('/'.join(sorted(t['subj'])))}</td></tr>")
h.append(f"<h2>Кого догонять на этой неделе</h2>")
h.append(f"<h3>Пришли, не купили, записи дальше нет — {len(came_nopay_lost)}</h3>"
         "<div class='card'><span class='small'>Самые тёплые: ребёнка видели, педагог его знает. Звонок Лены с наблюдением педагога и скидкой 10% до конца недели.</span></div>"
         "<div class='scroll'><table><tr><th></th><th>Были</th><th>Ребёнок</th><th>Телефон</th><th>Направление</th></tr>" + "".join(row(u) for u in came_nopay_lost) + "</table></div>")
h.append(f"<h3>Пришли, не купили, но записаны дальше — {len(came_nopay_re)}</h3><div class='card'><span class='small'>" +
         " · ".join(f"<b>{esc(U[u]['name'])}</b> → {esc(U[u]['next'])}" for u in came_nopay_re) + ". На следующем занятии закрывать на абонемент.</span></div>")
h.append(f"<h3>Не дошли и никуда не перезаписаны — {len(no_show_lost)}</h3>"
         "<div class='card'><span class='small'>Ира, окно 13:00–16:00. Одно конкретное предложение с днём и временем: открытый урок на этой неделе, "
         "диагностика или экскурсия. Шестеро из сегодняшних вечерних групп — сначала проверить отметку явки.</span></div>"
         "<div class='scroll'><table><tr><th></th><th>Были записаны</th><th>Ребёнок</th><th>Телефон</th><th>Направление</th></tr>" + "".join(row(u) for u in no_show_lost) + "</table></div>")

# --- стратегия -----------------------------------------------------------
need = m2 - l2
h.append("<h2>Стратегия набора на сентябрь</h2>")
h.append(f"<div class='card'><b>Математика цели.</b> В группах {m2} мест, живых записей {l2}, не хватает {need}. "
         f"Сейчас из каждых 100 записанных на пробное приходят {round(100*C/N)} и покупают {round(100*P/N)}. "
         f"Чтобы закрыть {need} мест при такой конверсии, нужно около {round(need/(P/N))} записей на пробное за месяц — по {round(need/(P/N)/26)} в день. "
         f"За три дня недели открытых уроков было {N}, то есть {round(N/3)} в день — но это пик. "
         "Значит ставка не на «больше лидов», а на три вещи: не терять тех, кто уже записался; вернуть тех, кто уже платил; "
         "и не держать пятнадцать полупустых групп там, где спрос на десять полных.</div>")
h.append("<div class='strat'>")
items = [
 ("1. Довести и закрыть тех, кто уже в воронке — бесплатно и сразу",
  f"Доходимость {round(100*C/N)}% и {unmarked} записей без отметки — это дыра, в которую утекает каждый третий записавшийся. Что делать: отметка явки сразу после занятия — обязанность встречающего админа; "
  f"напоминание за день и в день (работает), плюс звонок утром тем, кто не ответил «да»; после пробного — единая форма из девяти полей и три касания, а не «ну что решили». "
  f"Сейчас в дожиме {len(came_nopay_lost)+len(no_show_lost)} семей.",
  f"+{int((len(came_nopay_lost)*0.5 + len(no_show_lost)*0.35)*avg):,} ₽ на этой неделе".replace(",", " ")),
 ("2. Вернуть тех, кто уже платил — самый дешёвый канал",
  "507 семей платили нам в 2025/26 и летом. 206 из них сейчас в статусах «недозвон» и «думает», ещё 31 из ДОД и 34 с Праздника — без записи. "
  "Это люди, которые знают адрес, педагогов и цены; им не надо продавать центр, надо назначить день. Окно 13:00–16:00 у Иры — только на них: "
  "по 40 наборов в день, недозвон закрывает автопилот WhatsApp и СМС. Приоритет — те, у кого ребёнок по возрасту попадает в полупустые группы ПШ.",
  f"206 семей × 20% возврата × {int(avg):,} ₽ ≈ {int(206*0.2*avg):,} ₽".replace(",", " ")),
 ("3. Промоутер и сарафан вместо рекламы — пока",
  f"Из 60 семей 20 пришли по «знаем вас» и рекомендации, 9 — от промоутера, и только 8 — со всей платной рекламы. Директ и ВК запускать сейчас — "
  "лить лиды в воронку, где каждый третий теряется до CRM. Сначала: промоутер каждый будний день 16:00–19:00 у метро и «Янтаря» с QR на запись; "
  "рефералка «приведи друга — обоим −10% на второй месяц» всем 130 учащимся через приветственную серию и чат; отзыв на Яндекс Картах в обмен на подарок ребёнку. "
  "Рекламу включать в третью неделю, когда доходимость будет выше 75% и отметка явки — 100%.",
  "стоимость лида в 3–5 раз ниже рекламы"),
 ("4. Сжать расписание: меньше групп, но полные",
  f"Подготовка к школе — 15 групп на 120 мест, живых 58 (48%), {sum(1 for g in risk if 'ПШ' in g['name'])} групп с одним-двумя детьми. Английский — три группы переполнены (Гр3 9/8, Гр4 8/8, Гр6 8/8), а вт-чт 19:00 Movers — один ребёнок. "
  "Слить ПШ до 10–11 групп по самым востребованным слотам (17:00–19:00 пн-чт и вт-чт), Movers вт-чт объединить с пн-ср 19:00, из переполненных АЯ разводить в соседние часы. "
  "Педагог, ведущий двоих, стоит столько же, сколько ведущий восьмерых — и группа из двоих не удерживает: детям скучно, родители уходят.",
  "экономия 4–5 педагого-часов в неделю + выше удержание"),
 ("5. Открыть то, что спрашивают, и убрать то, что не берут",
  "Раннее развитие заполнено на 76%, шесть групп полных, ср-сб 11:45 — 9 детей на 7 мест: открывать вторые группы на 11:00–12:00. "
  "Робототехника — две группы набраны, расписания нет, админы ждут его от тебя, чтобы обзвонить. Скорочтение и каллиграфия — заявки есть, групп нет. Танцы — есть спрос, нет расписания. "
  "Сад полного дня спрашивают по два раза в день — хотя бы посчитать экономику. Шахматы — 4 записи на 16 мест: либо промо через ПШ-родителей, либо одна группа вместо двух.",
  "новые направления = новые семьи, а не перекладывание тех же"),
 ("6. Убрать утечку между разговором и CRM",
  "За один день: Деева записана в группу не по возрасту, у Софьи договорённость на завтра без записи, у Демьяненко перевод без записи на занятие, Романовым подтверждение с прошедшей датой. "
  "Правило: запись создаётся во время разговора, не «потом»; пробное только через запись на конкретное занятие; после звонка — комментарий девять полей. "
  "Плюс перестать писать клиентам «это бот, не обращайте внимания» — так мы учим их игнорировать канал, по которому идут напоминания.",
  "каждая потерянная договорённость = один ребёнок из недостающих"),
]
for t, body, money in items:
    h.append(f"<div class='card'><b class='h'>{t}<span class='money'>{money}</span></b><span class='small'>{body}</span></div>")
h.append("</div>")
h.append("<div class='card ok'><b>Честно про «100% к 30.09».</b> При нынешних 79 группах это нереально: нужно 156 новых детей за 26 дней при потоке, который в пиковую неделю дал 31 оплату. "
         "Реалистичная и более выгодная цель — 65 групп, заполненных на 85–90%: те же деньги, меньше педагого-часов, живые группы, из которых не уходят. "
         "Пункты 1, 2 и 6 не стоят ничего и дают эффект на этой неделе; пункт 4 — решение на неделю; 3 и 5 — на месяц.</div>")
h.append("</div>")
open("/home/user/kidsup/docs/itog_3dnya.html", "w", encoding="utf-8").write("".join(h))
print("itog_3dnya.html", len("".join(h)), "байт")
