"""Полный разбор задач смыслом вместо набора правил.

Чем отличается от того, что было. Правила решали по словам, и каждое
работало вслепую относительно остальных: одно поднимало просрочку,
второе двигало по сменам, третье резало дубли — и они спорили между
собой. Ошибка любого была не видна, пока кто-то не открывал список
и не спрашивал «а почему это здесь».

Здесь модель видит задачу целиком и отвечает сразу на все вопросы:
жива ли она, кому адресована, что в ней на самом деле надо сделать
и насколько это срочно. Один проход вместо шести.

Экономия. Задачи уходят пачками по PACK штук в одном запросе, ответы
кэшируются по содержанию. 440 задач — это около 30 запросов, меньше
доллара. Без пачек было бы 440 запросов и заметные деньги на ровном месте.

Границы. Модель НЕ применяет решения сама: она возвращает разбор,
а применяет его код с теми же предохранителями, что и раньше. Задачу
с деньгами не закрываем автоматически, даже если модель уверена;
низкая уверенность означает «оставить как есть», а не «сделать наугад».

Запуск:
    python -m app.taskmind show     — что модель думает о задачах
    python -m app.taskmind apply    — применить разбор
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter, defaultdict
from datetime import date

from . import brain, sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.taskmind")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"

STAFF = {232763: "Ира", 232805: "Аня", 202856: "Лена",
         154181: "Лиза", 84116: "Борис", 229704: "Маша"}
CALLERS = (232763, 232805, 202856)
CHAT_ADMIN, OWNER = 154181, 84116
CAT = {"urgent": 44337, "chat": 104575, "call": 104576,
       "push": 104577, "org": 104578}
PACK = 8            # задач в одном запросе к модели
# Ответ на пачку длиннее одиночного: по каждой задаче четыре поля
# плюс объяснение. На восьми задачах хватает четырёх тысяч токенов.
PACK_TOKENS = 4000

# Задачи, которые автоматика не закрывает никогда, что бы ни ответила
# модель: цена ошибки — потерянные деньги или обиженный клиент.
NEVER_CLOSE = re.compile(
    r"оплат|возврат|счёт|счет|долг|деньги|рассроч|компенсац|"
    r"жалоб|конфликт|претенз|инцидент", re.I)

SYSTEM_TPL = """\
СЕГОДНЯ {today}. Это важно: дата в будущем не является просрочкой,
а «стоит на 25.08» при сегодняшнем 22.08 означает запланировано, а не
пропущено. Модель без явной даты считает просроченным всё подряд.

Ты разбираешь список задач администраторов детского центра KidsUP.
По каждой задаче отвечаешь: жива ли она, кто её исполняет, что в ней
на самом деле надо сделать и насколько это срочно.

Кто есть кто:
· ВЛАДЕЛЕЦ (owner) — только то, чего сотрудник не может сделать сам:
  доступы и пароли, деньги компании, наём, аренда, реклама, договоры
  с юрлицами, правки сайта, решения о ценах, педагогах и расписании.
· ЛИЗА (liza) — ВХОДЯЩАЯ переписка и деньги: клиент написал сам и ждёт
  ответа; счета, ссылки на оплату, возвраты, долги. Она одна на весь чат,
  и её время — самый узкий ресурс центра.
· ДЕЖУРНЫЙ (duty) — работа с базой в любом канале: звонки, а если звонки
  не сработали, то и сообщения тому же клиенту. Исходящее сообщение по
  базе набора — это работа дежурного, а НЕ Лизы. Отдавать ей весь обзвон
  под видом «это же переписка» нельзя: у неё физически нет на это времени.

Что означает «мертва» (dead): задача больше не имеет смысла — цель
достигнута, срок безнадёжно прошёл, клиент отказался, или это дубль
по смыслу другой задачи из этого же списка.

Важные правила центра, по которым ты судишь о смысле:
· Учебный год начинается 31 августа 2026. Сегодня конец августа —
  набор в разгаре, задачи «позвать на пробное» актуальны.
· События: 29.08 праздник, 30.08 День открытых дверей,
  31.08–06.09 Неделя открытых уроков.
· Задача получает тот, кто ИСПОЛНЯЕТ. Если не хватает факта, который
  знает владелец, факт вписывают в текст, а задачу отдают исполнителю.
· Срочно — это то, что теряет смысл за сутки: клиент ждёт ответа,
  обещали перезвонить сегодня, пропущенный звонок. Обычный обзвон
  холодной базы срочным не бывает никогда.

Отвечай честно: если по тексту нельзя понять, что делать, ставь
confidence "low" — лучше оставить человеку, чем угадать неверно."""


def _system() -> str:
    return SYSTEM_TPL.format(today=date.today().strftime("%d.%m.%Y"))

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "description": "номер задачи из списка"},
                    "alive": {"type": "boolean",
                              "description": "имеет ли задача ещё смысл"},
                    "who": {"type": "string",
                            "enum": ["owner", "liza", "duty", "keep"],
                            "description": "keep — оставить текущего исполнителя"},
                    "urgency": {"type": "string",
                                "enum": ["today", "normal", "later"]},
                    "action": {"type": "string",
                               "description": "что сделать, одной фразой по-русски"},
                    "why": {"type": "string",
                            "description": "почему такой вывод, коротко по-русски"},
                    "confidence": {"type": "string",
                                   "enum": ["high", "medium", "low"]},
                },
                "required": ["n", "alive", "who", "urgency", "confidence"],
            },
        },
    },
    "required": ["items"],
}


def _pack_text(tasks: list[dict], ctx: dict) -> str:
    lines = []
    for i, t in enumerate(tasks, 1):
        mid = (t.get("managerIds") or [None])[0]
        uid = t.get("userId")
        extra = []
        if uid and uid in ctx.get("booked", {}):
            extra.append("клиент УЖЕ записан: "
                         + ", ".join(ctx["booked"][uid])[:70])
        if uid and uid in ctx.get("paid", set()):
            extra.append("абонемент нового сезона оплачен")
        age = ctx["ages"].get(t["id"])
        lines.append(
            f"[{i}] исполнитель: {STAFF.get(mid, '?')}; "
            f"заведена {age} дн. назад; стоит на {(t.get('beginDate') or '')[:10]}\n"
            f"     текст: {(t.get('body') or '')[:300]}"
            + ("\n     контекст: " + "; ".join(extra) if extra else ""))
    return "Задачи:\n\n" + "\n\n".join(lines)


def collect(mk: MoyklassClient) -> dict:
    tasks = []
    for mid in STAFF:
        tasks += [t for t in taskguard.all_tasks(mk, mid)
                  if not (t.get("isComplete") or t.get("isCompleted"))]
    tasks = list({t["id"]: t for t in tasks}.values())

    joins = taskguard.pull_all(mk, "/v1/company/joins", "joins")
    rc = mk.get("/v1/company/classes", {"limit": 500})
    cls = {c["id"]: (c.get("name") or "")
           for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
    booked = defaultdict(list)
    for j in joins:
        nm = cls.get(j.get("classId"), "")
        if nm.startswith("2627") and not re.search(r"Заявк|Roistat", nm, re.I) \
                and j.get("statusId") in {2, 50509, 58131, 58132, 83760} \
                and j.get("userId"):
            booked[j["userId"]].append(nm)
    subs = taskguard.pull_all(mk, "/v1/company/userSubscriptions", "subscriptions")
    paid = {s["userId"] for s in subs
            if s.get("userId") and ((s.get("beginDate") or "")[:10] >= "2026-08-25"
                                    or (s.get("endDate") or "")[:10] >= "2026-09-01")}
    ages = {}
    for t in tasks:
        c = (t.get("createdAt") or "")[:10]
        try:
            ages[t["id"]] = (date.today() - date.fromisoformat(c)).days
        except Exception:
            ages[t["id"]] = 0
    return {"tasks": tasks, "booked": dict(booked), "paid": paid, "ages": ages}


def think(data: dict, limit: int = 0) -> list[dict]:
    if not brain.enabled():
        log.error("taskmind: ключ Anthropic не вписан — разбор смыслом невозможен")
        return []
    tasks = data["tasks"][:limit] if limit else data["tasks"]
    out = []
    for start in range(0, len(tasks), PACK):
        pack = tasks[start:start + PACK]
        r = brain.ask(_system(), _pack_text(pack, data), SCHEMA, "tasks",
                      max_tokens=PACK_TOKENS)
        got = {i["n"]: i for i in (r or {}).get("items", []) if isinstance(i, dict)}
        for i, t in enumerate(pack, 1):
            v = got.get(i)
            out.append({"id": t["id"], "uid": t.get("userId"),
                        "mgr": (t.get("managerIds") or [None])[0],
                        "body": (t.get("body") or "")[:120],
                        "day": (t.get("beginDate") or "")[:10],
                        "cat": t.get("categoryId"),
                        "verdict": v})
        log.info("разобрано %d/%d", min(start + PACK, len(tasks)), len(tasks))
        time.sleep(0.4)
    json.dump(out, open(f"{SP}/taskmind.json", "w"), ensure_ascii=False)
    return out


def _plan(row: dict) -> dict | None:
    """Что сделать по разбору. None — не трогать."""
    v = row.get("verdict")
    if not v or v.get("confidence") == "low":
        return None
    body = row["body"]
    money = bool(NEVER_CLOSE.search(body))

    if not v.get("alive"):
        if money:
            # Деньги и жалобы автоматика не закрывает: ошибка стоит дороже
            # висящей лишней задачи.
            return None
        return {"act": "закрыть", "why": v.get("why") or "цель достигнута"}

    plan = {}
    who = v.get("who")
    if who in ("owner", "liza", "duty"):
        want = None
        if who == "owner":
            want = OWNER
        elif who == "liza":
            want = CHAT_ADMIN
        elif who == "duty" and row["mgr"] not in CALLERS:
            # «Дежурный» — это роль, а не конкретный человек: у задачи
            # на 25 августа дежурит не тот, кто сегодня. Поэтому задачу,
            # которая уже у звонящего администратора, не трогаем —
            # иначе она уедет к тому, кто в её день не работает.
            from .taskguard import _duty
            want = _duty() or CHAT_ADMIN
        if want and want != row["mgr"] and v.get("confidence") == "high":
            plan["mgr"] = want
            plan["why"] = v.get("why") or "исполнитель по смыслу задачи"

    urg = v.get("urgency")
    want_cat = (CAT["urgent"] if urg == "today" else None)
    if urg != "today" and row["cat"] == CAT["urgent"]:
        want_cat = CAT["call"] if "📞" in body or "звон" in body.lower() \
            else CAT["chat"]
    if want_cat and want_cat != row["cat"]:
        plan["cat"] = want_cat
        plan.setdefault("why", "срочность по смыслу, а не по метке")

    return {"act": "поправить", **plan} if plan else None


def apply(limit: int = 0) -> dict:
    rows = json.load(open(f"{SP}/taskmind.json"))
    if limit:
        rows = rows[:limit]
    mk = MoyklassClient(sync.get_api_key())
    stat: Counter = Counter()
    try:
        for row in rows:
            p = _plan(row)
            if not p:
                stat["оставлено"] += 1
                continue
            try:
                t = mk.get(f"/v1/company/tasks/{row['id']}")
            except Exception:
                stat["ошибка"] += 1
                continue
            if t.get("isComplete") or t.get("isCompleted"):
                continue
            b = {k: t.get(k) for k in ("userId", "classIds", "filialIds",
                                       "ownerId", "reminds")}
            b = {k: v for k, v in b.items() if v is not None}
            b["managerIds"] = [p["mgr"]] if p.get("mgr") else \
                (t.get("managerIds") or [CHAT_ADMIN])
            b["categoryId"] = p.get("cat") or t.get("categoryId") or CAT["call"]
            b["isAllDay"] = False
            d = (t.get("beginDate") or "")[:10] or date.today().isoformat()
            b["beginDate"] = f"{d}T{taskguard.msk_hour(t.get('beginDate'))}:00+03:00"
            b["endDate"] = f"{d}T20:00:00+03:00"
            if p["act"] == "закрыть":
                b["body"] = f"[убрано: {p['why']}] {t.get('body') or ''}"[:250]
                b["isComplete"] = True
            else:
                b["body"] = (t.get("body") or "")[:250]
            try:
                mk.post(f"/v1/company/tasks/{row['id']}", b)
                stat[p["act"]] += 1
            except Exception:
                stat["ошибка"] += 1
            time.sleep(0.2)
    finally:
        mk.close()
    return dict(stat)


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    lim = next((int(a) for a in sys.argv[2:] if a.isdigit()), 0)
    if cmd == "apply":
        print(apply(lim))
        return
    mk = MoyklassClient(sync.get_api_key())
    try:
        data = collect(mk)
    finally:
        mk.close()
    rows = think(data, lim)
    got = [r for r in rows if r.get("verdict")]
    print(f"\nразобрано {len(got)} из {len(rows)}")
    print("живых:", sum(1 for r in got if r["verdict"].get("alive")),
          "| мёртвых:", sum(1 for r in got if not r["verdict"].get("alive")))
    print("уверенность:", dict(Counter(r["verdict"].get("confidence") for r in got)))
    print("срочность:", dict(Counter(r["verdict"].get("urgency") for r in got)))
    print("исполнитель:", dict(Counter(r["verdict"].get("who") for r in got)))
    print("\nЧТО БУДЕТ СДЕЛАНО:")
    print(dict(Counter((_plan(r) or {}).get("act", "оставить") for r in rows)))
    for r in rows[:6]:
        p = _plan(r)
        if p:
            print(f"\n  {p['act']}: {p.get('why','')}")
            print(f"     {r['body'][:80]}")


if __name__ == "__main__":
    main()
