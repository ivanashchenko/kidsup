# -*- coding: utf-8 -*-
"""Коды входа из почты kidsup.claude@yandex.ru.

КОМТЕТ и Ivideon при входе с нового браузера шлют код на почту. Код берём
либо из настройки <имя>_code (владелец вносит вручную через
/api/<имя>/login {code}), либо — если задан пароль приложения Яндекса
(komtet_imap_password, общий для ящика) — читаем из ящика по IMAP сами.
"""
from __future__ import annotations

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


def wait(key: str, since: float, sender: str, status: dict, timeout: int = 900) -> str:
    """Ждать код: настройка <key> (вручную) или письмо от sender (IMAP)."""
    code = ""
    while not code and time.time() - since < timeout:
        time.sleep(10)
        code = (db.get_setting(key) or "").strip()
        if not code:
            try:
                code = from_mail(since, sender)
            except Exception as e:                                   # noqa: BLE001
                status["imap_error"] = str(e)[:200]
    db.set_setting(key, "")
    return code
