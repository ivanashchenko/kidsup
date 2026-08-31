# -*- coding: utf-8 -*-
import json
R=json.load(open("/home/user/kidsup/docs/rabota/angliyskiy.json"))
R=[r for r in R if not r["name"].startswith("АЯ_Заявки")]
def real(r): return [k for k in r["kids"] if not k["zay"] and k["st"]!="1. Новая заявка"]
def zay(r):  return [k for k in r["kids"] if k["st"]=="1. Новая заявка"]
def uch(r):  return [k for k in real(r) if k["st"]=="Учится"]
TOT=sum(len(real(r)) for r in R); ZAY=sum(len(zay(r)) for r in R)
SEATS=sum(r["max"] or 0 for r in R)
CSS="""
:root{--ink:#15132e;--muted:#6c6a86;--line:#e4e2f0;--bg:#f8f7fc;--card:#fff;
--indigo:#312783;--blue:#1DA7E0;--green:#7DB928;--amber:#F59C00;--red:#E30613}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:20px 14px 70px}
h1{font-size:24px;margin:0 0 4px;color:var(--indigo)}
.sub{color:var(--muted);font-size:14px;margin:0 0 18px}
h2{font-size:18px;margin:26px 0 8px;color:var(--indigo);border-bottom:2px solid var(--line);padding-bottom:5px}
table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);
font-weight:600;padding:8px 6px;border-bottom:2px solid var(--line)}
td{padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
.num{text-align:right;white-space:nowrap}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.tot td{font-weight:800;background:#f2f0fa}
.grp td{background:#f6f5fb;font-weight:700;font-size:13px;color:var(--indigo)}
.bar{display:inline-block;width:52px;height:8px;border-radius:99px;background:#e9e7f3;
vertical-align:middle;overflow:hidden;margin-right:6px}
.bar i{display:block;height:100%}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 15px;margin:12px 0}
.alarm{border-left:4px solid var(--red);background:#fff5f5}
.small{font-size:13px;color:var(--muted)}
.kids{font-size:13px;color:var(--muted);line-height:1.5}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:700}
.p-red{background:#fce8e9;color:#9c060f}.p-amber{background:#fdf0dc;color:#94600a}
.p-green{background:#eef7e0;color:#4d7511}.p-gray{background:#eeedf5;color:#5b5a70}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--ink:#eae8f6;--muted:#a3a1ba;
--line:#343150;--bg:#131228;--card:#1d1b36;--indigo:#ab9ff2}
:root:not([data-theme="light"]) .tot td{background:#262344}
:root:not([data-theme="light"]) .grp td{background:#232043}
:root:not([data-theme="light"]) .bar{background:#312e4d}
:root:not([data-theme="light"]) .alarm{background:#331519}
:root:not([data-theme="light"]) .p-red{background:#3d1416;color:#f38b90}
:root:not([data-theme="light"]) .p-amber{background:#3b2b0e;color:#f2b34c}
:root:not([data-theme="light"]) .p-green{background:#26361a;color:#a9d96a}
:root:not([data-theme="light"]) .p-gray{background:#2a2842;color:#a5a3bb}}
"""
def col(p): return "#E30613" if p<40 else ("#F59C00" if p<70 else "#7DB928")
H=[];A=H.append
A(f"<style>{CSS}</style><div class='wrap'>")
A("<h1>Английский — записи по группам</h1>")
A(f"<p class='sub'>Сверка CRM 31.08, 15:00 · 8 групп · кембриджские уровни</p>")
A(f"<div class='card'><b>{TOT} записей из {SEATS} мест — {round(TOT/SEATS*100)}%.</b> "
  f"Свободно {SEATS-TOT} мест. Плюс {ZAY} необработанных заявок, которые в счёт "
  "не входят — они висят в статусе «Новая заявка».</div>")
cur=None
A("<div class='scroll'><table><tr><th>Группа</th><th>Возраст</th><th>Уровень</th>"
  "<th class='num'>Мест</th><th class='num'>Записей</th><th class='num'>Заявок</th>"
  "<th class='num'>Свободно</th><th>Заполнено</th></tr>")
for r in R:
    if r["days"]!=cur:
        cur=r["days"]
        gs=[x for x in R if x["days"]==cur]
        gn=sum(len(real(x)) for x in gs); gm=sum(x["max"] or 0 for x in gs)
        A(f"<tr class='grp'><td colspan='3'>{cur} — {len(gs)} группы</td>"
          f"<td class='num'>{gm}</td><td class='num'>{gn}</td><td class='num'></td>"
          f"<td class='num'>{gm-gn}</td><td>{round(gn/gm*100) if gm else 0}%</td></tr>")
    n=len(real(r)); z=len(zay(r)); mx=r["max"] or 0; p=round(n/mx*100) if mx else 0
    zp=f"<span class='pill p-amber'>{z}</span>" if z else ""
    A(f"<tr><td><b>{r['time']}</b> <span class='small'>{r['name'].split('_')[-1]}</span></td>"
      f"<td class='small'>{r['age']}</td><td class='small'>{r['level']}</td>"
      f"<td class='num'>{mx}</td><td class='num'><b>{n}</b></td><td class='num'>{zp}</td>"
      f"<td class='num'>{mx-n}</td>"
      f"<td><span class='bar'><i style='width:{min(p,100)}%;background:{col(p)}'></i></span>{p}%</td></tr>")
A(f"<tr class='tot'><td colspan='3'>ИТОГО</td><td class='num'>{SEATS}</td>"
  f"<td class='num'>{TOT}</td><td class='num'>{ZAY}</td><td class='num'>{SEATS-TOT}</td>"
  f"<td>{round(TOT/SEATS*100)}%</td></tr></table></div>")

A("<h2>Кто в каждой группе</h2>")
A("<div class='scroll'><table><tr><th>Группа</th><th>Дети</th></tr>")
for r in R:
    ks=real(r); zs=zay(r)
    s=", ".join(k["name"] for k in ks) or "<i>пусто</i>"
    if zs: s += " · <span class='pill p-amber'>заявки:</span> " + ", ".join(k["name"] for k in zs)
    A(f"<tr><td class='small'><b>{r['days']} {r['time']}</b><br>{r['level']}</td>"
      f"<td class='kids'>{s}</td></tr>")
A("</table></div>")
A("</div>")
open("/home/user/kidsup/docs/angliyskiy.html","w",encoding="utf-8").write("\n".join(H))
print(f"ok · {TOT}/{SEATS} · заявок {ZAY}")
