"""«Шахматка» — недельная сетка занятий по предмету (просьба админа Лены).

Расписание группы восстанавливаем по будущим занятиям (lessons) за две
недели от ближайшего понедельника; если занятий ещё нет — пробуем достать
день/время из названия группы («ЛГ_Вт_16:00»). Дети — активные записи
(статусы «Учится», «Отработка» и заявки набора: новая/подтвердил/записался
на пробное/посетил).
"""
import json
import re
from collections import defaultdict
from datetime import date, timedelta

from . import db

DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
DAY_RX = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}
# записи, которые считаем «ребёнок в группе»
ACTIVE_JOIN = {2, 5}
# заявки набора — показываем отдельным стилем (ещё не учится, но место занято)
PENDING_JOIN = {50509, 83760, 58132, 58131, 70367}


def _class_slots(conn) -> dict[int, set[tuple[int, str]]]:
    """class_id -> {(weekday, 'HH:MM')} по будущим занятиям (2 недели)."""
    today = date.today()
    monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7) \
        if today.weekday() else today
    start = today.isoformat()
    end = (today + timedelta(days=21)).isoformat()
    slots: dict[int, set] = defaultdict(set)
    for cid, d, t in conn.execute(
            "SELECT class_id, date, begin_time FROM lessons "
            "WHERE date >= ? AND date <= ? AND begin_time IS NOT NULL", (start, end)):
        try:
            wd = date.fromisoformat(d).weekday()
        except Exception:
            continue
        slots[cid].add((wd, t[:5]))
    return slots


def _slots_from_name(name: str) -> set[tuple[int, str]]:
    days = [DAY_RX[m.lower()] for m in re.findall(
        r"\b(пн|вт|ср|чт|пт|сб|вс)\b", name, re.I)]
    times = re.findall(r"(\d{1,2}[:._]\d{2})", name)
    t = times[0].replace(".", ":").replace("_", ":") if times else None
    if days and t:
        t = f"{int(t.split(':')[0]):02d}:{t.split(':')[1]}"
        return {(d, t) for d in days}
    return set()


def build(course_id: int | None = None):
    with db.get_conn() as conn:
        courses = {r[0]: r[1] for r in conn.execute(
            "SELECT id, name FROM courses")}
        # активные группы (+ выбранный предмет)
        q = "SELECT id, name, course_id, max_students FROM classes WHERE status='opened'"
        args: list = []
        if course_id:
            q += " AND course_id=?"
            args.append(course_id)
        classes = conn.execute(q, args).fetchall()
        slots = _class_slots(conn)
        # дети по группам
        kids: dict[int, list] = defaultdict(list)
        pending: dict[int, list] = defaultdict(list)
        for uid, cid, sid, uname in conn.execute(
                "SELECT j.user_id, j.class_id, j.status_id, u.name FROM joins j "
                "JOIN users u ON u.id = j.user_id"):
            if sid in ACTIVE_JOIN:
                kids[cid].append((uid, uname or str(uid)))
            elif sid in PENDING_JOIN:
                pending[cid].append((uid, uname or str(uid)))

    grid: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    times_seen = set()
    used_courses = {}
    for cid, name, crs, max_st in classes:
        cls_slots = slots.get(cid) or _slots_from_name(name or "")
        if not cls_slots:
            continue
        used_courses[crs] = courses.get(crs, f"курс {crs}")
        entry = {
            "id": cid, "name": name, "course": courses.get(crs, ""),
            "max": max_st or 0,
            "kids": sorted(kids.get(cid, [])),
            "pending": sorted(pending.get(cid, [])),
            "free": max(0, (max_st or 0) - len(kids.get(cid, [])) - len(pending.get(cid, []))),
        }
        for wd, t in cls_slots:
            grid[t][wd].append(entry)
            times_seen.add(t)

    rows = []
    for t in sorted(times_seen):
        rows.append({"time": t, "cells": [
            sorted(grid[t][wd], key=lambda e: e["name"]) for wd in range(7)]})
    course_list = sorted(used_courses.items(), key=lambda x: x[1])
    return {"rows": rows, "days": DAYS, "courses": course_list}
