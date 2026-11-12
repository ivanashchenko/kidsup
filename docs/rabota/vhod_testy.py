# -*- coding: utf-8 -*-
"""Личные входы на app.kidsup.ru (app/vhod.py) — проверки группы А «вход», на стенде,
без сервера и без сети.

12.11.2026, Борис: «да» личным логинам админов. Каждый входит под своим именем на
странице /vhod, сервер знает, кто сделал действие. Главное требование выкатки: в момент
выкладки для людей и рутин не меняется НИЧЕГО — общий admin и прежний пароль владельца в
сером окне браузера работают как раньше, новое включается только действиями владельца.

Здесь зафиксировано (план, раздел 9.1, проверки 1–20): «как сегодня» до заведения входов;
обход всех адресов; форма входа, метка и выход; сроки; общий компьютер; подделка запросов;
перебор; раскладка; куда возвращать после входа; публичные домены; ключ педагога; режим без
входа; сбой файла входов; заголовки; vhod.js на страницах меню; автоматика сервера.

    python3 docs/rabota/vhod_testy.py [--work ПАПКА]

Обвязка — из liniya_testy.py: сеть закрыта, отправки — ловушки, автопилот не стартует,
данные выдуманные. Пароли и ключи придумываются на ходу, живут только в памяти прогона и
не печатаются: в сообщения об ошибках идут только коды ответов и признаки.
"""
from __future__ import annotations

import argparse
import base64
import importlib
import importlib.util
import os
import pkgutil
import re
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
import types
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, "/home/user/kidsup")
KOREN = sys.path[0]

_spec = importlib.util.spec_from_file_location("liniya_testy", os.path.join(KOREN, "docs", "rabota", "liniya_testy.py"))
lt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lt)
proverka = lt.proverka

B = "https://app.kidsup.ru"
UA = "Mozilla/5.0 (stend vhod_testy)"
STR = {"accept": "text/html,application/xhtml+xml", "sec-fetch-mode": "navigate", "sec-fetch-site": "none",
       "sec-fetch-dest": "document", "user-agent": UA}                      # человек открывает страницу
FETCH = {"accept": "application/json", "sec-fetch-mode": "cors", "sec-fetch-site": "same-origin",
         "user-agent": UA}                                                  # запрос со страницы
SVOI = {"origin": B, "sec-fetch-site": "same-origin", "sec-fetch-mode": "navigate", "user-agent": UA}
CHUZH_FON = {"sec-fetch-site": "cross-site", "sec-fetch-mode": "cors", "user-agent": UA}

# Открытые без входа адреса — снимок кода до личных входов (12.11.2026, 42 адреса: сайт,
# вебхуки, ссылки родителям). Список заморожен: новый открытый адрес или закрытый вебхук
# ломают тест. Плюс три адреса самого входа.
OTKRYTYE_DO = {
    "GET /", "GET /api/health", "GET /api/net", "GET /api/public/schedule", "GET /boris", "GET /day",
    "GET /english", "GET /english/metodika", "GET /favicon.ico", "GET /go/{channel}", "GET /izo", "GET /kamery",
    "GET /konkurs", "GET /lk/{token}", "GET /minisad", "GET /nulevoy", "GET /pedagog", "GET /podgotovka",
    "GET /podgotovka/metodika", "GET /price.yml", "GET /robototehnika", "GET /robototehnika/pedagog",
    "GET /robots.txt", "GET /shahmaty", "GET /site", "GET /sitemap.xml", "GET /v/{name}", "GET /vstrechi",
    "GET /wazzup/webhook", "GET /week", "OPTIONS /api/public/lead", "POST /api/pedagog/save",
    "POST /api/public/lead", "POST /api/tg/webhook/{secret}", "POST /events/sms", "POST /events/{rest:path}",
    "POST /hook/lead", "POST /kamery", "POST /mango/events", "POST /mango/{rest:path}", "POST /marquiz/webhook",
    "POST /wazzup/webhook"}
NOVYE_OTKRYTYE = {"GET /vhod", "POST /vhod", "POST /vyhod"}
PROVERKA_VNUTRI = {("GET", "/"), ("GET", "/boris")}     # без зависимости, check_auth зовут сами

# Выдуманные пароли стенда — придуманы на ходу.
OBSHCHIY = ("admin", "stend-" + secrets.token_urlsafe(10))          # старый общий вход
STARYY_VLADELCA = ("boris", "stend-" + secrets.token_urlsafe(10))   # прежний пароль владельца
P: dict = {}                                                        # личные пароли
PED = "stend-" + secrets.token_urlsafe(8)                           # ключ педагога
m = None


def pridumat() -> str:
    """Как кнопка «Придумать»: четыре слова из слогов и две цифры, строчная латиница."""
    while True:
        sl = [secrets.choice("bdfgklmnprstvz") + secrets.choice("aeiou") for _ in range(8)]
        p = "-".join(sl[i] + sl[i + 1] for i in range(0, 8, 2)) + "-" + str(10 + secrets.randbelow(90))
        if not m.vhod.parol_goden(p, "x"):
            return p


def kl(adres: str | None = None, **kw):
    from fastapi.testclient import TestClient
    if adres:     # запрос «через Caddy»: адрес посетителя — последний в X-Forwarded-For
        return TestClient(m.app, base_url=B, follow_redirects=False, client=("127.0.0.1", 50000),
                          headers={"x-forwarded-for": adres}, **kw)
    return TestClient(m.app, base_url=B, follow_redirects=False, **kw)


def voyti(c, login, parol, zapomnit=False, next_="/pult", hdr=None):
    data = {"login": login, "parol": parol, "next": next_}
    if zapomnit:
        data["zapomnit"] = "1"
    return c.post("/vhod", data=data, headers={**SVOI, **(hdr or {})})


def vyyti(c):
    return c.post("/vyhod", headers=SVOI)


def kto(c, **kw):
    r = c.get("/api/kto", **kw)
    return r.json() if r.status_code == 200 else r.status_code


def login_kto(c, **kw):
    k = kto(c, **kw)
    return k.get("login") if isinstance(k, dict) else k


def metki(r) -> dict:
    """Set-Cookie ответа: имя → вся строка (значения не печатаем)."""
    return {s.split("=", 1)[0]: s for s in r.headers.get_list("set-cookie")}


def kopiya_metki(c, imya=None):
    """Новый «браузер» с копией метки входа (или метки устройства) из c."""
    imya = imya or m.vhod.COOKIE
    d = kl()
    d.cookies.set(imya, c.cookies.get(imya), domain="app.kidsup.ru")
    return d


def chasy(t: float | None):
    m.vhod.SDVIG = 0.0 if t is None else t - time.time()


def msk(*a) -> float:
    return datetime(*a, tzinfo=m.vhod.MSK).timestamp()


def baza_vhodov():
    con = sqlite3.connect(m.config.VHODY_DB)
    con.row_factory = sqlite3.Row
    return con


def osnovnaya():
    con = sqlite3.connect(m.config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def zashchishchennye():
    """[(метод, путь)] маршрутов, у которых среди зависимостей есть проверка входа."""
    zashchita = {m.check_auth, m.check_owner, m.check_pedagog_or_admin}

    def vyzovy(d, acc):
        for s in d.dependencies:
            acc.add(s.call)
            vyzovy(s, acc)
        return acc
    otkr, zashch = set(), []
    for r in m.app.routes:
        if not hasattr(r, "dependant"):
            continue
        calls = vyzovy(r.dependant, set())
        for met in sorted(r.methods - {"HEAD"}):
            if calls & zashchita:
                zashch.append((met, r.path))
            else:
                otkr.add(f"{met} {r.path}")
    return otkr, zashch


def adres_puti(put: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", put)


# --- 1. Как сегодня ---------------------------------------------------------------------

def test_kak_segodnya():
    print("1. Как сегодня: входы ещё не заводились — старые входы ровно как раньше")
    proverka("отметки vhod_zavedeny нет, старые входы включены",
             not m.vhod.zavedeny() and m.vhod.starye_vklyucheny())
    with baza_vhodov() as b:
        zapisi = {r["login"]: r["hesh"] for r in b.execute("SELECT login, hesh FROM vhody")}
    proverka("шесть записей заведены сами, ни у одной нет пароля или ключа",
             set(zapisi) == {"lena", "ira", "liza", "boris", "klod", "klod-vykladka"} and not any(zapisi.values()),
             sorted(zapisi))
    c = kl()
    r = c.get("/api/pult/tasks", auth=OBSHCHIY)
    proverka("общий admin проходит AUTH", r.status_code == 200, r.status_code)
    r = c.get("/api/settings", auth=OBSHCHIY)
    proverka("общий admin на OWNER_AUTH → 401 с окном пароля, как раньше",
             (r.status_code, r.headers.get("www-authenticate")) == (401, "Basic"), r.status_code)
    r1, r2 = c.get("/api/pult/tasks", auth=STARYY_VLADELCA), c.get("/api/settings", auth=STARYY_VLADELCA)
    proverka("прежний boris проходит и AUTH, и OWNER_AUTH", (r1.status_code, r2.status_code) == (200, 200),
             (r1.status_code, r2.status_code))
    r = kl().get("/api/pult/tasks")
    proverka("без входа (скрипт) → 401 с окном Basic", (r.status_code, r.headers.get("www-authenticate")) == (401, "Basic"),
             r.status_code)
    r = kl().get("/liniya", headers=STR)
    proverka("без входа (браузер, через форму не входили) → 401 с окном Basic, а не форма",
             (r.status_code, r.headers.get("www-authenticate"), r.headers.get("location")) == (401, "Basic", None),
             r.status_code)
    r = kl().get("/api/pult/tasks", auth=("admin", "ne-tot-" + secrets.token_hex(4)))
    proverka("неверный пароль → 401", r.status_code == 401, r.status_code)
    proverka("/api/kto под общим admin → admin, способ «старый»",
             kto(kl(), auth=OBSHCHIY) == {"login": "admin", "imya": "", "rol": "admin", "sposob": "staryy"},
             kto(kl(), auth=OBSHCHIY))
    proverka("/api/kto под прежним boris → владелец, способ «старый»",
             kto(kl(), auth=STARYY_VLADELCA) == {"login": "boris", "imya": "Борис", "rol": "owner", "sposob": "staryy"})
    r = kl().get("/liniya", headers=STR, auth=OBSHCHIY)
    proverka("страница под старым входом — 200 и без единой метки", r.status_code == 200 and not metki(r),
             (r.status_code, sorted(metki(r))))
    proverka("корень: admin → /pult, boris → его стартовая",
             (kl().get("/", auth=OBSHCHIY).headers.get("location"),
              kl().get("/", auth=STARYY_VLADELCA).headers.get("location", "").startswith("/boris/docs"))
             == ("/pult", True))
    proverka("переход /boris: admin → /pult, boris → /boris/docs",
             (kl().get("/boris", auth=OBSHCHIY).headers.get("location"),
              kl().get("/boris", auth=STARYY_VLADELCA).headers.get("location")) == ("/pult", "/boris/docs"))
    r = kl().post("/api/plan/inbox", json={"who": "Лена", "text": "проверка входа: старый admin"}, auth=OBSHCHIY)
    proverka("изменяющий запрос скрипта под старым admin без Origin — 200, как раньше", r.status_code == 200,
             r.status_code)
    proverka("прямой вызов check_auth(credentials) без request — как раньше",
             m.check_auth(_cred(*OBSHCHIY)) is None and _kod(lambda: m.check_auth(_cred("admin", "x"))) == 401)
    with baza_vhodov() as b:
        hvost = {(r["login"], r["chem"]) for r in b.execute("SELECT login, chem FROM hvost")}
    proverka("хвост общего входа: кто ходит старым входом — браузером и скриптом",
             {("admin", "скрипт"), ("admin", "браузер"), ("boris", "скрипт")} <= hvost, sorted(hvost))
    with osnovnaya() as o:
        zh = o.execute("SELECT imya, sposob, metod, kod FROM deystviya WHERE put='/api/plan/inbox' "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    proverka("изменяющий запрос записан в журнал: «общий вход», способ «старый»",
             zh is not None and tuple(zh) == ("общий вход", "staryy", "POST", 200), zh and tuple(zh))


def _cred(u, p):
    from fastapi.security import HTTPBasicCredentials
    return HTTPBasicCredentials(username=u, password=p)


def _kod(f):
    from fastapi import HTTPException
    try:
        f()
    except HTTPException as e:
        return e.status_code
    return 200


# --- 2. Обход всех адресов -----------------------------------------------------------------

def test_obhod():
    print("2. Обход всех адресов: открытые заморожены, защищённые без входа — 401, никогда 200")
    otkr, zashch = zashchishchennye()
    zhdem = OTKRYTYE_DO | NOVYE_OTKRYTYE
    proverka(f"открытых адресов {len(zhdem)}: прежние 42 (сайт, вебхуки) и три адреса входа — и только они",
             otkr == zhdem, sorted(otkr ^ zhdem))
    proverka("защищённых адресов не меньше 420", len(zashch) >= 420, len(zashch))
    c = kl(raise_server_exceptions=False)
    plohie = []
    for met, put in zashch + sorted(PROVERKA_VNUTRI):
        r = c.request(met, adres_puti(put))
        if r.status_code != 401:
            plohie.append((met, put, r.status_code))
    proverka(f"{len(zashch) + 2} защищённых адресов без входа (скрипт) → 401", not plohie, plohie[:8])


# --- страница входа ------------------------------------------------------------------------

def test_stranica():
    print("4а. Страница входа /vhod")
    r = kl().get("/vhod", headers=STR)
    t = r.text
    proverka("открывается без входа", r.status_code == 200, r.status_code)
    proverka("логин, пароль, «глаз», подсказка про Caps Lock, «Запомнить», кнопки имён",
             all(x in t for x in ('name=login', 'type=password', 'id=glaz', 'Caps Lock', 'name=zapomnit',
                                  'id=imena', 'autocomplete=current-password', 'action="/vhod"')))
    proverka("галочка подписана «только на своём телефоне»", "только на своём телефоне" in t)
    proverka("vhod.js на месте и отдаётся", kl().get("/static/vhod.js").status_code == 200
             and "X-Vhod" in kl().get("/static/vhod.js").text)
    c = kl()
    voyti(c, "lena", P["lena"])
    t = c.get("/vhod?next=/liniya", headers=STR).text
    proverka("уже вошедшему: «Вы вошли как Лена · Продолжить · Выйти / сменить»",
             "Вы вошли как <b>Лена</b>" in t and "Продолжить" in t and "href='/liniya'" in t
             and "action='/vyhod'" in t and "Выйти / сменить" in t)
    r = kl().post("/vhod", data={"login": "lena", "parol": "ne-tot-" + secrets.token_hex(4)}, headers=SVOI)
    proverka("неверный пароль: 401, «Логин или пароль не подошли», подсказка про раскладку, логин остался",
             r.status_code == 401 and "не подошли" in r.text and "раскладку" in r.text and 'value="lena"' in r.text
             and not metki(r), r.status_code)
    r2 = kl().post("/vhod", data={"login": "nikto-" + secrets.token_hex(3), "parol": "ne-tot"}, headers=SVOI)
    proverka("незнакомый логин — тот же ответ, что и неверный пароль",
             r2.status_code == 401 and "не подошли" in r2.text and "раскладку" in r2.text)
    r = kl().post("/vhod", data={"login": "klod", "parol": "lyuboy-" + secrets.token_hex(4)}, headers=SVOI)
    proverka("служебной записью через форму не войти (тот же ответ)", r.status_code == 401 and not metki(r))


# --- 3. Без входа ---------------------------------------------------------------------------

def test_bez_vhoda():
    print("3. Без входа: страница — на форму, запрос страницы — 401 с X-Vhod, скрипт — окно пароля")
    c = kl()
    voyti(c, "lena", P["lena"])
    vyyti(c)                                    # браузер уже входил через форму: метка устройства есть
    put = f"/liniya?phone={lt.SEMYA}"
    r = c.get(put, headers=STR)
    loc = r.headers.get("location") or ""
    proverka("страница → 303 на /vhod?next=…", r.status_code == 303 and loc.startswith("/vhod?next="),
             (r.status_code, loc[:40]))
    proverka("в next сохранён путь с запросом", unquote(loc.split("next=", 1)[-1]) == put)
    r = c.get(put, headers=STR, auth=OBSHCHIY)
    proverka("…даже если браузер прислал старый общий пароль", r.status_code == 303 and
             (r.headers.get("location") or "").startswith("/vhod"), r.status_code)
    r = c.get("/api/pult/tasks", headers=FETCH)
    proverka("запрос со страницы → 401 с X-Vhod и без окна пароля",
             (r.status_code, r.headers.get("x-vhod"), "www-authenticate" in r.headers) == (401, "nuzhen", False),
             r.status_code)
    r = kl().get("/api/pult/tasks")
    proverka("скрипт → 401 с окном Basic и без X-Vhod",
             (r.status_code, r.headers.get("www-authenticate"), r.headers.get("x-vhod")) == (401, "Basic", None))
    r = c.get(loc, headers=STR)
    proverka("форма открывается и помнит, куда вернуть", r.status_code == 200 and f'value="{put}"' in r.text.replace("&amp;", "&"))
    r = voyti(c, "lena", P["lena"], next_=put)
    proverka("после входа — обратно на ту же страницу с запросом", (r.status_code, r.headers.get("location")) == (303, put),
             (r.status_code, r.headers.get("location")))
    vyyti(c)
    _, zashch = zashchishchennye()
    plohie = []
    d = kl(raise_server_exceptions=False)
    d.cookies.set(m.vhod.COOKIE_USTR, c.cookies.get(m.vhod.COOKIE_USTR), domain="app.kidsup.ru")
    for met, p in zashch:
        if met != "GET":
            continue
        r = d.get(adres_puti(p), headers=STR)
        stranica = not p.startswith(("/api/", "/export/"))
        zhdem = 303 if stranica else 401
        if r.status_code != zhdem or (stranica and not (r.headers.get("location") or "").startswith("/vhod?next=")):
            plohie.append((p, r.status_code))
    proverka("все защищённые GET в браузере без входа: страницы → 303 на форму, API → 401, ни одного 200",
             not plohie, plohie[:8])


# --- 4. Метка ------------------------------------------------------------------------------

def test_metka():
    print("4. Метка входа и метка устройства")
    c = kl()
    r = voyti(c, "lena", P["lena"])
    mt = metki(r)
    v, u = mt.get(m.vhod.COOKIE, ""), mt.get(m.vhod.COOKIE_USTR, "")
    for imya, s in (("метка входа", v), ("метка устройства", u)):
        sl = s.lower()
        proverka(f"{imya}: HttpOnly, Secure, SameSite=Lax, Path=/, без Domain",
                 bool(s) and "httponly" in sl and "secure" in sl and "samesite=lax" in sl and "path=/" in sl
                 and "domain" not in sl)
    proverka("метка входа без «Запомнить» — до закрытия браузера (без Max-Age)", "max-age" not in v.lower())
    proverka("метка устройства — на два года", "max-age=63072000" in u.lower())
    znach = c.cookies.get(m.vhod.COOKIE) or ""
    proverka("в метке нет пароля, логина и имени; это случайный номер",
             len(znach) >= 40 and P["lena"] not in v and "lena" not in znach.lower() and "Лена" not in v)
    with baza_vhodov() as b:
        est = b.execute("SELECT COUNT(*) FROM seansy WHERE id=?", (m.vhod._sha(znach),)).fetchone()[0]
    proverka("в базе — SHA-256 номера, сам номер в файле не лежит",
             est == 1 and znach.encode() not in Path(m.config.VHODY_DB).read_bytes())
    proverka("вход записан в журнал: Лена, форма, «вход через форму»",
             _posl_zhurnal("/vhod") == ("lena", "Лена", "forma", "POST", 303, "вход через форму"), _posl_zhurnal("/vhod"))
    proverka("/api/kto: Лена, админ, способ «форма»",
             kto(c) == {"login": "lena", "imya": "Лена", "rol": "admin", "sposob": "forma"})


# --- 5. Логин по-русски ------------------------------------------------------------------------

def test_login_po_russki():
    print("5. Вход по «Лена», «лена», «LENA», «lena» — один и тот же человек")
    for vvod in ("Лена", "лена", "LENA", "lena", "  ЛЕНА "):
        c = kl()
        r = voyti(c, vvod, P["lena"])
        proverka(f"«{vvod.strip()}» → вход, и это lena", r.status_code == 303 and login_kto(c) == "lena",
                 (r.status_code, login_kto(c)))


# --- 6. Выход и отзыв -------------------------------------------------------------------------

def test_vyhod():
    print("6. Выход настоящий: скопированная метка после выхода не действует")
    c = kl()
    voyti(c, "lena", P["lena"])
    d = kopiya_metki(c)
    proverka("копия метки до выхода — работает", login_kto(d) == "lena")
    r = vyyti(c)
    proverka("«Выйти» → 303 на /vhod и метка стёрта в браузере",
             r.status_code == 303 and r.headers.get("location", "").startswith("/vhod")
             and "max-age=0" in metki(r).get(m.vhod.COOKIE, "").lower(), r.status_code)
    proverka("копия метки после выхода → 401", login_kto(d) == 401)
    a, b = kl(), kl()
    voyti(a, "lena", P["lena"])
    voyti(b, "lena", P["lena"])
    m.vhod.seansy_zakryt("lena")
    proverka("«Выйти везде»: оба браузера Лены — без входа", (login_kto(a), login_kto(b)) == (401, 401))
    voyti(a, "lena", P["lena"])
    novyy = pridumat()
    proverka("новый пароль задан", m.vhod.zadat_parol("lena", novyy) == "")
    staryy, P["lena"] = P["lena"], novyy
    proverka("новый пароль: прежние сеансы — без входа", login_kto(a) == 401)
    proverka("прежний пароль больше не подходит, новый — подходит",
             (voyti(kl(), "lena", staryy).status_code, voyti(kl(), "lena", novyy).status_code) == (401, 303))
    voyti(a, "lena", P["lena"])
    proverka("«Отключить вход»", m.vhod.vklyuchit("lena", False) == "")
    proverka("…сеанс сразу без входа, войти нельзя", (login_kto(a), voyti(kl(), "lena", P["lena"]).status_code) == (401, 401))
    proverka("«Включить» возвращает вход", m.vhod.vklyuchit("lena", True) == "" and
             voyti(kl(), "lena", P["lena"]).status_code == 303)
    proverka("вход владельца отключить нельзя", m.vhod.vklyuchit("boris", False) != "")
    m.vhod.snyat_pauzu("lena")


# --- 7. Сроки -----------------------------------------------------------------------------

def test_sroki():
    print("7. Сроки (часы подменены): без «Запомнить» — до 4:00; с ним — 30 дней, предел 90")
    T0 = msk(2026, 11, 13, 10, 0)
    try:
        chasy(T0)
        c = kl()
        voyti(c, "ira", P["ira"])
        chasy(T0 + 17 * 3600 + 59 * 60)
        proverka("без «Запомнить»: в 03:59 ночи вход ещё есть", login_kto(c) == "ira")
        chasy(T0 + 18 * 3600 + 60)
        proverka("…в 04:01 — уже нет", login_kto(c) == 401)
        chasy(T0)
        c = kl()
        r = voyti(c, "ira", P["ira"], zapomnit=True)
        proverka("с «Запомнить» метка на 30 дней", "max-age=2592000" in metki(r).get(m.vhod.COOKIE, "").lower())
        chasy(T0 + 3600)
        r = c.get("/api/kto")
        proverka("через час — вход есть, метку не продлеваем (не чаще раза в сутки)",
                 r.status_code == 200 and m.vhod.COOKIE not in metki(r))
        chasy(T0 + 2 * 86400)
        r = c.get("/api/kto")
        ma = re.search(r"max-age=(\d+)", metki(r).get(m.vhod.COOKIE, "").lower())
        proverka("через 2 дня — вход есть и метка продлена на 30 дней",
                 r.status_code == 200 and ma is not None and abs(int(ma.group(1)) - 2592000) < 120)
        chasy(T0 + 2 * 86400 + 31 * 86400)
        proverka("31 день без захода — вход кончился", login_kto(c) == 401)
        T1 = T0 + 100 * 86400
        chasy(T1)
        c = kl()
        voyti(c, "ira", P["ira"], zapomnit=True)
        zhivoy = True
        for den in (20, 40, 60, 80, 89):
            chasy(T1 + den * 86400)
            zhivoy = zhivoy and login_kto(c) == "ira"
        proverka("заходит каждые 20 дней — вход живёт до 89-го дня", zhivoy)
        chasy(T1 + 90 * 86400 + 60)
        proverka("предел 90 дней от входа — дальше только новым входом", login_kto(c) == 401)
    finally:
        chasy(None)


# --- 8. Общий компьютер --------------------------------------------------------------------------

def test_obshchiy():
    print("8. Общий компьютер: второй человек на том же браузере")
    T0 = msk(2026, 11, 20, 10, 0)
    try:
        chasy(T0)
        z = kl()
        voyti(z, "lena", P["lena"], zapomnit=True)
        voyti(z, "Лена", P["lena"], zapomnit=True)
        proverka("тот же человек второй раз — устройство не общее", not m.vhod.ustroystvo_obshchee(z))
        x = kl()
        voyti(x, "lena", P["lena"], zapomnit=True)
        lena = kopiya_metki(x)
        y = kopiya_metki(x, m.vhod.COOKIE_USTR)
        proverka("пока входила одна Лена — галочка «Запомнить» есть", "name=zapomnit" in y.get("/vhod").text)
        r = voyti(x, "ira", P["ira"], zapomnit=True)
        proverka("Ира на том же браузере: вход есть, но не запомнен (метка без срока)",
                 r.status_code == 303 and "max-age" not in metki(r).get(m.vhod.COOKIE, "").lower(), r.status_code)
        proverka("…галочки «Запомнить» на этом браузере больше нет", "name=zapomnit" not in y.get("/vhod").text)
        chasy(T0 + 17 * 3600 + 59 * 60)
        proverka("запомненный сеанс Лены живёт только до 4:00: в 03:59 ещё есть", login_kto(lena) == "lena")
        chasy(T0 + 18 * 3600 + 60)
        proverka("…в 04:01 — нет (хотя запоминала на 30 дней); сеанс Иры — тоже",
                 (login_kto(lena), login_kto(x)) == (401, 401))
    finally:
        chasy(None)


# --- 9. Метка и старый пароль вместе ------------------------------------------------------------

def test_metka_i_staryy():
    print("9. Метка Лены и старый общий пароль в одном запросе")
    c = kl(raise_server_exceptions=False)
    voyti(c, "lena", P["lena"])
    proverka("метка + общий admin в окне → Лена", login_kto(c, auth=OBSHCHIY) == "lena")
    r = c.post("/api/plan/inbox", json={"who": "Лена", "text": "проверка входа: метка и окно"},
               headers=FETCH, auth=OBSHCHIY)
    proverka("изменяющий запрос с меткой и окном — 200 и подписан Леной", r.status_code == 200
             and _posl_zhurnal("/api/plan/inbox")[:2] == ("lena", "Лена"), r.status_code)
    krivoy_1 = {"authorization": "Basic " + base64.b64encode("admin:пароль".encode("cp1251")).decode()}
    krivoy_2 = {"authorization": "Basic пароль-по-русски".encode("utf-8")}
    for imya, h in (("байты не UTF-8", krivoy_1), ("русские буквы прямо в заголовке", krivoy_2)):
        r = c.get("/api/kto", headers=h)
        proverka(f"кривой Basic ({imya}) + метка Лены → Лена, не 401 и не 500",
                 r.status_code == 200 and r.json().get("login") == "lena", r.status_code)
        r = kl(raise_server_exceptions=False).get("/api/kto", headers=h)
        proverka(f"кривой Basic ({imya}) без метки → 401, не 500", r.status_code == 401, r.status_code)
    r = kl(raise_server_exceptions=False).get("/api/kto", auth=("admin", "пароль-по-русски"))
    proverka("русские буквы в пароле общего входа → 401, не 500", r.status_code == 401, r.status_code)


def test_podpis():
    print("Подпись и роль (заготовки для исполнителей Б и В)")
    zapros = lambda k: types.SimpleNamespace(state=types.SimpleNamespace(kto=k))  # noqa: E731
    lena = {"login": "lena", "imya": "Лена", "rol": "admin", "sposob": "forma", "seans": "x"}
    klod = {"login": "klod", "imya": "Клод", "rol": "sluzh", "sposob": "klyuch", "seans": ""}
    vykl = {"login": "klod-vykladka", "imya": "Клод (выкладка)", "rol": "vykladka", "sposob": "klyuch", "seans": ""}
    avto = {"login": "avtomatika", "imya": "Автоматика", "rol": "avto", "sposob": "vnutr", "seans": ""}
    staryy = {"login": "admin", "imya": "", "rol": "admin", "sposob": "staryy", "seans": ""}
    p = m.vhod.podpis
    proverka("человеку — всегда его имя, что бы ни пришло в запросе", p(zapros(lena), "Ира") == "Лена")
    proverka("ключу рутин — «Клод», «Клод за Лену», «Клод за Бориса»",
             (p(zapros(klod)), p(zapros(klod), "Лена"), p(zapros(klod), "Борис")) == ("Клод", "Клод за Лену", "Клод за Бориса"))
    proverka("ключу выкладки и автоматике — свои подписи",
             (p(zapros(vykl), "Лена"), p(zapros(avto), "Лена")) == ("Клод (выкладка)", "Автоматика"))
    proverka("общему входу — имя из запроса, как раньше", p(zapros(staryy), "Ира") == "Ира")
    proverka("iz() без входа — пустые поля, а не ошибка", m.vhod.iz(zapros(None))["rol"] == "")
    proverka("роль меню: владелец и ключ выкладки — owner, остальные — admin",
             [m.vhod.rol_menyu(k) for k in (lena, klod, vykl, avto, staryy)] == ["admin", "admin", "owner", "admin", "admin"])
    vl = {**lena, "login": "boris", "rol": "owner"}
    proverka("заготовка mozhno_vladelcu: владелец и оба ключа — да, админ и автоматика — нет",
             [m.vhod.mozhno_vladelcu(k) for k in (vl, klod, vykl, lena, avto, staryy, None)]
             == [True, True, True, False, False, False, False])


def _posl_zhurnal(put):
    with osnovnaya() as o:
        r = o.execute("SELECT login, imya, sposob, metod, kod, chto FROM deystviya WHERE put=? ORDER BY id DESC LIMIT 1",
                      (put,)).fetchone()
    return tuple(r) if r else ()


# --- 10. Чужой запрос ---------------------------------------------------------------------------

def test_chuzhoy():
    print("10. Подделка запросов: чужой сайт не может действовать от имени вошедшего")
    klyuch = m.vhod.vydat_klyuch("klod")
    BEAR = {"authorization": "Bearer " + klyuch}
    proverka("ключ рутин: /api/kto → klod (Bearer и Basic klod:<ключ>)",
             kto(kl(), headers=BEAR) == {"login": "klod", "imya": "Клод", "rol": "sluzh", "sposob": "klyuch"}
             and login_kto(kl(), auth=("klod", klyuch)) == "klod")
    proverka("неверный ключ → 401", kl().get("/api/kto", headers={"authorization": "Bearer ne-tot"}).status_code == 401)
    proverka("ключ рутин — не через форму: служебной записи пароль не задать",
             m.vhod.zadat_parol("klod", pridumat()) != "")
    c = kl()
    voyti(c, "lena", P["lena"])
    telo = {"who": "Лена", "text": "проверка входа: подделка"}
    sposoby = (("метка", c, {}, None), ("старый вход", kl(), {}, OBSHCHIY), ("ключ", kl(), BEAR, None))
    for imya, k, h, auth in sposoby:
        g = k.get("/api/pult/tasks", headers={**h, **CHUZH_FON}, auth=auth).status_code
        p = k.post("/api/plan/inbox", json=telo, headers={**h, **CHUZH_FON}, auth=auth).status_code
        o = k.post("/api/plan/inbox", json=telo, headers={**h, "origin": "https://chuzhoy.example"}, auth=auth).status_code
        proverka(f"{imya}: фоновый cross-site GET и POST, чужой Origin → 403", (g, p, o) == (403, 403, 403), (g, p, o))
    proverka("метка: Origin http://app.kidsup.ru (без шифрования) → 403",
             c.post("/api/plan/inbox", json=telo, headers={"origin": "http://app.kidsup.ru"}).status_code == 403)
    proverka("метка: POST без Origin и Sec-Fetch-Site → 403", c.post("/api/plan/inbox", json=telo).status_code == 403)
    proverka("метка: POST со своей страницы → 200", c.post("/api/plan/inbox", json=telo, headers=FETCH).status_code == 200)
    proverka("метка: Origin https://app.kidsup.ru без Sec-Fetch → 200",
             c.post("/api/plan/inbox", json=telo, headers={"origin": B}).status_code == 200)
    proverka("ключ без заголовков (скрипт) → 200", kl().post("/api/plan/inbox", json=telo, headers=BEAR).status_code == 200)
    proverka("старый вход без заголовков (скрипт) → 200",
             kl().post("/api/plan/inbox", json=telo, auth=OBSHCHIY).status_code == 200)
    r = c.get("/liniya", headers={**STR, "sec-fetch-site": "cross-site"})
    proverka("переход по ссылке из чата (cross-site, navigate) — страница открывается", r.status_code == 200, r.status_code)
    for h in ({"origin": "https://chuzhoy.example"}, {"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate"}):
        proverka(f"/vyhod с чужого сайта ({sorted(h)[0]}) → 403, вход не тронут",
                 c.post("/vyhod", headers=h).status_code == 403 and login_kto(c) == "lena")
    r = kl().post("/vhod", data={"login": "lena", "parol": P["lena"]}, headers={"origin": "https://chuzhoy.example"})
    proverka("вход с чужого сайта → 403 и без метки", r.status_code == 403 and not metki(r), r.status_code)
    r = kl().post("/vhod", data={"login": "lena", "parol": P["lena"]})
    proverka("вход без Origin и Sec-Fetch-Site (не со своей страницы) → 403", r.status_code == 403)
    return klyuch


# --- 11. Перебор --------------------------------------------------------------------------------

def test_perebor(klyuch):
    print("11. Перебор")
    neverno = lambda: "ne-tot-" + secrets.token_hex(5)  # noqa: E731
    T2 = msk(2026, 11, 25, 12, 0)
    try:
        chasy(T2)
        x = kl("9.9.9.9")
        kody = [voyti(x, "lena", neverno()).status_code for _ in range(5)]
        r = voyti(x, "lena", P["lena"])
        proverka("5 промахов подряд → пауза 1 минута, даже верный пароль не проверяется",
                 kody == [401] * 5 and r.status_code == 429 and "Подождите 1 минуту или позвоните Борису" in r.text,
                 (kody, r.status_code))
        t = T2
        for _ in range(5):
            t += 61
            chasy(t)
            voyti(x, "lena", neverno())
        r = voyti(x, "lena", P["lena"])
        proverka("после 10 — 5 минут", r.status_code == 429 and "Подождите 5 минут" in r.text, r.status_code)
        for _ in range(5):
            t += 301
            chasy(t)
            voyti(x, "lena", neverno())
        r = voyti(x, "lena", P["lena"])
        proverka("после 15 — 15 минут", r.status_code == 429 and "Подождите 15 минут" in r.text, r.status_code)
        proverka("с другого адреса (свой телефон) Лена входит", voyti(kl("5.5.5.5"), "lena", P["lena"]).status_code == 303)
        proverka("ключ с запертого адреса не задет",
                 kl("9.9.9.9").get("/api/pult/tasks", headers={"authorization": "Bearer " + klyuch}).status_code == 200)
        chasy(t + 901)
        proverka("через 15 минут пауза снята", voyti(x, "lena", P["lena"]).status_code == 303)
    finally:
        chasy(None)

    print("11. Перебор: 24 одновременных неверных пароля")
    nastoyashchih = []
    orig = m.vhod.parol_veren

    def schet(parol, hesh):
        if hesh:
            nastoyashchih.append(1)
        return orig(parol, hesh)
    m.vhod.parol_veren = schet
    kody = []
    try:
        def odna():
            kody.append(voyti(kl("8.8.4.4"), "ira", neverno()).status_code)
        potoki = [threading.Thread(target=odna) for _ in range(24)]
        [p.start() for p in potoki]
        [p.join() for p in potoki]
    finally:
        m.vhod.parol_veren = orig
    proverka("проверено паролей не больше порога (5), остальные — пауза",
             len(nastoyashchih) <= 5 and kody.count(401) <= 5 and kody.count(429) >= 19 and len(kody) == 24,
             (len(nastoyashchih), kody.count(401), kody.count(429)))
    m.vhod.snyat_pauzu("ira")
    proverka("«Снять паузу» — Ира с того же адреса входит", voyti(kl("8.8.4.4"), "ira", P["ira"]).status_code == 303)

    print("11. Перебор по адресу: знакомый браузер не попадает под запрет")
    znakomyy = kl("7.7.7.7")
    voyti(znakomyy, "liza", P["liza"])
    vyyti(znakomyy)
    kody = [voyti(kl("7.7.7.7"), "nikto" + secrets.token_hex(3), neverno()).status_code for _ in range(20)]
    proverka("20 промахов с одного адреса по выдуманным логинам — каждый 401", kody == [401] * 20, kody[-3:])
    proverka("новый браузер с того же адреса — пауза 15 минут даже с верным паролем",
             voyti(kl("7.7.7.7"), "liza", P["liza"]).status_code == 429)
    proverka("знакомый браузер (уже входили через форму) с того же адреса — входит",
             voyti(znakomyy, "liza", P["liza"]).status_code == 303)

    print("11. Перебор в окне браузера (старый общий вход)")
    kody = [kl("6.6.6.6").get("/api/pult/tasks", auth=("admin", neverno())).status_code for _ in range(40)]
    proverka("40 неверных паролей admin в окне → дальше пауза", kody[:5] == [401] * 5 and set(kody[5:]) == {429},
             sorted(set(kody)))
    proverka("…верный пароль с того же адреса тоже ждёт; с другого адреса — проходит",
             (kl("6.6.6.6").get("/api/pult/tasks", auth=OBSHCHIY).status_code,
              kl("6.6.6.7").get("/api/pult/tasks", auth=OBSHCHIY).status_code) == (429, 200))
    odin = neverno()
    for _ in range(30):
        kl("6.6.7.7").get("/api/pult/tasks", auth=("admin", odin))
    with baza_vhodov() as b:
        n = b.execute("SELECT COUNT(*) FROM popytki WHERE login='admin' AND adres='6.6.7.7'").fetchone()[0]
    proverka("один и тот же неверный пароль 30 раз — один промах (старая вкладка не запирает центр)", n == 1, n)
    proverka("…и общий вход с этого адреса работает", kl("6.6.7.7").get("/api/pult/tasks", auth=OBSHCHIY).status_code == 200)

    print("11. 50 промахов по одному логину с разных адресов — сообщение Борису")
    m.vhod.snyat_pauzu("ira")
    soobshcheniya = []
    orig_otpr, orig_pv = m.vhod._otpravit, m.vhod.parol_veren
    bylo_tel = m.db.get_setting("digest_phone")
    m.db.set_setting("digest_phone", "79990000000")          # выдуманный номер стенда
    m.vhod._otpravit = soobshcheniya.append
    m.vhod.parol_veren = lambda parol, hesh: False          # счёт, а не хеш: быстро
    try:
        for i in range(52):
            voyti(kl(f"10.0.{i // 200}.{i % 200 + 1}"), "ira", neverno())
        for _ in range(30):
            if soobshcheniya:
                break
            time.sleep(0.1)
    finally:
        m.vhod._otpravit, m.vhod.parol_veren = orig_otpr, orig_pv
        m.db.set_setting("digest_phone", bylo_tel or "")
    proverka("ровно одно сообщение, в нём логин и нет паролей",
             len(soobshcheniya) == 1 and "«ira»" in soobshcheniya[0] and P["ira"] not in soobshcheniya[0],
             len(soobshcheniya))
    proverka("вход Иры при этом не закрыт (со своего адреса)", voyti(kl("5.5.5.6"), "ira", P["ira"]).status_code == 303)
    with osnovnaya() as o:
        vazhnoe = o.execute("SELECT COUNT(*) FROM deystviya WHERE vazhnoe=1 AND chto LIKE '%ira%'").fetchone()[0]
    proverka("строка «Важное» в журнале", vazhnoe >= 1)
    m.vhod.snyat_pauzu("ira")


# --- 12. Раскладка ---------------------------------------------------------------------------------

EN_RU = str.maketrans("qwertyuiopasdfghjklzxcvbnm", "йцукенгшщзфывапролдячсмить")


def test_raskladka():
    print("12. Пароль набран в русской раскладке")
    parol = pridumat()
    proverka("пароль из «Придумать» задан Лизе", m.vhod.zadat_parol("liza", parol) == "")
    P["liza"] = parol
    ru = parol.translate(EN_RU)
    proverka("в русской раскладке — те же клавиши, вход проходит", voyti(kl(), "Лиза", ru).status_code == 303)
    proverka("чужой пароль в русской раскладке — не проходит",
             voyti(kl(), "Лиза", pridumat().translate(EN_RU)).status_code == 401)
    m.vhod.snyat_pauzu("liza")


# --- 13. Куда вернуть ------------------------------------------------------------------------------

def test_next():
    print("13. После входа — только внутрь сайта")
    for plohoy in ("//чужой.сайт/x", "/\\чужой", "https://чужой.сайт", "/\t/чужой.сайт", "/vhod?next=/pult"):
        r = voyti(kl(), "lena", P["lena"], next_=plohoy)
        proverka(f"next={plohoy!r} → /", (r.status_code, r.headers.get("location")) == (303, "/"),
                 (r.status_code, r.headers.get("location")))
    proverka("без next: админ → /pult", voyti(kl(), "lena", P["lena"], next_="").headers.get("location") == "/pult")
    loc = voyti(kl(), "boris", P["boris"], next_="").headers.get("location") or ""
    proverka("без next: владелец → его стартовая", loc.startswith("/boris/docs"), loc)
    r = kl().get("/vhod?next=//chuzhoy.example", headers=STR)
    proverka("на самой форме чужой next не сохраняется", 'chuzhoy' not in r.text)


# --- 14. Публичные домены ----------------------------------------------------------------------------

def test_publichnye():
    print("14. Публичные домены: ни формы, ни меток")
    c = kl()
    voyti(c, "lena", P["lena"])
    vyyti(c)
    PUB = {**STR, "host": "kidsup.ru"}
    r = c.get("/", headers=PUB)
    proverka("kidsup.ru/ — сайт, без метки и без перехода на форму",
             r.status_code == 200 and not metki(r) and "location" not in r.headers, r.status_code)
    r = c.get("/pult", headers=PUB)
    proverka("kidsup.ru/pult — формы не показывает (как раньше: 401)", r.status_code == 401 and
             "location" not in r.headers, r.status_code)
    proverka("kidsup.ru/vhod → 404", c.get("/vhod", headers=PUB).status_code == 404)
    proverka("POST kidsup.ru/vhod → 404",
             c.post("/vhod", data={"login": "lena", "parol": P["lena"]}, headers={**SVOI, "host": "kidsup.ru"}).status_code == 404)


# --- 15. Ключ педагога -------------------------------------------------------------------------------

def test_pedagog():
    print("15. Ключ педагога")
    c = kl()
    voyti(c, "lena", P["lena"])
    vyyti(c)                                 # браузер, где форма уже включена
    r = c.get(f"/english/karta?k={PED}", headers=STR)
    proverka("с ?k= — 200 без входа и без перехода на форму", r.status_code == 200 and "location" not in r.headers,
             r.status_code)
    r = c.get("/english/karta", headers=STR)
    proverka("без ключа — форма входа", r.status_code == 303 and r.headers.get("location", "").startswith("/vhod"))
    voyti(c, "lena", P["lena"])
    r = c.get(f"/english/karta?k={PED}", headers=STR)
    proverka("вошедшая Лена с ключом в ссылке — остаётся собой (меню админа на месте)",
             r.status_code == 200 and 'id="nav-razdely"' in r.text)
    r = kl().get(f"/english/karta?k={PED}", headers=STR)
    proverka("педагог без входа — без меню", r.status_code == 200 and 'id="nav-razdely"' not in r.text)


# --- 16. Режим без входа -----------------------------------------------------------------------------

def test_bez_vhoda_rezhim(work):
    print("16. Режим без входа — только KIDSUP_BEZ_VHODA=1 при пустом APP_PASSWORD")
    bylo = (m.config.APP_PASSWORD, m.config.BEZ_VHODA, m.config.VHODY_DB)
    with osnovnaya() as o:
        nastr = {r["key"]: r["value"] for r in o.execute(
            "SELECT key, value FROM settings WHERE key IN ('vhod_zavedeny', 'vhod_starye_do', 'owner_password')")}
    try:
        m.config.APP_PASSWORD, m.config.BEZ_VHODA = "", False
        m.config.VHODY_DB = Path(work) / "pustoy" / "vhody.db"
        m.config.VHODY_DB.parent.mkdir(exist_ok=True)
        proverka("без переменной и без единого входа — закрыто", kl().get("/api/pult/tasks").status_code == 401)
        with osnovnaya() as o:
            o.execute("DELETE FROM settings WHERE key IN ('vhod_zavedeny', 'vhod_starye_do', 'owner_password')")
        proverka("…и на «новом сервере» (пустой APP_PASSWORD, пустые vhody.db и настройки) — закрыто",
                 (kl().get("/api/pult/tasks").status_code, kl().get("/liniya", headers=STR).status_code) == (401, 401))
        m.config.BEZ_VHODA = True
        proverka("с KIDSUP_BEZ_VHODA=1 — открыто", kto(kl()) == {"login": "", "imya": "", "rol": "owner",
                                                               "sposob": "otkryto"})
        m.config.APP_PASSWORD = bylo[0]
        proverka("переменная при заданном APP_PASSWORD не открывает", kl().get("/api/pult/tasks").status_code == 401)
    finally:
        m.config.APP_PASSWORD, m.config.BEZ_VHODA, m.config.VHODY_DB = bylo
        for k_, v_ in nastr.items():
            m.db.set_setting(k_, v_)


# --- 17. Сбой файла входов ---------------------------------------------------------------------------

def test_sboy(work):
    print("17. Сбой vhody.db: старые входы всё равно проходят")
    c = kl(raise_server_exceptions=False)
    voyti(c, "lena", P["lena"])
    orig = m.vhod._baza

    def slomana():
        raise sqlite3.DatabaseError("стенд: файл входов сломан")
    m.vhod._baza = slomana
    try:
        d = kl(raise_server_exceptions=False)
        proverka("общий admin проходит", d.get("/api/pult/tasks", auth=OBSHCHIY).status_code == 200)
        r = d.post("/api/deploy", content=b"", auth=STARYY_VLADELCA)
        proverka("/api/deploy с пустым телом под прежним boris → 400", r.status_code == 400, r.status_code)
        proverka("браузер с меткой и старым паролем — проходит старым входом",
                 c.get("/api/pult/tasks", auth=OBSHCHIY).status_code == 200)
        proverka("метка без старого пароля — 401, не 500", c.get("/api/pult/tasks").status_code == 401)
        r = d.get("/vhod", headers=STR)
        proverka("форма входа открывается", r.status_code == 200, r.status_code)
        r = voyti(d, "lena", P["lena"])
        proverka("вход через форму — понятный отказ, не 500", r.status_code == 503 and "Борису" in r.text, r.status_code)
    finally:
        m.vhod._baza = orig
    print("17. Испорченный vhody.db: берётся копия рядом")
    put = Path(m.config.VHODY_DB)
    proverka("копия рядом есть и в ней нет сеансов", m.vhod._put_kopii().exists()
             and sqlite3.connect(m.vhod._put_kopii()).execute("SELECT COUNT(*) FROM seansy").fetchone()[0] == 0)
    put.write_bytes(b"\x00stend: isporchen\x00" * 64)
    m.vhod._GOTOVO.clear()
    d = kl(raise_server_exceptions=False)
    proverka("общий admin проходит", d.get("/api/pult/tasks", auth=OBSHCHIY).status_code == 200)
    proverka("пароли восстановлены из копии — Лена входит", voyti(kl(), "lena", P["lena"]).status_code == 303)
    proverka("испорченный файл отложен в сторону", any(p.name.startswith("vhody.db.isporchen-") for p in put.parent.iterdir()))
    proverka("прежний сеанс не воскрес (сеансы в копию не попадают)", c.get("/api/kto").status_code == 401)


# --- 18. Заголовки -------------------------------------------------------------------------------------

def test_zagolovki():
    print("18. Заголовки HTML под входом")
    c = kl()
    voyti(c, "lena", P["lena"])
    r = c.get("/liniya", headers=STR)
    proverka("страница под входом: Cache-Control: no-store и X-Frame-Options: DENY",
             (r.status_code, r.headers.get("cache-control"), r.headers.get("x-frame-options")) == (200, "no-store", "DENY"),
             (r.status_code, r.headers.get("cache-control"), r.headers.get("x-frame-options")))
    r = kl().get("/vhod", headers=STR)
    proverka("форма входа — тоже", (r.headers.get("cache-control"), r.headers.get("x-frame-options")) == ("no-store", "DENY"))
    r = kl().get("/", headers={**STR, "host": "kidsup.ru"})
    proverka("публичный сайт — без них", r.status_code == 200 and "x-frame-options" not in r.headers
             and "no-store" not in (r.headers.get("cache-control") or ""))
    r = c.get("/api/pult/tasks", headers=FETCH)
    proverka("JSON — без X-Frame-Options", r.status_code == 200 and "x-frame-options" not in r.headers)


# --- 19. vhod.js на страницах меню ------------------------------------------------------------------------

def test_vhod_js():
    print("19. vhod.js на каждой странице меню (админа и владельца)")
    from app import nav
    vladelec, admin = kl(), kl()
    voyti(vladelec, "boris", P["boris"])
    voyti(admin, "lena", P["lena"])
    for kto_, rol in ((admin, "admin"), (vladelec, "owner")):
        for razdel, punkty in nav.menu(rol):
            for imya, adres in punkty:
                if adres == "/owner":
                    print("  (–)     /owner — vhod.js подключает исполнитель Б вместе с блоком «Входы»")
                    continue
                r = kto_.get(adres, headers=STR)
                proverka(f"{rol}: «{imya}» {adres} → 200 и /static/vhod.js",
                         r.status_code == 200 and "/static/vhod.js" in r.text, r.status_code)


# --- 20. Автоматика сервера -------------------------------------------------------------------------------

def test_avtomatika():
    print("20. Автоматика сервера: свой ключ в памяти, пункты в пульт кладутся и без общего входа")
    import httpx
    from app import nadezhda, perepiska
    vh = m.db.admin_auth()
    proverka("db.admin_auth() в процессе сервера — вход «avtomatika», ключ длинный и случайный",
             vh is not None and vh[0] == "avtomatika" and len(vh[1]) >= 40 and vh[1] != OBSHCHIY[1])
    bylo_do = m.db.get_setting("vhod_starye_do")
    m.db.set_setting("vhod_starye_do", m.vhod._iso(time.time() - 60))
    c = kl()
    orig_post, orig_get = httpx.post, httpx.get

    def cherez_klienta(metod):
        def f(url, auth=None, json=None, timeout=None, **kw):
            u = urlsplit(url)
            return c.request(metod, u.path + ("?" + u.query if u.query else ""), auth=auth, json=json)
        return f
    metka = "проверка автоматики " + secrets.token_hex(4)
    try:
        proverka("старые входы выключены, общий admin не принимается",
                 not m.vhod.starye_vklyucheny() and kl().get("/api/pult/tasks", auth=OBSHCHIY).status_code == 401)
        r = kl().get("/liniya", headers=STR)
        proverka("…новый браузер без входа получает форму, а не серое окно",
                 r.status_code == 303 and r.headers.get("location", "").startswith("/vhod"))
        httpx.post, httpx.get = cherez_klienta("POST"), cherez_klienta("GET")
        perepiska._inbox("Лена", metka + " (переписка)", "", "проверка входа")
        nadezhda._inbox(metka + " (Надежда)", "")
    finally:
        httpx.post, httpx.get = orig_post, orig_get
        m.db.set_setting("vhod_starye_do", bylo_do)
    with osnovnaya() as o:
        punkty = [r["text"] for r in o.execute("SELECT text FROM plan_inbox WHERE text LIKE ?", (metka + "%",))]
        zh = o.execute("SELECT imya, sposob, kod FROM deystviya WHERE put='/api/plan/inbox' ORDER BY id DESC LIMIT 2").fetchall()
    proverka("perepiska._inbox и nadezhda._inbox положили по пункту", len(punkty) == 2, len(punkty))
    proverka("в журнале подписано «Автоматика»", [tuple(r) for r in zh] == [("Автоматика", "vnutr", 200)] * 2,
             [tuple(r) for r in zh])
    proverka("этим ключом /api/pult/done → 403", kl().post("/api/pult/done", json={"id": 1}, auth=vh).status_code == 403)
    proverka("…/api/settings → 403, страница пульта → 403",
             (kl().get("/api/settings", auth=vh).status_code, kl().get("/pult", auth=vh).status_code) == (403, 403))
    proverka("…/api/duty и /api/dialogs → 200",
             (kl().get("/api/duty", auth=vh).status_code, kl().get("/api/dialogs", auth=vh).status_code) == (200, 200))


# --- хранение и следы ---------------------------------------------------------------------------------------

def test_sledy(klyuch, work):
    print("Следы: паролей и ключей нет ни в файлах, ни в журнале")
    sekrety = [p for p in P.values()] + [klyuch]
    fajly = [Path(m.config.VHODY_DB), m.vhod._put_kopii(), Path(m.config.DB_PATH)]
    for f in fajly:
        raw = f.read_bytes() if f.exists() else b""
        proverka(f"{f.name}: ни одного пароля и ключа побайтно", raw and not any(s.encode() in raw for s in sekrety))
    with osnovnaya() as o:
        tablicy = {r[0] for r in o.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    proverka("в kidsup.db нет таблиц входов (они в vhody.db)", not tablicy & {"vhody", "seansy", "popytki", "ustroystva"})
    with baza_vhodov() as b:
        heshi = [r["hesh"] for r in b.execute("SELECT hesh FROM vhody WHERE hesh<>''")]
    proverka("хеши паролей — PBKDF2-SHA256 600 000 кругов, ключей — SHA-256",
             heshi and all(h.startswith(("pbkdf2_sha256$600000$", "sha256$")) for h in heshi))
    proverka("у одинакового пароля разные хеши (соль)", m.vhod.hesh_parolya("x" * 12) != m.vhod.hesh_parolya("x" * 12))
    proverka("права файла входов — только владельцу процесса (0600)",
             oct(os.stat(m.config.VHODY_DB).st_mode & 0o777) == "0o600")


def _lovushki_na_vse():
    import app
    for mod in pkgutil.iter_modules(app.__path__):
        importlib.import_module(f"app.{mod.name}")
    lt._postavit_lovushki()


def main():
    global m
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="")
    a = ap.parse_args()
    work = a.work or tempfile.mkdtemp(prefix="vhod_")
    os.makedirs(work, exist_ok=True)
    m = lt.zapustit(KOREN, work)
    lt.zasejat(os.path.join(work, "kidsup.db"))
    # роли — как на сервере: общий вход админов и отдельный пароль владельца; режим без входа выключен
    m.config.APP_USER, m.config.APP_PASSWORD = OBSHCHIY
    m.config.BEZ_VHODA = False
    m.db.set_setting("owner_password", STARYY_VLADELCA[1])
    m.db.set_setting("pedagog_key", PED)
    _lovushki_na_vse()
    test_kak_segodnya()
    test_obhod()
    for lg in ("lena", "ira", "liza", "boris"):
        P[lg] = pridumat()
        proverka(f"пароль задан: {lg}", m.vhod.zadat_parol(lg, P[lg]) == "")
    proverka("первый пароль: отметка «входы заведены», старые входы ещё 7 дней",
             m.vhod.zavedeny() and m.vhod.starye_vklyucheny()
             and 6.9 * 86400 < m.vhod._iz_iso(m.db.get_setting("vhod_starye_do")) - time.time() <= 7 * 86400)
    proverka("старые входы после заведения работают по-прежнему",
             (kl().get("/api/pult/tasks", auth=OBSHCHIY).status_code,
              kl().get("/api/settings", auth=STARYY_VLADELCA).status_code) == (200, 200))
    test_stranica()
    test_bez_vhoda()
    test_metka()
    test_login_po_russki()
    test_vyhod()
    test_sroki()
    test_obshchiy()
    test_metka_i_staryy()
    test_podpis()
    klyuch = test_chuzhoy()
    test_perebor(klyuch)
    test_raskladka()
    test_next()
    test_publichnye()
    test_pedagog()
    test_bez_vhoda_rezhim(work)
    test_zagolovki()
    test_vhod_js()
    test_avtomatika()
    test_sboy(work)
    test_sledy(klyuch, work)
    proverka("ни одной отправки", not lt.LOVUSHKI, lt.LOVUSHKI)
    chuzhie = [x for x in lt.SET if any("vhod" in f for f in x[1])]
    proverka("вход не ходит в сеть", not chuzhie, chuzhie)
    print(f"\nИтого: {lt.proshlo} OK, {lt.oshibok} ошибок · стенд {work}")
    sys.exit(1 if lt.oshibok else 0)


if __name__ == "__main__":
    main()
