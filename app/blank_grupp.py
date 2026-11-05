"""Списки детей по группам «как на бумаге» (просьба Иры 31.10.2026).

Модель строится из живого МойКласса: открытые группы, педагог по расписанию,
записи детей (joins), отметки на занятиях (lessonRecords) за окно дат.
Одна вкладка = педагог (+ все его направления): сначала индивидуальные занятия
(логопед), потом группы, последний блок — лист ожидания.

build_model(...) -> dict  — общая модель для Excel и для страницы /spiski.
to_xlsx(model, path)      — файл .xlsx по бланку администратора.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict

WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
WD_FULL = {"пн": "ПОНЕДЕЛЬНИК", "вт": "ВТОРНИК", "ср": "СРЕДА", "чт": "ЧЕТВЕРГ",
           "пт": "ПЯТНИЦА", "сб": "СУББОТА", "вс": "ВОСКРЕСЕНЬЕ"}
ACTIVE = {2, 5, 58132, 58131}          # учится, отработка, записан на пробное, посетил пробное
WAITING = {50509, 83760, 99336}        # заявка, подтвердил, не пришёл на пробное
SLOTS = 10
# Ира 31.10: «Логопед и ПШ_Татьяна» — индивидуальные Татьяны на одной вкладке с её ПШ
LOGO_TEACHER = {"Татьяна": "Татьяна Першина"}

COURSE_SHORT = {
    "Раннее развитие": "РР", "Подготовка к школе": "ПШ", "Английский язык": "Английский",
    "Английский детский сад": "Мини-сад и НК", "ИЗО-студия": "ИЗО", "Логопед": "Логопед",
    "Ментальная арифметика": "Ментальная арифметика", "Робототехника": "Робототехника",
    "Шахматы": "Шахматы",
}
SKIP_COURSES = {"Праздник", "Заявки Roistat", "Набор на новые предметы 26/27"}
# Ира 04.11: мини-сад и нулевой класс — бланк по дням недели (один блок = один день)
DAY_COURSE = "Английский детский сад"
DAY_WD = [("пн", "mo", "ПН"), ("вт", "tu", "ВТ"), ("ср", "we", "СР"), ("чт", "th", "ЧТ"), ("пт", "fr", "ПТ")]
TRIAL_ST = (58132, 58131)               # записан на пробное / посетил пробное
DAYS_CHOICES = ["пн", "вт", "ср", "чт", "пт", "сб", "вс", "пн-ср", "пн-чт", "пн-пт",
                "вт-чт", "вт-пт", "ср-пт", "ср-сб", "пн-сб", "чт-сб"]


def _list(x, key):
    return x if isinstance(x, list) else (x or {}).get(key, [])


def _age(bd: str | None, today: dt.date) -> str:
    if not bd:
        return ""
    try:
        b = dt.date.fromisoformat(bd[:10])
    except ValueError:
        return ""
    m = (today.year - b.year) * 12 + today.month - b.month - (1 if today.day < b.day else 0)
    return f"{m // 12}.{m % 12}" if m >= 0 else ""


def _bd(u: dict) -> str:
    for a in u.get("attributes") or []:
        if a.get("attributeAlias") == "birthday" and a.get("value"):
            return a["value"][:10]
    return ""


def _phone(p: str | None) -> str:
    d = re.sub(r"\D", "", p or "")
    if len(d) == 11 and d[0] in "78":
        d = d[1:]
    if len(d) != 10:
        return p or ""
    return f"({d[:3]}) {d[3:6]}-{d[6:8]}-{d[8:]}"


def _norm_days(s: str) -> str:
    s = s.lower().replace(" ", "")
    return s


def parse_group(name: str) -> dict:
    """«2627_АЯ_пн-ср_16:00_7-8 лет_Pre-A1 Starters (Гр1)» -> части заголовка."""
    n = re.sub(r"^2627_", "", name)
    parts = [p.strip() for p in n.split("_")]
    num = days = time = age = ""
    notes = []
    m = re.search(r"\(Гр\s*(\d+)\)", n) or re.search(r"Группа\s*(\d+)", n)
    if m:
        num = m.group(1)
    for i, p in enumerate(parts):
        if i == 0:
            continue                                   # префикс направления: АЯ, ПШ, РР.Лицей
        pl = p.lower()
        if re.match(r"^группа\s*\d+$", pl):
            continue
        if not days and re.search(r"\b(пн|вт|ср|чт|пт|сб|вс)\b", pl) and ":" not in pl:
            days = _norm_days(p)
            continue
        if not days and re.search(r"\b(пн|вт|ср|чт|пт|сб|вс)\b", pl) and "+" in pl:
            days = p                                   # «пн 19:00 + сб 10:00»
            continue
        if not time and re.match(r"^\d{1,2}:\d{2}(-\d{1,2}:\d{2})?$", p):
            time = p.replace(":", ".")
            continue
        if not age and re.search(r"\d", p) and re.search(r"(лет|год|^\d[\d,.\s-]*$)", pl):
            a = re.sub(r"\s*(лет|года|год)\s*$", "", p).replace(",", ".").replace(" ", "")
            age = a
            continue
        q = re.sub(r"\s*\(Гр\s*\d+\)", "", p).strip()
        if q:
            notes.append(q)
    if not time:
        tt = re.findall(r"(\d{1,2}):(\d{2})", days)
        if tt:
            time = "/".join(f"{h}.{m}" for h, m in tt)          # «пн 19:00 + сб 10:00»
            days = "-".join(re.findall(r"(пн|вт|ср|чт|пт|сб|вс)", days.lower()))
    return {"num": num, "days": days, "time": time, "age": age, "notes": " ".join(notes)}


def group_header(g: dict) -> str:
    s = f"гр.{g['num']}" if g["num"] else ""
    if g["days"]:
        s += f" ({g['days']})"
    if g["time"]:
        s += f" {g['time']}"
    if g["age"]:
        s += f" ({g['age']})"
    if g["notes"]:
        s += f" {g['notes']}"
    return s.strip()


def _first_name(full: str) -> str:
    return (full or "").split()[0] if full else ""


def _d(x):
    try:
        return dt.date.fromisoformat(str(x)[:10])
    except Exception:
        return None


def pay_month(today: dt.date) -> tuple[dt.date, dt.date]:
    """Месяц, оплату которого показываем: текущий, а с 25-го — следующий."""
    first = today.replace(day=1)
    if today.day >= 25:
        first = (first + dt.timedelta(days=32)).replace(day=1)
    last = (first + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    return first, last


MONTHS_RU = ["", "январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
             "сентябрь", "октябрь", "ноябрь", "декабрь"]


def build_model(meta: dict, lessons: list, joins: list, users: dict,
                today: dt.date | None = None, subs: list | None = None) -> dict:
    today = today or dt.date.today()
    m0, m1 = pay_month(today)
    # абонементы: userId -> [{cids, begin, end, price, payed, left}]
    subs_by_u: dict = defaultdict(list)
    for x in subs or []:
        b, e = _d(x.get("beginDate")), _d(x.get("endDate"))
        if not b or not e:
            continue
        cids = set(x.get("classIds") or []) | ({x["mainClassId"]} if x.get("mainClassId") else set())
        vc, vd = x.get("visitCount") or 0, x.get("visitedCount") or 0
        subs_by_u[x.get("userId")].append({"cids": cids, "b": b, "e": e, "price": x.get("price") or 0,
                                           "payed": x.get("payed") or 0, "left": (vc - vd) if vc else 99})
    classes = {c["id"]: c for c in _list(meta.get("classes"), "classes")}
    courses = {c["id"]: c["name"] for c in _list(meta.get("courses"), "courses")}
    managers = {m["id"]: m["name"] for m in _list(meta.get("managers"), "managers")}
    opened = {cid for cid, c in classes.items()
              if c.get("status") == "opened" and courses.get(c["courseId"]) not in SKIP_COURSES}

    # педагог группы — самый частый по расписанию; без занятий — по имени в названии
    tcount: dict[int, Counter] = defaultdict(Counter)
    cdays: dict[int, set] = defaultdict(set)
    recs: dict[tuple, list] = defaultdict(list)       # (classId, userId) -> [(date, test)]
    visits: dict[tuple, list] = defaultdict(list)     # (classId, userId) -> [(date, visit, held, test)]
    for l in lessons:
        cid = l.get("classId")
        for t in l.get("teacherIds") or []:
            tcount[cid][t] += 1
        try:
            d = dt.date.fromisoformat(l["date"])
        except Exception:
            continue
        cdays[cid].add(d.weekday())
        held = l.get("status") == 1
        for r in l.get("records") or []:
            recs[(cid, r.get("userId"))].append((d, bool(r.get("test"))))
            visits[(cid, r.get("userId"))].append((d, bool(r.get("visit")), held, bool(r.get("test"))))
    by_first = defaultdict(set)
    for name in managers.values():
        by_first[_first_name(name)].add(name)
    teacher_of = {}
    for cid in opened:
        if tcount.get(cid):
            teacher_of[cid] = managers.get(tcount[cid].most_common(1)[0][0], "")
    # логопеды: педагог — по имени в названии «ЛГ Марина_…». Если у всех групп этого
    # имени в расписании один и тот же педагог — берём ФИО, иначе только имя.
    lg = defaultdict(list)
    for cid in opened:
        m = re.search(r"ЛГ\s+(\S+?)_", classes[cid]["name"])
        if m and "Заявки" not in classes[cid]["name"]:
            lg[m.group(1)].append(cid)
    for nm, cids in lg.items():
        fulls = {teacher_of.get(c) for c in cids}
        full = LOGO_TEACHER.get(nm) or (fulls.pop() if len(fulls) == 1 and None not in fulls else nm)
        for c in cids:
            teacher_of[c] = full
    for cid in opened:
        teacher_of.setdefault(cid, "")

    # дети
    def child(uid, cid, st):
        u = users.get(uid) or users.get(str(uid)) or {}
        name = (u.get("name") or "").strip()
        rs = sorted(recs.get((cid, uid), []))
        fut = [d for d, _ in rs if d >= today] or [d for d, _ in rs]
        wds = sorted({d.weekday() for d in fut})
        allw = sorted(cdays.get(cid, set()))
        days = ""
        if wds:
            days = "-".join(WD[w] for w in wds) if wds != allw or len(wds) == 1 else "-".join(WD[w] for w in wds)
        mark = ""
        tests = [d for d, t in rs if t]
        if st == 5:
            mark = "О"
        elif st in (58132, 58131) or tests:
            mark = "п" + (f" ({tests[0].strftime('%d.%m')})" if tests else "")
        bd = _bd(u)
        k = {"uid": uid, "name": name, "days": days,
             "bd": dt.date.fromisoformat(bd).strftime("%d.%m.%y") if bd else "",
             "age": _age(bd, today), "phone": _phone(u.get("phone")), "mark": mark,
             "status": st}
        if subs is not None:
            k.update(_money(uid, cid, st, sorted(visits.get((cid, uid), []))))
        return k

    def _money(uid, cid, st, vis):
        """Оплата месяца (цвет), тревога «уходит молча», пробное — всё из МойКласса."""
        out = {}
        mine = [x for x in subs_by_u.get(uid, []) if not x["cids"] or cid in x["cids"]]
        # пробное: дата, пришёл ли, купил ли
        tests = [v for v in vis if v[3]]
        if st in (58132, 58131) or tests:
            td = tests[0][0] if tests else None
            came = None
            if tests and td <= today and (tests[0][2] or tests[0][1]):
                came = tests[0][1]
            since = (td or today) - dt.timedelta(days=3)
            bought = any(x["payed"] > 0 and x["b"] >= since and (x["left"] > 1 or x["price"] > 3000)
                         for x in mine)
            out["trial"] = {"date": td.strftime("%d.%m") if td else "", "came": came, "bought": bought}
        if st != 2:          # пробники и отработки — без цвета оплаты
            return out
        # оплата месяца: абонемент на этот месяц (начат не раньше чем за неделю до 1-го)
        # или длинный, который покрывает почти весь месяц и в нём ещё есть занятия
        cov = [x for x in mine if x["b"] <= m1 and x["e"] >= m0 and
               (x["b"] >= m0 - dt.timedelta(days=7) or (x["e"] >= m0 + dt.timedelta(days=20) and x["left"] > 0))]
        price = sum(x["price"] for x in cov)
        payed = sum(x["payed"] for x in cov)
        if cov and payed >= price - 1:
            out["pay"], out["auto_color"] = "оплачен", "g"
        elif cov and payed > 0:
            out["pay"], out["auto_color"] = f"оплачено {int(payed)} из {int(price)}", "y"
        elif cov:
            out["pay"], out["auto_color"] = "абонемент без оплаты", "r"
        else:
            prev = [x for x in mine if x["payed"] > 0 and x["e"] >= m0]
            out["pay"] = (f"оплачено до {max(x['e'] for x in prev).strftime('%d.%m')}, за месяц — нет"
                          if prev else "за месяц не оплачено")
            out["auto_color"] = "r"
        # уходит молча: 2 последних проведённых занятия — пропуски
        held = [v for v in vis if v[2] and v[0] < today]
        why = []
        if len(held) >= 2 and not held[-1][1] and not held[-2][1]:
            why.append(f"2 пропуска подряд (последний {held[-1][0].strftime('%d.%m')})")
        # абонемент кончается и нового нет
        now = [x for x in mine if x["b"] <= today <= x["e"]]
        if now:
            end = max(x["e"] for x in now)
            nxt = any(x["b"] > today and x["b"] >= end - dt.timedelta(days=3) for x in mine)
            if end <= today + dt.timedelta(days=7) and not nxt:
                why.append(f"абонемент до {end.strftime('%d.%m')}, продления нет")
        if why:
            out["risk"] = "; ".join(why)
        return out

    in_class = defaultdict(list)
    waiting_by_course = defaultdict(list)
    waiting_in_class = defaultdict(list)      # лист ожидания конкретной группы (мини-сад/НК по дням)
    for j in joins:
        cid, st = j.get("classId"), j.get("statusId")
        if cid not in opened:
            continue
        cname = classes[cid]["name"]
        if "Заявки" in cname:
            if st in ACTIVE | WAITING:
                c = child(j["userId"], cid, st)
                c["note"] = ""
                waiting_by_course[classes[cid]["courseId"]].append(c)
            continue
        if st in ACTIVE:
            in_class[cid].append(child(j["userId"], cid, st))
        elif st in WAITING:
            c = child(j["userId"], cid, st)
            c["note"] = "в " + (parse_group(cname)["num"] and "гр." + parse_group(cname)["num"] or cname[5:30])
            waiting_by_course[classes[cid]["courseId"]].append(c)
            waiting_in_class[cid].append(c)
    # Ира 05.11: «из листа ожидания записали на пробное — а из листа не удалить, после
    # обновления появляется снова». Заявка («Заявки …») остаётся висеть и после записи
    # в группу, поэтому того, кто уже учится или записан на пробное в группе этого
    # направления, из общего листа убираем сами. Ожидание конкретной группы (перевод
    # в другое время) не трогаем.
    busy = {(classes[cid]["courseId"], k["uid"]) for cid, ks in in_class.items() for k in ks}
    for crs in list(waiting_by_course):
        waiting_by_course[crs] = [k for k in waiting_by_course[crs]
                                  if k["note"] or (crs, k["uid"]) not in busy]

    # вкладки по педагогам
    tabs: dict[str, dict] = {}
    for cid in sorted(opened, key=lambda c: classes[c]["name"]):
        c = classes[cid]
        if "Заявки" in c["name"]:
            continue
        teacher = teacher_of.get(cid, "")
        course = courses.get(c["courseId"], "")
        tab = tabs.setdefault(teacher or course, {"teacher": teacher, "courses": [], "indiv": defaultdict(list),
                                                  "groups": []})
        if course not in tab["courses"]:
            tab["courses"].append(course)
        is_indiv = (c.get("maxStudents") == 1) or course == "Логопед"
        if is_indiv:
            m = re.search(r"_(Пн|Вт|Ср|Чт|Пт|Сб|Вс)_(\d{1,2}):(\d{2})", c["name"])
            if not m:
                continue
            wd = m.group(1).lower()
            tm = f"{int(m.group(2))}.{m.group(3)}"
            kids = sorted(in_class.get(cid, []), key=lambda k: k["name"])
            tab["indiv"][wd].append({"time": tm, "sort": int(m.group(2)) * 60 + int(m.group(3)),
                                     "kids": kids, "classId": cid})
        else:
            g = parse_group(c["name"])
            if course == "Английский детский сад":
                g = {"num": "", "days": "", "time": g["time"] or "", "age": "",
                     "notes": c["name"].replace("2627_", "")}
            kids = sorted(in_class.get(cid, []), key=lambda k: k["name"])
            grp = {"classId": cid, "header": group_header(g), "time": g["time"],
                   "g": g, "cap": c.get("maxStudents") or SLOTS, "kids": kids, "course": course}
            if course == DAY_COURSE:
                # мини-сад и НК раскладываются по дням недели (with_edits → _day_tab):
                # лист ожидания этой группы + общий по направлению («Заявки»)
                # (у мини-сада и НК одно направление, поэтому из общего берём только «Заявки»,
                # а не ожидающих соседней группы)
                wl, seen_w = [], set()
                common = [k for k in waiting_by_course.get(c["courseId"], []) if not k["note"]]
                for k in sorted(waiting_in_class.get(cid, []), key=lambda k: k["name"]) + \
                        sorted(common, key=lambda k: k["name"]):
                    if k["uid"] not in seen_w:
                        seen_w.add(k["uid"])
                        wl.append(k)
                grp["wait_kids"] = wl
            tab["groups"].append(grp)

    out = []
    first_names = Counter(_first_name(t["teacher"]) for t in tabs.values() if t["teacher"])
    for key, t in tabs.items():
        teacher = t["teacher"]
        fn = _first_name(teacher)
        tname = fn
        if fn and first_names[fn] > 1:
            tname = f"{fn} {teacher.split()[1][0]}." if len(teacher.split()) > 1 else fn
        dirs = []
        if t["indiv"]:
            dirs.append("Логопед")
        for grp in sorted(t["groups"], key=lambda g: classes[g["classId"]]["name"]):
            nm = classes[grp["classId"]]["name"]
            m = re.match(r"2627_РР\.([^_]+)", nm)
            if m:
                lab = m.group(1)
            elif "Мини-сад" in nm:
                lab = "Мини-сад"
            elif "Нулевой класс" in nm:
                lab = "НК"
            else:
                lab = COURSE_SHORT.get(grp["course"], grp["course"])
            if lab not in dirs:
                dirs.append(lab)
        rr = any(re.match(r"2627_РР\.", classes[g["classId"]]["name"]) for g in t["groups"])
        direction = " и ".join(dirs) if len(dirs) <= 2 else ", ".join(dirs[:-1]) + " и " + dirs[-1]
        sheet = f"{direction}_{tname}" if tname else direction
        sheet = re.sub(r"[/\\?*\[\]:]", "", sheet)
        if len(sheet) > 31:
            sheet = sheet[:31]
        title = (("РР " if rr and len(dirs) == 1 else "") + direction + (f" — {teacher}" if teacher else "")).strip()
        blocks = []
        order = {w: i for i, w in enumerate(WD)}
        for wd in sorted(t["indiv"], key=lambda w: order[w]):
            slots = sorted(t["indiv"][wd], key=lambda s: s["sort"])
            per = SLOTS // 2
            for i in range(0, len(slots), per):
                blocks.append({"kind": "indiv", "header": WD_FULL[wd] + (" (продолжение)" if i else ""),
                               "slots": slots[i:i + per]})
        groups = sorted(t["groups"], key=lambda g: (g["course"], int(g["g"]["num"] or 0), g["header"]))
        for g in groups:
            blocks.append({"kind": "group", **g})
        wl = []
        seen = set()
        for c in t["courses"]:
            cid_course = next((k for k, v in courses.items() if v == c), None)
            for k in sorted(waiting_by_course.get(cid_course, []), key=lambda k: k["name"]):
                if k["uid"] in seen:
                    continue
                seen.add(k["uid"])
                wl.append(k)
        shared = any(sum(1 for tt in tabs.values() if c in tt["courses"]) > 1 for c in t["courses"])
        for i in range(0, max(len(wl), 1), SLOTS):
            blocks.append({"kind": "wait", "header": "ЛИСТ ОЖИДАНИЯ" + (" (общий по направлению)" if shared else "")
                           + (" — продолжение" if i else ""), "kids": wl[i:i + SLOTS]})
        mx = max([len(b["kids"]) for b in blocks if b["kind"] == "group"] + [SLOTS])
        out.append({"sheet": sheet, "title": title, "teacher": teacher, "blocks": blocks, "slots": mx})

    def tab_order(t):
        pri = ["Музыка", "Первая школа", "Лицей", "Мини-сад", "ПШ", "Английский", "Логопед", "ИЗО", "Мент"]
        for i, p in enumerate(pri):
            if p in t["sheet"]:
                return (i, t["sheet"])
        return (99, t["sheet"])
    out.sort(key=tab_order)
    # уникальные имена вкладок
    seen_names = Counter()
    for t in out:
        seen_names[t["sheet"]] += 1
        if seen_names[t["sheet"]] > 1:
            t["sheet"] = t["sheet"][:28] + f" {seen_names[t['sheet']]}"
    return {"built": dt.datetime.now().strftime("%d.%m.%Y %H:%M"), "tabs": out,
            "days_choices": DAYS_CHOICES, "pay_month": MONTHS_RU[m0.month] if subs is not None else ""}


# ---------------------------------------------------------------- Excel
BLUE, GREEN = "2E86DE", "3BAA35"
LIGHT_GREEN, PINK = "D9F2D0", "F9C9DD"


def to_xlsx(model: dict, path: str) -> None:
    """Excel = то же, что на странице: бланк админов (№ / Ф.И. / Дни / Отм., заливка — их),
    лист ожидания (Ф.И., д.р., желаемые дни, телефон) и лист «Позвонить» из проверок."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    wb.remove(wb.active)
    FONT = "Calibri"
    fill = lambda c: PatternFill("solid", fgColor=c)
    widths = [3.6, 23, 8.5, 8, 13]               # №, Ф.И., [Дни | д.р.], [Дни], [Отм. | телефон]
    for tab in model["tabs"]:
        ws = wb.create_sheet(tab["sheet"])
        for col in range(3):
            for i, w in enumerate(widths):
                ws.column_dimensions[_col(col * 6 + i + 1)].width = w
            if col < 2:
                ws.column_dimensions[_col(col * 6 + 6)].width = 1.5
        ws.cell(1, 1, tab["title"]).font = Font(name=FONT, size=13, bold=True)
        ws.cell(1, 1).fill = fill(PINK)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=5)
        ws.cell(1, 7, f"выгружено {model['built']}").font = Font(name=FONT, size=9, italic=True, color="666666")
        ws.freeze_panes = "A2"
        dv = DataValidation(type="list", formula1='"' + ",".join(model["days_choices"]) + '"',
                            allow_blank=True, showErrorMessage=False)
        ws.add_data_validation(dv)
        N = tab.get("slots", SLOTS)
        rows_per = 1 + N + 1                        # заголовок, места, «Свободно»
        for bi, b in enumerate(tab["blocks"]):
            page, col, r = bi // 9, (bi % 9) // 3, bi % 3
            top = 3 + (page * 3 + r) * (rows_per + 1)
            c0 = col * 6 + 1
            color = GREEN if col == 1 else BLUE
            side = Side(style="thin", color=color)
            if b["kind"] == "group":
                h = ws.cell(top, c0, b.get("hpre", ""))
                h.font = Font(name=FONT, size=10, bold=True)
                h.alignment = Alignment(horizontal="left", vertical="center", shrink_to_fit=True)
                ws.merge_cells(start_row=top, start_column=c0, end_row=top, end_column=c0 + 1)
                tc = ws.cell(top, c0 + 2, b.get("time", ""))
                tc.fill = fill(LIGHT_GREEN)
                ws.cell(top, c0 + 3).fill = fill(LIGHT_GREEN)
                tc.font = Font(name=FONT, size=11, bold=True)
                tc.alignment = Alignment(horizontal="center", shrink_to_fit=True)
                ws.merge_cells(start_row=top, start_column=c0 + 2, end_row=top, end_column=c0 + 3)
                a = ws.cell(top, c0 + 4, b.get("hpost", ""))
                a.font = Font(name=FONT, size=10, bold=True)
                a.alignment = Alignment(shrink_to_fit=True)
            else:
                ws.cell(top, c0, b["header"]).font = Font(name=FONT, size=11, bold=True)
                for k in range(5):
                    ws.cell(top, c0 + k).fill = fill(LIGHT_GREEN)
                ws.merge_cells(start_row=top, start_column=c0, end_row=top, end_column=c0 + 4)
                ws.cell(top, c0).alignment = Alignment(horizontal="left", vertical="center", shrink_to_fit=True)
            for i in range(N):
                rr = top + 1 + i
                n = ws.cell(rr, c0, i + 1)
                n.fill = fill(color)
                n.font = Font(name=FONT, size=11, bold=True, color="FFFFFF")
                n.alignment = Alignment(horizontal="center")
                for k in range(5):
                    ws.cell(rr, c0 + k).border = Border(bottom=side)
                    if k:
                        ws.cell(rr, c0 + k).font = Font(name=FONT, size=11)
                if b["kind"] in ("group", "day"):
                    ws.merge_cells(start_row=rr, start_column=c0 + 2, end_row=rr, end_column=c0 + 3)
                    dv.add(ws.cell(rr, c0 + 2))
                elif b["kind"] == "wait":
                    dv.add(ws.cell(rr, c0 + 3))
            if b["kind"] == "group":
                for i, k in enumerate(b["kids"][:N]):
                    rr = top + 1 + i
                    nc = ws.cell(rr, c0 + 1, k.get("name", ""))
                    if PAY_FILL.get(k.get("view")):
                        nc.fill = fill(PAY_FILL[k["view"]])
                    ws.cell(rr, c0 + 2, k.get("days", ""))
                    m = ws.cell(rr, c0 + 4, k.get("mark", ""))
                    m.alignment = Alignment(horizontal="center", shrink_to_fit=True)
                fr = top + 1 + N
                ws.cell(fr, c0, "Свободно:").font = Font(name=FONT, size=10, bold=True)
                ws.merge_cells(start_row=fr, start_column=c0, end_row=fr, end_column=c0 + 1)
                ws.cell(fr, c0 + 1).alignment = Alignment(horizontal="left")
                if b.get("free"):
                    ws.cell(fr, c0 + 2, b["free"]).font = Font(name=FONT, size=9)
                if b["cap"] >= len(b["kids"]):
                    f = ws.cell(fr, c0 + 4, f"=MAX(0,{b['cap']}-COUNTA({_col(c0 + 1)}{top + 1}:{_col(c0 + 1)}{top + N}))")
                    f.font = Font(name=FONT, size=11, bold=True)
                    f.alignment = Alignment(horizontal="center")
            elif b["kind"] == "indiv":
                for i, s in enumerate(b["slots"]):
                    r1 = top + 1 + 2 * i
                    tm = ws.cell(r1, c0 + 2, s["time"])
                    tm.fill = fill(LIGHT_GREEN)
                    tm.font = Font(name=FONT, size=11, bold=True)
                    tm.alignment = Alignment(horizontal="center")
                    ws.cell(r1, c0 + 1, s.get("note") or ", ".join(k["name"] for k in s["kids"]))
                    if PAY_FILL.get(s.get("color")):
                        ws.cell(r1, c0 + 1).fill = fill(PAY_FILL[s["color"]])
                    ws.cell(r1 + 1, c0 + 1, ", ".join(k["phone"] for k in s["kids"] if k["phone"]))
                    ws.cell(r1 + 1, c0 + 1).font = Font(name=FONT, size=10)
                    if s["kids"]:
                        ws.cell(r1, c0 + 3, s["kids"][0]["bd"]).font = Font(name=FONT, size=9)
            elif b["kind"] == "day":                 # мини-сад/НК по дням: абонементы, пропуск, серые ручные
                i = 0
                for k in b["kids"]:
                    if i >= N:
                        break
                    rr = top + 1 + i
                    nc = ws.cell(rr, c0 + 1, k.get("name", ""))
                    if PAY_FILL.get(k.get("view")):
                        nc.fill = fill(PAY_FILL[k["view"]])
                    if k.get("first") and k.get("days"):
                        ws.cell(rr, c0 + 2, f"({k['days']})")
                    mc = ws.cell(rr, c0 + 4, k.get("mark", ""))
                    mc.alignment = Alignment(horizontal="center", shrink_to_fit=True)
                    i += 1
                i += 1 if b["kids"] else 0           # пропуск перед пробными/временными
                for g in b.get("gray") or []:
                    if i >= N:
                        break
                    rr = top + 1 + i
                    nc = ws.cell(rr, c0 + 1, g.get("name", ""))
                    nc.font = Font(name=FONT, size=11, color="8A8A8A")
                    if PAY_FILL.get(g.get("view")):
                        nc.fill = fill(PAY_FILL[g["view"]])
                    if g.get("days"):
                        ws.cell(rr, c0 + 2, g["days"]).font = Font(name=FONT, size=11, color="8A8A8A")
                    mc = ws.cell(rr, c0 + 4, g.get("mark", ""))
                    mc.font = Font(name=FONT, size=11, color="8A8A8A")
                    mc.alignment = Alignment(horizontal="center", shrink_to_fit=True)
                    i += 1
                fr = top + 1 + N
                ws.cell(fr, c0, "Свободно:").font = Font(name=FONT, size=10, bold=True)
                ws.merge_cells(start_row=fr, start_column=c0, end_row=fr, end_column=c0 + 1)
                if b.get("free"):
                    ws.cell(fr, c0 + 2, b["free"]).font = Font(name=FONT, size=9)
            elif b["kind"] == "empty":
                pass
            else:                                    # лист ожидания: Ф.И., д.р., желаемые дни, телефон
                for i, k in enumerate(b["kids"][:N]):
                    rr = top + 1 + i
                    ws.cell(rr, c0 + 1, k["name"])
                    ws.cell(rr, c0 + 2, k.get("bd", "")).font = Font(name=FONT, size=9)
                    ws.cell(rr, c0 + 3, k.get("days", ""))
                    ws.cell(rr, c0 + 4, k.get("phone", "")).font = Font(name=FONT, size=9)
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_margins.left = ws.page_margins.right = 0.25
        ws.page_margins.top = ws.page_margins.bottom = 0.3
        ws.print_title_rows = "1:1"
    _sheet_zvonit(wb, model, Font, Alignment, FONT)
    wb.save(path)


PAY_FILL = {"g": "C6EFCE", "y": "FFEB9C", "r": "FFC7CE", "x": "C9A2FF"}


def _sheet_zvonit(wb, model, Font, Alignment, FONT):
    """Лист «Позвонить» — из проверок: «уходит молча» и не оплатившие месяц по МойКлассу."""
    ws = wb.create_sheet("Позвонить", 0)
    mon = model.get("pay_month") or ""
    due = f" ({model['pay_due']})" if model.get("pay_due") else ""
    ws.cell(1, 1, f"Проверка по МойКлассу: некорректные оплаты, уходят молча, не оплатили {mon}{due}").font = Font(name=FONT, size=13, bold=True)
    ws.cell(2, 1, f"выгружено {model.get('built', '')} · сначала некорректные оплаты (в CRM есть, денег нет), потом «уходят молча», потом неоплаты; "
                  "кого знаете (болеет, уехал) — не звонить; после разговора — комментарий в карточку"
            ).font = Font(name=FONT, size=9, italic=True, color="666666")
    hdr = ["Педагог", "Группа", "Ребёнок", "Что", "Итог разговора"]
    for i, h in enumerate(hdr):
        ws.cell(4, i + 1, h).font = Font(name=FONT, size=11, bold=True)
    for i, w in enumerate([24, 34, 26, 46, 30]):
        ws.column_dimensions[_col(i + 1)].width = w
    rows = []
    for tab in model.get("tabs", []):
        ch = tab.get("checks") or {}
        for kind, lst in ((0, ch.get("badpay", [])), (1, ch.get("risk", [])), (2, ch.get("unpaid", []))):
            for x in lst:
                rows.append((kind, tab.get("title", ""), x["g"], x["name"], x["txt"]))
    rows.sort()
    for j, r in enumerate(rows):
        for i, v in enumerate(r[1:]):
            c = ws.cell(5 + j, i + 1, v)
            c.font = Font(name=FONT, size=10)
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A5"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# ---------------------------------------------------------------- сервер
# Живая модель (кэш на час) + правки администратора поверх неё.
# Правки привязаны к ребёнку в группе (u:<classId>:<userId>:<поле>) или к блоку
# (b:<ключ блока>:<поле>), поэтому переживают обновление из CRM.
import json as _json
import logging as _logging
import threading as _threading
import time as _time

_log = _logging.getLogger("blank_grupp")
_CACHE: dict = {"model": None, "ts": 0.0, "busy": False, "err": ""}
_LOCK = _threading.Lock()
TTL = 3600


def _ensure_table():
    from . import db
    with db.get_conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS blank_grupp_edits (k TEXT PRIMARY KEY, v TEXT, ts TEXT)")


def edits() -> dict:
    from . import db
    _ensure_table()
    with db.get_conn() as c:
        return {r["k"]: r["v"] for r in c.execute("SELECT k, v FROM blank_grupp_edits")}


def save_edit(k: str, v: str | None) -> None:
    from . import db
    _ensure_table()
    if not re.match(r"^[ub]:[\w:.\-]{1,120}$", k or ""):
        raise ValueError("bad key")
    with db.get_conn() as c:
        if v is None or v == "":
            c.execute("DELETE FROM blank_grupp_edits WHERE k=?", (k,))
        else:
            c.execute("INSERT OR REPLACE INTO blank_grupp_edits(k, v, ts) VALUES(?,?,?)",
                      (k, str(v)[:20000], dt.datetime.now().isoformat(timespec="seconds")))


def _items(r, key):
    return (r.get(key) if isinstance(r, dict) else r) or []


def _fetch_live() -> dict:
    from . import db, sync
    from .moyklass_client import MoyklassClient
    mk = MoyklassClient(sync.get_api_key())
    meta = {
        "classes": _items(mk.get("/v1/company/classes", {"limit": 500}), "classes"),
        "courses": _items(mk.get("/v1/company/courses"), "courses"),
        "managers": _items(mk.get("/v1/company/managers"), "managers"),
    }
    today = dt.date.today()
    d0, d1 = (today - dt.timedelta(days=28)).isoformat(), (today + dt.timedelta(days=14)).isoformat()
    lessons = mk.fetch_all("/v1/company/lessons", ["lessons"], {"date": [d0, d1], "includeRecords": "true"})
    opened = {c["id"] for c in meta["classes"] if c.get("status") == "opened"}
    joins = [j for j in mk.fetch_all("/v1/company/joins", ["joins"]) if j.get("classId") in opened]
    uids = {j["userId"] for j in joins if j.get("statusId") in ACTIVE | WAITING}
    users: dict = {}
    with db.get_conn() as c:
        q = list(uids)
        for i in range(0, len(q), 500):
            part = q[i:i + 500]
            for r in c.execute(f"SELECT id, name, phone, raw FROM users WHERE id IN ({','.join('?' * len(part))})", part):
                try:
                    u = _json.loads(r["raw"] or "{}")
                except Exception:
                    u = {}
                u.setdefault("name", r["name"])
                u.setdefault("phone", r["phone"])
                users[r["id"]] = u
    for uid in [u for u in uids if u not in users or not users[u].get("attributes")][:150]:
        try:
            users[uid] = mk.get(f"/v1/company/users/{uid}")
        except Exception as e:  # карточка удалена и т. п.
            _log.warning("blank_grupp: user %s: %s", uid, e)
    subs = []
    try:
        with db.get_conn() as c:
            for r in c.execute("SELECT raw FROM user_subscriptions WHERE end_date >= ?",
                               ((today - dt.timedelta(days=45)).isoformat(),)):
                try:
                    x = _json.loads(r["raw"] or "{}")
                except Exception:
                    continue
                if x.get("userId") in uids:
                    subs.append(x)
    except Exception as e:      # без абонементов страница всё равно работает (без цветов оплаты)
        _log.warning("blank_grupp: абонементы: %s", e)
        subs = None
    return build_model(meta, lessons, joins, users, today, subs)


def _refresh():
    try:
        m = _fetch_live()
        with _LOCK:
            _CACHE.update(model=m, ts=_time.time(), err="")
    except Exception as e:
        _log.exception("blank_grupp: обновление не удалось")
        with _LOCK:
            _CACHE["err"] = str(e)[:200]
    finally:
        with _LOCK:
            _CACHE["busy"] = False


def model(force: bool = False, wait: bool = False) -> dict:
    with _LOCK:
        stale = force or not _CACHE["model"] or _time.time() - _CACHE["ts"] > TTL
        start = stale and not _CACHE["busy"]
        if start:
            _CACHE["busy"] = True
    if start:
        if wait or not _CACHE["model"]:
            _refresh()
        else:
            _threading.Thread(target=_refresh, daemon=True).start()
    with _LOCK:
        return {"model": _CACHE["model"], "busy": _CACHE["busy"], "err": _CACHE["err"],
                "age_min": int((_time.time() - _CACHE["ts"]) / 60) if _CACHE["ts"] else None}


def _bkey(tab: dict, b: dict, i: int) -> str:
    if b["kind"] == "group":
        return f"g{b['classId']}"
    return re.sub(r"[^\w]", "", f"{b['kind']}{tab['sheet']}{i}")[:60]


# ---------------------------------------------------------------- бланк админов
# Замечания Иры 02.11: бланк ведут администраторы. Состав группы один раз берётся из
# текущего бланка (МойКласс + все правки админов), дальше в бланке меняется только то,
# что поменяла админ. МойКласс — только в проверках вверху страницы.
WAIT_OFF = ("Мини-сад, ПШ и Лицей_Татьяна",)     # Ира 02.11: здесь лист ожидания не нужен
OTRAB = 5                                          # статус записи «отработка» — не в бланк
_ROWS_LOCK = _threading.Lock()


def _rows(E: dict, key: str) -> list:
    try:
        return _json.loads(E.get(f"b:{key}:rows") or "[]")
    except Exception:
        return []


def _rid() -> str:
    import secrets
    return "r" + secrets.token_hex(4)


def _norm(s: str) -> list:
    return re.sub(r"[^\wё ]", " ", (s or "").lower().replace("ё", "е")).split()


def _same(a: str, b: str) -> bool:
    """Ручная строка «Иванова Маша» ↔ карточка «Иванова Мария»: фамилия + 3 буквы имени."""
    x, y = _norm(a), _norm(b)
    return len(x) >= 2 and len(y) >= 2 and x[0] == y[0] and x[1][:3] == y[1][:3]


def _freeze(m: dict, E: dict) -> None:
    """Один раз: то, что сейчас видно в бланке (дети из МойКласса + правки админов:
    имена, дни, «Отм.», ручная заливка, переносы, ручные строки), становится бланком админов.
    Отработчиков и вычеркнутых не берём; у купивших после пробного «п» не держим."""
    blocks = {b["key"]: b for t in m["tabs"] for b in t["blocks"] if b["kind"] == "group"}
    out: dict = {k: [] for k in blocks}
    moved = []
    for key, b in blocks.items():
        for k in b["kids"]:
            ck = f"u:{key}:{k['uid']}"
            if E.get(f"{ck}:hide") or k.get("status") == OTRAB:
                continue
            color = E.get(f"{ck}:color", "")
            r = {"id": f"u{k['uid']}", "uid": k["uid"],
                 "name": E.get(f"{ck}:name", k["name"]) or f"(без имени, карточка {k['uid']})",
                 "days": E.get(f"{ck}:days", k["days"]), "mark": E.get(f"{ck}:mark", k["mark"]),
                 "color": color}
            if f"{ck}:mark" not in E and ((k.get("trial") or {}).get("bought") or r["mark"] == "О"):
                r["mark"] = ""
            to = E.get(f"{ck}:move")
            if to in blocks and to != key:
                moved.append((to, r))
            else:
                out[key].append(r)
        try:
            man = _json.loads(E.get(f"b:{key}:manual") or "[]")
        except Exception:
            man = []
        for x in man:
            if (x.get("name") or "").strip():
                out[key].append({"id": _rid(), "uid": None, "name": x.get("name", ""),
                                 "days": x.get("days", ""), "mark": x.get("mark", ""),
                                 "color": x.get("color", "")})
    for to, r in moved:
        out[to].append(r)
    for key, rows in out.items():
        if f"b:{key}:rows" not in E:
            save_edit(f"b:{key}:rows", _json.dumps(rows, ensure_ascii=False))
    save_edit("b:frozen", dt.datetime.now().isoformat(timespec="minutes"))


def _header_parts(b: dict) -> tuple[str, str, str]:
    """Как на бумаге: «гр.№ (дни)» · время (зелёная ячейка) · «(возраст)». Время — один раз."""
    g = b.get("g") or {}
    tm = g.get("time") or ""
    notes = re.sub(r"[_\s]*\d{1,2}[:.]\d{2}\s*[-–]\s*\d{1,2}[:.]\d{2}", " ", g.get("notes") or "")
    notes = re.sub(r"\s+", " ", notes.replace("_", " ")).strip()
    pre = (f"гр.{g['num']}" if g.get("num") else "") + (f" ({g['days']})" if g.get("days") else "")
    post = f"({g['age']})" if g.get("age") else ""
    if notes:
        if pre:
            post = (post + " " + notes).strip()
        else:
            pre = notes
    return pre.strip(), tm, post


def _wait_row(k: dict, old_ck: str, E: dict) -> bool:
    """Строка листа ожидания: правки и «удалить» — по ребёнку (u:w:<uid>), а не по блоку.
    Ира 05.11: лист ожидания общий для нескольких вкладок одного направления, и удалённый
    на одной вкладке ребёнок оставался на соседней. Старые правки по ключу блока читаем
    по-прежнему. False — строку не показывать."""
    ck = f"u:w:{k['uid']}"
    k["ck"] = ck
    for f in ("days", "name"):
        for key in (f"{ck}:{f}", f"{old_ck}:{f}"):
            if key in E:
                k[f] = E[key]
                break
    if E.get(f"{ck}:hide") or E.get(f"{old_ck}:hide"):
        return False
    return bool((k.get("name") or "").strip())


def with_edits(m: dict) -> dict:
    """Бланк админов + лист ожидания и логопеды из МойКласса + проверки вверху (t["checks"])."""
    import copy
    m = copy.deepcopy(m)
    for t in m["tabs"]:
        for i, b in enumerate(t["blocks"]):
            b["key"] = _bkey(t, b, i)
    E = edits()
    if not E.get("b:frozen"):
        with _ROWS_LOCK:
            E = edits()
            if not E.get("b:frozen"):
                _freeze(m, E)
                E = edits()
    today = dt.date.today()
    m["pay_due"] = "срок оплаты до 5 числа" if (today.day <= 5 or today.day >= 25) else ""
    in_blank: dict = defaultdict(list)          # uid -> [заголовки групп, где он в бланке]
    for t in m["tabs"]:
        t["blocks"] = [b for b in t["blocks"]
                       if not (b["kind"] == "wait" and any(w in t["sheet"] for w in WAIT_OFF))]
        for b in t["blocks"]:
            if b["kind"] == "group":
                b["mk"] = [k for k in b["kids"] if k.get("status") != OTRAB]
                b["kids"] = _rows(E, b["key"])
                b["free"] = E.get(f"b:{b['key']}:free", "")
                b["hpre"], b["time"], b["hpost"] = _header_parts(b)
                for r in b["kids"]:
                    if r.get("uid"):
                        in_blank[r["uid"]].append(b["hpre"] or b["header"])
            elif b["kind"] == "wait":
                keep = []
                for k in b["kids"]:
                    if _wait_row(k, f"u:{b['key']}:{k['uid']}", E):
                        keep.append(k)
                b["kids"] = keep
            else:
                for s in b["slots"]:
                    s["ck"] = f"b:{b['key']}:{s['time'].replace('.', '')}"
                    s["note"] = E.get(s["ck"] + ":note", "")
                    s["color"] = E.get(s["ck"] + ":color", "")
    try:                                  # «некорректная оплата»: в CRM оплачено, денег в кассах нет
        badpay = {int(k): v for k, v in _json.loads(E.get("b:badpay") or "{}").get("uids", {}).items()}
    except Exception:
        badpay = {}
    m["badpay_at"] = (_json.loads(E.get("b:badpay") or "{}") or {}).get("at", "") if badpay else ""
    _day_tabs(m, E)
    for t in m["tabs"]:
        src = t.pop("src_block", None)
        # вкладка «по дням» проверяется по своей группе (она в бланке скрыта, но строки — её)
        t["checks"] = _checks({"blocks": [src]} if src else t, in_blank, today, badpay)
        for b in t["blocks"]:
            if b["kind"] == "group":
                for r in b["kids"]:
                    c = r.get("color") or r.get("auto", "")
                    r["view"] = "" if c == "-" else c
    return m


def _wd_list(s: str) -> list:
    """«пн-ср-пт», «пн, чт» → ['пн', 'ср', 'пт']: дни перечислены, а не диапазон."""
    return re.findall(r"пн|вт|ср|чт|пт|сб|вс", (s or "").lower())


def _day_tabs(m: dict, E: dict) -> None:
    """Ира 04.11: мини-сад и нулевой класс — отдельной вкладкой по дням недели.
    Блок группы уходит из вкладки педагога; новая вкладка ставится перед ней."""
    new = []
    for t in m["tabs"]:
        keep = []
        for b in t["blocks"]:
            if b["kind"] == "group" and b.get("course") == DAY_COURSE:
                s = " ".join([b.get("header") or "", (b.get("g") or {}).get("notes") or "", b.get("hpre") or ""])
                lab = "Мини-сад" if "Мини-сад" in s else ("Нулевой класс" if "Нулевой" in s else "")
                if lab:
                    new.append((t, _day_tab(t, b, lab, E)))
                    continue
            keep.append(b)
        t["blocks"] = keep
    for parent, nt in new:
        m["tabs"].insert(m["tabs"].index(parent), nt)


def _day_tab(t: dict, b: dict, lab: str, E: dict) -> dict:
    """Блоки ПН ВТ СР | ЧТ ПТ (без дней) | лист ожидания, пусто, пусто — заполнение сверху вниз.
    Сверху блока дня — дети с абонементом по алфавиту: это строки бланка админов этой группы
    (правка имени, дней, «Отм.», заливки пишется туда же — одна строка на ребёнка).
    Ниже — ручные строки админа (пробные, временные): хранятся отдельно по дню (b:d…:rows),
    Клод их не заполняет, не переносит и при обновлении не стирает. Автоцвета оплаты нет —
    заливку ставит администратор."""
    cid = b["classId"]
    fn = _first_name(t.get("teacher") or "") or t["sheet"].split("_")[-1]
    st = {k["uid"]: k.get("status") for k in b.get("mk", [])}
    subs = []
    for r in b["kids"]:
        uid = r.get("uid")
        if uid and st.get(uid) in TRIAL_ST:
            continue                               # пробники — ручные строки админа
        if not uid and (r.get("mark") or "").strip().lower().startswith("п"):
            continue
        if not (r.get("name") or "").strip():
            continue
        c = r.get("color") or ""
        subs.append({**r, "view": "" if c in ("", "-") else c, "src": b["key"]})
    subs.sort(key=lambda r: (r.get("name") or "").lower().replace("ё", "е"))
    blocks, seen = [], set()
    for wd, lat, up in DAY_WD:
        key = f"d{cid}{lat}"
        rows = []
        for r in subs:
            if wd in _wd_list(r.get("days")):
                rows.append({**r, "first": r["id"] not in seen})
                seen.add(r["id"])
        gray = _rows(E, key)
        for g in gray:
            c = g.get("color") or ""
            g["view"] = "" if c in ("", "-") else c
        blocks.append({"kind": "day", "key": key, "header": up, "src": b["key"], "kids": rows,
                       "gray": gray, "free": E.get(f"b:{key}:free", "")})
    nod = [{**r, "first": True} for r in subs if not _wd_list(r.get("days"))]
    if nod:
        blocks.append({"kind": "day", "key": f"d{cid}nd", "header": "БЕЗ ДНЕЙ — впишите дни", "src": b["key"],
                       "kids": nod, "gray": [], "nogray": True, "free": ""})
    else:
        blocks.append({"kind": "empty", "key": f"e{cid}0", "header": ""})
    wkey = f"w{cid}"
    wl = []
    for k in b.get("wait_kids") or []:
        k = dict(k)
        if _wait_row(k, f"u:{wkey}:{k['uid']}", E):
            wl.append(k)
    blocks.append({"kind": "wait", "key": wkey, "header": "ЛИСТ ОЖИДАНИЯ", "kids": wl})
    blocks.append({"kind": "empty", "key": f"e{cid}1", "header": ""})
    blocks.append({"kind": "empty", "key": f"e{cid}2", "header": ""})
    sheet = re.sub(r"[/\\?*\[\]:]", "", f"{lab}_{fn}")[:31]
    return {"sheet": sheet, "title": f"{lab} — {fn}", "teacher": t.get("teacher", ""),
            "blocks": blocks, "slots": SLOTS, "by_days": True, "src_block": b}


def _checks(t: dict, in_blank: dict, today: dt.date, badpay: dict | None = None) -> dict:
    """Расхождения бланка с МойКлассом для блоков вверху + цвет оплаты строки.
    Цвет: ручной (админ) важнее автоматического; «-» — админ сняла цвет.
    Автоматический: g оплачен, y частично, r не оплачен, x некорректная оплата."""
    badpay = badpay or {}
    ch = {"missing": [], "extra": [], "risk": [], "unpaid": [], "trials": [], "badpay": []}
    for b in t["blocks"]:
        if b["kind"] != "group":
            continue
        g = " ".join(x for x in (b["hpre"], b["time"], b["hpost"]) if x)
        mk_by = {k["uid"]: k for k in b["mk"]}
        rows = b["kids"]
        found = {}                                 # uid из МойКласса -> строка бланка
        for r in rows:
            if r.get("uid") in mk_by:
                found[r["uid"]] = r
        for k in b["mk"]:
            if k["uid"] in found:
                continue
            r = next((r for r in rows if not r.get("uid") and _same(r.get("name", ""), k["name"])), None)
            if r:
                found[k["uid"]] = r
                continue
            where = [w for w in in_blank.get(k["uid"], []) if w != (b["hpre"] or b["header"])]
            st = "пробное" if k.get("status") in (58132, 58131) else "учится"
            ch["missing"].append({"g": g, "name": k["name"], "uid": k["uid"], "block": b["key"],
                                  "days": k.get("days", ""),
                                  "txt": f"в CRM числится ({st}), в бланке нет"
                                         + (f" — в бланке он в {', '.join(where)}" if where else "")})
        linked = {id(r) for r in found.values()}
        for r in rows:
            if r.get("uid") and r["uid"] not in mk_by and id(r) not in linked:
                ch["extra"].append({"g": g, "name": r.get("name", ""),
                                    "txt": "в бланке есть, в CRM в этой группе не числится"})
        for r in rows:
            r["auto"], r["pay"] = "", ""
        for uid, r in found.items():
            k = mk_by[uid]
            nm = r.get("name") or k["name"] or f"карточка {uid}"
            if k.get("status") == 2:
                r["auto"], r["pay"] = k.get("auto_color", ""), k.get("pay", "")
            if uid in badpay:
                r["auto"], r["pay"] = "x", badpay[uid]
                ch["badpay"].append({"g": g, "name": nm, "txt": badpay[uid]})
            if k.get("risk"):
                ch["risk"].append({"g": g, "name": nm, "txt": k["risk"]})
            if k.get("status") == 2 and k.get("auto_color") in ("r", "y"):
                note = " · в бланке вручную отмечен зелёным" if r.get("color") == "g" else ""
                ch["unpaid"].append({"g": g, "name": nm, "txt": (k.get("pay") or "") + note})
            tr = k.get("trial")
            has_p = "п" in (r.get("mark") or "").lower()
            if tr:
                txt = f"пробное {tr.get('date') or '—'}"
                txt += {True: " · пришёл", False: " · не пришёл"}.get(tr.get("came"), " · ждём")
                if tr.get("came") is True or tr.get("bought"):
                    txt += " · купил" if tr.get("bought") else " · не купил"
                warn = ""
                if tr.get("bought") and has_p:
                    warn = "купил абонемент — «п» в бланке можно стереть"
                elif not tr.get("bought") and not has_p:
                    warn = "в бланке нет отметки «п»"
                ch["trials"].append({"g": g, "name": nm, "txt": txt, "warn": warn})
            elif has_p and k.get("status") == 2:
                ch["trials"].append({"g": g, "name": nm, "txt": "в бланке «п», в CRM пробного нет",
                                     "warn": "в бланке «п», в CRM пробного нет"})
    return ch


def row_op(p: dict) -> list:
    """Правка бланка админом: set (поле строки), add (новая строка / «+ в бланк»), del, move.
    Стёртое имя = строка удалена, список сдвигается. Возвращает строки блока."""
    key = str(p.get("block") or "")
    # g… — группа; d…mo/tu/… — ручные (серые) строки дня в бланке мини-сада/НК по дням
    if not re.match(r"^(g\d{1,12}|d\d{1,12}(mo|tu|we|th|fr))$", key):
        raise ValueError("bad block")
    op = p.get("op")
    if op == "move" and not key.startswith("g"):
        raise ValueError("bad move")
    rid = str(p.get("id") or "")
    clip = lambda v: re.sub(r"\s+", " ", str(v or "")).strip()[:80]
    with _ROWS_LOCK:
        E = edits()
        rows = _rows(E, key)
        row = next((r for r in rows if r.get("id") == rid), None)
        if op == "set":
            f = p.get("field")
            if f not in ("name", "days", "mark", "color") or row is None:
                raise ValueError("bad field/row")
            v = clip(p.get("value"))
            if f == "color" and v not in ("", "g", "y", "r", "-"):
                raise ValueError("bad color")
            if f == "name" and not v:
                rows.remove(row)
            else:
                row[f] = v
        elif op == "add":
            uid = int(p["uid"]) if str(p.get("uid") or "").isdigit() else None
            r = {"id": _rid(), "uid": uid, "name": clip(p.get("name")), "days": clip(p.get("days")),
                 "mark": clip(p.get("mark")), "color": ""}
            if not r["name"]:
                raise ValueError("no name")
            if not (uid and any(x.get("uid") == uid for x in rows)):
                rows.append(r)
        elif op == "del":
            if row is not None:
                rows.remove(row)
        elif op == "move":
            to = str(p.get("to") or "")
            if not re.match(r"^g\d{1,12}$", to) or row is None:
                raise ValueError("bad move")
            if to != key:
                rows.remove(row)
                dest = _rows(E, to)
                dest.append(row)
                save_edit(f"b:{to}:rows", _json.dumps(dest, ensure_ascii=False))
        else:
            raise ValueError("bad op")
        save_edit(f"b:{key}:rows", _json.dumps(rows, ensure_ascii=False))
    return rows


def _day_rows(b: dict) -> int:
    """Строк в блоке дня: абонементы + пропуск + серые ручные + одна свободная под новую."""
    if b.get("nogray"):
        return len(b["kids"])
    return len(b["kids"]) + 1 + len(b.get("gray") or []) + 1


def _need_openpyxl():
    try:
        import openpyxl  # noqa: F401
    except ImportError:          # на сервере пакета может не быть — ставим один раз (как ffmpeg в deti_video)
        import subprocess
        import sys
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "openpyxl"], timeout=180)


def xlsx_bytes() -> bytes:
    import tempfile
    _need_openpyxl()
    st = model()
    if not st["model"]:
        raise RuntimeError(st["err"] or "данные ещё загружаются")
    m = with_edits(st["model"])
    for t in m["tabs"]:
        t["slots"] = max([len(b["kids"]) for b in t["blocks"] if b["kind"] in ("group", "wait")]
                         + [_day_rows(b) for b in t["blocks"] if b["kind"] == "day"] + [SLOTS])
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
        to_xlsx(m, f.name)
        return open(f.name, "rb").read()
