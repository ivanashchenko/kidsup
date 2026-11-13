# -*- coding: utf-8 -*-
"""Страница «Проверка МойКласса» (app/proverka_mk.py) и средний синк (app/sync.py) —
без сервера, без сети, на временной базе.

12.11.2026, Борис — правило (2): правки Лизы в МойКлассе перестают приходить в пульт
пунктами; вместо них страница, которая сама собирается из МойКласса. Здесь зафиксированы:
каждое правило раздела на маленьком примере, ложные случаи из проверки скептика
(отработка — не лишняя запись, удалённый абонемент — не строка, пустые «проведено» —
одна сводная строка), средний синк (полный список → помечает пропавшее; неполный, ошибка
или пропало больше 10 % → ничего не трогает), «не ошибка» без причины → 400, строка в
пульте у Лизы и её отсутствие при N = 0, настройка proverka_mk_vmesto_punktov.

Имена и телефоны выдуманные (7999000xxxx).

  python3 docs/rabota/proverka_mk_testy.py
"""
import json
import os
import socket
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "/home/user/kidsup")

PAPKA = Path(tempfile.mkdtemp(prefix="proverka_mk_"))
os.environ["KIDSUP_DATA_DIR"] = str(PAPKA)
# 12.11.2026, личные входы: без APP_PASSWORD сайт открыт только в режиме без входа
os.environ["KIDSUP_BEZ_VHODA"] = "1"
for _k in ("APP_PASSWORD", "MOYKLASS_API_KEY", "KIDSUP_ADMIN_PASS"):
    os.environ.pop(_k, None)


def _zakryt_set():
    """Сеть закрыта: любая попытка выйти наружу — ошибка, а не запрос."""
    def connect(self, addr):
        if self.family == getattr(socket, "AF_UNIX", -1):
            return _orig(self, addr)
        raise OSError("сеть закрыта (proverka_mk_testy)")
    _orig = socket.socket.connect
    socket.socket.connect = connect
    socket.getaddrinfo = lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("сеть закрыта"))


_zakryt_set()

from app import config, db  # noqa: E402

db.init_db()
from app import autopilot as ap  # noqa: E402

CHASY = datetime(2026, 11, 12, 11, 31, tzinfo=ZoneInfo("Europe/Moscow"))   # чт 12.11
ap._now = lambda: CHASY
from app import proverka_mk as pm, sync, kontrol_oplat, sverka_chestnosti, pult  # noqa: E402

KONTROL = PAPKA / "kontrol_oplat"
SVERKA = PAPKA / "sverka_chestnosti"
KONTROL.mkdir()
SVERKA.mkdir()
kontrol_oplat.OUT_DIR = KONTROL
sverka_chestnosti.OUT_DIR = SVERKA

oshibok = 0


def proverka(nazvanie, poluchili, zhdali=True):
    global oshibok
    ok = poluchili == zhdali
    oshibok += not ok
    print(("  OK   " if ok else "  ОШИБКА "), nazvanie, "" if ok else f"-> {poluchili!r}, ждали {zhdali!r}")


# ── выдуманные данные ──────────────────────────────────────────────────────

GR = {
    101: ("2627_ПШ_пн-чт_17:00_5-7 лет_ПШ1 нечитающие (Гр1)", "opened"),
    102: ("2627_ПШ_вт-пт_17:00_5-7 лет_ПШ1 нечитающие (Гр2)", "opened"),
    103: ("2627_ИЗО_Группа 1_пн - ср_18:00_5-7 лет", "opened"),
    104: ("2627_ШАХ_Группа 9_вс 12:00_6-12", "opened"),           # учеников нет
    106: ("2627_ЛГ Тестовая_Вт_16:00", "opened"),                  # логопед: учится 1, слоты пустые
    107: ("2627_АЯ_вт-чт_16:00_7-8 лет (Гр3)", "opened"),          # время в названии ≠ занятия
    108: ("2627_ПШ_ср-пт_19:00_5-7 лет_ПШ1 нечитающие (Гр4)", "opened"),
    111: ("2627_Заявки_Танцы", "opened"),
    112: ("2526_ПШ_архив", "archive"),
}


def tel(n):
    return f"7999000{n:04d}"


LYUDI = {
    # id: (имя, телефон, статус, создана, баланс, день рождения)
    9001: ("Тестова Ева", tel(1), 125955, "2026-08-20", 0, "2020-01-01"),
    9002: ("Тестов Марк", tel(2), 125955, "2026-08-20", 0, "2020-02-02"),
    9003: ("Пробная Алиса", tel(3), 125955, "2026-08-20", 0, "2020-03-03"),
    9004: ("Образцова Мила", tel(4), 125955, "2026-08-20", 8000, "2020-04-04"),     # 6: деньги без абонемента
    9005: ("Остаткова Нина", tel(5), 125955, "2026-08-20", 2100, "2020-05-05"),     # 6: мелкий остаток — не строка
    9006: ("Дублева Вера ДУБЛЬ", tel(6), 125951, "2026-10-20", 0, None),            # 11а
    9007: ("Удалёнов Ян ДУБЛЬ", tel(7), 125951, "2026-10-20", 0, None),             # 11а, но карточка удалена
    9008: ("79990000008", tel(8), 146950, "2026-10-25", 0, None),                   # 11б
    9009: ("Кира", tel(9), 125951, "2026-10-20", 0, None),                          # 11в: одно слово
    9010: ("Старая Кира", tel(9), 125957, "2025-05-01", 0, "2019-01-01"),           # 11в: старая в отказе
    9011: ("Братов Пётр", tel(11), 125951, "2026-10-21", 0, "2020-06-06"),          # брат — не 11в
    9012: ("Братов Иван", tel(11), 125957, "2025-04-01", 0, "2018-06-06"),
    9013: ("Отказова Ия", tel(13), 125957, "2026-09-01", 0, "2020-07-07"),          # 11г
    9014: ("Новикова Ада", tel(14), 125952, "2026-11-05", 0, None),                 # 11д: после разговора
    9015: ("Лидова Ана", tel(15), 125951, "2026-11-05", 0, None),                   # новый лид — не 11д
    9016: ("Минусов Лев", tel(16), 125955, "2026-08-20", -1600, "2020-08-08"),      # 7д
    9017: ("Двойнова Ира", tel(17), 125955, "2026-08-20", 0, "2020-09-09"),         # 8а
    9018: ("Отработкин Ян", tel(18), 125955, "2026-08-20", 0, "2020-10-10"),        # отработка — не 8а
    9019: ("Выбывшев Гоша", tel(19), 125955, "2026-08-20", 0, "2020-11-11"),        # 8б
    9020: ("Учащийся Тим", tel(20), 125955, "2026-08-20", 0, "2020-12-12"),         # 8в
    9021: ("Пробнов Олег", tel(21), 125952, "2026-08-20", 0, "2021-01-01"),         # 8г
    9022: ("Бесплатнова Зоя", tel(22), 125955, "2026-08-20", 0, "2021-02-02"),      # 4б
    9023: ("Парова Зоя", tel(23), 125955, "2026-08-20", 0, "2021-03-03"),           # 4б с пропуском рядом
    9024: ("Логопедов Ян", tel(24), 125955, "2026-08-20", 0, "2021-04-04"),         # учится у логопеда
    9025: ("Возвратова Ия", tel(25), 125955, "2026-08-20", 0, "2021-05-05"),        # 7г
    9026: ("79990000026", "", 125951, "2026-10-28", 0, None),                        # 11б: номер в имени,
    9027: ("+79990000026", tel(26), 125951, "2026-10-28", 0, None),                  # вторая — тот же номер
    9028: ("Летнева Ада", tel(28), 125955, "2026-03-01", 0, "2019-05-05"),          # 7г: летний, возврат ровно
}

N_URok = [0]


def zasejat():
    c = sqlite3.connect(config.DB_PATH)
    c.executemany("INSERT INTO classes (id, name, status, raw) VALUES (?,?,?,'{}')",
                  [(i, n, s) for i, (n, s) in GR.items()])
    for uid, (n, p, st, cr, bal, bd) in LYUDI.items():
        attrs = [{"attributeAlias": "birthday", "value": bd}] if bd else []
        c.execute("INSERT INTO users (id, name, phone, balance, client_state_id, created_at, raw) VALUES (?,?,?,?,?,?,?)",
                  (uid, n, p, bal, st, cr, json.dumps({"name": n, "attributes": attrs}, ensure_ascii=False)))
    c.commit()
    c.close()


def urok(lid, cid, d, t="17:00", konec="17:50", status=1, zapisi=()):
    """Занятие и записи на него. zapisi: [(id, uid, {поля записи})]."""
    raw = {"id": lid, "date": d, "beginTime": t, "endTime": konec, "classId": cid, "status": status,
           "teacherIds": [1], "records": [dict({"id": rid, "userId": u, "lessonId": lid}, **f) for rid, u, f in zapisi]}
    db.save_lessons([raw])


def abonement(sid, uid, cid, price, payed, st=2, nachalo="2026-11-01", konec="2026-11-30", prodan="2026-11-01",
              poseshch=0, zanyatiy=8, **dop):
    db.save_user_subscriptions([dict({"id": sid, "userId": uid, "mainClassId": cid, "classIds": [cid], "price": price,
                                      "payed": payed, "statusId": st, "beginDate": nachalo, "endDate": konec,
                                      "sellDate": prodan, "visitedCount": poseshch, "visitCount": zanyatiy}, **dop)])


def zapis_v_gruppu(jid, uid, cid, st):
    db.save_joins([{"id": jid, "userId": uid, "classId": cid, "statusId": st}])


def platezh(pid, uid, d, summa, optype="income", tip=64725, **dop):
    db.save_payments([dict({"id": pid, "userId": uid, "date": d, "summa": summa, "optype": optype,
                            "paymentTypeId": tip}, **dop)])


def stroki():
    r = pm.sobrat(CHASY, zapisat=True)
    return {x["key"]: x for rz in r["razdely"] for x in rz["stroki"]}, r


# ── 1. Правила разделов ────────────────────────────────────────────────────

def test_razdely():
    print("Правила разделов на маленьких примерах")
    zasejat()
    # правила всех разделов проверяем при включённых 5 и 11г (по умолчанию они выключены —
    # это отдельная проверка в test_skeptik2)
    db.set_setting("proverka_mk_vyklyucheny", "-")
    # записи в группы
    for jid, uid, cid, st in [(1, 9001, 101, 2), (2, 9002, 102, 2), (3, 9003, 103, 2), (4, 9017, 101, 2),
                              (5, 9017, 102, 2), (6, 9018, 101, 2), (7, 9019, 108, 4), (8, 9020, 108, 2),
                              (9, 9021, 107, 58132), (10, 9024, 106, 2), (11, 9013, 111, 50509),
                              (12, 9022, 103, 2), (13, 9023, 103, 2)]:
        zapis_v_gruppu(jid, uid, cid, st)
    # Р1: прошло, не закрыто, записи есть; без записей — не строка
    urok(1, 101, "2026-11-05", status=0, zapisi=[(101, 9001, {})])
    urok(2, 101, "2026-11-06", status=0)
    # Р2а: «проведено», двое записаны, оба не пришли; индивидуальное с оплаченным пропуском — норма
    urok(3, 101, "2026-11-03", zapisi=[(103, 9001, {}), (104, 9017, {})])
    urok(4, 103, "2026-11-03", t="18:00", konec="18:50", zapisi=[(105, 9003, {"bill": {"summa": 900}})])
    # Р2в: «проведено» завтрашнее
    urok(5, 102, "2026-11-13", status=1, zapisi=[(106, 9002, {})])
    # Р2б: пустые «проведено» в группе с учеником (логопед) — две штуки → одна сводная строка;
    # в группе без учеников — строка 12г
    urok(6, 106, "2026-11-10", t="16:00", konec="16:40")
    urok(7, 106, "2026-11-03", t="16:00", konec="16:40")
    urok(8, 104, "2026-11-08", t="12:00", konec="12:50")
    urok(9, 104, "2026-11-22", t="12:00", konec="12:50", status=0)
    # Р4а: был, ничего не списано, абонемента нет
    urok(10, 103, "2026-11-04", t="18:00", konec="18:50",
         zapisi=[(110, 9003, {"visit": True}), (111, 9022, {"visit": True, "free": True}),
                 (112, 9023, {"visit": True, "free": True})])
    # пропуск со списанием рядом у 9023 → «бесплатно» вместо отработки, деньги верны — не строка 4б
    urok(11, 103, "2026-11-09", t="18:00", konec="18:50", zapisi=[(113, 9023, {"bill": {"summa": 875}})])
    # Р8а: два занятия одного направления в один день, время пересекается
    urok(12, 101, "2026-11-17", zapisi=[(120, 9017, {}), (122, 9018, {"missedLessonRecordId": 999})], status=0)
    urok(13, 102, "2026-11-17", t="17:00", konec="17:50", zapisi=[(121, 9017, {}), (123, 9018, {})], status=0)
    # Р8б: выбыл, запись на будущее осталась
    urok(14, 108, "2026-11-18", t="19:00", konec="19:50", status=0, zapisi=[(130, 9019, {})])
    urok(15, 108, "2026-11-20", t="19:00", konec="19:50", status=0)
    # Р8г: первое занятие по направлению — пробное, а пометки нет
    urok(16, 107, "2026-11-19", t="16:10", konec="16:50", status=0, zapisi=[(140, 9021, {})])
    # Р12а: два одинаковых занятия группы; Р12б — время ≠ названию (107: 16:00 в названии, занятия 16:10)
    urok(17, 102, "2026-11-24", status=0)
    urok(18, 102, "2026-11-24", status=0)
    urok(19, 107, "2026-11-26", t="16:10", konec="16:50", status=0)
    # 12в — только по ночной проверке расписания до 31.05: группа 101 кончается 19.11,
    # группа 103 идёт до конца мая
    urok(20, 103, "2027-01-12", t="18:00", konec="18:50", status=0)
    urok(21, 101, "2026-11-19", status=0)
    db.set_state("proverka_mk_raspisanie", json.dumps({"ts": "2026-11-11", "do": "2027-05-31",
                                                       "posledniy": {"101": "2026-11-19", "103": "2027-05-26"}}))
    # Р7: абонементы сезона
    abonement(501, 9001, 101, 8400, 8400)                       # оплаченный
    abonement(502, 9001, 101, 8400, 0)                          # 7а: рядом оплаченный — дубль
    abonement(503, 9002, 102, 8400, 8400)
    abonement(504, 9002, 102, 8400, 0)                          # тоже «дубль», но удалён в МойКлассе
    abonement(505, 9003, 103, 7000, 0, poseshch=0)              # 7б: неоплаченный, без занятий
    abonement(506, 9025, 103, 3800, 3800, prodan="2025-07-02", nachalo="2025-07-01", konec="2025-07-31")   # 7г
    abonement(507, 9016, 103, 1600, 0, st=4, prodan="2026-11-01", poseshch=0)                              # 7д
    platezh(701, 9025, "2025-08-13", -3800, optype="refund", tip=97845)
    abonement(508, 9028, 103, 33400, 33400, prodan="2026-04-10", nachalo="2026-06-01", konec="2026-08-31")
    abonement(509, 9028, 103, 8505, 8505, poseshch=3)          # второй оплаченный — ноябрь
    platezh(702, 9028, "2026-08-22", -33400, optype="refund", tip=97845)
    db.save_invoices([{"id": 801, "userId": 9002, "price": 1, "payed": 0, "createdAt": "2026-10-10"}])
    db.pometit_udalennye("user_subscriptions", [504, 501, 502, 503, 505, 506, 507, 600, 601, 602, 603, 604], [
        501, 502, 503, 505, 506, 507, 600, 601, 602, 603, 604], "тест")
    # Р6: деньги на балансе без абонемента
    platezh(601, 9004, "2026-10-20", 8000)
    platezh(602, 9005, "2026-10-20", 2100)
    # Р11а: карточка-призрак (удалена в МойКлассе) — не строка
    db.pometit_udalennye("users", list(LYUDI), [u for u in LYUDI if u != 9007], "тест")
    # Р3: кэш сверки явки за 10.11 и 11.11
    def yavka_den(d, rec_id, uid, visit_now):
        return {"day": d, "lessons": [{"lesson_id": 30 if d == "2026-11-10" else 31, "class_id": 102, "class": "ПШ Гр2",
                                       "time": "17:00", "reports": [{"ts": d}],
                                       "records": [{"record_id": rec_id, "uid": uid, "name": LYUDI[uid][0], "crm_visit": visit_now,
                                                    "report": "был", "action": "rashozhdenie"}],
                                       "unmatched": [{"name": "Пробная Алиса", "kind": "был"},
                                                     {"name": "праздник осени", "kind": "был"}],
                                       "conflicts": []}]}
    urok(30, 102, "2026-11-10", zapisi=[(301, 9002, {}), (303, 9001, {"visit": True})])
    urok(31, 102, "2026-11-11", zapisi=[(302, 9002, {"visit": True})])
    with db.get_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS yavka_sverka (day TEXT PRIMARY KEY, ts TEXT, data TEXT)")
        conn.execute("INSERT INTO yavka_sverka VALUES (?,?,?)", ("2026-11-10", "x", json.dumps(yavka_den("2026-11-10", 301, 9002, False), ensure_ascii=False)))
        conn.execute("INSERT INTO yavka_sverka VALUES (?,?,?)", ("2026-11-11", "x", json.dumps(yavka_den("2026-11-11", 302, 9002, False), ensure_ascii=False)))
        conn.execute("CREATE TABLE IF NOT EXISTS kadry (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, lesson_id INTEGER, "
                     "day TEXT, ts TEXT, room TEXT, class_name TEXT, begin_time TEXT, end_time TEXT, zapisano INTEGER, "
                     "otmecheno INTEGER, status INTEGER, file TEXT, error TEXT, deti INTEGER, vzroslye INTEGER, ocenka TEXT, ocenka_ts TEXT)")
        # Р9: на кадре детей нет, а «проведено» и «был»; кадр пустой группы — чужое занятие, не строка
        conn.execute("INSERT INTO kadry (key, lesson_id, day, ts, room, deti) VALUES ('31', 31, '2026-11-11', '2026-11-11T17:10', 'К1', 0)")
        conn.execute("INSERT INTO kadry (key, lesson_id, day, ts, room, deti) VALUES ('6', 6, '2026-11-10', '2026-11-10T16:10', 'К3', 1)")
        conn.execute("CREATE TABLE IF NOT EXISTS crm_comments (id INTEGER PRIMARY KEY, user_id INTEGER, ts TEXT, manager_id INTEGER, text TEXT)")
    # Р5: файл контроля оплат за 10.11
    platezh(1001, 9001, "2026-11-10", 1500, tip=64725)          # «карта», а по банку СБП
    platezh(1002, 9002, "2026-11-10", 2500, tip=64725)
    (KONTROL / "2026-11-10.json").write_text(json.dumps({
        "day": "2026-11-10", "sostavleno": "11.11.2026 08:30", "max_pid": 1002, "bloki": {},
        "bank": {"mk_card": 2500, "bank_card": 5000, "diff": -2500, "free_sbp": [2500],
                 "sbp_as_term": [["Лиза", "Тестова Ева", 1500, 1001]]},
        "matkap_net": [{"summa": 50000, "fio": "Образцова Мила", "data": "2026-11-09"}], "vozvraty": []},
        ensure_ascii=False), encoding="utf-8")
    # Р10: файл сверки честности — правка платежа и «механика» (списание ушло на отработку)
    urok(32, 101, "2026-11-02", zapisi=[(320, 9018, {"bill": None}), (321, 9018, {"visit": True, "missedLessonRecordId": 320})])
    (SVERKA / "2026-11-11.json").write_text(json.dumps({"day": "2026-11-11", "nakhodki": [
        {"level": "yellow", "kind": "правка: payments", "text": "Тестов Марк: тип нал → терминал (замечено 11.11 08:40 МСК, автор строки —)",
         "userId": 9002, "rid": 1002, "tbl": "payments", "extra": {"paymentTypeId": [1, 64725]}},
        {"level": "yellow", "kind": "правка: lesson_records", "text": "Отработкин Ян: снято списание за пропуск 875 ₽",
         "userId": 9018, "rid": 320, "tbl": "lesson_records", "extra": {"bill": [{"summa": 875}, None]}},
        {"level": "red", "kind": "удаление: payments", "text": "красное — Борису, не на страницу", "userId": 9001, "rid": 5, "tbl": "payments"},
        {"level": "yellow", "kind": "удаление: user_subscriptions", "text": "Тестова Ева: удалён абонемент 8400 ₽ (оплачено 0, 2/8)",
         "userId": 9001, "rid": 510, "tbl": "user_subscriptions", "extra": {"raw": {"payed": 0, "visitedCount": 2}}},
    ]}, ensure_ascii=False), encoding="utf-8")

    S, res = stroki()
    proverka("1: прошло, не закрыто, записи есть — строка", "r1:1" in S)
    proverka("1: без записей — не строка", "r1:2" in S, False)
    proverka("1: в действии «если занятия не было — снять записи» (поправка скептика)",
             "снять записи" in S["r1:1"]["sdelat"])
    proverka("2а: «проведено», все записанные не пришли — строка-вопрос", "r2a:3" in S and "было ли занятие" in S["r2a:3"]["chto"])
    proverka("2а: индивидуальное с оплаченным пропуском — не строка", "r2a:4" in S, False)
    proverka("2а: «не ошибка» можно (догадка)", S["r2a:3"]["dogadka"])
    proverka("2в: «проведено» раньше начала — строка", "r2v:5" in S)
    proverka("2б: пустые «проведено» — одна сводная строка", sum(1 for k in S if k.startswith("r2b")), 1)
    proverka("2б: сводная — «ждёт решения Бориса», в «осталось» не входит",
             S["r2b:svodka"]["seraya"] == "ждёт решения Бориса" and "2 занятий" in S["r2b:svodka"]["chto"])
    proverka("12г: группа без учеников, а занятия «проводятся» — строка на группу", "r12g:104" in S)
    proverka("3: по отчёту был, в МойКлассе «не был» — строка", "r3:rashozhdenie:9002:102" in S)
    proverka("3: «был» уже поставлен (11.11) — этот день в строку не попал", S["r3:rashozhdenie:9002:102"]["fakty"]["dni"], ["2026-11-10"])
    proverka("3: «был без записи» — повторы по дням склеены в одну строку",
             S.get("r3u:9003:102", {}).get("fakty", {}).get("dni"), ["2026-11-10", "2026-11-11"])
    proverka("3: тема занятия вместо имени («праздник осени») — не строка",
             any("праздник" in (x.get("rebenok") or "") for x in S.values()), False)
    proverka("4а: был, ничего не списано — строка с причиной (пропуск 03.11 со списанием → отработка)",
             "r4a:9003:ИЗО" in S and "не отмечена отработка (пропуск 03.11" in S["r4a:9003:ИЗО"]["chto"])
    proverka("4а: строка с причиной «абонемента нет», если пропусков рядом нет",
             pm._prichina_4a(pm.Dannye(CHASY), 9022, "ИЗО", "2026-11-04")[0] in ("абонемента нет", "пробное не отмечено «пробным»"))
    proverka("4б: «бесплатно», не пробное, не отработка — строка", "r4b:111" in S)
    proverka("4б: рядом пропуск со списанием — это отработка, не строка (поправка скептика)", "r4b:112" in S, False)
    proverka("5: «карта», а по банку СБП — строка по id платежа", "r5:2026-11-10:sbpterm:1001" in S)
    proverka("5: терминал за день не сходится с банком — строка", "r5:2026-11-10:bank" in S)
    proverka("5: СБП есть в банке, в МойКлассе нет — строка", "r5:2026-11-10:sbp:2500:0" in S)
    proverka("5: маткапитал не внесён — строка", "r5:matkap:2026-11-09:50000" in S)
    proverka("6: на балансе 8 000 ₽, абонемента нет — строка", "r6:9004" in S)
    proverka("6: остаток 2 100 ₽ без маткапитала — не строка (поправка скептика)", "r6:9005" in S, False)
    proverka("7а: неоплаченный рядом с оплаченным того же курса — строка", "r7a:502" in S)
    proverka("7а: удалённый в МойКлассе абонемент — не строка (призрак)", "r7a:504" in S, False)
    proverka("7б: неоплаченный без единого занятия — строка", "r7b:505" in S)
    proverka("7в: неоплаченный счёт на 1 ₽ — строка", "r7v:801" in S)
    proverka("7г: открыт после возврата всей оплаты (летний, по модулю) — строка", "r7g:506" in S)
    proverka("7г: возврат ровно на неиспользованный, хотя есть второй оплаченный — строка", "r7g:508" in S)
    proverka("7г: оплаченный ноябрьский рядом — не строка", "r7g:509" in S, False)
    proverka("7д: минус на балансе из-за «оконченного» неоплаченного — строка", "r7d:9016" in S)
    proverka("8а: две записи одного направления в один день — строка", "r8a:9017:101-102" in S)
    proverka("8а: отработка в другой группе — не лишняя запись (поправка скептика)",
             any(k.startswith("r8a:9018") for k in S), False)
    proverka("8б: выбыл, а запись на будущее осталась — строка", "r8b:9019:108" in S)
    proverka("8в: «учится», а записей на 3 недели нет — строка «проверить»", "r8v:9020:108" in S)
    proverka("8г: пробное без пометки — строка", "r8g:9021:АЯ" in S)
    proverka("9: на кадре детей нет, а «проведено» и «был» — строка", "r9:31:pusto" in S)
    proverka("9: кадр занятия без единой записи — чужое занятие, не строка",
             any(k.startswith("r9:6:") for k in S), False)
    proverka("10: правка без объяснения — строка", "r10:payments:1002" in S)
    proverka("10: списание за пропуск ушло на отработку — механика, не строка", "r10:lesson_records:320" in S, False)
    proverka("10: красное — Борису, не на страницу", "r10:payments:5" in S, False)
    proverka("10: удалён неоплаченный абонемент (с посещениями) — действие раздела 7, не строка",
             "r10:user_subscriptions:510" in S, False)
    proverka("11а: «ДУБЛЬ» при живом статусе — строка", "r11a:9006" in S)
    proverka("11а: карточка удалена в МойКлассе — не строка (призрак)", "r11a:9007" in S, False)
    proverka("11а: «архив» без проверки не советуем (поправка скептика)", "настоящий ребёнок" in S["r11a:9006"]["sdelat"])
    proverka("11б: номер вместо имени после разговора — строка", "r11b:9008" in S)
    proverka("11б: новый лид-номер, но тот же номер во второй карточке — строка «дубль №…»",
             "r11b:9026" in S and "№9027" in S["r11b:9026"]["sdelat"])
    proverka("11в: одно слово в имени + старая карточка в отказе — строка", "r11v:9009-9010" in S)
    proverka("11в: брат и брат (разные имена) — не строка", any(k.startswith("r11v:9011") for k in S), False)
    proverka("11г: заявка «новая», карточка в отказе — строка", "r11g:11" in S)
    proverka("11д: новая карточка после разговора без дня рождения — строка", "r11d:9014" in S)
    proverka("11д: новый лид без дня рождения — не строка (поправка скептика)", "r11d:9015" in S, False)
    proverka("12а: два одинаковых занятия в один час — строка", "r12a:102:17:00" in S)
    proverka("12б: время занятий ≠ времени в названии — строка", "r12b:107" in S)
    proverka("12в: расписание группы кончается рано (по ночной проверке) — строка", "r12v:101" in S)
    proverka("12в: группа с занятиями до конца мая — не строка", "r12v:103" in S, False)
    proverka("ссылка на карточку МойКласса", S["r6:9004"]["ssylka"], "https://app.moyklass.com/user/9004")
    proverka("ссылка на группу МойКласса", S["r1:1"]["ssylka"], "https://app.moyklass.com/class/101")
    proverka("у строки написано, когда она уйдёт сама", bool(S["r1:1"]["obnovitsya"]))
    vid = [x for x in S.values() if not x["seraya"] and not x["ne_oshibka"]]
    proverka("«осталось» — без «ждёт решения»", res["itog"]["ostalos"], len(vid))
    return S


# ── 2. Жизнь строки: исправили — ушла; окно не убирает открытую ───────────

def test_zhizn():
    print("Жизнь строки: исправили — закрылась; окно не убирает открытую строку")
    urok(1, 101, "2026-11-05", status=1, zapisi=[(101, 9001, {"visit": True, "bill": {"summa": 1050}})])
    platezh(1003, 9001, "2026-11-10", 2500, tip=2)     # СБП внесли после контроля
    S, res = stroki()
    proverka("1: занятие провели — строки нет", "r1:1" in S, False)
    with db.get_conn() as conn:
        z = conn.execute("SELECT zakryta FROM proverka_mk_stroki WHERE key='r1:1'").fetchone()[0]
    proverka("1: в таблице строка закрыта", bool(z))
    proverka("5: СБП внесли — строка ушла", "r5:2026-11-10:sbp:2500:0" in S, False)
    proverka("закрыто за сутки считается", res["itog"]["zakryto_za_sutki"] >= 2)
    # открытая строка старше окна (с 1-го числа прошлого месяца) живёт, новая такая — нет
    urok(40, 101, "2026-09-20", status=0, zapisi=[(400, 9001, {})])
    urok(41, 101, "2026-09-21", status=0, zapisi=[(401, 9001, {})])
    with db.get_conn() as conn:
        conn.execute("INSERT INTO proverka_mk_stroki (key, razdel, pervyi, posledniy, zakryta, zametka, dannye) "
                     "VALUES ('r1:40', '1', '2026-09-21T09:00', '2026-09-30T09:00', NULL, 'обещали маме: не списывать', ?)",
                     (json.dumps({"data": "2026-09-20"}),))
    S, _ = stroki()
    proverka("открытая строка от 20.09 не ушла по окну", "r1:40" in S)
    proverka("заметка закрытого пункта видна под строкой", S["r1:40"]["zametka"], "обещали маме: не списывать")
    proverka("новая строка старше окна не появляется", "r1:41" in S, False)
    proverka("возраст строки — от даты события", S["r1:40"]["vozrast"] > 14 and S["r1:40"]["hvost"])


# ── 3. «Не ошибка» ─────────────────────────────────────────────────────────

def test_ne_oshibka():
    print("«Не ошибка»: только с причиной и только где правило — догадка")
    pm.peresschet(CHASY)
    try:
        pm.ne_oshibka("r8v:9020:108", "  ", seychas=CHASY)
        proverka("без причины — отказ", False)
    except ValueError:
        proverka("без причины — отказ", True)
    try:
        pm.ne_oshibka("r7a:502", "это не дубль, так договорились", seychas=CHASY)
        proverka("в разделе 7 «не ошибка» не ставится", False)
    except ValueError:
        proverka("в разделе 7 «не ошибка» не ставится", True)
    pm.ne_oshibka("r8v:9020:108", "пауза до декабря, мама писала", kto="админ", seychas=CHASY)
    S, res = stroki()
    proverka("8в: строка скрыта и не входит в «осталось»", bool(S["r8v:9020:108"]["ne_oshibka"]))
    proverka("скрытых «не ошибка» — одна", res["itog"]["ne_oshibka_skryto"], 1)
    proverka("в сводке Бориса — «не ошибка» за неделю с причиной",
             [x["prichina"] for x in res["itog"]["ne_oshibka_za_nedelyu"]], ["пауза до декабря, мама писала"])
    # данные изменились (ребёнка записали в другую группу и сняли «учится») — «не ошибка» больше не действует
    urok(42, 108, "2026-11-20", t="19:00", konec="19:50", status=0, zapisi=[(420, 9020, {"test": True})])
    zapis_v_gruppu(8, 9020, 108, 2)
    S, _ = stroki()
    proverka("записали — строки нет совсем", "r8v:9020:108" in S, False)


# ── 4. Средний синк ────────────────────────────────────────────────────────

class FakeMK:
    """Подменённый ответ МойКласса: страницы списков, без сети."""

    def __init__(self, uroki, abonementy, total_uroki=None, total_abon=None, oshibka_na=None):
        self.uroki, self.abon = uroki, abonementy
        self.tu, self.ta = total_uroki, total_abon
        self.oshibka_na, self.zaprosov = oshibka_na, 0

    _extract_items = staticmethod(sync.MoyklassClient._extract_items)

    def get(self, path, params=None):
        self.zaprosov += 1
        off, lim = int(params.get("offset", 0)), int(params.get("limit", 500))
        if self.oshibka_na and self.oshibka_na[0] in path and off >= self.oshibka_na[1]:
            raise sync.MoyklassError("HTTP 502 после повторов")
        if "lessons" in path:
            return {"lessons": self.uroki[off:off + lim], "stats": {"totalItems": self.tu if self.tu is not None else len(self.uroki)}}
        return {"subscriptions": self.abon[off:off + lim], "stats": {"totalItems": self.ta if self.ta is not None else len(self.abon)}}

    def close(self):
        pass


def test_sredniy():
    print("Средний синк: помечает пропавшее только при полном списке и не больше 10 %")
    from app import moyklass_client
    moyklass_client.PAGE_LIMIT = 10          # чтобы листать страницы на маленьком примере
    config.DB_PATH = PAPKA / "sredniy.db"
    db.init_db()
    seg = datetime(2026, 11, 12).date()
    uroki = [{"id": 5000 + i, "date": f"2026-11-{1 + i % 25:02d}", "beginTime": "17:00", "classId": 101, "status": 1,
              "records": [{"id": 60000 + i, "userId": 9001, "visit": True}]} for i in range(30)]
    abon = [{"id": 7000 + i, "userId": 9001, "sellDate": "2026-09-10", "price": 100, "payed": 100, "statusId": 2}
            for i in range(30)]
    db.save_lessons(uroki)
    db.save_user_subscriptions(abon)
    vse_uroki = len(uroki)

    def pomecheno(tbl):
        return db.udalennye(tbl)

    # полный список без одного занятия и одного абонемента (3 % окна) → помечаем
    mk = FakeMK(uroki[1:], abon[1:])
    r = sync.sredniy_sync(client=mk, segodnya=seg)
    proverka("полный список: пропавшее занятие помечено", pomecheno("lessons"), {5000})
    proverka("полный список: пропавший абонемент помечен", pomecheno("user_subscriptions"), {7000})
    with db.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
    proverka("строки из базы не стёрты — только пометка", n, vse_uroki)
    proverka("запросов немного (страницы двух списков)", mk.zaprosov <= 8)
    proverka("итог среднего синка записан", json.loads(db.get_state("sredniy_sync"))["ok"])
    # строка вернулась живой — пометка снимается сама
    sync.sredniy_sync(client=FakeMK(uroki, abon), segodnya=seg)
    proverka("занятие снова в списке — пометка снята", pomecheno("lessons"), set())
    # неполный список: счётчик API больше, чем пришло строк
    sync.sredniy_sync(client=FakeMK(uroki[2:], abon[2:], total_uroki=30, total_abon=30), segodnya=seg)
    proverka("неполный список (счётчик не совпал) — ничего не помечено",
             (pomecheno("lessons"), pomecheno("user_subscriptions")), (set(), set()))
    # ошибка на второй странице
    r = sync.sredniy_sync(client=FakeMK(uroki[2:], abon, oshibka_na=("lessons", 10)), segodnya=seg)
    proverka("ошибка выгрузки занятий — занятия не помечены", pomecheno("lessons"), set())
    proverka("…и итог честно «не ок»", r["ok"], False)
    # пропало больше 10 % окна
    sync.sredniy_sync(client=FakeMK(uroki[5:], abon[5:]), segodnya=seg)
    proverka("пропало 5 из 30 (больше 10 %) — ничего не помечено",
             (pomecheno("lessons"), pomecheno("user_subscriptions")), (set(), set()))
    zh = json.loads(db.get_state("mk_udaleno_zhurnal"))
    proverka("…и это записано в журнал", any("больше 10" in (p.get("prichina") or "") for z in zh for p in z["pometki"]))
    # окно: занятие вне окна (сентябрь) не трогаем, даже если его нет в выгрузке
    db.save_lessons([{"id": 4999, "date": "2026-09-15", "beginTime": "17:00", "classId": 101, "status": 1, "records": []}])
    sync.sredniy_sync(client=FakeMK(uroki, abon), segodnya=seg)
    proverka("занятие вне окна не помечено", 4999 in pomecheno("lessons"), False)
    proverka("окно занятий — с 1-го числа прошлого месяца по +14 дней", sync.sredniy_okno(seg), ("2026-10-01", "2026-11-26"))
    moyklass_client.PAGE_LIMIT = 500
    config.DB_PATH = PAPKA / "kidsup.db"


# ── 5. Пульт: одна строка у Лизы ───────────────────────────────────────────

def test_pult():
    print("Пульт: строка «Проверка МойКласса» у Лизы, без галочки; при N = 0 — нет")
    st_sch, st_lenta, st_paid = pm.schetchik, pult._lenta, pult._paid_recently
    try:
        pult._paid_recently = lambda days=30: {}
        pult._lenta = lambda day, who, items: []
        pm.schetchik = lambda seychas=None: {"ostalos": 7, "novyh_za_sutki": 2, "ssylka": "/proverka-mk"}
        kol = pult._col("Лиза", [], True, "2026-11-12", "12:00")
        proverka("у Лизы сверху строка с числом", "Проверка МойКласса: осталось 7 (новых за сутки 2)" in kol)
        proverka("строка ведёт на /proverka-mk", "href='/proverka-mk'" in kol)
        stroka = kol.split("Проверка МойКласса")[0].rsplit("<li", 1)[-1]
        proverka("у строки нет галочки", "checkbox" in stroka, False)
        proverka("у Лены строки нет", "Проверка МойКласса" in pult._col("Лена", [], True, "2026-11-12", "12:00"), False)
        proverka("в /api/pult/tasks то же число", pult.proverka_mk_chislo()["ostalos"], 7)
        pm.schetchik = lambda seychas=None: {"ostalos": 0, "novyh_za_sutki": 0, "ssylka": "/proverka-mk"}
        proverka("при N = 0 строки нет", "Проверка МойКласса" in pult._col("Лиза", [], True, "2026-11-12", "12:00"), False)
        def padaet(seychas=None):
            raise RuntimeError("кэш битый")
        pm.schetchik = padaet
        proverka("страница сломалась — пульт рисуется без строки",
                 "Проверка МойКласса" in pult._col("Лиза", [], True, "2026-11-12", "12:00"), False)
    finally:
        pm.schetchik, pult._lenta, pult._paid_recently = st_sch, st_lenta, st_paid
    from app.pult_proverka import liza_mozhno
    proverka("Лизе можно «проведено» (правка в МойКлассе)", liza_mozhno("Вернуть «не проведено» у занятия 12.11"))
    proverka("Лизе можно явку", liza_mozhno("Поставить явку по отчёту педагога"))
    proverka("Лизе нельзя закрытие занятий за день", liza_mozhno("Закрыть занятия за вчера, 40 штук"), False)
    s = pm.stroka_dlya_borisa(CHASY)
    proverka("строка для вечернего саммари Бориса", s.startswith("Проверка МК: осталось ") and "«не ошибка» за неделю" in s)


# ── 6. Настройка proverka_mk_vmesto_punktov ────────────────────────────────

def test_nastroyka():
    print("Настройка proverka_mk_vmesto_punktov: «1» — пунктов Лизе нет, «0» — как раньше")
    from app import tbank, komtet, yavka
    st = (tbank.statement, komtet.cheki, yavka.sverka, ap.inbox_add, ap._client, ap._task, ap._admins_today)
    punkty = []
    try:
        tbank.statement = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("банк недоступен (тест)"))
        komtet.cheki = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("КОМТЕТ недоступен (тест)"))
        ap.inbox_add = lambda text, phone="", who="", source="": punkty.append((who, text)) or True
        # контроль оплат: «проведено» без присутствующих — пункт Лизе (блок C)
        urok(50, 101, "2026-11-11", zapisi=[(500, 9001, {})])
        db.set_setting("proverka_mk_vmesto_punktov", "1")
        R = kontrol_oplat.run("2026-11-11", dry=True)
        proverka("контроль оплат «1»: «проведено» без детей — на страницу, не в пульт",
                 (any("без присутствующих" in t for t in R["na_stranicu"]),
                  any("без присутствующих" in t for _, t, _ in R["inbox"])), (True, False))
        db.set_setting("proverka_mk_vmesto_punktov", "0")
        R = kontrol_oplat.run("2026-11-11", dry=True)
        proverka("контроль оплат «0»: как раньше — пункт Лизе",
                 any(w == "Лиза" and "без присутствующих" in t for w, t, _ in R["inbox"]))
        # сверка явки: при «1» пункт только про занятия без отчёта педагога
        it = {"s_otchetom": 3, "ne_provedeno": 1, "otmetok_postavit": 2, "imen_bez_zapisi": 1, "spornyh": 0,
              "konfliktov": 2, "bez_otcheta": 0}
        yavka.sverka = lambda day, **k: {"itogo": dict(it), "bez_otcheta": [{"time": "16:00", "class": "ЛГ Тестовая"}]}
        db.set_setting("proverka_mk_vmesto_punktov", "1")
        punkty.clear()
        yavka.nightly("2026-11-11", who="Лиза")
        proverka("явка «1», пункт Лизе: расхождения пунктом не перечисляем", punkty, [])
        it["bez_otcheta"] = 1
        yavka.nightly("2026-11-11", who="Лиза")
        proverka("явка «1», пункт Лизе: про занятия без отчёта педагога — пункт остаётся",
                 len(punkty) == 1 and "без отчёта педагога" in punkty[0][1] and "расхождений" not in punkty[0][1])
        # проверка скептика 12.11: пункт о явке идёт Лене, закрытие дня — её работа; его не глушим
        # 13.11.2026, Борис (М4, «по рекомендациям»): «Кому идёт пункт о незакрытой явке? — Лизе,
        # явку ведёт она». Было: yavka.nightly() без who клал Лене прежний полный пункт. Теперь по
        # умолчанию пункт идёт Лизе — при «1» коротким (только «без отчёта педагога»); полный текст
        # остаётся для явного who="Лена" и для настройки «0».
        punkty.clear()
        yavka.nightly("2026-11-11")
        proverka("явка «1», без who: пункт идёт Лизе, короткий — «без отчёта педагога», расхождения на странице",
                 (len(punkty), punkty[0][0] if punkty else "", "без отчёта педагога 1 занятий" in (punkty[0][1] if punkty else ""),
                  "app.kidsup.ru/proverka-mk, раздел 3" in (punkty[0][1] if punkty else ""),
                  any(x in (punkty[0][1] if punkty else "") for x in ("не проведено", "отметок поставить"))),
                 (1, "Лиза", True, True, False))
        punkty.clear()
        yavka.nightly("2026-11-11", who="Лена")
        proverka("явка «1», явно Лене: прежний текст — «не проведено», «отметок поставить», ссылка на /yavka",
                 (len(punkty), punkty[0][0] if punkty else "", all(x in (punkty[0][1] if punkty else "") for x in (
                     "не проведено 1", "отметок поставить 2", "app.kidsup.ru/yavka?day=2026-11-11"))), (1, "Лена", True))
        db.set_setting("proverka_mk_vmesto_punktov", "0")
        punkty.clear()
        yavka.nightly("2026-11-11")
        proverka("явка «0»: прежний пункт с расхождениями — тоже Лизе (решение 13.11)",
                 len(punkty) == 1 and punkty[0][0] == "Лиза" and "расхождений с CRM 2" in punkty[0][1])
        from app.pult_proverka import liza_mozhno as _lm
        proverka("явка «0»: сторож пульта не называет этот пункт «не тому»", _lm(punkty[0][1] if punkty else ""))
        # качество карточек
        class Mk:
            def fetch_all(self, *a, **k):
                return [{"id": 9101, "name": "лид без имени", "phone": "", "attributes": []}]

            def close(self):
                pass
        zadachi = []
        ap._client = lambda: Mk()
        ap._task = lambda mk, who, uid, body, day=None: zadachi.append(body)
        ap._admins_today = lambda: [{"managerId": 1, "name": "Лиза"}]
        db.set_setting("proverka_mk_vmesto_punktov", "1")
        ap.card_quality()
        proverka("качество карточек «1», дежурит Лиза: пункта нет (раздел 11д)", zadachi, [])
        # проверка скептика 12.11: пункт идёт дежурному админу по очереди — другим он нужен
        ap._admins_today = lambda: [{"managerId": 2, "name": "Аня"}]
        ap.card_quality()
        proverka("качество карточек «1», дежурит не Лиза: пункт ставится как раньше", len(zadachi), 1)
        zadachi.clear()
        ap._admins_today = lambda: [{"managerId": 1, "name": "Лиза"}]
        db.set_setting("proverka_mk_vmesto_punktov", "0")
        ap.card_quality()
        proverka("качество карточек «0»: пункт как раньше", len(zadachi), 1)
        # выключатель возвращает и прежнее правило «явка и проведено — не Лизе»
        # 13.11.2026, Борис (М4): явку ведёт Лиза — было «„0“: пункт со словом „явка“ Лизе снова
        # нельзя». Теперь «явка» Лизе можно при любой настройке; «проведено» без слова «явка» при
        # «0» — по-прежнему чужой пункт, как и закрытие занятий за день.
        from app.pult_proverka import liza_mozhno
        proverka("«0»: пункт со словом «явка» Лизе можно и при выключателе (решение 13.11)",
                 liza_mozhno("Поставить явку по отчёту педагога"))
        proverka("«0»: «проведено» без слова «явка» Лизе по-прежнему нельзя",
                 liza_mozhno("Вернуть «не проведено» у занятия 12.11"), False)
        proverka("«0»: закрытие занятий за день Лизе нельзя", liza_mozhno("Закрыть занятия за вчера, 40 штук"), False)
        proverka("«0»: пункт про долг Лизе можно", liza_mozhno("Долг 2 500 ₽ у семьи Тестовых"))
        db.set_setting("proverka_mk_vmesto_punktov", "1")
        proverka("«1»: пункт со словом «явка» Лизе можно", liza_mozhno("Поставить явку по отчёту педагога"))
    finally:
        (tbank.statement, komtet.cheki, yavka.sverka, ap.inbox_add, ap._client, ap._task, ap._admins_today) = st
        db.set_setting("proverka_mk_vmesto_punktov", "1")


# ── 7. HTTP: «не ошибка» без причины → 400 ─────────────────────────────────

def test_http():
    print("HTTP: страница, API и «не ошибка» без причины → 400")
    ap.start = lambda: None
    import app.main as m
    from fastapi.testclient import TestClient
    c = TestClient(m.app)
    r = c.get("/proverka-mk")
    proverka("страница открывается", r.status_code, 200)
    proverka("на странице разделы и кнопка «Перепроверить сейчас»",
             "Перепроверить сейчас" in r.text and "Прошло — не закрыто" in r.text and "Сводка для Бориса" in r.text)
    r = c.post("/api/proverka-mk/ne-oshibka", json={"key": "r9:31:pusto", "prichina": ""})
    proverka("«не ошибка» без причины → 400", r.status_code, 400)
    r = c.post("/api/proverka-mk/ne-oshibka", json={"key": "r6:9004", "prichina": "деньги на подарок, так решили"})
    proverka("«не ошибка» в разделе 6 → 400", r.status_code, 400)
    r = c.post("/api/proverka-mk/ne-oshibka", json={"key": "r9:31:pusto", "prichina": "дети были в К2, педагог подтвердил"})
    proverka("«не ошибка» с причиной в разделе 9 → 200", r.status_code, 200)
    d = c.get("/api/proverka-mk").json()
    proverka("API: итог и разделы", len(d["razdely"]) == 12 and "ostalos" in d["itog"])
    proverka("/api/pult/tasks: число для колонки Лизы", "ostalos" in c.get("/api/pult/tasks").json()["proverka_mk"])
    proverka("выключатель и список выключенных разделов меняются через /api/settings (откат без выкладки)",
             {"proverka_mk_vmesto_punktov", "proverka_mk_vyklyucheny"} <= m.SETTABLE)
    r = c.post("/api/proverka-mk/zametka", json={"key": "r6:9004", "text": "из пункта №6159: маткапитал 10.11"})
    proverka("заметка к строке сохраняется", r.status_code == 200
             and any(x.get("zametka") for rz in c.get("/api/proverka-mk").json()["razdely"] for x in rz["stroki"]
                     if x["key"] == "r6:9004"))


# ── 8. Ложные строки, найденные сверкой 12.11 на копии базы ────────────────

def test_sverka_1211():
    print("Сверка 12.11: ложные строки разделов 1, 3, 4б, 7 и 10")
    c = sqlite3.connect(config.DB_PATH)
    c.executemany("INSERT INTO classes (id, name, status, raw) VALUES (?,?,?,'{}')", [
        (201, "2627_АЯ_пн-ср_18:00_9-11 лет (Гр21)", "opened"),
        (202, "2627_ПШ_пн-чт_18:00_5-7 лет_ПШ2 читающие (Гр22)", "opened"),
        (203, "2627_ПШ_пн-чт_19:00_5-7 лет_ПШ2 читающие (Гр23)", "opened"),
        (204, "2627_МА_вс_14:00_7-12 лет", "opened")])
    novye = {9101: "Непроведённов Ян", 9102: "Небылова Ася", 9103: "Соседов Лев", 9104: "Расхождин Тим",
             9105: "Слотова Мия", 9106: "Заморозкина Ида", 9107: "Тёзкин Саша", 9108: "Тёзкин Саша",
             9109: "Беззаписин Рома", 9110: "Спорова Уля", 9111: "Пересчётов Глеб", 9112: "Журналова Зина",
             9113: "Объяснённов Юра", 9114: "Подаркова Эля"}
    for uid, n in novye.items():
        c.execute("INSERT INTO users (id, name, phone, balance, client_state_id, created_at, raw) VALUES (?,?,?,?,?,?,?)",
                  (uid, n, tel(uid - 9000), 0, 125955, "2026-08-20", json.dumps({"name": n, "attributes": []}, ensure_ascii=False)))
    c.commit()
    c.close()
    for i, (uid, cid) in enumerate([(9101, 202), (9102, 202), (9103, 202), (9104, 202), (9105, 203), (9106, 202),
                                    (9109, 202), (9110, 202), (9107, 204), (9108, 204), (9114, 201)]):
        zapis_v_gruppu(900 + i, uid, cid, 2)

    # Р1: день, где не закрыто 5 занятий, — одна строка «день не закрыт»; 4 — по занятию
    for i in range(5):
        urok(700 + i, 201, "2026-10-13", t=f"{12 + i}:00", konec=f"{12 + i}:50", status=0, zapisi=[(7000 + i, 9101, {})])
    for i in range(4):
        urok(710 + i, 201, "2026-10-14", t=f"{12 + i}:00", konec=f"{12 + i}:50", status=0, zapisi=[(7100 + i, 9101, {})])
    # сверка честности «подтвердила» удаление живой записи (404 на живую запись непроведённого
    # занятия) — такой пометке для записей не верим; для абонементов — верим
    abonement(520, 9111, 201, 8400, 8400)
    abonement(521, 9111, 201, 8400, 0)              # «дубль», но сверка честности подтвердила удаление
    db.set_state("sverka_chestnosti_udaleno", json.dumps({"lesson_records:7100": "2026-10-29",
                                                          "user_subscriptions:521": "2026-10-15"}))

    # Р3
    def yav(d, lessons):
        with db.get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO yavka_sverka VALUES (?,?,?)",
                         (d, "x", json.dumps({"day": d, "lessons": lessons}, ensure_ascii=False)))

    def zap(rid, uid, note="", act="rashozhdenie"):
        return {"record_id": rid, "uid": uid, "name": novye[uid], "crm_visit": act == "conflict_visit", "report": "был",
                "note": note, "action": act}

    urok(720, 202, "2026-10-15", t="18:00", konec="18:50", status=0, zapisi=[(7200, 9101, {})])      # не проведено
    urok(721, 202, "2026-10-15", t="18:00", konec="18:50", zapisi=[(7210, 9102, {}), (7211, 9103, {}), (7212, 9104, {}),
                                                                   (7213, 9110, {"visit": True})])
    urok(722, 203, "2026-10-15", t="19:00", konec="19:50", zapisi=[(7220, 9103, {"visit": True}), (7221, 9105, {"visit": True})])
    yav("2026-10-15", [
        {"lesson_id": 720, "class_id": 202, "class": "ПШ Гр22", "time": "18:00", "reports": [{"ts": "x"}],
         "records": [zap(7200, 9101)], "unmatched": [], "conflicts": []},
        {"lesson_id": 721, "class_id": 202, "class": "ПШ Гр22", "time": "18:00", "reports": [{"ts": "x"}],
         "records": [zap(7210, 9102, "не было"), zap(7211, 9103), zap(7212, 9104)],
         "unmatched": [{"name": "Слотова Мия", "kind": "был", "note": ""},
                       {"name": "Заморозкина Ида", "kind": "был", "note": "заморозка"},
                       {"name": "Тёзкин Саша", "kind": "был", "note": ""},
                       {"name": "Беззаписин Рома", "kind": "был", "note": "отработка"}],
         "conflicts": ["Спорова Уля: в одном отчёте «был», в другом «не был»"]}])
    urok(723, 202, "2026-11-05", t="18:00", konec="18:50", zapisi=[(7230, 9104, {}), (7231, 9110, {"visit": True})])
    yav("2026-11-05", [
        {"lesson_id": 723, "class_id": 202, "class": "ПШ Гр22", "time": "18:00", "reports": [{"ts": "x"}, {"ts": "y"}],
         "records": [zap(7230, 9104), dict(zap(7231, 9110), action=None, crm_visit=True)], "unmatched": [],
         "conflicts": ["Расхождин Тим: в одном отчёте «был», в другом «не был»",
                       "Спорова Уля: в одном отчёте «был», в другом «не был»"]}])

    # Р10: «эхо» цены абонемента, комментарий автоматики, объяснение человека накануне
    platezh(1101, 9111, "2026-11-01", -8400, optype="debit", userSubscriptionId=520)
    platezh(1102, 9112, "2026-11-08", 2500)
    platezh(1103, 9113, "2026-11-08", 2500)
    (SVERKA / "2026-11-09.json").write_text(json.dumps({"day": "2026-11-09", "nakhodki": [
        {"level": "yellow", "kind": "правка: payments", "text": "Пересчётов Глеб: сумма -9450 → -8400 (замечено 09.11 07:02 МСК, автор строки —)",
         "userId": 9111, "rid": 1101, "tbl": "payments", "extra": {"summa": [-9450, -8400]}},
        {"level": "red", "kind": "правка: user_subscriptions",
         "text": "Пересчётов Глеб: цена оплаченного абонемента снижена 9450 → 8400; занятий в абонементе 9 → 8",
         "userId": 9111, "rid": 520, "tbl": "user_subscriptions", "extra": {"price": [9450, 8400]}},
        {"level": "yellow", "kind": "правка: payments", "text": "Журналова Зина: тип нал → терминал",
         "userId": 9112, "rid": 1102, "tbl": "payments", "extra": {"paymentTypeId": [1, 64725]}},
        {"level": "yellow", "kind": "правка: payments", "text": "Объяснённов Юра: тип нал → терминал",
         "userId": 9113, "rid": 1103, "tbl": "payments", "extra": {"paymentTypeId": [1, 64725]}},
    ]}, ensure_ascii=False), encoding="utf-8")
    # Р4б: «бесплатно» без объяснения; в карточке только журнал звонка автоматики с этой датой
    urok(730, 201, "2026-11-06", t="18:00", konec="18:50", zapisi=[(7300, 9114, {"visit": True, "free": True})])
    with db.get_conn() as conn:
        conn.executemany("INSERT INTO crm_comments (id, user_id, ts, manager_id, text) VALUES (?,?,?,?,?)", [
            (1, 9112, "2026-11-09T11:00:00", None, "09.11 13:55, исходящий, 2 мин 10 с: мама уточняла расписание"),
            (2, 9113, "2026-11-08T15:00:00", 154181, "оплата была картой, тип исправила"),
            (3, 9114, "2026-11-06T16:00:00", None, "06.11 18:40, входящий, 40 с: мама спрашивала про 06.11"),
        ])

    S, _ = stroki()
    proverka("1: не закрыто 5 занятий за день — одна строка «день не закрыт»",
             "r1:den:2026-10-13" in S and "5 из 5" in S["r1:den:2026-10-13"]["chto"])
    proverka("1: … и поштучных строк за этот день нет", any(f"r1:{700 + i}" in S for i in range(5)), False)
    proverka("1: строка дня ведёт на явку за день", S["r1:den:2026-10-13"]["ssylka"], "/yavka?day=2026-10-13")
    proverka("1: не закрыто 4 занятия — четыре строки по занятию, строки дня нет",
             (sum(f"r1:{710 + i}" in S for i in range(4)), "r1:den:2026-10-14" in S), (4, False))
    proverka("1: «удалена» по сверке честности, а запись жива — строка остаётся", "r1:710" in S)
    proverka("7а: абонемент, удаление которого подтвердила сверка честности, — не строка", "r7a:521" in S, False)
    proverka("3: занятие не проведено — расхождения нет (это раздел 1)", "r3:rashozhdenie:9101:202" in S, False)
    proverka("3: в пометке педагога «не было» — не «был», строки нет", "r3:rashozhdenie:9102:202" in S, False)
    proverka("3: «был» стоит на соседнем занятии того же направления — строки нет", "r3:rashozhdenie:9103:202" in S, False)
    proverka("3: настоящее расхождение — строка, дни склеены",
             S.get("r3:rashozhdenie:9104:202", {}).get("fakty", {}).get("dni"), ["2026-10-15", "2026-11-05"])
    proverka("3: «был без записи», а «был» стоит в соседней группе направления — строки нет",
             any(k.startswith("r3u:9105") for k in S), False)
    proverka("3: «был без записи» с пометкой «заморозка» — строки нет", any(k.startswith("r3u:9106") for k in S), False)
    proverka("3: тёзки не из этой группы и направления — чью запись заводить, не понять; строки нет",
             any(k.startswith(("r3u:9107", "r3u:9108")) for k in S), False)
    proverka("3: «был без записи» у ребёнка этой группы — строка", "r3u:9109:202" in S)
    proverka("3: спор двух отчётов о ребёнке, у которого уже есть строка расхождения, — без отдельной строки",
             ("r3k:202:9104" in S, "расходятся" in S["r3:rashozhdenie:9104:202"]["chto"]), (False, True))
    proverka("3: спор двух отчётов — строка только за последние 14 дней, ключ без имени",
             S.get("r3k:202:9110", {}).get("fakty", {}).get("dni"), ["2026-11-05"])
    proverka("10: списание пересчитано вслед за ценой абонемента — одна строка про абонемент",
             ("r10:payments:1101" in S, "цена оплаченного абонемента снижена" in S.get("r10:user_subscriptions:520", {}).get("chto", "")),
             (False, True))
    proverka("10: комментарий автоматики (журнал звонка) строку не закрывает", "r10:payments:1102" in S)
    proverka("10: объяснение человека накануне находки — строки нет", "r10:payments:1103" in S, False)
    proverka("4б: дата занятия в журнале звонка автоматики — не объяснение, строка остаётся", "r4b:7300" in S)
    with db.get_conn() as conn:
        conn.execute("INSERT INTO crm_comments (id, user_id, ts, manager_id, text) VALUES (4, 9114, '2026-11-07T10:00:00', 154181, "
                     "'06.11 бесплатно — подарок на день рождения')")
    S, _ = stroki()
    proverka("4б: админ написала строку с датой занятия — строка ушла", "r4b:7300" in S, False)


# ── 9. Проверка скептика 12.11 вечером: опасные советы и выключенные разделы ─

def test_skeptik2():
    print("Проверка 12.11 вечером: дубль занятия, доплата, возврат переплаты, выключенные разделы")
    from datetime import timedelta
    c = sqlite3.connect(config.DB_PATH)
    c.executemany("INSERT INTO classes (id, name, status, raw) VALUES (?,?,?,'{}')", [
        (301, "2627_ТАН_пн-чт_10:00_5-7 лет (Гр31)", "opened"),
        (302, "2627_РОБ_Группа 5_вт_11:00_5-7 лет", "opened")])
    novye = {9201: "Дублёв Сева", 9202: "Сегоднев Ян", 9203: "Сегоднева Яна", 9204: "Спорный Ян",
             9205: "Сверхов Тим", 9206: "Пропускова Аня", 9207: "Доплатин Лёва", 9208: "Переплатова Соня"}
    for uid, n in novye.items():
        c.execute("INSERT INTO users (id, name, phone, balance, client_state_id, created_at, raw) VALUES (?,?,?,?,?,?,?)",
                  (uid, n, tel(uid - 9000), 0, 125955, "2026-08-20",
                   json.dumps({"name": n, "attributes": [{"attributeAlias": "birthday", "value": "2020-01-01"}]}, ensure_ascii=False)))
    c.commit()
    c.close()
    for i, (uid, cid) in enumerate([(9201, 301), (9202, 301), (9203, 301), (9204, 301), (9205, 302), (9206, 302),
                                    (9207, 301), (9208, 301)]):
        zapis_v_gruppu(950 + i, uid, cid, 2)

    def yav(d, lesson):
        with db.get_conn() as conn:
            r = conn.execute("SELECT data FROM yavka_sverka WHERE day=?", (d,)).fetchone()
            data = json.loads(r[0]) if r else {"day": d, "lessons": []}
            data["lessons"].append(lesson)
            conn.execute("INSERT OR REPLACE INTO yavka_sverka VALUES (?,?,?)", (d, "x", json.dumps(data, ensure_ascii=False)))

    # Р1: дубль занятия — двойник в тот же день и час проведён, ребёнку списано
    urok(800, 301, "2026-11-02", t="10:00", konec="10:50", zapisi=[(8000, 9201, {"visit": True, "bill": {"summa": 2500}})])
    urok(801, 301, "2026-11-02", t="10:00", konec="10:50", status=0, zapisi=[(8001, 9201, {})])
    urok(812, 301, "2026-11-02", t="10:00", konec="10:50", status=0, zapisi=[(8120, 9204, {})])   # дубль, но ребёнок другой
    # Р2а: сегодня и вчера — явку ещё вносят; с отчётом педагога — занятие было; старое без отчёта — строка
    for lid, d in ((802, "2026-11-12"), (803, "2026-11-11"), (804, "2026-11-09"), (805, "2026-11-06")):
        urok(lid, 301, d, t="09:00", konec="09:50", zapisi=[(lid * 10, 9202, {}), (lid * 10 + 1, 9203, {})])
    yav("2026-11-09", {"lesson_id": 804, "class_id": 301, "class": "ТАН Гр31", "time": "09:00", "reports": [{"ts": "x"}],
                       "records": [], "unmatched": [], "conflicts": []})
    # Р3: отчёты спорили 20.10 (старше 14 дней) — отдельной строки нет, но приписка к строке ребёнка есть
    urok(806, 301, "2026-10-20", t="10:00", konec="10:50", zapisi=[(8060, 9204, {"visit": True, "bill": {"summa": 1050}})])
    yav("2026-10-20", {"lesson_id": 806, "class_id": 301, "class": "ТАН Гр31", "time": "10:00", "reports": [{"ts": "x"}],
                       "records": [{"record_id": 8060, "uid": 9204, "name": "Спорный Ян", "crm_visit": True,
                                    "report": "не был", "action": "conflict_visit"}],
                       "unmatched": [], "conflicts": ["Спорный Ян: в одном отчёте «был», в другом «не был»"]})
    # Р4а: посещение 30.10 сверх абонемента, пропуск со списанием случился ПОЗЖЕ (05.11) — это не отработка;
    # у второго ребёнка пропуск 23.10 раньше посещения — отработка
    abonement(530, 9205, 302, 8400, 8400, nachalo="2026-10-01", konec="2026-10-31", prodan="2026-10-01", st=4)
    abonement(531, 9206, 302, 8400, 8400, nachalo="2026-10-01", konec="2026-10-31", prodan="2026-10-01", st=4)
    urok(809, 302, "2026-10-23", t="11:00", konec="11:50", zapisi=[(8090, 9206, {"bill": {"summa": 1050}}),
                                                                   (8091, 9205, {"visit": True, "bill": {"summa": 1050}})])
    urok(807, 302, "2026-10-30", t="11:00", konec="11:50", zapisi=[(8070, 9205, {"visit": True}), (8071, 9206, {"visit": True})])
    urok(808, 302, "2026-11-05", t="11:00", konec="11:50", zapisi=[(8080, 9205, {"bill": {"summa": 1050}})])
    # Р5: файл контроля оплат старого вида (без max_pid) — строку «СБП без оплаты» не закрыть, не показываем
    (KONTROL / "2026-11-05.json").write_text(json.dumps({
        "day": "2026-11-05", "bloki": {}, "bank": {"mk_card": 0, "bank_card": 0, "diff": 0, "free_sbp": [3300], "sbp_as_term": []}},
        ensure_ascii=False), encoding="utf-8")
    # Р7а: неоплаченный абонемент на одно занятие рядом с оплаченным на 13 — доплата, не дубль
    abonement(540, 9207, 301, 28817, 28817, zanyatiy=13, poseshch=7)
    abonement(541, 9207, 301, 2217, 0, zanyatiy=1, prodan="2026-11-06")
    urok(810, 301, "2026-11-16", t="10:00", konec="10:50", status=0, zapisi=[(8100, 9207, {}), (8101, 9208, {})])
    # Р7г: возврат переплаты (больше оплаты абонемента), а ребёнок после возврата ходит
    abonement(550, 9208, 301, 8400, 8400, poseshch=1)
    platezh(1200, 9208, "2026-11-03", -13965, optype="refund", tip=64725)
    urok(811, 301, "2026-11-05", t="10:00", konec="10:50", zapisi=[(8110, 9208, {"visit": True, "bill": {"summa": 1050}})])
    # Р9: на кадре пусто, но оценка кадра говорит «не пропуск» (ребёнок пришёл в другое время)
    with db.get_conn() as conn:
        conn.execute("INSERT INTO kadry (key, lesson_id, day, ts, room, deti, ocenka) VALUES ('k811', 811, '2026-11-05', "
                     "'2026-11-05T10:10', 'К1', 0, 'комната пустая. По отчёту педагога ребёнок был раньше — расхождение записи, не пропуск')")

    db.set_setting("proverka_mk_vyklyucheny", "-")
    S, res = stroki()
    proverka("1: дубль занятия — строка есть, совет «НЕ проводить», а не «провести»",
             ("дубль занятия" in S.get("r1:801", {}).get("chto", ""), "НЕ проводить" in S.get("r1:801", {}).get("sdelat", ""),
              "провести с явкой" in S.get("r1:801", {}).get("sdelat", "")), (True, True, False))
    proverka("1: дубль с другим ребёнком — не «снять записи», а перезаписать на проведённое занятие",
             ("записать туда" in S.get("r1:812", {}).get("sdelat", ""), "снять записи" in S.get("r1:812", {}).get("sdelat", "")),
             (True, False))
    proverka("2а: сегодняшнее и вчерашнее «проведено» без явки — не строка (явку ещё вносят)",
             ("r2a:802" in S, "r2a:803" in S), (False, False))
    proverka("2а: есть отчёт педагога — занятие было, не строка", "r2a:804" in S, False)
    proverka("2а: старше двух дней и без отчёта — строка", "r2a:805" in S)
    proverka("3: старый спор отчётов — отдельной строки нет, приписка «уточнить у педагога» есть",
             (any(k.startswith("r3k:301") for k in S), "расходятся" in S.get("r3:conflict_visit:9204:301", {}).get("chto", ""),
              "уточнить у педагога" in S.get("r3:conflict_visit:9204:301", {}).get("sdelat", "")), (False, True, True))
    proverka("4а: пропуск случился позже посещения — это не отработка, а «ходил сверх абонемента»",
             ("отработ" in S.get("r4a:9205:РОБ", {}).get("sdelat", "x отработ"), "абонемент кончился" in S.get("r4a:9205:РОБ", {}).get("chto", "")),
             (False, True))
    proverka("4а: пропуск раньше посещения — отработка", "отработку пропуска 23.10" in S.get("r4a:9206:РОБ", {}).get("sdelat", ""))
    proverka("5: файл без max_pid — строки «СБП без оплаты» нет", any(k.startswith("r5:2026-11-05:sbp") for k in S), False)
    proverka("6: в совете — «ушёл или отложили — абонемент НЕ создавать»", "НЕ создавать" in S["r6:9004"]["sdelat"])
    proverka("7а: доплата за одно занятие рядом с абонементом на 13 — не дубль и не «удалить»",
             ("r7a:541" in S, "r7b:541" in S), (False, False))
    proverka("7а: настоящий дубль (занятий поровну) остаётся строкой", "r7a:502" in S)
    proverka("7г: после возврата ребёнок ходит и записан вперёд — «закрыть абонемент» не советуем", "r7g:550" in S, False)
    proverka("7г: возврат без посещений после него — строка остаётся", "r7g:506" in S)
    proverka("9: в оценке кадра «не пропуск» — строки нет", any(k.startswith("r9:811") for k in S), False)
    proverka("9: кадр без такой оценки — строка остаётся", "r9:31:pusto" in S)
    # 12в без свежей ночной проверки расписания не показываем
    db.set_state("proverka_mk_raspisanie", "{}")
    S, res = stroki()
    proverka("12в: ночной проверки расписания не было — строк нет (край выгрузки — не конец занятий)",
             any(k.startswith("r12v") for k in S), False)
    # выключенные разделы: по умолчанию 5 и 11г
    with db.get_conn() as conn:
        conn.execute("DELETE FROM settings WHERE key='proverka_mk_vyklyucheny'")
    S, res = stroki()
    proverka("по умолчанию выключены 5 и 11г", pm.vyklyucheny(), {"5", "11г"})
    proverka("выключенные: строк разделов 5 и 11г нет",
             (any(x["razdel"] == "5" for x in S.values()), any(x["pod"] == "11г" for x in S.values())), (False, False))
    proverka("выключенные: раздела 5 нет в списке разделов, 11 — есть",
             ("5" in [rz["nomer"] for rz in res["razdely"]], "11" in [rz["nomer"] for rz in res["razdely"]]), (False, True))
    proverka("выключенные: в «осталось» не считаются", res["itog"]["ostalos"],
             len([x for x in S.values() if not x["seraya"] and not x["ne_oshibka"]]))
    with db.get_conn() as conn:
        z = conn.execute("SELECT COUNT(*) FROM proverka_mk_stroki WHERE zakryta IS NULL AND (key LIKE 'r5:%' OR key LIKE 'r11g:%')").fetchone()[0]
    proverka("выключенные: открытых строк в таблице не остаётся", z, 0)
    proverka("в данных страницы видно, какие разделы выключены, журнал пометок удалённого и ночной шаг",
             (res["vyklyucheny"], isinstance(res["udaleno_zhurnal"], list), "noch" in res), (["11г", "5"], True, True))
    # пока раздел 5 выключен, пункты о банке и возвратах идут Лизе в пульт, как раньше
    from app import tbank, komtet
    st = (tbank.statement, komtet.cheki)
    try:
        tbank.statement = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("банк недоступен (тест)"))
        komtet.cheki = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("КОМТЕТ недоступен (тест)"))
        db.set_setting("proverka_mk_vmesto_punktov", "1")
        R = kontrol_oplat.run("2026-11-03", dry=True)
        proverka("раздел 5 выключен: возврат не тем типом — пунктом Лизе, не «на страницу»",
                 (any(w == "Лиза" and "возврат записан типом" in t for w, t, _ in R["inbox"]),
                  any("возврат записан типом" in t for t in R["na_stranicu"])), (True, False))
        db.set_setting("proverka_mk_vyklyucheny", "11г")
        R = kontrol_oplat.run("2026-11-03", dry=True)
        proverka("раздел 5 включён настройкой: возврат — на страницу, пункта нет",
                 (any("возврат записан типом" in t for w, t, _ in R["inbox"]),
                  any("возврат записан типом" in t for t in R["na_stranicu"])), (False, True))
    finally:
        tbank.statement, komtet.cheki = st
        with db.get_conn() as conn:
            conn.execute("DELETE FROM settings WHERE key='proverka_mk_vyklyucheny'")
    # ночной шаг: в 22 часа — всегда; иначе догон, если не проходил больше 26 часов
    db.set_state("proverka_mk_noch", "{}")
    proverka("ночной шаг ни разу не проходил — нужен догон в любой час", pm.noch_nuzhen(CHASY))
    db.set_state("proverka_mk_noch", json.dumps({"ts": (CHASY - timedelta(hours=13)).isoformat(timespec="minutes")}))
    proverka("ночной шаг был 13 часов назад — днём не запускаем", pm.noch_nuzhen(CHASY), False)
    proverka("в 22 часа ночной шаг нужен всегда", pm.noch_nuzhen(CHASY.replace(hour=22)))
    db.set_state("proverka_mk_noch", json.dumps({"ts": (CHASY - timedelta(hours=40)).isoformat(timespec="minutes")}))
    proverka("ночной шаг был 40 часов назад — догон", pm.noch_nuzhen(CHASY))
    marki = []
    st_mark = ap._mark
    try:
        ap._mark = lambda kind, key: marki.append((kind, key)) or True
        proverka("автопилот: догон ночного шага помечается раз в 3 часа, а не раз в сутки",
                 (ap._proverka_mk_noch_pora(CHASY), marki), (True, [("proverka_mk_noch", f"{ap._today()}:dogon:3")]))
    finally:
        ap._mark = st_mark


# ── 10. М3 (13.11.2026): красные находки «Аномалий» — раздел 13; жёлтые в ленту не пишутся ──

def test_anomalii():
    print("М3: лента «Аномалии» — красные разделом 13 на странице, остальное выключено настройкой")
    from app import audit, rules, crmcheck
    import app.main as m

    def v_lente(usl="1=1"):
        with db.get_conn() as conn:
            audit._ensure(conn)
            return conn.execute(f"SELECT COUNT(*) FROM audit_flags WHERE {usl}").fetchone()[0]

    def vruchnuyu(kind, key, level, title, detail, uid, sozdano, status="new"):
        with db.get_conn() as conn:
            audit._ensure(conn)
            conn.execute("INSERT INTO audit_flags (kind, key, level, title, detail, user_id, day, status, created_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?)", (kind, key, level, title, detail, uid, sozdano[:10], status, sozdano))

    ostalos_do = pm.sobrat(CHASY)["itog"]["ostalos"]
    # 1. выключатель: по умолчанию в ленту пишутся только красные
    proverka("настройка anomalii_ne_krasnye меняется через /api/settings (откат без выкладки)",
             "anomalii_ne_krasnye" in m.SETTABLE)
    proverka("по умолчанию настройка не задана — «выключено»", (db.get_setting("anomalii_ne_krasnye"), audit.ne_krasnye_pishem()),
             ("", False))
    bylo = v_lente()
    proverka("жёлтая находка не пишется", audit.add_flag("деньги", "test:mid", audit.MID, "Долг клиента 5 000 ₽",
                                                          "Остаткова Нина · баланс −5 000 ₽", user_id=9005), False)
    proverka("серая — тоже", audit.add_flag("правила", "test:low", audit.LOW, "Отработка задним числом", "Тестова Ева"), False)
    proverka("красная пишется", audit.add_flag("деньги", "neg-balance:9004:2026-11", audit.HIGH, "Долг клиента 25 000 ₽",
                                               "Образцова Мила · баланс −25 000 ₽. Разобрать.", user_id=9004,
                                               day="2026-11-12"), True)
    proverka("повтор красной с тем же ключом — не вторая запись",
             audit.add_flag("деньги", "neg-balance:9004:2026-11", audit.HIGH, "Долг клиента 25 000 ₽", "", user_id=9004), False)
    proverka("в ленте прибавилась ровно одна запись — красная", (v_lente() - bylo, v_lente("level != 'high'")), (1, 0))
    # сами проверки не тронуты: считают и возвращают то же, пишут только красное
    st_subs = rules._subs
    try:
        rules._subs = lambda since=None: [
            {"id": 801, "userId": 9001, "price": 8000, "payed": 0, "visitedCount": 4, "statusId": 2, "sellDate": "2026-11-03", "managerId": 154181},
            {"id": 802, "userId": 9002, "price": 8000, "payed": 6000, "visitedCount": 1, "statusId": 2, "sellDate": "2026-11-03", "managerId": 154181}]
        nashli = rules.unpaid_subscriptions("2026-09-01")
    finally:
        rules._subs = st_subs
    proverka("проверка денег находит оба неоплаченных абонемента, как раньше", sorted(x["id"] for x in nashli), [801, 802])
    proverka("…а в ленту идёт только красный (долг от 10 000 ₽ или 3 занятия)",
             (v_lente("key='sub-unpaid:801' AND level='high'"), v_lente("key='sub-unpaid:802'")), (1, 0))
    proverka("код проверок на месте: аудит дня, правила, деньги, состав групп",
             all(callable(f) for f in (audit.daily_audit, audit.unsigned_payments, audit.off_price, audit.ghost_paying_clients,
                                       rules.check, rules.money_check, crmcheck.fit_check, ap.audit_yesterday,
                                       ap.rules_check, ap.money_check)))
    db.set_setting("anomalii_ne_krasnye", "1")
    try:
        proverka("настройка «1»: жёлтая и серая пишутся, как до 13.11",
                 (audit.add_flag("деньги", "test:mid", audit.MID, "Долг клиента 5 000 ₽", "Остаткова Нина", user_id=9005),
                  audit.add_flag("правила", "test:low", audit.LOW, "Отработка задним числом", "Тестова Ева")), (True, True))
    finally:
        db.set_setting("anomalii_ne_krasnye", "0")
    proverka("настройка «0»: снова только красные", audit.add_flag("скидка", "test:mid2", audit.MID, "Оплата ниже обычной"), False)

    # 2. раздел 13: красные за 14 дней (30.10–12.11), сняты «не нарушение» и старые — нет
    tekst = "Тестова Ева: оплачено уменьшено 9450 → 8400; цена оплаченного абонемента снижена (замечено 11.11)"
    audit.add_flag("честность", "chest:user_subscriptions:801:правка: user", audit.HIGH, tekst[:120], tekst, user_id=9001, day="2026-11-11")
    audit.add_flag("честность", "chest:invoices:901:правка: invoi", audit.HIGH, "Тестова Ева: оплачено по счёту уменьшено 9450 → 8400",
                   "Тестова Ева: оплачено по счёту уменьшено 9450 → 8400", user_id=9001, day="2026-11-11")
    vruchnuyu("правила", "disc-noreason:803", "high", "Скидка 35% без объяснения", "Тестов Марк · 5 200 ₽ вместо 8 000 ₽",
              9002, "2026-10-30T09:31:00+03:00")                                   # первый день окна
    vruchnuyu("деньги", "neg-balance:9016:2026-10", "high", "Долг клиента 21 000 ₽", "Минусов Лев", 9016,
              "2026-10-29T09:41:00+03:00")                                         # на день старше окна
    vruchnuyu("возврат", "ref901", "high", "Возврат 4 000 ₽", "Возвратова Ия · без причины", 9025,
              "2026-11-11T09:01:00+03:00", status="ok")                            # владелец снял: «не нарушение»
    vruchnuyu("оплата без подписи", "pay902", "high", "Оплата 7 000 ₽ без менеджера", "Пробная Алиса", 9003,
              "2026-11-10T09:01:00+03:00", status="violation")                     # владелец подтвердил
    vruchnuyu("группы", "fit:9007:2627_ПШ", "high", "Удалёнов Ян: 3 лет в группе 5–7", "ребёнок младше вилки группы", 9007,
              "2026-11-09T09:51:00+03:00")                                         # карточка удалена в МойКлассе
    vruchnuyu("речь", "звонок 1:без чека", "high", "оплата без чека", "звонок 1 · «…без чека…»", None,
              "2026-11-08T12:00:00+03:00")                                         # без карточки
    lenta_do = (v_lente(), v_lente("status='new'"))
    V = pm.krasnye(vladelec=True, seychas=CHASY)
    A = pm.krasnye(vladelec=False, seychas=CHASY)
    proverka("владельцу: красных за 14 дней 8 — по видам",
             (V["vsego"], V["po_vidam"], V["s_dnya"], V["dney"]),
             (8, {"деньги": 2, "честность": 2, "правила": 1, "оплата без подписи": 1, "группы": 1, "речь": 1}, "2026-10-30", 14))
    vidy = lambda R: sorted({g["vid"] for g in R["gruppy"]})                       # noqa: E731
    proverka("админу: только то, что админские страницы показывают и так, — деньги, правила, группы",
             (A["vsego"], vidy(A)), (4, ["группы", "деньги", "правила"]))
    vse_a = json.dumps(A, ensure_ascii=False)
    proverka("админу не попадают сверка честности, оплаты без подписи, стоп-фразы (отчёты о самих админах)",
             any(x in vse_a for x in ("оплачено уменьшено", "без менеджера", "без чека", "честность")), False)
    kl = {(g["vid"], g["uid"]): g for g in V["gruppy"]}
    proverka("две находки сверки честности об одном ребёнке за день — одна строка с двумя пунктами",
             (len(kl[("честность", 9001)]["punkty"]), len(V["gruppy"])), (2, 7))
    proverka("название сверки честности — обрезанный текст: показываем текст целиком, без повтора",
             (kl[("честность", 9001)]["punkty"][-1]["chto"], kl[("честность", 9001)]["punkty"][-1]["podrobno"]), (tekst, ""))
    proverka("ссылка на карточку МойКласса и имя ребёнка",
             (kl[("деньги", 9004)]["ssylka"], kl[("деньги", 9004)]["rebenok"], kl[("деньги", 9004)]["naydeno"]),
             ("https://app.moyklass.com/user/9004", "Образцова Мила", "2026-11-12"))
    proverka("находка на первый день окна (30.10) — есть, на день старше (29.10) — нет",
             (("правила", 9002) in kl, ("деньги", 9016) in kl), (True, False))
    proverka("снятая владельцем («не нарушение») — нет; подтверждённая — есть, с пометкой",
             (("возврат", 9025) in kl, kl[("оплата без подписи", 9003)]["podtverzhdeno"]), (False, True))
    proverka("карточка удалена в МойКлассе — строка без ссылки, с пометкой",
             (kl[("группы", 9007)]["ssylka"], kl[("группы", 9007)]["ssylka_tekst"]), ("", "карточка удалена в МойКлассе"))
    proverka("находка без карточки — отдельной строкой, без ссылки",
             [(g["rebenok"], g["ssylka"]) for g in V["gruppy"] if g["vid"] == "речь"], [("", "")])
    proverka("жёлтые и серые в раздел не попадают", any(g["vid"] == "скидка" for g in V["gruppy"]), False)
    proverka("раздел только читает: лента после сборки та же", (v_lente(), v_lente("status='new'")), lenta_do)

    # 3. в «осталось N» и в строки страницы раздел не входит
    res = pm.sobrat(CHASY)
    with db.get_conn() as conn:
        v_strokah = conn.execute("SELECT COUNT(*) FROM proverka_mk_stroki WHERE razdel LIKE '13%' OR key LIKE '%audit%'").fetchone()[0]
    proverka("тринадцатого раздела среди строк страницы нет: в «осталось N» и в proverka_mk_stroki он не входит",
             ("13" in [rz["nomer"] for rz in res["razdely"]], [rz["nomer"] for rz in res["razdely"]][-1],
              res["itog"]["ostalos"] == ostalos_do, v_strokah), (False, "12", True, 0))
    proverka("строка вечернего саммари Бориса не изменилась по форме",
             pm.stroka_dlya_borisa(CHASY).startswith("Проверка МК: осталось "))

    # 4. страница и API (режим без входа — роль владельца)
    from fastapi.testclient import TestClient
    c = TestClient(m.app)
    h = c.get("/proverka-mk").text
    r13 = h.split('id="r13"', 1)[-1].split("</section>", 1)[0] if 'id="r13"' in h else ""
    proverka("на странице раздел 13 «Красные находки „Аномалий“ за 14 дней», помечен «только чтение»",
             "Красные находки «Аномалий» за 14 дней" in r13 and "только чтение" in r13 and ">8 · только чтение<" in r13)
    proverka("в разделе ссылка на карточку МойКласса, текст находки и пометка «подтверждено владельцем»",
             'href="https://app.moyklass.com/user/9004"' in r13 and "Долг клиента 25 000 ₽" in r13
             and "подтверждено владельцем" in r13)
    proverka("в разделе нет ни кнопок, ни галочек — только чтение",
             any(x in r13 for x in ("<button", "onclick", "checkbox", "<form", "<input")), False)
    proverka("в сводке Бориса — строка раздела 13 с числом и разбивкой по видам, ссылка на «Аномалии»",
             "13. Красные находки «Аномалий» за 14 дней" in h and "честность 2" in h and 'href="/audit"' in h)
    d = c.get("/api/proverka-mk").json()
    k = pm.dannye()                                    # то, что отдаёт страница (кэш последней сборки)
    proverka("API: разделы и итог — те же, что без красных; красные — отдельным ключом krasnye",
             ([rz["nomer"] for rz in d["razdely"]] == [rz["nomer"] for rz in k["razdely"]], d["itog"]["ostalos"] == k["itog"]["ostalos"],
              "13" in [rz["nomer"] for rz in d["razdely"]], d["krasnye"]["vsego"]), (True, True, False, 8))
    proverka("API kratko=1 — как раньше, без красных", "krasnye" in c.get("/api/proverka-mk?kratko=1").json(), False)
    proverka("страница «Аномалии» на месте", c.get("/audit").status_code, 200)
    # лента сломана — страница жива
    st_conn = db.get_conn
    try:
        def padaet():
            raise sqlite3.OperationalError("база занята (тест)")
        db.get_conn = padaet
        R = pm.krasnye(vladelec=True, seychas=CHASY)
    finally:
        db.get_conn = st_conn
    proverka("лента не прочиталась — раздел пустой с пометкой, без падения", (R["vsego"], bool(R["oshibka"])), (0, True))


def main():
    for t in (test_razdely, test_zhizn, test_ne_oshibka, test_sredniy, test_pult, test_nastroyka, test_http, test_sverka_1211,
              test_skeptik2, test_anomalii):
        t()
        print()
    if oshibok:
        print(f"ПРОВАЛЕНО проверок: {oshibok}")
        return 1
    print("Все проверки прошли.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
