"""Стоп-лист отказов: кто письменно попросил снять бронь — тому не пишем.

Зачем. 26.08 подтверждение записи ушло семье Муралевых через два дня
после того, как они написали «не сможем водить, просьба снять бронь с
места». Механизм провала был не один, а три сразу:

1. Отказ пришёл в чат Telegram/MAX, где вместо телефона стоит внутренний
   chatId (5139026303). Для всей нашей автоматики это «другой человек»:
   рассылки ходят по телефонам из CRM и такой чат не видят вовсе.
2. Бронь в CRM после отказа сняли у одного ребёнка, а через два дня
   администратор записала в ту же группу второго ребёнка той же семьи —
   на тот же телефон. Формально это новая запись, и подтверждение ушло
   законно; для родителя это было «мы же просили снять».
3. Отказ вообще нигде не хранился как факт. Он жил только текстом в
   переписке, и каждый следующий сценарий начинал с чистого листа.

Что делает модуль. Читает входящие, распознаёт отказ, кладёт его в
таблицу otkazy и связывает с семьёй — по телефону, а для безымянных
чатов по имени ребёнка, названному в самом сообщении. Дальше guard()
в app.wazzup спрашивает is_refused() перед каждой отправкой.

Отказ снимается только человеком: если клиент передумал и написал сам,
администратор убирает его из стоп-листа кнопкой на /otkazy. Автоматика
себе такого права не даёт — цена ошибки здесь несимметрична.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from . import db

log = logging.getLogger("kidsup.otkaz")

MSK = timezone(timedelta(hours=3))

# Формулировки отказа. Держим их узкими: «не будем ходить в 19» — это про
# неудобное время, а не про отказ от центра, и такие фразы сюда попадать
# не должны. Проверено на живой переписке 25.08 (Кулаков): там разговор
# после этой фразы закончился записью на другое время.
REFUSAL = re.compile(
    r"снимите\s+брон|снять\s+брон|снимите\s+(нас|меня|реб[её]нка)\s+с\s+"
    r"|снимите\s+с\s+места|брон[ья]?\s+.{0,20}нужно\s+снять"
    r"|не\s+сможем\s+(водить|ходить|посещать)"
    r"|ходить\s+не\s+будем|не\s+будем\s+ходить\s*[!.,]?\s*$"
    r"|отказыва[ею]мся|мы\s+отказ|отмените\s+запис|отменить\s+запис"
    r"|не\s+будем\s+заниматься|больше\s+не\s+пишите|удалите\s+наш",
    re.I)

# Фразы, при которых отказ НЕ засчитывается, даже если шаблон совпал:
# клиент отказывается от конкретного варианта и тут же просит другой.
KEEP = re.compile(
    r"\bа\s+(в|на|к)\b|давайте|можно\s+ли|подберите|другое\s+врем|другой\s+день"
    r"|другому\s+логопед|перенес|поменя|запишите\s+на|а\s+утром|утро\s+есть"
    # «в 19 мы ходить не будем», «по понедельникам не будем» — это отказ
    # от предложенного варианта, а не от центра: клиент называет время и
    # ждёт, что предложат другое. Такие фразы 25.08 заканчивались записью.
    r"|\bв\s*\d{1,2}([:.]\d{2})?\b|по\s+(пн|вт|ср|чт|пт|сб|вс|понедельник"
    r"|вторник|сред|четверг|пятниц|суббот|воскресень)"
    r"|что\s+нам\s+делать|как\s+быть",
    re.I)

# Переезд. Правило владельца 26.08: кто написал или сказал, что переехал,
# — статус карточки меняется сразу, без ожидания «снимите бронь»: переезд
# сам по себе означает, что семья не придёт, и звонить ей больше не надо.
MOVED = re.compile(
    r"переехал|переезжа|переедем|уеха(ли|въ)|уезжаем\s+(насовсем|навсегда"
    r"|в\s+друг)|съехал|в\s+друг(ой|ом)\s+город|сменили\s+(город|район"
    r"|адрес)|живём\s+теперь\s+в|живем\s+теперь\s+в", re.I)

# «Переезжаем на дачу», «уехали в отпуск до сентября» — это не переезд,
# а сезонная жизнь семьи. Ошибка здесь дороже пропуска: ложный «переехал»
# выбрасывает живого клиента из воронки, и никто ему больше не позвонит.
NOT_MOVED = re.compile(
    r"дач[уае]|отпуск|каникул|отдых|на\s+выходн|к\s+бабушк|на\s+месяц"
    r"|на\s+недел|до\s+(сентября|осени|конца)|вернемся|вернёмся|вернутся"
    r"|приедем|временно", re.I)


# Имя ребёнка внутри просьбы: «не сможем водить Муралева Андрея на…».
CHILD = re.compile(
    r"(?:водить|ходить|записать|снять\s+бронь\s+(?:с|у))\s+"
    r"([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)")


def _tables(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS otkazy (
        chat TEXT PRIMARY KEY,     -- телефон (10 цифр) или chatId мессенджера
        phone TEXT DEFAULT '',     -- телефон семьи, если удалось связать
        name TEXT DEFAULT '',      -- кто именно отказался, если назван
        ts TEXT,                   -- когда написали
        text TEXT,                 -- цитата: администратор должен видеть повод
        source TEXT DEFAULT '',    -- как связали с семьёй
        released INTEGER DEFAULT 0 -- 1 = снят человеком, можно писать снова
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS otkaz_phone ON otkazy (phone)")


def _digits(x) -> str:
    return "".join(c for c in str(x or "") if c.isdigit())


def _is_phone(raw) -> str:
    """Российский мобильный в виде 10 цифр, иначе пустая строка.

    Одной длины мало: chatId Муралевых в MAX — 5139026303, ровно десять
    цифр, и проверка «10 цифр = телефон» приняла его за номер. Тогда
    связывание по имени ребёнка не запускается, а именно оно и нужно,
    чтобы отказ в мессенджере остановил рассылку по WhatsApp.
    """
    d = _digits(raw)
    if len(d) == 11 and d[0] in "78" and d[1] == "9":
        return d[-10:]
    if len(d) == 10 and d[0] == "9":
        return d
    return ""


def is_refusal(text: str) -> bool:
    """Отказ ли это. Пустой и короткий текст отказом не считаем."""
    t = (text or "").strip()
    if len(t) < 8:
        return False
    if not REFUSAL.search(t):
        return False
    # «в 19 мы ходить не будем. а утром?» — это выбор времени, не отказ
    return not KEEP.search(t)


def _family_phone(chat: str, text: str) -> tuple[str, str, str]:
    """(телефон, имя, чем связали). Для обычного чата телефон — это сам чат.

    Безымянный чат мессенджера — отдельный случай: там вместо телефона
    внутренний id, и связать с семьёй можно только по имени ребёнка,
    названному в самом сообщении. Это и спасло бы Муралевых.
    """
    p = _is_phone(chat)
    if p:
        return p, "", "телефон чата"
    m = CHILD.search(text or "")
    if not m:
        return "", "", ""
    who = m.group(1).strip()
    # В просьбе имя стоит в винительном падеже — «водить Муралева Андрея».
    # Поиск в CRM идёт по имени карточки, где записано «Муралев Андрей»,
    # поэтому пробуем ещё и основу фамилии без падежного окончания.
    stem = who.split()[0]
    tries = [who, stem]
    if len(stem) > 4 and stem[-1] in "ауеойыи":
        tries.append(stem[:-1])
    try:
        from . import sync
        from .moyklass_client import MoyklassClient
        mk = MoyklassClient(sync.get_api_key())
        mk.authenticate()
        try:
            for q in tries:
                r = mk.get("/v1/company/users", params={"name": q, "limit": 5})
                users = r.get("users") if isinstance(r, dict) else r
                for u in (users or []):
                    ph = _digits(u.get("phone"))[-10:]
                    if ph:
                        return (ph, u.get("name") or who,
                                f"по имени «{who}» в тексте")
        finally:
            mk.close()
    except Exception as e:
        log.warning("не связал чат %s по имени: %s", chat, str(e)[:80])
    return "", who, ""


def note(chat: str, text: str, ts: str = "") -> bool:
    """Записать отказ. True — если это новый отказ."""
    t = (text or "")
    moved = (bool(MOVED.search(t)) and not NOT_MOVED.search(t)
             and len(t.strip()) >= 8)
    if not is_refusal(text) and not moved:
        return False
    phone, name, how = _family_phone(chat, text)
    if moved and phone:
        try:
            mark_moved(phone, quote=(text or "")[:200])
        except Exception as e:
            log.warning("статус «переехал» не поставился %s: %s",
                        phone, str(e)[:80])
    key = _is_phone(chat) or str(chat)
    with db.get_conn() as conn:
        _tables(conn)
        cur = conn.execute("SELECT released FROM otkazy WHERE chat=?", (key,)).fetchone()
        if cur is not None:
            return False
        conn.execute(
            "INSERT OR REPLACE INTO otkazy (chat, phone, name, ts, text, source) "
            "VALUES (?,?,?,?,?,?)",
            (key, phone, name, ts or datetime.now(MSK).isoformat(timespec="seconds"),
             (text or "")[:400], how))
    log.info("отказ записан: %s (%s) %s", key, phone or "телефон не связан", how)
    return True


def is_refused(phone: str) -> str | None:
    """Причина, по которой этому человеку писать нельзя, либо None.

    Возвращаем текст, а не флаг: он попадает в лог предохранителя, и
    администратор при разборе видит, на каком основании письмо не ушло.
    """
    p = _digits(phone)[-10:]
    if not p:
        return None
    try:
        with db.get_conn() as conn:
            _tables(conn)
            row = conn.execute(
                "SELECT ts, text FROM otkazy WHERE phone=? AND released=0 "
                "ORDER BY ts DESC LIMIT 1", (p,)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return (f"клиент отказался {str(row[0])[5:16]}: «{str(row[1])[:60]}»")


def mark_moved(phone: str, quote: str = "") -> int:
    """Семья переехала: сменить статус всем её карточкам.

    Если владелец завёл в CRM статус «Переехал» — используем его; пока
    его нет, ставим «Отказ» (125957) с причиной 313606 «Ушёл: переехали /
    обстоятельства» — то же самое по смыслу, и воронка набора чиста.
    Возвращает число обновлённых карточек."""
    from . import sync
    from .moyklass_client import MoyklassClient
    p = _digits(phone)[-10:]
    if not p:
        return 0
    mk = MoyklassClient(sync.get_api_key())
    mk.authenticate()
    n = 0
    try:
        st = mk.get("/v1/company/clientStatuses")
        st = st if isinstance(st, list) else st.get("statuses") or []
        target = next((x["id"] for x in st
                       if "переех" in (x.get("name") or "").lower()), 125957)
        # Причины клиентских статусов — ОТДЕЛЬНЫЙ справочник (313609-313613),
        # не тот, что у записей в группы (313602-313607): проверено пробами
        # 26.08, из общего ряда сервер принимает только 313613.
        reason = 313613 if target == 125957 else None
        r = mk.get("/v1/company/users", params={"phone": "7" + p})
        users = r.get("users") if isinstance(r, dict) else r
        for u in (users or []):
            body = {"statusId": target}
            if reason:
                body["statusChangeReasonId"] = reason
            try:
                mk.post(f"/v1/company/users/{u['id']}/status", body)
                mk.post("/v1/company/userComments", {
                    "userId": u["id"], "showToUser": False,
                    "comment": (f"Переехали — статус изменён автоматически. "
                                f"Из сообщения: «{quote[:150]}»")[:400]})
                n += 1
            except Exception as e:
                log.warning("статус переехал uid=%s: %s", u.get("id"), str(e)[:80])
    finally:
        mk.close()
    log.info("переезд: %s — обновлено карточек %d", p, n)
    return n


def release(chat: str) -> bool:
    """Снять стоп-лист — только по решению человека."""
    with db.get_conn() as conn:
        _tables(conn)
        conn.execute("UPDATE otkazy SET released=1 WHERE chat=?",
                     (str(chat),))
    return True


def scan(hours: int = 720, rebuild: bool = False) -> dict:
    """Пройти по входящим за период и собрать отказы.

    Читаем прямо из wazzup_inbox: там лежит всё, что клиенты писали в
    любом канале, включая чаты без телефона.
    """
    since = (datetime.now(MSK) - timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = []
    with db.get_conn() as conn:
        _tables(conn)
        if rebuild:
            # снятые человеком не трогаем: это решение администратора,
            # и перезапуск разбора не должен его отменять
            conn.execute("DELETE FROM otkazy WHERE released=0")
        try:
            rows = conn.execute(
                "SELECT ts, phone, text FROM wazzup_inbox WHERE ts >= ? "
                "ORDER BY ts", (since,)).fetchall()
        except Exception:
            rows = []
    stat = {"просмотрено": len(rows), "новых отказов": 0, "без связи с семьёй": 0}
    for ts, chat, text in rows:
        if note(chat, text, ts):
            stat["новых отказов"] += 1
            if not _family_phone(chat, text)[0]:
                stat["без связи с семьёй"] += 1
    return stat


def feed() -> list[dict]:
    """Список отказов для страницы и для задач администраторам."""
    with db.get_conn() as conn:
        _tables(conn)
        rows = conn.execute(
            "SELECT chat, phone, name, ts, text, source, released "
            "FROM otkazy ORDER BY ts DESC").fetchall()
    return [{"чат": r[0], "телефон": r[1], "кто": r[2], "когда": r[3],
             "цитата": r[4], "как связали": r[5], "снят": bool(r[6])}
            for r in rows]


def main():
    import json
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "scan" in sys.argv:
        print(json.dumps(scan(), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(feed(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
