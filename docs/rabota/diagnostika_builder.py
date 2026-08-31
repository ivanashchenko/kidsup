# -*- coding: utf-8 -*-
"""Бланки диагностики первого занятия: ПШ и английский + лист родителю."""
CSS = """
@page{size:A4;margin:10mm}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:#312783;
font:14px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.sheet{max-width:190mm;margin:0 auto;padding:6mm;page-break-after:always}
.sheet:last-child{page-break-after:auto}
.top{border-bottom:3px solid #1DA7E0;padding-bottom:8px;margin-bottom:12px}
.top h1{font-size:21px;margin:0 0 3px;color:#312783}
.top p{margin:0;font-size:12px;color:#6c6a86}
.fill{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0 14px;font-size:13px}
.fill span{flex:1;min-width:150px;border-bottom:1px solid #c9c6dd;padding-bottom:3px}
h2{font-size:15px;margin:14px 0 6px;color:#1DA7E0;text-transform:uppercase;letter-spacing:.04em}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:#6c6a86;
font-weight:700;padding:5px 4px;border-bottom:2px solid #312783}
td{padding:6px 4px;border-bottom:1px solid #e4e2f0;vertical-align:top}
.c{text-align:center;width:26px}
.box{display:inline-block;width:15px;height:15px;border:1.5px solid #9a97b8;border-radius:3px}
.note{background:#f7fbf0;border-left:4px solid #7DB928;padding:8px 11px;font-size:12px;
margin:10px 0;border-radius:0 6px 6px 0}
.warn{background:#fffaf0;border-left:4px solid #F59C00;padding:8px 11px;font-size:12px;
margin:10px 0;border-radius:0 6px 6px 0}
.lines{margin-top:6px}
.lines div{border-bottom:1px solid #d8d5e8;height:20px}
.lvl{display:flex;gap:8px;margin:8px 0}
.lvl div{flex:1;border:1.5px solid #c9c6dd;border-radius:8px;padding:7px 9px;font-size:12px}
.foot{margin-top:12px;font-size:10px;color:#8b8a9c;border-top:1px solid #e4e2f0;padding-top:6px}
.big{font-size:17px;font-weight:800;color:#7DB928}
.two{display:flex;gap:12px}.two>*{flex:1}
"""
def rows(items):
    return "".join(
        f'<tr><td>{t}</td><td class="c"><span class="box"></span></td>'
        f'<td class="c"><span class="box"></span></td><td class="c"><span class="box"></span></td>'
        f'<td></td></tr>' for t in items)
HEAD = '<tr><th>Что смотрим</th><th class="c">нет</th><th class="c">част.</th>' \
       '<th class="c">да</th><th>Заметка педагога</th></tr>'
def lines(n): return '<div class="lines">' + '<div></div>'*n + '</div>'

PSH = f"""<div class="sheet">
<div class="top"><h1>Диагностика первого занятия — Подготовка к школе</h1>
<p>Заполняет педагог во время занятия. Методика Буракова, ступени ПШ1 (нечитающие) и ПШ2 (читающие)</p></div>
<div class="fill"><span>Ребёнок: </span><span>Возраст: </span><span>Группа: </span><span>Дата: </span></div>

<h2>1. Чтение — определяет ступень</h2>
<table>{HEAD}
{rows(["Узнаёт буквы (называет 10 и больше)",
       "Читает склады (МА, ПО, ТИ) слитно, не по буквам",
       "Читает короткие слова целиком",
       "Читает предложение и понимает смысл",
       "Отвечает на вопрос по прочитанному"])}
</table>
<div class="lvl">
<div><b>ПШ1 — нечитающие.</b> Буквы знает частично, склады не сливает</div>
<div><b>ПШ2 — читающие.</b> Слитно читает склады и слова</div>
</div>

<h2>2. Математика</h2>
<table>{HEAD}
{rows(["Считает до 10 без ошибок","Считает до 20","Соотносит цифру и количество",
       "Сравнивает: больше — меньше","Складывает и вычитает в пределах 5"])}
</table>

<h2>3. Рука и письмо</h2>
<table>{HEAD}
{rows(["Держит карандаш правильно (щепоть)","Обводит по контуру, не выходя за линию",
       "Штрихует в заданном направлении","Копирует образец (фигура, узор)"])}
</table>

<h2>4. Внимание и поведение в группе</h2>
<table>{HEAD}
{rows(["Удерживает задание до конца","Слышит инструкцию с первого раза",
       "Работает в общем темпе группы","Спокойно реагирует на ошибку",
       "Включается в общее дело, не отсиживается"])}
</table>

<h2>5. Речь</h2>
<table>{HEAD}
{rows(["Отвечает предложением, а не одним словом","Речь понятна постороннему",
       "Составляет рассказ по картинке"])}
</table>

<div class="warn"><b>Звуки под вопросом?</b> Отметьте — админ предложит бесплатную
консультацию логопеда. Это не диагноз, а повод посмотреть внимательнее.
{lines(1)}</div>

<h2>Вывод — переписать в лист родителю</h2>
<div class="note"><b>Одна конкретная вещь, которая получилась.</b> Не «молодец», а что именно:
«сама прочитала два слова», «сосчитала до двадцати без запинки».{lines(2)}
<b>Одна вещь, над которой работаем первой.</b>{lines(2)}</div>
<div class="foot">Детский центр KidsUP · б-р Маршала Рокоссовского, 6 к1В · заполненный бланк передать администратору сразу после занятия</div>
</div>"""

ENG = f"""<div class="sheet">
<div class="top"><h1>Диагностика первого занятия — Английский язык</h1>
<p>Заполняет педагог во время занятия. Кембриджская лестница: Pre-A1 Starters → A1 Movers → A1-A2 Flyers</p></div>
<div class="fill"><span>Ребёнок: </span><span>Возраст: </span><span>Группа: </span><span>Дата: </span></div>

<h2>1. Понимание на слух — без перевода</h2>
<table>{HEAD}
{rows(["Выполняет простую инструкцию (stand up, touch, show me)",
       "Понимает вопрос о себе (What's your name? How old are you?)",
       "Узнаёт знакомые слова в речи педагога",
       "Понимает без подсказки на русском"])}
</table>

<h2>2. Словарь — что уже есть</h2>
<table>{HEAD}
{rows(["Цвета","Числа до 10","Животные","Семья","Предметы вокруг (school things)",
       "Действия (run, jump, sing)"])}
</table>

<h2>3. Говорение</h2>
<table>{HEAD}
{rows(["Повторяет за педагогом чётко","Отвечает одним словом",
       "Отвечает фразой (I'm five. It's red.)","Задаёт вопрос сам",
       "Говорит без страха ошибиться"])}
</table>

<h2>4. Чтение и письмо (для 7+)</h2>
<table>{HEAD}
{rows(["Знает английский алфавит","Читает знакомые слова",
       "Пишет своё имя латиницей"])}
</table>

<h2>5. Поведение в группе</h2>
<table>{HEAD}
{rows(["Включается в игру и песни","Держит внимание всё занятие",
       "Не переходит на русский при первой трудности"])}
</table>

<div class="lvl">
<div><b>Pre-A1 Starters</b><br>первый год: слова, простые фразы о себе</div>
<div><b>A1 Movers</b><br>предложения, вопросы, короткий рассказ</div>
<div><b>A1-A2 Flyers</b><br>свободнее говорит, читает и пишет</div>
</div>
<div class="note"><b>Рекомендованный уровень и группа:</b>{lines(1)}
<b>Одна конкретная вещь, которая получилась:</b>{lines(2)}
<b>Над чем работаем первым:</b>{lines(2)}</div>
<div class="foot">Детский центр KidsUP · б-р Маршала Рокоссовского, 6 к1В · заполненный бланк передать администратору сразу после занятия</div>
</div>"""

PARENT = """<div class="sheet">
<div class="top" style="border-color:#7DB928">
<h1>Первое занятие: что мы увидели у вашего ребёнка</h1>
<p>Персональные заметки педагога — по итогам сегодняшнего занятия</p></div>
<div class="fill"><span>Ребёнок: </span><span>Направление: </span><span>Дата: </span></div>

<h2 style="color:#7DB928">Что уже получается</h2>
<div class="lines"><div></div><div></div><div></div></div>

<h2 style="color:#7DB928">С чего начнём</h2>
<div class="lines"><div></div><div></div><div></div></div>

<h2 style="color:#7DB928">Группа и уровень</h2>
<div class="lines"><div></div></div>

<div class="note" style="border-color:#1DA7E0;background:#eef8fd">
<b>Как мы работаем</b><br>
<b>Подготовка к школе</b> — методика Буракова, две ступени: для нечитающих и для тех,
кто уже начал читать. Чтение, математика и письмо в одном занятии, короткими блоками
со сменой деятельности — дети не устают. К школе ребёнок читает, считает и удерживает
внимание 40 минут.<br><br>
<b>Английский</b> — кембриджская лестница Pre-A1 Starters → A1 Movers → A1-A2 Flyers.
Группа набирается не по возрасту, а по уровню: педагог определяет его на первом занятии.
Язык через игру, песни и сценки — дети говорят с первого дня, а не переводят слова.
За первый год около 300 слов и 40 фраз; в группе до 8 детей каждый говорит минимум
12 раз за занятие. В конце года — уровневый тест: виден результат, а не «занимался».
</div>

<div class="note"><b class="big">Первое занятие условно-бесплатное.</b><br>
Не понравилось — платить не нужно. Понравилось — это занятие входит в первый абонемент.
<br><br><b>Сегодня, в день первого занятия, — скидка 10% на первый абонемент.</b>
Завтра она уже не действует. Второй предмет и второй ребёнок — тоже −10%;
скидки не суммируются, действует одна.</div>

<div class="two">
<div class="note" style="border-color:#F59C00;background:#fffaf0">
<b>Что дальше</b><br>
1. Выбираем группу и дни<br>
2. Оформляем абонемент на 8 или 4 занятия<br>
3. Через месяц педагог даёт обратную связь по этим же пунктам</div>
<div class="note" style="border-color:#312783;background:#f4f2fb">
<b>Остались вопросы?</b><br>
Подойдите к администратору на ресепшене или напишите в WhatsApp:<br>
<b>+7 919 968-35-07</b><br>kidsup.ru</div>
</div>
<div class="foot">Детский центр и английский сад KidsUP · б-р Маршала Рокоссовского, 6 к1В ·
БЦ «Богородский», 7-й подъезд, 2 этаж · педагог: ______________________</div>
</div>"""

open("/home/user/kidsup/docs/diagnostika_blanki.html","w",encoding="utf-8").write(
    f"<style>{CSS}</style>" + PSH + ENG + PARENT)
print("бланки готовы")
