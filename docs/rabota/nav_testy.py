# -*- coding: utf-8 -*-
"""Проверки шага 1 «скрыть старое» на app.kidsup.ru — на стенде, без сервера и без сети.

12.11.2026, Борис: «скрой не нужные / не актуальные / старые страницы». Страницы
убраны, их адреса ведут на страницы-замены. Здесь зафиксировано: куда ведёт каждый
старый адрес, что открытие ссылки больше ничего не отправляет клиентам и что из
таблицы маршрутов пропало только то, что убирали нарочно — всё, чем пользуются
рутины ассистента и автопилот (/api/…, /export/raw/…, вебхуки), на месте.

    python3 docs/rabota/nav_testy.py [--tree КОРЕНЬ] [--work ПАПКА] [--do ФАЙЛ.json]

  --tree  корень дерева, в котором лежит app/ (по умолчанию — этот репозиторий;
          перед выкаткой — чистая выгрузка `git archive`, без удалённых у нас файлов)
  --work  рабочая папка стенда (по умолчанию — временная)
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
        zhdem = quote(kuda, safe="/?=&")
        proverka(f"{otkuda} → 303 {kuda}", kod == 303 and loc == zhdem, (kod, loc))
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
    for adres in ("/settings", "/metrics", "/segodnya", "/spiski", "/zayavki", "/leads", "/clicks"):
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
    proverka("пропали ровно пять — те, что звала только убранная страница", do - posle == PROPALI,
             sorted((do - posle) ^ PROPALI))
    proverka("появились ровно три: «Архив» и признак «архив» у документов", posle - do == NOVYE,
             sorted((posle - do) ^ NOVYE))
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
    """HTML общей шапки страницы — всё, что до <main>."""
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
    proverka("подсвечена открытая страница", 'href="/liniya" class="active"' in h)
    proverka("на телефоне меню сворачивается: кнопка «☰ Разделы»",
             'id="nav-toggle"' in h and "☰ Разделы" in h and 'aria-controls="nav-razdely"' in h)
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
    proverka("/owner: строка-ссылка на ключ МойКласса (/settings) — иначе страницу не найти",
             "href='/settings'" in c.get("/owner", auth=VLADELEC).text)

    print("Педагог по ключу из ссылки")
    kluch = m.db.get_setting("pedagog_key")
    r = c.get(f"/english/karta?k={kluch}")
    proverka("карточка речи по ключу без входа → 200", r.status_code == 200, r.status_code)
    proverka("…ни меню, ни поиска, ни бейджа «ждут», ни синхронизации",
             all(x not in r.text for x in ('class="nav-main"', 'id="gs-input"', 'id="wait-badge"', 'id="sync-btn"',
                                           'id="nav-toggle"')))
    proverka("…виджеты админов не опрашиваются (флаг педагога от сервера)", "KIDSUP_PED = true;" in r.text)
    r = c.get(f"/english/karta?k={kluch}", auth=("boris", "chto-ugodno"))
    proverka("ключ и поддельный вход boris → педагог, не владелец",
             r.status_code == 200 and "Владельцу" not in r.text and 'class="nav-main"' not in r.text)
    r = c.get(f"/english/karta?k={kluch}", auth=ADMIN)
    proverka("та же ссылка у админа — с его меню",
             r.status_code == 200 and 'class="nav-main"' in r.text and "KIDSUP_PED = false;" in r.text)
    proverka("неверный ключ без входа → 401", c.get("/english/karta?k=nevernyj").status_code == 401)


# --- Б. Главная по роли ------------------------------------------------------------

def test_glavnaya(c, m):
    from app import boris_docs
    print("Главная: переход на рабочее место роли")
    proverka("/ админ → 303 /pult", lokaciya(c, "/") == (303, "/pult"), lokaciya(c, "/"))
    proverka("/ владелец, стартовой страницы нет → /boris/docs",
             lokaciya(c, "/", VLADELEC) == (303, "/boris/docs"), lokaciya(c, "/", VLADELEC))
    boris_docs.save("start", "Стартовая страница (стенд)", "<!doctype html><title>стенд</title>" + "<p>стенд</p>" * 5)
    proverka("/ владелец, стартовая выложена → /boris/docs/start",
             lokaciya(c, "/", VLADELEC) == (303, "/boris/docs/start"), lokaciya(c, "/", VLADELEC))
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
    proverka("блок «Переезжают»: семь страниц с переносом на шаге 2",
             "Переезжают" in r.text and len(nav.PEREEZD) == 7 and not net, net)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=ROOT)
    ap.add_argument("--work", default="")
    ap.add_argument("--do", default=os.path.join(TUT, "nav_marshruty_do.json"))
    a = ap.parse_args()
    work = a.work or tempfile.mkdtemp(prefix="nav_")
    os.makedirs(work, exist_ok=True)
    tree = os.path.abspath(a.tree)
    m = lt.zapustit(tree, work)
    baza = os.path.join(work, "kidsup.db")
    lt.zasejat(baza)
    # роли: общий вход админов и отдельный пароль владельца — как на сервере
    m.config.APP_USER, m.config.APP_PASSWORD = ADMIN
    m.db.set_setting("owner_password", VLADELEC[1])
    m.db.set_setting("pedagog_key", "stend-kluch")         # ключ педагога — только на стенде
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
    proverka("ни одной отправки", not lt.LOVUSHKI, lt.LOVUSHKI)
    print(f"\nИтого: {lt.proshlo} OK, {lt.oshibok} ошибок · стенд {work}")
    sys.exit(1 if lt.oshibok else 0)


if __name__ == "__main__":
    main()
