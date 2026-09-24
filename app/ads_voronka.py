"""Воронка рекламы по каналам: заявка с сайта → запись → пришёл → оплатил → выручка.

25.09.2026, разбор рекламы (TR-12). До этого воронку по каналам собирали вручную —
158–166 запросов к /api/pult/sledy, а он отдаёт только 5 последних платежей,
поэтому цифры были приблизительные. Здесь всё из локальной базы и только чтение:

  • заявки — site_leads за N дней (без спама, дублей и тестов), по телефону;
  • канал — по меткам в note (utm_*, roistat=…, yclid, ysclid) и в utm (JSON
    касаний из ku-ref.js); у телефона с несколькими заявками берём самый
    «рекламный» канал;
  • карточка — mk_user_id из заявки, иначе все карточки users с тем же номером
    (у семьи с двумя детьми их две);
  • записался — есть запись на урок с даты заявки, пришёл — visit=1 на прошедшем
    уроке, оплатил — income > 0 с даты заявки; возвраты (refund) вычитаются из
    выручки. Списания (debit) — это признание выручки, а не деньги от семьи,
    их не считаем.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import db

# порядок = приоритет: у телефона с несколькими заявками побеждает канал выше
KANALY = ["Директ поиск", "Директ бренд", "Директ РСЯ", "Я.Бизнес / Карты",
          "Яндекс реклама (только yclid)", "VK", "Прочие метки", "Органика",
          "Без меток"]

BRAND = re.compile(r"kids\s*-?\s*up|кидс\s*-?\s*ап|кидсап|кидз\s*ап|кидсуп|kidsup", re.I)
# значение — до & или |: ku-ref.js кладёт метки раскодированными, в utm_term бывают пробелы
_PARAM = re.compile(r"(?:^|[|&?\s])(utm_[a-z_]+|yclid|ysclid|roistat|rs)=([^&|]*)", re.I)
# статусы, которые не заявки: боты, повторы, отклонённые номера (T3/T4 — если появятся)
NE_ZAYAVKA = ("spam_nojs", "dup", "honeypot", "bad_phone")
TEST_PHONES = {"79000000000", "79000000001", "79990000000", "79990000001",
               "79990000002", "79990000003", "79160000001"}


def _params(note: str | None, utm: str | None = None) -> dict:
    """Метки из note («… | utm_source=…&roistat=…») и из JSON касаний utm.
    note первичен: он пришёл вместе с этой заявкой."""
    p: dict[str, str] = {}
    for k, v in _PARAM.findall(note or ""):
        p.setdefault(k.lower(), v.strip())
    if utm:
        try:
            j = json.loads(utm)
        except (ValueError, TypeError):
            j = None
        if isinstance(j, dict):
            for part in ("lt", "ft"):
                t = j.get(part)
                if isinstance(t, dict) and isinstance(t.get("p"), dict):
                    for k, v in t["p"].items():
                        p.setdefault(str(k).lower(), str(v))
    return p


def kanal(note: str | None, utm: str | None = None, roistat_col: str | None = None) -> str:
    """Канал заявки по меткам. Правила — из ручного разбора 24.09 (classify.py).

    roistat_col — колонка roistat заявки. С сайта там номер визита Roistat (не метка),
    а лид-формы ВК (vklead.py) пишут туда vk_leadform_<форма> и note «Лид-форма ВК…»
    без utm — 25.09.2026 после ревью: такие заявки попадали в «Без меток»."""
    if (str(roistat_col or "").lower().startswith("vk_leadform")
            or (note or "").startswith("Лид-форма ВК")):
        return "VK"
    p = _params(note, utm)
    src = (p.get("utm_source") or "").lower()
    med = (p.get("utm_medium") or "").lower()
    camp = (p.get("utm_campaign") or "").lower()
    rs = (p.get("roistat") or p.get("rs") or "").lower()
    low = (note or "").lower()
    if src == "e2e" or rs.startswith("e2e"):
        return "тест"
    if "geoadv" in src or rs.startswith("yandex.business") or "geoadv" in low:
        return "Я.Бизнес / Карты"
    direct = rs.startswith("direct") or (src in ("yandex", "ya", "yandex_direct", "direct")
                                          and med in ("cpc", "ppc", ""))
    if direct:
        if "context" in rs or "rsya" in camp:
            return "Директ РСЯ"
        # в roistat=direct1_search_<объявление>_<фраза> фраза — после второго «_»
        fraza = " ".join([p.get("utm_term") or "", rs.split("_", 3)[-1] if rs.count("_") >= 3 else ""])
        if BRAND.search(fraza.replace("+", " ")):
            return "Директ бренд"
        return "Директ поиск"
    if src in ("vk", "vk_ads", "vkads", "vkontakte", "mytarget") or rs.startswith("vk"):
        return "VK"
    if p.get("yclid"):
        return "Яндекс реклама (только yclid)"
    if src:
        return "Прочие метки"
    if p.get("ysclid"):
        return "Органика"
    return "Без меток"


def _p10(phone) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit())[-10:]


def _is_test(r: dict) -> bool:
    note = (r.get("note") or "").lower()
    course = (r.get("course") or "").lower()
    return (r.get("phone") in TEST_PHONES or "utm_source=e2e" in note or "тест" in note
            or "проверк" in course or "тест" in course
            or "e2e" in (r.get("roistat") or ""))


def voronka(days: int = 30, details: bool = False) -> dict:
    days = max(1, min(int(days or 30), 365))
    today = date.today().isoformat()
    with db.get_conn() as conn:
        has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='site_leads'").fetchone()
        if not has:
            return {"days": days, "kanaly": [], "itogo": {}, "note": "таблицы site_leads ещё нет"}
        cols = {r[1] for r in conn.execute("PRAGMA table_info(site_leads)").fetchall()}
        want = ["id", "ts", "phone", "course", "note", "roistat", "crm_status", "mk_user_id",
                "utm", "ym_cid", "yclid", "landing"]
        sel = ", ".join(c if c in cols else f"NULL AS {c}" for c in want)
        rows = [dict(r) for r in conn.execute(
            f"SELECT {sel} FROM site_leads WHERE ts >= datetime('now', ?) ORDER BY ts",
            (f"-{days} days",)).fetchall()]

        # заявки → семьи (по телефону)
        propusk = defaultdict(int)
        fam: dict[str, dict] = {}
        for r in rows:
            st = (r.get("crm_status") or "")
            if st in NE_ZAYAVKA:
                propusk[st] += 1
                continue
            if _is_test(r):
                propusk["тест"] += 1
                continue
            ph = _p10(r.get("phone"))
            if len(ph) != 10:
                propusk["без телефона"] += 1
                continue
            ch = kanal(r.get("note"), r.get("utm"), r.get("roistat"))
            if ch == "тест":
                propusk["тест"] += 1
                continue
            f = fam.setdefault(ph, {"phone": "7" + ph, "first": r["ts"], "kanal": ch,
                                    "zayavok": 0, "uids": set(), "kursy": [],
                                    "ym_cid": "", "yclid": "", "landing": ""})
            f["zayavok"] += 1
            if KANALY.index(ch) < KANALY.index(f["kanal"]):
                f["kanal"] = ch
            if r.get("mk_user_id"):
                f["uids"].add(int(r["mk_user_id"]))
            if r.get("course") and r["course"] not in f["kursy"]:
                f["kursy"].append(r["course"])
            for k in ("ym_cid", "yclid", "landing"):
                if r.get(k) and not f[k]:
                    f[k] = r[k]

        for ph, f in fam.items():
            ids = {int(x[0]) for x in conn.execute(
                "SELECT id FROM users WHERE substr(replace(replace(replace(replace(replace("
                "phone,' ',''),'-',''),'+',''),'(',''),')',''), -10) = ?", (ph,)).fetchall()}
            f["uids"] |= ids
            d0 = f["first"][:10]
            f.update(zapisalsya=False, doshel=False, oplatil=False, income=0.0,
                     refund=0.0, staraya=False)
            if not f["uids"]:
                continue
            q = ",".join("?" * len(f["uids"]))
            u = list(f["uids"])
            f["zapisalsya"] = bool(conn.execute(
                f"SELECT 1 FROM lesson_records lr JOIN lessons l ON l.id = lr.lesson_id "
                f"WHERE lr.user_id IN ({q}) AND substr(l.date,1,10) >= ? LIMIT 1",
                (*u, d0)).fetchone())
            f["doshel"] = bool(conn.execute(
                f"SELECT 1 FROM lesson_records lr JOIN lessons l ON l.id = lr.lesson_id "
                f"WHERE lr.user_id IN ({q}) AND lr.visit = 1 "
                f"AND substr(l.date,1,10) >= ? AND substr(l.date,1,10) <= ? LIMIT 1",
                (*u, d0, today)).fetchone())
            for optype, s in conn.execute(
                    f"SELECT optype, SUM(ABS(summa)) FROM payments WHERE user_id IN ({q}) "
                    f"AND substr(date,1,10) >= ? AND optype IN ('income','refund') "
                    f"AND (optype = 'refund' OR summa > 0) GROUP BY optype",
                    (*u, d0)).fetchall():
                f[optype] = round(float(s or 0), 2)
            f["oplatil"] = f["income"] > 0
            # карточка заведена задолго до заявки — это не новый клиент, а старый
            created = [x[0] for x in conn.execute(
                f"SELECT created_at FROM users WHERE id IN ({q}) AND created_at IS NOT NULL", u)]
            try:
                first_card = min(date.fromisoformat(str(c)[:10]) for c in created) if created else None
            except ValueError:
                first_card = None
            f["staraya"] = bool(first_card and first_card < date.fromisoformat(d0) - timedelta(days=30))

    agg: dict[str, dict] = {}
    for f in fam.values():
        a = agg.setdefault(f["kanal"], {"kanal": f["kanal"], "semey": 0, "zayavok": 0,
                                        "novyh": 0, "zapisalis": 0, "doshli": 0,
                                        "oplatili": 0, "income": 0.0, "refund": 0.0,
                                        "bez_kartochki": 0, "s_clientid": 0})
        a["semey"] += 1
        a["zayavok"] += f["zayavok"]
        a["novyh"] += not f["staraya"]
        a["zapisalis"] += f["zapisalsya"]
        a["doshli"] += f["doshel"]
        a["oplatili"] += f["oplatil"]
        a["income"] += f["income"]
        a["refund"] += f["refund"]
        a["bez_kartochki"] += not f["uids"]
        a["s_clientid"] += bool(f["ym_cid"])
    kanaly = []
    for ch in KANALY:
        if ch not in agg:
            continue
        a = agg[ch]
        a["income"] = round(a["income"], 2)
        a["refund"] = round(a["refund"], 2)
        a["vyruchka"] = round(a["income"] - a["refund"], 2)
        a["konv_oplata"] = round(a["oplatili"] / a["semey"], 3) if a["semey"] else 0
        kanaly.append(a)
    itogo = {k: sum(a[k] for a in kanaly) for k in
             ("semey", "zayavok", "novyh", "zapisalis", "doshli", "oplatili",
              "income", "refund", "vyruchka", "bez_kartochki", "s_clientid")}
    out = {"days": days, "s": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d"),
           "po": today, "kanaly": kanaly, "itogo": itogo, "propushcheno": dict(propusk),
           "pravila": "семья = телефон; канал — самый рекламный из её заявок; "
                      "оплатил — income > 0 с даты первой заявки; выручка = income − refund"}
    if details:
        out["semi"] = [{**{k: v for k, v in f.items() if k != "uids"},
                        "uids": sorted(f["uids"])}
                       for f in sorted(fam.values(), key=lambda x: x["first"])]
    return out
