# -*- coding: utf-8 -*-
"""План дня на среду 02.09 под порядок, принятый владельцем 01.09:
одна смена — только дожим и оплаты, вторая — встреча/проводы и окно 13:00–16:00
под списки; метрика — доля оплат от пришедших на пробное (порог 40%)."""
import json, re, html as H
BASE = "/home/user/kidsup/docs/rabota"
M = json.load(open(f"{BASE}/metrika_probnye.json"))
R = json.load(open(f"{BASE}/raspisanie_0209.json"))
IZV = json.load(open(f"{BASE}/izvinenie_0109.json"))
Z = open("/home/user/kidsup/docs/zadachi_02sen.html", encoding="utf-8").read()

CSS = re.search(r"<style>.*?</style>", open("/home/user/kidsup/docs/plan_01sen.html", encoding="utf-8").read(), re.S).group(0)
CSS = CSS.replace("</style>", """
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:14px 0}
.k{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
.k .v{font-size:34px;font-weight:800;line-height:1;color:var(--indigo)}.k .v.good{color:var(--green)}
.k .l{font-size:13px;color:var(--muted);margin-top:4px}
.tl{display:grid;grid-template-columns:90px 1fr;gap:6px 12px;font-size:14px;margin:8px 0}
.tl b.t{color:var(--indigo);white-space:nowrap}
.rule{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(230px,1fr))}
.rule .card{margin:0}.rule .card b{display:block;margin-bottom:4px}
.chk{display:inline-block;width:16px;height:16px;border:2px solid var(--muted);border-radius:4px;vertical-align:middle}
</style>""")

def esc(x): return H.escape(str(x or ""))
def short(g):
    g = re.sub(r"^2627_", "", g or "")
    return g

# --- метрика --------------------------------------------------------------
wk, sc, by = M["week"], M["school"], M["by_day"]
came = [r for r in M["rows"] if r["visited"]]
unpaid = [r for r in came if not r["paid_after_trial"] and not r["paid"]]
unmarked = [r for r in M["rows"] if not r["visited"] and r["state"] == 125952]
today = M["today"]
seen = set(); today_u = []
for t in today:
    k = (t["name"], t["time"])
    if k in seen: continue
    seen.add(k); today_u.append(t)

# --- задачи из zadachi_02sen: таблицы «Деньги» и «Люди ждут ответа» --------
def rows_of(section):
    m = re.search(r"<h2>" + re.escape(section) + r"</h2>.*?<table>(.*?)</table>", Z, re.S)
    out = []
    for tr in re.findall(r"<tr>(.*?)</tr>", m.group(1), re.S)[1:]:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        who = re.sub(r"<br>.*", "", tds[1], flags=re.S); who = re.sub(r"<[^>]+>", "", who)
        out.append((who.strip(), re.sub(r"<[^>]+>", "", tds[2]).strip(), re.sub(r"<[^>]+>", "", tds[3]).strip()))
    return out
money = rows_of("Деньги — сегодня")
waiting = rows_of("Люди ждут ответа")
crmfix = rows_of("Поправить в CRM — иначе будут звать не тех")
moves = rows_of("Переносы, болезни, вопросы")

def task_table(rows):
    s = "<div class='scroll'><table><tr><th></th><th>Кто</th><th>Телефон</th><th>Что сделать</th></tr>"
    for who, ph, what in rows:
        s += f"<tr><td><span class='chk'></span></td><td><b>{esc(who)}</b></td><td class='ph'>{esc(ph)}</td><td>{esc(what)}</td></tr>"
    return s + "</table></div>"

# --- сборка ---------------------------------------------------------------
n_groups = sum(1 for l in R if l["n"])
n_kids = sum(l["n"] for l in R)
n_trial = len({(t["name"]) for t in today_u})
evening_trial = {t["name"] for t in today_u if t["time"] >= "16:00"}

h = [CSS, "<div class='wrap'>",
     "<div class='hero'><h1>Среда 2 сентября — новый порядок смены</h1>"
     "<p>Работаем 9:00–20:00 · Лена — дожим и оплаты · Аня — встреча, проводы и списки · Лиза — переписка<br>"
     f"Сегодня {n_groups} групп, {n_kids} детей, из них {n_trial} на первом занятии — {len(evening_trial)} из них вечером</p></div>"]

# ошибка вчерашней рассылки
h.append("<h2>Сначала — вчерашняя ошибка рассылки</h2>")
h.append(f"<div class='card alarm'><b>Вчера в 13:33 напоминание «сегодня ждём вас» ушло 45 семьям, "
         f"и {len(IZV)} из них ждут не вчера, а 3–15 сентября.</b> Список собирался по составу групп, "
         "а не по записям на конкретный день: ребёнок числится в группе задолго до первого занятия. "
         "Ошибка моя, механизм я поправил — теперь адресат берётся только из записей на этот день. "
         "Трое уже ответили сами: Деева («я записана на 8.09»), Королева («хожу с 15.09»), Лагутина («у нас четверг»).</div>")
h.append("<div class='card'><b>Что делать админам:</b> если кто-то из списка позвонит или напишет — "
         "извиниться и назвать его настоящую дату из таблицы. Сами звонить не нужно: "
         "исправляющее сообщение уходит утром по команде владельца.</div>")
h.append("<div class='scroll'><table><tr><th>Кто</th><th>Телефон</th><th>Настоящая запись</th></tr>")
for ph, m in sorted(IZV.items(), key=lambda kv: kv[1]["when"]):
    h.append(f"<tr><td><b>{esc(m['kids'])}</b></td><td class='ph'>{esc(ph)}</td><td>{esc(m['when'])}</td></tr>")
h.append("</table></div>")

# метрика
h.append("<h2>Метрика набора — доля оплат от пришедших на пробное</h2>")
cls = "good" if (sc["доля"] or 0) >= 40 else ""
h.append("<div class='kpi'>"
         f"<div class='k'><div class='v {cls}'>{sc['доля']}%</div><div class='l'>оплатили из пришедших на пробное 31.08–01.09: {sc['оплатили']} из {sc['пришли']}. Порог — 40%</div></div>"
         f"<div class='k'><div class='v'>{by.get('2026-08-31',{}).get('оплатили',0)}/{by.get('2026-08-31',{}).get('пришли',0)}</div><div class='l'>понедельник 31.08: оплатили / пришли</div></div>"
         f"<div class='k'><div class='v'>{by.get('2026-09-01',{}).get('оплатили',0)}/{by.get('2026-09-01',{}).get('пришли',0)}</div><div class='l'>вторник 01.09: оплатили / пришли</div></div>"
         f"<div class='k'><div class='v'>{len(unmarked)}</div><div class='l'>записей на пробное за два дня без отметки «пришёл/не пришёл» — метрика их не видит</div></div>"
         "</div>")
h.append("<div class='card warn'><b>Метрика живёт на отметке явки.</b> Педагог или Аня отмечают в CRM «пришёл» сразу после занятия — иначе семья не попадает ни в счётчик, ни в список дожима. "
         f"Считаем каждую неделю: выше 40% — набор идёт, ниже — разбираем, где теряем: на выходе после занятия или в переписке.</div>")

# роли
h.append("<h2>Кто что делает</h2><div class='who'>")
h.append("<div class='wcard lena'><div class='nm'>Лена — только дожим и оплаты</div><div class='rl'>Весь день одна задача: чтобы семья с пробного ушла оплатившей. Один дожим = 600 ₽ бонуса и 8 000 ₽ выручки</div>"
         "<div class='tl'>"
         "<b class='t'>9:00–11:00</b><span>Деньги, которые уже ждут: Бернард, Мухаметшин, Юрченко, Романова, Беляев, Нариманлы — посчитать, прислать сумму и ссылку, дождаться оплаты (таблица ниже)</span>"
         "<b class='t'>11:00–13:00</b><span>Подтвердить всех, кто сегодня на первом занятии (список ниже, шаблон под ним). В сообщении — про скидку −10% в день первого занятия и что оплатить можно сразу на стойке</span>"
         f"<b class='t'>13:00–16:00</b><span>Дожим по пробным 31.08–01.09 без оплаты — {len(unpaid)} семей (список ниже). Звонок, не сообщение: «как ребёнку, что сказал педагог, закрепляем место?»</span>"
         "<b class='t'>16:00–20:00</b><span><b>Только разговоры с родителями на выходе.</b> Не встречает, не провожает, не отвечает на звонки — это Аня. Терминал и QR на стойке: оплата здесь и сейчас, ссылка «вдогонку» — только если родитель сам отказался платить на месте</span>"
         "</div></div>")
h.append("<div class='wcard anya'><div class='nm'>Аня — встреча, проводы и списки</div><div class='rl'>Телефон и дверь — её. К Лене родителей подводит, сама не продаёт</div>"
         "<div class='tl'>"
         "<b class='t'>9:00–13:00</b><span>Люди, которые ждут ответа со вчера (таблица ниже), потом правки в CRM и переносы. Входящие звонки — все её</span>"
         "<b class='t'>13:00–16:00</b><span><b>Окно под списки — занятий нет, никто не отвлекает.</b> По порядку: ДОД (35 семей) → Праздник (69) → летние и прошлогодние (206). Норма окна — 40 наборов. Недозвон — просто закрыть задачу «недозвон»: WhatsApp и СМС вдогонку автопилот шлёт сам</span>"
         "<b class='t'>16:00–20:00</b><span>Встречает и провожает: код двери, раздевалка, к педагогу. <b>Сразу после занятия ставит в CRM отметку «пришёл»</b> — это метрика. Когда группа выходит — подводит родителей первого занятия к Лене</span>"
         "</div></div>")
h.append("<div class='wcard' style='border-top-color:var(--blue)'><div class='nm'>Лиза — переписка</div><div class='rl'>Правило ответа: час в рабочее время, 15 минут с 16:00 до 20:00</div>"
         "<ol class='small'><li>Каждые 15 минут вечером — проверить входящие во всех каналах; кто ждёт дольше — отвечать первым</li>"
         "<li>Вопросы про цену и группу — отвечать сразу цифрой из /enrollment, не «уточню»</li>"
         "<li>Задачи по переписке закрывает Клод — вручную не создавать</li></ol></div>")
h.append("</div>")

# правила
h.append("<h2>Четыре правила смены — с сегодняшнего дня</h2><div class='rule'>")
for t, d in [("1. Встреча и продажа — разные люди", "С 16:00 до 20:00 Аня встречает и провожает, Лена только разговаривает с родителями на выходе. Это её основная задача смены, а не «если успею»."),
             ("2. Оплата на месте", "Терминал и QR лежат на стойке. Родитель уходит либо оплатив, либо назвав день оплаты — и это записано в CRM. Ссылка вдогонку — исключение, а не правило."),
             ("3. Час и пятнадцать минут", "В переписке отвечаем в течение часа в рабочее время и в течение 15 минут, когда идут занятия. Кто ждёт дольше — первый в очереди."),
             ("4. Списки — с 13:00 до 16:00", "Когда занятий нет. Утром — хвосты и подтверждения, вечером — люди в центре. Звонить по спискам в другое время — не надо.")]:
    h.append(f"<div class='card'><b>{t}</b><span class='small'>{d}</span></div>")
h.append("</div>")

# сегодня на пробное
h.append(f"<h2>Сегодня на первом занятии — {n_trial} детей</h2>")
h.append("<div class='q'>«Доброе утро! Это KidsUP. Сегодня в ЧЧ:ММ ждём [имя] на первое занятие — оно условно-бесплатное и с диагностикой. Бульвар Рокоссовского 6 к1В, 7-й подъезд, домофон 12, 2 этаж, из лифта налево, код от двери 667788#. Приходите за 10 минут. В день первого занятия действует −10% на абонемент — оплатить можно сразу на стойке. Будете?»</div>")
h.append("<div class='scroll'><table><tr><th></th><th>Время</th><th>Группа</th><th>Ребёнок</th><th>Телефон</th><th>Статус</th></tr>")
ST = {125951: "новый", 345768: "недозвон", 146950: "думает", 125952: "записался", 125953: "посетил", 125955: "клиент", 345759: "архив", 349497: "Праздник"}
for t in today_u:
    st = "<span class='pill p-green'>уже оплатил</span>" if t["paid"] else f"<span class='pill p-gray'>{esc(ST.get(t['state'], ''))}</span>"
    h.append(f"<tr><td><span class='chk'></span></td><td><b>{esc(t['time'][:5])}</b></td><td class='small'>{esc(short(t['group']))}</td><td><b>{esc(t['name'])}</b></td><td class='ph'>{esc(t['phone'])}</td><td>{st}</td></tr>")
h.append("</table></div>")
h.append("<div class='card ok'><b>На выходе (Лена):</b> «Как [имя]? Педагог сказал, что … Место в этой группе закрепляем? Сегодня −10% на первый абонемент — 4 занятия 4 500 ₽ или 8 занятий 7 695 ₽, можно картой или по QR прямо здесь». Ответ семьи — в CRM: оплатил / оплатит (дата) / отказ (причина).</div>")

# дожим по вчерашним
h.append(f"<h2>Дожим: были на пробном 31.08–01.09 и не оплатили — {len(unpaid)} семей</h2>")
h.append("<div class='scroll'><table><tr><th></th><th>Пробное</th><th>Направление</th><th>Ребёнок</th><th>Телефон</th></tr>")
for r in sorted(unpaid, key=lambda r: min(r["dates"])):
    h.append(f"<tr><td><span class='chk'></span></td><td>{esc(min(r['dates'])[5:])}</td><td class='small'>{esc(', '.join(short(g) for g in r['groups']))}</td><td><b>{esc(r['name'])}</b></td><td class='ph'>{esc(r['phone'])}</td></tr>")
h.append("</table></div>")
h.append("<div class='q'>«Здравствуйте! Это Лена из KidsUP. [Имя] был(а) у нас на первом занятии — как впечатления? Педагог отметил … Мы держим место в группе до [день]; закрепить абонементом? Скидка 10% на первый абонемент действует до конца недели, оплатить можно по ссылке или на стойке».</div>")

# без отметки
h.append(f"<h2>Записаны на пробное 31.08–01.09, явка не отмечена — {len(unmarked)}</h2>")
h.append("<div class='card'>Аня утром: были или нет? Были — отметить «пришёл» и отдать Лене на дожим. Не были — перенести на конкретный день. "
         "<span class='small'>" + " · ".join(f"<b>{esc(r['name'])}</b> {esc(r['phone'])}" for r in unmarked) + "</span></div>")

# задачи
h.append("<h2>Деньги — сегодня (Лена, с утра)</h2>" + task_table(money))
h.append("<h2>Люди ждут ответа (Аня, с утра)</h2>" + task_table(waiting))
h.append("<h2>Поправить в CRM (Аня, до 13:00)</h2>" + task_table(crmfix))
h.append("<h2>Переносы, болезни, вопросы (Аня)</h2>" + task_table(moves))

# расписание дня
h.append(f"<h2>Кто идёт сегодня — {n_groups} групп, {n_kids} детей</h2>")
h.append("<div class='scroll'><table><tr><th>Время</th><th>Группа</th><th class='num'>Детей</th><th>Кого ждём (★ — первое занятие)</th></tr>")
for l in R:
    if not l["n"]: continue
    kids = ", ".join(("★ " if k["test"] else "") + esc(k["name"]) for k in l["kids"])
    pill = "p-amber" if l["test"] else "p-gray"
    h.append(f"<tr><td><b>{esc(l['time'][:5])}</b></td><td class='small'>{esc(short(l['group']))}</td><td class='num'><span class='pill {pill}'>{l['n']}</span></td><td class='kids'>{kids}</td></tr>")
h.append("</table></div>")

h.append("<h2>Итог дня — что записать в 20:00</h2><div class='card'><ul class='small'>"
         "<li>Сколько семей было на первом занятии · сколько оплатили на месте · сколько назвали день оплаты · сколько отказов и почему</li>"
         "<li>Списки: сколько наборов в окне 13:00–16:00, сколько записей на пробное</li>"
         "<li>Переписка: кто ждал дольше часа</li>"
         "<li>Полный список задач из разборов — <a href='/base/zadachi_02sen'>Задачи админам — среда 2 сентября</a></li></ul></div>")
h.append("</div>")
open("/home/user/kidsup/docs/plan_02sen.html", "w", encoding="utf-8").write("".join(h))
print("plan_02sen.html:", len("".join(h)), "байт; дожим", len(unpaid), "без отметки", len(unmarked), "сегодня пробных", n_trial)
