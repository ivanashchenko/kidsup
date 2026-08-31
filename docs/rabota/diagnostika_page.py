# -*- coding: utf-8 -*-
CSS="""
:root{--ink:#15132e;--muted:#6c6a86;--line:#e4e2f0;--bg:#f8f7fc;--card:#fff;
--indigo:#312783;--blue:#1DA7E0;--green:#7DB928;--amber:#F59C00;--red:#E30613}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:0 16px 70px}
.hero{background:linear-gradient(135deg,#312783,#1DA7E0);color:#fff;margin:0 -16px 20px;
padding:26px 20px 22px;border-radius:0 0 18px 18px}
.hero h1{margin:0 0 5px;font-size:26px;line-height:1.15}
.hero p{margin:0;opacity:.92;font-size:15px}
h2{font-size:20px;margin:30px 0 10px;color:var(--indigo);border-bottom:2px solid var(--line);padding-bottom:6px}
h3{font-size:16px;margin:18px 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}
.ok{border-left:4px solid var(--green);background:#f7fbf0}
.warn{border-left:4px solid var(--amber);background:#fffaf0}
.blue{border-left:4px solid var(--blue);background:#eef8fd}
.step{display:flex;gap:12px;align-items:flex-start;margin:14px 0}
.step .n{flex:none;width:30px;height:30px;border-radius:50%;background:var(--indigo);color:#fff;
font-weight:800;display:flex;align-items:center;justify-content:center;font-size:15px}
.step .b{flex:1}
.q{background:#f1effb;border-radius:10px;padding:10px 13px;margin:8px 0;font-size:14px;font-style:italic}
ul{margin:8px 0;padding-left:20px}li{margin:5px 0}
.small{font-size:13px;color:var(--muted)}
.dl{display:inline-block;background:var(--green);color:#fff;padding:9px 16px;border-radius:9px;
font-weight:700;text-decoration:none;margin:6px 8px 6px 0}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);
font-weight:600;padding:8px 6px;border-bottom:2px solid var(--line)}
td{padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--ink:#eae8f6;--muted:#a3a1ba;
--line:#343150;--bg:#131228;--card:#1d1b36;--indigo:#ab9ff2}
:root:not([data-theme="light"]) .ok{background:#1e2c15}
:root:not([data-theme="light"]) .warn{background:#332812}
:root:not([data-theme="light"]) .blue{background:#122b39}
:root:not([data-theme="light"]) .q{background:#25233e}}
"""
H=[];A=H.append
A(f"<style>{CSS}</style><div class='wrap'>")
A("<div class='hero'><h1>Диагностика на первом занятии</h1>"
  "<p>Как посмотреть ребёнка, что отдать родителю и почему это продаёт абонемент</p></div>")

A("<div class='card ok'><b>Печатать сейчас:</b> "
  "<a class='dl' href='/static/diagnostika_blanki.pdf'>Бланки диагностики (PDF, 3 листа)</a><br>"
  "<span class='small'>Лист 1 — подготовка к школе (педагогу). Лист 2 — английский (педагогу). "
  "Лист 3 — «Что мы увидели у вашего ребёнка» (родителю). "
  "Третий печатать по числу новых детей в группе.</span></div>")

A("<h2>Зачем это вообще</h2>")
A("<div class='card blue'>Родитель платит не за занятие — за понимание, что с его ребёнком "
  "занимаются осмысленно. Общее «всё хорошо, приходите» не отличает нас ни от кого. "
  "Одна конкретная фраза — «Соня сама прочитала два слова, а слоги ещё сливает через раз, "
  "с этого и начнём» — делает больше, чем любая скидка.<br><br>"
  "Диагностика нужна для трёх вещей сразу: <b>поставить ребёнка в правильную группу</b> "
  "(в английском группы по уровню, не по возрасту), <b>дать педагогу точку отсчёта</b> "
  "и <b>дать администратору конкретику для разговора на выходе</b>.</div>")

A("<h2>Как проводит педагог</h2>")
A("<div class='step'><div class='n'>1</div><div class='b'><b>Не устраивать экзамен.</b> "
  "Диагностика идёт внутри обычного занятия: те же игры, песни, задания. "
  "Ребёнок не должен понять, что его проверяют — иначе зажмётся и покажет хуже, "
  "чем умеет.</div></div>")
A("<div class='step'><div class='n'>2</div><div class='b'><b>Отмечать по ходу, а не после.</b> "
  "Бланк лежит рядом, галочки ставятся сразу. Через час после занятия детали "
  "стираются, и остаётся то самое «всё хорошо».</div></div>")
A("<div class='step'><div class='n'>3</div><div class='b'><b>Главное — две строки в конце.</b> "
  "Что уже получается и над чем работаем первым. Обе — конкретные, про этого ребёнка. "
  "Их родитель унесёт домой и будет пересказывать мужу.</div></div>")
A("<div class='step'><div class='n'>4</div><div class='b'><b>Отдать администратору сразу.</b> "
  "Не в конце дня, а как только группа вышла: у админа есть три минуты, пока родитель "
  "одевает ребёнка.</div></div>")

A("<h3>Подготовка к школе — что смотрим</h3>")
A("<div class='scroll'><table><tr><th>Блок</th><th>Что даёт</th></tr>"
  "<tr><td><b>Чтение</b></td><td>Определяет ступень: ПШ1 для нечитающих, ПШ2 для тех, "
  "кто уже сливает склады. Ошибка здесь — ребёнок весь год скучает или не тянет</td></tr>"
  "<tr><td><b>Математика</b></td><td>Счёт, состав числа, сравнение</td></tr>"
  "<tr><td><b>Рука</b></td><td>Как держит карандаш, обводит, штрихует. "
  "Самое частое, что родители не замечают дома</td></tr>"
  "<tr><td><b>Внимание</b></td><td>Держит ли задание до конца, слышит ли инструкцию с первого раза. "
  "Это то, о чём в школе скажут «не старается»</td></tr>"
  "<tr><td><b>Речь</b></td><td>Отвечает словом или предложением. Звуки под вопросом — "
  "повод предложить консультацию логопеда</td></tr></table></div>")

A("<h3>Английский — что смотрим</h3>")
A("<div class='card'>Здесь диагностика решает главное: <b>в какую группу поставить</b>. "
  "Возраст не показатель — второклассник после «безобразного английского» в школе "
  "может быть слабее дошкольника, который год ходил к нам.<br><br>"
  "Понимание на слух без перевода → словарь → готовность говорить → "
  "для семи лет и старше ещё чтение и письмо. На выходе — уровень: "
  "Pre-A1 Starters, A1 Movers или A1-A2 Flyers.</div>")

A("<h2>Что делает администратор</h2>")
A("<div class='card warn'><b>Лист родителю — это не отчёт, а повод для разговора.</b> "
  "Отдавать его молча бессмысленно. Работает так: админ берёт лист у педагога, "
  "читает две строки вывода и с ними идёт к родителю.</div>")
A("<div class='q'>«[Педагог] передаёт: [конкретика из листа]. Вот, здесь записала — "
  "это ваш лист, забирайте. Мы такой же заполним через месяц, по тем же пунктам, "
  "чтобы было видно движение.<br><br>"
  "Давайте закреплю за [имя] место в этой группе. Абонемент на 8 занятий или на 4? "
  "И сегодня, в день первого занятия, действует −10%.»</div>")
A("<ul><li><b>Сначала лист, потом цена.</b> Родитель должен услышать про своего ребёнка "
  "раньше, чем про деньги</li>"
  "<li><b>Обещание следующего листа через месяц</b> — то, ради чего возвращаются: "
  "видно движение, а не «ходили»</li>"
  "<li><b>Если звуки под вопросом</b> — предложить консультацию логопеда. "
  "Это забота, а не допродажа</li>"
  "<li><b>Не готов сегодня</b> — лист всё равно отдаём, и назначаем конкретный час звонка "
  "на завтра</li></ul>")

A("<h2>Что нужно от вас</h2>")
A("<div class='card warn'>Бланки собраны из нашей методики: Бураков для подготовки, "
  "кембриджская лестница для английского. Но проверять по ним будут педагоги — "
  "покажите им листы до занятия и спросите, что убрать или добавить. "
  "Правки внесу за десять минут.<br><br>"
  "Отдельно стоит решить: <b>кто заполняет лист родителю</b> — педагог сам "
  "или администратор с его слов. Педагогу точнее, администратору быстрее.</div>")
A("</div>")
open("/home/user/kidsup/docs/diagnostika.html","w",encoding="utf-8").write("\n".join(H))
print("страница готова")
