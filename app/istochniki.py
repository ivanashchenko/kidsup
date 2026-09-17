"""Откуда пришли заявки и что из них вышло.

Владелец 17.09, разбирая рекламу: реклама за восемь дней съела 36 280 ₽,
кабинеты отчитались про 1 136 конверсий, а новых карточек в CRM за тот же
срок — 71. Пока не видно, какой канал дал эти 71, решать, куда добавлять
деньги и что выключать, можно только наугад.

Считаем по полю createSourceId карточки — это «источник заявки» МойКласса.
Ничего лучше в карточке нет: utm в атрибутах не сохраняются, а Roistat
подмешивает свои метки не всем. Поэтому страница честно показывает и долю
карточек без источника: если она большая, любые выводы по каналам шаткие,
и это надо видеть, а не прятать.

Главное здесь не число заявок, а что из них вышло. Канал, давший тридцать
карточек и ноль записей на пробное, хуже канала с пятью карточками и тремя
оплатами, хотя в отчёте кабинета выглядит в шесть раз лучше.

Только чтение.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from . import db

# Что случилось с карточкой дальше — по статусам клиента
ZAPISALSYA = 125952      # 4. Записался на пробное
POSETIL = 125953         # 5. Посетил пробное
KLIENT = 125955          # Клиент
DUMAET = 146950          # 3. Думает (разговор состоялся)
OTKAZ = 125957           # Отказ
MUSOR = {125954, 215202, 146328}


def _src_names(conn) -> dict[int, str]:
    """Справочник источников. Берём из кэша, чтобы не ходить в CRM на каждый показ."""
    try:
        raw = db.get_setting("create_sources")
        if raw:
            return {int(k): v for k, v in json.loads(raw).items()}
    except Exception:
        pass
    return {}


def obnovit_spravochnik() -> dict:
    """Подтянуть справочник источников из МойКласса и положить в настройки."""
    from . import sync
    from .moyklass_client import MoyklassClient
    mk = MoyklassClient(sync.get_api_key())
    try:
        data = mk.get("/v1/company/createSources")
        items = data if isinstance(data, list) else (data or {}).get("createSources") or []
        names = {str(x.get("id")): str(x.get("name") or "") for x in items}
        db.set_setting("create_sources", json.dumps(names, ensure_ascii=False))
        return {"ok": True, "источников": len(names), "справочник": names}
    finally:
        mk.close()


def otchet(since: str = "", until: str = "") -> dict:
    today = date.today()
    since = since or (today - timedelta(days=7)).isoformat()
    until = until or today.isoformat()

    with db.get_conn() as conn:
        names = _src_names(conn)
        try:
            st_names = {r[0]: r[1] for r in conn.execute(
                "SELECT id, name FROM client_statuses")}
        except Exception:
            st_names = {}

        # Кто из новых карточек дошёл до записи и до оплаты. Записи и платежи
        # смотрим ПОСЛЕ появления карточки: оплата, сделанная раньше, к этой
        # заявке отношения не имеет.
        zapisi = {r[0] for r in conn.execute(
            "SELECT DISTINCT user_id FROM joins WHERE status_id IN (58132, 83760, 58131, 50509)")}
        prishli = {r[0] for r in conn.execute(
            "SELECT DISTINCT lr.user_id FROM lesson_records lr "
            "JOIN lessons l ON l.id = lr.lesson_id WHERE lr.visit = 1 AND l.date >= ?",
            (since,))}
        platili = {r[0] for r in conn.execute(
            "SELECT DISTINCT user_id FROM payments WHERE user_id IS NOT NULL")}

        rows = conn.execute(
            "SELECT id, name, phone, client_state_id, created_at, raw FROM users "
            "WHERE created_at >= ? AND created_at <= ? ORDER BY created_at",
            (since, until + "T23:59:59")).fetchall()

    po_istochnikam: dict[str, dict] = {}
    vsego = {"карточек": 0, "без_источника": 0, "записались": 0,
             "дошли": 0, "оплатили": 0, "мусор": 0}
    primery: dict[str, list] = {}
    for uid, nm, phone, st, created, raw in rows:
        sid = None
        try:
            sid = (json.loads(raw) or {}).get("createSourceId")
        except Exception:
            pass
        klyuch = names.get(int(sid), f"источник {sid}") if sid else "не указан"
        b = po_istochnikam.setdefault(klyuch, {
            "карточек": 0, "записались": 0, "дошли": 0, "оплатили": 0,
            "мусор": 0, "отказ": 0, "новый_лид": 0})
        b["карточек"] += 1
        vsego["карточек"] += 1
        if not sid:
            vsego["без_источника"] += 1
        if uid in zapisi:
            b["записались"] += 1
            vsego["записались"] += 1
        if uid in prishli:
            b["дошли"] += 1
            vsego["дошли"] += 1
        if uid in platili:
            b["оплатили"] += 1
            vsego["оплатили"] += 1
        if st in MUSOR:
            b["мусор"] += 1
            vsego["мусор"] += 1
        elif st == OTKAZ:
            b["отказ"] += 1
        elif st == 125951:
            b["новый_лид"] += 1
        if len(primery.setdefault(klyuch, [])) < 8:
            primery[klyuch].append({
                "uid": uid, "имя": nm or "", "телефон": phone or "",
                "создан": (created or "")[:16].replace("T", " "),
                "статус": st_names.get(st, str(st or "")),
                "записался": uid in zapisi, "дошёл": uid in prishli,
                "оплатил": uid in platili})

    spisok_ = sorted(po_istochnikam.items(), key=lambda kv: -kv[1]["карточек"])
    return {
        "окно": {"с": since, "по": until},
        "обновлено": datetime.now().strftime("%H:%M"),
        "справочник_есть": bool(names),
        "итого": vsego,
        "источники": [dict(источник=k, **v) for k, v in spisok_],
        "примеры": primery,
    }
