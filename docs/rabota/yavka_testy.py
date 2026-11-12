# -*- coding: utf-8 -*-
"""Разбор отчётов педагогов о явке (app/yavka.py) — без сервера и без сети.

12.11.2026, 23:45, Лиза: «Клод, очень много ошибок у тебя. Очень много пунктов, где ты
пишешь „по отчёту педагога был / не был, а в базе по-другому“. И каждого смотрю, а ты не
правильно указываешь отчёт от педагога». Сверка всех 59 строк раздела 3 страницы
«Проверка МойКласса» с сырыми отчётами нашла четыре ошибки разбора — здесь каждая на
маленьком примере. Имена выдуманные, шаблоны строк — как пишут педагоги.

  python3 docs/rabota/yavka_testy.py
"""
import os
import sys
import tempfile

sys.path.insert(0, "/home/user/kidsup")
os.environ["KIDSUP_DATA_DIR"] = tempfile.mkdtemp(prefix="yavka_")

from app import yavka  # noqa: E402

oshibok = 0


def proverka(chto, est, nado):
    global oshibok
    ok = est == nado
    if not ok:
        oshibok += 1
    print(("ok   " if ok else "FAIL ") + chto + ("" if ok else f"\n     есть: {est!r}\n     надо: {nado!r}"))


def imena(bloki, spisok):
    return [n for b in bloki for n, _ in b[spisok]]


def test_ne_bylo_v_odnu_stroku():
    """«Не было: А, Б, В» одной строкой — все трое отсутствуют (раньше при > 3 слов — «были»)."""
    t = ("ПШ (05.11)\n⏰17:00\nИванова Аня\n\nНе было: Петров Рома, Сидорова Маша, Козлов Ваня\n\n"
         "⏰18:00\nОрлов Глеб\n\nНе было: Белова Катя")
    b = yavka.parse_report(t, "2026-11-06T12:07:29+03:00")
    proverka("17:00 — пришла одна Иванова Аня", [x["present"] for x in b if x["time"] == "17:00"][0], [("иванова аня", "")])
    proverka("17:00 — трое в «не было»", [n for n, _ in [x for x in b if x["time"] == "17:00"][0]["absent"]],
             ["петров рома", "сидорова маша", "козлов ваня"])
    proverka("18:00 — Орлов был, Белова нет", ([n for n, _ in b[1]["present"]], [n for n, _ in b[1]["absent"]]),
             (["орлов глеб"], ["белова катя"]))
    b = yavka.parse_report("ПШ (29.10)\n⏰17:00\nИванова Аня\n\nНе было: Петров Рома, Орлова Мира", "2026-10-29T21:08+03:00")
    proverka("два имени через запятую после «Не было:» — оба отсутствуют", imena(b, "absent"), ["петров рома", "орлова мира"])
    proverka("…и в «были» их нет", imena(b, "present"), ["иванова аня"])
    # фраза после «Не был» — не список: режим «не было» не включается, имя не выдумывается
    b = yavka.parse_report("ПШ (29.10)\n⏰17:00\nИванова Аня\nНе был на занятии никто из новеньких, все предупредили\nОрлов Глеб",
                           "2026-10-29T21:08+03:00")
    proverka("фраза «Не был на занятии …» не переводит список в «не было»", imena(b, "present"), ["иванова аня", "орлов глеб"])


def test_odno_slovo():
    """Одно слово в отчёте — имя: «Рома» не сходится с фамилией «Романова»."""
    proverka("«Рома» ≠ «Романова Алина»", yavka.name_matches("Рома", "Романова Алина МК"), False)
    proverka("«Рома» = «Ильин Роман»", yavka.name_matches("Рома", "Ильин Роман"), True)
    proverka("«Саша» = «Петрова Александра»", yavka.name_matches("Саша", "Петрова Александра"), True)
    proverka("«Софья» = «Иванова София»", yavka.name_matches("Софья", "Иванова София"), True)
    proverka("«Алексей» ≠ «Алексеева Ольга»", yavka.name_matches("Алексей", "Алексеева Ольга"), False)
    proverka("«Милена» = «Орлова Милана» (опечатка в букву)", yavka.name_matches("Милена", "Орлова Милана"), True)
    # два слова — прежнее правило: опечатки и префиксы фамилий
    proverka("«Бактимиров Кирилл» = «Бахтимиров Кирилл»", yavka.name_matches("Бактимиров Кирилл", "Бахтимиров Кирилл"), True)


def _urok(lid, cid, time, deti, status=1):
    return {"id": lid, "classId": cid, "beginTime": time, "endTime": "", "status": status, "teacherIds": [],
            "records": [{"id": lid * 100 + i, "userId": uid, "visit": byl} for i, (uid, byl) in enumerate(deti)]}


def _sverka(otchety, uroki, deti, klassy):
    """yavka.sverka на подставных данных: отчёты, занятия, имена и названия групп — без базы."""
    st = (yavka._reports, yavka._names, yavka._class_names, yavka._teachers, yavka._save)
    blocks = []
    for i, (ts, text) in enumerate(otchety):
        for b in yavka.parse_report(text, ts):
            b.update({"author": "педагог", "ts": ts, "message_id": f"m{i}"})
            blocks.append(b)
    yavka._reports = lambda day: [b for b in blocks if b["date"] == day]
    yavka._names = lambda uids, mk=None: {u: deti[u] for u in uids}
    yavka._class_names = lambda: klassy
    yavka._teachers = lambda: {}
    yavka._save = lambda day, res: None
    try:
        return yavka.sverka("2026-11-02", lessons=uroki)
    finally:
        yavka._reports, yavka._names, yavka._class_names, yavka._teachers, yavka._save = st


def test_dva_imeni_odin_rebenok():
    """«Алина Романова» в «Были» и «Рома» в «Не было» — у Алины «был», конфликта «двух отчётов» нет."""
    deti = {1: "Романова Алина МК", 2: "Кирова Анна", 3: "Ильина Настя"}
    t = "2/11 английский\n\n⏰ 19:00\nБыли:\nАня\nАлина Романова\n\nНе было:\nНастя\nРома"
    r = _sverka([("2026-11-02T22:39+03:00", t)], [_urok(10, 7, "19:00", [(1, True), (2, True), (3, False)])],
                deti, {7: "2627_АЯ_пн-ср_19:00_9-11 лет"})
    L = r["lessons"][0]
    rec = {x["uid"]: x for x in L["records"]}
    proverka("Алина — «был»", rec[1]["report"], "был")
    proverka("Алина — без действия «конфликт»", rec[1]["action"], None)
    proverka("конфликтов «в одном отчёте был, в другом не был» нет", L["conflicts"], [])
    proverka("Настя — «не был»", rec[3]["report"], "не был")


def test_zapasnoy_poisk_tolko_po_familii():
    """«Орлова София» (её нет в записях) не сходится по имени с Лебедевой Софией из соседней группы."""
    deti = {1: "Лебедева София", 2: "Котов Марк", 3: "Арутюнян Даниелла"}
    t1 = "02.11 ПШ\n18:00\nКотов Марк\nАрутюнян Дана\nНе было:\nОрлова София (заморозка)"
    t2 = "ПШ (02.11)\n⏰18:00\nЛебедева София"
    r = _sverka([("2026-11-02T20:30+03:00", t1), ("2026-11-02T21:00+03:00", t2)],
                [_urok(10, 7, "18:00", [(2, True), (3, True)]), _urok(11, 8, "18:00", [(1, True)])],
                deti, {7: "2627_ПШ_пн-чт_18:00_ПШ2 (Гр1)", 8: "2627_ПШ_пн-чт_18:00_ПШ2 (Гр7)"})
    rec = {x["uid"]: x for L in r["lessons"] for x in L["records"]}
    proverka("Лебедева София — «был» по своему отчёту", rec[1]["report"], "был")
    proverka("у неё нет «конфликт / не был»", rec[1]["action"], None)
    proverka("конфликтов нет", [c for L in r["lessons"] for c in L["conflicts"]], [])
    proverka("«Арутюнян Дана» → Арутюнян Даниелла (запасной поиск по фамилии работает)", rec[3]["report"], "был")


def test_sverka_311_otchetov():
    """Шаблоны, найденные сверкой всех 311 отчётов 01.10–12.11 (имена выдуманные)."""
    b = yavka.parse_report("11.10 МА\n14:00\nИванова Аня\nПетров Рома (не было)\nОрлова Мира (не было, заморозка)",
                           "2026-10-11T18:53+03:00")
    proverka("«(не было)» в скобках после имени — в «не было»", (imena(b, "present"), imena(b, "absent")),
             (["иванова аня"], ["петров рома", "орлова мира"]))
    b = yavka.parse_report("ПШ (26.10)\n⏰17:00\nСадовников Петя\nИванова Аня\n\nНе было: Козлов Ваня", "2026-10-26T20:07+03:00")
    proverka("фамилия на «Сад…» не режется как «сад»", imena(b, "present"), ["садовников петя", "иванова аня"])
    b = yavka.parse_report("12.10 изостудия\n19.00\nЕва\nЯна", "2026-10-12T20:33+03:00")
    proverka("имена из трёх букв не выпадают", imena(b, "present"), ["ева", "яна"])
    b = yavka.parse_report("19.10 Нулевой класс (не отправилось)\n\nИванова Аня\nПетров Рома (пробный час)", "2026-10-22T08:43+03:00")
    proverka("отчёт, присланный через 3 дня, — за свой день, а не за день отправки", {x["date"] for x in b}, {"2026-10-19"})
    proverka("…и «19.10» — дата, а не слот 19:10", {x["time"] for x in b}, {None})
    b = yavka.parse_report("8.10. Мини -сад\nИванова Аня", "2026-10-08T13:17+03:00")
    proverka("«Мини -сад» с пробелом — предмет, «8.10.» — дата", (b[0]["subj"], b[0]["time"], b[0]["date"]),
             (["Мини-сад"], None, "2026-10-08"))
    b = yavka.parse_report("1.11 праздник музыка с мамой\n16.30\nИванова Аня\n31.10\n17.30\nПетров Рома", "2026-11-01T17:35+03:00")
    proverka("вторая дата в сообщении — следующие слоты за неё", [(x["date"], x["time"]) for x in b],
             [("2026-11-01", "16:30"), ("2026-10-31", "17:30")])
    b = yavka.parse_report("24.10 музыка с мамой 9.45\nИванова Аня", "2026-10-24T19:39+03:00")
    proverka("время в строке заголовка после даты и предмета", b[0]["time"], "09:45")
    b = yavka.parse_report("03.11 Логопед\n18:00\nИванова Аня\n19 20\nПетров Рома", "2026-11-03T20:29+03:00")
    proverka("слот «19 20» через пробел — свой слот", [(x["time"], imena([x], "present")) for x in b],
             [("18:00", ["иванова аня"]), ("19:20", ["петров рома"])])
    b = yavka.parse_report("13.10 английский\n19:00\nПолина Белова. Ксения Лубова", "2026-10-13T21:17+03:00")
    proverka("точка внутри списка — как запятая", imena(b, "present"), ["полина белова", "ксения лубова"])
    b = yavka.parse_report("ПШ (09.10)\n16:00\nИванова Аня (заморозка), Петров Рома", "2026-10-09T20:48+03:00")
    proverka("пометка в скобках — только у своего имени", [x for x in b[0]["present"]],
             [("иванова аня", "заморозка"), ("петров рома", "")])
    b = yavka.parse_report("04.11 логопед\nНе было:\nОрлов Глеб (перенесли его на вечер", "2026-11-04T14:57+03:00")
    proverka("незакрытая скобка — строка не теряется", imena(b, "absent"), ["орлов глеб"])
    b = yavka.parse_report("Петров Рома\nИванова Аня\nКозлов Ваня", "2026-10-18T00:24+03:00")
    proverka("продолжение без даты после полуночи — за вчера", {x["date"] for x in b}, {"2026-10-17"})


def test_zapasnoy_poisk_imya_ne_sporit():
    """Фамилия совпала, а имя другое — это не тот ребёнок (сёстры; опечатка в фамилии)."""
    deti = {1: "Орлова Виктория", 2: "Кулагин Михаил"}
    t1 = "14.10 логопед\nАлиса\nНе было\nОрлова Дарья заболел зуб\nКульгин Максим болеют"
    t2 = "14.10 запуск речи\nОрлова В\nКулагин Михаил"
    r = _sverka([("2026-11-02T13:29+03:00", t2.replace("14.10", "02.11")), ("2026-11-02T20:30+03:00", t1.replace("14.10", "02.11"))],
                [_urok(10, 7, "09:30", [(1, True)]), _urok(11, 8, "12:00", [(2, True)])],
                deti, {7: "2627_ЛГ Елена_Ср_09:30", 8: "2627_ЛГ Елена_Ср_12:00"})
    rec = {x["uid"]: x for L in r["lessons"] for x in L["records"]}
    proverka("Орлова Дарья (не было) не легла на Орлову Викторию", (rec[1]["report"], rec[1]["action"]), ("был", None))
    proverka("Кульгин Максим (не было) не лёг на Кулагина Михаила", (rec[2]["report"], rec[2]["action"]), ("был", None))
    proverka("конфликтов нет", [c for L in r["lessons"] for c in L["conflicts"]], [])
    # перенос на другое время — не пропуск этого занятия
    deti = {1: "Орлов Глеб"}
    r = _sverka([("2026-11-02T14:57+03:00", "02.11 логопед\nНе было:\nОрлов Глеб (перенесли его на вечер)"),
                 ("2026-11-02T19:39+03:00", "02.11 логопед\n19:00\nОрлов Глеб")],
                [_urok(10, 7, "19:00", [(1, True)])], deti, {7: "2627_ЛГ Елена_Ср_19:00"})
    L = r["lessons"][0]
    proverka("«перенесли на вечер» не спорит с «был» вечером", (L["records"][0]["report"], L["conflicts"]), ("был", []))
    proverka("у отметки видно, из какого отчёта она", [(o["ts"][:16], o["imya"]) for o in L["records"][0]["otchety"]],
             [("2026-11-02T19:39", "орлов глеб")])


def main():
    for t in (test_ne_bylo_v_odnu_stroku, test_odno_slovo, test_dva_imeni_odin_rebenok, test_zapasnoy_poisk_tolko_po_familii,
              test_sverka_311_otchetov, test_zapasnoy_poisk_imya_ne_sporit):
        t()
        print()
    if oshibok:
        print(f"ПРОВАЛЕНО проверок: {oshibok}")
        return 1
    print("Все проверки прошли.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
