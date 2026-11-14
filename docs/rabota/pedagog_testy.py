# -*- coding: utf-8 -*-
"""Ключи педагогов (app/pedklyuchi.py) — проверки на стенде, без сервера и без сети.

13.11.2026, Борис — «М, П, Т — по рекомендациям»:
  Т1. Сменить общий ключ: ссылка с ним много раз пересылалась в чатах.
  Т2. Вторая страница «После первого занятия» — в шапке педагога.
  Т3. Личные ключи: каждому педагогу своя ссылка, видит только своих детей.

Главное требование выкладки: пока владелец ничего не сделал, для педагогов не меняется
НИЧЕГО — общая ссылка из чата открывается на обеих страницах и во всех запросах карточки.

Здесь зафиксировано: общий ключ до действий владельца; шапка из двух пунктов; блок «Ключи
педагогов» на /owner (только владелец через форму, подтверждение паролем, чужой сайт — 403);
личный ключ — свои дети видны, чужие 403 на чтение и на каждую изменяющую ручку; выданный
заново ключ отменяет старый, отключённый не работает; выключенный общий не работает, а
личные работают; ссылка показывается один раз; ключ не утекает в /api/settings, журнал, базу
и HTML других страниц; перебор ограничен; админ со ссылкой педагога остаётся админом.

    python3 docs/rabota/pedagog_testy.py [--work ПАПКА]

Обвязка — из liniya_testy.py: сеть закрыта, отправки — ловушки, автопилот не стартует.
Данные выдуманные (педагоги «Проверкина Анна», «Образцов Игорь»…, дети 9003, 93xx…); пароли
и ключи придумываются на ходу, живут только в памяти прогона и не печатаются.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import logging
import os
import pkgutil
import re
import secrets
import sqlite3
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

KOREN = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, KOREN)

_spec = importlib.util.spec_from_file_location("liniya_testy", os.path.join(KOREN, "docs", "rabota", "liniya_testy.py"))
lt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lt)
proverka = lt.proverka

B = "https://app.kidsup.ru"
UA = "Mozilla/5.0 (stend pedagog_testy)"
STR = {"accept": "text/html,application/xhtml+xml", "sec-fetch-mode": "navigate", "sec-fetch-site": "none",
       "sec-fetch-dest": "document", "user-agent": UA}                      # человек открывает страницу
SVOI = {"origin": B, "sec-fetch-site": "same-origin", "sec-fetch-mode": "navigate", "user-agent": UA}
FETCH = {"accept": "application/json", "sec-fetch-mode": "cors", "sec-fetch-site": "same-origin", "user-agent": UA}

OBSHCHIY_ADMIN = ("admin", "stend-" + secrets.token_urlsafe(10))    # старый общий вход админов
VLADELEC = ("boris", "stend-" + secrets.token_urlsafe(10))         # прежний пароль владельца (он же — на форме)
LENA = "stend-" + secrets.token_urlsafe(10)                         # личный пароль админа
PED = "stend" + secrets.token_hex(6)                                # прежний общий ключ педагогов (pedagog_key)
SEKRETY: list = []                                                  # выданные ключи — искать в следах
ZAHVAT: list = []
m = None
pk = None                                                           # app.pedklyuchi

# Выдуманные педагоги (сотрудники «МойКласса» стенда). 63600 — роль «педагог».
ANNA, IGOR, OLGA, VERA, DARYA, ZOYA, STOYKA = 7001, 7002, 7003, 7004, 7005, 7006, 7007
SOTRUDNIKI = [
    (ANNA, "Проверкина Анна", [63600], True),      # английский пн-ср (Гр1) и английский в саду и нулевом классе
    (IGOR, "Образцов Игорь", [63600], True),       # английский вт-чт (Гр6)
    (OLGA, "Примерова Ольга", [63600], True),      # подготовка к школе: групп английского нет
    (VERA, "Садовая Вера", [63600], True),         # воспитатель мини-сада: стоит во всех занятиях сада
    (DARYA, "Нулёвкина Дарья", [63600], True),     # воспитатель нулевого класса
    (ZOYA, "Бывшая Зоя", [63600], False),          # уволена: в списке педагогов её нет
    (STOYKA, "Стойкина Нина", [1], True),          # администратор, не педагог
]
GR1, GR6, GR5 = "пн-ср_16:00_7-8 лет_Pre-A1 Starters (Гр1)", "вт-чт_17:00_5-7 лет_A1 Pre-Starter (Гр6)", \
    "вт-чт_16:00_12-13 лет_A1 Starter (Гр5)"
SAD, NUL = "Мини-сад · английский пн, ср 12:00", "Нулевой класс · английский пн, ср 13:00"
DETI = {GR1: [9003] + [9300 + i for i in range(7)], GR6: [9800, 9801], GR5: [9920 + i for i in range(5)],
        SAD: [9500 + i for i in range(5)], NUL: [9600 + i for i in range(3)]}
SVOY, CHUZHOY, NICHEY, NET = 9003, 9800, 9920, 123456     # ребёнок Анны, Игоря, группы без педагога, несуществующий

# 14.11.2026, после проверки (R1 №1): «После первого занятия» сохраняет заметку только по ребёнку из
# списка, который сервер сам строит для страницы (karta_razvitiya._kids — первые занятия за два дня из
# МойКласса). На стенде МойКласса нет, список пуст — подставляем выдуманный (main(), pervye_stenda):
# те же дети 9001–9003, по которым проверки и раньше сохраняли заметки.
PERVYE = [
    {"uid": 9001, "name": "Тестова Ева", "phone": lt.SEMYA, "group": "ПШ_вт-пт_17:00", "course": "Подготовка к школе",
     "date": date.today().isoformat(), "time": "17:00", "visit": False},
    {"uid": 9002, "name": "Тестов Марк", "phone": lt.SEMYA, "group": "ПШ_вт-пт_16:00", "course": "Подготовка к школе",
     "date": date.today().isoformat(), "time": "16:00", "visit": False},
    {"uid": 9003, "name": "Пробная Алиса", "phone": lt.KLIENT, "group": "АЯ_пн-ср_16:00", "course": "Английский язык",
     "date": date.today().isoformat(), "time": "16:00", "visit": False},
]
CHUZHOY_NOMER = "79990007777"                                # выдуманный номер «из запроса»


def pervye_stenda(days: int = 2) -> dict:
    return {"kids": [dict(x) for x in PERVYE],
            "teachers": sorted(n for _, n, r_, w in SOTRUDNIKI if 63600 in r_ and w)}


# --- обвязка ---------------------------------------------------------------------------------------

def kl(adres: str | None = None, **kw):
    from fastapi.testclient import TestClient
    if adres:     # запрос «через Caddy»: адрес посетителя — последний в X-Forwarded-For
        return TestClient(m.app, base_url=B, follow_redirects=False, client=("127.0.0.1", 50000),
                          headers={"x-forwarded-for": adres}, **kw)
    return TestClient(m.app, base_url=B, follow_redirects=False, **kw)


def voyti(c, login, parol, next_="/owner"):
    return c.post("/vhod", data={"login": login, "parol": parol, "next": next_}, headers=SVOI)


def vladelec():
    """Браузер владельца, вошедшего через форму."""
    c = kl()
    r = voyti(c, *VLADELEC)
    assert r.status_code == 303, r.status_code
    return c


def pd(c, deystvie, moy=VLADELEC[1], hdr=None, **kw):
    """Запрос из блока «Ключи педагогов»: POST /owner/pedagogi со своей страницы."""
    data = {"deystvie": deystvie}
    if moy is not None:
        data["moy_parol"] = moy
    return c.post("/owner/pedagogi", data=data, headers=hdr or SVOI, **kw)


def ssylki(r) -> list:
    """Ссылки со страницы ответа (показываются один раз). Значения не печатаем."""
    return re.findall(r"<textarea class='ped-ssylka' readonly[^>]*>([^<]+)</textarea>", r.text)


def klyuch_iz(r) -> str:
    x = ssylki(r)
    k = x[0].split("?k=", 1)[1] if x and "?k=" in x[0] else ""
    if k:
        SEKRETY.append(k)
    return k


def vydat(c, mk_id) -> str:
    return klyuch_iz(pd(c, f"vydat:{mk_id}"))


def chasy(sdvig: float):
    m.vhod.SDVIG = sdvig


def baza_vhodov():
    con = sqlite3.connect(m.config.VHODY_DB)
    con.row_factory = sqlite3.Row
    return con


def osnovnaya():
    con = sqlite3.connect(m.config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def stroki() -> dict:
    """Таблица ключей: имя (или 'obshchiy') → строка."""
    with baza_vhodov() as b:
        return {(r["imya"] or r["vid"]): dict(r) for r in b.execute("SELECT * FROM pedagog_klyuchi")}


def koren(html: str) -> str:
    """Шапка страницы: от id="kidsup-nav" до конца её блока."""
    i = html.find('id="kidsup-nav"')
    return html[i:html.find("</div></div>", i)] if i >= 0 else ""


def punktov(h: str) -> int:
    """Пунктов-ссылок в шапке педагога (обёртка span.kn-ped-punkty — не пункт)."""
    return len(re.findall(r'<a class="kn-ped-punkt[ "]', h))


def deti_stranicy(html: str) -> set:
    return {int(x) for x in re.findall(r'<input class="n" data-uid="(\d+)"', html)}


def deti_api(r) -> set:
    return {d["user_id"] for d in r.json()["дети"]} if r.status_code == 200 else {r.status_code}


def vse_deti() -> set:
    return {u for g in DETI.values() for u in g}


def zasejat_pedagogov(baza: str):
    """Сотрудники с ролями и занятия групп английского с преподавателями — вокруг сегодняшнего
    дня (карточка речи считает от настоящей даты, не от часов автопилота)."""
    c = sqlite3.connect(baza)
    c.executemany("INSERT OR REPLACE INTO managers (id, name, raw) VALUES (?,?,?)",
                  [(i, n, json.dumps({"id": i, "name": n, "roles": r, "isWork": w}, ensure_ascii=False))
                   for i, n, r, w in SOTRUDNIKI])
    t, zan, nomer = date.today(), [], [70000]

    def zanyatie(den, klass, pedagogi):
        nomer[0] += 1
        zan.append((nomer[0], den.isoformat(), "16:00", klass,
                    json.dumps({"id": nomer[0], "classId": klass, "date": den.isoformat(), "teacherIds": pedagogi})))

    zameny = 0
    for sdvig in range(-27, 28):
        d = t + timedelta(days=sdvig)
        wd = d.weekday()
        if wd in (0, 2):
            zanyatie(d, 112, [ANNA])                      # АЯ пн-ср Гр1 — Анна
        if wd in (1, 3):
            if zameny < 1:
                zameny += 1
                zanyatie(d, 105, [ANNA])                  # одна замена: Анна раз провела группу Игоря
            else:
                zanyatie(d, 105, [IGOR])                  # АЯ вт-чт Гр6 — Игорь
            zanyatie(d, 118, [])                          # АЯ вт-чт Гр5 — преподаватель в занятиях не указан
        if wd < 5:
            zanyatie(d, 106, [VERA, ANNA] if wd in (0, 2) else [VERA])     # мини-сад: английский пн и ср
            zanyatie(d, 107, [DARYA, ANNA] if wd in (0, 2) else [DARYA])   # нулевой класс
    c.executemany("INSERT OR REPLACE INTO lessons (id, date, begin_time, class_id, raw) VALUES (?,?,?,?,?)", zan)
    c.commit()
    c.close()


# --- А. До действий владельца ----------------------------------------------------------------------

def karta_zaprosy(c, k, uid, hdr=None) -> dict:
    """Все запросы карточки речи по одному ребёнку → {название: код}. Отправку родителю не
    вызываем: у /otpravit проверяем только доступ (неизвестный вид сообщения → 400 до отправки)."""
    q = f"?k={k}" if k else ""
    h = hdr or {}
    out = {
        "отметки": c.post(f"/api/english/karta{q}", json={"user_id": uid, "tochka": "t2", "marks": {"komanda": "да"}}, headers=h),
        "видео-отметка": c.post(f"/api/english/karta/video{q}", json={"user_id": uid, "date": "2026-11-10"}, headers=h),
        "заметка": c.post(f"/api/english/karta/note{q}", json={"user_id": uid, "note": "стенд"}, headers=h),
        "видео-файл": c.post(f"/api/english/karta/video-upload{q}{'&' if q else '?'}user_id={uid}&ext=mp4", content=b"x" * 100, headers=h),
        "видео-удалить": c.post(f"/api/english/karta/video-delete{q}", json={"user_id": uid, "token": "netu"}, headers=h),
        "черновик": c.post(f"/api/english/karta/chernovik{q}", json={"user_id": uid, "tochka": "netu", "vvod": {}}, headers=h),
        "вводное": c.get(f"/api/english/karta/vvodnoe{q}{'&' if q else '?'}user_id={uid}", headers=h),
        "отправить": c.post(f"/api/english/karta/otpravit{q}", json={"user_id": uid, "kind": "netu"}, headers=h),
    }
    return {n: r.status_code for n, r in out.items()}


# что отвечают запросы карточки тому, кого пустили (сами обработчики, на пустых данных стенда)
PUSTILI = {"отметки": 200, "видео-отметка": 200, "заметка": 200, "видео-файл": 400, "видео-удалить": 404,
           "черновик": 400, "вводное": 200, "отправить": 400}
OTKAZ = dict.fromkeys(PUSTILI, 403)


def test_kak_ranshe():
    print("А. Пока владелец ничего не сделал: общий ключ работает на обеих страницах и во всех запросах")
    c = kl("198.51.100.1")
    r = c.get(f"/pedagog?k={PED}", headers=STR)
    proverka("/pedagog по общему ключу → 200, форма трёх строк", r.status_code == 200 and 'id="save"' in r.text
             and "Ссылка недействительна" not in r.text, r.status_code)
    cfg = json.loads(re.search(r"const CFG=(\{.*?\}); const \$", r.text, re.S).group(1))
    proverka("…педагог выбирается из списка, как раньше (имя не подставлено)", "ya" not in cfg and cfg["k"] == PED)
    proverka("…ни Cache-Control: no-store, ни X-Frame-Options общему ключу не добавились",
             "cache-control" not in r.headers and "x-frame-options" not in r.headers, dict(r.headers))
    r = c.get(f"/english/karta?k={PED}", headers=STR)
    proverka("/english/karta по общему ключу → 200, все дети всех групп английского",
             r.status_code == 200 and deti_stranicy(r.text) == vse_deti(), (r.status_code, len(deti_stranicy(r.text))))
    proverka("…кнопки «по педагогу» на месте, приписки о личной ссылке нет",
             'data-p="Маша"' in r.text and 'id="lichnyy"' not in r.text and "KIDSUP_PED = true;" in r.text)
    proverka("…заголовки ответа прежние", "cache-control" not in r.headers and "x-frame-options" not in r.headers)
    proverka("GET /api/english/karta по общему ключу → все дети",
             deti_api(c.get(f"/api/english/karta?k={PED}")) == vse_deti())
    proverka("…и с ключом в заголовке X-Pedagog-Key",
             deti_api(c.get("/api/english/karta", headers={"X-Pedagog-Key": PED})) == vse_deti())
    for uid, chey in ((SVOY, "Гр1"), (CHUZHOY, "Гр6"), (NICHEY, "Гр5, без педагога")):
        z = karta_zaprosy(c, PED, uid)
        proverka(f"общий ключ: все восемь запросов карточки принимаются — ребёнок {uid} ({chey})", z == PUSTILI, z)
    with osnovnaya() as o:
        avtor = o.execute("SELECT author FROM speech_cards WHERE user_id=? AND tochka='t2'", (SVOY,)).fetchone()[0]
    proverka("…автор отметки по общему ключу — как раньше (что прислали: пусто)", avtor == "")
    r = c.post("/api/pedagog/save", json={"k": PED, "uid": 9001, "child": "Ева", "teacher": "Образцов Игорь",
                                          "visit": True, "can": "стенд: умеет", "next_group": "стенд: группа"})
    with osnovnaya() as o:
        z = o.execute("SELECT teacher, can FROM karta_notes ORDER BY id DESC LIMIT 1").fetchone()
    proverka("POST /api/pedagog/save по общему ключу → ok, педагог — тот, кого выбрали в списке",
             r.status_code == 200 and r.json().get("ok") is True and tuple(z) == ("Образцов Игорь", "стенд: умеет"),
             (r.status_code, r.text[:120]))
    # неверный ключ — те же ответы, что были всегда
    r = c.get("/pedagog?k=ne-tot", headers=STR)
    proverka("неверный ключ: /pedagog → 200 «Ссылка недействительна», без шапки",
             r.status_code == 200 and "Ссылка недействительна" in r.text and 'id="kidsup-nav"' not in r.text)
    proverka("…/english/karta скрипту → 401 с окном, как раньше",
             c.get("/english/karta?k=ne-tot").status_code == 401
             and c.get("/api/english/karta?k=ne-tot").headers.get("www-authenticate") == "Basic")
    r = c.post("/api/pedagog/save", json={"k": "ne-tot", "uid": 1, "teacher": "x"})
    proverka("…POST /api/pedagog/save → 200 {ok: false, «ключ не подходит»}",
             r.status_code == 200 and r.json() == {"ok": False, "error": "ключ не подходит"}, r.text[:100])
    proverka("без ключа: /pedagog → «Ссылка недействительна»; карточка — 401",
             "Ссылка недействительна" in c.get("/pedagog").text and c.get("/english/karta").status_code == 401)
    proverka("таблица ключей пуста: общий ключ — прежний, из настройки pedagog_key",
             stroki() == {} and m.db.get_setting("pedagog_key") == PED and not pk._snyat())
    s = pk.sostoyanie()
    proverka("состояние: общий — «прежний», работает; личных ключей нет",
             s["obshchiy"]["sostoyanie"] == "prezhniy" and s["obshchiy"]["rabotaet"] and s["s_klyuchom"] == 0)
    nastr = kl().get("/api/settings", auth=VLADELEC)
    proverka("/api/settings: общий ключ целиком больше не отдаётся (только хвост и длина)",
             nastr.status_code == 200 and PED not in nastr.text and nastr.json()["pedagog_key"].startswith("…"),
             nastr.status_code)


def cfg_stranicy(t: str) -> dict:
    return json.loads(re.search(r"const CFG=(\{.*?\}); const \$", t, re.S).group(1))


def test_spisok_pervyh():
    print("А2. «После первого занятия»: без телефонов на странице; сохранить можно только ребёнка из списка сервера")
    c = kl("198.51.100.3")
    r = c.get(f"/pedagog?k={PED}", headers=STR)
    cfg = cfg_stranicy(r.text)
    proverka("в данных страницы нет телефонов родителей — ни поля phone, ни самих номеров",
             cfg["kids"] and all("phone" not in x for x in cfg["kids"]) and lt.SEMYA not in r.text and lt.KLIENT not in r.text,
             sorted(cfg["kids"][0]) if cfg["kids"] else "пусто")
    proverka("…а страница по-прежнему отправляет ребёнка по uid (сохранение без телефона на странице)",
             "uid:cur.uid" in r.text and "phone:cur.phone" not in r.text)
    with osnovnaya() as o:
        do = (o.execute("SELECT COUNT(*) FROM karta_notes").fetchone()[0], o.execute("SELECT COUNT(*) FROM plan_inbox").fetchone()[0])
    r = c.post("/api/pedagog/save", json={"k": PED, "uid": 555777, "child": "Чужой", "teacher": "Образцов Игорь", "visit": True,
                                          "can": "x", "next_group": "y", "phone": CHUZHOY_NOMER, "date": "2026-01-01", "time": "10:00"})
    with osnovnaya() as o:
        posle = (o.execute("SELECT COUNT(*) FROM karta_notes").fetchone()[0], o.execute("SELECT COUNT(*) FROM plan_inbox").fetchone()[0])
    proverka("общий ключ, ребёнка нет в списке первых занятий → понятный отказ; ни заметки, ни пункта дежурной",
             r.status_code == 200 and r.json().get("ok") is False and "нет в списке первых занятий" in r.json().get("error", "")
             and posle == do, (r.text[:120], do, posle))
    r = c.post("/api/pedagog/save", json={"k": PED, "uid": "9001", "child": "Чужое имя", "teacher": "Образцов Игорь", "visit": True,
                                          "can": "стенд: умеет", "next_group": "стенд: группа", "phone": CHUZHOY_NOMER,
                                          "group": "Чужая группа", "course": "Чужое", "date": "2026-01-01", "time": "10:00"})
    with osnovnaya() as o:
        z = o.execute("SELECT uid, child, phone, course, grp, teacher FROM karta_notes ORDER BY id DESC LIMIT 1").fetchone()
        inbox = o.execute("SELECT text, phone FROM plan_inbox ORDER BY id DESC LIMIT 1").fetchone()
    proverka("ребёнок из списка: телефон, имя, группа и направление — из списка сервера, присланные — нет",
             r.json().get("ok") is True and tuple(z) == (9001, "Ева", lt.SEMYA, "Подготовка к школе", "ПШ_вт-пт_17:00", "Образцов Игорь"),
             z and tuple(z))
    proverka("…пункт дежурной — с телефоном из списка, не из запроса",
             inbox is not None and inbox["phone"] == lt.SEMYA and CHUZHOY_NOMER not in inbox["text"], inbox and inbox["phone"])
    r = c.post("/api/pedagog/save", json={"k": PED, "uid": 0, "teacher": "Образцов Игорь", "visit": False})
    proverka("…без ребёнка — прежний отказ «нужны ребёнок и педагог»",
             r.json() == {"ok": False, "error": "нужны ребёнок и педагог"}, r.text[:100])


def test_perebor_prezhniy():
    print("А3. Прежний общий ключ до действий владельца: пауза ему не отказывает, промахи считаются, тревога приходит")
    adr = "203.0.113.20"
    x = kl(adr)
    for i in range(5):
        x.get(f"/pedagog?k=prezh{i}{secrets.token_hex(8)}")
    with baza_vhodov() as b:
        n5 = b.execute("SELECT COUNT(*) FROM popytki WHERE login=? AND adres=?", (pk.SCHET, adr)).fetchone()[0]
    proverka("пять разных неверных ключей с адреса записаны промахами", n5 == 5, n5)
    proverka("в паузу верный прежний общий ключ, впервые с этого адреса, работает как раньше: обе страницы, запросы, сохранение",
             rabotaet(PED, adr) == DA, rabotaet(PED, adr))
    x.get(f"/pedagog?k=prezh-v-pauzu{secrets.token_hex(8)}")
    with baza_vhodov() as b:
        n6 = b.execute("SELECT COUNT(*) FROM popytki WHERE login=? AND adres=?", (pk.SCHET, adr)).fetchone()[0]
    proverka("…неверный ключ в паузу тоже считается промахом (было: в паузу промахи не считались)", n6 == 6, n6)
    proverka("…удача прежнего общего ключа в паузу промахи адреса не стирает (как у серого окна)", n6 == 6)
    chasy(200)
    try:
        ok = rabotaet(PED, adr) == DA
        with baza_vhodov() as b:
            n0 = b.execute("SELECT COUNT(*) FROM popytki WHERE login=? AND adres=?", (pk.SCHET, adr)).fetchone()[0]
    finally:
        chasy(0)
    proverka("после паузы удача прежнего общего ключа стирает промахи адреса — как было", ok and n0 == 0, (ok, n0))
    # тревога: 50 разных неверных ключей с одного адреса за час — раньше пауза глушила счёт после пятого
    soobshcheniya = []
    bylo = m.vhod.soobshchit_borisu
    m.vhod.soobshchit_borisu = lambda tekst, chto="": soobshcheniya.append((tekst, chto))
    try:
        y = kl("203.0.113.21")
        for i in range(52):
            y.get(f"/pedagog?k=trevoga{i}{secrets.token_hex(8)}")
    finally:
        m.vhod.soobshchit_borisu = bylo
    proverka("52 разных неверных ключа с одного адреса за час → одно сообщение владельцу о неверных ключах педагогов",
             len(soobshcheniya) == 1 and "неверных ключей педагогов" in soobshcheniya[0][0]
             and not any(PED in t for t, _ in soobshcheniya), len(soobshcheniya))
    proverka("…и всё это время верный прежний общий ключ с того же адреса работает",
             rabotaet(PED, "203.0.113.21") == DA)


def test_zamena_i_sad():
    print("Б. Чьи группы: по преподавателю в занятиях МойКласса")
    from app import karta
    with m.db.get_conn() as c:
        ped = karta.pedagogi_grupp(c)
    proverka("Гр1 (пн-ср) — Анна; Гр6 (вт-чт) — Игорь: одна замена Анны группу ей не открывает",
             ped[GR1] == {ANNA} and ped[GR6] == {IGOR}, (ped[GR1], ped[GR6]))
    proverka("мини-сад и нулевой класс: английский ведёт Анна, воспитатели (все дни недели) — не педагоги английского",
             ped[SAD] == {ANNA} and ped[NUL] == {ANNA}, (ped[SAD], ped[NUL]))
    proverka("Гр5: преподаватель в занятиях не указан — педагог не определён", ped[GR5] == set())
    proverka("группы педагога: Анна — три, Игорь — одна, Ольга — ни одной",
             karta.gruppy_pedagoga([ANNA]) == {GR1, SAD, NUL} and karta.gruppy_pedagoga([IGOR]) == {GR6}
             and karta.gruppy_pedagoga([OLGA]) == set() and karta.gruppy_pedagoga([]) == set())
    with osnovnaya() as o:      # занятия Игоря в Гр6 «удалены в МойКлассе» — остаётся одно, Анны
        o.execute("CREATE TABLE IF NOT EXISTS mk_udaleno (tbl TEXT, rid INTEGER, ts TEXT, istochnik TEXT, PRIMARY KEY (tbl, rid))")
        ids = [r[0] for r in o.execute("SELECT id FROM lessons WHERE class_id=105 AND raw LIKE ?", (f"%[{IGOR}]%",))]
        o.executemany("INSERT OR IGNORE INTO mk_udaleno (tbl, rid, ts, istochnik) VALUES ('lessons', ?, '', 'стенд')", [(i,) for i in ids])
    with m.db.get_conn() as c:
        bez = karta.pedagogi_grupp(c)[GR6]
    with osnovnaya() as o:
        o.execute("DELETE FROM mk_udaleno WHERE istochnik='стенд'")
    # 14.11.2026, после проверки (R1 №2): было «остаётся Анна» — одно её занятие открывало группу. Теперь
    # педагогу нужно не меньше двух занятий и больше четверти всех занятий окна: после «удаления» занятий
    # Игоря одно занятие Анны группу не даёт никому — и Игоря среди педагогов нет (удалённые не считаются).
    proverka("занятия, удалённые в МойКлассе, не считаются: Игоря нет, а одно занятие Анны группу не даёт",
             len(ids) > 5 and bez == set(), (len(ids), bez))
    spisok = {p["mk_id"] for p in pk.pedagogi_spisok()}
    proverka("список педагогов: работающие с ролью «педагог»; уволенной и администратора нет",
             spisok == {ANNA, IGOR, OLGA, VERA, DARYA}, spisok)


class _Zanyatiya:
    """Занятия одной группы стенда на время проверки: прежние убираются, после — возвращаются."""

    def __init__(self, klass: int, zan: list):
        self.klass, self.zan, self.bylo = klass, zan, []

    def __enter__(self):
        with osnovnaya() as o:
            cur = o.execute("SELECT * FROM lessons WHERE class_id=?", (self.klass,))
            self.stolbcy = [d[0] for d in cur.description]
            self.bylo = [tuple(r) for r in cur]
            o.execute("DELETE FROM lessons WHERE class_id=?", (self.klass,))
            o.executemany("INSERT INTO lessons (id, date, begin_time, class_id, raw) VALUES (?,?,?,?,?)",
                          [(80000 + i, d, "16:00", self.klass,
                            json.dumps({"id": 80000 + i, "classId": self.klass, "date": d, "teacherIds": t}))
                           for i, (d, t) in enumerate(self.zan)])
        return self

    def __exit__(self, *a):
        with osnovnaya() as o:
            o.execute("DELETE FROM lessons WHERE class_id=?", (self.klass,))
            o.executemany(f"INSERT INTO lessons ({','.join(self.stolbcy)}) VALUES ({','.join('?' * len(self.stolbcy))})", self.bylo)


def _pedagogi(gruppa: str) -> set:
    from app import karta
    with m.db.get_conn() as c:
        return karta.pedagogi_grupp(c)[gruppa]


def test_dolya_i_sad():
    print("Б2. Чьи группы после проверки: доля — от всех занятий, замена группу не даёт, в саду — сначала ведущие «АЯ»")
    t = date.today()
    dni = [(t + timedelta(days=s)).isoformat() for s in range(-24, 24, 3)][:16]      # 16 занятий в окне ±28 дней
    # R1 №2: в Гр5 преподаватель не указан ни в одном занятии; одно занятие поставили Ольге
    with _Zanyatiya(118, [(d, [OLGA] if i == 0 else []) for i, d in enumerate(dni)]):
        p = _pedagogi(GR5)
    proverka("одно занятие из 16 (в остальных преподавателя нет) — группу не даёт; группа по-прежнему «без педагога»",
             p == set(), p)
    # R2 №10: двухнедельная замена в группе дважды в неделю — 4 занятия из 16, ровно четверть
    with _Zanyatiya(112, [(d, [IGOR] if 4 <= i < 8 else [ANNA]) for i, d in enumerate(dni)]):
        p = _pedagogi(GR1)
    proverka("замена на две недели (4 из 16) группу заменяющему не даёт; основной педагог — на месте", p == {ANNA}, p)
    with _Zanyatiya(112, [(d, [IGOR] if i < 5 else [ANNA]) for i, d in enumerate(dni)]):
        p = _pedagogi(GR1)
    proverka("…а 5 из 16 (больше четверти) — уже два педагога группы", p == {ANNA, IGOR}, p)
    with _Zanyatiya(112, [(d, [IGOR] if i < 1 else []) for i, d in enumerate(dni[:3])]):
        p = _pedagogi(GR1)
    proverka("…и одно занятие из трёх (треть, но меньше двух занятий) — тоже нет", p == set(), p)
    # R2 №11: плохие значения в teacherIds пропускаются по одному, а не всё занятие
    with _Zanyatiya(105, [(d, [None, IGOR] if i % 3 == 0 else (IGOR if i % 3 == 1 else ["x", str(IGOR)]))
                          for i, d in enumerate(dni)]):
        p = _pedagogi(GR6)
    proverka("teacherIds с None, числом вместо списка и мусорной строкой: Игорь — педагог Гр6", p == {IGOR}, p)
    # R2 №2: в окне у сада заведены только пн/ср, в каждом — воспитатель и педагог английского
    pnsr = [(t + timedelta(days=s)).isoformat() for s in range(-27, 28) if (t + timedelta(days=s)).weekday() in (0, 2)]
    with _Zanyatiya(106, [(d, [VERA, ANNA]) for d in pnsr]):
        p = _pedagogi(SAD)
    proverka("сад, в окне только пн/ср (воспитатель и педагог в каждом): педагог сада — тот, кто ведёт группы «АЯ»",
             p == {ANNA}, p)
    vse = [(t + timedelta(days=s)).isoformat() for s in range(-27, 28) if (t + timedelta(days=s)).weekday() < 5]
    zan = []
    for d in vse:
        zan.append((d, [VERA]))
        if date.fromisoformat(d).weekday() in (0, 2):
            zan += [(d, [ANNA]), (d, [OLGA])]                 # английский и другой специалист пн/ср — отдельными занятиями
    with _Zanyatiya(106, zan):
        p = _pedagogi(SAD)
    proverka("сад: другой специалист пн/ср (не ведёт «АЯ») в педагоги английского не попадает", p == {ANNA}, p)


# --- В. Шапка (Т2) -----------------------------------------------------------------------------------

def test_shapka():
    print("В. Шапка педагога: два пункта, оба с проверенным ключом, текущий подсвечен")
    c = kl("198.51.100.2")
    for adres, tut in (("/english/karta", "Карточка речи"), ("/pedagog", "После первого занятия")):
        r = c.get(f"{adres}?k={PED}", headers=STR)
        h = koren(r.text)
        proverka(f"{adres}: шапка одна — «KidsUP · педагогам», два пункта",
                 r.text.count('id="kidsup-nav"') == 1 and "педагогам" in h and punktov(h) == 2
                 and "Карточка речи" in h and "После первого занятия" in h and h.count("<a ") == 3
                 and '<span class="kn-ped-punkty">' in h, h[:300].replace(PED, "<ключ>"))
        proverka(f"{adres}: оба пункта ведут с ключом; логотип — на карточку",
                 f'href="/english/karta?k={PED}"' in h and f'href="/pedagog?k={PED}"' in h
                 and h.count(f"?k={PED}") == 3)
        x = re.search(r'<a class="kn-ped-punkt kn-tut"[^>]*aria-current="page">([^<]+)</a>', h)
        proverka(f"{adres}: подсвечен текущий пункт — «{tut}», и только он",
                 x is not None and x.group(1) == tut and h.count("kn-tut") == 1, x and x.group(1))
        proverka(f"{adres}: ни меню админов, ни поиска, ни бейджа, ни панели, ни скрипта; шапка ничего не опрашивает",
                 all(s not in r.text for s in ('id="nav-razdely"', 'id="gs-input"', 'id="wait-badge"', "kn-panel",
                                               "/static/nav.js", "Владельцу", 'href="/pult"', 'href="/liniya"'))
                 and "/api/" not in h and "data-schet" not in h and "<script" not in h)
    r = c.get("/pedagog?k=chuzhoy-kluch", headers=STR)
    proverka("чужая строка вместо ключа в шапку и в страницу не попадает",
             'id="kidsup-nav"' not in r.text and "chuzhoy-kluch" not in r.text)
    r = kl().get("/english/karta?k=chuzhoy-kluch", auth=OBSHCHIY_ADMIN)
    proverka("админ с чужой строкой в ?k=: шапка админа, строка в шапку не попала",
             r.status_code == 200 and 'id="nav-razdely"' in r.text and "chuzhoy-kluch" not in koren(r.text))


def test_referer_log_css():
    print("В2. Ключ в адресе: без Referer чужим сайтам, звёздочки в журнале доступа; шапка на 320 px")
    c = kl("198.51.100.4")
    for adres in (f"/pedagog?k={PED}", f"/english/karta?k={PED}", f"/english/metodika?k={PED}", "/pedagog?k=ne-tot"):
        r = c.get(adres, headers=STR)
        proverka(f"{adres.replace(PED, '<ключ>')}: Referrer-Policy: no-referrer",
                 r.headers.get("referrer-policy") == "no-referrer", (r.status_code, r.headers.get("referrer-policy")))
    t = c.get(f"/english/karta?k={PED}", headers=STR).text
    proverka("карточка: запросы на свой сервер — с referrerPolicy 'same-origin' (иначе старый Safari шлёт «Origin: null»)",
             t.count("referrerPolicy: 'same-origin'") == 3)
    t = c.get(f"/pedagog?k={PED}", headers=STR).text
    proverka("«После первого занятия»: сохранение — тоже с referrerPolicy 'same-origin'", "referrerPolicy:'same-origin'" in t)
    # журнал доступа uvicorn: путь со строкой запроса — ключ звёздочками
    zap = []

    class _H(logging.Handler):
        def emit(self, record):
            zap.append(self.format(record))
    h = _H()
    h.setFormatter(logging.Formatter("%(message)s"))
    lg = logging.getLogger("uvicorn.access")
    lg.addHandler(h)
    bylo = lg.level
    lg.setLevel(logging.INFO)
    try:
        lg.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:50000", "GET", f"/english/karta?x=1&k={PED}&y=2", "1.1", 200)
        lg.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:50000", "POST", f"/api/english/karta/note?k={PED}", "1.1", 200)
    finally:
        lg.removeHandler(h)
        lg.setLevel(bylo)
    proverka("журнал доступа uvicorn: значение k=… заменено звёздочками, остальной адрес на месте",
             len(zap) == 2 and all(PED not in z for z in zap) and "/english/karta?x=1&k=***&y=2" in zap[0]
             and "/api/english/karta/note?k=***" in zap[1], [z.replace(PED, "<КЛЮЧ>") for z in zap])
    proverka("…описание модуля больше не обещает «ни в лог»", "ни в журнал, ни в лог" not in (pk.__doc__ or ""))
    css = Path(KOREN, "app", "static", "nav.css").read_text(encoding="utf-8")
    mob = css[css.index("@media (max-width: 919.98px)"):]
    proverka("nav.css: на телефоне пункты шапки педагога — текст по центру кнопки (flex, align-items/justify-content center)",
             re.search(r"\.kn-ped-punkty \.kn-ped-punkt \{[^}]*display: flex;[^}]*align-items: center;[^}]*justify-content: center",
                       mob) is not None)
    proverka("…на 320 px (max-width: 359px) шрифт пунктов мельче; версия файлов шапки — 4",
             re.search(r"@media \(max-width: 359px\) \{[^@]*\.kn-ped-punkt \{[^}]*font-size: 13px", css) is not None
             and m.nav.VERSIYA == "4")


# --- Г. Блок владельца ---------------------------------------------------------------------------------

def test_kto_upravlyaet():
    print("Г. Блок «Ключи педагогов» на /owner: только владелец через форму, с подтверждением паролем")
    do = stroki()
    r = kl().get("/owner", auth=VLADELEC)
    proverka("/owner в сером окне (прежний boris): вместо блока — ссылка «войти через форму»",
             r.status_code == 200 and "id='pedagogi'" in r.text and "id='ped-forma'" not in r.text
             and "Управлять ключами педагогов" in r.text, r.status_code)
    c = vladelec()
    r = c.get("/owner", headers=STR)
    proverka("у владельца через форму — блок: общий ключ, личные ключи, пять педагогов с кнопкой «Выдать ключ»",
             r.status_code == 200 and "id='ped-forma'" in r.text and "Общий ключ" in r.text
             and r.text.count(">Выдать ключ</button>") == 5 and all(n in r.text for _, n, _, w in SOTRUDNIKI[:5]),
             (r.status_code, r.text.count(">Выдать ключ</button>")))
    proverka("…уволенной и администратора в списке нет", "Бывшая Зоя" not in r.text and "Стойкина Нина" not in r.text)
    proverka("…видно, кто что увидит: у Анны три группы и 16 детей, у Игоря одна; у троих групп английского нет",
             "Мини-сад, Нулевой класс, пн-ср 16:00 — 16 детей" in r.text and "вт-чт 17:00 — 2 ребёнка" in r.text
             and r.text.count("групп английского нет") == 3)
    proverka("…предупреждение: у 1 группы английского не определён педагог (вт-чт 16:00)",
             "id='ped-bez'" in r.text and "У 1 группы английского не определён педагог" in r.text
             and "вт-чт 16:00" in r.text.split("id='ped-bez'")[1][:400])
    # 14.11.2026, после проверки (R2 №8): было «кнопки „Сменить“ и „Выключить“». Пока не выдано ни одной
    # личной ссылки, «Выключить» оставила бы всех педагогов без страниц — кнопки нет, сервер отказывает.
    proverka("…общий ключ: «работает прежний»; кнопка «Сменить»; «Выключить» нет — личных ключей ещё ни одного",
             "Работает прежний" in r.text and "value='obshchiy_smenit'" in r.text and "value='obshchiy_vykl'" not in r.text
             and "сейчас ни одной действующей нет" in r.text)
    proverka("…на странице нет ни общего ключа, ни хешей", PED not in r.text and "sha256$" not in r.text)
    proverka("…одна фраза о том, что видно по личной ссылке на «После первого занятия»: дети первых занятий, без телефонов",
             "по личной ссылке видны дети на первых занятиях всего центра" in r.text and "без телефонов родителей" in r.text)
    r = pd(c, "obshchiy_vykl")
    proverka("«Выключить общий ключ» при 0 личных ключей → отказ с объяснением; общий ключ работает",
             r.status_code == 400 and "ни одной действующей личной ссылки не выдано" in r.text and stroki() == do
             and rabotaet(PED) == DA, r.status_code)
    r = c.get("/owner/pedagogi", headers=STR)
    proverka("GET /owner/pedagogi (обновили страницу ответа без повторной формы) → 303 на /owner#pedagogi, не 405",
             r.status_code == 303 and r.headers.get("location") == "/owner#pedagogi", r.status_code)

    r = pd(c, f"vydat:{ANNA}", moy=None)
    proverka("без moy_parol → отказ словами, ничего не меняется",
             r.status_code == 400 and "Введите ваш нынешний пароль" in r.text and not ssylki(r) and stroki() == do, r.status_code)
    r = pd(c, f"vydat:{ANNA}", moy="ne-tot-" + secrets.token_hex(4))
    proverka("с неверным moy_parol → отказ, ничего не меняется",
             r.status_code == 400 and "не подошёл" in r.text and not ssylki(r) and stroki() == do, r.status_code)
    r = pd(c, f"vydat:{ANNA}", moy=OBSHCHIY_ADMIN[1])
    proverka("общий пароль admin подтверждением не служит", r.status_code == 400 and stroki() == do)
    r = kl().post("/owner/pedagogi", data={"deystvie": f"vydat:{ANNA}", "moy_parol": VLADELEC[1]}, auth=VLADELEC)
    proverka("прежний boris в сером окне (скрипт) → 403, даже с верным moy_parol", r.status_code == 403 and stroki() == do,
             r.status_code)
    r = kl().post("/owner/pedagogi", data={"deystvie": f"vydat:{ANNA}", "moy_parol": VLADELEC[1]}, auth=OBSHCHIY_ADMIN)
    proverka("общий admin → не пускает (401 с окном, как на всех страницах владельца)",
             r.status_code == 401 and stroki() == do, r.status_code)
    lena = kl()
    voyti(lena, "lena", LENA, next_="/pult")
    r = pd(lena, f"vydat:{ANNA}", moy=LENA)
    proverka("личный админ (Лена через форму) выдать ключ не может → 403", r.status_code == 403 and stroki() == do, r.status_code)
    r = pd(lena, f"vydat:{ANNA}", moy=VLADELEC[1])
    proverka("…и с паролем владельца в поле — тоже 403", r.status_code == 403 and stroki() == do, r.status_code)
    proverka("…блока «Ключи педагогов» админ не видит: /owner ему закрыт (403)",
             lena.get("/owner", headers=STR).status_code == 403)
    proverka("…и состояние ключей админу закрыто", lena.get("/api/pedagogi/sostoyanie", headers=FETCH).status_code == 403)
    d = kl()
    d.cookies.set(m.vhod.COOKIE, c.cookies.get(m.vhod.COOKIE), domain="app.kidsup.ru")
    r = d.post("/owner/pedagogi", data={"deystvie": f"vydat:{ANNA}", "moy_parol": VLADELEC[1]},
               headers={"origin": "https://chuzhoy.example", "user-agent": UA})
    proverka("с чужого сайта (метка владельца, чужой Origin) → 403", r.status_code == 403 and stroki() == do, r.status_code)
    r = d.post("/owner/pedagogi", data={"deystvie": "obshchiy_vykl", "moy_parol": VLADELEC[1]},
               headers={"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate", "user-agent": UA})
    proverka("…форма с чужого сайта (Sec-Fetch-Site: cross-site) → 403, общий ключ на месте",
             r.status_code == 403 and stroki() == do and pk.proverit(PED) is not None, r.status_code)
    r = d.post("/owner/pedagogi", data={"deystvie": f"vydat:{ANNA}", "moy_parol": VLADELEC[1]}, headers={"user-agent": "curl/8"})
    proverka("…скрипт с меткой владельца, но без Origin и Sec-Fetch-Site → 403", r.status_code == 403 and stroki() == do,
             r.status_code)
    proverka("без входа → 401", kl().post("/owner/pedagogi", data={"deystvie": f"vydat:{ANNA}"}).status_code == 401)
    r = pd(c, "pridumannoe")
    proverka("неизвестное действие → отказ", r.status_code == 400 and "Неизвестное действие" in r.text and stroki() == do)
    for plohoy in (f"vydat:{ZOYA}", f"vydat:{STOYKA}", "vydat:999999", "vydat:abc", "otklyuchit:999", "otklyuchit:x"):
        r = pd(c, plohoy)
        proverka(f"«{plohoy}»: не из списка педагогов / нет такого ключа → отказ", r.status_code == 400 and not ssylki(r)
                 and stroki() == do, r.status_code)
    with osnovnaya() as o:
        zh = [tuple(x) for x in o.execute("SELECT imya, kod, chto FROM deystviya WHERE put='/owner/pedagogi' ORDER BY id")]
    # отказ до обработчика (403 — не владелец через форму, 401 — общий admin, окно пароля) пишется
    # без пометки «что»: до блока запрос не дошёл
    proverka("отказы записаны в журнал: кто, код, «ключи педагогов: … — отказ»",
             len(zh) >= 8 and all(x[2].startswith("ключи педагогов: ") and x[2].endswith(" — отказ")
                                  or (x[1] in (401, 403) and x[2] == "") for x in zh)
             and any(x[0] == "Лена" and x[1] == 403 for x in zh)
             and any(x[0] == "общий вход" and x[1] == 401 for x in zh), zh[:3])


# --- Д. Выдача: ссылка один раз, ключ не утекает ---------------------------------------------------------

def sledy(sekret: str, c_vlad, gde: str):
    """Ключа нет: в страницах владельца и админа, в состоянии, в настройках, в журнале, в файлах стенда."""
    stranicy = {a: c_vlad.get(a, headers=STR).text for a in ("/owner", "/journal", "/boris/docs")}
    stranicy["/pult (админ)"] = kl().get("/pult", auth=OBSHCHIY_ADMIN).text
    stranicy["/api/pedagogi/sostoyanie"] = c_vlad.get("/api/pedagogi/sostoyanie", headers=FETCH).text
    stranicy["/api/vhod/sostoyanie"] = c_vlad.get("/api/vhod/sostoyanie", headers=FETCH).text
    stranicy["/api/settings"] = kl().get("/api/settings", auth=VLADELEC).text
    plohie = [a for a, t in stranicy.items() if sekret in t]
    proverka(f"{gde}: ключа нет в /owner, журнале, состоянии, /api/settings и на страницах админа",
             not plohie and all(len(t) > 50 for t in stranicy.values()), plohie)
    with osnovnaya() as o:
        zh = " ".join(" ".join(str(x) for x in r_) for r_ in o.execute("SELECT * FROM deystviya"))
        nastr = " ".join(f"{r_[0]}={r_[1]}" for r_ in o.execute("SELECT key, value FROM settings"))
    proverka(f"{gde}: ключа нет в журнале действий и в настройках", sekret not in zh and sekret not in nastr)
    plohie = [f.name for f in sorted(Path(m.config.DB_PATH).parent.iterdir())
              if f.is_file() and sekret.encode() in f.read_bytes()]
    proverka(f"{gde}: ключа нет ни в одном файле стенда побайтно (базы, копия файла входов)", not plohie, plohie)


def test_vydacha() -> dict:
    print("Д. Выдача личного ключа: ссылка показывается один раз, ключ нигде не хранится")
    c = vladelec()
    r = pd(c, f"vydat:{ANNA}")
    ss = ssylki(r)
    ka = klyuch_iz(r)
    proverka("«Выдать ключ» Анне → страница со ссылкой, кнопка «Скопировать», не кешируется",
             r.status_code == 200 and len(ss) == 1 and "class='ped-kop'" in r.text
             and r.headers.get("cache-control") == "no-store", (r.status_code, len(ss)))
    proverka("…ссылка — https://app.kidsup.ru/english/karta?k=…: у Анны есть группы английского",
             ss and ss[0] == f"{B}/english/karta?k={ka}")
    proverka("…ключ — 40 знаков, только латинские буквы и цифры", re.fullmatch(r"[A-Za-z0-9]{40}", ka) is not None, len(ka))
    proverka("…ключ на странице ровно один раз", r.text.count(ka) == 1, r.text.count(ka))
    z = stroki().get("Проверкина Анна") or {}
    proverka("в таблице — только SHA-256 ключа, имя, id сотрудника, когда выдан; включён; ещё не открывал",
             z.get("hesh") == m.vhod.hesh_klyucha(ka) and z.get("mk_id") == ANNA and z.get("vid") == "lichnyy"
             and z.get("vkl") == 1 and z.get("vydan") and z.get("posl") is None, {k_: v for k_, v in z.items() if k_ != "hesh"})
    r2 = c.get("/owner", headers=STR)
    proverka("та же страница, открытая заново: ссылки и ключа уже нет; у Анны «ключ выдан», «ещё не открывал»",
             ka not in r2.text and not ssylki(r2) and "ключ выдан" in r2.text and "ещё не открывал" in r2.text
             and r2.text.count(">Выдать заново</button>") == 1 and r2.text.count(">Отключить</button>") == 1)
    r3 = pd(c, "pridumannoe")
    proverka("…и в ответе на следующее действие её нет", ka not in r3.text and not ssylki(r3))
    sledy(ka, c, "после выдачи")
    with osnovnaya() as o:
        zh = o.execute("SELECT imya, sposob, kod, chto FROM deystviya WHERE put='/owner/pedagogi' AND kod=200 "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    proverka("журнал: «ключи педагогов: выдан ключ — Проверкина Анна», кто — Борис",
             zh is not None and tuple(zh) == ("Борис", "forma", 200, "ключи педагогов: выдан ключ — Проверкина Анна"),
             zh and tuple(zh))
    # R2 №7: одноразовая метка формы — обновили страницу ответа, браузер отправил форму ещё раз
    metka = re.search(r"<input type='hidden' name='metka' value='([^']+)'>", c.get("/owner", headers=STR).text)
    proverka("в форме блока — одноразовая метка действия", metka is not None)
    forma = {"deystvie": f"vydat:{IGOR}", "moy_parol": VLADELEC[1], "metka": metka.group(1) if metka else ""}
    r = c.post("/owner/pedagogi", data=forma, headers=SVOI)
    ki = klyuch_iz(r)
    hesh_igorya = (stroki().get("Образцов Игорь") or {}).get("hesh")
    r2 = c.post("/owner/pedagogi", data=forma, headers=SVOI)
    proverka("повтор той же формы (та же метка): действие не повторено — «уже сделано», ссылки нет, ключ Игоря прежний",
             r.status_code == 200 and ki and r2.status_code == 200 and "Уже сделано" in r2.text and not ssylki(r2)
             and (stroki().get("Образцов Игорь") or {}).get("hesh") == hesh_igorya == m.vhod.hesh_klyucha(ki),
             (r2.status_code, len(ssylki(r2))))
    r = pd(c, f"vydat:{OLGA}")
    ko = klyuch_iz(r)
    proverka("Ольге (групп английского нет) ссылка ведёт на /pedagog", ssylki(r) == [f"{B}/pedagog?k={ko}"])
    proverka("три ключа — три разных; в копии файла входов рядом — те же три строки (только хеши)",
             len({ka, ki, ko}) == 3 and sqlite3.connect(m.vhod._put_kopii()).execute(
                 "SELECT COUNT(*) FROM pedagog_klyuchi WHERE hesh LIKE 'sha256$%'").fetchone()[0] == 3)
    return {"anna": ka, "igor": ki, "olga": ko}


# --- Е. Личный ключ: свои дети ---------------------------------------------------------------------------

def test_svoi_deti(K: dict):
    print("Е. Личный ключ: свои дети видны, чужие — 403 на чтение и на каждую изменяющую ручку")
    ka, ki, ko = K["anna"], K["igor"], K["olga"]
    svoi_anny = set(DETI[GR1]) | set(DETI[SAD]) | set(DETI[NUL])
    a = kl("198.51.100.10")
    r = a.get(f"/english/karta?k={ka}", headers=STR)
    proverka("Анна: /english/karta → 200, на странице только дети её трёх групп (16)",
             r.status_code == 200 and deti_stranicy(r.text) == svoi_anny, (r.status_code, len(deti_stranicy(r.text))))
    proverka("…ни одного ребёнка Игоря и группы без педагога — ни в разметке, ни в скриптах",
             not any(re.search(rf"\b{u}\b", r.text) for u in DETI[GR6] + DETI[GR5]) and GR6 not in r.text and GR5 not in r.text)
    proverka("…счётчик «детей в группах» — по её группам; приписка о личной ссылке с её именем; кнопок «по педагогу» нет",
             '<div class="card-value">16</div>' in r.text and 'id="lichnyy"' in r.text and "Проверкина Анна" in r.text
             and 'data-p="Маша"' not in r.text)
    proverka("…она по-прежнему педагог: без меню админов; заголовок X-Frame-Options: DENY, без no-store",
             'id="nav-razdely"' not in r.text and "KIDSUP_PED = true;" in r.text
             and r.headers.get("x-frame-options") == "DENY" and "cache-control" not in r.headers, dict(r.headers))
    proverka("GET /api/english/karta: только её дети и её группы; итог «всего» — 16",
             deti_api(a.get(f"/api/english/karta?k={ka}")) == svoi_anny
             and {g["группа"] for g in a.get(f"/api/english/karta?k={ka}").json()["группы"]} == {GR1, SAD, NUL}
             and a.get(f"/api/english/karta?k={ka}").json()["всего"] == 16)
    proverka("…то же с ключом в заголовке X-Pedagog-Key",
             deti_api(a.get("/api/english/karta", headers={"X-Pedagog-Key": ka})) == svoi_anny)
    z = karta_zaprosy(a, ka, SVOY)
    proverka("Анна, свой ребёнок: все восемь запросов карточки принимаются", z == PUSTILI, z)
    for uid, chey in ((CHUZHOY, "ребёнок Игоря"), (NICHEY, "группа без педагога"), (NET, "нет такого ребёнка"), (0, "без ребёнка")):
        z = karta_zaprosy(a, ka, uid)
        proverka(f"Анна, {chey}: все восемь запросов → 403 (в том числе чтение «вводного»)", z == OTKAZ, z)
    r1 = a.post(f"/api/english/karta/note?k={ka}", json={"user_id": CHUZHOY, "note": "x"})
    r2 = a.post(f"/api/english/karta/note?k={ka}", json={"user_id": NET, "note": "x"})
    proverka("отказ одинаков для «чужой» и «нет такого»: 403 с тем же текстом — не подсказка",
             r1.status_code == r2.status_code == 403 and r1.json() == r2.json() == {"detail": "Этот ребёнок не из ваших групп"},
             (r1.text, r2.text))
    z = karta_zaprosy(a, "", CHUZHOY, hdr={"X-Pedagog-Key": ka})
    proverka("…ключ в заголовке — те же 403", z == OTKAZ, z)
    with osnovnaya() as o:
        chuzhie = o.execute("SELECT COUNT(*) FROM speech_notes WHERE user_id IN (?,?,?) AND note='x'", (CHUZHOY, NICHEY, NET)).fetchone()[0]
        avtor = o.execute("SELECT author FROM speech_cards WHERE user_id=? AND tochka='t2'", (SVOY,)).fetchone()[0]
        avtor_z = o.execute("SELECT author FROM speech_notes WHERE user_id=?", (SVOY,)).fetchone()[0]
    proverka("…по чужим детям ничего не записано", chuzhie == 0, chuzhie)
    proverka("автор отметки и заметки — Анна: имя берёт сервер из ключа", avtor == avtor_z == "Проверкина Анна", (avtor, avtor_z))
    r = a.post(f"/api/english/karta?k={ka}", json={"user_id": SVOY, "tochka": "t2", "marks": {"komanda": "почти"},
                                                    "author": "Образцов Игорь"})
    with osnovnaya() as o:
        avtor = o.execute("SELECT author FROM speech_cards WHERE user_id=? AND tochka='t2'", (SVOY,)).fetchone()[0]
    proverka("…чужое имя в запросе сервер не принимает", r.status_code == 200 and avtor == "Проверкина Анна", avtor)
    # R1 №9: черновик отчёта и отметка «отчёт отправлен» (karta.save без отметок) автора не стирают
    r = a.post(f"/api/english/karta?k={ka}", json={"user_id": SVOY, "tochka": "t1", "marks": {"komanda": "да", "fraza": "да", "sam": "почти"}})
    r2 = a.post(f"/api/english/karta/chernovik?k={ka}", json={"user_id": SVOY, "tochka": "t1", "vvod": {"umeet": "стенд"}})
    from app import karta as _karta
    _karta.save(SVOY, "t1", None, report_date=date.today().isoformat())      # как после «Отправить» отчёт
    with osnovnaya() as o:
        avtor1 = o.execute("SELECT author FROM speech_cards WHERE user_id=? AND tochka='t1'", (SVOY,)).fetchone()[0]
    proverka("черновик отчёта и отметка «отчёт отправлен» автора отметок не стирают — Анна",
             r.status_code == 200 and r2.status_code == 200 and avtor1 == "Проверкина Анна", (r.status_code, r2.status_code, avtor1))

    i = kl("198.51.100.11")
    proverka("Игорь: видит только свою Гр6 (2 ребёнка); ребёнок Анны — 403, свой — принимается",
             deti_api(i.get(f"/api/english/karta?k={ki}")) == set(DETI[GR6])
             and karta_zaprosy(i, ki, SVOY) == OTKAZ and karta_zaprosy(i, ki, CHUZHOY) == PUSTILI)
    r = i.get(f"/english/karta?k={ki}", headers=STR)
    proverka("…на его странице нет детей Анны, сада и группы без педагога",
             deti_stranicy(r.text) == set(DETI[GR6]) and not any(re.search(rf"\b{u}\b", r.text) for u in DETI[GR1] + DETI[GR5]))
    o_ = kl("198.51.100.12")
    r = o_.get(f"/english/karta?k={ko}", headers=STR)
    proverka("Ольга (групп английского нет): карточка открывается пустой — ни одного ребёнка, с пояснением",
             r.status_code == 200 and deti_stranicy(r.text) == set() and "Групп английского за вами" in r.text
             and not any(re.search(rf"\b{u}\b", r.text) for u in vse_deti()), r.status_code)
    proverka("…GET /api/english/karta — пусто; любой ребёнок — 403",
             deti_api(o_.get(f"/api/english/karta?k={ko}")) == set() and karta_zaprosy(o_, ko, SVOY) == OTKAZ)
    h = koren(r.text)
    proverka("…в её шапке один пункт — «После первого занятия»; «Карточки речи» нет",
             punktov(h) == 1 and "После первого занятия" in h and "Карточка речи" not in h
             and f'href="/pedagog?k={ko}"' in h, re.sub(r"k=[A-Za-z0-9]+", "k=<ключ>", h[:300]))
    h = koren(a.get(f"/english/karta?k={ka}", headers=STR).text)
    proverka("у Анны в шапке оба пункта, оба с её ключом",
             punktov(h) == 2 and f'href="/english/karta?k={ka}"' in h and f'href="/pedagog?k={ka}"' in h)
    r = a.post(f"/api/english/karta/note?k={ka}", json={"user_id": SVOY, "note": "чужой сайт"},
               headers={"sec-fetch-site": "cross-site", "sec-fetch-mode": "cors", "user-agent": UA})
    r2 = a.post(f"/api/english/karta/note?k={PED}", json={"user_id": SVOY, "note": "чужой сайт"},
                headers={"sec-fetch-site": "cross-site", "sec-fetch-mode": "cors", "user-agent": UA})
    proverka("изменяющий запрос карточки с чужого сайта → 403 и с личным ключом, и с общим (как было)",
             (r.status_code, r2.status_code) == (403, 403), (r.status_code, r2.status_code))
    with osnovnaya() as o:
        zh = [tuple(x) for x in o.execute("SELECT imya, rol, sposob, put, kod, chto FROM deystviya "
                                          "WHERE sposob='pedklyuch' ORDER BY id")]
    proverka("журнал «кто что сделал»: запросы по личному ключу записаны под именем педагога, путь — без ключа",
             any(x[:5] == ("педагог Проверкина Анна", "pedagog", "pedklyuch", "/api/english/karta/note", 200) for x in zh)
             and any(x[0] == "педагог Образцов Игорь" for x in zh) and not any("?" in x[3] for x in zh), zh[:2])
    proverka("…отказы «чужой ребёнок» в журнале видны: код 403 и причина",
             any(x[4] == 403 and x[5] == "педагог: ребёнок не из его групп — отказ" for x in zh))
    with osnovnaya() as o:
        obshch = o.execute("SELECT COUNT(*) FROM deystviya WHERE put LIKE '/api/english/karta%' AND sposob=''").fetchone()[0]
    proverka("…запросы по общему ключу в журнал не пишутся, как раньше", obshch == 0, obshch)


# --- Ж. /pedagog с личным ключом -------------------------------------------------------------------------

def test_posle_zanyatiya(K: dict):
    print("Ж. «После первого занятия» с личным ключом: имя подставлено, список детей — тот же")
    # список первых занятий стенда — PERVYE (main подставляет его вместо МойКласса)

    def cfg(k):
        t = kl("198.51.100.20").get(f"/pedagog?k={k}", headers=STR).text
        return cfg_stranicy(t), t
    obshch, t_ob = cfg(PED)
    lich, t_l = cfg(K["olga"])
    proverka("общий ключ: в списке «Я — педагог» пять педагогов, имя не подставлено",
             len(obshch["teachers"]) == 5 and "ya" not in obshch)
    proverka("личный ключ Ольги: имя подставлено, в списке только она",
             lich.get("ya") == "Примерова Ольга" and lich["teachers"] == ["Примерова Ольга"], lich.get("teachers"))
    proverka("…страница запирает выбор педагога (select disabled по CFG.ya)", "CFG.ya" in t_l and ".disabled=true" in t_l)
    bez_done = lambda kids: [{k_: v for k_, v in x.items() if k_ != "done"} for x in kids]   # noqa: E731
    bez_tel = [{k_: v for k_, v in x.items() if k_ != "phone"} for x in PERVYE]
    proverka("список детей по личному ключу — тот же, что по общему (первые занятия сегодня и вчера по всему центру)",
             bez_done(lich["kids"]) == bez_done(obshch["kids"]) == bez_tel)
    # 14.11.2026, после проверки (R1 №1): было «…телефон родителя (в данных страницы)» — телефона больше нет
    proverka("…что в нём видно: имя ребёнка, группа, направление, дата и время — без телефона родителя",
             set(lich["kids"][0]) == {"uid", "name", "group", "course", "date", "time", "visit", "done"}
             and lt.SEMYA not in t_l and lt.KLIENT not in t_l)
    h = koren(t_l)
    proverka("шапка Ольги на /pedagog: один пункт, он подсвечен", punktov(h) == 1 and h.count("kn-tut") == 1)
    r = kl("198.51.100.21").post("/api/pedagog/save", json={"k": K["olga"], "uid": 555777, "child": "Чужой", "visit": True,
                                                            "can": "x", "next_group": "y", "phone": CHUZHOY_NOMER})
    proverka("личный ключ Ольги, ребёнок не из списка первых занятий → отказ (по ссылке не написать в карточку любого клиента)",
             r.json().get("ok") is False and "нет в списке первых занятий" in r.json().get("error", ""), r.text[:100])
    c = kl("198.51.100.21")
    r = c.post("/api/pedagog/save", json={"k": K["olga"], "uid": 9001, "child": "Ева", "teacher": "Образцов Игорь",
                                          "visit": True, "can": "личный: умеет", "next_group": "личный: группа"})
    with osnovnaya() as o:
        z = o.execute("SELECT teacher, can FROM karta_notes ORDER BY id DESC LIMIT 1").fetchone()
        inbox = o.execute("SELECT text FROM plan_inbox ORDER BY id DESC LIMIT 1").fetchone()
    proverka("сохранение по личному ключу с чужим именем в запросе: записан тот, кому выдан ключ",
             r.status_code == 200 and r.json().get("ok") is True and tuple(z) == ("Примерова Ольга", "личный: умеет"),
             (r.status_code, tuple(z)))
    proverka("…и в пункте дежурной на пульте — её имя, не присланное",
             inbox is not None and "Примерова Ольга" in inbox[0] and "Образцов Игорь" not in inbox[0], inbox and inbox[0][:80])
    r = c.post("/api/pedagog/save", json={"k": K["olga"], "uid": 9001, "child": "Ева", "visit": False})
    proverka("…имя в запросе можно не присылать вовсе", r.json().get("ok") is True, r.text[:100])
    with osnovnaya() as o:
        zh = o.execute("SELECT imya, put, kod, chto FROM deystviya WHERE put='/api/pedagog/save' ORDER BY id DESC LIMIT 1").fetchone()
    proverka("журнал: заметку записала «педагог Примерова Ольга»",
             zh is not None and tuple(zh) == ("педагог Примерова Ольга", "/api/pedagog/save", 200, "педагог: заметка после первого занятия"),
             zh and tuple(zh))


# --- З. Последний раз открывал ---------------------------------------------------------------------------

def test_otkryval(K: dict):
    print("З. «Последний раз открывал» — не чаще раза в 5 минут; админ по ссылке педагога — не педагог")
    posl = lambda imya: stroki()[imya]["posl"]      # noqa: E731
    p1 = posl("Проверкина Анна")
    proverka("Анна открывала: отметка стоит; у Игоря и Ольги — тоже", bool(p1) and bool(posl("Образцов Игорь")) and bool(posl("Примерова Ольга")))
    a = kl("198.51.100.10")
    chasy(120)
    a.get(f"/api/english/karta?k={K['anna']}")
    proverka("через 2 минуты новый запрос отметку не трогает", posl("Проверкина Анна") == p1)
    chasy(400)
    a.get(f"/api/english/karta?k={K['anna']}")
    p2 = posl("Проверкина Анна")
    proverka("через 6 минут — обновляет", p2 and p2 > p1, (p1, p2))
    chasy(0)
    c = vladelec()
    k_vera = vydat(c, VERA)
    K["vera"] = k_vera
    r = c.get(f"/english/karta?k={k_vera}", headers=STR)
    proverka("владелец открыл личную ссылку педагога: остаётся владельцем — меню на месте, дети все",
             r.status_code == 200 and 'id="nav-razdely"' in r.text and "Владельцу" in r.text
             and deti_stranicy(r.text) == vse_deti() and 'id="lichnyy"' not in r.text)
    r = kl().get(f"/english/karta?k={k_vera}", auth=OBSHCHIY_ADMIN)
    proverka("админ под общим входом с личной ссылкой педагога — админ, видит всех",
             r.status_code == 200 and 'id="nav-razdely"' in r.text and deti_stranicy(r.text) == vse_deti())
    lena = kl()
    voyti(lena, "lena", LENA, next_="/pult")
    r = lena.get(f"/english/karta?k={k_vera}", headers=STR)
    proverka("Лена (через форму) с личной ссылкой педагога — Лена: меню админа, запросы карточки без ограничений",
             r.status_code == 200 and 'id="nav-razdely"' in r.text and "Владельцу" not in r.text
             and lena.post(f"/api/english/karta/note?k={k_vera}", json={"user_id": CHUZHOY, "note": "админ"}, headers=FETCH).status_code == 200)
    r = c.get(f"/pedagog?k={k_vera}", headers=STR)
    proverka("…«педагог открывал» при этом не отмечается: у Веры по-прежнему «ещё не открывал»", posl("Садовая Вера") is None)
    # R1 №8, R2 №3: вошедший владелец по личной ссылке на /pedagog — имя педагога не подставлено и не заперто
    cf = cfg_stranicy(r.text)
    proverka("владелец открыл личную ссылку Веры на /pedagog: имя не подставлено, выбор из всего списка педагогов",
             "ya" not in cf and len(cf["teachers"]) == 5, (cf.get("ya"), len(cf["teachers"])))
    r = lena.post("/api/pedagog/save", json={"k": k_vera, "uid": 9001, "child": "Ева", "teacher": "Лена",
                                             "visit": False, "can": "админ: умеет"}, headers=FETCH)
    with osnovnaya() as o:
        z = o.execute("SELECT teacher, can FROM karta_notes ORDER BY id DESC LIMIT 1").fetchone()
        zh = o.execute("SELECT imya, sposob, kod, chto FROM deystviya WHERE put='/api/pedagog/save' "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    # было: «педагог в заметке — Вера (из ключа)». После проверки (R1 №8): вошедший остаётся собой и в
    # заметке — педагог тот, кого он выбрал в списке; подпись в МойКлассе и пункт дежурной — от него же
    proverka("Лена сохранила заметку по личной ссылке Веры: педагог в заметке — выбранный в списке (не Вера), "
             "в журнале — Лена, «Вера открывала» не отмечено",
             r.status_code == 200 and r.json().get("ok") is True and tuple(z) == ("Лена", "админ: умеет")
             and zh is not None and tuple(zh) == ("Лена", "forma", 200, "педагог: заметка после первого занятия")
             and posl("Садовая Вера") is None, (r.status_code, z and tuple(z), zh and tuple(zh)))
    # R1 №7, R2 №1: роботы предпросмотра ссылок — не «педагог открывал»
    for ua in ("TelegramBot (like TwitterBot)", "WhatsApp/2.23.20.0 A", "facebookexternalhit/1.1",
               "Mozilla/5.0 (compatible; vkShare; +http://vk.com/dev/Share)", "Viber", ""):
        r = kl("149.154.161.1").get(f"/pedagog?k={k_vera}", headers={"user-agent": ua, "accept": "*/*"})
        kl("149.154.161.1").get(f"/english/karta?k={k_vera}", headers={"user-agent": ua, "accept": "*/*"})
    proverka("превью ссылки в мессенджере (TelegramBot, WhatsApp, facebookexternalhit, vkShare, Viber, без User-Agent) — "
             "«ещё не открывал»", r.status_code == 200 and posl("Садовая Вера") is None, posl("Садовая Вера"))
    stary = {"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_8 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1",
             "accept": "text/html,application/xhtml+xml"}
    r = kl("198.51.100.13").get(f"/pedagog?k={k_vera}", headers=stary)
    proverka("браузер без Sec-Fetch-* (Safari до 16.4): по самому заходу отметки нет, страница несёт маяк «открыли»",
             r.status_code == 200 and posl("Садовая Вера") is None and pk.MAYAK in r.text)
    proverka("…роботу маяк не нужен (скрипт он не выполняет)",
             pk.MAYAK not in kl("149.154.161.1").get(f"/pedagog?k={k_vera}", headers={"user-agent": "TelegramBot"}).text)
    r = kl("198.51.100.13").post(pk.MAYAK, json={"k": k_vera, "put": "/pedagog"}, headers={**FETCH, "user-agent": stary["user-agent"]})
    with osnovnaya() as o:
        zh_m = o.execute("SELECT COUNT(*) FROM deystviya WHERE put=?", (pk.MAYAK,)).fetchone()[0]
    proverka("маяк со страницы → «последний раз открывал» записан; в журнал «кто что сделал» маяк не пишется",
             r.status_code == 200 and r.json() == {"ok": True} and bool(posl("Садовая Вера")) and zh_m == 0,
             (r.status_code, r.text[:60], zh_m))
    proverka("…маяк с неверным ключом → {ok: false}",
             kl("198.51.100.13").post(pk.MAYAK, json={"k": "ne-tot", "put": "/pedagog"}).json() == {"ok": False})
    proverka("…браузеру с Sec-Fetch-* маяк не нужен: переход виден по заголовкам",
             pk.MAYAK not in kl("198.51.100.13").get(f"/pedagog?k={k_vera}", headers=STR).text)
    r = kl("198.51.100.13").get(f"/pedagog?k={k_vera}", headers=STR)
    proverka("сама Вера открыла /pedagog — отметка появилась", r.status_code == 200 and bool(posl("Садовая Вера")))
    h = koren(kl("198.51.100.13").get(f"/pedagog?k={k_vera}", headers=STR).text)
    proverka("Вера — воспитатель сада, английский там ведёт Анна: «Карточки речи» в её шапке нет",
             punktov(h) == 1 and "Карточка речи" not in h)


def asgi_get(put: str, adres: str) -> tuple:
    """GET прямо в приложение, без TestClient (тот ждёт и фоновые задачи ответа):
    → (через сколько секунд ушёл конец тела ответа, через сколько вернулось приложение, код)."""
    import asyncio
    path, _, qs = put.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
             "method": "GET", "scheme": "https", "path": path, "raw_path": path.encode(), "query_string": qs.encode(),
             "root_path": "", "client": ("127.0.0.1", 50001), "server": ("app.kidsup.ru", 443),
             "headers": [(b"host", b"app.kidsup.ru"), (b"x-forwarded-for", adres.encode()),
                         (b"user-agent", UA.encode()), (b"accept", b"application/json")]}
    out = {"kon": None, "kod": 0}

    async def zapusk():
        t0 = time.monotonic()
        ushlo = asyncio.Event()
        pervyy = [True]

        async def receive():
            if pervyy[0]:
                pervyy[0] = False
                return {"type": "http.request", "body": b"", "more_body": False}
            await ushlo.wait()
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.start":
                out["kod"] = msg["status"]
            elif msg["type"] == "http.response.body" and not msg.get("more_body"):
                out["kon"] = time.monotonic() - t0
                ushlo.set()

        await m.app(scope, receive, send)
        return time.monotonic() - t0

    vsego = asyncio.run(zapusk())
    return out["kon"], vsego, out["kod"]


def test_ne_zhdat(K: dict):
    print("З2. Отметка «открывал» пишется после ответа: занятый файл входов педагога не задерживает")
    posl = lambda imya: stroki()[imya]["posl"]      # noqa: E731
    do = posl("Проверкина Анна")
    chasy(2000)                                      # отметка Анны «просрочена» — этот запрос её пишет
    blok = sqlite3.connect(m.config.VHODY_DB, timeout=0, isolation_level=None)
    blok.execute("BEGIN IMMEDIATE")                  # кто-то пишет в файл входов: читать можно, писать — ждать
    try:
        kon, vsego, kod = asgi_get(f"/api/english/karta?k={K['anna']}", "198.51.100.14")
    finally:
        blok.execute("ROLLBACK")
        blok.close()
    proverka("файл входов занят записью: ответ педагогу ушёл целиком, а запись отметки ждала (до полсекунды) уже после",
             kod == 200 and kon is not None and vsego - kon >= 0.4, (kod, kon, vsego))
    proverka("…отметка не записалась — и не потерялась: следующий запрос её пишет",
             posl("Проверкина Анна") == do and asgi_get(f"/api/english/karta?k={K['anna']}", "198.51.100.14")[2] == 200
             and posl("Проверкина Анна") > do, (do, posl("Проверкина Анна")))
    kon, vsego, kod = asgi_get(f"/api/english/karta?k={K['anna']}", "198.51.100.14")
    proverka("файл свободен, отметка свежая: запрос в файл входов не пишет и не ждёт",
             kod == 200 and vsego - kon < 0.3, (kon, vsego))
    chasy(0)


# --- И. Выдать заново, отключить -------------------------------------------------------------------------

def rabotaet(k, adres="198.51.100.30") -> tuple:
    """(карточка по ключу, API карточки, /pedagog действителен, сохранение заметки принято)."""
    c = kl(adres)
    return (c.get(f"/english/karta?k={k}").status_code, c.get(f"/api/english/karta?k={k}").status_code,
            "Ссылка недействительна" not in c.get(f"/pedagog?k={k}").text,
            c.post("/api/pedagog/save", json={"k": k, "uid": 9001, "teacher": "x", "visit": False}).json().get("ok"))


DA, NET_ = (200, 200, True, True), (401, 401, False, False)


def test_zanovo_i_otklyuchit(K: dict):
    print("И. «Выдать заново» отменяет прежний ключ; «Отключить» закрывает ссылку")
    c = vladelec()
    staryy = K["igor"]
    proverka("ключ Игоря работает на обеих страницах и в запросах", rabotaet(staryy) == DA, rabotaet(staryy))
    r = pd(c, f"vydat:{IGOR}")
    novyy = klyuch_iz(r)
    K["igor"] = novyy
    proverka("«Выдать заново» Игорю → новая ссылка, один раз", r.status_code == 200 and len(ssylki(r)) == 1 and novyy != staryy
             and staryy not in r.text)
    proverka("прежний ключ Игоря не работает нигде: карточка 401, /pedagog «Ссылка недействительна», сохранение — отказ",
             rabotaet(staryy) == NET_, rabotaet(staryy))
    proverka("…в заголовке X-Pedagog-Key — тоже", kl().get("/api/english/karta", headers={"X-Pedagog-Key": staryy}).status_code == 401)
    # R2 №4: педагог открыл старую ссылку в браузере — не окно пароля, а понятная страница
    r = kl("198.51.100.31").get(f"/english/karta?k={staryy}", headers=STR)
    proverka("прежняя ссылка Игоря в браузере: «Карточка речи» → «Ссылка недействительна. Попросите новую…», без окна пароля",
             r.status_code == 403 and "Ссылка недействительна. Попросите новую у администратора" in r.text
             and "www-authenticate" not in r.headers and r.headers.get("referrer-policy") == "no-referrer", r.status_code)
    r = kl("198.51.100.31").get("/english/karta?k=ne-tot-" + secrets.token_hex(4), headers=STR)
    proverka("…а просто неверный ключ — как раньше (401 с окном: не «устаревшая ссылка»)", r.status_code == 401, r.status_code)
    r = kl().get(f"/english/karta?k={staryy}", auth=OBSHCHIY_ADMIN, headers=STR)
    proverka("…админ с прежней ссылкой педагога — админ, страница открывается", r.status_code == 200 and 'id="nav-razdely"' in r.text,
             r.status_code)
    proverka("новый работает и видит ту же Гр6", rabotaet(novyy) == DA
             and deti_api(kl("198.51.100.30").get(f"/api/english/karta?k={novyy}")) == set(DETI[GR6]))
    z = stroki()["Образцов Игорь"]
    with baza_vhodov() as b:
        strok = b.execute("SELECT COUNT(*) FROM pedagog_klyuchi WHERE mk_id=?", (IGOR,)).fetchone()[0]
    proverka("строка Игоря одна: отпечаток — нового ключа, прежний помнится только как «старая ссылка»",
             strok == 1 and z["hesh"] == m.vhod.hesh_klyucha(novyy) and z["hesh_prezhniy"] == m.vhod.hesh_klyucha(staryy))
    sledy(novyy, c, "после «Выдать заново»")
    kid = stroki()["Образцов Игорь"]["id"]
    r = pd(c, f"otklyuchit:{kid}")
    proverka("«Отключить» Игоря → сообщение, ссылки в ответе нет", r.status_code == 200 and "ключ отключён" in r.text and not ssylki(r))
    proverka("отключённый ключ не работает нигде", rabotaet(novyy) == NET_, rabotaet(novyy))
    proverka("…остальные личные ключи и общий работают", rabotaet(K["anna"]) == DA and rabotaet(PED) == DA)
    r = c.get("/owner", headers=STR)
    proverka("в блоке: у Игоря «ключ отключён», кнопки «Отключить» у него нет, «Выдать заново» есть",
             "ключ отключён" in r.text and r.text.count(f"value='otklyuchit:{kid}'") == 0
             and f"value='vydat:{IGOR}'" in r.text)
    r = pd(c, f"vydat:{IGOR}")
    K["igor"], K["igor_pred"] = klyuch_iz(r), novyy
    proverka("«Выдать заново» после отключения: новый ключ работает, оба прежних — нет",
             rabotaet(K["igor"]) == DA and rabotaet(novyy) == NET_ and rabotaet(staryy) == NET_)
    # уволенный педагог: ключ остаётся в блоке, его можно отключить
    with osnovnaya() as o:
        o.execute("UPDATE managers SET raw=? WHERE id=?", (json.dumps({"id": DARYA, "name": "Нулёвкина Дарья", "roles": [63600],
                                                                       "isWork": False}, ensure_ascii=False), DARYA))
    try:
        r = c.get("/owner", headers=STR)
        proverka("уволенной без ключа в списке больше нет", "Нулёвкина Дарья" not in r.text)
    finally:
        with osnovnaya() as o:
            o.execute("UPDATE managers SET raw=? WHERE id=?", (json.dumps({"id": DARYA, "name": "Нулёвкина Дарья", "roles": [63600],
                                                                           "isWork": True}, ensure_ascii=False), DARYA))
    k_darya = vydat(c, DARYA)
    proverka("ключ Дарьи (работает в МойКлассе) работает", rabotaet(k_darya) == DA)

    def rabota_darya(raw):
        with osnovnaya() as o:
            if raw is None:
                o.execute("DELETE FROM managers WHERE id=?", (DARYA,))
            else:
                o.execute("INSERT OR REPLACE INTO managers (id, name, raw) VALUES (?,?,?)",
                          (DARYA, "Нулёвкина Дарья", json.dumps(raw, ensure_ascii=False)))
    rabota_darya({"id": DARYA, "name": "Нулёвкина Дарья", "roles": [63600], "isWork": False})
    r = c.get("/owner", headers=STR)
    kid_d = stroki()["Нулёвкина Дарья"]["id"]
    # 14.11.2026, после проверки (R1 №3): было «ключ уволенной пока работает (до „Отключить“)» и серая
    # приписка «нет в списке». Теперь ключ сотрудника, которого МойКласс отмечает «не работает», не
    # принимается сразу, а владелец видит красную строку наверху блока.
    proverka("педагог с ключом уволен: остаётся в блоке с пометкой, «Выдать заново» нет, «Отключить» есть",
             "в МойКлассе «не работает» — ключ не принимается" in r.text and f"value='vydat:{DARYA}'" not in r.text
             and f"value='otklyuchit:{kid_d}'" in r.text)
    blok = r.text[r.text.index("id='pedagogi'"):]
    proverka("…красная строка наверху блока (до «Общий ключ»): «Ключ выдан уволенному сотруднику: Нулёвкина Дарья»",
             "id='ped-uvoleny'" in blok and blok.index("id='ped-uvoleny'") < blok.index("<h3>Общий ключ</h3>")
             and "Нулёвкина Дарья" in blok[blok.index("id='ped-uvoleny'"):blok.index("<h3>Общий ключ</h3>")])
    proverka("…ключ уволенной не принимается сразу, до «Отключить»: обе страницы, запросы и сохранение — отказ",
             rabotaet(k_darya) == NET_, rabotaet(k_darya))
    r_ = kl("198.51.100.32").get(f"/english/karta?k={k_darya}", headers=STR)
    proverka("…её ссылка в браузере — «Ссылка недействительна», не окно пароля", r_.status_code == 403
             and "Ссылка недействительна" in r_.text, r_.status_code)
    s_ = pk.sostoyanie()
    proverka("…в счёт «с ключом» уволенная не входит", "Нулёвкина Дарья" in s_["uvoleny_s_klyuchom"]
             and not any(p["imya"] == "Нулёвкина Дарья" for p in s_["pedagogi"] if p["klyuch"] and not p["uvolen"]))
    rabota_darya(None)
    proverka("данных о сотруднике в выгрузке нет — ключ не отключаем: работает", rabotaet(k_darya) == DA, rabotaet(k_darya))
    rabota_darya({"id": DARYA, "name": "Нулёвкина Дарья", "roles": [63600]})
    proverka("…нет поля isWork — тоже работает", rabotaet(k_darya) == DA)
    rabota_darya({"id": DARYA, "name": "Нулёвкина Дарья", "roles": [63600], "isWork": False})
    proverka("снова «не работает» — не принимается; «Отключить» отключает совсем",
             rabotaet(k_darya) == NET_ and pd(c, f"otklyuchit:{kid_d}").status_code == 200
             and not stroki()["Нулёвкина Дарья"]["vkl"] and "id='ped-uvoleny'" not in c.get("/owner", headers=STR).text)


# --- К. Общий ключ: сменить, выключить, включить снова (Т1) ----------------------------------------------

def test_obshchiy(K: dict) -> str:
    print("К. Общий ключ: сменить, выключить, включить снова")
    c = vladelec()
    r = c.get("/owner", headers=STR)
    s = re.search(r"<p id='ped-sostoyanie'>(.*?)</p>", r.text, re.S).group(1)
    proverka("строка состояния: сколько педагогов открывали личным ключом и сколько открытий по общему за 7 дней",
             "Личным ключом страницы уже открывали" in s and "По общему ключу за 7 дней" in s
             and re.search(r"Открытия считаются с \d\d\.\d\d \d\d:\d\d\.", s) is not None, s[:300])
    sost = c.get("/api/pedagogi/sostoyanie", headers=FETCH).json()
    proverka("…числа честные: с действующим ключом 4 (Анна, Игорь, Ольга, Вера — отключённая Дарья не в счёт), открывали 4",
             (sost["s_klyuchom"], sost["otkryvali"]) == (4, 4) and "<b>4 из 4</b>" in s,
             (sost["s_klyuchom"], sost["otkryvali"]))
    n_do = sost["obshchiy"]["otkrytiy_za_7_dney"]
    p = kl("198.51.100.40")
    p.get(f"/pedagog?k={PED}", headers=STR)
    p.get(f"/english/karta?k={PED}", headers=STR)
    p.get(f"/api/english/karta?k={PED}")                                         # запрос со страницы — не открытие
    p.post(f"/api/english/karta/note?k={PED}", json={"user_id": SVOY, "note": "стенд"})
    kl().get(f"/english/karta?k={PED}", auth=OBSHCHIY_ADMIN)                     # админ по общей ссылке — не педагог
    p.get("/pedagog?k=ne-tot", headers=STR)
    sost = c.get("/api/pedagogi/sostoyanie", headers=FETCH).json()
    proverka("по общему ключу педагог открыл две страницы: счёт +2 (запросы со страницы, админ и неверный ключ не считаются)",
             sost["obshchiy"]["otkrytiy_za_7_dney"] == n_do + 2 and sost["obshchiy"]["posl_otkrytie"],
             (n_do, sost["obshchiy"]["otkrytiy_za_7_dney"]))
    with baza_vhodov() as b:
        dni = b.execute("SELECT COALESCE(SUM(n), 0) FROM pedagog_vhody WHERE vid='obshchiy'").fetchone()[0]
    proverka("…счёт записан в файл входов (таблица pedagog_vhody)", dni == n_do + 2, dni)
    # R2 №1: общую ссылку вставили в чат — превью мессенджера открытием не считается
    for ua in ("TelegramBot (like TwitterBot)", "WhatsApp/2.23.20.0 A", "facebookexternalhit/1.1", "Mozilla/5.0 (compatible; Discordbot/2.0)"):
        kl("149.154.161.2").get(f"/pedagog?k={PED}", headers={"user-agent": ua, "accept": "*/*"})
        kl("149.154.161.2").get(f"/english/karta?k={PED}", headers={"user-agent": ua, "accept": "*/*"})
    stary = {"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_8 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1",
             "accept": "text/html,application/xhtml+xml"}
    t_k = p.get(f"/english/karta?k={PED}", headers=stary).text            # Safari без Sec-Fetch-*: по заходу не видно
    n_bot = c.get("/api/pedagogi/sostoyanie", headers=FETCH).json()["obshchiy"]["otkrytiy_za_7_dney"]
    proverka("превью мессенджеров (Telegram, WhatsApp, Facebook, Discord) и заход без Sec-Fetch-* в счёт не идут",
             n_bot == n_do + 2, (n_do, n_bot))
    proverka("…страница карточки старому браузеру несёт маяк «открыли»", pk.MAYAK in t_k)
    p.post(pk.MAYAK, json={"k": PED, "put": "/english/karta"}, headers={**FETCH, "user-agent": stary["user-agent"]})
    p.post(pk.MAYAK, json={"k": PED, "put": "/chto-to-drugoe"}, headers={**FETCH, "user-agent": stary["user-agent"]})
    n_m = c.get("/api/pedagogi/sostoyanie", headers=FETCH).json()["obshchiy"]["otkrytiy_za_7_dney"]
    proverka("маяк страницы по общему ключу → +1 открытие (маяк с чужим адресом страницы — нет)", n_m == n_do + 3, (n_do, n_m))
    n_do += 1

    # сменить
    r = pd(c, "obshchiy_smenit")
    ss = ssylki(r)
    novyy = klyuch_iz(r)
    proverka("«Сменить общий ключ» → две готовые ссылки (обе страницы) с одним новым ключом, один раз",
             r.status_code == 200 and len(ss) == 2 and ss[0] == f"{B}/pedagog?k={novyy}" and ss[1] == f"{B}/english/karta?k={novyy}"
             and re.fullmatch(r"[A-Za-z0-9]{40}", novyy) is not None and r.headers.get("cache-control") == "no-store", len(ss))
    proverka("прежний общий ключ не работает нигде", rabotaet(PED) == NET_, rabotaet(PED))
    proverka("новый общий работает как прежний: обе страницы, все дети, имя выбирается",
             rabotaet(novyy) == DA and deti_api(kl("198.51.100.40").get(f"/api/english/karta?k={novyy}")) == vse_deti())
    proverka("личные ключи работают", rabotaet(K["anna"]) == DA and rabotaet(K["olga"])[2:] == (True, True))
    proverka("настройка pedagog_key стёрта; отметка «прежний снят» — в основной базе; в таблице — только хеш нового",
             m.db.get_setting("pedagog_key") == "" and bool(pk._snyat())
             and stroki()["obshchiy"]["hesh"] == m.vhod.hesh_klyucha(novyy) and stroki()["obshchiy"]["vkl"] == 1)
    sledy(novyy, c, "после смены общего ключа")
    r2 = c.get("/owner", headers=STR)
    proverka("в блоке: «Сменён …», ссылки уже нет", "работает новый общий ключ" in r2.text and novyy not in r2.text)
    r3 = kl().post("/api/settings", json={"key": "pedagog_key", "value": PED}, auth=VLADELEC)
    # 14.11.2026, после проверки (R2 №9): было «200 и молча ни на что не влияет». Теперь запись отклоняется
    # с объяснением, где теперь меняется общий ключ.
    proverka("прежний ключ через /api/settings обратно не вернуть: 400 «общий ключ теперь меняется на /owner…»",
             r3.status_code == 400 and "общий ключ теперь меняется на /owner, блок «Ключи педагогов»" in r3.text
             and m.db.get_setting("pedagog_key") == "" and rabotaet(PED) == NET_ and rabotaet(novyy) == DA, r3.status_code)
    r = kl("198.51.100.41").get(f"/english/karta?k={PED}", headers=STR)
    proverka("прежняя общая ссылка из чата в браузере: «Ссылка недействительна», не окно пароля",
             r.status_code == 403 and "Ссылка недействительна" in r.text and "www-authenticate" not in r.headers, r.status_code)
    proverka("…запросы /api по прежней ссылке — как раньше (401)", kl().get(f"/api/english/karta?k={PED}").status_code == 401)
    m.db.set_setting("pedagog_key", "")
    with osnovnaya() as o:
        vazh = [x[0] for x in o.execute("SELECT chto FROM deystviya WHERE vazhnoe=1")]
    proverka("строка «Важное» в журнале: общий ключ педагогов сменён", "общий ключ педагогов сменён" in vazh, vazh[-3:])

    # выключить
    r = pd(c, "obshchiy_vykl")
    proverka("«Выключить общий ключ» → сообщение, ссылок нет", r.status_code == 200 and "Общий ключ выключен" in r.text and not ssylki(r))
    proverka("выключенный общий не работает: ни новый, ни прежний", rabotaet(novyy) == NET_ and rabotaet(PED) == NET_,
             (rabotaet(novyy), rabotaet(PED)))
    proverka("…а личные работают: Анна — карточка и /pedagog, Ольга — /pedagog",
             rabotaet(K["anna"]) == DA and rabotaet(K["olga"])[2:] == (True, True)
             and deti_api(kl("198.51.100.40").get(f"/api/english/karta?k={K['anna']}")) == set(DETI[GR1]) | set(DETI[SAD]) | set(DETI[NUL]))
    r2 = c.get("/owner", headers=STR)
    proverka("в блоке: «Выключен», кнопка «Включить снова», кнопок «Сменить» и «Выключить» нет",
             "<b>Выключен</b>" in r2.text and ">Включить снова</button>" in r2.text
             and "value='obshchiy_smenit'" not in r2.text and "value='obshchiy_vykl'" not in r2.text)
    sost = c.get("/api/pedagogi/sostoyanie", headers=FETCH).json()["obshchiy"]
    proverka("состояние: общий выключен", sost["sostoyanie"] == "vyklyuchen" and sost["rabotaet"] is False, sost)

    # включить снова
    r = pd(c, "obshchiy_vkl")
    tretiy = klyuch_iz(r)
    proverka("«Включить снова» → новый ключ (не прежний), две ссылки",
             r.status_code == 200 and len(ssylki(r)) == 2 and tretiy not in (novyy, PED) and rabotaet(tretiy) == DA)
    proverka("…выключенный до этого и самый первый не ожили", rabotaet(novyy) == NET_ and rabotaet(PED) == NET_)
    return tretiy


# --- Л. Сбои хранилищ ------------------------------------------------------------------------------------

def test_hranilishcha(K: dict, obshchiy: str):
    print("Л. Два хранилища: восстановленная из копии база или пропавшая строка прежний ключ не оживляют")
    with osnovnaya() as o:
        o.execute("DELETE FROM settings WHERE key=?", (pk.SNYAT,))
    m.db.set_setting("pedagog_key", PED)             # «основную базу восстановили из старой копии»
    proverka("отметки в основной базе нет, прежний ключ в настройке снова лежит — но он не работает: решает файл входов",
             rabotaet(PED) == NET_ and rabotaet(obshchiy) == DA, rabotaet(PED))
    m.db.set_setting(pk.SNYAT, m.vhod._iso())
    with baza_vhodov() as b:
        sohr = [tuple(r) for r in b.execute("SELECT * FROM pedagog_klyuchi WHERE vid='obshchiy'")]
        b.execute("DELETE FROM pedagog_klyuchi WHERE vid='obshchiy'")
    proverka("строки общего ключа в файле входов нет, отметка в основной базе есть — прежний ключ тоже не работает",
             rabotaet(PED) == NET_, rabotaet(PED))
    with baza_vhodov() as b:
        b.executemany("INSERT INTO pedagog_klyuchi VALUES (?,?,?,?,?,?,?,?,?)", sohr)
    m.db.set_setting("pedagog_key", "")
    proverka("хранилища возвращены: общий и личные работают", rabotaet(obshchiy) == DA and rabotaet(K["anna"]) == DA)
    # файл входов не читается: личные ключи не проверить, страница отвечает как на неверный ключ, без ошибки 500
    bylo = pk._stroki
    pk._stroki = lambda: (_ for _ in ()).throw(sqlite3.OperationalError("стенд: файл входов заперт"))
    try:
        c = kl("198.51.100.50", raise_server_exceptions=False)
        kody = (c.get(f"/english/karta?k={K['anna']}").status_code, c.get(f"/pedagog?k={K['anna']}").status_code)
        proverka("файл входов не читается: личный ключ → обычный отказ (401, «Ссылка недействительна»), не 500", kody == (401, 200), kody)
    finally:
        pk._stroki = bylo
    r = kl().get("/api/backup/db", auth=VLADELEC)
    import gzip
    raw = gzip.decompress(r.content) if r.status_code == 200 else b""
    kop = Path(m.config.DB_PATH).parent / "kopiya_proverka.db"
    kop.write_bytes(raw)
    with sqlite3.connect(kop) as k_:
        tablicy = {x[0] for x in k_.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    kop.unlink()
    proverka("копия базы (/api/backup/db): таблиц ключей педагогов в ней нет, ни одного ключа и хеша ключа — тоже",
             r.status_code == 200 and tablicy and not tablicy & {"pedagog_klyuchi", "pedagog_vhody"}
             and not any(s.encode() in raw for s in SEKRETY) and b"sha256$" not in raw, r.status_code)


def test_do_smeny_sboy():
    """Отдельный стендовый случай до действий владельца: файл входов не читается — общий ключ работает."""
    print("М. Сбой файла входов до действий владельца: прежний общий ключ работает как раньше")
    bylo = pk._stroki
    pk._stroki = lambda: (_ for _ in ()).throw(sqlite3.OperationalError("стенд: файл входов заперт"))
    try:
        c = kl("198.51.100.60")
        proverka("таблица ключей не читается — общий ключ открывает обе страницы и запросы",
                 (c.get(f"/english/karta?k={PED}").status_code, c.get(f"/api/english/karta?k={PED}").status_code,
                  "Ссылка недействительна" not in c.get(f"/pedagog?k={PED}").text) == (200, 200, True))
    finally:
        pk._stroki = bylo


# --- Н. Перебор ------------------------------------------------------------------------------------------

def test_perebor(K: dict, obshchiy: str):
    print("Н. Перебор ключей: промахи считаются; ключам из таблицы пауза не отказывает и их удача промахи не стирает")
    adr, drugoy = "203.0.113.5", "203.0.113.6"
    zn = kl(adr)
    proverka("Анна с этого адреса уже ходила своим ключом", zn.get(f"/api/english/karta?k={K['anna']}").status_code == 200)
    x = kl(adr)
    otvety = set()
    for i in range(5):
        r = x.get(f"/pedagog?k=perebor{i}{secrets.token_hex(8)}")
        otvety.add((r.status_code, "Ссылка недействительна" in r.text))
    proverka("пять разных неверных ключей: каждый получает обычный ответ неверного ключа (200, «Ссылка недействительна»)",
             otvety == {(200, True)}, otvety)
    with baza_vhodov() as b:
        n = b.execute("SELECT COUNT(*) FROM popytki WHERE login=? AND adres=?", (pk.SCHET, adr)).fetchone()[0]
    proverka("промахи записаны в общий счётчик перебора (popytki, свой счёт «okno:pedagog»)", n == 5, n)
    # 14.11.2026, после проверки (R1 №6): было «в паузу верный ключ, которого сервер с этого адреса не видел,
    # получает тот же ответ, что и неверный». После перезапуска сервера (память «знакомых» пуста) пять чужих
    # неверных ключей с Wi-Fi центра запирали честных педагогов. Ключ из таблицы — 40 случайных знаков,
    # подобрать его нельзя: пауза ему не отказывает.
    r = x.get(f"/pedagog?k={K['olga']}")
    r_k = x.get(f"/english/karta?k={K['olga']}")
    proverka("в паузу личный ключ, которого сервер с этого адреса не видел, работает: /pedagog и карточка",
             r.status_code == 200 and "Ссылка недействительна" not in r.text and r_k.status_code == 200, (r.status_code, r_k.status_code))
    r = x.post("/api/pedagog/save", json={"k": K["olga"], "uid": 9001, "visit": False})
    proverka("…и сохранение принимается", r.json().get("ok") is True, r.text[:100])
    proverka("…новый общий ключ (тоже из таблицы), впервые с этого адреса в паузу, — работает", rabotaet(obshchiy, adr) == DA,
             rabotaet(obshchiy, adr))
    with baza_vhodov() as b:
        n = b.execute("SELECT COUNT(*) FROM popytki WHERE login=? AND adres=?", (pk.SCHET, adr)).fetchone()[0]
    proverka("…удачи ключами из таблицы промахи адреса не стёрли", n == 5, n)
    proverka("знакомый ключ (Анна ходила с этого адреса) работает",
             zn.get(f"/api/english/karta?k={K['anna']}").status_code == 200)
    proverka("с другого адреса тот же ключ Ольги работает", "Ссылка недействительна" not in kl(drugoy).get(f"/pedagog?k={K['olga']}").text)
    # R1 №5: «4 случайных + свой верный» вне паузы — раньше удача стирала промахи, перебор не копился
    adr3 = "203.0.113.8"
    z = kl(adr3)
    for cikl in range(3):
        for i in range(4):
            z.get(f"/pedagog?k=cikl{cikl}x{i}{secrets.token_hex(8)}")
        z.get(f"/api/english/karta?k={K['anna']}")
    with baza_vhodov() as b:
        n3 = b.execute("SELECT COUNT(*) FROM popytki WHERE login=? AND adres=?", (pk.SCHET, adr3)).fetchone()[0]
    proverka("три круга «4 неверных + свой личный ключ»: все 12 промахов на счету (удача личным ключом их не стирает)",
             n3 == 12, n3)
    # устаревшие ссылки — не перебор
    adr2 = "203.0.113.7"
    y = kl(adr2)
    with baza_vhodov() as b:
        do = b.execute("SELECT COUNT(*) FROM popytki WHERE login=?", (pk.SCHET,)).fetchone()[0]
    for _ in range(4):
        for staryy in (PED, K["staryy_igor"], K["igor_pred"], K["otkl"]):
            y.get(f"/pedagog?k={staryy}")
            y.get(f"/english/karta?k={staryy}")
    with baza_vhodov() as b:
        posle = b.execute("SELECT COUNT(*) FROM popytki WHERE login=?", (pk.SCHET,)).fetchone()[0]
    proverka("старые ссылки (прежний общий, два ключа до «Выдать заново», отключённый) перебором не считаются: 32 захода — 0 промахов",
             posle == do, (do, posle))
    proverka("…и новый ключ с того же адреса открывается", "Ссылка недействительна" not in y.get(f"/pedagog?k={K['olga']}").text)
    for i in range(6):
        y.get("/pedagog?k=odin-i-tot-zhe-nevernyj")
    with baza_vhodov() as b:
        odin = b.execute("SELECT COUNT(*) FROM popytki WHERE login=? AND adres=?", (pk.SCHET, adr2)).fetchone()[0]
    proverka("один и тот же неверный ключ с одного адреса считается один раз в час (старая вкладка центр не запирает)", odin == 1, odin)
    with baza_vhodov() as b:
        forma = b.execute("SELECT COUNT(*) FROM popytki WHERE login NOT LIKE 'okno:%' AND adres IN (?,?)", (adr, adr2)).fetchone()[0]
    proverka("в счёт формы входа /vhod промахи ключей педагогов не идут", forma == 0, forma)


# --- Н2. Документы ---------------------------------------------------------------------------------------

def test_dokumenty():
    print("Н2. Документы: памятка педагогу и порядок восстановления")
    pam = Path(KOREN, "docs", "pamyatka_pedagoga.html").read_text(encoding="utf-8")
    proverka("памятка педагогу больше не говорит «ключ хранится в настройках сервера (pedagog_key)»; общий ключ — на /owner",
             "Ключ хранится в настройках сервера (pedagog_key)" not in pam and "блок «Ключи педагогов»" in pam)
    vos = Path(KOREN, "docs", "rabota", "vosstanovlenie.md").read_text(encoding="utf-8")
    proverka("восстановление: прежний общий ключ не оживает, только если копия снята после смены; иначе — «Сменить общий ключ»",
             "снята после смены ключа" in vos and "«Сменить общий ключ» на /owner" in vos
             and "из копии при этом не оживает (отметка" not in vos)


# --- О. Следы ----------------------------------------------------------------------------------------------

def test_sledy_v_konce():
    print("О. Следы: ни одного ключа в файлах стенда, в журнале и в печати прогона")
    vse = [s for s in SEKRETY if s]
    with osnovnaya() as o:
        zh = " ".join(" ".join(str(x) for x in r_) for r_ in o.execute("SELECT * FROM deystviya"))
        nastr = " ".join(f"{r_[0]}={r_[1]}" for r_ in o.execute("SELECT key, value FROM settings"))
        hity = " ".join(str(r_[0]) for r_ in o.execute("SELECT path FROM page_hits"))
    proverka(f"выдано ключей за прогон: {len(vse)}; в журнале действий, настройках и счётчике открытий — ни одного",
             len(vse) >= 8 and not any(s in zh or s in nastr or s in hity for s in vse))
    for f in sorted(Path(m.config.DB_PATH).parent.iterdir()):
        if f.is_file():
            raw = f.read_bytes()
            proverka(f"{f.name}: ни одного ключа побайтно", not any(s.encode() in raw for s in vse))
    log_ = "".join(ZAHVAT)
    proverka(f"печать прогона и журнал сервера ({len(log_) // 1000} КБ): ни одного ключа",
             len(log_) > 3000 and not any(s in log_ for s in vse + [PED, VLADELEC[1], OBSHCHIY_ADMIN[1], LENA]))
    with baza_vhodov() as b:
        heshi = [r[0] for r in b.execute("SELECT hesh FROM pedagog_klyuchi WHERE hesh<>''")]
    proverka("в таблице ключей — только отпечатки sha256$…", heshi and all(re.fullmatch(r"sha256\$[0-9a-f]{64}", h) for h in heshi))


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


def _lovushki_na_vse():
    import app
    for mod in pkgutil.iter_modules(app.__path__):
        importlib.import_module(f"app.{mod.name}")
    lt._postavit_lovushki()


def main():
    global m, pk
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="")
    a = ap.parse_args()
    sys.stdout = _Tee(sys.stdout)
    work = a.work or tempfile.mkdtemp(prefix="pedagog_")
    os.makedirs(work, exist_ok=True)
    m = lt.zapustit(KOREN, work)
    lt.zasejat(os.path.join(work, "kidsup.db"))
    zasejat_pedagogov(os.path.join(work, "kidsup.db"))
    m.vhod.zapusk(True)             # как при запуске работающего сервера
    # роли — как на сервере: общий вход админов и отдельный пароль владельца; режим без входа выключен
    m.config.APP_USER, m.config.APP_PASSWORD = OBSHCHIY_ADMIN
    m.config.BEZ_VHODA = False
    m.db.set_setting("owner_password", VLADELEC[1])
    m.db.set_setting("pedagog_key", PED)
    from app import pedklyuchi, karta_razvitiya
    pk = pedklyuchi
    karta_razvitiya._kids = pervye_stenda       # список первых занятий стенда вместо МойКласса (см. PERVYE)
    _lovushki_na_vse()
    _zh = _LogVZahvat()
    _zh.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(_zh)
    t0 = time.time()

    test_do_smeny_sboy()
    test_kak_ranshe()
    test_spisok_pervyh()
    test_perebor_prezhniy()
    test_zamena_i_sad()
    test_dolya_i_sad()
    test_shapka()
    test_referer_log_css()
    proverka("личный пароль админа задан (для проверки «админ не может»)", m.vhod.zadat_parol("lena", LENA) == "")
    test_kto_upravlyaet()
    K = test_vydacha()
    test_svoi_deti(K)
    test_posle_zanyatiya(K)
    test_otkryval(K)
    test_ne_zhdat(K)
    K["staryy_igor"] = K["igor"]
    test_zanovo_i_otklyuchit(K)
    obshchiy = test_obshchiy(K)
    test_hranilishcha(K, obshchiy)
    # для проверки «старые ссылки — не перебор»: отключённый ключ
    c = vladelec()
    K["otkl"] = vydat(c, VERA)
    pd(c, f"otklyuchit:{stroki()['Садовая Вера']['id']}")
    test_perebor(K, obshchiy)
    test_dokumenty()
    test_sledy_v_konce()
    proverka("ни одной отправки", not lt.LOVUSHKI, lt.LOVUSHKI)
    chuzhie = [x for x in lt.SET if any("pedklyuchi" in f or "vhod" in f for f in x[1])]
    proverka("ключи педагогов не ходят в сеть", not chuzhie, chuzhie)
    print(f"\nИтого: {lt.proshlo} OK, {lt.oshibok} ошибок · стенд {work} · {time.time() - t0:.0f} с")
    sys.exit(1 if lt.oshibok else 0)


if __name__ == "__main__":
    main()
