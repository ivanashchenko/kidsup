# -*- coding: utf-8 -*-
"""Напоминание тем, у кого сегодня занятие с 16:00. Одно сообщение на семью."""
import sys, json, collections, re
sys.path.insert(0,"/home/user/kidsup")
ROWS=json.load(open("/home/user/kidsup/docs/rabota/rassylka_1609.json"))
SUBJ={"ПШ":"подготовка к школе","АЯ":"английский","ЛГ":"логопед",
      "ИЗО":"ИЗО","МА":"ментальная арифметика","РР":"раннее развитие"}
def subj(g):
    h=g.split("_")[0].strip()
    if h.startswith("РР"): return "раннее развитие"
    return SUBJ.get(h.split()[0], h)
fam=collections.defaultdict(list)
for r in ROWS:
    ph=(r.get("phone") or "").strip()
    if not re.fullmatch(r"7\d{10}", ph): continue
    fam[ph].append(r)
msgs={}
for ph, items in fam.items():
    items.sort(key=lambda x: x["time"])
    # ребёнок → его занятия
    by=collections.defaultdict(list)
    for it in items:
        by[it["name"]].append((it["time"], subj(it["group"])))
    parts=[]
    for name, lst in by.items():
        nm=re.sub(r"\s*\(.*?\)","",name).strip()
        lst.sort()
        parts.append(f"{nm} — " + ", ".join(f"{t} {s}" for t,s in lst))
    body = "; ".join(parts)
    wa = ("Здравствуйте! Это KidsUP на Бульваре Рокоссовского 🌿\n"
          f"Напоминаем: сегодня ждём вас — {body}.\n"
          "Адрес: б-р Маршала Рокоссовского, 6 к1В, 7-й подъезд, 2 этаж, "
          "из лифта налево. Код от двери центра 667788#.\n"
          "Приходите за 10 минут — переодеться и познакомиться с педагогом. "
          "Если планы изменились, напишите здесь 💛")
    first = min(t for _, lst in by.items() for t, _ in lst)
    kids = ", ".join(re.sub(r"\s*\(.*?\)","",n).strip() for n in by)
    sms = (f"KidsUP: сегодня ждем {kids} в {first}. "
           "Рокоссовского 6к1В, 7 подъезд, 2 этаж, код двери 667788#. "
           "Вопросы: 79160170918")
    msgs[ph]={"wa":wa,"sms":sms[:300],"kids":kids,"first":first}
json.dump(msgs, open("/home/user/kidsup/docs/rabota/rassylka_msgs.json","w"), ensure_ascii=False)
print(f"семей: {len(msgs)}")
for ph,m in list(msgs.items())[:4]:
    print(f'\n--- {ph} ({m["kids"]}, первое в {m["first"]})')
    print(m["wa"])
    print("SMS:", m["sms"], f"[{len(m['sms'])} симв]")
