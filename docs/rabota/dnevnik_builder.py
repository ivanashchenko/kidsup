# -*- coding: utf-8 -*-
"""Макет «Дневника достижений» A6, 12 полос — под цифровую печать.

07.09.2026, по системе /base/karta_dnevnik_sistema: обложка с гербом и именем,
разворот «моя карта» (три строки первого дня), 4 разворота по 10 клеток
под наклейки (после каждого — подарок со стойки), страница «мои уровни»
(значок за 30 наклеек), задняя обложка — правила простыми словами и телефон.

Запуск: python3 docs/rabota/dnevnik_builder.py  → app/static/dnevnik_maket.pdf
(+ превью первых полос PNG в scratchpad). Только PIL/Playwright, без отправок.
Шрифт — Montserrat (веб-замена Arlon из паспорта бренда), цвета — паспорт бренда.
"""
import base64, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
STATIC = ROOT / "app" / "static"
OUT_PDF = STATIC / "dnevnik_maket.pdf"
OUT_HTML = pathlib.Path("/tmp/claude-0/-home-user-kidsup/f2c35386-c271-55ec-b217-3b85ac2d6607/scratchpad/dnevnik.html")

def b64(p, mime):
    return f"data:{mime};base64," + base64.b64encode(pathlib.Path(p).read_bytes()).decode()

LOGO = b64(STATIC / "logo_color.png", "image/png")
LOGO_W = b64(STATIC / "logo_white.png", "image/png")
F_BOLD = b64(STATIC / "fonts" / "Montserrat-Bold.ttf", "font/ttf")
F_MED = b64(STATIC / "fonts" / "Montserrat-Medium.ttf", "font/ttf")

INDIGO, BLUE, GREEN, AMBER, RED = "#312783", "#1DA7E0", "#7DB928", "#F59C00", "#E30613"
PHONE = "+7 495 120-90-24"
ADDR = "б-р Маршала Рокоссовского, 6 к1В · 2 этаж"

CSS = f"""
@font-face{{font-family:Arlon;src:url({F_BOLD});font-weight:700}}
@font-face{{font-family:Arlon;src:url({F_MED});font-weight:500}}
@page{{size:105mm 148mm;margin:0}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:Arlon,'Montserrat',system-ui,sans-serif;color:{INDIGO};-webkit-print-color-adjust:exact;print-color-adjust:exact}}
.p{{width:105mm;height:148mm;page-break-after:always;position:relative;overflow:hidden;padding:9mm 8mm;background:#fff}}
.p:last-child{{page-break-after:auto}}
.dark{{background:{INDIGO};color:#fff}}
h1{{font-size:24pt;line-height:1.05;margin:0;font-weight:700;letter-spacing:-.01em}}
h2{{font-size:15pt;margin:0 0 3mm;font-weight:700;line-height:1.1}}
.small{{font-size:8pt;line-height:1.35;font-weight:500}}
.tiny{{font-size:6.5pt;line-height:1.3;font-weight:500;color:#6c6a86}}
.dark .tiny{{color:#cfd3ff}}
.line{{border-bottom:1.2px solid {INDIGO};height:8mm}}
.dark .line{{border-color:rgba(255,255,255,.7)}}
.grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:3mm;margin-top:5mm}}
.cell{{aspect-ratio:1/1.15;border:1.6px dashed {BLUE};border-radius:4mm;display:flex;align-items:center;justify-content:center;font-size:9pt;font-weight:700;color:{BLUE};position:relative}}
.cell.gift{{border:2px solid {AMBER};color:{AMBER}}}
.cell.level{{border:2px solid {GREEN};color:{GREEN}}}
.cell span{{position:absolute;bottom:1.2mm;right:2mm;font-size:6pt;font-weight:500;color:#6c6a86}}
.tag{{display:inline-block;padding:1mm 3mm;border-radius:3mm;font-size:7.5pt;font-weight:700;background:{BLUE};color:#fff}}
.tag.amber{{background:{AMBER}}}.tag.green{{background:{GREEN}}}
.foot{{position:absolute;left:8mm;right:8mm;bottom:6mm;display:flex;justify-content:space-between;align-items:center}}
.num{{font-size:7pt;font-weight:700;color:#6c6a86}}
.box{{border:1.5px solid {INDIGO};border-radius:4mm;padding:3.5mm 4mm;margin-top:3mm}}
.box b{{font-size:8.5pt;display:block;margin-bottom:1.5mm}}
.ring{{width:22mm;height:22mm;border-radius:50%;border:2.5px solid;display:flex;align-items:center;justify-content:center;flex-direction:column;font-weight:700;font-size:9pt;text-align:center;line-height:1.05}}
.ring i{{font-style:normal;font-size:6.5pt;font-weight:500;margin-top:1mm}}
ul{{margin:0;padding-left:4mm}}
li{{margin-bottom:1.6mm}}
"""

NOTES = [
    ("За что наклейка", "За выполненное задание или за смелую попытку. Вторая — «прорыв дня»: педагог скажет вслух, за что."),
    ("Десять наклеек — подарок 🎁", "Покажи дневник администратору на стойке. Тридцать — значок уровня."),
    ("Забыл дневник?", "Не беда: педагог помнит, наклейку получишь на следующем занятии."),
    ("Тридцать — значок уровня", "Первая ступень пройдена! Значок выдаёт администратор, педагог называет, за что."),
    ("Что дома?", "Одна вещь для дома — на первой странице. Сделал — расскажи педагогу, это тоже наклейка."),
    ("Половина пути", "Пятьдесят наклеек — это почти полгода занятий. Сравни с первой страницей: что уже умеешь?"),
    ("Шестьдесят — вторая ступень", "Второй значок. Педагог пишет отчёт прогресса — мама получит его вместе со счётом."),
    ("Весь год — 80 наклеек", "Дневник заполнен целиком — это редкость. На стойке ждёт большой подарок и сертификат года."),
]

def sticker_page(n0, page_no):
    cells = []
    for i in range(10):
        n = n0 + i
        cls, label = "cell", "★"
        if n in (30, 60):
            cls += " level"; label = "значок"
        elif n == 80:
            cls += " level"; label = "год"
        elif n % 10 == 0:
            cls += " gift"; label = "подарок"
        cells.append(f"<div class='{cls}'>{label}<span>{n}</span></div>")
    t, d = NOTES[(n0 - 1) // 10]
    hdr = f"<h2>Мои наклейки</h2><div class='small'>клетки {n0}–{n0+9} · страница {page_no - 2} из 8</div>"
    note = (f"<div class='box'><b>{t}</b><div class='small'>{d}</div></div>"
            f"<div class='box'><b>Что получилось · пишет педагог или мама</b>"
            f"<div class='line'></div><div class='line'></div><div class='line'></div></div>")
    return f"<div class='p'>{hdr}<div class='grid'>{''.join(cells)}</div>{note}" \
           f"<div class='foot'><span class='num'>{page_no}</span><img src='{LOGO}' style='height:7mm'></div></div>"

pages = []
# 1. обложка
pages.append(f"""<div class='p dark' style='display:flex;flex-direction:column;justify-content:space-between'>
<div style='text-align:center;margin-top:6mm'><img src='{LOGO_W}' style='width:38mm'></div>
<div><h1>Дневник<br>достижений</h1><div class='small' style='margin-top:3mm;color:#cfd3ff'>Каждое занятие — наклейка. Десять — подарок. Тридцать — значок ступени.</div></div>
<div><div class='tiny' style='margin-bottom:1.5mm'>Этот дневник принадлежит</div><div class='line' style='height:9mm'></div>
<div class='tiny' style='margin:3mm 0 1.5mm'>Направление · педагог</div><div class='line'></div></div>
<div class='tiny' style='text-align:center'>KidsUP · детский центр и английский сад · kidsup.ru</div></div>""")
# 2. моя карта
pages.append(f"""<div class='p'><span class='tag'>День первого занятия</span><h2 style='margin-top:3mm'>Моя карта развития</h2>
<div class='small'>Педагог заполняет после первого занятия. Мама получает такую же карту в WhatsApp — можно вклеить сюда.</div>
<div class='box'><b>Что я уже умею</b><div class='line'></div><div class='line'></div></div>
<div class='box'><b>Куда идём</b><div class='line'></div><div class='line'></div></div>
<div class='box'><b>С чего начнём · одна вещь для дома</b><div class='line'></div><div class='line'></div></div>
<div class='foot'><span class='num'>2</span><span class='tiny'>дата ______ · педагог __________</span></div></div>""")
# 3–10. наклейки: 8 полос по 10 клеток = 80 занятий, весь учебный год при 2 р/нед
for k in range(8):
    pages.append(sticker_page(k * 10 + 1, k + 3))
# 11. мои уровни
pages.append(f"""<div class='p'><span class='tag green'>Мои уровни</span><h2 style='margin-top:3mm'>Значки за ступени</h2>
<div class='small'>Значок выдаёт администратор, педагог называет вслух, за что. Тридцать наклеек — ступень, восемьдесят — весь год.</div>
<div style='display:flex;justify-content:space-between;margin-top:7mm'>
<div class='ring' style='border-color:{BLUE};color:{BLUE}'>1<i>ступень<br>30 наклеек</i></div>
<div class='ring' style='border-color:{GREEN};color:{GREEN}'>2<i>ступень<br>60 наклеек</i></div>
<div class='ring' style='border-color:{AMBER};color:{AMBER}'>★<i>весь год<br>80 наклеек</i></div></div>
<div class='box' style='margin-top:8mm'><b>Отчёт прогресса</b><div class='small'>В конце каждого абонемента педагог пишет, чему ты научился и куда идём дальше. Мама получает отчёт вместе со счётом на следующий месяц.</div>
<div class='line'></div><div class='line'></div></div>
<div class='foot'><span class='num'>11</span><img src='{LOGO}' style='height:7mm'></div></div>""")
# 12. задняя обложка
pages.append(f"""<div class='p dark'><h2 style='color:#fff'>Правила простыми словами</h2>
<ul class='small' style='color:#e9ebff'>
<li>Дневник приносим на каждое занятие. Забыл — наклейку получишь на следующем, педагог помнит.</li>
<li>Наклейка — за выполненное задание или за смелую попытку. Вторая — за «прорыв дня».</li>
<li>Десять наклеек — подарок со стойки. Тридцать и шестьдесят — значок ступени, восемьдесят — сертификат года.</li>
<li>Первая страница — карта развития: что умеешь, куда идём, с чего начнём.</li>
<li>Потерял дневник — не беда: администратор выдаст новый и перенесёт наклейки по журналу.</li>
</ul>
<div style='position:absolute;left:8mm;right:8mm;bottom:8mm'>
<div style='display:flex;align-items:center;gap:4mm'><img src='{LOGO_W}' style='height:12mm'>
<div class='tiny' style='color:#cfd3ff'>Детский центр и английский сад KidsUP<br>{ADDR}<br><b style='color:#fff;font-size:9pt'>{PHONE}</b> · kidsup.ru</div></div></div></div>""")

html = f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{''.join(pages)}</body></html>"
OUT_HTML.write_text(html, encoding="utf-8")

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome", args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 397, "height": 559}, device_scale_factor=3)
    pg.goto("file://" + str(OUT_HTML), wait_until="networkidle")
    pg.pdf(path=str(OUT_PDF), width="105mm", height="148mm", print_background=True,
           margin={"top": "0", "bottom": "0", "left": "0", "right": "0"}, prefer_css_page_size=True)
    # превью: обложка, карта, разворот наклеек, задник
    pg.emulate_media(media="print")
    for i, name in ((0, "cover"), (1, "karta"), (5, "stickers"), (10, "levels"), (11, "back")):
        el = pg.locator(".p").nth(i)
        el.screenshot(path=str(OUT_HTML.parent / f"dnevnik_{name}.png"))
    b.close()
print("PDF:", OUT_PDF, OUT_PDF.stat().st_size, "байт;", len(pages), "полос")
