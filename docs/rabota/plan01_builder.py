# -*- coding: utf-8 -*-
import json, os
BASE="/home/user/kidsup/docs/rabota"
V=json.load(open(f"{BASE}/vtornik.json"))
Z=json.load(open(f"{BASE}/spiski_31.json"))
S3=json.load(open(f"{BASE}/spisok3_payers.json")) if os.path.exists(f"{BASE}/spisok3_payers.json") else []
ST=json.load(open(f"{BASE}/client_statuses.json"))
import re as _re
def junk(p):
    n=(p.get("name") or "").strip(); ph=p.get("phone") or ""
    return (not n or "дубликат" in n.lower() or n.lower().startswith("анкета")
            or bool(_re.fullmatch(r"[^\w\s]+", n)) or not _re.fullmatch(r"7\d{10}", ph))
DOD=[d for d in Z["dod"] if d["status"]!="Учится"]
HEAT={125955:0,125953:1,125952:2,345767:3,146950:4,349497:5,347075:6,125951:7,345768:8}
PRZ=[p for p in Z["prazdnik"] if not junk(p) and p["state"]!=125954]
PRZ.sort(key=lambda p: HEAT.get(p["state"],9))
LOG=[g for g in V if g["name"].startswith("ЛГ ")]
GO=[g for g in V if g not in LOG]
n_go=sum(len(g["kids"]) for g in GO); n_log=sum(len(g["kids"]) for g in LOG)

CSS="""
:root{--ink:#15132e;--muted:#6c6a86;--line:#e4e2f0;--bg:#f8f7fc;--card:#fff;
--indigo:#312783;--blue:#1DA7E0;--green:#7DB928;--amber:#F59C00;--red:#E30613;
--lena:#7DB928;--anya:#F59C00}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:0 16px 70px}
.hero{background:linear-gradient(135deg,#312783,#1DA7E0);color:#fff;margin:0 -16px 20px;
padding:26px 20px 22px;border-radius:0 0 18px 18px}
.hero h1{margin:0 0 5px;font-size:26px;line-height:1.15}
.hero p{margin:0;opacity:.92;font-size:15px}
h2{font-size:20px;margin:30px 0 10px;color:var(--indigo);border-bottom:2px solid var(--line);padding-bottom:6px}
h3{font-size:16px;margin:18px 0 8px}
.who{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));margin:16px 0}
.wcard{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;border-top:4px solid var(--line)}
.wcard.lena{border-top-color:var(--lena)}.wcard.anya{border-top-color:var(--anya)}
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
.q{background:#f1effb;border-radius:10px;padding:10px 13px;margin:8px 0;font-size:14px;font-style:italic}
ul,ol{margin:8px 0;padding-left:20px}li{margin:5px 0}
.small{font-size:13px;color:var(--muted)}
.kids{font-size:13px;color:var(--muted);line-height:1.5}
.ph{font-variant-numeric:tabular-nums;white-space:nowrap}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px;font-weight:700}
.p-red{background:#fce8e9;color:#9c060f}.p-amber{background:#fdf0dc;color:#94600a}
.p-green{background:#eef7e0;color:#4d7511}.p-gray{background:#eeedf5;color:#5b5a70}
.p-blue{background:#e4f4fc;color:#12668b}
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
A("<div class='hero'><h1>Вторник 1 сентября</h1>"
  "<p>Работаем 9:00–20:00 · Лена на ресепшене · Аня на телефоне · Лиза онлайн<br>"
  f"Сегодня {len(GO)} групп и {n_go} детей — почти все стартуют впервые</p></div>")

A("<div class='who'>")
A("<div class='wcard lena'><div class='nm'>Лена — ресепшен</div>"
  "<div class='rl'>Как вчера: встреча, дожим, неявки</div>"
  "<ol class='small'>"
  "<li><b>Первым делом — три переноса со вчера.</b> Им не ответили: "
  "Киселев Павел (79651182969), Алимов Максим (79653549344), "
  "Лариса (79266774045, просит следующую среду)</li>"
  f"<li>Обзвон подтверждений на сегодня — {n_go} детей</li>"
  "<li>Табличка со скидкой на стойке, бланки диагностики педагогам</li>"
  "<li>Встреча групп, дожим до абонемента на выходе</li>"
  "<li><b>Через 5–10 минут после начала урока</b> — обзвон тех, кто не пришёл</li>"
  "</ol></div>")
A("<div class='wcard anya'><div class='nm'>Аня — телефон</div>"
  "<div class='rl'>Роль Иры вчера: сначала свои хвосты, потом три списка</div>"
  "<ol class='small'>"
  "<li><b>Свои клиенты со вчера</b> — 4 незакрытые задачи, список ниже</li>"
  f"<li>ДОД, не записанные на пробное — <b>{len(DOD)} семей</b></li>"
  f"<li>Анкеты с Праздника — <b>{len(PRZ)} семей</b></li>"
  f"<li>Летние и прошлогодние — <b>{len(S3)} семей</b></li>"
  "</ol></div>")
A("</div>")

# --- срочное
A("<h2>Горит прямо сейчас</h2>")
A("<div class='card alarm'><b>79267095054 — придёт сегодня в 18:00, а карточки в CRM нет.</b> "
  "Записались утром по телефону на подготовку, ребёнок не читает (ПШ1). "
  "Завести карточку и оформить запись, иначе его не будет в списке группы. "
  "Спрашивали про оплату материнским капиталом — ответ уточнить у Бориса.</div>")
A("<div class='card alarm'><b>Наталья (79057511557) ждёт сумму к оплате со вчера, 18:05.</b> "
  "Её расчёт — 31 050 ₽ со скидкой 10%. Скидка была привязана ко вчерашнему дню: "
  "уточнить у Бориса, оставляем ли цену, и ответить сегодня.</div>")
A("<div class='card warn'><b>Романова София (79169105744) — три предмета на 2 сентября.</b> "
  "Подготовка, английский, ИЗО = 23 800 ₽. В разговоре пообещали 10% на каждый абонемент — "
  "подтвердить у Бориса до её прихода завтра.</div>")
A("<div class='card warn'><b>Михавиловы Семён и Алиса (79164713342) — сегодня 16:00, английский.</b> "
  "Приведёт бабушка, мама на работе. После занятия педагог звонит маме с телефона Алисы — "
  "предупредить педагога заранее.</div>")

# --- расписание
A(f"<h2>Кто идёт сегодня — {len(GO)} групп, {n_go} детей</h2>")
A("<div class='q'>«Доброе утро! Это KidsUP. Сегодня в ЧЧ:ММ ждём [имя] на первое занятие. "
  "Бульвар Рокоссовского 6 к1В, 7-й подъезд, 2 этаж, из лифта налево — код от двери 667788#. "
  "Приходите за 10 минут. Будете?»</div>")
A("<div class='scroll'><table><tr><th>Время</th><th>Группа</th><th class='num'>Детей</th><th>Кого ждём</th></tr>")
for g in GO:
    n=len(g["kids"]); mx=g["max"] or 0
    cl="p-red" if n>mx else ("p-amber" if n==mx else "p-gray")
    A(f"<tr><td><b>{g['time']}</b></td><td class='small'>{g['name'][:52]}</td>"
      f"<td class='num'><span class='pill {cl}'>{n}/{mx}</span></td>"
      f"<td class='kids'>{', '.join(k['name'] for k in g['kids'])}</td></tr>")
A("</table></div>")
if LOG:
    A(f"<p class='small'>Плюс логопед Марина — {n_log} индивидуальных: "
      + " · ".join(f"{g['time']} {g['kids'][0]['name']}" for g in LOG) + "</p>")

# --- хвосты Ани
A("<h2>1. Аня: свои клиенты со вчера</h2>")
A("<div class='scroll'><table><tr><th>Клиент</th><th>Телефон</th><th>Что сделать</th></tr>"
  "<tr><td>Романова София</td><td class='ph'>79169105744</td>"
  "<td>Подтвердить приход 2.09 на три предмета, уточнить скидку</td></tr>"
  "<tr><td>Михавиловы</td><td class='ph'>79164713342</td>"
  "<td>Сегодня 16:00, приведёт бабушка — после занятия звонок маме</td></tr>"
  "<tr><td>Беляев Арсений</td><td class='ph'>79689777007</td>"
  "<td>Подобрать группу ПШ 2-й уровень и отписаться — обещали вчера. "
  "Свободных мест два на три группы</td></tr>"
  "<tr><td>79150830157</td><td class='ph'>79150830157</td>"
  "<td>Завести карточку: записан на пробное в субботу 12:00, карточки нет</td></tr>"
  "<tr><td>Астраханцев Филипп</td><td class='ph'>79683280360</td>"
  "<td>Перенос на вторник 8.09, переименовать карточку (сейчас «Кирилл»)</td></tr>"
  "</table></div>")

# --- три списка
A(f"<h2>2. ДОД — {len(DOD)} семей</h2>")
A("<div class='card'>Были у нас на дне открытых дверей 30.08, но до сих пор не записаны "
  "на пробное или записаны без оплаты. Самый тёплый список: они видели центр своими глазами.</div>")
A("<div class='q'>«Здравствуйте! Это KidsUP, вы были у нас на дне открытых дверей. "
  "Как вам [предмет]? Занятия уже идут — могу записать [имя] на эту неделю. Какой день удобнее?»</div>")
A("<div class='scroll'><table><tr><th>Ребёнок</th><th>Телефон</th><th>Был на</th><th>Статус</th></tr>")
for d in DOD:
    cl="p-red" if d["status"]=="нет записи" else "p-amber"
    A(f"<tr><td>{(d['name'] or '')[:26]}</td><td class='ph'>{d['phone'] or '—'}</td>"
      f"<td class='small'>{(d['course'] or '')[:24]}</td>"
      f"<td><span class='pill {cl}'>{d['status']}</span></td></tr>")
A("</table></div>")

A(f"<h2>3. Праздник — {len(PRZ)} семей</h2>")
A("<div class='card'>Тег «Праздник 2026», ни в одной группе не записаны. "
  "Список отсортирован по теплу — идти строго сверху вниз.</div>")
A("<div class='scroll'><table><tr><th>Семья</th><th>Телефон</th><th>Статус в базе</th></tr>")
for p in PRZ[:120]:
    st=ST.get(str(p["state"]), str(p["state"]))
    cl=("p-green" if p["state"] in (125955,125953,125952) else
        "p-amber" if p["state"] in (146950,345767) else
        "p-blue" if p["state"]==345768 else "p-gray")
    A(f"<tr><td>{p['name'][:30]}</td><td class='ph'>{p['phone'] or '—'}</td>"
      f"<td><span class='pill {cl}'>{st}</span></td></tr>")
A("</table></div>")

if S3:
    A(f"<h2>4. Летние и прошлогодние — {len(S3)} семей</h2>")
    A("<div class='card'>Платили нам в 2025/26 или летом 2026, сейчас никуда не записаны. "
      "Возврат своего дешевле любого нового: они знают педагогов, дорогу и цены.</div>")
    A("<div class='q'>«Здравствуйте! Это KidsUP. [Имя] ходил к нам в прошлом году — "
      "учебный год начался, хочу успеть предложить место, пока группа не закрылась. Продолжаем?»</div>")
    A("<div class='scroll'><table><tr><th>Семья</th><th>Телефон</th><th>Разговор</th></tr>")
    for x in S3[:150]:
        cl="p-blue" if x["state"]==345768 else "p-amber"
        A(f"<tr><td>{x['name'][:30]}</td><td class='ph'>{x['phone'] or '—'}</td>"
          f"<td><span class='pill {cl}'>{x['stname']}</span></td></tr>")
    A("</table></div>")

A("<h2>Дожим до абонемента — обеим</h2>")
A("<div class='card ok'><b>−10% на первый абонемент — только в день первого занятия.</b> "
  "Мини-сад и нулевой класс — максимум −5%. Скидки не суммируются.<br>"
  "На стойку: <a href='/static/tablichka_skidka.pdf'>табличка с ценами (PDF)</a> · "
  "педагогам: <a href='/static/diagnostika_blanki.pdf'>бланки диагностики</a> · "
  "родителям: <a href='/static/pasport_psh1.pdf'>паспорт развития ПШ1</a>, "
  "<a href='/static/pasport_psh2.pdf'>ПШ2</a></div>")
A("<ul><li>Ловим на выходе, не звоним вечером</li>"
  "<li>Начинаем с конкретики от педагога, не с цены</li>"
  "<li>Не готов — конкретный час звонка на завтра, записать в лист</li>"
  "<li>Всегда предлагаем второй предмет</li>"
  "<li>Не пришёл — звонок в тот же час, перенос на среду или четверг</li></ul>")
A("<p class='small'>Записи по группам: <a href='/base/gruppy_2627'>/base/gruppy_2627</a> · "
  "английский: <a href='/base/angliyskiy'>/base/angliyskiy</a> · "
  "диагностика: <a href='/base/diagnostika'>/base/diagnostika</a></p>")
A("</div>")
open("/home/user/kidsup/docs/plan_01sen.html","w",encoding="utf-8").write("\n".join(H))
print(f"ok · групп {len(GO)} · детей {n_go} · ДОД {len(DOD)} · Праздник {len(PRZ)} · летние {len(S3)}")
