# -*- coding: utf-8 -*-
"""Напоминания «сегодня/завтра ждём» — единственный правильный способ собрать список.

ИНЦИДЕНТ 01.09.2026. Напоминание «сегодня ждём вас в 18:00» ушло 45 семьям,
собранным ПО СОСТАВУ ГРУПП (join в класс + расписание группы). У 24 из 45
занятия в этот день не было: они записаны на 3, 4, 5, 8, 14, 15 сентября —
join в группу уже есть, а первое занятие ещё впереди. Деева Лада ответила
«Здравствуйте! А я записана на 8.09», Королева и Лагутина — то же самое.
Для семьи это выглядит так, будто мы не помним, на когда её записали.

Правило: адресат напоминания берётся ТОЛЬКО из /lessonRecords за конкретную
дату. Join в группу, расписание класса и «ходит по вторникам» — не источник:
в МойКлассе ребёнок числится в группе задолго до своего первого занятия.

Использование:
    from napominaniya import spisok
    rows = spisok("2026-09-02")              # все занятия дня
    rows = spisok("2026-09-02", only_test=True)   # только первые (пробные)
    rows = spisok("2026-09-02", from_time="16:00")
"""
import sys, time, re, collections
sys.path.insert(0, "/home/user/kidsup")
from app import db
from app.moyklass_client import MoyklassClient

SUBJ = {"ПШ": "подготовка к школе", "АЯ": "английский", "ЛГ": "логопед",
        "ИЗО": "ИЗО", "МА": "ментальная арифметика", "РР": "раннее развитие",
        "Мини-сад": "мини-сад", "Нулевой": "нулевой класс",
        "ШАХ": "шахматы", "СКЧ": "скорочтение", "КАЛ": "каллиграфия",
        "ДУ": "дошкольный университет", "РОБ": "робототехника"}


def _mk():
    for _ in range(6):
        try:
            c = MoyklassClient(db.get_setting("moyklass_api_key")); c.authenticate(); return c
        except Exception:
            time.sleep(4)
    raise RuntimeError("МойКласс недоступен")


def subj(group: str) -> str:
    head = re.sub(r"^\d+_", "", group or "").split("_")[0].strip()
    if head.startswith("РР"):
        return "раннее развитие"
    return SUBJ.get(head.split()[0], head)


def spisok(day: str, only_test: bool = False, from_time: str = "",
           mk: MoyklassClient | None = None) -> list[dict]:
    """Кого реально ждут в этот день. Один элемент — один ребёнок на занятии."""
    own = mk is None
    mk = mk or _mk()
    try:
        classes = {}
        c = mk.get("/v1/company/classes", params={"limit": 500})
        for x in (c.get("classes") if isinstance(c, dict) else c) or []:
            classes[x["id"]] = x.get("name") or "?"
        r = mk.get("/v1/company/lessons", params={
            "date": [day, day], "includeRecords": "true", "limit": 500})
        lessons = (r.get("lessons") if isinstance(r, dict) else r) or []
        out, names = [], {}
        for l in lessons:
            begin = (l.get("beginTime") or "")[:5]
            if from_time and begin < from_time:
                continue
            for rec in l.get("records") or []:
                if only_test and not rec.get("test"):
                    continue
                uid = rec.get("userId")
                if uid not in names:
                    try:
                        u = mk.get(f"/v1/company/users/{uid}")
                        names[uid] = (u.get("name") or "", u.get("phone") or "")
                    except Exception:
                        names[uid] = ("", "")
                    time.sleep(0.25)
                nm, ph = names[uid]
                if not re.fullmatch(r"7\d{10}", ph or ""):
                    continue          # без нормального номера писать некуда
                out.append({"uid": uid, "name": nm, "phone": ph, "time": begin,
                            "group": classes.get(l.get("classId"), "?"),
                            "subject": subj(classes.get(l.get("classId"), "")),
                            "test": bool(rec.get("test")), "date": day})
        out.sort(key=lambda x: (x["time"], x["name"]))
        return out
    finally:
        if own:
            mk.close()


def po_semyam(rows: list[dict]) -> dict[str, dict]:
    """Одно сообщение на семью: у одного телефона может быть два ребёнка."""
    fam = collections.defaultdict(list)
    for r in rows:
        fam[r["phone"]].append(r)
    out = {}
    for ph, items in fam.items():
        by = collections.defaultdict(list)
        for it in items:
            by[re.sub(r"\s*\(.*?\)", "", it["name"]).strip()].append((it["time"], it["subject"]))
        parts = [f"{nm} — " + ", ".join(f"{t} {s}" for t, s in sorted(set(lst)))
                 for nm, lst in by.items()]
        out[ph] = {"kids": ", ".join(by), "body": "; ".join(parts),
                   "first": min(t for lst in by.values() for t, _ in lst),
                   "test": any(i["test"] for i in items)}
    return out


def proverit(rows: list[dict], day: str) -> list[dict]:
    """Предохранитель: у каждого адресата обязана быть запись именно на day."""
    return [r for r in rows if r.get("date") != day]


def otpravit(day: str, from_time: str = "", only_test: bool = False,
             dry: bool = True) -> dict:
    """Отправка напоминаний с предохранителем: перед каждым сообщением ещё раз
    сверяемся с CRM, что у этого телефона занятие ИМЕННО в этот день.
    Список строится тут же — подсунуть готовый список нельзя, это и была
    причина инцидента 01.09."""
    from app import wazzup
    rows = spisok(day, only_test=only_test, from_time=from_time)
    fam = po_semyam(rows)
    ok, sent = {r["phone"] for r in rows if r["date"] == day}, []
    for ph, m in fam.items():
        if ph not in ok:
            continue
        text = ("Здравствуйте! Это KidsUP на Бульваре Рокоссовского 🌿\n"
                f"Напоминаем: сегодня ждём вас — {m['body']}.\n"
                "Адрес: б-р Маршала Рокоссовского, 6 к1В, 7-й подъезд (домофон 12), "
                "2 этаж, из лифта налево. Код от двери центра 667788#.\n"
                "Приходите за 10 минут — переодеться и познакомиться с педагогом. "
                "Если планы изменились, напишите здесь 💛")
        if dry:
            sent.append({"phone": ph, "text": text})
        else:
            wazzup.send(ph, text)
            sent.append({"phone": ph})
            time.sleep(0.5)
    return {"день": day, "семей": len(fam), "отправлено": len(sent), "dry": dry,
            "сообщения": sent if dry else []}


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else None
    if not day:
        from datetime import date
        day = date.today().isoformat()
    rows = spisok(day, from_time=(sys.argv[2] if len(sys.argv) > 2 else ""))
    fam = po_semyam(rows)
    print(f"{day}: занятий у {len(rows)} детей, семей {len(fam)}, "
          f"первых занятий {sum(1 for r in rows if r['test'])}")
    for ph, m in fam.items():
        print(f'  {ph} {m["first"]} {m["body"]}{"  ★первое" if m["test"] else ""}')
