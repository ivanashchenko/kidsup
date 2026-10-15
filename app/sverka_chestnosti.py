# -*- coding: utf-8 -*-
"""Сверка честности: что в МойКлассе изменили, удалили или провели задним числом.

Решение Бориса 14.10.2026: «ежедневно следить за действиями администраторов —
что ничего не украли и не смахиначили, все оплаты бьются с абонементами и в
МойКлассе работают корректно». Пароль владельца для этого не нужен: три
источника обходятся ключом API, который и так лежит в настройках сервера.

1. ЖУРНАЛ ПРАВОК. Сервер хранит копию CRM и никогда не удаляет строки
   (db.upsert_rows — INSERT … ON CONFLICT DO UPDATE). Триггеры SQLite на UPDATE
   платежей, абонементов, счетов и записей на занятия пишут
   старый и новый raw в таблицу izmeneniya при каждом синке (лёгкий — раз в
   5 минут). Так видно: сумма, дата, тип и автор платежа; цена, скидка,
   оплачено, статус абонемента; снятые отметки явки и списания за пропуск.
2. УДАЛЕНИЯ. Утром живой API сравнивается с локальной базой в окне 45 дней:
   строка есть у нас и нет в живой выборке → GET /…/{id}; 404 — удалена,
   и мы знаем, что это было, кто автор и сколько. Для удалений старше окна —
   тождество баланса: balans клиента == Σ summa всех его платежей
   (income +, debit −, refund −); расхождение «в минус» разбирается точечно.
3. СНИМКИ /export/raw день к дню — docs/rabota/sverka_snapshots.py; его гоняет
   вечерняя рутина без ключа МойКласса.

Модель денег МойКласса (по данным 14.10): income — деньги на баланс, к абонементу
не привязан никогда; debit — автоматическое списание по счёту (invoiceId всегда,
managerId никогда); refund — возврат, managerId всегда. Поэтому «оплата ↔
абонемент» сверяется цепочкой income → баланс → debit → счёт → абонемент, а не
полем userSubscriptionId у платежа. Онлайн-оплаты (тип 2) приходят без менеджера
по своей природе — это не нарушение.

Запуск: autopilot в 08:45 (после полного синка 07:00 и контроля оплат 08:30),
повтор в 11:45, если утренний не дошёл до конца;
руками — POST /api/sverka-chestnosti/run?day=&dry=1 (только владелец; dry ничего
не запоминает). Результат:
data/sverka_chestnosti/<день>.json, страница /sverka-chestnosti, флаги в ленте
аномалий (kind «честность»), красные пункты — Борису в инбокс, CRM-правки — Лизе.
Фамилии клиентов остаются на сервере; в репозиторий и в чаты — только сводка.

Самопроверка без базы и без сети: python3 -m app.sverka_chestnosti --selftest
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("kidsup.sverka_chestnosti")


def _data_dir() -> Path:
    """Каталог данных приложения. 14.10, ревью: путь был зашит рядом с кодом и
    не учитывал KIDSUP_DATA_DIR, в отличие от базы (config.DATA_DIR)."""
    try:
        from . import config
        return Path(config.DATA_DIR)
    except Exception:  # noqa: BLE001 — запуск вне пакета
        return Path(__file__).resolve().parent.parent / "data"


OUT_DIR = _data_dir() / "sverka_chestnosti"
OKNO_DNEY = 45           # окно живой сверки на удаления
IZVESTNO_DNEY = 400      # сколько помнить подтверждённое удаление (удаления по тождеству бывают старше окна)
IZVESTNO_MAX = 20000     # предел памяти удалений; сверх него уходят самые давние
PODTVERZHDENIY_MAX = 60  # сколько кандидатов на удаление подтверждаем GET-ом за один прогон
BALANS_POROG = 500.0     # расхождение тождества баланса, с которого разбираем клиента
BALANS_MAX = 30          # сколько клиентов с расхождением проверяем живым API за прогон
ZHURNAL_DNEY = 90        # сколько дней хранить журнал правок izmeneniya (14.10, ревью: рос без границы)
T_ONLINE = 2
_DAY_RX = re.compile(r"\d{4}-\d{2}-\d{2}")


def _den(day) -> str | None:
    """Параметр day — только дата ГГГГ-ММ-ДД. 14.10, ревью: day шёл в путь файла
    без проверки, и ?day=../lk_tokens отдавал токены личных страниц админов."""
    if not day:
        return None
    s = str(day).strip()
    if not _DAY_RX.fullmatch(s):
        raise ValueError("day: нужна дата в виде ГГГГ-ММ-ДД")
    date.fromisoformat(s)
    return s
TYPES = {1: "нал", 2: "онлайн", 64725: "терминал", 63934: "перевод на счёт",
         72508: "коррекция", 97845: "возврат"}

# Поля, по которым правка считается событием. Всё остальное (stats, remindDate,
# updatedAt и прочий шум синка) не превращается в находку.
POLYA = {
    "payments": ["summa", "date", "paymentTypeId", "managerId", "userId", "optype",
                 "comment", "invoiceId", "userSubscriptionId"],
    "user_subscriptions": ["price", "payed", "originalPrice", "discount", "extraDiscount",
                           "statusId", "beginDate", "endDate", "visitCount", "visitedCount",
                           "comment", "classIds", "freezeFrom", "freezeTo", "sellDate"],
    "invoices": ["price", "payed", "status", "payUntil", "userSubscriptionId"],
    # 14.10, ревью: bill — объект {id, summa, userSubscriptionId…}, а не число;
    # userSubscriptionId и billSumma на верхнем уровне записи не бывает.
    "lesson_records": ["visit", "free", "test", "paid", "skip", "bill", "goodReason"],
}

TRIGGERS_SQL = """
CREATE TABLE IF NOT EXISTS izmeneniya (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    tbl TEXT,
    rid INTEGER,
    user_id INTEGER,
    old_raw TEXT,
    new_raw TEXT,
    razobrano INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_izmeneniya_ts ON izmeneniya(ts);
CREATE TRIGGER IF NOT EXISTS trg_izm_payments AFTER UPDATE ON payments
WHEN old.raw IS NOT new.raw BEGIN
    INSERT INTO izmeneniya(ts, tbl, rid, user_id, old_raw, new_raw)
    VALUES (datetime('now'), 'payments', new.id, new.user_id, old.raw, new.raw);
END;
CREATE TRIGGER IF NOT EXISTS trg_izm_user_subscriptions AFTER UPDATE ON user_subscriptions
WHEN old.raw IS NOT new.raw BEGIN
    INSERT INTO izmeneniya(ts, tbl, rid, user_id, old_raw, new_raw)
    VALUES (datetime('now'), 'user_subscriptions', new.id, new.user_id, old.raw, new.raw);
END;
CREATE TRIGGER IF NOT EXISTS trg_izm_invoices AFTER UPDATE ON invoices
WHEN old.raw IS NOT new.raw BEGIN
    INSERT INTO izmeneniya(ts, tbl, rid, user_id, old_raw, new_raw)
    VALUES (datetime('now'), 'invoices', new.id, new.user_id, old.raw, new.raw);
END;
CREATE TRIGGER IF NOT EXISTS trg_izm_lesson_records AFTER UPDATE ON lesson_records
WHEN old.raw IS NOT new.raw BEGIN
    INSERT INTO izmeneniya(ts, tbl, rid, user_id, old_raw, new_raw)
    VALUES (datetime('now'), 'lesson_records', new.id, new.user_id, old.raw, new.raw);
END;
-- 14.10, ревью: движение баланса run() не читает (его объясняют платежи и
-- тождество баланса), а строки по users раздували журнал — триггера нет.
DROP TRIGGER IF EXISTS trg_izm_users_balance;
"""


# ── чистые функции (без базы и сети — их проверяет --selftest) ─────────────
def _j(s):
    try:
        return json.loads(s) if isinstance(s, str) else (s or {})
    except ValueError:
        return {}


def _num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _bill_summa(b) -> float:
    """bill записи на занятие — объект {id, summa, userSubscriptionId…} или пусто
    (14.10, ревью: _num(dict) давал 0, и снятое списание не ловилось)."""
    if isinstance(b, dict):
        return _num(b.get("summa"))
    return _num(b)


def msk(ts: str | None) -> str:
    """UTC из МойКласса или datetime('now') → «ДД.ММ ЧЧ:ММ» по Москве."""
    if not ts:
        return ""
    s = str(ts).replace("T", " ").replace("Z", "")[:19]
    try:
        t = datetime.fromisoformat(s) + timedelta(hours=3)
        return t.strftime("%d.%m %H:%M")
    except ValueError:
        return str(ts)


def raznica(tbl: str, old: dict, new: dict) -> dict:
    """Какие значимые поля изменились: {поле: (было, стало)}."""
    out = {}
    for f in POLYA.get(tbl, []):
        a, b = old.get(f), new.get(f)
        if a != b:
            out[f] = (a, b)
    return out


def ocenit_pravku(tbl: str, old: dict, new: dict, den: str | None = None) -> dict | None:
    """Правка строки → находка с уровнем: red / yellow / info; None — не событие.
    den — день правки (ГГГГ-ММ-ДД, из журнала); по умолчанию сегодня."""
    d = raznica(tbl, old, new)
    if not d:
        return None
    lvl, chto = "info", []
    if tbl == "payments":
        optype = new.get("optype") or old.get("optype")
        if "summa" in d:
            a, b = _num(d["summa"][0]), _num(d["summa"][1])
            if optype == "income" and b < a:
                lvl = "red"; chto.append(f"сумма прихода уменьшена {a:g} → {b:g}")
            else:
                lvl = "yellow"; chto.append(f"сумма {a:g} → {b:g}")
        if "userId" in d:
            lvl = "red"; chto.append(f"платёж перенесён на другого клиента {d['userId'][0]} → {d['userId'][1]}")
        if "date" in d:
            lvl = "yellow" if lvl != "red" else lvl; chto.append(f"дата {d['date'][0]} → {d['date'][1]}")
        if "paymentTypeId" in d:
            lvl = "yellow" if lvl != "red" else lvl
            chto.append(f"тип {TYPES.get(d['paymentTypeId'][0], d['paymentTypeId'][0])} → {TYPES.get(d['paymentTypeId'][1], d['paymentTypeId'][1])}")
        if "managerId" in d:
            lvl = "yellow" if lvl != "red" else lvl; chto.append(f"менеджер {d['managerId'][0]} → {d['managerId'][1]}")
        if "comment" in d and not chto:
            chto.append("изменён комментарий")
    elif tbl == "user_subscriptions":
        if "payed" in d and _num(d["payed"][1]) < _num(d["payed"][0]):
            lvl = "red"; chto.append(f"оплачено уменьшено {_num(d['payed'][0]):g} → {_num(d['payed'][1]):g}")
        if "price" in d:
            a, b = _num(d["price"][0]), _num(d["price"][1])
            if b < a and _num(new.get("payed")) > 0:
                lvl = "yellow" if lvl != "red" else lvl; chto.append(f"цена оплаченного абонемента снижена {a:g} → {b:g}")
            else:
                chto.append(f"цена {a:g} → {b:g}")
        for f in ("discount", "extraDiscount"):
            if f in d and _num(d[f][1]) > _num(d[f][0]):
                lvl = "yellow" if lvl != "red" else lvl; chto.append(f"скидка {f} {d[f][0]} → {d[f][1]}")
        if "statusId" in d:
            st = int(_num(new.get("statusId")))
            ne_doshli = _num(new.get("visitedCount")) < _num(new.get("visitCount"))
            # 15.10, ревью: «по сроку» — только если дата окончания не менялась в этой же
            # правке и уже прошла на ДЕНЬ ПРАВКИ (а не на день разбора журнала)
            end_old, end_new = str(old.get("endDate") or "")[:10], str(new.get("endDate") or "")[:10]
            po_sroku = bool(end_new) and end_old == end_new and end_new < (den or date.today().isoformat())[:10]
            vis = f"{_num(new.get('visitedCount')):g}/{_num(new.get('visitCount')):g}"
            if st == 4 and ne_doshli and po_sroku:
                chto.append(f"абонемент окончен по сроку, не использовано "
                            f"{_num(new.get('visitCount')) - _num(new.get('visitedCount')):g} зан.")
            elif st == 4 and ne_doshli and _num(new.get("payed")) > 0:
                lvl = "yellow" if lvl != "red" else lvl
                chto.append(f"оплаченный абонемент закрыт до конца ({vis})"
                            + (f", дата окончания сдвинута {end_old} → {end_new}" if end_old != end_new else ""))
            elif st == 4 and ne_doshli:
                chto.append(f"неоплаченный абонемент закрыт до конца ({vis})")
            elif st != 4:
                chto.append(f"статус {d['statusId'][0]} → {d['statusId'][1]}")
            # 14.10, ревью: обычное окончание (все занятия пройдены) — не событие
        if "visitCount" in d:
            a, b = _num(d["visitCount"][0]), _num(d["visitCount"][1])
            pa, pb = _num(old.get("price", new.get("price"))), _num(new.get("price"))
            # 15.10, ревью: сравниваем цену ЗА ЗАНЯТИЕ — «+3 занятия и +1 ₽» тоже уступка
            if b > a > 0 and pa > 0 and _num(new.get("payed")) > 0 and pb / b < pa / a * 0.9:
                lvl = "yellow" if lvl != "red" else lvl
                chto.append(f"в оплаченный абонемент добавлены занятия {a:g} → {b:g}, "
                            f"цена за занятие {pa / a:,.0f} → {pb / b:,.0f} ₽".replace(",", " "))
            else:
                chto.append(f"занятий в абонементе {a:g} → {b:g}")
        for f in ("beginDate", "endDate", "sellDate", "freezeFrom", "freezeTo", "classIds"):
            if f in d:
                chto.append(f"{f} {d[f][0]} → {d[f][1]}")
        if "comment" in d and not chto:
            chto.append("изменён комментарий")
    elif tbl == "invoices":
        if "payed" in d and _num(d["payed"][1]) < _num(d["payed"][0]):
            lvl = "red"; chto.append(f"оплачено по счёту уменьшено {_num(d['payed'][0]):g} → {_num(d['payed'][1]):g}")
        if "price" in d:
            a, b = _num(d["price"][0]), _num(d["price"][1])
            if b < a and _num(old.get("payed")) > 0:
                lvl = "yellow" if lvl != "red" else lvl; chto.append(f"цена оплаченного счёта снижена {a:g} → {b:g}")
            else:
                chto.append(f"счёт {a:g} → {b:g}")
        for f in ("status", "payUntil", "userSubscriptionId"):
            if f in d:
                chto.append(f"{f} {d[f][0]} → {d[f][1]}")
    elif tbl == "lesson_records":
        # 14.10, ревью: поставленная отметка явки, обычное списание (0 → N) и
        # paid False → True — будни, а не события: на снимках 12→14.10 это было
        # 93 из 100 строк «Справочно». Показываем только обратные движения.
        if "visit" in d and d["visit"][0] and not d["visit"][1]:
            chto.append("отметка явки снята")
        if "bill" in d:
            a, b = _bill_summa(d["bill"][0]), _bill_summa(d["bill"][1])
            if a > 0 and b == 0:
                lvl = "yellow"
                chto.append(f"снято списание{' за пропуск' if not new.get('visit') else ''} {a:g} ₽")
            elif a > 0 and b < a and new.get("goodReason") and abs(b - a / 2) < 0.01:
                # 15.10, ревью: уважительная причина — штатные 50 % МойКласса, не уступка
                chto.append(f"пропуск по уважительной причине: списание 50 % ({a:g} → {b:g} ₽)")
            elif a > 0 and b < a:
                # 14.10, ревью: частичное снятие (975 → 487,5) — тоже уступка
                lvl = "yellow"
                chto.append(f"списание уменьшено {a:g} → {b:g} ₽")
            # a == 0 → b > 0: обычное списание за занятие, не событие (шум «Справочно»)
        if "paid" in d and d["paid"][0] and not d["paid"][1] and lvl == "info":
            chto.append("снята отметка «оплачено»")
        if "free" in d and d["free"][1] and not d["free"][0]:
            chto.append("занятие отмечено бесплатным")
        if "goodReason" in d and d["goodReason"][1] and not d["goodReason"][0] \
                and not any(x.startswith("пропуск по уважительной") for x in chto):
            chto.append("пропуск отмечен уважительным")
        for f in ("test", "skip"):
            if f in d:
                chto.append(f"{f} {d[f][0]} → {d[f][1]}")
    elif tbl == "users":
        a, b = _num(d["balance"][0]), _num(d["balance"][1])
        chto.append(f"баланс {a:g} → {b:g}")
    if not chto:
        return None
    return {"level": lvl, "chto": "; ".join(chto), "polya": {k: list(v) for k, v in d.items()}}


def uroven_udaleniya(tbl: str, raw: dict) -> str:
    """Насколько тревожно удаление строки.

    15.10, первый боевой прогон: 20 красных из 53 удалений, и почти все — пересчёт
    абонемента (удалили абонемент и его списание с баланса, завели заново).
    Деньги видны по приходам и возвратам, поэтому красное — только когда исчез
    сам приход денег. Удалённый абонемент — жёлтое (нужна причина в карточке),
    удалённое списание с баланса и запись на занятие — справочно."""
    if tbl == "payments":
        if raw.get("optype") == "income" and _num(raw.get("summa")) > 0:
            return "red"
        return "info" if raw.get("optype") == "debit" else "yellow"
    if tbl == "user_subscriptions":
        return "yellow"
    return "info"


def tozhdestvo_balansa(balansy: dict, summy: dict, porog: float = BALANS_POROG, dolg: dict | None = None) -> list[dict]:
    """balans клиента против Σ его платежей. Минус — у нас есть платёж, которого
    в МойКлассе больше нет (удалён); плюс — платёж есть в МойКлассе, а у нас нет
    (пропуск синка). Возвращает только расхождения больше порога.

    dolg — недоплата по счетам клиента. 14.10, ревью: у одних клиентов
    неоплаченный счёт уводит баланс в минус, у других нет, поэтому клиент
    сходится, если сходится хоть одна формула (с долгом или без); иначе
    берётся меньшая дельта — так не прячется удаление старого платежа."""
    out = []
    dolg = dolg or {}
    for uid, bal in balansy.items():
        s = summy.get(uid, 0.0)
        d1 = round(bal - s, 2)
        d2 = round(bal - (s - dolg.get(uid, 0.0)), 2)
        if abs(d1) < porog or abs(d2) < porog:
            continue
        delta = min(d1, d2)
        if abs(delta) >= porog:
            out.append({"userId": uid, "balans": bal, "summa_platezhey": round(s, 2), "delta": delta,
                        "d1": d1, "d2": d2,
                        "smysl": "платёж удалён в МойКлассе" if delta < 0 else "пропуск синка или снятое списание"})
    return sorted(out, key=lambda x: x["delta"])


def ostatok_balansa(x: dict, summa_udalennyh: float, porog: float = BALANS_POROG) -> float | None:
    """Расхождение, которое НЕ объясняют удалённые платежи, или None.
    15.10, ревью: остаток считался только по формуле без долга, и у клиента, чей
    баланс уже уменьшил неоплаченный счёт, удалённый приход давал ложное жёлтое
    «баланс» ровно на сумму долга. Жёлтое — только если не сходится ни одна формула."""
    o1 = round(_num(x.get("d1", x.get("delta"))) + summa_udalennyh, 2)
    o2 = round(_num(x.get("d2", x.get("delta"))) + summa_udalennyh, 2)
    if o1 <= -porog and o2 <= -porog:
        return max(o1, o2)
    return None


def kandidaty(local_ids, live_ids) -> list:
    """Есть у нас, нет в живой выборке того же окна."""
    return sorted(set(local_ids) - set(live_ids))


def pochistit_izvestno(izvestno: dict, d0: date) -> dict:
    """Память подтверждённых удалений {«таблица:id»: день обнаружения}.
    Старше IZVESTNO_DNEY — забываем; сверх IZVESTNO_MAX — уходят самые давние.
    14.10, ревью: раньше обрезалось sorted(...)[-5000:], то есть по алфавиту,
    и первыми вылетали свежие lesson_records:* — назавтра они снова
    становились кандидатами и снова приходили в пульт."""
    granica = (d0 - timedelta(days=IZVESTNO_DNEY)).isoformat()
    ost = {str(k): str(v or "") for k, v in (izvestno or {}).items() if str(v or "") >= granica}
    if len(ost) > IZVESTNO_MAX:
        ost = dict(sorted(ost.items(), key=lambda kv: (kv[1], kv[0]))[-IZVESTNO_MAX:])
    return ost


# ── база ───────────────────────────────────────────────────────────────────
_TRG_RX = re.compile(r"CREATE TRIGGER IF NOT EXISTS (\w+) (.*?\bEND;)", re.S)


def _norm_sql(s) -> str:
    return " ".join(str(s or "").replace(";", " ").split())


def ensure(conn=None, peresozdat: bool = True) -> list:
    """Таблица журнала и триггеры. Повторный вызов безвреден. Возвращает имена
    устаревших триггеров.

    14.10, ревью: триггеры создаются «IF NOT EXISTS», и изменённое тело на
    рабочей базе не применилось бы никогда. Теперь триггер, чей текст в базе
    отличается от TRIGGERS_SQL, пересоздаётся. 15.10, ревью: DROP и CREATE —
    одной транзакцией (иначе под замком синка триггер мог пропасть совсем), а при
    peresozdat=False (прогон dry) только сообщается, база не трогается."""
    from . import db

    def _apply(c):
        stale = [(name, body) for name, body in _TRG_RX.findall(TRIGGERS_SQL)
                 if (row := c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)).fetchone())
                 and _norm_sql(row[0]) != _norm_sql(f"CREATE TRIGGER {name} {body}")]
        if stale and peresozdat:
            if c.in_transaction:
                c.commit()
            c.execute("BEGIN IMMEDIATE")
            try:
                for name, body in stale:
                    log.warning("сверка честности: триггер %s устарел — пересоздаю", name)
                    c.execute(f"DROP TRIGGER IF EXISTS {name}")
                    c.execute(f"CREATE TRIGGER {name} {body}")
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
        c.executescript(TRIGGERS_SQL)
        return [name for name, _ in stale]

    if conn is not None:
        return _apply(conn)
    with db.get_conn() as c:
        return _apply(c)


def _manager_names(conn) -> dict:
    out = {}
    for r in conn.execute("SELECT id, name FROM managers"):
        out[r["id"]] = (r["name"] or "").split()[0] if r["name"] else str(r["id"])
    out.setdefault(84116, "Борис"); out.setdefault(154181, "Лиза")
    return out


def _is_404(e: Exception) -> bool:
    resp = getattr(e, "response", None)
    code = getattr(resp, "status_code", None)
    return code == 404 or " 404 " in f" {e} " or "404 Client Error" in str(e)


# ── основной прогон ────────────────────────────────────────────────────────
_RUN_LOCK = threading.Lock()


def run(day: str | None = None, dry: bool = False, days: int = OKNO_DNEY) -> dict:
    """Один прогон сверки. Боевой (dry=False) — не больше одного за раз:
    15.10, ревью — повтор в 11:45 мог стартовать поверх идущего утреннего, и
    закладка журнала откатывалась."""
    if dry:
        return _run(day, dry=True, days=days)
    if not _RUN_LOCK.acquire(blocking=False):
        raise RuntimeError("сверка честности уже идёт — второй боевой прогон не запускаю")
    try:
        return _run(day, dry=False, days=days)
    finally:
        _RUN_LOCK.release()


def _run(day: str | None = None, dry: bool = False, days: int = OKNO_DNEY) -> dict:
    from . import db
    day = _den(day) or date.today().isoformat()
    d0 = date.fromisoformat(day)
    now_utc = datetime.utcnow()
    R: dict = {"day": day, "sostavleno": datetime.now().strftime("%d.%m.%Y %H:%M"),
               "nakhodki": [], "po_avtoru_stroki": {}, "predohranitel": {}, "cifry": {}}

    def nakhodka(level, kind, text, uid=None, who=None, rid=None, tbl=None, extra=None):
        R["nakhodki"].append({"level": level, "kind": kind, "text": text, "userId": uid,
                              "who": who, "rid": rid, "tbl": tbl, "extra": extra or {}})

    with db.get_conn() as conn:
        zastarelo = ensure(conn, peresozdat=not dry)
        if zastarelo and dry:
            nakhodka("info", "предохранитель", f"Триггеры журнала устарели ({', '.join(zastarelo)}) — пересоздадутся при боевом прогоне.")
        M = _manager_names(conn)
        users = {r["id"]: dict(r) for r in conn.execute("SELECT id, name, phone, balance, created_at FROM users")}
        name = lambda uid: (users.get(uid) or {}).get("name") or f"#{uid}"

        # S0. Предохранитель: свежесть данных
        last_sync = db.get_state("last_sync") or ""
        last_light = db.get_state("last_light_sync") or ""
        svezho = True
        try:
            svezho = (datetime.now() - datetime.fromisoformat(last_sync[:19])) < timedelta(hours=26)
        except ValueError:
            svezho = False
        R["predohranitel"] = {"last_sync": last_sync, "last_light_sync": last_light, "polnye_dannye": svezho}

        # 1. Журнал правок с прошлого прогона.
        # 14.10, повторное ревью: закладка — id строки журнала, а не время. По
        # времени строка, записанная в ту же секунду, что и старт прогона, но
        # после чтения, терялась навсегда. Читаем ровно до max_id на момент старта.
        kursor = int(_num(db.get_state("sverka_chestnosti_id"), 0))
        max_id = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM izmeneniya").fetchone()[0])
        if kursor > 0:
            usl, args = "id > ? AND id <= ?", (kursor, max_id)
        else:
            since = db.get_state("sverka_chestnosti_ts") or (now_utc - timedelta(hours=26)).strftime("%Y-%m-%d %H:%M:%S")
            usl, args = "ts > ? AND id <= ?", (since, max_id)
        # 14.10, ревью: курсором, а не списком — при массовой смене raw в журнале
        # десятки тысяч строк, а значимых правок среди них единицы
        n_pravok = 0
        for r in conn.execute(
                f"SELECT id, ts, tbl, rid, user_id, old_raw, new_raw FROM izmeneniya WHERE {usl} ORDER BY id", args):
            n_pravok += 1
            old, new = _j(r["old_raw"]), _j(r["new_raw"])
            oc = ocenit_pravku(r["tbl"], old, new, den=(r["ts"] or "")[:10] or None)
            if not oc:
                continue
            if r["tbl"] == "users":
                # движение баланса само по себе не событие — его объясняют платежи;
                # необъяснённое смотрим ниже по тождеству
                continue
            who = M.get(new.get("managerId") or old.get("managerId"), "—")
            uid = r["user_id"]
            nakhodka(oc["level"], f"правка: {r['tbl']}", f"{name(uid)}: {oc['chto']} (замечено {msk(r['ts'])} МСК, автор строки {who})",
                     uid, who, r["rid"], r["tbl"], oc["polya"])
        R["cifry"]["pravok_v_zhurnale"] = n_pravok

        # 2. Удаления: живой API против локальной базы в окне
        a = (d0 - timedelta(days=days)).isoformat()
        b = (d0 + timedelta(days=7)).isoformat()
        udaleno = []
        # 14.10, ревью: подтверждённое удаление запоминаем («таблица:id»), иначе
        # оно каждое утро до 45 дней возвращалось бы в пульт Бориса, даже закрытое.
        # Хранится {«таблица:id»: день обнаружения}; чистится по сроку, а не по алфавиту.
        _iz = _j(db.get_state("sverka_chestnosti_udaleno") or "{}")
        izvestno: dict = _iz if isinstance(_iz, dict) else {str(k): day for k in (_iz or [])}
        novye_udaleniya: set = set()
        try:
            from .moyklass_client import MoyklassClient
            from . import sync as _sync
            mk = MoyklassClient(_sync.get_api_key())
            try:
                live_pay = mk.fetch_all("/v1/company/payments", ["payments"], params={"date[]": [a, b]})
                live_pay_ids = {p["id"] for p in live_pay}
                loc_pay = {r["id"]: dict(r) for r in conn.execute(
                    "SELECT id, user_id, date, summa, optype, raw FROM payments WHERE date BETWEEN ? AND ?", (a, b))}
                R["cifry"]["platezhi_okno"] = {"local": len(loc_pay), "live": len(live_pay_ids),
                                               "net_u_nas": len(live_pay_ids - set(loc_pay))}
                live_sub = mk.fetch_all("/v1/company/userSubscriptions", ["subscriptions", "userSubscriptions"],
                                        params={"sellDate": [a, b]})
                live_sub_ids = {s["id"] for s in live_sub}
                loc_sub = {}
                for r in conn.execute("SELECT id, user_id, raw FROM user_subscriptions"):
                    raw = _j(r["raw"])
                    if a <= (raw.get("sellDate") or "") <= b:
                        loc_sub[r["id"]] = raw
                R["cifry"]["abonementy_okno"] = {"local": len(loc_sub), "live": len(live_sub_ids)}
                live_rec = mk.fetch_all("/v1/company/lessonRecords", ["lessonRecords", "records"],
                                        params={"date[]": [(d0 - timedelta(days=14)).isoformat(), day]})
                live_rec_ids = {x["id"] for x in live_rec}
                loc_rec = {r["id"]: dict(r) for r in conn.execute(
                    "SELECT lr.id, lr.user_id, lr.raw, l.date AS ldate FROM lesson_records lr JOIN lessons l ON l.id = lr.lesson_id "
                    "WHERE l.date BETWEEN ? AND ?", ((d0 - timedelta(days=14)).isoformat(), day))}
                R["cifry"]["zapisi_okno"] = {"local": len(loc_rec), "live": len(live_rec_ids)}

                plan = ([("payments", i, loc_pay[i]) for i in kandidaty(loc_pay, live_pay_ids)]
                        + [("user_subscriptions", i, loc_sub[i]) for i in kandidaty(loc_sub, live_sub_ids)]
                        + [("lesson_records", i, loc_rec[i]) for i in kandidaty(loc_rec, live_rec_ids)])
                R["cifry"]["uzhe_izvestnyh_udaleniy"] = sum(1 for p in plan if f"{p[0]}:{p[1]}" in izvestno)
                plan = [p for p in plan if f"{p[0]}:{p[1]}" not in izvestno]
                R["cifry"]["kandidatov_na_udalenie"] = len(plan)
                if len(plan) > PODTVERZHDENIY_MAX:
                    nakhodka("info", "предел", f"Кандидатов на удаление {len(plan)}, проверено первых {PODTVERZHDENIY_MAX} — остальное завтра.")
                path = {"payments": "/v1/company/payments/", "user_subscriptions": "/v1/company/userSubscriptions/",
                        "lesson_records": "/v1/company/lessonRecords/"}
                for tbl, rid, row in plan[:PODTVERZHDENIY_MAX]:
                    raw = _j(row.get("raw")) if isinstance(row, dict) and "raw" in row else row
                    try:
                        mk.get(path[tbl] + str(rid))
                        # жива, но вышла из окна: сменили дату или клиента.
                        # 15.10: справочно — денег не убыло, а Лизе в пульт шёл шум
                        nakhodka("info", f"правка: {tbl}", f"{name(raw.get('userId') or row.get('user_id'))}: строка {tbl} #{rid} жива в МойКлассе, но ушла из окна — изменена дата или клиент.",
                                 raw.get("userId") or row.get("user_id"), M.get(raw.get("managerId"), "—"), rid, tbl)
                        continue
                    except Exception as e:  # noqa: BLE001
                        if not _is_404(e):
                            log.warning("сверка честности: %s %s не проверился: %s", tbl, rid, e)
                            continue
                    uid = raw.get("userId") or row.get("user_id")
                    who = M.get(raw.get("managerId"), "—")
                    lvl = uroven_udaleniya(tbl, raw)
                    if tbl == "payments":
                        s = _num(raw.get("summa")); ot = raw.get("optype")
                        text = (f"{name(uid)}: удалён платёж {ot} {s:g} ₽ ({TYPES.get(raw.get('paymentTypeId'), raw.get('paymentTypeId'))}, "
                                f"дата {raw.get('date')}, создан {msk(raw.get('createdAt'))}, автор строки {who}; кто удалил — видно только в Истории действий).")
                    elif tbl == "user_subscriptions":
                        payed = _num(raw.get("payed"))
                        text = (f"{name(uid)}: удалён абонемент {_num(raw.get('price')):g} ₽ (оплачено {payed:g}, "
                                f"{_num(raw.get('visitedCount')):g}/{_num(raw.get('visitCount')):g}, продан {raw.get('sellDate')}, автор строки {who}; кто удалил — видно только в Истории действий).")
                    else:
                        text = (f"{name(uid)}: удалена запись на занятие {row.get('ldate')} "
                                f"(visit={raw.get('visit')}, списание {_bill_summa(raw.get('bill')):g} ₽).")
                    udaleno.append(rid)
                    novye_udaleniya.add(f"{tbl}:{rid}")
                    nakhodka(lvl, f"удаление: {tbl}", text, uid, who, rid, tbl, {"raw": raw})

                # 3. Тождество баланса (только при свежих данных)
                if svezho:
                    sr = db.get_state("sync_range") or ""
                    okno_ot = (_j(sr) or ["2024-07-01"])[0] if sr.startswith("[") else "2024-07-01"
                    summy = {r["user_id"]: _num(r["s"]) for r in conn.execute("SELECT user_id, SUM(summa) s FROM payments GROUP BY user_id")}
                    # 14.10, ревью: неоплаченный счёт уводит баланс в минус без всякого
                    # платежа (баланс −12 040 при недоплате 12 040) — без поправки
                    # 7 из 9 «минусовых» клиентов были ложной тревогой.
                    dolg = {r["user_id"]: _num(r["d"]) for r in conn.execute(
                        "SELECT user_id, SUM(CASE WHEN price > payed THEN price - payed ELSE 0 END) d "
                        "FROM invoices GROUP BY user_id")}
                    balansy = {uid: _num(u.get("balance")) for uid, u in users.items()
                               if (u.get("created_at") or "") >= okno_ot and uid in summy}
                    rash = tozhdestvo_balansa(balansy, summy, dolg=dolg)
                    R["cifry"]["tozhdestvo"] = {"klientov": len(balansy), "rashozhdeniy": len(rash),
                                               "v_minus": sum(1 for x in rash if x["delta"] < 0)}
                    # {клиент: расхождение при прошлой проверке} — перепроверяем, если оно изменилось
                    _pv = _j(db.get_state("sverka_chestnosti_balans") or "{}")
                    provereno = _pv if isinstance(_pv, dict) else {str(u): None for u in (_pv or [])}
                    n = 0
                    for x in rash:
                        key = str(x["userId"])
                        if x["delta"] >= 0 or n >= BALANS_MAX:
                            continue
                        if provereno.get(key) is not None and abs(_num(provereno[key]) - x["delta"]) < 1:
                            continue
                        n += 1
                        try:
                            live_u = mk.fetch_all("/v1/company/payments", ["payments"], params={"userId": x["userId"]})
                        except Exception as e:  # noqa: BLE001
                            log.warning("платежи клиента %s не прочитались: %s", x["userId"], e)
                            continue
                        live_ids = {p["id"] for p in live_u}
                        loc_u = [dict(r) | _j(r["raw"]) for r in conn.execute("SELECT id, raw FROM payments WHERE user_id = ?", (x["userId"],))]
                        gone_vse = [p for p in loc_u if p["id"] not in live_ids]
                        # 14.10, ревью: не повторяем удаления, найденные по окну в этом же прогоне
                        gone = [p for p in gone_vse if f"payments:{p['id']}" not in izvestno
                                and f"payments:{p['id']}" not in novye_udaleniya]
                        provereno[key] = x["delta"]
                        for p in gone:
                            s = _num(p.get("summa")); who = M.get(p.get("managerId"), "—")
                            lvl = uroven_udaleniya("payments", p)
                            novye_udaleniya.add(f"payments:{p['id']}")
                            nakhodka(lvl, "удаление: payments", f"{name(x['userId'])}: удалён платёж {p.get('optype')} {s:g} ₽ от {p.get('date')} (автор строки {who}); баланс {x['balans']:g} против суммы платежей {x['summa_platezhey']:g}.",
                                     x["userId"], who, p["id"], "payments", {"delta": x["delta"]})
                        # Остаток, который не объясняют удалённые платежи (известные и новые):
                        # 14.10, ревью — раньше любое новое расхождение у клиента с уже
                        # известным удалением молчало.
                        ostatok = ostatok_balansa(x, sum(_num(p.get("summa")) for p in gone_vse))
                        if ostatok is not None:
                            nakhodka("yellow", "баланс", f"{name(x['userId'])}: баланс {x['balans']:g} меньше суммы платежей на {-ostatok:g} ₽ сверх удалённых платежей — возможно, списание задним числом.", x["userId"])
                    if not dry:
                        db.set_state("sverka_chestnosti_balans", json.dumps(dict(list(provereno.items())[-3000:])))
                    plus = [x for x in rash if x["delta"] > 0]
                    if plus:
                        nakhodka("info", "синк", f"У {len(plus)} клиентов баланс больше суммы платежей (всего {sum(x['delta'] for x in plus):,.0f} ₽) — платежи есть в МойКлассе, у нас нет: пропуск синка, не честность.".replace(",", " "))
                else:
                    nakhodka("info", "предохранитель", f"Полный синк старше 26 часов ({last_sync or 'нет'}): тождество баланса не считали, данные неполные.")
            finally:
                mk.close()
        except Exception as e:  # noqa: BLE001
            log.exception("сверка честности: живой API недоступен")
            nakhodka("info", "предохранитель", f"Живой API МойКласса недоступен: {str(e)[:120]}. Проверены только правки по журналу.")

        # 4. Итог по сотрудникам и запись
        po = defaultdict(lambda: {"red": 0, "yellow": 0, "info": 0})
        for x in R["nakhodki"]:
            po[x.get("who") or "—"][x["level"]] += 1
        # 14.10, ревью: ключ назван честно — это автор исходной строки, а не тот,
        # кто правил или удалял (API этого не отдаёт)
        R["po_avtoru_stroki"] = dict(po)
        cnt = {k: sum(1 for x in R["nakhodki"] if x["level"] == k) for k in ("red", "yellow", "info")}
        R["cifry"]["nakhodki"] = cnt
        R["summary"] = (f"Сверка честности {day[8:]}.{day[5:7]}: правок в журнале {n_pravok}, удалений подтверждено {len(udaleno)}, "
                        f"красных {cnt['red']}, жёлтых {cnt['yellow']}" + ("" if svezho else "; данные неполные (нет свежего синка)")
                        + ". Подробно: app.kidsup.ru/sverka-chestnosti")
        R["dry"] = bool(dry)
        if dry:
            # 14.10, ревью: dry — только отчёт. Курсор журнала, отметки проверенных,
            # память удалений, лента аномалий и файл дня не трогаются: раньше один
            # прогон dry «съедал» сигналы следующего боевого (2 красных вместо 6).
            return R

        try:
            from . import audit
            for x in R["nakhodki"]:
                if x["level"] in ("red", "yellow") and x.get("rid"):
                    audit.add_flag("честность", f"chest:{x['tbl']}:{x['rid']}:{x['kind'][:12]}",
                                   audit.HIGH if x["level"] == "red" else audit.MID,
                                   x["text"][:120], x["text"], user_id=x.get("userId"), day=day)
        except Exception:  # noqa: BLE001
            log.exception("сверка честности: лента не записалась")

        from . import autopilot
        try:
            otkryto = "\n".join(r[0] or "" for r in conn.execute(
                "SELECT text FROM plan_inbox WHERE done=0 AND day>=?", ((d0 - timedelta(days=14)).isoformat(),)))
        except Exception:  # noqa: BLE001
            otkryto = ""
        n_b = n_l = 0
        # 14.10, ревью: пункты без телефона — с телефоном наряд и возврат считали
        # семью «занятой» и не давали по ней звонков, пока пункт открыт.
        for x in sorted(R["nakhodki"], key=lambda z: {"red": 0, "yellow": 1, "info": 2}[z["level"]]):
            if x["text"][:100] in otkryto:
                continue
            if x["level"] == "red" and n_b < 6:
                if autopilot.inbox_add("Сверка честности: " + x["text"], "", "Борис", source=f"сверка честности {day[8:]}.{day[5:7]}"):
                    n_b += 1
            elif x["level"] == "yellow" and n_l < 6 and x["kind"].startswith(("правка", "баланс")):
                if autopilot.inbox_add("Проверить и объяснить в карточке: " + x["text"], "", "Лиза", source=f"сверка честности {day[8:]}.{day[5:7]}"):
                    n_l += 1
        autopilot.inbox_add(R["summary"], "", "Борис", source="сверка честности")
        R["inbox_posted"] = {"Борис": n_b, "Лиза": n_l}
        # 14.10, повторное ревью: файл дня пишется после пунктов пульта (раньше
        # inbox_posted в него не попадал) и атомарно — страница не прочтёт половину.
        _zapisat_den(day, R)
        izvestno.update({k: day for k in novye_udaleniya})
        db.set_state("sverka_chestnosti_udaleno", json.dumps(pochistit_izvestno(izvestno, d0), ensure_ascii=False))
        try:
            conn.execute("DELETE FROM izmeneniya WHERE ts < datetime('now', ?)", (f"-{ZHURNAL_DNEY} days",))
            # 14.10, повторное ревью: без commit DELETE держал замок записи на conn,
            # и db.set_state ниже (второе соединение) 30 с ждал его и падал
            # «database is locked» — курсор не сохранялся, журнал не чистился,
            # вся остальная запись в базу стояла.
            conn.commit()
        except Exception:  # noqa: BLE001
            log.exception("сверка честности: журнал не почистился")
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
        db.set_state("sverka_chestnosti_id", str(max_id))
        db.set_state("sverka_chestnosti_ts", now_utc.strftime("%Y-%m-%d %H:%M:%S"))
        db.set_state("sverka_chestnosti_den", day)
    return R


def _zapisat_den(day: str, R: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    f = OUT_DIR / f"{day}.json"
    # 15.10, ревью: временный файл свой у каждого потока — два прогона не мешают друг другу
    tmp = OUT_DIR / f".{day}.{os.getpid()}.{threading.get_ident()}.tmp"
    tmp.write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(f)


def proshla(day: str) -> bool:
    """Боевой прогон за этот день дошёл до файла дня (для повтора в автопилоте).
    15.10, ревью: файл дня пишется после пунктов пульта — если он есть, повтор
    в 11:45 не нужен и не должен перезаписать полный отчёт почти пустым."""
    from . import db
    return (db.get_state("sverka_chestnosti_den") or "") == str(day) or (OUT_DIR / f"{day}.json").exists()


def run_safe(day: str | None = None) -> None:
    """Утренний прогон из потока автопилота. 14.10, повторное ревью: исключение
    в потоке уходило только в stderr и в этот день не повторялось. Теперь — в
    журнал приложения, а автопилот повторяет прогон в 11:45, если proshla() ложно."""
    try:
        run(day, dry=False)
    except Exception:  # noqa: BLE001
        log.exception("сверка честности: прогон упал")


def _fayly() -> list:
    return sorted(p for p in OUT_DIR.glob("*.json") if _DAY_RX.fullmatch(p.stem)) if OUT_DIR.exists() else []


def load(day: str | None = None) -> dict:
    day = _den(day)          # ValueError на всё, кроме ГГГГ-ММ-ДД
    files = _fayly()
    if day:
        f = OUT_DIR / f"{day}.json"
        files = [f] if f.exists() else []
    if not files:
        return {"day": day, "nakhodki": [], "summary": "Сверка ещё не запускалась."}
    try:
        R = json.loads(files[-1].read_text(encoding="utf-8"))
        if not isinstance(R, dict):
            raise ValueError("файл дня — не объект")
        return R
    except (OSError, ValueError):
        # 14.10, ревью: битый файл не должен выглядеть как «нет такого дня» (404)
        log.exception("сверка честности: файл %s не читается", files[-1].name)
        return {"day": day or files[-1].stem, "nakhodki": [],
                "summary": "Файл сверки за этот день не читается — запустите прогон заново или напишите Клоду."}


def html(day: str | None = None) -> str:
    import html as _h
    R = load(day)
    days = "".join(f'<a href="/sverka-chestnosti?day={p.stem}">{p.stem[8:]}.{p.stem[5:7]}</a> '
                   for p in _fayly()[-14:])
    body = [f"<h1>Сверка честности {(R.get('day') or '')[8:]}.{(R.get('day') or '')[5:7]}</h1>",
            f"<p class=lead>{_h.escape(R.get('summary', ''))} · собрано {R.get('sostavleno', '')}</p><p>Дни: {days}</p>"]
    po = R.get("po_avtoru_stroki") or R.get("po_sotrudnikam") or {}   # старые файлы — со старым ключом
    if po:
        # 14.10, ревью: API не говорит, кто правил или удалял, — только кто провёл
        # исходную строку. Подпись честная, чтобы не обвинить не того человека.
        body.append("<h2>По автору исходной строки</h2><p class=lead>Кто провёл платёж или продал абонемент. "
                    "Кто потом правил или удалял, МойКласс через API не показывает — это видно только в Истории действий.</p>"
                    "<table><tr><th>Автор строки</th><th>красных</th><th>жёлтых</th><th>прочее</th></tr>"
                    + "".join(f"<tr><td>{_h.escape(str(k))}</td><td>{v['red']}</td><td>{v['yellow']}</td><td>{v['info']}</td></tr>" for k, v in po.items())
                    + "</table>")
    for lvl, title in (("red", "Красное — объяснения нет в данных"), ("yellow", "Жёлтое — нужна причина в карточке"), ("info", "Справочно")):
        items = [x for x in R.get("nakhodki", []) if x["level"] == lvl]
        if items:
            body.append(f"<h2>{title} <small>({len(items)})</small></h2><ul>"
                        + "".join(f'<li class={lvl}>{_h.escape(x["text"])}</li>' for x in items) + "</ul>")
    if not R.get("nakhodki"):
        body.append("<p class=ok>Находок нет.</p>")
    css = ("body{font:15px/1.5 Inter,sans-serif;max-width:1000px;margin:0 auto;padding:20px 16px;color:#221F3B}"
           "h1,h2{color:#312783}li{margin:6px 0}li.red{color:#B3261E;font-weight:600}li.yellow{color:#7a4b00}"
           ".ok{color:#3f7d12}.lead{color:#6A6F87}small{color:#6A6F87;font-weight:400}table{border-collapse:collapse}td,th{padding:4px 10px;border-bottom:1px solid #ddd}")
    return f"<!doctype html><html lang=ru><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Сверка честности</title><style>{css}</style></head><body>{''.join(body)}</body></html>"


# ── самопроверка ───────────────────────────────────────────────────────────
def _selftest() -> int:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT, phone TEXT, balance REAL, client_state_id INTEGER, created_at TEXT, updated_at TEXT, raw TEXT);
    CREATE TABLE payments (id INTEGER PRIMARY KEY, user_id INTEGER, date TEXT, summa REAL, optype TEXT, comment TEXT, raw TEXT);
    CREATE TABLE user_subscriptions (id INTEGER PRIMARY KEY, user_id INTEGER, subscription_id INTEGER, status_id INTEGER, begin_date TEXT, end_date TEXT, visits_total INTEGER, visits_used INTEGER, raw TEXT);
    CREATE TABLE invoices (id INTEGER PRIMARY KEY, user_id INTEGER, price REAL, payed REAL, created_at TEXT, raw TEXT);
    CREATE TABLE lesson_records (id INTEGER PRIMARY KEY, lesson_id INTEGER, user_id INTEGER, visit INTEGER, raw TEXT);
    """)
    ensure(conn)
    # апсерт как в db.upsert_rows: INSERT … ON CONFLICT DO UPDATE → триггер пишет старое и новое
    sql = "INSERT INTO payments(id,user_id,date,summa,optype,comment,raw) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET user_id=excluded.user_id,date=excluded.date,summa=excluded.summa,optype=excluded.optype,comment=excluded.comment,raw=excluded.raw"
    conn.execute(sql, (1, 10, "2026-10-11", 8750, "income", "", json.dumps({"id": 1, "summa": 8750, "optype": "income", "date": "2026-10-11", "paymentTypeId": 64725, "managerId": 232763})))
    conn.execute(sql, (1, 10, "2026-10-11", 8750, "income", "", json.dumps({"id": 1, "summa": 8750, "optype": "income", "date": "2026-10-11", "paymentTypeId": 64725, "managerId": 232763})))
    assert conn.execute("SELECT COUNT(*) FROM izmeneniya").fetchone()[0] == 0, "одинаковый raw не должен попадать в журнал"
    conn.execute(sql, (1, 10, "2026-10-09", 5000, "income", "", json.dumps({"id": 1, "summa": 5000, "optype": "income", "date": "2026-10-09", "paymentTypeId": 1, "managerId": 232763})))
    conn.execute("INSERT INTO users(id,name,balance) VALUES(10,'Тест',0) ON CONFLICT(id) DO UPDATE SET balance=excluded.balance")
    conn.execute("INSERT INTO users(id,name,balance) VALUES(10,'Тест',3225) ON CONFLICT(id) DO UPDATE SET balance=excluded.balance")
    rows = [dict(r) for r in conn.execute("SELECT * FROM izmeneniya ORDER BY id")]
    assert [r["tbl"] for r in rows] == ["payments"], rows   # баланс в журнал больше не пишется
    oc = ocenit_pravku("payments", _j(rows[0]["old_raw"]), _j(rows[0]["new_raw"]))
    assert oc and oc["level"] == "red" and "уменьшена" in oc["chto"] and "дата" in oc["chto"] and "тип" in oc["chto"], oc
    assert ocenit_pravku("payments", {"summa": 1, "stats": 1}, {"summa": 1, "stats": 2}) is None
    oc2 = ocenit_pravku("user_subscriptions", {"price": 5000, "payed": 5000, "visitCount": 4, "visitedCount": 1, "statusId": 2},
                        {"price": 1250, "payed": 1250, "visitCount": 1, "visitedCount": 1, "statusId": 4})
    assert oc2 and oc2["level"] == "red", oc2
    oc3 = ocenit_pravku("lesson_records", {"visit": False, "bill": 975}, {"visit": False, "bill": 0})
    assert oc3 and oc3["level"] == "yellow", oc3
    # настоящая форма записи МойКласса: bill — объект, при снятии списания — null
    oc3b = ocenit_pravku("lesson_records",
                         {"visit": False, "paid": True, "bill": {"id": 7, "summa": 975, "userSubscriptionId": 5}},
                         {"visit": False, "paid": False, "bill": None})
    assert oc3b and oc3b["level"] == "yellow" and "снято списание за пропуск 975" in oc3b["chto"], oc3b
    for bad in ("../lk_tokens", "/etc/passwd", "2026-10-1", "2026-10-14.dry", "2026-13-40"):
        try:
            _den(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"_den пропустил {bad!r}")
    assert _den("2026-10-14") == "2026-10-14" and _den("") is None
    oc4 = ocenit_pravku("invoices", {"price": 5000, "payed": 5000}, {"price": 1250, "payed": 1250})
    assert oc4 and oc4["level"] == "red", oc4
    t = tozhdestvo_balansa({1: 0.0, 2: 3225.0, 3: 100.0}, {1: 5000.0, 2: 0.0, 3: 0.0})
    assert [x["userId"] for x in t] == [1, 2] and t[0]["delta"] == -5000 and "удалён" in t[0]["smysl"], t
    # долг: сходится по одной из формул — не расхождение; иначе меньшая дельта (удаление не прячется)
    t2 = tozhdestvo_balansa({5: -8200.0, 6: 0.0}, {5: 0.0, 6: 3000.0}, dolg={5: 8200.0, 6: 8200.0})
    assert [(x["userId"], x["delta"]) for x in t2] == [(6, -3000.0)], t2
    assert kandidaty({1, 2, 3}, {2, 3, 4}) == [1]
    assert msk("2026-10-12T19:40:13Z") == "12.10 22:40" and msk("2026-10-12 19:40:13") == "12.10 22:40"
    # шум «Справочно»: обычная отметка, обычное списание, обычное окончание — не события
    assert ocenit_pravku("lesson_records", {"visit": None, "paid": False, "bill": None},
                         {"visit": True, "paid": True, "bill": {"id": 1, "summa": 975}}) is None
    assert ocenit_pravku("user_subscriptions", {"statusId": 2, "visitCount": 1, "visitedCount": 0, "payed": 2500},
                         {"statusId": 4, "visitCount": 1, "visitedCount": 1, "payed": 2500}) is None
    oc5 = ocenit_pravku("user_subscriptions", {"visitCount": 8, "price": 9000, "payed": 9000},
                        {"visitCount": 9, "price": 9000, "payed": 9000})
    assert oc5 and oc5["level"] == "yellow" and "добавлены занятия" in oc5["chto"], oc5
    oc6 = ocenit_pravku("user_subscriptions", {"statusId": 2, "visitCount": 8, "visitedCount": 5, "payed": 9000, "endDate": "2020-01-31"},
                        {"statusId": 4, "visitCount": 8, "visitedCount": 5, "payed": 9000, "endDate": "2020-01-31"})
    assert oc6 and oc6["level"] == "info" and "по сроку" in oc6["chto"], oc6
    oc7 = ocenit_pravku("lesson_records", {"visit": False, "goodReason": False}, {"visit": False, "goodReason": True})
    assert oc7 and "уважительным" in oc7["chto"], oc7
    # память удалений чистится по сроку, а не по алфавиту
    d0 = date(2026, 10, 15)
    iz = pochistit_izvestno({"lesson_records:9": "2026-10-14", "payments:1": "2025-01-01", "user_subscriptions:3": "2026-09-01"}, d0)
    assert iz == {"lesson_records:9": "2026-10-14", "user_subscriptions:3": "2026-09-01"}, iz
    # устаревший триггер: в dry только сообщается, в боевом — пересоздаётся одной транзакцией
    conn.execute("DROP TRIGGER trg_izm_payments")
    conn.execute("CREATE TRIGGER trg_izm_payments AFTER UPDATE ON payments BEGIN SELECT 1; END")
    assert ensure(conn, peresozdat=False) == ["trg_izm_payments"]
    assert "SELECT 1" in conn.execute("SELECT sql FROM sqlite_master WHERE name='trg_izm_payments'").fetchone()[0]
    assert ensure(conn) == ["trg_izm_payments"]
    sql_t = conn.execute("SELECT sql FROM sqlite_master WHERE name='trg_izm_payments'").fetchone()[0]
    assert "INSERT INTO izmeneniya" in sql_t, sql_t
    assert ensure(conn) == [], "повторный ensure не должен ничего пересоздавать"
    # 15.10: остаток сверх удалённых — по обеим формулам (с долгом и без)
    assert ostatok_balansa({"d1": -11200.0, "d2": -3000.0}, 3000.0) is None      # долг 8200, удалён приход 3000
    assert ostatok_balansa({"d1": -35750.0, "d2": -35750.0}, 32750.0) == -3000.0  # сверх известного удаления
    # 15.10: «по сроку» — по дню правки и только если дата окончания не сдвинута в этой же правке
    sub = {"statusId": 2, "visitCount": 9, "visitedCount": 4, "payed": 8775}
    oa = ocenit_pravku("user_subscriptions", dict(sub, endDate="2026-10-31"), dict(sub, statusId=4, endDate="2026-10-01"), den="2026-10-15")
    assert oa and oa["level"] == "yellow" and "сдвинута" in oa["chto"], oa
    ob = ocenit_pravku("user_subscriptions", dict(sub, endDate="2026-10-12"), dict(sub, statusId=4, endDate="2026-10-12"), den="2026-10-10")
    assert ob and ob["level"] == "yellow" and "закрыт до конца" in ob["chto"], ob
    oc8 = ocenit_pravku("user_subscriptions", dict(sub, payed=0, endDate="2026-11-30"), dict(sub, payed=0, statusId=4, endDate="2026-11-30"), den="2026-10-15")
    assert oc8 and oc8["level"] == "info" and "неоплаченный" in oc8["chto"], oc8
    # 15.10: уважительная причина — штатные 50 %, не уступка; без дубля строки
    og = ocenit_pravku("lesson_records", {"visit": False, "goodReason": False, "bill": {"summa": 975}},
                       {"visit": False, "goodReason": True, "bill": {"summa": 487.5}})
    assert og and og["level"] == "info" and "уважительной" in og["chto"] and "отмечен" not in og["chto"], og
    # 15.10: цена за занятие — «+3 занятия и +1 ₽» тоже уступка; честное увеличение — нет
    ov = ocenit_pravku("user_subscriptions", {"visitCount": 8, "price": 9000, "payed": 9000}, {"visitCount": 11, "price": 9001, "payed": 9000})
    assert ov and ov["level"] == "yellow", ov
    ow = ocenit_pravku("user_subscriptions", {"visitCount": 8, "price": 9000, "payed": 9000}, {"visitCount": 12, "price": 13500, "payed": 13500})
    assert ow and ow["level"] == "info", ow
    # 15.10: красное — только пропавший приход денег; пересчёт абонемента — не тревога
    assert uroven_udaleniya("payments", {"optype": "income", "summa": 5000}) == "red"
    assert uroven_udaleniya("payments", {"optype": "income", "summa": 0}) == "yellow"
    assert uroven_udaleniya("payments", {"optype": "refund", "summa": 3000}) == "yellow"
    assert uroven_udaleniya("payments", {"optype": "debit", "summa": -8400}) == "info"
    assert uroven_udaleniya("user_subscriptions", {"payed": 9450}) == "yellow"
    assert uroven_udaleniya("lesson_records", {"visit": True}) == "info"
    _selftest_run()
    print("selftest ok: журнал правок, оценка правок, шум, тождество баланса, кандидаты, время, "
          "память удалений, триггеры, боевой прогон на файловой базе")
    return 0


def _selftest_run() -> None:
    """Боевой прогон run() на временной ФАЙЛОВОЙ базе с поддельным МойКлассом.
    14.10, повторное ревью: в памяти не ловилась взаимоблокировка — DELETE без
    commit держал замок, db.set_state ждал 30 с и падал. Здесь прогон обязан
    уложиться в секунды, сдвинуть закладку журнала, почистить старые строки,
    записать файл дня с inbox_posted и не повторить удаление назавтра.
    Сеть не нужна: МойКласс, автопилот, лента и синк подменены."""
    import tempfile
    import time
    import types

    global OUT_DIR
    pkg = sys.modules[__package__]
    from . import config, db

    class _NotFound(Exception):
        pass

    live_pay = [{"id": 2, "userId": 11, "summa": 5000, "optype": "income", "date": "2026-10-13"}]

    class FakeMK:
        def __init__(self, *a, **k):
            pass

        def fetch_all(self, path, keys, params=None):
            if path.endswith("/payments"):
                uid = (params or {}).get("userId")
                return [p for p in live_pay if uid is None or p["userId"] == uid]
            return []

        def get(self, path):
            raise _NotFound(f"404 Client Error: Not Found for url {path}")

        def close(self):
            pass

    inbox, flags = [], []
    fakes = {
        "moyklass_client": types.SimpleNamespace(MoyklassClient=FakeMK),
        "sync": types.SimpleNamespace(get_api_key=lambda: "test"),
        "autopilot": types.SimpleNamespace(inbox_add=lambda text, phone="", who="", source="": inbox.append((who, phone, text)) or True),
        "audit": types.SimpleNamespace(HIGH="high", MID="mid", add_flag=lambda *a, **k: flags.append(a)),
    }
    saved_mods = {k: sys.modules.get(f"{__package__}.{k}") for k in fakes}
    saved_attr = {k: getattr(pkg, k, None) for k in fakes}
    saved_db, saved_out = config.DB_PATH, OUT_DIR
    with tempfile.TemporaryDirectory() as tmp:
        try:
            config.DB_PATH = Path(tmp) / "kidsup.db"
            OUT_DIR = Path(tmp) / "sverka_chestnosti"
            for k, m in fakes.items():
                sys.modules[f"{__package__}.{k}"] = m
                setattr(pkg, k, m)
            db.init_db()
            ensure()
            raw1 = {"id": 1, "userId": 10, "summa": 5000, "optype": "income", "date": "2026-10-12",
                    "paymentTypeId": 1, "managerId": 7, "createdAt": "2026-10-12T09:00:00Z"}
            raw2 = {"id": 2, "userId": 11, "summa": 8750, "optype": "income", "date": "2026-10-13",
                    "paymentTypeId": 64725, "managerId": 7}
            ups = ("INSERT INTO payments(id,user_id,date,summa,optype,comment,raw) VALUES(?,?,?,?,?,?,?) "
                   "ON CONFLICT(id) DO UPDATE SET summa=excluded.summa, raw=excluded.raw")
            with db.get_conn() as c:
                c.execute("INSERT INTO managers(id,name) VALUES(7,'Тест Админ')")
                c.execute("INSERT INTO users(id,name,phone,balance,created_at) VALUES(10,'Клиент А','',0,'2026-01-01')")
                c.execute("INSERT INTO users(id,name,phone,balance,created_at) VALUES(11,'Клиент Б','',5000,'2026-01-01')")
                c.execute(ups, (1, 10, "2026-10-12", 5000, "income", "", json.dumps(raw1)))
                c.execute(ups, (2, 11, "2026-10-13", 8750, "income", "", json.dumps(raw2)))
                c.execute(ups, (2, 11, "2026-10-13", 5000, "income", "", json.dumps(dict(raw2, summa=5000))))
                c.execute("INSERT INTO izmeneniya(ts,tbl,rid,user_id,old_raw,new_raw) "
                          "VALUES (datetime('now','-100 days'),'payments',99,99,'{}','{}')")
            db.set_state("last_sync", datetime.now().isoformat(timespec="seconds"))

            day1, day2 = "2026-10-15", "2026-10-16"
            Rd = run(day1, dry=True)
            assert not db.get_state("sverka_chestnosti_id") and not (OUT_DIR / f"{day1}.json").exists() and not inbox, "dry что-то записал"
            assert any(x["kind"] == "удаление: payments" for x in Rd["nakhodki"]), Rd["nakhodki"]

            t0 = time.monotonic()
            R1 = run(day1, dry=False)
            sec = time.monotonic() - t0
            assert sec < 10, f"боевой прогон шёл {sec:.1f} с — замок базы?"
            assert proshla(day1) and int(db.get_state("sverka_chestnosti_id") or 0) > 0
            with db.get_conn() as c:
                assert c.execute("SELECT COUNT(*) FROM izmeneniya WHERE ts < datetime('now','-90 days')").fetchone()[0] == 0, \
                    "старая строка журнала не удалена"
            f = json.loads((OUT_DIR / f"{day1}.json").read_text(encoding="utf-8"))
            assert f.get("inbox_posted", {}).get("Борис", 0) >= 1, f.get("inbox_posted")
            assert _j(db.get_state("sverka_chestnosti_udaleno")) == {"payments:1": day1}
            udal = [t for w, p, t in inbox if "удалён платёж" in t]
            assert len(udal) == 1 and all(p == "" for _, p, _ in inbox), inbox    # без дубля по тождеству, без телефона
            assert any("уменьшена 8750 → 5000" in t for _, _, t in inbox), inbox
            assert R1["cifry"]["pravok_v_zhurnale"] >= 1

            n_inbox = len(inbox)
            R2 = run(day2, dry=False)
            assert R2["cifry"]["pravok_v_zhurnale"] == 0, R2["cifry"]          # закладка сдвинулась
            assert R2["cifry"].get("uzhe_izvestnyh_udaleniy") == 1, R2["cifry"]
            assert not [t for _, _, t in inbox[n_inbox:] if "удалён платёж" in t], inbox[n_inbox:]
            assert load(day1)["day"] == day1 and "<h1>" in html(day2)
            assert proshla(day1) and not proshla("2026-10-20")
            # файл дня — не объект: страница и API не падают
            (OUT_DIR / "2026-10-17.json").write_text("[]", encoding="utf-8")
            assert "не читается" in load("2026-10-17")["summary"] and "<h1>" in html("2026-10-17")
            # второй боевой прогон поверх идущего не запускается
            assert _RUN_LOCK.acquire(blocking=False)
            try:
                run(day2, dry=False)
            except RuntimeError:
                pass
            else:
                raise AssertionError("второй боевой прогон должен отказаться")
            finally:
                _RUN_LOCK.release()
        finally:
            config.DB_PATH, OUT_DIR = saved_db, saved_out
            for k in fakes:
                if saved_mods[k] is None:
                    sys.modules.pop(f"{__package__}.{k}", None)
                else:
                    sys.modules[f"{__package__}.{k}"] = saved_mods[k]
                if saved_attr[k] is None:
                    if hasattr(pkg, k):
                        delattr(pkg, k)
                else:
                    setattr(pkg, k, saved_attr[k])


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(__doc__)
