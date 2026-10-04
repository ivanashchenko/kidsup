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


# ---------------------------------------------------------------- кабинет (браузер)
# 04.10.2026 Борис завёл пользователя кабинета для чтения (komtet_login /
# komtet_password на /owner). Вход — через id.komtet.ru в браузере на сервере
# (тот же Playwright, что для МойКласса); сессия хранится в data/komtet_state.json,
# чтобы не логиниться на каждый запрос. Только чтение: никаких кликов по кнопкам
# «пробить», «вернуть», «закрыть смену».

def _state_path():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent / "data" / "komtet_state.json"


def web(url: str = "https://kassa.komtet.ru/manage", wait_ms: int = 5000, max_text: int = 30000,
        actions: list | None = None) -> dict:
    """Открыть страницу кабинета КОМТЕТ под пользователем «только просмотр»."""
    from playwright.sync_api import sync_playwright
    from . import mkweb
    if not url.startswith("https://kassa.komtet.ru/"):
        return {"ok": False, "error": "только kassa.komtet.ru"}
    login, pw = db.get_setting("komtet_login") or "", db.get_setting("komtet_password") or ""
    if not login or not pw:
        return {"ok": False, "error": "не заданы komtet_login / komtet_password на /owner"}
    st = _state_path()
    with mkweb._lock, sync_playwright() as p:
        b = mkweb._launch(p)
        kw = {"viewport": {"width": 1400, "height": 1000}, "locale": "ru-RU"}
        if st.exists():
            kw["storage_state"] = str(st)
        ctx = b.new_context(**kw)
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=90000)
        pg.wait_for_timeout(2500)
        voshli = False
        if "id.komtet.ru" in pg.url:
            pg.fill("input[type=email]", login)
            pg.fill("input[type=password]", pw)
            pg.get_by_text("Войти", exact=True).last.click(timeout=15000)
            pg.wait_for_timeout(6000)
            voshli = True
            if "id.komtet.ru" in pg.url:
                txt = pg.inner_text("body")[:500]
                b.close()
                return {"ok": False, "error": "вход не удался", "text": txt}
            ctx.storage_state(path=str(st))
            if not pg.url.startswith(url):
                pg.goto(url, wait_until="domcontentloaded", timeout=90000)
        pg.wait_for_timeout(wait_ms)
        done = []
        for a in actions or []:
            try:
                if "js" in a:
                    a = {**a, "result": pg.evaluate(a["js"])}
                elif "wait" in a:
                    pg.wait_for_timeout(int(a["wait"]))
                elif a.get("goto", "").startswith("https://kassa.komtet.ru/"):
                    pg.goto(a["goto"], wait_until="domcontentloaded", timeout=90000)
                    pg.wait_for_timeout(int(a.get("after", 4000)))
                done.append({**a, "ok": True})
            except Exception as e:                                   # noqa: BLE001
                done.append({**a, "ok": False, "error": str(e).splitlines()[0][:160]})
        out = {"ok": True, "url": pg.url, "title": pg.title(), "voshli": voshli,
               "text": pg.inner_text("body")[:max_text], "actions": done}
        ctx.storage_state(path=str(st))
        b.close()
        return out


# Вход в кабинет — с кодом из письма (id.komtet.ru шлёт код на почту
# komtet_login при входе с нового браузера). login_start() открывает браузер в
# фоне, вводит логин/пароль и до 15 минут ждёт код: либо владелец вносит его
# (настройка komtet_code через /api/komtet/login {code}), либо — если задан
# пароль приложения Яндекс-почты komtet_imap_password — код читается из ящика
# сам (app/pochta_kod.py). После входа сессия сохраняется в data/komtet_state.json и дальше web()
# ходит без кода, пока КОМТЕТ её не сбросит.

import threading as _th
import time as _time

_login = {"state": "idle"}


def _login_worker() -> None:
    from playwright.sync_api import sync_playwright
    from . import mkweb, pochta_kod
    old = pochta_kod.snapshot("komtet")
    t0 = _time.time()
    db.set_setting("komtet_code", "")
    try:
        with sync_playwright() as p:
            b = mkweb._launch(p)
            ctx = b.new_context(viewport={"width": 1400, "height": 1000}, locale="ru-RU")
            pg = ctx.new_page()
            pg.goto("https://kassa.komtet.ru/manage", wait_until="domcontentloaded", timeout=90000)
            pg.wait_for_timeout(2500)
            if "id.komtet.ru" in pg.url:
                pg.fill("input[type=email]", db.get_setting("komtet_login"))
                pg.fill("input[type=password]", db.get_setting("komtet_password"))
                pg.get_by_text("Войти", exact=True).last.click(timeout=15000)
                pg.wait_for_timeout(6000)
            if "id.komtet.ru" in pg.url:
                _login.update(state="wait_code", since=t0, text=pg.inner_text("body")[:300])
                code = pochta_kod.wait("komtet_code", t0, "komtet", _login, old=old)
                if not code:
                    _login.update(state="timeout")
                    b.close()
                    return
                # на странице два поля: Email (заполнено) и «Код из email» — берём последнее видимое
                pg.locator("input:visible:not([type=hidden]):not([type=checkbox])").last.fill(code)
                pg.get_by_text("Подтвердить вход", exact=False).last.click(timeout=15000)
                pg.wait_for_timeout(8000)
                if "id.komtet.ru" in pg.url:
                    _login.update(state="bad_code", text=pg.inner_text("body")[:300])
                    b.close()
                    return
            ctx.storage_state(path=str(_state_path()))
            _login.update(state="ok", url=pg.url, at=_time.strftime("%Y-%m-%d %H:%M"))
            b.close()
    except Exception as e:                                           # noqa: BLE001
        _login.update(state="error", error=f"{type(e).__name__}: {str(e)[:300]}")
    finally:
        db.set_setting("komtet_code", "")


def login_start(code: str = "") -> dict:
    if code:
        db.set_setting("komtet_code", code.strip())
        return {"ok": True, "code_saved": True, "login": dict(_login)}
    if _login.get("state") == "wait_code" and _time.time() - _login.get("since", 0) < 900:
        return {"ok": True, "already": True, "login": dict(_login)}
    _login.clear()
    _login["state"] = "starting"
    _th.Thread(target=_login_worker, daemon=True).start()
    return {"ok": True, "started": True}


def login_status() -> dict:
    return {**_login, "has_state": _state_path().exists()}
