"""Звонить ли семье — по карточке и журналу звонков, а не по нашим меткам.

17.10.2026, Ира: «большинство детей, которые есть в задачах, уже с
комментарием и чётким ответом… по таким задачам мы выглядим надоедами и
навязчивыми». В обзвоне дня из 37 семей у 15 ответ уже был: в карточке
«свяжусь сама», «далеко», «завершила общение», «после каникул», «сейчас
рано» — или 3–4 звонка без ответа и наше сообщение. Очереди звонков (возврат
«думает / недозвон / база», повторы недозвона в автопилоте) смотрели только
на статус и на свои метки и ставили звонок снова.

Правило одно для всех очередей:
  * решает последний СОДЕРЖАТЕЛЬНЫЙ комментарий админа или разбор звонка;
    отметки «но», «нет ответа», «не дозвонилась» ответом не считаются;
  * в нём ответ «не звонить» (свяжусь сама, далеко, другой центр, не
    актуально, после каникул, рано…) — не звоним, пока семья не объявится;
  * с последнего разговора — 3 исходящих без ответа и наше сообщение —
    не звоним (правило владельца 28.09: три попытки, потом сообщение).
"""
from __future__ import annotations

import re
from collections import defaultdict

NABOR_S = "2026-08-01"

# Ответ семьи, после которого звонок — навязчивость. Слова взяты из карточек
# обзвона 17.10; «недалеко» не ловится (граница слова).
STOP = re.compile(
    r"свяж\w*\s+сам|сам[аи]?\s+(?:свяж|напиш|позвон|обрат|верн|объяв)|"
    r"не\s+звонить|не\s+беспокоить|\bдалеко\b|"
    r"друг(?:ой|ую|ое|ие)\s+(?:центр|школ|сад|кружок|секци)|выбрали\s+друг|"
    r"не\s+актуальн|не\s+интересн|не\s+хот|"
    r"заверш\w*\s+общени|закрыл\w*\s+чат|"
    r"после\s+(?:\w+\s+)?каникул|ближе\s+к\s+\w+|на\s+будущее|"
    r"(?:сейчас|пока|ещ[её])\s+рано|рано\s+ещ",
    re.I)

# Отметка о недозвоне — не ответ семьи, решает комментарий до неё.
PUSTO = re.compile(
    r"^\W*(?:но|н/о|нет\s+ответа|не\s+дозвон\w*|недозвон\w*|не\s+берут\w*|не\s+взял\w*|"
    r"сброс\w*|занято|абонент\b.*|не\s+отвеча\w*)\W*$", re.I)


def _p10(x) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())[-10:]


def kommentarii(conn, s: str = NABOR_S) -> dict[int, list[tuple]]:
    """Комментарии карточек с начала набора: uid → [(ts, manager_id, text)], свежие первыми."""
    out: dict[int, list[tuple]] = defaultdict(list)
    try:
        rows = conn.execute("SELECT user_id, ts, manager_id, text FROM crm_comments "
                            "WHERE ts >= ? ORDER BY ts DESC", (s,)).fetchall()
    except Exception:
        return out
    for uid, ts, mid, text in rows:
        out[uid].append((ts or "", mid, text or ""))
    return out


def _otvet(rows: list[tuple]) -> str:
    """Причина не звонить по последнему содержательному комментарию — или ''."""
    from .voronka import MANAGERS, _CALL_LOG, _robot
    for ts, mid, text in sorted(rows, key=lambda r: r[0], reverse=True):
        t = str(text or "").strip()
        t0 = t.lstrip("📞").strip()
        razbor = t.startswith("📞") or bool(_CALL_LOG.match(t0))
        chelovek = mid in MANAGERS and not _robot(t)
        if not (razbor or chelovek):
            continue                      # наши служебные комментарии — не ответ семьи
        if PUSTO.match(t0):
            continue
        if STOP.search(t0):
            kto = "разбор звонка" if razbor else MANAGERS.get(mid, "админ")
            return f"{str(ts)[:10]} {kto}: «{t0[:90]}»"
        return ""
    return ""


def otvet_v_kartochke(conn, uids, kom: dict | None = None) -> str:
    """Семья уже ответила так, что звонить не надо, — текст причины, иначе ''.
    kom — заранее загруженные kommentarii() для очередей на сотни семей."""
    uids = [int(u) for u in (uids or []) if u]
    if not uids:
        return ""
    if kom is not None:
        rows = [r for u in uids for r in kom.get(u, [])]
    else:
        try:
            q = ",".join("?" * len(uids))
            rows = conn.execute(f"SELECT ts, manager_id, text FROM crm_comments "
                                f"WHERE user_id IN ({q}) AND ts >= ?", (*uids, NABOR_S)).fetchall()
            rows = [tuple(r) for r in rows]
        except Exception:
            rows = []
    return _otvet(rows) if rows else ""


def popytki_bez_otveta(conn, p10: str, s: str = NABOR_S) -> tuple[int, bool]:
    """Исходящие без разговора после последнего живого контакта и было ли
    после первого из них наше сообщение. Считаются только итоговые строки
    журнала (missed/short) — события вебхука (appeared, connected…) нет."""
    p = _p10(p10)
    if len(p) < 10:
        return 0, False
    try:
        calls = conn.execute(
            "SELECT ts, direction, state FROM mango_calls WHERE substr(phone,-10)=? AND ts >= ? "
            "AND state IN ('talked','short','missed') ORDER BY ts", (p, s)).fetchall()
    except Exception:
        return 0, False
    zhivoy = ""
    for ts, napr, st in calls:
        if st == "talked":
            zhivoy = max(zhivoy, str(ts))
    try:
        r = conn.execute("SELECT MAX(ts) FROM wazzup_inbox WHERE substr(phone,-10)=?", (p,)).fetchone()
        zhivoy = max(zhivoy, str((r and r[0]) or ""))
    except Exception:
        pass
    minuty = set()
    for ts, napr, st in calls:
        if str(ts) > zhivoy and str(napr or "").startswith(("out", "исх")) and st in ("missed", "short"):
            minuty.add(str(ts)[:16])     # одна попытка — один звонок, повтор строки не в счёт
    if not minuty:
        return 0, False
    pervaya = min(minuty)
    try:
        r = conn.execute("SELECT 1 FROM wazzup_outbox WHERE substr(phone,-10)=? AND ts >= ? LIMIT 1",
                         (p, pervaya)).fetchone()
        pisali = bool(r)
    except Exception:
        pisali = False
    return len(minuty), pisali


def hvatit_zvonit(conn, p10: str) -> str:
    n, pisali = popytki_bez_otveta(conn, p10)
    if n >= 3 and pisali:
        return f"{n} звонка без ответа и наше сообщение — ждём, когда семья ответит"
    return ""


def semya(conn, uids, p10: str) -> list[int]:
    """Все карточки семьи: переданные плюс заведённые на тот же номер (братья, сёстры)."""
    out = [u for u in (uids or []) if u]
    p = _p10(p10)
    if len(p) == 10:
        try:
            out += [r[0] for r in conn.execute("SELECT id FROM users WHERE substr(phone,-10)=?", (p,))]
        except Exception:
            pass
    return sorted(set(out))


def ne_zvonit(conn, uids, p10: str, kom: dict | None = None) -> str:
    """Причина не ставить звонок семье или ''."""
    return otvet_v_kartochke(conn, semya(conn, uids, p10), kom) or hvatit_zvonit(conn, p10)


def nedavno_govorili(conn, p10: str, s_ts: str) -> bool:
    """Был ли разговор с этим номером начиная с s_ts: итог звонка «talked» из
    опроса Манго или событие вебхука «connected», если опрос ещё не дошёл."""
    p = _p10(p10)
    if len(p) < 10:
        return False
    try:
        return bool(conn.execute(
            "SELECT 1 FROM mango_calls WHERE substr(phone,-10)=? AND state IN ('talked','connected') "
            "AND replace(ts,' ','T') >= ? LIMIT 1", (p, s_ts)).fetchone())
    except Exception:
        return False
