"""Креативы под VK и РСЯ: свой макет на каждый размер, нижняя зона свободна.

Площадки сами рисуют поверх картинки свои элементы внизу:
VK — зелёную кнопку «Записаться», Директ — плашку с доменом kidsup.ru.
Поэтому нижние 20% высоты держим пустыми: ни текста, ни кнопки.
Своей кнопки в макете больше нет — она дублировала кнопку площадки
и попадала ровно под неё.
"""
from PIL import Image, ImageDraw, ImageFont

INDIGO = (49, 39, 131); GREEN = (125, 185, 40); INK = (60, 60, 80); GREY = (120, 120, 140); WHITE = (255, 255, 255)
FB = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
FR = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
LOGO = Image.open("/home/user/kidsup/app/static/logo_white.png").convert("RGBA")

SAFE = 0.20          # доля высоты снизу, которую занимает интерфейс площадки

def f(path, size): return ImageFont.truetype(path, size)

def wrap(draw, text, font, maxw):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= maxw: cur = t
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines

def fit(draw, text, path, maxw, start, minimum=18):
    """Подбирает кегль так, чтобы строка гарантированно влезла в ширину."""
    s = start
    while s > minimum and draw.textlength(text, font=f(path, s)) > maxw: s -= 2
    return f(path, s)

def build(W, H, out, head="KidsUP · бульвар Рокоссовского", addr="б-р Маршала Рокоссовского, 6 к1В · 5 минут от метро"):
    im = Image.new("RGB", (W, H), WHITE); d = ImageDraw.Draw(im)
    pad = int(W * 0.06); inner = W - 2 * pad
    wide = H < W * 0.8            # 16:9 — по высоте тесно, шрифты мельче
    tall = H >= W * 1.1

    # подложка под интерфейс площадки: пустая белая полоса читается как брак вёрстки,
    # светлая плашка — как задуманный «подвал» карточки
    band = int(H * (1 - SAFE))
    d.rectangle([0, band, W, H], fill=(241, 240, 249))
    d.rectangle([0, band, W, band + max(2, int(H * 0.004))], fill=(225, 223, 240))

    # шапка
    bar = int(H * 0.115) if H <= W else int(H * 0.09)
    d.rectangle([0, 0, W, bar], fill=INDIGO)
    lh = int(bar * 0.78); logo = LOGO.resize((lh, lh), Image.LANCZOS)
    im.paste(logo, (pad, (bar - lh) // 2), logo)
    hf = fit(d, head, FB, W - pad * 2 - lh - int(W * 0.03), int(bar * 0.46))
    d.text((pad + lh + int(W * 0.025), bar // 2), head, font=hf, fill=WHITE, anchor="lm")

    # ---- собираем блок и меряем его высоту, чтобы отцентрировать в свободной зоне
    block = []                     # (тип, шрифт, текст, отступ сверху)

    tf = f(FB, int(W * (0.062 if wide else 0.072)))
    HEAD = "Подготовка к школе, английский, развивашки"
    lines = wrap(d, HEAD, tf, inner)
    while len(lines) > 2 and tf.size > 24:
        tf = f(FB, tf.size - 2); lines = wrap(d, HEAD, tf, inner)
    for i, ln in enumerate(lines):
        block.append(("t", tf, ln, 0 if i == 0 else int(tf.size * 0.25)))

    sf_size = int(W * (0.036 if wide else 0.042))
    for i, ln in enumerate(["Первое занятие условно-бесплатное:", "не понравится — платить не нужно"]):
        g = fit(d, ln, FR, inner, sf_size)
        block.append(("s", g, ln, int(H * (0.035 if wide else 0.045)) if i == 0 else int(g.size * 0.35)))

    if not wide:              # в квадрате и вертикали есть место на пользу, а не на воздух
        bl = f(FR, int(W * (0.040 if tall else 0.046)))
        for i, ln in enumerate(["Группы до 8 детей", "Кембриджская программа", "Возраст от 1,3 до 12 лет"]):
            block.append(("b", bl, ln, int(H * 0.035) if i == 0 else int(bl.size * 0.85)))

    af = fit(d, addr, FR, inner, int(W * (0.028 if wide else 0.030)))
    block.append(("a", af, addr, int(H * (0.045 if wide else 0.055))))

    total = sum(gap + font.size for _, font, _, gap in block)
    top, bottom = bar, int(H * (1 - SAFE))
    y = top + max(int(H * 0.03), (bottom - top - total) // 2)

    r = int(W * 0.011)
    bullets = [b for b in block if b[0] == "b"]
    x0 = 0
    if bullets:
        widest = max(d.textlength(t, font=fo) for _, fo, t, _ in bullets)
        x0 = int((W - (widest + r * 4)) / 2)
    for kind, font, text, gap in block:
        y += gap
        if kind == "t":   d.text((W // 2, y), text, font=font, fill=INDIGO, anchor="ma")
        elif kind == "s": d.text((W // 2, y), text, font=font, fill=INK, anchor="ma")
        elif kind == "a": d.text((W // 2, y), text, font=font, fill=GREY, anchor="ma")
        else:
            d.ellipse([x0, y + font.size // 2 - r, x0 + r * 2, y + font.size // 2 + r], fill=GREEN)
            d.text((x0 + r * 4, y), text, font=font, fill=INK)
        y += font.size
    assert y <= bottom, f"{out}: блок вылез в зону площадки ({y} > {bottom})"
    im.save(out, "JPEG", quality=92)
    print(out, im.size, "низ блока", y, "из", bottom)

if __name__ == "__main__":
    for w, h in ((1080, 607), (1080, 1350), (600, 600)):
        build(w, h, f"/tmp/new_{w}x{h}.jpg")
    for w, h in ((1080, 607), (600, 600)):
        build(w, h, f"/tmp/bur_{w}x{h}.jpg", head="Детский клуб Буракова · Люберцы",
              addr="Люберцы, ул. 8 Марта, 43к2 · рядом с домом")
