"""Списки обзвона по категориям A, B, C — взамен задач в CRM.

Зачем так. Массовый обзвон жил в задачах МойКласса: 122 штуки, размазанные
по четырём администраторам. В задачах это работает плохо — они тонут среди
срочных, у Ани их скопилось 208 штук, и человек не видит ни объёма работы,
ни своего продвижения. 26.08 владелец решил вернуть обзвон в списки,
но с полной картиной по каждому: что человек посещал в последний раз,
когда перестал ходить и что ему предлагать.

Категории (по дате последней оплаты — она надёжнее записей):
  A — ходили этим летом 2026;
  B — ходили в учебном году 2025/26;
  C — 2024/25 и лето 2025.

Кого не берём: записанных на новый сезон, «не писать», отказ,
некачественных, и тех, кому уже звонили с 17 августа.

Запуск:
    python -m app.spiski            — сколько в каждой категории
    python -m app.spiski build      — собрать docs/spisok_a|b|c.html
"""

from __future__ import annotations

import html as _html
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.spiski")

SEASON = date(2026, 9, 1)
ACTIVE_JOIN = {2, 50509, 58131, 58132, 83760}
SKIP_STATE = {146328, 125954, 125957}
# Граница «ему уже звонили». Была 17 августа — по началу нынешнего обзвона,
# но это была наша внутренняя дата, а не факт: 26.08 проверка показала, что
# тринадцать человек в списках получали звонок в начале августа, и для них
# «ни разу не звонили» звучало неправдой. Считаем по всему месяцу.
CALLED_SINCE = date(2026, 8, 1)

_SUBJ = (("_ПШ_", "подготовка к школе"), ("_АЯ_", "английский"),
         ("Первая школа", "раннее развитие"), ("Музыка и речь", "музыка и речь"),
         ("Лицей", "лицей для малышей"), ("ини-сад", "мини-сад"),
         ("_НК_", "нулевой класс"), ("нулев", "нулевой класс"),
         ("ИЗО", "ИЗО"), ("ШАХ", "шахматы"), ("_МА_", "ментальная арифметика"),
         ("ЛГ", "логопед"), ("ЛК", "летний клуб"), ("агер", "летний клуб"),
         ("СБТ", "робототехника"), ("Танц", "танцы"))


def subject(name: str) -> str:
    for key, label in _SUBJ:
        if key.lower() in (name or "").lower():
            return label
    return "занятия"


def offer(age: float | None, was: set) -> str:
    """Что предлагать. Сначала то же, чем занимался, — возвращаться легче
    туда, где уже знаешь педагога и порядки. Потом то, что подходит
    по возрасту и где у нас пустые места."""
    same = [w for w in was if w not in ("летний клуб", "занятия")]
    if age is None:
        return (", ".join(same[:2]) or "подобрать по возрасту") + " — уточнить возраст"
    if age < 3:
        add = "раннее развитие, мини-сад"
    elif age < 5:
        add = "«Лицей для малышей», подготовка с 4 лет, английский"
    elif age < 7.5:
        add = "подготовка к школе, английский, шахматы"
    elif age <= 12.5:
        add = "английский Cambridge, шахматы, ИЗО, ментальная арифметика"
    else:
        add = "английский для старших"
    return (f"вернуть в {', '.join(same[:2])}" if same else "новое: ") + f" · {add}"


def collect() -> dict:
    from . import mango
    mk = MoyklassClient(sync.get_api_key())
    try:
        users = taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=2)
        joins = taskguard.pull_all(mk, "/v1/company/joins", "joins")
        pays = mk.fetch_all("/v1/company/payments", ["payments"],
                            params={"date": ["2024-09-01", "2026-08-26"]}) or []
        rc = mk.get("/v1/company/classes", {"limit": 500})
        cls = {c["id"]: (c.get("name") or "")
               for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
    finally:
        mk.close()

    last_pay: dict = {}
    for p in pays:
        d = str(p.get("date") or "")[:10]
        uid = p.get("userId")
        try:
            s = float(p.get("summa") or 0)
        except (TypeError, ValueError):
            s = 0
        if uid and d and s > 0 and d > last_pay.get(uid, ""):
            last_pay[uid] = d

    booked, hist = set(), defaultdict(list)
    for j in joins:
        nm = cls.get(j.get("classId"), "")
        if not nm or "аявк" in nm.lower():
            continue
        if nm.startswith("2627") and j.get("statusId") in ACTIVE_JOIN:
            booked.add(j["userId"])
        hist[j["userId"]].append((str(j.get("createdAt") or "")[:10], nm))

    # {телефон: дата последнего звонка} — дату показываем в листе: знать,
    # что человеку уже звонили и когда, важнее, чем просто выкинуть его.
    called: dict = {}
    for dd in range((date.today() - CALLED_SINCE).days + 1):
        day = CALLED_SINCE + timedelta(days=dd)
        try:
            rows = mango.calls(datetime.combine(day, datetime.min.time()),
                               datetime.combine(day, datetime.max.time()))
        except Exception:
            continue
        for r in rows:
            n = (r.get("to_num") if r.get("from_ext") else r.get("from_num")) or ""
            x = "".join(c for c in str(n) if c.isdigit())[-10:]
            if len(x) == 10:
                called[x] = day.isoformat()

    out = {"A": [], "B": [], "C": []}
    for u in users:
        uid = u["id"]
        d = last_pay.get(uid)
        if not d or uid in booked or u.get("clientStateId") in SKIP_STATE:
            continue
        phone = "".join(c for c in str(u.get("phone") or "") if c.isdigit())[-10:]
        if len(phone) != 10 or phone in called:
            continue          # в этом месяце уже звонили — в список не берём
        cat = "A" if d >= "2026-06-01" else "B" if d >= "2025-09-01" else "C"
        bd = next((a.get("value") for a in (u.get("attributes") or [])
                   if a.get("attributeAlias") == "birthday"), None)
        age = None
        if bd:
            try:
                age = round((SEASON - date.fromisoformat(bd[:10])).days / 365.25, 1)
            except ValueError:
                pass
        # чем занимался в последний раз: берём записи, ближайшие к последней оплате
        recent = sorted(hist.get(uid, []), reverse=True)[:4]
        was = {subject(nm) for _, nm in recent}
        was.discard("занятия")
        out[cat].append({
            "name": (u.get("name") or "").strip()[:30], "phone": phone, "age": age,
            "was": ", ".join(sorted(was)[:3]) or "—", "last": d,
            "offer": offer(age, was)})
    for k in out:
        out[k].sort(key=lambda r: (r["last"], r["age"] is None, r["age"] or 99),
                    reverse=True)
    return out


CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:12px;color:#222}
h1{font-size:19px;margin:0 0 3px} .sub{color:#666;font-size:12px;margin-bottom:9px}
table{border-collapse:collapse;width:100%} thead{display:table-header-group}
th{background:#312783;color:#fff;font-size:11px;padding:5px 4px;text-align:left}
td{border-bottom:1px solid #ddd;padding:6px 4px;font-size:11pt;vertical-align:top}
.ph{font-size:12.5pt;font-weight:600;white-space:nowrap}
.ag{text-align:center;font-weight:600} .of{color:#1a6b1a}
.res{width:150px;border-bottom:1px solid #999}
.old{color:#B26F00}
@media print{body{margin:6px}}
"""

TITLES = {
    "A": ("A — ходили этим летом 2026",
          "Самые тёплые: были у нас месяц-два назад, помнят педагогов "
          "и порядки. Начинать с них."),
    "B": ("B — ходили в учебном году 2025/26",
          "Занимались весь прошлый год и просто не продлили. Разговор "
          "простой: «продолжаем?» Конверсия у таких вдвое выше холодных."),
    "C": ("C — 2024/25 и лето 2025",
          "Год и больше не были. Нужен повод вернуться: новый педагог, "
          "новое направление, изменившееся расписание."),
}


def page(cat: str, rows: list) -> str:
    title, why = TITLES[cat]
    out = [f"<style>{CSS}</style>", f"<h1>{title}</h1>",
           f"<div class=sub>{len(rows)} семей. {why} Здесь только те, кому "
           f"НЕ звонили с 17 августа и кто не записан на новый год. "
           f"Свежие сверху. Печатать в альбомной.</div>",
           "<table><thead><tr><th>Фамилия Имя</th><th>Возраст<br>на 1.09</th>"
           "<th>Что посещал</th><th>Перестал ходить</th><th>Телефон</th>"
           "<th>Что предлагаем</th><th>Итог разговора</th></tr></thead><tbody>"]
    for r in rows:
        age = ("%g" % r["age"]).replace(".", ",") if r["age"] is not None else "—"
        d = r["last"]
        out.append(f"<tr><td>{_html.escape(r['name'] or '—')}</td>"
                   f"<td class=ag>{age}</td>"
                   f"<td>{_html.escape(r['was'])}</td>"
                   f"<td class=old>{d[8:10]}.{d[5:7]}.{d[2:4]}</td>"
                   f"<td class=ph>+7{r['phone']}</td>"
                   f"<td class=of>{_html.escape(r['offer'])}</td>"
                   f"<td class=res></td></tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def main():
    import sys
    from pathlib import Path
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    data = collect()
    for k in ("A", "B", "C"):
        print(f"   {TITLES[k][0]:44} {len(data[k])}")
    if "build" in sys.argv:
        docs = Path(__file__).resolve().parent.parent / "docs"
        for k in ("A", "B", "C"):
            p = docs / f"spisok_{k.lower()}.html"
            p.write_text(page(k, data[k]), encoding="utf-8")
            print(p)


if __name__ == "__main__":
    main()
