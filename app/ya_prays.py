"""Прайс на Яндекс Картах = прайс приложения (PRICES).

28.09 Борис: «подключай карточку к нашему прайсу». Ссылку на фид Яндекс
Бизнес не принимает — только загрузку файла, и новая загрузка целиком
заменяет старый прайс. Поэтому: собираем тот же YML, что отдаёт /price.yml,
кладём файлом и загружаем браузером под сессией Яндекса. Раз в день
автопилот сверяет отпечаток фида и перезагружает, только если цены или
состав поменялись.
"""
from __future__ import annotations

import hashlib
import logging
import re

from . import db
from .mkweb import DATA

log = logging.getLogger("kidsup.ya_prays")
ORG = "220508405205"
URL = f"https://yandex.ru/sprav/{ORG}/p/edit/price-lists/"
FILE = DATA / "price_yandex.yml"


def _yml() -> bytes:
    from .main import price_yml
    return price_yml().body


def _otpechatok(body: bytes) -> str:
    # дата выгрузки в шапке меняется каждую минуту — в отпечаток не берём
    return hashlib.sha1(re.sub(rb'date="[^"]*"', b"", body)).hexdigest()[:16]


def zagruzit(force: bool = False, dry: bool = False) -> dict:
    from . import mkweb
    body = _yml()
    otp = _otpechatok(body)
    if not force and otp == db.get_setting("ya_price_hash", ""):
        return {"ok": True, "пропущено": "прайс не менялся", "отпечаток": otp}
    FILE.write_bytes(body)
    pozicij = body.count(b"<offer ")
    if dry:
        return {"ok": True, "dry_run": True, "позиций": pozicij, "файл": str(FILE)}
    shagi_ = [{"click": "Upload XLS/YML", "exact": True, "after": 3000},
              {"click": "YML", "exact": True, "after": 2000},
              # файл уходит в Яндекс сразу при выборе — отдельной кнопки не нужно
              {"upload": str(FILE), "after": 15000}]
    res = mkweb.ya_open(URL, 9000, 4000, shagi_)
    shagi = res.get("actions") or []
    ok = bool(res.get("ok")) and all(a.get("ok") for a in shagi[:3])
    if ok:
        db.set_setting("ya_price_hash", otp)
    log.info("прайс Карт: ok=%s, позиций %s", ok, pozicij)
    return {"ok": ok, "позиций": pozicij, "шаги": shagi, "текст": (res.get("text") or "")[:1500]}
