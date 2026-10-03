# -*- coding: utf-8 -*-
"""КОМТЕТ Касса — API магазина (только чтение).

03.10.2026 Борис внёс ID магазина и секрет (настройки komtet_shop_id,
komtet_secret на /owner). Подпись запроса — как в официальном SDK
komtet-kassa-sdk: заголовок Authorization = shop_id, X-HMAC-Signature =
HMAC-MD5(secret, METHOD + полный URL [+ тело]).

API магазина умеет только ставить чеки в очередь и смотреть их по id задачи:
списка пробитых чеков в нём нет. Поэтому здесь только проверка связи и чтение
очередей/задач; сверку «каждая оплата — с чеком» делаем по выгрузке из
личного кабинета, когда будет пользователь «только просмотр»
(komtet_login / komtet_password). Создавать чеки отсюда нельзя.
"""
from __future__ import annotations

import hashlib
import hmac

import httpx

from . import db

HOST = "https://kassa.komtet.ru"


def _keys() -> tuple[str, str]:
    shop, secret = db.get_setting("komtet_shop_id") or "", db.get_setting("komtet_secret") or ""
    if not shop or not secret:
        raise RuntimeError("не заданы komtet_shop_id / komtet_secret — владелец вносит их на /owner")
    return shop, secret


def get(path: str) -> dict:
    """GET с подписью. Только чтение: метод фиксирован, путь — /api/shop/…"""
    if not path.startswith("/api/shop/") or ".." in path:
        raise ValueError("путь должен начинаться с /api/shop/")
    shop, secret = _keys()
    url = HOST + path
    sig = hmac.new(secret.encode(), ("GET" + url).encode("utf-8"), hashlib.md5).hexdigest()
    r = httpx.get(url, timeout=30, follow_redirects=True,
                  headers={"Authorization": shop, "Accept": "application/json", "X-HMAC-Signature": sig})
    if r.status_code >= 400:
        raise RuntimeError(f"Комтет {path}: HTTP {r.status_code} {r.text[:300]}")
    try:
        return r.json()
    except ValueError:
        return {"text": r.text[:500]}


def probe() -> dict:
    """Проверка ключей: список сотрудников-кассиров магазина (безвредное чтение)
    и, если указан komtet_queue_id, состояние очереди."""
    out: dict = {"ok": False}
    try:
        emp = get("/api/shop/v1/employees?start=0&limit=10")
        out["employees"] = emp
        out["ok"] = True
    except Exception as e:                                           # noqa: BLE001
        out["error"] = str(e)[:400]
    q = db.get_setting("komtet_queue_id") or ""
    if q:
        try:
            out["queue"] = get(f"/api/shop/v1/queues/{q}")
        except Exception as e:                                       # noqa: BLE001
            out["queue_error"] = str(e)[:300]
    return out
