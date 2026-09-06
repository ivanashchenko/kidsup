"""Страница владельца /boris — живой список решений и дел Бориса.

06.09 Борис: «приоритизируй по важности и срочности, обоснуй; сделай динамической
и обновляемой; максимально удобной и практичной для меня».

Задачи живут в таблице boris_tasks (Клод кладёт и правит через POST /api/boris/tasks
по ключу key), статус ставит Борис на странице: сделано / ждём ответа / отложить,
с заметкой. Раскладка — матрица важность × срочность (Эйзенхауэр):
  Q1 важно и срочно — сегодня;  Q2 важно, не срочно — спланировать;
  Q3 срочно, мелкое — за 5 минут;  Q4 остальное — позже.
Сверху — живые цифры сезона из CRM (те же, что на пульте) и прогресс к 311.
Только чтение CRM и своя таблица; никаких отправок.
"""
from __future__ import annotations

import html
from datetime import datetime

from . import db

LEVERS = {"leads": "Лиды и реклама", "show": "Доходимость и смена", "pay": "Конверсия в оплату",
          "avg": "Чек и расписание", "keep": "Удержание и допродажи", "money": "Деньги и гигиена"}
GOAL_PAID = 311


def _init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS boris_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, title TEXT, detail TEXT, lever TEXT,
        imp INTEGER DEFAULT 2, urg INTEGER DEFAULT 2, deadline TEXT, minutes INTEGER, benefit TEXT, why TEXT,
        status TEXT DEFAULT 'open', note TEXT, ts TEXT, updated TEXT)""")


def upsert(items: list[dict]) -> int:
    """Кладёт/обновляет задачи по key. Статус и заметку Бориса не трогает."""
    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    with db.get_conn() as conn:
        _init(conn)
        for it in items:
            key = str(it.get("key") or "").strip()
            if not key:
                continue
            row = conn.execute("SELECT id FROM boris_tasks WHERE key=?", (key,)).fetchone()
            vals = (it.get("title", ""), it.get("detail", ""), it.get("lever", ""), int(it.get("imp", 2)),
                    int(it.get("urg", 2)), it.get("deadline", ""), int(it.get("minutes") or 0),
                    it.get("benefit", ""), it.get("why", ""), now)
            if row:
                conn.execute("UPDATE boris_tasks SET title=?,detail=?,lever=?,imp=?,urg=?,deadline=?,minutes=?,benefit=?,why=?,updated=? WHERE key=?",
                             vals + (key,))
            else:
                conn.execute("INSERT INTO boris_tasks (key,title,detail,lever,imp,urg,deadline,minutes,benefit,why,status,note,ts,updated) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?, 'open','',?,?)", (key,) + vals[:9] + (now, now))
            n += 1
    return n


def set_status(task_id: int, status: str, note: str = "") -> bool:
    if status not in ("open", "done", "waiting", "skip"):
        return False
    with db.get_conn() as conn:
        _init(conn)
        cur = conn.execute("UPDATE boris_tasks SET status=?, note=?, updated=? WHERE id=?",
                           (status, (note or "")[:300], datetime.now().isoformat(timespec="seconds"), int(task_id)))
        return cur.rowcount > 0


def tasks() -> list[dict]:
    with db.get_conn() as conn:
        _init(conn)
        rows = conn.execute("SELECT * FROM boris_tasks ORDER BY urg DESC, imp DESC, id").fetchall()
    return [dict(r) for r in rows]


def quadrant(t: dict) -> str:
    if t["imp"] >= 3 and t["urg"] >= 3:
        return "q1"
    if t["imp"] >= 3:
        return "q2"
    if t["urg"] >= 3:
        return "q3"
    return "q4"


Q_TITLE = {
    "q1": ("Сегодня — важно и срочно", "#E30613", "Каждый день без этого стоит денег или ломает работу смены. Делать первым, в этом порядке."),
    "q2": ("Важно, не горит — спланировать до 13.09 / 30.09", "#312783", "Двигает прибыль сильно, но эффект отложен. Поставить в календарь, не решать между звонками."),
    "q3": ("Срочно, но мелкое — по 1–5 минут", "#F59C00", "Не меняет стратегию, но пока не сделано — кто-то ждёт или что-то ломается."),
    "q4": ("Позже — когда закрыты первые три блока", "#6c6a86", "Полезно, но не в сентябре: ресурс сейчас на набор."),
}


def _btn(tid: int, status: str, label: str, color: str, ask: bool = False) -> str:
    prompt = "prompt('Заметка (можно пусто)')" if ask else "''"
    js = ("var r=" + prompt + ";if(r===null)return false;"
          "fetch('/api/boris/status',{method:'POST',headers:{'Content-Type':'application/json'},"
          "body:JSON.stringify({id:" + str(tid) + ",status:'" + status + "',note:r})}).then(()=>location.reload());return false")
    return "<a href='#' onclick=\"" + js + "\" style='font-size:12px;color:" + color + ";white-space:nowrap;margin-right:8px'>" + label + "</a>"


def _item(t: dict) -> str:
    st = t["status"]
    dim = "opacity:.5;text-decoration:line-through" if st in ("done", "skip") else ""
    wait = "<span style='display:inline-block;font-size:11px;font-weight:700;color:#fff;background:#1DA7E0;border-radius:6px;padding:1px 7px;margin-right:6px'>ждём ответа</span>" if st == "waiting" else ""
    dl = f"<span style='display:inline-block;font-size:11px;font-weight:700;color:#a35f00;background:#fff1d6;border-radius:6px;padding:1px 7px;margin-right:6px'>{html.escape(t['deadline'])}</span>" if t["deadline"] else ""
    mins = f"<span style='font-size:12px;color:#6c6a86'>· {t['minutes']} мин</span>" if t["minutes"] else ""
    lever = f"<span style='font-size:11px;color:#6c6a86;text-transform:uppercase;letter-spacing:.04em'>{html.escape(LEVERS.get(t['lever'], t['lever']))}</span>"
    note = f"<div style='font-size:13px;color:#0c6a94;margin-top:4px'>Заметка: {html.escape(t['note'])}</div>" if t.get("note") else ""
    ctrls = ""
    if st in ("done", "skip"):
        ctrls = _btn(t["id"], "open", "вернуть", "#6c6a86")
    else:
        ctrls = (_btn(t["id"], "done", "✓ сделано", "#4e8a12") + _btn(t["id"], "waiting", "ждём ответа", "#0c6a94", True)
                 + _btn(t["id"], "skip", "не делаем", "#6c6a86", True))
    return (f"<div class='card' style='margin:8px 0;{dim}'>"
            f"<div style='display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap'><div>{dl}{wait}<b>{t['title']}</b> {mins}</div><div>{lever}</div></div>"
            f"<div style='font-size:14px;margin-top:4px'>{t['detail']}</div>"
            + (f"<div style='font-size:14px;margin-top:4px'><b>Польза:</b> {t['benefit']}</div>" if t["benefit"] else "")
            + (f"<details style='font-size:13px;color:#6c6a86;margin-top:4px'><summary style='cursor:pointer'>почему такой приоритет</summary>{t['why']}</details>" if t["why"] else "")
            + note + f"<div style='margin-top:6px'>{ctrls}</div></div>")


def page() -> str:
    from . import pult, mesta
    ts = tasks()
    day = pult.today()
    k = pult.kpi(day)
    try:
        rows = mesta.rows()
        live = sum(r["live"] for r in rows); paid = sum(r["paid"] for r in rows)
        free = sum(r["free"] for r in rows if not r["merge"]); cap = sum(r["cap"] for r in rows)
    except Exception:
        live = paid = free = cap = 0
    open_t = [t for t in ts if t["status"] in ("open", "waiting")]
    done_t = [t for t in ts if t["status"] in ("done", "skip")]
    pct = int(100 * paid / GOAL_PAID) if GOAL_PAID else 0
    secs = []
    for q in ("q1", "q2", "q3", "q4"):
        items = [t for t in open_t if quadrant(t) == q]
        title, color, expl = Q_TITLE[q]
        if not items:
            continue
        secs.append(f"<h2 style='color:{color};border-color:{color}'>{title} <span style='font-size:14px;color:#6c6a86;font-weight:600'>{len(items)}</span></h2>"
                    f"<p class='small'>{expl}</p>" + "".join(_item(t) for t in items))
    done_html = ""
    if done_t:
        done_html = (f"<details style='margin-top:20px'><summary style='cursor:pointer;font-weight:800;color:#6c6a86'>Сделано и отложено — {len(done_t)}</summary>"
                     + "".join(_item(t) for t in done_t) + "</details>")
    now = datetime.now().strftime("%H:%M")
    return f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'><title>Борису · решения и дела</title>
<meta name='viewport' content='width=device-width,initial-scale=1'><meta http-equiv='refresh' content='300'>
<style>
:root{{--ink:#15132e;--muted:#6c6a86;--line:#e4e2f0;--bg:#f8f7fc;--card:#fff;--indigo:#312783}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}}
.wrap{{max-width:900px;margin:0 auto;padding:0 14px 60px}}
.hero{{background:linear-gradient(135deg,#312783,#1DA7E0);color:#fff;margin:0 -14px 14px;padding:18px 18px 16px;border-radius:0 0 18px 18px}}
.hero h1{{margin:0 0 4px;font-size:22px}}.hero p{{margin:0;opacity:.92;font-size:14px}}.hero a{{color:#fff}}
.kpis{{display:grid;gap:8px;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));margin:12px 0}}
.k{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px}}.k b{{display:block;font-size:22px;color:var(--indigo);line-height:1.1}}.k span{{font-size:12px;color:var(--muted)}}
.bar{{height:10px;background:#e4e2f0;border-radius:99px;overflow:hidden;margin:6px 0 2px}}.bar i{{display:block;height:100%;background:linear-gradient(90deg,#1DA7E0,#7DB928)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}}
h2{{font-size:18px;margin:22px 0 4px;border-bottom:2px solid var(--line);padding-bottom:4px}}.small{{font-size:13px;color:var(--muted);margin:0 0 8px}}
</style></head><body><div class='wrap'>
<div class='hero'><h1>Борису · решения и дела · {now} МСК</h1>
<p>Живой список: Клод добавляет и переоценивает пункты, вы ставите «сделано» или «ждём ответа» — статус виден мне сразу. Раскладка по важности и срочности, у каждого пункта польза в деньгах и «почему такой приоритет». Обновляется сама каждые 5 минут. Полная история 1.08–6.09 — <a href='/base/boris_itog'>здесь</a>, пульт смены — <a href='/pult'>/pult</a>.</p></div>
<div class='card' style='margin:10px 0'><b>Цель сезона: {GOAL_PAID} оплаченных к 30.09.</b> Сейчас {paid} — {pct}%.<div class='bar'><i style='width:{min(pct,100)}%'></i></div>
<span class='small'>живых записей {live} из {cap} мест · свободно {free} (без сливаемых групп) · сегодня оплат {k['pays']} на {str(k['pays_sum']).replace(',', ' ')} ₽ · новых записей {k['joins_new']} · первых занятий {k['firsts']}</span></div>
<div class='kpis'><div class='k'><b>{len([t for t in open_t if quadrant(t)=='q1'])}</b><span>важно и срочно</span></div><div class='k'><b>{len([t for t in open_t if t['status']=='waiting'])}</b><span>ждём ответа</span></div><div class='k'><b>{len(done_t)}</b><span>сделано / отложено</span></div><div class='k'><b>{sum(t['minutes'] for t in open_t if quadrant(t)=='q1')} мин</b><span>ваше время на блок «сегодня»</span></div></div>
{"".join(secs)}
{done_html}
</div></body></html>"""
