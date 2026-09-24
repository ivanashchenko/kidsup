"""Карточка речи: форма, в которой педагог отмечает прогресс каждого ребёнка.

В методичке (раздел 10) карточка была описана — шесть поведенческих пунктов
и пять точек замера за год, — но самой формы не существовало, и заполнять её
было негде. 15.09 владелец спросил прямо: «с формой оценки его прогресса?» —
описание формой не является.

Шесть пунктов намеренно поведенческие: не «знает лексику», а то, что видно
глазом. Каждый — «да / почти / пока нет». Два «пока нет» подряд на двух
точках — сигнал смотреть посещаемость и домашнее аудио, а не переводить
ребёнка в другую группу.

24.09 владелец спросил, «точно ли всё продумано и список актуальный». Не был:
1. Список брал только статус «Учится» — 33 ребёнка из ~51, которые реально
   ходят; группа вт-чт 18:00 не попадала целиком. Теперь берём всех, кто
   сидит в группе (как страница мест), и показываем статус записи: ребёнок
   на пробном или «был на пробном» без оформления — это видно педагогу и
   админу.
2. Видео было привязано к точкам замера, а методичка с 24.09 требует видео
   раз в месяц. Теперь отдельный журнал видео: последняя дата, просрочка
   больше 31 дня, сколько снято в этом месяце.
3. Отчёт родителю из трёх строк (умеет / работаем / дома) хранить было негде.
   Теперь он пишется на каждой точке, с датой отправки и кнопкой «скопировать».
4. Продвинутые группы (пн-ср 19:00, вт-чт 16:00, вт-чт 19:00) оценивались по
   пунктам для начинающих — у них свои пункты по умениям Movers. У малышей
   3–5 лет нет пункта про чтение.
5. Заметка педагога всегда писалась в точку «октябрь» — теперь она общая
   на ребёнка.
"""
from __future__ import annotations

import json
import re
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

# Пункты по дорожкам. Меняются только вместе с методичкой.
PUNKTY = [
    ("komanda", "Выполняет новую команду с первого раза, без показа"),
    ("fraza", "Отвечает на вопрос о себе целой фразой, не одним словом"),
    ("sam", "Сам задаёт вопрос или обращается к другому ребёнку по-английски"),
    ("repliki", "Говорит вслух не меньше 12 раз за занятие"),
    ("potok", "Узнаёт знакомые слова в быстрой речи, не только в медленной"),
    ("chtenie", "Читает знакомый текст хором, ведя пальцем по строке (с января)"),
]
PUNKTY_PRO = [
    ("warmup", "Задаёт warm-up вопросы и отвечает на них без подсказки"),
    ("o_sebe", "Рассказывает о себе и своём дне 5–6 фразами"),
    ("kartinka", "Описывает картинку и называет отличия между двумя фразой"),
    ("tekst", "Понимает короткий текст по теме и отвечает на вопросы к нему"),
    ("pismo", "Пишет 3–6 предложений о себе (сначала по образцу, потом сам)"),
    ("istoriya", "Рассказывает историю по 4 картинкам (с марта — в прошедшем времени)"),
]
DOROZHKI = {"base": PUNKTY, "malyshi": PUNKTY[:5], "pro": PUNKTY_PRO}
OTVETY = ("да", "почти", "пока нет")

# Кто сидит в группе — те же статусы записи, что на странице мест
LIVE = {2: "учится", 58132: "записан на пробное", 83760: "подтвердил пробное",
        58131: "был на пробном — не оформлен"}

# Продвинутые группы и малыши — по методичке (раздел 7, таблица групп)
PRO = ("Гр4)", "Гр5)", "Гр8)")
MALYSHI = ("Гр2)",)


# Английский в мини-саду и нулевом классе ведёт Маша, пн и ср (методичка,
# раздел 7): группы центра те же, отдельных групп английского в CRM нет.
SAD = {"Мини-сад": "Мини-сад · английский пн, ср 12:00",
       "Нулевой класс": "Нулевой класс · английский пн, ср 13:00"}


def _pedagog(cls: str) -> str:
    if cls.startswith("Мини-сад") or cls.startswith("Нулевой класс"):
        return "Маша"
    return "Маша" if "пн-ср" in cls else ("Илья" if "вт-чт" in cls else "")


def _dorozhka(cls: str) -> str:
    if any(x in cls for x in PRO):
        return "pro"
    if any(x in cls for x in MALYSHI) or cls.startswith("Мини-сад"):
        return "malyshi"
    return "base"


def tekushchaya_tochka(d: date | None = None) -> str:
    """Точка, отчёт по которой пишем сейчас: сентябрь–октябрь — октябрь и т.д."""
    m = (d or date.today()).month
    if m in (9, 10):
        return "t1"
    if m in (11, 12):
        return "t2"
    if m in (1, 2, 3):
        return "t3"
    return "t4"


def _ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS speech_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, tochka TEXT,
        marks TEXT, video_date TEXT, note TEXT, ts TEXT, author TEXT)""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_speech_cards_uniq "
                 "ON speech_cards (user_id, tochka)")
    for ddl in ("ALTER TABLE speech_cards ADD COLUMN report TEXT",
                "ALTER TABLE speech_cards ADD COLUMN report_date TEXT"):
        try:
            conn.execute(ddl)
        except Exception:
            pass
    conn.execute("""CREATE TABLE IF NOT EXISTS speech_videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT,
        note TEXT, ts TEXT, author TEXT)""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_speech_videos_uniq "
                 "ON speech_videos (user_id, date)")
    conn.execute("""CREATE TABLE IF NOT EXISTS speech_notes (
        user_id INTEGER PRIMARY KEY, note TEXT, ts TEXT, author TEXT)""")


def _deti(conn) -> list[dict]:
    """Все, кто сидит в группах английского сезона: учится, на пробном, был
    на пробном. Педагогу нужен весь класс, а не только оформленные."""
    q = ",".join("?" * len(LIVE))
    rows = conn.execute(
        "SELECT j.user_id, j.status_id, c.name AS cls, u.name AS kid, u.phone "
        "FROM joins j JOIN classes c ON c.id = j.class_id "
        "LEFT JOIN users u ON u.id = j.user_id "
        f"WHERE j.status_id IN ({q}) AND (c.name LIKE '2627_АЯ%' "
        "OR c.name LIKE '2627_Мини-сад%' OR c.name LIKE '2627_Нулевой класс%') "
        "AND c.name NOT LIKE '%аявк%' "
        "AND (c.status IS NULL OR c.status = 'opened')", tuple(LIVE)).fetchall()
    out = {}
    for r in rows:
        cls = (r["cls"] or "").replace("2627_АЯ_", "").replace("2627_", "")
        for k, v in SAD.items():
            if cls.startswith(k):
                cls = v
        key = (r["user_id"], cls)
        out[key] = {
            "user_id": r["user_id"], "имя": r["kid"] or str(r["user_id"]),
            "телефон": r["phone"] or "", "группа": cls,
            "педагог": _pedagog(cls), "дорожка": _dorozhka(cls),
            "статус": LIVE.get(r["status_id"], "")}
    return sorted(out.values(), key=lambda x: (x["педагог"], x["группа"], x["имя"] or ""))


def rows() -> dict:
    today = date.today()
    month = today.isoformat()[:7]
    tek = tekushchaya_tochka(today)
    with db.get_conn() as conn:
        _ensure(conn)
        deti = _deti(conn)
        saved = {}
        for r in conn.execute("SELECT user_id, tochka, marks, video_date, note, ts, "
                              "report, report_date FROM speech_cards"):
            try:
                marks = json.loads(r["marks"] or "{}")
            except ValueError:
                marks = {}
            try:
                report = json.loads(r["report"] or "{}")
            except ValueError:
                report = {}
            saved[(r["user_id"], r["tochka"])] = {
                "marks": marks, "video_date": r["video_date"] or "",
                "note": r["note"] or "", "ts": r["ts"] or "",
                "report": report, "report_date": r["report_date"] or ""}
        vids: dict[int, list[str]] = {}
        for r in conn.execute("SELECT user_id, date FROM speech_videos ORDER BY date"):
            vids.setdefault(r["user_id"], []).append(r["date"])
        notes = {r["user_id"]: r["note"] or "" for r in
                 conn.execute("SELECT user_id, note FROM speech_notes")}
    for d in deti:
        d["точки"] = {k: saved.get((d["user_id"], k), {}) for k, _, _ in TOCHKI}
        # старые отметки видео по точкам тоже считаются
        vv = sorted(set(vids.get(d["user_id"], []) +
                        [v.get("video_date") for v in d["точки"].values() if v.get("video_date")]))
        d["видео"] = vv
        d["последнее_видео"] = vv[-1] if vv else ""
        d["видео_просрочено"] = (not vv) or (today - date.fromisoformat(vv[-1])).days > 31
        d["видео_в_месяце"] = any(v.startswith(month) for v in vv)
        d["заполнено"] = sum(1 for v in d["точки"].values() if v.get("marks"))
        d["отчётов"] = sum(1 for v in d["точки"].values() if v.get("report_date"))
        d["отчёт_сейчас"] = d["точки"].get(tek, {}).get("report_date", "")
        d["пункты"] = DOROZHKI[d["дорожка"]]
        # в CRM «Фамилия Имя (пометка)» — для обращения нужно только имя
        slova = re.sub(r"\(.*?\)", "", d["имя"] or "").split()
        d["имя_короткое"] = slova[1] if len(slova) >= 2 else (slova[0] if slova else "")
        d["заметка"] = notes.get(d["user_id"], "") or d["точки"]["t1"].get("note", "")
    gruppy = sorted({(d["педагог"], d["группа"]) for d in deti})
    return {"дети": deti, "точки": TOCHKI, "ответы": OTVETY, "сегодня": today.isoformat(),
            "текущая_точка": tek,
            "текущая_точка_имя": dict((k, w) for k, w, _ in TOCHKI)[tek],
            "группы": [{"педагог": p, "группа": g,
                        "детей": sum(1 for d in deti if d["группа"] == g)} for p, g in gruppy],
            "всего": len(deti),
            "не_оформлены": sum(1 for d in deti if d["статус"] != "учится"),
            "без_видео": sum(1 for d in deti if not d["последнее_видео"]),
            "видео_в_месяце": sum(1 for d in deti if d["видео_в_месяце"]),
            "отчёт_сейчас": sum(1 for d in deti if d["отчёт_сейчас"])}


def save(user_id: int, tochka: str, marks: dict, video_date: str | None = None,
         note: str | None = None, author: str = "", report: dict | None = None,
         report_date: str | None = None) -> dict:
    """None в любом поле — «не трогать»: форма присылает только то, что видно
    на экране, и не должна стирать отчёт или видео другой точки."""
    if tochka not in {k for k, _, _ in TOCHKI}:
        raise ValueError("неизвестная точка замера")
    if int(user_id) <= 0:
        raise ValueError("нужен user_id ребёнка")
    keys = {k for k, _ in PUNKTY} | {k for k, _ in PUNKTY_PRO}
    if marks is not None:
        marks = {k: v for k, v in marks.items() if k in keys and v in OTVETY}
    with db.get_conn() as conn:
        _ensure(conn)
        old = conn.execute("SELECT marks, video_date, note, report, report_date FROM speech_cards "
                           "WHERE user_id=? AND tochka=?", (int(user_id), tochka)).fetchone()
        if old is not None:
            if marks is None:
                marks = json.loads(old["marks"] or "{}")
            video_date = old["video_date"] or "" if video_date is None else video_date
            note = old["note"] or "" if note is None else note
            if report is None:
                report = json.loads(old["report"] or "{}")
            report_date = old["report_date"] or "" if report_date is None else report_date
        rep = {k: str(v)[:400] for k, v in (report or {}).items()
               if k in ("umeet", "rabotaem", "doma") and str(v).strip()}
        conn.execute(
            "INSERT INTO speech_cards (user_id, tochka, marks, video_date, note, ts, author, "
            "report, report_date) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id, tochka) "
            "DO UPDATE SET marks=excluded.marks, video_date=excluded.video_date, "
            "note=excluded.note, ts=excluded.ts, author=excluded.author, "
            "report=excluded.report, report_date=excluded.report_date",
            (int(user_id), tochka, json.dumps(marks or {}, ensure_ascii=False),
             (video_date or "")[:10], (note or "")[:500], date.today().isoformat(), author[:40],
             json.dumps(rep, ensure_ascii=False), (report_date or "")[:10]))
    return {"ok": True, "отмечено": len(marks or {})}


def save_video(user_id: int, day: str, remove: bool = False, author: str = "") -> dict:
    day = (day or date.today().isoformat())[:10]
    date.fromisoformat(day)
    with db.get_conn() as conn:
        _ensure(conn)
        if remove:
            conn.execute("DELETE FROM speech_videos WHERE user_id=? AND date=?", (int(user_id), day))
            # старая отметка видео внутри точки замера — тоже снимаем
            conn.execute("UPDATE speech_cards SET video_date='' WHERE user_id=? AND video_date=?",
                         (int(user_id), day))
        else:
            conn.execute("INSERT OR IGNORE INTO speech_videos (user_id, date, ts, author) "
                         "VALUES (?,?,?,?)", (int(user_id), day, date.today().isoformat(), author[:40]))
    return {"ok": True}


def save_note(user_id: int, note: str, author: str = "") -> dict:
    with db.get_conn() as conn:
        _ensure(conn)
        conn.execute("INSERT INTO speech_notes (user_id, note, ts, author) VALUES (?,?,?,?) "
                     "ON CONFLICT(user_id) DO UPDATE SET note=excluded.note, ts=excluded.ts, "
                     "author=excluded.author",
                     (int(user_id), note[:800], date.today().isoformat(), author[:40]))
    return {"ok": True}
