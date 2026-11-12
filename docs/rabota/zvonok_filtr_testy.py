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
              "мы сами позвоним, как освободится место", "не надумали",
              "Спрашивала, далеко ли от метро", "Ребёнку не интересна робототехника, хотят английский"):
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
    proverka("«Но придут в среду» — это не отметка «но», после «свяжусь сама» звонить можно",
             stop(S, ("2026-10-12T08:21:00", "Но придут в среду, мама подтвердила")), False)
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
    proverka("«через месяц перезвонить» (10.10) — 17.10 пауза", stop(("2026-10-10T09:00:00", "через месяц перезвонить")), True)
    proverka("«через месяц перезвонить» (10.09) — 17.10 звоним", stop(("2026-09-10T09:00:00", "через месяц перезвонить")), False)
    proverka("«позвонить через 2 месяца на лдм» (05.09) — 17.10 пауза", stop(("2026-09-05T12:00:00", "позвонить через 2 месяца на лдм")), True)
    proverka("«ребенку 7 месяцев позвонить в марте» — пауза до марта",
             stop(("2026-09-05T12:00:00", "ребенку 7 месяцев позвонить в марте"), den=date(2027, 2, 27)), True)
    proverka("… а с 01.03 — звоним", stop(("2026-09-05T12:00:00", "ребенку 7 месяцев позвонить в марте"), den=date(2027, 3, 1)), False)
    proverka("«в конце месяца звонить» (сентябрь) — 17.10 звоним",
             stop(("2026-09-08T12:00:00", "в конце месяца звонить думают про англ и изо")), False)
    proverka("«ждём, пока дорастут» (28.08) — 17.10 звоним", stop(("2026-08-28T12:24:00", "идут в сад ждем пока дорастут до лдм")), False)
    proverka("«подумаю, если что — вернёмся» — не звонить",
             stop(("2026-10-03T08:07:00", "📞 03.10 13:08, исходящий 69 с (Аня): «Подумаю, если что — вернёмся».", ADMIN2)), True)


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


def test_revyu_1710():
    print("Повторное ревью 17.10")
    for t in ("Отказ", "отказались", "12.10 11:21, обзвон (Ира): отказ.", "📞 Прозвон (app.kidsup.ru): отказ",
              "Прозвучал отказ", "Неактуально", "неинтересно", "Сама перезвонит", "сами наберут",
              "Если что, свяжутся", "если что — позвонят", "Ходят в другой центр", "нашли другой кружок",
              "не перезванивать", "Не звоните больше", "не надо звонить", "Далековато"):
        proverka(f"не звонить: «{t}»", stop(("2026-10-10T09:00:00", t)), True)
    for t in ("Просила не звонить по утрам", "не звонить по будням", "не звонить на работу",
              "Живут не так далеко", "не очень далеко, 15 минут", "Сами позвоним, когда откроется группа",
              "Не интересует английский, хотят рисование", "Робототехника не актуальна, хотят английский",
              "отказались от пробного, хотят сразу в группу", "Автобусом 40 минут, но готовы ездить",
              "Ходит в другой сад до 17, к нам после"):
        proverka(f"звонить можно: «{t}»", stop(("2026-10-10T09:00:00", t)), False)

    S = ("2026-09-06T10:15:00", "Подумаю, свяжусь сама")
    for t in ("17.10 11:21 · обзвон (Ира): Не дозвонились\n\nПо задаче: Недозвон, попытка №2",
              "Набирали 11:20 МСК (Ира), попыток 2. Не ответили.",
              "Звонок 11:20 МСК, исходящий (Ира), 2 мин 05 сек.",
              "👤 ПОД ЭТУ СЕМЬЮ: ребёнок 6 лет, ходил на английский",
              "Н/о", "12.10 но", "Не ответила", "Сбрасывает", "Трубку не берут", "Телефон выключен",
              "Звонила, не дозвонилась"):
        proverka(f"после «свяжусь сама» — «{t[:36]}» не снимает запрет", stop(S, ("2026-10-12T08:21:00", t)), True)
    proverka("текст нашей задачи после «По задаче:» — не ответ семьи",
             stop(("2026-10-17T08:21:00", "17.10 11:21 · обзвон (Ира): думают\n\nПо задаче: Заявка от семьи "
                                          "со статусом «не писать / не звонить» — прочитай комментарий")), False)

    d17 = "2026-10-17T09:00:00"
    p = ("2026-10-17T09:00:00", "Перезвонила 15.10 — после каникул")
    proverka("«перезвонила 15.10» — прошлое, пауза 45 дней, а не год", stop(p, den=date(2026, 12, 5)), False)
    proverka("… а 17.10 — пауза", stop(p), True)
    t = (d17, "Перезвонить 25.10")
    proverka("«Перезвонить 25.10» — до 24.10 пауза", stop(t, den=date(2026, 10, 24)), True)
    proverka("… с 25.10 звоним", stop(t, den=date(2026, 10, 25)), False)
    t = (d17, "Хотят после нового года")
    proverka("«после нового года» — 09.01 пауза", stop(t, den=date(2027, 1, 9)), True)
    proverka("… с 10.01 звоним", stop(t, den=date(2027, 1, 10)), False)
    t = ("2026-10-10T09:00:00", "через год")
    proverka("«через год» — 09.10.2027 пауза", stop(t, den=date(2027, 10, 9)), True)
    proverka("… 10.10.2027 звоним", stop(t, den=date(2027, 10, 10)), False)
    t = ("2026-10-10T09:00:00", "перезвонить через 10 дней")
    proverka("«через 10 дней» — 19.10 пауза", stop(t, den=date(2026, 10, 19)), True)
    proverka("… 20.10 звоним", stop(t, den=date(2026, 10, 20)), False)
    t = ("2026-10-10T09:00:00", "Просила не звонить до ноября")
    proverka("«не звонить до ноября» — 31.10 пауза", stop(t, den=date(2026, 10, 31)), True)
    proverka("… с 01.11 звоним", stop(t, den=date(2026, 11, 1)), False)
    t = ("2026-10-10T09:00:00", "перезвонить 2 ноября")
    proverka("«перезвонить 2 ноября» — 01.11 пауза", stop(t, den=date(2026, 11, 1)), True)
    proverka("… 02.11 звоним", stop(t, den=date(2026, 11, 2)), False)
    t = ("2026-10-10T09:00:00", "позвонить весной")
    proverka("«позвонить весной» — 28.02 пауза", stop(t, den=date(2027, 2, 28)), True)
    proverka("… с 01.03 звоним", stop(t, den=date(2027, 3, 1)), False)
    t = ("2026-10-10T09:00:00", "Пока не актуально, позвонить в январе")
    proverka("«пока не актуально, позвонить в январе» — пауза, не запрет", stop(t, den=date(2027, 1, 1)), False)
    proverka("… в декабре ещё пауза", stop(t, den=date(2026, 12, 31)), True)
    t = ("2026-10-10T09:00:00", "Пока не актуально")
    proverka("«пока не актуально» — пауза 45 дней", stop(t, den=date(2026, 11, 25)), False)
    t = ("2026-10-10T09:00:00", "не актуально до нового года")
    proverka("«не актуально до нового года» — пауза до 10.01, а не запрет", stop(t, den=date(2027, 1, 10)), False)
    proverka("… 09.01 ещё пауза", stop(t, den=date(2027, 1, 9)), True)
    t = ("2026-10-10T09:00:00", "Не интересно до марта")
    proverka("«не интересно до марта» — с 01.03 звоним", stop(t, den=date(2027, 3, 1)), False)
    proverka("«оплатили абонемент до мая» — не отсрочка", stop(("2026-10-10T09:00:00", "оплатили абонемент до мая")), False)
    proverka("огромный срок «через 100000 месяцев» не роняет разбор",
             stop(("2026-10-10T09:00:00", "через 100000 месяцев"), den=date(2026, 10, 17)), True)


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


def test_staroe_1211():
    # 12.11.2026, решение Бориса «старые звонки»: «думает» без живого контакта
    # дольше 21 дня и заявки старше 30 дней без записи — звонок не ставим.
    print("Старые звонки (решение 12.11): pauza_do, staroe, staryy_zvonok")
    from datetime import timedelta
    D12 = date(2026, 11, 12)

    def dn(n):
        return (D12 - timedelta(days=n)).isoformat()
    proverka("«думает» 22 дня без контакта — не ставим", bool(zf.staroe("думает", D12, kontakt=dn(22))), True)
    proverka("«думает» 20 дней — ставим", zf.staroe("думает", D12, kontakt=dn(20)), "")
    proverka("ровно 21 день — ещё ставим", zf.staroe("думает", D12, kontakt=dn(21)), "")
    proverka("контакта не было вовсе — не ставим", bool(zf.staroe("думает", D12)), True)
    proverka("порог из настройки: 30 дней — 22 ещё ставим", zf.staroe("думает", D12, kontakt=dn(22), dmax=30), "")

    def kom(*k):
        return {1: [(x[0], x[2] if len(x) > 2 else ADMIN, x[1]) for x in k]}
    ne_ranshe = kom(("2026-10-13T11:55:00", "Пошли в сад, заниматься планируют, но не раньше ноября."))
    pd = zf.pauza_do(ne_ranshe, [1], [("2026-10-13T14:54:00+03:00", "out", "talked")])
    proverka("«не раньше ноября» (13.10) — пауза до 01.11", pd, date(2026, 11, 1))
    proverka("… 20.10 otvet держит паузу", bool(zf.otvet(ne_ranshe, [1], [], date(2026, 10, 20))), True)
    proverka("… 12.11 пауза кончилась — otvet не держит", zf.otvet(ne_ranshe, [1], [], D12), "")
    proverka("просила «в ноябре», дата прошла 11 дней назад — один звонок ставим",
             zf.staroe("думает", D12, kontakt="2026-10-13", pauza=pd, nabirali="2026-10-13T14:54:00+03:00"), "")
    proverka("… этот звонок уже был (05.11, не дозвонились) — больше не ставим",
             bool(zf.staroe("думает", D12, kontakt="2026-10-13", pauza=pd, nabirali="2026-11-05T12:00:00+03:00")), True)
    proverka("… через 19 дней после даты — не ставим",
             bool(zf.staroe("думает", date(2026, 11, 20), kontakt="2026-10-13", pauza=pd)), True)
    cherez = kom(("2026-10-06T12:06:00", "через месяц перезвонить", ADMIN2))
    proverka("«через месяц перезвонить» (06.10) — дата 06.11", zf.pauza_do(cherez, [1]), date(2026, 11, 6))
    proverka("… 12.11 — один звонок ставим",
             zf.staroe("думает", D12, kontakt="2026-10-06", pauza=zf.pauza_do(cherez, [1])), "")
    proverka("после просьбы — живой ответ без паузы: просьба не в счёт",
             zf.pauza_do(kom(("2026-10-06T12:06:00", "через месяц перезвонить"),
                             ("2026-10-20T09:00:00", "Поговорили, выбирают между ИЗО и английским")), [1]), None)
    proverka("последний ответ — «свяжусь сама»: паузы нет", zf.pauza_do(kom(("2026-10-06T12:06:00", "Подумаю, свяжусь сама")), [1]), None)
    proverka("после просьбы семья объявилась — паузы нет",
             zf.pauza_do(cherez, [1], [("2026-10-10T11:00:00+03:00", "in", "missed")]), None)
    proverka("«звонить не раньше 18.00» — время, не срок",
             stop(("2026-10-10T09:00:00", "звонить не раньше 18.00, днём на работе")), False)

    k = kom(("2026-11-07T09:00:00", "Поговорили с мамой с мобильного, думают до выходных"),
            ("2026-11-08T09:00:00", "но"),
            ("2026-11-09T09:00:00", "11.10 11:45 Клод: напомнили о себе (мессенджер)"),
            ("2026-11-10T09:00:00", "Поговорили, ждут места", 999999))
    proverka("комментарий админа о разговоре — контакт (МСК)", zf.kontakt_v_kartochke(k, [1]), "2026-11-07T12:00")
    proverka("«но», служебное и не-админ — не контакт", zf.kontakt_v_kartochke(kom(("2026-11-08T09:00:00", "но")), [1]), "")

    proverka("заявка 31 день без записи — не ставим", bool(zf.staroe("заявка", D12, zayavka=dn(31))), True)
    proverka("заявка 30 дней — ставим", zf.staroe("заявка", D12, zayavka=dn(30)), "")
    proverka("бывший ученик с оплатами — ставим", zf.staroe("заявка", D12, zapis=True, zayavka=dn(300)), "")
    proverka("дата заявки неизвестна — не отсекаем", zf.staroe("заявка", D12), "")
    proverka("разговор 0 с (длительность не записана) — контакт", zf.razgovor_zachet("talked", 0), True)
    proverka("разговор 5 с — не контакт", zf.razgovor_zachet("talked", 5), False)
    proverka("разговор 48 с — контакт", zf.razgovor_zachet("talked", 48), True)

    # то же правило по базе — для пунктов, которые уже стоят в пульте
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE crm_comments (id INTEGER PRIMARY KEY, user_id INTEGER, ts TEXT, manager_id INTEGER, text TEXT);
        CREATE TABLE mango_calls (id INTEGER PRIMARY KEY, ts TEXT, phone TEXT, direction TEXT, state TEXT, secs INTEGER);
        CREATE TABLE wazzup_inbox (id INTEGER PRIMARY KEY, ts TEXT, phone TEXT);
        CREATE TABLE users (id INTEGER PRIMARY KEY, phone TEXT, created_at TEXT);
        CREATE TABLE lessons (id INTEGER PRIMARY KEY, date TEXT, class_id INTEGER);
        CREATE TABLE lesson_records (id INTEGER PRIMARY KEY, lesson_id INTEGER, user_id INTEGER, visit INTEGER);
        CREATE TABLE payments (id INTEGER PRIMARY KEY, user_id INTEGER, date TEXT, summa REAL);
        CREATE TABLE joins (id INTEGER PRIMARY KEY, user_id INTEGER, class_id INTEGER, status_id INTEGER, created_at TEXT);
        CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT);
    """)
    por = (21, 30)
    for uid, ph in ((51, "79990000051"), (52, "79990000052"), (53, "79990000053"), (54, "79990000054"),
                    (55, "79990000055")):
        c.execute("INSERT INTO users (id, phone, created_at) VALUES (?, ?, ?)", (uid, ph, dn(60) + "T10:00:00+00:00"))
        c.execute("INSERT INTO mango_calls (ts, phone, direction, state, secs) VALUES (?, ?, 'out', 'talked', 60)",
                  (dn(25) + "T12:00:00+03:00", ph))
    proverka("по базе: 25 дней без контакта — старый", bool(zf.staryy_zvonok(c, [51], "79990000051", D12, "думает", por)), True)
    c.execute("INSERT INTO crm_comments (user_id, ts, manager_id, text) VALUES (52, ?, ?, ?)",
              (dn(4) + "T09:00:00", ADMIN, "Созвонились с папой с мобильного, думают"))
    proverka("по базе: комментарий админа 4 дня назад — не старый", zf.staryy_zvonok(c, [52], "79990000052", D12, "думает", por), "")
    c.execute("INSERT INTO lessons (id, date) VALUES (1, ?)", ((D12 + timedelta(days=4)).isoformat(),))
    c.execute("INSERT INTO lesson_records (lesson_id, user_id) VALUES (1, 53)")
    proverka("по базе: записаны на занятие вперёд — правило не применяем",
             zf.staryy_zvonok(c, [53], "79990000053", D12, "думает", por), "")
    c.execute("INSERT INTO wazzup_inbox (ts, phone) VALUES (?, '79990000054')", (dn(3) + "T10:00:00+03:00",))
    proverka("по базе: семья написала сама 3 дня назад — не старый", zf.staryy_zvonok(c, [54], "79990000054", D12, "думает", por), "")
    proverka("по базе: заявка 60 дней без записи — старая", bool(zf.staryy_zvonok(c, [55], "79990000055", D12, "заявка", por)), True)
    c.execute("INSERT INTO classes (id, name) VALUES (7, '2627_Заявки_Робототехника')")
    c.execute("INSERT INTO joins (user_id, class_id, status_id, created_at) VALUES (55, 7, 50509, ?)", (dn(6) + "T10:00:00+00:00",))
    proverka("по базе: та же семья оставила свежую заявку — не старая",
             zf.staryy_zvonok(c, [55], "79990000055", D12, "заявка", por), "")
    c.execute("INSERT INTO payments (user_id, date, summa) VALUES (51, '2025-03-01', 5000)")
    proverka("по базе: бывший ученик с оплатами — по заявке не старый",
             zf.staryy_zvonok(c, [51], "79990000051", D12, "заявка", por), "")


test_ne_zvonit()
test_zvonit_mozhno()
test_otmetki_i_avtomatika()
test_pauza()
test_obyavilis()
test_semya()
test_popytki()
test_revyu_1710()
test_nedavno()
test_staroe_1211()
print("\nошибок:", oshibok)
sys.exit(1 if oshibok else 0)
