"""Автопилот: фоновые сценарии, работающие пока приложение запущено.

Сценарии (все проверяются раз в минуту, каждый — по своему расписанию):
  speed_to_lead   каждые 3 мин  новая заявка/клиент в МойКласс → срочная
                                задача дежурному админу «позвонить за 5 минут»
  no_show         каждый час    записан на пробное, занятие прошло, не пришёл →
                                задача админу + WhatsApp «перенесём?»
  missed_calls    каждый час    недозвоны за день (Mango) → WhatsApp-догон
                  (10:00–20:00) (не чаще одного сообщения номеру в день)
  morning_tasks   в 08:00       порция задач на день каждому звонящему админу
                                из его очереди (сегменты A/B/C по фактическим
                                визитам, семьи не разрываются)
  daily_digest    в 20:00       сводка руководителю: звонки по админам, записи
                                за день, задачи, недозвоны

Настройки (таблица settings):
  autopilot            "on"/"off" — главный выключатель (по умолчанию off)
  call_admins          JSON: [{"managerId": 1, "name": "Админ 1"}, ...] —
                       звонящие админы МойКласс в порядке очередей A/B/C
  digest_phone         телефон руководителя для вечерней сводки (WhatsApp)
  daily_tasks_per_admin  сколько задач в утренней порции (по умолчанию 40)
  wazzup_dry_run       "1" — сообщения только логируются (по умолчанию "1")

Состояние (таблица autopilot_state) защищает от повторов: одна задача на
заявку, одно сообщение недозвону в день и т.п.
"""

import json
import logging
import threading
import time
from datetime import date, datetime, timedelta

from . import db, mango, wazzup
from .moyklass_client import MoyklassClient
from .sync import get_api_key

log = logging.getLogger("kidsup.autopilot")

# терминальные/нерабочие статусы: таким утренние задачи не создаём
SKIP_STATES = {125952, 125953, 125954, 125955, 125957, 146950,
               146328, 215202, 146330, 146513}
NEW_JOIN_STATUS = 50509  # «1. Новая заявка»


def _client() -> MoyklassClient:
    return MoyklassClient(get_api_key())


def _admins() -> list[dict]:
    try:
        return json.loads(db.get_setting("call_admins") or "[]")
    except ValueError:
        return []


def _mark(kind: str, key: str) -> bool:
    """True, если ключ новый (и помечает его). False — уже обрабатывали."""
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS autopilot_state (
            kind TEXT, key TEXT, ts TEXT, PRIMARY KEY (kind, key))""")
        cur = conn.execute(
            "INSERT OR IGNORE INTO autopilot_state VALUES (?, ?, ?)",
            (kind, key, datetime.now().isoformat(timespec="seconds")))
        return cur.rowcount > 0


def _task(mk: MoyklassClient, manager_id: int, user_id: int | None,
          body: str, day: date | None = None) -> None:
    d = (day or date.today()).isoformat()
    payload = {"body": body, "beginDate": f"{d} 09:00", "endDate": f"{d} 20:00",
               "isAllDay": True, "managerId": manager_id}
    if user_id:
        payload["userId"] = user_id
    mk.post("/v1/company/tasks", payload)


def _wa(phone: str, text: str, mode: str = "broadcast") -> None:
    """broadcast — во все мессенджеры (WhatsApp+Telegram+MAX): у кого какой есть."""
    dry = db.get_setting("wazzup_dry_run", "1") == "1"
    try:
        for line in wazzup.send(phone, text, mode=mode, dry_run=dry):
            log.info("wazzup: %s", line)
    except Exception as e:  # ключа может не быть — сценарии не должны падать
        log.warning("wazzup недоступен: %s", e)


# --- сценарии ------------------------------------------------------------

def speed_to_lead(mk: MoyklassClient) -> None:
    admins = _admins()
    if not admins:
        return
    duty = admins[date.today().toordinal() % len(admins)]  # дежурный по дню
    today = date.today().isoformat()
    joins = mk.fetch_all("/v1/company/joins", ["joins"], params={
        "statusId": NEW_JOIN_STATUS, "createdAt": [today, today]})
    for j in joins:
        if not _mark("lead_task", str(j["id"])):
            continue
        _task(mk, duty["managerId"], j.get("userId"),
              "🔥 НОВАЯ ЗАЯВКА — позвонить в течение 5 минут! "
              "Свежий лид конвертируется в разы лучше.")
        log.info("speed_to_lead: задача по заявке %s → %s", j["id"], duty["name"])


def no_show(mk: MoyklassClient) -> None:
    admins = _admins()
    now = datetime.now()
    recs = mk.fetch_all("/v1/company/lessonRecords", ["lessonRecords"], params={
        "date": date.today().isoformat(), "test": "true", "visit": "false",
        "includeLessons": "true"})
    for r in recs:
        lesson = r.get("lesson") or {}
        end = f"{lesson.get('date', '')} {lesson.get('endTime', '23:59')}"
        try:
            if datetime.strptime(end, "%Y-%m-%d %H:%M") > now:
                continue  # занятие ещё не закончилось
        except ValueError:
            continue
        if not _mark("no_show", str(r["id"])):
            continue
        uid = r.get("userId")
        if admins:
            _task(mk, admins[0]["managerId"], uid,
                  "Не пришёл на пробное сегодня — позвонить и перенести "
                  "(скрипт: «перенесём на другой день?»)")
        user = mk.get(f"/v1/company/users/{uid}")
        phone = user.get("phone")
        if phone:
            _wa(phone, f"{user.get('name', '')}, добрый день! Это KidsUP. "
                       "Мы ждали вас сегодня на пробном занятии — ничего страшного, "
                       "что не получилось! Давайте подберём другой день? "
                       "Ответьте на это сообщение, и мы всё устроим 🌿")
        log.info("no_show: запись %s, ученик %s", r["id"], uid)


def missed_calls() -> None:
    today = date.today().isoformat()
    for m in mango.missed():
        phone = m["phone"]
        if len(phone) < 10 or not _mark("missed_wa", f"{today}:{phone}"):
            continue
        _wa(phone, "Здравствуйте! Это детский центр KidsUP (м. Бульвар "
                   "Рокоссовского). Звонили вам по поводу занятий 2026/27 "
                   "учебного года — идёт набор групп. Когда удобно созвониться? "
                   "Или просто ответьте здесь — подберём группу в переписке 😊")


def _queues() -> dict[int, list[int]]:
    """Очереди обзвона по фактическим визитам: admin_index -> [user_id, ...]."""
    with db.get_conn() as conn:
        base = """WITH v AS (SELECT lr.user_id u, l.date d FROM lesson_records lr
                  JOIN lessons l ON l.id = lr.lesson_id WHERE lr.visit = 1)"""
        summer = {r[0] for r in conn.execute(base + " SELECT DISTINCT u FROM v WHERE d>='2026-06-01'")}
        y2526 = {r[0] for r in conn.execute(base + " SELECT DISTINCT u FROM v WHERE d>='2025-09-01' AND d<'2026-06-01'")} - summer
        y2425 = {r[0] for r in conn.execute(base + " SELECT DISTINCT u FROM v WHERE d>='2024-09-01' AND d<'2025-09-01'")} - summer - y2526
        phones = dict(conn.execute("SELECT id, phone FROM users"))
    # волна по календарю: пн-вт стартовой недели — A, дальше B, с 17.08 — C
    today = date.today()
    if today <= date(2026, 8, 11):
        wave = summer
    elif today <= date(2026, 8, 16):
        wave = y2526 | summer          # добираем хвост A
    else:
        wave = y2425 | y2526 | summer  # добираем хвосты
    fams: dict[str, list[int]] = {}
    for u in sorted(wave):
        fams.setdefault(phones.get(u) or f"x{u}", []).append(u)
    n = max(1, len(_admins()))
    out: dict[int, list[int]] = {}
    for i, fam in enumerate(fams.values()):
        out.setdefault(i % n, []).extend(fam)
    return out


def morning_tasks(mk: MoyklassClient) -> None:
    admins = _admins()
    if not admins:
        return
    per_admin = int(db.get_setting("daily_tasks_per_admin", "40") or 40)
    queues = _queues()
    for idx, adm in enumerate(admins):
        made = 0
        for uid in queues.get(idx, []):
            if made >= per_admin:
                break
            if not _mark("call_task", str(uid)):
                continue  # задача этому клиенту уже создавалась
            user = mk.get(f"/v1/company/users/{uid}")
            if user.get("clientStateId") in SKIP_STATES:
                continue
            _task(mk, adm["managerId"], uid,
                  "Обзвон набора 2026/27: открой карточку, прочитай подсказку 🎯, "
                  "позвони кнопкой и поставь статус по итогу.")
            made += 1
        log.info("morning_tasks: %s — %d задач", adm["name"], made)


def daily_digest() -> None:
    day = date.today().isoformat()
    try:
        rows = mango.calls(datetime.now().replace(hour=0, minute=0, second=0),
                           datetime.now())
        rep = mango.report(rows=rows)
        missed_n = len(mango.missed(rows=rows))
    except Exception as e:
        rep, missed_n = [], -1
        log.warning("digest: mango недоступен: %s", e)
    with db.get_conn() as conn:
        joined = conn.execute(
            "SELECT COUNT(*) FROM joins WHERE created_at >= ?", (day,)).fetchone()[0]
    lines = [f"📊 KidsUP — сводка за {day}"]
    for s in rep:
        lines.append(f"• {s['admin']}: {s['attempts']} звонков, "
                     f"{s['answered']} дозвонов, {s['talk_min']} мин")
    if not rep:
        lines.append("• звонков через АТС сегодня не было")
    lines.append(f"Новых записей в группы: {joined}")
    if missed_n >= 0:
        lines.append(f"Недозвонов в догоне: {missed_n}")
    text = "\n".join(lines)
    log.info("digest:\n%s", text)
    phone = db.get_setting("digest_phone")
    if phone:
        _wa(phone, text)


# --- планировщик ---------------------------------------------------------

def _loop() -> None:
    last3 = 0.0
    while True:
        try:
            if db.get_setting("autopilot", "off") != "on":
                time.sleep(60)
                continue
            now = datetime.now()
            hhmm = now.strftime("%H:%M")
            if time.monotonic() - last3 >= 180:
                last3 = time.monotonic()
                mk = _client()
                try:
                    speed_to_lead(mk)
                    if now.minute < 3 and 9 <= now.hour <= 20:  # раз в час
                        no_show(mk)
                finally:
                    mk.close()
                if now.minute < 3 and 10 <= now.hour <= 20:
                    missed_calls()
            if hhmm == "08:00" and _mark("morning", str(date.today())):
                mk = _client()
                try:
                    morning_tasks(mk)
                finally:
                    mk.close()
            if hhmm == "20:00" and _mark("digest", str(date.today())):
                daily_digest()
        except Exception:
            log.exception("autopilot: ошибка цикла")
        time.sleep(60)


def start() -> None:
    threading.Thread(target=_loop, daemon=True, name="autopilot").start()
    log.info("автопилот запущен (включение: настройка autopilot=on)")
