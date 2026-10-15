# -*- coding: utf-8 -*-
"""«Линия» — кто на телефоне и что ему ответить, одним экраном.

15.10.2026, Борис: живые подсказки админам во время звонка, шаг 1 — без живого
звука. Две администраторши на ресепшене (Манго, доб. 10 и 12) открывают одну
страницу и, пока говорят, видят:
  • кто на линии: дети, возраст, что посещали, записаны ли на 2026/27, статус
    и карточка в МойКлассе — все карточки на этом номере, а не одна;
  • последний контакт с семьёй и открытые обещания — из наших же таблиц;
  • 2–3 свободные группы по возрасту: день, время, места, цена — только живые
    данные (места считаются как на /mesta, цены — из PRICES);
  • кнопки «Дорого», «Подумаю»… — готовый ответ и один следующий шаг; тексты
    в app/liniya_teksty.py, до утверждения владельцем это черновик.

Откуда номер: /liniya?phone=… (ссылки «Линия» на пульте, в обзвоне дня, в досье
и в «Ответах»), поле «последние 4 цифры» (q=1234) или /liniya?ext=12 —
последний закончившийся звонок на добавочном из журнала Манго. Журнал мы
опрашиваем раз в минуту, и в нём только законченные звонки, — поэтому для
идущего разговора есть 4 цифры.

Только чтение: никаких отправок, записей в CRM и запросов наружу.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from . import db
from . import liniya_teksty as teksty

# Живая запись в группе — как на /mesta (app/mesta.py, LIVE).
LIVE = (2, 58132, 83760, 58131)
NE_PRISHEL = 99336
ZAPIS = {2: "учится", 58132: "записан на пробное", 83760: "подтвердил запись",
         58131: "был на пробном", 99336: "не пришёл на пробное", 50509: "новая заявка",
         1: "отчислен", 4: "архив"}
# Семья уже платила: скидка дня первого занятия ей не положена (только новым).
KLIENT = {125955, 125956}
SAD = ("Мини-сад", "Нулевой класс")
PLATNOE_PROBNOE = ("ИЗО-студия", "Шахматы", "Робототехника")
# Направление по началу имени группы: «ПШ_…», «АЯ_…», «РР.Лицей_…».
PREDMETY = (("ПШ", "Подготовка к школе"), ("АЯ", "Английский язык"), ("РР", "Раннее развитие"),
            ("ИЗО", "ИЗО-студия"), ("МА", "Ментальная арифметика"), ("ШАХ", "Шахматы"),
            ("Робот", "Робототехника"), ("Мини-сад", "Мини-сад"), ("Нулевой", "Нулевой класс"),
            ("Скороч", "Скорочтение (техника чтения)"), ("Каллиграф", "Каллиграфия + грамота"))
# Строка прайса, если курс группы в CRM назван не как в PRICES.
PRAJS_PO_PREDMETU = {"Нулевой класс": "Мини-сад"}
PO_DNYAM = {"пн": "понедельникам", "вт": "вторникам", "ср": "средам", "чт": "четвергам",
            "пт": "пятницам", "сб": "субботам", "вс": "воскресеньям"}
# Слово из подсказки по возрасту (main._suggest_by_age) → что искать в группе.
KLYUCHI_PODSKAZKI = (("мини-сад", "мини-сад"), ("нулев", "нулевой"),
                     ("подготовк", "подготовка к школе"), ("английск", "английск"),
                     ("шахмат", "шахмат"), ("изо", "изо"), ("музыка и речь", "музыка и речь"),
                     ("первая школа", "первая школа"), ("лицей", "лицей"),
                     ("скорочт", "скорочт"), ("каллиграф", "каллиграф"),
                     ("менталк", "ментальн"), ("логопед", "логопед"))
CRM_KARTOCHKA = "https://app.moyklass.com/user/{uid}"
TOP = 3              # сколько групп предлагать первыми
GRUPP_MAX = 10       # и сколько всего показывать по возрасту
OBESHCHANIJ_MAX = 15
SPISOK_MAX = 12      # семей по 4 цифрам

_VVOD_OK = re.compile(r"^[\d\s()+\-.]*$")
_PARA = re.compile(r"(пн|вт|ср|чт|пт|сб|вс)\s+(\d{1,2}:\d{2})")
_DIAPAZON = re.compile(r"(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})")
_VOZRAST_RE = re.compile(r"(\d{1,2}(?:[.,]\d)?)\s*[–-]\s*(\d{1,2}(?:[.,]\d)?)")
_OBZVON_KOM = re.compile(r"^\d{1,2}\.\d{2} \d{2}:\d{2}, обзвон")
_PERENOS = re.compile(r"^⏳ с (\d\d\.\d\d):\s*")
_TEG = re.compile(r"<[^>]+>")
# Номер без пробелов, скобок, дефисов и «+» — на сервере users.phone и так
# одиннадцать цифр, но лишняя чистка ничего не стоит.
_CHISTYJ = ("replace(replace(replace(replace(replace(COALESCE(phone,''),' ',''),'-',''),"
            "'(',''),')',''),'+','')")


# --- мелочи ------------------------------------------------------------------

def p10(x) -> str:
    d = "".join(c for c in str(x or "") if c.isdigit())
    return d[-10:] if len(d) >= 10 else ""


def pokaz(p: str) -> str:
    """9990001234 → «+7 999 000-12-34»."""
    return f"+7 {p[:3]} {p[3:6]}-{p[6:8]}-{p[8:]}" if len(p) == 10 else p


def razobrat_vvod(s) -> tuple[str, str]:
    """Что ввели в поле: ("4", "1234"), ("tel", "9990001234"), ("", "") — пусто,
    ("", "oshibka") — не цифры или цифр не 4 и не 10+."""
    s = str(s or "").strip()
    if not s:
        return "", ""
    d = "".join(c for c in s if c.isdigit())
    if not _VVOD_OK.match(s):
        return "", "oshibka"
    if len(d) == 4:
        return "4", d
    if len(d) >= 10:
        return "tel", d[-10:]
    return "", "oshibka"


def vozrast(x) -> float | None:
    """«5», «5,5», «4.5 года» → число лет; чепуха — None."""
    m = re.search(r"\d{1,2}(?:[.,]\d{1,2})?", str(x or ""))
    if not m:
        return None
    v = float(m.group(0).replace(",", "."))
    return v if 0.5 <= v <= 17 else None


def _chislo(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def _let(v: float) -> str:
    if v != int(v):
        return "года"
    n = int(v)
    if n % 10 == 1 and n % 100 != 11:
        return "год"
    return "года" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "лет"


def _rub(n) -> str:
    return f"{int(n):,}".replace(",", " ") + " ₽"


def _kogda_ts(ts) -> str:
    """«2026-10-15T13:55:00+03:00» → «15.10 13:55»."""
    s = str(ts or "")
    return f"{s[8:10]}.{s[5:7]} {s[11:16]}".strip() if len(s) >= 16 else s[:16]


def _data(s) -> str:
    s = str(s or "")
    return f"{s[8:10]}.{s[5:7]}.{s[:4]}" if len(s) >= 10 else s


def _dlit(secs: int) -> str:
    secs = int(secs or 0)
    return f"{secs // 60} мин {secs % 60:02d} с" if secs >= 60 else f"{secs} с"


def _sklon(n: int, one: str, few: str, many: str) -> str:
    t = n % 100
    if 11 <= t <= 14:
        return f"{n} {many}"
    return f"{n} {one}" if n % 10 == 1 else (f"{n} {few}" if 2 <= n % 10 <= 4 else f"{n} {many}")


def _kolonki(conn, tablica: str) -> set:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({tablica})")}
    except Exception:
        return set()


def _segodnya() -> date:
    from . import autopilot
    return autopilot._today()


def _kto_po_ext() -> dict:
    """Добавочный → имя админа: настройка mango_ext_admins, иначе раскладка
    по умолчанию (app/callmark.py)."""
    try:
        from .callmark import _who
        return dict(_who())
    except Exception:
        return {}


def dobavochnye() -> list[dict]:
    """Добавочные ресепшена для ссылок «последний звонок» — без чужих и партнёрских."""
    try:
        from .mango import FOREIGN_EXTS, PARTNER_EXTS
        chuzhie = set(FOREIGN_EXTS) | set(PARTNER_EXTS)
    except Exception:
        chuzhie = {"20", "21"}
    kto = _kto_po_ext()
    exts = sorted((e for e in kto if e.isdigit() and e not in chuzhie), key=lambda e: (len(e), e))
    return [{"ext": e, "kto": kto.get(e, "")} for e in (exts or ["10", "12"])]


# --- группы ------------------------------------------------------------------

def _predmet(short: str) -> str:
    for pref, title in PREDMETY:
        if short.startswith(pref):
            return title
    return short.split("_")[0]


def _uroven(short: str) -> str:
    """Уровень или программа из имени: «ПШ1 нечитающие», «Pre-A1 Starters», «Живопись»."""
    from .main import _DAY_RE, _TIME_RE
    out = []
    for chast in short.split("_")[1:]:
        s = re.sub(r"\(\s*гр\.?\s*\d+\s*\)", " ", chast, flags=re.I)
        s = re.sub(r"Группа\s*\d+", " ", s, flags=re.I).strip()
        ostatok = _TIME_RE.sub(" ", _DAY_RE.sub(" ", s.lower()))
        ostatok = re.sub(r"[\d,.\s+\-–]|лет|года?", "", ostatok)
        if not ostatok:
            continue                       # день, время или возраст
        s = re.sub(r"^\d{1,2}(?:[.,]\d)?\s*[-–]\s*\d{1,2}(?:[.,]\d)?\s*(?:лет|года?)?\s*", "", s)
        s = s.strip(" _-")
        if s and s not in out:
            out.append(s)
    return ", ".join(out)


def _vozrast_po_opisaniyu(predmet: str) -> tuple[float | None, float | None]:
    """У мини-сада и нулевого класса возраста в имени группы нет — берём из
    описаний занятий (app/descriptions.py: «2–4 года», «5–7 лет»)."""
    try:
        from .descriptions import COURSES
    except Exception:
        return None, None
    for c in COURSES:
        if c.get("course") == predmet:
            m = _VOZRAST_RE.search(c.get("age") or "")
            if m:
                return tuple(float(x.replace(",", ".")) for x in m.groups())
    return None, None


def _kogda(short: str, sad: bool) -> tuple[str, str]:
    """Когда идёт группа: для экрана («Вт и Пт, 17:00») и для речи
    («по вторникам и пятницам в 17:00»)."""
    from .main import DAY_LABEL, _TIME_RE, _name_days
    pary = _PARA.findall(short.lower())
    if len(pary) >= 2:
        return (" + ".join(f"{DAY_LABEL[d]} {t}" for d, t in pary),
                " и ".join(f"по {PO_DNYAM[d]} в {t}" for d, t in pary))
    dni = _name_days(short)
    dia = _DIAPAZON.search(short)
    vremya = _TIME_RE.findall(short)
    if dia:
        t_ekran, t_rech = f"{dia.group(1)}–{dia.group(2)}", f"с {dia.group(1)} до {dia.group(2)}"
    elif vremya:
        t_ekran, t_rech = vremya[0], f"в {vremya[0]}"
    else:
        t_ekran = t_rech = ""
    if dni:
        ekran = " и ".join(DAY_LABEL[d] for d in dni) + (f", {t_ekran}" if t_ekran else "")
        rech = "по " + " и ".join(PO_DNYAM[d] for d in dni) + (f" {t_rech}" if t_rech else "")
    elif sad:
        ekran, rech = f"будни, {t_ekran}".strip(", "), f"по будням {t_rech}".strip()
    else:
        ekran, rech = t_ekran, t_rech
    return ekran, rech


def _stroki_programmy(pr: dict, name: str, predmet: str) -> list:
    """Строки прайса той же программы, что выбирает main._price_line (у мини-сада —
    без строк нулевого класса)."""
    lines = pr["lines"]
    for key in ("Нулевой", "Первая школа", "Лицей", "Музыка и речь"):
        if key in (name or ""):
            return [l for l in lines if key in l[0]] or lines
    if predmet == "Мини-сад":
        return [l for l in lines if l[0].startswith("Мини-сад")] or lines
    return lines


def _cena_tekst(label: str, cena) -> str:
    """«8 400 ₽ за 8 занятий (2 р/нед)» — подпись строки прайса без названия программы."""
    lab = re.sub(r"^(Мини-сад|Нулевой класс),\s*", "", label or "").split(" · ")[-1].strip()
    return f"{_rub(cena)} за {lab}" if lab else _rub(cena)


def _probnoe_platnoe(pr: dict | None) -> str:
    for nazv, _staraya, nov in (pr or {}).get("lines", []):
        if "робн" in nazv:
            return _rub(nov)
    return ""


def vse_gruppy() -> list[dict]:
    """Группы сезона со свободными местами — тот же счёт, что на /mesta
    (mesta.rows: живые записи, нормы CAP_OVERRIDE, пометки MERGE/WAITLIST/HOLD),
    плюс направление, возраст, когда идёт и цена по прайсу."""
    from . import mesta
    from .main import COURSE_ALIAS, PRICES, _group_ages, _group_klass, _price_line
    rows = mesta.rows()
    with db.get_conn() as conn:
        info = {r[0]: (r[1] or "", r[2] or "") for r in conn.execute(
            "SELECT cl.id, cl.name, co.name FROM classes cl "
            "LEFT JOIN courses co ON co.id = cl.course_id WHERE cl.name LIKE '2627%'")}
    out = []
    for r in rows:
        polnoe, kurs = info.get(r["id"], ("2627_" + r["name"], ""))
        short = r["name"]
        predmet = _predmet(short)
        sad = predmet in SAD
        kurs = COURSE_ALIAS.get(kurs, kurs)
        kljuch = kurs if kurs in PRICES else (predmet if predmet in PRICES
                                               else PRAJS_PO_PREDMETU.get(predmet))
        pr = PRICES.get(kljuch) if kljuch else None
        lo, hi = _group_ages(polnoe)
        k_lo, k_hi = _group_klass(polnoe)
        if lo is None:
            lo, hi = _vozrast_po_opisaniyu(predmet)
        if k_lo:
            vozr = f"{k_lo}–{k_hi} класс" if k_hi != k_lo else f"{k_lo} класс"
        elif lo is not None:
            vozr = (f"{_chislo(lo)}–{_chislo(hi)} {_let(hi)}" if hi != lo
                    else f"{_chislo(lo)} {_let(lo)}")
        else:
            vozr = ""
        programma = uroven = ""
        cena = cena_menshe = raz_v_nedelyu = ""
        if pr:
            glav = _price_line(pr, polnoe)
            cena = _cena_tekst(glav[0], glav[2])
            if " · " in glav[0]:
                programma = glav[0].split(" · ")[0].strip()
            drugie = [l for l in _stroki_programmy(pr, polnoe, predmet)
                      if l is not glav and l[2] < glav[2]
                      and not re.search(r"разов|робн", l[0], re.I)]
            if drugie:
                m = min(drugie, key=lambda l: l[2])
                cena_menshe = _cena_tekst(m[0], m[2])
                if "1 р/нед" in m[0]:
                    raz_v_nedelyu = cena_menshe
        uroven = _uroven(short)
        if programma and programma.lower() in uroven.lower():
            programma = ""
        ekran, rech = _kogda(short, sad)
        free = max(0, int(r["free"] or 0))
        zametki = []
        if r.get("wait"):
            zametki.append("полная / лист: " + r["wait"])
        probnoe = _probnoe_platnoe(pr) if predmet in PLATNOE_PROBNOE else ""
        if probnoe:
            zametki.append(f"пробное платное — {probnoe}, в зачёт абонемента")
        opisanie = ", ".join(x for x in (predmet, programma, uroven, vozr) if x)
        out.append({
            "id": r["id"], "name": short, "predmet": predmet, "programma": programma,
            "uroven": uroven, "vozrast": vozr, "lo": lo, "hi": hi, "sad": sad,
            "kogda": ekran, "kogda_rech": rech, "free": free,
            "free_text": ("по дням — сверить в CRM" if predmet == "Мини-сад"
                          else _sklon(free, "место", "места", "мест")),
            "cap": r["cap"], "live": r["live"],
            "cena": cena, "cena_menshe": cena_menshe, "raz_v_nedelyu": raz_v_nedelyu,
            "probnoe": probnoe, "wait": r.get("wait") or "", "merge": r.get("merge") or "",
            "hold": r.get("hold") or "", "zametka": "; ".join(zametki),
            "opisanie": opisanie,
            "vid_pervogo": "sad" if sad else ("platnoe" if probnoe else "obychnoe"),
        })
    return out


def _rangi(age: float) -> list[str]:
    """Порядок направлений по возрасту — из той же матрицы, что /brief и /callplan
    (main._suggest_by_age): ключевые слова в порядке предложения."""
    from .main import _suggest_by_age
    out = []
    for slovo in re.split(r"\s*[·,]\s*", _suggest_by_age(age).lower()):
        for k, iskat in KLYUCHI_PODSKAZKI:
            if k in slovo and iskat not in out:
                out.append(iskat)
    return out


def podobrat(age: float | None, vse: list[dict]) -> tuple[list[dict], list[dict]]:
    """2–3 группы «предложить первыми» (по одной на направление, в порядке
    методички, где мест больше) и остальные подходящие по возрасту.
    «Не записывать» и «пауза» не показываем вовсе, «полная / лист» — только
    во втором списке, со своей пометкой."""
    if age is None:
        return [], []
    rangi = _rangi(age)

    def rang(g):
        stroka = f"{g['predmet']} {g['programma']} {g['uroven']} {g['name']}".lower()
        return next((i for i, k in enumerate(rangi) if k in stroka), len(rangi))

    fit = [g for g in vse
           if g["lo"] is not None and g["lo"] - 0.5 <= age <= g["hi"] + 0.5
           and g["free"] > 0 and not g["merge"] and not g["hold"]]
    fit.sort(key=lambda g: (rang(g), bool(g["wait"]), -g["free"], g["kogda"]))
    top, bylo = [], set()
    for g in fit:
        if g["wait"] or g["predmet"] in bylo:
            continue
        top.append(g)
        bylo.add(g["predmet"])
        if len(top) == TOP:
            break
    eshche = [g for g in fit if g not in top][:max(0, GRUPP_MAX - len(top))]
    return top, eshche


# --- семья на номере -----------------------------------------------------------

def _statusy(conn) -> dict:
    from .main import CLIENT_STATE_NAMES
    from .svidetelstva import STATUS
    out = dict(STATUS)
    out.update(CLIENT_STATE_NAMES)
    try:
        for r in conn.execute("SELECT id, name FROM client_statuses"):
            if r[1]:
                out[r[0]] = r[1]
    except Exception:
        pass
    return out


def _roditel(raw) -> str:
    try:
        j = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return ""
    for a in j.get("attributes") or []:
        if str(a.get("attributeAlias") or "").startswith("parent") and a.get("value"):
            return str(a["value"])[:80]
    return ""


def _karty(conn, p: str) -> tuple[list, bool]:
    """Все карточки на номере (братья и сёстры — на одном телефоне). Нет ни одной —
    ищем номер в карточке как второй (у части семей он только в raw)."""
    rows = conn.execute(
        f"SELECT id, name, phone, client_state_id, raw FROM users "
        f"WHERE substr({_CHISTYJ},-10)=? ORDER BY id", (p,)).fetchall()
    if rows:
        return rows, False
    rows = conn.execute("SELECT id, name, phone, client_state_id, raw FROM users "
                        "WHERE raw LIKE ? ORDER BY id LIMIT 5", (f"%{p}%",)).fetchall()
    return rows, bool(rows)


def _rebenok(conn, r, statusy: dict, segodnya: str) -> dict:
    from .autopilot import _child_name
    from .main import _age_from_raw, _brief_history
    uid = r["id"]
    age = _age_from_raw(r["raw"])
    gruppy, bufer, ne_prishel = [], [], []
    for z in conn.execute(
            "SELECT j.status_id, cl.name FROM joins j JOIN classes cl ON cl.id = j.class_id "
            "WHERE j.user_id=? AND cl.name LIKE '2627%' ORDER BY j.id", (uid,)).fetchall():
        n = (z["name"] or "").replace("2627_", "", 1)
        if "аявк" in (z["name"] or ""):
            if z["status_id"] not in (1, 4):
                bufer.append(n)
        elif z["status_id"] in LIVE:
            gruppy.append({"gruppa": n, "status": ZAPIS.get(z["status_id"], "")})
        elif z["status_id"] == NE_PRISHEL:
            ne_prishel.append(n)
    sled = None
    try:
        s = conn.execute(
            "SELECT l.date, l.begin_time, cl.name, lr.raw FROM lesson_records lr "
            "JOIN lessons l ON l.id = lr.lesson_id LEFT JOIN classes cl ON cl.id = l.class_id "
            "WHERE lr.user_id=? AND l.date>=? ORDER BY l.date, l.begin_time LIMIT 1",
            (uid, segodnya)).fetchone()
        if s:
            try:
                test = bool(json.loads(s["raw"] or "{}").get("test"))
            except (ValueError, TypeError):
                test = False
            sled = {"kogda": f"{_data(s['date'])[:5]} {(s['begin_time'] or '')[:5]}".strip(),
                    "gruppa": (s["name"] or "").replace("2627_", "", 1), "probnoe": test}
    except Exception:
        sled = None
    st = r["client_state_id"]
    from .hvost import STOP_STATUSY
    return {
        "id": uid, "imya": r["name"] or "без имени", "imya_rech": _child_name(r["name"] or "") or "",
        "vozrast": age, "vozrast_text": f"{_chislo(age)} {_let(age)}" if age is not None else "",
        "status": statusy.get(st, str(st) if st else "—"), "status_id": st,
        "stop": st in STOP_STATUSY, "klient": st in KLIENT,
        "zapisan": bool(gruppy), "gruppy": gruppy, "bufer": bufer, "ne_prishel": ne_prishel,
        "sled": sled, "istoriya": [{"kurs": h["course"] or "—", "poslednee": _data(h["last"]),
                                    "raz": h["visits"]} for h in _brief_history(conn, uid)],
        "crm": CRM_KARTOCHKA.format(uid=uid),
    }


def _zvonok(r, kto: dict) -> dict:
    vh = not str(r["direction"] or "").startswith(("out", "исх"))
    st = str(r["state"] or "")
    secs = int(r["secs"] or 0) if "secs" in r.keys() else 0
    if st == "talked":
        itog = "поговорили" + (f" {_dlit(secs)}" if secs else "")
    elif st == "short":
        itog = "короткий разговор" + (f", {_dlit(secs)}" if secs else "")
    elif st == "missed":
        itog = "пропущенный" if vh else "не дозвонились"
    else:
        itog = st or "звонок"
    ext = str(r["ext"] or "") if "ext" in r.keys() else ""
    nomer = p10(r["phone"]) if "phone" in r.keys() else ""
    return {"ts": r["ts"], "kogda": _kogda_ts(r["ts"]), "napr": "входящий" if vh else "исходящий",
            "itog": itog, "otvechen": st in ("talked", "short"), "ext": ext,
            "kto": kto.get(ext, "") if ext else "", "p10": nomer, "pokaz": pokaz(nomer)}


def _kontakt(conn, p: str, uids: list, segodnya: date) -> dict:
    """Последний контакт: факт звонка (журнал Манго, минуты), итог разговора
    (разбор звонков в карточке, через 1–2 часа), итог кнопкой обзвона (сразу),
    последний комментарий админа и последнее сообщение."""
    from .voronka import MANAGERS, _CALL_LOG, _robot
    out = {"zvonki": [], "za_7_dnej": 0, "razbor": None, "obzvon": None, "otmetka": None,
           "chelovek": None, "soobshchenie": None}
    kto = _kto_po_ext()
    cols = _kolonki(conn, "mango_calls")
    if cols:
        sel = ("ts, phone, direction, state" + (", COALESCE(secs,0) AS secs" if "secs" in cols else "")
               + (", ext" if "ext" in cols else ""))
        try:
            out["zvonki"] = [_zvonok(r, kto) for r in conn.execute(
                f"SELECT {sel} FROM mango_calls WHERE substr(phone,-10)=? ORDER BY ts DESC LIMIT 5",
                (p,)).fetchall()]
            out["za_7_dnej"] = conn.execute(
                "SELECT COUNT(*) FROM mango_calls WHERE substr(phone,-10)=? AND ts>=?",
                (p, (segodnya - timedelta(days=7)).isoformat())).fetchone()[0]
        except Exception:
            pass
    q = ",".join("?" * len(uids))
    if uids:
        try:
            for r in conn.execute(f"SELECT ts, manager_id, text FROM crm_comments "
                                  f"WHERE user_id IN ({q}) ORDER BY ts DESC LIMIT 40", uids).fetchall():
                t = str(r["text"] or "").strip()
                t0 = t.lstrip("📞").strip()
                zapis = {"kogda": _kogda_ts(r["ts"]), "tekst": t0[:420]}
                if _OBZVON_KOM.match(t0):
                    out["obzvon"] = out["obzvon"] or {**zapis, "otkuda": "комментарий обзвона"}
                elif t.startswith("📞") or _CALL_LOG.match(t0):
                    out["razbor"] = out["razbor"] or zapis
                elif not out["chelovek"] and r["manager_id"] in MANAGERS and not _robot(t):
                    out["chelovek"] = {**zapis, "kto": MANAGERS[r["manager_id"]]}
        except Exception:
            pass
        try:
            m = conn.execute(f"SELECT marked_at, outcome FROM callplan_marks WHERE user_id IN ({q}) "
                             f"ORDER BY marked_at DESC LIMIT 1", uids).fetchone()
            if m:
                out["otmetka"] = {"kogda": _kogda_ts(m["marked_at"]), "tekst": m["outcome"] or ""}
        except Exception:
            pass
    try:
        from .obzvon_dnya import ITOGI
        o = conn.execute("SELECT o.itog, o.note, o.who, o.ts FROM obzvon_dnya o "
                         "JOIN plan_inbox p ON p.id = o.item_id "
                         "WHERE length(p.phone)>=10 AND substr(p.phone,-10)=? "
                         "ORDER BY o.ts DESC LIMIT 1", (p,)).fetchone()
        if o:
            # кнопка обзвона сразу, комментарий в карточке — через 20 минут:
            # показываем то, что свежее
            out["obzvon"] = {"kogda": _kogda_ts(o["ts"]), "otkuda": f"обзвон дня ({o['who'] or 'админ'})",
                             "tekst": ITOGI.get(o["itog"], o["itog"] or "")
                             + (f" — {o['note']}" if o["note"] else "")}
    except Exception:
        pass
    try:
        from .voronka import _last_msg
        m = _last_msg(conn, p)
        if m:
            out["soobshchenie"] = {"kogda": _kogda_ts(m.get("ts")) or m["когда"], "kto": m["кто"],
                                   "tekst": m["текст"]}
    except Exception:
        pass
    out["est"] = any(out[k] for k in ("zvonki", "razbor", "obzvon", "otmetka", "chelovek",
                                      "soobshchenie"))
    return out


def _vid_punkta(source: str) -> str:
    s = (source or "").strip().lower()
    if s.startswith("звонок"):
        return "обещали в разговоре"
    if s.startswith("возврат"):
        return "порция обзвона"
    if s.startswith(("наряд", "автоматика")):
        return "сигнал без ответа"
    if "оплат" in s or "сверка" in s:
        return "деньги"
    return (source or "пункт пульта")[:30]


def _obeshchaniya(conn, p: str) -> list[dict]:
    """Открытые пункты пульта про эту семью: номер в поле телефона или в тексте
    (у трети пунктов поле пустое, номер — внутри текста)."""
    from .hvost import OBYAZATELSTVO
    from .svidetelstva import _telefony
    cols = _kolonki(conn, "plan_inbox")
    if not cols:
        return []
    sel = "id, day, ts, who, text, phone, source" + (", tries" if "tries" in cols else "")
    out = []
    for r in conn.execute(f"SELECT {sel} FROM plan_inbox WHERE done=0 "
                          f"ORDER BY day DESC, id DESC").fetchall():
        tekst = r["text"] or ""
        if p10(r["phone"]) != p and p not in _telefony(tekst):
            continue
        m = _PERENOS.match(tekst)
        out.append({"id": r["id"], "kto": r["who"] or "", "den": _data(r["day"])[:5],
                    "s": m.group(1) if m else "", "tekst": tekst[m.end():] if m else tekst,
                    "vid": _vid_punkta(r["source"]),
                    "obyazatelstvo": bool(OBYAZATELSTVO.search(tekst)),
                    "popytok": int(r["tries"] or 0) if "tries" in cols else 0})
        if len(out) >= OBESHCHANIJ_MAX:
            break
    return out


def _zadachi(conn, p: str, den: str) -> list[dict]:
    """Задачи смены на сегодня, где упомянута семья (номер — в тексте задачи)."""
    from .svidetelstva import _telefony
    if not _kolonki(conn, "pult_tasks"):
        return []
    out = []
    for r in conn.execute("SELECT who, t, text FROM pult_tasks WHERE day=? AND done=0 "
                          "ORDER BY t, id", (den,)).fetchall():
        tekst = _TEG.sub(" ", r["text"] or "")
        if p in _telefony(tekst):
            out.append({"kto": r["who"] or "", "t": r["t"] or "",
                        "tekst": re.sub(r"\s+", " ", tekst).strip()[:240]})
    return out[:5]


def _oplata(conn, uids: list) -> dict | None:
    if not uids:
        return None
    q = ",".join("?" * len(uids))
    try:
        r = conn.execute(f"SELECT date, summa FROM payments WHERE user_id IN ({q}) AND summa>0 "
                         f"ORDER BY date DESC LIMIT 1", uids).fetchone()
    except Exception:
        return None
    return {"kogda": _data(r["date"]), "summa": _rub(r["summa"])} if r else None


# --- ответы ------------------------------------------------------------------

def _obshchij_kontekst(imya: str, novaya: bool) -> dict:
    from . import autopilot, content
    from .main import PRICES
    ceny = []
    for nazv, kursy in (("ИЗО и шахматы", ("ИЗО-студия", "Шахматы")), ("робототехника", ("Робототехника",))):
        c = {_probnoe_platnoe(PRICES.get(k)) for k in kursy} - {""}
        if len(c) == 1:
            ceny.append(f"{nazv} — {c.pop()}")
    return {
        "imya_rebenka": imya or "ребёнок", "adres": content.ADDRESS, "metro": content.METRO,
        "skidka_pervyj": autopilot.SKIDKA_PERVYJ, "skidka_sad": autopilot.SKIDKA_SAD,
        "skidka_vtoroj": autopilot.SKIDKA_VTOROJ, "probnoe_ceny": ", ".join(ceny) or "по прайсу",
        "skidka": f"{autopilot.SKIDKA_PERVYJ}%" if novaya else "",
        "gruppa": "", "vremya": "", "cena": "", "raz_v_nedelyu": "",
        # без группы: общий вид меньшего шага; у конкретной группы — строка прайса или пусто
        "cena_menshe": "раз в неделю, а в мини-саду — два дня в неделю",
        "cena_probnogo": "", "vid_pervogo": "obychnoe",
    }


def _kontekst_gruppy(obshchij: dict, g: dict, novaya: bool) -> dict:
    from . import autopilot
    ctx = dict(obshchij)
    ctx.update({
        "gruppa": g["opisanie"], "vremya": g["kogda_rech"], "cena": g["cena"],
        "cena_menshe": g["cena_menshe"], "raz_v_nedelyu": g["raz_v_nedelyu"],
        "cena_probnogo": g["probnoe"] or "по прайсу", "vid_pervogo": g["vid_pervogo"],
        "skidka": (f"{autopilot.SKIDKA_SAD if g['sad'] else autopilot.SKIDKA_PERVYJ}%"
                   if novaya else ""),
    })
    return ctx


def sobrat_otvety(imya: str, novaya: bool, gruppy: list[dict], novyj: bool = False) -> dict:
    """Ответы кнопок: общий набор («_bez» — группа не выбрана) и для каждой группы
    только те, что от неё зависят. Заполняет сервер — на странице JS лишь показывает."""
    klyuchi = (("novyj_kontakt",) if novyj else ()) + teksty.KNOPKI
    obshchij = _obshchij_kontekst(imya, novaya)
    baza = teksty.otvety(obshchij, klyuchi)
    out = {"_bez": baza}
    for g in gruppy:
        svoi = teksty.otvety(_kontekst_gruppy(obshchij, g, novaya), klyuchi)
        out[str(g["id"])] = {k: v for k, v in svoi.items() if v != baza[k]}
    return out


# --- поиск -------------------------------------------------------------------

def po_4_cifram(conn, chetyre: str, segodnya: date) -> list[dict]:
    """Семьи, у которых номер кончается на эти 4 цифры, и номера не из CRM,
    которые звонили или писали за последние два дня. Свежий контакт — выше."""
    sem: dict[str, dict] = {}
    statusy = _statusy(conn)
    for r in conn.execute(f"SELECT id, name, phone, client_state_id FROM users "
                          f"WHERE substr({_CHISTYJ},-4)=? ORDER BY id LIMIT 200", (chetyre,)).fetchall():
        p = p10(r["phone"])
        if not p:
            continue
        s = sem.setdefault(p, {"p10": p, "pokaz": pokaz(p), "imena": [], "statusy": [],
                               "v_crm": True, "kontakt": "", "ts": ""})
        s["imena"].append(r["name"] or "без имени")
        st = statusy.get(r["client_state_id"])
        if st and st not in s["statusy"]:
            s["statusy"].append(st)
    s_dnya = (segodnya - timedelta(days=2)).isoformat()
    for tablica, chto in (("mango_calls", "звонил"), ("wazzup_inbox", "писал")):
        if not _kolonki(conn, tablica):
            continue
        try:
            for ph, ts in conn.execute(
                    f"SELECT substr(phone,-10), MAX(ts) FROM {tablica} "
                    f"WHERE substr(phone,-4)=? AND ts>=? GROUP BY substr(phone,-10)",
                    (chetyre, s_dnya)).fetchall():
                p = p10(ph)
                if not p:
                    continue
                s = sem.setdefault(p, {"p10": p, "pokaz": pokaz(p), "imena": [], "statusy": [],
                                       "v_crm": False, "kontakt": "", "ts": ""})
                if str(ts) > s["ts"]:
                    s["ts"], s["kontakt"] = str(ts), f"{chto} {_kogda_ts(ts)}"
        except Exception:
            continue
    out = sorted(sem.values(), key=lambda s: ", ".join(s["imena"]))
    out.sort(key=lambda s: s["ts"], reverse=True)       # без свежего контакта — в конце
    return out


def po_dobavochnomu(conn, ext: str, segodnya: date) -> dict:
    """Последний законченный звонок на добавочном за сегодня из журнала Манго.
    Разговор предпочитаем недозвону: входящий обходит добавочные по очереди, и
    «пропущенный» на доб. 12 часто значит лишь, что трубку взяли на доб. 10."""
    kto = _kto_po_ext()
    res = {"ext": ext, "kto": kto.get(ext, ""), "est_kolonka": False, "zvonki": [],
           "glavnyj": None, "zhurnal_do": ""}
    cols = _kolonki(conn, "mango_calls")
    if not cols:
        return res
    try:
        posl = conn.execute("SELECT MAX(ts) FROM mango_calls").fetchone()[0]
        res["zhurnal_do"] = _kogda_ts(posl) if posl else ""
    except Exception:
        pass
    if "ext" not in cols:
        return res
    res["est_kolonka"] = True
    sel = "ts, phone, direction, state, ext" + (", COALESCE(secs,0) AS secs" if "secs" in cols else "")
    rows = conn.execute(f"SELECT {sel} FROM mango_calls WHERE ext=? AND ts>=? "
                        f"ORDER BY ts DESC LIMIT 8", (ext, segodnya.isoformat())).fetchall()
    zv = [_zvonok(r, kto) for r in rows]
    res["zvonki"] = zv
    res["glavnyj"] = next((z for z in zv if z["otvechen"]), zv[0] if zv else None)
    return res


def dobavochnyj_kratko(ext: str) -> dict:
    """Для опроса со страницы: какой звонок на добавочном сейчас последний."""
    ext = ext if re.fullmatch(r"\d{1,4}", str(ext or "")) else ""
    if not ext:
        return {"ext": "", "ts": ""}
    with db.get_conn() as conn:
        g = po_dobavochnomu(conn, ext, _segodnya())["glavnyj"]
    return {"ext": ext, "ts": g["ts"] if g else "", "kogda": g["kogda"] if g else ""}


# --- всё вместе ----------------------------------------------------------------

def js_json(obj) -> str:
    """JSON для <script> на странице: кириллица как есть (tojson превращает каждую
    букву в \\uXXXX — страница с ответами тяжелеет вшестеро), а символы, которыми
    можно закрыть тег или сломать строку JS, экранированы, как в tojson."""
    s = json.dumps(obj, ensure_ascii=False)
    for a, b in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"), ("'", "\\u0027"),
                 (chr(0x2028), "\\u2028"), (chr(0x2029), "\\u2029")):
        s = s.replace(a, b)
    return s


def _sinhro() -> str:
    s = db.get_state("last_light_sync") or db.get_state("last_sync") or ""
    return f"{s[8:10]}.{s[5:7]} {s[11:16]}" if len(s) >= 16 else s


def sobrat(phone: str = "", q: str = "", ext: str = "", age: str = "", child=0) -> dict:
    """Всё для страницы и для /api/liniya. phone — номер; q — последние 4 цифры
    (или номер целиком); ext — добавочный; age — возраст, если в карточке нет;
    child — карточка, под которую подбирать группы, когда детей несколько."""
    try:
        child = int(str(child or "0").strip())
    except ValueError:
        child = 0
    segodnya = _segodnya()
    d = {"rezhim": "pusto", "oshibka": "", "chernovik": teksty.CHERNOVIK,
         "vvod": {"q": str(q or "")[:20], "phone": "", "ext": "", "age": str(age or "")[:8]},
         "dobavochnye": dobavochnye(), "sinhro": _sinhro(),
         "knopki": [{"key": k, "label": teksty.OTVETY[k]["knopka"]} for k in teksty.KNOPKI]}
    p = ""
    vid, znach = razobrat_vvod(q)
    if znach == "oshibka":
        d["oshibka"] = "Нужны последние 4 цифры номера или номер целиком — только цифры."
    elif vid == "tel":
        p = znach
    if phone:
        vid_t, znach_t = razobrat_vvod(phone)
        if vid_t == "tel":
            p, vid = znach_t, ""
        elif vid_t == "4":
            vid, znach = vid_t, znach_t
        else:
            d["oshibka"] = "В номере меньше 10 цифр — проверьте номер или введите последние 4 цифры."
    ext = str(ext or "").strip()
    with db.get_conn() as conn:
        if ext and not p:
            if not re.fullmatch(r"\d{1,4}", ext):
                d["oshibka"] = "Добавочный — это 2–3 цифры, например 10 или 12."
            else:
                d["rezhim"] = "ext"
                d["vvod"]["ext"] = ext
                info = po_dobavochnomu(conn, ext, segodnya)
                d["ext"] = info
                if info["glavnyj"] and info["glavnyj"]["p10"]:
                    p = info["glavnyj"]["p10"]
        if vid == "4" and not p:
            spisok = po_4_cifram(conn, znach, segodnya)
            d["vvod"]["q"] = znach
            if len(spisok) == 1:
                p = spisok[0]["p10"]
                d["nashli_po"] = znach
            else:
                d["rezhim"] = "spisok"
                d["spisok"] = spisok[:SPISOK_MAX]
                d["spisok_eshche"] = max(0, len(spisok) - SPISOK_MAX)
        if p:
            d["rezhim"] = "ext" if d["rezhim"] == "ext" else "telefon"
            d["vvod"]["phone"] = p
            d.update(_semya(conn, p, segodnya, age, child))
    if d["rezhim"] == "spisok" and not d["spisok"]:
        # никого с такими цифрами: это новый контакт — скрипт и группы по возрасту
        d.update(_novyj(age))
    if "otvety" not in d:
        # семьи на экране нет — кнопки всё равно отвечают, общими словами
        d["otvety"] = sobrat_otvety("", True, [])
    d["js"] = js_json({"otvety": d.get("otvety") or {}, "tek": d.get("tek") or "_bez",
                       "nachalo": d.get("nachalo") or "", "ext": (d.get("ext") or {}).get("ext", ""),
                       "ext_ts": ((d.get("ext") or {}).get("glavnyj") or {}).get("ts", "")})
    return d


def _gruppy_dlya(age_v: float | None) -> dict:
    vse = vse_gruppy() if age_v is not None else []
    top, eshche = podobrat(age_v, vse)
    pochemu = ""
    if age_v is None:
        pochemu = "Возраст неизвестен — введите его, и появятся группы."
    elif not top and not eshche:
        pochemu = "Свободных групп под этот возраст сейчас нет — предложите лист ожидания."
    return {"top": top, "eshche": eshche, "pochemu_pusto": pochemu}


def _novyj(age) -> dict:
    """Номера нет в CRM: скрипт нового контакта и группы по введённому возрасту."""
    age_v = vozrast(age)
    gr = _gruppy_dlya(age_v)
    vse = gr["top"] + gr["eshche"]
    return {"v_crm": False, "deti": [], "vozrast": age_v,
            "vozrast_text": f"{_chislo(age_v)} {_let(age_v)}" if age_v is not None else "",
            "vozrast_otkuda": "введён" if age_v is not None else "", "novaya_semya": True,
            "gruppy": gr, "otvety": sobrat_otvety("", True, vse, novyj=True),
            "tek": str(gr["top"][0]["id"]) if gr["top"] else "_bez", "nachalo": "novyj_kontakt",
            "knopki_novyj": True}


def _semya(conn, p: str, segodnya: date, age, child) -> dict:
    rows, vtoroj = _karty(conn, p)
    res = {"telefon": {"p10": p, "pokaz": pokaz(p), "tel": "7" + p},
           "kontakt": _kontakt(conn, p, [r["id"] for r in rows], segodnya),
           "obeshchaniya": _obeshchaniya(conn, p),
           "zadachi": _zadachi(conn, p, segodnya.isoformat())}
    if not rows:
        res.update(_novyj(age))
        return res
    statusy = _statusy(conn)
    deti = [_rebenok(conn, r, statusy, segodnya.isoformat()) for r in rows]
    uids = [r["id"] for r in rows]
    oplata = _oplata(conn, uids)
    novaya = not oplata and not any(x["klient"] for x in deti)
    fokus = next((x for x in deti if x["id"] == child), None) \
        or next((x for x in deti if x["vozrast"] is not None), None) or deti[0]
    age_v = vozrast(age)
    otkuda = "введён" if age_v is not None else ("карточка" if fokus["vozrast"] is not None else "")
    if age_v is None:
        age_v = fokus["vozrast"]
    gr = _gruppy_dlya(age_v)
    roditel = next((x for x in (_roditel(r["raw"]) for r in rows) if x), "")
    stop = [x for x in deti if x["stop"]]
    from . import autopilot
    if novaya:
        podskazka = (f"Семья ещё не платила: в день первого занятия — минус {autopilot.SKIDKA_PERVYJ}% "
                     f"на первый абонемент (мини-сад и нулевой класс — минус {autopilot.SKIDKA_SAD}%).")
    else:
        podskazka = (f"Семья уже платила: скидка дня первого занятия не действует; второй предмет "
                     f"или второй ребёнок — минус {autopilot.SKIDKA_VTOROJ}%. Скидки не суммируются.")
    res.update({
        "v_crm": True, "vtoroj_nomer": vtoroj, "deti": deti, "fokus": fokus["id"],
        "fokus_imya": fokus["imya"], "roditel": roditel, "oplata": oplata,
        "novaya_semya": novaya, "skidka_podskazka": podskazka,
        "stop": stop[0]["status"] if stop else "",
        "vozrast": age_v, "vozrast_otkuda": otkuda,
        "vozrast_text": f"{_chislo(age_v)} {_let(age_v)}" if age_v is not None else "",
        "gruppy": gr,
        "otvety": sobrat_otvety(fokus["imya_rech"], novaya, gr["top"] + gr["eshche"]),
        "tek": str(gr["top"][0]["id"]) if gr["top"] else "_bez",
    })
    return res
