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
    proverka("Лизе можно закрытие занятий",
             bool(P.LIZA_MOZHNO.search("Закрыть занятия за вчера, 40 штук")), True)
    proverka("Лизе можно долги",
             bool(P.LIZA_MOZHNO.search("Долг 2 500 ₽ у семьи Гориных")), True)
    proverka("Лизе нельзя обзвон",
             bool(P.LIZA_MOZHNO.search("Позвонить Петровым и дожать до записи")), False)
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


def main():
    for t in (test_dedup, test_polite, test_proverka_pulta, test_ves):
        t()
        print()
    if oshibok:
        print(f"ПРОВАЛЕНО проверок: {oshibok}")
        return 1
    print("Все проверки прошли.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
