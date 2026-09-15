"""Карточка речи: форма, в которой педагог отмечает прогресс каждого ребёнка.

В методичке (раздел 10) карточка была описана — шесть поведенческих пунктов
и пять точек замера за год, — но самой формы не существовало, и заполнять её
было негде. 15.09 владелец спросил прямо: «с формой оценки его прогресса?» —
описание формой не является.

Шесть пунктов намеренно поведенческие: не «знает лексику», а то, что видно
глазом. Каждый — «да / почти / пока нет». Два «пока нет» подряд на двух
точках — сигнал смотреть посещаемость и домашнее аудио, а не переводить
ребёнка в другую группу.

Видео — отдельная отметка: родителю нужен не отчёт, а ребёнок, говорящий
по-английски. Дата последнего видео хранится тут же, чтобы было видно, по
кому съёмка просрочена.
"""
from __future__ import annotations

import json
from datetime import date

from . import db

# Точки замера за год (раздел 10 методички)
TOCHKI = [
    ("start", "Первое занятие", "стартовая: уровень и группа"),
    ("t1", "Конец октября", "точка 1"),
    ("t2", "Конец декабря", "точка 2 — плюс сценка на празднике"),
    ("t3", "Конец марта", "точка 3 — плюс чтение знакомого текста"),
    ("t4", "Апрель", "точка 4 — внутренний экзамен в формате Cambridge"),
]

# Шесть пунктов карточки речи. Меняются только вместе с методичкой.
PUNKTY = [
    ("komanda", "Выполняет новую команду с первого раза, без показа"),
    ("fraza", "Отвечает на вопрос о себе целой фразой, не одним словом"),
    ("sam", "Сам задаёт вопрос или обращается к другому ребёнку по-английски"),
    ("repliki", "Говорит вслух не меньше 12 раз за занятие"),
    ("potok", "Узнаёт знакомые слова в быстрой речи, не только в медленной"),
    ("chtenie", "Читает знакомый текст хором, ведя пальцем по строке (с января)"),
]
OTVETY = ("да", "почти", "пока нет")


def _ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS speech_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, tochka TEXT,
        marks TEXT, video_date TEXT, note TEXT, ts TEXT, author TEXT)""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_speech_cards_uniq "
                 "ON speech_cards (user_id, tochka)")


def _deti(conn) -> list[dict]:
    """Кто учится в группах английского сезона. Берём только «Учится»:
    записанного на пробное оценивать нечего, он ещё не занимался."""
    rows = conn.execute(
        "SELECT j.user_id, c.name AS cls, u.name AS kid, u.phone "
        "FROM joins j JOIN classes c ON c.id = j.class_id "
        "LEFT JOIN users u ON u.id = j.user_id "
        "WHERE j.status_id = 2 AND c.name LIKE '2627_АЯ%' "
        "AND (c.status IS NULL OR c.status = 'opened')").fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["user_id"], {
            "user_id": r["user_id"], "имя": r["kid"] or str(r["user_id"]),
            "телефон": r["phone"] or "",
            "группа": (r["cls"] or "").replace("2627_АЯ_", "").replace("2627_", "")})
    return sorted(out.values(), key=lambda x: (x["группа"], x["имя"] or ""))


def rows() -> dict:
    today = date.today().isoformat()
    with db.get_conn() as conn:
        _ensure(conn)
        deti = _deti(conn)
        saved = {}
        for r in conn.execute("SELECT user_id, tochka, marks, video_date, note, ts FROM speech_cards"):
            try:
                marks = json.loads(r["marks"] or "{}")
            except ValueError:
                marks = {}
            saved[(r["user_id"], r["tochka"])] = {
                "marks": marks, "video_date": r["video_date"] or "",
                "note": r["note"] or "", "ts": r["ts"] or ""}
    for d in deti:
        d["точки"] = {k: saved.get((d["user_id"], k), {}) for k, _, _ in TOCHKI}
        vids = [v.get("video_date") for v in d["точки"].values() if v.get("video_date")]
        d["последнее_видео"] = max(vids) if vids else ""
        d["заполнено"] = sum(1 for v in d["точки"].values() if v.get("marks"))
    return {"дети": deti, "точки": TOCHKI, "пункты": PUNKTY, "ответы": OTVETY,
            "сегодня": today,
            "всего": len(deti),
            "без_видео": sum(1 for d in deti if not d["последнее_видео"])}


def save(user_id: int, tochka: str, marks: dict, video_date: str = "",
         note: str = "", author: str = "") -> dict:
    if tochka not in {k for k, _, _ in TOCHKI}:
        raise ValueError("неизвестная точка замера")
    if int(user_id) <= 0:
        raise ValueError("нужен user_id ребёнка")
    keys = {k for k, _ in PUNKTY}
    marks = {k: v for k, v in (marks or {}).items() if k in keys and v in OTVETY}
    with db.get_conn() as conn:
        _ensure(conn)
        conn.execute(
            "INSERT INTO speech_cards (user_id, tochka, marks, video_date, note, ts, author) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id, tochka) DO UPDATE SET "
            "marks=excluded.marks, video_date=excluded.video_date, note=excluded.note, "
            "ts=excluded.ts, author=excluded.author",
            (int(user_id), tochka, json.dumps(marks, ensure_ascii=False),
             video_date[:10], note[:500], date.today().isoformat(), author[:40]))
    return {"ok": True, "отмечено": len(marks)}
