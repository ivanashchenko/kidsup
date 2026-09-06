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
MERGE = {  # подстрока имени группы -> куда вместо
    "ПШ_вт-пт_16:00": "→ Гр1 вт-чт 16:00", "ПШ_вт-пт_17:00": "→ Гр2 вт-чт 17:00",
    "ПШ_чт 19:00 + сб 11:00": "→ Гр12 пн 19:00 + сб 10:00", "ПШ_ср-пт_18:00": "→ Гр10 вт-пт 18:00",
    "АЯ_вт-чт_16:00": "→ Гр1 пн-ср 16:00", "ИЗО_Группа 1": "→ ИЗО Гр2 17:00", "ИЗО_Группа 4": "→ ИЗО Гр2 17:00",
    "Первая школа_Группа 4": "→ вс 11:00 (Гр5)",
}
WAITLIST = {  # переполненные: не дописывать, лист новой группы
    "АЯ_вт-чт_17:00": "лист Starters 5–8 вт-чт 16:00", "АЯ_пн-ср_18:00": "лист Starters 5–8 вт-чт 16:00",
    "ПШ_пн-чт_18:00": "лист ПШ2 пн-чт 19:00", "Музыка и речь_Группа 3": "лист МиР 2,2–3 ср-сб 12:45",
    "Музыка и речь_Группа 6": "лист МиР 2,2–3 ср-сб 12:45", "Первая школа_Группа 3": "лист Первая школа 2–3 вт-чт 13:00",
}
ORDER = ("ПШ", "АЯ", "РР", "ИЗО", "МА", "ШАХ", "Робот", "Мини-сад", "Нулевой")
# 06.09 Борис: в мини-саду и нулевом классе по факту максимум 10 детей (в CRM стоит 15);
# больше — открывается ещё одна группа (например, 4–5 лет)
CAP_OVERRIDE = {"Мини-сад": 10, "Нулевой класс": 10}
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
        cls = conn.execute("SELECT id, name, max_students FROM classes WHERE name LIKE '2627_%'").fetchall()
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
            out.append({"id": c["id"], "name": short, "cap": cap, "live": live, "paid": paid,
                        "free": max(cap - live, 0), "merge": note, "wait": wait})
        def key(r):
            for i, p in enumerate(ORDER):
                if r["name"].startswith(p) or p in r["name"][:12]:
                    return (i, r["name"])
            return (len(ORDER), r["name"])
        out.sort(key=key)
        return out


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
    free_total = sum(r["free"] for r in rs if not r["merge"])
    live_total = sum(r["live"] for r in rs)
    paid_total = sum(r["paid"] for r in rs)
    return (f"<details class='card' style='border-left:4px solid #7DB928;margin:14px 0'>"
            f"<summary style='cursor:pointer;font-size:17px;font-weight:800'>Места сейчас: свободно {free_total} в {len(rs)} группах · живых записей {live_total} · оплатили {paid_total}</summary>"
            f"<div style='font-size:12.5px;color:#6c6a86;margin:4px 0 8px'>Считается по записям CRM при каждом открытии (синхронизация раз в 5 минут). "
            f"Красным — группы, куда записываем первыми; «не записывать» — сливаются 14–18.09, вместо них соседняя группа. Мини-сад и нулевой класс — норма 10 (больше — открываем ещё группу).</div>"
            f"<div class='scroll'><table><tr><th>Группа</th><th class='num'>живых / норма</th><th class='num'>оплатили</th><th>места</th></tr>"
            + "".join(td(r) for r in rs) + "</table></div></details>")
