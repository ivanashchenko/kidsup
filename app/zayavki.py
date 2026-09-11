"""Необработанные заявки сезона — живой раздел на страницах плана дня.

06.09 Борис: «почему в базе 374 заявки в статусе "Новая заявка"? кто они?
когда их дожмут? они есть в планах?» Разбор показал: 248 — хвосты прошлых
сезонов, 126 — заявки с 10.08, из которых по-настоящему не тронуты 12, а
у остальных разговор был, но статус записи никто не сменил.

Здесь считаем это каждый раз заново по локальным таблицам сервера
(joins/users/classes обновляются лёгким синком раз в 5 минут; звонки —
mango_calls из вебхука; переписка — wazzup_inbox/outbox) и отдаём HTML-блок
для plan_*.html. Никаких отправок, только чтение.

Правила отбора: записи в статусе «1. Новая заявка» (50509) с 10.08.2026,
без промоутера (статус карточки 347075) и без летнего лагеря/клуба.
«Не тронута» — ни звонка, ни сообщения в обе стороны, ни другой записи
семьи в статусе «подтвердил/записался/посетил/учится/отработка».
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta

from . import db

SEASON_FROM = "2026-08-10"
NEW_JOIN = 50509
WORKING_JOIN = (83760, 58132, 58131, 2, 5)
PROMOTER_STATE = 347075
ROBOT_PHONES = {"9099301750", "9044500230"}   # автодозвонщики, не семьи
JUNK_RE = re.compile(r"дубл|7777777777|тест|собеседован", re.I)
CAMP_RE = re.compile(r"лагер|летн|_лк\b|клуб", re.I)


_CALLS: dict = {"ts": None, "idx": {}}


def _calls_index() -> dict[str, list[tuple[str, bool]]]:
    """Звонки Манго за сезон: phone10 -> [(ts_msk, разговор состоялся)]. Кэш 30 минут.

    Таблица mango_calls на сервере пишется вебхуком и по факту почти пустая
    (06.09: у 41 «нетронутой» заявки 0 звонков, хотя по журналу Манго админы
    набирали большинство). Поэтому берём журнал через API статистики, как
    /docs/rabota/zvonki_chas.py, но только читаем."""
    now = datetime.now()
    if _CALLS["ts"] and (now - _CALLS["ts"]) < timedelta(minutes=30):
        return _CALLS["idx"]
    idx: dict[str, list[tuple[str, bool]]] = {}
    try:
        from . import mango
        rows = mango.calls(datetime.fromisoformat(SEASON_FROM), now + timedelta(hours=1))
        for r in rows:
            ts = datetime.fromtimestamp(int(r.get("start") or 0)).isoformat(timespec="seconds")
            ok = bool(r.get("answer"))
            for num in (r.get("from_num") or "", r.get("to_num") or ""):
                if len(num) >= 10 and not num.startswith("7495") and num[-10:] != "9165610077":
                    idx.setdefault(num[-10:], []).append((ts, ok))
        _CALLS.update(ts=now, idx=idx)
    except Exception:
        # без журнала Манго остаёмся на вебхучной таблице, кэш не трогаем
        return _CALLS["idx"]
    return idx


def _table(conn, name: str) -> bool:
    return bool(conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name=?", (name,)).fetchone()[0])


def _msk(ts_utc: str) -> str:
    """createdAt МойКласса приходит в UTC — приводим к МСК для сравнения с журналами."""
    try:
        d = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
        return (d + timedelta(hours=3)).replace(tzinfo=None).isoformat(timespec="seconds")
    except Exception:
        return ts_utc[:19]


def collect() -> dict:
    with db.get_conn() as conn:
        has_calls = _table(conn, "mango_calls")
        has_in, has_out = _table(conn, "wazzup_inbox"), _table(conn, "wazzup_outbox")
        joins = conn.execute(
            "SELECT id, user_id, class_id, created_at, raw FROM joins "
            "WHERE status_id=? AND created_at >= ? ORDER BY created_at",
            (NEW_JOIN, SEASON_FROM)).fetchall()
        old_total = conn.execute(
            "SELECT COUNT(*) FROM joins WHERE status_id=? AND created_at < ?",
            (NEW_JOIN, SEASON_FROM)).fetchone()[0]
        classes = {r["id"]: r["name"] or "" for r in conn.execute("SELECT id, name FROM classes")}
        cidx = _calls_index()
        out = {"untouched": [], "tried": [], "talked": [], "tail": [], "excluded": 0,
               "old_total": old_total, "season_total": len(joins)}
        for j in joins:
            u = conn.execute("SELECT id, name, phone, client_state_id FROM users WHERE id=?",
                             (j["user_id"],)).fetchone()
            cname = classes.get(j["class_id"], "")
            if not u or CAMP_RE.search(cname) or (u["client_state_id"] == PROMOTER_STATE) \
                    or JUNK_RE.search(u["name"] or "") or (u["phone"] or "").endswith("7777777777") \
                    or (u["phone"] or "")[-10:] in ROBOT_PHONES:
                out["excluded"] += 1
                continue
            try:
                raw = json.loads(j["raw"] or "{}")
            except ValueError:
                raw = {}
            comment = (raw.get("comment") or "").replace("Заявка с сайта: ", "")
            p10 = (u["phone"] or "")[-10:]
            since = _msk(j["created_at"])
            other = conn.execute(
                "SELECT COUNT(*) FROM joins WHERE user_id=? AND id<>? AND status_id IN (%s)"
                % ",".join("?" * len(WORKING_JOIN)),
                (u["id"], j["id"], *WORKING_JOIN)).fetchone()[0]
            n_out = n_in = n_calls = n_talk = 0
            if p10:
                if has_out:
                    n_out = conn.execute("SELECT COUNT(*) FROM wazzup_outbox WHERE substr(phone,-10)=? AND ts>=?",
                                         (p10, since)).fetchone()[0]
                if has_in:
                    n_in = conn.execute("SELECT COUNT(*) FROM wazzup_inbox WHERE substr(phone,-10)=? AND ts>=?",
                                        (p10, since)).fetchone()[0]
                # звонок за час до заявки тоже считаем: заявка «Звонок от …» создаётся после звонка
                since_call = (datetime.fromisoformat(since) - timedelta(hours=1)).isoformat(timespec="seconds")
                hits = [ok for ts, ok in cidx.get(p10, []) if ts >= since_call]
                n_calls, n_talk = len(hits), sum(1 for ok in hits if ok)
                if not hits and has_calls:
                    n_calls = conn.execute("SELECT COUNT(*) FROM mango_calls WHERE substr(phone,-10)=? AND ts>=?",
                                           (p10, since)).fetchone()[0]
                    n_talk = conn.execute("SELECT COUNT(*) FROM mango_calls WHERE substr(phone,-10)=? "
                                          "AND ts>=? AND state='connected'", (p10, since)).fetchone()[0]
            row = {"join": j["id"], "uid": u["id"], "name": u["name"] or "", "phone": p10,
                   "class": re.sub(r"^2627_", "", cname), "comment": comment,
                   "created": since[:10], "calls": n_calls, "out": n_out, "in": n_in,
                   "days": (datetime.now().date() - datetime.fromisoformat(since).date()).days}
            if other:
                out["tail"].append(row)
            elif n_talk or (n_out and n_in) or n_in:
                out["talked"].append(row)
            elif n_calls or n_out:
                out["tried"].append(row)
            else:
                out["untouched"].append(row)
        return out


def _li(r: dict, extra: str = "") -> str:
    ask = r["comment"] or r["class"]
    return (f"<li style='margin:6px 0'><a href='https://app.moyklass.com/client/{r['uid']}' target='_blank' "
            f"style='font-weight:700;color:#312783'>{html.escape(r['name'] or r['phone'])}</a> "
            f"<span style='white-space:nowrap;color:#6c6a86'>{html.escape(r['phone'])}</span> · "
            f"{html.escape(ask[:60])} · <span style='color:#6c6a86'>{r['created'][8:]}.{r['created'][5:7]}, "
            f"{r['days']} дн.</span>{extra}</li>")


def _semey(n: int) -> str:
    """«54 семьи», а не «54 семей» — цифры админы читают каждый день."""
    tail = n % 100
    if not 11 <= tail <= 14:
        if n % 10 == 1:
            return f"{n} семья"
        if n % 10 in (2, 3, 4):
            return f"{n} семьи"
    return f"{n} семей"


def _checked_block() -> str:
    """Блок по перепроверенному списку (app/zayavki_audit.py).

    11.09 Борис: «перепроверь, что эти 68 заявок реально необработанные, может
    это дубли». Оказалось: из 68 строк работы — на 14 семей. Остальное — семьи,
    с которыми уже говорили и не сменили статус записи, заявки одной семьи на
    пять кружков сразу, соискатели и рабочие номера компаний. Поэтому сначала
    показываем проверенный список, а сырой — только пока проверка считается.
    """
    from . import zayavki_audit
    d = zayavki_audit.cached()
    if not d:
        return ""
    fams = d["семьи"]

    def _sec(kind: str) -> list[dict]:
        return [f for f in fams if f["kind"] == kind]

    def _li_f(f: dict, tail: str = "") -> str:
        want = " + ".join(dict.fromkeys(x for x in f["хочет"] if x))[:70]
        dup = f" · {f['cards']} карточки на номере" if f["cards"] > 1 else ""
        return (f"<li style='margin:6px 0'><a href='https://app.moyklass.com/client/{f['uid']}' "
                f"target='_blank' style='font-weight:700;color:#312783'>"
                f"{html.escape(f['name'] or f['phone'])}</a> "
                f"<span style='white-space:nowrap;color:#6c6a86'>{html.escape(f['phone'])}</span> · "
                f"{html.escape(want)} · <span style='color:#6c6a86'>{f['days']} дн.{dup}</span>{tail}</li>")

    todo = _sec("не доделали")
    p = [f"<div class='card' style='border-left:4px solid #E30613;margin:14px 0'>"
         f"<b style='display:block;font-size:17px;margin-bottom:4px'>Заявки, где мы не доделали "
         f"({len(todo)})</b>"
         f"<div style='font-size:12.5px;color:#6c6a86;margin-bottom:8px'>Проверено по всем карточкам "
         f"на номере: записи, звонки, переписка и живые комментарии. Заявки одной семьи сведены в "
         f"одну строку. Из {d['проверено']} строк блока это {_semey(len(fams))}, и работы — на "
         f"{len(todo)}.</div>"]
    if todo:
        p.append("<div style='font-weight:700;color:#E30613;font-size:13px'>Ни звонка, ни сообщения, "
                 "ни комментария. Первый набор дня</div>"
                 "<ul style='list-style:none;padding:0;margin:0;font-size:14px'>"
                 + "".join(_li_f(f) for f in todo) + "</ul>")
    else:
        p.append("<div style='color:#7DB928;font-weight:700'>Все заявки сезона тронуты. Так держать.</div>")
    for kind, title in (("писали", "Писали в мессенджер, ответа нет — позвонить"),
                        ("звонили", "Звонили, разговора не вышло — набрать ещё раз"),
                        ("говорили", "Разговор был, а заявка висит «новой» — поставить статус записи"),
                        ("работает", "Семья уже занимается — закрыть заявку-хвост"),
                        ("не клиент", "Не семьи: соискатели, рабочие номера компаний — закрыть отказом")):
        s = _sec(kind)
        if s:
            p.append(f"<details style='margin-top:8px;font-size:13.5px'><summary style='cursor:pointer;"
                     f"font-weight:700'>{title} — {len(s)}</summary>"
                     "<ul style='list-style:none;padding:0;margin:6px 0 0'>"
                     + "".join(_li_f(f, f" <span style='color:#6c6a86;font-size:12px'>"
                                        f"{html.escape(f['why'][:90])}</span>") for f in s) + "</ul></details>")
    p.append(f"<div style='font-size:12px;color:#6c6a86;margin-top:8px'>Проверка обновляется раз в час.</div></div>")
    return "".join(p)


def block() -> str:
    try:
        checked = _checked_block()
        if checked:
            return checked
    except Exception:  # проверка не важнее самого блока
        pass
    try:
        d = collect()
    except Exception as e:  # страница плана важнее блока
        return (f"<div class='card' style='border-left:4px solid #E30613;margin:14px 0'>"
                f"<b>Заявки сезона без обработки</b> — не посчитались: {html.escape(str(e))}</div>")
    n_hot = len(d["untouched"]) + len(d["tried"])
    parts = [f"<div class='card' style='border-left:4px solid #E30613;margin:14px 0'>"
             f"<b style='display:block;font-size:17px;margin-bottom:4px'>Заявки с 10.08 без обработки ({n_hot})</b>"
             f"<div style='font-size:12.5px;color:#6c6a86;margin-bottom:8px'>Сайт, мессенджеры, телефон; без промоутера и лагеря. "
             f"Считается по CRM, звонкам и переписке при каждом открытии. Обработал — ставь статус записи "
             f"(«Подтвердил», «Записался», «Отказался»), тогда строка исчезнет сама.</div>"]
    if d["untouched"]:
        parts.append("<div style='font-weight:700;color:#E30613;font-size:13px;margin-top:6px'>Не тронуты — ни звонка, ни сообщения. Первый набор дня</div>"
                     "<ul style='list-style:none;padding:0;margin:0;font-size:14px'>"
                     + "".join(_li(r) for r in d["untouched"]) + "</ul>")
    if d["tried"]:
        parts.append("<div style='font-weight:700;color:#F59C00;font-size:13px;margin-top:8px'>Пытались, не дошли — недозвон или сообщение без ответа</div>"
                     "<ul style='list-style:none;padding:0;margin:0;font-size:14px'>"
                     + "".join(_li(r, f" <span style='color:#6c6a86;font-size:12px'>зв. {r['calls']}, сообщ. {r['out']}</span>")
                               for r in d["tried"]) + "</ul>")
    if not n_hot:
        parts.append("<div style='color:#7DB928;font-weight:700'>Все заявки сезона тронуты. Так держать.</div>")
    talked = d["talked"]
    if talked:
        parts.append(f"<details style='margin-top:10px;font-size:13.5px'><summary style='cursor:pointer;font-weight:700'>"
                     f"Разговор был, а заявка так и висит «новой» — {len(talked)}. Поставить статус записи по итогу разговора</summary>"
                     "<ul style='list-style:none;padding:0;margin:6px 0 0'>"
                     + "".join(_li(r) for r in talked) + "</ul></details>")
    if d["tail"]:
        parts.append(f"<details style='margin-top:6px;font-size:13.5px'><summary style='cursor:pointer;font-weight:700'>"
                     f"Семья уже записана в другую группу, заявка-хвост — {len(d['tail'])}. Закрыть «Завершил / записан в другую группу»</summary>"
                     "<ul style='list-style:none;padding:0;margin:6px 0 0'>"
                     + "".join(_li(r) for r in d["tail"]) + "</ul></details>")
    parts.append(f"<div style='font-size:12px;color:#6c6a86;margin-top:8px'>Всего «новых заявок» в CRM: {d['season_total'] + d['old_total']}, "
                 f"из них {d['old_total']} — хвосты до 10.08.2026 (прошлые сезоны), закрываются массово по решению Бориса.</div></div>")
    return "".join(parts)
