# -*- coding: utf-8 -*-
"""Личные входы на app.kidsup.ru (app/vhod.py) — проверки групп А «вход» и Б «управление», на стенде,
без сервера и без сети.

12.11.2026, Борис: «да» личным логинам админов. Каждый входит под своим именем на
странице /vhod, сервер знает, кто сделал действие. Главное требование выкатки: в момент
выкладки для людей и рутин не меняется НИЧЕГО — общий admin и прежний пароль владельца в
сером окне браузера работают как раньше, новое включается только действиями владельца.

Группа Б «управление» (раздел 9.2, проверки 21–30): блок «Входы» на /owner, подтверждение
паролем владельца, правила пароля, ключи Клода, самопорядок, «Отключить старые входы», срок
и автовыключение, временный вход, консольная команда, хранение. Владелец в тесте проходит
действия 1, 2 и 4 из раздела 7.2 плана.

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
import json
import importlib.util
import logging
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
# 13.11.2026, сверка безопасности: GET /api/net без входа отдавал правила файрвола и домены
# Caddy — закрыт нарочно (только владелец). Снимок выше не трогаем, ожидание — минус этот адрес.
ZAKRYTY_NAROCHNO = {"GET /api/net"}
PROVERKA_VNUTRI = {("GET", "/"), ("GET", "/boris")}     # без зависимости, check_auth зовут сами

# Выдуманные пароли стенда — придуманы на ходу.
OBSHCHIY = ("admin", "stend-" + secrets.token_urlsafe(10))          # старый общий вход
STARYY_VLADELCA = ("boris", "stend-" + secrets.token_urlsafe(10))   # прежний пароль владельца
P: dict = {}                                                        # личные пароли
PED = "stend-" + secrets.token_urlsafe(8)                           # ключ педагога
VNE_SERVERA: list = []              # что было сразу после импорта app.main, до «запуска сервера»
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


def otm(klyuch: str, znachenie: str | None):
    """Отметка входов (vhod_zavedeny, vhod_starye_do, vhod_starye_vykl) — сразу в оба
    хранилища: settings основной базы и otmetki файла входов. None — убрать из обоих.
    Сервер держит их в двух местах и при расхождении сводит к строгому."""
    with osnovnaya() as o:
        if znachenie is None:
            o.execute("DELETE FROM settings WHERE key=?", (klyuch,))
        else:
            o.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (klyuch, znachenie))
    with m.vhod._baza() as b:
        if znachenie is None:
            b.execute("DELETE FROM otmetki WHERE klyuch=?", (klyuch,))
        else:
            b.execute("INSERT INTO otmetki (klyuch, znachenie) VALUES (?,?) "
                      "ON CONFLICT(klyuch) DO UPDATE SET znachenie=excluded.znachenie", (klyuch, znachenie))


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
    # 12.11.2026, вечер, решение Бориса: главная — страница разделов, без перехода на пульт.
    ra, rb = kl().get("/", auth=OBSHCHIY), kl().get("/", auth=STARYY_VLADELCA)
    proverka("корень — страница разделов: admin → 200 без раздела «Владельцу», boris → 200 с ним",
             (ra.status_code, rb.status_code) == (200, 200) and "Открыть пульт" in ra.text
             and "Владельцу" not in ra.text and "Владельцу" in rb.text and "Мои дела на пульте" in rb.text,
             (ra.status_code, rb.status_code))
    proverka("…без входа корень → 401 с окном пароля, как раньше",
             kl().get("/").status_code == 401 and "basic" in kl().get("/").headers.get("www-authenticate", "").lower())
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


# --- замечания проверки 12.11: в день выкладки старые входы запереть нельзя ---------------------

def test_zamechaniya_v_den_vykladki():
    print("Замечания проверки 12.11 — день выкладки (входы не заведены)")
    neverno = lambda: "ne-tot-" + secrets.token_hex(5)  # noqa: E731
    proverka("отметки «входы заведены» нет", not m.vhod.zavedeny())

    print("  автоматика: ключ — только в работающем сервере")
    klyuch_bylo, vhod_bylo, fayl_byl = VNE_SERVERA[0]
    proverka("после импорта app.main вне сервера ключа автоматики нет, db.admin_auth() его не отдаёт, "
             "файл входов не появился",
             klyuch_bylo is None and (vhod_bylo is None or vhod_bylo[0] == "admin") and not fayl_byl
             and not m.vhod.eto_server(), (klyuch_bylo is None, vhod_bylo and vhod_bylo[0], fayl_byl))
    proverka("после запуска сервера ключ есть", bool(m.vhod.vnutr_klyuch()) and m.db.admin_auth()[0] == "avtomatika")
    bylo = m.vhod._VNUTR[0]
    m.vhod._VNUTR[0] = None
    try:
        from fastapi.testclient import TestClient
        with TestClient(m.app, base_url=B) as tc:      # настоящий запуск приложения (lifespan)
            proverka("запуск приложения сам создаёт ключ автоматики",
                     bool(m.vhod.vnutr_klyuch()) and tc.get("/api/health").status_code == 200)
    finally:
        m.vhod._VNUTR[0] = bylo

    print("  перечень адресов без входа не отдаётся")
    kody = [kl().get(a).status_code for a in ("/openapi.json", "/docs", "/redoc")]
    kody_b = [kl().get(a, auth=STARYY_VLADELCA).status_code for a in ("/openapi.json", "/docs", "/redoc")]
    proverka("/openapi.json, /docs, /redoc → 404 и без входа, и со входом", kody + kody_b == [404] * 6, kody + kody_b)
    proverka("…таких маршрутов в приложении нет",
             not {"/openapi.json", "/docs", "/redoc"} & {getattr(r_, "path", "") for r_ in m.app.routes})

    print("  промахи формы /vhod не запирают старые входы в сером окне")
    A = "198.51.100.30"
    proverka("общий admin и прежний boris с адреса центра — проходят",
             (kl(A).get("/api/pult/tasks", auth=OBSHCHIY).status_code,
              kl(A).get("/api/settings", auth=STARYY_VLADELCA).status_code) == (200, 200))
    kody = [voyti(kl(A), f"gost{i}", neverno()).status_code for i in range(22)]
    proverka("22 неверных входа через форму с этого адреса: 20 раз 401, дальше форма на паузе",
             kody == [401] * 20 + [429] * 2, kody[-4:])
    r1, r2 = kl(A).get("/api/pult/tasks", auth=OBSHCHIY), kl(A).get("/api/settings", auth=STARYY_VLADELCA)
    proverka("…верный общий admin и прежний boris в окне с того же адреса проходят, как раньше",
             (r1.status_code, r2.status_code) == (200, 200), (r1.status_code, r2.status_code))
    A0 = "198.51.100.31"
    [voyti(kl(A0), f"gost{i}", neverno()) for i in range(21)]
    proverka("…и с адреса, где верного входа в окне ещё не было, — тоже (счёт формы окну не мешает)",
             (kl(A0).get("/api/pult/tasks", auth=OBSHCHIY).status_code,
              kl(A0).get("/api/settings", auth=STARYY_VLADELCA).status_code) == (200, 200))
    kody = [kl(A0).get("/api/pult/tasks", auth=("gost" + secrets.token_hex(2), neverno())).status_code for _ in range(25)]
    proverka("25 неверных входов в окне с выдуманными логинами — каждый 401, общий admin после них проходит",
             set(kody) == {401} and kl(A0).get("/api/pult/tasks", auth=OBSHCHIY).status_code == 200, sorted(set(kody)))
    with baza_vhodov() as b:
        n = b.execute("SELECT COUNT(*) FROM popytki WHERE adres=? AND login LIKE 'okno:%'", (A0,)).fetchone()[0]
    proverka("…выдуманные логины окна в счёт не идут", n == 0, n)

    print("  чужие опечатки с того же адреса не запирают работающие браузеры и скрипты")
    A1 = "198.51.100.40"
    proverka("общий admin с адреса уже ходил", kl(A1).get("/api/pult/tasks", auth=OBSHCHIY).status_code == 200)
    kody = [kl(A1).get("/api/pult/tasks", auth=("admin", neverno())).status_code for _ in range(7)]
    proverka("7 разных неверных паролей admin с этого адреса: 5 раз 401, дальше пауза", kody == [401] * 5 + [429] * 2, kody)
    proverka("…верный общий пароль, которым с этого адреса уже ходили, паузу не замечает",
             kl(A1).get("/api/pult/tasks", auth=OBSHCHIY).status_code == 200)
    proverka("…изменяющий запрос рутины с этого адреса — тоже",
             kl(A1).post("/api/plan/inbox", json={"who": "Лена", "text": "проверка входа: пауза"},
                         auth=OBSHCHIY).status_code == 200)
    r = kl(A1).get("/api/settings", auth=STARYY_VLADELCA)
    proverka("…у прежнего boris свой счётчик: чужие промахи по admin его не касаются", r.status_code == 200, r.status_code)
    A2 = "198.51.100.41"
    kody = [kl(A2).get("/api/pult/tasks", auth=("admin", neverno())).status_code for _ in range(5)]
    r = kl(A2).get("/api/pult/tasks", auth=OBSHCHIY)
    proverka("перебор не ослаблен: с адреса, где верного пароля ещё не видели, после 5 промахов и верный ждёт",
             kody == [401] * 5 and r.status_code == 429 and r.headers.get("retry-after") == "60"
             and r.json()["detail"].startswith("Слишком много попыток"), (kody, r.status_code))
    r = kl(A2).get("/liniya", headers=STR, auth=OBSHCHIY)
    proverka("…человеку в браузере — понятная страница, а не сырой JSON; код 429 и Retry-After на месте",
             r.status_code == 429 and r.headers.get("content-type", "").startswith("text/html")
             and "Слишком много попыток" in r.text and "Подождите 1 минуту" in r.text and "detail" not in r.text
             and r.headers.get("retry-after") == "60", (r.status_code, r.headers.get("content-type")))
    try:
        chasy(time.time() + 61)
        proverka("через минуту пауза проходит сама", kl(A2).get("/api/pult/tasks", auth=OBSHCHIY).status_code == 200)
    finally:
        chasy(None)

    print("  «5 промахов подряд»: удачный вход стирает промахи")
    A3 = "198.51.100.50"
    kody = [kl(A3).get("/api/pult/tasks", auth=("admin", neverno())).status_code for _ in range(4)]
    kody += [kl(A3).get("/api/pult/tasks", auth=OBSHCHIY).status_code for _ in range(3)]
    kody += [kl(A3).get("/api/pult/tasks", auth=("admin", neverno())).status_code]
    r = kl(A3).get("/api/pult/tasks", auth=OBSHCHIY)
    with baza_vhodov() as b:
        n = b.execute("SELECT COUNT(*) FROM popytki WHERE login='okno:admin' AND adres=?", (A3,)).fetchone()[0]
    proverka("4 опечатки, 3 удачных входа, ещё опечатка — верный пароль проходит, счёт промахов стёрт",
             kody == [401] * 4 + [200] * 3 + [401] and r.status_code == 200 and n == 0, (kody, r.status_code, n))
    A4 = "198.51.100.51"
    [kl(A4).get("/api/pult/tasks", auth=("admin", neverno())) for _ in range(4)]
    m.vhod._EST_PROMAHI.clear()
    m.vhod._NEUD.clear()
    m.vhod._NEUD_ZAGRUZHEN[0] = False                  # как после перезапуска сервера: память пуста
    kl(A4).get("/api/pult/tasks", auth=OBSHCHIY)
    kl(A4).get("/api/pult/tasks", auth=("admin", neverno()))
    proverka("…то же после перезапуска сервера (промахи подняты из базы)",
             kl(A4).get("/api/pult/tasks", auth=OBSHCHIY).status_code == 200)

    print("  страницы под старым входом кешируются как раньше")
    r = kl().get("/liniya", headers=STR, auth=OBSHCHIY)
    proverka("общий admin: страница без Cache-Control: no-store (кнопка «Назад» работает как раньше), "
             "X-Frame-Options: DENY есть",
             r.status_code == 200 and "no-store" not in (r.headers.get("cache-control") or "")
             and r.headers.get("x-frame-options") == "DENY", (r.headers.get("cache-control"), r.headers.get("x-frame-options")))
    r = kl().get("/owner", headers=STR, auth=STARYY_VLADELCA)
    proverka("прежний boris: /owner открывается", r.status_code == 200, r.status_code)
    with baza_vhodov() as b:
        b.execute("DELETE FROM popytki")
    m.vhod._EST_PROMAHI.clear()


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
    zhdem = (OTKRYTYE_DO - ZAKRYTY_NAROCHNO) | NOVYE_OTKRYTYE
    proverka(f"открытых адресов {len(zhdem)}: прежние 42 (сайт, вебхуки) без /api/net (закрыт 13.11) и три адреса входа — и только они",
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
        # Ира входит в том же браузере, но без метки входа Лены (у y — только метка устройства):
        # так бывает, когда метку входа браузер уже потерял, а сеанс на сервере ещё жив
        r = voyti(y, "ira", P["ira"], zapomnit=True)
        proverka("Ира на том же браузере: вход есть, но не запомнен (метка без срока)",
                 r.status_code == 303 and "max-age" not in metki(r).get(m.vhod.COOKIE, "").lower(), r.status_code)
        proverka("…галочки «Запомнить» на этом браузере больше нет", "name=zapomnit" not in y.get("/vhod").text)
        chasy(T0 + 17 * 3600 + 59 * 60)
        proverka("запомненный сеанс Лены живёт только до 4:00: в 03:59 ещё есть", login_kto(lena) == "lena")
        chasy(T0 + 18 * 3600 + 60)
        proverka("…в 04:01 — нет (хотя запоминала на 30 дней); сеанс Иры — тоже",
                 (login_kto(lena), login_kto(y)) == (401, 401))
    finally:
        chasy(None)

    print("8. Вход поверх чужого входа: прежний сеанс этого браузера гасится (замечание проверки 12.11)")
    w = kl()
    voyti(w, "lena", P["lena"])
    lena = kopiya_metki(w)
    proverka("копия метки Лены работает, пока Лена вошла", login_kto(lena) == "lena")
    r = voyti(w, "ira", P["ira"])
    proverka("в том же браузере без выхода вошла Ира — в браузере Ира", r.status_code == 303 and login_kto(w) == "ira")
    proverka("…а скопированная раньше метка Лены больше не действует", login_kto(lena) == 401)
    with baza_vhodov() as b:
        ostalos = b.execute("SELECT COUNT(*) FROM seansy WHERE id=?",
                            (m.vhod._sha(lena.cookies.get(m.vhod.COOKIE)),)).fetchone()[0]
    proverka("…строки сеанса Лены в seansy нет — на /owner не висит лишний открытый сеанс", ostalos == 0, ostalos)
    vyyti(w)
    proverka("Ира нажала «Выйти» — в браузере никого", login_kto(w) == 401)
    r = voyti(w, "ira", "ne-tot-" + secrets.token_hex(5))
    w2 = kl()
    voyti(w2, "liza", P["liza"])
    liza = kopiya_metki(w2)
    voyti(w2, "ira", "ne-tot-" + secrets.token_hex(5))
    proverka("неудачный вход поверх чужого входа прежний сеанс не трогает", login_kto(liza) == "liza")
    m.vhod.snyat_pauzu("ira")


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
    mv = m.vhod.mozhno_vladelcu
    proverka("mozhno_vladelcu на /api/boris-docs: владелец и оба ключа — да, админ, автоматика и общий вход — нет",
             [mv(k, "GET", "/api/boris-docs") for k in (vl, klod, vykl, lena, avto, staryy, None)]
             == [True, True, True, False, False, False, False])
    proverka("…без адреса (прямой вызов по-старому): владелец — да, служебным ключам — нет",
             [mv(k) for k in (vl, klod, vykl, lena)] == [True, False, False, False])


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
        # 13.11.2026: было t = T2. Часы стенда сдвинуты, но идут: первые пять попыток — это пять
        # настоящих расчётов хеша (около 0,2 с каждый), и пятый промах записан не в T2, а на
        # 0,8–1,2 с позже. Шаг «+61 с от T2» попадал в ещё не кончившуюся минутную паузу, когда
        # машина чуть медленнее (2 прогона из 6 подряд с остальными наборами): попытка не
        # считалась, и до «10 промахов» не доходило. Отсчитываем минуту от «сейчас» по часам
        # стенда — проверяется то же самое, без зависимости от скорости хеша.
        t = m.vhod.seychas()
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

    print("11. По счётчику промахов не видно, существует ли логин (замечание проверки 12.11)")
    m.vhod.vklyuchit("liza", False)
    ne_to = "parol-v-pole-logina-" + secrets.token_hex(6)       # в поле логина по ошибке набрали пароль
    try:
        ryady = {}
        for i, lg in enumerate(("lena", "Лена", "netakogo" + secrets.token_hex(2), "klod", "liza", "admin", ne_to)):
            ryady[lg] = [voyti(kl(f"9.9.8.{i + 1}"), lg, neverno()).status_code for _ in range(7)]
        proverka("настоящий, незнакомый, служебный, отключённый логин и admin: 5 раз 401, дальше 429 — одинаково",
                 all(v == [401] * 5 + [429] * 2 for v in ryady.values()), {k_[:8]: v for k_, v in ryady.items()})
        with baza_vhodov() as b:
            loginy = [r["login"] for r in b.execute("SELECT DISTINCT login FROM popytki")]
        proverka("…введённое в поле логина в файл входов не пишется (только отпечаток)",
                 not any(ne_to in x or "netakogo" in x for x in loginy)
                 and ne_to.encode() not in Path(m.config.VHODY_DB).read_bytes())
    finally:
        m.vhod.vklyuchit("liza", True)
        with baza_vhodov() as b:
            b.execute("DELETE FROM popytki WHERE adres LIKE '9.9.8.%'")

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
        n = b.execute("SELECT COUNT(*) FROM popytki WHERE login='okno:admin' AND adres='6.6.7.7'").fetchone()[0]
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
        for k_ in ("vhod_zavedeny", "vhod_starye_do"):
            otm(k_, None)                   # и в пустом файле входов: сервер успел свести отметки
        with osnovnaya() as o:
            o.execute("DELETE FROM settings WHERE key='owner_password'")
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
            m.db.set_setting(k_, v_)        # в настоящем файле входов отметки не менялись


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
        r = d.get("/api/vhod/sostoyanie", auth=STARYY_VLADELCA)
        proverka("/api/vhod/sostoyanie не падает: 200, «файл входов не читается», срок старых входов виден",
                 r.status_code == 200 and r.json().get("fayl_vhodov") == "не читается"
                 and r.json()["starye"]["zavedeny"] is True and r.json()["starye"]["do"] and r.json()["vhody"] == [],
                 r.status_code)
    finally:
        m.vhod._baza = orig
    proverka("…а с исправным файлом — «в порядке» и шесть записей",
             (lambda j: j.get("fayl_vhodov") == "в порядке" and len(j["vhody"]) >= 6)(
                 kl().get("/api/vhod/sostoyanie", auth=STARYY_VLADELCA).json()))

    print("17. Файл входов нельзя подготовить: лог не раздувается, запросы не ждут")
    zapisi, podgotovok = [], []

    class _Schet(logging.Handler):
        def emit(self, record):
            zapisi.append(record.getMessage())
    schet = _Schet()
    logging.getLogger("kidsup.vhod").addHandler(schet)
    bylo_put, orig_podg = m.config.VHODY_DB, m.vhod._podgotovit

    def podg(p_):
        podgotovok.append(1)
        return orig_podg(p_)
    m.vhod._podgotovit = podg
    m.config.VHODY_DB = Path(work) / "net-takoy-papki" / "vhody.db"
    m.vhod._ZHALOBY.clear()
    try:
        t0 = time.time()
        d = kl(raise_server_exceptions=False)
        kody = []
        for _ in range(6):
            kody += [d.get("/api/pult/tasks", auth=OBSHCHIY).status_code, d.get("/api/kto", auth=STARYY_VLADELCA).status_code,
                     d.get("/liniya", headers=STR, auth=OBSHCHIY).status_code]
        kody.append(d.get("/api/vhod/sostoyanie", auth=STARYY_VLADELCA).status_code)
        proverka("19 запросов под старыми входами — все 200", kody == [200] * 19, sorted(set(kody)))
        proverka("файл пробовали подготовить один раз, дальше минуту — сразу короткий отказ",
                 len(podgotovok) == 1, len(podgotovok))
        proverka("в логе входа — не больше десятка записей (было 53), каждая жалоба один раз",
                 0 < len(zapisi) <= 10 and len(set(zapisi)) == len(zapisi), len(zapisi))
        proverka("запертый файл ждут 5 секунд, а не 30; запросы прошли быстро",
                 m.vhod.TAYMAUT == 5 and time.time() - t0 < 20, round(time.time() - t0, 1))
    finally:
        m.config.VHODY_DB, m.vhod._podgotovit = bylo_put, orig_podg
        logging.getLogger("kidsup.vhod").removeHandler(schet)
        m.vhod._SBOY.clear()
        m.vhod._ZHALOBY.clear()
    proverka("файл вернулся — входы работают сразу", voyti(kl(), "lena", P["lena"]).status_code == 303
             and kl().get("/api/pult/tasks", auth=OBSHCHIY).status_code == 200)

    print("17. Файл входов заперт (долгая чужая запись): запрос ждёт один раз, а не при каждом обращении")
    z = sqlite3.connect(m.config.VHODY_DB, timeout=1)
    z.execute("BEGIN EXCLUSIVE")
    bylo_t = m.vhod.TAYMAUT
    m.vhod.TAYMAUT = 0.3
    m.vhod._ZHALOBY.clear()
    try:
        t0 = time.time()
        d = kl(raise_server_exceptions=False)
        kody = []
        for _ in range(6):
            kody += [d.get("/api/pult/tasks", auth=OBSHCHIY).status_code, d.get("/api/kto", auth=STARYY_VLADELCA).status_code,
                     d.get("/liniya", headers=STR, auth=OBSHCHIY).status_code]
        dolgo = time.time() - t0
        proverka("18 запросов под старыми входами при запертом файле — все 200, ожиданий — одно-два, а не 18",
                 kody == [200] * 18 and dolgo < 0.3 * 6, (sorted(set(kody)), round(dolgo, 2)))
    finally:
        z.rollback()
        z.close()
        m.vhod.TAYMAUT = bylo_t
        m.vhod._ZAPERT.clear()
        m.vhod._ZHALOBY.clear()
    proverka("файл отперт — входы работают", voyti(kl(), "lena", P["lena"]).status_code == 303)
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
                r = kto_.get(adres, headers=STR)
                proverka(f"{rol}: «{imya}» {adres} → 200 и /static/vhod.js",
                         r.status_code == 200 and "/static/vhod.js" in r.text, r.status_code)
        # 13.11.2026, меню, вечер 1: восемь страниц ушли из меню (nav.REDKO_ADMIN, nav.REDKO_OWNER), но
        # открываются как раньше — проверяем их здесь же, чтобы они не выпали из проверки
        for imya, adres in list(nav.REDKO_ADMIN) + (list(nav.REDKO_OWNER) if rol == "owner" else []):
            r = kto_.get(adres, headers=STR)
            proverka(f"{rol}: «{imya}» {adres} (убрана из меню 13.11) → 200 и /static/vhod.js",
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
    otm("vhod_starye_do", m.vhod._iso(time.time() - 60))
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
        otm("vhod_starye_do", bylo_do)
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


# --- группа Б: страница владельца и управление входами (план, 9.2, проверки 21–30) ------------------------

SEKRETY: list = []          # пароли и ключи, придуманные в проверках группы Б (для поиска следов)
ZAHVAT: list = []           # всё, что прогон напечатал и записал в лог


class _Tee:
    """Печать идёт и на экран, и в ZAHVAT: в конце ищем в ней пароли и ключи."""

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


def novyy_parol() -> str:
    p = pridumat()
    SEKRETY.append(p)
    return p


def vh(c, deystvie, moy=None, **polya):
    """Запрос из блока «Входы»: POST /owner/vhody со своей страницы."""
    data = {"deystvie": deystvie, **polya}
    if moy is not None:
        data["moy_parol"] = moy
    return c.post("/owner/vhody", data=data, headers=SVOI)


def klyuch_iz(r) -> str:
    """Ключ со страницы ответа (показывается один раз). Значение не печатаем."""
    x = re.search(r"id='vh-klyuch' readonly value='([^']+)'", r.text)
    k_ = x.group(1) if x else ""
    if k_:
        SEKRETY.append(k_)
    return k_


def heshi() -> dict:
    with baza_vhodov() as b:
        return {r["login"]: r["hesh"] for r in b.execute("SELECT login, hesh FROM vhody")}


def nastroyki_vhoda() -> dict:
    with osnovnaya() as o:
        return {r["key"]: r["value"] for r in o.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'vhod_%' OR key='owner_password'")}


def bear(k_):
    return {"authorization": "Bearer " + k_}


def test_vladelec_paroli():
    """Действие 1 владельца (план, 7.2): войти прежним паролем через форму и задать пароли
    Лене, Ире, Лизе и новый себе — одной кнопкой. Заодно проверки 21, 22, 28 и отказ из 26."""
    print("21–22, 28. Страница владельца: блок «Входы», подтверждение паролем, правила пароля")
    do = (heshi(), nastroyki_vhoda())
    r = kl().get("/owner", auth=STARYY_VLADELCA)
    proverka("/owner в сером окне (прежний boris): открыт, вместо блока — ссылка «войти через форму»",
             r.status_code == 200 and "/vhod?next=/owner" in r.text and "id='vhody-forma'" not in r.text, r.status_code)
    proverka("…/static/vhod.js подключён, строки «Пароль владельца» на странице больше нет",
             "/static/vhod.js" in r.text and "owner_password" not in r.text)
    proverka("/owner общему admin закрыт, как раньше (401 с окном)",
             kl().get("/owner", auth=OBSHCHIY).status_code == 401)
    c = kl()
    r = voyti(c, "boris", STARYY_VLADELCA[1], next_="/owner")
    proverka("владелец входит через форму прежним паролем (новый ещё не задан) → /owner",
             (r.status_code, r.headers.get("location")) == (303, "/owner"), r.status_code)
    r = c.get("/owner", headers=STR)
    proverka("у вошедшего через форму — блок «Входы»: четыре карточки, ключи Клода, старые входы",
             r.status_code == 200 and "id='vhody-forma'" in r.text and all(
                 f"name='parol_{lg}'" in r.text for lg in ("lena", "ira", "liza", "boris"))
             and "Ключи Клода" in r.text and "Старые входы" in r.text and "Сохранить пароли" in r.text, r.status_code)
    proverka("…у всех «пароль не задан», старые входы — «работают как раньше»",
             r.text.count("пароль не задан — войти не может") == 3 and "Работают как раньше" in r.text)
    proverka("…на странице нет ни прежних паролей, ни хешей",
             not any(s in r.text for s in (STARYY_VLADELCA[1], OBSHCHIY[1], "pbkdf2_sha256", "sha256$")))

    # 28. owner_password больше не меняется ни через /api/settings, ни через POST /owner
    chuzhoy = "stend-" + secrets.token_urlsafe(10)
    r1 = kl().post("/api/settings", json={"key": "owner_password", "value": chuzhoy}, auth=STARYY_VLADELCA)
    r2 = kl().post("/owner", data={"owner_password": chuzhoy}, auth=STARYY_VLADELCA)
    r3 = c.post("/owner", data={"owner_password": chuzhoy}, headers=SVOI)
    proverka("28. owner_password не меняется через /api/settings (400) и через POST /owner",
             (r1.status_code, r2.status_code, r3.status_code) == (400, 200, 200)
             and m.db.get_setting("owner_password") == STARYY_VLADELCA[1], (r1.status_code, r2.status_code, r3.status_code))
    proverka("…и в списке /api/settings его нет; настройки входов через /api/settings тоже не задать",
             "owner_password" not in kl().get("/api/settings", auth=STARYY_VLADELCA).json()
             and all(kl().post("/api/settings", json={"key": k_, "value": "1"}, auth=STARYY_VLADELCA).status_code == 400
                     for k_ in ("vhod_zavedeny", "vhod_starye_do", "vhod_starye_vykl", "vhod_okno_vykladki_do")))
    r = kl().post("/owner", data={"tbank_inn": "0000000000"}, auth=STARYY_VLADELCA)
    proverka("…остальные поля страницы владельца сохраняются, как раньше",
             r.status_code == 200 and "Сохранено" in r.text and m.db.get_setting("tbank_inn") == "0000000000")

    # 21. подтверждение паролем
    np = {lg: novyy_parol() for lg in ("lena", "ira", "liza", "boris")}
    polya = {f"parol_{lg}": p for lg, p in np.items()}
    r = vh(c, "paroli", None, **polya)
    proverka("21. без moy_parol → отказ словами, ничего не меняется",
             r.status_code == 400 and "Введите ваш нынешний пароль" in r.text and (heshi(), nastroyki_vhoda()) == do,
             r.status_code)
    r = vh(c, "paroli", "ne-tot-" + secrets.token_hex(5), **polya)
    proverka("…с неверным moy_parol → отказ, ничего не меняется",
             r.status_code == 400 and "не подошёл" in r.text and (heshi(), nastroyki_vhoda()) == do, r.status_code)
    r = vh(c, "paroli", OBSHCHIY[1], **polya)
    proverka("…общий пароль admin подтверждением не служит", r.status_code == 400 and (heshi(), nastroyki_vhoda()) == do)
    proverka("…новые пароли из отказанного запроса в ответ не попали", not any(p in r.text for p in np.values()))
    r = kl().post("/owner/vhody", data={"deystvie": "paroli", "moy_parol": STARYY_VLADELCA[1], **polya},
                  auth=STARYY_VLADELCA)
    proverka("…прежний boris в сером окне (скрипт) → 403, даже с верным moy_parol",
             r.status_code == 403 and (heshi(), nastroyki_vhoda()) == do, r.status_code)
    r = kl().post("/owner/vhody", data={"deystvie": "paroli", "moy_parol": STARYY_VLADELCA[1], **polya}, auth=OBSHCHIY)
    proverka("…общий admin → не пускает (401 с окном, как на всех страницах владельца)",
             r.status_code == 401 and (heshi(), nastroyki_vhoda()) == do, r.status_code)
    d = kopiya_metki(c)
    r = d.post("/owner/vhody", data={"deystvie": "paroli", "moy_parol": STARYY_VLADELCA[1], **polya},
               headers={"origin": "https://chuzhoy.example", "user-agent": UA})
    proverka("…с чужого сайта (метка владельца, чужой Origin) → 403", r.status_code == 403 and heshi() == do[0])
    r = vh(c, "pridumannoe", STARYY_VLADELCA[1])
    proverka("…неизвестное действие → отказ", r.status_code == 400 and "Неизвестное действие" in r.text)

    # 26 (часть): без нового пароля владельца старые входы не отключить
    r = vh(c, "starye_vykl:tochno", STARYY_VLADELCA[1])
    proverka("26. «Отключить старые входы» без нового пароля владельца → отказ, ничего не меняется",
             r.status_code == 400 and "Сначала задайте себе новый пароль" in r.text
             and nastroyki_vhoda() == do[1] and m.vhod.starye_vklyucheny(), r.status_code)

    # 22. правила пароля — отказ словами
    def otkaz(polya_, slova):
        r_ = vh(c, "paroli", STARYY_VLADELCA[1], **polya_)
        return r_.status_code == 400 and slova in r_.text and "Ничего не изменено" in r_.text and heshi() == do[0]
    proverka("22. короче 10 знаков → отказ", otkaz({"parol_lena": "abc-12"}, "Лена — короче 10 знаков"))
    proverka("…меньше 5 разных знаков → отказ", otkaz({"parol_ira": "abababababab"}, "Ира — слишком простой"))
    proverka("…равен общему паролю → отказ", otkaz({"parol_liza": OBSHCHIY[1]}, "Лиза — совпадает со старым паролем"))
    proverka("…новый пароль Бориса равен прежнему → отказ",
             otkaz({"parol_boris": STARYY_VLADELCA[1]}, "Борис — совпадает со старым паролем"))
    proverka("…равен логину или имени → отказ (правило пароля)",
             m.vhod.parol_goden("dlinnyy-login", "dlinnyy-login") == "совпадает с логином"
             and m.vhod.parol_goden("Екатерина-Вторая", "ekaterina", "екатерина-вторая") == "совпадает с именем")
    proverka("…служебной записи пароль задать нельзя",
             otkaz({"parol_klod": novyy_parol()}, "служебной записи обычный пароль задать нельзя")
             and otkaz({"parol_klod-vykladka": novyy_parol()}, "служебной записи"))
    proverka("…незнакомому логину — тоже", otkaz({"parol_nikto": novyy_parol()}, "нет такого входа"))
    odin = novyy_parol()
    proverka("…один пароль двоим → отказ", otkaz({"parol_lena": odin, "parol_ira": odin}, "одинаковый пароль у двух человек"))
    proverka("…один плохой пароль из четырёх — не сохраняется ни один",
             otkaz({**polya, "parol_ira": "korotkiy"}, "Ира — короче 10 знаков"))
    proverka("…пустая форма → «не введено ни одного»", otkaz({}, "Не введено ни одного нового пароля"))
    proverka("входы всё ещё не заведены: отказы ничего не включили", not m.vhod.zavedeny())

    print("Действие 1 владельца: пароли Лене, Ире, Лизе и новый себе — одной кнопкой")
    r = vh(c, "paroli", STARYY_VLADELCA[1], **polya)
    h = heshi()
    proverka("пароли сохранены: у четверых хеш PBKDF2, у служебных — пусто",
             r.status_code == 200 and "Пароли сохранены: Лена, Ира, Лиза, Борис" in r.text
             and all(h[lg].startswith("pbkdf2_sha256$600000$") for lg in np) and not h["klod"] and not h["klod-vykladka"],
             r.status_code)
    proverka("…в ответе и на странице паролей нет", not any(p in r.text for p in np.values())
             and not any(p in c.get("/owner", headers=STR).text for p in np.values()))
    P.update(np)
    proverka("…владелец, сменив пароль себе, остался в системе (свой сеанс не закрыт)",
             kto(c) == {"login": "boris", "imya": "Борис", "rol": "owner", "sposob": "forma"}, kto(c))
    with osnovnaya() as o:
        zh = o.execute("SELECT imya, sposob, kod, chto FROM deystviya WHERE put='/owner/vhody' ORDER BY id DESC LIMIT 1").fetchone()
    proverka("…в журнале: Борис, форма, «входы: пароли — …» — без паролей",
             zh is not None and tuple(zh)[:3] == ("Борис", "forma", 200) and zh["chto"] == "входы: пароли — boris, ira, lena, liza",
             zh and tuple(zh))
    r = c.get("/owner", headers=STR)
    proverka("…на странице: у всех «пароль задан», общий admin «работает до …»",
             r.text.count("пароль задан ") == 4 and "Общий admin: работает до" in r.text
             and "в сером окне ещё действует" in r.text)

    print("25. Самопорядок: после нового пароля владельца")
    proverka("форма принимает только новый пароль владельца",
             voyti(kl(), "boris", STARYY_VLADELCA[1]).status_code == 401 and voyti(kl(), "boris", P["boris"]).status_code == 303)
    proverka("…подтверждением теперь служит новый пароль, прежний — нет",
             vh(c, "snyat_pauzu:lena", STARYY_VLADELCA[1]).status_code == 400
             and vh(c, "snyat_pauzu:lena", P["boris"]).status_code == 200)
    proverka("…прежний пароль в сером окне ещё проходит (ключа выкладки ещё нет)",
             kl().get("/api/settings", auth=STARYY_VLADELCA).status_code == 200)


def test_upravlenie(klyuch_a, work):
    """Действия 2 и 4 владельца и проверки 21 (остаток), 24–27, 29, 30."""
    chasy(None)
    print("21. /owner/vhody — только владелец через форму")
    c = kl()
    proverka("владелец входит новым паролем", voyti(c, "boris", P["boris"], next_="/owner").status_code == 303)
    MOY = P["boris"]
    lena = kl()
    voyti(lena, "lena", P["lena"])
    do = (heshi(), nastroyki_vhoda())
    r = vh(lena, "klyuch:klod", MOY)
    proverka("админ (Лена через форму) → 403, даже зная пароль владельца", r.status_code == 403, r.status_code)
    r = kl().post("/owner/vhody", data={"deystvie": "klyuch:klod", "moy_parol": MOY}, headers=bear(klyuch_a))
    proverka("ключ рутин → 403", r.status_code == 403, r.status_code)
    proverka("…ничего не изменилось", (heshi(), nastroyki_vhoda()) == do)
    proverka("/api/vhod/sostoyanie: админу — 403, без входа — 401",
             (lena.get("/api/vhod/sostoyanie").status_code, kl().get("/api/vhod/sostoyanie").status_code) == (403, 401))
    m.vhod._HVOST.clear()                # хвост пишется раз в минуту — на стенде не ждём
    kl().get("/api/pult/tasks", auth=OBSHCHIY)
    r = c.get("/api/vhod/sostoyanie")
    s = r.json() if r.status_code == 200 else {}
    proverka("/api/vhod/sostoyanie владельцу: у четверых пароль задан, ключ рутин выпущен, ключ выкладки — нет",
             r.status_code == 200 and {z["login"]: z["zadan"] for z in s["vhody"]} == {
                 "lena": True, "ira": True, "liza": True, "boris": True, "klod": True, "klod-vykladka": False}, r.status_code)
    proverka("…срок старых входов, хвост общего входа, автоматика, «Важное» — на месте",
             s.get("starye", {}).get("zavedeny") is True and s["starye"]["vklyucheny"] is True and s["starye"]["do"]
             and any(h["login"] == "admin" for h in s["starye"]["hvost"]) and s.get("avtomatika_posl")
             and isinstance(s.get("vazhnoe"), list) and isinstance(s.get("klodu_otkazano"), list))
    proverka("…секретов нет: ни хешей, ни паролей, ни ключей",
             not any(x in r.text for x in ("pbkdf2", "sha256$", '"hesh', klyuch_a, *P.values())))
    proverka("…ключу рутин и прежнему boris в окне — тоже 200",
             (kl().get("/api/vhod/sostoyanie", headers=bear(klyuch_a)).status_code,
              kl().get("/api/vhod/sostoyanie", auth=STARYY_VLADELCA).status_code) == (200, 200))

    print("Действие 2 владельца и 24. Ключи Клода: показываются один раз, замена без остановки рутин")
    r = vh(c, "klyuch:lena", MOY)
    proverka("ключ человеку не выпускается", r.status_code == 400 and not klyuch_iz(r))
    proverka("прежний пароль владельца в окне проходит, пока ключ выкладки не работал",
             kl().get("/api/settings", auth=STARYY_VLADELCA).status_code == 200)
    r = vh(c, "klyuch:klod-vykladka", MOY)
    kv = klyuch_iz(r)
    proverka("ключ выкладки выпущен: 43 знака, показан на странице ответа, ответ не кешируется",
             r.status_code == 200 and len(kv) == 43 and "показывается один раз" in r.text
             and "KidsUP выкладка" in r.text and r.headers.get("cache-control") == "no-store", r.status_code)
    proverka("…второй раз его не увидеть: ни на /owner, ни в состоянии",
             kv not in c.get("/owner", headers=STR).text and kv not in c.get("/api/vhod/sostoyanie").text)
    proverka("…в базе — только SHA-256", heshi()["klod-vykladka"].startswith("sha256$") and kv not in heshi()["klod-vykladka"])
    proverka("25. ключ выкладки ещё не делал запросов — прежний boris в окне проходит",
             kl().get("/api/settings", auth=STARYY_VLADELCA).status_code == 200)
    proverka("ключ выкладки: /api/kto → klod-vykladka", login_kto(kl(), headers=bear(kv)) == "klod-vykladka")
    proverka("25. после первого запроса ключом выкладки прежний пароль владельца в окне не проходит",
             (kl().get("/api/settings", auth=STARYY_VLADELCA).status_code,
              kl().get("/api/pult/tasks", auth=STARYY_VLADELCA).status_code) == (401, 401))
    proverka("…а общий admin ещё работает", kl().get("/api/pult/tasks", auth=OBSHCHIY).status_code == 200)
    proverka("…на странице: «Прежний пароль владельца: уже не действует»",
             "Прежний пароль владельца: уже не действует" in c.get("/owner", headers=STR).text)
    r = kl().post("/owner/vhody", data={"deystvie": "klyuch:klod", "moy_parol": MOY}, headers=bear(kv))
    proverka("21. ключ выкладки на /owner/vhody → 403", r.status_code == 403, r.status_code)

    def zhiv(k_):
        return kl().get("/api/kto", headers=bear(k_)).status_code
    r = vh(c, "klyuch:klod", MOY)
    k1 = klyuch_iz(r)
    proverka("новый ключ рутин выпущен, подпись — в какую форму вставлять",
             r.status_code == 200 and len(k1) == 43 and k1 != klyuch_a and "в основном окружении" in r.text)
    proverka("24. прежний ключ рутин живёт, пока новым не сделан ни один запрос", zhiv(klyuch_a) == 200)
    proverka("…первый запрос новым — прежний гаснет", zhiv(k1) == 200 and zhiv(klyuch_a) == 401)
    r = vh(c, "klyuch:klod", MOY)
    k2 = klyuch_iz(r)
    proverka("…ещё одна замена: прежний жив", bool(k2) and zhiv(k1) == 200)
    try:
        chasy(time.time() + 86400 + 120)
        proverka("…но не дольше 24 часов: через сутки прежний не принимается", zhiv(k1) == 401)
    finally:
        chasy(None)
    proverka("…а новый работает", zhiv(k2) == 200)
    r = vh(c, "klyuch:klod", MOY, srazu="1")
    k3 = klyuch_iz(r)
    proverka("…с галочкой «подозрение на утечку» прежний гаснет сразу", bool(k3) and zhiv(k2) == 401 and zhiv(k3) == 200)
    with osnovnaya() as o:
        vazh = [r_["chto"] for r_ in o.execute("SELECT chto FROM deystviya WHERE vazhnoe=1")]
    proverka("выпуск ключей — в «Важном» (без самих ключей)",
             "новый ключ выкладки" in vazh and "новый ключ рутин" in vazh and "новый ключ рутин (прежний погашен сразу)" in vazh
             and not any(k_ in x for x in vazh for k_ in (kv, k1, k2, k3)), vazh[-5:])

    print("Карточки: «Выйти везде», «Отключить вход», пауза, общий компьютер")
    proverka("сеанс Лены жив", login_kto(lena) == "lena")
    r = vh(c, "vyiti_vezde:lena", MOY)
    proverka("«Выйти везде» у Лены: её сеанс закрыт, владелец остался",
             r.status_code == 200 and lena.get("/api/kto").status_code == 401 and login_kto(c) == "boris", r.status_code)
    vtoroy = kl()
    voyti(vtoroy, "boris", MOY)
    r = vh(c, "vyiti_vezde:boris", MOY)
    proverka("«Выйти на других устройствах» у себя: этот сеанс жив, второй закрыт",
             r.status_code == 200 and login_kto(c) == "boris" and vtoroy.get("/api/kto").status_code == 401)
    voyti(lena, "lena", P["lena"])
    r = vh(c, "vkl:lena:0", MOY)
    proverka("«Отключить вход»: сеанс закрыт, войти нельзя",
             r.status_code == 200 and lena.get("/api/kto").status_code == 401 and voyti(kl(), "lena", P["lena"]).status_code == 401)
    proverka("…«Включить вход» возвращает", vh(c, "vkl:lena:1", MOY).status_code == 200
             and voyti(kl(), "lena", P["lena"]).status_code == 303)
    r = vh(c, "vkl:boris:0", MOY)
    proverka("…свой вход владельцу не отключить", r.status_code == 400 and "отключить нельзя" in r.text)
    x = kl("7.7.7.7")
    for _ in range(5):
        voyti(x, "ira", "ne-tot-" + secrets.token_hex(5))
    proverka("Ира заперта после пяти промахов — на странице «пауза» и кнопка «Снять паузу»",
             voyti(x, "ira", P["ira"]).status_code == 429 and "snyat_pauzu:ira" in c.get("/owner", headers=STR).text)
    proverka("…«Снять паузу» — Ира входит сразу",
             vh(c, "snyat_pauzu:ira", MOY).status_code == 200 and voyti(x, "ira", P["ira"]).status_code == 303)
    voyti(x, "liza", P["liza"])                      # второй человек в том же браузере — общий компьютер
    ustr = m.vhod.obshchie_ustroystva()
    moe = [u for u in ustr if set(u["imena"]) == {"Ира", "Лиза"}]
    proverka("общий компьютер виден владельцу: «входили: Ира, Лиза»",
             len(moe) == 1 and f"ustr_lichnoe:{moe[0]['id']}" in c.get("/owner", headers=STR).text, len(ustr))
    r = vh(c, "ustr_lichnoe:" + (moe[0]["id"] if moe else "0" * 64), MOY)
    proverka("…«Считать личным» — компьютер больше не общий",
             r.status_code == 200 and not any(u["id"] == moe[0]["id"] for u in m.vhod.obshchie_ustroystva()))
    proverka("…чужой номер компьютера → отказ", vh(c, "ustr_lichnoe:" + "0" * 64, MOY).status_code == 400)

    print("29. Временный вход на 24 часа; занятые имена")
    zanyatye = [vh(c, "dobavit", MOY, novoe_imya="Проверка", novyy_login=lg).status_code
                for lg in ("admin", "boris", "klod", "klod-vykladka", "avtomatika", "vyhod", "owner", "root")]
    proverka("занятые логины не выдаются", zanyatye == [400] * 8, zanyatye)
    plohie = [vh(c, "dobavit", MOY, novoe_imya="Проверка", novyy_login=lg).status_code
              for lg in ("ab", "1abc", "оля", "a b c", "x" * 21, "lena")]
    proverka("кривой логин (короткий, с цифры, по-русски, длинный) и уже существующий — отказ", plohie == [400] * 6, plohie)
    proverka("имя, которое уже есть (Лена), и пустое имя — отказ",
             (vh(c, "dobavit", MOY, novoe_imya="лена", novyy_login="lena2").status_code,
              vh(c, "dobavit", MOY, novoe_imya=" ", novyy_login="lena3").status_code) == (400, 400))
    with baza_vhodov() as b:
        n = b.execute("SELECT COUNT(*) FROM vhody").fetchone()[0]
    proverka("…после отказов записей по-прежнему шесть", n == 6, n)
    proverka("логин, равный имени другого входа, и служебное слово вместо имени — отказ словами "
             "(замечание проверки 12.11)",
             (m.vhod.dobavit("lena-novaya", "admin"), m.vhod.dobavit("avt", "Avtomatika"),
              m.vhod.dobavit("boris2", "klod-vykladka"))
             == ("это имя занято",) * 3 and m.vhod._zapis("lena-novaya") is None)
    with baza_vhodov() as b:      # старая запись, чьё имя совпадает с чужим логином (так могло остаться от прежней версии)
        b.execute("INSERT INTO vhody (login, imya, rol) VALUES ('aaa-proverka', 'Vasya', 'admin')")
    try:
        proverka("…новый логин vasya при живом имени «Vasya» не выдаётся",
                 m.vhod.dobavit("vasya", "Петя") == "логин совпадает с именем другого входа"
                 and vh(c, "dobavit", MOY, novoe_imya="Петя", novyy_login="vasya").status_code == 400
                 and m.vhod._zapis("vasya") is None)
        with baza_vhodov() as b:
            b.execute("INSERT INTO vhody (login, imya, rol) VALUES ('vasya', 'Петя', 'admin')")
        proverka("…а если такая пара уже есть — по вводу «vasya» находится логин vasya, а не чужое имя",
                 (m.vhod.login_po_vvodu("vasya"), m.vhod.login_po_vvodu("VASYA"), m.vhod.login_po_vvodu("Петя"))
                 == ("vasya", "vasya", "vasya"))
    finally:
        with baza_vhodov() as b:
            b.execute("DELETE FROM vhody WHERE login IN ('aaa-proverka', 'vasya')")
        m.vhod._obnovit_kopiyu()
    r = vh(c, "dobavit", MOY, novoe_imya="Замена", novyy_login="zamena-stoyka", sutki="1")
    proverka("временный вход добавлен: роль «админ», срок — сутки, пароля нет",
             r.status_code == 200 and "Вход добавлен на 24 часа" in r.text and "name='parol_zamena-stoyka'" in r.text
             and "временный, до " in r.text and heshi().get("zamena-stoyka") == "", r.status_code)
    with baza_vhodov() as b:
        z = b.execute('SELECT rol, "do" FROM vhody WHERE login=?', ("zamena-stoyka",)).fetchone()
    proverka("…в базе: admin, срок через 24 часа",
             z is not None and z["rol"] == "admin" and 86300 < m.vhod._iz_iso(z["do"]) - time.time() <= 86400)
    r = vh(c, "paroli", MOY, **{"parol_zamena-stoyka": "zamena-stoyka"})
    proverka("22. пароль, равный логину, → отказ", r.status_code == 400 and "совпадает с логином" in r.text)
    pz = novyy_parol()
    proverka("пароль временному входу задан", vh(c, "paroli", MOY, **{"parol_zamena-stoyka": pz}).status_code == 200)
    zam = kl()
    proverka("временный вход работает — по имени и по логину, права админа",
             voyti(zam, "Замена", pz).status_code == 303 and kto(zam).get("rol") == "admin"
             and zam.get("/owner", headers=STR).status_code == 403)
    try:
        chasy(time.time() + 86400 + 120)
        proverka("через сутки временный вход не действует: сеанс закрыт, войти нельзя",
                 zam.get("/api/kto").status_code == 401 and voyti(kl(), "zamena-stoyka", pz).status_code == 401)
    finally:
        chasy(None)

    print("27. Срок старых входов и автовыключение")
    bylo = nastroyki_vhoda()

    def srok(sek):
        otm("vhod_starye_do", m.vhod._iso(time.time() + sek))

    def klod_rabotal(sek_nazad):
        with baza_vhodov() as b:
            b.execute("UPDATE vhody SET posl_zapros=? WHERE login='klod'",
                      (m.vhod._iso(time.time() - sek_nazad) if sek_nazad is not None else None,))

    def vazhnoe_posl():
        with osnovnaya() as o:
            r_ = o.execute("SELECT chto FROM deystviya WHERE vazhnoe=1 ORDER BY id DESC LIMIT 1").fetchone()
        return r_["chto"] if r_ else ""
    srok(-60)
    proverka("срок в прошлом → старые входы выключены",
             not m.vhod.starye_vklyucheny() and kl().get("/api/pult/tasks", auth=OBSHCHIY).status_code == 401)
    otm("vhod_starye_do", None)
    proverka("отметка «заведены» есть, а записи о сроке нет → выключены",
             m.vhod.zavedeny() and not m.vhod.starye_vklyucheny()
             and kl().get("/api/pult/tasks", auth=OBSHCHIY).status_code == 401)
    proverka("…автопилот без записи о сроке ничего не трогает", m.vhod.starye_po_sroku() == "")
    srok(5 * 3600)
    klod_rabotal(60)
    proverka("до срока больше двух часов — автопилот ничего не делает",
             m.vhod.starye_po_sroku() == "" and m.vhod.starye_vklyucheny())
    srok(3600)
    klod_rabotal(2 * 86400)
    r = m.vhod.starye_po_sroku()
    novyy = m.vhod._iz_iso(m.db.get_setting("vhod_starye_do"))
    proverka("до срока меньше двух часов, ключ рутин не работал сутки → срок сдвинут на сутки, общий вход жив",
             r == "sdvinuto" and 86400 + 3500 < novyy - time.time() <= 86400 + 3600 and m.vhod.starye_vklyucheny()
             and kl().get("/api/pult/tasks", auth=OBSHCHIY).status_code == 200, r)
    proverka("…владельцу — строка «Важное» с причиной",
             vazhnoe_posl() == "общий вход не отключён, срок сдвинут на сутки: ключ рутин Клода не работал последние сутки",
             vazhnoe_posl())
    proverka("…owner_password не тронут", m.db.get_setting("owner_password") == STARYY_VLADELCA[1])
    srok(-600)
    klod_rabotal(None)
    r = m.vhod.starye_po_sroku()
    proverka("срок прошёл, пока сервер лежал, а ключ рутин не работал → тоже сдвиг, рутины не остаются без входа",
             r == "sdvinuto" and m.vhod.starye_vklyucheny(), r)
    srok(3600)
    klod_rabotal(600)
    r = m.vhod.starye_po_sroku()
    proverka("ключ рутин работал, у владельца новый пароль → в срок старые входы выключаются сами",
             r == "vyklyucheny" and not m.vhod.starye_vklyucheny()
             and m.db.get_setting("owner_password") not in ("", STARYY_VLADELCA[1])
             and vazhnoe_posl() == "старые входы отключены: сами, по сроку", r)
    proverka("…повторный тик автопилота ничего не меняет", m.vhod.starye_po_sroku() == "")

    print("27. Отметки входов — в двух хранилищах: старая копия основной базы не открывает общий вход")
    def v_fayle():
        with baza_vhodov() as b:
            return {r_["klyuch"]: r_["znachenie"] for r_ in b.execute("SELECT klyuch, znachenie FROM otmetki")}
    otmetki = {k_: v_ for k_, v_ in nastroyki_vhoda().items() if k_ in m.vhod.OTMETKI}
    proverka("после отключения отметки одинаковы в settings и в файле входов (и в его копии)",
             set(otmetki) == set(m.vhod.OTMETKI) and v_fayle() == otmetki
             and dict(sqlite3.connect(m.vhod._put_kopii()).execute("SELECT klyuch, znachenie FROM otmetki")) == otmetki,
             (sorted(otmetki), sorted(v_fayle())))
    with osnovnaya() as o:      # основную базу восстановили из копии, снятой до заведения входов
        o.execute("DELETE FROM settings WHERE key IN ('vhod_zavedeny', 'vhod_starye_do', 'vhod_starye_vykl')")
    r = kl().get("/api/kto", auth=OBSHCHIY)
    proverka("в основной базе отметок нет (старая копия) → общий admin всё равно не принимается",
             r.status_code == 401 and not m.vhod.starye_vklyucheny() and m.vhod.zavedeny(), r.status_code)
    proverka("…отметки вернулись в основную базу сами, владельцу — строка «Важное»",
             {k_: v_ for k_, v_ in nastroyki_vhoda().items() if k_ in m.vhod.OTMETKI} == otmetki
             and vazhnoe_posl().startswith("отметки входов восстановлены из файла входов"), vazhnoe_posl())
    with osnovnaya() as o:
        o.execute("UPDATE settings SET value='' WHERE key='vhod_zavedeny'")
    proverka("пустая отметка vhod_zavedeny в основной базе → общий admin не принимается",
             kl().get("/api/kto", auth=OBSHCHIY).status_code == 401 and m.db.get_setting("vhod_zavedeny") == "1")
    orig_n = m.vhod._nastroyki

    def ne_chitaetsya(*a):
        raise sqlite3.OperationalError("стенд: database is locked")
    m.vhod._ZHALOBY.clear()
    try:
        m.vhod._nastroyki = ne_chitaetsya
        m.vhod._POSL_STARYE[0] = True          # свежий процесс: удачного чтения ещё не было
        r = kl(raise_server_exceptions=False).get("/api/kto", auth=OBSHCHIY)
        r2 = kl(raise_server_exceptions=False).post("/api/plan/inbox", json={"who": "Лена", "text": "x"}, auth=OBSHCHIY)
        proverka("сразу после перезапуска настройки не читаются → общий admin не принимается (решает файл входов)",
                 (r.status_code, r2.status_code) == (401, 401) and not m.vhod.starye_vklyucheny(),
                 (r.status_code, r2.status_code))
    finally:
        m.vhod._nastroyki = orig_n
    orig_f = m.vhod._otmetki_fayla

    def fayl_ne_chitaetsya():
        raise sqlite3.DatabaseError("стенд: файл входов сломан")
    try:
        m.vhod._otmetki_fayla = fayl_ne_chitaetsya
        proverka("файл входов не читается → решает основная база: выключены", not m.vhod.starye_vklyucheny())
        m.vhod._nastroyki = ne_chitaetsya
        proverka("не читаются оба хранилища → последнее прочитанное значение", not m.vhod.starye_vklyucheny())
    finally:
        m.vhod._nastroyki, m.vhod._otmetki_fayla = orig_n, orig_f
        m.vhod._ZHALOBY.clear()
    # переходные дни: расхождение срока сводится к меньшему, общий вход при этом жив
    otm("vhod_starye_vykl", None)
    otm("vhod_starye_do", m.vhod._iso(time.time() + 3 * 86400))
    m.db.set_setting("vhod_starye_do", m.vhod._iso(time.time() + 6 * 86400))      # правка мимо файла входов
    proverka("переходные дни, сроки разошлись → общий admin работает, срок — меньший из двух, в обоих хранилищах",
             m.vhod.starye_vklyucheny() and kl().get("/api/pult/tasks", auth=OBSHCHIY).status_code == 200
             and m.db.get_setting("vhod_starye_do") == v_fayle()["vhod_starye_do"]
             and m.vhod._iz_iso(m.db.get_setting("vhod_starye_do")) - time.time() < 3 * 86400 + 5)
    with osnovnaya() as o:
        o.execute("DELETE FROM settings WHERE key IN ('vhod_zavedeny', 'vhod_starye_do')")
    proverka("переходные дни, основная база из старой копии → общий admin ещё работает до срока, отметки вернулись",
             kl().get("/api/pult/tasks", auth=OBSHCHIY).status_code == 200 and m.vhod.zavedeny()
             and m.db.get_setting("vhod_starye_do") == v_fayle()["vhod_starye_do"])
    m.vhod._POSL_SROK[0] = 0.0
    m.vhod.avtopilot_tik()
    proverka("…тик из цикла автопилота — не чаще раза в 10 минут и без ошибок", m.vhod._POSL_SROK[0] > 0)
    # стенд возвращаем к состоянию «переходные дни» — дальше то же самое делает владелец кнопкой
    otm("vhod_starye_vykl", None)
    for k_, v_ in bylo.items():
        if k_ in m.vhod.OTMETKI:
            otm(k_, v_)
        else:
            m.db.set_setting(k_, v_)
    proverka("стенд возвращён в переходные дни: общий admin работает",
             m.vhod.starye_vklyucheny() and kl().get("/api/pult/tasks", auth=OBSHCHIY).status_code == 200)

    print("Аварийное окно выкладки")
    r = vh(c, "okno_vykladki", MOY)
    okno = m.vhod._iz_iso(m.db.get_setting("vhod_okno_vykladki_do"))
    proverka("«Разрешить Клоду выкладку на 2 часа»: срок окна записан, на странице и в состоянии виден",
             r.status_code == 200 and okno and 7100 < okno - time.time() <= 7200
             and "Клоду разрешена выкладка ключом рутин до" in r.text
             and c.get("/api/vhod/sostoyanie").json()["okno_vykladki_do"], r.status_code)

    print("Действие 4 владельца и 26. «Отключить старые входы»")
    klod_rabotal(2 * 86400)
    m.vhod._HVOST.clear()
    kl().get("/api/pult/tasks", auth=OBSHCHIY, headers={"user-agent": UA, "sec-fetch-mode": "cors"})
    with baza_vhodov() as b:             # будто Ира через форму ещё не входила
        b.execute("UPDATE vhody SET forma_vhod=NULL WHERE login='ira'")
    do = nastroyki_vhoda()
    r = vh(c, "starye_vykl", MOY)
    proverka("предупреждения: ключ рутин молчит сутки, общим входом пользовались, не все входили через форму",
             r.status_code == 200 and "Перед отключением старых входов" in r.text
             and "не делал запросов последние сутки" in r.text and "Общим входом admin пользовались" in r.text
             and "Ещё не входили через форму: Ира." in r.text and "starye_vykl:tochno" in r.text, r.status_code)
    proverka("…пока не подтверждено второй раз — ничего не меняется",
             nastroyki_vhoda() == do and kl().get("/api/pult/tasks", auth=OBSHCHIY).status_code == 200)
    r = vh(c, "starye_vykl:tochno", "ne-tot-" + secrets.token_hex(5))
    proverka("…второе подтверждение тоже требует пароль", r.status_code == 400 and nastroyki_vhoda() == do)
    klod_rabotal(60)
    r = vh(c, "starye_vykl:tochno", MOY)
    proverka("старые входы отключены", r.status_code == 200 and "Старые входы отключены" in r.text
             and not m.vhod.starye_vklyucheny(), r.status_code)
    kody = [kl().get(a, auth=OBSHCHIY).status_code for a in ("/api/pult/tasks", "/api/settings", "/api/kto", "/export/raw/rooms.json")]
    kody += [kl().get(a, auth=STARYY_VLADELCA).status_code for a in ("/api/pult/tasks", "/api/settings", "/owner")]
    kody.append(kl().post("/api/deploy", content=b"", auth=STARYY_VLADELCA).status_code)
    proverka("общий admin и прежний boris не принимаются нигде", kody == [401] * 8, kody)
    proverka("…и в форме входа общий пароль и прежний пароль владельца не годятся",
             (voyti(kl(), "admin", OBSHCHIY[1]).status_code, voyti(kl(), "boris", STARYY_VLADELCA[1]).status_code) == (401, 401))
    r = kl().get("/liniya?phone=79990000000", headers=STR, auth=OBSHCHIY)
    proverka("страница с присланным старым паролем уходит на форму входа",
             r.status_code == 303 and r.headers.get("location", "").startswith("/vhod?next=/liniya"), r.status_code)
    r = kl().get("/api/pult/tasks", auth=OBSHCHIY)
    proverka("…скрипту — 401", r.status_code == 401)
    own = m.db.get_setting("owner_password")
    proverka("owner_password затёрт длинной случайной строкой (не удалён)",
             len(own) >= 40 and own not in (STARYY_VLADELCA[1], OBSHCHIY[1], P["boris"]))

    def staraya_check_owner(user, pw):
        """Проверка владельца, как в коде до 12.11.2026 (на случай отката на старую версию)."""
        own_ = m.db.get_setting("owner_password")
        if not own_:
            return user == m.config.APP_USER and pw == m.config.APP_PASSWORD
        return user == "boris" and pw == own_
    proverka("…прежняя логика check_owner с затёртой настройкой admin не пускает",
             not staraya_check_owner(*OBSHCHIY) and not staraya_check_owner(*STARYY_VLADELCA))
    proverka("…в «Важном» — «старые входы отключены: владелец»", vazhnoe_posl() == "старые входы отключены: владелец", vazhnoe_posl())
    proverka("личные входы, ключи и автоматика работают",
             login_kto(c) == "boris" and voyti(kl(), "lena", P["lena"]).status_code == 303
             and zhiv(k3) == 200 and zhiv(kv) == 200 and kl().get("/api/duty", auth=m.db.admin_auth()).status_code == 200)
    r = c.get("/owner", headers=STR)
    proverka("на странице: «Отключены», кнопки отключения больше нет, обратной кнопки тоже",
             "Отключены" in r.text and "value='starye_vykl'" not in r.text and "ключить обратно" not in r.text)
    proverka("повторное нажатие (старый запрос) ничего не ломает", vh(c, "starye_vykl:tochno", MOY).status_code == 200
             and not m.vhod.starye_vklyucheny())
    proverka("автопилот после отключения срок не сдвигает", m.vhod.starye_po_sroku() == "" and not m.vhod.starye_vklyucheny())

    print("Подтверждение паролем: перебор")
    z2 = kl("6.6.6.6")
    z2.cookies.set(m.vhod.COOKIE, c.cookies.get(m.vhod.COOKIE), domain="app.kidsup.ru")
    kody = [vh(z2, "snyat_pauzu:lena", "ne-tot-" + secrets.token_hex(5)).status_code for _ in range(5)]
    r = vh(z2, "snyat_pauzu:lena", MOY)
    proverka("5 неверных подтверждений подряд → пауза: даже верный пароль не проверяется",
             kody == [400] * 5 and r.status_code == 400 and "Слишком много попыток" in r.text, kody)
    proverka("…вход владельца при этом не заперт", voyti(kl("6.6.6.6"), "boris", MOY).status_code == 303)

    print("30. Консольная команда: python3 -m app.vhod parol <логин>")
    napechatano = []
    pk = novyy_parol()
    kod = m.vhod._konsol(["parol", "Борис"], vvod=lambda podskazka: napechatano.append(podskazka) or pk,
                         pechat=napechatano.append)
    proverka("пароль владельца задан из консоли, прежние сеансы закрыты",
             kod == 0 and c.get("/api/kto").status_code == 401 and voyti(kl(), "boris", pk).status_code == 303
             and voyti(kl(), "boris", MOY).status_code == 401, kod)
    proverka("…пароль не напечатан, в «Важном» — строка без пароля",
             not any(pk in str(x) for x in napechatano) and vazhnoe_posl() == "пароль задан из консоли сервера: boris")
    P["boris"] = pk
    raznye = iter([novyy_parol(), novyy_parol()])
    proverka("…пароли не совпали → ничего не меняется; плохой пароль — отказ словами; служебной записи — нельзя",
             m.vhod._konsol(["parol", "boris"], vvod=lambda _p: next(raznye), pechat=napechatano.append) == 1
             and m.vhod._konsol(["parol", "boris"], vvod=lambda _p: "korotkiy", pechat=napechatano.append) == 1
             and m.vhod._konsol(["parol", "klod"], vvod=lambda _p: pk, pechat=napechatano.append) == 1
             and m.vhod._konsol(["parol", "nikto"], vvod=lambda _p: pk, pechat=napechatano.append) == 1
             and m.vhod._konsol(["chto-to"], pechat=napechatano.append) == 2
             and voyti(kl(), "boris", pk).status_code == 303 and not heshi()["klod"].startswith("pbkdf2"))
    import subprocess
    pl = novyy_parol()
    okr = {k_: v_ for k_, v_ in os.environ.items() if k_ not in ("APP_PASSWORD", "KIDSUP_BEZ_VHODA")}
    okr["KIDSUP_DATA_DIR"] = str(work)
    pr = subprocess.run([sys.executable, "-m", "app.vhod", "parol", "liza"], cwd=KOREN, env=okr, input=f"{pl}\n{pl}\n",
                        capture_output=True, text=True, timeout=120, start_new_session=True)
    proverka("настоящий запуск команды в отдельном процессе: код 0, Лиза входит новым паролем",
             pr.returncode == 0 and "Пароль задан: Лиза (liza)" in pr.stdout
             and voyti(kl(), "liza", pl).status_code == 303 and voyti(kl(), "liza", P["liza"]).status_code == 401,
             (pr.returncode, pr.stderr[-300:].replace(pl, "***")))
    proverka("…пароль не напечатан ни в выводе, ни в ошибках", pl not in pr.stdout and pl not in pr.stderr)
    P["liza"] = pl
    return [kv, k1, k2, k3]


def test_hranenie(work):
    print("23. Хранение: паролей и ключей нет ни в копии базы, ни в логе прогона")
    import gzip
    sekrety = [s for s in list(P.values()) + SEKRETY if s]
    c = kl()
    voyti(c, "boris", P["boris"])
    kv = next((s for s in SEKRETY if len(s) == 43 and login_kto(kl(), headers=bear(s)) == "klod-vykladka"), "")
    r = kl().get("/api/backup/db", headers=bear(kv))
    proverka("копия базы снимается ключом выкладки", r.status_code == 200 and len(r.content) > 1000, r.status_code)
    raw = gzip.decompress(r.content) if r.status_code == 200 else b""
    proverka("…в ней нет ни одного пароля и ключа, нет хешей паролей", raw and not any(s.encode() in raw for s in sekrety)
             and b"pbkdf2_sha256$" not in raw)
    kop = Path(work) / "kopiya_proverka.db"
    kop.write_bytes(raw)
    with sqlite3.connect(kop) as k_:
        tablicy = {x[0] for x in k_.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    kop.unlink()
    proverka("…и нет таблиц входов (vhody, seansy, ustroystva, popytki, hvost)",
             tablicy and not tablicy & {"vhody", "seansy", "ustroystva", "popytki", "hvost"})
    with osnovnaya() as o:
        zh = " ".join(" ".join(str(x) for x in r_) for r_ in o.execute("SELECT * FROM deystviya"))
        nastr = " ".join(f"{r_[0]}={r_[1]}" for r_ in o.execute("SELECT key, value FROM settings"))
    proverka("журнал действий и настройки: ни одного пароля и ключа", not any(s in zh or s in nastr for s in sekrety))
    for f in sorted(Path(work).iterdir()):
        if f.is_file():
            raw = f.read_bytes()
            proverka(f"{f.name}: ни одного пароля и ключа побайтно", not any(s.encode() in raw for s in sekrety))
    log_ = "".join(ZAHVAT)
    proverka(f"лог прогона ({len(log_) // 1000} КБ печати и записей журнала сервера): ни одного пароля и ключа",
             len(log_) > 5000 and not any(s in log_ for s in sekrety + [OBSHCHIY[1], STARYY_VLADELCA[1], PED]))


# --- хранение и следы ---------------------------------------------------------------------------------------

def test_sledy(klyuch, work):
    print("Следы: паролей и ключей нет ни в файлах, ни в журнале")
    sekrety = [p for p in P.values()] + [klyuch] + SEKRETY
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



# --- группа В (план, 9.3): права и «кто вошёл» --------------------------------------------------

# Опись адресов, которые зовут рутины и помощники ассистента (12.11.2026): scratchpad-помощники,
# docs/rabota/*.py, опись рутин из nav_testy.RUTINY_VHOD и таблица 5.3 плана. Ключ рутин обязан
# проходить их все. Адреса выкладки (/api/deploy, /api/backup/db, /api/settings) сюда не входят:
# они переезжают в окружение «KidsUP выкладка» под ключ выкладки.
OPIS_RUTIN = """
GET /api/plan/inbox · POST /api/plan/inbox · POST /api/plan/inbox/done · GET /api/promo/grafik · GET /api/calls/list
GET /api/calls/recording · POST /api/gruppy/otvet · GET /api/gruppy/soobsheniya · GET /export/raw/{table}.json
GET /api/sync/status · GET /api/day/firsts · GET /api/mesta · GET /api/mesta/razrez · GET /api/oplata/plan
GET /api/waiting · GET /api/yavka · POST /api/crm/comment · GET /api/crm/user · GET /api/crm/poisk
GET /api/pult/tasks · POST /api/pult/tasks · POST /api/pult/done · GET /api/obzvon-dnya · GET /api/kadry
POST /api/kadry/ocenka · GET /kadry/file/{day}/{name} · POST /api/crm/roistat-merge · POST /api/roistat/auto-cards
GET /api/duty · GET /api/dialogs · GET /api/zayavki · GET /api/dolgi · GET /api/blank-grupp
POST /api/blank-grupp/edit · GET /api/perepiska/kto · GET /api/wazzup/raw · GET /api/wazzup/channels-map
GET /base/{slug} · GET /api/kto · GET /api/vhod/sostoyanie
POST /api/boris-docs · GET /api/boris-docs · POST /api/boris-docs/remove · GET /boris/docs · GET /boris/docs/{slug}
GET /api/admin-docs · POST /api/admin-docs · POST /api/admin-docs/remove
GET /api/doc-state/{doc} · POST /api/doc-state/{doc} · GET /api/doc-state/{doc}/log
GET /api/tbank/get · GET /api/komtet/cheki · POST /api/ads/vk · POST /api/ads/direct
GET /api/mkweb/history · POST /api/mkweb/history/start · GET /api/mkweb/history/result
POST /api/hooppy/post · GET /api/hooppy/posty · POST /api/hooppy/udalit · GET /api/crm/raw-user · GET /api/istochniki
"""
# Список 5.2 плана: что 12.11.2026 переведено с общего входа на вход владельца (и что уже перевёл шаг 1).
SPISOK_52 = """
GET /export/leads.csv · GET /export/students.csv · GET /export/payments.csv · GET /export/lessons.csv
GET /export/attendance.csv · GET /export/raw/{table}.json · GET /api/audit
POST /api/audit/run · GET /api/sverka-chestnosti · GET /api/nabor/analiz · GET /clicks
GET /api/clicks/summary · GET /kadry/file/{day}/{name} · GET /api/istochniki
POST /api/istochniki/spravochnik · GET /api/boris/tasks · POST /api/boris/tasks · POST /api/boris/status
POST /api/chaty/send · POST /api/chaty/deliver
"""
# Четыре страницы из списка 5.2, которые до 12.11 стояли в меню админов или на которые ведут их
# методички. Решение Бориса при выкатке шага 1 (nav_testy, VLADELCU_NA_PULT): админа уводим на
# пульт (303) без окна пароля и без страницы отказа. Они стоят под общим входом, а не владельцу
# их закрывает сам обработчик (main._vladelcu) — по роли вошедшего. Данных админ не получает.
NA_PULT = ("/journal", "/istochniki", "/sverka-chestnosti", "/audit")
# Страницы владельца, которые закрыты входом владельца (check_owner): админу — 403 со страницей
# «Только для владельца». Эти пять — для проверки после выкладки (план, 7.1 и 9.4).
PYAT_STRANIC = ("/owner", "/settings", "/boris/docs", "/arhiv", "/clicks")
# Остались под общим входом вопреки списку 5.2 — каждое с причиной (см. отчёт исполнителя В).
OSTALIS_ADMINAM = {
    "POST /api/audit/resolve": "кнопки «принято / исправлено» жмут админы на /pravila-kontrol",
    "GET /promo": "решение Бориса 28.10 «промоутерами управляют админы», ссылка с /promo/grafik",
}
VYKLADKA4 = ("/api/deploy", "/api/restart", "/api/backup/db", "/api/settings")


def _spisok(tekst: str) -> list:
    return [tuple(x.strip().split(" ", 1)) for x in tekst.replace("\n", " · ").split(" · ") if x.strip()]


def vhod_marshrutov() -> dict:
    """«МЕТОД путь» → какая проверка входа стоит: owner, auth, pauth или ''."""
    out = {}
    for r in m.app.routes:
        if not hasattr(r, "dependant"):
            continue
        calls = {d.call for d in r.dependant.dependencies}
        vid = ("owner" if m.check_owner in calls else "auth" if m.check_auth in calls
               else "pauth" if m.check_pedagog_or_admin in calls else "")
        for met in sorted(r.methods - {"HEAD"}):
            out[f"{met} {r.path}"] = vid
    return out


def vladelcheskie() -> list:
    return sorted(tuple(x.split(" ", 1)) for x, v in vhod_marshrutov().items() if v == "owner")


def dopusk(metod, put, hdr=None, auth=None, metka=None, chem="owner"):
    """Только проверка входа, без самого обработчика (выкладку, рассылку и перезапуск тест не
    запускает): 200 — пускает; "403в" — «Только для владельца»; иначе код отказа."""
    from fastapi import HTTPException
    from starlette.requests import Request
    put = adres_puti(put)
    h = {"host": "app.kidsup.ru", **(hdr or {})}
    if metka:
        h["cookie"] = f"{m.vhod.COOKIE}={metka}"
    scope = {"type": "http", "http_version": "1.1", "method": metod, "scheme": "https", "path": put,
             "raw_path": put.encode(), "query_string": b"", "client": ("203.0.113.77", 40000),
             "server": ("app.kidsup.ru", 443), "state": {},
             "headers": [(k.lower().encode(), str(v).encode()) for k, v in h.items()]}
    try:
        (m.check_owner if chem == "owner" else m.check_auth)(_cred(*auth) if auth else None, Request(scope))
    except m.TolkoVladelcu:
        return "403в"
    except m.vhod.NuzhenVhod:
        return 303
    except HTTPException as e:
        return e.status_code
    return 200


def po_vsem(adresa, **kw) -> dict:
    """Итоги dopusk по списку адресов: {итог: [адреса]}."""
    out: dict = {}
    for met, put in adresa:
        hdr = dict(kw.get("hdr") or {})
        if kw.get("metka") and met not in ("GET", "HEAD", "OPTIONS"):
            hdr.update(SVOI)                           # изменяющий запрос человека — со своей страницы
        out.setdefault(dopusk(met, put, **{**kw, "hdr": hdr}), []).append(f"{met} {put}")
    return out


def metka(c) -> str:
    return c.cookies.get(m.vhod.COOKIE)


class _BezPerezapuska:
    """Выкладка и перезапуск на стенде: архив не распаковывается, служба не перезапускается."""

    def __enter__(self):
        import subprocess
        import tarfile
        self.bylo = (tarfile.TarFile.extractall, threading.Timer, subprocess.Popen)
        self.zvali = []
        tarfile.TarFile.extractall = lambda *a, **k: self.zvali.append("extractall")

        class _Timer:
            def __init__(s_, *a, **k):
                pass

            def start(s_):
                self.zvali.append("timer")
        threading.Timer = _Timer
        subprocess.Popen = lambda *a, **k: self.zvali.append("popen")
        return self

    def __exit__(self, *a):
        import subprocess
        import tarfile
        tarfile.TarFile.extractall, threading.Timer, subprocess.Popen = self.bylo


def _arhiv() -> bytes:
    """Архив для /api/deploy: один файл docs/…, больше 1000 байт (случайное содержимое не сжимается)."""
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = secrets.token_bytes(3000)
        ti = tarfile.TarInfo("docs/rabota/_stend_vykladka.bin")
        ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def vazhnoe() -> list:
    with osnovnaya() as o:
        return [r["chto"] for r in o.execute("SELECT chto FROM deystviya WHERE vazhnoe=1 ORDER BY id")]


def test_prava_v_den_vykladki():
    """Главное требование выкатки: пока владелец не завёл входы, для общего admin и прежнего
    boris ничего не меняется — в том числе на адресах, которые закрываются от админов."""
    print("В. День выкладки: входы не заведены — права общего admin и прежнего boris прежние")
    vm = vhod_marshrutov()
    vl = vladelcheskie()
    proverka("адресов под входом владельца не меньше 140", len(vl) >= 140, len(vl))
    sp = _spisok(SPISOK_52)
    proverka("34. каждый адрес списка 5.2 существует и стоит под входом владельца",
             all(vm.get(f"{a} {b}") == "owner" for a, b in sp), [x for x in sp if vm.get(f"{x[0]} {x[1]}") != "owner"])
    proverka("…два адреса остались админам — названы поимённо, с причиной",
             all(vm.get(x) == "auth" for x in OSTALIS_ADMINAM), {x: vm.get(x) for x in OSTALIS_ADMINAM})
    proverka("…четыре страницы «на пульт» стоят под общим входом и закрыты обработчиком (_vladelcu)",
             all(vm.get(f"GET {a}") == "auth" for a in NA_PULT), {a: vm.get(f"GET {a}") for a in NA_PULT})
    na_pult = [(r_.status_code, r_.headers.get("location"), "www-authenticate" in r_.headers)
               for r_ in (kl().get(a, auth=OBSHCHIY, headers=STR) for a in NA_PULT)]
    proverka("общий admin на них → 303 на пульт без окна пароля, как на основной ветке",
             na_pult == [(303, "/pult", False)] * 4, na_pult)
    proverka("прежний boris открывает все четыре",
             [kl().get(a, auth=STARYY_VLADELCA, headers=STR).status_code for a in NA_PULT] == [200] * 4)
    proverka("отчёты о самих админах общему admin закрыты и в день выкладки (401, как на основной ветке)",
             [kl().get(a, auth=OBSHCHIY).status_code for a in ("/api/audit", "/api/istochniki", "/api/sverka-chestnosti")]
             == [401] * 3 and kl().post("/api/chaty/deliver", auth=OBSHCHIY).status_code == 401)
    V = m.vhod
    perehod = [(a, b) for a, b in vl if V._v_spiske(adres_puti(b), V.PEREHOD_ADMIN, V.PEREHOD_PREFIKS)]
    proverka("каждая запись PEREHOD_ADMIN и PEREHOD_PREFIKS — существующий адрес под входом владельца",
             all(any(adres_puti(b) == x for _, b in vl) for x in V.PEREHOD_ADMIN)
             and all(any(adres_puti(b).startswith(x + "/") for _, b in vl) for x in V.PEREHOD_PREFIKS))
    r = po_vsem(perehod, auth=OBSHCHIY)
    proverka(f"общий admin проходит все {len(perehod)} вновь закрытых адресов, как до 12.11", set(r) == {200}, r)
    ostalnye = [x for x in vl if x not in perehod]
    r = po_vsem(ostalnye, auth=OBSHCHIY)
    proverka(f"…а на остальных {len(ostalnye)} адресах владельца — 401 с окном пароля, как раньше", set(r) == {401},
             {k_: v[:5] for k_, v in r.items() if k_ != 401})
    r = po_vsem(vl, auth=STARYY_VLADELCA)
    proverka(f"прежний boris проходит все {len(vl)} адресов владельца, включая выкладку и /owner", set(r) == {200},
             {k_: v[:5] for k_, v in r.items() if k_ != 200})
    c = kl()
    r = c.get("/export/raw/rooms.json", auth=OBSHCHIY)
    proverka("общий admin: /export/raw/rooms.json → 200 (рутины под общим входом не ломаются)", r.status_code == 200,
             r.status_code)
    proverka("прежний boris: /api/deploy с пустым телом → 400, общий admin → 401",
             (c.post("/api/deploy", content=b"", auth=STARYY_VLADELCA).status_code,
              c.post("/api/deploy", content=b"", auth=OBSHCHIY).status_code) == (400, 401))
    do = len(vazhnoe())
    with _BezPerezapuska() as bp:
        r = c.post("/api/deploy", content=_arhiv(), auth=STARYY_VLADELCA)
        r2 = c.post("/api/restart", auth=STARYY_VLADELCA)
    r3 = c.get("/api/backup/db", auth=STARYY_VLADELCA)
    vz = vazhnoe()[do:]
    proverka("40. выкладка, перезапуск и копия базы прежним boris проходят и оставляют строки «Важное»",
             (r.status_code, r2.status_code, r3.status_code) == (200, 200, 200) and len(vz) == 3
             and vz[0].startswith("выкладка кода: 1 файлов") and vz[1].startswith("перезапуск службы")
             and vz[2].startswith("копия базы выгружена") and all("Борис (прежний пароль)" in x for x in vz),
             (r.status_code, r2.status_code, r3.status_code, vz))
    proverka("…архив на стенде не распакован, служба не перезапущена", bp.zvali == ["extractall", "timer", "timer"], bp.zvali)

    print("35. X-Kak: admin — проверка «как админ» без паролей админов")
    KAK = {"x-kak": "admin"}
    r = po_vsem(vl, auth=STARYY_VLADELCA, hdr=KAK)
    proverka(f"прежний boris с X-Kak: admin — все {len(vl)} адресов владельца → 403, ни одного 401 и 200",
             set(r) == {"403в"}, {k_: v[:5] for k_, v in r.items() if k_ != "403в"})
    r = po_vsem(vl, auth=OBSHCHIY, hdr=KAK)
    proverka("общий admin с X-Kak: admin — тоже везде 403: переходная поблажка на него не действует",
             set(r) == {"403в"}, {k_: v[:5] for k_, v in r.items() if k_ != "403в"})
    kody = [c.get(a, auth=STARYY_VLADELCA, headers={**STR, **KAK}).status_code for a in PYAT_STRANIC]
    proverka("пять страниц владельца с X-Kak: admin → 403 (проверка шага 1 выкладки)", kody == [403] * 5, kody)
    na_pult = [(r_.status_code, r_.headers.get("location"))
               for r_ in (c.get(a, auth=STARYY_VLADELCA, headers={**STR, **KAK}) for a in NA_PULT)]
    proverka("…а четыре страницы «на пульт» с X-Kak: admin → 303 на пульт", na_pult == [(303, "/pult")] * 4, na_pult)
    r = c.get("/clicks", auth=STARYY_VLADELCA, headers={**STR, **KAK})
    proverka("странице — «Только для владельца · ← На пульт», без окна пароля и без кеша",
             r.status_code == 403 and "Только для владельца" in r.text and "href='/pult'" in r.text
             and "На пульт" in r.text and "/static/vhod.js" in r.text and not r.headers.get("www-authenticate")
             and r.headers.get("cache-control") == "no-store", r.status_code)
    r = c.get("/api/settings", auth=STARYY_VLADELCA, headers=KAK)
    proverka("запросу /api/… — JSON с кодом 403",
             r.status_code == 403 and r.headers["content-type"].startswith("application/json")
             and "Только для владельца" in r.json()["detail"], r.status_code)
    proverka("с X-Kak: admin рабочие адреса открыты, а /api/kto показывает права админа",
             c.get("/api/pult/tasks", auth=STARYY_VLADELCA, headers=KAK).status_code == 200
             and kto(c, auth=STARYY_VLADELCA, headers=KAK) == {"login": "boris", "imya": "Борис", "rol": "admin",
                                                                "sposob": "staryy"})
    r = c.get("/", auth=STARYY_VLADELCA, headers=KAK)
    proverka("…корень с X-Kak — страница разделов админа: без раздела «Владельцу» и без документов владельца",
             r.status_code == 200 and "Открыть пульт" in r.text and "Владельцу" not in r.text
             and "Ваши документы" not in r.text, r.status_code)
    proverka("другое значение заголовка ничего не меняет: X-Kak: owner под общим admin — по-прежнему 401",
             c.get("/api/settings", auth=OBSHCHIY, headers={"x-kak": "owner"}).status_code == 401
             and kto(c, auth=OBSHCHIY, headers={"x-kak": "owner"})["rol"] == "admin")

    print("38. Пульт и шапка в день выкладки — без изменений")
    t = c.get("/pult", auth=OBSHCHIY, headers=STR).text
    proverka("пульт под общим admin: ни жёлтой полосы, ни «Вы: …», ни «ваша колонка»",
             "kto-polosa" not in t and "Вы под общим входом" not in t and "ваша колонка" not in t
             and "сейчас в системе" not in t)
    # 13.11.2026, шаг 2: /leads переехала на «Откуда заявки» и стала переходом (админа ведёт на пульт) —
    # страницу с общей шапкой base.html здесь и ниже (п. 44) теперь представляет «Подбор группы».
    t = c.get("/enrollment", auth=OBSHCHIY, headers=STR).text
    # 13.11.2026, шаг 2: шапка теперь <div id="kidsup-nav"> из app/nav.py (не <header>) — ожидание поправлено
    proverka("страница с общей шапкой под общим admin: полосы нет", "kto-polosa" not in t and 'id="kidsup-nav"' in t)
    proverka("/obeshchaniya под общим входом ведёт в колонку Лизы, как раньше; who из адреса остаётся",
             unquote(c.get("/obeshchaniya", auth=OBSHCHIY).headers["location"]) == "/pult?who=Лиза"
             and unquote(c.get("/obeshchaniya?who=Ира&day=2026-11-10", auth=OBSHCHIY).headers["location"])
             == "/pult?who=Ира&day=2026-11-10")


def test_prava_lyudey():
    """31, 32, 34, 35 — люди. Пароли заданы, ключей ещё нет: общий admin и прежний boris работают."""
    print("31. Все адреса владельца: личный админ — 403 и ни одного 401; владелец через форму — всё, кроме выкладки")
    vl = vladelcheskie()
    lena, boris = kl(), kl()
    proverka("Лена и владелец входят через форму",
             (voyti(lena, "lena", P["lena"]).status_code, voyti(boris, "boris", P["boris"]).status_code) == (303, 303))
    r = po_vsem(vl, metka=metka(lena))
    proverka(f"Лена: все {len(vl)} адресов владельца → 403 «Только для владельца», ни одного 401 и 200",
             set(r) == {"403в"}, {k_: v[:5] for k_, v in r.items() if k_ != "403в"})
    r = po_vsem(vl, metka=metka(boris))
    zakryto = sorted(r.get("403в", []))
    proverka("владелец через форму: проходит всё, кроме четырёх адресов выкладки (там 403)",
             set(r) == {200, "403в"} and {x.split(" ", 1)[1] for x in zakryto} == set(VYKLADKA4)
             and len(r[200]) == len(vl) - len(zakryto), (zakryto, {k_: v[:5] for k_, v in r.items() if k_ not in (200, "403в")}))
    kody = [lena.get(a, headers=STR).status_code for a in PYAT_STRANIC]
    proverka("Лена открывает страницы владельца в браузере → 403 со страницей, а не окно пароля и не форма",
             kody == [403] * 5, kody)
    na_pult = [(r_.status_code, r_.headers.get("location"), "www-authenticate" in r_.headers)
               for r_ in (lena.get(a, headers=STR) for a in NA_PULT)]
    proverka("…а с четырёх бывших пунктов меню админов её уводит на пульт (303), без окна и без формы",
             na_pult == [(303, "/pult", False)] * 4, na_pult)
    proverka("…владелец через форму открывает все четыре",
             [boris.get(a, headers=STR).status_code for a in NA_PULT] == [200] * 4)
    r = lena.get("/", headers=STR)
    proverka("главная у Лены — страница разделов с её именем, без раздела «Владельцу»",
             r.status_code == 200 and "Открыть пульт" in r.text and "Владельцу" not in r.text
             and "Лена" in r.text and "Ваши документы" not in r.text, r.status_code)
    r = boris.get("/", headers=STR)
    proverka("главная у владельца через форму — с разделом «Владельцу»",
             r.status_code == 200 and "Владельцу" in r.text and "Мои дела на пульте" in r.text, r.status_code)
    r = lena.get("/clicks", headers=STR)
    proverka("…на странице отказа — её имя и ссылка на пульт",
             "Вы вошли как Лена" in r.text and "href='/pult'" in r.text and not r.headers.get("www-authenticate"))
    r = lena.get("/api/audit", headers=FETCH)
    proverka("…запрос со страницы — JSON 403, без X-Vhod (вход-то есть)",
             r.status_code == 403 and "detail" in r.json() and not r.headers.get("x-vhod"), r.status_code)

    print("34. У закрытой страницы закрыт и её /api/…-двойник")
    pary = (("/sverka-chestnosti", "/api/sverka-chestnosti"), ("/audit", "/api/audit"), ("/clicks", "/api/clicks/summary"),
            ("/istochniki", "/api/istochniki"), ("/journal", "/export/raw/users.json"),
            ("/students", "/export/students.csv"), ("/leads", "/export/leads.csv"))
    plohie = [(a, b, lena.get(b).status_code) for a, b in pary if lena.get(b).status_code != 403]
    proverka("Лена: API и выгрузки закрытых страниц → 403", not plohie, plohie)
    proverka("…а её рабочие адреса открыты: пульт, линия, API пульта, разбор правил, итоги промоутеров",
             [lena.get(a, headers=STR).status_code for a in ("/pult", "/liniya", "/api/pult/tasks", "/promo")] == [200] * 4
             and lena.post("/api/audit/resolve?flag_id=0&status=ok", headers=SVOI).status_code != 403)
    proverka("владелец через форму открывает свои страницы и выгрузки",
             [boris.get(a, headers=STR).status_code for a in ("/owner", "/journal", "/export/raw/rooms.json",
                                                              "/api/vhod/sostoyanie")] == [200] * 4)

    print("32. Выкладка, перезапуск, копия базы, настройки — люди")
    proverka("/api/deploy с пустым телом: прежний boris → 400; владелец через форму → 403; админ → 403",
             (kl().post("/api/deploy", content=b"", auth=STARYY_VLADELCA).status_code,
              boris.post("/api/deploy", content=b"", headers=SVOI).status_code,
              lena.post("/api/deploy", content=b"", headers=SVOI).status_code) == (400, 403, 403))
    ad4 = [("POST", "/api/deploy"), ("POST", "/api/restart"), ("GET", "/api/backup/db"), ("GET", "/api/settings"),
           ("POST", "/api/settings")]
    proverka("…то же для перезапуска, копии базы и настроек: прежний boris — да, владелец через форму и админ — 403",
             (set(po_vsem(ad4, auth=STARYY_VLADELCA)), set(po_vsem(ad4, metka=metka(boris))),
              set(po_vsem(ad4, metka=metka(lena)))) == ({200}, {"403в"}, {"403в"}))
    r = boris.get("/api/settings", headers=FETCH)
    proverka("владельцу через форму сказано словами, почему закрыто", r.status_code == 403 and "ключом выкладки" in r.json()["detail"])

    print("Переход: пароли заданы, ключ рутин ещё не работал — общий admin ходит по-прежнему")
    V = m.vhod
    perehod = [(a, b) for a, b in vl if V._v_spiske(adres_puti(b), V.PEREHOD_ADMIN, V.PEREHOD_PREFIKS)]
    proverka("общий admin проходит вновь закрытые адреса и после того, как владелец задал пароли",
             V.zavedeny() and set(po_vsem(perehod, auth=OBSHCHIY)) == {200}
             and kl().get("/export/raw/rooms.json", auth=OBSHCHIY).status_code == 200)

    print("35. X-Kak: admin понижает владельца, админа не повышает")
    KAK = {"x-kak": "admin"}
    r = po_vsem(vl, metka=metka(boris), hdr=KAK)
    proverka("владелец через форму с X-Kak: admin — все адреса владельца → 403", set(r) == {"403в"},
             {k_: v[:5] for k_, v in r.items() if k_ != "403в"})
    proverka("…/api/kto → права админа; блок «Входы» и /owner/vhody закрыты",
             kto(boris, headers=KAK)["rol"] == "admin" and boris.get("/owner", headers={**STR, **KAK}).status_code == 403
             and boris.post("/owner/vhody", data={"deystvie": "klyuch:klod", "moy_parol": P["boris"]},
                            headers={**SVOI, **KAK}).status_code == 403 and not heshi()["klod"])
    proverka("админа заголовок не повышает: Лена с X-Kak: admin и X-Kak: owner — те же 403 и та же Лена",
             set(po_vsem(vl, metka=metka(lena), hdr=KAK)) == {"403в"}
             and lena.get("/api/settings", headers={"x-kak": "owner"}).status_code == 403
             and kto(lena, headers={"x-kak": "owner"})["rol"] == "admin" and login_kto(lena, headers=KAK) == "lena")
    proverka("общий admin с X-Kak: admin на вновь закрытом адресе → 403 (без заголовка — 200)",
             kl().get("/export/raw/rooms.json", auth=OBSHCHIY, headers=KAK).status_code == 403)


def test_kto_voshel():
    """37–39: имя с сервера, своя колонка пульта, журнал."""
    print("37. Имя ставит сервер: галочки, заметки, «не дозвонилась»")
    pult = importlib.import_module("app.pult")
    day = pult.today()
    lena, ira = kl(), kl()
    voyti(lena, "lena", P["lena"])
    voyti(ira, "ira", P["ira"])
    J = {**SVOI, "content-type": "application/json"}
    pult.set_tasks(day, "Лена", [{"t": "10:00", "text": "стенд В: задача с галочкой"},
                                 {"t": "11:00", "text": "стенд В: позвонить 79990001122"},
                                 {"t": "12:00", "text": "стенд В: задача общего входа"}])

    def zadacha(tekst):
        with osnovnaya() as o:
            return dict(o.execute("SELECT * FROM pult_tasks WHERE day=? AND text LIKE ?", (day, f"%{tekst}%")).fetchone())

    def punkt(iid):
        with osnovnaya() as o:
            return dict(o.execute("SELECT * FROM plan_inbox WHERE id=?", (iid,)).fetchone())
    t1, t2, t3 = (zadacha(x)["id"] for x in ("с галочкой", "позвонить", "общего входа"))
    slovo = "tayna-" + secrets.token_hex(6)                    # текст заметки — в журнал попасть не должен
    r = lena.post("/api/pult/done", json={"id": t1, "state": 1, "who": "Ира", "note": slovo}, headers=J)
    z = zadacha("с галочкой")
    proverka("галочка Лены пишет done_by = Лена при who: Ира в запросе, и время",
             r.status_code == 200 and (z["done"], z["done_by"]) == (1, "Лена") and re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d", z["done_ts"] or ""),
             (r.status_code, z["done_by"], z["done_ts"]))
    zh = _posl_zhurnal("/api/pult/done")
    proverka("39. журнал: логин, способ входа, код и «что»; тела запроса (заметки) в строке нет",
             zh[:5] == ("lena", "Лена", "forma", "POST", 200) and zh[5] == f"пункт смены {t1}: сделано" and slovo not in str(zh), zh)
    with osnovnaya() as o:
        adr = o.execute("SELECT adres, put FROM deystviya WHERE put='/api/pult/done' ORDER BY id DESC LIMIT 1").fetchone()
        vse = " ".join(str(x) for r_ in o.execute("SELECT * FROM deystviya") for x in tuple(r_))
    proverka("…в строке есть адрес посетителя и путь без запроса; заметки нет во всём журнале",
             bool(adr["adres"]) and adr["put"] == "/api/pult/done" and slovo not in vse, tuple(adr))
    lena.post("/api/pult/done", json={"id": t1, "state": 0}, headers=J)
    z = zadacha("с галочкой")
    proverka("снятая галочка стирает автора", (z["done"], z["done_by"], z["done_ts"]) == (0, None, None), z["done_by"])
    kl().post("/api/pult/done", json={"id": t3, "state": 1, "who": "Ира"}, auth=OBSHCHIY)
    kl().post("/api/pult/done", json={"id": t1, "state": 1}, auth=OBSHCHIY)
    proverka("общий вход: имя — из запроса, как раньше; без имени — галочка без автора",
             (zadacha("общего входа")["done_by"], zadacha("с галочкой")["done_by"]) == ("Ира", None))
    r1 = ira.post("/api/pult/nedozvon", json={"kind": "task", "id": t2, "who": "Лена"}, headers=J).json()
    r2 = lena.post("/api/pult/nedozvon", json={"kind": "task", "id": t2}, headers=J).json()
    note = zadacha("позвонить")["note"]
    proverka("«не дозвонилась (Ира ЧЧ:ММ)», потом «(Лена ЧЧ:ММ)»; счётчик попыток прежний: 1, 2",
             (r1["попыток"], r2["попыток"]) == (1, 2) and re.search(r"не дозвонилась \(Ира \d\d:\d\d\) не дозвонилась \(Лена \d\d:\d\d\)", note)
             and note.count("не дозвонилась") == 2, (r1, r2, note))
    lena.post("/api/plan/inbox", json={"who": "Лена", "text": "стенд В: обещание 79990003344", "phone": "79990003344"}, headers=J)
    lena.post("/api/plan/inbox", json={"who": "Лена", "text": "стенд В: второе обещание"}, headers=J)
    with osnovnaya() as o:
        i1, i2 = (o.execute("SELECT id FROM plan_inbox WHERE text LIKE ?", (f"%{x}%",)).fetchone()[0]
                  for x in ("обещание 79990003344", "второе обещание"))
    r = ira.post("/api/pult/nedozvon", json={"kind": "inbox", "id": i1}, headers=J).json()
    proverka("пункт инбокса: попытка считается как раньше, рядом — кто набирал",
             r["попыток"] == 1 and punkt(i1)["tries"] == 1 and re.match(r"Ира \d\d:\d\d$", punkt(i1)["tries_kto"] or ""), punkt(i1)["tries_kto"])
    r = lena.post("/api/plan/inbox/note", json={"id": i1, "note": "договорились на четверг", "who": "Ира"}, headers=J)
    proverka("заметка со штампом «✎ Лена ДД.ММ ЧЧ:ММ: …»",
             r.status_code == 200 and re.search(r" · ✎ Лена \d\d\.\d\d \d\d:\d\d: договорились на четверг$", punkt(i1)["text"]),
             punkt(i1)["text"][-60:])
    r = ira.post("/api/plan/inbox/done", json={"id": i1, "done": True, "note": "записала", "who": "Лена"}, headers=J)
    p_ = punkt(i1)
    proverka("галочка Иры на пункте: done_by = Ира, время, заметка с её именем",
             r.status_code == 200 and (p_["done"], p_["done_by"]) == (1, "Ира") and p_["done_ts"] and p_["text"].endswith(" — Ира: записала"),
             (p_["done_by"], p_["text"][-30:]))
    kl().post("/api/plan/inbox/note", json={"id": i2, "note": "без имени"}, auth=OBSHCHIY)
    kl().post("/api/plan/inbox/done", json={"id": i2, "done": True, "note": "как раньше"}, auth=OBSHCHIY)
    p_ = punkt(i2)
    proverka("общий вход на пункте: штамп и заметка без имени, как раньше",
             re.search(r" · ✎ \d\d\.\d\d \d\d:\d\d: без имени — как раньше$", p_["text"]) and p_["done_by"] is None, p_["text"][-50:])
    ira.post("/api/plan/inbox/done", json={"id": i1, "done": False}, headers=J)
    proverka("снятая галочка на пункте стирает автора", (punkt(i1)["done"], punkt(i1)["done_by"]) == (0, None))
    ira.post("/api/plan/inbox/done", json={"id": i1, "done": True}, headers=J)
    lena.post("/api/pult/done", json={"id": t1, "state": 1}, headers=J)

    print("38. Пульт: своя колонка первой, имя в шапке, жёлтая полоса общего входа")

    def kolonki(t):
        return re.findall(r"<div class='nm'>([^<]+?)[ <]", t)
    on = pult.duty(day)
    t = ira.get("/pult", headers=STR).text
    ko = kolonki(t)
    proverka("Ира не в смене — её колонка всё равно первая, с пометкой «ваша колонка» и «не в смене»",
             "Ира" not in on and ko[:1] == ["Ира"] and ko.count("Ира") == 1
             and "ваша колонка</span> <span style='font-size:11px;color:#6c6a86;font-weight:500'>не в смене" in t, (on, ko))
    t_l = lena.get("/pult", headers=STR).text
    bez_formy = kolonki(kl().get("/pult", auth=STARYY_VLADELCA, headers=STR).text)
    proverka("у Лены первой стоит её колонка; у пульта без входа через форму порядок прежний: смена, Лиза, Борис",
             kolonki(t_l)[:1] == ["Лена"] and bez_formy[:len(on) + 2] == on + ["Лиза", "Борис"], (kolonki(t_l)[:3], bez_formy))
    proverka("?who=Лена у Иры работает как раньше: одна колонка Лены",
             kolonki(ira.get("/pult?who=Лена", headers=STR).text) == ["Лена"])
    proverka("в шапке пульта: «Вы: Ира», её цвет, настоящая кнопка «Выйти / сменить» с возвратом на пульт",
             "Вы: <b style='color:#E30613'>Ира</b>" in t and "action='/vyhod'" in t and "Выйти / сменить" in t
             and "name='next' value='/pult'" in t)
    proverka("…и «По графику: … · сейчас в системе: …» с теми, кто заходил",
             "сейчас в системе:" in t and "По графику:" in t and "Лена (" in t.split("сейчас в системе:")[1][:200],
             t.split("сейчас в системе:")[1][:120] if "сейчас в системе:" in t else "нет строки")
    proverka("рядом со сделанным пунктом — «✓ Лена ЧЧ:ММ» и «✓ Ира ЧЧ:ММ»",
             re.search(r"✓ Лена \d\d:\d\d", t_l) and re.search(r"✓ Ира \d\d:\d\d", t_l), re.findall(r"✓ [^<]{1,20}", t_l)[:5])
    proverka("…а у задачи с попытками — кто набирал последним", re.search(r"не дозвонилась 2&nbsp;раза · Лена \d\d:\d\d", t_l))
    t = kl().get("/pult", auth=OBSHCHIY, headers=STR).text
    do = datetime.fromtimestamp(m.vhod._iz_iso(m.db.get_setting("vhod_starye_do")), m.vhod.MSK)
    proverka("под общим входом, когда личные входы заведены, — жёлтая полоса с датой и ссылкой на /vhod",
             f"Вы под общим входом. Он отключится {do.day:02d}.{do.month:02d}" in t and "войдите под своим именем" in t
             and "href='/vhod?next=/pult'" in t and "ваша колонка" not in t, t[t.find("kto-polosa"):][:200] if "kto-polosa" in t else "нет")
    t = lena.get("/enrollment", headers=STR).text
    proverka("общая шапка (base.html): «Вы: Лена», кнопка выхода — над меню",
             "Вы: <b style='color:#7DB928'>Лена</b>" in t and "action='/vyhod'" in t and t.index('id="kidsup-nav"') < t.index("kto-polosa") < t.index('id="nav-razdely"'))   # 13.11: полоска — внутри новой шапки, над меню
    t = kl().get("/enrollment", auth=OBSHCHIY, headers=STR).text
    proverka("…под общим входом — жёлтая полоса", "Вы под общим входом" in t and "kto-polosa obshchiy" in t)
    r = vyyti_s_next(lena, "/pult")
    proverka("«Выйти / сменить» из шапки: сеанс закрыт, форма входа вернёт на пульт",
             r.status_code == 303 and r.headers["location"] == "/vhod?next=/pult" and lena.get("/api/kto").status_code == 401,
             (r.status_code, r.headers.get("location")))
    proverka("/obeshchaniya: вошедшая Ира попадает в свою колонку; who из адреса сильнее",
             unquote(ira.get("/obeshchaniya").headers["location"]) == "/pult?who=Ира"
             and unquote(ira.get("/obeshchaniya?who=Лена").headers["location"]) == "/pult?who=Лена")


def vyyti_s_next(c, nxt):
    return c.post("/vyhod", data={"next": nxt}, headers=SVOI)


def test_perehod_konchilsya():
    print("25. После первого запроса ключом рутин общий admin на вновь закрытых адресах → 403")
    V = m.vhod
    vl = vladelcheskie()
    perehod = [(a, b) for a, b in vl if V._v_spiske(adres_puti(b), V.PEREHOD_ADMIN, V.PEREHOD_PREFIKS)]
    with baza_vhodov() as b:
        pz = b.execute("SELECT posl_zapros FROM vhody WHERE login='klod'").fetchone()[0]
    r = po_vsem(perehod, auth=OBSHCHIY)
    proverka("ключ рутин сработал — общий admin на всех вновь закрытых адресах получает 403, а не окно пароля",
             bool(pz) and set(r) == {"403в"}, (pz, {k_: v[:4] for k_, v in r.items() if k_ != "403в"}))
    r = kl().get("/export/raw/rooms.json", auth=OBSHCHIY)
    proverka("общий admin: /export/raw/rooms.json → 403 JSON без WWW-Authenticate",
             r.status_code == 403 and not r.headers.get("www-authenticate") and "detail" in r.json(), r.status_code)
    proverka("…на адресах, закрытых и раньше, — по-прежнему 401 с окном; рабочие адреса открыты",
             kl().get("/api/settings", auth=OBSHCHIY).status_code == 401
             and kl().get("/api/pult/tasks", auth=OBSHCHIY).status_code == 200
             and kl().post("/api/audit/resolve?flag_id=0&status=ok", auth=OBSHCHIY).status_code != 403)


def test_prava_klyuchey(klyuchi):
    """32, 33, 35, 36, 39, 40 — служебные ключи. Старые входы к этому времени выключены."""
    chasy(None)
    kv, k_rutin = klyuchi[0], klyuchi[-1]
    KV, KR = bear(kv), bear(k_rutin)
    V = m.vhod
    vm = vhod_marshrutov()
    vl = vladelcheskie()
    m.db.set_setting("vhod_okno_vykladki_do", V._iso(time.time() - 60))      # аварийное окно закрыто
    print("33. Ключ рутин: только свой список")
    proverka("оба ключа рабочие", (login_kto(kl(), headers=KR), login_kto(kl(), headers=KV)) == ("klod", "klod-vykladka"))
    puti_vl = {adres_puti(b) for _, b in vl}
    proverka("каждый путь из SLUZH_TOCHNO есть среди маршрутов под входом владельца",
             V.SLUZH_TOCHNO <= puti_vl, sorted(V.SLUZH_TOCHNO - puti_vl))
    proverka("каждое начало из SLUZH_PREFIKS — тоже",
             all(any(p_ == x or p_.startswith(x + "/") for p_ in puti_vl) for x in V.SLUZH_PREFIKS))
    razresh = [(a, b) for a, b in vl if V._v_spiske(adres_puti(b), V.SLUZH_TOCHNO, V.SLUZH_PREFIKS)]
    r = po_vsem(razresh, hdr=KR)
    proverka(f"ключ рутин проходит все {len(razresh)} адресов своего списка: не 401 и не 403", set(r) == {200},
             {k_: v for k_, v in r.items() if k_ != 200})
    ostalnye = [x for x in vl if x not in razresh]
    r = po_vsem(ostalnye, hdr=KR)
    proverka(f"…а на остальных {len(ostalnye)} адресах владельца — 403", set(r) == {"403в"},
             {k_: v[:5] for k_, v in r.items() if k_ != "403в"})
    zakr = [("POST", "/api/ads/vk-users-list"), ("POST", "/api/ads/vk/image"), ("POST", "/api/mkweb/open"),
            ("GET", "/owner"), ("POST", "/owner"), ("POST", "/owner/vhody"), ("GET", "/settings"), ("POST", "/settings/test"),
            ("POST", "/api/boris-docs/arhiv"), ("GET", "/api/bonusy"), ("POST", "/api/chaty/send"),
            ("POST", "/api/istochniki/spravochnik"), ("POST", "/api/audit/run")]
    proverka("поимённо: /api/ads/vk-users-list, /api/ads/vk/image, /api/mkweb/open, /owner, /settings и другие → 403",
             all(f"{a} {b}" in vm for a, b in zakr) and set(po_vsem(zakr, hdr=KR)) == {"403в"}, po_vsem(zakr, hdr=KR))
    opis = _spisok(OPIS_RUTIN)
    net = [x for x in opis if f"{x[0]} {x[1]}" not in vm]
    proverka(f"опись рутин: все {len(opis)} адресов существуют", not net, net)
    plohie = [(a, b, dopusk(a, b, hdr=KR, chem=vm[f"{a} {b}"] if vm[f"{a} {b}"] == "owner" else "auth"))
              for a, b in opis if f"{a} {b}" in vm]
    plohie = [x for x in plohie if x[2] != 200]
    proverka("каждый адрес из описи рутин ключу рутин доступен", not plohie, plohie)
    kody = [kl().get(a, headers=KR).status_code for a in ("/export/raw/rooms.json", "/api/vhod/sostoyanie",
                                                          "/api/boris-docs", "/boris/docs", "/api/pult/tasks")]
    proverka("…и настоящими запросами: /export/raw/rooms.json, состояние входов, документы владельца, пульт → 200",
             kody == [200] * 5, kody)
    proverka("ключ рутин с Basic klod:<ключ> — те же права",
             kl().get("/export/raw/rooms.json", auth=("klod", k_rutin)).status_code == 200
             and kl().get("/api/settings", auth=("klod", k_rutin)).status_code == 403)

    print("32. Выкладка, перезапуск, копия базы, настройки — ключи")
    ad4 = [("POST", "/api/deploy"), ("POST", "/api/restart"), ("GET", "/api/backup/db"), ("GET", "/api/settings"),
           ("POST", "/api/settings")]
    boris, lena = kl(), kl()
    voyti(boris, "boris", P["boris"])
    voyti(lena, "lena", P["lena"])
    proverka("/api/deploy с пустым телом: ключ выкладки → 400; ключ рутин → 403; владелец через форму → 403; админ → 403",
             (kl().post("/api/deploy", content=b"", headers=KV).status_code,
              kl().post("/api/deploy", content=b"", headers=KR).status_code,
              boris.post("/api/deploy", content=b"", headers=SVOI).status_code,
              lena.post("/api/deploy", content=b"", headers=SVOI).status_code) == (400, 403, 403, 403))
    proverka("…то же для перезапуска, копии базы и настроек: ключ выкладки — да, остальные — 403",
             (set(po_vsem(ad4, hdr=KV)), set(po_vsem(ad4, hdr=KR)), set(po_vsem(ad4, metka=metka(boris))),
              set(po_vsem(ad4, metka=metka(lena)))) == ({200}, {"403в"}, {"403в"}, {"403в"}))
    proverka("прежний пароль владельца и общий admin уже не действуют нигде",
             (set(po_vsem(ad4, auth=STARYY_VLADELCA)), set(po_vsem(ad4, auth=OBSHCHIY))) == ({401}, {401}))
    r = po_vsem(vl, hdr=KV)
    zakryto = {x.split(" ", 1)[1] for x in r.get("403в", [])}
    proverka("ключ выкладки проходит весь API владельца; страницы /owner, /owner/vhody, /settings, /settings/test — 403",
             set(r) == {200, "403в"} and zakryto == set(V.TOLKO_CHELOVEK), (sorted(zakryto), [k_ for k_ in r if k_ not in (200, "403в")]))
    proverka("…настоящим запросом: настройки читает, /owner — 403",
             kl().get("/api/settings", headers=KV).status_code == 200 and kl().get("/owner", headers=KV).status_code == 403)

    print("35. X-Kak: admin понижает оба ключа")
    KAK = {"x-kak": "admin"}
    proverka("ключ выкладки и ключ рутин с X-Kak: admin — все адреса владельца → 403",
             set(po_vsem(vl, hdr={**KV, **KAK})) == {"403в"} and set(po_vsem(vl, hdr={**KR, **KAK})) == {"403в"})
    proverka("…пять страниц владельца ключом выкладки с X-Kak → 403; рабочие адреса открыты; /api/kto → права админа",
             [kl().get(a, headers={**KV, **KAK, **STR}).status_code for a in PYAT_STRANIC]
             == [403] * 5
             and [kl().get(a, headers={**KV, **KAK, **STR}).headers.get("location") for a in NA_PULT] == ["/pult"] * 4
             and kl().get("/api/pult/tasks", headers={**KV, **KAK}).status_code == 200
             and kto(kl(), headers={**KV, **KAK}) == {"login": "klod-vykladka", "imya": "Клод (выкладка)", "rol": "admin",
                                                     "sposob": "klyuch"})
    proverka("…выкладка с X-Kak: admin не проходит даже ключом выкладки",
             kl().post("/api/deploy", content=b"", headers={**KV, **KAK}).status_code == 403)

    print("39. Отказ ключу рутин виден владельцу")
    def otkazov(put):
        with osnovnaya() as o:
            return o.execute("SELECT COUNT(*) FROM deystviya WHERE login='klod' AND kod=403 AND put=?", (put,)).fetchone()[0]
    do = (otkazov("/api/bonusy"), otkazov("/api/chaty/send"))
    kl().get("/api/bonusy", headers=KR)
    kl().get("/api/bonusy", headers=KR)
    kl().post("/api/chaty/send", headers=KR)
    kl().get("/api/bonusy", headers={**KR, **KAK})          # проверка «как админ» в отказы Клоду не идёт
    posle = (otkazov("/api/bonusy"), otkazov("/api/chaty/send"))
    proverka("отказы ключу рутин в журнале: /api/bonusy — 2 раза, /api/chaty/send — 1 раз (чтение и запись — по одной строке)",
             (posle[0] - do[0], posle[1] - do[1]) == (2, 1), (do, posle))
    sost = {x["put"]: x["n"] for x in boris.get("/api/vhod/sostoyanie").json()["klodu_otkazano"]}
    proverka("«Клоду отказано» в состоянии входов: /api/bonusy с тем же числом", sost.get("/api/bonusy") == posle[0], sost.get("/api/bonusy"))
    proverka("…и на странице /owner", "/api/bonusy" in boris.get("/owner", headers=STR).text)
    zh = _posl_zhurnal("/api/chaty/send")
    proverka("…в журнале: Клод, ключ, 403, «отказ: адрес закрыт ключу рутин»",
             zh == ("klod", "Клод", "klyuch", "POST", 403, "отказ: адрес закрыт ключу рутин"), zh)

    print("37. Отметка ключом рутин")
    pult = importlib.import_module("app.pult")
    day = pult.today()
    pult.set_tasks(day, "Ира", [{"t": "13:00", "text": "стенд В: задача для Клода"}], replace=False)
    with osnovnaya() as o:
        tid = o.execute("SELECT id FROM pult_tasks WHERE text LIKE '%задача для Клода%'").fetchone()[0]
    kl().post("/api/pult/done", json={"id": tid, "state": 1, "who": "Лена"}, headers={**KR, "x-rutina": "bystraya-proverka"})
    with osnovnaya() as o:
        by = o.execute("SELECT done_by FROM pult_tasks WHERE id=?", (tid,)).fetchone()[0]
        rut = o.execute("SELECT imya, rutina FROM deystviya WHERE put='/api/pult/done' ORDER BY id DESC LIMIT 1").fetchone()
    proverka("отметка ключом рутин с who: Лена — «Клод за Лену»; в журнале — Клод и рутина из X-Rutina",
             by == "Клод за Лену" and tuple(rut) == ("Клод", "bystraya-proverka"), (by, tuple(rut)))
    kl().post("/api/pult/done", json={"id": tid, "state": 1}, headers=KR)
    with osnovnaya() as o:
        by = o.execute("SELECT done_by FROM pult_tasks WHERE id=?", (tid,)).fetchone()[0]
    proverka("…без имени в запросе — «Клод»", by == "Клод", by)

    print("36. Аварийное окно выкладки")
    try:
        m.db.set_setting("vhod_okno_vykladki_do", V._iso(time.time() + 3600))
        proverka("в окне: ключ рутин на /api/deploy с пустым телом → 400 (вход есть, архива нет)",
                 kl().post("/api/deploy", content=b"", headers=KR).status_code == 400)
        proverka("…перезапуск — можно; копия базы и настройки — 403 всегда",
                 [dopusk(a, b, hdr=KR) for a, b in ad4] == [200, 200, "403в", "403в", "403в"],
                 [dopusk(a, b, hdr=KR) for a, b in ad4])
        proverka("…окно не открывает выкладку админу и владельцу через форму",
                 (dopusk("POST", "/api/deploy", metka=metka(lena), hdr=SVOI), dopusk("POST", "/api/deploy", metka=metka(boris), hdr=SVOI))
                 == ("403в", "403в"))
        do = len(vazhnoe())
        with _BezPerezapuska() as bp:
            r = kl().post("/api/deploy", content=_arhiv(), headers=KR)
        vz = vazhnoe()[do:]
        proverka("40. выкладка ключом рутин в окне проходит и оставляет «Важное»: чем и что",
                 r.status_code == 200 and len(vz) == 1 and "выкладка кода: 1 файлов" in vz[0]
                 and "ключ рутин, аварийное окно" in vz[0] and bp.zvali == ["extractall", "timer"], (r.status_code, vz, bp.zvali))
        chasy(time.time() + 3700)
        proverka("после окна: ключ рутин на /api/deploy → 403", kl().post("/api/deploy", content=b"", headers=KR).status_code == 403)
    finally:
        chasy(None)
        m.db.set_setting("vhod_okno_vykladki_do", V._iso(time.time() - 60))

    print("40. Выкладка и копия базы оставляют строку «Важное»")
    do = len(vazhnoe())
    with _BezPerezapuska() as bp:
        r = kl().post("/api/deploy", content=_arhiv(), headers=KV)
        r2 = kl().post("/api/restart", headers=KV)
    r3 = kl().get("/api/backup/db", headers=KV)
    r4 = kl().get("/api/backup/db", headers=KV)
    vz = vazhnoe()[do:]
    proverka("ключ выкладки: выкладка, перезапуск, две копии базы — четыре строки «Важное»",
             (r.status_code, r2.status_code, r3.status_code, r4.status_code) == (200,) * 4 and len(vz) == 4
             and "выкладка кода: 1 файлов" in vz[0] and "перезапуск службы" in vz[1] and "копия базы выгружена" in vz[2]
             and "вторая за сутки" in vz[3] and all("Клод (выкладка)" in x and "адрес " in x for x in vz), vz)
    proverka("…строки видны владельцу на /owner и в состоянии входов",
             "выкладка кода" in boris.get("/owner", headers=STR).text
             and any("копия базы" in x["chto"] for x in boris.get("/api/vhod/sostoyanie").json()["vazhnoe"]))
    with osnovnaya() as o:
        zh = o.execute("SELECT login, kod, chto FROM deystviya WHERE put='/api/deploy' AND kod=200 ORDER BY id DESC LIMIT 1").fetchone()
    proverka("…а сама выкладка — в журнале действий: кто и сколько файлов",
             tuple(zh) == ("klod-vykladka", 200, "выкладка: 1 файлов"), tuple(zh))


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
    sys.stdout = _Tee(sys.stdout)                     # всё напечатанное — в ZAHVAT (проверка 23)
    work = a.work or tempfile.mkdtemp(prefix="vhod_")
    os.makedirs(work, exist_ok=True)
    m = lt.zapustit(KOREN, work)
    lt.zasejat(os.path.join(work, "kidsup.db"))
    VNE_SERVERA.append((m.vhod.vnutr_klyuch(), m.db.admin_auth(), Path(m.config.VHODY_DB).exists()))
    m.vhod.zapusk(True)             # как при запуске работающего сервера: ключ автоматики в памяти
    # роли — как на сервере: общий вход админов и отдельный пароль владельца; режим без входа выключен
    m.config.APP_USER, m.config.APP_PASSWORD = OBSHCHIY
    m.config.BEZ_VHODA = False
    m.db.set_setting("owner_password", STARYY_VLADELCA[1])
    m.db.set_setting("pedagog_key", PED)
    _lovushki_na_vse()
    _zh = _LogVZahvat()
    _zh.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(_zh)           # и всё, что сервер пишет в свой журнал
    test_kak_segodnya()
    test_zamechaniya_v_den_vykladki()
    test_obhod()
    test_prava_v_den_vykladki()     # группа В: пока входы не заведены, права прежние
    test_vladelec_paroli()          # пароли задаёт сам владелец на /owner (действие 1 из 7.2)
    for lg in ("lena", "ira", "liza", "boris"):
        proverka(f"пароль задан: {lg}", bool(P.get(lg)) and heshi()[lg].startswith("pbkdf2_sha256$"))
    proverka("первый пароль: отметка «входы заведены», старые входы ещё 7 дней",
             m.vhod.zavedeny() and m.vhod.starye_vklyucheny()
             and 6.9 * 86400 < m.vhod._iz_iso(m.db.get_setting("vhod_starye_do")) - time.time() <= 7 * 86400)
    proverka("старые входы после заведения работают по-прежнему",
             (kl().get("/api/pult/tasks", auth=OBSHCHIY).status_code,
              kl().get("/api/settings", auth=STARYY_VLADELCA).status_code) == (200, 200))
    test_prava_lyudey()             # группа В: 31, 32, 34, 35 — люди; ключей ещё нет
    test_kto_voshel()               # группа В: 37–39 — имя с сервера, своя колонка, журнал
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
    test_perehod_konchilsya()       # 25, вторая половина: ключ рутин сработал — общему admin закрыто
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
    klyuchi = test_upravlenie(klyuch, work)
    test_prava_klyuchey(klyuchi)    # группа В: 32, 33, 35, 36, 39, 40 — служебные ключи
    test_sledy(klyuch, work)
    test_hranenie(work)
    proverka("ни одной отправки", not lt.LOVUSHKI, lt.LOVUSHKI)
    chuzhie = [x for x in lt.SET if any("vhod" in f for f in x[1])]
    proverka("вход не ходит в сеть", not chuzhie, chuzhie)
    print(f"\nИтого: {lt.proshlo} OK, {lt.oshibok} ошибок · стенд {work}")
    sys.exit(1 if lt.oshibok else 0)


if __name__ == "__main__":
    main()
