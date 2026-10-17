# -*- coding: utf-8 -*-
"""Проверки правила «звонить ли семье» (app/zvonok_filtr.py) без сервера.

17.10.2026, Ира: «большинство детей в задачах уже с комментарием и чётким
ответом… мы выглядим надоедами». Здесь — случаи из обзвона того дня и из
проверки правки (тексты обезличены): и те, где звонить нельзя, и те, где
фильтр не должен прятать заинтересованную семью.

  python3 docs/rabota/zvonok_filtr_testy.py
"""
import sqlite3
import sys
from datetime import date

sys.path.insert(0, "/home/user/kidsup")

from app import zvonok_filtr as zf  # noqa: E402

ADMIN, ADMIN2 = 232763, 232805       # админы из voronka.MANAGERS
DEN = date(2026, 10, 17)
oshibok = 0


def proverka(nazvanie, poluchili, zhdali):
    global oshibok
    ok = poluchili == zhdali
    oshibok += not ok
    print(("  OK   " if ok else "  ОШИБКА "), nazvanie,
          "" if ok else f"-> {poluchili!r}, ждали {zhdali!r}")


def stop(*kom, zvonki=(), den=DEN):
    """Есть ли запрет по комментариям одной карточки. kom: (ts_utc, текст[, manager_id])."""
    rows = [(k[0], k[2] if len(k) > 2 else ADMIN, k[1]) for k in kom]
    return bool(zf.otvet({1: rows}, [1], list(zvonki), den))


def test_ne_zvonit():
    print("Ответ в карточке — не звонить")
    for t in ("Подумаю, свяжусь сама", "далеко живут", "Далеко ездить", "Выбрали другой центр",
              "не актуально", "Не беспокоить", "Вежливо закрыла чат. Завершила общение",
              "мама 13.10: «вернусь к вам сама» после выписки", "попозже сама обращусь, рассматриваю вас",
              "Определились с детским центром", "Им не интересно"):
        proverka(f"«{t}»", stop(("2026-10-10T09:00:00", t)), True)
    proverka("разбор «свяжусь сама», потом «но» — всё равно не звонить",
             stop(("2026-09-06T10:15:00", "📞 06.09 12:28, исходящий, 29 с. «Подумаю, свяжусь сама».", ADMIN2),
                  ("2026-10-11T08:21:00", "но", ADMIN2)), True)


def test_zvonit_mozhno():
    print("Заинтересованные семьи фильтр не прячет")
    for t in ("Мама сама позвонила, спрашивала про английский, думает до выходных",
              "Сама обратилась по рекламе, интересуется ПШ",
              "Сами вернулись после лета, хотят на английский",
              "Просили перезвонить ближе к вечеру", "Перезвонить ближе к 18:00",
              "просили набрать ближе к октябрю", "Ищут ближе к дому",
              "Не хотела бы субботу, удобнее будни после 17",
              "Пока не хотят на английский, интересна робототехника",
              "Алине интересна гимнастика, мама думает", "Полине хотят подобрать группу 4-5 лет",
              "Татьяне звонить после 19, днём на работе", "Просила не звонить до 12, удобно после обеда",
              "Живут не далеко, 10 минут пешком", "живут недалеко", "к 18 часам позвоню, договорились",
              "Выбрали другое время — вт/чт 17:00, ждут места", "Ходит в другой сад до 17, нужно после 18",
              "Записала на пробное 20.10, закрыла чат", "Сейчас рано утром неудобно, перезвонить вечером",
              "мы сами позвоним, как освободится место", "не надумали"):
        proverka(f"«{t}»", stop(("2026-10-10T09:00:00", t)), False)


def test_otmetki_i_avtomatika():
    print("Отметки о недозвоне и служебные комментарии — не ответ семьи")
    S = ("2026-09-06T10:15:00", "Подумаю, свяжусь сама")
    for t, mid in (("но 11.10", ADMIN), ("н.о.", ADMIN), ("не берет", ADMIN), ("нет ответа (2)", ADMIN),
                   ("Не дозвонилась, написала в ватсап", ADMIN), ("📞 Прозвон (app.kidsup.ru): не дозвонились", ADMIN),
                   ("11.10 11:21, обзвон (Ира): не дозвонились.", ADMIN),
                   ("12.10 11:21 Клод: «думает» — напомнили о себе (мессенджер). Предложили: ПШ Гр9.", ADMIN),
                   ("🎯 ПОДСКАЗКА ДЛЯ ЗВОНКА (новый лид, сформирована автоматически)", ADMIN),
                   ("Ответ в переписке (цены): клиент спросил «а это далеко от метро?». Отправлено: …", ADMIN)):
        proverka(f"после «свяжусь сама» — «{t[:40]}» не снимает запрет",
                 stop(S, ("2026-10-12T08:21:00", t, mid)), True)
    proverka("служебное «далеко» в цитате клиента запрета не ставит",
             stop(("2026-10-12T08:21:00", "Ответ в переписке (адрес): клиент спросил «а это далеко от метро?»")), False)
    proverka("в разборе наша фраза из расшифровки не в счёт",
             stop(("2026-10-12T08:21:00", "📞 12.10 11:20, исходящий, 2 мин 5 с. Договорились на пробное.\n\n"
                                          "Расшифровка: вы не хотели бы прийти? Сейчас рано, давайте после каникул?")), False)
    proverka("итог обзвона «думают, перезвонить» — звонить можно",
             stop(S, ("2026-10-12T08:21:00", "12.10 11:21, обзвон (Ира): думают, перезвонить.")), False)


def test_pauza():
    print("Отсрочки истекают")
    t = ("2026-10-02T06:57:00", "После осенних каникул вернутся к вопросу над англ. языком")
    proverka("«после каникул» — 17.10 пауза", stop(t), True)
    proverka("«после каникул» — через 45 дней звоним", stop(t, den=date(2026, 11, 17)), False)
    t2 = ("2026-10-15T09:00:00", "Хотят начать после осенних каникул, перезвонить 02.11")
    proverka("названа дата «перезвонить 02.11» — до неё пауза", stop(t2, den=date(2026, 11, 1)), True)
    proverka("с 02.11 — звоним", stop(t2, den=date(2026, 11, 2)), False)
    proverka("«сейчас рано» — пауза", stop(("2026-09-15T17:13:00", "📞 15.09 19:36, исходящий, 53 с. Мама: «сейчас рано».")), True)


def test_obyavilis():
    print("Семья объявилась — запрет снят")
    d = ("2026-08-24T12:00:00", "далеко живут", ADMIN2)
    proverka("«далеко», потом их входящий звонок 10.10", stop(d, zvonki=[("2026-10-10T11:00:00+03:00", "in", "missed")]), False)
    proverka("«далеко», потом наш разговор 30 с", stop(d, zvonki=[("2026-10-10T11:00:00+03:00", "out", "talked")]), False)
    proverka("«далеко», потом наш недозвон — запрет остаётся", stop(d, zvonki=[("2026-10-10T11:00:00+03:00", "out", "missed")]), True)
    proverka("сам разговор, после которого написали «выбрали другой центр», запрет не снимает",
             stop(("2026-10-16T07:54:00", "Выбрали другой центр"), zvonki=[("2026-10-16T10:53:00+03:00", "out", "talked")]), True)


def test_semya():
    print("Семья на одном номере")
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE crm_comments (id INTEGER PRIMARY KEY, user_id INTEGER, ts TEXT, manager_id INTEGER, text TEXT);
        CREATE TABLE mango_calls (id INTEGER PRIMARY KEY, ts TEXT, phone TEXT, direction TEXT, state TEXT);
        CREATE TABLE wazzup_outbox (id INTEGER PRIMARY KEY, ts TEXT, phone TEXT);
        CREATE TABLE wazzup_inbox (id INTEGER PRIMARY KEY, ts TEXT, phone TEXT);
        CREATE TABLE users (id INTEGER PRIMARY KEY, phone TEXT);
        INSERT INTO users (id, phone) VALUES (21, '79990000021'), (22, '79990000021');
    """)
    c.execute("INSERT INTO crm_comments (user_id, ts, manager_id, text) VALUES (22, '2026-10-17T08:37:00', ?, ?)",
              (ADMIN, "Ходит только старший; младшую водить в другую группу неудобно — не актуально"))
    proverka("ответ в карточке брата — звонок по сестре не ставим", bool(zf.ne_zvonit(c, [21], "79990000021")), True)
    p = "79990000031"
    for ts in ("2026-09-24T12:00:00+03:00", "2026-10-04T10:32:00+03:00", "2026-10-06T11:02:00+03:00"):
        c.execute("INSERT INTO mango_calls (ts, phone, direction, state) VALUES (?, ?, 'out', 'missed')", (ts, p))
    c.execute("INSERT INTO mango_calls (ts, phone, direction, state) VALUES ('2026-10-06T11:02:05+03:00', ?, 'out', 'appeared')", (p,))
    proverka("три звонка без сообщения — ещё можно", zf.ne_zvonit(c, [], p), "")
    c.execute("INSERT INTO wazzup_outbox (ts, phone) VALUES ('2026-10-04T11:04:00+03:00', ?)", (p,))
    proverka("три звонка и наше сообщение — не звонить", bool(zf.ne_zvonit(c, [], p)), True)


def test_popytki():
    print("Попытки дозвона")
    z = [("2026-09-24T12:00:00+03:00", "out", "missed"), ("2026-10-04T10:32:00+03:00", "out", "short"),
         ("2026-10-06T11:02:00+03:00", "out", "missed"), ("2026-10-06T11:02:30+03:00", "out", "missed")]
    proverka("три звонка (повтор строки в ту же минуту не в счёт)", zf.popytki(z), (3, False))
    proverka("наше сообщение после первой попытки", zf.popytki(z, nashe_soobshchenie="2026-09-24T12:05:00+03:00"), (3, True))
    proverka("их пропущенный входящий после попыток — счёт заново",
             zf.popytki(z + [("2026-10-07T09:00:00+03:00", "in", "missed")]), (0, False))
    proverka("их сообщение после попыток — счёт заново", zf.popytki(z, vh_soobshchenie="2026-10-07T09:00:00+03:00"), (0, False))
    proverka("попытки до разговора не считаются",
             zf.popytki([("2026-09-01T10:00:00+03:00", "out", "missed"), ("2026-09-05T10:00:00+03:00", "in", "talked"),
                         ("2026-10-01T10:00:00+03:00", "out", "missed")]), (1, False))


def test_nedavno():
    print("Разговор в последний час")
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE mango_calls (id INTEGER PRIMARY KEY, ts TEXT, phone TEXT, direction TEXT, state TEXT)")
    c.execute("INSERT INTO mango_calls (ts, phone, direction, state) VALUES ('2026-10-17T11:45:12+03:00', '79990000041', 'in', 'talked')")
    c.execute("INSERT INTO mango_calls (ts, phone, direction, state) VALUES ('2026-10-17 11:50:00', '79990000042', 'in', 'connected')")
    proverka("говорили 15 минут назад", zf.nedavno_govorili(c, "79990000041", "2026-10-17T11:00:00"), True)
    proverka("разговор раньше часа — не в счёт", zf.nedavno_govorili(c, "79990000041", "2026-10-17T12:50:00"), False)
    proverka("«connected» от вебхука (может быть голосовое меню) — не разговор",
             zf.nedavno_govorili(c, "79990000042", "2026-10-17T11:00:00"), False)


test_ne_zvonit()
test_zvonit_mozhno()
test_otmetki_i_avtomatika()
test_pauza()
test_obyavilis()
test_semya()
test_popytki()
test_nedavno()
print("\nошибок:", oshibok)
sys.exit(1 if oshibok else 0)
