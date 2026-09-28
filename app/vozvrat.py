"""Вернуть и дожать: «думает», «недозвон» и база 2024/25 — звонки по сменам.

28.09.2026, решение владельца («давай всё делаем»):
  1. «Думает» — следующий живой контакт не позже чем через 7 дней после
     прошлого. 115 семей висели без контакта больше двух недель: сказали
     «подумаем», и им больше не звонили.
  2. «Недозвон» — три попытки в разное время (день, вечер, выходные), потом
     сообщение в чат и «Архив набора».
  3. Позапрошлый учебный год (01.09.2024–31.05.2025) и лето 2025
     (01.06–31.08.2025) — отдельный список: кто ходил тогда и не ходил
     с сентября 2025.

Каждое утро (после утренней актуализации пульта) очередь раскладывается по
сменам: пункт в инбокс на семью — что за семья, что уже было, какую группу
с местами и на какие даты предлагать. Сколько в день — настройка
vozvrat_v_den (по умолчанию 24), кому — дежурные из vozvrat_kto
(по умолчанию Аня, Ира и Лена: «думает» — дожим после разговора, это Лене;
«недозвон» и база — дежурным).

Предложение строится по живым местам (mesta.razrez): только группы, где
есть свободные места, нет пометки «полная / не записывать / лист», и
возраст ребёнка попадает в рамку группы. Сначала — продолжение того, что
ребёнок уже посещал, потом — что подходит по возрасту. Платное пробное
(шахматы, ИЗО, робототехника) называем с ценой.

Живой контакт — разговор от 20 секунд в любую сторону или сообщение от
клиента. Наши рассылки контактом не считаются. Только чтение CRM; запись —
в наш инбокс. Сообщения семьям — только через /api/vozvrat/napisat, по
отмашке, стандартной схемой (мессенджер с перепиской + WhatsApp, СМС тем,
кто платил).
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from . import db, nabor

log = logging.getLogger("kidsup.vozvrat")

DUMAET, NEDOZVON, OTKAZ, ARHIV = 146950, 345768, 125957, 345759
DEAD = {146328, 215202, 125954, 146513}
NABOR_S = "2026-08-01"
SEZON_S = "2026-08-25"
GOD25 = ("2024-09-01", "2025-05-31")
LETO25 = ("2025-06-01", "2025-08-31")
NOVYI_GOD = "2025-09-01"          # с этой даты — уже база 2025/26, у неё свой список /obzvon

DNI = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
PREDMET_KOD = {"ПШ": "Подготовка к школе", "АЯ": "Английский", "РР": "Раннее развитие",
               "МсМ": "Раннее развитие", "ИЗО": "ИЗО", "МА": "Ментальная арифметика",
               "ШАХ": "Шахматы", "Робот": "Робототехника"}
PLATNOE = {"ИЗО": "850 ₽", "Шахматы": "850 ₽", "Робототехника": "1 600 ₽"}
NAZVANIE = {"Подготовка к школе": "подготовка к школе", "Английский": "английский",
            "Раннее развитие": "раннее развитие", "ИЗО": "ИЗО-студия",
            "Ментальная арифметика": "ментальная арифметика", "Шахматы": "шахматы",
            "Робототехника": "робототехника"}
# 28.09: ПШ Гр5 (пн-чт 16:00) предложено объединить с Гр6 — пока Борис не решил,
# новых туда не зовём (разбор мер по ПШ, страница 311).
NE_PREDLAGAT = ("ПШ_пн-чт_16:00_5-7 лет",)
NE_ZAPISYVAT = re.compile(r"не записыв|полная|не набира|закрыт|лист |лист$", re.I)


def _p10(x) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())[-10:]


def _predmet_iz_klassa(cls: str) -> str:
    n = (cls or "").replace("2627_", "").replace("2526_", "").replace("2425_", "")
    for k in ("Подготовка", "ПШ", "Первая школа", "Лицей"):
        if k in n[:14]:
            return "ПШ" if k in ("Подготовка", "ПШ") else "РР"
    for k, v in (("Англ", "АЯ"), ("АЯ", "АЯ"), ("Музык", "РР"), ("РР", "РР"), ("МсМ", "РР"),
                 ("ИЗО", "ИЗО"), ("МА", "МА"), ("Ментал", "МА"), ("ШАХ", "ШАХ"), ("Шахмат", "ШАХ"),
                 ("Робот", "Робот"), ("Мини", "Мини-сад"), ("Нулев", "НК"), ("ЛГ", "ЛГ"), ("Логопед", "ЛГ"),
                 ("лагер", "Лагерь"), ("Лагер", "Лагерь")):
        if k in n:
            return v
    return ""


# ------------------------------------------------------------ группы с местами

def gruppy() -> list[dict]:
    """Группы сезона, куда сейчас можно звать: места есть, возраст из названия."""
    from . import mesta
    r = mesta.razrez()
    out = []
    for predmet, p in r["предметы"].items():
        if predmet.startswith("Логопед") or predmet in ("Мини-сад", "Нулевой класс"):
            continue
        for g in p["группы"]:
            if g["свободно"] <= 0 or NE_ZAPISYVAT.search(g.get("пометка") or ""):
                continue
            if any(g["name"].startswith(x) for x in NE_PREDLAGAT):
                continue
            name = g["name"]
            bez_vremeni = re.sub(r"\d{1,2}:\d{2}", " ", name)
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)", bez_vremeni)
            if not m:
                continue
            lo, hi = (float(x.replace(",", ".")) for x in m.groups())
            if hi <= lo:
                continue
            dni = [d for d in re.findall(r"(пн|вт|ср|чт|пт|сб|вс)", name.lower())]
            vremya = re.findall(r"\d{1,2}:\d{2}", name)
            # «ПШ_чт 19:00 + сб 11:00» — новых только в субботу (пометка группы)
            if "ТОЛЬКО на сб" in (g.get("пометка") or ""):
                dni, vremya = ["сб"], [t for t in vremya if t.startswith("11")] or vremya[-1:]
            out.append({"name": name, "предмет": predmet, "свободно": g["свободно"],
                        "lo": lo, "hi": hi, "дни": dni, "время": vremya,
                        "пометка": g.get("пометка") or ""})
    return out


def _blizhaishie(dni: list[str], vremya: list[str], n: int = 2) -> list[str]:
    """Две ближайшие даты занятий начиная с завтра: «вт 29.09 17:00»."""
    if not dni:
        return []
    out, d = [], date.today() + timedelta(days=1)
    for _ in range(14):
        wd = DNI[d.weekday()]
        if wd in dni:
            i = dni.index(wd)
            t = vremya[i] if i < len(vremya) else (vremya[0] if vremya else "")
            out.append(f"{wd} {d:%d.%m}" + (f" {t}" if t else ""))
            if len(out) >= n:
                break
        d += timedelta(days=1)
    return out


def predlozhit(vozrast, byvali: set[str], grp: list[dict] | None = None, n: int = 2) -> list[dict]:
    """Куда звать ребёнка: до n групп разных предметов, продолжение — первым."""
    if vozrast is None:
        return []
    grp = grp if grp is not None else gruppy()
    byl_predmety = {PREDMET_KOD.get(k, k) for k in byvali}
    kand = []
    for g in grp:
        if not (g["lo"] - 0.3 <= vozrast <= g["hi"] + 0.3):
            continue
        if g["предмет"] == "Подготовка к школе" and vozrast > 7.4:
            continue                                   # уже школьник
        ball = min(g["свободно"], 4)
        if g["предмет"] in byl_predmety:
            ball += 100
        if g["предмет"] in ("Подготовка к школе", "Английский"):
            ball += 20
        if g["предмет"] in PLATNOE:
            ball -= 10
        kand.append((ball, g))
    kand.sort(key=lambda x: -x[0])
    out, vzyato = [], set()
    for _, g in kand:
        if g["предмет"] in vzyato:
            continue
        vzyato.add(g["предмет"])
        out.append({"группа": g["name"], "предмет": g["предмет"], "свободно": g["свободно"],
                    "даты": _blizhaishie(g["дни"], g["время"]),
                    "пробное": (f"платное {PLATNOE[g['предмет']]}, в зачёт абонемента"
                                if g["предмет"] in PLATNOE else "условно-бесплатное")})
        if len(out) >= n:
            break
    return out


def _imya_korotko(full: str) -> str:
    parts = (full or "").split()
    return parts[1] if len(parts) > 1 else (parts[0] if parts else "")


def predlozheniya(deti: list[dict], byvali: set[str], grp: list[dict]) -> list[dict]:
    """Каждому ребёнку семьи (до трёх) — своя группа; старшему по списку — два варианта."""
    out = []
    s_vozr = [d for d in deti if d.get("возраст") is not None][:3]
    for i, d in enumerate(s_vozr):
        for o in predlozhit(d["возраст"], byvali, grp, n=2 if i == 0 else 1):
            o["для"] = _imya_korotko(d["имя"]) if len(s_vozr) > 1 else ""
            out.append(o)
    return out


def _kratko(o: dict) -> str:
    dat = " или ".join(o["даты"]) if o["даты"] else ""
    return ((f"{o['для']} → " if o.get("для") else "") + f"{NAZVANIE.get(o['предмет'], o['предмет'])} — {o['группа']} (мест {o['свободно']})"
            + (f": {dat}" if dat else "") + (f"; пробное {o['пробное']}" if o["предмет"] in PLATNOE else ""))


# ------------------------------------------------------------ данные

def _dannye(conn) -> dict:
    today = date.today()
    st_names = {}
    try:
        st_names = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM client_statuses")}
    except Exception:
        pass
    mertvye = set(DEAD) | {k for k, v in st_names.items()
                           if "Переехал" in (v or "") or "13 лет" in (v or "")}
    users = {r[0]: r for r in conn.execute(
        "SELECT id, name, phone, client_state_id, created_at, raw FROM users")}
    zhivoy: dict[str, str] = {}
    popytki: dict[str, list[str]] = defaultdict(list)
    for ts, phone, direction, state in conn.execute(
            "SELECT ts, phone, direction, state FROM mango_calls WHERE ts >= ?", ("2025-06-01",)):
        p = _p10(phone)
        if not p:
            continue
        if state == "talked":
            if str(ts) > zhivoy.get(p, ""):
                zhivoy[p] = str(ts)
        elif direction == "out" and str(ts) >= NABOR_S:
            popytki[p].append(str(ts))
    pisali_nam: dict[str, str] = {}
    my_pisali: dict[str, str] = {}
    for table, store in (("wazzup_inbox", pisali_nam), ("wazzup_outbox", my_pisali)):
        try:
            for ts, phone in conn.execute(f"SELECT ts, phone FROM {table} WHERE ts >= ?", ("2025-06-01",)):
                p = _p10(phone)
                if p and str(ts) > store.get(p, ""):
                    store[p] = str(ts)
        except Exception:
            pass
    seychas = {r[0] for r in conn.execute(
        "SELECT DISTINCT lr.user_id FROM lesson_records lr JOIN lessons l ON l.id = lr.lesson_id "
        "WHERE l.date >= ?", (SEZON_S,))}
    uchitsya = {r[0] for r in conn.execute(
        "SELECT DISTINCT j.user_id FROM joins j JOIN classes c ON c.id = j.class_id "
        "WHERE c.name LIKE ? AND c.name NOT LIKE ? AND j.status_id = 2", ("2627_%", "%Заявк%"))}
    platil = {r[0] for r in conn.execute("SELECT DISTINCT user_id FROM payments WHERE summa > 0")}
    return {"today": today, "st": st_names, "mertvye": mertvye, "users": users, "zhivoy": zhivoy,
            "popytki": popytki, "pisali_nam": pisali_nam, "my_pisali": my_pisali,
            "seychas": seychas, "uchitsya": uchitsya, "platil": platil}


def _semi(D: dict, uids) -> dict[str, list]:
    sem: dict[str, list] = defaultdict(list)
    for uid in uids:
        u = D["users"].get(uid)
        if not u:
            continue
        p = _p10(u[2])
        if p:
            sem[p].append(u)
    return sem


def _vse_na_nomere(D: dict) -> dict[str, list]:
    sem: dict[str, list] = defaultdict(list)
    for u in D["users"].values():
        p = _p10(u[2])
        if p:
            sem[p].append(u)
    return sem


def _slot(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts[:19])
    except ValueError:
        return "день"
    if dt.weekday() >= 5:
        return "выходные"
    return "вечер" if dt.hour >= 17 else "день"


def _byvali(conn, uids: list[int], s: str = "2024-09-01", po: str = "2026-08-30") -> tuple[set, int, str]:
    q = ",".join("?" * len(uids))
    pr, n, last = set(), 0, ""
    for d_, cls in conn.execute(
            f"SELECT l.date, c.name FROM lesson_records lr JOIN lessons l ON l.id = lr.lesson_id "
            f"LEFT JOIN classes c ON c.id = l.class_id WHERE lr.visit = 1 AND lr.user_id IN ({q}) "
            f"AND l.date >= ? AND l.date <= ?", (*uids, s, po)):
        n += 1
        last = max(last, d_ or "")
        k = _predmet_iz_klassa(cls or "")
        if k:
            pr.add(k)
    return pr, n, last


def spiski() -> dict:
    """Три очереди: думает, недозвон, база 2024/25 — с предложением по каждой семье."""
    grp = gruppy()
    with db.get_conn() as conn:
        D = _dannye(conn)
        today = D["today"]
        na_nomere = _vse_na_nomere(D)

        def kontakt(p):
            return max(D["zhivoy"].get(p, ""), D["pisali_nam"].get(p, ""))[:10]

        def rebenok(u):
            age = nabor._age(nabor._birthday(u[5]), today)
            return {"uid": u[0], "имя": (u[1] or "").strip(), "возраст": age}

        def semya_zanyata(p):
            return any(x[0] in D["uchitsya"] or x[0] in D["seychas"] for x in na_nomere.get(p, []))

        # ---- думает
        dumaet = []
        for p, us in _semi(D, [u[0] for u in D["users"].values() if u[3] == DUMAET]).items():
            if any(x[3] in D["mertvye"] for x in na_nomere.get(p, [])):
                continue
            if all(x[0] in D["uchitsya"] for x in us):
                continue                               # уже учится — статус просто не обновили
            k = kontakt(p)
            posl_pop = max(D["popytki"].get(p, [""]))[:10]
            dney = (today - date.fromisoformat(k)).days if k else 999
            if dney < 7 or (posl_pop and (today - date.fromisoformat(posl_pop)).days < 2):
                continue                               # срок ещё не подошёл / только что набирали
            deti = [rebenok(u) for u in us]
            byv, n, last = _byvali(conn, [u[0] for u in us])
            vozr = next((d["возраст"] for d in deti if d["возраст"] is not None), None)
            dumaet.append({"вид": "думает", "телефон": "7" + p, "дети": deti, "возраст": vozr,
                           "контакт": k, "дней_без_контакта": dney, "попыток_14д": sum(
                               1 for t in D["popytki"].get(p, []) if t[:10] >= (today - timedelta(days=14)).isoformat()),
                           "платил": any(u[0] in D["platil"] for u in us), "ходил_на": sorted(byv),
                           "занятий": n, "предложение": predlozheniya(deti, byv, grp)})

        def ball_dumaet(r):
            psh_ay = any(o["предмет"] in ("Подготовка к школе", "Английский") for o in r["предложение"][:1])
            return (0 if psh_ay else 1, 0 if r["платил"] else 1, -min(r["дней_без_контакта"], 90))
        dumaet.sort(key=ball_dumaet)

        # ---- недозвон
        nedozvon = []
        for p, us in _semi(D, [u[0] for u in D["users"].values() if u[3] == NEDOZVON]).items():
            if any(x[3] in D["mertvye"] for x in na_nomere.get(p, [])) or semya_zanyata(p):
                continue
            k = kontakt(p)
            pop = sorted(D["popytki"].get(p, []))
            if k and k >= NABOR_S:
                deystvie, slot = "гигиена: разговор был — поставить верный статус", ""
            else:
                sloty = {_slot(t) for t in pop}
                posl = pop[-1][:10] if pop else ""
                if posl and (today - date.fromisoformat(posl)).days < 2:
                    continue
                if len(pop) >= 3 and len(sloty) >= 2:
                    napisali = D["my_pisali"].get(p, "")[:10]
                    deystvie = ("3 попытки и сообщение были — поставить «Архив набора»" if napisali >= posl
                                else "3 попытки без ответа — написать в чат/WhatsApp, потом «Архив набора»")
                    slot = ""
                else:
                    nado = [s for s in ("вечер", "выходные", "день") if s not in sloty]
                    slot = nado[0] if nado else "вечер"
                    kogda = ('вечером после 18:00' if slot == 'вечер' else 'в выходные' if slot == 'выходные' else 'днём')
                    deystvie = (f"попытка {len(pop) + 1} из 3 — звонить {kogda}" if len(pop) < 3 else
                                f"уже {len(pop)} попыток, все в одно время дня — последняя попытка {kogda}, потом сообщение")
            deti = [rebenok(u) for u in us]
            byv, n, last = _byvali(conn, [u[0] for u in us])
            vozr = next((d["возраст"] for d in deti if d["возраст"] is not None), None)
            nedozvon.append({"вид": "недозвон", "телефон": "7" + p, "дети": deti, "возраст": vozr,
                             "попыток": len(pop), "слоты": sorted({_slot(t) for t in pop}), "слот": slot,
                             "действие": deystvie, "платил": any(u[0] in D["platil"] for u in us),
                             "ходил_на": sorted(byv), "занятий": n, "контакт": k,
                             "предложение": predlozheniya(deti, byv, grp)})
        nedozvon.sort(key=lambda r: (0 if r["действие"].startswith("попытка") else 1,
                                     0 if r["платил"] else 1, r["попыток"]))

        # ---- база 2024/25 и лето 2025
        hodil25: dict[int, dict] = {}
        for uid, d_ in conn.execute(
                "SELECT lr.user_id, l.date FROM lesson_records lr JOIN lessons l ON l.id = lr.lesson_id "
                "WHERE lr.visit = 1 AND l.date >= ? AND l.date <= ?", (GOD25[0], LETO25[1])):
            h = hodil25.setdefault(uid, {"год": 0, "лето": 0, "последнее": ""})
            h["год" if d_ <= GOD25[1] else "лето"] += 1
            h["последнее"] = max(h["последнее"], d_)
        pozzhe = {r[0] for r in conn.execute(
            "SELECT DISTINCT lr.user_id FROM lesson_records lr JOIN lessons l ON l.id = lr.lesson_id "
            "WHERE lr.visit = 1 AND l.date >= ?", (NOVYI_GOD,))}
        itog25 = Counter()
        sem25: dict[str, list] = defaultdict(list)
        for uid, h in hodil25.items():
            itog25["детей_ходили"] += 1
            if uid in pozzhe:
                itog25["ходили_и_позже_(есть_в_списке_2025_26)"] += 1
                continue
            u = D["users"].get(uid)
            if not u or not _p10(u[2]):
                itog25["без_телефона"] += 1
                continue
            sem25[_p10(u[2])].append((u, h))
        baza = []
        for p, rows in sem25.items():
            vse = na_nomere.get(p, [])
            if any(x[3] in D["mertvye"] for x in vse):
                itog25["семей_не_писать_переехали_13+"] += 1
                continue
            if semya_zanyata(p) or any(x[0] in pozzhe for x in vse):
                itog25["семей_где_кто-то_ходит_позже"] += 1
                continue
            k = kontakt(p)
            if k and k >= NABOR_S:
                itog25["семей_уже_говорили_в_этот_набор"] += 1
                continue
            us = [r[0] for r in rows]
            deti = [rebenok(u) for u in us]
            byv, n, last = _byvali(conn, [u[0] for u in us], GOD25[0], LETO25[1])
            vozr = next((d["возраст"] for d in deti if d["возраст"] is not None), None)
            pred = predlozheniya(deti, byv, grp)
            st = {x[3] for x in us}
            baza.append({"вид": "база 2024/25", "телефон": "7" + p, "дети": deti, "возраст": vozr,
                         "ходил_на": sorted(byv), "занятий": n, "последнее": last,
                         "период": "год и лето" if any(r[1]["год"] for r in rows) and any(r[1]["лето"] for r in rows)
                         else "учебный год" if any(r[1]["год"] for r in rows) else "лето",
                         "платил": any(u[0] in D["platil"] for u in us),
                         "статус": ", ".join(sorted({D["st"].get(s, str(s)) for s in st})),
                         "отказ_раньше": OTKAZ in st, "говорили_раньше": k,
                         "набирали_в_этот_набор": len(D["popytki"].get(p, [])),
                         "предложение": pred})
            itog25["семей_в_списке"] += 1
        baza.sort(key=lambda r: (0 if r["предложение"] else 1, 1 if r["отказ_раньше"] else 0,
                                 0 if r["платил"] else 1, -r["занятий"]))
    vozr_gr = Counter()
    for r in baza:
        a = r["возраст"]
        vozr_gr["возраст не указан" if a is None else "до 3" if a < 3 else "3–4" if a < 4
                else "4–7 (ПШ)" if a < 7.5 else "7,5–12" if a < 13 else "13+"] += 1
    itog25["по_возрасту"] = dict(vozr_gr)
    itog25["платили"] = sum(1 for r in baza if r["платил"])
    itog25["есть_группа_с_местами"] = sum(1 for r in baza if r["предложение"])
    itog25["отказ_раньше"] = sum(1 for r in baza if r["отказ_раньше"])
    return {"дата": today.isoformat(), "думает": dumaet, "недозвон": nedozvon, "база2425": baza,
            "итог2425": dict(itog25), "групп_с_местами": len(grp)}


# ------------------------------------------------------------ раздача по сменам

def _kto(day: str) -> list[str]:
    from . import pult
    dezh = pult.duty(day)
    hotim = [x.strip() for x in (db.get_setting("vozvrat_kto", "Аня,Ира,Лена") or "").split(",") if x.strip()]
    svoi = [x for x in dezh if x in hotim]
    return svoi or [x for x in dezh if x != "Лиза"] or ["Аня"]


def _tekst(r: dict) -> str:
    deti = ", ".join(f"{d['имя']}{' (' + str(d['возраст']).replace('.', ',') + ')' if d['возраст'] else ''}"
                     for d in r["дети"])
    pred = "; ".join(_kratko(o) for o in r["предложение"]) or "возраст неизвестен — спросить и подобрать группу"
    if r["вид"] == "думает":
        head = (f"ДУМАЕТ, {r['дней_без_контакта'] if r['дней_без_контакта'] < 999 else 'много'} дн. без контакта"
                f"{' (платили раньше)' if r['платил'] else ''}: {deti}.")
        tail = (" Предложить конкретно: " + pred + ". Итог — в карточку; записали — статус «Записался», "
                "думают дальше — следующий звонок не позже чем через 7 дней, нет — «Отказ» с причиной.")
    elif r["вид"] == "недозвон":
        head = f"НЕДОЗВОН, {r['действие']}{' (платили раньше)' if r['платил'] else ''}: {deti}."
        tail = (" Дозвонились — предложить: " + pred + "." if r["действие"].startswith("попытка") else "")
    else:
        head = (f"БАЗА 2024/25 ({r['период']}, {r['занятий']} зан. {', '.join(r['ходил_на'])}"
                f"{', платили' if r['платил'] else ''}{', в карточке «Отказ» с тех пор' if r['отказ_раньше'] else ''}): {deti}.")
        tail = (" Начать: «ваш ребёнок занимался у нас в " + ("прошлом году летом" if r["период"] == "лето" else "2024/25 году")
                + " — в этом сезоне для вашего ребёнка есть группа…». Предложить: " + pred + ". Итог — в карточку.")
    return (head + tail)[:600]


def razdat(day: str = "", dry: bool = False, v_den: int = 0) -> dict:
    """Положить в инбокс дежурным порцию звонков на день."""
    from .autopilot import inbox_add
    day = day or date.today().isoformat()
    n = v_den or int(db.get_setting("vozvrat_v_den", "24") or 24)
    kto = _kto(day)
    if len(kto) == 1:
        n = min(n, 14)                       # один человек в смене — половина порции
    S = spiski()
    # уже висящие открытые пункты по этим телефонам — не дублируем
    with db.get_conn() as conn:
        try:
            est = {_p10(r[0]) for r in conn.execute(
                "SELECT phone FROM plan_inbox WHERE done=0 AND day>=? AND phone IS NOT NULL",
                ((date.today() - timedelta(days=7)).isoformat(),))}
        except Exception:
            est = set()
    doli = [("думает", int(n * 0.55)), ("недозвон", int(n * 0.25)), ("база2425", n)]
    vybor, zanyato = [], set(est)
    for vid, lim in doli:
        for r in S[vid]:
            if len(vybor) >= n or sum(1 for x in vybor if x["вид"] == r["вид"]) >= lim:
                break
            p = _p10(r["телефон"])
            if p in zanyato:
                continue
            if vid == "недозвон" and r["действие"].startswith("гигиена"):
                continue                     # гигиену статусов раздаём отдельным пунктом, не звонком
            zanyato.add(p)
            vybor.append(r)
    # «Думает» — это дожим после разговора, роль Лены; звонки «недозвон» и база —
    # дежурным. Поровну по людям, в смене одна — всё ей.
    polozheno = Counter()
    per = -(-len(vybor) // max(1, len(kto)))
    drugie = [k for k in kto if k != "Лена"] or kto
    schet = Counter()
    for r in vybor:
        if r["вид"] == "думает" and "Лена" in kto and schet["Лена"] < per:
            who = "Лена"
        else:
            who = min(drugie, key=lambda k: schet[k])
            if schet[who] >= per and "Лена" in kto and schet["Лена"] < per:
                who = "Лена"
        schet[who] += 1
        if not dry:
            inbox_add(_tekst(r), phone=r["телефон"], who=who, source=f"возврат: {r['вид']}")
        polozheno[f"{who}: {r['вид']}"] += 1
    return {"ok": True, "день": day, "кому": kto, "в_день": n, "положено": dict(polozheno),
            "очередь": {k: len(S[k]) for k in ("думает", "недозвон", "база2425")},
            "dry_run": dry, "пример": _tekst(vybor[0]) if vybor else ""}


# ------------------------------------------------------------ сообщение «звонили, не застали»

def _programma(o: dict) -> str:
    """Как называть группу родителю: программа, а не служебное имя группы."""
    g = o.get("группа") or ""
    for k in ("Музыка и речь", "Первая школа", "Лицей"):
        if k in g:
            return f"«{k}»"
    return {"Подготовка к школе": "подготовка к школе", "Английский": "английский язык",
            "ИЗО": "ИЗО-студия", "Ментальная арифметика": "ментальная арифметика",
            "Шахматы": "шахматы", "Робототехника": "робототехника"}.get(o.get("предмет"), o.get("предмет", ""))


def soobshchenie(r: dict) -> tuple[str, str]:
    """Личное сообщение семье, до которой не дозвонились, и СМС к нему.

    Без «занимался/занималась»: пол по имени не угадываем. Имя — в нужном
    падеже (autopilot._genitive/_accusative), нет уверенного имени — «ребёнок»."""
    from .autopilot import _accusative, _child_name, _genitive
    imya = next((x for x in ((_child_name(d["имя"]) or "") for d in r["дети"]) if x), "")
    o = r["предложение"][0] if r["предложение"] else None
    abzac = ""
    if o:
        kogda = (" — ближайшие занятия " + " и ".join(o["даты"])) if o["даты"] else ""
        probn = (f"Пробное занятие — {PLATNOE[o['предмет']]}, при покупке абонемента идёт в зачёт."
                 if o["предмет"] in PLATNOE else
                 "Первое занятие условно-бесплатное: не понравится — платить не нужно.")
        dlya = f"для {_genitive(imya)} " if imya else ""
        abzac = f"В этом сезоне {dlya}есть место в группе: {_programma(o)}{kogda}. {probn}\n\n"
    kogo = _accusative(imya) if imya else "ребёнка"
    tekst = ("Здравствуйте! Это KidsUP на бульваре Рокоссовского 🌿\n\n"
             "Мы несколько раз вам звонили, но не застали — поэтому пишем. "
             "Вы уже занимались у нас, и мы будем рады видеть вас снова.\n\n"
             + abzac
             + f"Записать {kogo} на первое занятие? Если удобнее другое время или предмет — "
               "напишите, подберём 💛")
    sms = "KidsUP: звонили вам, не застали. "
    if o:
        sms += (f"Для {_genitive(imya)} есть" if imya else "Есть") + f" место: {_programma(o)}"
        sms += (f", {o['даты'][0]}" if o["даты"] else "") + ". "
    sms += "Записаться: 84951209024"
    return tekst, sms[:300]


def napisat(dry: bool = True, limit: int = 30, tolko: list[str] | None = None) -> dict:
    """Семьям из базы «ходили в 2025/26 и летом» со статусом «недозвон» и 3+ попытками."""
    from . import aychat, statusy
    razb = statusy.razbor(spiski=True)
    tseli = [x for x in razb["не_дозвонились_список"]
             if x["попыток"] >= 3 and x["статус"].startswith("2.")]
    if tolko:
        tseli = [x for x in tseli if x["телефон"] in tolko]
    S = spiski()
    po_tel = {r["телефон"]: r for r in S["недозвон"]}
    items = []
    for x in tseli:
        r = po_tel.get(x["телефон"])
        if not r:
            continue
        tekst, sms = soobshchenie(r)
        items.append({"phone": x["телефон"], "text": tekst, "sms": sms})
    res = aychat.send(items[:limit], kind="reactivate", dry=dry, sms=True, limit=limit)
    res["целей"] = len(tseli)
    res["тексты"] = [{"телефон": i["phone"], "текст": i["text"], "смс": i["sms"]} for i in items[:limit]] if dry else []
    return res
