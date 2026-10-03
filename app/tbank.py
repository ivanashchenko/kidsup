# -*- coding: utf-8 -*-
"""Т-Банк Бизнес, T-API — только чтение: счета и выписка.

03.10.2026 Борис выпустил токен T-API с правом «выписки» и привязкой к IP
сервера app.kidsup.ru (161.104.16.236) — поэтому ходим в банк только отсюда,
с сервера. Токен — в настройке tbank_token (страница /owner), в код и в
репозиторий не попадает.

Зачем: утренняя сверка «поступления на счёт ↔ оплаты в МойКлассе ↔ чеки
Комтета». Платёжных методов здесь нет и быть не должно: get() пускает только
GET и только пути /api/…
"""
from __future__ import annotations

import datetime as dt
import logging
import ssl
from pathlib import Path

import httpx

from . import db

log = logging.getLogger("kidsup.tbank")
BASE = "https://business.tbank.ru/openapi"
# счёт «Клуб Буракова» на этом же ИП — не наши деньги, в сверку не берём
CHUZHIE = ("буракова",)


def _ssl() -> ssl.SSLContext:
    """business.tbank.ru подписан корнем Минцифры (Russian Trusted Root CA),
    которого нет в стандартном наборе. Доверяем ему только для запросов в банк:
    стандартные корни (certifi) + root/sub CA Минцифры из app/certs (взяты с
    gu-st.ru 03.10.2026, SHA-256 корня D2:6D:2D:02:…:CA:8E:CF:31)."""
    import certifi
    ctx = ssl.create_default_context(cafile=certifi.where())
    for f in sorted((Path(__file__).resolve().parent / "certs").glob("russian_trusted_*.crt")):
        ctx.load_verify_locations(cafile=str(f))
    return ctx


def _token() -> str:
    t = db.get_setting("tbank_token") or ""
    if not t:
        raise RuntimeError("не задан tbank_token — владелец вносит его на /owner")
    return t


def get(path: str, params: dict | None = None) -> dict | list:
    """GET к T-API. Только чтение: метод фиксирован, путь — /api/…"""
    if not path.startswith("/api/") or ".." in path:
        raise ValueError("путь T-API должен начинаться с /api/")
    r = httpx.get(BASE + path, params=params or {}, timeout=60, verify=_ssl(),
                  headers={"Authorization": f"Bearer {_token()}", "Accept": "application/json"})
    if r.status_code >= 400:
        raise RuntimeError(f"T-API {path}: HTTP {r.status_code} {r.text[:300]}")
    return r.json()


def accounts() -> list[dict]:
    """Счета компании (без счёта клуба Буракова — поле chuzhoy)."""
    data = get("/api/v4/bank-accounts")
    rows = data if isinstance(data, list) else (data.get("accounts") or data.get("items") or [])
    out = []
    for a in rows:
        name = str(a.get("name") or a.get("accountName") or "")
        out.append({"number": a.get("accountNumber"), "name": name, "currency": a.get("currency"),
                    "balance": (a.get("balance") or {}).get("otb") if isinstance(a.get("balance"), dict) else a.get("balance"),
                    "type": a.get("accountType"), "chuzhoy": any(c in name.lower() for c in CHUZHIE)})
    return out


def statement(account: str, since: str, till: str | None = None, max_pages: int = 20) -> list[dict]:
    """Операции по счёту за период (даты YYYY-MM-DD), все страницы по cursor."""
    till = till or dt.date.today().isoformat()
    params = {"accountNumber": account, "from": f"{since}T00:00:00+03:00", "till": f"{till}T23:59:59+03:00", "limit": 1000}
    ops, cursor = [], None
    for _ in range(max_pages):
        if cursor:
            params["cursor"] = cursor
        data = get("/api/v1/statement", params)
        page = data.get("operations") if isinstance(data, dict) else data
        ops.extend(page or [])
        cursor = data.get("nextCursor") if isinstance(data, dict) else None
        if not cursor or not page:
            break
    return ops


def probe() -> dict:
    """Проверка связи: какие счета видны и сколько операций за вчера-сегодня."""
    out: dict = {"ok": False}
    try:
        acc = accounts()
        out["accounts"] = acc
        y = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        for a in acc:
            if a["chuzhoy"] or not a["number"]:
                continue
            try:
                ops = statement(a["number"], y)
                a["ops_2d"] = len(ops)
                if ops and "sample_keys" not in out:
                    out["sample_keys"] = sorted(ops[0].keys())
            except Exception as e:                                   # noqa: BLE001
                a["ops_error"] = str(e)[:200]
        out["ok"] = True
    except Exception as e:                                           # noqa: BLE001
        out["error"] = str(e)[:400]
    return out
