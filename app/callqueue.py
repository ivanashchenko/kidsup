"""Единая очередь дня: страница обзвона, собранная ИЗ ЗАДАЧ.

Зачем. До 22.08 работа шла по трём спискам сразу: задачи в МойКласс
(73 номера), страницы обзвона (386) и лист промоутера, который
администратор вёл сам. Пересечение первых двух — 32 номера из 73,
а 35 из 54 обзвоненных за смену семей не значились ни в одном списке.
Отчитываться при этом надо по задачам — и 23 из 37 закрытых задач
оказались без следа звонка. Это не приписки: звонили другим людям,
по другому списку.

Как здесь. Строка страницы — это и есть задача. Никакого второго списка
не существует: что видно на странице, то и висит в CRM. Отметка результата
закрывает задачу и пишет комментарий в карточку, то есть создаёт то самое
касание, отсутствие которого раньше и было проблемой.

Результат — не галочка, а выбор из четырёх:
    записан        — закрываем, работа сделана
    перезвонить    — переносим на следующую смену этого же человека
    не актуально   — закрываем, в карточке остаётся причина
    не дозвонились — переносим на другое время дня, считаем попытки

Запуск:
    python -m app.callqueue        — показать очередь дежурного
"""

from __future__ import annotations

import html as H
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import db, sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.callqueue")

STAFF = {232763: "Ира", 232805: "Аня", 202856: "Лена",
         154181: "Лиза", 84116: "Борис", 229704: "Маша"}
MINE = re.compile(r"^\[(убрано|дубль|закрыто|сведено)")
# Итоги, которые администратор выбирает на странице. Порядок — от самого
# частого к редкому: чем меньше ходить мышью, тем честнее отметки.
RESULTS = [
    ("nedozvon", "Не дозвонились", "#B26F00"),
    ("perezvon", "Перезвонить", "#1DA7E0"),
    ("zapisan", "Записан", "#5C8C1E"),
    ("neaktualno", "Не актуально", "#8A8A8A"),
]


def collect(manager_id: int, day: str | None = None) -> list[dict]:
    """Открытые задачи исполнителя на день — со всем, что нужно для звонка."""
    day = day or date.today().isoformat()
    mk = MoyklassClient(sync.get_api_key())
    try:
        tasks = [t for t in taskguard.all_tasks(mk, manager_id)
                 if (t.get("beginDate") or "")[:10] <= day
                 and not (t.get("isComplete") or t.get("isCompleted"))
                 and not MINE.match(t.get("body") or "")]
        uids = {t.get("userId") for t in tasks if t.get("userId")}
        users = {}
        if uids:
            for u in taskguard.pull_all(mk, "/v1/company/users", "users",
                                        cache_hours=6):
                if u.get("id") in uids:
                    users[u["id"]] = u
    finally:
        mk.close()

    out = []
    for t in tasks:
        u = users.get(t.get("userId")) or {}
        phone = "".join(c for c in str(u.get("phone") or "") if c.isdigit())
        out.append({
            "task_id": t["id"],
            "uid": t.get("userId"),
            "name": (u.get("name") or "").strip() or "— без карточки —",
            "phone": ("+" + phone[-11:]) if len(phone) >= 11 else "",
            "body": (t.get("body") or "").strip(),
            "day": (t.get("beginDate") or "")[:10],
            "overdue": (t.get("beginDate") or "")[:10] < date.today().isoformat(),
            "category": t.get("categoryId"),
        })
    # Просроченные вперёд: они ждут дольше всех, и по ним выше риск потерять
    # семью. Внутри — по тексту, чтобы однотипные шли подряд и разговор
    # не приходилось перестраивать на каждой строке.
    out.sort(key=lambda r: (not r["overdue"], r["body"][:40], r["name"]))
    return out


def apply_result(task_id: int, result: str, note: str = "",
                 manager_id: int = 0) -> dict:
    """Отметить результат: закрыть задачу или перенести, и записать касание.

    Комментарий в карточку пишется всегда — именно он отвечает на вопрос
    «звонили или нет», который раньше повисал в воздухе."""
    if result not in {k for k, _, _ in RESULTS}:
        raise ValueError(f"неизвестный итог: {result}")
    mk = MoyklassClient(sync.get_api_key())
    try:
        task = None
        for mid in ([manager_id] if manager_id else list(STAFF)):
            for t in taskguard.all_tasks(mk, mid):
                if t.get("id") == task_id:
                    task = t
                    break
            if task:
                break
        if not task:
            raise ValueError(f"задача {task_id} не найдена")

        stamp = datetime.now().strftime("%d.%m %H:%M")
        label = dict((k, n) for k, n, _ in RESULTS)[result]
        who = STAFF.get(manager_id, "администратор")
        if task.get("userId"):
            body = f"{stamp} · обзвон ({who}): {label}"
            if note:
                body += f"\n{note}"
            body += f"\n\nПо задаче: {(task.get('body') or '')[:200]}"
            mk.post("/v1/company/userComments",
                    {"userId": task["userId"], "comment": body[:995],
                     "showToUser": False})

        if result in ("zapisan", "neaktualno"):
            _rewrite(mk, task, done=True, prefix=f"[{label}]")
            moved = None
        else:
            # Перезвон — на ближайшую смену того же человека, чтобы задача
            # не всплывала в выходной; недозвон — на следующий день.
            nxt = taskguard.next_workday(
                manager_id or (task.get("managerIds") or [0])[0],
                not_before=(date.today() + timedelta(days=1)).isoformat())
            _rewrite(mk, task, done=False, day=nxt,
                     prefix=f"[{label} {stamp}]")
            moved = nxt
    finally:
        mk.close()
    return {"ok": True, "итог": label, "перенесено_на": moved}


def _rewrite(mk: MoyklassClient, t: dict, *, done: bool,
             day: str | None = None, prefix: str = "") -> None:
    """Полная замена задачи: поле, которого нет в теле, МойКласс стирает."""
    b = {k: t.get(k) for k in ("userId", "classIds", "filialIds", "ownerId",
                               "reminds", "managerIds")}
    b = {k: v for k, v in b.items() if v is not None}
    b["categoryId"] = t.get("categoryId") or 104576
    b["isAllDay"] = False
    d = day or (t.get("beginDate") or "")[:10] or date.today().isoformat()
    hour = taskguard.msk_hour(t.get("beginDate"))
    b["beginDate"] = f"{d}T{hour}:00+03:00"
    b["endDate"] = f"{d}T20:00:00+03:00"
    old = (t.get("body") or "")
    # Метку итога держим в начале, но не копим: вторая попытка заменяет первую,
    # иначе тело задачи за неделю превращается в ленту служебных пометок.
    old = re.sub(r"^\[[^\]]+\]\s*", "", old)
    b["body"] = f"{prefix} {old}".strip()[:250]
    if done:
        b["isComplete"] = True
    mk.post(f"/v1/company/tasks/{t['id']}", b)


def page(manager_id: int, rows: list[dict] | None = None) -> str:
    rows = rows if rows is not None else collect(manager_id)
    who = STAFF.get(manager_id, "смена")
    over = sum(1 for r in rows if r["overdue"])
    buttons = "".join(
        f'<button class="r" data-r="{k}" style="--c:{c}">{H.escape(n)}</button>'
        for k, n, c in RESULTS)
    trs = []
    for r in rows:
        tel = (f'<a href="tel:{H.escape(r["phone"])}">{H.escape(r["phone"])}</a>'
               if r["phone"] else '<span class="no">нет телефона</span>')
        link = (f'<a class="crm" href="https://app.moyklass.com/user/{r["uid"]}" '
                f'target="_blank" rel="noopener">карточка</a>' if r["uid"] else "")
        trs.append(f"""<div class="row" data-task="{r['task_id']}">
  <div class="who"><b>{H.escape(r['name'])}</b>{' <i class="od">просрочено</i>' if r['overdue'] else ''}
    <div class="tel">{tel} {link}</div></div>
  <div class="what">{H.escape(r['body'])}</div>
  <div class="act">{buttons}<input class="note" placeholder="что сказали"></div>
</div>""")
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Очередь на сегодня — {H.escape(who)}</title>
<style>
:root{{--indigo:#312783;--ink:#1B1D2B;--muted:#5F6478;--line:#E3E1DA;--fill:#F6F5F1}}
*{{box-sizing:border-box}}
body{{margin:0;background:#fff;color:var(--ink);
 font:15px/1.5 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
.wrap{{max-width:60rem;margin:0 auto;padding:1.2rem 1rem 4rem}}
h1{{font-size:1.5rem;font-weight:800;margin:.2rem 0 .3rem;color:var(--indigo)}}
.lead{{color:var(--muted);font-size:.93rem;margin:0 0 1.1rem}}
.row{{border:1px solid var(--line);border-radius:.6rem;padding:.7rem .85rem;
 margin-bottom:.6rem;display:grid;gap:.5rem;
 grid-template-columns:15rem minmax(0,1fr);align-items:start}}
.row.done{{opacity:.42}}
.who b{{font-size:1rem}}
.od{{font-style:normal;font-size:.7rem;color:#B03A2E;font-weight:700}}
.tel{{font-size:.86rem;margin-top:.15rem;font-variant-numeric:tabular-nums}}
.tel a{{color:var(--indigo);font-weight:700;text-decoration:none}}
.crm{{margin-left:.5rem;font-weight:400;font-size:.8rem;color:var(--muted)}}
.no{{color:var(--muted)}}
.what{{font-size:.9rem;color:#33364a}}
.act{{grid-column:1/-1;display:flex;gap:.4rem;flex-wrap:wrap;align-items:center;
 border-top:1px solid var(--line);padding-top:.5rem}}
button.r{{border:1px solid var(--c);background:#fff;color:var(--c);font-weight:700;
 font-size:.84rem;padding:.35rem .7rem;border-radius:.4rem;cursor:pointer}}
button.r:hover{{background:var(--c);color:#fff}}
.note{{flex:1;min-width:9rem;border:1px solid var(--line);border-radius:.4rem;
 padding:.35rem .5rem;font:inherit;font-size:.86rem}}
.msg{{font-size:.82rem;color:var(--muted)}}
@media (max-width:640px){{.row{{grid-template-columns:1fr}}}}
@media (prefers-color-scheme:dark){{
 :root:not([data-theme="light"]){{--ink:#EDEEF3;--muted:#A5A9BC;--line:#33364A;
  --fill:#222432;--indigo:#8E86E8}}
 :root:not([data-theme="light"]) body{{background:#161826}}
 :root:not([data-theme="light"]) button.r{{background:#161826}}
 :root:not([data-theme="light"]) .note{{background:#222432;color:var(--ink)}}
 :root:not([data-theme="light"]) .what{{color:var(--ink)}}
}}
</style></head><body><div class="wrap">
<h1>Очередь на сегодня — {H.escape(who)}</h1>
<p class="lead">{len(rows)} задач, из них просрочено {over}. Это и есть список
задач в МойКласс: отметили итог — задача закрылась, в карточку легла запись
о разговоре. Второго списка нет.</p>
{"".join(trs) or "<p>Очередь пуста.</p>"}
<script>
document.querySelectorAll('.row').forEach(row => {{
  row.querySelectorAll('button.r').forEach(b => {{
    b.addEventListener('click', async () => {{
      const note = row.querySelector('.note').value.trim();
      row.querySelectorAll('button.r').forEach(x => x.disabled = true);
      try {{
        const res = await fetch('/api/queue/result', {{
          method: 'POST', headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{task_id: +row.dataset.task, result: b.dataset.r,
                                note: note, manager_id: {manager_id}}})
        }});
        const j = await res.json();
        if (!res.ok) throw new Error(j.detail || 'ошибка');
        row.classList.add('done');
        row.querySelector('.act').innerHTML =
          '<span class="msg">' + j['итог'] +
          (j['перенесено_на'] ? ' — перенесено на ' + j['перенесено_на'] : ' — закрыто') +
          '</span>';
      }} catch (e) {{
        row.querySelectorAll('button.r').forEach(x => x.disabled = false);
        alert('Не получилось: ' + e.message);
      }}
    }});
  }});
}});
</script>
</div></body></html>"""


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    mid = int(sys.argv[1]) if len(sys.argv) > 1 else 232805
    rows = collect(mid)
    print(f"{STAFF.get(mid, mid)}: {len(rows)} задач в очереди, "
          f"просрочено {sum(1 for r in rows if r['overdue'])}")
    for r in rows[:12]:
        print(f"   [{r['task_id']}] {r['name'][:28]:28s} {r['phone']:13s} "
              f"{r['body'][:52]}")


if __name__ == "__main__":
    main()
