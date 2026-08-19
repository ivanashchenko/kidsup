"""Мост МойКласс → Roistat: выгрузка оплат как заказов.

Каждый платёж-приход из CRM становится заказом в Roistat со статусом "paid".
После первой выгрузки в Roistat (Интеграции → API) нужно один раз
сопоставить статус "paid" → «Оплачен» (продажа).

Запуск:
    python -m app.roistat push --since 2026-08-01 --dry-run
    python -m app.roistat push --since 2026-08-01
"""

import argparse
import json

import httpx

from . import db

BASE = "https://cloud.roistat.com/api/v1"
BATCH = 100


def _params() -> str:
    return f"project={db.get_setting('roistat_project')}&key={db.get_setting('roistat_key')}"


def _visits_by_phone() -> dict[str, str]:
    """Номер визита Roistat по последним 10 цифрам телефона: из заявок с сайта
    и из кликов по кнопкам мессенджеров. Без этого выгрузка оплат приходит
    в Roistat «ниоткуда» и сквозная аналитика по источникам не считается."""
    out: dict[str, str] = {}
    with db.get_conn() as conn:
        for table, phone_col in (("site_leads", "phone"), ("messenger_clicks", "matched_phone")):
            try:
                rows = conn.execute(
                    f"SELECT {phone_col} ph, roistat_visit FROM {table} "
                    f"WHERE roistat_visit IS NOT NULL AND roistat_visit != '' ORDER BY id"
                ).fetchall() if table == "messenger_clicks" else conn.execute(
                    "SELECT phone ph, roistat FROM site_leads "
                    "WHERE roistat IS NOT NULL AND roistat != '' ORDER BY id").fetchall()
            except Exception:
                continue
            for r in rows:
                ph = "".join(ch for ch in str(r["ph"] or "") if ch.isdigit())[-10:]
                visit = r[1]
                if len(ph) == 10 and visit:
                    out[ph] = visit          # берём самый свежий
    return out


def build_orders(since: str) -> list[dict]:
    visits = _visits_by_phone()
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT p.id, p.date, p.optype, ABS(p.summa) amount, p.user_id,
                   u.name, u.phone, u.email
            FROM payments p LEFT JOIN users u ON u.id = p.user_id
            WHERE p.optype IN ('income', 'refund') AND ABS(p.summa) > 0 AND p.date >= ?
            ORDER BY p.date
        """, (since,)).fetchall()
    orders = []
    for r in rows:
        fields = {}
        if r["phone"]:
            fields["phone"] = str(r["phone"])
        if r["email"]:
            fields["email"] = r["email"]
        refund = r["optype"] == "refund"
        order = {
            "id": ("mkr" if refund else "mk") + str(r["id"]),
            "name": ("Возврат — " if refund else "Оплата — ") + (r["name"] or f"клиент {r['user_id']}"),
            "date_create": f"{r['date']}T12:00:00+0300",
            "status": "returned" if refund else "paid",
            "price": -float(r["amount"]) if refund else float(r["amount"]),
            "client_id": str(r["user_id"] or ""),
            "fields": fields,
        }
        ph10 = "".join(ch for ch in str(r["phone"] or "") if ch.isdigit())[-10:]
        if visits.get(ph10):
            order["roistat"] = visits[ph10]   # номер визита = склейка с рекламой
        orders.append(order)
    return orders


def push_lead(lead: dict) -> None:
    """Заявка с сайта уходит в Roistat сразу — чтобы воронка видела не только
    оплаты, но и обращения (иначе конверсия по источникам не считается)."""
    project, key = db.get_setting("roistat_project"), db.get_setting("roistat_key")
    if not project or not key:
        return
    order = {
        "id": "lead" + str(lead.get("lead_id") or lead.get("phone")),
        "name": "Заявка с сайта — " + (lead.get("child") or "без имени"),
        "date_create": __import__("datetime").datetime.now().strftime("%Y-%m-%dT%H:%M:%S+0300"),
        "status": "lead",
        "price": 0,
        "client_id": str(lead.get("phone") or ""),
        "fields": {"phone": "+" + str(lead.get("phone") or ""),
                   "comment": (lead.get("course") or "") + " " + (lead.get("note") or "")},
    }
    if lead.get("roistat"):
        order["roistat"] = lead["roistat"]
    httpx.post(f"{BASE}/project/add-orders?{_params()}", json=[order], timeout=20)


def push(since: str, dry_run: bool = True) -> None:
    orders = build_orders(since)
    total = sum(o["price"] for o in orders)
    print(f"К выгрузке: {len(orders)} оплат на {total:,.0f} ₽ (с {since})")
    if dry_run:
        for o in orders[:5]:
            print("  пример:", json.dumps(o, ensure_ascii=False)[:160])
        print("[dry-run] ничего не отправлено")
        return
    sent = 0
    for i in range(0, len(orders), BATCH):
        chunk = orders[i:i + BATCH]
        r = httpx.post(f"{BASE}/project/add-orders?{_params()}", json=chunk, timeout=60)
        body = r.text[:200]
        ok = r.status_code == 200 and '"error"' not in body
        print(f"  батч {i // BATCH + 1}: HTTP {r.status_code} {body}")
        if not ok:
            raise RuntimeError("Roistat отклонил батч — остановка")
        sent += len(chunk)
    db.set_setting("roistat_last_push", since)
    print(f"Готово: {sent} заказов в Roistat")


def main():
    ap = argparse.ArgumentParser(description="Мост МойКласс → Roistat")
    ap.add_argument("command", choices=["push"])
    ap.add_argument("--since", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.command == "push":
        push(args.since, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
