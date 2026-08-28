"""KidsUp Analytics — выгрузка и аналитика данных из CRM МойКласс."""

import csv
import html
import io
import json
import logging
import re
import secrets
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse, PlainTextResponse
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


@app.middleware("http")
async def _public_hosts(request, call_next):
    """Боевые домены на этом же сервере.

    kidsup.ru отдаёт сайт прямо с корня; kidsupday.ru и kidsupweek.ru —
    те же страницы, но сразу на своём разделе (день открытых дверей и
    расписание). Пока DNS этих доменов смотрит на Tilda, middleware
    просто спит; в момент переключения записей всё уже готово — сайт
    поднимется без деплоя. Служебные пути (/api, /static, вебхуки) на
    публичных доменах работают как обычно — их зовёт сам сайт."""
    host = (request.headers.get("host") or "").split(":")[0].lower().lstrip("www.")
    path = request.url.path
    if host in ("kidsup.ru",):
        # Старые страницы Tilda не должны стать 404 после переезда: у них
        # есть позиции в поиске и живые ссылки в переписках. 301 ведёт на
        # соответствующий раздел новой главной.
        TILDA_301 = {
            "/english": "/#courses", "/schoolpreparation1": "/#courses",
            "/schoolpreparation2": "/#courses", "/preschooluniversity": "/#courses",
            "/precocity1": "/#courses", "/precocity2": "/#courses",
            "/chess": "/#courses", "/drawing": "/#courses",
            "/arithmetic": "/#courses", "/speechtherapist": "/#courses",
            "/robotics": "/#courses", "/music": "/#courses",
            "/calligraphy": "/#courses", "/fastreading": "/#courses",
            "/brainstorm": "/#courses", "/dancing": "/#courses",
            "/celebration": "/#events", "/table": "/#schedule",
            "/contacts": "/#contact", "/o": "/static/oferta.html",
            "/privacy": "/static/privacy.html",
        }
        if path in TILDA_301:
            from fastapi.responses import RedirectResponse
            return RedirectResponse("https://kidsup.ru" + TILDA_301[path],
                                    status_code=301)
    if host in ("kidsup.ru",) and path == "/":
        # HTMLResponse, а не FileResponse: FileResponse из middleware на
        # этом стеке отдавал заголовки без тела (0 байт) — читаем сами
        html_text = (BASE / "static" / "site.html").read_text(encoding="utf-8")
        return HTMLResponse(html_text)
    if host == "kidsupday.ru" and not path.startswith(("/api", "/static", "/go", "/wazzup", "/hook")):
        from fastapi.responses import RedirectResponse
        # 27.08: СМС про праздник 29.08 зовёт на KidsUPday.ru — ведём на блок
        # событий (праздник + ДОД + неделя уроков), а не только на ДОД.
        return RedirectResponse("https://kidsup.ru/#events", status_code=301)
    if host == "kidsupweek.ru" and not path.startswith(("/api", "/static", "/go", "/wazzup", "/hook")):
        from fastapi.responses import RedirectResponse
        return RedirectResponse("https://kidsup.ru/#schedule", status_code=301)
    return await call_next(request)

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

PUBLIC_HOSTS = {
    # Домен → какой публичной странице отвечает его корень. Когда DNS
    # kidsup.ru переедет на этот сервер, посетитель получит сайт, а не
    # окно авторизации; служебный app.kidsup.ru остаётся под паролем.
    "kidsup.ru": "site", "www.kidsup.ru": "site",
    "kidsupday.ru": "day", "www.kidsupday.ru": "day",
    "kidsupweek.ru": "week", "www.kidsupweek.ru": "week",
}


@app.get("/", response_class=HTMLResponse)
def root(request: Request,
         credentials: HTTPBasicCredentials | None = Depends(_security)):
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host") or "").split(":")[0].lower()
    page = PUBLIC_HOSTS.get(host)
    if page == "site":
        return await_page("site")
    if page == "day":
        return await_page("day")
    if page == "week":
        return await_page("week")
    check_auth(credentials)          # служебный хост — как раньше, под паролем
    return dashboard(request)


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


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots(request: Request):
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host") or "kidsup.ru").split(":")[0]
    if host.startswith("app."):
        return PlainTextResponse("User-agent: *\nDisallow: /\n")
    return PlainTextResponse(
        f"User-agent: *\nAllow: /\nSitemap: https://{host}/sitemap.xml\n")


@app.get("/sitemap.xml")
def sitemap(request: Request):
    from fastapi.responses import Response
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host") or "kidsup.ru").split(":")[0]
    urls = "".join(f"<url><loc>https://{host}/{u}</loc></url>"
                   for u in ("",))
    return Response(content=('<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + urls + "</urlset>"), media_type="application/xml")


@app.get("/favicon.ico")
def favicon():
    from fastapi.responses import FileResponse
    return FileResponse(BASE / "static" / "logo_color.png",
                        media_type="image/png")


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
            LEFT JOIN joins j ON j.class_id = cl.id AND j.status_id NOT IN (1, 4)
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
            LEFT JOIN joins j ON j.class_id = cl.id AND j.status_id NOT IN (1, 4)
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
        if cc["key"] in ("minisad", "zeroclass", "dance", "choreography",
                         "football", "martial", "acrobatics", "acting", "speech"):
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
            LEFT JOIN joins j ON j.class_id = cl.id AND j.status_id NOT IN (1, 4)
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
    ("Каждый день на смене", [
        ("__url:/zayavki", "📋 Листы заявок — кому звонить",
         "Все открытые заявки сезона поимённо с телефонами и кнопками: "
         "действующие курсы со скриптом записи, будущие (робототехника, "
         "танцы, скорочтение) со скриптом-мостом. Собирается при открытии"),
        ("plan341", "🎯 План «341 к 30 сентября» — по шагам",
         "Детальный план заполнения групп: решения владельца, спринт дедлайна "
         "цен 28–31.08, конвейер праздника и ДОД, сетка под спрос, сентябрь. "
         "С чек-боксами — отмечайте сделанное"),
        ("__url:/ochered", "☎️ Очередь на сегодня — звонить отсюда",
         "Список задач дежурного с телефонами и кнопками итога: записан, перезвонить, не актуально, не дозвонились. Отметка закрывает задачу и пишет разговор в карточку — отдельный лист обзвона больше не нужен"),
        ("__url:/perepiska", "💬 Переписка: что спросили и что мы ответили",
         "Все диалоги без ответа за день. Зелёным — то, на что отвечаю сам (расписание, цены, адрес) с точными цифрами из прайса и CRM; серым — то, что уходит администратору: оплаты, переносы, жалобы. Страница ничего не отправляет, пока не нажать кнопку"),
        ("__url:/nedozvony", "📵 Недозвоны сегодня — вечерний прозвон",
         "Живой список: кому сегодня не дозвонились и кто не ответил на сообщение-догон. Строится в момент открытия"),
        ("__url:/zapolnyaemost", "📊 Заполняемость групп 2026/27",
         "Все группы сезона против плана, самые пустые сверху, горящие подсвечены. По ним собираются приоритеты обзвона"),
        ("bonusy_raschet", "💰 Рейтинг админов и расчёт бонусов",
         "Записи, дошедшие до пробного и оплаты по каждому админу с 17.08 "
         "с начислением по ставкам владельца. Пробные начинаются 31.08 — "
         "до этой даты бонус идёт только за оплаты"),
        ("__url:/zadachi-lizy", "🗂 Задачи Лизы: проверка на актуальность",
         "Каждая открытая задача проверена по звонкам, переписке, "
         "комментариям и записям: что уже сделано и можно закрыть, "
         "что проглядеть глазами, что делать. Считается при открытии"),
        ("zadachi_lizy", "🗂 Задачи Лизы: что срочно, что подождёт",
         "288 открытых задач, разложенных не по дате, а по тому, что мы "
         "теряем, если не сделать. Наверху 50 человек, которые написали "
         "нам и ждут ответа. Задачи, снятые автоматикой, из списка убраны"),
        ("ostatok_ira", "⏳ Ира — хвост вчерашнего листа",
         "Кого из вчерашнего листа так и не набрали ни разу. "
         "Строки те же, что вчера: имя, возраст, куда звать"),
        ("ostatok_lena", "⏳ Лена — хвост вчерашнего листа",
         "Кого из вчерашнего листа так и не набрали ни разу"),
        ("ostatok_anya", "⏳ Аня — хвост вчерашнего листа",
         "Мини-сад и нулевой класс: кого вчера не набрали"),
        ("zayavki_svezhie", "🔥 Заявки с сайта без ответа — срочно",
         "10 человек оставили заявку с 1 августа и не получили от нас "
         "ни разговора, ни записи. У пяти нет карточки в CRM. Свежие "
         "сверху: вчерашняя заявка дороже трёхнедельной"),
        ("zayavki_leto", "🌐 Заявки с сайта этим летом — обзвон",
         "77 человек оставляли заявку с июня 2026 и не записаны на год. "
         "11 из них вообще нет в CRM — карточки заводятся при разговоре. "
         "Видно, что оставляли и когда"),
        ("shablony_wazzup", "💬 Шаблоны Wazzup: быстрые ответы админов",
         "27 готовых текстов с нумерацией по разделам: возраст, предметы, организационное, события, ответы на сомнения. Цены из единого прайса. Скопировать в кабинет Wazzup, старые пометить «архив»"),
        ("blanki_obzvona", "🖨 Бланки для обзвона: расписание, цены, контакты",
         "Печатать в альбомной ориентации. Сетка день × время по каждому предмету со свободными клетками под имена, лист цен из CRM и ответы на частые вопросы. Для Надежды и для работы без компьютера"),
        ("list_lena", "📋 Лист Лены — доб. 12 (Катя и Инга)",
         "100 семей: занимались у Кати или Инги на подготовке, плюс добор из возрастного листа. Те, с кем говорили накануне, вычтены"),
        ("list_irina", "📋 Лист Ирины — доб. 10 (английский 7-12)",
         "90 семей: английский, ребёнку 7-12 лет к 1 сентября, платили за последние два года, на новый год не записаны. Свежие сверху"),
        ("list_anya_sad", "📋 Лист Ани — мини-сад и нулевой класс",
         "57 семей: все, кто когда-либо ходил, пробовал или оставлял заявку на мини-сад либо нулевой класс и не записан туда на 2026/27. Малыши сверху, у каждого — куда звать по возрасту"),
        ("spisok_a", "🔥 Список A — ходили этим летом 2026",
         "82 семьи: были у нас месяц-два назад, помнят педагогов. "
         "Видно, что посещал, когда перестал ходить и что предлагать. "
         "Только те, кому не звонили с 17 августа"),
        ("spisok_b", "📗 Список B — ходили в 2025/26 учебном году",
         "163 семьи: занимались весь прошлый год и просто не продлили. "
         "Разговор простой — «продолжаем?». Конверсия вдвое выше холодных"),
        ("anketa_prazdnik", "🎁 Анкета праздника 29.08 — печать",
         "Двусторонняя А4: лицо — анкета-билет беспроигрышной лотереи "
         "(контакты, интересы, согласие на связь), оборот — карта занятий "
         "по возрастам. Печатать 300 шт., после праздника — Лизе в CRM"),
        ("spisok_ira", "📞 Ира — лист «ни разу не звонили» (28.08)",
         "Пересобран 28.08 по всем звонкам с 16.08 со всех трубок: без "
         "дублей, без уже записанных, без тех, кому уже звонили. Сначала "
         "лагерь-2026, дальше учебный год"),
        ("spisok_anya", "📞 Аня — лист «ни разу не звонили» (28.08)",
         "Пересобран 28.08: только нетронутые семьи прошлого учебного "
         "года, один номер — одна строка"),
        ("spisok_lena", "📞 Лена — прежний лист (устарел)",
         "Заменён листами Иры и Ани от 28.08 — в старом были дубли и уже "
         "прозвоненные"),
        ("spisok_b_lena", "📗 Список B — половина Лены",
         "Чётные строки списка B: Лена звонит только по ним, Ира — по своей "
         "половине. Деление не плывёт при пересчёте"),
        ("spisok_b_ira", "📗 Список B — половина Иры",
         "Нечётные строки списка B: половина Иры. Вторая половина — у Лены"),
        ("spisok_c", "📘 Список C — 2024/25 и лето 2025",
         "299 семей: год и больше не были. Нужен повод вернуться — "
         "новый педагог, новое направление, другое расписание"),
        ("list_ira_4_7", "📋 Ира — дети 4–7 лет: подготовка к школе",
         "90 семей: платили нам полный месяц или неделю лагеря, ребёнку "
         "4–7 лет к 1 сентября, на новый год не записаны. Бьёт прямо "
         "в подготовку к школе, где свободны 88 мест из 120. Свежие деньги сверху"),
        ("list_lena_7_12", "📋 Лена — дети 7–12 лет: английский и кружки",
         "155 семей того же отбора, но школьного возраста: английский "
         "(46 свободных мест из 64), шахматы, ИЗО, ментальная арифметика. "
         "Свежие деньги сверху"),
        ("obzvon_vozrast", "🎯 Обзвон по возрастам: кто у нас платил",
         "370 семей: полный абонемент на месяц в 2025/26 или неделя лагеря этим летом, на новый год не записаны. По возрастным блокам, у каждого — что посещал, куда звать в первую очередь и куда во вторую. Место для записи от руки, печатать в альбомной"),
    ]),
    ("Набор 2026/27", [
        ("megaplan", "🎯 Мега-план набора до 30 сентября",
         "Всё в одном месте: честная математика по трём сценариям, что уже работает и что берём из марафона, календарь по неделям и числа, по которым видно, работает ли план"),
        ("strategiya_nabora", "🧭 Стратегия набора до 30 сентября",
         "Где на самом деле лежат 332 места: база 367 семей, промо, входящие. Четыре фазы, чего сознательно не делаем и по каким цифрам себя проверяем"),
        ("sobytiya_29_31", "🎪 Три события 29–31.08: чек-листы и скрипты",
         "Праздник, ДОД и Неделя уроков по шагам: маршрут гостя, скрипты педагога и администратора после занятия, анкета, разбор возражений"),
        ("kontent_plan", "📱 Контент-план соцсетей: готовые тексты",
         "Что публиковать каждый день до конца сентября — с готовыми текстами постов и сторис. Цифры о свободных местах подставляются из CRM, продающий пост не чаще одного из пяти"),
        ("skripty_nabora", "📞 Скрипты набора: заявка, холодная база, возврат",
         "Три главных разговора со схемой касаний и возражениями — по материалам марафона"),
        ("prodazhi_vozrazheniya", "🎯 Продажи и возражения: схема разговора",
         "5 этапов, отработка «дорого/подумаю/сравним», цепочки для замолчавших, welcome-серия"),
        ("tri_scenariya", "🧭 Три сценария админа: промо, входящие, база",
         "Что делать в каждом из трёх случаев — по шагам, со статусами и фразами"),
        ("pedagog_probnoe", "🎓 Педагог на пробном: девять шагов",
         "Урок 7 интенсива под нас: что сделать до занятия, девять шагов, запрещённые фразы, что говорить, если ребёнок не тянет или плачет"),
        ("zapis_v_gruppy", "📝 Запись в группы: корзина, потом день",
         "Почему сначала предмет и уровень, и только потом расписание"),
        ("predmety_shpargalka", "🎓 Предметы: что держать в голове",
         "Шпаргалка админа по каждому направлению: одна фраза ради «да», три факта, живые свободные места и цены"),
        ("bonusy_adminov", "💰 Бонусы администратора: ставки и расчёт ЗП",
         "Почему такие ставки, сколько выйдет по месяцам при графике 4/3 — с сентября по май"),
        ("promo_vvod", "🪧 Заведение промо-контакта",
         "Алгоритм Бориса: дубль → источник → тег → статус"),
    ]),
    ("Команде и родителям", [
        ("pravila_kidsup", "📋 Правила посещения KidsUP",
         "Для ресепшена и старт-пакета: быт, запись, оплата, скидки, отработка и заморозка. Плюс как это оформлять в МойКласс, чтобы правило работало"),
        ("karta_kabinetov", "🚪 Карта кабинетов",
         "Кто где занимается: предмет × день × комната. Печатать на ресепшен"),
        ("blank_diagnostiki", "📝 Бланк диагностики на первом занятии",
         "Для педагогов, печатать к 31.08: фиксация стартового уровня — без неё гарантия чтения не действует"),
        ("blank_roditelya", "🧭 Анкета родителя на событие",
         "Три вопроса на одну страницу для праздника 29.08 и ДОД 30.08, плюс инструкция админу — как читать ответы"),
        ("progress_detei", "📈 Прогресс ребёнка: система отчётности",
         "Вехи вместо оценок по ПкШ, английскому, саду и нулевому классу; ежедневные сводки, месячные отчёты, порядок запуска"),
    ]),
    ("Владельцу", [
        ("reestr_del", "✅ Реестр важных и срочных дел",
         "Все дела команды с фильтрами по людям и срочности"),
        ("it_zadachi_borisa", "🔑 ИТ-задачи владельца: доступы и решения",
         "18 задач Бориса по всем системам и рекламе — с приоритетом, временем и что я делаю после"),
        ("vozvraty_dolgi", "📒 Возвраты и долги: рабочая сводка",
         "Неоплаченные счета и должники — для обзвона Лизой"),
        ("vozvraty_statistika", "💸 Статистика возвратов",
         "Причины и суммы возвратов с марта, собрано Лизой 18.08"),
        ("domeny_sayta", "🌐 Как подключить новый сайт к доменам",
         "Что сейчас на kidsup.ru, kidsupday.ru и kidsupweek.ru, три способа переключения и какой для какого домена подходит. С предупреждением про ссылки в шаблонах рассылки"),
        ("waba_podklyuchenie", "🟢 Подключение WABA на номер 0918",
         "Пошагово: что меняется необратимо, порядок, чтобы рассылка не встала, четыре шаблона для Meta и как связать с новым сайтом"),
        ("kontrol_sistem", "👁 Глаза и уши: что я вижу в каждой системе",
         "Карта покрытия по МойКласс, Mango, Wazzup и сайту, десять схем увода денег и что докручиваем"),
        ("chto_ya_delayu", "🤖 Что я делаю сам и что контролирую",
         "Карта по часам: что идёт без касания, что по сигналу Бориса, чего не делаю принципиально — с обоснованием каждого решения"),
        ("podklyuchenie_modeli", "🔌 Подключение модели: пошагово",
         "Как включить разбор смыслом вместо поиска по словам: ключ Anthropic, прокси на Cloudflare и три настройки. Один раз, около 20 минут"),
        ("smm_zhurnal", "📮 Контент до 30 сентября",
         "СММ-журнал Полины, переложенный на KidsUP: шесть недель, готовые тексты, пиар-задания"),
    ]),
    ("Архив — устаревшее и разовые разборы", [
        ("list_burakov", "📋 Лист Клуба Буракова — доб. 20 (больше не звонит)",
         "С 25 августа администратор Клуба Буракова обзвон не ведёт — "
         "решение владельца. Лист оставлен для истории: его семьи "
         "перераспределены по возрастным листам Иры и Лены"),
        ("afisha_vystuplenie", "📣 Афиша выступления «ИИ-сотрудник в детском центре»",
         "Превью доклада для анонса: тема, цифры-крючки, что будет на выступлении. Слайды — /static/slides_ai_kidsup.pptx"),
        ("zadachi_kak_ispravil", "🗂 Задачи в МойКласс: что изменилось",
         "Для Иры, Ани, Лены и Лизы: почему списки не работали, где я ошибся, как теперь устроен день и почему так лучше"),
        ("zadachi_lizy", "🗂 Задачи Лизы: что делать, а что закрыть",
         "365 открытых задач разложены по действию: деньги, ждут ответа, заявки, порядок в CRM и то, что можно закрыть"),
        ("plan_nabora_21_30", "📆 План набора 21–30 августа по сменам",
         "Стратегия, наложенная на график: волны обзвона, роли по силам, пик 26-27, день подтверждений 28-го и узкое место событий 29-30"),
        ("mega_plan", "🎯 Мега-план набора до 30.09",
         "Интерактивный план: цифры, роли, воронка, грабли. Галочки сохраняются"),
        ("promo_status", "📋 Промо-контакты: статус по каждому",
         "Каждый контакт с листов: карточка, статус, кто и сколько звонил (срез 18.08)"),
        ("napisat_spisok", "✍️ Написать: звонки не помогли",
         "Семьи, до которых не дозвонились. Одна задача вместо десяти одинаковых: открыть переписку и предложить занятие"),
        ("tri_ocheredi", "🔍 Три очереди: почему задачи закрываются без касания",
         "Разбор по данным 22.08: работа идёт по трём спискам сразу — задачи в CRM, страницы обзвона и лист промоутера, — а отчитываться надо по одному. 35 из 54 звонков за смену не видны ни в одном списке"),
        ("obzvon_nadezhda", "📞 Обзвон для Надежды: возраст подготовки к школе",
         "136 семей: ходили у нас в прошлом учебном году или были этим летом, ребёнку к 1 сентября 4–7 лет, на новый сезон никуда не записаны. Тёплые первыми, у каждого — что было у нас и когда. Печатать в альбомной ориентации, итог разговора вписывается от руки"),
        ("obzvon_psh", "🎯 Обзвон ПШ: лист владельца на 22–23.08",
         "238 семей, которые уже занимались у нас в подготовке к школе и на новый сезон никуда не записаны: 103 целевого возраста на выходные, остальные отдельной волной. Скрипт разговора, ссылки на карточки и отметка результата"),
        ("obzvon_21_08", "📞 Обзвон 21.08: ПШ и английский",
         "156 самых тёплых из базы 2025/26 и лета — поимённо, с телефонами, возрастом и что предлагать. Лена и Ира — лето, Аня — апрель-май"),
        ("utro_20_08", "🔧 Утро четверга: 12 хвостов со вторника",
         "Что чиним до звонков: «записаны», но не в группе; непрозвоненные промо-контакты; дубли"),
        ("razbor_lena", "🎧 Четыре звонка Лены: разбор с записями",
         "Для Ани и Иры: как довести незнакомого до записи с датой, как удержать семью при смене расписания, как вернуть из архива, как свернуть невовремя"),
        ("algoritm_obzvona", "📞 Обзвон базы: пошагово",
         "Главная инструкция дня: с чего начать, что говорить, что нажать в CRM"),
        ("metodichka_v3", "Методичка администратора",
         "Как вести смену: приветствие, запись, оплата, конфликты"),
        ("skripty_v3", "Скрипты обзвона и переписки",
         "Что говорить по каждому поводу — дословно"),
        ("resepshen_v2", "Ресепшен-листы",
         "Печатные листы на стойку: цены, расписание, ответы"),
        ("brify_adminov", "Брифы администраторов",
         "Короткие памятки по каждому предмету"),
        ("plan_nabora_v2", "План набора (актуальный)",
         "Цель 523 места, сегменты, сроки, ответственные"),
        ("operativka_13_17", "Оперативный план недели",
         "Что делаем по дням"),
        ("plan_del_borisa", "План дел Бориса",
         "Что зависит только от собственника"),
        ("kontent_plan_13_30", "Контент-план",
         "Посты и сторис по дням"),
        ("listovki_promoutery", "Листовки и промоутеры",
         "Тексты листовок, механика сбора контактов"),
        ("audit_saitov", "Аудит сайтов",
         "Что чинить на kidsup.ru"),
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
            # «__url:/путь» — не методичка, а живая страница портала:
            # очередь дня собирается из задач и файла в docs не имеет
            if slug.startswith("__url:"):
                rows.append({"slug": slug, "href": slug[6:], "name": name,
                             "note": note, "size": 0})
                continue
            f = root / f"{slug}.html"
            if f.exists():
                rows.append({"slug": slug, "href": f"/base/{slug}", "name": name,
                             "note": note, "size": f.stat().st_size // 1024})
        if rows:
            groups.append({"title": title, "items": rows})
    return render(request, "base_docs.html", active="base", groups=groups)



def _page_cache(key: str, ttl_min: int, builder, fresh: bool = False) -> str:
    """Кэш тяжёлых страниц (/nedozvony, /zayavki собираются 30-60 сек:
    Mango и МойКласс медленные). Свежий кэш отдаётся мгновенно; протухший
    пересобирается в этом же запросе (первый посетитель ждёт, остальные нет).
    ?fresh=1 — пересобрать принудительно."""
    import json as _j
    from datetime import datetime as _dt
    now = _dt.now()
    if not fresh:
        try:
            c = _j.loads(db.get_setting(f"cache_{key}") or "{}")
            ts = _dt.fromisoformat(c["ts"])
            if (now - ts).total_seconds() < ttl_min * 60 and c.get("html"):
                return c["html"].replace("<!--cache-note-->",
                    f"<p style='color:#8A8A9E;font-size:.8rem'>данные на {ts.strftime('%H:%M')} · "
                    f"<a href='?fresh=1'>обновить сейчас</a></p>")
        except Exception:
            pass
    html_text = builder()
    try:
        db.set_setting(f"cache_{key}", _j.dumps(
            {"ts": now.isoformat(), "html": html_text}))
    except Exception:
        pass
    return html_text.replace("<!--cache-note-->", "")


@app.get("/nedozvony", response_class=HTMLResponse, dependencies=AUTH)
def nedozvony_page(fresh: int = 0):
    def _build():
        r = _nedozvony_build()
        return r.body.decode() if hasattr(r, "body") else str(r)
    return HTMLResponse(_page_cache("nedozvony", 10, _build, fresh=bool(fresh)))


def _nedozvony_build():
    """Живой список сегодняшних недозвонов для вечернего прозвона.

    Бумажный лист устаревает за день (24.08 семья получила третий звонок,
    потому что люберецкий лист печатался накануне). Эта страница строится
    в момент открытия: те, кому не дозвонились сегодня, МИНУС те, кто уже
    ответил на сообщение-догон, минус отказы и «не писать»."""
    import html as _h
    from . import mango
    try:
        missed = mango.missed()
    except Exception as e:
        return HTMLResponse(f"<p>Манго недоступен: {_h.escape(str(e)[:120])}</p>")
    replied = set()
    with db.get_conn() as conn:
        try:
            for (ph,) in conn.execute(
                    "SELECT DISTINCT substr(phone,-10) FROM wazzup_inbox "
                    "WHERE date(ts)=date('now') AND chat_type!='manual'"):
                replied.add(ph)
        except Exception:
            pass
        names, states = {}, {}
        for uid, nm, phone, st in conn.execute(
                "SELECT id, name, phone, client_state_id FROM users WHERE phone IS NOT NULL"):
            p10 = "".join(ch for ch in str(phone) if ch.isdigit())[-10:]
            if len(p10) == 10 and p10 not in names:
                names[p10], states[p10] = (nm or ""), st
    rows = []
    for m in sorted(missed, key=lambda x: -x["attempts"]):
        p = m["phone"][-10:]
        if p in replied or states.get(p) in (125957, 146328, 125954):
            continue
        rows.append(f"<tr><td>{_h.escape((names.get(p) or '—')[:34])}</td>"
                    f"<td class=ph>+7{p}</td><td class=at>{m['attempts']}</td>"
                    f"<td class=res></td></tr>")
    body = f"""<style>
    body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:14px;color:#222}}
    h1{{font-size:19px;margin:0 0 4px}} .sub{{color:#666;font-size:13px;margin-bottom:10px}}
    table{{border-collapse:collapse;width:100%}}
    th{{background:#312783;color:#fff;font-size:12px;padding:6px;text-align:left}}
    td{{border-bottom:1px solid #ddd;padding:7px 6px;font-size:14px}}
    .ph{{font-weight:600;white-space:nowrap}} .at{{text-align:center}}
    .res{{width:200px;border-bottom:1px solid #999}}</style>
    <h1>Вечерний прозвон: сегодняшние недозвоны</h1>
    <div class=sub>{len(rows)} номеров. Кто уже ответил на сообщение-догон,
    отказники и «не писать» — убраны. Страница живая: обновите перед звонками.
    Больше всего попыток — сверху.</div>
    <table><tr><th>Кто</th><th>Телефон</th><th>Попыток</th><th>Итог</th></tr>
    {''.join(rows)}</table>"""
    return HTMLResponse(body)


@app.get("/api/perepiska", dependencies=AUTH)
def perepiska_report(day: str | None = None, send: int = 0):
    """Отчёт по переписке машинным форматом. Человеку — /perepiska."""
    from . import perepiska as P
    return P.run(day=day, dry=not send)


@app.get("/perepiska", response_class=HTMLResponse, dependencies=AUTH)
def perepiska_page(request: Request, day: str | None = None, send: int = 0):
    """Что спросили в переписке и что мы ответили — читаемой страницей.

    Открывается без параметров и НИЧЕГО не отправляет: сначала владелец
    смотрит тексты, и только ссылка «отправить» пускает их клиентам."""
    from . import perepiska as P
    import html as _h
    r = P.run(day=day, dry=not send)
    rows = []
    for x in r["строки"]:
        mine = x["action"] != "человеку"
        rows.append(
            f"<tr class='{'mine' if mine else 'human'}'>"
            f"<td><b>{_h.escape(x['name'][:28] or '—')}</b><br>"
            f"<span class=ph>+7{x['phone']}</span></td>"
            f"<td>{_h.escape(x['topic'] or '—')}"
            f"{'<br><i>' + _h.escape(x['subject']) + '</i>' if x.get('subject') else ''}</td>"
            f"<td class=q>{_h.escape(x['text'][:400])}</td>"
            f"<td class=a>{_h.escape(x.get('answer') or '') or '<i>пишет администратор</i>'}"
            f"{'<div class=bk>запись: ' + _h.escape(x['booked']) + '</div>' if x.get('booked') else ''}</td>"
            f"<td>{_h.escape(x['action'])}</td></tr>")
    night = ("<div class=warn>Сейчас вне рабочих часов центра (9:00–20:00 МСК) — "
             "отправка отложена до утра.</div>" if r["ночь"] else "")
    body = f"""<style>
    body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:16px;color:#222}}
    h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#666;font-size:13px;margin-bottom:10px}}
    table{{border-collapse:collapse;width:100%}}
    th{{background:#312783;color:#fff;font-size:12px;padding:6px;text-align:left}}
    td{{border-bottom:1px solid #e3e3e3;padding:8px 6px;font-size:13px;vertical-align:top}}
    .q{{max-width:340px;color:#444}} .a{{max-width:430px;white-space:pre-wrap}}
    .ph{{color:#666;font-size:12px}} .bk{{color:#5C8C1E;font-weight:600;margin-top:4px}}
    tr.mine td{{background:#F4F9EF}} tr.human td{{background:#fff;color:#777}}
    .warn{{background:#FFF4E0;border-left:4px solid #F59C00;padding:8px 10px;
          margin-bottom:10px;font-size:13px}}
    .go{{display:inline-block;background:#7DB928;color:#fff;padding:8px 14px;
        border-radius:6px;text-decoration:none;font-weight:600;margin-bottom:12px}}
    </style>
    <h1>Переписка: что спросили и что мы ответили</h1>
    <div class=sub>Диалогов без ответа {r['всего']} · отвечаю сам {r['ответили']} ·
    пишет администратор {r['человеку']}. Зелёные строки — мои ответы,
    серые уходят человеку.</div>
    {night}
    {'<a class=go href="/perepiska?send=1">Отправить мои ответы клиентам</a>'
     if not send else '<div class=warn>Отправлено.</div>'}
    <table><tr><th>Клиент</th><th>Тема</th><th>Спросили</th>
    <th>Наш ответ</th><th>Что сделано</th></tr>{''.join(rows)}</table>"""
    return HTMLResponse(body)


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
            LEFT JOIN joins j ON j.class_id = cl.id AND j.status_id NOT IN (1, 4)
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
     "«Абонемент 8 занятий — X ₽ с 1 сентября. Первое занятие условно-бесплатное (не понравится — платить не нужно, понравится — войдёт в абонемент) — на какой день записать?»"),
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
                LEFT JOIN joins j ON j.class_id = cl.id AND j.status_id NOT IN (1, 4)
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
    # Отказ ловим в момент получения, а не при следующем разборе: между
    # просьбой снять бронь и очередной рассылкой бывает меньше часа.
    for ts, phone, chat_type, text, mid in rows:
        try:
            from . import otkaz
            if otkaz.note(phone, text, ts):
                log.warning("отказ от %s — автосообщения остановлены", phone)
        except Exception:
            pass


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
    # мессенджеры для обращений
    "whatsapp": "https://wa.me/79165610077",   # номер входящих (см. wa_incoming)
    "telegram": "https://t.me/KidsUPchat",
    "max": "https://max.ru/u/f9LHodD0cOL7ouxX67LQufADpyAmbvMGRUdMqaGj2Ya-F1EuIVQMGWeU9gc",
    # соцсети — тоже через /go, чтобы видеть, откуда приходят
    "tgchannel": "https://t.me/KidsUP_ru",
    "vk": "https://vk.com/kidsup_ru",
    "youtube": "https://youtube.com/@kidsup_ru",
    "instagram": "https://instagram.com/kidsup_ru",
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
    if channel == "whatsapp":
        if visit:
            target += "?text=" + quote(WA_HELLO + visit)
        elif q.get("t"):
            # кнопка сайта передаёт готовое приветствие — без него клиент
            # попадает в пустой чат и половина не пишет первой
            target += "?text=" + quote(q.get("t")[:200])
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


APP_VERSION = "2026-08-28.20"


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
            "anthropic_proxy_secret", "work_hours", "ext_by_day",
            "vk_token", "vk_group_id", "tg_bot_token", "tg_channel", "mango_ext_admins",
            "calls_parsed", "sms_on", "sms_sender_name", "lead_hook_key",
            # разобранные записи разговоров: список recording_id, чтобы почасовой
            # разбор не написал в карточку один и тот же звонок дважды
            "calls_done",
            # сквозная аналитика: без этих двух ключей выгрузка оплат в Roistat
            # молча падает каждую ночь, а заявки с сайта туда не уходят вовсе
            "roistat_project", "roistat_key",
            # id утверждённого WABA-шаблона: без него массовая отправка через
            # 3507 отменяется, чтобы не плодить «отправленные» письма впустую
            "waba_template_id", "waba_templates",
            # номера, закрытые для любой отправки — включая Telegram и MAX,
            # которые могут висеть на том же аккаунте
            "blocked_senders",
            # номер WhatsApp для разовых сообщений: 0077 как канал переписки,
            # а пока он выведен из работы — резервный
            "chat_whatsapp"}


# Значения, которые нельзя отдавать целиком даже по авторизованному запросу.
# Ключ Anthropic — это доступ к деньгам владельца, секрет прокси открывает
# сам прокси. Показываем хвост: убедиться «тот ли вписан» можно,
# скопировать — нет. 22.08 ключ отдавался целиком, и это была дыра:
# страница настроек открыта всем, у кого есть пароль администратора.
SECRET_KEYS = {"anthropic_api_key", "anthropic_proxy_secret",
               "vk_token", "tg_bot_token"}


def _mask(key: str, value: str | None) -> str | None:
    if not value or key not in SECRET_KEYS:
        return value
    return f"…{value[-4:]} ({len(value)} симв.)"


@app.get("/api/settings", dependencies=AUTH)
async def api_get_settings():
    return {k: _mask(k, db.get_setting(k)) for k in sorted(SETTABLE)}


@app.post("/api/settings", dependencies=AUTH)
async def api_set_setting(payload: dict):
    """{"key": "...", "value": "..."} — только ключи из SETTABLE."""
    key, value = (payload.get("key") or "").strip(), payload.get("value")
    if key not in SETTABLE or value is None:
        raise HTTPException(400, f"key должен быть одним из {sorted(SETTABLE)}")
    db.set_setting(key, str(value).strip())
    return {"ok": True, key: _mask(key, db.get_setting(key))}


@app.get("/api/ai/probe", dependencies=AUTH)
async def api_ai_probe():
    """Дошёл ли запрос до Anthropic и что ответил.

    Разделяем состояния, которые снаружи выглядят одинаково «не работает»:
    прокси не прописан, прокси не отвечает, секрет не принят, ключ неверен."""
    from . import assistant
    r = assistant.probe()
    base = db.get_setting("anthropic_base_url") or "https://api.anthropic.com"
    key = db.get_setting("anthropic_api_key") or ""
    secret = db.get_setting("anthropic_proxy_secret") or ""
    st = r.get("status")
    via_proxy = "api.anthropic.com" not in base
    body = (r.get("body") or "").lower()
    if not r.get("reachable"):
        verdict = ("Адрес не отвечает вовсе. Проверьте, что он написан без "
                   "косой черты в конце и открывается в браузере.")
    elif not via_proxy:
        verdict = ("Прокси не прописан: запрос идёт напрямую на "
                   "api.anthropic.com, оттуда отказ по региону.")
    elif st == 500 and "shared_secret" in body:
        # Воркер жив и отвечает, но в его настройках нет переменной.
        # Частая причина: значение добавили, а Deploy не нажали.
        verdict = ("Прокси работает, но в нём не задан SHARED_SECRET. "
                   "В настройках воркера: Settings → Variables and Secrets → "
                   "Add → тип Secret, имя SHARED_SECRET, значение — то же, "
                   "что в anthropic_proxy_secret. После добавления обязательно "
                   "нажмите Deploy, иначе переменная не применится.")
    elif st == 403 and "forbidden" in body:
        verdict = ("Прокси отвечает, но не принимает секрет: "
                   "anthropic_proxy_secret должен совпадать с SHARED_SECRET "
                   "в настройках воркера посимвольно.")
    elif st == 401:
        verdict = ("Регион в порядке, прокси работает — дело только в ключе. "
                   "Проверьте, что он вписан целиком.")
    elif st == 200:
        verdict = "Всё работает: запрос дошёл до Anthropic и получил ответ."
    else:
        verdict = f"Неожиданный ответ {st}. Текст ниже поможет понять причину."
    return {"вердикт": verdict, "адрес": base,
            "ключ": ("вписан" if key else "НЕ вписан"),
            "секрет": ("вписан" if secret else "НЕ вписан"), "детали": r}


@app.post("/api/ai/route", dependencies=AUTH)
async def api_ai_route(payload: dict):
    """Проверка разбора смыслом: {"body": "текст задачи"} → кому и почему."""
    from . import brain
    body = (payload.get("body") or "").strip()
    if not body:
        raise HTTPException(400, "нужен body")
    if not brain.enabled():
        return {"error": "ключ Anthropic не вписан"}
    return {"разбор": brain.route_task(body)}


@app.post("/api/ai/hint", dependencies=AUTH)
async def api_ai_hint(payload: dict):
    """{"userId": N} — подсказка под конкретную семью из её карточки."""
    from . import brain, hintsweep, sync
    from .moyklass_client import MoyklassClient
    uid = payload.get("userId")
    if not uid:
        raise HTTPException(400, "нужен userId")
    if not brain.enabled():
        return {"error": "ключ Anthropic не вписан"}
    mk = MoyklassClient(sync.get_api_key())
    try:
        u = mk.get(f"/v1/company/users/{uid}")
        c = {"name": u.get("name") or "", "phone": (u.get("phone") or "")[-10:]}
        for a in (u.get("attributes") or []):
            if a.get("attributeAlias") == "birthday" and a.get("value"):
                c["birthday"] = a["value"][:10]
        profile = hintsweep._profile(mk, int(uid), c, None)
        return {"профиль": profile, "подсказка": brain.call_hint(profile)}
    finally:
        mk.close()


@app.post("/api/ai/dialog", dependencies=AUTH)
async def api_ai_dialog(payload: dict):
    """{"messages":[{"dir":"in|out","text":"…"}]} — намерение и следующий шаг."""
    from . import brain
    ms = payload.get("messages") or []
    if not ms:
        raise HTTPException(400, "нужен messages")
    if not brain.enabled():
        return {"error": "ключ Anthropic не вписан"}
    return {"разбор": brain.read_dialog(ms)}


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
_SCHED_HEADERS = dict(_PUB_CORS, **{"Cache-Control": "public, max-age=60, stale-while-revalidate=120"})
_SCHED_CACHE: dict = {"ts": 0.0, "data": None}
_LEAD_HITS: dict[str, list[float]] = {}


_SCHED_LOCK = threading.Lock()


@app.get("/api/public/schedule")
def public_schedule():
    """Расписание групп 2026/27 и свободные места — для сайта kidsup.ru."""
    import time as _t
    fresh = _SCHED_CACHE["data"] is not None and _t.time() - _SCHED_CACHE["ts"] < 180
    if fresh or (_SCHED_CACHE["data"] is not None and not _SCHED_LOCK.acquire(blocking=False)):
        # пока один поток пересчитывает, остальные отдают прошлый ответ
        return JSONResponse(_SCHED_CACHE["data"], headers=_SCHED_HEADERS)
    locked = not fresh
    try:
        return _build_schedule()
    finally:
        if locked and _SCHED_LOCK.locked():
            _SCHED_LOCK.release()


def _build_schedule():
    import time as _t
    all_groups = _enrollment_groups()
    groups = [g for g in all_groups if not g["buffer"]]
    # Витринное правило владельца (27.08, заменяет «минус два всем»):
    # где свободных мест мало — показываем честную цифру; где много —
    # прижимаем к 3-4, чтобы «свободно 8 мест» не читалось пустым залом.
    # Цифра прижатия детерминирована именем группы и не прыгает между
    # пересчётами. Живая цифра для админов на /enrollment не трогается.
    # У сада и нулевого класса вместимость 10 (решение владельца).
    def _pub(g):
        cap = g["capacity"]
        if "Мини-сад" in g["name"] or "Нулевой" in g["name"]:
            cap = min(cap, 10)
        real = max(0, cap - g["enrolled"])
        # Витрина (26.08): группа близка к заполнению или полна (<=3 свободных) —
        # показываем как есть; свободных ещё много — показываем на 2 меньше.
        shown = real if real <= 3 else real - 2
        return {**g, "capacity": cap, "free": shown}
    groups = [_pub(g) for g in groups]
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
    # листы ожидания новых направлений: сколько семей уже записалось
    waitlist: dict[str, int] = {}
    WAIT_KEYS = {"Танцы": "dance", "Хореография": "choreography", "Футбол": "football",
                 "Единоборства": "martial", "Акробатика": "acrobatics",
                 "Актёрское мастерство": "acting", "Техника речи": "speech"}
    for g in all_groups:
        if not g["name"].startswith("2627_Заявки_"):
            continue
        tail = g["name"].replace("2627_Заявки_", "").strip()
        k = WAIT_KEYS.get(tail)
        if k:
            waitlist[k] = g["enrolled"]
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT value FROM sync_state WHERE key IN ('last_light_sync','last_sync')").fetchall()
        vals = [r[0] for r in rows if r and r[0]]
        data_updated = max(vals) if vals else None
    except Exception:
        data_updated = None
    data = {"generated": datetime.now().isoformat(timespec="seconds"),
            "updated": datetime.now().isoformat(timespec="seconds"),
            "data_updated": data_updated,
            "by_key": by_key, "waitlist": waitlist,
            "groups": [{"name": g["name"], "course": g["course"], "day": g["day"],
                        "time": g["time"], "age": g["age"], "free": g["free"],
                        "capacity": g["capacity"],
                        "age_lo": g["age_lo"], "age_hi": g["age_hi"],
                        "price": g["price_new"],
                        "price_label": g["price_label"]} for g in groups]}
    _SCHED_CACHE.update(ts=_t.time(), data=data)
    return JSONResponse(data, headers=_SCHED_HEADERS)


@app.options("/api/public/lead")
def public_lead_options():
    return JSONResponse({"ok": True}, headers=_PUB_CORS)


def _lead_mark(lead_id: int | None, status: str, uid: int | None = None,
               error: str = "") -> None:
    """Отметка судьбы заявки: чтобы потерянные было видно и можно было добрать."""
    if not lead_id:
        return
    try:
        with db.get_conn() as conn:
            conn.execute("UPDATE site_leads SET crm_status=?, mk_user_id=COALESCE(?, mk_user_id), "
                         "attempts=COALESCE(attempts,0)+1, last_error=? WHERE id=?",
                         (status, uid, error[:300], lead_id))
            conn.commit()
    except Exception:
        logging.getLogger("kidsup.lead").warning("не удалось отметить заявку %s", lead_id)


def _find_client(mk, phone: str) -> tuple[int | None, int]:
    """Ищем карточку по номеру: сперва локальная база (самая свежая), затем
    живой поиск в МойКласс — локальная копия отстаёт до нескольких минут,
    и без этого шага на каждый повторный звонок плодился дубль карточки."""
    p10 = phone[-10:]
    if len(set(p10)) <= 2:        # 7777777777 и подобные стоят у десятков карточек
        return None, 0
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id FROM users WHERE substr(phone,-10)=? ORDER BY id DESC", (p10,)).fetchall()
        if rows:
            return rows[0]["id"], len(rows)
    except Exception:
        pass
    try:
        r = mk.get("/v1/company/users", {"phone": phone})
        users = (r.get("users") if isinstance(r, dict) else r) or []
        if users:
            return users[0]["id"], len(users)
    except Exception:
        pass
    return None, 0


def _lead_to_crm(lead: dict) -> None:
    """Доводка заявки с сайта. Порядок важен: СНАЧАЛА живому человеку
    (уведомление не зависит от МойКласс), потом CRM — иначе авария в CRM
    means клиент вообще никому не достался."""
    from . import autopilot, wazzup
    from .moyklass_client import MoyklassClient
    log = logging.getLogger("kidsup.lead")
    phone, child = lead["phone"], lead.get("child") or ""
    lead_id = lead.get("lead_id")
    course = lead.get("course") or ""

    admins = autopilot._admins_today() or autopilot._admins()
    duty = admins[autopilot._today().toordinal() % len(admins)] if admins else None
    # 0) мгновенный обратный звонок — решение владельца 24.08 вместо
    # платного «Автодозвона из форм». В рабочие часы АТС тут же набирает
    # дежурного и соединяет с клиентом; одна попытка на номер в день,
    # ночная заявка получает обычный порядок (уведомление + задача) и
    # звонок утром.
    # Кто за какой трубкой. 28.08 16:00 Ира ушла с доб.12 (трубка глючит)
    # на доб.10; Аня — ноутбук доб.15. Менять здесь при каждой пересадке:
    # по этой карте мгновенный перезвон по заявке набирает дежурного.
    MGR_EXT = {232763: "10", 232805: "15", 202856: "12"}
    try:
        from . import mango as _mango
        hour = autopilot._now().hour
        ext = MGR_EXT.get(duty["managerId"]) if duty else None
        if ext and 9 <= hour < 20 and len(phone) >= 10 \
                and autopilot._mark("lead_callback", f"{autopilot._today()}:{phone[-10:]}"):
            if _mango.callback(ext, phone):
                log.info("мгновенный перезвон: доб. %s → %s", ext, phone[-4:])
    except Exception as e:
        log.warning("мгновенный перезвон не запустился: %s", e)
    # 1) человек узнаёт о заявке в любом случае
    try:
        phones = json.loads(db.get_setting("admin_phones") or "{}")
        dphone = (phones.get(str(duty["managerId"])) if duty else None) or db.get_setting("digest_phone")
        if dphone:
            wazzup.send_via("tgapi", dphone,
                            f"🔥 Заявка с сайта kidsup.ru: {child or 'имя не указано'}, +{phone}"
                            + (f", {course}" if course else "")
                            + (f", {lead['age']}" if lead.get("age") else "")
                            + ". Звоним в течение 5 минут! — Клод", dry_run=False)
    except Exception as e:
        log.warning("уведомление дежурному не ушло: %s", e)

    # 2) CRM — по шагам, каждый сбой не роняет остальные
    mk = MoyklassClient(sync.get_api_key())
    try:
        uid, same = _find_client(mk, phone)
        if not uid:
            try:
                u = mk.post("/v1/company/users", {"name": child or "Заявка с сайта", "phone": phone})
                uid = (u or {}).get("id")
                if uid:
                    try:
                        mk.post(f"/v1/company/users/{uid}/status",
                                {"statusId": 125951, "statusChangeReasonId": 313608})
                    except Exception as e:
                        log.warning("статус новому лиду не поставлен: %s", e)
            except Exception as e:
                log.warning("карточка не создана: %s", e)
        details = ["🤖 Клод: 🌐 Заявка с нового сайта kidsup.ru",
                   f"Ребёнок: {child or '—'}" + (f", возраст {lead['age']}" if lead.get("age") else ""),
                   f"Направление: {course or 'не выбрано'}",
                   f"Телефон: +{phone}"]
        if lead.get("note"):
            details.append(f"Комментарий: {lead['note']}")
        if lead.get("roistat"):
            details.append(f"roistat_visit: {lead['roistat']} (сквозная аналитика)")
        if same > 1:
            details.append(f"⚠️ В базе {same} карточек с этим номером — проверьте, к какому ребёнку заявка.")
        details.append("Правило: позвонить в течение 5 минут (скорость = конверсия).")
        if uid:
            try:
                mk.post("/v1/company/userComments",
                        {"userId": uid, "comment": "\n".join(details), "showToUser": False})
            except Exception as e:
                log.warning("комментарий не записан: %s", e)
        if duty:
            body = ("🤖 Клод: 🔥 НОВАЯ ЗАЯВКА с сайта — позвонить в течение 5 минут! "
                    f"{child or 'имя не указано'}"
                    + (f", {lead['age']}" if lead.get("age") else "")
                    + (f", {course}" if course else "")
                    + f", тел. +{phone}")[:250]
            autopilot._task(mk, duty["managerId"], uid, body)
        _lead_mark(lead_id, "done", uid)
        log.info("заявка с сайта: +%s → карточка %s", phone, uid or "не создана")
    finally:
        mk.close()

    # 3) лид в Roistat — чтобы воронка видела не только оплаты
    try:
        from . import roistat as roistat_mod
        roistat_mod.push_lead(lead)
    except Exception as e:
        log.info("лид в Roistat не ушёл: %s", e)


@app.post("/hook/lead")
async def hook_lead(request: Request, key: str = ""):
    """Приём заявок с форм старого сайта (Tilda/Roistat) — тем же путём,
    что и формы нового сайта.

    24.08 выяснилось: формы Тильды доходят до сервиса заявок, но в CRM не
    попадают — из четырёх заявок дня одна пропала совсем, остальные завелись
    только потому, что клиенты потом позвонили сами. Вебхук закрывает дыру:
    любая форма, настроенная сюда, попадает в CRM, получает мгновенный
    перезвон дежурному и задачу.

    Настройка в Tilda: Сайт → Формы → Webhook, URL
    https://app.kidsup.ru/hook/lead?key=<секрет из настройки lead_hook_key>."""
    secret = db.get_setting("lead_hook_key", "")
    if secret and key != secret:
        return {"ok": False}
    try:
        payload = await request.json()
    except Exception:
        form = await request.form()
        payload = dict(form)
    low = {str(k).lower(): v for k, v in (payload or {}).items()}

    def pick(*names):
        for n in names:
            for k, v in low.items():
                if n in k and str(v).strip():
                    return str(v).strip()
        return ""
    phone = "".join(ch for ch in pick("phone", "тел") if ch.isdigit())
    if len(phone) == 11 and phone[0] == "8":
        phone = "7" + phone[1:]
    if len(phone) == 10:
        phone = "7" + phone
    if len(phone) != 11:
        log = logging.getLogger("kidsup.lead")
        log.warning("вебхук заявки без телефона: %s", str(payload)[:200])
        return {"ok": False, "error": "no phone"}
    lead = {"phone": phone, "child": pick("name", "имя", "ребен"),
            "age": pick("age", "возраст"),
            "course": pick("formname", "form_name", "форм", "курс", "направл"),
            "note": "заявка с формы старого сайта",
            "roistat": pick("roistat", "rv"), "lead_id": None}
    from starlette.concurrency import run_in_threadpool

    def _store():
        with db.get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO site_leads (ts, phone, child, age, course, note,"
                " roistat, ip, crm_status) VALUES"
                " (datetime('now'), ?, ?, ?, ?, ?, ?, ?, 'pending')",
                (lead["phone"], lead["child"], lead["age"], lead["course"],
                 lead["note"], lead["roistat"], "webhook"))
            conn.commit()
            return cur.lastrowid

    lead["lead_id"] = await run_in_threadpool(_store)
    import threading
    threading.Thread(target=lambda: _safe_lead(lead), daemon=True).start()
    return {"ok": True}


@app.post("/api/public/lead")
async def public_lead(request: Request):
    """Форма нового сайта kidsup.ru. Ботам не подсказываем (honeypot и лимит
    отвечают ok), но всё, что похоже на живую заявку, обязательно доходит
    хотя бы до журнала site_leads."""
    import time as _t
    from starlette.concurrency import run_in_threadpool
    log = logging.getLogger("kidsup.lead")
    try:
        if int(request.headers.get("content-length") or 0) > 8192:
            return JSONResponse({"ok": False, "error": "Слишком длинная заявка"},
                                status_code=413, headers=_PUB_CORS)
    except ValueError:
        pass
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):     # POST [] или "abc" не должны валить сервер
        payload = {}
    if not payload:
        # Вебхук Tilda шлёт форму как x-www-form-urlencoded или multipart,
        # а не JSON. Без этой ветки заявки с боевых доменов (kidsup.ru,
        # kidsupday.ru, kidsupweek.ru — все три на Tilda) до CRM не доходят.
        try:
            form = await request.form()
            payload = {k: v for k, v in form.items() if isinstance(v, str)}
        except Exception:
            payload = {}
        # Tilda называет поля с большой буквы и по-своему
        alias = {"Name": "name", "Phone": "phone", "Email": "email",
                 "Age": "age", "Course": "course", "Comment": "note",
                 "tranid": "tilda_id", "formid": "form"}
        for src, dst in alias.items():
            if payload.get(src) and not payload.get(dst):
                payload[dst] = payload[src]
    if str(payload.get("website") or "").strip():   # honeypot — люди его не видят
        return JSONResponse({"ok": True}, headers=_PUB_CORS)

    digits = "".join(ch for ch in str(payload.get("phone") or "") if ch.isdigit())
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":
        digits = "7" + digits
    if len(digits) != 11 or not digits.startswith("79"):
        # опечатка в телефоне не должна съедать лимит попыток
        return JSONResponse({"ok": False, "error": "Проверьте номер телефона"},
                            headers=_PUB_CORS)

    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() \
        or (request.client.host if request.client else "?")
    now = _t.time()
    for k in [k for k, v in _LEAD_HITS.items() if not v or now - v[-1] > 3600]:
        _LEAD_HITS.pop(k, None)          # чтобы словарь не рос вечно
    hits = [t for t in _LEAD_HITS.get(ip, []) if now - t < 600]
    throttled = len(hits) >= 15
    hits.append(now)
    _LEAD_HITS[ip] = hits

    lead = {"phone": digits,
            "child": str(payload.get("name") or "").strip()[:80],
            "age": str(payload.get("age") or "").strip()[:20],
            "course": str(payload.get("course") or "").strip()[:80],
            "note": str(payload.get("note") or "").strip()[:300],
            "roistat": str(payload.get("roistat") or "").strip()[:64]}

    def _store() -> int | None:
        with db.get_conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS site_leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
                child TEXT, age TEXT, course TEXT, note TEXT, roistat TEXT, ip TEXT)""")
            for col, ddl in (("crm_status", "TEXT"), ("attempts", "INTEGER"),
                             ("last_error", "TEXT"), ("mk_user_id", "INTEGER")):
                try:
                    conn.execute(f"ALTER TABLE site_leads ADD COLUMN {col} {ddl}")
                except Exception:
                    pass
            cur = conn.execute(
                "INSERT INTO site_leads (ts, phone, child, age, course, note, roistat, ip, crm_status)"
                " VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?)",
                (lead["phone"], lead["child"], lead["age"], lead["course"],
                 lead["note"], lead["roistat"], ip,
                 "throttled" if throttled else "pending"))
            conn.commit()
            return cur.lastrowid

    lead["lead_id"] = await run_in_threadpool(_store)
    if throttled:
        log.warning("лимит заявок с IP %s: заявка +%s сохранена в журнал без CRM", ip, digits)
        return JSONResponse({"ok": True, "throttled": True}, headers=_PUB_CORS)
    import threading
    threading.Thread(target=lambda: _safe_lead(lead), daemon=True).start()
    return JSONResponse({"ok": True}, headers=_PUB_CORS)


def _safe_lead(lead: dict) -> None:
    """Три попытки довести заявку до CRM: сеть и МойКласс иногда моргают."""
    import time as _t
    log = logging.getLogger("kidsup.lead")
    last = ""
    for attempt in range(3):
        try:
            _lead_to_crm(lead)
            return
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            log.warning("заявка +%s: попытка %s не удалась (%s)", lead.get("phone"), attempt + 1, last)
            _t.sleep(5 * (attempt + 1))
    _lead_mark(lead.get("lead_id"), "failed", None, last)
    log.error("заявка +%s НЕ доехала до МойКласс: %s", lead.get("phone"), last)
    try:                                   # молча терять лид нельзя — зовём людей
        from . import wazzup
        for ph in {db.get_setting("digest_phone") or "", }:
            if ph:
                wazzup.send_via("tgapi", ph,
                                f"⚠️ Заявка с сайта НЕ доехала до МойКласс: {lead.get('child') or 'без имени'}, "
                                f"+{lead.get('phone')}, {lead.get('course') or 'направление не выбрано'}. "
                                f"Позвоните вручную! Журнал: app.kidsup.ru/api/public/leads — Клод",
                                dry_run=False)
    except Exception:
        pass


@app.get("/api/public/leads", dependencies=AUTH)
def public_leads_list(limit: int = 50):
    """Просмотр принятых с сайта заявок (для админки, с auth)."""
    with db.get_conn() as conn:
        try:
            rows = conn.execute(
                "SELECT ts, phone, child, age, course, note, roistat, ip, "
                "COALESCE(crm_status,'?') crm_status, COALESCE(attempts,0) attempts, "
                "COALESCE(last_error,'') last_error, mk_user_id "
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


@app.get("/api/waba/templates", dependencies=AUTH)
async def api_waba_templates():
    """Шаблоны WABA и их статус модерации. Создать шаблон через API нельзя —
    только прочитать список; заводятся они в кабинете Wazzup."""
    from . import wazzup
    try:
        items = wazzup.templates()
    except Exception as e:
        raise HTTPException(502, f"Wazzup недоступен: {e}")
    return {"всего": len(items),
            "текущий_id": db.get_setting("waba_template_id") or None,
            "шаблоны": [{"id": t.get("id") or t.get("templateId"),
                         "имя": t.get("name"), "статус": t.get("status"),
                         "текст": (t.get("text") or t.get("body") or "")[:200]}
                        for t in items]}


@app.post("/api/waba/pick-template", dependencies=AUTH)
async def api_waba_pick(payload: dict = None):
    """Подхватить одобренный шаблон и вернуть рассылку в строй вручную —
    то же, что автопилот делает раз в пять минут."""
    from . import autopilot
    return autopilot._waba_template_watch()


@app.get("/api/prelaunch", dependencies=AUTH)
def api_prelaunch():
    """Предполётный чек рассылок: одна страница вместо десяти проверок.

    Появился 26.08 после дня, когда сбои находили клиенты, а не мы.
    Каждый пункт — то, что в этот день сработало или должно было
    сработать. Вердикт «можно включать» значит: все автосообщения
    пройдут через предохранитель и ни одно из известных настоящему
    моменту протухших не уйдёт."""
    from . import dostavka, otkaz, wazzup
    from datetime import date as _d
    blockers, warns = [], []
    # 1. стоп-кран
    off = db.get_setting("messages_off", "0") == "1"
    # 2. здоровье каналов за 4 часа
    try:
        health = dostavka.channel_health(hours=4)
    except Exception as e:
        health = []
        warns.append(f"здоровье каналов не посчиталось: {str(e)[:80]}")
    for h in health:
        if h.get("всего", 0) >= 5 and h.get("доля", 100) < 60:
            blockers.append(f"канал {h['transport']}: доходит {h['доля']}% — сначала чинить канал")
    # 3. очередь nabormail: размер и дубли
    try:
        q = json.loads(db.get_setting("nabormail_queue", "[]") or "[]")
        seen, dups = set(), 0
        for x in q:
            k = f"{x.get('kind') or 'nabor'}:{x.get('uid')}"
            dups += k in seen
            seen.add(k)
        if dups:
            blockers.append(f"в очереди nabormail {dups} дублей")
    except Exception:
        q = []
    # 4. протухшие тексты в broadcast_queue: прогоняем маркеры EXPIRED
    stale = 0
    today = _d.today().isoformat()
    with db.get_conn() as conn:
        try:
            rows = conn.execute("SELECT id, text FROM broadcast_queue "
                                "WHERE status='pending'").fetchall()
        except Exception:
            rows = []
        for rid, text in rows:
            low = (text or "").lower()
            if any(m.lower() in low and today >= dead
                   for m, dead in wazzup.EXPIRED):
                stale += 1
    if stale:
        warns.append(f"{stale} pending-писем уже режутся маркерами EXPIRED — "
                     f"похоронить через /api/broadcast/cancel-campaign")
    # 5. стоп-лист отказов
    try:
        ref = [r for r in otkaz.feed() if not r["снят"]]
        unlinked = [r for r in ref if not r["телефон"]]
        if unlinked:
            blockers.append(f"{len(unlinked)} отказов не связаны с телефоном — "
                            f"их семьи не защищены: app.kidsup.ru/otkazy")
    except Exception as e:
        blockers.append(f"стоп-лист отказов не читается: {str(e)[:80]}")
        ref = []
    # 6. отправители
    senders = db.get_setting("wa_senders", "") or ""
    blocked = db.get_setting("blocked_senders", "") or ""
    for num in [x.strip() for x in senders.split(",") if x.strip()]:
        if num in blocked or f"{num}:whatsapp" in blocked:
            blockers.append(f"номер {num} стоит и в wa_senders, и в blocked_senders")
    verdict = "можно включать" if not blockers else "НЕ включать"
    return {"вердикт": verdict,
            "стоп-кран": "включён (сообщения стоят)" if off else "снят (сообщения идут)",
            "блокеры": blockers, "предупреждения": warns,
            "каналы за 4ч": health,
            "очередь nabormail": len(q),
            "отказов в стоп-листе": len(ref),
            "sms_on": db.get_setting("sms_on", "0") == "1",
            "отправители": senders}


@app.get("/prelaunch", response_class=HTMLResponse, dependencies=AUTH)
def prelaunch_page():
    """Предполётный чек глазами человека: светофор вместо JSON.

    /api/prelaunch отдаёт машинный ответ с русскими ключами в юникоде —
    владелец открыл его с телефона и увидел кашу. Люди смотрят сюда."""
    d = api_prelaunch()
    ok = d["вердикт"] == "можно включать"
    rows = []
    for b in d["блокеры"]:
        rows.append(f"<li class=bad>⛔ {html.escape(b)}</li>")
    for w in d["предупреждения"]:
        rows.append(f"<li class=warn>⚠️ {html.escape(w)}</li>")
    ch = "".join(
        f"<tr><td>{html.escape(str(c['transport']))}</td>"
        f"<td>{c['всего']}</td><td>{c['дошло']}</td>"
        f"<td class={'ok' if c['доля'] >= 80 else 'warn'}>{c['доля']}%</td></tr>"
        for c in d["каналы за 4ч"])
    return f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Предполётный чек — KidsUP</title>
<style>
body{{font:16px/1.5 system-ui,sans-serif;margin:0;padding:20px;color:#312783;
     background:#fff;max-width:720px}}
.verdict{{font-size:26px;font-weight:700;padding:18px 22px;border-radius:14px;
  background:{'#e8f6e8' if ok else '#fdeaea'};color:{'#1b7a1b' if ok else '#c01818'}}}
.sub{{color:#666;margin:10px 0 22px}}
ul{{padding-left:20px}} li{{margin:6px 0}}
li.bad{{color:#c01818}} li.warn{{color:#b07500}}
table{{border-collapse:collapse;margin-top:8px}}
td,th{{border-bottom:1px solid #e6e6ef;padding:6px 14px;text-align:left}}
td.ok{{color:#1b7a1b;font-weight:600}} td.warn{{color:#b07500;font-weight:600}}
.kv{{margin-top:18px;color:#444}} .kv b{{color:#312783}}
</style>
<div class=verdict>{'✅ Можно включать' if ok else '⛔ НЕ включать'}</div>
<div class=sub>Стоп-кран: {html.escape(d["стоп-кран"])}</div>
{('<ul>' + ''.join(rows) + '</ul>') if rows else '<p>Блокеров и предупреждений нет.</p>'}
<h3>Доставка за 4 часа</h3>
<table><tr><th>Канал</th><th>Отправлено</th><th>Дошло</th><th>Доля</th></tr>{ch}</table>
<div class=kv>
Очередь рассылки: <b>{d["очередь nabormail"]}</b> ·
Отказов в стоп-листе: <b>{d["отказов в стоп-листе"]}</b> ·
СМС: <b>{'включены' if d["sms_on"] else 'выключены'}</b><br>
Отправители WhatsApp: <b>{html.escape(d["отправители"])}</b></div>"""


@app.post("/api/broadcast/cancel-campaign", dependencies=AUTH)
async def api_broadcast_cancel_campaign(payload: dict = Body(...)):
    """Отменить все неотправленные письма кампании. Насовсем.

    Появилось 26.08: в очереди лежали 290 приглашений в лагерь «ещё
    можно успеть» — за два дня до конца смены. Их держал только
    выключатель broadcast_transports=off, то есть одно неосторожное
    включение рассылки отправило бы все 290. Протухшую кампанию надо
    хоронить, а не ставить на паузу."""
    camp = str((payload or {}).get("campaign") or "").strip()
    if not camp:
        raise HTTPException(400, "нужна campaign")
    with db.get_conn() as conn:
        n = conn.execute(
            "UPDATE broadcast_queue SET status='cancelled' "
            "WHERE status='pending' AND campaign=?", (camp,)).rowcount
    return {"ok": True, "кампания": camp, "отменено": n}


@app.post("/api/broadcast/hold", dependencies=AUTH)
async def api_broadcast_hold(payload: dict = None):
    """Снять человека со всех рассылок: у семьи сейчас не до нас.

    Появилось 22.08: администратор дозвонилась до мамы, которая была
    в роддоме. Такой семье нельзя ни звонить, ни слать — а очередь про
    это не знает и через час отправит письмо про лагерь. Статус «не
    писать» здесь не годится: он навсегда, а нужно на время.

    Отменяет все строки в состоянии pending по этому телефону.
    Кампании, поставленные позже, снимать надо заново — это осознанно:
    «не беспокоить» имеет срок, и через месяц семье можно написать."""
    phone = "".join(c for c in str((payload or {}).get("phone") or "") if c.isdigit())
    if len(phone) < 10:
        raise HTTPException(400, "нужен телефон")
    with db.get_conn() as conn:
        n = conn.execute(
            "UPDATE broadcast_queue SET status='cancelled' WHERE status='pending' "
            "AND substr(replace(replace(replace(phone,' ',''),'-',''),'+',''), -10)=?",
            (phone[-10:],)).rowcount
    return {"ok": True, "телефон": phone[-10:], "снято_с_рассылки": n}


@app.post("/api/broadcast/requeue", dependencies=AUTH)
async def api_broadcast_requeue(payload: dict = None):
    """Вернуть в очередь то, что помечено недоставляемым: {"campaign": "..."}.

    Нужно, когда отказ был не по вине адресата, а по нашей — например
    отправка шла через заблокированный номер, и Wazzup принимал сообщения,
    не доставляя их. Метки попыток снимаем, иначе строка снова упрётся
    в проверку «этим каналом уже пробовали»."""
    campaign = ((payload or {}).get("campaign") or "").strip()
    sender = ((payload or {}).get("sender") or "").strip()
    day = ((payload or {}).get("day") or "").strip()
    if not campaign and not sender:
        raise HTTPException(400, "нужен campaign или sender")
    # sender+day — разбор «фантомной» отправки: канал отчитался об успехе,
    # а сообщения не ушли. Такие строки помечены sent, и без возврата
    # адресат больше никогда не получит письмо.
    where, args = [], []
    if campaign:
        where.append("campaign=?"); args.append(campaign)
    if sender:
        where.append("sender=?"); args.append(sender)
        where.append("status='sent'")
    else:
        where.append("status='undeliverable'")
    if day:
        where.append("substr(sent,1,10)=?"); args.append(day)
    with db.get_conn() as conn:
        n = conn.execute(
            "UPDATE broadcast_queue SET status='pending', tried='', sender=NULL, "
            "sent=NULL WHERE " + " AND ".join(where), args).rowcount
    return {"ok": True, "campaign": campaign or "—", "sender": sender or "—",
            "вернулось_в_очередь": n}


@app.get("/api/broadcast/status", dependencies=AUTH)
async def api_broadcast_status():
    from . import autopilot
    return autopilot.broadcast_status()


@app.post("/api/broadcast/suppress", dependencies=AUTH)
def api_broadcast_suppress(payload: dict = Body(...)):
    """Точечно снять с рассылки конкретные номера: {"phones": [...],
    "dry_run": true}. В отличие от /cancel не трогает всю кампанию —
    нужна, когда клиент попросил не писать или семья ушла с конфликтом."""
    phones = {"".join(ch for ch in str(p) if ch.isdigit())[-10:]
              for p in (payload.get("phones") or [])}
    phones = {p for p in phones if len(p) == 10}
    if not phones:
        raise HTTPException(422, "нужен список phones")
    dry = bool(payload.get("dry_run"))
    hit, camps = 0, {}
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, phone, campaign FROM broadcast_queue WHERE status='pending'").fetchall()
        for rid, phone, camp in rows:
            p10 = "".join(ch for ch in str(phone or "") if ch.isdigit())[-10:]
            if p10 not in phones:
                continue
            hit += 1
            camps[camp] = camps.get(camp, 0) + 1
            if not dry:
                conn.execute("UPDATE broadcast_queue SET status='cancelled' WHERE id=?", (rid,))
        if not dry:
            conn.commit()
    return {"matched": hit, "by_campaign": camps, "dry_run": dry}


@app.post("/api/broadcast/restore", dependencies=AUTH)
def api_broadcast_restore(payload: dict = Body(...)):
    """Вернуть в очередь ошибочно отменённые строки: {"campaigns": [...],
    "exclude_phones": ["7996…"]}. Не трогает тех, кому уже отправляли, —
    чтобы никто не получил сообщение дважды."""
    camps = [str(c) for c in (payload.get("campaigns") or []) if c]
    excl = {"".join(ch for ch in str(p) if ch.isdigit())[-10:]
            for p in (payload.get("exclude_phones") or [])}
    if not camps:
        raise HTTPException(422, "нужен список campaigns")
    marks = ",".join("?" * len(camps))
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT id, phone FROM broadcast_queue WHERE status='cancelled' "
            f"AND campaign IN ({marks})", camps).fetchall()
        sent_phones = {r[0][-10:] for r in conn.execute(
            "SELECT DISTINCT phone FROM broadcast_queue WHERE status='sent' AND phone IS NOT NULL")}
        back = 0
        for rid, phone in rows:
            p10 = "".join(ch for ch in str(phone or "") if ch.isdigit())[-10:]
            if p10 in excl or p10 in sent_phones:
                continue
            conn.execute("UPDATE broadcast_queue SET status='pending' WHERE id=?", (rid,))
            back += 1
        conn.commit()
    return {"restored": back, "skipped_already_sent_or_excluded": len(rows) - back}


@app.post("/api/broadcast/retext-age", dependencies=AUTH)
def api_broadcast_retext_age(payload: dict = None):
    """Переписать ожидающие письма под возрастные тексты набора.

    От соседнего /api/broadcast/retext отличается тем, что текст не один
    на кампанию, а свой для каждого адресата — по возрасту ребёнка.

    В очереди лежат формулировки, заведённые до того, как появились
    возрастные шаблоны: место праздника, состав направлений и события
    там описаны иначе. Клиент не должен получать разное в зависимости
    от того, каким каналом до него дошли, поэтому тексты приводим
    к одному источнику — app.wabatexts, с подстановкой имени вместо
    переменной шаблона.

    По умолчанию только те строки, что уйдут в MAX и Telegram: WABA
    шлёт утверждённый шаблон, и её текст в очереди всё равно не
    используется."""
    from . import autopilot, wabatexts, wazzup
    p = payload or {}
    campaigns = p.get("campaigns") or ["invite_a", "invite_b", "invite_v"]
    only_free = p.get("only_free", True)
    dry = p.get("dry_run", True)

    def pick(age):
        if age is None:
            return wabatexts.TEMPLATES["nabor_bez_vozrasta"]
        for lim, name in autopilot.AGE_TEMPLATES:
            if age < lim:
                return wabatexts.TEMPLATES[name]
        return wabatexts.TEMPLATES["nabor_bez_vozrasta"]

    done, skip, sample = 0, 0, None
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, phone, child FROM broadcast_queue WHERE status='pending' "
            "AND campaign IN (%s)" % ",".join("?" * len(campaigns)),
            campaigns).fetchall()
        for r in rows:
            phone = r["phone"] or ""
            if only_free:
                try:
                    if wazzup.best_channel(phone, mass=True) not in ("tgapi", "max"):
                        skip += 1
                        continue
                except Exception:
                    skip += 1
                    continue
            age = autopilot._age_by_phone(phone)
            # {{1}} — синтаксис Meta; в живом канале подставляем имя сами
            text = pick(age).replace("({{1}})", "({имя})").replace("{{1}}", "{имя}")
            if sample is None:
                sample = text[:400]
            if not dry:
                conn.execute("UPDATE broadcast_queue SET text=? WHERE id=?",
                             (text, r["id"]))
            done += 1
    return {"переписано": done, "пропущено_не_свой_канал": skip,
            "пробный_прогон": dry, "образец": sample}


@app.get("/api/broadcast/free-list", dependencies=AUTH)
def api_broadcast_free_list(limit: int = 200):
    """Письма, которые можно отправить бесплатно — в MAX и Telegram.

    Очередь на это не рассчитана: её тик умеет только WhatsApp и шлёт
    подряд. Для живых мессенджеров нужен другой темп — паузы и вариации,
    иначе аккаунт читается как спам-бот. Поэтому список отдаётся наружу,
    а отправку ведёт app.tgdrip."""
    from . import autopilot, wazzup
    out = []
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, campaign, phone, child, text FROM broadcast_queue "
            "WHERE status='pending' LIMIT 4000").fetchall()
    for r in rows:
        phone = r["phone"] or ""
        uid = autopilot._uid_by_phone(phone)
        try:
            tr = wazzup.best_channel(phone, uid, mass=True)
        except Exception:
            continue
        if tr not in ("tgapi", "max"):
            continue
        out.append({"id": r["id"], "campaign": r["campaign"], "transport": tr,
                    "phone": phone, "child": r["child"],
                    "uid": uid, "text": r["text"]})
        if len(out) >= limit:
            break
    return {"всего": len(out), "письма": out}


@app.post("/api/broadcast/mark-sent", dependencies=AUTH)
def api_broadcast_mark_sent(payload: dict = None):
    """Отметить письма отправленными: id и каким каналом ушло."""
    p = payload or {}
    ids = [int(x) for x in (p.get("ids") or [])]
    sender = str(p.get("sender") or "")[:40]
    if not ids:
        raise HTTPException(400, "нужен ids")
    from . import autopilot
    with db.get_conn() as conn:
        n = conn.execute(
            "UPDATE broadcast_queue SET status='sent', sent=?, sender=?, "
            "tried=COALESCE(tried,'')||? WHERE id IN (%s)" % ",".join("?" * len(ids)),
            [autopilot._now().isoformat(timespec="seconds"), sender,
             f"{sender}=ok;"] + ids).rowcount
    return {"ok": True, "отмечено": n}


@app.get("/api/broadcast/channels", dependencies=AUTH)
def api_broadcast_channels(campaign: str = ""):
    """Куда уйдёт очередь: сколько писем в MAX, Telegram и WABA.

    Правило владельца: есть переписка в MAX или Telegram — пишем туда
    бесплатно и без шаблона; нет — только WABA с утверждённым шаблоном.
    Пока шаблоны на модерации, первая часть очереди уже может уходить,
    и эта сводка показывает, насколько она велика."""
    from . import autopilot, wazzup
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT campaign, phone FROM broadcast_queue WHERE status='pending'"
            + (" AND campaign=?" if campaign else ""),
            ((campaign,) if campaign else ())).fetchall()
    out: dict = {}
    for r in rows:
        phone = r["phone"] or ""
        # uid обязателен: телеграм-контакт по телефону не найти, у него
        # телефона нет вовсе — без карточки всё уезжало бы в WABA
        uid = autopilot._uid_by_phone(phone)
        try:
            tr = wazzup.best_channel(phone, uid, mass=True) or "wapi"
        except Exception:
            tr = "wapi"
        if tr == "wapi" and not uid:
            tr = "wapi (без карточки)"
        c = out.setdefault(r["campaign"], {})
        c[tr] = c.get(tr, 0) + 1
    itog: dict = {}
    for c in out.values():
        for k, v in c.items():
            itog[k] = itog.get(k, 0) + v
    free = sum(v for k, v in itog.items() if k in ("tgapi", "max"))
    return {"по_кампаниям": out, "итого": itog,
            "можно_слать_сейчас": free,
            "ждут_шаблона_WABA": sum(itog.values()) - free}


@app.get("/api/broadcast/peek", dependencies=AUTH)
def api_broadcast_peek(campaign: str = "", limit: int = 3):
    """Показать тексты, которые СЕЙЧАС стоят в очереди, — чтобы перед запуском
    рассылки глазами проверить даты, цены и место события."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT campaign, status, COUNT(*) n FROM broadcast_queue "
            + ("WHERE campaign=? " if campaign else "")
            + "GROUP BY campaign, status", ((campaign,) if campaign else ())).fetchall()
        out = {"counts": [dict(r) for r in rows], "samples": []}
        q = ("SELECT campaign, phone, child, text FROM broadcast_queue "
             "WHERE status='pending' " + ("AND campaign=? " if campaign else "")
             + "GROUP BY campaign LIMIT ?")
        args = ((campaign, limit) if campaign else (limit,))
        for r in conn.execute(q, args).fetchall():
            out["samples"].append({"campaign": r["campaign"], "child": r["child"],
                                   "phone": (r["phone"] or "")[-4:], "text": r["text"]})
    return out


@app.get("/api/broadcast/log", dependencies=AUTH)
def api_broadcast_log(day: str = "", limit: int = 100):
    """Кому и когда реально ушла рассылка. Нужно, чтобы разбирать вопросы
    «почему это пришло вот этому человеку» — по телефону сразу видно кампанию,
    сегмент и время отправки."""
    from . import autopilot
    d = day or autopilot._today().isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT campaign, phone, child, status, sent, created, sender FROM broadcast_queue "
            "WHERE substr(COALESCE(sent, created), 1, 10) = ? "
            "ORDER BY COALESCE(sent, created) DESC LIMIT ?",
            (d, max(1, min(500, limit)))).fetchall()
        out = []
        for r in rows:
            ph = "".join(ch for ch in (r["phone"] or "") if ch.isdigit())
            name = None
            if len(ph) >= 10:
                u = conn.execute("SELECT name FROM users WHERE substr(phone,-10)=? LIMIT 1",
                                 (ph[-10:],)).fetchone()
                name = u["name"] if u else None
            out.append({"campaign": r["campaign"], "phone": ph, "crm_name": name,
                        "child": r["child"], "status": r["status"],
                        "sent": r["sent"], "created": r["created"],
                        # с какого нашего номера ушло: без этого нельзя ответить
                        # на вопрос «почему сообщение висит недоставленным» —
                        # у каждого номера своё состояние и свой лимит
                        "sender": r["sender"]})
    return {"day": d, "count": len(out), "rows": out}


@app.post("/api/autopilot/reactivate-now", dependencies=AUTH)
def autopilot_reactivate_now(cap: int = 15):
    from . import autopilot
    import traceback
    mk = autopilot._client()
    try:
        return {"ok": True, "sent": autopilot.reactivate_thinkers(mk, cap=cap)}
    except Exception:
        return {"ok": False, "error": traceback.format_exc()[-700:]}
    finally:
        mk.close()


@app.post("/api/autopilot/digest-now", dependencies=AUTH)
def autopilot_digest_now():
    from . import autopilot
    import traceback
    mk = autopilot._client()
    try:
        autopilot.evening_digest(mk)
        return {"ok": True}
    except Exception:
        return {"ok": False, "error": traceback.format_exc()[-700:]}
    finally:
        mk.close()


@app.get("/zayavki", response_class=HTMLResponse, dependencies=AUTH)
def zayavki_page(fresh: int = 0):
    def _build():
        r = _zayavki_build()
        return r.body.decode() if hasattr(r, "body") else str(r)
    return HTMLResponse(_page_cache("zayavki", 15, _build, fresh=bool(fresh)))


def _zayavki_build():
    """Листы заявок сезона 2026/27 поимённо: кому звонить и что говорить.

    27.08 план дня отправил админов «обзвонить 19 заявок», не сказав, где
    взять имена и телефоны, — владелец справедливо спросил «откуда это
    брать?». Страница собирает живые заявки из МойКласс при открытии:
    группы «…Заявки…» сезона 2627, без отказавшихся и завершивших."""
    import html as H
    from datetime import datetime as _dt
    from . import sync as _sync, taskguard as _tg
    from .moyklass_client import MoyklassClient
    mk = MoyklassClient(_sync.get_api_key())
    try:
        joins = _tg.pull_all(mk, "/v1/company/joins", "joins")
        rc = mk.get("/v1/company/classes", {"limit": 500})
        cls = {c["id"]: (c.get("name") or "")
               for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
        users_ = _tg.pull_all(mk, "/v1/company/users", "users", cache_hours=2)
    finally:
        mk.close()
    byid = {u["id"]: u for u in users_}
    DEAD = {1, 4}
    # существующий курс → что говорить; несуществующий → мост
    FUTURE = {"Робототехника": "шахматы или ментальная арифметика",
              "Танцы": "«Музыка и речь» (движение и ритм)",
              "Скорочтение": "подготовка к школе (читающие) или Нулевой класс"}
    rows = []
    for j in joins:
        nm = cls.get(j.get("classId"), "")
        if not nm.startswith("2627") or "аявк" not in nm:
            continue
        if j.get("statusId") in DEAD:
            continue
        u = byid.get(j.get("userId")) or {}
        phone = "".join(ch for ch in (u.get("phone") or "") if ch.isdigit())
        course = (nm.replace("2627_", "").replace("_Заявки", "")
                  .replace("Заявки_", "")) or "?"
        rows.append({"course": course, "name": u.get("name") or f"id {j.get('userId')}",
                     "uid": j.get("userId"), "phone": phone,
                     "when": str(j.get("createdAt") or "")[:10]})
    rows.sort(key=lambda r: (r["course"] in FUTURE, r["course"], r["when"]))
    out = ["""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Листы заявок — кому звонить</title>
<style>body{font:16px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;
padding:14px 16px 50px;background:#F7F7FC;color:#232046;max-width:820px}
h1{font-size:1.4rem;color:#312783}h2{font-size:1.05rem;color:#312783;margin:1.6rem 0 .4rem}
.c{background:#fff;border:1px solid #E3E1F0;border-radius:12px;padding:10px 14px;margin:8px 0;
display:flex;gap:10px;flex-wrap:wrap;align-items:center;justify-content:space-between}
.c .n{font-weight:700}.c .d{color:#5B5876;font-size:.85rem}
a.btn{background:#7DB928;color:#fff;text-decoration:none;border-radius:999px;
padding:.45rem 1rem;font-weight:700;font-size:.9rem}
a.wa{background:#25D366}a.crm{background:#1DA7E0}
.scr{background:#EEF3FB;border-radius:10px;padding:10px 14px;margin:6px 0 14px;font-size:.92rem}
.warn{background:#FFF4E0;border:1px solid #F59C00;border-radius:10px;padding:10px 14px;margin:10px 0}
</style>
<h1>📋 Листы заявок — кому звонить</h1>
<p style="color:#5B5876">Живые данные из МойКласс на этот момент. Позвонили — поставьте итог
в CRM (запись / пробное / не актуально), и человек исчезнет отсюда после смены статуса.</p>"""]
    cur = None
    n_exist = sum(1 for r in rows if r["course"] not in FUTURE)
    out.append(f"<div class=warn><b>Заявки на действующие курсы: {n_exist}.</b> Скрипт: "
               "«Здравствуйте! Вы оставляли заявку на [курс] в KidsUP. Место есть, и до 31 августа "
               "включительно действуют цены прошлого года. Могу записать сразу — или удобнее прийти "
               "на открытый урок на следующей неделе? Первое занятие условно-бесплатное».</div>")
    for r in rows:
        if r["course"] != cur:
            cur = r["course"]
            if cur in FUTURE:
                out.append(f"<h2>🕐 {H.escape(cur)} — курса пока НЕТ</h2>"
                           f"<div class=scr>Скрипт: «Вы у нас первые в списке на {H.escape(cur.lower())} — "
                           f"откроем при наборе группы, вы узнаете первыми. А уже сейчас есть "
                           f"{FUTURE[cur]} — и приходите в субботу на праздник в парке „Янтарная горка“, "
                           f"вход свободный». Даты старта НЕ обещаем.</div>")
            else:
                out.append(f"<h2>{H.escape(cur)}</h2>")
        tel = f"+7{r['phone'][-10:]}" if len(r["phone"]) >= 10 else ""
        btns = ""
        if tel:
            btns = (f"<span><a class=btn href='tel:{tel}'>Позвонить</a> "
                    f"<a class='btn wa' target=_blank href='https://wa.me/{tel.lstrip('+')}'>WhatsApp</a> "
                    f"<a class='btn crm' target=_blank "
                    f"href='https://app.moyklass.com/user/{r['uid']}/info'>CRM</a></span>")
        out.append(f"<div class=c><span><span class=n>{H.escape(r['name'])}</span> "
                   f"<span class=d>{tel} · заявка от {r['when']}</span></span>{btns}</div>")
    if not rows:
        out.append("<p>Открытых заявок нет — все разобраны 🎉</p>")
    out.append(f"<p style='color:#5B5876;font-size:.85rem'>Собрано {_dt.now().strftime('%d.%m %H:%M')} · "
               f"всего {len(rows)} заявок</p><!--cache-note-->")
    return HTMLResponse("".join(out))


@app.get("/zapolnyaemost", response_class=HTMLResponse, dependencies=AUTH)
def zapolnyaemost_page():
    """Заполняемость групп нового сезона — самые пустые сверху."""
    import html as _h
    from . import autopilot
    mk = autopilot._client()
    try:
        fills = autopilot.group_fill(mk)
    finally:
        mk.close()
    rows = []
    for f in fills:
        bar = "█" * f["got"] + "░" * f["free"]
        hot = ' style="background:#FFF4E0"' if f["got"] * 2 < f["target"] else ""
        rows.append(f"<tr{hot}><td>{_h.escape(autopilot._join_title(f['name'])[:64])}</td>"
                    f"<td class=n>{f['got']}/{f['target']}</td>"
                    f"<td class=b>{bar}</td></tr>")
    return HTMLResponse(f"""<style>
    body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:14px;color:#222}}
    h1{{font-size:19px;margin:0 0 4px}} .sub{{color:#666;font-size:13px;margin-bottom:10px}}
    table{{border-collapse:collapse;width:100%}}
    th{{background:#312783;color:#fff;font-size:12px;padding:6px;text-align:left}}
    td{{border-bottom:1px solid #ddd;padding:6px;font-size:14px}}
    .n{{white-space:nowrap;font-weight:600}} .b{{font-family:monospace;letter-spacing:1px}}</style>
    <h1>Заполняемость групп 2026/27</h1>
    <div class=sub>Самые пустые сверху. Жёлтым — занято меньше половины плана:
    эти группы в приоритете обзвона. Живая страница.</div>
    <table><tr><th>Группа</th><th>Занято</th><th></th></tr>{''.join(rows)}</table>""")


@app.post("/api/autopilot/confirm-now", dependencies=AUTH)
def autopilot_confirm_now():
    """Подтверждения записей немедленно + текст ошибки, если падает."""
    from . import autopilot
    import traceback
    mk = autopilot._client()
    try:
        autopilot.confirm_joins(mk)
        return {"ok": True}
    except Exception:
        return {"ok": False, "error": traceback.format_exc()[-800:]}
    finally:
        mk.close()


@app.post("/api/nabormail/build", dependencies=AUTH)
def nabormail_build():
    """Собрать очередь рассылки по набору. Считать её надо именно на
    сервере: отметки «кому уже ушло» лежат в его базе, и очередь,
    собранная с другой машины, о них не знает и продублирует."""
    from . import nabormail
    return {"ok": True, "в очереди": nabormail.build()}


@app.post("/api/nabormail/confirms", dependencies=AUTH)
def nabormail_confirms(rebuild: bool = False):
    """Поставить в начало очереди подтверждения записи, не дошедшие 25.08
    из-за десятизначного chatId."""
    from . import nabormail
    return {"ok": True, "поставлено": nabormail.confirms_to_queue(rebuild=rebuild)}


@app.post("/api/lizacheck/close", dependencies=AUTH)
def lizacheck_close(dry: bool = True):
    """Закрыть задачи Лизы, по которым работа уже сделана."""
    from . import lizacheck
    return lizacheck.close_done(dry=dry)


@app.get("/zadachi-lizy", response_class=HTMLResponse, dependencies=AUTH)
def zadachi_lizy_live():
    """Задачи Лизы с проверкой актуальности — считается в момент открытия.

    Живая страница, а не файл: журнал переписки лежит в базе сервера,
    собрать её где-то ещё нельзя, а данные меняются каждый час."""
    from . import lizacheck
    return HTMLResponse(lizacheck.page(lizacheck.check()))


@app.get("/api/guard", dependencies=AUTH)
def api_guard(hours: int = 24):
    """Что предохранитель пропустил и что остановил за последние часы."""
    from collections import Counter as _C
    from datetime import datetime as _dt, timedelta as _td
    edge = (_dt.utcnow() + _td(hours=3) - _td(hours=hours)).isoformat(timespec="seconds")
    rows = []
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT phone, kind, transport, ts FROM wazzup_guard "
                "WHERE ts >= ? ORDER BY ts DESC", (edge,)).fetchall()
    except Exception:
        pass
    per_phone = _C(r[0] for r in rows)
    return {"стоп-кран": db.get_setting("messages_off", "0") == "1",
            "лимит в сутки на человека": 2,
            "отправок за период": len(rows),
            "по видам": dict(_C(r[1] for r in rows)),
            "по каналам": dict(_C(r[2] or "—" for r in rows)),
            "больше всех получили":
                [{"телефон": "+7" + p, "сообщений": n}
                 for p, n in per_phone.most_common(5)]}


@app.post("/api/caddy-reload", dependencies=AUTH)
def api_caddy_reload():
    """Перезагрузить Caddy на этом же сервере. Постоянный, но узкий рычаг:
    никаких аргументов, только reload. Нужен, когда у нового домена DNS
    доехал позже, чем Caddy попытался выпустить сертификат, и выпуск ушёл
    в часовой бэкофф — reload сбрасывает его и пробует сразу (так чинили
    kidsupday и kidsupweek 27.08)."""
    import subprocess
    out = subprocess.run(["systemctl", "reload", "caddy"],
                         capture_output=True, text=True, timeout=30)
    return {"код": out.returncode, "вывод": (out.stdout + out.stderr)[-400:]}


@app.post("/api/guard/stop", dependencies=AUTH)
def api_guard_stop(off: bool = True):
    """Мгновенный стоп-кран для ВСЕХ автосообщений."""
    db.set_setting("messages_off", "1" if off else "0")
    return {"ok": True, "автосообщения": "остановлены" if off else "включены"}


@app.get("/api/wazzup/channels-map", dependencies=AUTH)
def api_wazzup_channels_map():
    """Номера с живой перепиской по каналам (для точечных рассылок в
    Telegram/MAX: туда можно писать только тем, кто писал нам сам)."""
    with db.get_conn() as conn:
        try:
            rows = conn.execute(
                "SELECT phone, chat_type, COUNT(*) n, MAX(ts) last FROM wazzup_inbox "
                "WHERE phone != '' GROUP BY phone, chat_type").fetchall()
        except Exception:
            return {"rows": []}
    return {"rows": [{"phone": r[0], "chat_type": r[1], "n": r[2], "last": r[3]}
                     for r in rows]}


@app.post("/api/chat-watchdog", dependencies=AUTH)
def api_chat_watchdog():
    """Проверить прямо сейчас, кто ждёт ответа в переписке (обычно
    запускается сам каждые 10 минут)."""
    from . import autopilot
    return autopilot.chat_watchdog()


@app.get("/api/otkazy", dependencies=AUTH)
def api_otkazy():
    """Кто письменно просил снять бронь — им автоматика не пишет."""
    from . import otkaz
    return {"отказов": otkaz.feed()}


@app.get("/api/otkazy/check", dependencies=AUTH)
def api_otkazy_check(phone: str, kind: str = "nabor"):
    """Уйдёт ли письмо на этот номер прямо сейчас и если нет, то почему.

    Нужен, чтобы проверять предохранитель фактом, а не рассуждением:
    26.08 мы дважды узнавали о поломке рассылки от самих клиентов."""
    from . import wazzup
    why = wazzup.guard(phone, "проверка предохранителя", kind=kind,
                       transport="whatsapp")
    return {"телефон": phone, "уйдёт": why is None, "причина": why}


@app.post("/api/otkazy/scan", dependencies=AUTH)
def api_otkazy_scan(hours: int = 720, rebuild: bool = False):
    """Пройти по входящим и собрать отказы, которые раньше нигде не жили."""
    from . import otkaz
    return otkaz.scan(hours=hours, rebuild=rebuild)


@app.post("/api/otkazy/add", dependencies=AUTH)
def api_otkazy_add(phone: str, text: str, name: str = ""):
    """Внести отказ руками — когда клиент попросил голосом (в звонке), а не
    текстом: сканеру переписки такой отказ увидеть неоткуда."""
    from datetime import datetime as _dt
    from . import otkaz
    p = "".join(ch for ch in phone if ch.isdigit())[-10:]
    if len(p) != 10:
        return {"ok": False, "почему": "нужен телефон из 10-11 цифр"}
    with db.get_conn() as conn:
        otkaz._tables(conn)
        conn.execute(
            "INSERT OR REPLACE INTO otkazy (chat, phone, name, ts, text, source) "
            "VALUES (?,?,?,?,?,?)",
            ("7" + p, p, name, _dt.now().isoformat(timespec="seconds"),
             (text or "")[:400], "руками"))
    return {"ok": True, "phone": p, "стоп": otkaz.is_refused(p)}


@app.post("/api/otkazy/release", dependencies=AUTH)
def api_otkazy_release(chat: str):
    """Снять стоп-лист — когда клиент сам написал, что передумал.

    Только руками: автоматика не должна решать, что отказ «устарел»."""
    from . import otkaz
    otkaz.release(chat)
    return {"ok": True, "чат": chat, "статус": "снят со стоп-листа"}


@app.get("/spiski", response_class=HTMLResponse, dependencies=AUTH)
def spiski_page():
    """Три списка обзвона одной страницей — короткий адрес для админов.

    Сами списки лежат в базе знаний под /base/spisok_a|b|c, и это тот
    адрес, который никто не помнит. 26.08 задачи первичного обзвона были
    закрыты с отсылкой на app.kidsup.ru/spiski — адреса, которого не
    существовало, и администраторы упёрлись в 404 в разгар набора.

    Пересчёт трёх списков занимает полторы минуты: он поднимает всю базу
    клиентов, платежи и звонки. Телефон администратора столько не ждёт —
    в тот же день Аня увидела вместо страницы «сайт недоступен». Поэтому
    результат живёт в настройке, а не в памяти процесса: перезапуск не
    обнуляет его, и утренний заход в смену не упирается в полторы минуты
    ожидания. Протухший кэш отдаём сразу, а считаем следом — за четверть
    часа список меняется на единицы строк, и показать их минутой позже
    безопаснее, чем не показать вовсе."""
    from . import spiski as S
    import json as _j
    import threading
    import time as _t
    now = _t.time()
    raw = db.get_setting("spiski_cache", "") or ""
    stamp, data = 0.0, None
    if raw:
        try:
            box = _j.loads(raw)
            stamp, data = float(box.get("ts") or 0), box.get("data")
        except Exception:
            stamp, data = 0.0, None
    age = now - stamp
    if data and age < 900:
        return _spiski_html(S, data, int(age / 60))
    if data:
        threading.Thread(target=_spiski_refresh, daemon=True).start()
        return _spiski_html(S, data, int(age / 60))
    try:
        data = _spiski_refresh()
    except Exception as e:
        return HTMLResponse(
            f"<meta charset=utf-8><p style='font:16px system-ui;margin:40px'>"
            f"Списки не собрались: {html.escape(str(e)[:200])}<br><br>"
            f"Откройте напрямую: <a href='/base/spisok_a'>A</a> · "
            f"<a href='/base/spisok_b'>B</a> · <a href='/base/spisok_c'>C</a></p>",
            status_code=200)
    return _spiski_html(S, data, 0)


def _spiski_refresh() -> dict:
    """Пересчитать списки и положить в настройку. Зовётся и из фона."""
    import json as _j
    import time as _t
    from . import spiski as S
    data = S.collect()
    db.set_setting("spiski_cache",
                   _j.dumps({"ts": _t.time(), "data": data}, ensure_ascii=False))
    # Заодно перекладываем сами списки: счётчик на /spiski и строки в
    # /base/spisok_* обязаны сходиться. Пока файлы собирались отдельной
    # командой, на витрине стояло 82, а внутри лежало 78 — и администратор
    # не знает, какой цифре верить.
    try:
        for k in ("A", "B", "C"):
            (BASE.parent / "docs" / f"spisok_{k.lower()}.html").write_text(
                S.page(k, data[k]), encoding="utf-8")
        # Список B делится пополам между Леной и Ирой (решение владельца
        # 27.08): чёт/нечёт вместо «сверху и снизу» — так деление не
        # плывёт при пересчёте и никто не звонит по чужой половине.
        rows_a = data.get("A") or []
        rows_b = data.get("B") or []
        (BASE.parent / "docs" / "spisok_b_lena.html").write_text(
            S.page("B", rows_b[0::2]), encoding="utf-8")
        (BASE.parent / "docs" / "spisok_b_ira.html").write_text(
            S.page("B", rows_b[1::2]), encoding="utf-8")
        # Личные листы на день (владелец 27.08): одна ссылка на человека,
        # внутри сначала её доля A (тёплые), затем её доля B.
        # Схема владельца 27.08: лагерь-2026 (A) целиком у Иры, учебный
        # год (B) пополам — Лене чёт, Ире нечет.
        (BASE.parent / "docs" / "spisok_lena.html").write_text(
            S.personal("Лена", [], rows_b[0::2]), encoding="utf-8")
        (BASE.parent / "docs" / "spisok_ira.html").write_text(
            S.personal("Ира", rows_a, rows_b[1::2]), encoding="utf-8")
    except Exception as e:
        logging.getLogger("kidsup").warning("списки не перезаписались: %s", str(e)[:90])
    return data


def _spiski_html(S, data: dict, age_min: int) -> "HTMLResponse":
    """Разметка страницы списков. Вынесена, чтобы отдавать и свежий
    расчёт, и кэш одним и тем же кодом."""
    cards = []
    for k in ("A", "B", "C"):
        title, sub = S.TITLES[k]
        n = len(data.get(k) or [])
        cards.append(
            f"<a class=card href='/base/spisok_{k.lower()}'>"
            f"<div class=n>{n}</div><div class=t>{html.escape(title)}</div>"
            f"<div class=s>{html.escape(sub)}</div></a>")
    return f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Списки обзвона — KidsUP</title>
<style>
body{{font:16px/1.5 system-ui,sans-serif;margin:0;padding:20px;color:#312783;
     background:#fff;max-width:760px}}
h1{{font-size:21px;margin:0 0 6px}}
.sub{{color:#666;font-size:14px;margin-bottom:20px}}
.card{{display:block;text-decoration:none;color:inherit;border:1px solid #e6e6ef;
      border-radius:14px;padding:16px 18px;margin-bottom:12px}}
.card:active{{background:#f6f6fb}}
.n{{font-size:30px;font-weight:700;color:#1DA7E0;line-height:1}}
.t{{font-weight:600;margin-top:6px}}
.s{{color:#666;font-size:14px;margin-top:2px}}
.foot{{color:#666;font-size:14px;margin-top:22px;border-top:1px solid #e6e6ef;
      padding-top:14px}}
a.plain{{color:#1DA7E0}}
</style>
<h1>Списки обзвона</h1>
<div class=sub>Кому за август не звонили ни разу. {"Посчитано только что"
if not age_min else f"Данные {age_min} мин назад"}: позвонили — человек
из списка уходит сам.</div>
<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px">
  <a class=card style="flex:1;min-width:220px;border-color:#1DA7E0" href="/base/spisok_lena">
    <div class=t>📞 Лена — на сегодня</div>
    <div class=s>Её половины списков A и B одной страницей</div></a>
  <a class=card style="flex:1;min-width:220px;border-color:#1DA7E0" href="/base/spisok_ira">
    <div class=t>📞 Ира — на сегодня</div>
    <div class=s>Её половины списков A и B одной страницей</div></a>
</div>
{''.join(cards)}
<div class=foot>Личный лист — главная ссылка дня: внутри сначала тёплые
(лето-2026), затем прошлый учебный год. Общие A/B/C выше — для сверки.<br><br>
Очередь дня и задачи — <a class=plain href="/ochered">app.kidsup.ru/ochered</a><br>
Кому не пишем — <a class=plain href="/otkazy">app.kidsup.ru/otkazy</a></div>"""


@app.get("/otkazy", response_class=HTMLResponse, dependencies=AUTH)
def otkazy_page():
    """Отказы глазами администратора: кто, когда, дословно и на чём связали."""
    from . import otkaz
    import html as _h
    rows = otkaz.feed()
    body = []
    for r in rows:
        cls = "off" if r["снят"] else "on"
        who = r["кто"] or "—"
        ph = ("+7" + r["телефон"]) if r["телефон"] else \
             f"<span class=warn>чат без телефона ({_h.escape(str(r['чат']))})</span>"
        body.append(
            f"<tr class={cls}><td>{_h.escape(str(r['когда'])[:16])}</td>"
            f"<td>{_h.escape(who)}<br>{ph}</td>"
            f"<td class=q>«{_h.escape(str(r['цитата'])[:220])}»</td>"
            f"<td>{_h.escape(r['как связали'] or '—')}</td>"
            f"<td>{'снят' if r['снят'] else 'не пишем'}</td></tr>")
    n_live = sum(1 for r in rows if not r["снят"])
    return f"""<!doctype html><meta charset=utf-8>
<title>Отказы — KidsUP</title>
<style>
body{{font:15px/1.5 system-ui,sans-serif;margin:24px;color:#312783;max-width:1100px}}
h1{{font-size:22px}} .sub{{color:#666;margin-bottom:18px}}
table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #e6e6ef;padding:8px 10px;vertical-align:top;text-align:left}}
th{{background:#f6f6fb;font-size:13px;color:#555}}
tr.off{{opacity:.45}} .q{{color:#444;font-style:italic}}
.warn{{color:#E30613}}
</style>
<h1>Отказы: {n_live} семей, которым автоматика не пишет</h1>
<div class=sub>Сюда попадает всё, где клиент письменно просил снять бронь.
Пока строка активна, ни одна рассылка этому телефону не уходит — только
живые ответы администратора. Снять стоп-лист может только человек:
<code>POST /api/otkazy/release?chat=…</code></div>
<table><tr><th>Когда</th><th>Кто</th><th>Что написали</th>
<th>Как связали с семьёй</th><th>Статус</th></tr>
{''.join(body) or '<tr><td colspan=5>Пока пусто</td></tr>'}</table>"""


@app.get("/api/dostavka", dependencies=AUTH)
def api_dostavka(hours: int = 1):
    """Что уходит и что доходит. Первый экран при подозрении, что канал лёг."""
    from . import dostavka
    nd = dostavka.undelivered()
    from collections import Counter as _C2
    st = []
    try:
        with db.get_conn() as conn:
            st = conn.execute(
                "SELECT s.transport, COALESCE(x.status,'нет статуса') "
                "FROM wazzup_sent s LEFT JOIN wazzup_status x "
                "ON x.message_id = s.message_id").fetchall()
    except Exception:
        pass
    return {"здоровье каналов за час": dostavka.channel_health(hours),
            "какие статусы вообще приходят":
                {f"{t}/{v}": n for (t, v), n in _C2((a, b) for a, b in st).most_common(12)},
            "не дошло за 2 часа": len(nd),
            "из них важного (пойдёт СМС)":
                len([r for r in nd if r["kind"] in dostavka.CHASE_KINDS]),
            "примеры": [{"время": r["ts"][11:16], "канал": r["transport"],
                         "вид": r["kind"] or "—", "статус": r["status"],
                         "телефон": "+7" + (r["phone"] or "")[-10:]}
                        for r in nd[:8]]}


@app.get("/api/nabormail/preview", dependencies=AUTH)
def nabormail_preview(n: int = 5, kind: str = ""):
    """Показать тексты, которые уйдут следующими. Ничего не отправляет."""
    import json as _j
    from . import db as _db, nabormail
    q = _j.loads(_db.get_setting("nabormail_queue", "[]") or "[]")
    if kind:
        q = [r for r in q if (r.get("kind") or "nabor") == kind]
    out = []
    for r in q[:n]:
        txt = (r.get("text")
               or (nabormail.push_text(r["name"], r["seg"]) if r.get("push")
                   else nabormail.text_for(r["name"], r["seg"], r.get("paid", True))))
        out.append({"кому": r.get("name") or "", "телефон": "+7" + r["phone"],
                    "возраст": r.get("seg"), "вид": r.get("kind") or "nabor",
                    "второе касание": bool(r.get("push")),
                    "каналы": r.get("msgr"), "текст": txt})
    return {"всего в очереди": len(q), "показано": len(out), "письма": out}


@app.post("/api/akciya", dependencies=AUTH)
def api_akciya(rebuild: bool = False):
    """Письма записанным без оплаты: старая цена действует до 30 августа.

    rebuild=true выбрасывает уже поставленные письма и собирает заново —
    нужно, когда правится текст или цена: текст лежит прямо в строке
    очереди и сам не обновляется."""
    import json as _j
    from . import akciya, db as _db
    if rebuild:
        q = _j.loads(_db.get_setting("nabormail_queue", "[]") or "[]")
        q = [r for r in q if (r.get("kind") or "") != "akciya"]
        _db.set_setting("nabormail_queue", _j.dumps(q, ensure_ascii=False))
    return akciya.to_queue()


@app.post("/api/nabormail/tasks", dependencies=AUTH)
def nabormail_tasks(limit: int = 0):
    """Взять на себя все задачи Лизы, где нужно написать клиенту."""
    from . import nabormail
    return nabormail.tasks_to_queue(limit=limit)


@app.post("/api/nabormail/liza", dependencies=AUTH)
def nabormail_liza(reorder: bool = False):
    """Взять на себя задачи Лизы «написать клиенту».

    reorder=true — только поднять уже поставленные строки в начало очереди,
    ничего не добавляя: пригодилось 25.08, когда они попали в хвост."""
    import json as _j
    from . import db as _db, nabormail
    if reorder:
        q = _j.loads(_db.get_setting("nabormail_queue", "[]") or "[]")
        # Строки, поставленные до 25.08 14:30, несут канал WABA — её шаблон
        # ещё на модерации, и такие письма не уходят вовсе. Меняем на обычный
        # WhatsApp: это адресное сообщение, а не рассылка.
        for r in q:
            if (r.get("kind") or "") == "liza" and "wapi" in (r.get("msgr") or []):
                r["msgr"] = [t if t != "wapi" else "whatsapp" for t in r["msgr"]]
        head = [r for r in q if (r.get("kind") or "nabor") in ("confirm", "liza")]
        rest = [r for r in q if (r.get("kind") or "nabor") not in ("confirm", "liza")]
        _db.set_setting("nabormail_queue", _j.dumps(head + rest, ensure_ascii=False))
        return {"ok": True, "поднято в начало": len(head)}
    return {"ok": True, "поставлено": nabormail.liza_to_queue()}


@app.post("/api/nabormail/pause", dependencies=AUTH)
def nabormail_pause(off: bool = True):
    """Приостановить рассылку, сохранив очередь.

    25.08 Wazzup предупредил: на тарифе осталось 100 новых диалогов до конца
    месяца, дальше канал отключается. В очереди рассылки лежал ровно 101
    адресат — она бы съела весь остаток, и в неделю перед стартом занятий
    мы не смогли бы ответить ни одному новому обращению. Очередь уходит
    в резерв целиком и возвращается одной командой."""
    import json as _j
    from . import db as _db
    if off:
        q = _db.get_setting("nabormail_queue", "[]") or "[]"
        _db.set_setting("nabormail_parked", q)
        _db.set_setting("nabormail_queue", "[]")
        return {"ok": True, "в резерве": len(_j.loads(q)), "рассылка": "на паузе"}
    parked = _db.get_setting("nabormail_parked", "[]") or "[]"
    cur = _j.loads(_db.get_setting("nabormail_queue", "[]") or "[]")
    _db.set_setting("nabormail_queue", _j.dumps(cur + _j.loads(parked),
                                                ensure_ascii=False))
    _db.set_setting("nabormail_parked", "[]")
    return {"ok": True, "вернули в очередь": len(_j.loads(parked))}


@app.post("/api/nabormail/skip", dependencies=AUTH)
def nabormail_skip(uid: int, kind: str = "confirm"):
    """Пометить письмо отправленным, не отправляя. Нужно там, где сообщение
    до клиента дошло, а отметка не сохранилась: 25.08 из-за сбоя сохранения
    один клиент получил письмо десяток раз и остался без отметки — при
    пересборке очереди он снова оказался первым."""
    import json as _j
    from . import db as _db
    done = {str(x) for x in _j.loads(_db.get_setting("nabormail_done", "[]") or "[]")}
    done.add(f"{kind}:{uid}")
    _db.set_setting("nabormail_done", _j.dumps(sorted(done)))
    q = _j.loads(_db.get_setting("nabormail_queue", "[]") or "[]")
    left = [r for r in q if not (r["uid"] == uid
                                 and (r.get("kind") or "nabor") == kind)]
    _db.set_setting("nabormail_queue", _j.dumps(left, ensure_ascii=False))
    return {"ok": True, "исключён": f"{kind}:{uid}", "в очереди": len(left)}


@app.post("/api/nabormail/send-now", dependencies=AUTH)
def nabormail_send_now(batch: int = 40, dry: bool = True):
    """Прогнать очередь. Большой batch — только для сухого предпросмотра.

    При реальной отправке batch зажимается до 1: залп в 40 сообщений
    за полминуты — подпись рассылочного бота, каналы за такое банят
    (владелец останавливал это руками 25.08 на сороковом письме)."""
    from . import nabormail
    if not dry:
        batch = 1
    return nabormail.tick(dry_run=dry, batch=batch)


@app.get("/api/nabormail/state", dependencies=AUTH)
def nabormail_state():
    import json as _j
    from . import db as _db
    q = _j.loads(_db.get_setting("nabormail_queue", "[]") or "[]")
    d = _j.loads(_db.get_setting("nabormail_done", "[]") or "[]")
    from collections import Counter as _C
    dup = [k for k, n in _C(f"{r.get('kind') or 'nabor'}:{r['uid']}"
                            for r in q).items() if n > 1]
    from datetime import datetime as _dt, timedelta as _td
    return {"в очереди": len(q), "отправлено": len(d),
            "следующая отправка": _db.get_setting("nabormail_next", "") or "—",
            "сейчас МСК": (_dt.utcnow() + _td(hours=3)).isoformat(timespec="seconds"),
            "по возрастам": dict(_C(r["seg"] for r in q)),
            "дубли в очереди": dup[:10],
            "первые": [f"{r.get('kind') or 'nabor'}:{r['uid']} {r.get('name','')[:18]}"
                       for r in q[:5]]}


@app.post("/api/autopilot/missed-now", dependencies=AUTH)
def autopilot_missed_now():
    """Догон недозвонов немедленно, в процессе сервера.

    Нужен, когда часовой тик пропустил своё окно (рестарт при деплое,
    сбой Манго): отметки об отправке живут в серверной базе, и запускать
    догон надо именно здесь, иначе после запуска с другой машины сервер
    не узнает об отправленном и продублирует."""
    from . import autopilot
    before = len(autopilot.db.get_setting("_", "") or "")
    autopilot.missed_calls()
    return {"ok": True}


@app.get("/api/autopilot/log", dependencies=AUTH)
def api_autopilot_log(kind: str = "", day: str = "", limit: int = 200):
    """Что автопилот уже сделал: отметки из autopilot_state.

    Нужно, чтобы отвечать на вопрос «почему это сообщение ушло вот этому
    человеку»: автосообщения (недозвон, пропущенный, напоминание) идут мимо
    очереди рассылки, и без этой выборки их адресата не восстановить."""
    from . import autopilot
    d = day or autopilot._today().isoformat()
    with db.get_conn() as conn:
        try:
            q = "SELECT kind, key FROM autopilot_state WHERE key LIKE ?"
            args = [f"{d}%"]
            if kind:
                q += " AND kind = ?"
                args.append(kind)
            rows = conn.execute(q + " ORDER BY rowid DESC LIMIT ?",
                                (*args, max(1, min(500, limit)))).fetchall()
        except Exception:
            return {"day": d, "count": 0, "rows": []}
        out = []
        for r in rows:
            key = r["key"]
            phone = "".join(ch for ch in key.split(":")[-1] if ch.isdigit())
            name = None
            if len(phone) >= 10:
                u = conn.execute("SELECT name FROM users WHERE substr(phone,-10)=? LIMIT 1",
                                 (phone[-10:],)).fetchone()
                name = u["name"] if u else None
            out.append({"kind": r["kind"], "key": key,
                        "phone": phone if len(phone) >= 10 else None, "crm_name": name})
    return {"day": d, "count": len(out), "rows": out}


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


@app.get("/ochered", response_class=HTMLResponse, dependencies=AUTH)
async def ochered_page(manager: int = 0):
    """Очередь дня — та же, что в CRM. Строка страницы и есть задача.

    Заменяет прежние листы обзвона, которые жили рядом с задачами и не были
    с ними связаны: администратор звонил по листу, а задачи закрывал отдельно,
    и в CRM оставалось «закрыто без касания»."""
    from . import autopilot, callqueue
    mid = manager
    if not mid:
        # Кто сегодня звонит — из графика смен; тот же источник, что у /api/duty,
        # иначе очередь покажется человеку, который сегодня отдыхает.
        try:
            sched = json.loads(db.get_setting("admin_schedule") or "{}")
            v = sched.get(autopilot._today().isoformat())
            mid = (v[0] if isinstance(v, list) else v) or 0
        except Exception:
            mid = 0
    mid = mid or 232805
    try:
        rows = callqueue.collect(mid)
    except Exception as e:
        raise HTTPException(502, f"МойКласс недоступен: {e}")
    return HTMLResponse(callqueue.page(mid, rows))


@app.post("/api/queue/result", dependencies=AUTH)
async def api_queue_result(payload: dict = Body(...)):
    """Итог звонка со страницы очереди: закрыть задачу или перенести."""
    from . import callqueue
    try:
        return callqueue.apply_result(
            int(payload.get("task_id") or 0),
            str(payload.get("result") or ""),
            str(payload.get("note") or "")[:400],
            int(payload.get("manager_id") or 0))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(502, f"не удалось записать итог: {e}")


@app.get("/vstrechi", response_class=HTMLResponse)
async def vstrechi_page():
    """Три события открытия сезона — публично, без пароля.

    Ссылка идёт в WABA-шаблоны и в переписку с родителями, поэтому
    страница не может быть за админской авторизацией. Собирается
    из тех же данных, что и /enrollment, — расписание открытых уроков
    на ней всегда совпадает с настоящим."""
    f = BASE.parent / "docs" / "vstrechi.html"
    if not f.exists():
        raise HTTPException(404, "страница ещё не собрана (python3 -m app.events)")
    return HTMLResponse(f.read_text(encoding="utf-8"),
                        headers={"Cache-Control": "public, max-age=600"})


def await_page(which: str) -> HTMLResponse:
    """Отдать публичную страницу по имени — общий код для превью-роутов
    и корня публичных доменов."""
    name = {"site": "site.html", "day": "day.html", "week": "week.html"}[which]
    f = BASE / "static" / name
    return HTMLResponse(f.read_text(encoding="utf-8"))


@app.get("/site", response_class=HTMLResponse)
async def site_preview():
    """Новый сайт на техническом домене — чтобы проверять живые интеграции
    (расписание, бейджи мест, заявку) до переноса на kidsup.ru. Локально
    открытый файл для этого не годится: браузер режет запросы с file://."""
    f = BASE / "static" / "site.html"
    if not f.exists():
        raise HTTPException(404, "сайт ещё не собран (python3 site/build_site.py)")
    html = f.read_text(encoding="utf-8")
    # черновик на служебном домене не должен попадать в поиск
    html = html.replace("<head>", '<head>\n<meta name="robots" content="noindex, nofollow">', 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


def _preview(name: str) -> HTMLResponse:
    f = BASE / "static" / name
    if not f.exists():
        raise HTTPException(404, "страница ещё не собрана (python3 site/build_site.py)")
    html = f.read_text(encoding="utf-8")
    html = html.replace("<head>", '<head>\n<meta name="robots" content="noindex, nofollow">', 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/day", response_class=HTMLResponse)
async def day_preview():
    """Лендинг дня открытых дверей 30.08 — для kidsupday.ru. Здесь его можно
    проверить с живыми заявками до переноса домена."""
    return _preview("day.html")


@app.get("/week", response_class=HTMLResponse)
async def week_preview():
    """Лендинг Недели открытых уроков 31.08–06.09 — для kidsupweek.ru.
    Расписание и свободные места тянутся из /api/public/schedule."""
    return _preview("week.html")


@app.post("/api/roistat/push", dependencies=AUTH)
async def api_roistat_push(since: str = "", dry_run: int = 1, collect: int = 1):
    """Выгрузка оплат и возвратов в Roistat вручную (автопилот делает это в 21:00).
    collect=1 — сначала подобрать номера визитов из комментариев Roistat в карточках,
    без них заказы приходят в аналитику без привязки к рекламе."""
    from . import roistat as ro
    from .moyklass_client import MoyklassClient
    log = logging.getLogger("kidsup.roistat")
    since = since or (date.today() - timedelta(days=3)).isoformat()
    picked = 0
    if collect:
        try:
            mk = MoyklassClient(sync.get_api_key())
            mk.authenticate()
            picked = ro.collect_visits_from_crm(mk, since)
        except Exception as e:
            log.warning("сбор визитов не удался: %s", e)
    orders = ro.build_orders(since)
    with_visit = sum(1 for o in orders if o.get("roistat"))
    result = {"since": since, "orders": len(orders), "with_visit": with_visit,
              "visits_picked": picked, "sum": round(sum(o["price"] for o in orders), 2),
              "dry_run": bool(dry_run)}
    if dry_run:
        result["sample"] = orders[:3]
        return result
    out = ro.push(since=since, dry_run=False)
    if out.get("sent"):
        db.set_setting("roistat_pushed_day", str(date.today()))
    return {**result, **out}


@app.get("/api/audit", dependencies=AUTH)
async def api_audit(status: str = "new", limit: int = 200):
    """Лента аномалий: деньги без подписи, возвраты, скидки вне правил,
    стоп-слова в разговорах и переписке."""
    from . import audit
    return {"summary": audit.summary(), "flags": audit.feed(status, limit)}


@app.post("/api/audit/run", dependencies=AUTH)
async def api_audit_run(day: str = ""):
    from . import audit
    r = audit.daily_audit(day or None)
    return {k: (len(v) if isinstance(v, list) else v) for k, v in r.items()}


@app.post("/api/audit/resolve", dependencies=AUTH)
async def api_audit_resolve(flag_id: int, status: str = "ok"):
    from . import audit
    if status not in ("ok", "violation", "new"):
        raise HTTPException(400, "status: ok | violation | new")
    return {"ok": audit.resolve(flag_id, status)}


@app.get("/audit", response_class=HTMLResponse, dependencies=AUTH)
async def audit_page():
    """Лента аномалий человеческим лицом: красное сверху, кнопки разбора."""
    from . import audit
    s = audit.summary()
    flags = audit.feed("new", 200)
    colors = {"high": "#A3282B", "mid": "#9A5B00", "low": "#6E7264"}
    rows = []
    for f in flags:
        c = colors.get(f["level"], "#6E7264")
        rows.append(
            f'<div class="f" data-id="{f["id"]}">'
            f'<div class="dot" style="background:{c}"></div>'
            f'<div><b>{html.escape(f["title"] or "")}</b>'
            f'<div class="d">{html.escape(f["detail"] or "")}</div>'
            f'<div class="m">{html.escape(f["kind"] or "")} · {f["day"]}</div></div>'
            f'<div class="btns"><button onclick="mark({f["id"]},\'ok\')">не нарушение</button>'
            f'<button class="v" onclick="mark({f["id"]},\'violation\')">нарушение</button></div>'
            f'</div>')
    body = "".join(rows) or '<p class="empty">Лента пуста — за вчера расхождений нет.</p>'
    kinds = " · ".join(f"{k}: {v}" for k, v in (s.get("by_kind") or {}).items()) or "—"
    return HTMLResponse(f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Лента аномалий — KidsUP</title>
<style>
:root{{--paper:#FAF9F5;--ink:#22271F;--muted:#6E7264;--line:#E3E1D6;--card:#fff}}
@media (prefers-color-scheme:dark){{:root{{--paper:#151812;--ink:#E7E6DD;--muted:#9B9F90;
  --line:#2C3026;--card:#1A1E16}}}}
body{{background:var(--paper);color:var(--ink);margin:0;font:16px/1.6 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
.wrap{{max-width:56rem;margin:0 auto;padding:2rem 1rem 4rem}}
h1{{font-size:1.8rem;margin:.2rem 0 .3rem;letter-spacing:-.02em}}
.sub{{color:var(--muted);margin:0 0 1.2rem}}
.nums{{display:flex;gap:.6rem;flex-wrap:wrap;margin-bottom:1.4rem}}
.n{{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:.6rem .9rem}}
.n b{{font-size:1.4rem;display:block;line-height:1.2}}
.n span{{font-size:.8rem;color:var(--muted)}}
.f{{display:grid;grid-template-columns:10px 1fr auto;gap:.75rem;align-items:start;
  background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.8rem .95rem;margin:.5rem 0}}
.dot{{width:10px;height:10px;border-radius:99px;margin-top:.45rem}}
.d{{font-size:.9rem;color:var(--ink);margin-top:.15rem}}
.m{{font-size:.76rem;color:var(--muted);margin-top:.25rem;text-transform:uppercase;letter-spacing:.05em}}
.btns{{display:flex;gap:.35rem;flex-wrap:wrap}}
button{{font:inherit;font-size:.82rem;padding:.35rem .6rem;border-radius:7px;
  border:1px solid var(--line);background:transparent;color:var(--ink);cursor:pointer}}
button.v{{border-color:#A3282B;color:#A3282B}}
.f.done{{opacity:.35}}
.empty{{color:var(--muted)}}
</style></head><body><div class="wrap">
<h1>Лента аномалий</h1>
<p class="sub">Расхождения между тем, что записано в системах, и тем, что должно быть.
Разбирается за одно утро: «не нарушение» убирает из ленты, «нарушение» оставляет след.</p>
<div class="nums">
  <div class="n"><b>{s.get('high', 0)}</b><span>красных — деньги</span></div>
  <div class="n"><b>{s.get('mid', 0)}</b><span>жёлтых — процесс</span></div>
  <div class="n"><b>{s.get('calls_scored', 0)}</b><span>разговоров оценено</span></div>
  <div class="n"><b>{s.get('avg_call_score', 0)}</b><span>средний балл звонка из 8</span></div>
</div>
<p class="sub">{html.escape(kinds)}</p>
{body}
</div><script>
async function mark(id, st) {{
  await fetch('/api/audit/resolve?flag_id=' + id + '&status=' + st, {{method: 'POST'}});
  const el = document.querySelector('.f[data-id="' + id + '"]');
  if (el) el.classList.add('done');
}}
</script></body></html>""")


@app.get("/api/rules", dependencies=AUTH)
async def api_rules():
    """Сводка по соблюдению правил посещения."""
    from . import rules as _rules
    return {"summary": _rules.summary(), "flags": _rules.open_flags(200)}


@app.post("/api/rules/run", dependencies=AUTH)
async def api_rules_run(since: str = ""):
    from . import rules as _rules
    r = _rules.check(since or None)
    return {k: len(v) for k, v in r.items()}


@app.get("/api/rules/baseline", dependencies=AUTH)
async def api_rules_baseline(since: str = "2025-09-01"):
    """Как жил центр до правил — счёт без флагов, чтобы видеть масштаб."""
    from . import rules as _rules
    return _rules.baseline(since)


@app.get("/pravila-kontrol", response_class=HTMLResponse, dependencies=AUTH)
async def rules_page():
    """Кто как соблюдает правила посещения. Не рейтинг стыда, а список
    того, что надо поправить в карточках — с именем и абонементом."""
    from . import rules as _rules
    s = _rules.summary()
    flags = _rules.open_flags(200)
    base = _rules.baseline()
    colors = {"high": "#A3282B", "mid": "#9A5B00", "low": "#6E7264"}
    RU = {"freeze-offbook": "заморозка мимо системы", "freeze-over": "заморозка сверх нормы",
          "freeze-back": "заморозка задним числом", "makeup-late": "отработка позже месяца",
          "makeup-repeat": "повторная отработка", "makeup-post": "отработка задним числом",
          "disc-noreason": "скидка без объяснения", "disc-stack": "две скидки",
          "comp-nostreak": "компенсация без непрерывности"}
    rows = []
    for f in flags:
        c = colors.get(f["level"], "#6E7264")
        rule = RU.get((f["key"] or "").split(":")[0], "правило")
        rows.append(
            f'<div class="f" data-id="{f["id"]}">'
            f'<div class="dot" style="background:{c}"></div>'
            f'<div><b>{html.escape(f["title"] or "")}</b>'
            f'<div class="d">{html.escape(f["detail"] or "")}</div>'
            f'<div class="m">{html.escape(rule)} · {f["day"] or ""}</div></div>'
            f'<div class="btns"><button onclick="mark({f["id"]},\'ok\')">по правилам</button>'
            f'<button class="v" onclick="mark({f["id"]},\'violation\')">нарушение</button></div>'
            f'</div>')
    body = ("".join(rows) or
            '<p class="empty">Расхождений нет — правила соблюдаются.</p>')
    mgr = "".join(
        f'<tr><td>{html.escape(m["name"])}</td><td>{m["high"]}</td>'
        f'<td>{m["mid"]}</td><td>{m["low"]}</td></tr>'
        for m in s.get("managers", [])) or '<tr><td colspan="4">пока пусто</td></tr>'
    mute = " · ".join(f"{html.escape(k)} {v}" for k, v in
                      (base.get("discount_mute_by_manager") or {}).items()) or "—"
    return HTMLResponse(f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Соблюдение правил — KidsUP</title>
<style>
:root{{--paper:#FAF9F5;--ink:#22271F;--muted:#6E7264;--line:#E3E1D6;--card:#fff}}
@media (prefers-color-scheme:dark){{:root{{--paper:#151812;--ink:#E7E6DD;--muted:#9B9F90;
  --line:#2C3026;--card:#1A1E16}}}}
body{{background:var(--paper);color:var(--ink);margin:0;
  font:16px/1.6 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
.wrap{{max-width:58rem;margin:0 auto;padding:2rem 1rem 4rem}}
h1{{font-size:1.8rem;margin:.2rem 0 .3rem;letter-spacing:-.02em}}
h2{{font-size:1.05rem;margin:2rem 0 .6rem;letter-spacing:-.01em}}
.sub{{color:var(--muted);margin:0 0 1.2rem}}
.nums{{display:flex;gap:.6rem;flex-wrap:wrap;margin-bottom:1rem}}
.n{{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:.6rem .9rem}}
.n b{{font-size:1.4rem;display:block;line-height:1.2}}
.n span{{font-size:.8rem;color:var(--muted)}}
.f{{display:grid;grid-template-columns:10px 1fr auto;gap:.75rem;align-items:start;
  background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:.8rem .95rem;margin:.5rem 0}}
.dot{{width:10px;height:10px;border-radius:99px;margin-top:.45rem}}
.d{{font-size:.9rem;margin-top:.15rem}}
.m{{font-size:.76rem;color:var(--muted);margin-top:.25rem;
  text-transform:uppercase;letter-spacing:.05em}}
.btns{{display:flex;gap:.35rem;flex-wrap:wrap}}
button{{font:inherit;font-size:.82rem;padding:.35rem .6rem;border-radius:7px;
  border:1px solid var(--line);background:transparent;color:var(--ink);cursor:pointer}}
button.v{{border-color:#A3282B;color:#A3282B}}
.f.done{{opacity:.35}}
.empty{{color:var(--muted)}}
table{{border-collapse:collapse;width:100%;background:var(--card);
  border:1px solid var(--line);border-radius:10px;overflow:hidden}}
th,td{{text-align:left;padding:.5rem .8rem;border-bottom:1px solid var(--line);
  font-variant-numeric:tabular-nums}}
th{{font-size:.78rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}}
tr:last-child td{{border-bottom:none}}
.hist{{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:.9rem 1.1rem;font-size:.92rem}}
</style></head><body><div class="wrap">
<h1>Соблюдение правил</h1>
<p class="sub">Правила посещения приняты 20 августа 2026 — <a href="/base/pravila_kidsup">текст здесь</a>.
Проверки идут по данным МойКласс каждое утро. Задним числом никого не судим:
в ленту попадает только то, что случилось после 20 августа.</p>
<div class="nums">
  <div class="n"><b>{s.get('high', 0)}</b><span>красных</span></div>
  <div class="n"><b>{s.get('mid', 0)}</b><span>жёлтых</span></div>
  <div class="n"><b>{s.get('low', 0)}</b><span>серых</span></div>
</div>
{body}
<h2>По администраторам за 30 дней</h2>
<table><tr><th>кто</th><th>красных</th><th>жёлтых</th><th>серых</th></tr>{mgr}</table>
<h2>Что было до правил — для понимания масштаба</h2>
<div class="hist">
За учебный год 2025/26 продано <b>{base.get('subs', 0)}</b> абонементов.
Из них <b>{base.get('discount_big', 0)}</b> дешевле чем −10%, и у
<b>{base.get('discount_mute', 0)}</b> в комментарии нет причины ({mute}).
Заморозок в тексте комментария — <b>{base.get('freeze_in_text', 0)}</b>,
оформленных полями «заморозить с/по» — <b>{base.get('freeze_in_system', 0)}</b>:
поэтому «две недели за год» до сих пор посчитать было нельзя.
Отработок — <b>{base.get('makeups', 0)}</b>, из них
<b>{base.get('makeup_late', 0)}</b> закрыты позже месяца.
До 20 августа этих правил не было, поэтому ничего из перечисленного
нарушением не считается — это отправная точка.
</div>
</div><script>
async function mark(id, st) {{
  await fetch('/api/audit/resolve?flag_id=' + id + '&status=' + st, {{method: 'POST'}});
  const el = document.querySelector('.f[data-id="' + id + '"]');
  if (el) el.classList.add('done');
}}
</script></body></html>""")


@app.post("/api/fit/run", dependencies=AUTH)
async def api_fit_run():
    """Состав групп: возраст, уровень, перебор, неотмеченная посещаемость."""
    from . import crmcheck
    r = crmcheck.fit_check()
    return {k: (v if k.startswith("_") else len(v)) for k, v in r.items()}


@app.get("/api/debts", dependencies=AUTH)
async def api_debts():
    from . import crmcheck
    b = crmcheck.debt_buckets()
    return {"totals": {k: {"n": len(v), "sum": round(sum(x["balance"] for x in v))}
                       for k, v in b.items()}, "buckets": b}


@app.post("/api/money/run", dependencies=AUTH)
async def api_money_run():
    from . import rules as _rules
    return {k: len(v) for k, v in _rules.money_check().items()}


@app.get("/api/dolgi", dependencies=AUTH)
async def api_dolgi():
    from . import crmcheck
    return crmcheck.debts_report()


@app.get("/dolgi", response_class=HTMLResponse, dependencies=AUTH)
async def money_page():
    """Долги: по абонементам и по занятиям, разложенные по тому, что с ними
    реально можно сделать. Данные тянутся из МойКласс при каждом открытии."""
    from . import crmcheck
    d = crmcheck.debts_report()
    if not d.get("ready"):
        return HTMLResponse("<p>Нет ключа МойКласс</p>")
    t = d["totals"]
    rows, nobill = d["rows"], d["nobill"]
    BUCKET = {
        "собрать": ("Собрать", "#2E6B2A",
                    "Абонемент свежий или семья ещё с нами — эти деньги живые."),
        "разобрать": ("Разобрать руками", "#9A5B00",
                      "Пограничные: могла быть оплата мимо CRM или ошибка проведения."),
        "списать": ("Списать", "#6E7264",
                    "Абонементы прошлых лет, израсходованы, семьи в архиве. "
                    "Не вернуть — но пока висят, баланс врёт."),
    }
    def money(v):
        return f"{v:,.0f}".replace(",", " ") + " ₽"
    blocks = []
    for b, (title, color, why) in BUCKET.items():
        sel = [r for r in rows if r["bucket"] == b]
        if not sel:
            continue
        trs = "".join(
            f'<tr><td><b>{html.escape(str(r["name"]))}</b>'
            f'<div class="m">{html.escape(r["kind"])} · счёт от {r["date"]}'
            + (f' · занятий {r["used"]}/{r["quota"]}' if r["quota"] else "")
            + (f' · оформил {html.escape(r["manager"])}' if r["manager"] != "—" else "")
            + f'</div></td>'
            f'<td class="num">{money(r["debt"])}</td>'
            f'<td class="num">{r["overdue"]} дн.</td>'
            f'<td class="w"><a href="tel:+7{r["phone"]}">+7{r["phone"]}</a></td></tr>'
            for r in sel)
        blocks.append(
            f'<section><h2><span class="chip" style="background:{color}"></span>'
            f'{title} — {len(sel)} на {money(sum(r["debt"] for r in sel))}</h2>'
            f'<p class="sub">{why}</p>'
            f'<div class="tbl"><table>'
            f'<tr><th>кто и за что</th><th class="num">долг</th>'
            f'<th class="num">просрочка</th><th>телефон</th></tr>{trs}</table></div></section>')
    nb = ""
    if nobill:
        nb = ('<section><h2>Занятия без счёта — ' + str(len(nobill)) + '</h2>'
              '<p class="sub">Ребёнок был на занятии, а счёта нет вообще: ни абонемента, '
              'ни разовой оплаты. Этих денег не ждёт даже система — их надо не собирать, '
              'а сначала провести.</p><div class="tbl"><table>'
              '<tr><th>ребёнок</th><th>дата</th><th>группа</th></tr>'
              + "".join(f'<tr><td>{html.escape(str(x["name"] or x["user_id"]))}</td>'
                        f'<td class="w">{x["date"]}</td>'
                        f'<td>{html.escape(str(x["class"] or ""))}</td></tr>'
                        for x in nobill) + '</table></div></section>')
    return HTMLResponse(f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Долги по абонементам и занятиям</title><style>
:root{{--paper:#FCFBF7;--ink:#1B1D2B;--muted:#5F6478;--line:#E7E4DA;--card:#fff;--fill:#F3F1E9}}
@media (prefers-color-scheme:dark){{:root{{--paper:#14151D;--ink:#E7E6EC;--muted:#9A9EAE;
  --line:#292B37;--card:#1B1D26;--fill:#20222C}}}}
*{{box-sizing:border-box}}
body{{background:var(--paper);color:var(--ink);margin:0;
  font:16px/1.6 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
.wrap{{max-width:62rem;margin:0 auto;padding:2rem 1rem 4rem}}
h1{{font-size:1.9rem;margin:.2rem 0 .3rem;letter-spacing:-.025em}}
h2{{font-size:1.12rem;margin:0 0 .3rem;display:flex;align-items:center;gap:.5rem}}
.chip{{width:10px;height:10px;border-radius:99px;display:inline-block}}
.sub{{color:var(--muted);margin:0 0 .9rem;max-width:46rem;font-size:.92rem}}
section{{margin-top:2.2rem;padding-top:1.4rem;border-top:1px solid var(--line)}}
.nums{{display:grid;gap:.6rem;grid-template-columns:repeat(auto-fit,minmax(10rem,1fr));margin:1.2rem 0}}
.n{{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:.7rem .9rem}}
.n b{{font-size:1.45rem;display:block;line-height:1.15;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums}}
.n span{{font-size:.78rem;color:var(--muted);display:block;margin-top:.1rem}}
.tbl{{overflow-x:auto;border:1px solid var(--line);border-radius:11px;background:var(--card)}}
table{{border-collapse:collapse;width:100%;font-size:.91rem;min-width:34rem}}
th{{background:var(--fill);text-align:left;font-size:.7rem;letter-spacing:.06em;
  text-transform:uppercase;color:var(--muted);padding:.5rem .8rem;white-space:nowrap}}
td{{padding:.55rem .8rem;border-top:1px solid var(--line);vertical-align:top}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
td.w{{white-space:nowrap}}
.m{{font-size:.78rem;color:var(--muted);margin-top:.1rem}}
.how{{background:var(--card);border:1px solid var(--line);border-left:3px solid #9A5B00;
  border-radius:0 10px 10px 0;padding:.9rem 1.1rem;margin:1.2rem 0;max-width:46rem;font-size:.93rem}}
.how ol{{margin:.5rem 0;padding-left:1.2rem}} .how li{{margin:.3rem 0}}
a{{color:inherit}}
</style></head><body><div class="wrap">
<h1>Долги по абонементам и занятиям</h1>
<p class="sub">Данные тянутся из МойКласс при каждом открытии страницы —
на {d["as_of"]}. Источник: счета с датой оплаты, поэтому просрочка считается точно.</p>
<div class="nums">
  <div class="n"><b>{money(t["всего"]["sum"])}</b><span>всего, {t["всего"]["n"]} счетов</span></div>
  <div class="n"><b>{money(t["абонементы"]["sum"])}</b><span>по абонементам — {t["абонементы"]["n"]}</span></div>
  <div class="n"><b>{money(t["занятия"]["sum"])}</b><span>по занятиям — {t["занятия"]["n"]}</span></div>
  <div class="n"><b>{money(t["собрать"]["sum"])}</b><span>можно собрать</span></div>
</div>
<div class="how"><b>Откуда берётся долг.</b>
<ol>
<li>Абонемент можно завести, не проведя оплату — карточка выглядит нормально.</li>
<li>Отметка посещения списывает занятие с абонемента и ставит «оплачено».
Это значит «списано с абонемента», а не «деньги получены».</li>
<li>Если абонемент не оплачен, каждое занятие уводит баланс в минус.
Ошибки на экране не появляется — долг растёт молча.</li>
</ol></div>
{"".join(blocks)}
{nb}
</div></body></html>""")


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


@app.post("/events/sms")
async def mango_sms_status(request: Request):
    """Статус доставки СМС от Манго (уведомление events/sms).

    Другого способа узнать судьбу СМС нет: команда отправки отвечает лишь
    «принято», а 27.08 выяснилось, что МТС молча фильтрует часть массовых
    отправок (у владельца из двух одинаковых СМС дошла одна). Адрес этого
    обработчика прописывается в ЛК Манго как «адрес внешней системы» —
    Манго сама добавляет суффикс /events/sms.
    Коды reason: 1000 доставлено; 43xx — нет (4300 не удалось, 4301
    устарело, 4391 утеряно оператором, 4392 отклонено оператором,
    4393 отменено)."""
    import hashlib as _h
    import json as _j
    from datetime import datetime as _dt
    try:
        form = await request.form()
        raw = form.get("json") or "{}"
        key = form.get("vpbx_api_key") or ""
        sign = form.get("sign") or ""
        data = _j.loads(raw)
    except Exception:
        return {"ok": True}
    our_key = db.get_setting("mango_key") or ""
    salt = db.get_setting("mango_salt") or ""
    valid = (key == our_key and sign == _h.sha256(
        (our_key + raw + salt).encode()).hexdigest())
    cid = str(data.get("command_id") or "")
    phone = ""
    if cid.startswith("sms") and "_" in cid:
        digits = "".join(ch for ch in cid.split("_")[0] if ch.isdigit())
        phone = digits[-11:] if len(digits) >= 11 else digits
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS sms_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, command_id TEXT,
            phone TEXT, reason INTEGER, valid_sign INTEGER)""")
        conn.execute(
            "INSERT INTO sms_status (ts, command_id, phone, reason, valid_sign)"
            " VALUES (?,?,?,?,?)",
            (_dt.now().isoformat(timespec="seconds"), cid[:128], phone,
             int(data.get("reason") or 0), int(valid)))
    return {"ok": True}


@app.get("/api/sms-report", dependencies=AUTH)
def api_sms_report(day: str = ""):
    """Сводка доставки СМС по вебхук-статусам Манго (за день, МСК)."""
    from datetime import datetime as _dt
    d = day or _dt.now().strftime("%Y-%m-%d")
    with db.get_conn() as conn:
        try:
            rows = conn.execute(
                "SELECT phone, reason, ts FROM sms_status WHERE ts LIKE ? "
                "ORDER BY ts", (d + "%",)).fetchall()
        except Exception:
            rows = []
    dostavleno = [r[0] for r in rows if r[1] == 1000]
    problemy = [{"phone": r[0], "code": r[1], "ts": r[2]}
                for r in rows if r[1] != 1000]
    return {"день": d, "событий": len(rows),
            "доставлено": len(dostavleno),
            "не_доставлено": len(problemy), "проблемные": problemy[:200],
            "подсказка": ("пусто = в ЛК Манго не прописан адрес внешней "
                          "системы https://app.kidsup.ru — см. Настройки → "
                          "Интеграции → API")}


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
