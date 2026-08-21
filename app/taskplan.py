"""Перепланирование задач с разбором истории по каждому клиенту.

Как было неправильно. 21.08 я разложил 780 задач по дням смен, опираясь
только на текст задачи и её категорию. В итоге задача с комментарием
«перезвонить 24-го» встала на 21-е, задачи по клиентам, которые уже
записались, продолжали висеть как «позвонить для записи», а на одного
человека оставалось по три-четыре пункта подряд. Администраторы получили
список, в котором половина пунктов не имела смысла.

Как правильно. Решение принимается **по клиенту, а не по задаче**. Перед тем
как что-то двигать, собираем всё, что известно:

  · карточка — статус, баланс, записан ли в группу нового года;
  · комментарии в карточке — что уже говорили и обещали;
  · журнал АТС — звонили ли, дозвонились ли, когда и сколько говорили;
  · переписка Wazzup — писали ли мы и ответил ли человек;
  · остальные открытые задачи по этому же клиенту.

И только потом решаем, что с задачами делать. Пять исходов:

  ЗАКРЫТЬ      клиент уже записан, либо отказался, либо задача дублирует
               другую по тому же человеку;
  СРОК         в разговоре или в тексте назван день — ставим на него;
  ЖДЁМ         мы написали и ждём ответа — двигаем на завтра, сегодня
               делать нечего;
  ПЕРЕДАТЬ     задача про деньги у звонящего администратора — Лизе;
  ОСТАВИТЬ     ничего по клиенту не происходило — работает как есть.

Запуск:
    python -m app.taskplan collect   — собрать историю (долго, ~10 минут)
    python -m app.taskplan decide    — показать решения, ничего не меняя
    python -m app.taskplan apply     — применить
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from . import db, sync
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.taskplan")
MSK = ZoneInfo("Europe/Moscow")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"
os.makedirs(SP, exist_ok=True)

CHAT_ADMIN = 154181
CALL_ADMINS = [232763, 232805, 202856]
NAMES = {84116: "Борис", 154181: "Лиза", 202856: "Лена",
         229704: "Маша", 232763: "Ира", 232805: "Аня"}
DEAD_STATES = {125954, 125957, 146328}     # некачественный, отказ, не писать
ENROLLED_JOIN = {2, 83760}                 # учится, подтвердил

MONEY = re.compile(
    r"абонемент|не оплач|оплат|долг|чек|возврат|счёт|счет|баланс|платёж|платеж|"
    r"соцфонд|маткапитал|касс|рассроч|компенсац", re.I)
WAITING = re.compile(
    r"написал[аи]? в (чат|whatsapp|telegram|max)|ответа нет|жд[ёе]м ответ|"
    r"жду ответ|отправил[аи]? (сообщение|информацию)|написано в чат", re.I)
ABOUT_ENROLL = re.compile(
    r"обзвон|позвони|перезвон|записать|запись|пригласи|дожать|дожим|"
    r"продолжение занятий|набора|пробное", re.I)
_VERB = (r"(?:перезвон\w*|позвон\w*|созвон\w*|напомн\w*|связаться|набрать|"
         r"написать|выслать|прислать|отправить|проверить|подтвердить|ждём|ждёт)")
DEADLINE = [
    re.compile(_VERB + r"[^.]{0,40}?\b(\d{1,2})[.\-/](0?[89])\b", re.I),
    re.compile(_VERB + r"[^.]{0,40}?\bна\s+(\d{1,2})\s*(?:-?е|число|числа)?\b", re.I),
    re.compile(_VERB + r"[^.]{0,40}?\b(\d{1,2})\s*-?\s*(?:го|е)\b", re.I),
    re.compile(r"\b(\d{1,2})[.\-/](0?[89])\b[^.]{0,25}?" + _VERB, re.I),
]
LABEL = re.compile(r"\[(?:перенесено в сентябрь[^\]]*|в сентябрь[^\]]*|"
                   r"передано с [^\]]*|перенос ×\d+)\]\s*")


def deadline_in_text(body: str, year: int = 2026) -> str | None:
    for rx in DEADLINE:
        m = rx.search(body or "")
        if not m:
            continue
        g = m.groups()
        try:
            day = int(g[0])
            mon = int(g[1]) if len(g) > 1 and g[1] else 8
        except (TypeError, ValueError):
            continue
        if 1 <= day <= 31 and mon in (8, 9):
            return f"{year}-{mon:02d}-{day:02d}"
    return None


def _mk() -> MoyklassClient:
    return MoyklassClient(sync.get_api_key())


def _retry(fn, *a, **k):
    for i in range(4):
        try:
            return fn(*a, **k)
        except Exception:
            if i == 3:
                raise
            time.sleep(2 ** i)


# ── сбор истории ──────────────────────────────────────────────────────────
def _calls(days: int = 12) -> dict[str, list[dict]]:
    """Журнал АТС по телефонам: когда звонили и сколько говорили."""
    key = db.get_setting("mango_key", "")
    salt = db.get_setting("mango_salt", "")
    now = datetime.now(MSK)
    body = json.dumps({"date_from": int((now - timedelta(days=days)).timestamp()),
                       "date_to": int(now.timestamp()),
                       "fields": "start,finish,answer,from_extension,from_number,"
                                 "to_extension,to_number"}, separators=(",", ":"))
    sign = hashlib.sha256((key + body + salt).encode()).hexdigest()
    r = httpx.post("https://app.mango-office.ru/vpbx/stats/request", timeout=180,
                   data={"vpbx_api_key": key, "sign": sign, "json": body})
    if r.status_code not in (200, 202):
        return {}
    k = r.json().get("key")
    for _ in range(40):
        time.sleep(4)
        b2 = json.dumps({"key": k}, separators=(",", ":"))
        s2 = hashlib.sha256((key + b2 + salt).encode()).hexdigest()
        rr = httpx.post("https://app.mango-office.ru/vpbx/stats/result", timeout=180,
                        data={"vpbx_api_key": key, "sign": s2, "json": b2})
        if rr.status_code == 200 and rr.text.strip():
            break
    else:
        return {}
    out: dict[str, list[dict]] = defaultdict(list)
    for line in rr.text.strip().splitlines():
        p = line.split(";")
        if len(p) < 7:
            continue
        st, fin, ans, fext, fnum, tex, tnum = p[:7]
        if fext in ("20", "21") or tex in ("20", "21"):
            continue
        try:
            st, fin, ans = int(st or 0), int(fin or 0), int(ans or 0)
        except ValueError:
            continue
        ph = (fnum if not fext else tnum)
        ph = "".join(c for c in ph if c.isdigit())[-10:]
        if len(ph) != 10:
            continue
        out[ph].append({"ts": datetime.fromtimestamp(st, MSK).isoformat(timespec="minutes"),
                        "day": datetime.fromtimestamp(st, MSK).strftime("%Y-%m-%d"),
                        "dur": (fin - ans) if ans else 0,
                        "dir": "in" if not fext else "out"})
    return dict(out)


def _chats() -> dict[str, dict]:
    """Переписка: когда клиент писал последний раз и когда писали мы."""
    out: dict[str, dict] = {}
    try:
        with db.get_conn() as conn:
            for ph, ts in conn.execute(
                    "SELECT substr(phone,-10), MAX(ts) FROM wazzup_inbox "
                    "GROUP BY substr(phone,-10)"):
                out.setdefault(ph, {})["in"] = ts
            try:
                for ph, ts in conn.execute(
                        "SELECT substr(phone,-10), MAX(sent) FROM broadcast_queue "
                        "WHERE status='sent' GROUP BY substr(phone,-10)"):
                    out.setdefault(ph, {})["out"] = ts
            except Exception:
                pass
    except Exception:
        pass
    return out


def collect() -> dict:
    """Собрать всё, что нужно для решения. Пишется в файл, чтобы решать
    и применять можно было отдельно."""
    mk = _mk()
    try:
        tasks = []
        for mid in CALL_ADMINS + [CHAT_ADMIN]:
            off = 0
            while off < 1500:
                r = _retry(mk.get, "/v1/company/tasks",
                           {"date": date.today().isoformat(), "limit": 100,
                            "offset": off, "managerId": mid})
                ts = r.get("tasks") or []
                if not ts:
                    break
                tasks += [t for t in ts
                          if not t.get("isComplete") and not t.get("isCompleted")]
                off += 100
        # дедуплицируем: задача может прийти по нескольким менеджерам
        tasks = list({t["id"]: t for t in tasks}.values())
        log.info("открытых задач: %d", len(tasks))

        uids = sorted({t["userId"] for t in tasks if t.get("userId")})
        cls = {}
        rc = _retry(mk.get, "/v1/company/classes", {"limit": 500})
        for c in (rc.get("classes") if isinstance(rc, dict) else rc):
            cls[c["id"]] = c.get("name") or ""
        clients = {}
        for i, uid in enumerate(uids):
            try:
                u = _retry(mk.get, f"/v1/company/users/{uid}")
            except Exception:
                continue
            try:
                js = _retry(mk.get, "/v1/company/joins",
                            {"userId": uid, "limit": 30}).get("joins") or []
            except Exception:
                js = []
            try:
                cm = _retry(mk.get, "/v1/company/userComments",
                            {"userId": uid, "limit": 20})
                cm = cm.get("comments") or cm.get("userComments") or []
            except Exception:
                cm = []
            # Действия администраторов по клиенту: кто вёл, кто менял статус,
            # кто оформлял записи, оплаты и абонементы. Без этого непонятно,
            # почему клиент в таком состоянии и с кого спрашивать.
            try:
                pays = _retry(mk.get, "/v1/company/payments",
                              {"userId": uid, "limit": 50}).get("payments") or []
            except Exception:
                pays = []
            try:
                subs = _retry(mk.get, "/v1/company/userSubscriptions",
                              {"userId": uid, "limit": 30}).get("subscriptions") or []
            except Exception:
                subs = []
            actions = []
            for j in js:
                if j.get("managerId"):
                    actions.append({"who": NAMES.get(j["managerId"], j["managerId"]),
                                    "what": "запись в " + (cls.get(j["classId"], "")[:34]),
                                    "when": (j.get("createdAt") or "")[:10]})
            for pm in pays[:10]:
                if pm.get("managerId"):
                    actions.append({"who": NAMES.get(pm["managerId"], pm["managerId"]),
                                    "what": f"оплата {pm.get('summa')} ₽",
                                    "when": (pm.get("date") or "")[:10]})
            for sb in subs[:10]:
                if sb.get("managerId"):
                    actions.append({"who": NAMES.get(sb["managerId"], sb["managerId"]),
                                    "what": f"абонемент {sb.get('price')} ₽ "
                                            f"(оплачено {sb.get('payed')})",
                                    "when": (sb.get("sellDate") or "")[:10]})
            actions.sort(key=lambda x: x["when"] or "", reverse=True)

            enrolled = [cls.get(j["classId"], "") for j in js
                        if j.get("statusId") in ENROLLED_JOIN
                        and (cls.get(j["classId"], "")).startswith("2627")
                        and "Заявки" not in cls.get(j["classId"], "")]
            trial = [cls.get(j["classId"], "") for j in js
                     if j.get("statusId") in (58131, 58132)
                     and (cls.get(j["classId"], "")).startswith("2627")]
            clients[str(uid)] = {
                "name": u.get("name"), "state": u.get("clientStateId"),
                "phone": "".join(c for c in str(u.get("phone") or "")
                                 if c.isdigit())[-10:],
                "balance": u.get("balans"), "enrolled": enrolled, "trial": trial,
                "comments": [{"date": (c.get("createdAt") or "")[:10],
                              "text": (c.get("comment") or "")[:400]}
                             for c in cm[:8]],
                "actions": actions[:12],
                "changed": (u.get("stateChangedAt") or "")[:10],
                "responsible": [NAMES.get(x, x) for x in (u.get("responsibles") or [])]}
            if i % 50 == 0:
                log.info("клиентов собрано: %d/%d", i, len(uids))
        data = {"tasks": tasks, "clients": clients,
                "calls": _calls(), "chats": _chats(),
                "collected": datetime.now(MSK).isoformat(timespec="seconds")}
        json.dump(data, open(f"{SP}/taskplan.json", "w"), ensure_ascii=False)
        log.info("собрано: задач %d, клиентов %d", len(tasks), len(clients))
        return {"tasks": len(tasks), "clients": len(clients)}
    finally:
        mk.close()


# ── решение ───────────────────────────────────────────────────────────────
def decide() -> list[dict]:
    """По каждой задаче — что с ней сделать и почему. Ничего не меняет."""
    d = json.load(open(f"{SP}/taskplan.json"))
    tasks, clients = d["tasks"], d["clients"]
    calls, chats = d["calls"], d.get("chats", {})
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    by_user: dict[str, list] = defaultdict(list)
    for t in tasks:
        if t.get("userId"):
            by_user[str(t["userId"])].append(t)

    out = []
    for t in tasks:
        body = LABEL.sub("", t.get("body") or "").strip()
        cur = (t.get("beginDate") or "")[:10]
        mgrs = t.get("managerIds") or []
        uid = str(t.get("userId") or "")
        c = clients.get(uid, {})
        ph = c.get("phone") or ""
        mine = by_user.get(uid, [])
        hist = calls.get(ph, [])
        talks = [x for x in hist if x["dur"] >= 10]
        last_talk = max((x["day"] for x in talks), default="")
        last_try = max((x["day"] for x in hist if x["dir"] == "out"), default="")
        chat = chats.get(ph, {})
        act, day, mgr, why = "оставить", None, None, ""

        # 1. клиент отказался или помечен как некачественный
        if c.get("state") in DEAD_STATES:
            act, why = "закрыть", f"клиент в статусе {c.get('state')}"

        # 2. уже записан в группу нового года, а задача про то, чтобы записать
        elif c.get("enrolled") and ABOUT_ENROLL.search(body) and not MONEY.search(body):
            act = "закрыть"
            why = "уже записан: " + ", ".join(x[:34] for x in c["enrolled"][:2])

        # 3. дубль: несколько задач по клиенту про одно и то же
        elif len(mine) > 1 and ABOUT_ENROLL.search(body) and \
                t["id"] != max(x["id"] for x in mine
                               if ABOUT_ENROLL.search(x.get("body") or "")):
            act, why = "закрыть", f"дубль: по клиенту ещё {len(mine)-1} задач"

        else:
            # 4. срок назван в тексте
            want = deadline_in_text(body)
            if want and want != cur and want >= today:
                act, day, why = "срок", want, "срок назван в задаче"
            # 5. деньги у звонящего администратора
            elif MONEY.search(body) and any(m in CALL_ADMINS for m in mgrs):
                act, mgr, why = "передать", CHAT_ADMIN, "про деньги — Лизе"
            # 6. написали и ждём ответа
            elif WAITING.search(body) and cur <= today:
                act, day, why = "ждём", tomorrow, "написали, ответа нет"
            # 7. сегодня уже поговорили — не звонить второй раз
            elif last_talk >= today and ABOUT_ENROLL.search(body):
                act, day, why = "ждём", tomorrow, f"сегодня уже говорили ({last_talk})"
            # 8. записан на пробное — задача только подтвердить, и не сегодня
            elif c.get("trial") and ABOUT_ENROLL.search(body) and cur <= today:
                act, day = "срок", tomorrow
                why = "записан на пробное: " + (c["trial"][0][:34])

        # Приоритет внутри «оставить»: кого ещё ни разу не набирали, тот
        # ценнее любого повторного звонка — у непрозвоненного шанс нулевой.
        # Дальше по теплоте: с кем недавно говорили, тот уже в работе.
        if act == "оставить" and ABOUT_ENROLL.search(body):
            if not hist:
                rank = 0                       # не звонили ни разу
            elif not talks:
                rank = 1                       # набирали, но не дозвонились
            else:
                rank = 2                       # уже разговаривали
        else:
            rank = 3
        out.append({"rank": rank, "id": t["id"], "uid": uid, "name": c.get("name"),
                    "phone": ph, "cur": cur, "mgr_now": mgrs,
                    "act": act, "day": day, "mgr": mgr, "why": why,
                    "body": body[:120], "last_talk": last_talk,
                    "last_try": last_try, "chat_in": chat.get("in", "")[:10]})
    # Раскладка «оставить» по дням смен: сначала непрозвоненные, норма 40.
    SHIFTS = {232763: ["2026-08-21", "2026-08-25", "2026-08-26", "2026-08-27"],
              232805: ["2026-08-22", "2026-08-23", "2026-08-24", "2026-08-26", "2026-08-27"],
              202856: ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27"],
              CHAT_ADMIN: ["2026-08-21", "2026-08-22", "2026-08-23", "2026-08-24",
                           "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"]}
    CAP = {232763: 40, 232805: 40, 202856: 40, CHAT_ADMIN: 35}
    HOUR = {0: "09:00", 1: "11:00", 2: "14:00", 3: "16:00"}
    keep: dict[int, list] = defaultdict(list)
    for x in out:
        if x["act"] != "оставить":
            continue
        mgr = next((m for m in x["mgr_now"] if m in SHIFTS), None)
        if mgr:
            keep[mgr].append(x)
    for mgr, items in keep.items():
        items.sort(key=lambda x: (x["rank"], x["last_try"] or "", x["id"]))
        days, cap, load, di = SHIFTS[mgr], CAP[mgr], defaultdict(int), 0
        for x in items:
            if di < len(days) and load[days[di]] >= cap:
                di += 1
            day = days[di] if di < len(days) else "2026-09-02"
            load[day] += 1
            if day != x["cur"] or True:
                x["act"] = "разложить"
                x["day"] = day
                x["hour"] = HOUR[x["rank"]]
                x["why"] = ("не звонили ни разу" if x["rank"] == 0 else
                            "набирали, не дозвонились" if x["rank"] == 1 else
                            "уже разговаривали" if x["rank"] == 2 else "по плану смен")
    json.dump(out, open(f"{SP}/taskplan_decisions.json", "w"), ensure_ascii=False)
    return out


def apply() -> dict:
    """Применить решения."""
    dec = json.load(open(f"{SP}/taskplan_decisions.json"))
    mk = _mk()
    stat = defaultdict(int)
    try:
        for it in dec:
            if it["act"] == "оставить":
                stat["оставлено"] += 1
                continue
            try:
                t = _retry(mk.get, f"/v1/company/tasks/{it['id']}")
            except Exception:
                stat["ошибка"] += 1
                continue
            if t.get("isComplete") or t.get("isCompleted"):
                continue
            b = {k: t.get(k) for k in ("categoryId", "userId", "classIds",
                                       "filialIds", "ownerId", "reminds")}
            b = {k: v for k, v in b.items() if v is not None}
            body = LABEL.sub("", t.get("body") or "").strip()
            b["managerIds"] = [it["mgr"]] if it.get("mgr") else (t.get("managerIds") or [])
            b["isAllDay"] = False
            if it["act"] == "закрыть":
                b["body"] = (f"[закрыто: {it['why']}] {body}")[:250]
                b["beginDate"] = t.get("beginDate")
                b["endDate"] = t.get("endDate")
                b["isComplete"] = True
            else:
                day = it.get("day") or (t.get("beginDate") or "")[:10]
                hour = it.get("hour") or "09:00"
                b["body"] = body[:250]
                b["beginDate"] = f"{day}T{hour}:00+03:00"
                b["endDate"] = f"{day}T20:00:00+03:00"
            try:
                _retry(mk.post, f"/v1/company/tasks/{it['id']}", b)
                stat[it["act"]] += 1
            except Exception:
                stat["ошибка"] += 1
    finally:
        mk.close()
    return dict(stat)


def main():
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "decide"
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if cmd == "collect":
        print(collect())
    elif cmd == "apply":
        print(apply())
    else:
        dec = decide()
        by = defaultdict(list)
        for x in dec:
            by[x["act"]].append(x)
        for k, v in sorted(by.items(), key=lambda x: -len(x[1])):
            print(f"\n{k}: {len(v)}")
            for x in v[:5]:
                print(f"   {str(x['name'])[:24]:24s} {x['cur']}"
                      + (f" → {x['day']}" if x.get("day") else "")
                      + f" | {x['why']} | {x['body'][:60]}")


if __name__ == "__main__":
    main()
