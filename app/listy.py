"""Три листа обзвона на смену — по одному на каждого звонящего.

Зачем разные листы. 23.08 мама и администратор весь день шли по одному
и тому же списку: 42 семьи получили по два звонка, а половина списка
осталась нетронутой. Лист должен быть закреплён за человеком, и один
номер не может стоять в двух листах одновременно.

Кто что берёт (решение владельца 23.08 с правками):
  Лена, доб. 12   — кто занимался у Кати или Инги (подготовка к школе);
  Клуб Буракова, доб. 20 — остальные из списка Надежды;
  Ирина, доб. 10  — английский 7-12 лет, кто платил за последние два года.

Из первых двух вычитаются те, с кем СЕГОДНЯ уже состоялся разговор:
звонить им завтра снова — выглядеть так, будто мы не помним разговора.
Недозвоны остаются: повторная попытка через день это нормально.

Списки добираются до нужного объёма из общего возрастного листа —
иначе у звонящего работа кончается через полтора часа.

Запуск:
    python -m app.listy          — собрать docs/list_lena|burakov|irina.html
"""

from __future__ import annotations

import html as _html
import logging
import re
import subprocess
from datetime import date, datetime, timedelta

from . import mango, prozvon, sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.listy")

SEASON = date(2026, 9, 1)
TARGET = 100          # сколько строк в листе на смену
IRINA_TARGET = 90
AY = re.compile(r"_АЯ_|_ЛК_|нглийск", re.I)
LAST2 = re.compile(r"^(2425|2526|2024|2626)_")
ZAY = re.compile(r"аявк", re.I)
SKIP_STATE = {146328, 125954, 125957}


def _mother_list() -> list[dict]:
    """Список Надежды с педагогом — берём из свёрстанной страницы.

    Педагог подготовки (Катя или Инга) есть только там: в CRM группа
    называется по дням и времени, а не по имени педагога."""
    for ref in ("HEAD", "HEAD~1", "HEAD~4", "HEAD~9"):
        try:
            h = subprocess.run(["git", "show", f"{ref}:docs/obzvon_nadezhda.html"],
                               capture_output=True, text=True).stdout
        except Exception:
            continue
        rx = re.compile(
            r"<tr><td class='nm'>(.*?)</td><td class='ag'>(.*?)</td>"
            r"<td class='ph'>\+7(\d{10})</td><td>(.*?)</td>"
            r"<td class='tch'>(.*?)</td><td class='dt'>(.*?)</td><td class='st'>(.*?)</td>")
        rows = [{"name": re.sub(r"<.*?>", "", m.group(1)).strip(), "age": m.group(2),
                 "phone": m.group(3), "was": re.sub(r"<.*?>", "", m.group(4)),
                 "teacher": m.group(5), "last": m.group(6), "state": m.group(7)}
                for m in rx.finditer(h)]
        if rows:
            return rows
    return []


def _talked_today() -> set[str]:
    """С кем сегодня состоялся разговор — этих завтра не тревожим."""
    now = datetime.now()
    try:
        rows = mango.calls(now.replace(hour=0, minute=0, second=0), now)
    except Exception as e:
        log.warning("Mango недоступен, вычитание сегодняшних разговоров пропущено: %s", e)
        return set()
    return {c["to_num"][-10:] for c in rows
            if c["from_ext"] and c["answer"]
            and (c["finish"] - c["answer"]) >= mango.TALK_MIN}


def _english_7_12(mk) -> list[dict]:
    """Английский 7-12 лет: кто платил за два года и не записан на новый.

    Свежие первыми — семья, занимавшаяся в прошлом сезоне, помнит нас,
    а ушедшая в 2024-м уже нет."""
    joins = taskguard.pull_all(mk, "/v1/company/joins", "joins")
    users = taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=2)
    subs = taskguard.pull_all(mk, "/v1/company/userSubscriptions",
                              "subscriptions", cache_hours=6)
    rc = mk.get("/v1/company/classes", {"limit": 500})
    classes = rc.get("classes") if isinstance(rc, dict) else rc
    cls = {c["id"]: (c.get("name") or "") for c in classes}

    fresh, older = set(), set()
    for j in joins:
        nm = cls.get(j.get("classId"), "")
        if not (AY.search(nm) and LAST2.search(nm)) or ZAY.search(nm):
            continue
        (fresh if nm.startswith(("2526", "2626")) else older).add(j["userId"])
    booked = {j["userId"] for j in joins
              if cls.get(j.get("classId"), "").startswith("2627")
              and j.get("statusId") in prozvon.ACTIVE_JOIN}
    paid = {s["userId"] for s in subs
            if (s.get("visitCount") or 0) >= 4
            and (s.get("beginDate") or "")[:10] >= "2024-09-01"}
    byid = {u["id"]: u for u in users}
    was = {}
    for j in joins:
        s = prozvon._subject(cls.get(j.get("classId"), ""))
        if s and j.get("userId"):
            was.setdefault(j["userId"], set()).add(s)

    out = []
    for tier, group in (("свежие", fresh), ("давние", older - fresh)):
        for uid in sorted((group & paid) - booked):
            u = byid.get(uid)
            if not u or u.get("clientStateId") in SKIP_STATE:
                continue
            bd = next((a.get("value") for a in (u.get("attributes") or [])
                       if a.get("attributeAlias") == "birthday"), None)
            if not bd:
                continue
            try:
                age = round((SEASON - date.fromisoformat(bd[:10])).days / 365.25, 1)
            except ValueError:
                continue
            if not 7.0 <= age <= 12.5:
                continue
            out.append({"name": u.get("name") or "", "age": age,
                        "phone": "".join(c for c in (u.get("phone") or "")
                                         if c.isdigit())[-10:],
                        "was": ", ".join(sorted(was.get(uid, set())))[:44],
                        "tier": tier})
    return out


CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:12px;color:#222}
h1{font-size:19px;margin:0 0 3px} .sub{color:#666;font-size:12px;margin-bottom:9px}
table{border-collapse:collapse;width:100%} thead{display:table-header-group}
th{background:#312783;color:#fff;font-size:11px;padding:5px 4px;text-align:left}
td{border-bottom:1px solid #ddd;padding:6px 4px;font-size:11pt;vertical-align:top}
.ph{font-size:12.5pt;font-weight:600;white-space:nowrap}
.ag{text-align:center;font-weight:600;white-space:nowrap}
.res{width:170px;border-bottom:1px solid #999}
tr.band td{background:#F3F1FB;font-weight:700;font-size:12px;color:#312783}
.o1{color:#1a6b1a;font-weight:600}
@media print{body{margin:6px} th{font-size:10px}}
"""


def page(title: str, sub: str, rows: list[dict], cols: list[tuple]) -> str:
    out = [f"<style>{CSS}</style>", f"<h1>{title}</h1>", f"<div class=sub>{sub}</div>",
           "<table><thead><tr>" +
           "".join(f"<th>{c[0]}</th>" for c in cols) +
           "<th>Итог разговора</th></tr></thead><tbody>"]
    band = None
    for r in rows:
        if r.get("band") and r["band"] != band:
            band = r["band"]
            out.append(f"<tr class=band><td colspan={len(cols)+1}>{band}</td></tr>")
        cells = "".join(f"<td class='{c[2]}'>{_html.escape(str(r.get(c[1]) or '—'))}</td>"
                        for c in cols)
        out.append(f"<tr>{cells}<td class=res></td></tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def _busy_phones(mk) -> set[str]:
    """Телефоны, по которым у администратора уже висит открытая задача.

    Такой клиент завтра получит звонок от того, на ком задача. Если он
    же стоит в печатном листе, ему позвонят дважды из одного центра —
    ровно то, из-за чего 23.08 семья Радюхиных выслушала нас два раза
    за день. Печатный лист и очередь задач должны не пересекаться."""
    users = taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=3)
    ph = {u["id"]: "".join(c for c in str(u.get("phone") or "") if c.isdigit())[-10:]
          for u in users}
    out = set()
    for mid in (232805, 232763, 202856, 154181):
        for t in taskguard.all_tasks(mk, mid):
            if (t.get("isComplete") or t.get("isCompleted")) or not t.get("userId"):
                continue
            p = ph.get(t["userId"])
            if p and len(p) == 10:
                out.add(p)
    return out


def build() -> dict:
    from pathlib import Path
    docs = Path(__file__).resolve().parent.parent / "docs"
    mother = _mother_list()
    talked = _talked_today()
    mk0 = MoyklassClient(sync.get_api_key())
    try:
        busy = _busy_phones(mk0)
    finally:
        mk0.close()
    log.info("список Надежды: %d, вчера поговорили: %d, занято задачами: %d",
             len(mother), len(talked), len(busy))

    def fit(r):
        return (r["phone"] not in talked and r["phone"] not in busy
                and r["state"] != "думает")

    lena = [r for r in mother if r["teacher"] in ("Катя", "Инга", "Инга, Катя") and fit(r)]
    # У семьи бывает двое детей: один занимался у Кати, другой нет — и
    # один номер попадает в оба листа. Звонок в семью один, поэтому
    # приоритет у Лены: с ней родителю есть о чём говорить предметно.
    lena_phones = {r["phone"] for r in lena}
    burak = [r for r in mother
             if r["teacher"] not in ("Катя", "Инга", "Инга, Катя")
             and r["phone"] not in lena_phones and fit(r)]

    # добор из общего возрастного листа: у звонящего работа не должна
    # кончиться через полтора часа
    extra = [r for r in prozvon.collect()
             if r["phone"] not in talked and r["phone"] not in busy
             and r["phone"] not in {x["phone"] for x in mother}]
    # Добор делится пополам между листами: одна и та же половина не
    # должна попасть в оба — иначе двое звонят одному человеку.
    half = len(extra) // 2
    for lst, pool in ((lena, extra[:half]), (burak, extra[half:])):
        # в seen кладём номера ОБОИХ листов: добор второго не должен
        # попасть на семью, уже стоящую в первом
        seen = {x["phone"] for x in lena} | {x["phone"] for x in burak}
        for r in pool:
            if len(lst) >= TARGET:
                break
            if r["phone"] in seen:
                continue
            seen.add(r["phone"])
            lst.append({"name": r["name"], "age": ("%g" % r["age"]).replace(".", ",")
                        if r["age"] else "—", "phone": r["phone"],
                        "was": ", ".join(r["was"]) or "занимался у нас",
                        "teacher": "—", "state": "—", "first": r["first"]})

    mk = MoyklassClient(sync.get_api_key())
    try:
        irina = _english_7_12(mk)
    finally:
        mk.close()
    # Лист Ирины собирается по своему признаку (английский 7-12), поэтому
    # без явного вычитания в него попадают те же семьи, что уже стоят у
    # Лены и Буракова: в первой сборке пересечение было 82 человека из 86.
    # Один номер — один лист, иначе смысл разделения теряется.
    taken = {r["phone"] for r in lena} | {r["phone"] for r in burak}
    irina = [r for r in irina
             if r["phone"] not in talked and r["phone"] not in busy
             and r["phone"] not in taken][:IRINA_TARGET]
    for r in irina:
        r["band"] = ("занимались в прошлом сезоне" if r["tier"] == "свежие"
                     else "занимались раньше")
        r["age"] = ("%g" % r["age"]).replace(".", ",")

    COLS_M = [("Фамилия Имя", "name", "nm"), ("Возраст", "age", "ag"),
              ("Что посещал", "was", ""), ("Педагог", "teacher", ""),
              ("Телефон", "phone_fmt", "ph"), ("Статус", "state", "")]
    COLS_I = [("Фамилия Имя", "name", "nm"), ("Возраст", "age", "ag"),
              ("Что посещал", "was", ""), ("Телефон", "phone_fmt", "ph")]
    for lst in (lena, burak, irina):
        for r in lst:
            r["phone_fmt"] = "+7" + r["phone"]

    today = (date.today() + timedelta(days=1)).strftime("%d.%m")
    files = {}
    for slug, title, sub, rows, cols in (
        ("list_lena", f"Лена — обзвон {today}, звонить с доб. 12",
         f"{len(lena)} семей: занимались у Кати или Инги на подготовке. "
         f"Те, с кем говорили 23.08, из листа убраны.", lena, COLS_M),
        ("list_burakov", f"Клуб Буракова — обзвон {today}, звонить с доб. 20",
         f"{len(burak)} семей из базы KidsUP, кроме подопечных Кати и Инги. "
         f"Те, с кем говорили 23.08, из листа убраны.", burak, COLS_M),
        ("list_irina", f"Ирина — обзвон {today}, звонить с доб. 10",
         f"{len(irina)} семей: английский, ребёнку 7-12 лет к 1 сентября, "
         f"платили за последние два года, на новый год не записаны. "
         f"Свежие сверху.", irina, COLS_I),
    ):
        p = docs / f"{slug}.html"
        p.write_text(page(title, sub + " Печатать в альбомной ориентации.",
                          rows, cols), encoding="utf-8")
        files[slug] = len(rows)
        log.info("%s: %d строк", p, len(rows))
    return files


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(build())


if __name__ == "__main__":
    main()
