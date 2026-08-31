"""«Спросить Клода» — встроенный помощник для администраторов.

Страница /ask отправляет вопрос на /api/ask; сервер собирает системный
промпт из живых данных (цены, группы с остатками, правила центра, короткие
описания курсов) и вызывает Anthropic API. Ключ хранится в настройке
anthropic_api_key, модель — в assistant_model (по умолчанию claude-sonnet-5).

История диалога живёт в браузере администратора и присылается с каждым
запросом — сервер ничего не хранит.
"""

from __future__ import annotations

import httpx

from . import db
from . import descriptions as descr_mod

DEFAULT_MODEL = "claude-sonnet-5"


def _base_url() -> str:
    """api.anthropic.com отвечает серверу 403 «Request not allowed» — хостинг
    в РФ. Поэтому базовый адрес настраиваемый: заводим Cloudflare Worker-прокси
    за пределами РФ и указываем его в anthropic_base_url."""
    return (db.get_setting("anthropic_base_url") or "https://api.anthropic.com").rstrip("/")

RULES = """\
Ты — Клод, помощник администраторов детского центра KidsUP (Москва,
б-р Маршала Рокоссовского, 6 к1В, напротив ТЦ «Янтарь», 2 минуты от
м. Бульвар Рокоссовского, сайт kidsup.ru). Отвечаешь админам на смене:
коротко, по делу, по-русски. Если ответа нет в данных ниже — так и скажи
и укажи, где посмотреть (страницы app.kidsup.ru или спросить Бориса).
Цены и расписание НЕ выдумывай никогда — только из данных ниже.

Ключевые правила центра:
- Учебный год: 31 августа 2026 — 31 мая 2027. Родителю говорим «стартуем
  31 августа».
- Занятия идут с 31 августа по обычному расписанию. Праздник 29.08 и
  День открытых дверей 30.08 УЖЕ ПРОШЛИ — про них не звать и не упоминать.
  Если спрашивают «когда можно прийти посмотреть» — ответ один: на первое
  занятие своей группы, оно условно-бесплатное и с диагностикой; педагог
  смотрит уровень ребёнка и подбирает группу.
- ПЕДАГОГИ. Подготовку к школе в 2026/27 ведут ТАТЬЯНА и ЕЛЕНА. Екатерина
  и Инга у нас больше не работают, причины личные. Если родитель спрашивает
  про них — отвечать прямо и сразу, не увиливать: «Екатерина и Инга у нас
  больше не работают. Подготовку ведут Татьяна и Елена, познакомиться можно
  на первом занятии своей группы». Про методику
  сказать ОДИН раз: чтение идёт по технологии Бураковой, пошагово, поэтому
  мы и даём гарантию. Не повторять по кругу «результат не зависит от
  педагога» — родитель слышит в этом «нам всё равно, кто ведёт».
- ГАРАНТИЯ ЧТЕНИЯ (только подготовка к школе): ребёнок с нуля читает
  трёхбуквенные слова за 3 месяца, иначе занимается бесплатно, пока
  не зачитает. Условия называть сразу: диагностика на первом занятии,
  посещаемость от 80%, домашние задания.
- ДИАГНОСТИКА на первом занятии: педагог говорит родителю, что у ребёнка
  уже хорошо, что стоит подтянуть и как занятия с этим помогут. Это главный
  довод прийти — родитель уходит с пониманием, а не «посмотрели».
- Новые направления набираются в листы ожидания: танцы, хореография, футбол,
  единоборства, акробатика, актёрское мастерство, техника речи. Первой
  группе — скидка 10%.
- Чего НЕ говорить: «бесплатное пробное»; «осталось два места», если это
  не подтверждено данными ниже; «расписание то же» — расписание может
  меняться, обещать «подберём удобное время».
- Первое занятие у кружков УСЛОВНО-БЕСПЛАТНОЕ: не понравилось — клиент не
  платит; понравилось — занятие входит в первый абонемент. Слова
  «бесплатное пробное» не используем. У мини-сада и нулевого класса —
  бесплатный пробный ДЕНЬ, дальше пробная неделя с зачётом стоимости при
  покупке месяца в течение 3 дней.
- До 31.08 включительно сентябрь по ценам прошлого года. Скидки все по 10%:
  −10% на первый абонемент при оплате в день пробного (только новым),
  −10% второй предмет, −10% второй ребёнок, −10% многодетным и семьям
  участников СВО. Скидки не суммируются — действует одна.
- Запись: сначала предмет и уровень («корзина»), потом день и время.
  Английский — по уровням Cambridge (Starters/Movers/Flyers), подготовка
  к школе — по чтению (ПШ1 нечитающие / ПШ2 читающие), не по возрасту.
- Оплата маткапиталом и налоговый вычет — только сад и нулевой класс
  (образовательная лицензия).
- Где что лежит: скрипты и методички — app.kidsup.ru/base; полные
  описания занятий с текстом для WhatsApp — /courses; группы с местами
  и ценами — /enrollment; кого обзванивать — /callplan; готовые
  рассылки — /content.
"""


def _system_prompt(groups: list[dict]) -> str:
    lines = [RULES, "\nГруппы 2026/27 и свободные места сейчас (из CRM):"]
    by: dict[str, list[str]] = {}
    for g in groups:
        if g.get("buffer"):
            continue
        by.setdefault(g["course"], []).append(
            f"{g['day']} {g['time']} ({g['age']}) — свободно {g['free']} из {g['capacity']}")
    for course, items in sorted(by.items()):
        price = next((c["price"] for c in descr_mod.COURSES if c["course"] == course), "")
        lines.append(f"\n• {course}" + (f" — {price}" if price else ""))
        lines += [f"   {x}" for x in items[:14]]
    lines.append("\nКраткие описания курсов (для ответов родителям):")
    for c in descr_mod.COURSES:
        lines.append(f"\n[{c['course']} / {c['key']}] {c['tag']} "
                     f"Возраст: {c['age']}. Расписание: {c['sched']}. Цена: {c['price']}.")
    return "\n".join(lines)


def _headers(key: str) -> dict:
    """Заголовки запроса к API.

    Если базовый адрес — наш прокси на Cloudflare, добавляем общий секрет:
    без него воркер отвечает 403. Иначе воркер был бы открытым прокси,
    которым быстро начнут пользоваться посторонние."""
    h = {"x-api-key": key, "anthropic-version": "2023-06-01",
         "content-type": "application/json"}
    secret = db.get_setting("anthropic_proxy_secret")
    if secret and "api.anthropic.com" not in _base_url():
        h["x-kidsup-auth"] = secret
    return h


def ask(question_history: list[dict], groups: list[dict]) -> dict:
    """history: [{role, content}...] от браузера, последний — вопрос админа."""
    key = db.get_setting("anthropic_api_key")
    if not key:
        return {"error": "no_key",
                "message": "Ключ Anthropic API ещё не настроен. Борис в курсе."}
    model = db.get_setting("assistant_model") or DEFAULT_MODEL
    msgs = [m for m in question_history if m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str) and m["content"].strip()][-20:]
    if not msgs or msgs[-1]["role"] != "user":
        return {"error": "bad_request", "message": "Пустой вопрос."}
    try:
        r = httpx.post(_base_url() + "/v1/messages", timeout=60,
                       headers=_headers(key),
                       json={"model": model, "max_tokens": 1200,
                             "system": _system_prompt(groups),
                             "messages": msgs})
    except Exception as e:  # noqa: BLE001
        return {"error": "network", "message": f"Не удалось достучаться до API: {e}"}
    if r.status_code != 200:
        return {"error": f"api_{r.status_code}", "message": r.text[:300]}
    data = r.json()
    text = "".join(b.get("text", "") for b in data.get("content", []))
    return {"answer": text, "model": data.get("model"),
            "usage": data.get("usage", {})}


def probe() -> dict:
    """Достижим ли api.anthropic.com с сервера (вопрос региона/блокировок)."""
    try:
        key = db.get_setting("anthropic_api_key") or ""
        r = httpx.get(_base_url() + "/v1/models", timeout=12,
                      headers=_headers(key) if key else
                      ({"x-kidsup-auth": db.get_setting("anthropic_proxy_secret")}
                       if db.get_setting("anthropic_proxy_secret") else {}))
        return {"reachable": True, "status": r.status_code, "body": r.text[:300],
                "note": "401 authentication_error = регион ок, нужен только ключ; "
                        "403 с текстом про region/country = блокировка региона"}
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "error": f"{type(e).__name__}: {e}"}
