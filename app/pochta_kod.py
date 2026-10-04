# -*- coding: utf-8 -*-
"""Коды входа из почты kidsup.claude@yandex.ru.

КОМТЕТ и Ivideon при входе с нового браузера шлют код на почту. Код берём
(по порядку): из настройки <имя>_code (владелец вносит вручную через
/api/<имя>/login {code}); из веб-почты — браузер на сервере под
pochta_password открывает письмо и достаёт код (основной путь с 04.10.2026);
по IMAP, если задан пароль приложения komtet_imap_password.
"""
from __future__ import annotations

import json
import re
import time

from . import db


def from_mail(since: float, sender: str) -> str:
    pw = db.get_setting("komtet_imap_password") or ""
    if not pw:
        return ""
    import email
    import imaplib
    from email.header import decode_header, make_header
    from email.utils import parsedate_to_datetime
    m = imaplib.IMAP4_SSL("imap.yandex.ru", 993, timeout=30)
    try:
        m.login(db.get_setting("komtet_login") or db.get_setting("ivideon_login"), pw)
        m.select("INBOX")
        _, ids = m.search(None, "ALL")
        for i in reversed(ids[0].split()[-15:]):
            _, d = m.fetch(i, "(RFC822)")
            msg = email.message_from_bytes(d[0][1])
            frm = str(make_header(decode_header(msg.get("From", "")))).lower()
            if sender not in frm:
                continue
            try:
                if parsedate_to_datetime(msg["Date"]).timestamp() < since - 60:
                    break
            except Exception:                                        # noqa: BLE001
                pass
            body = ""
            for part in msg.walk():
                if part.get_content_type() in ("text/plain", "text/html"):
                    body += (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", "ignore")
            body = re.sub(r"<[^>]+>|&#?\w+;", " ", body)
            c = re.search(r"(?<![\d#])(\d{4,8})(?!\d)", body)
            if c:
                return c.group(1)
    finally:
        try:
            m.logout()
        except Exception:                                            # noqa: BLE001
            pass
    return ""


# тема письма с кодом и где в тексте сам код
SOURCES = {
    "komtet": ("Код подтверждения email", r"формы\.?\s*([0-9A-Za-z]{4,10})\b"),
    "ivideon": ("Код для входа", r"используйте код:?\s*(\d{4,8})\b"),
}


def web_codes(sender: str) -> list[str]:
    """Все коды из последнего письма-кода от sender в веб-почте (новые — в конце).
    Браузер запускаем в отдельном потоке: вызывающий поток может сам держать
    Playwright (вход в КОМТЕТ/Ivideon), а два sync_playwright в одном потоке нельзя."""
    import concurrent.futures as cf
    subj, rx = SOURCES[sender]
    if not db.get_setting("pochta_password"):
        return []
    # письма с одной темой Яндекс склеивает в цепочку: строка списка ведёт на
    # #/thread/…, внутри цепочки первым идёт самое новое письмо (#/message/…)
    find = ("(() => { for (const a of document.querySelectorAll('a[href*=\"#/message/\"],a[href*=\"#/thread/\"]')) {"
            " let e = a; for (let i = 0; i < 6 && e; i++, e = e.parentElement) {"
            " if ((e.innerText || '').includes(%s)) return a.getAttribute('href'); } } return ''; })()" % json.dumps(subj))
    msg = ("(() => { const a = document.querySelector('a[href*=\"#/message/\"]');"
           " return location.hash.startsWith('#/thread/') && a ? a.getAttribute('href') : ''; })()")
    with cf.ThreadPoolExecutor(1) as ex:
        r = ex.submit(ya_web, "https://mail.yandex.ru/", 6000, 20000, False, "",
                      [{"goto_js": find}, {"goto_js": msg}]).result(timeout=300)
    return re.findall(rx, r.get("text") or "")


def snapshot(sender: str) -> set:
    """Коды, которые уже лежат в ящике до входа — их не вводим."""
    try:
        return set(web_codes(sender))
    except Exception:                                                # noqa: BLE001
        return set()


def wait(key: str, since: float, sender: str, status: dict, timeout: int = 900, old: set | None = None) -> str:
    """Ждать код: настройка <key> (вручную), веб-почта (новый код, которого нет в old) или IMAP."""
    code = ""
    old = old or set()
    while not code and time.time() - since < timeout:
        time.sleep(15)
        code = (db.get_setting(key) or "").strip()
        if not code:
            try:
                new = [c for c in web_codes(sender) if c not in old]
                code = new[-1] if new else ""
                status["web_checked"] = time.strftime("%H:%M:%S")
            except Exception as e:                                   # noqa: BLE001
                status["web_error"] = str(e)[:200]
        if not code:
            try:
                code = from_mail(since, sender)
            except Exception as e:                                   # noqa: BLE001
                status["imap_error"] = str(e)[:200]
    db.set_setting(key, "")
    return code


# ---------------------------------------------------------------- веб-почта
# 04.10.2026 Борис: «давай ты сам зайдешь в почту». Вход в mail.yandex.ru
# через браузер на сервере под ivideon_login / pochta_password, сессия —
# data/yandex_state.json. Только читаем письма с кодами; ничего не отправляем
# и не удаляем.

def _ya_state():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent / "data" / "yandex_state.json"


def ya_web(url: str = "https://mail.yandex.ru/", wait_ms: int = 5000, max_text: int = 8000,
           shot: bool = False, code: str = "", actions: list | None = None) -> dict:
    """Открыть почту; если просит вход — войти. code — ответ на проверку Яндекса, если спросит."""
    from playwright.sync_api import sync_playwright
    from . import mkweb
    login = db.get_setting("ivideon_login") or db.get_setting("komtet_login")
    pw = db.get_setting("pochta_password") or ""
    if not login or not pw:
        return {"ok": False, "error": "не задан pochta_password"}
    if not url.startswith(("https://mail.yandex.ru/", "https://passport.yandex.ru/", "https://id.yandex.ru/")):
        return {"ok": False, "error": "только почта/паспорт Яндекса"}
    st = _ya_state()
    steps = []
    with sync_playwright() as p:
        b = mkweb._launch(p)
        kw = {"viewport": {"width": 1400, "height": 1000}, "locale": "ru-RU",
              "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}
        if st.exists():
            kw["storage_state"] = str(st)
        ctx = b.new_context(**kw)
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=90000)
        pg.wait_for_timeout(4000)
        steps.append(pg.url)
        if "passport.yandex" in pg.url or "/auth" in pg.url:
            try:
                # новый паспорт по умолчанию просит телефон: «Ещё» → «Войти по логину»
                lf = "input[placeholder*='Логин'], #passp-field-login"
                for t in ("Ещё", "Войти по логину", "Почта", "Логин"):
                    if pg.locator(lf).count():
                        break
                    el = pg.get_by_text(t, exact=True)
                    if el.count() and el.first.is_visible():
                        el.first.click(timeout=5000)
                        pg.wait_for_timeout(2000)
                        steps.append("click:" + t)
                if pg.locator(lf).count():
                    pg.locator(lf).first.fill(login)
                    pg.get_by_role("button", name="Войти", exact=True).first.click(timeout=10000)
                    pg.wait_for_timeout(5000)
                    steps.append("login:" + pg.url)
                pf = pg.locator("input[type=password]")
                if pf.count() and pf.first.is_visible():
                    pf.first.fill(pw)
                    pg.keyboard.press("Enter")
                    pg.wait_for_timeout(7000)
                    steps.append("passwd:" + pg.url)
                for t in ("Напомнить позже", "Не сейчас", "Пропустить"):
                    el = pg.get_by_text(t, exact=True)
                    if el.count() and el.first.is_visible():
                        el.first.click(timeout=5000)
                        pg.wait_for_timeout(3000)
                        steps.append("skip:" + t)
                        break
                steps.append("page:" + pg.inner_text("body")[:300])
                if code:
                    pg.locator("input:visible:not([type=hidden]):not([type=checkbox])").last.fill(code)
                    pg.locator("button[type=submit]").first.click(timeout=10000)
                    pg.wait_for_timeout(6000)
                    steps.append("code:" + pg.url)
            except Exception as e:                                   # noqa: BLE001
                steps.append("err:" + str(e).splitlines()[0][:160])
            if "mail.yandex" not in pg.url and url.startswith("https://mail.yandex.ru/"):
                pg.goto(url, wait_until="domcontentloaded", timeout=90000)
                pg.wait_for_timeout(5000)
        pg.wait_for_timeout(wait_ms)
        for a in actions or []:
            try:
                if "js" in a:
                    steps.append({"js": str(pg.evaluate(a["js"]))[:3000]})
                elif "goto_js" in a:
                    # JS возвращает адрес (#/thread/… или #/message/…) — переходим, если он есть
                    href = pg.evaluate(a["goto_js"])
                    steps.append("goto:" + str(href))
                    if href:
                        pg.goto("https://mail.yandex.ru/" + str(href).lstrip("/"), wait_until="domcontentloaded", timeout=90000)
                        pg.wait_for_timeout(int(a.get("after", 5000)))
                elif "click" in a:
                    pg.get_by_text(a["click"], exact=True).first.click(timeout=8000)
                    pg.wait_for_timeout(int(a.get("after", 2500)))
                    steps.append("click:" + a["click"] + " -> " + pg.url)
            except Exception as e:                                   # noqa: BLE001
                steps.append("err:" + str(e).splitlines()[0][:160])
        out = {"ok": True, "url": pg.url, "steps": steps, "text": pg.inner_text("body")[:max_text]}
        if shot:
            import base64
            out["png_b64"] = base64.b64encode(pg.screenshot(type="png")).decode()
        ctx.storage_state(path=str(st))
        b.close()
        return out
