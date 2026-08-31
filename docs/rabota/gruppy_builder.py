# -*- coding: utf-8 -*-
import json, re, collections
d=json.load(open("/home/user/kidsup/docs/rabota/audit_zapisey.json"))
REAL=[g for g in d["groups"] if not g["zayavki_group"]]
BUF=[g for g in d["groups"] if g["zayavki_group"]]
LIVE={"Учится","3. Записался на пробное","5. Посетил пробное"}
def rows(g): return [k for k in g["kids"] if k["jstname"] in LIVE and not k["zayavka"]]
def uch(g):  return sum(1 for k in rows(g) if k["jstname"]=="Учится")
DAYS=["пн","вт","ср","чт","пт","сб","вс"]
def dstr(g): return " ".join(sorted(g["days"], key=DAYS.index)) or "—"
TOT=sum(len(rows(g)) for g in REAL); SEATS=sum(g["max"] or 0 for g in REAL)
UCH=sum(uch(g) for g in REAL)

CSS="""
:root{--ink:#15132e;--muted:#6c6a86;--line:#e4e2f0;--bg:#f8f7fc;--card:#fff;
--indigo:#312783;--blue:#1DA7E0;--green:#7DB928;--amber:#F59C00;--red:#E30613}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:20px 14px 80px}
h1{font-size:25px;margin:0 0 4px;color:var(--indigo)}
.sub{color:var(--muted);font-size:14px;margin:0 0 18px}
h2{font-size:19px;margin:30px 0 8px;color:var(--indigo);border-bottom:2px solid var(--line);padding-bottom:5px}
table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);
font-weight:600;padding:7px 6px;border-bottom:2px solid var(--line);position:sticky;top:0;background:var(--bg)}
td{padding:7px 6px;border-bottom:1px solid var(--line)}
.num{text-align:right;white-space:nowrap}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.tot td{font-weight:800;background:#f2f0fa}
.sub-row td{background:#f6f5fb;font-weight:700;font-size:13px}
.bar{display:inline-block;width:56px;height:8px;border-radius:99px;background:#e9e7f3;
vertical-align:middle;overflow:hidden;margin-right:6px}
.bar i{display:block;height:100%}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 15px;margin:12px 0}
.small{font-size:13px;color:var(--muted)}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:700}
.p-red{background:#fce8e9;color:#9c060f}.p-amber{background:#fdf0dc;color:#94600a}
.p-green{background:#eef7e0;color:#4d7511}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--ink:#eae8f6;--muted:#a3a1ba;
--line:#343150;--bg:#131228;--card:#1d1b36;--indigo:#ab9ff2}
:root:not([data-theme="light"]) .tot td{background:#262344}
:root:not([data-theme="light"]) .sub-row td{background:#232043}
:root:not([data-theme="light"]) .bar{background:#312e4d}
:root:not([data-theme="light"]) .p-red{background:#3d1416;color:#f38b90}
:root:not([data-theme="light"]) .p-amber{background:#3b2b0e;color:#f2b34c}
:root:not([data-theme="light"]) .p-green{background:#26361a;color:#a9d96a}}
"""
def col(p): return "#E30613" if p<40 else ("#F59C00" if p<70 else "#7DB928")
H=[];A=H.append
A(f"<style>{CSS}</style><div class='wrap'>")
A("<h1>Группы 2026/27 — записи по каждой группе</h1>")
A(f"<p class='sub'>Сверка CRM 31.08 · {len(REAL)} групп · считаем ЗАПИСИ, "
  "а не детей: один ребёнок может быть записан на несколько предметов, "
  "и каждая запись занимает своё место в своей группе</p>")
A(f"<div class='card'><b>{TOT} записей из {SEATS} мест — {round(TOT/SEATS*100)}%.</b> "
  f"Из них {UCH} со статусом «Учится» (зачислены), {TOT-UCH} записаны на пробное "
  "и деньгами ещё не стали. Свободно <b>"
  f"{SEATS-TOT} мест</b>.</div>")
by=collections.defaultdict(list)
for g in REAL: by[g["subj"]].append(g)
A("<div class='scroll'><table>")
A("<tr><th>Группа</th><th>Дни</th><th>Время</th><th class='num'>Мест</th>"
  "<th class='num'>Записей</th><th class='num'>Учится</th><th class='num'>Свободно</th>"
  "<th>Заполнено</th></tr>")
for subj in sorted(by, key=lambda s:-sum(len(rows(g)) for g in by[s])):
    gs=by[subj]
    sn=sum(len(rows(g)) for g in gs); sm=sum(g["max"] or 0 for g in gs)
    su=sum(uch(g) for g in gs)
    A(f"<tr class='sub-row'><td colspan='3'>{subj} — {len(gs)} групп</td>"
      f"<td class='num'>{sm}</td><td class='num'>{sn}</td><td class='num'>{su}</td>"
      f"<td class='num'>{sm-sn}</td><td>{round(sn/sm*100) if sm else 0}%</td></tr>")
    for g in sorted(gs, key=lambda x:-len(rows(x))):
        n=len(rows(g)); mx=g["max"] or 0; p=round(n/mx*100) if mx else 0
        free=mx-n
        fc="p-red" if free>0 and p<40 else ("p-green" if free==0 else "")
        note=" ⚠" if n>mx else ""
        A(f"<tr><td>{g['name'][:58]}{note}</td><td class='small'>{dstr(g)}</td>"
          f"<td class='small'>{g['time']}</td><td class='num'>{mx}</td>"
          f"<td class='num'><b>{n}</b></td><td class='num'>{uch(g)}</td>"
          f"<td class='num'>{free}</td>"
          f"<td><span class='bar'><i style='width:{min(p,100)}%;background:{col(p)}'></i></span>"
          f"{p}%</td></tr>")
A(f"<tr class='tot'><td colspan='3'>ИТОГО {len(REAL)} групп</td><td class='num'>{SEATS}</td>"
  f"<td class='num'>{TOT}</td><td class='num'>{UCH}</td><td class='num'>{SEATS-TOT}</td>"
  f"<td>{round(TOT/SEATS*100)}%</td></tr>")
A("</table></div>")
bz=sum(len([k for k in g['kids'] if not k['dead']]) for g in BUF)
A(f"<h2>Буферные группы «Заявки» — {bz} человек</h2>")
A("<div class='card small'>Это не занятые места, а очередь: люди оставили заявку, "
  "но в конкретную группу ещё не поставлены. В проценты заполнения не входят.</div>")
A("<div class='scroll'><table><tr><th>Группа</th><th class='num'>Заявок</th></tr>")
for g in sorted(BUF, key=lambda x:-len([k for k in x['kids'] if not k['dead']])):
    n=len([k for k in g["kids"] if not k["dead"]])
    if n: A(f"<tr><td>{g['name'][:60]}</td><td class='num'><b>{n}</b></td></tr>")
A("</table></div>")
A("</div>")
open("/home/user/kidsup/docs/gruppy_2627.html","w",encoding="utf-8").write("\n".join(H))
print(f"ok · {len(REAL)} групп · {TOT}/{SEATS} записей · буфер {bz}")
