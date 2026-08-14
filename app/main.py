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


@app.get("/enrollment", response_class=HTMLResponse, dependencies=AUTH)
def enrollment_page(request: Request, course: str = "", day: str = "", free: int = 0):
    """Набор 2026/27: все группы «2627_…» с заполненностью — рабочий экран админа."""
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
        groups.append({
            "name": name, "course": r["course"] or (parts[1] if len(parts) > 1 else "?"),
            "days": g_days, "buffer": buffer,
            "day": " · ".join(DAY_LABEL[d] for d in g_days) or "—",
            "time": " · ".join(times[:2]) or "—",
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
    return render(request, "enrollment.html", active="enrollment",
                  groups=groups, summary=sorted(summary.values(), key=lambda s: -s["fill_pct"]),
                  courses=courses_list, days=days_list, course=course, day=day, free=free)


@app.get("/settings", response_class=HTMLResponse, dependencies=AUTH)
def settings_page(request: Request, msg: str = "", ok: int = 1):
    key = sync.get_api_key()
    masked = (key[:4] + "•" * 8 + key[-4:]) if len(key) > 8 else ("задан" if key else "")
    return render(request, "settings.html", active="settings",
                  masked_key=masked,
                  key_from_env=bool(config.ENV_API_KEY),
                  history_months=db.get_setting(
                      "history_months", str(config.DEFAULT_HISTORY_MONTHS)),
                  msg=msg, ok=ok,
                  counts=db.table_counts())


@app.post("/settings", dependencies=AUTH)
def settings_save(api_key: str = Form(""), history_months: str = Form("24")):
    if api_key.strip():
        db.set_setting("moyklass_api_key", api_key.strip())
    if history_months.strip().isdigit():
        db.set_setting("history_months", history_months.strip())
    return RedirectResponse("/settings?msg=Настройки+сохранены", status_code=303)


@app.post("/settings/test", dependencies=AUTH)
def settings_test():
    key = sync.get_api_key()
    if not key:
        return RedirectResponse("/settings?ok=0&msg=Сначала+укажите+API-ключ",
                                status_code=303)
    ok, message = sync.test_connection(key)
    return RedirectResponse(
        f"/settings?ok={1 if ok else 0}&msg={message}", status_code=303)


# --- Захват лидов с офлайн-каналов (QR) ------------------------------------

LEAD_TITLES = {
    "lottery": ("Участвуйте в лотерее! 🎁",
                "Заполните анкету — и ребёнок получит подарок. А мы подберём занятия по вашему запросу."),
    "promoter": ("Запишитесь на бесплатную диагностику",
                 "Оставьте контакты — подберём программу под вашего ребёнка и подарим диагностику."),
    "yantar": ("KidsUP — прямо напротив «Янтаря»",
               "Оставьте контакты и получите бесплатную диагностику для ребёнка."),
    "flyer": ("Набор на 2026/27 учебный год",
              "Оставьте контакты — расскажем о группах и подарим бесплатную диагностику."),
    "screen": ("Набор в детский центр и сад KidsUP",
               "Оставьте контакты — подберём занятия и подарим диагностику."),
    "metro": ("Промокод МЕТРО: −10% на первый абонемент",
              "Оставьте контакты — закрепим скидку и подберём группу."),
    "trailer": ("KidsUP: детский центр и сад рядом с домом",
                "Оставьте контакты — расскажем о наборе и подарим диагностику."),
    "chat": ("KidsUP для семей вашего ЖК",
             "Оставьте контакты — подберём занятия и подарим бесплатную диагностику."),
    "partner": ("Специальное предложение от партнёра KidsUP",
                "Оставьте контакты — активируем ваш бонус и подберём занятия."),
}


@app.get("/q/{source}", response_class=HTMLResponse)
def lead_form(request: Request, source: str, promo: str = ""):
    """Публичная форма по QR-коду. Пример: /q/lottery?promo=DR2908"""
    title, subtitle = LEAD_TITLES.get(source, LEAD_TITLES["promoter"])
    return templates.TemplateResponse(request, "lead_form.html", {
        "source": source, "promo": promo, "title": title, "subtitle": subtitle,
        "interests": leads.INTERESTS, "done": False, "error": "",
    })


@app.post("/lead", response_class=HTMLResponse)
async def lead_submit(request: Request):
    form = await request.form()
    data = {
        "source": form.get("source", "other"),
        "promo": form.get("promo", ""),
        "parent_name": form.get("parent_name", ""),
        "phone": form.get("phone", ""),
        "child_name": form.get("child_name", ""),
        "child_age": form.get("child_age", ""),
        "interests": form.getlist("interests"),
        "comment": form.get("comment", ""),
        "consent_pd": form.get("consent_pd"),
        "consent_ads": form.get("consent_ads"),
    }
    if not data["consent_pd"] or not data["phone"].strip():
        title, subtitle = LEAD_TITLES.get(data["source"], LEAD_TITLES["promoter"])
        return templates.TemplateResponse(request, "lead_form.html", {
            "source": data["source"], "promo": data["promo"], "title": title,
            "subtitle": subtitle, "interests": leads.INTERESTS, "done": False,
            "error": "Укажите телефон и подтвердите согласие на обработку данных.",
        })
    lead_id = leads.save_lead(data)
    # пытаемся сразу отправить в CRM, но форму не роняем при ошибке
    try:
        leads.push_to_crm(lead_id)
    except Exception:  # noqa: BLE001
        pass
    return templates.TemplateResponse(request, "lead_form.html", {
        "done": True, "parent_name": data["parent_name"],
        "interests": leads.INTERESTS, "source": data["source"],
        "promo": data["promo"], "title": "", "subtitle": "", "error": "",
    })


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
            echoes.append((ts, phone, str(msg.get("messageId") or "")))
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
            message_id TEXT UNIQUE)""")
        if rows:
            conn.executemany(
                "INSERT OR IGNORE INTO wazzup_inbox (ts, phone, chat_type, text, message_id) "
                "VALUES (?, ?, ?, ?, ?)", rows)
        if echoes:
            conn.executemany(
                "INSERT OR IGNORE INTO wazzup_outbox (ts, phone, message_id) "
                "VALUES (?, ?, ?)", echoes)


def _wazzup_process(payload: dict) -> None:
    try:
        _inbox_store(payload)
    except Exception:
        logging.getLogger("kidsup.wazzup").exception("inbox: не сохранилось")
    if not wazzup_forward(payload):
        _fwd_store(payload)  # автопилот дошлёт позже
        logging.getLogger("kidsup.wazzup").warning("пересылка в МойКласс не удалась — в очередь")
    _wazzup_tag(payload)


APP_VERSION = "2026-08-14.02"  # видно в /api/health — чтобы проверять, что обновление применилось


@app.get("/api/health")
async def health():
    from . import autopilot
    today = autopilot._today().isoformat()
    return {"ok": True, "version": APP_VERSION,
            "msk": autopilot._now().isoformat(timespec="seconds"),
            "morning_done": autopilot._has_mark("morning", today)}


SETTABLE = {"admin_schedule", "daily_tasks_per_admin", "broadcast_per_hour", "broadcast_transports",
            "wazzup_dry_run", "digest_phone", "autopilot", "missed_reject_attempts", "wa_daily_cap", "wa_per_hour", "wa_senders",
            "broadcast_until"}


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
