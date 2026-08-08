"""Вечерняя сводка дня: звонки админов (Mango) + воронка статусов (МойКласс).

Что показывает:
  1. По каждому админу: попыток / дозвонов / уникальных номеров / минут.
  2. Воронка обзвона по статусам клиентов (очередь → недозвон → записался).
  3. Недозвоны за день, сопоставленные с клиентами CRM, — готовый список
     для WhatsApp-догона (app.wazzup, шаблон nedozvon).

Запуск:
    python -m app.daily            — сводка за сегодня
    python -m app.daily --date 2026-08-10
"""

import argparse
from datetime import date

from . import db, mango


def _norm(phone) -> str:
    p = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if p.startswith("8") and len(p) == 11:
        p = "7" + p[1:]
    return p


def funnel() -> list[tuple[str, int]]:
    """Число клиентов в каждом статусе (только статусы воронки обзвона)."""
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT cs.name, COUNT(u.id) c
            FROM users u JOIN client_statuses cs ON cs.id = u.client_state_id
            GROUP BY cs.name ORDER BY cs.name
        """).fetchall()
    return [(r["name"], r["c"]) for r in rows]


def match_clients(phones: list[str]) -> dict[str, str]:
    """телефон → имя клиента CRM (для листа догона)."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT name, phone FROM users WHERE phone IS NOT NULL").fetchall()
    idx = {_norm(r["phone"]): r["name"] for r in rows}
    return {p: idx.get(_norm(p), "— нет в CRM") for p in phones}


def main():
    ap = argparse.ArgumentParser(description="Вечерняя сводка KidsUP")
    ap.add_argument("--date", default=None)
    args = ap.parse_args()
    day = args.date or date.today().isoformat()

    print(f"===== Сводка за {day} =====\n")

    print("— Звонки по администраторам (Mango):")
    day_calls = None
    try:
        day_calls = mango._day_calls(args.date)
        rows = mango.report(args.date, rows=day_calls)
        if not rows:
            print("  звонков не было")
        for s in rows:
            print(f"  {s['admin'][:28]:28s} попыток {s['attempts']:3d}  дозвонов {s['answered']:3d}  "
                  f"уник. {s['unique']:3d}  разговоров {s['talk_min']:.0f} мин")
    except Exception as e:  # АТС может не ответить — сводка не должна падать
        print("  Mango недоступна:", e)

    print("\n— Воронка по статусам клиентов (CRM):")
    for name, c in funnel():
        print(f"  {name[:40]:40s} {c:5d}")

    print("\n— Недозвоны за день (для WhatsApp-догона):")
    try:
        if day_calls is None:
            raise RuntimeError("нет данных Mango")
        miss = mango.missed(args.date, rows=day_calls)
        if not miss:
            print("  нет")
        names = match_clients([m["phone"] for m in miss])
        for m in miss:
            print(f"  {m['phone']}  попыток {m['attempts']}  {names.get(m['phone'], '')}")
        if miss:
            print(f"\n  Догон: python -m app.wazzup send --phone <номер> --template nedozvon --send")
    except Exception as e:
        print("  Mango недоступна:", e)


if __name__ == "__main__":
    main()
