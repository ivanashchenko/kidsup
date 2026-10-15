# -*- coding: utf-8 -*-
"""Проверки страницы «Линия» (/liniya) на стенде — без сервера и без сети.

15.10.2026. «Линия» — справка о звонящем и готовые ответы на время разговора.
Здесь зафиксировано, что она должна показывать, и что соседние страницы
(/brief, /pult, /obzvon-dnya, /waiting, /obeshchaniya) после ссылок «Линия»
открываются как раньше.

    python docs/rabota/liniya_testy.py [--tree КОРЕНЬ] [--db ОБРАЗЕЦ.db] [--work ПАПКА]

  --tree  корень дерева, в котором лежит app/ (по умолчанию — этот репозиторий;
          на стенде — выгрузка `git archive HEAD app`)
  --db    база-образец, копия которой ляжет под стенд (по умолчанию — пустая)
  --work  рабочая папка стенда (по умолчанию — временная)

Нужен python с fastapi, jinja2 и httpx (TestClient). Обвязка закрывает сеть
(любое соединение наружу — ошибка, и оно записывается), подменяет функции
отправки ловушками, не запускает автопилот и ставит часы на 14.10.2026 12:00 МСК.

Репозиторий публичный: данные здесь только выдуманные — номера 7 999 000-…,
имена вроде «Тестова Ева». Живых номеров и фамилий сюда не класть.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import tempfile
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEN = "2026-10-14"                     # «сегодня» стенда (часы автопилота)
oshibok = 0
proshlo = 0
SET: list = []                         # попытки выйти в сеть
LOVUSHKI: list = []                    # вызовы отправок


def proverka(nazvanie, ok, podrobno=""):
    global oshibok, proshlo
    ok = bool(ok)
    oshibok += not ok
    proshlo += ok
    print(("  OK      " if ok else "  ОШИБКА  ") + nazvanie + ("" if ok or not podrobno else f"  → {podrobno}"))


# --- обвязка: сеть закрыта, отправки — ловушки, часы стоят ---------------------

def _zakryt_set():
    orig_connect, orig_gai = socket.socket.connect, socket.getaddrinfo

    def connect(self, addr):
        if self.family == getattr(socket, "AF_UNIX", -1):
            return orig_connect(self, addr)
        import traceback
        otkuda = [f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno}" for f in traceback.extract_stack()
                  if "/app/" in f.filename][-3:]
        SET.append(("connect", otkuda))
        raise OSError("сеть закрыта (liniya_testy)")

    def gai(host, *a, **k):
        if host in ("localhost", "127.0.0.1", "::1", None):
            return orig_gai(host, *a, **k)
        import traceback
        otkuda = [f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno}" for f in traceback.extract_stack()
                  if "/app/" in f.filename][-3:]
        SET.append(("dns", otkuda))
        raise socket.gaierror("сеть закрыта (liniya_testy)")

    socket.socket.connect, socket.getaddrinfo = connect, gai


def _lovushka(imya):
    def f(*a, **k):
        LOVUSHKI.append(imya)
        raise RuntimeError(f"ловушка: вызвана отправка {imya}")
    return f


def _postavit_lovushki():
    for modname, mod in list(sys.modules.items()):
        if not (modname == "app" or modname.startswith("app.")) or mod is None:
            continue
        for attr in list(vars(mod)):
            if (attr in ("send", "send_via", "send_smart", "send_media", "send_sms", "_wa",
                         "missed_calls", "_send", "send_text")
                    or attr.startswith(("send_", "broadcast_")) or attr.endswith("_series")) \
                    and callable(getattr(mod, attr, None)):
                setattr(mod, attr, _lovushka(f"{modname}.{attr}"))


def zapustit(tree: str, work: str):
    os.environ["KIDSUP_DATA_DIR"] = work
    for k in ("APP_PASSWORD", "KIDSUP_ADMIN_PASS", "MOYKLASS_API_KEY"):
        os.environ.pop(k, None)
    _zakryt_set()
    sys.path.insert(0, tree)
    import logging
    import app.autopilot as ap
    ap.start = lambda: None                       # цикл автопилота не запускаем
    import app.main as m
    import app.wazzup, app.perepiska  # noqa: E401,F401 — чтобы ловушки встали и на них
    _postavit_lovushki()
    from zoneinfo import ZoneInfo
    chasy = datetime(2026, 10, 14, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    ap._now = lambda: chasy
    logging.getLogger().setLevel(logging.WARNING)
    return m


# --- выдуманные данные ---------------------------------------------------------

def _raw(imya, telefon, birthday=None, roditel=None, eshche_nomer=None):
    attrs = []
    if birthday:
        attrs.append({"attributeAlias": "birthday", "value": birthday})
    if roditel:
        attrs.append({"attributeAlias": "parent", "value": roditel})
    if eshche_nomer:
        attrs.append({"attributeAlias": "phone2", "value": eshche_nomer})
    return json.dumps({"name": imya, "phone": telefon, "attributes": attrs}, ensure_ascii=False)


KURSY = [(1, "Подготовка к школе"), (2, "Английский язык"), (3, "Мини-сад"),
         (4, "Раннее развитие с Еленой. Музыка и речь"), (5, "ИЗО-студия"), (6, "Шахматы"),
         (7, "Ментальная арифметика"), (8, "Робототехника"), (9, "Набор новых групп")]
# Имена групп — по образцу живых (имена групп — не персональные данные).
GRUPPY = [
    (101, "2627_ПШ_вт-пт_17:00_5-7 лет_ПШ1 нечитающие (Гр9)", 1, 8),
    (102, "2627_ПШ_вт-пт_16:00_4-5 лет_ПШ1 нечитающие (Гр8)", 1, 8),      # MERGE — не записывать
    (103, "2627_ПШ_чт 19:00 + сб 11:00_5-7 лет_ПШ1 нечитающие (Гр13)", 1, 8),  # HOLD — пауза
    (104, "2627_ПШ_пн-чт_18:00_4-7 лет_ПШ2 читающие (Гр7)", 1, 8),        # WAITLIST — полная
    (105, "2627_АЯ_вт-чт_17:00_5-7 лет_A1 Pre-Starter (Гр6)", 2, 8),
    (106, "2627_Мини-сад_9:00-13:00", 3, 15),
    (107, "2627_Нулевой класс_10:00-14:00", 3, 15),
    (108, "2627_РР.Музыка и речь_Группа 4_пн - пт_10:00_1,3 - 1,8", 4, 7),
    (109, "2627_ИЗО_Группа 4_пн - ср_19:00_3.5 - 5 лет_Творчество", 5, 8),
    (110, "2627_ШАХ_Группа 2_пт 19:00 + вс 12:00_6-12 начинающие", 6, 8),
    (111, "2627_Заявки_Танцы", 9, 0),
    (112, "2627_АЯ_пн-ср_16:00_7-8 лет_Pre-A1 Starters (Гр1)", 2, 8),
    (113, "2526_АЯ_пн-ср_16:00_6-7 лет", 2, 8),
    (114, "2627_Робототехника_пт 16:00 + вс 14:30_4-7 лет", 8, 8),
    (115, "2627_МА_вс_12:00_4-6 лет", 7, 8),
    (116, "2627_ПШ_ср-пт_19:00_5-7 лет_ПШ1 нечитающие (Гр15)", 1, 8),     # тот же уровень, что Гр9, другое время
    (117, "2627_РР.Музыка и речь_Группа 7_сб_16:30_1.8 - 2.2 года", 4, 7),  # раз в неделю
    (118, "2627_АЯ_вт-чт_16:00_12-13 лет_A1 Starter (Гр5)", 2, 8),         # WAITLIST «добирать 10–12»
]
SEMYA = "79990001001"      # двое детей на одном номере, у младшего нет дня рождения
KLIENT = "79990002001"     # уже платили
NOMER_A = "79990001234"    # две семьи с одинаковыми последними цифрами
NOMER_B = "79990011234"
STOP = "79990004001"       # статус «не писать / не звонить»
VTOROJ = "79990006001"     # номер только в карточке как второй
NEZNAKOMYJ = "79990009999"  # нет в CRM
STARSHIJ = "79990008001"   # старший брат 14 лет (не наш возраст) и сестра 6 лет


def zasejat(baza: str):
    c = sqlite3.connect(baza)
    c.executemany("INSERT OR REPLACE INTO courses (id, name, raw) VALUES (?,?,'{}')", KURSY)
    c.executemany("INSERT OR REPLACE INTO classes (id, name, course_id, status, max_students, raw) "
                  "VALUES (?,?,?,'opened',?,'{}')", GRUPPY)
    lyudi = [
        (9001, "Тестова Ева", SEMYA, 125953, _raw("Тестова Ева", SEMYA, "2021-03-10", "Мама: Тестова Ольга")),
        (9002, "Тестов Марк", SEMYA, 125951, _raw("Тестов Марк", SEMYA)),
        (9003, "Пробная Алиса", KLIENT, 125955, _raw("Пробная Алиса", KLIENT, "2018-05-01")),
        (9004, "Образцова Мила", NOMER_A, 146950, _raw("Образцова Мила", NOMER_A, "2020-01-15")),
        (9005, "Примеров Лев", NOMER_B, 125951, _raw("Примеров Лев", NOMER_B, "2017-02-02")),
        (9006, "Стопова Вера", STOP, 146328, _raw("Стопова Вера", STOP, "2019-06-01")),
        (9007, "Второв Ян", "79990005001", 125951, _raw("Второв Ян", "79990005001", "2020-09-09",
                                                        eshche_nomer=VTOROJ)),
        (9008, "Старшев Глеб", STARSHIJ, 125951, _raw("Старшев Глеб", STARSHIJ, "2012-01-01")),
        (9009, "Старшева Ника", STARSHIJ, 125951, _raw("Старшева Ника", STARSHIJ, "2020-05-05")),
    ]
    c.executemany("INSERT OR REPLACE INTO users (id, name, phone, client_state_id, raw) VALUES (?,?,?,?,?)",
                  lyudi)
    zapisi = [(9001, 101, 58132), (9002, 111, 50509), (9003, 112, 2), (9110, 101, 99336)]
    zapisi += [(9200 + i, 101, 2) for i in range(4)]            # ПШ Гр9: 5 живых → 3 места
    zapisi += [(9300 + i, 112, 2) for i in range(7)]            # АЯ Гр1: 8 из 8
    zapisi += [(9400 + i, 110, 2) for i in range(8)]            # шахматы: 8 из 8
    zapisi += [(9500 + i, 106, 2) for i in range(5)]            # мини-сад
    zapisi += [(9600 + i, 107, 2) for i in range(3)]            # нулевой класс: 3 из 10
    zapisi += [(9700 + i, 104, 2) for i in range(6)]            # ПШ2 (лист)
    zapisi += [(9800 + i, 105, 2) for i in range(2)]            # АЯ 5–7: 2 из 8
    zapisi += [(9900, 109, 2), (9901, 114, 2)]
    zapisi += [(9910 + i, 116, 2) for i in range(6)]            # ПШ Гр15: 2 места (меньше, чем в Гр9)
    zapisi += [(9920 + i, 118, 2) for i in range(5)]            # АЯ Гр5: 3 места
    c.executemany("INSERT OR REPLACE INTO joins (id, user_id, class_id, status_id, raw) VALUES (?,?,?,?,'{}')",
                  [(i + 1, u, k, s) for i, (u, k, s) in enumerate(zapisi)])
    c.executemany("INSERT OR REPLACE INTO lessons (id, date, begin_time, class_id) VALUES (?,?,?,?)",
                  [(5001, "2026-05-20", "16:00", 113), (5002, "2026-10-16", "17:00", 101)])
    c.executemany("INSERT OR REPLACE INTO lesson_records (id, lesson_id, user_id, visit, raw) VALUES (?,?,?,?,?)",
                  [(6001, 5001, 9003, 1, '{"visit": true}'), (6002, 5002, 9001, 0, '{"test": true}')])
    c.execute("INSERT OR REPLACE INTO payments (id, user_id, date, summa, raw) VALUES (7001, 9003, '2026-10-05', 5200, '{}')")
    c.execute("""CREATE TABLE IF NOT EXISTS crm_comments (id INTEGER PRIMARY KEY, user_id INTEGER, ts TEXT,
                 manager_id INTEGER, text TEXT)""")
    c.executemany("INSERT OR REPLACE INTO crm_comments VALUES (?,?,?,?,?)", [
        (8001, 9001, "2026-10-14T11:35:00", 202856,
         "14.10 11:30, исходящий (Лена), 2 мин 10 с. Мама выбирает между вторником-пятницей и субботой."),
        (8002, 9001, "2026-10-13T18:20:00", 202856, "Мама просила перезванивать после 18:00"),
    ])
    c.execute("""CREATE TABLE IF NOT EXISTS mango_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,
                 phone TEXT, direction TEXT, state TEXT, rec_id TEXT, secs INTEGER DEFAULT 0, ext TEXT)""")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_mango_calls_uniq ON mango_calls (ts, phone, direction)")
    c.executemany("INSERT OR IGNORE INTO mango_calls (ts, phone, direction, state, rec_id, secs, ext) "
                  "VALUES (?,?,?,?,?,?,?)", [
                      ("2026-10-14T11:30:00+03:00", SEMYA[-10:], "out", "talked", "", 130, "12"),
                      ("2026-10-14T11:50:00+03:00", KLIENT[-10:], "in", "missed", "", 0, "12"),
                      ("2026-10-13T17:00:00+03:00", NOMER_A[-10:], "in", "talked", "", 60, "10"),
                      ("2026-10-14T10:05:00+03:00", "9990077001", "in", "missed", "", 0, ""),
                  ])
    c.execute("""CREATE TABLE IF NOT EXISTS plan_inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, ts TEXT,
                 who TEXT, text TEXT, phone TEXT, source TEXT, done INTEGER DEFAULT 0,
                 tries INTEGER DEFAULT 0, prio INTEGER)""")
    c.executemany("INSERT INTO plan_inbox (day, ts, who, text, phone, source, done, tries) VALUES (?,?,?,?,?,?,?,?)", [
        (DEN, DEN + "T11:40", "Лена", "Перезвонить в пятницу: мама ждёт расписание ПШ на субботу",
         SEMYA, "звонок 11:30", 0, 1),
        (DEN, DEN + "T09:00", "Лиза", "⏳ с 12.10: Отправить семье договор и чек — 7 999 000-10-01",
         "", "наряд", 0, 0),
        ("2026-10-13", "2026-10-13T10:00", "Лена", "Закрытое обещание 79990001001", SEMYA, "звонок 10:00", 1, 0),
        (DEN, DEN + "T08:00", "Ира", "С9 · База: позвонить по заявке", KLIENT, "возврат: база", 0, 0),
    ])
    pervyj = c.execute("SELECT id FROM plan_inbox WHERE source='звонок 11:30'").fetchone()[0]
    c.execute("CREATE TABLE IF NOT EXISTS obzvon_dnya (item_id INTEGER PRIMARY KEY, itog TEXT, note TEXT, who TEXT, ts TEXT)")
    c.execute("INSERT OR REPLACE INTO obzvon_dnya VALUES (?, 'dum', 'думают до пятницы', 'Ира', ?)",
              (pervyj, DEN + "T11:45:00"))
    c.execute("""CREATE TABLE IF NOT EXISTS pult_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, who TEXT,
                 t TEXT, text TEXT, done INTEGER DEFAULT 0, ord INTEGER DEFAULT 0, ts TEXT, note TEXT, prio INTEGER)""")
    c.execute("INSERT INTO pult_tasks (day, who, t, text) VALUES (?, 'Лена', '15:00', ?)",
              (DEN, "Подтвердить пробные: <b>Тестова Ева</b> 79990001001"))
    c.execute("""CREATE TABLE IF NOT EXISTS wazzup_inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
                 chat_type TEXT, text TEXT, message_id TEXT UNIQUE)""")
    c.execute("""CREATE TABLE IF NOT EXISTS wazzup_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
                 message_id TEXT, text TEXT, author_id TEXT, author_name TEXT)""")
    c.execute("INSERT OR IGNORE INTO wazzup_inbox (ts, phone, chat_type, text, message_id) "
              "VALUES (?, ?, 'whatsapp', 'Здравствуйте, а есть места в субботу?', 'liniya-test-1')",
              (DEN + "T11:00:00+03:00", SEMYA))
    c.commit()
    c.close()


# --- проверки ------------------------------------------------------------------

def test_teksty(m):
    print("Тексты кнопок (app/liniya_teksty.py)")
    from app import autopilot, liniya, liniya_teksty as t
    from app.pult_proverka import ZAPRESHCHENO
    vse = []
    for k, o in t.OTVETY.items():
        vse += [o["knopka"], o["otvet"], o.get("shag", ""), o.get("pomni", "")]
        vse += list((o.get("otvet_po_vidu") or {}).values())
    vse += list(t.PERVOE_ZANYATIE.values()) + list(t.MESTO.values()) + list(t.VOPROS_UROVEN.values())
    proverka("место «придержу» не обещаем — его держит только запись в CRM",
             not any("придерж" in x for x in vse), [x[:60] for x in vse if "придерж" in x])
    proverka("у каждого вида первого занятия — свой ответ «Пробное бесплатное?»",
             set(t.OTVETY["probnoe"]["otvet_po_vidu"]) == set(t.PERVOE_ZANYATIE))
    proverka("ни одного «бесплатного пробного занятия»",
             not any(ZAPRESHCHENO.search(x) for x in vse),
             [x[:60] for x in vse if ZAPRESHCHENO.search(x)])
    proverka("все 8 кнопок на месте", list(t.KNOPKI) == ["dorogo", "podumayu", "daleko", "vremya_ne_podhodit",
                                                         "skolko_stoit", "probnoe", "skidki", "pedagogi"])
    neizv = {p for x in vse for p in re.findall(r"\{([a-z_]+)\}", x)} - t.PODSTANOVKI
    proverka("все подстановки известны странице", not neizv, neizv)
    proverka("«условно-бесплатное» — дословная формулировка",
             "условно-бесплатное: не понравится — платить не нужно; понравится — занятие входит в первый абонемент"
             in t.PERVOE_ZANYATIE["obychnoe"])
    for x in vse:
        for mm in re.finditer(r"бесплатн\w* пробн\w* д", x, re.I):
            okno = x[max(0, mm.start() - 40):mm.start()].lower()
            proverka("«бесплатный пробный день» — только про мини-сад и нулевой класс",
                     "мини-сад" in okno or "нулев" in okno, x[:80])
    proverka("в шаблонах нет цен числом и «осталось N мест»",
             not any(re.search(r"\d[\d\s]*₽|осталось\s+\d", x) for x in vse))
    obshchie = liniya.sobrat_otvety("Ева", True, [])["_bez"]
    sk = obshchie["skidki"]["otvet"]
    proverka("скидки — числа из autopilot.SKIDKA_*",
             f"Минус {autopilot.SKIDKA_PERVYJ}%" in sk and f"минус {autopilot.SKIDKA_SAD}%" in sk
             and f"Минус {autopilot.SKIDKA_VTOROJ}%" in sk, sk[:120])
    proverka("скидки «не суммируются»", "не суммируются" in sk)
    proverka("имя ребёнка подставлено", "что Ева уже умеет" in obshchie["probnoe"]["otvet"])
    proverka("адрес — из content.py", "б-р Маршала Рокоссовского, 6 к1В" in obshchie["daleko"]["otvet"])
    bez_imeni = liniya.sobrat_otvety("", True, [])["_bez"]["probnoe"]["otvet"]
    proverka("нет имени — «ребёнок»", "что ребёнок уже умеет" in bez_imeni)
    proverka("пустая подстановка убирает предложение, а не оставляет «группе «»»",
             t.zapolnit("Первое. В группе «{gruppa}» есть место. Последнее.", {"gruppa": ""})
             == "Первое. Последнее.")
    proverka("опечатка в подстановке остаётся на виду", "{grupa}" in t.zapolnit("Есть {grupa}.", {}))
    proverka("пустая подстановка-предложение не тянет за собой соседние",
             t.zapolnit("Вопрос? {vopros_uroven} {pervoe_zanyatie} {mesto}",
                        {"vopros_uroven": "", "pervoe_zanyatie": "Первое.", "mesto": "Место."})
             == "Вопрос? Первое. Место.")
    staraya = liniya.sobrat_otvety("Ева", False, [])["_bez"]
    proverka("семья уже платила — без скидки дня первого занятия",
             "скидка" not in staraya["skolko_stoit"]["otvet"] and "скидка" not in staraya["dorogo"]["otvet"])
    zapolnennye = [s for gr in liniya.sobrat_otvety("Ева", True, liniya.vse_gruppy()).values()
                   for o in gr.values() for s in (o["otvet"], o["shag"], o["pomni"])]
    proverka("заполненные ответы тоже без «бесплатного пробного занятия»",
             not any(ZAPRESHCHENO.search(s) for s in zapolnennye))
    proverka("в заполненных ответах не осталось фигурных скобок",
             not any("{" in s or "}" in s for s in zapolnennye),
             [s[:60] for s in zapolnennye if "{" in s][:2])
    proverka("в заполненных ответах нет «осталось N мест»",
             not any(re.search(r"осталось\s+\d", s) for s in zapolnennye))


def _html(t):
    """Страница без <script>: в JS-подсказке под полем есть и «нет в CRM», и «никого нет»."""
    return re.sub(r"<script.*?</script>", "", t, flags=re.S)


def _D(html):
    """Данные кнопок, вшитые в страницу (var D = …;)."""
    mm = re.search(r"var D = (\{.*?\});", html, re.S)
    return json.loads(mm.group(1)) if mm else {}


def test_stranicy(c, m):
    from app import liniya
    print("Данные для страницы")
    s = liniya.js_json({"a": "</script><b>'&" + chr(0x2028)})
    proverka("JSON в <script> экранирован",
             not re.search("[<>'&" + chr(0x2028) + "]", s) and json.loads(s)["a"] == "</script><b>'&" + chr(0x2028))
    print("Пустая страница")
    r = c.get("/liniya")
    proverka("/liniya → 200", r.status_code == 200, r.status_code)
    proverka("поле «последние 4 цифры»", 'name="q"' in r.text and "Последние 4 цифры" in r.text)
    proverka("ссылки на доб. 10 и 12", "/liniya?ext=10" in r.text and "/liniya?ext=12" in r.text)
    proverka("метка «черновик — утверждает Борис»", "черновик — утверждает Борис" in r.text)
    for k in ("Дорого", "Подумаю", "Далеко", "Время не подходит", "Сколько стоит",
              "Пробное бесплатное?", "Скидки", "Про педагогов"):
        proverka(f"кнопка «{k}»", f">{k}</button>" in r.text)
    D = _D(r.text)
    proverka("ответы уже в странице, без запроса",
             D.get("otvety", {}).get("_bez", {}).get("dorogo", {}).get("otvet", "").startswith("Понимаю")
             and "Понимаю, хочется быть уверенными" in r.text)
    proverka("у каждой кнопки есть ответ и следующий шаг",
             all(D["otvety"]["_bez"].get(k, {}).get("otvet") and D["otvety"]["_bez"][k].get("shag")
                 for k in ("dorogo", "podumayu", "daleko", "vremya_ne_podhodit", "skolko_stoit",
                           "probnoe", "skidki", "pedagogi")))
    proverka("на странице нет таблиц (узкий экран)", "<table" not in r.text.split('<main>')[-1])

    print("Знакомый номер: двое детей, обещания, последний звонок")
    r = c.get(f"/liniya?phone={SEMYA}")
    h = r.text
    proverka("200", r.status_code == 200, r.status_code)
    proverka("оба ребёнка на номере", "Тестова Ева" in h and "Тестов Марк" in h)
    vozr = round((date.today().year - 2021) + (date.today().month - 3) / 12, 1)
    proverka("возраст Евы из карточки", f"{vozr:g}".replace(".", ",") in h, vozr)
    proverka("у Марка возраст не указан", "возраст не указан" in h)
    proverka("статус без таблицы статусов — запасное имя", "Посетил пробное" in h and "1. Новый лид" in h)
    proverka("ссылка на карточку МойКласса", "https://app.moyklass.com/user/9001" in h)
    proverka("Ева записана (живая запись), Марк — нет", "записан</span>" in h and "не записан" in h)
    proverka("Марк в листе заявок", "в листе заявок: Заявки_Танцы" in h)
    proverka("ближайшее занятие и пометка пробного", "16.10 17:00" in h and "пробное" in h)
    proverka("родитель из карточки", "Мама: Тестова Ольга" in h)
    proverka("последний звонок: поговорили, доб. 12", "поговорили 2 мин 10 с" in h and "доб. 12" in h)
    proverka("итог обзвона кнопкой", "думают, перезвонить" in h and "думают до пятницы" in h)
    proverka("о чём говорили — из разбора звонков", "Мама выбирает между вторником-пятницей" in h)
    proverka("комментарий админа отдельно", "перезванивать после 18:00" in h)
    proverka("последнее сообщение", "есть места в субботу" in h)
    proverka("обещание по номеру из поля", "Перезвонить в пятницу" in h and "обещали в разговоре" in h)
    proverka("обещание по номеру внутри текста + обязательство", "Отправить семье договор" in h
             and "обязательство" in h and "с 12.10" in h)
    proverka("закрытое обещание не показано", "Закрытое обещание" not in h)
    proverka("задача смены с номером в тексте", "задача смены" in h and "Подтвердить пробные" in h)
    proverka("нет таблиц", "<table" not in h.split('<main>')[-1])
    proverka("черновик помечен", "черновик — утверждает Борис" in h)

    j = c.get(f"/api/liniya?phone={SEMYA}").json()
    top, eshche = j["gruppy"]["top"], j["gruppy"]["eshche"]
    imena = [g["name"] for g in top]
    proverka("API: 2–3 группы первыми", 2 <= len(top) <= 3, imena)
    proverka("API: первым — нулевой класс (по методичке для 5–7)", top and top[0]["predmet"] == "Нулевой класс", imena)
    proverka("API: по одной на направление", len({g["predmet"] for g in top}) == len(top), imena)
    vse_imena = [g["name"] for g in top + eshche]
    proverka("API: «не записывать» и «пауза» не предлагаем",
             not any("(Гр8)" in n or "(Гр13)" in n for n in vse_imena), vse_imena)
    proverka("API: «полная» — не первыми, без числа мест, с пометкой листа",
             not any("(Гр7)" in n for n in imena)
             and any("(Гр7)" in g["name"] and g["mesto"] == "list" and g["free_text"] == "полная"
                     and "лист ПШ2" in g["zametka"] for g in eshche), vse_imena)
    proverka("API: полные группы не предлагаем", not any("ШАХ" in n for n in vse_imena), vse_imena)
    proverka("API: места не отрицательные", all(g["free"] >= 0 for g in top + eshche))
    psh = next((g for g in top if g["name"].startswith("ПШ_вт-пт_17:00")), None)
    proverka("API: места как на /mesta (не пришедший на пробное места не занимает)",
             psh and psh["free"] == 3, psh and psh["free"])
    proverka("API: цена с подписью из прайса", psh and psh["cena"] == "8 400 ₽ за 8 занятий (2 р/нед)",
             psh and psh["cena"])
    proverka("API: абонемент поменьше", psh and psh["cena_menshe"] == "5 000 ₽ за 4 занятия (1 р/нед)",
             psh and psh["cena_menshe"])
    proverka("API: когда — для экрана и для речи", psh and psh["kogda"] == "Вт и Пт, 17:00"
             and psh["kogda_rech"] == "по вторникам и пятницам в 17:00", psh and psh["kogda"])
    nk = next((g for g in top if g["predmet"] == "Нулевой класс"), None)
    proverka("API: нулевой класс — возраст из описаний, норма 10, цена своей строкой",
             nk and nk["vozrast"] == "5–7 лет" and nk["free"] == 7
             and nk["cena"] == "35 500 ₽ за 20 посещений (5 дней)", nk)
    otv = j["otvety"]
    proverka("API: ответы для каждой группы", all(str(g["id"]) in otv for g in top))
    proverka("API: в ответе про нулевой класс — бесплатный пробный день и скидка 8%",
             "бесплатный пробный день" in otv[str(nk["id"])]["skolko_stoit"]["otvet"]
             and "скидка 8%" in otv[str(nk["id"])]["skolko_stoit"]["otvet"])
    proverka("API: в ответе про ПШ — группа и цена",
             "В группе «Подготовка к школе, ПШ1 нечитающие, 5–7 лет» абонемент — 8 400 ₽ за 8 занятий"
             in otv[str(psh["id"])]["skolko_stoit"]["otvet"])
    proverka("API: новая семья — скидка 15% в ответе про ПШ", "скидка 15%" in otv[str(psh["id"])]["skolko_stoit"]["otvet"])
    proverka("API: имя ребёнка в ответах", "как Ева занимается" in otv["_bez"]["dorogo"]["otvet"])

    def otvet(gid, k, pole="otvet"):
        return (otv.get(str(gid), {}).get(k) or otv["_bez"][k])[pole]

    vnp = otvet(psh["id"], "vremya_ne_podhodit")
    proverka("«Время не подходит» — другое время того же уровня, а не та же группа",
             "Есть ещё группа «Подготовка к школе, ПШ1 нечитающие, 5–7 лет» по средам и пятницам в 19:00" in vnp
             and "по вторникам и пятницам в 17:00" not in vnp, vnp)
    proverka("«Время не подходит»: нет другого времени — предложения про место нет",
             "Есть ещё группа" not in otvet(nk["id"], "vremya_ne_podhodit"), otvet(nk["id"], "vremya_ne_podhodit"))
    gr7 = next(g for g in eshche if "(Гр7)" in g["name"])
    vse_gr7 = " ".join(otvet(gr7["id"], k) for k in otv["_bez"])
    proverka("полная группа: ни «есть место в группе», ни «придержу», ни «ходить раз в неделю»",
             f"место в группе «{gr7['opisanie']}»" not in vse_gr7 and "придерж" not in vse_gr7
             and "один день из двух" not in vse_gr7, vse_gr7[:200])
    proverka("«Подумаю» не обещает придержать место",
             not any("придерж" in o["otvet"] for gr in otv.values() for o in gr.values()))
    proverka("«Подумаю»: как закрепить место — записью на первое занятие",
             "запишите на первое занятие" in otv["_bez"]["podumayu"]["shag"])
    proverka("«Дорого», шаг: абонемент поменьше — из прайса группы",
             "Назвать абонемент поменьше — 5 000 ₽ за 4 занятия (1 р/нед)." in otvet(psh["id"], "dorogo", "shag"))
    ma = next((g for g in top + eshche if g["name"].startswith("МА_")), None)
    proverka("«Дорого», шаг: у группы нет абонемента поменьше — не просим его назвать",
             ma and "поменьше" not in otvet(ma["id"], "dorogo", "shag")
             and otvet(ma["id"], "dorogo", "shag").startswith("Предложить записаться"),
             ma and otvet(ma["id"], "dorogo", "shag"))
    proverka("«Пробное бесплатное?» про нулевой класс — сразу про пробный день",
             otvet(nk["id"], "probnoe").startswith("В мини-саду и нулевом классе — бесплатный пробный день"),
             otvet(nk["id"], "probnoe")[:80])
    proverka("«Пробное бесплатное?» про ПШ — условно-бесплатное и без чужих направлений",
             otvet(psh["id"], "probnoe").startswith("Первое занятие условно-бесплатное")
             and "ИЗО" not in otvet(psh["id"], "probnoe"), otvet(psh["id"], "probnoe")[:80])
    proverka("«Пробное бесплатное?» без группы — про все направления",
             "мини-саду" in otv["_bez"]["probnoe"]["otvet"] and "ИЗО" in otv["_bez"]["probnoe"]["otvet"])

    print("Мини-сад: места по дням, без числа")
    j = c.get(f"/api/liniya?phone={SEMYA}&age=3").json()
    sad = next((g for g in j["gruppy"]["top"] if g["predmet"] == "Мини-сад"), None)
    proverka("мини-сад предложен первым для 3 лет", j["gruppy"]["top"] and j["gruppy"]["top"][0]["predmet"] == "Мини-сад",
             [g["name"] for g in j["gruppy"]["top"]])
    proverka("вместо числа мест — «по дням — сверить в CRM»", sad and sad["free_text"] == "по дням — сверить в CRM")
    proverka("мини-сад: будни 9:00–13:00", sad and sad["kogda"] == "будни, 9:00–13:00")
    proverka("введённый возраст важнее карточки", j["vozrast"] == 3 and j["vozrast_otkuda"] == "введён")
    otv_sad = " ".join(o["otvet"] for o in j["otvety"].get(str(sad["id"]), {}).values()) if sad else ""
    proverka("мини-сад: в ответах ни «есть место», ни «придержу» — места по дням",
             sad and "есть место" not in otv_sad and "придерж" not in otv_sad, otv_sad[:200])
    proverka("мини-сад: «Пробное бесплатное?» — про пробный день",
             sad and j["otvety"][str(sad["id"])]["probnoe"]["otvet"].startswith(
                 "В мини-саду и нулевом классе — бесплатный пробный день"))

    print("Группа раз в неделю, набор с условием, возраст центра")
    from app import liniya as L
    vse_g = {g["id"]: g for g in L.vse_gruppy()}
    sb = vse_g.get(117, {})
    proverka("раз в неделю (сб): цена строкой «1 р/нед», без «поменьше» и «один день из двух»",
             sb.get("cena") == "5 200 ₽ за 4 занятия (1 р/нед)" and sb.get("cena_menshe") == ""
             and sb.get("raz_v_nedelyu") == "", {k: sb.get(k) for k in ("cena", "cena_menshe", "raz_v_nedelyu")})
    j = c.get(f"/api/liniya?phone={NEZNAKOMYJ}&age=2").json()
    o117 = j["otvety"].get("117", {})
    vnp117 = (o117.get("vremya_ne_podhodit") or j["otvety"]["_bez"]["vremya_ne_podhodit"])["otvet"]
    proverka("раз в неделю: «Сколько стоит» — 5 200 ₽, «Время не подходит» — без «один день из двух»",
             "5 200 ₽ за 4 занятия (1 р/нед)" in o117.get("skolko_stoit", {}).get("otvet", "")
             and "8 600" not in json.dumps(o117, ensure_ascii=False) and "один день из двух" not in vnp117,
             o117.get("skolko_stoit"))
    a18 = vse_g.get(118, {})
    proverka("«добирать 10–12» — не «полная»: места есть, условие в пометке",
             a18.get("mesto") == "est" and a18.get("dobor", "").startswith("добирать 10–12")
             and a18.get("free_text") == "3 места" and "полная" not in a18.get("zametka", ""), a18)
    for vozr in ("14", "0,8"):
        j = c.get(f"/api/liniya?phone={NEZNAKOMYJ}&age={vozr}").json()
        proverka(f"возраст {vozr}: групп нет — «не наш возраст», а не лист ожидания",
                 not j["gruppy"]["top"] and not j["gruppy"]["eshche"]
                 and "Не наш возраст" in j["gruppy"]["pochemu_pusto"], j["gruppy"]["pochemu_pusto"])
    j = c.get(f"/api/liniya?phone={STARSHIJ}").json()
    proverka("двое детей, старшему 14: группы — под младшую, а не «не наш возраст»",
             j.get("fokus") == 9009 and j["gruppy"]["top"], (j.get("fokus"), j["gruppy"].get("pochemu_pusto")))
    j = c.get(f"/api/liniya?phone={NEZNAKOMYJ}&age=12").json()
    proverka("12 лет — ещё наш возраст", any(g["id"] == 118 for g in j["gruppy"]["top"]),
             [g["name"] for g in j["gruppy"]["top"]])

    print("Ребёнок без возраста")
    r = c.get(f"/liniya?phone={SEMYA}&child=9002")
    proverka("200", r.status_code == 200)
    proverka("групп нет, просим возраст", "Возраст неизвестен — введите его" in r.text)
    proverka("поле возраста на месте", 'name="age"' in r.text and 'name="child" value="9002"' in r.text)
    proverka("кривые child и age — страница, а не ошибка",
             c.get(f"/liniya?phone={SEMYA}&child=abc&age=пять").status_code == 200
             and c.get(f"/api/liniya?phone={SEMYA}&child=&kratko=").status_code == 200)

    print("Незнакомый номер")
    r = c.get(f"/liniya?phone={NEZNAKOMYJ}")
    proverka("200", r.status_code == 200)
    proverka("«нет в CRM»", "нет в CRM</span>" in _html(r.text))
    D = _D(r.text)
    proverka("скрипт нового контакта открыт сразу", ">Новый контакт</button>" in r.text
             and D.get("nachalo") == "novyj_kontakt"
             and "как к вам обращаться" in D["otvety"]["_bez"]["novyj_kontakt"]["otvet"])
    j = c.get(f"/api/liniya?phone={NEZNAKOMYJ}&age=5").json()
    proverka("с возрастом 5 — группы", len(j["gruppy"]["top"]) >= 2, [g["name"] for g in j["gruppy"]["top"]])
    proverka("ответ «новый контакт» заполнен группой",
             any("По возрасту есть место в группе «" in v.get("novyj_kontakt", {}).get("otvet", "")
                 for v in j["otvety"].values()))
    nov = {gid: (v.get("novyj_kontakt") or j["otvety"]["_bez"]["novyj_kontakt"])["otvet"]
           for gid, v in j["otvety"].items()}
    proverka("новый контакт, полная группа: «сейчас полная» и лист, а не «есть место»",
             "сейчас полная" in nov.get("104", "") and "лист ожидания" in nov.get("104", "")
             and "есть место" not in nov.get("104", "").replace("где место есть", ""), nov.get("104"))
    psh_n = nov.get("101", "")
    proverka("новый контакт, ПШ: сначала «читает?», потом группа и время",
             "Ребёнок уже читает?" in psh_n
             and psh_n.find("Ребёнок уже читает?") < psh_n.find("По возрасту есть место"), psh_n)
    proverka("новый контакт, нулевой класс: про чтение не спрашиваем", "читает" not in nov.get("107", "x читает"))
    proverka("новый контакт без группы: без «есть место» и без вопроса про уровень",
             "есть место" not in nov["_bez"] and "читает" not in nov["_bez"], nov["_bez"])
    j = c.get(f"/api/liniya?phone={NEZNAKOMYJ}&age=4").json()
    izo = j["otvety"].get("109", {}).get("probnoe", {}).get("otvet", "")
    proverka("ИЗО: «Пробное бесплатное?» начинается с платного пробного, без «условно-бесплатного»",
             izo.startswith("Первое занятие — знакомство") and "850 ₽" in izo and "условно" not in izo, izo[:90])
    proverka("звонок с незнакомого номера виден как контакт",
             c.get("/api/liniya?phone=79990077001").json()["kontakt"]["zvonki"][0]["itog"] == "пропущенный")

    print("Клиент, который уже платил")
    j = c.get(f"/api/liniya?phone={KLIENT}").json()
    proverka("семья не новая — скидки дня нет", j["novaya_semya"] is False and "уже платила" in j["skidka_podskazka"])
    proverka("последняя оплата", j["oplata"] == {"kogda": "05.10.2026", "summa": "5 200 ₽"})
    proverka("история посещений", j["deti"][0]["istoriya"] and j["deti"][0]["istoriya"][0]["kurs"] == "Английский язык")
    proverka("порция обзвона подписана как порция, а не обещание",
             j["obeshchaniya"] and j["obeshchaniya"][0]["vid"] == "порция обзвона")

    print("Стоп-статус и второй номер")
    r = c.get(f"/liniya?phone={STOP}")
    proverka("предупреждение «сами не звоним»", "⛔" in r.text and "сами не звоним" in r.text)
    j = c.get(f"/api/liniya?phone={VTOROJ}").json()
    proverka("номер из карточки как дополнительный", j.get("v_crm") and j.get("vtoroj_nomer")
             and j["deti"][0]["imya"] == "Второв Ян")

    print("Последние 4 цифры")
    r = c.get("/liniya?q=1234")
    proverka("две семьи — список на выбор", r.status_code == 200 and "Образцова Мила" in r.text
             and "Примеров Лев" in r.text and f"/liniya?phone={NOMER_A[-10:]}" in r.text)
    proverka("свежий контакт — выше", r.text.find("Образцова Мила") < r.text.find("Примеров Лев"))
    r = c.get("/liniya?q=2001")
    proverka("одна семья — открыта сразу", "Пробная Алиса" in r.text and "Кто на линии" in r.text
             and "Нашли по последним цифрам …2001" in r.text)
    r = c.get("/liniya?q=8888")
    proverka("никого — новый контакт", "никого нет" in _html(r.text) and _D(r.text).get("nachalo") == "novyj_kontakt")
    r = c.get("/liniya?q=7001")
    proverka("номер не из CRM, но звонил сегодня — найден и открыт",
             "нет в CRM</span>" in _html(r.text) and "Нашли по последним цифрам …7001" in r.text and "пропущенный" in r.text)
    r = c.get("/liniya?q=12a4")
    proverka("не цифры — понятная ошибка", "только цифры" in r.text)
    r = c.get("/liniya?q=123")
    proverka("три цифры — понятная ошибка", "В номере 3 цифры" in r.text)
    proverka("API: last4", len(c.get("/api/liniya?last4=1234").json()["spisok"]) == 2)
    proverka("номер целиком в поле — как ?phone=", "Тестова Ева" in c.get(f"/liniya?q=+7 (999) 000-10-01").text)
    r = c.get("/liniya?q=10-01")
    proverka("«10-01» ищем, но одну семью не открываем — список, проверить номер",
             "Номер на …1001" in r.text and "Тестова Ева" in r.text and "Кто на линии" not in r.text)
    for vvod in ("8 999", "+7 999"):
        r = c.get("/liniya", params={"q": vvod})
        proverka(f"«{vvod}» — начало номера, а не последние цифры",
                 "Похоже на начало номера" in r.text and "Кто на линии" not in r.text
                 and "никого нет" not in _html(r.text))
    r = c.get("/liniya?q=8999000100")
    proverka("10 цифр с 8 в начале — не хватает цифры, «нет в CRM» не показываем",
             "не хватает цифры" in r.text and "нет в CRM" not in _html(r.text))
    j = c.get("/api/liniya?q=1001&kratko=1").json()
    proverka("подсказка под полем: список без открытия семьи",
             j.get("q") == "1001" and [s["p10"] for s in j.get("spisok", [])] == ["9990001001"]
             and "deti" not in j and "otvety" not in j, j)
    proverka("подсказка под полем: на 8999 никого — пустой список",
             c.get("/api/liniya?q=8999&kratko=1").json() == {"q": "8999", "spisok": [], "eshche": 0})
    stranica = c.get("/liniya").text
    proverka("страница сама не открывает семью по 4 цифрам — только подсказка",
             "/api/liniya?kratko=1&q=" in stranica and 'id="ln-pod"' in stranica)

    print("Номер с лишними цифрами")
    for vvod, cifr in (("799900010011", "12 цифр"), ("799900010011234", "15 цифр"),
                       ("79990001001 79990002001", "22 цифры")):
        for param in ("phone", "q"):
            j = c.get("/api/liniya", params={param: vvod}).json()
            proverka(f"{param}={vvod}: ошибка «{cifr}», чужой номер не показан",
                     cifr in j.get("oshibka", "") and "telefon" not in j and j["rezhim"] == "pusto",
                     (j.get("oshibka"), (j.get("telefon") or {}).get("pokaz")))
    j = c.get("/api/liniya?phone=8120000001").json()
    proverka("ссылка с 10 цифрами (городской, 812…) — как есть", (j.get("telefon") or {}).get("p10") == "8120000001")
    j = c.get("/api/liniya?phone=7999000600").json()
    proverka("кусок чужого номера в карточке — не «второй номер»", j.get("v_crm") is False, j.get("deti"))

    print("Добавочный")
    r = c.get("/liniya?ext=12")
    h = r.text
    proverka("200", r.status_code == 200)
    proverka("главный — последний РАЗГОВОР, а не недозвон после него",
             "14.10 11:30 · исходящий · поговорили" in h and f"/liniya?phone={SEMYA[-10:]}" in h)
    proverka("честно про задержку", "примерно через минуту" in h and "последний закончившийся звонок" in h)
    proverka("карточка семьи под звонком", "Тестова Ева" in h)
    proverka("страница знает добавочный — будет спрашивать о новом звонке", '"ext": "12"' in h)
    proverka("без ?ext= опроса нет", '"ext": ""' in c.get(f"/liniya?phone={SEMYA}").text)
    r = c.get("/liniya?ext=10")
    proverka("доб. 10 сегодня без звонков — так и сказано", "законченных звонков на доб. 10" in r.text)
    con = sqlite3.connect(os.path.join(os.environ["KIDSUP_DATA_DIR"], "kidsup.db"))
    con.execute("INSERT INTO mango_calls (ts, phone, direction, state, rec_id, secs, ext) VALUES "
                "('2026-10-14T11:55:00+03:00', ?, 'in', 'talked', '', 95, '10')", (NOMER_B[-10:],))
    con.commit()
    con.close()
    r = c.get("/liniya?ext=10")
    proverka("доб. 10 со звонком — показан", "Примеров Лев" in r.text and "поговорили 1 мин 35 с" in r.text)
    j = c.get("/api/liniya?ext=10&kratko=1").json()
    proverka("короткий ответ для опроса", j.get("ts") == "2026-10-14T11:55:00+03:00", j)
    proverka("кривой добавочный — ошибка, не 500", "Добавочный" in c.get("/liniya?ext=1x").text)


def test_sosedi(c):
    print("Соседние страницы и ссылки «Линия»")
    r = c.get(f"/brief?phone={SEMYA}")
    proverka("/brief → 200 и ссылка", r.status_code == 200 and f"/liniya?phone={SEMYA}" in r.text, r.status_code)
    r = c.get(f"/brief?phone={NEZNAKOMYJ}")
    proverka("/brief незнакомый → 200 и ссылка", r.status_code == 200 and f"/liniya?phone={NEZNAKOMYJ}" in r.text)
    r = c.get("/pult")
    proverka("/pult → 200 и ссылка", r.status_code == 200 and "/liniya?phone=" in r.text, r.status_code)
    r = c.get("/pult?who=Лиза")
    proverka("/pult, колонка: номер внутри текста «7 999 000-10-01» — ссылка",
             r.status_code == 200 and "/liniya?phone=9990001001" in r.text)
    r = c.get("/pult?who=Лена")
    proverka("/pult, колонка: и у пункта с телефоном, и у задачи смены с номером в тексте",
             r.status_code == 200 and r.text.count("/liniya?phone=9990001001") >= 2,
             r.text.count("/liniya?phone=9990001001"))
    r = c.get("/obzvon-dnya")
    proverka("/obzvon-dnya → 200 и ссылка в шаблоне", r.status_code == 200 and "/liniya?phone=${esc(x.phone)}" in r.text,
             r.status_code)
    r = c.get("/waiting")
    proverka("/waiting → 200 и ссылка", r.status_code == 200 and f"/liniya?phone={SEMYA[-10:]}" in r.text, r.status_code)
    r = c.get(f"/obeshchaniya?who=Лена&day={DEN}")
    proverka("/obeshchaniya → 200 и ссылка", r.status_code == 200 and "/liniya?phone=79990001001" in r.text,
             r.status_code)
    proverka("в меню есть «Линия»", 'href="/liniya"' in c.get(f"/brief?phone={SEMYA}").text)


def test_store_calls(c, m):
    print("Журнал звонков: добавочный (autopilot.store_calls)")
    from zoneinfo import ZoneInfo
    from app import autopilot
    baza = os.path.join(os.environ["KIDSUP_DATA_DIR"], "kidsup.db")
    msk = ZoneInfo("Europe/Moscow")
    con = sqlite3.connect(baza)
    con.execute("DROP TABLE mango_calls")
    con.execute("""CREATE TABLE mango_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
                   direction TEXT, state TEXT, rec_id TEXT, secs INTEGER DEFAULT 0)""")
    con.execute("CREATE UNIQUE INDEX ix_mango_calls_uniq ON mango_calls (ts, phone, direction)")
    t0 = int(datetime(2026, 10, 14, 11, 20, tzinfo=msk).timestamp())
    con.execute("INSERT INTO mango_calls (ts, phone, direction, state, rec_id, secs) VALUES (?,?,?,?,?,?)",
                (datetime.fromtimestamp(t0, msk).isoformat(timespec="seconds"), "9990002001", "in",
                 "talked", "", 40))
    con.commit()
    con.close()
    r = c.get("/liniya?ext=12")
    proverka("журнал без колонки — страница честно говорит", "пока не знает добавочных" in r.text)
    stroki = [
        {"start": t0 + 600, "finish": t0 + 700, "answer": t0 + 610, "from_ext": "12",
         "from_num": "sip:user5@x", "to_ext": "", "to_num": "79990001001", "sip": "user5", "ext": "12", "rec": "r1"},
        {"start": t0 + 900, "finish": t0 + 990, "answer": t0 + 905, "from_ext": "",
         "from_num": "79990011234", "to_ext": "10", "to_num": "", "sip": "", "ext": "", "rec": "r2"},
        {"start": t0, "finish": t0 + 60, "answer": t0 + 20, "from_ext": "",
         "from_num": "79990002001", "to_ext": "12", "to_num": "", "sip": "", "ext": "", "rec": "r0"},
    ]
    dobavleno = autopilot.store_calls(stroki)
    con = sqlite3.connect(baza)
    kol = [x[1] for x in con.execute("PRAGMA table_info(mango_calls)")]
    vse = {(p, d): (s, e, sec) for p, d, s, e, sec in
           con.execute("SELECT phone, direction, state, ext, secs FROM mango_calls")}
    con.close()
    proverka("колонка ext добавлена", "ext" in kol, kol)
    proverka("добавлены две новые строки", dobavleno == 2, dobavleno)
    proverka("исходящий: добавочный звонившего", vse.get(("9990001001", "out")) == ("talked", "12", 90),
             vse.get(("9990001001", "out")))
    proverka("входящий без SIP: добавочный из to_ext", vse.get(("9990011234", "in")) == ("talked", "10", 85),
             vse.get(("9990011234", "in")))
    proverka("старая строка без добавочного дописана, остальное не тронуто",
             vse.get(("9990002001", "in")) == ("talked", "12", 40), vse.get(("9990002001", "in")))
    autopilot.store_calls(stroki)
    con = sqlite3.connect(baza)
    n = con.execute("SELECT COUNT(*) FROM mango_calls").fetchone()[0]
    con.close()
    proverka("повторный опрос дублей не плодит", n == 3, n)
    r = c.get("/liniya?ext=10")
    proverka("после опроса доб. 10 виден", "Примеров Лев" in r.text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=ROOT)
    ap.add_argument("--db", default="")
    ap.add_argument("--work", default="")
    a = ap.parse_args()
    work = a.work or tempfile.mkdtemp(prefix="liniya_")
    os.makedirs(work, exist_ok=True)
    if a.db:
        shutil.copy(a.db, os.path.join(work, "kidsup.db"))
    m = zapustit(os.path.abspath(a.tree), work)
    zasejat(os.path.join(work, "kidsup.db"))
    from fastapi.testclient import TestClient
    c = TestClient(m.app)
    test_teksty(m)
    test_stranicy(c, m)
    test_sosedi(c)
    test_store_calls(c, m)
    chuzhie = [x for x in SET if any("liniya" in f for f in x[1])]
    proverka("«Линия» не ходит в сеть", not chuzhie, chuzhie)
    proverka("ни одной отправки", not LOVUSHKI, LOVUSHKI)
    print(f"\nИтого: {proshlo} OK, {oshibok} ошибок · стенд {work}")
    if SET:
        print("  (сеть пытались открыть не из «Линии»: " + ", ".join(sorted({";".join(x[1]) for x in SET}))[:300] + ")")
    sys.exit(1 if oshibok else 0)


if __name__ == "__main__":
    main()
