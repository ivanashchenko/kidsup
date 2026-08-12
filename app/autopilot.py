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
import re
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
SKIP_HARD = {125954, 125957,                      # Некачественный, Отказ
             146328, 215202, 146330, 146513}      # 0.1-0.2, переехал, 13+
SKIP_FUNNEL = {146950, 125952, 125953, 345767}    # думает/записался/посетил/думает-2
ST_CLIENT = 125955        # «Клиент»: для летних семей НЕ повод пропускать —
                          # лагерь закончился, это самые тёплые продажи набора
SKIP_STATES = SKIP_HARD | SKIP_FUNNEL | {ST_CLIENT}
NEW_JOIN_STATUS = 50509   # «1. Новая заявка»
ST_NEDOZVON = 345768      # «2. Недозвон (в работе)»
ST_REJECT = 125957        # «Отказ»

CAMP_COURSE = "Английский летний клуб"


def _summer_kinds() -> dict[int, str]:
    """user_id -> 'camp' (только летний лагерь) или 'regular' (МсМ/логопед/сад…).

    Лагерные семьи в CRM стоят как «Клиент», но лагерь закончился — им нужен
    продающий звонок про учебный год. Регулярным летним — звонок на продление.
    """
    with db.get_conn() as conn:
        rows = conn.execute("""SELECT lr.user_id, co.name FROM lesson_records lr
            JOIN lessons l ON l.id = lr.lesson_id
            LEFT JOIN classes cl ON cl.id = l.class_id
            LEFT JOIN courses co ON co.id = cl.course_id
            WHERE lr.visit = 1 AND l.date >= '2026-06-01'""").fetchall()
    kinds: dict[int, str] = {}
    for uid, cname in rows:
        if cname == CAMP_COURSE:
            kinds.setdefault(uid, "camp")
        else:
            kinds[uid] = "regular"
    return kinds


def _client() -> MoyklassClient:
    return MoyklassClient(get_api_key())


# --- имена детей для рассылок ----------------------------------------------
# В CRM имя чаще «Фамилия Имя (пометки)». Берём слово из словаря имён; если
# уверенного имени нет — нейтральный запасной вариант, а не фамилия.

GIVEN_NAMES = set("""
александр алексей андрей антон арсений артем артемий артур богдан вадим
валентин валерий василий виктор виталий владимир владислав всеволод вячеслав
георгий герман глеб григорий давид дамир даниил данил данила даниэль демид
демир денис дмитрий добрыня егор елисей ждан иван игнат игорь илья кирилл
клим константин лев леон леонид лука макар максим марат марк матвей мирон
михаил назар никита николай олег оскар павел петр платон прохор роберт роман
ростислав савва савелий святослав семен сергей степан тамерлан тимофей тимур
тихон федор филипп эмиль эрик юрий ярослав амир адам али карим рамиль руслан
рустам салах самир умар эмир янис
агата аглая аделина адель алевтина александра алина алиса алла амелия амина
анастасия ангелина анна антонина арина ася божена валентина валерия варвара
василиса вера вероника виктория виолетта влада владислава галина дарина дарья
диана ева евангелина евгения екатерина елена елизавета есения жанна злата зоя
ирина инга инна карина кира кристина ксения лада лариса лидия лилия лия
любовь людмила майя маргарита марина мария марта марьяна мила милана милица
мира мирослава моника надежда наталья нелли ника николь нина оксана олеся
ольга полина рада раиса регина римма роза сабина сафия светлана серафима
снежана софия софья стефания таисия тамара татьяна ульяна устинья фаина
эвелина элина эльвира эмилия эмма юлия яна ярослава
алена агния дания радислав джамаль бектур аннур велислава евдокия
пелагея аглаида мирон дарий люция азалия марьям анаит айлин мадлен
""".split())

FEM_SOFT = {"любовь"}                            # женские на -ь: Любови
INDECLINABLE = {"николь", "адель", "нелли",      # не склоняются
                "марьям", "анаит", "айлин", "мадлен"}
GEN_SPECIAL = {"лев": "Льва", "павел": "Павла"}  # беглые гласные


def _genitive(name: str) -> str:
    """Родительный падеж имени: Аглая → Аглаи, Марк → Марка, Игорь → Игоря."""
    low = name.lower()
    if low in GEN_SPECIAL:
        return GEN_SPECIAL[low]
    if low in INDECLINABLE:
        return name
    if low in FEM_SOFT:
        return name[:-1] + "и"
    if low.endswith("ия"):
        return name[:-1] + "и"
    if low.endswith(("га", "ка", "ха", "жа", "ша", "ча", "ща")):
        return name[:-1] + "и"
    if low.endswith("а"):
        return name[:-1] + "ы"
    if low.endswith("я"):
        return name[:-1] + "и"
    if low.endswith(("й", "ь")):
        return name[:-1] + "я"
    if low[-1] in "бвгджзклмнпрстфхцчшщ":
        return name + "а"
    return name  # несклоняемые (Лео, Отто…)


def _child_name(full: str) -> str | None:
    """Имя ребёнка из строки CRM или None, если уверенного имени нет."""
    clean = re.sub(r"\([^)]*\)", " ", full or "")
    for w in clean.split():
        if w.lower().replace("ё", "е") in GIVEN_NAMES:
            return w.capitalize()
    return None


def _fill_name(text: str, full_name: str) -> str:
    """Плейсхолдеры: {имя} — именительный, {имя_р} — родительный падеж."""
    child = _child_name(full_name)
    text = text.replace("{имя}", child or "ваш ребёнок")
    return text.replace("{имя_р}", _genitive(child) if child else "вашего ребёнка")


# --- кампании рассылок ----------------------------------------------------

def _bq_init(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS broadcast_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign TEXT, phone TEXT, child TEXT, text TEXT,
        status TEXT DEFAULT 'pending', created TEXT, sent TEXT)""")


def enqueue_broadcast(campaign: str, segment: str, text: str) -> dict:
    """Ставит кампанию в очередь. Сегменты: contin (уч. год 25/26), camp
    (летний лагерь), regular (летние регулярные), y2425 (давние), warm (все
    перечисленные). Плейсхолдер {имя} = имя ребёнка из CRM. Один номер — одно
    сообщение за кампанию; жёсткие статусы (отказ/не звонить) исключаются."""
    kinds = _summer_kinds()
    with db.get_conn() as conn:
        _bq_init(conn)
        base = """WITH v AS (SELECT lr.user_id u, l.date d FROM lesson_records lr
                  JOIN lessons l ON l.id = lr.lesson_id WHERE lr.visit = 1)"""
        summer = {r[0] for r in conn.execute(base + " SELECT DISTINCT u FROM v WHERE d>='2026-06-01'")}
        y2526 = {r[0] for r in conn.execute(base + " SELECT DISTINCT u FROM v WHERE d>='2025-09-01' AND d<'2026-06-01'")} - summer
        y2425 = {r[0] for r in conn.execute(base + " SELECT DISTINCT u FROM v WHERE d>='2024-09-01' AND d<'2025-09-01'")} - summer - y2526
        pick: set[int] = set()
        if segment in ("contin", "warm"):
            pick |= y2526
        if segment in ("camp", "warm"):
            pick |= {u for u in summer if kinds.get(u) == "camp"}
        if segment in ("regular", "warm"):
            pick |= {u for u in summer if kinds.get(u) == "regular"}
        if segment in ("y2425", "warm"):
            pick |= y2425
        seen_phones: set[str] = set()
        n = 0
        now = _now().isoformat(timespec="seconds")
        for uid in sorted(pick):
            row = conn.execute("SELECT name, phone, raw FROM users WHERE id=?",
                               (uid,)).fetchone()
            if not row or not row[1] or row[1] in seen_phones:
                continue
            try:
                state = json.loads(row[2] or "{}").get("clientStateId")
            except ValueError:
                state = None
            if state in SKIP_HARD:
                continue
            seen_phones.add(row[1])
            child = row[0] or ""  # полное имя из CRM; имя ребёнка выделяется при отправке
            conn.execute("INSERT INTO broadcast_queue (campaign, phone, child, text, created) "
                         "VALUES (?, ?, ?, ?, ?)",
                         (campaign, row[1], child, text, now))
            n += 1
    log.info("broadcast: кампания %s (%s) — %d получателей", campaign, segment, n)
    return {"campaign": campaign, "segment": segment, "queued": n}


def broadcast_status() -> dict:
    with db.get_conn() as conn:
        _bq_init(conn)
        rows = conn.execute("SELECT campaign, status, COUNT(*) FROM broadcast_queue "
                            "GROUP BY campaign, status").fetchall()
        ps = conn.execute("SELECT campaign, status, COUNT(*) FROM broadcast_queue "
                          "WHERE text LIKE '%перезапустили английский%' "
                          "GROUP BY campaign, status").fetchall()
    out: dict = {}
    for camp, status, cnt in rows:
        out.setdefault(camp, {})[status] = cnt
    for camp, status, cnt in ps:
        out.setdefault(camp, {})[f"{status}_eng_ps"] = cnt
    # честная разбивка: sent ≠ доставлено — Telegram/MAX принимаются Wazzup-ом,
    # но не доставляются незнакомым номерам
    with db.get_conn() as conn:
        _bq_init(conn)
        try:
            def _n(p):
                return "".join(ch for ch in str(p or "") if ch.isdigit())[-10:]
            try:
                replied = {_n(r[0]) for r in conn.execute("SELECT phone FROM wazzup_inbox")}
            except Exception:
                replied = set()
            dl = conn.execute(
                "SELECT campaign, COUNT(*) FROM broadcast_queue WHERE status='sent' "
                "AND COALESCE(tried,'') LIKE '%whatsapp=ok%' GROUP BY campaign").fetchall()
            ph = conn.execute(
                "SELECT campaign, phone FROM broadcast_queue WHERE status='sent' "
                "AND COALESCE(tried,'') NOT LIKE '%whatsapp=ok%'").fetchall()
            for camp, cnt in dl:
                out.setdefault(camp, {})["delivered_whatsapp"] = cnt
            for camp, phone in ph:
                key = ("delivered_replied" if _n(phone) in replied
                       else "phantom_tg_max")
                out.setdefault(camp, {})[key] = out.setdefault(camp, {}).get(key, 0) + 1
        except Exception:
            pass
    out["_diag"] = {"last_tick": db.get_setting("broadcast_last_tick"),
                    "last_error": db.get_setting("broadcast_last_error"),
                    "transports": db.get_setting("broadcast_transports", "whatsapp"),
                    "wa_senders": db.get_setting("wa_senders", wazzup.WHATSAPP_PREFERRED)}
    return out


def broadcast_requeue_undelivered(day: str | None = None) -> dict:
    """Вернуть в очередь «мнимо-отправленное»: строки, ушедшие в Telegram/MAX
    со статусом sent, реально не доставились (Wazzup отвечает 200, а доставка
    падает уже после — красный «!» в чате). Для каждого телефона: если ни одна
    строка дня не доставлена в WhatsApp — исходная (первая) кампания
    возвращается в pending, остальные строки дня помечаются skipped_dup,
    чтобы семья в итоге получила ровно одно сообщение."""
    d = day or _today().isoformat()
    requeued = skipped = 0
    with db.get_conn() as conn:
        _bq_init(conn)
        try:
            conn.execute("ALTER TABLE broadcast_queue ADD COLUMN tried TEXT DEFAULT ''")
        except Exception:
            pass
        def _n(p):
            return "".join(ch for ch in str(p or "") if ch.isdigit())[-10:]
        try:
            replied = {_n(r[0]) for r in conn.execute("SELECT phone FROM wazzup_inbox")}
        except Exception:
            replied = set()
        rows = conn.execute(
            "SELECT id, phone, COALESCE(tried,''), status FROM broadcast_queue "
            "WHERE created LIKE ? OR sent LIKE ?", (d + "%", d + "%")).fetchall()
        byphone: dict[str, list] = {}
        for r in rows:
            byphone.setdefault(r[1], []).append(r)
        for phone, rs in byphone.items():
            if any("whatsapp=ok" in r[2] for r in rs):
                continue                       # семья получила в WhatsApp
            if _n(phone) in replied:
                continue                       # ответили (MAX/ТГ доставил) — не дублируем
            if not any(("tgapi=ok" in r[2]) or ("max=ok" in r[2]) for r in rs):
                continue                       # мнимых отправок нет
            keep = min(rs, key=lambda r: r[0])
            for r in rs:
                if r[0] == keep[0]:
                    conn.execute("UPDATE broadcast_queue SET status='pending', "
                                 "tried='', sent=NULL WHERE id=?", (r[0],))
                    requeued += 1
                elif r[3] in ("sent", "pending"):
                    conn.execute("UPDATE broadcast_queue SET status='skipped_dup' "
                                 "WHERE id=?", (r[0],))
                    skipped += 1
    log.info("broadcast: requeue %s — %d строк в очередь, %d дублей снято",
             d, requeued, skipped)
    return {"day": d, "requeued": requeued, "duplicates_skipped": skipped}


def broadcast_cancel(campaign: str | None = None) -> int:
    with db.get_conn() as conn:
        _bq_init(conn)
        cur = conn.execute(
            "UPDATE broadcast_queue SET status='cancelled' WHERE status='pending'"
            + (" AND campaign=?" if campaign else ""),
            (campaign,) if campaign else ())
        return cur.rowcount


def _broadcast_tick() -> None:
    """Раз в минуту: каскадная доставка очереди. Порядок каналов —
    настройка broadcast_transports (по умолчанию "tgapi,whatsapp,max").
    Telegram — общий темп (broadcast_per_hour, 60/час), WhatsApp — щадяще:
    не чаще 15/час и не больше 50/день (после бана 0077), MAX — 30/час.
    Строка уходит в ПЕРВЫЙ доставивший канал; недоставленное копит попытки
    в колонке tried и ждёт своего канала."""
    now = _now()
    db.set_setting("broadcast_last_tick", now.isoformat(timespec="seconds"))
    if not (10 <= now.hour < 19):
        return
    # 12.08: инициирующие рассылки — только WhatsApp, независимо от настройки.
    # Telegram «принимается» Wazzup-ом, но не доставляется незнакомым номерам
    # (упали все 100% отправок), MAX заблокирован. Настройка broadcast_transports
    # может лишь сузить (выключить и WhatsApp), но не вернуть tgapi/max.
    setting = [x.strip() for x in
               (db.get_setting("broadcast_transports", "whatsapp") or "whatsapp").split(",") if x.strip()]
    transports = ["whatsapp"] if "whatsapp" in setting else []
    if not transports:
        return
    budgets = {}
    if now.minute % 10 == 0:
        budgets["whatsapp"] = 1   # 6/час — щадящий темп после блокировки 0077
    day = now.strftime("%Y-%m-%d")
    with db.get_conn() as conn:
        _bq_init(conn)
        try:
            conn.execute("ALTER TABLE broadcast_queue ADD COLUMN tried TEXT DEFAULT ''")
        except Exception:
            pass
        wa_today = conn.execute(
            "SELECT COUNT(*) FROM broadcast_queue WHERE status='sent' "
            "AND sent LIKE ? AND tried LIKE '%whatsapp=ok%'", (day + "%",)).fetchone()[0]
        if wa_today >= int(db.get_setting("wa_daily_cap", "25") or 25):
            budgets.pop("whatsapp", None)
        rows = conn.execute(
            "SELECT id, phone, child, text, COALESCE(tried,'') FROM broadcast_queue "
            "WHERE status='pending' "
            "ORDER BY campaign = 'no1_apology' DESC, id LIMIT 30").fetchall()
    dry = db.get_setting("wazzup_dry_run", "1") == "1"
    for rid, phone, child, text, tried in rows:
        if not budgets:
            break
        # канал N доступен строке только после неудачи всех предыдущих —
        # WhatsApp-лимит тратим лишь на тех, кому Telegram не доставил
        target = next((tr for i, tr in enumerate(transports)
                       if tr in budgets and f"{tr}=" not in tried
                       and all(f"{p}=" in tried for p in transports[:i])), None)
        if target is None:
            # все доступные сейчас каналы уже пробованы этой строкой
            if all(f"{tr}=" in tried for tr in transports):
                with db.get_conn() as conn:
                    conn.execute("UPDATE broadcast_queue SET status='undeliverable' WHERE id=?", (rid,))
            continue
        msg = _fill_name(text, child)
        try:
            ok = wazzup.send_via(target, phone, msg, dry_run=dry)
        except Exception as e:
            log.warning("wazzup %s недоступен: %s", target, e)
            ok = False
        budgets[target] -= 1
        if budgets[target] <= 0:
            budgets.pop(target)
        mark = f"{target}={'ok' if ok else 'fail'};"
        with db.get_conn() as conn:
            if ok:
                conn.execute("UPDATE broadcast_queue SET status='sent', sent=?, tried=COALESCE(tried,'')||? WHERE id=?",
                             (_now().isoformat(timespec="seconds"), mark, rid))
            else:
                conn.execute("UPDATE broadcast_queue SET tried=COALESCE(tried,'')||? WHERE id=?", (mark, rid))
        log.info("broadcast: #%s %s -> %s %s", rid, phone[-4:], target, "ok" if ok else "fail")


def _admins() -> list[dict]:
    try:
        return json.loads(db.get_setting("call_admins") or "[]")
    except ValueError:
        return []


def _admins_today() -> list[dict]:
    """Кто сегодня на смене. Настройка admin_schedule: {"2026-08-12": 232805, ...}
    (дата -> managerId). Нет записи на сегодня — работают все из call_admins."""
    admins = _admins()
    try:
        sched = json.loads(db.get_setting("admin_schedule") or "{}")
    except ValueError:
        sched = {}
    mid = sched.get(_today().isoformat())
    if mid:
        onduty = [a for a in admins if a.get("managerId") == mid]
        if onduty:
            return onduty
    return admins


def _has_mark(kind: str, key: str) -> bool:
    with db.get_conn() as conn:
        try:
            return conn.execute(
                "SELECT 1 FROM autopilot_state WHERE kind=? AND key=?",
                (kind, key)).fetchone() is not None
        except Exception:
            return False


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
        main = "Английский детский сад (гр. 3–4) или Раннее развитие ур.2; вторым — ИЗО"
    elif ag < 5.5:
        main = "Подготовка к школе + английский; вторым — менталка 4–7, шахматы"
    elif ag < 7:
        main = "Нулевой класс (полный день) или ПШ; вторым — менталка/скорочтение"
    else:
        main = "Английский по уровню + менталка 7–12/скорочтение; вторым — шахматы/ИЗО"
    age_s = f"{ag:.1f} лет" if ag else "возраст неизвестен"
    lines = [
        "🎯 ПОДСКАЗКА ДЛЯ ЗВОНКА (новый лид, сформирована автоматически)",
        f"Возраст: {age_s}." + (f" Заявка на: {course}." if course else " Источник заявки — уточните в карточке."),
        f"Предлагать: {main}.",
        "Акция: при оплате до 30.08 сентябрь идёт по ценам прошлого учебного года (продаём только помесячно). "
        "Приглашайте: 29.08 День рождения KidsUP в парке, 30.08 День открытых дверей (KidsUPday.ru), "
        "31.08–06.09 Неделя открытых уроков (KidsUPweek.ru).",
    ]
    mk.post("/v1/company/userComments",
            {"userId": user_id, "comment": "\n".join(lines), "showToUser": False})


# --- сценарии ------------------------------------------------------------

def speed_to_lead(mk: MoyklassClient) -> None:
    admins = _admins_today()
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
    admins = _admins_today()
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


def _queues() -> tuple[dict[int, list[int]], dict[int, str]]:
    """Очереди обзвона: (admin_index -> [user_id, ...], user_id -> тип звонка).

    Приоритет по решению руководителя: сначала прошлый учебный год
    (продолжение занятий), затем лето (лагерь — продажа, регулярные —
    продление), затем позапрошлый год (давние).
    """
    with db.get_conn() as conn:
        base = """WITH v AS (SELECT lr.user_id u, l.date d FROM lesson_records lr
                  JOIN lessons l ON l.id = lr.lesson_id WHERE lr.visit = 1)"""
        summer = {r[0] for r in conn.execute(base + " SELECT DISTINCT u FROM v WHERE d>='2026-06-01'")}
        y2526 = {r[0] for r in conn.execute(base + " SELECT DISTINCT u FROM v WHERE d>='2025-09-01' AND d<'2026-06-01'")} - summer
        y2425 = {r[0] for r in conn.execute(base + " SELECT DISTINCT u FROM v WHERE d>='2024-09-01' AND d<'2025-09-01'")} - summer - y2526
        phones = dict(conn.execute("SELECT id, phone FROM users"))
    kinds = _summer_kinds()                      # camp / regular (лето)
    for u in y2526:
        kinds.setdefault(u, "contin")            # продолжение занятий
    today = _today()
    if today <= date(2026, 8, 11):
        wave = summer
    elif today <= date(2026, 8, 16):
        wave = y2526 | summer
    else:
        wave = y2425 | y2526 | summer            # добираем хвосты
    fams: dict[str, list[int]] = {}
    order = (sorted(wave & y2526) + sorted(wave & summer)
             + sorted(wave - y2526 - summer))
    for u in order:
        fams.setdefault(phones.get(u) or f"x{u}", []).append(u)
    # прогретые рассылкой — первыми: сначала ответившие, потом получившие
    def _norm(p):
        return "".join(ch for ch in str(p or "") if ch.isdigit())[-10:]
    with db.get_conn() as conn:
        _bq_init(conn)
        # «прогретые» = реально доставленные (WhatsApp); мнимые отправки в
        # Telegram/MAX (Wazzup принял, доставка упала) прогревом не считаются
        sent_ph = {_norm(r[0]) for r in conn.execute(
            "SELECT phone FROM broadcast_queue WHERE status='sent' "
            "AND COALESCE(tried,'') LIKE '%whatsapp=ok%'")}
        try:
            replied_ph = {_norm(r[0]) for r in conn.execute(
                "SELECT phone FROM wazzup_inbox")}
        except Exception:
            replied_ph = set()
    def _warmth(phone):
        p = _norm(phone)
        return 0 if p in replied_ph else 1 if p in sent_ph else 2
    fam_items = sorted(fams.items(), key=lambda kv: _warmth(kv[0]))  # stable
    n = max(1, len(_admins()))
    out: dict[int, list[int]] = {}
    for i, (_, fam) in enumerate(fam_items):
        out.setdefault(i % n, []).extend(fam)
    return out, kinds


def morning_tasks(mk: MoyklassClient) -> None:
    admins = _admins_today()
    if not admins:
        return
    per_admin = int(db.get_setting("daily_tasks_per_admin", "40") or 40)
    queues, kinds = _queues()
    TEXT_CONTIN = ("Продолжение занятий 2026/27: в подсказке 🎯 — чем занимался "
                   "ребёнок в прошлом году. Предложи продолжить в новой группе. "
                   "Если пошёл в школу — вместо подготовки к школе предлагай "
                   "каллиграфию, скорочтение, менталку. До 30.08 сентябрь по старым ценам.")
    TEXT_CAMP = ("🔥 Летний лагерь закончился — продающий звонок про учебный год: "
                 "«вы уже видели центр изнутри — тот же английский круглый год, "
                 "плюс направления по возрасту». Зови на Неделю открытых уроков "
                 "31.08–06.09 (KidsUPweek.ru); до 30.08 сентябрь по старым ценам.")
    TEXT_RENEW = ("Продление: семья занимается у нас летом (МсМ/логопед/сад). "
                  "Подтверди продолжение с сентября и предложи второй предмет "
                  "по возрасту из подсказки 🎯. До 30.08 сентябрь по старым ценам.")
    TEXT_COLD = ("Обзвон набора 2026/27: открой карточку, прочитай подсказку 🎯, "
                 "позвони кнопкой и поставь статус по итогу.")
    TEXTS = {"contin": TEXT_CONTIN, "camp": TEXT_CAMP, "regular": TEXT_RENEW}
    for idx, adm in enumerate(admins):
        made = 0
        errors = 0
        for uid in queues.get(idx, []):
            if made >= per_admin:
                break
            # одна плохая карточка/сбой API не должны ронять всю порцию
            try:
                kind = kinds.get(uid)
                # летним (лагерь/регулярные) статус «Клиент» не помеха
                skip = (SKIP_HARD | SKIP_FUNNEL) if kind in ("camp", "regular") \
                    else SKIP_STATES
                user = mk.get(f"/v1/company/users/{uid}")
                state = user.get("clientStateId")
                if state in skip:
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
                _task(mk, adm["managerId"], uid, TEXTS.get(kind, TEXT_COLD))
                made += 1
            except Exception:
                errors += 1
                log.exception("morning_tasks: сбой на клиенте %s — пропускаю", uid)
                if errors >= 15:  # МойКласс лежит — нет смысла молотить дальше
                    raise
        log.info("morning_tasks: %s — %d задач (ошибок: %d)", adm["name"], made, errors)


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

NO1_TEXT_V2 = (
    "Здравствуйте! ☀️ Это KidsUP на Рокоссовского. Как вы? Как прошло лето "
    "у {имя_р}? Мы уже готовимся к сентябрю: обновили программы и придумали "
    "особенное на конец августа — 29.08 (сб.) праздник с аниматорами и "
    "беспроигрышной лотереей в парке «Янтарная горка» (у ЖК Богородский) "
    "в честь дня рождения KidsUP, 30.08 (вс.) День открытых дверей, "
    "с 31.08 Неделя открытых уроков (всё бесплатно). Скоро расскажем "
    "подробнее 💛 И маленький вопрос: какие у вас планы — 🌊 ещё отдыхаем "
    "или 🏫 уже думаем про сентябрь? Можно просто смайликом 🙂")


APOLOGY_TEXT = (
    "Ой, неловко вышло 🙈 Это снова KidsUP. Утром наша автоматическая "
    "рассылка сглючила и перепутала имя — обратилась к вам по фамилии. "
    "Простите! Робота мы уже починили, а всё остальное в сообщении — "
    "чистая правда: 29.08 (сб.) праздник с аниматорами и беспроигрышной "
    "лотереей в парке «Янтарная горка», 30.08 (вс.) День открытых дверей, "
    "с 31.08 Неделя открытых уроков — всё бесплатно. Хорошего остатка "
    "лета вашей семье! 💛")


def _migrations() -> None:
    """Одноразовые правки данных, приезжают вместе с кодом."""
    if _mark("migration", "roistat_project_v1"):
        if not db.get_setting("roistat_project"):
            db.set_setting("roistat_project", "228571")  # Kids UP в Roistat
    if _mark("migration", "no1_resume_tg_v1"):
        # WhatsApp 0077 заблокирован за массовость: остаток рассылки — только
        # Telegram (настройка broadcast_transports); заморозку вернуть в очередь
        if not db.get_setting("broadcast_transports"):
            db.set_setting("broadcast_transports", "tgapi")
        with db.get_conn() as conn:
            _bq_init(conn)
            cur = conn.execute(
                "UPDATE broadcast_queue SET status='pending' "
                "WHERE campaign='no1_digest' AND status='cancelled'")
            log.info("миграция no1_resume_tg_v1: %d сообщений обратно в очередь (только TG)",
                     cur.rowcount)
    if _mark("migration", "no1_apology_v1"):
        # тем, кому утром ушёл текст с фамилией, — извинение (уходит первым,
        # см. приоритет кампании в _broadcast_tick)
        with db.get_conn() as conn:
            _bq_init(conn)
            cur = conn.execute("""
                INSERT INTO broadcast_queue (campaign, phone, child, text, created)
                SELECT 'no1_apology', phone, child, ?, ?
                FROM broadcast_queue
                WHERE campaign = 'no1_digest' AND status = 'sent'""",
                (APOLOGY_TEXT, _now().isoformat(timespec="seconds")))
            log.info("миграция no1_apology_v1: %d извинений в очереди", cur.rowcount)
    if _mark("migration", "no1_text_v2"):
        # рассылка №1: остановленным сообщениям — новый текст, полное имя
        # из CRM (для склонения) и обратно в очередь
        with db.get_conn() as conn:
            _bq_init(conn)
            cur = conn.execute("""
                UPDATE broadcast_queue SET
                    text = ?,
                    child = COALESCE((SELECT u.name FROM users u
                                      WHERE u.phone = broadcast_queue.phone), child),
                    status = 'pending'
                WHERE campaign = 'no1_digest' AND status IN ('cancelled', 'pending')""",
                (NO1_TEXT_V2,))
            log.info("миграция no1_text_v2: обновлено %d сообщений", cur.rowcount)
    if _mark("migration", "no1_eng_ps_v3"):
        # выпускникам английского прошлых лет — P.S. про новый формат курса:
        # и в основной рассылке, и в извинениях. Телефоны сравниваем по
        # последним 10 цифрам — формат в очереди и в users может отличаться
        with db.get_conn() as conn:
            _bq_init(conn)
            def norm(p):
                return "".join(ch for ch in (p or "") if ch.isdigit())[-10:]
            alumni = {norm(p) for p in _eng_alumni_phones(conn)}
            alumni.discard("")
            rows = conn.execute(
                "SELECT id, phone FROM broadcast_queue "
                "WHERE campaign IN ('no1_digest', 'no1_apology') AND status = 'pending' "
                "AND text NOT LIKE '%перезапустили английский%'").fetchall()
            ids = [r[0] for r in rows if norm(r[1]) in alumni]
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                conn.execute(
                    f"UPDATE broadcast_queue SET text = text || ? "
                    f"WHERE id IN ({','.join('?' * len(chunk))})", (ENG_PS, *chunk))
            log.info("миграция no1_eng_ps_v3: P.S. про английский у %d сообщений "
                     "(выпускников в базе: %d)", len(ids), len(alumni))


ENG_PS = (
    "\n\nP.S. И отдельная новость для вас: мы полностью перезапустили "
    "английский. Группы теперь строго по уровням Cambridge — начинающих "
    "и продолжающих не смешиваем; у каждого ученика — языковой паспорт "
    "с замерами прогресса, раз в два месяца родители получают видео, где "
    "ребёнок говорит по-английски, ежемесячно — спикинг-клубы, где дети "
    "только разговаривают, а в конце года — настоящий экзамен в формате "
    "Cambridge YLE с сертификатом. На Неделе открытых уроков (31.08–06.09) "
    "покажем новый формат в деле — приходите 🇬🇧")

ENG_COURSE_ID = 82621  # «Английский язык»


def _eng_alumni_phones(conn) -> list[str]:
    """Телефоны семей, чьи дети ходили на курсовой английский с осени 2024."""
    return [r[0] for r in conn.execute("""
        SELECT DISTINCT u.phone FROM users u
        JOIN lesson_records lr ON lr.user_id = u.id
        JOIN lessons l ON l.id = lr.lesson_id
        JOIN classes cl ON cl.id = l.class_id
        WHERE cl.course_id = ? AND lr.visit = 1 AND l.date >= '2024-09-01'
          AND u.phone IS NOT NULL AND u.phone != ''""", (ENG_COURSE_ID,))]


def _loop() -> None:
    last3 = 0.0
    while True:
        try:
            if db.get_setting("autopilot", "off") != "on":
                time.sleep(60)
                continue
            _migrations()
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
            # рестарте днём порции всё равно должны создаться (догон).
            # отметка ставится ТОЛЬКО после успеха: упавшая порция
            # доделается на следующем тике (call_task-метки защищают от дублей)
            if 8 <= now.hour < 19 and not _has_mark("morning", str(_today())):
                mk = _client()
                try:
                    morning_tasks(mk)
                    _mark("morning", str(_today()))
                finally:
                    mk.close()
            _retry_forwards()
            # лёгкий синк групп и записей каждые 5 минут (08:00-21:00) —
            # «Набор 26/27» видит новые записи почти сразу; полный синк —
            # раз в день утром и по кнопке
            if 8 <= now.hour < 21 and now.minute % 5 == 0 \
                    and _mark("lightsync", now.strftime("%Y-%m-%d %H:%M")):
                try:
                    from . import sync as _sync
                    threading.Thread(target=_sync.light_sync, daemon=True).start()
                except Exception:
                    log.exception("лёгкий синк не запустился")
            if now.hour == 7 and now.minute < 2 and _mark("fullsync", str(_today())):
                try:
                    from . import sync as _sync
                    _sync.start_sync()
                except Exception:
                    log.exception("полный автосинк не запустился")
            try:
                _broadcast_tick()
            except Exception as e:
                log.exception("broadcast_tick упал — продолжаем")
                try:
                    db.set_setting("broadcast_last_error",
                                   f"{_now().isoformat(timespec='seconds')}: {type(e).__name__}: {e}"[:300])
                except Exception:
                    pass
            if (now.hour, now.minute) >= (19, 45) and _mark("areject", str(_today())):
                mk = _client()
                try:
                    auto_reject(mk)
                finally:
                    mk.close()
            if now.hour >= 20 and _mark("digest", str(_today())):
                daily_digest()
            if now.hour >= 21 and _mark("roistat", str(_today())):
                try:
                    from . import roistat as _ro
                    from datetime import timedelta as _td
                    _ro.push(since=(_today() - _td(days=2)).isoformat(), dry_run=False)
                except Exception:
                    log.exception("roistat push не удался")
        except Exception:
            log.exception("autopilot: ошибка цикла")
        time.sleep(60)


def start() -> None:
    threading.Thread(target=_loop, daemon=True, name="autopilot").start()
    log.info("автопилот запущен (включение: настройка autopilot=on)")
