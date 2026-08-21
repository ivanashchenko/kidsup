"""Разбор смыслом: обращение к модели там, где правила по словам врут.

Зачем. Маршрут задачи выбирался поиском по словам, и на границе это
давало ошибки, которые видел владелец: «заявка с сайта» уезжала к нему
из-за слова «сайт», «договорились» срабатывало как «договор», а «кто
ведёт» перевешивало «прислать программу». Слова не несут смысла —
их сочетание несёт.

Как устроено. Модель отвечает строго структурой (tool use), поэтому
разбирать её текст не нужно и нечему ломаться. Ответ кэшируется по
содержанию: одна и та же задача не спрашивается дважды.

Границы, которые модель НЕ переходит:
  · она ничего не меняет сама — только называет решение, а применяют его
    те же функции с теми же предохранителями;
  · если она недоступна или ответила невнятно, работает прежнее правило
    по словам: система не должна вставать из-за чужого сбоя;
  · спорные случаи она может закрыть, но денежные и владельческие решения
    по-прежнему уходят человеку.

Запуск:
    python -m app.brain check     — жива ли связь
    python -m app.brain task "текст задачи"
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time

import httpx

from . import db

log = logging.getLogger("kidsup.brain")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"
CACHE = f"{SP}/brain_cache.json"

DEFAULT_MODEL = "claude-sonnet-5"
TIMEOUT = 40
MAX_RETRY = 2


def enabled() -> bool:
    return bool(db.get_setting("anthropic_api_key"))


def _base() -> str:
    return (db.get_setting("anthropic_base_url")
            or "https://api.anthropic.com").rstrip("/")


def _headers() -> dict:
    h = {"x-api-key": db.get_setting("anthropic_api_key") or "",
         "anthropic-version": "2023-06-01",
         "content-type": "application/json"}
    secret = db.get_setting("anthropic_proxy_secret")
    if secret and "api.anthropic.com" not in _base():
        h["x-kidsup-auth"] = secret
    return h


def _cache_load() -> dict:
    try:
        return json.load(open(CACHE))
    except Exception:
        return {}


def _cache_save(d: dict) -> None:
    try:
        # Кэш не должен расти бесконечно: держим последние 3000 ответов.
        if len(d) > 3000:
            d = dict(list(d.items())[-3000:])
        json.dump(d, open(CACHE, "w"), ensure_ascii=False)
    except Exception:
        pass


def ask(system: str, user: str, schema: dict, name: str = "answer",
        model: str | None = None, cache: bool = True) -> dict | None:
    """Спросить модель и получить строго структурированный ответ.

    Возвращает None, если связи нет или ответ не разобрался, — вызывающий
    код обязан уметь работать без модели."""
    if not enabled():
        return None
    key = hashlib.sha256(
        f"{name}|{system}|{user}|{json.dumps(schema, sort_keys=True)}"
        .encode()).hexdigest()[:32]
    store = _cache_load() if cache else {}
    if cache and key in store:
        return store[key]

    # Имя инструмента и ключи схемы — только латиница: API отклоняет
    # кириллические имена, а ошибка приходит как пустой ответ, и снаружи
    # это выглядит как «модель не работает».
    body = {
        "model": model or db.get_setting("assistant_model") or DEFAULT_MODEL,
        "max_tokens": 900,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "tools": [{"name": name, "description": "Верни разбор в этой структуре.",
                   "input_schema": schema}],
        "tool_choice": {"type": "tool", "name": name},
    }
    for attempt in range(MAX_RETRY + 1):
        try:
            r = httpx.post(_base() + "/v1/messages", timeout=TIMEOUT,
                           headers=_headers(), json=body)
        except Exception as e:
            log.warning("brain: сеть не дала ответа (%s)", type(e).__name__)
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            # Перегрузка на той стороне — подождать и повторить.
            time.sleep(2.0 * (attempt + 1))
            continue
        if r.status_code != 200:
            log.warning("brain: %s %s", r.status_code, r.text[:180])
            return None
        try:
            for block in r.json().get("content", []):
                if block.get("type") == "tool_use":
                    out = block.get("input") or {}
                    if cache:
                        store[key] = out
                        _cache_save(store)
                    return out
        except Exception:
            log.warning("brain: ответ не разобрался")
        return None
    return None


# --- маршрут задачи ---------------------------------------------------------

ROUTE_SYSTEM = """\
Ты разбираешь задачи в CRM детского центра KidsUP и решаешь, КТО должен
её выполнить. Отвечай по существу задачи, а не по отдельным словам в ней.

Кто есть кто:
· ВЛАДЕЛЕЦ (Борис) — только то, чего сотрудник физически не может сделать:
  доступы и пароли, деньги компании, наём и увольнение, аренда, реклама,
  договоры с юрлицами, правки сайта, решения о ценах и педагогах.
· ЛИЗА — переписка в мессенджерах и всё, что связано с оплатами клиентов:
  выставить счёт, прислать ссылку на оплату, разобраться с возвратом,
  ответить в чате.
· ДЕЖУРНЫЙ АДМИНИСТРАТОР — звонки клиентам, запись в группы, подтверждение
  приходов, работа с карточками.

Главное правило: задачу получает тот, кто её ИСПОЛНЯЕТ. Если для исполнения
не хватает факта, который знает только владелец (например, имя педагога),
это не делает задачу владельческой — факт надо вписать в текст, а отправить
задачу исполнителю.

Если по тексту нельзя понять, что делать, — так и скажи, не угадывай."""

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "who": {"type": "string", "enum": ["owner", "liza", "duty", "unclear"],
                "description": "owner — владелец Борис, liza — переписка "
                               "и оплаты, duty — дежурный администратор"},
        "why": {"type": "string",
                "description": "одна фраза по-русски для человека, без терминов"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "action": {"type": "string",
                   "description": "суть задачи одной фразой по-русски"},
    },
    "required": ["who", "why", "confidence"],
}

_WHO = {"owner": "владелец", "liza": "лиза", "duty": "дежурный",
        "unclear": "непонятно"}
_CONF = {"high": "высокая", "medium": "средняя", "low": "низкая"}


def route_task(body: str) -> dict | None:
    """Кому эта задача. None — если модель недоступна."""
    if not (body or "").strip():
        return None
    r = ask(ROUTE_SYSTEM, f"Задача:\n{body[:900]}", ROUTE_SCHEMA, "route")
    if not r:
        return None
    return {"кому": _WHO.get(r.get("who"), "непонятно"),
            "почему": r.get("why", ""),
            "уверенность": _CONF.get(r.get("confidence"), "низкая"),
            "что_сделать": r.get("action", "")}


# --- намерение клиента в переписке ------------------------------------------

INTENT_SYSTEM = """\
Ты разбираешь переписку родителя с детским центром и определяешь, чего
человек хочет и что должен сделать администратор дальше.

Центр: KidsUP, Москва, Бульвар Рокоссовского. Предметы: подготовка к школе,
английский, музыка и речь, раннее развитие, мини-сад, нулевой класс, ИЗО,
шахматы, ментальная арифметика, логопед. Учебный год с 31 августа.

Отвечай по смыслу переписки целиком, а не по последнему сообщению.
Если человек прощается или благодарит, это не значит, что вопрос закрыт."""

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string",
                   "enum": ["signup", "price", "schedule", "reschedule",
                            "refusal", "complaint", "payment", "lesson_question",
                            "smalltalk", "unclear"]},
        "urgency": {"type": "string", "enum": ["burning", "normal", "can_wait"]},
        "waiting": {"type": "boolean",
                    "description": "нужен ли наш ответ прямо сейчас"},
        "subject": {"type": "string", "description": "предмет, если назван"},
        "next_step": {"type": "string",
                      "description": "что сделать администратору, одной фразой "
                                     "по-русски"},
    },
    "required": ["intent", "urgency", "waiting", "next_step"],
}

_INTENT = {"signup": "записаться", "price": "узнать цену",
           "schedule": "узнать расписание", "reschedule": "перенести",
           "refusal": "отказ", "complaint": "жалоба", "payment": "оплата",
           "lesson_question": "вопрос по занятиям", "smalltalk": "болтовня",
           "unclear": "непонятно"}
_URG = {"burning": "горит", "normal": "обычная", "can_wait": "может ждать"}


def read_dialog(messages: list[dict]) -> dict | None:
    """messages: [{dir: in|out, text}] — переписка по одному клиенту."""
    if not messages:
        return None
    lines = []
    for m in messages[-14:]:
        who = "Клиент" if m.get("dir") == "in" else "Мы"
        lines.append(f"{who}: {(m.get('text') or '').strip()[:220]}")
    r = ask(INTENT_SYSTEM, "\n".join(lines), INTENT_SCHEMA, "intent")
    if not r:
        return None
    return {"намерение": _INTENT.get(r.get("intent"), "непонятно"),
            "срочность": _URG.get(r.get("urgency"), "обычная"),
            "ждёт_ответа": bool(r.get("waiting")),
            "предмет": r.get("subject", ""),
            "следующий_шаг": r.get("next_step", "")}


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "check":
        print("ключ вписан:", enabled(), "| адрес:", _base())
        r = ask("Отвечай кратко.", "Скажи одно слово: работает",
                {"type": "object", "properties": {"ответ": {"type": "string"}},
                 "required": ["ответ"]}, "check", cache=False)
        print("ответ модели:", r)
    elif cmd == "task":
        print(json.dumps(route_task(" ".join(sys.argv[2:])),
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
