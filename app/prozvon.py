"""Лист обзвона по возрастам: кто платил у нас и ещё не записан на 2026/27.

Чем отличается от прежнего листа. Тот собирался по факту «был у нас
с сентября», и в него попадали те, кто оставил заявку и не дошёл, и те,
кто взял одно разовое занятие. Разговор с такими начинается с «вы у нас
занимались», а семья этого не помнит. Здесь порог другой: семья дошла до
покупки — полный абонемент на месяц в учебном году или хотя бы неделя
лагеря этим летом.

Что в строке. Возраст на 1 сентября, что посещал, куда звать в первую
очередь и куда во вторую, место для записи от руки. Первая очередь —
продолжение того, что ребёнок уже посещал: разговор начинается с «вы
занимались на подготовке — продолжаем?», и это самый простой заход.
Вторая — то, что подходит по возрасту, но семья ещё не пробовала.

Предлагаем только то, где на 2026/27 действительно открыты группы:
обещать несуществующее хуже, чем не позвонить.

Запуск:
    python -m app.prozvon          — собрать docs/obzvon_vozrast.html
"""

from __future__ import annotations

import html as _html
import logging
import re
from collections import defaultdict
from datetime import date

from . import sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.prozvon")

SEASON = date(2026, 9, 1)
YEAR_FROM, YEAR_TO = date(2025, 9, 1), date(2026, 6, 10)
SUMMER_FROM = date(2026, 6, 1)

# Полный месяц: раз в неделю — четыре занятия, два раза — восемь. Порог по
# решению владельца от 23.08: четыре занятия либо оплаченный период от
# 25 дней, чтобы абонемент «раз в неделю» тоже считался полным.
MONTH_VISITS = 4
MONTH_DAYS = 25
CAMP_VISITS = 5  # неделя лагеря

ACTIVE_JOIN = {2, 50509, 58131, 58132, 83760}
# Кому не звоним: не писать, некачественный, отказ.
SKIP_STATE = {146328, 125954, 125957}
NO_CALL_MARK = re.compile(r"не\s*звонить|не\s*звоните|не\s*беспоко", re.I)

CAMP_RE = re.compile(r"ЛК|агер", re.I)

# Предметы, по которым на 2026/27 открыты группы. Ключ — как показываем
# в листе, значение — как узнаём предмет в названии группы.
SUBJECTS = [
    ("подготовка к школе", re.compile(r"_ПШ_|одготовк", re.I)),
    ("нулевой класс", re.compile(r"нулев", re.I)),
    ("мини-сад", re.compile(r"ини-сад|_НК_", re.I)),
    ("английский", re.compile(r"_АЯ_|_ЛК_|нглийск", re.I)),
    ("раннее развитие", re.compile(r"РР\.Первая|Первая школа", re.I)),
    ("музыка и речь", re.compile(r"Музыка и речь|МсМ", re.I)),
    ("лицей для малышей", re.compile(r"Лицей", re.I)),
    ("ИЗО", re.compile(r"ИЗО", re.I)),
    ("шахматы", re.compile(r"ШАХ", re.I)),
    ("ментальная арифметика", re.compile(r"_МА_|ентальн", re.I)),
    ("логопед", re.compile(r"^2627_ЛГ|логопед", re.I)),
]

# Что предлагать по возрасту на 1 сентября — только там, где есть группы.
BY_AGE = [
    (1.2, 2.0, ["музыка и речь", "раннее развитие"]),
    (2.0, 3.0, ["раннее развитие", "музыка и речь", "мини-сад"]),
    (3.0, 4.0, ["лицей для малышей", "мини-сад", "английский"]),
    (4.0, 5.0, ["подготовка к школе", "английский", "лицей для малышей"]),
    (5.0, 6.5, ["подготовка к школе", "английский", "нулевой класс", "ИЗО"]),
    (6.5, 7.5, ["нулевой класс", "подготовка к школе", "английский", "ИЗО"]),
    (7.5, 13.0, ["английский", "ментальная арифметика", "шахматы", "ИЗО"]),
]


def _subject(name: str) -> str | None:
    for label, rx in SUBJECTS:
        if rx.search(name or ""):
            return label
    return None


def _age(bd: str | None) -> float | None:
    if not bd:
        return None
    try:
        return round((SEASON - date.fromisoformat(bd[:10])).days / 365.25, 1)
    except ValueError:
        return None


def _d(x) -> date | None:
    try:
        return date.fromisoformat(str(x)[:10])
    except Exception:
        return None


def _offers(age: float | None, was: list[str], open_subj: set[str]) -> tuple[str, str]:
    """Первая очередь — продолжение, вторая — добор по возрасту.

    Если ребёнок ходил на подготовку, звать снова на подготовку проще
    всего: родитель уже выбрал этот продукт и знает педагога. Добор
    предлагается тем, чего семья ещё не пробовала, — иначе во второй
    колонке повторяется первая и звонящему нечего сказать дальше."""
    first = [s for s in was if s in open_subj]
    fit: list[str] = []
    if age is not None:
        for lo, hi, subj in BY_AGE:
            if lo <= age < hi:
                fit = [s for s in subj if s in open_subj]
                break
    # ребёнок вырос из своего предмета: раннее развитие в пять лет не
    # предложишь, поэтому продолжение оставляем только то, что подходит
    if age is not None and fit:
        first = [s for s in first if s in fit] or fit[:1]
    second = [s for s in fit if s not in first]
    return ", ".join(first[:2]) or "—", ", ".join(second[:2]) or "—"


def collect() -> list[dict]:
    mk = MoyklassClient(sync.get_api_key())
    try:
        subs = taskguard.pull_all(mk, "/v1/company/userSubscriptions",
                                  "subscriptions", cache_hours=6)
        joins = taskguard.pull_all(mk, "/v1/company/joins", "joins")
        rc = mk.get("/v1/company/classes", {"limit": 500})
        classes = rc.get("classes") if isinstance(rc, dict) else rc
        cls = {c["id"]: (c.get("name") or "") for c in classes}
        open_subj = {s for c in classes if (c.get("name") or "").startswith("2627")
                     for s in [_subject(c["name"])] if s}

        paid_year: dict[int, set[str]] = defaultdict(set)
        paid_camp: set[int] = set()
        for s in subs:
            b, e = _d(s.get("beginDate")), _d(s.get("endDate"))
            uid, vc = s.get("userId"), s.get("visitCount") or 0
            if not (uid and b):
                continue
            name = cls.get(s.get("mainClassId"), "")
            if CAMP_RE.search(name) and b >= SUMMER_FROM:
                if vc >= CAMP_VISITS:
                    paid_camp.add(uid)
                continue
            long_enough = vc >= MONTH_VISITS or (e and (e - b).days >= MONTH_DAYS)
            if YEAR_FROM <= b < YEAR_TO and long_enough:
                paid_year[uid].add(_subject(name) or "")

        booked = {j["userId"] for j in joins
                  if cls.get(j.get("classId"), "").startswith("2627")
                  and j.get("statusId") in ACTIVE_JOIN}
        # чем ребёнок занимался вообще — для колонки «куда ходил»
        was: dict[int, set[str]] = defaultdict(set)
        for j in joins:
            s = _subject(cls.get(j.get("classId"), ""))
            if s and j.get("userId"):
                was[j["userId"]].add(s)

        busy = set()
        for mid in (232763, 232805, 202856, 154181):
            for t in taskguard.all_tasks(mk, mid):
                if not (t.get("isComplete") or t.get("isCompleted")) and t.get("userId"):
                    busy.add(t["userId"])

        targets = sorted((set(paid_year) | paid_camp) - booked)
        log.info("платили у нас: %d, из них не записаны на 2026/27: %d",
                 len(set(paid_year) | paid_camp), len(targets))

        out = []
        for i, uid in enumerate(targets):
            try:
                u = mk.get(f"/v1/company/users/{uid}")
            except Exception:
                continue
            if u.get("clientStateId") in SKIP_STATE:
                continue
            name = (u.get("name") or "").strip()
            if NO_CALL_MARK.search(name):
                continue
            bd = None
            for a in (u.get("attributes") or []):
                if a.get("attributeAlias") == "birthday" and a.get("value"):
                    bd = a["value"]
            age = _age(bd)
            hist = sorted(x for x in (was.get(uid) or set()) | paid_year.get(uid, set()) if x)
            first, second = _offers(age, hist, open_subj)
            out.append({
                "uid": uid, "name": name,
                "phone": "".join(ch for ch in (u.get("phone") or "") if ch.isdigit())[-10:],
                "age": age, "was": hist,
                "camp": uid in paid_camp, "year": uid in paid_year,
                "first": first, "second": second, "busy": uid in busy,
            })
            if i % 40 == 0:
                log.info("... %d/%d", i, len(targets))
    finally:
        mk.close()
    # по возрастам: звонящий идёт сверху вниз и весь блок говорит об одном
    out.sort(key=lambda r: (r["age"] is None, r["age"] or 0, r["name"]))
    log.info("собрано: %d", len(out))
    return out


CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:14px;color:#222}
h1{font-size:19px;margin:0 0 4px} .sub{color:#666;font-size:12px;margin-bottom:10px}
table{border-collapse:collapse;width:100%} thead{display:table-header-group}
th{background:#312783;color:#fff;font-size:11px;padding:5px 4px;text-align:left}
td{border-bottom:1px solid #ddd;padding:6px 4px;font-size:11pt;vertical-align:top}
.ph{font-size:12pt;font-weight:600;white-space:nowrap}
.ag{text-align:center;font-weight:600;white-space:nowrap}
.o1{color:#1a6b1a;font-weight:600} .o2{color:#666}
.res{width:150px;border-bottom:1px solid #999}
tr.band td{background:#F3F1FB;font-weight:700;font-size:12px;color:#312783}
.mk{font-size:10px;color:#B26F00;white-space:nowrap}
@media print{body{margin:6px} th{font-size:10px}}
"""


def page(rows: list[dict]) -> str:
    def band(age):
        if age is None:
            return "возраст неизвестен"
        for lo, hi, _ in BY_AGE:
            if lo <= age < hi:
                return f"{lo:g}–{hi:g} лет".replace(".", ",")
        return "старше 13"
    out = [f"<style>{CSS}</style>",
           "<h1>Обзвон по возрастам — семьи, которые у нас занимались</h1>",
           f"<div class=sub>{len(rows)} семей: полный абонемент на месяц в 2025/26 "
           f"или неделя лагеря этим летом. На 2026/27 не записаны. "
           f"Собрано {date.today().strftime('%d.%m.%Y')}. "
           f"Печатать в альбомной ориентации.</div>",
           "<table><thead><tr><th>Фамилия Имя</th><th>Возраст<br>на 1.09</th>"
           "<th>Куда ходил</th><th>Телефон</th><th>Предложить в первую очередь</th>"
           "<th>Во вторую очередь</th><th>Записали / итог</th></tr></thead><tbody>"]
    cur = None
    for r in rows:
        b = band(r["age"])
        if b != cur:
            cur = b
            out.append(f"<tr class=band><td colspan=7>{b}</td></tr>")
        mark = " <span class=mk>админ звонит</span>" if r["busy"] else ""
        was = ", ".join(r["was"])
        if r["camp"] and "лагерь" not in was:
            was = (was + ", лагерь").lstrip(", ")
        if not was:
            # предмет не опознан по названию группы, но абонемент был:
            # честнее сказать «занимался у нас», чем поставить прочерк
            was = "занимался у нас"
        out.append(
            f"<tr><td>{_html.escape(r['name'])}{mark}</td>"
            f"<td class=ag>{('%g' % r['age']).replace('.', ',') if r['age'] else '—'}</td>"
            f"<td>{_html.escape(was)}</td>"
            f"<td class=ph>+7{r['phone']}</td>"
            f"<td class=o1>{_html.escape(r['first'])}</td>"
            f"<td class=o2>{_html.escape(r['second'])}</td>"
            f"<td class=res></td></tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def main():
    from pathlib import Path
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rows = collect()
    p = Path(__file__).resolve().parent.parent / "docs" / "obzvon_vozrast.html"
    p.write_text(page(rows), encoding="utf-8")
    print(f"{p}: {len(rows)} семей")


if __name__ == "__main__":
    main()
