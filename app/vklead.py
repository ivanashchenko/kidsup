"""Заявки из лид-форм ВК Рекламы → CRM, как заявки с сайта.

VK Ads API лиды из форм не отдаёт: ни /lead_ads/leads.json, ни
/lead_forms/{id}/leads.json — 404 под нашим токеном (проверено 15.09.2026).
Кабинет выгружает их своим внутренним прокси myTarget:

    https://ads.vk.ru/proxy/mt/v1/lead_ads/lead_forms/{form}/leads.csv
        ?sudo=<кабинет клиента>&account=<id>&_created_at__gte=…&_created_at__lte=…

и он работает только с живой сессией. Поэтому забираем CSV из браузера на
сервере (mkweb, состояние «vk»), а дальше заявка идёт тем же путём, что и с
сайта: уведомление дежурному, мгновенный перезвон, карточка в МойКлассе.

Лид без обработки умирает за часы, поэтому проверка идёт каждые 10 минут,
а не раз в день.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import date, datetime, timedelta

from . import db

log = logging.getLogger(__name__)

SUDO = "vkads_203149219%40vk%401627163"     # кабинет KidsUP глазами таргетолога
ACCOUNT = "22378297"
BASE = "https://ads.vk.ru/proxy/mt/v1/lead_ads/lead_forms/{form}/leads.csv"
PAGE = "https://ads.vk.ru/hq/leadads/leadforms?sudo=" + SUDO


def _forms() -> list[str]:
    """Какие формы опрашиваем (настройка vk_lead_forms, по умолчанию наша)."""
    raw = (db.get_setting("vk_lead_forms", "") or "994206").strip()
    return [x.strip() for x in re.split(r"[,\s]+", raw) if x.strip().isdigit()]


def _ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS vk_leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT, form TEXT, lead_ts TEXT,
        phone TEXT, name TEXT, raw TEXT, ts TEXT, status TEXT)""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_vk_leads_uniq "
                 "ON vk_leads (form, lead_ts, phone)")
    conn.execute("DELETE FROM vk_leads WHERE status='dry'")   # следы старых проверок


def _digits(v: str) -> str:
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    if len(d) == 11 and d[0] == "8":
        d = "7" + d[1:]
    if len(d) == 10:
        d = "7" + d
    return d


def fetch_csv(form: str, days: int = 7) -> str:
    """CSV заявок формы за последние days дней — через браузер на сервере."""
    from . import mkweb
    gte = (date.today() - timedelta(days=days)).isoformat() + " 00:00:00"
    lte = date.today().isoformat() + " 23:59:59"
    url = (BASE.format(form=form) + f"?sudo={SUDO}&account={ACCOUNT}"
           + f"&_created_at__gte={gte.replace(' ', '+').replace(':', '%3A')}"
           + f"&_created_at__lte={lte.replace(' ', '+').replace(':', '%3A')}")
    js = ("(async()=>{const r=await fetch(" + json.dumps(url)
          + ",{credentials:'include'});const t=await r.text();"
            "return JSON.stringify({status:r.status,text:t});})()")
    res = mkweb.open_page(PAGE, wait_ms=7000, max_text=200, state="vk",
                          actions=[{"js": js}])
    if not res.get("ok"):
        raise RuntimeError(f"браузер ВК: {res.get('error')}")
    out = (res.get("actions") or [{}])[0].get("result")
    if not out:
        raise RuntimeError("пустой ответ от кабинета ВК")
    data = json.loads(out)
    if int(data.get("status") or 0) != 200:
        raise RuntimeError(f"выгрузка лидов: HTTP {data.get('status')}")
    return data.get("text") or ""


def parse(text: str) -> list[dict]:
    """Строки CSV → заявки. Колонки формы произвольны: имя и телефон
    забираем по названию, остальное складываем в примечание."""
    rows = list(csv.DictReader(io.StringIO(text)))
    out = []
    for r in rows:
        phone = ""
        name = ""
        extra = []
        for k, v in r.items():
            kl = (k or "").strip().lower()
            v = (v or "").strip()
            if not v:
                continue
            if "телефон" in kl or "phone" in kl:
                phone = _digits(v)
            elif kl in ("имя", "name", "фио"):
                name = v
            elif kl.startswith("id ") or "время лида" in kl:
                continue
            else:
                extra.append(f"{k.strip()}: {v}")
        if len(phone) != 11:
            continue
        out.append({"phone": phone, "name": name,
                    "lead_ts": (r.get("Время лида") or "").strip()[:19],
                    "extra": " · ".join(extra),
                    "campaign": (r.get("ID Кампании") or "").strip(),
                    "banner": (r.get("ID Объявления") or "").strip()})
    return out


def sync(days: int = 7, dry: bool = False, forms: list | None = None) -> dict:
    """Забрать новые заявки из форм и довести до CRM. Повторы отсекаются
    по (форма, время лида, телефон) — выгрузка всегда идёт за неделю."""
    stat = {"форм": 0, "строк": 0, "новых": 0, "ошибок": 0, "формы": {}}
    for form in (forms or _forms()):
        stat["форм"] += 1
        try:
            leads = parse(fetch_csv(form, days))
        except Exception as e:  # noqa: BLE001
            log.warning("лид-форма %s: %s", form, e)
            stat["ошибок"] += 1
            stat["формы"][form] = f"ошибка: {str(e)[:120]}"
            continue
        stat["строк"] += len(leads)
        new = 0
        for ld in leads:
            with db.get_conn() as conn:
                _ensure(conn)
                if dry:
                    # проверка парсера ничего не пишет: иначе после неё
                    # настоящая заявка сочтётся уже обработанной и потеряется
                    seen = conn.execute(
                        "SELECT 1 FROM vk_leads WHERE form=? AND lead_ts=? AND phone=?",
                        (form, ld["lead_ts"], ld["phone"])).fetchone()
                    if not seen:
                        new += 1
                        stat.setdefault("образец", []).append(
                            {k: ld[k] for k in ("phone", "name", "lead_ts", "extra")})
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO vk_leads (form, lead_ts, phone, name, raw, ts, status) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (form, ld["lead_ts"], ld["phone"], ld["name"],
                     json.dumps(ld, ensure_ascii=False),
                     datetime.now().isoformat(timespec="seconds"), "new"))
                if not cur.rowcount:
                    continue
            new += 1
            try:
                _to_crm(form, ld)
                with db.get_conn() as conn:
                    conn.execute("UPDATE vk_leads SET status='ok' WHERE form=? AND lead_ts=? AND phone=?",
                                 (form, ld["lead_ts"], ld["phone"]))
            except Exception as e:  # noqa: BLE001
                log.exception("лид ВК %s не доехал", ld["phone"][-4:])
                stat["ошибок"] += 1
                with db.get_conn() as conn:
                    conn.execute("UPDATE vk_leads SET status=? WHERE form=? AND lead_ts=? AND phone=?",
                                 (f"ошибка: {str(e)[:120]}", form, ld["lead_ts"], ld["phone"]))
        stat["новых"] += new
        stat["формы"][form] = f"{len(leads)} строк, {new} новых"
    return stat


def _to_crm(form: str, ld: dict) -> None:
    """Тот же путь, что у заявки с сайта: дежурному, перезвон, карточка."""
    from .main import _lead_to_crm          # импорт здесь — main тянет пол-мира
    note = "Лид-форма ВК"
    if ld.get("campaign"):
        note += f" (кампания {ld['campaign']})"
    if ld.get("extra"):
        note += ": " + ld["extra"]
    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS site_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
            child TEXT, age TEXT, course TEXT, note TEXT, roistat TEXT, ip TEXT)""")
        cur = conn.execute(
            "INSERT INTO site_leads (ts, phone, child, age, course, note, roistat, ip, crm_status) "
            "VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?)",
            (ld["phone"], ld.get("name") or "", "", "Заявка из ВК",
             note, f"vk_leadform_{form}", "vk", "pending"))
        lead_id = cur.lastrowid
    _lead_to_crm({"phone": ld["phone"], "child": ld.get("name") or "",
                  "age": "", "course": "Заявка из ВК", "note": note,
                  "roistat": f"vk_leadform_{form}", "lead_id": lead_id})
