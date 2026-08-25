"""Автопилот: фоновые сценарии, работающие пока приложение запущено.

Сценарии (все проверяются раз в минуту, каждый — по своему расписанию):
  speed_to_lead   каждые 3 мин  новая заявка/клиент в МойКласс → срочная
                                задача дежурному админу «позвонить за 5 минут»
  unanswered_inbound каждые 15 мин  клиент написал в Wazzup, 45 минут без
                  (10:00–20:00)   нашего ответа → задача админу переписки
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
  daily_tasks_per_admin  норма звонков на смену; порция утром добирает до неё (45)
  wazzup_dry_run       "1" — сообщения только логируются (по умолчанию "1")

Состояние (таблица autopilot_state) защищает от повторов: одна задача на
заявку, одно сообщение недозвону в день и т.п.
"""

import json
import logging
import random
import re
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import brain
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
ST_BOOKED = 125952        # «3. Записался на пробное»
ST_PROMO = 347075         # «От промоутера»: контакт есть, разговора ещё не было
ST_JOIN_BOOKED = 58132    # запись в группу «Записался на пробное»

CAMP_COURSE = "Английский летний клуб"


def _past_summer_kinds(conn) -> dict[int, str]:
    """Кто чем занимался у нас летом 2024/2025: 'camp' или 'regular'.

    Нужно строго по факту: текст «был у нас в летнем лагере» нельзя слать
    семье, которая ходила на регулярные занятия (Музыка и речь и т. п.).
    """
    rows = conn.execute("""SELECT lr.user_id, co.name FROM lesson_records lr
        JOIN lessons l ON l.id = lr.lesson_id
        LEFT JOIN classes cl ON cl.id = l.class_id
        LEFT JOIN courses co ON co.id = cl.course_id
        WHERE lr.visit = 1
          AND ((l.date >= '2024-06-01' AND l.date < '2024-09-01')
            OR (l.date >= '2025-06-01' AND l.date < '2025-09-01'))""").fetchall()
    kinds: dict[int, str] = {}
    for uid, cname in rows:
        if cname == CAMP_COURSE:
            kinds[uid] = "camp"          # лагерь перевешивает: он точно был
        else:
            kinds.setdefault(uid, "regular")
    return kinds


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


def _accusative(name: str) -> str:
    """Винительный падеж имени: «записать Еву», «помним Марка», «ждём Игоря».
    У одушевлённых мужских совпадает с родительным, у женских на -а/-я — -у/-ю."""
    low = name.lower()
    if low in INDECLINABLE or low in FEM_SOFT:   # Николь, Любовь — не меняются
        return name
    if low.endswith("а"):
        return name[:-1] + "у"
    if low.endswith("я"):
        return name[:-1] + "ю"
    return _genitive(name)                        # Марк → Марка, Игорь → Игоря


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
    text = text.replace("{имя_р}", _genitive(child) if child else "вашего ребёнка")
    # запасной вариант «ваш ребёнок» может попасть в начало предложения —
    # без этого клиент видит «…Рокоссовского. ваш ребёнок был…»
    return re.sub(r"(^|[.!?]\s+)([а-яё])",
                  lambda m: m.group(1) + m.group(2).upper(), text)


# --- кампании рассылок ----------------------------------------------------

def _bq_init(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS broadcast_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign TEXT, phone TEXT, child TEXT, text TEXT,
        status TEXT DEFAULT 'pending', created TEXT, sent TEXT)""")


def enqueue_broadcast(campaign: str, segment: str, text: str,
                      include_active: bool = False,
                      exclude_enrolled: bool = False,
                      exclude_campaigns: list[str] | None = None) -> dict:
    """Ставит кампанию в очередь. Сегменты: contin (уч. год 25/26), camp
    (летний лагерь), regular (летние регулярные), y2425 (давние), warm (все
    перечисленные), funnel (открытая воронка набора: новые, промо, недозвон,
    думают). Плейсхолдер {имя} = имя ребёнка из CRM. Один номер — одно
    сообщение за кампанию; жёсткие статусы (отказ/не звонить) исключаются.
    include_active — не отсекать семьи, ходившие этим летом (для приглашений);
    exclude_enrolled — пропустить уже записанных в группы 26/27;
    exclude_campaigns — пропустить номера, уже стоящие в других кампаниях."""
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
        if segment == "funnel":
            # открытая воронка набора: по статусу карточки, визиты не нужны
            FUNNEL = {125951, 347075, 345768, 146950, 345767}
            for uid, raw in conn.execute("SELECT id, raw FROM users"):
                try:
                    if json.loads(raw or "{}").get("clientStateId") in FUNNEL:
                        pick.add(uid)
                except ValueError:
                    continue
        if segment in ("camp_past", "camp_past_camp", "camp_past_regular"):
            # лето 2024/2025, но НЕ были летом 2026
            past = {r[0] for r in conn.execute(base + """
                SELECT DISTINCT u FROM v WHERE (d>='2024-06-01' AND d<'2024-09-01')
                OR (d>='2025-06-01' AND d<'2025-09-01')""")} - summer
            past_kinds = _past_summer_kinds(conn)
            if segment == "camp_past_camp":
                past = {u for u in past if past_kinds.get(u) == "camp"}
            elif segment == "camp_past_regular":
                past = {u for u in past if past_kinds.get(u) == "regular"}
            pick |= past
        active_phones = {(_ph or "")[-10:] for (_ph,) in conn.execute(
            """SELECT DISTINCT u.phone FROM users u
               JOIN lesson_records lr ON lr.user_id = u.id
               JOIN lessons l ON l.id = lr.lesson_id
               WHERE lr.visit = 1 AND l.date >= '2026-06-01'
                 AND u.phone IS NOT NULL AND u.phone != ''""") if _ph}
        enrolled_ids: set[int] = set()
        if exclude_enrolled:
            enrolled_ids = {r[0] for r in conn.execute(
                """SELECT DISTINCT j.user_id FROM joins j
                   JOIN classes c ON c.id = j.class_id
                   WHERE c.name LIKE '2627%'
                     AND j.status_id IN (2, 5, 50509, 83760, 58132, 58131, 70367)""")}
        other_phones: set[str] = set()
        if exclude_campaigns:
            marks = ",".join("?" for _ in exclude_campaigns)
            other_phones = {(p or "")[-10:] for (p,) in conn.execute(
                f"SELECT DISTINCT phone FROM broadcast_queue WHERE campaign IN ({marks})",
                exclude_campaigns)}
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
            if uid in enrolled_ids:
                continue      # уже записан в группы 26/27 — приглашать не нужно
            if row[1][-10:] in other_phones:
                continue      # уже получает другую кампанию — не дублируем
            if not include_active and row[1][-10:] in active_phones:
                continue      # семья ходит к нам этим летом — «не увиделись» ей писать нельзя
            seen_phones.add(row[1])
            child = row[0] or ""  # полное имя из CRM; имя ребёнка выделяется при отправке
            conn.execute("INSERT INTO broadcast_queue (campaign, phone, child, text, created) "
                         "VALUES (?, ?, ?, ?, ?)",
                         (campaign, row[1], child, text, now))
            n += 1
    log.info("broadcast: кампания %s (%s) — %d получателей", campaign, segment, n)
    return {"campaign": campaign, "segment": segment, "queued": n}


def broadcast_audit_camp(campaign: str) -> dict:
    """Проверяет, кому из получателей кампании про «летний лагерь» этот текст
    не соответствует факту: семья была летом, но на регулярных занятиях.
    Возвращает списки — по отправленным и по ожидающим отправки."""
    with db.get_conn() as conn:
        _bq_init(conn)
        kinds = _past_summer_kinds(conn)
        by_phone: dict[str, str] = {}
        for uid, kind in kinds.items():
            row = conn.execute("SELECT phone FROM users WHERE id=?", (uid,)).fetchone()
            if not row or not row[0]:
                continue
            key = row[0][-10:]
            if kind == "camp" or key not in by_phone:
                by_phone[key] = kind      # лагерь по любому ребёнку → семья была
        rows = conn.execute(
            "SELECT phone, child, status, sent FROM broadcast_queue WHERE campaign=?",
            (campaign,)).fetchall()
    out: dict = {"campaign": campaign, "sent_wrong": [], "pending_wrong": [],
                 "sent_ok": 0, "pending_ok": 0, "unknown": 0}
    for phone, child, status, sent in rows:
        kind = by_phone.get((phone or "")[-10:])
        if kind is None:
            out["unknown"] += 1
            continue
        rec = {"phone": phone, "child": child, "sent": sent}
        if kind == "regular":
            out["sent_wrong" if status == "sent" else "pending_wrong"].append(rec)
        elif status == "sent":
            out["sent_ok"] += 1
        else:
            out["pending_ok"] += 1
    out["sent_wrong_n"] = len(out["sent_wrong"])
    out["pending_wrong_n"] = len(out["pending_wrong"])
    return out


def broadcast_prune_wrong_camp(campaign: str) -> dict:
    """Снимает с очереди тех, для кого текст про лагерь не соответствует факту."""
    wrong = {r["phone"] for r in broadcast_audit_camp(campaign)["pending_wrong"]}
    with db.get_conn() as conn:
        _bq_init(conn)
        n = 0
        for ph in wrong:
            n += conn.execute(
                "UPDATE broadcast_queue SET status='cancelled' "
                "WHERE campaign=? AND phone=? AND status='pending'",
                (campaign, ph)).rowcount
    log.info("broadcast_prune_wrong_camp: снято %d", n)
    return {"campaign": campaign, "cancelled": n}


def broadcast_prune_active(campaign: str) -> dict:
    """Убирает из очереди семьи, которые занимаются у нас этим летом.
    Нужна, когда у брата и сестры разные карточки: по одной визитов нет,
    и текст «этим летом мы не увиделись» уходит действующему клиенту."""
    with db.get_conn() as conn:
        _bq_init(conn)
        active = {(p or "")[-10:] for (p,) in conn.execute(
            """SELECT DISTINCT u.phone FROM users u
               JOIN lesson_records lr ON lr.user_id = u.id
               JOIN lessons l ON l.id = lr.lesson_id
               WHERE lr.visit = 1 AND l.date >= '2026-06-01'
                 AND u.phone IS NOT NULL AND u.phone != ''""") if p}
        rows = conn.execute(
            "SELECT id, phone FROM broadcast_queue WHERE campaign=? AND status='pending'",
            (campaign,)).fetchall()
        ids = [rid for rid, ph in rows if (ph or "")[-10:] in active]
        for rid in ids:
            conn.execute("UPDATE broadcast_queue SET status='cancelled' WHERE id=?", (rid,))
    log.info("broadcast_prune_active: снято %d действующих семей", len(ids))
    return {"campaign": campaign, "removed": len(ids), "checked": len(rows)}


def broadcast_add(campaign: str, text: str, recipients: list) -> dict:
    """Добавляет явный список получателей [{phone, child}] в кампанию.
    Телефоны, уже присутствующие в кампании, пропускаются."""
    with db.get_conn() as conn:
        _bq_init(conn)
        existing = {r[0] for r in conn.execute(
            "SELECT phone FROM broadcast_queue WHERE campaign=?", (campaign,))}
        now = _now().isoformat(timespec="seconds")
        n = 0
        for r in recipients:
            phone = str((r or {}).get("phone") or "").strip()
            if not phone or phone in existing:
                continue
            existing.add(phone)
            conn.execute("INSERT INTO broadcast_queue (campaign, phone, child, text, created) "
                         "VALUES (?, ?, ?, ?, ?)",
                         (campaign, phone, str((r or {}).get("child") or "")[:120], text, now))
            n += 1
    log.info("broadcast_add: кампания %s — добавлено %d", campaign, n)
    return {"campaign": campaign, "added": n}


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


def _waba_template_watch() -> dict:
    """Подхватить одобренный WABA-шаблон и вернуть рассылку в строй.

    Шаблон заводится руками в кабинете Wazzup (API умеет только читать
    список), а дальше ждать модерацию Meta человеку незачем: как только
    статус стал «одобрен», id сам ложится в настройку, а транспорт
    возвращается из «off». Без шаблона отправка через WABA намеренно
    отказывает, поэтому включать транспорт заранее безопасно только
    вместе с появлением id.

    Шаблонов несколько: под лагерь один, под набор — четыре по возрастам.
    Все одобренные складываем в карту «имя → id», а какой применить к
    конкретному адресату, решает возраст ребёнка в момент отправки."""
    try:
        items = wazzup.templates()
    except Exception as e:
        log.warning("шаблоны WABA недоступны: %s", e)
        return {"ok": False, "ошибка": str(e)[:120]}
    ok = {}
    for t in items:
        if str(t.get("status") or "").lower() not in {"approved", "active", "одобрен"}:
            continue
        name, tid = (t.get("name") or "").strip(), (t.get("id") or t.get("templateId"))
        if name and tid:
            ok[name] = str(tid)
    if not ok:
        return {"ok": True, "одобренных_нет": True, "всего_шаблонов": len(items)}
    db.set_setting("waba_templates", json.dumps(ok, ensure_ascii=False))
    # Запасной шаблон на случай, когда возраст ребёнка неизвестен:
    # универсальный, а если его ещё не одобрили — любой доступный.
    if not db.get_setting("waba_template_id"):
        pick = ok.get("nabor_bez_vozrasta") or ok.get("nabor_5_7_let") \
            or next(iter(ok.values()))
        db.set_setting("waba_template_id", pick)
    if (db.get_setting("broadcast_transports", "") or "").strip() == "off":
        db.set_setting("broadcast_transports", "whatsapp")
    log.info("одобренных WABA-шаблонов: %d (%s) — рассылка включена",
             len(ok), ", ".join(sorted(ok)))
    return {"ok": True, "шаблоны": ok, "рассылка": "включена"}


# Возрастные шаблоны набора: до какого возраста действует каждый.
AGE_TEMPLATES = [(3.0, "nabor_1_3_goda"), (5.0, "nabor_3_5_let"),
                 (7.0, "nabor_5_7_let"), (99.0, "nabor_7_12_let")]


def _template_for(phone: str, campaign: str) -> str | None:
    """Какой шаблон слать этому адресату.

    У кампании лагеря шаблон один на всех. У набора — четыре по возрасту
    ребёнка: пятилетке незачем читать про мини-сад, а двухлетке про
    кембриджские уровни. Возраст неизвестен — берём 5–7 лет, это самый
    широкий сегмент и самый ходовой продукт."""
    try:
        tpls = json.loads(db.get_setting("waba_templates", "") or "{}")
    except ValueError:
        tpls = {}
    if not tpls:
        return db.get_setting("waba_template_id") or None
    if not campaign.startswith("nabor"):
        camp = next((v for k, v in tpls.items() if k.startswith("lager")), None)
        return camp or db.get_setting("waba_template_id") or None
    age = _age_by_phone(phone)
    # Возраст неизвестен у половины базы (3162 карточки из 6996). Раньше
    # им уходил шаблон 5–7 лет, то есть разговор про подготовку к школе —
    # мимо для всех, кроме дошкольников. Универсальный вместо угадывания
    # просит назвать возраст: ответ заодно чинит карточку.
    name = "nabor_bez_vozrasta"
    if age is not None:
        name = next(n for lim, n in AGE_TEMPLATES if age < lim)
    return tpls.get(name) or tpls.get("nabor_bez_vozrasta") \
        or tpls.get("nabor_5_7_let") or db.get_setting("waba_template_id") or None


def _age_by_phone(phone: str) -> float | None:
    """Возраст ребёнка на 1 сентября по телефону из карточки."""
    digits = "".join(c for c in str(phone or "") if c.isdigit())[-10:]
    if not digits:
        return None
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT raw FROM users WHERE substr(replace(replace(replace("
                "phone,' ',''),'-',''),'+',''), -10)=? LIMIT 1", (digits,)).fetchone()
    except Exception:
        return None
    if not row or not row[0]:
        return None
    try:
        j = json.loads(row[0])
    except ValueError:
        return None
    for a in (j.get("attributes") or []):
        if a.get("attributeAlias") == "birthday" and a.get("value"):
            try:
                bd = date.fromisoformat(str(a["value"])[:10])
            except ValueError:
                return None
            return round((date(2026, 9, 1) - bd).days / 365.25, 1)
    return None


def _uid_by_phone(phone: str) -> int | None:
    """Карточка МойКласс по телефону — нужна Телеграму для chatId."""
    digits = "".join(c for c in str(phone or "") if c.isdigit())[-10:]
    if not digits:
        return None
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE substr(replace(replace(replace("
                "phone,' ',''),'-',''),'+',''), -10)=? LIMIT 1", (digits,)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _broadcast_tick() -> None:
    """Раз в минуту: каскадная доставка очереди. Порядок каналов —
    настройка broadcast_transports (по умолчанию "tgapi,whatsapp,max").
    Telegram — общий темп (broadcast_per_hour, 60/час), WhatsApp — щадяще:
    не чаще 15/час и не больше 50/день (после бана 0077), MAX — 30/час.
    Строка уходит в ПЕРВЫЙ доставивший канал; недоставленное копит попытки
    в колонке tried и ждёт своего канала."""
    now = _now()
    db.set_setting("broadcast_last_tick", now.isoformat(timespec="seconds"))
    # окно отправки: с 10:00 до broadcast_until (по умолчанию 19:00)
    until = (db.get_setting("broadcast_until", "19:00") or "19:00").strip()
    try:
        uh, um = (int(x) for x in until.split(":"))
    except Exception:
        uh, um = 19, 0
    if now.hour < 10 or (now.hour, now.minute) >= (uh, um):
        return
    # 12.08: инициирующие рассылки — только WhatsApp, независимо от настройки.
    # Telegram «принимается» Wazzup-ом, но не доставляется незнакомым номерам
    # (упали все 100% отправок), MAX заблокирован. Настройка broadcast_transports
    # может лишь сузить (выключить и WhatsApp), но не вернуть tgapi/max.
    setting = [x.strip() for x in
               (db.get_setting("broadcast_transports", "whatsapp") or "whatsapp").split(",") if x.strip()]
    if "whatsapp" not in setting:
        return
    # темп: wa_per_hour сообщений в час суммарно (по умолчанию 6)
    per_hour = max(1, min(30, int(db.get_setting("wa_per_hour", "6") or 6)))
    if now.minute % max(1, 60 // per_hour) != 0:
        return
    # джиттер: ~20% слотов пропускаем, чтобы отправки не шли строго по
    # расписанию (ровный интервал — признак бота для антиспама WhatsApp)
    if random.random() < 0.2:
        return
    # ротация номеров: каждому активному WhatsApp-номеру — свой дневной
    # лимит wa_daily_cap (по умолчанию 25); шлём с наименее загруженного
    try:
        chans = wazzup.channels()
    except Exception as e:
        log.warning("wazzup channels недоступны: %s", e)
        return
    # только реально живые номера: у заблокированного канала Wazzup принимает
    # сообщение, но не доставляет — при высоком темпе это потеря всей порции
    ok_states = {"active", "opened", "ready"}
    # WABA (transport «wapi») — такой же отправитель, как обычный WhatsApp,
    # и для массовых рассылок он основной: это официальный канал Meta с
    # суточным лимитом в тысячи сообщений, тогда как обычные номера живут
    # под угрозой бана и идут по квотам в десятки. До 22.08 очередь искала
    # только transport == "whatsapp" и WABA не видела вовсе.
    wa_kinds = {"whatsapp", "wapi"}
    transport_of = {c.get("plainId"): c.get("transport") for c in chans
                    if c.get("transport") in wa_kinds
                    and str(c.get("state") or "").lower() in ok_states}
    active = [c.get("plainId") for c in chans
              if c.get("transport") in wa_kinds
              and str(c.get("state") or "").lower() in ok_states]
    pref = [p.strip() for p in (db.get_setting(
        "wa_senders", wazzup.WHATSAPP_PREFERRED) or "").split(",") if p.strip()]
    active = [p for p in pref if p in active] or active
    if not active:
        return
    cap = int(db.get_setting("wa_daily_cap", "25") or 25)
    # индивидуальные квоты номеров: wa_caps = {"7916…": 40, …}. Нужны, чтобы
    # свежий номер прогревался малыми объёмами, а основной 0077 почти не
    # участвовал (у него история банов — беречь как канал переписки)
    try:
        caps = {str(k): int(v) for k, v in json.loads(
            db.get_setting("wa_caps", "") or "{}").items()}
    except Exception:
        caps = {}
    day = now.strftime("%Y-%m-%d")
    with db.get_conn() as conn:
        _bq_init(conn)
        for ddl in ("ALTER TABLE broadcast_queue ADD COLUMN tried TEXT DEFAULT ''",
                    "ALTER TABLE broadcast_queue ADD COLUMN sender TEXT"):
            try:
                conn.execute(ddl)
            except Exception:
                pass
        counts = dict(conn.execute(
            "SELECT COALESCE(sender,''), COUNT(*) FROM broadcast_queue "
            "WHERE status='sent' AND sent LIKE ? GROUP BY COALESCE(sender,'')",
            (day + "%",)).fetchall())
        legacy = counts.get("", 0)  # строки до ротации — вешаем на первый номер
        def used(p):
            return counts.get(p, 0) + (legacy if p == active[0] else 0)
        def limit(p):
            return caps.get(p, cap)
        # шлём с номера, у которого больше всего свободной квоты
        candidates = [p for p in active if used(p) < limit(p)]
        if not candidates:
            return                      # все номера выбрали дневной лимит
        sender = max(candidates, key=lambda p: limit(p) - used(p))
        rows = conn.execute(
            "SELECT id, phone, child, text, COALESCE(tried,''), campaign "
            "FROM broadcast_queue "
            "WHERE status='pending' "
            # Всё, у чего дедлайн — события 29.08–06.09, идёт вперёд лагерной
            # рассылки: после события такое сообщение теряет смысл целиком.
            # promo_nedozvon сюда добавлен 20.08: 57 промо-контактов, до которых
            # не дозвонились, стояли в самом хвосте очереди и по темпу дошли бы
            # до отправки уже после праздника — то есть никогда.
            # promo_nedozvon — самый скоропортящийся сегмент: человек отдал номер
            # промоутеру на улице и помнит нас несколько дней. Поставленный в
            # общую группу с приглашениями, он всё равно уходил в хвост по id
            # (заведён позже) и ждал бы отправки 3 дня. Поэтому отдельный,
            # высший приоритет — выше приглашений.
            # camp_aug26 добавлен 22.08 выше приглашений: у лагерной смены
            # жёсткая дата — 24 августа. Приглашение, ушедшее на день позже,
            # ничего не теряет; предложение недели, ушедшее после её начала,
            # бессмысленно целиком. Очередь приглашений (263 строки) отодвигала
            # лагерь за пределы этого срока.
            "ORDER BY campaign = 'no1_apology' DESC, "
            "campaign = 'promo_nedozvon' DESC, "
            "campaign = 'camp_aug26' DESC, "
            "campaign LIKE 'invite%' DESC, id "
            "LIMIT 30").fetchall()
    dry = db.get_setting("wazzup_dry_run", "1") == "1"
    team = _team_phones()
    # сколько сообщений WABA отправляет за один тик (настройка wapi_burst)
    wapi_burst = max(1, min(25, int(db.get_setting("wapi_burst", "8") or 8)))
    sent_this_tick = 0
    for rid, phone, child, text, tried, campaign in rows:
        # Недоставляемым считаем только после попытки ОБОИМИ каналами: у
        # обычного номера и у WABA разные причины отказа, и неудача с одного
        # ничего не говорит о втором. Прежняя проверка по одной метке
        # хоронила строку после первой же осечки обычного номера — в том
        # числе когда осечка была вызвана нашим же заблокированным каналом.
        if "whatsapp=" in tried and "wapi=" in tried:
            with db.get_conn() as conn:
                conn.execute("UPDATE broadcast_queue SET status='undeliverable' "
                             "WHERE id=?", (rid,))
            continue
        # какой канал этой строке положен — решаем здесь, до проверки
        # прошлых попыток: иначе повтор уходит тем же каналом, что уже
        # не сработал
        try:
            pref = wazzup.best_channel(phone, mass=True)
        except Exception:
            pref = None
        if pref in ("tgapi", "max"):
            tr, snd = pref, None
        else:
            tr, snd = transport_of.get(sender, "whatsapp"), sender
        if f"{tr}=" in tried:
            continue                    # этим каналом уже пробовали — ждём другого
        # свои номера в очередь попадают легко: у сотрудников и педагогов
        # бывают карточки детей. Продающая рассылка своему человеку —
        # позор, поэтому проверяем перед каждой отправкой, а не при постановке
        if (phone or "")[-10:] in team:
            with db.get_conn() as conn:
                conn.execute("UPDATE broadcast_queue SET status='cancelled' WHERE id=?", (rid,))
            log.info("broadcast: #%s — свой номер (%s), не шлём", rid, team[phone[-10:]])
            continue
        if _wa_unanswered(phone):
            continue  # ждёт ответа админа — не сбрасываем непрочитанное, вернёмся позже
        msg = _fill_name(text, child)
        try:
            # у шаблона WABA текст утверждён Meta и не меняется — от нас
            # идут только подстановки. Переменная одна: имя ребёнка
            vals = [_child_name(child) or "ваш ребёнок"] if tr == "wapi" else None
            tid = _template_for(phone, campaign or "") if tr == "wapi" else None
            # Телеграму нужен id карточки: у telegram-чата нет телефона,
            # и отправка «на номер» возвращает BAD_CONTACT
            ok = wazzup.send_via(tr, phone, msg, dry_run=dry, sender=snd,
                                 template_values=vals, template_id=tid,
                                 uid=_uid_by_phone(phone) if tr == "tgapi" else None)
        except Exception as e:
            log.warning("wazzup %s недоступен: %s", tr, e)
            ok = False
        # метка и отправитель — фактические, иначе в журнале не разобрать,
        # каким каналом сообщение уходило и почему не дошло
        mark = f"{tr}={'ok' if ok else 'fail'};"
        with db.get_conn() as conn:
            if ok:
                conn.execute("UPDATE broadcast_queue SET status='sent', sent=?, sender=?, "
                             "tried=COALESCE(tried,'')||? WHERE id=?",
                             (_now().isoformat(timespec="seconds"), snd or tr, mark, rid))
            else:
                conn.execute("UPDATE broadcast_queue SET tried=COALESCE(tried,'')||? "
                             "WHERE id=?", (mark, rid))
        log.info("broadcast: #%s %s -> %s(%s) %s", rid, phone[-4:], tr,
                 (snd or "—")[-4:], "ok" if ok else "fail")
        # Обычный номер шлёт по одному сообщению за тик: ровный редкий темп —
        # его единственная защита от бана. У WABA такой угрозы нет, это
        # официальный канал с суточным лимитом в тысячи, и по одному в минуту
        # очередь в несколько сотен растянулась бы на дни — к дате смены
        # рассылка просто не успевала бы.
        sent_this_tick += 1
        if tr != "wapi" or sent_this_tick >= wapi_burst:
            break


def sync_admin_phones(mk: MoyklassClient) -> dict:
    """Телефоны сотрудников из CRM в настройку admin_phones.

    По ним уходит справка о клиенте в момент входящего звонка — админ
    видит, кто звонит и что ему говорить, ДО того как снял трубку.
    Настройка заполнялась руками, поэтому стояла пустой, и подсказки
    молча никуда не уходили: механика была, а телефонов не было."""
    try:
        r = mk.get("/v1/company/managers", {"limit": 100})
        ms = r.get("managers") if isinstance(r, dict) else r
    except Exception:
        return {}
    out = {}
    for m in ms or []:
        ph = "".join(c for c in str(m.get("phone") or "") if c.isdigit())
        if len(ph) >= 11 and m.get("id"):
            out[str(m["id"])] = ph[-11:]
    if out:
        db.set_setting("admin_phones", json.dumps(out))
        log.info("телефоны сотрудников обновлены: %d", len(out))
    return out


def _admins() -> list[dict]:
    try:
        return json.loads(db.get_setting("call_admins") or "[]")
    except ValueError:
        return []


def _admins_today() -> list[dict]:
    """Кто сегодня на смене. admin_schedule: {"2026-08-18": 232805} или
    {"2026-08-18": [232805, 202856]} — двое в смене; задачи делятся между ними.
    Нет записи на сегодня — работают все из call_admins."""
    admins = _admins()
    try:
        sched = json.loads(db.get_setting("admin_schedule") or "{}")
    except ValueError:
        sched = {}
    mid = sched.get(_today().isoformat())
    # один id или список [id, id] — когда в смене работают двое
    mids = mid if isinstance(mid, list) else ([mid] if mid else [])
    if mids:
        onduty = [a for a in admins if a.get("managerId") in mids]
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


# виды задач в МойКласс (справочник taskCategories). Категория отвечает на
# вопрос «как быстро и каким навыком это делать», а не «про какого клиента».
CAT_URGENT = 44337   # 🔥 Срочно (15 минут): свежий лид, пропущенный, готовы платить
CAT_CHAT = 104575    # 💬 Ответить в чате: переписка, SLA 30 минут (умеет и Лиза)
CAT_CALL = 104576    # 📞 Прозвон базы: плановые продающие звонки блоками
CAT_PUSH = 104577    # 🔁 Дожим: повторное касание, контроль обещания
CAT_ORG = 104578     # 🏢 Организационное: внутренние дела, мёртвые часы
CAT_PLAIN = 44336    # Обычная — запасная


def _task_category(body: str, user_id: int | None) -> int:
    """Вид задачи по её тексту. Порядок правил = порядок приоритета."""
    b = (body or "").lower()

    def has(*keys: str) -> bool:
        return any(k in b for k in keys)

    if not user_id and has("объявление", "промоутер", "яндекс.бизнес", "2гис", "zoon",
                           "карточки на картах", "дизайн", "распечат", "печать",
                           "инвентар", "договор", "прицеп", "юрпакет"):
        return CAT_ORG
    if has("новая заявка", "свежая заявка", "заявка с вечера", "без звонка",
           "пропущенный звонок", "готовы оплатить", "запись+оплата", "данные пришли",
           "марквиз", "новый контакт"):
        return CAT_URGENT
    if has("недозвон", "повторн", "не взял трубку", "дожать", "дожим", "думает",
           "не пришёл", "не пришел", "no-show", "контроль:", "дедлайн обещания"):
        return CAT_PUSH
    if has("whatsapp", "wazzup", "в чате", "непрочитанное", "написать",
           "отправить расписание", "ответить", "переписк", "мониторить ответы",
           "ответ в", "ответ на рассылку", "(max", "max,"):
        return CAT_CHAT
    if has("продолжение занятий", "обзвон", "продающий звонок", "продление",
           "тёплый", "теплый", "позвонить", "звонок", "прозвон"):
        return CAT_CALL
    return CAT_PLAIN


# Владелец не должен получать задачи по лидам. 19.08 к Борису так налипло
# 178 просроченных: «продолжение занятий», «ответил смайликом», продающие
# звонки по лагерю — обычная работа обзвона, которая копилась четыре дня,
# потому что владелец её физически не делает. Задача владельцу ставится,
# только когда нужно ЕГО решение: доступы, договоры, деньги наружу, люди.
OWNER_ID = 84116
OWNER_ONLY = re.compile(
    r"реши|согласова|доступ|логин|парол|токен|договор|аренд|реклам|бюджет|"
    r"нанять|найм|уволь|закуп|списать|учредител|юрлиц|лиценз|партнёр|стратег|"
    r"сайт|домен|тариф", re.I)


def _task(mk: MoyklassClient, manager_id: int, user_id: int | None,
          body: str, day: date | None = None) -> None:
    if manager_id == OWNER_ID and not OWNER_ONLY.search(body or ""):
        alt = (_admins_today() or _admins())
        if alt:
            log.info("задача владельцу перенаправлена дежурному: %s", body[:60])
            manager_id = alt[0]["managerId"]
    d = (day or _today()).isoformat()
    payload = {"body": body, "beginDate": f"{d}T09:00:00+03:00",
               "endDate": f"{d}T20:00:00+03:00",
               "isAllDay": True, "managerIds": [manager_id],
               "categoryId": _task_category(body, user_id)}
    if user_id:
        payload["userId"] = user_id
    mk.post("/v1/company/tasks", payload)


def _wa_unanswered(phone: str) -> bool:
    """Клиент написал нам, а мы (люди или робот) ещё не ответили после этого.
    Автосообщения таким не шлём: исходящее сбрасывает непрочитанное в Wazzup,
    и админ не увидит, что клиент ждёт ответа."""
    p = "".join(ch for ch in str(phone or "") if ch.isdigit())[-10:]
    if len(p) < 10:
        return False
    with db.get_conn() as conn:
        try:
            last_in = conn.execute(
                "SELECT MAX(ts) FROM wazzup_inbox WHERE substr(phone,-10)=? "
                "AND chat_type!='manual'", (p,)).fetchone()[0]
            if not last_in:
                return False
            last_out = conn.execute(
                "SELECT MAX(ts) FROM wazzup_outbox WHERE substr(phone,-10)=?",
                (p,)).fetchone()[0]
        except Exception:
            return False
    return not last_out or last_in > last_out


# Центр работает с 9:00 до 20:00 по Москве, и автоматика пишет клиентам
# только в эти часы. Сообщение в девять вечера читается как беспокойство,
# а ответить на него всё равно некому: администратор уже ушёл. Проверка
# стоит в одной точке — через _wa проходят все автосообщения, и добавить
# новый сценарий в обход рабочего дня теперь нельзя.
WA_HOUR_FROM, WA_HOUR_TO = 9, 20


def _wa(phone: str, text: str, mode: str = "broadcast",
        kind: str = "") -> bool | None:
    """broadcast — во все мессенджеры (WhatsApp+Telegram+MAX): у кого какой есть."""
    hour = _now().hour
    if not (WA_HOUR_FROM <= hour < WA_HOUR_TO) \
            and phone != (db.get_setting("digest_phone") or ""):
        log.info("wazzup: %s — сейчас %d:00 МСК, вне рабочих часов, не пишем",
                 phone[-4:], hour)
        return None
    if phone != (db.get_setting("digest_phone") or "") and _wa_unanswered(phone):
        log.info("wazzup: %s ждёт ответа админа — автосообщение отложено", phone[-4:])
        return None
    dry = db.get_setting("wazzup_dry_run", "1") == "1"
    try:
        lines = wazzup.send(phone, text, mode=mode, dry_run=dry, kind=kind)
        for line in lines:
            log.info("wazzup: %s", line)
        # HTTP 20x хотя бы в одном канале — сообщение принято к доставке
        return any("HTTP 20" in ln or ln.startswith("[dry-run]") for ln in lines)
    except Exception as e:  # ключа может не быть — сценарии не должны падать
        log.warning("wazzup недоступен: %s", e)
        return False


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
    # служебные «группы-накопители» (заявки из рекламы, лист ожидания) названием
    # курса не описывают запрос клиента — подставлять их в подсказку бессмысленно
    if course and any(s in course.lower() for s in ("не создавайте", "заявк", "roistat")):
        course = None
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
        "Акция: при оплате до 31.08 включительно сентябрь идёт по ценам прошлого учебного года "
        "(продаём только помесячно). ВАЖНО: старая цена и скидка 10% НЕ складываются — "
        "считаем оба варианта и предлагаем выгодный.",
        "",
        "🏁 ФИНАЛ ЗВОНКА — три шага, проговорить обязательно:",
        "1) Запись с датой: «вторник-четверг в 16:00 или понедельник-среда в 17:00 — какая удобнее?» "
        "Не «я перезвоню» и не «оставлю в заявке».",
        "2) Событие: 29.08 (сб) 11:00 — праздник в парке «Янтарная горка» рядом с ЖК Богородский "
        "(НЕ в центре!), вход свободный, без записи; 30.08 (вс) — день открытых дверей в центре "
        "(KidsUPday.ru); "
        "31.08–06.09 — первое занятие своей группы (оно же открытый урок).",
        "3) Новые направления: «открываем танцы, хореографию, футбол, единоборства, акробатику, "
        "актёрское мастерство, технику речи — что-то интересно? Запишу в лист ожидания, первой группе −10%».",
        "Формулировка первого занятия — только «условно-бесплатное». Исключения: ИЗО/шахматы/танцы 850 ₽, "
        "робототехника 1 100 ₽, если абонемент не куплен.",
        "",
        "❓ СНАЧАЛА СПРОСИТЬ, ПОТОМ ПРЕДЛАГАТЬ (объясните родителю, зачем спрашиваете):",
        "«Что для вас сейчас самое важное в занятиях?» · «Был ли опыт похожих занятий — что "
        "понравилось, а что нет?» · «Какие дни и время вам ТОЧНО не подходят?» · «Что важнее: "
        "педагог, программа, результат, расписание или цена?»",
        "",
        "🛡 ВОЗРАЖЕНИЕ: смягчение → уточнение → аргументация → завершение.",
        "1) Смягчить: «Понимаю, в начале года собрать расписание ребёнка — испытание».",
        "2) Уточнить настоящую причину: «Правильно понимаю, что занятия подходят, а вопрос "
        "во времени?» — «дорого» у разных людей значит разное.",
        "3) Аргументировать ТОЛЬКО названное сомнение, а не все плюсы центра подряд. "
        "Скидку называем после объяснения ценности, а не вместо неё.",
        "4) Завершить конкретикой: «Записать во вторник или в четверг?», «Напишу в пятницу».",
        "НИКОГДА не заканчивать словами «обращайтесь, когда решите» — клиент не вернётся.",
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
        # Одна задача на семью в день, а не на каждую заявку. 25.08 у одной
        # мамы было четыре заявки — логопед, подготовка, ИЗО, лепка, — и
        # администратор получил четыре одинаковых «позвонить в течение
        # 5 минут» по одному и тому же человеку. Звонок всё равно один,
        # а четыре карточки в списке дел выглядят как сбой и обесценивают
        # значок 🔥 на всех остальных задачах.
        if j.get("userId") and not _mark("lead_task_user",
                                         f"{j['userId']}:{today}"):
            _mark("lead_task", str(j["id"]))
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


# клиент написал — а мы молчим -----------------------------------------

# служебные уведомления самого Wazzup — не клиентские сообщения
_SERVICE_RE = re.compile(r"канал (заработал|не работает)", re.I)
UNANSWERED_MIN = 45          # сколько минут ждём ответа админа, прежде чем звать
HOT_WORDS = ("оплат", "запиш", "записать", "пришлите", "пришлите расписание",
             "хотим", "можно", "когда можно", "давайте", "да,", "готовы")


def _is_reaction(text: str) -> bool:
    """Одни эмодзи/реакция без слов."""
    return not any(ch.isalnum() for ch in text or "")


def _got_broadcast(conn, phone: str, days: int = 14) -> bool:
    """Слали ли мы этому номеру рассылку за последние days дней.

    Важно: в рассылках мы прямо просим «ответьте просто смайликом», поэтому
    эмодзи от получателя рассылки — это ОТВЕТ и тёплый сигнал, а не шум.
    """
    since = (_now() - timedelta(days=days)).isoformat(timespec="seconds")
    try:
        return bool(conn.execute(
            "SELECT 1 FROM broadcast_queue WHERE status='sent' AND sent >= ? "
            "AND substr(phone,-10) = ? LIMIT 1", (since, phone[-10:])).fetchone())
    except Exception:
        return False


def _is_phone(x: str) -> bool:
    """Российский мобильный, а не идентификатор чата.

    У клиентов из Telegram и MAX номер не передаётся — приходит внутренний
    id аккаунта. Раньше он подставлялся в задачу как «+5113895858»: выглядит
    телефоном, набрать нельзя, найти человека тоже. Администратор видел
    задачу и не понимал, кому отвечать."""
    d = "".join(c for c in str(x or "") if c.isdigit())[-10:]
    # Тот же критерий, что и для пропущенных звонков: российский код зоны
    # начинается с 3, 4, 8 (география и сервисные) или 9 (мобильные).
    # Проверять только «начинается с девятки» нельзя — городские номера
    # 495 и 812 живые, и они уехали бы в «Telegram».
    return _real_number(d)


def _who_label(phone: str, name: str, chat_type: str | None) -> str:
    """Как назвать собеседника в теле задачи."""
    kind = {"telegram": "Telegram", "tgapi": "Telegram", "max": "MAX",
            "instagram": "Instagram", "vk": "ВКонтакте"}.get(
        (chat_type or "").lower(), "")
    if _is_phone(phone):
        return f"+7{''.join(c for c in phone if c.isdigit())[-10:]}" + \
               (f", {name}" if name else "")
    tail = ''.join(c for c in str(phone or '') if c.isdigit())[-6:]
    src = kind or "мессенджер"
    return (f"{name}, {src}" if name
            else f"{src}, чат …{tail} — телефона нет, искать чат в Wazzup")


def _thread(phone: str, until: str, limit: int = 12) -> list[dict]:
    """Переписка по клиенту до указанного момента — модели нужен разговор
    целиком, а не последняя реплика: «спасибо» после «пришлите цену»
    и «спасибо» после «нам не подходит» означают разное."""
    out = []
    with db.get_conn() as conn:
        for table, direction in (("wazzup_inbox", "in"), ("wazzup_outbox", "out")):
            try:
                rows = conn.execute(
                    f"SELECT ts, text FROM {table} WHERE phone LIKE ? AND ts <= ? "
                    f"ORDER BY ts DESC LIMIT ?",
                    (f"%{phone[-10:]}", until, limit)).fetchall()
            except Exception:
                rows = []
            out += [{"ts": ts, "dir": direction, "text": t or ""} for ts, t in rows]
    return sorted(out, key=lambda m: m["ts"])[-limit:]


def unanswered_inbound(mk: MoyklassClient) -> None:
    """Клиент написал в Wazzup, прошло UNANSWERED_MIN минут, ответа от нас нет →
    задача админу. Смайлик от получателя рассылки — тёплый сигнал (мы сами
    просили ответить смайликом); смайлик вне рассылки и служебные уведомления
    Wazzup пропускаем. Одна задача на номер в день."""
    now = _now()
    if not (10 <= now.hour < 20):
        return
    cutoff = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    with db.get_conn() as conn:
        try:
            inbox = conn.execute(
                "SELECT phone, MAX(ts), chat_type, text FROM wazzup_inbox "
                "WHERE ts >= ? AND chat_type != 'manual' GROUP BY phone", (cutoff,)).fetchall()
            outbox = dict(conn.execute(
                "SELECT phone, MAX(ts) FROM wazzup_outbox GROUP BY phone").fetchall())
        except Exception:
            return  # таблиц ещё нет — вебхук не приносил сообщений
        after_broadcast = {p: _got_broadcast(conn, p) for p, *_ in inbox}
    chat_admin = int(db.get_setting("chat_admin", "154181") or 154181)
    admins = _admins_today()
    fallback = admins[0]["managerId"] if admins else chat_admin
    for phone, ts_in, chat_type, text in inbox:
        text = (text or "").strip()
        reaction = _is_reaction(text)
        warm = reaction and after_broadcast.get(phone)   # ответ смайликом на рассылку
        if (reaction and not warm) or _SERVICE_RE.search(text):
            continue
        if ts_in > (now - timedelta(minutes=UNANSWERED_MIN)).isoformat(timespec="seconds"):
            continue                      # ещё есть время ответить по-человечески
        if outbox.get(phone, "") > ts_in:
            continue                      # уже ответили
        if not _mark("inbox_task", f"{_today().isoformat()}:{phone[-10:]}"):
            continue                      # задача по этому номеру сегодня уже есть
        uid, name = None, ""
        try:
            found = mk.get("/v1/company/users", {"phone": phone[-10:], "limit": 3})
            users = found.get("users", found) if isinstance(found, dict) else found
            if users:
                uid, name = users[0].get("id"), (users[0].get("name") or "")[:28]
        except Exception:
            log.warning("unanswered_inbound: клиент по номеру %s не найден", phone[-10:])
        # Срочность и намерение — смыслом, а не списком слов. «Мы подумаем
        # и вернёмся в сентябре» и «подумайте, как записаться прямо сейчас»
        # содержат одно и то же слово, но требуют разного. Модель читает
        # переписку целиком; если её нет — работает прежний список слов.
        sense = None
        if brain.enabled():
            sense = brain.read_dialog(_thread(phone, ts_in))
        hot = (sense.get("срочность") == "горит" if sense
               else any(w in text.lower() for w in HOT_WORDS))
        who = _who_label(phone, name, chat_type)
        if warm:
            # мы просили «ответьте смайликом» — смайлик и есть ответ: тёплый
            body = (f"🔥 Ответил(а) на рассылку смайликом {text[:6]} ({ts_in[11:16]}, {who}) — "
                    f"тёплый. Спросить про планы на сентябрь, позвать 29.08 на праздник, "
                    f"подобрать группу по возрасту.")
            owner = admins[0]["managerId"] if admins else chat_admin   # это звонок
        else:
            head = "🔥 КЛИЕНТ ЖДЁТ ОТВЕТА" if hot else "Клиент писал, ответа нет"
            where = "в WhatsApp" if _is_phone(phone) else "в том же чате"
            if sense and sense.get("следующий_шаг"):
                # Модель говорит, что именно сделать, — это полезнее общего
                # «ответить и поставить следующий шаг».
                what = sense["следующий_шаг"]
                mark = f"[{sense.get('намерение')}] " if sense.get("намерение") else ""
                body = (f"{head} ({ts_in[11:16]}, {who}): «{text[:70]}» — "
                        f"{mark}{what} Ответить {where}.")
            else:
                body = (f"{head} ({ts_in[11:16]}, {who}): «{text[:90]}» — "
                        f"ответить {where} и поставить следующий шаг.")
            owner = chat_admin or fallback
            # Отказ не требует ответа в чате — он требует пометки в карточке,
            # иначе человека продолжат звать и раздражать.
            if sense and sense.get("намерение") == "отказ" \
                    and not sense.get("ждёт_ответа"):
                body = (f"Отказ ({ts_in[11:16]}, {who}): «{text[:80]}» — "
                        f"поставить статус «Отказ» с причиной и не звонить.")
        _task(mk, owner, uid, body[:250])
        log.info("unanswered_inbound: задача по %s (%s)", phone[-10:],
                 "тёплый-смайлик" if warm else ("горячий" if hot else "обычный"))


def trial_reminder(mk: MoyklassClient) -> None:
    """Напоминание за день до пробного занятия.

    Скрипт из интенсива «Система набора групп», урок «Напоминание о МК»:
    повторить адрес, назвать время, попросить прийти на 10 минут раньше и
    обязательно спросить «Вы подойдёте?» — ответ клиента сам по себе поднимает
    доходимость, потому что превращается в маленькое обязательство.
    До этого напоминания у нас не было вовсе: 19.08 мама записалась на пробное
    и сама попросила «а можно мне напомнить, чтобы я не забыла»."""
    tomorrow = (_today() + timedelta(days=1)).isoformat()
    try:
        recs = mk.fetch_all("/v1/company/lessonRecords", ["lessonRecords"], params={
            "date": tomorrow, "test": "true", "includeLessons": "true"})
    except Exception:
        log.exception("не удалось получить записи на завтрашние пробные")
        return
    for r in recs:
        uid = r.get("userId")
        if not uid or not _mark("trial_reminder", str(r.get("id"))):
            continue
        lesson = r.get("lesson") or {}
        begin = (lesson.get("beginTime") or "")[:5]
        try:
            user = mk.get(f"/v1/company/users/{uid}")
        except Exception:
            continue
        phone = user.get("phone")
        if not phone:
            continue
        child = _child_name(user.get("name") or "") or "вашего ребёнка"
        when = f"завтра в {begin}" if begin else "завтра"
        _wa(phone, f"Здравствуйте! Это KidsUP на Бульваре Рокоссовского 🌿\n"
                   f"Напоминаем: {child} ждём на пробном занятии {when}.\n"
                   f"Адрес: б-р Маршала Рокоссовского, 6 к1В — 7-й подъезд, 2 этаж "
                   f"(напротив ТЦ «Янтарь»).\n"
                   f"Придите, пожалуйста, на 10 минут раньше: заполнить документы и спокойно "
                   f"переодеться. Нужна сменная обувь, бахилы дадим.\n"
                   f"Первое занятие условно-бесплатное: не понравится — платить не нужно, "
                   f"понравится — войдёт в первый абонемент.\n"
                   f"Вы подойдёте?", kind="trial_reminder")
        log.info("напоминание о пробном: %s на %s", phone[-4:], when)


def booking_summary(mk: MoyklassClient) -> None:
    """Резюме сразу после записи на пробное: дата, время, адрес, что взять.

    «Книга продаж 2025», приём 4: родитель записывается голосом и через час
    уже не помнит деталей — резюме в мессенджере снимает половину неявок
    и вопросов «а куда идти»."""
    today = _today().isoformat()
    tomorrow = (_today() + timedelta(days=1)).isoformat()
    try:
        joins = mk.fetch_all("/v1/company/joins", ["joins"], params={
            "statusId": ST_JOIN_BOOKED, "createdAt": [today, tomorrow]})
    except Exception:
        log.exception("booking_summary: не удалось получить записи")
        return
    for j in joins:
        if not _mark("booking_summary", str(j.get("id"))):
            continue
        try:
            user = mk.get(f"/v1/company/users/{j.get('userId')}")
            cls = mk.get(f"/v1/company/classes/{j.get('classId')}")
        except Exception:
            continue
        phone = user.get("phone")
        if not phone:
            continue
        child = _child_name(user.get("name") or "") or "вашего ребёнка"
        parts = (cls.get("name") or "").split("_")
        when = " · ".join(p for p in parts[1:3] if p) or "время уточним"
        _wa(phone, f"Записали {child} — подтверждаем 🌿\n"
                   f"Занятие: {when}.\n"
                   f"Адрес: б-р Маршала Рокоссовского, 6 к1В, 7-й подъезд, 2 этаж "
                   f"(напротив ТЦ «Янтарь»).\n"
                   f"С собой сменная обувь, бахилы дадим. Придите на 10 минут раньше — "
                   f"заполнить документы.\n"
                   f"Первое занятие условно-бесплатное: не понравится — платить не нужно, "
                   f"понравится — войдёт в первый абонемент.\n"
                   f"Сохраните сообщение, накануне напомним 😊", kind="booking")
        log.info("booking_summary: %s → %s", str(phone)[-4:], when)


def after_trial(mk: MoyklassClient) -> None:
    """Окно 24 часов после пробного — главный конвертер «был → купил».

    «Книга продаж» (приём 7) и годовой маркетинговый план (действие 4):
    решение принимается в первые сутки, дальше впечатление стирается.
    Вопрос админа обязательно открытый — «какие впечатления?», а не
    «понравилось?»: закрытый даёт «да» и тишину, открытый — возражение,
    которое можно отработать."""
    admins = _admins_today()
    today = _today().isoformat()
    try:
        recs = mk.fetch_all("/v1/company/lessonRecords", ["lessonRecords"], params={
            "date": today, "test": "true", "visit": "true", "includeLessons": "true"})
    except Exception:
        log.exception("after_trial: не удалось получить посещения")
        return
    for r in recs:
        uid = r.get("userId")
        if not uid or not _mark("after_trial", str(r.get("id"))):
            continue
        lesson = r.get("lesson") or {}
        end = f"{lesson.get('date', '')} {lesson.get('endTime', '23:59')}"
        try:
            if datetime.strptime(end, "%Y-%m-%d %H:%M") > _now():
                continue                      # занятие ещё идёт
        except ValueError:
            continue
        try:
            user = mk.get(f"/v1/company/users/{uid}")
        except Exception:
            continue
        child = _child_name(user.get("name") or "") or "ребёнка"
        if admins:
            _task(mk, admins[0]["managerId"], uid,
                  "🔥 БЫЛ НА ПРОБНОМ СЕГОДНЯ — позвонить завтра до обеда, пока впечатление живое. "
                  "Вопрос открытый: «Какие у вас впечатления?», не «понравилось?». "
                  "Причина решить сейчас: скидка 10% на первый абонемент действует сутки.")
        phone = user.get("phone")
        if phone:
            _wa(phone, f"Спасибо, что пришли к нам сегодня! 🌿 Если хотите, перешлём "
                       f"комментарий педагога о том, как {child} занимался.\n"
                       f"Решите продолжить — скидка 10% на первый абонемент действует "
                       f"сутки после пробного. Подобрать группу и время?")
        log.info("after_trial: %s", str(phone)[-4:])


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


def _real_number(num: str) -> bool:
    """Существует ли такой российский код зоны.

    Коды начинаются с 3, 4 (география), 8 (география и сервисные 800)
    или 9 (мобильные). 21.08 в задачах лежали +71400138165 и +73400138165 —
    кодов 140 и 340 не существует, это подставные номера автообзвона.
    Администратор набирал их и слушал тишину."""
    return len(num) == 10 and num[0] in "3489"


def missed_inbound(mk: MoyklassClient) -> None:
    """Пропущенные ВХОДЯЩИЕ: клиент звонил, никто не взял, и мы не перезвонили
    с ответом — срочная задача дежурному админу (раз в день на номер) +
    мягкое сообщение в WhatsApp."""
    today = _today().isoformat()
    try:
        rows = mango.calls(_now().replace(hour=0, minute=0, second=0), _now())
    except Exception as e:
        log.warning("missed_inbound: mango недоступен: %s", e)
        return
    answered_back: set[str] = set()
    missed: dict[str, int] = {}
    for r in rows:
        num = (r.get("from_num") if not r.get("from_ext") else r.get("to_num")) or ""
        num = "".join(ch for ch in str(num) if ch.isdigit())[-10:]
        if len(num) < 10:
            continue
        if r.get("answer"):
            answered_back.add(num)          # поговорили (в любую сторону)
        elif not r.get("from_ext"):
            missed[num] = missed.get(num, 0) + 1
    admins = _admins_today() or _admins()
    if not admins:
        return
    with db.get_conn() as conn:
        phone_uid = { "".join(ch for ch in str(p or "") if ch.isdigit())[-10:]: uid
                      for uid, p in conn.execute("SELECT id, phone FROM users") }
    for num, times in missed.items():
        if num in answered_back or not _mark("missed_in_task", f"{today}:{num}"):
            continue
        if not _real_number(num):
            log.info("missed_inbound: +7%s — несуществующий код, пропуск", num)
            continue
        uid = phone_uid.get(num)
        mobile = num.startswith("9")
        if mobile or uid:
            _task(mk, admins[0]["managerId"], uid,
                  f"🔥 ПРОПУЩЕННЫЙ ЗВОНОК от +7{num} — перезвонить в течение "
                  "15 минут! Клиент звонил сам — самый горячий контакт дня.")
        else:
            # Городской номер, которого нет в базе, — чаще всего обзвонщик
            # или ошибка набора. Задачу ставим, но обычную: срочность,
            # потраченная на робота, обесценивает срочность вообще.
            _task(mk, admins[0]["managerId"], None,
                  f"Городской +7{num} звонил {times} раз(а) и не дозвонился. "
                  "Перезвонить между делом: если это семья — записать, если "
                  "обзвон или ошибка — пометить, чтобы больше не всплывал.")
        # В WhatsApp пишем только на мобильный: у городского его нет,
        # сообщение уходит в пустоту и портит статистику доставляемости.
        if mobile:
            _wa("7" + num, "Здравствуйте! Это детский центр KidsUP (Бульвар "
                "Рокоссовского) — видели ваш звонок, простите, что не успели "
                "ответить! Уже перезваниваем. Или напишите здесь, что "
                "подсказать? 😊", mode="cascade")
        log.info("missed_inbound: +7%s — задача%s", num,
                 " и сообщение" if mobile else "")


MISSED_COLD = (
    "Здравствуйте! Это детский центр KidsUP (м. Бульвар "
    "Рокоссовского). Звонили вам по поводу занятий 2026/27 "
    "учебного года — идёт набор групп.\n"
    "Ближайшее, куда можно просто прийти и посмотреть:\n"
    "🎉 сб 29.08 в 11:00 — праздник в парке «Янтарная горка» "
    "(рядом с ЖК Богородский): аниматоры, конкурсы, беспроигрышная "
    "лотерея, вход свободный\n"
    "🚪 вс 30.08 — день открытых дверей в центре (KidsUPday.ru)\n"
    "📚 с 31.08 — первое занятие своей группы (условно-бесплатное, "
    "с диагностикой)\n"
    "При оплате до 31.08 сентябрь — по ценам прошлого года. "
    "Когда удобно созвониться? Или ответьте здесь — подберём группу 😊")

MISSED_OUR = (
    "Здравствуйте! Это KidsUP (Бульвар Рокоссовского) 🎈\n"
    "Звонили вам{child} — не дозвонились. Хотели обсудить расписание "
    "на 2026/27 учебный год: с 31 августа стартует новый год, и мы "
    "закрепляем места в группах.\n"
    "Когда удобно созвониться? Или напишите здесь — всё подскажем 😊")


def _missed_kind(mk: MoyklassClient, phone: str) -> tuple[str, str]:
    """Кому мы звонили: своим, действующему клиенту или холодному контакту.

    Инцидент 20.08.2026: маме действующей ученицы (логопед три раза в неделю,
    платит с июня) ушёл холодный текст «идёт набор групп, приходите посмотреть,
    первое занятие условно-бесплатное». Для семьи, которая ходит к нам год,
    это выглядит так, будто нас в центре никто не знает. Причина была в том,
    что догон недозвона не смотрел на карточку вообще."""
    p10 = phone[-10:]
    if p10 in _team_phones():
        return "team", ""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, raw FROM users WHERE substr(phone,-10)=? LIMIT 1",
            (p10,)).fetchone()
    if not row:
        return "cold", ""
    child = _child_name(row["name"] or "") or ""
    try:
        state = json.loads(row["raw"] or "{}").get("clientStateId")
    except ValueError:
        state = None
    if state == ST_CLIENT:
        return "client", child
    with db.get_conn() as conn:
        learning = conn.execute(
            "SELECT 1 FROM joins WHERE user_id=? AND status_id=2 LIMIT 1",
            (row["id"],)).fetchone()
    if learning or _paid_recently(row["id"], 120):
        return "client", child
    return "cold", child


# Читаемое имя группы для сообщения клиенту: из «2627_ПШ_пн-чт_18:00_4-7
# лет_ПШ2 читающие (Гр7)» получается «Подготовка к школе · пн-чт · 18:00 ·
# ПШ2 читающие». Родителю в подтверждении нужен предмет, дни и время,
# а не внутренний код сезона.
_SUBJ_FULL = {
    "ПШ": "Подготовка к школе", "АЯ": "Английский язык", "МА": "Ментальная арифметика",
    "НК": "Мини-сад", "ШАХ": "Шахматы", "ИЗО": "ИЗО-студия",
}


# Предмет по названию группы — по нему решаем, продолжает ли ребёнок то же
# самое или приходит на новое. Код сезона и номер группы тут не важны:
# «2526_ПШ_пн-чт_17:00_Гр3» и «2627_ПШ_вт-пт_18:00_Гр7» — один предмет.
_SUBJ_PATTERNS = (
    (r"ПШ|одготовка к школе", "ПШ"),
    (r"АЯ|нглийск|Starters|Movers|Flyers", "АЯ"),
    (r"МА\b|ентальн", "МА"),
    (r"НК\b|ини-сад|нулев|ГКП", "НК"),
    (r"ШАХ|ахмат", "ШАХ"),
    (r"ИЗО|епка|ивопись", "ИЗО"),
    (r"Лицей", "ЛИЦЕЙ"),
    (r"Первая школа|аннее развитие", "РР"),
    (r"Музыка и речь", "МУЗ"),
    (r"ЛГ|огопед", "ЛГ"),
)


def _subject_key(name: str) -> str | None:
    """Предмет по названию группы. Названия за годы менялись — «2627_ПШ_…»,
    «ПШ_Гр3_…», «OLD_Начинающие…», — поэтому ищем по смыслу, а не по коду."""
    for pattern, key in _SUBJ_PATTERNS:
        if re.search(pattern, name or "", re.I):
            return key
    return None


# Учебный год 2025/26 начался 1 сентября 2025-го. Группы того года лежат
# под префиксом «2024_» (нумерация досталась от прежней CRM), часть — вовсе
# без префикса, поэтому «прошлый год» определяем по дате записи, а не по
# названию: единственный надёжный признак.
_LAST_YEAR_FROM = "2025-08-01"


def _continuing(user_joins: list, cls: dict, new_class_id) -> bool:
    """Ходил ли ребёнок на ЭТОТ ЖЕ предмет в прошлом учебном году.

    Продолжающему нельзя писать про условно-бесплатное первое занятие
    и бесплатную диагностику — решение владельца 25.08. Он ходит второй
    год, диагностику давно прошёл, а фраза про бесплатное занятие читается
    либо как насмешка, либо как обещание не платить за сентябрь."""
    key = _subject_key(cls.get(new_class_id, ""))
    if not key:
        return False
    for j in user_joins:
        nm = cls.get(j.get("classId"), "")
        if nm.startswith("2627"):
            continue                      # это и есть новая запись
        when = str(j.get("createdAt") or "")[:10]
        if when < _LAST_YEAR_FROM:
            continue                      # позапрошлые годы не считаются
        if _subject_key(nm) == key:
            return True
    return False


def _join_title(name: str) -> str:
    parts = [p for p in name.split("_") if p and not p.startswith("2627")]
    if parts and parts[0] in _SUBJ_FULL:
        parts[0] = _SUBJ_FULL[parts[0]]
    return " · ".join(parts)[:120]


def _duty_manager() -> int:
    """Первый администратор из сегодняшней смены — на него ложатся наряды."""
    import json as _json
    try:
        sched = _json.loads(db.get_setting("admin_schedule", "") or "{}")
        v = sched.get(str(_today()))
        if isinstance(v, list) and v:
            return int(v[0])
        if v:
            return int(v)
    except Exception:
        pass
    return 232805


def confirm_joins(mk: MoyklassClient) -> None:
    """Подтверждение новых записей — решение владельца 24.08.

    До этого подтверждение получали только записи, оформленные Клодом:
    родитель, которого администратор записал по телефону, клал трубку
    и не получал ничего — ни группы, ни адреса. Информация жила только
    в его памяти, а память после трёх звонков за день ненадёжна.

    ОДНО письмо на семью, а не на запись. 25.08 мама, записавшая ребёнка
    к логопеду на понедельник, на него же в пятницу и на подготовку
    к школе, получила три почти одинаковых сообщения подряд. Для
    родителя это выглядит сбоем, а для WhatsApp — поведением бота:
    в тот же день номер 0918 перестал доставлять вообще что-либо, все
    77 сообщений повисли с ошибкой. Поэтому записи копятся по клиенту
    и уходят одним списком.

    Если ни один канал не принял сообщение — ставим задачу дежурному
    подтвердить голосом. Молча потерять подтверждение хуже, чем
    потратить минуту администратора."""
    today = _today().isoformat()
    joins = mk.fetch_all("/v1/company/joins", ["joins"],
                         params={"createdAt": today}) or []
    rc = mk.get("/v1/company/classes", {"limit": 500})
    cls = {c["id"]: (c.get("name") or "")
           for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
    by_user: dict = {}
    fresh_ids: dict = {}
    for j in joins:
        nm = cls.get(j.get("classId"), "")
        if not nm.startswith("2627") or "аявк" in nm.lower():
            continue
        if str(j.get("createdAt") or "")[:10] != today:
            continue
        if j.get("statusId") not in {2, 50509, 58131, 58132, 83760}:
            continue
        if not _mark("join_confirm", str(j["id"])):
            continue
        by_user.setdefault(j["userId"], []).append(_join_title(nm))
        fresh_ids.setdefault(j["userId"], []).append(j.get("classId"))
    # Прошлогодние записи — чтобы отличить продолжающего от новичка.
    # Берём из общего кэша: отдельный запрос на каждого клиента стоил бы
    # десятков вызовов в каждом цикле.
    past: dict = {}
    if by_user:
        from . import taskguard as _tg
        try:
            for j in _tg.pull_all(mk, "/v1/company/joins", "joins"):
                if j.get("userId") in by_user:
                    past.setdefault(j["userId"], []).append(j)
        except Exception:
            log.warning("подтверждение: история записей недоступна")
    for uid, titles in by_user.items():
        try:
            u = mk.get(f"/v1/company/users/{uid}")
        except Exception:
            continue
        phone = "".join(ch for ch in (u.get("phone") or "") if ch.isdigit())[-10:]
        if len(phone) != 10:
            continue
        titles = list(dict.fromkeys(titles))
        if len(titles) == 1:
            what = f"Подтверждаем запись: {titles[0]}."
        else:
            what = ("Подтверждаем записи:\n"
                    + "\n".join(f"• {t}" for t in titles))
        # Продолжающему — ни слова про условно-бесплатное занятие
        # и диагностику: он ходит второй год и то, и другое давно прошёл.
        mine = past.get(uid, [])
        cont = all(_continuing(mine, cls, cid) for cid in fresh_ids.get(uid, []))
        ok = _wa(phone, f"Здравствуйте! {what}\n\n" + (
            f"Занятия начинаются 31 августа, всё как обычно — б-р Маршала "
            f"Рокоссовского, 6к1В. Рады, что продолжаете с нами. Если "
            f"что-то поменяется, просто ответьте здесь."
            if cont else
            f"Занятия начинаются 31 августа. Адрес: б-р Маршала "
            f"Рокоссовского, 6к1В (напротив ТЦ «Янтарь»), 2 минуты "
            f"от метро Бульвар Рокоссовского. Первое занятие "
            f"условно-бесплатное, и на нём же бесплатная диагностика — "
            f"педагог посмотрит уровень и подберёт ступень. Если "
            f"что-то поменяется, просто ответьте здесь."), kind="confirm")
        short = ", ".join(t[:34] for t in titles)
        status_note = ("отправлено" if ok else
                       "каналы не приняли, поставлена задача подтвердить голосом")
        if ok is False:
            # каналы отказали — подтверждение делает человек голосом
            admins = _admins_today()
            if admins:
                duty = admins[_today().toordinal() % len(admins)]
                _task(mk, duty["managerId"], uid,
                      f"Подтвердить запись голосом — сообщение не ушло "
                      f"(каналы недоступны): {short}"[:250])
        try:
            mk.post("/v1/company/userComments",
                    {"userId": uid, "showToUser": False,
                     "comment": (f"Авто: подтверждение записи «{short}» — "
                                 f"{status_note}.")[:1000]})
        except Exception:
            log.warning("подтверждение: комментарий не записан uid=%s", uid)
        log.info("подтверждение записи: %s → %s (%s)", short[:40], phone[-4:], ok)


def evening_recall(mk: MoyklassClient) -> None:
    """Вечерний наряд: перезвонить утренним недозвонам — решение 24.08.

    Утром две трети наборов уходят в никуда: родители на работе. Вечером
    (17:00-19:00) доля ответов заметно выше, поэтому в 16:55 дежурный
    получает задачу со ссылкой на живой список /nedozvony — там те, кому
    сегодня не дозвонились И кто не ответил на сообщение-догон."""
    from . import mango
    try:
        missed = mango.missed()
    except Exception as e:
        log.warning("вечерний наряд: Манго недоступен: %s", e)
        return
    n = len(missed)
    if not n:
        return
    duty = _duty_manager() if "_duty_manager" in globals() else 232805
    mk.post("/v1/company/tasks", {
        "managerIds": [duty], "categoryId": 44337,
        "beginDate": f"{_today()}T13:55:00+00:00",
        "endDate": f"{_today()}T17:00:00+00:00",
        "body": (f"ВЕЧЕРНИЙ ПРОЗВОН: {n} утренних недозвонов ждут второй "
                 f"попытки — вечером берут трубку чаще. Живой список: "
                 f"app.kidsup.ru/nedozvony (кто уже ответил на сообщение, "
                 f"из списка убран).")[:250]})
    log.info("вечерний наряд: %d недозвонов → задача дежурному", n)


# --- заполняемость групп нового сезона --------------------------------
# Целевой размер: у сада и нулевого класса мест больше, чем у кружковых
# групп. Точных планов по каждой группе в CRM нет, поэтому берём типовые.
# Вместимости по данным владельца 24.08: сад и нулевой класс — по 10,
# все программы раннего развития — по 7, остальные предметы — по 8.
_TARGET = (("ини-сад", 10), ("улевой", 10), ("Лицей", 7),
           ("Музыка и речь", 7), ("Первая школа", 7),
           ("_ПШ_", 8), ("_АЯ_", 8), ("ИЗО", 8), ("ШАХ", 8), ("_МА_", 8))


def _group_target(name: str) -> int:
    for pat, n in _TARGET:
        if pat in name:
            return n
    return 8


def group_fill(mk: MoyklassClient) -> list[dict]:
    """Заполняемость групп 2627: сколько записано против типового плана.

    «Горящая» группа — та, где занято меньше половины плана: звонок
    «во вторник в 17:00 осталось три места» честен и работает лучше
    любого скрипта, но только если места посчитаны, а не выдуманы."""
    joins = mk.fetch_all("/v1/company/joins", ["joins"]) or []
    rc = mk.get("/v1/company/classes", {"limit": 500})
    cls = {c["id"]: (c.get("name") or "")
           for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
    cnt: dict[int, int] = {}
    for j in joins:
        nm = cls.get(j.get("classId"), "")
        if nm.startswith("2627") and "аявк" not in nm.lower()                 and j.get("statusId") in {2, 50509, 58131, 58132, 83760}:
            cnt[j["classId"]] = cnt.get(j["classId"], 0) + 1
    out = []
    for cid, nm in cls.items():
        if not nm.startswith("2627") or "аявк" in nm.lower():
            continue
        # логопед — индивидуальные слоты, а не группы с планом набора:
        # 34 пустых получаса Елены и Марины хоронят под собой реально
        # горящие группы
        if "_ЛГ" in nm:
            continue
        got = cnt.get(cid, 0)
        tgt = _group_target(nm)
        out.append({"name": nm, "got": got, "target": tgt,
                    "free": max(0, tgt - got)})
    return sorted(out, key=lambda x: (x["got"] / max(1, x["target"]), x["name"]))


def reactivate_thinkers(mk: MoyklassClient, cap: int = 15) -> int:
    """Одно точное сообщение «думающим», до которых давно не касались.

    Статус «думает» означает состоявшийся разговор без отказа — самая
    тёплая часть базы, и она тихо остывает: на 24.08 таких 35+ без
    единого касания за трое суток. Сообщение персональное (имя ребёнка,
    предмет из его истории), не чаще раза в неделю на семью, не больше
    cap за день — это разовые письма по правилу каналов, не рассылка."""
    week = _today().isocalendar()[1]
    users = mk.fetch_all("/v1/company/users", ["users"],
                         params={"clientStateIds": 146950}) or []
    thinkers = [u for u in users if u.get("clientStateId") == 146950]
    joins = mk.fetch_all("/v1/company/joins", ["joins"]) or []
    rc = mk.get("/v1/company/classes", {"limit": 500})
    cls = {c["id"]: (c.get("name") or "")
           for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
    SUBJ = (("_ПШ_|одготовк|нулев", "подготовке к школе"),
            ("_АЯ_|_ЛК_|нглийск", "английскому языку"),
            ("ини-сад|_НК_", "мини-саду"), ("ИЗО", "ИЗО-студии"),
            ("ШАХ", "шахматам"), ("_МА_|ентальн", "ментальной арифметике"),
            ("Лицей|Первая школа|МсМ|Музыка", "занятиям для малышей"))
    import re as _re
    interest: dict[int, str] = {}
    for j in joins:
        nm = cls.get(j.get("classId"), "")
        for pat, label in SUBJ:
            if _re.search(pat, nm):
                interest.setdefault(j.get("userId"), label)
                break
    # свежие касания: комментарии за трое суток
    since = (_today() - timedelta(days=3)).isoformat()
    touched = set()
    try:
        cm = mk.get("/v1/company/userComments",
                    {"createdAt": [since, _today().isoformat()], "limit": 500})
        touched = {x.get("userId") for x in
                   ((cm.get("userComments") if isinstance(cm, dict) else cm) or [])}
    except Exception:
        log.warning("reactivate: комментарии недоступны, шлём без фильтра касаний")
    sent = 0
    for u in thinkers:
        if sent >= cap:
            break
        uid = u["id"]
        if uid in touched or not _mark("reactivate", f"{uid}:w{week}"):
            continue
        phone = "".join(ch for ch in (u.get("phone") or "") if ch.isdigit())[-10:]
        if len(phone) != 10:
            continue
        child = _child_name(u.get("name") or "")
        subj = interest.get(uid)
        about = f" по {subj}" if subj else ""
        who = f" {child}" if child else ""
        ok = _wa(phone,
                 f"Здравствуйте! Это KidsUP на бульваре Рокоссовского. "
                 f"Вы думали про занятия{about} для{who or ' ребёнка'} — группы "
                 f"на новый год собрались почти полностью, занятия с 31 августа. "
                 f"Чтобы место точно осталось за вами, можно прийти на первое "
                 f"занятие: оно условно-бесплатное, и на нём же бесплатная "
                 f"диагностика. Написать, какие дни и время ещё свободны?")
        if ok:
            try:
                mk.post("/v1/company/userComments",
                        {"userId": uid, "showToUser": False,
                         "comment": f"Авто-реактивация «думает»: отправлено личное "
                                    f"сообщение{about or ' (предмет не определён)'}."})
            except Exception:
                pass
            sent += 1
    log.info("reactivate: отправлено %d", sent)
    return sent


_MGR_NAMES = {202856: "Лена", 232805: "Аня", 232763: "Ира", 154181: "Лиза",
              84116: "Борис", 229704: "Маша", None: "Админ Бураковых"}


def joins_by_admin(mk: MoyklassClient) -> tuple[dict, dict]:
    """Записи в группы 2627 по авторам: за сегодня и нарастающим итогом.

    У записи в МойКлассе есть managerId; пустой автор — создание через
    API, то есть оформленные автоматикой записи Админа Бураковых (его
    людей в нашем МойКлассе нет, их договорённости оформляет разбор
    звонков). Буферы заявок не считаются — запись в «Заявки» не запись."""
    joins = mk.fetch_all("/v1/company/joins", ["joins"]) or []
    rc = mk.get("/v1/company/classes", {"limit": 500})
    cls = {c["id"]: (c.get("name") or "")
           for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
    today = _today().isoformat()
    day, total = {}, {}
    for j in joins:
        nm = cls.get(j.get("classId"), "")
        if not nm.startswith("2627") or "аявк" in nm.lower():
            continue
        if j.get("statusId") not in {2, 50509, 58131, 58132, 83760}:
            continue
        who = _MGR_NAMES.get(j.get("managerId"), f"мгр{j.get('managerId')}")
        total[who] = total.get(who, 0) + 1
        if str(j.get("createdAt") or "")[:10] == today:
            day[who] = day.get(who, 0) + 1
    return day, total


def evening_digest(mk: MoyklassClient) -> None:
    """Вечерняя сводка владельцу — решение 24.08.

    Всё, что владелец спрашивает вечером руками, приходит само: звонки
    по людям, записи, автоматика, невыполненные обещания клиентам,
    горящие группы на завтра."""
    from . import mango
    today = _today().isoformat()
    lines = [f"KidsUP · сводка за {_today().strftime('%d.%m')}"]
    try:
        rep = mango.report()
        lines.append("\nЗВОНКИ:")
        for r in rep:
            lines.append(f"• {r['admin']}: набрано {r['attempts']}, дозвон "
                         f"{r['answered']}, {r['talk_min']} мин")
    except Exception as e:
        lines.append(f"звонки: Манго недоступен ({str(e)[:40]})")
    try:
        day, total = joins_by_admin(mk)
        lines.append(f"\nЗАПИСЕЙ В ГРУППЫ: {sum(day.values())} за день, "
                     f"{sum(total.values())} всего на 2026/27")
        for who in sorted(total, key=lambda w: -total[w]):
            lines.append(f"• {who}: +{day.get(who, 0)} сегодня, {total[who]} всего")
    except Exception:
        lines.append("записи: не посчитались")
    # обещания клиентам, не выполненные к вечеру: срочные и переписка
    try:
        broken = []
        from . import taskguard as _tg
        for mid in (154181, 232805, 202856, 232763):
            for t in _tg.all_tasks(mk, mid):
                if t.get("isComplete") or t.get("isCompleted"):
                    continue
                if str(t.get("endDate") or "")[:10] <= today                         and t.get("categoryId") in (44337, 104575):
                    broken.append(t)
        if broken:
            lines.append(f"\n⚠ НЕ ЗАКРЫТО К ВЕЧЕРУ: {len(broken)} срочных/переписка")
    except Exception:
        pass
    try:
        fills = group_fill(mk)
        hot = [f for f in fills if f["got"] * 2 < f["target"]][:5]
        if hot:
            lines.append("\nГОРЯЩИЕ ГРУППЫ (меньше половины плана):")
            for f in hot:
                lines.append(f"• {_join_title(f['name'])[:52]} — {f['got']}/{f['target']}")
    except Exception:
        pass
    bal = mango.balance()
    if bal is not None:
        mark = " ⚠ ПОПОЛНИТЬ — встанут звонки и СМС" if bal < 500 else ""
        lines.append(f"\nБаланс Манго: {bal:.0f} ₽{mark}")
    text = "\n".join(lines)[:1800]
    phone = db.get_setting("digest_phone") or ""
    if phone:
        _wa(phone, text)
        log.info("вечерняя сводка отправлена владельцу")


def missed_calls() -> None:
    today = _today().isoformat()
    mk = _client()
    try:
        for m in mango.missed():
            phone = m["phone"]
            if len(phone) < 10 or not _mark("missed_wa", f"{today}:{phone}"):
                continue
            kind, child = _missed_kind(mk, phone)
            if kind == "team":
                log.info("missed_calls: %s — свой номер, автосообщение не шлём", phone[-4:])
                continue
            if kind == "client":
                text = MISSED_OUR.format(child=f" по занятиям {_genitive(child)}" if child else "")
            else:
                text = MISSED_COLD
            delivered = _wa(phone, text)
            # Правило владельца 24.08 (вечер): если с семьёй НЕТ переписки
            # в Telegram и MAX, СМС уходит ВМЕСТЕ с WhatsApp — не как
            # запасной канал, а параллельно. Плюс страховка: все каналы
            # отказали → тоже СМС. Одна на номер в день; None от _wa —
            # ночь или клиент ждёт ответа, туда СМС не суём.
            try:
                has_msgr = any(t in ("telegram", "max")
                               for t in wazzup.channels_for(phone))
            except Exception:
                has_msgr = False
            # СМС только бывшим и действующим клиентам — тем, у кого в CRM
            # есть хоть одна оплата. Рекламная СМС человеку, который у нас
            # никогда не покупал, — риск штрафа по закону о рекламе
            # (решение владельца 24.08). Мессенджеры остаются для всех.
            paid_before = False
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT 1 FROM users u JOIN payments p ON p.user_id = u.id "
                    "WHERE substr(u.phone,-10)=? AND p.summa > 0 LIMIT 1",
                    (phone[-10:],)).fetchone()
                paid_before = bool(row)
            # Выключатель: 24.08 Манго принимал СМС (result 1000), но
            # длинный кириллический текст до абонента НЕ ДОХОДИЛ — оператор
            # режет многосегментные сообщения от незарегистрированного имени
            # «MDeveloper». Платить ~9 ₽ за три сегмента, которые молча
            # умирают у оператора, и считать семью «догнанной» — хуже, чем
            # не слать. Включается настройкой sms_on=1 после регистрации
            # имени отправителя в ЛК Манго.
            sms_on = db.get_setting("sms_on", "0") == "1"
            # Решение владельца 25.08: СМС уходит ВСЕГДА, если клиент у нас
            # платил, — независимо от того, есть ли переписка в мессенджерах.
            # Прежде она была замыкающим каналом «на случай, если больше
            # некуда»; теперь это полноценная третья опора рядом с WhatsApp.
            need_sms = sms_on and paid_before and delivered is not None
            if need_sms and _mark("missed_sms", f"{today}:{phone}"):
                # Кириллический сегмент — 70 символов; прежний текст был
                # три сегмента и полз до абонента десять минут. Два сегмента
                # доставляются быстрее и стоят на треть дешевле.
                if mango.send_sms(phone,
                        "KidsUP: звонили по поводу занятий - идёт набор групп "
                        "2026/27. Ответьте в WhatsApp 79160170918 или "
                        "перезвоните 74951209024 - подберём группу."):
                    log.info("sms-догон: %s", phone[-4:])
            log.info("missed_calls: +7%s — %s", phone, kind)
    finally:
        mk.close()


DEAD_STATES = {345759, 125957, 146328, 125954, 215202, 146330, 146513}
# «Архив набора» — не приговор: туда массово уезжают и свои же клиенты. Замер 19.08:
# из 142 семей, плативших этим летом, 136 лежали в архиве. По таким задачи не трогаем.
SOFT_DEAD = {345759}


def _paid_recently(user_id: int, days: int = 150) -> bool:
    since = (_today() - timedelta(days=days)).isoformat()
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM payments WHERE user_id = ? AND optype = 'income' "
            "AND summa > 0 AND date >= ? LIMIT 1", (user_id, since)).fetchone()
    return bool(row)


def close_dead_tasks(limit: int = 400) -> int:
    """Закрывает открытые задачи по клиентам, ушедшим в архив/отказ.

    Замер 19.08: 167 из 215 висящих задач (77 %) стояли на архивных карточках —
    админ видит список из сотни дел, где живых десяток, перестаёт ему верить и
    работает по своим спискам мимо CRM. Чистим ежедневно, чтобы список задач
    оставался списком реальной работы."""
    mk = _client()
    cache: dict[int, int | None] = {}
    closed = 0
    ids = [a["managerId"] for a in _admins()]
    chat_admin = int(db.get_setting("chat_admin", "154181") or 154181)
    if chat_admin not in ids:
        ids.append(chat_admin)
    for mid in ids:
        try:
            tasks = mk.fetch_all("/v1/company/tasks", ["tasks"], {"managerId": mid})
        except Exception:
            log.exception("не удалось получить задачи менеджера %s", mid)
            continue
        for t in tasks:
            if closed >= limit:
                return closed
            if t.get("isComplete") or not t.get("userId"):
                continue
            uid = t["userId"]
            if uid not in cache:
                try:
                    cache[uid] = mk.get(f"/v1/company/users/{uid}").get("clientStateId")
                except Exception:
                    cache[uid] = None
            if cache[uid] not in DEAD_STATES:
                continue
            if cache[uid] in SOFT_DEAD and _paid_recently(uid):
                continue          # платил в этом сезоне — это клиент, а не отработанный лид
            body = {k: t.get(k) for k in ("body", "beginDate", "endDate", "isAllDay",
                                          "reminds", "ownerId", "managerIds", "userId",
                                          "classIds", "filialIds", "categoryId")}
            body["isComplete"] = True
            try:
                mk.post(f"/v1/company/tasks/{t['id']}", body)
                closed += 1
            except Exception:
                log.warning("не закрылась задача %s", t.get("id"))
    if closed:
        log.info("закрыто задач по архивным клиентам: %s", closed)
    return closed


def card_quality() -> None:
    """Проверка новых карточек: без даты рождения и телефона карточка почти
    бесполезна — по ней не подобрать группу и не перезвонить. Раз в день
    собираем такие за последние 3 дня и отдаём одной задачей тому, кто их завёл."""
    import re as _re
    mk = _client()
    try:
        since = (_today() - timedelta(days=3)).isoformat()
        users = mk.fetch_all("/v1/company/users", ["users"],
                             params={"createdAt": [since, (_today() + timedelta(days=1)).isoformat()],
                                     "limit": 500})
        bad_bd, bad_phone, bad_name = [], [], []
        MSG = _re.compile(r"\((телеграмм?|telegram|tg|max|макс|whats?app|ватсап|вайбер)\)", _re.I)
        AGE = _re.compile(r"\b\d{1,2}\s*(лет|год|года|мес)\b")
        for u in users:
            name = (u.get("name") or "").strip()
            attrs = {a.get("attributeAlias"): a.get("value") for a in (u.get("attributes") or [])}
            if not attrs.get("birthday"):
                bad_bd.append(name or str(u.get("id")))
            if not u.get("phone"):
                bad_phone.append(name or str(u.get("id")))
            if MSG.search(name) or AGE.search(name) or (name[:1].islower() if name else False) \
                    or _re.match(r"^7?9\d{9}$", name.replace(" ", "")):
                bad_name.append(name)
        if not (bad_bd or bad_phone or bad_name):
            return
        admins = _admins_today() or _admins()
        if not admins:
            return
        who = admins[_today().toordinal() % len(admins)]["managerId"]
        parts = []
        if bad_bd:
            parts.append(f"без даты рождения {len(bad_bd)}")
        if bad_phone:
            parts.append(f"без телефона {len(bad_phone)}")
        if bad_name:
            parts.append(f"кривое имя {len(bad_name)}")
        body = ("🤖 Клод: качество карточек за 3 дня — " + ", ".join(parts) +
                ". Дозаполнить: дата рождения нужна для подбора группы, телефон — чтобы перезвонить. "
                "В имени — только имя ребёнка (не мессенджер, не возраст, не телефон).")
        _task(mk, who, None, body[:250])
        log.info("card_quality: %s", ", ".join(parts))
    finally:
        mk.close()


def _subject_of(class_name: str) -> str | None:
    """Предмет по названию группы. Нужен, чтобы звонить не «всем подряд»,
    а под конкретную недобранную позицию."""
    n = class_name or ""
    if n.startswith(("ДОД", "МК")):
        return None
    if "_ПШ" in n or n.startswith("ПШ") or "одготовка" in n:
        return "ПШ"
    if "_АЯ" in n or n.startswith("АЯ") or "_ЛК" in n or "НЕЙРО Англ" in n:
        return "АЯ"
    if "ини-сад" in n or "нулевой" in n.lower():
        return "Сад"
    if ("МсМ" in n or "узыка и речь" in n or "_РР" in n or n.startswith("РР")
            or "азвити" in n or "ицей" in n):
        return "РР"
    if "_ЛГ" in n or "огопед" in n:
        return "Логопед"
    if "ШАХ" in n or "ахмат" in n:
        return "Шахматы"
    if "ИЗО" in n:
        return "ИЗО"
    return None


def _target_subject(subs: set[str], age: float | None) -> str:
    """Куда зовём ребёнка в 2026/27 с учётом того, что он вырос.

    Прошлогодний малыш с «Музыки и речи» — сегодняшний клиент подготовки
    к школе, а вчерашний дошкольник с ПШ, пошедший в первый класс, — клиент
    английского. Без этого пересчёта обзвон предлагает людям то, из чего
    они уже выросли, и получает отказ на ровном месте."""
    a = age or 0
    if "ПШ" in subs:
        return "АЯ" if a >= 7.3 else "ПШ"
    if "АЯ" in subs:
        return "АЯ"
    if ("Сад" in subs or "РР" in subs) and a >= 4.2:
        return "ПШ"
    if 4.5 <= a <= 11:
        return "АЯ"
    return next(iter(sorted(subs))) if subs else "?"


# План 21–30.08 (docs/plan_nabora_21_30.html): какая волна в какой день.
# Волна определяет, кого автопилот кладёт в утренние задачи.
WAVES = [
    (date(2026, 8, 24), ("hot", "warm")),      # 21–24: лето, потом апрель-май
    (date(2026, 8, 25), ("hot", "warm", "rr")),
    (date(2026, 8, 27), ("rr", "warm", "hot")),   # 25–27: РР-волна и дожим
    (date(2026, 8, 28), ()),                      # 28: только подтверждения
    (date(2026, 8, 30), ()),                      # 29–30: события
]


def _wave_today() -> tuple[str, ...]:
    d = _today()
    for until, waves in WAVES:
        if d <= until:
            return waves
    return ("hot", "warm", "rr", "cold")          # сентябрь — добираем всё


# Роли администраторов из плана: кому какой сегмент достаётся первым.
# Считано по 20.08: Лена 11 разговоров → 7 записей (закрыватель),
# Ира 21 → 12 (объём), Аня 28 → 4 (реактив и короткий скрипт).
ADMIN_ROLE = {202856: "closer", 232763: "volume", 232805: "reactive"}
ROLE_ORDER = {"closer": ("hot", "warm", "rr"),
              "volume": ("hot", "rr", "warm"),
              "reactive": ("warm", "rr", "hot")}
ADMIN_NORM = {202856: 40, 232763: 45, 232805: 45}


def _plan_queues() -> tuple[dict[int, list[int]], dict[int, str]]:
    """Очереди по плану 21–30.08: сегмент по теплоте и целевому предмету,
    раздача — по сильным сторонам администратора, а не поровну.

    Возвращает (admin_index -> [user_id]), (user_id -> сегмент)."""
    import json as _json
    with db.get_conn() as conn:
        cls = {r[0]: r[1] or "" for r in conn.execute("SELECT id, name FROM classes")}
        les = {r[0]: (r[1], r[2]) for r in
               conn.execute("SELECT id, date, class_id FROM lessons")}
        enrolled = {r[0] for r in conn.execute(
            "SELECT DISTINCT j.user_id FROM joins j JOIN classes cl ON cl.id = j.class_id "
            "WHERE cl.name LIKE '2627%' AND cl.name NOT LIKE '%Заявки%'")}
        seen: dict[int, dict] = {}
        for uid, lid in conn.execute(
                "SELECT user_id, lesson_id FROM lesson_records WHERE visit = 1"):
            d, cid = les.get(lid, (None, None))
            if not d or d < "2025-09-01":
                continue
            s = _subject_of(cls.get(cid, ""))
            if not s:
                continue
            k = seen.setdefault(uid, {"subs": set(), "last": ""})
            k["subs"].add(s)
            if d > k["last"]:
                k["last"] = d
        users = {r[0]: r for r in conn.execute(
            "SELECT id, phone, client_state_id, raw FROM users")}
    segs: dict[int, str] = {}
    for uid, k in seen.items():
        if uid in enrolled or uid not in users:
            continue
        u = users[uid]
        if u[2] in (146328, 125954):              # «не писать», «некачественный»
            continue
        if len("".join(c for c in str(u[1] or "") if c.isdigit())) < 10:
            continue
        age = None
        try:
            for a in (_json.loads(u[3]).get("attributes") or []):
                if a.get("attributeAlias") == "birthday" and a.get("value"):
                    age = (date(2026, 9, 1)
                           - date.fromisoformat(a["value"][:10])).days / 365.25
        except Exception:
            pass
        tgt = _target_subject(k["subs"], age)
        core = tgt in ("ПШ", "АЯ")
        if k["last"] >= "2026-06-01" and core:
            segs[uid] = "hot"
        elif k["last"] >= "2026-04-01" and core:
            segs[uid] = "warm"
        elif tgt == "РР" or not core:
            segs[uid] = "rr"
        else:
            segs[uid] = "cold"
    active = _wave_today()
    if not active:                                 # дни подтверждений и событий
        return {}, segs
    # Сначала охват, потом дозвоны (решение Бориса 21.08). Пока по контакту
    # не было ни одной попытки, он ценнее любого повторного набора: у
    # непрозвоненного шанс ровно нулевой, а недозвонившемуся я в тот же
    # вечер шлю WhatsApp — второе касание он получает и без второго звонка.
    # Повторные наборы копятся и достаются, только когда первый проход
    # по сегменту закончен.
    try:
        called = set(json.loads(db.get_setting("called_phones") or "[]"))
    except Exception:
        called = set()

    def _p10(uid):
        u = users.get(uid)
        return "".join(c for c in str(u[1] if u else "") if c.isdigit())[-10:]

    pool: dict[str, list[int]] = {s: [] for s in ("hot", "warm", "rr", "cold")}
    retry: dict[str, list[int]] = {s: [] for s in pool}
    for uid, s in segs.items():
        if s not in active:
            continue
        (retry if _p10(uid) in called else pool)[s].append(uid)
    for s in pool:
        pool[s].sort(key=lambda u: seen[u]["last"], reverse=True)
        retry[s].sort(key=lambda u: seen[u]["last"], reverse=True)
        pool[s] += retry[s]                # дозвоны — в хвост, после охвата
    log.info("охват: не звонили %s, повторные %s",
             {s: len(v) - len(retry[s]) for s, v in pool.items() if v},
             {s: len(v) for s, v in retry.items() if v})
    admins = _admins_today()
    out: dict[int, list[int]] = {}
    taken: set[int] = set()
    for idx, adm in enumerate(admins):
        role = ADMIN_ROLE.get(adm.get("managerId"), "volume")
        order = [s for s in ROLE_ORDER[role] if s in active]
        norm = ADMIN_NORM.get(adm.get("managerId"), 45)
        mine: list[int] = []
        for s in order:
            for uid in pool[s]:
                if len(mine) >= norm:
                    break
                if uid in taken:
                    continue
                taken.add(uid)
                mine.append(uid)
            if len(mine) >= norm:
                break
        out[idx] = mine
    log.info("план-очереди: %s | сегменты %s",
             {i: len(v) for i, v in out.items()},
             {s: len(v) for s, v in pool.items()})
    return out, segs


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


def _open_calls_today(mk: MoyklassClient, manager_id: int) -> int:
    """Сколько незакрытых задач 📞 «Прозвон базы» уже стоит на сотрудника сегодня."""
    day = _today().isoformat()
    n, offset = 0, 0
    try:
        while True:
            data = mk.get("/v1/company/tasks",
                          {"beginDate": day, "endDate": day, "categoryId": CAT_CALL,
                           "isComplete": "false", "limit": 100, "offset": offset})
            rows = (data.get("tasks") if isinstance(data, dict) else data) or []
            if not rows:
                break
            n += sum(1 for t in rows if manager_id in (t.get("managerIds") or []))
            offset += 100
            if offset >= 600:
                break
    except Exception:
        log.exception("_open_calls_today: не удалось посчитать задачи — считаю 0")
        return 0
    return n


def morning_tasks(mk: MoyklassClient) -> None:
    admins = _admins_today()
    if not admins:
        return
    per_admin = int(db.get_setting("daily_tasks_per_admin", "45") or 45)
    # С 21.08 очередь строится по плану набора: сегмент по теплоте и целевому
    # предмету, раздача по сильным сторонам администратора. Старый _queues()
    # делил всех поровну и по дате последнего визита — без учёта того, что
    # ребёнок вырос и ему нужен уже другой предмет.
    plan_on = db.get_setting("plan_queues", "1") == "1"
    if plan_on:
        try:
            queues, kinds = _plan_queues()
            if not queues:      # день подтверждений или событий — новых не даём
                log.info("morning_tasks: по плану сегодня без новых звонков")
                return
        except Exception:
            log.exception("план-очереди упали — работаем по старой схеме")
            queues, kinds = _queues()
    else:
        queues, kinds = _queues()
    TEXT_HOT = ("🔥 Был у нас ЭТИМ ЛЕТОМ. Начни с «как вам лето у нас» — он "
                "помнит педагога. Цель звонка: приход 29.08 (праздник), "
                "30.08 (ДОД) или на Неделю уроков 31.08–06.09. В подсказке 🎯 — "
                "чем занимался и что предлагать по возрасту.")
    TEXT_WARM = ("Занимался у нас до апреля-мая — не ушёл, а закончил сезон. "
                 "Повод: «группа нового года по его возрасту уже собирается». "
                 "Цель — приход на событие, не продажа по телефону. "
                 "До 30.08 сентябрь по ценам прошлого года.")
    TEXT_RR = ("Раннее развитие: малыш подрос — предложи группу нового года "
               "по возрасту (в подсказке 🎯). Свободных мест в РР больше всего. "
               "Зови на Неделю открытых уроков 31.08–06.09.")
    TEXT_CONTIN = ("Продолжение занятий 2026/27: в подсказке 🎯 — чем занимался "
                   "ребёнок в прошлом году. Предложи продолжить в новой группе. "
                   "Если пошёл в школу — вместо подготовки к школе предлагай "
                   "каллиграфию, скорочтение, менталку. До 31.08 сентябрь по старым ценам.")
    TEXT_CAMP = ("🔥 Летний лагерь закончился — продающий звонок про учебный год: "
                 "«вы уже видели центр изнутри — тот же английский круглый год, "
                 "плюс направления по возрасту». Зови на Неделю открытых уроков "
                 "31.08–06.09 (KidsUPweek.ru) и на День открытых дверей 30.08 (KidsUPday.ru); "
                 "до 31.08 сентябрь по старым ценам.")
    TEXT_RENEW = ("Продление: семья занимается у нас летом (МсМ/логопед/сад). "
                  "Подтверди продолжение с сентября и предложи второй предмет "
                  "по возрасту из подсказки 🎯. До 31.08 сентябрь по старым ценам.")
    TEXT_COLD = ("Обзвон набора 2026/27: открой карточку, прочитай подсказку 🎯, "
                 "позвони кнопкой и поставь статус по итогу.")
    TEXTS = {"contin": TEXT_CONTIN, "camp": TEXT_CAMP, "regular": TEXT_RENEW,
             "hot": TEXT_HOT, "warm": TEXT_WARM, "rr": TEXT_RR}
    for idx, adm in enumerate(admins):
        made = 0
        errors = 0
        if plan_on:
            per_admin = ADMIN_NORM.get(adm.get("managerId"), per_admin)
        # Порция — это ДОБОРКА до нормы, а не «+40 сверх того, что уже стоит».
        # Иначе ручное перепланирование смены и утренний автопилот складываются,
        # и у администратора оказывается вдвое больше звонков, чем влезает в день.
        already = _open_calls_today(mk, adm["managerId"])
        quota = max(0, per_admin - already)
        if quota == 0:
            log.info("morning_tasks: %s — уже %d звонков на сегодня, добор не нужен",
                     adm["name"], already)
            continue
        for uid in queues.get(idx, []):
            if made >= quota:
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


DISCIPLINE_RULES = [
    # (ключ, что проверяем, как объясняем админу)
    ("booked_no_join", "статус «Записался на пробное» без записи в группу 2026/27",
     "педагог не знает, что ребёнка ждать, и в списке группы его нет"),
    ("promo_never_called", "лидов без единого разговора (статусы «от промоутера» и «новый лид»)",
     "за промо-контакты уже заплачено по 600 ₽, а заявки просто стынут"),
    ("no_comment", "разговор состоялся, а комментария в карточке нет",
     "следующий, кто позвонит этому клиенту, будет звонить вслепую"),
]


def discipline_check() -> dict:
    """Проверка, что команда исправляет повторяющиеся ошибки, а не копит их.

    Каждый день считаем одни и те же нарушения и сравниваем со вчерашним днём.
    Если число не падает второй день подряд — это уже не случайность, и задача
    уходит не админу, а руководителю: значит замечание не работает.
    Заведено 19.08.2026 после дня, где одни и те же ошибки повторялись
    у всех троих: «бесплатное пробное» пять раз, время записи не совпало
    с договорённостью, шесть детей «записаны» мимо групп."""
    mk = _client()
    stat: dict[str, int] = {}
    try:
        classes = mk.fetch_all("/v1/company/classes", ["classes"]) or []
        ids2627 = {c["id"] for c in classes if (c.get("name") or "").startswith("2627")}

        booked = mk.fetch_all("/v1/company/users", ["users"], {"clientStateId": ST_BOOKED}) or []
        bad = []
        for u in booked:
            joins = mk.get("/v1/company/joins", {"userId": u["id"]}).get("joins") or []
            if not any(j.get("classId") in ids2627 for j in joins):
                bad.append(u)
        stat["booked_no_join"] = len(bad)

        never = 0
        for state in (ST_PROMO, 125951):
            never += len(mk.fetch_all("/v1/company/users", ["users"],
                                      {"clientStateId": state}) or [])
        stat["promo_never_called"] = never
    except Exception:
        log.exception("discipline_check: не удалось собрать статистику")
        mk.close()
        return {}

    prev = {}
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS discipline_log (
            day TEXT, rule TEXT, n INTEGER, PRIMARY KEY (day, rule))""")
        yday = (_today() - timedelta(days=1)).isoformat()
        for r, n in conn.execute("SELECT rule, n FROM discipline_log WHERE day = ?", (yday,)):
            prev[r] = n
        for rule, n in stat.items():
            conn.execute("INSERT OR REPLACE INTO discipline_log (day, rule, n) VALUES (?,?,?)",
                         (_today().isoformat(), rule, n))

    worse = [(r, n, prev.get(r)) for r, n in stat.items()
             if prev.get(r) is not None and n >= prev[r] and n > 0]
    if worse and _mark("discipline_alert", _today().isoformat()):
        lines = ["🤖 Клод: ошибки не исправляются второй день подряд.", ""]
        for rule, n, was in worse:
            title = next((t for k, t, _ in DISCIPLINE_RULES if k == rule), rule)
            why = next((w for k, _, w in DISCIPLINE_RULES if k == rule), "")
            lines.append(f"• {title}: было {was}, стало {n} — {why}.")
        lines += ["", "Замечание озвучено, поведение не изменилось. Нужен разбор на смене."]
        _wa(db.get_setting("digest_phone") or "79104526673", "\n".join(lines),
                kind="digest")
        log.warning("discipline_check: не исправлено — %s", worse)
    mk.close()
    return {"today": stat, "yesterday": prev, "worse": [w[0] for w in worse]}


def wazzup_watchdog() -> dict:
    """Сторож канала Wazzup: вебхук и живые каналы.

    Две поломки, каждая из которых происходит молча.
    1. Адрес вебхука в Wazzup один на всех. Интеграция МойКласс при
       пересохранении перезаписывает его на свой — и мы перестаём видеть
       входящие сообщения и статусы доставки рассылки. 19.08 так потеряли
       20 часов переписки, и заметили это только со слов админов.
    2. Номер-отправитель может отвалиться (state != active) — рассылка
       тогда молча уходит в пустоту.
    Проверяем каждые полчаса, вебхук чиним сами, про номера пишем Борису."""
    from . import wazzup as wz
    res = {}
    try:
        uri = wz.webhook_uri()
        res["uri"] = uri
        if uri != wz.OUR_HOOK:
            res["restored"] = wz.set_webhook()
            log.warning("вебхук Wazzup был на %s — вернули на себя (%s)", uri, res["restored"])
            if _mark("wz_hook_alert", f"{_today()}:{uri[:40]}"):
                _wa(db.get_setting("digest_phone") or "79104526673",
                    "🤖 Клод: вебхук Wazzup был переписан на "
                    f"{uri[:60]} — мы не видели переписку и статусы доставки. "
                    "Вернул на наш портал, чаты в МойКласс продолжают работать "
                    "через пересылку. Если адрес слетит снова — значит его "
                    "меняет интеграция МойКласс при пересохранении.")
    except Exception as e:
        log.warning("watchdog: вебхук не проверился: %s", e)

    try:
        senders = [s.strip() for s in (db.get_setting("wa_senders") or "").split(",") if s.strip()]
        # У номера бывает несколько каналов: старые заблокированные и один
        # живой. Живым считаем номер, если ХОТЯ БЫ ОДИН его канал активен, —
        # и смотрим оба транспорта: whatsapp (обычный, чинится QR-кодом) и
        # wapi (WABA, где QR не существует вовсе). 25.08 сторож искал WABA
        # 3507 среди обычных WhatsApp, не находил и просил сканировать QR,
        # которого у WABA нет.
        chans = wz.all_channels()
        alive, kind = set(), {}
        for c in chans:
            pid, tr = c.get("plainId"), c.get("transport")
            if tr in ("whatsapp", "wapi"):
                kind.setdefault(pid, tr)
                if c.get("state") == "active":
                    alive.add(pid)
                    kind[pid] = tr
        dead = [s for s in senders if s not in alive]
        res["dead_senders"] = dead
        if dead and _mark("wz_dead_sender", f"{_today()}:{','.join(dead)}"):
            how = []
            for d in dead:
                if kind.get(d) == "wapi":
                    how.append(f"{d} — это WABA: QR не нужен, смотреть статус "
                               f"номера в WhatsApp Manager и подписку Wazzup")
                else:
                    how.append(f"{d} — обычный WhatsApp: переподключить канал "
                               f"в Wazzup, сканировать QR заново")
            _wa(db.get_setting("digest_phone") or "79104526673",
                "🤖 Клод: не вижу активного канала для номеров — "
                + ", ".join(dead) + ". Рассылка с них не уйдёт. " + "; ".join(how))
            log.warning("watchdog: мёртвые отправители %s", dead)
    except Exception as e:
        log.warning("watchdog: каналы не проверились: %s", e)

    # тишина в вебхуке — тоже симптом: события идут постоянно в рабочие часы
    try:
        with db.get_conn() as conn:
            last = conn.execute("SELECT MAX(ts) FROM wazzup_raw").fetchone()[0]
        res["last_event"] = last
        if last and 9 <= _now().hour < 20:
            quiet = (_now() - datetime.fromisoformat(last)).total_seconds() / 3600
            res["quiet_hours"] = round(quiet, 1)
            if quiet >= 4 and _mark("wz_quiet", f"{_today()}:{int(quiet)}"):
                _wa(db.get_setting("digest_phone") or "79104526673",
                    f"🤖 Клод: от Wazzup нет событий {int(quiet)} ч подряд. "
                    "Обычно в рабочие часы они идут постоянно — похоже, канал "
                    "или вебхук отвалились. Проверяю автоматически каждые полчаса.")
    except Exception as e:
        log.warning("watchdog: тишина не проверилась: %s", e)
    return res


def audit_yesterday() -> dict:
    """Утренний аудит вчерашних денег.

    Неподписанная оплата — это не мелочь учёта: под ней не стоит ничьей
    подписи, поэтому её нельзя ни зачесть в бонус, ни отличить от денег,
    прошедших мимо кассы. Поэтому каждое утро список уходит задачей тому,
    кто сегодня на смене, а красные находки — сразу Борису."""
    from . import audit as _audit
    res = _audit.daily_audit()
    unsigned, refunds = res["unsigned"], res["refunds"]
    if not unsigned and not refunds and not res["discounts"]:
        return res

    mk = _client()
    try:
        if unsigned:
            total = sum(x["summa"] for x in unsigned)
            names = ", ".join((x["name"] or str(x["user_id"]))[:22] for x in unsigned[:6])
            body = (f"🤖 Клод: вчера {len(unsigned)} оплат на "
                    f"{total:,.0f} ₽ без менеджера. ".replace(",", " ") +
                    f"Открой каждую и поставь себя ответственным: {names}. "
                    "Без подписи оплата не попадает в твой бонус.")
            for a in (_admins_today() or [])[:2]:
                _task(mk, a["managerId"], None, body[:250])
        if refunds:
            lines = ["🤖 Клод: возвраты за вчера — проверь основания.", ""]
            lines += [f"• {r['name']}: {r['summa']:,.0f} ₽".replace(",", " ")
                      for r in refunds[:8]]
            _wa(db.get_setting("digest_phone") or "79104526673", "\n".join(lines),
                kind="digest")
    finally:
        mk.close()
    return res


# Кто есть кто. У звонящих администраторов (Ира, Аня, Лена) в задачах должны
# быть ТОЛЬКО звонки и сообщения ради записи детей в группы. Деньги, долги,
# правила, переписка и организационное — на Лизе: у неё это основная работа,
# а у них список обзвона иначе тонет в чужих делах.
CHAT_ADMIN = 154181                       # Лиза
CALL_ADMINS = {232763, 232805, 202856}    # Ира, Аня, Лена
MANAGER_NAMES = {84116: "Борис", 154181: "Лиза", 202856: "Лена",
                 229704: "Маша", 232763: "Ира", 232805: "Аня"}

RULE_TASK = {
    "freeze-offbook": "Заморозку надо ставить полями «заморозить с/по» в абонементе, "
                      "а не словами в комментарии. Иначе две недели за год не "
                      "посчитать, а со стороны это выглядит как скидка от себя.",
    "freeze-over": "У клиента заморозка сверх двух недель за год. Сверх нормы — "
                   "только по справке о стационаре. Приложи справку или сними.",
    "freeze-back": "Заморозка оформлена задним числом. Заявление пишется ДО начала.",
    "makeup-late": "Отработка выдана позже месяца после пропуска. Просроченная "
                   "сгорает — если решили дать, согласуй с Борисом и напиши почему.",
    "makeup-repeat": "Один пропуск отработан дважды. Неявка на отработку её сжигает.",
    "makeup-post": "Отработка оформлена задним числом. Только по предварительной "
                   "записи — иначе правило про сгорание не работает.",
    "disc-noreason": "Скидка выше 10% без причины в абонементе. Напиши основание "
                     "в комментарии: пересчёт, заморозка, компенсация, справка.",
    "disc-stack": "На абонементе две скидки. Скидки не суммируются — одна, наибольшая.",
    "comp-nostreak": "Компенсация 50% дана, а предыдущий абонемент закончился "
                     "с перерывом. Компенсация полагается только при покупке подряд.",
}


def rules_check() -> dict:
    """Следим, что администраторы работают по правилам посещения.

    Правило, за которым никто не следит, живёт две недели. Поэтому каждое
    утро прогоняем проверки по данным МойКласс и адресно возвращаем находку
    тому, кто её сделал: не «команде», а конкретному администратору, с
    указанием абонемента и того, что именно поправить. Красные — сразу
    Борису, потому что скидка без объяснения неотличима от увода денег."""
    from . import rules as _rules
    res = _rules.check()
    fresh = _rules.open_flags(200)
    if not fresh:
        return {"flags": 0}

    # группируем по администратору: одна задача на человека, а не пять
    by_mgr: dict[int, list[dict]] = {}
    for f in fresh:
        if f.get("manager_id"):
            by_mgr.setdefault(f["manager_id"], []).append(f)

    mk = _client()
    try:
        for mgr, items in by_mgr.items():
            rule = (items[0].get("key") or "").split(":")[0]
            hint = RULE_TASK.get(rule, "Проверь по правилам: /base/pravila_kidsup")
            what = "; ".join(f["title"] for f in items[:3])
            # Разбирает всё это Лиза: у звонящих администраторов в задачах
            # должны быть ТОЛЬКО звонки и сообщения ради записи детей
            # в группы. Деньги, правила и разбор ошибок — не их работа,
            # иначе список обзвона тонет в бухгалтерии (решение Бориса 21.08).
            who = MANAGER_NAMES.get(mgr, f"менеджер {mgr}")
            body = (f"🤖 Клод: правила посещения, оформил {who} — "
                    f"{len(items)} расхождение(й). {what}. {hint}")
            try:
                _task(mk, CHAT_ADMIN, items[0].get("user_id"), body[:250])
            except Exception:
                log.warning("правила: задача для %s не поставилась", mgr)

        high = [f for f in fresh if f["level"] == "high"]
        if high:
            lines = [f"🤖 Клод: {len(high)} нарушений правил, требующих тебя:", ""]
            lines += [f"• {f['title']} — {(f['detail'] or '')[:90]}" for f in high[:6]]
            lines.append("")
            lines.append("Разбор: https://app.kidsup.ru/pravila-kontrol")
            _wa(db.get_setting("digest_phone") or "79104526673", "\n".join(lines),
                kind="digest")
    finally:
        mk.close()
    log.info("правила: %d флагов, задачи %d администраторам",
             len(fresh), len(by_mgr))
    return {"flags": len(fresh), "managers": len(by_mgr),
            "by_rule": {k: len(v) for k, v in res.items() if v}}


def money_check() -> dict:
    """Ежедневная проверка денег в CRM: абонементы, балансы, отметки.

    Долг в МойКласс растёт молча: отметка посещения списывает занятие
    с абонемента независимо от того, оплачен он или нет. Ошибки на экране
    не появляется, поэтому находят такое обычно через месяцы. Проверяем
    каждое утро и возвращаем находку тому, кто заводил абонемент."""
    from . import rules as _rules
    res = _rules.money_check()
    flags = [f for f in _rules.open_flags(300) if f.get("kind") == "деньги"]
    if not flags:
        return {"flags": 0}
    by_mgr: dict[int, list[dict]] = {}
    for f in flags:
        if f.get("manager_id"):
            by_mgr.setdefault(f["manager_id"], []).append(f)
    mk = _client()
    try:
        for mgr, items in by_mgr.items():
            total = len(items)
            what = "; ".join(f["title"] for f in items[:2])
            who = MANAGER_NAMES.get(mgr, f"менеджер {mgr}")
            body = (f"🔥 Клод: деньги в CRM, оформил {who} — {total} "
                    f"расхождение(й). {what}. Абонемент без оплаты продолжает "
                    f"списывать занятия и растит долг молча. "
                    f"Разбор: app.kidsup.ru/dolgi")
            try:
                _task(mk, CHAT_ADMIN, items[0].get("user_id"), body[:250])
            except Exception:
                log.warning("деньги: задача для %s не поставилась", mgr)
        high = [f for f in flags if f["level"] == "high"]
        if high:
            lines = [f"🤖 Клод: {len(high)} денежных расхождений в CRM:", ""]
            lines += [f"• {f['title']} — {(f['detail'] or '')[:80]}" for f in high[:6]]
            lines += ["", "Разбор: https://app.kidsup.ru/dolgi"]
            _wa(db.get_setting("digest_phone") or "79104526673", "\n".join(lines),
                kind="digest")
    finally:
        mk.close()
    log.info("деньги: %d флагов, задачи %d администраторам", len(flags), len(by_mgr))
    return {"flags": len(flags), "managers": len(by_mgr),
            "by_rule": {k: len(v) for k, v in res.items() if v}}


def daily_digest() -> None:
    """Точка входа штатного расписания (20:00). Сама сводка собирается в
    evening_digest: прежняя считала записи по локальной копии базы и не
    видела ни горящих групп, ни невыполненных обещаний клиентам."""
    mk = _client()
    try:
        evening_digest(mk)
    finally:
        mk.close()


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
    "в честь дня рождения KidsUP, 30.08 (вс.) День открытых дверей (KidsUPday.ru), "
    "с 31.08 Неделя открытых уроков (всё бесплатно). Скоро расскажем "
    "подробнее 💛 И маленький вопрос: какие у вас планы — 🌊 ещё отдыхаем "
    "или 🏫 уже думаем про сентябрь? Можно просто смайликом 🙂")


APOLOGY_TEXT = (
    "Ой, неловко вышло 🙈 Это снова KidsUP. Утром наша автоматическая "
    "рассылка сглючила и перепутала имя — обратилась к вам по фамилии. "
    "Простите! Робота мы уже починили, а всё остальное в сообщении — "
    "чистая правда: 29.08 (сб.) праздник с аниматорами и беспроигрышной "
    "лотереей в парке «Янтарная горка», 30.08 (вс.) День открытых дверей (KidsUPday.ru), "
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
    if _mark("migration", "replied_requeue_v1"):
        # 12.08: ответившие/лайкнувшие в MAX и WhatsApp (по чатам) — фиксируем
        # как получивших; затем возвращаем в очередь тех, кому не дошло
        # (мнимые отправки в Telegram/MAX). Порядок важен: сначала отметка
        # ответивших, потом requeue — иначе им уйдёт дубль.
        replied_phones = [
            "79258918322", "79266774045", "79036649004", "79106356942",
            "79032567843", "79060688848", "79055389799", "79858190030",
            "79153901997", "79269930533", "79267369118", "79060341004",
            "79169570156", "79096901009", "79264749585", "79636553779",
            "79190146711", "79629319883", "79151003905", "79151019030",
        ]
        ts = _now().isoformat(timespec="seconds")
        with db.get_conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS wazzup_inbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
                chat_type TEXT, text TEXT, message_id TEXT UNIQUE)""")
            for p in replied_phones:
                conn.execute(
                    "INSERT OR IGNORE INTO wazzup_inbox (ts, phone, chat_type, text, message_id) "
                    "VALUES (?, ?, 'manual', 'ответы 12.08', ?)", (ts, p, f"manual-{p}"))
        db.set_setting("broadcast_transports", "whatsapp")
        res = broadcast_requeue_undelivered("2026-08-12")
        log.info("миграция replied_requeue_v1: 20 ответивших отмечено, requeue: %s", res)
    if _mark("migration", "anna_away_v1"):
        # Анна отсутствует 13–15.08 — утренние порции только Дарье
        try:
            sched = json.loads(db.get_setting("admin_schedule") or "{}")
        except ValueError:
            sched = {}
        for d in ("2026-08-13", "2026-08-14", "2026-08-15"):
            sched[d] = 232763  # Дарья Чистякова
        db.set_setting("admin_schedule", json.dumps(sched))
        log.info("миграция anna_away_v1: смены 13–15.08 закреплены за Дарьей")
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


def _team_phones() -> dict[str, str]:
    """последние 10 цифр телефона -> подпись сотрудника (для Клода-диспетчера)."""
    out: dict[str, str] = {}
    try:
        for mid, ph in json.loads(db.get_setting("admin_phones") or "{}").items():
            if ph and len(ph) >= 10:
                out[ph[-10:]] = f"сотрудник (менеджер {mid})"
    except Exception:
        pass
    dg = db.get_setting("digest_phone") or ""
    if len(dg) >= 10:
        out[dg[-10:]] = "Борис (владелец)"
    for item in (db.get_setting("team_extra_phones") or "").split(","):
        item = item.strip()
        if not item:
            continue
        ph, _, name = item.partition(":")
        if len(ph.strip()) >= 10:
            out[ph.strip()[-10:]] = name.strip() or "сотрудник"
    # педагоги и бывшие сотрудники в admin_phones не заведены, а звонить им
    # мы можем каждый день. Берём телефоны всех менеджеров прямо из CRM,
    # иначе своему же человеку однажды уйдёт продающая рассылка.
    try:
        with db.get_conn() as conn:
            for r in conn.execute("SELECT name, raw FROM managers").fetchall():
                ph = "".join(ch for ch in
                             str(json.loads(r["raw"] or "{}").get("phone") or "")
                             if ch.isdigit())
                if len(ph) >= 10:
                    out.setdefault(ph[-10:], f"сотрудник ({r['name']})")
    except Exception:
        log.warning("_team_phones: телефоны менеджеров не прочитались")
    return out


def _team_history(p10: str) -> list[dict]:
    """Последние сообщения переписки с сотрудником в формате Anthropic API."""
    msgs: list[tuple] = []
    with db.get_conn() as conn:
        for ts, text in conn.execute(
                "SELECT ts, text FROM wazzup_inbox WHERE substr(phone,-10)=? "
                "ORDER BY ts DESC LIMIT 8", (p10,)):
            msgs.append((ts, "user", text or ""))
        for ts, text in conn.execute(
                "SELECT ts, text FROM wazzup_outbox WHERE substr(phone,-10)=? "
                "AND text IS NOT NULL ORDER BY ts DESC LIMIT 8", (p10,)):
            msgs.append((ts, "assistant", text or ""))
    msgs.sort()
    out: list[dict] = []
    for _, role, text in msgs:
        if not text.strip():
            continue
        if out and out[-1]["role"] == role:  # API требует чередования ролей
            out[-1]["content"] += "\n" + text
        else:
            out.append({"role": role, "content": text})
    while out and out[-1]["role"] != "user":
        out.pop()
    return out


def team_chat_tick() -> None:
    """Клод-диспетчер: раз в минуту отвечает команде в WhatsApp/Telegram/MAX.

    Смотрит свежие входящие от номеров команды (admin_phones + digest_phone +
    team_extra_phones «телефон:имя,…»), отвечает через Anthropic API тем же
    каналом. Без ключа anthropic_api_key молчит — тогда сообщения разбирает
    большой Клод в ежечасном прогоне."""
    if not db.get_setting("anthropic_api_key"):
        return
    team = _team_phones()
    if not team:
        return
    last = int(db.get_state("team_chat_last_id") or 0)
    with db.get_conn() as conn:
        if last == 0:  # первый запуск: старую историю не трогаем
            row = conn.execute("SELECT COALESCE(MAX(id),0) FROM wazzup_inbox").fetchone()
            db.set_state("team_chat_last_id", str(row[0]))
            return
        cutoff = (_now() - timedelta(hours=2)).isoformat(timespec="seconds")
        rows = conn.execute(
            "SELECT id, phone, chat_type, text FROM wazzup_inbox "
            "WHERE id > ? AND ts >= ? ORDER BY id", (last, cutoff)).fetchall()
    if not rows:
        return
    fresh: dict[str, tuple] = {}
    for _id, phone, ctype, _text in rows:
        if phone[-10:] in team:
            fresh[phone[-10:]] = (phone, ctype)
    if fresh:
        from . import analytics, assistant, wazzup
        groups = analytics.group_stats()
        tmap = {"telegram": "tgapi", "whatsapp": "whatsapp", "max": "max", "vk": "vk"}
        for p10, (phone, ctype) in list(fresh.items())[:5]:
            hist = _team_history(p10)
            if not hist:
                continue
            hist[-1]["content"] = (f"(Пишет {team[p10]} в мессенджер. Ответь кратко "
                                   f"и по делу, как коллега.)\n" + hist[-1]["content"])
            res = assistant.ask(hist, groups)
            ans = (res.get("answer") or "").strip()
            if ans:
                wazzup.send_via(tmap.get(ctype, "whatsapp"), phone, ans, dry_run=False,
                                kind="reply")
                log.info("team_chat: ответ %s (%s)", team[p10], ctype)
            elif res.get("error"):
                log.warning("team_chat: %s — %s", team[p10], res.get("message", ""))
    db.set_state("team_chat_last_id", str(max(r[0] for r in rows)))


def poll_calls() -> dict:
    """Минутный опрос Mango вместо платных уведомлений.

    Вторая внешняя система в API-коннекторе Mango стоит 4 200 ₽/мес. Она даёт
    события в момент звонка — без неё нельзя подсказать админу ДО снятия
    трубки. Всё остальное (журнал звонков, проверка «закрыта ли задача с
    результатом», учёт обзвона) собирается опросом статистики раз в минуту.
    """
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS mango_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
            direction TEXT, state TEXT)""")
        try:
            conn.execute("ALTER TABLE mango_calls ADD COLUMN rec_id TEXT")
        except Exception:
            pass
        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_mango_calls_uniq "
                         "ON mango_calls (ts, phone, direction)")
        except Exception:
            pass
    since = _now() - timedelta(minutes=15)
    try:
        rows = mango.calls(since, _now())
    except Exception as e:
        log.warning("poll_calls: mango недоступен: %s", e)
        return {"error": str(e)[:120]}
    added = 0
    with db.get_conn() as conn:
        for r in rows:
            start = r.get("start")
            if not start:
                continue
            ts = datetime.fromtimestamp(int(start), _now().tzinfo).isoformat(timespec="seconds")
            ext = str(r.get("from_extension") or "")
            num = str(r.get("to_number") if ext else r.get("from_number") or "")
            phone = "".join(ch for ch in num if ch.isdigit())[-10:]
            if len(phone) < 10:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO mango_calls (ts, phone, direction, state, rec_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, phone, "out" if ext else "in", "finished",
                 (r.get("records") or [""])[0] if isinstance(r.get("records"), list)
                 else str(r.get("records") or "")))
            added += cur.rowcount
    if added:
        log.info("poll_calls: добавлено %d звонков", added)
    return {"added": added, "seen": len(rows)}


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
                    if now.minute % 15 < 3:      # каждые 15 минут
                        unanswered_inbound(mk)
                    if now.minute < 3 and 9 <= now.hour <= 20:  # раз в час
                        no_show(mk)
                        after_trial(mk)
                        booking_summary(mk)
                    # напоминание о завтрашнем пробном — раз в день вечером,
                    # когда родитель уже дома и может ответить на вопрос
                    if now.hour == 18 and _mark("trial_reminder_day", str(_today())):
                        trial_reminder(mk)
                    # пропущенные входящие: каждые 15 мин в рабочие часы —
                    # клиент, до которого не перезвонили, остывает быстро
                    if now.minute % 15 < 3 and 9 <= now.hour <= 20:
                        missed_inbound(mk)
                finally:
                    mk.close()
                if now.minute % 20 < 3 and 9 <= now.hour < 20:
                    mk = _client()
                    try:
                        confirm_joins(mk)
                    except Exception:
                        log.exception("подтверждение записей упало — продолжаем")
                    finally:
                        mk.close()
                # Сторож имён: раз в день проверяет, не дописали ли в имена
                # карточек служебные пометки заново. 24.08 таких было 404 —
                # без сторожа они накопятся снова за пару месяцев.
                if now.hour == 19 and now.minute < 10 \
                        and _mark("imena_watch", str(_today())):
                    mk = _client()
                    try:
                        from . import imena
                        imena.watch(mk)
                    except Exception:
                        log.exception("сторож имён упал — продолжаем")
                    finally:
                        mk.close()
                if now.hour == 12 and now.minute < 10 \
                        and _mark("reactivate_day", str(_today())):
                    mk = _client()
                    try:
                        reactivate_thinkers(mk)
                    except Exception:
                        log.exception("реактивация упала — продолжаем")
                    finally:
                        mk.close()
                if now.hour == 16 and now.minute >= 50 \
                        and _mark("evening_recall", str(_today())):
                    mk = _client()
                    try:
                        evening_recall(mk)
                    except Exception:
                        log.exception("вечерний наряд упал — продолжаем")
                    finally:
                        mk.close()
                # ответы в переписке на вопросы с однозначным ответом
                # (цена/расписание/адрес из прайса и живых групп) — решение
                # владельца 24.08 после проверки первой отправки. Каждые
                # 20 минут: реже — клиент успевает остыть, чаще — рискуем
                # столкнуться с администратором, который уже начал отвечать.
                if now.minute % 20 < 3 and 9 <= now.hour < 20 \
                        and _mark("perepiska", now.strftime("%Y-%m-%d %H:%M")[:15]):
                    try:
                        from . import perepiska
                        r = perepiska.run(dry=False)
                        if r.get("ответили"):
                            log.info("переписка: отвечено %d, человеку %d",
                                     r["ответили"], r["человеку"])
                    except Exception:
                        log.exception("автоответ в переписке упал — продолжаем")
                if now.minute < 3 and 10 <= now.hour <= 20:
                    # Манго жёстко ограничивает stats/request; один 429 в
                    # этом вызове 24.08 убивал весь тик — и вместе с ним все
                    # последующие сценарии часа. Догон не важнее цикла.
                    try:
                        missed_calls()
                    except Exception:
                        log.exception("догон недозвонов упал — продолжаем")
            # окна вместо точной минуты: тик может пропустить минуту, а при
            # рестарте днём порции всё равно должны создаться (догон).
            # отметка ставится ТОЛЬКО после успеха: упавшая порция
            # доделается на следующем тике (call_task-метки защищают от дублей)
            if 8 <= now.hour < 19 and not _has_mark("morning", str(_today())):
                mk = _client()
                try:
                    sync_admin_phones(mk)
                    morning_tasks(mk)
                    _mark("morning", str(_today()))
                finally:
                    mk.close()
            # Сторож задач: утром до смены и вечером после неё. За 21.08 я
            # дважды сам наводил в задачах беспорядок и дважды чинил руками —
            # «сейчас чисто» держится ровно до следующей правки, если никто
            # не смотрит. Теперь смотрит сторож.
            for _h, _tag in ((8, "am"), (20, "pm")):
                if now.hour == _h and now.minute < 5 \
                        and _mark("taskguard", f"{_today()}:{_tag}"):
                    try:
                        from . import taskguard
                        mk = _client()
                        try:
                            taskguard.check(mk)
                        finally:
                            mk.close()
                    except Exception:
                        log.exception("сторож задач упал — продолжаем")
            # Звонки Надежды по нашей базе: она звонит из Люберец с доб.20,
            # и её разговоры с нашими клиентами надо разбирать так же, как
            # разговоры администраторов. Раз в час — чаще незачем.
            if 9 <= now.hour < 21 and now.minute < 5 \
                    and _mark("nadezhda", now.strftime("%Y-%m-%d %H")):
                try:
                    from . import nadezhda
                    threading.Thread(target=nadezhda.run, daemon=True).start()
                except Exception:
                    log.exception("разбор звонков Надежды не запустился")
            _retry_forwards()
            # звонки — раз в минуту (бесплатная замена платных уведомлений Mango)
            if 8 <= now.hour < 21:
                try:
                    poll_calls()
                except Exception:
                    log.exception("poll_calls упал — продолжаем")
            # Клод-диспетчер: ответы команде в мессенджерах раз в минуту
            if 8 <= now.hour < 22:
                try:
                    team_chat_tick()
                except Exception:
                    log.exception("team_chat_tick упал — продолжаем")
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
            # раз в 5 минут смотрим, не одобрила ли Meta шаблон: как только
            # одобрит, рассылка возобновится сама, без участия человека
            if now.minute % 5 == 0:
                try:
                    _waba_template_watch()
                except Exception:
                    log.exception("проверка WABA-шаблона не удалась")
            # Рассылка по набору в Telegram и MAX. Стоит здесь, в общем
            # цикле, а не в трёхминутном блоке выше: темп задаёт сам модуль
            # (одно письмо и случайная пауза 1–2,5 минуты), и лишний слой
            # ожидания растянул бы её вдвое — 25.08 из-за этого уходило
            # двенадцать писем в час вместо тридцати. Ровный залп из сорока
            # сообщений — подпись бота, за которую Telegram и MAX снимают
            # аккаунт; владелец остановил такую отправку 25.08. Идёт, пока
            # WABA на модерации, потом второй заход шаблоном по молчунам.
            if 9 <= now.hour < 20:
                try:
                    from . import nabormail
                    r = nabormail.tick()
                    if r:
                        log.info("рассылка набора: %s", r)
                except Exception:
                    log.exception("рассылка набора упала — продолжаем")
            # Доставка: раз в 15 минут догоняем СМС то, что не дошло за два
            # часа, раз в час смотрим здоровье каналов. 25.08 семьдесят семь
            # сообщений сутки висели недоставленными, и заметили это люди,
            # а не система, — этот блок закрывает именно ту дыру.
            if 9 <= now.hour < 20 and now.minute % 15 < 2 \
                    and _mark("dostavka", now.strftime("%Y-%m-%d %H:%M")[:15]):
                try:
                    from . import dostavka
                    r = dostavka.chase(dry=False)
                    if r.get("смс"):
                        log.info("догон СМС: %s", r)
                    if now.minute < 15:
                        dostavka.watch()
                except Exception:
                    log.exception("контроль доставки упал — продолжаем")
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
            # дисциплина по задачам: SLA/эскалация — каждые 10 минут,
            # проверка «закрыто ли с результатом» — каждые 20 минут
            if 8 <= now.hour < 21 and now.minute % 10 < 2:
                try:
                    from . import sla as _sla
                    _sla.escalate_overdue()
                    if now.minute % 20 < 2:
                        _sla.verify_closed()
                except Exception:
                    log.exception("модуль SLA упал — продолжаем")
            if now.hour >= 9 and _mark("card_quality", str(_today())):
                try:
                    card_quality()
                except Exception:
                    log.exception("проверка качества карточек не удалась")
            if now.hour >= 19 and _mark("discipline_day", str(_today())):
                try:
                    discipline_check()
                except Exception:
                    log.exception("проверка дисциплины не удалась")
            # сторож Wazzup: вебхук один на всех, его перезаписывает МойКласс
            if now.minute % 30 < 2:
                try:
                    wazzup_watchdog()
                except Exception:
                    log.exception("сторож Wazzup упал — продолжаем")
            # аудит вчерашнего дня: неподписанные оплаты, возвраты, скидки.
            # В 9 утра, чтобы находки попадали в утренние задачи админам.
            if now.hour >= 9 and _mark("audit_day", str(_today())):
                try:
                    audit_yesterday()
                except Exception:
                    log.exception("аудит не удался — продолжаем")
            # контроль правил посещения: в 9:30, после аудита денег, чтобы
            # находка приходила администратору вместе с утренними задачами
            if (now.hour, now.minute) >= (9, 30) and _mark("rules_day", str(_today())):
                try:
                    rules_check()
                except Exception:
                    log.exception("контроль правил не удался — продолжаем")
            # деньги в CRM: неоплаченные абонементы, минусовые балансы,
            # бесплатные отметки и несписанные пропуски — в 9:40, следом
            # за контролем правил
            if (now.hour, now.minute) >= (9, 40) and _mark("money_day", str(_today())):
                try:
                    money_check()
                except Exception:
                    log.exception("проверка денег не удалась — продолжаем")
            # состав групп: возраст, уровень, перебор, неотмеченная
            # посещаемость. В 9:50, после проверки денег
            if (now.hour, now.minute) >= (9, 50) and _mark("fit_day", str(_today())):
                try:
                    from . import crmcheck as _cc
                    r = _cc.fit_check()
                    n = sum(len(v) for k, v in r.items() if not k.startswith('_'))
                    if n:
                        mk = _client()
                        try:
                            if True:
                                _task(mk, CHAT_ADMIN, None,
                                      f"🤖 Клод: состав групп — {n} расхождений "
                                      f"(возраст {len(r['age'])}, уровень "
                                      f"{len(r['level'])}, перебор {len(r['over'])}, "
                                      f"неотмеченных занятий {len(r['unmarked'])}). "
                                      f"Разбор: app.kidsup.ru/gruppy"[:250])
                        finally:
                            mk.close()
                except Exception:
                    log.exception("проверка состава групп не удалась")
            if now.hour >= 8 and _mark("close_dead_tasks", str(_today())):
                try:
                    close_dead_tasks()
                except Exception:
                    log.exception("чистка задач по архивным клиентам не удалась")
            if now.hour >= 20 and _mark("digest", str(_today())):
                daily_digest()
            if now.hour >= 21 and db.get_setting("roistat_pushed_day") != str(_today()):
                try:
                    from . import roistat as _ro
                    from datetime import timedelta as _td
                    last = db.get_setting("roistat_last_push") or ""
                    since = min(last, (_today() - _td(days=3)).isoformat()) if last \
                        else (_today() - _td(days=3)).isoformat()
                    _ro.push(since=since, dry_run=False)
                    # отметку ставим ТОЛЬКО после успеха, иначе сбой съедал бы весь день
                    db.set_setting("roistat_pushed_day", str(_today()))
                except Exception:
                    log.exception("roistat push не удался — повторим на следующем круге")
        except Exception:
            log.exception("autopilot: ошибка цикла")
        time.sleep(60)


def start() -> None:
    threading.Thread(target=_loop, daemon=True, name="autopilot").start()
    log.info("автопилот запущен (включение: настройка autopilot=on)")
