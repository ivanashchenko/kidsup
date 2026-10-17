# -*- coding: utf-8 -*-
"""Синк занятий (app/db.save_lessons): перенесённая запись не остаётся на старой дате.

17.10.2026: первое занятие перенесли в МойКлассе на 24.10, а в базе сервера
запись на 17.10 осталась — синк только дописывал. Здесь — на временной базе.

  python3 docs/rabota/zanyatiya_sinhro_testy.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/home/user/kidsup")

from app import config  # noqa: E402

config.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
from app import db  # noqa: E402

db.init_db()
oshibok = 0


def proverka(nazvanie, ok):
    global oshibok
    oshibok += not ok
    print(("  OK   " if ok else "  ОШИБКА "), nazvanie)


def zapisi(lesson_id):
    with db.get_conn() as conn:
        return sorted(r[0] for r in conn.execute("SELECT id FROM lesson_records WHERE lesson_id=?", (lesson_id,)))


def urok(lid, date, recs):
    return {"id": lid, "date": date, "beginTime": "16:30", "classId": 7, "records": recs}


db.save_lessons([urok(1, "2026-10-17", [{"id": 11, "userId": 100, "test": True}, {"id": 12, "userId": 101}]),
                 urok(2, "2026-10-24", [{"id": 21, "userId": 102}])])
proverka("записи сохранены", zapisi(1) == [11, 12] and zapisi(2) == [21])

# ребёнка 100 перенесли с 17.10 на 24.10: в МойКлассе старой записи больше нет
db.save_lessons([urok(1, "2026-10-17", [{"id": 12, "userId": 101}]),
                 urok(2, "2026-10-24", [{"id": 21, "userId": 102}, {"id": 22, "userId": 100, "test": True}])])
proverka("перенесённая запись ушла со старой даты", zapisi(1) == [12])
proverka("и появилась на новой", zapisi(2) == [21, 22])

db.save_lessons([urok(1, "2026-10-17", []), urok(2, "2026-10-24", [{"id": 21, "userId": 102}, {"id": 22, "userId": 100}])])
proverka("занятие без записей — все записи сняты", zapisi(1) == [])

# API не отдал записи ни у одного занятия (запасной путь через /lessonRecords) — ничего не трогаем
db.save_lessons([{"id": 2, "date": "2026-10-24", "beginTime": "16:30", "classId": 7, "records": []}])
proverka("пустые списки у всех занятий — не удаляем", zapisi(2) == [21, 22])
db.save_lessons([{"id": 2, "date": "2026-10-24", "beginTime": "16:30", "classId": 7}])
proverka("занятие без поля records — не удаляем", zapisi(2) == [21, 22])

print("\nошибок:", oshibok)
sys.exit(1 if oshibok else 0)
