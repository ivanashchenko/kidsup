# -*- coding: utf-8 -*-
"""Баннер в метро под световой короб 2000×1500 (ТТ Московского метрополитена).

Полотно с карманами 1900×1400 мм — фон заливается целиком.
Поле значимой информации 1670×1170 мм — весь текст и логотип внутри него
(поля по 115 мм с каждой стороны уходят под рамку короба).

Масштаб макета: 1 мм = 2 css-px → 3800×2800.
Выход:
  app/static/banner_metro.png            превью RGB (как будет в коробе, с рамкой)
  app/static/banner_metro_clean.png      превью RGB без рамки
  app/static/banner_metro_1900x1400.tif  CMYK TIFF 1:1, 72 dpi, без сжатия — в типографию
"""
import base64, io, pathlib, sys
from playwright.sync_api import sync_playwright
from PIL import Image, ImageCms
import qrcode

ROOT = pathlib.Path("/home/user/kidsup")
RAB = ROOT / "docs/rabota"
OUT = ROOT / "app/static"
MM = 2  # css-px на миллиметр

def b64(p, mime="image/png"):
    return f"data:{mime};base64," + base64.b64encode(pathlib.Path(p).read_bytes()).decode()

def font_face(name, file, weight):
    return ('@font-face{font-family:"M";font-weight:%d;src:url("%s") format("truetype")}'
            % (weight, b64(RAB / "fonts" / file, "font/ttf")))

FONTS = "".join([
    font_face("M", "Montserrat-Medium.ttf", 500),
    font_face("M", "Montserrat-SemiBold.ttf", 600),
    font_face("M", "Montserrat-Bold.ttf", 700),
    font_face("M", "Montserrat-ExtraBold.ttf", 800),
])
def logo_b64():
    """Логотип без прозрачных полей — иначе на макете он выглядит мелким."""
    im = Image.open(ROOT / "app/static/logo_white.png").convert("RGBA")
    im = im.crop(im.getchannel("A").getbbox())
    buf = io.BytesIO(); im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
LOGO = logo_b64()

def qr_png():
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=1, box_size=20)
    q.add_data("https://kidsup.ru/?utm_source=metro&utm_medium=lightbox&utm_campaign=2026_09")
    q.make(fit=True)
    img = q.make_image(fill_color="#241C6B", back_color="white").convert("RGB")
    buf = io.BytesIO(); img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

HTML = """<style>
__FONTS__
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:3800px;height:2800px;overflow:hidden;background:#241C6B}
body{font-family:"M",Arial,sans-serif;font-weight:500;color:#fff;-webkit-print-color-adjust:exact}
.b{width:3800px;height:2800px;position:relative;overflow:hidden;background:#241C6B}
.blob{position:absolute;border-radius:50%;background:#fff;opacity:.07}
.b1{width:1500px;height:1500px;right:-420px;top:-620px}
.b2{width:900px;height:900px;left:-300px;bottom:-400px}

/* поле значимой информации 1670×1170 → 3340×2340, отступ 230 */
.safe{position:absolute;left:230px;top:230px;width:3340px;height:2340px;
display:flex;flex-direction:column;gap:46px}
.guide{position:absolute;inset:0;pointer-events:none;
box-shadow:0 0 0 230px rgba(0,0,0,.55);border:6px dashed rgba(255,255,255,.7)}
.guide span{position:absolute;left:0;top:-90px;font-size:44px;font-weight:700;color:#fff}

/* шапка */
.top{display:flex;align-items:center;gap:70px;height:430px}
.top .lg{position:relative;flex:none}
.top img{height:410px;display:block}
.top .lg i{position:absolute;right:-34px;top:-6px;font-style:normal;font-size:96px;font-weight:800;line-height:1;color:#8FE04A}
.top .t h1{font-size:126px;line-height:.98;font-weight:800;letter-spacing:-.025em}
.top .t h1 span{color:#8FE04A}
.top .t p{margin-top:20px;font-size:46px;line-height:1.25;font-weight:600;color:#C9C4F0}
.top .t p b{color:#fff;font-weight:700}
.top .m{margin-left:auto;text-align:right;flex:none;max-width:900px}
.top .m .ya{display:inline-flex;align-items:center;gap:22px;background:#fff;color:#241C6B;border-radius:99px;padding:14px 40px 14px 30px;margin-bottom:24px}
.top .m .ya i{font-style:normal;color:#F59C00;font-size:56px;letter-spacing:2px;line-height:1}
.top .m .ya b{font-size:50px;font-weight:800;line-height:1}
.top .m .ya em{font-style:normal;font-size:40px;font-weight:600;color:#4A44A0}
.top .m .hm{display:block;font-size:46px;font-weight:800;color:#8FE04A;margin-bottom:18px}
.top .m b.d{display:block;font-size:54px;font-weight:800;line-height:1.05}
.top .m span{display:block;margin-top:10px;font-size:40px;line-height:1.2;font-weight:600;color:#C9C4F0}

/* четыре карточки */
.grid{flex:1;display:grid;grid-template-columns:repeat(4,1fr);gap:44px}
.c{border-radius:56px;padding:60px 56px 54px;display:flex;flex-direction:column;overflow:hidden}
.c .ic{font-size:110px;line-height:1;margin-bottom:20px}
.c h2{font-size:100px;line-height:1;font-weight:800;letter-spacing:-.02em;margin-bottom:22px}
.c .age{display:inline-block;align-self:flex-start;font-size:48px;font-weight:700;
padding:12px 36px;border-radius:99px;margin-bottom:44px;color:#fff}
.c .win{font-size:72px;font-weight:800;line-height:1.06;margin-bottom:40px}
.c ul{list-style:none;font-size:54px;line-height:1.28;font-weight:600}
.c li{padding-left:64px;position:relative;margin-bottom:22px}
.c li:before{content:"";position:absolute;left:0;top:26px;width:26px;height:26px;border-radius:50%}
.c .price{margin-top:auto;font-size:62px;font-weight:800;padding-top:22px}
.c .g{margin-top:auto;background:#F59C00;color:#fff;border-radius:32px;padding:26px 30px;font-size:46px;line-height:1.22;font-weight:700}
.c .g b{display:block;font-size:52px;font-weight:800;margin-bottom:8px}
.c1 .price{margin-top:0}

.c1{background:#FFF3D6;color:#5A3D00}.c1 .age{background:#F59C00}.c1 .win{color:#C97A00}
.c1 li:before{background:#F59C00}.c1 .price{color:#8A5C00}
.c2{background:#DDF3FE;color:#0B3F5C}.c2 .age{background:#1DA7E0}.c2 .win{color:#0F7FAE}
.c2 li:before{background:#1DA7E0}.c2 .price{color:#0B5C82}
.c3{background:#E8F7D4;color:#2E4A0C}.c3 .age{background:#7DB928}.c3 .win{color:#4F7D12}
.c3 li:before{background:#7DB928}.c3 .price{color:#3E6410}
.c4{background:#F0E8FE;color:#33196B}.c4 .age{background:#7B4FD4}.c4 .win{color:#5B2FB0}
.c4 li:before{background:#7B4FD4}.c4 .price{color:#4A2394}

/* подвал */
.foot{height:300px;background:#8FE04A;border-radius:56px;padding:0 70px;
display:flex;align-items:center;gap:60px;color:#1C3D00}
.foot .l b{display:block;font-size:66px;font-weight:800;line-height:1.05;white-space:nowrap}
.foot .l span{display:block;margin-top:16px;font-size:44px;font-weight:600}
.foot .r{margin-left:auto;text-align:right}
.foot .r b{display:block;font-size:76px;font-weight:800;line-height:1.05}
.foot .r span{display:block;margin-top:14px;font-size:44px;line-height:1.2;font-weight:600;white-space:nowrap}
.foot img{height:240px;width:240px;border-radius:28px;background:#fff;padding:12px;flex:none}
/* сноска и возрастная категория рекламы */
.note{height:64px;display:flex;align-items:center;justify-content:space-between;color:#C9C4F0;font-size:40px;font-weight:600}
.note .age0{border:5px solid #fff;color:#fff;border-radius:14px;padding:4px 22px;font-size:44px;font-weight:800;line-height:1}
</style>
<div class="b">
  <div class="blob b1"></div><div class="blob b2"></div>
  <div class="safe">
    <div class="top">
      <div class="lg"><img src="__LOGO__"><i>*</i></div>
      <div class="t">
        <h1>Учим детей тому,<br>что <span>правда пригодится</span></h1>
        <p>Детский центр и английский сад · от 1,3 до 12 лет<br><b>Образовательная лицензия</b> · оплата маткапиталом · налоговый вычет</p>
      </div>
      <div class="m">
        <div class="ya"><i>★★★★★</i><b>5,0</b><em>на Яндекс Картах</em></div>
        <span class="hm">«Хорошее место 2026»</span>
        <b class="d">5 минут пешком от метро</b><span>б-р Маршала Рокоссовского, 6к1В · напротив ТЦ «Янтарь»</span>
      </div>
    </div>

    <div class="grid">
      <div class="c c1">
        <div class="ic">📖</div>
        <h2>Подготовка<br>к школе</h2>
        <span class="age">4–7 лет</span>
        <div class="win">Читает<br>через 3 месяца</div>
        <ul>
          <li>Методика Буракова</li>
          <li>Чтение, счёт и письмо на одном занятии</li>
        </ul>
        <div class="g"><b>Гарантия</b>Не зачитал за 3 месяца — ходит бесплатно, пока не зачитает</div>
        <div class="price">от 5 000 ₽ в месяц</div>
      </div>
      <div class="c c2">
        <div class="ic">🗣️</div>
        <h2>Английский<br>язык</h2>
        <span class="age">3–12 лет</span>
        <div class="win">Заговорит<br>за первый год</div>
        <ul>
          <li>Уровни Cambridge**</li>
          <li>300 слов и 40 фраз за год</li>
          <li>Говорит с первого дня</li>
        </ul>
        <div class="price">от 5 000 ₽ в месяц</div>
      </div>
      <div class="c c3">
        <div class="ic">🧸</div>
        <h2>Мини-сад<br>&nbsp;</h2>
        <span class="age">2–4 года</span>
        <div class="win">Свободное<br>утро для мамы</div>
        <ul>
          <li>9:00–13:00, 4 занятия каждый день</li>
          <li>Английский 2 раза в неделю</li>
          <li>Видеотрансляция для родителей</li>
        </ul>
        <div class="price">от 19 600 ₽ в месяц</div>
      </div>
      <div class="c c4">
        <div class="ic">🎓</div>
        <h2>Нулевой<br>класс</h2>
        <span class="age">5–7 лет</span>
        <div class="win">Готов к школе<br>без репетиторов</div>
        <ul>
          <li>10:00–14:00, программа первого класса</li>
          <li>Английский 2 раза в неделю</li>
          <li>2–5 дней на выбор</li>
        </ul>
        <div class="price">от 26 600 ₽ в месяц</div>
      </div>
    </div>

    <div class="foot">
      <div class="l"><b>Первое занятие — условно-бесплатное, с диагностикой</b>
        <span>Покажем, где ребёнок сейчас и что подтянуть · Не понравится — платить не нужно</span></div>
      <div class="r"><b>kidsup.ru</b><span>+7 (495) 120-90-24<br>запись на сайте и по телефону</span></div>
    </div>
    <div class="note"><span>* KidsUP. Best for our kids — КидсАП. Лучшее нашим детям &nbsp;·&nbsp; ** Cambridge (Кембридж) — международная шкала уровней английского языка</span><span class="age0">0+</span></div>
    __GUIDE__
  </div>
</div>"""

def build_html(guide=False):
    g = '<div class="guide"><span>поле значимой информации 1670×1170 мм · дальше рамка короба</span></div>' if guide else ""
    return (HTML.replace("__FONTS__", FONTS).replace("__LOGO__", LOGO)
            .replace("__GUIDE__", g))

def shot(html, scale, path):
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
                               args=["--no-sandbox"])
        pg = br.new_page(viewport={"width": 3800, "height": 2800}, device_scale_factor=scale)
        pg.set_content(html); pg.wait_for_timeout(600)
        pg.screenshot(path=str(path), full_page=False)
        br.close()

if __name__ == "__main__":
    (ROOT / "docs/banner_metro.html").write_text(build_html(), encoding="utf-8")
    # превью: с рамкой и без, ширина 1900 px
    shot(build_html(guide=True), 0.5, OUT / "banner_metro.png")
    shot(build_html(), 0.5, OUT / "banner_metro_clean.png")
    # печать: 1900 мм при 72 dpi = 5386 px → scale 5386/3800
    tmp = OUT / "_banner_rgb.png"
    shot(build_html(), 5386 / 3800, tmp)
    rgb = Image.open(tmp).convert("RGB")
    print("RGB", rgb.size)
    srgb = ImageCms.createProfile("sRGB")
    cmyk = ImageCms.getOpenProfile(str(RAB / "icc/default_cmyk.icc"))
    tr = ImageCms.buildTransform(srgb, cmyk, "RGB", "CMYK", renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC)
    img = ImageCms.applyTransform(rgb, tr)
    tif = OUT / "banner_metro_1900x1400.tif"
    img.save(tif, "TIFF", compression=None, dpi=(72, 72))
    tmp.unlink()
    print("TIFF", img.mode, img.size, round(tif.stat().st_size / 1e6, 1), "MB")
