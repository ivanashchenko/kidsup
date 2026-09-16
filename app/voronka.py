"""Воронка «клиент — запись — стадия»: чем обогащаем каждую строку.

Список детей без оплаты сам по себе мало что говорит админу: нужно видеть,
что с семьёй было в последний раз — звонили ли, дозвонились ли, писали ли,
что записал в карточке админ. Всё это уже есть в локальной базе (журнал
звонков Манго, входящие и исходящие Wazzup), а комментарии из МойКласса
забираем окном за месяц одним-двумя запросами и держим в таблице
crm_comments — страница из-за них не ждёт API.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, timedelta

from . import db

log = logging.getLogger(__name__)

MANAGERS = {84116: "Борис", 154181: "Лиза", 202856: "Лена",
            229704: "Маша", 232805: "Аня", 232763: "Ира"}
STATUS = {125951: "новый лид", 345768: "недозвон (в работе)", 146950: "думает",
          125952: "записался на пробное", 125955: "клиент", 125957: "отказ",
          345759: "архив набора", 146328: "не писать", 347075: "от промоутера"}
COMMENTS_FROM = "2026-08-15"
_lock = threading.Lock()


def _digits(p) -> str:
    return "".join(ch for ch in str(p or "") if ch.isdigit())[-10:]


def _ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS crm_comments (
        id INTEGER PRIMARY KEY, user_id INTEGER, ts TEXT, manager_id INTEGER,
        text TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_crm_comments_user ON crm_comments (user_id, ts)")


def refresh_comments(mk, since: str | None = None) -> dict:
    """Забрать комментарии карточек из МойКласса окном с since по сегодня.

    limit у API — 500, дальше листаем offset. Комментарии автопилота
    («Клод: …») тоже сохраняем: в таблице они отличимы по тексту, а на
    странице показываем последний ЧЕЛОВЕЧЕСКИЙ, чтобы видеть работу админа."""
    since = since or COMMENTS_FROM
    today = date.today().isoformat()
    got = 0
    offset = 0
    with _lock:
        while True:
            try:
                cm = mk.get("/v1/company/userComments",
                            {"createdAt": [since, today], "limit": 500, "offset": offset})
            except Exception as e:
                log.warning("crm_comments: %s", e)
                break
            items = (cm.get("userComments") if isinstance(cm, dict) else cm) or []
            with db.get_conn() as conn:
                _ensure(conn)
                for x in items:
                    if not x.get("id") or not x.get("userId"):
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO crm_comments (id, user_id, ts, manager_id, text) "
                        "VALUES (?,?,?,?,?)",
                        (x["id"], x["userId"], (x.get("createdAt") or "")[:19],
                         x.get("managerId") or x.get("createdBy"),
                         (x.get("comment") or x.get("text") or "")[:600]))
            got += len(items)
            if len(items) < 500:
                break
            offset += 500
            time.sleep(0.3)
    db.set_setting("crm_comments_refreshed", time.strftime("%Y-%m-%dT%H:%M"))
    return {"ok": True, "комментариев": got, "с": since}


def refresh_comments_bg() -> None:
    from .moyklass_client import MoyklassClient
    from . import sync

    def run():
        mk = MoyklassClient(sync.get_api_key())
        try:
            refresh_comments(mk)
        except Exception:
            log.exception("crm_comments: фоновое обновление упало")
        finally:
            mk.close()
    threading.Thread(target=run, daemon=True).start()


def _fmt_ts(ts: str | None) -> str:
    if not ts:
        return ""
    ts = ts.replace("T", " ")
    return ts[5:16].replace("-", ".") if len(ts) >= 16 else ts


def _last_call(conn, p10: str) -> dict | None:
    try:
        row = conn.execute(
            "SELECT * FROM mango_calls WHERE substr(phone,-10)=? "
            "ORDER BY ts DESC LIMIT 1", (p10,)).fetchone()
    except Exception as e:
        log.warning("voronka: mango_calls недоступна: %s", e)
        return None
    if not row:
        return None
    direction = "исх" if str(row["direction"]).startswith(("out", "исх")) else "вх"
    # Манго отдаёт запись только у состоявшегося разговора; звонок без
    # записи — не взяли трубку или сброс
    state = str(row["state"] or "")
    ok = state == "talked"
    n = conn.execute(
        "SELECT COUNT(*) FROM mango_calls WHERE substr(phone,-10)=? AND ts >= ?",
        (p10, (date.today() - timedelta(days=7)).isoformat())).fetchone()[0]
    return {"когда": _fmt_ts(row["ts"]), "направление": direction,
            "итог": ("поговорили" if ok else "сброс" if state == "short"
                     else "звонок был" if state not in ("missed",)
                     else "не дозвонились" if direction == "исх" else "пропущен"),
            "за_7_дней": n, "ts": row["ts"]}


def _last_msg(conn, p10: str) -> dict | None:
    rin = conn.execute(
        "SELECT ts, text FROM wazzup_inbox WHERE substr(phone,-10)=? ORDER BY ts DESC LIMIT 1",
        (p10,)).fetchone()
    rout = conn.execute(
        "SELECT ts, text, author_name FROM wazzup_outbox WHERE substr(phone,-10)=? "
        "ORDER BY ts DESC LIMIT 1", (p10,)).fetchone()
    best = None
    if rin:
        best = {"когда": _fmt_ts(rin["ts"]), "кто": "родитель", "текст": (rin["text"] or "")[:140], "ts": rin["ts"]}
    if rout and (not best or (rout["ts"] or "") > (best["ts"] or "")):
        who = rout["author_name"] or ""
        who = "автопилот" if who in ("", "Admin", "Клод") else who
        best = {"когда": _fmt_ts(rout["ts"]), "кто": who, "текст": (rout["text"] or "")[:140], "ts": rout["ts"]}
    if best and rin:
        best["родитель_отвечал"] = True
    return best


def _last_comment(conn, uid: int) -> dict | None:
    rows = conn.execute(
        "SELECT ts, manager_id, text FROM crm_comments WHERE user_id=? ORDER BY ts DESC LIMIT 6",
        (uid,)).fetchall()
    if not rows:
        return None
    # человеческий — от админа из списка; остальное (технический аккаунт,
    # «📞 …», «Авто: …», «🤖 Клод …») — автопилот
    human = next((r for r in rows if r["manager_id"] in MANAGERS
                  and not str(r["text"] or "").startswith(("Клод", "🤖", "Авто", "📞"))), None)
    r = human or rows[0]
    return {"когда": _fmt_ts(r["ts"]), "кто": MANAGERS.get(r["manager_id"], "автопилот") if human else "автопилот",
            "текст": (r["text"] or "")[:220], "ts": r["ts"], "человек": bool(human)}


def trial_state(conn, uid: int, class_id: int | None) -> dict:
    """Что с пробным у записанного: ближайшая будущая запись на занятие
    (lessonRecords — правило владельца 03.09), последняя прошедшая и явка.

    Владелец 14.09: «зачем тут записанные на пробное? мы их ждём, напоминания
    идут сами». Верно — пока дата впереди, админу делать нечего. Работа
    появляется в двух случаях: дата прошла и явки нет (не пришёл — перезвонить
    и перезаписать) или записи на конкретное занятие нет вовсе (записать
    на дату)."""
    today = date.today().isoformat()
    q = ("SELECT l.date, l.begin_time, lr.visit, l.class_id, lr.raw FROM lesson_records lr "
         "JOIN lessons l ON l.id = lr.lesson_id WHERE lr.user_id=? ")
    args: list = [uid]
    if class_id:
        q += "AND l.class_id=? "
        args.append(class_id)
    try:
        rows = conn.execute(q + "ORDER BY l.date, l.begin_time", args).fetchall()
    except Exception:
        return {"вид": "нет_данных"}
    if not rows and class_id:
        return trial_state(conn, uid, None)
    future = [r for r in rows if r["date"] >= today]
    past = [r for r in rows if r["date"] < today]
    if future:
        f = future[0]
        d, t = f["date"], (f["begin_time"] or "")[:5]
        # Флаг «пробное» на записи ставит админ руками. Без него автонапоминание
        # накануне не уходит вовсе: 16.09 из семи записанных вперёд флага не было
        # у шести, и ни одна семья не получила бы напоминания. Показываем прямо
        # в списке — это единственное, что здесь может потребовать работы.
        try:
            test = bool(json.loads(f["raw"] or "{}").get("test"))
        except (ValueError, TypeError, IndexError, KeyError):
            test = True          # не смогли прочитать — не пугаем админа зря
        return {"вид": "ждём", "дата": d, "время": t, "флаг_пробного": test,
                "через_дней": (date.fromisoformat(d) - date.today()).days}
    if past:
        last = past[-1]
        if last["visit"]:
            return {"вид": "был", "дата": last["date"]}
        return {"вид": "не_пришёл", "дата": last["date"],
                "дней_назад": (date.today() - date.fromisoformat(last["date"])).days}
    return {"вид": "без_даты"}


DEAD_STATES = {345759, 125957, 146328, 125954, 215202, 146330, 146513}
_MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма[йя]": 5, "июн": 6, "июл": 7,
           "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}


def snooze_until(text: str, when: str) -> str | None:
    """Дата из комментария админа, до которой карточку трогать не надо:
    «перезвонить 01.10», «позвоню им 26», «хотят в октябре», «на след неделе»,
    «в конце сентября». Возвращает ISO-дату в будущем или None.

    Лена 15.09: «там у многих есть комментарии, а они всё равно в списке» —
    договорённость «позже» должна убирать строку из работы до срока."""
    import re
    if not text:
        return None
    low = text.lower()
    base = date.fromisoformat(when[:10]) if when else date.today()
    today = date.today()
    cands: list[date] = []
    for m in re.finditer(r"(?<!\d)(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?(?!\d)", low):
        d, mo = int(m.group(1)), int(m.group(2))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            try:
                cands.append(date(base.year, mo, d))
            except ValueError:
                pass
    m = re.search(r"(перезвон\w*|позвон\w*|звонить|связ\w*|набрать)\D{0,12}(\d{1,2})(?:-?го)?(?!\d|:|[.\-/]\d)", low)
    if m:
        d = int(m.group(2))
        if 1 <= d <= 31:
            mo = base.month if d >= base.day else base.month % 12 + 1
            try:
                cands.append(date(base.year + (1 if mo < base.month else 0), mo, d))
            except ValueError:
                pass
    for pat, mo in _MONTHS.items():
        if re.search(r"(в|до|с|на|после)\s+(конце\s+|начале\s+|середине\s+)?" + pat, low):
            day = 1
            if re.search(r"конце\s+" + pat, low):
                day = 25
            elif re.search(r"середине\s+" + pat, low):
                day = 15
            y = base.year + (1 if mo < base.month else 0)
            cands.append(date(y, mo, day))
    if re.search(r"(след\w*|следующ\w*)\s+недел", low):
        cands.append(base + timedelta(days=7))
    if re.search(r"через\s+(две|2)\s+недел", low):
        cands.append(base + timedelta(days=14))
    elif re.search(r"через\s+недел", low):
        cands.append(base + timedelta(days=7))
    if re.search(r"через\s+месяц", low):
        cands.append(base + timedelta(days=30))
    fut = [c for c in cands if c > today]
    return min(fut).isoformat() if fut else None


def enrich(rows: list[dict]) -> None:
    """Дописать в каждую строку статус карточки, последний звонок, последнее
    сообщение и последний комментарий админа. Меняет rows на месте."""
    with db.get_conn() as conn:
        _ensure(conn)
        for r in rows:
            p10 = _digits(r.get("phone"))
            uid = r.get("uid")
            raw = conn.execute("SELECT raw FROM users WHERE id=?", (uid,)).fetchone()
            try:
                st = json.loads((raw["raw"] if raw else None) or "{}").get("clientStateId")
            except ValueError:
                st = None
            r["статус"] = STATUS.get(st, "")
            r["статус_id"] = st
            r["мёртвый_статус"] = st in DEAD_STATES
            r["пробное"] = trial_state(conn, uid, r.get("class_id")) if uid else None
            r["звонок"] = _last_call(conn, p10) if p10 else None
            r["сообщение"] = _last_msg(conn, p10) if p10 else None
            r["комментарий"] = _last_comment(conn, uid) if uid else None
            last = max((x["ts"] for x in (r["звонок"], r["сообщение"], r["комментарий"]) if x and x.get("ts")),
                       default="")
            c = r["комментарий"] or {}
            r["отложено_до"] = snooze_until(c.get("текст", ""), c.get("ts", "")) if c.get("человек") else None
            r["последний_контакт"] = last[:10]
            r["дней_тишины"] = (date.today() - date.fromisoformat(last[:10])).days if last[:10] else None
