# -*- coding: utf-8 -*-
"""Печатная табличка на стойку: скидка 15% в день пробного + цены (сад/НК 8%)."""
import sys
sys.path.insert(0,"/home/user/kidsup")
import re as _re, ast as _ast
_src=open("/home/user/kidsup/app/main.py",encoding="utf-8").read()
_m=_re.search(r"^PRICES\s*=\s*(\{.*?^\})", _src, _re.S|_re.M)
PRICES=_ast.literal_eval(_m.group(1))
def f(x): return f"{x:,}".replace(",", " ")
ROWS=[]
# ставка скидки (06.09): кружки −15%, мини-сад и нулевой класс −8%; только первый абонемент
PLAN=[("Раннее развитие",None,15),("Подготовка к школе",None,15),
      ("Английский язык","Английский",15),("ИЗО-студия","ИЗО",15),("Шахматы",None,15),
      ("Скорочтение (техника чтения)","Скорочтение",15),
      ("Каллиграфия + грамота","Каллиграфия",15),
      ("Ментальная арифметика",None,15),
      ("Английский детский сад","Мини-сад и нулевой класс",8)]
for c,lab,rate in PLAN:
    pr=PRICES.get(c)
    if not pr: continue
    for title,_old,new in pr["lines"]:
        if "разово" in title.lower() or "если не купили" in title or "робное занятие" in title: continue
        ROWS.append(((lab or c), title, new, round(new*(100-rate)/100), rate))
H=f"""<style>
@page{{size:A4;margin:12mm}}
*{{box-sizing:border-box}}
body{{margin:0;background:#fff;color:#312783;
font:15px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}}
.sheet{{max-width:190mm;margin:0 auto;padding:10mm 8mm}}
.top{{text-align:center;border-bottom:3px solid #7DB928;padding-bottom:12px;margin-bottom:16px}}
.big{{font-size:44px;font-weight:900;color:#7DB928;line-height:1;letter-spacing:-.02em}}
.top h1{{font-size:23px;margin:8px 0 4px;color:#312783}}
.top p{{margin:0;font-size:14px;color:#5b5a70}}
table{{width:100%;border-collapse:collapse;font-size:14px;
font-variant-numeric:tabular-nums;margin-top:4px}}
th{{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
color:#6c6a86;font-weight:700;padding:7px 6px;border-bottom:2px solid #312783}}
td{{padding:7px 6px;border-bottom:1px solid #e4e2f0}}
.num{{text-align:right;white-space:nowrap}}
.old{{color:#8b8a9c;text-decoration:line-through;font-size:13px}}
.new{{font-weight:800;color:#7DB928;font-size:16px}}
.grp td{{background:#f4f2fb;font-weight:800;font-size:13px;color:#312783}}
.r5{{display:inline-block;margin-left:8px;padding:1px 8px;border-radius:99px;
background:#fdf0dc;color:#94600a;font-size:11px;font-weight:800}}
.note{{margin-top:14px;padding:11px 13px;background:#f7fbf0;border-left:4px solid #7DB928;
font-size:13px;color:#3d3c52;border-radius:0 8px 8px 0}}
.foot{{margin-top:14px;font-size:11px;color:#8b8a9c;text-align:center;
border-top:1px solid #e4e2f0;padding-top:8px}}
@media print{{.noprint{{display:none}}}}
</style>
<div class="sheet">
<div class="top">
  <div class="big">−15%</div>
  <h1>на первый абонемент — только сегодня,<br>в день первого занятия</h1><p style="margin-top:6px;font-size:13px;color:#94600a"><b>Мини-сад и нулевой класс — −8%</b> на первый месяц. Скидка дня — только на первый абонемент</p>
  <p>Оформите сейчас, на ресепшене — скидка действует до конца дня</p>
</div>
<table>
<tr><th>Направление</th><th>Абонемент</th><th class="num">Цена</th><th class="num">Сегодня</th></tr>
"""
prev=None
for c,title,new,disc,rate in ROWS:
    if c!=prev:
        badge=("" if rate==15 else
               f' <span class="r5">скидка −{rate}%</span>')
        H+=f'<tr class="grp"><td colspan="4">{c}{badge}</td></tr>\n'; prev=c
    H+=(f'<tr><td></td><td>{title}</td><td class="num old">{f(new)} ₽</td>'
        f'<td class="num new">{f(disc)} ₽</td></tr>\n')
H+="""</table>
<div class="note"><b>Первое занятие условно-бесплатное:</b> не понравится — платить не нужно,
понравится — оно входит в первый абонемент.<br>
Скидки не суммируются: второй ребёнок, второй предмет, многодетные и семьи участников
СВО — тоже −10%, действует одна.<br>
<b>Мини-сад и нулевой класс:</b> −8% на первый месяц. Плюс бесплатный пробный
день и пробная неделя с зачётом стоимости при покупке месяца в течение 3 дней.</div>
<div class="foot">Детский центр и английский сад KidsUP · б-р Маршала Рокоссовского, 6 к1В ·
БЦ «Богородский», 7-й подъезд, 2 этаж · kidsup.ru</div>
<p class="foot noprint">Печатать на A4, вертикально. Положить на стойку ресепшена
и держать перед глазами при разговоре с родителем.</p>
</div>"""
open("/home/user/kidsup/docs/tablichka_skidka.html","w",encoding="utf-8").write(H)
print("табличка ok ·", len(ROWS), "строк")
