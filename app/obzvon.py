"""Дети, которые ходили в прошлом сезоне и летом, но которым ни разу не звонили.

Владелец 17.09: «вернёмся к тем, кто ходил прошлый учебный год и это лето,
НО ни разу не прозвонен — отдельная страница с фамилией и именем ребёнка,
хочу сегодня поручить Ане».

Отличие от /nezvonili принципиальное, и ради него написан отдельный модуль.
На /nezvonili список строится по КАРТОЧКАМ, заведённым в окне: человек
оставил номер — и всё. Оттуда и берутся сорок строк «Звонок от 79…»
и городские номера салонов красоты: имени нет, потому что разговора не было.

Здесь список строится по ПОСЕЩЁННЫМ ЗАНЯТИЯМ. Ребёнок сидел в нашем классе,
педагог отметил его в журнале, родитель платил. Имя есть всегда — иначе бы
его не записали в группу. Это самая тёплая база, какая у нас вообще бывает:
звонок начинается не с «вы оставляли заявку», а с «ваш ребёнок у нас
занимался».

Что считаем:
  окно        — 01.09.2025–31.08.2026 (учебный год плюс лето), по дате занятия;
  ходил       — есть хотя бы одно занятие с отметкой о посещении;
  не прозвонен — в журнале Манго нет НИ ОДНОГО состоявшегося разговора с этим
                 номером. Именно разговора, а не набранного номера: телефон,
                 который прозвонил и который никто не взял, для семьи не
                 произошёл, там просто не знают, что мы звонили. По набору
                 номера список выходил на 14 человек, по разговору — на 44.

Оговорка та же, что и на /nezvonili: «не звонили» мы можем утверждать только
за период, покрытый журналом Манго. Журнал начинается 30.08.2025, то есть
окно он покрывает целиком — здесь эта дыра нам не мешает.

Кто сюда НЕ попадает:
  — те, кто ходит сейчас: звонить действующему клиенту со словами «вы у нас
    занимались» — позориться. Они вынесены отдельным блоком, просто чтобы
    было видно, что их не потеряли;
  — статусы «не писать», «не работаем», «некачественный лид».

Только чтение.
"""
from __future__ import annotations

from datetime import date

from . import db, nabor

# Учебный год 2025/26 плюс лето 2026
OKNO_S = "2025-09-01"
OKNO_PO = "2026-08-31"

# Текущий сезон: кто ходит сейчас — не для обзвона
SEZON_S = "2026-08-25"

DEAD = {146328,   # 0.1. Не писать / не звонить
        215202,   # 0.2. Не работаем с ним
        125954}   # Некачественный лид


def _p10(x) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())[-10:]


def _predmet(cls: str) -> str:
    """Из «2627_АЯ_пн-ср_16:00_8-12 лет_Pre-A1 Starters (Гр1)» — «АЯ».

    Имя группы у нас собрано из кусков через подчёркивание, первый кусок
    после префикса сезона — предмет. Админу в разговоре нужен именно он:
    «ваш ребёнок ходил на английский», а не вся строка расписания.
    """
    s = (cls or "").replace("2627_", "").replace("2526_", "")
    s = s.split("_")[0].strip()
    return s[:40]


def spisok() -> dict:
    today = date.today()
    with db.get_conn() as conn:
        # Кому мы звонили — и, отдельно, с кем в итоге поговорили.
        #
        # Первый список вышел на четырнадцать человек, потому что «звонили»
        # считалось по факту набранного номера. Но набранный номер и
        # состоявшийся разговор — разные вещи: телефон прозвонил, никто не
        # взял, и для семьи не произошло ничего. Такого человека звать
        # «прозвоненным» нельзя. Манго различает исходы: talked — разговор
        # от двадцати секунд, missed — не сняли, short — сняли и положили.
        zvonili: dict[str, str] = {}      # набирали хоть раз
        govorili: dict[str, str] = {}     # разговор состоялся, в любую сторону
        pokrytie = {"есть": False}
        try:
            row = conn.execute(
                "SELECT MIN(ts), MAX(ts), COUNT(*) FROM mango_calls").fetchone()
            if row and row[0]:
                pokrytie = {"есть": True, "с": row[0][:10], "по": row[1][:10],
                            "всего": row[2]}
                for ts, phone, direction, state in conn.execute(
                        "SELECT ts, phone, direction, state FROM mango_calls"):
                    p = _p10(phone)
                    if not p:
                        continue
                    day = str(ts)[:10]
                    ishod = str(direction).startswith(("out", "исх"))
                    if ishod and day > zvonili.get(p, ""):
                        zvonili[p] = day
                    if str(state or "") == "talked" and day > govorili.get(p, ""):
                        govorili[p] = day
        except Exception:
            pass

        # Посещения в окне: ребёнок, дата, предмет
        hodil: dict[int, dict] = {}
        for uid, d_, cls in conn.execute(
                "SELECT lr.user_id, l.date, c.name "
                "FROM lesson_records lr JOIN lessons l ON l.id = lr.lesson_id "
                "LEFT JOIN classes c ON c.id = l.class_id "
                "WHERE lr.visit = 1 AND l.date >= ? AND l.date <= ?",
                (OKNO_S, OKNO_PO)):
            h = hodil.setdefault(uid, {"занятий": 0, "первое": "", "последнее": "",
                                       "предметы": set()})
            h["занятий"] += 1
            if not h["первое"] or d_ < h["первое"]:
                h["первое"] = d_
            if d_ > h["последнее"]:
                h["последнее"] = d_
            pr = _predmet(cls)
            if pr:
                h["предметы"].add(pr)

        # Кто ходит сейчас — этих в обзвон не отдаём
        seychas = {r[0] for r in conn.execute(
            "SELECT DISTINCT lr.user_id FROM lesson_records lr "
            "JOIN lessons l ON l.id = lr.lesson_id WHERE l.date >= ?", (SEZON_S,))}

        platili = {r[0] for r in conn.execute(
            "SELECT DISTINCT user_id FROM payments WHERE user_id IS NOT NULL")}

        try:
            st_names = {r[0]: r[1] for r in conn.execute(
                "SELECT id, name FROM client_statuses")}
        except Exception:
            st_names = {}

        users = {r[0]: r for r in conn.execute(
            "SELECT id, name, phone, client_state_id, raw FROM users")}

    itogo = {"ходили_всего": len(hodil), "без_телефона": 0, "мёртвый_статус": 0,
             "поговорили": 0, "ходят_сейчас": 0, "нет_карточки": 0}
    deti: list[dict] = []
    aktivnye: list[dict] = []
    for uid, h in hodil.items():
        u = users.get(uid)
        if not u:
            itogo["нет_карточки"] += 1
            continue
        _, name, phone, st, raw = u
        p = _p10(phone)
        rec = {
            "uid": uid,
            "имя": (name or "").strip() or f"без имени ({uid})",
            "телефон": ("7" + p) if p else "",
            "возраст": nabor._age(nabor._birthday(raw), today),
            "статус": st,
            "статус_текст": st_names.get(st, ""),
            "занятий": h["занятий"],
            "первое": h["первое"],
            "последнее": h["последнее"],
            "предметы": sorted(h["предметы"]),
            "платил": uid in platili,
        }
        if uid in seychas:
            itogo["ходят_сейчас"] += 1
            aktivnye.append(rec)
            continue
        if not p:
            itogo["без_телефона"] += 1
            continue
        if st in DEAD:
            itogo["мёртвый_статус"] += 1
            continue
        if p in govorili:
            itogo["поговорили"] += 1
            continue
        rec["набирали"] = zvonili.get(p, "")
        rec["разговора_не_было"] = bool(rec["набирали"])
        deti.append(rec)

    # Сортировка под разговор, а не под отчёт. Сверху те, кто ушёл недавно:
    # у них свежая память о нас и о педагоге, и разговор начинается сам собой.
    # Дальше — кто ходил дольше (больше занятий = больше причин вернуться).
    deti.sort(key=lambda r: (r["последнее"], r["занятий"]), reverse=True)

    # Один номер — одна семья. Аня звонит на телефон, а не ребёнку: если на
    # номере двое детей, это один звонок, и знать про обоих надо заранее.
    semyi: dict[str, list[dict]] = {}
    for r in deti:
        semyi.setdefault(r["телефон"], []).append(r)
    for rs in semyi.values():
        for r in rs:
            r["братья"] = [x["имя"] for x in rs if x["uid"] != r["uid"]]

    nikogda = [r for r in deti if not r["набирали"]]
    ne_dozvonilis = [r for r in deti if r["набирали"]]

    po_predmetam: dict[str, int] = {}
    for r in deti:
        for pr in r["предметы"] or ["не указан"]:
            po_predmetam[pr] = po_predmetam.get(pr, 0) + 1
    po_mesyacam: dict[str, int] = {}
    for r in deti:
        m = r["последнее"][:7]
        po_mesyacam[m] = po_mesyacam.get(m, 0) + 1

    return {
        "дата": today.isoformat(),
        "окно": {"с": OKNO_S, "по": OKNO_PO},
        "покрытие_журнала": pokrytie,
        "итоги": itogo,
        "детей": len(deti),
        "семей": len(semyi),
        "платили": sum(1 for r in deti if r["платил"]),
        "дети": deti,
        "никогда_не_набирали": len(nikogda),
        "не_дозвонились": len(ne_dozvonilis),
        "по_предметам": dict(sorted(po_predmetam.items(), key=lambda kv: -kv[1])),
        "по_месяцам": dict(sorted(po_mesyacam.items(), reverse=True)),
        "ходят_сейчас": sorted(aktivnye, key=lambda r: r["имя"]),
    }
