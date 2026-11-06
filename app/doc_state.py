"""Отметки на страницах владельца (/boris/docs): статус, ответственный, срок, заметка по пункту.

06.11.2026, Борис: «Сделаем единый очень подробный и мощный пульт внедрения изменений
(и фиксации, что уже есть у нас)». Сама страница лежит в документах владельца, а не
в репозитории: в ней разбор платных курсов, а репозиторий открыт. Отметки по её пунктам
хранятся здесь, чтобы были одни и те же с телефона и с компьютера и чтобы их видел Клод.

Хранилище общее для любых документов: doc — имя документа (как slug страницы),
item — пункт внутри него, fields — небольшой словарь строк (status, owner, due, note…).
Каждая правка пишется ещё и в журнал: кто, когда и что поменял.
"""
from __future__ import annotations

import json
import re

from . import db

ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_KEYS = 24
MAX_VAL = 2000
MAX_BATCH = 500


def _ensure(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS doc_state (doc TEXT, item TEXT, data TEXT, "
                 "updated TEXT, author TEXT, PRIMARY KEY (doc, item))")
    conn.execute("CREATE TABLE IF NOT EXISTS doc_state_log (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "doc TEXT, item TEXT, fields TEXT, ts TEXT, author TEXT)")


def _now() -> str:
    from . import autopilot
    return autopilot._now().strftime("%Y-%m-%d %H:%M")


def _id(x, what: str) -> str:
    x = str(x or "").strip()
    if not ID.match(x):
        raise ValueError(f"{what}: латиница в нижнем регистре, цифры, «-» и «_», до 64 знаков")
    return x


def _fields(fields) -> dict:
    """Проверить правку: ключи — короткие латинские, значения — строки (числа и да/нет
    приводим к строке), None или пустая строка — убрать поле."""
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields: нужен непустой словарь")
    if len(fields) > MAX_KEYS:
        raise ValueError(f"fields: не больше {MAX_KEYS} полей за раз")
    out = {}
    for k, v in fields.items():
        if not KEY.match(str(k)):
            raise ValueError(f"поле «{k}»: латиница в нижнем регистре, цифры и «_», до 32 знаков")
        if v is None:
            out[k] = None
        elif isinstance(v, (str, int, float, bool)):
            s = str(v).strip()
            if len(s) > MAX_VAL:
                raise ValueError(f"поле «{k}»: не длиннее {MAX_VAL} знаков")
            out[k] = s or None
        else:
            raise ValueError(f"поле «{k}»: нужна строка")
    return out


def get(doc: str) -> dict:
    doc = _id(doc, "doc")
    with db.get_conn() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT item, data, updated, author FROM doc_state WHERE doc=?",
                            (doc,)).fetchall()
    items = {}
    for item, data, updated, author in rows:
        try:
            d = json.loads(data or "{}")
        except ValueError:
            d = {}
        d["updated"] = updated or ""
        d["author"] = author or ""
        items[item] = d
    return items


def put(doc: str, item: str, fields: dict, author: str = "") -> dict:
    """Записать правку одного пункта. Возвращает его поля целиком после правки."""
    doc, item = _id(doc, "doc"), _id(item, "item")
    f = _fields(fields)
    author = str(author or "").strip()[:40]
    ts = _now()
    with db.get_conn() as conn:
        _ensure(conn)
        row = conn.execute("SELECT data FROM doc_state WHERE doc=? AND item=?", (doc, item)).fetchone()
        try:
            data = json.loads(row[0]) if row and row[0] else {}
        except ValueError:
            data = {}
        for k, v in f.items():
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v
        if len(data) > MAX_KEYS:
            raise ValueError(f"у пункта не больше {MAX_KEYS} полей")
        conn.execute("INSERT OR REPLACE INTO doc_state (doc, item, data, updated, author) VALUES (?,?,?,?,?)",
                     (doc, item, json.dumps(data, ensure_ascii=False), ts, author))
        conn.execute("INSERT INTO doc_state_log (doc, item, fields, ts, author) VALUES (?,?,?,?,?)",
                     (doc, item, json.dumps(f, ensure_ascii=False), ts, author))
    return {**data, "updated": ts, "author": author}


def put_many(doc: str, items: dict, author: str = "") -> dict:
    """Несколько пунктов одним запросом — страница так отдаёт правки, сделанные,
    пока сервер был недоступен. Сначала проверяем всё, потом пишем: кривая
    правка в середине не должна оставить половину записанной."""
    doc = _id(doc, "doc")
    if not isinstance(items, dict) or not items:
        raise ValueError("items: нужен непустой словарь {пункт: поля}")
    if len(items) > MAX_BATCH:
        raise ValueError(f"items: не больше {MAX_BATCH} пунктов за раз")
    checked = {_id(k, "item"): _fields(v) for k, v in items.items()}
    return {k: put(doc, k, v, author) for k, v in checked.items()}


def log(doc: str, limit: int = 200) -> list[dict]:
    doc = _id(doc, "doc")
    limit = max(1, min(int(limit or 200), 2000))
    with db.get_conn() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT item, fields, ts, author FROM doc_state_log WHERE doc=? "
                            "ORDER BY id DESC LIMIT ?", (doc, limit)).fetchall()
    out = []
    for item, fields, ts, author in rows:
        try:
            f = json.loads(fields or "{}")
        except ValueError:
            f = {}
        out.append({"item": item, "fields": f, "ts": ts, "author": author or ""})
    return out
