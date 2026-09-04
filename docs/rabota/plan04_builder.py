# -*- coding: utf-8 -*-
"""План на пятницу 04.09 для Ани и Иры: проблемы и прогресс четырёх дней 31.08–03.09,
срочные поручения Бориса (робототехника 1/2 раза в неделю, шахматы и робототехника
сегодня не идут), дожим, реактивация, хвосты инбокса."""
import json, re, html as H, collections, base64, urllib.request
BASE = "/home/user/kidsup/docs/rabota"
D = json.load(open(f"{BASE}/den_0409.json"))
Dn = json.load(open(f"{BASE}/den_0309.json"))
U = {int(k): v for k, v in D["users"].items()}
PAYS = {int(k): v for k, v in D["pays"].items()}
CSS = re.search(r"<style>.*?</style>", open("/home/user/kidsup/docs/plan_03sen.html", encoding="utf-8").read(), re.S).group(0)
CSS = CSS.replace("</style>", ".w-i{background:#E30613}.prob td:first-child{width:34%}</style>")
esc = lambda x: H.escape(str(x or ""))
A_ = "<span class='w w-a'>Аня</span>"; I_ = "<span class='w w-i'>Ира</span>"; Z_ = "<span class='w w-z'>Лиза</span>"; B_ = "<span class='w w-b'>Борис</span>"; L_ = "<span class='w w-l'>Лена</span>"
short = lambda g: re.sub(r"^2627_", "", g or "")

def api(path):
    req = urllib.request.Request("https://app.kidsup.ru" + path, headers={"Authorization": "Basic " + base64.b64encode(b"admin:CGWstart8*").decode()})
    return json.load(urllib.request.urlopen(req, timeout=30))
inbox03 = api("/api/plan/inbox?day=2026-09-03")
inbox03 = inbox03 if isinstance(inbox03, list) else inbox03.get("items", [])
open03 = [x for x in inbox03 if not x.get("done")]

# --- воронка четырёх дней ---------------------------------------------------
trial = [r for r in D["recs"] if r["test"]]
by = {}
for r in sorted(trial, key=lambda r: (r["day"], r["time"])):
    t = by.setdefault(r["uid"], {"day": r["day"], "time": r["time"], "subj": set(), "visit": False, "n": 0})
    t["subj"].add(r["subject"]); t["visit"] |= r["visit"]; t["n"] += 1
came = lambda u: by[u]["visit"] or bool(U[u]["paid"])
paid = lambda u: bool(U[u]["paid"])
N = len(by); C = sum(1 for u in by if came(u)); P = sum(1 for u in by if paid(u))
hot = [u for u in by if came(u) and not paid(u) and not U[u]["next"]]
warm = [u for u in by if came(u) and not paid(u) and U[u]["next"]]
lost = [u for u in by if not came(u) and not U[u]["next"]]
lost_re = [u for u in by if not came(u) and U[u]["next"]]
unmarked = [u for u in by if not by[u]["visit"] and not paid(u)]
money_all = int(sum(x["summa"] for v in PAYS.values() for x in v))
money_trial = int(sum(x["summa"] for u in by for x in U[u]["paid"]))
days = collections.OrderedDict()
for u, t in by.items():
    d = days.setdefault(t["day"], {"n": 0, "c": 0, "p": 0})
    d["n"] += 1; d["c"] += came(u); d["p"] += paid(u)
DN = {"2026-08-31": "пн 31.08", "2026-09-01": "вт 01.09", "2026-09-02": "ср 02.09", "2026-09-03": "чт 03.09"}

# --- сегодня -----------------------------------------------------------------
L = D["lessons"]
n_kids = sum(len(l["kids"]) for l in L)
trials_today = [(l["time"], l["group"], k) for l in L for k in l["kids"] if k["test"]]
first_uniq = {(k["name"], k["phone"]) for _, _, k in trials_today}
chess_today = [(l["time"], l["group"], k) for l in L for k in l["kids"] if "ШАХ" in l["group"]]
robo_today = [(l["time"], l["group"], k) for l in L for k in l["kids"] if "обот" in l["group"]]
chess_lessons = [l for l in L if "ШАХ" in l["group"]]

# --- робототехника и шахматы -------------------------------------------------
JS = {2: "учится", 1: "отказался", 4: "переведён", 5: "отработка", 58132: "записан на пробное", 58131: "посетил", 50509: "новая заявка", 99336: "не пришёл", "record": "запись"}
robo = {}
for x in D["robo"]:
    if x["status"] in (1, 4): continue
    robo.setdefault(x["phone"] or x["name"], x)
robo = sorted(robo.values(), key=lambda x: x["name"] or "")
chess_j = {}
for x in D["chess"]:
    if x["status"] in (1, 4): continue
    k = (x["uid"], x["group"])
    c = chess_j.setdefault(k, {**x, "dates": []})
    if x.get("date"): c["dates"].append(f"{x['date'][5:]} {x.get('time', '')}")
chess_rows = sorted(chess_j.values(), key=lambda x: (x["group"], x["name"] or ""))

# --- реактивация: следующие 40 ------------------------------------------------
R = Dn["react"]
def prio(x):
    a = x["age"] or 0
    return (0 if 4 <= a <= 7 else (1 if 1.3 <= a <= 4 else 2), 0 if x["state"] == 146950 else 1, x["name"] or "")
Rs = sorted([x for x in R if x["phone"]], key=prio)
R40 = Rs[40:80]

# --- заполненность ------------------------------------------------------------
G = [g for g in D["groups"] if g["subject"] != "логопед"]
live = lambda g: g["учится"] + g["посетил"] + g["записался"]
cap = sum(g["max"] or 0 for g in G); filled = sum(live(g) for g in G)
empty = sorted([g for g in G if live(g) <= 2], key=lambda g: g["name"])
full = [g for g in G if g["max"] and live(g) >= g["max"]]

h = [CSS, "<div class='wrap'>",
     "<div class='hero'><h1>Пятница 4 сентября — Аня и Ира</h1>"
     "<p>Работаем 9:00–20:00 · Аня — дожим, оплаты и разговоры на выходе · Ира — телефон, дверь, отметки в CRM и окно 13:00–16:00 под списки · Лиза — переписка<br>"
     f"Сегодня {sum(1 for l in L if l['kids'])} групп, {n_kids} детей, из них {len(first_uniq)} на первом занятии</p></div>"]

# --- срочное от Бориса ----------------------------------------------------------
h.append("<div class='card alarm'><b>Три поручения Бориса на сегодняшнее утро — до 12:00.</b><ol>"
         "<li>" + I_ + " <b>Робототехника: обзвонить все заявки</b> (список ниже) и уточнить у каждого — <b>1 раз в неделю (4 занятия в месяц, 6 400 ₽) или 2 раза (8 занятий, 10 400 ₽)</b>. "
         "Сказать: сегодня занятия по робототехнике <b>нет</b>, старт <b>с воскресенья 6 сентября</b>; пробное 1 600 ₽, разовое 2 300 ₽. Ответ «1» или «2» — в комментарий заявки в CRM и в инбокс одной строкой «робототехника: N семей по 1 разу, M по 2».</li>"
         "<li>" + I_ + " <b>Шахматы: сегодня занятий нет.</b> На 18:00 записаны Прокопович Георгий, Гасанова Айлин, Кузина Агата, Боброва Анна, на 19:00 — Прокопович Владимир. <b>Четверым с пробным вчера в 18:00 уже ушло напоминание «ждём завтра»</b> — поэтому звонить первым делом, до 10:00, иначе приедут. Текст: «Группы по шахматам мы ещё набираем, как только соберётся — позвоним и назначим первое занятие». Не обещать дату. Записи на сегодня в CRM снять, чтобы вечером не ушло напоминание.</li>"
         "<li>" + I_ + " <b>Шахматы в воскресенье</b> (Гасанова Айлин ждёт ответа «хотим в воскресенье») — тот же ответ: группы набираем; " + B_ + " если воскресные шахматы 6.09 тоже не идут — сказать до 12:00, чтобы Ира не звала.</li></ol></div>")

# робототехника — список
h.append(f"<h2>Робототехника — {len(robo)} заявок {I_}</h2>")
h.append("<div class='q'>«Здравствуйте, это Ира из KidsUP. Вы оставляли заявку на робототехнику для [имя]. Группы стартуют с воскресенья 6 сентября. Подскажите, вам удобнее 1 раз в неделю — это 4 занятия в месяц за 6 400 ₽, или 2 раза — 8 занятий за 10 400 ₽? Первое занятие пробное, 1 600 ₽: ребёнок соберёт первого робота и всё поймёт сам».</div>")
h.append("<div class='scroll'><table><tr><th></th><th class='kto'>Кто</th><th>Ребёнок</th><th>Возраст</th><th>Телефон</th><th>Статус заявки</th><th>1 раз / 2 раза</th></tr>")
for x in robo:
    h.append(f"<tr><td><span class='chk'></span></td><td class='kto'>{I_}</td><td><b>{esc(x['name'])}</b></td><td>{esc(x['age'] if x['age'] is not None else '—')}</td><td class='ph'>{esc(x['phone'])}</td><td><span class='pill p-gray'>{esc(JS.get(x['status'], x['status']))}</span></td><td class='small'>{esc(x['comment'][:60])}</td></tr>")
h.append("</table></div>")
if robo_today:
    h.append("<div class='card warn'>В CRM на сегодня стоят занятия по робототехнике: " + ", ".join(f"{esc(k['name'])} {esc(tm)}" for tm, g, k in robo_today) + " — снять записи.</div>")

# шахматы — список
h.append(f"<h2>Шахматы — кто записан и кого предупредить {I_}</h2>")
if chess_lessons:
    h.append("<div class='card warn'>В CRM на сегодня стоят занятия по шахматам: " + " · ".join(f"<b>{esc(l['time'])}</b> {esc(short(l['group']))} — {len(l['kids'])} детей" for l in chess_lessons) +
             ". Занятия не будет: позвонить каждому до 12:00, записи на сегодня снять, занятие в CRM отменить (иначе вечером уйдёт напоминание и семьи приедут).</div>")
h.append("<div class='scroll'><table><tr><th></th><th class='kto'>Кто</th><th>Ребёнок</th><th>Телефон</th><th>Группа</th><th>Статус</th><th>Записи на занятия</th></tr>")
for x in chess_rows:
    st = JS.get(x["status"], x["status"])
    h.append(f"<tr><td><span class='chk'></span></td><td class='kto'>{I_}</td><td><b>{esc(x['name'])}</b></td><td class='ph'>{esc(x['phone'])}</td><td class='small'>{esc(short(x['group']))}</td><td><span class='pill p-gray'>{esc(st)}</span></td><td class='small'>{esc(', '.join(x['dates']) or '—')}</td></tr>")
h.append("</table></div>")
h.append("<div class='q'>«Здравствуйте, это Ира из KidsUP. [Имя] записан у нас на шахматы. Группы мы ещё набираем — сегодня занятия не будет, как только группа соберётся, я позвоню и назначим первое занятие. Записывать пока никуда не надо, место за вами». " + B_ + " — из «Прокопович Георгий, 11 лет, умеет играть» и Гасановой можно собрать группу продолжающих: сейчас там 3 живые записи из 8.</div>")

# --- метрика четырёх дней -------------------------------------------------------
h.append("<h2>Где мы после четырёх дней (31.08–03.09)</h2>")
h.append("<div class='kpi'>"
         f"<div class='k'><div class='v'>{N}</div><div class='l'>детей на пробном за 4 дня</div></div>"
         f"<div class='k'><div class='v {'good' if C/N >= .5 else 'bad'}'>{round(100*C/N)}%</div><div class='l'>дошли: {C}. Норма — выше 50%</div></div>"
         f"<div class='k'><div class='v {'good' if P/max(C,1) >= .4 else 'bad'}'>{round(100*P/max(C,1))}%</div><div class='l'>купили из дошедших: {P}. Порог — 40%</div></div>"
         f"<div class='k'><div class='v'>{money_trial:,}</div><div class='l'>₽ от семей с пробного (всего оплат {money_all:,} ₽)</div></div>".replace(",", " ") +
         f"<div class='k'><div class='v'>{len(hot)+len(warm)}</div><div class='l'>дошли и не купили — дожим Ани</div></div>"
         f"<div class='k'><div class='v'>{len(lost)}</div><div class='l'>не дошли, записи нет — Ира</div></div>"
         "</div>")
h.append("<div class='scroll'><table><tr><th>День</th><th class='num'>На пробном</th><th class='num'>Дошли</th><th class='num'>Купили</th><th class='num'>Купили из дошедших</th></tr>" +
         "".join(f"<tr><td>{DN.get(d, d)}</td><td class='num'>{v['n']}</td><td class='num'>{v['c']}</td><td class='num'>{v['p']}</td><td class='num'><b>{round(100*v['p']/max(v['c'],1))}%</b></td></tr>" for d, v in days.items()) +
         f"<tr><td><b>Итого</b></td><td class='num'><b>{N}</b></td><td class='num'><b>{C}</b></td><td class='num'><b>{P}</b></td><td class='num'><b>{round(100*P/max(C,1))}%</b></td></tr></table></div>")
h.append(f"<div class='card'>Заполненность групп без логопеда: <b>{filled} из {cap} мест — {round(100*filled/cap)}%</b>. Полных — {len(full)}, с одним-двумя детьми — {len(empty)}. "
         f"Записей за четыре дня без отметки явки — <b>{len(unmarked)}</b>: они считаются «не дошли», и дожим идёт по неполному списку. <a href='/base/itog_3dnya'>Итог трёх дней и стратегия</a>.</div>")

# --- проблемы четырёх дней --------------------------------------------------------
h.append("<h2>Проблемы четырёх дней — и что с каждой делаем сегодня</h2>")
PROB = [
 ("Явку не отмечают сразу после занятия", f"{len(unmarked)} записей за 4 дня без отметки. Из-за этого 03.09 шестеро вечерних числились «не дошли», а дожим шёл по неполному списку.",
  I_ + " Отметка явки — в первые 5 минут после каждого занятия, сегодня по всем 23 группам. Утром проставить вчерашний вечер (18:00–19:00)."),
 ("Ложное напоминание 01.09 — 22 семьям из состава групп, а не по записям", "Семьи, записанные на другой день, получили «ждём вас сегодня». Извинение отправлено 03.09, Деева и ещё несколько написали в чат.",
  Z_ + " Если спрашивают «нам приходить?» — отвечать по записи в CRM, не по группе. Автоматика теперь шлёт только тем, у кого есть запись на дату."),
 ("Переписка без ответа по 6–13 часов", "Литовченко ждала с 6:44, Батманова с 13:39, Гасанова с 10:32, МА-лид с 15:36, Демьяненко с 15:22. Двое ответили «Да» на предложения, которые никто не оформил (Цуцкова, Козлова Екатерина).",
  Z_ + " Правило: ответ в 1 час днём, 15 минут с 16:00. Каждое «да» клиента = запись в CRM в тот же час, не «завтра». Хвосты — в блоке «Появилось за день» внизу."),
 ("Договорились по телефону — карточки и записи нет", "Марушин Артём (пробное вт 8.09 16:00), Прокопович Георгий, «Левы завтра не будет» с незнакомого номера, Сорокина Полина (АЯ вт-чт 16:00 с 8.09 — мама во вторник платит), Наталья с малышом 1,3 г.",
  I_ + " Карточка заводится во время разговора, запись — сразу. Сегодня закрыть все пять из списка (внизу, инбокс 03.09)."),
 ("Жалобы", "Яфясовы: обещали «бесплатную диагностику» логопеда, а стоит 2 700 ₽, логопеда в 17:40 не было. Михавиловы: двоим детям разный уровень, маме нужно в одно время — «не понравилось». Деева — ложное напоминание.",
  B_ + " Яфясовы — решение Бориса (диагностика бесплатно или нет). " + A_ + " Михавиловы: мама перезвонит — заранее приготовить вариант «два ребёнка в одно время в соседних кабинетах» (ПШ1 + ПШ2 пн-чт 18:00)."),
 ("Робототехника: две группы набраны, расписания не было", "Заявки лежат с прошлой недели, админы ждали расписание, чтобы обзвонить.",
  I_ + " Сегодня обзвон всех заявок: 1 или 2 раза в неделю, старт с вс 6.09 — первый блок этой страницы."),
 ("Спрос, под который нет продукта", "Танцы (Мамедова, Сеничева), скорочтение и каллиграфия, сад полного дня спрашивают по два раза в день, вечерние группы после 19:00.",
  I_ + " Всё — в группу «Заявки» в CRM с направлением и возрастом, не терять. " + B_ + " — расписание танцев и скорочтения."),
 ("Задачи в МойКлассе разъехались", "807 незакрытых задач, часть дублировала друг друга; 03.09 все закрыты, автосоздание выключено.",
  A_ + " " + I_ + " Единственный список дел — эта страница и блок «Появилось за день» внизу. Сделали — жмите «готово». В МойКлассе задач больше нет и не будет."),
 ("Полупустые группы рядом с переполненными", f"{len(empty)} групп с одним-двумя детьми (в основном ПШ и АЯ вт-чт), {len(full)} полных. Утро (9:00–12:00) даёт конверсию 90%, но туда почти не записывают.",
  I_ + " В окне реактивации звать в конкретные полупустые слоты (список групп в конце). " + B_ + " — решение о слиянии ПШ до 10–11 групп."),
]
h.append("<div class='scroll'><table class='prob'><tr><th>Проблема</th><th>Что было</th><th>Сегодня</th></tr>" +
         "".join(f"<tr><td><b>{p}</b></td><td class='small'>{w}</td><td class='small'>{t}</td></tr>" for p, w, t in PROB) + "</table></div>")

# --- роли -------------------------------------------------------------------------
h.append("<h2>Кто что делает</h2><div class='who'>")
h.append("<div class='wcard anya'><div class='nm'>Аня — дожим, оплаты, разговоры на выходе</div><div class='rl'>Смена под деньги: один дожим = 600 ₽ бонуса. Телефон утром берёт Ира</div>"
         "<div class='tl'>"
         "<b class='t'>9:00–11:00</b><span>Кто ждёт с вечера: Бернард — папа с сыном к 18:00, принять 1 500 ₽ за тетради на стойке; Бугрова — подтвердить в чате сдвоенное пн 7.09; Михавиловы — заготовить вариант на двоих в одно время. Сорокина Полина — оформить АЯ вт-чт 16:00 с 8.09 и посчитать со скидкой −10% на второго ребёнка.</span>"
         f"<b class='t'>11:00–13:00</b><span>Подтвердить {len(first_uniq)} детей на первом занятии сегодня (список ниже): −10% в день первого занятия, оплата на стойке.</span>"
         f"<b class='t'>13:00–16:00</b><span>Дожим по единой форме: {len(hot)} «пришли, не купили» — сначала у педагога реакция ребёнка и рекомендация; {len(warm)} «записаны дальше» — подтвердить дату, закрывать на занятии.</span>"
         "<b class='t'>16:00–20:00</b><span><b>Только разговоры с родителями на выходе</b> — девять полей по каждому, оплата здесь и сейчас, ответ семьи в комментарий карточки. Первые занятия сегодня — вечерние, это главные деньги дня.</span>"
         "</div></div>")
h.append("<div class='wcard' style='border-top-color:#E30613'><div class='nm'>Ира — телефон, дверь, CRM и окно 13:00–16:00</div><div class='rl'>Смена под порядок: карточки, записи, отметки. Ничего не остаётся «на словах»</div>"
         "<div class='tl'>"
         "<b class='t'>9:00–12:00</b><span><b>Поручения Бориса:</b> робототехника — 1 или 2 раза в неделю, старт с вс; шахматы — сегодня не идут, группы набираем, записи на сегодня снять. Проставить явку за вчерашний вечер. Хвосты 03.09: Марушин — карточка + запись вт 8.09 16:00; Прокопович Георгий — карточка; Наталья/«савелий» — 8 или 10.09; «Маша» 12:00 — подтвердить; Батманова — подобрать АЯ вт-чт; Литовченко — утро; Цуцкова и Козлова Екатерина — что значит «Да», оформить.</span>"
         f"<b class='t'>13:00–16:00</b><span><b>Окно реактивации — следующие 40 бывших плательщиков</b> (вчерашние 40 были первыми по списку; сегодня — 41–80). Звать в конкретный полупустой слот. Недозвон — комментарий в карточке, догон шлёт автопилот.</span>"
         f"<b class='t'>16:00–20:00</b><span>Встречает, провожает, <b>отмечает явку сразу</b>. Родителей первого занятия подводит к Ане. В паузах — {len(lost)} «не дошли, записи нет»: одно предложение с днём и временем.</span>"
         "</div></div>")
h.append("<div class='wcard' style='border-top-color:var(--blue)'><div class='nm'>Лиза — переписка</div><div class='rl'>Час днём, 15 минут с 16:00 до 20:00. Каждое «да» — в CRM в тот же час</div>"
         "<ol class='small'><li>" + Z_ + " Демьяненко — передать Ирине Семёновне, чтобы прислала фото через нас (у мамы не работает Telegram)</li>"
         "<li>" + Z_ + " Бугрова — подтвердить в чате: пн 7.09 17:00 + 18:00 в счёт 16.09</li>"
         "<li>" + Z_ + " Архангельская — написать маме: ждём в пн 7.09 18:00, ПШ2 читающие, педагог Татьяна</li>"
         "<li>" + Z_ + " Соискатель Олеся — ответить по вакансии (" + B_ + " решение: звать на собеседование или нет)</li>"
         "<li>" + Z_ + " Никому не писать «это автоматическая рассылка». Отвечать по существу и по записи в CRM</li></ol></div>")
h.append("</div>")

# --- первое занятие сегодня ---------------------------------------------------------
n_first_real = len({(k["name"], k["phone"]) for _, g, k in trials_today if "ШАХ" not in g})
h.append(f"<h2>Сегодня на первом занятии — {n_first_real} {A_} {I_} <span class='small'>(ещё {len(first_uniq)-n_first_real} были записаны на шахматы — не идут)</span></h2>")
h.append("<div class='q'>«Доброе утро! Это KidsUP. Сегодня в ЧЧ:ММ ждём [имя] на первое занятие — оно условно-бесплатное и с диагностикой. Бульвар Рокоссовского 6 к1В, 7-й подъезд, домофон 12, 2 этаж, из лифта налево, код 667788#. Ориентир — магазин «Дикси», от него по лестнице наверх. Приходите за 10 минут. В день первого занятия −10% на абонемент, оплатить можно на стойке. Будете?»</div>")
h.append("<div class='scroll'><table><tr><th></th><th class='kto'>Кто</th><th>Время</th><th>Группа</th><th>Ребёнок</th><th>Телефон</th></tr>")
seen = set()
for tm, g, k in trials_today:
    if (k["name"], tm) in seen: continue
    seen.add((k["name"], tm))
    if "ШАХ" in g:
        h.append(f"<tr><td><span class='chk'></span></td><td class='kto'>{I_} <b>занятия нет — позвонить до 10:00</b></td><td><b>{esc(tm)}</b></td><td class='small'>{esc(short(g))} — <b>НЕ ИДЁТ</b></td><td><b>{esc(k['name'])}</b></td><td class='ph'>{esc(k['phone'])}</td></tr>")
        continue
    h.append(f"<tr><td><span class='chk'></span></td><td class='kto'>{A_} подтверждает<br>{I_} встречает, отмечает явку</td><td><b>{esc(tm)}</b></td><td class='small'>{esc(short(g))}</td><td><b>{esc(k['name'])}</b></td><td class='ph'>{esc(k['phone'])}</td></tr>")
h.append("</table></div>")

# --- дожим ------------------------------------------------------------------------
def trow(u, who):
    t = by[u]; x = U[u]
    return (f"<tr><td><span class='chk'></span></td><td class='kto'>{who}</td><td>{t['day'][5:]} {t['time']}</td><td><b>{esc(x['name'])}</b></td>"
            f"<td class='ph'>{esc(x['phone'])}</td><td class='small'>{esc('/'.join(sorted(t['subj'])))}</td></tr>")
h.append(f"<h2>Пришли и не купили, записи дальше нет — {len(hot)} {A_}</h2>")
h.append("<div class='card warn'><b>Перед звонком — у педагога:</b> реакция ребёнка и рекомендация. Без этого звонок превращается в «ну что решили».</div>")
h.append("<div class='scroll'><table><tr><th></th><th class='kto'>Кто</th><th>Были</th><th>Ребёнок</th><th>Телефон</th><th>Направление</th></tr>" + "".join(trow(u, A_) for u in sorted(hot, key=lambda u: by[u]["day"])) + "</table></div>")
h.append("<div class='q'>«Здравствуйте! Это Аня из KidsUP. [Имя] был у нас на первом занятии — педагог отметил, что … Мы держим место в группе до понедельника; закрепить абонементом? Скидка 10% действует до конца недели, оплатить можно по ссылке или на стойке».</div>")
h.append(f"<h3>Записаны дальше, оплаты нет — {len(warm)}</h3><div class='card'>{A_} <span class='small'>" +
         " · ".join(f"<b>{esc(U[u]['name'])}</b> {esc(U[u]['phone'])} → {esc(U[u]['next'])}" for u in sorted(warm, key=lambda u: U[u]['next'] or '')) +
         ". Подтвердить дату, предупредить про −10% в день занятия, на занятии закрывать.</span></div>")

# --- реактивация ---------------------------------------------------------------------
h.append(f"<h2>Окно реактивации 13:00–16:00 — семьи 41–80 из списка бывших плательщиков {I_}</h2>")
h.append("<div class='card'><span class='small'>Вчера Аня шла по первым 40; если кого-то из них не набрали — сначала добить их (пометки в карточках). "
         "Скрипт: «Здравствуйте, это Ира из KidsUP. [Имя] ходил к нам в прошлом году на …; группы стартовали, для его возраста есть место в … в … Записать на первое занятие — понедельник 18:00 или вторник 17:00?» Недозвон — комментарий, WhatsApp и СМС уйдут сами.</span></div>")
h.append("<div class='scroll'><table><tr><th></th><th class='kto'>Кто</th><th>Ребёнок</th><th>Возраст</th><th>Телефон</th><th>Статус</th><th>Куда звать</th></tr>")
ST = {345768: "недозвон", 146950: "думает"}
for x in R40:
    a = x["age"]
    where = "ПШ1 вт-пт 16:00/17:00, пн-чт 16:00, ср-пт 18:00 — там по 1–2 ребёнка" if a and 4 <= a <= 7 else ("Первая школа вт-чт 11:00 (1 ребёнок), вс 10:00/11:00" if a and a < 4 else ("АЯ вт-чт 19:00 Movers (1 ребёнок), ИЗО пн-ср 16:00/19:00" if a and a > 7 else "уточнить возраст"))
    h.append(f"<tr><td><span class='chk'></span></td><td class='kto'>{I_}</td><td><b>{esc(x['name'])}</b></td><td>{esc(a if a is not None else '—')}</td><td class='ph'>{esc(x['phone'])}</td><td><span class='pill p-gray'>{ST.get(x['state'], '')}</span></td><td class='small'>{where}</td></tr>")
h.append("</table></div>")

# --- не дошли ---------------------------------------------------------------------------
h.append(f"<h2>Не дошли на пробное и никуда не записаны — {len(lost)} {I_}</h2>")
h.append(f"<div class='card'><span class='small'>Сначала сверить отметку явки — часть из них просто не отмечена. Остальным одно конкретное предложение: открытый урок на следующей неделе, диагностика или экскурсия. Ещё {len(lost_re)} не дошли, но уже перезаписаны — им ничего не надо, кроме напоминания.</span></div>")
h.append("<div class='scroll'><table><tr><th></th><th class='kto'>Кто</th><th>Были записаны</th><th>Ребёнок</th><th>Телефон</th><th>Направление</th></tr>" + "".join(trow(u, I_) for u in sorted(lost, key=lambda u: by[u]["day"])) + "</table></div>")

# --- хвосты инбокса 03.09 -----------------------------------------------------------------
h.append(f"<h2>Незакрытое со вчера — {len(open03)} пунктов из «Появилось за день» 03.09</h2>")
h.append("<div class='card'><span class='small'>Сегодняшний блок «Появилось за день» — внизу страницы, он живой. Здесь — то, что осталось незакрытым вчера; сделали — нажмите «готово» на <a href='/base/plan_03sen'>странице 03.09</a>.</span></div>")
WHO = {"Аня": A_, "Ира": I_, "Лиза": Z_, "Борис": B_, "Лена": L_}
h.append("<div class='scroll'><table><tr><th></th><th class='kto'>Кто</th><th>Что</th><th>Телефон</th></tr>")
for x in sorted(open03, key=lambda x: x.get("who", "")):
    who = x.get("who", "")
    if who == "Лена": who_pill = A_ + " <span class='small'>(за Лену)</span>"
    else: who_pill = WHO.get(who, esc(who))
    h.append(f"<tr><td><span class='chk'></span></td><td class='kto'>{who_pill}</td><td class='small'>{esc(x.get('text'))}</td><td class='ph'>{esc(x.get('phone'))}</td></tr>")
h.append("</table></div>")

# --- расписание ------------------------------------------------------------------------------
h.append(f"<h2>Кто идёт сегодня — {sum(1 for l in L if l['kids'])} групп, {n_kids} детей</h2>")
h.append("<div class='scroll'><table><tr><th class='kto'>Кто</th><th>Время</th><th>Группа</th><th class='num'>Детей</th><th>Кого ждём (★ — первое занятие)</th></tr>")
for l in L:
    if not l["kids"]: continue
    kids = ", ".join(("★ " if k["test"] else "") + esc(k["name"]) for k in l["kids"])
    cancelled = "ШАХ" in l["group"] or "обот" in l["group"]
    pill = "p-red" if cancelled else ("p-amber" if any(k["test"] for k in l["kids"]) else "p-gray")
    who = (I_ + " <b>отменить, обзвонить</b>") if cancelled else (I_ + " явка" + (" · " + A_ + " выход" if any(k["test"] for k in l["kids"]) else ""))
    h.append(f"<tr><td class='kto'>{who}</td><td><b>{esc(l['time'])}</b></td><td class='small'>{esc(short(l['group']))}{' — <b>НЕ ИДЁТ</b>' if cancelled else ''}</td><td class='num'><span class='pill {pill}'>{len(l['kids'])}</span></td><td class='kids'>{kids}</td></tr>")
h.append("</table></div>")

# --- полупустые группы ---------------------------------------------------------------------
h.append(f"<h2>Куда звать — {len(empty)} групп с одним-двумя детьми</h2>")
h.append("<div class='card'><span class='small'>" + " · ".join(f"<b>{live(g)}/{g['max']}</b> {esc(short(g['name']))}" for g in empty) + "</span></div>")

h.append("<h2>Итог дня — записать в 20:00 в инбокс</h2><div class='card'><ul class='small'>"
         "<li>" + I_ + " Робототехника: сколько семей по 1 разу, сколько по 2, сколько не дозвонились</li>"
         "<li>" + I_ + " Шахматы: все записанные на сегодня предупреждены — да/нет; записи сняты</li>"
         "<li>" + A_ + " Первое занятие: сколько было · оплатили на месте · назвали день · отказы и почему</li>"
         "<li>" + A_ + " Дожим: из «пришли, не купили» — сколько оплатили</li>"
         "<li>" + I_ + " Реактивация: сколько наборов из 40 · дозвонились · записали</li>"
         "<li>" + I_ + " Отметка явки стоит у всех сегодняшних занятий — да/нет</li>"
         "<li>" + I_ + " Карточки Марушина и Прокоповича Георгия заведены, записи стоят — да/нет</li></ul></div>")
h.append("</div>")
open("/home/user/kidsup/docs/plan_04sen.html", "w", encoding="utf-8").write("".join(h))
print("plan_04sen.html", len("".join(h)), "байт | пробных за 4 дня", N, "дошли", C, "купили", P, "| первых сегодня", len(first_uniq), "| дожим", len(hot), "+", len(warm), "| не дошли", len(lost), "| робо", len(robo), "| шахматы", len(chess_rows), "| хвосты", len(open03))
