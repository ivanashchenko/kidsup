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
    # длинный горизонт расписания у другой группы → 101 «кончается рано» (12в, запасное правило)
    urok(20, 103, "2027-01-12", t="18:00", konec="18:50", status=0)
    urok(21, 101, "2026-11-19", status=0)
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
    proverka("12в: расписание группы кончается рано — строка", "r12v:101" in S)
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
        yavka.nightly("2026-11-11")
        proverka("явка «1»: расхождения пунктом не перечисляем", punkty, [])
        it["bez_otcheta"] = 1
        yavka.nightly("2026-11-11")
        proverka("явка «1»: про занятия без отчёта педагога — пункт остаётся",
                 len(punkty) == 1 and "без отчёта педагога" in punkty[0][1] and "расхождений" not in punkty[0][1])
        db.set_setting("proverka_mk_vmesto_punktov", "0")
        punkty.clear()
        yavka.nightly("2026-11-11")
        proverka("явка «0»: прежний пункт с расхождениями", len(punkty) == 1 and "расхождений с CRM 2" in punkty[0][1])
        # качество карточек
        class Mk:
            def fetch_all(self, *a, **k):
                return [{"id": 9101, "name": "лид без имени", "phone": "", "attributes": []}]

            def close(self):
                pass
        zadachi = []
        ap._client = lambda: Mk()
        ap._task = lambda mk, who, uid, body, day=None: zadachi.append(body)
        ap._admins_today = lambda: [{"managerId": 1, "name": "Тест"}]
        db.set_setting("proverka_mk_vmesto_punktov", "1")
        ap.card_quality()
        proverka("качество карточек «1»: пункта нет (раздел 11д)", zadachi, [])
        db.set_setting("proverka_mk_vmesto_punktov", "0")
        ap.card_quality()
        proverka("качество карточек «0»: пункт как раньше", len(zadachi), 1)
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
    r = c.post("/api/proverka-mk/zametka", json={"key": "r6:9004", "text": "из пункта №6159: маткапитал 10.11"})
    proverka("заметка к строке сохраняется", r.status_code == 200
             and any(x.get("zametka") for rz in c.get("/api/proverka-mk").json()["razdely"] for x in rz["stroki"]
                     if x["key"] == "r6:9004"))


def main():
    for t in (test_razdely, test_zhizn, test_ne_oshibka, test_sredniy, test_pult, test_nastroyka, test_http):
        t()
        print()
    if oshibok:
        print(f"ПРОВАЛЕНО проверок: {oshibok}")
        return 1
    print("Все проверки прошли.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
