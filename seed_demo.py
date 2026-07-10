"""Заполняет базу демо-данными, чтобы посмотреть интерфейс без API-ключа.

Запуск:  python seed_demo.py
Очистка: удалите файл data/kidsup.db и синхронизируйтесь с МойКласс.
"""

import random
from datetime import date, datetime, timedelta

from app import db

random.seed(42)

FIRST = ["Аня", "Миша", "Соня", "Тимур", "Лиза", "Артём", "Вера", "Кирилл",
         "Даша", "Лев", "Полина", "Максим", "Ева", "Захар", "Мария", "Фёдор"]
LAST = ["Иванова", "Петров", "Смирнова", "Кузнецов", "Соколова", "Попов",
        "Лебедева", "Козлов", "Новикова", "Морозов", "Волкова", "Павлов"]

GROUPS = [
    {"id": 1, "name": "Робототехника 6–8 лет", "maxStudents": 10},
    {"id": 2, "name": "Программирование Scratch", "maxStudents": 12},
    {"id": 3, "name": "Английский для малышей", "maxStudents": 8},
    {"id": 4, "name": "Шахматы", "maxStudents": 14},
    {"id": 5, "name": "Рисование", "maxStudents": 10},
]


def main():
    db.init_db()
    today = date.today()

    db.save_simple("filials", [{"id": 1, "name": "Центр на Ленина, 10"}])
    db.save_simple("courses", [{"id": g["id"], "name": g["name"]} for g in GROUPS])
    db.save_classes([
        {"id": g["id"], "name": g["name"], "courseId": g["id"], "filialId": 1,
         "status": "active", "maxStudents": g["maxStudents"]}
        for g in GROUPS
    ])

    # ученики за последние 18 месяцев
    users = []
    for uid in range(1, 121):
        created = today - timedelta(days=random.randint(0, 540))
        users.append({
            "id": uid,
            "name": f"{random.choice(FIRST)} {random.choice(LAST)}",
            "phone": f"+7 9{random.randint(10, 99)} {random.randint(100, 999)}-"
                     f"{random.randint(10, 99)}-{random.randint(10, 99)}",
            "email": f"parent{uid}@example.com",
            "balans": random.choice([0, 0, 0, 1500, 3000, -1200, -2400, -800, 500]),
            "clientStateId": random.choice([1, 1, 1, 2]),
            "createdAt": created.isoformat() + "T10:00:00",
            "updatedAt": today.isoformat() + "T10:00:00",
        })
    db.save_users(users)

    # платежи за 14 месяцев
    payments = []
    pid = 1
    for u in users:
        created = datetime.fromisoformat(u["createdAt"]).date()
        months_active = random.randint(1, 14)
        for m in range(months_active):
            d = created + timedelta(days=30 * m + random.randint(0, 5))
            if d > today:
                break
            amount = random.choice([3600, 4800, 5200, 6400])
            payments.append({"id": pid, "userId": u["id"],
                             "date": d.isoformat(), "summa": amount,
                             "optype": "income", "comment": "Оплата абонемента"})
            pid += 1
            for w in range(4):
                dd = d + timedelta(days=7 * w)
                if dd > today:
                    break
                payments.append({"id": pid, "userId": u["id"],
                                 "date": dd.isoformat(), "summa": amount / 4,
                                 "optype": "debit", "comment": "Списание за занятие"})
                pid += 1
            if random.random() < 0.02:
                payments.append({"id": pid, "userId": u["id"],
                                 "date": d.isoformat(), "summa": amount / 2,
                                 "optype": "refund", "comment": "Возврат"})
                pid += 1
    db.save_payments(payments)

    # занятия и посещаемость за 6 месяцев
    lessons = []
    lid, rid = 1, 1
    group_students = {
        g["id"]: random.sample(range(1, 121), k=random.randint(6, g["maxStudents"]))
        for g in GROUPS
    }
    for g in GROUPS:
        d = today - timedelta(days=180)
        while d <= today + timedelta(days=14):
            if d.weekday() in (1, 4):  # вт и пт
                records = []
                if d <= today:
                    for uid in group_students[g["id"]]:
                        records.append({"id": rid, "userId": uid,
                                        "visit": random.random() < 0.82})
                        rid += 1
                lessons.append({
                    "id": lid, "date": d.isoformat(), "beginTime": "17:00",
                    "endTime": "18:00", "classId": g["id"], "filialId": 1,
                    "status": 1 if d <= today else 0,
                    "topic": f"Занятие №{lid}", "records": records,
                })
                lid += 1
            d += timedelta(days=1)
    db.save_lessons(lessons)

    db.set_state("last_sync", "демо-данные")
    print("Демо-данные загружены:", db.table_counts())


if __name__ == "__main__":
    main()
