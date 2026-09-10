"""Расчёт бонусов Ани и Иры за август — страница, которую можно показать им самим.

Схема владельца от 26.08. Считаем не «сколько нащёлкали», а сколько принесла
каждая запись: дошёл ребёнок до пробного и купил ли абонемент. Данные берём
через /api/bonusy (он гоняет app.bonusy.collect по живой CRM), период —
записи, созданные с 01.08 по 31.08, а результат по ним смотрим на сегодня:
пробные начались 31.08, поэтому августовская работа доплачивается сентябрём.

    python3 docs/rabota/bonusy_avgust.py        — собрать docs/bonusy_avgust.html
"""
from __future__ import annotations

import collections
import html
import json
import pathlib
import subprocess
from datetime import date

OUT = pathlib.Path("/home/user/kidsup/docs/bonusy_avgust.html")
SINCE, UNTIL = "2026-08-01", "2026-08-31"
WHO = ("Ира", "Аня")
# Журнал действий МойКласса за август: сколько записей человек создал сам и в
# скольких днях был активен. Схема начисляет по ответственному менеджеру записи,
# а его можно переназначить, — журнал показывает автора и служит проверкой.
JOURNAL = {"Ира": {"records": 115, "days": 12}, "Аня": {"records": 79, "days": 14}}
RU_MONTH = {8: "августа", 9: "сентября"}


def fetch() -> dict:
    cmd = ["curl", "-s", "-u", "boris:KU-KkI1Uvrd81hWcm", "-G",
           "https://app.kidsup.ru/api/bonusy",
           "--data-urlencode", f"since={SINCE}",
           "--data-urlencode", f"until={UNTIL}",
           "--data-urlencode", "rows=1",
           "--data-urlencode", "who=" + ",".join(WHO), "--max-time", "900"]
    return json.loads(subprocess.run(cmd, capture_output=True).stdout.decode())


def plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return few
    return many


def rub(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " ₽"


def short_group(g: str) -> str:
    """«2627_ПШ_вт-чт_17:00_5-7 лет_ПШ1 нечитающие (Гр2)» → читаемое имя."""
    g = g[5:] if g.startswith("2627_") else g
    return g.replace("_", " · ")


def analyse(rows: list[dict]) -> dict:
    money = collections.Counter()
    times = collections.Counter()
    for r in rows:
        for part in r["why"].split(", "):
            times[part] += 1
            money[part] += 300 if "+300" in part else (150 if "+150" in part else 0)
    waiting_trial = times.get("ждём пробного", 0)
    waiting_pay = times.get("ждём оплату", 0)
    return {
        "n": len(rows),
        "new": sum(1 for r in rows if not r["cont"] and not r["zayavka"]),
        "cont": sum(1 for r in rows if r["cont"]),
        "zayavki": sum(1 for r in rows if r["zayavka"]),
        "came": sum(1 for r in rows if r["came"]),
        "paid": sum(1 for r in rows if r["paid"]),
        "total": sum(r["bonus"] for r in rows),
        "times": times, "money": money,
        # что ещё не сыграло: у нового предмета впереди пробное (+300) и покупка
        # (+300), у продолжающего — только покупка
        "on_table": waiting_trial * 600 + waiting_pay * 300,
        "waiting_trial": waiting_trial, "waiting_pay": waiting_pay,
    }


CSS = """
:root{--ink:#15132e;--muted:#6c6a86;--line:#e4e2f0;--bg:#f7f6fb;--indigo:#312783;
      --blue:#1DA7E0;--green:#7DB928;--amber:#F59C00;--card:#fff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:0 16px 70px}
.hero{background:linear-gradient(135deg,#312783,#1DA7E0);color:#fff;margin:0 -16px 22px;
      padding:26px 20px 24px;border-radius:0 0 20px 20px}
.hero h1{margin:0 0 6px;font-size:26px;line-height:1.15}
.hero p{margin:0;opacity:.93;font-size:15px;max-width:70ch}
h2{font-size:20px;margin:32px 0 10px;color:var(--indigo);border-bottom:2px solid var(--line);padding-bottom:6px}
h3{font-size:17px;margin:22px 0 8px;color:var(--indigo)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px 17px;margin:12px 0}
.tot{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin:16px 0 4px}
.tot div{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:13px 15px}
.tot .who{font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;font-weight:700}
.tot b{display:block;font-size:31px;color:var(--indigo);font-variant-numeric:tabular-nums;margin:2px 0 1px}
.tot span{font-size:13px;color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:14.5px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);
   padding:8px 6px;border-bottom:2px solid var(--line);white-space:nowrap}
td{padding:7px 6px;border-bottom:1px solid var(--line);vertical-align:top}
.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.scroll{overflow-x:auto}
.small{font-size:13px;color:var(--muted)}
.sum td{background:#f4f2fd;font-weight:800}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:700;color:#fff;white-space:nowrap}
.t-new{background:var(--blue)}.t-cont{background:var(--green)}.t-zay{background:#9a99b3}
.ok{color:var(--green);font-weight:700}.no{color:#b9b7cb}
.zero td{color:#8b89a3}
.note{border-left:4px solid var(--amber);background:#fffaf0}
.good{border-left:4px solid var(--green);background:#f7fbf0}
ul{padding-left:20px;margin:6px 0}li{margin:5px 0}
"""


def page(data: dict) -> str:
    rows = data["rows"]
    st = {w: analyse(rows[w]) for w in WHO}
    today = date.today()
    p = [f"<!doctype html><html lang=ru><meta charset=utf-8>"
         f"<meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<title>Бонусы за август — Аня и Ира</title><style>{CSS}</style>"
         f"<div class=wrap><div class=hero><h1>Бонусы за август: как посчитано</h1>"
         f"<p>Схема владельца от 26.08. Считаются записи, созданные "
         f"с 1 по 31 августа, а результат по ним — на {today.day} "
         f"{RU_MONTH[today.month]}: пробные начались 31.08, поэтому часть "
         f"августовской работы дошла до денег только в сентябре. "
         f"Ниже каждая запись поимённо — можно проверить любую строку.</p></div>"]

    p.append("<div class=tot>")
    for w in WHO:
        p.append(f"<div><div class=who>{w}</div><b>{rub(st[w]['total'])}</b>"
                 f"<span>{st[w]['n']} {plural(st[w]['n'], 'запись', 'записи', 'записей')} · "
                 f"{st[w]['came']} дошли до пробного · {st[w]['paid']} оплатили</span></div>")
    p.append("</div>")

    p.append("<h2>Ставки: за что начисляется</h2><div class='card'><div class=scroll><table>"
             "<tr><th>Случай</th><th>Что должно случиться</th><th class=n>Ставка</th></tr>"
             "<tr><td><b>Новый предмет</b><div class=small>клиент не занимался им в прошлом году; "
             "английский и подготовка к школе — всегда новый предмет, там все педагоги новые</div></td>"
             "<td>ребёнок дошёл до пробного</td><td class=n>+300 ₽</td></tr>"
             "<tr><td></td><td>купил абонемент в день пробного или до 31.08</td><td class=n>+300 ₽</td></tr>"
             "<tr><td></td><td>купил в течение двух недель после пробного</td><td class=n>+150 ₽</td></tr>"
             "<tr><td><b>Продолжающий</b><div class=small>тот же предмет к тому же педагогу — "
             "пробного у него нет</div></td><td>купил до 31.08</td><td class=n>+300 ₽</td></tr>"
             "<tr><td></td><td>купил в течение двух недель</td><td class=n>+150 ₽</td></tr>"
             "</table></div><p class=small>Ставки одинаковые у обеих. Никаких коэффициентов "
             "«за старание» нет: платит только результат — пришёл ребёнок и купил ли семья абонемент.</p></div>")

    p.append("<h2>Откуда сложилась сумма</h2><div class='card'><div class=scroll><table>"
             "<tr><th>За что</th><th class=n>Ира, раз</th><th class=n>Ира, ₽</th>"
             "<th class=n>Аня, раз</th><th class=n>Аня, ₽</th></tr>")
    for key in ("дошёл на пробное +300", "купил сразу +300", "купил до 31.08 +300",
                "купил за 2 недели +150"):
        i, a = st["Ира"], st["Аня"]
        if not (i["times"].get(key) or a["times"].get(key)):
            continue
        p.append(f"<tr><td>{key}</td><td class=n>{i['times'].get(key,0)}</td>"
                 f"<td class=n>{rub(i['money'].get(key,0))}</td>"
                 f"<td class=n>{a['times'].get(key,0)}</td>"
                 f"<td class=n>{rub(a['money'].get(key,0))}</td></tr>")
    p.append(f"<tr class=sum><td>ИТОГО за август</td><td class=n></td>"
             f"<td class=n>{rub(st['Ира']['total'])}</td><td class=n></td>"
             f"<td class=n>{rub(st['Аня']['total'])}</td></tr></table></div></div>")

    p.append("<h2>Что ещё не сыграло — деньги на столе</h2><div class='card note'><div class=scroll><table>"
             "<tr><th>Осталось</th><th class=n>Ира</th><th class=n>Аня</th></tr>"
             f"<tr><td>записей, где ребёнок ещё не дошёл до пробного</td>"
             f"<td class=n>{st['Ира']['waiting_trial']}</td><td class=n>{st['Аня']['waiting_trial']}</td></tr>"
             f"<tr><td>продолжающих, которые ещё не оплатили</td>"
             f"<td class=n>{st['Ира']['waiting_pay']}</td><td class=n>{st['Аня']['waiting_pay']}</td></tr>"
             f"<tr class=sum><td>если довести всех — придёт ещё</td>"
             f"<td class=n>{rub(st['Ира']['on_table'])}</td>"
             f"<td class=n>{rub(st['Аня']['on_table'])}</td></tr>"
             "</table></div><p class=small>Это не обещание, а верхняя граница: столько "
             "принесут августовские записи, если каждый ребёнок дойдёт до занятия и купит "
             "абонемент. Каждый, кого не подтвердили накануне, вычитается отсюда живыми рублями.</p></div>")

    p.append("<h2>Почему у Иры больше — по-честному</h2><div class='card'><div class=scroll><table>"
             "<tr><th>Показатель</th><th class=n>Ира</th><th class=n>Аня</th><th>О чём говорит</th></tr>")
    i, a = st["Ира"], st["Аня"]
    ji, ja = JOURNAL["Ира"], JOURNAL["Аня"]
    per_i, per_a = ji["records"] / ji["days"], ja["records"] / ja["days"]
    conv_i = round(i["came"] * 100 / max(i["n"], 1))
    conv_a = round(a["came"] * 100 / max(a["n"], 1))
    for label, vi, va, comment in [
        ("Записей за август (по схеме)", i["n"], a["n"],
         "Объём. Главная причина разрыва в деньгах"),
        ("Записей по журналу МойКласса", ji["records"], ja["records"],
         "Кто реально нажимал. Разрыв меньше, чем по схеме, — см. оговорку ниже"),
        ("Активных дней в августе", ji["days"], ja["days"],
         "Аня работала больше дней"),
        ("Записей в день (по журналу)", f"{per_i:.1f}".replace(".", ","),
         f"{per_a:.1f}".replace(".", ","),
         "Производительность за смену — вот здесь Ира впереди по-настоящему"),
        ("Продолжающих в портфеле", f"{i['cont']} из {i['n']}", f"{a['cont']} из {a['n']}",
         "Продление к своему педагогу — работа легче холодной записи. Часть разрыва даёт база, а не человек"),
        ("Дошли до пробного", f"{i['came']} ({conv_i}%)", f"{a['came']} ({conv_a}%)",
         "Довести до двери — вторая половина работы, и здесь лучше Аня"),
    ]:
        p.append(f"<tr><td>{label}</td><td class=n>{vi}</td><td class=n>{va}</td>"
                 f"<td class=small>{comment}</td></tr>")
    p.append("</table></div></div>")

    p.append("<div class='card note'><b>Честная оговорка про метрику.</b> МойКласс не хранит, "
             "кто именно записал ребёнка, — схема начисляет по <b>ответственному менеджеру</b> "
             "записи, а его можно переназначить. По журналу действий авторство за август "
             f"{ji['records']} против {ja['records']}, по схеме — {i['n']} против {a['n']}. "
             "Разрыв в чью-то пользу тут не заложен, метрика просто шумная. Начисление можно "
             "пересобрать по автору из журнала — тогда спорить будет не о чем.</div>")

    p.append("<div class='card good'><b>Что это значит для сентября.</b> За 1–10 сентября "
             "Аня уже идёт первой. Разрыв августа — это разрыв объёма, а не способностей: "
             "больше записей в смену даёт больше денег на выходе, а лучшая доводимость до "
             "пробного превращает в деньги каждую из них. Сильнее всего растёт тот, у кого "
             "получается и то, и другое.</div>")

    for w in WHO:
        s = st[w]
        p.append(f"<h2>{w} — все {s['n']} {plural(s['n'], 'запись', 'записи', 'записей')} за август</h2>"
                 f"<div class=card><div class=scroll><table>"
                 f"<tr><th>Дата</th><th>Ребёнок</th><th>Группа</th><th>Тип</th>"
                 f"<th>Пробное</th><th>Оплата</th><th>Что засчитано</th><th class=n>₽</th></tr>")
        for r in sorted(rows[w], key=lambda x: (x["date"], x["name"])):
            tag = ('<span class="tag t-zay">заявка</span>' if r["zayavka"]
                   else '<span class="tag t-cont">продолж.</span>' if r["cont"]
                   else '<span class="tag t-new">новый</span>')
            d = r["date"][8:10] + "." + r["date"][5:7]
            cls = "" if r["bonus"] else " class=zero"
            p.append(f"<tr{cls}><td class=n>{d}</td><td>{html.escape(r['name'] or '—')}</td>"
                     f"<td class=small>{html.escape(short_group(r['group']))}</td><td>{tag}</td>"
                     f"<td>{'<span class=ok>дошёл</span>' if r['came'] else '<span class=no>—</span>'}</td>"
                     f"<td>{'<span class=ok>есть</span>' if r['paid'] else '<span class=no>—</span>'}</td>"
                     f"<td class=small>{html.escape(r['why'])}</td>"
                     f"<td class=n>{rub(r['bonus']) if r['bonus'] else '—'}</td></tr>")
        p.append(f"<tr class=sum><td colspan=7>ИТОГО {w} за август</td>"
                 f"<td class=n>{rub(s['total'])}</td></tr></table></div></div>")

    p.append(f"<p class=small>Собрано {today.strftime('%d.%m.%Y')} по живым данным CRM "
             f"(app.bonusy.collect через /api/bonusy). Пересобирается в любой день: "
             f"суммы меняются, когда записанные дети доходят до занятий и покупают абонементы.</p>")
    p.append("</div></html>")
    return "".join(p)


if __name__ == "__main__":
    data = fetch()
    OUT.write_text(page(data), encoding="utf-8")
    print(OUT, OUT.stat().st_size, "байт")
