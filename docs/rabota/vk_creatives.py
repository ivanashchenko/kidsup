"""Три креатива VK под свои размеры: без кадрирования и обрезанного текста."""
from PIL import Image, ImageDraw, ImageFont

INDIGO = (49, 39, 131); GREEN = (125, 185, 40); INK = (60, 60, 80); GREY = (120, 120, 140); WHITE = (255, 255, 255)
FB = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
FR = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
LOGO = Image.open("/home/user/kidsup/app/static/logo_white.png").convert("RGBA")

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

def fit(draw, text, path, maxw, start, minimum=20):
    """Подбирает кегль так, чтобы строка гарантированно влезла в ширину."""
    s = start
    while s > minimum and draw.textlength(text, font=f(path, s)) > maxw: s -= 2
    return f(path, s)

def build(W, H, out, head="KidsUP · бульвар Рокоссовского", addr="б-р Маршала Рокоссовского, 6 к1В · 5 минут от метро"):
    im = Image.new("RGB", (W, H), WHITE); d = ImageDraw.Draw(im)
    pad = int(W * 0.06); inner = W - 2 * pad
    # шапка
    bar = int(H * 0.115) if H <= W else int(H * 0.09)
    d.rectangle([0, 0, W, bar], fill=INDIGO)
    lh = int(bar * 0.78); logo = LOGO.resize((lh, lh), Image.LANCZOS)
    im.paste(logo, (pad, (bar - lh) // 2), logo)
    hf = fit(d, head, FB, W - pad * 2 - lh - int(W * 0.03), int(bar * 0.46))
    d.text((pad + lh + int(W * 0.025), bar // 2), head, font=hf, fill=WHITE, anchor="lm")
    # заголовок
    wide = H < W * 0.8            # 16:9 — по высоте тесно, шрифты мельче
    y = bar + int(H * (0.07 if wide else 0.10))
    tf = f(FB, int(W * (0.060 if wide else 0.072)))
    lines = wrap(d, "Подготовка к школе, английский, развивашки", tf, inner)
    while len(lines) > 2 and tf.size > 24:
        tf = f(FB, tf.size - 2); lines = wrap(d, "Подготовка к школе, английский, развивашки", tf, inner)
    for ln in lines:
        d.text((W // 2, y), ln, font=tf, fill=INDIGO, anchor="ma"); y += int(tf.size * 1.25)
    # подзаголовок
    y += int(H * (0.020 if wide else 0.035))
    sf = f(FR, int(W * (0.034 if wide else 0.042)))
    for ln in ["Первое занятие условно-бесплатное:", "не понравится — платить не нужно"]:
        g = fit(d, ln, FR, inner, sf.size)
        d.text((W // 2, y), ln, font=g, fill=INK, anchor="ma"); y += int(g.size * 1.35)
    # вертикальный формат высокий — заполняем середину пользой, а не пустотой
    if H >= W * 1.1:
        y += int(H * 0.03)
        bl = f(FR, int(W * 0.040)); r = int(W * 0.011)
        items = ["Группы до 8 детей", "Кембриджская программа", "Возраст от 1,3 до 12 лет"]
        widest = max(d.textlength(t, font=bl) for t in items)
        x0 = int((W - (widest + r * 4)) / 2)
        for ln in items:
            d.ellipse([x0, y + bl.size // 2 - r, x0 + r * 2, y + bl.size // 2 + r], fill=GREEN)
            d.text((x0 + r * 4, y), ln, font=bl, fill=INK)
            y += int(bl.size * 1.9)
    # кнопка — от текста, а не наоборот
    btn_text = "Записаться на первое занятие"
    bf = fit(d, btn_text, FB, inner - int(W * 0.10), int(W * (0.038 if wide else 0.045)))
    bw = int(d.textlength(btn_text, font=bf) + W * 0.11); bh = int(bf.size * 2.1)
    bx = (W - bw) // 2
    by = max(y + int(H * 0.03), H - int(H * (0.16 if wide else 0.135)) - bh)
    by = min(by, H - int(H * 0.13) - bh)
    d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=bh // 2, fill=GREEN)
    d.text((W // 2, by + bh // 2), btn_text, font=bf, fill=WHITE, anchor="mm")
    # адрес
    af = fit(d, addr, FR, inner, int(W * 0.030))
    d.text((W // 2, H - int(H * 0.055)), addr, font=af, fill=GREY, anchor="ma")
    im.save(out, "JPEG", quality=92)
    print(out, im.size)

for w, h in ((1080, 607), (1080, 1350), (600, 600)):
    build(w, h, f"/tmp/new_{w}x{h}.jpg")
for w, h in ((1080, 607), (600, 600)):
    build(w, h, f"/tmp/bur_{w}x{h}.jpg", head="Детский клуб Буракова · Люберцы",
          addr="Люберцы, ул. 8 Марта, 43к2 · рядом с домом")
