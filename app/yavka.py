# -*- coding: utf-8 -*-
"""Явка по отчётам педагогов: чат «KidsUP Team» → занятия МойКласса.

03.10.2026, Борис: «педагоги каждый день пишут списки „были / не был“ в
KidsUP Team, а в МойКлассе явка не отмечена три дня. Делаю сверку
автоматически — делай! И ты можешь сам отмечать?»

Как устроено:
  • отчёты — обычные сообщения в группе (через Wazzup, таблица gruppy_chaty).
    Автора вебхук почти никогда не даёт (у педагога может не быть username),
    поэтому разбираем сам текст: дата, предмет («ПШ», «Логопед», «Музыка с
    мамой», «Лицей», «английский»…), время («16:00», «10.00ч», «⏰11:00»),
    списки имён, маркеры «Не было:», пометки в скобках и после имени
    («болеет», «отработка», «пробное», «новый»);
  • занятия дня берём живьём из API (/v1/company/lessons?date=…&includeRecords),
    имена детей — из локальной таблицы users (недостающие — по API);
  • имя из отчёта ищем среди записанных на занятия-кандидаты: тот же предмет
    по названию группы и время ±20 минут. Уменьшительные (Саша → Александр/
    Александра), порядок слов, опечатки в одну букву — учитываем. Совпало
    ровно с одним ребёнком — однозначно; с несколькими (два Саши в ИЗО) —
    спорно; ни с одним — «нет в записи» (отработка, пробное без записи,
    опечатка) — это Лизе;
  • пишем в МойКласс ТОЛЬКО однозначное: visit=true тем, кто «был»,
    goodReason=true тем, кто «не был» по болезни/предупредив. Провести
    занятие (status=1 — с этого момента списываются абонементы) — только
    если в отчёте учтён каждый записанный ребёнок и нет спорных имён.
    Режим yavka_auto: 0 — только сверка и пункт в Пульт (по умолчанию),
    1 — ставить отметки, 2 — ставить и проводить. Включает владелец.
"""
from __future__ import annotations

import collections
import datetime as dt
import difflib
import json
import logging
import re

from . import db

log = logging.getLogger("kidsup.yavka")
CHAT_TEAM = "4378505637"  # Telegram-группа «KidsUP Team»

# предмет в тексте отчёта → подстроки названия группы в CRM («2627_ПШ_вт-пт_16:00_…»)
SUBJECTS = [
    (re.compile(r"запуск\w*\s+речи"), ("ЛГ Елена",)),
    (re.compile(r"логопед\w*"), ("ЛГ",)),
    (re.compile(r"музык[аи]\s+(с\s+мамой|и\s+речь)|\bмсм\b"), ("Музыка и речь",)),
    (re.compile(r"раннее\s+развитие|первая\s+школа"), ("Первая школа",)),
    (re.compile(r"мини[\s-]?сад\w*"), ("Мини-сад",)),
    (re.compile(r"лицей\w*"), ("Лицей",)),
    (re.compile(r"нулев\w*(\s+класс\w*)?"), ("Нулевой класс",)),
    (re.compile(r"английск\w*|english|\bангл\b"), ("АЯ",)),
    (re.compile(r"\bизо\b|изо-?студи\w*|живопис\w*"), ("ИЗО",)),
    (re.compile(r"ментальн\w*(\s+арифметик\w*)?|\bма\b"), ("МА",)),
    (re.compile(r"\bпш\b|подготовк[аеи]\s+к\s+школе"), ("ПШ",)),
    (re.compile(r"шахмат\w*"), ("ШАХ", "Шахмат")),
    (re.compile(r"робот\w*"), ("Робот",)),
]
RE_PRESENT = re.compile(r"^(были|был[аи]?|присутствовали|пришли)\s*:?\s*$")
RE_ABSENT = re.compile(r"^(не\s*был[аои]?|не\s*пришл[иа]|отсутствовал[аи]?|пропустил[аи]?|нет)\s*:?\s*(.*)$")
RE_TIME = re.compile(r"^(\d{1,2})\s*[.:]\s*(\d{2})\s*ч?(?![\d])\s*[-–—:]?\s*(.*)$")
RE_HOUR = re.compile(r"^(\d{1,2})\s*ч(?![а-яa-z])\s*[-–—:]?\s*(.*)$")
RE_DATE = re.compile(r"(?<!\d)(\d{1,2})\s*[./]\s*(\d{1,2})(?:\s*[./]\s*(\d{2,4}))?(?!\d)")
# номер пункта списка («1. Имя», «2)Имя») — за ним буква; «10.00» — время, его не трогаем
RE_NUM = re.compile(r"^\s*(?:\d{1,2}\s*[.)]\s*(?=[^\d\s.)])|[•·\-–—*⏰]\s*)+")
# после имени педагоги дописывают состояние — по нему и режем
RE_NOTE = re.compile(r"\b(отработ\w*|пробн\w*|болеет|болен|больна|заболел\w*|уехал\w*|отпуск\w*|заморозк\w*|нов(ая|ый|енький|енькая)|"
                     r"мама\b.*|предупред\w*|опоздал\w*|пришел\w*|пришла|будет\b.*|будут\b.*|т\.?к\.?\b.*|из\s+\d.*|с\s+\d{1,2}[.:]\d{2}.*|"
                     r"справк\w*|уваж\w*|карантин\w*|сад\w*|занят\w*|перенос\w*|отмен\w*|последний\s+раз.*|переходит.*|девочка|мальчик)", re.I)
# строка с такими словами — не имя, а разговор
RE_NOISE = re.compile(r"\b(набираем|групп[аыу]|заняти\w*|урок\w*|сегодня|завтра|вчера|спасибо|пожалуйста|добрый|доброе|здравствуйте|"
                      r"коллеги|удалено|кликните|сообщение|списки|прислать|пришлите|всех|нас|каюсь|видела|должна|ресепшен\w*)\b", re.I)
GOOD_REASON = re.compile(r"боле|заболе|справк|уваж|предупред|карантин|уехал|отпуск|перенос", re.I)
SERVICE_LINES = ("сообщение изменено", "сообщение удалено", "кликните, чтобы перейти к сообщению", "изменено", "удалено")

DIM = {
    "саша": ("александр", "александра"), "шура": ("александр", "александра"), "алекс": ("александр", "александра", "алексей"),
    "миша": ("михаил",), "федя": ("федор",), "паша": ("павел",), "вова": ("владимир",), "володя": ("владимир",),
    "варя": ("варвара",), "катя": ("екатерина",), "витя": ("виктор",), "леша": ("алексей",), "мила": ("милана", "людмила", "камилла", "милица", "мила"),
    "настя": ("анастасия",), "маша": ("мария",), "даша": ("дарья",), "соня": ("софия", "софья"), "софа": ("софия", "софья"),
    "софья": ("софия", "софья"), "софия": ("софия", "софья"), "тася": ("таисия",), "лиза": ("елизавета",), "костя": ("константин",),
    "аня": ("анна",), "таня": ("татьяна",), "вика": ("виктория",), "женя": ("евгений", "евгения"), "сева": ("всеволод",),
    "лена": ("елена",), "рома": ("роман",), "тема": ("артем",), "слава": ("вячеслав", "ярослав", "святослав"), "ваня": ("иван",),
    "дима": ("дмитрий",), "коля": ("николай",), "юля": ("юлия",), "лева": ("лев",), "тима": ("тимофей", "тимур"),
    "сеня": ("семен", "арсений"), "степа": ("степан",), "даня": ("даниил", "данила", "данил"), "данила": ("даниил", "данил", "данила"),
    "даниил": ("данила", "данил", "даниил"), "кирюша": ("кирилл",), "оля": ("ольга",), "гриша": ("григорий",), "андрюша": ("андрей",),
    "влада": ("владислава", "влада"), "люба": ("любовь",), "мира": ("мирослава", "мира"), "ника": ("вероника", "ника"),
    "рита": ("маргарита",), "леня": ("леонид",), "боря": ("борис",), "петя": ("петр",), "сережа": ("сергей",), "максим": ("максим",),
    "макс": ("максим",), "никита": ("никита",), "глеб": ("глеб",), "ярик": ("ярослав",), "марк": ("марк",), "матвей": ("матвей",),
    "лера": ("валерия",), "валя": ("валентина", "валентин"), "кира": ("кира",), "алиса": ("алиса",), "агата": ("агата",),
}
NOTE_WORDS = {"был", "была", "были", "не", "и", "а", "но", "в", "на", "с", "по", "за", "до", "от", "у", "к", "о"}


def _norm(s: str) -> list[str]:
    s = (s or "").lower().replace("ё", "е")
    return [t for t in re.sub(r"[^а-яa-z\- ]", " ", s).replace("-", " ").split() if t]


def _tok_match(t: str, f: str) -> bool:
    """Слово из отчёта t против слова из карточки f."""
    if t == f:
        return True
    for x in (t,) + tuple(DIM.get(t, ())):
        if f == x:
            return True
        if len(x) >= 4 and f.startswith(x[:max(4, len(x) - 1)]):
            return True
        if len(f) >= 4 and x.startswith(f[:max(4, len(f) - 1)]):
            return True
    if len(t) >= 5 and len(f) >= 5 and difflib.SequenceMatcher(None, t, f).ratio() >= 0.8:
        return True  # опечатка в одну букву: Бахтимиров / Бактимиров, Любань / Лобань, Котков / Катков
    return False


def name_matches(report_name: str, crm_name: str) -> bool:
    toks = _norm(report_name)
    full = _norm(crm_name)
    if not toks or not full:
        return False
    if len(toks) == 1 and len(toks[0]) < 3:
        return False
    used = set()
    for t in toks:
        hit = None
        for i, f in enumerate(full):
            if i in used:
                continue
            if len(t) == 1:  # инициал: «Зиньковская Д»
                if f.startswith(t):
                    hit = i
                    break
            elif _tok_match(t, f):
                hit = i
                break
        if hit is None:
            return False
        used.add(hit)
    return True


# ---------------------------------------------------------------- разбор текста

def _date_in(line: str, msg_date: dt.date) -> dt.date | None:
    """Дата отчёта в строке: dd.mm не старше 3 дней и не из будущего, причём
    строка после даты — не имя (иначе это время, как «9.10 Гунт Лео» у логопеда)."""
    for m in RE_DATE.finditer(line):
        d, mo = int(m.group(1)), int(m.group(2))
        if not (1 <= d <= 31 and 1 <= mo <= 12):
            continue
        year = msg_date.year
        if m.group(3):
            y = int(m.group(3))
            year = y + 2000 if y < 100 else y
        try:
            cand = dt.date(year, mo, d)
        except ValueError:
            continue
        if not (-3 <= (msg_date - cand).days <= 1):
            continue
        rest = (line[:m.start()] + " " + line[m.end():]).strip(" .:-–—()⏰")
        rest_l = rest.lower()
        if not rest or any(rx.search(rest_l) for rx, _ in SUBJECTS) or len(_norm(rest)) <= 1:
            return cand
        if RE_TIME.match(rest) or RE_HOUR.match(rest):
            return cand
    return None


def _subject(line_l: str):
    for rx, keys in SUBJECTS:
        m = rx.search(line_l)
        if m:
            return keys, m
    return None, None


def _split_names(line: str) -> list[tuple[str, str]]:
    """Строка с именами → [(имя, пометка)]. «Алиса Михавилова, Ксения Лубенец»;
    «Марк Костанян (отработка)»; «Настя Копылова уехала, будет отрабатывать»;
    «Зиньковская Д» (инициал отбрасываем, ищем по фамилии)."""
    line = RE_NUM.sub("", line).strip()
    notes = " ".join(re.findall(r"\(([^)]*)\)", line))
    line = re.sub(r"\([^)]*\)", " ", line)
    line = re.sub(r"[.!]+$", "", line).strip()
    if not line or RE_NOISE.search(line):
        return []
    out = []
    chunks = [c.strip(" .;") for c in re.split(r"[,;]", line) if c.strip(" .;")]
    if any(len(_norm(c)) > 4 for c in chunks):
        chunks = [line]  # не список имён, а фраза с запятыми
    for c in chunks:
        note = notes
        m = RE_NOTE.search(c)
        if m:
            note = (note + " " + c[m.start():]).strip()
            c = c[:m.start()]
        raw = _norm(c)
        # одиночная буква в конце — инициал («Саркулова К»), в середине — предлог
        toks = [t for i, t in enumerate(raw)
                if (len(t) >= 2 and t not in NOTE_WORDS) or (len(t) == 1 and i == len(raw) - 1 and i > 0)]
        if not toks or len(toks) > 3 or sum(len(t) for t in toks) < 4 or len(toks[0]) < 2:
            continue
        out.append((" ".join(toks), note.strip()))
    return out


def _time_of(s: str, msg_date: dt.date):
    """«16:00 Имя» / «10.00ч» / «11ч» → ('16:00', остаток) или None. Дата — не время."""
    if _date_in(s, msg_date):
        return None
    mt = RE_TIME.match(s)
    mh = None if mt else RE_HOUR.match(s)
    if not (mt or mh):
        return None
    h = int((mt or mh).group(1))
    mi = int(mt.group(2)) if mt else 0
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return f"{h:02d}:{mi:02d}", ((mt.group(3) if mt else mh.group(2)) or "").strip()


def parse_report(text: str, msg_ts: str) -> list[dict]:
    """Текст сообщения → блоки {date, subj, time, present:[(имя, пометка)], absent:[...]}.
    Пустой список — это не отчёт о явке."""
    msg_date = dt.date.fromisoformat(msg_ts[:10])
    lines = []
    for raw in (text or "").split("\n"):
        s = raw.strip()
        low = s.lower().strip(" .")
        if not s or low in SERVICE_LINES or low.startswith("кликните"):
            continue
        if s.startswith("@"):
            continue  # обращения к админам, не явка
        lines.append(s)
    if not lines:
        return []
    date = None
    for l in lines[:4]:
        date = _date_in(l, msg_date)
        if date:
            break
    explicit_date = date is not None
    date = date or msg_date
    blocks: list[dict] = []
    cur = None
    subj = None
    mode = "present"
    saw_marker = False

    def new_block(t, keep_mode=False):
        nonlocal cur, mode
        cur = {"date": date.isoformat(), "subj": subj, "time": t, "present": [], "absent": []}
        blocks.append(cur)
        if not keep_mode:
            mode = "present"

    for l in lines:
        s = RE_NUM.sub("", l).strip()
        if not s:
            continue
        low = s.lower()
        if RE_PRESENT.match(low):
            mode = "present"; saw_marker = True
            continue
        ma = RE_ABSENT.match(low)
        if ma and (not ma.group(2) or len(_norm(ma.group(2))) <= 3):
            mode = "absent"; saw_marker = True
            rest = s[ma.start(2):].strip() if ma.group(2) else ""
            if rest:
                if cur is None:
                    new_block(None, keep_mode=True)
                cur["absent"].extend(_split_names(rest))
            continue
        d_here = _date_in(s, msg_date)
        keys, msub = _subject(low)
        if keys:
            # заголовок предмета: «26.09 Логопед», «ПШ (01.10)», «3.10 Музыка с мамой. 9.45 Имя»
            subj = keys
            rest = s[:msub.start()] + " " + s[msub.end():]
            if d_here:
                rest = RE_DATE.sub(" ", rest)
            rest = rest.strip(" .:-–—()⏰")
            tm = _time_of(rest, msg_date) if rest else None
            new_block(tm[0] if tm else None)
            rest = tm[1] if tm else rest
            if rest and not d_here:
                cur[mode].extend(_split_names(rest))
            continue
        if d_here and len(_norm(RE_DATE.sub(" ", s))) <= 1:
            continue  # строка-дата
        tm = _time_of(s, msg_date)
        if tm:
            t, rest = tm
            names = _split_names(rest) if rest else []
            # «17:00» отдельной строкой — новый слот, и список снова «были».
            # «16.00 Имя» внутри раздела «Не были:» (логопед) — слот свой, раздел тот же.
            if cur is None or cur["time"] != t or (not names and (cur["present"] or cur["absent"])):
                new_block(t, keep_mode=bool(names))
            if names:
                cur[mode].extend(names)
            continue
        names = _split_names(s)
        if not names:
            continue
        if cur is None:
            new_block(None, keep_mode=True)
        cur[mode].extend(names)
    out = [b for b in blocks if b["present"] or b["absent"]]
    # сообщение без даты, предмета, времени и маркеров «были/не был» — отчёт
    # только если это явный список: ≥3 имён вида «Фамилия Имя»
    if out and not explicit_date and not saw_marker and all(b["subj"] is None and b["time"] is None for b in out):
        names = [n for b in out for n, _ in b["present"] + b["absent"]]
        if len(names) < 3 or any(len(n.split()) != 2 for n in names):
            return []
    for b in out:
        b["subj"] = list(b["subj"]) if b["subj"] else None
    return out


# ---------------------------------------------------------------- данные

def _reports(day: str) -> list[dict]:
    """Сообщения группы «KidsUP Team» за день и соседние (отчёт за вечер
    пишут и ночью, и утром), разобранные на блоки нужного дня."""
    d = dt.date.fromisoformat(day)
    lo = (d - dt.timedelta(days=0)).isoformat() + "T00:00"
    hi = (d + dt.timedelta(days=2)).isoformat() + "T00:00"
    with db.get_conn() as conn:
        try:
            rows = conn.execute(
                "SELECT ts, author, username, text, message_id, kind FROM gruppy_chaty "
                "WHERE chat_id LIKE ? AND ts >= ? AND ts < ? ORDER BY ts, id",
                (f"%{CHAT_TEAM}", lo, hi)).fetchall()
        except Exception:
            return []
    out, seen = [], set()
    for r in rows:
        if (r["kind"] or "") == "deleted" or not (r["text"] or "").strip():
            continue
        for b in parse_report(r["text"], r["ts"]):
            if b["date"] != day:
                continue
            sig = (b["subj"] and b["subj"][0], b["time"], tuple(n for n, _ in b["present"]), tuple(n for n, _ in b["absent"]))
            if sig in seen:
                continue  # один отчёт пришёл дважды (вебхук + импорт истории)
            seen.add(sig)
            b.update({"author": r["author"] or r["username"] or "", "ts": r["ts"], "message_id": r["message_id"]})
            out.append(b)
    return out


def _mk():
    from .moyklass_client import MoyklassClient
    from . import sync
    key = sync.get_api_key()
    if not key:
        raise RuntimeError("API-ключ МойКласса не задан")
    return MoyklassClient(key)


def _lessons(day: str, mk=None) -> list[dict]:
    own = mk is None
    mk = mk or _mk()
    try:
        return mk.fetch_all("/v1/company/lessons", ["lessons"], params={"date": day, "includeRecords": "true"})
    finally:
        if own:
            mk.close()


def _names(uids: set[int], mk=None) -> dict[int, str]:
    names: dict[int, str] = {}
    if not uids:
        return names
    with db.get_conn() as conn:
        q = ",".join("?" * len(uids))
        for r in conn.execute(f"SELECT id, name FROM users WHERE id IN ({q})", tuple(uids)):
            names[int(r["id"])] = r["name"] or ""
    missing = [u for u in uids if u not in names]
    if missing:
        own = mk is None
        try:
            mk = mk or _mk()
            for u in missing[:40]:
                try:
                    names[u] = (mk.get(f"/v1/company/users/{u}") or {}).get("name") or ""
                except Exception:
                    names[u] = ""
        except Exception:
            pass
        finally:
            if own and mk is not None:
                try:
                    mk.close()
                except Exception:
                    pass
    return names


def _class_names() -> dict[int, str]:
    with db.get_conn() as conn:
        return {int(r["id"]): r["name"] or "" for r in conn.execute("SELECT id, name FROM classes")}


def _teachers() -> dict[int, str]:
    with db.get_conn() as conn:
        try:
            return {int(r["id"]): r["name"] or "" for r in conn.execute("SELECT id, name FROM managers")}
        except Exception:
            return {}


def _minutes(t: str) -> int | None:
    try:
        h, m = t.split(":")[:2]
        return int(h) * 60 + int(m)
    except Exception:
        return None


# ---------------------------------------------------------------- сверка

def sverka(day: str, lessons: list[dict] | None = None, mk=None) -> dict:
    """Отчёты педагогов за день против занятий МойКласса.

    Возвращает {day, lessons:[…], bez_otcheta:[…], ne_razobrano:[…], itogo:{…}}.
    В каждом занятии: records с полями report ('был'/'не был'/None), crm_visit,
    crm_good, action — что поставим; unmatched — имена без записи; ambiguous."""
    reports = _reports(day)
    own = mk is None and lessons is None
    if lessons is None:
        mk = mk or _mk()
        lessons = _lessons(day, mk)
    cls = _class_names()
    teachers = _teachers()
    lessons = [L for L in lessons if L.get("records") is not None]
    uids = {int(r["userId"]) for L in lessons for r in (L.get("records") or []) if r.get("userId")}
    names = _names(uids, mk)
    if own and mk is not None:
        try:
            mk.close()
        except Exception:
            pass
    by_id: dict[int, dict] = {}
    for L in lessons:
        cname = cls.get(int(L["classId"]), "") if L.get("classId") else ""
        by_id[L["id"]] = {
            "lesson_id": L["id"], "class_id": L.get("classId"), "class": re.sub(r"^\d{4}_", "", cname),
            "time": (L.get("beginTime") or "")[:5], "end": (L.get("endTime") or "")[:5], "status": L.get("status"),
            "room_id": L.get("roomId"),
            "teacher": ", ".join(teachers.get(int(t), str(t)) for t in (L.get("teacherIds") or [])),
            "records": [{"record_id": r["id"], "uid": r.get("userId"), "name": names.get(int(r["userId"]), "") if r.get("userId") else "",
                         "crm_visit": bool(r.get("visit")), "crm_good": bool(r.get("goodReason")), "test": bool(r.get("test")),
                         "report": None, "note": "", "action": None} for r in (L.get("records") or [])],
            "unmatched": [], "ambiguous": [], "reports": [], "conflicts": [],
        }

    def in_class(key, cname):
        k, c = key.lower(), cname.lower()
        if len(k) <= 3:  # «МА» не должно находиться в «Марина», «ПШ» — целое слово
            return re.search(r"(?<![а-яa-z])" + re.escape(k) + r"(?![а-яa-z])", c) is not None
        return k in c

    def candidates(block, use_time=True):
        out = []
        for L in lessons:
            cname = cls.get(int(L["classId"]), "") if L.get("classId") else ""
            if block["subj"] and not any(in_class(k, cname) for k in block["subj"]):
                continue
            if block["time"] and use_time:
                a, b = _minutes(block["time"]), _minutes(L.get("beginTime") or "")
                if a is None or b is None or abs(a - b) > 20:
                    continue
            out.append(L)
        return out

    def find(name, pool):
        hits = []
        for L in pool:
            for r in L.get("records") or []:
                nm = names.get(int(r["userId"]), "") if r.get("userId") else ""
                if nm and name_matches(name, nm):
                    hits.append((L, r))
        if hits:
            return hits
        # имя не сошлось («Степанян Дана» — в карточке «Даниелла»): если среди
        # кандидатов ровно один ребёнок с такой фамилией — это он
        toks = [t for t in _norm(name) if len(t) >= 5]
        if len(_norm(name)) >= 2 and toks:
            for L in pool:
                for r in L.get("records") or []:
                    nm = names.get(int(r["userId"]), "") if r.get("userId") else ""
                    ft = _norm(nm)
                    if any(t == f or (len(f) >= 5 and difflib.SequenceMatcher(None, t, f).ratio() >= 0.85) for t in toks for f in ft):
                        hits.append((L, r))
            if len(hits) != 1:
                return []
        return hits

    ne_razobrano = []
    for b in reports:
        pool = candidates(b)
        time_note = ""
        if not pool and b["time"]:
            pool = candidates(b, use_time=False)  # логопед перенёс ребёнка на другой час
            time_note = f"время в отчёте {b['time']}"
        all_names = [(n, note, "был") for n, note in b["present"]] + [(n, note, "не был") for n, note in b["absent"]]
        if not pool:
            ne_razobrano.append({"ts": b["ts"], "author": b["author"], "subj": b["subj"], "time": b["time"],
                                 "names": [n for n, _, _ in all_names], "why": "нет занятия с таким предметом в этот день"})
            continue
        touched = set()
        assigned = []  # (lesson_id, record_id, prev_report, prev_note) — чтобы откатить блок-пустышку
        extra = {"unmatched": [], "ambiguous": []}
        for n, note, kind in all_names:
            hits = find(n, pool)
            if not hits and not b["time"] and b["subj"]:
                hits = find(n, [L for L in lessons if L not in pool])  # предмет назвали иначе
            if len(hits) == 1:
                L, r = hits[0]
                rec = next(x for x in by_id[L["id"]]["records"] if x["record_id"] == r["id"])
                assigned.append((L["id"], r["id"], rec["report"], rec["note"]))
                if rec["report"] and rec["report"] != kind:
                    by_id[L["id"]]["conflicts"].append(f"{rec['name']}: в одном отчёте «был», в другом «не был»")
                rec["report"] = kind
                rec["note"] = (note + (f"; {time_note}" if time_note else "")).strip("; ")
                touched.add(L["id"])
            elif len(hits) > 1:
                same_lesson = {L["id"] for L, _ in hits}
                extra["ambiguous"].append((list(same_lesson)[0] if len(same_lesson) == 1 else None,
                                           {"name": n, "kind": kind, "note": note,
                                            "variants": sorted({names.get(int(r["userId"]), "") for _, r in hits})}))
            else:
                extra["unmatched"].append((None, {"name": n, "kind": kind, "note": note}))
        # имена без адреса кладём в занятие, где нашлось больше всего детей этого
        # блока (несколько занятий в одно время: ПШ вт-чт и ПШ вт-пт в 16:00,
        # логопед 11:40 и «Музыка с мамой» 11:45), иначе — в первое из кандидатов
        home = collections.Counter(lid for lid, _, _, _ in assigned).most_common(1)
        home = home[0][0] if home else pool[0]["id"]
        for k in ("ambiguous", "unmatched"):
            extra[k] = [(t if t is not None else home, item) for t, item in extra[k]]
            for t, _ in extra[k]:
                touched.add(t)
        # блок, где почти никто не нашёлся, — не из этих групп (педагог поставил не ту дату,
        # «29/09» вместо 28.09) или не отчёт вовсе: откатываем, чтобы не засорять занятия
        if len(all_names) >= 3 and len(assigned) < 0.4 * len(all_names):
            for lid, rid, prev_r, prev_n in assigned:
                rec = next(x for x in by_id[lid]["records"] if x["record_id"] == rid)
                rec["report"], rec["note"] = prev_r, prev_n
            ne_razobrano.append({"ts": b["ts"], "author": b["author"], "subj": b["subj"], "time": b["time"],
                                 "names": [n for n, _, _ in all_names],
                                 "why": f"из {len(all_names)} имён в записях этого дня нашлись {len(assigned)} — возможно, отчёт за другой день"})
            continue
        for target, item in extra["ambiguous"]:
            by_id[target]["ambiguous"].append(item)
        for target, item in extra["unmatched"]:
            by_id[target]["unmatched"].append(item)
        for lid in touched:
            by_id[lid]["reports"].append({"ts": b["ts"], "author": b["author"], "time": b["time"], "message_id": b["message_id"]})

    # что ставим. Проведённые занятия (status=1) не трогаем: там уже списаны
    # абонементы, расхождения только показываем
    now_dt = dt.datetime.now()
    today = now_dt.date().isoformat()
    now_m = now_dt.hour * 60 + now_dt.minute
    for Lr in by_id.values():
        if not Lr["reports"]:
            continue
        conducted = Lr["status"] == 1
        for rec in Lr["records"]:
            if rec["report"] == "был" and not rec["crm_visit"]:
                rec["action"] = "rashozhdenie" if conducted else "visit"
            elif rec["report"] == "не был":
                if rec["crm_visit"]:
                    rec["action"] = "conflict_visit"  # в CRM «был», педагог пишет «не был»
                elif GOOD_REASON.search(rec["note"] or "") and not rec["crm_good"]:
                    rec["action"] = None if conducted else "good_reason"
        accounted = bool(Lr["records"]) and all(r["report"] is not None or (r["test"] and r["crm_visit"]) for r in Lr["records"])
        over = day < today or (_minutes(Lr["end"] or Lr["time"]) or 0) < now_m
        Lr["mozhno_provesti"] = (accounted and over and not conducted and not Lr["unmatched"] and not Lr["ambiguous"]
                                 and not Lr["conflicts"] and any(r["report"] == "был" for r in Lr["records"]))
        Lr["ne_upomyanuty"] = [r["name"] for r in Lr["records"] if r["report"] is None]
        Lr["n_actions"] = sum(1 for r in Lr["records"] if r["action"] in ("visit", "good_reason"))
        Lr["n_rashozhdeniy"] = sum(1 for r in Lr["records"] if r["action"] in ("rashozhdenie", "conflict_visit"))

    with_reports = [L for L in by_id.values() if L["reports"]]
    bez = [L for L in by_id.values() if not L["reports"] and L["records"] and L["status"] != 1
           and (day < today or (_minutes(L["end"] or L["time"]) or 0) < now_m)]
    itogo = {
        "otchetov": len(reports), "zanyatiy_v_crm": len(by_id),
        "s_otchetom": len(with_reports),
        "ne_provedeno": sum(1 for L in with_reports if L["status"] != 1),
        "otmetok_postavit": sum(L["n_actions"] for L in with_reports),
        "mozhno_provesti": sum(1 for L in with_reports if L["mozhno_provesti"]),
        "imen_bez_zapisi": sum(len(L["unmatched"]) for L in with_reports),
        "spornyh": sum(len(L["ambiguous"]) for L in with_reports),
        "konfliktov": sum(len(L["conflicts"]) + L["n_rashozhdeniy"] for L in with_reports),
        "bez_otcheta": len(bez), "ne_razobrano": len(ne_razobrano),
    }
    res = {"day": day, "lessons": sorted(by_id.values(), key=lambda x: (x["time"], x["class"])),
           "bez_otcheta": [{"lesson_id": L["lesson_id"], "class": L["class"], "time": L["time"], "teacher": L["teacher"], "n": len(L["records"])} for L in sorted(bez, key=lambda x: x["time"])],
           "ne_razobrano": ne_razobrano, "itogo": itogo, "auto": db.get_setting("yavka_auto", "0"),
           "ts": dt.datetime.now().isoformat(timespec="minutes")}
    _save(day, res)
    return res


def _init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS yavka_sverka (day TEXT PRIMARY KEY, ts TEXT, data TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS yavka_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, day TEXT,
        lesson_id INTEGER, record_id INTEGER, uid INTEGER, name TEXT, field TEXT, value TEXT, who TEXT, result TEXT)""")


def _save(day: str, res: dict) -> None:
    with db.get_conn() as conn:
        _init(conn)
        conn.execute("INSERT OR REPLACE INTO yavka_sverka (day, ts, data) VALUES (?,?,?)",
                     (day, res["ts"], json.dumps(res, ensure_ascii=False)))


def cached(day: str) -> dict | None:
    with db.get_conn() as conn:
        _init(conn)
        r = conn.execute("SELECT data FROM yavka_sverka WHERE day=?", (day,)).fetchone()
    return json.loads(r["data"]) if r else None


def log_rows(day: str) -> list[dict]:
    with db.get_conn() as conn:
        _init(conn)
        return [dict(r) for r in conn.execute("SELECT * FROM yavka_log WHERE day=? ORDER BY id", (day,))]


# ---------------------------------------------------------------- запись в МойКласс

def otmetit(day: str, lesson_ids: list[int] | None = None, provesti: bool = False, who: str = "Клод", dry: bool = False) -> dict:
    """Поставить в МойКлассе однозначные отметки по сверке дня.

    visit=true — «был»; goodReason=true — «не был» по болезни/предупредив.
    provesti=True — ещё и провести занятия, где учтён каждый записанный
    (с этого момента списываются абонементы). Конфликты, спорные и «нет в
    записи» не трогаем — они остаются Лизе."""
    res = sverka(day)
    done, errors, skipped = [], [], []
    mk = None if dry else _mk()
    try:
        for L in res["lessons"]:
            if lesson_ids and L["lesson_id"] not in lesson_ids:
                continue
            if not L["reports"]:
                continue
            for rec in L["records"]:
                body = None
                if rec["action"] == "visit":
                    body = {"visit": True}
                elif rec["action"] == "good_reason":
                    body = {"visit": False, "goodReason": True}
                if not body:
                    continue
                result = "dry"
                if not dry:
                    try:
                        mk.post(f"/v1/company/lessonRecords/{rec['record_id']}", body)
                        result = "ok"
                    except Exception as e:
                        result = f"ошибка: {str(e)[:120]}"
                        errors.append({"lesson_id": L["lesson_id"], "name": rec["name"], "error": result})
                _log(day, L["lesson_id"], rec["record_id"], rec["uid"], rec["name"], ",".join(body.keys()), json.dumps(body), who, result)
                done.append({"lesson_id": L["lesson_id"], "class": L["class"], "time": L["time"], "name": rec["name"], "body": body, "result": result})
            if provesti:
                if L["mozhno_provesti"]:
                    result = "dry"
                    if not dry:
                        try:
                            mk.post(f"/v1/company/lessons/{L['lesson_id']}/status", {"status": 1})
                            result = "ok"
                        except Exception as e:
                            result = f"ошибка: {str(e)[:120]}"
                            errors.append({"lesson_id": L["lesson_id"], "error": result})
                    _log(day, L["lesson_id"], None, None, L["class"], "status", "1", who, result)
                    done.append({"lesson_id": L["lesson_id"], "class": L["class"], "time": L["time"], "name": "— занятие проведено —", "body": {"status": 1}, "result": result})
                elif L["status"] != 1:
                    skipped.append({"lesson_id": L["lesson_id"], "class": L["class"], "time": L["time"],
                                    "why": ("не упомянуты: " + ", ".join(L["ne_upomyanuty"][:5])) if L["ne_upomyanuty"] else
                                    ("нет в записи: " + ", ".join(u["name"] for u in L["unmatched"][:4])) if L["unmatched"] else
                                    ("спорные: " + ", ".join(a["name"] for a in L["ambiguous"][:4])) if L["ambiguous"] else
                                    ("конфликты: " + "; ".join(L["conflicts"][:2])) if L["conflicts"] else "в отчёте никто не был"})
    finally:
        if mk is not None:
            mk.close()
    if not dry and done:
        try:
            sverka(day)  # пересчитать после записи
        except Exception:
            log.exception("пересчёт сверки после отметки не удался")
    return {"ok": not errors, "day": day, "dry": dry, "postavleno": len([d for d in done if d["result"] in ("ok", "dry")]),
            "oshibok": len(errors), "done": done, "errors": errors, "skipped": skipped}


def _log(day, lesson_id, record_id, uid, name, field, value, who, result):
    with db.get_conn() as conn:
        _init(conn)
        conn.execute("INSERT INTO yavka_log (ts, day, lesson_id, record_id, uid, name, field, value, who, result) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (dt.datetime.now().isoformat(timespec="seconds"), day, lesson_id, record_id, uid, name, field, value, who, result))


# ---------------------------------------------------------------- расписание

def nightly(day: str | None = None, who: str = "Лиза") -> dict:
    """Вечером (и утром за вчера): сверка → пункт в Пульт. При yavka_auto≥1 —
    ещё и отметки (2 — и проведение учтённых занятий)."""
    from . import autopilot
    day = day or autopilot._today().isoformat()
    res = sverka(day)
    auto = db.get_setting("yavka_auto", "0")
    applied = None
    if auto in ("1", "2") and res["itogo"]["s_otchetom"]:
        try:
            applied = otmetit(day, provesti=(auto == "2"), who="автоматика")
            res = cached(day) or res
        except Exception as e:
            log.exception("авто-отметка явки не удалась")
            applied = {"ok": False, "error": str(e)[:200]}
    it = res["itogo"]
    dd = f"{day[8:10]}.{day[5:7]}"
    if it["s_otchetom"] or it["bez_otcheta"]:
        parts = [f"Явка {dd}: отчёты педагогов есть по {it['s_otchetom']} занятиям"]
        if it["ne_provedeno"]:
            parts.append(f"из них в МойКлассе не проведено {it['ne_provedeno']}")
        if it["otmetok_postavit"]:
            parts.append(f"отметок поставить {it['otmetok_postavit']}" + (" — поставлены автоматически" if applied and applied.get("ok") else ""))
        if it["imen_bez_zapisi"]:
            parts.append(f"имён без записи на занятие {it['imen_bez_zapisi']} (отработки/пробные — завести запись)")
        if it["spornyh"] or it["konfliktov"]:
            parts.append(f"спорных {it['spornyh']}, расхождений с CRM {it['konfliktov']}")
        if it["bez_otcheta"]:
            parts.append(f"без отчёта педагога {it['bez_otcheta']} занятий: " + ", ".join(f"{b['time']} {b['class'][:28]}" for b in res["bez_otcheta"][:4]))
        text = "; ".join(parts) + f". Открыть app.kidsup.ru/yavka?day={day} — там кнопки «отметить» по каждому занятию."
        try:
            autopilot.inbox_add(text, who=who, source=f"явка {dd}")
        except Exception:
            log.exception("пункт о явке в Пульт не поставлен")
    return {"day": day, "itogo": it, "applied": applied}
