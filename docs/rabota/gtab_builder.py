import json, html, re, collections, datetime
rows=json.load(open("docs/rabota/groups_now.json"))
def subj(n):
    if n.startswith("2627_РР.Музыка"): return ("Раннее развитие · «Музыка и речь»","Елена")
    if n.startswith("2627_РР.Первая"): return ("Раннее развитие · «Первая школа»","Ирина")
    if n.startswith("2627_РР.Лицей"):  return ("Раннее развитие · «Лицей для малышей»","")
    if n.startswith("2627_ПШ"):        return ("Подготовка к школе","")
    if n.startswith("2627_АЯ"):        return ("Английский язык","")
    if "Мини-сад" in n:                return ("Английский мини-сад","")
    if "Нулевой" in n:                 return ("Нулевой класс","")
    if n.startswith("2627_ИЗО"):       return ("ИЗО-студия","")
    if n.startswith("2627_МА"):        return ("Ментальная арифметика","")
    if n.startswith("2627_ШАХ"):       return ("Шахматы","")
    if n.startswith("2627_ЛГ"):        return ("Логопед","")
    return ("Прочее","")
def parts(n):
    s=re.sub(r"^2627_(РР\.)?","",n)
    for p in ("Музыка и речь_","Первая школа_","Лицей_","ПШ_","АЯ_","ИЗО_","МА_","ШАХ_","ЛГ "):
        s=s.replace(p,"")
    bits=[b for b in s.split("_") if b]
    grp=""; when=""; age=""; note=""
    for b in bits:
        if re.match(r"^Группа|^Гр\.?\d", b): grp=b
        elif re.search(r"\d{1,2}:\d{2}", b): when=(when+" "+b).strip()
        elif re.search(r"пн|вт|ср|чт|пт|сб|вс", b, re.I): when=(b+" "+when).strip()
        elif re.search(r"лет|года|год", b): age=b
        else: note=(note+" "+b).strip()
    m=re.search(r"\(Гр\.?\s*(\d+)\)", note)
    if m and not grp: grp="Гр."+m.group(1)
    note=re.sub(r"\(Гр\.?\s*\d+\)","",note).strip()
    return grp, when, age, note
ORDER=["Подготовка к школе","Английский язык","ИЗО-студия","Раннее развитие · «Первая школа»",
       "Шахматы","Английский мини-сад","Нулевой класс","Ментальная арифметика",
       "Раннее развитие · «Музыка и речь»","Раннее развитие · «Лицей для малышей»","Логопед","Прочее"]
by=collections.defaultdict(list)
for r in rows:
    s,ped=subj(r["name"]); by[(s,ped)].append(r)
blocks=[]; tot_c=tot_e=0
for s in ORDER:
    for (name,ped),lst in sorted(by.items(), key=lambda x: ORDER.index(x[0][0]) if x[0][0] in ORDER else 99):
        if name!=s: continue
        lst=sorted(lst, key=lambda x:-(x["cap"]-x["enr"]))
        cap=sum(x["cap"] for x in lst); enr=sum(x["enr"] for x in lst); free=cap-enr
        tot_c+=cap; tot_e+=enr
        trs=[]
        for x in lst:
            f=x["cap"]-x["enr"]; grp,when,age,note=parts(x["name"])
            cls="ok"; mark=""
            if x["enr"]==0: cls="empty"; mark="пустая"
            elif f<0: cls="over"; mark="перебор"
            elif f==0: cls="full"; mark="полная"
            elif f<=2: cls="near"; mark="почти полна"
            pct=int(x["enr"]*100/x["cap"]) if x["cap"] else 0
            trs.append(
              f'<tr class="{cls}"><td class="g">{html.escape(grp)}</td>'
              f'<td class="w">{html.escape(when)}</td><td class="a">{html.escape(age)}</td>'
              f'<td class="nt">{html.escape(note)}</td>'
              f'<td class="n">{x["enr"]}</td><td class="n">{x["cap"]}</td>'
              f'<td class="n f">{f}</td>'
              f'<td class="bar"><span style="width:{min(pct,100)}%"></span></td>'
              f'<td class="m">{mark}</td></tr>')
        pedtxt=f' <span class="ped">педагог {ped}</span>' if ped else ""
        blocks.append(
          f'<h2>{html.escape(name)}{pedtxt}<span>{enr} из {cap} · свободно {free}</span></h2>'
          f'<table><tr><th>Группа</th><th>Дни и время</th><th>Возраст</th><th>Особенность</th>'
          f'<th class="n">Зап.</th><th class="n">Мест</th><th class="n">Своб.</th>'
          f'<th>Заполнение</th><th>Статус</th></tr>{"".join(trs)}</table>')
empty=sum(1 for r in rows if r["enr"]==0)
full=sum(1 for r in rows if r["cap"] and r["enr"]>=r["cap"])
now=datetime.datetime.utcnow()+datetime.timedelta(hours=3)
page=f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Группы 2026/27 — заполнение по каждой</title>
<style>
body{{font:15px/1.45 -apple-system,'Segoe UI',Roboto,sans-serif;margin:0;padding:16px 18px 70px;background:#F7F7FC;color:#232046;max-width:1080px}}
h1{{font-size:1.35rem;color:#312783;margin:.3rem 0}}
.sub{{color:#5B5876;font-size:.88rem;margin:0 0 12px}}
.kpi{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 18px}}
.kpi div{{background:#fff;border:1px solid #E3E1F0;border-radius:11px;padding:8px 14px;font-size:.78rem;color:#5B5876}}
.kpi b{{display:block;font-size:1.35rem;color:#312783;line-height:1.15}}
.kpi .hot b{{color:#E30613}} .kpi .good b{{color:#5C8C1E}}
h2{{font-size:1.03rem;margin:22px 0 5px;padding:8px 13px;border-radius:9px;color:#fff;background:#312783;display:flex;align-items:baseline;gap:9px}}
h2 span{{margin-left:auto;font-weight:400;font-size:.8rem;opacity:.92}}
h2 .ped{{font-weight:400;font-size:.8rem;opacity:.85;margin-left:0}}
table{{border-collapse:collapse;width:100%;background:#fff;border-radius:11px;overflow:hidden;margin:0 0 4px}}
th{{background:#EEF3FB;color:#5B5876;font-size:.68rem;text-transform:uppercase;letter-spacing:.02em;padding:5px 9px;text-align:left;white-space:nowrap}}
td{{border-bottom:1px solid #F2F1F8;padding:5px 9px;font-size:.85rem;vertical-align:middle}}
tr:last-child td{{border-bottom:none}}
td.n,th.n{{text-align:right;font-variant-numeric:tabular-nums;width:44px}}
td.f{{font-weight:700}}
td.g{{width:74px;color:#5B5876}} td.w{{white-space:nowrap}} td.a{{width:96px;color:#5B5876;font-size:.8rem}}
td.nt{{color:#8b87ad;font-size:.78rem}}
td.bar{{width:88px}} td.bar span{{display:block;height:7px;border-radius:5px;background:#7DB928}}
td.m{{width:88px;font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.02em}}
tr.empty td{{background:#F7F7FC}} tr.empty td.m{{color:#9a96c0}} tr.empty td.bar span{{background:#D9D6EC}}
tr.near td.m{{color:#F59C00}} tr.near td.bar span{{background:#F59C00}}
tr.full td{{background:#FFF9EE}} tr.full td.m{{color:#E30613}} tr.full td.bar span{{background:#E30613}}
tr.over td{{background:#FCE9EA}} tr.over td.m{{color:#E30613}} tr.over td.bar span{{background:#E30613}}
tr.over td.f{{color:#E30613}}
.note{{background:#FFF4E0;border-left:5px solid #F59C00;padding:10px 14px;border-radius:0 10px 10px 0;margin:14px 0;font-size:.88rem;line-height:1.5}}
.note b{{color:#312783}}
.foot{{margin-top:20px;color:#5B5876;font-size:.78rem;border-top:1px solid #E3E1F0;padding-top:8px}}
</style></head><body>
<h1>Группы 2026/27 — заполнение по каждой</h1>
<p class="sub">Живые данные МойКласс на {now.strftime('%d.%m в %H:%M')}. Записи без учёта отменённых. Направления идут по размеру свободного резерва: сверху то, где больше всего мест.</p>
<div class="kpi">
  <div><b>{tot_e}</b>записей из {tot_c}</div>
  <div class="hot"><b>{tot_c-tot_e}</b>свободных мест</div>
  <div><b>{tot_e*100//tot_c}%</b>заполнение</div>
  <div><b>{len(rows)}</b>групп всего</div>
  <div class="good"><b>{full}</b>полных</div>
  <div class="hot"><b>{empty}</b>пустых</div>
</div>
{''.join(blocks)}
<div class="note"><b>Как читать.</b> Серая строка — в группе нет ни одного человека, она не стартует. Красная — мест нет или уже перебор. Жёлтая — осталось одно-два места, можно давить срочностью в разговоре. Всё остальное зелёное — сажаем свободно.</div>
<div class="foot">Обновляется из МойКласс. Живая витрина со свободными местами — <a href="/enrollment">набор 2026/27</a>.</div>
</body></html>"""
open("docs/gruppy_2627.html","w",encoding="utf-8").write(page)
print("страница собрана:", tot_e, "/", tot_c, "| пустых", empty, "| полных", full)
