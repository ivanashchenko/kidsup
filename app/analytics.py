"""Сводка по группам из локальной базы: занятий за 90 дней и средняя посещаемость.

До 12.11.2026 модуль считал ещё и дашборд (выручка, новые ученики, посещаемость по
месяцам, должники). Борис: «скрой не нужные / не актуальные / старые страницы» —
дашборд и страница «Группы» убраны, их расчёты удалены вместе с ними.
group_stats остаётся: её зовёт Клод-диспетчер команды (autopilot.team_chat_tick).
"""

from datetime import date, timedelta

from . import db


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
                 AND l.status = 1 AND l.date >= ? AND l.date <= date('now')
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
