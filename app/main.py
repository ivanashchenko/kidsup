"""KidsUp Analytics — выгрузка и аналитика данных из CRM МойКласс."""

import csv
import io
import json
import logging
import re
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import analytics, config, db, leads, sync
from . import content as content_mod
from . import descriptions as descr_mod
from . import assistant as assistant_mod

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


@app.get("/api/search", dependencies=AUTH)
def api_search(q: str = ""):
    """Глобальный поиск из шапки: группы 2026/27 и клиенты (имя/телефон)."""
    q = " ".join(q.split())
    if len(q) < 2:
        return {"items": []}
    items: list[dict] = []
    digits = "".join(ch for ch in q if ch.isdigit())
    ql = q.lower()
    with db.get_conn() as conn:
        # групп 2026/27 немного — фильтруем в Python: SQLite LIKE не умеет
        # кириллицу без учёта регистра
        rows = conn.execute("""
            SELECT cl.id, cl.name, co.name course, cl.max_students,
                   COUNT(DISTINCT j.user_id) enrolled
            FROM classes cl LEFT JOIN courses co ON co.id = cl.course_id
            LEFT JOIN joins j ON j.class_id = cl.id
            WHERE cl.name LIKE '2627%'
            GROUP BY cl.id""").fetchall()
        hits = [r for r in rows
                if ql in (r["name"] or "").lower() or ql in (r["course"] or "").lower()]
        for r in hits[:5]:
            free = max(0, (r["max_students"] or 0) - r["enrolled"])
            items.append({"type": "Группа", "title": r["name"],
                          "sub": f"{r['course'] or ''} · свободно {free}",
                          "url": f"/enrollment?q={r['name']}"})
        if digits and len(digits) >= 4:
            urows = conn.execute(
                "SELECT id, name, phone FROM users WHERE phone LIKE ? LIMIT 5",
                (f"%{digits}%",)).fetchall()
        else:
            variants = {q, q.capitalize(), q.title(), ql}
            conds = " OR ".join(["name LIKE ?"] * len(variants))
            urows = conn.execute(
                f"SELECT id, name, phone FROM users WHERE {conds} LIMIT 5",
                tuple(f"%{v}%" for v in variants)).fetchall()
        for r in urows:
            url = (f"/brief?phone={r['phone']}" if r["phone"]
                   else f"/students?q={r['name'] or digits}")
            items.append({"type": "Клиент", "title": r["name"] or "Без имени",
                          "sub": r["phone"] or "", "url": url})
    return {"items": items[:9]}


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
        "lines": [("Мини-сад, 20 посещений (5 дней)", 34500, 35500),
                  ("Мини-сад, 16 посещений (4 дня)", 32000, 33000),
                  ("Мини-сад, 12 посещений (3 дня)", 25800, 26600),
                  ("Мини-сад, 8 посещений (2 дня)", 19000, 19600),
                  ("Мини-сад, разовое посещение", 2700, 2700),
                  ("Нулевой класс, 20 посещений (5 дней)", 34500, 35500),
                  ("Нулевой класс, 12 посещений (3 дня)", 25800, 26600)]},
    "Английский язык": {"title": "Кембриджские уровни Starters/Movers/Flyers · занятие 50 мин",
        "lines": [("8 занятий (2 р/нед)", 8200, 8400),
                  ("4 занятия (1 р/нед)", 4800, 5000)]},
    "Подготовка к школе": {"title": "Ступени 1 и 2 · занятие 50 мин",
        "lines": [("8 занятий (2 р/нед)", 8200, 8400),
                  ("4 занятия (1 р/нед)", 4800, 5000),
                  ("Разовое занятие", 1600, 1600)]},
    "Дошкольный университет": {"title": "Занятие 50 мин",
        "lines": [("8 занятий (2 р/нед)", 8200, 8400),
                  ("4 занятия (1 р/нед)", 4800, 5000),
                  ("Разовое занятие", 1600, 1600)]},
    "Раннее развитие": {
        "title": "Три программы по возрасту: «Музыка и речь» (50 мин), "
                 "«Первая школа» и «Лицей для малышей» (45 мин)",
        "lines": [("Музыка и речь · 8 занятий (2 р/нед)", 8200, 8600),
                  ("Музыка и речь · 4 занятия (1 р/нед)", 5000, 5200),
                  ("Первая школа · 8 занятий (2 р/нед)", 7600, 7800),
                  ("Первая школа · 4 занятия (1 р/нед)", 4800, 5000),
                  ("Лицей для малышей · 8 занятий (2 р/нед)", 7600, 7800),
                  ("Лицей для малышей · 4 занятия (1 р/нед)", 4800, 5000),
                  ("Разовое занятие (любая программа)", 1600, 1600)]},
    "Ментальная арифметика": {"title": "Занятие 90 мин",
        "lines": [("4 занятия", 8000, 8600)]},
    "ИЗО-студия": {"title": "Занятие 50 мин · первое занятие условно-бесплатное (входит в абонемент)",
        "lines": [("8 занятий (2 р/нед)", 6800, 7000),
                  ("4 занятия (1 р/нед)", 4700, 4900),
                  ("Разовое занятие", 1600, 1600),
                  ("Первое занятие, если не купили абонемент", 850, 850)]},
    "Шахматы": {"title": "Занятие 50 мин · первое занятие условно-бесплатное (входит в абонемент)",
        "lines": [("8 занятий (2 р/нед)", 6800, 7000),
                  ("4 занятия (1 р/нед)", 4700, 4900),
                  ("Разовое занятие", 1600, 1600),
                  ("Первое занятие, если не купили абонемент", 850, 850)]},
    "Скорочтение (техника чтения)": {"title": "Занятие 50 мин · с 7 лет",
        "lines": [("8 занятий", 8200, 8400)]},
    "Каллиграфия + грамота": {"title": "Занятие 50 мин · с 7 лет",
        "lines": [("8 занятий", 8200, 8400)]},
    "Робототехника": {"title": "Занятие 55 мин · партнёрский курс, цену ставит партнёр",
        "lines": [("4 занятия", 6400, 6400),
                  ("Разовое занятие", 2000, 2000),
                  ("Пробное занятие", 1100, 1100)]},
    "Логопед": {"title": "Индивидуально. На /price указаны только консультации",
        "lines": [("Консультация с Мариной, 40 мин", 2700, 2700),
                  ("Консультация с Еленой, 40 мин", 2200, 2500)]},
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
        "close": "«Первый день условно-бесплатный: не понравится — ничего не платите, понравится — этот день входит в первый абонемент. На какой день записать?»",
        "combo": "Логопед и ИЗО — идут в те же дни после мини-сада."},
    "Английский язык": {
        "what": "Английский по кембриджской лестнице: Pre-A1 Starters → A1 Movers → A1-A2 Flyers. Запись не по возрасту, а по уровню — педагог определяет его на первом занятии. Язык через игру, песни и сценки: дети говорят с первого занятия, а не переводят слова.",
        "why": "За первый год (Starters) — около 300 слов и 40 фраз, ребёнок отвечает о себе и семье. В группе до 8 детей: каждый говорит минимум 12 раз за занятие. В конце года уровневый тест: виден результат, а не «занимался».",
        "close": "«Давайте определим уровень — бесплатно, 10 минут в центре, и сразу подберём группу. Вам удобнее понедельник-среда или вторник-четверг?»",
        "combo": "Шахматы или менталка вторым предметом (−10%); дошкольникам — подготовка к школе."},
    "Подготовка к школе": {
        "what": "Методика Буракова, два уровня: ПШ1 для нечитающих и ПШ2 для читающих. Чтение, математика и письмо в одном занятии, короткие блоки со сменой деятельности — дети не устают.",
        "why": "К школе ребёнок читает, считает и умеет держать внимание 40 минут. Есть группы для нечитающих и для тех, кто уже начал.",
        "close": "«Есть группа как раз по возрасту, осталось N мест. Первое занятие условно-бесплатное: не понравится — не платите, понравится — оно входит в абонемент. Записываю на этой неделе?»",
        "combo": "Английский язык, ИЗО или шахматы вторым предметом — −10%."},
    "Раннее развитие": {
        "what": "Три программы по возрасту. «Музыка и речь» 1–3 года (с мамой, музыка, ритм, "
                "запуск речи, педагог-логопед). «Первая школа» 1–3 года (сенсорика, моторика, "
                "первые понятия, логика). «Лицей для малышей» 3–4 года (45 минут, больше "
                "самостоятельности, формат «как в школе»).",
        "why": "Единая лестница без разрывов: с мамой → сам в группе → лицей → подготовка к школе "
               "с 4 лет. Ребёнок не теряет год и привыкает заниматься до сада.",
        "close": "«Подберу программу точно по возрасту — сколько сейчас месяцев? Первое занятие "
                 "условно-бесплатное: платите только если понравится».",
        "combo": "Логопед, если есть вопросы к речи; дальше — мини-сад."},
    "Логопед": {
        "what": "Индивидуально: диагностика речи, постановка звуков, запуск речи у неговорящих, подготовка к школьной грамоте.",
        "why": "Чем раньше начать, тем короче курс — в 3–4 года звуки ставятся в разы быстрее, чем в 6.",
        "close": "«Первый шаг — консультация 40 минут: педагог скажет, есть ли проблема и сколько нужно занятий. Записать на эту неделю?»",
        "combo": "Мини-сад или раннее развитие — речь быстрее запускается в среде."},
    "ИЗО-студия": {
        "what": "Четыре разных группы: живопись, лепка, картины великих художников, алфавитная живопись — не «рисование вообще», а программа.",
        "why": "Ребёнок уносит готовую работу с каждого занятия — виден результат, растёт уверенность и усидчивость.",
        "close": "«Первое занятие условно-бесплатное: ребёнок нарисует первую картину, не понравится — не платите. На какой день записать?» (не купили абонемент — занятие 850 ₽)",
        "combo": "Второй предмет к подготовке к школе и английскому (−10%)."},
    "Шахматы": {
        "what": "Отдельные группы для начинающих и продолжающих, с 4 лет. Тренируют логику, счёт и умение думать на шаг вперёд.",
        "why": "Лучшая тренировка внимания и усидчивости перед школой; сильные ребята ездят на турниры.",
        "close": "«Первое занятие условно-бесплатное: педагог посмотрит уровень и определит в группу. Не понравится — не платите. Записываю?» (не купили абонемент — занятие 850 ₽)",
        "combo": "К английскому и подготовке к школе; отдельные группы начинающих и продолжающих."},
    "Ментальная арифметика": {
        "what": "CleverStart: счёт на соробане и в уме, две возрастные группы — 4–7 и 7–12 лет, занятие 90 минут.",
        "why": "Растёт скорость мышления и концентрация — заметно по школьной математике уже через пару месяцев.",
        "close": "«Первое занятие условно-бесплатное — увидите, как ребёнок считает без калькулятора, и платите только если понравится. Суббота или будни?»",
        "combo": "Второй предмет к английскому и школьным курсам (−10%)."},
    "Робототехника": {
        "what": "Ребёнок сам собирает и программирует робота, работает по инструкции и в команде, лучшие едут на соревнования.",
        "why": "Экранное время превращается в созидание; отличный второй предмет для 4–12 лет.",
        "close": "«Пробное 1 100 ₽ (партнёрский курс) — соберёт первого робота и всё поймёт сам. Записать на ближайшее?»",
        "combo": "Идёт вторым к любому основному предмету."},
    "Скорочтение (техника чтения)": {
        "what": "Для школьников 7–12: техника чтения, понимание текста, память и внимание.",
        "why": "Домашка занимает меньше времени — ребёнок читает быстрее и сразу понимает прочитанное.",
        "close": "«Первое занятие условно-бесплатное — замерим текущую скорость чтения, будет с чем сравнить. Платите только если понравится. Записываю?»",
        "combo": "Каллиграфия + грамота — берут парой (−10% на второй)."},
    "Каллиграфия + грамота": {
        "what": "Для школьников 7–12: постановка почерка, письмо без ошибок, грамотность.",
        "why": "Снимает главную боль началки — «пишет как курица лапой» и ошибки по невнимательности.",
        "close": "«Первое занятие условно-бесплатное: педагог посмотрит почерк и скажет, что поправить. Платите только если понравится. На какой день?»",
        "combo": "Скорочтение — берут парой (−10% на второй)."},
}

# особенности групп — по ключевым словам в названии
FEATURES = [("starters", "Starters · 1-й год"), ("movers", "Movers · 2-й год"),
            ("flyers", "Flyers · 3-й год"),
            ("пш1", "ПШ1 · нечитающие"), ("пш2", "ПШ2 · читающие"),
            ("продолжающие", "продолжающие"), ("начинающие", "начинающие"),
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
                    age: str = "", slot: str = "", feature: str = "", q: str = "",
                    waitlist: int = 0):
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
            "name": name,
            "course": COURSE_ALIAS.get(r["course"] or "",
                                       r["course"] or (parts[1] if len(parts) > 1 else "?")),
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
    all_groups = list(groups)          # до фильтров — для табло набора
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
    # сколько пробных уже назначено на будущие занятия группы
    with db.get_conn() as conn:
        trials = dict(conn.execute("""
            SELECT l.class_id, COUNT(*) FROM lesson_records lr
            JOIN lessons l ON l.id = lr.lesson_id
            WHERE l.date >= date('now') AND lr.raw LIKE '%"test": true%'
            GROUP BY l.class_id""").fetchall())
    for g in groups:
        g["trials"] = trials.get(g["id"], 0)
    # табло набора: одна цифра, ради которой всё и делается
    real = [g for g in all_groups if not g["buffer"]]
    total_places = sum(g["capacity"] for g in real)
    total_taken = sum(g["enrolled"] for g in real)
    days_left = max(0, (date(2026, 9, 30) - autopilot._today()).days)
    board = {
        "places": total_places, "taken": total_taken,
        "free": max(0, total_places - total_taken),
        "pct": round(total_taken * 100 / total_places) if total_places else 0,
        "days": days_left,
        "per_day": round((total_places - total_taken) / days_left, 1) if days_left else 0,
        "full": sum(1 for g in real if g["free"] <= 0),
        "empty": sum(1 for g in real if g["enrolled"] == 0),
        "groups": len(real),
    }
    return render(request, "enrollment.html", active="enrollment", board=board,
                  groups=groups, courses=courses_list, course=course,
                  days=days_list, day=day, free=free,
                  age=age, slots=slots_list, slot=slot,
                  features=features_list, feature=feature, q=q,
                  prices=price_blocks, pitches=pitches, waitlist=_waitlist_rows(),
                  summary=sorted(summary.values(), key=lambda x: -x["free"]))



@app.get("/courses", response_class=HTMLResponse, dependencies=AUTH)
def courses_page(request: Request, c: str = ""):
    """Витрина занятий: полные описания + короткие версии для WhatsApp."""
    groups = _enrollment_groups()
    free_by_course: dict[str, int] = {}
    sad_split = {"Мини-сад": 0, "Нулевой": 0}
    for g in groups:
        if g["buffer"]:
            continue
        free_by_course[g["course"]] = free_by_course.get(g["course"], 0) + g["free"]
        for k in sad_split:
            if k in g["name"]:
                sad_split[k] += g["free"]
    TITLES = {"minisad": "Мини-сад с английским", "zeroclass": "Нулевой класс",
              "reading": "Скорочтение"}
    items = []
    for cc in descr_mod.COURSES:
        if cc["key"] == "minisad":
            free_now = sad_split["Мини-сад"]
        elif cc["key"] == "zeroclass":
            free_now = sad_split["Нулевой"]
        else:
            free_now = free_by_course.get(cc["course"])
        plain = re.sub(r"<li>", "• ", cc["html"])
        plain = re.sub(r"<[^>]+>", "", plain)
        plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
        trial = None
        if cc["key"] in ("minisad", "zeroclass", "dance",
                         "choreography", "football", "martial", "acrobatics"):
            pass  # сад/нулевой — пробный день; лист ожидания — условия при старте группы
        else:
            trial = descr_mod.TRIAL
        items.append({**cc, "title": TITLES.get(cc["key"], cc["course"]),
                      "free_now": free_now,
                      "wa_full": descr_mod.wa_text(cc),
                      "plain": f"{cc['tag']}\n\n{plain}", "trial_note": trial})
    return render(request, "courses.html", active="courses",
                  items=items, sel=c if any(x["key"] == c for x in items) else "")


@app.get("/ask", response_class=HTMLResponse, dependencies=AUTH)
def ask_page(request: Request):
    """Чат-помощник для админов: живые данные набора + Anthropic API."""
    return render(request, "ask.html", active="ask",
                  has_key=bool(db.get_setting("anthropic_api_key")))


@app.post("/api/ask", dependencies=AUTH)
async def api_ask(payload: dict):
    msgs = payload.get("messages") or []
    return assistant_mod.ask(msgs, _enrollment_groups())


@app.get("/api/ask/health", dependencies=AUTH)
async def api_ask_health():
    """Достижимость Anthropic API с сервера + настроен ли ключ."""
    out = assistant_mod.probe()
    out["key_set"] = bool(db.get_setting("anthropic_api_key"))
    out["model"] = db.get_setting("assistant_model") or assistant_mod.DEFAULT_MODEL
    return out


@app.get("/content", response_class=HTMLResponse, dependencies=AUTH)
def content_page(request: Request, g: str = ""):
    """Фабрика контента: готовые тексты каналов, собранные из живых данных набора."""
    groups = _enrollment_groups()
    blocks = content_mod.build(groups)
    gnames = content_mod.groups_of(blocks)
    return render(request, "content.html", active="content",
                  blocks=blocks, gnames=gnames, sel=g if g in gnames else "",
                  f=content_mod.facts(groups))


@app.post("/api/publish", dependencies=AUTH)
async def api_publish(request: Request):
    """Публикация макета: PNG рендерится в браузере и приходит сюда,
    сервер раздаёт по каналам (ВК, ТГ-канал)."""
    from . import publish as publish_mod
    form = await request.form()
    up = form.get("image")
    caption = (form.get("caption") or "").strip()
    channels = [c for c in (form.get("channels") or "").split(",") if c]
    if up is None or not channels:
        raise HTTPException(422, "нужны image и channels")
    image = await up.read()
    if len(image) > 8_000_000:
        raise HTTPException(413, "картинка больше 8 МБ")
    return publish_mod.publish(image, caption, channels)


@app.get("/api/publish/health", dependencies=AUTH)
def api_publish_health():
    return {"vk": bool(db.get_setting("vk_token") and db.get_setting("vk_group_id")),
            "tg": bool(db.get_setting("tg_bot_token") and db.get_setting("tg_channel"))}


@app.get("/makety", response_class=HTMLResponse, dependencies=AUTH)
def makety_page(request: Request):
    """Готовые визуалы (SVG→PNG) на живых данных: посты, сторис, плакат, листовка."""
    from . import makety as makety_mod
    f = content_mod.facts(_enrollment_groups())
    makets = makety_mod.build_makets(f)
    return render(request, "makety.html", active="makety",
                  makets=makets, mgroups=makety_mod.maket_groups(makets), f=f)


def _enrollment_groups() -> list[dict]:
    """Группы 2026/27 с остатком мест и ценой — то же, что видит страница набора."""
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT cl.id, cl.name, cl.max_students, co.name course,
                   COUNT(DISTINCT j.user_id) enrolled
            FROM classes cl
            LEFT JOIN courses co ON co.id = cl.course_id
            LEFT JOIN joins j ON j.class_id = cl.id
            WHERE cl.name LIKE '2627%'
            GROUP BY cl.id ORDER BY co.name, cl.name""").fetchall()
    out = []
    for r in rows:
        name = r["name"] or ""
        if name.startswith("OLD_") or "ТЕСТ" in name.upper():
            continue
        g_days = _name_days(name)
        times = _TIME_RE.findall(name)
        buffer = "аявк" in name
        cap = r["max_students"] or 8
        enrolled = r["enrolled"] or 0
        age_lo, age_hi = _group_ages(name)
        course = COURSE_ALIAS.get(r["course"] or "", r["course"] or "?")
        pr = PRICES.get(course)
        out.append({
            "name": name, "course": course, "buffer": buffer,
            "day": " · ".join(DAY_LABEL[d] for d in g_days) or "—",
            "time": " · ".join(times[:2]) or "—",
            "age_lo": age_lo, "age_hi": age_hi,
            "age": (f"{age_lo:g}–{age_hi:g} лет" if age_lo else "—"),
            "enrolled": enrolled, "capacity": cap,
            "free": 0 if buffer else max(0, cap - enrolled),
            "price_new": pr["lines"][0][2] if pr else None,
            "price_label": pr["lines"][0][0] if pr else None,
        })
    return out


# --- план обзвона: кого перезаписать на 2026/27 ---------------------------

def _age_from_raw(raw: str) -> float | None:
    """Возраст ребёнка из атрибута birthday в карточке МойКласс."""
    try:
        j = json.loads(raw or "{}")
    except ValueError:
        return None
    for a in j.get("attributes") or []:
        if a.get("attributeAlias") == "birthday" and a.get("value"):
            try:
                y, m, _d = (int(x) for x in str(a["value"])[:10].split("-"))
            except ValueError:
                return None
            t = date.today()
            return round((t.year - y) + (t.month - m) / 12, 1)
    return None


CALL_SEGMENTS = {
    "all": "Все — вся база",
    "summer": "Ходили этим летом (самые тёплые)",
    "year": "Учебный год 25/26",
    "old": "Ходили давно (до сентября 25)",
}



# --- лист ожидания на заполненные группы ----------------------------------

def _waitlist_init(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS waitlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, class_id INTEGER,
        class_name TEXT, name TEXT, phone TEXT, note TEXT,
        status TEXT DEFAULT 'open')""")


APOLOGY_TEXT = (
    "Здравствуйте! Это KidsUP на Рокоссовского.\n"
    "Прошу прощения за прошлое сообщение — в нём ошибка: {имя_в} мы помним "
    "не по лагерю, а по занятиям «{курс}». Рассылка ушла общим текстом, "
    "это моя оплошность. Извините!\n\n"
    "Приглашение при этом в силе, и оно про другое: 29 августа у нас праздник "
    "открытия сезона, а 31 августа – 6 сентября — Неделя открытых уроков: можно "
    "прийти на любое занятие нового учебного года и выбрать своё.\n"
    "Записать {имя_в} на открытый урок?"
)

# Три программы раннего развития объединены в один предмет. Пока курс в
# МойКласс не переименован вручную, показываем правильное имя сами.
COURSE_ALIAS = {
    "Раннее развитие с Еленой. Музыка и речь": "Раннее развитие",
    "Раннее развитие с Ириной. Первая школа": "Раннее развитие",
    "Лицей для малышей": "Раннее развитие",
}


# в CRM курсы названы длинно — в письме родителю нужно коротко и по-человечески
COURSE_SHORT = {
    "Раннее развитие с Еленой. Музыка и речь": "Музыка и речь с Еленой",
    "Английский летний клуб": "летний клуб",
}


# --- база знаний: методички, скрипты, планы ---------------------------------
# Раньше они лежали файлами и терялись. Теперь открываются с любой страницы,
# сгруппированы по тому, когда именно нужны в работе.

CLIENT_STATE_NAMES = {
    125951: "1. Новый лид", 345768: "2. Недозвон", 146950: "3. Думает",
    125952: "4. Записался на пробное", 125953: "Посетил пробное",
    345767: "Думает-2", 125955: "Клиент", 125957: "Отказ",
    345759: "0. Архив набора", 146328: "0.1 Не писать", 146513: "0.3 13 лет+",
}

DOC_GROUPS = [
    ("Планы и деньги", [
        ("mega_plan", "🎯 Мега-план набора до 30.09",
         "Интерактивный план: цифры, роли, воронка, грабли. Галочки сохраняются"),
        ("reestr_del", "✅ Реестр важных и срочных дел",
         "Все дела команды с фильтрами по людям и срочности"),
        ("vozvraty_statistika", "💸 Статистика возвратов",
         "Причины и суммы возвратов с марта, собрано Лизой 18.08"),
        ("vozvraty_dolgi", "📒 Возвраты и долги: рабочая сводка",
         "Неоплаченные счета и должники — для обзвона Лизой"),
        ("promo_vvod", "🪧 Заведение промо-контакта",
         "Алгоритм Бориса: дубль → источник → тег → статус"),
        ("promo_status", "📋 Промо-контакты: статус по каждому",
         "Каждый контакт с листов: карточка, статус, кто и сколько звонил (срез 18.08)"),
    ]),
    ("Каждый день на смене", [
        ("tri_scenariya", "🧭 Три сценария админа: промо, входящие, база",
         "Что делать в каждом из трёх случаев — по шагам, со статусами и фразами"),
        ("prodazhi_vozrazheniya", "🎯 Продажи и возражения: схема разговора",
         "5 этапов, отработка «дорого/подумаю/сравним», цепочки для замолчавших, welcome-серия"),
        ("algoritm_obzvona", "📞 Обзвон базы: пошагово",
         "Главная инструкция дня: с чего начать, что говорить, что нажать в CRM"),
        ("zapis_v_gruppy", "📝 Запись в группы: корзина, потом день",
         "Почему сначала предмет и уровень, и только потом расписание"),
        ("karta_kabinetov", "🚪 Карта кабинетов",
         "Кто где занимается: предмет × день × комната. Печатать на ресепшен"),
        ("metodichka_v3", "Методичка администратора",
         "Как вести смену: приветствие, запись, оплата, конфликты"),
        ("skripty_v3", "Скрипты обзвона и переписки",
         "Что говорить по каждому поводу — дословно"),
        ("skripty_nabora", "📞 Скрипты набора: заявка, холодная база, возврат",
         "Три главных разговора со схемой касаний и возражениями — по материалам марафона"),
        ("resepshen_v2", "Ресепшен-листы",
         "Печатные листы на стойку: цены, расписание, ответы"),
        ("brify_adminov", "Брифы администраторов",
         "Короткие памятки по каждому предмету"),
    ]),
    ("Набор 2026/27", [
        ("plan_nabora_v2", "План набора (актуальный)",
         "Цель 523 места, сегменты, сроки, ответственные"),
        ("operativka_13_17", "Оперативный план недели",
         "Что делаем по дням"),
        ("plan_del_borisa", "План дел Бориса",
         "Что зависит только от собственника"),
    ]),
    ("Маркетинг и привлечение", [
        ("kontent_plan_13_30", "Контент-план",
         "Посты и сторис по дням"),
        ("listovki_promoutery", "Листовки и промоутеры",
         "Тексты листовок, механика сбора контактов"),
        ("audit_saitov", "Аудит сайтов",
         "Что чинить на kidsup.ru"),
    ]),
    ("Деньги и система", [
        ("bonusy_adminov", "💰 Бонусы администраторов",
         "Тариф, примеры «от звонка до денег», расчёт по месяцам на весь год"),
        ("sistema_pribyli", "Система прибыли",
         "Из чего складывается прибыль центра"),
        ("plan_nabora", "План набора (первая версия)",
         "Архив — для истории решений"),
    ]),
]


def _settings_ctx(request: Request, msg: str = "", ok: bool = True) -> HTMLResponse:
    """Страница настроек: ключ МойКласс, глубина выгрузки, сырые выгрузки."""
    key = sync.get_api_key()
    return render(
        request, "settings.html", active="settings",
        counts=db.table_counts(),
        key_from_env=bool(config.ENV_API_KEY),
        masked_key=(key[:4] + "…" + key[-4:]) if len(key) > 10 else ("задан" if key else ""),
        history_months=db.get_setting("history_months", str(config.DEFAULT_HISTORY_MONTHS)),
        msg=msg, ok=ok,
    )


@app.get("/settings", response_class=HTMLResponse, dependencies=AUTH)
def settings_page(request: Request):
    return _settings_ctx(request)


@app.post("/settings", response_class=HTMLResponse, dependencies=AUTH)
def settings_save(request: Request, api_key: str = Form(""),
                  history_months: str = Form("")):
    saved = []
    if api_key.strip():
        db.set_setting("moyklass_api_key", api_key.strip())
        saved.append("API-ключ")
    if history_months.strip().isdigit():
        db.set_setting("history_months", history_months.strip())
        saved.append("глубина выгрузки")
    msg = ("Сохранено: " + ", ".join(saved)) if saved else "Нечего сохранять."
    return _settings_ctx(request, msg, bool(saved))


@app.post("/settings/test", response_class=HTMLResponse, dependencies=AUTH)
def settings_test(request: Request):
    """Пробный запрос к МойКласс — сразу видно, живой ключ или нет."""
    if not sync.get_api_key():
        return _settings_ctx(request, "API-ключ не задан.", False)
    try:
        mk = autopilot._client()
        data = mk.get("/v1/company/managers")
        n = len(data.get("managers") if isinstance(data, dict) else data or [])
        return _settings_ctx(request, f"Связь есть: МойКласс отдал {n} сотрудников.", True)
    except Exception as e:                                    # noqa: BLE001
        return _settings_ctx(request, f"Не получилось: {e}", False)


@app.get("/base", response_class=HTMLResponse, dependencies=AUTH)
def base_page(request: Request):
    """Все методички в одном месте, сгруппированные по моменту применения."""
    root = Path(__file__).resolve().parent.parent / "docs"
    groups = []
    for title, items in DOC_GROUPS:
        rows = []
        for slug, name, note in items:
            f = root / f"{slug}.html"
            if f.exists():
                rows.append({"slug": slug, "name": name, "note": note,
                             "size": f.stat().st_size // 1024})
        if rows:
            groups.append({"title": title, "items": rows})
    return render(request, "base_docs.html", active="base", groups=groups)


@app.get("/base/{slug}", response_class=HTMLResponse, dependencies=AUTH)
def base_doc(slug: str):
    """Отдаёт методичку. Имя файла проверяем по белому списку."""
    known = {s for _, items in DOC_GROUPS for s, _, _ in items}
    if slug not in known:
        raise HTTPException(404)
    f = Path(__file__).resolve().parent.parent / "docs" / f"{slug}.html"
    if not f.exists():
        raise HTTPException(404)
    back = (
        '<div style="position:sticky;top:0;z-index:99;display:flex;gap:14px;'
        'align-items:center;flex-wrap:wrap;padding:9px 18px;'
        'background:rgba(255,255,255,.94);backdrop-filter:blur(10px);'
        'border-bottom:1px solid #E3E9F2;'
        'box-shadow:0 10px 24px -22px rgba(20,30,48,.6);'
        'font:600 14px/1.4 Inter,-apple-system,Segoe UI,Roboto,sans-serif">'
        '<a href="/base" style="color:#1481B4;text-decoration:none">← Все методички</a>'
        '<span style="color:#6B7A91;font-weight:500">KidsUP · база знаний</span>'
        '<button onclick="window.print()" style="margin-left:auto;border:1px solid '
        '#D2DBE8;background:#fff;color:#1A2333;border-radius:999px;padding:5px 14px;'
        'font:700 12.5px/1.2 inherit;cursor:pointer">🖨 Печать</button>'
        '<div style="position:absolute;left:0;right:0;bottom:-1px;height:3px;'
        'background:linear-gradient(90deg,#2AA7DE,#5FB53B 34%,#F5A81C 67%,#E5232A)">'
        '</div></div>')
    return HTMLResponse(back + f.read_text(encoding="utf-8"))


@app.get("/callaudit", response_class=HTMLResponse, dependencies=AUTH)
def callaudit_page(request: Request):
    """Учёт обзвона: по каждой семье — была ли задача, закрыта ли она,
    был ли звонок или сообщение, какой статус и записан ли ребёнок на 26/27.
    Отвечает на вопрос «кому позвонили и кого записали», а не «сколько задач»."""
    from . import sla
    mk = autopilot._client()
    try:
        tasks = sla._open_tasks(mk)
        done = mk.get("/v1/company/tasks", {"isComplete": "true", "limit": 100})
        tasks += (done.get("tasks") if isinstance(done, dict) else done) or []
    finally:
        mk.close()
    call_cats = {sla.CAT_CALL, sla.CAT_PUSH}
    by_user: dict[int, dict] = {}
    for t in tasks:
        uid = t.get("userId")
        if not uid or t.get("categoryId") not in call_cats:
            continue
        cur = by_user.get(uid)
        if cur and cur["date"] <= (t.get("beginDate") or ""):
            continue
        by_user[uid] = {"date": (t.get("beginDate") or "")[:10],
                        "manager": (t.get("managerIds") or [None])[0],
                        "done": bool(t.get("isComplete"))}
    names = {a["managerId"]: a["name"] for a in autopilot._admins()}
    names[84116] = "Борис"
    names[154181] = "Лиза"
    rows = []
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS mango_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
            direction TEXT, state TEXT)""")
        for uid, info in by_user.items():
            u = conn.execute("SELECT name, phone, raw FROM users WHERE id=?", (uid,)).fetchone()
            if not u:
                continue
            p = "".join(ch for ch in (u["phone"] or "") if ch.isdigit())[-10:]
            called = msg = False
            if len(p) == 10:
                called = bool(conn.execute(
                    "SELECT 1 FROM mango_calls WHERE substr(phone,-10)=? AND direction='out' "
                    "AND ts>=? LIMIT 1", (p, info["date"])).fetchone())
                try:
                    msg = bool(conn.execute(
                        "SELECT 1 FROM wazzup_outbox WHERE substr(phone,-10)=? AND ts>=? LIMIT 1",
                        (p, info["date"])).fetchone())
                except Exception:
                    msg = False
            enrolled = conn.execute(
                "SELECT COUNT(*) FROM joins j JOIN classes cl ON cl.id=j.class_id "
                "WHERE j.user_id=? AND cl.name LIKE '2627%'", (uid,)).fetchone()[0]
            try:
                state = json.loads(u["raw"] or "{}").get("clientStateId")
            except ValueError:
                state = None
            rows.append({"uid": uid, "name": u["name"], "phone": u["phone"],
                         "date": info["date"], "manager": names.get(info["manager"], "—"),
                         "done": info["done"], "called": called, "msg": msg,
                         "enrolled": enrolled, "state": CLIENT_STATE_NAMES.get(state, "—")})
    rows.sort(key=lambda r: (r["date"], r["name"] or ""))
    total = len(rows)
    stat = {
        "total": total,
        "done": sum(1 for r in rows if r["done"]),
        "touched": sum(1 for r in rows if r["called"] or r["msg"]),
        "enrolled": sum(1 for r in rows if r["enrolled"]),
        "ghost": sum(1 for r in rows if r["done"] and not (r["called"] or r["msg"])),
    }
    return render(request, "callaudit.html", active="callaudit", rows=rows, stat=stat)


@app.get("/journal", response_class=HTMLResponse, dependencies=AUTH)
def journal_page(request: Request, day: str = ""):
    """Кто что сделал: записи в группы, оплаты, комментарии — с автором.

    Журнала действий в МойКласс через API нет, но автор есть у самих объектов:
    join.managerId — кто записал, payment.managerId — кто провёл оплату,
    userComment.managerId — кто написал. Этого хватает, чтобы видеть работу
    поимённо, без вебхуков и без входа в интерфейс.
    """
    d = day or autopilot._today().isoformat()
    names = {a["managerId"]: a["name"] for a in autopilot._admins()}
    names.update({84116: "Борис", 154181: "Лиза"})
    rows = []
    with db.get_conn() as conn:
        for r in conn.execute("SELECT raw FROM joins"):
            try:
                j = json.loads(r["raw"] or "{}")
            except ValueError:
                continue
            if (j.get("createdAt") or "")[:10] != d:
                continue
            u = conn.execute("SELECT name FROM users WHERE id=?", (j.get("userId"),)).fetchone()
            cl_ = conn.execute("SELECT name FROM classes WHERE id=?", (j.get("classId"),)).fetchone()
            rows.append({"ts": (j.get("createdAt") or "")[11:16], "kind": "запись в группу",
                         "who": names.get(j.get("managerId"), j.get("managerId") or "—"),
                         "client": (u["name"] if u else "—"),
                         "what": (cl_["name"] if cl_ else "—")})
        for r in conn.execute("SELECT raw FROM payments"):
            try:
                p = json.loads(r["raw"] or "{}")
            except ValueError:
                continue
            ts = (p.get("createdAt") or "").replace("T", " ")
            if ts[:10] != d:
                continue
            u = conn.execute("SELECT name FROM users WHERE id=?", (p.get("userId"),)).fetchone()
            rows.append({"ts": ts[11:16], "kind": "оплата",
                         "who": names.get(p.get("managerId"), p.get("managerId") or "—"),
                         "client": (u["name"] if u else "—"),
                         "what": f"{p.get('summa')} ₽ · {p.get('comment') or ''}"[:70]})
    try:
        mk = autopilot._client()
        try:
            cm = mk.get("/v1/company/userComments", {"createdAt": [d, d], "limit": 200})
            for x in (cm.get("userComments") if isinstance(cm, dict) else cm) or []:
                with db.get_conn() as conn:
                    u = conn.execute("SELECT name FROM users WHERE id=?",
                                     (x.get("userId"),)).fetchone()
                rows.append({"ts": (x.get("createdAt") or "")[11:16], "kind": "комментарий",
                             "who": names.get(x.get("managerId"), x.get("managerId") or "—"),
                             "client": (u["name"] if u else "—"),
                             "what": (x.get("comment") or "")[:70]})
        finally:
            mk.close()
    except Exception as e:
        logging.getLogger("kidsup.journal").warning("комментарии не забрались: %s", e)
    rows.sort(key=lambda r: r["ts"], reverse=True)
    per = {}
    for r in rows:
        p = per.setdefault(r["who"], {"запись в группу": 0, "оплата": 0, "комментарий": 0})
        p[r["kind"]] = p.get(r["kind"], 0) + 1
    return render(request, "journal.html", active="journal", rows=rows, per=per, day=d)


@app.get("/metrics", response_class=HTMLResponse, dependencies=AUTH)
def metrics_page(request: Request):
    """Не «сколько задач», а что они дают: скорость первого касания,
    доля закрытых в тот же день, конверсия задачи в запись."""
    from . import sla
    try:
        load = sla.load_caps()
    except Exception as e:
        load = {"error": f"{type(e).__name__}: {e}", "load": {}, "over": {}}
    # «нагрузка смены» — только про админов; задачи Бориса/Маши (владелец,
    # маркетинг) — не смена, потолки к ним не относятся
    ADMINS_ONLY = {202856: "Лена", 232805: "Аня", 232763: "Ира", 154181: "Лиза"}
    load["load"] = {m: (load.get("load") or {}).get(m, {"calls": 0, "chats": 0, "other": 0})
                    for m in ADMINS_ONLY}
    names = {a["managerId"]: a["name"] for a in autopilot._admins()}
    for mid, nm in ADMINS_ONLY.items():
        names.setdefault(mid, nm)
    try:
        alert = json.loads(db.get_setting("sla_last_alert") or "{}")
    except ValueError:
        alert = {}
    # реальные звонки за день — напрямую из Mango (чужой центр уже отфильтрован)
    try:
        from . import mango
        _now = autopilot._now()
        mrows = mango.calls(_now.replace(hour=0, minute=0, second=0, microsecond=0), _now)
        calls = {"in": len({r["from_num"][-10:] for r in mrows if not r["from_ext"]}),
                 "out": len({r["to_num"][-10:] for r in mrows if r["from_ext"]}),
                 "talked": len({(r["to_num"] if r["from_ext"] else r["from_num"])[-10:]
                                for r in mrows if r["answer"]})}
    except Exception:
        calls = {}
    with db.get_conn() as conn:
        today = autopilot._today().isoformat()
        msgs = conn.execute(
            "SELECT COUNT(*) FROM wazzup_outbox WHERE ts >= ?", (today,)).fetchone()[0]
        # главный счёт недели: записи в группы по админам (создатель заявки).
        # joins обновляются лёгким синком каждые 5 минут
        yest = (autopilot._today() - timedelta(days=1)).isoformat()
        week_ago = (autopilot._today() - timedelta(days=6)).isoformat()
        # в зачёте только тройка набора: Лена, Аня, Ира. Лиза — операционка,
        # её записи (лагерь, переоформления) в соревнование не входят
        MGR_NAMES = {202856: "Лена", 232805: "Аня", 232763: "Ира"}
        rec: dict[int, dict] = {}
        IRA_SINCE = "2026-08-18"  # аккаунт Ирины Головиной передан новой Ире 18.08
        for (raw,) in conn.execute(
                "SELECT raw FROM joins WHERE created_at >= ?", (week_ago,)):
            try:
                j = json.loads(raw)
            except ValueError:
                continue
            mid = j.get("managerId")
            if mid not in MGR_NAMES or j.get("autoJoin"):
                continue
            if mid == 232763 and (j.get("createdAt") or "")[:10] < IRA_SINCE:
                continue  # записи прежнего владельца аккаунта — не в зачёт новой Иры
            day = (j.get("createdAt") or "")[:10]
            r = rec.setdefault(mid, {"today": 0, "yest": 0, "week": 0})
            r["week"] += 1
            if day == today:
                r["today"] += 1
            elif day == yest:
                r["yest"] += 1
    records = sorted(
        ({"name": MGR_NAMES[m], **v}
         for m, v in rec.items()),
        key=lambda x: (-x["today"], -x["week"]))
    return render(request, "metrics.html", active="metrics", load=load,
                  names=names, alert=alert, calls=calls, msgs=msgs, records=records,
                  sla=[(sla.CAT_NAME[k], v) for k, v in sla.SLA_MINUTES.items()],
                  caps={"calls": sla.CAP_CALLS, "chats": sla.CAP_CHATS})


@app.post("/waitlist/add", dependencies=AUTH)
async def waitlist_add(class_id: int = Form(...), class_name: str = Form(""),
                       name: str = Form(""), phone: str = Form(""), note: str = Form("")):
    """Записать клиента в лист ожидания по заполненной группе."""
    from . import autopilot
    with db.get_conn() as conn:
        _waitlist_init(conn)
        conn.execute("INSERT INTO waitlist (ts, class_id, class_name, name, phone, note) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (autopilot._now().isoformat(timespec="seconds"), class_id,
                      class_name[:120], name[:80], phone[:20], note[:200]))
    return RedirectResponse("/enrollment?waitlist=1", status_code=303)


@app.post("/waitlist/close/{row_id}", dependencies=AUTH)
async def waitlist_close(row_id: int):
    with db.get_conn() as conn:
        _waitlist_init(conn)
        conn.execute("UPDATE waitlist SET status='closed' WHERE id=?", (row_id,))
    return RedirectResponse("/enrollment", status_code=303)


def _waitlist_rows() -> list[dict]:
    with db.get_conn() as conn:
        _waitlist_init(conn)
        return [{"id": r[0], "ts": r[1], "class_name": r[3], "name": r[4],
                 "phone": r[5], "note": r[6]}
                for r in conn.execute(
                    "SELECT id, ts, class_id, class_name, name, phone, note FROM waitlist "
                    "WHERE status='open' ORDER BY id DESC LIMIT 100")]



# --- конструктор: несколько предметов в один вечер --------------------------

# длительность занятия по предмету (мин) — нужна, чтобы строить цепочки подряд
DURATION = {"Ментальная арифметика": 90, "Робототехника": 55,
            "Раннее развитие с Ириной. Первая школа": 45, "Лицей для малышей": 45,
            "Английский детский сад": 240}
DEFAULT_DURATION = 50
MAX_GAP = 30          # максимальная пауза между занятиями, мин
MAX_COMBOS = 24       # сколько связок показываем


def _slots(days: list[str], times: list[str]) -> list[tuple[str, str]]:
    """Пары «день + время»: «вт - чт 16:00» → оба дня в 16:00,
    «чт 19:00 сб 11:00» → каждый день со своим временем."""
    if not days:
        return []
    if len(times) == len(days):
        return list(zip(days, times))
    if times:
        return [(d, times[0]) for d in days]
    return []


def _minutes(hhmm: str) -> int | None:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _chains(cands: list[dict], size: int) -> list[list[dict]]:
    """Цепочки из size занятий подряд в один день: без пересечений,
    пауза между занятиями не больше MAX_GAP, предметы разные."""
    out: list[list[dict]] = []
    cands = sorted(cands, key=lambda x: x["start"])

    def walk(chain: list[dict]) -> None:
        if len(chain) == size:
            out.append(list(chain))
            return
        last = chain[-1]
        for c in cands:
            if c["start"] < last["end"] or c["start"] - last["end"] > MAX_GAP:
                continue
            if any(x["course"] == c["course"] for x in chain):
                continue
            chain.append(c)
            walk(chain)
            chain.pop()

    for c in cands:
        walk([c])
    return out


@app.get("/constructor", response_class=HTMLResponse, dependencies=AUTH)
def constructor_page(request: Request, age: str = "", day: str = "",
                     free: int = 1, size: int = 0, discount: str = ""):
    """Конструктор для родителя: 2–3 предмета в ОДИН день подряд, чтобы за
    один приезд ребёнок успел всё. Показывает цепочки, время и цену за месяц."""
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT cl.id, cl.name, cl.max_students, co.name course,
                   COUNT(DISTINCT j.user_id) enrolled
            FROM classes cl
            LEFT JOIN courses co ON co.id = cl.course_id
            LEFT JOIN joins j ON j.class_id = cl.id
            WHERE cl.name LIKE '2627%' AND cl.name NOT LIKE '%аявк%'
            GROUP BY cl.id""").fetchall()
    tpl = db.get_setting("moyklass_group_url",
                         "https://app.moyklass.com/class/{id}/joins")
    age_val = None
    try:
        age_val = float((age or "").replace(",", ".")) if age else None
    except ValueError:
        age_val = None

    by_day: dict[str, list[dict]] = {d: [] for d in DAY_ORDER}
    for r in rows:
        name = r["name"] or ""
        course = r["course"] or "?"
        cap, enrolled = (r["max_students"] or 8), (r["enrolled"] or 0)
        free_n = max(0, cap - enrolled)
        if free and free_n <= 0:
            continue
        lo, hi = _group_ages(name)
        if age_val is not None and lo is not None and not (lo - 0.5 <= age_val <= hi + 0.5):
            continue
        pr = PRICES.get(course)
        for d, t in _slots(_name_days(name), _TIME_RE.findall(name)):
            st = _minutes(t)
            if st is None:
                continue
            dur = DURATION.get(course, DEFAULT_DURATION)
            by_day[d].append({
                "id": r["id"], "name": name, "course": course, "day": d,
                "start": st, "end": st + dur, "time": t,
                "time_to": f"{(st + dur) // 60:02d}:{(st + dur) % 60:02d}",
                "free": free_n, "age": (f"{lo:g}–{hi:g}" if lo else ""),
                "price": pr["lines"][0][2] if pr else 0,
                "crm_url": tpl.replace("{id}", str(r["id"])),
            })

    sizes = [size] if size in (2, 3) else [3, 2]
    combos = []
    for d in DAY_ORDER:
        if day and d != day:
            continue
        for n in sizes:
            for ch in _chains(by_day[d], n):
                total = sum(c["price"] for c in ch)
                gaps = sum(ch[i + 1]["start"] - ch[i]["end"] for i in range(len(ch) - 1))
                combos.append({"day": d, "day_label": DAY_LABEL[d], "lessons": ch,
                               "total": total, "gaps": gaps, "n": len(ch),
                               "begin": ch[0]["time"], "finish": ch[-1]["time_to"]})
    # сначала больше предметов, потом меньше «окон», потом раньше начало
    combos.sort(key=lambda c: (-c["n"], c["gaps"], c["begin"]))
    seen, uniq = set(), []
    for c in combos:
        key = (c["day"], tuple(sorted(x["id"] for x in c["lessons"])))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
        if len(uniq) >= MAX_COMBOS:
            break
    # скидки НЕ суммируются — применяем одну, самую выгодную
    disc = 10 if discount else 0
    for c in uniq:
        c["total_disc"] = round(c["total"] * (100 - disc) / 100)
    return render(request, "constructor.html", active="constructor",
                  combos=uniq, age=age, day=day, free=free, size=size,
                  discount=discount, disc=disc,
                  days=[{"value": d, "label": DAY_LABEL[d]} for d in DAY_ORDER],
                  found=len(uniq))



# --- карточка-подсказка для разговора --------------------------------------



# --- готовые ответы на входящие сообщения ---------------------------------

DRAFT_RULES = [
    (("цен", "стоимост", "сколько стоит", "прайс"),
     "Ответить цену по нужному предмету + позвать на условно-бесплатное первое занятие: "
     "«Абонемент 8 занятий — X ₽ с 1 сентября. Первое занятие бесплатное — на какой день записать?»"),
    (("расписан", "какие есть заняти", "время"),
     "Прислать расписание по возрасту (кнопка «скопировать расписание» на странице подсказки) "
     "и закрыть вопросом: «Какое время удобнее — будни вечером или суббота?»"),
    (("запиш", "записать", "хотим", "давайте"),
     "Оформить запись в CRM сразу, затем подтвердить: группа, день, время, адрес, что взять с собой."),
    (("оплат", "счет", "счёт", "реквизит", "чек"),
     "Выставить счёт в МойКласс, прислать ссылку/реквизиты, поставить себе контроль поступления."),
    (("возраст", "лет", "года", "годик"),
     "Уточнить дату рождения, занести в карточку и подобрать группу по возрасту."),
    (("не буд", "не актуал", "отказ", "не пойд", "другой центр"),
     "Мягко уточнить причину, зафиксировать статус «Отказ» + причину в CRM, "
     "предложить остаться на связи и позвать на бесплатный праздник 29.08."),
    (("перенес", "не сможем", "заболел", "болеет"),
     "Предложить конкретные альтернативные даты и сразу перезаписать."),
]


def _draft_for(text: str) -> str:
    low = (text or "").lower()
    for keys, advice in DRAFT_RULES:
        if any(k in low for k in keys):
            return advice
    return ("Ответить по сути вопроса, затем следующий шаг: пригласить на условно-бесплатное первое занятие "
            "или на праздник 29.08. Сообщение должно заканчиваться вопросом.")


@app.get("/suggest", dependencies=AUTH)
def suggest_redirect():
    """Страница «Ответы» объединена с «Кто ждёт ответа» (/waiting)."""
    return RedirectResponse("/waiting", status_code=307)


def _suggest_page_legacy(request: Request, hours: int = 24):
    """Клиенты, которые написали и ждут ответа: что они спросили, что известно
    о клиенте и что ответить. Черновик проверяет админ, а не робот."""
    from . import autopilot
    since = (autopilot._now() - timedelta(hours=max(1, min(72, hours)))).isoformat(timespec="seconds")
    items = []
    with db.get_conn() as conn:
        try:
            inbox = conn.execute(
                "SELECT phone, MAX(ts) t, text FROM wazzup_inbox WHERE ts >= ? AND chat_type != 'manual' "
                "GROUP BY substr(phone,-10) ORDER BY t DESC", (since,)).fetchall()
            outbox = dict(conn.execute(
                "SELECT substr(phone,-10), MAX(ts) FROM wazzup_outbox GROUP BY substr(phone,-10)").fetchall())
        except Exception:
            inbox, outbox = [], {}
        for phone, ts, text in inbox:
            key = phone[-10:]
            answered = outbox.get(key, "") > ts
            row = conn.execute("SELECT id, name, raw FROM users WHERE substr(phone,-10)=? LIMIT 1",
                               (key,)).fetchone()
            uid, name, raw = row if row else (None, "", "")
            items.append({"phone": key, "name": name or "нет в CRM", "ts": ts,
                          "text": text or "", "answered": answered,
                          "age": _age_from_raw(raw) if raw else None,
                          "draft": _draft_for(text),
                          "brief": f"/brief?phone={key}",
                          "crm": f"https://app.moyklass.com/client/{uid}" if uid else ""})
    items.sort(key=lambda x: (x["answered"], x["ts"]), reverse=False)
    items.sort(key=lambda x: x["answered"])
    return render(request, "suggest.html", active="suggest", items=items, hours=hours,
                  waiting=len([x for x in items if not x["answered"]]))


# --- Mango: событие о звонке → мгновенная подсказка админу ------------------

def _mango_sign_ok(key: str, js: str, sign: str) -> bool:
    import hashlib
    salt = db.get_setting("mango_salt", "") or ""
    return hashlib.sha256((key + js + salt).encode()).hexdigest() == (sign or "")


def _brief_text(phone: str) -> str:
    """Короткая справка о клиенте — то, что успеет прочитать админ за гудки."""
    digits = "".join(ch for ch in phone if ch.isdigit())[-10:]
    with db.get_conn() as conn:
        row = conn.execute("SELECT id, name, raw FROM users WHERE substr(phone,-10)=? LIMIT 1",
                           (digits,)).fetchone()
        if not row:
            return (f"📞 Звонит +{phone} — в CRM карточки нет. Новый контакт: спросите имя "
                    f"и возраст ребёнка, заведите карточку.\napp.kidsup.ru/brief?phone={digits}")
        uid, name, raw = row
        age = _age_from_raw(raw)
        last = conn.execute("""SELECT co.name, MAX(l.date) FROM lesson_records lr
            JOIN lessons l ON l.id = lr.lesson_id
            LEFT JOIN classes cl ON cl.id = l.class_id
            LEFT JOIN courses co ON co.id = cl.course_id
            WHERE lr.user_id=? AND lr.visit=1""", (uid,)).fetchone()
        enrolled = conn.execute("SELECT COUNT(*) FROM joins j JOIN classes cl ON cl.id=j.class_id "
                                "WHERE j.user_id=? AND cl.name LIKE '2627%'", (uid,)).fetchone()[0]
    parts = [f"📞 Звонит {name} (+{phone})"]
    if age:
        parts.append(f"Возраст: {age} → {_suggest_by_age(age)}")
    if last and last[0]:
        parts.append(f"Посещал: {last[0]}, последнее занятие {last[1]}")
    parts.append("На 26/27: записан ✅" if enrolled else "На 26/27 НЕ записан — предложить место")
    parts.append(f"Подсказка: app.kidsup.ru/brief?phone={digits}")
    return "\n".join(parts)


@app.post("/mango/events")
@app.post("/mango/{rest:path}")
async def mango_events(request: Request, rest: str = ""):
    """Уведомления Mango о звонках.

    В ЛК Mango: Интеграции → API коннектор → Внешние системы → Добавить
    систему → адрес https://app.kidsup.ru/mango/ (со слешем на конце).
    Mango дописывает к адресу подпуть события: events/call, events/summary,
    events/record/added — поэтому ловим любой подпуть, а не один адрес.

    На входящий звонок шлём дежурному админу справку о клиенте, готовую
    запись разговора сразу ставим в очередь на расшифровку."""
    from . import autopilot, wazzup
    form = await request.form()
    js = form.get("json")
    if js is None:
        return {"ok": True}          # «Проверить подключение» шлёт пустой запрос
    if not _mango_sign_ok(form.get("vpbx_api_key") or "", js, form.get("sign") or ""):
        raise HTTPException(403, "подпись не сошлась")
    try:
        ev = json.loads(js)
    except ValueError:
        return {"ok": True}
    kind = (rest or "events").strip("/")
    # запись разговора готова — кладём в очередь, разберём в ближайшие минуты
    if ev.get("recording_id") or kind.endswith("record/added"):
        rid = ev.get("recording_id") or ""
        if rid:
            with db.get_conn() as conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS mango_recordings (
                    recording_id TEXT PRIMARY KEY, ts TEXT, done INTEGER DEFAULT 0)""")
                conn.execute("INSERT OR IGNORE INTO mango_recordings (recording_id, ts) "
                             "VALUES (?, ?)",
                             (rid, autopilot._now().isoformat(timespec="seconds")))
        return {"ok": True}
    state = (ev.get("call_state") or "").lower()
    frm = ((ev.get("from") or {}).get("number") or "")
    ext = ((ev.get("from") or {}).get("extension") or "")
    to_num = "".join(ch for ch in str((ev.get("to") or {}).get("number") or "") if ch.isdigit())
    phone = "".join(ch for ch in str(frm) if ch.isdigit())
    # журнал звонков: нужен, чтобы проверять «закрыта ли задача с результатом»
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS mango_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
            direction TEXT, state TEXT)""")
        conn.execute("INSERT INTO mango_calls (ts, phone, direction, state) VALUES (?,?,?,?)",
                     (autopilot._now().isoformat(timespec="seconds"),
                      (to_num if ext else phone)[-10:], "out" if ext else "in", state))
    if state not in ("appeared", "connected") or ext or len(phone) < 10:
        return {"ok": True}      # исходящие и служебные события пропускаем
    if not autopilot._mark("call_brief", f"{autopilot._today()}:{phone[-10:]}:{state}"):
        return {"ok": True}      # одна подсказка на номер и состояние в день
    try:
        phones = json.loads(db.get_setting("admin_phones") or "{}")
    except ValueError:
        phones = {}
    admins = autopilot._admins_today()
    target = ""
    if admins:
        target = phones.get(str(admins[0].get("managerId")), "")
    target = target or db.get_setting("digest_phone", "")
    if target:
        dry = db.get_setting("wazzup_dry_run", "1") == "1"
        try:
            wazzup.send_via("whatsapp", target, _brief_text(phone), dry_run=dry)
        except Exception:
            logging.getLogger("kidsup.mango").exception("подсказка не ушла")
    return {"ok": True}


@app.get("/brief", response_class=HTMLResponse, dependencies=AUTH)
def brief_page(request: Request, phone: str = ""):
    """Всё о клиенте на одном экране: кто это, что было, что предложить,
    какие группы свободны и что написать. Открывается по номеру телефона —
    перед звонком и во время переписки."""
    digits = "".join(ch for ch in phone if ch.isdigit())[-10:]
    client, history, dialog = None, [], []
    if digits:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT id, name, phone, client_state_id, raw FROM users "
                "WHERE substr(phone,-10)=? LIMIT 1", (digits,)).fetchone()
            if row:
                uid, name, ph, state, raw = row
                age = _age_from_raw(raw)
                history = [{"course": r[0], "last": r[1], "visits": r[2]} for r in conn.execute(
                    """SELECT co.name, MAX(l.date), COUNT(*) FROM lesson_records lr
                       JOIN lessons l ON l.id = lr.lesson_id
                       LEFT JOIN classes cl ON cl.id = l.class_id
                       LEFT JOIN courses co ON co.id = cl.course_id
                       WHERE lr.user_id = ? AND lr.visit = 1
                       GROUP BY co.name ORDER BY MAX(l.date) DESC LIMIT 6""", (uid,))]
                enrolled = conn.execute(
                    "SELECT COUNT(*) FROM joins j JOIN classes cl ON cl.id=j.class_id "
                    "WHERE j.user_id=? AND cl.name LIKE '2627%'", (uid,)).fetchone()[0]
                st = conn.execute("SELECT name FROM client_statuses WHERE id=?",
                                  (state,)).fetchone()
                client = {"id": uid, "name": name, "phone": ph, "age": age,
                          "status": st[0] if st else "—", "enrolled": enrolled,
                          "crm": f"https://app.moyklass.com/client/{uid}",
                          "suggest": _suggest_by_age(age)}
                try:
                    dialog = [{"ts": r[0], "dir": "in", "text": r[2]} for r in conn.execute(
                        "SELECT ts, phone, text FROM wazzup_inbox WHERE substr(phone,-10)=? "
                        "ORDER BY id DESC LIMIT 5", (digits,))]
                except Exception:
                    dialog = []
    # свободные группы под возраст
    groups = []
    if client and client["age"]:
        with db.get_conn() as conn:
            rows = conn.execute("""
                SELECT cl.id, cl.name, cl.max_students, co.name course,
                       COUNT(DISTINCT j.user_id) enrolled
                FROM classes cl LEFT JOIN courses co ON co.id = cl.course_id
                LEFT JOIN joins j ON j.class_id = cl.id
                WHERE cl.name LIKE '2627%' AND cl.name NOT LIKE '%аявк%'
                GROUP BY cl.id""").fetchall()
        for r in rows:
            lo, hi = _group_ages(r["name"])
            if lo is None or not (lo - 0.5 <= client["age"] <= hi + 0.5):
                continue
            cap, en = (r["max_students"] or 8), (r["enrolled"] or 0)
            if cap - en <= 0:
                continue
            pr = PRICES.get(r["course"] or "")
            groups.append({"name": r["name"], "course": r["course"],
                           "day": " · ".join(DAY_LABEL[d] for d in _name_days(r["name"])) or "—",
                           "time": " · ".join(_TIME_RE.findall(r["name"])[:2]) or "—",
                           "free": cap - en,
                           "price": pr["lines"][0][2] if pr else 0})
        groups.sort(key=lambda g: (g["course"], g["time"]))
    pitch = {}
    for g in groups[:3]:
        if g["course"] in PITCH and g["course"] not in pitch:
            pitch[g["course"]] = PITCH[g["course"]]
    return render(request, "brief.html", active="brief", phone=phone,
                  client=client, history=history, groups=groups[:12],
                  pitch=pitch, dialog=dialog)


@app.post("/api/callplan/done", dependencies=AUTH)
def callplan_done(payload: dict = Body(...)):
    """Кнопка «прозвонён» на /callplan: комментарий сразу в карточку МойКласс
    и локальная метка, чтобы админ видел, кого уже отработали сегодня."""
    uid = int(payload.get("user_id") or 0)
    outcome = (payload.get("outcome") or "").strip()[:300]
    if not uid or not outcome:
        raise HTTPException(422, "user_id и outcome обязательны")
    key = sync.get_api_key()
    if not key:
        raise HTTPException(400, "нет API-ключа МойКласс")
    from .moyklass_client import MoyklassClient
    c = MoyklassClient(key)
    try:
        c.post("/v1/company/userComments",
               {"userId": uid, "comment": f"📞 Прозвон (app.kidsup.ru): {outcome}",
                "showToUser": False})
    finally:
        c.close()
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS callplan_marks
                        (user_id INTEGER PRIMARY KEY, marked_at TEXT, outcome TEXT)""")
        conn.execute("INSERT OR REPLACE INTO callplan_marks VALUES (?, datetime('now'), ?)",
                     (uid, outcome))
        conn.commit()
    return {"ok": True}


@app.get("/callplan", response_class=HTMLResponse, dependencies=AUTH)
def callplan_page(request: Request, segment: str = "all", q: str = "",
                  done: int = 0, limit: int = 150):
    """Кого обзвонить: семьи прошлых лет, ещё не записанные в группы 2026/27."""
    with db.get_conn() as conn:
        # Запись в настоящую группу и «висит в буфере Заявок» — разные вещи.
        # Раньше буфер считался записью, и семья пропадала из обзвона, хотя
        # её ещё никто никуда не поставил.
        enrolled = {r[0] for r in conn.execute(
            "SELECT DISTINCT j.user_id FROM joins j JOIN classes cl ON cl.id = j.class_id "
            "WHERE cl.name LIKE '2627%' AND cl.name NOT LIKE '%Заявки%'")}
        in_buffer = {r[0] for r in conn.execute(
            "SELECT DISTINCT j.user_id FROM joins j JOIN classes cl ON cl.id = j.class_id "
            "WHERE cl.name LIKE '2627%' AND cl.name LIKE '%Заявки%'")}
        base = """SELECT lr.user_id u, MAX(l.date) last, co.name course FROM lesson_records lr
                  JOIN lessons l ON l.id = lr.lesson_id
                  LEFT JOIN classes cl ON cl.id = l.class_id
                  LEFT JOIN courses co ON co.id = cl.course_id
                  WHERE lr.visit = 1 AND l.date >= ? AND l.date < ?
                  GROUP BY lr.user_id, co.name"""
        ranges = {"summer": ("2026-06-01", "2026-12-31"),
                  "year": ("2025-09-01", "2026-06-01"),
                  "old": ("2024-01-01", "2025-09-01")}
        # «Все» — вся база разом: от самых давних до летних, без деления
        d1, d2 = ("2024-01-01", "2026-12-31") if segment == "all" \
            else ranges.get(segment, ranges["summer"])
        rows = conn.execute(base, (d1, d2)).fetchall()
        conn.execute("""CREATE TABLE IF NOT EXISTS callplan_marks
                        (user_id INTEGER PRIMARY KEY, marked_at TEXT, outcome TEXT)""")
        marks = {r[0]: r[1][:10] for r in conn.execute(
            "SELECT user_id, marked_at FROM callplan_marks").fetchall()}
        seen_all = {"summer": set(), "year": set(), "old": set()}
        for key, (a, b) in ranges.items():
            seen_all[key] = {r[0] for r in conn.execute(base, (a, b)).fetchall()}
        people: dict[int, dict] = {}
        for uid, last, course in rows:
            p = people.setdefault(uid, {"id": uid, "last": last, "courses": set()})
            p["last"] = max(p["last"], last)
            if course:
                p["courses"].add(course)
        # сегменты не пересекаются: год без летних, «давно» без первых двух
        if segment == "year":
            people = {k: v for k, v in people.items() if k not in seen_all["summer"]}
        if segment == "old":
            people = {k: v for k, v in people.items()
                      if k not in seen_all["summer"] and k not in seen_all["year"]}
        out = []
        for uid, p in people.items():
            row = conn.execute("SELECT name, phone, client_state_id, raw FROM users WHERE id=?",
                               (uid,)).fetchone()
            if not row:
                continue
            name, phone, state, raw = row
            if state in (125954, 125957, 146328, 215202, 146330, 146513):
                continue  # отказ / не звонить / не наш возраст
            is_done = uid in enrolled
            p["marked"] = marks.get(uid)
            if not done and is_done:
                continue
            if q:
                needle = " ".join(q.lower().split())
                digits = re.sub(r"\D", "", q)
                if needle not in " ".join((name or "").lower().split()) and not (
                        digits and digits in re.sub(r"\D", "", phone or "")):
                    continue
            age = _age_from_raw(raw)
            out.append({"id": uid, "name": name or "—", "phone": phone or "",
                        "age": age, "last": p["last"], "enrolled": is_done,
                        "buffer": uid in in_buffer,
                        "courses": ", ".join(sorted(p["courses"]))[:60],
                        "crm": f"https://app.moyklass.com/client/{uid}",
                        "suggest": _suggest_by_age(age)})
    # сначала не записанные, внутри — кто был у нас недавно
    out.sort(key=lambda x: (x["enrolled"], x["last"] or ""), reverse=True)
    out.sort(key=lambda x: x["enrolled"])
    total = len(out)
    return render(request, "callplan.html", active="callplan",
                  people=out[:max(10, min(500, limit))], total=total,
                  segment=segment, segments=CALL_SEGMENTS, q=q, done=done,
                  enrolled_n=len([x for x in out if x["enrolled"]]))


def _suggest_by_age(age: float | None) -> str:
    """Что предлагать по возрасту — матрица из методички."""
    if age is None:
        return "уточнить возраст"
    if age < 2:
        return "Музыка и речь · Первая школа"
    if age < 3:
        return "Мини-сад ГКП · Музыка и речь · логопед"
    if age < 4:
        return "Мини-сад ГКП · Лицей для малышей · логопед, ИЗО"
    if age < 7:
        return "Нулевой класс · Подготовка к школе · английский, шахматы, ИЗО"
    if age < 13:
        return "Английский · скорочтение, каллиграфия, менталка, шахматы, ИЗО"
    return "13+ — не наш возраст"


@app.get("/grid", response_class=HTMLResponse, dependencies=AUTH)
def grid_page(request: Request, course: int = 0, all: int = 0):
    from . import grid
    return render(request, "grid.html", active="grid",
                  g=grid.build(course or None, show_all=bool(all)),
                  course=course, show_all=bool(all))


@app.get("/promo", response_class=HTMLResponse, dependencies=AUTH)
def promo_page(request: Request):
    from . import promo
    return render(request, "promo.html", active="promo", s=promo.stats())


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
        for ddl in ("ALTER TABLE wazzup_outbox ADD COLUMN message_id TEXT",
                    "ALTER TABLE wazzup_outbox ADD COLUMN text TEXT"):
            try:
                conn.execute(ddl)
            except Exception:
                pass
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
        for ts, phone, chat_type, text, mid in rows:
            _match_click(conn, ts, phone, chat_type)


# --- переходы по кнопкам мессенджеров (номер Roistat) -----------------------
# WhatsApp несёт номер визита в тексте первого сообщения, Telegram-бот — в
# /start. У MAX параметра для этого нет: ссылку открывают «как есть». Поэтому
# клик по кнопке пишем к себе, а первое входящее из MAX сопоставляем с
# последним кликом по времени.

CLICK_MATCH_MINUTES = 20


def _clicks_init(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS messenger_clicks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, channel TEXT,
        roistat_visit TEXT, utm TEXT, referrer TEXT,
        matched_phone TEXT, matched_ts TEXT)""")


def _match_click(conn, ts: str, phone: str, chat_type: str) -> None:
    """Привязывает входящее сообщение к клику по кнопке мессенджера."""
    if chat_type not in ("max", "telegram"):
        return                      # у WhatsApp номер визита приходит в тексте
    _clicks_init(conn)
    seen = conn.execute(
        "SELECT 1 FROM wazzup_inbox WHERE substr(phone,-10)=? AND ts < ? LIMIT 1",
        ((phone or "")[-10:], ts)).fetchone()
    if seen:
        return                      # не первое сообщение — источник уже известен
    from . import autopilot
    border = (autopilot._now() - timedelta(
        minutes=CLICK_MATCH_MINUTES)).isoformat(timespec="seconds")
    row = conn.execute(
        "SELECT id FROM messenger_clicks WHERE channel=? AND matched_phone IS NULL "
        "AND ts >= ? ORDER BY ts DESC LIMIT 1", (chat_type, border)).fetchone()
    if row:
        conn.execute("UPDATE messenger_clicks SET matched_phone=?, matched_ts=? WHERE id=?",
                     (phone, ts, row[0]))


MESSENGER_LINKS = {
    "whatsapp": "https://wa.me/79165610077",
    "telegram": "https://t.me/KidsUP_ru",
    "max": "https://max.ru/u/f9LHodD0cOL7ouxX67LQufADpyAmbvMGRUdMqaGj2Ya-F1EuIVQMGWeU9gc",
}
WA_HELLO = ("Здравствуйте! Пожалуйста, отправьте это сообщение и дождитесь "
            "ответа. Ваш номер: ")


@app.get("/go/{channel}")
async def go_messenger(channel: str, request: Request):
    """Кнопка мессенджера на сайте ведёт сюда: фиксируем номер визита Roistat
    и переводим в мессенджер."""
    target = MESSENGER_LINKS.get(channel)
    if not target:
        raise HTTPException(404)
    q = request.query_params
    visit = (q.get("rv") or q.get("roistat_visit") or "")[:64]
    utm = json.dumps({k: v for k, v in q.items() if k.startswith("utm_")},
                     ensure_ascii=False)[:500]
    from . import autopilot
    with db.get_conn() as conn:
        _clicks_init(conn)
        conn.execute("INSERT INTO messenger_clicks (ts, channel, roistat_visit, utm, referrer) "
                     "VALUES (?, ?, ?, ?, ?)",
                     (autopilot._now().isoformat(timespec="seconds"), channel,
                      visit, utm, (request.headers.get("referer") or "")[:300]))
    if channel == "whatsapp" and visit:
        target += "?text=" + quote(WA_HELLO + visit)
    return RedirectResponse(target, status_code=302)


@app.get("/clicks", response_class=HTMLResponse, dependencies=AUTH)
def clicks_page(request: Request):
    """Клики по кнопкам мессенджеров и то, с каким обращением они склеились."""
    with db.get_conn() as conn:
        _clicks_init(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT ts, channel, roistat_visit, utm, matched_phone, matched_ts "
            "FROM messenger_clicks ORDER BY id DESC LIMIT 300")]
    return render(request, "clicks.html", active="clicks", rows=rows)



def _wazzup_status(payload: dict) -> None:
    """Статусы наших сообщений (sent → delivered → read). Нужны, чтобы видеть,
    сколько людей прочитали рассылку и промолчали."""
    from . import autopilot
    rows = []
    ts = autopilot._now().isoformat(timespec="seconds")
    for st in payload.get("statuses", []) or []:
        mid, status = str(st.get("messageId") or ""), (st.get("status") or "")
        if mid and status:
            rows.append((mid, status, ts))
    for m in payload.get("messages", []) or []:
        if m.get("isEcho") and m.get("messageId") and m.get("status"):
            rows.append((str(m["messageId"]), m["status"], ts))
    if not rows:
        return
    rank = {"sent": 1, "delivered": 2, "read": 3, "error": 0}
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS wazzup_status (
            message_id TEXT PRIMARY KEY, status TEXT, rank INTEGER, ts TEXT)""")
        for mid, status, t in rows:
            r = rank.get(status, 0)
            conn.execute(
                "INSERT INTO wazzup_status (message_id, status, rank, ts) VALUES (?,?,?,?) "
                "ON CONFLICT(message_id) DO UPDATE SET status=excluded.status, rank=excluded.rank, "
                "ts=excluded.ts WHERE excluded.rank > wazzup_status.rank",
                (mid, status, r, t))


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
        _wazzup_status(payload)
    except Exception:
        logging.getLogger("kidsup.wazzup").exception("статусы: не сохранились")
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


APP_VERSION = "2026-08-19.4"  # видно в /api/health — чтобы проверять, что обновление применилось


@app.get("/api/net")
async def api_net(request: Request):
    """Диагностика доступа: видно, дошёл ли запрос до приложения и с какого IP.
    Без авторизации — чтобы можно было проверить доступность из-под VPN."""
    import subprocess
    def sh(cmd: list[str]) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=8).stdout.strip()[:1500]
        except Exception as e:  # noqa: BLE001
            return f"{type(e).__name__}: {e}"
    peer = request.client.host if request.client else "?"
    fwd = request.headers.get("x-forwarded-for", "")
    return {
        "ok": True,
        "ваш_ip_как_видит_сервер": (fwd.split(",")[0].strip() or peer),
        "peer": peer,
        "host": request.headers.get("host", ""),
        "proto": request.headers.get("x-forwarded-proto", ""),
        "user_agent": request.headers.get("user-agent", "")[:120],
        "firewall": sh(["ufw", "status"]) or sh(["iptables", "-S"])[:800],
        "caddy_hosts": sh(["grep", "-E", "^[a-z0-9.]+ ", "/etc/caddy/Caddyfile"]),
    }


@app.get("/api/health")
async def health():
    from . import autopilot
    today = autopilot._today().isoformat()
    return {"ok": True, "version": APP_VERSION,
            "msk": autopilot._now().isoformat(timespec="seconds"),
            "morning_done": autopilot._has_mark("morning", today)}


SETTABLE = {"admin_schedule", "daily_tasks_per_admin", "broadcast_per_hour", "broadcast_transports",
            "wazzup_dry_run", "digest_phone", "autopilot", "missed_reject_attempts", "wa_daily_cap", "wa_per_hour", "wa_senders", "wa_caps",
            "broadcast_until", "call_admins", "chat_admin", "moyklass_group_url",
            "admin_phones", "team_extra_phones", "anthropic_api_key", "assistant_model", "anthropic_base_url",
            "vk_token", "vk_group_id", "tg_bot_token", "tg_channel", "mango_ext_admins",
            # разобранные записи разговоров: список recording_id, чтобы почасовой
            # разбор не написал в карточку один и тот же звонок дважды
            "calls_done"}


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
                not (n == "requirements.txt" or n in ("app", "docs")
                     or n.startswith("app/") or n.startswith("docs/")):
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
    if not campaign or not text or segment not in (
            "warm", "contin", "camp", "regular", "y2425", "camp_past", "funnel"):
        raise HTTPException(400, "нужны campaign, text и segment из списка")
    return autopilot.enqueue_broadcast(
        campaign, segment, text,
        include_active=bool(payload.get("include_active")),
        exclude_enrolled=bool(payload.get("exclude_enrolled")),
        exclude_campaigns=payload.get("exclude_campaigns") or None)


# --- публичные эндпоинты для сайта kidsup.ru (без Basic-auth) -------------
# Сайт живёт на другом домене, поэтому CORS «*». Ничего приватного здесь нет:
# schedule отдаёт то же, что видно в открытых материалах (свободные места),
# lead только принимает заявку. Всё пишущее — с honeypot и лимитом по IP.

_PUB_CORS = {"Access-Control-Allow-Origin": "*",
             "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
             "Access-Control-Allow-Headers": "Content-Type"}
_SCHED_CACHE: dict = {"ts": 0.0, "data": None}
_LEAD_HITS: dict[str, list[float]] = {}


@app.get("/api/public/schedule")
def public_schedule():
    """Расписание групп 2026/27 и свободные места — для сайта kidsup.ru."""
    import time as _t
    if _SCHED_CACHE["data"] is not None and _t.time() - _SCHED_CACHE["ts"] < 180:
        return JSONResponse(_SCHED_CACHE["data"], headers=_PUB_CORS)
    groups = [g for g in _enrollment_groups() if not g["buffer"]]
    free_by_course: dict[str, int] = {}
    sad_split = {"Мини-сад": 0, "Нулевой": 0}
    for g in groups:
        free_by_course[g["course"]] = free_by_course.get(g["course"], 0) + g["free"]
        for k in sad_split:
            if k in g["name"]:
                sad_split[k] += g["free"]
    by_key: dict[str, int] = {}
    for cc in descr_mod.COURSES:
        if cc["key"] == "minisad":
            by_key[cc["key"]] = sad_split["Мини-сад"]
        elif cc["key"] == "zeroclass":
            by_key[cc["key"]] = sad_split["Нулевой"]
        elif cc["course"] in free_by_course:
            by_key[cc["key"]] = free_by_course[cc["course"]]
    data = {"updated": datetime.now().isoformat(timespec="seconds"),
            "by_key": by_key,
            "groups": [{"name": g["name"], "course": g["course"], "day": g["day"],
                        "time": g["time"], "age": g["age"], "free": g["free"],
                        "capacity": g["capacity"]} for g in groups]}
    _SCHED_CACHE.update(ts=_t.time(), data=data)
    return JSONResponse(data, headers=_PUB_CORS)


@app.options("/api/public/lead")
def public_lead_options():
    return JSONResponse({"ok": True}, headers=_PUB_CORS)


def _lead_to_crm(lead: dict) -> None:
    """Фоновая доводка заявки: карточка в МойКласс + задача дежурному + ТГ."""
    from . import autopilot, wazzup
    from .moyklass_client import MoyklassClient
    log = logging.getLogger("kidsup.lead")
    phone, child = lead["phone"], lead.get("child") or ""
    mk = MoyklassClient(sync.get_api_key())
    try:
        uid = None
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT id, name FROM users WHERE substr(phone,-10)=? LIMIT 1",
                (phone[-10:],)).fetchone()
        if row:
            uid = row["id"]
        else:
            u = mk.post("/v1/company/users", {
                "name": child or "Заявка с сайта", "phone": phone})
            uid = (u or {}).get("id")
            if uid:
                try:
                    mk.post(f"/v1/company/users/{uid}/status",
                            {"statusId": 125951, "statusChangeReasonId": 313608})
                except Exception:
                    pass
        details = [f"🤖 Клод: 🌐 Заявка с нового сайта kidsup.ru",
                   f"Ребёнок: {child or '—'}" + (f", возраст {lead['age']}" if lead.get("age") else ""),
                   f"Направление: {lead.get('course') or 'не выбрано'}",
                   f"Телефон: +{phone}"]
        if lead.get("note"):
            details.append(f"Комментарий: {lead['note']}")
        if lead.get("roistat"):
            details.append(f"roistat_visit: {lead['roistat']} (для сквозной аналитики)")
        details.append("Правило: позвонить в течение 5 минут (скорость = конверсия).")
        if uid:
            mk.post("/v1/company/userComments",
                    {"userId": uid, "comment": "\n".join(details), "showToUser": False})
        admins = autopilot._admins_today() or autopilot._admins()
        duty = admins[autopilot._today().toordinal() % len(admins)] if admins else None
        if duty:
            body = (f"🤖 Клод: 🔥 НОВАЯ ЗАЯВКА с сайта — позвонить в течение 5 минут! "
                    f"{child or 'имя не указано'}"
                    + (f", {lead['age']}" if lead.get("age") else "")
                    + (f", {lead['course']}" if lead.get("course") else "")
                    + f", тел. +{phone}")[:250]
            autopilot._task(mk, duty["managerId"], uid, body)
            try:
                phones = json.loads(db.get_setting("admin_phones") or "{}")
                dphone = phones.get(str(duty["managerId"])) or db.get_setting("digest_phone")
                if dphone:
                    wazzup.send_via("tgapi", dphone,
                                    f"🔥 Заявка с сайта kidsup.ru: {child or 'имя не указано'}, "
                                    f"+{phone}" + (f", {lead['course']}" if lead.get("course") else "")
                                    + ". Задача уже в МойКласс — звоним в течение 5 минут! — Клод",
                                    dry_run=False)
            except Exception as e:
                log.warning("уведомление дежурному не ушло: %s", e)
        log.info("заявка с сайта: +%s → карточка %s", phone, uid or "не создана")
    finally:
        mk.close()


@app.post("/api/public/lead")
async def public_lead(request: Request):
    """Форма нового сайта kidsup.ru. Всегда отвечает ok (чтобы не подсказывать
    ботам), но honeypot и лимит по IP отсекают мусор до CRM."""
    import time as _t
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if (payload.get("website") or "").strip():   # honeypot-поле, люди его не видят
        return JSONResponse({"ok": True}, headers=_PUB_CORS)
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() \
        or (request.client.host if request.client else "?")
    now = _t.time()
    hits = [t for t in _LEAD_HITS.get(ip, []) if now - t < 600]
    if len(hits) >= 5:
        return JSONResponse({"ok": True}, headers=_PUB_CORS)
    hits.append(now)
    _LEAD_HITS[ip] = hits
    digits = "".join(ch for ch in str(payload.get("phone") or "") if ch.isdigit())
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":
        digits = "7" + digits
    if len(digits) != 11 or not digits.startswith("79"):
        return JSONResponse({"ok": False, "error": "Проверьте номер телефона"},
                            headers=_PUB_CORS)
    lead = {"phone": digits,
            "child": str(payload.get("name") or "").strip()[:80],
            "age": str(payload.get("age") or "").strip()[:20],
            "course": str(payload.get("course") or "").strip()[:80],
            "note": str(payload.get("note") or "").strip()[:300],
            "roistat": str(payload.get("roistat") or "").strip()[:64]}
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS site_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
            child TEXT, age TEXT, course TEXT, note TEXT, roistat TEXT, ip TEXT)""")
        conn.execute(
            "INSERT INTO site_leads (ts, phone, child, age, course, note, roistat, ip)"
            " VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?)",
            (lead["phone"], lead["child"], lead["age"], lead["course"],
             lead["note"], lead["roistat"], ip))
        conn.commit()
    import threading
    threading.Thread(target=lambda: _safe_lead(lead), daemon=True).start()
    return JSONResponse({"ok": True}, headers=_PUB_CORS)


def _safe_lead(lead: dict) -> None:
    try:
        _lead_to_crm(lead)
    except Exception:
        logging.getLogger("kidsup.lead").exception(
            "заявка +%s сохранена локально (site_leads), но не доехала до МойКласс",
            lead.get("phone"))


@app.get("/api/public/leads", dependencies=AUTH)
def public_leads_list(limit: int = 50):
    """Просмотр принятых с сайта заявок (для админки, с auth)."""
    with db.get_conn() as conn:
        try:
            rows = conn.execute(
                "SELECT ts, phone, child, age, course, note, roistat, ip "
                "FROM site_leads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        except Exception:
            return {"count": 0, "leads": []}
    return {"count": len(rows), "leads": [dict(r) for r in rows]}


@app.post("/api/broadcast/retext", dependencies=AUTH)
async def api_broadcast_retext(payload: dict):
    """Заменить текст у ещё не отправленных сообщений кампании (status=pending)."""
    campaign = (payload.get("campaign") or "").strip()
    text = (payload.get("text") or "").strip()
    if not campaign or not text:
        raise HTTPException(400, "нужны campaign и text")
    with db.get_conn() as conn:
        cur = conn.execute(
            "UPDATE broadcast_queue SET text=? WHERE campaign=? AND status='pending'",
            (text, campaign))
    return {"campaign": campaign, "updated": cur.rowcount}


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


@app.get("/api/broadcast/reads", dependencies=AUTH)
async def api_broadcast_reads(campaign: str = "camp_aug26"):
    """Сколько получателей рассылки прочитали сообщение и промолчали.
    Считаем по статусам Wazzup: read → прочитал, delivered → дошло, но не открыл."""
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS wazzup_status (
            message_id TEXT PRIMARY KEY, status TEXT, rank INTEGER, ts TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS wazzup_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
            message_id TEXT UNIQUE, text TEXT)""")
        for ddl in ("ALTER TABLE wazzup_outbox ADD COLUMN message_id TEXT",
                    "ALTER TABLE wazzup_outbox ADD COLUMN text TEXT"):
            try:
                conn.execute(ddl)
            except Exception:
                pass
        try:
            sent = [r[0] for r in conn.execute(
                "SELECT phone FROM broadcast_queue WHERE campaign=? AND status='sent'", (campaign,))]
            statuses = dict(conn.execute("""
                SELECT substr(o.phone,-10), MAX(s.rank) FROM wazzup_outbox o
                JOIN wazzup_status s ON s.message_id = o.message_id
                GROUP BY substr(o.phone,-10)""").fetchall())
            replied = {r[0] for r in conn.execute(
                "SELECT DISTINCT substr(phone,-10) FROM wazzup_inbox WHERE chat_type != 'manual'")}
        except Exception as e:
            return {"error": f"нет данных: {type(e).__name__}: {e}"}
    out = {"sent": len(sent), "read": 0, "delivered_not_read": 0,
           "no_status": 0, "replied": 0, "read_silent": 0}
    for ph in sent:
        key = ph[-10:]
        rank = statuses.get(key)
        has_reply = key in replied
        if has_reply:
            out["replied"] += 1
        if rank == 3:
            out["read"] += 1
            if not has_reply:
                out["read_silent"] += 1
        elif rank == 2:
            out["delivered_not_read"] += 1
        elif rank is None:
            out["no_status"] += 1
    out["hint"] = ("статусы собираются с 15.08 — по сообщениям, отправленным раньше, "
                   "данных о прочтении нет")
    return out


@app.get("/api/broadcast/audit-camp", dependencies=AUTH)
async def api_broadcast_audit_camp(campaign: str = "camp_aug26"):
    """Кому текст про «летний лагерь» ушёл (или уйдёт) не по факту."""
    from . import autopilot
    return autopilot.broadcast_audit_camp(campaign)


@app.post("/api/broadcast/prune-wrong-camp", dependencies=AUTH)
async def api_broadcast_prune_wrong_camp(payload: dict = None):
    """Снять с очереди получателей, которым текст про лагерь не по факту."""
    from . import autopilot
    return autopilot.broadcast_prune_wrong_camp(
        (payload or {}).get("campaign", "camp_aug26"))


@app.post("/api/broadcast/prune-active", dependencies=AUTH)
async def api_broadcast_prune(payload: dict = None):
    """Убрать из очереди семьи, которые занимаются у нас этим летом."""
    from . import autopilot
    return autopilot.broadcast_prune_active((payload or {}).get("campaign", "camp_aug26"))


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
    # значение расписания: один id или список — в смене может быть двое
    mids = mid if isinstance(mid, list) else ([mid] if mid else [])
    onduty = [a for a in admins if a.get("managerId") in mids]
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


MONEY_RX = re.compile(r"оплат|плат[ёе]ж|по карте|переве|сч[её]т|запис|прода|купит|абонемент|возврат", re.I)

# Кэш пропущенных: Mango stats с жёстким rate-limit, чаще раза в 5 минут не ходим
_MISSED_CACHE: dict = {"ts": 0.0, "data": None}


def _missed_inbound(hours: int = 48) -> list[dict]:
    """Входящие без ответа, по которым не перезвонили (и не написали в чат).

    Перезвон засчитан, если ПОСЛЕ пропущенного был отвеченный разговор с этим
    номером (в любую сторону). Честность к админам:
    - серия мгновенных отбоев АТС (перебор добавочных) склеивается в один
      «пропущенный» (кластер с паузой < 5 минут);
    - наши недозвоны считаются и ДО пропущенного (за 2 часа): если мы сами
      набирали клиента, это «взаимный недозвон» (mutual), а не «прозевали»."""
    import time as _time
    from . import autopilot, mango
    if _MISSED_CACHE["data"] is not None and _time.time() - _MISSED_CACHE["ts"] < 300:
        return _MISSED_CACHE["data"]
    now = autopilot._now()
    rows = mango.calls(now - timedelta(hours=hours), now)
    rows.sort(key=lambda r: r["start"])
    missed: dict[str, dict] = {}       # номер -> последний пропущенный
    for r in rows:
        num = (r["from_num"] if not r["from_ext"] else r["to_num"])[-10:]
        if not num.isdigit() or len(num) < 10:
            continue
        if not r["from_ext"] and not r["answer"]:
            m = missed.setdefault(num, {"ts": 0, "attempts": 0, "tried": 0,
                                        "ours_before": 0})
            # отбой АТС при переборе добавочных даёт десятки строк за минуту —
            # это ОДИН звонок клиента, а не N пропущенных
            if r["start"] - m["ts"] > 300:
                m["attempts"] += 1
            m["ts"] = max(m["ts"], r["start"])
    for r in rows:                      # закрываем перезвонами/дозвонами
        num = (r["from_num"] if not r["from_ext"] else r["to_num"])[-10:]
        m = missed.get(num)
        if not m:
            continue
        if r["start"] >= m["ts"]:
            if r["answer"]:
                missed.pop(num, None)
            elif r["from_ext"]:
                m["tried"] += 1
        elif r["from_ext"] and r["start"] > m["ts"] - 7200:
            # мы сами набирали клиента незадолго до его звонка:
            # он перезванивал на наш недозвон — взаимный недозвон
            m["ours_before"] += 1
    out = []
    answered_after = [(  # все дозвоны (в обе стороны): для закрытия по карточке
        (r["from_num"] if not r["from_ext"] else r["to_num"])[-10:], r["start"])
        for r in rows if r["answer"]]
    try:
        dismissed = json.loads(db.get_setting("missed_dismissed") or "{}")
    except Exception:
        dismissed = {}
    with db.get_conn() as conn:
        for num, m in missed.items():
            if float(dismissed.get(num, 0)) >= m["ts"]:
                continue  # админ закрыл строку кнопкой «отработано»
            row = conn.execute(
                "SELECT ts FROM wazzup_outbox WHERE substr(phone,-10)=? ORDER BY ts DESC LIMIT 1",
                (num,)).fetchone()
            if row:
                try:
                    if datetime.fromisoformat(row[0]).timestamp() > m["ts"]:
                        continue  # написали в чат после пропущенного
                except Exception:
                    pass
            # у семьи бывает несколько номеров в одной карточке: пропущенный
            # с одного, а дозвонились по другому — считаем закрытым
            user_row = conn.execute(
                "SELECT name, raw FROM users WHERE substr(phone,-10)=? "
                "OR raw LIKE '%' || ? || '%' LIMIT 1", (num, num)).fetchone()
            name = user_row[0] if user_row else ""
            if user_row and user_row[1]:
                card_nums = {d[-10:] for d in re.findall(r"7?9\d{9}", user_row[1])}
                card_nums.discard(num)
                if any(n in card_nums and ts_ >= m["ts"] for n, ts_ in answered_after):
                    continue
            ts = datetime.fromtimestamp(m["ts"], tz=now.tzinfo)
            out.append({"phone": num, "name": name,
                        "since": ts.isoformat(timespec="minutes"),
                        "wait_min": int((now - ts).total_seconds() // 60),
                        "attempts": m["attempts"], "tried": m["tried"],
                        "mutual": m["ours_before"] > 0 or m["tried"] > 0,
                        "ours": m["ours_before"] + m["tried"]})
    out.sort(key=lambda x: -x["wait_min"])
    _MISSED_CACHE.update(ts=_time.time(), data=out)
    return out


@app.post("/api/waiting/dismiss", dependencies=AUTH)
async def api_waiting_dismiss(payload: dict):
    """Кнопка «✓ отработано» на строке пропущенного: скрыть до нового звонка."""
    import time as _time
    num = "".join(ch for ch in (payload.get("phone") or "") if ch.isdigit())[-10:]
    if len(num) < 10:
        raise HTTPException(400, "нужен телефон")
    try:
        d = json.loads(db.get_setting("missed_dismissed") or "{}")
    except Exception:
        d = {}
    d[num] = _time.time()
    db.set_setting("missed_dismissed", json.dumps(d))
    _MISSED_CACHE["data"] = None
    return {"ok": True}


@app.get("/api/waiting", dependencies=AUTH)
async def api_waiting(min_minutes: int = 15):
    """Диалоги, где последнее слово за клиентом дольше min_minutes минут
    (за последние 2 дня). Для бейджа в шапке и страницы /waiting."""
    from . import autopilot
    now = autopilot._now()
    since = (now - timedelta(days=2)).isoformat(timespec="seconds")
    msgs: dict[str, list] = {}
    with db.get_conn() as conn:
        for table, direction in (("wazzup_inbox", "in"), ("wazzup_outbox", "out")):
            try:
                rows = conn.execute(
                    f"SELECT ts, phone, text FROM {table} WHERE ts >= ?",
                    (since,)).fetchall()
            except Exception:
                rows = []
            for ts, phone, text in rows:
                msgs.setdefault(phone[-10:], []).append(
                    {"ts": ts, "dir": direction, "text": text or ""})
        info = {}
        for phone in msgs:
            row = conn.execute(
                "SELECT id, name FROM users WHERE substr(phone,-10)=? LIMIT 1", (phone,)).fetchone()
            info[phone] = row if row else (None, "")
    waiting = []
    for p, m in msgs.items():
        m.sort(key=lambda x: x["ts"])
        last = m[-1]
        if last["dir"] != "in":
            continue
        try:
            last_ts = datetime.fromisoformat(last["ts"])
        except Exception:
            continue
        wait_min = int((now - last_ts).total_seconds() // 60)
        if wait_min < min_minutes:
            continue
        # хвост подряд идущих входящих — весь непрочитанный кусок
        tail = []
        for x in reversed(m):
            if x["dir"] != "in":
                break
            tail.append(x["text"])
        text = " · ".join(t for t in reversed(tail) if t)[:300]
        money = bool(MONEY_RX.search(text))
        uid, name = info.get(p, (None, ""))
        waiting.append({"phone": p, "name": name, "since": last["ts"],
                        "wait_min": wait_min, "text": text, "money": money,
                        "draft": _draft_for(text),
                        "brief": f"/brief?phone={p}",
                        "crm": f"https://app.moyklass.com/client/{uid}" if uid else ""})
    waiting.sort(key=lambda w: (not w["money"], -w["wait_min"]))
    try:
        calls_waiting = _missed_inbound()
    except Exception as exc:
        logging.getLogger(__name__).warning("missed-calls: %s", exc)
        calls_waiting = []
    return {"count": len(waiting),
            "money": sum(1 for w in waiting if w["money"]),
            "max_wait": max((w["wait_min"] for w in waiting), default=0),
            "items": waiting,
            "calls_count": len(calls_waiting),
            "calls": calls_waiting}


@app.get("/waiting", response_class=HTMLResponse, dependencies=AUTH)
async def waiting_page(request: Request):
    data = await api_waiting()
    return render(request, "waiting.html", active="waiting", w=data)


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
