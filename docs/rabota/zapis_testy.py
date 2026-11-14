# -*- coding: utf-8 -*-
"""Запись занятий «Музыка и речь» (app/zapis.py) — проверки на стенде, без сервера и без сети.

14.11.2026, Борис: все занятия «Музыка и речь» пишутся на видео нашим сервером и уходят на
Яндекс.Диск центра — архив методики, только для внутреннего пользования.

Здесь зафиксировано: какие занятия пишутся (фильтр групп, отменённые и удалённые — нет, кабинет →
камера); когда (поток за 30 минут, ссылка за 2 минуты, запись с начала−1 до конца+1, позднее
начало после перезапуска, перенос занятия на другой день); обрыв → новая ссылка и вторая часть;
перезапуск сервера посреди записи; выгрузка (MKCOL папок, PUT,
проверка размера PROPFIND, удаление только после совпадения), повтор раз в час, 401/500/нет места
на Диске; лимиты диска сервера (3 ГБ свободно, 6 ГБ невыгруженного); без пароля Диска запись идёт и
ждёт; один пункт в пульт в день на тип сбоя; получение ссылки — под общей с кадрами блокировкой
браузера (подставной Playwright); блок на /owner — только владелец через форму, с подтверждением
паролем, пробная запись 2 минуты; пароль приложения и токен потока не утекают в /api/settings,
журнал, базу, пульт, HTML и вывод тестов. Если на машине есть настоящий ffmpeg — им же проверяется
команда записи на локальном живом FLV: файл закрывается и при обрыве потока, и при остановке
сигналом (SIGTERM всей группе, как при выкладке, и SIGKILL), проверка сертификата HTTPS.

15.11.2026 (после проверок V1 и V2): поток открывает Python и подаёт ffmpeg в stdin; фрагментированный
mp4 + перепаковка; пересмотр пропущенного до конца занятия; пункты в пульт — только по итогам;
части до 900 МБ; настройки записи — только с /owner; метки формы выдаёт сервер; проба — по одной и
не чаще раза в 5 минут; камера — строго по id; вход в Ivideon — не чаще раза в 15 минут; запись
проходит к браузеру раньше кадров фото-явки; права 0600/0700.

    python3 docs/rabota/zapis_testy.py [--work ПАПКА]
    ZAPIS_FFMPEG=/путь/к/ffmpeg-7.0.2 python3 docs/rabota/zapis_testy.py   # раздел Л — той же версией, что на сервере

Обвязка — из liniya_testy.py, сеть закрыта строже: соединения — только с портами своих подставок на
127.0.0.1 (WebDAV, живой FLV); агент-прокси песочницы (HTTPS_PROXY и т. п. — переменные не трогаем)
и любые внешние адреса закрыты в самом процессе теста, попытки записываются. Манго на стенде
выключено (пульт админа звал mango.calls через прокси). Отправки — ловушки, автопилот не стартует.
Подставки: ffmpeg — скрипт, который пишет mp4 нужного размера и/или падает; поток — объект без сети;
WebDAV — локальный http-сервер; ссылка на поток — функция без браузера и без сети; часы — стенда
(сон и «работа» ffmpeg двигают их, а не ждут). Данные выдуманные; пароли, ключи и токен
придумываются на ходу и не печатаются.
"""
from __future__ import annotations

import argparse
import base64
import importlib
import importlib.util
import json
import logging
import os
import pkgutil
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import types
from collections import namedtuple
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

KOREN = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, KOREN)

_spec = importlib.util.spec_from_file_location("liniya_testy", os.path.join(KOREN, "docs", "rabota", "liniya_testy.py"))
lt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lt)
proverka = lt.proverka

B = "https://app.kidsup.ru"
UA = "Mozilla/5.0 (stend zapis_testy)"
STR = {"accept": "text/html,application/xhtml+xml", "sec-fetch-mode": "navigate", "sec-fetch-site": "none",
       "sec-fetch-dest": "document", "user-agent": UA}
SVOI = {"origin": B, "sec-fetch-site": "same-origin", "sec-fetch-mode": "navigate", "user-agent": UA}
FETCH = {"accept": "application/json", "sec-fetch-mode": "cors", "sec-fetch-site": "same-origin", "user-agent": UA}
MSK = ZoneInfo("Europe/Moscow")
GB = 1024 ** 3

OBSHCHIY_ADMIN = ("admin", "stend-" + secrets.token_urlsafe(10))
VLADELEC = ("boris", "stend-" + secrets.token_urlsafe(10))
LENA = "stend-" + secrets.token_urlsafe(10)
YA_LOGIN = "stend-arhiv@example.org"                                   # выдуманный аккаунт Диска
YA_PAROL = "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(16))   # как пароль приложения
TOKEN = "stendtok" + secrets.token_hex(12)                                 # «access_token» потока
SEKRETY = [YA_PAROL, TOKEN]
ZAHVAT: list = []
m = None
z = None                                                                  # app.zapis

DEN = "2026-11-16"                                                         # понедельник стенда
# Выдуманные группы (имена групп — по образцу живых, это не персональные данные).
GRUPPY = [
    (120, "2627_РР.Музыка и речь_Группа 1_пн, ср_09:00_1,3 - 1,8", 4, 7),
    (121, "2627_РР.Музыка и речь_Группа 5_пн - пт_11:00_1,8 - 2,2", 4, 7),
    (122, "2627_РР.Музыка и речь_Группа 6_пн_12:00_2 - 3", 4, 7),
    (123, "2627_РР.Музыка и речь_Группа 8_пн - чт_17:00_2,2 - 3", 4, 7),
    (124, "2627_Заявки_Музыка и речь", 9, 0),
    (125, "Музыка и речь индивидуально Тестова Ева", 4, 1),               # в имени — ребёнок: в файл не попадёт
]
# (id, дата, начало, конец, группа, статус, кабинет-id или None)
ZANYATIYA = [
    (7101, DEN, "09:00", "09:50", 120, 2, 1),          # отменено
    (7102, DEN, "10:00", "10:50", 108, 0, 1),          # МиР Гр4 — пишем
    (7103, DEN, "11:00", "11:50", 121, 0, 1),          # удалено в МойКлассе
    (7104, DEN, "10:00", "10:50", 101, 0, 1),          # подготовка к школе — не наш фильтр
    (7105, DEN, "12:00", "12:50", 122, 0, 3),          # К3 — камеры для записи нет
    (7106, DEN, "17:00", "17:50", 123, 0, None),       # кабинет не указан → К1
    (7107, DEN, "13:00", "13:50", 124, 0, 1),          # «Заявки» — не занятие
    (7108, "2026-11-21", "16:30", "17:20", 117, 0, 1),  # МиР Гр7, суббота
    (7109, "2026-11-17", "10:00", "10:50", 108, 0, 1),  # вторник: позднее начало
    (7110, "2026-11-18", "10:00", "10:50", 108, 0, 1),  # среда: поток не сразу
    (7111, "2026-11-19", "10:00", "10:50", 108, 0, 1),  # четверг: места нет
    (7112, "2026-11-19", "12:00", "12:50", 108, 0, 1),  # четверг: невыгруженного много
    (7113, "2026-11-20", "10:00", "10:50", 108, 0, 1),  # пятница: выключено
    (7114, "2026-11-20", "15:00", "15:50", 125, 0, 2),  # индивидуальное, К2 нет камеры
    (7115, "2026-11-22", "11:00", "11:50", 108, 0, 1),  # воскресенье: перенесут на понедельник
    (7116, "2026-11-24", "10:00", "10:50", 108, 0, 1),  # вторник: выключили и включили, пока ждали
    (7117, "2026-11-24", "12:00", "12:50", 108, 0, 1),  # вторник: отменили и вернули в МойКлассе
    (7118, "2026-11-24", "15:00", "15:50", 108, 0, 1),  # вторник: временный сбой базы
    (7119, "2026-11-25", "10:00", "10:50", 108, 0, 1),  # среда: части по пределу размера
    (7120, "2026-11-25", "12:00", "12:50", 108, 0, 1),  # среда: поток так и не дали — пункт по итогам
    (7121, "2026-11-25", "15:00", "15:50", 101, 0, None),  # ПШ без кабинета: К1 не пишем
    (7122, "2026-11-26", "10:00", "10:50", 108, 0, 1),  # четверг: звук G.711 — та же ссылка
    (7123, "2026-11-26", "12:00", "12:50", 108, 0, 1),  # четверг: выключили посреди и включили
]


# --- сеть: только свои подставки на 127.0.0.1 -----------------------------------------------------

_ORIG_CONNECT = socket.socket.connect
_ORIG_CONNECT_EX = socket.socket.connect_ex
_ORIG_CREATE = socket.create_connection
SVOI_PORTY: set = set()           # порты своих подставок (WebDAV, живой FLV по http и https)
ZAPRET: list = []                 # попытки соединиться мимо них: (что, куда, откуда)
MESTNYE = ("127.0.0.1", "::1", "localhost")


def _proksi_sredy() -> set:
    """Адреса прокси из переменных окружения (агент-прокси песочницы): сами переменные не трогаем."""
    out = set()
    for k_, v_ in os.environ.items():
        if k_.lower() in ("http_proxy", "https_proxy", "all_proxy") and v_:
            try:
                u = urlsplit(v_ if "://" in v_ else "http://" + v_)
                out.add((u.hostname, u.port or 80))
            except ValueError:
                pass
    return out


def _otkuda() -> list:
    import traceback
    return [f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno}" for f in traceback.extract_stack()
            if "/app/" in f.filename or "zapis_testy" in f.filename][-4:]


def _zakryt_set_strozhe():
    """Поверх замка liniya_testy: TCP — только на порты своих подставок; прокси песочницы (он тоже на
    127.0.0.1) и всё внешнее — отказ в этом процессе, с записью в ZAPRET."""
    zakryto = socket.socket.connect
    proksi = _proksi_sredy()

    def mozhno(self, addr) -> bool:
        return self.family in (socket.AF_INET, socket.AF_INET6) and isinstance(addr, tuple) \
            and addr[0] in ("127.0.0.1", "::1") and addr[1] in SVOI_PORTY

    def connect(self, addr):
        if mozhno(self, addr):
            return _ORIG_CONNECT(self, addr)
        if self.family == getattr(socket, "AF_UNIX", -1):
            return zakryto(self, addr)
        ZAPRET.append(("connect", "прокси песочницы" if tuple(addr[:2]) in proksi else str(addr[:2]), _otkuda()))
        raise OSError("сеть закрыта (zapis_testy): только свои подставки на 127.0.0.1")

    def connect_ex(self, addr):
        if mozhno(self, addr):
            return _ORIG_CONNECT_EX(self, addr)
        ZAPRET.append(("connect_ex", str(addr[:2]) if isinstance(addr, tuple) else str(addr), _otkuda()))
        return 111

    def create_connection(address, *a, **k):
        if address[0] not in MESTNYE:
            ZAPRET.append(("create_connection", str(address[0]), _otkuda()))
            raise OSError("сеть закрыта (zapis_testy): внешний адрес")
        return _ORIG_CREATE(address, *a, **k)
    socket.socket.connect, socket.socket.connect_ex, socket.create_connection = connect, connect_ex, create_connection


# --- часы стенда -----------------------------------------------------------------------------------

PODSTAVA: Path = Path()
CHASY = {"baza": datetime(2026, 11, 16, 9, 0), "spali": 0.0}


def chasy() -> datetime:
    try:
        ff = sum(float(x) for x in (PODSTAVA / "vremya.txt").read_text().split())
    except OSError:
        ff = 0.0
    return CHASY["baza"] + timedelta(seconds=CHASY["spali"] + ff)


def postavit(dt: datetime) -> None:
    CHASY["baza"], CHASY["spali"] = dt, 0.0
    (PODSTAVA / "vremya.txt").write_text("")


def spat(s: float) -> None:
    CHASY["spali"] += max(0.0, float(s))


# --- подставной ffmpeg -----------------------------------------------------------------------------

FFMPEG = r'''#!/usr/bin/env python3
# подставной ffmpeg стенда.
# Запись (-i pipe:0): поток приходит в stdin (его не читаем), пишет mp4 по сценарию — фрагментированный
# (ftyp + moov + [moof + mdat]…), как настоящая команда записи; -fs — как у настоящего: стоп на пределе;
# «oborvat» — последний фрагмент недописан, процесс убит SIGKILL (как при жёсткой остановке).
# Перепаковка (-i файл): ftyp + moov + mdat с теми же байтами кадров, часы стенда не двигает.
import json, os, signal, sys
d = os.environ["ZAPIS_PODSTAVA"]
a = sys.argv[1:]
vhod = a[a.index("-i") + 1] if "-i" in a else ""
out = a[-1]
ZAG = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
MOOV = (16).to_bytes(4, "big") + b"moov" + b"\x00" * 8
def nuli(f, n):
    while n > 0:
        k = min(n, 1 << 20)
        f.write(b"\x00" * k)
        n -= k
if vhod != "pipe:0":
    with open(os.path.join(d, "remuks.jsonl"), "a") as f:
        f.write(json.dumps(a) + "\n")
    b = open(vhod, "rb").read()
    n = pos = 0
    while pos + 8 <= len(b):
        k = int.from_bytes(b[pos:pos + 4], "big")
        if k < 8 or pos + k > len(b):
            break
        if b[pos + 4:pos + 8] == b"mdat":
            n += k - 8
        pos += k
    if os.path.exists(os.path.join(d, "remuks_sboy")):
        sys.stderr.write("remux: stend fail\n")
        sys.exit(1)
    with open(out, "wb") as f:
        f.write(ZAG + MOOV + (8 + n).to_bytes(4, "big") + b"mdat")
        nuli(f, n)
    sys.exit(0)
with open(os.path.join(d, "vyzovy.jsonl"), "a") as f:
    f.write(json.dumps(a) + "\n")
p = os.path.join(d, "scenariy.json")
try:
    sc = json.load(open(p))
except Exception:
    sc = []
shag = sc.pop(0) if sc else {}
json.dump(sc, open(p, "w"))
t = float(a[a.index("-t") + 1]) if "-t" in a else 10.0
sek = t * float(shag.get("dolya", 1.0))
bps = int(shag.get("bps", 1000))
n = int(sek * bps)
fs = int(a[a.index("-fs") + 1]) if "-fs" in a else 0
frag = any("frag_keyframe" in x for x in a)
KUSOK = 64 * 1024
napisano = 0
if not shag.get("pusto"):
    with open(out, "wb") as f:
        f.write(ZAG)
        razmer = len(ZAG)
        if not shag.get("bez_moov"):
            f.write(MOOV)
            razmer += 16
        if frag:
            while napisano < n and not (fs and razmer >= fs):
                k = min(KUSOK, n - napisano)
                f.write((16).to_bytes(4, "big") + b"moof" + b"\x00" * 8 + (8 + k).to_bytes(4, "big") + b"mdat")
                nuli(f, k)
                napisano += k
                razmer += 24 + k
            if shag.get("oborvat"):
                f.write((16).to_bytes(4, "big") + b"moof" + b"\x00" * 8 + (8 + KUSOK).to_bytes(4, "big") + b"mdat")
                nuli(f, 1000)
        else:
            f.write((8 + n).to_bytes(4, "big") + b"mdat")
            nuli(f, n)
            napisano = n
    if n and napisano < n:
        sek = sek * napisano / n            # упёрлись в -fs раньше времени
with open(os.path.join(d, "vremya.txt"), "a") as f:
    f.write("%s\n" % sek)
if shag.get("err"):
    sys.stderr.write(shag["err"] + "\n")
    sys.stderr.flush()
if shag.get("oborvat"):
    os.kill(os.getpid(), signal.SIGKILL)
sys.exit(int(shag.get("rc", 0)))
'''


def scenariy(*shagi):
    (PODSTAVA / "scenariy.json").write_text(json.dumps(list(shagi)))


def vyzovy() -> list:
    try:
        return [json.loads(x) for x in (PODSTAVA / "vyzovy.jsonl").read_text().splitlines() if x.strip()]
    except OSError:
        return []


def arg(a: list, kl_: str) -> str:
    return a[a.index(kl_) + 1] if kl_ in a else ""


# --- подставная ссылка на поток --------------------------------------------------------------------

SSYLKI = {"vyzovy": [], "otkazy": 0, "zaderzhka": 0.0}


def ssylka_stenda(kam: dict) -> dict:
    SSYLKI["vyzovy"].append((chasy(), dict(kam)))
    if SSYLKI["zaderzhka"]:
        spat(SSYLKI["zaderzhka"])                 # браузер, my.ivideon.com, клик — ~30–40 с
    if SSYLKI["otkazy"] > 0:
        SSYLKI["otkazy"] -= 1
        return {"ok": False, "error": f"плеер Ivideon не запросил поток за 25 с (access_token={TOKEN})"}
    return {"ok": True, "url": f"http://127.0.0.1:9/cameras/{kam.get('cam_id')}:0/live_stream?op=GET"
                               f"&access_token={TOKEN}&q=2&video_codecs=h264&format=flv"}


# --- подставной поток (вместо HTTP-соединения с сервером Ivideon) ----------------------------------

POTOK = {"vyzovy": [], "otkazy": 0}


class _OtvetStenda:
    def __init__(self):
        self.ostalos = 3

    def read1(self, n):
        if self.ostalos <= 0:
            return b""
        self.ostalos -= 1
        return b"FLV\x01\x05" + b"\x00" * 1019

    def close(self):
        pass


class _SoedStenda:
    def close(self):
        pass


def potok_stenda(url: str):
    POTOK["vyzovy"].append(url)
    if POTOK["otkazy"] > 0:
        POTOK["otkazy"] -= 1
        raise z.PotokOshibka("сервер потока ответил 403")
    return _SoedStenda(), _OtvetStenda()


def remuksy() -> list:
    try:
        return [json.loads(x) for x in (PODSTAVA / "remuks.jsonl").read_text().splitlines() if x.strip()]
    except OSError:
        return []


# --- подставной WebDAV -----------------------------------------------------------------------------

DAV = {"auth": "", "dirs": {"/"}, "files": {}, "log": [], "put_oshibki": 0, "urezat": 0, "svobodno": 50 * GB}


class _Dav(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _put(self) -> str:
        p = unquote(urlsplit(self.path).path)
        return p.rstrip("/") or "/"

    def _telo(self) -> int:
        n = int(self.headers.get("Content-Length") or 0)
        ost = n
        while ost > 0:
            ch = self.rfile.read(min(ost, 1 << 20))
            if not ch:
                break
            ost -= len(ch)
        return n - ost

    def _otvet(self, kod: int, body: bytes = b"", ctype: str = "text/plain"):
        self.send_response(kod)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _avt(self) -> bool:
        ok = self.headers.get("Authorization") == DAV["auth"]
        DAV["log"].append((self.command, self._put(), ok))
        if not ok:
            self._otvet(401, b"unauthorized")
        return ok

    def do_MKCOL(self):
        self._telo()
        if not self._avt():
            return
        p = self._put()
        if p in DAV["dirs"] or p in DAV["files"]:
            return self._otvet(405)
        roditel = p.rsplit("/", 1)[0] or "/"
        if roditel not in DAV["dirs"]:
            return self._otvet(409)
        DAV["dirs"].add(p)
        self._otvet(201)

    def do_PUT(self):
        n = self._telo()
        if not self._avt():
            return
        p = self._put()
        if (p.rsplit("/", 1)[0] or "/") not in DAV["dirs"]:
            return self._otvet(409)
        if DAV["put_oshibki"] > 0:
            DAV["put_oshibki"] -= 1
            return self._otvet(500, b"internal")
        DAV["files"][p] = n - DAV["urezat"]
        self._otvet(201)

    def do_PROPFIND(self):
        self._telo()
        if not self._avt():
            return
        p = self._put()
        if p == "/":
            body = ('<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:"><d:response><d:href>/</d:href>'
                    f'<d:propstat><d:status>HTTP/1.1 200 OK</d:status><d:prop><d:quota-available-bytes>{DAV["svobodno"]}'
                    '</d:quota-available-bytes><d:quota-used-bytes>1</d:quota-used-bytes></d:prop></d:propstat>'
                    '</d:response></d:multistatus>')
            return self._otvet(207, body.encode(), "application/xml")
        if p in DAV["files"]:
            body = ('<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:"><d:response><d:href>x</d:href>'
                    f'<d:propstat><d:status>HTTP/1.1 200 OK</d:status><d:prop><d:getcontentlength>{DAV["files"][p]}'
                    '</d:getcontentlength></d:prop></d:propstat></d:response></d:multistatus>')
            return self._otvet(207, body.encode(), "application/xml")
        if p in DAV["dirs"]:
            return self._otvet(207, b'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"></d:multistatus>', "application/xml")
        self._otvet(404)

    def _lishnee(self):
        self._telo()
        DAV["log"].append((self.command, self._put(), None))
        self._otvet(405)

    do_PROPPATCH = do_DELETE = do_MOVE = do_COPY = do_POST = do_GET = _lishnee


def dav_avt(login: str, parol: str) -> str:
    return "Basic " + base64.b64encode(f"{login}:{parol}".encode()).decode()


# --- обвязка ---------------------------------------------------------------------------------------

def kl(**kw):
    from fastapi.testclient import TestClient
    return TestClient(m.app, base_url=B, follow_redirects=False, **kw)


def voyti(c, login, parol, next_="/owner"):
    return c.post("/vhod", data={"login": login, "parol": parol, "next": next_}, headers=SVOI)


def vladelec():
    c = kl()
    r = voyti(c, *VLADELEC)
    assert r.status_code == 303, r.status_code
    return c


def forma(**izm) -> dict:
    """Поля формы блока, как их показывает страница (что видно — то и уходит), плюс изменения."""
    s = z.sostoyanie()
    f = {"polya": "1", "filtr": "; ".join(s["filtr"]), "papka": s["papka"], "yadisk_login": s["login"],
         "kamery": "; ".join(f"{k['komnata']} = {k['imya']}" + (f" = {k['cam_id']}" if k["cam_id"] else "")
                             for k in s["kamery"])}
    if s["vklyucheno"]:
        f["vkl"] = "1"
    for k_, v_ in izm.items():
        if v_ is None:
            f.pop(k_, None)
        else:
            f[k_] = v_
    return f


def zp(c, deystvie, moy=VLADELEC[1], metka="", **izm):
    """Отправить форму блока. Метка — выданная сервером (как в странице), если не задана другая."""
    data = {"deystvie": deystvie, **forma(**izm), "metka": metka or z._metka_vydat()}
    if metka is None:
        data.pop("metka")
    if moy is not None:
        data["moy_parol"] = moy
    return c.post("/owner/zapis", data=data, headers=SVOI)


def osnovnaya():
    con = sqlite3.connect(m.config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def punkty() -> list:
    with osnovnaya() as o:
        try:
            return [dict(r) for r in o.execute("SELECT day, who, text, phone, source FROM plan_inbox "
                                               "WHERE source='запись занятий' ORDER BY id")]
        except sqlite3.OperationalError:
            return []


def zapis_po_uroku(lid: int) -> dict:
    with osnovnaya() as o:
        r = o.execute("SELECT * FROM zapis WHERE lesson_id=?", (lid,)).fetchone()
        return dict(r) if r else {}


def fayly(zid: int) -> list:
    with osnovnaya() as o:
        return [dict(r) for r in o.execute("SELECT * FROM zapis_fayly WHERE zapis_id=? ORDER BY id", (zid,))]


def lokalnye() -> list:
    return sorted(str(p.relative_to(z.DIR)) for p in z.DIR.rglob("*") if p.is_file()) if z.DIR.exists() else []


def zasejat(baza: str):
    c = sqlite3.connect(baza)
    c.executemany("INSERT OR REPLACE INTO classes (id, name, course_id, status, max_students, raw) "
                  "VALUES (?,?,?,'opened',?,'{}')", GRUPPY)
    c.executemany("INSERT OR REPLACE INTO rooms (id, name, raw) VALUES (?,?,'{}')",
                  [(1, "К1"), (2, "К2"), (3, "К3")])
    c.executemany("INSERT OR REPLACE INTO lessons (id, date, begin_time, end_time, class_id, status, raw) "
                  "VALUES (?,?,?,?,?,?,?)",
                  [(i, d, b, e, k, s, json.dumps({"roomId": r} if r else {})) for i, d, b, e, k, s, r in ZANYATIYA])
    c.execute("CREATE TABLE IF NOT EXISTS mk_udaleno (tbl TEXT, rid INTEGER, ts TEXT, istochnik TEXT, "
              "PRIMARY KEY (tbl, rid))")
    c.execute("INSERT OR REPLACE INTO mk_udaleno VALUES ('lessons', 7103, '2026-11-15T20:00:00', 'стенд')")
    c.commit()
    c.close()


# --- А. Какие занятия пишутся ----------------------------------------------------------------------

def test_vybor():
    print("А. Какие занятия пишутся: фильтр групп, отменённые и удалённые — нет, кабинет → камера")
    zz = {x["lesson_id"]: x for x in z.zanyatiya(DEN)}
    proverka("в списке дня — только группы «Музыка и речь»: подготовка к школе и «Заявки» не попали",
             7104 not in zz and 7107 not in zz and {7101, 7102, 7103, 7105, 7106} <= set(zz), sorted(zz))
    g4 = zz[7102]
    proverka("МиР Гр4 10:00–10:50 в К1: пишем, камера «Комната 1» с id по умолчанию",
             g4["pisat"] and g4["gruppa"] == "МиР Гр4" and g4["komnata"] == "К1" and g4["kamera"]["imya"] == "Комната 1"
             and g4["kamera"]["cam_id"] == "100-TYpMigSeczbQqK032w6QzZ"
             and (g4["begin"], g4["end"]) == (datetime(2026, 11, 16, 10, 0), datetime(2026, 11, 16, 10, 50)), g4)
    proverka("отменённое в МойКлассе (статус 2) — не пишем, причина видна",
             not zz[7101]["pisat"] and zz[7101]["pochemu"] == "отменено в МойКлассе")
    proverka("удалённое в МойКлассе (пометка mk_udaleno) — не пишем", not zz[7103]["pisat"]
             and "удалено" in zz[7103]["pochemu"])
    proverka("занятие в К3 — камеры для записи нет: не пишем, причина «кабинет К3 — камеры для записи нет»",
             not zz[7105]["pisat"] and "кабинет К3 — камеры для записи нет" in zz[7105]["pochemu"])
    proverka("кабинет в МойКлассе не указан у группы «Музыка и речь» — пишем К1, и это видно в причине",
             zz[7106]["pisat"] and zz[7106]["komnata"] == "К1" and "не указан — пишем К1" in zz[7106]["pochemu"]
             and "«Музыка и речь» идут в К1" in zz[7106]["pochemu"], zz[7106]["pochemu"])
    m.db.set_setting("zapis_filtr", "Музыка и речь; ПШ1")
    psh = {x["lesson_id"]: x for x in z.zanyatiya("2026-11-25")}.get(7121) or {}
    m.db.set_setting("zapis_filtr", "")
    proverka("S6: занятие без кабинета у другой группы под фильтром (ПШ) — НЕ пишем с К1: «укажите кабинет»",
             psh and not psh["pisat"] and psh["kamera"] is None and "укажите кабинет" in psh["pochemu"], psh)
    imena = [z._imya_fayla({"day": DEN, "begin_time": "10:00", "gruppa": "МиР Гр4"}, 1),
             z._imya_fayla({"day": DEN, "begin_time": "10:00", "gruppa": "МиР Гр4"}, 2),
             z._imya_fayla({"day": "2026-11-14", "begin_time": "18:05:07", "proba": 1, "komnata": "К1"}, 1)]
    proverka("имена файлов: «2026-11-16 10-00 МиР Гр4.mp4», «… (часть 2).mp4», проба — до секунды «… 18-05-07 Проба К1.mp4»",
             imena == ["2026-11-16 10-00 МиР Гр4.mp4", "2026-11-16 10-00 МиР Гр4 (часть 2).mp4",
                       "2026-11-14 18-05-07 Проба К1.mp4"], imena)
    proverka("короткое имя: только сокращение и номер группы, имя ребёнка из названия группы в файл не попадает",
             z.korotkoe_imya(GRUPPY[-1][1], 125) == "МиР группа 125"
             and z.korotkoe_imya("2627_РР.Музыка и речь_Группа 7_сб_16:30_1.8 - 2.2 года", 117) == "МиР Гр7"
             and "Тестова" not in z._imya_fayla({"day": DEN, "begin_time": "15:00",
                                                  "gruppa": z.korotkoe_imya(GRUPPY[-1][1], 125)}, 1))
    m.db.set_setting("zapis_filtr", "Группа 8")
    zz2 = [x["lesson_id"] for x in z.zanyatiya(DEN)]
    m.db.set_setting("zapis_filtr", "")
    proverka("фильтр из настройки: «Группа 8» → только занятие Гр8", zz2 == [7106], zz2)
    proverka("пустой фильтр в настройке → по умолчанию «Музыка и речь»", z.filtry() == ["Музыка и речь"])
    k, osh = z.razobrat_kamery("K1 = Комната 1 = abc-DEF_123; к2 = Комната 2 = xyz-987_Q")
    proverka("камеры: «K1» латиницей и «к2» понимаются как К1 и К2",
             not osh and k == {"К1": {"komnata": "К1", "imya": "Комната 1", "cam_id": "abc-DEF_123"},
                               "К2": {"komnata": "К2", "imya": "Комната 2", "cam_id": "xyz-987_Q"}}, (k, osh))
    k, osh = z.razobrat_kamery("К1 = Комната 1 = abc-DEF_123; К2 = Комната 2")
    proverka("S6: камера без id — отказ «нужен id камеры» (запись только с камеры с этим id)",
             list(k) == ["К1"] and len(osh) == 1 and "нужен id камеры" in osh[0], (k, osh))
    _, osh = z.razobrat_kamery("Комната 1; К1 = <b> = 1; К2 = Комната 2 = id с пробелом")
    proverka("камеры: строка без кабинета, разметка в названии и id с пробелом — три отказа", len(osh) == 3, osh)


# --- Б. Время записи --------------------------------------------------------------------------------

def test_vremya():
    print("Б. Когда пишем: поток за 30 мин, ссылка за 2 мин, запись с начала−1 до конца+1; выгрузка и удаление")
    n0 = len(vyzovy())
    postavit(datetime(2026, 11, 16, 9, 29))
    r = z.tick()
    proverka("09:29 — до МиР Гр4 (10:00) больше 30 минут: ничего не запущено", r == [] and not zapis_po_uroku(7102), r)
    postavit(datetime(2026, 11, 16, 9, 31))
    SSYLKI["vyzovy"].clear()
    r = z.tick()
    zap = zapis_po_uroku(7102)
    proverka("09:31 — поток записи МиР Гр4 запущен (один: отменённое, удалённое и К3 — нет)",
             len(r) == 1 and zap.get("id") == r[0] and not zapis_po_uroku(7101) and not zapis_po_uroku(7103)
             and not zapis_po_uroku(7105), r)
    proverka("свежая ссылка на поток взята ровно за 2 минуты до начала (09:58), камера «Комната 1»",
             [t for t, _ in SSYLKI["vyzovy"]] == [datetime(2026, 11, 16, 9, 58)]
             and SSYLKI["vyzovy"][0][1]["imya"] == "Комната 1", SSYLKI["vyzovy"])
    v = vyzovy()[n0:]
    a = v[0] if v else []
    proverka("ffmpeg вызван один раз: с 09:59 до 10:51 → -t 3120", len(v) == 1 and arg(a, "-t") == "3120", [arg(x, "-t") for x in v])
    proverka("…без перекодирования (-c copy), видео и звук (если есть), выход — фрагментированный mp4 "
             "(+frag_keyframe+empty_moov, фрагмент ≤ 2 с — читается и после SIGKILL)",
             arg(a, "-c") == "copy" and arg(a, "-f") == "mp4" and "0:v:0" in a and "0:a:0?" in a and "-vcodec" not in a
             and arg(a, "-movflags") == "+frag_keyframe+empty_moov+default_base_moof"
             and arg(a, "-frag_duration") == "2000000" and "-nostdin" not in a, a)
    proverka("B1/S3: вход — stdin (-f flv -i pipe:0): ссылки с токеном в командной строке ffmpeg нет; поток открыл "
             "Python по ссылке формата flv",
             arg(a, "-i") == "pipe:0" and arg(a, "-f") in ("flv", "mp4") and a[a.index("-i") - 1] == "flv"
             and not any(TOKEN in x for x in a) and POTOK["vyzovy"] and "format=flv" in POTOK["vyzovy"][-1]
             and TOKEN in POTOK["vyzovy"][-1], a[:8])
    proverka("…выход — data/zapisi/2026-11-16/«2026-11-16 10-00 МиР Гр4.mp4»",
             a[-1].endswith("/zapisi/2026-11-16/2026-11-16 10-00 МиР Гр4.mp4"), a[-1:] if a else a)
    proverka("L1: -fs — предел части 900 МБ (у бесплатного Яндекс.Диска предел файла — 1 ГБ)",
             arg(a, "-fs") == str(900 * 1024 ** 2) and z.MAX_FAYL < GB, arg(a, "-fs"))
    rm = remuksy()
    proverka("B1: после занятия часть перепакована -c copy в обычный mp4 (+faststart), часы стенда не двигались",
             len(rm) == 1 and arg(rm[0], "-c") == "copy" and arg(rm[0], "-movflags") == "+faststart"
             and arg(rm[0], "-i").endswith("2026-11-16 10-00 МиР Гр4.mp4"), rm)
    zap = zapis_po_uroku(7102)
    ff = fayly(zap["id"])
    kuda = "/Архив методики/Музыка и речь/2026-11/2026-11-16 10-00 МиР Гр4.mp4"
    proverka("после занятия файл на Диске: «/Архив методики/Музыка и речь/2026-11/2026-11-16 10-00 МиР Гр4.mp4»",
             kuda in DAV["files"] and len(ff) == 1 and ff[0]["status"] == "vygruzhen" and ff[0]["disk_put"] == kuda,
             (sorted(DAV["files"]), ff))
    proverka("…размер на Диске совпал с записанным (3 120 000 + заголовки), локальный файл удалён",
             DAV["files"].get(kuda) == ff[0]["razmer"] == 3120000 + 48 and not lokalnye(), (DAV["files"].get(kuda), lokalnye()))
    proverka("…папки созданы MKCOL по одной: «/Архив методики» → «…/Музыка и речь» → «…/2026-11»",
             [p for k_, p, _ in DAV["log"] if k_ == "MKCOL"][:3]
             == ["/Архив методики", "/Архив методики/Музыка и речь", "/Архив методики/Музыка и речь/2026-11"],
             [x for x in DAV["log"] if x[0] == "MKCOL"][:4])
    posle_put = [k_ for k_, p, _ in DAV["log"] if p == kuda]
    proverka("…порядок: PROPFIND (нет файла) → PUT → PROPFIND (размер), все с паролем приложения",
             posle_put == ["PROPFIND", "PUT", "PROPFIND"] and all(ok for _, _, ok in DAV["log"]), DAV["log"][-4:])
    proverka("…публичных ссылок нет: ни PROPPATCH, ни других методов, кроме MKCOL / PUT / PROPFIND",
             {k_ for k_, _, _ in DAV["log"]} <= {"MKCOL", "PUT", "PROPFIND"})
    proverka("запись: статус «на Диске», 10:00, МиР Гр4, К1", (zap["status"], zap["begin_time"], zap["gruppa"], zap["komnata"])
             == ("vygruzheno", "10:00", "МиР Гр4", "К1"), zap)
    proverka("сбоев нет — пунктов в пульт нет", punkty() == [], punkty())
    n1 = len(vyzovy())
    postavit(datetime(2026, 11, 16, 10, 40))
    r = z.tick()
    proverka("повторный тик во время/после того же занятия новую запись не начинает", r == [] and len(vyzovy()) == n1, r)

    # вторник: сервер перезапускался — тик пришёл в 10:20, посреди занятия
    postavit(datetime(2026, 11, 17, 10, 20))
    r = z.tick()
    a = vyzovy()[-1]
    proverka("позднее начало (тик в 10:20 посреди занятия 10:00–10:50): ссылка сразу, -t 1860 — до 10:51",
             len(r) == 1 and arg(a, "-t") == "1860" and SSYLKI["vyzovy"][-1][0] == datetime(2026, 11, 17, 10, 20),
             (r, arg(a, "-t")))
    with osnovnaya() as o:                        # «записи не было»: убираем строку и смотрим 10:49 и 10:50
        zid = o.execute("SELECT id FROM zapis WHERE lesson_id=7109").fetchone()[0]
        o.execute("DELETE FROM zapis_fayly WHERE zapis_id=?", (zid,))
        o.execute("DELETE FROM zapis WHERE id=?", (zid,))
    postavit(datetime(2026, 11, 17, 10, 50))
    proverka("за минуту до конца+1 (10:50) занятие без записи уже не начинается", z.tick() == [] and not zapis_po_uroku(7109))


# --- В. Обрыв и сбои потока -------------------------------------------------------------------------

def test_obryv():
    print("В. Обрыв: новая ссылка и вторая часть до конца занятия; сбои — один пункт в день на тип")
    scenariy({"rc": 1, "dolya": 0.3, "err": f"Connection reset by peer: http://x/live_stream?access_token={TOKEN}"},
             {"rc": 1, "dolya": 0.2, "err": "Stream ends prematurely"}, {})
    SSYLKI["vyzovy"].clear()
    n0 = len(vyzovy())
    postavit(datetime(2026, 11, 16, 16, 45))
    z.tick()
    zap = zapis_po_uroku(7106)
    ff = fayly(zap["id"])
    v = vyzovy()[n0:]
    proverka("МиР Гр8 (кабинет не указан → К1): три части — поток дважды обрывался",
             [f["imya"] for f in ff] == ["2026-11-16 17-00 МиР Гр8.mp4", "2026-11-16 17-00 МиР Гр8 (часть 2).mp4",
                                         "2026-11-16 17-00 МиР Гр8 (часть 3).mp4"], [f["imya"] for f in ff])
    t = [int(arg(a, "-t")) for a in v]
    proverka("ссылка бралась заново перед каждой частью: 16:58, после обрыва — ещё две", len(SSYLKI["vyzovy"]) == 3
             and SSYLKI["vyzovy"][0][0] == datetime(2026, 11, 16, 16, 58), [x[0] for x in SSYLKI["vyzovy"]])
    proverka("длительности частей: 3120 с, затем остаток до 17:51 (3120−936−5 = 2179), затем остаток второй",
             t[0] == 3120 and t[1] == 2179 and t[2] == int(2179 - 2179 * 0.2 - 5), t)
    proverka("все три части — на Диске, локальных файлов нет",
             all(f["status"] == "vygruzhen" for f in ff) and not lokalnye() and zap["status"] == "vygruzheno", (ff, lokalnye()))
    proverka("итог записи — «3 части»", "3 части" in (zap["prichina"] or ""), zap["prichina"])
    proverka("m3: обрывы с продолжением записи — пунктов в пульт нет (повтор удался, занятие записано целиком)",
             not [x for x in punkty() if x["day"] == DEN], punkty())
    with osnovnaya() as o:
        n = o.execute("SELECT COUNT(*) FROM zapis_sboi WHERE day=?", (DEN,)).fetchone()[0]
    proverka("…и в счётчике сбоев за день ничего нет", n == 0, n)
    proverka("токена потока нет в ошибке части (замаскирован)",
             all(TOKEN not in (f["oshibka"] or "") + (f["zametka"] or "") for f in ff)
             and "access_token=***" in (ff[0]["zametka"] or ""), ff[0]["zametka"])
    proverka("все три части перепакованы в обычный mp4", len(remuksy()) >= 4
             and all(arg(x, "-movflags") == "+faststart" for x in remuksy()), len(remuksy()))

    # среда: плеер дважды не отдал поток, потом ffmpeg упал сразу (403), потом пошло
    SSYLKI["vyzovy"].clear()
    SSYLKI["otkazy"] = 2
    scenariy({"rc": 1, "dolya": 0.001, "bps": 10, "err": "Server returned 403 Forbidden (access denied)"}, {})
    n0 = len(vyzovy())
    postavit(datetime(2026, 11, 18, 9, 40))
    z.tick()
    zap = zapis_po_uroku(7110)
    ff = fayly(zap["id"])
    v = vyzovy()[n0:]
    proverka("ссылка: 09:58 — нет, 09:58:30 — нет, 09:59 — есть", [x[0] for x in SSYLKI["vyzovy"]][:3]
             == [datetime(2026, 11, 18, 9, 58), datetime(2026, 11, 18, 9, 58, 30), datetime(2026, 11, 18, 9, 59)],
             [x[0] for x in SSYLKI["vyzovy"]])
    proverka("ffmpeg упал сразу (файл крошечный) — часть битая и удалена, новая ссылка, запись дальше под тем же именем",
             [(f["imya"], f["status"]) for f in ff] == [("2026-11-18 10-00 МиР Гр4.mp4", "bityy"),
                                                       ("2026-11-18 10-00 МиР Гр4.mp4", "vygruzhen")]
             and len(v) == 2 and "403" in (ff[0]["zametka"] or ""), [(f["imya"], f["status"], f["zametka"]) for f in ff])
    postavit(datetime(2026, 11, 18, 10, 55))
    z.tick()                                          # итог занятия подводится после его конца
    proverka("m3: плеер дважды не дал поток, ffmpeg раз упал — но занятие записано: пунктов за среду нет",
             not [x for x in punkty() if x["day"] == "2026-11-18"] and zapis_po_uroku(7110)["itog"] == "ok",
             [x["text"][:60] for x in punkty()])


# --- Г. Выгрузка: проверка размера, повтор раз в час, ошибки Диска ----------------------------------

def test_vygruzka():
    print("Г. Выгрузка: размер не совпал — файл остаётся; повтор раз в час; 500, 401, нет места на Диске")
    postavit(datetime(2026, 11, 16, 13, 0))
    DAV["urezat"] = 1
    r = z.proba()
    zid = r["zapis_id"]
    ff = fayly(zid)
    proverka("пробная запись: 2 минуты (-t 120) с К1, файл «2026-11-16 13-00-00 Проба К1.mp4»",
             arg(vyzovy()[-1], "-t") == "120" and [f["imya"] for f in ff] == ["2026-11-16 13-00-00 Проба К1.mp4"], ff)
    proverka("Диск сохранил на байт меньше: PROPFIND показал другой размер — файл НЕ удалён, ошибка с причиной",
             ff[0]["status"] == "gotov" and lokalnye() == ["2026-11-16/2026-11-16 13-00-00 Проба К1.mp4"]
             and "не совпал" in (ff[0]["oshibka"] or "") and ff[0]["popytok"] == 1, ff[0])
    p = [x for x in punkty() if "не принял файл" in x["text"] and x["day"] == DEN]
    proverka("m3: первая неудача выгрузки — пункта в пульт нет (через час повтор, он может пройти)", not p, punkty())
    DAV["urezat"] = 0
    postavit(datetime(2026, 11, 16, 13, 31))
    z.tick()
    proverka("через полчаса тик не повторяет (повтор раз в час)", fayly(zid)[0]["popytok"] == 1 and lokalnye())
    postavit(datetime(2026, 11, 16, 14, 3))
    z.tick()
    ff = fayly(zid)
    proverka("через час — повтор: файл на Диске, размер совпал, локальный удалён, попыток 2",
             ff[0]["status"] == "vygruzhen" and ff[0]["popytok"] == 2 and not lokalnye(), ff[0])

    DAV["put_oshibki"] = 1
    zid = z.proba()["zapis_id"]
    ff = fayly(zid)
    proverka("PUT ответил 500 → «Диск ответил 500», файл ждёт; первая неудача — пункта нет",
             ff[0]["status"] == "gotov" and "500" in (ff[0]["oshibka"] or "")
             and not [x for x in punkty() if "не принял файл" in x["text"] and x["day"] == DEN], ff[0])
    postavit(datetime(2026, 11, 16, 15, 20))
    z.tick()
    proverka("через час — ушёл", fayly(zid)[0]["status"] == "vygruzhen" and not lokalnye())

    DAV["svobodno"] = 1000
    zid = z.proba()["zapis_id"]
    ff = fayly(zid)
    proverka("на Диске места меньше файла → «на Яндекс.Диске свободно …», PUT не делался",
             ff[0]["status"] == "gotov" and "на Яндекс.Диске свободно" in (ff[0]["oshibka"] or "")
             and not any(k_ == "PUT" and p_.endswith(ff[0]["imya"]) for k_, p_, _ in DAV["log"]), ff[0])
    DAV["svobodno"] = 50 * GB
    m.db.set_setting("yadisk_password", "nevernyy" + secrets.token_hex(4))
    postavit(datetime(2026, 11, 16, 16, 30))
    z.tick()
    ff = fayly(zid)
    proverka("неверный пароль приложения → 401: «Диск не принял логин или пароль приложения (401)»",
             ff[0]["status"] == "gotov" and "не принял логин или пароль приложения (401)" in (ff[0]["oshibka"] or ""), ff[0])
    p = [x for x in punkty() if "не принял файл" in x["text"] and x["day"] == DEN]
    proverka("…это вторая неудача подряд у этого файла — пункт «Яндекс.Диск не принял файл (два раза подряд)», один",
             len(p) == 1 and "два раза подряд" in p[0]["text"] and ff[0]["imya"] in p[0]["text"], [x["text"] for x in p])
    m.db.set_setting("yadisk_password", YA_PAROL)
    # файл уже лежит на Диске целиком (сервер упал после PUT, до удаления): повторно не заливаем
    kuda = f"/Архив методики/Музыка и речь/2026-11/{ff[0]['imya']}"
    DAV["files"][kuda] = (z.DIR / ff[0]["fayl"]).stat().st_size
    nput = sum(1 for k_, p_, _ in DAV["log"] if k_ == "PUT")
    z.vygruzit_vse(True)
    proverka("файл уже на Диске того же размера → без повторного PUT, локальный удалён",
             fayly(zid)[0]["status"] == "vygruzhen" and sum(1 for k_, p_, _ in DAV["log"] if k_ == "PUT") == nput
             and not lokalnye())


# --- Д. Без пароля Диска -----------------------------------------------------------------------------

def test_bez_parolya(c):
    print("Д. Пароль Диска не введён: запись идёт, файл ждёт; владелец видит «введите пароль»; ввёл — ушло")
    m.db.set_setting("yadisk_password", "")
    postavit(datetime(2026, 11, 16, 18, 0))
    n_dav = len(DAV["log"])
    zid = z.proba()["zapis_id"]
    ff = fayly(zid)
    proverka("без пароля проба записана, файл лежит на сервере, к Диску не обращались",
             ff[0]["status"] == "gotov" and lokalnye() == ["2026-11-16/2026-11-16 18-00-00 Проба К1.mp4"]
             and len(DAV["log"]) == n_dav, (ff[0], lokalnye()))
    fp = z.DIR / ff[0]["fayl"]
    prava = (oct(fp.stat().st_mode & 0o777), oct(fp.parent.stat().st_mode & 0o777), oct(z.DIR.stat().st_mode & 0o777))
    proverka("S4: видео на сервере — 0600, папка дня и папка записей — 0700 (читает только служба)",
             prava == ("0o600", "0o700", "0o700"), prava)
    rab = z.DIR / ".rab"
    proverka("S3: журналы ffmpeg после части удалены (в .rab пусто), .rab — 0700",
             rab.exists() and not list(rab.iterdir()) and oct(rab.stat().st_mode & 0o777) == "0o700",
             [x.name for x in rab.iterdir()] if rab.exists() else None)
    post = next(x for x in z.poslednie(100) if x["id"] == zid)
    proverka("в «Последних записях» — «ждёт на сервере: введите пароль приложения Яндекса»",
             "введите пароль приложения" in post["sostoyanie"] and post["vid"] == "zhdet", post)
    z.tick()
    z.tick()
    p = [x for x in punkty() if "нет пароля приложения" in x["text"]]
    proverka("пункт в пульт «записи ждут на сервере — нет пароля» — один за день, хоть тиков два",
             len(p) == 1 and "1 файл" in p[0]["text"], [x["text"] for x in p])
    h = c.get("/owner", headers=STR).text
    proverka("на /owner у владельца — плашка «Введите пароль приложения Яндекса» и «Пароль приложения: не введён»",
             "id='zapis-net-parolya'" in h and "Введите пароль приложения Яндекса" in h
             and "Пароль приложения: <b>не введён</b>" in h)
    r = zp(c, "sohranit", yadisk_password=" ".join([YA_PAROL[:8], YA_PAROL[8:]]))   # с пробелом посередине, как копируют
    proverka("владелец ввёл пароль (с пробелом посередине — убран) → «Сохранено: пароль приложения», вход проверен",
             r.status_code == 200 and "Сохранено: пароль приложения Яндекса" in r.text and "Яндекс.Диск принял вход" in r.text
             and m.db.get_setting("yadisk_password") == YA_PAROL, r.status_code)
    proverka("…и сразу выгрузка: файл на Диске, с сервера удалён", fayly(zid)[0]["status"] == "vygruzhen" and not lokalnye()
             and "Выгрузка на Диск началась" in r.text)
    proverka("…пароля нет в ответе", YA_PAROL not in r.text)


# --- Е. Место на диске сервера ---------------------------------------------------------------------

Usage = namedtuple("Usage", "total used free")


def test_mesto():
    print("Е. Место на сервере: свободно < 3 ГБ — не начинать; невыгруженного > 6 ГБ — пропуск с причиной")
    bylo = z._disk_usage
    z._disk_usage = lambda: Usage(20 * GB, 18 * GB, 2 * GB)
    n0 = len(vyzovy())
    SSYLKI["vyzovy"].clear()
    postavit(datetime(2026, 11, 19, 9, 40))
    z.tick()
    zap = zapis_po_uroku(7111)
    proverka("свободно 2 ГБ → занятие 10:00 пропущено: «свободно 2,0 ГБ — меньше 3 ГБ, запись не начинаю»",
             zap["status"] == "propusk" and "свободно 2,0 ГБ — меньше 3 ГБ" in zap["prichina"], zap)
    proverka("…ни ссылки, ни ffmpeg", len(vyzovy()) == n0 and not SSYLKI["vyzovy"])
    proverka("m3: пока занятие не кончилось, пункта о месте нет (место могут освободить — запись начнётся)",
             not [x for x in punkty() if "не хватает места" in x["text"]])
    postavit(datetime(2026, 11, 19, 10, 52))
    z.tick()
    p = [x for x in punkty() if "не хватает места" in x["text"]]
    proverka("…занятие кончилось, места так и не было — один пункт Борису «не хватает места на сервере — занятие не "
             "записано»", len(p) == 1 and "10:00 МиР Гр4" in p[0]["text"] and zapis_po_uroku(7111)["itog"] == "mesto",
             [x["text"] for x in p])
    z.tick()
    proverka("…следующий тик второго пункта не ставит", len([x for x in punkty() if "не хватает места" in x["text"]]) == 1)
    z._disk_usage = lambda: Usage(20 * GB, 16 * GB, 4 * GB)
    predel = z._predel_fayla(z.mesto())
    proverka("свободно 4 ГБ → предел части (-fs) 900 МБ — предел файла Диска", predel == 900 * 1024 ** 2, predel)
    z._disk_usage = lambda: Usage(20 * GB, 18.5 * GB, 1.5 * GB)
    predel = z._predel_fayla(z.mesto())
    proverka("свободно 1,5 ГБ → предел части 0,5 ГБ: на диске остаётся 1 ГБ", predel == GB // 2, predel)
    z._disk_usage = lambda: Usage(20 * GB, 16 * GB, 4 * GB)
    # на сервере ждёт невыгруженное больше предела
    m.db.set_setting("yadisk_password", "")
    bylo_max = z.MAX_LOKALNO
    postavit(datetime(2026, 11, 19, 11, 0))
    zid = z.proba()["zapis_id"]                     # ~120 КБ ждут выгрузки
    z.MAX_LOKALNO = 100_000
    postavit(datetime(2026, 11, 19, 11, 40))
    n0 = len(vyzovy())
    z.tick()
    zap = zapis_po_uroku(7112)
    proverka("невыгруженного больше предела → занятие 12:00 пропущено: «ждут выгрузки … занятие пропущено»",
             zap["status"] == "propusk" and "ждут выгрузки" in zap["prichina"] and "занятие пропущено" in zap["prichina"]
             and len(vyzovy()) == n0, zap)
    s = z.sostoyanie()
    proverka("состояние: «net_mesta» с той же причиной, ждут выгрузки 1 файл", "ждут выгрузки" in s["mesto"]["net_mesta"]
             and s["mesto"]["zhdut_vygruzki"] == 1, s["mesto"])
    z.MAX_LOKALNO = bylo_max
    z._disk_usage = bylo
    m.db.set_setting("yadisk_password", YA_PAROL)
    z.vygruzit_vse(True)
    proverka("пароль вернули — ждавший файл ушёл", fayly(zid)[0]["status"] == "vygruzhen" and not lokalnye())


# --- Ж. Блок на /owner -------------------------------------------------------------------------------

def test_owner():
    print("Ж. Блок «Запись занятий» на /owner: только владелец через форму, подтверждение паролем")
    r = kl().get("/owner", auth=VLADELEC)
    proverka("прежний boris в сером окне: вместо блока — ссылка «войти через форму»",
             r.status_code == 200 and "id='zapis'" in r.text and "id='zapis-forma'" not in r.text
             and "Управлять записью занятий" in r.text, r.status_code)
    c = vladelec()
    r = c.get("/owner", headers=STR)
    h = r.text
    proverka("у владельца через форму — блок и форма, ссылка на блок вверху страницы",
             r.status_code == 200 and "id='zapis-forma'" in h and "href='#zapis'" in h and "<h2>Запись занятий</h2>" in h)
    proverka("…включение, группы, камера, папка, логин и пароль приложения (поле ввода, без значения)",
             all(x in h for x in ("name='vkl'", "name='filtr'", "name='kamery'", "name='papka'", "name='yadisk_login'",
                                  "type='password' name='yadisk_password'", "name='moy_parol'")))
    proverka("…ближайшие занятия, последние записи, свободное место, кнопка «Пробная запись 2 минуты сейчас»",
             all(x in h for x in ("Ближайшие занятия", "Последние записи", "Свободно на сервере",
                                  "Пробная запись 2 минуты сейчас")))
    proverka("…инструкция: id.yandex.ru → Безопасность → Пароли приложений → «Файлы (WebDAV)»",
             all(x in h for x in ("id.yandex.ru", "«Безопасность» → «Пароли приложений»", "«Файлы (WebDAV)»")))
    proverka("…пароля приложения в странице нет — только «введён»", YA_PAROL not in h and "Пароль приложения: <b>введён</b>" in h)
    r = c.get("/owner/zapis", headers=STR)
    proverka("GET /owner/zapis (обновили страницу ответа) → 303 на /owner#zapis",
             r.status_code == 303 and r.headers.get("location") == "/owner#zapis", r.status_code)
    s = c.get("/api/zapis/sostoyanie", headers=FETCH)
    proverka("GET /api/zapis/sostoyanie владельцу → 200, пароля нет, «parol_zadan»: true",
             s.status_code == 200 and s.json()["parol_zadan"] is True and YA_PAROL not in s.text, s.status_code)

    # кто не может
    proverka("личный пароль админа задан", m.vhod.zadat_parol("lena", LENA) == "")
    lena = kl()
    voyti(lena, "lena", LENA, next_="/pult")
    proverka("админ (Лена через форму): /owner → 403", lena.get("/owner", headers=STR).status_code == 403)
    r = lena.post("/owner/zapis", data={"deystvie": "proba", "moy_parol": VLADELEC[1]}, headers=SVOI)
    proverka("…POST /owner/zapis → 403, даже с паролем владельца в поле", r.status_code == 403, r.status_code)
    proverka("…состояние записи → 403", lena.get("/api/zapis/sostoyanie", headers=FETCH).status_code == 403)
    r = kl().post("/owner/zapis", data={"deystvie": "proba", "moy_parol": VLADELEC[1]}, auth=OBSHCHIY_ADMIN)
    proverka("общий admin → 401 с окном, как на всех страницах владельца", r.status_code == 401, r.status_code)
    r = kl().post("/owner/zapis", data={"deystvie": "proba", "moy_parol": VLADELEC[1]}, auth=VLADELEC)
    proverka("прежний boris в сером окне (скрипт) → 403", r.status_code == 403, r.status_code)
    proverka("без входа → 401", kl().post("/owner/zapis", data={"deystvie": "proba"}).status_code == 401)
    proverka("без входа: состояние → 401", kl().get("/api/zapis/sostoyanie").status_code == 401)

    # подтверждение паролем
    n_zap = len(z.poslednie(100))
    r = zp(c, "sohranit", moy=None, vkl=None)
    proverka("без moy_parol → 400 «Введите ваш нынешний пароль», ничего не меняется",
             r.status_code == 400 and "Введите ваш нынешний пароль" in r.text and z.vklyucheno(), r.status_code)
    r = zp(c, "proba", moy="ne-tot-" + secrets.token_hex(4))
    proverka("неверный moy_parol → 400 «не подошёл», пробная запись не началась",
             r.status_code == 400 and "не подошёл" in r.text and len(z.poslednie(100)) == n_zap, r.status_code)
    r = zp(c, "proba", moy=OBSHCHIY_ADMIN[1])
    proverka("общий пароль admin подтверждением не служит", r.status_code == 400 and len(z.poslednie(100)) == n_zap)

    # отказы полей
    for opis, izm, frag in (("пустые группы", {"filtr": "  "}, "Группы: поле не может быть пустым"),
                            ("камера без кабинета", {"kamery": "Комната 1"}, "Камера:"),
                            ("папка с «..»", {"papka": "/Архив/../чужое"}, "Папка на Диске"),
                            ("логин с пробелом", {"yadisk_login": "hello @kidsup"}, "Логин Яндекса"),
                            ("короткий пароль приложения", {"yadisk_password": "abc"}, "Пароль приложения"),
                            ("S6: камера без id", {"kamery": "К1 = Комната 1"}, "нужен id камеры"),
                            ("S7: пароль приложения 15 букв", {"yadisk_password": "abcdefghijklmno"}, "16 латинских букв"),
                            ("S7: пароль приложения 17 букв", {"yadisk_password": "abcdefghijklmnopq"}, "16 латинских букв"),
                            ("S7: пароль с цифрами (похож на пароль почты)", {"yadisk_password": "Parol2026pochta!"},
                             "Обычный пароль от почты сюда не подходит"),
                            ("S7: пароль кириллицей", {"yadisk_password": "абвгдеёжзийклмно"}, "16 латинских букв")):
        do = (m.db.get_setting("zapis_filtr"), m.db.get_setting("zapis_kamery"), m.db.get_setting("zapis_papka"),
              m.db.get_setting("yadisk_login"), m.db.get_setting("yadisk_password"))
        r = zp(c, "sohranit", **izm)
        posle = (m.db.get_setting("zapis_filtr"), m.db.get_setting("zapis_kamery"), m.db.get_setting("zapis_papka"),
                 m.db.get_setting("yadisk_login"), m.db.get_setting("yadisk_password"))
        proverka(f"{opis} → 400 «{frag}…», ничего не изменено", r.status_code == 400 and frag in r.text
                 and "Ничего не изменено" in r.text and do == posle, r.status_code)

    # выключить — тик ничего не запускает; включить обратно
    r = zp(c, "sohranit", vkl=None)
    proverka("снять галочку «Записывать занятия» → «Сохранено: запись выключена», zapis_on=0",
             r.status_code == 200 and "запись выключена" in r.text and m.db.get_setting("zapis_on") == "0", r.status_code)
    postavit(datetime(2026, 11, 20, 9, 45))
    proverka("…в пятницу 09:45 тик ничего не запускает", z.tick() == [] and not zapis_po_uroku(7113))
    proverka("…в «Ближайших занятиях» — «запись выключена»",
             all(x["sostoyanie"] == "запись выключена" for x in z.blizhaishie()) and z.blizhaishie())
    r = zp(c, "sohranit", vkl="1", filtr="Музыка и речь;Группа 8", papka="Архив методики/Музыка и речь/")
    proverka("включить снова, два фильтра через «;»; та же папка без «/» в начале — не изменение",
             r.status_code == 200 and m.db.get_setting("zapis_on") == "1"
             and m.db.get_setting("zapis_filtr") == "Музыка и речь; Группа 8" and not m.db.get_setting("zapis_papka")
             and "Сохранено: запись включена, группы." in r.text, r.text[r.text.find("zapis-itog"):][:200])
    proverka("папка: «Архив методики/Музыка и речь/» → «/Архив методики/Музыка и речь»; «..», «:» и пусто — не годятся",
             z._papka_norm("Архив методики/Музыка и речь/") == "/Архив методики/Музыка и речь"
             and z._papka_norm("/a/../b") == z._papka_norm("C:/x") == z._papka_norm(" / ") == "")
    blizh = z.blizhaishie()
    proverka("«Ближайшие занятия» (пятница 09:45): 10:00 МиР Гр4 — будет записано; К2 индивидуальное — нет камеры; "
             "суббота 16:30 МиР Гр7",
             [(x["den"], x["begin"], x["gruppa"], x["pisat"]) for x in blizh][:3]
             == [("пт 20.11", "10:00", "МиР Гр4", True), ("пт 20.11", "15:00", "МиР группа 125", False),
                 ("сб 21.11", "16:30", "МиР Гр7", True)] and "камеры для записи нет" in blizh[1]["sostoyanie"],
             [(x["den"], x["begin"], x["gruppa"], x["sostoyanie"]) for x in blizh])

    # автопилот выключен — запись не идёт, и это видно
    m.db.set_setting("autopilot", "off")
    h = c.get("/owner", headers=STR).text
    blizh = z.blizhaishie()
    m.db.set_setting("autopilot", "on")
    proverka("m5: автопилот выключен — в блоке «Запись не будет: автопилот выключен», в ближайших — то же",
             "id='zapis-avtopilot'" in h and "Запись не будет: автопилот выключен" in h
             and "включена, но не будет: автопилот выключен" in h
             and blizh[0]["sostoyanie"] == "запись не будет: автопилот выключен", blizh[:1])
    h = c.get("/owner", headers=STR).text
    proverka("…автопилот включён — плашки нет, «будет записано»", "id='zapis-avtopilot'" not in h
             and z.blizhaishie()[0]["sostoyanie"].startswith("будет записано"))
    blizh7 = z.blizhaishie()
    proverka("m5: ближайшие — на 7 дней вперёд, а не 10 строк (пт 20.11 → по чт 26.11 включительно)",
             len(blizh7) > 10 and max(x["day"] for x in blizh7) == "2026-11-26"
             and all(x["day"] <= "2026-11-27" for x in blizh7), [(x["den"], x["begin"]) for x in blizh7][-3:])

    # метки формы: выдаёт сервер, без неё — отказ
    n_zap = len(z.poslednie(100))
    r = zp(c, "proba", metka=None)
    proverka("S5: форма без метки → 400 «Форма устарела», проба не началась",
             r.status_code == 400 and "Форма устарела" in r.text and len(z.poslednie(100)) == n_zap, r.status_code)
    r = zp(c, "proba", metka="vydumannaya-" + secrets.token_hex(6))
    proverka("S5: выдуманная метка → 400 «Форма устарела», проба не началась",
             r.status_code == 400 and "Форма устарела" in r.text and len(z.poslednie(100)) == n_zap, r.status_code)

    # пробная запись с /owner — метка из самой страницы
    n_v = len(vyzovy())
    postavit(datetime(2026, 11, 20, 18, 5))
    metka = re.search(r"name='metka' value='([^']+)'", c.get("/owner", headers=STR).text).group(1)
    r = c.post("/owner/zapis", data={"deystvie": "proba", "moy_parol": VLADELEC[1], "metka": metka}, headers=SVOI)
    kuda = "/Архив методики/Музыка и речь/2026-11/2026-11-20 18-05-00 Проба К1.mp4"
    proverka("«Пробная запись 2 минуты сейчас» → «Пробная запись началась: 2 минуты с камеры К1 («Комната 1»)»",
             r.status_code == 200 and "Пробная запись началась: 2 минуты с камеры К1 («Комната 1»)" in r.text, r.status_code)
    proverka("…записано 2 минуты и выгружено: «…/2026-11/2026-11-20 18-05 Проба К1.mp4» на Диске",
             len(vyzovy()) == n_v + 1 and arg(vyzovy()[-1], "-t") == "120" and kuda in DAV["files"] and not lokalnye())
    proverka("…в ответе «Последние записи» — строка «Проба», «выгружено на Диск»",
             "<span class='zp-g'>Проба</span>" in r.text and "выгружено на Диск" in r.text)
    r = c.post("/owner/zapis", data={"deystvie": "proba", "moy_parol": VLADELEC[1], "metka": metka}, headers=SVOI)
    proverka("та же форма ещё раз (обновили страницу) → «Уже сделано», вторая проба не началась",
             r.status_code == 200 and "Уже сделано" in r.text and len(vyzovy()) == n_v + 1, r.status_code)

    # проба: не чаще раза в 5 минут и по одной
    postavit(datetime(2026, 11, 20, 18, 8))
    r = zp(c, "proba")
    proverka("S5: новая проба через 3 минуты → 400 «не чаще раза в 5 минут — следующую можно после 18:10»",
             r.status_code == 400 and "не чаще раза в 5 минут" in r.text and "после 18:10" in r.text
             and len(vyzovy()) == n_v + 1, r.status_code)
    proverka("…то же при вызове proba() напрямую", z.proba().get("ok") is False)
    with osnovnaya() as o:
        z._init(o)
        zid_idet = o.execute("INSERT INTO zapis (key, lesson_id, day, begin_time, end_time, gruppa, komnata, kamera, status, "
                             "prichina, proba, sozdano, obnovleno) VALUES ('proba-stend-idet', NULL, '2026-11-20', "
                             "'17:00:00', '17:02', 'Проба', 'К1', 'Комната 1', 'pishetsya', '', 1, "
                             "'2026-11-20T17:00:00', '2026-11-20T17:00:00')").lastrowid
    postavit(datetime(2026, 11, 20, 18, 30))
    z._busy.add(zid_idet)
    r1 = z.proba()
    z._busy.discard(zid_idet)
    r2 = z.proba()
    with osnovnaya() as o:
        o.execute("UPDATE zapis SET status='oshibka' WHERE id=?", (zid_idet,))
    proverka("S5: пока идёт другая проба — отказ «уже идёт»; кончилась — проба начинается",
             r1.get("ok") is False and "уже идёт" in r1.get("error", "") and r2.get("ok") is True, (r1, r2.get("ok")))
    SSYLKI["zaderzhka"] = 40.0
    postavit(datetime(2026, 11, 20, 18, 40))
    zid = z.proba()["zapis_id"]
    SSYLKI["zaderzhka"] = 0.0
    a = vyzovy()[-1]
    ff = fayly(zid)
    proverka("m1: ссылка заняла 40 с — проба всё равно пишет 2 минуты от начала записи (-t 120, а не 80)",
             arg(a, "-t") == "120" and ff and ff[0]["nachalo"] == "2026-11-20T18:40:40", (arg(a, "-t"), ff[:1]))
    proverka("m2: имена проб — до секунды, разные", len({f["imya"] for x in z.poslednie(100) if x["proba"]
                                                          for f in fayly(x["id"])}) >= 3)
    r = zp(c, "vygruzit")
    proverka("«Выгрузить сейчас», когда ждать нечего → «Выгружать нечего»", r.status_code == 200 and "Выгружать нечего" in r.text)
    with osnovnaya() as o:
        zh = [tuple(x) for x in o.execute("SELECT imya, kod, chto FROM deystviya WHERE put='/owner/zapis' ORDER BY id")]
    proverka("журнал: действия блока записаны («запись занятий: …», кто и код), отказы — с пометкой «отказ»",
             any(x[0] == "Борис" and x[1] == 200 and x[2].startswith("запись занятий: ") for x in zh)
             and any(x[1] == 400 and x[2].endswith("— отказ") for x in zh)
             and any(x[0] == "Лена" and x[1] == 403 for x in zh), zh[-5:])
    proverka("журнал: пароля приложения и токена нет", not any(s_ in " ".join(map(str, x)) for x in zh for s_ in SEKRETY))
    return c


# --- З. Секреты не утекают ------------------------------------------------------------------------

def test_sekrety(c):
    print("З. Пароль приложения и токен потока: не в /api/settings, журнале, базе, пульте, HTML и выводе")
    shest = ("zapis_on", "zapis_filtr", "zapis_kamery", "zapis_papka", "yadisk_login", "yadisk_password")
    r = kl().get("/api/settings", auth=VLADELEC)
    j = r.json() if r.status_code == 200 else {}
    proverka("S1: GET /api/settings — настроек записи в нём нет (пароля приложения — тем более)",
             r.status_code == 200 and not set(shest) & set(j) and YA_PAROL not in r.text
             and not set(shest) & m.SETTABLE and "yadisk_password" in m.SECRET_KEYS, (r.status_code, set(shest) & set(j)))
    do = {k_: m.db.get_setting(k_) for k_ in shest}
    otvety = []
    for k_, v_ in (("zapis_on", "1"), ("zapis_filtr", "а"), ("zapis_kamery", "К1 = Комната 1 = abcd; К2 = Комната 2 = efgh"),
                   ("zapis_papka", "/chuzhoe"), ("yadisk_login", "chuzhoy@example.org"),
                   ("yadisk_password", "qwertyuiopasdfgh")):
        r = kl().post("/api/settings", json={"key": k_, "value": v_}, auth=VLADELEC, headers=FETCH)
        otvety.append((k_, r.status_code, "блок «Запись занятий»" in r.text))
    proverka("S1: POST /api/settings для шести настроек записи → 400 «…меняются на /owner, блок «Запись занятий»», "
             "ничего не изменено", all(kod == 400 and ok for _, kod, ok in otvety)
             and do == {k_: m.db.get_setting(k_) for k_ in shest}, otvety)
    r = kl().post("/api/settings", json={"key": "digest_off", "value": m.db.get_setting("digest_off") or ""},
                  auth=VLADELEC, headers=FETCH)
    proverka("…остальные настройки через /api/settings пишутся как раньше", r.status_code == 200, r.status_code)
    proverka("S3: ни в одной командной строке ffmpeg (запись и перепаковка) токена потока нет",
             vyzovy() and not any(TOKEN in " ".join(a_) for a_ in vyzovy() + remuksy()))
    stranicy = {a: c.get(a, headers=STR).text for a in ("/owner", "/journal")}
    stranicy["/api/zapis/sostoyanie"] = c.get("/api/zapis/sostoyanie", headers=FETCH).text
    stranicy["/pult (админ)"] = kl().get("/pult", auth=OBSHCHIY_ADMIN).text
    stranicy["/api/plan/inbox"] = kl().get(f"/api/plan/inbox?day={DEN}", auth=VLADELEC).text
    plohie = [(a, s_[:4]) for a, t in stranicy.items() for s_ in SEKRETY if s_ in t]
    proverka("ни пароля, ни токена в /owner, /journal, состоянии записи, пульте и /api/plan/inbox", not plohie
             and all(len(t) > 50 for t in stranicy.values()), [a for a, _ in plohie])
    with osnovnaya() as o:
        tablicy = [x[0] for x in o.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        gde = []
        for t in tablicy:
            for r_ in o.execute(f"SELECT * FROM {t}"):
                stroka = " ".join(str(x) for x in r_)
                if t == "settings" and r_[0] == "yadisk_password":
                    continue
                if any(s_ in stroka for s_ in SEKRETY):
                    gde.append(t)
    proverka("в основной базе пароль — только в настройке yadisk_password; токена нет нигде", not gde, sorted(set(gde)))
    plohie = [f.name for f in sorted(Path(m.config.VHODY_DB).parent.iterdir())
              if f.is_file() and f.name != "kidsup.db" and not f.name.startswith("kidsup.db")
              and any(s_.encode() in f.read_bytes() for s_ in SEKRETY)]
    proverka("ни в одном другом файле стенда (файл входов и др.) — ни пароля, ни токена", not plohie, plohie)
    lok = [p.name for p in z.DIR.rglob("*") if p.is_file() and any(s_.encode() in p.read_bytes() for s_ in SEKRETY)] \
        if z.DIR.exists() else []
    proverka("в папке записей (журналы ffmpeg) — тоже нет", not lok, lok)


# --- И. Ссылка на поток: подставной Playwright, общая блокировка с кадрами -------------------------

def test_ssylka():
    print("И. Ссылка на поток (ivideon.potok_ssylka): подставной браузер, общая с кадрами блокировка")
    from app import ivideon, kadry
    plohaya = ("https://openapi-alpha-eu01.ivideon.com/cameras/100-TYpMigSeczbQqK032w6QzZ:0/live_stream?op=GET"
               f"&access_token={TOKEN}&q=2&video_codecs=h264&audio_codecs=aac,mp3,pcma,pcmu,none&format=ws-fmp4&_=1731590000")
    proverka("format=ws-fmp4 → format=flv, остальное как было",
             ivideon._ssylka_flv(plohaya) == plohaya.replace("format=ws-fmp4", "format=flv"))
    proverka("чужой адрес, http и адрес без токена — не годятся",
             ivideon._ssylka_flv(plohaya.replace("ivideon.com", "example.com")) == ""
             and ivideon._ssylka_flv(plohaya.replace("https://", "http://")) == ""
             and ivideon._ssylka_flv(plohaya.replace("access_token", "x")) == "")
    S = {"zamok": [], "klik": [], "konteksty": 0, "zakryt": 0, "login": False}

    class Zapros:
        def __init__(self, url):
            self.url = url

    class Lokator:
        def __init__(self, pg, tekst, exact):
            self.pg, self.tekst, self.exact = pg, tekst, exact

        def count(self):
            return 1 if not self.exact or self.tekst == "Комната 1" else 0

        def nth(self, i):
            return self

        def click(self, timeout=0):
            S["klik"].append((self.tekst, self.exact))
            for fn in self.pg.obr:
                fn(Zapros("https://openapi-alpha-eu01.ivideon.com/cameras/100-CHUZHAYA:0/live_stream?op=GET"
                          f"&access_token={TOKEN}&format=ws-fmp4"))
                fn(Zapros(plohaya))

    class Stranica:
        def __init__(self):
            self.obr, self.url = [], ""

        def on(self, sob, fn):
            if sob == "request":
                self.obr.append(fn)

        def goto(self, url, **kw):
            self.url = "https://my.ivideon.com/service/login" if S["login"] else url

        def wait_for_timeout(self, ms):
            pass

        def fill(self, *a):
            pass

        def click(self, *a, **kw):
            pass

        def inner_text(self, sel):
            return "Подтвердите вход" if S["login"] else ""

        def get_by_text(self, tekst, exact=False):
            return Lokator(self, tekst, exact)

    class Kontekst:
        def new_page(self):
            return Stranica()

        def storage_state(self, path=None):
            Path(path).write_text("{}")

    class Brauzer:
        def new_context(self, **kw):
            S["konteksty"] += 1
            return Kontekst()

        def close(self):
            S["zakryt"] += 1

    class Chromium:
        def launch(self, **kw):
            S["zamok"].append(kadry._snap_lock.locked())
            return Brauzer()

    class PW:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    pw_mod = types.ModuleType("playwright")
    sapi = types.ModuleType("playwright.sync_api")
    sapi.sync_playwright = lambda: PW()
    bylo = {k_: sys.modules.get(k_) for k_ in ("playwright", "playwright.sync_api")}
    sys.modules["playwright"], sys.modules["playwright.sync_api"] = pw_mod, sapi
    bylo_login, bylo_st = ivideon.login_start, ivideon._state_path
    zvali_login = []
    ivideon.login_start = lambda code="": zvali_login.append(1) or {"ok": True}
    ivideon._state_path = lambda: Path(m.config.DATA_DIR) / "ivideon_state.json"   # не в data/ репозитория
    try:
        m.db.set_setting("ivideon_login", "stend@example.org")
        m.db.set_setting("ivideon_password", "stend-" + secrets.token_hex(4))
        r = ivideon.potok_ssylka("Комната 1", "100-TYpMigSeczbQqK032w6QzZ", zhdat_s=1)
        proverka("ссылка поймана из запроса плеера своей камеры (чужая камера отброшена), format=flv",
                 r.get("ok") and "100-TYpMigSeczbQqK032w6QzZ" in r["url"] and "format=flv" in r["url"]
                 and "CHUZHAYA" not in r["url"], {k_: v_ for k_, v_ in r.items() if k_ != "url"})
        proverka("клик — по тексту «Комната 1» (сначала точное совпадение); браузер закрыт",
                 S["klik"] == [("Комната 1", True)] and S["zakryt"] == 1, S["klik"])
        proverka("Chromium запускается только под блокировкой кадров (kadry._snap_lock)", S["zamok"] == [True], S["zamok"])
        # кадры держат браузер — ссылка ждёт
        kadry._snap_lock.acquire()
        gotovo = []
        t = threading.Thread(target=lambda: gotovo.append(ivideon.potok_ssylka("Комната 1", "", zhdat_s=1)), daemon=True)
        t.start()
        time.sleep(0.4)
        zhdala = not gotovo
        kadry._snap_lock.release()
        t.join(5)
        proverka("пока кадр фото-явки держит браузер, ссылка ждёт; отпустил — получила", zhdala and gotovo and gotovo[0]["ok"])
        S["login"] = True
        r = ivideon.potok_ssylka("Комната 1", "", zhdat_s=1)
        proverka("сессия Ivideon слетела и просит код → ошибка словами и запуск входа с кодом из письма (login_start)",
                 not r["ok"] and "код из письма" in r["error"] and zvali_login == [1], r)
        S["login"] = False
        m.db.set_setting("ivideon_password", "")
        r = ivideon.potok_ssylka("Комната 1", "")
        proverka("нет пароля Ivideon → отказ без браузера", not r["ok"] and "ivideon_password" in r["error"]
                 and S["zamok"] == [True, True, True], r)
    finally:
        ivideon.login_start, ivideon._state_path = bylo_login, bylo_st
        for k_, v_ in bylo.items():
            if v_ is None:
                sys.modules.pop(k_, None)
            else:
                sys.modules[k_] = v_
    proverka("zapis._poluchit_ssylku зовёт ivideon.potok_ssylka с названием и id камеры из настройки",
             "ivideon.potok_ssylka(kam.get(\"imya\")" in Path(z.__file__).read_text(encoding="utf-8"))


# --- К. Перезапуск сервера посреди записи ----------------------------------------------------------

def test_perezapusk():
    print("К. Перезапуск посреди записи: закрытая часть — в выгрузку, незакрытая — прочь; запись продолжается")
    postavit(datetime(2026, 11, 21, 16, 40))
    with osnovnaya() as o:
        z._init(o)
        o.execute("INSERT INTO zapis (key, lesson_id, day, begin_time, end_time, gruppa, komnata, kamera, status, "
                  "prichina, proba, sozdano, obnovleno) VALUES ('2026-11-21:7108', 7108, '2026-11-21', '16:30', '17:20', 'МиР Гр7', "
                  "'К1', 'Комната 1', 'pishetsya', '', 0, '2026-11-21T16:00:00', '2026-11-21T16:29:00')")
        zid = o.execute("SELECT id FROM zapis WHERE key='2026-11-21:7108'").fetchone()[0]
        for ch, imya, moov in ((1, "2026-11-21 16-30 МиР Гр7.mp4", True), (2, "2026-11-21 16-30 МиР Гр7 (часть 2).mp4", False)):
            p = z.DIR / "2026-11-21" / imya
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2")
                if moov:
                    f.write((16).to_bytes(4, "big") + b"moov" + b"\x00" * 8)
                f.write((8 + 200000).to_bytes(4, "big") + b"mdat" + b"\x00" * (200000 if moov else 1000))
            o.execute("INSERT INTO zapis_fayly (zapis_id, chast, fayl, imya, status, nachalo) VALUES (?,?,?,?,"
                      "'pishetsya', '2026-11-21T16:29:00')", (zid, ch, f"2026-11-21/{imya}", imya))
        o.commit()
    z._vosstanovleno[0] = False
    n0 = len(vyzovy())
    z.tick()
    ff = fayly(zid)
    proverka("после перезапуска: часть 1 (закрыта) — выгружена, часть 2 (без оглавления) — битая, удалена",
             [(f["chast"], f["status"]) for f in ff][:2] == [(1, "vygruzhen"), (2, "bityy")], [(f["chast"], f["status"]) for f in ff])
    proverka("запись продолжилась частью 2 с 16:40 до 17:21 (-t 2460) и тоже ушла на Диск",
             len(vyzovy()) == n0 + 1 and arg(vyzovy()[-1], "-t") == "2460"
             and ff[-1]["imya"] == "2026-11-21 16-30 МиР Гр7 (часть 2).mp4" and ff[-1]["status"] == "vygruzhen"
             and not lokalnye(), [(f["imya"], f["status"]) for f in ff])


# --- М. Занятие перенесли на другой день ------------------------------------------------------------

def test_perenos():
    print("М. Занятие перенесли на другой день, пока поток ждал: пропуск с причиной; в новый день — своя запись")
    bylo = z._spat

    def spat_i_perenesti(s):                       # пока поток ждёт, админ переносит занятие в МойКлассе
        with osnovnaya() as o:
            o.execute("UPDATE lessons SET date='2026-11-23' WHERE id=7115")
        z._spat = bylo
        bylo(s)
    z._spat = spat_i_perenesti
    n0 = len(vyzovy())
    postavit(datetime(2026, 11, 22, 10, 35))
    try:
        z.tick()
    finally:
        z._spat = bylo
    zap = zapis_po_uroku(7115)
    proverka("воскресенье: «занятие перенесено на пн 23.11 — запишется в тот день», ffmpeg не звали",
             zap.get("status") == "propusk" and "перенесено на пн 23.11" in (zap.get("prichina") or "")
             and zap.get("key") == "2026-11-22:7115" and len(vyzovy()) == n0, zap)
    postavit(datetime(2026, 11, 23, 10, 35))
    z.tick()
    with osnovnaya() as o:
        rows = [tuple(r) for r in o.execute("SELECT key, status FROM zapis WHERE lesson_id=7115 ORDER BY id")]
    proverka("понедельник: новая запись того же занятия, файл «2026-11-23 11-00 МиР Гр4.mp4» на Диске",
             rows == [("2026-11-22:7115", "propusk"), ("2026-11-23:7115", "vygruzheno")] and len(vyzovy()) == n0 + 1
             and "/Архив методики/Музыка и речь/2026-11/2026-11-23 11-00 МиР Гр4.mp4" in DAV["files"], rows)


# --- Л. Настоящий ffmpeg (если есть на машине) -----------------------------------------------------

def test_nastoyashchiy_ffmpeg(work: str):
    exe = shutil.which("ffmpeg")
    print("Л. Настоящий ffmpeg на локальном FLV (H.264 + звук): команда записи, обрыв, звук не для mp4")
    if not exe:
        print("  (пропущено: ffmpeg на этой машине не найден)")
        return
    d = Path(work) / "nastoyashchiy"
    d.mkdir(exist_ok=True)

    def sdelat(imya, zvuk):
        p = d / imya
        subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=8000", "-t", "8", "-c:v", "libx264",
                        "-preset", "ultrafast", "-g", "25", "-pix_fmt", "yuv420p", *zvuk, "-f", "flv", str(p)],
                       check=True, timeout=120)
        return p.read_bytes()
    try:
        FLV = {"/aac.flv": sdelat("aac.flv", ["-c:a", "aac", "-b:a", "32k"]),
               "/alaw.flv": sdelat("alaw.flv", ["-c:a", "pcm_alaw"])}
    except Exception as e:  # noqa: BLE001
        print(f"  (пропущено: тестовый FLV не собрался: {type(e).__name__})")
        return
    REZHIM = {"obryv": 0.0, "zavis": False}

    class Potok(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            put = urlsplit(self.path).path
            data = FLV.get(put)
            if data is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/x-flv")
            self.end_headers()                     # живой поток: без Content-Length
            n = int(len(data) * REZHIM["obryv"]) if REZHIM["obryv"] else len(data)
            try:
                self.wfile.write(data[:n])
                self.wfile.flush()
                if REZHIM["zavis"]:
                    time.sleep(25)
            except OSError:
                pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Potok)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    baza = f"http://127.0.0.1:{srv.server_address[1]}"
    proksi = {k_: os.environ.pop(k_) for k_ in list(os.environ) if k_.lower() in ("http_proxy", "https_proxy", "all_proxy")}
    bylo_exe, bylo_rw = z._ffmpeg_exe, z.RW_TIMEOUT_S
    z._ffmpeg_exe = lambda: exe
    try:
        def zapisat(put, imya, dlit):
            out = d / imya
            t0 = time.monotonic()
            rc, err = z._ffmpeg(f"{baza}{put}?access_token={TOKEN}&format=flv", out, dlit, 10 ** 9)
            return out, rc, err, time.monotonic() - t0

        def korobki(p):
            out, pos, b = [], 0, p.read_bytes()
            while pos + 8 <= len(b):
                n = int.from_bytes(b[pos:pos + 4], "big")
                out.append(b[pos + 4:pos + 8].decode("latin-1"))
                if n < 8:
                    break
                pos += n
            return out

        def kodeki(p):
            pr = shutil.which("ffprobe")
            if not pr:
                return None
            r = subprocess.run([pr, "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(p)],
                               capture_output=True, text=True, timeout=30)
            return [x.strip() for x in r.stdout.split() if x.strip()]

        out, rc, err, _ = zapisat("/aac.flv", "polnyy.mp4", 3)
        kb = korobki(out)
        proverka("целый поток, -t 3: код 0, mp4 закрыт (оглавление есть), +faststart — moov перед mdat",
                 rc == 0 and z.mp4_celyy(out) and "moov" in kb and kb.index("moov") < kb.index("mdat"), (rc, err, kb))
        kd = kodeki(out)
        proverka("…без перекодирования: внутри h264 и aac" if kd is not None else "…(ffprobe нет — кодеки не проверены)",
                 kd is None or kd == ["h264", "aac"], kd)
        REZHIM["obryv"] = 0.4
        out, rc, err, _ = zapisat("/aac.flv", "obryv.mp4", 30)
        proverka("поток оборвался на 40 %: ffmpeg вышел сам, файл закрыт и открывается (часть годится в архив)",
                 z.mp4_celyy(out) and out.stat().st_size > z.MIN_FAYL // 10, (rc, err, out.stat().st_size if out.exists() else 0))
        REZHIM["zavis"] = True
        z.RW_TIMEOUT_S = 2
        out, rc, err, sek = zapisat("/aac.flv", "zavis.mp4", 30)
        proverka("поток завис (сервер молчит 25 с): по -rw_timeout (2 с) ffmpeg закрыл файл за секунды, а не ждал",
                 z.mp4_celyy(out) and sek < 15, (rc, round(sek, 1)))
        REZHIM.update(obryv=0.0, zavis=False)
        z._zvuk_aac[0] = False
        out, rc, err, _ = zapisat("/alaw.flv", "alaw.mp4", 3)
        proverka("звук G.711 (pcma) mp4 как есть не берёт — ошибку узнаёт проверка из _pisat",
                 rc != 0 and z.ZVUK_NE_DLYA_MP4.search(err), (rc, err[-200:]))
        z._zvuk_aac[0] = True
        out, rc, err, _ = zapisat("/alaw.flv", "alaw2.mp4", 3)
        kd = kodeki(out)
        proverka("…со звуком в AAC (видео по-прежнему copy) — записалось", rc == 0 and z.mp4_celyy(out)
                 and (kd is None or kd == ["h264", "aac"]), (rc, err, kd))
        proverka("токен ссылки в хвост ошибки ffmpeg не попадает", TOKEN not in err)
    finally:
        z._zvuk_aac[0] = False
        z._ffmpeg_exe, z.RW_TIMEOUT_S = bylo_exe, bylo_rw
        os.environ.update(proksi)
        srv.shutdown()
        shutil.rmtree(d, ignore_errors=True)


# --- вывод и журнал --------------------------------------------------------------------------------

class _Tee:
    def __init__(self, kuda):
        self.kuda = kuda

    def write(self, s):
        ZAHVAT.append(s)
        return self.kuda.write(s)

    def flush(self):
        self.kuda.flush()


class _LogVZahvat(logging.Handler):
    def emit(self, record):
        try:
            ZAHVAT.append(self.format(record) + "\n")
        except Exception:  # noqa: BLE001
            pass


def main():
    global m, z, PODSTAVA
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--work", default="")
    a = ap_.parse_args()
    sys.stdout = _Tee(sys.stdout)
    work = a.work or tempfile.mkdtemp(prefix="zapis_")
    os.makedirs(work, exist_ok=True)
    m = lt.zapustit(KOREN, work)
    _otkryt_lokalnyy()
    lt.zasejat(os.path.join(work, "kidsup.db"))
    zasejat(os.path.join(work, "kidsup.db"))
    m.vhod.zapusk(True)
    m.config.APP_USER, m.config.APP_PASSWORD = OBSHCHIY_ADMIN
    m.config.BEZ_VHODA = False
    m.db.set_setting("owner_password", VLADELEC[1])
    import app
    for mod in pkgutil.iter_modules(app.__path__):
        importlib.import_module(f"app.{mod.name}")
    lt._postavit_lovushki()
    from app import autopilot as ap, zapis
    z = zapis
    zh = _LogVZahvat()
    zh.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(zh)
    logging.getLogger("kidsup.zapis").setLevel(logging.INFO)

    PODSTAVA = Path(work) / "podstava"
    PODSTAVA.mkdir(exist_ok=True)
    os.environ["ZAPIS_PODSTAVA"] = str(PODSTAVA)
    ff = PODSTAVA / "ffmpeg"
    ff.write_text(FFMPEG)
    ff.chmod(0o755)
    ap._now = lambda: chasy().replace(tzinfo=MSK)
    z._seychas = chasy
    z._spat = spat
    z._potok = lambda fn, *args: fn(*args)            # потоки — по очереди, в этом же потоке
    z._ffmpeg_exe = lambda: str(ff)
    z._poluchit_ssylku = ssylka_stenda
    z._disk_usage = lambda: Usage(20 * GB, 5 * GB, 15 * GB)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Dav)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    z.WEBDAV = f"http://127.0.0.1:{srv.server_address[1]}"
    DAV["auth"] = dav_avt(YA_LOGIN, YA_PAROL)
    m.db.set_setting("yadisk_login", YA_LOGIN)
    m.db.set_setting("yadisk_password", YA_PAROL)
    t0 = time.time()

    proverka("подставной WebDAV — на 127.0.0.1, настоящий адрес Диска в коде — webdav.yandex.ru",
             z.WEBDAV.startswith("http://127.0.0.1:") and 'WEBDAV = "https://webdav.yandex.ru"'
             in Path(z.__file__).read_text(encoding="utf-8"))
    test_vybor()
    test_vremya()
    test_obryv()
    test_vygruzka()
    c = test_owner()
    test_bez_parolya(c)
    test_mesto()
    test_ssylka()
    test_perezapusk()
    test_perenos()
    test_sekrety(c)
    test_nastoyashchiy_ffmpeg(work)

    proverka("ни одной отправки клиентам", not lt.LOVUSHKI, lt.LOVUSHKI)
    chuzhie = [x for x in lt.SET if any("zapis" in f or "ivideon" in f for f in x[1])]
    proverka("запись и Ivideon не ходили в сеть мимо 127.0.0.1", not chuzhie, chuzhie)
    srv.shutdown()
    vyvod = "".join(ZAHVAT)
    proverka("в выводе тестов и журнале приложения нет ни пароля приложения, ни токена потока",
             not any(s_ in vyvod for s_ in SEKRETY) and "запись выгружена на Диск" in vyvod)
    print(f"\nИтого: {lt.proshlo} OK, {lt.oshibok} ошибок · стенд {work} · {time.time() - t0:.0f} с")
    sys.exit(1 if lt.oshibok else 0)


if __name__ == "__main__":
    main()
