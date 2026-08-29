"""Разбор бумажных анкет с праздника 29.08.2026.

Распознаёт анкеты не этот модуль — их читает Клод глазами и передаёт сюда
готовые словари. Модуль отвечает за то, что дальше: найти семью в CRM или
завести, проставить тег и статус, не потерять отмеченные направления.

Правила, ради которых он существует (решение владельца 29.08.2026):
  * телефон в CRM хранится ТОЛЬКО как 7XXXXXXXXXX — без плюса, без восьмёрки;
    в этом виде поиск по базе работает предсказуемо;
  * нашли семью — просто вешаем тег, статус не трогаем: человек уже в воронке
    и у него своя история;
  * не нашли — заводим карточку сразу в статусе «1.2. Праздник 2026»;
  * карточка называется по ребёнку (имя + фамилия), родитель уходит в атрибут.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .moyklass_client import MoyklassClient

TAG_PRAZDNIK = 118871          # тег «Праздник 2026»
STATUS_PRAZDNIK = 349497       # статус «1.2. Праздник 2026»
ATTR_PARENT = 2                # «Родитель (имя, телефон)»
ATTR_CLIENT_TYPE = 4           # «Тип клиента», multiselect
ATTR_PHONE2 = 6606
ATTR_PHONE3 = 7173

# Пункты бумажной анкеты → варианты атрибута «Тип клиента».
# Справочник владелец переименовал 29.08.2026: раннее развитие разделилось на
# «Музыка и речь» и «Первую школу», добавился «Лицей для малышей» — ключи ниже
# повторяют формулировки анкеты дословно, чтобы при разборе не гадать.
INTERES = {
    # блок «1-3 года»
    "раннее развитие. первая школа": 10422,
    "раннее развитие. музыка и речь": 15995,
    "мини-сад": 4971,
    # блок «3 года»
    "лицей для малышей": 66263,
    "алфавитная живопись": 10434,
    "логопед": 10435,
    "английский язык": 10425,
    "танцы": 10430,
    # блок «4-7 лет»
    "подготовка к школе": 10419,
    "нулевой класс": 4970,
    "изостудия": 10434,
    "ментальная арифметика": 10427,
    "шахматы": 10426,
    # блок «7-12 лет»
    "скорочтение": 10432,
    "коррекция почерка": 10433,
    # встречается в свободной форме
    "робототехника": 10428,
    "английский летний клуб": 10436,
}

# статусы, из которых человека, пришедшего на праздник, надо вернуть в работу
COLD = {345759, 125956, 345768}   # архив набора, неактивный клиент, недозвон
NEVER_TOUCH = {146328, 215202, 125957}  # не писать, не работаем, отказ


def normalize_phone(raw: str | None) -> str | None:
    """К единому виду 7XXXXXXXXXX. Возвращает None, если номер не российский
    мобильный — такие идут в ручной разбор, а не в базу."""
    if not raw:
        return None
    d = re.sub(r"\D", "", str(raw))
    if len(d) == 11 and d[0] == "8":
        d = "7" + d[1:]
    elif len(d) == 10 and d[0] == "9":
        d = "7" + d
    return d if re.fullmatch(r"79\d{9}", d) else None


def find_user(mk: MoyklassClient, phone: str) -> dict | None:
    """Поиск семьи по номеру. Штатный users?phone= иногда не находит
    существующую карточку, поэтому проверяем ещё и хвост номера, и запасные
    телефоны в атрибутах."""
    for params in ({"phone": phone}, {"phone": phone[-10:]}):
        try:
            r = mk.get("/v1/company/users", params={**params, "limit": 5})
        except Exception:
            continue
        users = (r.get("users") if isinstance(r, dict) else r) or []
        for u in users:
            if normalize_phone(u.get("phone")) == phone:
                return u
        if users:
            return users[0]
    return None


def _attr_values(user: dict, attribute_id: int) -> list[int]:
    for a in user.get("attributes") or []:
        if a.get("attributeId") == attribute_id:
            return list(a.get("valueIds") or [])
    return []


def add_tag(mk: MoyklassClient, user_id: int, tag_id: int = TAG_PRAZDNIK) -> None:
    u = mk.get(f"/v1/company/users/{user_id}")
    tags = [t["id"] if isinstance(t, dict) else t for t in (u.get("tags") or [])]
    if tag_id not in tags:
        mk.post(f"/v1/company/users/{user_id}/tags", {"tags": sorted(set(tags) | {tag_id})})


def merge_interests(mk: MoyklassClient, user_id: int, interests: list[str]) -> list[int]:
    """Отмеченные направления добавляем к уже проставленным, а не затираем."""
    ids = {INTERES[k] for k in interests if k in INTERES}
    if not ids:
        return []
    cur = mk.get(f"/v1/company/users/{user_id}")
    have = set(_attr_values(cur, ATTR_CLIENT_TYPE))
    if ids <= have:
        return sorted(have)
    mk.safe_update_user(user_id, attributes=[
        {"attributeId": ATTR_CLIENT_TYPE, "valueIds": sorted(have | ids)}
    ])
    return sorted(have | ids)


def comment_text(a: dict) -> str:
    """Что именно семья написала на анкете — одной записью в карточке."""
    parts = ["Анкета с праздника 29.08.2026."]
    if a.get("parent"):
        parts.append(f"Родитель: {a['parent']}.")
    if a.get("child_age"):
        parts.append(f"Возраст ребёнка: {a['child_age']}.")
    if a.get("interests"):
        parts.append("Отметили направления: " + ", ".join(a["interests"]) + ".")
    if a.get("time"):
        parts.append(f"Удобное время: {a['time']}.")
    if a.get("contact_way"):
        parts.append(f"Как связаться: {a['contact_way']}.")
    if a.get("was_client"):
        parts.append("Отметили, что уже занимались в KidsUP.")
    if a.get("wants_dod"):
        parts.append("Просили записать на открытый урок 30.08.")
    if a.get("toy"):
        parts.append(f"Игрушка из барабана № {a['toy']}.")
    if a.get("note"):
        parts.append(f"Дописали от руки: {a['note']}")
    return " ".join(parts)


def process(mk: MoyklassClient, a: dict, duty_manager: int | None = None) -> dict:
    """Одна анкета. Возвращает, что сделано — для итогового отчёта."""
    phone = normalize_phone(a.get("phone"))
    if not phone:
        return {"status": "нет телефона", "anketa": a}

    user = find_user(mk, phone)
    res = {"phone": phone, "child": a.get("child")}

    if user:
        uid = user["id"]
        res["status"] = "нашли"
        res["user_id"] = uid
        res["name"] = user.get("name")
        add_tag(mk, uid)
        if user.get("clientStateId") in COLD:
            mk.post(f"/v1/company/users/{uid}/status", {"statusId": STATUS_PRAZDNIK})
            res["status"] = "нашли, вернули в воронку"
    else:
        body = {"name": a.get("child") or f"Анкета {phone[-4:]}",
                "phone": phone, "clientStateId": STATUS_PRAZDNIK}
        attrs = []
        if a.get("parent"):
            attrs.append({"attributeId": ATTR_PARENT, "value": a["parent"]})
        ids = {INTERES[k] for k in (a.get("interests") or []) if k in INTERES}
        if ids:
            attrs.append({"attributeId": ATTR_CLIENT_TYPE, "valueIds": sorted(ids)})
        if attrs:
            body["attributes"] = attrs
        created = mk.post("/v1/company/users", body)
        uid = created.get("id")
        res.update({"status": "завели", "user_id": uid, "name": body["name"]})
        add_tag(mk, uid)

    if user:
        merge_interests(mk, uid, a.get("interests") or [])
    mk.post("/v1/company/userComments",
            {"userId": uid, "comment": comment_text(a), "showToUser": False})

    if a.get("wants_dod") and duty_manager:
        now = datetime.utcnow()
        mk.post("/v1/company/tasks", {
            "body": (f"{res.get('name')} ({phone}): на анкете праздника отметили открытый урок "
                     f"30.08 — позвонить, подобрать время и записать.")[:250],
            "managerIds": [duty_manager], "userId": uid,
            "beginDate": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "endDate": (now + timedelta(hours=16)).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "categoryId": 44337})
        res["task"] = True
    return res
