# -*- coding: utf-8 -*-
"""Страница владельца «Места в группах» (поручение Бориса 03.11.2026).

Всего мест — сколько занято — сколько пробников — сколько остаётся (= всего − занято − пробники).
Мини-сад и нулевой класс — по дням недели. Закрытые для записи группы в статистику не входят.
Обновляется 2 раза в день: данные — выгрузка сервера (/export/raw), страница — /boris/docs/mesta-v-gruppah.

Запуск:  KIDSUP_ADMIN_PASS=… python3 docs/rabota/mesta_boris.py --out mesta.html [--data DIR]
Без --data скачивает classes, joins, lessons, managers с сервера во временную папку.
Пароль берётся только из переменной окружения и передаётся curl через stdin — не печатается.
В выходной странице нет имён детей и телефонов: только числа по группам.

13.11.2026, Борис (решение М2 «сколько свободных мест считать верным — как в вашем документе»):
правило счёта вынесено в app/mesta_pravilo.py и здесь больше НЕ хранится копией. Его же берут
страницы сервера «Места» (/mesta, /api/mesta/tablica), «Подбор группы» (/enrollment) и блок мест
на пульте — поэтому числа документа и страниц совпадают до единицы. Менять правило (закрытые
группы CLOSED, потолки сада MINISAD_CAP, статусы) — только там. Здесь остались выгрузка и вёрстка.
Заодно документ перестал считать записи в группу, удалённые в МойКлассе: их список сервер отдаёт
как /export/raw/mk_udaleno.json (на сервере старше 13.11 такого адреса нет — тогда считаем без него
и пишем об этом на странице).
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import subprocess
import tempfile
from collections import defaultdict

import sys

# Тонкая обёртка: правило лежит в приложении, документ запускается из копии репозитория вне
# сервера. app/mesta_pravilo.py ничего из приложения не импортирует (ни базы, ни настроек).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from app import mesta_pravilo as pravilo  # noqa: E402

BASE = 'https://app.kidsup.ru'
TABLES = ['classes', 'joins', 'lessons', 'managers']
UDALENO = 'mk_udaleno'              # пометки «удалено в МойКлассе»; необязательная таблица выгрузки
# Правило — одно, в app/mesta_pravilo.py; имена ниже оставлены для тех, кто читал этот файл раньше.
UCHITSYA, PROBNYE, WD = pravilo.UCHITSYA, pravilo.PROBNYE, pravilo.WD
MINISAD_CAP, CLOSED, NAPR, napr = pravilo.MINISAD_CAP, pravilo.CLOSED, pravilo.NAPR, pravilo.napr
E = html.escape
NB = '\u202f'             # узкий неразрывный пробел в числах


def download(dst: str) -> None:
    pw = os.environ.get('KIDSUP_ADMIN_PASS')
    if not pw:
        raise SystemExit('нет KIDSUP_ADMIN_PASS')
    cfg = 'user = "admin:%s"\n' % pw.replace('\\', '\\\\').replace('"', '\\"')
    for t in TABLES:
        out = os.path.join(dst, t + '.json')
        for _ in range(4):
            p = subprocess.run(['curl', '-sS', '-K', '-', '-m', '300', '-o', out, '-w', '%{http_code}',
                                f'{BASE}/export/raw/{t}.json'], input=cfg, capture_output=True, text=True)
            if p.stdout.strip() == '200' and os.path.getsize(out) > 2:
                break
        else:
            raise SystemExit(f'не скачалась таблица {t}')
    # пометки «удалено в МойКлассе» — необязательны: старый сервер ответит 404, документ соберётся без них
    out = os.path.join(dst, UDALENO + '.json')
    p = subprocess.run(['curl', '-sS', '-K', '-', '-m', '120', '-o', out, '-w', '%{http_code}',
                        f'{BASE}/export/raw/{UDALENO}.json'], input=cfg, capture_output=True, text=True)
    if p.stdout.strip() != '200' and os.path.exists(out):
        os.remove(out)


def load(d: str, t: str) -> list:
    return [r for r in json.load(open(os.path.join(d, t + '.json'), encoding='utf-8')) if r]


def udaleno(d: str) -> set | None:
    """id записей в группу, удалённых в МойКлассе; None — сервер пометок не отдал."""
    try:
        return {int(r['rid']) for r in load(d, UDALENO) if r.get('tbl') == 'joins'}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def schet(d: str, today: dt.date) -> tuple[dict, bool]:
    """Счёт мест по правилу app/mesta_pravilo.py из папки с выгрузкой. → (итог правила, были ли пометки)."""
    ud = udaleno(d)
    r = pravilo.poschitat(
        [pravilo.gruppa_iz_mk(c) for c in load(d, 'classes')],
        [pravilo.zapis_iz_mk(j) for j in load(d, 'joins')],
        [pravilo.zanyatie_iz_mk(x) for x in load(d, 'lessons')],
        today, managers={m['id']: m['name'] for m in load(d, 'managers')}, udaleno_joins=ud or ())
    return r, ud is not None


def build(d: str, now: dt.datetime) -> tuple[str, dict]:
    r, est_pometki = schet(d, now.date())
    groups, closed_rows, tot = r['groups'], r['closed'], r['tot']

    # ----------------------------------------------------------------- HTML
    def num(x, neg_red=False):
        s = f'{x:,}'.replace(',', NB)
        if x < 0:
            return f'<span class="neg">−{s[1:]}</span>'
        return s

    order = list(pravilo.PORYADOK_NAPR)
    reg = [g for g in groups if 'days' not in g]
    by = defaultdict(list)
    for g in reg:
        by[g['dir']].append(g)

    dir_rows, blocks = [], []
    anchor = {dr: f'd{i}' for i, dr in enumerate(order + sorted(set(by) - set(order)))}
    for dr in order + sorted(set(by) - set(order)):
        gs = by.get(dr)
        if not gs:
            continue
        s = {k: sum(g[k] for g in gs) for k in ('cap', 'busy', 'prob')}
        free = sum(max(0, g['left']) for g in gs)
        over = sum(max(0, -g['left']) for g in gs)
        dir_rows.append(f'<tr><th scope="row"><a href="#{anchor[dr]}">{E(dr)}</a></th><td>{num(s["cap"])}</td><td>{num(s["busy"])}</td>'
                        f'<td>{num(s["prob"])}</td><td><b>{num(free)}</b>{f"<span class=over>переполнено +{over}</span>" if over else ""}</td></tr>')
        rows = []
        for g in sorted(gs, key=lambda g: (-g['left'], g['name'])):
            cls = ' class="full"' if g['left'] <= 0 else (' class="few"' if g['left'] <= 2 else '')
            rows.append(f'<tr{cls}><th scope="row">{E(g["name"])}<span class="t">{E(g["teacher"])}</span></th>'
                        f'<td data-l="всего">{num(g["cap"])}</td><td data-l="занято">{num(g["busy"])}</td>'
                        f'<td data-l="пробники">{num(g["prob"])}</td><td data-l="остаётся"><b>{num(g["left"])}</b></td></tr>')
        blocks.append(f'<section id="{anchor[dr]}"><h3>{E(dr)}</h3><div class="tw"><table class="g"><thead><tr><th>Группа</th><th>Всего</th><th>Занято</th><th>Пробники</th><th>Остаётся</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>')

    day_blocks = []
    for g in sorted([g for g in groups if 'days' in g], key=lambda g: g['dir'] != 'Мини-сад'):
        hdr = ''.join(f'<th>{w}</th>' for w in g['days'])

        def line(lab, k, bold=False):
            cells = ''.join(f'<td>{"<b>" if bold else ""}{num(v[k])}{"</b>" if bold else ""}</td>' for v in g['days'].values())
            return f'<tr><th scope="row">{lab}</th>{cells}</tr>'
        week = {k: sum(v[k] for v in g['days'].values()) for k in ('cap', 'busy', 'prob', 'left')}
        cap_note = 'пн и ср — 6 мест (комнату 3 занимает логопед), вт, чт, пт — 8' if g['dir'] == 'Мини-сад' else f'{g["cap"]} мест в день'
        day_blocks.append(f'''<section><h3>{E(g["dir"])} <span class="sub">{E(g["name"])} · {E(g["teacher"])}</span></h3>
<div class="tw"><table class="days"><thead><tr><th></th>{hdr}</tr></thead><tbody>
{line("Всего мест", "cap")}{line("Занято", "busy")}{line("Пробники", "prob")}{line("Остаётся", "left", True)}
</tbody></table></div>
<p class="note">Места: {E(cap_note)}. За неделю свободно мест-дней: <b>{num(sum(max(0, v["left"]) for v in g["days"].values()))}</b>. В статусе «Учится» — {g["busy"]} детей, на пробном — {g["prob"]}.</p></section>''')

    closed_html = ''.join(f'<li>{E(g["name"])} — {E(g["why"])}; мест {g["cap"]}, учится {g["busy"]}, пробников {g["prob"]}</li>'
                          for g in sorted(closed_rows, key=lambda g: g['name']))
    stamp = now.strftime('%d.%m.%Y %H:%M')
    udal_note = (' Записи, удалённые в МойКлассе, места не занимают.' if est_pometki else
                 ' Список записей, удалённых в МойКлассе, сервер не отдал — они могут быть посчитаны.')
    page = f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Места в группах</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Golos+Text:wght@400;500;600;700&display=swap">
<style>
:root{{--ink:#1f1b4d;--mut:#6f6c8f;--line:#e3e1ef;--bg:#fbfbfe;--card:#fff;--soft:#f3f2fa;--acc:#312783;--ok:#2f7d32;--warn:#b25d00;--bad:#c4161c}}
@media (prefers-color-scheme:dark){{:root{{--ink:#ecebff;--mut:#9d9ac2;--line:#34315a;--bg:#14122b;--card:#1c1a38;--soft:#24214a;--acc:#a99ff5;--ok:#8fd18f;--warn:#f2b24a;--bad:#ff6b6b}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Golos Text",system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
main{{max-width:980px;margin:0 auto;padding:24px 16px 56px}}h1{{font-size:27px;margin:0 0 4px;color:var(--acc)}}h2{{font-size:20px;margin:34px 0 8px}}h3{{font-size:16px;margin:22px 0 6px}}
.sub,.t{{display:block;font-size:12.5px;color:var(--mut);font-weight:400}}h3 .sub{{display:inline;margin-left:6px}}
p{{margin:6px 0;max-width:78ch}}.lead{{color:var(--mut)}}.note{{font-size:13px;color:var(--mut)}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:16px 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}.card h6{{margin:0;font-size:12px;color:var(--mut);font-weight:600;letter-spacing:.04em;text-transform:uppercase}}
.card .v{{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums}}.card.ok .v{{color:var(--ok)}}.card.bad .v{{color:var(--bad)}}
.tw{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px;background:var(--card)}}
th,td{{padding:7px 9px;border-bottom:1px solid var(--line);text-align:right;font-variant-numeric:tabular-nums;vertical-align:top}}
th[scope=row],thead th:first-child{{text-align:left;font-weight:500}}thead th{{font-size:12px;color:var(--mut);font-weight:600}}
tr.full td:last-child b{{color:var(--bad)}}tr.few td:last-child b{{color:var(--warn)}}.neg{{color:var(--bad)}}
.over{{display:block;font-size:11.5px;color:var(--bad)}}table.days td{{text-align:center}}table.days thead th{{text-align:center}}
a{{color:var(--acc)}}ul{{padding-left:20px}}li{{margin:4px 0}}
@media (max-width:640px){{table.g thead{{display:none}}table.g tr{{display:grid;grid-template-columns:repeat(4,1fr);gap:2px 8px;padding:8px 0;border-bottom:1px solid var(--line)}}
table.g th,table.g td{{border:0;padding:0;text-align:left}}table.g th{{grid-column:1/-1;padding-bottom:3px}}table.g td::before{{content:attr(data-l);display:block;font-size:11px;color:var(--mut)}}h1{{font-size:23px}}}}
</style></head><body><main>
<h1>Места в группах</h1>
<p class="lead">Обновлено {stamp} МСК по МойКлассу. Страница обновляется 2 раза в день.</p>
<div class="cards"><div class="card"><h6>Всего мест</h6><div class="v">{num(tot["cap"])}</div></div><div class="card"><h6>Занято</h6><div class="v">{num(tot["busy"])}</div></div>
<div class="card"><h6>Пробники</h6><div class="v">{num(tot["prob"])}</div></div><div class="card ok"><h6>Остаётся</h6><div class="v">{num(tot["free"])}</div></div>
<div class="card bad"><h6>Переполнено</h6><div class="v">{num(tot["over"])}</div></div></div>
<p class="note">Остаётся = всего − занято − пробники, по каждой группе. Переполненная группа не уменьшает свободные места в других группах, поэтому она показана отдельно. Мини-сад и нулевой класс в этих итогах не учтены, они ниже по дням.</p>
<h2>По направлениям</h2>
<div class="tw"><table><thead><tr><th>Направление</th><th>Всего</th><th>Занято</th><th>Пробники</th><th>Остаётся</th></tr></thead><tbody>{"".join(dir_rows)}</tbody></table></div>
<h2>Мини-сад и нулевой класс по дням</h2>
{"".join(day_blocks)}
<h2>По группам</h2>
<p class="note">Сверху — группы, где мест больше всего. Красным — мест нет, оранжевым — осталось 1–2.</p>
{"".join(blocks)}
<h2>Как считаю</h2>
<ul class="note">
<li>Всего мест — вместимость группы в МойКлассе. У мини-сада по дням: пн и ср — 6, вт, чт, пт — 8.</li>
<li>Занято — дети в статусе «Учится». Пробники — «Записался на пробное» и «Посетил пробное».</li>
<li>Мини-сад и нулевой класс: ребёнок занимает место в те дни недели, на которые у него есть запись на прошлой или следующей неделе. Пробник — если записан на этот день вперёд.</li>
<li>Один ребёнок — одно место: две записи ребёнка в одну группу (пробное и «Учится») дважды не считаются.{udal_note}</li>
<li>Тем же правилом считают страницы сервера «Места» и «Подбор группы» — числа совпадают.</li>
<li>Не в статистике: логопеды (индивидуальные занятия), «Заявки», праздники и закрытые для записи группы:</li>
</ul>
<ul class="note">{closed_html or "<li>нет</li>"}</ul>
</main></body></html>'''
    summary = {'stamp': stamp, 'tot': tot, 'groups': len(groups), 'closed': len(closed_rows), 'udaleno': est_pometki,
               'days': {g['dir']: g['days'] for g in groups if 'days' in g}}
    return page, summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--data')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).replace(tzinfo=None)
    if a.data:
        page, s = build(a.data, now)
    else:
        with tempfile.TemporaryDirectory() as d:
            download(d)
            page, s = build(d, now)
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write(page)
    print(json.dumps(s, ensure_ascii=False))


if __name__ == '__main__':
    main()
