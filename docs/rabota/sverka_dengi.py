# -*- coding: utf-8 -*-
"""Сверка денег KidsUP: тетрадь кассы ↔ МойКласс ↔ КОМТЕТ ↔ Т-Банк + долги.

Запуск: python3 docs/rabota/sverka_dengi.py <папка с выгрузками> [С] [ПО]
В папке: payments.json, users.json, user_subscriptions.json, invoices.json,
managers.json (/export/raw/…), komtet.json (/api/komtet/cheki),
bank_40802810200001196965.json (выписка T-API), dolgi.json (файл Лизы, опц.).
Пишет docs/rabota/kontrol/sverka.html (страница /sverka, только владелец)
и sverka.json рядом.

Тетрадь кассы перенесена с фото руками (04.10.2026, 8 фото от Иры,
записи 19.08–04.10). Суммы — «чистые», т.е. за вычетом выданной сдачи.
"""
from __future__ import annotations

import html
import itertools
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

D = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
START = sys.argv[2] if len(sys.argv) > 2 else "2026-08-10"
END = sys.argv[3] if len(sys.argv) > 3 else "2026-10-04"
OUT = Path(__file__).resolve().parent / "kontrol"

TYPES = {1: "наличные", 2: "онлайн-ссылка", 64725: "терминал", 63934: "перевод на счёт",
         72508: "коррекция", 97845: "возврат (тип)"}

# ── тетрадь кассы: (дата, сумма, что написано) ; + приход, − расход ──────────
TETRAD_START = "2026-08-19"
TETRAD = [
    ("2026-08-21", 500, "лагерь полдня, пробный Шальнев Дмитрий"),
    ("2026-08-26", 4400, "оплата логопеда за 2х детей"),
    ("2026-08-26", 100, "Мария дала на сдачу из личных"),
    ("2026-09-01", 30000, "мини-сад, на баланс"),
    ("2026-09-01", 7897.5, "раннее развитие — Ирина (дали 8 000, должны сдачу 102,5)"),
    ("2026-09-01", 8505, "ПШ"),
    ("2026-09-01", 1600, "оплата разового"),
    ("2026-09-01", -45000, "ИЗЪЯТИЕ (подпись Б.И.)"),
    ("2026-09-02", 9225, "раннее развитие (дали 10 000, сдача 775)"),
    ("2026-09-03", 1600, "тетради (должны сдачу 100)"),
    ("2026-09-04", 5850, "дали 10 000, сдача 4 000 + должны 150"),
    ("2026-09-05", 5000, "раннее развитие"),
    ("2026-09-07", -6794, "Комус (чек)"),
    ("2026-09-07", 3300, "РР муз. с на…"),
    ("2026-09-07", 1000, "пробное, ИЗО"),
    ("2026-09-07", 7100, "ИЗО"),
    ("2026-09-07", -78, "по чеку Виктории"),
    ("2026-09-08", 9060, "ПШ + тетради (дали 10 000, сдачу должны)"),
    ("2026-09-08", 1500, "тетради"),
    ("2026-09-08", 1500, "тетради"),
    ("2026-09-09", -277, "чек Виктории"),
    ("2026-09-10", -3500, "букет Ирине (чек в почте)"),
    ("2026-09-10", 2000, "тетради"),
    ("2026-09-11", 2500, "Елена лог."),
    ("2026-09-12", 4900, "лог. Марина (5 000, сдача 100)"),
    ("2026-09-12", 3900, "ПШ (5 000, сдача 1 100)"),
    ("2026-09-12", 5962, "ПШ + тетради (6 000, сдача 38)"),
    ("2026-09-12", 2700, "в итоге дня, отдельной строкой не записано"),
    ("2026-09-13", 8287.5, "Ирина, первая школа: 8 000 нал + 287,5 картой"),
    ("2026-09-13", 5500, "мент. арифм."),
    ("2026-09-14", 2500, "логопед"),
    ("2026-09-14", 5000, "логопед — 2 зан."),
    ("2026-09-14", -710, "ИЗО, чек Виктории"),
    ("2026-09-15", -1979, "Дикси (чек)"),
    ("2026-09-15", 1500, "тетради"),
    ("2026-09-15", 1500, "тетради"),
    ("2026-09-15", -80000, "ИЗЪЯТИЕ (подпись Б.И.)"),
    ("2026-09-16", 4140, "Лицей д/м"),
    ("2026-09-16", 4150, "Лицей д/м"),
    ("2026-09-18", 2500, "логопед Елены"),
    ("2026-09-18", 850, "шахматы (1 000, сдача 150)"),
    ("2026-09-19", 4900, "5 000, сдача 100"),
    ("2026-09-19", 2700, "лого"),
    ("2026-09-20", 3200, "дали 5 200 / сдала 2 000"),
    ("2026-09-21", 1600, "пробник ИЗО"),
    ("2026-09-21", -170, "чек Виктории, ИЗО"),
    ("2026-09-22", 9950, "оплата 12 занятий"),
    ("2026-09-23", 8200, "оплата ИЗО"),
    ("2026-09-26", 9817, "ПШ"),
    ("2026-09-26", 1500, "книги"),
    ("2026-09-26", 2700, "лого"),
    ("2026-09-27", 1600, "роботы"),
    ("2026-09-28", -44, "ватные палочки"),
    ("2026-09-28", 277, "возврат (чек Виктории)"),
    ("2026-09-29", 4875, "РР — Ирине"),
    ("2026-09-29", 9450, "ПШ"),
    ("2026-10-01", 2700, "логопед"),
    ("2026-10-02", 7900, ""),
    ("2026-10-03", 5200, "МсМ"),
    ("2026-10-04", 3875, "РР (100 р перевести)"),
]
TETRAD_ITOG = 99033   # «в кассе на конец дня» 04.10


def load(n, default=None):
    p = D / n
    return json.loads(p.read_text()) if p.exists() else default


P = load("payments.json")
U = {u["id"]: u for u in load("users.json")}
S = load("user_subscriptions.json")
M = {m["id"]: (m.get("name") or "").split()[0] for m in load("managers.json")}
K = load("komtet.json")["items"]
B = load("bank_40802810200001196965.json")

name = lambda uid: (U.get(uid) or {}).get("name") or f"#{uid}"
man = lambda p: M.get(p.get("managerId"), "—")
inper = lambda d: START <= d <= END
msk = lambda s: (datetime.fromisoformat(s.replace("Z", "+00:00")) + timedelta(hours=3))
dd = lambda s: date.fromisoformat(s)
INC = [p for p in P if p["optype"] == "income" and inper(p["date"]) and p["summa"] > 1]

# ── банк ─────────────────────────────────────────────────────────────────────
card = defaultdict(float)      # торговый эквайринг, по дню операции (реестр D+1)
web = defaultdict(float)       # интернет-эквайринг (договор 7010024896), брутто по дате реестра
sbp = []                       # СБП поштучно
bank_refunds, bank_other = [], []
for o in B:
    pp = o.get("payPurpose") or ""
    if o["typeOfOperation"] == "Credit":
        m = re.search(r"терминалам эквайринга от (\d\d)\.(\d\d)\.(\d{4})", pp)
        if m:
            card[(date(int(m[3]), int(m[2]), int(m[1])) - timedelta(1)).isoformat()] += o["operationAmount"]
            continue
        m = re.search(r"реестру операций от (\d\d)\.(\d\d)\.(\d{4})", pp)
        if m:
            k = re.search(r"комиссии (\d+) руб\. (\d+) коп", pp)
            web[f"{m[3]}-{m[2]}-{m[1]}"] += o["operationAmount"] + (int(k[1]) + int(k[2]) / 100 if k else 0)
            continue
        if "СБП" in pp:
            t = msk(o.get("authorizationDate") or o["operationDate"])
            sbp.append({"d": t.date().isoformat(), "t": t.strftime("%d.%m %H:%M"), "sum": o["operationAmount"], "op": pp.split()[4] if len(pp.split()) > 4 else ""})
            continue
        bank_other.append(o)
    elif "Возврат" in pp:
        bank_refunds.append((msk(o["operationDate"]).date().isoformat(), o["operationAmount"], pp))

# ── КОМТЕТ: чек ↔ оплата ─────────────────────────────────────────────────────
byid = {str(p["id"]): p for p in P}
kt_err, kt_free, kt_bur = [], [], []
check = {}                     # id оплаты МК → чек
for x in K:
    if "Буракова" in x["shop"]["name"]:
        kt_bur.append(x); continue
    d = msk(x["created_time"]).date().isoformat()
    if not (START <= d <= END) and x["state"] == "done":
        continue
    if x["state"] != "done":
        kt_err.append(x); continue
    ext = str(x.get("external_id"))
    if ext in byid:
        check[ext] = x; continue
    nm = " ".join(q["name"] for q in x["body"].get("positions", []))
    m = re.search(r"Уч\.? ?№ ?(\d+)", nm)
    uid = int(m[1]) if m else None
    cand = [p for p in INC if p["userId"] == uid and abs(p["summa"] - x["amount"]) < 0.5
            and abs((dd(p["date"]) - dd(d)).days) <= 3 and str(p["id"]) not in check]
    if cand:
        check[str(cand[0]["id"])] = x
    else:
        kt_free.append(x)

# ── СБП поштучно → оплаты МК (любого типа) ────────────────────────────────────
sbp_of = {}                    # id оплаты → операция СБП
for s in sorted(sbp, key=lambda s: s["d"]):
    if not inper(s["d"]):
        continue
    best = None
    for lag in (0, 1, -1):
        for pref in (2, 64725, 1):
            dl = (dd(s["d"]) + timedelta(lag)).isoformat()
            c = [p for p in INC if p.get("paymentTypeId") == pref and p["date"] == dl
                 and abs(p["summa"] - s["sum"]) < 0.5 and str(p["id"]) not in sbp_of]
            if c:
                best = c[0]; break
        if best:
            break
    if best:
        sbp_of[str(best["id"])] = s
    else:
        s["free"] = True

# ── дневные таблицы: терминал и интернет-эквайринг ───────────────────────────
days = [(dd(START) + timedelta(i)).isoformat() for i in range((dd(END) - dd(START)).days + 1)]
term_rows, web_rows = [], []
for d in days:
    mk_t = [p for p in INC if p["date"] == d and p.get("paymentTypeId") == 64725 and str(p["id"]) not in sbp_of]
    mk_cash_card = []
    s1 = sum(p["summa"] for p in mk_t)
    term_rows.append({"d": d, "mk": s1, "bank": card.get(d, 0.0), "diff": round(s1 - card.get(d, 0.0), 2), "pays": mk_t})
    mk_w = [p for p in INC if p["date"] == d and p.get("paymentTypeId") == 2 and str(p["id"]) not in sbp_of]
    web_rows.append({"d": d, "mk": sum(p["summa"] for p in mk_w), "pays": mk_w})
# последний день, по которому банк уже прислал реестр карты
last_card = max(card) if card else END
term_rows = [r for r in term_rows if r["d"] <= last_card]
for a, b in zip(term_rows, term_rows[1:]):
    if abs(a["diff"]) >= 1 and abs(a["diff"] + b["diff"]) < 1.5:
        a["explain"] = b["explain"] = "сдвиг на день: оплата внесена в МК соседним днём (в сумме за 2 дня сходится)"
        a["shift"] = b["shift"] = True
# интернет-эквайринг: подбираем сдвиг реестра
best_off, best_err = 0, 1e18
for off in (-1, 0, 1, 2):
    err = sum(abs(r["mk"] - web.get((dd(r["d"]) + timedelta(off)).isoformat(), 0)) for r in web_rows)
    if err < best_err:
        best_off, best_err = off, err
for r in web_rows:
    r["bank"] = web.get((dd(r["d"]) + timedelta(best_off)).isoformat(), 0.0)
    r["diff"] = round(r["mk"] - r["bank"], 2)

# наличные в МК, которые по банку прошли картой: день, где банк > МК, и подмножество нал. = разнице
cash_was_card = {}
for r in term_rows:
    if r["diff"] < -1:
        cash = [p for p in INC if p["date"] == r["d"] and p.get("paymentTypeId") == 1 and p["summa"] > 1]
        found = None
        for n in (1, 2, 3):
            for comb in itertools.combinations(cash, n):
                if abs(sum(p["summa"] for p in comb) + r["diff"]) <= 1:
                    found = comb; break
            if found:
                break
        if found:
            for p in found:
                cash_was_card[str(p["id"])] = f"по банку — карта ({r['d'][8:]}.{r['d'][5:7]}: в банке на {-r['diff']:,.0f} ₽ больше)".replace(",", " ")
            r["explain"] = "нал. в МК = " + " + ".join(f"{p['summa']:g} {name(p['userId'])}" for p in found)
for pid, s in sbp_of.items():
    p = byid[pid]
    if p.get("paymentTypeId") == 1:
        cash_was_card[pid] = f"по банку — СБП {s['t']}"

# ── наличные: МК ↔ тетрадь ───────────────────────────────────────────────────
mk_cash = sorted([p for p in INC if p.get("paymentTypeId") == 1], key=lambda p: p["createdAt"])
tin = [{"i": i, "d": d, "sum": s, "note": n, "mk": []} for i, (d, s, n) in enumerate(TETRAD) if s > 0]
used = set()
pool = [p for p in mk_cash if p["date"] >= TETRAD_START and str(p["id"]) not in cash_was_card]
for tol, span, nmax in ((1, 0, 1), (150, 0, 3), (150, 1, 3), (250, 0, 1), (1, 25, 1)):
    for e in tin:
        if e["mk"]:
            continue
        cand = [p for p in pool if str(p["id"]) not in used and abs((dd(p["date"]) - dd(e["d"])).days) <= span]
        hit = None
        for n in range(1, nmax + 1):
            for comb in itertools.combinations(cand, n):
                if abs(sum(p["summa"] for p in comb) - e["sum"]) <= tol:
                    hit = comb; break
            if hit:
                break
        if hit:
            e["mk"] = list(hit)
            used |= {str(p["id"]) for p in hit}
cash_rows = []
for p in mk_cash:
    pid = str(p["id"])
    if p["date"] < TETRAD_START:
        st = "тетрадь за эти дни не прислана"
    elif pid in cash_was_card:
        st = cash_was_card[pid]
    elif pid in used:
        e = next(e for e in tin if p in e["mk"])
        df = sum(x["summa"] for x in e["mk"]) - e["sum"]
        st = f"в тетради {e['d'][8:]}.{e['d'][5:7]}: {e['sum']:g} «{e['note']}»" + (f" — в тетради на {df:g} ₽ меньше" if df > 160 else "")
    else:
        surplus = -next((r["diff"] for r in term_rows if r["d"] == p["date"]), 0) - sum(
            byid[i]["summa"] for i in cash_was_card if byid[i]["date"] == p["date"] and not cash_was_card[i].startswith("по банку — СБП"))
        st = "НЕТ В ТЕТРАДИ" + (f"; но в банке за этот день картой на {surplus:,.0f} ₽ больше, чем в МК — вероятно, оплачено картой".replace(",", " ") if surplus >= p["summa"] - 1 else "")
    cash_rows.append({"p": p, "status": st, "check": pid in check})
tetrad_only = [e for e in tin if not e["mk"]]

# ── онлайн-оплаты: банк + чек по каждой ──────────────────────────────────────
web_pay = []
for p in sorted([p for p in INC if p.get("paymentTypeId") == 2], key=lambda p: p["createdAt"]):
    pid = str(p["id"])
    bank = ("СБП " + sbp_of[pid]["t"]) if pid in sbp_of else "интернет-эквайринг (сумма дня)"
    web_pay.append({"p": p, "bank": bank, "check": pid in check})

# ── долги ────────────────────────────────────────────────────────────────────
minus = sorted([u for u in U.values() if (u.get("balans") or 0) < -1], key=lambda u: u["balans"])
nonpaid = []
for u in U.values():
    for j in u.get("joins") or []:
        st = j.get("stats") or {}
        if (st.get("nonPayedLessons") or 0) > 0 and (st.get("lastVisit") or "") >= START:
            nonpaid.append((u, j))
subs_by = defaultdict(list)
for s in S:
    subs_by[s["userId"]].append(s)
unpaid_subs = [s for s in S if (s.get("sellDate") or "") >= START and (s.get("price") or 0) - (s.get("payed") or 0) > 1]
LIZA = load("dolgi.json", None) or json.loads((OUT / "dolgi_liza_04-10.json").read_text(encoding="utf-8"))

# ── HTML ─────────────────────────────────────────────────────────────────────
e = html.escape
rub = lambda x: f"{x:,.2f}".replace(",", " ").replace(".00", "") + " ₽"
def ddmm(d): return f"{d[8:]}.{d[5:7]}"


def pay_line(p):
    return f"{ddmm(p['date'])} · {e(man(p))} · {rub(p['summa'])} · {e(name(p['userId']))}"


css = """
:root{--ink:#221F3B;--indigo:#312783;--blue:#1DA7E0;--green:#3f7d12;--amber:#b86e00;--red:#B3261E;
--line:#E4E8F3;--soft:#F5F7FC;--bg:#FBFCFE;--card:#fff;--muted:#6A6F87}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--ink:#E8E9F3;--indigo:#B9B3FF;--line:#2E3247;--soft:#1B1E2C;--bg:#12141E;--card:#191C29;--muted:#9A9EB5;--green:#8fd45a;--amber:#f0b04a;--red:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 Inter,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:20px 16px 60px}
h1{font:700 24px/1.2 Rubik,Inter,sans-serif;color:var(--indigo);margin:0 0 6px}
h2{font:700 18px/1.3 Rubik,Inter,sans-serif;color:var(--indigo);margin:28px 0 8px}
h3{font-size:15px;margin:18px 0 6px}
.lead{color:var(--muted);margin:0 0 14px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin:12px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.kpi b{display:block;font-size:20px;font-variant-numeric:tabular-nums}
.kpi span{color:var(--muted);font-size:12.5px}
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--card);margin:8px 0}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:6px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{background:var(--soft);font-weight:600;font-size:12.5px;position:sticky;top:0}
td.n{text-align:right;white-space:nowrap}
.bad{color:var(--red);font-weight:600}.warn{color:var(--amber);font-weight:600}.ok{color:var(--green)}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;border:1px solid currentColor}
details{margin:6px 0}summary{cursor:pointer;color:var(--indigo);font-weight:600}
.note{background:var(--soft);border:1px solid var(--line);border-radius:12px;padding:10px 14px;color:var(--muted)}
"""
H = [f"<!doctype html><html lang=ru><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Сверка денег</title><style>{css}</style></head><body><div class=wrap>"]
H.append(f"<h1>Сверка денег {ddmm(START)} — {ddmm(END)}</h1><p class=lead>Тетрадь кассы ↔ МойКласс ↔ КОМТЕТ ↔ Т-Банк (торговый и интернет-эквайринг, СБП). Собрано {datetime.now().strftime('%d.%m.%Y %H:%M')}.</p>")

by_t = Counter(); by_tn = Counter()
for p in INC:
    by_t[p.get("paymentTypeId")] += p["summa"]; by_tn[p.get("paymentTypeId")] += 1
H.append("<div class=kpis>" + "".join(f"<div class=kpi><b>{rub(by_t[t])}</b><span>{TYPES.get(t, t)} · {by_tn[t]} оплат в МК</span></div>" for t in sorted(by_t, key=lambda t: -by_t[t])) + "</div>")

# --- итог
nocash = [r for r in cash_rows if r["status"] == "НЕТ В ТЕТРАДИ"]
nocash_maybe = [r for r in cash_rows if r["status"].startswith("НЕТ В ТЕТРАДИ;")]
term_mk_more = [r for r in term_rows if r["diff"] > 1 and not r.get("shift")]
term_bank_more = [r for r in term_rows if r["diff"] < -1 and not r.get("explain")]
H.append("<h2>Главное</h2><ul>")
H.append(f"<li><b class=bad>Наличные в МойКлассе, которых нет в тетради и нет в банке: {len(nocash)} на {rub(sum(r['p']['summa'] for r in nocash))}</b> — список в разделе 1.</li>")
H.append(f"<li class=warn>Нет в тетради, но в тот день в банке «лишние» деньги картой (вероятно, оплачено картой): {len(nocash_maybe)} на {rub(sum(r['p']['summa'] for r in nocash_maybe))}.</li>")
H.append(f"<li>Наличные, записанные в МК, но по банку прошедшие картой/СБП: {len(cash_was_card)} на {rub(sum(byid[i]['summa'] for i in cash_was_card))} — не хищение, путаница типа оплаты.</li>")
H.append(f"<li>Чеки КОМТЕТ на наличные: {sum(1 for r in cash_rows if r['check'])} из {len(cash_rows)}; на онлайн-оплаты: {sum(1 for r in web_pay if r['check'])} из {len(web_pay)}; на терминал — {sum(1 for p in INC if p.get('paymentTypeId')==64725 and str(p['id']) in check)} из {by_tn[64725]}.</li>")
H.append(f"<li>Терминал ↔ банк: дней, где в МК больше, чем пришло, — {len(term_mk_more)} на {rub(sum(r['diff'] for r in term_mk_more))}; где в банке больше (оплата не внесена) — {len(term_bank_more)} на {rub(-sum(r['diff'] for r in term_bank_more))}.</li>")
H.append(f"<li>Интернет-эквайринг ↔ МК (онлайн-ссылки): за период МК {rub(sum(r['mk'] for r in web_rows))}, банк {rub(sum(r['bank'] for r in web_rows))} (реестр банка = день оплаты {'+' if best_off>=0 else ''}{best_off}).</li>")
H.append(f"<li>Чеки КОМТЕТ в статусе «ошибка» (не пробиты): {len(kt_err)} на {rub(sum(x['amount'] for x in kt_err))}.</li>")
H.append(f"<li>Тетрадь: остаток на 04.10 — {rub(TETRAD_ITOG)}; изъятия владельцем {rub(-sum(s for d,s,n in TETRAD if 'ИЗЪЯТИЕ' in n))}; расходы {rub(-sum(s for d,s,n in TETRAD if s<0 and 'ИЗЪЯТИЕ' not in n))}.</li></ul>")

# --- 1. наличные
H.append("<h2>1. Наличные: МойКласс ↔ тетрадь ↔ чек КОМТЕТ</h2><div class=tw><table><tr><th>Дата</th><th>Внесено</th><th>Админ</th><th class=n>Сумма</th><th>Клиент</th><th>За что</th><th>Тетрадь / банк</th><th>Чек</th></tr>")
for r in cash_rows:
    p = r["p"]; st = r["status"]
    cls = "bad" if st == "НЕТ В ТЕТРАДИ" else ("warn" if ("банк" in st or "не прислана" in st or "меньше" in st) else "ok")
    H.append(f"<tr><td>{ddmm(p['date'])}</td><td>{p['createdAt'][8:10]}.{p['createdAt'][5:7]} {p['createdAt'][11:16]}</td><td>{e(man(p))}</td><td class=n>{rub(p['summa'])}</td><td>{e(name(p['userId']))}</td><td>{e((p.get('comment') or '')[:70])}</td><td class={cls}>{e(st)}</td><td>{'✓' if r['check'] else '<span class=bad>нет</span>'}</td></tr>")
H.append("</table></div>")
H.append("<h3>Есть в тетради, нет в МойКлассе (как наличные)</h3><div class=tw><table><tr><th>Дата</th><th class=n>Сумма</th><th>Запись</th></tr>" + "".join(f"<tr><td>{ddmm(t['d'])}</td><td class=n>{rub(t['sum'])}</td><td>{e(t['note'])}</td></tr>" for t in tetrad_only) + "</table></div>")
H.append("<h3>Расходы и изъятия по тетради</h3><div class=tw><table><tr><th>Дата</th><th class=n>Сумма</th><th>Запись</th></tr>" + "".join(f"<tr><td>{ddmm(d)}</td><td class=n>{rub(s)}</td><td>{e(n)}</td></tr>" for d, s, n in TETRAD if s < 0) + "</table></div>")

# --- 2. терминал
H.append(f"<h2>2. Торговый эквайринг (терминал) ↔ МойКласс</h2><p class=lead>Банк зачисляет карту за день D реестром D+1. СБП через терминал сверены поштучно и сюда не входят.</p><div class=tw><table><tr><th>Дата</th><th class=n>МК терминал</th><th class=n>Банк карта</th><th class=n>Разница</th><th>Объяснение / оплаты дня</th></tr>")
for r in term_rows:
    if abs(r["diff"]) < 1 and not r["mk"]:
        continue
    cls = "ok" if abs(r["diff"]) < 1 or r.get("shift") else ("bad" if r["diff"] > 0 else "warn")
    det = e(r.get("explain") or "")
    if abs(r["diff"]) >= 1:
        det += "<details><summary>оплаты дня</summary>" + "<br>".join(pay_line(p) for p in r["pays"]) + "</details>"
    H.append(f"<tr><td>{ddmm(r['d'])}</td><td class=n>{rub(r['mk'])}</td><td class=n>{rub(r['bank'])}</td><td class='n {cls}'>{rub(r['diff'])}</td><td>{det}</td></tr>")
H.append("</table></div><p class=note>Плюс в «разнице» — в МК записано больше, чем пришло на счёт (опасное направление). Минус — деньги на счёте есть, а оплата в МК не внесена или внесена как наличные.</p>")

# --- 3. онлайн
H.append(f"<h2>3. Онлайн-оплаты (ссылки МойКласса) ↔ интернет-эквайринг / СБП ↔ чек КОМТЕТ</h2><div class=tw><table><tr><th>Дата</th><th class=n>МК (без СБП)</th><th class=n>Банк реестр</th><th class=n>Разница</th></tr>")
for r in web_rows:
    if r["mk"] or r["bank"]:
        cls = "ok" if abs(r["diff"]) < 1 else "warn"
        H.append(f"<tr><td>{ddmm(r['d'])}</td><td class=n>{rub(r['mk'])}</td><td class=n>{rub(r['bank'])}</td><td class='n {cls}'>{rub(r['diff'])}</td></tr>")
H.append("</table></div>")
nochk = [w for w in web_pay if not w["check"]]
H.append(f"<h3>Онлайн-оплаты без чека КОМТЕТ: {len(nochk)}</h3><div class=tw><table><tr><th>Оплата</th><th>Банк</th></tr>" + "".join(f"<tr><td>{pay_line(w['p'])}</td><td>{e(w['bank'])}</td></tr>" for w in nochk) + "</table></div>")
free_sbp = [s for s in sbp if s.get("free") and inper(s["d"])]
H.append(f"<h3>СБП в банке без оплаты в МК: {len(free_sbp)}</h3>" + ("<div class=tw><table><tr><th>Время</th><th class=n>Сумма</th><th>Операция</th></tr>" + "".join(f"<tr><td>{s['t']}</td><td class=n>{rub(s['sum'])}</td><td>{e(s['op'])}</td></tr>" for s in free_sbp) + "</table></div>" if free_sbp else "<p class=ok>нет</p>"))

# --- 4. КОМТЕТ
H.append(f"<h2>4. КОМТЕТ</h2><h3>Чеки без оплаты в МК: {len(kt_free)}</h3>")
if kt_free:
    H.append("<div class=tw><table><tr><th>Время</th><th class=n>Сумма</th><th>Позиция</th></tr>" + "".join(f"<tr><td>{msk(x['created_time']).strftime('%d.%m %H:%M')}</td><td class=n>{rub(x['amount'])}</td><td>{e(' '.join(q['name'] for q in x['body']['positions'])[:110])}</td></tr>" for x in kt_free) + "</table></div>")
H.append(f"<h3>Чеки с ошибкой (не пробиты): {len(kt_err)}</h3><div class=tw><table><tr><th>Время</th><th class=n>Сумма</th><th>Позиция</th></tr>" + "".join(f"<tr><td>{msk(x['created_time']).strftime('%d.%m %H:%M')}</td><td class=n>{rub(x['amount'])}</td><td>{e(' '.join(q['name'] for q in x['body']['positions'])[:110])}</td></tr>" for x in kt_err) + "</table></div>")
H.append("<p class=note>Оплаты по терминалу в КОМТЕТ не пробиваются (кроме единичных). Если терминал Т-Банка не печатает фискальный чек сам — по терминальным оплатам чеков нет вовсе.</p>")

# --- 5. возвраты
refunds = [p for p in P if inper(p["date"]) and p["optype"] == "refund"]
H.append("<h2>5. Возвраты</h2><div class=tw><table><tr><th>Дата</th><th>Кто</th><th class=n>Сумма</th><th>Клиент</th></tr>" + "".join(f"<tr><td>{ddmm(p['date'])}</td><td>{e(man(p))}</td><td class=n>{rub(p['summa'])}</td><td>{e(name(p['userId']))} {e(p.get('comment') or '')}</td></tr>" for p in refunds) + "</table></div>")
H.append("<p>В банке: " + "; ".join(f"{ddmm(d)} {rub(s)} — {e(t[:60])}" for d, s, t in bank_refunds) + "</p>")

# --- 6. долги
cur_minus = [u for u in minus if any(j.get("statusId") == 2 for j in (u.get("joins") or []))]
old_minus = [u for u in minus if u not in cur_minus]
def sub_kind(s):
    same = [x for x in subs_by[s["userId"]] if x["id"] != s["id"] and x.get("payed", 0) > 0
            and set(x.get("classIds") or []) & set(s.get("classIds") or []) and (x.get("beginDate") or "")[:7] == (s.get("beginDate") or "")[:7]]
    if same:
        return "ДУБЛЬ — такой же абонемент на этот месяц уже оплачен; удалить", "warn"
    if (s.get("beginDate") or "") >= "2026-10-01":
        return f"октябрь — ждём оплату, посещено {s.get('visitedCount')}", "warn" if s.get("visitedCount") else ""
    return f"НЕОПЛАЧЕННЫЙ СЕНТЯБРЬ — посещено {s.get('visitedCount')} из {s.get('visitCount')}", "bad"
H.append("<h2>6. Долги</h2>")
H.append(f"<h3>Абонементы, оплаченные не полностью: {len(unpaid_subs)}</h3><div class=tw><table><tr><th>Продан</th><th>Админ</th><th>Клиент</th><th class=n>Цена</th><th class=n>Оплачено</th><th class=n>Долг</th><th>Что это</th><th>Комментарий</th></tr>")
for s in sorted(unpaid_subs, key=lambda s: (sub_kind(s)[0][:3], s["sellDate"])):
    k, cls = sub_kind(s)
    H.append(f"<tr><td>{ddmm(s['sellDate'])}</td><td>{e(M.get(s.get('managerId'),'—'))}</td><td>{e(name(s['userId']))}</td><td class=n>{rub(s['price'])}</td><td class=n>{rub(s['payed'])}</td><td class=n>{rub(s['price']-s['payed'])}</td><td class={cls}>{e(k)}</td><td>{e(s.get('comment') or '')}</td></tr>")
H.append("</table></div>")
trial = [(u, j) for u, j in nonpaid if j["statusId"] in (58131, 58132)]
real = [(u, j) for u, j in nonpaid if (u, j) not in trial]
H.append(f"<h3>Ходили сверх абонемента / без абонемента: {len(real)}</h3><div class=tw><table><tr><th>Клиент</th><th class=n>Неоплач. занятий</th><th>Посл. визит</th><th class=n>Баланс</th></tr>" + "".join(f"<tr><td>{e(u['name'])}</td><td class=n>{j['stats']['nonPayedLessons']}</td><td>{ddmm(j['stats']['lastVisit'])}</td><td class=n>{rub(u.get('balans') or 0)}</td></tr>" for u, j in sorted(real, key=lambda x: -x[1]['stats']['nonPayedLessons'])) + "</table></div>")
H.append(f"<h3>Пробные без решения (не долг, нужен звонок): {len(trial)}</h3><p>" + ", ".join(f"{e(u['name'])} ({ddmm(j['stats']['lastVisit'])})" for u, j in trial) + "</p>")
H.append(f"<h3>Старые долги (нет активных групп): {len(old_minus)} на {rub(sum(u['balans'] for u in old_minus))}</h3><div class=tw><table><tr><th>Клиент</th><th class=n>Баланс</th><th>Последняя оплата</th></tr>")
lastpay = {}
for p in P:
    if p["optype"] == "income" and p["summa"] > 1 and (p["userId"] not in lastpay or p["date"] > lastpay[p["userId"]]["date"]):
        lastpay[p["userId"]] = p
for u in old_minus:
    lp = lastpay.get(u["id"])
    H.append(f"<tr><td>{e(u['name'])}</td><td class='n bad'>{rub(u['balans'])}</td><td>{(lp['date']+' · '+rub(lp['summa'])) if lp else 'оплат нет'}</td></tr>")
H.append("</table></div><p class=note>Старые минусы — прошлые сезоны (часть, вероятно, маткапитал или не закрытые списания). Решение: списать или выставить — за владельцем.</p>")
if LIZA:
    H.append("<h3>Файл Лизы — разбор по каждому</h3><div class=tw><table><tr><th>Ребёнок</th><th>Лиза</th><th>Что в МойКлассе / банке / тетради</th><th>Вывод — что сделать</th></tr>" + "".join(f"<tr><td>{e(r['name'])}</td><td>{e(r['liza'])}</td><td>{e(r['mk'])}</td><td>{e(r['vyvod'])}</td></tr>" for r in LIZA) + "</table></div>")

# --- 7. маткапиталы: ОСФР → счёт Буракова → счета KidsUP → баланс клиента в МК → абонемент
BUR = load("bank_40802810600000603211.json", [])
if BUR:
    norm = lambda s: (s or "").replace("ё", "е").lower()
    moved = [o for o in BUR if o["typeOfOperation"] == "Debit" and o.get("category") == "selfTransferOuter"]
    rows = []
    for o in sorted(BUR, key=lambda o: o["operationDate"]):
        if "СФР" not in (o.get("payer") or {}).get("name", "") or o["operationDate"][:10] < "2026-05-01":
            continue
        pp = o.get("payPurpose") or ""
        m = re.search(r"ФИО обуч\.?\s*([А-ЯЁ][а-яё-]+)\s+([А-ЯЁ][а-яё]+)", pp) or re.search(r"расх\s+([А-ЯЁ][а-яё-]+)\s+([А-ЯЁ][а-яё]+)", pp)
        fam, im = (m[1], m[2]) if m else ("?", "?")
        amt, d = o["operationAmount"], msk(o["operationDate"]).date().isoformat()
        us = [u for u in U.values() if norm(fam) in norm(u["name"]) and norm(im) in norm(u["name"])]
        ids = {u["id"] for u in us}
        mkp = [p for p in P if p["userId"] in ids and p["optype"] == "income" and abs(p["summa"] - amt) < 1 and p["date"] >= "2026-04-01"]
        near = [p for p in P if p["userId"] in ids and p["optype"] == "income" and p.get("paymentTypeId") == 63934
                and abs((dd(p["date"]) - dd(d)).days) <= 25]
        tr = [x for x in moved if abs(x["operationAmount"] - amt) < 1 or x["operationAmount"] > amt][:1]
        bal = sum((u.get("balans") or 0) for u in us)
        if not us:
            st, cls = "КАРТОЧКИ НЕТ — деньги в МК не учтены", "bad"
        elif mkp:
            p0 = mkp[0]
            st, cls = f"внесено {ddmm(p0['date'])} ({e(M.get(p0.get('managerId'),'—'))}), баланс сейчас {rub(bal)}", "ok"
        elif near:
            st, cls = "в МК другая сумма: " + ", ".join(f"{ddmm(p['date'])} {rub(p['summa'])}" for p in near) + f" — разница", "warn"
        else:
            st, cls = f"НЕ ВНЕСЕНО в МК (баланс {rub(bal)})", "bad"
        rows.append((d, amt, f"{fam} {im}", st, cls))
    H.append(f"<h2>7. Маткапиталы (ОСФР → счёт Буракова → KidsUP → баланс в МК)</h2><div class=tw><table><tr><th>Пришло</th><th class=n>Сумма</th><th>Ребёнок</th><th>В МойКлассе</th></tr>"
             + "".join(f"<tr><td>{ddmm(d)}</td><td class=n>{rub(a)}</td><td>{e(n)}</td><td class={c}>{s}</td></tr>" for d, a, n, s, c in rows) + "</table></div>")
    H.append("<p class=note>С 03.08 все суммы маткапиталов переведены со счёта Буракова на счета KidsUP (основной / маткапитал) в день поступления или в течение недели. После внесения на баланс Лиза должна создать абонемент со списанием с баланса — проверка «баланс висит, абонемента на месяц нет» в таблице ниже.</p>")
    hang = []
    for u in U.values():
        if (u.get("balans") or 0) > 5000:
            mk_in = [p for p in P if p["userId"] == u["id"] and p.get("paymentTypeId") == 63934 and p["date"] >= "2026-04-01"]
            if mk_in:
                octs = [s for s in subs_by[u["id"]] if (s.get("beginDate") or "") >= "2026-10-01"]
                hang.append((u["name"], u["balans"], len(octs)))
    if hang:
        H.append("<h3>Маткапитал лежит на балансе — есть ли абонемент на октябрь</h3><div class=tw><table><tr><th>Клиент</th><th class=n>Баланс</th><th>Абонементов на октябрь</th></tr>" + "".join(f"<tr><td>{e(n)}</td><td class=n>{rub(b)}</td><td class={'ok' if k else 'bad'}>{k or 'нет — создать со списанием с баланса'}</td></tr>" for n, b, k in hang) + "</table></div>")

# --- 8. возвраты: в МК после возврата баланс = 0, абонемент закрыт
refs = [p for p in P if p["optype"] == "refund" and p["date"] >= "2026-06-01"]
H.append("<h2>8. Возвраты: проверка в МойКлассе</h2><p class=lead>Правило: остаток абонемента сначала закрывается на баланс, затем возврат с баланса — баланс должен стать 0, абонемент — закрыт. Деньги уходят с личного счёта владельца («Возврат KidsUP») — сверка с ним после подключения ZenMoney.</p><div class=tw><table><tr><th>Дата</th><th>Внёс</th><th class=n>Сумма</th><th>Тип в МК</th><th>Клиент</th><th class=n>Баланс сейчас</th><th>Абонемент после возврата</th></tr>")
for p in sorted(refs, key=lambda p: p["date"]):
    u = U.get(p["userId"]) or {}
    act = [s for s in subs_by[p["userId"]] if s.get("statusId") == 2 and (s.get("sellDate") or "") <= p["date"] and (s.get("visitedCount") or 0) < (s.get("visitCount") or 0) and (s.get("payed") or 0) > 0]
    note = ("<span class=warn>активный оплаченный абонемент не закрыт: " + "; ".join(f"{ddmm(s['sellDate'])} {rub(s['payed'])} ({s.get('visitedCount')}/{s.get('visitCount')})" for s in act) + "</span>") if act else "<span class=ok>закрыт</span>"
    bal = u.get("balans") or 0
    H.append(f"<tr><td>{ddmm(p['date'])}</td><td>{e(man(p))}</td><td class=n>{rub(-p['summa'])}</td><td>{TYPES.get(p.get('paymentTypeId'), p.get('paymentTypeId'))}</td><td>{e(u.get('name') or '')}</td><td class='n {'ok' if abs(bal)<1 else 'warn'}'>{rub(bal)}</td><td>{note}</td></tr>")
H.append("</table></div>")

H.append("</div></body></html>")

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "sverka.html").write_text("\n".join(H), encoding="utf-8")
print("cash rows", len(cash_rows), "нет в тетради", len(nocash), sum(r['p']['summa'] for r in nocash))
for r in cash_rows:
    if "НЕТ" in r["status"] or "меньше" in r["status"]: print("  ", pay_line(r["p"]), "|", r["status"][:120])
print("tetrad only", [(t["d"], t["sum"], t["note"]) for t in tetrad_only])
print("cash was card", [(byid[i]['date'], byid[i]['summa'], name(byid[i]['userId']), v) for i, v in cash_was_card.items()])
print("web offset", best_off, "mk", sum(r['mk'] for r in web_rows), "bank", sum(r['bank'] for r in web_rows))
print("kt_free", [(msk(x['created_time']).strftime('%d.%m'), x['amount']) for x in kt_free])
print("web no check", len(nochk), "free sbp", [(s['t'], s['sum']) for s in free_sbp])
for w in nochk: print("   NOCHK", pay_line(w["p"]), w["bank"], w["p"]["createdAt"][11:16])
for r in web_rows:
    if abs(r["diff"])>1: print("   WEB", r["d"], r["mk"], r["bank"], r["diff"])
print("TERM MK>bank", [(r["d"], r["diff"]) for r in term_mk_more]); print("TERM bank>MK", [(r["d"], r["diff"]) for r in term_bank_more])
print("minus", len(minus), sum(u['balans'] for u in minus), "nonpaid", len(nonpaid), "unpaid subs", len(unpaid_subs))
