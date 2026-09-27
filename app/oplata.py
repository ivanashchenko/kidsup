"""Сбор оплат за следующий месяц у семей, оплативших текущий.

Владелец 27.09: «с утра начать рассылку у кого есть абонементы за сентябрь,
что начинаем сбор оплат за октябрь — аккуратное и красивое сообщение, почему
нам важно скорее собирать оплаты. По стандартной схеме: сообщение в
мессенджер по приоритету + все СМС».

Стандартная схема — та же, что у «Твой Класс» (tvoyklass.py) и точечной
рассылки (aychat.py):

  · мессенджер — wazzup.send_smart(mass=False): MAX и Telegram, где с семьёй
    уже есть переписка, и всегда WhatsApp с номера chat_whatsapp;
  · СМС — всем адресатам: это семьи, оплатившие сентябрь, то есть платившие
    клиенты, и закон о рекламе здесь не мешает (правило владельца 24.08).

Кому: ребёнок учится (статус записи 2) в группе сезона 2627_ и у него есть
оплаченный абонемент этой группы, начатый в сентябре. Кто уже купил
следующий месяц (оплаченный абонемент с началом не раньше
NEXT_FROM или проданный после SOLD_AFTER с остатком занятий), тому не пишем:
просить оплату у заплатившего — худший из возможных дублей. Проверка
повторяется перед каждой отправкой, потому что оплаты идут весь день.

Темп: одна семья за раз и пауза 2–4 минуты. Номер 0918 — живой канал
переписки, залп в сотню сообщений он не переживёт (22.08 так ушёл в бан 0077).

Отправка только из цикла сервера (autopilot) и серверных эндпоинтов;
локально модуль ничего не шлёт.
"""
from __future__ import annotations

import json
import logging
import random
import re
from datetime import datetime, timedelta

from . import db

log = logging.getLogger("kidsup.oplata")

CAMPAIGN = "oplata_oct26"
KIND = "oplata"                      # сервисный вид для wazzup.guard
SEASON_PREFIX = "2627_"
ST_UCHITSYA = 2
CUR_FROM, CUR_TO = "2026-08-20", "2026-10-01"   # «абонемент за сентябрь»: начало в этом окне
NEXT_FROM = "2026-10-01"                         # абонемент следующего месяца
SOLD_AFTER = "2026-09-21"                        # куплен в последнюю неделю — считаем за октябрь
START_AT = "2026-09-28T10:00"                    # решение владельца: «завтра с утра»
DEADLINE = "5 октября"
SKIP_STATES = (146328, 215202, 125954)          # «не писать» и прочие жёсткие
MONTH = "октябрь"
MONTH_P = "октября"
MONTH_V = "октябре"
# Имена, которые в CRM записаны с явной опечаткой: в сообщении без имени,
# чем с чужим («Сладислава» 27.09 — админам поправить карточку)
NAME_SUSPECT = {"Сладислава"}

SUBJ = [  # префикс имени группы (без 2627_) → как называем родителю
    ("ПШ", "подготовка к школе"), ("АЯ", "английский язык"),
    ("РР.Музыка и речь", "«Музыка и речь»"), ("РР.Первая школа", "«Первая школа»"),
    ("РР.Лицей", "«Лицей»"), ("ИЗО", "ИЗО-студия"), ("МА", "ментальная арифметика"),
    ("ШАХ", "шахматы"), ("Робот", "робототехника"), ("Мини-сад", "мини-сад"),
    ("Нулевой", "нулевой класс"), ("НК", "нулевой класс"), ("ЛГ", "занятия с логопедом"),
    ("Скорочт", "скорочтение"), ("Каллиграф", "каллиграфия"),
]


def _p10(x) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())[-10:]


def _subject(cls: str) -> str:
    short = (cls or "").replace(SEASON_PREFIX, "", 1)
    for k, v in SUBJ:
        if short.startswith(k):
            return v
    return short.split("_")[0]


def _child(full: str) -> str:
    from .autopilot import _child_name
    got = _child_name(full or "")
    if got:
        return got
    parts = (full or "").split()
    name = parts[1] if len(parts) > 1 else (parts[0] if parts else "")
    return "" if name in NAME_SUSPECT else name


# ---------------------------------------------------------------- тексты

def text_for(kids: list[tuple[str, list[str]]]) -> str:
    """kids = [(имя, [предметы])]. Имена в именительном — без склонений."""
    spisok = "\n".join(f"• {n} — {', '.join(ss)}" if n else f"• {', '.join(ss)}"
                        for n, ss in kids)
    lg = any("логопед" in x for _, ss in kids for x in ss)
    mesto = "место в группе" + (" и время у логопеда" if lg else "")
    return (
        f"Здравствуйте! Это KidsUP 🌿\n\n"
        f"Спасибо, что сентябрь провели с нами! Открываем оплату занятий на {MONTH}:\n"
        f"{spisok}\n\n"
        f"Просим оплатить до {DEADLINE}. Почему нам важно не откладывать:\n"
        f"• оплата закрепляет за вами {mesto} — сейчас идёт набор, и новые семьи "
        f"записываются на свободные места;\n"
        f"• под точный состав группы педагоги планируют программу месяца и готовят материалы;\n"
        f"• в первые дни {MONTH_P} не будет очереди на ресепшене — занятия начнутся вовремя.\n\n"
        f"Как оплатить:\n"
        f"• в личном кабинете «Твой Класс» — kidsup.tvoyklass.com;\n"
        f"• на ресепшене картой или по СБП;\n"
        f"• или ответьте на это сообщение — пришлём ссылку для оплаты по СБП.\n\n"
        f"Принимаем материнский капитал, для налогового вычета 13% подготовим справку. "
        f"Если в {MONTH_V} что-то меняется — время, пауза, ещё один предмет — "
        f"просто напишите, всё подберём 💛\n\n"
        f"Если вы уже оплатили {MONTH} — спасибо, это сообщение можно пропустить."
    )


def sms_for(kids: list[tuple[str, list[str]]]) -> str:
    names = ", ".join(n for n, _ in kids if n)
    return (f"KidsUP: открыта оплата занятий на {MONTH}{f' ({names})' if names else ''}. "
            f"Просим оплатить до {DEADLINE} — так место в группе закрепится за вами. "
            f"kidsup.tvoyklass.com или ресепшен, тел. 84951209024")


# ---------------------------------------------------------------- кто

def _subs(conn) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for uid, raw, b, e, vt, vu in conn.execute(
            "SELECT user_id, raw, begin_date, end_date, visits_total, visits_used "
            "FROM user_subscriptions WHERE begin_date >= ?", (CUR_FROM,)):
        try:
            j = json.loads(raw or "{}")
        except ValueError:
            continue
        if not (j.get("payed") or 0) > 0:
            continue
        cids = set(j.get("classIds") or [])
        if j.get("mainClassId"):
            cids.add(j["mainClassId"])
        out.setdefault(uid, []).append({
            "begin": (b or "")[:10], "end": (e or "")[:10], "sold": (j.get("sellDate") or "")[:10],
            "total": vt, "used": vu, "cids": cids, "payed": j.get("payed"),
            "price": j.get("price") or j.get("summ")})
    return out


def _covered(subs: list[dict], cid: int) -> bool:
    """Следующий месяц уже оплачен по этой группе."""
    for s in subs:
        if s["cids"] and cid not in s["cids"]:
            continue
        if s["begin"] >= NEXT_FROM:
            return True
        if s["sold"] >= SOLD_AFTER and s["begin"] >= SOLD_AFTER:
            return True
    return False


def _has_current(subs: list[dict], cid: int) -> bool:
    return any((not s["cids"] or cid in s["cids"]) and CUR_FROM <= s["begin"] < CUR_TO
               for s in subs)


def plan() -> dict:
    """Кому и что отправим. Ничего не отправляет."""
    fam: dict[str, dict] = {}
    skipped_paid = []
    # 28.09 Борис: «ходят 259, а рассылка на 160 детей — почему?». Каждого
    # «Учится», кто не попал в рассылку, записываем с причиной.
    mimo: list[dict] = []
    with db.get_conn() as conn:
        subs = _subs(conn)
        rows = conn.execute(
            "SELECT u.id, u.name, u.phone, c.id, c.name, COALESCE(u.client_state_id,0) FROM joins j "
            "JOIN users u ON u.id = j.user_id JOIN classes c ON c.id = j.class_id "
            "WHERE j.status_id=? AND (c.status IS NULL OR c.status='opened') AND c.name LIKE ? "
            "AND c.name NOT LIKE ?",
            (ST_UCHITSYA, SEASON_PREFIX + "%", "%Заявк%")).fetchall()
    for uid, name, phone, cid, cls, st in rows:
        s = subs.get(uid, [])
        why = ""
        if st in SKIP_STATES:
            why = "статус «не писать»"
        elif not any((not x["cids"] or cid in x["cids"]) for x in s):
            why = "нет оплаченного абонемента на группу"
        elif _covered(s, cid):
            skipped_paid.append({"uid": uid, "child": name, "group": cls})
            continue
        elif not _has_current(s, cid):
            why = "абонемент не сентябрьский (" + ", ".join(
                sorted({x["begin"] for x in s if not x["cids"] or cid in x["cids"]})) + ")"
        p = _p10(phone)
        if not why and (len(p) != 10 or not p.startswith("9")):
            why = f"нет мобильного номера ({phone or 'пусто'})"
        if why:
            mimo.append({"uid": uid, "child": name, "group": cls.replace(SEASON_PREFIX, ""), "почему": why})
            continue
        f = fam.setdefault(p, {"phone": "7" + p, "uids": [], "kids": {}, "groups": []})
        if uid not in f["uids"]:
            f["uids"].append(uid)
        k = f["kids"].setdefault(_child(name), [])
        subj = _subject(cls)
        if subj not in k:
            k.append(subj)
        f["groups"].append(cls.replace(SEASON_PREFIX, ""))
    out = []
    for f in fam.values():
        kids = list(f["kids"].items())
        f["text"] = text_for(kids)
        f["sms"] = sms_for(kids)
        f["kids"] = kids
        out.append(f)
    out.sort(key=lambda f: f["kids"][0][0])
    return {"семей": len(out), "детей": sum(len(f["uids"]) for f in out),
            "уже_оплатили_следующий": len(skipped_paid), "оплатившие": skipped_paid,
            "не_попали": mimo, "recipients": out}


def diag() -> dict:
    """Как выглядят абонементы сезона: окна начала/конца, остатки. Только чтение."""
    with db.get_conn() as conn:
        subs = _subs(conn)
    by_begin: dict[str, int] = {}
    by_end: dict[str, int] = {}
    by_sold: dict[str, int] = {}
    for ss in subs.values():
        for s in ss:
            by_begin[s["begin"][:7] + ("-a" if s["begin"][8:] < "21" else "-b")] = \
                by_begin.get(s["begin"][:7] + ("-a" if s["begin"][8:] < "21" else "-b"), 0) + 1
            by_end[s["end"][:7]] = by_end.get(s["end"][:7], 0) + 1
            by_sold[s["sold"]] = by_sold.get(s["sold"], 0) + 1
    sample = [dict(s, cids=sorted(s["cids"])) for ss in list(subs.values())[:8] for s in ss]
    return {"детей_с_абонементом": len(subs), "начало": dict(sorted(by_begin.items())),
            "конец": dict(sorted(by_end.items())),
            "продажи_после_20_09": {k: v for k, v in sorted(by_sold.items()) if k >= "2026-09-20"},
            "пример": sample}


# ---------------------------------------------------------------- очередь
# Своя таблица, а не broadcast_queue: общую очередь забирает
# autopilot._broadcast_tick и отправил бы эти строки массовым путём (WABA).

def _q(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS oplata_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT, campaign TEXT, phone TEXT, uids TEXT,
        text TEXT, sms TEXT, status TEXT DEFAULT 'pending', created TEXT, sent TEXT,
        tried TEXT DEFAULT '')""")

def enqueue(dry: bool = True) -> dict:
    p = plan()
    from .autopilot import _now
    now = _now().isoformat(timespec="seconds")
    n = 0
    with db.get_conn() as conn:
        _q(conn)
        done = {r[0] for r in conn.execute(
            "SELECT phone FROM oplata_queue WHERE campaign=?", (CAMPAIGN,))}
        for f in p["recipients"]:
            if f["phone"] in done:
                continue
            n += 1
            if not dry:
                conn.execute("INSERT INTO oplata_queue (campaign, phone, uids, text, sms, created) "
                             "VALUES (?, ?, ?, ?, ?, ?)",
                             (CAMPAIGN, f["phone"], json.dumps(f["uids"]), f["text"], f["sms"], now))
    return {"ok": True, "dry_run": dry, "queued": n,
            **{k: v for k, v in p.items() if k not in ("recipients", "оплатившие", "не_попали")}}


def _still_due(uids: list[int]) -> bool:
    """Перед отправкой: не оплатил ли кто-то из детей следующий месяц за это время."""
    with db.get_conn() as conn:
        subs = _subs(conn)
        rows = conn.execute(
            "SELECT j.user_id, j.class_id FROM joins j JOIN classes c ON c.id=j.class_id "
            "WHERE j.status_id=? AND c.name LIKE ? AND c.name NOT LIKE ? "
            "AND j.user_id IN (%s)" % ",".join("?" * len(uids)),
            (ST_UCHITSYA, SEASON_PREFIX + "%", "%Заявк%", *uids)).fetchall()
    return any(_has_current(subs.get(u, []), c) and not _covered(subs.get(u, []), c)
               for u, c in rows)


def deliver_one(dry: bool = True) -> dict:
    """Одна семья: мессенджеры + WhatsApp, затем СМС."""
    from . import mango, wazzup
    from .autopilot import _now
    now = _now()
    if not (10 <= now.hour < 19 or (now.hour == 19 and now.minute < 30)):
        return {"ok": False, "error": "вне окна 10:00–19:30"}
    with db.get_conn() as conn:
        _q(conn)
        # два отказа за день — в сторону, чтобы не занимать очередь
        conn.execute(
            "UPDATE oplata_queue SET status='hold' WHERE campaign=? AND status='pending' "
            "AND (LENGTH(COALESCE(tried,'')) - LENGTH(REPLACE(COALESCE(tried,''),'fail',''))) >= 8",
            (CAMPAIGN,))
        row = conn.execute("SELECT id, phone, uids, text, sms FROM oplata_queue "
                           "WHERE campaign=? AND status='pending' ORDER BY id LIMIT 1",
                           (CAMPAIGN,)).fetchone()
    if not row:
        return {"ok": True, "пусто": True}
    rid, phone, uids_s, text, sms = row
    try:
        uids = [int(x) for x in json.loads(uids_s or "[]")]
    except ValueError:
        uids = []
    if uids and not _still_due(uids):
        with db.get_conn() as conn:
            conn.execute("UPDATE oplata_queue SET status='cancelled', "
                         "tried=COALESCE(tried,'')||'оплатили_до_отправки;' WHERE id=?", (rid,))
        return {"ok": True, "phone": phone, "снят": "уже оплатили"}
    if dry:
        return {"ok": True, "dry_run": True, "phone": phone,
                "каналы": wazzup.channels_for(phone, uids[0] if uids else None), "text": text, "sms": sms}
    try:
        log_ = wazzup.send_smart(phone, text, uid=uids[0] if uids else None,
                                 dry_run=False, mass=False, kind=KIND)
        ok = any(x.endswith(": ok") for x in log_)
    except Exception as e:  # noqa: BLE001
        ok, log_ = False, [str(e)[:150]]
    sms_st = "нет"
    if ok and sms and db.get_setting("sms_on", "0") == "1":
        try:
            sms_st = "ok" if mango.send_sms(phone, sms) else "fail"
        except Exception as e:  # noqa: BLE001
            sms_st = f"err {str(e)[:60]}"
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE oplata_queue SET status=?, sent=?, tried=COALESCE(tried,'')||? WHERE id=?",
            ("sent" if ok else "pending", now.isoformat(timespec="seconds") if ok else None,
             ("msg=ok" if ok else "msg=fail") + f",sms={sms_st};", rid))
    if ok:
        _comment(uids, sms_st)
    log.info("oplata: %s → %s, смс %s", phone[-4:], "ok" if ok else "fail", sms_st)
    return {"ok": ok, "phone": phone, "log": log_, "sms": sms_st}


def _comment(uids: list[int], sms_st: str) -> None:
    """След в карточке: админ видит, что напоминание об оплате ушло."""
    try:
        from .autopilot import _client, _now
        mk = _client()
        try:
            for u in uids[:3]:
                mk.post("/v1/company/userComments", {
                    "userId": u, "showToUser": False,
                    "comment": f"{_now():%d.%m %H:%M} Клод: ушло сообщение «открыта оплата на "
                               f"{MONTH}, до {DEADLINE}» (мессенджер/WhatsApp"
                               f"{', СМС' if sms_st == 'ok' else ''})."})
        finally:
            mk.close()
    except Exception:
        log.exception("oplata: комментарий в карточку не записан")


def tick() -> dict | None:
    """Из минутного цикла autopilot: одна семья раз в 2–4 минуты."""
    if db.get_setting("oplata_on", "0") != "1":
        return None
    from .autopilot import _now
    now = _now()
    start = db.get_setting("oplata_start", START_AT) or START_AT
    if now.isoformat(timespec="minutes") < start:
        return None
    nxt = db.get_setting("oplata_next", "") or ""
    if nxt and now.isoformat(timespec="seconds") < nxt:
        return None
    r = deliver_one(dry=False)
    if r.get("error") or r.get("пусто"):
        return None
    db.set_setting("oplata_next", (now + timedelta(seconds=random.randint(120, 240)))
                   .isoformat(timespec="seconds"))
    return r


def status() -> dict:
    with db.get_conn() as conn:
        _q(conn)
        rows = conn.execute("SELECT status, COUNT(*) FROM oplata_queue WHERE campaign=? "
                            "GROUP BY status", (CAMPAIGN,)).fetchall()
        sms_ok = conn.execute("SELECT COUNT(*) FROM oplata_queue WHERE campaign=? "
                              "AND tried LIKE '%sms=ok%'", (CAMPAIGN,)).fetchone()[0]
        last = conn.execute("SELECT phone, sent, tried FROM oplata_queue WHERE campaign=? "
                            "AND sent IS NOT NULL ORDER BY sent DESC LIMIT 5", (CAMPAIGN,)).fetchall()
    return {"campaign": CAMPAIGN, "по_статусу": dict(rows), "смс_ушло": sms_ok,
            "включено": db.get_setting("oplata_on", "0") == "1",
            "старт": db.get_setting("oplata_start", START_AT) or START_AT,
            "sms_on": db.get_setting("sms_on", "0"),
            "последние": [list(r) for r in last]}
