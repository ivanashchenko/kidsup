"""Мост МойКласс → Roistat: выгрузка оплат как заказов.

Каждый платёж-приход из CRM становится заказом в Roistat со статусом "paid".
После первой выгрузки в Roistat (Интеграции → API) нужно один раз
сопоставить статус "paid" → «Оплачен» (продажа).

Запуск:
    python -m app.roistat push --since 2026-08-01 --dry-run
    python -m app.roistat push --since 2026-08-01
"""

import argparse
import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from . import db

BASE = "https://cloud.roistat.com/api/v1"
BATCH = 100
MSK = ZoneInfo("Europe/Moscow")
log = logging.getLogger("kidsup.roistat")


def _params() -> str:
    return f"project={db.get_setting('roistat_project')}&key={db.get_setting('roistat_key')}"


VISIT_RE = re.compile(r"Промокод:\s*(\d{3,})")


def _ensure_visits_table(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS roistat_visits (
        phone10 TEXT PRIMARY KEY, visit TEXT, source TEXT, ts TEXT)""")


def collect_visits_from_crm(mk, since: str, limit: int = 400) -> int:
    """Номер визита Roistat живёт в карточке МойКласс единственным способом —
    строкой «Промокод: 206750» внутри комментария, который пишет интеграция.
    Отдельного поля под него нет, поэтому вытаскиваем регуляркой и храним у себя:
    без визита оплата приходит в Roistat «ниоткуда» и к рекламе не привязывается.

    Возвращает число новых связок «телефон → визит»."""
    with db.get_conn() as conn:
        _ensure_visits_table(conn)
        rows = conn.execute("""
            SELECT DISTINCT p.user_id, u.phone FROM payments p
            JOIN users u ON u.id = p.user_id
            WHERE p.date >= ? AND u.phone IS NOT NULL AND u.phone != ''
            ORDER BY p.date DESC LIMIT ?""", (since, limit)).fetchall()
        known = {r[0] for r in conn.execute("SELECT phone10 FROM roistat_visits")}
    added = 0
    for r in rows:
        ph10 = "".join(ch for ch in str(r["phone"] or "") if ch.isdigit())[-10:]
        if len(ph10) != 10 or ph10 in known:
            continue
        try:
            cm = mk.get("/v1/company/userComments", {"userId": r["user_id"]})
        except Exception:
            continue
        text = " ".join((c.get("comment") or "") for c in (cm.get("userComments") or []))
        m = VISIT_RE.search(text)
        if not m:
            continue
        with db.get_conn() as conn:
            _ensure_visits_table(conn)
            conn.execute("INSERT OR REPLACE INTO roistat_visits (phone10, visit, source, ts) "
                         "VALUES (?, ?, 'crm_comment', datetime('now'))", (ph10, m.group(1)))
        known.add(ph10)
        added += 1
    return added


def _visits_by_phone() -> dict[str, str]:
    """Номер визита Roistat по последним 10 цифрам телефона: из заявок с сайта,
    из кликов по кнопкам мессенджеров и из комментариев Roistat в карточках CRM.
    Без этого выгрузка оплат приходит в Roistat «ниоткуда» и сквозная аналитика
    по источникам не считается."""
    out: dict[str, str] = {}
    with db.get_conn() as conn:
        try:
            _ensure_visits_table(conn)
            for r in conn.execute("SELECT phone10, visit FROM roistat_visits"):
                out[r[0]] = r[1]
        except Exception:
            pass
        for table, phone_col in (("site_leads", "phone"), ("messenger_clicks", "matched_phone")):
            try:
                rows = conn.execute(
                    f"SELECT {phone_col} ph, roistat_visit FROM {table} "
                    f"WHERE roistat_visit IS NOT NULL AND roistat_visit != '' ORDER BY id"
                ).fetchall() if table == "messenger_clicks" else conn.execute(
                    "SELECT phone ph, roistat FROM site_leads "
                    "WHERE roistat IS NOT NULL AND roistat != '' ORDER BY id").fetchall()
            except Exception:
                continue
            for r in rows:
                ph = "".join(ch for ch in str(r["ph"] or "") if ch.isdigit())[-10:]
                visit = r[1]
                if len(ph) == 10 and visit:
                    out[ph] = visit          # берём самый свежий
    return out


def build_orders(since: str) -> list[dict]:
    visits = _visits_by_phone()
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT p.id, p.date, p.optype, ABS(p.summa) amount, p.user_id,
                   u.name, u.phone, u.email
            FROM payments p LEFT JOIN users u ON u.id = p.user_id
            WHERE p.optype IN ('income', 'refund') AND ABS(p.summa) > 0 AND p.date >= ?
            ORDER BY p.date
        """, (since,)).fetchall()
    orders = []
    for r in rows:
        fields = {}
        if r["phone"]:
            fields["phone"] = str(r["phone"])
        if r["email"]:
            fields["email"] = r["email"]
        refund = r["optype"] == "refund"
        order = {
            "id": ("mkr" if refund else "mk") + str(r["id"]),
            "name": ("Возврат — " if refund else "Оплата — ") + (r["name"] or f"клиент {r['user_id']}"),
            "date_create": f"{r['date']}T12:00:00+0300",
            "status": "returned" if refund else "paid",
            "price": -float(r["amount"]) if refund else float(r["amount"]),
            "client_id": str(r["user_id"] or ""),
            "fields": fields,
        }
        ph10 = "".join(ch for ch in str(r["phone"] or "") if ch.isdigit())[-10:]
        if visits.get(ph10):
            order["roistat"] = visits[ph10]   # номер визита = склейка с рекламой
        orders.append(order)
    return orders


def push_lead(lead: dict) -> None:
    """Заявка с сайта уходит в Roistat сразу — чтобы воронка видела не только
    оплаты, но и обращения (иначе конверсия по источникам не считается)."""
    project, key = db.get_setting("roistat_project"), db.get_setting("roistat_key")
    if not project or not key:
        return
    order = {
        "id": "lead" + str(lead.get("lead_id") or lead.get("phone")),
        "name": "Заявка с сайта — " + (lead.get("child") or "без имени"),
        "date_create": __import__("datetime").datetime.now().strftime("%Y-%m-%dT%H:%M:%S+0300"),
        "status": "lead",
        "price": 0,
        "client_id": str(lead.get("phone") or ""),
        "fields": {"phone": "+" + str(lead.get("phone") or ""),
                   "comment": (lead.get("course") or "") + " " + (lead.get("note") or "")},
    }
    if lead.get("roistat"):
        order["roistat"] = lead["roistat"]
    httpx.post(f"{BASE}/project/add-orders?project={project}",
               headers=_headers(), json=[order], timeout=20)


def _headers() -> dict:
    return {"Api-key": db.get_setting("roistat_key") or "", "Content-type": "application/json"}


def _has_error(j) -> bool:
    """Есть ли в разобранном ответе Roistat непустое поле error или errors —
    на любой глубине: ошибка по отдельному заказу внутри общего success —
    тоже отказ. "error": null и "errors": [] — не ошибка (14.10)."""
    if isinstance(j, dict):
        return any((k in ("error", "errors") and bool(v)) or _has_error(v)
                   for k, v in j.items())
    if isinstance(j, list):
        return any(_has_error(x) for x in j)
    return False


def _accepted(r: httpx.Response) -> bool:
    """Принял ли Roistat батч заказов.

    14.10: раньше — HTTP 200 и нет подстроки "error" в первых 200 знаках
    ответа; подстрока считала отказом и успешный ответ с "error": null.
    Теперь решаем по разобранному ответу: HTTP 200; ответ — JSON-объект или
    список (страница-заглушка с кодом 200 от прокси или техработ — не приём);
    поле status, если есть, — success или ok; нигде нет непустого error /
    errors. Настоящего успешного ответа add-orders мы ещё не видели: его
    начало сохраняется в roistat_last_ok (response) — после переключения
    коннектора на «Интеграцию по API» один раз сверить его с этим правилом."""
    if r.status_code != 200:
        return False
    try:
        j = r.json()
    except ValueError:
        return False
    if not isinstance(j, (dict, list)):
        return False
    if isinstance(j, dict) and str(j.get("status", "success")).lower() not in ("success", "ok"):
        return False
    return not _has_error(j)


def push(since: str, dry_run: bool = True) -> dict:
    """Отдаёт итог словарём, а не бросает исключение: приём сделок в Roistat
    может быть выключен на стороне проекта (метод add-orders доступен только
    при заведённой «Интеграции по API»), и тогда ночной прогон должен оставлять
    внятную причину в логе, а не падать молча каждые сутки.

    14.10: итог явный. ok — Roistat принял все заказы либо отправлять было
    нечего: в окне нет оплат, тогда nothing_to_send=True и Roistat не
    спрашивали — это не «принято», roistat_last_ok не пишется. accepted —
    сколько заказов принято; sent — то же самое (так было до 14.10, ключ
    оставлен для старых вызовов, новым читать ok и accepted); posted —
    сколько ушло в Roistat (батчи, на которые он ответил); error — почему
    нет: ответ Roistat или сбой сети, до 500 знаков; response — последний
    ответ Roistat как есть, до 500 знаков. Раньше при отказе был только
    список errors, автопилот его не читал и всё равно отмечал день
    выгруженным: по данным 19.08 и 08.09 Roistat отклонял add-orders (в
    проекте коннектор «Мой Класс», а не «Интеграция по API»), а видно это
    было лишь в stdout сервера. Итог настоящей выгрузки теперь пишется в
    настройки (record_result) и виден в /api/ads/status; сбой этой записи
    итог не меняет — только лог. Что и как отправляется — без изменений."""
    orders = build_orders(since)
    total = sum(o["price"] for o in orders)
    res = {"since": since, "orders": len(orders), "sum": round(total, 2),
           "with_visit": sum(1 for o in orders if o.get("roistat"))}
    print(f"К выгрузке: {len(orders)} оплат на {total:,.0f} ₽ (с {since})")
    no_exchange = {"ok": True, "sent": 0, "posted": 0, "accepted": 0,
                   "error": "", "response": "", "errors": []}
    if dry_run:
        for o in orders[:5]:
            print("  пример:", json.dumps(o, ensure_ascii=False)[:160])
        print("[dry-run] ничего не отправлено")
        return {**res, **no_exchange, "dry_run": True}
    if not orders:
        # 14.10: пустое окно (длинные праздники, встал синк оплат) — Roistat
        # не спрашиваем, и свежим «успехом» в статусе это не становится
        print("В окне нет оплат — в Roistat ничего не отправлено")
        return {**res, **no_exchange, "nothing_to_send": True}
    project = db.get_setting("roistat_project")
    posted, accepted, error, response = 0, 0, "", ""
    for i in range(0, len(orders), BATCH):
        chunk = orders[i:i + BATCH]
        try:
            r = httpx.post(f"{BASE}/project/add-orders?project={project}",
                           headers=_headers(), json=chunk, timeout=60)
        except Exception as e:          # сеть, таймаут: ответа Roistat нет
            error = f"нет ответа Roistat: {type(e).__name__}: {e}"[:500]
            print(f"  батч {i // BATCH + 1}: {error}")
            break
        posted += len(chunk)
        response = r.text[:500]
        print(f"  батч {i // BATCH + 1}: HTTP {r.status_code} {r.text[:200]}")
        if not _accepted(r):
            error = f"HTTP {r.status_code}: {r.text}"[:500]
            break                       # смысла долбить остальными батчами нет
        accepted += len(chunk)
    print(f"Итог: отправлено {posted}, принято {accepted} из {len(orders)}"
          + (f"; отказ: {error[:200]}" if error else ""))
    out = {**res, "ok": not error, "sent": accepted, "posted": posted,
           "accepted": accepted, "error": error, "response": response,
           "errors": [error] if error else []}   # sent, errors — как раньше, для старых вызовов
    # 14.10: Roistat уже ответил. Сбой записи в базу (database is locked)
    # итог не меняет — только лог: иначе принятая выгрузка выглядела бы
    # упавшей, а ручной вызов отдавал бы 500 без итога
    try:
        if accepted:
            db.set_setting("roistat_last_push", since)
    except Exception:
        log.exception("roistat_last_push не записан — итог выгрузки не меняется")
    try:
        record_result(out)
    except Exception:
        log.exception("итог выгрузки в Roistat не записан в настройки")
    return out


def record_result(res: dict) -> None:
    """Итог настоящей выгрузки — в настройки, чтобы его было видно без stdout.

    14.10: roistat_last_ok — время МСК, сколько заказов принято и начало
    ответа Roistat (response, до 200 знаков: по какому ответу признали
    приём, см. _accepted); roistat_last_error — время МСК и текст отказа (до
    500 знаков). Пишется одно из двух, второе остаётся от прошлого раза —
    верно то, что свежее. Показывает /api/ads/status (поле roistat_push).
    Пробный прогон (dry_run) и пустое окно (nothing_to_send) ничего не
    пишут: Roistat не спрашивали, и свежий «успех» не должен заслонять
    прошлый отказ."""
    if res.get("dry_run") or res.get("nothing_to_send"):
        return
    ts = datetime.now(MSK).isoformat(timespec="seconds")
    if res.get("ok"):
        db.set_setting("roistat_last_ok", json.dumps(
            {"ts": ts, "orders": res.get("accepted", 0), "since": res.get("since"),
             "response": str(res.get("response") or "")[:200]},
            ensure_ascii=False))
    else:
        db.set_setting("roistat_last_error", json.dumps(
            {"ts": ts, "error": str(res.get("error") or "причина неизвестна")[:500],
             "accepted": res.get("accepted", 0), "orders": res.get("orders"),
             "since": res.get("since")}, ensure_ascii=False))


def push_status() -> dict:
    """{last_ok, last_error} для /api/ads/status (14.10): что лежит в
    настройках. None — такого итога ещё не было."""
    out = {}
    for k in ("last_ok", "last_error"):
        raw = db.get_setting(f"roistat_{k}")
        try:
            out[k] = json.loads(raw) if raw else None
        except ValueError:
            out[k] = raw                # не JSON (руками) — как есть
    return out


def main():
    ap = argparse.ArgumentParser(description="Мост МойКласс → Roistat")
    ap.add_argument("command", choices=["push"])
    ap.add_argument("--since", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.command == "push":
        push(args.since, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
