# -*- coding: utf-8 -*-
"""Паспорт развития ребёнка — комплект родителю (ПШ1, ПШ2)."""
import json, base64, re, pathlib
d=json.load(open("/home/user/kidsup/docs/rabota/programmy.json"))
def b64(p):
    return "data:image/png;base64," + base64.b64encode(pathlib.Path(p).read_bytes()).decode()
LOGO=b64("/home/user/kidsup/app/static/logo_color.png")
LOGOW=b64("/home/user/kidsup/app/static/logo_white.png")

CSS = """
@page{size:A4;margin:0}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:#241f52;
font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
-webkit-print-color-adjust:exact;print-color-adjust:exact}
.pg{width:210mm;height:297mm;padding:14mm 13mm;page-break-after:always;position:relative;overflow:hidden}
.pg:last-child{page-break-after:auto}

/* ---------- обложка ---------- */
.cover{background:linear-gradient(150deg,#312783 0%,#3b2fa0 45%,#1DA7E0 100%);color:#fff;
display:flex;flex-direction:column;justify-content:space-between;padding:20mm 16mm}
.cover .logo{width:74px;opacity:.97}
.cover h1{font-size:44px;line-height:1.02;margin:0 0 10px;letter-spacing:-.025em;font-weight:800}
.cover .lead{font-size:16px;opacity:.93;max-width:118mm;line-height:1.45}
.namebox{background:rgba(255,255,255,.14);border:1.5px solid rgba(255,255,255,.4);
border-radius:16px;padding:16px 20px;margin-top:26px;backdrop-filter:blur(2px)}
.namebox .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.14em;opacity:.8}
.namebox .line{border-bottom:2px solid rgba(255,255,255,.55);height:30px;margin-top:5px}
.namebox .row{display:flex;gap:16px}.namebox .row>div{flex:1}
.cover .foot{font-size:11.5px;opacity:.85;line-height:1.6}
.blob{position:absolute;border-radius:50%;opacity:.13;background:#fff}
.b1{width:280px;height:280px;right:-90px;top:120px}
.b2{width:170px;height:170px;right:60px;bottom:-40px}

/* ---------- общее ---------- */
.h{display:flex;align-items:center;gap:10px;margin-bottom:4px}
.h img{width:30px}
.h .t{font-size:10px;text-transform:uppercase;letter-spacing:.16em;color:#8b88ad;font-weight:700}
h2{font-size:27px;margin:6px 0 4px;color:#312783;letter-spacing:-.02em;line-height:1.12}
.sub{color:#6f6c92;font-size:13.5px;margin:0 0 16px;max-width:150mm}

/* ---------- подход ---------- */
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:11px}
.tile{border-radius:15px;padding:15px 17px;background:#f6f5fc}
.tile .ic{font-size:21px;line-height:1;margin-bottom:6px}
.tile b{display:block;font-size:14.5px;margin-bottom:4px;color:#312783}
.tile p{margin:0;font-size:12.3px;line-height:1.5;color:#4e4a75}
.t-b{background:#eaf6fd}.t-g{background:#f1f8e6}.t-a{background:#fef4e6}.t-p{background:#f7eefc}
.nums{display:flex;gap:11px;margin:16px 0}
.num{flex:1;text-align:center;border:1.5px solid #e6e3f3;border-radius:15px;padding:14px 8px}
.num .v{font-size:31px;font-weight:800;line-height:1;color:#1DA7E0;letter-spacing:-.02em}
.num .k{font-size:11px;color:#6f6c92;margin-top:5px;line-height:1.35}
.quote{margin-top:16px;background:#312783;color:#fff;border-radius:15px;padding:16px 19px;
font-size:14px;line-height:1.5}
.quote b{color:#a8d95a}

/* ---------- маршрут года ---------- */
.road{position:relative;margin-top:6px}
.road:before{content:"";position:absolute;left:31px;top:8px;bottom:8px;width:3px;
background:linear-gradient(#1DA7E0,#7DB928);border-radius:2px}
.stop{display:flex;gap:14px;margin-bottom:9px;position:relative}
.stop .dot{flex:none;width:65px;text-align:center;position:relative;z-index:1}
.stop .dot i{display:inline-block;width:27px;height:27px;border-radius:50%;background:#fff;
border:3px solid #1DA7E0;font-style:normal;font-size:11.5px;font-weight:800;color:#312783;
line-height:22px}
.stop .m{font-size:9.5px;color:#8b88ad;margin-top:2px;text-transform:uppercase;letter-spacing:.05em}
.stop .body{flex:1;background:#f8f7fd;border-radius:13px;padding:9px 13px}
.stop .body b{font-size:12.5px;color:#312783}
.stop .body span{display:block;font-size:11.5px;color:#5c5885;line-height:1.45;margin-top:2px}
.stop:nth-child(odd) .body{background:#f2f7fb}
.result{margin-top:12px;background:linear-gradient(120deg,#7DB928,#5f9c14);color:#fff;
border-radius:15px;padding:15px 19px}
.result b{font-size:15px;display:block;margin-bottom:5px}
.result p{margin:0;font-size:12.8px;line-height:1.5;opacity:.97}

/* ---------- отметки ---------- */
.mark{border:1.5px solid #e6e3f3;border-radius:15px;padding:14px 17px;margin-bottom:11px}
.mark .when{display:inline-block;background:#312783;color:#fff;font-size:10.5px;
padding:3px 11px;border-radius:99px;letter-spacing:.06em;text-transform:uppercase;font-weight:700}
.mark .ln{border-bottom:1px solid #ddd9ee;height:21px;margin-top:7px}
.pay{margin-top:14px;background:#f1f8e6;border:1.5px solid #cfe6a8;border-radius:15px;padding:15px 18px}
.pay .big{font-size:23px;font-weight:800;color:#5f9c14;line-height:1.15}
.pay p{margin:6px 0 0;font-size:12.3px;color:#4e4a75;line-height:1.5}
.fin{position:absolute;left:13mm;right:13mm;bottom:11mm;font-size:10px;color:#9794b5;
border-top:1px solid #ece9f6;padding-top:7px;display:flex;justify-content:space-between}
"""

def road(key, title, sub, tiles, nums, quote, result_lines):
    t=d[key]["tables"][0][1:]
    stops=[]
    for r in t:
        mon=r[0].split(". ")[-1]
        n=r[0].split(".")[0]
        read=re.split(r"[.;]", r[1])[0].strip()
        rest=[]
        for c in (r[2], r[3], r[4]):
            f=re.split(r"[.;]", c)[0].strip()
            if f and f!="—": rest.append(f)
        stops.append(f'<div class="stop"><div class="dot"><i>{n}</i>'
                     f'<div class="m">{mon[:3]}</div></div>'
                     f'<div class="body"><b>{read}</b><span>{" · ".join(rest[:2])}</span></div></div>')
    return stops

def build(key, title, lead, tiles, nums, quote, marks_title):
    t=d[key]["tables"][0][1:]
    ps=d[key]["paras"]
    i=[n for n,p in enumerate(ps) if "ИТОГ ГОДА" in p]
    fin=[p for p in ps[i[0]:i[0]+8] if "→" not in p and "ИТОГ" not in p] if i else []
    H=[]
    # 1. обложка
    H.append(f'''<div class="pg cover">
<div class="blob b1"></div><div class="blob b2"></div>
<div><img class="logo" src="{LOGOW}"></div>
<div>
  <h1>Паспорт<br>развития</h1>
  <p class="lead">{lead}</p>
  <div class="namebox">
    <div class="lbl">Имя ребёнка</div><div class="line"></div>
    <div class="row" style="margin-top:14px">
      <div><div class="lbl">Группа</div><div class="line"></div></div>
      <div><div class="lbl">Педагог</div><div class="line"></div></div>
    </div>
  </div>
</div>
<div class="foot">Детский центр и английский сад KidsUP<br>
б-р Маршала Рокоссовского, 6 к1В · БЦ «Богородский», 7-й подъезд, 2 этаж<br>
kidsup.ru · +7 916 017-09-18</div></div>''')
    # 2. подход
    tl="".join(f'<div class="tile {c}"><div class="ic">{ic}</div><b>{h}</b><p>{p}</p></div>'
               for ic,h,p,c in tiles)
    nm="".join(f'<div class="num"><div class="v">{v}</div><div class="k">{k}</div></div>'
               for v,k in nums)
    H.append(f'''<div class="pg">
<div class="h"><img src="{LOGO}"><span class="t">Как мы учим</span></div>
<h2>{title}</h2>
<p class="sub">{quote[0]}</p>
<div class="grid2">{tl}</div>
<div class="nums">{nm}</div>
<div class="quote">{quote[1]}</div>
<div class="fin"><span>KidsUP · Best for our kids</span><span>kidsup.ru</span></div></div>''')
    # 3. маршрут
    stops="".join(road(key,"","",None,None,None,None))
    res = fin[0] if fin else ""
    H.append(f'''<div class="pg">
<div class="h"><img src="{LOGO}"><span class="t">Маршрут года</span></div>
<h2>Что ребёнок освоит<br>месяц за месяцем</h2>
<p class="sub">Программа расписана заранее — от первого занятия до мая. Ниже главное
за каждый месяц; полная карта по всем четырём линиям есть у администратора.</p>
{stops}
<div class="result"><b>К концу года</b><p>{res}</p></div>
<div class="fin"><span>KidsUP · Best for our kids</span><span>kidsup.ru</span></div></div>''')
    # 4. отметки + оплата
    H.append(f'''<div class="pg">
<div class="h"><img src="{LOGO}"><span class="t">Обратная связь</span></div>
<h2>{marks_title}</h2>
<p class="sub">Педагог заполняет эти строки три раза за год — вы видите движение,
а не просто «ходили». Заберите паспорт домой и приносите на встречи с педагогом.</p>
<div class="mark"><span class="when">Сентябрь · старт</span>
<div class="ln"></div><div class="ln"></div><div class="ln"></div></div>
<div class="mark"><span class="when">Декабрь · середина</span>
<div class="ln"></div><div class="ln"></div><div class="ln"></div></div>
<div class="mark"><span class="when">Май · итог</span>
<div class="ln"></div><div class="ln"></div><div class="ln"></div></div>
<div class="pay"><div class="big">Первое занятие условно-бесплатное</div>
<p>Не понравилось — платить не нужно. Понравилось — занятие входит в первый абонемент.
<b>В день первого занятия действует скидка 10% на первый абонемент.</b>
Второй предмет и второй ребёнок — тоже −10%; скидки не суммируются, действует одна.</p></div>
<div class="fin"><span>Вопросы — администратору или в WhatsApp +7 916 017-09-18</span>
<span>kidsup.ru</span></div></div>''')
    return "".join(H)

PSH1 = build("psh1", "Подготовка к школе, первый уровень",
  "Первый год обучения · 72 занятия · для детей, которые ещё не читают",
  [("📖","Чтение по складам","Методика Буракова: ребёнок не заучивает буквы по одной, "
    "а сразу видит склады и складывает из них слова. Читать получается раньше.","t-b"),
   ("✏️","Рука к письму","От крупной моторики к линиям, овалам и штриховке — "
    "к маю рука готова к прописям, а не устаёт через строчку.","t-g"),
   ("🔢","Математика","Стартует с четвёртого месяца, когда ребёнок уже привык "
    "к формату: счёт, сравнение, состав числа, сложение и вычитание.","t-a"),
   ("🧠","Интеллект","Отдельная линия занятия: внимание, память, логика. "
    "То, из-за чего в школе говорят «умный, но невнимательный».","t-p")],
  [("72","занятия<br>за учебный год"),("4","линии развития<br>на каждом занятии"),
   ("8","детей в группе<br>максимум")],
  ["Мы не гоняем ребёнка по карточкам. За год он проходит путь от первых букв "
   "до чтения целыми словами — и главное, привыкает учиться без слёз.",
   "Занятие идёт короткими блоками со сменой деятельности: чтение, потом рука, "
   "потом счёт, потом логика. <b>Ребёнок не устаёт</b> — и приходит на следующее сам."],
  "Как растёт ваш ребёнок")

PSH2 = build("psh2", "Подготовка к школе, второй уровень",
  "Для детей, которые уже читают · год до школы",
  [("📖","От слова к тексту","Начинаем со слов 5–6 букв и доходим до предложений "
    "с пониманием и выразительностью. Читать — значит понимать, а не озвучивать.","t-b"),
   ("✏️","Печатные буквы","Каждый месяц новая группа букв, графические диктанты, "
    "копирование. К весне — элементы письменных букв.","t-g"),
   ("🔢","Вычисления","Сложение и вычитание в пределах 10 доводим до автоматизма, "
    "разбираем состав чисел и переход через десяток.","t-a"),
   ("🧠","Мышление","Внимание, память, пространственное мышление и самостоятельное "
    "решение задач — то, что в первом классе важнее счёта.","t-p")],
  [("9","месяцев<br>программы"),("10","предел, в котором<br>считает свободно"),
   ("8","детей в группе<br>максимум")],
  ["Ребёнок уже читает — значит, год до школы нужен не для повторения, "
   "а для скорости, понимания и уверенности.",
   "К маю ребёнок читает текст и понимает прочитанное, считает в пределах десятка "
   "не задумываясь и <b>умеет работать самостоятельно</b> — без взрослого над плечом."],
  "Как растёт ваш ребёнок")

open("/home/user/kidsup/docs/pasport_psh1.html","w",encoding="utf-8").write(f"<style>{CSS}</style>"+PSH1)
open("/home/user/kidsup/docs/pasport_psh2.html","w",encoding="utf-8").write(f"<style>{CSS}</style>"+PSH2)
print("паспорта готовы")
