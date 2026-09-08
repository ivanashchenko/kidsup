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
  • ссылки на подробные списки дня (plan_ДДмес), /spiski, /karta.

?who=Аня — только одна колонка (для вкладки самого админа).
Никаких отправок — только чтение CRM и своя таблица задач.
"""
from __future__ import annotations

import html
import json
from datetime import datetime

from . import db

SHORT = {"Анна Инкина": "Аня", "Елена Кузнецова": "Лена", "Ирина Головина": "Ира"}
COLOR = {"Лена": "#7DB928", "Аня": "#F59C00", "Ира": "#E30613", "Лиза": "#1DA7E0", "Борис": "#312783"}
ROLE = {"Аня": "телефон, деньги, пробные", "Ира": "дверь, явка, база, CRM", "Лена": "дожим, оплаты, разговор на выходе",
        "Лиза": "переписка", "Борис": "решения"}
MON = {1: "yan", 2: "fev", 3: "mar", 4: "apr", 5: "may", 6: "iyn", 7: "iyl", 8: "avg", 9: "sen", 10: "okt", 11: "noy", 12: "dek"}
LIVE_JOIN = (2, 58132, 83760, 58131)


def _init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS pult_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, who TEXT, t TEXT, text TEXT,
        done INTEGER DEFAULT 0, ord INTEGER DEFAULT 0, ts TEXT)""")
    try:
        conn.execute("ALTER TABLE pult_tasks ADD COLUMN note TEXT")
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
        rows = conn.execute("SELECT id, who, t, text, done, note FROM pult_tasks WHERE day=? ORDER BY who, ord, id",
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
        n = 0
        for i, it in enumerate(items, 1):
            text = str(it.get("text") or "").strip()
            if not text:
                continue
            conn.execute("INSERT INTO pult_tasks (day, who, t, text, done, ord, ts) VALUES (?,?,?,?,?,?,?)",
                         (day, who, str(it.get("t") or ""), text, 1 if text in done_texts else 0, base + i, now))
            n += 1
        return n


def mark(task_id: int, state: int, note: str = "") -> bool:
    """state: 0 — не сделано, 1 — сделано, 2 — «перенести на завтра».

    08.09: кнопку «не успела» с вопросом «почему» за три дня не нажали ни разу —
    пункты просто висели. Теперь одна кнопка без вопросов: пункт помечается
    перенесённым и СРАЗУ копируется в колонку того же человека на завтра
    (без дублей, если нажать дважды)."""
    from datetime import date, timedelta
    with db.get_conn() as conn:
        _init(conn)
        row = conn.execute("SELECT day, who, t, text FROM pult_tasks WHERE id=?", (int(task_id),)).fetchone()
        cur = conn.execute("UPDATE pult_tasks SET done=?, note=? WHERE id=?",
                           (int(state), (note or "")[:200], int(task_id)))
        if int(state) == 2 and row:
            nxt = (date.fromisoformat(row["day"]) + timedelta(days=1)).isoformat()
            text = row["text"] if row["text"].startswith("↩") else "↩ <b>перенос со вчера</b> — " + row["text"]
            dup = conn.execute("SELECT 1 FROM pult_tasks WHERE day=? AND who=? AND text=?", (nxt, row["who"], text)).fetchone()
            if not dup:
                conn.execute("INSERT INTO pult_tasks (day, who, t, text, done, note) VALUES (?,?,?,?,0,'')",
                             (nxt, row["who"], row["t"], text))
        return cur.rowcount > 0


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
        try:
            ib = conn.execute("SELECT COUNT(*), COALESCE(SUM(done),0) FROM plan_inbox WHERE day=?", (day,)).fetchone()
        except Exception:
            ib = (0, 0)
        tk = conn.execute("SELECT COUNT(*), COALESCE(SUM(done),0) FROM pult_tasks WHERE day=?", (day,)).fetchone() \
            if conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='pult_tasks'").fetchone()[0] else (0, 0)
    return {"pays": pays[0], "pays_sum": int(pays[1]), "joins_new": joins_new, "lessons": len(lids), "kids": kids,
            "firsts": firsts, "visits": visits, "y_kids": y_kids, "y_visits": y_visits, "inbox": ib[0], "inbox_done": ib[1], "tasks": tk[0], "tasks_done": tk[1]}


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


def _promises_html(day: str, who: str, color: str) -> str:
    """Обещания клиентам этого человека — прямо в его колонке, под задачами.
    08.09 Борис: «всё на одной странице», отдельной страницы под телефон не нужно."""
    try:
        with db.get_conn() as conn:
            rows = conn.execute("SELECT id, ts, text, phone, source, done FROM plan_inbox WHERE day=? AND who=? ORDER BY done, id",
                                (day, who)).fetchall()
    except Exception:
        rows = []
    if not rows:
        return ""
    lis = []
    for r in rows:
        ph = "".join(ch for ch in (r["phone"] or "") if ch.isdigit())
        tel = f" <a href='tel:+{ph}' style='color:#6c6a86;white-space:nowrap'>+{ph}</a>" if len(ph) >= 10 else ""
        txt = html.escape(r["text"] or "")
        short = txt if len(txt) <= 150 else f"{txt[:150]}<details style='display:inline'><summary style='display:inline;cursor:pointer;color:#6c6a86'> …</summary> {txt[150:]}</details>"
        lis.append(f"<li style='margin:6px 0;{'opacity:.45;text-decoration:line-through' if r['done'] else ''}'><label style='display:flex;gap:8px;align-items:flex-start;cursor:pointer'>"
                   f"<input type='checkbox' {'checked' if r['done'] else ''} style='margin-top:4px;width:18px;height:18px;flex:none' "
                   f"onchange=\"fetch('/api/plan/inbox/done',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{id:{r['id']},done:this.checked}})}}).then(()=>location.reload())\">"
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


def _col(who: str, items: list[dict], onduty: bool, day: str = "", now_hm: str = "") -> str:
    """Метка СЕЙЧАС идёт за часами: текущий — несделанный пункт с самым поздним временем,
    которое уже наступило (мы внутри его окна). Несделанные пункты с более ранним временем —
    «время прошло»: делаются сразу после текущего. Пункты без времени («весь день»,
    «после каждого занятия») — фоновые, без метки. Будущие — приглушены."""
    c = COLOR.get(who, "#6c6a86")
    done_n = sum(1 for i in items if i["done"] == 1)
    open_items = [i for i in items if not i["done"]]
    cur_id = None
    if open_items:
        timed = [(i, _first_time(i["t"])) for i in open_items if _first_time(i["t"])]
        started = [(i, ft) for i, ft in timed if not now_hm or ft <= now_hm]
        if started:
            cur_id = max(started, key=lambda x: x[1])[0]["id"]
        else:
            cur_id = open_items[0]["id"]
    cur_ft = next((_first_time(i["t"]) for i in items if i["id"] == cur_id), "")
    lis = []
    for it in items:
        st, badge, extra = "", "", ""
        ft = _first_time(it["t"])
        if it["done"] == 1:
            st = "opacity:.45;text-decoration:line-through"
        elif it["done"] == 2:
            st = "opacity:.7"
            badge = "<span style='display:inline-block;font-size:11px;font-weight:700;color:#fff;background:#a35f00;border-radius:6px;padding:1px 7px;margin-right:6px;vertical-align:middle'>перенесено на завтра</span>"
            if it.get("note") and it["note"] != "перенос":
                extra = f" <span style='color:#a35f00;font-size:12.5px'>— {html.escape(it['note'])}</span>"
        elif it["id"] == cur_id:
            st = f"background:#f1effb;border-left:4px solid {c};border-radius:8px;padding:6px 8px;margin-left:-8px"
            badge = f"<span style='display:inline-block;font-size:11px;font-weight:800;color:#fff;background:{c};border-radius:6px;padding:1px 7px;margin-right:6px;vertical-align:middle'>СЕЙЧАС</span>"
        elif ft and cur_ft and ft < cur_ft:
            badge = "<span style='display:inline-block;font-size:11px;font-weight:700;color:#a35f00;background:#fff1d6;border-radius:6px;padding:1px 7px;margin-right:6px;vertical-align:middle'>время прошло — сразу после СЕЙЧАС</span>"
        elif not ft:
            badge = "<span style='display:inline-block;font-size:11px;font-weight:700;color:#0c6a94;background:#e6f4fb;border-radius:6px;padding:1px 7px;margin-right:6px;vertical-align:middle'>фон</span>"
        else:
            st = "opacity:.7"
        ctrl = ""
        if not it["done"]:
            ctrl = (f" <a href='#' style='font-size:12px;color:#a35f00;white-space:nowrap' title='Пункт уйдёт в твою колонку на завтра' onclick=\""
                    f"fetch('/api/pult/done',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{id:{it['id']},state:2,note:'перенос'}})}}).then(()=>location.reload());return false\">перенести на завтра ↩</a>")
        lis.append((it["done"] == 1, it["done"] == 0,
            f"<li style='margin:7px 0;{st}'><label style='display:flex;gap:8px;align-items:flex-start;cursor:pointer'>"
            f"<input type='checkbox' {'checked' if it['done'] == 1 else ''} style='margin-top:4px;width:18px;height:18px;flex:none' "
            f"onchange=\"fetch('/api/pult/done',{{method:'POST',headers:{{'Content-Type':'application/json'}},"
            f"body:JSON.stringify({{id:{it['id']},state:this.checked?1:0}})}}).then(()=>location.reload())\">"
            f"<span>{badge}" + (f"<b>{html.escape(it['t'])}</b> — " if it["t"] else "") + f"{it['text']}{extra}{ctrl}</span></label></li>"))
    # 08.09: по 10–12 пунктов в колонке не читаются. Показываем 5 первых несделанных,
    # остальные несделанные и все сделанные — свёрнуто.
    LIMIT = 5
    done_html = "".join(h for d_, o_, h in lis if d_)
    open_html = [h for d_, o_, h in lis if o_]
    moved_html = "".join(h for d_, o_, h in lis if not d_ and not o_)
    body = "".join(open_html[:LIMIT])
    if len(open_html) > LIMIT:
        body += (f"<details style='margin:6px 0'><summary style='cursor:pointer;color:#6c6a86;font-size:13px'>ещё {len(open_html) - LIMIT} на день — после этих пяти</summary>"
                 f"<ol style='list-style:none;padding:0;margin:0'>{''.join(open_html[LIMIT:])}</ol></details>")
    if moved_html:
        body += f"<details style='margin:6px 0'><summary style='cursor:pointer;color:#a35f00;font-size:13px'>перенесено на завтра</summary><ol style='list-style:none;padding:0;margin:0'>{moved_html}</ol></details>"
    if done_html:
        body += f"<details style='margin:6px 0'><summary style='cursor:pointer;color:#4e8a12;font-size:13px'>сделано {done_n} ✓</summary><ol style='list-style:none;padding:0;margin:0'>{done_html}</ol></details>"
    body = body or "<li style='color:#6c6a86'>задач пока нет — Клод положит к началу смены</li>"
    nb = "" if onduty else " <span style='font-size:11px;color:#6c6a86;font-weight:500'>не в смене</span>"
    ib = _inbox_open(day, who) if day else 0
    ib_html = (f"<span style='font-size:12.5px;color:#E30613;font-weight:700'>обещаний клиентам: {ib} — ниже в колонке ↓</span>"
               if ib else "<span style='font-size:12.5px;color:#7DB928;font-weight:700'>обещаний клиентам нет</span>")
    late = sum(1 for i in items if not i["done"] and _first_time(i["t"]) and cur_ft and _first_time(i["t"]) < cur_ft)
    late_html = f" · <span style='color:#a35f00;font-weight:700'>{late} пункт(а) без галочки, время прошло</span>" if late else ""
    return (f"<div class='wcard' style='border-top-color:{c}'><div class='nm'>{html.escape(who)}{nb} "
            f"<span style='font-size:12px;color:#6c6a86;font-weight:600'>{done_n}/{len(items)}</span></div>"
            f"<div class='rl'>{html.escape(ROLE.get(who, ''))} · {ib_html}{late_html}</div>"
            f"<ol class='small' style='list-style:none;padding:0;margin:0'>{body}</ol>{_promises_html(day, who, c) if day else ''}</div>")


HOWTO = ("<div class='card' style='border-left:4px solid #312783;margin:10px 0;font-size:15px'>"
         "<b>Как пользоваться — три шага.</b> "
         "<b>1.</b> Делай пункт с меткой <span style='background:#312783;color:#fff;border-radius:6px;padding:1px 7px;font-size:12px;font-weight:800'>СЕЙЧАС</span> — она идёт за часами. В колонке видны пять ближайших, остальное свёрнуто ниже. "
         "<b>2.</b> Сделала — галочка сразу, не вечером. Не успеваешь — «перенести на завтра ↩», пункт сам появится в твоей колонке завтра. "
         "<b>3.</b> Ниже задач в твоей колонке — <b>«Обещания клиентам»</b>: семьи, которым мы что-то обещали в звонке или переписке. Закрываются сверху вниз раз в час, галочка сразу после действия. "
         "<details style='margin-top:6px;font-size:13.5px'><summary style='cursor:pointer;color:#6c6a86'>подробнее: время прошло, фон, явка</summary>"
         "<ul style='margin:6px 0 0;padding-left:20px'>"
         "<li><b>«время прошло»</b> — пункт со временем, который не сделан и не отмечен. Делается сразу после СЕЙЧАС, не пропускается; если уже бессмыслен — «перенести на завтра».</li>"
         "<li><b>«фон»</b> — пункты без времени: карта развития после каждого занятия, ответ в чате за 30 минут. Делаются между строками весь день.</li>"
         "<li><b>Явка</b> — отмечает Лиза вечером по спискам педагогов (все занятия дня, флажок «пробное» у первых). Плитка сверху показывает вчера и сегодня; красная — вчерашний день не закрыт. Дежурная отмечает только первые занятия сразу после них — от этого зависит карта развития и оплата на выходе.</li>"
         "<li><b>Итог дня</b> складывается сам из галочек и переносов: отдельный отчёт писать не нужно.</li>"
         "</ul></details></div>")


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
    extra = [w for w in tk if w not in order]           # задачи есть, а в смене нет — показываем свёрнуто внизу
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
        (f"{k['inbox_done']}/{k['inbox']}", "обещаний клиентам закрыто / всего"),
        (f"{k['tasks_done']}/{k['tasks']}", "задач смены сделано"),
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
<div class='links card'><b>Подробные списки дня:</b> <a href='/base/{slug}'>план и списки семей</a> · <a href='/spiski'>списки занятий</a> · <a href='/karta'>карта развития</a> · <a href='/base/gruppy_reshenia'>куда зовём, какие группы сливаем</a> · <a href='/base/skripty_v3'>скрипты</a> · <a href='/base'>вся база</a></div>
<div id='inbox'></div>{_inbox_block(day)}
{zayavki.block()}
{mesta.block()}
<p style='color:#6c6a86;font-size:12px'>Пульт собран сервером {now_msk.strftime('%H:%M')} МСК · версия {ver}</p><script>(function(){{var off={int(now_msk.utcoffset().total_seconds())}*1000;function t(){{var d=new Date(Date.now()+off);document.getElementById('clock').textContent=('0'+d.getUTCHours()).slice(-2)+':'+('0'+d.getUTCMinutes()).slice(-2)}};t();setInterval(t,15000)}})();</script>
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
            links = (f" <a href='tel:+{ph}' style='color:{c};font-weight:700;white-space:nowrap'>📞 +{ph}</a>"
                     f" <a href='https://wa.me/{ph}' style='color:#25D366;font-weight:700'>WA</a>")
        txt = html.escape(r["text"] or "")
        head, sep, tail = txt.partition(" — ")
        if not sep or len(head) > 120:
            head, tail = txt[:110], txt[110:]
        body = f"<b>{head}</b>" + (f"<details style='display:inline'><summary style='display:inline;cursor:pointer;color:#6c6a86'> …</summary><span> {tail}</span></details>" if tail else "")
        lis.append(f"<li style='margin:10px 0;padding:10px 12px;background:#fff;border:1px solid #e4e2f0;border-radius:12px;{'opacity:.45;text-decoration:line-through' if r['done'] else ''}'>"
                   f"<label style='display:flex;gap:10px;align-items:flex-start'><input type='checkbox' {'checked' if r['done'] else ''} style='width:22px;height:22px;flex:none;margin-top:2px' "
                   f"onchange=\"fetch('/api/plan/inbox/done',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{id:{r['id']},done:this.checked}})}}).then(()=>location.reload())\">"
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
<ul>{body_ul}</ul></div></body></html>"""
