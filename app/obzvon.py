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
  ходил       — есть хотя бы одно занятие с отметкой о посещении в одном из
                двух окон: учебный год 01.09.2025–31.05.2026 или лето
                01.06.2026–30.08.2026. В таблице видно, в каком именно;
  не связались — за КАМПАНИЮ НАБОРА, с 01.08.2026 по сегодня, с этой семьёй
                не состоялось ни одного живого контакта.

Про второе владелец 17.09 поправил меня отдельно, и поправка важная. Сначала
я отсеивал всех, с кем разговор случался когда-либо за год: поговорили в
октябре — значит прозвонен. Но к набору на этот сезон октябрьский разговор
отношения не имеет; родителю тогда звонили про другое, и приглашения
вернуться он не слышал. Из-за этого из списка выпадали дети, которых как раз
и надо звать. Окно контакта теперь равно окну набора.

Живой контакт — это:
  — телефонный разговор от двадцати секунд (talked), в любую сторону:
    набранный и не взятый номер контактом не является, там просто не знают,
    что мы звонили;
  — входящее сообщение от клиента: написал нам сам — значит связь есть.
Наша рассылка контактом НЕ считается: отправить текст в WhatsApp и связаться
с человеком — разные вещи. В таблице видно, уходила ли рассылка.

Оговорка про журнал: «не звонили» мы можем утверждать только за период,
покрытый журналом Манго. Он начинается 30.08.2025 и оба окна покрывает
целиком, так что здесь эта дыра не мешает.

Кто сюда НЕ попадает:
  — те, кто ходит сейчас: звонить действующему клиенту со словами «вы у нас
    занимались» — позориться. Они вынесены отдельным блоком, просто чтобы
    было видно, что их не потеряли;
  — статусы «не писать», «не работаем», «некачественный лид».

Только чтение.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import db, nabor

# Два окна, как их назвал владелец
GOD_S, GOD_PO = "2025-09-01", "2026-05-31"      # прошлый учебный год
LETO_S, LETO_PO = "2026-06-01", "2026-08-30"    # лето

# Кампания набора на сезон 2026/27: в этом окне и смотрим, связались ли
NABOR_S = "2026-08-01"

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
        # Всё нижеследующее считается ТОЛЬКО за кампанию набора, с 01.08.2026.
        zvonili: dict[str, str] = {}      # набирали, но не дозвонились
        govorili: dict[str, str] = {}     # разговор состоялся, в любую сторону
        ranshe: dict[str, str] = {}       # разговор был, но ДО кампании
        # Что случилось по номеру СЕГОДНЯ, с точностью до минуты. Нужно, чтобы
        # админ видел свою работу сразу: строка не просто исчезает из списка,
        # а переезжает в «сделано сегодня» с временем и исходом. И чтобы не
        # набирать повторно номер, который уже пробовали полчаса назад.
        segodnya: dict[str, list[dict]] = {}
        today_s = today.isoformat()
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
                    govoril = str(state or "") == "talked"
                    ish = str(direction).startswith(("out", "исх"))
                    if day == today_s:
                        segodnya.setdefault(p, []).append({
                            "время": str(ts)[11:16],
                            "что": ("поговорили" if govoril
                                    else "набирали — не взяли" if ish
                                    else "звонили нам — не сняли")})
                    if day < NABOR_S:
                        # разговор до набора — не контакт по этому поводу,
                        # но админу полезно знать, что человек уже общался
                        if govoril and day > ranshe.get(p, ""):
                            ranshe[p] = day
                        continue
                    if govoril:
                        if day > govorili.get(p, ""):
                            govorili[p] = day
                    elif ish:
                        if day > zvonili.get(p, ""):
                            zvonili[p] = day
        except Exception:
            pass

        # Переписка за кампанию: входящее от клиента — это связь состоялась,
        # наша рассылка — нет. Разводим их намеренно.
        pisali: dict[str, str] = {}       # мы отправили (в т.ч. рассылку)
        otvechali: dict[str, str] = {}    # клиент написал сам
        for table, store in (("wazzup_outbox", pisali), ("wazzup_inbox", otvechali)):
            try:
                rows_w = conn.execute(
                    f"SELECT ts, phone FROM {table} WHERE ts >= ?", (NABOR_S,)).fetchall()
            except Exception:
                continue
            for ts, phone in rows_w:
                p = _p10(phone)
                if not p:
                    continue
                if str(ts)[:10] > store.get(p, ""):
                    store[p] = str(ts)[:10]
                if str(ts)[:10] == today_s and store is otvechali:
                    segodnya.setdefault(p, []).append(
                        {"время": str(ts)[11:16], "что": "написали нам сами"})

        # Посещения в двух окнах: ребёнок, дата, предмет
        hodil: dict[int, dict] = {}
        for uid, d_, cls in conn.execute(
                "SELECT lr.user_id, l.date, c.name "
                "FROM lesson_records lr JOIN lessons l ON l.id = lr.lesson_id "
                "LEFT JOIN classes c ON c.id = l.class_id "
                "WHERE lr.visit = 1 AND ((l.date >= ? AND l.date <= ?) "
                "OR (l.date >= ? AND l.date <= ?))",
                (GOD_S, GOD_PO, LETO_S, LETO_PO)):
            h = hodil.setdefault(uid, {"занятий": 0, "первое": "", "последнее": "",
                                       "предметы": set(), "год": 0, "лето": 0})
            h["занятий"] += 1
            if d_ <= GOD_PO:
                h["год"] += 1
            else:
                h["лето"] += 1
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
             "поговорили": 0, "написали_нам": 0, "ходят_сейчас": 0,
             "нет_карточки": 0}
    deti: list[dict] = []
    aktivnye: list[dict] = []
    sdelano: list[dict] = []
    vchera_s = (date.today() - timedelta(days=1)).isoformat()
    vchera: list[dict] = []
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
            "занятий_год": h["год"],
            "занятий_лето": h["лето"],
            "период": ("год и лето" if h["год"] and h["лето"]
                       else "учебный год" if h["год"] else "лето"),
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
        rec["события_сегодня"] = sorted(segodnya.get(p, []),
                                        key=lambda e: e["время"])
        if p in govorili:
            itogo["поговорили"] += 1
            # Связались именно сегодня — это работа текущей смены. Такую
            # строку не прячем молча: админ должен видеть, что сделал.
            if govorili[p] == today_s:
                rec["итог"] = "поговорили"
                sdelano.append(rec)
            elif govorili[p] == vchera_s:
                # 18.09 Борис: «оставь только невыполненное». Вчерашние
                # разговоры из списка уже ушли, но владельцу нужно видеть,
                # что смена отработала, — поэтому отдельным блоком.
                vchera.append({**rec, "итог": "поговорили"})
            continue
        if p in otvechali:
            itogo["написали_нам"] += 1
            if otvechali[p] == today_s:
                rec["итог"] = "написали нам сами"
                sdelano.append(rec)
            elif otvechali[p] == vchera_s:
                vchera.append({**rec, "итог": "написали нам сами"})
            continue
        rec["набирали"] = zvonili.get(p, "")
        rec["рассылка"] = pisali.get(p, "")
        rec["говорили_раньше"] = ranshe.get(p, "")
        rec["пробовали_сегодня"] = bool(rec["события_сегодня"])
        deti.append(rec)

    # Сортировка под разговор, а не под отчёт. Сверху те, кто ушёл недавно:
    # у них свежая память о нас и о педагоге, и разговор начинается сам собой.
    # Дальше — кто ходил дольше (больше занятий = больше причин вернуться).
    deti.sort(key=lambda r: (r["последнее"], r["занятий"]), reverse=True)
    # Кого уже пробовали сегодня — вниз: перезванивать через полчаса бессмысленно.
    # Сортировка устойчивая, поэтому порядок внутри каждой группы сохраняется.
    deti.sort(key=lambda r: r["пробовали_сегодня"])
    sdelano.sort(key=lambda r: (r["события_сегодня"][0]["время"]
                                if r["события_сегодня"] else ""), reverse=True)

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
        "обновлено": datetime.now().strftime("%H:%M"),
        "сделано_сегодня": sdelano,
        "сделано_вчера": vchera,
        "пробовали_сегодня": sum(1 for r in deti if r["пробовали_сегодня"]),
        "окно": {"год_с": GOD_S, "год_по": GOD_PO,
                 "лето_с": LETO_S, "лето_по": LETO_PO,
                 "набор_с": NABOR_S, "набор_по": today.isoformat()},
        "по_периодам": {k: sum(1 for r in deti if r["период"] == k)
                        for k in ("год и лето", "учебный год", "лето")},
        "рассылка_уходила": sum(1 for r in deti if r["рассылка"]),
        "говорили_раньше": sum(1 for r in deti if r["говорили_раньше"]),
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
