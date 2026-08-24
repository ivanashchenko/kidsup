"""Лист Ани: все, кто когда-либо касался мини-сада или нулевого класса.

Решение владельца 24.08: дежурная обзванивает всех, кто пробовал ходить
в мини-сад или нулевой класс — В ТОМ ЧИСЛЕ побывавших только на пробном
и оставивших заявку. Это осознанно шире обычных листов: сад — продукт
с самым длинным циклом решения, и заявка полугодовой давности здесь
живее, чем в разовых кружках.

Исключаются: уже записанные в мини-сад или нулевой класс нового сезона,
статусы «не писать»/«некачественный»/«отказ», пометка «не звонить»
в имени, и телефоны из сегодняшних листов Лены, Иры и Клуба Буракова —
один номер за день слышит нас один раз.

Запуск:
    python -m app.sadlist        — собрать docs/list_anya_sad.html
"""

from __future__ import annotations

import html as _html
import logging
import re
from datetime import date

from . import prozvon, sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.sadlist")

SEASON = date(2026, 9, 1)
SAD = re.compile(r"ини-сад|_НК_|нулев|ГКП", re.I)
NEW = re.compile(r"^2627")
SKIP_STATE = {146328, 125954, 125957}
NO_CALL = re.compile(r"не\s*звонить|не\s*звоните|не\s*беспоко", re.I)


def _other_lists() -> set[str]:
    out = set()
    from pathlib import Path
    docs = Path(__file__).resolve().parent.parent / "docs"
    for slug in ("list_lena", "list_burakov", "list_irina"):
        try:
            out |= set(re.findall(r"\+7(\d{10})",
                                  (docs / f"{slug}.html").read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


def collect() -> list[dict]:
    mk = MoyklassClient(sync.get_api_key())
    try:
        joins = taskguard.pull_all(mk, "/v1/company/joins", "joins")
        users = taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=2)
        rc = mk.get("/v1/company/classes", {"limit": 500})
        classes = rc.get("classes") if isinstance(rc, dict) else rc
        cls = {c["id"]: (c.get("name") or "") for c in classes}
    finally:
        mk.close()

    touched: dict[int, set[str]] = {}
    already: set[int] = set()
    for j in joins:
        nm = cls.get(j.get("classId"), "")
        uid = j.get("userId")
        if not (uid and SAD.search(nm)):
            continue
        if NEW.match(nm):
            # запись нового сезона со «взрослым» статусом — уже наш,
            # звать не надо; заявка нового сезона — как раз надо
            if j.get("statusId") in prozvon.ACTIVE_JOIN and not re.search(r"аявк", nm, re.I):
                already.add(uid)
                continue
        kind = ("заявка" if re.search(r"аявк", nm, re.I)
                else ("нулевой класс" if re.search(r"нулев|_НК_", nm, re.I)
                      else "мини-сад"))
        touched.setdefault(uid, set()).add(kind)

    other = _other_lists()
    byid = {u["id"]: u for u in users}
    out = []
    for uid, kinds in touched.items():
        if uid in already:
            continue
        u = byid.get(uid)
        if not u or u.get("clientStateId") in SKIP_STATE:
            continue
        name = (u.get("name") or "").strip()
        if NO_CALL.search(name):
            continue
        phone = "".join(c for c in (u.get("phone") or "") if c.isdigit())[-10:]
        if len(phone) != 10 or phone in other:
            continue
        bd = next((a.get("value") for a in (u.get("attributes") or [])
                   if a.get("attributeAlias") == "birthday"), None)
        age = None
        if bd:
            try:
                age = round((SEASON - date.fromisoformat(bd[:10])).days / 365.25, 1)
            except ValueError:
                pass
        if age is not None and age > 8.5:
            continue          # из сада и нулевого класса выросли
        # куда звать решает возраст: до 4,5 — мини-сад, старше — нулевой
        offer = ("мини-сад 9:00–13:00" if age is not None and age < 4.5
                 else "нулевой класс 10:00–14:00" if age is not None
                 else "мини-сад или нулевой класс (уточнить возраст)")
        out.append({"uid": uid, "name": name, "phone": phone, "age": age,
                    "was": ", ".join(sorted(kinds)), "offer": offer})
    # малыши сверху: у мини-сада группы пустее и цикл решения длиннее
    out.sort(key=lambda r: (r["age"] is None, r["age"] or 0, r["name"]))
    return out


CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:12px;color:#222}
h1{font-size:19px;margin:0 0 3px} .sub{color:#666;font-size:12px;margin-bottom:9px}
table{border-collapse:collapse;width:100%} thead{display:table-header-group}
th{background:#312783;color:#fff;font-size:11px;padding:5px 4px;text-align:left}
td{border-bottom:1px solid #ddd;padding:6px 4px;font-size:11pt;vertical-align:top}
.ph{font-size:12.5pt;font-weight:600;white-space:nowrap}
.ag{text-align:center;font-weight:600} .of{color:#1a6b1a;font-weight:600}
.res{width:170px;border-bottom:1px solid #999}
@media print{body{margin:6px}}
"""


def main():
    from pathlib import Path
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rows = collect()
    body = [f"<style>{CSS}</style>",
            "<h1>Аня — мини-сад и нулевой класс, обзвон 24.08</h1>",
            f"<div class=sub>{len(rows)} семей: все, кто когда-либо ходил, пробовал "
            f"или оставлял заявку на мини-сад либо нулевой класс и не записан туда "
            f"на 2026/27. Малыши сверху. Печатать в альбомной ориентации.</div>",
            "<table><thead><tr><th>Фамилия Имя</th><th>Возраст<br>на 1.09</th>"
            "<th>Что было</th><th>Телефон</th><th>Куда звать</th>"
            "<th>Итог разговора</th></tr></thead><tbody>"]
    for r in rows:
        age = ("%g" % r["age"]).replace(".", ",") if r["age"] is not None else "—"
        body.append(f"<tr><td>{_html.escape(r['name'])}</td><td class=ag>{age}</td>"
                    f"<td>{_html.escape(r['was'])}</td><td class=ph>+7{r['phone']}</td>"
                    f"<td class=of>{_html.escape(r['offer'])}</td><td class=res></td></tr>")
    body.append("</tbody></table>")
    p = Path(__file__).resolve().parent.parent / "docs" / "list_anya_sad.html"
    p.write_text("\n".join(body), encoding="utf-8")
    print(f"{p}: {len(rows)} семей")


if __name__ == "__main__":
    main()
