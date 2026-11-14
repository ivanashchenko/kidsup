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


# 14.11.2026, личные ключи педагогов (Борис 13.11: «каждому педагогу своя ссылка, видит только
# своих детей»). _pedagog() выше — подпись в заголовке группы, по названию; для доступа она
# не годится: в ней нет ни id сотрудника, ни полного имени. Кто ведёт группу на самом деле,
# записано в занятиях МойКласса (lessons.raw → teacherIds) — оттуда и берём.
OKNO_PEDAGOGA = 28        # дней назад и вперёд: занятия этого окна показывают, кто ведёт группу
DOLYA_PEDAGOGA = 0.25     # ведёт не меньше четверти занятий окна — педагог группы; разовая замена — нет
GRUPPY_SQL = ("(c.name LIKE '2627_АЯ%' OR c.name LIKE '2627_Мини-сад%' OR c.name LIKE '2627_Нулевой класс%') "
              "AND c.name NOT LIKE '%аявк%' AND (c.status IS NULL OR c.status = 'opened')")


def _imya_gruppy(name: str) -> str:
    """Название группы МойКласса → как группа названа в карточке (сад и нулевой класс — одной строкой)."""
    cls = (name or "").replace("2627_АЯ_", "").replace("2627_", "")
    for k, v in SAD.items():
        if cls.startswith(k):
            return v
    return cls


def _chastye(schet: dict, vsego: int) -> set:
    return {t for t, n in schet.items() if vsego and n >= DOLYA_PEDAGOGA * vsego}


def pedagogi_grupp(conn) -> dict:
    """Кто ведёт английский в группах карточки: {группа, как в карточке: {id сотрудника МойКласса}}.

    По занятиям группы за OKNO_PEDAGOGA дней назад и вперёд: педагог группы — тот, кто стоит
    преподавателем не меньше чем в четверти занятий, где преподаватель указан (одна замена
    группу чужому педагогу не открывает; новый педагог виден, как только на него переписали
    будущие занятия). Занятия, удалённые в МойКлассе, не считаются.
    Мини-сад и нулевой класс: английский там только пн и ср, и в занятии стоят двое —
    воспитатель и педагог английского. Берём тех, кто ведёт пн/ср, но не ведёт остальные дни;
    если так никого не осталось (английский ведёт сам воспитатель) — тех из них, кто ведёт
    группы «АЯ». Пустое множество — педагога по данным определить нельзя: такая группа
    личному ключу не показывается, владелец видит предупреждение на /owner."""
    from datetime import timedelta
    t = date.today()
    d0, d1 = (t - timedelta(days=OKNO_PEDAGOGA)).isoformat(), (t + timedelta(days=OKNO_PEDAGOGA)).isoformat()
    gruppy = {r["id"]: r["name"] or "" for r in conn.execute(f"SELECT c.id, c.name FROM classes c WHERE {GRUPPY_SQL}")}
    out: dict = {_imya_gruppy(n): set() for n in gruppy.values() if _imya_gruppy(n)}
    if not gruppy:
        return out
    udaleno = db.udalennye("lessons", conn)
    # по группе: [счёт педагогов в «английские» дни, занятий в эти дни, счёт в прочие дни, занятий в прочие дни]
    schet = {cid: [{}, 0, {}, 0] for cid in gruppy}
    for r in conn.execute("SELECT id, class_id, date, raw FROM lessons WHERE date BETWEEN ? AND ? "
                          f"AND class_id IN ({','.join('?' * len(gruppy))})", (d0, d1, *gruppy)):
        if r["id"] in udaleno:
            continue
        try:
            ped = [int(x) for x in (json.loads(r["raw"] or "{}").get("teacherIds") or [])]
            den = date.fromisoformat(str(r["date"])[:10]).weekday()
        except (TypeError, ValueError, AttributeError):
            continue
        if not ped:
            continue
        sad = any(gruppy[r["class_id"]].replace("2627_", "").startswith(k) for k in SAD)
        e = schet[r["class_id"]]
        i = 0 if (not sad or den in (0, 2)) else 2
        e[i + 1] += 1
        for x in set(ped):
            e[i][x] = e[i].get(x, 0) + 1
    aya = set()                       # кто ведёт группы «АЯ» — на случай, когда в саду английский ведёт воспитатель
    for cid, name in gruppy.items():
        if not any(name.replace("2627_", "").startswith(k) for k in SAD):
            svoi = _chastye(schet[cid][0], schet[cid][1])
            aya |= svoi
            out[_imya_gruppy(name)] |= svoi
    for cid, name in gruppy.items():
        if any(name.replace("2627_", "").startswith(k) for k in SAD):
            angl = _chastye(schet[cid][0], schet[cid][1])
            svoi = angl - _chastye(schet[cid][2], schet[cid][3])
            out[_imya_gruppy(name)] |= (svoi or (angl & aya))
    return out


def gruppy_s_pedagogami() -> list[dict]:
    """Группы карточки, в которых есть дети: [{"группа", "детей", "педагоги": [id сотрудников]}].
    Лёгкий запрос (без отметок, видео и переписки) — для блока «Ключи педагогов» на /owner."""
    with db.get_conn() as conn:
        deti = _deti(conn)
        ped = pedagogi_grupp(conn)
    schet: dict = {}
    for d in deti:
        schet[d["группа"]] = schet.get(d["группа"], 0) + 1
    return [{"группа": g, "детей": n, "педагоги": sorted(ped.get(g, ()))} for g, n in sorted(schet.items())]


def gruppy_pedagoga(mk_ids) -> set:
    """Группы карточки, которые ведёт сотрудник (сотрудники) с такими id в МойКлассе."""
    ids = {int(x) for x in (mk_ids or ()) if x is not None}
    if not ids:
        return set()
    with db.get_conn() as conn:
        return {g for g, ped in pedagogi_grupp(conn).items() if ped & ids}


def deti_grupp(gruppy) -> set:
    """user_id детей, которые сидят в этих группах карточки (тот же отбор, что на странице)."""
    gruppy = set(gruppy or ())
    if not gruppy:
        return set()
    with db.get_conn() as conn:
        return {d["user_id"] for d in _deti(conn) if d["группа"] in gruppy}


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


def _sad_pn_sr(conn) -> tuple[set, set]:
    """Маша 06.11: «не все ребята из мини-сада и нулевого класса ходят на
    английский». Английский там только пн и ср, а дети ходят по своим дням.
    Кто в пн/ср (записи ±4 недели от сегодня) — (user_id, class_id); и группы,
    по которым записи вообще есть (если синхронизации нет — никого не прячем)."""
    from datetime import timedelta
    t = date.today()
    d0, d1 = (t - timedelta(days=28)).isoformat(), (t + timedelta(days=28)).isoformat()
    est, pn_sr = set(), set()
    for r in conn.execute(
            "SELECT lr.user_id, l.class_id, l.date FROM lesson_records lr "
            "JOIN lessons l ON l.id = lr.lesson_id JOIN classes c ON c.id = l.class_id "
            "WHERE l.date BETWEEN ? AND ? AND (c.name LIKE '2627_Мини-сад%' "
            "OR c.name LIKE '2627_Нулевой класс%')", (d0, d1)):
        est.add(r["class_id"])
        try:
            if date.fromisoformat(r["date"]).weekday() in (0, 2):
                pn_sr.add((r["user_id"], r["class_id"]))
        except (TypeError, ValueError):
            pass
    return est, pn_sr


def _deti(conn) -> list[dict]:
    """Все, кто сидит в группах английского сезона: учится, на пробном, был
    на пробном. Педагогу нужен весь класс, а не только оформленные.
    В мини-саду и нулевом классе — только те, кто ходит в пн или ср."""
    q = ",".join("?" * len(LIVE))
    sad_est, sad_pn_sr = _sad_pn_sr(conn)
    rows = conn.execute(
        "SELECT j.user_id, j.status_id, j.class_id, c.name AS cls, u.name AS kid, u.phone "
        "FROM joins j JOIN classes c ON c.id = j.class_id "
        "LEFT JOIN users u ON u.id = j.user_id "
        f"WHERE j.status_id IN ({q}) AND {GRUPPY_SQL}", tuple(LIVE)).fetchall()
    out = {}
    for r in rows:
        cls = _imya_gruppy(r["cls"])
        if cls in SAD.values() and r["class_id"] in sad_est and (r["user_id"], r["class_id"]) not in sad_pn_sr:
            continue                  # ходит только вт/чт/пт — английского у него нет
        if not cls:
            continue
        key = (r["user_id"], cls)
        out[key] = {
            "user_id": r["user_id"], "имя": r["kid"] or str(r["user_id"]),
            "телефон": r["phone"] or "", "группа": cls,
            "педагог": _pedagog(cls), "дорожка": _dorozhka(cls),
            "статус": LIVE.get(r["status_id"], "")}
    return sorted(out.values(), key=lambda x: (x["педагог"], x["группа"], x["имя"] or ""))


def rows(tolko_gruppy=None) -> dict:
    """tolko_gruppy — множество групп (как они названы в карточке): педагогу с личным ключом
    показываем только его группы, и все счётчики страницы считаются по ним же (14.11.2026).
    None — все группы, как раньше."""
    today = date.today()
    month = today.isoformat()[:7]
    tek = tekushchaya_tochka(today)
    with db.get_conn() as conn:
        _ensure(conn)
        deti = _deti(conn)
        if tolko_gruppy is not None:
            deti = [d for d in deti if d["группа"] in tolko_gruppy]
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
    from . import karta_otchet as ko, deti_video
    kontakty = ko.poslednie_kontakty()
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
        # 25.09: личные сообщения родителю, видео-файлы, флаг «сначала звонок»
        kt = kontakty.get(d["user_id"], "")
        d["последний_контакт"] = kt
        d["дней_без_контакта"] = (today - date.fromisoformat(kt[:10])).days if kt else None
        d["флаг"] = ko.flag(d["user_id"], d["дорожка"], d["группа"], d["статус"])
        d["видео_файлы"] = deti_video.spisok(d["user_id"])
        d["журнал"] = ko.zhurnal(d["user_id"])[:5]
        d["группа_по_русски"] = ko.gruppa_chelovecheski(d["группа"])
    # «Двое в фокусе»: в группах, которые занимаются сегодня, — двое, кому
    # дольше всех не писали лично. Им педагог вечером отправляет весточку.
    wd = today.weekday()
    fokus = []
    for g in sorted({d["группа"] for d in deti}):
        if wd not in ko.dni_gruppy(g):
            continue
        v_gr = [d for d in deti if d["группа"] == g]
        v_gr.sort(key=lambda d: (d["последний_контакт"] or "0000", d["имя"]))
        for d in v_gr[:2]:
            d["фокус"] = True
            fokus.append(d["user_id"])
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
            "отчёт_сейчас": sum(1 for d in deti if d["отчёт_сейчас"]),
            "фокус": fokus,
            "без_контакта_15": sum(1 for d in deti if d["дней_без_контакта"] is None
                                   or d["дней_без_контакта"] > 15),
            "с_флагом": sum(1 for d in deti if d["флаг"])}


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
               if k in ("umeet", "rabotaem", "doma", "epizod", "tajm", "uchimsya", "doma_i",
                        "menyaem", "cel", "zvonok", "podpis", "tekst") and str(v).strip()}
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
