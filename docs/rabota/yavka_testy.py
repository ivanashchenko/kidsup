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
    deti = {1: "Лебедева София", 2: "Котов Марк", 3: "Степанян Даниелла"}
    t1 = "02.11 ПШ\n18:00\nКотов Марк\nСтепанян Дана\nНе было:\nОрлова София (заморозка)"
    t2 = "ПШ (02.11)\n⏰18:00\nЛебедева София"
    r = _sverka([("2026-11-02T20:30+03:00", t1), ("2026-11-02T21:00+03:00", t2)],
                [_urok(10, 7, "18:00", [(2, True), (3, True)]), _urok(11, 8, "18:00", [(1, True)])],
                deti, {7: "2627_ПШ_пн-чт_18:00_ПШ2 (Гр1)", 8: "2627_ПШ_пн-чт_18:00_ПШ2 (Гр7)"})
    rec = {x["uid"]: x for L in r["lessons"] for x in L["records"]}
    proverka("Лебедева София — «был» по своему отчёту", rec[1]["report"], "был")
    proverka("у неё нет «конфликт / не был»", rec[1]["action"], None)
    proverka("конфликтов нет", [c for L in r["lessons"] for c in L["conflicts"]], [])
    proverka("«Степанян Дана» → Степанян Даниелла (запасной поиск по фамилии работает)", rec[3]["report"], "был")


def main():
    for t in (test_ne_bylo_v_odnu_stroku, test_odno_slovo, test_dva_imeni_odin_rebenok, test_zapasnoy_poisk_tolko_po_familii):
        t()
        print()
    if oshibok:
        print(f"ПРОВАЛЕНО проверок: {oshibok}")
        return 1
    print("Все проверки прошли.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
