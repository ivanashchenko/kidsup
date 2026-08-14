"""KidsUp Analytics — выгрузка и аналитика данных из CRM МойКласс."""

import csv
import io
import json
import logging
import re
import secrets
from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import analytics, config, db, leads, sync

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")

db.init_db()

app = FastAPI(title="KidsUp Analytics")

from . import autopilot  # noqa: E402  (нужен db.init_db выше)
autopilot.start()

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

_security = HTTPBasic(auto_error=False)


def check_auth(credentials: HTTPBasicCredentials | None = Depends(_security)):
    """HTTP Basic — только если задан APP_PASSWORD."""
    if not config.APP_PASSWORD:
        return
    ok = (
        credentials is not None
        and secrets.compare_digest(credentials.username, config.APP_USER)
        and secrets.compare_digest(credentials.password, config.APP_PASSWORD)
    )
    if not ok:
        raise HTTPException(status_code=401, detail="Требуется авторизация",
                            headers={"WWW-Authenticate": "Basic"})


AUTH = [Depends(check_auth)]


def render(request: Request, template: str, **ctx) -> HTMLResponse:
    counts = db.table_counts()
    ctx.update({
        "request": request,
        "has_data": counts["users"] > 0 or counts["payments"] > 0,
        "has_api_key": bool(sync.get_api_key()),
        "last_sync": db.get_state("last_sync"),
    })
    return templates.TemplateResponse(request, template, ctx)


# --- страницы -------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, dependencies=AUTH)
def dashboard(request: Request):
    return render(
        request, "dashboard.html",
        active="dashboard",
        kpis=analytics.kpis(),
        revenue=analytics.revenue_by_month(),
        new_students=analytics.new_students_by_month(),
        attendance=analytics.attendance_by_month(),
        payments_by_type=analytics.payments_by_type(),
        debtors=analytics.top_debtors(),
        groups=analytics.group_stats()[:15],
    )


@app.get("/students", response_class=HTMLResponse, dependencies=AUTH)
def students_page(request: Request, q: str = "", debtors: int = 0):
    sql = "SELECT id, name, phone, email, balance, created_at FROM users"
    conds, args = [], []
    if q:
        conds.append("(name LIKE ? OR phone LIKE ? OR email LIKE ?)")
        args += [f"%{q}%"] * 3
    if debtors:
        conds.append("balance < 0")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY created_at DESC LIMIT 500"
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        total = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    return render(request, "students.html", active="students",
                  students=rows, q=q, debtors=debtors, total=total)


@app.get("/payments", response_class=HTMLResponse, dependencies=AUTH)
def payments_page(request: Request, date_from: str = "", date_to: str = "",
                  optype: str = ""):
    if not date_from:
        date_from = (date.today() - timedelta(days=30)).isoformat()
    if not date_to:
        date_to = date.today().isoformat()
    sql = """SELECT p.id, p.date, p.summa, p.optype, p.comment, u.name user_name
             FROM payments p LEFT JOIN users u ON u.id = p.user_id
             WHERE substr(p.date,1,10) >= ? AND substr(p.date,1,10) <= ?"""
    args = [date_from, date_to]
    if optype:
        sql += " AND p.optype = ?"
        args.append(optype)
    sql += " ORDER BY p.date DESC LIMIT 1000"
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        totals = conn.execute(
            """SELECT optype, SUM(summa) s, COUNT(*) c FROM payments
               WHERE substr(date,1,10) >= ? AND substr(date,1,10) <= ?
               GROUP BY optype""", (date_from, date_to)).fetchall()
    return render(request, "payments.html", active="payments",
                  payments=rows, date_from=date_from, date_to=date_to,
                  optype=optype, totals=[dict(t) for t in totals])


@app.get("/groups", response_class=HTMLResponse, dependencies=AUTH)
def groups_page(request: Request):
    return render(request, "groups.html", active="groups",
                  groups=analytics.group_stats(),
                  top_students=analytics.top_students_by_visits())


DAY_ORDER = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
DAY_LABEL = dict(zip(DAY_ORDER, ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]))
_DAY_RE = re.compile(r"(?<![а-яё])(пн|вт|ср|чт|пт|сб|вс)(?![а-яё])")
_TIME_RE = re.compile(r"\d{1,2}:\d{2}")


def _name_days(name: str) -> list[str]:
    """Дни недели из названия группы: «вт-чт», «пн 19:00 + сб 12:00» → [вт, сб]."""
    days = []
    for m in _DAY_RE.finditer((name or "").lower()):
        if m.group(1) not in days:
            days.append(m.group(1))
    return sorted(days, key=DAY_ORDER.index)



# --- цены (сверено с kidsup.ru/price 14.08.2026; новые — с 01.09) ---------
# Слева цена прошлого учебного года со страницы /price, справа новая.
PRICES = {
    "Английский детский сад": {
        "title": "Мини-сад ГКП 9:00–13:00 · Нулевой класс 10:00–14:00",
        "lines": [("Мини-сад, 20 посещений (5 дней)", 34500, 35900),
                  ("Мини-сад, 16 посещений (4 дня)", 32000, 33300),
                  ("Мини-сад, 12 посещений (3 дня)", 25800, 26800),
                  ("Мини-сад, 8 посещений (2 дня)", 19000, 19800),
                  ("Мини-сад, разовое посещение", 2700, 2700),
                  ("Нулевой класс, 20 посещений (5 дней)", 34500, 35900),
                  ("Нулевой класс, 12 посещений (3 дня)", 25800, 26800)]},
    "Английский язык": {"title": "Лингвитания · занятие 50 мин",
        "lines": [("8 занятий (2 р/нед)", 8200, 8550),
                  ("4 занятия (1 р/нед)", 4800, 5000)]},
    "Подготовка к школе": {"title": "Ступени 1 и 2 · занятие 50 мин",
        "lines": [("8 занятий (2 р/нед)", 8200, 8550),
                  ("4 занятия (1 р/нед)", 4800, 5000),
                  ("Разовое занятие", 1600, 1700)]},
    "Дошкольный университет": {"title": "Занятие 50 мин",
        "lines": [("8 занятий (2 р/нед)", 8200, 8550),
                  ("4 занятия (1 р/нед)", 4800, 5000),
                  ("Разовое занятие", 1600, 1700)]},
    "Раннее развитие с Еленой. Музыка и речь": {"title": "Занятие 50 мин",
        "lines": [("8 занятий (2 р/нед)", 8200, 8550),
                  ("4 занятия (1 р/нед)", 5000, 5200),
                  ("Разовое занятие", 1600, 1700)]},
    "Раннее развитие с Ириной. Первая школа": {"title": "Занятие 45 мин",
        "lines": [("8 занятий (2 р/нед)", 7600, 7900),
                  ("4 занятия (1 р/нед)", 4800, 5000),
                  ("Разовое занятие", 1600, 1700)]},
    "Лицей для малышей": {"title": "Занятие 45 мин",
        "lines": [("8 занятий (2 р/нед)", 7600, 7900),
                  ("4 занятия (1 р/нед)", 4800, 5000),
                  ("Разовое занятие", 1600, 1700)]},
    "Ментальная арифметика": {"title": "Занятие 90 мин",
        "lines": [("4 занятия", 8000, 8300)]},
    "ИЗО-студия": {"title": "Занятие 50 мин · пробное 850 ₽",
        "lines": [("8 занятий (2 р/нед)", 6800, 7050),
                  ("4 занятия (1 р/нед)", 4700, 4900),
                  ("Разовое занятие", 1600, 1700),
                  ("Пробное занятие", 850, 850)]},
    "Шахматы": {"title": "Занятие 50 мин · пробное 850 ₽",
        "lines": [("8 занятий (2 р/нед)", 6800, 7050),
                  ("4 занятия (1 р/нед)", 4700, 4900),
                  ("Разовое занятие", 1600, 1700),
                  ("Пробное занятие", 850, 850)]},
    "Скорочтение (техника чтения)": {"title": "Занятие 50 мин · с 7 лет",
        "lines": [("8 занятий", 8200, 8550)]},
    "Каллиграфия + грамота": {"title": "Занятие 50 мин · с 7 лет",
        "lines": [("8 занятий", 8200, 8550)]},
    "Робототехника": {"title": "Занятие 55 мин · партнёрский курс, без индексации",
        "lines": [("4 занятия", 6400, 6400),
                  ("Разовое занятие", 2000, 2000),
                  ("Пробное занятие", 1100, 1100)]},
    "Логопед": {"title": "Индивидуально. На /price указаны только консультации",
        "lines": [("Консультация с Мариной, 40 мин", 2700, 2700),
                  ("Консультация с Еленой, 40 мин", 2200, 2200)]},
    "Английский летний клуб": {"title": "Лагерь до 28.08 (в скобках — цена без скидки)",
        "lines": [("1 неделя, полдня 8:00–15:00", 20400, 16400),
                  ("1 неделя, полный день 8:00–19:00", 24200, 20700),
                  ("2 недели, полдня", 33800, 29300),
                  ("2 недели, полный день", 42600, 36700),
                  ("4 недели, полдня", 50700, 44100),
                  ("4 недели, полный день", 69600, 63100)]},
}

# что говорить админу по каждому предмету: ценность → выгода → закрытие
PITCH = {
    "Английский детский сад": {
        "what": "Мини-сад ГКП 9:00–13:00: не присмотр, а обучение — минимум 4 занятия каждый день, английский ежедневно, ИЗО и лепка, песочная терапия, «говорилка» для запуска речи.",
        "why": "Ребёнок за полдня получает и общение, и полноценную программу, а мама — свободное утро. Видеотрансляция: видно, чем занят ребёнок.",
        "close": "«Приходите на бесплатный пробный день — посмотрите группу и педагога. На какой день записать?»",
        "combo": "Логопед и ИЗО — идут в те же дни после мини-сада.",
        "combo": "Подготовка к школе (4–7) или скорочтение и каллиграфия (7–12).",
        "combo": "Английский язык вторым предметом; с 5 лет — шахматы или менталка.",
        "combo": "Английский язык, шахматы.",
        "combo": "Логопед, если есть вопросы к речи; дальше — мини-сад.",
        "combo": "Логопед; со следующего года — Лицей для малышей.",
        "combo": "Логопед и ИЗО; с 4 лет — подготовка к школе.",
        "combo": "Идёт вторым к любому основному предмету.",
        "combo": "Отличный второй предмет к ПШ, английскому и мини-саду.",
        "combo": "Второй предмет к подготовке к школе и английскому.",
        "combo": "К английскому и подготовке к школе; школьникам — со скорочтением.",
        "combo": "Второй предмет к любому основному, 4–12 лет.",
        "combo": "Каллиграфия + грамота — берут парой.",
        "combo": "Скорочтение — берут парой."},
    "Английский язык": {
        "what": "«Лингвитания» — авторская программа: язык через игру, песни и сценки, говорить начинают с первого занятия, а не переводить слова.",
        "why": "Группа 6–8 детей, каждый успевает говорить. Перед стартом бесплатное тестирование уровня — ребёнок попадает в свою группу.",
        "close": "«Давайте запишу на бесплатное пробное — педагог посмотрит уровень и подберёт группу. Вам удобнее будни вечером или суббота?»"},
    "Подготовка к школе": {
        "what": "Методика Буракова: чтение, математика и письмо в одном занятии, короткие блоки со сменой деятельности и переменками — дети не устают.",
        "why": "К школе ребёнок читает, считает и умеет держать внимание 40 минут. Есть группы для нечитающих и для тех, кто уже начал.",
        "close": "«Есть группа как раз по возрасту, осталось N мест. Записываю на бесплатное пробное на этой неделе?»"},
    "Раннее развитие с Еленой. Музыка и речь": {
        "what": "«Музыка вместе с мамой» для 1–3 лет: музыка, ритм, пальчиковые игры, запуск речи. Мама на занятии рядом.",
        "why": "Мягкая адаптация к группе и первый социальный опыт; педагог — логопед, сразу видит, что с речью.",
        "close": "«Ближайшая группа по возрасту — [день/время]. Приходите на бесплатное пробное, малыш попробует, а вы посмотрите формат»."},
    "Раннее развитие с Ириной. Первая школа": {
        "what": "Раннее развитие 1–3 лет: сенсорика, мелкая моторика, первые понятия, логика — по возрастным ступеням.",
        "why": "Ребёнок привыкает заниматься в группе до сада, легче потом входит в мини-сад и подготовку.",
        "close": "«Подберу группу точно по возрасту — сколько сейчас месяцев? Запишу на бесплатное пробное»."},
    "Лицей для малышей": {
        "what": "Следующая ступень после раннего развития: занятие 45 минут, больше самостоятельности, подготовка к формату «как в школе».",
        "why": "Плавный мостик к подготовке к школе с 4 лет — ребёнок не теряет год.",
        "close": "«По возрасту вам как раз лицей. Записываю на пробное — посмотрите педагога?»"},
    "Логопед": {
        "what": "Индивидуально: диагностика речи, постановка звуков, запуск речи у неговорящих, подготовка к школьной грамоте.",
        "why": "Чем раньше начать, тем короче курс — в 3–4 года звуки ставятся в разы быстрее, чем в 6.",
        "close": "«Первый шаг — консультация 40 минут: педагог скажет, есть ли проблема и сколько нужно занятий. Записать на эту неделю?»"},
    "ИЗО-студия": {
        "what": "Четыре разных группы: живопись, лепка, картины великих художников, алфавитная живопись — не «рисование вообще», а программа.",
        "why": "Ребёнок уносит готовую работу с каждого занятия — виден результат, растёт уверенность и усидчивость.",
        "close": "«Пробное всего 850 ₽ — придёте, ребёнок нарисует свою первую картину. На какой день записать?»"},
    "Шахматы": {
        "what": "Отдельные группы для начинающих и продолжающих, с 4 лет. Тренируют логику, счёт и умение думать на шаг вперёд.",
        "why": "Лучшая тренировка внимания и усидчивости перед школой; сильные ребята ездят на турниры.",
        "close": "«Пробное 850 ₽ — педагог посмотрит уровень и определит в группу. Записываю?»"},
    "Ментальная арифметика": {
        "what": "CleverStart: счёт на соробане и в уме, две возрастные группы — 4–7 и 7–12 лет, занятие 90 минут.",
        "why": "Растёт скорость мышления и концентрация — заметно по школьной математике уже через пару месяцев.",
        "close": "«Приходите на бесплатное пробное — увидите, как ребёнок считает без калькулятора. Суббота или будни?»"},
    "Робототехника": {
        "what": "Ребёнок сам собирает и программирует робота, работает по инструкции и в команде, лучшие едут на соревнования.",
        "why": "Экранное время превращается в созидание; отличный второй предмет для 4–12 лет.",
        "close": "«Пробное 1 100 ₽ — соберёт первого робота и всё поймёт сам. Записать на ближайшее?»"},
    "Скорочтение (техника чтения)": {
        "what": "Для школьников 7–12: техника чтения, понимание текста, память и внимание.",
        "why": "Домашка занимает меньше времени — ребёнок читает быстрее и сразу понимает прочитанное.",
        "close": "«Бесплатное пробное — замерим текущую скорость чтения, будет с чем сравнить. Записываю?»"},
    "Каллиграфия + грамота": {
        "what": "Для школьников 7–12: постановка почерка, письмо без ошибок, грамотность.",
        "why": "Снимает главную боль началки — «пишет как курица лапой» и ошибки по невнимательности.",
        "close": "«Приходите на бесплатное пробное, педагог посмотрит почерк и скажет, что поправить. На какой день?»"},
}

# особенности групп — по ключевым словам в названии
FEATURES = [("продолжающие", "продолжающие"), ("начинающие", "начинающие"),
            ("нечит", "нечитающие"), ("школьник", "школьники"),
            ("лепка", "лепка"), ("живопись", "живопись"),
            ("картины великих", "картины великих художников")]
_AGE_RE = re.compile(r"(\d{1,2}(?:[.,]\d)?)\s*[-–]\s*(\d{1,2}(?:[.,]\d)?)")


def _group_ages(name: str) -> tuple[float | None, float | None]:
    """Возраст из названия: «5-6 лет», «1,3 - 1,8», «4-7» → (мин, макс).
    Номера групп и время вырезаем, чтобы не принять их за возраст."""
    clean = re.sub(r"Группа\s*\d+", " ", name or "", flags=re.I)
    clean = _TIME_RE.sub(" ", clean).replace("_", " ")
    m = _AGE_RE.search(clean)
    if not m:
        # одиночный возраст в хвосте: «…_16:00_4» → 4 года
        one = re.search(r"(?:^|[\s_])(\d{1,2})(?:\s*лет|\s*года?)?\s*$", clean.strip())
        if one and 1 <= int(one.group(1)) <= 12:
            v = float(one.group(1))
            return v, v
        return None, None
    try:
        lo, hi = (float(x.replace(",", ".")) for x in m.groups())
    except ValueError:
        return None, None
    return (lo, hi) if 0 < lo <= hi <= 16 else (None, None)


def _group_features(name: str) -> list[str]:
    low = (name or "").lower()
    return [label for key, label in FEATURES if key in low]


def _time_slot(times: list[str]) -> str:
    """Утро / день / вечер по первому времени в названии."""
    if not times:
        return ""
    try:
        h = int(times[0].split(":")[0])
    except ValueError:
        return ""
    return "morning" if h < 13 else "day" if h < 17 else "evening"


SLOT_LABEL = {"morning": "утро (до 13:00)", "day": "день (13–17)", "evening": "вечер (17:00+)"}


@app.get("/enrollment", response_class=HTMLResponse, dependencies=AUTH)
def enrollment_page(request: Request, course: str = "", day: str = "", free: int = 0,
                    age: str = "", slot: str = "", feature: str = "", q: str = ""):
    """Набор 2026/27: все группы «2627_…» с заполненностью — рабочий экран админа.

    Фильтры: направление, день, возраст ребёнка, время дня, особенность группы,
    поиск по названию, только со свободными местами."""
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT cl.id, cl.name, cl.max_students, co.name course,
                   COUNT(DISTINCT j.user_id) enrolled,
                   COUNT(DISTINCT CASE WHEN j.created_at >= datetime('now', '-7 day')
                                       THEN j.user_id END) fresh
            FROM classes cl
            LEFT JOIN courses co ON co.id = cl.course_id
            LEFT JOIN joins j ON j.class_id = cl.id
            WHERE cl.name LIKE '2627%'
            GROUP BY cl.id ORDER BY co.name, cl.name""").fetchall()
    groups = []
    for r in rows:
        name = r["name"] or ""
        parts = name.split("_")
        g_days = _name_days(name)
        times = _TIME_RE.findall(name)
        buffer = "аявк" in name  # группы «Заявки» — буфер без лимита мест
        cap = r["max_students"] or 8
        enrolled = r["enrolled"] or 0
        fill = 0 if buffer else (min(100, round(enrolled * 100 / cap)) if cap else 0)
        age_lo, age_hi = _group_ages(name)
        groups.append({
            "id": r["id"],
            "name": name, "course": r["course"] or (parts[1] if len(parts) > 1 else "?"),
            "days": g_days, "buffer": buffer,
            "day": " · ".join(DAY_LABEL[d] for d in g_days) or "—",
            "time": " · ".join(times[:2]) or "—",
            "slot": _time_slot(times), "features": _group_features(name),
            "age_lo": age_lo, "age_hi": age_hi,
            "age": (f"{age_lo:g}–{age_hi:g} лет" if age_lo else "—"),
            "enrolled": enrolled, "capacity": cap,
            "free": max(0, cap - enrolled), "fresh": r["fresh"] or 0, "fill_pct": fill,
            "color": "#E5232A" if fill >= 100 else "#F5A81C" if fill >= 75 else "#5FB53B",
        })
    courses_list = sorted({g["course"] for g in groups})
    if course:
        groups = [g for g in groups if g["course"] == course]
    # дни — только те, что реально есть в группах выбранного направления
    day = day.strip().lower()
    days_list = [{"value": d, "label": DAY_LABEL[d],
                  "n": sum(1 for g in groups if d in g["days"])}
                 for d in DAY_ORDER if any(d in g["days"] for g in groups)]
    if day and day not in {d["value"] for d in days_list}:
        day = ""  # при смене направления невозможный день сбрасываем, а не показываем пусто
    if day:
        groups = [g for g in groups if day in g["days"]]
    # возраст ребёнка: показываем группы, в диапазон которых он попадает
    age = (age or "").strip().replace(",", ".")
    age_val = None
    try:
        age_val = float(age) if age else None
    except ValueError:
        age_val = None
    if age_val is not None:
        groups = [g for g in groups
                  if g["age_lo"] is None or (g["age_lo"] - 0.5 <= age_val <= g["age_hi"] + 0.5)]
    slots_list = [{"value": k, "label": SLOT_LABEL[k],
                   "n": sum(1 for g in groups if g["slot"] == k)}
                  for k in ("morning", "day", "evening")
                  if any(g["slot"] == k for g in groups)]
    if slot:
        groups = [g for g in groups if g["slot"] == slot]
    features_list = sorted({f for g in groups for f in g["features"]})
    if feature:
        groups = [g for g in groups if feature in g["features"]]
    if q:
        ql = q.lower()
        groups = [g for g in groups if ql in g["name"].lower() or ql in g["course"].lower()]
    if free:
        groups = [g for g in groups if g["free"] > 0]
    groups.sort(key=lambda g: (g["course"],
                               DAY_ORDER.index(g["days"][0]) if g["days"] else 9,
                               g["time"]))
    summary = {}
    for g in groups:
        s = summary.setdefault(g["course"], {"course": g["course"], "groups": 0,
                                             "enrolled": 0, "capacity": 0, "free": 0})
        s["groups"] += 1; s["enrolled"] += g["enrolled"]
        if not g["buffer"]:
            s["capacity"] += g["capacity"]; s["free"] += g["free"]
    for s in summary.values():
        s["fill_pct"] = round(s["enrolled"] * 100 / s["capacity"]) if s["capacity"] else 0
        s["price"] = PRICES.get(s["course"])
    # цены: показываем блок целиком либо только по выбранному направлению
    price_blocks = [{"course": c, **PRICES[c]} for c in PRICES
                    if not course or c == course] or \
                   [{"course": c, **PRICES[c]} for c in PRICES]
    # ссылка «Записать» — шаблон настраивается: у МойКласса адрес группы
    # может отличаться от аккаунта к аккаунту (настройка moyklass_group_url)
    tpl = db.get_setting("moyklass_group_url",
                         "https://app.moyklass.com/#/company/classes/{id}")
    for g in groups:
        g["crm_url"] = tpl.replace("{id}", str(g["id"]))
        pr = PRICES.get(g["course"])
        g["price_new"] = pr["lines"][0][2] if pr else None
        # подсказка админу, чем давить: добор или последние места
        if g["buffer"]:
            g["urgency"] = ""
        elif g["free"] == 0:
            g["urgency"] = "мест нет — в лист ожидания"
        elif g["enrolled"] <= 3:
            g["urgency"] = "идёт добор — группа формируется"
        elif g["free"] <= 2:
            g["urgency"] = f"последние места: {g['free']}"
        else:
            g["urgency"] = ""
    pitches = [{"course": c, **PITCH[c]} for c in PITCH if not course or c == course]
    return render(request, "enrollment.html", active="enrollment",
                  groups=groups, courses=courses_list, course=course,
                  days=days_list, day=day, free=free,
                  age=age, slots=slots_list, slot=slot,
                  features=features_list, feature=feature, q=q,
                  prices=price_blocks, pitches=pitches,
                  summary=sorted(summary.values(), key=lambda x: -x["free"]))


@app.get("/leads", response_class=HTMLResponse, dependencies=AUTH)
def leads_page(request: Request):
    return render(request, "leads.html", active="leads",
                  stats=leads.stats_by_source(), rows=leads.recent(),
                  sources=leads.SOURCES)


@app.post("/leads/push/{lead_id}", dependencies=AUTH)
def leads_push(lead_id: int):
    ok, msg = leads.push_to_crm(lead_id)
    return {"ok": ok, "message": msg}


@app.get("/export/leads.csv", dependencies=AUTH)
def export_leads():
    rows = leads.recent(limit=10000)
    return _csv_response(
        "leads.csv",
        ["ID", "Дата", "Источник", "Промокод", "Родитель", "Телефон",
         "Ребёнок", "Возраст", "Интересы", "Комментарий", "В CRM"],
        [(r["id"], r["created_at"], leads.SOURCES.get(r["source"], r["source"]),
          r["promo"], r["parent_name"], r["phone"], r["child_name"], r["child_age"],
          r["interests"], r["comment"], "да" if r["pushed_to_crm"] else "нет")
         for r in rows])


# --- API: синхронизация ----------------------------------------------------

@app.post("/api/sync/start", dependencies=AUTH)
def sync_start():
    if not sync.get_api_key():
        return JSONResponse({"ok": False, "error": "API-ключ не задан"}, status_code=400)
    started = sync.start_sync()
    return {"ok": True, "started": started}


@app.get("/api/sync/status", dependencies=AUTH)
def sync_status():
    return sync.get_status()


# --- экспорт ---------------------------------------------------------------

def _csv_response(filename: str, header: list[str], rows: list[tuple]) -> StreamingResponse:
    """CSV, который корректно открывается в русском Excel (utf-8-sig, ';')."""
    def cell(v):
        # дробные числа — с запятой, иначе русский Excel посчитает их текстом
        if isinstance(v, float):
            return ("%.2f" % v).rstrip("0").rstrip(".").replace(".", ",")
        return v

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(header)
    writer.writerows([tuple(cell(v) for v in row) for row in rows])
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return StreamingResponse(
        iter([data]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/export/students.csv", dependencies=AUTH)
def export_students():
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT id, name, phone, email, balance, client_state_id,
                      created_at, updated_at FROM users ORDER BY id""").fetchall()
    return _csv_response(
        "students.csv",
        ["ID", "Имя", "Телефон", "Email", "Баланс", "Статус (ID)",
         "Создан", "Обновлён"],
        [tuple(r) for r in rows])


@app.get("/export/payments.csv", dependencies=AUTH)
def export_payments(date_from: str = "", date_to: str = ""):
    sql = """SELECT p.id, p.date, u.name, p.summa, p.optype, p.comment
             FROM payments p LEFT JOIN users u ON u.id = p.user_id"""
    args = []
    if date_from and date_to:
        sql += " WHERE substr(p.date,1,10) >= ? AND substr(p.date,1,10) <= ?"
        args = [date_from, date_to]
    sql += " ORDER BY p.date"
    with db.get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return _csv_response(
        "payments.csv",
        ["ID", "Дата", "Ученик", "Сумма", "Тип операции", "Комментарий"],
        [tuple(r) for r in rows])


@app.get("/export/lessons.csv", dependencies=AUTH)
def export_lessons():
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT l.id, l.date, l.begin_time, l.end_time, c.name,
                      l.topic, l.status,
                      (SELECT COUNT(*) FROM lesson_records lr WHERE lr.lesson_id=l.id),
                      (SELECT SUM(visit) FROM lesson_records lr WHERE lr.lesson_id=l.id)
               FROM lessons l LEFT JOIN classes c ON c.id = l.class_id
               ORDER BY l.date""").fetchall()
    return _csv_response(
        "lessons.csv",
        ["ID", "Дата", "Начало", "Конец", "Группа", "Тема", "Статус",
         "Записано", "Пришло"],
        [tuple(r) for r in rows])


@app.get("/export/attendance.csv", dependencies=AUTH)
def export_attendance():
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT lr.id, l.date, c.name, u.name, lr.visit
               FROM lesson_records lr
               JOIN lessons l ON l.id = lr.lesson_id
               LEFT JOIN classes c ON c.id = l.class_id
               LEFT JOIN users u ON u.id = lr.user_id
               ORDER BY l.date""").fetchall()
    return _csv_response(
        "attendance.csv",
        ["ID", "Дата занятия", "Группа", "Ученик", "Посетил (1/0)"],
        [tuple(r) for r in rows])


@app.get("/export/raw/{table}.json", dependencies=AUTH)
def export_raw(table: str):
    allowed = {"users", "joins", "payments", "invoices", "classes", "courses",
               "filials", "managers", "subscriptions", "user_subscriptions",
               "lessons", "lesson_records"}
    if table not in allowed:
        raise HTTPException(404)
    with db.get_conn() as conn:
        rows = conn.execute(f"SELECT raw FROM {table}").fetchall()
    payload = "[" + ",".join(r["raw"] or "null" for r in rows) + "]"
    # проверим, что получился валидный JSON
    json.loads(payload)
    return StreamingResponse(
        iter([payload.encode()]), media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{table}.json"'},
    )


# --- Wazzup webhook: метка канала + пересылка событий в МойКласс -----------

CHANNEL_TAG = {"whatsapp": 117413, "telegram": 117414, "max": 117415}
MK_HOOK_DEFAULT = ("https://api.moyklass.com/v1/hooks/wazzupEvents/"
                   "1-e174eb5b2ce14848dbe1910ba2af6ab4")


def _fwd_store(payload: dict) -> None:
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS wazzup_fwd_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT, ts TEXT)""")
        conn.execute("INSERT INTO wazzup_fwd_queue (payload, ts) VALUES (?, datetime('now'))",
                     (json.dumps(payload),))


def wazzup_forward(payload: dict) -> bool:
    """Переслать событие в МойКласс (их вебхук — чтобы чаты в CRM жили)."""
    import httpx
    url = db.get_setting("wazzup_forward_url", MK_HOOK_DEFAULT)
    for attempt in range(3):
        try:
            r = httpx.post(url, json=payload, timeout=15)
            if r.status_code < 500:
                return True
        except httpx.HTTPError:
            pass
    return False


def _wazzup_tag(payload: dict) -> None:
    for msg in payload.get("messages", []):
        if msg.get("isEcho"):
            continue
        tag_id = CHANNEL_TAG.get((msg.get("chatType") or "").lower())
        phone = "".join(ch for ch in str(msg.get("chatId") or "") if ch.isdigit())
        if not tag_id or len(phone) < 10:
            continue
        try:
            from .moyklass_client import MoyklassClient
            mk = MoyklassClient(sync.get_api_key())
            try:
                found = mk.get("/v1/company/users", {"phone": phone[-10:], "limit": 5})
                users = found.get("users", found) if isinstance(found, dict) else found
                for u in users:
                    tags = [t["id"] if isinstance(t, dict) else t for t in (u.get("tags") or [])]
                    if tag_id not in tags:
                        mk.post(f"/v1/company/users/{u['id']}/tags",
                                {"tags": sorted(set(tags) | {tag_id})})
            finally:
                mk.close()
        except Exception:
            logging.getLogger("kidsup.wazzup").exception("тег канала: не вышло для %s", phone)


def _inbox_store(payload: dict) -> None:
    """Входящие — в wazzup_inbox (для /api/replies и защиты рассылок),
    наши исходящие (isEcho) — в wazzup_outbox: по ним видно, ответили ли мы."""
    from . import autopilot
    rows, echoes = [], []
    for msg in payload.get("messages", []):
        phone = "".join(ch for ch in str(msg.get("chatId") or "") if ch.isdigit())
        if len(phone) < 10:
            continue
        ts = autopilot._now().isoformat(timespec="seconds")
        if msg.get("isEcho"):
            etext = (msg.get("text") or "").strip() or f"[{msg.get('type', 'вложение')}]"
            echoes.append((ts, phone, str(msg.get("messageId") or ""), etext[:500]))
            continue
        text = (msg.get("text") or "").strip() or f"[{msg.get('type', 'вложение')}]"
        rows.append((ts, phone, (msg.get("chatType") or "")[:12], text[:500],
                     str(msg.get("messageId") or "")))
    if not rows and not echoes:
        return
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS wazzup_inbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
            chat_type TEXT, text TEXT, message_id TEXT UNIQUE)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS wazzup_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
            message_id TEXT UNIQUE, text TEXT)""")
        try:
            conn.execute("ALTER TABLE wazzup_outbox ADD COLUMN text TEXT")
        except Exception:
            pass
        if rows:
            conn.executemany(
                "INSERT OR IGNORE INTO wazzup_inbox (ts, phone, chat_type, text, message_id) "
                "VALUES (?, ?, ?, ?, ?)", rows)
        if echoes:
            conn.executemany(
                "INSERT OR IGNORE INTO wazzup_outbox (ts, phone, message_id, text) "
                "VALUES (?, ?, ?, ?)", echoes)


def _wazzup_raw(payload: dict) -> None:
    """Сырые события вебхука — чтобы видеть, что Wazzup реально присылает
    (в частности, приходят ли isEcho-события об ответах сотрудников)."""
    from . import autopilot
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS wazzup_raw (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, body TEXT)""")
        conn.execute("INSERT INTO wazzup_raw (ts, body) VALUES (?, ?)",
                     (autopilot._now().isoformat(timespec="seconds"),
                      json.dumps(payload, ensure_ascii=False)[:4000]))
        conn.execute("DELETE FROM wazzup_raw WHERE id <= "
                     "(SELECT MAX(id) - 300 FROM wazzup_raw)")


def _wazzup_process(payload: dict) -> None:
    try:
        _wazzup_raw(payload)
    except Exception:
        logging.getLogger("kidsup.wazzup").exception("raw: не сохранилось")
    try:
        _inbox_store(payload)
    except Exception:
        logging.getLogger("kidsup.wazzup").exception("inbox: не сохранилось")
    if not wazzup_forward(payload):
        _fwd_store(payload)  # автопилот дошлёт позже
        logging.getLogger("kidsup.wazzup").warning("пересылка в МойКласс не удалась — в очередь")
    _wazzup_tag(payload)


APP_VERSION = "2026-08-14.12"  # видно в /api/health — чтобы проверять, что обновление применилось


@app.get("/api/health")
async def health():
    from . import autopilot
    today = autopilot._today().isoformat()
    return {"ok": True, "version": APP_VERSION,
            "msk": autopilot._now().isoformat(timespec="seconds"),
            "morning_done": autopilot._has_mark("morning", today)}


SETTABLE = {"admin_schedule", "daily_tasks_per_admin", "broadcast_per_hour", "broadcast_transports",
            "wazzup_dry_run", "digest_phone", "autopilot", "missed_reject_attempts", "wa_daily_cap", "wa_per_hour", "wa_senders",
            "broadcast_until", "call_admins", "chat_admin", "moyklass_group_url"}


@app.get("/api/settings", dependencies=AUTH)
async def api_get_settings():
    return {k: db.get_setting(k) for k in sorted(SETTABLE)}


@app.post("/api/settings", dependencies=AUTH)
async def api_set_setting(payload: dict):
    """{"key": "...", "value": "..."} — только ключи из SETTABLE."""
    key, value = (payload.get("key") or "").strip(), payload.get("value")
    if key not in SETTABLE or value is None:
        raise HTTPException(400, f"key должен быть одним из {sorted(SETTABLE)}")
    db.set_setting(key, str(value))
    return {"ok": True, key: db.get_setting(key)}


@app.post("/api/deploy", dependencies=AUTH)
async def api_deploy(request: Request):
    """Самообновление: тело запроса — tar.gz с app/ и requirements.txt.
    Распаковывает в корень проекта и перезапускает службу через 2 секунды.
    Допустимы только относительные пути внутри app/ и requirements.txt."""
    import tarfile
    import threading
    import subprocess
    data = await request.body()
    if len(data) < 1000 or len(data) > 50_000_000:
        raise HTTPException(400, "подозрительный размер архива")
    root = Path(__file__).resolve().parent.parent
    try:
        tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except tarfile.TarError as e:
        raise HTTPException(400, f"не tar.gz: {e}")
    names = tar.getnames()
    for n in names:
        p = Path(n)
        if p.is_absolute() or ".." in p.parts or \
                not (n == "requirements.txt" or n == "app" or n.startswith("app/")):
            raise HTTPException(400, f"недопустимый путь в архиве: {n}")
    tar.extractall(root)  # noqa: S202 — пути проверены выше
    threading.Timer(2.0, lambda: subprocess.Popen(
        ["systemctl", "restart", "kidsup"])).start()
    logging.getLogger("kidsup.deploy").info(
        "deploy: распаковано %d файлов, перезапуск через 2 с", len(names))
    return {"ok": True, "files": len(names), "restarting": True,
            "hint": "через ~10 секунд проверьте /api/health — version должна смениться"}


@app.post("/api/restart", dependencies=AUTH)
async def api_restart():
    """Перезапуск службы (после deploy или при зависании фоновых потоков)."""
    import threading
    import subprocess
    threading.Timer(1.0, lambda: subprocess.Popen(
        ["systemctl", "restart", "kidsup"])).start()
    return {"ok": True, "restarting": True}


@app.post("/api/broadcast", dependencies=AUTH)
async def api_broadcast(payload: dict):
    """Кампания рассылки: {"campaign": "no1_digest", "segment": "warm|contin|camp|regular|y2425",
    "text": "..., {имя} = имя ребёнка"}. Отправка — постепенно, темп broadcast_per_hour."""
    from . import autopilot
    campaign = (payload.get("campaign") or "").strip()
    segment = (payload.get("segment") or "").strip()
    text = (payload.get("text") or "").strip()
    if not campaign or not text or segment not in ("warm", "contin", "camp", "regular", "y2425", "camp_past"):
        raise HTTPException(400, "нужны campaign, text и segment из списка")
    return autopilot.enqueue_broadcast(campaign, segment, text)


@app.post("/api/broadcast/add", dependencies=AUTH)
async def api_broadcast_add(payload: dict):
    """{"campaign": "...", "text": "...", "recipients": [{"phone","child"},…]} —
    добавить явный список получателей в кампанию (дубли телефонов пропускаются)."""
    from . import autopilot
    campaign = (payload.get("campaign") or "").strip()
    text = (payload.get("text") or "").strip()
    recips = payload.get("recipients")
    if not campaign or not text or not isinstance(recips, list) or not recips:
        raise HTTPException(400, "нужны campaign, text и непустой recipients")
    return autopilot.broadcast_add(campaign, text, recips)


@app.get("/api/broadcast/status", dependencies=AUTH)
async def api_broadcast_status():
    from . import autopilot
    return autopilot.broadcast_status()


@app.post("/api/broadcast/cancel", dependencies=AUTH)
async def api_broadcast_cancel(payload: dict = None):
    from . import autopilot
    return {"cancelled": autopilot.broadcast_cancel((payload or {}).get("campaign"))}


@app.post("/api/broadcast/requeue-undelivered", dependencies=AUTH)
async def api_broadcast_requeue(payload: dict = None):
    """Вернуть в очередь недоставленное в Telegram/MAX (доставка упала после
    ответа API). Опционально {"day": "2026-08-12"} — по умолчанию сегодня.
    Ответившие (wazzup_inbox) не дублируются."""
    from . import autopilot
    return autopilot.broadcast_requeue_undelivered((payload or {}).get("day"))


@app.post("/api/wazzup/mark-replied", dependencies=AUTH)
async def api_wazzup_mark_replied(payload: dict):
    """Ручная отметка ответивших (пока вебхук Wazzup не настроен): ответы,
    увиденные в чатах, фиксируются как доставленные. {"phones": ["79...", ...],
    "note": "MAX 12.08"}. Такие телефоны: идут первыми в обзвоне, исключаются
    из повторной отправки, в статистике считаются delivered_replied."""
    phones = [p for p in (payload or {}).get("phones", []) if p]
    note = (payload or {}).get("note", "manual")
    ts = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    added = 0
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS wazzup_inbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
            chat_type TEXT, text TEXT, message_id TEXT UNIQUE)""")
        for p in phones:
            digits = "".join(ch for ch in str(p) if ch.isdigit())
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO wazzup_inbox (ts, phone, chat_type, text, message_id) "
                    "VALUES (?, ?, 'manual', ?, ?)",
                    (ts, digits, note, f"manual-{digits}"))
                added += 1
            except Exception:
                pass
    return {"marked": added, "phones": phones}


@app.get("/api/wazzup/raw", dependencies=AUTH)
async def api_wazzup_raw(limit: int = 40, kind: str = ""):
    """Последние события вебхука Wazzup как есть. kind=echo — только события
    с isEcho (ответы сотрудников), kind=keys — сводка по типам событий."""
    with db.get_conn() as conn:
        try:
            rows = conn.execute(
                "SELECT ts, body FROM wazzup_raw ORDER BY id DESC LIMIT ?",
                (max(1, min(300, limit)),)).fetchall()
        except Exception:
            return {"count": 0, "events": [], "hint": "таблицы ещё нет — событий не было"}
    events = [{"ts": ts, "body": body} for ts, body in rows]
    if kind == "echo":
        events = [e for e in events if '"isEcho":true' in e["body"].replace(" ", "")]
    if kind == "keys":
        from collections import Counter
        cnt = Counter()
        for e in events:
            try:
                cnt.update(json.loads(e["body"]).keys())
            except Exception:
                pass
        return {"count": len(events), "top_keys": cnt.most_common()}
    return {"count": len(events), "events": events}


@app.get("/api/duty", dependencies=AUTH)
async def api_duty(day: str = ""):
    """Кто сегодня в смене: звонящий админ (по admin_schedule или очереди
    call_admins) и админ переписки. Нужно, чтобы задачи из разбора звонков и
    переписок падали на того, кто реально работает."""
    from . import autopilot
    admins = autopilot._admins()
    try:
        sched = json.loads(db.get_setting("admin_schedule") or "{}")
    except ValueError:
        sched = {}
    d = day or autopilot._today().isoformat()
    mid = sched.get(d)
    onduty = [a for a in admins if a.get("managerId") == mid] if mid else []
    if not onduty and admins:
        onduty = [admins[(autopilot._today().toordinal()) % len(admins)]]
    chat = db.get_setting("chat_admin", "")
    return {"date": d, "duty": onduty, "chat_admin": int(chat) if chat else None,
            "all_admins": admins, "schedule": sched}


@app.get("/api/dialogs", dependencies=AUTH)
async def api_dialogs(day: str = "", hours: int = 0):
    """Переписки за день целиком: {"phone": …, "name": …, "messages": [
    {"ts", "dir": "in"|"out", "text"}]}. day=YYYY-MM-DD (по умолчанию сегодня),
    либо hours=N — за последние N часов. Для разбора дня и рекомендаций."""
    from . import autopilot
    if hours:
        since = (autopilot._now() - timedelta(hours=hours)).isoformat(timespec="seconds")
        until = "9999"
    else:
        d = day or autopilot._today().isoformat()
        since, until = f"{d}T00:00:00", f"{d}T23:59:59"
    msgs: dict[str, list] = {}
    with db.get_conn() as conn:
        for table, direction in (("wazzup_inbox", "in"), ("wazzup_outbox", "out")):
            try:
                rows = conn.execute(
                    f"SELECT ts, phone, text FROM {table} WHERE ts >= ? AND ts <= ?",
                    (since, until)).fetchall()
            except Exception:
                rows = []
            for ts, phone, text in rows:
                msgs.setdefault(phone[-10:], []).append(
                    {"ts": ts, "dir": direction, "text": text or ""})
        names = {}
        for phone in msgs:
            row = conn.execute(
                "SELECT name FROM users WHERE substr(phone,-10)=? LIMIT 1", (phone,)).fetchone()
            names[phone] = row[0] if row else ""
    out = [{"phone": p, "name": names.get(p, ""),
            "messages": sorted(m, key=lambda x: x["ts"])}
           for p, m in msgs.items()]
    out.sort(key=lambda d: d["messages"][-1]["ts"], reverse=True)
    return {"count": len(out), "dialogs": out}


@app.get("/api/replies", dependencies=AUTH)
async def api_replies(since: str = ""):
    """Кто ответил в мессенджерах (с момента установки учёта). since=YYYY-MM-DD."""
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS wazzup_inbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
            chat_type TEXT, text TEXT, message_id TEXT UNIQUE)""")
        rows = conn.execute(
            "SELECT ts, phone, chat_type, text FROM wazzup_inbox "
            "WHERE ts >= ? ORDER BY ts", (since or "2026-01-01",)).fetchall()
        sent = {"".join(ch for ch in (r[0] or "") if ch.isdigit())[-10:]
                for r in conn.execute(
                    "SELECT phone FROM broadcast_queue WHERE status='sent' "
                    "AND COALESCE(tried,'') LIKE '%whatsapp=ok%'")}
        names = {}
        for uid, name, phone in conn.execute("SELECT id, name, phone FROM users WHERE phone IS NOT NULL"):
            names["".join(ch for ch in str(phone) if ch.isdigit())[-10:]] = name
    out = []
    for ts, phone, chat_type, text in rows:
        p10 = phone[-10:]
        out.append({"ts": ts, "phone": phone, "name": names.get(p10),
                    "channel": chat_type, "text": text,
                    "got_broadcast": p10 in sent})
    return {"count": len(out), "replies": out}


@app.post("/api/autopilot/morning", dependencies=AUTH)
async def run_morning_now():
    """Принудительно создать/досоздать утренние порции (идемпотентно)."""
    from . import autopilot

    def _run() -> None:
        mk = autopilot._client()
        try:
            autopilot.morning_tasks(mk)
            autopilot._mark("morning", str(autopilot._today()))
        except Exception:
            logging.getLogger("kidsup.autopilot").exception("ручной запуск порций упал")
        finally:
            mk.close()

    import threading
    threading.Thread(target=_run, daemon=True).start()
    return {"started": True}


MARQUIZ_KEY = "mqz-KdsUp-2026-hQ7rT"  # секрет в URL вебхука Марквиза


@app.post("/marquiz/webhook")
async def marquiz_webhook(request: Request, key: str = ""):
    """Заявки из квизов Марквиз → лид + клиент/заявка в МойКласс.

    В панели Марквиза: Интеграции → Вебхук →
    https://app.kidsup.ru/marquiz/webhook?key=<секрет>"""
    if key != MARQUIZ_KEY:
        raise HTTPException(403)
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "нужен JSON")
    contacts = payload.get("contacts") or {}
    phone = str(contacts.get("phone") or payload.get("phone") or "").strip()
    if not phone:
        return {"ok": False, "error": "нет телефона"}
    name = str(contacts.get("name") or payload.get("name") or "").strip()
    quiz = ((payload.get("quiz") or {}).get("name")
            or payload.get("quizName") or "квиз")
    answers = payload.get("answers") or []
    qa = "; ".join(
        f"{a.get('q') or a.get('question') or ''}: {a.get('a') or a.get('answer') or ''}".strip(": ")
        for a in answers if isinstance(a, dict))[:800]
    lead_id = leads.save_lead({
        "source": "marquiz", "promo": "", "parent_name": name, "phone": phone,
        "child_name": "", "child_age": "", "interests": [],
        "comment": f"Марквиз «{quiz}». {qa}".strip(),
        "consent_pd": "on", "consent_ads": None,
    })
    try:
        ok, msg = leads.push_to_crm(lead_id)
    except Exception as e:  # noqa: BLE001
        ok, msg = False, str(e)
    logging.getLogger("kidsup.marquiz").info("заявка %s (%s): crm=%s %s",
                                             name, phone, ok, msg)
    return {"ok": True, "lead_id": lead_id, "crm": ok}


@app.get("/wazzup/webhook")
async def wazzup_webhook_check():
    return {"ok": True}


@app.post("/wazzup/webhook")
async def wazzup_webhook(request: Request):
    """Принимаем событие, отвечаем 200 сразу; работа — в фоне."""
    try:
        payload = await request.json()
    except Exception:
        return {"ok": True}
    if payload:
        import threading
        threading.Thread(target=_wazzup_process, args=(payload,), daemon=True).start()
    return {"ok": True}
