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
# Новые адреса шага 1: страница «Архив» и признак «архив» у документов. Пока их делают
# следующие исполнители, отсутствие — не ошибка; чужих новых адресов быть не должно.
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
    proverka("чужих новых маршрутов нет", posle - do <= NOVYE, sorted(posle - do - NOVYE))
    if NOVYE - posle:
        print("          (ещё не сделаны: " + ", ".join(sorted(NOVYE - posle)) + ")")
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
    from fastapi.testclient import TestClient
    c = TestClient(m.app)
    test_perehody(c, m)
    test_bezopasnost(c, m)
    test_ssylki(c, m, baza)
    test_marshruty(m, a.do)
    test_uborka(tree)
    proverka("ни одной отправки", not lt.LOVUSHKI, lt.LOVUSHKI)
    print(f"\nИтого: {lt.proshlo} OK, {lt.oshibok} ошибок · стенд {work}")
    sys.exit(1 if lt.oshibok else 0)


if __name__ == "__main__":
    main()
