"""Наряд: строки живых списков превращаются в пункты дежурной.

21.09.2026. Борис, глядя на блок «Заявки, где мы не доделали»: «у кого они
сегодня на прозвоне??!!» — ни у кого. Блоки внизу пульта (заявки, места,
воронка) всё это время были справочными: они честно показывали, что работа
есть, но никому её не назначали. Из 58 строк заявок сезона в чьих-то задачах
лежало восемь.

Здесь каждая строка получает хозяина. Раз в час в рабочее окно берём живые
списки, выкидываем то, что уже стоит в колонке или в обещаниях, и кладём
остаток в инбокс дежурной — по одному пункту на семью, с телефоном, сроком
и тем, что именно сделать. Только чтение CRM и запись в наш инбокс: ни
сообщений, ни статусов, ни задач в МойКлассе.
"""
from __future__ import annotations

import json
import logging
import re

from . import db

log = logging.getLogger(__name__)

# Сколько пунктов в день кладём максимум — чтобы наряд помогал, а не заваливал.
# Звонки — главное, хвосты в CRM идут малой порцией, они не приносят денег сразу.
LIMIT_CALLS = 12
LIMIT_TAILS = 6

# «Спасибо», «поняла», «👍» — это конец разговора, а не вопрос. Такие
# сообщения в наряд не идут: админ и так их видит на /waiting.
POLITE_RE = re.compile(
    r"^(спасибо|благодарю|хорошо|ок|окей|поняла|понял|принял|принято|ага|да|нет|"
    r"отлично|супер|будем|мы будем|получилось|всё вышло|все вышло|до встречи|"
    r"(все|всё) (отлично|хорошо|супер|получилось)|"
    r"хорошего дня|доброго дня|добрый день|доброе утро|добрый вечер|здравствуйте)$",
    re.I)


def _polite(text: str) -> bool:
    """Ответа не ждут: «Спасибо 🌺», «Отлично! Спасибо! Мы будем».
    Смотрим каждое предложение отдельно — вежливость часто идёт в три строки."""
    clean = re.sub(r"[^\w\s?!.,·\n]", " ", text or "", flags=re.U)
    parts = [p.strip(" .,!·\n") for p in re.split(r"[.!\n·]+", clean)]
    parts = [p for p in parts if p]
    if not parts or "?" in (text or ""):
        return False
    return all(POLITE_RE.match(p) for p in parts)

# Что срочнее: ждущий ответа человек важнее старого хвоста в CRM.
PRIORITY = {"ответ": 0, "звонок": 1, "статус": 2, "хвост": 3}


def _p10(x) -> str:
    d = "".join(c for c in str(x or "") if c.isdigit())
    return d[-10:] if len(d) >= 10 else ""


def _busy(day: str) -> set[str]:
    """Телефоны, которые сегодня уже у кого-то: колонки пульта и инбокс."""
    from . import pult
    out: set[str] = set()
    blob = json.dumps(pult.tasks(day), ensure_ascii=False)
    with db.get_conn() as conn:
        try:
            rows = conn.execute("SELECT text, phone FROM plan_inbox WHERE day=?", (day,)).fetchall()
        except Exception:
            rows = []
    blob += " ".join(f"{t or ''} {p or ''}" for t, p in rows)
    for n in re.findall(r"\d{10,11}", blob):
        out.add(n[-10:])
    return out


def _duty(day: str) -> list[str]:
    from . import pult
    on = [w for w in pult.duty(day) if w in set(pult.SHORT.values())]
    return on or ["Лена"]


def sobrat(day: str = "") -> list[dict]:
    """Что сегодня ничьё. Список пунктов: {kind, phone, name, text}."""
    from . import pult, zayavki, zayavki_audit
    day = day or pult.today()
    busy = _busy(day)
    items: list[dict] = []
    seen: set[str] = set()

    def _add(kind: str, phone: str, name: str, text: str) -> None:
        p = _p10(phone)
        if not p or p in busy or p in seen:
            return
        seen.add(p)
        items.append({"kind": kind, "phone": p, "name": name, "text": text})

    # 1. Проверенные заявки: по каждой уже известно, чего не хватает.
    checked = zayavki_audit.cached(max_age_min=180) or {}
    for f in checked.get("семьи", []):
        want = " + ".join(dict.fromkeys(x for x in f.get("хочет") or [] if x))[:60]
        nm = f.get("name") or f.get("phone")
        days = f.get("days")
        if f["kind"] == "не доделали":
            _add("звонок", f["phone"], nm,
                 f"Заявка без единого касания: {nm} {f['phone']}, {want}, {days} дн. "
                 f"Позвонить первым делом. Не дозвонилась — статус «2. Нет ответа», "
                 f"сообщение в мессенджер и СМС.")
        elif f["kind"] in ("писали", "звонили"):
            _add("звонок", f["phone"], nm,
                 f"Заявка, где не доделали: {nm} {f['phone']}, {want}, {days} дн — "
                 f"{f.get('why', '')[:60]}. Набрать голосом; не дозвонилась — статус "
                 f"«2. Нет ответа» и сообщение.")
        elif f["kind"] in ("говорили", "работает", "не клиент"):
            _add("статус", f["phone"], nm,
                 f"Заявка висит «новой», хотя итог известен: {nm} {f['phone']}, {days} дн — "
                 f"{f.get('why', '')[:60]}. Поставить статус записи в CRM (звонить не нужно).")

    # 2. Сырые списки — то, что проверка ещё не разобрала.
    try:
        raw = zayavki.collect()
    except Exception as e:  # noqa: BLE001
        log.warning("наряд: заявки не собрались: %s", e)
        raw = {"untouched": [], "tried": [], "talked": [], "tail": []}
    for r in raw["untouched"] + raw["tried"]:
        _add("звонок", r["phone"], r["name"],
             f"Заявка сезона без результата: {r['name']} {r['phone']}, "
             f"{(r['comment'] or r['class'])[:50]}, {r['days']} дн. Позвонить.")
    for r in raw["talked"]:
        _add("звонок", r["phone"], r["name"],
             f"С семьёй говорили, решения нет: {r['name']} {r['phone']}, "
             f"{(r['comment'] or r['class'])[:50]}, {r['days']} дн. Дожать до записи "
             f"или отказа и поставить статус.")
    for r in raw["tail"]:
        _add("хвост", r["phone"], r["name"],
             f"Хвост: заявка {r['name']} {r['phone']} висит {r['days']} дн, "
             f"семья записана в другую группу. Закрыть запись в CRM.")

    # 3. Записались на пробное и не пришли — самые тёплые из холодных.
    try:
        from .main import api_mesta_voronka          # считается там же, где страница
        for r in (api_mesta_voronka().get("списки") or {}).get("не пришёл на пробное", []):
            _add("звонок", r.get("phone"), r.get("name"),
                 f"Не пришёл на пробное: {r.get('name')} {r.get('phone')}, "
                 f"{(r.get('group') or '')[:45]}. Позвонить и перезаписать на ближайшее.")
    except Exception as e:  # noqa: BLE001
        log.warning("наряд: воронка не собралась: %s", e)

    # 4. Клиент написал и ждёт. Это сигнал unanswered_inbound, который с 03.09
    #    уходил задачей в МойКласс, а задачи выключены, — значит, не доходил
    #    никуда. Вежливые «спасибо» и смайлики пропускаем: они ответа не ждут.
    try:
        from .main import api_waiting
        import asyncio
        w = asyncio.run(api_waiting(min_minutes=40))
        for r in w.get("items", []):
            t = (r.get("text") or "").strip()
            p = _p10(r.get("phone"))
            if not p.startswith("9"):
                continue                     # групповые чаты и рабочие номера — не семьи
            if len(t) < 12 or _polite(t):
                continue
            if not (r.get("money") or "?" in t or len(t) > 25):
                continue
            nm = r.get("name") or r.get("phone")
            _add("ответ", r["phone"], nm,
                 f"Клиент ждёт ответа {r.get('wait_min')} мин: {nm} {r['phone']} — "
                 f"«{t[:90]}». Ответить в мессенджере и поставить следующий шаг.")
    except Exception as e:  # noqa: BLE001
        log.warning("наряд: ждущие ответа не собрались: %s", e)

    items.sort(key=lambda i: PRIORITY.get(i["kind"], 9))
    calls = [i for i in items if i["kind"] != "хвост"][:LIMIT_CALLS + LIMIT_TAILS]
    tails = [i for i in items if i["kind"] == "хвост"][:LIMIT_TAILS]
    return calls + tails


def raspredelit(day: str = "", dry: bool = False) -> dict:
    """Разложить ничьи строки по дежурным. Возвращает, что поставлено."""
    from . import autopilot, pult
    day = day or pult.today()
    items = sobrat(day)
    on = _duty(day)
    out: dict[str, list[str]] = {w: [] for w in on}
    for n, it in enumerate(items):
        who = on[n % len(on)]
        if dry:
            out[who].append(it["text"])
            continue
        if autopilot.inbox_add(it["text"], it["phone"], who, "наряд"):
            out[who].append(it["text"])
    if not dry and any(out.values()):
        log.info("наряд %s: поставлено %d пунктов (%s)", day,
                 sum(len(v) for v in out.values()),
                 ", ".join(f"{w}: {len(v)}" for w, v in out.items()))
    return {"день": day, "дежурные": on, "ничьих": len(items),
            "поставлено": {w: len(v) for w, v in out.items()}, "пункты": out}
