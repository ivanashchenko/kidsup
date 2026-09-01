# -*- coding: utf-8 -*-
"""Доля оплат от пришедших на пробное — метрика набора (решение владельца 01.09:
держится выше 40% — набор идёт). Плюс кто приходит на пробное сегодня.

Выход: docs/rabota/metrika_probnye.json
"""
import sys, json, time, datetime as dt
sys.path.insert(0, "/home/user/kidsup")
from app import db
from app.moyklass_client import MoyklassClient

TODAY = dt.date(2026, 9, 2)
WEEK_FROM = TODAY - dt.timedelta(days=7)          # 26.08
SCHOOL_FROM = dt.date(2026, 8, 31)                # неделя открытых уроков

mk = None
for a in range(6):
    try:
        mk = MoyklassClient(db.get_setting("moyklass_api_key")); mk.authenticate(); break
    except Exception as e:
        print("auth retry", e); time.sleep(5)

def paged(path, key, **params):
    out, off = [], 0
    while True:
        r = mk.get(path, params={"limit": 500, "offset": off, **params})
        rows = (r.get(key) if isinstance(r, dict) else r) or []
        out.extend(rows)
        if len(rows) < 500: break
        off += 500
        if off > 20000: break
    return out

# 1. Пробные за неделю: записи на занятия с флагом test и отметкой визита
recs = paged("/v1/company/lessonRecords", "lessonRecords",
             date=[WEEK_FROM.isoformat(), (TODAY - dt.timedelta(days=1)).isoformat()])
print("lessonRecords за неделю:", len(recs))
lesson_ids = {r["lessonId"] for r in recs}
lessons = {}
for l in paged("/v1/company/lessons", "lessons",
               date=[WEEK_FROM.isoformat(), TODAY.isoformat()], includeRecords="true"):
    lessons[l["id"]] = l
print("lessons:", len(lessons))

# классы/группы для названий
classes = {c["id"]: c for c in paged("/v1/company/classes", "classes")}
courses = {c["id"]: c for c in (mk.get("/v1/company/courses") or [])}

def gname(l):
    c = classes.get(l.get("classId")) or {}
    return c.get("name") or "?"

trial = {}   # userId -> {date, group, visit}
for r in recs:
    if not r.get("test"):
        continue
    l = lessons.get(r["lessonId"]) or {}
    d = l.get("date") or ""
    u = r["userId"]
    t = trial.setdefault(u, {"dates": [], "groups": set(), "visited": False})
    t["dates"].append(d); t["groups"].add(gname(l))
    if r.get("visit"): t["visited"] = True
print("пробных записей (уник. детей):", len(trial),
      "| пришли:", sum(1 for t in trial.values() if t["visited"]))

# 2. Оплаты за период
pays = paged("/v1/company/payments", "payments",
             date=[WEEK_FROM.isoformat(), TODAY.isoformat()])
paid_by_user = {}
for p in pays:
    if p.get("userId") and (p.get("summa") or 0) > 0 and p.get("optype") == "income":
        paid_by_user.setdefault(p["userId"], []).append(
            {"date": p.get("date"), "summa": p.get("summa")})
print("оплат за период:", len(pays), "| плательщиков:", len(paid_by_user))

# 3. Карточки пришедших
def user(uid):
    for a in range(3):
        try:
            return mk.get(f"/v1/company/users/{uid}")
        except Exception:
            time.sleep(2)
    return {}

rows = []
for uid, t in trial.items():
    u = user(uid); time.sleep(0.25)
    st = u.get("clientStateId")
    rows.append({"uid": uid, "name": u.get("name"), "phone": u.get("phone"),
                 "state": st, "dates": sorted(t["dates"]), "groups": sorted(t["groups"]),
                 "visited": t["visited"],
                 "paid": paid_by_user.get(uid, []),
                 "paid_after_trial": any(pp["date"] >= min(t["dates"]) for pp in paid_by_user.get(uid, []))})

def share(rs):
    came = [r for r in rs if r["visited"]]
    paid = [r for r in came if r["paid_after_trial"]]
    return {"пришли": len(came), "оплатили": len(paid),
            "доля": round(100 * len(paid) / len(came)) if came else None}

week = share(rows)
school = share([r for r in rows if r["dates"] and min(r["dates"]) >= SCHOOL_FROM.isoformat()])
by_day = {}
for r in rows:
    if r["visited"]:
        d = min(r["dates"])
        b = by_day.setdefault(d, {"пришли": 0, "оплатили": 0})
        b["пришли"] += 1; b["оплатили"] += int(r["paid_after_trial"])
print("НЕДЕЛЯ 26.08–01.09:", week); print("с 31.08:", school); print(json.dumps(by_day, ensure_ascii=False))

# 4. Кто приходит сегодня на пробное
today_rows = []
for l in lessons.values():
    if l.get("date") != TODAY.isoformat():
        continue
    for r in l.get("records") or []:
        if r.get("test"):
            u = user(r["userId"]); time.sleep(0.25)
            today_rows.append({"uid": r["userId"], "name": u.get("name"), "phone": u.get("phone"),
                               "state": u.get("clientStateId"), "group": gname(l),
                               "time": f'{l.get("beginTime")}–{l.get("endTime")}',
                               "paid": bool(paid_by_user.get(r["userId"]))})
today_rows.sort(key=lambda x: x["time"])
print("сегодня на пробное:", len(today_rows))
for r in today_rows: print("  ", r["time"], r["group"], "—", r["name"], "оплачено" if r["paid"] else "")

mk.close()
json.dump({"week": week, "school": school, "by_day": by_day, "rows": rows, "today": today_rows},
          open("/home/user/kidsup/docs/rabota/metrika_probnye.json", "w"), ensure_ascii=False, indent=1)
print("saved")
