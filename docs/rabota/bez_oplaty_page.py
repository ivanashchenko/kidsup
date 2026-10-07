#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Страница /bez-oplaty из bez_oplaty_rows.json и bez_oplaty_ending.json (см. bez_oplaty_build.py).

Запуск: python3 docs/rabota/bez_oplaty_page.py <папка с json> <дата данных, текст> > docs/rabota/kontrol/bez_oplaty.html
"""
from __future__ import annotations

import html
import json
import sys
from collections import defaultdict
from pathlib import Path

SRC = Path(sys.argv[1])
STAMP = sys.argv[2] if len(sys.argv) > 2 else ""
rows = json.load(open(SRC / "bez_oplaty_rows.json", encoding="utf-8"))
ending = json.load(open(SRC / "bez_oplaty_ending.json", encoding="utf-8"))

TAG = {"bal": ("bal", "завести абонемент с баланса"), "inv": ("inv", "счёт выставлен, не оплачен"),
       "ask": ("debt", "был без оплаты — собрать"), "next": ("ask", "ходит, остаток месяца не оплачен"),
       "nov": ("nov", "в октябре ещё не был — выяснить"), "debt": ("debt", "долг на балансе")}
ORDER = ["bal", "inv", "ask", "next", "nov"]


def e(x) -> str:
    return html.escape(str(x))


def tel(p: str) -> str:
    d = "".join(ch for ch in (p or "") if ch.isdigit())[-10:]
    return f'<a href="tel:+7{d}">+7 {d[:3]} {d[3:6]}-{d[6:8]}-{d[8:]}</a>' if len(d) == 10 else e(p or "—")


def rub(x) -> str:
    return f"{int(round(float(x or 0))):,}".replace(",", " ")


def main_tag(r) -> str:
    for t in ORDER:
        if t in r["tags"]:
            return t
    return "nov"


by_t = defaultdict(lambda: defaultdict(list))
for r in rows:
    by_t[r["teacher"]][r["group"]].append(r)

kids = len({r["uid"] for r in rows})
summa = sum(r["price"] for r in rows)
cnt = defaultdict(int)
for r in rows:
    cnt[main_tag(r)] += 1
    if "debt" in r["tags"]:
        cnt["debt"] += 1

out = []
out.append(f'''<!doctype html><html lang=ru><head><meta charset=utf-8><meta name=viewport content="width=device-width, initial-scale=1"><title>Без оплаты за октябрь</title>
<link rel=stylesheet href="https://fonts.googleapis.com/css2?family=Rubik:wght@500;600;700&family=Inter:wght@400;500;600&display=swap"><style>
:root{{--indigo:#312783;--blue:#1DA7E0;--green:#7DB928;--amber:#F59C00;--red:#E30613;--ink:#221F3B;--muted:#6A6F87;--line:#E4E8F3;--soft:#F5F8FD}}
*{{box-sizing:border-box}}body{{margin:0;background:#FBFCFE;color:var(--ink);font:15px/1.5 Inter,-apple-system,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:22px 16px 60px}}h1{{font:700 24px/1.2 Rubik,sans-serif;color:var(--indigo);margin:0 0 6px}}
.lead{{color:var(--muted);margin:0 0 14px;max-width:90ch}}.kpi{{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 16px}}
.kpi div{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:10px 14px;cursor:pointer}}.kpi div.on{{outline:2px solid var(--blue)}}.kpi b{{display:block;font-size:20px;color:var(--indigo)}}
.tools{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}}.tools input{{flex:1 1 240px;padding:9px 12px;border:1px solid var(--line);border-radius:10px;font-size:15px}}
h2{{font:700 18px Rubik,sans-serif;color:var(--indigo);margin:24px 0 6px}}h2 small{{font:500 14px Inter,sans-serif;color:var(--muted);margin-left:8px}}
h3{{font:600 15px Inter,sans-serif;margin:14px 0 6px;color:var(--ink)}}h3 small{{color:var(--muted);font-weight:500;margin-left:6px}}
table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden}}
th,td{{padding:8px 10px;text-align:left;border-top:1px solid var(--line);vertical-align:top}}th{{background:var(--soft);font-weight:600;font-size:13px;color:var(--muted);border-top:0}}
td.n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}td a{{color:var(--indigo);text-decoration:none}}
.tag{{display:inline-block;font-size:12px;font-weight:600;border-radius:7px;padding:2px 8px;white-space:nowrap;margin:1px 2px 1px 0}}
.bal{{background:#eef8e6;color:#3f6a12}}.inv{{background:#fdf0d9;color:#8a5a00}}.debt{{background:#fbe3e4;color:#a3201a}}.nov{{background:var(--soft);color:var(--muted)}}.ask{{background:#e3f4fb;color:#0b5f85}}
.st{{width:100%;min-width:220px;padding:6px 8px;border:1px solid var(--line);border-radius:8px;font:14px Inter,sans-serif}}.st.saved{{border-color:var(--green)}}
.hidden{{display:none}}.note{{margin-top:22px;padding:12px 16px;background:var(--soft);border:1px solid #DCE6F5;border-radius:12px;font-size:14px;color:var(--muted)}}
.plan{{margin:0 0 18px;padding:12px 16px;background:#fff;border:1px solid var(--line);border-radius:12px}}.plan ol{{margin:6px 0 0 18px;padding:0}}.plan li{{margin:4px 0}}
@media (max-width:760px){{th:nth-child(4),td:nth-child(4),th:nth-child(5),td:nth-child(5){{display:none}}}}
</style></head><body><div class=wrap>
<h1>Ходят без оплаты за октябрь</h1>
<p class=lead>Дети со статусом «Учится» в группах сезона, у которых в октябре есть хотя бы одно занятие без оплаченного абонемента (по отметке «оплачено» на записи и по абонементам ребёнка, включая годовые и на несколько групп). Данные МойКласса на {e(STAMP)}. Пробные и заявки не считаются. Явка за 05–06.10 ещё не закрыта — колонка «Был в октябре» по ней неполная.</p>
<div class=kpi>
<div data-f="" class=on><b>{len(rows)}</b>записей «ребёнок — группа» · {kids} детей</div>
<div data-f=""><b>{rub(summa)} ₽</b>ожидается по ценам групп</div>
<div data-f="bal"><b>{cnt['bal']}</b>завести абонемент с баланса</div>
<div data-f="inv"><b>{cnt['inv']}</b>счёт есть, не оплачен</div>
<div data-f="next"><b>{cnt['next']+cnt['ask']}</b>ходят, остаток не оплачен</div>
<div data-f="nov"><b>{cnt['nov']}</b>в октябре ещё не были</div>
<div data-f="debt"><b>{cnt['debt']}</b>долг на балансе</div>
</div>
<div class=plan><b>Порядок (Борис, 06.10):</b><ol>
<li><b>Лиза</b> — сначала закрыть явку за 05.10 и 06.10, иначе «был / не был» врёт.</li>
<li><b>Лена</b> — у кого деньги на балансе (обычно маткапитал): завести абонементы на октябрь — тег «завести абонемент с баланса».</li>
<li><b>Лена</b> — кто ходит, а остаток месяца не оплачен, и у кого счёт выставлен: напоминание со ссылкой (автонапоминание уходит 07.10 в 10:30 тем, кому про оплату не писали 3 дня).</li>
<li><b>Аня</b> — кто из плативших в октябре ещё не был: после закрытия явки позвонить и выяснить, ходит ли, — тег «в октябре ещё не был».</li>
<li><b>Лена</b> — абонементы, которые кончаются в ближайшие 10 дней без следующего (список внизу): предложить продление до последнего занятия.</li>
</ol></div>
<div class=tools><input id=q type=search placeholder="Имя, телефон, группа, педагог"><label><input type=checkbox id=onlyv> только те, кто уже ходил в октябре</label></div>''')


nov_rows = [r for r in rows if "nov" in r["tags"]]
nov_rows.sort(key=lambda r: ("ЛГ" in r["group"], r.get("last_visit") or "—"))
out.append(f'<h2 id=nov>Ещё не были в октябре<small>{len(nov_rows)} · статус пишут админы, сохраняется сразу</small></h2>')
out.append('<table id=novtab><thead><tr><th>Ребёнок</th><th>Телефон</th><th>Группа</th><th>Педагог</th><th>Последний визит</th><th>Деньги</th><th>Статус (что знаем / что сказали)</th></tr></thead><tbody>')
for r in nov_rows:
    dengi = []
    if "inv" in r["tags"]:
        dengi.append(f'<span class="tag inv">счёт {rub(r["price"])} не оплачен</span>')
    if "bal" in r["tags"]:
        dengi.append(f'<span class="tag bal">на балансе {rub(r["balance"])}</span>')
    if "debt" in r["tags"] and "inv" not in r["tags"]:
        dengi.append(f'<span class="tag debt">долг {rub(-r["balance"])}</span>')
    if not dengi:
        dengi.append(f'<span class="tag nov">к оплате {rub(r["price"])}</span>')
    key = f'{r["uid"]}:{r["class_id"]}'
    out.append(f'<tr data-v="0" data-tags="{" ".join(r["tags"])}"><td>{e(r["name"])}</td><td>{tel(r["phone"])}</td><td>{e(r["group"])}</td><td>{e(r["teacher"])}</td>'
               f'<td>{e(r.get("last_visit","—"))}</td><td>{"".join(dengi)}</td>'
               f'<td><input class=st data-key="{key}" placeholder="например: болел, вернётся чт 08.10 / ушли / оплатят в пт" value=""></td></tr>')
out.append('</tbody></table>')

out.append('<h2>Все записи по педагогам и группам</h2>')
for t in sorted(by_t, key=lambda k: -sum(len(v) for v in by_t[k].values())):
    groups = by_t[t]
    n = sum(len(v) for v in groups.values())
    s = sum(r["price"] for v in groups.values() for r in v)
    out.append(f'<h2 data-t>{e(t)}<small>{n} · {rub(s)} ₽</small></h2>')
    for g in sorted(groups):
        rs = groups[g]
        out.append(f'<h3 data-g>{e(g)}<small>{len(rs)} · {rub(sum(r["price"] for r in rs))} ₽</small></h3>')
        out.append('<table><thead><tr><th>Ребёнок</th><th>Телефон</th><th>Был в октябре</th><th>Последний визит</th><th>Занятий / оплачено</th><th>Последний абонемент</th><th>Последний платёж</th><th>Баланс</th><th>Ожидается</th><th>Что делать</th></tr></thead><tbody>')
        for r in sorted(rs, key=lambda x: x["name"]):
            tags = "".join(f'<span class="tag {TAG[t][0]}">{TAG[t][1]}</span>' for t in ORDER + ["debt"] if t in r["tags"] and (t != "debt" or True))
            out.append(f'<tr data-v="{1 if r["visited"] else 0}" data-tags="{" ".join(r["tags"])}"><td>{e(r["name"])}</td><td>{tel(r["phone"])}</td>'
                       f'<td>{", ".join(r["visited"]) or "—"}</td><td>{e(r.get("last_visit","—"))}</td><td class=n>{r["recs"]} / {r["covered"]}</td><td>{e(r["last_sub"])}</td><td>{e(r["last_pay"])}</td>'
                       f'<td class=n>{rub(r["balance"])}</td><td class=n>{rub(r["price"])}</td><td>{tags}</td></tr>')
        out.append('</tbody></table>')

out.append(f'<h2 id=ending>Абонементы кончаются в ближайшие 10 дней, следующего нет<small>{len(ending)}</small></h2>')
out.append('<table><thead><tr><th>Ребёнок</th><th>Телефон</th><th>Группа</th><th>Педагог</th><th>Кончается</th><th>Осталось занятий</th><th>Баланс</th><th>Цена</th></tr></thead><tbody>')
for x in ending:
    out.append(f'<tr><td>{e(x["name"])}</td><td>{tel(x["phone"])}</td><td>{e(x["group"])}</td><td>{e(x["teacher"])}</td><td>{e(x["end"])}</td><td class=n>{x["left"]}</td><td class=n>{rub(x["balance"])}</td><td class=n>{rub(x["price"])}</td></tr>')
out.append('</tbody></table>')
out.append('''<p class=note>Как считали: берём записи детей на занятия октября в группах сезона (статус «Учится»); занятие считается оплаченным по отметке МойКласса «оплачено» на записи или по оплаченному абонементу ребёнка, действующему в октябре и покрывающему группу. Пара «ребёнок — группа» в списке, если есть хотя бы одно неоплаченное занятие месяца. Логопеды часто платят за разовые занятия на месте — их строки нужно сверить с кассовым журналом. Пересчёт: docs/rabota/bez_oplaty_build.py.</p>
<script>
const q=document.getElementById('q'),ov=document.getElementById('onlyv');let f='';
function apply(){const s=q.value.trim().toLowerCase();document.querySelectorAll('tbody tr').forEach(tr=>{const ok=(!s||tr.textContent.toLowerCase().includes(s)||(tr.closest('table').previousElementSibling||{textContent:''}).textContent.toLowerCase().includes(s))&&(!ov.checked||tr.dataset.v==='1')&&(!f||(tr.dataset.tags||'').split(' ').includes(f));tr.classList.toggle('hidden',!ok)});
document.querySelectorAll('h3[data-g]').forEach(h=>{const t=h.nextElementSibling;const vis=t.querySelectorAll('tbody tr:not(.hidden)').length;h.classList.toggle('hidden',!vis);t.classList.toggle('hidden',!vis)});
document.querySelectorAll('h2[data-t]').forEach(h=>{let n=h.nextElementSibling,vis=false;while(n&&n.tagName!=='H2'){if(n.tagName==='H3'&&!n.classList.contains('hidden'))vis=true;n=n.nextElementSibling}h.classList.toggle('hidden',!vis)});}
q.addEventListener('input',apply);ov.addEventListener('change',apply);
document.querySelectorAll('.kpi div').forEach(d=>d.addEventListener('click',()=>{f=d.dataset.f||'';document.querySelectorAll('.kpi div').forEach(x=>x.classList.toggle('on',x===d));apply()}));
fetch('/api/bez-oplaty/status',{credentials:'same-origin'}).then(r=>r.json()).then(st=>{document.querySelectorAll('input.st').forEach(i=>{const v=st[i.dataset.key];if(v){i.value=v.text;i.title=(v.who||'')+' '+(v.ts||'');i.classList.add('saved')}})}).catch(()=>{});
let who=localStorage.getItem('bo_who')||'';
document.querySelectorAll('input.st').forEach(i=>{let t;const save=()=>{clearTimeout(t);t=setTimeout(()=>{if(!who){who=prompt('Кто пишет статус? (имя)')||'';try{localStorage.setItem('bo_who',who)}catch(e){}}
fetch('/api/bez-oplaty/status',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:i.dataset.key,text:i.value,who})}).then(()=>{i.classList.toggle('saved',!!i.value)}).catch(()=>{i.classList.remove('saved')})},600)};i.addEventListener('input',save);i.addEventListener('change',save)});
</script></div></body></html>''')
sys.stdout.write("\n".join(out))
