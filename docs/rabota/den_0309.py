# -*- coding: utf-8 -*-
"""Данные для плана на четверг 03.09: расписание дня, переназначение задач Ира→Аня,
список реактивации бывших плательщиков с возрастом."""
import sys, json, time, datetime as dt
sys.path.insert(0, "/home/user/kidsup/docs/rabota")
from napominaniya import _mk, subj
mk = _mk(); TODAY = "2026-09-03"
c = mk.get("/v1/company/classes", params={"limit": 500})
classes = {x["id"]: x.get("name") or "?" for x in ((c.get("classes") if isinstance(c, dict) else c) or [])}
names = {}
def nm(uid):
    if uid not in names:
        try:
            u = mk.get(f"/v1/company/users/{uid}")
            bd = next((a.get("value") for a in u.get("attributes") or [] if a.get("attributeAlias") == "birthday"), None)
            names[uid] = {"name": u.get("name") or str(uid), "phone": u.get("phone") or "", "state": u.get("clientStateId"), "bd": bd}
        except Exception:
            names[uid] = {"name": str(uid), "phone": "", "state": None, "bd": None}
        time.sleep(0.22)
    return names[uid]
# 1. расписание дня
r = mk.get("/v1/company/lessons", params={"date": [TODAY, TODAY], "includeRecords": "true", "limit": 500})
lessons = []
for l in sorted((r.get("lessons") if isinstance(r, dict) else r) or [], key=lambda x: (x.get("beginTime") or "", x.get("classId") or 0)):
    recs = l.get("records") or []
    lessons.append({"time": (l.get("beginTime") or "")[:5], "group": classes.get(l.get("classId"), "?"), "subject": subj(classes.get(l.get("classId"), "")),
                    "kids": [{**nm(x["userId"]), "test": bool(x.get("test"))} for x in recs]})
print("занятий сегодня:", len(lessons), "| детей:", sum(len(l["kids"]) for l in lessons), "| пробных:", sum(1 for l in lessons for k in l["kids"] if k["test"]))
# 2. задачи Иры на сегодня → Аня
moved = []
t = mk.get("/v1/company/tasks", params={"managerId": 232763, "isComplete": "false", "limit": 200})
for task in (t.get("tasks") if isinstance(t, dict) else t) or []:
    if task.get("isComplete"): continue
    if (task.get("beginDate") or "")[:10] > TODAY: continue
    body = {k: task[k] for k in ("body", "beginDate", "endDate", "userId", "categoryId") if task.get(k) is not None}
    body["managerIds"] = [232805]; body["isComplete"] = False
    try:
        mk.post(f"/v1/company/tasks/{task['id']}", body); moved.append(task["id"])
    except Exception as e:
        print("не перенеслась", task["id"], str(e)[:60])
    time.sleep(0.25)
print("задач перевешено с Иры на Аню:", len(moved))
# 3. реактивация: бывшие плательщики в недозвоне/думает + возраст
SP = json.load(open("/home/user/kidsup/docs/rabota/spisok3_payers.json"))
today = dt.date(2026, 9, 3)
react = []
for x in SP:
    info = nm(x["uid"])
    age = None
    if info["bd"]:
        try:
            y, m, d = map(int, info["bd"][:10].split("-")); age = round((today - dt.date(y, m, d)).days / 365.25, 1)
        except Exception: pass
    react.append({"uid": x["uid"], "name": info["name"], "phone": info["phone"], "state": info["state"], "age": age, "changed": x.get("changed")})
print("реактивация:", len(react), "| с возрастом:", sum(1 for r in react if r["age"] is not None))
mk.close()
json.dump({"lessons": lessons, "moved": moved, "react": react}, open("/home/user/kidsup/docs/rabota/den_0309.json", "w"), ensure_ascii=False, indent=1)
print("saved")
