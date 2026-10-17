"""Звонить ли семье — по карточке и журналу звонков, а не по нашим меткам.

17.10.2026, Ира: «большинство детей, которые есть в задачах, уже с
комментарием и чётким ответом… по таким задачам мы выглядим надоедами и
навязчивыми». В обзвоне дня из 37 семей у 15 ответ уже был: в карточке
«свяжусь сама», «далеко», «завершила общение», «после каникул», «сейчас
рано» — или 3–4 звонка без ответа и наше сообщение. Очереди звонков (возврат
«думает / недозвон / база», повторы недозвона в автопилоте) смотрели только
на статус и на свои метки и ставили звонок снова.

Правило одно для всех очередей:
  * решает последний СОДЕРЖАТЕЛЬНЫЙ комментарий админа, разбор звонка или
    итог обзвона; отметки «но», «нет ответа», «не дозвонилась» и служебные
    комментарии автоматики ответом не считаются;
  * в нём ответ «не звонить» (свяжусь сама, далеко, выбрали другой центр,
    не актуально, завершила общение) — не звоним, пока семья не объявится:
    разговор после этого комментария или их входящий звонок снимают запрет;
  * «после каникул», «сейчас рано», «на будущее» — пауза на SROK_PAUZY дней
    или до даты «перезвонить ДД.ММ», если она названа;
  * с последнего живого контакта — 3 исходящих без ответа и наше сообщение —
    не звоним (правило владельца 28.09: три попытки, потом сообщение).

Время: crm_comments.ts — UTC без пояса (так отдаёт МойКласс), журнал звонков
и переписки — МСК с «+03:00». Перед сравнением комментарий переводим в МСК.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta

NABOR_S = "2026-08-01"
SROK_PAUZY = 45          # дней паузы для «после каникул», «рано», «на будущее»

_BUD = (r"свяжусь|свяжемся|свяжутся|свяжется|напишу|напишем|напишут|напишет|позвоню|позвоним|"
        r"позвонят|позвонит|обращусь|обратимся|обратятся|обратится|вернусь|вернёмся|вернемся|"
        r"вернутся|вернётся|вернется|объявлюсь|объявимся|объявятся|объявится")

# Ответ, после которого звонок — навязчивость, пока семья не объявится сама.
# Только будущее время: «мама сама позвонила» — это не отказ. «Мы сами
# позвоним» — наше обещание, не их.
STOP = re.compile(
    rf"(?<!мы\s)\b(?:{_BUD})\s+(?:к\s+вам\s+|вам\s+)?сам[аи]?\b|"
    rf"(?<!мы\s)\bсам[аи]?\s+(?:{_BUD})\b|"
    r"\bне\s+(?:звонить|беспокоить)\b(?!\s+(?:до|после|утром|днём|днем|вечером|раньше|позже|в|с)\b)|"
    r"(?<!не\s)\bдалеко\b|"
    r"\bвыбрали\s+друг(?:ой|ую|ие|ое)\s+(?:центр|кружок|секци\w*|студи\w*|школ\w*|сад\w*)|"
    r"\bопределились\s+с\s+(?:друг|детск)\w*\s+(?:центр|сад|школ)\w*|"
    r"\bне\s+актуальн\w*|\bне\s+интересн\w*|\bне\s+интересует\b|"
    r"\bзаверш\w*\s+общени\w*",
    re.I)

# Отсрочка: звонить можно, но не сейчас.
PAUZA = re.compile(
    r"\bпосле\s+(?:\w+\s+){0,2}каникул\b|\bна\s+будущее\b|"
    r"\b(?:сейчас|пока|ещ[её])\s+рано\b(?!\s+утр)|\bрано\s+ещ[её]\b|"
    r"\bвернутся\s+(?:к\s+(?:вопросу|занятиям)\s+)?(?:после|позже|осенью|зимой|весной|летом)\b",
    re.I)
DATA_ZVONKA = re.compile(r"\b(?:перезвон\w*|позвон\w*|набрать|звонить)\s+(?:после\s+|с\s+)?(\d{1,2})\.(\d{2})\b", re.I)

# Отметка о недозвоне — не ответ семьи; решает комментарий до неё.
PUSTO = re.compile(
    r"^\W*(?:н\.?\s?о\.?(?=\W|$)|но\b|нет\s+ответа|не\s+дозвон\w*|недозвон\w*|не\s+бер[её]т\w*|"
    r"не\s+взял\w*|не\s+отвеча\w*|сброс\w*|занято|абонент\w*|автоответ\w*|недоступ\w*|"
    r"не\s+доступ\w*|вне\s+зоны)", re.I)

# Итог обзвона и кнопки «Прозвон» — пишутся от админа, но с префиксом.
PREFIKS = re.compile(r"^(?:📞\s*)?(?:\d{1,2}\.\d{2}(?:\s+\d{1,2}:\d{2})?,?\s*(?:обзвон|прозвон)[^:]{0,40}:\s*|"
                     r"прозвон\s*\([^)]*\):\s*)", re.I)
OBZVON = re.compile(r"^\d{1,2}\.\d{2}(?:\s+\d{1,2}:\d{2})?,?\s*обзвон\b", re.I)

# Служебные комментарии автоматики: они пишутся от того же аккаунта, что и
# админы, и не несут ответа семьи («17.10 11:45 Клод: напомнили о себе…»,
# «Ответ в переписке…», подсказка к звонку, отзыв педагога).
ROBOT = re.compile(r"^(?:🤖|🎯|Клод\b|Авто|\d{1,2}\.\d{2}(?:\s+\d{1,2}:\d{2})?\s+Клод\b|"
                   r"Ответ в переписке|Педагог\s)", re.I)


def _p10(x) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())[-10:]


def _msk(ts_utc: str) -> str:
    """'2026-10-16T07:54:00' (UTC из МойКласса) → '2026-10-16T10:54' (МСК)."""
    try:
        return (datetime.fromisoformat(str(ts_utc)[:19]) + timedelta(hours=3)).isoformat()[:16]
    except ValueError:
        return str(ts_utc)[:16]


def _data_pauzy(t: str, kogda_msk: str) -> date:
    """До какого дня пауза: названная дата «перезвонить ДД.ММ» или SROK_PAUZY дней."""
    try:
        d0 = date.fromisoformat(kogda_msk[:10])
    except ValueError:
        d0 = date.today()
    m = DATA_ZVONKA.search(t)
    if m:
        try:
            d = date(d0.year, int(m.group(2)), int(m.group(1)))
            if d < d0:
                d = date(d0.year + 1, d.month, d.day)
            return d
        except ValueError:
            pass
    return d0 + timedelta(days=SROK_PAUZY)


def _zhivoy_posle(zvonki: list[tuple]) -> str:
    """Последний живой контакт по журналу (МСК, до минуты): наш или их разговор
    от 20 с или любой их входящий звонок — семья объявилась сама."""
    best = ""
    for ts, napr, st in zvonki:
        vh = not str(napr or "").startswith(("out", "исх"))
        if st == "talked" or (vh and st in ("short", "missed", "talked")):
            best = max(best, str(ts)[:16])
    return best


def otvet(kom: dict, uids, zvonki: list[tuple] | None = None, segodnya: date | None = None) -> str:
    """Причина не звонить по последнему содержательному комментарию — или ''.
    kom — kommentarii(); zvonki — итоговые строки журнала по номеру (ts, napr, state)."""
    from .voronka import MANAGERS, _CALL_LOG, _robot
    segodnya = segodnya or date.today()
    rows = [r for u in (uids or []) for r in kom.get(u, [])]
    zhivoy = _zhivoy_posle(zvonki or [])
    for ts, mid, text in sorted(rows, key=lambda r: str(r[0] or ""), reverse=True):
        t = str(text or "").strip()
        t0 = t.lstrip("📞").strip()
        razbor = t.startswith("📞") or bool(_CALL_LOG.match(t0)) or bool(OBZVON.match(t0))
        if not razbor and (ROBOT.match(t0) or _robot(t) or mid not in MANAGERS):
            continue                      # служебное — не ответ семьи
        t0 = t0.split("Расшифровка:")[0]  # в разборе дальше сырой текст разговора, там и наши фразы
        t0 = PREFIKS.sub("", t0).strip()
        kogda = _msk(ts)
        if STOP.search(t0):
            if zhivoy and zhivoy > kogda:
                return ""                 # после этого семья объявилась — запрет снят
            kto = "разбор звонка" if razbor else MANAGERS.get(mid, "админ")
            return f"{kogda[:10]} {kto}: «{t0[:90]}»"
        if PAUZA.search(t0):
            if (zhivoy and zhivoy > kogda) or segodnya >= _data_pauzy(t0, kogda):
                return ""
            kto = "разбор звонка" if razbor else MANAGERS.get(mid, "админ")
            return f"{kogda[:10]} {kto}: «{t0[:90]}» — пауза до {_data_pauzy(t0, kogda):%d.%m}"
        if PUSTO.match(t0):
            continue                      # «но», «не дозвонилась» — смотрим комментарий раньше
        return ""
    return ""


def popytki(zvonki: list[tuple], vh_soobshchenie: str = "", nashe_soobshchenie: str = "") -> tuple[int, bool]:
    """Исходящие без разговора после последнего живого контакта (разговор,
    их звонок или их сообщение) и было ли после первой из них наше сообщение.
    Одна минута — один звонок: повтор строки журнала не в счёт."""
    zhivoy = max(_zhivoy_posle(zvonki), str(vh_soobshchenie or "")[:16])
    minuty = {str(ts)[:16] for ts, napr, st in zvonki
              if str(ts)[:16] > zhivoy and str(napr or "").startswith(("out", "исх"))
              and st in ("missed", "short")}
    if not minuty:
        return 0, False
    return len(minuty), str(nashe_soobshchenie or "")[:16] >= min(minuty)


def kommentarii(conn, uids=None, s: str = NABOR_S) -> dict[int, list[tuple]]:
    """Комментарии карточек с начала набора: uid → [(ts, manager_id, text)].
    uids=None — все (для очередей на сотни семей), иначе только эти карточки."""
    out: dict[int, list[tuple]] = defaultdict(list)
    try:
        if uids is None:
            rows = conn.execute("SELECT user_id, ts, manager_id, text FROM crm_comments WHERE ts >= ?",
                                (s,)).fetchall()
        else:
            uids = [int(u) for u in uids if u]
            if not uids:
                return out
            q = ",".join("?" * len(uids))
            rows = conn.execute(f"SELECT user_id, ts, manager_id, text FROM crm_comments "
                                f"WHERE user_id IN ({q}) AND ts >= ?", (*uids, s)).fetchall()
    except Exception:
        return out
    for uid, ts, mid, text in rows:
        out[uid].append((ts or "", mid, text or ""))
    return out


def zvonki_nomera(conn, p10: str, s: str = NABOR_S) -> list[tuple]:
    """Итоговые строки журнала по номеру: (ts, направление, talked/short/missed).
    События вебхука (appeared, connected…) не берём: «connected» ставит и
    голосовое меню, это ещё не разговор."""
    p = _p10(p10)
    if len(p) < 10:
        return []
    try:
        return [tuple(r) for r in conn.execute(
            "SELECT ts, direction, state FROM mango_calls WHERE substr(phone,-10)=? AND ts >= ? "
            "AND state IN ('talked','short','missed')", (p, s)).fetchall()]
    except Exception:
        return []


def _poslednee(conn, table: str, p: str) -> str:
    try:
        r = conn.execute(f"SELECT MAX(ts) FROM {table} WHERE substr(phone,-10)=?", (p,)).fetchone()
        return str((r and r[0]) or "")
    except Exception:
        return ""


def semya(conn, uids, p10: str) -> list[int]:
    """Все карточки семьи: переданные плюс заведённые на тот же номер (братья, сёстры)."""
    out = [u for u in (uids or []) if u]
    p = _p10(p10)
    if len(p) == 10:
        try:
            out += [r[0] for r in conn.execute("SELECT id FROM users WHERE substr(phone,-10)=?", (p,))]
        except Exception:
            pass
    return sorted(set(out))


def ne_zvonit(conn, uids, p10: str) -> str:
    """Причина не ставить звонок семье или '' — для очередей автопилота."""
    p = _p10(p10)
    zv = zvonki_nomera(conn, p)
    vse = semya(conn, uids, p)
    pr = otvet(kommentarii(conn, vse), vse, zv)
    if pr:
        return pr
    n, pisali = popytki(zv, _poslednee(conn, "wazzup_inbox", p), _poslednee(conn, "wazzup_outbox", p))
    if n >= 3 and pisali:
        return f"{n} звонка без ответа и наше сообщение — ждём, когда семья ответит"
    return ""


def nedavno_govorili(conn, p10: str, s_ts: str) -> bool:
    """Был ли разговор (от 20 с, итог опроса Манго) с этим номером начиная с s_ts."""
    p = _p10(p10)
    if len(p) < 10:
        return False
    try:
        return bool(conn.execute(
            "SELECT 1 FROM mango_calls WHERE substr(phone,-10)=? AND state='talked' "
            "AND replace(ts,' ','T') >= ? LIMIT 1", (p, s_ts)).fetchone())
    except Exception:
        return False
