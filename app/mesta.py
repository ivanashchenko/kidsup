"""Живой блок «Свободные места сейчас» для страниц плана дня.

06.09 Борис: «страницы с планами обновляются по наполнению?» — до этого
числа мест в плане были статичными (выгрузка на момент сборки). Здесь
считаем по таблицам сервера, которые лёгкий синк обновляет каждые 5 минут:
classes (норма max_students) и joins (живые записи). Только чтение.

Живая запись — статус «учится», «записался на пробное», «подтвердил заявку»,
«посетил пробное». Группы сезона — имя начинается с «2627_», без заявочных
(«_Заявки», «Заявки через»). Группы, которые сливаем (решение по анализу
/base/gruppy_reshenia), помечаем «не записывать» — их список в MERGE.

13.11.2026, Борис (решение М2: «сколько свободных мест считать верным — как в вашем
документе: без закрытых для записи групп, сад и нулевой класс по дням»). Счёт мест для
страницы «Места» (tablica), блока на пульте и плане дня (block) и «Подбора группы»
(dlya_podbora) теперь один — app/mesta_pravilo.py, тем же модулем считает документ
владельца docs/rabota/mesta_boris.py. Здесь — только чтение базы для правила (svod) и
вёрстка. «Оплатили» на этих страницах — оплата ТЕКУЩЕГО месяца (oplatili_mesyac), а не
«платил хоть раз с июня» (_paid_by_class).
Прежний счёт оставлен там, где на него опираются сообщения клиентам и разговор по телефону
и где он проверен своими тестами: rows() (подсказки «Линии», /api/mesta для рутин) и
razrez() (предложения возврата, обзвон дня) — их числа от страницы «Места» могут
отличаться, пока эти места не переведены на правило отдельным решением.
"""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timedelta

from . import db, mesta_pravilo

LIVE = (2, 58132, 83760, 58131)
# 08.09 решение Бориса: чистых слияний нет — только замены (на место слабой группы в тот же слот
# встаёт востребованная). «Не записывать» осталось у двух групп, которые заменяются; ПШ Гр8/Гр9/Гр14,
# ИЗО Гр1/Гр4 — добирать до 30.09. Подробно: /base/kabinety_zamena_0809.
MERGE = {  # подстрока имени группы -> куда вместо
    # 28.09.2026, Ира (переслал Борис): держать группу из двух детей невыгодно.
    # Вт-пт 16:00 закрываем, детей — во вт-пт 17:00 (Гр9, тот же уровень);
    # пн-чт 16:00 уже переведена в пн-чт 17:00 (Гр6). Новых туда не записываем.
    "ПШ_вт-пт_16:00": "новых не записывать; дети ходят по пятницам 1 р/нед; перевод в 17:00 (Гр9) — только с согласия родителей, ведёт Лена",
    "ПШ_пн-чт_16:00": "новых не записывать; перевод в 17:00 (Гр6) — с согласия родителей, ведёт Лена",
    # 22.09.2026: прежнее решение по «АЯ_вт-чт_16:00» («с 22.09 → Starters 5–8
    # в этом слоте, детей 8–12 в Гр1») отменено владельцем. Группа остаётся
    # на месте, рамка 10–12 лет (см. WAITLIST ниже).
    "Первая школа_Группа 4": "→ вс 11:00 (Гр5); слот вс 10:00 → ПШ1 по 4 предоплатам",
}
WAITLIST = {  # переполненные: не дописывать, лист новой группы
    # 22.09.2026, решение владельца и админов по английскому:
    # вт-чт 16:00 — фактический состав 9–13 лет, но это случайное совпадение
    # уровней. Ира и оба преподавателя английского: добирать ТОЛЬКО 10–12,
    # всё, что ниже или выше, — исключение и только по решению педагога.
    # На витрине и в CRM стоит 10–12: писать «9–13» нельзя, родитель видит
    # разрыв в четыре года и читает это как «центр не умеет со школьниками».
    "АЯ_вт-чт_16:00": "добирать 10–12 лет; 9 и 13 — только исключением, спросить педагога",
    "АЯ_пн-ср_18:00": "ПОЛНАЯ, запись закрыта — нулевым 6–7 лет предлагать вт-чт 18:00 (Гр7)",
    "АЯ_вт-чт_19:00": "полная — лист на вторую продвинутую группу 8–11",
    "АЯ_пн-ср_19:00": "лист второй Movers–Flyers 8–12 (при 4 предзаписях)",
    "ПШ_пн-чт_18:00": "лист ПШ2 пн-чт 19:00", "Музыка и речь_Группа 3": "лист МиР 2,2–3 ср-сб 12:45",
    "Музыка и речь_Группа 6": "лист МиР 2,2–3 ср-сб 12:45", "Музыка и речь_Группа 5": "полная — Гр2 ср-сб 10:45 или лист",
    "Первая школа_Группа 3": "лист Первая школа 2–3 вт-чт 13:00",
    "Первая школа_Группа 1": "полная — лист «малыши ср-сб 09:45», решение 30.09",
}
# 07.09: группы, куда пока НЕ набираем и почему (решение по /base/gruppy_reshenia; снимает Борис)
HOLD = {
    "ПШ_чт 19:00 + сб 11:00": "записывать ТОЛЬКО на сб 11:00 (1 р/нед, 5 000); четверг 19:00 с 15.09 → новая ПШ2 читающие пн-чт 19:00",
    "МА_вс_10:30": "не набирать: 0 детей; открывать только при 4 предоплатах",
    "Робототехника_пт 17:00": "старт 11.09 только при 4 предоплатах; до этого — записывать в 4–7 (пт 16:00)",
    "ШАХ_Группа 1": "пятницу объединяем с Гр2 18:00, пока меньше 4; набирать в Гр2",
    "Первая школа_Группа 5": "набирать до 30.09; 0 оплат — меньше 4 оплат к 30.09 = закрыть, семьям будни",
}
ORDER = ("ПШ", "АЯ", "РР", "ИЗО", "МА", "ШАХ", "Робот", "Мини-сад", "Нулевой")
# 06.09 Борис: в мини-саду и нулевом классе по факту максимум 10 детей (в CRM стоит 15);
# больше — открывается ещё одна группа (например, 4–5 лет). ИЗО Гр3 (живопись) — норма 10 по анализу групп.
# 13.10 Борис («я согласен»): в мини-саду ограничиваем не список, а день — не больше 8 записанных
# в один день на педагога; каждый ребёнок ходит 2–4 дня из 5, поэтому в списке до 12 детей.
# Список 10 из 10 давал в пунктах обзвона «сад заполнен, только лист», хотя по дням было по 3 ребёнка.
# 15.10 Борис: «набирайте минимум 8 детей в день». Ребёнок ходит 2–3 дня из 5, поэтому для 8 в день
# в списке нужно 14–20 детей. С лимитом 12 при 11 в списке обзвон переставал предлагать сад,
# а /minisad уже писал «Группа набрана» — при 3–4 детях в день.
CAP_OVERRIDE = {"Мини-сад": 20, "Нулевой класс": 10, "ИЗО_Группа 3": 10}
# 15.10 вечер, Борис: «по мини-саду решили: пн-ср до 6 детей (комнату 3 занимает логопед),
# а вт-чт-пт до 8». Потолок одного дня по weekday() — 0 = понедельник.
SAD_DAY_CAP = {0: 6, 1: 8, 2: 6, 3: 8, 4: 8}
SEASON_SELL_FROM = "2026-06-01"


def _paid_by_class(conn) -> dict[int, set[int]]:
    """class_id -> {user_id} с оплаченным абонементом сезона (sellDate с июня, payed>0).
    Абонемент привязан к группе через classIds/mainClassId; синк — каждые 5 минут.

    13.11.2026: это «платил за группу хотя бы раз с июня», а НЕ «оплатил месяц». Число
    только растёт: ребёнок, ушедший в октябре, остаётся «оплатившим» до конца сезона.
    На копии базы 12.11 так набиралось 395 «оплативших» мест при 272 «Учится» (разбор —
    отчёт C1). Для «оплатили» на страницах — oplatili_mesyac ниже; эта функция оставлена
    для вопросов «платил ли когда-нибудь» (razrez, воронка пробных, анализ набора)."""
    import json as _json
    out: dict[int, set[int]] = {}
    try:
        rows = conn.execute("SELECT user_id, raw FROM user_subscriptions WHERE begin_date >= ?",
                            (SEASON_SELL_FROM,)).fetchall()
    except Exception:
        return out
    for r in rows:
        try:
            raw = _json.loads(r["raw"] or "{}")
        except ValueError:
            continue
        if (raw.get("sellDate") or "") < SEASON_SELL_FROM or not (raw.get("payed") or 0) > 0:
            continue
        cids = set(raw.get("classIds") or [])
        if raw.get("mainClassId"):
            cids.add(raw["mainClassId"])
        for cid in cids:
            out.setdefault(cid, set()).add(r["user_id"])
    return out


# --- единое правило мест и честное «оплатили» (13.11.2026) -----------------------

MESYACY = ["", "январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
           "сентябрь", "октябрь", "ноябрь", "декабрь"]


def _segodnya() -> date:
    """Сегодня по Москве (сервер живёт по UTC; день центра — московский)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Moscow")).date()
    except Exception:  # noqa: BLE001 — нет базы часовых поясов
        return (datetime.utcnow() + timedelta(hours=3)).date()


def mesyac(today: date) -> tuple[date, date]:
    """Первый и последний день текущего месяца."""
    m0 = today.replace(day=1)
    return m0, (m0 + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def _data(x) -> date | None:
    try:
        return date.fromisoformat(str(x)[:10])
    except (TypeError, ValueError):
        return None


def _udalennye_abonementy(conn) -> set[int]:
    """Абонементы, удалённые в МойКлассе: пометки синка и удаления, подтверждённые сверкой
    честности (тот же набор, что у «Проверки МойКласса», proverka_mk.Dannye)."""
    ud = set(db.udalennye("user_subscriptions", conn))
    try:
        row = conn.execute("SELECT value FROM sync_state WHERE key='sverka_chestnosti_udaleno'").fetchone()
        izv = json.loads(row[0]) if row and row[0] else {}
    except Exception:  # noqa: BLE001 — нет таблицы или не JSON: считаем без этих пометок
        izv = {}
    for k in (izv if isinstance(izv, dict) else {}):
        tbl, _, rid = str(k).partition(":")
        if tbl == "user_subscriptions" and rid.isdigit():
            ud.add(int(rid))
    return ud


def oplatili_mesyac(conn, today: date | None = None) -> dict:
    """Кто оплатил ТЕКУЩИЙ месяц — по группам. Только чтение.

    13.11.2026, Борис (М2, «честное „оплатили“»): число на «Местах» и «Без оплаты» должно
    значить «дети с оплаченным абонементом на текущий месяц в этой группе». Прежнее
    (_paid_by_class) значило «платил за группу хоть раз с июня» и было завышено.

    Абонемент считается «на этот месяц» по тому же правилу, что цвет оплаты в «Бланках
    групп» (blank_grupp._money) — его админы видят каждый день: абонемент этой группы
    (classIds/mainClassId; без привязки к группе — на любую), действует в этом месяце и
    начат не раньше чем за неделю до 1-го числа — либо длинный: кончается не раньше 21-го
    и в нём ещё есть занятия. Абонемент «на N занятий» без даты окончания действует, пока
    не израсходован (как в kontrol_oplat._covered). Удалённые в МойКлассе не считаются.
    Оплачен — внесено не меньше цены; внесено меньше — «частично» (оплатившим не считаем).

    Возвращает: m0, m1, mesyac («ноябрь»); oplatili / chastichno — {class_id: {user_id}};
    lyubaya / lyubaya_chastichno — {user_id} с абонементом без привязки к группе;
    abonementy — оплаченные абонементы месяца [{'uid', 'cids', 'main'}] (для сверок).
    Ребёнок с двумя абонементами на группу — один оплативший: это множества детей.
    """
    today = today or _segodnya()
    m0, m1 = mesyac(today)
    out = {"m0": m0, "m1": m1, "mesyac": MESYACY[m0.month], "oplatili": {}, "chastichno": {},
           "lyubaya": set(), "lyubaya_chastichno": set(), "abonementy": []}
    try:
        ud = _udalennye_abonementy(conn)
        rows = conn.execute(
            "SELECT id, user_id, begin_date, end_date, raw FROM user_subscriptions "
            "WHERE substr(begin_date, 1, 10) <= ? AND (end_date IS NULL OR end_date = '' "
            "OR substr(end_date, 1, 10) >= ?)", (m1.isoformat(), m0.isoformat())).fetchall()
    except Exception:  # noqa: BLE001 — на пустой базе таблицы нет: никто не оплатил
        return out
    for r in rows:
        if r["id"] in ud:
            continue
        try:
            raw = json.loads(r["raw"] or "{}")
        except ValueError:
            continue
        b, e = _data(r["begin_date"]), _data(r["end_date"])
        if not b:
            continue
        vc, vd = raw.get("visitCount") or 0, raw.get("visitedCount") or 0
        left = (vc - vd) if vc else 99
        if e is None:
            if left <= 0:
                continue
        elif not (b >= m0 - timedelta(days=7) or (e >= m0 + timedelta(days=20) and left > 0)):
            continue
        price, payed = raw.get("price") or 0, raw.get("payed") or 0
        if not payed > 0:
            continue
        polnost = payed >= price - 1
        cids = set(raw.get("classIds") or [])
        if raw.get("mainClassId"):
            cids.add(raw["mainClassId"])
        uid = r["user_id"]
        if polnost:
            out["abonementy"].append({"uid": uid, "cids": cids, "main": raw.get("mainClassId")})
        if not cids:
            out["lyubaya" if polnost else "lyubaya_chastichno"].add(uid)
        for cid in cids:
            out["oplatili" if polnost else "chastichno"].setdefault(cid, set()).add(uid)
    return out


def _oplata_gruppy(opl: dict, cid: int, u_uch: set) -> tuple[set, set]:
    """Из детей «Учится» группы: (оплатили месяц, оплатили частично)."""
    u_opl = (opl["oplatili"].get(cid, set()) | opl["lyubaya"]) & u_uch
    u_chast = ((opl["chastichno"].get(cid, set()) | opl["lyubaya_chastichno"]) & u_uch) - u_opl
    return u_opl, u_chast


def svod(today: date | None = None, conn=None) -> dict:
    """Места по единому правилу (app/mesta_pravilo.py) — из базы сервера. Только чтение.

    Группы, записи и занятия берём теми же полями, что отдаёт выгрузка /export/raw
    (у занятий — raw с педагогами и записями), поэтому документ владельца и страницы
    считают по одним и тем же данным. Записи в группу, удалённые в МойКлассе
    (mk_udaleno), места не занимают."""
    today = today or _segodnya()

    def _chitat(c):
        classes = [{"id": r["id"], "name": r["name"] or "", "status": r["status"], "cap": r["max_students"]}
                   for r in c.execute("SELECT id, name, status, max_students FROM classes WHERE name LIKE '2627%'")]
        ids = [x["id"] for x in classes]
        if not ids:
            return classes, [], [], {}, set()
        q = ",".join("?" * len(ids))
        joins = [{"id": r["id"], "user_id": r["user_id"], "class_id": r["class_id"], "status_id": r["status_id"]}
                 for r in c.execute(f"SELECT id, user_id, class_id, status_id FROM joins WHERE class_id IN ({q})", ids)]
        d0, d1 = (today - timedelta(days=7)).isoformat(), (today + timedelta(days=13)).isoformat()
        lessons = []
        for r in c.execute(f"SELECT class_id, date, raw FROM lessons WHERE date >= ? AND date <= ? "
                           f"AND class_id IN ({q})", (d0, d1 + "~", *ids)):
            try:
                raw = json.loads(r["raw"] or "{}")
            except ValueError:
                raw = {}
            x = mesta_pravilo.zanyatie_iz_mk(raw)
            x["class_id"], x["date"] = r["class_id"], str(r["date"] or "")[:10]
            lessons.append(x)
        try:
            managers = {r["id"]: r["name"] or "" for r in c.execute("SELECT id, name FROM managers")}
        except Exception:  # noqa: BLE001 — без таблицы педагогов правило считает так же, без подписи
            managers = {}
        return classes, joins, lessons, managers, db.udalennye("joins", c)

    if conn is not None:
        dannye = _chitat(conn)
    else:
        with db.get_conn() as c:
            dannye = _chitat(c)
    classes, joins, lessons, managers, ud = dannye
    return mesta_pravilo.poschitat(classes, joins, lessons, today, managers=managers, udaleno_joins=ud)


def _primechaniya(polnoe: str) -> dict:
    """Пометки админам по имени группы (MERGE / WAITLIST / HOLD) — на счёт мест не влияют."""
    n = "2627_" + polnoe
    return {"замена": next((v for k, v in MERGE.items() if k in n), ""),
            "лист": next((v for k, v in WAITLIST.items() if k in n), ""),
            "пауза": next((v for k, v in HOLD.items() if k in n), "")}


def _poryadok(name: str):
    for i, p in enumerate(ORDER):
        if name.startswith(p) or p in name[:12]:
            return (i, name)
    return (len(ORDER), name)


def dlya_podbora(today: date | None = None) -> dict[int, dict]:
    """Места по правилу для страницы «Подбор группы»: {class_id: {...}}.

    enrolled — занято + пробники; free — сколько остаётся (не меньше нуля); vid:
    «obychnaya» — открытая группа из статистики; «po_dnyam» — сад и нулевой (free — мест в
    самый свободный день, dni — по дням); «zakryta» — закрыта для записи (free = 0, why);
    «vne» — вне статистики (логопеды): тот же счёт, в итог по центру не входит.
    Ключ 0 — итог по центру, как на «Местах»."""
    s = svod(today)
    reg = [g for g in s["groups"] if "days" not in g]
    out: dict[int, dict] = {0: {"vid": "itog", **s["tot"], "groups": len(reg),
                                "full": sum(1 for g in reg if g["left"] <= 0),
                                "empty": sum(1 for g in reg if not g["busy"] + g["prob"])}}
    for g in s["groups"]:
        if "days" in g:
            out[g["cid"]] = {"vid": "po_dnyam", "enrolled": g["busy"] + g["prob"], "capacity": g["cap"],
                             "free": max([max(0, v["left"]) for v in g["days"].values()] or [0]),
                             "dni": mesta_pravilo.svobodno_po_dnyam(g["days"]), "why": ""}
        else:
            out[g["cid"]] = {"vid": "obychnaya", "enrolled": g["busy"] + g["prob"], "capacity": g["cap"],
                             "free": max(0, g["left"]), "dni": "", "why": ""}
    for g in s["closed"]:
        out[g["cid"]] = {"vid": "zakryta", "enrolled": g["busy"] + g["prob"], "capacity": g["cap"],
                         "free": 0, "dni": "", "why": g["why"]}
    for g in s["vne"]:
        out[g["cid"]] = {"vid": "vne", "enrolled": g["busy"] + g["prob"], "capacity": g["cap"],
                         "free": max(0, g["left"]), "dni": "", "why": g["why"]}
    return out


def rows() -> list[dict]:
    with db.get_conn() as conn:
        cls = conn.execute("SELECT id, name, max_students FROM classes WHERE name LIKE '2627_%' "
                           "AND (status IS NULL OR status = 'opened')").fetchall()   # удалённые/архивные группы CRM в таблице остаются
        # 13.11.2026: «оплатили» — дети «Учится» с оплаченным абонементом на текущий месяц
        # (oplatili_mesyac), а не «платил хоть раз с июня». Счёт мест в этой функции прежний:
        # на него опираются подсказки «Линии» и рутины (/api/mesta) — см. шапку модуля.
        opl = oplatili_mesyac(conn)
        out = []
        for c in cls:
            n = c["name"] or ""
            if "Заявк" in n or "лагер" in n.lower() or "летн" in n.lower() or n.startswith("2627_ЛГ"):
                continue   # логопеды — индивидуальные слоты, в блок мест не входят (лист ожидания)
            live = conn.execute(
                "SELECT COUNT(*) FROM joins WHERE class_id=? AND status_id IN (%s)" % ",".join("?" * len(LIVE)),
                (c["id"], *LIVE)).fetchone()[0]
            cap = c["max_students"] or 8
            short = re.sub(r"^2627_", "", n)
            for k, v in CAP_OVERRIDE.items():
                if short.startswith(k):
                    cap = v
            u_uch = {r[0] for r in conn.execute(
                "SELECT user_id FROM joins WHERE class_id=? AND status_id=?", (c["id"], ST_UCHITSYA))}
            paid = len(_oplata_gruppy(opl, c["id"], u_uch)[0])
            note = next((v for k, v in MERGE.items() if k in n), "")
            wait = next((v for k, v in WAITLIST.items() if k in n), "")
            hold = next((v for k, v in HOLD.items() if k in n), "")
            out.append({"id": c["id"], "name": short, "cap": cap, "live": live, "paid": paid,
                        "free": max(cap - live, 0), "merge": note, "wait": wait, "hold": hold})
        def key(r):
            for i, p in enumerate(ORDER):
                if r["name"].startswith(p) or p in r["name"][:12]:
                    return (i, r["name"])
            return (len(ORDER), r["name"])
        out.sort(key=key)
        return out


# Статусы записи в группе: что означает каждая колонка таблицы мест.
ST_UCHITSYA = 2          # «Учится» — ребёнок ходит в группу
ST_ZAPISAN = (83760, 58132)   # «Подтвердил заявку» и «Записался на пробное»
ST_BYL = 58131           # «Посетил пробное» — пришёл, абонемент ещё не купил


def tablica(today: date | None = None) -> dict:
    """Места по группам с разбивкой: сколько ходят, сколько ждём на пробное,
    сколько мест реально свободно.

    Свободное место — это норма минус ВСЕ живые записи: ребёнок, записанный
    на пробное, место уже занимает, иначе в группу запишут больше, чем в неё
    помещается. Отдельно показываем «были на пробном, но не оплатили» — это
    не новички, это несостоявшиеся оплаты, за ними идёт работа в /voronka.

    13.11.2026, Борис (решение М2): счёт — единое правило app/mesta_pravilo.py, «как в
    документе владельца»: без закрытых для записи групп, сад и нулевой — по дням.
    Что изменилось против прежнего счёта этой страницы (на копии базы 12.11 было 146
    свободных в 53 группах, стало 93 в 45 — таблицы в отчёте C1):
      · «занято» — дети в статусе «Учится»; «пробные» — «Записался на пробное» и
        «Посетил пробное»; «Подтвердил заявку» место больше не держит (у него нет даты
        занятия — так считает документ); свободно = мест − занято − пробные;
      · закрытые для записи группы — отдельным списком «закрытые», в итог не входят;
      · мини-сад и нулевой класс — отдельным блоком «по_дням» (потолок на день), в итог
        не входят; прежде сад считался списком до 20 детей, нулевой — до 10;
      · мест всего — вместимость из МойКласса, без поправок CAP_OVERRIDE;
      · запись, удалённая в МойКлассе, места не занимает; один ребёнок — одно место;
      · «оплатили» — дети «Учится» с оплаченным абонементом на текущий месяц
        (oplatili_mesyac); «ходят_без_оплаты» = «Учится» минус они. Поэтому
        ходят = оплатили + ходят_без_оплаты в каждой строке.
    Ключи «итого.свободно / занято / пробные» совпадают с документом до единицы.
    """
    today = today or _segodnya()
    with db.get_conn() as conn:
        sv = svod(today, conn)
        opl = oplatili_mesyac(conn, today)
        # заявки к логопеду: «Подтвердил заявку» в группах «ЛГ …_Заявки» — ждут окно у педагога.
        # До 13.11 они стояли в строке логопеда как «записаны на пробное»; правило заявочные
        # группы не считает, поэтому показываем отдельной строкой, чтобы число не пропало.
        ud = db.udalennye("joins", conn)
        log_zayavki = {r["user_id"] for r in conn.execute(
            "SELECT j.id, j.user_id FROM joins j JOIN classes c ON c.id = j.class_id "
            "WHERE c.name LIKE '2627_ЛГ%' AND c.name LIKE '%аявк%' AND j.status_id = ? "
            "AND (c.status IS NULL OR c.status = 'opened')", (mesta_pravilo.PODTVERDIL,)) if r["id"] not in ud}
    # ребёнок на двух предметах занимает два места, но ребёнок он один:
    # места считаем по записям, детей — по карточкам, иначе цель 311 врёт
    deti = {"ходят": set(), "оплатили": set(), "записаны_на_пробное": set(), "ходят_без_оплаты": set()}

    def stroka(g: dict) -> dict:
        """Общие поля строки группы: счёт по правилу + оплата месяца + пометки админам."""
        u_uch, u_prob, u_byl = g["_uch"], g["_prob"], g["_byl"]
        u_opl, u_chast = _oplata_gruppy(opl, g["cid"], u_uch)
        # «Оплатили» и «ходят» считаются по разным признакам (абонемент
        # против статуса записи), поэтому вычитать одно число из другого
        # нельзя: 17.09 так вышло «минус тринадцать неплательщиков».
        # Долг — это ребёнок со статусом «Учится», у которого на ЭТУ
        # группу нет оплаченного абонемента сезона.
        # 13.11.2026: теперь «оплатили» — подмножество «Учится» (оплаченный абонемент на
        # текущий месяц), и вычитать можно: долг = «Учится» без оплаты этого месяца.
        u_dolg = u_uch - u_opl
        deti["ходят"] |= u_uch
        deti["записаны_на_пробное"] |= u_prob
        deti["оплатили"] |= u_opl
        deti["ходят_без_оплаты"] |= u_dolg
        return {
            "id": g["cid"], "name": g["polnoe"], "предмет": g["dir"], "педагог": g["teacher"],
            "мест_всего": g["cap"], "занято": g["busy"], "ходят": g["busy"], "пробные": g["prob"],
            "оплатили": len(u_opl), "частично": len(u_chast), "ходят_без_оплаты": len(u_dolg),
            "записаны_на_пробное": len(u_prob - u_byl), "были_на_пробном": len(u_byl),
            "подтвердили_заявку": g["podtv"],      # справка: даты занятия нет — место не держим
            "остаётся": g["left"], "свободно": max(g["left"], 0), "перебор": max(-g["left"], 0),
            **_primechaniya(g["polnoe"]),
        }

    out = sorted((stroka(g) for g in sv["groups"] if "days" not in g), key=lambda r: _poryadok(r["name"]))
    fields = ("мест_всего", "занято", "ходят", "пробные", "оплатили", "частично", "ходят_без_оплаты",
              "записаны_на_пробное", "были_на_пробном", "подтвердили_заявку", "свободно", "перебор")
    itogo = {f: sum(r[f] for r in out) for f in fields}
    itogo["переполнено"] = itogo.pop("перебор")
    itogo["остаток"] = sv["tot"]["left"]
    # по предметам — чтобы видеть, где набор идёт, а где стоит
    # (13.11.2026: направления и порядок — как в документе владельца)
    по_предметам = {}
    por = mesta_pravilo.PORYADOK_NAPR
    for r in sorted(out, key=lambda r: por.index(r["предмет"]) if r["предмет"] in por else len(por)):
        d = по_предметам.setdefault(r["предмет"], {f: 0 for f in fields})
        for f in fields:
            d[f] += r[f]
        d["групп"] = d.get("групп", 0) + 1
    # мини-сад и нулевой класс — по дням недели, в итог по центру не входят
    по_дням = []
    for g in sv["groups"]:
        if "days" not in g:
            continue
        r = stroka(g)
        r["дни"] = {w: {"мест": v["cap"], "занято": v["busy"], "пробные": v["prob"], "остаётся": v["left"]}
                    for w, v in g["days"].items()}
        r["свободно_мест_дней"] = sum(max(0, v["left"]) for v in g["days"].values())
        r["свободно_по_дням"] = mesta_pravilo.svobodno_po_dnyam(g["days"])
        r["потолок"] = ("пн и ср — 6 мест (комнату 3 занимает логопед), вт, чт, пт — 8"
                        if g["dir"] == "Мини-сад" else f"{g['cap']} мест в день")
        по_дням.append(r)
    по_дням.sort(key=lambda r: r["предмет"] != "Мини-сад")
    # закрытые для записи: в статистику не входят, но дети в них ходят и платят
    закрытые = []
    for g in sorted(sv["closed"], key=lambda g: g["name"]):
        r = stroka(g)
        r["почему"] = g["why"]
        закрытые.append(r)
    # Группы сезона, которых в таблице мест нет: логопеды (индивидуальные
    # слоты), заявочные и летние. Дети оттуда оплату внесли, и на /voronka
    # к цели 311 они считаются — иначе две страницы дают разные числа и
    # непонятно, какому верить.
    # Логопед — индивидуальные слоты, места в них не считаются, но детей
    # там столько же, сколько в иной группе: 18.09 Борис спросил «а где
    # логопед?». Поэтому отдельным разделом, с тем же счётом.
    вне, logoped = [], []
    log_deti = {"ходят": set(), "пробные": set(), "оплатили": set()}
    for g in sorted(sv["vne"], key=lambda g: g["polnoe"]):
        if g["dir"] != "Логопед":
            continue
        u_opl, _ = _oplata_gruppy(opl, g["cid"], g["_uch"])
        log_deti["ходят"] |= g["_uch"]
        log_deti["пробные"] |= g["_prob"]
        log_deti["оплатили"] |= u_opl
        if g["_uch"] or g["_prob"]:
            logoped.append({"name": g["polnoe"], "ходят": g["busy"],
                            "записаны_на_пробное": g["prob"], "оплатили": len(u_opl)})
        if u_opl:
            вне.append({"name": g["polnoe"], "оплатили": len(u_opl)})
    itogo["детей_вне_таблицы"] = len(log_deti["оплатили"])
    itogo["детей_ходят"] = len(deti["ходят"])
    # 13.11.2026: дети делятся без пересечений — «оплатили месяц по всем своим группам» и
    # «не оплачен хотя бы один предмет»; вместе это все, кто ходит (раньше ребёнок с двумя
    # предметами мог попасть в обе карточки, и они не складывались).
    itogo["детей_без_оплаты"] = len(deti["ходят_без_оплаты"])
    itogo["детей_оплатили"] = len(deti["ходят"] - deti["ходят_без_оплаты"])
    itogo["детей_на_пробное"] = len(deti["записаны_на_пробное"] - deti["ходят"])
    itogo["логопед_ходят"] = len(log_deti["ходят"])
    itogo["логопед_на_пробное"] = len(log_deti["пробные"])
    itogo["логопед_заявки"] = len(log_zayavki)
    # «Всего по центру» — то, что владелец спрашивает первым: группы плюс
    # индивидуальные слоги логопеда, без двойного счёта одного ребёнка
    itogo["всего_детей_ходят"] = len(deti["ходят"] | log_deti["ходят"])
    itogo["всего_детей_на_пробное"] = len(
        deti["записаны_на_пробное"] | log_deti["пробные"])
    return {"группы": out, "итого": itogo, "по_предметам": по_предметам,
            "по_дням": по_дням, "закрытые": закрытые,
            "правило": mesta_pravilo.PRAVILO, "правило_подробно": mesta_pravilo.PRAVILO_PODROBNO,
            "месяц": opl["mesyac"], "сегодня": today.isoformat(),
            "логопед": logoped,
            "вне_таблицы": sorted(вне, key=lambda r: -r["оплатили"]),
            "групп": len(out),
            "обновлено": db.get_state("last_light_sync") or db.get_state("last_sync") or ""}


SUBJECTS = (
    ("Первая школа", "Подготовка к школе"), ("ПШ", "Подготовка к школе"),
    ("АЯ", "Английский"), ("Английский", "Английский"),
    ("Музыка и речь", "Раннее развитие"), ("РР", "Раннее развитие"),
    ("ИЗО", "ИЗО"), ("МА", "Ментальная арифметика"), ("ШАХ", "Шахматы"),
    ("Робот", "Робототехника"), ("Мини-сад", "Мини-сад"), ("Нулевой", "Нулевой класс"),
    ("Логопед", "Логопед"), ("ЛГ", "Логопед"),
)


def _subject(short_name: str) -> str:
    for pref, title in SUBJECTS:
        if short_name.startswith(pref):
            return title
    return short_name.split("_")[0]


def block() -> str:
    # 13.11.2026, Борис (М2): блок считает тем же правилом, что страница «Места» и документ
    # владельца (svod / app/mesta_pravilo.py). До этого дня заголовок блока говорил
    # «свободно 146 в 53 группах», а документ в тот же день — 78: блок считал все группы
    # подряд, сад — списком до 20 детей. «Оплатили» — оплата текущего месяца.
    try:
        today = _segodnya()
        with db.get_conn() as conn:
            sv = svod(today, conn)
            opl = oplatili_mesyac(conn, today)
    except Exception as e:
        return (f"<div class='card' style='border-left:4px solid #F59C00;margin:14px 0'><b>Свободные места сейчас</b> — "
                f"не посчитались: {html.escape(str(e))}</div>")
    if not (sv["groups"] or sv["closed"]):
        return ""
    rs = []
    for g in sv["groups"] + sv["closed"]:
        pr = _primechaniya(g["polnoe"])
        rs.append({"name": g["polnoe"], "cap": g["cap"], "live": g["busy"] + g["prob"],
                   "paid": len(_oplata_gruppy(opl, g["cid"], g["_uch"])[0]),
                   "free": max(g["left"], 0), "merge": pr["замена"], "wait": pr["лист"], "hold": pr["пауза"],
                   "why": g.get("why") or "",
                   "dni": mesta_pravilo.svobodno_po_dnyam(g["days"]) if "days" in g else ""})
    rs.sort(key=lambda r: _poryadok(r["name"]))

    def td(r):
        if r["why"]:
            st = (f"<span style='color:#b00010;font-weight:700'>закрыта для записи</span> "
                  f"<span style='color:#6c6a86'>{html.escape(r['why'])} — в счёт не входит</span>")
        elif r["merge"]:
            st = f"<span style='color:#b00010;font-weight:700'>не записывать</span> <span style='color:#6c6a86'>{html.escape(r['merge'])}</span>"
        elif r["dni"]:
            st = (f"<span style='color:#4e8a12;font-weight:700'>по дням: {html.escape(r['dni'])}</span> "
                  f"<span style='color:#6c6a86'>— в общий счёт не входит</span>")
        elif r["wait"]:
            st = f"<span style='color:#a35f00;font-weight:700'>полная</span> <span style='color:#6c6a86'>{html.escape(r['wait'])}</span>"
        elif r.get("hold"):
            st = f"<span style='color:#b00010;font-weight:700'>⛔ пауза</span> <span style='color:#6c6a86'>{html.escape(r['hold'])}</span>"
        elif r["free"] >= 4:
            st = f"<span style='color:#E30613;font-weight:800'>{r['free']} мест — сюда первыми</span>"
        elif r["free"] > 0:
            st = f"<span style='color:#4e8a12;font-weight:700'>{r['free']} " + ("место" if r["free"] == 1 else "места") + "</span>"
        else:
            st = "<span style='color:#6c6a86'>мест нет</span>"
        return (f"<tr><td style='font-size:13px'>{html.escape(r['name'])}</td>"
                f"<td class='num' style='white-space:nowrap'>{r['live']} / {r['cap']}</td>"
                f"<td class='num' style='white-space:nowrap;color:{'#4e8a12' if r['paid'] else '#6c6a86'}'>{r['paid']}</td>"
                f"<td style='font-size:13px'>{st}</td></tr>")
    # 22.09.2026, аудит: здесь считалось без сливаемых групп, а в шапке /mesta —
    # со всеми, и две страницы под одной подписью «в 49 группах» показывали 110
    # и 119. Считаем всё, а разницу называем словами — это разные вопросы:
    # «сколько мест в центре» и «куда сегодня можно записать».
    # 13.11.2026: число в заголовке — итог правила (то же, что на /mesta и в документе
    # владельца); места в группах с пометкой «не записывать» называем отдельно, как и раньше.
    tot = sv["tot"]
    reg = [g for g in sv["groups"] if "days" not in g]
    free_merge = sum(r["free"] for r in rs if r["merge"] and not r["why"] and not r["dni"])
    free_hint = (f" · из них {free_merge} в группах с пометкой «не записывать»" if free_merge else "")
    paid_total = sum(r["paid"] for r in rs)
    return (f"<details class='card' style='border-left:4px solid #7DB928;margin:14px 0'>"
            f"<summary style='cursor:pointer;font-size:17px;font-weight:800'>Места сейчас: свободно {tot['free']} в {len(reg)} группах{free_hint} · занято {tot['busy']} · пробников {tot['prob']} · оплатили {opl['mesyac']} {paid_total}</summary>"
            f"<div style='font-size:12.5px;color:#6c6a86;margin:4px 0 8px'>Считаем так: {html.escape(mesta_pravilo.PRAVILO)} — то же число на странице <a href='/mesta'>«Места»</a> и в документе владельца. "
            f"Считается по записям CRM при каждом открытии (синхронизация раз в 5 минут). "
            f"Красным — группы, куда записываем первыми. В колонке «занято / мест» — «Учится» и пробники. "
            f"Мини-сад — пн и ср до 6 детей в день, вт, чт, пт — до 8; нулевой класс — по дням. «Оплатили» — дети с оплаченным абонементом на {opl['mesyac']}, по всем строкам таблицы.</div>"
            f"<div class='scroll'><table><tr><th>Группа</th><th class='num'>занято / мест</th><th class='num'>оплатили {opl['mesyac']}</th><th>места</th></tr>"
            + "".join(td(r) for r in rs) + "</table></div></details>")


def raskhozhdenie() -> dict:
    """Почему «оплатили» больше, чем «ходят».

    17.09 Борис: «почему в колонке ходят меньше, чем оплатили?» Два числа
    считаются по разным таблицам: «ходят» — это записи в группу со статусом
    «Учится», «оплатили» — абонементы сезона, привязанные к группе. Когда
    семья заплатила, а запись осталась в статусе «Записался на пробное»
    или её вообще нет, ребёнок платит, но в наполнении группы не виден —
    и место под него не держится. Здесь поимённо: кто оплатил и с каким
    статусом записи числится.

    13.11.2026: «оплатили» на странице «Места» теперь — дети «Учится» с оплаченным
    абонементом на текущий месяц, и больше, чем «ходят», оно быть не может. Вопрос этой
    функции остался, но уже без шума прошлых месяцев: у кого оплачен ТЕКУЩИЙ месяц
    (oplatili_mesyac), а «Учится» нет ни в одной группе этого абонемента. До правки сюда
    попадал каждый, кто платил за группу хоть раз с июня и ушёл (на копии базы 12.11 —
    127 строк из 130 были «Отказался» и «Завершил»; по оплате месяца строк 6).
    Абонемент на несколько групп — одна строка, а не по строке на группу.
    """
    today = _segodnya()
    with db.get_conn() as conn:
        cls = {}
        for c in conn.execute("SELECT id, name FROM classes WHERE name LIKE '2627_%' "
                              "AND (status IS NULL OR status = 'opened')").fetchall():
            n = c["name"] or ""
            if "Заявк" in n or "лагер" in n.lower() or "летн" in n.lower() or n.startswith("2627_ЛГ"):
                continue
            cls[c["id"]] = n
        opl = oplatili_mesyac(conn, today)
        ud = db.udalennye("joins", conn)
        st_names = {}
        try:
            for r in conn.execute("SELECT id, name FROM join_statuses").fetchall():
                st_names[r["id"]] = r["name"]
        except Exception:
            pass
        statusy: dict = {}          # (группа, ребёнок) -> статусы живых (не удалённых в МойКлассе) записей
        for r in conn.execute("SELECT id, user_id, class_id, status_id FROM joins"):
            if r["class_id"] in cls and r["id"] not in ud:
                statusy.setdefault((r["class_id"], r["user_id"]), []).append(r["status_id"])
        out, po_statusam, bylo = [], {}, set()
        for a in opl["abonementy"]:
            uid = a["uid"]
            cids = sorted(c for c in a["cids"] if c in cls)
            if not cids or any(ST_UCHITSYA in statusy.get((c, uid), ()) for c in cids):
                continue
            cid = a["main"] if a["main"] in cids else cids[0]
            if (uid, cid) in bylo:
                continue
            bylo.add((uid, cid))
            sids = statusy.get((cid, uid)) or []
            st = st_names.get(sids[0], str(sids[0])) if sids else "записи в группе нет"
            u = conn.execute("SELECT name, phone FROM users WHERE id=?", (uid,)).fetchone()
            out.append({"uid": uid, "ребёнок": (u["name"] if u else "") or str(uid),
                        "телефон": (u["phone"] if u else "") or "",
                        "группа": re.sub(r"^2627_", "", cls[cid]), "статус_записи": st})
            po_statusam[st] = po_statusam.get(st, 0) + 1
        out.sort(key=lambda r: (r["группа"], r["ребёнок"]))
    return {"всего": len(out), "по_статусам": po_statusam, "строки": out, "месяц": opl["mesyac"]}


def sostav(marker: str = "АЯ") -> dict:
    """Поимённый состав групп предмета: кто ходит и кто записан на пробное.

    21.09 владелец спросил, все ли дети с английского зашли в МойЧат, чтобы
    педагоги могли с ними переписываться. Ответ требует не счётчиков, а
    списка карточек: по каждому ребёнку дальше смотрим в МойКлассе, включён
    ли личный кабинет и заходили ли в него.
    """
    with db.get_conn() as conn:
        cls = conn.execute(
            "SELECT id, name FROM classes WHERE name LIKE ? "
            "AND (status IS NULL OR status = 'opened') ORDER BY name",
            (f"2627_{marker}%",)).fetchall()
        out, deti = [], {}
        for c in cls:
            if "Заявк" in (c["name"] or ""):
                continue
            rows = conn.execute(
                "SELECT j.user_id, j.status_id, u.name, u.phone, u.email "
                "FROM joins j LEFT JOIN users u ON u.id = j.user_id "
                "WHERE j.class_id=? AND j.status_id IN (?,?,?,?)",
                (c["id"], ST_UCHITSYA, *ST_ZAPISAN, ST_BYL)).fetchall()
            gruppa = []
            for r in rows:
                kto = {"uid": r["user_id"], "имя": r["name"] or "", "телефон": r["phone"] or "",
                       "почта": r["email"] or "",
                       "статус": {ST_UCHITSYA: "ходит", ST_BYL: "был на пробном"}.get(
                           r["status_id"], "записан на пробное")}
                gruppa.append(kto)
                deti.setdefault(r["user_id"], kto)
            if gruppa:
                out.append({"группа": c["name"], "id": c["id"], "детей": len(gruppa), "дети": gruppa})
    return {"предмет": marker, "групп": len(out), "детей_всего": len(deti),
            "группы": out, "дети": list(deti.values())}


def razrez() -> dict:
    """Поимённый разрез по каждой группе сезона, включая логопедов.

    27.09 владелец: «актуализация в разрезе каждого предмета и каждой группы
    (в том числе по логопедам): ходит (куплен абонемент) — записан на пробное
    (указана дата пробного) — мест всего в группе». Счётчики уже есть на /mesta,
    здесь — имена, чтобы по каждой строке можно было сразу действовать.

    Ходит — статус «Учится» И оплаченный абонемент сезона на эту группу.
    «Учится» без абонемента — отдельный столбец: это долг, а не ученик.
    Дата пробного — ближайшая запись ребёнка на занятие этой группы начиная
    со вчерашнего дня; если впереди записи нет — последняя прошедшая с
    пометкой «прошла», это сигнал, что пробное надо переназначить.
    """
    from datetime import date as _date, timedelta as _td
    today = _date.today()
    since = (today - _td(days=1)).isoformat()
    with db.get_conn() as conn:
        cls = conn.execute("SELECT id, name, max_students FROM classes WHERE name LIKE '2627_%' "
                           "AND (status IS NULL OR status = 'opened')").fetchall()
        paid_idx = _paid_by_class(conn)

        def trial_date(uid: int, cid: int, past_only: bool = False) -> str:
            if not past_only:
                r = conn.execute(
                    "SELECT MIN(l.date) FROM lesson_records lr JOIN lessons l ON l.id=lr.lesson_id "
                    "WHERE lr.user_id=? AND l.class_id=? AND l.date>=?", (uid, cid, since)).fetchone()
                if r and r[0]:
                    return r[0][:10]
            r = conn.execute(
                "SELECT MAX(l.date) FROM lesson_records lr JOIN lessons l ON l.id=lr.lesson_id "
                "WHERE lr.user_id=? AND l.class_id=? AND l.date<?", (uid, cid, today.isoformat())).fetchone()
            return (r[0][:10] + (" прошла" if not past_only else "")) if r and r[0] else ""

        groups = []
        for c in cls:
            n = c["name"] or ""
            if "Заявк" in n or "лагер" in n.lower() or "летн" in n.lower():
                continue
            rows = conn.execute(
                "SELECT j.user_id, j.status_id, u.name FROM joins j LEFT JOIN users u ON u.id=j.user_id "
                "WHERE j.class_id=? AND j.status_id IN (?,?,?,?)",
                (c["id"], ST_UCHITSYA, *ST_ZAPISAN, ST_BYL)).fetchall()
            paid = paid_idx.get(c["id"], set())
            hodit, dolg, prob, byl = [], [], [], []
            for r in rows:
                nm = r["name"] or f"№{r['user_id']}"
                if r["status_id"] == ST_UCHITSYA:
                    (hodit if r["user_id"] in paid else dolg).append(nm)
                elif r["status_id"] == ST_BYL:
                    byl.append({"имя": nm, "дата": trial_date(r["user_id"], c["id"], past_only=True)})
                else:
                    prob.append({"имя": nm, "дата": trial_date(r["user_id"], c["id"])})
            short = re.sub(r"^2627_", "", n)
            cap = c["max_students"] or (1 if short.startswith("ЛГ") else 8)
            for k, v in CAP_OVERRIDE.items():
                if short.startswith(k):
                    cap = v
            prob.sort(key=lambda x: x["дата"] or "9999")
            zanyato = len(hodit) + len(dolg) + len(prob) + len(byl)
            groups.append({
                "id": c["id"], "name": short, "предмет": _subject(short), "мест": cap,
                "ходит": sorted(hodit), "без_оплаты": sorted(dolg), "пробное": prob,
                "были": sorted(byl, key=lambda x: x["дата"]),
                "свободно": max(cap - zanyato, 0), "перебор": max(zanyato - cap, 0),
                "пометка": next((v for k, v in {**MERGE, **WAITLIST, **HOLD}.items() if k in n), ""),
            })

    def key(g):
        for i, p in enumerate(ORDER + ("ЛГ",)):
            if g["name"].startswith(p) or p in g["name"][:12]:
                return (i, g["name"])
        return (len(ORDER) + 1, g["name"])
    groups.sort(key=key)
    # логопед Марина и логопед Елена — отдельными «предметами», у них разные графики
    for g in groups:
        if g["name"].startswith("ЛГ"):
            g["предмет"] = "Логопед " + g["name"].split("_")[0].replace("ЛГ", "").strip()
    predmety: dict[str, dict] = {}
    for g in groups:
        p = predmety.setdefault(g["предмет"], {"групп": 0, "мест": 0, "ходит": 0, "без_оплаты": 0,
                                               "пробное": 0, "были": 0, "свободно": 0, "сверх": 0,
                                               "группы": []})
        p["групп"] += 1
        p["мест"] += g["мест"]
        for f in ("ходит", "без_оплаты", "пробное", "были"):
            p[f] += len(g[f])
        p["свободно"] += g["свободно"]
        # «сверх» — дети сверх нормы в переполненных группах. Без этого
        # столбца «мест − занято» по предмету не сходится со «свободно»
        # (27.09 Борис: ПШ 120−54−3−10−0 = 53, а на странице 54)
        p["сверх"] += g["перебор"]
        p["группы"].append(g)
    itogo = {f: sum(p[f] for p in predmety.values())
             for f in ("групп", "мест", "ходит", "без_оплаты", "пробное", "были", "свободно", "сверх")}
    return {"предметы": predmety, "итого": itogo, "сегодня": today.isoformat(),
            "обновлено": db.get_state("last_light_sync") or db.get_state("last_sync") or ""}
