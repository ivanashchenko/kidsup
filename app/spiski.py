# -*- coding: utf-8 -*-
"""Списки по группам для стойки: кто записан, кто оплатил, кто на пробном.

Просьба Иры 05.09: бумажные листы по группам устаревают за час, а подготовку
к школе и английский она не видит в свои смены. Страница /spiski строится из
локальной базы (лёгкий синк каждые 5 минут: группы, записи, изменённые карточки,
оплаты и абонементы за последние дни) и сама перезагружается раз в 5 минут.
Печатается как раздаточные листы: одна группа — одна карточка, пустые строки
до вместимости оставлены под ручку."""
from __future__ import annotations
import json, re, datetime as dt
from . import db

# Парсеры имён групп дублируем здесь намеренно: импорт app.main поднимает
# автопилот, а этот модуль должен быть безопасен для локального запуска.
DAY_ORDER = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
DAY_LABEL = dict(zip(DAY_ORDER, ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]))
_TIME_RE = re.compile(r"\d{1,2}:\d{2}")
_DAY_RE = re.compile(r"(?<![а-яa-z])(пн|вт|ср|чт|пт|сб|вс)(?![а-яa-z])", re.I)


def _name_days(name: str) -> list[str]:
    """«пн-чт», «ср - сб», «пт 18:00 + вс 11:00», «пн - пт» → дни занятий.
    Дефис у нас означает ДВА дня в неделю (пн и чт), а не диапазон."""
    low = (name or "").lower()
    return list(dict.fromkeys(m.group(1) for m in _DAY_RE.finditer(low)))


LIVE = (2, 58131, 58132)            # учится · посетил пробное · записан на пробное
LABEL = {2: "учится", 58131: "был на пробном", 58132: "записан на пробное"}
PAID_SINCE = "2026-08-01"   # предоплата за сентябрь шла и в начале августа


def _days_times(name: str) -> tuple[str, str]:
    days = " · ".join(DAY_LABEL[d] for d in _name_days(name)) or "—"
    times = " / ".join(_TIME_RE.findall(name)[:2]) or "—"
    return days, times


def build(day: str | None = None, subject: str | None = None) -> dict:
    """Группы 2026/27 (открытые, без буферов «Заявки») со списком детей."""
    today = dt.date.today().isoformat()
    with db.get_conn() as conn:
        classes = conn.execute("""SELECT cl.id, cl.name, cl.max_students, co.name course
                                  FROM classes cl LEFT JOIN courses co ON co.id = cl.course_id
                                  WHERE cl.name LIKE '2627%' AND cl.status = 'opened'
                                  ORDER BY cl.name""").fetchall()
        joins = conn.execute("""SELECT j.user_id, j.class_id, j.status_id, j.created_at, j.raw,
                                       u.name, u.phone
                                FROM joins j LEFT JOIN users u ON u.id = j.user_id
                                WHERE j.status_id IN (2, 58131, 58132)""").fetchall()
        paid_rows = conn.execute("SELECT DISTINCT user_id FROM payments WHERE optype='income' AND summa > 0 AND date >= ?",
                                 (PAID_SINCE,)).fetchall()
        subs = conn.execute("""SELECT user_id, raw FROM user_subscriptions
                               WHERE end_date >= ? AND begin_date <= ?""", (today, (dt.date.today() + dt.timedelta(days=14)).isoformat())).fetchall()
    paid_users = {r["user_id"] for r in paid_rows}
    sub_ok: dict[int, str] = {}      # user_id -> «абонемент до ДД.ММ» если оплачен
    sub_debt: dict[int, str] = {}    # user_id -> «абонемент создан, не оплачен»
    for r in subs:
        try:
            j = json.loads(r["raw"] or "{}")
        except Exception:
            continue
        price, payed = float(j.get("price") or 0), float(j.get("payed") or 0)
        end = (j.get("endDate") or "")[5:10].replace("-", ".")
        if price and payed >= price - 1:
            sub_ok[r["user_id"]] = f"абонемент до {end[3:]}.{end[:2]}" if end else "абонемент оплачен"
        elif price:
            sub_debt[r["user_id"]] = f"абонемент {int(price):,} ₽ не оплачен".replace(",", " ")
    by_class: dict[int, list] = {}
    for r in joins:
        by_class.setdefault(r["class_id"], []).append(r)
    out = []
    merged: dict[str, dict] = {}     # логопед: «ЛГ Марина · Сб» → одна карточка со слотами
    for c in classes:
        name = c["name"] or ""
        if "аявк" in name or name.startswith("OLD_") or "ТЕСТ" in name.upper():
            continue
        days, times = _days_times(name)
        gdays = _name_days(name)
        course = c["course"] or "?"
        is_lg = name.startswith("2627_ЛГ")
        if day and day not in gdays:
            continue
        if subject and subject.lower() not in (course or "").lower():
            continue
        kids = []
        seen = set()
        for r in sorted(by_class.get(c["id"], []), key=lambda r: (r["status_id"] != 2, r["name"] or "")):
            if r["user_id"] in seen:
                continue
            seen.add(r["user_id"])
            try:
                st = (json.loads(r["raw"] or "{}").get("stats") or {})
            except Exception:
                st = {}
            nxt = st.get("nextRecord") or ""
            paid = r["user_id"] in paid_users or r["user_id"] in sub_ok
            if paid:
                money = sub_ok.get(r["user_id"], "оплачено")
                cls = "ok"
            elif r["user_id"] in sub_debt:
                money, cls = sub_debt[r["user_id"]], "debt"
            elif r["status_id"] == 58132:
                money, cls = ("пробное " + nxt[8:10] + "." + nxt[5:7]) if nxt else "пробное, дата не стоит", "trial"
            elif r["status_id"] == 58131:
                money, cls = "был, не оплатил — дожать", "debt"
            else:
                money, cls = "учится, оплаты нет — дожать", "debt"
            kids.append({"uid": r["user_id"], "name": r["name"] or str(r["user_id"]), "phone": r["phone"] or "",
                         "status": LABEL.get(r["status_id"], str(r["status_id"])), "money": money, "cls": cls,
                         "next": nxt[5:10].replace("-", ".") if nxt else "", "last": (st.get("lastVisit") or "")[5:10].replace("-", ".")})
        cap = c["max_students"] or 8
        if is_lg:
            for k in kids:
                k["slot"] = times
                if k["cls"] == "debt":
                    k["money"], k["cls"] = "оплаты с 01.08 нет", "debt"
            key = re.sub(r"_\d{1,2}:\d{2}.*$", "", name.replace("2627_", ""))   # «ЛГ Марина_Сб»
            m = merged.setdefault(key, {"id": c["id"], "name": key, "short": key.replace("_", " · "), "course": course, "days": days, "times": "по слотам",
                                        "gdays": gdays, "cap": 0, "live": 0, "paid": 0, "trial": 0, "debt": 0, "kids": [], "lg": True})
            m["cap"] += max(cap, len(kids)); m["live"] += len(kids); m["kids"] += kids
            m["paid"] += sum(1 for k in kids if k["cls"] == "ok"); m["trial"] += sum(1 for k in kids if k["cls"] == "trial"); m["debt"] += sum(1 for k in kids if k["cls"] == "debt")
            continue
        out.append({"id": c["id"], "name": name, "short": name.replace("2627_", ""), "course": course, "days": days, "times": times,
                    "gdays": gdays, "cap": cap, "live": len(kids), "paid": sum(1 for k in kids if k["cls"] == "ok"),
                    "trial": sum(1 for k in kids if k["cls"] == "trial"), "debt": sum(1 for k in kids if k["cls"] == "debt"),
                    "kids": kids})
    for m in merged.values():
        m["kids"].sort(key=lambda k: k.get("slot", ""))
        out.append(m)
    out.sort(key=lambda g: (g["course"], DAY_ORDER.index(g["gdays"][0]) if g["gdays"] else 9, g["times"]))
    return {"groups": out, "synced": db.get_state("last_light_sync") or db.get_state("last_sync"),
            "built": dt.datetime.now().strftime("%H:%M")}


def page(day: str | None, subject: str | None, print_mode: bool) -> str:
    import html as H
    esc = lambda x: H.escape(str(x or ""))
    d = build(day, subject)
    G = d["groups"]
    courses = sorted({g["course"] for g in build()["groups"]})
    tot_live = sum(g["live"] for g in G); tot_cap = sum(g["cap"] for g in G); tot_paid = sum(g["paid"] for g in G); tot_debt = sum(g["debt"] for g in G)
    css = """<style>
    body{font:14px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;color:#1d1d3a;background:#f4f5fb;margin:0}
    .top{position:sticky;top:0;background:#312783;color:#fff;padding:10px 16px;display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;z-index:2}
    .top b{font-size:16px}.top a{color:#fff;text-decoration:none;background:rgba(255,255,255,.14);padding:4px 10px;border-radius:999px;font-size:13px}
    .top a.on{background:#7DB928}.top .st{margin-left:auto;font-size:12px;opacity:.85}
    .wrap{padding:14px;display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(420px,1fr))}
    .g{background:#fff;border-radius:12px;box-shadow:0 2px 10px rgba(49,39,131,.08);padding:12px 14px;break-inside:avoid;page-break-inside:avoid}
    .g h3{margin:0;font-size:15px;color:#312783}.g .m{color:#666;font-size:12px;margin:2px 0 8px;display:flex;gap:10px;flex-wrap:wrap}
    .g .m b{color:#1d1d3a}
    table{width:100%;border-collapse:collapse}td{padding:4px 6px;border-bottom:1px solid #e6e8f2;vertical-align:top;font-size:13px}
    td.n{width:22px;color:#999;text-align:right}td.ph{white-space:nowrap;font-variant-numeric:tabular-nums;color:#444}
    td.chk{width:26px}td.chk span{display:inline-block;width:16px;height:16px;border:1.5px solid #999;border-radius:4px}
    .pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:700;white-space:nowrap}
    .ok{background:#e4f4d3;color:#3d6e0e}.debt{background:#fde3e3;color:#a30d15}.trial{background:#fff0d1;color:#8a5a00}
    tr.empty td{height:22px;border-bottom:1px dashed #cfd3e6}
    .sum{padding:6px 16px 0;color:#444;font-size:13px}
    .full{border-top:4px solid #7DB928}.thin{border-top:4px solid #E30613}
    @media print{.top,.sum{display:none}body{background:#fff}.wrap{display:block;padding:0}.g{box-shadow:none;border:1px solid #bbb;margin:0 0 10px;border-radius:6px}}
    </style>"""
    from urllib.parse import quote
    nav = "".join(f"<a class='{'on' if day == k else ''}' href='/spiski?day={quote(k)}{'&subject=' + quote(subject) if subject else ''}'>{DAY_LABEL[k]}</a>" for k in DAY_ORDER)
    subj = "".join(f"<a class='{'on' if subject == c else ''}' href='/spiski?subject={quote(c)}{'&day=' + quote(day) if day else ''}'>{esc(c)}</a>" for c in courses)
    h = [f"<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta http-equiv='refresh' content='300'><title>Списки по группам — KidsUP</title>{css}</head><body>",
         f"<div class='top'><b>Списки по группам</b><a class='{'on' if not day and not subject else ''}' href='/spiski'>Все</a>{nav}<span style='opacity:.5'>|</span>{subj}<a href='javascript:print()'>🖨 Печать</a>"
         f"<span class='st'>данные CRM на {esc(d['synced'])} · страница обновляется сама каждые 5 минут · собрано {d['built']}</span></div>",
         f"<div class='sum'>Групп {len(G)} · мест {tot_cap} · живых записей <b>{tot_live}</b> · оплачено <b style='color:#3d6e0e'>{tot_paid}</b> · не оплатили <b style='color:#a30d15'>{tot_debt}</b> · "
         f"<span class='pill ok'>оплачено</span> <span class='pill trial'>записан на пробное</span> <span class='pill debt'>был / учится без оплаты — дожать</span></div>",
         "<div class='wrap'>"]
    for g in G:
        klass = "full" if g["live"] >= g["cap"] else ("thin" if g["live"] <= 2 else "")
        h.append(f"<div class='g {klass}'><h3>{esc(g['short'])}</h3><div class='m'><span>{esc(g['days'])} · {esc(g['times'])}</span><span>мест <b>{g['cap']}</b></span><span>живых <b>{g['live']}</b></span><span>оплачено <b>{g['paid']}</b></span><span>пробных <b>{g['trial']}</b></span><span>дожать <b>{g['debt']}</b></span></div><table>")
        for i, k in enumerate(g["kids"], 1):
            h.append(f"<tr><td class='n'>{esc(k.get('slot')) if g.get('lg') else i}</td><td class='chk'><span></span></td><td><b>{esc(k['name'])}</b><br><span style='color:#777;font-size:11px'>{esc(k['status'])}{(' · был ' + k['last']) if k['last'] else ''}</span></td><td class='ph'>{esc(k['phone'])}</td><td><span class='pill {k['cls']}'>{esc(k['money'])}</span></td></tr>")
        for i in range(len(g["kids"]) + 1, max(g["cap"], len(g["kids"])) + 1):
            h.append(f"<tr class='empty'><td class='n'>{i}</td><td class='chk'><span></span></td><td></td><td></td><td></td></tr>")
        h.append("</table></div>")
    h.append("</div></body></html>")
    return "".join(h)
