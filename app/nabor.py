"""Где именно теряется набор: воронка по предметам и списки для работы.

Вопрос Бориса 15.09: «что сделать для максимизации заполнения групп, как
ускорить ПШ и АЯ». Отвечать на него по ощущениям нельзя — в центре 129
свободных мест, и от того, где именно рвётся цепочка, зависит, звонить
завтра или менять рекламу.

Цепочка одна и та же для любого предмета:
    заявка → записан на пробное → дошёл до пробного → купил абонемент
Каждый переход теряет детей, и лечится каждый разрыв по-своему:
дошёл мало — напоминания и подтверждение; купил мало — разговор на выходе
и скидка дня пробного; записан мало — реклама и обзвон базы.

Только чтение. Списки отдаются с телефонами, чтобы админ работал прямо
по ним, не выгружая ничего руками.
"""
from __future__ import annotations

import json
from datetime import date

from . import db, mesta

SEASON_START = "2026-08-31"          # первый учебный день сезона
DEAD_STATES = {345759, 125957, 146328, 125954, 215202, 146330, 146513}
ST_UCHITSYA = 2
ST_ZAPISAN = (83760, 58132)          # подтвердил заявку / записался на пробное
ST_POSETIL = 58131                   # пришёл на пробное
ST_NE_PRISHEL = 99336                # не пришёл на пробное


def _season_classes(conn) -> dict[int, str]:
    return {r[0]: r[1] for r in conn.execute(
        "SELECT id, name FROM classes WHERE name LIKE '2627_%' "
        "AND (status IS NULL OR status='opened')")
        if not any(x in (r[1] or "") for x in ("Заявк",))
        and not (r[1] or "").startswith("2627_ЛГ")
        and "лагер" not in (r[1] or "").lower()}


def _birthday(raw: str) -> str:
    try:
        for a in (json.loads(raw or "{}").get("attributes") or []):
            if a.get("attributeAlias") == "birthday":
                return (a.get("value") or "")[:10]
    except ValueError:
        pass
    return ""


def _age(bd: str, today: date) -> float | None:
    if len(bd) != 10:
        return None
    try:
        y, m, d = int(bd[:4]), int(bd[5:7]), int(bd[8:10])
    except ValueError:
        return None
    return round((today - date(y, m, d)).days / 365.25, 1)


def analiz() -> dict:
    today = date.today()
    with db.get_conn() as conn:
        cls = _season_classes(conn)
        subj = {cid: mesta._subject(n.replace("2627_", "")) for cid, n in cls.items()}
        paid_idx = mesta._paid_by_class(conn)

        # какие предметы ребёнок оплатил — для конверсии и кросс-продажи
        paid_subj: dict[int, set[str]] = {}
        for cid, users in paid_idx.items():
            if cid not in cls:
                continue
            for uid in users:
                paid_subj.setdefault(uid, set()).add(subj[cid])

        # воронка по предметам
        F: dict[str, dict] = {}
        for cid, s in subj.items():
            f = F.setdefault(s, {"учатся": set(), "записаны": set(),
                                 "посетили": set(), "не_пришли": set(), "оплатили": set()})
            f["оплатили"] |= paid_idx.get(cid, set())
        rows = conn.execute(
            "SELECT user_id, class_id, status_id FROM joins WHERE status_id IN (?,?,?,?,?)",
            (ST_UCHITSYA, *ST_ZAPISAN, ST_POSETIL, ST_NE_PRISHEL)).fetchall()
        for uid, cid, st in rows:
            if cid not in subj:
                continue
            f = F[subj[cid]]
            if st == ST_UCHITSYA:
                f["учатся"].add(uid)
            elif st in ST_ZAPISAN:
                f["записаны"].add(uid)
            elif st == ST_POSETIL:
                f["посетили"].add(uid)
            elif st == ST_NE_PRISHEL:
                f["не_пришли"].add(uid)

        # Пробные считаем по журналу занятий, а не по статусу записи: как
        # только ребёнок покупает абонемент, админ переводит запись в
        # «Учится», и статус «Посетил пробное» на нём не остаётся. По
        # статусам получалась бы конверсия 0% при живых оплатах.
        # lesson_records.raw.test — отметка «пробное занятие».
        probn: dict[str, dict[str, set[int]]] = {}
        for uid, cid, d, visit, raw in conn.execute(
                "SELECT lr.user_id, l.class_id, l.date, lr.visit, lr.raw FROM lesson_records lr "
                "JOIN lessons l ON l.id = lr.lesson_id WHERE l.date >= ?", (SEASON_START,)):
            if cid not in subj:
                continue
            try:
                test = bool(json.loads(raw or "{}").get("test"))
            except ValueError:
                test = False
            if not test:
                continue
            p = probn.setdefault(subj[cid], {"назначено": set(), "пришли": set(),
                                             "не_пришли": set(), "впереди": set()})
            p["назначено"].add(uid)
            if visit:
                p["пришли"].add(uid)
            elif d >= today.isoformat():
                p["впереди"].add(uid)
            else:
                p["не_пришли"].add(uid)

        voronka = {}
        for s, f in F.items():
            p = probn.get(s, {"назначено": set(), "пришли": set(),
                              "не_пришли": set(), "впереди": set()})
            pos = len(p["пришли"] - p["впереди"])
            nep = len(p["не_пришли"] - p["пришли"])
            kupili = len([u for u in p["пришли"] if s in paid_subj.get(u, ())])
            voronka[s] = {
                "учатся": len(f["учатся"]), "оплатили": len(f["оплатили"]),
                "ждём_на_пробное": len(f["записаны"]),
                "пробных_назначено": len(p["назначено"]),
                "пришли_на_пробное": pos, "не_пришли_на_пробное": nep,
                "пробное_впереди": len(p["впереди"]),
                "купили_после_пробного": kupili,
                "доходимость_%": round(100 * pos / (pos + nep)) if pos + nep else None,
                "конверсия_пробное_оплата_%": round(100 * kupili / pos) if pos else None,
            }
            f["пришли_факт"] = p["пришли"]

        # кросс-продажа: кто платит ровно за один предмет
        odin = [uid for uid, ss in paid_subj.items() if len(ss) == 1]
        cross: dict[str, int] = {}
        for uid in odin:
            s = next(iter(paid_subj[uid]))
            cross[s] = cross.get(s, 0) + 1

        # тёплые списки для работы
        users = {r[0]: r for r in conn.execute(
            "SELECT id, name, phone, client_state_id, raw FROM users")}

        def person(uid, extra=None):
            u = users.get(uid)
            if not u:
                return None
            bd = _birthday(u[4])
            return {"id": uid, "имя": u[1], "телефон": u[2],
                    "статус": u[3], "возраст": _age(bd, today), **(extra or {})}

        spiski: dict[str, list] = {
            "не_пришли_на_пробное": [], "были_не_купили": [], "ждём_на_пробное": [],
            "один_предмет": [],
        }
        for s, f in F.items():
            for uid in (f["не_пришли"] | probn.get(s, {}).get("не_пришли", set())):
                if uid in f["учатся"] or s in paid_subj.get(uid, ()):
                    continue
                p = person(uid, {"предмет": s})
                if p and p["статус"] not in DEAD_STATES:
                    spiski["не_пришли_на_пробное"].append(p)
            for uid in (f["посетили"] | f.get("пришли_факт", set())):
                if s in paid_subj.get(uid, ()) or uid in f["учатся"]:
                    continue
                p = person(uid, {"предмет": s})
                if p and p["статус"] not in DEAD_STATES:
                    spiski["были_не_купили"].append(p)
            for uid in f["записаны"]:
                p = person(uid, {"предмет": s})
                if p and p["статус"] not in DEAD_STATES:
                    spiski["ждём_на_пробное"].append(p)
        for uid in odin:
            p = person(uid, {"ходит_на": next(iter(paid_subj[uid]))})
            if p:
                spiski["один_предмет"].append(p)

        # база: карточки без оплаты сезона, живой статус, возраст под ПШ и АЯ
        paid_any = set(paid_subj)
        baza = {"ПШ_4_8": [], "АЯ_3_12": [], "без_возраста": 0, "всего_живых_без_оплаты": 0}
        for uid, u in users.items():
            if uid in paid_any or (u[3] in DEAD_STATES) or not u[2]:
                continue
            baza["всего_живых_без_оплаты"] += 1
            a = _age(_birthday(u[4]), today)
            if a is None:
                baza["без_возраста"] += 1
                continue
            rec = {"id": uid, "имя": u[1], "телефон": u[2], "возраст": a, "статус": u[3]}
            if 4 <= a <= 8:
                baza["ПШ_4_8"].append(rec)
            if 3 <= a <= 12:
                baza["АЯ_3_12"].append(rec)

        # полупустые группы: где набор не идёт и слот стоит зря
        polupustye = []
        for r in mesta.tablica()["группы"]:
            if r["ходят"] + r["записаны_на_пробное"] <= 2 and not r["пауза"] and not r["замена"]:
                polupustye.append({"группа": r["name"], "ходят": r["ходят"],
                                   "на_пробное": r["записаны_на_пробное"],
                                   "свободно": r["свободно"]})

    for k in spiski:
        seen = set()
        spiski[k] = [r for r in spiski[k]
                     if not (( r["id"], r.get("предмет") ) in seen
                             or seen.add((r["id"], r.get("предмет"))))]
        spiski[k].sort(key=lambda r: (r.get("предмет") or "", r["имя"] or ""))
    return {"воронка": voronka, "кросс_продажа_один_предмет": cross,
            "списки": {k: {"сколько": len(v), "кто": v} for k, v in spiski.items()},
            "база_без_оплаты": {"всего_живых": baza["всего_живых_без_оплаты"],
                                "без_даты_рождения": baza["без_возраста"],
                                "возраст_ПШ_4_8": len(baza["ПШ_4_8"]),
                                "возраст_АЯ_3_12": len(baza["АЯ_3_12"]),
                                "ПШ": baza["ПШ_4_8"], "АЯ": baza["АЯ_3_12"]},
            "полупустые_группы": polupustye,
            "дата": today.isoformat()}
