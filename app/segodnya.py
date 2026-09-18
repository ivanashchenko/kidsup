"""Одна страница на день: всё, что горит, с именами и телефонами.

Владелец 17.09: «дай список детей… всё размести на одной странице». До этого
списки жили по разным местам — галочки пробного на /voronka, ждущие ответа в
Wazzup, необработанные лиды в /zayavki, обещания в инбоксе, — и чтобы собрать
картину дня, админу надо было открыть пять вкладок и свести их в голове.

Здесь всё в одном экране и считается на лету. Ничего не отправляет и ничего
не меняет — только читает.

Разделы ровно те, что горят сегодня:
  1. записи на пробное без галочки «Пробное» — по ним не уйдёт напоминание;
  2. кто ждёт ответа в переписке и сколько часов;
  3. новые лиды, которых не коснулись ничем;
  4. явка, которую не проставили.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from . import db

NEW_LEAD = 125951          # 1. Новый лид
_MUSOR = re.compile(r"дубл|дублик|тест|проверка", re.I)
DEAD = {146328, 215202, 125954}


def _p10(x) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())[-10:]


def _bez_galochki() -> list[dict]:
    """Записи на пробное, по которым не уйдёт напоминание.

    Берём из той же логики, что и воронка: у записанного на пробное есть
    будущая запись на занятие, но флага test на ней нет, и занятий в этой
    группе впереди не больше двух — то есть это разовый визит, а не ученик,
    поставленный сразу на всю серию.
    """
    from . import voronka
    today = date.today().isoformat()
    out = []
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT j.user_id FROM joins j JOIN classes c ON c.id = j.class_id "
            "WHERE j.status_id IN (58132, 83760) AND c.name LIKE '2627_%' "
            "AND (c.status IS NULL OR c.status='opened')").fetchall()
        users = {r[0]: r for r in conn.execute(
            "SELECT id, name, phone FROM users")}
        for (uid,) in rows:
            st = voronka.trial_state(conn, uid, None)
            if st.get("вид") != "ждём" or st.get("флаг_пробного", True):
                continue
            u = users.get(uid)
            if not u:
                continue
            cls = conn.execute(
                "SELECT c.name FROM lesson_records lr JOIN lessons l ON l.id = lr.lesson_id "
                "LEFT JOIN classes c ON c.id = l.class_id "
                "WHERE lr.user_id=? AND l.date=? LIMIT 1", (uid, st["дата"])).fetchone()
            out.append({"uid": uid, "имя": u[1], "телефон": u[2] or "",
                        "дата": st["дата"], "время": st.get("время", ""),
                        "группа": ((cls[0] if cls else "") or "").replace("2627_", ""),
                        "через_дней": st.get("через_дней")})
    out.sort(key=lambda r: (r["дата"], r["время"]))
    return out


# Окно, за которое ответ ещё имеет смысл. Первая версия брала всю историю и
# выдала 157 строк, где сверху висели отказы месячной давности: «мы переехали
# в другой район», «нас не интересует». Это не ждущие ответа, это законченные
# разговоры. Три дня — столько, сколько человек ещё помнит, что писал.
OKNO_CHASOV = 72


def _zhdut() -> list[dict]:
    """Кто написал нам и не получил ответа. Смайлик и «спасибо» — не вопрос."""
    now = datetime.now()
    spasibo = ("спасибо", "благодар", "хорошо", "поняла", "понял", "ок", "ага",
               "принял", "жду", "до свидания", "отлично", "ясно", "договорились")
    # разговор, который клиент сам закрыл: отвечать нечего, звать назад — отдельная работа
    otkaz = ("не интересует", "не будем", "не планируем", "переехали", "спасибо, нет",
             "нет, спасибо", "уже решил", "уже выбран", "выбрали", "не сможем",
             "не подходит", "не актуально", "буду иметь в виду", "время занято",
             "в другом", "не в москве", "в отъезде")
    # чужие рассылки: аренда, сотрудничество, продвижение — это не клиенты
    spam = ("предлагаем", "для вашего бизнеса", "сотрудничеств", "продвижен",
            "коммерческое предложение", "арендn")
    out = []
    today = date.today().isoformat()
    zavtra = (date.today() + timedelta(days=1)).isoformat()
    with db.get_conn() as conn:
        try:
            rows = conn.execute(
                "SELECT phone, MAX(ts) FROM wazzup_inbox GROUP BY substr(phone,-10)").fetchall()
        except Exception:
            return []
        # У кого ребёнок идёт на занятие сегодня или завтра. Вопрос «мы сегодня
        # в 16:00?» важнее вопроса трёхдневной давности: ответ на него нужен
        # до занятия, иначе семья просто не придёт. Сортировка по одним часам
        # ожидания топит такие строки в самый низ.
        blizko: dict[int, str] = {}
        for uid, d_ in conn.execute(
                "SELECT lr.user_id, l.date FROM lesson_records lr "
                "JOIN lessons l ON l.id = lr.lesson_id WHERE l.date IN (?,?)",
                (today, zavtra)):
            if d_ < blizko.get(uid, "9999"):
                blizko[uid] = d_
        users = {}
        for uid, name, phone in conn.execute("SELECT id, name, phone FROM users"):
            p = _p10(phone)
            if p:
                users.setdefault(p, (uid, name))
        for phone, ts in rows:
            p = _p10(phone)
            last_out = conn.execute(
                "SELECT MAX(ts) FROM wazzup_outbox WHERE substr(phone,-10)=?", (p,)).fetchone()[0]
            if last_out and last_out >= ts:
                continue                      # мы ответили последними
            txt = conn.execute(
                "SELECT text FROM wazzup_inbox WHERE substr(phone,-10)=? ORDER BY ts DESC LIMIT 1",
                (p,)).fetchone()
            text = (txt[0] if txt else "") or ""
            try:
                wait = round((now - datetime.fromisoformat(ts[:19])).total_seconds() / 3600, 1)
            except Exception:
                wait = None
            if wait is None or wait > OKNO_CHASOV:
                continue
            low = text.strip().lower()
            # короткая благодарность ответа не требует — не гоняем админа зря
            # сообщение из одних смайликов — это «принято», а не вопрос
            bukvy = any(ch.isalnum() for ch in low)
            vezhlivost = (not bukvy) \
                or (len(low) <= 30 and any(low.startswith(s) for s in spasibo)) \
                or any(o in low for o in otkaz) \
                or any(o in low for o in spam)
            uid, name = users.get(p, (None, ""))
            zan = blizko.get(uid, "")
            out.append({"uid": uid, "имя": name or "", "телефон": "7" + p,
                        "ждёт_часов": wait, "текст": text[:200],
                        "занятие": ("сегодня" if zan == today
                                    else "завтра" if zan == zavtra else ""),
                        "вежливость": vezhlivost})
    # сначала те, у кого занятие на носу, внутри — кто ждёт дольше
    out.sort(key=lambda r: ({"сегодня": 0, "завтра": 1}.get(r["занятие"], 2),
                            -(r["ждёт_часов"] or 0)))
    return out


def _novye_lidy() -> list[dict]:
    """Новые лиды, которых не коснулись ничем: ни звонка, ни комментария."""
    from . import voronka
    out = []
    with db.get_conn() as conn:
        voronka._ensure(conn)
        called = {r[0][-10:] for r in conn.execute(
            "SELECT phone FROM mango_calls WHERE direction='out'")}
        commented = {r[0] for r in conn.execute(
            "SELECT DISTINCT user_id FROM crm_comments WHERE manager_id IN (%s)"
            % ",".join(str(x) for x in voronka.MANAGERS))}
        written = set()
        try:
            written = {r[0][-10:] for r in conn.execute("SELECT phone FROM wazzup_outbox")}
        except Exception:
            pass
        for uid, name, phone, created in conn.execute(
                "SELECT id, name, phone, created_at FROM users WHERE client_state_id=?",
                (NEW_LEAD,)):
            p = _p10(phone)
            if not p or uid in commented or p in called:
                continue
            # 77777777777 — заглушка, которую админ ставит, чтобы сохранить
            # карточку; «дубль» и «тест» админы пишут в имя сами
            if p.startswith("7777777") or _MUSOR.search(name or ""):
                continue
            out.append({"uid": uid, "имя": name or "", "телефон": "7" + p,
                        "создан": (created or "")[:10],
                        "писали": p in written})
    out.sort(key=lambda r: r["создан"], reverse=True)
    return out


# 13.09 по группам английского ушла рассылка с приглашением в чат, и текст в
# ней был неверный: он звал заходить в кабинет по номеру телефона и вёл на
# app.moyklass.com/lk — это кабинет сотрудника, клиент туда войти не может.
# Кабинет клиента живёт на kidsup.tvoyklass.com, вход по e-mail ученика.
# Несколько семей написали «не смогла войти, откройте доступ», получили
# «как откроем — сразу напишем» и ждут до сих пор. Открывать нечего:
# доступ и так открыт, не хватало только почты в карточке. Эти ответы старше
# трёх суток и в раздел «ждут» не попадают — поэтому им отдельное место.
_CHAT_MARK = ("moyklass.com/lk", "чат групп", "групповой чат", "чат группы")


def _chaty() -> list[dict]:
    """Кто ответил на рассылку про чаты и остался без ответа."""
    out = []
    with db.get_conn() as conn:
        try:
            posl = conn.execute(
                "SELECT ts, phone, text FROM wazzup_outbox WHERE ts >= '2026-09-10' "
                "ORDER BY ts").fetchall()
        except Exception:
            return []
        # телефон -> когда ушло приглашение в чат
        priglas: dict[str, str] = {}
        for ts, phone, text in posl:
            low = (text or "").lower()
            if any(m in low for m in _CHAT_MARK):
                p = _p10(phone)
                if p:
                    priglas.setdefault(p, str(ts))
        if not priglas:
            return []
        users = {}
        try:
            rows_u = conn.execute("SELECT id, name, phone, email FROM users").fetchall()
        except Exception:
            rows_u = [(a, b, c, None) for a, b, c
                      in conn.execute("SELECT id, name, phone FROM users")]
        for uid, name, phone, email in rows_u:
            pp = _p10(phone)
            if pp:
                users.setdefault(pp, (uid, name, email))
        now = datetime.now()
        for p, ts_out in priglas.items():
            otvet = conn.execute(
                "SELECT ts, text FROM wazzup_inbox WHERE substr(phone,-10)=? AND ts > ? "
                "ORDER BY ts DESC LIMIT 1", (p, ts_out)).fetchone()
            if not otvet:
                continue                      # промолчали — тут ничего не горит
            # Смотрим ВСЁ, что ушло после их сообщения, а не только последнее.
            # У Жамовой последним было автоматическое «как первые занятия?» —
            # по нему разговор выглядит продолженным, хотя обещание открыть
            # доступ, данное накануне, так и висит невыполненным.
            nash = conn.execute(
                "SELECT ts, text FROM wazzup_outbox WHERE substr(phone,-10)=? AND ts > ? "
                "ORDER BY ts", (p, otvet[0])).fetchall()
            obeshchali = ""
            reshili = False
            for nts, ntext in nash:
                nlow = (ntext or "").lower()
                # настоящий ответ — тот, где названа почта или верный адрес кабинета
                if "tvoyklass" in nlow or "почт" in nlow or "e-mail" in nlow or "емейл" in nlow:
                    reshili = True
                # «Как откроем доступ, сразу напишем вам здесь» — это не ответ,
                # а обещание, которое выполнить нельзя: открывать нечего, доступ
                # открыт. Семья ждёт его четвёртый день, считая разговор живым.
                elif any(m in nlow for m in ("откро", "как только", "сообщим",
                                             "напишем вам", "дадим знать")):
                    obeshchali = str(nts)[:16].replace("T", " ")
            if reshili:
                continue                      # разобрались, почта названа
            if nash and not obeshchali:
                continue                      # ответили по существу
            uid, name, email = users.get(p, (None, "", None))
            try:
                zhdet = round((now - datetime.fromisoformat(str(otvet[0])[:19])).total_seconds() / 3600)
            except Exception:
                zhdet = None
            out.append({"uid": uid, "имя": name or "", "телефон": "7" + p,
                        "почта": email or "", "написал": str(otvet[0])[:16].replace("T", " "),
                        "ждёт_часов": zhdet, "текст": (otvet[1] or "")[:200],
                        "обещали": obeshchali})
    out.sort(key=lambda r: -(r["ждёт_часов"] or 0))
    return out


def _yavka() -> list[dict]:
    """Занятия, на которых не отмечен ни один ученик."""
    today = date.today().isoformat()
    now_hm = datetime.now().strftime("%H:%M")
    since = (date.today() - timedelta(days=6)).isoformat()
    out = []
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT l.date, l.begin_time, c.name, COUNT(lr.id), SUM(COALESCE(lr.visit,0)) "
            "FROM lessons l LEFT JOIN lesson_records lr ON lr.lesson_id = l.id "
            "LEFT JOIN classes c ON c.id = l.class_id "
            "WHERE l.date >= ? AND l.date <= ? AND c.name LIKE '2627_%' "
            "GROUP BY l.id ORDER BY l.date DESC, l.begin_time", (since, today)).fetchall()
        for d, t, name, n, y in rows:
            # занятие, которое ещё не началось, неявкой быть не может
            if d == today and (t or "")[:5] > now_hm:
                continue
            if n and not (y or 0):
                out.append({"дата": d, "время": (t or "")[:5],
                            "группа": (name or "").replace("2627_", ""), "детей": n})
    return out


def _pervye() -> list[dict]:
    """Кто идёт на первое занятие сегодня, завтра и послезавтра.

    18.09 Борис: «нужно же кто идёт на шахматы и робототехнику прозвонить».
    Пробное, которое не подтвердили голосом, срывается чаще всего: семья
    записалась неделю назад и забыла, а группа стартует только при четырёх
    оплатах — один не пришедший ребёнок решает судьбу всей группы.
    Показываем, звонили ли мы этой семье за последние сутки и писали ли.
    """
    today = date.today()
    gr = {}
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT l.date, l.begin_time, c.name, u.id, u.name, u.phone, lr.raw, l.class_id "
            "FROM lesson_records lr JOIN lessons l ON l.id = lr.lesson_id "
            "JOIN classes c ON c.id = l.class_id JOIN users u ON u.id = lr.user_id "
            "WHERE l.date BETWEEN ? AND ? AND c.name LIKE '2627_%' "
            "ORDER BY l.date, l.begin_time",
            (today.isoformat(), (today + timedelta(days=2)).isoformat())).fetchall()
        # звонок или сообщение за последние сутки — признак, что уже коснулись
        sutki = (datetime.now() - timedelta(hours=26)).isoformat(timespec="seconds")
        zvonili, pisali = set(), set()
        try:
            for (ph,) in conn.execute(
                    "SELECT DISTINCT substr(phone,-10) FROM mango_calls "
                    "WHERE ts >= ? AND state='talked'", (sutki,)):
                zvonili.add(ph)
        except Exception:
            pass
        try:
            for (ph,) in conn.execute(
                    "SELECT DISTINCT substr(phone,-10) FROM wazzup_outbox WHERE ts >= ?",
                    (sutki,)):
                pisali.add(ph)
        except Exception:
            pass
        # первое занятие: у ребёнка в этой группе раньше посещений не было
        for d_, t_, cname, uid, uname, phone, raw, class_id in rows:
            # Именно записи, а не отметки о посещении: явку админы ставят
            # с опозданием (18.09 её не было вообще), и по visit=1 «первым»
            # оказалось бы каждое занятие в центре.
            was = conn.execute(
                "SELECT 1 FROM lesson_records lr JOIN lessons l ON l.id = lr.lesson_id "
                "WHERE lr.user_id=? AND l.class_id=? AND l.date < ? LIMIT 1",
                (uid, class_id, d_)).fetchone()
            if was:
                continue
            # флаг «Пробное» приходит в сыром JSON записи полем test
            try:
                trial = bool((json.loads(raw or "{}") or {}).get("test"))
            except ValueError:
                trial = False
            p = _p10(phone)
            g = (cname or "").replace("2627_", "")
            key = (d_, (t_ or "")[:5], g)
            deti = gr.setdefault(key, {})
            # у ребёнка бывает две записи на одно занятие (и две карточки на
            # номер) — в списке для обзвона он должен быть один раз
            if uid in deti:
                deti[uid]["галочка"] = deti[uid]["галочка"] or bool(trial)
                continue
            deti[uid] = {
                "uid": uid, "имя": uname, "телефон": phone or "",
                "галочка": bool(trial),
                "звонили": p in zvonili, "писали": p in pisali}
    out = []
    for (d_, t_, g), det in sorted(gr.items()):
        deti = sorted(det.values(), key=lambda r: r["имя"] or "")
        out.append({"дата": d_, "время": t_, "группа": g, "дети": deti,
                    "детей": len(deti),
                    "без_касания": sum(1 for x in deti
                                       if not x["звонили"] and not x["писали"])})
    return out


def _dela_dnya() -> list[dict]:
    """Список дел дежурного на сегодня — тот же, что в плане дня."""
    with db.get_conn() as conn:
        try:
            rows = conn.execute(
                "SELECT id, ts, who, text, phone, done FROM plan_inbox "
                "WHERE day=? ORDER BY done, id", (date.today().isoformat(),)).fetchall()
        except Exception:
            return []
    return [dict(r) for r in rows]


def dela() -> dict:
    g = _bez_galochki()
    z = _zhdut()
    l = _novye_lidy()
    y = _yavka()
    ch = _chaty()
    # семью из раздела про чаты незачем показывать дважды: там разговор
    # предметный и с готовым текстом, здесь была бы та же строка без контекста
    v_chatah = {r["телефон"] for r in ch}
    pv = _pervye()
    dd = _dela_dnya()
    return {
        "дата": date.today().isoformat(),
        "первые": pv,
        "дела": dd,
        "без_галочки": g,
        "ждут": [r for r in z if not r["вежливость"] and r["телефон"] not in v_chatah],
        "ждут_вежливость": [r for r in z if r["вежливость"]],
        "чаты": ch,
        "новые_лиды": l,
        "явка": y,
        "итоги": {"первые_занятия": sum(r["детей"] for r in pv),
                  "первые_без_касания": sum(r["без_касания"] for r in pv),
                  "дел_открыто": sum(1 for r in dd if not r["done"]),
                  "без_галочки": len(g),
                  "ждут": sum(1 for r in z if not r["вежливость"]
                              and r["телефон"] not in v_chatah),
                  "чаты": len(ch),
                  "новые_лиды": len(l),
                  "занятий_без_явки": len(y),
                  "детей_без_явки": sum(r["детей"] for r in y)},
    }
