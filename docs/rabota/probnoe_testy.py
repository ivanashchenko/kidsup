# -*- coding: utf-8 -*-
"""Условия первого занятия в сообщениях семьям (app/autopilot._trial_terms).

17.10.2026, Ира: «У логопеда нет условно-бесплатных занятий. Ты прислал
неправильно…» — подтверждение записи к логопеду обещало условно-бесплатное
занятие. Здесь — что пишем по каждому направлению.

  python3 docs/rabota/probnoe_testy.py
"""
import os
import sys

sys.path.insert(0, "/home/user/kidsup")
# 12.11.2026, личные входы: без пароля сайт открыт только в режиме без входа
os.environ["KIDSUP_BEZ_VHODA"] = "1"

from app import autopilot as ap  # noqa: E402

D = "Первое занятие условно-бесплатное."
oshibok = 0


def proverka(nazvanie, ok):
    global oshibok
    oshibok += not ok
    print(("  OK   " if ok else "  ОШИБКА "), nazvanie)


def t(*names):
    return ap._trial_terms(list(names), D)


for n in ("ЛГ Татьяна · Вт · 16:00", "2627_ЛГ Елена_Пн_17:00", "Логопед"):
    r = t(n)
    proverka(f"логопед «{n}»: платно, без «условно-бесплатного» и «в зачёт»",
             "платные" in r and "условно" not in r and "зачёт" not in r)
for n in ("Мини-сад · 9:00-13:00", "Нулевой класс · 10:00-14:00"):
    r = t(n)
    proverka(f"«{n}»: бесплатное знакомство, без «условно-бесплатного» и без «дня»",
             "бесплатн" in r and "условно" not in r and "день" not in r)
proverka("ИЗО — платное пробное 850 ₽", "850" in t("ИЗО-студия · Группа 1") and "условно" not in t("ИЗО-студия · Группа 1"))
proverka("робототехника — 1 600 ₽", "1 600" in t("Робототехника пт 17:00"))
proverka("английский — условно-бесплатное", t("Английский язык · пн-ср · 17:00") == D)
proverka("Лицей для малышей — условно-бесплатное", t("РР.Лицей_ср-пт_16:00") == D)
r = t("ЛГ Марина_Сб_09:10", "Английский язык")
proverka("логопед и английский в одном сообщении — оба условия", "платные" in r and D in r)
proverka("слово «лг» внутри других слов не ловится", t("Английский язык (Гр7)") == D)


def test_robototehnika_po_dnyam():
    """13.11.2026, Ира: робототехника в бланках — отдельно те, кто ходит по пт, и те, кто по вс.
    Группа с разным временем по дням становится вкладкой по дням; пробные остаются в списке
    своего дня; группы, где дети ходят оба дня (ПШ, шахматы), не трогаем."""
    from app import blank_grupp as B

    def gruppa(cid, kurs, dni, vremya, deti):
        return {"kind": "group", "classId": cid, "key": f"g{cid}", "course": kurs, "cap": 8,
                "header": f"({dni}) {vremya} (4-7)", "time": vremya, "hpre": f"({dni})", "hpost": "(4-7)",
                "g": {"num": "", "days": dni, "time": vremya, "age": "4-7", "notes": ""},
                "kids": [{"id": f"r{cid}{i}", "uid": 900 + i, "name": imya, "days": d, "mark": m}
                         for i, (imya, d, m) in enumerate(deti)]}

    m = {"tabs": [
        {"sheet": "Робототехника", "title": "Робототехника", "teacher": "", "slots": 10, "blocks": [
            gruppa(1, "Робототехника", "пт-вс", "16.00/14.30",
                   [("Иванов Петя", "пт", ""), ("Сидорова Маша", "вс", ""), ("Кузнецов Лёва", "вс", "п (15.11)")]),
            gruppa(2, "Робототехника", "пт-вс", "17.00/15.30", []),
            {"kind": "wait", "key": "waitР", "header": "ЛИСТ ОЖИДАНИЯ", "kids": []}]},
        {"sheet": "ПШ", "title": "ПШ", "teacher": "Анна Тестова", "slots": 10, "blocks": [
            gruppa(3, "Подготовка к школе", "пн-сб", "19.00/10.00", [("Петров Ваня", "пн-сб", "")])]},
    ]}
    B._day_tabs(m, {})
    po_dnyam = [t for t in m["tabs"] if t.get("by_days")]
    proverka("робототехника с детьми стала вкладкой по дням", len(po_dnyam) == 1)
    dni = {b["header"]: [k["name"] for k in b["kids"]] for b in po_dnyam[0]["blocks"] if b["kind"] == "day"}
    proverka("два списка: ПТ 16.00 и ВС 14.30", list(dni) == ["ПТ 16.00", "ВС 14.30"])
    proverka("в пятнице — только пятничные", dni.get("ПТ 16.00") == ["Иванов Петя"])
    proverka("в воскресенье — воскресные, пробный тоже", dni.get("ВС 14.30") == ["Кузнецов Лёва", "Сидорова Маша"])
    ostalis = [b.get("key") for t in m["tabs"] if not t.get("by_days") for b in t["blocks"] if b["kind"] == "group"]
    proverka("пустая группа робототехники и ПШ (дети ходят оба дня) остались обычными блоками",
          ostalis == ["g2", "g3"])


test_robototehnika_po_dnyam()

print("\nошибок:", oshibok)
sys.exit(1 if oshibok else 0)
