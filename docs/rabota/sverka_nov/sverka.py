# -*- coding: utf-8 -*-
"""Сверка оплат месяца — выгрузка данных.

    python3 sverka_nov/sverka.py fetch <от YYYY-MM-DD> <до YYYY-MM-DD>

Качает свежую копию всего, что нужно странице сверки (page.py), в <папка>/in/
и складывает нормализованный накопительный снимок в <папка>/snap/:

  МойКласс   /export/raw/{payments,user_subscriptions,users,classes,managers}.json
             (логин админа) → snap/mk.json (нужные поля; копия целиком, не за период)
  Т-Банк     /api/tbank/get → /api/v4/bank-accounts и /api/v1/statement по каждому
             счёту за период (основной счёт, куда приходят терминал, СБП и реестры
             интернет-эквайринга; счета маткапитала; счёт клуба Буракова — туда
             приходят деньги ОСФР) → snap/bank.json (операции по operationId)
  терминал   /api/v1/tacq/terminals и /api/v1/tacq/operations/terminal/{key} —
             каждая операция торгового эквайринга (банк отдаёт не больше суток за
             запрос, 1 запрос в секунду) → snap/terminal.json (по дням)
  Комтет     /api/komtet/cheki?since&till → snap/komtet.json (чеки по id; без
             телефонов и почт покупателей)
  чат        /api/gruppy/soobsheniya?chat=<«Администраторы»> — фото складываются
             в foto_dl/, список — в foto.json (по message_id). Страницу тетради с
             фото переносит в tetrad.json человек/агент: машина тетрадь не читает.

Номера счетов в код не зашиты: берутся из ответа банка во время работы и
хранятся только в рабочей папке. Всё — только чтение.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from obshchee import (MSK, admin, days, load_json, now_msk, owner, p_in, p_snap,  # noqa: E402
                      save_json, sverka_dir, to_msk)

CHAT_ADMINY = "4430280960"          # группа «Администраторы» (Telegram через Wazzup)
CHUZHIE = ("буракова",)             # счета/касса клуба Буракова — не наши деньги
MK_TABLES = ("payments", "user_subscriptions", "users", "classes", "managers")


def log(*a):
    print(*a, flush=True)


def _cover(meta: dict, src: str, d1: str, d2: str) -> None:
    """Запомнить, за какие дни источник загружен (списком дней — так проще)."""
    have = set(meta.setdefault("coverage", {}).get(src, []))
    have.update(days(d1, d2))
    meta["coverage"][src] = sorted(have)


# ── МойКласс ─────────────────────────────────────────────────────────────────
def fetch_mk(meta: dict) -> dict:
    raw = {}
    try:
        st = admin("/api/sync/status")
        meta["mk_sync"] = st.get("last_sync") if isinstance(st, dict) else None
    except Exception as e:  # noqa: BLE001
        meta["mk_sync"] = None
        log("  МойКласс: статус синхронизации не прочитан:", str(e)[:120])
    for t in MK_TABLES:
        d = admin(f"/export/raw/{t}.json")
        if not isinstance(d, list):
            raise RuntimeError(f"/export/raw/{t}.json: не список")
        save_json(p_in() / f"{t}.json", d)
        raw[t] = d
        log(f"  МойКласс {t}: {len(d)}")
    P = [{k: p.get(k) for k in ("id", "userId", "summa", "date", "optype", "comment", "managerId",
                                "invoiceId", "userSubscriptionId", "paymentTypeId", "createdAt")}
         for p in raw["payments"]]
    S = [{k: s.get(k) for k in ("id", "userId", "sellDate", "beginDate", "endDate", "price", "payed",
                                "originalPrice", "visitCount", "visitedCount", "statusId", "classIds",
                                "mainClassId", "period", "managerId", "discount", "comment")}
         for s in raw["user_subscriptions"]]
    snap = {"at": now_msk().isoformat(timespec="seconds"), "sync": meta.get("mk_sync"),
            "payments": P, "subs": S,
            "users": {str(u["id"]): u.get("name") or "" for u in raw["users"]},
            "classes": {str(c["id"]): c.get("name") or "" for c in raw["classes"]},
            "managers": {str(m["id"]): m.get("name") or "" for m in raw["managers"]}}
    save_json(p_snap() / "mk.json", snap)
    meta["mk_at"] = snap["at"]
    return snap


# ── Т-Банк: счета и выписка ─────────────────────────────────────────────────
def tget(path: str, **params):
    return owner("GET", "/api/tbank/get", {"path": path, **params})


def _bank_kind(o: dict) -> dict:
    pp = o.get("payPurpose") or o.get("description") or ""
    cr = o.get("typeOfOperation") == "Credit"
    out = {"kind": "other"}
    m = re.search(r"терминалам эквайринга от (\d\d)\.(\d\d)\.(\d{4})", pp)
    if m and cr:
        out = {"kind": "term_credit", "for": f"{m[3]}-{m[2]}-{m[1]}"}
    elif m:
        out = {"kind": "term_fee", "for": f"{m[3]}-{m[2]}-{m[1]}"}
    elif cr and re.search(r"реестру операций от (\d\d)\.(\d\d)\.(\d{4})", pp):
        m = re.search(r"реестру операций от (\d\d)\.(\d\d)\.(\d{4})", pp)
        k = re.search(r"комиссии (\d+) руб\.? ?(\d+)? ?коп", pp)
        fee = (int(k[1]) + int(k[2] or 0) / 100) if k else 0.0
        out = {"kind": "reestr", "for": f"{m[3]}-{m[2]}-{m[1]}", "fee": round(fee, 2)}
    elif "СБП" in pp:
        m = re.search(r"операции СБП (\d+)\.?\s*(?:Терминал\s+(.+?))?(?:\.|$)", pp)
        out = {"kind": "sbp" if cr else "sbp_fee", "sbp_id": m[1] if m else "", "sbp_term": (m[2] or "").strip() if m else ""}
    elif cr and "СФР" in ((o.get("payer") or {}).get("name") or ""):
        out = {"kind": "osfr"}
    elif "собственных средств" in pp:
        out = {"kind": "self"}
    elif cr:
        out = {"kind": "credit_other"}
    return out


def _norm_op(o: dict, acc: dict) -> dict:
    ad = o.get("authorizationDate") or o.get("operationDate")
    payer = o.get("payer") or {}
    n = {"id": o.get("operationId"), "acc": acc["last4"], "acc_name": acc["name"], "chuzhoy": acc["chuzhoy"],
         "dir": o.get("typeOfOperation"), "cat": o.get("category"), "amount": o.get("operationAmount"),
         "op_utc": o.get("operationDate"), "auth_utc": ad, "msk": to_msk(ad).isoformat(timespec="seconds") if ad else None,
         "purpose": o.get("payPurpose") or o.get("description") or "",
         "payer": payer.get("name") or "", "payer_inn": payer.get("inn") or ""}
    n.update(_bank_kind(o))
    return n


def fetch_bank(meta: dict, d1: str, d2: str) -> None:
    data = tget("/api/v4/bank-accounts")
    rows = data if isinstance(data, list) else (data.get("accounts") or data.get("items") or [])
    snap = load_json(p_snap() / "bank.json", {}) or {}
    ops = snap.get("ops") or {}
    accs = []
    for a in rows:
        num, name = a.get("accountNumber") or "", a.get("name") or ""
        acc = {"number": num, "last4": num[-4:], "name": name, "main": bool(a.get("mainFlag")),
               "status": a.get("status"), "chuzhoy": any(c in name.lower() for c in CHUZHIE)}
        accs.append(acc)
        got, cursor, raw = 0, None, []
        try:
            for _ in range(30):
                params = {"accountNumber": num, "from": f"{d1}T00:00:00+03:00", "till": f"{d2}T23:59:59+03:00", "limit": 1000}
                if cursor:
                    params["cursor"] = cursor
                r = tget("/api/v1/statement", **params)
                page = (r.get("operations") if isinstance(r, dict) else r) or []
                raw.extend(page)
                cursor = r.get("nextCursor") if isinstance(r, dict) else None
                if not cursor or not page:
                    break
        except Exception as e:  # noqa: BLE001
            acc["error"] = str(e)[:200]
            log(f"  банк «{name}»: ошибка {str(e)[:120]}")
            continue
        save_json(p_in() / f"bank_{acc['last4']}.json", {"account": name, "from": d1, "till": d2, "operations": raw})
        for o in raw:
            if o.get("operationId"):
                ops[o["operationId"]] = _norm_op(o, acc)
                got += 1
        acc["fetched"] = got
        log(f"  банк «{name}»: операций {got}")
    snap["accounts"] = accs
    snap["ops"] = ops
    snap["at"] = now_msk().isoformat(timespec="seconds")
    save_json(p_snap() / "bank.json", snap)
    _cover(meta, "bank", d1, d2)


# ── терминалы торгового эквайринга: каждая операция ──────────────────────────
def fetch_terminal(meta: dict, d1: str, d2: str) -> None:
    t = tget("/api/v1/tacq/terminals", page=0, size=50)
    terms = [x.get("key") for x in (t.get("terminals") or []) if x.get("key")]
    snap = load_json(p_snap() / "terminal.json", {}) or {}
    snap["terminals"] = terms
    dd = snap.setdefault("days", {})
    raw = {}
    for d in days(d1, d2):
        # сутки МСК в UTC: банк принимает период не длиннее суток
        f = (datetime.fromisoformat(d + "T00:00:00") - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        tl = (datetime.fromisoformat(d + "T23:59:59") - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for k in terms:
            try:
                r = tget(f"/api/v1/tacq/operations/terminal/{k}", **{"from": f, "till": tl, "limit": 1000})
            except Exception as e:  # noqa: BLE001
                log(f"  терминал …{k[-4:]} {d}: ошибка {str(e)[:120]}")
                time.sleep(1.2)
                continue
            ops = r.get("operations") or []
            raw.setdefault(d, {})[k] = r
            dd.setdefault(d, {})[k] = [{
                "rrn": o.get("rrn"), "utc": o.get("transactionDate"),
                "msk": to_msk(o["transactionDate"]).isoformat(timespec="seconds") if o.get("transactionDate") else None,
                "amount": round((o.get("amount") or 0) / 100, 2), "last4": str(o.get("cardNumber") or "")[-4:],
                "type": o.get("type")} for o in ops]
            time.sleep(1.15)
        log(f"  терминал {d}: " + ", ".join(f"…{k[-4:]} {len(dd.get(d, {}).get(k, []))} оп." for k in terms))
    save_json(p_in() / "tacq.json", {"from": d1, "till": d2, "terminals": terms, "days": raw})
    snap["at"] = now_msk().isoformat(timespec="seconds")
    save_json(p_snap() / "terminal.json", snap)
    _cover(meta, "terminal", d1, d2)


# ── Комтет: чеки ─────────────────────────────────────────────────────────────
def _norm_chek(x: dict) -> dict:
    body, rep = x.get("body") or {}, x.get("report") or {}
    pos = " | ".join(str(q.get("name") or "") for q in (body.get("positions") or []))
    shop = (x.get("shop") or {}).get("name") if isinstance(x.get("shop"), dict) else ""
    addr = rep.get("organisation_address") or ""
    m_u = re.search(r"Уч\.?\s?№\s?(\d+)", pos)
    m_a = re.search(r"Аб\.?\s?№\s?(\d+)", pos)
    cashier = body.get("cashier")
    return {"id": x.get("id"), "ext": str(x.get("external_id") or ""), "state": x.get("state"),
            "intent": body.get("intent"), "amount": x.get("amount"),
            "created": to_msk(x["created_time"]).isoformat(timespec="seconds") if x.get("created_time") else None,
            "time": rep.get("time"), "pay": [q.get("type") for q in (body.get("payments") or [])],
            "queue": x.get("print_queue_id"), "pos_id": x.get("pos_id"), "shop": shop or "", "addr": addr,
            "kassir": (cashier.get("name") if isinstance(cashier, dict) else cashier) or rep.get("cashier") or "",
            "chuzhoy": any(c in (shop or "").lower() for c in CHUZHIE) or "люберц" in addr.lower(),
            "pos": pos[:300], "uid": int(m_u[1]) if m_u else None, "aid": int(m_a[1]) if m_a else None}


def fetch_komtet(meta: dict, d1: str, d2: str) -> None:
    r = owner("GET", "/api/komtet/cheki", {"since": d1, "till": d2}, timeout=600)
    if not isinstance(r, dict) or not r.get("ok"):
        raise RuntimeError(f"Комтет: {(r or {}).get('error') if isinstance(r, dict) else str(r)[:200]}")
    items = r.get("items") or []
    save_json(p_in() / "komtet.json", {"since": d1, "till": d2, "count": len(items), "items": items})
    snap = load_json(p_snap() / "komtet.json", {}) or {}
    ch = snap.get("cheki") or {}
    for x in items:
        if x.get("id") is not None:
            ch[str(x["id"])] = _norm_chek(x)
    snap["cheki"] = ch
    snap["at"] = now_msk().isoformat(timespec="seconds")
    save_json(p_snap() / "komtet.json", snap)
    _cover(meta, "komtet", d1, d2)
    log(f"  Комтет: чеков {len(items)} (в снимке {len(ch)})")


# ── чат «Администраторы»: фото ───────────────────────────────────────────────
def fetch_foto(meta: dict, d1: str, d2: str) -> None:
    r = admin(f"/api/gruppy/soobsheniya?chat={CHAT_ADMINY}&since={d1}T00:00&limit=2000")
    msgs = (r.get("messages") if isinstance(r, dict) else r) or []
    save_json(p_in() / "chat.json", msgs)
    base = sverka_dir()
    fdir = base / "foto_dl"
    fdir.mkdir(exist_ok=True)
    foto = load_json(base / "foto.json", []) or []
    have = {f.get("message_id") for f in foto}
    new = 0
    for m in msgs:
        if m.get("kind") != "image" or not m.get("media_url") or m.get("message_id") in have:
            continue
        if (m.get("ts") or "")[:10] > d2:
            continue
        t = to_msk(m["ts"])
        name = t.strftime("%m%d_%H%M")
        f = fdir / f"{name}.jpg"
        i = 2
        while f.exists():
            f = fdir / f"{name}_{i}.jpg"
            i += 1
        p = subprocess.run(["curl", "-sS", "-L", "-m", "120", "-o", str(f), m["media_url"]], capture_output=True, text=True)
        if p.returncode != 0 or not f.exists() or f.stat().st_size < 1000:
            log(f"  фото {m['ts']}: не скачалось {p.stderr.strip()[:100]}")
            f.unlink(missing_ok=True)
            continue
        foto.append({"ts": m["ts"], "chat": CHAT_ADMINY, "author": m.get("author") or "", "text": m.get("text") or "",
                     "file": f"{base.name}/foto_dl/{f.name}", "message_id": m.get("message_id")})
        have.add(m.get("message_id"))
        new += 1
    foto.sort(key=lambda x: x.get("ts") or "")
    save_json(base / "foto.json", foto, indent=1)
    _cover(meta, "chat", d1, d2)
    # какие фото ещё не разобраны в tetrad.json
    td = load_json(base / "tetrad.json", {}) or {}
    used = set((td.get("_skipped_foto") or {}).keys())
    for k, v in td.items():
        if isinstance(v, dict) and isinstance(v.get("foto"), list):
            used.update(v["foto"])
    todo = [f for f in foto if f.get("ts") not in used]
    log(f"  чат: сообщений {len(msgs)}, новых фото {new}; не разобрано в tetrad.json: {len(todo)}")
    for f in todo:
        log(f"    {f['ts']}  {f['file']}  {(f.get('author') or '').strip()} {(f.get('text') or '')[:60]}")


def fetch(d1: str, d2: str) -> int:
    date.fromisoformat(d1), date.fromisoformat(d2)
    if d1 > d2:
        raise SystemExit("от > до")
    base = sverka_dir()
    base.mkdir(parents=True, exist_ok=True)
    meta = load_json(p_snap() / "meta.json", {}) or {}
    run = {"at": now_msk().isoformat(timespec="seconds"), "from": d1, "till": d2, "errors": {}}
    log(f"Сверка: выгрузка {d1} — {d2} в {base}")
    for name, fn, args in (("МойКласс", fetch_mk, (meta,)), ("банк", fetch_bank, (meta, d1, d2)),
                           ("терминал", fetch_terminal, (meta, d1, d2)), ("Комтет", fetch_komtet, (meta, d1, d2)),
                           ("чат", fetch_foto, (meta, d1, d2))):
        log(f"— {name}")
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001 — один источник упал, остальные качаем
            run["errors"][name] = f"{type(e).__name__}: {str(e)[:300]}"
            log(f"  ОШИБКА {name}: {run['errors'][name]}")
    meta.setdefault("runs", []).append(run)
    meta["runs"] = meta["runs"][-60:]
    save_json(p_snap() / "meta.json", meta, indent=1)
    log("Готово" + (" с ошибками: " + ", ".join(run["errors"]) if run["errors"] else "") +
        ". Дальше: перенести новые фото тетради в tetrad.json и запустить page.py.")
    return 1 if run["errors"] else 0


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[1] == "fetch":
        return fetch(argv[2], argv[3])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
