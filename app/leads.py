"""Захват лидов с офлайн-каналов: лотерея на ДР, промоутеры, стойка в ТЦ, листовки.

Родитель сканирует QR → заполняет короткую форму → лид сразу в базе и
(если включено) уходит в МойКласс как заявка. Учитывается источник и
промокод канала, поэтому потом видно, какой канал сколько привёл.
"""

import json
import re
from datetime import datetime

from . import db, sync
from .moyklass_client import MoyklassClient, MoyklassError

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    source TEXT,          -- канал: lottery, promoter, yantar, flyer_zhk, screen...
    promo TEXT,           -- промокод канала: МЕТРО, ЯНТАРЬ, имя промоутера
    parent_name TEXT,
    phone TEXT,
    child_name TEXT,
    child_age TEXT,
    interests TEXT,       -- интересующие направления (через запятую)
    comment TEXT,
    consent_pd INTEGER,   -- согласие на обработку персданных (152-ФЗ)
    consent_ads INTEGER,  -- согласие на рекламные сообщения
    pushed_to_crm INTEGER DEFAULT 0,
    crm_user_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_leads_source ON leads(source);
CREATE INDEX IF NOT EXISTS idx_leads_created ON leads(created_at);
"""

# направления для галочек в форме
INTERESTS = [
    "Детский сад / мини-сад (ГКП)",
    "Нулевой класс",
    "Подготовка к школе",
    "Английский язык",
    "Логопед / запуск речи",
    "Раннее развитие (1–3 года)",
    "ИЗО-студия",
    "Ментальная арифметика / скорочтение",
    "Шахматы",
    "Летний лагерь",
]

SOURCES = {
    "lottery": "Лотерея на Дне рождения",
    "promoter": "Промоутер",
    "yantar": "Стойка в ТЦ «Янтарь»",
    "flyer": "Листовка по ящикам",
    "screen": "Видеоэкран",
    "metro": "Баннер в метро",
    "trailer": "Автоприцеп",
    "chat": "Чат ЖК",
    "partner": "Партнёр",
    "other": "Другое",
}


def init():
    with db.get_conn() as conn:
        conn.executescript(SCHEMA)


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return digits


def save_lead(data: dict) -> int:
    """Сохраняет лид локально. Возвращает id."""
    init()
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO leads(created_at, source, promo, parent_name, phone,
                   child_name, child_age, interests, comment, consent_pd, consent_ads)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (datetime.now().isoformat(timespec="seconds"),
             data.get("source", "other"), data.get("promo", ""),
             data.get("parent_name", "").strip(), normalize_phone(data.get("phone", "")),
             data.get("child_name", "").strip(), data.get("child_age", "").strip(),
             ", ".join(data.get("interests", [])), data.get("comment", "").strip(),
             1 if data.get("consent_pd") else 0,
             1 if data.get("consent_ads") else 0),
        )
        return cur.lastrowid


def push_to_crm(lead_id: int) -> tuple[bool, str]:
    """Отправляет лид в МойКласс как нового клиента с комментарием об источнике."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row:
        return False, "лид не найден"
    if row["pushed_to_crm"]:
        return True, "уже отправлен"

    api_key = sync.get_api_key()
    if not api_key:
        return False, "API-ключ МойКласс не задан"

    name = row["child_name"] or row["parent_name"] or "Лид без имени"
    source_label = SOURCES.get(row["source"], row["source"])
    comment_parts = [f"Источник: {source_label}"]
    if row["promo"]:
        comment_parts.append(f"Промокод: {row['promo']}")
    if row["parent_name"]:
        comment_parts.append(f"Родитель: {row['parent_name']}")
    if row["child_age"]:
        comment_parts.append(f"Возраст: {row['child_age']}")
    if row["interests"]:
        comment_parts.append(f"Интересы: {row['interests']}")
    if row["comment"]:
        comment_parts.append(f"Комментарий: {row['comment']}")

    client = None
    try:
        client = MoyklassClient(api_key)
        client.authenticate()
        payload = {
            "name": name,
            "phone": row["phone"],
            "advSourceId": None,
            "comment": " | ".join(comment_parts),
        }
        resp = client._request("POST", "/v1/company/users", None) if False else None
        # POST с телом: используем внутренний httpx-клиент напрямую
        r = client._client.post(
            f"{client.base_url}/v1/company/users",
            headers={"x-access-token": client._token},
            json=payload,
        )
        if r.status_code >= 400:
            return False, f"МойКласс ответил {r.status_code}: {r.text[:200]}"
        crm_id = (r.json() or {}).get("id")
        with db.get_conn() as conn:
            conn.execute("UPDATE leads SET pushed_to_crm=1, crm_user_id=? WHERE id=?",
                         (crm_id, lead_id))
        return True, f"создан клиент {crm_id}"
    except MoyklassError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    finally:
        if client is not None:
            client.close()


def stats_by_source() -> list[dict]:
    init()
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT source, promo, COUNT(*) c,
                      SUM(pushed_to_crm) pushed,
                      MIN(created_at) first_at, MAX(created_at) last_at
               FROM leads GROUP BY source, promo ORDER BY c DESC""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["source_label"] = SOURCES.get(r["source"], r["source"])
        out.append(d)
    return out


def recent(limit: int = 200) -> list[dict]:
    init()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
