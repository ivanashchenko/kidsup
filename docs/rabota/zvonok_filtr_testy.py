# -*- coding: utf-8 -*-
"""Проверки правила «звонить ли семье» (app/zvonok_filtr.py) без сервера.

17.10.2026, Ира: «большинство детей в задачах уже с комментарием и чётким
ответом… мы выглядим надоедами». Здесь — случаи из обзвона того дня (тексты
обезличены), чтобы следующая правка очередей звонков ломала тест, а не смену.

  python3 docs/rabota/zvonok_filtr_testy.py
"""
import sqlite3
import sys

sys.path.insert(0, "/home/user/kidsup")

from app import zvonok_filtr as zf  # noqa: E402

ADMIN, ADMIN2 = 232763, 232805       # админы из voronka.MANAGERS
oshibok = 0


def proverka(nazvanie, poluchili, zhdali):
    global oshibok
    ok = poluchili == zhdali
    oshibok += not ok
    print(("  OK   " if ok else "  ОШИБКА "), nazvanie,
          "" if ok else f"-> {poluchili!r}, ждали {zhdali!r}")


def baza():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE crm_comments (id INTEGER PRIMARY KEY, user_id INTEGER, ts TEXT, manager_id INTEGER, text TEXT);
        CREATE TABLE mango_calls (id INTEGER PRIMARY KEY, ts TEXT, phone TEXT, direction TEXT, state TEXT);
        CREATE TABLE wazzup_outbox (id INTEGER PRIMARY KEY, ts TEXT, phone TEXT);
        CREATE TABLE wazzup_inbox (id INTEGER PRIMARY KEY, ts TEXT, phone TEXT);
        CREATE TABLE users (id INTEGER PRIMARY KEY, phone TEXT);
    """)
    return c


def kom(c, uid, ts, text, mid=ADMIN):
    c.execute("INSERT INTO crm_comments (user_id, ts, manager_id, text) VALUES (?,?,?,?)", (uid, ts, mid, text))


def zv(c, ts, phone, direction, state):
    c.execute("INSERT INTO mango_calls (ts, phone, direction, state) VALUES (?,?,?,?)", (ts, phone, direction, state))


def test_otvet():
    print("Ответ в карточке")
    c = baza()
    kom(c, 1, "2026-09-06T10:15:00", "📞 06.09 12:28, исходящий, 29 с. «Подумаю, свяжусь сама». Не звонить, касание рассылкой.")
    kom(c, 1, "2026-10-11T08:21:00", "но", ADMIN2)
    proverka("разбор «свяжусь сама», потом отметка «но» — не звонить",
             bool(zf.otvet_v_kartochke(c, [1])), True)
    kom(c, 2, "2026-08-24T12:00:00", "далеко живут", ADMIN2)
    proverka("«далеко живут» — не звонить", bool(zf.otvet_v_kartochke(c, [2])), True)
    kom(c, 3, "2026-10-01T09:00:00", "живут недалеко, думают про английский")
    proverka("«недалеко» — звонить можно", zf.otvet_v_kartochke(c, [3]), "")
    kom(c, 4, "2026-10-16T07:54:00", "Выбрали другой центр")
    proverka("«выбрали другой центр» — не звонить", bool(zf.otvet_v_kartochke(c, [4])), True)
    kom(c, 5, "2026-10-02T06:57:00", "После осенних каникул вернутся к вопросу над англ. языком")
    proverka("«после осенних каникул» — не звонить", bool(zf.otvet_v_kartochke(c, [5])), True)
    kom(c, 6, "2026-09-10T10:00:00", "далеко ездить")
    kom(c, 6, "2026-10-12T10:00:00", "Мама передумала, записались на пробное в пн")
    proverka("свежий содержательный комментарий перекрывает старый «далеко»", zf.otvet_v_kartochke(c, [6]), "")
    kom(c, 7, "2026-09-20T10:00:00", "Сказали не актуально")
    kom(c, 7, "2026-10-14T10:00:00", "🤖 Клод: напомнили о себе сообщением")
    proverka("служебный комментарий автоматики — не ответ, решает «не актуально»",
             bool(zf.otvet_v_kartochke(c, [7])), True)
    kom(c, 8, "2026-08-19T08:22:00", "Вежливо закрыла чат, пригласила на праздник. Завершила общение")
    proverka("«завершила общение» — не звонить", bool(zf.otvet_v_kartochke(c, [8])), True)
    kom(c, 9, "2026-09-15T17:13:00", "📞 15.09 19:36, исходящий, 53 с. Мама: «я на будущее рассматривала, сейчас рано».")
    proverka("«сейчас рано» в разборе — не звонить", bool(zf.otvet_v_kartochke(c, [9])), True)
    kom(c, 10, "2026-10-11T10:45:00", "не надумали", ADMIN2)
    proverka("«не надумали» — это не отказ, звонить можно в срок", zf.otvet_v_kartochke(c, [10]), "")
    kom(c, 11, "2026-10-03T12:11:00", "но", ADMIN2)
    proverka("только «но» — ответа нет, звонить можно", zf.otvet_v_kartochke(c, [11]), "")


def test_semya():
    print("Семья на одном номере")
    c = baza()
    c.execute("INSERT INTO users (id, phone) VALUES (21, '79990000021'), (22, '79990000021')")
    kom(c, 22, "2026-10-17T08:37:00", "Ходит только старший, младшая нет — другие занятия, водить в разные группы неудобно. Не хотят")
    proverka("ответ в карточке брата — звонок по сестре не ставим",
             bool(zf.ne_zvonit(c, [21], "79990000021")), True)


def test_popytki():
    print("Попытки дозвона")
    c = baza()
    p = "79990000031"
    for ts in ("2026-09-24T12:00:00+03:00", "2026-10-04T10:32:00+03:00", "2026-10-06T11:02:00+03:00"):
        zv(c, ts, p, "out", "missed")
    zv(c, "2026-10-06T11:02:30+03:00", p, "out", "missed")          # та же минута — тот же звонок
    zv(c, "2026-10-06T11:02:05+03:00", p, "out", "appeared")        # событие вебхука — не попытка
    proverka("три звонка — три попытки (вебхук и повтор строки не в счёт)",
             zf.popytki_bez_otveta(c, p), (3, False))
    proverka("три звонка без сообщения — ещё можно (сначала написать)", zf.hvatit_zvonit(c, p), "")
    c.execute("INSERT INTO wazzup_outbox (ts, phone) VALUES ('2026-10-04T11:04:00+03:00', ?)", (p,))
    proverka("три звонка и наше сообщение — не звонить", bool(zf.hvatit_zvonit(c, p)), True)

    q = "79990000032"
    zv(c, "2026-09-01T10:00:00+03:00", q, "out", "missed")
    zv(c, "2026-09-02T10:00:00+03:00", q, "out", "short")
    zv(c, "2026-09-05T10:00:00+03:00", q, "in", "talked")
    zv(c, "2026-10-01T10:00:00+03:00", q, "out", "missed")
    proverka("попытки до разговора не считаются", zf.popytki_bez_otveta(c, q), (1, False))
    r = "79990000033"
    for ts in ("2026-09-01T10:00:00", "2026-09-03T18:00:00", "2026-09-06T12:00:00"):
        zv(c, ts, r, "out", "missed")
    c.execute("INSERT INTO wazzup_inbox (ts, phone) VALUES ('2026-09-07T09:00:00', ?)", (r,))
    proverka("семья написала сама — попытки обнуляются", zf.popytki_bez_otveta(c, r), (0, False))


def test_nedavno():
    print("Разговор в последний час")
    c = baza()
    p = "79990000041"
    zv(c, "2026-10-17T11:45:12+03:00", p, "in", "talked")
    proverka("говорили 15 минут назад", zf.nedavno_govorili(c, p, "2026-10-17T11:00:00"), True)
    proverka("разговор раньше часа — не в счёт", zf.nedavno_govorili(c, p, "2026-10-17T12:50:00"), False)
    q = "79990000042"
    zv(c, "2026-10-17 11:50:00", q, "in", "connected")
    proverka("опрос ещё не дошёл — хватает события вебхука", zf.nedavno_govorili(c, q, "2026-10-17T11:00:00"), True)


test_otvet()
test_semya()
test_popytki()
test_nedavno()
print("\nошибок:", oshibok)
sys.exit(1 if oshibok else 0)
