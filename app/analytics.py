"""Аналитика по выгруженным данным (SQL по локальной базе).

Терминология платежей МойКласс (поле optype):
  income — поступление денег (оплата),
  debit  — списание за занятия (признанная выручка),
  refund — возврат.
"""

from datetime import date, timedelta

from . import db


def _month_list(months: int) -> list[str]:
    """Последние N месяцев в формате YYYY-MM, по возрастанию."""
    result = []
    d = date.today().replace(day=1)
    for _ in range(months):
        result.append(d.strftime("%Y-%m"))
        d = (d - timedelta(days=1)).replace(day=1)
    return list(reversed(result))


def kpis() -> dict:
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    d30 = (today - timedelta(days=30)).isoformat()
    with db.get_conn() as conn:
        def one(sql, *args):
            row = conn.execute(sql, args).fetchone()
            return row[0] if row and row[0] is not None else 0

        total_students = one("SELECT COUNT(*) FROM users")
        new_month = one(
            "SELECT COUNT(*) FROM users WHERE substr(created_at,1,10) >= ?", month_start)
        active_students = one(
            """SELECT COUNT(DISTINCT lr.user_id) FROM lesson_records lr
               JOIN lessons l ON l.id = lr.lesson_id
               WHERE lr.visit = 1 AND l.date >= ?""", d30)
        income_month = one(
            "SELECT SUM(summa) FROM payments WHERE optype='income' AND substr(date,1,10) >= ?",
            month_start)
        refund_month = one(
            "SELECT SUM(summa) FROM payments WHERE optype='refund' AND substr(date,1,10) >= ?",
            month_start)
        debit_month = one(
            "SELECT SUM(summa) FROM payments WHERE optype='debit' AND substr(date,1,10) >= ?",
            month_start)
        debtors = one("SELECT COUNT(*) FROM users WHERE balance < 0")
        debt_sum = one("SELECT SUM(balance) FROM users WHERE balance < 0")

        att = conn.execute(
            """SELECT SUM(lr.visit) v, COUNT(*) t FROM lesson_records lr
               JOIN lessons l ON l.id = lr.lesson_id
               WHERE l.date >= ? AND l.date <= ?""",
            (d30, today.isoformat())).fetchone()
        attendance = round(100.0 * (att["v"] or 0) / att["t"], 1) if att and att["t"] else None

    return {
        "total_students": total_students,
        "new_month": new_month,
        "active_students": active_students,
        "income_month": income_month,
        "refund_month": refund_month,
        "debit_month": debit_month,
        "debtors": debtors,
        "debt_sum": abs(debt_sum),
        "attendance_30d": attendance,
    }


def revenue_by_month(months: int = 12) -> dict:
    labels = _month_list(months)
    income = dict.fromkeys(labels, 0.0)
    debit = dict.fromkeys(labels, 0.0)
    refund = dict.fromkeys(labels, 0.0)
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT substr(date,1,7) m, optype, SUM(summa) s
               FROM payments WHERE substr(date,1,7) >= ?
               GROUP BY m, optype""", (labels[0],)).fetchall()
    for r in rows:
        target = {"income": income, "debit": debit, "refund": refund}.get(r["optype"])
        if target is not None and r["m"] in target:
            target[r["m"]] = round(r["s"] or 0, 2)
    return {
        "labels": labels,
        "income": [income[m] for m in labels],
        "debit": [debit[m] for m in labels],
        "refund": [refund[m] for m in labels],
    }


def new_students_by_month(months: int = 12) -> dict:
    labels = _month_list(months)
    counts = dict.fromkeys(labels, 0)
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT substr(created_at,1,7) m, COUNT(*) c FROM users
               WHERE substr(created_at,1,7) >= ? GROUP BY m""",
            (labels[0],)).fetchall()
    for r in rows:
        if r["m"] in counts:
            counts[r["m"]] = r["c"]
    return {"labels": labels, "counts": [counts[m] for m in labels]}


def attendance_by_month(months: int = 12) -> dict:
    labels = _month_list(months)
    pct = dict.fromkeys(labels, None)
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT substr(l.date,1,7) m, SUM(lr.visit) v, COUNT(*) t
               FROM lesson_records lr JOIN lessons l ON l.id = lr.lesson_id
               WHERE substr(l.date,1,7) >= ? AND l.date <= date('now')
               GROUP BY m""", (labels[0],)).fetchall()
    for r in rows:
        if r["m"] in pct and r["t"]:
            pct[r["m"]] = round(100.0 * (r["v"] or 0) / r["t"], 1)
    return {"labels": labels, "pct": [pct[m] for m in labels]}


def payments_by_type(days: int = 365) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT optype, COUNT(*) c, SUM(summa) s FROM payments
               WHERE substr(date,1,10) >= ? GROUP BY optype ORDER BY s DESC""",
            (since,)).fetchall()
    return [dict(r) for r in rows]


def top_debtors(limit: int = 20) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT id, name, phone, email, balance FROM users
               WHERE balance < 0 ORDER BY balance ASC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]


def group_stats() -> list[dict]:
    """По каждой группе: занятий за 90 дней, средняя посещаемость."""
    since = (date.today() - timedelta(days=90)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.max_students,
                   COUNT(DISTINCT l.id) lessons_cnt,
                   COUNT(lr.id) records_cnt,
                   SUM(lr.visit) visits_cnt,
                   COUNT(DISTINCT lr.user_id) students_cnt
            FROM classes c
            LEFT JOIN lessons l ON l.class_id = c.id
                 AND l.date >= ? AND l.date <= date('now')
            LEFT JOIN lesson_records lr ON lr.lesson_id = l.id
            GROUP BY c.id ORDER BY lessons_cnt DESC, c.name
            """, (since,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["attendance"] = (round(100.0 * (r["visits_cnt"] or 0) / r["records_cnt"], 1)
                           if r["records_cnt"] else None)
        d["avg_per_lesson"] = (round((r["visits_cnt"] or 0) / r["lessons_cnt"], 1)
                               if r["lessons_cnt"] else None)
        fill = None
        if r["max_students"] and d["avg_per_lesson"] is not None:
            fill = round(100.0 * d["avg_per_lesson"] / r["max_students"], 1)
        d["fill_pct"] = fill
        out.append(d)
    return out


def top_students_by_visits(days: int = 90, limit: int = 15) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT u.id, u.name, SUM(lr.visit) visits, COUNT(lr.id) total
               FROM lesson_records lr
               JOIN lessons l ON l.id = lr.lesson_id
               JOIN users u ON u.id = lr.user_id
               WHERE l.date >= ? AND l.date <= date('now')
               GROUP BY u.id ORDER BY visits DESC LIMIT ?""",
            (since, limit)).fetchall()
    return [dict(r) for r in rows]
