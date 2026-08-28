"""Рейтинг администраторов и расчёт бонусов по фактам из CRM.

Схема владельца от 26.08 (заменяет схему 24.08). Денежный расчёт — для
Ани, Иры и Админа Бураковых; правило делит записи на два случая:

1. НОВЫЙ ПРЕДМЕТ — клиент не посещал его в прошлом учебном году.
   Английский и подготовка к школе считаются новым предметом ВСЕГДА:
   там сменились все педагоги, любая запись — работа с нуля.
   Ставки: дошёл до пробного +300; купил абонемент в день пробного
   (или вообще до 31.08) ещё +300; купил в течение двух недель после
   пробного +150 вместо трёхсот.

2. ПРОДОЛЖАЮЩИЙ — тот же предмет к тому же педагогу, что и в 2025/26.
   Пробного у него нет. Ставки: купил до 31.08 включительно +300;
   купил в течение двух недель +150.

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
RATE_TRIAL, RATE_BUY_FAST, RATE_BUY_2W = 300, 300, 150
MGR = {232763: "Ира", 232805: "Аня", 202856: "Лена", 154181: "Лиза",
       None: "Админ Бураковых"}
# денежный расчёт по решению владельца ведётся только этим троим
PAID_ADMINS = {"Аня", "Ира", "Админ Бураковых"}
# записи и заявки: всё, кроме «Отказался» (1) и «Завершил/перевод» (4)
DEAD_JOIN = {1, 4}
DEADLINE = "2026-08-31"       # старая цена и граница «купил до 31.08»

# Педагоги английского и подготовки к школе в этом сезоне новые все —
# для этих предметов «продолжающего» не бывает.
ALWAYS_NEW = {"АЯ", "ПШ"}


def _subject(nm: str) -> str:
    """Предмет группы любого сезона: «2627_ПШ_…» и «ПШ_Группа 1…» → ПШ."""
    nm = nm or ""
    if nm.startswith("2627_"):
        nm = nm[5:]
    head = nm.split("_")[0].strip()
    if head.startswith("РР"):
        return "РР"
    if head.startswith("ЛГ"):
        return "ЛГ"
    if head.startswith("МсМ") or "ини-сад" in head:
        return "Мини-сад"
    if "улев" in head or head == "НК":
        return "НК"
    return head.split(" ")[0]


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
    # чем клиент занимался в прошлом учебном году: предметы групп БЕЗ
    # префикса сезона (2627_/2024_/OLD_/АРХИВ_) — это и есть 2025/26
    past_subj = defaultdict(set)
    for j in joins:
        nm = cls.get(j.get("classId"), "")
        if not nm or nm.startswith(("2627", "2024", "OLD", "АРХИВ", "2526")):
            continue
        past_subj[j["userId"]].add(_subject(nm))

    # кто кого записал: ВСЕ записи сезона 2627, включая листы заявок,
    # кроме отказавшихся и переводов (владелец 26.08: «учти все записи»)
    made = defaultdict(list)
    total_all = defaultdict(int)      # записей за всё время, без фильтра даты
    today_all = defaultdict(int)
    today = date.today().isoformat()
    for j in joins:
        nm = cls.get(j.get("classId"), "")
        if not nm.startswith("2627"):
            continue
        if j.get("statusId") in DEAD_JOIN:
            continue
        d = str(j.get("createdAt") or "")[:10]
        who = MGR.get(j.get("managerId"), f"мгр{j.get('managerId')}")
        total_all[who] += 1
        if d == today:
            today_all[who] += 1
        subj = _subject(nm)
        cont = subj not in ALWAYS_NEW and subj in past_subj.get(j["userId"], set())
        if d >= since:
            made[who].append({"uid": j["userId"], "date": d, "group": nm,
                              "cont": cont,
                              "zayavka": "аявк" in nm.lower()})

    came = {r.get("userId") for r in recs if r.get("test") and r.get("visit")}
    pay = defaultdict(list)
    for s in subs:
        if (s.get("stats") or {}).get("totalPayed", 0) > 0:
            d = (s.get("beginDate") or "")[:10]
            if d >= since:
                pay[s["userId"]].append(d)

    out = {}
    for who, items in made.items():
        rows, money, forecast = [], 0, 0
        for it in items:
            uid = it["uid"]
            bonus, why = 0, []
            pays = sorted(pay.get(uid, []))
            gap = None
            if pays:
                gap = (datetime.fromisoformat(pays[0]).date()
                       - datetime.fromisoformat(it["date"]).date()).days
            if it["cont"]:
                # продолжающий: деньги только за покупку
                if pays and pays[0] <= DEADLINE:
                    bonus += RATE_BUY_FAST; why.append("купил до 31.08 +300")
                elif gap is not None and gap <= 14:
                    bonus += RATE_BUY_2W; why.append("купил за 2 недели +150")
                else:
                    why.append("ждём оплату")
                forecast += RATE_BUY_FAST
            else:
                # новый предмет: пробное + покупка
                if uid in came:
                    bonus += RATE_TRIAL; why.append("дошёл на пробное +300")
                if pays and (pays[0] <= DEADLINE or (gap is not None and gap <= 0)):
                    bonus += RATE_BUY_FAST; why.append("купил сразу +300")
                elif gap is not None and gap <= 14:
                    bonus += RATE_BUY_2W; why.append("купил за 2 недели +150")
                if not why:
                    why.append("ждём пробного")
                forecast += RATE_TRIAL + RATE_BUY_FAST
            if who in PAID_ADMINS:
                money += bonus
            rows.append({**it, "name": (byid.get(uid, {}).get("name") or "")[:26],
                         "came": uid in came, "paid": bool(pays),
                         "bonus": bonus if who in PAID_ADMINS else 0,
                         "why": ", ".join(why)})
        new_no_trial = sum(1 for r in rows if not r["cont"] and not r["came"])
        trial_pot = (money + new_no_trial * RATE_TRIAL) if who in PAID_ADMINS else 0
        out[who] = {"rows": sorted(rows, key=lambda r: r["date"]),
                    "trial_pot": trial_pot,
                    "count": len(rows),
                    "total_all": total_all.get(who, 0),
                    "today": today_all.get(who, 0),
                    "cont": sum(1 for r in rows if r["cont"]),
                    "zayavki": sum(1 for r in rows if r["zayavka"]),
                    "came": sum(1 for r in rows if r["came"]),
                    "paid": sum(1 for r in rows if r["paid"]),
                    "money": money,
                    "forecast": forecast if who in PAID_ADMINS else 0}
    for who, n in total_all.items():
        if who not in out:
            out[who] = {"rows": [], "count": 0, "total_all": n,
                        "today": today_all.get(who, 0), "cont": 0,
                        "zayavki": 0, "came": 0, "paid": 0,
                        "money": 0, "forecast": 0, "trial_pot": 0}
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
    """Прогноз владельца: каждая запись доходит до пробного и кончается
    покупкой сразу. Новый предмет — 600 ₽, продолжающий — 300 ₽."""
    out = {}
    for who, d in data.items():
        if who not in PAID_ADMINS:
            out[who] = {"n": d["count"], "max": 0, "note": "без денежной схемы"}
            continue
        new_n = d["count"] - d["cont"]
        out[who] = {"n": d["count"], "max": d["forecast"],
                    "note": f"{new_n} новых × 600 + {d['cont']} продолж. × 300"}
    return out


def page(data: dict) -> str:
    order = sorted(data, key=lambda w: -data[w]["total_all"])
    out = [f"<style>{CSS}</style>", "<h1>Рейтинг администраторов и бонусы</h1>",
           f"<div class=sub>Обновлено {datetime.now():%d.%m.%Y %H:%M}. Схема "
           f"владельца от 26.08. Новый предмет (и любая запись на английский "
           f"или подготовку к школе — там все педагоги новые): дошёл до "
           f"пробного +300 ₽, купил в день пробного или до 31.08 ещё +300 ₽, "
           f"купил в течение 2 недель +150 ₽ вместо трёхсот. Продолжающий "
           f"(тот же предмет к тому же педагогу): купил до 31.08 +300 ₽, "
           f"за 2 недели +150 ₽. Денежная схема — у Ани, Иры и Админа "
           f"Бураковых.</div>",
           "<div class=warn>«Кто записал» в МойКласс не хранится — считаем "
           "по ответственному менеджеру записи; менеджера можно "
           "переназначить, и тогда запись уедет в чужую колонку. Скидка в "
           "день пробного — 10%.</div>",
           "<h2>Итог</h2><table><tr><th>Кто</th><th>Записей всего</th>"
           "<th>Сегодня</th><th>С 17.08</th><th>из них заявки</th>"
           "<th>Продолж.</th><th>Дошли</th><th>Оплатили</th>"
           "<th>Начислено (факт, включая проданные абонементы)</th>"
           "<th>Если все дойдут до пробного</th>"
           "<th>Максимум: все дойдут и купят</th></tr>"]
    t = {"all": 0, "today": 0, "cnt": 0, "money": 0, "fc": 0, "tp": 0}
    for w in order:
        d = data[w]
        t["all"] += d["total_all"]; t["today"] += d["today"]
        t["cnt"] += d["count"]; t["money"] += d["money"]; t["fc"] += d["forecast"]
        t["tp"] += d.get("trial_pot", 0)
        fc = f"{d['forecast']:,} ₽".replace(",", " ") if w in PAID_ADMINS else "—"
        mn = f"{d['money']:,} ₽".replace(",", " ") if w in PAID_ADMINS else "—"
        tp = f"{d.get('trial_pot',0):,} ₽".replace(",", " ") if w in PAID_ADMINS else "—"
        out.append(f"<tr><td><b>{w}</b></td><td>{d['total_all']}</td>"
                   f"<td>{d['today']}</td><td>{d['count']}</td>"
                   f"<td>{d['zayavki']}</td><td>{d['cont']}</td>"
                   f"<td>{d['came']}</td><td>{d['paid']}</td>"
                   f"<td class=money>{mn}</td><td class=money>{tp}</td>"
                   f"<td class=money>{fc}</td></tr>")
    out.append(f"<tr class=tot><td>Всего</td><td>{t['all']}</td>"
               f"<td>{t['today']}</td><td>{t['cnt']}</td><td></td><td></td>"
               f"<td></td><td></td>"
               f"<td class=money>{t['money']:,} ₽</td>"
               f"<td class=money>{t['tp']:,} ₽</td>"
               f"<td class=money>{t['fc']:,} ₽</td></tr></table>"
               .replace(",", " "))
    for w in order:
        d = data[w]
        if not d["rows"]:
            continue
        head = f"{w} — {d['count']} записей с 17.08"
        if w in PAID_ADMINS:
            head += (f", начислено {d['money']} ₽, прогноз "
                     f"{d['forecast']:,} ₽".replace(",", " "))
        out.append(f"<h2>{head}</h2>"
                   "<table><tr><th>Дата</th><th>Клиент</th><th>Группа</th>"
                   "<th>Тип</th><th>Начислено</th></tr>")
        for r in d["rows"]:
            tp = "продолж." if r["cont"] else ("заявка" if r["zayavka"] else "новый")
            out.append(f"<tr><td>{r['date'][8:10]}.{r['date'][5:7]}</td>"
                       f"<td>{_html.escape(r['name'] or '—')}</td>"
                       f"<td>{_html.escape(r['group'][5:60])}</td>"
                       f"<td>{tp}</td>"
                       f"<td class=money>{r['bonus']} ₽ "
                       f"<span class=rate>{_html.escape(r['why'])}</span></td></tr>")
        out.append("</table>")
    return "\n".join(out)


def main():
    from pathlib import Path
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    data = collect()
    html_out = page(data)
    p = Path(__file__).resolve().parent.parent / "docs" / "bonusy_raschet.html"
    p.write_text(html_out, encoding="utf-8")
    for w, d in sorted(data.items(), key=lambda x: -x[1]["total_all"]):
        print(f"{w:16} всего {d['total_all']:3}  сегодня {d['today']:2}  "
              f"с 17.08 {d['count']:3}  начислено {d['money']:5} ₽  "
              f"прогноз {d['forecast']:6} ₽")
    print(p)


if __name__ == "__main__":
    main()
