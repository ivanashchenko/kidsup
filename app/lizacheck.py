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
    """Последнее входящее и последнее исходящее по каждому телефону —
    вместе с текстом. Текст нужен для второй фазы разбора: одно дело,
    когда клиент последним написал «а сколько стоит», и совсем другое —
    когда «спасибо, поняла»."""
    inbox, outbox = {}, {}
    try:
        with db.get_conn() as conn:
            for tbl, dst in (("wazzup_inbox", inbox), ("wazzup_outbox", outbox)):
                try:
                    for ts, phone, text in conn.execute(
                            f"SELECT ts, phone, text FROM {tbl} ORDER BY ts"):
                        p = "".join(c for c in str(phone or "") if c.isdigit())[-10:]
                        if len(p) == 10:
                            dst[p] = (ts, str(text or ""))
                except Exception:
                    continue
    except Exception:
        pass
    return inbox, outbox


# Реплики, которыми разговор заканчивают, а не продолжают. Если последнее
# слово клиента такое — вопрос закрыт, задача больше ни к чему.
_CLOSING = re.compile(
    r"^\s*(спасибо|благодар|хорошо|ок|окей|ok|поняла|понял|принял|принято|"
    r"договорились|ждём|ждем|будем ждать|да, конечно|отлично|супер|"
    r"подтверждаю|👍|🙏|❤|😊)", re.I)
# Слова, по которым видно, что клиент задал вопрос и ждёт ответа.
_ASKING = re.compile(r"[?]|сколько|когда|какой|какие|можно ли|подскажите|"
                     r"уточнит|а если|расскажите", re.I)


def _second_pass(row: dict, inbox: dict, outbox: dict,
                 comments_text: dict) -> dict:
    """Вторая фаза для «проверить»: смотрим НЕ факт переписки, а её итог.

    Три исхода. Клиент написал последним и задал вопрос — это самая
    дорогая строка в списке, человек ждёт нас прямо сейчас. Клиент
    попрощался — закрываем. Мы написали последними и человек молчит —
    задача жива, но не горит: тут нужен дожим, а не ответ."""
    p = row["phone"]
    inn = inbox.get(p)
    out = outbox.get(p)
    if inn and (not out or inn[0] > out[0]):
        text = inn[1].strip()
        if _CLOSING.match(text):
            row["verdict"] = "закрыть"
            row["why"] = f"клиент попрощался: «{text[:40]}»"
        elif _ASKING.search(text):
            row["verdict"] = "актуальна"
            row["why"] = f"КЛИЕНТ ЖДЁТ ОТВЕТА: «{text[:60]}»"
        else:
            row["verdict"] = "актуальна"
            row["why"] = f"последнее слово за клиентом: «{text[:50]}»"
        return row
    if out and (not inn or out[0] > inn[0]):
        row["verdict"] = "актуальна"
        row["why"] = (f"мы написали {out[0][5:16]}, ответа нет — "
                      f"нужен дожим, а не ответ")
        return row
    ct = comments_text.get(row["uid"], "")
    if ct:
        row["verdict"] = "актуальна"
        row["why"] = f"по карточке шла работа: «{ct[:50]}»"
    return row


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
        comments_text: dict = {}
        try:
            cm = mk.get("/v1/company/userComments", {"limit": 500})
            for x in ((cm.get("userComments") if isinstance(cm, dict) else cm) or []):
                uid, when = x.get("userId"), str(x.get("createdAt") or "")
                if uid and when > comments.get(uid, ""):
                    comments[uid] = when
                    comments_text[uid] = re.sub(r"\s+", " ",
                                                str(x.get("comment") or ""))
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
            elif (inbox.get(phone) or ("", ""))[0] > born:
                verdict, why = "проверить", "клиент ответил после постановки"
            elif (outbox.get(phone) or ("", ""))[0] > born:
                verdict, why = "проверить", "мы уже написали ему после постановки"
            elif uid and comments.get(uid, "") > born:
                verdict, why = "проверить", "в карточке новый комментарий"
            elif after:
                why = f"набирали {len(after)} раз, разговора не было"
        row = {"id": t["id"], "uid": uid,
                    "cat": CAT.get(t.get("categoryId"), "—"),
                    "body": re.sub(r"\s+", " ", str(t.get("body") or ""))[:200],
                    "end": str(t.get("endDate") or "")[:10],
                    "name": (u.get("name") or "")[:26], "phone": phone,
               "verdict": verdict, "why": why}
        if verdict == "проверить":
            row = _second_pass(row, inbox, outbox, comments_text)
        out.append(row)
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
    ("проверить", "Проверить глазами — итог переписки неясен",
     "Что-то происходило, но чем кончилось — по журналу не видно. "
     "Открыть диалог и решить руками."),
    ("актуальна", "Живые задачи — делать",
     "Либо после постановки не было ничего, либо разговор оборвался "
     "на нас. Строки, где написано «КЛИЕНТ ЖДЁТ ОТВЕТА», разбирать "
     "первыми: человек задал вопрос и сидит без ответа."),
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
           f"работы остаётся {len(by['актуальна'])}, и из них "
           f"{len([r for r in rows if 'ЖДЁТ ОТВЕТА' in r['why']])} — люди, "
           f"которые задали вопрос и до сих пор без ответа.</div>"]
    for key, title, why in GROUPS:
        rws = by.get(key) or []
        if not rws:
            continue
        rws.sort(key=lambda r: (0 if "ЖДЁТ ОТВЕТА" in r["why"] else 1,
                                r["end"] or "9999", r["name"]))
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


def close_done(dry: bool = True) -> dict:
    """Отметить выполненными задачи с вердиктом «закрыть».

    Владелец разрешил это 25.08, увидев основания. В тело задачи дописываем,
    почему она закрыта: через месяц никто не вспомнит, и «закрыто роботом»
    без объяснения выглядит как потеря работы."""
    import time
    rows = [r for r in check() if r["verdict"] == "закрыть"]
    stat = {"к закрытию": len(rows), "закрыто": 0, "ошибок": 0}
    if dry:
        return stat
    mk = MoyklassClient(sync.get_api_key())
    try:
        for r in rows:
            try:
                t = mk.get(f"/v1/company/tasks/{r['id']}")
                payload = {k: t.get(k) for k in
                           ("body", "beginDate", "endDate", "isAllDay",
                            "managerIds", "userId", "classIds", "filialIds",
                            "categoryId")}
                payload["isComplete"] = True
                payload["body"] = (f"✅ Закрыто автоматически: {r['why']}. "
                                   + str(payload.get("body") or ""))[:250]
                # Проверяем результат, а не верим ответу: 25.08 из 134 задач
                # реально закрылись 67 — МойКласс отвечал 200, но на потоке
                # молча не применял isComplete. Ошибок при этом не было ни
                # одной, и список выглядел разобранным, оставаясь прежним.
                ok = False
                for attempt in range(3):
                    mk.post(f"/v1/company/tasks/{r['id']}", payload)
                    time.sleep(0.5)
                    if mk.get(f"/v1/company/tasks/{r['id']}").get("isComplete"):
                        ok = True
                        break
                    time.sleep(1.0)
                if ok:
                    stat["закрыто"] += 1
                else:
                    stat["не применилось"] = stat.get("не применилось", 0) + 1
            except Exception as e:
                stat["ошибок"] += 1
                log.warning("задача %s: %s", r["id"], str(e)[:80])
            time.sleep(0.3)
    finally:
        mk.close()
    return stat


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
