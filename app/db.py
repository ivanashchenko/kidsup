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

CREATE TABLE IF NOT EXISTS client_statuses (
    id INTEGER PRIMARY KEY,
    name TEXT,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS join_statuses (
    id INTEGER PRIMARY KEY,
    name TEXT,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS payment_types (
    id INTEGER PRIMARY KEY,
    name TEXT,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY,
    name TEXT,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS lesson_records (
    id INTEGER PRIMARY KEY,
    lesson_id INTEGER,
    user_id INTEGER,
    visit INTEGER,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS mk_udaleno (
    tbl TEXT,
    rid INTEGER,
    ts TEXT,
    istochnik TEXT,
    PRIMARY KEY (tbl, rid)
);

CREATE INDEX IF NOT EXISTS idx_payments_date ON payments(date);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
CREATE INDEX IF NOT EXISTS idx_lessons_date ON lessons(date);
CREATE INDEX IF NOT EXISTS idx_lesson_records_lesson ON lesson_records(lesson_id);
CREATE INDEX IF NOT EXISTS idx_lesson_records_user ON lesson_records(user_id);
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_user ON user_subscriptions(user_id);

-- 13.11.2026: счётчик открытий страниц (см. page_hit ниже). Одна строка на день, адрес и роль.
CREATE TABLE IF NOT EXISTS page_hits (
    day TEXT, path TEXT, rol TEXT, n INTEGER,
    PRIMARY KEY (day, path, rol)
);
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
    """Значение настройки: сначала база, затем переменная окружения.

    08.09.2026: рабочий контейнер откатили — вместе с ним пропала локальная
    data/kidsup.db, где лежали ключи МойКласса и Манго, и почасовой разбор
    звонков встал. Переменные окружения переживают откат, поэтому ключи
    можно положить туда (KIDSUP_MANGO_KEY, KIDSUP_MOYKLASS_API_KEY и т. п.):
    база важнее, env — запасной вариант. На сервере ничего не меняется.
    """
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    except Exception:                     # базы ещё нет (свежий контейнер)
        row = None
    if row and row["value"]:
        return row["value"]
    import os
    env = os.environ.get("KIDSUP_" + key.upper()) or os.environ.get(key.upper())
    return env or default


def admin_auth() -> tuple[str, str] | None:
    """Вход для запросов служебных модулей к app.kidsup.ru (httpx auth=…).

    12.10.2026: Аня уволена, общий пароль admin знали все админы — Борис его
    меняет. Раньше пароль был зашит в код значением по умолчанию.

    12.11.2026, личные входы. В процессе самого сервера (perepiska, nadezhda) —
    внутренний ключ автоматики ("avtomatika", ключ): он живёт только в памяти процесса,
    меняется при каждом перезапуске и пускает только на три адреса (vhod.AVTO_PUTI).
    Так пункты в пульте не перестанут появляться, когда общий admin выключат.
    Вне сервера (контейнер разбора) — как раньше: config.APP_PASSWORD, затем настройка
    admin_pass (или KIDSUP_ADMIN_PASS в окружении). Нет ни того, ни другого — None:
    запрос уйдёт без пароля, вход подставит окружение (служебный ключ)."""
    from . import vhod
    k = vhod.vnutr_klyuch()
    if k:
        return (vhod.AVTO_LOGIN, k)
    pw = config.APP_PASSWORD or get_setting("admin_pass", "")
    return ("admin", pw) if pw else None


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


# --- «удалено в МойКлассе» ----------------------------------------------
#
# 12.11.2026, страница «Проверка МойКласса» (правило 2 Бориса). upsert_rows только
# добавляет и обновляет: занятие, абонемент, счёт, карточка или запись в группу,
# удалённые в МойКлассе, живут в локальной базе вечно — 31 абонемент, удалённый
# с 20.10, до сих пор «активен», а 17 из 27 карточек «ДУБЛЬ» в МойКлассе уже не
# открываются. Физически такие строки НЕ удаляем: те же таблицы читают зарплата,
# явка, прибыль и сверка честности (она находит удаления, сравнивая нашу строку с
# живой выборкой, — стёртую строку она бы уже не увидела). Вместо этого — пометка в
# отдельной таблице. Её учитывают страница проверки и сверка честности; остальные
# страницы видят базу ровно как раньше.
#
# Пометку ставит только синк, получивший список ЦЕЛИКОМ (все страницы, счётчик API
# совпал с числом строк), и только если пропала небольшая доля окна (pometit_udalennye).
# Снимается сама: любой синк, увидевший строку живой, стирает её пометку (_snyat_pometki).

UDALENO_DOLYA = 0.10      # пропало больше 10 % окна — выгрузка подозрительная, ничего не трогаем

_UDALENO_SQL = ("CREATE TABLE IF NOT EXISTS mk_udaleno (tbl TEXT, rid INTEGER, ts TEXT, "
                "istochnik TEXT, PRIMARY KEY (tbl, rid))")


def _snyat_pometki(conn: sqlite3.Connection, tbl: str, ids) -> None:
    """Строка снова пришла из МойКласса живой — пометка «удалено» с неё снимается."""
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return
    conn.execute(_UDALENO_SQL)
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        conn.execute("DELETE FROM mk_udaleno WHERE tbl=? AND rid IN (%s)" % ",".join("?" * len(part)),
                     (tbl, *part))


def udalennye(tbl: str, conn: sqlite3.Connection | None = None) -> set[int]:
    """id строк таблицы, помеченных «удалено в МойКлассе»."""
    def _q(c):
        try:
            return {int(r[0]) for r in c.execute("SELECT rid FROM mk_udaleno WHERE tbl=?", (tbl,))}
        except sqlite3.OperationalError:          # таблицы ещё нет (старая база)
            return set()
    if conn is not None:
        return _q(conn)
    with get_conn() as c:
        return _q(c)


def pometit_udalennye(tbl: str, lokalnye, svezhie, istochnik: str,
                      dolya: float = UDALENO_DOLYA) -> dict:
    """Пометить «удалено в МойКлассе» строки окна, которых нет в свежем полном списке.

    lokalnye — id нашей базы в том же окне, что и выгрузка; svezhie — id из выгрузки.
    Вызывать ТОЛЬКО с полным списком (sync._polnyi_spisok вернул polnyi=True).
    Пропало больше доли dolya строк окна — ничего не помечаем: так выглядит не работа
    админа, а сбой выгрузки (обрезанный ответ, сменившийся фильтр API)."""
    lok = {int(i) for i in lokalnye if i is not None}
    sv = {int(i) for i in svezhie if i is not None}
    propalo = sorted(lok - sv)
    itog = {"tbl": tbl, "okno": len(lok), "svezhih": len(sv), "propalo": len(propalo),
            "pomecheno": 0, "ok": True, "prichina": ""}
    if not propalo:
        return itog
    if len(propalo) > dolya * len(lok):
        import logging
        logging.getLogger("kidsup.db").warning(
            "пометка удалённых %s пропущена (%s): пропало %s из %s строк окна — больше %d %%",
            tbl, istochnik, len(propalo), len(lok), round(dolya * 100))
        itog.update(ok=False, prichina=f"пропало {len(propalo)} из {len(lok)} — больше {round(dolya * 100)} %")
        return itog
    from datetime import datetime as _dt
    ts = _dt.now().isoformat(timespec="seconds")
    with _lock, get_conn() as conn:
        conn.execute(_UDALENO_SQL)
        cur = conn.executemany("INSERT OR IGNORE INTO mk_udaleno (tbl, rid, ts, istochnik) VALUES (?,?,?,?)",
                               [(tbl, i, ts, istochnik) for i in propalo])
        itog["pomecheno"] = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else len(propalo)
    return itog


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
        _snyat_pometki(conn, "users", [r[0] for r in rows])


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
        _snyat_pometki(conn, "joins", [r[0] for r in rows])


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


def payments_missing(date_from: str, date_to: str, items: list[dict]) -> list[dict]:
    """Платежи локальной копии за окно [date_from, date_to], которых нет в
    выгрузке items за то же окно, — кандидаты в удалённые в МойКлассе:
    [{"id", "user_id"}].

    26.10.2026: save_payments только добавляет и обновляет, поэтому платёж,
    удалённый в МойКлассе, оставался в базе навсегда. 25.10 наличную оплату
    одной семьи удалили и внесли заново картой, а контроль оплат продолжал
    считать её наличной без чека — и касса «не сходилась» на 1 600 ₽.
    Удалять кандидата можно только после второй проверки по списку платежей
    ученика (sync._prune_payments): неполная выгрузка не должна стирать базу."""
    keep = {int(p["id"]) for p in items if p.get("id") is not None}
    with get_conn() as conn:
        local = [(int(r[0]), r[1]) for r in conn.execute(
            "SELECT id, user_id FROM payments WHERE substr(date, 1, 10) BETWEEN ? AND ?",
            (date_from[:10], date_to[:10]))]
    return [{"id": i, "user_id": u} for i, u in local if i not in keep]


def delete_payments(ids: list[int]) -> None:
    if not ids:
        return
    with _lock, get_conn() as conn:
        conn.executemany("DELETE FROM payments WHERE id=?", [(int(i),) for i in ids])


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
        _snyat_pometki(conn, "invoices", [r[0] for r in rows])


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
        # 07.09: группы, удалённые в CRM, иначе живут в таблице вечно и
        # показываются в блоке «Места сейчас» (робототехника — четыре вместо двух)
        ids = [r[0] for r in rows if r[0] is not None]
        if len(ids) >= 50:
            conn.execute("DELETE FROM classes WHERE id NOT IN (%s)" % ",".join("?" * len(ids)), ids)


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
        _snyat_pometki(conn, "user_subscriptions", [r[0] for r in rows])


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
    # 17.10.2026: перенос записи в МойКлассе — это удаление записи на старую дату
    # и новая на другую. Синк только дописывал, удалённая запись жила в базе, и
    # /api/day/firsts звал админа встречать первое занятие, перенесённое накануне.
    # Занятие пришло со списком записей — это весь список, чего в нём нет, удалено.
    # Если записи не пришли ни у одного занятия (API их не отдал), ничего не удаляем.
    polnye = ([l for l in items if isinstance(l.get("records"), list)]
              if any(l.get("records") for l in items) else [])
    with _lock, get_conn() as conn:
        upsert_rows(conn, "lessons",
                    ["id", "date", "begin_time", "end_time", "class_id", "filial_id",
                     "status", "topic", "raw"], lesson_rows)
        _snyat_pometki(conn, "lessons", [r[0] for r in lesson_rows])
        upsert_rows(conn, "lesson_records",
                    ["id", "lesson_id", "user_id", "visit", "raw"], record_rows)
        for l in polnye:
            ids = [r.get("id") for r in l["records"] if r.get("id") is not None]
            conn.execute("DELETE FROM lesson_records WHERE lesson_id=?"
                         + (" AND id NOT IN (%s)" % ",".join("?" * len(ids)) if ids else ""),
                         (l.get("id"), *ids))


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
              "lessons", "lesson_records", "client_statuses", "join_statuses",
              "payment_types", "rooms"]
    out = {}
    with get_conn() as conn:
        for t in tables:
            out[t] = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
    return out


# --- счётчик открытий страниц ----------------------------------------------
# 13.11.2026, решение владельца от 12.11: «архивные страницы удалить совсем через 30 дней,
# если заходов нет» — для этого нужно знать, какие страницы открывают. Сервер не ведёт
# журнала запросов, поэтому считаем сами: одна строка на (день, адрес, роль).

_PAGE_HITS_SQL = ("CREATE TABLE IF NOT EXISTS page_hits (day TEXT, path TEXT, rol TEXT, n INTEGER, "
                  "PRIMARY KEY (day, path, rol))")
_page_hits_est = False
_page_hits_chistka = ""      # день, за который старые строки уже стёрты
PAGE_HITS_HRANIT = 90        # дней


def page_hit(day: str, path: str, rol: str) -> bool:
    """+1 к счётчику открытий. Одна короткая транзакция. timeout=0: если база занята
    (синхронизация пишет большую пачку) — запись пропускаем и возвращаем False, а не держим
    страницу: счётчик — справка, а не учёт. Прочие ошибки уходят наверх, их глотает
    вызывающий (main._schetchik_otkrytiy)."""
    global _page_hits_est, _page_hits_chistka
    conn = sqlite3.connect(config.DB_PATH, timeout=0)
    try:
        if not _page_hits_est:
            conn.execute(_PAGE_HITS_SQL)
        if _page_hits_chistka != day:
            # 13.11.2026, проверка шага 2: таблица росла без конца (строка на день, адрес и
            # роль), а показ берёт 30 дней. Раз в сутки (и при запуске) стираем старше 90 дней.
            from datetime import date as _date, timedelta as _td
            conn.execute("DELETE FROM page_hits WHERE day < ?",
                         ((_date.fromisoformat(day) - _td(days=PAGE_HITS_HRANIT)).isoformat(),))
            _page_hits_chistka = day
        conn.execute(
            "INSERT INTO page_hits (day, path, rol, n) VALUES (?,?,?,1) "
            "ON CONFLICT(day, path, rol) DO UPDATE SET n = n + 1", (day, path, rol))
        conn.commit()
        _page_hits_est = True
        return True
    except sqlite3.OperationalError as e:
        if "locked" in str(e) or "busy" in str(e):
            return False
        _page_hits_est = False      # таблицу могли удалить или испортить — в следующий раз создадим
        raise
    finally:
        conn.close()


def page_hits_svod(s_dnya: str) -> tuple[dict, str]:
    """Открытия с дня s_dnya: ({адрес: {роль: сколько}}, первый день, за который есть записи
    вообще — чтобы «ни разу за 30 дней» не читали как правду на второй день работы счётчика)."""
    svod: dict = {}
    with get_conn() as conn:
        # таблицы ещё нет (счётчик ни разу не писал) — пусто; чтение базу на запись не берёт
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='page_hits'").fetchone():
            return svod, ""
        for path, rol, n in conn.execute(
                "SELECT path, rol, SUM(n) FROM page_hits WHERE day >= ? GROUP BY path, rol", (s_dnya,)):
            svod.setdefault(path, {})[rol] = int(n or 0)
        pervyy = conn.execute("SELECT MIN(day) FROM page_hits").fetchone()[0] or ""
    return svod, pervyy
