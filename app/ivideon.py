# -*- coding: utf-8 -*-
"""Ivideon — камеры центра, только просмотр.

04.10.2026 Борис дал пользователю kidsup.claude@yandex.ru доступ ко всем
камерам (настройки ivideon_login / ivideon_password на /owner). Вход — через
my.ivideon.com в браузере на сервере, сессия хранится в data/ivideon_state.json.
Ничего не меняем: ни настроек камер, ни доступов, ни записи в облаке Ivideon.
14.11.2026: potok_ssylka() — ссылка на живой поток камеры для записи занятий на нашем
сервере (app/zapis.py); в самом Ivideon от этого ничего не меняется.
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
    _login["zapusk"] = _time.time()          # когда вход запускали последний раз (пауза в potok_ssylka)
    _th.Thread(target=_login_worker, daemon=True).start()
    return {"ok": True, "started": True}


def login_status() -> dict:
    return {**_login, "has_state": _state_path().exists()}


# 14.11.2026, Борис — запись занятий «Музыка и речь» (app/zapis.py). Плеер my.ivideon.com на
# камере делает запрос openapi-…ivideon.com/cameras/<id>:0/live_stream?…&access_token=…&
# format=ws-fmp4; тот же адрес с format=flv отдаёт живой FLV (H.264 + звук), его ffmpeg пишет
# без перекодирования. Токена нет ни в localStorage, ни в доступных cookie — ловим адрес из
# сетевого запроса страницы. Срок жизни токена неизвестен: ссылку берут свежую перед каждым
# занятием и после обрыва. Адрес с токеном не печатаем и не пишем в журнал.

import re as _re
from urllib.parse import urlsplit as _urlsplit


def _ssylka_flv(url: str) -> str:
    """Адрес из запроса плеера → тот же адрес с format=flv; чужой адрес → ""."""
    u = _urlsplit(url or "")
    if u.scheme != "https" or not (u.hostname or "").endswith(".ivideon.com") or "/live_stream" not in u.path \
            or "access_token=" not in (u.query or ""):
        return ""
    if _re.search(r"(^|&)format=", u.query):
        return _re.sub(r"(?<=[?&])format=[^&]*", "format=flv", url, count=1)
    return url + "&format=flv"


# Вход заново из potok_ssylka — не чаще раза в 15 минут: при слетевшей сессии запись пробует ссылку
# каждые 30–60 с, и без паузы Ivideon получил бы десятки входов с паролем (и писем с кодом) за занятие —
# так учётку могут закрыть, а с ней сломается и фото-явка (проверка V1, №8).
VHOD_PAUZA_S = 15 * 60


def _id_v_adrese(url: str, cam_id: str) -> bool:
    """Адрес запроса плеера — поток именно этой камеры: /cameras/<id>:… (не «<id>X», не другой)."""
    return bool(cam_id) and _re.search(r"/cameras/" + _re.escape(cam_id) + r"(?::|/|\?|$)", url or "") is not None


def potok_ssylka(kamera: str = "Комната 1", cam_id: str = "", zhdat_s: int = 25) -> dict:
    """Свежая ссылка на живой поток камеры: {"ok": True, "url": …} или {"ok": False, "error": …}.

    Браузер — на ~30 секунд и под общей блокировкой с кадрами фото-явки (kadry._snap_lock, вне
    очереди кадров — kadry.brauzer_dlya_zapisi): два Chromium разом сервер с 2 ГБ памяти не тянет,
    а 07.10 одновременный запуск уже давал пустые кадры.

    15.11.2026 (проверка V1, №6 и №8): камера — строго по id: клик только по точному названию
    («Комната 1», не «Комната 10»), и годится только запрос /cameras/<id>:… этой камеры; без id —
    отказ без браузера. Сессия слетела (страница входа или «Подтвердите вход») — форму здесь не
    отправляем: login_start() (он же достаёт код из письма), и не чаще раза в VHOD_PAUZA_S."""
    from playwright.sync_api import sync_playwright
    from . import kadry, mkweb
    login, pw = db.get_setting("ivideon_login") or "", db.get_setting("ivideon_password") or ""
    if not login or not pw:
        return {"ok": False, "error": "не заданы ivideon_login / ivideon_password на /owner"}
    if not cam_id:
        return {"ok": False, "error": "не задан id камеры (настройка «Камера» на /owner) — без него поток не беру"}
    if _login.get("state") in ("starting", "wait_code") and _time.time() - _login.get("since", _time.time()) < 900:
        return {"ok": False, "error": "идёт вход в Ivideon с кодом из письма — ссылка будет после входа"}
    st = _state_path()
    nashli: list[str] = []
    nuzhen_vhod = False
    oshibka = ""

    def lovit(req):
        u = req.url
        if "/live_stream" in u and "access_token=" in u and _id_v_adrese(u, cam_id):
            nashli.append(u)

    with kadry.brauzer_dlya_zapisi(), sync_playwright() as p:
        b = mkweb._launch(p)
        try:
            kw = {"viewport": {"width": 1400, "height": 1000}, "locale": "ru-RU"}
            if st.exists():
                kw["storage_state"] = str(st)
            ctx = b.new_context(**kw)
            pg = ctx.new_page()
            pg.on("request", lovit)
            pg.goto(HOME, wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(3000)
            if "/service/login" in pg.url or "Подтвердите вход" in pg.inner_text("body"):
                nuzhen_vhod = True
            else:
                try:
                    tochno = pg.get_by_text(kamera, exact=True)
                    if not tochno.count():
                        raise LookupError("нет точного совпадения")
                    tochno.nth(0).click(timeout=15000)
                except Exception as e:  # noqa: BLE001
                    oshibka = (f"камера «{kamera}» не нашлась в списке Ivideon по точному названию "
                               f"({type(e).__name__}) — проверьте настройку «Камера» на /owner")
                if not oshibka:
                    for _ in range(int(zhdat_s * 2)):
                        if nashli:
                            break
                        pg.wait_for_timeout(500)
                    if nashli:
                        ctx.storage_state(path=str(st))
        finally:
            b.close()
    if nuzhen_vhod:
        posl = float(_login.get("zapusk") or 0)
        if _time.time() - posl < VHOD_PAUZA_S:
            cherez = max(1, int((posl + VHOD_PAUZA_S - _time.time()) // 60) + 1)
            return {"ok": False, "error": "сессия Ivideon слетела, вход уже запускали недавно — повторю через "
                                          f"~{cherez} мин (не чаще раза в 15 минут, чтобы Ivideon не закрыл вход)"}
        login_start()
        return {"ok": False, "error": "Ivideon просит войти заново — вход (с кодом из письма) запущен, ссылка будет на "
                                      "следующей попытке"}
    if oshibka:
        return {"ok": False, "error": oshibka}
    url = _ssylka_flv(nashli[-1]) if nashli else ""
    if not url or not _id_v_adrese(url, cam_id):
        return {"ok": False, "error": "плеер Ivideon не запросил поток этой камеры за %d с" % zhdat_s}
    return {"ok": True, "url": url}
