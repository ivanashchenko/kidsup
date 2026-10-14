#!/usr/bin/env python3
"""Сравнение двух снимков локальной базы app.kidsup.ru (/export/raw/*.json).

usage: compare_snapshots.py <old_dir> <new_dir> <out.json> [--since YYYY-MM-DD] [--until YYYY-MM-DD]

В каждом каталоге ожидаются payments.json, user_subscriptions.json и по возможности
invoices.json, lesson_records.json, lessons.json, users.json, managers.json, classes.json.
Отсутствующие файлы пропускаются (raw2 содержит только payments/user_subscriptions/joins).

Что считаем (только чтение, ничего не меняем):
  changed.*            — записи с одним id в обоих снимках, у которых изменились поля (до/после)
  backdated_new        — новые записи, у которых createdAt/sellDate/date раньше, чем за сутки до старого снимка
  date_lag             — платежи, у которых date отстаёт от createdAt больше чем на 1 день
  corrections, refunds — платежи типа 72508 (коррекция) и optype=refund за период
  discounts            — абонементы со скидкой >15% или ценой ниже originalPrice без скидки (ручная цена)
  closed_early         — абонементы с payed>0, статус стал 4 (закрыт), а занятия не выхожены
  unlinked_payments    — приходы без userSubscriptionId и без invoiceId
  visits_without_sub   — посещения (visit=1) за 12–14.10 без действующего оплаченного абонемента
  unpaid_with_visits   — абонементы с payed=0 (или < price) и visitedCount>0
  dup_subs, dup_pays   — дубли абонементов / платежей
  balance_mismatch     — изменение баланса клиента между снимками не совпадает с суммой новых/изменённых платежей
Все суммы в рублях, время createdAt в снимках — UTC (МойКласс), в выводе добавлено msk.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

PAY_FIELDS = ["summa", "date", "paymentTypeId", "managerId", "userId",
              "userSubscriptionId", "invoiceId", "optype", "comment"]
SUB_FIELDS = ["price", "payed", "originalPrice", "discount", "extraDiscount", "beginDate",
              "endDate", "statusId", "visitCount", "visitedCount", "classIds", "managerId",
              "comment", "freezeFrom", "freezeTo", "sellDate"]
INV_FIELDS = ["price", "payed", "status", "payUntil", "userSubscriptionId"]
LR_FIELDS = ["visit", "free", "test", "paid", "userSubscriptionId", "billSumma", "skip"]

PAY_TYPES = {1: "нал", 2: "онлайн", 64725: "терминал", 63934: "перевод на счёт (маткапитал)",
             72508: "коррекция", 97845: "возврат", None: "списание"}
SUB_STATUS = {1: "не начат", 2: "активен", 3: "заморожен", 4: "закрыт/использован"}
CORRECTION_TYPE = 72508


def load(d: str, name: str):
    p = os.path.join(d, name + ".json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def by_id(rows):
    return {r["id"]: r for r in rows or [] if r and "id" in r}


def snap_time(d: str) -> datetime:
    """Время снимка — mtime payments.json (файлы качались одним заходом)."""
    p = os.path.join(d, "payments.json")
    return datetime.fromtimestamp(os.path.getmtime(p))


def msk(ts: str | None) -> str | None:
    """createdAt МойКласса в UTC → МСК (+3)."""
    if not ts:
        return None
    t = ts.replace("T", " ")[:19]
    try:
        dt = datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            dt = datetime.strptime(t[:10], "%Y-%m-%d")
        except ValueError:
            return ts
    return (dt + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M") + " МСК"


def inv_status(r: dict) -> str:
    price, payed = r.get("price") or 0, r.get("payed") or 0
    if price <= 0:
        return "нулевой"
    if payed >= price:
        return "оплачен"
    if payed > 0:
        return "частично"
    return "не оплачен"


def lr_flat(r: dict) -> dict:
    bill = r.get("bill") or {}
    return {"visit": r.get("visit"), "free": r.get("free"), "test": r.get("test"),
            "paid": r.get("paid"), "skip": r.get("skip"),
            "userSubscriptionId": bill.get("userSubscriptionId"),
            "billSumma": bill.get("summa")}


def diff(old: dict, new: dict, fields: list[str]) -> dict:
    out = {}
    for f in fields:
        a, b = old.get(f), new.get(f)
        if a != b:
            out[f] = [a, b]
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    old_dir, new_dir, out_path = argv[1], argv[2], argv[3]
    since = until = None
    if "--since" in argv:
        since = argv[argv.index("--since") + 1]
    if "--until" in argv:
        until = argv[argv.index("--until") + 1]

    t_old, t_new = snap_time(old_dir), snap_time(new_dir)
    backdate_before = (t_old - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    period_from = since or (t_old - timedelta(days=1)).strftime("%Y-%m-%d")
    period_to = until or t_new.strftime("%Y-%m-%d")
    today = t_new.strftime("%Y-%m-%d")

    O = {k: load(old_dir, k) for k in ["payments", "user_subscriptions", "invoices", "lesson_records", "users"]}
    N = {k: load(new_dir, k) for k in ["payments", "user_subscriptions", "invoices", "lesson_records",
                                       "lessons", "users", "managers", "classes"]}
    users = by_id(N["users"]) if N["users"] else {}
    managers = by_id(N["managers"]) if N["managers"] else {}
    classes = by_id(N["classes"]) if N["classes"] else {}

    def uname(uid):
        u = users.get(uid)
        return (u or {}).get("name") or (f"user {uid}" if uid else None)

    def mname(mid):
        if not mid:
            return "не указан"
        m = managers.get(mid)
        return (m or {}).get("name") or f"manager {mid}"

    def cname(cid):
        return (classes.get(cid) or {}).get("name") or (str(cid) if cid else None)

    def in_period(d: str | None) -> bool:
        return bool(d) and period_from <= d[:10] <= period_to

    res: dict = {"meta": {
        "old_dir": old_dir, "new_dir": new_dir,
        "old_snapshot": t_old.strftime("%Y-%m-%d %H:%M"), "new_snapshot": t_new.strftime("%Y-%m-%d %H:%M"),
        "backdated_threshold": backdate_before, "period": [period_from, period_to],
        "counts": {}, "note": "createdAt в снимках — UTC МойКласса; поле msk = +3 ч"}}

    # ───────────────────────── payments ─────────────────────────
    po, pn = by_id(O["payments"]), by_id(N["payments"])
    res["meta"]["counts"]["payments"] = {"old": len(po), "new": len(pn),
                                         "added": len(set(pn) - set(po)), "missing_in_new": len(set(po) - set(pn))}
    changed_p, new_p, backdated, date_lag, corrections, refunds, unlinked = [], [], [], [], [], [], []

    def pay_ctx(p: dict) -> dict:
        return {"id": p["id"], "userId": p.get("userId"), "client": uname(p.get("userId")),
                "summa": p.get("summa"), "optype": p.get("optype"), "date": p.get("date"),
                "createdAt": p.get("createdAt"), "msk": msk(p.get("createdAt")),
                "paymentTypeId": p.get("paymentTypeId"), "paymentType": PAY_TYPES.get(p.get("paymentTypeId"), str(p.get("paymentTypeId"))),
                "managerId": p.get("managerId"), "manager": mname(p.get("managerId")),
                "userSubscriptionId": p.get("userSubscriptionId"), "invoiceId": p.get("invoiceId"),
                "comment": p.get("comment")}

    for pid, p in pn.items():
        ctx = pay_ctx(p)
        if pid in po:
            d = diff(po[pid], p, PAY_FIELDS)
            if d:
                changed_p.append({**ctx, "changes": d})
        else:
            new_p.append(ctx)
            ca = (p.get("createdAt") or "").replace("T", " ")[:19]
            if ca and ca < backdate_before:
                backdated.append({"table": "payments", **ctx, "why": f"createdAt {ca} раньше порога {backdate_before}"})
        if p.get("optype") == "income" and p.get("createdAt"):
            ca_d = p["createdAt"][:10]
            d_ = p.get("date") or ca_d
            lag = (date.fromisoformat(ca_d) - date.fromisoformat(d_)).days
            if lag > 1 and (pid not in po or in_period(p.get("createdAt"))):
                date_lag.append({**ctx, "lag_days": lag})
        if (in_period(p.get("date")) or in_period(p.get("createdAt"))):
            if p.get("paymentTypeId") == CORRECTION_TYPE:
                corrections.append(ctx)
            if p.get("optype") == "refund" or p.get("paymentTypeId") == 97845:
                refunds.append(ctx)
            if p.get("optype") == "income" and (p.get("summa") or 0) > 0 \
                    and not p.get("userSubscriptionId") and not p.get("invoiceId"):
                unlinked.append(ctx)

    # дубли платежей: один клиент, одна сумма, один день, один optype — две записи
    dup_pays = []
    grp = defaultdict(list)
    for p in pn.values():
        if in_period(p.get("date")) and p.get("optype") in ("income", "refund") and (p.get("summa") or 0) > 0:
            grp[(p.get("userId"), p.get("summa"), p.get("date"), p.get("optype"))].append(p)
    for k, lst in grp.items():
        if len(lst) > 1:
            dup_pays.append({"userId": k[0], "client": uname(k[0]), "summa": k[1], "date": k[2], "optype": k[3],
                             "payments": [pay_ctx(x) for x in sorted(lst, key=lambda x: x["id"])]})

    res["changed"] = {"payments": changed_p}
    res["new_in_period"] = {"payments": [x for x in new_p if in_period(x["date"]) or in_period(x["createdAt"])]}
    res["new_counts"] = {"payments": len(new_p)}
    res["backdated_new"] = backdated
    res["date_lag"] = sorted(date_lag, key=lambda x: -x["lag_days"])
    res["corrections"] = corrections
    res["refunds"] = refunds
    res["unlinked_payments"] = unlinked
    res["dup_pays"] = dup_pays

    # ───────────────────────── user_subscriptions ─────────────────────────
    so, sn = by_id(O["user_subscriptions"]), by_id(N["user_subscriptions"])
    res["meta"]["counts"]["user_subscriptions"] = {"old": len(so), "new": len(sn),
                                                   "added": len(set(sn) - set(so)), "missing_in_new": len(set(so) - set(sn))}
    res["meta"]["statusId_values"] = dict(Counter(s.get("statusId") for s in sn.values()))
    changed_s, new_s, discounts, closed_early, unpaid_visits = [], [], [], [], []

    def sub_ctx(s: dict) -> dict:
        return {"id": s["id"], "userId": s.get("userId"), "client": uname(s.get("userId")),
                "sellDate": s.get("sellDate"), "beginDate": s.get("beginDate"), "endDate": s.get("endDate"),
                "price": s.get("price"), "payed": s.get("payed"), "originalPrice": s.get("originalPrice"),
                "discount": s.get("discount"), "extraDiscount": s.get("extraDiscount"),
                "statusId": s.get("statusId"), "status": SUB_STATUS.get(s.get("statusId"), str(s.get("statusId"))),
                "visitCount": s.get("visitCount"), "visitedCount": s.get("visitedCount"),
                "classIds": s.get("classIds"), "classes": [cname(c) for c in (s.get("classIds") or [])],
                "managerId": s.get("managerId"), "manager": mname(s.get("managerId")),
                "freezeFrom": s.get("freezeFrom"), "freezeTo": s.get("freezeTo"),
                "comment": s.get("comment"), "stats": s.get("stats")}

    for sid, s in sn.items():
        ctx = sub_ctx(s)
        is_new = sid not in so
        if not is_new:
            d = diff(so[sid], s, SUB_FIELDS)
            if d:
                changed_s.append({**ctx, "changes": d})
                # закрыт раньше срока: статус → 4, занятия не выхожены, деньги были
                if d.get("statusId") and d["statusId"][1] == 4 and (s.get("payed") or 0) > 0 \
                        and (s.get("visitedCount") or 0) < (s.get("visitCount") or 0) \
                        and (s.get("endDate") or "9999") >= period_from:
                    closed_early.append({**ctx, "changes": d})
        else:
            new_s.append(ctx)
            sd = s.get("sellDate") or ""
            if sd and sd < backdate_before[:10]:
                backdated.append({"table": "user_subscriptions", **ctx,
                                  "why": f"sellDate {sd} раньше порога {backdate_before[:10]}"})
        recent = is_new or in_period(s.get("sellDate")) or (not is_new and diff(so[sid], s, SUB_FIELDS))
        if recent:
            op, pr = s.get("originalPrice") or 0, s.get("price") or 0
            disc, extra = s.get("discount") or 0, s.get("extraDiscount") or 0
            expected = round(op * (1 - disc / 100) - extra) if op else None
            if disc > 15:
                discounts.append({**ctx, "kind": f"скидка {disc}% (>15%)"})
            elif op and pr < op and disc == 0 and extra == 0:
                discounts.append({**ctx, "kind": f"ручная цена: {pr} вместо {op} без скидки (−{round((1 - pr / op) * 100)}%)"})
            elif expected is not None and pr < expected - 1:
                discounts.append({**ctx, "kind": f"цена {pr} ниже расчётной {expected} (скидка {disc}% от {op})"})
            if (s.get("visitedCount") or 0) > 0 and (s.get("payed") or 0) < (s.get("price") or 0):
                unpaid_visits.append({**ctx, "debt": (s.get("price") or 0) - (s.get("payed") or 0)})

    # дубли абонементов: один клиент, пересекающиеся даты, общая группа, оба с деньгами или активны
    dup_subs = []
    per_user = defaultdict(list)
    for s in sn.values():
        if s.get("statusId") in (2, 3) or in_period(s.get("sellDate")):
            per_user[s.get("userId")].append(s)
    for uid, lst in per_user.items():
        lst = sorted(lst, key=lambda x: x["id"])
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                a, b = lst[i], lst[j]
                if not (set(a.get("classIds") or []) & set(b.get("classIds") or [])):
                    continue
                ab, ae = a.get("beginDate") or "0000", a.get("endDate") or "9999"
                bb, be = b.get("beginDate") or "0000", b.get("endDate") or "9999"
                if ab <= be and bb <= ae and (in_period(a.get("sellDate")) or in_period(b.get("sellDate"))):
                    dup_subs.append({"userId": uid, "client": uname(uid), "a": sub_ctx(a), "b": sub_ctx(b)})

    res["changed"]["user_subscriptions"] = changed_s
    res["new_in_period"]["user_subscriptions"] = [x for x in new_s if in_period(x["sellDate"])]
    res["new_counts"]["user_subscriptions"] = len(new_s)
    res["discounts"] = discounts
    res["closed_early"] = closed_early
    res["unpaid_with_visits"] = sorted(unpaid_visits, key=lambda x: -x["debt"])
    res["dup_subs"] = dup_subs

    # ───────────────────────── invoices ─────────────────────────
    if O["invoices"] is not None and N["invoices"] is not None:
        io, inn = by_id(O["invoices"]), by_id(N["invoices"])
        res["meta"]["counts"]["invoices"] = {"old": len(io), "new": len(inn),
                                             "added": len(set(inn) - set(io)), "missing_in_new": len(set(io) - set(inn))}
        changed_i, new_i = [], []
        for iid, r in inn.items():
            rr = {**r, "status": inv_status(r)}
            ctx = {"id": iid, "userId": r.get("userId"), "client": uname(r.get("userId")), "price": r.get("price"),
                   "payed": r.get("payed"), "status": rr["status"], "date": r.get("date"),
                   "createdAt": r.get("createdAt"), "payUntil": r.get("payUntil"),
                   "userSubscriptionId": r.get("userSubscriptionId"), "comment": r.get("comment")}
            if iid in io:
                d = diff({**io[iid], "status": inv_status(io[iid])}, rr, INV_FIELDS)
                if d:
                    changed_i.append({**ctx, "changes": d})
            else:
                new_i.append(ctx)
                ca = (r.get("createdAt") or r.get("date") or "")[:10]
                if ca and ca < backdate_before[:10]:
                    backdated.append({"table": "invoices", **ctx, "why": f"createdAt {ca} раньше порога {backdate_before[:10]}"})
        res["changed"]["invoices"] = changed_i
        res["new_in_period"]["invoices"] = new_i
        res["new_counts"]["invoices"] = len(new_i)

    # ───────────────────────── lesson_records ─────────────────────────
    lessons = by_id(N["lessons"]) if N["lessons"] else {}
    if O["lesson_records"] is not None and N["lesson_records"] is not None:
        lo, ln = by_id(O["lesson_records"]), by_id(N["lesson_records"])
        res["meta"]["counts"]["lesson_records"] = {"old": len(lo), "new": len(ln),
                                                   "added": len(set(ln) - set(lo)), "missing_in_new": len(set(lo) - set(ln))}
        changed_lr, suspicious_lr = [], []
        summary = Counter()
        for rid, r in ln.items():
            les = lessons.get(r.get("lessonId")) or {}
            ctx = {"id": rid, "userId": r.get("userId"), "client": uname(r.get("userId")),
                   "lessonId": r.get("lessonId"), "lessonDate": les.get("date"), "time": les.get("beginTime"),
                   "class": cname(les.get("classId")), "createdAt": r.get("createdAt"), "msk": msk(r.get("createdAt")),
                   **lr_flat(r)}
            if rid in lo:
                d = diff(lr_flat(lo[rid]), lr_flat(r), LR_FIELDS)
                if d:
                    for f, (a, b) in d.items():
                        summary[f"{f}: {a} → {b}"] += 1
                    changed_lr.append({**ctx, "changes": d})
                    # снятая явка, снятое списание, перекинутое на другой абонемент списание
                    if (d.get("visit") and d["visit"][0] is True and d["visit"][1] is False) \
                            or (d.get("userSubscriptionId") and d["userSubscriptionId"][0] is not None) \
                            or (d.get("free") and d["free"][1] is True) \
                            or (d.get("paid") and d["paid"][0] is True and d["paid"][1] is False):
                        suspicious_lr.append({**ctx, "changes": d})
            else:
                ca = (r.get("createdAt") or "").replace("T", " ")[:19]
                if ca and ca < backdate_before and (les.get("date") or "") >= period_from:
                    backdated.append({"table": "lesson_records", **ctx, "why": f"createdAt {ca} раньше порога"})
        res["changed"]["lesson_records"] = changed_lr
        res["lesson_records_change_summary"] = dict(summary)
        res["suspicious_lesson_records"] = suspicious_lr

        # посещения за 12–14.10 без действующего оплаченного абонемента
        subs_by_user = defaultdict(list)
        for s in sn.values():
            subs_by_user[s.get("userId")].append(s)
        visits_without = []
        vis_from, vis_to = "2026-10-12", period_to
        for r in ln.values():
            if not r.get("visit"):
                continue
            les = lessons.get(r.get("lessonId")) or {}
            ld = les.get("date") or ""
            if not (vis_from <= ld <= vis_to):
                continue
            bill = r.get("bill") or {}
            bsid = bill.get("userSubscriptionId")
            bsub = sn.get(bsid) if bsid else None
            covering = [s for s in subs_by_user.get(r.get("userId"), [])
                        if (s.get("beginDate") or "0000") <= ld <= (s.get("endDate") or "9999")
                        and (s.get("payed") or 0) > 0
                        and (not s.get("classIds") or les.get("classId") in (s.get("classIds") or []))]
            ok = False
            why = ""
            if bsub is not None:
                if (bsub.get("payed") or 0) >= (bsub.get("price") or 0) and (bsub.get("price") or 0) > 0:
                    ok = True
                else:
                    why = f"списано с абонемента {bsid}, оплачено {bsub.get('payed')} из {bsub.get('price')}"
            elif r.get("free"):
                why = "занятие отмечено бесплатным (free=true)"
            elif r.get("test"):
                why = "пробное (test=true) без счёта" + (", но есть оплаченный абонемент на группу" if covering else "")
            else:
                why = "нет списания (bill=null)" + ("; есть оплаченный абонемент на группу, но списания нет" if covering else ", оплаченного абонемента на эту группу нет")
            if ok:
                continue
            visits_without.append({"recordId": r["id"], "userId": r.get("userId"), "client": uname(r.get("userId")),
                                   "lessonDate": ld, "time": les.get("beginTime"), "class": cname(les.get("classId")),
                                   "classId": les.get("classId"), "test": r.get("test"), "free": r.get("free"),
                                   "billSubscriptionId": bsid, "why": why,
                                   "covering_subs": [{"id": s["id"], "payed": s.get("payed"), "price": s.get("price"),
                                                      "status": SUB_STATUS.get(s.get("statusId")), "begin": s.get("beginDate"),
                                                      "end": s.get("endDate"), "manager": mname(s.get("managerId"))}
                                                     for s in covering]})
        res["visits_without_sub"] = sorted(visits_without, key=lambda x: (x["lessonDate"], x["time"] or ""))

    # ───────────────────────── баланс vs платежи ─────────────────────────
    if O["users"] is not None and N["users"] is not None:
        uo = by_id(O["users"])
        delta_pay = defaultdict(float)
        detail = defaultdict(list)

        def signed(p):
            # в МойКлассе summa уже со знаком: income > 0, debit/refund < 0 —
            # баланс клиента = сумма summa по всем его платежам
            return p.get("summa") or 0

        for pid, p in pn.items():
            old = po.get(pid)
            if old is not None and not diff(old, p, PAY_FIELDS):
                continue                      # платёж не менялся — баланс он не двигал
            v = signed(p) - (signed(old) if old else 0)
            delta_pay[p.get("userId")] += v
            detail[p.get("userId")].append({"id": pid, "optype": p.get("optype"), "summa": p.get("summa"),
                                            "date": p.get("date"), "new": old is None})
        mism = []
        for uid, u in users.items():
            bo = (uo.get(uid) or {}).get("balans")
            bn = u.get("balans")
            if bo is None or bn is None:
                continue
            db_ = round((bn or 0) - (bo or 0), 2)
            dp = round(delta_pay.get(uid, 0.0), 2)
            if abs(db_ - dp) > 1:
                mism.append({"userId": uid, "client": u.get("name"), "balance_old": bo, "balance_new": bn,
                             "delta_balance": db_, "delta_payments": dp, "gap": round(db_ - dp, 2),
                             "payments": detail.get(uid, [])})
        res["balance_mismatch"] = sorted(mism, key=lambda x: -abs(x["gap"]))

    res["totals"] = {k: (len(v) if isinstance(v, list) else {kk: len(vv) for kk, vv in v.items()} if isinstance(v, dict) else v)
                     for k, v in res.items() if k not in ("meta", "totals", "lesson_records_change_summary", "new_counts")}
    res["totals"]["new_counts"] = res["new_counts"]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    print(json.dumps({"meta": res["meta"], "totals": res["totals"],
                      "lesson_records_change_summary": res.get("lesson_records_change_summary")},
                     ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
