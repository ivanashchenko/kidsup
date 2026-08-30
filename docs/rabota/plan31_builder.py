# -*- coding: utf-8 -*-
import json, re, collections
d=json.load(open("/home/user/kidsup/docs/rabota/audit_zapisey.json"))
Z=json.load(open("/home/user/kidsup/docs/rabota/zayavki_open.json"))
REAL=[g for g in d["groups"] if not g["zayavki_group"]]
LIVE={"Учится","3. Записался на пробное","5. Посетил пробное"}
DAYS=["пн","вт","ср","чт","пт","сб","вс"]
def kids(g): return [k for k in g["kids"] if k["jstname"] in LIVE and not k["zayavka"]]
def first_day(g): return min(g["days"], key=DAYS.index) if g["days"] else None
MON=[g for g in REAL if first_day(g)=="пн" and kids(g)]
MON.sort(key=lambda g:(len(g["time"]), g["time"]))
IZO=[g for g in MON if g["subj"]=="ИЗО"]
LOG=[g for g in MON if g["subj"]=="Логопед"]
GO=[g for g in MON if g not in IZO and g not in LOG]
n_go=sum(len(kids(g)) for g in GO); n_izo=sum(len(kids(g)) for g in IZO); n_log=sum(len(kids(g)) for g in LOG)
# итоги
subj_kids=collections.defaultdict(set); seats=collections.defaultdict(int); grp=collections.defaultdict(int)
uch=set(); allk=set()
for g in REAL:
    seats[g["subj"]]+=g["max"] or 0; grp[g["subj"]]+=1
    for k in kids(g):
        subj_kids[g["subj"]].add(k["uid"]); allk.add(k["uid"])
        if k["jstname"]=="Учится": uch.add(k["uid"])
TOTS=sum(seats.values()); TOTK=sum(len(v) for v in subj_kids.values())

CSS="""
:root{--ink:#15132e;--muted:#6c6a86;--line:#e4e2f0;--bg:#f8f7fc;--card:#fff;
--indigo:#312783;--blue:#1DA7E0;--green:#7DB928;--amber:#F59C00;--red:#E30613}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:0 16px 80px}
.hero{background:linear-gradient(135deg,#312783,#1DA7E0);color:#fff;margin:0 -16px 22px;
padding:30px 20px 26px;border-radius:0 0 20px 20px}
.hero h1{margin:0 0 6px;font-size:28px;line-height:1.15}
.hero p{margin:0;opacity:.92;font-size:15px}
h2{font-size:21px;margin:34px 0 10px;color:var(--indigo);border-bottom:2px solid var(--line);padding-bottom:6px}
h3{font-size:16px;margin:20px 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}
.alarm{border-left:4px solid var(--red);background:#fff5f5}
.warn{border-left:4px solid var(--amber);background:#fffaf0}
.ok{border-left:4px solid var(--green);background:#f7fbf0}
.why{border-left:4px solid var(--blue);background:#eef8fd}
table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
th{text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);
font-weight:600;padding:8px;border-bottom:2px solid var(--line)}
td{padding:8px;border-bottom:1px solid var(--line);vertical-align:top}
.num{text-align:right;white-space:nowrap}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.tot td{font-weight:700;background:#f2f0fa}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px;font-weight:600;white-space:nowrap}
.p-green{background:#eef7e0;color:#4d7511}.p-amber{background:#fdf0dc;color:#94600a}
.p-red{background:#fce8e9;color:#9c060f}.p-blue{background:#e4f4fc;color:#12668b}
.p-gray{background:#eeedf5;color:#5b5a70}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin:16px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.kpi .lbl{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.kpi .big{font-size:29px;font-weight:800;color:var(--indigo);line-height:1.15}
.step{display:flex;gap:12px;align-items:flex-start;margin:14px 0}
.step .n{flex:none;width:30px;height:30px;border-radius:50%;background:var(--indigo);color:#fff;
font-weight:800;display:flex;align-items:center;justify-content:center;font-size:15px}
.step .b{flex:1}
ul{margin:8px 0;padding-left:20px}li{margin:5px 0}
.small{font-size:13px;color:var(--muted)}
.q{background:#f1effb;border-radius:10px;padding:10px 13px;margin:8px 0;font-size:14px;font-style:italic}
.kids{font-size:13px;color:var(--muted);line-height:1.5}
.ph{font-variant-numeric:tabular-nums;white-space:nowrap}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--ink:#eae8f6;--muted:#a3a1ba;
--line:#343150;--bg:#131228;--card:#1d1b36;--indigo:#ab9ff2}
:root:not([data-theme="light"]) .alarm{background:#331519}
:root:not([data-theme="light"]) .warn{background:#332812}
:root:not([data-theme="light"]) .ok{background:#1e2c15}
:root:not([data-theme="light"]) .why{background:#122b39}
:root:not([data-theme="light"]) .q{background:#25233e}
:root:not([data-theme="light"]) .tot td{background:#262344}
:root:not([data-theme="light"]) .p-green{background:#26361a;color:#a9d96a}
:root:not([data-theme="light"]) .p-amber{background:#3b2b0e;color:#f2b34c}
:root:not([data-theme="light"]) .p-red{background:#3d1416;color:#f38b90}
:root:not([data-theme="light"]) .p-blue{background:#122f3f;color:#77cdf0}
:root:not([data-theme="light"]) .p-gray{background:#2a2842;color:#a5a3bb}}
"""
H=[];A=H.append
A(f"<style>{CSS}</style><div class='wrap'>")
A("<div class='hero'><h1>Понедельник 31 августа</h1>"
  "<p>Первый день учебного года · в смене Елена и Ирина · Лиза онлайн<br>"
  "Полная сверка CRM сделана в ночь на 31.08: 101 группа, каждая запись проверена</p></div>")

# ── ИТОГ
A("<h2>Где мы сейчас на самом деле</h2>")
A("<div class='grid'>")
for l,v,n in [("Детей в группах",str(len(allk)),"уникальных, без двойного счёта"),
              ("Из них платят",str(len(uch)),"статус «Учится»"),
              ("Только на пробном",str(len(allk)-len(uch)),"деньгами ещё не стали"),
              ("Заполнение",f"{round(TOTK/TOTS*100)}%",f"{TOTK} из {TOTS} мест"),
              ("Заявок без ответа",str(len(Z)),"самой старой 18 дней")]:
    A(f"<div class='kpi'><div class='lbl'>{l}</div><div class='big'>{v}</div><div class='small'>{n}</div></div>")
A("</div>")
A("<div class='card warn'><b>Цифра заполнения снизилась с 60% до "
  f"{round(TOTK/TOTS*100)}% — не потому, что кто-то ушёл.</b> Прошлый счёт брал все живые "
  "строки подряд. Полная сверка выявила три вида двойного счёта: буферные группы «Заявки» "
  "считались как занятые места; логопед и раннее развитие ходят дважды в неделю — "
  "один ребёнок занимал две строки; плюс карточки-заявки без имени ребёнка. "
  "Сейчас считаем по уникальным детям.</div>")
A("<div class='scroll'><table><tr><th>Предмет</th><th class='num'>Детей</th>"
  "<th class='num'>Мест</th><th class='num'>Групп</th><th class='num'>Занято</th></tr>")
for k in sorted(subj_kids, key=lambda x:-len(subj_kids[x])):
    n=len(subj_kids[k]); mx=seats[k]; p=round(n/mx*100) if mx else 0
    cl="p-green" if p>=70 else ("p-amber" if p>=45 else "p-red")
    A(f"<tr><td>{k}</td><td class='num'>{n}</td><td class='num'>{mx}</td>"
      f"<td class='num'>{grp[k]}</td><td class='num'><span class='pill {cl}'>{p}%</span></td></tr>")
A(f"<tr class='tot'><td>Итого</td><td class='num'>{TOTK}</td><td class='num'>{TOTS}</td>"
  f"<td class='num'>{len(REAL)}</td><td class='num'>{round(TOTK/TOTS*100)}%</td></tr></table></div>")

# ── ОБОСНОВАНИЕ
# ── КТО ЧТО ДЕЛАЕТ ПО ЧАСАМ
A("<h2>Кто что делает по часам</h2>")
A("<div class='card'>В смене два администратора: <b>Ирина Головина</b> и <b>Елена Кузнецова</b> "
  "(педагога Ирину, которая ведёт раннее развитие, не путаем). "
  "День разваливается на три куска, и в каждом роли разные — потому что вечером "
  "звонить физически некогда, а днём некого встречать.</div>")
A("<div class='scroll'><table><tr><th>Время</th><th>Что происходит</th>"
  "<th>Ирина</th><th>Елена</th></tr>")
ROWS=[("до 10:00","Тишина","<b>Обзвон подтверждений</b> — все 56 записанных, плюс ИЗО и логопед отдельно",
       "Готовит список заявок, начинает с самых старых"),
      ("10:00–13:00","Раннее развитие: 3 группы, 16 детей и мам",
       "<b>На ресепшене.</b> Встречает, знакомит с педагогом, после занятия — дожим до абонемента. "
       "РР заполнено на 65%, эти мамы решают быстрее всех",
       "<b>На телефоне.</b> Заявки без ответа"),
      ("13:00–16:00","<b>Окно. Никого нет три часа</b>",
       "<b>На телефоне вместе с Еленой.</b> Это 6 человеко-часов — самый ценный ресурс дня",
       "<b>На телефоне.</b> Продолжает заявки"),
      ("16:00–20:00","Пик: по две группы каждый час, 40 детей",
       "<b>Встреча и проводы.</b> Каждый час приходят и уходят две группы сразу — "
       "нужен человек в дверях",
       "<b>Дожим на выходе.</b> Ловит родителей после занятия, оформляет абонементы"),
      ("в течение дня","Между делом",
       "Правки в CRM: перебор в РР, дубли у логопеда и в подготовке",
       "Кто не пришёл — звонок в тот же час и перенос")]
for t,w,i,e in ROWS:
    A(f"<tr><td><b>{t}</b></td><td class='small'>{w}</td><td>{i}</td><td>{e}</td></tr>")
A("</table></div>")
A("<div class='card warn'><b>Почему Ирина утром на ресепшене, а не на телефоне.</b> "
  "В 10, 11 и 12 приходят мамы с малышами 1–3 лет — это раннее развитие, "
  "самое заполненное направление и самая быстрая конверсия в абонемент: "
  "решение принимают на месте, пока ребёнок ещё в зале. Оставить их без человека "
  "ради звонков — обменять горячее на тёплое.<br><br>"
  "<b>Почему вечером обе на площадке.</b> С 16:00 каждый час заходят и выходят "
  "по две группы сразу: 40 детей за четыре часа. Один человек физически не успевает "
  "и встречать, и разговаривать с родителями на выходе. Звонки в это время не идут — "
  "и не должны.</div>")

A("<h2>Почему план именно такой</h2>")
A("<div class='card why'>Воронка на сегодня выглядит так: "
  f"<b>{len(Z)} человек сами оставили заявку</b> и ждут ответа → "
  f"<b>{len(allk)-len(uch)} записаны на пробное</b>, но ещё не заплатили → "
  f"<b>{len(uch)} платят</b>. Порядок работы задаёт не важность, а цена одного ребёнка: "
  "чем ближе человек к кассе, тем дешевле его довести.</div>")
A("<div class='step'><div class='n'>1</div><div class='b'><b>Тот, кто уже в здании</b> — "
  f"{n_go} детей придут сегодня на первое занятие. Разговор в коридоре после занятия "
  "стоит ноль минут поиска и даёт максимальную конверсию: ребёнок доволен, "
  "родитель рядом, скидка действует сегодня. Ушёл домой — половина теряется.</div></div>")
A("<div class='step'><div class='n'>2</div><div class='b'><b>Тот, кто сам попросился</b> — "
  f"{len(Z)} необработанных заявок. Человек оставил телефон по своей воле и не получил "
  "ответа: самая старая заявка от 12 августа, ей 18 дней. Такой контакт конвертируется "
  "в разы лучше холодного, а стоит столько же — один звонок.</div></div>")
A("<div class='step'><div class='n'>3</div><div class='b'><b>Тот, кто записан, но ещё не дошёл</b> — "
  f"{len(allk)-len(uch)} детей. На них уже потрачена вся работа по привлечению. "
  "Каждый недошедший обнуляет её целиком.</div></div>")
A("<div class='step'><div class='n'>4</div><div class='b'><b>И только потом холодная база.</b> "
  "71 семья, кому ни разу не звонили, никуда не денется — а заявка протухает за дни.</div></div>")
A("<div class='card'><b>Арифметика, почему заявки идут раньше холодного обзвона.</b> "
  f"{len(Z)} заявок при конверсии в запись около половины дают ~26 детей. "
  "Те же часы на холодном списке при обычной конверсии дают вчетверо меньше. "
  "Разница не в скрипте и не в старании — в том, что один человек уже поднял руку, "
  "а другого нужно уговаривать с нуля.<br><br>"
  "<b>Слабое место плана, чтобы вы знали:</b> он держится на явке. Если из "
  f"{n_go} записанных придёт половина, день провалится независимо от того, "
  "как хорошо отработают на месте. Поэтому обзвон подтверждений с утра — "
  "не формальность, а несущая конструкция.</div>")

# ── ПРИОРИТЕТ 1
A("<h2>1. До 10:00 — Ирина. Подтверждения</h2>")
A(f"<div class='card ok'>{n_go} детей в {len(GO)} группах. Цель — не «обзвонить», "
  "а услышать «да, будем» от каждого. Кто не берёт трубку — отметить, Клод догонит "
  "в WhatsApp и мессенджерах.</div>")
A("<div class='q'>«Доброе утро! Это KidsUP. Напоминаю: сегодня в ЧЧ:ММ ждём [имя] "
  "на первое занятие. Бульвар Маршала Рокоссовского 6 к1В, БЦ «Богородский», "
  "7-й подъезд, 2 этаж, домофон 12. Приходите за 10 минут. Будете?»</div>")
A("<div class='scroll'><table><tr><th>Время</th><th>Группа</th><th class='num'>Детей</th><th>Кого ждём</th></tr>")
for g in GO:
    kk=kids(g); mx=g["max"] or 0
    cl="p-red" if len(kk)>mx else ("p-amber" if len(kk)==mx else "p-gray")
    A(f"<tr><td><b>{g['time']}</b></td><td class='small'>{g['name'][:58]}</td>"
      f"<td class='num'><span class='pill {cl}'>{len(kk)}/{mx}</span></td>"
      f"<td class='kids'>{', '.join(k['name'] for k in kk)}</td></tr>")
A("</table></div>")
A("<h3>Отдельно — иначе приедут зря</h3>")
A(f"<div class='card warn'><b>ИЗО — {n_izo} детей: занятия с СРЕДЫ 2 сентября.</b>"
  "<div class='q'>«Здравствуйте! Уточняю по ИЗО: группа стартует в среду 2 сентября "
  "в ЧЧ:ММ, сегодня занятия нет. Ждём в среду, хорошо?»</div>"
  + "".join(f"<div class='small'>{g['time']} · {g['name'][:52]} — "
            f"{', '.join(k['name'] for k in kids(g))}</div>" for g in IZO) + "</div>")
A(f"<div class='card warn'><b>Логопед Елена — {n_log} детей: приём с 7 сентября.</b>"
  "<div class='q'>«Здравствуйте! По логопеду: Елена начинает 7 сентября, ваше время "
  "ЧЧ:ММ закреплено. Если нужно раньше — есть Марина, посмотрю окно на этой неделе.»</div>"
  "<div class='small'>" + " · ".join(f"{g['time']} {kids(g)[0]['name']}" for g in LOG) + "</div></div>")

# ── ПРИОРИТЕТ 2
A(f"<h2>2. Заявки без ответа — {len(Z)} штук</h2>")
A("<div class='card alarm'>Главный резерв дня. Люди оставили телефон сами — и не получили "
  "ответа. Начинать с самых старых: чем дольше молчим, тем холоднее.<br><br>"
  "<b>Елена ведёт список с утра и до вечера. С 13:00 до 16:00 к ней подключается Ирина</b> — "
  "в окне между группами звонят вдвоём. Отмечать результат прямо в строке, "
  "чтобы не звонить дважды.</div>")
A("<div class='q'>«Здравствуйте! Это KidsUP, бульвар Рокоссовского. Вы оставляли заявку "
  "на [предмет] — извините, что отвечаем не сразу, был большой поток перед стартом года. "
  "Вопрос ещё актуален? Могу записать [имя] на пробное на этой неделе.»</div>")
A("<div class='scroll'><table><tr><th>Заявка от</th><th>Ребёнок</th><th>Телефон</th>"
  "<th>Предмет</th><th>Куда записан</th></tr>")
for z in Z:
    days_old=(__import__("datetime").date(2026,8,31) -
              __import__("datetime").date(*map(int,z["created"].split("-")))).days
    cl="p-red" if days_old>=10 else ("p-amber" if days_old>=5 else "p-gray")
    A(f"<tr><td><span class='pill {cl}'>{z['created'][8:]}.{z['created'][5:7]} · {days_old} дн.</span></td>"
      f"<td>{z['name'][:26]}</td><td class='ph'>{z['phone']}</td>"
      f"<td class='small'>{z['subj'][:20]}</td><td class='small'>{z['grp'][:40]}</td></tr>")
A("</table></div>")

# ── ПРИОРИТЕТ 3
A("<h2>3. Главное за день — дожим до абонемента</h2>")
A("<div class='card ok'><b>−10% на первый абонемент действует только в день пробного.</b> "
  "Акция «сентябрь по ценам прошлого года» закончилась вчера — другой причины "
  "оформить сегодня у родителя нет. Скидки не суммируются: действует одна −10%.</div>")
A("<ul><li><b>Ловим на выходе, не звоним вечером.</b> Родитель в коридоре, ребёнок "
  "доволен — лучшая минута дня</li>"
  "<li>Педагог передаёт одну конкретную деталь про ребёнка — с неё начинаем, не с цены</li>"
  "<li>Не готов — <b>назначаем конкретный час звонка на вторник</b> и пишем в лист</li>"
  "<li>Второй предмет предлагаем всегда: «в соседний день в это же время идёт [предмет]»</li>"
  "<li>Не пришёл — звоним в тот же час и сразу переносим на вторник или среду</li></ul>")
A("<div class='q'>Вечером не оформившим: «Спасибо, что были у нас! [Педагог] передаёт: "
  "[конкретика]. Какие впечатления? Напомню: при оформлении до конца завтрашнего дня "
  "первый абонемент со скидкой 10%. Забронировать [дни/время]?»</div>")

# ── ПОЧИНКИ
A("<h2>4. Починить в CRM — сверка нашла ошибки</h2>")
A("<div class='card alarm'><b>Раннее развитие, Гр3 ср-сб 11:45 (2,2–3 года) — 10 детей на 7 мест.</b> "
  "Перебор на троих, и группа идёт уже в среду. Это больше, чем в английском. "
  "Решить сегодня: часть детей в Гр6 (пн-пт 12:00, там 4 из 7) — тот же возраст, "
  "другие дни. Кому звонить — выбирает Ирина, начиная с записанных последними.</div>")
A("<div class='card alarm'><b>Английский пн-ср 19:00 (Movers-Flyers) — 9 детей на 8 мест.</b> "
  "Десятая строка — карточка «Заявка Елена (английский)» (79263936933), уровень не определён, "
  "поэтому её посадили сразу в два слота. Позвонить, определить уровень, лишнее убрать. "
  "Если все девять придут — расширяем группу до 9 или открываем второй слот Movers-Flyers: "
  "перевести некуда, Гр1 в 16:00 уровнем ниже.</div>")
A("<div class='card warn'><b>Логопед Марина: двойные записи в одну субботу.</b> "
  "Гунт Лео стоит и в 13:20, и в 14:00. Ситковский Александр — сразу в трёх слотах "
  "(вт 16:50, чт 17:40, сб 13:20) и при этом конфликтует с Лео за 13:20. "
  "Уточнить у родителей реальное расписание и снять лишнее: у Марины все слоты заняты, "
  "каждая ошибочная запись держит место живой очереди.</div>")
A("<div class='card warn'><b>Подготовка: трое записаны сразу в две группы.</b> "
  "Шиш Савелий, Шиш Сергей и Панкратова Александра стоят и в Гр7 (пн-чт 18:00), "
  "и в Гр11 (вт-пт 19:00). Из-за этого Гр7 показывает 8 из 8 «полная», хотя трое "
  "могут уйти в Гр11. Спросить у родителей, какие дни удобнее, и снять вторую запись — "
  "это сразу до трёх свободных мест в самой востребованной группе.</div>")
A("<div class='card warn'><b>Ещё дубли внутри одного предмета:</b> "
  "Самчук Егор (РР Гр4 пн-пт и Гр1 ср-сб), Нистратова Агата (РР Гр6 и Гр3), "
  "Димитров Александр и Семенова Мария (ИЗО «Лепка» и «Живопись» подряд). "
  "У ИЗО это может быть намеренно — уточнить, ходит ли ребёнок на оба направления.</div>")
A("<div class='card'><b>Четыре пустые группы:</b> ПШ Гр13 (чт 19:00 + сб 11:00), "
  "английский Гр5 (вт-чт 16:00) — ноль записей. Туда в первую очередь предлагаем "
  "тех, кто не помещается в переполненные. Плюс два слота логопеда Елены (пн и пт 18:00) "
  "числятся пустыми, потому что заявка Нариманлы Селин от 25.08 так и осталась "
  "необработанной.</div>")

A("<h2>5. Чего сегодня не делаем</h2>")
A("<div class='card'>Холодный список из 71 семьи — не сегодня. Не потому что он плохой, "
  "а потому что заявки и явка дают больше на том же времени, а холодная база не протухнет "
  "за сутки. Возвращаемся к ней в пятницу, когда в смене двое, "
  "или раньше, если заявки закончатся.<br>"
  "<span class='small'>Список: <a href='/base/spisok_final'>/base/spisok_final</a></span></div>")
A("<p class='small'>Заполнение по всем группам: <a href='/base/gruppy_2627'>/base/gruppy_2627</a> · "
  "Бонусы: <a href='/base/bonusy_gonka'>/base/bonusy_gonka</a></p>")
A("</div>")
open("/home/user/kidsup/docs/plan_31avgusta.html","w",encoding="utf-8").write("\n".join(H))
print("ok · детей в пн:",n_go,"· ИЗО:",n_izo,"· логопед:",n_log,"· заявок:",len(Z))
