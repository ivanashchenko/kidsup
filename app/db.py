"""Локальная база данных (SQLite) для выгруженных из МойКласс данных.

Каждая сущность хранится в своей таблице: часто используемые поля —
в типизированных колонках, а полный ответ API — в колонке raw (JSON),
чтобы ни одно поле не потерялось.
"""

import json
import sqlite3
import threading
from contextlib import contextmanager

from . import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT,
    email TEXT,
    phone TEXT,
    balance REAL,
    client_state_id INTEGER,
    created_at TEXT,
    updated_at TEXT,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS joins (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    class_id INTEGER,
    status_id INTEGER,
    created_at TEXT,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    date TEXT,
    summa REAL,
    optype TEXT,
    comment TEXT,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    price REAL,
    payed REAL,
    created_at TEXT,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY,
    name TEXT,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY,
    name TEXT,
    course_id INTEGER,
    filial_id INTEGER,
    status TEXT,
    max_students INTEGER,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS filials (
    id INTEGER PRIMARY KEY,
    name TEXT,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS managers (
    id INTEGER PRIMARY KEY,
    name TEXT,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY,
    name TEXT,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS user_subscriptions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    subscription_id INTEGER,
    status_id INTEGER,
    begin_date TEXT,
    end_date TEXT,
    visits_total INTEGER,
    visits_used INTEGER,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY,
    date TEXT,
    begin_time TEXT,
    end_time TEXT,
    class_id INTEGER,
    filial_id INTEGER,
    status INTEGER,
    topic TEXT,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS lesson_records (
    id INTEGER PRIMARY KEY,
    lesson_id INTEGER,
    user_id INTEGER,
    visit INTEGER,
    raw TEXT
);

CREATE INDEX IF NOT EXISTS idx_payments_date ON payments(date);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
CREATE INDEX IF NOT EXISTS idx_lessons_date ON lessons(date);
CREATE INDEX IF NOT EXISTS idx_lesson_records_lesson ON lesson_records(lesson_id);
CREATE INDEX IF NOT EXISTS idx_lesson_records_user ON lesson_records(user_id);
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_user ON user_subscriptions(user_id);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_conn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# --- настройки / служебные значения -------------------------------------

def get_setting(key: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_state(key: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_state(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sync_state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


# --- сохранение сущностей ------------------------------------------------

def _num(value):
    """Число из API может прийти строкой ('1500.00') — приводим аккуратно."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def upsert_rows(conn: sqlite3.Connection, table: str, columns: list[str], rows: list[tuple]):
    if not rows:
        return
    placeholders = ",".join("?" for _ in columns)
    col_list = ",".join(columns)
    updates = ",".join(f"{c}=excluded.{c}" for c in columns if c != "id")
    sql = (
        f"INSERT INTO {table}({col_list}) VALUES({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}"
    )
    conn.executemany(sql, rows)


def save_users(items: list[dict]):
    rows = [
        (
            u.get("id"), u.get("name"), u.get("email"), u.get("phone"),
            _num(u.get("balans", u.get("balance"))), u.get("clientStateId"),
            u.get("createdAt"), u.get("updatedAt"), json.dumps(u, ensure_ascii=False),
        )
        for u in items
    ]
    with _lock, get_conn() as conn:
        upsert_rows(conn, "users",
                    ["id", "name", "email", "phone", "balance", "client_state_id",
                     "created_at", "updated_at", "raw"], rows)


def save_joins(items: list[dict]):
    rows = [
        (
            j.get("id"), j.get("userId"), j.get("classId"), j.get("statusId"),
            j.get("createdAt"), json.dumps(j, ensure_ascii=False),
        )
        for j in items
    ]
    with _lock, get_conn() as conn:
        upsert_rows(conn, "joins",
                    ["id", "user_id", "class_id", "status_id", "created_at", "raw"], rows)


def save_payments(items: list[dict]):
    rows = [
        (
            p.get("id"), p.get("userId"), p.get("date"), _num(p.get("summa")),
            p.get("optype"), p.get("comment"), json.dumps(p, ensure_ascii=False),
        )
        for p in items
    ]
    with _lock, get_conn() as conn:
        upsert_rows(conn, "payments",
                    ["id", "user_id", "date", "summa", "optype", "comment", "raw"], rows)


def save_invoices(items: list[dict]):
    rows = [
        (
            i.get("id"), i.get("userId"), _num(i.get("price")), _num(i.get("payed")),
            i.get("createdAt"), json.dumps(i, ensure_ascii=False),
        )
        for i in items
    ]
    with _lock, get_conn() as conn:
        upsert_rows(conn, "invoices",
                    ["id", "user_id", "price", "payed", "created_at", "raw"], rows)


def save_simple(table: str, items: list[dict]):
    """Справочники: courses, filials, managers, subscriptions."""
    rows = [
        (x.get("id"), x.get("name"), json.dumps(x, ensure_ascii=False))
        for x in items
    ]
    with _lock, get_conn() as conn:
        upsert_rows(conn, table, ["id", "name", "raw"], rows)


def save_classes(items: list[dict]):
    rows = [
        (
            c.get("id"), c.get("name"), c.get("courseId"), c.get("filialId"),
            str(c.get("status", "")), c.get("maxStudents"),
            json.dumps(c, ensure_ascii=False),
        )
        for c in items
    ]
    with _lock, get_conn() as conn:
        upsert_rows(conn, "classes",
                    ["id", "name", "course_id", "filial_id", "status", "max_students", "raw"],
                    rows)


def save_user_subscriptions(items: list[dict]):
    rows = [
        (
            s.get("id"), s.get("userId"), s.get("subscriptionId"), s.get("statusId"),
            s.get("beginDate"), s.get("endDate"),
            s.get("visitCount", s.get("visitsTotal")),
            s.get("usedCount", s.get("visitsUsed")),
            json.dumps(s, ensure_ascii=False),
        )
        for s in items
    ]
    with _lock, get_conn() as conn:
        upsert_rows(conn, "user_subscriptions",
                    ["id", "user_id", "subscription_id", "status_id", "begin_date",
                     "end_date", "visits_total", "visits_used", "raw"], rows)


def save_lessons(items: list[dict]):
    lesson_rows = []
    record_rows = []
    for l in items:
        lesson_rows.append((
            l.get("id"), l.get("date"), l.get("beginTime"), l.get("endTime"),
            l.get("classId"), l.get("filialId"), l.get("status"), l.get("topic"),
            json.dumps(l, ensure_ascii=False),
        ))
        for r in l.get("records") or []:
            record_rows.append((
                r.get("id"), l.get("id"), r.get("userId"),
                1 if r.get("visit") else 0, json.dumps(r, ensure_ascii=False),
            ))
    with _lock, get_conn() as conn:
        upsert_rows(conn, "lessons",
                    ["id", "date", "begin_time", "end_time", "class_id", "filial_id",
                     "status", "topic", "raw"], lesson_rows)
        upsert_rows(conn, "lesson_records",
                    ["id", "lesson_id", "user_id", "visit", "raw"], record_rows)


def save_lesson_records(items: list[dict]):
    rows = [
        (
            r.get("id"), r.get("lessonId"), r.get("userId"),
            1 if r.get("visit") else 0, json.dumps(r, ensure_ascii=False),
        )
        for r in items
    ]
    with _lock, get_conn() as conn:
        upsert_rows(conn, "lesson_records",
                    ["id", "lesson_id", "user_id", "visit", "raw"], rows)


def table_counts() -> dict:
    tables = ["users", "joins", "payments", "invoices", "classes", "courses",
              "filials", "managers", "subscriptions", "user_subscriptions",
              "lessons", "lesson_records"]
    out = {}
    with get_conn() as conn:
        for t in tables:
            out[t] = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
    return out
