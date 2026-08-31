# -*- coding: utf-8 -*-
import json, re, collections, datetime, os
BASE="/home/user/kidsup/docs/rabota"
aud=json.load(open(f"{BASE}/audit_zapisey.json"))
S=json.load(open(f"{BASE}/spiski_31.json"))
Z=json.load(open(f"{BASE}/zayavki_open.json"))
S3=json.load(open(f"{BASE}/spisok3.json")) if os.path.exists(f"{BASE}/spisok3.json") else []
REAL=[g for g in aud["groups"] if not g["zayavki_group"]]
LIVE={"Учится","3. Записался на пробное","5. Посетил пробное"}
DAYS=["пн","вт","ср","чт","пт","сб","вс"]
def rows(g): return [k for k in g["kids"] if k["jstname"] in LIVE and not k["zayavka"]]
def first(g): return min(g["days"], key=DAYS.index) if g["days"] else None
MON=[g for g in REAL if first(g)=="пн" and rows(g)]
def tkey(t):
    h,m=(t or "0:0").split(":"); return int(h)*60+int(m)
MON.sort(key=lambda g:tkey(g["time"]))
IZO=[g for g in MON if g["subj"]=="ИЗО"]; LOG=[g for g in MON if g["subj"]=="Логопед"]
GO=[g for g in MON if g not in IZO and g not in LOG]
n_go=sum(len(rows(g)) for g in GO); n_izo=sum(len(rows(g)) for g in IZO)
n_log=sum(len(rows(g)) for g in LOG)
DOD_NO=[d for d in S["dod"] if d["status"]=="нет записи"]
DOD_PR=[d for d in S["dod"] if d["status"]=="на пробном"]
PRZ=S["prazdnik"]
NEDOZ=[x for x in S3 if x["state"]==345768]; DUM=[x for x in S3 if x["state"]==146950]

CSS="""
:root{--ink:#15132e;--muted:#6c6a86;--line:#e4e2f0;--bg:#f8f7fc;--card:#fff;
--indigo:#312783;--blue:#1DA7E0;--green:#7DB928;--amber:#F59C00;--red:#E30613;
--ira:#1DA7E0;--lena:#7DB928}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:0 16px 80px}
.hero{background:linear-gradient(135deg,#312783,#1DA7E0);color:#fff;margin:0 -16px 22px;
padding:28px 20px 24px;border-radius:0 0 20px 20px}
.hero h1{margin:0 0 6px;font-size:27px;line-height:1.15}
.hero p{margin:0;opacity:.92;font-size:15px}
h2{font-size:20px;margin:32px 0 10px;color:var(--indigo);border-bottom:2px solid var(--line);padding-bottom:6px}
h3{font-size:16px;margin:18px 0 8px}
.who{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));margin:16px 0}
.wcard{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;
border-top:4px solid var(--line)}
.wcard.ira{border-top-color:var(--ira)}.wcard.lena{border-top-color:var(--lena)}
.wcard .nm{font-size:18px;font-weight:800;margin-bottom:2px}
.wcard .rl{font-size:13px;color:var(--muted);margin-bottom:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}
.alarm{border-left:4px solid var(--red);background:#fff5f5}
.warn{border-left:4px solid var(--amber);background:#fffaf0}
.ok{border-left:4px solid var(--green);background:#f7fbf0}
table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);
font-weight:600;padding:8px 6px;border-bottom:2px solid var(--line)}
td{padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
.num{text-align:right;white-space:nowrap}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.step{display:flex;gap:12px;align-items:flex-start;margin:12px 0}
.step .n{flex:none;width:28px;height:28px;border-radius:50%;background:var(--indigo);color:#fff;
font-weight:800;display:flex;align-items:center;justify-content:center;font-size:14px}
.step .b{flex:1}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px;font-weight:700;white-space:nowrap}
.p-red{background:#fce8e9;color:#9c060f}.p-amber{background:#fdf0dc;color:#94600a}
.p-green{background:#eef7e0;color:#4d7511}.p-gray{background:#eeedf5;color:#5b5a70}
.p-blue{background:#e4f4fc;color:#12668b}
ul{margin:8px 0;padding-left:20px}li{margin:5px 0}
.small{font-size:13px;color:var(--muted)}
.q{background:#f1effb;border-radius:10px;padding:10px 13px;margin:8px 0;font-size:14px;font-style:italic}
.ph{font-variant-numeric:tabular-nums;white-space:nowrap}
.kids{font-size:13px;color:var(--muted)}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--ink:#eae8f6;--muted:#a3a1ba;
--line:#343150;--bg:#131228;--card:#1d1b36;--indigo:#ab9ff2}
:root:not([data-theme="light"]) .alarm{background:#331519}
:root:not([data-theme="light"]) .warn{background:#332812}
:root:not([data-theme="light"]) .ok{background:#1e2c15}
:root:not([data-theme="light"]) .q{background:#25233e}
:root:not([data-theme="light"]) .p-red{background:#3d1416;color:#f38b90}
:root:not([data-theme="light"]) .p-amber{background:#3b2b0e;color:#f2b34c}
:root:not([data-theme="light"]) .p-green{background:#26361a;color:#a9d96a}
:root:not([data-theme="light"]) .p-gray{background:#2a2842;color:#a5a3bb}
:root:not([data-theme="light"]) .p-blue{background:#122f3f;color:#77cdf0}}
"""
H=[];A=H.append
A(f"<style>{CSS}</style><div class='wrap'>")
A("<div class='hero'><h1>Понедельник 31 августа — по шагам</h1>"
  "<p>Работаем 9:00–20:00 · Лена на ресепшене (пн–чт) · Ира на телефоне<br>"
  "Цель дня: записать в группы и дожать до абонементов</p></div>")

# ── РОЛИ
A("<div class='who'>")
A("<div class='wcard lena'><div class='nm'>Лена — ресепшен</div>"
  "<div class='rl'>Понедельник–четверг на площадке. Живые люди важнее телефона</div>"
  "<ul class='small'><li>Встречает каждую группу, знакомит с педагогом</li>"
  "<li>После занятия ловит родителя <b>на выходе</b> и доводит до абонемента</li>"
  "<li>Кто не пришёл — сразу сообщает Ире, та звонит в тот же час</li>"
  "<li>Записывает второй предмет тем, кто уже доволен первым</li></ul></div>")
A("<div class='wcard ira'><div class='nm'>Ира — телефон</div>"
  "<div class='rl'>Три списка по порядку, сверху вниз. Не перескакивать</div>"
  f"<ul class='small'><li>До 10:00 — подтверждения на сегодня ({n_go} детей)</li>"
  f"<li>Дожим вчерашнего ДОД — {len(DOD_NO)} без записи</li>"
  f"<li>Заявки с Праздника — {len(PRZ)} семей</li>"
  f"<li>Летние и прошлогодние: недозвоны и думающие</li></ul></div>")
A("</div>")

# ── ШАГ 0
A("<h2>Шаг 0. До 10:00 — Ира. Подтверждения на сегодня</h2>")
A(f"<div class='card ok'>{n_go} детей в {len(GO)} группах. Цель — услышать «да, будем» "
  "от каждого. Не дозвонилась — отметь, Клод догонит в WhatsApp.</div>")
A("<div class='q'>«Доброе утро! Это KidsUP. Сегодня в ЧЧ:ММ ждём [имя] на первое занятие. "
  "Бульвар Рокоссовского 6 к1В, БЦ «Богородский», 7-й подъезд, 2 этаж, домофон 12. "
  "Приходите за 10 минут. Будете?»</div>")
A("<div class='scroll'><table><tr><th>Время</th><th>Группа</th><th class='num'>Детей</th><th>Кого ждём</th></tr>")
for g in GO:
    kk=rows(g); mx=g["max"] or 0
    cl="p-red" if len(kk)>mx else ("p-amber" if len(kk)==mx else "p-gray")
    A(f"<tr><td><b>{g['time']}</b></td><td class='small'>{g['name'][:52]}</td>"
      f"<td class='num'><span class='pill {cl}'>{len(kk)}/{mx}</span></td>"
      f"<td class='kids'>{', '.join(k['name'] for k in kk)}</td></tr>")
A("</table></div>")
A(f"<div class='card warn'><b>Двоим сказать отдельно, иначе приедут зря.</b> "
  f"ИЗО ({n_izo} детей) начинается в среду 2.09: "
  + ", ".join(k["name"] for g in IZO for k in rows(g)) + ". "
  f"Логопед Елена ({n_log}) принимает с 7.09: "
  + ", ".join(k["name"] for g in LOG for k in rows(g)) + ".</div>")

# ── ШАГ 1
A(f"<h2>Шаг 1. Дожать вчерашний ДОД — {len(DOD_NO)} семей</h2>")
A("<div class='card alarm'><b>Самое горячее, что есть.</b> Вчера эти люди были у нас "
  "в центре, видели педагогов и залы. Прошли сутки — дальше впечатление тускнеет "
  "с каждым днём. Звонить сегодня, до обеда.<br><br>"
  f"Из {len(S['dod'])} записанных на ДОД: {len(DOD_NO)} до сих пор не записаны ни в одну группу, "
  f"{len(DOD_PR)} записаны на пробное, но не оплатили, "
  f"{len(S['dod'])-len(DOD_NO)-len(DOD_PR)} уже учатся.</div>")
A("<div class='q'>«Здравствуйте! Это KidsUP, вы вчера были у нас на дне открытых дверей. "
  "Как вам [имя предмета]? Мы уже начали занятия — могу записать [имя] на эту неделю. "
  "Какой день удобнее?»<br><br>Если сомневается: «Первое занятие условно-бесплатное: "
  "не понравится — платить не нужно, понравится — оно входит в первый абонемент».</div>")
A("<div class='scroll'><table><tr><th>Ребёнок</th><th>Телефон</th><th>Был на</th>"
  "<th>Время</th><th>Статус</th></tr>")
for d in DOD_NO+DOD_PR:
    cl="p-red" if d["status"]=="нет записи" else "p-amber"
    A(f"<tr><td>{(d['name'] or '')[:26]}</td><td class='ph'>{d['phone'] or '—'}</td>"
      f"<td class='small'>{(d['course'] or '')[:26]}</td><td class='small'>{d['slot']}</td>"
      f"<td><span class='pill {cl}'>{d['status']}</span></td></tr>")
A("</table></div>")

# ── ШАГ 2
A(f"<h2>Шаг 2. Заявки с Праздника — {len(PRZ)} семей</h2>")
A("<div class='card'>Люди с тега «Праздник 2026», которые до сих пор не записаны "
  "ни в одну группу. Они нас знают: были на празднике 29.08 или оставили анкету. "
  "Идти по списку сверху вниз, отмечать результат в строке.</div>")
A("<div class='q'>«Здравствуйте! Это KidsUP с бульвара Рокоссовского. Вы оставляли "
  "анкету на нашем празднике. Учебный год начался вчера, группы набраны наполовину — "
  "хочу успеть предложить вам место, пока оно есть. Что интересно для [имя]?»</div>")
A("<div class='scroll'><table><tr><th>Семья</th><th>Телефон</th><th>Статус в базе</th></tr>")
SN={125951:"новый лид",345768:"недозвон",146950:"думает",125952:"записался",345759:"архив"}
for p in PRZ[:200]:
    st=SN.get(p["state"], str(p["state"]))
    cl="p-amber" if p["state"]==146950 else ("p-blue" if p["state"]==345768 else "p-gray")
    A(f"<tr><td>{p['name'][:30]}</td><td class='ph'>{p['phone'] or '—'}</td>"
      f"<td><span class='pill {cl}'>{st}</span></td></tr>")
A("</table></div>")

# ── ШАГ 3
A(f"<h2>Шаг 3. Летние и прошлогодние — {len(S3)} семей</h2>")
A("<div class='card'>Те, кто ходил летом 2026 или в 2025/26 и сейчас никуда не записан. "
  f"Делятся на два разговора: <b>{len(NEDOZ)} недозвонов</b> — до них просто не дошли, "
  f"и <b>{len(DUM)} думающих</b> — с ними уже говорили, но решения нет.</div>")
A("<div class='q'><b>Недозвон:</b> «Здравствуйте! Это KidsUP. [Имя] ходил к нам "
  "в прошлом году — учебный год начался, я хочу успеть предложить место, пока группа "
  "не закрылась. Продолжаем?»<br><br>"
  "<b>Думающий:</b> «Здравствуйте! Мы говорили на прошлой неделе про [предмет]. "
  "Занятия уже начались — если решите на этой неделе, место ещё держим. "
  "Что вас останавливает?»</div>")
if S3:
    A("<div class='scroll'><table><tr><th>Семья</th><th>Телефон</th><th>Разговор</th>"
      "<th>Праздник</th><th>Статус с</th></tr>")
    for x in S3[:250]:
        cl="p-blue" if x["state"]==345768 else "p-amber"
        A(f"<tr><td>{x['name'][:30]}</td><td class='ph'>{x['phone'] or '—'}</td>"
          f"<td><span class='pill {cl}'>{x['stname']}</span></td>"
          f"<td class='small'>{'да' if x['prazdnik'] else ''}</td>"
          f"<td class='small'>{x['changed']}</td></tr>")
    A("</table></div>")

# ── ДОЖИМ
A("<h2>Как дожимать до абонемента — обеим</h2>")
A("<div class='card ok'><b>−10% на первый абонемент действует только в день пробного.</b> "
  "Акция «сентябрь по ценам прошлого года» закончилась — другой причины оформить "
  "сегодня у родителя нет. Скидки не суммируются: действует одна −10%.</div>")
A("<div class='step'><div class='n'>1</div><div class='b'><b>Ловить на выходе, не звонить вечером.</b> "
  "Родитель в коридоре, ребёнок доволен — лучшая минута дня. Ушли домой — теряется половина.</div></div>")
A("<div class='step'><div class='n'>2</div><div class='b'><b>Начинать с ребёнка, не с цены.</b> "
  "Взять у педагога одну живую деталь («Соня сама прочитала два слова») и открыть разговор ею.</div></div>")
A("<div class='step'><div class='n'>3</div><div class='b'><b>Не отпускать без даты.</b> "
  "Не готов сегодня — назначаем конкретный час звонка на завтра и пишем в лист. "
  "«Перезвоню как-нибудь» не считается.</div></div>")
A("<div class='step'><div class='n'>4</div><div class='b'><b>Всегда предлагать второй предмет:</b> "
  "«в соседний день в это же время идёт [предмет] — многие берут парой, рассказать?» "
  "Второй предмет — те же −10%.</div></div>")
A("<div class='step'><div class='n'>5</div><div class='b'><b>Не пришёл — звонок в тот же час.</b> "
  "Пока причина свежая, переносим на вторник или среду. Через день это уже отказ.</div></div>")

# ── ПОЧИНКИ
A("<h2>Между делом — починить в CRM</h2>")
A("<div class='card alarm'><b>Раннее развитие, Гр3 ср-сб 11:45 (2,2–3 года) — 10 записей на 7 мест.</b> "
  "Группа идёт в среду. Часть детей переводим в Гр6 (пн-пт 12:00, там 4 из 7) — "
  "тот же возраст, другие дни. Начинать с записанных последними.</div>")
A("<div class='card warn'><b>Английский пн-ср 19:00 — 9 детей на 8 мест.</b> "
  "Плюс карточка «Заявка Елена (английский)» (79263936933) сидит сразу в двух слотах: "
  "позвонить, определить уровень, лишнее убрать.</div>")
A("<div class='card warn'><b>Подготовка: Шиш Савелий, Шиш Сергей и Панкратова Александра</b> "
  "записаны и в Гр7 (пн-чт 18:00), и в Гр11 (вт-пт 19:00). Из-за этого Гр7 показывает 8 из 8. "
  "Спросить, какие дни удобнее, лишнее снять — освободится до трёх мест.</div>")
A("<div class='card warn'><b>Логопед Марина:</b> Гунт Лео стоит и в 13:20, и в 14:00 одной субботы; "
  "Ситковский Александр — в трёх слотах. У Марины занято всё, каждая ошибка держит место очереди.</div>")
A(f"<p class='small'>Записи по каждой группе: <a href='/base/gruppy_2627'>/base/gruppy_2627</a> · "
  f"Бонусы: <a href='/base/bonusy_gonka'>/base/bonusy_gonka</a> · "
  f"Ещё {len(Z)} необработанных заявок в буферных группах — когда закончатся три списка выше</p>")
A("</div>")
open("/home/user/kidsup/docs/plan_31avgusta.html","w",encoding="utf-8").write("\n".join(H))
print(f"ok · пн {n_go} · ДОД без записи {len(DOD_NO)} · Праздник {len(PRZ)} · список3 {len(S3)}")
