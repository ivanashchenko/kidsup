"""Макеты: готовые визуалы для соцсетей и печати, собранные из живых данных CRM.

Каждый макет — самодостаточный SVG в фирменных цветах KidsUP (токены из
style.css). Страница /makety показывает превью и отдаёт PNG/SVG одной кнопкой —
без Canva и дизайнера. Тексты согласованы с фабрикой (/content): те же цифры,
те же формулировки, то же «условно-бесплатное».

Дизайн-система (design DNA):
  цвета   — голубой #2AA7DE (основной), индиго #33348E (текст/фон),
            зелёный #5FB53B (успех/CTA), янтарный #F5A81C (акция), белый фон
  формы   — круги и «леденцы» (радиус 28+), никаких острых углов
  шрифт   — системный гротеск (Arial/Helvetica): в SVG-экспорте ведёт себя
            одинаково у всех, ничего не скачиваем
"""

from __future__ import annotations

from html import escape

from .content import ADDRESS, DEADLINE, METRO, SITE, START

BLUE = "#1DA7E0"
INDIGO = "#312783"
GREEN = "#7DB928"
AMBER = "#F59C00"
RED = "#E30613"
PAPER = "#FFFFFF"
SOFT = "#E8F6FC"     # голубой ~10%
FONT = "Arial, Helvetica, sans-serif"

import base64 as _b64
from pathlib import Path as _Path

_STATIC = _Path(__file__).resolve().parent / "static"


def _logo_b64(name, cache={}):
    if name not in cache:
        cache[name] = _b64.b64encode((_STATIC / name).read_bytes()).decode()
    return cache[name]


def _logo(cx, y, w=220, white=False):
    """Настоящий герб KidsUP (PNG из брендбука), по центру cx, верх на y."""
    name = "logo_white.png" if white else "logo_color.png"
    return (f'<image x="{cx - w / 2}" y="{y}" width="{w}" height="{w * 0.9995}" '
            f'href="data:image/png;base64,{_logo_b64(name)}"/>')



def _t(x, y, size, s, fill=INDIGO, weight="bold", anchor="start", spacing=None):
    ls = f' letter-spacing="{spacing}"' if spacing else ""
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{ls}>'
            f'{escape(str(s))}</text>')




def _bubbles(w, h, seed=0):
    """Фоновые круги по углам — фирменный «воздух», не мешает тексту."""
    pts = [(-60, -60, 190, BLUE, .10), (w - 90, -80, 230, AMBER, .12),
           (w + 40, h - 160, 240, GREEN, .10), (-80, h - 120, 200, INDIGO, .07)]
    out = []
    for i, (x, y, r, c, o) in enumerate(pts):
        out.append(f'<circle cx="{x + seed * 17}" cy="{y}" r="{r}" fill="{c}" opacity="{o}"/>')
    return "".join(out)


def _badge(x, y, w, text, fill=AMBER, tcol=PAPER, size=40, h=None):
    h = h or int(size * 1.9)
    return (f'<g><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h // 2}" fill="{fill}"/>'
            + _t(x + w / 2, y + h / 2 + size * .36, size, text, tcol, "bold", "middle") + "</g>")


def _scarce_rows(f, limit=4):
    rows = []
    for g in f["scarce"][:limit]:
        rows.append((f"{g['course']}", f"{g['day']} {g['time']}", g["free"]))
    return rows


def build_makets(f: dict) -> list[dict]:
    """f — результат content.facts(): free_total, by_course, scarce, days_left."""
    makets: list[dict] = []
    free = f["free_total"]
    courses = [c["course"] for c in f["by_course"] if c["free"] > 0][:7]

    # ---------------------------------------------- 1. пост «Набор открыт» ---
    W = H = 1080
    rows = "".join(
        _t(W / 2, 712 + i * 54, 42, "· " + c + " ·", INDIGO, "normal", "middle")
        for i, c in enumerate(courses[:4]))
    makets.append({"key": "post_open", "group": "Посты 1080×1080",
        "title": "Пост «Набор открыт»", "w": W, "h": H,
        "note": "Закреп ВК, лента, Яндекс.Карты.",
        "svg": f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<rect width="{W}" height="{H}" fill="{PAPER}"/>{_bubbles(W, H)}
{_logo(W / 2, 40, 210)}
{_t(W / 2, 330, 92, "НАБОР ОТКРЫТ", INDIGO, "bold", "middle", "2")}
{_t(W / 2, 400, 50, f"на учебный год · занятия с {START}", BLUE, "bold", "middle")}
<circle cx="{W / 2}" cy="505" r="80" fill="{GREEN}"/>
{_t(W / 2, 527, 62, free, PAPER, "bold", "middle")}
{_t(W / 2, 640, 40, "свободных мест сейчас", INDIGO, "normal", "middle")}
{rows}
{_badge(W / 2 - 330, 920, 660, f"первое занятие — условно-бесплатное", GREEN, PAPER, 32)}
{_t(W / 2, 1030, 34, f"{ADDRESS} · {SITE}", INDIGO, "normal", "middle")}
</svg>'''})

    # ------------------------------------------ 2. пост «Последние места» ---
    sr = _scarce_rows(f)
    if sr:
        lines = []
        for i, (course, when, n) in enumerate(sr):
            y = 480 + i * 110
            lines.append(f'<rect x="90" y="{y - 62}" width="900" height="88" rx="44" fill="{SOFT}"/>')
            lines.append(_t(130, y, 40, f"{course} · {when}", INDIGO, "bold"))
            lines.append(f'<circle cx="930" cy="{y - 18}" r="36" fill="{RED}"/>')
            lines.append(_t(930, y - 4, 38, n, PAPER, "bold", "middle"))
        body = "".join(lines)
        head2 = "ПОСЛЕДНИЕ МЕСТА"
        sub2 = "в этих группах почти всё занято"
    else:
        body = _t(W / 2, 560, 46, "Пока можно выбрать любое удобное время —", INDIGO, "normal", "middle") + \
               _t(W / 2, 620, 46, "группы только формируются", INDIGO, "normal", "middle")
        head2 = "ВЫБИРАЙТЕ ВРЕМЯ"
        sub2 = f"запись в группы 2026/27 открыта"
    makets.append({"key": "post_scarce", "group": "Посты 1080×1080",
        "title": "Пост «Последние места»", "w": W, "h": H,
        "note": "Цифры настоящие, из CRM на момент скачивания. Если дефицита нет — макет сам превращается в «выбирайте время».",
        "svg": f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<rect width="{W}" height="{H}" fill="{PAPER}"/>{_bubbles(W, H, 1)}
{_logo(W / 2, 26, 180)}
{_t(W / 2, 290, 84, head2, RED if sr else INDIGO, "bold", "middle", "2")}
{_t(W / 2, 354, 44, sub2, INDIGO, "normal", "middle")}
{body}
{_badge(W / 2 - 330, 900, 660, f"успейте до {DEADLINE} — цены прошлого года", AMBER, PAPER, 30)}
{_t(W / 2, 1020, 34, f"{METRO} · {SITE}", INDIGO, "normal", "middle")}
</svg>'''})

    # --------------------------------------------------- 3. сторис «Набор» ---
    W2, H2 = 1080, 1920
    story_courses = "".join(
        _t(W2 / 2, 890 + i * 72, 48, c, PAPER, "normal", "middle")
        for i, c in enumerate(courses[:4]))
    makets.append({"key": "story_open", "group": "Сторис 1080×1920",
        "title": "Сторис «Набор открыт»", "w": W2, "h": H2,
        "note": "Кадр 1 из серии. CTA-стикер «напишите нам» ВК добавит сам.",
        "svg": f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W2} {H2}">
<rect width="{W2}" height="{H2}" fill="{INDIGO}"/>
<circle cx="-80" cy="-60" r="300" fill="{BLUE}" opacity=".35"/>
<circle cx="1120" cy="300" r="260" fill="{AMBER}" opacity=".55"/>
<circle cx="1140" cy="1780" r="320" fill="{GREEN}" opacity=".25"/>
<circle cx="-60" cy="1600" r="260" fill="{BLUE}" opacity=".25"/>
{_logo(W2 / 2, 100, 300, white=True)}
{_t(W2 / 2, 530, 106, "НАБОР", PAPER, "bold", "middle", "6")}
{_t(W2 / 2, 648, 106, "ОТКРЫТ", PAPER, "bold", "middle", "6")}
{_t(W2 / 2, 738, 52, f"занятия с {START}", AMBER, "bold", "middle")}
<circle cx="{W2 / 2}" cy="1350" r="130" fill="{GREEN}"/>
{_t(W2 / 2, 1330, 90, free, PAPER, "bold", "middle")}
{_t(W2 / 2, 1410, 36, "мест сейчас", PAPER, "normal", "middle")}
{story_courses}
{_badge(W2 / 2 - 360, 1560, 720, "первое занятие условно-бесплатное", GREEN, PAPER, 34)}
{_t(W2 / 2, 1740, 40, ADDRESS.split("(")[0].strip(), PAPER, "normal", "middle")}
{_t(W2 / 2, 1800, 40, METRO, PAPER, "normal", "middle")}
</svg>'''})

    # ------------------------------------------------ 4. сторис «Дедлайн» ---
    makets.append({"key": "story_deadline", "group": "Сторис 1080×1920",
        "title": "Сторис «Дедлайн цены»", "w": W2, "h": H2,
        "note": f"Крутить с 25 по 30 августа, дальше макет устареет сам.",
        "svg": f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W2} {H2}">
<rect width="{W2}" height="{H2}" fill="{PAPER}"/>{_bubbles(W2, H2, 2)}
{_logo(W2 / 2, 90, 260)}
{_t(W2 / 2, 520, 72, "ТОЛЬКО ДО", INDIGO, "bold", "middle", "4")}
<g><rect x="{W2 / 2 - 330}" y="580" width="660" height="200" rx="100" fill="{AMBER}"/>
{_t(W2 / 2, 712, 110, DEADLINE.upper(), PAPER, "bold", "middle", "2")}</g>
{_t(W2 / 2, 900, 56, "сентябрь по ценам", INDIGO, "normal", "middle")}
{_t(W2 / 2, 970, 56, "прошлого года", INDIGO, "normal", "middle")}
<g><rect x="{W2 / 2 - 400}" y="1080" width="800" height="150" rx="75" fill="{SOFT}"/>
{_t(W2 / 2, 1140, 44, "−15% на первый абонемент", INDIGO, "bold", "middle")}
{_t(W2 / 2, 1195, 36, "при покупке в день первого занятия", BLUE, "normal", "middle")}</g>
{_t(W2 / 2, 1400, 48, f"занятия стартуют {START}", GREEN, "bold", "middle")}
{_badge(W2 / 2 - 300, 1500, 600, "напишите нам — подберём группу", BLUE, PAPER, 34)}
{_t(W2 / 2, 1760, 40, f"{SITE}", INDIGO, "bold", "middle")}
</svg>'''})

    # -------------------------------------------------------- 5. плакат A4 ---
    W3, H3 = 1240, 1754  # A4 150 dpi
    plakat_courses = "".join(
        _t(W3 / 2, 800 + i * 70, 44, c, INDIGO, "normal", "middle")
        for i, c in enumerate(courses[:7]))
    makets.append({"key": "poster_a4", "group": "Печать",
        "title": "Плакат A4 — подъезд и центр", "w": W3, "h": H3,
        "note": "Скачать PNG → печать. Читается с двух метров: главное — крупно.",
        "svg": f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W3} {H3}">
<rect width="{W3}" height="{H3}" fill="{PAPER}"/>
<rect width="{W3}" height="14" fill="{BLUE}"/><rect y="{H3 - 14}" width="{W3}" height="14" fill="{BLUE}"/>
{_bubbles(W3, H3, 3)}
{_logo(W3 / 2, 40, 270)}
{_t(W3 / 2, 410, 76, "ДЕТСКИЙ ЦЕНТР", INDIGO, "bold", "middle", "3")}
{_t(W3 / 2, 498, 76, "В ВАШЕМ ДВОРЕ", INDIGO, "bold", "middle", "3")}
{_badge(W3 / 2 - 330, 548, 660, f"набор на учебный год · с {START}", AMBER, PAPER, 34)}
{_t(W3 / 2, 726, 40, "от 1,3 года до 12 лет", BLUE, "bold", "middle")}
{plakat_courses}
<g><rect x="{W3 / 2 - 420}" y="1310" width="840" height="120" rx="60" fill="{GREEN}"/>
{_t(W3 / 2, 1360, 40, "Первое занятие — условно-бесплатное:", PAPER, "bold", "middle")}
{_t(W3 / 2, 1408, 34, "не понравится — платить не нужно", PAPER, "normal", "middle")}</g>
{_t(W3 / 2, 1520, 38, ADDRESS, INDIGO, "normal", "middle")}
{_t(W3 / 2, 1575, 38, METRO, INDIGO, "normal", "middle")}
{_t(W3 / 2, 1660, 52, SITE, BLUE, "bold", "middle", "2")}
</svg>'''})

    # ------------------------------------------------------ 6. листовка A5 ---
    W4, H4 = 874, 1240  # A5 150 dpi
    makets.append({"key": "flyer_a5", "group": "Печать",
        "title": "Листовка A5 — лицевая сторона", "w": W4, "h": H4,
        "note": "Оборот с расписанием и QR печатаем из /content («Листовка промоутера»).",
        "svg": f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W4} {H4}">
<rect width="{W4}" height="{H4}" fill="{INDIGO}"/>
<circle cx="-40" cy="-40" r="220" fill="{BLUE}" opacity=".4"/>
<circle cx="900" cy="240" r="200" fill="{AMBER}" opacity=".55"/>
<circle cx="880" cy="1180" r="240" fill="{GREEN}" opacity=".3"/>
<circle cx="-30" cy="1050" r="190" fill="{BLUE}" opacity=".3"/>
{_logo(W4 / 2, 40, 230, white=True)}
{_t(W4 / 2, 330, 34, "детский центр и английский сад", PAPER, "normal", "middle")}
{_t(W4 / 2, 500, 60, "Научим читать", PAPER, "bold", "middle")}
{_t(W4 / 2, 574, 60, "за 3 месяца!", AMBER, "bold", "middle")}
{_t(W4 / 2, 700, 60, "Заговорит по-английски", PAPER, "bold", "middle")}
{_t(W4 / 2, 774, 60, "за год!", GREEN, "bold", "middle")}
<g><rect x="{W4 / 2 - 350}" y="850" width="700" height="130" rx="65" fill="{PAPER}"/>
{_t(W4 / 2, 905, 36, "Первое занятие условно-бесплатное:", INDIGO, "bold", "middle")}
{_t(W4 / 2, 950, 30, "не понравится — платить не нужно", BLUE, "normal", "middle")}</g>
{_t(W4 / 2, 1070, 32, METRO, PAPER, "normal", "middle")}
{_t(W4 / 2, 1125, 32, ADDRESS.split("(")[0].strip(), PAPER, "normal", "middle")}
{_t(W4 / 2, 1195, 42, SITE, AMBER, "bold", "middle", "2")}
</svg>'''})

    # ------------------------------------------------- 7. паспорт миссий ---
    W5, H5 = 1240, 1754
    MISSIONS = [
        "Пришёл на первое занятие",
        "Выполнил задание педагога до конца",
        "Рассказал дома, что нового узнал",
        "Сам собрал всё к занятию",
        "Позанимался дома 10 минут",
        "Помог другу на занятии",
        "Неделя без пропусков",
        "Придумал вопрос и задал его педагогу",
    ]
    mrows = []
    for i, m in enumerate(MISSIONS):
        y = 560 + i * 120
        mrows.append(f'<rect x="100" y="{y - 70}" width="1040" height="100" rx="50" '
                     f'fill="{SOFT if i % 2 == 0 else PAPER}" stroke="{BLUE}" stroke-width="2"/>')
        mrows.append(f'<circle cx="170" cy="{y - 20}" r="34" fill="{PAPER}" stroke="{AMBER}" stroke-width="5"/>')
        mrows.append(_t(170, y - 6, 40, i + 1, AMBER, "bold", "middle"))
        mrows.append(_t(240, y - 6, 38, m, INDIGO, "bold"))
        mrows.append(f'<circle cx="1060" cy="{y - 20}" r="28" fill="none" stroke="{GREEN}" '
                     f'stroke-width="4" stroke-dasharray="6 7"/>')
    makets.append({"key": "passport_a4", "group": "Печать",
        "title": "Паспорт миссий «Старт в учёбе» — A4", "w": W5, "h": H5,
        "note": "Печатать каждому новичку. Педагог ставит печать/наклейку в пунктирный "
                "круг за миссию. Собрал все восемь — приз на ресепшене.",
        "svg": f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W5} {H5}">
<rect width="{W5}" height="{H5}" fill="{PAPER}"/>
<rect width="{W5}" height="12" fill="{AMBER}"/><rect y="{H5 - 12}" width="{W5}" height="12" fill="{AMBER}"/>
{_bubbles(W5, H5, 4)}
{_logo(W5 / 2, 36, 190)}
{_t(W5 / 2, 300, 64, "ПАСПОРТ МИССИЙ", INDIGO, "bold", "middle", "2")}
{_t(W5 / 2, 360, 42, "«Старт в учёбе»", BLUE, "bold", "middle")}
{_t(150, 448, 36, "Агент:", INDIGO, "bold")}
<line x1="290" y1="452" x2="900" y2="452" stroke="{INDIGO}" stroke-width="2" stroke-dasharray="3 6"/>
{_t(940, 448, 36, "Возраст:", INDIGO, "bold")}
<line x1="1100" y1="452" x2="1160" y2="452" stroke="{INDIGO}" stroke-width="2" stroke-dasharray="3 6"/>
{"".join(mrows)}
<g><rect x="{W5 / 2 - 520}" y="1560" width="1040" height="110" rx="55" fill="{GREEN}"/>
{_t(W5 / 2, 1606, 32, "Все 8 печатей собраны? Неси паспорт на ресепшен —", PAPER, "bold", "middle")}
{_t(W5 / 2, 1648, 32, "тебя ждёт приз!", PAPER, "bold", "middle")}</g>
</svg>'''})

    # ---------------------------------------------- 8. трекер прогресса ---
    SKILL_ROWS = 6
    trows = []
    for i in range(SKILL_ROWS):
        y = 640 + i * 130
        trows.append(f'<rect x="90" y="{y}" width="1060" height="110" rx="20" '
                     f'fill="{SOFT if i % 2 == 0 else PAPER}" stroke="{BLUE}" stroke-width="1.5"/>')
        trows.append(f'<line x1="560" y1="{y}" x2="560" y2="{y + 110}" stroke="{BLUE}" stroke-width="1.5"/>')
        trows.append(f'<line x1="800" y1="{y}" x2="800" y2="{y + 110}" stroke="{BLUE}" stroke-width="1.5"/>')
        for j in range(5):
            trows.append(f'<circle cx="{610 + j * 40}" cy="{y + 55}" r="14" fill="none" '
                         f'stroke="{AMBER}" stroke-width="3"/>')
    head_y = 585
    makets.append({"key": "tracker_a4", "group": "Печать",
        "title": "Трекер прогресса — A4", "w": W5, "h": H5,
        "note": "Педагог заполняет на первом занятии и отдаёт родителю: навыки, уровень "
                "сейчас (закрасить кружки), что развиваем. Превращает пробное в "
                "консультацию с результатом на руках.",
        "svg": f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W5} {H5}">
<rect width="{W5}" height="{H5}" fill="{PAPER}"/>
<rect width="{W5}" height="12" fill="{BLUE}"/><rect y="{H5 - 12}" width="{W5}" height="12" fill="{BLUE}"/>
{_bubbles(W5, H5, 5)}
{_logo(W5 / 2, 36, 190)}
{_t(W5 / 2, 296, 60, "ТРЕКЕР ПРОГРЕССА", INDIGO, "bold", "middle", "2")}
{_t(150, 380, 34, "Ребёнок:", INDIGO, "bold")}
<line x1="310" y1="384" x2="760" y2="384" stroke="{INDIGO}" stroke-width="2" stroke-dasharray="3 6"/>
{_t(800, 380, 34, "Возраст:", INDIGO, "bold")}
<line x1="950" y1="384" x2="1090" y2="384" stroke="{INDIGO}" stroke-width="2" stroke-dasharray="3 6"/>
{_t(150, 450, 34, "Курс:", INDIGO, "bold")}
<line x1="260" y1="454" x2="760" y2="454" stroke="{INDIGO}" stroke-width="2" stroke-dasharray="3 6"/>
{_t(800, 450, 34, "Дата:", INDIGO, "bold")}
<line x1="900" y1="454" x2="1090" y2="454" stroke="{INDIGO}" stroke-width="2" stroke-dasharray="3 6"/>
{_t(110, head_y, 30, "НАВЫК", BLUE, "bold")}
{_t(610, head_y, 30, "УРОВЕНЬ СЕЙЧАС", BLUE, "bold")}
{_t(820, head_y, 30, "ЧТО РАЗВИВАЕМ", BLUE, "bold")}
{"".join(trows)}
{_t(110, 1490, 34, "Рекомендация педагога:", INDIGO, "bold")}
<line x1="110" y1="1540" x2="1130" y2="1540" stroke="{INDIGO}" stroke-width="1.5" stroke-dasharray="3 6"/>
<line x1="110" y1="1600" x2="1130" y2="1600" stroke="{INDIGO}" stroke-width="1.5" stroke-dasharray="3 6"/>
{_t(110, 1680, 30, "Педагог:", INDIGO, "bold")}
<line x1="250" y1="1684" x2="560" y2="1684" stroke="{INDIGO}" stroke-width="1.5" stroke-dasharray="3 6"/>
{_t(W5 - 110, 1680, 30, SITE, BLUE, "bold", "end")}
</svg>'''})

    return makets


def maket_groups(makets: list[dict]) -> list[str]:
    seen: list[str] = []
    for m in makets:
        if m["group"] not in seen:
            seen.append(m["group"])
    return seen
