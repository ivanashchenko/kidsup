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

    print("Остаются по адресу (перенос содержимого — шаг 2)")
    # на этих страницах есть то, чего нет на целевой: переход ставить рано
    # 13.11.2026, шаг 2: /settings и /metrics переехали (раздел «Ж») — из списка убраны
    for adres in ("/segodnya", "/spiski", "/zayavki", "/leads", "/clicks"):
        proverka(f"{adres} — маршрут на месте и не переход", f"GET {adres}" in est and adres not in m.UBRANO)
    proverka("POST /settings и /settings/test — форма ключа МойКласса не осталась без адреса",
             {"POST /settings", "POST /settings/test"} <= est)
    proverka("/spiski — один обработчик (мёртвый второй удалён)",
             sum(1 for r in m.app.routes if getattr(r, "path", "") == "/spiski") == 1)
    r = c.get("/spiski", auth=ADMIN)
    proverka("/spiski открывается: списки по группам", r.status_code == 200 and "Списки по группам" in r.text,
             r.status_code)


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
    items = c.get("/api/search?q=Безномерова", auth=ADMIN).json()["items"]
    proverka("поиск: клиент без телефона → карточка МойКласса",
             [i["url"] for i in items] == ["https://app.moyklass.com/client/9901"], [i["url"] for i in items])
    items = c.get("/api/search?q=Длинномерова", auth=ADMIN).json()["items"]
    proverka("поиск: номер из 12 цифр не обрезаем до чужого — карточка МойКласса",
             [i["url"] for i in items] == ["https://app.moyklass.com/client/9902"], [i["url"] for i in items])
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
# Не из шага 1: разовый просмотр «старых звонков» (решение Бориса 12.11), только чтение.
NOVYE_ZVONKI_1211 = {"GET /api/hvost/staroe"}
# То, что зовут рутины ассистента, автопилот, сайт и вебхуки, — названо поимённо,
# чтобы пропажа была видна и без снимка «до».
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
GET /api/perepiska · GET /api/perepiska/kto · GET /api/vozvrat/spiski · GET /api/bez-oplaty/status
POST /api/bez-oplaty/status · GET /api/spiski · GET /api/segodnya · GET /api/nezvonili · GET /api/ai/probe
POST /api/callplan/done · GET /export/leads.csv · POST /leads/push/{lead_id} · GET /go/{channel}
GET /boris/docs · GET /boris/docs/{slug} · GET /admin/docs · GET /admin/docs/{slug} · GET /base/{slug}
GET /pult · GET /liniya · GET /waiting · GET /obzvon-dnya · GET /mesta · GET /voronka · GET /blank-grupp
"""


def test_marshruty(m, fajl_do):
    print("Таблица маршрутов: до и после")
    do = set(json.load(open(fajl_do, encoding="utf-8")))
    posle = marshruty(m)
    proverka("снимок «до» — 471 маршрут", len(do) == 471, len(do))
    proverka("пропали ровно пять — те, что звала только убранная страница (и перечень адресов: "
             "/openapi.json, /docs, /redoc)", do - posle == PROPALI | UBRANY_VHOD,
             sorted((do - posle) ^ (PROPALI | UBRANY_VHOD)))
    vse_novye = NOVYE | NOVYE_ZVONKI_1211 | NOVYE_PROVERKA_MK | NOVYE_VHOD | NOVYE_SHAPKA
    proverka("появились ровно три: «Архив» и признак «архив» у документов (и просмотр старых звонков 12.11, "
             "пять адресов страницы «Проверка МойКласса», адреса личных входов и счётчик бейджа шапки 13.11)",
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
        ostalis = [f for f in os.listdir(os.path.join(rabota, "plan")) if f.startswith("311_")]
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
    proverka("у админа 16 пунктов: Смена, Группы, Деньги, Справка",
             [r for r, _ in nav.menu("admin")] == ["Смена", "Группы", "Деньги", "Справка"]
             and len(_adresa(nav.menu("admin"))) == 16, [r for r, _ in nav.menu("admin")])
    proverka("у владельца — те же и 11 своих последним разделом",
             nav.menu("owner")[:-1] == nav.menu("admin") and nav.menu("owner")[-1] == nav.OWNER
             and len(nav.OWNER[1]) == 11)
    proverka("педагогу по ключу меню не положено", nav.menu("pedagog") == [])

    print("Меню: админ")
    h = shapka(c, "/liniya", ADMIN)
    net = [a for a in _adresa(nav.ADMIN) if f'href="{a}"' not in h]
    proverka("в шапке все 16 пунктов админов", not net, net)
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
    proverka("в шапке все 16 пунктов админов и 11 своих", not net, net)
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
    proverka("у админа все четыре раздела и кнопка пульта",
             all(x in r.text for x in ("Смена", "Группы", "Деньги", "Справка", "Открыть пульт", "href=\"/pult\"")))
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
    net = [a for a, *_ in nav.PEREEZD if f'href="{a}"' not in r.text]
    # 13.11.2026, шаг 2: было семь; /settings и /metrics переехали — они в блоке «Уже переехали»
    proverka("блок «Переезжают»: пять страниц, которые ещё открываются по адресу",
             "Переезжают" in r.text and len(nav.PEREEZD) == 5 and not net, net)
    net = [a for a, _, _, kuda, *_ in nav.PEREEHALI if a not in r.text or f'href="{kuda}"' not in r.text]
    proverka("блок «Уже переехали»: /settings и /metrics — с датой и ссылкой на новое место, без ссылки на старый адрес",
             "Уже переехали" in r.text and {a for a, *_ in nav.PEREEHALI} == set(OBEDINENY_PEREEHALI) and not net
             and 'href="/settings"' not in r.text and 'href="/metrics"' not in r.text and "13.11.2026" in r.text, net)
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
    proverka("строка «Ещё»: вернуть ушедших, карта, конструктор",
             all(f"href='{a}'" in blok for a in ("/obzvon", "/karta", "/constructor")))
    blok = c.get("/pult", auth=VLADELEC).text.split("id='razdely'", 1)[-1].split("</div>", 1)[0]
    proverka("у владельца — и его раздел", "Владельцу" in blok and "href='/arhiv'" in blok and "href='/owner'" in blok)


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
OBEDINENY_SHAG2 = {"/segodnya": "/pult", "/spiski": "/blank-grupp",
                   "/zayavki": "/blank-grupp", "/leads": "/istochniki", "/clicks": "/istochniki"}
OBEDINENY_PEREEHALI = {"/settings": "/owner#mk", "/metrics": "/journal#schetchiki"}
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
    for imya, adres in nav.OWNER[1]:
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
    proverka("уводим на пульт ровно четыре пункта владельца", VLADELCU_NA_PULT <= set(_adresa([nav.OWNER])))
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
    pereezd = {a: kuda for a, _, _, kuda, _ in nav.PEREEZD}
    proverka("все пять записаны на страницу «Архив» в блок «Переезжают» с той же целью", pereezd == OBEDINENY_SHAG2,
             pereezd)
    for adres in OBEDINENY_SHAG2:
        if adres == "/zayavki":
            # без ключа МойКласса на стенде страница падает — так было и до правок; смотрим маршрут
            r = [x for x in m.app.routes if getattr(x, "path", "") == adres]
            proverka(f"{adres}: маршрут — сама страница, не переход",
                     len(r) == 1 and r[0].endpoint.__name__ == "zayavki_page", [x.endpoint.__name__ for x in r])
            continue
        kod = c.get(adres, auth=ADMIN, follow_redirects=False).status_code
        proverka(f"{adres} открывается по адресу (200), не переход", kod == 200, kod)


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
    for imya, adres in [p for _, punkty in nav.ADMIN for p in punkty]:
        lishnie = ZAPRET & ssylki(c.get(adres, auth=ADMIN).text)
        proverka(f"admin: на странице «{imya}» {adres} — ни одной", not lishnie, sorted(lishnie))
    # у владельца: /settings — из плашки «укажите ключ МойКласса» (на стенде ключа нет) и со
    # страницы «Настройки»: только там ключ и меняется; /arhiv — сама страница архива
    # 13.11.2026, шаг 2: плашка теперь зовёт на /owner#mk, исключение осталось одно — сама
    # /owner: её форма ключа шлёт POST на /settings (адрес под защитой vhod.TOLKO_CHELOVEK)
    for imya, adres in nav.OWNER[1]:
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
    p = _plashka(c.get("/leads", auth=VLADELEC).text)
    proverka("/leads у владельца: «переезжает», ссылка на «Откуда заявки»",
             "переезжает" in p and 'href="/istochniki"' in p, p[:200])
    p = _plashka(c.get("/leads", auth=ADMIN).text)
    proverka("/leads у админа: «скоро переедет», без ссылки на закрытую ему страницу владельца",
             "скоро переедет" in p and "/istochniki" not in p, p[:200])
    p = _plashka(c.get("/segodnya", auth=ADMIN).text)
    proverka("/segodnya у админа: ссылка на пульт", 'href="/pult"' in p, p[:200])
    for imya, adres in [x for _, punkty in m.nav.ADMIN for x in punkty]:
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
                   "/pravila-kontrol", "/proverka-mk", "/spiski", "/mesta", "/waiting", "/liniya"]
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
    proverka("нижняя панель владельца: Главная · Мои дела · Контроль оплат · ☰ Разделы",
             [x for x in re.findall(r'class="kn-podpis">([^<]+)<', h)] == ["Главная", "Мои дела", "Контроль оплат", "Разделы"])
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


def test_schetchik_i_pereezdy(c, m, baza):
    import time
    from starlette.requests import Request
    BR = {"Accept": "text/html,application/xhtml+xml", "Sec-Fetch-Dest": "document"}

    print("Ж1. Счётчик открытий: что пишет")
    do = _hity(baza, "/pult", "admin")
    r = c.get("/pult", auth=ADMIN, headers=BR)
    proverka("GET /pult под admin из браузера → 200 и +1 в page_hits с rol=admin",
             r.status_code == 200 and _hity(baza, "/pult", "admin") == do + 1, (r.status_code, do, _hity(baza, "/pult", "admin")))
    con = sqlite3.connect(baza)
    den = con.execute("SELECT day FROM page_hits WHERE path='/pult' AND rol='admin'").fetchall()
    kol = [x[1] for x in con.execute("PRAGMA table_info(page_hits)")]
    con.close()
    proverka("строка одна на день (UPSERT), день — сегодняшний по часам сервера, колонки day/path/rol/n",
             den == [(m.autopilot._today().isoformat(),)] and kol == ["day", "path", "rol", "n"], (den, kol))
    do = _hity(baza, "/owner", "owner")
    c.get("/owner", auth=VLADELEC, headers=BR)
    proverka("GET /owner под владельцем → rol=owner (роль из request.state доходит до слоя)",
             _hity(baza, "/owner", "owner") == do + 1)
    kluch = m.db.get_setting("pedagog_key")
    c.get(f"/english/karta?k={kluch}", headers=BR)
    proverka("педагог по ключу: rol=pedagog, ключ из адреса в таблицу не попал",
             _hity(baza, "/english/karta", "pedagog") == 1 and _hity(baza, f"/english/karta?k={kluch}") == 0)
    kod, loc = c.get("/groups", auth=ADMIN, headers=BR, follow_redirects=False).status_code, None
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
    proverka("админ: /metrics → 303 сразу на /pult → 200 («Журнал» — страница владельца, цепочки нет)",
             (kod, loc, cel) == (303, "/pult", 200), (kod, loc, cel))
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
    c.get("/students", auth=VLADELEC, headers=BR)
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


def _lovushki_na_vse():
    """Страницы меню подгружают модули при первом открытии — ловушки отправки ставим и на них,
    до первого запроса: сеть на стенде закрыта, но и попытки отправить быть не должно."""
    import importlib
    import pkgutil
    import app
    for mod in pkgutil.iter_modules(app.__path__):
        importlib.import_module(f"app.{mod.name}")
    lt._postavit_lovushki()


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
    proverka("ни одной отправки", not lt.LOVUSHKI, lt.LOVUSHKI)
    proverka("ни одной попытки выйти в сеть, кроме известных опросов Mango с пульта и «Ответов»",
             all(any(f.startswith(("mango.py", "zayavki.py")) for f in otkuda) for _, otkuda in lt.SET), lt.SET[:5])
    print(f"\nИтого: {lt.proshlo} OK, {lt.oshibok} ошибок · стенд {work}")
    sys.exit(1 if lt.oshibok else 0)


if __name__ == "__main__":
    main()
