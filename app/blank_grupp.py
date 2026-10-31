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


def build_model(meta: dict, lessons: list, joins: list, users: dict,
                today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    classes = {c["id"]: c for c in _list(meta.get("classes"), "classes")}
    courses = {c["id"]: c["name"] for c in _list(meta.get("courses"), "courses")}
    managers = {m["id"]: m["name"] for m in _list(meta.get("managers"), "managers")}
    opened = {cid for cid, c in classes.items()
              if c.get("status") == "opened" and courses.get(c["courseId"]) not in SKIP_COURSES}

    # педагог группы — самый частый по расписанию; без занятий — по имени в названии
    tcount: dict[int, Counter] = defaultdict(Counter)
    cdays: dict[int, set] = defaultdict(set)
    recs: dict[tuple, list] = defaultdict(list)       # (classId, userId) -> [(date, test)]
    for l in lessons:
        cid = l.get("classId")
        for t in l.get("teacherIds") or []:
            tcount[cid][t] += 1
        try:
            d = dt.date.fromisoformat(l["date"])
        except Exception:
            continue
        cdays[cid].add(d.weekday())
        for r in l.get("records") or []:
            recs[(cid, r.get("userId"))].append((d, bool(r.get("test"))))
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
        return {"uid": uid, "name": name, "days": days,
                "bd": dt.date.fromisoformat(bd).strftime("%d.%m.%y") if bd else "",
                "age": _age(bd, today), "phone": _phone(u.get("phone")), "mark": mark,
                "status": st}

    in_class = defaultdict(list)
    waiting_by_course = defaultdict(list)
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
            tab["groups"].append({"classId": cid, "header": group_header(g), "time": g["time"],
                                  "g": g, "cap": c.get("maxStudents") or SLOTS, "kids": kids,
                                  "course": course})

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
            "days_choices": DAYS_CHOICES}


# ---------------------------------------------------------------- Excel
BLUE, GREEN = "2E86DE", "3BAA35"
LIGHT_GREEN, PINK = "D9F2D0", "F9C9DD"


def to_xlsx(model: dict, path: str) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    wb.remove(wb.active)
    FONT = "Calibri"
    fill = lambda c: PatternFill("solid", fgColor=c)
    widths = [3.6, 22, 7, 8.2, 7.5]               # №, Ф.И., Дни, Д.р., Отм.
    for tab in model["tabs"]:
        ws = wb.create_sheet(tab["sheet"])
        ncols = 3 * 5 + 2
        for col in range(3):
            for i, w in enumerate(widths):
                ws.column_dimensions[_col(col * 6 + i + 1)].width = w
            if col < 2:
                ws.column_dimensions[_col(col * 6 + 6)].width = 1.5
        ws.cell(1, 1, tab["title"]).font = Font(name=FONT, size=13, bold=True)
        ws.cell(1, 1).fill = fill(PINK)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=5)
        ws.cell(1, 7, f"из МойКласса {model['built']}").font = Font(name=FONT, size=9, italic=True, color="666666")
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
            hdr = ws.cell(top, c0)
            if b["kind"] == "group":
                ws.cell(top, c0, b["header"]).font = Font(name=FONT, size=10, bold=True)
                ws.merge_cells(start_row=top, start_column=c0, end_row=top, end_column=c0 + 3)
                tc = ws.cell(top, c0 + 4, b["time"])
                tc.fill = fill(LIGHT_GREEN)
                tc.font = Font(name=FONT, size=11, bold=True)
                tc.alignment = Alignment(horizontal="center")
            else:
                ws.cell(top, c0, b["header"]).font = Font(name=FONT, size=11, bold=True)
                for k in range(5):
                    ws.cell(top, c0 + k).fill = fill(LIGHT_GREEN)
                ws.merge_cells(start_row=top, start_column=c0, end_row=top, end_column=c0 + 4)
            hdr.alignment = Alignment(horizontal="left", vertical="center", shrink_to_fit=True)
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
                if b["kind"] in ("group", "wait"):
                    dv.add(ws.cell(rr, c0 + 2))
            if b["kind"] == "group":
                for i, k in enumerate(b["kids"][:N]):
                    rr = top + 1 + i
                    ws.cell(rr, c0 + 1, k["name"])
                    ws.cell(rr, c0 + 2, k["days"])
                    d = ws.cell(rr, c0 + 3, k["bd"])
                    d.font = Font(name=FONT, size=9)
                    ws.cell(rr, c0 + 4, k["mark"])
                extra = b["kids"][N:]
                fr = top + 1 + N
                ws.cell(fr, c0, "Свободно:").font = Font(name=FONT, size=10, bold=True)
                ws.merge_cells(start_row=fr, start_column=c0, end_row=fr, end_column=c0 + 1)
                ws.cell(fr, c0 + 1).alignment = Alignment(horizontal="left")
                if extra:
                    ws.cell(fr, c0 + 2, "ещё: " + ", ".join(k["name"] for k in extra)).font = Font(name=FONT, size=8, color="C00000")
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
                    ws.cell(r1, c0 + 1, ", ".join(k["name"] for k in s["kids"]))
                    ws.cell(r1 + 1, c0 + 1, ", ".join(k["phone"] for k in s["kids"] if k["phone"]))
                    ws.cell(r1 + 1, c0 + 1).font = Font(name=FONT, size=10)
                    if s["kids"]:
                        ws.cell(r1, c0 + 3, s["kids"][0]["bd"]).font = Font(name=FONT, size=9)
            else:
                for i, k in enumerate(b["kids"]):
                    rr = top + 1 + i
                    ws.cell(rr, c0 + 1, k["name"])
                    ws.cell(rr, c0 + 2, k.get("note", ""))
                    ws.cell(rr, c0 + 3, k["age"])
                    ws.cell(rr, c0 + 4, k["mark"])
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_margins.left = ws.page_margins.right = 0.25
        ws.page_margins.top = ws.page_margins.bottom = 0.3
        ws.print_title_rows = "1:1"
    wb.save(path)


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
                      (k, str(v)[:2000], dt.datetime.now().isoformat(timespec="seconds")))


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
    d0, d1 = (today - dt.timedelta(days=7)).isoformat(), (today + dt.timedelta(days=14)).isoformat()
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
    return build_model(meta, lessons, joins, users, today)


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


def with_edits(m: dict) -> dict:
    """Модель + правки: скрытые/перенесённые дети, ручные строки, цвета, пометки."""
    import copy
    m = copy.deepcopy(m)
    E = edits()
    blocks_by_key = {}
    for t in m["tabs"]:
        for i, b in enumerate(t["blocks"]):
            b["key"] = _bkey(t, b, i)
            blocks_by_key[b["key"]] = b
    moved = []
    for t in m["tabs"]:
        for b in t["blocks"]:
            if b["kind"] == "indiv":
                continue
            keep = []
            for k in b["kids"]:
                ck = f"u:{b['key']}:{k['uid']}"
                k["ck"] = ck
                for f in ("days", "mark", "color", "note", "name"):
                    if f"{ck}:{f}" in E:
                        k[f] = E[f"{ck}:{f}"]
                to = E.get(f"{ck}:move")
                if E.get(f"{ck}:hide"):
                    k["hidden"] = True
                if to and to in blocks_by_key and to != b["key"]:
                    k["moved_from"] = b.get("header", "")
                    moved.append((to, k))
                    continue
                keep.append(k)
            b["kids"] = keep
            extra = E.get(f"b:{b['key']}:manual")
            b["manual"] = _json.loads(extra) if extra else []
            b["free"] = E.get(f"b:{b['key']}:free", "")
    for to, k in moved:
        blocks_by_key[to]["kids"].append(k)
    for t in m["tabs"]:
        for b in t["blocks"]:
            if b["kind"] == "indiv":
                for s in b["slots"]:
                    s["ck"] = f"b:{b['key']}:{s['time'].replace('.', '')}"
                    s["note"] = E.get(s["ck"] + ":note", "")
                    s["color"] = E.get(s["ck"] + ":color", "")
    return m


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
        for b in t["blocks"]:
            if b["kind"] != "indiv":
                b["kids"] = [k for k in b["kids"] if not k.get("hidden")] + [
                    {"name": r.get("name", ""), "days": r.get("days", ""), "bd": r.get("bd", ""),
                     "mark": r.get("mark", ""), "age": "", "note": "", "color": r.get("color", "")}
                    for r in b.get("manual", [])]
        t["slots"] = max([len(b["kids"]) for b in t["blocks"] if b["kind"] == "group"] + [SLOTS])
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
        to_xlsx(m, f.name)
        _paint(f.name, m)
        return open(f.name, "rb").read()


def _paint(path: str, m: dict) -> None:
    """Зелёный/красный фон ФИО — как отметила администратор на странице."""
    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill
    wb = load_workbook(path)
    col = {"g": "C6EFCE", "r": "FFC7CE"}
    for t in m["tabs"]:
        ws = wb[t["sheet"]]
        N = t["slots"]
        for bi, b in enumerate(t["blocks"]):
            if b["kind"] == "indiv":
                continue
            page, c, r = bi // 9, (bi % 9) // 3, bi % 3
            top = 3 + (page * 3 + r) * (N + 3)
            for i, k in enumerate(b["kids"][:N]):
                if k.get("color") in col:
                    ws.cell(top + 1 + i, c * 6 + 2).fill = PatternFill("solid", fgColor=col[k["color"]])
    wb.save(path)
