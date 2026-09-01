# -*- coding: utf-8 -*-
"""Кому 01.09 ушло ложное «сегодня ждём» и какая у них настоящая дата.
Список строим заново из CRM — по правилу инцидента (см. napominaniya.py)."""
import sys, json, time, re, collections
sys.path.insert(0, "/home/user/kidsup")
from napominaniya import _mk, subj
SENT = json.load(open("/home/user/kidsup/docs/rabota/rassylka_1609.json"))
mk = _mk()
r = mk.get("/v1/company/lessons", params={"date": ["2026-09-01", "2026-09-01"],
                                          "includeRecords": "true", "limit": 500})
had = set()
for l in (r.get("lessons") if isinstance(r, dict) else r) or []:
    for rec in l.get("records") or []:
        had.add(rec["userId"])
classes = {}
c = mk.get("/v1/company/classes", params={"limit": 500})
for x in (c.get("classes") if isinstance(c, dict) else c) or []:
    classes[x["id"]] = x.get("name") or "?"
fam = collections.defaultdict(list)
for x in SENT:
    if x["uid"] in had:
        continue
    rr = mk.get("/v1/company/lessonRecords", params={
        "userId": x["uid"], "date": ["2026-09-02", "2026-10-31"],
        "includeLessons": "true", "limit": 50})
    rows = (rr.get("lessonRecords") if isinstance(rr, dict) else rr) or []
    time.sleep(0.3)
    nxt = sorted(((y.get("lesson") or {}).get("date"),
                  ((y.get("lesson") or {}).get("beginTime") or "")[:5],
                  classes.get((y.get("lesson") or {}).get("classId"), ""),
                  bool(y.get("test"))) for y in rows if (y.get("lesson") or {}).get("date"))
    if not nxt:
        continue
    d, t, g, test = nxt[0]
    fam[x["phone"]].append({"name": re.sub(r"\s*\(.*?\)", "", x["name"]).strip(),
                            "date": d, "time": t, "subject": subj(g), "test": test})
mk.close()
MON = {"09": "сентября", "10": "октября"}
out = {}
for ph, items in fam.items():
    parts, seen = [], set()
    for i in sorted(items, key=lambda i: (i["date"], i["time"])):
        k = (i["name"], i["date"], i["time"], i["subject"])
        if k in seen:
            continue
        seen.add(k)
        dd = f'{int(i["date"][8:10])} {MON.get(i["date"][5:7], "")}'
        parts.append(f'{i["name"]} — {dd} в {i["time"]}, {i["subject"]}')
    first = " ; ".join(parts)
    out[ph] = {"kids": ", ".join(i["name"] for i in items), "when": first,
               "text": ("Здравствуйте! Это KidsUP на Бульваре Рокоссовского 🌿\n"
                        "Вчера мы прислали вам напоминание про занятие — оно ушло по ошибке, "
                        "извините. Ваша запись не менялась.\n"
                        f"Ждём вас: {first}.\n"
                        "Адрес прежний: б-р Маршала Рокоссовского, 6 к1В, 7-й подъезд "
                        "(домофон 12), 2 этаж, из лифта налево, код двери 667788#.\n"
                        "Если удобнее другой день — напишите здесь, подберём 💛")}
json.dump(out, open("/home/user/kidsup/docs/rabota/izvinenie_0109.json", "w"),
          ensure_ascii=False, indent=1)
print("семей:", len(out))
for ph, m in out.items():
    print(f'  {ph}  {m["when"]}')
