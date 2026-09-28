"""Прозвон базы и открытые статусы воронки: что сделано и что висит.

Владелец 28.09: «что там с прозвоном прошлого учебного года и этого лета?
Все прозвонили? Сколько всего прозвонили, сколько недозвонов? Что со
статусом клиента „недозвон“ и „думает“?»

Два среза, оба только чтение.

1. База «ходили». Дети с отмеченным посещением в прошлом учебном году
   (01.09.2025–31.05.2026) или этим летом (01.06–30.08.2026). По каждой
   семье, которая сейчас не ходит, — что было за кампанию набора
   (с 01.08.2026): разговор (звонок от 20 с в любую сторону), только
   попытки (набирали — не взяли / сбросили, с числом попыток), вообще не
   набирали; написали ли нам сами. Для тех, с кем поговорили, — чем
   кончилось по статусу карточки.

2. Статусы «2. Недозвон (в работе)» и «3. Думает (разговор состоялся)»,
   плюс «6. Думает после пробного» — по всей базе. Сколько карточек и
   семей, как давно был последний живой контакт, сколько попыток за
   последние 14 дней, кто уже записан в группу сезона (статус карточки
   тогда просто не обновили), у кого статус «недозвон», хотя разговор
   был (тоже гигиена CRM), а кого никто не трогал больше двух недель.

Живой контакт — как в /obzvon: разговор от 20 секунд или входящее
сообщение от клиента. Наша рассылка контактом не считается.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from . import db, nabor

GOD_S, GOD_PO = "2025-09-01", "2026-05-31"
LETO_S, LETO_PO = "2026-06-01", "2026-08-30"
NABOR_S = "2026-08-01"
SEZON_S = "2026-08-25"

NEDOZVON, DUMAET, DUMAET_POSLE = 345768, 146950, 345767
DEAD = {146328, 215202, 125954}
LIVE_JOIN = (2, 58132, 83760, 58131)


def _p10(x) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())[-10:]


def _vozrast_gruppa(age) -> str:
    if age is None:
        return "возраст не указан"
    if age < 3:
        return "до 3 лет"
    if age < 4:
        return "3–4 года"
    if age < 8:
        return "4–7 лет (ПШ)"
    if age < 13:
        return "8–12 лет"
    return "13+"


def _davnost(d: str, today: date) -> str:
    if not d:
        return "не было с 01.08"
    n = (today - date.fromisoformat(d[:10])).days
    if n <= 7:
        return "до 7 дней"
    if n <= 14:
        return "8–14 дней"
    if n <= 30:
        return "15–30 дней"
    return "больше 30 дней"


def razbor(spiski: bool = False) -> dict:
    today = date.today()
    d14 = (today - timedelta(days=14)).isoformat()
    with db.get_conn() as conn:
        try:
            st_names = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM client_statuses")}
        except Exception:
            st_names = {}
        users = {r[0]: r for r in conn.execute(
            "SELECT id, name, phone, client_state_id, created_at, raw FROM users")}

        # звонки за кампанию набора, по номеру
        kontakt: dict[str, str] = {}          # последний живой контакт
        popytki: Counter = Counter()           # наши исходящие без разговора
        popytki14: Counter = Counter()
        posl_popytka: dict[str, str] = {}
        oni_zvonili: dict[str, str] = {}       # звонили нам, а мы не сняли
        for ts, phone, direction, state in conn.execute(
                "SELECT ts, phone, direction, state FROM mango_calls WHERE ts >= ?", (NABOR_S,)):
            p, d = _p10(phone), str(ts)[:10]
            if not p:
                continue
            if state == "talked":
                if d > kontakt.get(p, ""):
                    kontakt[p] = d
            elif direction == "out":
                popytki[p] += 1
                if d >= d14:
                    popytki14[p] += 1
                if d > posl_popytka.get(p, ""):
                    posl_popytka[p] = d
            elif d > oni_zvonili.get(p, ""):
                oni_zvonili[p] = d
        pisali_nam: dict[str, str] = {}
        try:
            for ts, phone in conn.execute(
                    "SELECT ts, phone FROM wazzup_inbox WHERE ts >= ?", (NABOR_S,)):
                p, d = _p10(phone), str(ts)[:10]
                if p and d > pisali_nam.get(p, ""):
                    pisali_nam[p] = d
        except Exception:
            pass

        # посещения в двух окнах и кто ходит сейчас
        hodil: dict[int, dict] = {}
        for uid, d_ in conn.execute(
                "SELECT lr.user_id, l.date FROM lesson_records lr JOIN lessons l ON l.id = lr.lesson_id "
                "WHERE lr.visit = 1 AND ((l.date >= ? AND l.date <= ?) OR (l.date >= ? AND l.date <= ?))",
                (GOD_S, GOD_PO, LETO_S, LETO_PO)):
            h = hodil.setdefault(uid, {"год": 0, "лето": 0})
            h["год" if d_ <= GOD_PO else "лето"] += 1
        seychas = {r[0] for r in conn.execute(
            "SELECT DISTINCT lr.user_id FROM lesson_records lr JOIN lessons l ON l.id = lr.lesson_id "
            "WHERE l.date >= ?", (SEZON_S,))}
        v_gruppe = defaultdict(list)           # записан в живую группу сезона (не «Заявки»)
        v_zayavkah = defaultdict(list)
        for uid, cname, sid in conn.execute(
                "SELECT j.user_id, c.name, j.status_id FROM joins j JOIN classes c ON c.id = j.class_id "
                "WHERE c.name LIKE ? AND (c.status IS NULL OR c.status='opened') "
                "AND j.status_id IN (%s)" % ",".join("?" * len(LIVE_JOIN)), ("2627_%", *LIVE_JOIN)):
            (v_zayavkah if "Заявк" in (cname or "") else v_gruppe)[uid].append(cname.replace("2627_", ""))
        platil = {r[0] for r in conn.execute(
            "SELECT DISTINCT user_id FROM payments WHERE summa > 0")}

    def kontakt_of(p: str) -> str:
        return max(kontakt.get(p, ""), pisali_nam.get(p, ""))

    # ---------------- 1. база «ходили» -------------------------------------
    sem_hodili: dict[str, dict] = {}
    for uid, h in hodil.items():
        u = users.get(uid)
        if not u:
            continue
        p = _p10(u[2])
        if not p:
            continue
        s = sem_hodili.setdefault(p, {"дети": [], "сейчас": False, "статусы": set(),
                                      "год": False, "лето": False, "мёртв": False})
        s["дети"].append(uid)
        s["статусы"].add(u[3])
        s["год"] |= bool(h["год"])
        s["лето"] |= bool(h["лето"])
        if uid in seychas:
            s["сейчас"] = True
        if u[3] in DEAD:
            s["мёртв"] = True
    b1 = Counter()
    itog_pogovorili = Counter()
    ne_doz_popytki = Counter()
    ne_doz_spisok = []
    ne_nabirali = []
    pogovorili = []
    for p, s in sem_hodili.items():
        b1["семей_ходили"] += 1
        b1["детей_ходили"] += len(s["дети"])
        if s["сейчас"]:
            b1["ходят_сейчас"] += 1
            continue
        if s["мёртв"]:
            b1["статус_не_писать"] += 1
            continue
        b1["не_ходят"] += 1
        if kontakt.get(p):
            b1["поговорили"] += 1
            st = sorted(s["статусы"])
            pogovorili.append({"телефон": "7" + p, "дети": [users[x][1] for x in s["дети"]],
                               "статусы": [st_names.get(x, str(x)) for x in st],
                               "разговор": kontakt.get(p, "")})
            itog_pogovorili[", ".join(st_names.get(x, str(x)) for x in st)] += 1
        elif pisali_nam.get(p):
            b1["написали_нам_сами"] += 1
        elif popytki.get(p):
            b1["не_дозвонились"] += 1
            ne_doz_popytki["3+ попыток" if popytki[p] >= 3 else f"{popytki[p]} попытк"
                           + ("а" if popytki[p] == 1 else "и")] += 1
            if oni_zvonili.get(p):
                b1["не_дозвонились_но_они_нам_звонили"] += 1
            u = users[s["дети"][0]]
            ne_doz_spisok.append({"телефон": "7" + p, "дети": [users[x][1] for x in s["дети"]],
                                  "статус": st_names.get(u[3], ""), "попыток": popytki[p],
                                  "последняя": posl_popytka.get(p, ""),
                                  "звонили_нам": oni_zvonili.get(p, ""),
                                  "период": "год и лето" if s["год"] and s["лето"]
                                  else "учебный год" if s["год"] else "лето"})
        else:
            b1["не_набирали"] += 1
            u = users[s["дети"][0]]
            ne_nabirali.append({"телефон": "7" + p, "дети": [users[x][1] for x in s["дети"]],
                                "статус": st_names.get(u[3], "")})
    ne_doz_spisok.sort(key=lambda r: (r["попыток"], r["последняя"]))

    # ---------------- 2. открытые статусы -----------------------------------
    def srez(status: int) -> dict:
        karty = [u for u in users.values() if u[3] == status]
        sem: dict[str, list] = defaultdict(list)
        bez_tel = 0
        for u in karty:
            p = _p10(u[2])
            if not p:
                bez_tel += 1
                continue
            sem[p].append(u)
        out = Counter()
        davn = Counter()
        vozr = Counter()
        prishli = Counter()
        popyt14 = Counter()
        spisok = []
        for p, us in sem.items():
            k = kontakt_of(p)
            davn[_davnost(k, today)] += 1
            uids = [u[0] for u in us]
            age = nabor._age(nabor._birthday(us[0][5]), today)
            vozr[_vozrast_gruppa(age)] += 1
            cr = min(str(u[4] or "")[:10] for u in us)
            prishli["с прошлого года/лета" if any(x in hodil for x in uids) else
                    ("карточка до 01.08" if cr < NABOR_S else "новые с 01.08")] += 1
            if any(x in v_gruppe for x in uids):
                out["уже_в_группе_сезона"] += 1
            elif any(x in v_zayavkah for x in uids):
                out["в_заявочной_группе"] += 1
            if any(x in platil for x in uids):
                out["платили_раньше"] += 1
            if any(x in seychas for x in uids):
                out["есть_запись_на_занятие_в_сезоне"] += 1
            n14 = popytki14.get(p, 0)
            popyt14["0" if n14 == 0 else "1" if n14 == 1 else "2" if n14 == 2 else "3+"] += 1
            if status == NEDOZVON and kontakt.get(p):
                out["был_разговор_а_статус_недозвон"] += 1
            if status == NEDOZVON and not popytki.get(p) and not k:
                out["с_01_08_ни_разу_не_набирали"] += 1
            if status in (DUMAET, DUMAET_POSLE) and (not k or k < d14) and not n14:
                out["больше_14_дней_никто_не_трогал"] += 1
            if spiski:
                spisok.append({"телефон": "7" + p, "дети": [u[1] for u in us], "возраст": age,
                               "контакт": k, "попыток_14д": n14, "попыток_с_01_08": popytki.get(p, 0),
                               "последняя_попытка": posl_popytka.get(p, ""),
                               "группа": sum((v_gruppe.get(x, []) for x in uids), [])[:2],
                               "создана": cr})
        res = {"статус": st_names.get(status, str(status)), "карточек": len(karty),
               "семей": len(sem), "без_телефона": bez_tel,
               "последний_живой_контакт": dict(davn), "попыток_за_14_дней": dict(popyt14),
               "возраст": dict(vozr), "откуда": dict(prishli), **dict(out)}
        if spiski:
            res["список"] = sorted(spisok, key=lambda r: (r["контакт"] or "", r["последняя_попытка"]))
        return res

    voronka = Counter()
    for u in users.values():
        voronka[st_names.get(u[3], str(u[3]))] += 1

    return {"дата": today.isoformat(), "окна": {"год": [GOD_S, GOD_PO], "лето": [LETO_S, LETO_PO],
                                               "кампания_набора_с": NABOR_S},
            "база_ходили": dict(b1),
            "поговорили_чем_кончилось": dict(itog_pogovorili.most_common()),
            "не_дозвонились_по_попыткам": dict(ne_doz_popytki),
            "не_дозвонились_список": ne_doz_spisok if spiski else len(ne_doz_spisok),
            "не_набирали_список": ne_nabirali,
            "поговорили_список": pogovorili if spiski else len(pogovorili),
            "недозвон": srez(NEDOZVON), "думает": srez(DUMAET), "думает_после_пробного": srez(DUMAET_POSLE),
            "все_статусы": dict(voronka.most_common())}
