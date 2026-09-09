"""Браузерный вход в МойКласс на сервере (09.09.2026).

Зачем. API МойКласса не отдаёт чаты с родителями и не позволяет вносить
остатки на складе. Борис завёл технический аккаунт сотрудника и попросил
«подключиться через браузер на нашем сервере». Из контейнера Клода браузер
наружу не выходит (прокси режет Chromium), поэтому Chromium живёт здесь,
рядом с приложением, и управляется через эндпоинты /api/mkweb/*.

Что здесь есть:
  status()  — стоит ли playwright и браузер, чей это процесс, хвост лога установки;
  setup()   — ставит playwright и Chromium (фоновый поток, лог в data/mkweb_setup.log);
  login()   — входит под mk_web_login / mk_web_password из настроек, сохраняет
              cookies в data/mk_web_state.json и скриншот в data/mkweb_last.png;
  open_page(url) — открывает страницу под сохранённой сессией, возвращает текст
              и скриншот — этим читаем чаты.

Логин и пароль — только из настроек сервера (SECRET_KEYS), в ответы не попадают.
Никаких действий в CRM отсюда не делается — только чтение экрана.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import db

DATA = Path(__file__).resolve().parent.parent / "data"
DATA.mkdir(exist_ok=True)
SETUP_LOG = DATA / "mkweb_setup.log"
STATE = DATA / "mk_web_state.json"
SHOT = DATA / "mkweb_last.png"
LOGIN_URL = "https://app.moyklass.com/"

_lock = threading.Lock()
_setup_running = False


def _chromium_path() -> str | None:
    """Путь к Chromium, который поставил playwright (или системный).

    Раскладки у playwright разные: chromium-*/chrome-linux/chrome,
    chromium_headless_shell-*/chrome-linux/headless_shell и новая
    chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell.
    """
    bases = [Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])] if os.environ.get("PLAYWRIGHT_BROWSERS_PATH") else []
    bases += [Path.home() / ".cache" / "ms-playwright", Path("/root/.cache/ms-playwright"), Path("/opt/pw-browsers")]
    names = ("chrome", "headless_shell", "chrome-headless-shell")
    for base in bases:
        if not base.exists():
            continue
        for d in sorted(base.glob("chromium-*")) + sorted(base.glob("chromium_headless_shell-*")):
            for sub in d.iterdir():
                if sub.is_dir():
                    for n in names:
                        exe = sub / n
                        if exe.exists():
                            return str(exe)
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def status() -> dict:
    try:
        import playwright  # noqa: F401
        pw = getattr(playwright, "__version__", "ok")
    except Exception as e:  # noqa: BLE001
        pw = f"нет ({type(e).__name__})"
    tail = ""
    if SETUP_LOG.exists():
        tail = SETUP_LOG.read_text(encoding="utf-8", errors="replace")[-1500:]
    return {
        "python": sys.executable,
        "user": os.environ.get("USER") or os.environ.get("LOGNAME") or str(os.getuid()),
        "playwright": pw,
        "chromium": _chromium_path(),
        "setup_running": _setup_running,
        "setup_log_tail": tail,
        "session_saved": STATE.exists(),
        "session_mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(STATE.stat().st_mtime)) if STATE.exists() else None,
        "login_set": bool(db.get_setting("mk_web_login")),
        "password_set": bool(db.get_setting("mk_web_password")),
    }


def _run(cmd: list[str], log) -> int:
    log.write(f"\n$ {' '.join(cmd)}\n"); log.flush()
    p = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, text=True, timeout=1800)
    log.write(f"[exit {p.returncode}]\n"); log.flush()
    return p.returncode


def setup() -> dict:
    """Ставит playwright и Chromium. Долгая операция — уходит в фон."""
    global _setup_running
    if _setup_running:
        return {"ok": True, "already_running": True}

    def worker():
        global _setup_running
        _setup_running = True
        try:
            with SETUP_LOG.open("w", encoding="utf-8") as log:
                log.write(time.strftime("%Y-%m-%d %H:%M:%S") + " установка playwright + chromium\n")
                _run([sys.executable, "-m", "pip", "install", "-q", "playwright"], log)
                # --with-deps тянет системные библиотеки через apt (нужен root);
                # если не вышло — ставим сам браузер, библиотеки чаще всего уже есть.
                rc = _run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"], log)
                if rc != 0:
                    _run([sys.executable, "-m", "playwright", "install", "chromium"], log)
                log.write("готово: chromium=" + str(_chromium_path()) + "\n")
        finally:
            _setup_running = False

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "started": True}


def _launch(p):
    exe = _chromium_path()
    kw = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    if exe:
        kw["executable_path"] = exe
    return p.chromium.launch(**kw)


def login() -> dict:
    """Вход по логину/паролю из настроек, сохранение сессии и скриншота."""
    from playwright.sync_api import sync_playwright

    user = db.get_setting("mk_web_login")
    pwd = db.get_setting("mk_web_password")
    if not user or not pwd:
        return {"ok": False, "error": "mk_web_login / mk_web_password не заданы в настройках"}
    with _lock, sync_playwright() as p:
        b = _launch(p)
        ctx = b.new_context(viewport={"width": 1400, "height": 900}, locale="ru-RU")
        pg = ctx.new_page()
        pg.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(2500)
        inputs = []
        for i in pg.query_selector_all("input"):
            inputs.append({"type": i.get_attribute("type"), "name": i.get_attribute("name"),
                           "placeholder": i.get_attribute("placeholder")})
        try:
            email = pg.locator("input[type=email], input[name=email], input[name=login], input[name=username], input[type=text]").first
            email.fill(user)
            pw = pg.locator("input[type=password]").first
            pw.fill(pwd)
            pw.press("Enter")
        except Exception as e:  # noqa: BLE001
            pg.screenshot(path=str(SHOT))
            b.close()
            return {"ok": False, "error": f"поля входа не найдены: {e}", "inputs": inputs, "url": pg.url}
        # SPA после входа долго крутит лоадер: ждём до 25 с, пока не исчезнет поле пароля
        logged = False
        for _ in range(25):
            pg.wait_for_timeout(1000)
            if pg.locator("input[type=password]").count() == 0 and "login=yes" not in pg.url:
                logged = True
                break
        pg.wait_for_timeout(3000)
        url, title = pg.url, pg.title()
        body = pg.inner_text("body")[:2000]
        pg.screenshot(path=str(SHOT))
        if not logged:
            logged = pg.locator("input[type=password]").count() == 0
        if logged:
            ctx.storage_state(path=str(STATE))
        b.close()
        return {"ok": logged, "url": url, "title": title, "inputs": inputs, "text": body}


def open_page(url: str, wait_ms: int = 4000, max_text: int = 6000) -> dict:
    """Открыть страницу под сохранённой сессией; вернуть текст и сделать скриншот."""
    from playwright.sync_api import sync_playwright

    if not STATE.exists():
        return {"ok": False, "error": "сессии нет — сначала /api/mkweb/login"}
    with _lock, sync_playwright() as p:
        b = _launch(p)
        ctx = b.new_context(storage_state=str(STATE), viewport={"width": 1400, "height": 900}, locale="ru-RU")
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(wait_ms)
        text = pg.inner_text("body")[:max_text]
        pg.screenshot(path=str(SHOT), full_page=False)
        out = {"ok": True, "url": pg.url, "title": pg.title(), "text": text}
        # сессия могла обновиться (cookies) — сохраняем
        try:
            ctx.storage_state(path=str(STATE))
        except Exception:  # noqa: BLE001
            pass
        b.close()
        return out
