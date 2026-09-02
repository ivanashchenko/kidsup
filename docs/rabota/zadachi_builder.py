# -*- coding: utf-8 -*-
"""Задачи админам на 2 сентября — из разборов звонков и переписок 31.08–01.09."""
CSS="""
:root{--ink:#15132e;--muted:#6c6a86;--line:#e4e2f0;--bg:#f8f7fc;--card:#fff;
--indigo:#312783;--blue:#1DA7E0;--green:#7DB928;--amber:#F59C00;--red:#E30613;
--lena:#7DB928;--anya:#F59C00;--liza:#1DA7E0}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:0 16px 70px}
.hero{background:linear-gradient(135deg,#312783,#1DA7E0);color:#fff;margin:0 -16px 20px;
padding:26px 20px 22px;border-radius:0 0 18px 18px}
.hero h1{margin:0 0 5px;font-size:26px;line-height:1.15}
.hero p{margin:0;opacity:.92;font-size:15px}
h2{font-size:20px;margin:28px 0 10px;color:var(--indigo);border-bottom:2px solid var(--line);padding-bottom:6px}
h3{font-size:16px;margin:18px 0 8px;display:flex;align-items:center;gap:8px}
.dot{width:11px;height:11px;border-radius:50%;flex:none}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);
font-weight:600;padding:8px 6px;border-bottom:2px solid var(--line)}
td{padding:9px 6px;border-bottom:1px solid var(--line);vertical-align:top}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.ph{font-variant-numeric:tabular-nums;white-space:nowrap;font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}
.alarm{border-left:4px solid var(--red);background:#fff5f5}
.warn{border-left:4px solid var(--amber);background:#fffaf0}
.ok{border-left:4px solid var(--green);background:#f7fbf0}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:11px;font-weight:700;white-space:nowrap}
.p-red{background:#fce8e9;color:#9c060f}.p-amber{background:#fdf0dc;color:#94600a}
.p-blue{background:#e4f4fc;color:#12668b}.p-green{background:#eef7e0;color:#4d7511}
.small{font-size:13px;color:var(--muted)}
ul,ol{margin:8px 0;padding-left:20px}li{margin:5px 0}
.chk{width:18px;height:18px;border:2px solid #b9b5d0;border-radius:4px;display:inline-block;
vertical-align:middle;margin-right:6px}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--ink:#eae8f6;--muted:#a3a1ba;
--line:#343150;--bg:#131228;--card:#1d1b36;--indigo:#ab9ff2}
:root:not([data-theme="light"]) .alarm{background:#331519}
:root:not([data-theme="light"]) .warn{background:#332812}
:root:not([data-theme="light"]) .ok{background:#1e2c15}
:root:not([data-theme="light"]) .p-red{background:#3d1416;color:#f38b90}
:root:not([data-theme="light"]) .p-amber{background:#3b2b0e;color:#f2b34c}
:root:not([data-theme="light"]) .p-blue{background:#122f3f;color:#77cdf0}
:root:not([data-theme="light"]) .p-green{background:#26361a;color:#a9d96a}}
"""
# (кто, телефон, что сделать, срочность)
DENGI=[
 ("Бернард Михаил","79096821229","Хочет оплатить сразу ОКТЯБРЬ, НОЯБРЬ и ДЕКАБРЬ. Посчитать и прислать ссылку","срочно"),
 ("Мухаметшин Даниэль","79175686599","Мини-сад ТРИ дня в неделю (вт-ср-пт). Обещали прислать сумму и ссылку в WhatsApp","срочно"),
 ("Юрченко Полина","79851971456","Просит ссылку на ментальную арифметику","срочно"),
 ("Романова София","79169105744","Приходит 2.09 на ТРИ предмета (23 800 ₽). Подтвердить скидку — обещали 10% на каждый абонемент","срочно"),
 ("Беляев","79689777007","Просит цены: подготовка и английский. Плюс подобрать группу ПШ2 — обещали вчера","важно"),
 ("Нариманлы Селин","79013412303","Прислала чек за тетради — подтвердить оплату и выдать комплект","важно"),
]
LYUDI=[
 ("Михавиловы","79164713342","Мама ЖДЁТ с 17:00: можно ли Семёна (9) и Алису (12) в ОДНУ группу вт-чт 16:00. Уточнить у педагога и перезвонить","срочно"),
 ("Нашиков Назар","79150961183","Логопед 3.09 в 18:00 — у мамы собрание в школе. Найти замену на четверг, постоянное время сохранить","срочно"),
 ("79267095054","79267095054","Был вчера в 18:00 на подготовке, карточки в CRM нет. Завести и оформить запись","срочно"),
 ("Романова Лиана","79629025003","Просит перенести на четверг — Дарину забрали из сада с температурой","срочно"),
 ("Власова Варвара","79265733606","РИСК УХОДА из-за смены педагога. В четверг забирает — педагог должен подойти сам с конкретикой","важно"),
 ("79646236761","79646236761","Просили ТЕХНИКУ ЧТЕНИЯ, ответили «нет такого». У нас ЕСТЬ скорочтение. Перезвонить и извиниться","срочно"),
 ("79150830157","79150830157","Записан на пробное в СУББОТУ 12:00, карточки нет. Завести","важно"),
]
DATY=[
 ("Королева Виктория","79096901009","«Вика ходит с 15.09, оплачивала с 15.09» — поправить дату старта в CRM"),
 ("Деева Лада","79772826485","«Я записана на 8.09» — поправить дату старта"),
 ("Лагутина София","79264126957","«Записаны на пробное на четверг 3.09» — сверить с CRM"),
 ("Купцов Матвей","79689581744","ОТКАЗ: «Матвей не будет ходить». Снять с двух групп, проставить причину"),
 ("Астраханцев Филипп","79683280360","Перенос на вторник 8.09. Карточка названа «Кирилл» — переименовать"),
 ("Заявка Елена (англ.)","79263936933","Определить уровень и убрать лишнюю запись — сидит в двух группах английского"),
]
PEREN=[
 ("Пугачев Павел","79153327267","Предупредили о пропуске вторника, ходят дальше — отметить, не считать неявкой"),
 ("Бакланов Семён","79031415124","Заболел, писал на другой номер — отметить болезнь, предложить отработку"),
 ("София","79264173797","«Сегодня не придём» — выяснить причину, перенести"),
 ("Чемоданова Аделина","79778676814","«Не сможем присутствовать» — перенести на конкретный день"),
 ("Мукабенов Матвей","79636362728","«Нужно что-то приносить на занятие?» — ответить: всё выдаём; тетради ПШ 3×500 ₽ после оплаты"),
 ("Ломаховская Дарья","79645259360","«Придём 7 октября» — уточнить направление, поставить напоминание"),
]
H=[];A=H.append
A(f"<style>{CSS}</style><div class='wrap'>")
A("<div class='hero'><h1>Задачи админам — среда 2 сентября</h1>"
  "<p>Собрано из разборов звонков и переписок за 31.08 и 01.09<br>"
  "В смене Лена и Ира · Лиза на переписке</p></div>")

A("<div class='card alarm'><b>Сначала — четыре, где люди уже ждут.</b> "
  "Михавиловы ждут с вчерашних 17:00, Бернард хочет отдать деньги за три месяца, "
  "Мухаметшин ждёт счёт, Романова приходит сегодня и ей обещали скидку на три предмета.</div>")

A("<h2>Деньги — сегодня</h2>")
A("<div class='scroll'><table><tr><th></th><th>Кто</th><th>Телефон</th><th>Что сделать</th></tr>")
for n,p,t,s in DENGI:
    cl="p-red" if s=="срочно" else "p-amber"
    A(f"<tr><td><span class='chk'></span></td><td><b>{n}</b><br>"
      f"<span class='pill {cl}'>{s}</span></td><td class='ph'>{p}</td><td>{t}</td></tr>")
A("</table></div>")

A("<h2>Люди ждут ответа</h2>")
A("<div class='scroll'><table><tr><th></th><th>Кто</th><th>Телефон</th><th>Что сделать</th></tr>")
for n,p,t,s in LYUDI:
    cl="p-red" if s=="срочно" else "p-amber"
    A(f"<tr><td><span class='chk'></span></td><td><b>{n}</b><br>"
      f"<span class='pill {cl}'>{s}</span></td><td class='ph'>{p}</td><td>{t}</td></tr>")
A("</table></div>")

A("<h2>Поправить в CRM — иначе будут звать не тех</h2>")
A("<div class='card warn'>Вчерашняя рассылка вскрыла: у трёх семей в базе стоит не та дата "
  "старта, они оплатили и договорились на другое число. Плюс отказ и две карточки "
  "с чужими именами.</div>")
A("<div class='scroll'><table><tr><th></th><th>Кто</th><th>Телефон</th><th>Что сделать</th></tr>")
for n,p,t in DATY:
    A(f"<tr><td><span class='chk'></span></td><td><b>{n}</b></td>"
      f"<td class='ph'>{p}</td><td>{t}</td></tr>")
A("</table></div>")

A("<h2>Переносы, болезни, вопросы</h2>")
A("<div class='scroll'><table><tr><th></th><th>Кто</th><th>Телефон</th><th>Что сделать</th></tr>")
for n,p,t in PEREN:
    A(f"<tr><td><span class='chk'></span></td><td><b>{n}</b></td>"
      f"<td class='ph'>{p}</td><td>{t}</td></tr>")
A("</table></div>")

A("<h2>Что не забыть по ходу дня</h2>")
A("<ul>"
  "<li><b>ИЗО стартует сегодня</b> — 13 детей, для них это первое занятие. "
  "Позвонить и подтвердить с утра</li>"
  "<li><b>Дожим до абонемента.</b> За два дня 25 записей на пробное и только 3 оплаты. "
  "Скидка 10% действует в день первого занятия — называть её на выходе, а не «потом позвоним»</li>"
  "<li><b>Три списка обзвона</b> почти не тронуты: ДОД 35 семей, Праздник 69, летние 206</li>"
  "<li><b>Отвечать в переписке в течение часа.</b> Вчера 12 человек ждали дольше; "
  "одна мама писала «очень жду ответ», потому что не знала, забирать ли ребёнка из сада</li>"
  "</ul>")

A("<h2>Ждут решения владельца</h2>")
A("<div class='card'><ul class='small'>"
  "<li><b>Робототехника</b> — 11 заявок, четверо спрашивали за два дня, расписания нет</li>"
  "<li><b>Уход Екатерины</b> — четыре случая потери. Нужна рассылка её семьям</li>"
  "<li><b>Скорочтение и каллиграфия</b> — есть в прайсе, групп нет</li>"
  "<li><b>ПШ2 переполнена</b> — два свободных места на три группы</li>"
  "<li><b>Телефония</b> — 7 неудачных дозвонов за день, один клиент набирал пять раз</li>"
  "<li><b>Гарантия «читает через 3 месяца»</b> — звучит в разговорах, нигде не описана</li>"
  "<li><b>Материнский капитал</b> — клиенту ответили «возможно», у нас нет такой опции</li>"
  "</ul></div>")
A("</div>")
open("/home/user/kidsup/docs/zadachi_02sen.html","w",encoding="utf-8").write("\n".join(H))
print("ok:", len(DENGI)+len(LYUDI)+len(DATY)+len(PEREN), "задач")
