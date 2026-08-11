"""Автопилот: фоновые сценарии, работающие пока приложение запущено.

Сценарии (все проверяются раз в минуту, каждый — по своему расписанию):
  speed_to_lead   каждые 3 мин  новая заявка/клиент в МойКласс → срочная
                                задача дежурному админу «позвонить за 5 минут»
  no_show         каждый час    записан на пробное, занятие прошло, не пришёл →
                                задача админу + WhatsApp «перенесём?»
  missed_calls    каждый час    недозвоны за день (Mango) → WhatsApp-догон
                  (10:00–20:00) (не чаще одного сообщения номеру в день)
  morning_tasks   с 08:00 МСК   порция задач на день каждому звонящему админу
                                из его очереди (сегменты A/B/C по фактическим
                                визитам, семьи не разрываются)
  daily_digest    в 20:00 МСК   сводка руководителю: звонки по админам, записи
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
from zoneinfo import ZoneInfo

from . import db, mango, wazzup
from .moyklass_client import MoyklassClient
from .sync import get_api_key

MSK = ZoneInfo("Europe/Moscow")


def _now() -> datetime:
    """Московское время — расписание не зависит от таймзоны сервера."""
    return datetime.now(MSK)


def _today() -> date:
    return _now().date()

log = logging.getLogger("kidsup.autopilot")

# статусы, по которым утренние задачи не создаём: терминальные, «не звонить»
# и активная воронка, где следующий шаг назначает сам админ
SKIP_STATES = {125954, 125955, 125957,            # Некачественный, Клиент, Отказ
               146328, 215202, 146330, 146513,    # 0.1-0.2, переехал, 13+
               146950, 125952, 125953, 345767}    # думает/записался/посетил/думает-2
NEW_JOIN_STATUS = 50509   # «1. Новая заявка»
ST_NEDOZVON = 345768      # «2. Недозвон (в работе)»
ST_REJECT = 125957        # «Отказ»


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
            (kind, key, _now().isoformat(timespec="seconds")))
        return cur.rowcount > 0


def _task(mk: MoyklassClient, manager_id: int, user_id: int | None,
          body: str, day: date | None = None) -> None:
    d = (day or _today()).isoformat()
    payload = {"body": body, "beginDate": f"{d}T09:00:00+03:00",
               "endDate": f"{d}T20:00:00+03:00",
               "isAllDay": True, "managerIds": [manager_id]}
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


def _age_years(user: dict) -> float | None:
    for a in user.get("attributes") or []:
        if a.get("attributeAlias") == "birthday" and a.get("value"):
            try:
                y, m, d0 = map(int, a["value"][:10].split("-"))
                t = _today()
                return (t.year - y) + (t.month - m) / 12
            except ValueError:
                return None
    return None


def _hint_for_lead(mk: MoyklassClient, user_id: int, join: dict) -> None:
    """Подсказка 🎯 в карточку нового лида: возраст + программа заявки."""
    user = mk.get(f"/v1/company/users/{user_id}")
    ag = _age_years(user)
    with db.get_conn() as conn:
        row = conn.execute("""SELECT co.name FROM classes cl
            LEFT JOIN courses co ON co.id = cl.course_id WHERE cl.id = ?""",
            (join.get("classId"),)).fetchone()
    course = row[0] if row else None
    if ag is None:
        main = "уточнить возраст; до 3 лет — МсМ/раннее развитие, 4–6 — подготовка к школе + английский, 7+ — английский/менталка/скорочтение"
    elif ag < 3:
        main = "Музыка с мамой (по возрасту) или Раннее развитие ур.1"
    elif ag < 4:
        main = "Английский детский сад (гр. 3–4) или Раннее развитие ур.2; вторым — танцы/ИЗО"
    elif ag < 5.5:
        main = "Подготовка к школе + английский; вторым — менталка 4–7, шахматы"
    elif ag < 7:
        main = "Нулевой класс (полный день) или ПШ; вторым — менталка/скорочтение"
    else:
        main = "Английский по уровню + менталка 7–12/скорочтение; вторым — шахматы/робо"
    age_s = f"{ag:.1f} лет" if ag else "возраст неизвестен"
    lines = [
        "🎯 ПОДСКАЗКА ДЛЯ ЗВОНКА (новый лид, сформирована автоматически)",
        f"Возраст: {age_s}." + (f" Заявка на: {course}." if course else " Источник заявки — уточните в карточке."),
        f"Предлагать: {main}.",
        "Не забудьте акцию: до 30.08 фиксируем цену прошлого года на весь учебный год.",
    ]
    mk.post("/v1/company/userComments",
            {"userId": user_id, "comment": "\n".join(lines), "showToUser": False})


# --- сценарии ------------------------------------------------------------

def speed_to_lead(mk: MoyklassClient) -> None:
    admins = _admins()
    if not admins:
        return
    duty = admins[_today().toordinal() % len(admins)]  # дежурный по дню
    today = _today().isoformat()
    tomorrow = (_today() + timedelta(days=1)).isoformat()
    joins = mk.fetch_all("/v1/company/joins", ["joins"], params={
        "statusId": NEW_JOIN_STATUS, "createdAt": [today, tomorrow]})
    for j in joins:
        with db.get_conn() as conn:
            seen = conn.execute(
                "SELECT 1 FROM autopilot_state WHERE kind='lead_task' AND key=?",
                (str(j["id"]),)).fetchone()
        if seen:
            continue
        _task(mk, duty["managerId"], j.get("userId"),
              "🔥 НОВАЯ ЗАЯВКА — позвонить в течение 5 минут! "
              "Свежий лид конвертируется в разы лучше.")
        if j.get("userId") and _mark("lead_hint", str(j["userId"])):
            try:
                _hint_for_lead(mk, j["userId"], j)
            except Exception:
                log.exception("speed_to_lead: подсказка не создана для %s", j["userId"])
        _mark("lead_task", str(j["id"]))  # метка только после успешного создания
        log.info("speed_to_lead: задача по заявке %s → %s", j["id"], duty["name"])


def no_show(mk: MoyklassClient) -> None:
    admins = _admins()
    now = _now()
    recs = mk.fetch_all("/v1/company/lessonRecords", ["lessonRecords"], params={
        "date": _today().isoformat(), "test": "true", "visit": "false",
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
    today = _today().isoformat()
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
    today = _today()
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
            user = mk.get(f"/v1/company/users/{uid}")
            state = user.get("clientStateId")
            if state in SKIP_STATES:
                continue
            if not _mark("call_task", str(uid)):
                # повтор для недозвона: новая задача раз в 2 дня, до 3 раз
                if state != ST_NEDOZVON:
                    continue
                retry = next((i for i in range(1, 4)
                              if _mark("retry_task", f"{uid}:{i}:{_today().toordinal() // 2}")), None)
                if retry is None:
                    continue
                _task(mk, adm["managerId"], uid,
                      f"Недозвон — попытка в другое время дня (повтор №{retry}). "
                      "После разговора поставь статус.")
                made += 1
                continue
            _task(mk, adm["managerId"], uid,
                  "Обзвон набора 2026/27: открой карточку, прочитай подсказку 🎯, "
                  "позвони кнопкой и поставь статус по итогу.")
            made += 1
        log.info("morning_tasks: %s — %d задач", adm["name"], made)


def auto_reject(mk: MoyklassClient) -> None:
    """6+ попыток дозвона без ответа за 14 дней -> «Отказ» (для статуса Недозвон)."""
    limit = int(db.get_setting("missed_reject_attempts", "6") or 6)
    counts: dict[str, int] = {}
    answered: set[str] = set()
    try:
        rows = mango.calls(_now() - timedelta(days=14), _now())
    except Exception as e:
        log.warning("auto_reject: mango недоступен: %s", e)
        return
    for r in rows:
        if not r["from_ext"]:
            continue
        num = r["to_num"][-10:]
        if r["answer"]:
            answered.add(num)
        else:
            counts[num] = counts.get(num, 0) + 1
    with db.get_conn() as conn:
        users = conn.execute(
            "SELECT id, phone FROM users WHERE client_state_id = ?", (ST_NEDOZVON,)).fetchall()
    for uid, phone in users:
        p = "".join(ch for ch in str(phone or "") if ch.isdigit())[-10:]
        if not p or p in answered or counts.get(p, 0) < limit:
            continue
        if not _mark("auto_reject", str(uid)):
            continue
        mk.post(f"/v1/company/users/{uid}/status", {"statusId": ST_REJECT})
        log.info("auto_reject: %s (%d попыток) -> Отказ", uid, counts.get(p, 0))


def daily_digest() -> None:
    day = _today().isoformat()
    try:
        rows = mango.calls(_now().replace(hour=0, minute=0, second=0),
                           _now())
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


def _retry_forwards() -> None:
    """Дослать события Wazzup в МойКласс, не ушедшие с первого раза."""
    with db.get_conn() as conn:
        try:
            rows = conn.execute(
                "SELECT id, payload FROM wazzup_fwd_queue ORDER BY id LIMIT 20").fetchall()
        except Exception:
            return
    if not rows:
        return
    from .main import wazzup_forward
    for rid, payload in rows:
        if wazzup_forward(json.loads(payload)):
            with db.get_conn() as conn:
                conn.execute("DELETE FROM wazzup_fwd_queue WHERE id = ?", (rid,))
        else:
            break  # МойКласс недоступен — попробуем через минуту


# --- планировщик ---------------------------------------------------------

def _loop() -> None:
    last3 = 0.0
    while True:
        try:
            if db.get_setting("autopilot", "off") != "on":
                time.sleep(60)
                continue
            now = _now()
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
            # окна вместо точной минуты: тик может пропустить минуту, а при
            # рестарте днём порции всё равно должны создаться (догон)
            if 8 <= now.hour < 19 and _mark("morning", str(_today())):
                mk = _client()
                try:
                    morning_tasks(mk)
                finally:
                    mk.close()
            _retry_forwards()
            if (now.hour, now.minute) >= (19, 45) and _mark("areject", str(_today())):
                mk = _client()
                try:
                    auto_reject(mk)
                finally:
                    mk.close()
            if now.hour >= 20 and _mark("digest", str(_today())):
                daily_digest()
        except Exception:
            log.exception("autopilot: ошибка цикла")
        time.sleep(60)


def start() -> None:
    threading.Thread(target=_loop, daemon=True, name="autopilot").start()
    log.info("автопилот запущен (включение: настройка autopilot=on)")
