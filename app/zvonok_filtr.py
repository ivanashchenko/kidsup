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

# Семья свяжется сама — только будущее время: «мама сама позвонила» — это не
# отказ. Первого лица множественного («позвоним», «напишем», «свяжемся»,
# «перезвоним», «наберём») здесь нет: без «если что» это обычно наше обещание
# («сами позвоним, когда откроется группа»), а «мы сами…» — тем более.
_BUD = (r"свяжусь|свяжутся|свяжется|напишу|напишут|напишет|позвоню|позвонят|позвонит|"
        r"перезвоню|перезвонят|перезвонит|наберу|наберут|наберёт|наберет|"
        r"обращусь|обратимся|обратятся|обратится|вернусь|вернёмся|вернемся|"
        r"вернутся|вернётся|вернется|объявлюсь|объявимся|объявятся|объявится")
_ESLI = (r"вернёмся|вернемся|вернутся|позвоним|позвонят|позвонит|перезвоним|перезвонят|перезвонит|"
         r"напишем|напишут|напишет|обратимся|обратятся|обратится|свяжемся|свяжутся|свяжется|"
         r"наберём|наберем|наберут")
# «Не звонить» с уточнением — это про время звонка, не отказ: «до 12»,
# «по утрам», «на работу», «рано утром».
_KROME = r"(?!\s+(?:до|после|утром|днём|днем|вечером|раньше|позже|в|во|с|по|на|рано|поздно|часто)\b)"

# Ответ, после которого звонок — навязчивость, пока семья не объявится сама.
STOP = re.compile(
    rf"(?<!мы\s)\b(?:{_BUD})\s+(?:к\s+вам\s+|вам\s+)?сам[аи]?\b|"
    rf"(?<!мы\s)\bсам[аи]?\s+(?:{_BUD})\b|"
    rf"\bне\s+(?:звонить|беспокоить|перезванивать|звоните|(?:надо|нужно)\s+(?:звонить|перезванивать))\b{_KROME}|"
    r"(?<!не\s)(?<!не\sтак\s)(?<!не\sочень\s)(?<!не\sслишком\s)\bдалек(?:о|оват\w*)\b(?!\s+ли\b)|"
    r"\b(?:выбрали|нашли)\s+друг(?:ой|ую|ие|ое)\s+(?:центр|кружок|секци|студи|школ|сад)\w*|"
    r"\b(?:ходят|ходим|ходит|записались|ушли|перешли)\s+в\s+друг(?:ой|ую|ие|ое)\s+(?:центр|кружок|секци|студи)\w*|"
    r"\bопределились\s+с\s+(?:друг|детск)\w*\s+(?:центр|сад|школ)\w*|"
    r"\bне\s?актуальн\w*|\bне\s?интересн\w*|\bне\s+интересует\b|"
    r"\bзаверш\w*\s+общени\w*|"
    rf"\bесли\s+что\W{{0,3}}\s*(?:сами\s+)?(?:{_ESLI})\b|"
    r"(?<!не\s)(?<!без\s)\bотказ(?:ались|ался|алась|ываются|ывается)?\b(?!\s+от\b)",
    re.I)
# «Не актуально» и «не интересно» — мягкий ответ: про одно направление при
# интересе к другому («робототехника не актуальна, хотят английский») это не
# отказ, а «пока не актуально, позвонить в январе» — отсрочка, не запрет.
MYAGKO = re.compile(r"не\s?(?:актуальн|интересн|интересует)", re.I)
INTERES = re.compile(r"(?<!не\s)\b(?:хотят|хочет|хотим|хотели\s+бы|интересует|интересна|интересно|"
                     r"интересны|интересен|нравится)\b", re.I)
POKA = re.compile(r"\b(?:пока|сейчас|ещ[её]|на\s+данный\s+момент|в\s+этом\s+году)\b", re.I)

# Отсрочка: звонить можно, но не сейчас — «через месяц», «позвонить в марте»,
# «в конце месяца», «подрастут» (из карточек «думают» 17.10).
_MES = r"январ|феврал|март|апрел|ма[йяе]|июн|июл|август|сентябр|октябр|ноябр|декабр"
_ZVON = r"перезвонить|позвонить|набрать|звонить|перезвоним|позвоним|связаться|написать"
CHEREZ = re.compile(r"\bчерез\s+(?:(\d+|пару|два|две|три|четыре|пять|полгода)\s+)?"
                    r"(недел\w*|месяц\w*|полгода|год\w*|лет|дн\w*|день)\b", re.I)
V_MESYATS = re.compile(rf"\b(?:позвонить|перезвонить|звонить|набрать|вернуться|связаться|вернутся)\s+"
                       rf"(в\s+конце\s+|в\s+начале\s+|в\s+|после\s+)({_MES})\w*", re.I)
V_KONCE = re.compile(r"\bв\s+конце\s+(месяца|недели)\b", re.I)
# Названная дата: «перезвонить 25.10», «позвонить 2 ноября», «не звонить до
# ноября». Только неопределённая форма и «перезвоним»: «перезвонила 15.10» —
# это прошлое, не срок.
DATA_ZVONKA = re.compile(rf"\b(?:{_ZVON})\s+(?:после\s+|с\s+)?(\d{{1,2}})\.(\d{{2}})\b", re.I)
DATA_SLOVOM = re.compile(rf"\b(?:{_ZVON})\s+(?:после\s+|с\s+)?(\d{{1,2}})\s+({_MES})\w*", re.I)
NE_DO = re.compile(rf"\bне\s+(?:(?:надо|нужно)\s+)?(?:звонить|беспокоить|перезванивать)\s+до\s+(?:(\d{{1,2}})\.(\d{{2}})\b|({_MES})\w*)", re.I)
NOVYJ_GOD = re.compile(r"\bпосле\s+нового\s+года\b|\b(?:в\s+следующем|на\s+следующий)\s+(учебн\w*\s+)?год\w*", re.I)
SEZON = re.compile(r"\b(?:перезвонить|позвонить|звонить|набрать|связаться|вернуться|вернутся|придут|начнут|начать)"
                   r"\s+(?:\w+\s+)?(весной|летом|осенью|зимой)\b", re.I)
PAUZA = re.compile(
    r"\bпосле\s+(?:\w+\s+){0,2}каникул\b|\bна\s+будущее\b|"
    r"\b(?:сейчас|пока|ещ[её])\s+рано\b(?!\s+утр)|\bрано\s+ещ[её]\b|"
    r"\bвернутся\s+(?:к\s+(?:вопросу|занятиям)\s+)?(?:после|позже|осенью|зимой|весной|летом)\b|"
    rf"{CHEREZ.pattern}|{V_MESYATS.pattern}|{V_KONCE.pattern}|\bдораст\w*|\bподраст\w*|"
    rf"{DATA_ZVONKA.pattern}|{DATA_SLOVOM.pattern}|{NE_DO.pattern}|{NOVYJ_GOD.pattern}|{SEZON.pattern}",
    re.I)
_CHISLO = {"пару": 2, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5}
_NOMER_MES = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "май": 5, "мая": 5, "мае": 5, "июн": 6,
              "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
_SEZON_MES = {"весной": 3, "летом": 6, "осенью": 9, "зимой": 12}


def _plus_mes(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, 28))


def _konec_mes(d: date) -> date:
    return _plus_mes(date(d.year, d.month, 1), 1) - timedelta(days=1)

# Отметка о недозвоне — не ответ семьи; решает комментарий до неё.
# «но», «Н/о», «12.10 но», «не дозвонилась», «сбрасывает», «телефон выключен».
_NET_OTVETA = (r"нет\s+ответа|не\s+дозвон\w*|недозвон\w*|не\s+бер[её]т\w*|не\s+берут|трубку\s+не\s+бер\w*|"
               r"не\s+взял\w*|не\s+отвеча\w*|не\s+ответил\w*|сброс\w*|сбрасыва\w*|занято|абонент\w*|"
               r"автоответ\w*|недоступ\w*|не\s+доступ\w*|вне\s+зоны|выключен\w*")
PUSTO = re.compile(
    r"^\W*(?:\d{1,2}\.\d{2}(?:\.\d{2,4})?(?:\s+\d{1,2}:\d{2})?\W*)?(?:"
    r"(?:н[./]?\s?о\.?|но)\W*(?:\(\d+\))?\W*(?:\d{1,2}\.\d{2}(?:\.\d{2,4})?)?\W*$|"
    rf"{_NET_OTVETA})", re.I)
NET_OTVETA = re.compile(_NET_OTVETA, re.I)   # в коротких: «Звонила, не дозвонилась»

# Итог обзвона и кнопки «Прозвон» — пишутся от админа, но с префиксом:
# «12.10 11:21, обзвон (Ира): …», «17.10 11:21 · обзвон (Ира): …».
PREFIKS = re.compile(r"^(?:📞\s*)?(?:\d{1,2}\.\d{2}(?:\s+\d{1,2}:\d{2})?\s*[,·]?\s*(?:обзвон|прозвон)[^:]{0,40}:\s*|"
                     r"прозвон\s*\([^)]*\):\s*)", re.I)
OBZVON = re.compile(r"^\d{1,2}\.\d{2}(?:\s+\d{1,2}:\d{2})?\s*[,·]?\s*обзвон\b", re.I)

# Служебные комментарии автоматики: они пишутся от того же аккаунта, что и
# админы, и не несут ответа семьи («17.10 11:45 Клод: напомнили о себе…»,
# «Ответ в переписке…», подсказка к звонку, отзыв педагога, отметки звонков
# «Набирали 11:20 МСК…» и «Звонок 11:20 МСК…», справка «👤 ПОД ЭТУ СЕМЬЮ»).
ROBOT = re.compile(r"^(?:🤖|🎯|👤|Клод\b|Авто\b|\d{1,2}\.\d{2}(?:\s+\d{1,2}:\d{2})?\s+Клод\b|"
                   r"Ответ в переписке|Педагог\s|Набирали\s+\d|Звонок\s+\d{1,2}:\d{2}\s+МСК)", re.I)


def _p10(x) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())[-10:]


def _msk(ts_utc: str) -> str:
    """'2026-10-16T07:54:00' (UTC из МойКласса) → '2026-10-16T10:54' (МСК)."""
    try:
        return (datetime.fromisoformat(str(ts_utc)[:19]) + timedelta(hours=3)).isoformat()[:16]
    except ValueError:
        return str(ts_utc)[:16]


def _nomer_mes(slovo: str) -> int:
    return next((v for k, v in _NOMER_MES.items() if slovo.lower().startswith(k)), 0)


def _den_mesyaca(d0: date, den: int, mes: int) -> date | None:
    """Ближайшее «ДД.ММ» от d0. Дата в прошлом до 60 дней — это прошедший срок
    (пауза уже кончилась), а не тот же день через год."""
    try:
        d = date(d0.year, mes, den)
    except ValueError:
        return None
    if d < d0 - timedelta(days=60):
        try:
            d = date(d0.year + 1, mes, den)
        except ValueError:
            return None
    return d


def _data_pauzy(t: str, kogda_msk: str) -> date:
    """До какого дня пауза: названная дата («перезвонить 02.11», «позвонить
    2 ноября», «не звонить до ноября»), срок («через месяц»), месяц («позвонить
    в марте», «в конце месяца»), «после нового года», сезон или SROK_PAUZY дней."""
    try:
        d0 = date.fromisoformat(kogda_msk[:10])
    except ValueError:
        d0 = date.today()
    for rx in (DATA_ZVONKA, NE_DO):
        m = rx.search(t)
        if m and m.group(1) and m.group(2):
            d = _den_mesyaca(d0, int(m.group(1)), int(m.group(2)))
            if d:
                return d
    m = DATA_SLOVOM.search(t)
    if m and _nomer_mes(m.group(2)):
        d = _den_mesyaca(d0, int(m.group(1)), _nomer_mes(m.group(2)))
        if d:
            return d
    m = NE_DO.search(t)
    if m and m.group(3) and _nomer_mes(m.group(3)):
        num = _nomer_mes(m.group(3))
        return date(d0.year if num > d0.month else d0.year + 1, num, 1)
    m = CHEREZ.search(t)
    if m:
        n = m.group(1) or "1"
        n = 6 if n == "полгода" else (int(n) if n.isdigit() else _CHISLO.get(n.lower(), 1))
        ed = m.group(2).lower()
        if ed.startswith("полгода"):
            return _plus_mes(d0, 6)
        if ed.startswith("недел"):
            return d0 + timedelta(weeks=min(n, 104))
        if ed.startswith(("дн", "день")):
            return d0 + timedelta(days=min(n, 730))
        if ed.startswith(("год", "лет")):
            return _plus_mes(d0, 12 * min(n, 2))
        return _plus_mes(d0, min(n, 24))
    m = V_MESYATS.search(t)
    if m:
        kak, num = m.group(1).strip().lower(), _nomer_mes(m.group(2))
        if num:
            god = d0.year if num >= d0.month else d0.year + 1
            nachalo = date(god, num, 1)
            if kak.startswith("в конце"):
                return _konec_mes(nachalo) - timedelta(days=7)
            if kak.startswith("после"):
                return _plus_mes(nachalo, 1)
            return nachalo if nachalo > d0 else d0
    m = V_KONCE.search(t)
    if m:
        return d0 + timedelta(days=6) if m.group(1).lower().startswith("недел") else _konec_mes(d0) - timedelta(days=5)
    m = NOVYJ_GOD.search(t)
    if m:
        if m.group(1):                                    # «в следующем учебном году» — к набору
            return date(d0.year + (1 if d0.month >= 6 else 0), 8, 20)
        return d0 if d0.month <= 2 else date(d0.year + 1, 1, 10)
    m = SEZON.search(t)
    if m:
        num = _SEZON_MES[m.group(1).lower()]
        nachalo = date(d0.year if num > d0.month else d0.year + 1, num, 1)
        tekushchij = num <= d0.month < num + 3 or (num == 12 and d0.month <= 2)
        return d0 + timedelta(days=SROK_PAUZY) if tekushchij else nachalo
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
    from .voronka import MANAGERS, _CALL_LOG
    segodnya = segodnya or date.today()
    rows = [r for u in (uids or []) for r in kom.get(u, [])]
    zhivoy = _zhivoy_posle(zvonki or [])
    for ts, mid, text in sorted(rows, key=lambda r: str(r[0] or ""), reverse=True):
        t = str(text or "").strip()
        t0 = t.lstrip("📞").strip()
        razbor = t.startswith("📞") or bool(_CALL_LOG.match(t0)) or bool(OBZVON.match(t0))
        if not razbor and (ROBOT.match(t0) or mid not in MANAGERS):
            continue                      # служебное — не ответ семьи
        # в разборе дальше сырой текст разговора (там и наши фразы), в итоге
        # обзвона — текст нашей задачи
        t0 = t0.split("Расшифровка:")[0].split("По задаче:")[0]
        t0 = PREFIKS.sub("", t0).strip()
        kogda = _msk(ts)
        stop, pauza = STOP.search(t0), bool(PAUZA.search(t0))
        if stop and MYAGKO.match(stop.group(0)):
            if INTERES.search(t0):
                stop = None               # не актуально одно — хотят другое
            elif pauza or POKA.search(t0):
                stop, pauza = None, True  # «пока не актуально, позвонить в январе»
        if stop:
            if zhivoy and zhivoy > kogda:
                return ""                 # после этого семья объявилась — запрет снят
            kto = "разбор звонка" if razbor else MANAGERS.get(mid, "админ")
            return f"{kogda[:10]} {kto}: «{t0[:90]}»"
        if pauza:
            do = _data_pauzy(t0, kogda)
            if (zhivoy and zhivoy > kogda) or segodnya >= do:
                return ""
            kto = "разбор звонка" if razbor else MANAGERS.get(mid, "админ")
            return f"{kogda[:10]} {kto}: «{t0[:90]}» — пауза до {do:%d.%m}"
        if PUSTO.match(t0) or (len(t0) < 40 and NET_OTVETA.search(t0)):
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
