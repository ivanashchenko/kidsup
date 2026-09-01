# -*- coding: utf-8 -*-
"""Баннер в метро: 4 направления, сетка 2×2. Формат 1000×1400 (вертикальный стикер)."""
import base64, pathlib
def b64(p):
    return "data:image/png;base64," + base64.b64encode(pathlib.Path(p).read_bytes()).decode()
LOGO=b64("/home/user/kidsup/app/static/logo_white.png")

HTML = """<style>
@font-face{font-family:x;src:local("Arial")}
*{box-sizing:border-box;margin:0;padding:0}
body{width:1000px;height:1400px;overflow:hidden;
font:16px/1.2 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
-webkit-print-color-adjust:exact}
.b{width:1000px;height:1400px;background:#241C6B;display:flex;flex-direction:column;
position:relative;overflow:hidden}

/* шапка */
.top{padding:30px 40px 20px;position:relative;z-index:2}
.top img{width:82px;display:block;margin-bottom:14px}
.top h1{font-size:58px;line-height:.98;color:#fff;font-weight:800;letter-spacing:-.025em}
.top h1 span{color:#8FE04A}
.top p{margin-top:10px;font-size:22px;color:#C9C4F0;font-weight:500}

/* сетка 2×2 */
.grid{flex:1;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;
gap:12px;padding:0 26px 18px}
.c{border-radius:22px;padding:22px 24px 20px;position:relative;overflow:hidden;
display:flex;flex-direction:column}
.c .ic{font-size:40px;line-height:1;margin-bottom:8px}
.c h2{font-size:32px;line-height:1.02;font-weight:800;letter-spacing:-.02em;margin-bottom:6px}
.c .age{display:inline-block;font-size:17px;font-weight:700;padding:4px 13px;
border-radius:99px;margin-bottom:12px;align-self:flex-start}
.c .win{font-size:27px;font-weight:800;line-height:1.08;margin-bottom:10px}
.c ul{list-style:none;font-size:18px;line-height:1.36}
.c li{padding-left:22px;position:relative;margin-bottom:4px}
.c li:before{content:"";position:absolute;left:0;top:8px;width:9px;height:9px;
border-radius:50%}
.c .price{margin-top:auto;font-size:20px;font-weight:700;padding-top:10px}

/* цвета карточек */
.c1{background:#FFF3D6;color:#5A3D00}
.c1 .age{background:#F59C00;color:#fff}.c1 .win{color:#C97A00}
.c1 li:before{background:#F59C00}.c1 .price{color:#8A5C00}
.c2{background:#DDF3FE;color:#0B3F5C}
.c2 .age{background:#1DA7E0;color:#fff}.c2 .win{color:#0F7FAE}
.c2 li:before{background:#1DA7E0}.c2 .price{color:#0B5C82}
.c3{background:#E8F7D4;color:#2E4A0C}
.c3 .age{background:#7DB928;color:#fff}.c3 .win{color:#4F7D12}
.c3 li:before{background:#7DB928}.c3 .price{color:#3E6410}
.c4{background:#F0E8FE;color:#33196B}
.c4 .age{background:#7B4FD4;color:#fff}.c4 .win{color:#5B2FB0}
.c4 li:before{background:#7B4FD4}.c4 .price{color:#4A2394}

/* подвал */
.foot{background:#8FE04A;padding:20px 40px;display:flex;align-items:center;
justify-content:space-between;gap:20px}
.foot .l{color:#1C3D00}
.foot .l b{display:block;font-size:28px;font-weight:800;line-height:1.1;margin-bottom:4px}
.foot .l span{font-size:19px;font-weight:600}
.foot .r{text-align:right;color:#1C3D00}
.foot .r b{display:block;font-size:24px;font-weight:800}
.foot .r span{font-size:17px;font-weight:600}
.blob{position:absolute;border-radius:50%;opacity:.10;background:#fff}
.b1{width:520px;height:520px;right:-160px;top:-140px}
</style>
<div class="b">
  <div class="blob b1"></div>
  <div class="top">
    <img src="__LOGO__">
    <h1>Учим детей<br>тому, что <span>правда<br>пригодится</span></h1>
    <p>Детский центр и английский сад · Бульвар Рокоссовского</p>
  </div>

  <div class="grid">
    <div class="c c1">
      <div class="ic">📖</div>
      <h2>Подготовка<br>к школе</h2>
      <span class="age">4–7 лет</span>
      <div class="win">Читает<br>через 3 месяца</div>
      <ul>
        <li>Методика Буракова</li>
        <li>Чтение, счёт и письмо<br>на одном занятии</li>
        <li>Группы 6–8 детей</li>
      </ul>
      <div class="price">от 5 000 ₽ в месяц</div>
    </div>

    <div class="c c2">
      <div class="ic">🇬🇧</div>
      <h2>Английский<br>язык</h2>
      <span class="age">3–12 лет</span>
      <div class="win">Заговорит<br>за первый год</div>
      <ul>
        <li>Уровни Cambridge</li>
        <li>300 слов и 40 фраз<br>за год</li>
        <li>Говорит с первого дня</li>
      </ul>
      <div class="price">от 5 000 ₽ в месяц</div>
    </div>

    <div class="c c3">
      <div class="ic">🧸</div>
      <h2>Мини-сад</h2>
      <span class="age">2–4 года</span>
      <div class="win">Свободное утро<br>для мамы</div>
      <ul>
        <li>9:00–13:00, 4 занятия<br>каждый день</li>
        <li>Английский ежедневно</li>
        <li>Видеотрансляция в группе</li>
      </ul>
      <div class="price">от 19 600 ₽ в месяц</div>
    </div>

    <div class="c c4">
      <div class="ic">🎓</div>
      <h2>Нулевой<br>класс</h2>
      <span class="age">5–7 лет</span>
      <div class="win">Готов к школе<br>без репетиторов</div>
      <ul>
        <li>10:00–14:00, полная<br>программа первого класса</li>
        <li>Английский два раза<br>в неделю</li>
        <li>2–5 дней на выбор</li>
      </ul>
      <div class="price">от 26 600 ₽ в месяц</div>
    </div>
  </div>

  <div class="foot">
    <div class="l"><b>Первое занятие — условно-бесплатное</b>
      <span>Не понравится — платить не нужно</span></div>
    <div class="r"><b>kidsup.ru</b><span>б-р Рокоссовского, 6к1В</span></div>
  </div>
</div>"""
open("/home/user/kidsup/docs/banner_metro.html","w",encoding="utf-8").write(
    HTML.replace("__LOGO__", LOGO))
print("баннер собран")
