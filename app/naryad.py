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


# «Всё вышло», «зашли в кабинет», «разобрались» — человек сообщает, что его
# проблема решилась. Ответа он не ждёт, и пункт дежурной тут не нужен.
# 22.09.2026, жалоба Лены: «Сорокина Полина, действий не требуется, в чате
# написала — из списка задача не уходит». Её сообщение было ровно таким:
# «Добрый день! После ссылки по смс только что наконец всё вышло».
RESHILOS_RE = re.compile(
    r"(вс[её] (вышло|получилось|заработало|работает|ок|хорошо)|"
    r"(на)?конец[а-я]* (вс[её] )?(вышло|получилось|заработало)|"
    r"получилось (войти|зайти|открыть|оплатить)|"
    r"(заш[ёел][а-я]*|вош[ёел][а-я]*|удалось (войти|зайти)) (в )?(кабинет|личный кабинет)|"
    r"разобрал[иа]сь|уже (вс[её] )?(вышло|получилось|решил[иа]сь))", re.I)


def _polite(text: str) -> bool:
    """Ответа не ждут: «Спасибо 🌺», «Отлично! Спасибо! Мы будем»,
    «Наконец всё вышло». Смотрим каждое предложение отдельно — вежливость
    часто идёт в три строки."""
    if "?" in (text or ""):
        return False
    if RESHILOS_RE.search(text or ""):
        return True
    clean = re.sub(r"[^\w\s?!.,·\n]", " ", text or "", flags=re.U)
    parts = [p.strip(" .,!·\n") for p in re.split(r"[.!\n·]+", clean)]
    parts = [p for p in parts if p]
    if not parts:
        # Букв не осталось вообще — значит сообщение было из одних смайликов
        # («👍», «🌺❤️»). Комментарий над POLITE_RE это и обещал, а код
        # возвращал False, и «👍» шёл дежурной пунктом. Поймано проверкой
        # правил 22.09.2026.
        return bool((text or "").strip())
    return all(POLITE_RE.match(p) for p in parts)

# Человек закрыл разговор отказом. Ответа он не ждёт, звонить ему не нужно —
# нужно поставить статус в карточке. 22.09.2026, аудит пульта: «Добрый день,
# уже неактуально» и «Не стоит, спасибо, я свяжусь с вами» стояли у Ани
# пунктами «Клиент ждёт ответа» на втором и третьем месте, а по второму
# номеру рядом висело указание автоматики «поставить Отказ и не звонить».
OTKAZ_RE = re.compile(
    r"не стоит|уже не ?актуальн|не ?актуальн|спасибо, не надо|не надо, спасибо|"
    r"мы передумали|передумали|отказ[ыа]ваемся|больше не (нужно|интересно)|"
    r"не будем|не пойд[её]м|в другом городе|уже занимаемся", re.I)

# Что срочнее: ждущий ответа человек важнее старого хвоста в CRM.
PRIORITY = {"ответ": 0, "звонок": 1, "статус": 2, "хвост": 3}


def _zhdet(minut) -> str:
    """«2645 мин» админ не переводит в уме. 22.09.2026, аудит: число ещё и
    врало — оно вшивалось в текст при постановке и потом не менялось."""
    try:
        m = int(minut or 0)
    except (TypeError, ValueError):
        return "давно"
    if m < 90:
        return f"{m} мин"
    if m < 60 * 20:
        return f"{m // 60} ч"
    d = m // (60 * 24)
    return "почти сутки" if d < 1 else ("больше суток" if d == 1 else f"{d} дня и дольше")


# Имя, годное для разговора. В карточках встречается служебный мусор
# («Звонок от…», сам номер вместо имени) — админ читает такую строку и не
# понимает, к кому обращаться (22.09.2026, аудит).
MUSOR_IMYA = re.compile(r"^\s*(звонок|заявка|лид|заказ|клиент|тест)\b|^\+?\d[\d\s\-()]*$", re.I)
# Служебные группы CRM — это не то, чего хочет семья, а наш внутренний буфер.
SLUZHEBNOE = re.compile(r"буфер|лист ожидан|без группы|нераспредел|архив|общая|прочее", re.I)


def _imya(nm, phone: str = "") -> str:
    n = str(nm or "").strip()
    if not n or MUSOR_IMYA.match(n):
        return "имя не заполнено — спросить в разговоре"
    return n[:40]


def _hochet(want: str) -> str:
    """Чего хочет семья — без служебных групп и без пустого «, ,»."""
    chasti = [c.strip() for c in str(want or "").split("+") if c.strip()]
    chasti = [c for c in chasti if not SLUZHEBNOE.search(c)]
    return " + ".join(chasti)[:60]


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
        # Телефон кладём с кодом страны: по десяти цифрам пульт собирал
        # ссылку tel:+9032952727 и телефон дежурной звонил в Турцию
        # (22.09.2026, аудит). Сравниваем по-прежнему по десяти.
        items.append({"kind": kind, "phone": "7" + p, "name": name, "text": text})

    # 1. Проверенные заявки: по каждой уже известно, чего не хватает.
    checked = zayavki_audit.cached(max_age_min=180) or {}
    for f in checked.get("семьи", []):
        want = " + ".join(dict.fromkeys(x for x in f.get("хочет") or [] if x))[:60]
        nm = f.get("name") or f.get("phone")
        days = f.get("days")
        if f["kind"] == "не доделали":
            _add("звонок", f["phone"], nm,
                 f"Заявка без единого касания: {_imya(nm)}"
                 + (f", {_hochet(want)}" if _hochet(want) else "")
                 + f", {days} дн. "
                 f"Позвонить первым делом. Не дозвонилась — статус «2. Нет ответа», "
                 f"сообщение в мессенджер и СМС.")
        elif f["kind"] in ("писали", "звонили"):
            _add("звонок", f["phone"], nm,
                 f"Заявка, где не доделали: {_imya(nm)}"
                 + (f", {_hochet(want)}" if _hochet(want) else "")
                 + f", {days} дн — "
                 f"{f.get('why', '')[:60]}. Набрать голосом; не дозвонилась — статус "
                 f"«2. Нет ответа» и сообщение.")
        elif f["kind"] in ("говорили", "работает", "не клиент"):
            _add("статус", f["phone"], nm,
                 f"Заявка висит «новой», хотя итог известен: {_imya(nm)}, {days} дн — "
                 f"{f.get('why', '')[:60]}. Поставить статус записи в CRM (звонить не нужно).")

    # 2. Сырые списки — то, что проверка ещё не разобрала.
    try:
        raw = zayavki.collect()
    except Exception as e:  # noqa: BLE001
        log.warning("наряд: заявки не собрались: %s", e)
        raw = {"untouched": [], "tried": [], "talked": [], "tail": []}
    for r in raw["untouched"] + raw["tried"]:
        _add("звонок", r["phone"], r["name"],
             f"Заявка сезона без результата: {_imya(r['name'])}"
             + (f", {_hochet(r['comment'] or r['class'])}" if _hochet(r['comment'] or r['class']) else "")
             + f", {r['days']} дн. Позвонить.")
    for r in raw["talked"]:
        _add("звонок", r["phone"], r["name"],
             f"С семьёй говорили, решения нет: {_imya(r['name'])}"
             + (f", {_hochet(r['comment'] or r['class'])}" if _hochet(r['comment'] or r['class']) else "")
             + f", {r['days']} дн. Дожать до записи "
             f"или отказа и поставить статус.")
    for r in raw["tail"]:
        _add("хвост", r["phone"], r["name"],
             f"Хвост: заявка {_imya(r['name'])} висит {r['days']} дн, "
             f"семья записана в другую группу. Закрыть запись в CRM.")

    # 3. Записались на пробное и не пришли — самые тёплые из холодных.
    try:
        from .main import api_mesta_voronka          # считается там же, где страница
        for r in (api_mesta_voronka().get("списки") or {}).get("не пришёл на пробное", []):
            _add("звонок", r.get("phone"), r.get("name"),
                 f"Не пришёл на пробное: {_imya(r.get('name'))}, "
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
            if OTKAZ_RE.search(t):
                # Человек сказал «не стоит», «уже неактуально» — он не ждёт
                # ответа, он закрыл разговор. 22.09.2026 такие пункты стояли
                # у Ани вторым, третьим и четвёртым, а по одному из номеров
                # рядом висело противоположное указание «не звонить».
                continue
            if not (r.get("money") or "?" in t or len(t) > 25):
                continue
            nm = r.get("name") or r.get("phone")
            _add("ответ", r["phone"], nm,
                 f"Клиент ждёт ответа {_zhdet(r.get('wait_min'))}: {_imya(nm)} — "
                 f"«{t[:90]}». Ответить в мессенджере и поставить следующий шаг.")
    except Exception as e:  # noqa: BLE001
        log.warning("наряд: ждущие ответа не собрались: %s", e)

    items.sort(key=lambda i: PRIORITY.get(i["kind"], 9))
    # Лимит — на ДЕНЬ, а не на прогон. 21.09 наряд шёл раз в час и каждый раз
    # брал по двенадцать: к обеду у Ани набралось 33 открытых пункта при семи
    # задачах смены — столько за смену не делается, и список перестаёт быть
    # планом. Считаем, сколько наряд уже положил сегодня, и добираем до лимита.
    postavleno = _postavleno(day)
    calls = [i for i in items if i["kind"] != "хвост"][:max(0, LIMIT_CALLS - postavleno)]
    tails = [i for i in items if i["kind"] == "хвост"][
        :max(0, LIMIT_TAILS - max(0, postavleno - LIMIT_CALLS))]
    return calls + tails


def _postavleno(day: str) -> int:
    """Сколько пунктов наряд уже положил за этот день (включая закрытые)."""
    try:
        with db.get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM plan_inbox WHERE day=? AND source='наряд'",
                (day,)).fetchone()[0]
    except Exception:
        return 0


def _peregruz(day: str, who: str) -> bool:
    """У человека и так больше двадцати пяти незакрытых пунктов — не доливаем.
    Лучше пусть строка подождёт до завтра, чем утонет в списке сегодня."""
    try:
        with db.get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) FROM plan_inbox WHERE day=? AND who=? AND done=0",
                             (day, who)).fetchone()[0]
        from . import pult
        n += sum(1 for x in pult.tasks(day).get(who, []) if not x["done"])
        return n >= 25
    except Exception:
        return False


def raspredelit(day: str = "", dry: bool = False) -> dict:
    """Разложить ничьи строки по дежурным. Возвращает, что поставлено."""
    from . import autopilot, pult
    day = day or pult.today()
    items = sobrat(day)
    on = _duty(day)
    free = [w for w in on if dry or not _peregruz(day, w)]
    out: dict[str, list[str]] = {w: [] for w in on}
    if not free:
        log.info("наряд %s: у всех дежурных больше 25 открытых пунктов — не доливаем", day)
        return {"день": day, "дежурные": on, "ничьих": len(items),
                "поставлено": {w: 0 for w in on}, "перегруз": True, "пункты": out}
    for n, it in enumerate(items):
        who = free[n % len(free)]
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
