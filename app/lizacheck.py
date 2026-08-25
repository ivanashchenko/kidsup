"""Проверка задач Лизы на актуальность: что уже сделано и можно закрыть.

Зачем. У администратора переписки скопилось под три сотни открытых задач.
Часть из них давно отработана — человеку позвонили, ответили в мессенджере,
он записался или отказался, — но галочку никто не поставил. Список,
в котором половина строк мертва, перестают читать целиком, и вместе
с мёртвыми теряются живые.

Чем проверяем каждую задачу (всё, что было ПОСЛЕ её постановки):
  · запись в группу нового сезона — по набору говорить больше не о чем;
  · состоявшийся разговор дольше двадцати секунд — по журналу АТС;
  · наше исходящее сообщение в мессенджере — по журналу Wazzup;
  · ответ клиента — диалог живой, его ведёт человек;
  · статус карточки «отказ» или «не писать» — работать не с кем;
  · новый комментарий в карточке — кто-то уже занимался.

Недозвон отработкой НЕ считается: набрали и не дозвонились — задача жива.
Это главная развилка, из-за которой нельзя закрывать всё подряд по факту
любого звонка.

Мы предлагаем закрыть, а не закрываем: решение за человеком, а рядом
со строкой написано основание.

Запуск:
    python -m app.lizacheck            — сводка
    python -m app.lizacheck build      — записать docs/zadachi_lizy.html
"""

from __future__ import annotations

import html as _html
import logging
import re
from datetime import date, datetime, timedelta

from . import db, sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.lizacheck")

LIZA = 154181
CAT = {44336: "общая", 44337: "срочно", 104575: "переписка",
       104576: "звонок", 104577: "дожим", 104578: "орг"}
ACTIVE_JOIN = {2, 50509, 58131, 58132, 83760}
DEAD_STATE = {125957: "отказ", 146328: "не писать", 125954: "некачественный"}
TALK = 20


def _calls(days: int = 16) -> dict:
    from . import mango
    out: dict = {}
    for dd in range(days):
        day = date.today() - timedelta(days=dd)
        try:
            rows = mango.calls(datetime.combine(day, datetime.min.time()),
                               datetime.combine(day, datetime.max.time()))
        except Exception:
            continue
        for r in rows:
            n = (r.get("to_num") if r.get("from_ext") else r.get("from_num")) or ""
            d = "".join(c for c in str(n) if c.isdigit())[-10:]
            if len(d) != 10:
                continue
            dur = (r["finish"] - r["answer"]) if r.get("answer") else 0
            when = datetime.fromtimestamp(r["start"]).isoformat(timespec="seconds")
            out.setdefault(d, []).append((when, dur))
    return out


def _messages() -> tuple:
    inbox, outbox = {}, {}
    try:
        with db.get_conn() as conn:
            for tbl, dst in (("wazzup_inbox", inbox), ("wazzup_outbox", outbox)):
                try:
                    for ts, phone in conn.execute(
                            f"SELECT ts, phone FROM {tbl} ORDER BY ts"):
                        p = "".join(c for c in str(phone or "") if c.isdigit())[-10:]
                        if len(p) == 10:
                            dst[p] = ts
                except Exception:
                    continue
    except Exception:
        pass
    return inbox, outbox


def check() -> list:
    mk = MoyklassClient(sync.get_api_key())
    try:
        tasks = mk.fetch_all("/v1/company/tasks", ["tasks"],
                             params={"limit": 500}) or []
        users = {u["id"]: u for u in
                 taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=2)}
        joins = taskguard.pull_all(mk, "/v1/company/joins", "joins")
        rc = mk.get("/v1/company/classes", {"limit": 500})
        cls = {c["id"]: (c.get("name") or "")
               for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
        comments: dict = {}
        try:
            cm = mk.get("/v1/company/userComments", {"limit": 500})
            for x in ((cm.get("userComments") if isinstance(cm, dict) else cm) or []):
                uid, when = x.get("userId"), str(x.get("createdAt") or "")
                if uid and when > comments.get(uid, ""):
                    comments[uid] = when
        except Exception:
            pass
    finally:
        mk.close()

    booked = {j["userId"] for j in joins
              if cls.get(j.get("classId"), "").startswith("2627")
              and j.get("statusId") in ACTIVE_JOIN
              and "аявк" not in cls.get(j.get("classId"), "").lower()}
    calls = _calls()
    inbox, outbox = _messages()

    out = []
    for t in tasks:
        if t.get("isComplete") or LIZA not in (t.get("managerIds") or []):
            continue
        uid = t.get("userId")
        u = users.get(uid) or {}
        phone = "".join(c for c in str(u.get("phone") or "") if c.isdigit())[-10:]
        born = str(t.get("createdAt") or t.get("beginDate") or "")[:19]
        verdict, why = "актуальна", ""
        if uid and uid in booked:
            verdict, why = "закрыть", "клиент записан на 2026/27"
        elif u.get("clientStateId") in DEAD_STATE:
            verdict = "закрыть"
            why = f"статус карточки — {DEAD_STATE[u['clientStateId']]}"
        else:
            after = [(w, d) for w, d in calls.get(phone, []) if w >= born]
            talked = [(w, d) for w, d in after if d >= TALK]
            if talked:
                w, d = max(talked, key=lambda x: x[1])
                verdict = "закрыть"
                why = (f"разговор {w[8:10]}.{w[5:7]} в {w[11:16]}, "
                       f"{d // 60} мин {d % 60} с")
            elif inbox.get(phone, "") > born:
                verdict, why = "проверить", "клиент ответил после постановки"
            elif outbox.get(phone, "") > born:
                verdict, why = "проверить", "мы уже написали ему после постановки"
            elif uid and comments.get(uid, "") > born:
                verdict, why = "проверить", "в карточке новый комментарий"
            elif after:
                why = f"набирали {len(after)} раз, разговора не было"
        out.append({"id": t["id"], "uid": uid,
                    "cat": CAT.get(t.get("categoryId"), "—"),
                    "body": re.sub(r"\s+", " ", str(t.get("body") or ""))[:200],
                    "end": str(t.get("endDate") or "")[:10],
                    "name": (u.get("name") or "")[:26], "phone": phone,
                    "verdict": verdict, "why": why})
    return out


CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:14px;color:#222;max-width:1150px}
h1{font-size:20px;margin:0 0 4px} .sub{color:#666;font-size:13px;margin-bottom:14px}
h2{font-size:16px;color:#312783;margin:22px 0 4px}
.why{color:#555;font-size:12.5px;margin:0 0 8px;border-left:3px solid #1DA7E0;padding-left:9px}
table{border-collapse:collapse;width:100%;margin-bottom:4px}
th{background:#312783;color:#fff;font-size:11px;padding:5px;text-align:left}
td{border-bottom:1px solid #e3e3e3;padding:6px 5px;font-size:12.5px;vertical-align:top}
.ph{white-space:nowrap;font-weight:600} .cat{color:#888;font-size:11px}
.late{color:#E30613;font-weight:600} .ok{color:#3d7a1f;font-size:12px}
.tot{background:#F4F9EF;border-left:4px solid #7DB928;padding:10px 12px;font-size:13px;margin:12px 0}
.n{color:#888;font-weight:400;font-size:14px}
"""

GROUPS = [
    ("закрыть", "Можно закрыть — работа уже сделана",
     "После постановки задачи состоялся разговор, человек записался или "
     "карточка закрыта отказом. Проверьте основание справа и отметьте "
     "выполненными — список станет честным."),
    ("проверить", "Проверить глазами — что-то происходило",
     "Клиент отвечал, мы ему писали или появился комментарий в карточке. "
     "Возможно, задача уже не нужна, но автоматически такое закрывать "
     "нельзя: переписка могла оборваться на полуслове."),
    ("актуальна", "Живые задачи — делать",
     "Ни разговора, ни переписки после постановки. Это и есть настоящая "
     "работа на сегодня."),
]


def page(rows: list) -> str:
    today = date.today().isoformat()
    by = {k: [r for r in rows if r["verdict"] == k] for k, *_ in GROUPS}
    out = [f"<style>{CSS}</style>",
           "<h1>Задачи Лизы: проверка на актуальность</h1>",
           f"<div class=sub>{len(rows)} открытых задач на "
           f"{date.today():%d.%m.%Y}. Каждая проверена по журналу звонков, "
           f"переписке, комментариям и записям — всё, что было после "
           f"её постановки.</div>",
           f"<div class=tot><b>{len(by['закрыть'])} задач можно закрыть "
           f"прямо сейчас</b> — работа по ним сделана. Ещё "
           f"{len(by['проверить'])} стоит проглядеть глазами. Реальной "
           f"работы остаётся {len(by['актуальна'])}.</div>"]
    for key, title, why in GROUPS:
        rws = by.get(key) or []
        if not rws:
            continue
        rws.sort(key=lambda r: (r["end"] or "9999", r["name"]))
        out.append(f"<h2>{title} <span class=n>— {len(rws)}</span></h2>")
        out.append(f"<div class=why>{why}</div>")
        out.append("<table><tr><th>Срок</th><th>Клиент</th><th>Телефон</th>"
                   "<th>Что сделать</th><th>Основание</th></tr>")
        for r in rws:
            late = " class=late" if r["end"] and r["end"] < today else ""
            end = f"{r['end'][8:10]}.{r['end'][5:7]}" if r["end"] else "—"
            out.append(f"<tr><td{late}>{end}</td>"
                       f"<td>{_html.escape(r['name'] or '—')}</td>"
                       f"<td class=ph>{'+7' + r['phone'] if r['phone'] else ''}</td>"
                       f"<td>{_html.escape(r['body'])} "
                       f"<span class=cat>[{r['cat']}]</span></td>"
                       f"<td class=ok>{_html.escape(r['why'])}</td></tr>")
        out.append("</table>")
    return "\n".join(out)


def main():
    import sys
    from collections import Counter
    from pathlib import Path
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rows = check()
    if "build" in sys.argv:
        p = Path(__file__).resolve().parent.parent / "docs" / "zadachi_lizy.html"
        p.write_text(page(rows), encoding="utf-8")
        print(p)
    for k, n in Counter(r["verdict"] for r in rows).most_common():
        print(f"   {k:12} {n}")


if __name__ == "__main__":
    main()
