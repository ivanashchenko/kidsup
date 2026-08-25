"""Контроль доставки: догон СМС по недошедшему и сторож живых каналов.

Зачем. 25.08 семьдесят семь сообщений — подтверждения записи и ответы
клиентам — сутки висели недоставленными, потому что chatId уходил без
семёрки. Wazzup принимал их и отвечал «ок», статусы доставки мы писали,
но связать статус с человеком не могли, и заметили провал только когда
администраторы открыли приложение глазами.

Что делает модуль:

· ДОГОН СМС. Сообщение, которое за два часа не дошло, догоняем коротким
  СМС — но только по-настоящему важное (подтверждение записи, напоминание
  о пробном, перенос) и только тем, кто у нас платил. Решение владельца
  25.08: СМС не дубль мессенджера, а страховка на случай, когда мессенджер
  молчит. На ответы в живом диалоге и на рассылки-новости СМС не идёт.

· СТОРОЖ КАНАЛОВ. Если за последний час доля недоставленного по каналу
  перевалила за половину при хотя бы пяти отправках — канал сломался,
  и владелец узнаёт об этом сразу, а не через сутки.

Почему СМС короткая. Текст подтверждения — 350 знаков, это пять
СМС-сегментов, около 12 ₽ за штуку. В сентябре подтверждений будет
тридцать-сорок в день. Поэтому у СМС свой текст на 130 знаков, ровно
с тем, без чего человек не придёт: что записали, когда начало, адрес
и телефон.

Запуск:
    python -m app.dostavka          — что не дошло и что уйдёт СМС
    python -m app.dostavka chase    — догнать недоставленное
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from . import db

log = logging.getLogger("kidsup.dostavka")

# Сообщения, ради которых имеет смысл платить за СМС: без них человек
# просто не придёт. Всё остальное — переписка и новости — молчит.
CHASE_KINDS = {"confirm", "trial_reminder", "reschedule"}
WAIT_HOURS = 2          # столько ждём доставки, прежде чем слать СМС
SMS_FROM, SMS_TO = 9, 20


def _now() -> datetime:
    return datetime.utcnow() + timedelta(hours=3)


def _table() -> None:
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS wazzup_sent (
            message_id TEXT PRIMARY KEY, ts TEXT, phone TEXT, uid TEXT,
            transport TEXT, kind TEXT, chased INTEGER DEFAULT 0)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS wazzup_status (
            message_id TEXT PRIMARY KEY, status TEXT, rank INTEGER, ts TEXT)""")


def undelivered(hours: int = WAIT_HOURS, kinds: set | None = None) -> list[dict]:
    """Отправленное больше `hours` назад, по чему не пришло ни delivered,
    ни read. Статуса нет вовсе — тоже сюда: Wazzup молчит и когда канал
    не смог доставить."""
    _table()
    edge = (_now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    q = """SELECT s.message_id, s.ts, s.phone, s.uid, s.transport, s.kind,
                  COALESCE(st.status, '—')
             FROM wazzup_sent s
        LEFT JOIN wazzup_status st ON st.message_id = s.message_id
            WHERE s.ts <= ? AND s.chased = 0
              AND COALESCE(st.rank, 0) < 2
         ORDER BY s.ts"""
    with db.get_conn() as conn:
        rows = conn.execute(q, (edge,)).fetchall()
    out = [{"mid": r[0], "ts": r[1], "phone": r[2], "uid": r[3],
            "transport": r[4], "kind": r[5], "status": r[6]} for r in rows]
    if kinds is not None:
        out = [r for r in out if r["kind"] in kinds]
    return out


def _paid_before(uid: str) -> bool:
    """Платил ли клиент. Тем, кто не платил, СМС не шлём никогда: это
    подпадает под закон о рекламе, и штраф достанется нам."""
    if not uid:
        return False
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM payments WHERE user_id = ? AND summa > 0 LIMIT 1",
                (uid,)).fetchone()
        return bool(row)
    except Exception:
        return False


def sms_text(kind: str, note: str = "") -> str:
    """Короткий текст: два сегмента вместо пяти. Всё, без чего не придут."""
    base = {
        "confirm": "KidsUP: вы записаны{note}. Занятия с 31.08. "
                   "Рокоссовского 6к1В. Вопросы: 4951209024",
        "trial_reminder": "KidsUP: напоминаем о пробном{note}. "
                          "Рокоссовского 6к1В. Вопросы: 4951209024",
        "reschedule": "KidsUP: изменение по занятию{note}. "
                      "Позвоните нам: 4951209024",
    }.get(kind, "KidsUP: у нас для вас сообщение. Позвоните: 4951209024")
    return base.replace("{note}", f" — {note}" if note else "")


def chase(dry: bool = True, limit: int = 25) -> dict:
    """Догнать СМС то, что не дошло. Возвращает, что сделано."""
    from . import mango
    hour = _now().hour
    if not (SMS_FROM <= hour < SMS_TO):
        return {"пропуск": f"сейчас {hour}:00, вне окна 9-20"}
    if db.get_setting("sms_on", "0") != "1":
        return {"пропуск": "СМС выключены настройкой sms_on"}
    rows = undelivered(kinds=CHASE_KINDS)[:limit]
    stat = {"недоставлено": len(rows), "смс": 0, "без оплат": 0, "ошибок": 0}
    for r in rows:
        if not _paid_before(r["uid"]):
            stat["без оплат"] += 1
            _mark_chased(r["mid"])          # второй раз не смотрим
            continue
        if dry:
            stat["смс"] += 1
            continue
        try:
            if mango.send_sms(r["phone"], sms_text(r["kind"])):
                stat["смс"] += 1
                _mark_chased(r["mid"])
            else:
                stat["ошибок"] += 1
        except Exception as e:
            stat["ошибок"] += 1
            log.warning("СМС %s: %s", r["phone"][-4:], str(e)[:80])
    return stat


def _mark_chased(mid: str) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE wazzup_sent SET chased = 1 WHERE message_id = ?", (mid,))


def channel_health(hours: int = 1, ripe_min: int = 30) -> list[dict]:
    """Доля доставленного по каналам за последние часы.

    Учитываем только сообщения старше `ripe_min` минут: статус доставки
    приходит от мессенджера не мгновенно, и свежая отправка почти всегда
    выглядит недоставленной. 25.08 без этой поправки сторож собрался
    поднять тревогу на здоровом канале через минуту после отправки."""
    _table()
    edge = (_now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    ripe = (_now() - timedelta(minutes=ripe_min)).isoformat(timespec="seconds")
    q = """SELECT s.transport, COUNT(*),
                  SUM(CASE WHEN COALESCE(st.rank, 0) >= 2 THEN 1 ELSE 0 END)
             FROM wazzup_sent s
        LEFT JOIN wazzup_status st ON st.message_id = s.message_id
            WHERE s.ts >= ? AND s.ts <= ?
         GROUP BY s.transport"""
    with db.get_conn() as conn:
        rows = conn.execute(q, (edge, ripe)).fetchall()
    return [{"transport": r[0], "всего": r[1], "дошло": r[2] or 0,
             "доля": round(100 * (r[2] or 0) / r[1]) if r[1] else 0}
            for r in rows]


def watch() -> dict:
    """Канал сломался — сказать владельцу сразу, а не через сутки.

    Порог намеренно грубый: половина недоставленного при пяти и более
    отправках. Мессенджеры отвечают статусом не мгновенно, и на мелких
    числах любая тонкая настройка даст ложную тревогу каждый час."""
    bad = [c for c in channel_health(1) if c["всего"] >= 5 and c["доля"] < 50]
    if not bad:
        return {"ok": True}
    from . import autopilot
    lines = [f"{c['transport']}: дошло {c['дошло']} из {c['всего']} "
             f"({c['доля']}%)" for c in bad]
    text = ("Канал перестал доставлять сообщения за последний час:\n"
            + "\n".join(lines)
            + "\n\nПроверьте номер в кабинете Wazzup. Отправка продолжается, "
              "но клиенты сообщений не получают.")
    for c in bad:
        if not autopilot._mark("channel_alarm",
                               f"{c['transport']}:{_now():%Y-%m-%d %H}"):
            continue
        autopilot._wa(db.get_setting("digest_phone") or "79104936673", text)
    return {"тревога": lines}


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "chase" in sys.argv:
        print(chase(dry="--real" not in sys.argv))
        return
    print("здоровье каналов за час:", channel_health(1))
    rows = undelivered()
    print(f"не дошло за {WAIT_HOURS} ч: {len(rows)}")
    for r in rows[:12]:
        print(f"   {r['ts'][11:16]} {r['transport']:9} +7{r['phone'][-10:]} "
              f"{r['kind'] or '—':16} статус {r['status']}")


if __name__ == "__main__":
    main()
