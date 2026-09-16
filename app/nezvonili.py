"""Кому так и не позвонили: карточки прошлого года и лета без единого звонка.

Владелец 16.09: «составь список, кому с прошлого учебного года и этого лета
ни разу так и не позвонили». Вопрос правильный — это самая дешёвая база из
всех, что у нас есть: люди сами оставили номер, а разговора с ними так и не
случилось.

Одна оговорка, без которой список врёт. «Не звонили» мы можем утверждать
только про период, за который у нас есть журнал Манго. Всё, что было до
первой записи в mango_calls, для нас невидимо: там могли звонить каждый день,
и мы об этом не узнаем. Поэтому отчёт всегда начинается с покрытия журнала,
а карточки, созданные раньше его начала, выносятся в отдельную кучу
«не проверить» и в обзвон не идут — иначе админ будет звонить людям со
словами «мы вам никогда не звонили», а звонили.

Считаем по семьям, а не по карточкам: звонок на номер — это разговор со всей
семьёй, даже если детей на номере трое. Телефоны сравниваем по последним
десяти цифрам: в журнале они лежат без кода страны, в карточках — с ним.

Только чтение.
"""
from __future__ import annotations

import json
import re
from datetime import date

from . import db, nabor

# Учебный год 2025/26 плюс лето 2026 — окно из вопроса владельца
OKNO_S = "2025-09-01"
OKNO_PO = "2026-08-31"

# Кому не звоним по правилу владельца
DEAD = {146328,   # 0.1. Не писать / не звонить
        215202,   # 0.2. Не работаем с ним
        125954}   # Некачественный лид


def _p10(x) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())[-10:]


# Имена, которые CRM ставит сама, когда человека опознать не успели
_BEZ_IMENI = re.compile(
    r"^(звонок|пропущен|заявка|без имени|номер)|^[\d\s+()\-]+$", re.I)
_SLUZHEBNOE = re.compile(r"дубл|тест|не важно|проверка", re.I)


def _kachestvo(name: str, p10: str) -> str:
    """Насколько по карточке вообще можно звонить.

    Из 186 семей, которым ни разу не позвонили, у сорока вместо имени стоит
    «Звонок от 7…», ещё у сорока четырёх — просто номер, а среди остальных
    попались эвакуатор, три салона красоты и «Заявка Тест». Отдать это одним
    списком — значит заставить админа самого отделять людей от мусора.

    Мобильный у нас всегда 9xxxxxxxxx после кода страны: городской номер в
    базе детского центра почти всегда чужая организация, а 7777777777 —
    заглушка, которую админ поставил, чтобы сохранить карточку.
    """
    n = (name or "").strip()
    if _SLUZHEBNOE.search(n):
        return "служебная"
    if not p10.startswith("9"):
        return "не мобильный"
    if not n or _BEZ_IMENI.match(n):
        return "имя не заполнено"
    return "можно звонить"


def pokrytie(conn) -> dict:
    """За какой период у нас вообще есть журнал звонков."""
    try:
        row = conn.execute(
            "SELECT MIN(ts), MAX(ts), COUNT(*) FROM mango_calls").fetchone()
    except Exception:
        return {"есть": False, "почему": "таблицы mango_calls нет"}
    if not row or not row[0]:
        return {"есть": False, "почему": "журнал пуст"}
    po_mes: dict[str, int] = {}
    for (ts,) in conn.execute("SELECT ts FROM mango_calls"):
        po_mes[str(ts)[:7]] = po_mes.get(str(ts)[:7], 0) + 1
    return {"есть": True, "с": row[0][:10], "по": row[1][:10], "всего": row[2],
            "по_месяцам": dict(sorted(po_mes.items()))}


def spisok(since: str = OKNO_S, until: str = OKNO_PO, limit: int = 0) -> dict:
    today = date.today()
    with db.get_conn() as conn:
        pokr = pokrytie(conn)
        gorizont = pokr.get("с") if pokr.get("есть") else None

        # кому звонили и кто звонил нам — по последним 10 цифрам
        # Три разных вещи, и смешивать их нельзя:
        #   мы звонили            — вопрос владельца ровно про это;
        #   клиент дозвонился нам — разговор был, звать его «непрозвоненным» глупо;
        #   клиент звонил, мы не взяли — самое горячее, что тут есть: человек
        #                           тянулся к нам сам, и мы не ответили.
        zvonili_my: dict[str, str] = {}
        govorili: dict[str, str] = {}
        ne_otvetili: dict[str, str] = {}    # входящий, трубку не сняли вовсе
        sbrosili: dict[str, str] = {}       # сняли и разговор короче 20 секунд
        if pokr.get("есть"):
            for ts, phone, direction, state in conn.execute(
                    "SELECT ts, phone, direction, state FROM mango_calls"):
                p = _p10(phone)
                if not p:
                    continue
                day = str(ts)[:10]
                st_ = str(state or "")
                if str(direction).startswith(("out", "исх")):
                    d = zvonili_my
                elif st_ == "talked":
                    d = govorili
                elif st_ == "missed":
                    d = ne_otvetili
                else:
                    d = sbrosili
                if day > d.get(p, ""):
                    d[p] = day

        # писали ли в мессенджер — это тоже касание, но не разговор
        pisali: dict[str, str] = {}
        otvechali: dict[str, str] = {}
        for table, store in (("wazzup_outbox", pisali), ("wazzup_inbox", otvechali)):
            try:
                rows = conn.execute(f"SELECT ts, phone FROM {table}").fetchall()
            except Exception:
                continue
            for ts, phone in rows:
                p = _p10(phone)
                if p and str(ts)[:10] > store.get(p, ""):
                    store[p] = str(ts)[:10]

        # кто когда-либо платил — такие знают нас, им звонить легче
        platili = {r[0] for r in conn.execute(
            "SELECT DISTINCT user_id FROM payments WHERE user_id IS NOT NULL")}

        try:
            st_names = {r[0]: r[1] for r in conn.execute(
                "SELECT id, name FROM client_statuses")}
        except Exception:
            st_names = {}

        rows = conn.execute(
            "SELECT id, name, phone, client_state_id, created_at, raw FROM users "
            "WHERE created_at >= ? AND created_at <= ? ORDER BY created_at",
            (since, until + "T23:59:59")).fetchall()

    itogo = {"карточек_в_окне": len(rows), "без_телефона": 0,
             "мёртвый_статус": 0, "звонили": 0, "не_звонили": 0,
             "не_проверить": 0}
    semyi: dict[str, dict] = {}
    ne_proverit: dict[str, dict] = {}
    for uid, name, phone, st, created, raw in rows:
        p = _p10(phone)
        if not p:
            itogo["без_телефона"] += 1
            continue
        if st in DEAD:
            itogo["мёртвый_статус"] += 1
            continue
        if p in zvonili_my or p in govorili:
            itogo["звонили"] += 1
            continue
        bd = nabor._birthday(raw)
        rec = {"телефон": "7" + p, "дети": [], "статус": st,
               "статус_текст": st_names.get(st, ""),
               "создан": (created or "")[:10],
               "возраст": nabor._age(bd, today),
               "платил": False, "писали": "", "отвечал": "",
               "не_ответили": ne_otvetili.get(p, ""),
               "сбросили": sbrosili.get(p, "")}
        # карточка старше журнала — про неё нельзя сказать «не звонили»
        kuda = ne_proverit if (gorizont and (created or "")[:10] < gorizont) else semyi
        f = kuda.setdefault(p, rec)
        f["дети"].append(name or str(uid))
        f["платил"] = f["платил"] or uid in platili
        if f["возраст"] is None:
            f["возраст"] = nabor._age(bd, today)
        f["создан"] = min(f["создан"], (created or "")[:10]) if f["создан"] else (created or "")[:10]
        f["писали"] = pisali.get(p, "")
        f["отвечал"] = otvechali.get(p, "")
        f["не_ответили"] = ne_otvetili.get(p, "")
        f["сбросили"] = sbrosili.get(p, "")

    itogo["не_звонили"] = sum(len(f["дети"]) for f in semyi.values())
    itogo["не_проверить"] = sum(len(f["дети"]) for f in ne_proverit.values())

    def _sort(f):
        # сверху те, кто звонил нам сам и не дозвонился: это не холодный
        # обзвон, а возврат долга. Дальше платившие, потом отвечавшие в
        # переписке, потом по свежести карточки
        return (not f["не_ответили"], not f["сбросили"], not f["платил"],
                not bool(f["отвечал"]), f["создан"] or "")

    spisok_ = sorted(semyi.values(), key=_sort, reverse=False)
    for f in spisok_:
        f["дети"] = sorted(set(f["дети"]))
        f["качество"] = _kachestvo(", ".join(f["дети"]), _p10(f["телефон"]))
    if limit:
        spisok_ = spisok_[:limit]
    po_mesyacam: dict[str, int] = {}
    po_statusam: dict[str, int] = {}
    for f in semyi.values():
        po_mesyacam[f["создан"][:7]] = po_mesyacam.get(f["создан"][:7], 0) + 1
        k = f["статус_текст"] or str(f["статус"] or "без статуса")
        po_statusam[k] = po_statusam.get(k, 0) + 1

    return {
        "окно": {"с": since, "по": until},
        "покрытие_журнала": pokr,
        "итоги": itogo,
        "семей_без_звонка": len(semyi),
        "из_них_звонили_мы_не_ответили": sum(
            1 for f in semyi.values() if f["не_ответили"]),
        "из_них_сняли_и_сбросили": sum(
            1 for f in semyi.values() if f["сбросили"] and not f["не_ответили"]),
        "из_них_платили": sum(1 for f in semyi.values() if f["платил"]),
        "из_них_писали_в_whatsapp": sum(1 for f in semyi.values() if f["писали"]),
        "из_них_отвечали": sum(1 for f in semyi.values() if f["отвечал"]),
        "из_них_вообще_без_касаний": sum(
            1 for f in semyi.values() if not f["писали"] and not f["отвечал"]),
        "по_месяцам": dict(sorted(po_mesyacam.items())),
        "по_статусам": dict(sorted(po_statusam.items(), key=lambda kv: -kv[1])),
        "по_качеству": {k: sum(1 for f in spisok_ if f.get("качество") == k)
                        for k in ("можно звонить", "имя не заполнено",
                                  "не мобильный", "служебная")},
        "семьи": spisok_,
        "не_проверить_семей": len(ne_proverit),
        "не_проверить": sorted(ne_proverit.values(), key=lambda f: f["создан"]),
        "дата": today.isoformat(),
    }
