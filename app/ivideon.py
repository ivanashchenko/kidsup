# -*- coding: utf-8 -*-
"""Ivideon — камеры центра, только просмотр.

04.10.2026 Борис дал пользователю kidsup.claude@yandex.ru доступ ко всем
камерам (настройки ivideon_login / ivideon_password на /owner). Вход — через
my.ivideon.com в браузере на сервере, сессия хранится в data/ivideon_state.json.
Ничего не меняем: ни настроек камер, ни доступов, ни записи.
"""
from __future__ import annotations

from pathlib import Path

from . import db

HOME = "https://my.ivideon.com/"


def _state_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "ivideon_state.json"


def web(url: str = HOME, wait_ms: int = 6000, max_text: int = 30000, actions: list | None = None,
        shot: bool = False) -> dict:
    """Открыть страницу кабинета Ivideon; actions: [{js}|{wait}|{goto}]."""
    from playwright.sync_api import sync_playwright
    from . import mkweb
    if not url.startswith("https://my.ivideon.com/"):
        return {"ok": False, "error": "только my.ivideon.com"}
    login, pw = db.get_setting("ivideon_login") or "", db.get_setting("ivideon_password") or ""
    if not login or not pw:
        return {"ok": False, "error": "не заданы ivideon_login / ivideon_password на /owner"}
    st = _state_path()
    with sync_playwright() as p:
        b = mkweb._launch(p)
        kw = {"viewport": {"width": 1400, "height": 1000}, "locale": "ru-RU"}
        if st.exists():
            kw["storage_state"] = str(st)
        ctx = b.new_context(**kw)
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=90000)
        pg.wait_for_timeout(3000)
        voshli = False
        if "/service/login" in pg.url:
            pg.fill("input[name='LoginForm[username]']", login)
            pg.fill("input[name='LoginForm[password]']", pw)
            pg.click("button[type=submit]", timeout=15000)
            pg.wait_for_timeout(8000)
            voshli = True
            if "/service/login" in pg.url:
                txt = pg.inner_text("body")[:600]
                b.close()
                return {"ok": False, "error": "вход не удался", "text": txt}
            ctx.storage_state(path=str(st))
            if url != HOME and not pg.url.startswith(url):
                pg.goto(url, wait_until="domcontentloaded", timeout=90000)
        pg.wait_for_timeout(wait_ms)
        done = []
        for a in actions or []:
            try:
                if "js" in a:
                    a = {**a, "result": pg.evaluate(a["js"])}
                elif "wait" in a:
                    pg.wait_for_timeout(int(a["wait"]))
                elif "click_css" in a:
                    pg.locator(a["click_css"]).nth(int(a.get("nth", 0))).click(timeout=10000)
                    pg.wait_for_timeout(int(a.get("after", 3000)))
                elif "click" in a:
                    pg.get_by_text(a["click"], exact=bool(a.get("exact"))).nth(int(a.get("nth", 0))).click(timeout=10000)
                    pg.wait_for_timeout(int(a.get("after", 3000)))
                elif a.get("goto", "").startswith("https://my.ivideon.com/"):
                    pg.goto(a["goto"], wait_until="domcontentloaded", timeout=90000)
                    pg.wait_for_timeout(int(a.get("after", 5000)))
                done.append({**a, "ok": True})
            except Exception as e:                                   # noqa: BLE001
                done.append({**a, "ok": False, "error": str(e).splitlines()[0][:160]})
        out = {"ok": True, "url": pg.url, "title": pg.title(), "voshli": voshli,
               "text": pg.inner_text("body")[:max_text], "actions": done}
        if shot:
            import base64
            out["png_b64"] = base64.b64encode(pg.screenshot(type="png")).decode()
        ctx.storage_state(path=str(st))
        b.close()
        return out


# Вход с кодом из письма: Ivideon при входе с нового браузера шлёт код на почту.
# login_start() в фоне вводит логин/пароль и до 15 минут ждёт код
# (ivideon_code вручную или из ящика по IMAP — app/pochta_kod.py).

import threading as _th
import time as _time

_login = {"state": "idle"}


def _login_worker() -> None:
    from playwright.sync_api import sync_playwright
    from . import mkweb, pochta_kod
    old = pochta_kod.snapshot("ivideon")
    t0 = _time.time()
    db.set_setting("ivideon_code", "")
    try:
        with sync_playwright() as p:
            b = mkweb._launch(p)
            ctx = b.new_context(viewport={"width": 1400, "height": 1000}, locale="ru-RU")
            pg = ctx.new_page()
            pg.goto(HOME, wait_until="domcontentloaded", timeout=90000)
            pg.wait_for_timeout(3000)
            if "/service/login" in pg.url:
                pg.fill("input[name='LoginForm[username]']", db.get_setting("ivideon_login"))
                pg.fill("input[name='LoginForm[password]']", db.get_setting("ivideon_password"))
                pg.click("button[type=submit]", timeout=15000)
                pg.wait_for_timeout(8000)
            if "/service/login" in pg.url or "Подтвердите вход" in pg.inner_text("body"):
                _login.update(state="wait_code", since=t0)
                code = pochta_kod.wait("ivideon_code", t0, "ivideon", _login, old=old)
                if not code:
                    _login.update(state="timeout")
                    b.close()
                    return
                inp = pg.locator("input:visible:not([type=hidden]):not([type=checkbox])").last
                inp.fill(code)
                inp.press("Enter")
                pg.wait_for_timeout(8000)
                if "/service/login" in pg.url or "Подтвердите вход" in pg.inner_text("body"):
                    _login.update(state="bad_code", text=pg.inner_text("body")[:300])
                    b.close()
                    return
            ctx.storage_state(path=str(_state_path()))
            _login.update(state="ok", url=pg.url, at=_time.strftime("%Y-%m-%d %H:%M"))
            b.close()
    except Exception as e:                                           # noqa: BLE001
        _login.update(state="error", error=f"{type(e).__name__}: {str(e)[:300]}")
    finally:
        db.set_setting("ivideon_code", "")


def login_start(code: str = "") -> dict:
    if code:
        db.set_setting("ivideon_code", code.strip())
        return {"ok": True, "code_saved": True, "login": dict(_login)}
    if _login.get("state") == "wait_code" and _time.time() - _login.get("since", 0) < 900:
        return {"ok": True, "already": True, "login": dict(_login)}
    _login.clear()
    _login["state"] = "starting"
    _th.Thread(target=_login_worker, daemon=True).start()
    return {"ok": True, "started": True}


def login_status() -> dict:
    return {**_login, "has_state": _state_path().exists()}
