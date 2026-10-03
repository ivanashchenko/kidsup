# -*- coding: utf-8 -*-
"""Обзвон дня: звонки из Пульта одного админа на один день — по спискам.

03.10.2026, Борис: «добавь на сайт — хочу Ане дать прозванивать». Те же
пункты, что лежат в Пульте (новые заявки, «думает, платили раньше»,
недозвоны, база 2024/25), но одной лентой на звонок: дети с возрастом,
телефон, какую группу предложить и сколько в ней сейчас мест. Итог звонка
пишется сразу в три места — карточку МойКласса, пункт Пульта и журнал
этой страницы, — чтобы Борис видел результат, а не только галочки.
"""
import re
from datetime import datetime

from . import db

KINDS = {
    "new": "Новые заявки",
    "think": "Думают, платили раньше",
    "base": "База 2024/25",
    "miss": "Недозвоны",
}
# Порядок вкладок — как Борис поставил Ане 03.10: новые → думают → база → недозвоны.
ITOGI = {
    "zap": "записали на пробное",
    "dum": "думают, перезвонить",
    "ned": "не дозвонились",
    "otk": "отказ",
}


def _init(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS obzvon_dnya (item_id INTEGER PRIMARY KEY, "
                 "itog TEXT, note TEXT, who TEXT, ts TEXT)")


_NEW = re.compile(r"НОВАЯ ЗАЯВКА|ПРОПУЩЕННЫЙ ЗВОНОК|Летняя заявка|Заявка с Директа|заявка с сайта|"
                  r"\(карточки нет\)|^Позвонить \+?7\d|^Звонить на|сам звонил")
_MISS = re.compile(r"НЕДОЗВОН|Недозвон|Последняя попытка|недозвон")
_BASE = re.compile(r"^БАЗА|Бывшие ученики|летнем лагере|в прошлом году|до весны")
# Не звонки: переписка, статусы, уборка CRM — это Ира (решение Бориса 03.10).
_NE_ZVONOK = re.compile(r"Похоже на отказ|Клиент писал, ответа нет|КЛИЕНТ ЖДЁТ ОТВЕТА|^Отказ \(|качество карточек|"
                        r"без менеджера|^CRM:|Гигиена статусов|Хвост: заявка|Заявка висит «новой»|ИМЕНА КАРТОЧЕК|"
                        r"\+?734001|Статусы после разбора")
_THINK = re.compile(r"^ДУМАЕТ|ВЕРНУТЬ|решения нет|ОТЛОЖЕННЫЙ ЗВОНОК")


def _chist(text: str) -> str:
    return re.sub(r"^⏳ с \d\d\.\d\d:\s*", "", text or "").replace("🤖 Клод: ", "").strip()


def _kind(text: str, source: str, svoy: bool = True) -> str | None:
    """Вкладка для пункта. svoy — пункт лежит на том, кто звонит: тогда любой
    его пункт — звонок (Пульт Ани на 03–04.10 — только обзвон), и то, что не
    узнали по словам, идёт в «думают» (это семьи, с которыми уже говорили).
    Чужие пункты (у Иры, Лены) берём только по точным признакам звонка."""
    t = _chist(text)
    if _NE_ZVONOK.search(t):
        return None
    if _NEW.search(t):
        return "new"
    if _MISS.search(t):
        return "miss"
    if _BASE.search(t):
        return "base"
    if _THINK.search(t):
        return "think"
    return "think" if svoy else None


def razobrat(text: str) -> dict:
    """Пункт Пульта → дети, пометка и предложенные группы (без дат: в пунктах,
    перенесённых с прошлых дней, они уже прошли)."""
    t = _chist(text)
    if "НОВАЯ ЗАЯВКА" in t:
        m = re.search(r"позвонить в течение 5 минут!\s*(.*)", t)
        rest = m.group(1) if m else t
        name = rest.split(",")[0].strip()
        if name.lower().startswith("имя не"):
            name = "Имя не указано"
        note = re.sub(r",?\s*тел\. \+\d+", "", rest[len(rest.split(",")[0]):]).strip(" ,.")
        note = note.replace("Свежий лид конвертируется в разы лучше", "").strip(" .")
        return {"name": name, "kids": [], "meta": note or "заявка с сайта", "offer": []}
    m = (re.search(r"\): (.*?)\. (?:Предложить|Начать|Дозвонились)", t)
         or re.search(r": (.*?)\. (?:Предложить|Начать|Дозвонились)", t))
    kto = m.group(1) if m else ""
    kids = [{"n": a.strip(), "age": b} for a, b in re.findall(r"([^,()]+?)\s*\((\d+,\d)\)", kto)]
    if not kids and kto:
        kids = [{"n": kto.strip(), "age": ""}]
    meta = ""
    if t.startswith("ДУМАЕТ"):
        d = re.search(r"(\d+) дн\. без контакта", t)
        meta = f"{d.group(1)} дн. без контакта" if d else ""
    elif t.startswith("НЕДОЗВОН"):
        a = re.search(r"попытка (\d) из 3", t)
        w = re.search(r"звонить ([^(:]+?)(?:\s*\(|:)", t)
        meta = (f"попытка {a.group(1)} из 3" if a else "") + (f" · {w.group(1).strip()}" if w else "")
    elif t.startswith("БАЗА"):
        b = re.search(r"БАЗА 2024/25 \(([^)]*)\)", t)
        meta = re.sub(r"\s+,", ",", b.group(1)) if b else ""
    if "платили раньше" in t and not t.startswith("БАЗА"):
        meta += " · платили раньше"
    offers = []
    off = re.search(r"(?:Предложить|предложить): (.*?)(?:\. Итог|$)", t)
    if off:
        for part in off.group(1).split(";"):
            part = part.strip()
            pm = re.match(r"(?:(\S+) → )?(.+?):\s", part + " ")
            if pm:
                platno = re.search(r"пробное ([\d\s]+₽)", part)
                offers.append(((pm.group(1) + " → ") if pm.group(1) else "") + pm.group(2).strip()
                              + (f" (пробное {platno.group(1)})" if platno else ""))
            elif "возра" in part:
                offers.append("по возрасту — уточнить")
    if not kids and not offers and not meta:
        m = re.search(r"ПРОПУЩЕННЫЙ ЗВОНОК от \+?(\d+)", t)
        if m:
            return {"name": "Пропущенный звонок", "kids": [], "offer": [],
                    "meta": "клиент звонил сам и не дозвонился — перезвонить первым делом"}
        m = re.match(r"Недозвон, попытка №(\d): (.+?) \+?7\d{10} — (.*)", t)
        if m:
            return {"name": m.group(2), "kids": [], "offer": [], "meta": f"попытка {m.group(1)} · {m.group(3)[:260]}"}
        m = re.match(r"Летняя заявка[^«]*«([^»]+)»,?\s*(?:\d{11},?\s*)?(.*)", t)
        if m:
            return {"name": m.group(1), "kids": [], "offer": [], "meta": "летняя заявка без ответа · " + m.group(2)[:260]}
        m = re.match(r"ВЕРНУТЬ \(([^)]*)\): (.+?): (.*)", t)
        if m:
            return {"name": m.group(2), "kids": [], "offer": [], "meta": f"вернуть ({m.group(1)}) · {m.group(3)[:260]}"}
        # Пункт свободной формы («Орлова Алиса (3 г.), мама …. Утром написать…»):
        # имя — до первой запятой или двоеточия, остальное — что сделать.
        m = re.match(r"(.{3,70}?)(?:[,:]|\s—\s)\s*(.*)", t, re.S)
        name, rest = (m.group(1), m.group(2)) if m else (t[:60], t[60:])
        return {"name": name.strip(), "kids": [], "meta": rest.strip()[:320], "offer": []}
    return {"name": ", ".join(k["n"] for k in kids) or "—", "kids": kids,
            "meta": meta.strip(" ·"), "offer": offers}


def _legenda() -> list[dict]:
    """Свободные места по группам — ключи в том же виде, что в пунктах («ПШ Гр9»)."""
    from . import mesta
    try:
        r = mesta.razrez()
    except Exception:
        return []
    pref = {"Подготовка к школе": "ПШ", "Английский": "англ.", "ИЗО": "ИЗО", "Шахматы": "шахматы",
            "Робототехника": "робототехника", "Мини-сад": "мини-сад", "Нулевой класс": "нулевой класс"}
    out = []
    for p, d in (r.get("предметы") or {}).items():
        if p.startswith("Логопед"):
            continue
        for g in d.get("группы") or []:
            n = g["name"].replace("2627_", "")
            m = re.search(r"\(Гр(\d+)", n) or re.search(r"Группа (\d+)", n)
            if p == "Раннее развитие":
                pr = "Музыка и речь" if "Музыка" in n else "Первая школа" if "Первая школа" in n else "Лицей"
            else:
                pr = pref.get(p, p)
            key = f"{pr} Гр{m.group(1)}" if m else pr
            if p == "Ментальная арифметика":
                vs = re.search(r"вс_(\d\d:\d\d)", n)
                key = f"МА вс {vs.group(1)}" if vs else "МА"
            out.append({"key": key, "name": n, "free": g.get("свободно", 0),
                        "mest": g.get("мест", 0), "note": g.get("пометка") or ""})
    return out


def spisok(day: str, who: str) -> dict:
    with db.get_conn() as conn:
        _init(conn)
        rows = conn.execute("SELECT id, who, text, phone, source, done FROM plan_inbox "
                            "WHERE day=? ORDER BY id", (day,)).fetchall()
        itogi = {r["item_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM obzvon_dnya WHERE item_id IN (%s)" % ",".join(str(r["id"]) for r in rows)
        ).fetchall()} if rows else {}
    items = []
    for r in rows:
        if r["who"] not in (who, "Ира", "Лена", "Аня"):
            continue
        k = _kind(r["text"], r["source"] or "", svoy=(r["who"] == who))
        if not k or (r["who"] != who and (r["done"] or not r["phone"])):
            continue
        it = razobrat(r["text"])
        it.update(id=r["id"], kind=k, phone=r["phone"] or "", done=bool(r["done"]),
                  itog=itogi.get(r["id"]), chej=r["who"])
        items.append(it)
    leg = _legenda()
    by = {}
    for g in leg:
        by.setdefault(g["key"], []).append(g)
    for it in items:
        it["offer"] = [{"t": o, "free": (by[k][0]["free"] if len(by.get(k, [])) == 1 else None)}
                       for o in it["offer"]
                       for k in [re.sub(r"\s*\(.*$", "", o.split(" → ")[-1]).strip()]]
    schet = {k: 0 for k in ITOGI}
    for it in items:
        if it["itog"]:
            schet[it["itog"]["itog"]] = schet.get(it["itog"]["itog"], 0) + 1
    return {"day": day, "who": who, "items": items, "legend": [g for g in leg if g["free"] > 0],
            "kinds": KINDS, "itogi": ITOGI, "schet": schet}


def otmetit(item_id: int, itog: str, note: str, who: str) -> dict:
    """Итог звонка: журнал страницы + комментарий в карточку + пункт Пульта.
    «Не дозвонились» пункт не закрывает — попытка не последняя."""
    if itog not in ITOGI:
        raise ValueError("итог: " + ", ".join(ITOGI))
    with db.get_conn() as conn:
        _init(conn)
        row = conn.execute("SELECT id, text, phone FROM plan_inbox WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise ValueError("нет такого пункта")
        now = datetime.now().strftime("%d.%m %H:%M")
        conn.execute("INSERT OR REPLACE INTO obzvon_dnya (item_id, itog, note, who, ts) VALUES (?,?,?,?,?)",
                     (item_id, itog, note[:300], who[:20], datetime.now().isoformat(timespec="seconds")))
        pometka = f"итог {now} ({who or 'админ'}): {ITOGI[itog]}" + (f" — {note[:150]}" if note else "")
        conn.execute("UPDATE plan_inbox SET done=?, text=substr(text||' — '||?, 1, 600) WHERE id=?",
                     (0 if itog == "ned" else 1, pometka, item_id))
        phone = row["phone"] or ""
    kartochka = None
    if phone:
        try:
            from . import svidetelstva, sync
            from .moyklass_client import MoyklassClient
            from datetime import date as _d, timedelta as _td
            with db.get_conn() as conn:
                s = svidetelstva._sledy(conn, svidetelstva._p10(phone),
                                        (_d.today() - _td(days=30)).isoformat(), _d.today().isoformat())
            karty = s.get("карточки") or []
            if karty:
                kartochka = karty[0]["id"]
                mk = MoyklassClient(sync.get_api_key())
                try:
                    mk.post("/v1/company/userComments", {
                        "userId": kartochka, "showToUser": False,
                        "comment": f"{now}, обзвон ({who or 'админ'}): {ITOGI[itog]}." + (f" {note}" if note else "")})
                finally:
                    mk.close()
        except Exception as e:  # комментарий — не повод терять итог
            return {"ok": True, "kartochka": None, "oshibka_kommentariya": str(e)[:200]}
    return {"ok": True, "kartochka": kartochka}
