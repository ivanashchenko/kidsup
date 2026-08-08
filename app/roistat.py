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


def build_orders(since: str) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT p.id, p.date, ABS(p.summa) amount, p.user_id,
                   u.name, u.phone, u.email
            FROM payments p LEFT JOIN users u ON u.id = p.user_id
            WHERE p.optype = 'income' AND ABS(p.summa) > 0 AND p.date >= ?
            ORDER BY p.date
        """, (since,)).fetchall()
    orders = []
    for r in rows:
        fields = {}
        if r["phone"]:
            fields["phone"] = str(r["phone"])
        if r["email"]:
            fields["email"] = r["email"]
        orders.append({
            "id": f"mk{r['id']}",
            "name": f"Оплата — {r['name'] or 'клиент ' + str(r['user_id'])}",
            "date_create": f"{r['date']}T12:00:00+0300",
            "status": "paid",
            "price": float(r["amount"]),
            "client_id": str(r["user_id"] or ""),
            "fields": fields,
        })
    return orders


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
