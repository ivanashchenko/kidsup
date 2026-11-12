# -*- coding: utf-8 -*-
"""Проверки правил пульта, которые можно гонять без сервера и без живой базы.

22.09.2026. Борис: «Можно уже наконец сделать Пульт без косяков??!!» Каждая
починка до сегодня проверялась глазами и через день всплывала обратно. Здесь
зафиксированы правила, на которых пульт ломался, — чтобы следующая правка
ломала тест, а не смену Ани.

  python3 docs/rabota/pult_testy.py
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, "/home/user/kidsup")

DEN = "2026-09-22"
oshibok = 0


def proverka(nazvanie, poluchili, zhdali):
    global oshibok
    ok = poluchili == zhdali
    oshibok += not ok
    print(("  OK   " if ok else "  ОШИБКА "), nazvanie,
          "" if ok else f"-> {poluchili!r}, ждали {zhdali!r}")


# ── 1. Дедупликация: один сигнал — один открытый пункт ────────────────────
# 22.09, жалоба Лены: наряд кладёт «Клиент ждёт ответа N мин», через час
# автоматика кладёт «Клиент писал, ответа нет» про ту же фразу клиента.
def test_dedup():
    print("Дедупликация пунктов (inbox_add)")
    conn = sqlite3.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
    conn.execute("""CREATE TABLE plan_inbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, ts TEXT, who TEXT,
        text TEXT, phone TEXT, source TEXT, done INTEGER DEFAULT 0)""")

    def add(text, phone, source="автоматика"):
        """Та же логика, что в app/autopilot.py inbox_add."""
        text = (text or "").strip()[:400]
        if not text:
            return False
        if conn.execute("SELECT 1 FROM plan_inbox WHERE day=? AND text=?",
                        (DEN, text)).fetchone():
            return False
        p10 = "".join(c for c in str(phone or "") if c.isdigit())[-10:]
        if source == "автоматика" and len(p10) == 10 and "🔥" not in text:
            if conn.execute("SELECT 1 FROM plan_inbox WHERE day=? AND done=0 "
                            "AND length(phone)>=10 AND substr(phone,-10)=?",
                            (DEN, p10)).fetchone():
                return False
        conn.execute("INSERT INTO plan_inbox (day, ts, who, text, phone, source) "
                     "VALUES (?,?,?,?,?,?)",
                     (DEN, DEN + "T10:00", "Аня", text,
                      "".join(c for c in str(phone) if c.isdigit())[-11:], source))
        return True

    ph = "+7 926 694-25-05"          # один и тот же номер в разном написании
    proverka("наряд кладёт первым",
             add("Клиент ждёт ответа 1175 мин: Афанасьева — «Не могу дозвониться»", ph, "наряд"), True)
    proverka("автоматика не повторяет тот же сигнал",
             add("Клиент писал, ответа нет (13:27, Афанасьева): «Не могу дозвониться»", "79266942505"), False)
    proverka("горящее проходит всегда",
             add("🔥 КЛИЕНТ ЖДЁТ ОТВЕТА (14:02, Афанасьева): «готова оплатить сегодня»", ph), True)
    proverka("другую семью не задевает",
             add("Клиент писал, ответа нет (13:30, Копылова): «Настя приболела»", "79031316253"), True)
    proverka("ручной пункт из разбора звонков проходит",
             add("Махлин: снять с сегодняшнего пробного", "79651624974", "звонок 14:13"), True)
    proverka("и второй ручной по той же семье тоже",
             add("Махлин: оформить шахматы на воскресенье", "79651624974", "звонок 14:13"), True)
    conn.execute("UPDATE plan_inbox SET done=1 WHERE substr(phone,-10)='9266942505'")
    proverka("когда по семье всё закрыто — новый сигнал проходит",
             add("Клиент писал, ответа нет (16:10, Афанасьева): «ну что там?»", ph), True)


# ── 2. Что не требует ответа ──────────────────────────────────────────────
def test_polite():
    print("Сообщения, которые не требуют ответа (naryad._polite)")
    from app import naryad
    sluchai = [
        ("Добрый день!\nПосле ссылки по смс только что наконец все вышло", True),
        ("Спасибо! Все получилось", True),
        ("Зашла в кабинет, спасибо", True),
        ("Разобрались, спасибо большое", True),
        ("👍", True),
        ("Здравствуйте, а можно перенести занятие?", False),
        ("Не могу дозвониться", False),
        ("Всё вышло, но не вижу расписание. Что делать?", False),
        ("Добрый день, хотим записаться на пробное", False),
        ("Мы хотим прекратить ходить, как вернуть остаток", False),
    ]
    for t, zhdali in sluchai:
        proverka(repr(t[:45]), naryad._polite(t), zhdali)


# ── 3. Правила самопроверки пульта ────────────────────────────────────────
def test_proverka_pulta():
    print("Правила самопроверки (pult_proverka)")
    from app import pult_proverka as P
    proverka("«действий не требуется» — пустышка",
             bool(P.PUSTYSHKA.search("Сорокина Полина, действий не требуется")), True)
    proverka("нормальный пункт — не пустышка",
             bool(P.PUSTYSHKA.search("Позвонить маме и записать на пробное")), False)
    proverka("«бесплатное пробное» ловится",
             bool(P.ZAPRESHCHENO.search("позвать на бесплатное пробное занятие")), True)
    proverka("«условно-бесплатное» не ловится",
             bool(P.ZAPRESHCHENO.search("условно-бесплатное первое занятие")), False)
    # 08.10 Борис: закрытие занятий у Лизы сняли (7a022af) — так и осталось.
    # 29.10 Борис: «Лиза из дома всё может делать, кроме звонков».
    proverka("Лизе нельзя закрытие занятий (с 08.10)",
             P.liza_mozhno("Закрыть занятия за вчера, 40 штук"), False)
    proverka("Лизе можно долги",
             P.liza_mozhno("Долг 2 500 ₽ у семьи Гориных"), True)
    proverka("Лизе нельзя обзвон",
             P.liza_mozhno("Позвонить Петровым и дожать до записи"), False)
    proverka("Лизе можно переписку (с 29.10)",
             P.liza_mozhno("Написать в WhatsApp Петровым: вернуть после пробного"), True)
    proverka("Лизе нельзя разговор в холле",
             P.liza_mozhno("в холле 29.10 — до или после занятия, иначе звонком"), False)
    proverka("«сами позвонят» — не звонок Лизы",
             P.liza_mozhno("Почему: «сами позвонят» (15–21.09)"), True)
    proverka("телефон из любого написания", P._p10("+7 (916) 187-97-63"), "9161879763")
    proverka("короткий номер не телефон", P._p10("123"), "")


# ── 4. Порядок дел в колонке ──────────────────────────────────────────────
def test_ves():
    print("Порядок дел по цене ошибки (pult._ves)")
    from app import pult
    def ves(text, tag="обещание", phone="9161879763"):
        return pult._ves({"text": text, "tag": tag, "phone": phone})
    proverka("жалоба — первой", ves("!! Жалоба: семья ждёт ответа сутки"), 0)
    proverka("готов оплатить — первой", ves("Семья готова оплатить сегодня"), 0)
    proverka("подтвердить пробное — вторым", ves("Подтвердить пробное сегодня в 17:00"), 1)
    proverka("дожим — третьим", ves("Заявка: перезвонить и дожать"), 2)
    proverka("«готова оплатить» — тоже первой", ves("Мама готова оплатить сегодня"), 0)
    proverka("семья объявила об уходе — первой", ves("Хотим прекратить ходить, как вернуть остаток"), 0)
    proverka("не дозвонилась — не ниже дожима", ves("Три раза не дозвонилась до семьи") <= 2, True)
    proverka("возврат ушедшего — четвёртым", ves("Вернуть: Астахов", "возврат"), 3)
    proverka("хвост в CRM — последним", ves("Закрыть запись в CRM, поставить статус"), 4)
    # 24.09: разобранный вручную порядок сильнее шаблонов.
    proverka("prio сильнее слов «ВЕРНУТЬ:»",
             pult._ves({"text": "ВЕРНУТЬ: Сысоев", "tag": "возврат", "prio": 120}), 1)
    proverka("prio сильнее слова «карточка»",
             pult._ves({"text": "Поставить запись до прихода, вписать в карточку", "prio": 5}), 0)


# ── 4а. Блоки колонки (просьба Иры 23.10.2026) ────────────────────────────
def test_bloki():
    print("Блоки колонки (pult._blok)")
    from app import pult
    def blok(text, tag="обещание"):
        return pult._blok({"text": text, "tag": tag})
    proverka("уход семьи — горит", blok("Мама: хотим уйти, верните остаток"), "gorit")
    proverka("заболевший педагог — горит",
             blok("Сообщить семьям, состоится ли у Ильи суббота, выздоровел ли он"), "gorit")
    proverka("«клиент писал, ответа нет» — не горит, а заявки",
             blok("Клиент писал, ответа нет (12:10): «когда отработка?»"), "zayavki")
    proverka("пропущенный звонок — заявки", blok("🔥 ПРОПУЩЕННЫЙ ЗВОНОК от +79990000000"), "zayavki")
    proverka("подтвердить пробное — пробные", blok("Подтвердить пробное сегодня в 16:00"), "probnye")
    proverka("после пробного с абонементом — обзвон, не оплаты",
             blok("Посетил пробное: позвонить, впечатления → абонемент −10 %"), "obzvon")
    proverka("«Учится» в группе Заявки_ — чистка, не заявки",
             blok("Смирнов — стоит «Учится» в группе «Заявки_Робототехника»"), "chistka")
    proverka("«проведено» заранее — чистка, не оплаты",
             blok("Занятие стоит «проведено» — проверить, что с абонемента ничего не списалось"), "chistka")
    proverka("камера: ребёнок без записи — чистка, не оплаты",
             blok("на камере 5 детей, записей 3 — поставить запись (и проверить абонемент)"), "chistka")
    proverka("долг по абонементу — оплаты", blok("Выставить счёт и ссылку на оплату 5 250 ₽"), "oplaty")
    proverka("хвост с датой не мешает разбору", blok("⏳ с 19.10: перезвонить маме"), "obzvon")
    proverka("заявка без ответа по метке", blok("Иван, 5 лет", "наряд"), "zayavki")
    proverka("вернуть ушедшего по метке", blok("Астахов", "возврат"), "obzvon")


# ── 5. Правки аудита 22.09.2026 ───────────────────────────────────────────
def test_audit():
    print("Находки аудита 22.09 (телефоны, обрезка, отказы, адресаты)")
    from app.pult import _tel
    from app.autopilot import obrezat, _OTKAZ_SLOVA, _manager_short, MANAGER_NAMES
    from app import naryad

    # Кнопка «позвонить» набирала Турцию, Японию и Германию
    proverka("мобильный из десяти цифр", _tel("9032952727"), "79032952727")
    proverka("городской Петербурга", _tel("8123315033"), "78123315033")
    proverka("восьмёрка спереди", _tel("89161879763"), "79161879763")
    proverka("мусор — ссылки нет", _tel("123"), "")
    proverka("пусто — ссылки нет", _tel(""), "")

    # Текст обрывался на полуслове, и последнее указание не читалось
    dlinno = ("Нафиков Назар придёт к логопеду СЕГОДНЯ в 19:20 вместо четверга. "
              "Если не придут, снять запись, чтобы время не простаивало.")
    proverka("короткий не трогаем", obrezat("Позвонить маме", 400), "Позвонить маме")
    proverka("длинный обрывается видимо", obrezat(dlinno, 80).endswith("…"), True)
    proverka("обрыв по границе предложения",
             obrezat(dlinno, 80), "Нафиков Назар придёт к логопеду СЕГОДНЯ в 19:20 вместо четверга…")

    # Статус «Отказ» по одной догадке модели ставить нельзя
    proverka("«Спасибо большое!» — не отказ", bool(_OTKAZ_SLOVA.search("Спасибо большое! ❤️")), False)
    proverka("«не будем ходить» — отказ", bool(_OTKAZ_SLOVA.search("Мы не будем ходить")), True)
    proverka("«уже неактуально» — отказ наряда",
             bool(naryad.OTKAZ_RE.search("Добрый день, уже неактуально")), True)
    proverka("«хотим записаться» — не отказ",
             bool(naryad.OTKAZ_RE.search("Здравствуйте, хотим записаться")), False)

    # Пункты Лизе и Борису уезжали дежурной
    proverka("Лиза находится по справочнику", _manager_short(154181), "Лиза")
    proverka("Борис находится по справочнику", _manager_short(84116), "Борис")
    proverka("неизвестный id — пусто", _manager_short(999999), "")

    # «2645 мин» админ в уме не переводит
    proverka("минуты до полутора часов", naryad._zhdet(45), "45 мин")
    proverka("часы", naryad._zhdet(1032), "17 ч")
    proverka("больше суток", naryad._zhdet(2645), "больше суток")


# ── 6. Дата первого занятия — только из той группы, куда записали ─────────
# 10.09 Новикову Льву пришло «Робототехника 11 сентября в 10:00» — это был
# его нулевой класс. 23.09: откат «нет записей в группе → берём любую» убран.
def test_data_gruppy():
    print("Дата первого занятия в подтверждении записи")
    from app import autopilot as ap

    class Mk:
        def get(self, path, params=None):
            return {"lessonRecords": [
                {"lesson": {"classId": 1, "date": "2099-01-05", "beginTime": "10:00"}},
                {"lesson": {"classId": 2, "date": "2099-01-09", "beginTime": "17:00"}},
            ]}
    proverka("своя группа", ap._next_lesson(Mk(), 7, days=99999, class_id=2), "9 января в 17:00")
    proverka("несколько групп — ближайшая из них",
             ap._next_lesson(Mk(), 7, days=99999, class_id=[2, 1]), "5 января в 10:00")
    proverka("в группе записей нет — даты нет, чужую не берём",
             ap._next_lesson(Mk(), 7, days=99999, class_id=3), "")
    proverka("группа не известна — ближайшая любая",
             ap._next_lesson(Mk(), 7, days=99999), "5 января в 10:00")



# ── 7. Повторное «позвонить» не ложится поверх открытого хвоста ──────────
# 23.09 в 08:01 утренний добор положил Ане «Недозвон, попытка №1: Комлев»
# и «…Княжев», хотя у обоих с прошлых дней открыты «недозвон, повтор №2»
# и «ВЕРНУТЬ». Проверка дублей смотрела только сегодняшний день.
def test_povtor():
    print("Повторные звонки сверяются с хвостом")
    from app.autopilot import POVTOR_RE
    for t, zhdali in (("Недозвон, попытка №1: Комлев Ярослав +79251766455", True),
                      ("ВЕРНУТЬ: Княжев Богдан 79263891584", True),
                      ("Не пришёл на пробное: Даша", True),
                      ("Клиент писал, ответа нет (18:34, +79151316404)", False),
                      ("🔥 НОВАЯ ЗАЯВКА — позвонить в течение 5 минут!", False)):
        proverka(t[:40], bool(POVTOR_RE.match(t)), zhdali)



# ── 8. Две заявки с сайта от одной семьи — один пункт ────────────────────
# 23.09: мама Стефании за две минуты отправила две формы — у Ани два 🔥
# пункта и третий, пустой, от «свежего лида». Проверяем на временной базе.
def test_zayavki_odin_punkt():
    print("Повторная заявка с сайта дописывается в первый пункт")
    from app import config, autopilot as ap
    staryj = config.DB_PATH
    config.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
    try:
        ap._duty_name = lambda: "Аня"
        a = ap.inbox_add("🤖 Клод: 🔥 НОВАЯ ЗАЯВКА с сайта — позвонить в течение 5 минут! "
                         "Стефания, 3, 🎭 Актёрское мастерство (лист ожидания), тел. +79153520003", "", "Аня")
        b = ap.inbox_add("🤖 Клод: 🔥 НОВАЯ ЗАЯВКА с сайта — позвонить в течение 5 минут! "
                         "Стефания, 3, 💃 Спортивно-бальные танцы (лист ожидания), тел. +79153520003", "", "Аня")
        c = ap.inbox_add("🔥 НОВАЯ ЗАЯВКА — позвонить в течение 5 минут! Стефания, тел. +79153520003. "
                         "Свежий лид конвертируется в разы лучше.", "", "Аня")
        d = ap.inbox_add("🤖 Клод: 🔥 НОВАЯ ЗАЯВКА с сайта — позвонить в течение 5 минут! "
                         "Мирон, 3,7, акробатика, тел. +79815062239", "", "Аня")
        conn = sqlite3.connect(config.DB_PATH)
        rows = conn.execute("SELECT text FROM plan_inbox ORDER BY id").fetchall()
        proverka("первая заявка — пункт", a, True)
        proverka("вторая той же семьи — без нового пункта", b, False)
        proverka("«свежий лид» по ней же — без нового пункта", c, False)
        proverka("другая семья — свой пункт", d, True)
        proverka("всего пунктов", len(rows), 2)
        proverka("второе направление дописано", "бальные танцы" in rows[0][0], True)
    finally:
        config.DB_PATH = staryj


# ── 9. Утро пульта: порции обзвона не копятся, текст не режется ──────────
# 15.10: у Лены и Ани висело 300+ невзятых «возвратов» — утро переносило их
# без срока, а сам перенос резал текст на 400 символах посреди слова.
def test_utro():
    print("Утро пульта (hvost.utro): срок «возвратам», текст целиком")
    from app import config, hvost, pult
    staryj, st_today, st_duty = config.DB_PATH, pult.today, pult.duty
    config.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
    try:
        pult.today = lambda: "2026-10-16"          # пятница, в смене одна Ира
        pult.duty = lambda day: ["Ира"]
        conn = sqlite3.connect(config.DB_PATH)
        conn.execute("""CREATE TABLE plan_inbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, ts TEXT, who TEXT,
            text TEXT, phone TEXT, source TEXT, done INTEGER DEFAULT 0,
            tries INTEGER DEFAULT 0, prio INTEGER)""")
        dlinnyj = "ЗАЯВКА: " + "очень длинный текст пункта " * 22 + "КОНЕЦ"
        for r in (
            ("2026-10-15", "2026-10-13T08:01+03:00", "Аня", "БАЗА 2024/25: Иванов", "79990000001", "возврат: база 2024/25", 0),
            ("2026-10-15", "2026-10-15T08:01+03:00", "Лена", "ДУМАЕТ: Петров", "79990000002", "возврат: думает", 0),
            ("2026-10-15", "2026-10-13T08:01+03:00", "Лена", "НЕДОЗВОН: Сидоров", "79990000003", "возврат: недозвон", 2),
            ("2026-10-15", "2026-10-11T08:01+03:00", "Лена", "НЕДОЗВОН: Козлов", "79990000004", "возврат: недозвон", 1),
            ("2026-10-15", "2026-10-12T08:01+03:00", "Лена", "БАЗА: Орлов — договорились на пт", "79990000005", "возврат: база 2024/25", 0),
            ("2026-10-15", "2026-10-15T09:00+03:00", "Лена", dlinnyj, "79990000006", "план 15.10", 0),
            ("2026-10-15", "2026-10-10T09:00+03:00", "Лиза", "Маткапитал: вернуть 1 000", "79990000007", "звонок", 0),
            ("2026-10-15", "2026-10-13T08:01+03:00", "Ира",
             "БАЗА 2025/26: Смирнов — итог 14.10 12:00 (Ира): не дозвонились", "79990000008", "возврат: база 2025/26", 0),
        ):
            conn.execute("INSERT INTO plan_inbox (day, ts, who, text, phone, source, tries) "
                         "VALUES (?,?,?,?,?,?,?)", r)
        conn.commit()
        conn.close()
        itog = hvost.utro()
        conn = sqlite3.connect(config.DB_PATH)
        got = {r[0]: r[1:] for r in conn.execute("SELECT phone, done, day, who, text FROM plan_inbox")}
        conn.close()
        proverka("три дня без попыток — закрыт", got["79990000001"][0], 1)
        proverka("в пометке — куда делся номер", "общую очередь обзвона" in got["79990000001"][3], True)
        proverka("вчерашний — перенесён Ире", got["79990000002"][:3], (0, "2026-10-16", "Ира"))
        proverka("с попытками держим четыре дня", got["79990000003"][0], 0)
        proverka("пять дней с попытками — закрыт", got["79990000004"][0], 1)
        proverka("«договорились» — не закрываем", got["79990000005"][0], 0)
        proverka("длинный текст перенесён целиком", got["79990000006"][3].endswith("КОНЕЦ"), True)
        proverka("пометка возраста на длинном", got["79990000006"][3].startswith("⏳ с 15.10: "), True)
        proverka("обязательство Лизы остаётся у Лизы", got["79990000007"][:3], (0, "2026-10-16", "Лиза"))
        proverka("недозвон с «Обзвона дня» — попытка, держим", got["79990000008"][0], 0)
        proverka("закрыто ровно два", len(itog["закрыто"]), 2)
    finally:
        pult.today, pult.duty, config.DB_PATH = st_today, st_duty, staryj


# ── 10. «Старые звонки» (решение Бориса 12.11) ───────────────────────────
# «Думает» без живого контакта дольше 21 дня и заявки старше 30 дней без
# записи на занятие больше не ставятся звонком; уже стоящие такие пункты утро
# закрывает по правилу, а не «вернётся в общую очередь». Данные выдуманные.
def _baza_1211():
    """Временная база со всеми таблицами, которые читают очереди звонков."""
    from app import config, db
    config.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
    db.init_db()
    conn = sqlite3.connect(config.DB_PATH)
    conn.executescript("""
        CREATE TABLE crm_comments (id INTEGER PRIMARY KEY, user_id INTEGER, ts TEXT, manager_id INTEGER, text TEXT);
        CREATE TABLE mango_calls (id INTEGER PRIMARY KEY, ts TEXT, phone TEXT, direction TEXT, state TEXT,
                                  rec_id TEXT, secs INTEGER, ext TEXT);
        CREATE TABLE wazzup_inbox (id INTEGER PRIMARY KEY, ts TEXT, phone TEXT, text TEXT);
        CREATE TABLE wazzup_outbox (id INTEGER PRIMARY KEY, ts TEXT, phone TEXT, text TEXT);
        CREATE TABLE plan_inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, ts TEXT, who TEXT,
                                 text TEXT, phone TEXT, source TEXT, done INTEGER DEFAULT 0,
                                 tries INTEGER DEFAULT 0, prio INTEGER);
    """)
    return conn


def _semya_1211(conn, uid, phone, status, sozdana, razgovor="", kom="", kom_ts="", oplata=False, zanyatie=""):
    conn.execute("INSERT INTO users (id, name, phone, client_state_id, created_at, raw) VALUES (?,?,?,?,?,?)",
                 (uid, f"Тестов {uid}", phone, status, sozdana + "T10:00:00+00:00", "{}"))
    if razgovor:
        conn.execute("INSERT INTO mango_calls (ts, phone, direction, state, secs) VALUES (?,?,'out','talked',60)",
                     (razgovor + "T12:00:00+03:00", phone))
    if kom:
        conn.execute("INSERT INTO crm_comments (user_id, ts, manager_id, text) VALUES (?,?,232763,?)",
                     (uid, kom_ts + "T09:05:00", kom))
    if oplata:
        conn.execute("INSERT INTO payments (user_id, date, summa) VALUES (?, '2025-03-01', 5000)", (uid,))
    if zanyatie:
        conn.execute("INSERT INTO lessons (id, date, class_id) VALUES (?,?,1)", (uid, zanyatie))
        conn.execute("INSERT INTO lesson_records (id, lesson_id, user_id, visit) VALUES (?,?,?,0)", (uid, uid, uid))


def test_vozvrat_1211():
    print("Очереди обзвона (vozvrat.spiski): правило 12.11")
    from datetime import date, timedelta
    from app import config, db, vozvrat
    staryj = config.DB_PATH
    try:
        conn = _baza_1211()
        t = date.today()

        def dn(n):
            return (t - timedelta(days=n)).isoformat()
        DUM, NED = vozvrat.DUMAET, vozvrat.NEDOZVON
        _semya_1211(conn, 101, "79990000101", DUM, dn(90), razgovor=dn(22))
        _semya_1211(conn, 102, "79990000102", DUM, dn(90), razgovor=dn(20))
        _semya_1211(conn, 103, "79990000103", DUM, dn(90), razgovor=dn(40), zanyatie=(t + timedelta(days=3)).isoformat())
        _semya_1211(conn, 104, "79990000104", DUM, dn(90), razgovor=dn(30), kom_ts=dn(30),
                    kom=f"Пошли в сад, просили перезвонить {(t - timedelta(days=5)):%d.%m}")
        _semya_1211(conn, 105, "79990000105", DUM, dn(90), razgovor=dn(40), kom_ts=dn(10),
                    kom="Поговорили с мамой с мобильного, думают про английский")
        _semya_1211(conn, 106, "79990000106", NED, dn(31))
        _semya_1211(conn, 107, "79990000107", NED, dn(400), oplata=True)
        _semya_1211(conn, 108, "79990000108", NED, dn(10))
        conn.commit()
        conn.close()
        S = vozvrat.spiski()
        dum = {r["телефон"]: r for r in S["думает"]}
        ned = {r["телефон"] for r in S["недозвон"]}
        proverka("«думает» 22 дня без контакта — не ставится", "79990000101" in dum, False)
        proverka("«думает» 20 дней — ставится", "79990000102" in dum, True)
        proverka("с будущей записью на занятие — не ставится", "79990000103" in dum, False)
        proverka("просили перезвонить к дате, прошло 5 дней — один звонок ставится", "79990000104" in dum, True)
        proverka("комментарий админа о разговоре — контакт (10 дн., а не 40)",
                 dum.get("79990000105", {}).get("дней_без_контакта"), 10)
        proverka("заявка 31 день без записи — не ставится", "79990000106" in ned, False)
        proverka("бывший ученик с оплатами — ставится", "79990000107" in ned, True)
        proverka("свежая заявка — ставится", "79990000108" in ned, True)
        proverka("счётчик правила", S["старые_по_правилу"], {"думает>21": 1, "заявка>30": 1})
        proverka("счётчик «записаны на занятие»", S["записаны_на_занятие_исключено"], {"думает": 1})
        tekst = vozvrat._tekst(dum["79990000102"])
        proverka("текст пункта не обещает звонок через 7 дней",
                 ("≤7" in tekst, "звонок больше не поставим" in tekst), (False, True))
        db.set_setting("dumaet_max_dney", "25")
        proverka("порог из настройки: при 25 днях 22-дневный снова ставится",
                 "79990000101" in {r["телефон"] for r in vozvrat.spiski()["думает"]}, True)
    finally:
        config.DB_PATH = staryj


def test_utro_1211():
    print("Утро пульта (hvost.utro): закрытие по правилу 12.11, пометка админа")
    from app import config, hvost, pult
    staryj, st_today, st_duty = config.DB_PATH, pult.today, pult.duty
    try:
        conn = _baza_1211()
        pult.today = lambda: "2026-11-13"          # пятница, в смене Лена
        pult.duty = lambda day: ["Лена"]
        DUM, NED = 146950, 345768
        _semya_1211(conn, 301, "79990000301", DUM, "2026-08-01", razgovor="2026-10-20")
        _semya_1211(conn, 302, "79990000302", DUM, "2026-08-01", razgovor="2026-10-03")
        _semya_1211(conn, 303, "79990000303", DUM, "2026-08-01", razgovor="2026-11-03")
        _semya_1211(conn, 304, "79990000304", NED, "2026-09-20")
        _semya_1211(conn, 305, "79990000305", NED, "2026-10-01")
        _semya_1211(conn, 306, "79990000306", NED, "2026-09-01")
        _semya_1211(conn, 307, "79990000307", 125957, "2024-09-01", oplata=True)
        _semya_1211(conn, 308, "79990000308", NED, "2024-09-01", oplata=True)
        for r in (
            ("2026-11-12", "2026-11-12T08:01+03:00", "Лена", "ДУМАЕТ, 23 дн. без контакта: Тестов Петя", "79990000301", "возврат: думает"),
            ("2026-11-12", "2026-11-09T08:01+03:00", "Лена",
             "ДУМАЕТ, 37 дн. без контакта: Тестов Ваня · ✎ 12.11 10:00: мама просила набрать в пятницу",
             "79990000302", "возврат: думает"),
            ("2026-11-12", "2026-11-12T08:01+03:00", "Лена", "ДУМАЕТ, 9 дн. без контакта: Тестов Коля", "79990000303", "возврат: думает"),
            ("2026-11-12", "2026-11-12T08:01+03:00", "Лена", "НЕДОЗВОН, попытка 2 из 3: Тестов Миша", "79990000304", "возврат: недозвон"),
            ("2026-11-12", "2026-11-12T10:00+03:00", "Лена", "⏳ с 11.11: Заявка сезона без результата: Тестова Аня, 43 дн. Позвонить.",
             "79990000305", "наряд"),
            ("2026-11-12", "2026-11-12T10:00+03:00", "Лена", "Клиент ждёт ответа 2 ч: Тестова Оля — «а есть места?»",
             "79990000306", "наряд"),
            ("2026-11-12", "2026-11-10T08:01+03:00", "Лена", "БАЗА 2024/25: Тестов Гоша", "79990000307", "возврат: база 2024/25"),
            ("2026-11-12", "2026-11-12T08:01+03:00", "Лена", "НЕДОЗВОН, попытка 1 из 3 (платили раньше): Тестов Лёва",
             "79990000308", "возврат: недозвон"),
        ):
            conn.execute("INSERT INTO plan_inbox (day, ts, who, text, phone, source) VALUES (?,?,?,?,?,?)", r)
        conn.commit()
        conn.close()
        prosmotr = hvost.staroe()
        proverka("просмотр /api/hvost/staroe: к закрытию — думает, недозвон-заявка и заявка наряда",
                 sorted(x["id"] for x in prosmotr["закрыть"]), [1, 4, 5])
        proverka("… пункт с пометкой админа — отдельно", [x["id"] for x in prosmotr["с_пометкой_админа"]], [2])
        conn = sqlite3.connect(config.DB_PATH)
        proverka("просмотр ничего не закрыл", conn.execute("SELECT COUNT(*) FROM plan_inbox WHERE done=1").fetchone()[0], 0)
        conn.close()
        itog = hvost.utro()
        conn = sqlite3.connect(config.DB_PATH)
        got = {r[0]: r[1:] for r in conn.execute("SELECT phone, done, day, who, text FROM plan_inbox")}
        conn.close()
        a = got["79990000301"]
        proverka("«думает» 24 дня без контакта — закрыт по правилу", a[0], 1)
        proverka("… с пометкой «по правилу 12.11 … в обзвон не вернётся»",
                 ("по правилу 12.11: «думает» без контакта >21 дн. — в обзвон не вернётся" in a[3],
                  "общую очередь" in a[3]), (True, False))
        proverka("пункт с « · ✎ » не закрыт ни по правилу, ни как невзятый — перенесён",
                 got["79990000302"][:3], (0, "2026-11-13", "Лена"))
        proverka("«думает» 10 дней — перенесён", got["79990000303"][:2], (0, "2026-11-13"))
        proverka("недозвон по заявке 54 дня без записи — закрыт по правилу",
                 (got["79990000304"][0], "заявка >30 дн. без записи — в обзвон не вернётся" in got["79990000304"][3]), (1, True))
        proverka("заявка наряда 43 дня (с ⏳) — закрыта по правилу", got["79990000305"][0], 1)
        proverka("«клиент ждёт ответа» правило не трогает", got["79990000306"][0], 0)
        proverka("база 2024/25 — по-старому: не взята, «вернётся в общую очередь»",
                 (got["79990000307"][0], "общую очередь обзвона" in got["79990000307"][3]), (1, True))
        proverka("недозвон бывшего ученика с оплатами — перенесён", got["79990000308"][0], 0)
        proverka("по правилу закрыто три", itog.get("по_правилу"), 3)
    finally:
        pult.today, pult.duty, config.DB_PATH = st_today, st_duty, staryj


def test_naryad_1211():
    print("Наряд (naryad.sobrat): заявки старше 30 дней без записи не звоним")
    import types
    from app import config, naryad, zayavki, zayavki_audit
    staryj = config.DB_PATH
    st = (naryad._busy, zayavki_audit.cached, zayavki.collect, sys.modules.get("app.main"))
    try:
        conn = _baza_1211()
        _semya_1211(conn, 204, "79990000204", 345768, "2024-09-01", oplata=True)
        conn.commit()
        conn.close()

        def stroka(phone, dney):
            return {"phone": phone, "name": f"Тестов {phone[-3:]}", "comment": "", "class": "Английский",
                    "days": dney, "uid": int(phone[-3:]), "created": ""}
        naryad._busy = lambda day: set()
        zayavki_audit.cached = lambda max_age_min=60: {"семьи": [
            {"phone": "9990000201", "name": "Тестов 201", "kind": "не доделали", "why": "", "days": 40,
             "days_min": 40, "uid": 201, "cards": 1, "хочет": ["Английский"]},
            {"phone": "9990000202", "name": "Тестов 202", "kind": "не доделали", "why": "", "days": 40,
             "days_min": 5, "uid": 202, "cards": 1, "хочет": ["Английский", "ИЗО"]}]}
        zayavki.collect = lambda: {"untouched": [stroka("9990000203", 40), stroka("9990000203", 3)],
                                   "tried": [], "talked": [stroka("9990000204", 45)], "tail": []}
        zaglushka = types.ModuleType("app.main")
        zaglushka.api_mesta_voronka = lambda: {"списки": {}}

        async def _zhdut(min_minutes=40):
            return {"items": []}
        zaglushka.api_waiting = _zhdut
        sys.modules["app.main"] = zaglushka
        items, stare = naryad._sobrat("2026-11-13")
        po_nomeru = {i["phone"]: i["text"] for i in items}
        proverka("заявка 40 дней без записи — не берёт", "79990000201" in po_nomeru, False)
        proverka("у семьи есть свежая заявка — берёт, по свежей (5 дн.)",
                 ("79990000202" in po_nomeru, ", 5 дн." in po_nomeru.get("79990000202", "")), (True, True))
        proverka("сырая строка: 40 дней не берёт, свежая той же семьи — берёт",
                 ", 3 дн." in po_nomeru.get("79990000203", ""), True)
        proverka("бывший ученик с оплатами — берёт и через 45 дней", "79990000204" in po_nomeru, True)
        proverka("счётчик отсечённых семей", stare, 1)
    finally:
        naryad._busy, zayavki_audit.cached, zayavki.collect = st[:3]
        if st[3] is None:
            sys.modules.pop("app.main", None)
        else:
            sys.modules["app.main"] = st[3]
        config.DB_PATH = staryj


def test_audit_1211():
    print("Разбор заявок (zayavki_audit.audit): самая свежая заявка семьи, свежие первыми")
    from app import config, moyklass_client, zayavki, zayavki_audit
    staryj = config.DB_PATH
    st = (zayavki.collect, moyklass_client.MoyklassClient)
    try:
        _baza_1211().close()

        class FalshivyjMK:                       # МойКласс не зовём: ни карточек, ни комментариев
            def __init__(self, *a, **k):
                pass

            def get(self, path, params=None):
                if path == "/v1/company/users":
                    return {"users": []}
                return {} if path.startswith("/v1/company/users/") else []

            def close(self):
                pass
        moyklass_client.MoyklassClient = FalshivyjMK

        def stroka(phone, dney, kl):
            return {"phone": phone, "name": f"Тестов {phone[-3:]}", "comment": "", "class": kl, "days": dney,
                    "uid": int(phone[-3:]), "created": "2026-10-01", "calls": 0, "out": 0, "in": 0}
        zayavki.collect = lambda: {"untouched": [stroka("9990000501", 60, "ИЗО"), stroka("9990000502", 12, "ИЗО"),
                                                 stroka("9990000501", 4, "Английский")],
                                   "tried": [], "talked": [], "tail": []}
        sem = zayavki_audit.audit()["семьи"]
        proverka("у семьи две заявки: самая старая 60, самая свежая 4",
                 [(f["phone"], f["days"], f["days_min"]) for f in sem if f["phone"] == "9990000501"],
                 [("9990000501", 60, 4)])
        proverka("свежие первыми (по самой свежей заявке)", [f["phone"] for f in sem], ["9990000501", "9990000502"])
    finally:
        zayavki.collect, moyklass_client.MoyklassClient = st
        config.DB_PATH = staryj


def test_zayavki_blok_1211():
    print("Блок заявок на пульте (zayavki.block): старше месяца — одной строкой")
    from app import config, zayavki, zayavki_audit
    staryj = config.DB_PATH
    st = (zayavki_audit.cached, zayavki.collect)
    try:
        _baza_1211().close()

        def stroka(phone, dney):
            return {"phone": phone, "name": f"Тестов {phone[-3:]}", "comment": "", "class": "Английский",
                    "days": dney, "uid": int(phone[-3:]), "created": "2026-10-01", "calls": 0, "out": 0, "in": 0}
        zayavki.collect = lambda: {"untouched": [stroka("9990000401", 40), stroka("9990000402", 4)],
                                   "tried": [stroka("9990000403", 35)], "talked": [stroka("9990000404", 50)],
                                   "tail": [stroka("9990000405", 60)], "season_total": 5, "old_total": 0}
        zayavki_audit.cached = lambda max_age_min=60: None
        h = zayavki.block()
        proverka("сырой блок: старые не зовут звонить, свежая и хвост на месте",
                 ("9990000401" in h, "9990000403" in h, "9990000404" in h, "9990000402" in h, "9990000405" in h),
                 (False, False, False, True, True))
        proverka("сырой блок: «3 заявки старше месяца — по правилу 12.11 не звоним, только статус»",
                 "3 заявки старше месяца — по правилу 12.11 не звоним, только статус" in h, True)
        zayavki_audit.cached = lambda max_age_min=60: {"проверено": 3, "семьи": [
            {"phone": "9990000411", "name": "Тестов 411", "kind": "не доделали", "why": "", "days": 40, "days_min": 40,
             "uid": 411, "cards": 1, "хочет": ["Английский"]},
            {"phone": "9990000412", "name": "Тестов 412", "kind": "не доделали", "why": "", "days": 40, "days_min": 6,
             "uid": 412, "cards": 1, "хочет": ["Английский", "ИЗО"]},
            {"phone": "9990000413", "name": "Тестов 413", "kind": "говорили", "why": "был разговор", "days": 50,
             "days_min": 50, "uid": 413, "cards": 1, "хочет": ["ИЗО"]}]}
        h = zayavki.block()
        proverka("по разбору: 40 дней — строкой, свежая заявка семьи — в списке, смена статуса — на месте",
                 ("9990000411" in h, "9990000412" in h, "9990000413" in h), (False, True, True))
        proverka("по разбору: строка про старые (одна семейная + одна «дожать»)",
                 "2 заявки старше месяца — по правилу 12.11 не звоним" in h, True)
    finally:
        zayavki_audit.cached, zayavki.collect = st
        config.DB_PATH = staryj


def test_boris_desyat():
    # 12.11.2026, Борис: в его колонке 10 решений сверху, остальное — «Позже».
    # И номер пункта виден у каждого пункта инбокса (вопрос «где их видно?»).
    print("Колонка владельца: 10 + «Позже», номера пунктов")
    from app import pult
    lis = [(False, True, i % 3 == 0, f"<li>п{i}</li>", {}) for i in range(14)]
    lis.append((True, False, False, "<li>сделан</li>", {}))
    h = pult._boris_desyat(lis)
    proverka("сверху ровно десять", h.split("<details")[0].count("<li>п"), 10)
    proverka("порядок ленты сохранён", h.index("<li>п0</li>") < h.index("<li>п9</li>"), True)
    proverka("одиннадцатый — под «Позже»", h.index("<li>п10</li>") > h.index("Позже · 4"), True)
    proverka("хвосты прошлых дней — тоже решения", "<li>п3</li>" in h, True)
    proverka("сделанный не попадает", "сделан" in h, False)
    proverka("заголовок «10 из 14»", "Решения · 10 из 14" in h, True)
    mal = pult._boris_desyat(lis[:4])
    proverka("до десяти — без «Позже»", ("Позже" in mal, "Решения · 4<" in mal), (False, True))
    proverka("пусто — пусто", pult._boris_desyat([]), "")
    st_lenta, st_paid = pult._lenta, pult._paid_recently
    try:
        pult._paid_recently = lambda days=30: {}
        pult._lenta = lambda day, who, items: [
            {"kind": "inbox", "id": 6140 + n, "t": "", "text": f"Решить вопрос {n}", "done": 0,
             "note": None, "tag": "обещание", "phone": "", "tries": 0, "prio": 100 + n, "ves": 1}
            for n in range(12)]
        kol = pult._col("Борис", [], True, "2026-11-12", "18:00")
        proverka("в колонке Бориса «Позже · 2»", "Позже · 2" in kol, True)
        proverka("номер пункта виден", "№6140<" in kol, True)
        proverka("блоков дежурной у Бориса нет", "Обзвон базы" in kol or "Хвосты прошлых" in kol, False)
        kol_l = pult._col("Лена", [], True, "2026-11-12", "18:00")
        proverka("у Лены блоки как раньше, без «Позже»", "Позже" in kol_l, False)
        proverka("у Лены номер тоже виден", "№6141<" in kol_l, True)
    finally:
        pult._lenta, pult._paid_recently = st_lenta, st_paid


def main():
    for t in (test_dedup, test_polite, test_proverka_pulta, test_ves, test_bloki, test_audit,
              test_data_gruppy, test_povtor,
              test_zayavki_odin_punkt, test_utro, test_boris_desyat,
              test_vozvrat_1211, test_utro_1211, test_naryad_1211, test_audit_1211, test_zayavki_blok_1211):
        t()
        print()
    if oshibok:
        print(f"ПРОВАЛЕНО проверок: {oshibok}")
        return 1
    print("Все проверки прошли.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
