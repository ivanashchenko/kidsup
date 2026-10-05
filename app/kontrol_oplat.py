# -*- coding: utf-8 -*-
"""Утренний контроль оплат за вчера (решение владельца 05.10.2026).

Правила, которые проверяем (приняты Борисом 05.10):
1. Все наличные — через КОМТЕТ, чек родителю обязателен.
2. Один тип платежа на один способ оплаты (нал / карта / СБП / онлайн / перевод на счёт / возврат).
3. Скидки только из списка (−15 % новым в день пробного, −10 % второй предмет/ребёнок/многодетные),
   иное — только с пометкой «по согласованию с Б.И.» в комментарии абонемента.
4. Абонемент — до первого занятия месяца; «в долг» не больше 2 занятий.

Источники: локальная база (синк МойКласса), T-API (основной счёт и счёт Буракова),
КОМТЕТ (чеки через кабинет). Тетрадь кассы машина не читает — по наличным дням
кладём Лизе напоминание прислать фото страницы.

Запуск: autopilot в 08:30 за вчера; руками — POST /api/kontrol-oplat/run?day=&dry=1 (владелец).
Результат: data/kontrol_oplat/<день>.json, страница /kontrol-oplat, пункты в инбокс
(CRM-правки — Лизе, деньги с родителей — Лене, итог — Борису).
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import db

log = logging.getLogger("kidsup.kontrol_oplat")

T_NAL, T_ONLINE, T_TERM, T_PEREVOD, T_KORR, T_VOZVRAT = 1, 2, 64725, 63934, 72508, 97845
TYPES = {T_NAL: "нал", T_ONLINE: "онлайн", T_TERM: "терминал", T_PEREVOD: "перевод на счёт",
         T_KORR: "коррекция", T_VOZVRAT: "возврат"}
ACC_MAIN, ACC_BUR = "40802810200001196965", "40802810600000603211"
# полная цена за занятие по прайсу 2026/27: курс → (при 2 р/нед, при 1 р/нед, разовое)
PRAYS = {"АЯ": (1050, 1250, 1600), "ПШ": (1050, 1250, 1600), "ШАХ": (1050, 1250, 1600),
         "ИЗО": (875, 1225, 1600), "РР.Музыка": (1075, 1300, 1600), "РР.Первая": (975, 1250, 1600),
         "РР.Лицей": (975, 1250, 1600), "МА": (2150, 2150, 2150), "Робототехника": (1300, 1600, 2300),
         "ЛГ": (None, None, 2500)}
SOGLASOVANO = re.compile(r"Б\.?\s?И\.|Борис|согласован", re.I)
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "kontrol_oplat"


def _j(s):
    try:
        return json.loads(s) if isinstance(s, str) else (s or {})
    except ValueError:
        return {}


def _fam(cls: str) -> str:
    """Семейство курса из имени группы «2627_РР.Музыка и речь_…» → «РР.Музыка»."""
    parts = (cls or "").split("_")
    k = parts[1] if len(parts) > 1 else (cls or "")
    return k.split(" ")[0]


def _load(day: str) -> dict:
    """Всё нужное из локальной базы одним заходом."""
    d0 = date.fromisoformat(day)
    with db.get_conn() as conn:
        pays = [dict(r) | _j(r["raw"]) for r in conn.execute(
            "SELECT * FROM payments WHERE date BETWEEN ? AND ?",
            ((d0 - timedelta(days=10)).isoformat(), (d0 + timedelta(days=1)).isoformat()))]
        subs = [dict(r) | _j(r["raw"]) for r in conn.execute("SELECT * FROM user_subscriptions")]
        users = {r["id"]: dict(r) | _j(r["raw"]) for r in conn.execute("SELECT id, name, phone, raw FROM users")}
        classes = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM classes")}
        managers = {r["id"]: (r["name"] or "").split()[0] for r in conn.execute("SELECT id, name FROM managers")}
        staff = {(r["name"] or "").split()[0].lower() for r in conn.execute("SELECT name FROM managers") if r["name"]}
        lessons = [dict(r) | _j(r["raw"]) for r in conn.execute("SELECT * FROM lessons WHERE date=?", (day,))]
        lids = [l["id"] for l in lessons]
        recs = defaultdict(list)
        if lids:
            for r in conn.execute("SELECT * FROM lesson_records WHERE lesson_id IN (%s)" % ",".join("?" * len(lids)), lids):
                recs[r["lesson_id"]].append(dict(r) | _j(r["raw"]))
        month_visits = Counter()
        for r in conn.execute(
                "SELECT lr.user_id, COUNT(*) n FROM lesson_records lr JOIN lessons l ON l.id=lr.lesson_id "
                "WHERE lr.visit=1 AND l.date BETWEEN ? AND ? GROUP BY lr.user_id", (day[:8] + "01", day)):
            month_visits[r["user_id"]] = r["n"]
    return dict(pays=pays, subs=subs, users=users, classes=classes, managers=managers, staff=staff,
                lessons=lessons, recs=recs, month_visits=month_visits)


def _covered(subs_by: dict, uid: int, cls: str, d: str, classes: dict):
    f = _fam(cls)
    best = None
    for s in subs_by.get(uid, []):
        if not ((s.get("beginDate") or "9") <= d <= (s.get("endDate") or "0")):
            continue
        if any(_fam(classes.get(c, "")) == f for c in (s.get("classIds") or [])):
            if (s.get("payed") or 0) > 0:
                return "paid"
            best = "unpaid"
    return best


def run(day: str | None = None, dry: bool = False) -> dict:
    day = day or (date.today() - timedelta(days=1)).isoformat()
    d0 = date.fromisoformat(day)
    D = _load(day)
    pays, subs, users, classes, M = D["pays"], D["subs"], D["users"], D["classes"], D["managers"]
    name = lambda uid: (users.get(uid) or {}).get("name") or f"#{uid}"
    phone = lambda uid: (users.get(uid) or {}).get("phone") or ""
    man = lambda p: M.get(p.get("managerId"), "—")
    rub = lambda x: f"{x:,.0f} ₽".replace(",", " ")
    subs_by = defaultdict(list)
    for s in subs:
        subs_by[s["userId"]].append(s)
    first_paid = {}
    for s in subs:
        if (s.get("payed") or 0) > 0 and (s.get("sellDate") or ""):
            first_paid[s["userId"]] = min(first_paid.get(s["userId"], "9999"), s["sellDate"])
    inc = [p for p in pays if p["date"] == day and p.get("optype") == "income" and (p.get("summa") or 0) > 1]
    R: dict = {"day": day, "sostavleno": datetime.now().strftime("%d.%m.%Y %H:%M"), "bloki": {}, "inbox": []}
    def flag(block, text, who=None, ph="", level="warn"):
        R["bloki"].setdefault(block, []).append({"text": text, "who": who, "level": level})
        if who:
            R["inbox"].append((who, text, ph))

    # ── A. деньги ↔ банк ────────────────────────────────────────────────────
    bank_ok = True
    try:
        from . import tbank
        ops = tbank.statement(ACC_MAIN, (d0 - timedelta(days=1)).isoformat(), (d0 + timedelta(days=1)).isoformat())
        card = 0.0
        sbp = []
        for o in ops:
            pp = o.get("payPurpose") or ""
            m = re.search(r"терминалам эквайринга от (\d\d)\.(\d\d)\.(\d{4})", pp)
            if o["typeOfOperation"] == "Credit" and m and f"{m[3]}-{m[2]}-{m[1]}" == (d0 + timedelta(days=1)).isoformat():
                card += o["operationAmount"]
            elif o["typeOfOperation"] == "Credit" and "СБП" in pp:
                t = datetime.fromisoformat((o.get("authorizationDate") or o["operationDate"]).replace("Z", "+00:00")) + timedelta(hours=3)
                if t.date().isoformat() == day:
                    sbp.append(o["operationAmount"])
        term = [p for p in inc if p.get("paymentTypeId") == T_TERM]
        online = [p for p in inc if p.get("paymentTypeId") == T_ONLINE]
        # СБП поштучно: сначала к онлайн-оплатам, потом к терминальным (админ ошибся типом)
        pool_on = [p["summa"] for p in online]
        sbp_as_term = []
        free_sbp = []
        for a in sbp:
            if a in pool_on:
                pool_on.remove(a)
                continue
            hit = next((p for p in term if abs(p["summa"] - a) < 0.5 and not p.get("_sbp")), None)
            if hit:
                hit["_sbp"] = 1
                sbp_as_term.append(hit)
            else:
                free_sbp.append(a)
        mk_card = sum(p["summa"] for p in term if not p.get("_sbp"))
        diff = round(mk_card - card, 2)
        R["bank"] = {"mk_card": mk_card, "bank_card": card, "diff": diff, "sbp_ops": len(sbp), "free_sbp": free_sbp,
                     "sbp_as_term": [(man(p), name(p["userId"]), p["summa"]) for p in sbp_as_term]}
        if diff > 100:
            flag("A. Банк", f"Терминал {day[8:]}.{day[5:7]}: в МойКлассе картой записано на {rub(diff)} БОЛЬШЕ, чем пришло в банк "
                 f"(МК {rub(mk_card)}, банк {rub(card)}). Проверить оплаты «карта» за день: какая не прошла по терминалу.", "Лиза", level="bad")
        elif diff < -100:
            flag("A. Банк", f"Терминал {day[8:]}.{day[5:7]}: в банк пришло на {rub(-diff)} БОЛЬШЕ, чем записано в МойКлассе "
                 f"(банк {rub(card)}, МК {rub(mk_card)}) — чья-то оплата картой не внесена (или внесена как «нал»). Найти по чекам терминала.", "Лиза")
        for a in free_sbp:
            flag("A. Банк", f"СБП {rub(a)} {day[8:]}.{day[5:7]} есть в банке, оплаты на эту сумму в МойКлассе нет — внести.", "Лиза")
        for p in sbp_as_term:
            flag("A. Банк", f"{name(p['userId'])}: {rub(p['summa'])} записано «карта», а по банку это СБП ({man(p)}) — поправить тип платежа (правило 2).", "Лиза", phone(p["userId"]))
    except Exception as e:  # noqa: BLE001
        bank_ok = False
        R["bank"] = {"error": str(e)[:200]}
        flag("A. Банк", f"Банк недоступен: {str(e)[:120]}", level="warn")

    # ── A3. наличные ↔ чеки КОМТЕТ ──────────────────────────────────────────
    nal = [p for p in inc if p.get("paymentTypeId") == T_NAL]
    try:
        from . import komtet
        kt = komtet.cheki((d0 - timedelta(days=1)).isoformat(), (d0 + timedelta(days=1)).isoformat())
        ext = {str(x.get("external_id")): x for x in (kt.get("items") or []) if x.get("state") == "done"}
        R["komtet"] = {"cheki": len(ext), "nal": len(nal)}
        for p in nal:
            if str(p["id"]) not in ext:
                flag("A. Касса", f"{name(p['userId'])}: наличные {rub(p['summa'])} ({man(p)}) — чека КОМТЕТ нет (правило 1). Пробить чек или объяснить.", "Лиза", phone(p["userId"]), "bad")
    except Exception as e:  # noqa: BLE001
        R["komtet"] = {"error": str(e)[:200]}
        if nal:
            flag("A. Касса", f"КОМТЕТ недоступен ({str(e)[:80]}), наличных за день {len(nal)} на {rub(sum(p['summa'] for p in nal))} — чеки не проверены.")
    if nal:
        flag("A. Касса", f"Наличные {day[8:]}.{day[5:7]}: {len(nal)} оплат на {rub(sum(p['summa'] for p in nal))} "
             "(" + ", ".join(f"{name(p['userId'])} {p['summa']:g}" for p in nal) + "). Прислать фото страницы тетради за день в чат «Администраторы».", "Лиза")

    # ── A4. маткапитал: ОСФР на счёт Буракова → приход «перевод на счёт» в МК ─
    if bank_ok:
        try:
            from . import tbank
            bur = tbank.statement(ACC_BUR, (d0 - timedelta(days=7)).isoformat(), day)
            for o in bur:
                if o["typeOfOperation"] != "Credit" or "СФР" not in (o.get("payer") or {}).get("name", ""):
                    continue
                od = (datetime.fromisoformat(o["operationDate"].replace("Z", "+00:00")) + timedelta(hours=3)).date().isoformat()
                pp = o.get("payPurpose") or ""
                m = re.search(r"ФИО обуч\.?\s*([А-ЯЁ][а-яё-]+)\s+([А-ЯЁ][а-яё]+)", pp)
                fam, im = (m[1], m[2]) if m else ("?", "?")
                amt = o["operationAmount"]
                hit = [p for p in pays if p.get("paymentTypeId") == T_PEREVOD and abs((p.get("summa") or 0) - amt) < 1
                       and p["date"] >= od]
                if not hit:
                    flag("A. Маткапитал", f"Маткапитал {rub(amt)} за {fam} {im} пришёл {od[8:]}.{od[5:7]} на счёт Буракова — "
                         f"в МойКлассе прихода «перевод на счёт» на эту сумму нет. Внести на баланс и создать абонемент со списанием.", "Лиза", level="bad")
        except Exception as e:  # noqa: BLE001
            flag("A. Маткапитал", f"Счёт Буракова недоступен: {str(e)[:100]}")
    # маткапитал на балансе, а абонемента на текущий месяц нет
    with db.get_conn() as conn:
        mk_users = {r["user_id"] for r in conn.execute(
            "SELECT DISTINCT user_id FROM payments WHERE json_extract(raw,'$.paymentTypeId')=? AND date>='2026-08-01'", (T_PEREVOD,))}
    for uid in mk_users:
        u = users.get(uid) or {}
        if (u.get("balans") or 0) > 5000 and not any((s.get("beginDate") or "") >= day[:8] + "01" for s in subs_by.get(uid, [])):
            flag("A. Маткапитал", f"{name(uid)}: на балансе {rub(u['balans'])} (маткапитал), абонемента на текущий месяц нет — создать со списанием с баланса.", "Лиза", phone(uid))

    # ── A5. возвраты ─────────────────────────────────────────────────────────
    for p in pays:
        if p["date"] == day and p.get("optype") == "refund":
            u = users.get(p["userId"]) or {}
            act = [s for s in subs_by.get(p["userId"], []) if s.get("statusId") == 2 and (s.get("payed") or 0) > 0
                   and (s.get("visitedCount") or 0) < (s.get("visitCount") or 0)]
            if abs(u.get("balans") or 0) > 1 or act:
                flag("A. Возвраты", f"{name(p['userId'])}: возврат {rub(-p['summa'])} ({man(p)}), но баланс {rub(u.get('balans') or 0)}"
                     f"{' и активный оплаченный абонемент не закрыт' if act else ''} — остаток → на баланс, возврат с баланса, абонемент закрыть.", "Лиза", phone(p["userId"]))
            if p.get("paymentTypeId") != T_VOZVRAT:
                flag("A. Возвраты", f"{name(p['userId'])}: возврат записан типом «{TYPES.get(p.get('paymentTypeId'), p.get('paymentTypeId'))}» — должен быть «возврат» (правило 2).", "Лиза", phone(p["userId"]))

    # ── B. абонементы, проданные за день ─────────────────────────────────────
    sold = [s for s in subs if s.get("sellDate") == day]
    seen_month = defaultdict(list)
    for s in sold:
        cls = classes.get((s.get("classIds") or [None])[0], "") if s.get("classIds") else ""
        if "2627" not in cls and cls:
            continue
        uid = s["userId"]; n = s.get("visitCount") or 0; pr = s.get("price") or 0; op = s.get("originalPrice") or pr
        disc = (s.get("discount") or 0) + (s.get("extraDiscount") or 0)
        com = s.get("comment") or ""
        ok_bi = bool(SOGLASOVANO.search(com))
        who = M.get(s.get("managerId"), "—")
        f = _fam(cls); pl = PRAYS.get(f)
        if pr <= 200 and n:
            flag("B. Абонементы", f"{name(uid)}: абонемент {f} {n} зан. за {rub(pr)} ({who}) — цена символическая. Объяснение админа.", "Борис", phone(uid), "bad")
        elif pl and n and pl[0] and op / n < pl[0] * 0.85 - 1 and not ok_bi:
            flag("B. Абонементы", f"{name(uid)}: {f} {n} зан. за {rub(op)} — {op / n:,.0f} ₽/зан. при прайсе {pl[0]} ({who}), без пометки «по согласованию с Б.И.» (правило 3).", "Борис", phone(uid))
        if disc > 15 and not ok_bi:
            flag("B. Абонементы", f"{name(uid)}: скидка {disc}% ({who}) без согласования (правило 3).", "Борис", phone(uid))
        if disc == 0 and pr < op - 1 and not ok_bi:
            flag("B. Абонементы", f"{name(uid)}: ручная цена {rub(pr)} вместо {rub(op)} ({who}) без скидки и без согласования (правило 3).", "Борис", phone(uid))
        if disc == 15 and first_paid.get(uid, "9999") < "2026-08-20" and not ok_bi:
            flag("B. Абонементы", f"{name(uid)}: −15% «за пробное», но клиент платил ещё {first_paid[uid][8:]}.{first_paid[uid][5:7]}.{first_paid[uid][:4]} — не новый ({who}). Правило 3.", "Борис", phone(uid))
        fam_u = (name(uid).split() or [""])[0].lower()
        if fam_u and fam_u in D["staff"] and disc:
            flag("B. Абонементы", f"{name(uid)}: фамилия совпадает с сотрудником, скидка {disc}% ({who}) — решение владельца.", "Борис", phone(uid))
        key = (uid, f, (s.get("beginDate") or "")[:7])
        seen_month[key].append(s)
    for key, ss in seen_month.items():
        allm = [x for x in subs_by.get(key[0], []) if _fam(classes.get((x.get("classIds") or [None])[0], "")) == key[1]
                and (x.get("beginDate") or "")[:7] == key[2] and (x.get("statusId") in (2, 4))]
        if len(allm) > 1 and sum(1 for x in allm if (x.get("payed") or 0) > 0) >= 1 and any((x.get("payed") or 0) == 0 for x in allm):
            flag("B. Абонементы", f"{name(key[0])}: на {key[2][5:]}.{key[2][:4]} по курсу {key[1]} {len(allm)} абонемента, один без оплаты — похоже на дубль, удалить лишний.", "Лиза", phone(key[0]))

    # ── C. посещения за день ─────────────────────────────────────────────────
    unpaid_visits = defaultdict(list)
    for l in D["lessons"]:
        cls = classes.get(l.get("class_id") or l.get("classId"), "")
        if "2627" not in cls:
            continue
        rs = D["recs"].get(l["id"], [])
        vis = [r for r in rs if r.get("visit")]
        if l.get("status") == 1 and not vis:
            tch = ", ".join(M.get(t, str(t)) for t in (l.get("teacherIds") or [])) or "педагог не указан"
            flag("C. Занятия", f"{cls[:50]} {l.get('begin_time') or ''}: «проведено» без присутствующих ({tch}). Правило: без детей — «отменено».", "Лиза")
        for r in vis:
            if r.get("test") or r.get("free"):
                continue
            cv = _covered(subs_by, r["user_id"], cls, day, classes)
            if cv != "paid":
                unpaid_visits[r["user_id"]].append(cls)
    for uid, cl in unpaid_visits.items():
        n_month = D["month_visits"].get(uid, 0)
        bal = (users.get(uid) or {}).get("balans") or 0
        lvl = "bad" if n_month > 2 else "warn"
        flag("C. Занятия", f"{name(uid)}: был на занятии {cl[0][5:45]} без оплаченного абонемента (баланс {rub(bal)}; за месяц посещений {n_month}). "
             f"{'Больше 2 «в долг» — правило 4: до оплаты не пускать.' if n_month > 2 else 'Взять оплату до следующего занятия.'}", "Лена", phone(uid), lvl)

    # ── итог ──────────────────────────────────────────────────────────────────
    cnt = Counter(x["level"] for b in R["bloki"].values() for x in b)
    R["itogo"] = {"оплат": len(inc), "сумма": sum(p["summa"] for p in inc), "нал": len(nal), "продано_абонементов": len(sold),
                  "замечаний": sum(cnt.values()), "красных": cnt.get("bad", 0)}
    summary = (f"Контроль оплат за {day[8:]}.{day[5:7]}: оплат {len(inc)} на {rub(R['itogo']['сумма'])}, наличных {len(nal)}, "
               f"абонементов продано {len(sold)}; замечаний {cnt.get('bad', 0) + cnt.get('warn', 0)} (красных {cnt.get('bad', 0)}). "
               + ("Банк: " + (f"терминал сходится" if abs(R.get('bank', {}).get('diff', 0)) <= 100 else f"разница {rub(R['bank']['diff'])}") + ". " if R.get("bank") and "diff" in R["bank"] else "")
               + "Подробно: app.kidsup.ru/kontrol-oplat")
    R["summary"] = summary
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{day}.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if not dry:
        from . import autopilot
        n = 0
        for who, text, ph in R["inbox"]:
            if autopilot.inbox_add(text, ph, who, source=f"контроль оплат {day[8:]}.{day[5:7]}"):
                n += 1
        autopilot.inbox_add(summary, "", "Борис", source="контроль оплат")
        R["inbox_posted"] = n
    return R


def html(day: str | None = None) -> str:
    import html as _h
    files = sorted(OUT_DIR.glob("*.json")) if OUT_DIR.exists() else []
    if day:
        f = OUT_DIR / f"{day}.json"
        files = [f] if f.exists() else []
    if not files:
        return "<p>Контроль ещё не запускался.</p>"
    R = json.loads(files[-1].read_text(encoding="utf-8"))
    days = "".join(f'<a href="/kontrol-oplat?day={p.stem}">{p.stem[8:]}.{p.stem[5:7]}</a> ' for p in sorted(OUT_DIR.glob("*.json"))[-14:])
    body = [f"<h1>Контроль оплат за {R['day'][8:]}.{R['day'][5:7]}</h1><p class=lead>{_h.escape(R.get('summary', ''))} · собрано {R.get('sostavleno', '')}</p><p>Дни: {days}</p>"]
    for blk, items in R["bloki"].items():
        body.append(f"<h2>{_h.escape(blk)} <small>({len(items)})</small></h2><ul>" + "".join(
            f'<li class={x["level"]}>{_h.escape(x["text"])}{(" <b>→ " + _h.escape(x["who"]) + "</b>") if x.get("who") else ""}</li>' for x in items) + "</ul>")
    if not R["bloki"]:
        body.append("<p class=ok>Замечаний нет.</p>")
    css = ("body{font:15px/1.5 Inter,sans-serif;max-width:1000px;margin:0 auto;padding:20px 16px;color:#221F3B}"
           "h1{color:#312783}h2{color:#312783;margin-top:24px}li{margin:6px 0}li.bad{color:#B3261E;font-weight:600}"
           "li.warn{color:#7a4b00}.ok{color:#3f7d12}.lead{color:#6A6F87}small{color:#6A6F87;font-weight:400}")
    return f"<!doctype html><html lang=ru><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Контроль оплат</title><style>{css}</style></head><body>{''.join(body)}</body></html>"
