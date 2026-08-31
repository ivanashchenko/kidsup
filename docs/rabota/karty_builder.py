# -*- coding: utf-8 -*-
"""Карты развития для родителей: ПШ1, ПШ2, Лицей 3–4 года."""
import json, re
d=json.load(open("/home/user/kidsup/docs/rabota/programmy.json"))
CSS="""
@page{size:A4;margin:8mm}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:#312783;
font:12px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.sheet{max-width:194mm;margin:0 auto;padding:4mm;page-break-after:always}
.sheet:last-child{page-break-after:auto}
.top{text-align:center;margin-bottom:10px}
.top h1{font-size:19px;margin:0 0 3px;color:#312783;letter-spacing:-.01em}
.top p{margin:0;font-size:11.5px;color:#6c6a86}
table{width:100%;border-collapse:separate;border-spacing:0 5px;font-size:10.5px}
th{font-size:11px;font-weight:800;padding:7px 8px;text-align:left;border-radius:8px}
th.m{background:#eef1fb;color:#312783;width:66px}
th.a{background:#e4f4fc;color:#12668b}
th.b{background:#eef7e0;color:#4d7511}
th.c{background:#fdf0dc;color:#94600a}
th.d{background:#f6e9fb;color:#6b2e8a}
td{padding:7px 8px;vertical-align:top;background:#fbfaff;border-radius:8px}
td.m{background:#f2f4fc;font-weight:800;font-size:11px;color:#312783;white-space:nowrap}
td.a{background:#f3fafd}td.b{background:#f7fbef}td.c{background:#fffaf1}td.d{background:#fbf5fd}
td div{margin:1px 0;padding-left:9px;position:relative}
td div:before{content:"•";position:absolute;left:0;color:#9a97b8}
.res{margin-top:10px;background:#f7fbf0;border-left:4px solid #7DB928;padding:9px 12px;
border-radius:0 8px 8px 0;font-size:11.5px}
.res b{color:#4d7511}
.path{margin-top:8px;font-size:11px}
.path div{padding:4px 0;border-bottom:1px dashed #ddd9ee}
.foot{margin-top:10px;font-size:9.5px;color:#8b8a9c;text-align:center;
border-top:1px solid #e4e2f0;padding-top:6px}
.mono{font-variant-numeric:tabular-nums}
"""
def bullets(txt):
    parts=[p.strip(" .;") for p in re.split(r"(?<=[.;])\s+", txt) if p.strip(" .;")]
    if not parts or (len(parts)==1 and parts[0] in ("—","-")): return "<div>—</div>"
    return "".join(f"<div>{p}.</div>" for p in parts)

def card(key, title, sub, foot_note):
    t=d[key]["tables"][0]
    head=t[0]
    H=[f'<div class="sheet"><div class="top"><h1>{title}</h1><p>{sub}</p></div>']
    H.append('<table><tr><th class="m">Месяц</th>'
             f'<th class="a">{head[1]}</th><th class="b">{head[2]}</th>'
             f'<th class="c">{head[3]}</th><th class="d">{head[4]}</th></tr>')
    for r in t[1:]:
        H.append(f'<tr><td class="m">{r[0]}</td>'
                 f'<td class="a">{bullets(r[1])}</td><td class="b">{bullets(r[2])}</td>'
                 f'<td class="c">{bullets(r[3])}</td><td class="d">{bullets(r[4])}</td></tr>')
    H.append('</table>')
    ps=d[key]["paras"]
    i=[n for n,p in enumerate(ps) if "ИТОГ ГОДА" in p]
    if i:
        block=ps[i[0]+1:i[0]+7]
        rows="".join(f"<div>{b}</div>" for b in block if "→" in b)
        final=[b for b in block if "→" not in b]
        H.append(f'<div class="res"><b>Итог года</b><div class="path">{rows}</div>'
                 + (f'<div style="margin-top:6px">{final[0]}</div>' if final else "") + '</div>')
    H.append(f'<div class="foot">{foot_note}</div></div>')
    return "".join(H)

FOOT=("Детский центр и английский сад KidsUP · б-р Маршала Рокоссовского, 6 к1В · "
      "БЦ «Богородский», 7-й подъезд, 2 этаж · kidsup.ru · +7 919 968-35-07")
out=[f"<style>{CSS}</style>"]
out.append(card("psh1","Подготовка к школе, 1-й уровень",
    "Карта развития ребёнка за первый год обучения · 72 занятия · для нечитающих детей", FOOT))
out.append(card("psh2","Подготовка к школе, 2-й уровень",
    "Карта развития ребёнка за учебный год · для детей, которые уже читают", FOOT))

# --- лицей 3-4 года: собираем из абзацев
ps=d["malyshi34"]["paras"]
months=[]
cur=None
for p in ps:
    m=re.match(r"^(\d) МЕСЯЦ\s*—\s*(.+)$", p)
    if m:
        cur={"n":m.group(1),"title":m.group(2),"lines":[],"items":[]}
        months.append(cur); continue
    if cur is None: continue
    if p.startswith(("🔤","🔢","9 занятий","8 занятий","10 занятий")) or p.startswith("Темы"):
        cur["lines"].append(p)
    elif p=="На занятиях ребёнок:":
        continue
    elif len(cur["lines"]) and not p.startswith(("Математическое","База","Главный")):
        cur["items"].append(p)
H=[f'<div class="sheet"><div class="top">'
   f'<h1>Лицей для малышей, 3–4 года</h1>'
   f'<p>Программа развития на 9 месяцев · 82 занятия · грамота, математика, '
   f'окружающий мир, мышление</p></div>']
H.append('<table><tr><th class="m">Месяц</th><th class="a">Тема и грамота</th>'
         '<th class="c">Математика</th><th class="d">Что ребёнок осваивает</th></tr>')
for mo in months[:9]:
    gram=" ".join(x for x in mo["lines"] if x.startswith("🔤") or x.startswith("Темы") or "заняти" in x)
    math=" ".join(x for x in mo["lines"] if x.startswith("🔢"))
    items="".join(f"<div>{i}</div>" for i in mo["items"][:5])
    H.append(f'<tr><td class="m">{mo["n"]} мес.<br><span style="font-weight:600;'
             f'font-size:9px;white-space:normal;line-height:1.2">{mo["title"]}</span></td>'
             f'<td class="a">{gram.replace("🔤 Грамота:","<b>Грамота:</b>")}</td>'
             f'<td class="c">{math.replace("🔢 Математика:","")}</td>'
             f'<td class="d">{items}</td></tr>')
H.append('</table>')
tail=[p for p in ps if p.startswith(("1-й триместр","2-й триместр","3-й триместр"))]
fin=[p for p in ps if p.startswith("Мы не просто учим")]
H.append('<div class="res"><b>Математическое мышление: путь за год</b><div class="path">'
         + "".join(f"<div>{t}</div>" for t in tail) + "</div>"
         + (f'<div style="margin-top:6px">{fin[0]}</div>' if fin else "") + "</div>")
H.append(f'<div class="foot">{FOOT}</div></div>')
out.append("".join(H))
open("/home/user/kidsup/docs/karty_razvitiya.html","w",encoding="utf-8").write("".join(out))
print("карты готовы: ПШ1, ПШ2, Лицей 3–4")
