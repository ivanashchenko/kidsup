"""Пульт — единая страница плана смены: /pult.

06.09 Борис: «единая страница с планом, который адаптируется по админам
в смене + мои задачи — одна ссылка, которую ты обновляешь регулярно,
пульт управления за админами».

Что на странице (всё живое, вкладка перезагружается раз в 5 минут):
  • шапка: дата, кто в смене (admin_schedule), Лиза на переписке;
  • цифры дня по CRM: оплаты, новые записи, первые занятия, инбокс;
  • колонки задач — только для тех, кто сегодня в смене, плюс Лиза и Борис;
    задачи живут в таблице pult_tasks, Клод кладёт их через POST /api/pult/tasks,
    админ ставит галочку (POST /api/pult/done), галочка видна всем;
  • живые блоки: «Появилось за день», «Заявки с 10.08 без обработки»,
    «Свободные места сейчас»;
  • ссылки на рабочие списки (/nabor, /voronka, /mesta, /spiski, /karta) —
    админ не ходит туда сам, а попадает по ссылке из своей задачи.

?who=Аня — только одна колонка (для вкладки самого админа).
Никаких отправок — только чтение CRM и своя таблица задач.
"""
from __future__ import annotations

import html
import json
import logging
from datetime import datetime

from . import db

log = logging.getLogger("kidsup.pult")

SHORT = {"Анна Инкина": "Аня", "Елена Кузнецова": "Лена", "Ирина Головина": "Ира"}
COLOR = {"Лена": "#7DB928", "Аня": "#F59C00", "Ира": "#E30613", "Лиза": "#1DA7E0", "Борис": "#312783"}
ROLE = {"Аня": "телефон, деньги, пробные", "Ира": "телефон, переписка, дверь", "Лена": "дожим, оплаты, разговор на выходе",
        "Лиза": "только явка и долги", "Борис": "решения"}
MON = {1: "yan", 2: "fev", 3: "mar", 4: "apr", 5: "may", 6: "iyn", 7: "iyl", 8: "avg", 9: "sen", 10: "okt", 11: "noy", 12: "dek"}
LIVE_JOIN = (2, 58132, 83760, 58131)
# «перенос со вчера от Лена» читается как машинный текст — держим родительный падеж
OT = {"Аня": "Ани", "Лена": "Лены", "Ира": "Иры", "Лиза": "Лизы", "Борис": "Бориса"}


def _init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS pult_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, who TEXT, t TEXT, text TEXT,
        done INTEGER DEFAULT 0, ord INTEGER DEFAULT 0, ts TEXT)""")
    for col in ("note TEXT", "prio INTEGER"):
        try:
            conn.execute("ALTER TABLE pult_tasks ADD COLUMN " + col)
        except Exception:
            pass


def today() -> str:
    from . import autopilot
    return autopilot._today().isoformat()


def duty(day: str) -> list[str]:
    """Короткие имена дежурных по admin_schedule; нет записи — все звонящие админы."""
    from . import autopilot
    admins = autopilot._admins()
    try:
        sched = json.loads(db.get_setting("admin_schedule") or "{}")
    except ValueError:
        sched = {}
    mid = sched.get(day)
    mids = mid if isinstance(mid, list) else ([mid] if mid else [])
    on = [a for a in admins if a.get("managerId") in mids] or admins
    return [SHORT.get(a.get("name", ""), a.get("name", "")) for a in on]


def tasks(day: str) -> dict[str, list[dict]]:
    with db.get_conn() as conn:
        _init(conn)
        rows = conn.execute("SELECT id, who, t, text, done, note, prio FROM pult_tasks WHERE day=? ORDER BY who, ord, id",
                            (day,)).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["who"], []).append(dict(r))
    return out


def set_tasks(day: str, who: str, items: list[dict], replace: bool = True) -> int:
    """items: [{"t": "11:00", "text": "…"}]. replace=True — колонка на день заменяется целиком,
    но сделанные галочки по совпадающему тексту сохраняются."""
    now = datetime.now().isoformat(timespec="seconds")
    with db.get_conn() as conn:
        _init(conn)
        done_texts = set()
        if replace:
            done_texts = {r[0] for r in conn.execute(
                "SELECT text FROM pult_tasks WHERE day=? AND who=? AND done=1", (day, who))}
            conn.execute("DELETE FROM pult_tasks WHERE day=? AND who=?", (day, who))
            base = 0
        else:
            base = conn.execute("SELECT COALESCE(MAX(ord),0) FROM pult_tasks WHERE day=? AND who=?",
                                (day, who)).fetchone()[0]
        n, chuzhie = 0, []
        for i, it in enumerate(items, 1):
            text = str(it.get("text") or "").strip()
            if not text:
                continue
            # 22.09.2026, аудит. Решение владельца от 20.09: у Лизы только
            # закрытие занятий (явка, «проведено») и долги. Помешать поставить
            # ей что-то ещё было нечему — и на 22.09 у неё в колонке лежали
            # две чужие задачи. Такие не записываем и называем вслух, чтобы
            # тот, кто их ставил, переадресовал.
            if who == "Лиза":
                from .pult_proverka import LIZA_MOZHNO
                if not LIZA_MOZHNO.search(text):
                    chuzhie.append(text[:70])
                    continue
            conn.execute("INSERT INTO pult_tasks (day, who, t, text, done, ord, ts) VALUES (?,?,?,?,?,?,?)",
                         (day, who, str(it.get("t") or ""), text, 1 if text in done_texts else 0, base + i, now))
            n += 1
        if chuzhie:
            log.warning("set_tasks: Лизе не поставлено %d задач вне её роли "
                        "(с 20.09 у неё только явка и долги): %s", len(chuzhie), chuzhie)
        return n


def mark(task_id: int, state: int, note: str = "") -> bool:
    """state: 0 — не сделано, 1 — сделано, 2 — «перенести на завтра».

    08.09: кнопку «не успела» с вопросом «почему» за три дня не нажали ни разу —
    пункты просто висели. Теперь одна кнопка без вопросов: пункт помечается
    перенесённым и СРАЗУ копируется в завтрашнюю колонку (без дублей, если
    нажать дважды).

    10.09: перенос уходил тому же человеку — и попадал в никуда. 10.09 дежурит
    одна Лена, 11.09 — Ира и Аня; всё, что Лена перенесла бы «на завтра»,
    легло бы в колонку человека, который завтра не работает, и пункт не увидел
    бы никто. Поэтому дело дежурной уходит той, кто дежурит завтра, с пометкой,
    от кого оно пришло. Лизу (переписка) и Бориса (решения) не трогаем: у них
    роль, а не смена."""
    from datetime import date, timedelta
    with db.get_conn() as conn:
        _init(conn)
        row = conn.execute("SELECT day, who, t, text FROM pult_tasks WHERE id=?", (int(task_id),)).fetchone()
        # 22.09.2026, аудит: заметка перетиралась пустой при каждом нажатии
        # галочки, и счётчик попыток дозвона («не дозвонилась ☎») обнулялся.
        # Пустую заметку не пишем — состояние и комментарий живут отдельно.
        if (note or "").strip():
            cur = conn.execute("UPDATE pult_tasks SET done=?, note=? WHERE id=?",
                               (int(state), (note or "")[:200], int(task_id)))
        else:
            cur = conn.execute("UPDATE pult_tasks SET done=? WHERE id=?",
                               (int(state), int(task_id)))
        if int(state) == 2 and row:
            nxt = (date.fromisoformat(row["day"]) + timedelta(days=1)).isoformat()
            who, frm = row["who"], ""
            shifts = set(SHORT.values())          # Аня, Лена, Ира — те, у кого смены
            if who in shifts:
                tomorrow = [d for d in duty(nxt) if d in shifts]
                if tomorrow and who not in tomorrow:
                    who, frm = tomorrow[0], f" от {OT.get(row['who'], row['who'])}"
            head = f"↩ <b>перенос со вчера{frm}</b> — "
            text = row["text"] if row["text"].startswith("↩") else head + row["text"]
            dup = conn.execute("SELECT 1 FROM pult_tasks WHERE day=? AND who=? AND text=?", (nxt, who, text)).fetchone()
            if not dup:
                conn.execute("INSERT INTO pult_tasks (day, who, t, text, done, note) VALUES (?,?,?,?,0,'')",
                             (nxt, who, row["t"], text))
        return cur.rowcount > 0


def nedozvon(kind: str, item_id: int) -> dict:
    """Исход «набрала — не дозвонилась». Дело остаётся, попытка считается.

    21.09.2026. До сегодня у дела был один исход — галочка. Админ звонит,
    никто не берёт, и дальше два пути: соврать галочкой или оставить строку
    висеть без следа. Так 19.09 список обзвона закрыли 88 галочками при нуле
    звонков. Теперь вторая кнопка: попытка записывается, дело остаётся и
    уходит чуть ниже, на третьей попытке подсказываем перейти в мессенджер.
    """
    kind = "task" if str(kind) == "task" else "inbox"
    with db.get_conn() as conn:
        _init(conn)
        _inbox_tries(conn)
        if kind == "task":
            row = conn.execute("SELECT note FROM pult_tasks WHERE id=?", (int(item_id),)).fetchone()
            if not row:
                return {"ok": False}
            n = (row["note"] or "").count("не дозвонилась") + 1
            conn.execute("UPDATE pult_tasks SET note=? WHERE id=?",
                         (f"{(row['note'] or '').strip()} не дозвонилась".strip()[:200], int(item_id)))
        else:
            row = conn.execute("SELECT COALESCE(tries,0) FROM plan_inbox WHERE id=?",
                               (int(item_id),)).fetchone()
            if not row:
                return {"ok": False}
            n = int(row[0]) + 1
            conn.execute("UPDATE plan_inbox SET tries=? WHERE id=?", (n, int(item_id)))
    sovet = ("Третья попытка — дальше телефоном не возьмёшь: напиши в мессенджер "
             "и поставь статус «2. Нет ответа»." if n >= 3 else
             "Попытка записана. Дело осталось в колонке — набери ещё раз через час.")
    return {"ok": True, "попыток": n, "совет": sovet}


def _govorili(phone: str, hours: int = 4) -> dict:
    """Был ли за последние часы разговор от 30 секунд по этому номеру.

    То же правило, что владелец принял для обзвона, только теперь на всём
    пульте: галочка на деле с одним телефоном должна за чем-то стоять.
    """
    from datetime import timedelta
    from . import autopilot
    p = "".join(c for c in str(phone or "") if c.isdigit())[-10:]
    if len(p) < 10:
        return {"есть": True, "чем": ""}          # дело без телефона не проверяем
    since = (autopilot._now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    with db.get_conn() as conn:
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(mango_calls)")}
            sel = "ts, state" + (", secs" if "secs" in cols else "")
            for row in conn.execute(
                    f"SELECT {sel} FROM mango_calls WHERE ts >= ? AND substr(phone,-10)=?",
                    (since, p)):
                if str(row[1] or "") != "talked":
                    continue
                secs = int(row[2] or 0) if "secs" in cols and len(row) > 2 else 0
                if secs and secs < 30:
                    continue
                return {"есть": True, "чем": f"разговор {str(row[0])[11:16]}"
                                             + (f", {secs} с" if secs else "")}
        except Exception:
            return {"есть": True, "чем": ""}      # журнала нет — не мешаем работать
        try:
            for row in conn.execute(
                    "SELECT ts FROM wazzup_outbox WHERE ts >= ? AND substr(phone,-10)=?",
                    (since, p)):
                return {"есть": True, "чем": f"написали в {str(row[0])[11:16]}"}
        except Exception:
            pass
    return {"есть": False, "почему": "за последние 4 часа нет ни разговора от 30 секунд, "
                                     "ни отправленного сообщения по этому номеру"}


def kpi(day: str) -> dict:
    with db.get_conn() as conn:
        pays = conn.execute("SELECT COUNT(*), COALESCE(SUM(summa),0) FROM payments WHERE date=? AND summa>0", (day,)).fetchone()
        joins_new = conn.execute(
            "SELECT COUNT(*) FROM joins WHERE substr(created_at,1,10)=? AND status_id IN (%s)" % ",".join("?" * len(LIVE_JOIN)),
            (day, *LIVE_JOIN)).fetchone()[0]
        lessons = conn.execute("SELECT id FROM lessons WHERE date=?", (day,)).fetchall()
        lids = [r["id"] for r in lessons]
        firsts = kids = 0
        if lids:
            q = ",".join("?" * len(lids))
            kids = conn.execute(f"SELECT COUNT(*) FROM lesson_records WHERE lesson_id IN ({q})", lids).fetchone()[0]
            for r in conn.execute(f"SELECT raw FROM lesson_records WHERE lesson_id IN ({q})", lids):
                try:
                    if json.loads(r["raw"] or "{}").get("test"):
                        firsts += 1
                except ValueError:
                    pass
        # 08.09 Борис: явку отмечает Лиза по вечерам по спискам педагогов — поэтому
        # считаем и вчерашний день: он должен быть закрыт к утру.
        from datetime import date as _d, timedelta as _td
        yday = (_d.fromisoformat(day) - _td(days=1)).isoformat()
        ylids = [r["id"] for r in conn.execute("SELECT id FROM lessons WHERE date=?", (yday,)).fetchall()]
        y_kids = y_visits = 0
        if ylids:
            yq = ",".join("?" * len(ylids))
            for r in conn.execute(f"SELECT raw FROM lesson_records WHERE lesson_id IN ({yq})", ylids):
                y_kids += 1
                try:
                    if json.loads(r["raw"] or "{}").get("visit"):
                        y_visits += 1
                except ValueError:
                    pass
        visits = 0
        if lids:
            for r in conn.execute(f"SELECT raw FROM lesson_records WHERE lesson_id IN ({q})", lids):
                try:
                    if json.loads(r["raw"] or "{}").get("visit"):
                        visits += 1
                except ValueError:
                    pass
        # 22.09.2026, аудит. Считали SUM(done), а done=2 означает «перенесено
        # на завтра»: каждое нажатие «перенести» добавляло к итогу дня ДВА
        # закрытых дела. Цифра «сделано за день» завышалась ровно на число
        # переносов — то есть тем сильнее, чем хуже шёл день.
        try:
            ib = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(CASE WHEN done=1 THEN 1 ELSE 0 END),0) "
                "FROM plan_inbox WHERE day=?", (day,)).fetchone()
        except Exception:
            ib = (0, 0)
        tk = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(CASE WHEN done=1 THEN 1 ELSE 0 END),0) "
            "FROM pult_tasks WHERE day=?", (day,)).fetchone() \
            if conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='pult_tasks'").fetchone()[0] else (0, 0)
    return {"pays": pays[0], "pays_sum": int(pays[1]), "joins_new": joins_new, "lessons": len(lids), "kids": kids,
            "firsts": firsts, "visits": visits, "y_kids": y_kids, "y_visits": y_visits, "inbox": ib[0], "inbox_done": ib[1], "tasks": tk[0], "tasks_done": tk[1]}


def _re_phone(text: str | None) -> bool:
    import re as _re
    return bool(_re.search(r"\b[78]?9\d{9}\b", text or ""))


def _tel(phone: str | None) -> str:
    """Номер для ссылки tel:/wa.me — всегда с кодом страны, иначе пусто.

    22.09.2026, аудит пульта. Наряд хранил десять цифр, а ссылка собиралась
    как tel:+{номер}: телефон дежурной набирал +90 (Турция) вместо +7 903…,
    +81 (Япония) вместо 8 123…, +49 (Германия) вместо 8 495…. На живой
    странице таких ссылок было тридцать шесть. Лучше не показать кнопку,
    чем отправить админа в международный вызов.
    """
    d = "".join(c for c in str(phone or "") if c.isdigit())
    if len(d) == 10 and d[0] == "9":
        return "7" + d
    if len(d) == 11 and d[0] in "78":
        return "7" + d[1:]
    if len(d) == 10:                       # городской: 495…, 812…
        return "7" + d
    return ""


def _sklon(n: int, one: str, few: str, many: str) -> str:
    """«1 пункт», «2 пункта», «32 пункта», «5 пунктов» — админ читает это
    каждый день, и «32 пунктов» бьёт по глазам."""
    t = n % 100
    if 11 <= t <= 14:
        return f"{n} {many}"
    return f"{n} {one}" if n % 10 == 1 else (f"{n} {few}" if 2 <= n % 10 <= 4 else f"{n} {many}")


def _first_time(t: str) -> str:
    """«12:00, 12:30» → «12:00»; «15:00–17:00» → «15:00»; «весь день» → ""."""
    import re as _re
    m = _re.search(r"\b(\d{1,2}):(\d{2})", t or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def _inbox_open(day: str, who: str) -> int:
    try:
        with db.get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM plan_inbox WHERE day=? AND who=? AND done=0",
                                (day, who)).fetchone()[0]
    except Exception:
        return 0


def _paid_recently(days: int = 30) -> dict[str, tuple[str, int]]:
    """Телефон (последние 10 цифр) → (дата, сумма) последней оплаты за N дней.

    10.09.2026, по разбору Иры: первой задачей дня стояло «Слатину срочно
    отправить ссылку на оплату», а он оплатил 05.09 — 5 200 ₽ за ту самую
    группу. Заметка администратора была написана вечером 09.09 и к утру
    протухла, а проверить оплату можно было одним запросом. Теперь у каждого
    пункта, где клиент недавно платил, висит зелёная плашка с датой и суммой:
    видно и мне, когда я собираю пульт, и админу, когда она его открывает.
    """
    from datetime import date as _d, timedelta as _td
    since = (_d.today() - _td(days=days)).isoformat()
    out: dict[str, tuple[str, int]] = {}
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT u.phone, p.date, p.summa FROM payments p JOIN users u ON u.id = p.user_id "
                "WHERE p.summa > 0 AND substr(p.date,1,10) >= ? ORDER BY p.date", (since,)).fetchall()
    except Exception:
        return out
    for r in rows:
        ph = "".join(ch for ch in (r["phone"] or "") if ch.isdigit())[-10:]
        if len(ph) == 10:
            out[ph] = (r["date"][:10], int(r["summa"]))   # последняя по возрастанию даты
    return out


def _promises_html(day: str, who: str, color: str, items: list[dict] | None = None) -> str:
    """Обещания клиентам этого человека — прямо в его колонке, под задачами.
    08.09 Борис: «всё на одной странице», отдельной страницы под телефон не нужно.

    21.09 Борис спросил, как задачи сверху связаны с блоками ниже. Связь была
    только в голове у того, кто ставил задачи: у Ани 6 задач и 12 обещаний,
    и пять обещаний — про те же семьи, что уже перечислены в задаче блоком
    («заявки без касания: Милана, Ларина…»). Админ видит это как двойную
    работу. Теперь обещание, чей телефон встречается в тексте задачи того же
    человека, помечается «в задаче ЧЧ:ММ» — видно, что отдельно звонить не надо.
    """
    try:
        with db.get_conn() as conn:
            rows = conn.execute("SELECT id, ts, text, phone, source, done FROM plan_inbox WHERE day=? AND who=? ORDER BY done, id",
                                (day, who)).fetchall()
    except Exception:
        rows = []
    if not rows:
        return ""
    import re as _re
    in_task: dict[str, str] = {}          # последние 10 цифр телефона → время задачи
    for it in (items or []):
        for num in _re.findall(r"\d{10,11}", it.get("text") or ""):
            in_task.setdefault(num[-10:], _first_time(it.get("t") or "") or (it.get("t") or ""))
    paid = _paid_recently()
    lis = []
    for r in rows:
        ph = "".join(ch for ch in (r["phone"] or "") if ch.isdigit())
        _t = _tel(ph)
        tel = f" <a href='tel:+{_t}' style='color:#6c6a86;white-space:nowrap'>+{_t}</a>" if _t else ""
        task_t = in_task.get(ph[-10:]) if len(ph) >= 10 else None
        if not task_t:
            for num in _re.findall(r"\d{10,11}", r["text"] or ""):
                task_t = in_task.get(num[-10:])
                if task_t:
                    break
        if task_t and not r["done"]:
            tel += (f" <span style='display:inline-block;background:#eef1fb;color:#312783;border-radius:6px;"
                    f"padding:1px 7px;font-size:11.5px;font-weight:700;white-space:nowrap'>в задаче {html.escape(task_t)}</span>")
        pay = paid.get(ph[-10:]) if len(ph) >= 10 else None
        if pay:
            d, summa = pay
            tel += (f" <span style='display:inline-block;background:#eaf5db;color:#3f6f0f;border-radius:6px;"
                    f"padding:1px 7px;font-size:11.5px;font-weight:700;white-space:nowrap'>"
                    f"оплата {d[8:10]}.{d[5:7]} · {summa:,} ₽</span>".replace(",", " "))
        txt = html.escape(r["text"] or "")
        short = txt if len(txt) <= 150 else f"{txt[:150]}<details style='display:inline'><summary style='display:inline;cursor:pointer;color:#6c6a86'> …</summary> {txt[150:]}</details>"
        lis.append(f"<li style='margin:6px 0;{'opacity:.45;text-decoration:line-through' if r['done'] else ''}'><label style='display:flex;gap:8px;align-items:flex-start;cursor:pointer'>"
                   f"<input type='checkbox' {'checked' if r['done'] else ''} style='margin-top:4px;width:18px;height:18px;flex:none' "
                   f"onchange=\"inboxDone(this,{r['id']})\">"
                   f"<span><span style='color:#6c6a86;font-size:11.5px'>{html.escape((r['source'] or '')[:22])}</span> {short}{tel}</span></label></li>")
    open_n = sum(1 for r in rows if not r["done"])
    open_li = [h for r, h in zip(rows, lis) if not r["done"]]
    done_li = [h for r, h in zip(rows, lis) if r["done"]]
    body = "".join(open_li)
    if done_li:
        body += f"<details style='margin:4px 0'><summary style='cursor:pointer;color:#4e8a12;font-size:13px'>закрыто {len(done_li)} ✓</summary><ol style='list-style:none;padding:0;margin:0'>{''.join(done_li)}</ol></details>"
    head_col = "#E30613" if open_n else "#4e8a12"
    return (f"<div style='margin-top:12px;border-top:2px dashed {color};padding-top:8px'>"
            f"<div style='font-weight:800;font-size:15px;color:{head_col}'>Обещания клиентам: {open_n} не закрыто</div>"
            f"<div style='font-size:12px;color:#6c6a86;margin-bottom:4px'>что мы пообещали семьям в звонках и переписке — сверху вниз, галочка сразу после действия</div>"
            f"<ol class='small' style='list-style:none;padding:0;margin:0'>{body}</ol></div>")


def _lenta(day: str, who: str, items: list[dict]) -> list[dict]:
    """Одна лента на человека: задачи смены и обещания клиентам вперемешку,
    по времени.

    21.09.2026. Борис, глядя на колонку: «Я не понимаю вообще как админам
    пользоваться пультом!!?? Когда им идти в "обещания" и "заявки где
    недоделали"??!!» — и он прав. В колонке было два списка с разными
    правилами (задачи сверху, обещания под ними), блок заявок — третий, и
    порядок действий держался только на объяснении сверху страницы.

    Теперь список один и правило одно: иди сверху вниз. Задача смены и
    обещание клиенту различаются меткой, а не местом на странице.
    """
    out: list[dict] = []
    for it in items:
        # Попытки дозвона у задачи смены жили только в заметке, а в ленту
        # приходил жёсткий ноль — кнопка «не дозвонилась ☎» нажималась, но
        # на странице ничего не менялось (22.09, аудит).
        out.append({"kind": "task", "id": it["id"], "t": it["t"], "text": it["text"],
                    "done": it["done"], "note": it.get("note"), "tag": "", "phone": "",
                    "tries": (it.get("note") or "").lower().count("не дозвонил"),
                    "prio": it.get("prio")})
    try:
        with db.get_conn() as conn:
            _inbox_tries(conn)
            # 22.09.2026, аудит пульта. Незакрытый пункт в полночь просто
            # исчезал: колонка строилась запросом WHERE day=сегодня, и всё,
            # что не успели вчера, переставало показываться кому бы то ни
            # было. Так накопилось 261 дело за 14–21.09 — с обещаниями
            # семьям «перезвоним сегодня». Берём и хвост прошлых дней,
            # помечая, с какого числа он висит: дата обещания важнее, чем
            # аккуратный список.
            rows = conn.execute(
                "SELECT id, ts, day, text, phone, source, done, COALESCE(tries,0) AS tries, prio "
                "FROM plan_inbox WHERE who=? AND (day=? OR (day<? AND done=0)) "
                "ORDER BY day, id", (who, day, day)).fetchall()
    except Exception:
        rows = []
    for r in rows:
        src = (r["source"] or "")
        t = _first_time(src) or _first_time((r["ts"] or "")[11:16]) or ""
        staryj = (r["day"] or day) < day
        tekst = r["text"] or ""
        if staryj:
            d = (r["day"] or "")[8:10] + "." + (r["day"] or "")[5:7]
            tekst = f"⏳ с {d}: {tekst}"
        out.append({"kind": "inbox", "id": r["id"], "t": t, "text": tekst,
                    "done": 1 if r["done"] else 0, "note": None,
                    "day": r["day"] or day, "staryj": staryj,
                    "tag": "наряд" if src.startswith("наряд")
                           else "возврат" if src.startswith("возврат") else "обещание",
                    "phone": "".join(c for c in (r["phone"] or "") if c.isdigit()),
                    "tries": int(r["tries"] or 0), "prio": r["prio"]})
    for x in out:
        x["ves"] = _ves(x)
    # 24.09.2026, Борис: «упорядочи по срочности и важности для набора полных
    # групп». Шаблоны VES угадывают важность по словам — и «ВЕРНУТЬ» стоит
    # выше «записать до занятия», если во втором есть слово «карточка». Когда
    # порядок разобран вручную, у дела есть prio = уровень·100 + место внутри
    # уровня; новые дела без prio встают в середину своего уровня.
    out.sort(key=lambda x: (x["ves"], x["prio"] % 100 if x.get("prio") is not None else 50,
                            x["t"] or "99:99"))
    return out


# Порядок дел — по цене ошибки, а не по времени появления.
#
# 21.09.2026, Борис: «Точно ли она самая лучшая?» Нет: хвост заявки
# сорокадневной давности стоял между «подтвердить пробное на 18:00» и
# «клиент ждёт ответа три часа» только потому, что попал в колонку в 14:04.
# Сегодня центр теряет деньги в этом порядке — так и сортируем.
# 22.09.2026. Проверка правил (docs/rabota/pult_testy.py) поймала две дыры,
# которые год стоили денег молча:
#   «Семья готова оплатить сегодня» не попадала в нулевой вес, потому что
#   шаблон был «готов(ы)? оплат» — а мамы пишут «готовА оплатить». Самый
#   дорогой сигнал в бизнесе сортировался ниже «подтвердить пробное».
#   Семья, объявившая об уходе («хотим прекратить ходить, как вернуть
#   остаток» — Харьковские, 21.09), не попадала в нулевой вес вообще:
#   удержание платящей семьи уезжало в середину колонки.
VES = (
    (0, r"!!|СРОЧНО|ЖДЁТ ОТВЕТА|ждёт ответа|не может дозвониться|"
        r"не дозвонил(ся|ась|ись)|жалоб|готов[аыо]? оплат|"
        r"вернуть деньги|возврат денег|забрать (остаток|деньги)|"
        r"прекратить ходить|перестан(ем|ут) ходить|хот(им|ят) уйти|"
        r"расторг|удержание"),
    (1, r"подтвердить пробн|пробное сегодня|первое занятие|выход с|окно \d|"
        r"оплат|счёт|счета|ссылк[ау] на оплату|абонемент законч"),
    (2, r"перезвонить|обещали перезвонить|не дозвонились|набрать|дожим|"
        r"были, не купили|не пришёл на пробное|заявк"),
    (3, r"вернуть:|возврат|бывш"),
    (4, r"статус|закрыть запись|хвост|в crm|карточк|почт|чат групп"),
)


UROVNI = {0: "Горит: сегодня и до занятия", 1: "Деньги: оплаты и продажа",
          2: "Заполнить группы: тёплые и неявки", 3: "Вторые дети, листы, вернуть бывших",
          4: "CRM — в свободное окно"}


def _ves(it: dict) -> int:
    """Чем меньше число, тем раньше дело в колонке."""
    import re as _re
    txt = (it.get("text") or "")
    if it.get("prio") is not None:
        return max(0, min(4, int(it["prio"]) // 100))
    if it.get("tag") == "возврат":
        return 3
    for ves, pat in VES:
        if _re.search(pat, txt, _re.I):
            return ves
    # 22.09.2026, аудит. Задача смены, не попавшая ни в один шаблон, всегда
    # получала 4 — хуже любого пункта с телефоном. Не потому, что она менее
    # важная, а потому, что _lenta принудительно ставит задачам пустой
    # phone, а телефон семьи у них лежит внутри текста. Так «Подтвердить
    # звонком ВСЕ вечерние пробные сегодня» (семь семей) уехало под черту
    # «до 20:00 не успеть» и в свёрнутый блок, а над ним стоял хвост заявки
    # сорокадневной давности. Смотрим и текст — как это уже делает расчёт
    # самой черты.
    if it.get("kind") == "task":
        return 2
    return 2 if (it.get("phone") or _re_phone(txt)) else 4


def _inbox_tries(conn) -> None:
    """Колонка попыток дозвона у дел инбокса — появилась 21.09 вместе с
    исходом «не дозвонилась»."""
    for col in ("tries INTEGER DEFAULT 0", "prio INTEGER"):
        try:
            conn.execute("ALTER TABLE plan_inbox ADD COLUMN " + col)
        except Exception:
            pass


def _col(who: str, items: list[dict], onduty: bool, day: str = "", now_hm: str = "") -> str:
    """Метка СЕЙЧАС идёт за часами: текущий — несделанный пункт с самым поздним временем,
    которое уже наступило (мы внутри его окна). Несделанные пункты с более ранним временем —
    «время прошло»: делаются сразу после текущего. Пункты без времени («весь день»,
    «после каждого занятия») — фоновые, без метки. Будущие — приглушены."""
    c = COLOR.get(who, "#6c6a86")
    tasks_n = len(items)
    items = _lenta(day, who, items) if day else [
        {**i, "kind": "task", "tag": "", "phone": ""} for i in items]
    done_n = sum(1 for i in items if i["done"] == 1)
    open_items = [i for i in items if not i["done"]]
    cur_key = None
    # СЕЙЧАС идёт по задачам смены: у них время плановое. У обещаний время —
    # это момент, когда обещание появилось, и метка «время прошло» на нём
    # означала бы просрочку там, где её нет.
    if [i for i in open_items if i["kind"] == "task"]:
        open_items = [i for i in open_items if i["kind"] == "task"]
    if open_items:
        timed = [(i, _first_time(i["t"])) for i in open_items if _first_time(i["t"])]
        started = [(i, ft) for i, ft in timed if not now_hm or ft <= now_hm]
        if started:
            cur = max(started, key=lambda x: x[1])[0]
        else:
            cur = open_items[0]
        cur_key = (cur["kind"], cur["id"])
    cur_ft = next((_first_time(i["t"]) for i in items if (i["kind"], i["id"]) == cur_key), "")
    paid = _paid_recently() if day else {}
    lis = []
    # Заголовки уровней — только когда порядок в колонке разобран вручную
    # (есть prio): тогда уровень значит «почему это выше», а не догадку по словам.
    razobrano = any(i.get("prio") is not None for i in items)
    prev_ves = None
    for it in items:
        st, badge, extra = "", "", ""
        head = ""
        if razobrano and not it["done"] and not it.get("staryj") and it.get("ves") != prev_ves:
            prev_ves = it.get("ves")
            head = (f"<li style='margin:12px 0 2px;font-size:11.5px;font-weight:800;letter-spacing:.04em;"
                    f"text-transform:uppercase;color:#6c6a86'>{UROVNI.get(prev_ves, '')}</li>")
        ft = _first_time(it["t"])
        # 10.09, просьба Иры: «пишите красным жизненно важное, без чего работа
        # встанет, а не СРОЧНО перенёс ли пробник свой день». Пункт, начатый
        # с «!!», получает красную плашку и красную рамку; всё остальное —
        # обычная работа, и выглядеть должно обычно.
        crit = it["text"].startswith("!!")
        if crit:
            it = {**it, "text": it["text"][2:].lstrip()}
        if it["done"] == 1:
            st = "opacity:.45;text-decoration:line-through"
        elif it["done"] == 2:
            st = "opacity:.7"
            badge = "<span style='display:inline-block;font-size:11px;font-weight:700;color:#fff;background:#a35f00;border-radius:6px;padding:1px 7px;margin-right:6px;vertical-align:middle'>перенесено на завтра</span>"
            if it.get("note") and it["note"] != "перенос":
                extra = f" <span style='color:#a35f00;font-size:12.5px'>— {html.escape(it['note'])}</span>"
        elif crit:
            st = "background:#fff2f2;border-left:4px solid #E30613;border-radius:8px;padding:6px 8px;margin-left:-8px"
            badge = ("<span style='display:inline-block;font-size:11px;font-weight:800;color:#fff;background:#E30613;"
                     "border-radius:6px;padding:1px 7px;margin-right:6px;vertical-align:middle'>БЕЗ ЭТОГО ВСТАНЕТ</span>")
        elif (it["kind"], it["id"]) == cur_key:
            st = f"background:#f1effb;border-left:4px solid {c};border-radius:8px;padding:6px 8px;margin-left:-8px"
            badge = f"<span style='display:inline-block;font-size:11px;font-weight:800;color:#fff;background:{c};border-radius:6px;padding:1px 7px;margin-right:6px;vertical-align:middle'>СЕЙЧАС</span>"
        elif it["kind"] == "inbox" and ft:
            wait = ""
            if now_hm and ft < now_hm:
                mins = (int(now_hm[:2]) * 60 + int(now_hm[3:])) - (int(ft[:2]) * 60 + int(ft[3:]))
                if mins >= 120:
                    wait = f", ждёт {mins // 60} ч"
            col = ("#a35f00", "#fff1d6") if wait else ("#0c6a94", "#e6f4fb")
            badge = (f"<span style='display:inline-block;font-size:11px;font-weight:700;color:{col[0]};"
                     f"background:{col[1]};border-radius:6px;padding:1px 7px;margin-right:6px;"
                     f"vertical-align:middle'>с {ft}{wait}</span>")
        elif ft and cur_ft and ft < cur_ft:
            badge = "<span style='display:inline-block;font-size:11px;font-weight:700;color:#a35f00;background:#fff1d6;border-radius:6px;padding:1px 7px;margin-right:6px;vertical-align:middle'>время прошло — сразу после СЕЙЧАС</span>"
        elif not ft:
            badge = "<span style='display:inline-block;font-size:11px;font-weight:700;color:#0c6a94;background:#e6f4fb;border-radius:6px;padding:1px 7px;margin-right:6px;vertical-align:middle'>фон</span>"
        else:
            st = "opacity:.7"
        # Метка вида: задача смены, обещание клиенту или наряд — чтобы в одной
        # ленте было видно, откуда пункт, но порядок оставался один.
        if it.get("tag") == "обещание":
            badge += ("<span style='display:inline-block;font-size:11px;font-weight:700;color:#8a1a00;background:#ffe7df;"
                      "border-radius:6px;padding:1px 7px;margin-right:6px;vertical-align:middle'>обещали клиенту</span>")
        elif it.get("tag") == "наряд":
            badge += ("<span style='display:inline-block;font-size:11px;font-weight:700;color:#0c6a94;background:#e6f4fb;"
                      "border-radius:6px;padding:1px 7px;margin-right:6px;vertical-align:middle'>заявка без ответа</span>")
        elif it.get("tag") == "возврат":
            badge += ("<span style='display:inline-block;font-size:11px;font-weight:700;color:#3f6f0f;background:#eaf5db;"
                      "border-radius:6px;padding:1px 7px;margin-right:6px;vertical-align:middle'>вернуть ушедшего</span>")
        if it.get("tries"):
            n = int(it["tries"])
            badge += (f"<span style='display:inline-block;font-size:11px;font-weight:700;color:#8a5a00;background:#fff1d6;"
                      f"border-radius:6px;padding:1px 7px;margin-right:6px;vertical-align:middle'>"
                      f"не дозвонилась {n}&nbsp;раз{'а' if 2 <= n <= 4 else ''}</span>")
        ctrl, tel = "", ""
        if it["kind"] == "task":
            if not it["done"]:
                ctrl = (f" <a href='#' style='font-size:12px;color:#a35f00;white-space:nowrap' title='Пункт уйдёт в завтрашнюю колонку — твою, если ты завтра в смене, иначе к дежурной' onclick=\""
                        f"fetch('/api/pult/done',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{id:{it['id']},state:2,note:'перенос'}})}}).then(()=>location.reload());return false\">перенести на завтра ↩</a>")
            done_js = f"pultDone(this,'task',{it['id']})"
            text_html = it["text"]
        else:
            done_js = f"pultDone(this,'inbox',{it['id']})"
            if not it["done"] and day:
                # Перенести можно было только задачу смены. Пункт инбокса
                # оставалось либо закрыть галочкой (соврав), либо тащить до
                # ночи: 21.09 так набралось 33 открытых за день.
                from datetime import date as _dt, timedelta as _td
                nxt = (_dt.fromisoformat(day) + _td(days=1)).isoformat()
                # Кому: если завтра человек не в смене, дело уходит завтрашней
                # дежурной — иначе оно ляжет в колонку того, кто не работает.
                shifts = set(SHORT.values())
                to_who = who
                if who in shifts:
                    tom = [d for d in duty(nxt) if d in shifts]
                    if tom and who not in tom:
                        to_who = tom[0]
                body = (f"{{ids:[{it['id']}],to_day:'{nxt}'"
                        + (f",to_who:'{to_who}'" if to_who != who else "") + "}")
                ctrl = (f" <a href='#' style='font-size:12px;color:#a35f00;white-space:nowrap' "
                        f"title='Пункт уйдёт в завтрашнюю колонку дежурной' onclick=\""
                        f"fetch('/api/plan/inbox/move',{{method:'POST',headers:{{'Content-Type':'application/json'}},"
                        f"body:JSON.stringify({body})}}).then(()=>location.reload());"
                        f"return false\">перенести на завтра ↩</a>")
            text_html = html.escape(it["text"])
            ph = it.get("phone") or ""
            if len(ph) >= 10:
                _t = _tel(ph)
                tel = (f" <a href='tel:+{_t}' style='color:#6c6a86;white-space:nowrap'>+{_t}</a>"
                       if _t else "")
                pay = paid.get(ph[-10:])
                if pay:
                    d_, summa = pay
                    tel += (f" <span style='display:inline-block;background:#eaf5db;color:#3f6f0f;border-radius:6px;"
                            f"padding:1px 7px;font-size:11.5px;font-weight:700;white-space:nowrap'>"
                            f"оплата {d_[8:10]}.{d_[5:7]} · {summa:,} ₽</span>".replace(",", " "))
        import re as _re2
        nums = set(_re2.findall(r"\b[78]?9\d{9}\b", (it.get("text") or "") + " " + (it.get("phone") or "")))
        if not it["done"] and len(nums) == 1:
            ctrl = (f" <a href='#' style='font-size:12px;color:#8a5a00;white-space:nowrap' "
                    f"title='Набрала, никто не взял — дело останется, попытка запишется' onclick=\""
                    f"pultMiss('{it['kind']}',{it['id']});return false\">не дозвонилась ☎</a>") + ctrl
        lis.append((it["done"] == 1, it["done"] == 0, bool(it.get("staryj")),
            head + f"<li style='margin:7px 0;{st}'><label style='display:flex;gap:8px;align-items:flex-start;cursor:pointer'>"
            f"<input type='checkbox' {'checked' if it['done'] == 1 else ''} style='margin-top:4px;width:18px;height:18px;flex:none' "
            f"onchange=\"{done_js}\">"
            f"<span>{badge}"
            + (f"<b>{html.escape(it['t'])}</b> — " if it["t"] and it["kind"] == "task" else "")
            + f"{text_html}{tel}{extra}{ctrl}</span></label></li>"))
    # 08.09: по 10–12 пунктов в колонке не читаются. Показываем ближайшие,
    # остальные несделанные и все сделанные — свёрнуто. 21.09: лента стала
    # общей (задачи + обещания), поэтому показываем семь, а не пять.
    LIMIT = 7
    done_html = "".join(h for d_, o_, st_, h in lis if d_)
    # 22.09.2026. Незакрытые дела прошлых дней раньше просто исчезали в
    # полночь — так пропало 261 обещание семьям. Теперь они видны, но не в
    # сегодняшней ленте: 261 строка в колонку — это уже не план, а свалка.
    # Отдельным блоком внизу, со счётчиком и самой старой датой.
    open_html = [h for d_, o_, st_, h in lis if o_ and not st_]
    staryj_html = [h for d_, o_, st_, h in lis if o_ and st_]
    moved_html = "".join(h for d_, o_, st_, h in lis if not d_ and not o_)
    # Черта смены. 21.09 у Ани к обеду висело 33 дела на четыре часа работы —
    # это не план, а гора. Считаем по-честному: звонок с записью в CRM — шесть
    # минут, дело без телефона — три. Всё, что за чертой, до конца смены не
    # делается, и врать об этом не надо: пусть человек видит границу и решает,
    # что перенести, а не тонет.
    uspeem = None
    if day == today() and now_hm and onduty:
        # На дела уходит не всё время смены: входящие звонки, встреча детей,
        # родители на ресепшене съедают около сорока процентов часа. 21.09 без
        # этой поправки выходило, что 33 дела «успеваются», — а по факту за
        # день закрывалось втрое меньше.
        left = int(max(0, (20 * 60) - (int(now_hm[:2]) * 60 + int(now_hm[3:]))) * 0.6)
        otkrytye = [i for i in items if not i["done"] and not i.get("staryj")]
        cena = [6 if i.get("phone") or _re_phone(i.get("text")) else 3
                for i in otkrytye]
        s_, uspeem = 0, 0
        for c_ in cena:
            if s_ + c_ > left:
                break
            s_ += c_
            uspeem += 1
        # Задача смены под чертой не прячется никогда. 22.09.2026, аудит:
        # «Подтвердить звонком ВСЕ вечерние пробные сегодня» и «Встретить
        # пробные 16:00–17:00» лежали в свёрнутом блоке под надписью «до
        # 20:00 не успеть» — страница сама разрешила их не делать, а это
        # каркас дня, на котором стоят семь семей. Черта режет добавочные
        # пункты, а не расписание смены.
        posledn = max((n for n, i in enumerate(otkrytye, 1)
                       if i.get("kind") == "task"), default=0)
        uspeem = max(uspeem, posledn)
    body = "".join(open_html[:LIMIT])
    if len(open_html) > LIMIT:
        hvost = open_html[LIMIT:]
        if uspeem is not None and LIMIT <= uspeem < len(open_html):
            n_do = uspeem - LIMIT
            body += (f"<details open style='margin:6px 0'><summary style='cursor:pointer;color:#6c6a86;font-size:13px'>"
                     f"ещё {n_do} успеваешь до конца смены — по шесть минут на звонок</summary>"
                     f"<ol style='list-style:none;padding:0;margin:0'>{''.join(hvost[:n_do])}</ol></details>")
            hvost = hvost[n_do:]
            if hvost:
                body += (f"<div style='margin:10px 0 4px;border-top:2px dashed #E30613;padding-top:6px;"
                         f"font-size:12.5px;color:#E30613;font-weight:700'>Ниже черты: {len(hvost)} — "
                         f"до 20:00 при обычной скорости не успеть</div>"
                         f"<details style='margin:2px 0'><summary style='cursor:pointer;color:#6c6a86;font-size:13px'>"
                         f"показать и решить, что перенести</summary>"
                         f"<ol style='list-style:none;padding:0;margin:0'>{''.join(hvost)}</ol></details>")
        else:
            body += (f"<details style='margin:6px 0'><summary style='cursor:pointer;color:#6c6a86;font-size:13px'>ещё {len(hvost)} на день — после этих семи</summary>"
                     f"<ol style='list-style:none;padding:0;margin:0'>{''.join(hvost)}</ol></details>")
    if staryj_html:
        body += (f"<div style='margin:12px 0 4px;border-top:2px solid #F59C00;padding-top:6px;"
                 f"font-size:12.5px;color:#a35f00;font-weight:700'>"
                 f"Не закрыто с прошлых дней: {len(staryj_html)}</div>"
                 f"<details style='margin:2px 0'><summary style='cursor:pointer;color:#6c6a86;font-size:13px'>"
                 f"показать — это обещания семьям, которые мы не выполнили</summary>"
                 f"<ol style='list-style:none;padding:0;margin:0'>{''.join(staryj_html)}</ol></details>")
    if moved_html:
        body += f"<details style='margin:6px 0'><summary style='cursor:pointer;color:#a35f00;font-size:13px'>перенесено на завтра</summary><ol style='list-style:none;padding:0;margin:0'>{moved_html}</ol></details>"
    if done_html:
        body += f"<details style='margin:6px 0'><summary style='cursor:pointer;color:#4e8a12;font-size:13px'>сделано {done_n} ✓</summary><ol style='list-style:none;padding:0;margin:0'>{done_html}</ol></details>"
    body = body or "<li style='color:#6c6a86'>задач пока нет — Клод положит к началу смены</li>"
    nb = "" if onduty else " <span style='font-size:11px;color:#6c6a86;font-weight:500'>не в смене</span>"
    # «Дел на сегодня» — именно на сегодня: хвост прошлых дней считаем
    # отдельно, иначе цифра в шапке колонки пугает и перестаёт быть планом.
    open_n = sum(1 for i in items if not i["done"] and not i.get("staryj"))
    staryh = sum(1 for i in items if not i["done"] and i.get("staryj"))
    late = sum(1 for i in items if not i["done"] and i["kind"] == "task"
               and _first_time(i["t"]) and cur_ft and _first_time(i["t"]) < cur_ft)
    late_html = (f" · <span style='color:#a35f00;font-weight:700'>{_sklon(late, 'дело', 'дела', 'дел')} без галочки, время прошло</span>") if late else ""
    if staryh:
        late_html += (f" · <span style='color:#a35f00;font-weight:700'>"
                      f"+{staryh} с прошлых дней</span>")
    return (f"<div class='wcard' style='border-top-color:{c}'><div class='nm'>{html.escape(who)}{nb} "
            f"<span style='font-size:12px;color:#6c6a86;font-weight:600'>{done_n}/{len(items)}</span></div>"
            f"<div class='rl'>{html.escape(ROLE.get(who, ''))} · "
            f"<span style='font-size:12.5px;color:#312783;font-weight:700'>{_sklon(open_n, 'дело', 'дела', 'дел')} на сегодня</span>{late_html}</div>"
            f"<ol class='small' style='list-style:none;padding:0;margin:0'>{body}</ol></div>")


HOWTO = ("<div class='card' style='border-left:4px solid #312783;margin:10px 0;font-size:15px'>"
         "<b>Работай только в своей колонке, сверху вниз.</b> В ней уже всё: задачи смены, "
         "обещания клиентам и заявки без ответа — одним списком по времени. Выбирать, "
         "куда пойти дальше, не нужно: следующий пункт — тот, что ниже. "
         "<b>Сделала — галочка сразу</b>, не вечером. Не успеваешь — «перенести на завтра ↩». "
         "Списки ниже (заявки, места) — справочные, туда ходить не надо: всё, что оттуда "
         "нужно сегодня, уже стоит у тебя в колонке. "
         "<details style='margin-top:6px;font-size:13.5px'><summary style='cursor:pointer;color:#6c6a86'>что значат метки и откуда берутся пункты</summary>"
         "<ul style='margin:6px 0 0;padding-left:20px'>"
         "<li><span style='background:#E30613;color:#fff;border-radius:6px;padding:1px 7px;font-size:12px;font-weight:800'>БЕЗ ЭТОГО ВСТАНЕТ</span> — делается первым, до всего остального.</li>"
         "<li><span style='background:#312783;color:#fff;border-radius:6px;padding:1px 7px;font-size:12px;font-weight:800'>СЕЙЧАС</span> — пункт текущего часа, метка идёт за часами сама.</li>"
         "<li><span style='background:#fff1d6;color:#a35f00;border-radius:6px;padding:1px 7px;font-size:12px;font-weight:700'>время прошло</span> — не сделан и не отмечен: делается сразу после СЕЙЧАС. Уже не нужен — «перенести на завтра».</li>"
         "<li><span style='background:#ffe7df;color:#8a1a00;border-radius:6px;padding:1px 7px;font-size:12px;font-weight:700'>обещали клиенту</span> — мы что-то пообещали семье в звонке или переписке. Срок — тот, что назвали клиенту.</li>"
         "<li><span style='background:#e6f4fb;color:#0c6a94;border-radius:6px;padding:1px 7px;font-size:12px;font-weight:700'>заявка без ответа</span> — семья оставила заявку или написала, и ей ещё никто не ответил. Кладётся в колонку само, раз в час.</li>"
         "<li><span style='background:#e6f4fb;color:#0c6a94;border-radius:6px;padding:1px 7px;font-size:12px;font-weight:700'>фон</span> — без времени: карта развития после занятия, ответ в чате за 30 минут. Между делом весь день.</li>"
         "<li><b>Явка</b> — Лиза вечером по спискам педагогов. Дежурная отмечает только первые занятия сразу после них: от этого зависят карта развития и оплата на выходе.</li>"
         "<li><b>Итог дня</b> складывается сам из галочек: отдельный отчёт писать не нужно.</li>"
         # 24.09.2026: «я не понимаю задачи по личному кабинету: не знаю, как
         # помочь, как настроить чат, как сбросить пароль». Всё, что нужно
         # знать дежурной, — пять строк; остальное делает Клод.
         "<li><b>Личный кабинет «Твой Класс»</b> — вход только по <b>почте</b> из поля «Email» карточки "
         "(не «Email 2»). Порядок для родителя: kidsup.tvoyklass.com → почта → «Войти» → «Восстановить пароль» → "
         "письмо от lk-noreply@tvoyklass.com (часто в «Спаме») → по ссылке задать пароль (от 6 знаков: заглавная, "
         "строчная, цифра, спецсимвол). Пароли мы не создаём и не сбрасываем — родитель делает это сам по ссылке. "
         "«Не могу войти» — сначала сверь почту в карточке по буквам: опечатка — самая частая причина. "
         "<b>Чат группы</b> настраивать не нужно: как только родитель вошёл в кабинет, Клод сам добавит ребёнка "
         "в чат его группы английского (каждое утро). Не заработало за 10 минут — пункт Борису.</li>"
         "</ul></details></div>")


def _nikogda_ne_zvonili(day: str) -> str:
    """Ответ на вопрос «а где список тех, кому ещё ни разу не позвонили?».

    21.09.2026, Борис. Таких списков у нас три, и они про разных людей:
    свежие заявки этого сезона, семьи прошлого года и те, кто ходил и ушёл.
    Показываем их одной карточкой с числами — чтобы не искать по меню.
    """
    n_new = n_old = None
    ob: dict = {}
    try:
        from . import zayavki as _z
        d = _z.collect()
        n_new = len(d["untouched"])
    except Exception:
        pass
    try:
        from . import nezvonili as _nz
        n_old = _nz.spisok(_nz.OKNO_S, _nz.OKNO_PO).get("семей_без_звонка")
    except Exception:
        pass
    try:
        from . import obzvon as _ob
        ob = _ob.spisok()
    except Exception:
        ob = {}
    def _row(n, title, link, note):
        num = f"<b style='font-size:22px;color:#312783'>{n}</b>" if n is not None else "<b>—</b>"
        return (f"<li style='margin:9px 0'>{num} <a href='{link}' style='font-weight:700'>{title}</a>"
                f"<div style='font-size:12.5px;color:#6c6a86'>{note}</div></li>")
    it = ob.get("итоги") or {}
    hodili = (f"из {it.get('ходили_всего', '—')} детей, посещавших занятия в этом окне: "
              f"{it.get('ходят_сейчас', '—')} ходят и сейчас, с {it.get('поговорили', '—')} поговорили "
              f"за набор, {it.get('написали_нам', '—')} написали нам сами, {it.get('мёртвый_статус', '—')} "
              f"со статусом «не писать». Остались эти. Из них {ob.get('платили', '—')} платили, "
              f"{ob.get('не_дозвонились', '—')} набирали и не дозвонились, "
              f"{ob.get('никогда_не_набирали', '—')} не набирали ни разу")
    return ("<div class='card' style='border-left:4px solid #1DA7E0'>"
            "<b style='font-size:17px'>Кому мы ещё ни разу не позвонили</b>"
            "<div style='font-size:12.5px;color:#6c6a86;margin:2px 0 6px'>Три списка, и это разные люди: "
            "одни у нас <b>занимались</b>, другие только <b>оставляли заявку</b>. Самое горячее из них "
            "попадает в колонки само, раз в час.</div>"
            "<ul style='list-style:none;padding:0;margin:0'>"
            + _row(ob.get("детей"), "ХОДИЛИ к нам 01.09.2025 – 30.08.2026, живого разговора не было",
                   "/obzvon", hodili)
            + _row(n_old, "Оставляли заявку за тот же год, но не занимались", "/nezvonili",
                   "карточка в CRM заведена, ни одного нашего звонка не было. База холоднее: "
                   "часть — «Звонок от 79…» без имени и рабочие номера компаний")
            + _row(n_new, "Заявки этого сезона без единого касания", "/pult#zayavki",
                   "ни звонка, ни сообщения, ни комментария — блок «Заявки, где мы не доделали» ниже")
            + "</ul></div>")


def _safe(fn, *a, imya: str = "", **kw) -> str:
    """Блок страницы, который не роняет страницу.

    22.09.2026, аудит: справочные блоки пульта звались без защиты, и падение
    одного из них — воронки, заявок, мест — уносило весь пульт вместе с
    колонками админов. Соседние блоки так уже умеют; теперь умеют все.
    """
    try:
        return fn(*a, **kw) or ""
    except Exception:
        log.exception("блок пульта «%s» упал — страницу показываем без него",
                      imya or getattr(fn, "__name__", "?"))
        return (f"<div class='card' style='border-left:4px solid #E30613'>"
                f"<b>Блок «{html.escape(imya or '?')}» не собрался.</b> "
                f"Остальной пульт работает. Клод увидит это в логе и починит.</div>")


def page(day: str = "", who: str = "") -> str:
    from . import zayavki, mesta
    from .main import _inbox_block
    day = day or today()
    on = duty(day)
    k = kpi(day)
    tk = tasks(day)
    d = datetime.fromisoformat(day)
    wd = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"][d.weekday()]
    order = on + [w for w in ("Лиза", "Борис") if w not in on]
    # 22.09.2026, аудит. Колонка рисовалась только тем, у кого есть ЗАДАЧИ
    # смены. Ира не в смене и задач нет — колонки нет, и пункты инбокса,
    # адресованные лично ей, не видел никто, включая её саму (так пролежал
    # пункт 544 про Дарсалию: спросили робототехнику, а на первое занятие
    # не позвали). Считаем и по адресатам инбокса.
    komu = set(tk)
    try:
        with db.get_conn() as conn:
            _init(conn)
            komu |= {r[0] for r in conn.execute(
                "SELECT DISTINCT who FROM plan_inbox WHERE done=0 AND day<=? "
                "AND COALESCE(who,'') != ''", (day,)).fetchall()}
    except Exception:
        pass
    extra = [w for w in sorted(komu) if w not in order]  # не в смене — показываем свёрнуто внизу
    if who:
        order = [w for w in order + extra if w == who] or [who]
        extra = []
    from . import autopilot
    now_msk = autopilot._now()
    now_hm = now_msk.strftime("%H:%M") if day == today() else ""
    cols = "".join(_col(w, tk.get(w, []), w in on or w in ("Лиза", "Борис"), day, now_hm) for w in order)
    if extra:
        cols += "".join(_col(w, tk.get(w, []), False, day, now_hm) for w in extra)
    slug = f"plan_{d.day:02d}{MON[d.month]}"
    ver = ""
    try:
        from .main import APP_VERSION
        ver = APP_VERSION
    except Exception:
        pass
    kpis = [
        (f"{k['pays']}", f"оплат сегодня · {k['pays_sum']:,} ₽".replace(",", " ")),
        (f"{k['joins_new']}", "новых записей в группы сегодня"),
        (f"{k['firsts']}", f"первых занятий сегодня из {k['kids']} детей в {k['lessons']} занятиях"),
        (f"{k['inbox_done'] + k['tasks_done']}/{k['inbox'] + k['tasks']}",
         "дел за день закрыто: план смены, обещания клиентам, заявки без ответа, возврат"),
    ]
    # явку отмечает Лиза вечером по спискам педагогов: вчера должно быть закрыто к утру,
    # сегодня — к 21:00. Красное — только если вчерашний день не закрыт.
    y_ok = (not k["y_kids"]) or k["y_visits"] >= k["y_kids"] * 0.8
    vis_col = "#4e8a12" if y_ok else "#E30613"
    today_txt = f"сегодня {k['visits']} из {k['kids']} (Лиза закрывает вечером)" if now_hm < "21:00" else f"сегодня {k['visits']} из {k['kids']}"
    kpi_html = "".join(f"<div class='k'><b>{a}</b><span>{b}</span></div>" for a, b in kpis)
    kpi_html += f"<div class='k' style='border-color:{vis_col}'><b style='color:{vis_col}'>{k['y_visits']} из {k['y_kids']}</b><span style='color:{vis_col};font-weight:700'>явка за вчера в CRM · {today_txt}</span></div>"
    hero_who = " + ".join(on) if on else "смена не задана"
    filt = "".join(f"<a href='/pult?who={html.escape(w)}' style='margin-right:8px'>{html.escape(w)}</a>" for w in on + ["Лиза", "Борис"])
    return f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'><title>Пульт KidsUP · {day}</title>
<meta name='viewport' content='width=device-width,initial-scale=1'><meta http-equiv='refresh' content='300'>
<style>
:root{{--ink:#15132e;--muted:#6c6a86;--line:#e4e2f0;--bg:#f8f7fc;--card:#fff;--indigo:#312783;--blue:#1DA7E0;--green:#7DB928;--amber:#F59C00;--red:#E30613}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:0 14px 60px}}
.hero{{background:linear-gradient(135deg,#312783,#1DA7E0);color:#fff;margin:0 -14px 14px;padding:18px 18px 16px;border-radius:0 0 18px 18px}}
.hero h1{{margin:0 0 4px;font-size:22px;line-height:1.2}}.hero p{{margin:0;opacity:.92;font-size:14px}}.hero a{{color:#fff}}
.kpis{{display:grid;gap:8px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin:12px 0}}
.k{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px}}.k b{{display:block;font-size:22px;color:var(--indigo);line-height:1.1}}.k span{{font-size:12px;color:var(--muted)}}
.who{{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));margin:12px 0}}
.wcard{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;border-top:4px solid var(--line)}}
.wcard .nm{{font-size:18px;font-weight:800}}.wcard .rl{{font-size:12.5px;color:var(--muted);margin:2px 0 8px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:12px 0}}
.small{{font-size:14px}}table{{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}}
th{{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600;padding:6px;border-bottom:2px solid var(--line)}}
td{{padding:6px;border-bottom:1px solid var(--line);vertical-align:top}}.num{{text-align:right;white-space:nowrap}}.scroll{{overflow-x:auto}}
.links a{{display:inline-block;margin:4px 10px 4px 0;font-size:14px}}
h2{{font-size:18px;margin:22px 0 8px;color:var(--indigo)}}
</style></head><body><div class='wrap'>
<div class='hero'><h1>Пульт · {wd}, {d.day:02d}.{d.month:02d} · в смене: {html.escape(hero_who)} · <span id='clock'>{now_msk.strftime('%H:%M')}</span> МСК</h1>
<p>Одна ссылка для всех: задачи по колонкам — только тем, кто сегодня работает, плюс Лиза и Борис. Галочка видна всем. Обновляется сама каждые 5 минут. Своя колонка: {filt}
{"<a href='/pult'>все</a>" if who else ""}</p></div>
<div class='kpis'>{kpi_html}</div>
{HOWTO}
<div class='who'>{cols}</div>
<div class='links card'><b>Рабочие списки</b> — открываются из задачи в колонке, отдельно заходить не нужно:
<a href='/segodnya'>что горит сегодня</a> · <a href='/nabor'>где теряем набор</a> · <a href='/voronka'>кто без оплаты</a> · <a href='/obzvon'>вернуть ушедших</a> · <a href='/nezvonili'>кому не звонили</a> · <a href='/mesta'>свободные места</a> · <a href='/spiski'>списки занятий</a> · <a href='/karta'>карта развития</a><br>
<b>Справочное:</b> <a href='/base/{slug}'>план и списки семей</a> · <a href='/base/gruppy_reshenia'>куда зовём, какие группы сливаем</a> · <a href='/base/skripty_v3'>скрипты</a> · <a href='/base'>вся база</a></div>
<h2 style='margin-top:26px'>Справочные списки — для Бориса и на потом</h2>
<p style='margin:-4px 0 10px;color:#6c6a86;font-size:13.5px'>Админам сюда ходить не нужно: всё, что нужно сделать сегодня, уже стоит в колонке выше.</p>
{_safe(_nikogda_ne_zvonili, day, imya='кому не звонили')}
<div id='inbox'></div>{_safe(_inbox_block, day, imya='обещания за день')}
{_safe(zayavki.block, imya='заявки сезона')}
{_safe(mesta.block, imya='свободные места')}
<p style='color:#6c6a86;font-size:12px'>Пульт собран сервером {now_msk.strftime('%H:%M')} МСК · версия {ver}</p><script>
// 21.09. У дела два исхода, а не один. Галочка — «сделала», и если дело про
// один телефон, сервер проверяет, стоит ли за ней разговор от 30 секунд или
// отправленное сообщение; нет — спрашиваем словами, что произошло. Вторая
// кнопка — «не дозвонилась»: попытка записывается, дело остаётся.
function pultDone(box, kind, id, note){{
  var url = kind === 'task' ? '/api/pult/done' : '/api/plan/inbox/done';
  var body = kind === 'task' ? {{id:id, state: box.checked ? 1 : 0, note: note || ''}}
                             : {{id:id, done: box.checked, note: note || ''}};
  fetch(url, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(body)}})
   .then(function(r){{ return r.json(); }})
   .then(function(res){{
     if (res && res.ok === false) {{ box.checked = false; alert('Не сохранилось: ' + (res.почему || '')); return; }}
     location.reload();
   }})
   .catch(function(){{ box.checked = !box.checked; alert('Не сохранилось, попробуйте ещё раз'); }});
}}
function pultMiss(kind, id){{
  fetch('/api/pult/nedozvon', {{method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{kind:kind, id:id}})}})
   .then(function(r){{ return r.json(); }})
   .then(function(res){{ if (res && res.совет) {{ alert(res.совет); }} location.reload(); }});
}}
</script><script>(function(){{var off={int(now_msk.utcoffset().total_seconds())}*1000;function t(){{var d=new Date(Date.now()+off);document.getElementById('clock').textContent=('0'+d.getUTCHours()).slice(-2)+':'+('0'+d.getUTCMinutes()).slice(-2)}};t();setInterval(t,15000)}})();</script>
</div></body></html>"""


def promises_page(day: str, who: str) -> str:
    """/obeshchaniya?who=Лиза — обещания клиентам одного человека, крупно, под телефон.
    08.09: Лиза живёт в Wazzup и колонку пульта не открывает; ей нужен список
    длиной в экран с галочкой и кнопкой позвонить/написать."""
    day = day or today()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, ts, who, text, phone, source, done FROM plan_inbox WHERE day=? AND who=? ORDER BY done, id",
                            (day, who)).fetchall()
    c = COLOR.get(who, "#312783")
    lis = []
    for r in rows:
        ph = "".join(ch for ch in (r["phone"] or "") if ch.isdigit())
        links = ""
        if len(ph) >= 10:
            _t = _tel(ph)
            links = ((f" <a href='tel:+{_t}' style='color:{c};font-weight:700;white-space:nowrap'>📞 +{_t}</a>"
                      f" <a href='https://wa.me/{_t}' style='color:#25D366;font-weight:700'>WA</a>")
                     if _t else "")
        txt = html.escape(r["text"] or "")
        head, sep, tail = txt.partition(" — ")
        if not sep or len(head) > 120:
            head, tail = txt[:110], txt[110:]
        body = f"<b>{head}</b>" + (f"<details style='display:inline'><summary style='display:inline;cursor:pointer;color:#6c6a86'> …</summary><span> {tail}</span></details>" if tail else "")
        lis.append(f"<li style='margin:10px 0;padding:10px 12px;background:#fff;border:1px solid #e4e2f0;border-radius:12px;{'opacity:.45;text-decoration:line-through' if r['done'] else ''}'>"
                   f"<label style='display:flex;gap:10px;align-items:flex-start'><input type='checkbox' {'checked' if r['done'] else ''} style='width:22px;height:22px;flex:none;margin-top:2px' "
                   f"onchange=\"inboxDone(this,{r['id']})\">"
                   f"<span><span style='color:#6c6a86;font-size:12px'>{html.escape((r['source'] or '')[:24])} · {html.escape((r['ts'] or '')[11:16])}</span><br>{body}{links}</span></label></li>")
    open_n = sum(1 for r in rows if not r["done"])
    status = (f"Не закрыто: <b style='color:#E30613'>{open_n}</b>. Сверху вниз, галочка сразу после действия." if open_n
              else "<b style='color:#4e8a12'>Всё закрыто</b> — спасибо.")
    body_ul = "".join(lis) or "<li style='color:#6c6a86'>пока пусто</li>"
    return f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'><title>Обещания клиентам · {html.escape(who)} · {day}</title>
<meta name='viewport' content='width=device-width,initial-scale=1'><meta http-equiv='refresh' content='300'>
<style>body{{margin:0;background:#f8f7fc;color:#15132e;font:17px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}}.wrap{{max-width:680px;margin:0 auto;padding:14px 12px 60px}}
h1{{font-size:20px;margin:0 0 4px;color:{c}}}p{{margin:0 0 10px;color:#6c6a86;font-size:14px}}ul{{list-style:none;padding:0;margin:0}}a{{text-decoration:none}}</style></head><body><div class='wrap'>
<h1>{html.escape(who)}: обещания клиентам · {day[8:]}.{day[5:7]}</h1>
<p>{status} · <a href='/pult?who={html.escape(who)}' style='color:{c}'>моя колонка на пульте</a></p>
<ul>{body_ul}</ul></div>
<script>function inboxDone(b,i){{
  fetch('/api/plan/inbox/done',{{method:'POST',headers:{{'Content-Type':'application/json'}},
        body:JSON.stringify({{id:i,done:b.checked}})}})
   .then(function(r){{if(!r.ok)throw 0;return r.json()}})
   .then(function(x){{if(x&&x.ok===false){{b.checked=!b.checked;alert('Не сохранилось');return}}location.reload()}})
   .catch(function(){{b.checked=!b.checked;alert('Не сохранилось — проверьте связь и нажмите ещё раз')}});
}}</script></body></html>"""
