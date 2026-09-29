"""Карусели для Instagram / VK: HTML-слайды 1080×1350 → PNG через Playwright.

29.09 Борис: «давай начнём делать посты для соцсетей» (образец — карусель
Lumini «Подготовка к школе»: программа, расписание, цены на слайдах).
Факты — только из живых данных: цены из PRICES (app/main.py), группы с
местами — из /api/mesta/razrez, программа — из карты развития ПШ
(docs/rabota/programmy.json). Скидки и формулировки — по бренд-гиду.

Запуск: python3 docs/rabota/posty/karusel.py psh  → docs/rabota/posty/out/psh/*.png
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ST = ROOT / "app" / "static"
OUT = Path(__file__).resolve().parent / "out"

INDIGO, BLUE, GREEN, AMBER, PAPER = "#312783", "#1DA7E0", "#7DB928", "#F59C00", "#FFFFFF"
MIST = "#EEF0FB"      # светлый индиго для карточек
INK2 = "#5C5A72"      # вторичный текст


def _b64(p: Path) -> str:
    mime = "image/png" if p.suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def _font_face() -> str:
    b = base64.b64encode((ST / "fonts" / "Montserrat-Bold.ttf").read_bytes()).decode()
    m = base64.b64encode((ST / "fonts" / "Montserrat-Medium.ttf").read_bytes()).decode()
    return (f"@font-face{{font-family:M;font-weight:700;src:url(data:font/ttf;base64,{b})}}"
            f"@font-face{{font-family:M;font-weight:500;src:url(data:font/ttf;base64,{m})}}")


CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{width:1080px;height:1350px;font-family:M,Arial,sans-serif;color:%(INDIGO)s;background:%(PAPER)s;overflow:hidden}
.s{position:relative;width:1080px;height:1350px;padding:84px 80px}
.logo{position:absolute;top:44px;left:64px;height:130px}
.cnt{position:absolute;top:64px;right:72px;background:%(INDIGO)s;color:#fff;font-weight:700;font-size:30px;
     padding:12px 26px;border-radius:40px}
.tag{display:inline-block;border:4px dashed %(BLUE)s;color:%(BLUE)s;font-weight:700;font-size:34px;
     padding:10px 28px;border-radius:26px}
h1{font-weight:700;font-size:84px;line-height:1.02;letter-spacing:-1px}
h2{font-weight:700;font-size:66px;line-height:1.06;letter-spacing:-.5px}
.sub{font-weight:500;font-size:36px;line-height:1.35;color:%(INK2)s}
.card{background:%(MIST)s;border-radius:36px;padding:36px 42px}
.card b{font-weight:700}
.row{display:flex;gap:26px;align-items:flex-start}
.dot{flex:none;width:64px;height:64px;border-radius:50%%;display:flex;align-items:center;justify-content:center;
     color:#fff;font-weight:700;font-size:32px}
.foot{position:absolute;left:80px;right:80px;bottom:60px;display:flex;justify-content:space-between;
      font-weight:700;font-size:30px;color:%(INDIGO)s}
.amber{color:%(AMBER)s}.green{color:%(GREEN)s}.blue{color:%(BLUE)s}
""" % dict(INDIGO=INDIGO, PAPER=PAPER, BLUE=BLUE, MIST=MIST, INK2=INK2, AMBER=AMBER, GREEN=GREEN)


def page(body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{_font_face()}{CSS}</style></head>"
            f"<body>{body}</body></html>")


def foot() -> str:
    return "<div class='foot'><span>kidsup.ru</span><span>б-р Рокоссовского, 6 к1В</span></div>"


# ------------------------------------------------------------------ ПШ
def psh() -> list[str]:
    logo_c, logo_w = _b64(ST / "logo_color.png"), _b64(ST / "logo_white.png")
    ph1, ph2 = _b64(ST / "img/lp/podgotovka_1.jpg"), _b64(ST / "img/lp/podgotovka_2.jpg")
    N = 6
    cnt = lambda i: f"<div class='cnt'>{i}/{N}</div>"
    s = []
    # 1 — обложка
    s.append(f"""<div class='s' style='padding:0'>
      <div style='position:absolute;inset:0 0 520px 0;background:url({ph1}) center 35%/cover'></div>
      <img class='logo' src='{logo_w}' style='filter:drop-shadow(0 4px 14px rgba(0,0,0,.35))'>{cnt(1)}
      <div style='position:absolute;left:0;right:0;bottom:0;height:560px;background:{INDIGO};padding:64px 80px;
                  border-radius:56px 56px 0 0'>
        <div style='color:{AMBER};font-weight:700;font-size:34px;letter-spacing:3px'>ПОДГОТОВКА К ШКОЛЕ · 4–7 ЛЕТ</div>
        <h1 style='color:#fff;margin-top:26px'>Научим читать<br><span class='amber'>за 3 месяца!</span></h1>
        <div class='sub' style='color:#D9DBF3;margin-top:30px'>Складами, а не по буквам —<br>по технологии Николая Буракова</div>
      </div></div>""")
    # 2 — как учим
    items = [(AMBER, "📖", "Чтение складами", "Ребёнок не заучивает буквы по одной: сразу видит склад и складывает из складов слова."),
             (BLUE, "✏️", "Рука к письму", "От крупной моторики к линиям и штриховке — к маю рука готова к прописям."),
             (GREEN, "🔢", "Математика", "Счёт, сравнение, состав числа, сложение и вычитание в пределах 10."),
             (INDIGO, "🧠", "Внимание и логика", "Память, закономерности и главное — «сел и доделал» без слёз.")]
    rows = "".join(f"""<div class='row' style='margin-top:26px'><div class='dot' style='background:{c};font-size:34px'>{e}</div>
        <div><div style='font-weight:700;font-size:40px'>{t}</div><div class='sub' style='font-size:30px;margin-top:4px'>{d}</div></div></div>"""
                   for c, e, t, d in items)
    s.append(f"""<div class='s'><img class='logo' src='{logo_c}'>{cnt(2)}
      <div style='margin-top:150px'><span class='tag'>Что входит в программу</span></div>
      <h2 style='margin-top:26px'>4 линии на каждом занятии</h2>
      <div class='card' style='margin-top:30px;padding:8px 40px 34px'>{rows}</div>{foot()}</div>""")
    # 3 — результат по месяцам (карта развития ПШ1)
    steps = [("Октябрь", "узнаёт склады и составляет из них слова"),
             ("Ноябрь", "читает слова из складов"),
             ("Декабрь", "читает слова по слогам, начинается математика"),
             ("Январь", "читает короткие слова целиком"),
             ("Май", "читает слова из 7–8 букв, считает до 10 — готов к школе")]
    tl = "".join(f"""<div class='row' style='margin-top:26px;align-items:center'>
        <div style='flex:none;width:210px;font-weight:700;font-size:38px;color:{[AMBER,BLUE,GREEN,BLUE,AMBER][i]}'>{m}</div>
        <div class='sub' style='font-size:34px;color:{INDIGO}'>{t}</div></div>""" for i, (m, t) in enumerate(steps))
    s.append(f"""<div class='s'><img class='logo' src='{logo_c}'>{cnt(3)}
      <div style='margin-top:150px'><span class='tag'>Для нечитающих</span></div>
      <h2 style='margin-top:30px'>Что вы увидите<br>дома</h2>
      <div class='card' style='margin-top:40px;padding:14px 42px 44px'>{tl}</div>
      <div class='sub' style='margin-top:34px;font-size:32px'>Раз в четверть — контрольная точка и короткий отчёт родителю.</div>{foot()}</div>""")
    # 4 — фото + группа
    s.append(f"""<div class='s' style='padding:0'>
      <div style='position:absolute;inset:0;background:url({ph2}) center/cover'></div>
      <img class='logo' src='{logo_w}' style='filter:drop-shadow(0 4px 14px rgba(0,0,0,.35))'>{cnt(4)}
      <div style='position:absolute;left:60px;right:60px;bottom:70px;background:rgba(255,255,255,.95);border-radius:40px;padding:46px 50px'>
        <h2 style='font-size:56px'>До 8 детей в группе</h2>
        <div class='sub' style='margin-top:18px;font-size:34px'>Группы по ступеням: <b style='color:{INDIGO}'>ПШ1</b> — для тех, кто ещё не читает,
        <b style='color:{INDIGO}'>ПШ2</b> — для читающих. Ступень педагог определяет на первом занятии.</div>
      </div></div>""")
    # 5 — расписание (группы, где есть места, 29.09)
    grp = [("ПШ1 · 4–5 лет", ["вт-чт 16:00", "пн-чт 17:00", "ср-пт 18:00"]),
           ("ПШ1 · 5–7 лет", ["вт-чт 17:00", "вт-пт 17:00", "вт-пт 18:00", "вт-чт 19:00", "ср-пт 19:00", "пн 19:00 + сб 10:00", "сб 11:00 (1 раз в неделю)"]),
           ("ПШ2 · читающие", ["вт-чт 18:00", "пн-чт 18:00"])]
    blocks = "".join(f"""<div style='margin-top:28px'><div style='font-weight:700;font-size:36px;color:{[AMBER,BLUE,GREEN][i]}'>{t}</div>
        <div style='display:flex;flex-wrap:wrap;gap:12px;margin-top:14px'>{''.join(f"<span style='background:#fff;border-radius:22px;padding:10px 20px;font-weight:700;font-size:29px'>{x}</span>" for x in xs)}</div></div>"""
                     for i, (t, xs) in enumerate(grp))
    s.append(f"""<div class='s'><img class='logo' src='{logo_c}'>{cnt(5)}
      <div style='margin-top:150px'><span class='tag'>Расписание</span></div>
      <h2 style='margin-top:30px'>Будни после сада<br>и суббота утром</h2>
      <div class='card' style='margin-top:34px;padding:12px 40px 40px'>{blocks}</div>
      <div class='sub' style='margin-top:26px;font-size:30px'>Занятие 50 минут · места есть во всех группах выше</div>{foot()}</div>""")
    # 6 — цены и CTA
    s.append(f"""<div class='s' style='background:{INDIGO};color:#fff'><img class='logo' src='{logo_w}'>{cnt(6)}
      <h2 style='margin-top:150px;color:#fff'>Первое занятие —<br><span class='amber'>условно-бесплатное</span></h2>
      <div class='sub' style='color:#D9DBF3;margin-top:22px'>С диагностикой: не понравится — платить не нужно,<br>понравится — занятие войдёт в абонемент.</div>
      <div style='margin-top:44px;background:#fff;color:{INDIGO};border-radius:36px;padding:34px 44px;display:grid;grid-template-columns:1fr 1fr;gap:22px 30px'>
        <div><div class='sub' style='font-size:30px'>8 занятий · 2 раза в неделю</div><div style='font-weight:700;font-size:52px'>8 400 ₽</div></div>
        <div><div class='sub' style='font-size:30px'>4 занятия · 1 раз в неделю</div><div style='font-weight:700;font-size:52px'>5 000 ₽</div></div>
        <div style='grid-column:1/3;font-weight:500;font-size:29px;line-height:1.35;color:{INK2}'>
          <b style='color:{GREEN}'>−15%</b> на первый абонемент в день пробного · <b style='color:{GREEN}'>−10%</b> второму ребёнку, на второй предмет,
          многодетным и семьям участников СВО (скидки не суммируются) · маткапитал и вычет 13%</div>
      </div>
      <div style='margin-top:44px;font-weight:700;font-size:44px'>Запись: <span class='amber'>+7 495 120-90-24</span></div>
      <div class='sub' style='color:#D9DBF3;margin-top:10px;font-size:32px'>или в директ · б-р Маршала Рокоссовского, 6 к1В · kidsup.ru</div>
    </div>""")
    return s


CAPTIONS = {"psh": """Научим читать за 3 месяца! 📖

Подготовка к школе в KidsUP — для детей 4–7 лет. Читаем по технологии Николая Буракова: ребёнок не заучивает буквы по одной, а сразу видит склады и складывает из них слова. Поэтому читать получается раньше — и без слёз.

На каждом занятии 4 линии: чтение, рука к письму, математика, внимание и логика. Группы до 8 детей, по ступеням: ПШ1 — для нечитающих, ПШ2 — для читающих.

🗓 Будни после сада (16:00–19:00) и суббота утром
💰 8 занятий — 8 400 ₽, 4 занятия — 5 000 ₽
🎁 −15% на первый абонемент в день пробного

Первое занятие условно-бесплатное, с диагностикой: не понравится — платить не нужно.

📍 б-р Маршала Рокоссовского, 6 к1В (напротив ТЦ «Янтарь»), 5 минут от м. Бульвар Рокоссовского
📞 +7 495 120-90-24 · kidsup.ru

#подготовкакшколе #бульваррокоссовского #богородское #метрогородок #детскийцентр #kidsup"""}


def render(name: str) -> list[Path]:
    from playwright.sync_api import sync_playwright
    slides = {"psh": psh}[name]()
    out = OUT / name
    out.mkdir(parents=True, exist_ok=True)
    files = []
    with sync_playwright() as p:
        exe = next((str(x) for x in Path("/opt/pw-browsers").glob("chromium-*/chrome-linux*/chrome")), None)
        b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        pg = b.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
        for i, html in enumerate(slides, 1):
            (out / f"{i:02d}.html").write_text(page(html), encoding="utf-8")
            pg.set_content(page(html), wait_until="load")
            pg.wait_for_timeout(300)
            f = out / f"{name}_{i:02d}.png"
            pg.screenshot(path=str(f), full_page=False)
            files.append(f)
        b.close()
    (out / "podpis.txt").write_text(CAPTIONS[name], encoding="utf-8")
    for h in out.glob("*.html"):
        h.unlink()
    return files


if __name__ == "__main__":
    for f in render(sys.argv[1] if len(sys.argv) > 1 else "psh"):
        print(f)
