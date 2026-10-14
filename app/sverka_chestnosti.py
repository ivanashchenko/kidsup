# -*- coding: utf-8 -*-
"""Сверка честности: что в МойКлассе изменили, удалили или провели задним числом.

Решение Бориса 14.10.2026: «ежедневно следить за действиями администраторов —
что ничего не украли и не смахиначили, все оплаты бьются с абонементами и в
МойКлассе работают корректно». Пароль владельца для этого не нужен: три
источника обходятся ключом API, который и так лежит в настройках сервера.

1. ЖУРНАЛ ПРАВОК. Сервер хранит копию CRM и никогда не удаляет строки
   (db.upsert_rows — INSERT … ON CONFLICT DO UPDATE). Триггеры SQLite на UPDATE
   платежей, абонементов, счетов, записей на занятия и баланса клиента пишут
   старый и новый raw в таблицу izmeneniya при каждом синке (лёгкий — раз в
   5 минут). Так видно: сумма, дата, тип и автор платежа; цена, скидка,
   оплачено, статус абонемента; снятые отметки явки и списания за пропуск.
2. УДАЛЕНИЯ. Утром живой API сравнивается с локальной базой в окне 45 дней:
   строка есть у нас и нет в живой выборке → GET /…/{id}; 404 — удалена,
   и мы знаем, что это было, кто автор и сколько. Для удалений старше окна —
   тождество баланса: balans клиента == Σ summa всех его платежей
   (income +, debit −, refund −); расхождение «в минус» разбирается точечно.
3. СНИМКИ /export/raw день к дню — docs/rabota/sverka_snapshots.py; его гоняет
   вечерняя рутина без ключа МойКласса.

Модель денег МойКласса (по данным 14.10): income — деньги на баланс, к абонементу
не привязан никогда; debit — автоматическое списание по счёту (invoiceId всегда,
managerId никогда); refund — возврат, managerId всегда. Поэтому «оплата ↔
абонемент» сверяется цепочкой income → баланс → debit → счёт → абонемент, а не
полем userSubscriptionId у платежа. Онлайн-оплаты (тип 2) приходят без менеджера
по своей природе — это не нарушение.

Запуск: autopilot в 08:45 (после полного синка 07:00 и контроля оплат 08:30);
руками — POST /api/sverka-chestnosti/run?day=&dry=1. Результат:
data/sverka_chestnosti/<день>.json, страница /sverka-chestnosti, флаги в ленте
аномалий (kind «честность»), красные пункты — Борису в инбокс, CRM-правки — Лизе.
Фамилии клиентов остаются на сервере; в репозиторий и в чаты — только сводка.

Самопроверка без базы и без сети: python3 -m app.sverka_chestnosti --selftest
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("kidsup.sverka_chestnosti")

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "sverka_chestnosti"
OKNO_DNEY = 45           # окно живой сверки на удаления
PODTVERZHDENIY_MAX = 60  # сколько кандидатов на удаление подтверждаем GET-ом за один прогон
BALANS_POROG = 500.0     # расхождение тождества баланса, с которого разбираем клиента
BALANS_MAX = 30          # сколько клиентов с расхождением проверяем живым API за прогон
T_ONLINE = 2
TYPES = {1: "нал", 2: "онлайн", 64725: "терминал", 63934: "перевод на счёт",
         72508: "коррекция", 97845: "возврат"}

# Поля, по которым правка считается событием. Всё остальное (stats, remindDate,
# updatedAt и прочий шум синка) не превращается в находку.
POLYA = {
    "payments": ["summa", "date", "paymentTypeId", "managerId", "userId", "optype",
                 "comment", "invoiceId", "userSubscriptionId"],
    "user_subscriptions": ["price", "payed", "originalPrice", "discount", "extraDiscount",
                           "statusId", "beginDate", "endDate", "visitCount", "visitedCount",
                           "comment", "classIds", "freezeFrom", "freezeTo", "sellDate"],
    "invoices": ["price", "payed", "status", "payUntil", "userSubscriptionId"],
    "lesson_records": ["visit", "free", "test", "paid", "skip", "userSubscriptionId", "bill", "billSumma"],
    "users": ["balance"],
}

TRIGGERS_SQL = """
CREATE TABLE IF NOT EXISTS izmeneniya (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    tbl TEXT,
    rid INTEGER,
    user_id INTEGER,
    old_raw TEXT,
    new_raw TEXT,
    razobrano INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_izmeneniya_ts ON izmeneniya(ts);
CREATE TRIGGER IF NOT EXISTS trg_izm_payments AFTER UPDATE ON payments
WHEN old.raw IS NOT new.raw BEGIN
    INSERT INTO izmeneniya(ts, tbl, rid, user_id, old_raw, new_raw)
    VALUES (datetime('now'), 'payments', new.id, new.user_id, old.raw, new.raw);
END;
CREATE TRIGGER IF NOT EXISTS trg_izm_user_subscriptions AFTER UPDATE ON user_subscriptions
WHEN old.raw IS NOT new.raw BEGIN
    INSERT INTO izmeneniya(ts, tbl, rid, user_id, old_raw, new_raw)
    VALUES (datetime('now'), 'user_subscriptions', new.id, new.user_id, old.raw, new.raw);
END;
CREATE TRIGGER IF NOT EXISTS trg_izm_invoices AFTER UPDATE ON invoices
WHEN old.raw IS NOT new.raw BEGIN
    INSERT INTO izmeneniya(ts, tbl, rid, user_id, old_raw, new_raw)
    VALUES (datetime('now'), 'invoices', new.id, new.user_id, old.raw, new.raw);
END;
CREATE TRIGGER IF NOT EXISTS trg_izm_lesson_records AFTER UPDATE ON lesson_records
WHEN old.raw IS NOT new.raw BEGIN
    INSERT INTO izmeneniya(ts, tbl, rid, user_id, old_raw, new_raw)
    VALUES (datetime('now'), 'lesson_records', new.id, new.user_id, old.raw, new.raw);
END;
CREATE TRIGGER IF NOT EXISTS trg_izm_users_balance AFTER UPDATE ON users
WHEN old.balance IS NOT new.balance BEGIN
    INSERT INTO izmeneniya(ts, tbl, rid, user_id, old_raw, new_raw)
    VALUES (datetime('now'), 'users', new.id, new.id,
            json_object('balance', old.balance), json_object('balance', new.balance));
END;
"""


# ── чистые функции (без базы и сети — их проверяет --selftest) ─────────────
def _j(s):
    try:
        return json.loads(s) if isinstance(s, str) else (s or {})
    except ValueError:
        return {}


def _num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def msk(ts: str | None) -> str:
    """UTC из МойКласса или datetime('now') → «ДД.ММ ЧЧ:ММ» по Москве."""
    if not ts:
        return ""
    s = str(ts).replace("T", " ").replace("Z", "")[:19]
    try:
        t = datetime.fromisoformat(s) + timedelta(hours=3)
        return t.strftime("%d.%m %H:%M")
    except ValueError:
        return str(ts)


def raznica(tbl: str, old: dict, new: dict) -> dict:
    """Какие значимые поля изменились: {поле: (было, стало)}."""
    out = {}
    for f in POLYA.get(tbl, []):
        a, b = old.get(f), new.get(f)
        if a != b:
            out[f] = (a, b)
    return out


def ocenit_pravku(tbl: str, old: dict, new: dict) -> dict | None:
    """Правка строки → находка с уровнем: red / yellow / info; None — не событие."""
    d = raznica(tbl, old, new)
    if not d:
        return None
    lvl, chto = "info", []
    if tbl == "payments":
        optype = new.get("optype") or old.get("optype")
        if "summa" in d:
            a, b = _num(d["summa"][0]), _num(d["summa"][1])
            if optype == "income" and b < a:
                lvl = "red"; chto.append(f"сумма прихода уменьшена {a:g} → {b:g}")
            else:
                lvl = "yellow"; chto.append(f"сумма {a:g} → {b:g}")
        if "userId" in d:
            lvl = "red"; chto.append(f"платёж перенесён на другого клиента {d['userId'][0]} → {d['userId'][1]}")
        if "date" in d:
            lvl = "yellow" if lvl != "red" else lvl; chto.append(f"дата {d['date'][0]} → {d['date'][1]}")
        if "paymentTypeId" in d:
            lvl = "yellow" if lvl != "red" else lvl
            chto.append(f"тип {TYPES.get(d['paymentTypeId'][0], d['paymentTypeId'][0])} → {TYPES.get(d['paymentTypeId'][1], d['paymentTypeId'][1])}")
        if "managerId" in d:
            lvl = "yellow" if lvl != "red" else lvl; chto.append(f"менеджер {d['managerId'][0]} → {d['managerId'][1]}")
        if "comment" in d and not chto:
            chto.append("изменён комментарий")
    elif tbl == "user_subscriptions":
        if "payed" in d and _num(d["payed"][1]) < _num(d["payed"][0]):
            lvl = "red"; chto.append(f"оплачено уменьшено {_num(d['payed'][0]):g} → {_num(d['payed'][1]):g}")
        if "price" in d:
            a, b = _num(d["price"][0]), _num(d["price"][1])
            if b < a and _num(new.get("payed")) > 0:
                lvl = "yellow" if lvl != "red" else lvl; chto.append(f"цена оплаченного абонемента снижена {a:g} → {b:g}")
            else:
                chto.append(f"цена {a:g} → {b:g}")
        for f in ("discount", "extraDiscount"):
            if f in d and _num(d[f][1]) > _num(d[f][0]):
                lvl = "yellow" if lvl != "red" else lvl; chto.append(f"скидка {f} {d[f][0]} → {d[f][1]}")
        if "statusId" in d:
            zakryt_rano = (_num(new.get("payed")) > 0 and int(new.get("statusId") or 0) == 4
                           and _num(new.get("visitedCount")) < _num(new.get("visitCount")))
            if zakryt_rano:
                lvl = "yellow" if lvl != "red" else lvl
                chto.append(f"оплаченный абонемент закрыт до конца ({_num(new.get('visitedCount')):g}/{_num(new.get('visitCount')):g})")
            else:
                chto.append(f"статус {d['statusId'][0]} → {d['statusId'][1]}")
        for f in ("beginDate", "endDate", "sellDate", "freezeFrom", "freezeTo", "visitCount", "classIds"):
            if f in d:
                chto.append(f"{f} {d[f][0]} → {d[f][1]}")
        if "comment" in d and not chto:
            chto.append("изменён комментарий")
    elif tbl == "invoices":
        if "payed" in d and _num(d["payed"][1]) < _num(d["payed"][0]):
            lvl = "red"; chto.append(f"оплачено по счёту уменьшено {_num(d['payed'][0]):g} → {_num(d['payed'][1]):g}")
        if "price" in d:
            a, b = _num(d["price"][0]), _num(d["price"][1])
            if b < a and _num(old.get("payed")) > 0:
                lvl = "yellow" if lvl != "red" else lvl; chto.append(f"цена оплаченного счёта снижена {a:g} → {b:g}")
            else:
                chto.append(f"счёт {a:g} → {b:g}")
        for f in ("status", "payUntil", "userSubscriptionId"):
            if f in d:
                chto.append(f"{f} {d[f][0]} → {d[f][1]}")
    elif tbl == "lesson_records":
        if "visit" in d:
            chto.append("отметка явки " + ("снята" if not d["visit"][1] else "поставлена"))
        for f in ("bill", "billSumma"):
            if f in d and _num(d[f][0]) > 0 and _num(d[f][1]) == 0:
                lvl = "yellow"; chto.append(f"снято списание за пропуск {_num(d[f][0]):g} ₽")
        for f in ("free", "test", "paid", "skip", "userSubscriptionId"):
            if f in d:
                chto.append(f"{f} {d[f][0]} → {d[f][1]}")
    elif tbl == "users":
        a, b = _num(d["balance"][0]), _num(d["balance"][1])
        chto.append(f"баланс {a:g} → {b:g}")
    if not chto:
        return None
    return {"level": lvl, "chto": "; ".join(chto), "polya": {k: list(v) for k, v in d.items()}}


def tozhdestvo_balansa(balansy: dict, summy: dict, porog: float = BALANS_POROG) -> list[dict]:
    """balans клиента против Σ его платежей. Минус — у нас есть платёж, которого
    в МойКлассе больше нет (удалён); плюс — платёж есть в МойКлассе, а у нас нет
    (пропуск синка). Возвращает только расхождения больше порога."""
    out = []
    for uid, bal in balansy.items():
        s = summy.get(uid, 0.0)
        delta = round(bal - s, 2)
        if abs(delta) >= porog:
            out.append({"userId": uid, "balans": bal, "summa_platezhey": round(s, 2), "delta": delta,
                        "smysl": "платёж удалён в МойКлассе" if delta < 0 else "пропуск синка или снятое списание"})
    return sorted(out, key=lambda x: x["delta"])


def kandidaty(local_ids, live_ids) -> list:
    """Есть у нас, нет в живой выборке того же окна."""
    return sorted(set(local_ids) - set(live_ids))


# ── база ───────────────────────────────────────────────────────────────────
def ensure(conn=None) -> None:
    """Таблица журнала и триггеры. Повторный вызов безвреден."""
    from . import db
    if conn is not None:
        conn.executescript(TRIGGERS_SQL)
        return
    with db.get_conn() as c:
        c.executescript(TRIGGERS_SQL)


def _manager_names(conn) -> dict:
    out = {}
    for r in conn.execute("SELECT id, name FROM managers"):
        out[r["id"]] = (r["name"] or "").split()[0] if r["name"] else str(r["id"])
    out.setdefault(84116, "Борис"); out.setdefault(154181, "Лиза")
    return out


def _is_404(e: Exception) -> bool:
    resp = getattr(e, "response", None)
    code = getattr(resp, "status_code", None)
    return code == 404 or " 404 " in f" {e} " or "404 Client Error" in str(e)


# ── основной прогон ────────────────────────────────────────────────────────
def run(day: str | None = None, dry: bool = False, days: int = OKNO_DNEY) -> dict:
    from . import db
    day = day or date.today().isoformat()
    d0 = date.fromisoformat(day)
    now_utc = datetime.utcnow()
    R: dict = {"day": day, "sostavleno": datetime.now().strftime("%d.%m.%Y %H:%M"),
               "nakhodki": [], "po_sotrudnikam": {}, "predohranitel": {}, "cifry": {}}

    def nakhodka(level, kind, text, uid=None, who=None, rid=None, tbl=None, extra=None):
        R["nakhodki"].append({"level": level, "kind": kind, "text": text, "userId": uid,
                              "who": who, "rid": rid, "tbl": tbl, "extra": extra or {}})

    with db.get_conn() as conn:
        ensure(conn)
        M = _manager_names(conn)
        users = {r["id"]: dict(r) for r in conn.execute("SELECT id, name, phone, balance, created_at FROM users")}
        name = lambda uid: (users.get(uid) or {}).get("name") or f"#{uid}"
        phone = lambda uid: (users.get(uid) or {}).get("phone") or ""

        # S0. Предохранитель: свежесть данных
        last_sync = db.get_state("last_sync") or ""
        last_light = db.get_state("last_light_sync") or ""
        svezho = True
        try:
            svezho = (datetime.now() - datetime.fromisoformat(last_sync[:19])) < timedelta(hours=26)
        except ValueError:
            svezho = False
        R["predohranitel"] = {"last_sync": last_sync, "last_light_sync": last_light, "polnye_dannye": svezho}

        # 1. Журнал правок с прошлого прогона
        since = db.get_state("sverka_chestnosti_ts") or (now_utc - timedelta(hours=26)).strftime("%Y-%m-%d %H:%M:%S")
        rows = [dict(r) for r in conn.execute(
            "SELECT id, ts, tbl, rid, user_id, old_raw, new_raw FROM izmeneniya WHERE ts > ? ORDER BY ts", (since,))]
        R["cifry"]["pravok_v_zhurnale"] = len(rows)
        novye_platezhi_po_klientu = defaultdict(float)
        for r in rows:
            old, new = _j(r["old_raw"]), _j(r["new_raw"])
            oc = ocenit_pravku(r["tbl"], old, new)
            if not oc:
                continue
            if r["tbl"] == "users":
                # движение баланса само по себе не событие — его объясняют платежи;
                # необъяснённое смотрим ниже по тождеству
                continue
            who = M.get(new.get("managerId") or old.get("managerId"), "—")
            uid = r["user_id"]
            nakhodka(oc["level"], f"правка: {r['tbl']}", f"{name(uid)}: {oc['chto']} (замечено {msk(r['ts'])} МСК, автор строки {who})",
                     uid, who, r["rid"], r["tbl"], oc["polya"])

        # 2. Удаления: живой API против локальной базы в окне
        a = (d0 - timedelta(days=days)).isoformat()
        b = (d0 + timedelta(days=7)).isoformat()
        udaleno = []
        try:
            from .moyklass_client import MoyklassClient
            from . import sync as _sync
            mk = MoyklassClient(_sync.get_api_key())
            try:
                live_pay = mk.fetch_all("/v1/company/payments", ["payments"], params={"date[]": [a, b]})
                live_pay_ids = {p["id"] for p in live_pay}
                loc_pay = {r["id"]: dict(r) for r in conn.execute(
                    "SELECT id, user_id, date, summa, optype, raw FROM payments WHERE date BETWEEN ? AND ?", (a, b))}
                R["cifry"]["platezhi_okno"] = {"local": len(loc_pay), "live": len(live_pay_ids),
                                               "net_u_nas": len(live_pay_ids - set(loc_pay))}
                live_sub = mk.fetch_all("/v1/company/userSubscriptions", ["subscriptions", "userSubscriptions"],
                                        params={"sellDate": [a, b]})
                live_sub_ids = {s["id"] for s in live_sub}
                loc_sub = {}
                for r in conn.execute("SELECT id, user_id, raw FROM user_subscriptions"):
                    raw = _j(r["raw"])
                    if a <= (raw.get("sellDate") or "") <= b:
                        loc_sub[r["id"]] = raw
                R["cifry"]["abonementy_okno"] = {"local": len(loc_sub), "live": len(live_sub_ids)}
                live_rec = mk.fetch_all("/v1/company/lessonRecords", ["lessonRecords", "records"],
                                        params={"date[]": [(d0 - timedelta(days=14)).isoformat(), day]})
                live_rec_ids = {x["id"] for x in live_rec}
                loc_rec = {r["id"]: dict(r) for r in conn.execute(
                    "SELECT lr.id, lr.user_id, lr.raw, l.date AS ldate FROM lesson_records lr JOIN lessons l ON l.id = lr.lesson_id "
                    "WHERE l.date BETWEEN ? AND ?", ((d0 - timedelta(days=14)).isoformat(), day))}
                R["cifry"]["zapisi_okno"] = {"local": len(loc_rec), "live": len(live_rec_ids)}

                plan = ([("payments", i, loc_pay[i]) for i in kandidaty(loc_pay, live_pay_ids)]
                        + [("user_subscriptions", i, loc_sub[i]) for i in kandidaty(loc_sub, live_sub_ids)]
                        + [("lesson_records", i, loc_rec[i]) for i in kandidaty(loc_rec, live_rec_ids)])
                R["cifry"]["kandidatov_na_udalenie"] = len(plan)
                if len(plan) > PODTVERZHDENIY_MAX:
                    nakhodka("info", "предел", f"Кандидатов на удаление {len(plan)}, проверено первых {PODTVERZHDENIY_MAX} — остальное завтра.")
                path = {"payments": "/v1/company/payments/", "user_subscriptions": "/v1/company/userSubscriptions/",
                        "lesson_records": "/v1/company/lessonRecords/"}
                for tbl, rid, row in plan[:PODTVERZHDENIY_MAX]:
                    raw = _j(row.get("raw")) if isinstance(row, dict) and "raw" in row else row
                    try:
                        mk.get(path[tbl] + str(rid))
                        # жива, но вышла из окна: сменили дату или клиента
                        nakhodka("yellow", f"правка: {tbl}", f"{name(raw.get('userId') or row.get('user_id'))}: строка {tbl} #{rid} жива в МойКлассе, но ушла из окна — изменена дата или клиент.",
                                 raw.get("userId") or row.get("user_id"), M.get(raw.get("managerId"), "—"), rid, tbl)
                        continue
                    except Exception as e:  # noqa: BLE001
                        if not _is_404(e):
                            log.warning("сверка честности: %s %s не проверился: %s", tbl, rid, e)
                            continue
                    uid = raw.get("userId") or row.get("user_id")
                    who = M.get(raw.get("managerId"), "—")
                    if tbl == "payments":
                        s = _num(raw.get("summa")); ot = raw.get("optype")
                        lvl = "red" if ot == "income" and s > 0 else "yellow"
                        text = (f"{name(uid)}: удалён платёж {ot} {s:g} ₽ ({TYPES.get(raw.get('paymentTypeId'), raw.get('paymentTypeId'))}, "
                                f"дата {raw.get('date')}, создан {msk(raw.get('createdAt'))}, автор {who}).")
                    elif tbl == "user_subscriptions":
                        payed = _num(raw.get("payed")); lvl = "red" if payed > 0 else "yellow"
                        text = (f"{name(uid)}: удалён абонемент {_num(raw.get('price')):g} ₽ (оплачено {payed:g}, "
                                f"{_num(raw.get('visitedCount')):g}/{_num(raw.get('visitCount')):g}, продан {raw.get('sellDate')}, автор {who}).")
                    else:
                        lvl = "info"
                        text = f"{name(uid)}: удалена запись на занятие {row.get('ldate')} (visit={raw.get('visit')}, списание {raw.get('bill')})."
                    udaleno.append(rid)
                    nakhodka(lvl, f"удаление: {tbl}", text, uid, who, rid, tbl, {"raw": raw})

                # 3. Тождество баланса (только при свежих данных)
                if svezho:
                    sr = db.get_state("sync_range") or ""
                    okno_ot = (_j(sr) or ["2024-07-01"])[0] if sr.startswith("[") else "2024-07-01"
                    summy = {r["user_id"]: _num(r["s"]) for r in conn.execute("SELECT user_id, SUM(summa) s FROM payments GROUP BY user_id")}
                    balansy = {uid: _num(u.get("balance")) for uid, u in users.items()
                               if (u.get("created_at") or "") >= okno_ot and uid in summy}
                    rash = tozhdestvo_balansa(balansy, summy)
                    R["cifry"]["tozhdestvo"] = {"klientov": len(balansy), "rashozhdeniy": len(rash),
                                               "v_minus": sum(1 for x in rash if x["delta"] < 0)}
                    provereno = set(_j(db.get_state("sverka_chestnosti_balans") or "[]") or [])
                    n = 0
                    for x in rash:
                        if x["delta"] >= 0 or x["userId"] in provereno or n >= BALANS_MAX:
                            continue
                        n += 1
                        try:
                            live_u = mk.fetch_all("/v1/company/payments", ["payments"], params={"userId": x["userId"]})
                        except Exception as e:  # noqa: BLE001
                            log.warning("платежи клиента %s не прочитались: %s", x["userId"], e)
                            continue
                        live_ids = {p["id"] for p in live_u}
                        loc_u = [dict(r) | _j(r["raw"]) for r in conn.execute("SELECT id, raw FROM payments WHERE user_id = ?", (x["userId"],))]
                        gone = [p for p in loc_u if p["id"] not in live_ids]
                        provereno.add(x["userId"])
                        for p in gone:
                            s = _num(p.get("summa")); who = M.get(p.get("managerId"), "—")
                            lvl = "red" if p.get("optype") == "income" and s > 0 else "yellow"
                            nakhodka(lvl, "удаление: payments", f"{name(x['userId'])}: удалён платёж {p.get('optype')} {s:g} ₽ от {p.get('date')} (автор {who}); баланс {x['balans']:g} против суммы платежей {x['summa_platezhey']:g}.",
                                     x["userId"], who, p["id"], "payments", {"delta": x["delta"]})
                        if not gone:
                            nakhodka("yellow", "баланс", f"{name(x['userId'])}: баланс {x['balans']:g} меньше суммы платежей на {-x['delta']:g} ₽, а удалённых платежей не найдено — возможно, списание задним числом.", x["userId"])
                    db.set_state("sverka_chestnosti_balans", json.dumps(sorted(provereno)[-2000:]))
                    plus = [x for x in rash if x["delta"] > 0]
                    if plus:
                        nakhodka("info", "синк", f"У {len(plus)} клиентов баланс больше суммы платежей (всего {sum(x['delta'] for x in plus):,.0f} ₽) — платежи есть в МойКлассе, у нас нет: пропуск синка, не честность.".replace(",", " "))
                else:
                    nakhodka("info", "предохранитель", f"Полный синк старше 26 часов ({last_sync or 'нет'}): тождество баланса не считали, данные неполные.")
            finally:
                mk.close()
        except Exception as e:  # noqa: BLE001
            log.exception("сверка честности: живой API недоступен")
            nakhodka("info", "предохранитель", f"Живой API МойКласса недоступен: {str(e)[:120]}. Проверены только правки по журналу.")

        # 4. Итог по сотрудникам и запись
        po = defaultdict(lambda: {"red": 0, "yellow": 0, "info": 0})
        for x in R["nakhodki"]:
            po[x.get("who") or "—"][x["level"]] += 1
        R["po_sotrudnikam"] = dict(po)
        cnt = {k: sum(1 for x in R["nakhodki"] if x["level"] == k) for k in ("red", "yellow", "info")}
        R["cifry"]["nakhodki"] = cnt
        R["summary"] = (f"Сверка честности {day[8:]}.{day[5:7]}: правок в журнале {len(rows)}, удалений подтверждено {len(udaleno)}, "
                        f"красных {cnt['red']}, жёлтых {cnt['yellow']}" + ("" if svezho else "; данные неполные (нет свежего синка)")
                        + ". Подробно: app.kidsup.ru/sverka-chestnosti")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"{day}.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

        try:
            from . import audit
            for x in R["nakhodki"]:
                if x["level"] in ("red", "yellow") and x.get("rid"):
                    audit.add_flag("честность", f"chest:{x['tbl']}:{x['rid']}:{x['kind'][:12]}",
                                   audit.HIGH if x["level"] == "red" else audit.MID,
                                   x["text"][:120], x["text"], user_id=x.get("userId"), day=day)
        except Exception:  # noqa: BLE001
            log.exception("сверка честности: лента не записалась")

        if not dry:
            from . import autopilot
            try:
                otkryto = "\n".join(r[0] or "" for r in conn.execute(
                    "SELECT text FROM plan_inbox WHERE done=0 AND day>=?", ((d0 - timedelta(days=14)).isoformat(),)))
            except Exception:  # noqa: BLE001
                otkryto = ""
            n_b = n_l = 0
            for x in sorted(R["nakhodki"], key=lambda z: {"red": 0, "yellow": 1, "info": 2}[z["level"]]):
                if x["text"][:100] in otkryto:
                    continue
                if x["level"] == "red" and n_b < 6:
                    if autopilot.inbox_add("Сверка честности: " + x["text"], phone(x.get("userId")), "Борис", source=f"сверка честности {day[8:]}.{day[5:7]}"):
                        n_b += 1
                elif x["level"] == "yellow" and n_l < 6 and x["kind"].startswith(("правка", "баланс")):
                    if autopilot.inbox_add("Проверить и объяснить в карточке: " + x["text"], phone(x.get("userId")), "Лиза", source=f"сверка честности {day[8:]}.{day[5:7]}"):
                        n_l += 1
            autopilot.inbox_add(R["summary"], "", "Борис", source="сверка честности")
            R["inbox_posted"] = {"Борис": n_b, "Лиза": n_l}
        db.set_state("sverka_chestnosti_ts", now_utc.strftime("%Y-%m-%d %H:%M:%S"))
    return R


def load(day: str | None = None) -> dict:
    files = sorted(OUT_DIR.glob("*.json")) if OUT_DIR.exists() else []
    if day:
        f = OUT_DIR / f"{day}.json"
        files = [f] if f.exists() else []
    if not files:
        return {"day": day, "nakhodki": [], "summary": "Сверка ещё не запускалась."}
    return json.loads(files[-1].read_text(encoding="utf-8"))


def html(day: str | None = None) -> str:
    import html as _h
    R = load(day)
    days = "".join(f'<a href="/sverka-chestnosti?day={p.stem}">{p.stem[8:]}.{p.stem[5:7]}</a> '
                   for p in (sorted(OUT_DIR.glob("*.json"))[-14:] if OUT_DIR.exists() else []))
    body = [f"<h1>Сверка честности {(R.get('day') or '')[8:]}.{(R.get('day') or '')[5:7]}</h1>",
            f"<p class=lead>{_h.escape(R.get('summary', ''))} · собрано {R.get('sostavleno', '')}</p><p>Дни: {days}</p>"]
    po = R.get("po_sotrudnikam") or {}
    if po:
        body.append("<h2>По сотрудникам</h2><table><tr><th>Кто</th><th>красных</th><th>жёлтых</th><th>прочее</th></tr>"
                    + "".join(f"<tr><td>{_h.escape(str(k))}</td><td>{v['red']}</td><td>{v['yellow']}</td><td>{v['info']}</td></tr>" for k, v in po.items())
                    + "</table>")
    for lvl, title in (("red", "Красное — объяснения нет в данных"), ("yellow", "Жёлтое — нужна причина в карточке"), ("info", "Справочно")):
        items = [x for x in R.get("nakhodki", []) if x["level"] == lvl]
        if items:
            body.append(f"<h2>{title} <small>({len(items)})</small></h2><ul>"
                        + "".join(f'<li class={lvl}>{_h.escape(x["text"])}</li>' for x in items) + "</ul>")
    if not R.get("nakhodki"):
        body.append("<p class=ok>Находок нет.</p>")
    css = ("body{font:15px/1.5 Inter,sans-serif;max-width:1000px;margin:0 auto;padding:20px 16px;color:#221F3B}"
           "h1,h2{color:#312783}li{margin:6px 0}li.red{color:#B3261E;font-weight:600}li.yellow{color:#7a4b00}"
           ".ok{color:#3f7d12}.lead{color:#6A6F87}small{color:#6A6F87;font-weight:400}table{border-collapse:collapse}td,th{padding:4px 10px;border-bottom:1px solid #ddd}")
    return f"<!doctype html><html lang=ru><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Сверка честности</title><style>{css}</style></head><body>{''.join(body)}</body></html>"


# ── самопроверка ───────────────────────────────────────────────────────────
def _selftest() -> int:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT, phone TEXT, balance REAL, client_state_id INTEGER, created_at TEXT, updated_at TEXT, raw TEXT);
    CREATE TABLE payments (id INTEGER PRIMARY KEY, user_id INTEGER, date TEXT, summa REAL, optype TEXT, comment TEXT, raw TEXT);
    CREATE TABLE user_subscriptions (id INTEGER PRIMARY KEY, user_id INTEGER, subscription_id INTEGER, status_id INTEGER, begin_date TEXT, end_date TEXT, visits_total INTEGER, visits_used INTEGER, raw TEXT);
    CREATE TABLE invoices (id INTEGER PRIMARY KEY, user_id INTEGER, price REAL, payed REAL, created_at TEXT, raw TEXT);
    CREATE TABLE lesson_records (id INTEGER PRIMARY KEY, lesson_id INTEGER, user_id INTEGER, visit INTEGER, raw TEXT);
    """)
    ensure(conn)
    # апсерт как в db.upsert_rows: INSERT … ON CONFLICT DO UPDATE → триггер пишет старое и новое
    sql = "INSERT INTO payments(id,user_id,date,summa,optype,comment,raw) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET user_id=excluded.user_id,date=excluded.date,summa=excluded.summa,optype=excluded.optype,comment=excluded.comment,raw=excluded.raw"
    conn.execute(sql, (1, 10, "2026-10-11", 8750, "income", "", json.dumps({"id": 1, "summa": 8750, "optype": "income", "date": "2026-10-11", "paymentTypeId": 64725, "managerId": 232763})))
    conn.execute(sql, (1, 10, "2026-10-11", 8750, "income", "", json.dumps({"id": 1, "summa": 8750, "optype": "income", "date": "2026-10-11", "paymentTypeId": 64725, "managerId": 232763})))
    assert conn.execute("SELECT COUNT(*) FROM izmeneniya").fetchone()[0] == 0, "одинаковый raw не должен попадать в журнал"
    conn.execute(sql, (1, 10, "2026-10-09", 5000, "income", "", json.dumps({"id": 1, "summa": 5000, "optype": "income", "date": "2026-10-09", "paymentTypeId": 1, "managerId": 232763})))
    conn.execute("INSERT INTO users(id,name,balance) VALUES(10,'Тест',0) ON CONFLICT(id) DO UPDATE SET balance=excluded.balance")
    conn.execute("INSERT INTO users(id,name,balance) VALUES(10,'Тест',3225) ON CONFLICT(id) DO UPDATE SET balance=excluded.balance")
    rows = [dict(r) for r in conn.execute("SELECT * FROM izmeneniya ORDER BY id")]
    assert [r["tbl"] for r in rows] == ["payments", "users"], rows
    oc = ocenit_pravku("payments", _j(rows[0]["old_raw"]), _j(rows[0]["new_raw"]))
    assert oc and oc["level"] == "red" and "уменьшена" in oc["chto"] and "дата" in oc["chto"] and "тип" in oc["chto"], oc
    assert ocenit_pravku("payments", {"summa": 1, "stats": 1}, {"summa": 1, "stats": 2}) is None
    oc2 = ocenit_pravku("user_subscriptions", {"price": 5000, "payed": 5000, "visitCount": 4, "visitedCount": 1, "statusId": 2},
                        {"price": 1250, "payed": 1250, "visitCount": 1, "visitedCount": 1, "statusId": 4})
    assert oc2 and oc2["level"] == "red", oc2
    oc3 = ocenit_pravku("lesson_records", {"visit": False, "bill": 975}, {"visit": False, "bill": 0})
    assert oc3 and oc3["level"] == "yellow", oc3
    oc4 = ocenit_pravku("invoices", {"price": 5000, "payed": 5000}, {"price": 1250, "payed": 1250})
    assert oc4 and oc4["level"] == "red", oc4
    t = tozhdestvo_balansa({1: 0.0, 2: 3225.0, 3: 100.0}, {1: 5000.0, 2: 0.0, 3: 0.0})
    assert [x["userId"] for x in t] == [1, 2] and t[0]["delta"] == -5000 and "удалён" in t[0]["smysl"], t
    assert kandidaty({1, 2, 3}, {2, 3, 4}) == [1]
    assert msk("2026-10-12T19:40:13Z") == "12.10 22:40" and msk("2026-10-12 19:40:13") == "12.10 22:40"
    print("selftest ok: журнал правок, оценка правок, тождество баланса, кандидаты, время")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(__doc__)
