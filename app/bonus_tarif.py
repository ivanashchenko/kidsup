"""Расчёт бонусов администраторов по действующему тарифу (docs/bonusy_adminov.html).

Тариф с 17.08.2026: состоявшееся пробное 300 ₽, покупка абонемента новым учеником
800 ₽, возврат «спящего» 500 ₽, второй предмет 400 ₽. Гарантия за смену 1 900 ₽:
если сдельная меньше, платится гарантия.

Строка «разговор дольше минуты 50 ₽» действовала только в августе и отменена 11.09:
журнал АТС меряет, за каким аппаратом сидел администратор, а не с кем он говорил.

Бонус за пробное начисляется по дате СОСТОЯВШЕГОСЯ занятия, а не записи, поэтому
записи берём с начала тарифа, а начисляем в тот период, куда попал визит.

Считает в фоновом потоке (обход CRM занимает минуты), результат кладёт в
data/bonus_tarif.json. Роуты — в main: POST /api/bonus/tarif/run, GET /api/bonus/tarif.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import sync
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.bonus_tarif")

MGR = {232805: "Аня", 232763: "Ира", 202856: "Лена", 154181: "Лиза"}
JOIN_FROM = "2026-08-17"          # начало действия тарифа
RATE_TRIAL, RATE_NEW, RATE_BACK, RATE_SECOND = 300, 800, 500, 400
RATE_SHIFT = 1900                 # гарантия за смену
SLEEPING_BEFORE = "2026-05-01"    # не платил с этой даты — «спящий»

OUT = Path(__file__).resolve().parent.parent / "data" / "bonus_tarif.json"

_lock = threading.Lock()
_state: dict = {"running": False, "step": "", "error": "", "data": None}

SUBJ = {"ПШ": "подготовка к школе", "АЯ": "английский", "ЛГ": "логопед",
        "ИЗО": "ИЗО", "МА": "ментальная арифметика", "РР": "раннее развитие",
        "Мини-сад": "мини-сад", "Нулевой": "нулевой класс", "ШАХ": "шахматы",
        "СКЧ": "скорочтение", "КАЛ": "каллиграфия", "ДУ": "дошкольный университет",
        "РОБ": "робототехника"}


def _subject(group: str) -> str:
    head = re.sub(r"^\d+_", "", group or "").split("_")[0].strip()
    for k, v in SUBJ.items():
        if head.startswith(k):
            return v
    return head or "—"


def _step(text: str) -> None:
    with _lock:
        _state["step"] = text
    log.info("bonus_tarif: %s", text)


def collect(p0: str, p1: str) -> dict:
    """Считает бонусы за период [p0, p1] включительно. Блокирующая, минуты."""
    mk = MoyklassClient(sync.get_api_key())
    mk.authenticate()
    try:
        _step("группы")
        rc = mk.get("/v1/company/classes", {"limit": 500})
        classes = {c["id"]: c for c in (rc.get("classes") if isinstance(rc, dict) else rc) or []}
        work = [cid for cid, cl in classes.items()
                if (cl.get("name") or "").startswith("2627_")
                and cl.get("status") == "opened" and "Заявки" not in (cl.get("name") or "")]

        _step(f"записи по {len(work)} группам")
        joins = []
        for cid in work:
            j = mk.get("/v1/company/joins", {"classId": cid, "limit": 500})
            time.sleep(0.25)
            for x in (j.get("joins") if isinstance(j, dict) else j) or []:
                if x.get("managerId") in MGR and (x.get("createdAt") or "")[:10] >= JOIN_FROM \
                        and x.get("statusId") != 1:
                    joins.append({"uid": x["userId"], "cid": cid,
                                  "cls": classes[cid].get("name"),
                                  "subject": _subject(classes[cid].get("name")),
                                  "who": MGR[x["managerId"]],
                                  "created": x["createdAt"][:10]})

        names: dict[int, str] = {}

        def uname(uid: int) -> str:
            if uid not in names:
                try:
                    u = mk.get(f"/v1/company/users/{uid}")
                    names[uid] = u.get("name") or str(uid)
                except Exception:
                    names[uid] = str(uid)
                time.sleep(0.2)
            return names[uid]

        _step(f"занятия по {len({j['uid'] for j in joins})} детям")
        recs_cache: dict[int, list] = {}

        def recs(uid: int) -> list:
            if uid not in recs_cache:
                try:
                    rr = mk.get("/v1/company/lessonRecords",
                                {"userId": uid, "date": [JOIN_FROM, p1],
                                 "includeLessons": "true", "limit": 100})
                except Exception:
                    rr = {}
                time.sleep(0.22)
                recs_cache[uid] = [
                    {"date": (y.get("lesson") or {}).get("date"),
                     "cid": (y.get("lesson") or {}).get("classId"),
                     "test": bool(y.get("test")), "visit": bool(y.get("visit"))}
                    for y in ((rr.get("lessonRecords") if isinstance(rr, dict) else rr) or [])]
            return recs_cache[uid]

        trials, seen = [], set()
        for j in joins:
            mine = [r for r in recs(j["uid"]) if r["cid"] == j["cid"]]
            done = [r for r in mine if r["test"] and r["visit"] and r["date"]
                    and p0 <= r["date"] <= p1]
            if done and (j["uid"], j["cid"]) not in seen:
                seen.add((j["uid"], j["cid"]))
                trials.append({"uid": j["uid"], "name": uname(j["uid"]), "who": j["who"],
                               "subject": j["subject"],
                               "date": min(r["date"] for r in done)})

        _step("оплаты")
        pays, off = [], 0
        while True:
            r = mk.get("/v1/company/payments", {"limit": 500, "offset": off, "date": [p0, p1]})
            ps = (r.get("payments") if isinstance(r, dict) else r) or []
            for p in ps:
                if p.get("optype") == "income" and (p.get("summa") or 0) > 0 and p.get("userId"):
                    pays.append({"uid": p["userId"], "who": MGR.get(p.get("managerId")),
                                 "date": p["date"], "summa": p["summa"]})
            if len(ps) < 500:
                break
            off += 500
        pays_total = len(pays)
        pays_no_mgr = sum(1 for p in pays if not p["who"])
        pays = [p for p in pays if p["who"]]

        _step(f"история оплат по {len({p['uid'] for p in pays})} семьям")
        fam: dict[tuple, dict] = {}
        for p in pays:
            f = fam.setdefault((p["uid"], p["who"]),
                               {"uid": p["uid"], "who": p["who"], "sum": 0, "dates": []})
            f["sum"] += p["summa"]
            f["dates"].append(p["date"])
        for f in fam.values():
            try:
                r = mk.get("/v1/company/payments",
                           {"userId": f["uid"], "limit": 200,
                            "date": ["2020-01-01", (date.fromisoformat(p0)
                                                    - timedelta(days=1)).isoformat()]})
            except Exception:
                r = {}
            time.sleep(0.22)
            hist = sorted(x["date"] for x in ((r.get("payments") if isinstance(r, dict) else r) or [])
                          if x.get("optype") == "income" and (x.get("summa") or 0) > 0)
            f["kind"] = ("новый" if not hist
                         else "возврат спящего" if hist[-1] < SLEEPING_BEFORE
                         else "продление")
            f["last_pay_before"] = hist[-1] if hist else None
            f["name"] = uname(f["uid"])

        _step("вторые предметы")
        second = []
        for f in fam.values():
            try:
                j = mk.get("/v1/company/joins", {"userId": f["uid"], "limit": 50})
            except Exception:
                j = {}
            time.sleep(0.22)
            js = [x for x in ((j.get("joins") if isinstance(j, dict) else j) or [])
                  if x.get("statusId") == 2 and x.get("classId") in classes
                  and (classes[x["classId"]].get("name") or "").startswith("2627_")]
            subs: dict[str, dict] = {}
            for x in sorted(js, key=lambda x: x.get("createdAt") or ""):
                subs.setdefault(_subject(classes[x["classId"]].get("name")), x)
            for s, x in list(subs.items())[1:]:
                d = (x.get("createdAt") or "")[:10]
                if p0 <= d <= p1 and x.get("managerId") in MGR:
                    second.append({"uid": f["uid"], "name": f["name"],
                                   "who": MGR[x["managerId"]], "subject": s, "date": d})
    finally:
        mk.close()

    # смены: плановый график владельца + дни с фактической работой в CRM
    from . import db as _db
    sched = json.loads(_db.get_setting("admin_schedule") or "{}")
    act: dict[str, set] = defaultdict(set)
    for j in joins:
        if p0 <= j["created"] <= p1:
            act[j["created"]].add(j["who"])
    for p in pays:
        if p0 <= p["date"] <= p1:
            act[p["date"]].add(p["who"])
    plan: dict[str, list] = {}
    d = date.fromisoformat(p0)
    while d <= date.fromisoformat(p1):
        k = d.isoformat()
        plan[k] = [MGR.get(x, str(x)) for x in sched.get(k, [])]
        d += timedelta(days=1)
    shifts_plan, shifts_fact = Counter(), Counter()
    for k, who_list in plan.items():
        for n in who_list:
            shifts_plan[n] += 1
        for n in act.get(k, ()):
            shifts_fact[n] += 1

    people = {}
    for who in sorted({*(t["who"] for t in trials), *(f["who"] for f in fam.values()),
                       *(s["who"] for s in second), *shifts_plan, *shifts_fact}):
        t = [x for x in trials if x["who"] == who]
        new = [f for f in fam.values() if f["who"] == who and f["kind"] == "новый"]
        back = [f for f in fam.values() if f["who"] == who and f["kind"] == "возврат спящего"]
        sec = [x for x in second if x["who"] == who]
        sdel = (len(t) * RATE_TRIAL + len(new) * RATE_NEW
                + len(back) * RATE_BACK + len(sec) * RATE_SECOND)
        sh_plan, sh_fact = shifts_plan.get(who, 0), shifts_fact.get(who, 0)
        shifts = sh_plan or sh_fact
        people[who] = {
            "trials": t, "new": new, "back": back, "second": sec,
            "sdelnaya": sdel, "shifts_plan": sh_plan, "shifts_fact": sh_fact,
            "guarantee": shifts * RATE_SHIFT, "to_pay": max(sdel, shifts * RATE_SHIFT),
        }

    return {"period": [p0, p1], "built": datetime.now().isoformat(timespec="seconds"),
            "rates": {"пробное": RATE_TRIAL, "новый абонемент": RATE_NEW,
                      "возврат спящего": RATE_BACK, "второй предмет": RATE_SECOND,
                      "гарантия за смену": RATE_SHIFT},
            "people": people, "plan": plan,
            "act": {k: sorted(v) for k, v in act.items()},
            "pays_total": pays_total, "pays_no_manager": pays_no_mgr,
            "joins": len(joins)}


def _run(p0: str, p1: str) -> None:
    try:
        data = collect(p0, p1)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        with _lock:
            _state.update(running=False, step="готово", error="", data=data)
    except Exception as e:
        log.exception("bonus_tarif failed")
        with _lock:
            _state.update(running=False, step="ошибка", error=str(e)[:300])


def start(p0: str, p1: str) -> dict:
    with _lock:
        if _state["running"]:
            return {"ok": False, "running": True, "step": _state["step"]}
        _state.update(running=True, step="старт", error="", data=None)
    threading.Thread(target=_run, args=(p0, p1), daemon=True).start()
    return {"ok": True, "running": True}


def status() -> dict:
    with _lock:
        st = {"running": _state["running"], "step": _state["step"], "error": _state["error"]}
    if not st["running"] and OUT.exists():
        st["data"] = json.loads(OUT.read_text(encoding="utf-8"))
    return st
