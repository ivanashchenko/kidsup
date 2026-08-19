"""Аудит: единая лента аномалий по деньгам, звонкам и переписке.

Мы ловим не «плохих людей», а расхождения между тем, что записано в системах,
и тем, что должно быть записано. Любая находка попадает в таблицу
`audit_flags` и живёт там, пока её не разберут: new → ok (ложное срабатывание)
или violation (подтвердилось).

Почему это вообще понадобилось. На 20.08.2026 из 77 августовских приходов
в МойКласс менеджером подписаны 39 — то есть под половиной денег не стоит
ничьей подписи. Пока это так, нельзя ни честно посчитать бонус, ни отличить
оплату, которую провёл администратор, от оплаты, которая прошла мимо кассы.

Запуск разово:
    python -m app.audit day            # аудит за вчера
    python -m app.audit feed           # что лежит в ленте
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta

from . import db

log = logging.getLogger("kidsup.audit")

# ── уровни ────────────────────────────────────────────────────────────────
HIGH = "high"      # деньги: разбираем в тот же день
MID = "mid"        # процесс: разбираем на утренней планёрке
LOW = "low"        # гигиена: копим и показываем цифрой

MANAGERS = {84116: "Борис", 154181: "Лиза", 202856: "Лена",
            229704: "Маша", 232805: "Аня", 232763: "Ира"}

# Стоп-слова в расшифровках звонков и в переписке. Формулировки подобраны
# так, чтобы не ловить обычный разговор: «карта» сама по себе не флаг,
# флаг — «переведите мне на карту».
FRAUD_PATTERNS: list[tuple[str, str, str]] = [
    (r"перевед[иеё]те?\s+(мне\b|на\s+(мою|мой|карт))", "просьба перевести лично", HIGH),
    (r"(я\s+(сам|сама)\s+провед|проведу\s+сам)", "«я сам проведу» мимо кассы", HIGH),
    (r"(сбер\w*|тинько\w*|озон\w*)\s+по\s+номеру", "перевод по номеру телефона", HIGH),
    (r"без\s+чека", "оплата без чека", HIGH),
    (r"мимо\s+кассы", "прямая формулировка «мимо кассы»", HIGH),
    (r"наличк\w*\s+(мне|лично|в\s+руки)", "наличные лично в руки", HIGH),
    (r"занима\w+\s+(частно|индивидуально)\s+(у\s+меня|со\s+мной|дома)", "увод на частные занятия", HIGH),
    (r"(мой|личн\w+)\s+(номер|телефон)\b", "обмен личными контактами", MID),
    (r"(беспл\w*\s+пробн\w*|пробн\w*\s+(урок\w*\s+|занятие\s+)?беспл\w*)",
     "запрещённая формулировка «бесплатное пробное»", MID),
    (r"скидк\w*\s+(15|20|25|30|40|50)\s*%", "скидка сверх правил (у нас максимум 10%)", MID),
    (r"наш\s+педагог\s+ушла?\s+в\s+друг", "разговор об уходе педагога", MID),
]
_COMPILED = [(re.compile(p, re.I), t, lv) for p, t, lv in FRAUD_PATTERNS]


# ── хранилище ─────────────────────────────────────────────────────────────
def _ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS audit_flags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT, key TEXT, level TEXT, title TEXT, detail TEXT,
        user_id INTEGER, manager_id INTEGER, day TEXT,
        status TEXT DEFAULT 'new', created_at TEXT)""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_audit_uniq "
                 "ON audit_flags (kind, key)")
    conn.execute("""CREATE TABLE IF NOT EXISTS call_transcripts (
        recording_id TEXT PRIMARY KEY, ts TEXT, phone TEXT, ext TEXT,
        dur INTEGER, user_id INTEGER, text TEXT, score INTEGER)""")


def _now_iso() -> str:
    from .autopilot import _now
    return _now().isoformat(timespec="seconds")


def add_flag(kind: str, key: str, level: str, title: str, detail: str = "",
             user_id: int | None = None, manager_id: int | None = None,
             day: str | None = None) -> bool:
    """Кладём находку в ленту. Повторный вызов с тем же ключом ничего не делает —
    поэтому проверки можно гонять хоть каждый час, лента не распухнет."""
    with db.get_conn() as conn:
        _ensure(conn)
        cur = conn.execute(
            "INSERT OR IGNORE INTO audit_flags "
            "(kind, key, level, title, detail, user_id, manager_id, day, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (kind, key, level, title[:200], detail[:600], user_id, manager_id,
             day or date.today().isoformat(), _now_iso()))
        return cur.rowcount > 0


def save_transcript(recording_id: str, text: str, phone: str = "", ext: str = "",
                    dur: int = 0, ts: str = "", user_id: int | None = None) -> None:
    """Расшифровка разговора должна где-то жить.

    Раньше она умирала сразу после того, как я вписывал заметку в карточку:
    искать по ней задним числом было нельзя, считать качество разговоров —
    тоже. Теперь каждая расшифровка ложится сюда, и по ней работают
    и антифрод, и оценка качества."""
    with db.get_conn() as conn:
        _ensure(conn)
        conn.execute(
            "INSERT OR REPLACE INTO call_transcripts "
            "(recording_id, ts, phone, ext, dur, user_id, text, score) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (recording_id, ts or _now_iso(), phone[-10:], ext, dur, user_id,
             text, call_score(text)))
    scan_text(text, source=f"звонок {recording_id}", phone=phone, user_id=user_id)


# ── качество разговора ────────────────────────────────────────────────────
QUALITY_CHECKS: list[tuple[str, str]] = [
    (r"здравствуйте|добрый\s+(день|вечер|утро)", "поздоровался"),
    (r"как\s+(вас|вашего\s+реб[её]нка)\s+зовут|как\s+зовут\s+реб", "спросил имя"),
    (r"сколько\s+(лет|годик)", "спросил возраст"),
    (r"заниман?лись?\s+(ли\s+)?(раньше|где-то|уже)|был\s+опыт", "спросил про опыт"),
    (r"что\s+(вас|для\s+вас)\s+важн|что\s+хотели\s+бы|что\s+беспокоит", "выявил потребность"),
    (r"\b\d{1,2}[:.]\d{2}\b|во\s+вторник|в\s+среду|в\s+четверг|в\s+пятницу|в\s+субботу",
     "предложил конкретное время"),
    (r"рокоссовск|янтар", "назвал адрес"),
    (r"жду\s+вас|запишу\s+вас|записала\s+вас|подтвержу|перезвоню\s+в", "зафиксировал шаг"),
]
_QC = [(re.compile(p, re.I), n) for p, n in QUALITY_CHECKS]


def call_score(text: str) -> int:
    """0–8 баллов на разговор: сколько пунктов скрипта админ реально закрыл."""
    return sum(1 for rx, _ in _QC if rx.search(text or ""))


def call_gaps(text: str) -> list[str]:
    return [name for rx, name in _QC if not rx.search(text or "")]


# ── проверки ──────────────────────────────────────────────────────────────
def scan_text(text: str, source: str, phone: str = "",
              user_id: int | None = None) -> list[str]:
    """Стоп-слова в любом тексте: расшифровка звонка или сообщение в чате."""
    hits = []
    for rx, title, level in _COMPILED:
        m = rx.search(text or "")
        if not m:
            continue
        hits.append(title)
        around = (text or "")[max(0, m.start() - 90):m.end() + 90].replace("\n", " ")
        add_flag("речь", f"{source}:{title}", level, title,
                 f"{source} · +{phone[-10:] if phone else '?'} · «…{around}…»",
                 user_id=user_id)
    return hits


def unsigned_payments(day: str | None = None) -> list[dict]:
    """Приходы без менеджера.

    Главная дыра: под оплатой не стоит ничьей подписи. Значит бонус не
    посчитать, а деньги, проведённые вручную, невозможно отличить от чужих."""
    d = day or (date.today() - timedelta(days=1)).isoformat()
    out = []
    with db.get_conn() as conn:
        _ensure(conn)
        seen = conn.execute("SELECT COUNT(*) FROM payments WHERE date = ?", (d,)).fetchone()[0]
        rows = [dict(r) for r in conn.execute("""
            SELECT p.id, p.user_id, p.summa, p.comment, u.name
            FROM payments p LEFT JOIN users u ON u.id = p.user_id
            WHERE p.date = ? AND p.optype = 'income' AND p.summa > 0
              AND json_extract(p.raw, '$.managerId') IS NULL
            ORDER BY p.summa DESC""", (d,)).fetchall()]
    if not seen:
        # синхронизация отстаёт на день-два, а деньги вчерашние нужны сегодня —
        # спрашиваем МойКласс напрямую, иначе проверка молча покажет ноль
        rows = _unsigned_from_api(d)
    for r in rows:
        out.append({"id": r["id"], "user_id": r["user_id"], "summa": r["summa"],
                    "name": r.get("name"), "comment": r.get("comment")})
        add_flag("оплата без подписи", f"pay{r['id']}", HIGH,
                 f"Оплата {r['summa']:,.0f} ₽ без менеджера".replace(",", " "),
                 f"{r.get('name') or r['user_id']} · {(r.get('comment') or '')[:120]}",
                 user_id=r["user_id"], day=d)
    return out


def _unsigned_from_api(d: str) -> list[dict]:
    try:
        from .moyklass_client import MoyklassClient
        mk = MoyklassClient(db.get_setting("moyklass_api_key"))
    except Exception:
        return []
    try:
        resp = mk.get("/v1/company/payments", {"date": [d, d], "limit": 500})
        items = resp.get("payments", resp) if isinstance(resp, dict) else resp
    except Exception:
        log.warning("не удалось спросить оплаты за %s у МойКласс", d)
        return []
    finally:
        try:
            mk.close()
        except Exception:
            pass
    bad = [x for x in items
           if x.get("optype") == "income" and (x.get("summa") or 0) > 0
           and not x.get("managerId")]
    names = {}
    if bad:
        with db.get_conn() as conn:
            for r in conn.execute(
                    "SELECT id, name FROM users WHERE id IN (%s)"
                    % ",".join("?" * len({x["userId"] for x in bad})),
                    tuple({x["userId"] for x in bad})):
                names[r["id"]] = r["name"]
    return [{"id": x["id"], "user_id": x["userId"], "summa": x["summa"],
             "name": names.get(x["userId"]), "comment": x.get("comment")}
            for x in sorted(bad, key=lambda y: -y["summa"])]


def refunds(day: str | None = None, threshold: float = 3000) -> list[dict]:
    """Возвраты крупнее порога — каждый под именем и с причиной."""
    d = day or (date.today() - timedelta(days=1)).isoformat()
    out = []
    with db.get_conn() as conn:
        _ensure(conn)
        rows = conn.execute("""
            SELECT p.id, p.user_id, p.summa, p.comment, u.name
            FROM payments p LEFT JOIN users u ON u.id = p.user_id
            WHERE p.date = ? AND p.optype = 'refund' AND ABS(p.summa) >= ?
            ORDER BY ABS(p.summa) DESC""", (d, threshold)).fetchall()
    for r in rows:
        out.append({"id": r["id"], "summa": abs(r["summa"]), "name": r["name"]})
        add_flag("возврат", f"ref{r['id']}", HIGH,
                 f"Возврат {abs(r['summa']):,.0f} ₽".replace(",", " "),
                 f"{r['name'] or r['user_id']} · {(r['comment'] or 'без причины')[:120]}",
                 user_id=r["user_id"], day=d)
    return out


def off_price(day: str | None = None) -> list[dict]:
    """Оплата, сильно выпадающая из привычной суммы по этому же курсу.

    Прайса в API нет, поэтому эталон берём из фактов: медиана приходов по
    тому же тексту курса за последние полгода. Отклонение вниз больше чем
    на 25% — повод спросить, на каком основании скидка. У нас максимум 10%,
    и скидки не суммируются."""
    d = day or (date.today() - timedelta(days=1)).isoformat()
    since = (date.fromisoformat(d) - timedelta(days=180)).isoformat()
    out = []
    with db.get_conn() as conn:
        _ensure(conn)
        rows = conn.execute("""
            SELECT p.id, p.user_id, p.summa, p.comment, u.name
            FROM payments p LEFT JOIN users u ON u.id = p.user_id
            WHERE p.date = ? AND p.optype = 'income' AND p.summa > 0
              AND p.comment IS NOT NULL AND p.comment <> ''""", (d,)).fetchall()
        for r in rows:
            base = (r["comment"] or "").split(" - ")[0][:40]
            if not base:
                continue
            peers = [x[0] for x in conn.execute("""
                SELECT summa FROM payments
                WHERE optype='income' AND summa > 0 AND date BETWEEN ? AND ?
                  AND comment LIKE ? AND id <> ?""",
                (since, d, base + "%", r["id"])).fetchall()]
            if len(peers) < 5:
                continue                      # мало данных — не выдумываем эталон
            peers.sort()
            med = peers[len(peers) // 2]
            if med <= 0 or r["summa"] >= med * 0.75:
                continue
            off = round((1 - r["summa"] / med) * 100)
            out.append({"id": r["id"], "summa": r["summa"], "median": med, "off": off})
            add_flag("скидка", f"disc{r['id']}", MID,
                     f"Оплата на {off}% ниже обычной по «{base}»",
                     f"{r['name'] or r['user_id']} · заплатил {r['summa']:,.0f} ₽, "
                     f"обычно {med:,.0f} ₽".replace(",", " "),
                     user_id=r["user_id"], day=d)
    return out


def ghost_paying_clients(days: int = 45) -> list[dict]:
    """Клиент платит и ходит, а в наших каналах связи его нет.

    Ни звонка через АТС, ни сообщения в Wazzup за полтора месяца. Либо
    общение идёт с личного телефона в обход системы, либо семья на
    автопилоте и её никто не ведёт. Оба варианта надо знать."""
    since = (date.today() - timedelta(days=days)).isoformat()
    out = []
    with db.get_conn() as conn:
        _ensure(conn)
        has_calls = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='mango_calls'").fetchone()[0]
        # если журналов почти нет, «тишина» означает не обход системы,
        # а то, что мы сами ещё не собрали данные — молчим, а не шумим
        chats = conn.execute("SELECT COUNT(*) FROM wazzup_inbox WHERE ts >= ?",
                             (since,)).fetchone()[0]
        calls = conn.execute("SELECT COUNT(*) FROM mango_calls WHERE ts >= ?",
                             (since,)).fetchone()[0] if has_calls else 0
        if chats + calls < 200:
            log.info("ghost_paying_clients: журналов мало (%d) — проверка пропущена",
                     chats + calls)
            return []
        rows = conn.execute("""
            SELECT u.id, u.name, substr(u.phone,-10) p10, SUM(pp.summa) s
            FROM payments pp JOIN users u ON u.id = pp.user_id
            WHERE pp.optype='income' AND pp.summa > 0 AND pp.date >= ?
            GROUP BY u.id HAVING s > 0 ORDER BY s DESC LIMIT 60""", (since,)).fetchall()
        for r in rows:
            if len(out) >= 25:      # лента должна быть разбираемой за одно утро
                break
            p10 = r["p10"] or ""
            if len(p10) < 10:
                continue
            seen = conn.execute(
                "SELECT COUNT(*) FROM wazzup_inbox WHERE substr(phone,-10)=? AND ts >= ?",
                (p10, since)).fetchone()[0]
            if not seen and has_calls:
                seen = conn.execute(
                    "SELECT COUNT(*) FROM mango_calls WHERE substr(phone,-10)=? AND ts >= ?",
                    (p10, since)).fetchone()[0]
            if seen:
                continue
            out.append({"id": r["id"], "name": r["name"], "sum": r["s"]})
            add_flag("вне каналов", f"ghost{r['id']}:{since}", MID,
                     "Платит, но ни звонка, ни переписки",
                     f"{r['name']} · {r['s']:,.0f} ₽ за {days} дней, "
                     f"в АТС и Wazzup — тишина".replace(",", " "),
                     user_id=r["id"])
    return out


def calls_without_extension(day: str | None = None) -> int:
    """Разговоры, которых нет в АТС.

    Если по клиенту есть запись в карточке о разговоре, а в журнале Mango
    за этот день звонка нет — значит звонили с личного мобильного. Считаем
    только количество: имена разбираем глазами."""
    d = day or (date.today() - timedelta(days=1)).isoformat()
    with db.get_conn() as conn:
        _ensure(conn)
        if not conn.execute("SELECT COUNT(*) FROM sqlite_master "
                            "WHERE name='mango_calls'").fetchone()[0]:
            return 0
        return conn.execute("SELECT COUNT(*) FROM mango_calls WHERE substr(ts,1,10)=?",
                            (d,)).fetchone()[0]


def daily_audit(day: str | None = None) -> dict:
    """Один прогон всех проверок. Возвращает сводку для дайджеста."""
    d = day or (date.today() - timedelta(days=1)).isoformat()
    res = {
        "day": d,
        "unsigned": unsigned_payments(d),
        "refunds": refunds(d),
        "discounts": off_price(d),
        "ghosts": ghost_paying_clients(),
        "calls": calls_without_extension(d),
    }
    with db.get_conn() as conn:
        _ensure(conn)
        res["open_high"] = conn.execute(
            "SELECT COUNT(*) FROM audit_flags WHERE status='new' AND level=?",
            (HIGH,)).fetchone()[0]
    unsigned_sum = sum(x["summa"] for x in res["unsigned"])
    log.info("аудит %s: без подписи %d на %.0f ₽, возвратов %d, скидок %d, "
             "вне каналов %d, открытых красных %d", d, len(res["unsigned"]),
             unsigned_sum, len(res["refunds"]), len(res["discounts"]),
             len(res["ghosts"]), res["open_high"])
    return res


def feed(status: str = "new", limit: int = 200) -> list[dict]:
    with db.get_conn() as conn:
        _ensure(conn)
        q = ("SELECT * FROM audit_flags WHERE status = ? "
             "ORDER BY CASE level WHEN 'high' THEN 0 WHEN 'mid' THEN 1 ELSE 2 END, "
             "id DESC LIMIT ?")
        return [dict(r) for r in conn.execute(q, (status, limit)).fetchall()]


def resolve(flag_id: int, status: str = "ok") -> bool:
    with db.get_conn() as conn:
        _ensure(conn)
        cur = conn.execute("UPDATE audit_flags SET status=? WHERE id=?",
                           (status, flag_id))
        return cur.rowcount > 0


def summary() -> dict:
    """Короткая сводка для дашборда: сколько чего висит неразобранным."""
    with db.get_conn() as conn:
        _ensure(conn)
        by_level = {r[0]: r[1] for r in conn.execute(
            "SELECT level, COUNT(*) FROM audit_flags WHERE status='new' "
            "GROUP BY level").fetchall()}
        by_kind = {r[0]: r[1] for r in conn.execute(
            "SELECT kind, COUNT(*) FROM audit_flags WHERE status='new' "
            "GROUP BY kind ORDER BY 2 DESC").fetchall()}
        scored = conn.execute(
            "SELECT COUNT(*), AVG(score) FROM call_transcripts").fetchone()
    return {"high": by_level.get(HIGH, 0), "mid": by_level.get(MID, 0),
            "low": by_level.get(LOW, 0), "by_kind": by_kind,
            "calls_scored": scored[0] or 0,
            "avg_call_score": round(scored[1] or 0, 1)}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Аудит KidsUP")
    ap.add_argument("command", choices=["day", "feed", "summary"])
    ap.add_argument("--day")
    args = ap.parse_args()
    if args.command == "day":
        r = daily_audit(args.day)
        print(json.dumps({k: (len(v) if isinstance(v, list) else v)
                          for k, v in r.items()}, ensure_ascii=False, indent=2))
    elif args.command == "feed":
        for f in feed():
            print(f"[{f['level']}] {f['title']} — {f['detail'][:100]}")
    else:
        print(json.dumps(summary(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
