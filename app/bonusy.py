"""Рейтинг администраторов и расчёт бонусов по фактам из CRM.

Схема владельца от 24.08: 300 ₽ за клиента, дошедшего до пробного;
300 ₽ за продажу абонемента в день пробного; 200 ₽ — если абонемент
куплен в течение двух недель после; 100 ₽ за второй предмет тому же
ребёнку в течение месяца. Админ Бураковых получает только первую
ставку — его работа кончается на «дошёл до пробного».

Важная оговорка расчёта: пробные занятия нового сезона начинаются
31 августа, поэтому до этой даты первая ставка почти всегда нулевая, а
рейтинг держится на записях. Это не ошибка, а состояние: записи уже
сделаны, деньги по ним придут в сентябре.

Запуск:
    python -m app.bonusy      — собрать docs/bonusy_raschet.html
"""

from __future__ import annotations

import html as _html
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.bonusy")

SINCE = "2026-08-17"          # дата, с которой пошёл вал работы
RATE_TRIAL, RATE_SAME_DAY, RATE_2WEEKS, RATE_SECOND = 300, 300, 200, 100
MGR = {232763: "Ира", 232805: "Аня", 202856: "Лена", 154181: "Лиза",
       None: "Админ Бураковых"}
ACTIVE_JOIN = {2, 50509, 58131, 58132, 83760}


def collect(since: str = SINCE) -> dict:
    mk = MoyklassClient(sync.get_api_key())
    try:
        joins = taskguard.pull_all(mk, "/v1/company/joins", "joins")
        subs = taskguard.pull_all(mk, "/v1/company/userSubscriptions",
                                  "subscriptions", cache_hours=6)
        users = taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=2)
        rc = mk.get("/v1/company/classes", {"limit": 500})
        cls = {c["id"]: (c.get("name") or "")
               for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
        try:
            recs = mk.fetch_all("/v1/company/lessonRecords", ["lessonRecords"],
                                params={"date": [since, date.today().isoformat()],
                                        "includeLessons": "true"}) or []
        except Exception:
            recs = []
    finally:
        mk.close()

    byid = {u["id"]: u for u in users}
    # кто кого записал
    made = defaultdict(list)
    first_join = {}
    for j in joins:
        nm = cls.get(j.get("classId"), "")
        if not nm.startswith("2627") or "аявк" in nm.lower():
            continue
        if j.get("statusId") not in ACTIVE_JOIN:
            continue
        d = str(j.get("createdAt") or "")[:10]
        who = MGR.get(j.get("managerId"), f"мгр{j.get('managerId')}")
        if d >= since:
            made[who].append({"uid": j["userId"], "date": d, "group": nm})
        first_join.setdefault(j["userId"], d)

    # пришёл ли на пробное
    came = {r.get("userId") for r in recs if r.get("test") and r.get("visit")}
    # оплаты после записи
    pay = defaultdict(list)
    for s in subs:
        if (s.get("stats") or {}).get("totalPayed", 0) > 0:
            d = (s.get("beginDate") or "")[:10]
            if d >= since:
                pay[s["userId"]].append(d)

    out = {}
    for who, items in made.items():
        seen_child = set()
        rows, money = [], 0
        for it in items:
            uid = it["uid"]
            bonus, why = 0, []
            if uid in came:
                bonus += RATE_TRIAL
                why.append("дошёл на пробное 300")
            pays = sorted(pay.get(uid, []))
            if pays:
                gap = (datetime.fromisoformat(pays[0]).date()
                       - datetime.fromisoformat(it["date"]).date()).days
                if who != "Админ Бураковых":
                    if gap <= 0:
                        bonus += RATE_SAME_DAY; why.append("оплата в день 300")
                    elif gap <= 14:
                        bonus += RATE_2WEEKS; why.append("оплата за 2 недели 200")
            if uid in seen_child and who != "Админ Бураковых":
                bonus += RATE_SECOND; why.append("второй предмет 100")
            seen_child.add(uid)
            money += bonus
            rows.append({**it, "name": (byid.get(uid, {}).get("name") or "")[:26],
                         "came": uid in came, "paid": bool(pays),
                         "bonus": bonus, "why": ", ".join(why) or "ждём пробного"})
        out[who] = {"rows": sorted(rows, key=lambda r: r["date"]),
                    "count": len(rows), "came": sum(1 for r in rows if r["came"]),
                    "paid": sum(1 for r in rows if r["paid"]), "money": money}
    return out


CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:16px;color:#222;max-width:1000px}
h1{font-size:20px;margin:0 0 4px} .sub{color:#666;font-size:13px;margin-bottom:14px}
h2{font-size:16px;color:#312783;margin:20px 0 6px}
table{border-collapse:collapse;width:100%;margin-bottom:6px}
th{background:#312783;color:#fff;font-size:12px;padding:6px;text-align:left}
td{border-bottom:1px solid #e3e3e3;padding:6px;font-size:13px}
.tot{background:#F4F9EF;font-weight:700}
.money{text-align:right;white-space:nowrap;font-weight:600}
.warn{background:#FFF4E0;border-left:4px solid #F59C00;padding:9px 11px;font-size:13px;margin:10px 0}
.rate{color:#666;font-size:12px}
"""


def potential(data: dict) -> dict:
    """Сколько будет начислено, если каждая запись дойдёт до пробного и
    закончится покупкой в тот же день. Верхняя граница фонда: реальность
    ляжет ниже, но владельцу нужно понимать масштаб обязательств заранее."""
    out = {}
    for who, d in data.items():
        n = d["count"]
        if who == "Админ Бураковых":
            out[who] = {"n": n, "max": n * RATE_TRIAL,
                        "note": "только за доведённых до пробного"}
        else:
            out[who] = {"n": n, "max": n * (RATE_TRIAL + RATE_SAME_DAY),
                        "note": "пробное + продажа в тот же день"}
    return out


def page(data: dict) -> str:
    order = sorted(data, key=lambda w: -data[w]["count"])
    out = [f"<style>{CSS}</style>", "<h1>Рейтинг администраторов и бонусы</h1>",
           f"<div class=sub>С {SINCE} по {date.today():%d.%m.%Y}. Ставки: 300 ₽ "
           f"за дошедшего до пробного, 300 ₽ за оплату в день пробного, "
           f"200 ₽ за оплату в течение двух недель, 100 ₽ за второй предмет. "
           f"У Админа Бураковых — только первая ставка.</div>",
           "<div class=warn>Пробные занятия нового сезона начинаются "
           "31 августа, поэтому «дошёл до пробного» пока почти везде ноль, "
           "а бонус начислен только там, где уже есть оплата. Записи "
           "сделаны — деньги по ним придут в сентябре.</div>",
           "<h2>Итог</h2><table><tr><th>Кто</th><th>Записей</th>"
           "<th>Дошли на пробное</th><th>Оплатили</th><th>Бонус</th></tr>"]
    total = 0
    for w in order:
        d = data[w]
        total += d["money"]
        out.append(f"<tr><td><b>{w}</b></td><td>{d['count']}</td>"
                   f"<td>{d['came']}</td><td>{d['paid']}</td>"
                   f"<td class=money>{d['money']:,} ₽</td></tr>".replace(",", " "))
    out.append(f"<tr class=tot><td>Всего</td><td>{sum(data[w]['count'] for w in order)}</td>"
               f"<td>{sum(data[w]['came'] for w in order)}</td>"
               f"<td>{sum(data[w]['paid'] for w in order)}</td>"
               f"<td class=money>{total:,} ₽</td></tr></table>".replace(",", " "))
    pot = potential(data)
    out.append("<h2>Если все дойдут и все купят</h2>"
               "<div class=sub>Верхняя граница: каждая сделанная запись "
               "доходит до пробного и заканчивается покупкой в тот же день. "
               "Реальность ляжет ниже — но так виден масштаб обязательств.</div>"
               "<table><tr><th>Кто</th><th>Записей</th><th>Максимум</th>"
               "<th>Из чего</th></tr>")
    tot_max = 0
    for w in order:
        p = pot[w]
        tot_max += p["max"]
        out.append(f"<tr><td><b>{w}</b></td><td>{p['n']}</td>"
                   f"<td class=money>{p['max']:,} ₽</td>"
                   f"<td class=rate>{p['note']}</td></tr>".replace(",", " "))
    out.append(f"<tr class=tot><td>Всего</td>"
               f"<td>{sum(p['n'] for p in pot.values())}</td>"
               f"<td class=money>{tot_max:,} ₽</td><td></td></tr></table>"
               .replace(",", " "))
    for w in order:
        d = data[w]
        out.append(f"<h2>{w} — {d['count']} записей, {d['money']} ₽</h2>"
                   "<table><tr><th>Дата</th><th>Клиент</th><th>Группа</th>"
                   "<th>Начислено</th></tr>")
        for r in d["rows"]:
            out.append(f"<tr><td>{r['date'][8:10]}.{r['date'][5:7]}</td>"
                       f"<td>{_html.escape(r['name'] or '—')}</td>"
                       f"<td>{_html.escape(r['group'][5:60])}</td>"
                       f"<td class=money>{r['bonus']} ₽ "
                       f"<span class=rate>{_html.escape(r['why'])}</span></td></tr>")
        out.append("</table>")
    return "\n".join(out)


def main():
    from pathlib import Path
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    data = collect()
    p = Path(__file__).resolve().parent.parent / "docs" / "bonusy_raschet.html"
    p.write_text(page(data), encoding="utf-8")
    for w, d in sorted(data.items(), key=lambda x: -x[1]["count"]):
        print(f"   {w:16s} записей {d['count']:>3}, пробных {d['came']:>2}, "
              f"оплат {d['paid']:>2} → {d['money']} ₽")
    print(p)


if __name__ == "__main__":
    main()
