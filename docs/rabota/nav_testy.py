# -*- coding: utf-8 -*-
"""Проверки шага 1 «скрыть старое» на app.kidsup.ru — на стенде, без сервера и без сети.

12.11.2026, Борис: «скрой не нужные / не актуальные / старые страницы». Страницы
убраны, их адреса ведут на страницы-замены. Здесь зафиксировано: куда ведёт каждый
старый адрес, что открытие ссылки больше ничего не отправляет клиентам и что из
таблицы маршрутов пропало только то, что убирали нарочно — всё, чем пользуются
рутины ассистента и автопилот (/api/…, /export/raw/…, вебхуки), на месте.

Раздел «Д» — поправки по замечаниям проверки перед выкаткой (вечерний прозвон, отчёты о
самих админах, рассылка в чаты только JSON-телом, плашка «архив», ссылки назад, заголовки,
ключ педагога).

Раздел «Г» — сквозная проверка решения владельца целиком, записанного здесь отдельно от
кода: каждый пункт меню открывается у того, кому показан, а пункты владельца у админа —
401/403 или, для четырёх бывших пунктов админов, 303 на пульт без окна пароля; каждый убранный и объединённый адрес ведёт 303 прямо на страницу, которая сама
отвечает 200 (без цепочек и петель); на меню, пульте и рабочих страницах нет ссылок на
убранное, архивное и объединённое, а в app/ убранные адреса остались только в таблице
переходов; адреса рутин на месте и под прежним входом.

Раздел «М» (13.11.2026, вечер 1) — уборка и безопасность: в коде нет телефонов клиентов, мёртвый
код убран, кампании августа–сентября в базе знаний свёрнуты, полная выгрузка МойКласса — только
владельцу, ключи вебхуков Марквиза и Wazzup задаются настройкой и наружу не отдаются, ни один
изменяющий адрес под входом не принимает запрос с чужого сайта, запись через GET убрана.

Раздел «Н» (13.11.2026, вечер 1) — устаревшее на страницах меню: сезонные строки «Подбора группы»
с прошедшими датами не рисуются; расчёты и авансы промоутеров — только владельцу и с пометкой;
«Долги» — снимком на 15 минут в отдельном потоке, старые счета — свёрнутым блоком; у «Стоп-листа»
появилась кнопка «снять» с причиной (запись не удаляется), на «Линии» — плашка «в стоп-листе».

    python3 docs/rabota/nav_testy.py [--tree КОРЕНЬ] [--work ПАПКА] [--do ФАЙЛ.json]

  --tree  корень дерева, в котором лежит app/ (по умолчанию — этот репозиторий;
          перед выкаткой — чистая выгрузка `git archive`, без удалённых у нас файлов)
  --work  рабочая папка стенда (по умолчанию — новая временная; своя — только пустая
          и внутри временной папки: стенд пишет в базу выдуманных клиентов и пароли)
  --do    таблица маршрутов «до» (по умолчанию — nav_marshruty_do.json рядом, снимок 12.11)

Обвязка — из liniya_testy.py: сеть закрыта, отправки — ловушки, автопилот не
стартует, данные выдуманные. Пароли здесь тоже выдуманные и живут только на стенде.
"""
from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import os
import re
import sqlite3
import sys
import tempfile
from urllib.parse import quote

TUT = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(TUT))

_spec = importlib.util.spec_from_file_location("liniya_testy", os.path.join(TUT, "liniya_testy.py"))
lt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lt)
proverka = lt.proverka

ADMIN = ("admin", "stend-admin")            # общий вход админов — только на стенде
VLADELEC = ("boris", "stend-vladelec")      # вход владельца — только на стенде
SEMYA = lt.SEMYA                            # 79990001001 — выдуманная семья стенда


def lokaciya(c, adres, kto=ADMIN):
    """(код ответа, куда ведёт) — без перехода по ссылке."""
    r = c.get(adres, auth=kto, follow_redirects=False) if kto else c.get(adres, follow_redirects=False)
    return r.status_code, r.headers.get("location")


def marshruty(m) -> set:
    return {f"{metod} {r.path}" for r in m.app.routes if hasattr(r, "methods")
            for metod in r.methods if metod != "HEAD"}


# --- А. Переходы со старых адресов ----------------------------------------------

def test_perehody(c, m):
    print("Убранные страницы: старый адрес ведёт на замену")
    est = marshruty(m)
    for otkuda, kuda in m.UBRANO.items():
        kod, loc = lokaciya(c, otkuda)
        # RedirectResponse кодирует кириллицу сам: /pult?who=Лиза → /pult?who=%D0%9B…
        # «/» в таблице — стартовая страница роли: админа ведём сразу на пульт, без второго перехода
        zhdem = "/pult" if kuda == "/" else quote(kuda, safe="/?=&")
        proverka(f"{otkuda} → 303 {zhdem}", kod == 303 and loc == zhdem, (kod, loc))
        proverka(f"{otkuda} без входа → 401, адрес замены не показан",
                 lokaciya(c, otkuda, kto=None) == (401, None), lokaciya(c, otkuda, kto=None))
        proverka(f"{otkuda}: замена есть в таблице маршрутов и сама не убрана",
                 f"GET {kuda.split('?')[0]}" in est and kuda.split("?")[0] not in m.UBRANO)
    proverka("убрано 15 адресов по таблице (+ /sverka и второй /spiski — отдельно)", len(m.UBRANO) == 15,
             len(m.UBRANO))
    proverka("параметры запроса не переносятся", lokaciya(c, "/nedozvony?fresh=1") == (303, "/obzvon-dnya"),
             lokaciya(c, "/nedozvony?fresh=1"))
    proverka("сайт kidsup.ru: старый адрес без входа — 401, а не переход",
             c.get("/groups", headers={"host": "kidsup.ru"}, follow_redirects=False).status_code == 401)

    print("Досье → «Линия»")
    proverka("/brief?phone=79990001001 → /liniya?phone=9990001001",
             lokaciya(c, f"/brief?phone={SEMYA}") == (303, f"/liniya?phone={SEMYA[-10:]}"),
             lokaciya(c, f"/brief?phone={SEMYA}"))
    proverka("/brief без номера → /liniya", lokaciya(c, "/brief") == (303, "/liniya"), lokaciya(c, "/brief"))
    gryaz = "/brief?phone=" + quote("+8 7 (999) 000-10-01 доб")
    proverka("/brief: 12 цифр и мусор → в адресе 10 цифр", lokaciya(c, gryaz) == (303, "/liniya?phone=9990001001"),
             lokaciya(c, gryaz))
    proverka("/brief без входа → 401", lokaciya(c, f"/brief?phone={SEMYA}", kto=None)[0] == 401)

    print("Обещания → колонка пульта")
    lena = quote("Лена")
    proverka("/obeshchaniya?who=Лена&day=… → /pult?who=Лена&day=…",
             lokaciya(c, "/obeshchaniya?who=Лена&day=2026-10-14") == (303, f"/pult?who={lena}&day=2026-10-14"),
             lokaciya(c, "/obeshchaniya?who=Лена&day=2026-10-14"))
    proverka("/obeshchaniya без параметров → колонка Лизы",
             lokaciya(c, "/obeshchaniya") == (303, f"/pult?who={quote('Лиза')}"), lokaciya(c, "/obeshchaniya"))
    kod, loc = lokaciya(c, "/obeshchaniya?who=" + quote("Лена&day=x\r\nX-Zlo: 1"))
    proverka("/obeshchaniya: «&» и перевод строки в имени остаются внутри одного параметра",
             kod == 303 and loc.startswith("/pult?who=") and "&" not in loc and "\n" not in loc and "\r" not in loc, loc)

    print("Список решений владельца и старая сверка")
    proverka("/boris: админ → /pult", lokaciya(c, "/boris") == (303, "/pult"), lokaciya(c, "/boris"))
    proverka("/boris: владелец → /boris/docs", lokaciya(c, "/boris", VLADELEC) == (303, "/boris/docs"),
             lokaciya(c, "/boris", VLADELEC))
    proverka("/boris без входа → 401", lokaciya(c, "/boris", kto=None)[0] == 401)
    proverka("/sverka: админ → 401 (страница была только владельцу)", lokaciya(c, "/sverka")[0] == 401)
    proverka("/sverka: владелец, документа нет → /boris/docs",
             lokaciya(c, "/sverka", VLADELEC) == (303, "/boris/docs"), lokaciya(c, "/sverka", VLADELEC))
    from app import boris_docs
    boris_docs.save("sverka-oplat", "Сверка оплат (стенд)", "<!doctype html><title>стенд</title>" + "<p>проверка</p>" * 5)
    proverka("/sverka: владелец, документ есть → /boris/docs/sverka-oplat",
             lokaciya(c, "/sverka", VLADELEC) == (303, "/boris/docs/sverka-oplat"), lokaciya(c, "/sverka", VLADELEC))
    proverka("…и документ по этому адресу открывается",
             c.get("/boris/docs/sverka-oplat", auth=VLADELEC).status_code == 200)
    proverka("/api/boris/tasks: админ → 401", c.get("/api/boris/tasks", auth=ADMIN).status_code == 401)
    proverka("/api/boris/tasks: владелец → 200", c.get("/api/boris/tasks", auth=VLADELEC).status_code == 200)
    proverka("POST /api/boris/status: админ → 401",
             c.post("/api/boris/status", auth=ADMIN, json={"id": 1, "status": "done"}).status_code == 401)

    print("Переехали на шаге 2 (содержимое перенесено — адрес остался переходом)")
    # 13.11.2026, шаг 2: /settings и /metrics переехали (раздел «Ж»), остальные пять — раздел «З».
    # Было: «маршрут на месте и не переход» — на страницах оставалось то, чего не было на целевой.
    # Теперь содержимое перенесено; адрес живёт своим обработчиком (не в таблице UBRANO), он ведёт
    # на якорь нового места — сами переходы и перенесённые блоки проверяет раздел «З».
    for adres in ("/segodnya", "/spiski", "/zayavki", "/leads", "/clicks"):
        proverka(f"{adres} — маршрут на месте (свой обработчик-переход, не в UBRANO)",
                 f"GET {adres}" in est and adres not in m.UBRANO)
    proverka("POST /settings и /settings/test — форма ключа МойКласса не осталась без адреса",
             {"POST /settings", "POST /settings/test"} <= est)
    proverka("/spiski — один обработчик (мёртвый второй удалён)",
             sum(1 for r in m.app.routes if getattr(r, "path", "") == "/spiski") == 1)
    # 13.11.2026, шаг 2: было «/spiski открывается (200): списки по группам». Страница переехала на
    # «Бланки групп» (вид «Все группы дня»), сам адрес — переход; списки открываются на новом месте.
    r = c.get("/spiski", auth=ADMIN)
    proverka("/spiski ведёт на «Все группы дня» на бланках: списки по группам открываются там",
             # 13.11.2026, проверка шага 2: без параметров — вся неделя (day=vse), как было на /spiski
             r.status_code == 200 and str(r.url).endswith("/blank-grupp?vid=den&day=vse") and "Все группы дня" in r.text,
             (r.status_code, str(r.url)))


# --- А. Безопасность: открытие ссылки ничего не отправляет ---------------------

def test_bezopasnost(c, m):
    print("Переписка: открытие ссылки ничего не отправляет")
    import app.perepiska as P
    vyzovy = []

    def run(day=None, dry=True):           # настоящий run ходит в сеть — сюда не пускаем
        vyzovy.append({"day": day, "dry": dry})
        return {"всего": 0, "ответили": 0, "человеку": 0, "ночь": False, "строки": []}
    P.run = run
    proverka("у GET /api/perepiska нет параметра send", "send" not in inspect.signature(m.perepiska_report).parameters)
    r = c.get("/api/perepiska?send=1", auth=ADMIN)
    proverka("GET /api/perepiska?send=1 → отчёт без отправки (dry=True)",
             r.status_code == 200 and vyzovy == [{"day": None, "dry": True}], (r.status_code, vyzovy))
    r = c.get("/api/perepiska?day=2026-10-14&send=1&dry=0", auth=ADMIN)
    proverka("…и с ?dry=0 тоже", vyzovy[-1] == {"day": "2026-10-14", "dry": True}, vyzovy[-1])
    bylo = len(vyzovy)
    proverka("GET /perepiska?send=1 → 303 на «Ответы», без параметров",
             lokaciya(c, "/perepiska?send=1") == (303, "/waiting"), lokaciya(c, "/perepiska?send=1"))
    proverka("…переписку при этом никто не запускал", len(vyzovy) == bylo, len(vyzovy) - bylo)
    proverka("POST /perepiska не существует", c.post("/perepiska?send=1", auth=ADMIN).status_code == 405)
    proverka("GET /api/perepiska/kto на месте (вечерний контроль)", "GET /api/perepiska/kto" in marshruty(m))
    proverka("кнопки «Опубликовать» во все соцсети под общим входом больше нет",
             c.post("/api/publish", auth=ADMIN).status_code in (404, 405))
    proverka("ни одной отправки за все проверки", not lt.LOVUSHKI, lt.LOVUSHKI)


# --- А. Ссылки на рабочих страницах ---------------------------------------------

def test_ssylki(c, m, baza):
    print("Ссылки: поиск, «Ответы», пульт, база знаний")
    con = sqlite3.connect(baza)
    con.execute("INSERT OR REPLACE INTO users (id, name, phone, raw) VALUES (9901, 'Безномерова Ада', '', '{}')")
    con.execute("INSERT OR REPLACE INTO users (id, name, phone, raw) VALUES (9902, 'Длинномерова Яна', '799900010019', '{}')")
    con.commit()
    con.close()
    items = c.get("/api/search?q=Тестова", auth=ADMIN).json()["items"]
    proverka("поиск: клиент с телефоном → «Линия» с 10 цифрами",
             any(i["url"] == f"/liniya?phone={SEMYA[-10:]}" for i in items), [i["url"] for i in items])
    # 13.11.2026, вечер 1: было …/client/<id>. Карточка МойКласса теперь по адресу /user/<id> —
    # как на «Линии» (liniya.CRM_KARTOCHKA), пульте, «Без оплаты» и «Проверке МойКласса».
    items = c.get("/api/search?q=Безномерова", auth=ADMIN).json()["items"]
    proverka("поиск: клиент без телефона → карточка МойКласса",
             [i["url"] for i in items] == ["https://app.moyklass.com/user/9901"], [i["url"] for i in items])
    items = c.get("/api/search?q=Длинномерова", auth=ADMIN).json()["items"]
    proverka("поиск: номер из 12 цифр не обрезаем до чужого — карточка МойКласса",
             [i["url"] for i in items] == ["https://app.moyklass.com/user/9902"], [i["url"] for i in items])
    vse = json.dumps([c.get(f"/api/search?q={quote(q)}", auth=ADMIN).json() for q in ("Тестова", "999000", "ПШ")],
                     ensure_ascii=False)
    proverka("поиск не ведёт на /brief и /students", "/brief" not in vse and "/students" not in vse)

    w = c.get("/api/waiting", auth=ADMIN).json()
    proverka("/api/waiting: ключ brief на месте и ведёт на «Линию»",
             w["items"] and all(i["brief"] == f"/liniya?phone={i['phone']}" for i in w["items"]),
             [i.get("brief") for i in w["items"]][:3])
    h = c.get("/waiting", auth=ADMIN).text
    proverka("«Ответы»: ссылки «досье» нет, «Линия» есть", "/brief" not in h and "/liniya?phone=" in h)
    proverka("подсказка при входящем звонке ведёт на «Линию»",
             "app.kidsup.ru/liniya?phone=9990001001" in m._brief_text(SEMYA) and "/brief" not in m._brief_text(SEMYA)
             and "app.kidsup.ru/liniya?phone=9990009999" in m._brief_text(lt.NEZNAKOMYJ))

    h = c.get("/pult", auth=ADMIN).text
    for adres in ("/nezvonili", "/nabor", "/spiski", "/base/plan_", "/obeshchaniya", "/brief"):
        proverka(f"пульт: ссылки {adres} нет", f"href='{adres}" not in h and f'href="{adres}' not in h)
    proverka("пульт: «бланки групп» вместо «списков занятий»", "href='/blank-grupp'" in h)

    h = c.get("/base", auth=ADMIN).text
    for adres in ("/ochered", "/perepiska", "/nedozvony", "/zapolnyaemost", "/zadachi-lizy", "/boris", "/zayavki", "/spiski"):
        proverka(f"база знаний: пункта {adres} нет", f'href="{adres}"' not in h)
    proverka("база знаний: «Бланки групп» есть", 'href="/blank-grupp"' in h)
    for adres in ("/callplan", "/content", "/courses"):
        t = c.get(adres, auth=ADMIN).text.split("<main", 1)[-1]      # меню в шапке проверяется отдельно
        proverka(f"{adres}: нет ссылок на убранные /callaudit и /makety", "/callaudit" not in t and "/makety" not in t)
    from app import assistant
    proverka("Клод-диспетчер не отправляет сотрудников на /callplan и /content",
             "/callplan" not in assistant.RULES and "/content" not in assistant.RULES and "/obzvon-dnya" in assistant.RULES)


# --- А. Таблица маршрутов: пропало только то, что убирали нарочно ---------------

# Маршруты, которые звала только убранная страница.
PROPALI = {"POST /api/ask", "GET /api/ask/health", "POST /api/publish", "GET /api/publish/health",
           "POST /api/queue/result"}
# Новые адреса шага 1: страница «Архив» и признак «архив» у документов — ровно эти три,
# чужих новых адресов быть не должно.
NOVYE = {"GET /arhiv", "POST /api/boris-docs/arhiv", "POST /api/admin-docs/arhiv"}
# 12.11.2026, личные входы (app/vhod.py): страница входа, выход и «кто вошёл».
NOVYE_VHOD = {"GET /vhod", "POST /vhod", "POST /vyhod", "GET /api/kto",
              "POST /owner/vhody", "GET /api/vhod/sostoyanie"}
# 12.11.2026, личные входы: перечень адресов (/openapi.json, /docs, /redoc) без входа больше
# не отдаётся — его никто не зовёт, а чужому он показывал все адреса владельца и выкладки.
UBRANY_VHOD = {"GET /docs", "GET /docs/oauth2-redirect", "GET /openapi.json", "GET /redoc"}
# 12.11.2026, правило 2 Бориса: страница «Проверка МойКласса» и её API — ровно эти пять.
NOVYE_PROVERKA_MK = {"GET /proverka-mk", "GET /api/proverka-mk", "POST /api/proverka-mk/perepoverit",
                     "POST /api/proverka-mk/ne-oshibka", "POST /api/proverka-mk/zametka"}
# 13.11.2026, шаг 2 (общая шапка): лёгкий счётчик для бейджа «ждут ответа» — шапка на каждой
# странице опрашивает его, а не тяжёлый /api/waiting.
NOVYE_SHAPKA = {"GET /api/waiting/schet"}
# 13.11.2026, шаг 2: лист ожидания будущих направлений переехал с /zayavki на бланки — чтение.
NOVYE_PEREEZDY = {"GET /api/blank-grupp/budushchie"}
# Не из шага 1: разовый просмотр «старых звонков» (решение Бориса 12.11), только чтение.
NOVYE_ZVONKI_1211 = {"GET /api/hvost/staroe"}
# 13.11.2026, Борис — уборка и безопасность (вечер 1). Убраны два адреса статусов «Без оплаты»:
# их звали только страницы /bez-oplaty и /ne-oplatili, снятые 12.11 (последняя отметка — 10.10).
UBRANY_VECHER1 = {"GET /api/bez-oplaty/status", "POST /api/bez-oplaty/status"}
# Появились два POST вместо записи через GET: снятие пустых отметок обзвона (было
# GET /api/obzvon/audit?vernut=1) и пересчёт проверок бланков (было GET /api/blank-grupp?refresh=1).
NOVYE_VECHER1 = {"POST /api/obzvon/audit/vernut", "POST /api/blank-grupp/obnovit"}
# 13.11.2026, Борис — устаревшее на страницах меню (вечер 1, раздел «Н»): кнопка «снять» на
# «Стоп-листе» (с причиной; старый POST /api/otkazy/release остался для рутин) и кнопка
# «обновить сейчас» на «Долгах» (страница больше не ходит в МойКласс при каждом открытии).
NOVYE_VECHER1 |= {"POST /api/otkazy/snyat", "POST /api/dolgi/obnovit"}
# 13.11.2026, вечер (проверка «вечера 1»): кнопка «вернуть в стоп-лист» у снятой записи — сняли не
# ту строку, а вернуть её со страницы было нельзя.
NOVYE_VECHER1 |= {"POST /api/otkazy/vernut"}
# То, что зовут рутины ассистента, автопилот, сайт и вебхуки, — названо поимённо,
# чтобы пропажа была видна и без снимка «до».
# 13.11.2026, Борис (уборка, вечер 1): из списка вышли GET и POST /api/bez-oplaty/status —
# они стояли здесь с шага 1 «до проверки на сервере»; читателей не осталось (UBRANY_VECHER1).
NUZHNY = """
GET /api/health · POST /api/deploy · GET /api/settings · GET /api/backup/db
GET /export/raw/{table}.json · GET /api/plan/inbox · POST /api/plan/inbox · POST /api/plan/inbox/done
GET /api/pult/tasks · POST /api/pult/tasks · POST /api/pult/done · GET /api/waiting · GET /api/duty
GET /api/gruppy/soobsheniya · POST /api/gruppy/otvet · GET /api/kadry · POST /api/kadry/ocenka
GET /kadry/file/{day}/{name} · GET /api/mesta · GET /api/mesta/razrez · GET /api/mesta/sostav
GET /api/boris-docs · POST /api/boris-docs · GET /api/admin-docs · POST /api/admin-docs
GET /api/doc-state/{doc} · GET /api/zayavki · GET /api/dolgi · GET /api/obzvon-dnya · GET /api/dialogs
GET /api/calls/list · GET /api/calls/recording · GET /api/crm/user · POST /api/crm/comment
GET /api/day/firsts · GET /api/oplata/plan · GET /api/yavka · GET /api/english/karta
POST /api/hooppy/post · GET /api/hooppy/posty · POST /api/hooppy/udalit · GET /api/promo/grafik
GET /api/perepiska · GET /api/perepiska/kto · GET /api/vozvrat/spiski
GET /api/spiski · GET /api/segodnya · GET /api/nezvonili · GET /api/ai/probe
POST /api/callplan/done · GET /export/leads.csv · POST /leads/push/{lead_id} · GET /go/{channel}
GET /boris/docs · GET /boris/docs/{slug} · GET /admin/docs · GET /admin/docs/{slug} · GET /base/{slug}
GET /pult · GET /liniya · GET /waiting · GET /obzvon-dnya · GET /mesta · GET /voronka · GET /blank-grupp
"""


def test_marshruty(m, fajl_do):
    print("Таблица маршрутов: до и после")
    do = set(json.load(open(fajl_do, encoding="utf-8")))
    posle = marshruty(m)
    proverka("снимок «до» — 471 маршрут", len(do) == 471, len(do))
    # 13.11.2026 (вечер 1): к пропавшим добавились два адреса статусов «Без оплаты» (UBRANY_VECHER1),
    # к новым — два POST вместо записи через GET (NOVYE_VECHER1). Причины — у самих списков.
    proverka("пропали ровно пять — те, что звала только убранная страница (и перечень адресов: "
             "/openapi.json, /docs, /redoc; 13.11 — два адреса статусов «Без оплаты»)",
             do - posle == PROPALI | UBRANY_VHOD | UBRANY_VECHER1,
             sorted((do - posle) ^ (PROPALI | UBRANY_VHOD | UBRANY_VECHER1)))
    vse_novye = (NOVYE | NOVYE_ZVONKI_1211 | NOVYE_PROVERKA_MK | NOVYE_VHOD | NOVYE_SHAPKA | NOVYE_PEREEZDY
                 | NOVYE_VECHER1)
    proverka("появились ровно три: «Архив» и признак «архив» у документов (и просмотр старых звонков 12.11, "
             "пять адресов страницы «Проверка МойКласса», адреса личных входов, счётчик бейджа шапки и лист "
             "«Будущие направления» для бланков 13.11; два POST вместо записи через GET — вечер 1)",
             posle - do == vse_novye, sorted((posle - do) ^ vse_novye))
    nuzhny = [x.strip() for x in NUZHNY.replace("\n", " · ").split(" · ") if x.strip()]
    net = [x for x in nuzhny if x not in posle]
    proverka(f"{len(nuzhny)} адресов рутин, автопилота и сайта — на месте", not net, net)
    for koren in ("/lk/", "/v/", "/go/"):
        proverka(f"{koren}… на месте", any(x.split(" ", 1)[1].startswith(koren) for x in posle))
    proverka("вебхуки на месте", {"POST /mango/events", "POST /mango/{rest:path}", "POST /events/{rest:path}"} <= posle
             and any("wazzup" in x for x in posle))


# --- А. Уборка: удалённое нигде не упоминается -----------------------------------

UBRANY_SHABLONY = ("groups", "ask", "makety", "callaudit", "suggest", "plan311", "vozvrat", "sostav", "brief")
UBRANY_MODULI = ("makety", "publish", "callqueue")
# Модули, которые удалять нельзя, хотя их страницы убраны или в архиве: их зовут автопилот и «Линия».
ZHIVYE_MODULI = ("assistant", "analytics", "content", "perepiska", "vozvrat", "lizacheck", "taskguard", "sla",
                 "boris", "nezvonili", "nabor", "chaty", "leads", "spiski", "segodnya", "zayavki")


def test_uborka(tree):
    print("Уборка: удалённые шаблоны, модули и файлы с телефонами")
    app_dir = os.path.join(tree, "app")
    kod = {}
    for papka, _, fajly in os.walk(app_dir):
        for f in fajly:
            if f.endswith((".py", ".html")):
                p = os.path.join(papka, f)
                kod[os.path.relpath(p, tree)] = open(p, encoding="utf-8").read()
    for sh in UBRANY_SHABLONY:
        gde = [p for p, t in kod.items() if re.search(rf"[\"']{sh}\.html[\"']", t)]
        proverka(f"шаблон {sh}.html удалён и нигде не рисуется",
                 not os.path.exists(os.path.join(app_dir, "templates", f"{sh}.html")) and not gde, gde)
    for mod in UBRANY_MODULI:
        rx = re.compile(rf"^\s*(?:from\s+(?:\.|app)\s+import\s+[^\n]*\b{mod}\b|from\s+(?:\.|app\.){mod}\s+import|import\s+app\.{mod}\b)",
                        re.M)
        gde = [p for p, t in kod.items() if rx.search(t)]
        proverka(f"модуль app/{mod}.py удалён и нигде не импортируется",
                 not os.path.exists(os.path.join(app_dir, f"{mod}.py")) and not gde, gde)
    for mod in ZHIVYE_MODULI:
        proverka(f"модуль app/{mod}.py на месте", os.path.exists(os.path.join(app_dir, f"{mod}.py")))
    rabota = os.path.join(tree, "docs", "rabota")
    if os.path.isdir(rabota):
        # 13.11.2026: папка docs/rabota/plan целиком ушла из репозитория вместе с выгрузками
        # с телефонами клиентов (разбор 23.09, 311_*.json) — её отсутствие тоже «убрано».
        plan_dir = os.path.join(rabota, "plan")
        ostalis = [f for f in os.listdir(plan_dir) if f.startswith("311_")] if os.path.isdir(plan_dir) else []
        ostalis += [f for f in ("bez_oplaty.html", "ne_oplatili_oktyabr.html", "sverka.html")
                    if os.path.exists(os.path.join(rabota, "kontrol", f))]
        proverka("файлы с телефонами и суммами клиентов убраны из репозитория", not ostalis, ostalis)
        zhurnal = [f for f in os.listdir(os.path.join(rabota, "kontrol")) if re.fullmatch(r"\d\d-\d\d\.json", f)]
        proverka("журнал вечернего контроля (kontrol/ДД-ММ.json) не тронут", len(zhurnal) >= 50, len(zhurnal))


# --- Б. Меню и роли ----------------------------------------------------------------

ARHIVNYE = {"/students", "/payments", "/content", "/callplan", "/chaty", "/nezvonili", "/gruppy/media",
            "/prelaunch", "/nabor"}
# Объединённые: у каждой есть страница-цель, в меню стоит она. Плюс страницы без пункта
# меню по решению владельца (/constructor, /obzvon, /karta…) и прежние пункты шапки.
NE_V_MENU = ARHIVNYE | {"/", "/brief", "/boris", "/obeshchaniya", "/sverka", "/settings", "/metrics", "/segodnya",
                        "/spiski", "/zayavki", "/leads", "/clicks", "/constructor", "/obzvon", "/karta",
                        "/english/karta", "/pravila-kontrol"}


def shapka(c, adres, kto):
    """HTML общей шапки страницы — всё, что до <main>. 13.11.2026: шапку собирает
    app/nav.py (id="kidsup-nav", классы kn-), прежних header.topbar и nav-main нет."""
    return c.get(adres, auth=kto).text.split("<main", 1)[0]


def _adresa(razdely) -> list:
    return [adres for _, punkty in razdely for _, adres in punkty]


def test_menu(c, m):
    from app import nav
    print("Меню: что в нём есть")
    est = marshruty(m)
    vse = _adresa(nav.ADMIN + [nav.OWNER])
    net = [a for a in vse if f"GET {a.split('?')[0]}" not in est]
    proverka(f"все {len(vse)} пунктов меню ведут на существующие страницы", not net, net)
    lishnie = [a for a in vse if a.split("?")[0] in set(m.UBRANO) | NE_V_MENU]
    proverka("в меню нет убранных, архивных и объединённых страниц", not lishnie, lishnie)
    # 13.11.2026, Борис — меню, вечер 1 («М — по рекомендациям»): было 16 пунктов в четырёх
    # разделах и 11 у владельца. Из меню админов ушли «Обзвон», «Шахматка», «Стоп-лист», пришла
    # «Проверка МойКласса» (раздел «МойКласс», туда же переехала «Явка») — стало 14; у владельца
    # ушли «Аномалии», «Журнал», «Откуда заявки», «Камеры», «Архив» — стало 6. Точный состав,
    # замены и поиск проверяет раздел «Л».
    # 13.11.2026, вечер (проверка «вечера 1»): раздел называется «МойКласс (Лиза)» — как в
    # утверждённом Борисом тексте «Вечера 1» и в макете меню (днём был записан «МойКласс»).
    proverka("у админа 14 пунктов: Смена, Группы, МойКласс (Лиза), Деньги, Справка",
             [r for r, _ in nav.menu("admin")] == ["Смена", "Группы", "МойКласс (Лиза)", "Деньги", "Справка"]
             and len(_adresa(nav.menu("admin"))) == 14, [r for r, _ in nav.menu("admin")])
    proverka("у владельца — те же и 6 своих последним разделом",
             nav.menu("owner")[:-1] == nav.menu("admin") and nav.menu("owner")[-1] == nav.OWNER
             and len(nav.OWNER[1]) == 6)
    proverka("педагогу по ключу меню не положено", nav.menu("pedagog") == [])

    print("Меню: админ")
    h = shapka(c, "/liniya", ADMIN)
    net = [a for a in _adresa(nav.ADMIN) if f'href="{a}"' not in h]
    proverka("в шапке все 14 пунктов админов", not net, net)      # 13.11.2026, вечер 1: было 16
    # 13.11.2026, шаг 2: ожидания поправлены под новую шапку — подсветка классом kn-tut,
    # «☰ Разделы» — четвёртая кнопка нижней панели (значок и подпись в отдельных span).
    proverka("подсвечена открытая страница", 'href="/liniya" class="kn-tut" aria-current="page"' in h)
    proverka("на телефоне меню сворачивается: кнопка «☰ Разделы»",
             'id="nav-toggle"' in h and "☰" in h and ">Разделы<" in h and 'aria-controls="nav-razdely"' in h
             and 'class="kn-panel"' in h)
    vladelcu = [a for a in _adresa([nav.OWNER]) if f'href="{a}"' in h]
    proverka("раздела владельца в шапке нет", "Владельцу" not in h and not vladelcu, vladelcu)
    t = c.get("/liniya", auth=ADMIN).text
    proverka("…и во всей странице нет «Владельцу», /owner, /arhiv, /kontrol-oplat",
             all(x not in t for x in ("Владельцу", 'href="/owner"', 'href="/arhiv"', 'href="/kontrol-oplat"')))
    proverka("кнопки «Синхронизировать» у админа нет", 'id="sync-btn"' not in t and "Синхронизировать" not in h)
    # логотип ведёт на «/» — на свою главную по роли; это не пункт меню, смотрим только разделы
    razdely = h.split('id="nav-razdely"', 1)[-1]
    starye = [a for a in sorted(NE_V_MENU | {"/ask", "/makety", "/plan311", "/groups"}) if f'href="{a}"' in razdely]
    proverka("ни одного прежнего пункта (/ask, /makety, /callplan, /students, /settings…)", not starye, starye)

    print("Меню: владелец")
    h = shapka(c, "/liniya", VLADELEC)
    net = [a for a in _adresa(nav.menu("owner")) if f'href="{a}"' not in h]
    proverka("в шапке все 14 пунктов админов и 6 своих", not net, net)      # 13.11.2026, вечер 1: было 16 и 11
    proverka("раздел «Владельцу» — последним", h.rfind("Владельцу") > h.find('href="/base"') > 0)
    proverka("кнопка «Синхронизировать» есть", 'id="sync-btn"' in h)
    # 13.11.2026, шаг 2: ключ МойКласса переехал на саму /owner (раздел id='mk') — ссылка
    # вверху теперь ведёт на раздел этой же страницы, а не на /settings
    h_owner = c.get("/owner", auth=VLADELEC).text
    proverka("/owner: строка-ссылка на раздел ключа МойКласса (#mk) и сам раздел",
             "href='#mk'" in h_owner and "id='mk'" in h_owner and "href='/settings'" not in h_owner)

    print("Педагог по ключу из ссылки")
    kluch = m.db.get_setting("pedagog_key")
    r = c.get(f"/english/karta?k={kluch}")
    proverka("карточка речи по ключу без входа → 200", r.status_code == 200, r.status_code)
    proverka("…ни меню, ни поиска, ни бейджа «ждут», ни синхронизации",
             all(x not in r.text for x in ('id="nav-razdely"', 'id="gs-input"', 'id="wait-badge"', 'id="sync-btn"',
                                           'id="nav-toggle"')))
    proverka("…виджеты админов не опрашиваются (флаг педагога от сервера)", "KIDSUP_PED = true;" in r.text)
    r = c.get(f"/english/karta?k={kluch}", auth=("boris", "chto-ugodno"))
    proverka("ключ и поддельный вход boris → педагог, не владелец",
             r.status_code == 200 and "Владельцу" not in r.text and 'id="nav-razdely"' not in r.text)
    r = c.get(f"/english/karta?k={kluch}", auth=ADMIN)
    proverka("та же ссылка у админа — с его меню",
             r.status_code == 200 and 'id="nav-razdely"' in r.text and "KIDSUP_PED = false;" in r.text)
    proverka("неверный ключ без входа → 401", c.get("/english/karta?k=nevernyj").status_code == 401)


# --- Б. Главная по роли ------------------------------------------------------------

def test_glavnaya(c, m):
    from app import boris_docs
    # 12.11.2026, вечер: Борис спросил, почему главная перекидывает на пульт и на документы,
    # и выбрал «страницу разделов»: главная отвечает 200, на ней всё меню с пояснениями.
    print("Главная: страница разделов")
    r = c.get("/", auth=ADMIN, follow_redirects=False)
    proverka("/ админ → 200, без перехода", (r.status_code, r.headers.get("location")) == (200, None),
             (r.status_code, r.headers.get("location")))
    # 13.11.2026, вечер 1: разделов стало пять — добавился «МойКласс» с «Проверкой МойКласса»
    proverka("у админа все пять разделов и кнопка пульта",
             all(x in r.text for x in ("Смена", "Группы", "МойКласс", "Деньги", "Справка", "Открыть пульт",
                                       "href=\"/pult\"", "href=\"/proverka-mk\"")))
    proverka("у админа нет раздела «Владельцу» и документов", "Владельцу" not in r.text and "Ваши документы" not in r.text)
    boris_docs.save("start", "Стартовая страница (стенд)", "<!doctype html><title>стенд</title>" + "<p>стенд</p>" * 5)
    boris_docs.save("stary-stend", "Старый документ (стенд)", "<!doctype html><title>стенд</title>" + "<p>стенд</p>" * 5)
    boris_docs.v_arhiv(["stary-stend"], True)
    r = c.get("/", auth=VLADELEC, follow_redirects=False)
    proverka("/ владелец → 200, раздел «Владельцу» и его документы",
             r.status_code == 200 and "Владельцу" in r.text and "Ваши документы" in r.text
             and "Стартовая страница (стенд)" in r.text, r.status_code)
    proverka("архивные документы на главной не показываются", "Старый документ (стенд)" not in r.text)
    boris_docs.remove("stary-stend")      # не мешать проверкам блока «Архив» ниже
    proverka("…а сам start открывается по адресу", c.get("/boris/docs/start", auth=VLADELEC).status_code == 200)
    proverka("/ без входа → 401, куда ведёт — не видно", lokaciya(c, "/", kto=None) == (401, None))
    proverka("дашборда больше нет", not hasattr(m, "dashboard") and "GET /" in marshruty(m))
    print("Главная: публичные сайты — как раньше")
    r = c.get("/", headers={"host": "kidsup.ru"}, follow_redirects=False)
    proverka("kidsup.ru/ → 200, страница сайта (рутина баланса ждёт 200)",
             r.status_code == 200 and "location" not in r.headers and "/pult" not in r.text, r.status_code)
    r = c.get("/", headers={"host": "kidsup.ru"}, auth=ADMIN, follow_redirects=False)
    proverka("kidsup.ru/ со входом админа — тоже сайт, не пульт", r.status_code == 200, r.status_code)
    for host in ("kidsupday.ru", "kidsupweek.ru"):
        r = c.get("/", headers={"host": host}, follow_redirects=False)
        loc = r.headers.get("location") or ""
        proverka(f"{host}/ → 301 на сайт", r.status_code == 301 and loc.startswith("https://kidsup.ru/"),
                 (r.status_code, loc))


# --- Б. Архив страниц и архивные страницы -----------------------------------------------

def test_arhiv(c, m):
    from app import nav
    print("Архив страниц (/arhiv)")
    proverka("/arhiv: админ → 401", c.get("/arhiv", auth=ADMIN).status_code == 401)
    proverka("/arhiv без входа → 401", c.get("/arhiv").status_code == 401)
    r = c.get("/arhiv", auth=VLADELEC)
    proverka("/arhiv: владелец → 200, с его меню", r.status_code == 200 and "Владельцу" in r.text, r.status_code)
    proverka("в списке ровно девять архивных страниц из решения", {a for a, *_ in nav.ARHIV} == ARHIVNYE)
    net = [a for a, *_ in nav.ARHIV if f'href="{a}"' not in r.text]
    proverka("на каждую — ссылка", not net, net)
    proverka("у каждой — «что это» и «почему в архиве»",
             all(chto and pochemu for _, _, chto, pochemu in nav.ARHIV) and r.text.count("Почему в архиве:") == 9)
    # 13.11.2026, шаг 2: было семь «переезжают»; утром переехали /settings и /metrics, днём —
    # остальные пять. Список пуст, и блок «Переезжают» не рисуется: «пока открываются по адресу»
    # было бы неправдой. Все семь — в блоке «Уже переехали».
    proverka("блок «Переезжают» пуст и не показан: по адресу не открывается ни одна из семи",
             nav.PEREEZD == [] and "Переезжают на другие страницы" not in r.text, nav.PEREEZD)
    net = [a for a, _, _, kuda, *_ in nav.PEREEHALI if a not in r.text or f'href="{kuda}"' not in r.text]
    proverka("блок «Уже переехали»: все семь — с датой и ссылкой на новое место, без ссылки на старый адрес",
             "Уже переехали" in r.text and {a: k for a, _, _, k, *_ in nav.PEREEHALI} == OBEDINENY_PEREEHALI and not net
             and not [a for a in OBEDINENY_PEREEHALI if f'href="{a}"' in r.text] and "13.11.2026" in r.text, net)
    proverka("внизу — ссылки на свёрнутый архив документов",
             'href="/boris/docs#arhiv"' in r.text and 'href="/admin/docs#arhiv"' in r.text)

    print("Архивные страницы: кому открываются")
    for adres in ("/students", "/payments", "/chaty", "/gruppy/media"):
        proverka(f"{adres}: владелец → 200", c.get(adres, auth=VLADELEC).status_code == 200)
        proverka(f"{adres}: админ → 303 на пульт, без окна пароля", lokaciya(c, adres) == (303, "/pult"),
                 lokaciya(c, adres))
    for adres in ("/content", "/callplan", "/nezvonili", "/prelaunch", "/nabor"):
        proverka(f"{adres}: админ открывает по адресу, как раньше", c.get(adres, auth=ADMIN).status_code == 200)
    # отправку не зовём: 401 отдаёт проверка входа раньше обработчика
    for metod, adres in (("get", "/export/students.csv"), ("get", "/export/payments.csv"), ("post", "/api/chaty/send")):
        proverka(f"{metod.upper()} {adres}: админ → 401", getattr(c, metod)(adres, auth=ADMIN).status_code == 401)
    proverka("GET /export/students.csv: владелец → 200", c.get("/export/students.csv", auth=VLADELEC).status_code == 200)

    print("Пульт: блок «Разделы» вместо «Рабочих списков»")
    h = c.get("/pult", auth=ADMIN).text
    blok = h.split("id='razdely'", 1)[-1].split("</div>", 1)[0]
    proverka("у админа в разделах «Бланки групп» и «Списки»", "href='/blank-grupp'" in blok and "href='/admin/docs'" in blok)
    for adres in ("/segodnya", "/nabor", "/nezvonili", "/spiski", "/base/plan_"):
        proverka(f"пульт: ссылки {adres} нет", f"href='{adres}" not in h and f'href="{adres}' not in h)
    proverka("у админа раздела владельца нет",
             "Владельцу" not in blok and all(f"href='{a}'" not in h for a in ("/arhiv", "/owner", "/kontrol-oplat")))
    # 13.11.2026, Борис — меню, вечер 1: было «строка „Ещё“: вернуть ушедших, карта, конструктор».
    # Строка «Ещё» с пульта снята («убираем сразу то, что без потерь»); вместо неё одна строка
    # «Редко: Обзвон · Шахматка · Стоп-лист» — страницы, ушедшие из меню админов.
    proverka("строки «Ещё» нет: вернуть ушедших, карта, конструктор с пульта сняты",
             "Ещё:" not in blok and not any(f"href='{a}'" in blok for a in ("/obzvon", "/karta", "/constructor")))
    proverka("строка «Редко»: Обзвон · Шахматка · Стоп-лист",
             "<b>Редко:</b>" in blok and all(f"href='{a}'" in blok for a in ("/obzvon-dnya", "/grid", "/otkazy")))
    blok = c.get("/pult", auth=VLADELEC).text.split("id='razdely'", 1)[-1].split("</div>", 1)[0]
    # 13.11.2026, вечер 1: было «…и href='/arhiv'» — «Архив» ушёл из меню владельца на /owner
    # («Редко нужное»); в его разделе на пульте остались «Настройки» и «Контроль оплат».
    proverka("у владельца — и его раздел", "Владельцу" in blok and "href='/owner'" in blok
             and "href='/kontrol-oplat'" in blok and "href='/arhiv'" not in blok)


# --- В. Архив документов: признак в index.json, свёрнутый блок внизу списка ----------

DOK = "<!doctype html><title>стенд</title>" + "<p>документ стенда</p>" * 5


def vylozhit(c, put, slug, **eshche):
    body = {"slug": slug, "title": f"Документ {slug} (стенд)", "html": DOK + slug, **eshche}
    return c.post(put, json=body, auth=VLADELEC)


def zapis(c, put, slug) -> dict:
    """Запись документа из GET /api/…-docs — так её видят рутины перед выкладкой."""
    return next((x for x in c.get(put, auth=VLADELEC).json()["items"] if x["slug"] == slug), {})


def razrezat(html: str) -> tuple[str, str]:
    """(часть списка выше блока «Архив», сам блок)."""
    verh, _, niz = html.partition("<details class=arh")
    return verh, niz


def test_arhiv_dokumentov(c, m, work):
    from app import boris_docs
    print("Архив документов владельца (/boris/docs)")
    for s in ("stend-a", "stend-b", "stend-c"):
        vylozhit(c, "/api/boris-docs", s)
    r = c.post("/api/boris-docs/arhiv", json={"slug": "stend-b", "arhiv": True}, auth=VLADELEC)
    proverka("POST /api/boris-docs/arhiv: владелец → ok, документ помечен",
             r.status_code == 200 and r.json().get("ok") and r.json().get("izmeneno") == ["stend-b"], r.text[:200])
    h = c.get("/boris/docs", auth=VLADELEC).text
    verh, niz = razrezat(h)
    proverka("в списке — свёрнутый блок «Архив (1)»", "Архив (1)" in niz and "<details class=arh id=arhiv>" in h)
    proverka("помеченный документ — внутри блока, остальные — выше",
             "/boris/docs/stend-b" in niz and "/boris/docs/stend-b" not in verh
             and all(f"/boris/docs/{s}" in verh for s in ("stend-a", "stend-c")))
    proverka("по ссылке /boris/docs#arhiv блок раскрывается сам", "location.hash==='#arhiv'" in niz)
    proverka("строка «← Пульт · Архив страниц» над заголовком",
             'href="/pult"' in verh and 'href="/arhiv"' in verh)
    proverka("в подзаголовке нет «уходит в архив» (не путать с блоком «Архив»)", "уходит в архив" not in h)
    r = c.get("/boris/docs/stend-b", auth=VLADELEC)
    proverka("архивный документ открывается по прежнему адресу — 200",  r.status_code == 200, r.status_code)
    proverka("…и тело документа сервер не трогает (проверка выкладки mesta_post сравнивает байты)",
             r.text == DOK + "stend-b")
    z = zapis(c, "/api/boris-docs", "stend-b")
    proverka("GET /api/boris-docs: архивный в списке, с \"arhiv\": true", z.get("arhiv") is True, z)
    proverka("GET /api/boris-docs: у актуальных поля нет", "arhiv" not in zapis(c, "/api/boris-docs", "stend-a"))

    print("Признак переживает перевыкладку")
    r = vylozhit(c, "/api/boris-docs", "stend-b", note="перевыложено рутиной", order=5)
    z = zapis(c, "/api/boris-docs", "stend-b")
    proverka("перевыкладка без поля arhiv (как у рутин) — признак на месте",
             r.status_code == 200 and z.get("arhiv") is True and z.get("versions") == 2, z)
    proverka("…и запись обновилась: note, order", z.get("note") == "перевыложено рутиной" and z.get("order") == 5, z)
    vylozhit(c, "/api/boris-docs", "stend-b", arhiv=None)
    proverka("\"arhiv\": null — тоже «не трогать»", zapis(c, "/api/boris-docs", "stend-b").get("arhiv") is True)
    boris_docs.save("stend-b", "Документ stend-b (стенд)", DOK)
    proverka("save() из кода без параметра — признак на месте", zapis(c, "/api/boris-docs", "stend-b").get("arhiv"))
    vylozhit(c, "/api/boris-docs", "stend-b", arhiv=False)
    proverka("выкладка с \"arhiv\": false — снят", "arhiv" not in zapis(c, "/api/boris-docs", "stend-b"))
    vylozhit(c, "/api/boris-docs", "stend-b", arhiv=True)
    proverka("выкладка с \"arhiv\": true — поставлен", zapis(c, "/api/boris-docs", "stend-b").get("arhiv") is True)

    print("Снять признак, ошибки запроса")
    r = c.post("/api/boris-docs/arhiv", json={"slugs": ["stend-b", "net-takogo", "Плохой slug"], "arhiv": False},
               auth=VLADELEC).json()
    proverka("снять: izmeneno — stend-b, несуществующие — в net_takih",
             r.get("izmeneno") == ["stend-b"] and r.get("net_takih") == ["net-takogo", "Плохой slug"], r)
    h = c.get("/boris/docs", auth=VLADELEC).text
    proverka("блок «Архив» исчез, документ снова в списке", "<details" not in h and "/boris/docs/stend-b" in h)
    idx = json.load(open(os.path.join(work, "boris_docs", "index.json"), encoding="utf-8"))
    proverka("индекс цел: несуществующие slug в него не попали", "net-takogo" not in idx and "stend-b" in idx)
    r = c.post("/api/boris-docs/arhiv", json={"slug": "stend-a", "arhiv": "false"}, auth=VLADELEC).json()
    proverka("строка \"false\" значит «нет», а не «да»", r.get("arhiv") is False and r.get("uzhe_tak") == ["stend-a"], r)
    r = c.post("/api/boris-docs/arhiv", json={"slug": "stend-c"}, auth=VLADELEC).json()
    proverka("без поля arhiv — «в архив» (по умолчанию да)", r.get("arhiv") is True and r.get("izmeneno") == ["stend-c"], r)
    r = c.post("/api/boris-docs/arhiv", json={"slug": "stend-c"}, auth=VLADELEC).json()
    proverka("повтор — uzhe_tak, ничего не меняется", r.get("uzhe_tak") == ["stend-c"] and not r.get("izmeneno"), r)
    proverka("пустой запрос → 400", c.post("/api/boris-docs/arhiv", json={}, auth=VLADELEC).status_code == 400)
    proverka("slugs не списком → 400",
             c.post("/api/boris-docs/arhiv", json={"slugs": "stend-a"}, auth=VLADELEC).status_code == 400)
    for put in ("/api/boris-docs/arhiv", "/api/admin-docs/arhiv"):
        proverka(f"POST {put}: админ → 401", c.post(put, json={"slug": "stend-a"}, auth=ADMIN).status_code == 401)
        proverka(f"POST {put} без входа → 401", c.post(put, json={"slug": "stend-a"}).status_code == 401)
    n = len(c.get("/api/boris-docs", auth=VLADELEC).json()["items"])
    proverka("GET /api/boris-docs отдаёт все документы, архивные не пропадают",
             n == len(boris_docs.BORIS.spisok()) and n >= 4, n)

    print("Архив списков для админов (/admin/docs)")
    vylozhit(c, "/api/admin-docs", "stend-x")
    r = c.post("/api/admin-docs/arhiv", json={"slugs": ["stend-x"]}, auth=VLADELEC).json()
    proverka("POST /api/admin-docs/arhiv: владелец → ok", r.get("ok") and r.get("izmeneno") == ["stend-x"], r)
    h = c.get("/admin/docs", auth=ADMIN).text
    proverka("все списки в архиве → «Актуальных документов нет» и «Архив (1)»",
             "Актуальных документов нет" in h and "Архив (1)" in h)
    vylozhit(c, "/api/admin-docs", "stend-y")
    h = c.get("/admin/docs", auth=ADMIN).text
    verh, niz = razrezat(h)
    teg = "<details class=arh" + niz.split(">", 1)[0] + ">"
    proverka("/admin/docs под админом: блок архива есть и свёрнут", "Архив (1)" in niz and " open" not in teg, teg)
    proverka("актуальный список — выше блока, архивный — в блоке",
             "/admin/docs/stend-y" in verh and "/admin/docs/stend-x" in niz and "/admin/docs/stend-x" not in verh)
    proverka("у админов — «← Пульт», ссылки на архив страниц владельца нет",
             'href="/pult"' in verh and 'href="/arhiv"' not in h)
    r = c.get("/admin/docs/stend-x", auth=ADMIN)
    proverka("архивный список открывается по прежнему адресу, тело не тронуто",
             r.status_code == 200 and r.text == DOK + "stend-x", r.status_code)
    vylozhit(c, "/api/admin-docs", "stend-x", note="перевыложено")
    proverka("перевыкладка списка без поля arhiv — признак на месте",
             zapis(c, "/api/admin-docs", "stend-x").get("arhiv") is True)
    proverka("склады раздельные: у владельца stend-x нет", not zapis(c, "/api/boris-docs", "stend-x"))


# --- Г. Сквозные проверки решения владельца 12.11 -----------------------------------
# Решение (итог аудита с поправками скептиков) записано здесь отдельно от кода: случайная
# правка UBRANO или меню ломает тест, а не смену админов.

DOMOY = "домой"            # «замены нет — на стартовую страницу роли»
UBRANY_17 = {
    "/groups": "/mesta", "/ask": DOMOY, "/makety": DOMOY, "/callaudit": "/obzvon-dnya",
    "/suggest": "/waiting", "/plan311": "/mesta", "/bez-oplaty": "/voronka",
    "/ne-oplatili": "/voronka",             # не /admin/docs — замечание скептика 8
    "/vozvrat": "/obzvon-dnya", "/zapolnyaemost": "/mesta", "/zadachi-lizy": "/pult?who=Лиза",
    "/mesta/sostav": "/blank-grupp", "/ochered": "/pult", "/nedozvony": "/obzvon-dnya", "/perepiska": "/waiting",
}
# + старая /sverka (только владельцу) и второй, мёртвый обработчик /spiski — проверяются отдельно.
# Объединённые: переход ставим, только где цель закрывает главную нужду; остальные семь
# открываются по адресу до шага 2 (nav.PEREEZD).
OBEDINENY_PEREHOD = ("/brief", "/boris", "/obeshchaniya")
# 13.11.2026, шаг 2: /settings и /metrics переехали — отдельным словарём (раздел «Ж»).
# 13.11.2026, шаг 2 (днём): переехали и остальные пять — «ещё открываются по адресу» больше нет.
OBEDINENY_SHAG2: dict = {}
OBEDINENY_PEREEHALI = {"/settings": "/owner#mk", "/metrics": "/journal#schetchiki",
                       "/segodnya": "/pult#pervye", "/spiski": "/blank-grupp?vid=den",
                       "/zayavki": "/blank-grupp#budushchie", "/leads": "/istochniki#leads",
                       "/clicks": "/istochniki#clicks"}
# Чего не должно быть ни в меню, ни на пульте, ни на рабочих страницах.
ZAPRET = (set(UBRANY_17) | {"/sverka"} | ARHIVNYE | set(OBEDINENY_PEREHOD) | set(OBEDINENY_SHAG2)
          | set(OBEDINENY_PEREEHALI))
# Пункты раздела «Владельцу», которые админ всё же открывает, — каждый с причиной.
VLADELCU_I_ADMINU = {
    "/pult?who=Борис": "это пульт — стартовая страница админов, колонку выбирают параметром",
    "/promo": "решение Бориса 28.10 «промоутерами управляют админы»: «воронка и деньги по каждому» "
              "(методичка promo_upravlenie, ссылка «← Эффективность промоутеров» с /promo/grafik)",
}
# Пункты владельца, которые до 12.11 стояли в меню админов или на которые ведут методички
# админов: админа уводим на пульт (303), а не окном пароля — как с архивными страницами.
VLADELCU_NA_PULT = {"/journal", "/istochniki", "/sverka-chestnosti", "/audit"}
# Адреса из жёстких правил и рутин: всё, что было под этими началами, обязано остаться.
NACHALA = ("/api/plan/inbox", "/api/pult/", "/api/waiting", "/api/gruppy/", "/api/kadry", "/api/mesta",
           "/api/boris-docs", "/api/admin-docs", "/api/doc-state/", "/api/deploy", "/export/raw/", "/lk/", "/v/",
           "/go/", "/mango/", "/events/", "/wazzup/", "/api/wazzup/", "/api/tg/webhook", "/marquiz/", "/kadry/file/")
# API рутин ассистента и автопилота (опись рутин 12.11) — с тем входом, что был до шага 1
# (снимок 277ac71). Вход строже — рутина упадёт. Отчёты о самих админах (/api/audit,
# /api/istochniki, /api/sverka-chestnosti) рутины не читают — их вход проверяет раздел «Д».
RUTINY_VHOD = {
    "check_auth": """
GET /api/plan/inbox · POST /api/plan/inbox · POST /api/plan/inbox/done · GET /api/promo/grafik · GET /api/calls/list
GET /api/calls/recording · POST /api/gruppy/otvet · GET /api/gruppy/soobsheniya
GET /api/sync/status · GET /api/day/firsts · GET /api/mesta · GET /api/oplata/plan · GET /api/waiting · GET /api/yavka
POST /api/crm/comment · GET /api/crm/user · GET /api/pult/tasks · POST /api/pult/tasks · POST /api/pult/done
GET /api/obzvon-dnya · GET /api/kadry · POST /api/kadry/ocenka
POST /api/crm/roistat-merge · POST /api/roistat/auto-cards · GET /api/duty · GET /api/dialogs · GET /api/zayavki
GET /api/dolgi · GET /api/blank-grupp · POST /api/blank-grupp/edit · GET /api/perepiska/kto""",
    # 12.11.2026, личные входы (план, 5.2): пять адресов из строки выше переведены на вход
    # владельца. Рутинам они остаются открыты служебным ключом (vhod.SLUZH_TOCHNO и
    # SLUZH_PREFIKS — /export/raw, /kadry/file, /api/istochniki), общему admin — пока ключ
    # рутин ни разу не сработал (vhod.PEREHOD_ADMIN); это проверяет vhod_testy.py, группа В.
    "check_owner": """
GET /export/raw/{table}.json · GET /kadry/file/{day}/{name} · GET /api/sverka-chestnosti · GET /api/audit
GET /api/istochniki
POST /api/boris-docs · GET /api/boris-docs · GET /api/admin-docs · POST /api/admin-docs · GET /api/doc-state/{doc}
POST /api/doc-state/{doc} · POST /api/deploy · GET /api/tbank/get · GET /api/komtet/cheki · POST /api/ads/vk
POST /api/ads/direct · GET /api/mkweb/history · GET /api/backup/db · POST /api/hooppy/post · GET /api/hooppy/posty
POST /api/hooppy/udalit""",
    "check_pedagog_or_admin": "GET /api/english/karta · POST /api/english/karta/video · POST /api/english/karta/video-upload",
    "": "GET /api/health",
}
# Ссылка в HTML: href/action/адрес в кавычках (в том числе из скриптов) и app.kidsup.ru/… в тексте.
SSYLKA = re.compile(r"""(?:(?:href|action)\s*=\s*["']?|["'`]|app\.kidsup\.ru)(?:https?://app\.kidsup\.ru)?(/[^"'`\s<>?#)]*)""")


def domoy(kto) -> str:
    """Стартовая страница роли: админу — пульт, владельцу — список его документов
    (не документ start: его сборка 10.11 повторяет старое меню — замечание проверки 12.11)."""
    return "/boris/docs" if kto == VLADELEC else "/pult"


def ssylki(html: str) -> set:
    return set(SSYLKA.findall(html))


def perehod_i_cel(c, adres, kto):
    """(код, куда ведёт, код цели): цель открываем тем же входом и тоже без перехода —
    так видна цепочка (303 → 303) или петля."""
    kod, loc = lokaciya(c, adres, kto)
    if kod != 303 or not loc:
        return kod, loc, None
    return kod, loc, c.get(loc, auth=kto, follow_redirects=False).status_code


def _razdely_pulta(c, kto) -> str:
    return c.get("/pult", auth=kto).text.split("id='razdely'", 1)[-1].split("</div>", 1)[0]


def test_punkty_menu(c, m):
    from app import nav
    print("Г1. Каждый пункт меню открывается у того, кому показан")
    for kto, rol in ((ADMIN, "admin"), (VLADELEC, "owner")):
        for razdel, punkty in nav.menu(rol):
            for imya, adres in punkty:
                kod = c.get(adres, auth=kto, follow_redirects=False).status_code
                proverka(f"{rol}: «{razdel} → {imya}» {adres} → 200", kod == 200, kod)
    print("Г1. Пункты владельца у админа")
    sh = shapka(c, "/liniya", ADMIN)
    pult = _razdely_pulta(c, ADMIN)
    # 13.11.2026, вечер 1: пять страниц владельца ушли из его меню (nav.REDKO_OWNER), но остались
    # его страницами — проверяем их вместе с пунктами меню, доступы не менялись.
    for imya, adres in list(nav.OWNER[1]) + list(nav.REDKO_OWNER):
        v_menu = f'href="{adres}"' in sh.split('id="nav-razdely"', 1)[-1] or f"href='{adres}'" in pult
        put = adres.split("?")[0]
        kod = c.get(adres, auth=ADMIN, follow_redirects=False).status_code
        if adres in VLADELCU_I_ADMINU:
            proverka(f"«{imya}» {adres}: в меню админа нет; открывается ({VLADELCU_I_ADMINU[adres][:60]}…)",
                     not v_menu and kod == 200, (v_menu, kod))
        elif adres in VLADELCU_NA_PULT:
            kuda = lokaciya(c, adres)
            proverka(f"«{imya}» {adres}: в меню и на пульте админа нет, по адресу — 303 на пульт без окна пароля",
                     not v_menu and kuda == (303, "/pult"), (v_menu, kuda))
        else:
            proverka(f"«{imya}» {adres}: в меню и на пульте админа нет, по адресу — 401/403",
                     not v_menu and kod in (401, 403), (v_menu, kod))
        proverka(f"«{imya}» {put}: не совпадает с пунктом админов (кроме пульта)",
                 put == "/pult" or put not in {a.split("?")[0] for a in _adresa(nav.ADMIN)})
    proverka("исключений ровно два, и оба — пункты владельца",
             set(VLADELCU_I_ADMINU) <= set(_adresa([nav.OWNER])) and len(VLADELCU_I_ADMINU) == 2)
    # 13.11.2026, вечер 1: было «…<= пункты nav.OWNER»; три из четырёх (/journal, /istochniki, /audit)
    # ушли из меню владельца в nav.REDKO_OWNER — правило увода на пульт для них прежнее.
    proverka("уводим на пульт ровно четыре страницы владельца",
             VLADELCU_NA_PULT <= set(_adresa([nav.OWNER])) | {a for _, a in nav.REDKO_OWNER} and len(VLADELCU_NA_PULT) == 4)
    for adres in sorted(VLADELCU_NA_PULT):
        r = c.get(adres, auth=ADMIN, follow_redirects=False)
        proverka(f"{adres}: у админа нет WWW-Authenticate (браузер не покажет окно пароля)",
                 "www-authenticate" not in {k.lower() for k in r.headers}, dict(r.headers))


def test_perehody_bez_cepochek(c, m):
    print("Г2. Убранные и объединённые: 303 на ожидаемый адрес, цель сама отвечает 200")
    proverka("таблица переходов в коде совпадает с решением",
             {k: ("/" if v == DOMOY else v) for k, v in UBRANY_17.items()} == m.UBRANO,
             sorted(set(UBRANY_17) ^ set(m.UBRANO)))
    for kto, rol in ((ADMIN, "админ"), (VLADELEC, "владелец")):
        for otkuda, kuda in UBRANY_17.items():
            zhdem = domoy(kto) if kuda == DOMOY else quote(kuda, safe="/?=&")
            kod, loc, cel = perehod_i_cel(c, otkuda, kto)
            proverka(f"{rol}: {otkuda} → 303 {zhdem} → 200", (kod, loc, cel) == (303, zhdem, 200), (kod, loc, cel))
        zhdem = {"/brief": "/liniya", "/boris": "/boris/docs" if kto == VLADELEC else "/pult",
                 "/obeshchaniya": "/pult?who=" + quote("Лиза")}
        for otkuda in OBEDINENY_PEREHOD:
            kod, loc, cel = perehod_i_cel(c, otkuda, kto)
            proverka(f"{rol}: {otkuda} → 303 {zhdem[otkuda]} → 200", (kod, loc, cel) == (303, zhdem[otkuda], 200),
                     (kod, loc, cel))
        kod, loc, cel = perehod_i_cel(c, f"/brief?phone={SEMYA}", kto)
        proverka(f"{rol}: /brief?phone=… → «Линия» с номером → 200",
                 (kod, loc, cel) == (303, f"/liniya?phone={SEMYA[-10:]}", 200), (kod, loc, cel))
    kod, loc, cel = perehod_i_cel(c, "/sverka", VLADELEC)
    proverka("владелец: /sverka → 303 на сверку оплат в его документах → 200",
             kod == 303 and loc in ("/boris/docs/sverka-oplat", "/boris/docs") and cel == 200, (kod, loc, cel))
    proverka("админ: /sverka → 401 (страница была только владельцу)", lokaciya(c, "/sverka")[0] == 401)
    for adres in ZAPRET | set(OBEDINENY_PEREHOD):
        proverka(f"{adres}: без входа — 401, адрес замены не виден",
                 lokaciya(c, adres, kto=None) == (401, None), lokaciya(c, adres, kto=None))

    print("Г2. Объединённые без перехода (перенос содержимого — шаг 2)")
    from app import nav
    # 13.11.2026, шаг 2: было «пять записаны в блок „Переезжают“ и открываются по адресу (200)».
    # Содержимое перенесено, все пять стали переходами — их проверяет раздел «З»; здесь остаётся
    # сверка: в списке «переезжают» нет никого, чего нет в решении.
    pereezd = {a: kuda for a, _, _, kuda, _ in nav.PEREEZD}
    proverka("«Переезжают» совпадает с решением: на 13.11 — пусто, все семь переехали", pereezd == OBEDINENY_SHAG2,
             pereezd)


def test_arhivnye(c, m):
    from app import nav
    print("Г3. Архивные страницы: открываются по адресу, в меню их нет")
    for adres in sorted(ARHIVNYE):
        kod = c.get(adres, auth=VLADELEC, follow_redirects=False).status_code
        proverka(f"{adres}: владелец → 200", kod == 200, kod)
    for kto, rol in ((ADMIN, "admin"), (VLADELEC, "owner")):
        v_nav = ARHIVNYE & {a.split("?")[0] for a in _adresa(nav.menu(rol))}
        razdely = shapka(c, "/liniya", kto).split('id="nav-razdely"', 1)[-1]
        v_shapke = ARHIVNYE & ssylki(razdely)
        v_pulte = ARHIVNYE & ssylki(c.get("/pult", auth=kto).text)
        proverka(f"{rol}: архивных нет ни в меню, ни в шапке, ни на пульте",
                 not (v_nav or v_shapke or v_pulte), (v_nav, v_shapke, v_pulte))
    proverka("/arhiv только владельцу: админ → 401, владелец → 200",
             c.get("/arhiv", auth=ADMIN).status_code == 401 and c.get("/arhiv", auth=VLADELEC).status_code == 200)


def _stroki_strok(tree) -> list:
    """(файл, строка, адрес) — каждое упоминание убранного адреса в app/, кроме
    объявлений маршрутов, таблицы UBRANO, комментариев и докстрингов."""
    import ast
    rx = {a: re.compile(r"(?:^|(?<=[\"'\s(=`>])|(?<=kidsup\.ru))" + re.escape(a) + r"(?=$|[\"'?#\s)<&`])")
          for a in set(UBRANY_17) | {"/sverka"}}
    nashli = []
    for papka, _, fajly in os.walk(os.path.join(tree, "app")):
        for f in sorted(fajly):
            p = os.path.join(papka, f)
            rel = os.path.relpath(p, tree)
            if f.endswith(".py"):
                t = open(p, encoding="utf-8").read()
                derevo = ast.parse(t)
                mimo = set()
                for n in ast.walk(derevo):
                    tela = getattr(n, "body", None)
                    if isinstance(tela, list) and tela and isinstance(tela[0], ast.Expr) \
                            and isinstance(tela[0].value, ast.Constant):
                        mimo.add(id(tela[0].value))                   # докстринг
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        for d in n.decorator_list:                    # @app.get("/sverka", …)
                            mimo.update(id(x) for x in ast.walk(d))
                    if isinstance(n, ast.Assign) and any(getattr(x, "id", "") == "UBRANO" for x in n.targets):
                        mimo.update(id(x) for x in ast.walk(n.value))
                for n in ast.walk(derevo):
                    if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in mimo:
                        nashli += [(rel, n.lineno, a) for a, r in rx.items() if r.search(n.value)]
            elif f.endswith((".html", ".js", ".css")):
                t = open(p, encoding="utf-8").read()
                t = re.sub(r"\{#.*?#\}|<!--.*?-->", lambda x: "\n" * x.group().count("\n"), t, flags=re.S)
                for i, stroka in enumerate(t.split("\n"), 1):
                    nashli += [(rel, i, a) for a, r in rx.items() if r.search(stroka)]
    return nashli


def test_net_ssylok(c, m, tree):
    from app import nav
    print("Г4. Нет ссылок на убранные, архивные и объединённые адреса")
    for kto, rol in ((ADMIN, "admin"), (VLADELEC, "owner")):
        razdely = shapka(c, "/liniya", kto).split('id="nav-razdely"', 1)[-1]
        lishnie = ZAPRET & ssylki(razdely)
        proverka(f"{rol}: в меню шапки — ни одной", not lishnie, sorted(lishnie))
        lishnie = ZAPRET & ssylki(c.get("/pult", auth=kto).text)
        proverka(f"{rol}: на пульте (весь HTML) — ни одной", not lishnie, sorted(lishnie))
    # 13.11.2026, вечер 1: и на страницах, ушедших из меню (nav.REDKO_ADMIN / REDKO_OWNER), — они живые
    for imya, adres in [p for _, punkty in nav.ADMIN for p in punkty] + list(nav.REDKO_ADMIN):
        lishnie = ZAPRET & ssylki(c.get(adres, auth=ADMIN).text)
        proverka(f"admin: на странице «{imya}» {adres} — ни одной", not lishnie, sorted(lishnie))
    # у владельца: /settings — из плашки «укажите ключ МойКласса» (на стенде ключа нет) и со
    # страницы «Настройки»: только там ключ и меняется; /arhiv — сама страница архива
    # 13.11.2026, шаг 2: плашка теперь зовёт на /owner#mk, исключение осталось одно — сама
    # /owner: её форма ключа шлёт POST на /settings (адрес под защитой vhod.TOLKO_CHELOVEK)
    for imya, adres in list(nav.OWNER[1]) + list(nav.REDKO_OWNER):
        if adres == "/arhiv":
            continue
        mozhno = {"/settings"} if adres == "/owner" else set()
        lishnie = (ZAPRET - mozhno) & ssylki(c.get(adres, auth=VLADELEC).text)
        proverka(f"owner: на странице «{imya}» {adres} — ни одной (кроме формы ключа МойКласса на /owner)",
                 not lishnie, sorted(lishnie))
    nashli = _stroki_strok(tree)
    proverka("grep по app/: убранные адреса остались только в таблице переходов и объявлениях маршрутов",
             not nashli, nashli[:10])
    est = {a for a in set(UBRANY_17) | {"/sverka"} if re.search(re.escape(f'"{a}"'), open(
        os.path.join(tree, "app", "main.py"), encoding="utf-8").read())}
    proverka("…и сами строки таблицы на месте (grep их видит)", est == set(UBRANY_17) | {"/sverka"},
             sorted(set(UBRANY_17) - est))


def test_perepiska_send(c, m):
    print("Г6. GET /perepiska?send=1 ничего не отправляет")
    import app.perepiska as P
    vyzovy = []
    P.run = lambda *a, **k: vyzovy.append((a, k)) or {"всего": 0, "ответили": 0, "человеку": 0, "ночь": False,
                                                      "строки": []}
    bylo = list(lt.LOVUSHKI)
    for kto in (ADMIN, VLADELEC):
        r = c.get("/perepiska?send=1&dry=0", auth=kto, follow_redirects=False)
        proverka("ответ — переход 303 на «Ответы», тело пустое",
                 r.status_code == 303 and r.headers.get("location") == "/waiting" and not r.content.strip(),
                 (r.status_code, r.headers.get("location")))
    proverka("переписка не запускалась, ловушки отправки пусты", not vyzovy and lt.LOVUSHKI == bylo,
             (vyzovy, lt.LOVUSHKI[len(bylo):]))
    gde = [r for r in m.app.routes if getattr(r, "path", "") == "/perepiska"]
    proverka("на адресе /perepiska один маршрут — переход", len(gde) == 1 and gde[0].endpoint.__name__ == "perehod",
             [(r.path, r.endpoint.__name__) for r in gde])
    proverka("отчёт для автопилота на месте: GET /api/perepiska (только чтение)",
             "GET /api/perepiska" in marshruty(m) and "send" not in inspect.signature(m.perepiska_report).parameters)


def test_marshruty_zhestkie(m, fajl_do):
    print("Г8. Адреса из жёстких правил и рутин — на месте и под прежним входом")
    do = set(json.load(open(fajl_do, encoding="utf-8")))
    posle = marshruty(m)
    for nachalo in NACHALA:
        byli = {x for x in do if x.split(" ", 1)[1].startswith(nachalo)}
        net = sorted(byli - posle)
        proverka(f"всё под {nachalo}… ({len(byli)}) на месте", byli and not net, net or "в снимке «до» нет ни одного")
    vhod = {}
    for r in m.app.routes:
        for metod in getattr(r, "methods", ()):
            deps = getattr(getattr(r, "dependant", None), "dependencies", [])
            vhod[f"{metod} {r.path}"] = {getattr(d.call, "__name__", "") for d in deps}
    for nado, spisok in RUTINY_VHOD.items():
        adresa = [x.strip() for x in spisok.replace("\n", " · ").split(" · ") if x.strip()]
        net = [x for x in adresa if x not in posle]
        drugoj = {x: sorted(vhod[x]) for x in adresa if x in vhod and vhod[x] != ({nado} if nado else set())}
        proverka(f"{len(adresa)} адресов рутин под входом «{nado or 'без входа'}» — на месте, вход прежний",
                 not net and not drugoj, (net, drugoj))
    hooki = ("POST /mango/events", "POST /mango/{rest:path}", "POST /events/{rest:path}", "POST /wazzup/webhook",
             "POST /marquiz/webhook", "POST /api/tg/webhook/{secret}")
    proverka("вебхуки — без входа (их зовут Mango, Wazzup, Telegram, Марквиз)",
             all(x in vhod and not vhod[x] for x in hooki), {x: vhod.get(x) for x in hooki})


# --- Д. Поправки по замечаниям проверки 12.11 (перед выкаткой) ------------------------

def _plashka(html: str) -> str:
    """Текст плашки «архивная / переезжает» вверху страницы, или пусто."""
    return html.split('plashka-arh', 1)[1].split("</div>", 1)[0] if 'plashka-arh' in html else ""


def test_popravki(c, m):
    print("Д1. Вечерний прозвон не обещает список, которого нет")
    import app.autopilot as ap
    import app.mango as mango
    zadachi = []

    class Mk:                                   # вместо МойКласса: только запоминаем тело задачи
        def post(self, put, telo):
            zadachi.append((put, telo))
    bylo_missed, bylo_duty = mango.missed, ap._duty_manager
    mango.missed = lambda *a, **k: [{"phone": f"799900000{i}", "attempts": 1} for i in range(3)]
    ap._duty_manager = lambda: 1
    try:
        ap.evening_recall(Mk())
    finally:
        mango.missed, ap._duty_manager = bylo_missed, bylo_duty
    telo = zadachi[0][1]["body"] if zadachi else ""
    proverka("задача с числом недозвонов", "3 утренних недозвонов" in telo, telo)
    proverka("…без ссылки на «Обзвон» и вкладку «Недозвоны» (там другие люди)",
             "obzvon-dnya" not in telo and "nedozvony" not in telo and "вкладк" not in telo, telo)

    print("Д2. Отчёты о самих админах — только владельцу")
    for adres in ("/api/audit", "/api/istochniki", "/api/sverka-chestnosti"):
        proverka(f"GET {adres}: админ → 401", c.get(adres, auth=ADMIN).status_code == 401)
        try:
            kod = c.get(adres, auth=VLADELEC).status_code
        except Exception as e:                  # на пустом стенде отчёт может упасть — нам важен вход
            kod = type(e).__name__
        proverka(f"GET {adres}: владелец проходит вход", kod != 401, kod)
    kod = c.post("/api/audit/resolve?flag_id=0&status=ok", auth=ADMIN).status_code
    proverka("POST /api/audit/resolve под общим входом (кнопки /pravila-kontrol)", kod not in (401, 403), kod)

    print("Д3. Рассылка приглашений в чаты: только владелец и только JSON-телом")
    import app.chaty as chaty
    vyzovy = []
    bylo = chaty.send, chaty.deliver
    chaty.send = lambda dry=True: vyzovy.append(("send", dry)) or {"ok": True, "queued": 0, "total": 0}
    chaty.deliver = lambda limit=3, dry=True: vyzovy.append(("deliver", dry)) or {"ok": True}
    try:
        proverka("POST /api/chaty/deliver: админ → 401", c.post("/api/chaty/deliver", auth=ADMIN).status_code == 401)
        for put in ("/api/chaty/send", "/api/chaty/deliver"):
            imya = put.rsplit("/", 1)[1]
            vyzovy.clear()
            r = c.post(put + "?dry=0", auth=VLADELEC)
            proverka(f"{put}?dry=0 без тела — только подсчёт", r.status_code == 200 and vyzovy == [(imya, True)],
                     (r.status_code, vyzovy))
            vyzovy.clear()
            r = c.post(put, auth=VLADELEC, content='{"dry": false}', headers={"content-type": "text/plain"})
            proverka(f"{put}: тело формы чужой страницы (text/plain) → 422, ничего не вызвано",
                     r.status_code == 422 and not vyzovy, (r.status_code, vyzovy))
            vyzovy.clear()
            r = c.post(put, auth=VLADELEC, json={"dry": False})
            proverka(f"{put}: {{\"dry\": false}} JSON-телом — по-настоящему (здесь подмена)",
                     r.status_code == 200 and vyzovy == [(imya, False)], (r.status_code, vyzovy))
    finally:
        chaty.send, chaty.deliver = bylo
    h = c.get("/chaty", auth=VLADELEC).text
    proverka("кнопка на /chaty шлёт JSON-тело, а не ?dry=0",
             "?dry=0" not in h and "JSON.stringify({dry: false})" in h)

    print("Д4. Плашка на архивных и переезжающих страницах")
    p = _plashka(c.get("/content", auth=ADMIN).text)
    proverka("/content у админа: «Архивная страница», не отправлять семьям, ссылка на «Подбор группы»",
             "Архивная страница" in p and "без проверки не отправлять" in p and 'href="/enrollment"' in p, p[:200])
    p = _plashka(c.get("/callplan", auth=ADMIN).text)
    proverka("/callplan у админа: ссылка на «Обзвон»", "Архивная страница" in p and 'href="/obzvon-dnya"' in p,
             p[:200])
    # /prelaunch рисует свой HTML без общей шапки — плашки там нет (светофор, текстов для семей нет)
    for adres in ("/nezvonili", "/nabor"):
        proverka(f"{adres} у админа: плашка есть", "Архивная страница" in _plashka(c.get(adres, auth=ADMIN).text))
    p = _plashka(c.get("/students", auth=VLADELEC).text)
    proverka("/students у владельца: плашка и ссылка на «Линию»", 'href="/liniya"' in p, p[:200])
    # 13.11.2026, шаг 2: /metrics переехала в «Журнал» и стала переходом — плашки «переезжает»
    # на ней больше нет (проверки перехода — раздел «Ж»). Плашку «переезжает» со ссылкой на
    # страницу владельца и «скоро переедет» без ссылки проверяем на тех, что ещё не переехали.
    proverka("/metrics и /settings: плашки нет — страницы переехали",
             m.nav.plashka("/metrics", "owner") is None and m.nav.plashka("/settings", "owner") is None)
    # 13.11.2026, шаг 2 (днём): /leads и /segodnya тоже переехали. Было: у владельца на /leads плашка
    # «переезжает» со ссылкой на «Откуда заявки», у админа — «скоро переедет» без ссылки, на /segodnya —
    # ссылка на пульт. Страниц с плашкой «переезжает» не осталось; само правило (админу не показывать
    # ссылку на страницу владельца) проверяем на функции с подставной записью.
    proverka("переехавшие /leads, /clicks, /segodnya, /spiski, /zayavki: плашки нет",
             not any(m.nav.plashka(a, r) for a in ("/leads", "/clicks", "/segodnya", "/spiski", "/zayavki")
                     for r in ("owner", "admin")))
    bylo = m.nav.PEREEZD
    m.nav.PEREEZD = [("/primer", "Пример", "Откуда заявки", "/istochniki", "Что-то ещё не перенесено."),
                     ("/primer2", "Пример 2", "Пульт", "/pult", "Что-то ещё не перенесено.")]
    try:
        pv, pa = m.nav.plashka("/primer", "owner"), m.nav.plashka("/primer", "admin")
        pp = m.nav.plashka("/primer2", "admin")
    finally:
        m.nav.PEREEZD = bylo
    proverka("правило плашки: владельцу — «переезжает» со ссылкой на «Откуда заявки»",
             "переезжает" in pv["vid"] and pv["kuda"] == [("Откуда заявки", "/istochniki")], pv)
    proverka("правило плашки: админу — «скоро переедет», без ссылки на закрытую ему страницу владельца",
             "скоро переедет" in pa["vid"] and pa["kuda"] == [], pa)
    proverka("правило плашки: админу на страницу, которая ему открыта, — ссылка на пульт",
             pp["kuda"] == [("Пульт", "/pult")], pp)
    for imya, adres in [x for _, punkty in m.nav.ADMIN for x in punkty] + list(m.nav.REDKO_ADMIN):
        if adres in ("/pult",):
            continue
        r = c.get(adres, auth=ADMIN)
        proverka(f"рабочая страница «{imya}» — без плашки", 'plashka-arh' not in r.text)

    # 13.11.2026, шаг 2: временную строку «← Пульт · Разделы» заменила общая шапка — в ней
    # есть и «Пульт», и кнопка «☰ Разделы». Ожидание поправлено нарочно: строки больше нет.
    print("Д5. Свои страницы меню: путь назад на пульт — в общей шапке")
    for adres in ("/blank-grupp", "/dolgi", "/otkazy"):
        h = c.get(adres, auth=ADMIN).text
        proverka(f"{adres}: в шапке «Пульт» и «☰ Разделы», временной строки шага 1 нет",
                 'href="/pult"' in _koren(h) and 'id="nav-toggle"' in _koren(h) and 'href="/pult#razdely"' not in h)

    print("Д6. Заголовки вкладок — как пункты меню")
    for adres, imya in (("/enrollment", "Подбор группы"), ("/courses", "Описания занятий"),
                        ("/voronka", "Без оплаты"), ("/otkazy", "Стоп-лист")):
        h = c.get(adres, auth=ADMIN).text
        zag = h.split("<title>", 1)[-1].split("</title>", 1)[0]
        proverka(f"{adres}: вкладка «{imya} — KidsUP»", zag.strip() == f"{imya} — KidsUP", zag)
    stary = [f for f in os.listdir(os.path.join(ROOT, "app", "templates"))
             if "KidsUp Analytics" in open(os.path.join(ROOT, "app", "templates", f), encoding="utf-8").read()]
    proverka("старого имени «KidsUp Analytics» в шаблонах нет", not stary, stary)

    print("Д7. Пульт: в «Ещё» нет методички из архива базы знаний")
    h = c.get("/pult", auth=ADMIN).text
    proverka("нет ссылки на /base/skripty_v3", "/base/skripty_v3" not in h)

    print("Д8. Педагог по ключу: ключ идёт по ссылкам карточка ↔ методика")
    kluch = m.db.get_setting("pedagog_key")
    h = c.get(f"/english/karta?k={kluch}").text
    proverka("карточка → методика с ключом", f'href="/english/metodika?k={kluch}#otchet"' in h)
    h = c.get("/english/karta", auth=ADMIN).text
    proverka("у админа ссылка без ключа", 'href="/english/metodika#otchet"' in h)
    h = c.get(f"/english/metodika?k={kluch}").text
    proverka("методика переносит ?k= в ссылки на карточку", 'a[href^="/english/karta"]' in h and "encodeURIComponent(k)" in h)


# --- Е. Шапка шага 2 (13.11.2026): одна шапка на всех рабочих страницах ---------------

def _koren(html: str) -> str:
    """Общая шапка страницы: от id="kidsup-nav" до подключения её скрипта (у педагога
    скрипта нет — до конца первой строки шапки)."""
    i = html.find('id="kidsup-nav"')
    if i < 0:
        return ""
    j = html.find("/static/nav.js", i)
    if j < 0:
        j = html.find("</div></div>", i)
    return html[i:j if j > 0 else len(html)]


# Страницы со своим HTML и несколько страниц base.html: на каждой шапка ровно одна.
S_SHAPKOJ_ADMIN = ["/pult", "/blank-grupp", "/promo/grafik", "/admin/docs", "/dolgi", "/otkazy", "/karta",
                   "/pravila-kontrol", "/proverka-mk", "/blank-grupp?vid=den", "/mesta", "/waiting", "/liniya"]
S_SHAPKOJ_VLADELEC = ["/boris/docs", "/owner", "/kontrol-oplat", "/sverka-chestnosti", "/audit",
                      "/kamery/nastroyka", "/arhiv"]
ADRESA_VLADELCA = ("/owner", "/arhiv", "/kontrol-oplat", "/boris/docs", "/sverka-chestnosti", "/audit")


def test_shapka_shag2(c, m, work):
    from app import nav
    BRAUZER = {"Accept": "text/html,application/xhtml+xml"}

    print("Е1. Шапка ровно одна — под админом")
    for adres in S_SHAPKOJ_ADMIN:
        r = c.get(adres, auth=ADMIN)
        h = _koren(r.text)
        proverka(f"{adres}: 200, шапка одна, стили и скрипт подключены",
                 r.status_code == 200 and r.text.count('id="kidsup-nav"') == 1
                 and "/static/nav.css" in h and r.text.count("/static/nav.js") == 1,
                 (r.status_code, r.text.count('id="kidsup-nav"')))
        vlad = [a for a in ADRESA_VLADELCA if f'href="{a}"' in h]
        proverka(f"{adres}: в шапке админа нет раздела и адресов владельца", "Владельцу" not in h and not vlad, vlad)
        proverka(f"{adres}: шапка — не <header> и стоит в теле страницы, после <body>",
                 "<header" not in h and 0 <= r.text.lower().find("<body") < r.text.find('id="kidsup-nav"'))
    h = _koren(c.get("/dolgi", auth=ADMIN).text)
    proverka("нижняя панель админа: Пульт · Ответы · Линия · ☰ Разделы",
             [x for x in re.findall(r'class="kn-podpis">([^<]+)<', h)] == ["Пульт", "Ответы", "Линия", "Разделы"])
    proverka("у «Ответов» в панели место под число, бейдж берёт числа из /api/waiting/schet",
             'id="kn-chislo"' in h and 'data-schet="/api/waiting/schet"' in h)
    proverka("в шапке /dolgi подсвечен пункт «Долги», его раздел раскрыт",
             'href="/dolgi" class="kn-tut" aria-current="page"' in h and "kn-raskryt kn-zdes" in h)

    print("Е2. Шапка ровно одна — под владельцем")
    for adres in S_SHAPKOJ_ADMIN + S_SHAPKOJ_VLADELEC:
        r = c.get(adres, auth=VLADELEC)
        h = _koren(r.text)
        proverka(f"{adres}: 200, шапка одна, в ней раздел «Владельцу»",
                 r.status_code == 200 and r.text.count('id="kidsup-nav"') == 1 and "Владельцу" in h
                 and 'href="/kontrol-oplat"' in h, (r.status_code, r.text.count('id="kidsup-nav"')))
    h = _koren(c.get("/owner", auth=VLADELEC).text)
    # 13.11.2026, проверка шага 2: было «Контроль оплат» — на телефоне 320–360 px подпись
    # обрезалась («Контроль оп…»); на кнопке теперь «Оплаты», адрес тот же
    proverka("нижняя панель владельца: Главная · Мои дела · Оплаты · ☰ Разделы",
             [x for x in re.findall(r'class="kn-podpis">([^<]+)<', h)] == ["Главная", "Мои дела", "Оплаты", "Разделы"]
             and 'class="kn-knopka" href="/kontrol-oplat"' in h)
    t = c.get("/pult", auth=ADMIN).text
    proverka("/pult: блок «Разделы» внизу остался", "id='razdely'" in t)
    t = c.get("/otkazy", auth=ADMIN).text
    proverka("/otkazy: у страницы появился <body>", "<body>" in t and t.lstrip().lower().startswith("<!doctype"))

    print("Е3. Педагог по ключу")
    kluch = m.db.get_setting("pedagog_key")
    for adres in ("/english/karta", "/pedagog"):
        r = c.get(f"{adres}?k={kluch}")
        h = _koren(r.text)
        proverka(f"{adres}: шапка педагога одна — «KidsUP · педагогам», один пункт",
                 r.status_code == 200 and r.text.count('id="kidsup-nav"') == 1 and "педагогам" in h
                 and "Карточка речи" in h and h.count("<a ") == 2, (r.status_code, h[:200]))
        proverka(f"{adres}: ни меню админов, ни поиска, ни бейджа, ни панели, ни скрипта шапки",
                 all(x not in r.text for x in ('id="nav-razdely"', 'id="gs-input"', 'id="wait-badge"', "kn-panel",
                                               "/static/nav.js", "Владельцу", 'href="/pult"', 'href="/liniya"')))
        proverka(f"{adres}: шапка не опрашивает /api/waiting", "/api/waiting" not in h and "data-schet" not in h)
        proverka(f"{adres}: ключ — в ссылках шапки", h.count(f'href="/english/karta?k={kluch}"') == 2)
    r = c.get("/pedagog?k=chuzhoy-kluch")
    proverka("/pedagog с чужим ключом: шапки нет, ключ в ссылки не попал",
             'id="kidsup-nav"' not in r.text and "chuzhoy-kluch" not in r.text)
    r = c.get("/english/karta?k=chuzhoy-kluch", auth=ADMIN)
    proverka("админ с чужим ключом в адресе: шапка админа, чужой ключ в шапку не попал",
             r.status_code == 200 and 'id="nav-razdely"' in r.text and "chuzhoy-kluch" not in _koren(r.text))
    plohoy = 'a b"<i>&k=\'x'
    m.db.set_setting("pedagog_key", plohoy)
    try:
        for adres in ("/english/karta", "/pedagog"):
            r = c.get(adres, params={"k": plohoy})
            h = _koren(r.text)
            proverka(f"{adres}: ключ в ссылках шапки экранирован",
                     r.status_code == 200 and f'href="/english/karta?k={quote(plohoy, safe="")}"' in h
                     and "<i>" not in h and 'b"' not in h and "'x" not in h, h[:300])
    finally:
        m.db.set_setting("pedagog_key", kluch)
    proverka("nav.shapka педагогу без ключа — ссылка без ?k=",
             'href="/english/karta"' in nav.shapka("pedagog", "/english/karta"))

    print("Е4. Документы: тонкая строка только браузеру, <!doctype — первым")
    slug = next(s for _, punkty in m.DOC_GROUPS for s, _, _ in punkty
                if not s.startswith("plan_") and os.path.exists(os.path.join(ROOT, "docs", f"{s}.html")))
    fajl = open(os.path.join(ROOT, "docs", f"{slug}.html"), encoding="utf-8").read()
    r = c.get(f"/base/{slug}", auth=ADMIN, headers=BRAUZER)
    proverka(f"/base/{slug} браузеру: начинается с <!doctype, строка «← База знаний» одна, полной шапки нет",
             r.text.lstrip()[:9].lower() == "<!doctype" and r.text.count('id="kidsup-doc-bar"') == 1
             and "← База знаний" in r.text and 'href="/base"' in r.text and 'id="kidsup-nav"' not in r.text,
             r.text[:80])
    proverka("…строка стоит после <body>, а не перед <!doctype>",
             0 <= r.text.lower().find("<body") < r.text.find('id="kidsup-doc-bar"'))
    r = c.get(f"/base/{slug}", auth=ADMIN)
    proverka(f"/base/{slug} без Accept: text/html — тело методички без изменений",
             r.text == fajl and 'id="kidsup-doc-bar"' not in r.text)
    kusok = next(s for _, punkty in m.DOC_GROUPS for s, _, _ in punkty if not s.startswith("plan_")
                 and os.path.exists(os.path.join(ROOT, "docs", f"{s}.html"))
                 and not open(os.path.join(ROOT, "docs", f"{s}.html"), encoding="utf-8").read().lstrip()[:9].lower().startswith("<!doctype"))
    fajl = open(os.path.join(ROOT, "docs", f"{kusok}.html"), encoding="utf-8").read()
    r = c.get(f"/base/{kusok}", auth=ADMIN, headers=BRAUZER)
    proverka(f"/base/{kusok} (файл без <!doctype>) браузеру: ответ всё равно начинается с <!doctype, строка одна",
             r.text[:15].lower() == "<!doctype html>" and r.text.count('id="kidsup-doc-bar"') == 1
             and r.text.find("<!doctype") < r.text.find('id="kidsup-doc-bar"'), r.text[:80])
    proverka(f"/base/{kusok} без Accept: text/html — файл как лежит", c.get(f"/base/{kusok}", auth=ADMIN).text == fajl)
    telo = "<!doctype html><html><head><meta charset=utf-8><title>стенд</title></head><body><h1>Список стенда</h1></body></html>"
    for put, api, nazad in (("/boris/docs", "/api/boris-docs", "Мои документы"), ("/admin/docs", "/api/admin-docs", "Списки")):
        rr = c.post(api, auth=VLADELEC, json={"slug": "shapka-stend", "title": "Стенд шапки", "html": telo})
        proverka(f"{api}: документ стенда выложен", rr.status_code == 200, rr.text[:120])
        r = c.get(f"{put}/shapka-stend", auth=VLADELEC, headers=BRAUZER)
        proverka(f"{put}/…: браузеру — строка «← {nazad}» сразу после <body>, полной шапки нет",
                 r.status_code == 200 and f'<body><div id="kidsup-doc-bar"' in r.text and f"← {nazad}" in r.text
                 and f'href="{put}"' in r.text and r.text.startswith("<!doctype") and 'id="kidsup-nav"' not in r.text,
                 r.text[:200])
        r = c.get(f"{put}/shapka-stend", auth=VLADELEC)
        proverka(f"{put}/…: без Accept: text/html тело байт в байт то, что выложено", r.text == telo, r.text[:120])
        c.post(f"{api}/remove", auth=VLADELEC, json={"slug": "shapka-stend"})

    print("Е5. Счётчик бейджа /api/waiting/schet")
    proverka("без входа → 401", c.get("/api/waiting/schet").status_code == 401)
    proverka("педагогу по ключу → 401", c.get(f"/api/waiting/schet?k={kluch}").status_code == 401)
    raschetov = []
    nastoyashchiy = m.api_waiting

    async def schitaem(min_minutes: int = 15):
        raschetov.append(1)
        return await nastoyashchiy(min_minutes)
    m.api_waiting = schitaem
    m._SCHET.update(ts=0.0, dannye=None, zadacha=None)
    try:
        r = c.get("/api/waiting/schet", auth=ADMIN)
        d = r.json() if r.status_code == 200 else {}
        proverka("под админом → 200, числа переписки и звонков",
                 r.status_code == 200 and all(isinstance(d.get(k), int) for k in ("perepiska", "bez_nomera", "zvonki"))
                 and isinstance(d.get("goryacho"), bool), r.text[:200])
        for _ in range(5):
            c.get("/api/waiting/schet", auth=ADMIN)
        proverka("шесть опросов подряд — один расчёт /api/waiting (остальные из кэша)", len(raschetov) == 1, len(raschetov))
        w = c.get("/api/waiting", auth=ADMIN).json()
        d = c.get("/api/waiting/schet", auth=VLADELEC).json()
        proverka("числа счётчика совпадают с /api/waiting",
                 (d["perepiska"], d["bez_nomera"], d["zvonki"]) == (w["count"], len(w["nophone"]), w["calls_count"]), (d, w["count"]))
    finally:
        m.api_waiting = nastoyashchiy
    t = c.get("/mesta", auth=ADMIN).text
    proverka("страницы base.html больше не опрашивают /api/waiting сами", "fetch('/api/waiting')" not in t)

    print("Е6. nav.wrap, стили и скрипт")
    S = "<i id=sh></i>"
    proverka("wrap: сразу после первого <body…>",
             nav.wrap("<!doctype html><html><head></head><body class='a'><p>x</p></body></html>", S)
             == "<!doctype html><html><head></head><body class='a'>" + S + "<p>x</p></body></html>")
    proverka("wrap: без <body> — после <meta charset…> и заголовочного, не перед <!doctype>",
             nav.wrap("<!doctype html><meta charset=utf-8><title>т</title><style>a{}</style><h1>x</h1>", S)
             == "<!doctype html><meta charset=utf-8><title>т</title><style>a{}</style>" + S + "<h1>x</h1>")
    proverka("wrap: нет ни <body>, ни <meta> — после <!doctype>/<html>",
             nav.wrap("<!doctype html><html><h1>x</h1>", S) == "<!doctype html><html>" + S + "<h1>x</h1>"
             and nav.wrap("<h1>x</h1>", S) == S + "<h1>x</h1>")
    raz = nav.wrap("<body><p>x</p>", {"rol": "admin", "path": "/dolgi"})
    proverka("wrap: шапка уже есть — вторую не ставит", raz.count('id="kidsup-nav"') == 1
             and nav.wrap(raz, {"rol": "owner", "path": "/dolgi"}) == raz)
    proverka("doc_bar: адрес и подпись экранированы",
             "<b>" not in nav.doc_bar('/x"><b>', "<b>Списки") and "window.print()" in nav.doc_bar("/base", "База знаний"))
    for fajl_ in ("nav.css", "nav.js"):
        proverka(f"/static/{fajl_} отдаётся", c.get(f"/static/{fajl_}").status_code == 200)
    css = open(os.path.join(ROOT, "app/static/nav.css"), encoding="utf-8").read()
    proverka("nav.css: печать (шапка, панель и строка документа скрыты) и тёмная тема",
             re.search(r"@media print\s*\{\s*#kidsup-nav, #kidsup-doc-bar[^}]*display: none !important", css) is not None
             and "prefers-color-scheme: dark" in css and "env(safe-area-inset-bottom)" in css)
    bez_komm = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    selektory = [x.strip() for kusok in re.findall(r"(?:^|[{}])\s*([^{}@]+?)\s*\{", bez_komm) for x in kusok.split(",")]
    chuzhie = [x for x in selektory if not x.startswith(("#kidsup-nav", "#kidsup-doc-bar", ".kn-", "body.kn-", "html.kn-"))]
    proverka("nav.css: все правила — внутри шапки или с префиксом kn-, глобальных (a{}, body{}) нет",
             selektory and not chuzhie, chuzhie[:5])
    js = open(os.path.join(ROOT, "app/static/nav.js"), encoding="utf-8").read()
    proverka("nav.js: бейдж опрашивает только счётчик из data-schet, не /api/waiting",
             "/api/waiting" not in re.sub(r"/\*.*?\*/|//[^\n]*", "", js, flags=re.S) and "data-schet" in js)
    proverka("nav.js: «назад» закрывает лист разделов (pushState / popstate)", "pushState" in js and "popstate" in js)
    bl = open(os.path.join(ROOT, "app/templates/blank_grupp.html"), encoding="utf-8").read()
    proverka("/blank-grupp: подсказка .toast на телефоне поднята над панелью",
             ".toast{bottom:calc(64px + env(safe-area-inset-bottom))}" in bl)


# --- Ж. Шаг 2: счётчик открытий и переезды /settings, /metrics (13.11.2026) -------------

def _hity(baza, path=None, rol=None) -> int:
    con = sqlite3.connect(baza)
    try:
        q, arg = "SELECT COALESCE(SUM(n),0) FROM page_hits WHERE 1=1", []
        if path is not None:
            q, arg = q + " AND path=?", arg + [path]
        if rol is not None:
            q, arg = q + " AND rol=?", arg + [rol]
        return con.execute(q, arg).fetchone()[0]
    except sqlite3.OperationalError:
        return -1
    finally:
        con.close()


def _dozhdatsya_fona(sek: float = 20) -> None:
    """Дождаться фонового потока перепроверки заявок («zayavki-audit»): он стартует при открытии
    пульта и пишет в базу стенда."""
    import threading
    for t in threading.enumerate():
        if t.name == "zayavki-audit":
            t.join(sek)


def _otkryt_i_schitat(c, baza, put, rol, schitat=None, **kw):
    """Открыть страницу «из браузера» и вернуть (ответ, прирост счётчика открытий).

    13.11.2026, вечер (проверка «вечера 1»): раздел «Ж1» падал в одном прогоне из трёх (и у
    исполнителя B — в одном из шести). Причина не в счётчике: db.page_hit открывает базу с
    timeout=0 и при занятой базе запись НАРОЧНО пропускает (счётчик — справка, страницу не
    держит), а открытие /pult запускает фоновый поток «zayavki-audit», который пишет в ту же
    базу. На нагруженной машине запись попадала на его транзакцию. Поведение счётчика не
    меняем; в тесте ждём фоновый поток и повторяем открытие до трёх раз, пока прирост нулевой.
    Проверка та же и не слабее: прирост от одного открытия — ровно 1 (от двух было бы 2)."""
    import time
    schitat = schitat or put
    r, prirost = None, 0
    for _ in range(3):
        _dozhdatsya_fona()
        do = _hity(baza, schitat, rol)
        r = c.get(put, **kw)
        prirost = _hity(baza, schitat, rol) - do
        if prirost:
            break
        time.sleep(0.5)
    return r, prirost


def test_schetchik_i_pereezdy(c, m, baza):
    import time
    from starlette.requests import Request
    BR = {"Accept": "text/html,application/xhtml+xml", "Sec-Fetch-Dest": "document"}

    print("Ж1. Счётчик открытий: что пишет")
    r, prirost = _otkryt_i_schitat(c, baza, "/pult", "admin", auth=ADMIN, headers=BR)
    proverka("GET /pult под admin из браузера → 200 и +1 в page_hits с rol=admin",
             r.status_code == 200 and prirost == 1, (r.status_code, prirost, _hity(baza, "/pult", "admin")))
    con = sqlite3.connect(baza)
    den = con.execute("SELECT day FROM page_hits WHERE path='/pult' AND rol='admin'").fetchall()
    kol = [x[1] for x in con.execute("PRAGMA table_info(page_hits)")]
    con.close()
    proverka("строка одна на день (UPSERT), день — сегодняшний по часам сервера, колонки day/path/rol/n",
             den == [(m.autopilot._today().isoformat(),)] and kol == ["day", "path", "rol", "n"], (den, kol))
    _, prirost = _otkryt_i_schitat(c, baza, "/owner", "owner", auth=VLADELEC, headers=BR)
    proverka("GET /owner под владельцем → rol=owner (роль из request.state доходит до слоя)", prirost == 1, prirost)
    kluch = m.db.get_setting("pedagog_key")
    _otkryt_i_schitat(c, baza, f"/english/karta?k={kluch}", "pedagog", schitat="/english/karta", headers=BR)
    proverka("педагог по ключу: rol=pedagog, ключ из адреса в таблицу не попал",
             _hity(baza, "/english/karta", "pedagog") == 1 and _hity(baza, f"/english/karta?k={kluch}") == 0)
    r, _ = _otkryt_i_schitat(c, baza, "/groups", "admin", auth=ADMIN, headers=BR, follow_redirects=False)
    kod, loc = r.status_code, None
    proverka("303 со старого адреса (/groups) тоже считается — это основание адрес удалить",
             kod == 303 and _hity(baza, "/groups", "admin") == 1, (kod, _hity(baza, "/groups")))

    print("Ж1. Счётчик открытий: чего не пишет")
    vsego = _hity(baza)
    c.get("/api/waiting/schet", auth=ADMIN, headers=BR)
    c.get("/api/health", headers=BR)
    c.get("/static/nav.css", headers=BR)
    c.get("/export/leads.csv", auth=ADMIN, headers=BR)
    proverka("/api/*, /static/*, /export/* — не пишет", _hity(baza) == vsego, _hity(baza) - vsego)
    r = c.get("/", headers={**BR, "host": "kidsup.ru"})
    c.get("/english", headers={**BR, "host": "kidsup.ru"})
    proverka("публичный хост kidsup.ru — не пишет", r.status_code == 200 and _hity(baza) == vsego, _hity(baza) - vsego)
    c.get("/pult", auth=ADMIN)
    c.get("/pult", auth=ADMIN, headers={"Accept": "*/*"})
    proverka("рутина (без Accept: text/html) — не открытие", _hity(baza) == vsego, _hity(baza) - vsego)
    c.get("/pult", auth=ADMIN, headers={**BR, "Sec-Fetch-Dest": "empty"})
    proverka("фоновый запрос страницы (Sec-Fetch-Dest: empty) — не открытие", _hity(baza) == vsego)
    kody = (c.get("/pult", headers=BR, follow_redirects=False).status_code,
            c.get("/owner", auth=ADMIN, headers=BR, follow_redirects=False).status_code,
            c.get("/net-takoy-stranicy", auth=ADMIN, headers=BR).status_code)
    proverka("без входа, отказ админу на странице владельца, 404 — не пишет",
             _hity(baza) == vsego and all(k >= 300 for k in kody), (kody, _hity(baza) - vsego))
    c.post("/api/pult/done", auth=ADMIN, headers=BR, json={})
    proverka("POST — не пишет", _hity(baza) == vsego)

    def zapros(path, rol="owner", kod=200, **zag):
        sc = {"type": "http", "method": "GET", "path": path, "query_string": b"a=1", "scheme": "https",
              "server": ("app.kidsup.ru", 443), "state": {"rol": rol} if rol else {},
              "headers": [(k.lower().encode(), v.encode()) for k, v in {**BR, "host": "app.kidsup.ru", **zag}.items()]}
        return m._schet_chto_pisat(Request(sc), kod)
    proverka("адрес: без параметров и хвостового «/», /boris/docs/<slug> — со slug",
             zapros("/boris/docs/pribyl-oktyabr/")[1:] == ("/boris/docs/pribyl-oktyabr", "owner"),
             zapros("/boris/docs/pribyl-oktyabr/"))
    proverka("длинный адрес обрезан до 120 знаков", len(zapros("/base/" + "x" * 500)[1]) == 120)
    proverka("личные ссылки семей (/lk/…, /v/…) и вебхуки — мимо: в адресе ключ",
             zapros("/lk/abcdef") is None and zapros("/v/abc") is None and zapros("/mango/events") is None
             and zapros("/go/wa") is None and zapros("/favicon.ico") is None)
    proverka("роли нет или она незнакомая → «?»; 303 без роли (на страницу входа) — мимо; 401/404/307 — мимо",
             zapros("/vhod", rol="")[2] == "?" and zapros("/vhod", rol="neizvestno")[2] == "?"
             and zapros("/pult", rol="", kod=303) is None and zapros("/pult", kod=401) is None
             and zapros("/pult", kod=404) is None and zapros("/pult.", kod=307) is None)
    proverka("слой счётчика не читает тело ответа и стоит внутри сжатия (GZip — снаружи)",
             "body" not in inspect.getsource(m._schetchik_otkrytiy)
             and r.headers.get("content-encoding") in ("gzip", None))

    print("Ж1. Счётчик открытий: сбой не ломает страницу")
    con = sqlite3.connect(baza)
    con.execute("ALTER TABLE page_hits RENAME TO page_hits_zapas")
    con.execute("CREATE TABLE page_hits (chto_to TEXT)")
    con.commit()
    r = c.get("/pult", auth=ADMIN, headers=BR)
    proverka("таблица сломана (не те колонки) → страница всё равно 200", r.status_code == 200, r.status_code)
    r = c.get("/journal", auth=VLADELEC, headers=BR)
    proverka("…и «Журнал» у владельца 200 — без блока открытий, с остальным",
             r.status_code == 200 and 'id="otkrytiya"' not in r.text and "Подробно" in r.text, r.status_code)
    con.execute("DROP TABLE page_hits")
    con.commit()
    _dozhdatsya_fona()
    r = c.get("/pult", auth=ADMIN, headers=BR)
    if r.status_code == 200 and _hity(baza, "/pult", "admin") <= 0:      # база была занята фоном — открываем ещё раз
        _dozhdatsya_fona()
        r = c.get("/pult", auth=ADMIN, headers=BR)
    proverka("таблицы нет вовсе → 200, таблица создалась заново и запись легла",
             r.status_code == 200 and _hity(baza, "/pult", "admin") == 1, (r.status_code, _hity(baza, "/pult", "admin")))
    con.execute("DROP TABLE page_hits")
    con.execute("ALTER TABLE page_hits_zapas RENAME TO page_hits")
    con.commit()
    con.close()
    m.db._page_hits_est = False
    zamok = sqlite3.connect(baza, timeout=30)      # сам замок ждёт: фоновые потоки стенда тоже пишут
    zamok.execute("BEGIN IMMEDIATE")
    t0 = time.time()
    try:
        itog = m.db.page_hit("2026-01-01", "/zanyato", "admin")
    finally:
        zamok.rollback()
        zamok.close()
    proverka("база занята (database is locked) → запись пропущена сразу, без ожидания",
             itog is False and time.time() - t0 < 1 and _hity(baza, "/zanyato") == 0, (itog, round(time.time() - t0, 2)))

    print("Ж2. /settings → /owner#mk")
    kod, loc, cel = perehod_i_cel(c, "/settings", VLADELEC)
    proverka("владелец: GET /settings → 303 /owner#mk → 200, без цепочки",
             (kod, loc, cel) == (303, "/owner#mk", 200), (kod, loc, cel))
    proverka("админ: /settings → отказ, как и был (не переход)", lokaciya(c, "/settings")[0] in (401, 403)
             and lokaciya(c, "/settings")[1] is None, lokaciya(c, "/settings"))
    proverka("шаблоны settings.html и metrics.html удалены, обработчики на них не ссылаются",
             not os.path.exists(os.path.join(ROOT, "app/templates/settings.html"))
             and not os.path.exists(os.path.join(ROOT, "app/templates/metrics.html"))
             and "settings.html" not in inspect.getsource(m) and "metrics.html" not in inspect.getsource(m))
    kluch_mk = "stend-mk-" + "0" * 20 + "WXYZ"                 # выдуманный ключ — только на стенде
    m.db.set_setting("moyklass_api_key", kluch_mk)
    h = c.get("/owner", auth=VLADELEC).text
    con = sqlite3.connect(baza)
    con.execute("DELETE FROM settings WHERE key='moyklass_api_key'")   # дальше стенд снова без ключа
    con.commit()
    con.close()
    proverka("раздел id='mk': поле нового ключа, глубина, «Сохранить», «Проверить подключение»",
             "id='mk'" in h and "name='api_key'" in h and "name='history_months'" in h
             and "action='/settings'" in h and "action='/settings/test'" in h and "Проверить подключение" in h)
    proverka("ключ целиком на страницу не попадает — только последние 4 знака",
             kluch_mk not in h and kluch_mk[:12] not in h and "…WXYZ" in h)
    proverka("…и счётчики локальной базы с сырыми выгрузками — в свёрнутом блоке",
             "<details" in h and "/export/raw/users.json" in h)
    r = c.post("/settings", auth=VLADELEC, data={"history_months": "36"}, follow_redirects=False)
    h = c.get("/owner?mk=1", auth=VLADELEC).text
    proverka("POST /settings (глубина 36) → 303 /owner?mk=1#mk; на странице итог и новое значение",
             (r.status_code, r.headers.get("location")) == (303, "/owner?mk=1#mk")
             and "Сохранено: глубина выгрузки" in h and "value='36'" in h and m.db.get_setting("history_months") == "36",
             (r.status_code, r.headers.get("location")))
    proverka("без ?mk=1 итог не показывается (обычное открытие страницы)",
             "id='mk-itog'" not in c.get("/owner", auth=VLADELEC).text)
    r = c.post("/settings/test", auth=VLADELEC, follow_redirects=False)
    h = c.get("/owner?mk=1", auth=VLADELEC).text
    proverka("POST /settings/test без ключа → 303 на /owner?mk=1#mk, «API-ключ не задан»",
             (r.status_code, r.headers.get("location")) == (303, "/owner?mk=1#mk") and "API-ключ не задан" in h)
    proverka("текст из адреса на страницу не попадает (итог хранится на сервере)",
             "PODSTAVA" not in c.get("/owner?mk=PODSTAVA&msg=PODSTAVA", auth=VLADELEC).text)
    proverka("POST /settings под админом → отказ",
             c.post("/settings", auth=ADMIN, data={"history_months": "1"}, follow_redirects=False).status_code in (401, 403)
             and m.db.get_setting("history_months") == "36")
    h = c.get("/liniya", auth=VLADELEC).text
    proverka("плашка «укажите API-ключ» у владельца ведёт на /owner#mk",
             'href="/owner#mk"' in h and 'href="/settings"' not in h)

    print("Ж3. /metrics → /journal#schetchiki")
    kod, loc, cel = perehod_i_cel(c, "/metrics", VLADELEC)
    proverka("владелец: GET /metrics → 303 /journal#schetchiki → 200, без цепочки",
             (kod, loc, cel) == (303, "/journal#schetchiki", 200), (kod, loc, cel))
    kod, loc, cel = perehod_i_cel(c, "/metrics", ADMIN)
    # 13.11.2026, проверка шага 2: было «→ /pult» молча; до шага 2 «Дисциплина» открывалась
    # админам, поэтому теперь переход на пульт с пояснением (?bylo=/metrics)
    proverka("админ: /metrics → 303 сразу на пульт с пояснением → 200 («Журнал» — страница владельца, цепочки нет)",
             (kod, loc, cel) == (303, "/pult?bylo=%2Fmetrics", 200), (kod, loc, cel))
    proverka("/metrics без входа → 401", lokaciya(c, "/metrics", kto=None) == (401, None))
    den = m.autopilot._today().isoformat()
    con = sqlite3.connect(baza)
    con.execute("INSERT OR IGNORE INTO mango_calls (ts, phone, direction, state) VALUES (?,?,?,?)",
                (den + "T09:00:00", "9990001090", "in", "talked"))
    con.execute("INSERT OR IGNORE INTO mango_calls (ts, phone, direction, state) VALUES (?,?,?,?)",
                (den + "T09:05:00", "9990001091", "out", "missed"))
    con.commit()
    con.close()
    s = m._schetchiki(den)
    con = sqlite3.connect(baza)
    con.execute("DELETE FROM mango_calls WHERE phone IN ('9990001090','9990001091')")
    con.commit()
    con.close()
    proverka("счётчики дня считаются по локальной mango_calls: входящий с разговором и исходящий без",
             s["calls"]["in"] >= 1 and s["calls"]["out"] >= 1 and s["calls"]["talked"] >= 1
             and s["calls"]["talked"] <= s["calls"]["in"] + s["calls"]["out"], s["calls"])
    r = c.get("/journal", auth=VLADELEC)
    proverka("«Журнал»: раздел id=\"schetchiki\" — звонки, сообщения, записи по админам за 7 дней",
             r.status_code == 200 and 'id="schetchiki"' in r.text and "входящих звонков" in r.text
             and "отправлено сообщений" in r.text and "Записи в группы по админам" in r.text)
    proverka("SLA и «нагрузка» не перенесены", "SLA" not in r.text and "Нагрузка сегодня" not in r.text
             and "потолк" not in r.text.lower())

    print("Ж4. Блок «Какие страницы открывают» — только владельцу")
    _otkryt_i_schitat(c, baza, "/students", "owner", auth=VLADELEC, headers=BR)
    r = c.get("/journal", auth=VLADELEC)
    blok = r.text.split('id="otkrytiya"', 1)[-1].split("<h2>Подробно</h2>")[0]
    proverka("у владельца блок есть: адрес, всего, по ролям",
             'id="otkrytiya"' in r.text and "Какие страницы открывают — 30 дней" in r.text
             and "Админы" in blok and "Владелец" in blok and "Педагоги" in blok and ">/pult<" in blok
             and ">/english/karta<" in blok)
    ne = blok.split('id="ne-otkryvali"', 1)[-1]
    proverka("отдельной строкой — что из меню и архива не открывали: /payments там есть, открытых /students и /pult нет",
             "Не открывали ни разу" in ne and "/payments<" in ne and "/students<" not in ne and "/pult<" not in ne,
             ne[:300])
    proverka("…и честная оговорка, с какого дня счётчик работает", "Счётчик работает с" in blok)
    kod, loc = lokaciya(c, "/journal")
    proverka("админ: /journal → 303 /pult — блока (и самой страницы) у админа нет", (kod, loc) == (303, "/pult"), (kod, loc))
    shab = open(os.path.join(ROOT, "app/templates/journal.html"), encoding="utf-8").read()
    proverka("в шаблоне блок закрыт условием rol == 'owner', в обработчике данные считаются только владельцу",
             "{% if rol == 'owner' and otkr %}" in shab
             and '_otkrytiya() if _rol(request) == "owner" else None' in inspect.getsource(m.journal_page))
    for adres in ("/pult", "/waiting", "/liniya"):
        proverka(f"админ: на {adres} блока открытий нет",
                 "Какие страницы открывают" not in c.get(adres, auth=ADMIN).text)


# --- З. Шаг 2: переезды /segodnya, /spiski, /zayavki, /leads, /clicks (13.11.2026) -------

def _vhod_marshruta(m, metod: str, put: str) -> set:
    for r in m.app.routes:
        if getattr(r, "path", "") == put and metod in getattr(r, "methods", ()):
            return {getattr(d.call, "__name__", "") for d in getattr(getattr(r, "dependant", None), "dependencies", [])}
    return {"нет маршрута"}


def test_pereezdy_pyati(c, m, baza):
    import datetime as dt
    from app import pult, nav
    den = dt.date.today()
    print("З1. Старые адреса: 303 сразу на страницу, которая сама отвечает 200")
    zhdem = {  # адрес: (владельцу, админу)
        "/segodnya": ("/pult#pervye", "/pult#pervye"),
        # 13.11.2026, проверка шага 2: без параметров /spiski показывал всю неделю — day=vse
        "/spiski": ("/blank-grupp?vid=den&day=vse", "/blank-grupp?vid=den&day=vse"),
        "/zayavki": ("/blank-grupp#budushchie", "/blank-grupp#budushchie"),
        # «Откуда заявки» — страница владельца: админа с /leads ведём на пульт сразу, без цепочки
        # 13.11.2026, проверка шага 2: не молча, а с пояснением на пульте (?bylo=/leads)
        "/leads": ("/istochniki#leads", "/pult?bylo=%2Fleads"),
    }
    for adres, (vl, ad) in zhdem.items():
        for kto, rol, kuda in ((VLADELEC, "владелец", vl), (ADMIN, "админ", ad)):
            kod, loc, cel = perehod_i_cel(c, adres, kto)
            proverka(f"{rol}: {adres} → 303 {kuda} → 200, без цепочки", (kod, loc, cel) == (303, kuda, 200), (kod, loc, cel))
        proverka(f"{adres} без входа → 401", lokaciya(c, adres, kto=None) == (401, None))
    kod, loc, cel = perehod_i_cel(c, "/clicks", VLADELEC)
    proverka("владелец: /clicks → 303 /istochniki#clicks → 200, без цепочки",
             (kod, loc, cel) == (303, "/istochniki#clicks", 200), (kod, loc, cel))
    # общий admin на переходный срок проходит вход владельца на /clicks (vhod.PEREHOD_ADMIN, пока ключ
    # рутин ни разу не сработал), после — 401. В обоих случаях до кликов он не доходит и цепочки нет.
    kod, loc, cel = perehod_i_cel(c, "/clicks", ADMIN)
    proverka("админ: /clicks → сразу на /pult (переходный срок) или 401 — не цепочка через /istochniki",
             (kod, loc, cel) == (303, "/pult", 200) or kod == 401, (kod, loc, cel))
    proverka("личный админ (X-Kak: admin): /clicks закрыт — 403, без перехода",
             c.get("/clicks", auth=VLADELEC, headers={"X-Kak": "admin"}, follow_redirects=False).status_code == 403)
    proverka("/clicks без входа → 401", lokaciya(c, "/clicks", kto=None) == (401, None))
    proverka("вход у старых адресов прежний: /clicks — владельца, остальные четыре — общий",
             _vhod_marshruta(m, "GET", "/clicks") == {"check_owner"}
             and all(_vhod_marshruta(m, "GET", a) == {"check_auth"} for a in zhdem))
    kod, loc = lokaciya(c, "/spiski?day=" + quote("ср"))
    proverka("/spiski?day=ср → день переносится", (kod, loc) == (303, "/blank-grupp?vid=den&day=" + quote("ср")), (kod, loc))
    kod, loc = lokaciya(c, "/spiski?subject=" + quote("Шахматы"))
    proverka("/spiski?subject=… (без дня — была вся неделя) → day=vse и предмет",
             (kod, loc) == (303, "/blank-grupp?vid=den&day=vse&subject=" + quote("Шахматы")), (kod, loc))
    proverka("/arhiv и плашка честные: «переезжают» пуст, все пять — в «уже переехали»",
             nav.PEREEZD == [] and {"/segodnya", "/spiski", "/zayavki", "/leads", "/clicks"}
             <= {a for a, *_ in nav.PEREEHALI})
    for imya in ("segodnya.html", "leads.html", "clicks.html"):
        proverka(f"шаблон {imya} удалён", not os.path.exists(os.path.join(ROOT, "app/templates", imya)))

    print("З2. /pult: «Первые занятия» и «Занятия без отметок явки»")
    con = sqlite3.connect(baza)
    zavtra, vchera = (den + dt.timedelta(days=1)).isoformat(), (den - dt.timedelta(days=1)).isoformat()
    con.executemany("INSERT OR REPLACE INTO lessons (id, date, begin_time, class_id) VALUES (?,?,?,?)",
                    [(5901, zavtra, "17:00", 105), (5902, vchera, "17:00", 101)])
    # 9001 (Тестова Ева, 79990001001) идёт завтра в первый раз; 9003 был вчера — отметки явки нет
    con.executemany("INSERT OR REPLACE INTO lesson_records (id, lesson_id, user_id, visit, raw) VALUES (?,?,?,?,?)",
                    [(59011, 5901, 9001, 0, '{"test": true}'), (59021, 5902, 9003, 0, "{}")])
    con.commit()
    con.close()
    pult._SEGODNYA_KESH.clear()
    r = c.get("/pult", auth=ADMIN)
    h = r.text
    proverka("оба блока есть: id=\"pervye\" и id=\"bez-yavki\"", "id='pervye'" in h and "id='bez-yavki'" in h)
    proverka("оба — свёрнутые <details>, стоят над блоком «Разделы»",
             "<details class='card small' id='pervye'>" in h and "<details class='card small' id='bez-yavki'>" in h
             and 0 < h.find("id='pervye'") < h.find("id='bez-yavki'") < h.find("id='razdely'"))
    pv = h.split("id='pervye'", 1)[-1].split("</details>")[0]
    proverka("«Первые занятия»: число в заголовке, ребёнок, телефон с +7, отметка касания",
             "Первые занятия: сегодня и два дня вперёд — 1 ребёнок" in pv and "Тестова Ева" in pv
             and "tel:+" + SEMYA in pv and "не касались" in pv and "не прозвонено: 1" in pv, pv[:400])
    by = h.split("id='bez-yavki'", 1)[-1].split("</details>")[0]
    proverka("«Без явки»: число в заголовке, занятие вчера, ссылка на отметку явки",
             "Занятия без отметок явки за 6 дней — 1 занятие, 1 ребёнок" in by and vchera[8:] + "." + vchera[5:7] in by
             and "href='/yavka'" in by, by[:400])
    api = c.get("/api/segodnya", auth=ADMIN).json()
    proverka("числа те же, что отдаёт /api/segodnya (считают одни функции)",
             api["итоги"]["первые_занятия"] == 1 and api["итоги"]["занятий_без_явки"] == 1, api["итоги"])
    con = sqlite3.connect(baza)
    con.execute("DELETE FROM lesson_records WHERE id IN (59011, 59021)")
    con.execute("DELETE FROM lessons WHERE id IN (5901, 5902)")
    con.commit()
    con.close()
    proverka("расчёт минуту держится в памяти: второй показ пульта базу не читает",
             "Тестова Ева" in c.get("/pult", auth=ADMIN).text.split("id='pervye'", 1)[-1].split("</details>")[0])
    pult._SEGODNYA_KESH.clear()
    h = c.get("/pult", auth=ADMIN).text
    proverka("пусто — блоки на месте, в заголовке ноль",
             "вперёд — 0 детей" in h and "за 6 дней — 0 — явка проставлена везде" in h)
    proverka("блок раскрывается по якорю (#pervye) — скрипт на странице", "ids=['pervye','bez-yavki']" in h)

    print("З3. /blank-grupp: «Все группы дня» и «Будущие направления»")
    h = c.get("/blank-grupp", auth=ADMIN).text
    proverka("на бланках — переключатель «Все группы дня» и свёрнутый блок id=\"budushchie\"",
             'href="/blank-grupp?vid=den"' in h and '<details id="budushchie"' in h and "Будущие направления" in h)
    proverka("бланки остались бланками: вкладки, Excel, печать",
             'id="tabs"' in h and "/api/blank-grupp/xlsx" in h and "window.print()" in h)
    r = c.get("/blank-grupp?vid=den&day=" + quote("пн"), auth=ADMIN)
    h = r.text
    proverka("?vid=den&day=пн: группы понедельника по времени, с телефонами, шапка одна",
             r.status_code == 200 and "Все группы дня" in h and "ПШ2 читающие (Гр7)" in h and "ПШ1 нечитающие (Гр9)" not in h
             and "class='ph'" in h and h.count('id="kidsup-nav"') == 1, r.status_code)
    proverka("печать как у /spiski: кнопка «Печать» и правила печати на странице",
             "javascript:print()" in h and "@media print" in h)
    proverka("ссылки дней ведут на новое место, обратно — на бланки; /spiski в ссылках нет",
             "href='/blank-grupp?vid=den&day=" + quote("вт") + "'" in h and "href='/blank-grupp'" in h
             and "/spiski" not in h)
    segodnya = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][m.autopilot._today().weekday()]
    h = c.get("/blank-grupp?vid=den", auth=ADMIN).text
    proverka("без дня — сегодняшний день недели", f"class='on' href='/blank-grupp?vid=den&day={quote(segodnya)}'" in h)
    h = c.get("/blank-grupp?vid=den&day=vse", auth=ADMIN).text
    proverka("day=vse — вся неделя по дням (как раньше /spiski без параметров)", "Понедельник" in h and "Пятница" in h)
    proverka("?vid=den без входа → 401", lokaciya(c, "/blank-grupp?vid=den", kto=None)[0] == 401)
    con = sqlite3.connect(baza)
    con.executemany("INSERT OR REPLACE INTO classes (id, name, course_id, status, max_students, raw) VALUES (?,?,?,'opened',0,'{}')",
                    [(191, "2627_ПШ_Заявки", 1), (192, "2627_Заявки_Скорочтение", 9)])
    con.executemany("INSERT OR REPLACE INTO joins (id, user_id, class_id, status_id, created_at, raw) VALUES (?,?,?,?,?,'{}')",
                    [(88001, 9001, 192, 50509, "2026-11-01T10:00:00"), (88002, 9003, 192, 4, "2026-11-02T10:00:00"),
                     (88003, 9003, 191, 50509, "2026-11-03T10:00:00"), (88004, 9001, 192, 50509, "2026-11-04T10:00:00")])
    con.commit()
    con.close()
    r = c.get("/api/blank-grupp/budushchie", auth=ADMIN)
    d = r.json() if r.status_code == 200 else {}
    kursy = {n["kurs"]: n["deti"] for n in d.get("napravleniya", [])}
    proverka("лист будущих направлений: скорочтение есть, ПШ (действующий курс) — нет",
             "Скорочтение" in kursy and not any("ПШ" in k for k in kursy), list(kursy))
    sk = kursy.get("Скорочтение", [])
    proverka("поимённо, с телефоном и датой заявки; отказавшийся не показан, двойная заявка — одной строкой",
             [x["imya"] for x in sk] == ["Тестова Ева"] and sk[0]["tel"] == SEMYA and sk[0]["kogda"] == "2026-11-01", sk)
    proverka("число в заголовке блока — из тех же данных", d.get("vsego") == sum(len(v) for v in kursy.values()), d.get("vsego"))
    proverka("/api/blank-grupp/budushchie без входа → 401",
             c.get("/api/blank-grupp/budushchie").status_code == 401)
    con = sqlite3.connect(baza)
    con.execute("DELETE FROM joins WHERE id BETWEEN 88001 AND 88004")
    con.execute("DELETE FROM classes WHERE id IN (191, 192)")
    con.commit()
    con.close()
    proverka("устаревшие скрипты разговоров на бланки не перенесены",
             "цены прошлого года" not in c.get("/blank-grupp", auth=ADMIN).text)

    print("З4. /istochniki: «Заявки Марквиза» и «Клики по ссылкам»")
    lid = m.leads.save_lead({"source": "marquiz", "parent_name": "Тестова Ольга", "phone": "79990001077",
                             "comment": "Марквиз «стенд»", "consent_pd": "on"})
    con = sqlite3.connect(baza)
    m._clicks_init(con)
    con.execute("INSERT INTO messenger_clicks (ts, channel, roistat_visit, utm, matched_phone, matched_ts) "
                "VALUES (?,?,?,?,?,?)", (den.isoformat() + "T10:00:00", "stend-kanal", "990077", "{}", "79990001078",
                                         den.isoformat() + "T10:05:00"))
    con.commit()
    con.close()
    r = c.get("/istochniki", auth=VLADELEC)
    h = r.text
    proverka("владелец: разделы id=\"leads\" и id=\"clicks\" на месте, отчёт по источникам — тоже",
             r.status_code == 200 and '<section id="leads"' in h and '<section id="clicks"' in h
             and "Откуда заявки и что из них вышло" in h)
    ld = h.split('<section id="leads"', 1)[-1].split("</section>")[0]
    proverka("заявки: заголовок, заявка поимённо, «не дошли до CRM», ссылка на выгрузку CSV",
             "Заявки Марквиза, не дошедшие до CRM" in ld and "Тестова Ольга" in ld and "79990001077" in ld
             and 'href="/export/leads.csv"' in ld and "не дошли до CRM" in ld, ld[:300])
    proverka("кнопки отправки в CRM на странице нет — как не было и на /leads (открытие и клик ничего не шлют)",
             "/leads/push" not in h and "<form" not in ld)
    kl = h.split('<section id="clicks"', 1)[-1].split("</section>")[0]
    proverka("клики: построчно, со склейкой с обращением",
             "Клики по ссылкам" in kl and "stend-kanal" in kl and "990077" in kl and "79990001078" in kl, kl[:300])
    proverka("битые QR-ссылки /q/… не перенесены", "/q/" not in h)
    kod, loc = lokaciya(c, "/istochniki")
    proverka("админ: /istochniki → 303 /pult — раздела кликов (и всей страницы владельца) у админа нет",
             (kod, loc) == (303, "/pult") and "stend-kanal" not in c.get("/istochniki", auth=ADMIN).text, (kod, loc))
    shab = open(os.path.join(ROOT, "app/templates/istochniki.html"), encoding="utf-8").read()
    proverka("раздел кликов закрыт ролью дважды: данные считаются только владельцу, шаблон без них раздел не рисует",
             'kliki = _kliki_stroki() if _rol(request) == "owner" else None' in inspect.getsource(m.istochniki_page)
             and "{% if kliki is not none %}\n<section id=\"clicks\"" in shab)
    # если страницу когда-нибудь откроют админам: снимаем увод на пульт и смотрим, что админ увидит
    bylo = m._vladelcu
    m._vladelcu = lambda request: None
    try:
        ha = c.get("/istochniki", auth=ADMIN).text
    finally:
        m._vladelcu = bylo
    proverka("если страницу открыть админу: заявки есть, раздела id=\"clicks\", ссылки на него и самих кликов нет",
             '<section id="leads"' in ha and 'id="clicks"' not in ha and 'href="#clicks"' not in ha
             and "stend-kanal" not in ha and "Тестова Ольга" in ha)
    con = sqlite3.connect(baza)
    con.execute("DELETE FROM leads WHERE id=?", (lid,))
    con.execute("DELETE FROM messenger_clicks WHERE channel='stend-kanal'")
    con.commit()
    con.close()

    print("З5. API старых страниц — на месте и под прежним входом")
    for metod, put, vhod in (("GET", "/api/segodnya", "check_auth"), ("GET", "/api/spiski", "check_auth"),
                             ("GET", "/api/zayavki", "check_auth"), ("GET", "/export/leads.csv", "check_owner"),
                             ("POST", "/leads/push/{lead_id}", "check_auth"),
                             ("GET", "/api/clicks/summary", "check_owner"), ("GET", "/go/{channel}", "")):
        proverka(f"{metod} {put} — вход «{vhod or 'без входа'}»",
                 _vhod_marshruta(m, metod, put) == ({vhod} if vhod else set()), _vhod_marshruta(m, metod, put))
    proverka("/api/segodnya отдаёт все разделы, как раньше",
             {"первые", "дела", "без_галочки", "ждут", "чаты", "новые_лиды", "явка", "итоги"}
             <= set(c.get("/api/segodnya", auth=ADMIN).json()))
    proverka("/api/spiski отвечает списком групп", "groups" in c.get("/api/spiski", auth=ADMIN).json())
    proverka("/api/zayavki отвечает (вечерний контроль 21:00)", c.get("/api/zayavki", auth=ADMIN).status_code == 200)
    # вход владельца у этих двух — прежний (проверено выше по зависимостям маршрута); общий admin на
    # переходный срок проходит по списку vhod.PEREHOD_ADMIN — это правило личных входов, здесь не менялось
    for put in ("/export/leads.csv", "/api/clicks/summary"):
        proverka(f"{put}: владельцу — 200, без входа — 401, личному админу (X-Kak) — 403",
                 c.get(put, auth=VLADELEC).status_code == 200 and c.get(put).status_code == 401
                 and c.get(put, auth=VLADELEC, headers={"X-Kak": "admin"}).status_code == 403,
                 (c.get(put, auth=VLADELEC).status_code, c.get(put).status_code,
                  c.get(put, auth=VLADELEC, headers={"X-Kak": "admin"}).status_code))


def _lovushki_na_vse():
    """Страницы меню подгружают модули при первом открытии — ловушки отправки ставим и на них,
    до первого запроса: сеть на стенде закрыта, но и попытки отправить быть не должно."""
    import importlib
    import pkgutil
    import app
    for mod in pkgutil.iter_modules(app.__path__):
        importlib.import_module(f"app.{mod.name}")
    lt._postavit_lovushki()


# --- И. 13.11.2026: четыре правки безопасности и надёжности (сверка 13.11) -----------------

class _BezPerezapuska:
    """Выкладка на стенде: служба не перезапускается, а попытки считаются."""

    def __enter__(self):
        import subprocess
        import threading
        self.bylo = (threading.Timer, subprocess.Popen)
        self.zvali = []

        class _Timer:
            def __init__(s_, *a, **k):
                pass

            def start(s_):
                self.zvali.append("timer")
        threading.Timer = _Timer
        nastoyashchij = subprocess.Popen

        def _popen(args, *a, **k):
            # проверка импорта идёт настоящим отдельным процессом (subprocess.run внутри зовёт
            # Popen) — её пропускаем; systemctl на стенде не запускаем никогда
            if "systemctl" in str(args):
                self.zvali.append("systemctl")
                raise RuntimeError("стенд: systemctl не запускаем")
            return nastoyashchij(args, *a, **k)
        subprocess.Popen = _popen
        return self

    def __exit__(self, *a):
        import subprocess
        import threading
        threading.Timer, subprocess.Popen = self.bylo


def _arhiv_koda(fajly: dict) -> bytes:
    """tar.gz для /api/deploy: {путь: содержимое}; добивка случайным файлом до 1000+ байт."""
    import io
    import secrets
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for imya, data in {**fajly, "docs/_stend_dobivka.bin": secrets.token_bytes(1500)}.items():
            data = data.encode("utf-8") if isinstance(data, str) else data
            ti = tarfile.TarInfo(imya)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def test_pravki_1311(c, m, tree, baza):
    import datetime as dt
    import shutil
    from pathlib import Path

    print("И1. /api/net (правила файрвола, домены Caddy) — только владельцу")
    proverka("/api/net без входа → 401", c.get("/api/net").status_code == 401)
    proverka("/api/net: админ → 401/403", c.get("/api/net", auth=ADMIN).status_code in (401, 403))
    r = c.get("/api/net", auth=VLADELEC)
    proverka("/api/net: владелец → 200, ответ прежний", r.status_code == 200 and r.json().get("ok") is True
             and "firewall" in r.json(), r.status_code)
    proverka("/api/health без входа по-прежнему 200 (проверка «сервер жив» не закрыта)",
             c.get("/api/health").status_code == 200)

    print("И2. Неопределённые имена и суточный кэш контактов Wazzup")
    imena = [f"{r_.path}:{r_.endpoint.__name__}" for r_ in m.app.routes
             if getattr(r_, "path", "").startswith("/api/broadcast/requeue")]
    proverka("два адреса requeue — два разных имени функций, адреса прежние",
             sorted(imena) == ["/api/broadcast/requeue-undelivered:api_broadcast_requeue_undelivered",
                               "/api/broadcast/requeue:api_broadcast_requeue"], imena)
    try:
        from pyflakes import api as _pf, reporter as _rep
        import io
        vyvod = io.StringIO()
        for f in ("app/main.py", "app/wazzup.py"):
            _pf.checkPath(os.path.join(tree, f), _rep.Reporter(vyvod, vyvod))
        plohie = [x for x in vyvod.getvalue().splitlines() if "undefined name" in x]
        proverka("pyflakes: в app/main.py и app/wazzup.py нет «undefined name»", not plohie, plohie[:5])
    except ImportError:
        print("  --   pyflakes не установлен — проверка имён пропущена")
    from app import wazzup
    st = (wazzup._CONTACTS_CACHE, wazzup.httpx.get, wazzup._headers)
    hodili = []

    class _Otvet:
        status_code = 200

        def __init__(self, data):
            self._d = data

        def json(self):
            return {"data": self._d}

    def _get(url, **k):                      # подмена сети: стенд наружу не ходит
        hodili.append((k.get("params") or {}).get("offset"))
        if hodili[-1] == 0 and not pusto[0]:
            return _Otvet([{"id": 501, "name": "Стендова Мама",
                            "contactData": [{"chatId": "9990001095", "chatType": "telegram"}]}])
        return _Otvet([])
    pusto = [False]
    try:
        wazzup._CONTACTS_CACHE = os.path.join(os.path.dirname(baza), "kesh_wz", "wz_contacts.json")
        wazzup.httpx.get, wazzup._headers = _get, (lambda: {})
        pervyj = wazzup.contacts()
        n1 = len(hodili)
        vtoroj = wazzup.contacts()
        proverka("первый вызов ходит в Wazzup и отдаёт карту chatId → имя",
                 n1 == 2 and pervyj.get("9990001095", {}).get("name") == "Стендова Мама", (n1, pervyj))
        proverka("второй вызов в тот же день в сеть не ходит и отдаёт то же", len(hodili) == n1 and vtoroj == pervyj,
                 (len(hodili), vtoroj))
        proverka("кэш лежит файлом (папка создана сама)", os.path.isfile(wazzup._CONTACTS_CACHE))
        wazzup.contacts(refresh=True)
        proverka("refresh=True ходит в сеть заново", len(hodili) == n1 + 2, len(hodili))
        d = json.load(open(wazzup._CONTACTS_CACHE, encoding="utf-8"))
        d["день"] = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        json.dump(d, open(wazzup._CONTACTS_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
        wazzup.contacts()
        proverka("вчерашний кэш не годится — идём в сеть", len(hodili) == n1 + 4, len(hodili))
        pusto[0] = True
        os.remove(wazzup._CONTACTS_CACHE)
        proverka("пустой ответ (сбой, отказ ключу) в кэш не кладётся",
                 wazzup.contacts() == {} and not os.path.exists(wazzup._CONTACTS_CACHE))

        def _padaet(url, **k):
            raise OSError("сети нет")
        wazzup.httpx.get = _padaet
        proverka("сбой сети — пустая карта без падения (раньше NameError: log)", wazzup.contacts() == {})
    finally:
        wazzup._CONTACTS_CACHE, wazzup.httpx.get, wazzup._headers = st

    print("И3. Пульт: колонка «Борис» и его пункты — только владельцу")
    TAJNA = "Контроль стенда: админ не перезвонила Стендовым"
    den = m.pult.today() if hasattr(m, "pult") else __import__("app.pult", fromlist=["x"]).today()
    con = sqlite3.connect(baza)
    con.execute("INSERT INTO plan_inbox (day, ts, who, text, phone, source, done) VALUES (?,?,?,?,?,?,0)",
                (den, den + " 10:00:00", "Борис", TAJNA, "", "контроль"))
    con.commit()
    nomer = con.execute("SELECT id FROM plan_inbox WHERE text=?", (TAJNA,)).fetchone()[0]
    con.close()
    try:
        vl = c.get("/pult", auth=VLADELEC).text
        proverka("владелец: колонка «Борис» и пункт контроля на пульте есть",
                 TAJNA in vl and "who=Борис" in vl and ">Борис" in vl)
        proverka("владелец: /pult?who=Борис — его колонка", TAJNA in c.get("/pult?who=Борис", auth=VLADELEC).text)
        for adres in ("/pult", "/pult?who=Борис", "/pult?who=%D0%91%D0%BE%D1%80%D0%B8%D1%81&day=" + den):
            r = c.get(adres, auth=ADMIN)
            proverka(f"админ {adres}: 200, пункта Бориса в HTML нет, ссылки на его колонку нет",
                     r.status_code == 200 and TAJNA not in r.text and "who=Борис" not in r.text
                     and "class='nm'>Борис" not in r.text, r.status_code)
        r = c.get("/pult?who=Борис", auth=ADMIN)
        proverka("админ ?who=Борис: обычный пульт с колонками смены (Лиза на месте)", "class='nm'>Лиза" in r.text)
    finally:
        con = sqlite3.connect(baza)
        con.execute("DELETE FROM plan_inbox WHERE id=?", (nomer,))
        con.commit()
        con.close()

    print("И4. /api/deploy: код с ошибкой не выкладывается, прежние файлы возвращаются")
    koren = Path(tempfile.mkdtemp(prefix="nav_vykladka_"))
    shutil.copytree(os.path.join(tree, "app"), koren / "app", ignore=shutil.ignore_patterns("__pycache__"))
    glavnyj = koren / "app" / "main.py"
    bylo = glavnyj.read_bytes()
    bylo_nav = (koren / "app" / "nav.py").read_bytes()
    st_koren = m._koren_proekta
    m._koren_proekta = lambda: koren
    try:
        proverka("проверка импорта не стартует потоки и стоит под KIDSUP_PROVERKA_IMPORTA",
                 "threading.Thread.start = lambda self: None" in m._PROVERKA_IMPORTA_KOD
                 and 'environ.get("KIDSUP_PROVERKA_IMPORTA") != "1"' in bylo.decode("utf-8"))
        with _BezPerezapuska() as bp:
            r = c.post("/api/deploy", auth=VLADELEC, content=_arhiv_koda({
                "app/main.py": "def slomano(:\n    pass\n", "app/nav.py": bylo_nav + b"\n# stend\n",
                "app/_stend_novyj.py": "X = 1\n"}))
        proverka("архив с синтаксической ошибкой в app/main.py → 500", r.status_code == 500, r.status_code)
        otvet = r.json() if r.status_code == 500 else {}
        proverka("…в ответе хвост ошибки (SyntaxError), не длиннее 2000 знаков",
                 "SyntaxError" in otvet.get("stderr", "") and len(otvet.get("stderr", "")) <= 2000
                 and otvet.get("restarting") is False, str(otvet)[:300])
        proverka("…файлы прежние: main.py и nav.py как были, нового файла нет",
                 glavnyj.read_bytes() == bylo and (koren / "app" / "nav.py").read_bytes() == bylo_nav
                 and not (koren / "app" / "_stend_novyj.py").exists())
        proverka("…перезапуск не вызван", bp.zvali == [], bp.zvali)
        proverka("…без входа и под админом выкладка закрыта, как раньше",
                 (c.post("/api/deploy", content=b"x" * 2000).status_code,
                  c.post("/api/deploy", content=b"x" * 2000, auth=ADMIN).status_code) == (401, 401))

        # нормальный архив: тот же main.py + строка, которая записывает число потоков проверочного процесса
        metka = (b"\nimport threading as _t, pathlib as _p\n"
                 b"_p.Path(__file__).with_name('_stend_potoki.txt').write_text(str(_t.active_count()))\n")
        with _BezPerezapuska() as bp:
            r = c.post("/api/deploy", auth=VLADELEC, content=_arhiv_koda({
                "app/main.py": bylo + metka, "app/_stend_novyj.py": "X = 1\n"}))
        proverka("нормальный архив → 200, ответ как раньше (ok, files, restarting)",
                 r.status_code == 200 and r.json().get("ok") is True and r.json().get("restarting") is True
                 and r.json().get("files") == 3, (r.status_code, r.text[:300]))
        proverka("…файлы новые, перезапуск назначен один раз",
                 glavnyj.read_bytes() == bylo + metka and (koren / "app" / "_stend_novyj.py").exists()
                 and bp.zvali == ["timer"], bp.zvali)
        potoki = koren / "app" / "_stend_potoki.txt"
        proverka("…проверочный процесс импортировал новый код и не запустил ни одного потока (автопилот не стартовал)",
                 potoki.exists() and potoki.read_text() == "1", potoki.read_text() if potoki.exists() else "нет файла")

        with _BezPerezapuska() as bp:
            r = c.post("/api/deploy", auth=VLADELEC, content=_arhiv_koda({"requirements.txt": "fastapi>=0.110\n"}))
        proverka("архив без app/ — как раньше: 200 и перезапуск, проверка импорта не нужна",
                 r.status_code == 200 and bp.zvali == ["timer"], (r.status_code, bp.zvali))
        with _BezPerezapuska() as bp:
            r = c.post("/api/deploy?bez_proverki=1", auth=VLADELEC,
                       content=_arhiv_koda({"app/_stend_bitoe.py": "def slomano(:\n"}))
        proverka("?bez_proverki=1 — запасной выход: выкладка без проверки, как раньше",
                 r.status_code == 200 and bp.zvali == ["timer"], (r.status_code, bp.zvali))
        proverka("сам репозиторий выкладками стенда не тронут",
                 not os.path.exists(os.path.join(tree, "app", "_stend_novyj.py"))
                 and not os.path.exists(os.path.join(tree, "docs", "_stend_dobivka.bin")))
    finally:
        m._koren_proekta = st_koren
        shutil.rmtree(koren, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=ROOT)
    ap.add_argument("--work", default="")
    ap.add_argument("--do", default=os.path.join(TUT, "nav_marshruty_do.json"))
    a = ap.parse_args()
    work = a.work or tempfile.mkdtemp(prefix="nav_")
    # 12.11.2026: стенд пишет в базу выдуманных клиентов и пароль владельца, опубликованный в этом
    # файле. Ошибочный --work на папку данных сервера заменил бы настоящий пароль и засеял бы
    # боевую базу — поэтому работаем только в пустой папке внутри временной.
    tmp = os.path.realpath(tempfile.gettempdir())
    w = os.path.realpath(work)
    if not w.startswith(tmp + os.sep) or (os.path.isdir(w) and os.listdir(w)):
        sys.exit(f"стенд только во временной папке: --work — пустая папка внутри {tmp}, а не {w}")
    os.makedirs(work, exist_ok=True)
    tree = os.path.abspath(a.tree)
    os.environ["KIDSUP_BEZ_VHODA"] = "1"     # 12.11.2026: режим без входа гаснет, как только задан APP_PASSWORD
    m = lt.zapustit(tree, work)
    baza = os.path.join(work, "kidsup.db")
    lt.zasejat(baza)
    # роли: общий вход админов и отдельный пароль владельца — как на сервере
    m.config.APP_USER, m.config.APP_PASSWORD = ADMIN
    m.db.set_setting("owner_password", VLADELEC[1])
    m.db.set_setting("pedagog_key", "stend-kluch")         # ключ педагога — только на стенде
    _lovushki_na_vse()
    from fastapi.testclient import TestClient
    c = TestClient(m.app)
    test_perehody(c, m)
    test_bezopasnost(c, m)
    test_ssylki(c, m, baza)
    test_marshruty(m, a.do)
    test_uborka(tree)
    test_menu(c, m)
    test_glavnaya(c, m)
    test_arhiv(c, m)
    test_arhiv_dokumentov(c, m, work)
    test_punkty_menu(c, m)
    test_perehody_bez_cepochek(c, m)
    test_arhivnye(c, m)
    test_net_ssylok(c, m, tree)
    test_perepiska_send(c, m)
    test_popravki(c, m)
    test_marshruty_zhestkie(m, a.do)
    test_shapka_shag2(c, m, work)
    test_schetchik_i_pereezdy(c, m, baza)
    test_pereezdy_pyati(c, m, baza)
    test_pravki_1311(c, m, tree, baza)
    test_posle_proverki(c, m, tree, baza)
    test_vecher1(c, m, tree, baza)
    test_uborka_bezopasnost(c, m, tree, baza)
    test_ustarevshee(c, m, tree, baza)
    test_posle_proverki_vechera1(c, m, tree, baza)
    proverka("ни одной отправки", not lt.LOVUSHKI, lt.LOVUSHKI)
    proverka("ни одной попытки выйти в сеть, кроме известных опросов Mango с пульта и «Ответов»",
             all(any(f.startswith(("mango.py", "zayavki.py")) for f in otkuda) for _, otkuda in lt.SET), lt.SET[:5])
    print(f"\nИтого: {lt.proshlo} OK, {lt.oshibok} ошибок · стенд {work}")
    sys.exit(1 if lt.oshibok else 0)



# --- К. Правки после проверки шага 2 (13.11.2026) ---------------------------------------

def test_posle_proverki(c, m, tree, baza):
    import asyncio
    import time
    from app import nav
    BR = {"Accept": "text/html,application/xhtml+xml", "Sec-Fetch-Dest": "document"}

    print("К1. Админ на переехавших к владельцу /metrics и /leads: пояснение на пульте")
    for adres, imya, kuda in (("/metrics", "Дисциплина", "Журнал"), ("/leads", "Лиды", "Откуда заявки")):
        r = c.get(adres, auth=ADMIN)
        proverka(f"админ: {adres} → пульт, на нём строка «Страница «{imya}» переехала в «{kuda}»»",
                 r.status_code == 200 and "id='pereehala'" in r.text
                 and f"Страница «{imya}» переехала в «{kuda}»" in r.text, (r.status_code, str(r.url)))
    t = c.get("/pult", auth=ADMIN).text
    proverka("обычный пульт: пояснения нет", "id='pereehala'" not in t)
    t = c.get("/pult?bylo=" + quote("/metrics"), auth=VLADELEC).text
    proverka("владельцу пояснение не показывается", "id='pereehala'" not in t)
    t = c.get("/pult?bylo=" + quote("<script>alert(1)</script>"), auth=ADMIN).text
    proverka("чужая строка из ?bylo= в страницу не попадает", "id='pereehala'" not in t and "alert(1)" not in t)

    print("К2. Счётчик открытий: увод на пульт, /pedagog, чистка")
    do = _hity(baza)
    kody = [c.get(a, auth=ADMIN, headers=BR, follow_redirects=False).status_code
            for a in ("/journal", "/audit", "/istochniki", "/students", "/metrics", "/leads")]
    proverka("админа увели со страницы владельца на пульт (303) — открытием не считается",
             kody == [303] * 6 and _hity(baza) == do, (kody, _hity(baza) - do))
    do = _hity(baza, "/spiski", "admin")
    c.get("/spiski", auth=ADMIN, headers=BR, follow_redirects=False)
    proverka("переход со старого адреса на новое место (/spiski) считается, как раньше",
             _hity(baza, "/spiski", "admin") == do + 1)
    kluch = m.db.get_setting("pedagog_key")
    do, do_q = _hity(baza, "/pedagog", "pedagog"), _hity(baza, "/pedagog", "?")
    r = c.get(f"/pedagog?k={kluch}", headers=BR)
    proverka("/pedagog с верным ключом пишется с ролью pedagog, а не «?»",
             r.status_code == 200 and _hity(baza, "/pedagog", "pedagog") == do + 1
             and _hity(baza, "/pedagog", "?") == do_q, (do, _hity(baza, "/pedagog", "pedagog")))
    c.get("/pedagog?k=ne-tot-kluch", headers=BR)
    proverka("/pedagog с неверным ключом — роль не pedagog", _hity(baza, "/pedagog", "pedagog") == do + 1)
    den = m.autopilot._today()
    staryy = (den - __import__("datetime").timedelta(days=m.db.PAGE_HITS_HRANIT + 5)).isoformat()
    nedavniy = (den - __import__("datetime").timedelta(days=m.db.PAGE_HITS_HRANIT - 5)).isoformat()
    con = sqlite3.connect(baza)
    con.execute("INSERT INTO page_hits (day, path, rol, n) VALUES (?,?,?,1)", (staryy, "/stend-staryy", "admin"))
    con.execute("INSERT INTO page_hits (day, path, rol, n) VALUES (?,?,?,1)", (nedavniy, "/stend-nedavniy", "admin"))
    con.commit()
    con.close()
    m.db._page_hits_chistka = ""
    m.db.page_hit(den.isoformat(), "/pult", "admin")
    proverka(f"строки старше {m.db.PAGE_HITS_HRANIT} дней стираются при записи (раз в сутки), свежие остаются",
             _hity(baza, "/stend-staryy") == 0 and _hity(baza, "/stend-nedavniy") == 1
             and m.db._page_hits_chistka == den.isoformat())
    con = sqlite3.connect(baza)
    con.execute("DELETE FROM page_hits WHERE path='/stend-nedavniy'")
    con.commit()
    con.close()

    print("К3. Бейдж: после упавшего пересчёта числа не выдаются за свежие")
    zapas = dict(m._SCHET)
    staryy_raschet = m.api_waiting

    async def padaet(*a, **k):
        raise RuntimeError("стенд: расчёт упал")
    try:
        m._SCHET.update({"dannye": {"perepiska": 3, "bez_nomera": 0, "zvonki": 0, "dengi": False,
                                    "goryacho": False, "max_wait": 5},
                         "ts": time.time() - 500, "ts_ok": time.time() - 500, "oshibka": False, "zadacha": None})
        m.api_waiting = padaet
        asyncio.run(m._schet_obnovit())
        j = c.get("/api/waiting/schet", auth=ADMIN).json()
        proverka("кэш есть, пересчёт упал: прежние числа, vozrast — от удачного расчёта, oshibka: true",
                 j.get("perepiska") == 3 and j.get("vozrast", 0) >= 499 and j.get("oshibka") is True, j)
        proverka("упавший расчёт не повторяется на каждый опрос (время попытки обновлено)",
                 time.time() - m._SCHET["ts"] < 50)
        m._schet_zapomnit({"count": 1})
        j = c.get("/api/waiting/schet", auth=ADMIN).json()
        proverka("после удачного расчёта: vozrast около нуля, oshibka нет",
                 j.get("perepiska") == 1 and j.get("vozrast", 99) < 5 and "oshibka" not in j, j)
    finally:
        m.api_waiting = staryy_raschet
        m._SCHET.clear()
        m._SCHET.update(zapas)

    print("К4. Пульт: мусор в ?day=, полоска «кто вошёл» — в шапке")
    r = c.get("/pult?day=" + quote('"><script>x</script>'), auth=ADMIN)
    proverka("/pult?day=<мусор> → 200 с сегодняшним днём (было 500)",
             r.status_code == 200 and "<script>x</script>" not in r.text, r.status_code)
    proverka("/pult?day=2026-13-40 и ?day=20261113 → 200",
             c.get("/pult?day=2026-13-40", auth=ADMIN).status_code == 200
             and c.get("/pult?day=20261113", auth=ADMIN).status_code == 200)
    polosa = "<div class='kto-polosa'>Вы: Стенд</div>"
    h = nav.shapka("admin", "/liniya", kto_html=polosa)
    proverka("«Вы: … · Выйти» — внутри верхней строки шапки (после бейджа), не отдельной строкой",
             h.index('class="kn-verh"') < h.index('id="wait-badge"') < h.index("kto-polosa") < h.index('id="nav-razdely"'))
    zh = "<div class='kto-polosa obshchiy'>Вы под общим входом</div>"
    h = nav.shapka("admin", "/liniya", kto_html=zh)
    proverka("жёлтое предупреждение общего входа — отдельной строкой над верхней",
             h.index("kn-kto-vazhno") < h.index('class="kn-verh"') and h.count("kto-polosa") == 1)
    isx = inspect.getsource(m.pult_page)
    proverka("пульт: полоску рисует общая шапка, своей у пульта нет (polosa=False)",
             "polosa=False" in isx and "kto=False" not in isx.split('"""')[-1])

    print("К5. Шапка: светлая на страницах без тёмной темы, «назад» после листа, узкий телефон")
    t = c.get("/pult", auth=ADMIN).text
    proverka("/pult (своей тёмной темы нет): шапка с классом kn-svetlaya", 'class="kn kn-svetlaya kn-rol-admin"' in t)
    t = c.get("/liniya", auth=ADMIN).text
    proverka("/liniya (base.html, своя тёмная тема): шапка без kn-svetlaya", 'class="kn kn-rol-admin"' in t)
    css = open(os.path.join(tree, "app/static/nav.css"), encoding="utf-8").read()
    js = open(os.path.join(tree, "app/static/nav.js"), encoding="utf-8").read()
    proverka("nav.css: тёмная палитра не применяется к .kn-svetlaya; бейдж на телефоне сжимается с многоточием",
             "#kidsup-nav:not(.kn-svetlaya), #kidsup-doc-bar" in css and "text-overflow: ellipsis" in css.split(".kn-badge:not([hidden])")[1][:200])
    proverka("nav.js: переход из листа заменяет запись листа (location.replace), после перезагрузки запись убирается",
             "location.replace(a.href)" in js and "history.state.knList" in js)
    proverka("версия nav.css/nav.js поднята — браузеры не возьмут старые файлы из кэша",
             nav.VERSIYA != "1" and f"nav.js?v={nav.VERSIYA}" in t)

    print("К6. Методички: viewport браузеру, /promo/grafik без второго логотипа")
    slug = "english_standard"
    fajl = open(os.path.join(tree, "docs", f"{slug}.html"), encoding="utf-8").read()
    r = c.get(f"/base/{slug}", auth=ADMIN, headers=BR)
    proverka(f"/base/{slug} (в файле нет viewport) браузеру: viewport добавлен один раз, <!doctype — первым",
             "viewport" not in fajl[:4000] and r.text.count('name="viewport"') == 1
             and r.text.lstrip()[:9].lower() == "<!doctype"
             and r.text.find('name="viewport"') < r.text.find('id="kidsup-doc-bar"'), r.text[:120])
    r2 = c.get(f"/base/{slug}", auth=ADMIN)
    proverka("…а скрипту (без Accept: text/html) viewport не добавляется", 'name="viewport"' not in r2.text)
    proverka("vstavit_v_nachalo: после <!doctype>/<html>/<head>/<meta>; без них — в начало",
             nav.vstavit_v_nachalo("<!doctype html><html><head><meta charset=utf-8><title>т</title>", "V")
             == "<!doctype html><html><head><meta charset=utf-8>V<title>т</title>"
             and nav.vstavit_v_nachalo("<title>т</title><p>x", "V") == "V<title>т</title><p>x")
    t = c.get("/promo/grafik", auth=ADMIN).text
    proverka("/promo/grafik: логотип один (в общей шапке), ссылка «← Эффективность промоутеров» на месте",
             t.count("logo_color.png") == 0 and t.count("/static/logo.svg") == 1 and 'href="/promo"' in t, t.count("logo"))

# --- Л. Меню вечера 1 (13.11.2026) ---------------------------------------------------------
# Решение владельца 13.11.2026: «М, П, Т — по рекомендациям». Записано здесь отдельно от
# кода, как и решение 12.11 (раздел «Г»): случайная правка nav.ADMIN / nav.OWNER ломает тест.
# Из меню админов уходят «Обзвон», «Шахматка», «Стоп-лист» и строка «Ещё» на пульте; из меню
# владельца — «Аномалии», «Журнал», «Откуда заявки», «Камеры», «Архив». Появляется раздел
# «МойКласс»: «Проверка МойКласса» и «Явка» (переезжает из «Смены»). Все адреса открываются
# как раньше; убранное не теряется — ссылки-заменители и поиск «Раздел» в шапке.

MENU_ADMIN_V1 = [
    ("Смена", [("Пульт", "/pult"), ("Ответы", "/waiting"), ("Линия", "/liniya")]),
    ("Группы", [("Подбор группы", "/enrollment"), ("Места", "/mesta"), ("Бланки групп", "/blank-grupp")]),
    ("МойКласс (Лиза)", [("Проверка МойКласса", "/proverka-mk"), ("Явка", "/yavka")]),   # название — по утверждённому тексту «Вечера 1»
    ("Деньги", [("Без оплаты", "/voronka"), ("Долги", "/dolgi")]),
    ("Справка", [("Списки", "/admin/docs"), ("Описания занятий", "/courses"), ("Промоутеры", "/promo/grafik"),
                 ("База знаний", "/base")]),
]
MENU_OWNER_V1 = [("Мои документы", "/boris/docs"), ("Мои дела", "/pult?who=Борис"), ("Контроль оплат", "/kontrol-oplat"),
                 ("Сверка честности", "/sverka-chestnosti"), ("Итоги промоутеров", "/promo"), ("Настройки", "/owner")]
UBRANO_ADMIN_V1 = {"/obzvon-dnya": "Обзвон", "/grid": "Шахматка", "/otkazy": "Стоп-лист"}
UBRANO_OWNER_V1 = {"/audit": "Аномалии", "/journal": "Журнал", "/istochniki": "Откуда заявки",
                   "/kamery/nastroyka": "Камеры", "/arhiv": "Архив"}
ESHCHE_V1 = ("/obzvon", "/karta", "/constructor", "/base/gruppy_reshenia")      # строка «Ещё», снята с пульта


def _poisk(c, q, kto) -> list:
    return c.get("/api/search", params={"q": q}, auth=kto).json()["items"]


def _razdely_poiska(c, q, kto) -> list:
    return [(i["title"], i["url"]) for i in _poisk(c, q, kto) if i["type"] == "Раздел"]


def test_vecher1(c, m, tree, baza):
    from app import nav
    print("Л1. Состав меню по ролям")
    proverka("меню админа — ровно по решению: 14 пунктов, пять разделов", nav.menu("admin") == MENU_ADMIN_V1,
             nav.menu("admin"))
    proverka("раздел владельца — ровно по решению: 6 пунктов", nav.OWNER == ("Владельцу", MENU_OWNER_V1), nav.OWNER)
    proverka("меню владельца — меню админа и его раздел последним", nav.menu("owner") == MENU_ADMIN_V1 + [nav.OWNER])
    proverka("убранные из меню записаны отдельно (nav.REDKO_…) — ровно те восемь",
             {a: i for i, a in nav.REDKO_ADMIN} == UBRANO_ADMIN_V1 and {a: i for i, a in nav.REDKO_OWNER} == UBRANO_OWNER_V1)
    v_menu = set(_adresa(nav.menu("owner")))
    proverka("ни одна убранная страница не осталась пунктом меню",
             not v_menu & (set(UBRANO_ADMIN_V1) | set(UBRANO_OWNER_V1) | set(ESHCHE_V1)))
    proverka("у каждого пункта меню есть описание для главной (nav.OPIS), у «Проверки МойКласса» тоже",
             not [a for a in v_menu if not nav.OPIS.get(a)] and "МойКласс" in nav.OPIS["/proverka-mk"],
             [a for a in v_menu if not nav.OPIS.get(a)])
    for kto, rol, ubrano in ((ADMIN, "admin", UBRANO_ADMIN_V1), (VLADELEC, "owner", {**UBRANO_ADMIN_V1, **UBRANO_OWNER_V1})):
        h = shapka(c, "/liniya", kto)
        razdely = h.split('id="nav-razdely"', 1)[-1]          # шапка на компьютере и лист «☰ Разделы» — одна разметка
        ostalis = [a for a in list(ubrano) + list(ESHCHE_V1) if f'href="{a}"' in razdely]
        proverka(f"{rol}: в шапке и листе «☰ Разделы» убранных пунктов нет", not ostalis, ostalis)
        # 13.11.2026, вечер: раздел называется «МойКласс (Лиза)» — по утверждённому тексту «Вечера 1»
        mk = razdely.split(">МойКласс (Лиза)<", 1)[-1].split('class="kn-razdel', 1)[0] if ">МойКласс (Лиза)<" in razdely else ""
        proverka(f"{rol}: раздел «МойКласс (Лиза)» — «Проверка МойКласса» и «Явка», больше ничего",
                 re.findall(r'href="([^"]+)"', mk) == ["/proverka-mk", "/yavka"], re.findall(r'href="([^"]+)"', mk))
        smena = razdely.split(">Смена<", 1)[-1].split('class="kn-razdel', 1)[0]
        proverka(f"{rol}: «Явка» из «Смены» ушла", 'href="/yavka"' not in smena and 'href="/pult"' in smena)
        panel = h.split('class="kn-panel"', 1)[-1]
        proverka(f"{rol}: кнопки нижней панели ведут на пункты меню (или на главную), не на убранное",
                 all(a == "/" or a in _adresa(nav.menu(rol)) for _, a, _ in nav.PANEL[rol])
                 and not [a for a in ubrano if f'href="{a}"' in panel])
        g = c.get("/", auth=kto).text.split('class="gl-grid"', 1)[-1].split("gl-docs", 1)[0]
        proverka(f"{rol}: главная — раздел «МойКласс» с «Проверкой МойКласса» и её описанием, убранных нет",
                 'href="/proverka-mk"' in g and nav.OPIS["/proverka-mk"] in g
                 and not [a for a in list(ubrano) + list(ESHCHE_V1) if f'href="{a}"' in g],
                 [a for a in list(ubrano) + list(ESHCHE_V1) if f'href="{a}"' in g])
        blok = _razdely_pulta(c, kto)
        proverka(f"{rol}: пульт, блок «Разделы» — строки «Ещё» нет, одна строка «Редко: Обзвон · Шахматка · Стоп-лист»",
                 "Ещё:" not in blok and blok.count("<b>Редко:</b>") == 1
                 and re.findall(r"href='([^']+)'", blok.split("<b>Редко:</b>", 1)[-1]) == list(UBRANO_ADMIN_V1)
                 and not [a for a in ESHCHE_V1 if f"href='{a}'" in blok], blok[-400:])
        proverka(f"{rol}: пульт, блок «Разделы» — есть «Проверка МойКласса», нет убранных пунктов владельца",
                 "href='/proverka-mk'" in blok and not [a for a in UBRANO_OWNER_V1 if f"href='{a}'" in blok])

    print("Л2. Убранные из меню страницы открываются по адресу, доступы прежние")
    for adres, imya in UBRANO_ADMIN_V1.items():
        for kto, rol in ((ADMIN, "admin"), (VLADELEC, "owner")):
            kod = c.get(adres, auth=kto, follow_redirects=False).status_code
            proverka(f"{rol}: «{imya}» {adres} → 200", kod == 200, kod)
    for adres in ESHCHE_V1:
        kod = c.get(adres, auth=ADMIN, follow_redirects=False).status_code
        proverka(f"admin: {adres} (была в строке «Ещё») → 200", kod == 200, kod)
    for adres, imya in UBRANO_OWNER_V1.items():
        kod = c.get(adres, auth=VLADELEC, follow_redirects=False).status_code
        proverka(f"owner: «{imya}» {adres} → 200", kod == 200, kod)
        kuda = lokaciya(c, adres)
        zhdem = (303, "/pult") if adres in VLADELCU_NA_PULT else (401, None)
        proverka(f"admin: «{imya}» {adres} — как до вечера 1: {zhdem[0]}", kuda == zhdem, kuda)
        proverka(f"{adres} без входа → 401", lokaciya(c, adres, kto=None)[0] == 401)
    proverka("nav.TOLKO_VLADELCU: убранные страницы владельца по-прежнему «только владельцу»",
             set(UBRANO_OWNER_V1) <= nav.TOLKO_VLADELCU and "/owner" in nav.TOLKO_VLADELCU
             and not nav.TOLKO_VLADELCU & {"/promo", "/pult?who=Борис", "/pult"}, sorted(nav.TOLKO_VLADELCU))
    bylo = nav.PEREEZD
    nav.PEREEZD = [("/primer", "Пример", "Журнал", "/journal#schetchiki", "Что-то ещё не перенесено.")]
    try:
        pa, pv = nav.plashka("/primer", "admin"), nav.plashka("/primer", "owner")
    finally:
        nav.PEREEZD = bylo
    proverka("плашка: админа не зовём на убранную из меню страницу владельца, даже если адрес с якорем",
             pa["kuda"] == [] and pv["kuda"] == [("Журнал", "/journal#schetchiki")], (pa, pv))
    for adres in list(UBRANO_ADMIN_V1) + list(ESHCHE_V1)[:3]:
        proverka(f"{adres}: плашки «архивная» нет — страница не в архиве", nav.plashka(adres, "admin") is None)

    print("Л3. Ссылки-заменители: убранное не потерялось")
    h = c.get("/owner", auth=VLADELEC).text
    blok = h.split("id='redko'", 1)[-1].split("</div>", 1)[0] if "id='redko'" in h else ""
    net = [a for a in list(UBRANO_OWNER_V1) + list(UBRANO_ADMIN_V1) + list(ESHCHE_V1) if f"href='{a}'" not in blok]
    proverka("/owner: блок «Редко нужное» — ссылки на пять страниц владельца, три админские и строку «Ещё»",
             "Редко нужное" in blok and not net, net)
    proverka("/owner: в блоке названия страниц владельца — Аномалии, Журнал, Откуда заявки, Камеры, Архив",
             all(f">{i}</a>" in blok for i in UBRANO_OWNER_V1.values()))
    proverka("/owner: вверху ссылка на блок", "href='#redko'" in h)
    telo = c.get("/liniya", auth=ADMIN).text.split("<main", 1)[-1]
    proverka("«Линия»: ссылка «Стоп-лист» в самой странице", 'href="/otkazy"' in telo and "Стоп-лист" in telo)
    telo = c.get("/mesta", auth=ADMIN).text.split("<main", 1)[-1]
    proverka("«Места»: ссылка «Шахматка» в самой странице", 'href="/grid"' in telo and "Шахматка" in telo)
    o = m._otkrytiya(30)
    uchteny = {x["path"] for x in o["stroki"]} | {x["adres"] for x in o["ne_otkryvali"]}
    proverka("счётчик открытий (/journal) следит и за убранными из меню страницами",
             set(UBRANO_ADMIN_V1) | set(UBRANO_OWNER_V1) <= uchteny, sorted((set(UBRANO_ADMIN_V1) | set(UBRANO_OWNER_V1)) - uchteny))

    print("Л4. Поиск: результаты «Раздел» с учётом роли")
    vladelca = {a for _, a in MENU_OWNER_V1} | set(UBRANO_OWNER_V1)
    proverka("админ, «оплат» → «Без оплаты», «Долги»",
             _razdely_poiska(c, "оплат", ADMIN) == [("Без оплаты", "/voronka"), ("Долги", "/dolgi")],
             _razdely_poiska(c, "оплат", ADMIN))
    proverka("владелец, «оплат» → ещё и «Контроль оплат»",
             set(_razdely_poiska(c, "оплат", VLADELEC)) == {("Без оплаты", "/voronka"), ("Долги", "/dolgi"),
                                                           ("Контроль оплат", "/kontrol-oplat")},
             _razdely_poiska(c, "оплат", VLADELEC))
    for q, zhdem in (("расписание", ("Шахматка", "/grid")), ("не звонить", ("Стоп-лист", "/otkazy")),
                     ("обзвон", ("Обзвон", "/obzvon-dnya")), ("шахматка", ("Шахматка", "/grid")),
                     ("стоп-лист", ("Стоп-лист", "/otkazy")), ("проверка", ("Проверка МойКласса", "/proverka-mk")),
                     ("МойКласс", ("Проверка МойКласса", "/proverka-mk")), ("явка", ("Явка", "/yavka")),
                     ("ушедших", ("Вернуть ушедших", "/obzvon")), ("конструктор", ("2–3 предмета в один вечер", "/constructor")),
                     ("ЛИНИЯ", ("Линия", "/liniya")), ("бланки", ("Бланки групп", "/blank-grupp"))):
        for kto, rol in ((ADMIN, "admin"), (VLADELEC, "owner")):
            r = _razdely_poiska(c, q, kto)
            proverka(f"{rol}, «{q}» → «{zhdem[0]}»", zhdem in r, r)
    for q, zhdem in (("аномалии", ("Аномалии", "/audit")), ("журнал", ("Журнал", "/journal")),
                     ("заявки", ("Откуда заявки", "/istochniki")), ("камеры", ("Камеры", "/kamery/nastroyka")),
                     ("архив", ("Архив", "/arhiv")), ("настройки", ("Настройки", "/owner")),
                     ("честность", ("Сверка честности", "/sverka-chestnosti")), ("документы", ("Мои документы", "/boris/docs"))):
        r = _razdely_poiska(c, q, VLADELEC)
        proverka(f"owner, «{q}» → «{zhdem[0]}»", zhdem in r, r)
        r = _razdely_poiska(c, q, ADMIN)
        proverka(f"admin, «{q}» → страницы владельца в поиске нет", not [x for x in r if x[1] in vladelca], r)
    # все слова, по которым поиск вообще что-то знает: названия и синонимы всех страниц
    slova = set()
    for imya, adres in nav.stranicy_poiska("owner"):
        slova |= set(nav._slova(imya))
        for f in nav.SINONIMY.get(adres, []):
            slova |= set(nav._slova(f)) | {f}
    slova = sorted(w for w in slova if len(w) >= 2)
    utechki, arhivnye, mertvye = [], [], []
    for q in slova:
        for kto, rol in ((ADMIN, "admin"), (VLADELEC, "owner")):
            for i in _poisk(c, q, kto):
                put = i["url"].split("?")[0]
                if rol == "admin" and i["type"] == "Раздел" and i["url"] in vladelca:
                    utechki.append((q, i["url"]))
                if put in ARHIVNYE:
                    arhivnye.append((rol, q, i["url"]))
                if put in ("/brief", "/students") or put in m.UBRANO:
                    mertvye.append((rol, q, i["url"]))
    proverka(f"admin: ни по одному из {len(slova)} слов и синонимов поиск не даёт страницу владельца", not utechki, utechki[:5])
    proverka("архивных страниц (nav.ARHIV) в поиске нет ни у кого", not arhivnye
             and not ARHIVNYE & {a.split("?")[0] for rol in ("admin", "owner") for _, a in nav.stranicy_poiska(rol)}, arhivnye[:5])
    proverka("поиск не ведёт на /brief, /students и убранные адреса", not mertvye, mertvye[:5])
    for q, adres in (("ученики", "/students"), ("платежи", "/payments"), ("фото и видео", "/gruppy/media"),
                     ("где теряем набор", "/nabor"), ("кому не звонили", "/nezvonili"), ("тексты набора", "/content"),
                     ("чаты групп", "/chaty"), ("проверка перед рассылкой", "/prelaunch")):
        r = _razdely_poiska(c, q, VLADELEC)
        proverka(f"архивная «{q}» ({adres}) поиском не находится — даже владельцу",
                 adres not in [a.split("?")[0] for _, a in r] and not [x for x in r if x[0].lower() == q], r)
    for kto, rol in ((ADMIN, "admin"), (VLADELEC, "owner")):
        ne200 = [(a, kod) for _, a in nav.stranicy_poiska(rol)
                 for kod in [c.get(a, auth=kto, follow_redirects=False).status_code] if kod != 200]
        proverka(f"{rol}: каждая страница, которую может выдать поиск, открывается этой роли (200)", not ne200, ne200)
    proverka("педагогу поиск страниц не положен", nav.stranicy_poiska("pedagog") == [] and nav.nayti_razdely("пульт", "pedagog") == [])
    items = _poisk(c, "Стоп", ADMIN)
    proverka("клиент и раздел вместе: сначала клиент «Стопова», за ним раздел «Стоп-лист»",
             [i["type"] for i in items] == ["Клиент", "Раздел"] and items[1]["url"] == "/otkazy", items)
    items = _poisk(c, "Тестова", ADMIN)
    proverka("фамилия — только клиенты, разделов нет; клиент с телефоном → «Линия»",
             items and all(i["type"] == "Клиент" for i in items)
             and any(i["url"] == f"/liniya?phone={SEMYA[-10:]}" for i in items), items)
    proverka("телефон и короткий запрос — без разделов",
             not _razdely_poiska(c, "9990001001", ADMIN) and not _razdely_poiska(c, "0001", ADMIN)
             and _poisk(c, "п", ADMIN) == [])
    proverka("две буквы ищут только в названиях («пр» не поднимает «Бланки групп» по синониму «пробные»)",
             ("Бланки групп", "/blank-grupp") not in _razdely_poiska(c, "пр", ADMIN)
             and ("Проверка МойКласса", "/proverka-mk") in _razdely_poiska(c, "пр", ADMIN), _razdely_poiska(c, "пр", ADMIN))
    proverka("разделов в выдаче не больше четырёх", all(len(_razdely_poiska(c, q, VLADELEC)) <= 4 for q in ("пр", "по", "оплат", "звон")))
    items = [i for i in _poisk(c, "Гр13", ADMIN) if i["type"] == "Группа"]
    proverka("группа с «+» в названии: адрес закодирован и открывает «Подбор группы» с этим названием",
             len(items) == 1 and "+" not in items[0]["url"].replace("%2B", "") and " " not in items[0]["url"]
             and "Гр13" in c.get(items[0]["url"], auth=ADMIN).text, items)
    js = open(os.path.join(tree, "app/static/nav.js"), encoding="utf-8").read()
    css = open(os.path.join(tree, "app/static/nav.css"), encoding="utf-8").read()
    proverka("nav.js: «Раздел» — своей меткой, карточка МойКласса — в новой вкладке; версия ?v= поднята до 3",
             "kn-gs-razdel" in js and "kn-gs-razdel" in css and 'target="_blank" rel="noopener"' in js
             and nav.VERSIYA == "3" and f"nav.js?v={nav.VERSIYA}" in c.get("/liniya", auth=ADMIN).text)
    proverka("подсказка в поле поиска называет разделы", "Поиск: раздел, группа, ребёнок, телефон" in shapka(c, "/liniya", ADMIN))

    print("Л5. «Аномалии» ушли из меню: красные находки — разделом 13 на «Проверке МойКласса», по роли")
    # 13.11.2026, Борис (М3): красные находки ленты за 14 дней показываем на «Проверке МойКласса».
    # Отчёты о самих админах (сверка честности, оплаты без подписи) с 12.11 только владельцу
    # (раздел «Д2») — на общей странице админ их видеть не должен.
    from app import audit
    audit.add_flag("деньги", "neg-balance:9003:stend", audit.HIGH, "Долг клиента 25 000 ₽", "Пробная Алиса · баланс −25 000 ₽",
                   user_id=9003)
    audit.add_flag("честность", "chest:user_subscriptions:1:stend", audit.HIGH, "Тестова Ева: оплачено уменьшено 9450 → 8400",
                   "Тестова Ева: оплачено уменьшено 9450 → 8400", user_id=9001)
    proverka("жёлтая находка в ленту не пишется (настройка anomalii_ne_krasnye по умолчанию выключена)",
             audit.add_flag("деньги", "test:mid:stend", audit.MID, "Долг клиента 4 000 ₽") is False
             and "anomalii_ne_krasnye" in m.SETTABLE)

    def r13(kto):
        h = c.get("/proverka-mk", auth=kto).text
        return h.split('id="r13"', 1)[-1].split("</section>", 1)[0] if 'id="r13"' in h else "", h
    ra, ha = r13(ADMIN)
    rv, hv = r13(VLADELEC)
    proverka("админ: раздел 13 есть, в нём долг со ссылкой на карточку МойКласса",
             "Долг клиента 25 000 ₽" in ra and 'href="https://app.moyklass.com/user/9003"' in ra and "только чтение" in ra, ra[:300])
    proverka("админ: находки сверки честности на странице нет вовсе — ни в разделе, ни в сводке",
             "оплачено уменьшено" not in ha and "честность" not in ra and 'href="/audit"' not in ha.split("<main", 1)[-1])
    proverka("владелец: в разделе 13 и долг, и находка сверки честности, и ссылка на «Аномалии»",
             "Долг клиента 25 000 ₽" in rv and "оплачено уменьшено" in rv and 'href="/audit"' in rv)
    proverka("раздел только для чтения: ни кнопок, ни форм — у обеих ролей",
             not any(x in ra + rv for x in ("<button", "onclick", "<form", "<input")))
    da = c.get("/api/proverka-mk", auth=ADMIN).json()["krasnye"]
    dv = c.get("/api/proverka-mk", auth=VLADELEC).json()["krasnye"]
    proverka("API: админу — без сверки честности, владельцу — всё",
             (da["vsego"], sorted(da["po_vidam"]), dv["vsego"], sorted(dv["po_vidam"])) == (1, ["деньги"], 2, ["деньги", "честность"]),
             (da["po_vidam"], dv["po_vidam"]))
    proverka("лента «Аномалии» админу по-прежнему закрыта (Д2), страница /audit владельцу открыта",
             c.get("/api/audit", auth=ADMIN).status_code == 401 and lokaciya(c, "/audit") == (303, "/pult")
             and c.get("/audit", auth=VLADELEC).status_code == 200)


# --- М. Уборка и безопасность (13.11.2026, вечер 1) --------------------------------------
# Борис, 13.11.2026: «М, П, Т — по рекомендациям». Здесь зафиксировано: в коде нет телефонов
# клиентов; мёртвый код убран, а то, что убирать рано, на месте; кампании августа–сентября в
# базе знаний свёрнуты в один раздел; полная выгрузка МойКласса — только владельцу; секреты
# вебхуков задаются настройкой и наружу не отдаются; чужой сайт не может послать изменяющий
# запрос ни на один адрес под входом; запись через GET убрана.

# Файлы, пройденные 13.11 на телефоны клиентов. В них остаются только выдуманные номера
# (7999…, 7900…, 7916000…), заглушки и номера самого центра.
FAJLY_BEZ_TELEFONOV = ("app/autopilot.py", "app/ads_voronka.py", "app/main.py", "app/wazzup.py",
                       "app/nadezhdalist.py", "app/nabormail.py", "app/dostavka.py", "app/wabatexts.py",
                       "app/naryad.py", "docs/rabota/pult_testy.py")
# Номера центра (каналы Wazzup, их использование в коде не трогали) и запасной номер сводки владельцу.
# Сами номера в тест не вписаны (репозиторий открытый) — только начала их хешей sha256.
NOMERA_CENTRA_HESH = {"0f518dbb18416b69", "1b9227c9ca32311c", "87c8d23eda66d024", "95d7c0887ab6f156"}


def _nomer_centra(n: str) -> bool:
    import hashlib
    return hashlib.sha256(n.encode()).hexdigest()[:16] in NOMERA_CENTRA_HESH


# Действующее — должно остаться наверху базы знаний, не в свёрнутом архиве кампаний.
BAZA_NAVERHU = ("pravila_kidsup", "shablony_wazzup", "skripty_nabora", "prodazhi_vozrazheniya", "tri_scenariya",
                "pedagog_probnoe", "zapis_v_gruppy", "predmety_shpargalka", "promo_upravlenie", "karta_kabinetov",
                "diagnostika", "english_standard", "pamyatka_pedagoga", "roditeli_vidyat", "tablichka_skidka")
# Изменяющие адреса без зависимости входа — заморожены вместе с тем, чем каждый защищён.
BEZ_VHODA_IZM = {
    "POST /vhod": "форма входа: принимается только со своей страницы (vhod.svoy_zapros)",
    "POST /vyhod": "выход: только со своей страницы",
    "POST /mango/events": "подпись Mango (ключ и соль АТС)",
    "POST /mango/{rest:path}": "подпись Mango",
    "POST /events/{rest:path}": "подпись Mango",
    "POST /events/sms": "подпись Mango: без неё статус пишется с пометкой «подпись не сошлась»",
    "POST /api/pedagog/save": "ключ педагога в теле запроса",
    "POST /api/tg/webhook/{secret}": "секрет в адресе",
    "POST /hook/lead": "ключ ?key= (настройка lead_hook_key)",
    "POST /marquiz/webhook": "ключ ?key= (настройка marquiz_key, до неё — прежний ключ)",
    "POST /wazzup/webhook": "ключ ?k= (настройка wazzup_hook_key; пока не задана — без проверки)",
    "POST /api/public/lead": "форма заявки на сайте — открыта нарочно",
    "POST /kamery": "пароль камер для родителей",
}
CHUZHOY_FON = {"sec-fetch-site": "cross-site", "sec-fetch-mode": "cors"}       # fetch/форма с чужого сайта
CHUZHOY_ORIGIN = {"origin": "https://chuzhoy.example"}                          # старый браузер: только Origin
SVOYA_STRANICA = {"sec-fetch-site": "same-origin", "sec-fetch-mode": "cors"}    # запрос со своей страницы


class _OtvetWazzup:
    """Ответ Wazzup на стенде: сеть закрыта, вместо неё — эта заглушка."""

    def __init__(self, dannye, kod=200):
        self._d, self.status_code, self.text = dannye, kod, json.dumps(dannye)

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


def test_uborka_bezopasnost(c, m, tree, baza):
    import datetime as dt
    import app.autopilot as ap
    from app import blank_grupp, wazzup as wz

    print("М1. Телефонов клиентов в коде нет; миграция с номерами — пустая метка")
    src = inspect.getsource(ap._migrations)
    proverka("миграция replied_requeue_v1: списка номеров и тела нет, метка ставится",
             '_mark("migration", "replied_requeue_v1")' in src and "replied_phones" not in src
             and 'if _mark("migration", "replied_requeue_v1")' not in src and not re.findall(r"\b[78]9\d{9}\b", src))
    kusok = src.split('_mark("migration", "replied_requeue_v1")', 1)[1].split('if _mark("migration", "anna_away_v1")', 1)[0]
    proverka("…между этой меткой и следующей миграцией нет ни одной строки кода", not kusok.strip(), kusok[:200])
    proverka("requeue и ручные «ответы 12.08» из миграций никто не зовёт",
             "broadcast_requeue_undelivered" not in src and "wazzup_inbox" not in src)
    for put in FAJLY_BEZ_TELEFONOV:
        tekst = open(os.path.join(tree, put), encoding="utf-8").read()
        nomera = set(re.findall(r"(?<![\d.])(?:\+?7|8)9\d{9}(?!\d)", tekst))
        chuzhie = sorted(n for n in nomera if not n.lstrip("+").startswith(("7999", "8999", "7900", "7916000"))
                         and not _nomer_centra(n.lstrip("+")))
        proverka(f"{put}: только выдуманные номера и номера центра", not chuzhie, chuzhie[:5])
    dost = open(os.path.join(tree, "app/dostavka.py"), encoding="utf-8").read()
    # 13.11.2026, вечер (проверка «вечера 1»): ошибочный номер из опечатки здесь был вписан целиком —
    # возможно, это телефон постороннего человека, а репозиторий публичный. Проверяем без него:
    # запасной номер после get_setting("digest_phone") в файле один, и это номер сводки автопилота.
    zapasnye = set(re.findall(r'get_setting\("digest_phone"\) or "(\d+)"', dost))
    proverka("запасной номер тревоги о канале — тот же, что у сводки автопилота (была опечатка в двух цифрах)",
             zapasnye == {"79104526673"}, len(zapasnye))

    print("М2. Мёртвый код убран, а то, что убирать рано, — на месте")
    est = marshruty(m)
    kod = {}
    for papka, _, fajly in os.walk(os.path.join(tree, "app")):
        for fl in fajly:
            if fl.endswith(".py"):
                kod[fl] = open(os.path.join(papka, fl), encoding="utf-8").read()
    gde = [fl for fl, t in kod.items() if re.search(r"^\s*(?:from\s+(?:\.|app)\s+import\s+[^\n]*\bmeganplan\b|"
                                                   r"from\s+(?:\.|app\.)meganplan\s+import|import\s+app\.meganplan\b)", t, re.M)]
    proverka("app/meganplan.py удалён и нигде не импортируется",
             not os.path.exists(os.path.join(tree, "app", "meganplan.py")) and not gde, gde)
    proverka("документ /base/megaplan, который он когда-то собрал, открывается как раньше",
             c.get("/base/megaplan", auth=ADMIN).status_code == 200)
    proverka("адреса /api/bez-oplaty/status (GET и POST) убраны, кода при них не осталось",
             not UBRANY_VECHER1 & est and "_bez_oplaty_status_init(" not in kod["main.py"]
             and c.get("/api/bez-oplaty/status", auth=ADMIN).status_code == 404)
    proverka("старые адреса /bez-oplaty и /ne-oplatili по-прежнему ведут на «Без оплаты»",
             lokaciya(c, "/bez-oplaty") == (303, "/voronka") and lokaciya(c, "/ne-oplatili") == (303, "/voronka"))
    proverka("таблицу bez_oplaty_status код не трогает: ни создания, ни удаления, ни записи",
             not re.search(r"(DROP TABLE|DELETE FROM|INSERT[^\n]*INTO|CREATE TABLE[^\n]*)\s+bez_oplaty_status", "\n".join(kod.values())))
    proverka("app/boris.py и /api/boris/* не тронуты (сначала перенос пунктов на сервере)",
             os.path.exists(os.path.join(tree, "app", "boris.py"))
             and {"GET /api/boris/tasks", "POST /api/boris/tasks", "POST /api/boris/status"} <= est)
    proverka("autopilot.evening_recall не тронут (ждёт решения владельца)",
             callable(getattr(ap, "evening_recall", None)) and "evening_recall(mk)" in inspect.getsource(ap))

    print("М3. База знаний: кампании августа–сентября — одним свёрнутым разделом внизу")
    vse = [sl for _, punkty in m.DOC_GROUPS for sl, _, _ in punkty]
    proverka("в списке архива нет опечаток: каждый адрес есть в белом списке DOC_GROUPS",
             m.ARHIV_KAMPANIY <= set(vse), sorted(m.ARHIV_KAMPANIY - set(vse)))
    proverka("действующие правила, скрипты и методики занятий в архив не попали",
             not set(BAZA_NAVERHU) & m.ARHIV_KAMPANIY and not [x for x in m.ARHIV_KAMPANIY if x.startswith("__url:/") and "/static/" not in x],
             sorted(set(BAZA_NAVERHU) & m.ARHIV_KAMPANIY))
    kampanii = [sl for sl in vse if re.match(r"(plan_\d|spisok_|obzvon_|list_|ostatok_|dod_|zadachi_|utro_)", sl)]
    proverka("все планы дня, списки, листы, обзвоны, хвосты, ДОД и задачи тех недель — в архиве",
             kampanii and set(kampanii) <= m.ARHIV_KAMPANIY, sorted(set(kampanii) - m.ARHIV_KAMPANIY))
    h = c.get("/base", auth=ADMIN).text.split("<main", 1)[-1]
    verh, razdel, niz = h.partition('<details class="base-arhiv"')
    proverka("раздел «Архив: кампании августа–сентября 2026» есть, он свёрнут (<details> без open) и стоит последним",
             razdel and m.ARHIV_KAMPANIY_IMYA in niz.split("</summary>", 1)[0] and not re.match(r"[^>]*\bopen\b", niz)
             and "<h2>" not in niz and niz.count("</details>") == 1, niz[:200])
    ssylki_verh, ssylki_niz = set(re.findall(r'href="/base/([^"]+)"', verh)), set(re.findall(r'href="/base/([^"]+)"', niz))
    proverka("наверху нет ни одного документа из архива кампаний, внизу — только они",
             not ssylki_verh & m.ARHIV_KAMPANIY and ssylki_niz and ssylki_niz <= m.ARHIV_KAMPANIY,
             sorted(ssylki_verh & m.ARHIV_KAMPANIY)[:5])
    proverka("действующее — наверху: правила, шаблоны Wazzup, скрипты, памятки",
             set(BAZA_NAVERHU) <= ssylki_verh, sorted(set(BAZA_NAVERHU) - ssylki_verh))
    proverka("наверху — четыре рабочих раздела и прежний «Архив — устаревшее», ссылки на пульт и бланки на месте",
             re.findall(r"<h2>([^<]+)</h2>", verh) == [t for t, _ in m.DOC_GROUPS]
             and 'href="/pult"' in verh and 'href="/blank-grupp"' in verh, re.findall(r"<h2>([^<]+)</h2>", verh))
    docs = os.path.join(tree, "docs")
    s_fajlom = [sl for sl in dict.fromkeys(vse) if os.path.exists(os.path.join(docs, f"{sl}.html"))]
    proverka("ни один документ со страницы не пропал: сколько файлов в белом списке — столько ссылок на странице",
             ssylki_verh | ssylki_niz == set(s_fajlom), sorted(set(s_fajlom) ^ (ssylki_verh | ssylki_niz))[:5])
    plohie = [sl for sl in s_fajlom if c.get(f"/base/{sl}", auth=ADMIN).status_code != 200]
    proverka(f"адреса не менялись: все {len(s_fajlom)} документов открываются по /base/<имя>, архивные тоже", not plohie, plohie[:5])
    proverka("чужое имя по-прежнему не открывается (белый список на месте)",
             c.get("/base/net_takogo", auth=ADMIN).status_code == 404 and c.get("/base/..%2Fapp%2Fmain", auth=ADMIN).status_code == 404)
    print(f"        наверху {len(ssylki_verh)} документов и {verh.count('<tr>') - len(ssylki_verh)} ссылок на страницы и файлы, "
          f"в свёрнутом архиве {niz.count('<tr>')} (в списке архива {len(m.ARHIV_KAMPANIY)} имён)")

    print("М4. Полная выгрузка МойКласса — только владельцу")
    vhod = {f"{metod} {r.path}": {getattr(d.call, "__name__", "") for d in getattr(getattr(r, "dependant", None), "dependencies", [])}
            for r in m.app.routes for metod in getattr(r, "methods", ())}
    proverka("POST /api/sync/start — под входом владельца, GET /api/sync/status — под общим (его опрашивают рутины)",
             vhod["POST /api/sync/start"] == {"check_owner"} and vhod["GET /api/sync/status"] == {"check_auth"})
    r_a, r_v = c.post("/api/sync/start", auth=ADMIN), c.post("/api/sync/start", auth=VLADELEC)
    proverka("админу запуск закрыт (401/403), владельца пускает (на стенде ключа МойКласса нет — «API-ключ не задан»)",
             r_a.status_code in (401, 403) and r_v.status_code == 400 and r_v.json()["ok"] is False, (r_a.status_code, r_v.status_code))
    proverka("кнопка «Синхронизировать» — только у владельца",
             'id="sync-btn"' not in c.get("/mesta", auth=ADMIN).text and 'id="sync-btn"' in c.get("/mesta", auth=VLADELEC).text)
    proverka("автопилот запускает выгрузку сам, без HTTP",
             "_sync.start_sync()" in inspect.getsource(ap) and "/api/sync/start" not in inspect.getsource(ap))

    print("М5. Марквиз и заявки старого сайта: ключ — настройкой, наружу не отдаётся")
    # Запросы идут без телефона: обработчик после проверки ключа отвечает «нет телефона» и
    # ничего не создаёт — ни заявки, ни карточки в CRM.
    def mq(klyuch=None):
        return c.post("/marquiz/webhook", params={} if klyuch is None else {"key": klyuch}, json={})
    proverka("настройки нет — действует прежний ключ из кода, заявки не теряются",
             not m.db.get_setting("marquiz_key") and mq(m.MARQUIZ_KEY).status_code == 200
             and mq(m.MARQUIZ_KEY).json() == {"ok": False, "error": "нет телефона"})
    proverka("без ключа, с чужим ключом и с ключом русскими буквами — 403, не 500",
             [mq().status_code, mq("").status_code, mq("чужой").status_code, mq("mqz-chuzhoy").status_code] == [403] * 4)
    NOVYY = "stend-marquiz-novyy-klyuch-QZXW"
    r = c.post("/api/settings", json={"key": "marquiz_key", "value": NOVYY}, auth=VLADELEC)
    proverka("ключ задаётся настройкой marquiz_key; ответ на сохранение значения не содержит",
             r.status_code == 200 and NOVYY not in r.text and "marquiz_key" in m.SETTABLE and "marquiz_key" in m.SECRET_KEYS, r.text[:120])
    proverka("настройка задана — принимается ТОЛЬКО она: прежний ключ из кода больше не подходит",
             mq(NOVYY).status_code == 200 and mq(m.MARQUIZ_KEY).status_code == 403 and mq().status_code == 403)
    nastr = c.get("/api/settings", auth=VLADELEC)
    stranica = c.get("/owner", auth=VLADELEC).text
    proverka("значение не отдаётся ни в /api/settings, ни на странице «Настройки» (видны только хвост и длина); "
             "поле для ключа на странице есть",
             NOVYY not in nastr.text and nastr.json()["marquiz_key"] == f"…QZXW ({len(NOVYY)} симв.)"
             and NOVYY not in stranica and NOVYY[-4:] not in stranica and NOVYY[:-3] not in stranica
             and "name='marquiz_key'" in stranica and "name='wazzup_hook_key'" in stranica, nastr.json().get("marquiz_key"))
    proverka("админу настройки по-прежнему закрыты", c.get("/api/settings", auth=ADMIN).status_code in (401, 403))
    r = c.post("/owner", data={"marquiz_key": NOVYY + "-forma"}, auth=VLADELEC)
    proverka("ключ можно вписать и на странице «Настройки»; в ответе после сохранения его значения нет",
             r.status_code == 200 and m.db.get_setting("marquiz_key") == NOVYY + "-forma" and NOVYY not in r.text
             and "Сохранено: Марквиз" in r.text and mq(NOVYY + "-forma").status_code == 200 and mq(NOVYY).status_code == 403,
             r.status_code)
    m.db.set_setting("marquiz_key", "  ")
    proverka("пустая настройка (пробелы) — снова прежний ключ, а не «подходит любой»",
             mq(m.MARQUIZ_KEY).status_code == 200 and mq("").status_code == 403 and mq(" ").status_code == 403)
    m.db.set_setting("marquiz_key", "")
    proverka("короткий секрет в /api/settings — без хвоста, длинный — последние четыре знака",
             m._mask("lead_hook_key", "abcdef1234") == "задано (10 симв.)" and m._mask("marquiz_key", "x" * 20 + "ABCD") == "…ABCD (24 симв.)"
             and m._mask("digest_off", "1") == "1" and m._mask("marquiz_key", "") == "")
    proverka("сравнение ключей — secrets.compare_digest, пустой наш ключ не подходит никому",
             "secrets.compare_digest" in inspect.getsource(m._klyuch_veren) and not m._klyuch_veren("", "") and not m._klyuch_veren("x", "")
             and m._klyuch_veren("ключ-1", "ключ-1") and not m._klyuch_veren("ключ-1", "ключ-2"))

    def lead(klyuch=None):
        return c.post("/hook/lead", params={} if klyuch is None else {"key": klyuch}, json={})
    m.db.set_setting("lead_hook_key", "stend-lead-key")
    proverka("/hook/lead: с заданным ключом чужой запрос отклоняется, свой проходит проверку (дальше — «нет телефона»)",
             lead("чужой").json() == {"ok": False} and lead().json() == {"ok": False}
             and lead("stend-lead-key").json() == {"ok": False, "error": "no phone"}
             and "lead_hook_key" in m.SECRET_KEYS and "stend-lead-key" not in c.get("/api/settings", auth=VLADELEC).text)
    m.db.set_setting("lead_hook_key", "")
    proverka("/hook/lead без настройки — как раньше, принимает без ключа (на сервере ключ задан)",
             lead().json() == {"ok": False, "error": "no phone"})

    print("М6. Вебхук Wazzup: необязательный ключ ?k=")
    # Тело запроса пустое: обработчик отвечает сразу и разбор сообщений не запускает.
    def wh(klyuch=None):
        return c.post("/wazzup/webhook", params={} if klyuch is None else {"k": klyuch}, json={}).status_code
    proverka("ключ не задан — вебхук принимает всё, как раньше; регистрируется прежний адрес",
             not wz.hook_klyuch() and [wh(), wh("lyuboy")] == [200, 200] and wz.nash_hook() == wz.OUR_HOOK
             and not wz.hook_strogo())
    K1, K2 = "stend-wazzup-klyuch-odin", "stend-wazzup-klyuch-dva"
    m.db.set_setting("wazzup_hook_key", K1)
    proverka("ключ задан, но Wazzup ещё шлёт по старому адресу — сообщения не теряются: принимаем и без ключа",
             [wh(), wh("nevernyy")] == [200, 200] and not wz.hook_strogo() and "ждём первое событие" in wz.hook_sostoyanie())
    proverka("первое событие с верным ключом — и дальше ключ обязателен: без ключа, с чужим, русскими буквами — 403",
             wh(K1) == 200 and wz.hook_strogo() and [wh(), wh("nevernyy"), wh("ключ"), wh(K1)] == [403, 403, 403, 200]
             and "ключ требуется" in wz.hook_sostoyanie())
    proverka("проверочный GET /wazzup/webhook отвечает, как раньше", c.get("/wazzup/webhook").json() == {"ok": True})
    proverka("адрес для регистрации — с ключом; для журнала и ответов — без его значения",
             wz.nash_hook() == f"{wz.OUR_HOOK}?k={K1}" and K1 not in wz.bez_klyucha(wz.nash_hook())
             and wz.bez_klyucha(wz.nash_hook()) != wz.OUR_HOOK and wz.bez_klyucha("https://drugoy.example/hook") == "https://drugoy.example/hook")
    import httpx
    v_wazzup, zvali = [wz.OUR_HOOK], []

    def _get(url, **kw):
        zvali.append(("GET", url))
        return _OtvetWazzup({"webhooksUri": v_wazzup[0], "subscriptions": {}})

    def _patch(url, **kw):
        zvali.append(("PATCH", url, kw.get("json")))
        v_wazzup[0] = kw["json"]["webhooksUri"]
        return _OtvetWazzup({})
    bylo = (httpx.get, httpx.patch)
    httpx.get, httpx.patch = _get, _patch          # сеть закрыта: вместо Wazzup — заглушка, сама регистрация не идёт
    try:
        proverka("ключ уже требуется, а в Wazzup адрес без ключа — это «не наш адрес»: сторож его перерегистрирует",
                 wz.webhook_uri() != wz.OUR_HOOK and K1 not in wz.webhook_uri())
        r = c.post("/api/wazzup/webhook", auth=VLADELEC)
        proverka("«вернуть вебхук» регистрирует адрес с ключом; в ответе ключа нет, адрес считается нашим",
                 r.status_code == 200 and r.json() == {"ok": True, "uri": wz.OUR_HOOK} and v_wazzup[0] == f"{wz.OUR_HOOK}?k={K1}"
                 and zvali[-2][0] == "PATCH" and zvali[-2][1].endswith("/webhooks"), (r.text[:120], v_wazzup))
        proverka("сторож автопилота сравнивает адрес с OUR_HOOK и чинит через set_webhook() — адрес с ключом для него «наш»",
                 wz.webhook_uri() == wz.OUR_HOOK and "uri != wz.OUR_HOOK" in inspect.getsource(ap.wazzup_watchdog)
                 and "wz.set_webhook()" in inspect.getsource(ap.wazzup_watchdog))
        r = c.get("/api/wazzup/webhook", auth=VLADELEC)
        proverka("состояние вебхука владельцу: ключа в ответе нет, есть строка о проверке",
                 r.status_code == 200 and K1 not in r.text and "ключ требуется" in r.json()["klyuch"]
                 and r.json()["wazzup"]["webhooksUri"] == f"{wz.OUR_HOOK}?k=…", r.text[:200])
        proverka("ключа нет ни в /api/settings, ни на странице «Настройки»",
                 K1 not in c.get("/api/settings", auth=VLADELEC).text and K1 not in c.get("/owner", auth=VLADELEC).text
                 and "wazzup_hook_key" in m.SETTABLE and "wazzup_hook_key" in m.SECRET_KEYS)
        v_wazzup[0] = "https://api.moyklass.com/v1/hooks/wazzupEvents/stend"
        proverka("чужой адрес (вебхук переписала интеграция МойКласса) виден как есть — сторож вернёт наш",
                 wz.webhook_uri() == v_wazzup[0])
        # смена ключа: пока Wazzup шлёт со старым, ничего не теряется; новый начинает требоваться с первого события с ним
        m.db.set_setting("wazzup_hook_key", K2)
        v_wazzup[0] = f"{wz.OUR_HOOK}?k={K1}"
        proverka("смена ключа: старый адрес — «не наш» и без значения ключа; события со старым ключом пока принимаются",
                 wz.webhook_uri() not in (wz.OUR_HOOK,) and K1 not in wz.webhook_uri() and not wz.hook_strogo()
                 and [wh(K1), wh()] == [200, 200])
        proverka("…после первого события с новым ключом старый ключ и запросы без ключа — 403",
                 wh(K2) == 200 and wz.hook_strogo() and [wh(K1), wh(), wh(K2)] == [403, 403, 200])
        proverka("set_webhook() без адреса берёт адрес с действующим ключом",
                 wz.set_webhook() is True and v_wazzup[0] == f"{wz.OUR_HOOK}?k={K2}" and wz.webhook_uri() == wz.OUR_HOOK)
        m.db.set_setting("wazzup_hook_key", "")
        proverka("ключ убран — снова как раньше: принимаем всё, регистрируем адрес без ключа, старый адрес с ключом — «не наш»",
                 [wh(), wh(K2)] == [200, 200] and not wz.hook_strogo() and wz.nash_hook() == wz.OUR_HOOK
                 and wz.webhook_uri() != wz.OUR_HOOK and K2 not in wz.webhook_uri())
        m.db.set_setting("wazzup_hook_key", K1)
        v_wazzup[0] = wz.OUR_HOOK
        m.db.set_setting(wz._HOOK_OK, "")
        proverka("первое включение: ключ задан, событий с ним ещё не было, в Wazzup прежний адрес — сторож владельца не тревожит",
                 wz.webhook_uri() == wz.OUR_HOOK and not wz.hook_strogo())
    finally:
        httpx.get, httpx.patch = bylo
        m.db.set_setting("wazzup_hook_key", "")
        m.db.set_setting(wz._HOOK_OK, "")
    proverka("вебхуки остались адресами без входа (проверка ключа — внутри обработчика)",
             all(not vhod[x] for x in ("POST /wazzup/webhook", "POST /marquiz/webhook", "POST /hook/lead")))

    print("М7. Чужой сайт не может послать изменяющий запрос (сделано 12.11 в vhod.kto — здесь зафиксировано по всем адресам)")
    pod_vhodom, bez_vhoda = [], set()
    for r in m.app.routes:
        for metod in getattr(r, "methods", ()) or ():
            if metod in ("GET", "HEAD", "OPTIONS"):
                continue
            zavis = vhod[f"{metod} {r.path}"]
            if zavis & {"check_auth", "check_owner", "check_pedagog_or_admin"}:
                pod_vhodom.append((metod, r.path, VLADELEC if "check_owner" in zavis else ADMIN))
            else:
                bez_vhoda.add(f"{metod} {r.path}")
    proverka("изменяющих адресов без зависимости входа — ровно тринадцать известных (вебхуки, сайт, вход); новый ломает тест",
             bez_vhoda == set(BEZ_VHODA_IZM), sorted(bez_vhoda ^ set(BEZ_VHODA_IZM)))
    for imya, zag in (("фоновый запрос с чужого сайта (Sec-Fetch-Site: cross-site)", CHUZHOY_FON),
                      ("переход-форма с чужого сайта (cross-site, navigate)", {"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate"}),
                      ("соседний поддомен (Sec-Fetch-Site: same-site)", {"sec-fetch-site": "same-site", "sec-fetch-mode": "cors"}),
                      ("чужой Origin без Sec-Fetch-*", CHUZHOY_ORIGIN)):
        ne403 = [(metod, put) for metod, put, kto in pod_vhodom
                 if c.request(metod, re.sub(r"\{[^}]+\}", "1", put), json={}, headers=zag, auth=kto).status_code != 403]
        proverka(f"{imya}: все {len(pod_vhodom)} изменяющих адресов под входом отвечают 403, хотя пароль верный", not ne403, ne403[:5])
    proverka("таких адресов — не меньше 190 (AUTH, OWNER_AUTH и вход педагога)", len(pod_vhodom) >= 190, len(pod_vhodom))
    with m.db.get_conn() as conn:
        nastr_do = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
    r1 = c.post("/api/blank-grupp/edit", json={"k": "u:stend:m7", "v": "чужой"}, headers=CHUZHOY_FON, auth=ADMIN)
    r2 = c.post("/api/settings", json={"key": "pult_hvost_glavnoe", "value": "чужой"}, headers=CHUZHOY_ORIGIN, auth=VLADELEC)
    proverka("отказ — до обработчика: правка бланка и настройка с чужого сайта не записаны",
             (r1.status_code, r2.status_code) == (403, 403) and "u:stend:m7" not in blank_grupp.edits()
             and m.db.get_setting("pult_hvost_glavnoe") != "чужой")
    r1 = c.post("/api/blank-grupp/edit", json={"k": "u:stend:m7", "v": "своя"}, headers=SVOYA_STRANICA, auth=ADMIN)
    r2 = c.post("/api/blank-grupp/edit", json={"k": "u:stend:m7", "v": ""}, headers={"origin": "https://app.kidsup.ru"}, auth=ADMIN)
    proverka("со своей страницы под общим входом — проходит (Sec-Fetch-Site: same-origin; Origin https://app.kidsup.ru)",
             (r1.status_code, r2.status_code) == (200, 200) and "u:stend:m7" not in blank_grupp.edits(), (r1.status_code, r2.status_code))
    stare = m.db.get_setting("pult_hvost_glavnoe") or ""
    r1 = c.post("/api/settings", json={"key": "pult_hvost_glavnoe", "value": stare}, headers=SVOYA_STRANICA, auth=VLADELEC)
    r2 = c.post("/api/sync/start", headers=SVOYA_STRANICA, auth=VLADELEC)
    proverka("со своей страницы под входом владельца — проходит (настройка сохранена; запуск выгрузки дошёл до обработчика)",
             (r1.status_code, r2.status_code) == (200, 400), (r1.status_code, r2.status_code))
    proverka("скрипт рутины (без Origin и Sec-Fetch-*) под служебным входом — проходит, как раньше",
             c.post("/api/blank-grupp/edit", json={"k": "u:stend:m7", "v": ""}, auth=ADMIN).status_code == 200)
    proverka("фоновый GET с чужого сайта (картинка, fetch) — тоже 403; переход по ссылке из чата страницу открывает",
             c.get("/api/blank-grupp/budushchie", headers=CHUZHOY_FON, auth=ADMIN).status_code == 403
             and c.get("/liniya", headers={"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate", "accept": "text/html"},
                       auth=ADMIN).status_code == 200)
    with m.db.get_conn() as conn:
        proverka("число настроек за проверки не выросло больше чем на одну (pult_hvost_glavnoe)",
                 conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] - nastr_do <= 1)

    print("М8. Запись через GET убрана: обзвон и бланки групп")
    from app import obzvon
    segodnya = dt.date.today().isoformat()          # obzvon.audit считает окно от настоящей даты
    with m.db.get_conn() as conn:
        obzvon._done_init(conn)
        conn.execute("DELETE FROM obzvon_done")
        conn.execute("INSERT INTO obzvon_done (uid, ts, who, note) VALUES (987654, ?, 'Стенд', '')", (segodnya + "T10:00:00",))

    def otmetok():
        with m.db.get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM obzvon_done").fetchone()[0]
    r = c.get("/api/obzvon/audit", auth=ADMIN)
    proverka("GET /api/obzvon/audit показывает пустую отметку и ничего не снимает",
             r.status_code == 200 and [x["uid"] for x in r.json()["пустые"]] == [987654] and "вернули" not in r.json() and otmetok() == 1,
             r.text[:200])
    r = c.get("/api/obzvon/audit?vernut=1", auth=ADMIN)
    proverka("GET …?vernut=1 — 405 с подсказкой про POST, отметка на месте (раньше открытие такой ссылки её стирало)",
             r.status_code == 405 and "POST /api/obzvon/audit/vernut" in r.json()["detail"] and r.headers.get("allow") == "POST"
             and otmetok() == 1, (r.status_code, otmetok()))
    proverka("…и переход по такой ссылке с чужого сайта её не снимает",
             c.get("/api/obzvon/audit?vernut=1", auth=ADMIN,
                   headers={"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate"}).status_code == 405 and otmetok() == 1)
    proverka("POST /api/obzvon/audit/vernut с чужого сайта — 403, отметка на месте",
             c.post("/api/obzvon/audit/vernut", auth=ADMIN, headers=CHUZHOY_FON).status_code == 403 and otmetok() == 1)
    r = c.post("/api/obzvon/audit/vernut", auth=ADMIN, headers=SVOYA_STRANICA)
    proverka("POST /api/obzvon/audit/vernut снимает пустую отметку — то, что раньше делал GET",
             r.status_code == 200 and r.json().get("вернули") == 1 and otmetok() == 0, r.text[:200])
    proverka("без входа — 401", c.post("/api/obzvon/audit/vernut").status_code == 401
             and c.post("/api/blank-grupp/obnovit").status_code == 401)

    zvali_model = []
    nast = blank_grupp.model

    def _model(force=False, wait=False):
        zvali_model.append((bool(force), bool(wait)))
        return {"model": None, "busy": False, "err": "", "age_min": None}
    blank_grupp.model = _model          # МойКласс на стенде недоступен: смотрим, с чем зовут пересчёт
    try:
        r = c.get("/api/blank-grupp?refresh=1", auth=ADMIN)
        proverka("GET /api/blank-grupp?refresh=1 пересчёт из МойКласса больше не запускает (только чтение)",
                 r.status_code == 200 and zvali_model == [(False, False)], zvali_model)
        proverka("…в том числе по ссылке с чужого сайта",
                 c.get("/api/blank-grupp?refresh=1", auth=ADMIN, headers={"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate"}
                       ).status_code == 200 and zvali_model == [(False, False)] * 2, zvali_model)
        proverka("POST /api/blank-grupp/obnovit с чужого сайта — 403, пересчёт не начат",
                 c.post("/api/blank-grupp/obnovit", auth=ADMIN, headers=CHUZHOY_FON).status_code == 403 and len(zvali_model) == 2)
        r = c.post("/api/blank-grupp/obnovit", auth=ADMIN, headers=SVOYA_STRANICA)
        proverka("POST /api/blank-grupp/obnovit запускает пересчёт и ждёт его — как раньше кнопка через GET",
                 r.status_code == 200 and zvali_model[-1] == (True, True) and set(r.json()) == {"model", "busy", "err", "age_min"},
                 zvali_model)
    finally:
        blank_grupp.model = nast
    shablon = open(os.path.join(tree, "app/templates/blank_grupp.html"), encoding="utf-8").read()
    proverka("кнопка «Обновить проверки из МойКласса» на странице зовёт POST, адреса ?refresh=1 в странице нет",
             "fetch('/api/blank-grupp/obnovit',{method:'POST'})" in shablon and "?refresh=1'" not in shablon
             and "$('#refresh').onclick=()=>load(true)" in shablon)
    roli = open(os.path.join(tree, "docs/rabota/roli.md"), encoding="utf-8").read()
    proverka("в описании ролей записан новый способ снять пустые отметки", "POST /api/obzvon/audit/vernut" in roli)


# --- Н. Устаревшее на страницах меню (13.11.2026, вечер 1) --------------------------------

# Выдуманный отчёт «Долгов»: МойКласс на стенде недоступен. Имена и номера — стендовые.
def _dolgi_otchet(den: str = "2026-11-13") -> dict:
    def stroka(n, imya, dolg, data, korzina, vid="абонемент", kto="Лена"):
        return {"invoice": 700 + n, "user_id": 9000 + n, "name": imya, "phone": f"99900010{n:02d}",
                "debt": dolg, "price": dolg, "date": data, "pay_until": data, "overdue": 10 + n,
                "kind": vid, "used": 3, "quota": 8, "manager": kto, "state": 125955, "bucket": korzina}
    rows = [stroka(1, "Тестова Ева", 8400, "2026-11-01", "собрать"),
            stroka(2, "Пробная Алиса", 4200, "2026-10-20", "собрать", kto="Лиза"),
            stroka(3, "Примеров Лев", 1600, "2026-11-05", "собрать", vid="занятия", kto="—"),
            stroka(4, "Образцова Мила", 900, "2025-10-03", "разобрать"),
            stroka(5, "Старшев Глеб", 25000, "2024-11-02", "списать"),
            stroka(6, "Старшева Ника", 5000, "2025-03-15", "списать", kto="—")]
    rows.sort(key=lambda r: -r["debt"])

    def tot(pred):
        sel = [r for r in rows if pred(r)]
        return {"n": len(sel), "sum": round(sum(r["debt"] for r in sel))}
    return {"ready": True, "as_of": den, "rows": rows,
            "nobill": [{"user_id": 9001, "name": "Тестова Ева", "date": "2026-11-10",
                        "class": "2627_ПШ_вт-пт_17:00_5-7 лет_ПШ1 нечитающие (Гр9)"}],
            "totals": {"всего": tot(lambda r: True), "абонементы": tot(lambda r: r["kind"] == "абонемент"),
                       "занятия": tot(lambda r: r["kind"] == "занятия"),
                       "собрать": tot(lambda r: r["bucket"] == "собрать"),
                       "разобрать": tot(lambda r: r["bucket"] == "разобрать"),
                       "списать": tot(lambda r: r["bucket"] == "списать")}}


OTKAZ_TEKST = "Здравствуйте, просьба снять бронь с места, ходить не будем"
# (чат, телефон, кто, когда, текст, как связали, снят)
OTKAZY_STEND = [
    ("9990001001", "9990001001", "", "2026-10-29T14:44:58+03:00", OTKAZ_TEKST, "телефон чата", 0),
    ("5139000001", "9990001001", "Тестова Ева", "2026-10-30T09:10:00+03:00",
     "Не сможем водить Тестову Еву, снимите бронь", "по имени «Тестову Еву» в тексте", 0),   # вторая запись той же семьи
    ("5139000002", "", "", "2026-10-12T18:00:00+03:00", "Отмените запись, пожалуйста", "", 0),  # чат без телефона
    ("79990002001", "9990002001", "Пробная Алиса", "2026-09-20T12:00:00", "попросила по телефону больше не писать", "руками", 0),
    ("9990003002", "9990003002", "", "2026-09-05T10:00:00+03:00", "Мы отказываемся, удалите наш номер", "телефон чата", 0),  # нет в CRM
    ("9990003001", "9990003001", "", "2026-08-24T17:00:40+03:00", "больше не пишите", "телефон чата", 1),  # снят до 13.11
]


def _otkazy_zaseyat(m):
    from app import otkaz
    with m.db.get_conn() as conn:
        otkaz._tables(conn)
        conn.execute("DELETE FROM otkazy")
        conn.executemany("INSERT INTO otkazy (chat, phone, name, ts, text, source, released) VALUES (?,?,?,?,?,?,?)",
                         OTKAZY_STEND)


def _promo_zaseyat(baza: str):
    """Четыре выдуманных контакта промоутеров (тег «Промо: …»): двое записались, один думает, один отказ."""
    lyudi = [(9801, "Листовкина Аня", "79990021001", 125952, "Промо: Маша"),
             (9802, "Листовкин Петя", "79990021002", 146950, "Промо: Маша"),
             (9803, "Раздатков Юра", "79990021003", 125953, "Промо: Руслан"),
             (9804, "Раздаткова Оля", "79990021004", 125957, "Промо: Виталий")]
    c = sqlite3.connect(baza)
    c.executemany("INSERT OR REPLACE INTO users (id, name, phone, client_state_id, raw) VALUES (?,?,?,?,?)",
                  [(i, imya, tel, st, json.dumps({"id": i, "name": imya, "phone": tel, "clientStateId": st,
                                                   "createdAt": "2026-11-05T12:00:00", "advSourceId": 376242,
                                                   "tags": [{"name": teg}]}, ensure_ascii=False))
                   for i, imya, tel, st, teg in lyudi])
    c.commit()
    c.close()


def _osnovnoe(html_text: str) -> str:
    """Содержимое страницы без общей шапки: всё после <main …> (у страниц на base.html)."""
    return html_text.split("<main", 1)[1] if "<main" in html_text else html_text


def _otkazy_staraya_tablica(m) -> bool:
    """Таблица стоп-листа, какой она была до 13.11 (без «кто, когда, почему снял»): первое же
    обращение дополняет её колонками, запись цела, снятие работает."""
    from app import otkaz
    with m.db.get_conn() as conn:
        conn.execute("DROP TABLE IF EXISTS otkazy")
        conn.execute("""CREATE TABLE otkazy (chat TEXT PRIMARY KEY, phone TEXT DEFAULT '', name TEXT DEFAULT '',
                        ts TEXT, text TEXT, source TEXT DEFAULT '', released INTEGER DEFAULT 0)""")
        conn.execute("INSERT INTO otkazy (chat, phone, ts, text, source) VALUES ('9990003003', '9990003003', "
                     "'2026-10-01T10:00:00+03:00', 'снимите бронь, пожалуйста', 'телефон чата')")
    def kolonki():
        with m.db.get_conn() as conn:
            return [r[1] for r in conn.execute("PRAGMA table_info(otkazy)")]
    try:
        do = otkaz.is_refused("9990003003")
        # проверка перед отправкой (is_refused) таблицу не правит: при ошибке она отвечает «отказа
        # нет», и правка таблицы на этом пути могла бы пропустить письмо семье из стоп-листа
        ne_tronuta = "snyal_kto" not in kolonki()
        lenta = otkaz.feed()
        itog = otkaz.snyat(phone="9990003003", prichina="проверка старой таблицы", kto="стенд")
        return bool(do) and ne_tronuta and len(lenta) == 1 and lenta[0]["снял"] == "" and itog["snyato"] == 1 \
            and kolonki()[-3:] == ["snyal_kto", "snyal_kogda", "snyal_prichina"] \
            and otkaz.is_refused("9990003003") is None and otkaz.feed()[0]["снял"] == "стенд"
    finally:
        with m.db.get_conn() as conn:
            conn.execute("DELETE FROM otkazy")


# Сезонные строки «Подбора группы» и последний день, когда каждая ещё на экране (main.SEZON_SROKI).
SEZON_STROKI = [
    ("Английский летний клуб", "2026-08-28"),
    ("Лагерь до 28.08", "2026-08-28"),
    ("29.08 (сб) — праздник открытия сезона", "2026-08-29"),
    ("Было (25/26)", "2026-08-30"),
    ("цена прошлого учебного года", "2026-08-30"),
    ("(было ", "2026-08-30"),
    ("Цена с 01.09", "2026-08-31"),
    ("Абонемент с 01.09", "2026-08-31"),
    ("₽ с 01.09. Первое занятие", "2026-08-31"),
    ("стартуем 31 августа", "2026-08-31"),
    ("31.08–06.09 — Неделя открытых уроков", "2026-09-06"),
    ("1–6 сентября", "2026-09-06"),
    ("Учебный год: 31 августа 2026 — 31 мая 2027", "2027-05-31"),
    ("Последние занятия 25–31 мая", "2027-05-31"),
]


def test_ustarevshee(c, m, tree, baza):
    import asyncio
    import datetime as dt
    import threading
    import time
    from zoneinfo import ZoneInfo
    import app.autopilot as ap
    from app import audit, crmcheck, liniya, otkaz, promo, rules, wazzup
    KLIENT_BEZ_OTKAZA = lt.NOMER_A          # семья из CRM, которой в стоп-листе нет

    msk = ZoneInfo("Europe/Moscow")
    chasy_byli = ap._now

    def den(d: str):
        g, mes, chislo = (int(x) for x in d.split("-"))
        ap._now = lambda: dt.datetime(g, mes, chislo, 12, 0, tzinfo=msk)

    print("Н1. «Подбор группы»: сезонные строки с прошедшими датами не рисуются")
    try:
        stranicy = {}
        for d in ("2026-08-20", "2026-08-28", "2026-08-29", "2026-08-30", "2026-08-31", "2026-09-01",
                  "2026-09-06", "2026-09-07", "2026-11-13", "2027-05-31", "2027-06-01"):
            den(d)
            r = c.get("/enrollment", auth=ADMIN)
            stranicy[d] = _osnovnoe(r.text) if r.status_code == 200 else ""
        proverka("страница открывается в любой из одиннадцати дней", all(stranicy.values()),
                 [d for d, t in stranicy.items() if not t])
        for stroka, srok in SEZON_STROKI:
            ne_tak = [d for d, t in stranicy.items() if (stroka in t) != (d <= srok)]
            proverka(f"«{stroka}» — на экране по {srok[8:]}.{srok[5:7]}.{srok[:4]} включительно, позже нет", not ne_tak, ne_tak)
        noyabr = stranicy["2026-11-13"]
        proverka("13.11: ни праздника, ни Недели открытых уроков, ни «стартуем», ни прошлогодних цен, ни «с 01.09»",
                 not [s for s, srok in SEZON_STROKI if srok < "2026-11-13" and s in noyabr]
                 and "с 01.09" not in noyabr and "30 сентября" not in noyabr,
                 [s for s, srok in SEZON_STROKI if srok < "2026-11-13" and s in noyabr])
        for d, t in stranicy.items():
            proverka(f"{d}: «Первое занятие условно-бесплатное» на месте — в памятке, в текстах клиенту и в калькуляторе",
                     "<b>Первое занятие условно-бесплатное:</b> не понравилось — клиент не платит" in t
                     and t.count("условно-бесплатное") >= 4 and "Слов «бесплатное пробное» не используем" in t,
                     t.count("условно-бесплатное"))
        proverka("слова «бесплатное пробное» на странице — только в запрете на них",
                 all(t.count("бесплатное пробное") == 1 for t in stranicy.values()),
                 {d: t.count("бесплатное пробное") for d, t in stranicy.items()})
        proverka("мини-сад: «Первый день условно-бесплатный» — текст не тронут",
                 "Первый день условно-бесплатный: не понравится — ничего не платите" in noyabr)
        proverka("13.11: цены на месте — одна колонка «Цена», 8 400 ₽ за английский и подготовку к школе",
                 '<th class="num">Цена</th>' in noyabr and noyabr.count("8 400 ₽") >= 4 and "Сверено 14.08.2026." in noyabr
                 and "<th>Позиция</th>" in noyabr and "Разница" not in noyabr)
        proverka("13.11: текст клиенту у группы — цена без «с 01.09», формулировка первого занятия прежняя",
                 "Абонемент 8 занятий — 8400 ₽. Первое занятие условно-бесплатное: не понравится — платить не нужно, "
                 "понравится — оно входит в этот абонемент." in noyabr, noyabr.count("Абонемент 8 занятий"))
        proverka("13.11: в калькуляторе летнего клуба нет, остальные предметы на месте",
                 'data-name="Английский летний клуб"' not in noyabr and 'data-name="Английский язык"' in noyabr
                 and 'data-name="Английский летний клуб"' in stranicy["2026-08-20"])
        proverka("20.08 страница та же, что была до правки: все сезонные строки на экране",
                 all(s in stranicy["2026-08-20"] for s, _ in SEZON_STROKI),
                 [s for s, _ in SEZON_STROKI if s not in stranicy["2026-08-20"]])
        den("2026-11-13")
        r = c.get("/enrollment?course=" + quote("Английский летний клуб"), auth=ADMIN)
        proverka("13.11: фильтр по летнему клубу прайс лагеря не возвращает", r.status_code == 200 and "Лагерь до 28.08" not in r.text)
        shablon = open(os.path.join(tree, "app/templates/enrollment.html"), encoding="utf-8").read()
        v_shablone = set(re.findall(r"sezon\.([a-z_0-9]+)", shablon))
        proverka("у каждого срока main.SEZON_SROKI есть строка на странице и наоборот (летний клуб — в обработчике)",
                 v_shablone | {"letnij_klub"} == set(m.SEZON_SROKI), sorted(v_shablone ^ set(m.SEZON_SROKI)))
        proverka("PRICES не тронут: летний клуб остался в прайсе для автопилота и «Линии»", "Английский летний клуб" in m.PRICES)
    finally:
        ap._now = chasy_byli

    print("Н2. «Итоги промоутеров»: расчёты и авансы — только владельцу, с пометкой")
    _promo_zaseyat(baza)
    POMETKA = "Расчёт по правилам 17.08, сейчас не действует — ждёт нового правила оплаты промоутеров."
    ra, rv = c.get("/promo", auth=ADMIN), c.get("/promo", auth=VLADELEC)
    a, v = _osnovnoe(ra.text), _osnovnoe(rv.text)
    proverka("страница открывается у админа и у владельца", (ra.status_code, rv.status_code) == (200, 200))
    dengi = ("Расчёты с промоутерами", "Правила оплаты", "Начислено", "Оплачено", "аванс", "доплатить", "23 200", "3 400",
             "₽", "id=\"raschety\"", "×600", "×200")
    proverka("админ: блока «Расчёты» нет, в HTML ни ставок, ни начислений, ни авансов, ни одного знака ₽",
             not [x for x in dengi if x in a], [x for x in dengi if x in a])
    proverka("админ: остальное на месте — график, воронка по промоутерам, весь канал, сбор по дням",
             "Эффективность промоутеров" in a and 'href="/promo/grafik"' in a and "<h3>Промо: Маша</h3>" in a
             and "Весь промо-канал" in a and "Сбор контактов по дням" in a and "Подтверждено" in a
             and "Обзвонено / дозвон" in a and 'id="p-upd"' in a)
    proverka("владелец: блок есть, пометка — дословно, блок помечен «только владельцу»",
             'id="raschety"' in v and POMETKA in v and "Блок виден только владельцу." in v
             and v.index("Расчёты с промоутерами") < v.index(POMETKA) < v.index("Правила оплаты"))
    proverka("владелец: расчёт тот же, что был — Маша: 1 подтв.×600 + 0 в базе×200 + 4 ч×400 = 2 200 ₽ при авансе 3 400 ₽",
             "1 подтв.×600 + 0 в базе×200 + 4 ч×400" in v and "<b>2 200 ₽</b>" in v and "<td>3 400 ₽</td>" in v
             and "аванс не отработан на 1 200 ₽" in v and "<td>23 200 ₽</td>" in v and "✅ Подтверждено (×600 ₽)" in v)
    bez, s = promo.stats(raschety=False), promo.stats()
    proverka("promo.stats(raschety=False): денег нет в самих данных — ни pay_groups, ни total_fee, ни rates; остальное то же",
             not {"pay_groups", "total_fee", "rates"} & set(bez) and {"pay_groups", "total_fee", "rates"} <= set(s)
             and {k: v2 for k, v2 in s.items() if k not in ("pay_groups", "total_fee", "rates")} == bez)
    proverka("обновление страницы раз в 3 минуты берёт ту же страницу той же роли (fetch(location.href))",
             "fetch(location.href" in a and "fetch(location.href" in v)
    rg = c.get("/promo/grafik", auth=ADMIN)
    proverka("«Промоутеры» /promo/grafik открывается как раньше, расчётов на ней нет",
             rg.status_code == 200 and "Расчёты с промоутерами" not in rg.text and "аванс" not in rg.text.lower())
    proverka("/promo без входа → 401", c.get("/promo").status_code == 401)

    print("Н3. «Долги»: снимок на 15 минут в отдельном потоке, кнопка «обновить сейчас»")
    nast = crmcheck.debts_report
    zvali = []                     # (в потоке без цикла событий?, имя потока)
    rezhim = {"oshibka": False, "pauza": 0.0}

    def _otchet():
        try:
            asyncio.get_running_loop()
            v_potoke = False
        except RuntimeError:
            v_potoke = True
        zvali.append((v_potoke, threading.current_thread().name))
        if rezhim["pauza"]:
            time.sleep(rezhim["pauza"])
        if rezhim["oshibka"]:
            raise ConnectionError("стенд: МойКласс не отвечает")
        return _dolgi_otchet()

    def sostarit(minut):
        snimok = json.loads(m.db.get_state(m.DOLGI_KLYUCH))
        snimok["ts"] = (dt.datetime.now() - dt.timedelta(minutes=minut)).isoformat()
        m.db.set_state(m.DOLGI_KLYUCH, json.dumps(snimok, ensure_ascii=False))

    m.db.set_state(m.DOLGI_KLYUCH, "")
    r = c.get("/dolgi", auth=ADMIN)
    proverka("ключа МойКласса нет — «Нет ключа МойКласс», как раньше; снимок не сохраняется",
             r.status_code == 200 and "Нет ключа МойКласс" in r.text and not m.db.get_state(m.DOLGI_KLYUCH))
    proverka("…и /api/dolgi отвечает {\"ready\": false}, как раньше", c.get("/api/dolgi", auth=ADMIN).json() == {"ready": False})
    crmcheck.debts_report = _otchet
    try:
        r1 = c.get("/dolgi", auth=ADMIN)
        r2 = c.get("/dolgi", auth=VLADELEC)
        proverka("первое открытие считает отчёт, второе берёт снимок: МойКласс спрошен один раз",
                 (r1.status_code, r2.status_code) == (200, 200) and len(zvali) == 1, len(zvali))
        proverka("расчёт идёт в отдельном потоке, а не в цикле событий сервера (раньше сервер на это время замирал)",
                 zvali and zvali[0][0], zvali[:1])
        proverka("обработчики «Долгов» — async с run_in_threadpool, прямого вызова crmcheck.debts_report() в них нет",
                 all("run_in_threadpool(_dolgi_snimok" in inspect.getsource(f) and "debts_report" not in inspect.getsource(f)
                     for f in (m.money_page, m.api_dolgi, m.api_dolgi_obnovit)))
        t = _osnovnoe(r1.text)
        proverka("на странице: «Снимок из МойКласса на ДД.ММ ЧЧ:ММ», «не чаще раза в 15 минут», кнопка «обновить сейчас»",
                 re.search(r'Снимок из МойКласса на <b id="snimok-ot">\d\d\.\d\d \d\d:\d\d</b>', t) is not None
                 and "15 минут" in t and '<button type="button" id="obn">обновить сейчас</button>' in t
                 and "при каждом открытии" not in t and "fetch('/api/dolgi/obnovit', {method: 'POST'})" in t, t[:300])
        j = c.get("/api/dolgi", auth=ADMIN).json()
        proverka("/api/dolgi отдаёт тот же снимок: ключи прежние (ready, as_of, rows, nobill, totals) и «snimok»",
                 len(zvali) == 1 and {"ready", "as_of", "rows", "nobill", "totals", "snimok", "snimok_oshibka"} == set(j)
                 and j["totals"] == _dolgi_otchet()["totals"] and len(j["rows"]) == 6 and j["snimok_oshibka"] == "", sorted(j))
        proverka("время снимка — московское, что бы ни показывали часы сервера (и на странице, и в /api/dolgi)",
                 j["snimok"].endswith("+03:00") and abs((dt.datetime.fromisoformat(j["snimok"])
                                                         - dt.datetime.now(dt.timezone.utc)).total_seconds()) < 120
                 and dt.datetime.fromisoformat(j["snimok"]).strftime("%d.%m %H:%M") in t, j["snimok"])
        c.get("/dolgi?fresh=1", auth=ADMIN)
        c.get("/dolgi?refresh=1", auth=ADMIN)
        proverka("GET с ?fresh=1 или ?refresh=1 пересчёт не запускает — только кнопка (POST)", len(zvali) == 1, len(zvali))
        proverka("POST /api/dolgi/obnovit с чужого сайта — 403, пересчёта нет; без входа — 401",
                 c.post("/api/dolgi/obnovit", auth=ADMIN, headers=CHUZHOY_FON).status_code == 403
                 and c.post("/api/dolgi/obnovit").status_code == 401 and len(zvali) == 1)
        r = c.post("/api/dolgi/obnovit", auth=ADMIN, headers=SVOYA_STRANICA)
        proverka("«обновить сейчас» пересчитывает сразу, не дожидаясь 15 минут — и тоже в потоке",
                 r.status_code == 200 and r.json()["ok"] is True and r.json()["pereschitan"] is True and len(zvali) == 2
                 and zvali[1][0] and r.json()["vsego"] == {"n": 6, "sum": 45100}, r.text[:200])
        sostarit(14)
        c.get("/dolgi", auth=ADMIN)
        proverka("снимку 14 минут — открытие страницы МойКласс не трогает", len(zvali) == 2, len(zvali))
        sostarit(16)
        r = c.get("/dolgi", auth=ADMIN)
        proverka("снимку 16 минут — открытие страницы пересчитывает", r.status_code == 200 and len(zvali) == 3, len(zvali))
        rezhim["oshibka"] = True
        sostarit(16)
        r = c.get("/dolgi", auth=ADMIN)
        proverka("МойКласс не ответил — страница жива: прежний снимок и пометка, а не ошибка 500",
                 r.status_code == 200 and 'id="snimok-oshibka"' in r.text and "МойКласс не ответил" in r.text
                 and "показан прежний снимок" in r.text and "Собрать — 3 на" in r.text, r.status_code)
        j = c.get("/api/dolgi", auth=ADMIN).json()
        proverka("…и /api/dolgi отдаёт прежний снимок с «snimok_oshibka»", j.get("ready") is True and "МойКласс не ответил" in j["snimok_oshibka"])
        r = c.post("/api/dolgi/obnovit", auth=ADMIN, headers=SVOYA_STRANICA)
        proverka("…кнопка честно отвечает ok=false с причиной", r.status_code == 200 and r.json()["ok"] is False and r.json()["oshibka"])
        m.db.set_state(m.DOLGI_KLYUCH, "")
        r = c.get("/dolgi", auth=ADMIN)
        proverka("МойКласс не ответил и снимка ещё нет — страница с объяснением (200), /api/dolgi — 502",
                 r.status_code == 200 and "снимка долгов ещё нет" in r.text and c.get("/api/dolgi", auth=ADMIN).status_code == 502)
        rezhim["oshibka"] = False

        # двое открыли страницу одновременно — МойКласс спрошен один раз
        m.db.set_state(m.DOLGI_KLYUCH, "")
        rezhim["pauza"] = 0.4
        bylo = len(zvali)
        itogi = []
        potoki = [threading.Thread(target=lambda: itogi.append(m._dolgi_snimok())) for _ in range(3)]
        for p in potoki:
            p.start()
        for p in potoki:
            p.join(10)
        rezhim["pauza"] = 0.0
        proverka("трое открыли одновременно — расчёт один, снимок у всех один и тот же",
                 len(zvali) - bylo == 1 and len(itogi) == 3 and len({x["ts"] for x in itogi}) == 1
                 and sorted(x["pereschitan"] for x in itogi) == [False, False, True], (len(zvali) - bylo, len(itogi)))

        print("Н3в. «Долги»: старые счета — отдельным свёрнутым блоком")
        t = _osnovnoe(c.get("/dolgi", auth=ADMIN).text)
        blok = t.split('<details id="starye-scheta">', 1)[-1].split("</details>", 1)[0] if '<details id="starye-scheta">' in t else ""
        vne = t.replace(blok, "")
        proverka("блок «Старые счета — ждут решения владельца — 2 на 30 000 ₽» есть и свёрнут",
                 "Старые счета — ждут решения владельца — 2 на 30 000 ₽</summary>" in blok
                 and "<details id=\"starye-scheta\" open" not in t)
        proverka("в блоке — оба счёта прошлых лет, вне блока их нет; раздела «Списать» больше нет",
                 "Старшев Глеб" in blok and "Старшева Ника" in blok and "счёт от 2024-11-02" in blok
                 and "Старшев Глеб" not in vne and "Старшева Ника" not in vne and "Списать —" not in t)
        proverka("«Собрать» и «Разобрать руками» — как были, и стоят выше блока старых счетов",
                 "Собрать — 3 на 14 200 ₽" in vne and "Разобрать руками — 1 на 900 ₽" in vne and "Занятия без счёта — 1" in vne
                 and t.index("Собрать — 3") < t.index("Разобрать руками — 1") < t.index("Занятия без счёта") < t.index('<details id="starye-scheta">'))
        proverka("в блоке сказано, чьё это решение, и что админам с этими счетами делать нечего",
                 "Списывать или взыскивать — решает владелец" in blok and "выставленные до 1 сентября 2025 года" in blok
                 and "ничего делать не нужно" in blok)
        proverka("наверху: «всего» по-прежнему считает все счета, старые — отдельной карточкой",
                 "<b>45 100 ₽</b><span>всего, 6 счетов</span>" in t
                 and "<b>30 000 ₽</b><span>старые счета — 2, ждут решения владельца</span>" in t
                 and "<b>14 200 ₽</b><span>можно собрать</span>" in t)

        print("Н3б. «Долги» и утренняя проверка денег: только показ, автопилот не тронут")
        bylo_krasnyh = m._dolgi_krasnyh()
        for i in range(2):
            audit.add_flag("деньги", f"sub-unpaid:stend-n3:{i}", audit.HIGH, "Абонемент не оплачен (стенд)",
                           "выдуманная находка", user_id=9001, manager_id=202856)
        audit.add_flag("честность", "chest:stend-n3", audit.HIGH, "Правка оплаченного (стенд)", "выдуманная находка", user_id=9001)
        t = _osnovnoe(c.get("/dolgi", auth=ADMIN).text)
        stroka = t.split('id="proverka-deneg"', 1)[-1].split("</p>", 1)[0]
        proverka("на «Долгах» строка об утренней проверке денег: разделы 4, 7 и 13 «Проверки МойКласса» и число красных "
                 "находок вида «деньги» за 14 дней (сверка честности в него не входит)",
                 'href="/proverka-mk#r4"' in stroka and 'href="/proverka-mk#r7"' in stroka and 'href="/proverka-mk#r13"' in stroka
                 and bylo_krasnyh is not None and f"(сейчас {bylo_krasnyh + 2})" in stroka
                 and "пунктов и сообщений со ссылкой на эту страницу не ставит" in stroka, (bylo_krasnyh, stroka[-200:]))
        proverka("то же число — в разделе 13 «Проверки МойКласса» у админа",
                 c.get("/api/proverka-mk", auth=ADMIN).json()["krasnye"]["po_vidam"].get("деньги") == bylo_krasnyh + 2)
        proverka("rules.open_flags отдаёт только вид «правила» — утренняя проверка денег по-прежнему никому ничего не ставит",
                 not [f for f in rules.open_flags(300) if f.get("kind") == "деньги"]
                 and '_rules.open_flags(300) if f.get("kind") == "деньги"' in inspect.getsource(ap.money_check))
        with m.db.get_conn() as conn:
            conn.execute("DELETE FROM audit_flags WHERE key LIKE '%stend-n3%'")
    finally:
        crmcheck.debts_report = nast
        m.db.set_state(m.DOLGI_KLYUCH, "")

    print("Н4. «Стоп-лист»: кнопка «снять» с причиной; снятая запись не удаляется и перестаёт действовать")
    _otkazy_zaseyat(m)
    semya, vtoraya = SEMYA[-10:], "9990002001"

    def stroki(gde="1=1", arg=()):
        with m.db.get_conn() as conn:
            return [dict(r) for r in conn.execute(
                f"SELECT rowid AS id, chat, phone, released, snyal_kto, snyal_kogda, snyal_prichina FROM otkazy WHERE {gde} ORDER BY ts",
                arg)]

    def ujdet(nomer):
        return c.get(f"/api/otkazy/check?phone={nomer}", auth=ADMIN).json()

    def plashka_linii(nomer):
        t = c.get(f"/liniya?phone={nomer}", auth=ADMIN).text
        return t.split('id="ln-stop-list"', 1)[1].split("</div>", 1)[0] if 'id="ln-stop-list"' in t else ""

    t = c.get("/otkazy", auth=ADMIN).text
    # 13.11.2026, вечер (проверка «вечера 1»): заголовок считает семьи, а не записи — у выдуманной
    # семьи две записи (с телефона и из чата мессенджера), всего 5 записей у 4 семей; число
    # записей названо рядом. Было «Стоп-лист: 5 семей».
    proverka("страница: 5 действующих записей у 4 семей, у каждой записи — «снять» с полем причины; снятая — в свёрнутом «Сняты — 1»",
             "Стоп-лист: 4 семьи, которым автоматика не пишет" in t and "Записей — 5" in t
             and t.count("<details class=snyat>") == 5 and t.count("name=prichina required") == 5
             and "<details id=snyatye><summary>Сняты — 1</summary>" in t and "причина не записана" in t
             and "<details id=snyatye open" not in t, t.count("<details class=snyat>"))
    proverka("даты — «29.10.2026 14:44», а не «2026-10-29T14:44»; на телефоне запись идёт карточкой — кнопка «снять» не за краем экрана",
             "29.10.2026 14:44" in t and "2026-10-29T14:44" not in t and "@media (max-width:700px)" in t
             and ".tabl table,.tabl tbody,.tabl tr,.tabl td{display:block" in t)
    # 13.11.2026, вечер (проверка «вечера 1»): у чата без телефона кнопка шлёт ключ записи (chat), а не
    # её номер (rowid) — номера меняются при пересборке списка, и старая вкладка сняла бы чужую семью.
    proverka("кнопка шлёт телефон семьи, у чата без телефона — ключ записи (chat), не номер; старой подсказки про запрос в адресной строке нет",
             t.count(f'data-phone="{semya}"') == 2 and t.count('data-chat="5139000002"') == 1 and "data-id=" not in t
             and "telo.chat = f.dataset.chat" in t and "fetch('/api/otkazy/snyat'" in t
             and "/api/otkazy/release" not in t)
    proverka("до снятия: предохранитель отправок не пускает письмо семье — «клиент отказался»",
             ujdet(SEMYA)["уйдёт"] is False and "клиент отказался" in ujdet(SEMYA)["причина"]
             and otkaz.is_refused(SEMYA) and "клиент отказался" in (wazzup.guard(SEMYA, "проверка", kind="nabor", transport="whatsapp") or ""))
    bez_otkaza = ujdet(KLIENT_BEZ_OTKAZA)

    print("Н5. «Линия»: семья в стоп-листе — плашка с причиной и датой")
    pl = plashka_linii(semya)
    proverka("семья в стоп-листе: плашка «В стоп-листе с 30.10.2026: «…»» (последняя запись семьи) и ссылка на стоп-лист",
             "В стоп-листе с 30.10.2026" in pl and "Не сможем водить Тестову Еву, снимите бронь" in pl and 'href="/otkazy"' in pl
             and "Автоматика этой семье не пишет — только живые ответы администратора" in pl, pl[:200])
    proverka("плашка — в карточке «Кто на линии», красной полосой (класс ln-stop), до списка детей",
             (lambda s: 'class="ln-stop" id="ln-stop-list"' in s and s.index("Кто на линии") < s.index('id="ln-stop-list"') < s.index("Тестова Ева"))(
                 c.get(f"/liniya?phone={semya}", auth=ADMIN).text))
    proverka("номера нет в CRM, но он в стоп-листе — плашка всё равно есть",
             "В стоп-листе с 05.09.2026" in plashka_linii("9990003002") and "нет в CRM" in c.get("/liniya?phone=9990003002", auth=ADMIN).text)
    proverka("запись снята ещё до 13.11 — плашки нет; семьи нет в стоп-листе — плашки нет",
             not plashka_linii("9990003001") and not plashka_linii(KLIENT_BEZ_OTKAZA[-10:]))
    stop_crm = c.get(f"/liniya?phone={lt.STOP[-10:]}", auth=ADMIN).text
    proverka("прежняя плашка «Статус в CRM …: сами не звоним и не пишем» — как была, и это другая плашка",
             "⛔ Статус в CRM «" in stop_crm and 'id="ln-stop-list"' not in stop_crm)
    proverka("/api/liniya отдаёт то же: stop_list = {kogda, prichina, kak}",
             c.get(f"/api/liniya?phone={semya}", auth=ADMIN).json().get("stop_list", {}).get("kogda") == "30.10.2026"
             and c.get(f"/api/liniya?phone={KLIENT_BEZ_OTKAZA[-10:]}", auth=ADMIN).json().get("stop_list") is None)
    proverka("плашку собирает только чтение: в «Линии» стоп-лист читается соединением страницы, таблицу не создаёт",
             "otkaz.v_stop_liste(p, conn)" in inspect.getsource(liniya._semya))

    print("Н4 (продолжение). Снятие")
    bylo = stroki()
    for telo, chto in (({"phone": SEMYA}, "без причины"), ({"phone": SEMYA, "prichina": "  "}, "с пустой причиной"),
                       ({"phone": SEMYA, "prichina": "ок"}, "с причиной из двух букв"),
                       ({"prichina": "мама передумала"}, "без телефона и номера записи"),
                       ({"phone": "123", "prichina": "мама передумала"}, "с обрывком телефона"),
                       ({"phone": "79990009999", "prichina": "мама передумала"}, "по номеру, которого в стоп-листе нет"),
                       ({"id": 987654, "prichina": "мама передумала"}, "по номеру записи, которой нет")):
        r = c.post("/api/otkazy/snyat", json=telo, auth=ADMIN, headers=SVOYA_STRANICA)
        proverka(f"снять {chto} — 400, ничего не снято", r.status_code == 400 and stroki() == bylo, (r.status_code, r.text[:120]))
    proverka("без входа — 401, с чужого сайта — 403, ничего не снято",
             c.post("/api/otkazy/snyat", json={"phone": SEMYA, "prichina": "мама передумала"}).status_code == 401
             and c.post("/api/otkazy/snyat", json={"phone": SEMYA, "prichina": "мама передумала"}, auth=ADMIN,
                        headers=CHUZHOY_FON).status_code == 403 and stroki() == bylo)
    r = c.post("/api/otkazy/snyat", json={"phone": "+7 (999) 000-10-01", "prichina": "  мама написала 12.11,   что передумали  "},
               auth=ADMIN, headers=SVOYA_STRANICA)
    j = r.json() if r.status_code == 200 else {}
    posle = stroki("phone=?", (semya,))
    proverka("снять по телефону с причиной — 200; сняты обе записи семьи (с телефона и из чата мессенджера)",
             r.status_code == 200 and j.get("ok") is True and j.get("snyato") == 2 and j.get("eshche_dejstvuet") is None, r.text[:200])
    proverka("записи не удалены: помечены снятыми — кто («админ» под общим входом), когда, причина (пробелы убраны)",
             len(stroki()) == len(bylo) and len(posle) == 2
             and all(x["released"] == 1 and x["snyal_kto"] == "админ" and x["snyal_prichina"] == "мама написала 12.11, что передумали"
                     and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+03:00", x["snyal_kogda"]) for x in posle), posle)
    proverka("после снятия: is_refused молчит, предохранитель отправок отвечает так же, как семье без отказа",
             otkaz.is_refused(SEMYA) is None and ujdet(SEMYA)["уйдёт"] == bez_otkaza["уйдёт"]
             and "клиент отказался" not in (ujdet(SEMYA)["причина"] or "")
             and "клиент отказался" not in (wazzup.guard(SEMYA, "проверка", kind="nabor", transport="whatsapp") or ""),
             (ujdet(SEMYA), bez_otkaza))
    proverka("…и плашки на «Линии» у этой семьи больше нет", not plashka_linii(semya)
             and c.get(f"/api/liniya?phone={semya}", auth=ADMIN).json().get("stop_list") is None)
    proverka("неснятые действуют как действовали: вторая семья и номер не из CRM по-прежнему под запретом, плашка на месте",
             otkaz.is_refused(vtoraya) and ujdet("7" + vtoraya)["уйдёт"] is False and "клиент отказался" in ujdet("7" + vtoraya)["причина"]
             and otkaz.is_refused("9990003002") and plashka_linii("9990003002")
             and [x["released"] for x in stroki("phone IN (?, ?) OR chat='5139000002'", (vtoraya, "9990003002"))] == [0, 0, 0])
    r = c.post("/api/otkazy/snyat", json={"phone": SEMYA, "prichina": "второй раз"}, auth=VLADELEC, headers=SVOYA_STRANICA)
    proverka("повторное снятие той же семьи — 400; автор и причина первого снятия не затёрты",
             r.status_code == 400 and stroki("phone=?", (semya,)) == posle, r.status_code)
    # 13.11.2026, вечер (проверка «вечера 1», два проверяющих): было «под общим входом имя берётся
    # как прислано» — владелец под прежним паролем записывал в «Кто снял» «Лена», админ — «Борис».
    # Теперь под общим входом имя из запроса не читается: подпись по роли. Чат без телефона
    # снимается по ключу записи (chat); номер записи (id) — только если он всё ещё этой записи.
    bez_tel = stroki("chat='5139000002'")[0]["id"]
    r = c.post("/api/otkazy/snyat", json={"chat": "5139000002", "id": bez_tel + 1000, "prichina": "чат оказался педагога, не клиента"},
               auth=VLADELEC, headers=SVOYA_STRANICA)
    proverka("номер записи не совпал с её ключом (список пересобрали, пока вкладка была открыта) — 400, ничего не снято",
             r.status_code == 400 and "обновите" in r.text and stroki("chat='5139000002'")[0]["released"] == 0, r.text[:200])
    r = c.post("/api/otkazy/snyat", json={"chat": "5139000002", "prichina": "чат оказался педагога, не клиента", "kto": "Лена"},
               auth=VLADELEC, headers=SVOYA_STRANICA)
    proverka("чат без телефона снимается по ключу записи; под общим входом присланное имя не берётся — подпись по роли",
             r.status_code == 200 and r.json()["snyato"] == 1 and r.json()["phone"] == "" and r.json()["kto"] == "Борис"
             and stroki("chat='5139000002'")[0]["snyal_kto"] == "Борис", r.text[:200])
    with m.db.get_conn() as conn:
        conn.execute("INSERT INTO otkazy (chat, phone, ts, text, source) VALUES ('5139000007', '', "
                     "'2026-11-02T10:00:00+03:00', 'отмените запись', '')")
    r = c.post("/api/otkazy/snyat", json={"id": stroki("chat='5139000007'")[0]["id"], "prichina": "проверка номера записи", "kto": "Борис"},
               auth=ADMIN, headers=SVOYA_STRANICA)
    proverka("по номеру записи (запрос руками) снять по-прежнему можно; админу под общим входом не подписаться «Борисом»",
             r.status_code == 200 and r.json()["snyato"] == 1 and r.json()["kto"] == "админ"
             and stroki("chat='5139000007'")[0]["snyal_kto"] == "админ", r.text[:200])
    with m.db.get_conn() as conn:
        conn.execute("DELETE FROM otkazy WHERE chat='5139000007'")
    nast_podpis = m.vhod.podpis
    m.vhod.podpis = lambda request, ot_imeni="": "Лиза"          # личный вход: vhod.podpis всегда отдаёт имя вошедшего
    try:
        r = c.post("/api/otkazy/snyat", json={"phone": "9990003002", "prichina": "позвонили сами, просят писать", "kto": "Борис"},
                   auth=ADMIN, headers=SVOYA_STRANICA)
    finally:
        m.vhod.podpis = nast_podpis
    proverka("автор — с сервера (vhod.podpis): вошедшей Лизе не подписаться «Борисом»",
             r.status_code == 200 and r.json()["kto"] == "Лиза" and stroki("phone='9990003002'")[0]["snyal_kto"] == "Лиза"
             and "vhod.podpis(request" in inspect.getsource(m._otkazy_kto)
             and "_otkazy_kto(request" in inspect.getsource(m.api_otkazy_snyat), r.text[:200])
    with m.db.get_conn() as conn:
        conn.execute("INSERT INTO otkazy (chat, phone, ts, text, source) VALUES ('9990003004', '9990003004', "
                     "'2026-11-01T10:00:00+03:00', 'отмените запись', 'телефон чата')")
    r = c.post("/api/otkazy/snyat", json={"phone": "9990003004", "prichina": "проверка владельца"}, auth=VLADELEC, headers=SVOYA_STRANICA)
    proverka("владелец под общим входом, имя не прислано — подпись «Борис»",
             r.status_code == 200 and r.json()["kto"] == "Борис" and stroki("phone='9990003004'")[0]["snyal_kto"] == "Борис", r.text[:200])
    with m.db.get_conn() as conn:
        conn.execute("DELETE FROM otkazy WHERE chat='9990003004'")

    print("Н4 (продолжение). Старый адрес для рутин и страница после снятия")
    r = c.post(f"/api/otkazy/release?chat=7{vtoraya}", auth=VLADELEC, headers=SVOYA_STRANICA)
    x = stroki("phone=?", (vtoraya,))[0]
    proverka("POST /api/otkazy/release?chat=… работает как работал (причину не спрашивает) и теперь пишет, кто и когда снял",
             r.status_code == 200 and r.json() == {"ok": True, "чат": "7" + vtoraya, "статус": "снят со стоп-листа"}
             and x["released"] == 1 and x["snyal_kto"] == "Борис" and x["snyal_kogda"] and x["snyal_prichina"] == ""
             and otkaz.is_refused(vtoraya) is None, x)
    c.post(f"/api/otkazy/release?chat={semya}", auth=VLADELEC, headers=SVOYA_STRANICA)
    proverka("…а уже снятую запись старый адрес не переписывает: автор и причина первого снятия целы",
             stroki("phone=?", (semya,)) == posle)
    stranica = c.get("/otkazy", auth=ADMIN).text
    snyatye = stranica.split("<details id=snyatye>", 1)[-1].split("</details>", 1)[0]
    proverka("страница после снятия: действующих нет («Пока пусто»), «Сняты — 6» свёрнут; внутри — кто, когда и почему",
             "Стоп-лист: 0 семей" in stranica and "Пока пусто" in stranica and "<summary>Сняты — 6</summary>" in stranica
             and "мама написала 12.11, что передумали" in snyatye and "админ<br>" in snyatye and "Лиза<br>" in snyatye
             and "чат оказался педагога, не клиента" in snyatye and re.search(r"админ<br>\d\d\.\d\d\.\d{4} \d\d:\d\d", snyatye) is not None
             and snyatye.count("причина не записана") == 2 and "дата не записана" in snyatye
             and snyatye.count("вернуть в стоп-лист</button>") == 6, snyatye[:300])
    lenta = c.get("/api/otkazy", auth=ADMIN).json()["отказов"]
    proverka("/api/otkazy: прежние ключи на месте, добавлены id записи и «кто, когда, почему снял»",
             len(lenta) == 6 and {"чат", "телефон", "кто", "когда", "цитата", "как связали", "снят"} <= set(lenta[0])
             and {"id", "снял", "снят когда", "причина снятия"} <= set(lenta[0]) and all(z["снят"] for z in lenta))
    r = c.post(f"/api/otkazy/add?phone={SEMYA}&text=" + quote("по телефону попросила больше не писать"), auth=ADMIN, headers=SVOYA_STRANICA)
    proverka("вернуть семью в стоп-лист руками (POST /api/otkazy/add) можно: запрет и плашка «Линии» снова действуют",
             r.status_code == 200 and r.json()["ok"] is True and otkaz.is_refused(SEMYA) and "В стоп-листе" in plashka_linii(semya)
             and "Стоп-лист: 1 семья, которой автоматика не пишет" in c.get("/otkazy", auth=ADMIN).text)
    with m.db.get_conn() as conn:
        conn.execute("DELETE FROM otkazy")
    proverka("таблица стоп-листа старого вида (без новых колонок) дополняется сама при открытии страницы или снятии, записи "
             "целы; проверка перед отправкой (is_refused) таблицу не правит и работает на старой",
             _otkazy_staraya_tablica(m))
    proverka("is_refused и _tables — как были: новых колонок не читают и не создают",
             "snyal" not in inspect.getsource(otkaz.is_refused) and "ALTER" not in inspect.getsource(otkaz._tables))


# --- П. После проверки «вечера 1» (13.11.2026, вечер) -------------------------------------
# Замечания трёх проверяющих: стоп-лист (повторный отказ после снятия, «вернуть», подпись,
# ключ записи), снятие чужих находок запросом, отметка ключа вебхука Wazzup, ключ Марквиза,
# старые долги в разделе 13 «Проверки МойКласса».

def test_posle_proverki_vechera1(c, m, tree, baza):
    import datetime as dt
    import logging
    from app import audit, otkaz, proverka_mk, wazzup as wz

    def zapis(chat):
        with m.db.get_conn() as conn:
            r = conn.execute("SELECT rowid AS id, * FROM otkazy WHERE chat=?", (chat,)).fetchone()
            return dict(r) if r else None

    def ujdet(nomer):
        return c.get(f"/api/otkazy/check?phone={nomer}", auth=ADMIN).json()

    print("П1. Стоп-лист: повторный письменный отказ после снятия возвращает запись в действие")
    _otkazy_zaseyat(m)
    semya = SEMYA[-10:]
    NOVYY = "Пожалуйста, снимите бронь и больше не пишите нам"
    r = c.post("/api/otkazy/snyat", json={"phone": SEMYA, "prichina": "мама написала, что передумали"},
               auth=ADMIN, headers=SVOYA_STRANICA)
    snyato = zapis(semya)
    proverka("семью сняли кнопкой: запрета нет", r.status_code == 200 and snyato["released"] == 1 and otkaz.is_refused(SEMYA) is None)
    proverka("то же старое сообщение (разбор входящих перечитывает месяц) снятую запись не возвращает",
             otkaz.note(SEMYA, OTKAZ_TEKST, "2026-10-29T14:44:58+03:00") is False
             and zapis(semya)["released"] == 1 and otkaz.is_refused(SEMYA) is None)
    pozzhe = (dt.datetime.fromisoformat(snyato["snyal_kogda"]) + dt.timedelta(minutes=5)).isoformat(timespec="seconds")
    vernulas = otkaz.note(SEMYA, NOVYY, pozzhe)
    z = zapis(semya)
    proverka("новый письменный отказ после снятия → запись снова действует (до правки note() отвечала False, и автоматика писала дальше)",
             vernulas is True and z["released"] == 0 and z["ts"] == pozzhe and z["text"] == NOVYY, z)
    proverka("…is_refused снова запрещает, предохранитель отправок письмо не пускает, плашка на «Линии» вернулась",
             "больше не пишите" in (otkaz.is_refused(SEMYA) or "") and ujdet(SEMYA)["уйдёт"] is False
             and "клиент отказался" in ujdet(SEMYA)["причина"]
             and 'id="ln-stop-list"' in c.get(f"/liniya?phone={semya}", auth=ADMIN).text)
    proverka("«кто, когда, почему снял» у вернувшейся записи целы; вторая запись семьи (чат мессенджера) осталась снятой",
             z["snyal_kto"] == "админ" and z["snyal_kogda"] == snyato["snyal_kogda"]
             and z["snyal_prichina"] == "мама написала, что передумали" and zapis("5139000001")["released"] == 1, z)
    proverka("действующую запись повтор отказа не трогает — как и раньше",
             otkaz.note(SEMYA, NOVYY + "!", pozzhe) is False and zapis(semya)["text"] == NOVYY)
    # 13.11.2026, вторая проверка «вечера 1»: у записи, снятой до 13.11, времени снятия нет.
    # Второе «отказное» сообщение семьи, пришедшее ещё до снятия (через 5 минут после первого),
    # при повторном разборе входящих не должно возвращать запись, которую человек снял позже.
    # Граница у таких записей — не раньше otkaz.STARYE_SNYATIYA_DO.
    proverka("запись, снятая до 13.11 (время снятия не писалось): старое сообщение и второе, пришедшее до снятия, её не возвращают",
             otkaz.note("79990003001", "больше не пишите", "2026-08-24T17:00:40+03:00") is False
             and otkaz.note("79990003001", "больше не пишите нам, пожалуйста", "2026-08-24T17:05:40+03:00") is False
             and otkaz.note("79990003001", "больше не пишите нам, пожалуйста", "2026-11-13T12:00:00+03:00") is False
             and zapis("9990003001")["released"] == 1 and otkaz.is_refused("9990003001") is None)
    proverka("…а отказ, пришедший после появления правила возврата, — возвращает",
             otkaz.note("79990003001", "больше не пишите нам, пожалуйста", "2026-11-14T12:00:00+03:00") is True
             and zapis("9990003001")["released"] == 0 and otkaz.is_refused("9990003001"))
    t = c.get("/otkazy", auth=ADMIN).text
    proverka("страница: у вернувшейся записи сказано, что семью снимали и она отказалась снова; правило названо в шапке",
             "семья отказалась снова после снятия (снимал(а) админ" in t and "«мама написала, что передумали»" in t
             and "снова письменно откажется, запись сама вернётся" in t)
    proverka("is_refused и _tables по-прежнему не читают и не создают новых колонок (путь отправки не тронут); "
             "note() таблицу не правит — колонку времени снятия только читает",
             "snyal" not in inspect.getsource(otkaz.is_refused) and "ALTER" not in inspect.getsource(otkaz._tables)
             and "ALTER" not in inspect.getsource(otkaz.note) and "_kolonki_snyatiya" not in inspect.getsource(otkaz.note)
             and "ALTER" not in inspect.getsource(otkaz._posle_snyatiya))

    print("П1 (продолжение). Разбор входящих: снятую запись старое сообщение не возвращает, новое — возвращает")
    _otkazy_zaseyat(m)
    nomer = "9990003002"
    with m.db.get_conn() as conn:
        m._wz_tables(conn)
        conn.execute("DELETE FROM wazzup_inbox WHERE message_id LIKE 'stend-p1-%'")
        conn.execute("INSERT INTO wazzup_inbox (ts, phone, chat_type, text, message_id) VALUES (?,?,?,?,?)",
                     ("2026-09-05T10:00:00+03:00", "7" + nomer, "whatsapp", "Мы отказываемся, удалите наш номер", "stend-p1-1"))
    try:
        r = c.post("/api/otkazy/snyat", json={"phone": nomer, "prichina": "позвонили сами, просят писать"},
                   auth=ADMIN, headers=SVOYA_STRANICA)
        c.post("/api/otkazy/scan?hours=9000", auth=ADMIN, headers=SVOYA_STRANICA)
        proverka("сняли → разбор входящих за год: старое сообщение запись не вернуло",
                 r.status_code == 200 and zapis(nomer)["released"] == 1 and otkaz.is_refused(nomer) is None)
        c.post("/api/otkazy/scan?hours=9000&rebuild=true", auth=ADMIN, headers=SVOYA_STRANICA)
        proverka("…и пересборка стоп-листа (rebuild) тоже: решение человека не отменяется",
                 zapis(nomer)["released"] == 1 and otkaz.is_refused(nomer) is None)
        # 13.11.2026, вторая проверка «вечера 1»: пересборка стирала все действующие записи,
        # а собрать заново может только то, что есть во входящих за период. Запись, внесённая
        # руками (отказ голосом), и запись старше периода должны пережить пересборку.
        c.post("/api/otkazy/add?phone=79990009996&text=" + quote("по телефону: просила не писать"), auth=ADMIN, headers=SVOYA_STRANICA)
        with m.db.get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO otkazy (chat, phone, name, ts, text, source) VALUES (?,?,?,?,?,?)",
                         ("9990009995", "9990009995", "Стенд Давний", "2026-01-10T10:00:00+03:00", "не пишите нам", "по телефону чата"))
        c.post("/api/otkazy/scan?hours=24&rebuild=true", auth=ADMIN, headers=SVOYA_STRANICA)
        proverka("пересборка не стирает запись, внесённую руками, и запись старше периода разбора",
                 bool(otkaz.is_refused("9990009996")) and bool(otkaz.is_refused("9990009995")))
        with m.db.get_conn() as conn:
            conn.execute("DELETE FROM otkazy WHERE phone IN ('9990009996', '9990009995')")
        cherez_chas = (dt.datetime.fromisoformat(zapis(nomer)["snyal_kogda"]) + dt.timedelta(hours=1)).isoformat(timespec="seconds")
        with m.db.get_conn() as conn:
            conn.execute("INSERT INTO wazzup_inbox (ts, phone, chat_type, text, message_id) VALUES (?,?,?,?,?)",
                         (cherez_chas, "7" + nomer, "whatsapp", "нам больше не пишите, ходить не будем, снимите бронь", "stend-p1-2"))
        st = c.post("/api/otkazy/scan?hours=9000", auth=ADMIN, headers=SVOYA_STRANICA).json()
        proverka("семья после снятия пишет «нам больше не пишите, ходить не будем, снимите бронь» → разбор находит новый отказ, запрет действует",
                 st["новых отказов"] >= 1 and zapis(nomer)["released"] == 0 and zapis(nomer)["ts"] == cherez_chas
                 and otkaz.is_refused(nomer) and ujdet("7" + nomer)["уйдёт"] is False, st)
    finally:
        with m.db.get_conn() as conn:
            conn.execute("DELETE FROM wazzup_inbox WHERE message_id LIKE 'stend-p1-%'")

    print("П1 (продолжение). «Вернуть в стоп-лист» и внесение руками не стирают сведения о снятии")
    _otkazy_zaseyat(m)
    c.post("/api/otkazy/snyat", json={"chat": "5139000002", "prichina": "сняли не ту строку"}, auth=ADMIN, headers=SVOYA_STRANICA)
    do = zapis("5139000002")
    proverka("без входа — 401, с чужого сайта — 403, без ключа записи и по несуществующему — 400; ничего не вернулось",
             c.post("/api/otkazy/vernut", json={"chat": "5139000002"}).status_code == 401
             and c.post("/api/otkazy/vernut", json={"chat": "5139000002"}, auth=ADMIN, headers=CHUZHOY_FON).status_code == 403
             and c.post("/api/otkazy/vernut", json={}, auth=ADMIN, headers=SVOYA_STRANICA).status_code == 400
             and c.post("/api/otkazy/vernut", json={"chat": "net-takogo"}, auth=ADMIN, headers=SVOYA_STRANICA).status_code == 400
             and zapis("5139000002") == do and do["released"] == 1)
    r = c.post("/api/otkazy/vernut", json={"chat": "5139000002"}, auth=ADMIN, headers=SVOYA_STRANICA)
    posle = zapis("5139000002")
    proverka("кнопка «вернуть»: запись снова действует; дата и цитата отказа прежние, «кто, когда, почему снял» остались",
             r.status_code == 200 and r.json()["ok"] is True and posle["released"] == 0
             and {k: posle[k] for k in posle if k != "released"} == {k: do[k] for k in do if k != "released"}, posle)
    proverka("повторное «вернуть» действующей записи — 400",
             c.post("/api/otkazy/vernut", json={"chat": "5139000002"}, auth=ADMIN, headers=SVOYA_STRANICA).status_code == 400)
    t = c.get("/otkazy", auth=ADMIN).text
    proverka("страница: у записи пометка «возвращена в стоп-лист после снятия»; в «Сняты» у каждой строки — «вернуть в стоп-лист»",
             "возвращена в стоп-лист после снятия (снимал(а) админ" in t and "fetch('/api/otkazy/vernut'" in t
             and t.count("вернуть в стоп-лист</button>") == t.count("<form class=vernut") == 1)
    c.post("/api/otkazy/release?chat=79990002001", auth=VLADELEC, headers=SVOYA_STRANICA)
    do = zapis("79990002001")
    r = c.post("/api/otkazy/add?phone=79990002001&text=" + quote("снова попросила по телефону не писать"), auth=ADMIN, headers=SVOYA_STRANICA)
    posle = zapis("79990002001")
    proverka("POST /api/otkazy/add по семье, чья ручная запись снята: строка та же (раньше стиралась), снова действует, "
             "текст новый, «кто и когда снял» целы, имя в записи не затёрто",
             r.status_code == 200 and r.json()["ok"] is True and posle["id"] == do["id"] and posle["released"] == 0
             and posle["text"] == "снова попросила по телефону не писать" and posle["snyal_kto"] == "Борис"
             and posle["snyal_kogda"] == do["snyal_kogda"] and posle["name"] == "Пробная Алиса" and otkaz.is_refused("9990002001"), posle)
    r = c.post("/api/otkazy/add?phone=79990009998&text=" + quote("по телефону: не пишите"), auth=ADMIN, headers=SVOYA_STRANICA)
    proverka("…а новая семья вносится как раньше", r.status_code == 200 and zapis("79990009998")["released"] == 0
             and zapis("79990009998")["source"] == "руками" and otkaz.is_refused("9990009998"))

    print("П1 (продолжение). Пересборка списка сдвигает номера записей — старая вкладка чужую семью не снимет")
    with m.db.get_conn() as conn:
        conn.execute("DELETE FROM otkazy")
        conn.execute("INSERT INTO otkazy (chat, phone, ts, text, source) VALUES ('5139000002', '', '2026-10-12T18:00:00+03:00', 'Отмените запись, пожалуйста', '')")
        conn.execute("INSERT INTO otkazy (chat, phone, ts, text, source) VALUES ('9990003002', '9990003002', '2026-09-05T10:00:00+03:00', 'Мы отказываемся', 'телефон чата')")
    nomer_chata = zapis("5139000002")["id"]
    proverka("открытая вкладка: у чата без телефона в форме — ключ записи", 'data-chat="5139000002"' in c.get("/otkazy", auth=ADMIN).text)
    with m.db.get_conn() as conn:                    # как после scan?rebuild=true: те же записи, вставлены заново в другом порядке
        conn.execute("DELETE FROM otkazy")
        conn.execute("INSERT INTO otkazy (chat, phone, ts, text, source) VALUES ('9990003002', '9990003002', '2026-09-05T10:00:00+03:00', 'Мы отказываемся', 'телефон чата')")
        conn.execute("INSERT INTO otkazy (chat, phone, ts, text, source) VALUES ('5139000002', '', '2026-10-12T18:00:00+03:00', 'Отмените запись, пожалуйста', '')")
    proverka("после пересборки под прежним номером записи — уже семья, а не чат", zapis("9990003002")["id"] == nomer_chata)
    r = c.post("/api/otkazy/snyat", json={"chat": "5139000002", "id": nomer_chata, "prichina": "чат педагога, не клиента"},
               auth=ADMIN, headers=SVOYA_STRANICA)
    proverka("запрос с прежним номером и ключом чата — 400 «обновите страницу», никто не снят",
             r.status_code == 400 and zapis("9990003002")["released"] == 0 and zapis("5139000002")["released"] == 0, r.text[:160])
    r = c.post("/api/otkazy/snyat", json={"chat": "5139000002", "prichina": "чат педагога, не клиента"}, auth=ADMIN, headers=SVOYA_STRANICA)
    proverka("кнопка со старой вкладки (ключ записи) снимает именно чат; семья под запретом, как была",
             r.status_code == 200 and r.json()["snyato"] == 1 and zapis("5139000002")["released"] == 1
             and zapis("9990003002")["released"] == 0 and otkaz.is_refused("9990003002"))
    with m.db.get_conn() as conn:
        conn.execute("DELETE FROM otkazy")

    print("П2. /api/audit/resolve: не владелец снимает только находки вида «правила»")
    nomera = {}
    for vid in ("честность", "оплата без подписи", "деньги", "правила"):
        audit.add_flag(vid, f"stend-p2:{vid}", audit.HIGH, f"Выдуманная находка «{vid}» (стенд)", "стенд", user_id=9001)
    with m.db.get_conn() as conn:
        for r in conn.execute("SELECT id, kind FROM audit_flags WHERE key LIKE 'stend-p2:%'"):
            nomera[r["kind"]] = r["id"]

    def status(vid):
        with m.db.get_conn() as conn:
            return conn.execute("SELECT status FROM audit_flags WHERE id=?", (nomera[vid],)).fetchone()[0]
    try:
        u_vladelca = proverka_mk.krasnye(vladelec=True)["vsego"]
        kody = {vid: c.post(f"/api/audit/resolve?flag_id={nomera[vid]}&status=ok", auth=ADMIN, headers=SVOYA_STRANICA).status_code
                for vid in ("честность", "оплата без подписи", "деньги")}
        proverka("админ по номеру находки «честность», «оплата без подписи», «деньги» — 403, находка не снята",
                 set(kody.values()) == {403} and all(status(v) == "new" for v in kody), kody)
        proverka("…и число красных находок в разделе 13 у владельца не изменилось (до правки запросом админа их становилось меньше)",
                 proverka_mk.krasnye(vladelec=True)["vsego"] == u_vladelca)
        r = c.post(f"/api/audit/resolve?flag_id={nomera['правила']}&status=ok", auth=ADMIN, headers=SVOYA_STRANICA)
        proverka("находку вида «правила» админ снимает, как раньше (кнопки /pravila-kontrol)",
                 r.status_code == 200 and r.json() == {"ok": True} and status("правила") == "ok")
        r = c.post("/api/audit/resolve?flag_id=0&status=ok", auth=ADMIN, headers=SVOYA_STRANICA)
        proverka("номера нет в ленте — как было: не отказ, {\"ok\": false}", r.status_code == 200 and r.json() == {"ok": False}, r.text[:80])
        r = c.post(f"/api/audit/resolve?flag_id={nomera['честность']}&status=ok", auth=VLADELEC, headers=SVOYA_STRANICA)
        proverka("владелец снимает любую находку", r.status_code == 200 and r.json() == {"ok": True} and status("честность") == "ok")
        proverka("кнопки «не нарушение» на /audit (владелец) и «принято» на /pravila-kontrol зовут тот же адрес",
                 "/api/audit/resolve?flag_id=" in inspect.getsource(m.audit_page) and "_rol(request) != \"owner\"" in inspect.getsource(m.api_audit_resolve))
    finally:
        with m.db.get_conn() as conn:
            conn.execute("DELETE FROM audit_flags WHERE key LIKE 'stend-p2:%'")

    print("П3. Вебхук Wazzup: после отката и повторного ввода того же ключа сообщения не теряются")
    def wh(klyuch=None):
        return c.post("/wazzup/webhook", params={} if klyuch is None else {"k": klyuch}, json={}).status_code

    def nastroyka(znachenie):
        return c.post("/api/settings", json={"key": "wazzup_hook_key", "value": znachenie}, auth=VLADELEC).status_code
    K, K2 = "stend-wazzup-klyuch-povtor", "stend-wazzup-klyuch-vtoroj"
    try:
        proverka("ключ вписан, пришло событие с ключом — ключ требуется: без ключа 403",
                 nastroyka(K) == 200 and wh(K) == 200 and wz.hook_strogo() and wh() == 403)
        proverka("откат: настройку очистили — принимается всё, отметка «ключ требуется» стёрта",
                 nastroyka("") == 200 and wh() == 200 and not m.db.get_setting(wz._HOOK_OK))
        r = c.post("/owner", data={"wazzup_hook_key": K}, auth=VLADELEC)
        proverka("тот же ключ вписали снова (Wazzup ещё шлёт по адресу без ключа): запрос без ключа принимается — "
                 "состояние «ждём первое событие», а не «ключ требуется»",
                 r.status_code == 200 and not wz.hook_strogo() and wh() == 200 and "ждём первое событие" in wz.hook_sostoyanie()
                 and "ждём первое событие" in r.text and "ключ требуется" not in r.text, wz.hook_sostoyanie())
        proverka("…и снова требуется только после первого события с ключом", wh(K) == 200 and wz.hook_strogo() and wh() == 403)
        c.post("/owner", data={"wazzup_hook_key": K}, auth=VLADELEC)
        proverka("тот же ключ, сохранённый повторно, проверку не выключает", wz.hook_strogo() and wh() == 403)
        proverka("K → K2 (события с K2 ещё не было) → снова K: проверка сама не включается, без ключа принимаем",
                 nastroyka(K2) == 200 and not wz.hook_strogo() and nastroyka(K) == 200 and not wz.hook_strogo() and wh() == 200)
        proverka("оба места, где настройку сохраняет человек, идут через одну функцию со сбросом отметки",
                 "_nastroyku_zapisat(" in inspect.getsource(m.owner_save) and "_nastroyku_zapisat(" in inspect.getsource(m.api_set_setting)
                 and "hook_klyuch_zapisat" in inspect.getsource(m._nastroyku_zapisat))
    finally:
        m.db.set_setting("wazzup_hook_key", "")
        m.db.set_setting(wz._HOOK_OK, "")

    print("П4. Вебхук Марквиза: ключ, ломающий адрес, не сохраняется; отказ по ключу оставляет след")
    def mq(klyuch=None):
        return c.post("/marquiz/webhook", params={} if klyuch is None else {"key": klyuch}, json={})
    plohie = ("Kl+yuch&2026#x%41-dlinnyy", "korotkiy-1", "ключ-русскими-буквами-12345", "s probelom vnutri 1234567")
    otvety = [(c.post("/owner", data={"marquiz_key": k}, auth=VLADELEC), c.post("/api/settings", json={"key": "marquiz_key", "value": k}, auth=VLADELEC))
              for k in plohie]
    proverka("ключ с «+», «&», «#», «%», пробелом, русскими буквами или короче 16 знаков не сохраняется: страница объясняет, запрос — 400",
             all(a.status_code == 200 and "ключ не сохранён" in a.text and b.status_code == 400 for a, b in otvety)
             and not m.db.get_setting("marquiz_key"), [(a.status_code, b.status_code) for a, b in otvety])
    proverka("…значение отвергнутого ключа на страницу не попадает, прежний ключ продолжает работать",
             all(k not in a.text for (a, _), k in zip(otvety, plohie)) and mq(m.MARQUIZ_KEY).status_code == 200)
    HOROSHIJ = "Stend_marquiz-klyuch-2026"
    zapisi_zhurnala = []

    class _Lovec(logging.Handler):
        def emit(self, record):
            zapisi_zhurnala.append(record.getMessage())
    lovec = _Lovec()
    logging.getLogger("kidsup.marquiz").addHandler(lovec)
    try:
        r = c.post("/owner", data={"marquiz_key": HOROSHIJ}, auth=VLADELEC)
        do = dict(m._MARQUIZ_SLED)
        kody = [mq("chuzhoy-klyuch-s-oshibkoj").status_code, mq(HOROSHIJ).status_code]
        stranica = c.get("/owner", auth=VLADELEC).text
        proverka("ключ из латиницы, цифр, «-» и «_» сохраняется; с ним заявка принимается, с чужим — 403",
                 r.status_code == 200 and "Сохранено: Марквиз" in r.text and m.db.get_setting("marquiz_key") == HOROSHIJ and kody == [403, 200], kody)
        proverka("отказ по ключу оставляет след: строка в журнале сервера без значения ключа и счёт на странице «Настройки»",
                 len(zapisi_zhurnala) == 1 and "ключ не подошёл" in zapisi_zhurnala[0] and "chuzhoy" not in zapisi_zhurnala[0]
                 and HOROSHIJ not in zapisi_zhurnala[0]
                 and m._MARQUIZ_SLED["otkloneno"] == do["otkloneno"] + 1 and m._MARQUIZ_SLED["prinyato"] == do["prinyato"] + 1
                 and f"отклонено по ключу {m._MARQUIZ_SLED['otkloneno']}" in stranica and "действует ключ из настройки" in stranica, zapisi_zhurnala)
        proverka("на странице — готовый адрес для кабинета Марквиза без значения ключа и порядок шагов",
                 "https://app.kidsup.ru/marquiz/webhook?key=&lt;ключ&gt;" in stranica and HOROSHIJ not in stranica
                 and "Сначала сохранить здесь, сразу после — вписать адрес в Марквизе" in stranica)
        src = inspect.getsource(m)
        proverka("порядок шагов в коде — тот же, что на странице: сначала настройка, потом адрес в кабинете",
                 "1) /owner → «Марквиз — ключ вебхука» → «Сохранить»" in src and "2) сразу после — адрес" in src)
    finally:
        logging.getLogger("kidsup.marquiz").removeHandler(lovec)
        m.db.set_setting("marquiz_key", "")

    print("П5. Раздел 13 «Проверки МойКласса»: «долг клиента» по старым счетам админам красным не показывается")
    # суммы и названия — приметные: в ленте стенда уже есть свои «Долг клиента …» от прежних проверок
    audit.add_flag("деньги", "neg-balance:9005:stend-p5", audit.HIGH, "Долг клиента 25 777 ₽", "стенд: весь долг — счёт 2024 года", user_id=9005)
    audit.add_flag("деньги", "neg-balance:9001:stend-p5", audit.HIGH, "Долг клиента 22 777 ₽", "стенд: счёт этого месяца", user_id=9001)
    audit.add_flag("деньги", "sub-unpaid:9005:stend-p5", audit.HIGH, "Абонемент не оплачен (стенд П5)", "стенд: другая находка той же семьи", user_id=9005)

    def nashi(vladelec):
        kr = proverka_mk.krasnye(vladelec=vladelec)
        return kr, {x["chto"]: g for g in kr["gruppy"] for x in g["punkty"]}
    bylo_snimok = m.db.get_state(m.DOLGI_KLYUCH)
    try:
        m.db.set_state(m.DOLGI_KLYUCH, "")
        kr, stroki = nashi(False)
        proverka("снимка «Долгов» нет — админ видит обе находки «Долг клиента», как раньше",
                 "Долг клиента 25 777 ₽" in stroki and "Долг клиента 22 777 ₽" in stroki and kr["starye_scheta"] == 0)
        m.db.set_state(m.DOLGI_KLYUCH, json.dumps({"ts": dt.datetime.now().isoformat(), "d": _dolgi_otchet()}, ensure_ascii=False))
        kr, stroki = nashi(False)
        proverka("свежий снимок: весь долг семьи — в корзине «списать» (старые счета) → находка админу не показана и посчитана отдельно",
                 "Долг клиента 25 777 ₽" not in stroki and kr["starye_scheta"] == 1,
                 (sorted(stroki), sorted(proverka_mk._dolzhniki_po_starym_schetam())))
        proverka("…долг по счёту этого месяца и другая находка той же семьи — на месте",
                 "Долг клиента 22 777 ₽" in stroki and "Абонемент не оплачен (стенд П5)" in stroki)
        kr_v, stroki_v = nashi(True)
        proverka("владелец видит всё; у старого долга пометка «старый счёт», у остальных её нет",
                 stroki_v["Долг клиента 25 777 ₽"]["staryj_schet"] is True and not stroki_v["Долг клиента 22 777 ₽"]["staryj_schet"]
                 and kr_v["starye_scheta"] == 0 and kr_v["vsego"] >= kr["vsego"] + 1)
        ha, hv = c.get("/proverka-mk", auth=ADMIN).text, c.get("/proverka-mk", auth=VLADELEC).text
        proverka("страница админа: «Не показано: 1 «долг клиента» по старым счетам … делать с ними ничего не нужно»; владельца — пометка у строки",
                 "Не показано: 1 «долг клиента» по старым счетам" in ha and "делать с ними ничего не нужно" in ha
                 and "Долг клиента 25 777 ₽" not in ha and "Долг клиента 25 777 ₽" in hv
                 and "старый счёт — ждёт вашего решения" in hv and "Не показано:" not in hv)
        proverka("то же число красных «денег» — на «Долгах» (строка про раздел 13)",
                 m._dolgi_krasnyh() == kr["po_vidam"].get("деньги", 0))
        m.db.set_state(m.DOLGI_KLYUCH, json.dumps({"ts": (dt.datetime.now() - dt.timedelta(days=3)).isoformat(), "d": _dolgi_otchet()},
                                                  ensure_ascii=False))
        proverka("снимок старше полутора суток — не доверяем: находка снова показана",
                 "Долг клиента 25 777 ₽" in nashi(False)[1] and proverka_mk.DOLGI_SNIMOK_CHASOV == 36)
        proverka("ключ снимка — один на «Долги» и на раздел 13", proverka_mk.DOLGI_SNIMOK == m.DOLGI_KLYUCH)
    finally:
        m.db.set_state(m.DOLGI_KLYUCH, bylo_snimok or "")
        with m.db.get_conn() as conn:
            conn.execute("DELETE FROM audit_flags WHERE key LIKE '%stend-p5'")


if __name__ == "__main__":
    main()
