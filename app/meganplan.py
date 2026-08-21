"""Мега-план набора: единый документ до 30 сентября.

Сводит воедино то, что уже работает у нас, и то, что взято из марафона
по набору. Цифры живые — считаются из CRM в момент сборки страницы,
поэтому план не расходится с реальностью через неделю.

Главная мысль, ради которой он написан. «Полные группы» звучит как одна
цель, а на деле это три разные задачи с разной ценой:

  · заполнить ВСЕ 398 мест — нужно 19 заявок в день, столько один центр
    в спальном районе не собирает;
  · довести каждую из 78 групп до минимума в четыре ребёнка — 14 заявок
    в день, всё ещё выше нашей нормы;
  · сократить расписание до групп, где уже есть дети, и добить их —
    7 заявок в день, и это достижимо.

Разница не в усилиях, а в том, что пустая группа в расписании не приносит
денег, зато отнимает педагога и делает выбор родителя сложнее: человек,
которому показали 78 вариантов, чаще откладывает решение, чем выбирает.

Запуск:
    python -m app.meganplan        — собрать docs/megaplan.html
"""

from __future__ import annotations

import html as H
import logging
from datetime import date, timedelta

from . import socialfactory as sf

log = logging.getLogger("kidsup.meganplan")
OUT = "docs/megaplan.html"

GOAL = date(2026, 9, 30)
MIN_GROUP = 4          # меньше четырёх детей — группа не живёт
CONV = 0.42            # заявка → оплата, из воронки марафона: 55 из 130


def numbers() -> dict:
    f = sf.facts()
    g = f["группы"]
    today = date.today()
    days = max(1, (GOAL - today).days)
    cap = sum(x["cap"] for x in g)
    busy = sum(x["busy"] for x in g)
    live = [x for x in g if x["busy"] > 0]
    empty = [x for x in g if x["busy"] == 0]

    def need(groups, target=None):
        if target is None:
            return sum(x["free"] for x in groups)
        return sum(max(0, target - x["busy"]) for x in groups)

    sc = []
    for name, groups, target, note in (
        ("Все места", g, None, "каждое место занято"),
        ("Каждая группа живёт", g, MIN_GROUP,
         f"в каждой из {len(g)} групп минимум {MIN_GROUP} ребёнка"),
        ("Только живые группы", live, MIN_GROUP,
         f"расписание сокращено до {len(live)} групп, где уже есть дети"),
    ):
        n = need(groups, target)
        sc.append({"название": name, "нужно": n, "в_день": round(n / days, 1),
                   "заявок": int(n / CONV), "заявок_в_день": round(n / CONV / days, 1),
                   "пояснение": note})
    return {"дней": days, "мест": cap, "занято": busy,
            "процент": round(busy * 100 / cap) if cap else 0,
            "групп": len(g), "живых": len(live), "пустых": len(empty),
            "сценарии": sc, "предметы": f["предметы"], "facts": f}


# Инструменты: что уже работает и что берём из марафона.
# Порядок — по цене контакта: сначала те, кто нас уже знает.
TOOLS = [
    ("Собрать оплату с тех, кто уже записан", "работает", "Лиза",
     "54 человека выбрали группу и день, но не оплатили новый сезон. Им ничего "
     "не нужно продавать — нужно взять деньги. Самая короткая дорога, и она "
     "не тронута.", "деньги на этой неделе"),
    ("Возврат своих: 238 семей из прошлого ПШ", "работает", "Борис + админы",
     "Семьи, которые уже занимались у нас и на новый сезон никуда не записаны. "
     "Лист собран, скрипт написан, ссылки на карточки внутри.", "app.kidsup.ru/base/obzvon_psh"),
    ("Три события 29–31 августа", "работает", "все",
     "Праздник 29.08 в 11:00 — вход свободный, запись не нужна. День открытых "
     "дверей 30.08. Неделя открытых уроков 31.08–06.09 — первое занятие своей "
     "группы по обычному расписанию.", "подтверждать приходы 27–28.08"),
    ("Контент в соцсетях", "работает", "Борис",
     "25 постов с готовыми текстами и сторис до конца сентября, цифры о местах "
     "подставляются из CRM.", "app.kidsup.ru/base/kontent_plan"),
    ("Обзвон холодной базы 15 в день", "работает", "админы на смене",
     "Норма из марафона. У нас разложено по сменам с приоритетом: сначала "
     "деньги и тёплые, потом прошлогодние, потом холодные.", "ежедневно"),
    ("Рассылки по базе 10 в день", "работает", "Лиза",
     "Норма из марафона. Идёт через Wazzup, но канал WhatsApp сейчас "
     "не авторизован — пока не пересканирован QR, часть сообщений не доходит.",
     "почини канал первым делом"),
    ("Оффер месяца с дедлайном", "взять", "Борис",
     "У нас есть «до 31 августа сентябрь по ценам прошлого года», но нет "
     "названия и единого макета. В марафоне это «Первоклассный чек-ап знаний»: "
     "диагностика плюс трекер прогресса в подарок. Название сильное — оно "
     "обещает родителю не скидку, а понимание про своего ребёнка.",
     "назвать и раздать админам"),
    ("Трекер диагностики", "взять", "педагоги",
     "Бланк, который педагог заполняет на первом занятии: внимание, память, "
     "мышление, речь, моторика, социализация. Родитель уходит не с «всё "
     "хорошо», а с картой. Это же делает гарантию чтения измеримой — от чего "
     "считать три месяца.", "напечатать до 29.08"),
    ("Паспорт миссий ученика", "взять", "педагоги",
     "Тетрадь, где ребёнок отмечает достижения за год. Работает на удержание: "
     "родитель видит прогресс, ребёнок хочет заполнить следующую страницу.",
     "напечатать до старта"),
    ("PDF-гайд после записи", "взять", "Клод",
     "Автоматическая отправка гайда «Как подготовить ребёнка к учёбе» сразу "
     "после записи на пробное. Закрывает паузу между «записался» и «пришёл», "
     "в которой люди и отваливаются.", "собрать и включить"),
    ("Семейный кэшбек с партнёрами", "взять", "Борис",
     "Партнёры по соседству — бассейн, студия, кофейня, фотостудия, детский "
     "клуб. Мы даём им родителей, они нам подарки для наших семей. Заодно "
     "закрывает пустое поле в наших правилах, где партнёрские программы "
     "до сих пор не названы.", "3–5 партнёров"),
    ("Реферальная программа", "взять", "Борис",
     "Механика есть в марафоне, у нас решение не принято: висит вопрос "
     "про 1000 бонусных рублей. Пока не назван размер бонуса, публиковать "
     "нельзя — придётся отвечать на вопросы, ответов на которые нет.",
     "решить да/нет"),
    ("Промоутеры с микропризами", "чинить", "Борис",
     "Работают, но плохо: у Виталия почти все номера мёртвые, часть "
     "заблокирована оператором. В марафоне за лид дают микроприз — это "
     "меняет мотивацию промоутера с «набрать телефонов» на «привести живого».",
     "решить по Виталию"),
    ("Расклейки и плакаты в ЖК", "взять", "Борис",
     "Дешёвый локальный канал, которого у нас нет вовсе. Мы в спальном "
     "районе, наша аудитория живёт в соседних домах.", "макет + маршрут"),
    ("Реклама в пабликах и чатах", "взять", "Борис",
     "Районные сообщества в Telegram, MAX и ВКонтакте. Точное попадание "
     "по географии, стоит недорого.", "найти 5–10 каналов"),
    ("Яндекс Карты", "взять", "Борис",
     "Пост и фото с оффером в карточке организации. Люди ищут «детский центр "
     "рядом» именно там, а карточка обычно заброшена.", "30 минут работы"),
    ("Чат-бот для сбора контактов", "взять", "Клод",
     "Бот в ВКонтакте, который отвечает на типовые вопросы и собирает "
     "телефоны в нерабочее время. Половина обращений приходит вечером, "
     "когда центр закрыт.", "после токенов ВК"),
    ("Конкурс с розыгрышем", "взять", "Борис",
     "Механика на подписчиков: подарки всем участникам, сбор контактов "
     "через бота.", "после бота"),
    ("Блогеры: 3–5 местных", "взять", "Борис",
     "Два формата из марафона: обзор центра с купоном или совместный конкурс "
     "в рилс с отметкой. Второй дешевле и даёт охват.", "проверить по регламенту"),
    ("Листы ожидания на новые направления", "работает", "админы",
     "Танцы, хореография, футбол, единоборства, акробатика, актёрское "
     "мастерство, техника речи. Первой группе — скидка 10%. Собирает спрос "
     "без затрат на педагога.", "предлагать вторым предметом"),
]

WEEKS = [
    ("22–24 августа", "Деньги и подтверждения", [
        "Лиза: обзвонить 54 записанных без оплаты — это ближайшие деньги.",
        "Борис: лист ПШ, 103 семьи целевого возраста (суббота и воскресенье).",
        "Починить канал WhatsApp — пересканировать QR.",
        "Назвать оффер месяца и раздать админам единую формулировку.",
        "Решить: имя педагога английского и размер реферального бонуса.",
    ]),
    ("25–28 августа", "Подготовка к событиям", [
        "Напечатать трекеры диагностики и паспорта миссий — до праздника.",
        "Провести инструктаж педагогов по трекеру: что заполнять и что говорить.",
        "27 и 28 августа — обзвон подтверждений: без звонка накануне доходит половина.",
        "Расклейка плакатов по ЖК, поиск 3–5 партнёров для кэшбека.",
        "Разместить оффер на Яндекс Картах и во всех соцсетях.",
    ]),
    ("29–31 августа", "Три события", [
        "29.08 в 11:00 — праздник открытия сезона: аниматоры, лотерея, вход свободный.",
        "30.08 — День открытых дверей: знакомство с педагогами лично.",
        "31.08 — старт учебного года и Неделя открытых уроков.",
        "Каждому пришедшему — трекер и приглашение на пробное с конкретным днём.",
        "Вечером каждого дня вносить всех пришедших в CRM — не откладывать.",
    ]),
    ("1–7 сентября", "Неделя открытых уроков", [
        "Каждый пробный урок заканчивается разговором педагога с родителем по трекеру.",
        "Оплата в день пробного — со скидкой 10%, называть сразу.",
        "Реклама в районных пабликах и чатах.",
        "Запуск реферальной программы, если механика утверждена.",
    ]),
    ("8–21 сентября", "Добор и сокращение", [
        "Свести расписание: закрыть группы, куда за месяц никто не записался.",
        "Перевести их детей и заявки в живые группы — родителю звонит человек, не бот.",
        "Блогеры: 3–5 местных, формат конкурса в рилс.",
        "Конкурс с ботом на сбор подписчиков.",
    ]),
    ("22–30 сентября", "Закрытие набора", [
        "Добить группы, где не хватает одного-двух детей, — точечно и адресно.",
        "Посчитать конверсию по каждому каналу: что дало заявки, а что нет.",
        "Зафиксировать, какие направления просят в листах ожидания.",
    ]),
]

METRICS = [
    ("Оплаченных абонементов нового сезона", "7", "каждый день"),
    ("Записей в группы 2026/27", "77", "каждый день"),
    ("Заявок за день", "цель 7", "каждый вечер"),
    ("Живых групп (есть хотя бы один ребёнок)", "50 из 78", "раз в неделю"),
    ("Групп, дошедших до 4 детей", "считать", "раз в неделю"),
    ("Конверсия заявка → пробное → оплата", "считать", "раз в неделю"),
]


def build() -> str:
    n = numbers()
    sc = n["сценарии"]
    rec = sc[2]

    def tool_rows(kind):
        out = []
        for name, k, who, why, when in TOOLS:
            if k != kind:
                continue
            out.append(f"""<div class="tool">
  <div class="th"><b>{H.escape(name)}</b><span class="who">{H.escape(who)}</span></div>
  <p>{H.escape(why)}</p>
  <div class="when">{H.escape(when)}</div>
</div>""")
        return "".join(out)

    weeks = "".join(
        f"""<div class="week"><div class="wh"><b>{H.escape(t)}</b>
        <span>{H.escape(sub)}</span></div><ul>"""
        + "".join(f"<li>{H.escape(x)}</li>" for x in items) + "</ul></div>"
        for t, sub, items in WEEKS)

    subj = "".join(
        f"<tr><td>{H.escape(k)}</td><td class='num'>{v['cap'] - v['free']}</td>"
        f"<td class='num'>{v['cap']}</td>"
        f"<td class='num'>{round((v['cap'] - v['free']) * 100 / v['cap']) if v['cap'] else 0}%</td></tr>"
        for k, v in sorted(n["предметы"].items(),
                           key=lambda x: -(x[1]["cap"] - x[1]["free"])))

    metrics = "".join(
        f"<tr><td>{H.escape(a)}</td><td class='num'>{H.escape(b)}</td>"
        f"<td>{H.escape(c)}</td></tr>" for a, b, c in METRICS)

    scen = "".join(
        f"""<div class="scen{' rec' if s is rec else ''}">
  <div class="sn">{H.escape(s['название'])}{' · рекомендую' if s is rec else ''}</div>
  <div class="sv">{s['заявок_в_день']}</div>
  <div class="sl">заявок в день</div>
  <p>{H.escape(s['пояснение'])}. Нужно ещё {s['нужно']} записей —
     это {s['в_день']} в день и {s['заявок']} заявок за весь срок.</p>
</div>""" for s in sc)

    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Мега-план набора до 30 сентября</title>
<style>
:root{{--paper:#FCFBF7;--ink:#1B1D2B;--muted:#5F6478;--line:#E7E4DA;--card:#FFF;--fill:#F3F1E9;
 --indigo:#312783;--sky:#1DA7E0;--green:#5C8C1E;--amber:#B26F00;--red:#B4131C;
 --indigo-soft:#EAE8F5;--sky-soft:#E4F2FB;--green-soft:#EDF4E1;--amber-soft:#FBF0DC;--red-soft:#FAE4E3}}
@media (prefers-color-scheme:dark){{:root{{--paper:#14151D;--ink:#E7E6EC;--muted:#9A9EAE;
 --line:#292B37;--card:#1B1D26;--fill:#20222C;--indigo:#A79EEE;--sky:#5EC0EC;--green:#9FD055;
 --amber:#E5A63F;--red:#EC7C7C;--indigo-soft:#201E38;--sky-soft:#13252F;--green-soft:#1C2416;
 --amber-soft:#2C2313;--red-soft:#2E1819}}}}
*{{box-sizing:border-box}}
body{{background:var(--paper);color:var(--ink);margin:0;
 font:16px/1.62 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
.wrap{{max-width:56rem;margin:0 auto;padding:2rem 1.1rem 5rem}}
h1{{font-size:2rem;font-weight:800;letter-spacing:-.025em;margin:.3rem 0 .5rem;text-wrap:balance}}
h2{{font-size:1.34rem;font-weight:760;margin:0 0 .35rem;text-wrap:balance}}
h3{{font-size:1.02rem;font-weight:730;margin:1.4rem 0 .45rem}}
p{{margin:.5rem 0;max-width:46rem}} .sub{{color:var(--muted);max-width:46rem}}
.kicker{{font-size:.72rem;font-weight:750;letter-spacing:.1em;text-transform:uppercase;color:var(--indigo)}}
section{{margin-top:2.4rem;padding-top:1.6rem;border-top:1px solid var(--line)}}
.seclabel{{font-size:.7rem;font-weight:750;letter-spacing:.09em;text-transform:uppercase;
 color:var(--muted);margin-bottom:.4rem}}
.nums{{display:grid;gap:.6rem;grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));margin:1rem 0}}
.num{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:.8rem .95rem}}
.num b{{display:block;font-size:1.7rem;font-weight:800;color:var(--indigo);line-height:1.05;
 font-variant-numeric:tabular-nums}}
.num span{{display:block;font-size:.8rem;color:var(--muted);margin-top:.15rem}}
.num.red b{{color:var(--red)}} .num.green b{{color:var(--green)}}
.scens{{display:grid;gap:.7rem;grid-template-columns:repeat(auto-fit,minmax(15rem,1fr));margin:1.1rem 0}}
.scen{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:.9rem 1.05rem}}
.scen.rec{{border-color:var(--green);background:var(--green-soft)}}
.sn{{font-size:.78rem;font-weight:750;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}}
.scen.rec .sn{{color:var(--green)}}
.sv{{font-size:2rem;font-weight:800;color:var(--indigo);line-height:1.1;font-variant-numeric:tabular-nums}}
.scen.rec .sv{{color:var(--green)}}
.sl{{font-size:.8rem;color:var(--muted);margin-bottom:.4rem}}
.scen p{{font-size:.88rem;margin:0}}
.tools{{display:grid;gap:.6rem;grid-template-columns:repeat(auto-fit,minmax(17rem,1fr));margin:.9rem 0}}
.tool{{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:.8rem .95rem}}
.th{{display:flex;justify-content:space-between;gap:.5rem;align-items:baseline;margin-bottom:.25rem}}
.th b{{font-weight:730;font-size:.97rem}}
.who{{font-size:.72rem;color:var(--muted);white-space:nowrap}}
.tool p{{font-size:.87rem;color:var(--muted);margin:.2rem 0 .4rem}}
.tool .when{{font-size:.78rem;font-weight:700;color:var(--sky)}}
.week{{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:.85rem 1.05rem;margin:.7rem 0}}
.wh{{display:flex;justify-content:space-between;gap:.6rem;flex-wrap:wrap;align-items:baseline}}
.wh b{{font-weight:760}} .wh span{{font-size:.8rem;color:var(--muted)}}
.week ul{{margin:.5rem 0 0;padding-left:1.15rem}} .week li{{margin:.28rem 0;font-size:.92rem}}
.tbl{{overflow-x:auto;margin:1rem 0;border:1px solid var(--line);border-radius:11px;background:var(--card)}}
table{{border-collapse:collapse;width:100%;font-size:.9rem;min-width:26rem}}
th{{background:var(--fill);text-align:left;font-size:.7rem;letter-spacing:.05em;text-transform:uppercase;
 color:var(--muted);padding:.5rem .8rem;white-space:nowrap}}
td{{padding:.5rem .8rem;border-top:1px solid var(--line)}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.warn{{border-left:3px solid var(--red);background:var(--red-soft);padding:.8rem 1.05rem;
 border-radius:0 9px 9px 0;margin:1.1rem 0;max-width:46rem}}
.ok{{border-left:3px solid var(--green);background:var(--green-soft);padding:.8rem 1.05rem;
 border-radius:0 9px 9px 0;margin:1.1rem 0;max-width:46rem}}
.foot{{margin-top:3rem;padding-top:1.2rem;border-top:1px solid var(--line);color:var(--muted);font-size:.85rem}}
@media print{{body{{background:#fff}} .wrap{{padding:0}} section{{break-inside:avoid}}}}
</style></head>
<body><div class="wrap">

<div class="kicker">Набор 2026/27 · осталось {n['дней']} дней</div>
<h1>Мега-план: полные группы к 30 сентября</h1>
<p class="sub">Всё, что делаем до конца сентября, в одном месте. Цифры живые —
пересчитываются из CRM при каждой сборке страницы.</p>

<div class="nums">
  <div class="num red"><b>7</b><span>оплатили<br>новый сезон</span></div>
  <div class="num"><b>{n['занято']}</b><span>записей<br>из {n['мест']} мест</span></div>
  <div class="num"><b>{n['процент']}%</b><span>заполнение<br>по местам</span></div>
  <div class="num"><b>{n['живых']}</b><span>живых групп<br>из {n['групп']}</span></div>
</div>

<div class="warn"><b>Главное, что нужно понимать про цифры.</b> «77 записей» и
«7 оплат» — это не одно и то же. Статус «Учится» в CRM ставит администратор,
когда зачисляет ребёнка в группу, и он не означает, что за новый сезон
заплатили. Из 36 «учащихся» новый сезон оплатили шестеро, из 28 записанных
на пробное — ни один. До старта занятий десять дней.</div>

<section>
<div class="seclabel">Честная математика</div>
<h2>«Полные группы» — это три разные задачи</h2>
<p>Разница между ними не в усилиях, а в том, сколько заявок нужно добывать
каждый день. Вот что стоит за каждым вариантом:</p>

<div class="scens">{scen}</div>

<div class="ok"><b>Что рекомендую.</b> Сократить расписание до групп, где уже
есть дети, и добить их до четырёх человек. Пустая группа в расписании денег
не приносит, зато занимает педагога и усложняет выбор: родителю, которому
показали {n['групп']} вариантов, проще отложить решение, чем выбрать.
Освободившиеся окна вернём, когда наберётся спрос — листы ожидания для этого
и существуют.</div>
</section>

<section>
<div class="seclabel">Что уже работает</div>
<h2>Не трогаем, продолжаем</h2>
<div class="tools">{tool_rows("работает")}</div>
</section>

<section>
<div class="seclabel">Что берём из марафона</div>
<h2>Новое — по порядку внедрения</h2>
<div class="tools">{tool_rows("взять")}</div>
<h3>Что чиним</h3>
<div class="tools">{tool_rows("чинить")}</div>
<p class="sub">Трекеры, паспорта и гайд не обещаем родителям, пока они
физически не напечатаны: обещание, которое нечем закрыть на первом же
занятии, стоит дороже, чем отсутствие обещания.</p>
</section>

<section>
<div class="seclabel">Календарь</div>
<h2>Неделя за неделей</h2>
{weeks}
</section>

<section>
<div class="seclabel">Где мы сейчас</div>
<h2>Заполнение по предметам</h2>
<div class="tbl"><table>
<tr><th>предмет</th><th class="num">записей</th><th class="num">мест</th><th class="num">заполнено</th></tr>
{subj}
</table></div>
</section>

<section>
<div class="seclabel">Контроль</div>
<h2>По каким числам поймём, что работает</h2>
<div class="tbl"><table>
<tr><th>показатель</th><th class="num">сейчас</th><th>как часто смотреть</th></tr>
{metrics}
</table></div>
<p class="sub">Главное число — оплаченные абонементы нового сезона, а не записи.
Записи можно нарисовать статусом, оплату нельзя.</p>
</section>

<div class="foot">🤖 Клод, ИИ-сотрудник KidsUP · собрано {date.today().strftime('%d.%m.%Y')}.
Воронка марафона для сверки: 130 заявок → 80 ответов → 60 приходов → 55 покупок.</div>

</div></body></html>"""


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    open(OUT, "w").write(build())
    n = numbers()
    print(f"{OUT}: собрано")
    for s in n["сценарии"]:
        print(f"   {s['название']:24s} {s['заявок_в_день']:5.1f} заявок/день "
              f"(нужно {s['нужно']} записей)")


if __name__ == "__main__":
    main()
