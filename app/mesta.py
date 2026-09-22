"""Живой блок «Свободные места сейчас» для страниц плана дня.

06.09 Борис: «страницы с планами обновляются по наполнению?» — до этого
числа мест в плане были статичными (выгрузка на момент сборки). Здесь
считаем по таблицам сервера, которые лёгкий синк обновляет каждые 5 минут:
classes (норма max_students) и joins (живые записи). Только чтение.

Живая запись — статус «учится», «записался на пробное», «подтвердил заявку»,
«посетил пробное». Группы сезона — имя начинается с «2627_», без заявочных
(«_Заявки», «Заявки через»). Группы, которые сливаем (решение по анализу
/base/gruppy_reshenia), помечаем «не записывать» — их список в MERGE.
"""
from __future__ import annotations

import html
import re

from . import db

LIVE = (2, 58132, 83760, 58131)
# 08.09 решение Бориса: чистых слияний нет — только замены (на место слабой группы в тот же слот
# встаёт востребованная). «Не записывать» осталось у двух групп, которые заменяются; ПШ Гр8/Гр9/Гр14,
# ИЗО Гр1/Гр4 — добирать до 30.09. Подробно: /base/kabinety_zamena_0809.
MERGE = {  # подстрока имени группы -> куда вместо
    "АЯ_вт-чт_16:00": "с 22.09 → Starters 5–8 в этом слоте; детей 8–12 → Гр1 пн-ср 16:00 (или Илья 15:00)",
    "Первая школа_Группа 4": "→ вс 11:00 (Гр5); слот вс 10:00 → ПШ1 по 4 предоплатам",
}
WAITLIST = {  # переполненные: не дописывать, лист новой группы
    "АЯ_вт-чт_17:00": "лист Starters 5–8 вт-чт 16:00", "АЯ_пн-ср_18:00": "лист Starters 5–8 вт-чт 16:00",
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
CAP_OVERRIDE = {"Мини-сад": 10, "Нулевой класс": 10, "ИЗО_Группа 3": 10}
SEASON_SELL_FROM = "2026-06-01"


def _paid_by_class(conn) -> dict[int, set[int]]:
    """class_id -> {user_id} с оплаченным абонементом сезона (sellDate с июня, payed>0).
    Абонемент привязан к группе через classIds/mainClassId; синк — каждые 5 минут."""
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


def rows() -> list[dict]:
    with db.get_conn() as conn:
        cls = conn.execute("SELECT id, name, max_students FROM classes WHERE name LIKE '2627_%' "
                           "AND (status IS NULL OR status = 'opened')").fetchall()   # удалённые/архивные группы CRM в таблице остаются
        paid_idx = _paid_by_class(conn)
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
            paid = len(paid_idx.get(c["id"], set()))
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


def tablica() -> dict:
    """Места по группам с разбивкой: сколько ходят, сколько ждём на пробное,
    сколько мест реально свободно.

    Свободное место — это норма минус ВСЕ живые записи: ребёнок, записанный
    на пробное, место уже занимает, иначе в группу запишут больше, чем в неё
    помещается. Отдельно показываем «были на пробном, но не оплатили» — это
    не новички, это несостоявшиеся оплаты, за ними идёт работа в /voronka.
    """
    with db.get_conn() as conn:
        cls = conn.execute("SELECT id, name, max_students FROM classes WHERE name LIKE '2627_%' "
                           "AND (status IS NULL OR status = 'opened')").fetchall()
        paid_idx = _paid_by_class(conn)
        out = []
        # ребёнок на двух предметах занимает два места, но ребёнок он один:
        # места считаем по записям, детей — по карточкам, иначе цель 311 врёт
        deti = {"ходят": set(), "оплатили": set(), "записаны_на_пробное": set()}
        for c in cls:
            n = c["name"] or ""
            if "Заявк" in n or "лагер" in n.lower() or "летн" in n.lower() or n.startswith("2627_ЛГ"):
                continue
            def ids(states):
                q = ",".join("?" * len(states))
                return {r[0] for r in conn.execute(
                    f"SELECT user_id FROM joins WHERE class_id=? AND status_id IN ({q})",
                    (c["id"], *states))}
            u_uch, u_zap, u_byl = ids((ST_UCHITSYA,)), ids(ST_ZAPISAN), ids((ST_BYL,))
            uch, zap, byl = len(u_uch), len(u_zap), len(u_byl)
            u_paid = paid_idx.get(c["id"], set())
            # «Оплатили» и «ходят» считаются по разным признакам (абонемент
            # против статуса записи), поэтому вычитать одно число из другого
            # нельзя: 17.09 так вышло «минус тринадцать неплательщиков».
            # Долг — это ребёнок со статусом «Учится», у которого на ЭТУ
            # группу нет оплаченного абонемента сезона.
            u_dolg = u_uch - u_paid
            deti["ходят"] |= u_uch
            deti["записаны_на_пробное"] |= u_zap
            deti["оплатили"] |= u_paid
            deti.setdefault("ходят_без_оплаты", set()).update(u_dolg)
            cap = c["max_students"] or 8
            short = re.sub(r"^2627_", "", n)
            for k, v in CAP_OVERRIDE.items():
                if short.startswith(k):
                    cap = v
            out.append({
                "id": c["id"], "name": short, "предмет": _subject(short), "мест_всего": cap,
                "ходят": uch, "оплатили": len(u_paid),
                "ходят_без_оплаты": len(u_dolg),
                "записаны_на_пробное": zap, "были_на_пробном": byl,
                "занято": uch + zap + byl, "свободно": max(cap - uch - zap - byl, 0),
                "перебор": max(uch + zap + byl - cap, 0),
                "замена": next((v for k, v in MERGE.items() if k in n), ""),
                "лист": next((v for k, v in WAITLIST.items() if k in n), ""),
                "пауза": next((v for k, v in HOLD.items() if k in n), ""),
            })

        def key(r):
            for i, p in enumerate(ORDER):
                if r["name"].startswith(p) or p in r["name"][:12]:
                    return (i, r["name"])
            return (len(ORDER), r["name"])
        out.sort(key=key)
        fields = ("мест_всего", "ходят", "оплатили", "ходят_без_оплаты",
                  "записаны_на_пробное", "были_на_пробном", "занято", "свободно")
        itogo = {f: sum(r[f] for r in out) for f in fields}
        # по предметам — чтобы видеть, где набор идёт, а где стоит
        по_предметам = {}
        for r in out:
            d = по_предметам.setdefault(r["предмет"], {f: 0 for f in fields})
            for f in fields:
                d[f] += r[f]
            d["групп"] = d.get("групп", 0) + 1
        # Группы сезона, которых в таблице мест нет: логопеды (индивидуальные
        # слоты), заявочные и летние. Дети оттуда оплату внесли, и на /voronka
        # к цели 311 они считаются — иначе две страницы дают разные числа и
        # непонятно, какому верить.
        # Логопед — индивидуальные слоты, места в них не считаются, но детей
        # там столько же, сколько в иной группе: 18.09 Борис спросил «а где
        # логопед?». Поэтому отдельным разделом, с тем же счётом.
        вне, logoped = [], []
        log_deti = {"ходят": set(), "пробные": set()}
        for c in cls:
            n = c["name"] or ""
            if not ("Заявк" in n or "лагер" in n.lower() or "летн" in n.lower()
                    or n.startswith("2627_ЛГ")):
                continue
            u = paid_idx.get(c["id"], set())
            if n.startswith("2627_ЛГ"):
                def ids_l(states):
                    q = ",".join("?" * len(states))
                    return {r[0] for r in conn.execute(
                        f"SELECT user_id FROM joins WHERE class_id=? AND status_id IN ({q})",
                        (c["id"], *states))}
                u_uch, u_zap = ids_l((ST_UCHITSYA,)), ids_l(ST_ZAPISAN)
                log_deti["ходят"] |= u_uch
                log_deti["пробные"] |= u_zap
                if u_uch or u_zap or u:
                    logoped.append({"name": re.sub(r"^2627_", "", n),
                                    "ходят": len(u_uch),
                                    "записаны_на_пробное": len(u_zap),
                                    "оплатили": len(u)})
            if u:
                вне.append({"name": re.sub(r"^2627_", "", n), "оплатили": len(u)})
                deti.setdefault("вне_таблицы", set()).update(u)
        itogo["детей_вне_таблицы"] = len(deti.get("вне_таблицы", set()))
        itogo["детей_ходят"] = len(deti["ходят"])
        itogo["детей_оплатили"] = len(deti["оплатили"])
        itogo["детей_на_пробное"] = len(deti["записаны_на_пробное"])
        itogo["детей_без_оплаты"] = len(deti.get("ходят_без_оплаты", set()) - deti["оплатили"])
        itogo["логопед_ходят"] = len(log_deti["ходят"])
        itogo["логопед_на_пробное"] = len(log_deti["пробные"])
        # «Всего по центру» — то, что владелец спрашивает первым: группы плюс
        # индивидуальные слоги логопеда, без двойного счёта одного ребёнка
        itogo["всего_детей_ходят"] = len(deti["ходят"] | log_deti["ходят"])
        itogo["всего_детей_на_пробное"] = len(
            deti["записаны_на_пробное"] | log_deti["пробные"])
        logoped.sort(key=lambda r: r["name"])
        return {"группы": out, "итого": itogo, "по_предметам": по_предметам,
                "логопед": logoped,
                "вне_таблицы": sorted(вне, key=lambda r: -r["оплатили"]),
                "групп": len(out), "обновлено": db.get_state("last_sync") or ""}


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
    try:
        rs = rows()
    except Exception as e:
        return (f"<div class='card' style='border-left:4px solid #F59C00;margin:14px 0'><b>Свободные места сейчас</b> — "
                f"не посчитались: {html.escape(str(e))}</div>")
    if not rs:
        return ""
    def td(r):
        if r["merge"]:
            st = f"<span style='color:#b00010;font-weight:700'>не записывать</span> <span style='color:#6c6a86'>{html.escape(r['merge'])}</span>"
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
    free_total = sum(r["free"] for r in rs)
    free_open = sum(r["free"] for r in rs if not r["merge"])
    free_hint = (f" · из них {free_total - free_open} в группах, куда сейчас не записываем"
                 if free_total != free_open else "")
    live_total = sum(r["live"] for r in rs)
    paid_total = sum(r["paid"] for r in rs)
    return (f"<details class='card' style='border-left:4px solid #7DB928;margin:14px 0'>"
            f"<summary style='cursor:pointer;font-size:17px;font-weight:800'>Места сейчас: свободно {free_total} в {len(rs)} группах{free_hint} · живых записей {live_total} · оплатили {paid_total}</summary>"
            f"<div style='font-size:12.5px;color:#6c6a86;margin:4px 0 8px'>Считается по записям CRM при каждом открытии (синхронизация раз в 5 минут). "
            f"Красным — группы, куда записываем первыми; «не записывать» — сливаются 14–18.09, вместо них соседняя группа. Мини-сад и нулевой класс — норма 10 (больше — открываем ещё группу).</div>"
            f"<div class='scroll'><table><tr><th>Группа</th><th class='num'>живых / норма</th><th class='num'>оплатили</th><th>места</th></tr>"
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
    """
    with db.get_conn() as conn:
        cls = conn.execute("SELECT id, name, max_students FROM classes WHERE name LIKE '2627_%' "
                           "AND (status IS NULL OR status = 'opened')").fetchall()
        paid_idx = _paid_by_class(conn)
        st_names = {}
        try:
            for r in conn.execute("SELECT id, name FROM join_statuses").fetchall():
                st_names[r["id"]] = r["name"]
        except Exception:
            pass
        names = {r["id"]: (r["name"] or "") for r in
                 conn.execute("SELECT id, name FROM users").fetchall()}
        phones = {r["id"]: (r["phone"] or "") for r in
                  conn.execute("SELECT id, phone FROM users").fetchall()}
        out, svod = [], {}
        for c in cls:
            n = c["name"] or ""
            if "Заявк" in n or "лагер" in n.lower() or "летн" in n.lower() or n.startswith("2627_ЛГ"):
                continue
            uch = {r[0] for r in conn.execute(
                "SELECT user_id FROM joins WHERE class_id=? AND status_id=?",
                (c["id"], ST_UCHITSYA))}
            for uid in sorted(paid_idx.get(c["id"], set()) - uch):
                row = conn.execute(
                    "SELECT status_id FROM joins WHERE class_id=? AND user_id=? LIMIT 1",
                    (c["id"], uid)).fetchone()
                sid = row["status_id"] if row else None
                st = st_names.get(sid, str(sid)) if sid else "записи в группе нет"
                out.append({"uid": uid, "ребёнок": names.get(uid, str(uid)),
                            "телефон": phones.get(uid, ""),
                            "группа": re.sub(r"^2627_", "", n), "статус_записи": st})
                svod[st] = svod.get(st, 0) + 1
        out.sort(key=lambda r: (r["группа"], r["ребёнок"]))
    return {"всего": len(out), "по_статусам": svod, "строки": out}


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
