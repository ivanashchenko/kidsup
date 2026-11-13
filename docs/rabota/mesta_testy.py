# -*- coding: utf-8 -*-
"""Проверки единого счёта мест и честного «оплатили» — на стенде, без сервера и без сети.

13.11.2026, Борис (решение М2, дословно «М, П, Т — по рекомендациям»): свободные места
считаем «как в документе владельца — без закрытых для записи групп, сад и нулевой класс
по дням». Правило одно — app/mesta_pravilo.py; им считают страница «Места» (/mesta,
/api/mesta/tablica), «Подбор группы» (/enrollment), блок мест на пульте и документ владельца
docs/rabota/mesta_boris.py. «Оплатили» — дети «Учится» с оплаченным абонементом на текущий
месяц (app/mesta.py, oplatili_mesyac), а не «платил хоть раз с июня».

Здесь зафиксировано:
  А. само правило (закрытая группа не в счёте; сад и нулевой — по дням; удалённая в МойКлассе
     запись и вторая запись того же ребёнка места не занимают; логопеды, «Заявки», праздники);
  Б. документ владельца считает тем же правилом и сходится со страницей до единицы;
  В. страницы «Места», «Подбор группы» и блок пульта показывают те же числа;
  Г. «оплатили»: ребёнок с двумя абонементами — один оплативший; прошлый месяц, частичная
     оплата, удалённый абонемент — не оплата; ходят = оплатили + не оплатили;
  Д. «Без оплаты» (/voronka): стадии по оплате месяца, честная подпись к «цели 311».

    python3 docs/rabota/mesta_testy.py [--tree КОРЕНЬ] [--work ПАПКА]

Обвязка — из liniya_testy.py: сеть закрыта, отправки — ловушки, автопилот не стартует.
Репозиторий публичный: данные только выдуманные (дети — «Примеров Ребёнок 1001…», семья стенда
79990001001). Имена групп — по образцу живых (имена групп — не персональные данные).
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SEGODNYA = dt.date(2026, 11, 12)       # четверг; «сегодня» стенда для счёта мест и оплат
oshibok = 0
proshlo = 0


def proverka(nazvanie, ok, podrobno=""):
    global oshibok, proshlo
    ok = bool(ok)
    oshibok += not ok
    proshlo += ok
    print(("  OK      " if ok else "  ОШИБКА  ") + nazvanie + ("" if ok or podrobno == "" else f"  → {podrobno}"))


# --- выдуманные данные ---------------------------------------------------------
# Группы: (id, имя, статус в МойКлассе, вместимость)
GRUPPY = [
    (201, "2627_ПШ_вт-пт_17:00_5-7 лет_ПШ1 нечитающие (Гр9)", "opened", 8),       # открытая
    (202, "2627_ПШ_пн-чт_16:00_5-7 лет_ПШ1 нечитающие (Гр5)", "opened", 8),       # закрыта решением (CLOSED)
    (203, "2627_АЯ_пн-ср_16:00_7-8 лет_Pre-A1 Starters (Гр1)", "opened", 8),      # переполнена: 10 из 8
    (204, "2627_ИЗО_Группа 4_пн - ср_19:00_3.5 - 5 лет_Творчество", "opened", 8),  # нет занятий впереди
    (205, "2627_Мини-сад_9:00-13:00", "opened", 12),                              # по дням, потолок 6/8
    (206, "2627_Нулевой класс_10:00-14:00", "opened", 12),                        # по дням, потолок из МойКласса
    (207, "2627_ЛГ Марина_Вт_16:00", "opened", 1),                                # логопед — вне статистики
    (208, "2627_Заявки_Танцы", "opened", None),                                   # буфер заявок
    (209, "2627_РР.Музыка и речь_Группа 4_пн - пт_10:00_1,3 - 1,8", "opened", 7),  # открытая
    (210, "2526_АЯ_пн-ср_16:00_6-7 лет", "opened", 8),                            # прошлый сезон
    (211, "2627_ШАХ_Группа 2_пт 19:00 + вс 12:00_6-12 начинающие", "archive", 8),  # архив
    (212, "2627_МА_вс_10:30_продолжающие", "closed", 8),                          # закрыта в самой CRM
    (213, "2627_Праздник_Новый год", "opened", 30),                               # праздник
    (214, "2627_ЛГ Марина_Заявки", "opened", None),                               # заявки к логопеду
]
UCH, ZAP, BYL, PODTV, OTKAZ, OTRAB, NOVAYA, NE_PRISHEL = 2, 58132, 58131, 83760, 1, 5, 50509, 99336
# Записи в группы: (id записи, ребёнок, группа, статус)
ZAPISI = [
    # 201: трое учатся, двое пробников, остальные места не занимают → занято 3, пробники 2, остаётся 3
    (1, 1001, 201, UCH), (2, 1002, 201, UCH), (3, 1003, 201, UCH), (4, 1004, 201, ZAP), (5, 1005, 201, BYL),
    (6, 1006, 201, PODTV), (7, 1007, 201, OTKAZ), (8, 1008, 201, OTRAB), (9, 1009, 201, NOVAYA),
    (10, 1010, 201, NE_PRISHEL),
    # 202 (закрыта решением): двое учатся — в счёт мест не входят
    (11, 1011, 202, UCH), (12, 1012, 202, UCH),
    # 204 (нет занятий впереди): один учится
    (13, 1030, 204, UCH),
    # 205 мини-сад: трое учатся, один пробник
    (14, 1040, 205, UCH), (15, 1041, 205, UCH), (16, 1042, 205, ZAP), (17, 1043, 205, UCH),
    # 206 нулевой класс
    (18, 1050, 206, UCH),
    # 207 логопед, 208 заявки, 214 заявки к логопеду
    (19, 1060, 207, UCH), (20, 1061, 208, PODTV), (21, 1062, 214, PODTV),
    # 209: ребёнок 1070 с ДВУМЯ записями (пробное и «Учится») — одно место; 1003 ходит ещё и сюда;
    # записи 24 и 25 удалены в МойКлассе (пометка mk_udaleno)
    (22, 1070, 209, ZAP), (23, 1070, 209, UCH), (24, 1071, 209, UCH), (25, 1072, 209, ZAP), (26, 1003, 209, UCH),
    # прошлый сезон, архив, праздник (на празднике — «Отказался»: «Без оплаты» праздники из своих
    # списков не исключает, и это не предмет этих проверок)
    (27, 1080, 210, UCH), (28, 1081, 211, UCH), (29, 1082, 213, OTKAZ),
]
ZAPISI += [(100 + i, 1020 + i, 203, UCH) for i in range(10)]          # 203: 1020…1029 — десять из восьми
UDALENO_ZAPISI = [24, 25]
PEDAGOG = (301, "Педагог Тестовый")
# Занятия: (id, дата, группа, [(ребёнок, пробное?)])
ZANYATIYA = [
    (5001, "2026-11-13", 201, []), (5002, "2026-11-16", 202, []), (5003, "2026-11-16", 203, []),
    (5004, "2026-11-05", 204, []),                                    # только прошедшее — впереди пусто
    (5005, "2026-11-13", 209, []), (5006, "2026-11-15", 212, []), (5007, "2026-11-13", 207, []),
    # мини-сад: 1040 — пн и ср; 1041 — вт (прошлый и следующий: один ребёнок, одно место); 1042 — пробный день пт
    (5010, "2026-11-09", 205, [(1040, False)]), (5011, "2026-11-11", 205, [(1040, False)]),
    (5012, "2026-11-10", 205, [(1041, False)]), (5013, "2026-11-17", 205, [(1041, False)]),
    (5014, "2026-11-13", 205, [(1042, True)]),
    (5015, "2026-11-20", 205, [(1043, False)]),                       # за окном «неделя назад — неделя вперёд»
    # нулевой класс: 1050 ходит каждый день
    (5020, "2026-11-12", 206, [(1050, False)]), (5021, "2026-11-13", 206, [(1050, False)]),
    (5022, "2026-11-16", 206, [(1050, False)]), (5023, "2026-11-17", 206, [(1050, False)]),
    (5024, "2026-11-18", 206, [(1050, False)]),
]
# Абонементы: (id, ребёнок, группы, основная группа, начало, конец, цена, внесено, занятий, использовано, продан)
NOV = ("2026-11-01", "2026-11-30")
ABONEMENTY = [
    # 201
    (7001, 1001, [201], 201, *NOV, 8400, 8400, 8, 3, "2026-10-28"),   # ДВА абонемента ноября на одну группу —
    (7002, 1001, [201], 201, *NOV, 8400, 8400, 8, 0, "2026-11-02"),   # … один оплативший
    (7003, 1002, [201], 201, "2026-10-01", "2026-10-31", 8400, 8400, 8, 8, "2026-09-29"),   # только октябрь
    (7004, 1004, [201], 201, *NOV, 8400, 8400, 8, 0, "2026-11-10"),   # оплатил, а запись — «на пробное»
    (7005, 1007, [201], 201, "2026-09-01", "2026-09-30", 8400, 8400, 8, 8, "2026-08-30"),   # ушёл в сентябре
    # 203
    (7010, 1020, [203, 201], 203, *NOV, 8400, 8400, 8, 2, "2026-10-30"),   # абонемент на две группы, ходит в одну
    (7011, 1021, [203], 203, *NOV, 8400, 3000, 8, 2, "2026-11-01"),        # оплачен частично
    (7012, 1022, [203], 203, *NOV, 8400, 8400, 8, 0, "2026-11-01"),        # удалён в МойКлассе (пометка синка)
    (7013, 1023, [203], 203, "2026-10-10", None, 8400, 8400, 8, 3, "2026-10-10"),   # без даты конца, занятия есть
    (7014, 1024, [203], 203, "2026-10-10", None, 4200, 4200, 4, 4, "2026-10-10"),   # без даты конца, израсходован
    (7015, 1025, [203], 203, "2026-09-01", "2027-08-31", 68985, 68985, 73, 20, "2026-09-01"),   # годовой
    (7016, 1026, [203], 203, "2026-10-19", "2026-11-18", 8400, 8400, 8, 5, "2026-10-19"),   # кончается до 21-го
    (7017, 1027, [203], 203, "2026-10-26", "2026-11-25", 8400, 8400, 8, 4, "2026-10-26"),   # начат за 6 дней до 1-го
    (7018, 1028, [], None, *NOV, 8400, 8400, 8, 1, "2026-11-01"),          # без привязки к группе
    (7019, 1029, [203], 203, *NOV, 8400, 8400, 8, 0, "2026-11-01"),        # удалён (подтвердила сверка честности)
    # 209, сад, нулевой, закрытая группа, логопед
    (7020, 1070, [209], 209, *NOV, 8600, 8600, 8, 2, "2026-11-01"),
    (7021, 1003, [209], 209, *NOV, 8600, 8600, 8, 2, "2026-11-01"),        # 1003: РР оплачен, ПШ — нет
    (7022, 1040, [205], 205, *NOV, 20000, 20000, 20, 4, "2026-11-01"),
    (7023, 1050, [206], 206, *NOV, 20000, 20000, 20, 4, "2026-11-01"),
    (7024, 1011, [202], 202, *NOV, 8400, 8400, 8, 2, "2026-11-01"),
    (7025, 1060, [207], 207, *NOV, 10000, 10000, 4, 1, "2026-11-01"),
]
UDALENO_ABONEMENTY_SINK = [7012]
UDALENO_ABONEMENTY_SVERKA = [7019]


def _raw_zanyatie(lid, data, cid, zapisi):
    return {"id": lid, "date": data, "classId": cid, "teacherIds": [PEDAGOG[0]],
            "records": [{"id": lid * 10 + i, "lessonId": lid, "userId": u, "test": t, "visit": False}
                        for i, (u, t) in enumerate(zapisi)]}


def _raw_abonement(a):
    aid, uid, cids, main, b, e, price, payed, vc, vd, sell = a
    return {"id": aid, "userId": uid, "classIds": cids, "mainClassId": main, "beginDate": b, "endDate": e,
            "price": price, "payed": payed, "visitCount": vc, "visitedCount": vd, "sellDate": sell}


def zasejat(baza: str):
    c = sqlite3.connect(baza)
    c.executemany("INSERT OR REPLACE INTO classes (id, name, course_id, status, max_students, raw) VALUES (?,?,1,?,?,?)",
                  [(i, n, st, cap, json.dumps({"id": i, "name": n, "status": st, "maxStudents": cap}, ensure_ascii=False))
                   for i, n, st, cap in GRUPPY])
    c.execute("INSERT OR REPLACE INTO courses (id, name, raw) VALUES (1, 'Курс стенда', '{}')")
    c.execute("INSERT OR REPLACE INTO managers (id, name, raw) VALUES (?,?,'{}')", PEDAGOG)
    deti = sorted({u for _, u, _, _ in ZAPISI})
    c.executemany("INSERT OR REPLACE INTO users (id, name, phone, client_state_id, raw) VALUES (?,?,?,125955,?)",
                  # «Примеров», а не «Тестов»: «Без оплаты» карточки со словом «тест» в имени пропускает
                  [(u, f"Примеров Ребёнок {u}", "79990001001", json.dumps({"name": f"Примеров Ребёнок {u}"}, ensure_ascii=False))
                   for u in deti])
    c.executemany("INSERT OR REPLACE INTO joins (id, user_id, class_id, status_id, raw) VALUES (?,?,?,?,?)",
                  [(i, u, k, s, json.dumps({"id": i, "userId": u, "classId": k, "statusId": s})) for i, u, k, s in ZAPISI])
    for lid, data, cid, zap in ZANYATIYA:
        raw = _raw_zanyatie(lid, data, cid, zap)
        c.execute("INSERT OR REPLACE INTO lessons (id, date, begin_time, class_id, raw) VALUES (?,?,?,?,?)",
                  (lid, data, "10:00", cid, json.dumps(raw, ensure_ascii=False)))
        c.executemany("INSERT OR REPLACE INTO lesson_records (id, lesson_id, user_id, visit, raw) VALUES (?,?,?,0,?)",
                      [(r["id"], lid, r["userId"], json.dumps(r)) for r in raw["records"]])
    c.executemany("INSERT OR REPLACE INTO user_subscriptions (id, user_id, begin_date, end_date, visits_total, "
                  "visits_used, raw) VALUES (?,?,?,?,?,?,?)",
                  [(a[0], a[1], a[4], a[5], a[8], a[9], json.dumps(_raw_abonement(a))) for a in ABONEMENTY])
    c.execute("CREATE TABLE IF NOT EXISTS mk_udaleno (tbl TEXT, rid INTEGER, ts TEXT, istochnik TEXT, PRIMARY KEY (tbl, rid))")
    c.executemany("INSERT OR REPLACE INTO mk_udaleno VALUES (?,?,'2026-11-12T22:00:00','стенд')",
                  [("joins", i) for i in UDALENO_ZAPISI] + [("user_subscriptions", i) for i in UDALENO_ABONEMENTY_SINK])
    c.execute("INSERT OR REPLACE INTO sync_state (key, value) VALUES ('sverka_chestnosti_udaleno', ?)",
              (json.dumps({f"user_subscriptions:{i}": "2026-11-12" for i in UDALENO_ABONEMENTY_SVERKA}),))
    # журналы звонков и переписки — пустые: их читает «Без оплаты» (на сервере они есть всегда)
    c.execute("""CREATE TABLE IF NOT EXISTS crm_comments (id INTEGER PRIMARY KEY, user_id INTEGER, ts TEXT,
                 manager_id INTEGER, text TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS mango_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,
                 phone TEXT, direction TEXT, state TEXT, rec_id TEXT, secs INTEGER DEFAULT 0, ext TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS wazzup_inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
                 chat_type TEXT, text TEXT, message_id TEXT UNIQUE)""")
    c.execute("""CREATE TABLE IF NOT EXISTS wazzup_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
                 message_id TEXT, text TEXT, author_id TEXT, author_name TEXT)""")
    c.commit()
    c.close()


def vygruzka(papka: str, s_pometkami: bool = True):
    """Те же данные в виде выгрузки /export/raw — как их получает документ владельца."""
    os.makedirs(papka, exist_ok=True)

    def zapisat(imya, dannye):
        with open(os.path.join(papka, imya + ".json"), "w", encoding="utf-8") as f:
            json.dump(dannye, f, ensure_ascii=False)
    zapisat("classes", [{"id": i, "name": n, "status": st, "maxStudents": cap} for i, n, st, cap in GRUPPY])
    zapisat("joins", [{"id": i, "userId": u, "classId": k, "statusId": s} for i, u, k, s in ZAPISI])
    zapisat("lessons", [_raw_zanyatie(*z) for z in ZANYATIYA])
    zapisat("managers", [{"id": PEDAGOG[0], "name": PEDAGOG[1]}])
    if s_pometkami:
        zapisat("mk_udaleno", [{"tbl": "joins", "rid": i} for i in UDALENO_ZAPISI]
                + [{"tbl": "user_subscriptions", "rid": i} for i in UDALENO_ABONEMENTY_SINK])


# --- А. само правило -------------------------------------------------------------

def test_pravilo(tree):
    print("А. Правило счёта мест (app/mesta_pravilo.py)")
    from app import mesta_pravilo as P
    kod = open(os.path.join(tree, "app", "mesta_pravilo.py"), encoding="utf-8").read()
    proverka("правило не тянет приложение: ни базы, ни настроек (его запускает и документ вне сервера)",
             not re.search(r"^\s*(from\s+\.|from\s+app\b|import\s+app\b)", kod, re.M))
    r = P.poschitat([P.gruppa_iz_mk({"id": i, "name": n, "status": st, "maxStudents": cap}) for i, n, st, cap in GRUPPY],
                    [P.zapis_iz_mk({"id": i, "userId": u, "classId": k, "statusId": s}) for i, u, k, s in ZAPISI],
                    [P.zanyatie_iz_mk(_raw_zanyatie(*z)) for z in ZANYATIYA],
                    SEGODNYA, managers={PEDAGOG[0]: PEDAGOG[1]}, udaleno_joins=UDALENO_ZAPISI)
    g = {x["cid"]: x for x in r["groups"]}
    z = {x["cid"]: x for x in r["closed"]}
    v = {x["cid"]: x for x in r["vne"]}
    proverka("открытые группы — три обычные и две «по дням»", sorted(g) == [201, 203, 205, 206, 209], sorted(g))
    proverka("занято — «Учится», пробники — «Записался» и «Посетил»; остальные статусы места не занимают",
             (g[201]["busy"], g[201]["prob"], g[201]["left"]) == (3, 2, 3), (g[201]["busy"], g[201]["prob"], g[201]["left"]))
    proverka("«Подтвердил заявку» — справкой, не в счёте", g[201]["podtv"] == 1, g[201]["podtv"])
    proverka("закрытая решением группа — не в счёте, с причиной",
             202 in z and 202 not in g and "Гр5" in z[202]["why"] and z[202]["busy"] == 2, z.get(202))
    proverka("группа без занятий на две недели вперёд — закрыта", 204 in z and z[204]["why"] == P.NET_ZANYATIY, z.get(204))
    proverka("группа со статусом closed в МойКлассе — закрыта", 212 in z and "МойКласс" in z[212]["why"], z.get(212))
    proverka("архив, прошлый сезон, «Заявки», праздник — нигде",
             not {208, 210, 211, 213, 214} & (set(g) | set(z) | set(v)), sorted(set(g) | set(z) | set(v)))
    proverka("логопед — вне статистики, но посчитан", 207 in v and v[207]["busy"] == 1 and 207 not in g, v.get(207))
    proverka("переполненная группа: остаётся −2", g[203]["left"] == -2, g[203]["left"])
    proverka("ребёнок с двумя записями в группу — одно место; удалённые в МойКлассе записи не считаются",
             (g[209]["busy"], g[209]["prob"], g[209]["left"]) == (2, 0, 5), (g[209]["busy"], g[209]["prob"], g[209]["left"]))
    bez = P.poschitat([P.gruppa_iz_mk({"id": i, "name": n, "status": st, "maxStudents": cap}) for i, n, st, cap in GRUPPY],
                      [P.zapis_iz_mk({"id": i, "userId": u, "classId": k, "statusId": s}) for i, u, k, s in ZAPISI],
                      [P.zanyatie_iz_mk(_raw_zanyatie(*x)) for x in ZANYATIYA], SEGODNYA)
    g209 = next(x for x in bez["groups"] if x["cid"] == 209)
    proverka("без пометок удаления те же записи считаются (занято 3, пробник 1)",
             (g209["busy"], g209["prob"]) == (3, 1), (g209["busy"], g209["prob"]))
    t = r["tot"]
    proverka("итог — только открытые обычные группы: мест 23, занято 15, пробников 2",
             (t["cap"], t["busy"], t["prob"]) == (23, 15, 2), t)
    proverka("свободно 8 = 3 + 5: переполненная группа чужие места не съедает; переполнено 2; сальдо 6",
             (t["free"], t["over"], t["left"]) == (8, 2, 6), t)
    sad, nul = g[205]["days"], g[206]["days"]
    proverka("сад и нулевой — по дням и не в итоге", "days" in g[205] and "days" in g[206] and t["cap"] == 23)
    proverka("сад: потолок пн и ср 6, вт, чт, пт 8", [sad[w]["cap"] for w in P.BUDNI] == [6, 8, 6, 8, 8],
             [sad[w]["cap"] for w in P.BUDNI])
    proverka("сад: ребёнок занимает место в свои дни (пн 1, вт 1, ср 1, чт 0, пт 0)",
             [sad[w]["busy"] for w in P.BUDNI] == [1, 1, 1, 0, 0], [sad[w]["busy"] for w in P.BUDNI])
    proverka("сад: пробник — в день, на который записан вперёд (пт 1)",
             [sad[w]["prob"] for w in P.BUDNI] == [0, 0, 0, 0, 1], [sad[w]["prob"] for w in P.BUDNI])
    proverka("сад: остаётся по дням 5, 7, 5, 8, 7", [sad[w]["left"] for w in P.BUDNI] == [5, 7, 5, 8, 7],
             [sad[w]["left"] for w in P.BUDNI])
    proverka("сад: в списке «Учится» трое (один — без записей на эти две недели), пробник один",
             (g[205]["busy"], g[205]["prob"]) == (3, 1), (g[205]["busy"], g[205]["prob"]))
    proverka("нулевой класс: потолок дня — вместимость из МойКласса, каждый день занято 1, остаётся 11",
             all((nul[w]["cap"], nul[w]["busy"], nul[w]["left"]) == (12, 1, 11) for w in P.BUDNI), nul)
    proverka("подпись правила — одной строкой", P.PRAVILO == "без закрытых для записи групп; сад и нулевой — по дням")
    proverka("«по дням» одной строкой для страницы", P.svobodno_po_dnyam(sad) == "пн 5 · вт 7 · ср 5 · чт 8 · пт 7",
             P.svobodno_po_dnyam(sad))
    return r


# --- Б. документ владельца -------------------------------------------------------

def test_dokument(tree, work, pravilo_itog):
    print("Б. Документ владельца docs/rabota/mesta_boris.py считает тем же правилом")
    put = os.path.join(tree, "docs", "rabota", "mesta_boris.py")
    kod = open(put, encoding="utf-8").read()
    spec = importlib.util.spec_from_file_location("mesta_boris_test", put)
    mb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mb)
    from app import mesta_pravilo as P
    proverka("документ не держит копию правила: списка закрытых групп и потолков сада в нём нет",
             "CLOSED = [" not in kod and "MINISAD_CAP = {" not in kod and "def napr(" not in kod)
    proverka("документ берёт правило из app/mesta_pravilo.py",
             mb.pravilo is P and mb.CLOSED is P.CLOSED and mb.MINISAD_CAP is P.MINISAD_CAP)
    d1 = os.path.join(work, "vygruzka")
    vygruzka(d1)
    stranica, s = mb.build(d1, dt.datetime(2026, 11, 12, 12, 0))
    proverka("документ: итог равен правилу до единицы", s["tot"] == pravilo_itog["tot"], (s["tot"], pravilo_itog["tot"]))
    proverka("документ: сад и нулевой по дням — те же", s["days"] == pravilo_itog["days"])
    proverka("документ: открытых групп 5, закрытых 3; пометки удаления получены",
             (s["groups"], s["closed"], s["udaleno"]) == (5, 3, True), (s["groups"], s["closed"], s["udaleno"]))
    proverka("документ: на странице только числа — ни имён, ни телефонов, ни номеров карточек детей стенда",
             "Примеров" not in stranica and "7999000" not in stranica
             and not any(f">{u}<" in stranica for _, u, _, _ in ZAPISI))
    proverka("документ: закрытые группы перечислены отдельно, с причиной",
             "не набираем" in stranica and "нет занятий в расписании" in stranica)
    d2 = os.path.join(work, "vygruzka_bez_pometok")
    vygruzka(d2, s_pometkami=False)
    stranica2, s2 = mb.build(d2, dt.datetime(2026, 11, 12, 12, 0))
    proverka("старый сервер (без /export/raw/mk_udaleno.json): документ собирается и говорит об этом на странице",
             s2["udaleno"] is False and "сервер не отдал" in stranica2 and (s2["tot"]["busy"], s2["tot"]["prob"]) == (16, 3),
             s2["tot"])
    return s


# --- В. страницы -----------------------------------------------------------------

def _chisla_kartochek(h):
    return [(re.sub(r"<[^>]+>", "", a).strip(), re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", b)).strip())
            for a, b in re.findall(r'<div class="card-value"[^>]*>(.*?)</div>\s*<div class="card-label"[^>]*>(.*?)</div>', h, re.S)]


def test_stranicy(c, m, dok):
    print("В. «Места», «Подбор группы», блок пульта — те же числа")
    from app import mesta
    t = c.get("/api/mesta/tablica").json()
    it = t["итого"]
    proverka("API «Мест»: свободно, занято, пробные, мест всего, переполнено — как в документе, до единицы",
             (it["свободно"], it["занято"], it["пробные"], it["мест_всего"], it["переполнено"])
             == (dok["tot"]["free"], dok["tot"]["busy"], dok["tot"]["prob"], dok["tot"]["cap"], dok["tot"]["over"]),
             (it, dok["tot"]))
    proverka("API «Мест»: сад и нулевой по дням — как в документе",
             {g["предмет"]: {w: (d["мест"], d["занято"], d["пробные"], d["остаётся"]) for w, d in g["дни"].items()}
              for g in t["по_дням"]}
             == {k: {w: (d["cap"], d["busy"], d["prob"], d["left"]) for w, d in v.items()} for k, v in dok["days"].items()})
    proverka("API «Мест»: в таблице групп — только открытые обычные (3); закрытые (3) — отдельным списком",
             sorted(g["id"] for g in t["группы"]) == [201, 203, 209] and sorted(g["id"] for g in t["закрытые"]) == [202, 204, 212],
             ([g["id"] for g in t["группы"]], [g["id"] for g in t["закрытые"]]))
    proverka("API «Мест»: у закрытой группы есть причина", all(g["почему"] for g in t["закрытые"]))
    proverka("API «Мест»: подпись правила", t["правило"] == "без закрытых для записи групп; сад и нулевой — по дням", t.get("правило"))
    proverka("API «Мест»: в ответе нет множеств детей (только числа)", "_uch" not in json.dumps(t, ensure_ascii=False))
    h = c.get("/mesta").text
    telo = h.split("<main", 1)[-1]
    k = _chisla_kartochek(telo)
    proverka("«Места»: карточки наверху — свободно 8 в 3 группах, занято 15, пробников 2, мест 23, переполнено 2",
             [x[0] for x in k[:5]] == ["8", "15", "2", "23", "2"] and "в 3 группах" in k[0][1], k[:5])
    proverka("«Места»: правило подписано одной строкой",
             "Как считаем: без закрытых для записи групп; сад и нулевой — по дням." in re.sub(r"<[^>]+>", "", telo))
    zakr = re.search(r'<details id="zakrytye"([^>]*)>(.*?)</details>', telo, re.S)
    proverka("«Места»: закрытые группы — отдельным свёрнутым списком",
             zakr and "open" not in zakr.group(1) and "Закрытые для записи группы — 3" in zakr.group(2), zakr and zakr.group(1))
    vne_spiska = telo.replace(zakr.group(0), "") if zakr else telo
    proverka("«Места»: закрытая группа есть только в свёрнутом списке",
             zakr and "(Гр5)" in zakr.group(2) and "(Гр5)" not in vne_spiska)
    proverka("«Места»: сад и нулевой — блоком «по дням», в таблице групп их нет",
             "Мини-сад и нулевой класс — по дням" in telo
             and "Мини-сад_9:00" not in telo.split("<h2>По группам</h2>", 1)[-1].split("</table>", 1)[0])
    proverka("«Места»: ссылка «Шахматка» на месте", 'href="/grid"' in telo)
    # «Подбор группы»
    h = c.get("/enrollment").text
    k = _chisla_kartochek(h.split("<main", 1)[-1])
    proverka("«Подбор группы»: табло — 17 из 23 мест занято, свободно 8 в 3 группах (как на «Местах»)",
             k[0][0] == "17 / 23" and k[1][0] == "8" and "в 3 группах" in k[1][1], k[:2])
    proverka("«Подбор группы»: правило подписано", "Как считаем места: без закрытых для записи групп; сад и нулевой — по дням."
             in re.sub(r"<[^>]+>", "", h))
    eg = {g["id"]: g for g in m._enrollment_groups()}
    proverka("«Подбор группы»: отработка, заявки и «не пришёл» места не занимают — в Гр9 записано 5, свободно 3 "
             "(прежний счёт дал бы 9 записанных и «мест нет»)",
             (eg[201]["enrolled"], eg[201]["free"]) == (5, 3), (eg[201]["enrolled"], eg[201]["free"]))
    proverka("«Подбор группы»: закрытая для записи группа свободных мест не даёт", eg[202]["free"] == 0 and eg[204]["free"] == 0,
             (eg[202]["free"], eg[204]["free"]))
    proverka("«Подбор группы»: сад — мест в самый свободный день (8), нулевой — 11",
             (eg[205]["free"], eg[206]["free"]) == (8, 11), (eg[205]["free"], eg[206]["free"]))
    proverka("«Подбор группы»: буфер «Заявки» — как раньше (без лимита, записано 1)",
             eg[208]["buffer"] and eg[208]["free"] == 0 and eg[208]["enrolled"] == 1, eg[208])
    proverka("«Подбор группы»: в строках — «закрыта для записи» (3 группы) и раскладка сада по дням",
             h.count("закрыта для записи:") == 3 and "мест по дням: пн 5 · вт 7 · ср 5 · чт 8 · пт 7" in h,
             h.count("закрыта для записи:"))
    stroka = re.search(r"<tr>\s*<td><b>2627_ПШ_пн-чт_16:00_5-7 лет_ПШ1 нечитающие \(Гр5\)</b>.*?</tr>", h, re.S)
    proverka("«Подбор группы»: у закрытой группы нет формы листа ожидания", stroka and "/waitlist/add" not in stroka.group(0))
    proverka("«Подбор группы»: карточек «0 дней до 30 сентября» больше нет", "дней до 30 сентября" not in h)
    # блок пульта и плана дня
    b = mesta.block()
    proverka("блок мест (пульт, план дня): заголовок — то же число, свободно 8 в 3 группах",
             "Места сейчас: свободно 8 в 3 группах" in b and "занято 15" in b and "пробников 2" in b, b[:400])
    proverka("блок мест: закрытая группа помечена, сад — по дням",
             b.count("закрыта для записи") == 3 and "по дням: пн 5 · вт 7 · ср 5 · чт 8 · пт 7" in b)
    proverka("блок мест: правило подписано", "без закрытых для записи групп; сад и нулевой — по дням" in b)
    # выгрузка пометок для документа
    r = c.get("/export/raw/mk_udaleno.json")
    proverka("/export/raw/mk_udaleno.json отдаёт пометки удалённого: только таблица и id",
             r.status_code == 200 and {"tbl": "joins", "rid": 24} in r.json()
             and all(set(x) == {"tbl", "rid"} for x in r.json()), r.status_code)
    proverka("/export/raw: чужую таблицу по-прежнему не отдаёт", c.get("/export/raw/settings.json").status_code == 404)
    # прежний счёт rows() (подсказки «Линии», рутины) — на месте
    rs = {x["id"]: x for x in c.get("/api/mesta").json()}
    proverka("/api/mesta (подсказки «Линии», рутины): счёт мест прежний — живых 6 из 8, свободно 2",
             (rs[201]["live"], rs[201]["cap"], rs[201]["free"]) == (6, 8, 2), rs[201])
    return t


# --- Г. «оплатили» ---------------------------------------------------------------

def test_oplatili(c, m, t):
    print("Г. «Оплатили» — оплаченный абонемент на текущий месяц")
    from app import db, mesta
    with db.get_conn() as conn:
        opl = mesta.oplatili_mesyac(conn, SEGODNYA)
        staroe = mesta._paid_by_class(conn)
    g = {x["id"]: x for x in t["группы"]}
    proverka("месяц — ноябрь", opl["mesyac"] == "ноябрь" and t["месяц"] == "ноябрь", opl["mesyac"])
    proverka("ребёнок с двумя абонементами на группу — один оплативший",
             g[201]["оплатили"] == 1 and opl["oplatili"][201] >= {1001} and sum(1 for a in opl["abonementy"] if a["uid"] == 1001) == 2,
             g[201]["оплатили"])
    proverka("абонемент прошлого месяца — не оплата этого; ушедший в сентябре не считается "
             "(прежний счёт «платил хоть раз с июня» давал бы в группе 5 оплативших)",
             g[201]["ходят_без_оплаты"] == 2 and 1002 not in opl["oplatili"].get(201, set())
             and 1007 not in opl["oplatili"].get(201, set()) and len(staroe[201]) == 5, (g[201], len(staroe[201])))
    proverka("оплатил, но запись не «Учится» — в «оплатили» группы не входит", 1004 in opl["oplatili"][201] and g[201]["оплатили"] == 1)
    proverka("абонемент на две группы считается там, где ребёнок ходит", 1020 in opl["oplatili"][201] and g[201]["оплатили"] == 1
             and g[203]["оплатили"] == 5, (g[201]["оплатили"], g[203]["оплатили"]))
    proverka("частичная оплата — «частично», не «оплатили»", g[203]["частично"] == 1 and 1021 not in opl["oplatili"].get(203, set()),
             g[203]["частично"])
    proverka("удалённый в МойКлассе абонемент — не оплата (пометка синка и сверка честности)",
             not {1022, 1029} & opl["oplatili"].get(203, set()))
    proverka("абонемент без даты конца действует, пока есть занятия; израсходованный — нет",
             1023 in opl["oplatili"][203] and 1024 not in opl["oplatili"][203])
    proverka("годовой абонемент — оплата месяца; кончающийся до 21-го и начатый раньше чем за неделю до 1-го — нет",
             1025 in opl["oplatili"][203] and 1026 not in opl["oplatili"][203])
    proverka("абонемент, начатый за неделю до 1-го, — оплата месяца", 1027 in opl["oplatili"][203])
    proverka("абонемент без привязки к группе — оплата любой группы ребёнка", 1028 in opl["lyubaya"] and g[203]["оплатили"] == 5)
    proverka("в каждой строке: занято = оплатили + не оплатили",
             all(x["занято"] == x["оплатили"] + x["ходят_без_оплаты"] for x in t["группы"] + t["по_дням"] + t["закрытые"]))
    it = t["итого"]
    proverka("итог: оплатили 8, не оплатили 7, занято 15", (it["оплатили"], it["ходят_без_оплаты"], it["занято"]) == (8, 7, 15), it)
    proverka("дети: ходят 21 = оплатили по всем предметам 10 + не оплачен хотя бы один 11",
             (it["детей_ходят"], it["детей_оплатили"], it["детей_без_оплаты"]) == (21, 10, 11), it)
    proverka("ребёнок на двух предметах, один не оплачен, — среди «не оплачен хотя бы один», ребёнок один",
             g[209]["оплатили"] == 2 and g[201]["ходят_без_оплаты"] == 2)
    proverka("логопед: ходит 1, оплатил месяц 1; заявок к логопеду 1",
             (it["логопед_ходят"], it["детей_вне_таблицы"], it["логопед_заявки"]) == (1, 1, 1), it)
    rs = {x["id"]: x for x in c.get("/api/mesta").json()}
    proverka("/api/mesta: «оплатили» — тоже по месяцу (1, а не 5)", rs[201]["paid"] == 1, rs[201]["paid"])
    r = c.get("/api/mesta/raskhozhdenie").json()
    proverka("«оплатил месяц, а в группе не Учится» — одна строка (запись «на пробное»), ушедших нет",
             r["всего"] == 1 and r["строки"][0]["uid"] == 1004, r["всего"])
    h = re.sub(r"<[^>]+>", " ", c.get("/mesta").text.split("<main", 1)[-1])
    proverka("«Места»: подписи «оплатили ноябрь» и «не оплатили ноябрь»", "оплатили ноябрь" in h and "не оплатили ноябрь" in h)
    proverka("«Места»: «цель 311» названа сезонной и накопленной — без «осталось до цели»",
             "Цель «311» была сезонной" in h and "до цели" not in h and "счёт к цели" not in h.lower())


# --- Д. «Без оплаты» -------------------------------------------------------------

def test_voronka(c, m):
    print("Д. «Без оплаты» (/voronka): оплата месяца и честная подпись к цели")
    v = c.get("/api/mesta/voronka").json()
    uch = sorted(r["uid"] for r in v["списки"]["учится без оплаты"])
    zak = {r["uid"]: r for r in v["списки"]["абонемент закончился"]}
    proverka("«учится без оплаты» — кто ходит и за эту группу не платил ни разу (включая закрытые группы и сад)",
             uch == [1003, 1012, 1030, 1041, 1043], uch)
    proverka("«абонемент закончился» — платил раньше, на месяц оплаченного абонемента нет",
             sorted(zak) == [1002, 1021, 1022, 1024, 1026, 1029], sorted(zak))
    proverka("оплатившие месяц в списки не попадают", not {1001, 1020, 1023, 1025, 1027, 1028, 1070, 1040, 1050, 1011}
             & (set(uch) | set(zak)))
    proverka("дата конца — по абонементу этого предмета", zak[1002]["конец"] == "2026-10-31" and zak[1026]["конец"] == "2026-11-18",
             (zak[1002].get("конец"), zak[1026].get("конец")))
    proverka("у ребёнка, оплатившего другой предмет, пометка «платил за другой предмет»",
             next(r for r in v["списки"]["учится без оплаты"] if r["uid"] == 1003).get("платил_другое") is True)
    proverka("удалённая в МойКлассе запись строки не даёт", 1071 not in uch and 1071 not in zak)
    s = v["сейчас"]
    t = c.get("/api/mesta/tablica").json()["итого"]
    proverka("карточки «сейчас» — те же числа, что на «Местах»",
             (s["детей_ходят"], s["детей_оплатили"], s["детей_без_оплаты"]) == (t["детей_ходят"], t["детей_оплатили"], t["детей_без_оплаты"]),
             s)
    proverka("накопленный счёт сезона отдаётся под своим именем", v["оплачено_в_группах_сезона"] >= 1)
    h = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c.get("/voronka").text.split("<main", 1)[-1]))
    proverka("страница: «оплачено · цель 311» больше нет — накопленный счёт назван накопленным",
             "оплачено · цель 311" not in h and "платили хотя бы раз с июня, считая ушедших · цель сезона была 311 к 30.09" in h)
    proverka("страница: «детей оплатили ноябрь — из 21, кто ходит в группы»",
             "детей оплатили ноябрь — из 21, кто ходит в группы" in h)
    proverka("страница: действующий абонемент не назван закончившимся («оплачено до 18.11»), прошлый — «закончился 31.10»",
             "оплачено до 18.11" in h and "закончился 31.10" in h)


# --- Е. После проверки «вечера 1» (13.11.2026, вечер) ------------------------------

def test_posle_proverki(c, m):
    """Замечания проверяющих «вечера 1» по числам: заголовок блока пульта, сводка «Подбора
    группы», «Линия» и предложения возврата по единому правилу, разница «Мест» и «Без оплаты»."""
    from app import db, liniya, mesta, mesta_pravilo, vozvrat
    it = c.get("/api/mesta/tablica").json()["итого"]

    print("Е1. Блок мест на пульте: «занято» и «оплатили» — по одним и тем же группам")
    b = mesta.block()
    zag = re.sub(r"<[^>]+>", "", b.split("<summary", 1)[1].split("</summary>", 1)[0])
    proverka("заголовок: «занято 15, из них оплатили ноябрь 8» — оба числа по открытым группам, как на «Местах»",
             f"занято {it['занято']}, из них оплатили ноябрь {it['оплатили']}" in zag and (it["занято"], it["оплатили"]) == (15, 8), zag)
    proverka("оплатившие сада, нулевого и закрытых групп (3) в заголовок не входят — названы отдельной строкой",
             "оплатили ноябрь 11" not in zag and "Ещё 3 оплативших — в строках мини-сада, нулевого класса и закрытых групп" in b)
    proverka("в заголовке нет числа «оплатили», которое больше «занято» по другой базе групп",
             int(re.search(r"оплатили ноябрь (\d+)", zag).group(1)) <= it["занято"])

    print("Е2. Блок мест: у группы с пометкой видно и число мест, заголовок считает все пометки")
    bylo = dict(mesta.WAITLIST), dict(mesta.HOLD)
    mesta.WAITLIST["ПШ_вт-пт_17:00"] = "стендовая пометка «полная»"          # группа 201: по счёту 3 места
    mesta.HOLD["РР.Музыка и речь_Группа 4"] = "стендовая пометка «пауза»"       # группа 209: по счёту 5 мест
    try:
        b2 = mesta.block()
    finally:
        mesta.WAITLIST.clear(); mesta.WAITLIST.update(bylo[0])
        mesta.HOLD.clear(); mesta.HOLD.update(bylo[1])
    stroka201 = re.sub(r"<[^>]+>", "", next(x for x in b2.split("<tr>") if "ПШ_вт-пт_17:00" in x))
    stroka209 = re.sub(r"<[^>]+>", "", next(x for x in b2.split("<tr>") if "Музыка и речь_Группа 4" in x))
    proverka("«полная»: перед пометкой — «3 места по счёту» (раньше стояла одна пометка)",
             "3 места по счёту · полная стендовая пометка" in stroka201, stroka201)
    proverka("«пауза»: перед пометкой — «5 мест по счёту»", "5 мест по счёту · ⛔ пауза стендовая пометка" in stroka209, stroka209)
    proverka("заголовок: «из них 8 в группах с пометкой «не записывать», «полная» или «пауза»» — сумма по всем пометкам",
             "свободно 8 в 3 группах · из них 8 в группах с пометкой «не записывать», «полная» или «пауза»" in re.sub(r"<[^>]+>", "", b2), b2[:300])
    proverka("без пометок приписки «из них …» нет", "из них 8 в группах" not in b and "по счёту" not in b.split("<table>", 1)[-1])

    print("Е3. «Подбор группы», сводка по предметам: сад и нулевой — «по дням», а не «0 мест / 0 свободно / 0 %»")
    h = c.get("/enrollment").text
    svodka = h.split("<h2>Сводка по предметам</h2>", 1)[1].split("</div>", 1)[0]
    yacheyki = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", x)).strip()
                for x in re.findall(r"<td[^>]*>(.*?)</td>", svodka.split("</tr>", 2)[1], re.S)]
    # На стенде все группы — одного курса, поэтому в строке вместе: три открытые группы (23 места,
    # записано 17), логопед (1 место, 1), архивная группа и праздник (8 и 30 мест по прежнему счёту,
    # записан 1) — всего 62 места и 19 записанных; сад и нулевой — «по дням»; закрытые и «Заявки» — мимо.
    proverka("строка курса: «всего мест» — «62 + по дням», «свободно» — «45 + до 11 в день» (а не 0 и 0 у сада)",
             yacheyki[3] == "62 + по дням" and yacheyki[4] == "45 + до 11 в день", yacheyki[:6])
    proverka("заполненность — по тем же группам, что места: 19 из 62 = 31 % (прежняя формула делила всех 29 записанных "
             "курса, с садом, закрытыми и «Заявками», на те же 62 места — 47 %)",
             yacheyki[2] == "29" and yacheyki[5] == "31%", yacheyki[:6])
    proverka("под таблицей сказано, что считается по дням и что в места не входит",
             "«до N в день» — мест в самый свободный день" in svodka and "без закрытых для записи групп и буферов «Заявки»" in svodka)
    eg = {g["id"]: g for g in m._enrollment_groups()}
    proverka("у сада и нулевого в строке группы свободно 8 и 11 в день — сводка с ними больше не спорит",
             (eg[205]["free"], eg[206]["free"]) == (8, 11), (eg[205]["free"], eg[206]["free"]))

    print("Е4. «Линия»: закрытые для записи группы не предлагаются, места — по правилу, сад и нулевой — по дням")
    vse = {g["id"]: g for g in liniya.vse_gruppy()}
    proverka("закрытая решением владельца группа (ПШ Гр5) помечена и в предложения не попадает",
             "Гр5" in vse[202]["zakryta"] and not [g for sp in liniya.podobrat(6, list(vse.values())) for g in sp if g["id"] == 202],
             vse[202]["zakryta"])
    proverka("группа без занятий на две недели вперёд — тоже закрыта (такая одна — это не сбой расписания)",
             vse[204]["zakryta"] == mesta_pravilo.NET_ZANYATIY
             and not [g for sp in liniya.podobrat(4, list(vse.values())) for g in sp if g["id"] == 204], vse[204]["zakryta"])
    proverka("свободные места — как на «Местах»: в Гр9 3 (прежний счёт давал 2: «Подтвердил заявку» держал место), в МиР 5 (было 2)",
             (vse[201]["free"], vse[201]["free_text"], vse[209]["free"]) == (3, "3 места", 5)
             and not vse[201]["zakryta"], (vse[201]["free"], vse[209]["free"]))
    proverka("мини-сад и нулевой класс — «по дням» с раскладкой, как на «Местах» (нулевой раньше был «N мест» при норме 10)",
             vse[205]["mesto"] == "po_dnyam" and vse[205]["free_text"] == "по дням: пн 5 · вт 7 · ср 5 · чт 8 · пт 7"
             and vse[206]["mesto"] == "po_dnyam" and vse[206]["free_text"].startswith("по дням: ") and vse[206]["free"] == 11,
             (vse[205]["free_text"], vse[206]["free_text"], vse[206]["free"]))
    top, eshche = liniya.podobrat(6, list(vse.values()))
    proverka("для шести лет открытая Гр9 предлагается, закрытая Гр5 — нет",
             any(g["id"] == 201 for g in top + eshche) and all(g["id"] != 202 for g in top + eshche), [g["id"] for g in top + eshche])
    nast = mesta.svod
    zakr = [{"cid": 900 + i, "why": mesta_pravilo.NET_ZANYATIY, "cap": 8, "busy": 0, "prob": 0} for i in range(3)]
    mesta.svod = lambda today=None, conn=None: {"groups": [], "closed": zakr + [{"cid": 950, "why": "ПШ Гр5 — не набираем", "cap": 8, "busy": 1, "prob": 0}]}
    try:
        dl = mesta.dlya_linii()
    finally:
        mesta.svod = nast
    proverka("без занятий сразу три группы и больше (каникулы, сбой расписания) — «Линия» их не прячет; закрытые решением — прячет",
             sorted(dl) == [950] and dl[950]["zakryta"] == "ПШ Гр5 — не набираем" and mesta.BEZ_ZANYATIY_SBOJ == 3, sorted(dl))
    mesta.svod = lambda today=None, conn=None: (_ for _ in ()).throw(RuntimeError("стенд: правило не посчиталось"))
    try:
        zapas = {g["id"]: g for g in liniya.vse_gruppy()}
    finally:
        mesta.svod = nast
    proverka("правило не посчиталось — «Линия» работает на прежнем счёте, а не падает",
             zapas[201]["free"] == 2 and zapas[202]["zakryta"] == "", (zapas[201]["free"], zapas[202]["zakryta"]))

    print("Е5. Предложения возврата (vozvrat.gruppy): закрытые решением владельца группы не предлагаются")
    with db.get_conn() as conn:
        conn.executemany("INSERT INTO classes (id, name, course_id, status, max_students, raw) VALUES (?,?,1,'opened',8,'{}')",
                         [(290, "2627_ПШ_ср-пт_19:00_5-7 лет_ПШ1 нечитающие (Гр15)"),      # в списке закрытых
                          (291, "2627_ПШ_ср-пт_18:00_5-7 лет_ПШ1 нечитающие (Гр16)")])     # обычная, для сравнения
    try:
        imena = [g["name"] for g in vozvrat.gruppy()]
    finally:
        with db.get_conn() as conn:
            conn.execute("DELETE FROM classes WHERE id IN (290, 291)")
    proverka("ПШ Гр15 («не набираем») в списке «куда звать» нет, такая же открытая Гр16 — есть; остальное — как было",
             not any("(Гр15)" in n for n in imena) and any("(Гр16)" in n for n in imena) and any("(Гр9)" in n for n in imena), imena)
    proverka("список только сужается: правило применяется по списку решений владельца, без «нет занятий»",
             "mesta_pravilo.zakryta(" in __import__("inspect").getsource(vozvrat.gruppy)
             and "NET_ZANYATIY" not in __import__("inspect").getsource(vozvrat.gruppy))

    print("Е6. «Без оплаты»: разница с «Местами» названа — оплачен тот же предмет в другой группе")
    with db.get_conn() as conn:
        conn.execute("INSERT INTO users (id, name, phone, client_state_id, raw) VALUES (1090, 'Примеров Ребёнок 1090', '79990001001', 125955, '{}')")
        conn.execute("INSERT INTO joins (id, user_id, class_id, status_id, raw) VALUES (990, 1090, 201, 2, '{}')")
        conn.execute("INSERT INTO user_subscriptions (id, user_id, begin_date, end_date, visits_total, visits_used, raw) "
                     "VALUES (7990, 1090, '2026-11-01', '2026-11-30', 8, 1, ?)",
                     (json.dumps(_raw_abonement((7990, 1090, [202], 202, "2026-11-01", "2026-11-30", 8400, 8400, 8, 1, "2026-11-01"))),))
    try:
        v = c.get("/api/mesta/voronka").json()
        t2 = c.get("/api/mesta/tablica").json()
        hv = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c.get("/voronka").text.split("<main", 1)[-1]))
    finally:
        with db.get_conn() as conn:
            conn.execute("DELETE FROM user_subscriptions WHERE id=7990")
            conn.execute("DELETE FROM joins WHERE id=990")
            conn.execute("DELETE FROM users WHERE id=1090")
    v_spiskah = {r["uid"] for sp in v["списки"].values() for r in sp}
    proverka("ребёнок ходит в Гр9, ноябрь оплачен абонементом Гр5 того же предмета: на «Местах» он в «не оплатили», в списках «Без оплаты» его нет",
             t2["итого"]["детей_без_оплаты"] == it["детей_без_оплаты"] + 1 and 1090 not in v_spiskah
             and [r["uid"] for r in v["оплачен_в_другой_группе"]] == [1090], v["оплачен_в_другой_группе"])
    proverka("страница называет разницу: «входят ещё 1 ребёнок … абонемент привязан к другой группе», с именем и группой",
             "входят ещё 1 ребёнок" in hv and "которых в списках ниже нет" in hv
             and "абонемент привязан к другой группе" in hv and "Примеров Ребёнок 1090" in hv
             and "ПШ_вт-пт_17:00" in hv.split("абонемент привязан к другой группе", 1)[-1][:400], hv[:200])
    proverka("подпись карточки больше не обещает «как на „Местах“» — сказано, что число с «Мест» и что оно значит",
             "не оплачен — как на «Местах»" not in hv and "число с «Мест»: нет оплаченного абонемента на свою группу" in hv)
    proverka("без таких детей строки на странице нет",
             "абонемент привязан к другой группе" not in re.sub(r"<[^>]+>", " ", c.get("/voronka").text.split("<main", 1)[-1])
             and c.get("/api/mesta/voronka").json()["оплачен_в_другой_группе"] == [])

    print("Е7. «Места»: правило оплаты не выдаётся за правило «Бланков групп»")
    hm = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c.get("/mesta").text.split("<main", 1)[-1]))
    proverka("слов «правило то же» нет; отличия от бланков названы: абонемент без даты конца, второй неоплаченный, смена месяца с 25-го",
             "правило то же" not in hm and "Чем отличается от бланков" in hm and "без даты окончания" in hm
             and "с 25-го числа бланки показывают оплату следующего месяца" in hm)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=ROOT)
    ap.add_argument("--work", default="")
    a = ap.parse_args()
    tree = os.path.abspath(a.tree)
    work = a.work or tempfile.mkdtemp(prefix="mesta_")
    os.makedirs(work, exist_ok=True)
    spec = importlib.util.spec_from_file_location("liniya_testy", os.path.join(tree, "docs", "rabota", "liniya_testy.py"))
    lt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lt)
    m = lt.zapustit(tree, work)
    zasejat(os.path.join(work, "kidsup.db"))
    from app import mesta
    mesta._segodnya = lambda: SEGODNYA           # счёт мест и оплат — на 12.11.2026
    from fastapi.testclient import TestClient
    c = TestClient(m.app)
    itog = test_pravilo(tree)
    dok = test_dokument(tree, work, itog)
    t = test_stranicy(c, m, dok)
    test_oplatili(c, m, t)
    test_voronka(c, m)
    test_posle_proverki(c, m)
    proverka("в сеть не ходили", not lt.SET, lt.SET[:3])
    proverka("ни одной отправки", not lt.LOVUSHKI, lt.LOVUSHKI)
    print(f"\nИтого: {proshlo} OK, {oshibok} ошибок · стенд {work}")
    sys.exit(1 if oshibok else 0)


if __name__ == "__main__":
    main()
