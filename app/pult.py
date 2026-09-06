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
        rows = conn.execute("SELECT id, who, t, text, done FROM pult_tasks WHERE day=? ORDER BY who, ord, id",
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


def mark(task_id: int, flag: bool) -> bool:
    with db.get_conn() as conn:
        _init(conn)
        cur = conn.execute("UPDATE pult_tasks SET done=? WHERE id=?", (1 if flag else 0, int(task_id)))
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
        try:
            ib = conn.execute("SELECT COUNT(*), COALESCE(SUM(done),0) FROM plan_inbox WHERE day=?", (day,)).fetchone()
        except Exception:
            ib = (0, 0)
        tk = conn.execute("SELECT COUNT(*), COALESCE(SUM(done),0) FROM pult_tasks WHERE day=?", (day,)).fetchone() \
            if conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='pult_tasks'").fetchone()[0] else (0, 0)
    return {"pays": pays[0], "pays_sum": int(pays[1]), "joins_new": joins_new, "lessons": len(lids), "kids": kids,
            "firsts": firsts, "inbox": ib[0], "inbox_done": ib[1], "tasks": tk[0], "tasks_done": tk[1]}


def _col(who: str, items: list[dict], onduty: bool) -> str:
    c = COLOR.get(who, "#6c6a86")
    done_n = sum(1 for i in items if i["done"])
    lis = []
    for it in items:
        st = "opacity:.45;text-decoration:line-through" if it["done"] else ""
        lis.append(
            f"<li style='margin:7px 0;{st}'><label style='display:flex;gap:8px;align-items:flex-start;cursor:pointer'>"
            f"<input type='checkbox' {'checked' if it['done'] else ''} style='margin-top:4px;width:18px;height:18px;flex:none' "
            f"onchange=\"fetch('/api/pult/done',{{method:'POST',headers:{{'Content-Type':'application/json'}},"
            f"body:JSON.stringify({{id:{it['id']},done:this.checked}})}}).then(()=>location.reload())\">"
            f"<span>" + (f"<b>{html.escape(it['t'])}</b> — " if it["t"] else "") + f"{it['text']}</span></label></li>")
    body = "".join(lis) or "<li style='color:#6c6a86'>задач пока нет — Клод положит к началу смены</li>"
    badge = "" if onduty else " <span style='font-size:11px;color:#6c6a86;font-weight:500'>не в смене</span>"
    return (f"<div class='wcard' style='border-top-color:{c}'><div class='nm'>{html.escape(who)}{badge} "
            f"<span style='font-size:12px;color:#6c6a86;font-weight:600'>{done_n}/{len(items)}</span></div>"
            f"<div class='rl'>{html.escape(ROLE.get(who, ''))} · сверху вниз, галочка после каждого шага</div>"
            f"<ol class='small' style='list-style:none;padding:0;margin:0'>{body}</ol></div>")


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
    cols = "".join(_col(w, tk.get(w, []), w in on or w in ("Лиза", "Борис")) for w in order)
    if extra:
        cols += "".join(_col(w, tk.get(w, []), False) for w in extra)
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
        (f"{k['inbox_done']}/{k['inbox']}", "инбокс: сделано / всего"),
        (f"{k['tasks_done']}/{k['tasks']}", "задач смены сделано"),
    ]
    kpi_html = "".join(f"<div class='k'><b>{a}</b><span>{b}</span></div>" for a, b in kpis)
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
<div class='hero'><h1>Пульт · {wd}, {d.day:02d}.{d.month:02d} · в смене: {html.escape(hero_who)}</h1>
<p>Одна ссылка для всех: задачи по колонкам — только тем, кто сегодня работает, плюс Лиза и Борис. Галочка видна всем. Обновляется сама каждые 5 минут. Своя колонка: {filt}
{"<a href='/pult'>все</a>" if who else ""}</p></div>
<div class='kpis'>{kpi_html}</div>
<div class='who'>{cols}</div>
<div class='links card'><b>Подробные списки дня:</b> <a href='/base/{slug}'>план и списки семей</a> · <a href='/spiski'>списки занятий</a> · <a href='/karta'>карта развития</a> · <a href='/base/gruppy_reshenia'>куда зовём, какие группы сливаем</a> · <a href='/base/skripty_v3'>скрипты</a> · <a href='/base'>вся база</a></div>
{_inbox_block(day)}
{zayavki.block()}
{mesta.block()}
<p style='color:#6c6a86;font-size:12px'>Пульт собран сервером {datetime.now().strftime('%H:%M')} · версия {ver}</p>
</div></body></html>"""
