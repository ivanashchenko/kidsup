# -*- coding: utf-8 -*-
"""Сверка оплат месяца — страница «Сверка оплат <месяца>: МойКласс ↔ Т-Банк ↔ Комтет ↔ тетрадь».

    python3 sverka_nov/page.py            собрать <папка>/page_new.html
    python3 sverka_nov/page.py --post     собрать и выложить /boris/docs/sverka-oplat
    ключи: --month 2026-11  --prodazhi-s 2026-10-24  --out файл  --na "2026-11-07T11:09"
           (--na — собрать страницу «как было на момент», по данным, созданным до него)

Читает только снимок sverka.py (<папка>/snap/*.json), tetrad.json и foto.json.

Что считается оплатой месяца: абонемент в группу с датой начала в этом месяце, или
абонемент на несколько занятий, купленный с даты начала продаж (по умолчанию — за 8
дней до 1-го числа) и начинающийся не раньше неё. Разовые (1 занятие / «1 day») и
праздники не считаем; абонемент без списания (в выгрузке платежей нет ни одного
списания на него) — отдельной строкой ниже таблицы.

Каждая оплата МойКласса (приход) сверяется поштучно:
  карта      → операция терминала Т-Банка в тот же день, та же сумма (ближайшая по
               времени); нет в списке операций — остаток дня «зачислено − операции»
               (у центра есть терминал, которого нет в выдаче банка); нет и там —
               терминал клуба Буракова (деньги на чужом счёте) или СБП (ошибка типа);
  безнал     → СБП «Терминал Kids UP» поштучно (сумма, время), остальное — реестр
               интернет-эквайринга за день: сумма до комиссии = МК (или подмножество);
  наличные   → запись в тетради за день (сумма или итог прихода дня);
  перевод    → поступление ОСФР (маткапитал) на любой из счетов;
  чек        → Комтет: external_id = id платежа МК, иначе «Уч №<ученик>» + сумма.
Касса и счета клуба Буракова — не наши: в сверку не идут (только как место, куда
по ошибке могли уйти деньги).
"""
from __future__ import annotations

import argparse
import html
import itertools
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from obshchee import (MSK, ddmm, days, load_json, now_msk, owner, p_snap,  # noqa: E402
                      save_json, sverka_dir, to_msk)

T_NAL, T_ONLINE, T_TERM, T_PEREVOD, T_KORR, T_VOZVRAT = 1, 2, 64725, 63934, 72508, 97845
TYPE_SHORT = {T_NAL: "нал", T_ONLINE: "безнал", T_TERM: "карта", T_PEREVOD: "маткапитал", T_KORR: "коррекция"}
MES_R = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
         "сентября", "октября", "ноября", "декабря"]
MES_I = ["", "январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
         "сентябрь", "октябрь", "ноябрь", "декабрь"]
SLUG = "sverka-oplat"
# правила — на странице в разделе «Как сверяю» (те же, что в README)
PRAVILA = (
    "Безнал (ссылка, СБП) → СБП «Терминал Kids UP» той же суммы в пределах 15 минут; остальное — реестр интернет-эквайринга "
    "за день (сумма до комиссии = сумма онлайн-оплат дня или их часть).",
    "Карта → операция нашего терминала в тот же день (по дате оплаты в МойКлассе), та же сумма, ближайшая по времени. "
    "Одна банковская операция идёт только к одной оплате.",
    "Одна операция терминала за несколько оплат в МойКлассе (родитель приложил карту один раз за всех детей, админ внёс "
    "по отдельности) — сходится, если сумма оплат в пределах 90 минут равна операции до копейки; так же оплата двумя операциями.",
    "Нал или карта в МойКлассе, а по банку СБП или терминал той же суммы рядом по времени — один пункт «поправить тип», "
    "деньги найдены.",
    "Карты нет в списке операций — остаток дня «зачислено − операции» (у центра есть терминал, которого банк по операциям "
    "не отдаёт): сходится до рубля — «второй терминал». Терминал клуба Буракова ±20 минут — деньги на чужом счёте.",
    "Наличные → строка прихода в тетради за день той же суммы (по фото из чата); что поштучно не сошлось — сумма "
    "оставшихся оплат наличными дня против суммы оставшихся строк прихода.",
    "Маткапитал → поступление ОСФР той же суммы (приходит на счёт «Клуб Буракова» того же ИП, оттуда переводится на KidsUP).",
    "Сумма строки — все списания на абонемент; деньги — приход, внесённый вместе со списанием, иначе «с баланса»: приходы, "
    "из которых по порядку баланса ученика оплачено это списание (самый старый приход с остатком).",
    "Чек → Комтет: external_id = id платежа, иначе «Уч №ученика» + сумма (до 3 суток), иначе онлайн-чек на брата/сестру "
    "той же суммы в пределах 3 часов. Касса клуба Буракова (Люберцы) не учитывается.",
)
OK, BAD, WAIT = "ok", "bad", "wait"
SYM = {OK: '<span class="ok">✓</span>', BAD: '<span class="bad">✗</span>', WAIT: '<span class="wait">…</span>'}

e = lambda s: html.escape(str(s if s is not None else ""), quote=True)


def num(x: float) -> str:
    """7560 → «7 560», 5312.5 → «5 312,5», 26511.64 → «26 511,64»."""
    x = round(float(x or 0), 2)
    s = f"{abs(x):,.2f}".rstrip("0").rstrip(".")
    return ("−" if x < 0 else "") + s.replace(",", " ").replace(".", ",")


def rub(x: float) -> str:
    return num(x) + " ₽"


def hm(t: datetime | None) -> str:
    return t.strftime("%H:%M") if t else ""


def cut(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n]


def group_short(cls: str) -> str:
    """«2627_ПШ_вт-пт_18:00_5-7 лет_…» → «ПШ вт-пт 18:00 5-7 лет …»."""
    s = re.sub(r"^\d{4}_", "", cls or "")
    return s.replace("_", " ")


def subset_sum(items: list, target: float, key, tol: float = 1.0, limit: int = 300000):
    """Подмножество items с суммой key = target (±tol). Сначала всё целиком, потом по размеру."""
    if not items or target <= tol:
        return None
    if abs(sum(key(x) for x in items) - target) <= tol:
        return list(items)
    n, tried = len(items), 0
    for r in range(1, n):
        for comb in itertools.combinations(items, r):
            tried += 1
            if tried > limit:
                return None
            if abs(sum(key(x) for x in comb) - target) <= tol:
                return list(comb)
    return None


class Sverka:
    def __init__(self, month: str, start_sales: str, na: datetime | None):
        self.month, self.start_sales, self.na = month, start_sales, na
        self.now = na or now_msk()
        self.today = self.now.date().isoformat()
        y, m = int(month[:4]), int(month[5:7])
        self.m1 = f"{month}-01"
        self.m2 = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(1)).isoformat()
        self.mes_r, self.mes_i = MES_R[m], MES_I[m]
        self.load()

    # ── данные ──────────────────────────────────────────────────────────────
    def cutoff(self, t: datetime | None) -> bool:
        return self.na is None or (t is not None and t <= self.na)

    def load(self) -> None:
        sp, base = p_snap(), sverka_dir()
        mk = load_json(sp / "mk.json")
        if not mk:
            raise SystemExit(f"нет {sp / 'mk.json'} — сначала sverka.py fetch")
        self.mk_at, self.mk_sync = mk.get("at"), mk.get("sync")
        self.users = {int(k): v for k, v in (mk.get("users") or {}).items()}
        self.classes = {int(k): v for k, v in (mk.get("classes") or {}).items()}
        self.managers = {int(k): (v or "").split()[0] if v else "" for k, v in (mk.get("managers") or {}).items()}
        P = []
        for p in mk.get("payments") or []:
            t = to_msk(p["createdAt"]) if p.get("createdAt") else None
            if not self.cutoff(t):
                continue
            P.append({**p, "t": t})
        self.P = P
        self.byid = {p["id"]: p for p in P}
        self.subs = [s for s in (mk.get("subs") or []) if self.na is None or (s.get("sellDate") or "") <= self.today]
        meta = load_json(sp / "meta.json", {}) or {}
        cov = meta.get("coverage") or {}
        self.cov = {k: {d for d in v if d <= self.today} for k, v in cov.items()}
        bank = load_json(sp / "bank.json", {}) or {}
        self.accounts = bank.get("accounts") or []
        ops = []
        for o in (bank.get("ops") or {}).values():
            # trxnPostDate — когда операция появилась в выписке; operationDate у операций выходных
            # банк потом переносит на понедельник
            pu = o.get("post_utc") or o.get("op_utc")
            posted = to_msk(pu) if pu else None
            if not self.cutoff(posted):
                continue
            o = dict(o)
            o["t"] = to_msk(o["auth_utc"]) if o.get("auth_utc") else posted
            o["posted"] = posted
            ops.append(o)
        self.ops = ops
        term = load_json(sp / "terminal.json", {}) or {}
        self.terminals = term.get("terminals") or []
        self.term_days = {}
        for d, per in (term.get("days") or {}).items():
            for k, lst in per.items():
                for o in lst:
                    t = to_msk(o["utc"]) if o.get("utc") else None
                    if not self.cutoff(t):
                        continue
                    self.term_days.setdefault(d, {}).setdefault(k, []).append(
                        {**o, "t": t, "term": k, "_id": f"t:{k}:{o.get('rrn')}:{o.get('utc')}"})
        kt = load_json(sp / "komtet.json", {}) or {}
        self.cheki = []
        for c in (kt.get("cheki") or {}).values():
            t = datetime.fromisoformat(c["created"]) if c.get("created") else None
            if not self.cutoff(t):
                continue
            self.cheki.append({**c, "t": t})
        self.tetrad = load_json(base / "tetrad.json", {}) or {}
        self.foto = load_json(base / "foto.json", []) or []
        if self.na:
            td = {}
            for k, v in self.tetrad.items():
                if not isinstance(v, dict) or not re.fullmatch(r"\d{4}-\d\d-\d\d", k):
                    continue
                if k <= self.today and all(datetime.fromisoformat(f) <= self.na for f in v.get("foto") or []):
                    td[k] = v
            self.tetrad = td

    # ── помощники ───────────────────────────────────────────────────────────
    def name(self, uid) -> str:
        return self.users.get(uid) or f"ученик #{uid}"

    def mgr(self, p) -> str:
        """Кто внёс оплату. Онлайн-оплату (ссылка, СБП) МойКласс создаёт сам — «онлайн»,
        даже если на ней стоит менеджер, выставивший ссылку."""
        if p.get("paymentTypeId") == T_ONLINE:
            return "онлайн"
        m = p.get("managerId")
        return self.managers.get(m, "") if m else ""

    def how(self, p) -> str:
        """«карта, <кто внёс>» / «безнал, онлайн»."""
        return f"{TYPE_SHORT.get(p.get('paymentTypeId'), '?')}, {self.mgr(p) or 'онлайн'}"

    def pgroup(self, p) -> str:
        c = p.get("comment") or ""
        return group_short(c.split(" - ", 1)[1] if " - " in c else c)

    def class_of(self, s) -> str:
        ids = s.get("classIds") or []
        cid = s.get("mainClassId") or (ids[0] if ids else None)
        return self.classes.get(cid, "") if cid else ""

    # ── сверка ──────────────────────────────────────────────────────────────
    def run(self) -> None:
        self.prep_bank()
        self.inc = [p for p in self.P if p["optype"] == "income" and (p.get("summa") or 0) > 0.5]
        for p in self.inc:
            p["d"] = p["date"]
        self.res = {}          # id прихода → {"st", "txt", "how"}
        self.problems = []     # (день, раздел, текст, кому)
        self.used = set()
        self.match_bank()
        self.match_cash()
        self.match_cheki()
        self.build_rows()

    def prep_bank(self) -> None:
        ours = lambda o: not o.get("chuzhoy")
        self.sbp = [o for o in self.ops if o["kind"] == "sbp" and ours(o)]
        for o in self.sbp:
            o["_id"] = "b:" + o["id"]
        self.reestr = defaultdict(float)
        self.reestr_fee = defaultdict(float)
        for o in self.ops:
            if o["kind"] == "reestr" and ours(o):
                self.reestr[o["for"]] += o["amount"] + (o.get("fee") or 0)
                self.reestr_fee[o["for"]] += o.get("fee") or 0
        self.credit = defaultdict(float)      # день операций → зачислено (наш счёт)
        self.credit_bur = defaultdict(float)
        for o in self.ops:
            if o["kind"] in ("term_credit", "term_fee"):
                d = (date.fromisoformat(o["for"]) - timedelta(1)).isoformat()
                if o["kind"] == "term_credit":
                    (self.credit if ours(o) else self.credit_bur)[d] += o["amount"]
        self.osfr = [o for o in self.ops if o["kind"] == "osfr"]
        for o in self.osfr:
            o["_id"] = "b:" + o["id"]
        # поступления на наши счета не от банка (переводы по реквизитам); возвраты по карте — не они
        self.credit_other = [o for o in self.ops if o["kind"] == "credit_other" and ours(o)
                             and "тбанк" not in (o.get("payer") or "").lower() and "возврат" not in (o.get("purpose") or "").lower()
                             and o.get("t") and self.start_sales <= o["t"].date().isoformat() <= self.today]
        self.fee_day = defaultdict(float)
        for o in self.ops:
            if o["kind"] == "term_fee" and ours(o):
                self.fee_day[(date.fromisoformat(o["for"]) - timedelta(1)).isoformat()] += o["amount"]
        # какой терминал наш: его операции за день = зачислению на наш счёт
        score = {}
        for k in self.terminals:
            a = b = 0
            for d, per in self.term_days.items():
                s = round(sum(o["amount"] * (1 if o["type"] == "Debit" else -1 if o["type"] == "Credit" else 0)
                              for o in per.get(k, [])), 2)
                if s <= 0:
                    continue
                if d in self.credit and abs(self.credit[d] - s) <= 1:
                    a += 1
                elif d in self.credit and s <= self.credit[d] + 1:
                    a += 0.5
                if d in self.credit_bur and abs(self.credit_bur[d] - s) <= 1:
                    b += 1
                elif d in self.credit_bur and s <= self.credit_bur[d] + 1:
                    b += 0.5
            score[k] = (a, b)
        self.term_our = [k for k, (a, b) in score.items() if a > b] or (self.terminals[:1] if self.terminals else [])
        self.term_bur = [k for k in self.terminals if k not in self.term_our]

    def tops(self, d: str, keys: list, kind: str = "Debit") -> list:
        per = self.term_days.get(d, {})
        return [o for k in keys for o in per.get(k, []) if o["type"] == kind]

    def term_total(self, d: str, keys: list) -> float | None:
        if d not in self.cov.get("terminal", set()):
            return None
        return round(sum(o["amount"] for o in self.tops(d, keys)) - sum(o["amount"] for o in self.tops(d, keys, "Credit")), 2)

    def setres(self, p, st, txt, how="", op=None) -> None:
        self.res[p["id"]] = {"st": st, "txt": txt, "how": how}
        if op is not None:
            self.used.add(op["_id"])
            p["_op"] = op

    def _assign(self, pays, ops, ok, key, on_hit) -> None:
        pairs = []
        for p in pays:
            for o in ops:
                if o["_id"] in self.used or p["id"] in self.res:
                    continue
                if ok(p, o):
                    pairs.append((key(p, o), p["id"], o["_id"], p, o))
        pairs.sort(key=lambda x: x[:3])
        for _, _, _, p, o in pairs:
            if p["id"] in self.res or o["_id"] in self.used:
                continue
            on_hit(p, o)

    def _reestr(self, online: list, final: bool) -> None:
        """Онлайн-оплаты картой (не СБП) ↔ реестр интернет-эквайринга за день, до комиссии."""
        last = max(self.reestr) if self.reestr else ""
        for d in sorted({p["d"] for p in online}):
            rest = [p for p in online if p["d"] == d and p["id"] not in self.res]
            if not rest:
                continue
            R = round(self.reestr.get(d, 0.0) - self.reestr_used[d], 2)
            S = round(sum(p["summa"] for p in rest), 2)
            full = f"интернет-эквайринг: реестр {ddmm(d)} сошёлся ({num(self.reestr.get(d, 0))} до комиссии)"
            if R > 0.5 and abs(R - S) <= 1:
                for p in rest:
                    self.setres(p, OK, full, "reestr")
                self.reestr_used[d] += S
                continue
            if R > S + 1:
                if final:   # в банке больше, чем в МК: все оплаты дня в реестре могут быть
                    for p in rest:
                        self.setres(p, OK, f"интернет-эквайринг: реестр {ddmm(d)} {num(self.reestr[d])} до комиссии — больше, чем в МК, на {num(R - S)}", "reestr_bolshe")
                    self.reestr_used[d] += S
                    self.problems.append((d, "МойКласс", f"Реестр интернет-эквайринга {ddmm(d)}: в банке {num(self.reestr[d])} ₽ до комиссии, в МойКлассе онлайн-оплат картой на {num(R - S)} ₽ меньше. Найти оплату по ссылке, которой нет в МК за этот день, и внести.", "Лиза"))
                continue
            if R > 0.5:
                sub = subset_sum(rest, R, lambda p: p["summa"])
                if sub:
                    for p in sub:
                        self.setres(p, OK, f"интернет-эквайринг: реестр {ddmm(d)} ({num(self.reestr[d])} до комиссии) — эта оплата в нём есть", "reestr")
                    self.reestr_used[d] += sum(p["summa"] for p in sub)
                    rest = [p for p in rest if p["id"] not in self.res]
            if not final or not rest:
                continue
            for p in rest:
                if not self.reestr.get(d):
                    if d >= last:
                        self.setres(p, WAIT, f"реестр интернет-эквайринга за {ddmm(d)} ещё не пришёл")
                    else:
                        self.setres(p, BAD, f"в банке нет ни СБП, ни реестра интернет-эквайринга за {ddmm(d)}", "reestr_net")
                else:
                    self.setres(p, BAD, f"в реестре {ddmm(d)} её нет: в банке {num(self.reestr[d])}, в МК онлайн-оплат {num(S + self.reestr_used[d])}", "reestr_net")

    def match_bank(self) -> None:
        cb, ct = self.cov.get("bank", set()), self.cov.get("terminal", set())
        inb = [p for p in self.inc if p["d"] in cb]
        dmin = lambda a, b: abs((a - b).total_seconds()) / 60 if a and b else 1e9
        same = lambda p, o: abs((p.get("summa") or 0) - (o.get("amount") or 0)) < 0.011
        online = [p for p in inb if p.get("paymentTypeId") == T_ONLINE]
        card = [p for p in inb if p.get("paymentTypeId") == T_TERM]
        cash = [p for p in inb if p.get("paymentTypeId") == T_NAL]

        def hit_sbp(p, o, note=""):
            self.setres(p, OK, f"СБП {hm(o['t'])}" + (f" ({o['t'].strftime('%d.%m')})" if o["t"].date().isoformat() != p["d"] else "") + note, "sbp", o)
        # 1. СБП ↔ онлайн-оплаты: МойКласс создаёт оплату в ту же минуту
        self._assign(online, self.sbp, lambda p, o: same(p, o) and dmin(p["t"], o["t"]) <= 15,
                     lambda p, o: dmin(p["t"], o["t"]), hit_sbp)
        # 2. карта ↔ операция терминала за день (по дате оплаты в МК), ближайшая по времени
        allt = [o for d in ct for o in self.tops(d, self.term_our)]
        self._assign(card, allt, lambda p, o: same(p, o) and o["t"] and o["t"].date().isoformat() == p["d"],
                     lambda p, o: dmin(p["t"], o["t"]),
                     lambda p, o: self.setres(p, OK, f"терминал {hm(o['t'])} *{o['last4']}", "term", o))
        # 3. реестр интернет-эквайринга за день: оставшиеся онлайн-оплаты дня = сумма до комиссии
        #    (раньше позднего СБП — иначе СБП той же суммы из другого часа «съедает» оплату картой)
        self.reestr_used = defaultdict(float)
        self._reestr(online, final=False)
        # 4. СБП ↔ оставшиеся онлайн (внесены позже) и карта/нал, записанные не тем типом
        self._assign(online, self.sbp, lambda p, o: same(p, o) and dmin(p["t"], o["t"]) <= 36 * 60,
                     lambda p, o: dmin(p["t"], o["t"]), hit_sbp)
        self._assign(card, self.sbp, lambda p, o: same(p, o) and dmin(p["t"], o["t"]) <= 36 * 60,
                     lambda p, o: dmin(p["t"], o["t"]),
                     lambda p, o: (hit_sbp(p, o, " (в МК тип «карта»)"), self.res[p["id"]].update(how="sbp_tip")))
        self._assign(cash, self.sbp, lambda p, o: same(p, o) and dmin(p["t"], o["t"]) <= 12 * 60,
                     lambda p, o: dmin(p["t"], o["t"]),
                     lambda p, o: (hit_sbp(p, o, " (в МК тип «нал»)"), self.res[p["id"]].update(how="sbp_tip")))
        # нал, а по банку карта — та же сумма на терминале в тот же день, в пределах 30 минут
        self._assign(cash, allt, lambda p, o: same(p, o) and o["t"] and o["t"].date().isoformat() == p["d"] and dmin(p["t"], o["t"]) <= 30,
                     lambda p, o: dmin(p["t"], o["t"]),
                     lambda p, o: self.setres(p, OK, f"терминал {hm(o['t'])} *{o['last4']} (в МК тип «нал»)", "term_tip", o))
        # 4а. одна операция терминала = несколько оплат в МК (родитель приложил карту один раз за
        #     всех детей / все абонементы, админ внёс по отдельности) и наоборот — оплата двумя операциями
        for o in sorted(allt, key=lambda o: o["t"]):
            if o["_id"] in self.used:
                continue
            near = [p for p in card if p["id"] not in self.res and p["d"] == o["t"].date().isoformat()
                    and dmin(p["t"], o["t"]) <= 90][:12]
            sub = subset_sum(near, o["amount"], lambda p: p["summa"], tol=0.011) if len(near) >= 2 else None
            if sub and len(sub) >= 2:
                for p in sub:
                    self.setres(p, OK, f"терминал {hm(o['t'])} *{o['last4']} — одной операцией {num(o['amount'])} ₽ за {len(sub)} оплаты", "term", None)
                    p["_op"] = o
                self.used.add(o["_id"])
        for p in card:
            if p["id"] in self.res:
                continue
            near = [o for o in allt if o["_id"] not in self.used and o["t"].date().isoformat() == p["d"] and dmin(p["t"], o["t"]) <= 90]
            for l4 in sorted({o["last4"] for o in near}):
                grp = [o for o in near if o["last4"] == l4][:8]
                sub = subset_sum(grp, p["summa"], lambda o: o["amount"], tol=0.011) if len(grp) >= 2 else None
                if sub and len(sub) >= 2:
                    self.setres(p, OK, "терминал " + " + ".join(f"{hm(o['t'])} {num(o['amount'])}" for o in sub) + f" *{l4} — оплата двумя операциями", "term")
                    p["_op"] = sub[0]
                    for o in sub:
                        self.used.add(o["_id"])
                    break
        # 5. карта, которой нет в списке операций: остаток дня «зачислено − операции»
        for d in sorted({p["d"] for p in card}):
            rest = [p for p in card if p["d"] == d and p["id"] not in self.res]
            if not rest:
                continue
            tot = self.term_total(d, self.term_our)
            if d in self.credit and tot is not None:
                resid = round(self.credit[d] - tot, 2)
                sub = subset_sum(rest, resid, lambda p: p["summa"])
                for p in sub or []:
                    self.setres(p, OK, "второй терминал: операции нет в списке банка, зачисление за день сходится до рубля", "term2")
        # 6. терминал клуба Буракова: та же сумма ±20 минут — деньги на чужом счёте
        bt = [o for d in ct for o in self.tops(d, self.term_bur)]
        self._assign([p for p in card if p["id"] not in self.res], bt,
                     lambda p, o: same(p, o) and dmin(p["t"], o["t"]) <= 20,
                     lambda p, o: dmin(p["t"], o["t"]),
                     lambda p, o: self.setres(p, WAIT, f"прошла по терминалу клуба Буракова {hm(o['t'])} *{o['last4']} — деньги на счёте клуба", "bur", o))
        for p in card:
            if p["id"] in self.res:
                continue
            if p["d"] not in ct or p["d"] not in self.credit:
                self.setres(p, WAIT, f"зачисления за {ddmm(p['d'])} ещё нет — банк отдаёт операции с задержкой")
            else:
                self.setres(p, BAD, "на терминале такой операции нет", "net")
        # остаток онлайн-оплат — снова к реестру (за вычетом уже сошедшихся)
        self._reestr(online, final=True)
        # 7. маткапитал / перевод на счёт: поступление ОСФР той же суммы
        for p in [p for p in self.inc if p.get("paymentTypeId") == T_PEREVOD]:
            d0 = date.fromisoformat(p["d"])
            c = [o for o in self.osfr if o["_id"] not in self.used and abs(o["amount"] - p["summa"]) < 1
                 and o["t"] and d0 - timedelta(60) <= o["t"].date() <= d0 + timedelta(5)]
            if c:
                o = min(c, key=lambda o: abs((o["t"].date() - d0).days))
                self.setres(p, OK, f"ОСФР {o['t'].strftime('%d.%m')} на счёт «{o['acc_name']}»", "osfr", o)
            elif p["d"] in cb:
                self.setres(p, WAIT, f"поступления ОСФР на {num(p['summa'])} ₽ в выписке с {ddmm(min(cb))} нет")
        for p in [p for p in self.inc if p.get("paymentTypeId") == T_KORR]:
            self.setres(p, WAIT, "коррекция в МойКлассе — не деньги")
        # лишнее в банке
        for d in sorted(ct):
            for o in self.tops(d, self.term_our):
                if o["_id"] in self.used:
                    continue
                same_card = []
                for x in self.inc:
                    xo = x.get("_op")
                    if xo and xo.get("last4") == o["last4"] and xo.get("term") and abs((xo["t"] - o["t"]).days) <= 3:
                        same_card.append(f"в {hm(xo['t'])} оплачено {num(x['summa'])} ₽ — {self.name(x['userId'])} ({cut(self.pgroup(x), 30)})")
                d_t = o["t"].date().isoformat()
                # подсказка: оплаты «нет на терминале» за соседние дни, которые вместе дают эту сумму
                d0 = o["t"].date()
                lost = [x for x in card if (self.res.get(x["id"]) or {}).get("how") == "net"
                        and abs((date.fromisoformat(x["d"]) - d0).days) <= 2][:10]
                hint = subset_sum(lost, o["amount"], lambda x: x["summa"], tol=0.011) if lost else None
                hint_t = ("; похоже, это " + " + ".join(f"{self.name(x['userId'])} {num(x['summa'])} ₽ ({ddmm(x['d'])})" for x in hint)
                          + " одной операцией — в МК у оплаты другая дата, поправить" if hint else "")
                self.problems.append((d_t, "МойКласс", f"Терминал {ddmm(d_t)} {hm(o['t'])}: {num(o['amount'])} ₽ картой *{o['last4']} — деньги пришли, в МойКлассе такой оплаты нет"
                                      + ("; той же картой " + ", ".join(same_card) if same_card else "") + hint_t
                                      + ("." if hint else ". Выяснить, за что, и внести."), "Лиза"))
        for o in self.sbp:
            if o["_id"] in self.used or not o["t"] or o["t"].date().isoformat() not in cb:
                continue
            d_t = o["t"].date().isoformat()
            self.problems.append((d_t, "МойКласс", f"СБП {ddmm(d_t)} {hm(o['t'])}: {num(o['amount'])} ₽ — деньги пришли, в МойКлассе такой оплаты нет. Выяснить, за что, и внести.", "Лиза"))
        for o in self.credit_other:
            if not o["t"]:
                continue
            d_t = o["t"].date().isoformat()
            hitp = [p for p in self.inc if p.get("paymentTypeId") == T_PEREVOD and abs(p["summa"] - o["amount"]) < 1
                    and abs((date.fromisoformat(p["d"]) - o["t"].date()).days) <= 10]
            if not hitp:
                self.problems.append((d_t, "МойКласс", f"Поступление на счёт «{o['acc_name']}» {ddmm(d_t)}: {num(o['amount'])} ₽ — в МойКлассе оплаты переводом на эту сумму нет. Выяснить, от кого, и внести.", "Лиза"))
        # проблемы по оплатам
        for p in inb:
            r = self.res.get(p["id"]) or {}
            nm, t = self.name(p["userId"]), p["t"]
            if r.get("how") == "net":
                self.problems.append((p["d"], "Деньги", f"{nm}: {num(p['summa'])} ₽ «картой» {hm(t)} ({self.mgr(p) or '—'}) — на терминале банка такой оплаты нет, в зачислении за день её тоже нет. Чем и куда заплатили?", "Борис"))
            elif r.get("how") == "bur":
                self.problems.append((p["d"], "Деньги", f"{nm}: {num(p['summa'])} ₽ «картой» {hm(t)} ({self.mgr(p) or '—'}) — {r['txt']}. Перевести на счёт KidsUP.", "Борис"))
            elif r.get("how") == "sbp_tip":
                tip = "карта" if p.get("paymentTypeId") == T_TERM else "нал"
                self.problems.append((p["d"], "МойКласс", f"{nm}: {num(p['summa'])} ₽ записано «{tip}», по банку это {r['txt'].split(' (')[0]} — поправить тип платежа.", "Лиза"))
            elif r.get("how") == "term_tip":
                self.problems.append((p["d"], "МойКласс", f"{nm}: {num(p['summa'])} ₽ записано «нал», по банку это {r['txt'].split(' (')[0]} — поправить тип платежа.", "Лиза"))
            elif r.get("how") == "reestr_net":
                self.problems.append((p["d"], "Деньги", f"{nm}: {num(p['summa'])} ₽ онлайн {hm(t)} — {r['txt']}. Прошла ли оплата?", "Борис"))

    def tetrad_day(self, d):
        v = self.tetrad.get(d)
        return v if isinstance(v, dict) else None

    def match_cash(self) -> None:
        cash = [p for p in self.inc if p.get("paymentTypeId") == T_NAL and p["id"] not in self.res]
        self.no_foto = defaultdict(float)
        for d in sorted({p["d"] for p in cash} | {k for k in self.tetrad if re.fullmatch(r"\d{4}-\d\d-\d\d", k)}):
            if d < self.start_sales_window():
                continue
            ps = [p for p in cash if p["d"] == d]
            td = self.tetrad_day(d)
            if td is None:
                for p in ps:
                    self.setres(p, WAIT, "тетрадь: фото за день ещё нет")
                    self.no_foto[d] += p["summa"]
                continue
            prih = [dict(x, _i=i) for i, x in enumerate(td.get("prihody") or []) if (x.get("summa") or 0) > 0]
            used = set()
            for p in ps:
                h = next((x for x in prih if x["_i"] not in used and abs(x["summa"] - p["summa"]) < 0.6), None)
                if h:
                    used.add(h["_i"])
                    self.setres(p, OK, f"тетрадь +{num(h['summa'])} ✓", "tetrad")
            rest = [p for p in ps if p["id"] not in self.res]
            restp = [x for x in prih if x["_i"] not in used]
            if rest and restp and abs(sum(p["summa"] for p in rest) - sum(x["summa"] for x in restp)) <= 1:
                tot = sum(x["summa"] for x in prih)
                for p in rest:
                    self.setres(p, OK, f"тетрадь: приход дня {num(tot)} ✓", "tetrad")
                restp = []
            for p in ps:
                if p["id"] not in self.res:
                    self.setres(p, BAD, "в тетради такого прихода нет", "tetrad_net")
                    self.problems.append((d, "МойКласс", f"{self.name(p['userId'])}: наличные {num(p['summa'])} ₽ ({self.mgr(p) or '—'}) в МойКлассе есть, в тетради за {ddmm(d)} — нет.", "дежурная"))
            for x in restp:
                self.problems.append((d, "МойКласс", f"Тетрадь {ddmm(d)}: приход +{num(x['summa'])} ₽" + (f" ({x['chto']})" if x.get("chto") else "") + " — в МойКлассе такой оплаты наличными нет.", "дежурная"))

    def start_sales_window(self) -> str:
        cov = self.cov.get("bank", set()) | self.cov.get("komtet", set())
        return min([self.start_sales] + sorted(cov)[:1])

    def match_cheki(self) -> None:
        ck = self.cov.get("komtet", set())
        ours = [c for c in self.cheki if not c.get("chuzhoy") and c.get("state") == "done" and c.get("intent") in (None, "sell")]
        self.cheki_bur = [c for c in self.cheki if c.get("chuzhoy") and c.get("state") == "done"]
        for c in ours:
            c["_id"] = f"k:{c['id']}"
        self.chek = {}
        used = set()
        pays = [p for p in self.inc if p.get("paymentTypeId") in (T_NAL, T_ONLINE, T_TERM, T_PEREVOD)]
        byext = {c["ext"]: c for c in ours}
        for p in pays:
            c = byext.get(str(p["id"]))
            if c and c["_id"] not in used:
                self.chek[p["id"]] = c
                used.add(c["_id"])
        # онлайн-чеки и прочие: ученик + сумма, ближайший по времени (до 3 суток)
        pairs = []
        for p in pays:
            if p["id"] in self.chek:
                continue
            for c in ours:
                if c["_id"] in used or c.get("uid") != p["userId"] or abs((c.get("amount") or 0) - p["summa"]) > 0.6:
                    continue
                dt = abs((c["t"] - p["t"]).total_seconds()) if c.get("t") and p.get("t") else 1e12
                if dt <= 3 * 86400:
                    pairs.append((dt, p["id"], c["_id"], p, c))
        pairs.sort(key=lambda x: x[:3])
        for _, _, _, p, c in pairs:
            if p["id"] in self.chek or c["_id"] in used:
                continue
            self.chek[p["id"]] = c
            used.add(c["_id"])
        # онлайн-чек пробит на брата/сестру (другой «Уч №»), та же сумма, в пределах 3 часов
        pairs = []
        for p in pays:
            if p["id"] in self.chek:
                continue
            for c in ours:
                if c["_id"] in used or abs((c.get("amount") or 0) - p["summa"]) > 0.6 or not (c.get("t") and p.get("t")):
                    continue
                dt = abs((c["t"] - p["t"]).total_seconds())
                if dt <= 3 * 3600:
                    pairs.append((dt, p["id"], c["_id"], p, c))
        pairs.sort(key=lambda x: x[:3])
        for _, _, _, p, c in pairs:
            if p["id"] in self.chek or c["_id"] in used:
                continue
            self.chek[p["id"]] = c
            used.add(c["_id"])
        self.cheki_ours = ours

    def chek_txt(self, p) -> tuple[str, str]:
        if p is None:
            return BAD, ""
        c = self.chek.get(p["id"])
        if c:
            t = c.get("time") or ""
            tt = t[11:16] if len(t) >= 16 else hm(c.get("t"))
            if not re.fullmatch(r"\d+", c.get("ext") or ""):
                return OK, f"онлайн-чек {tt}"
            return OK, f"чек {tt} ({'/'.join(c.get('pay') or []) or '?'})"
        if p["d"] not in self.cov.get("komtet", set()):
            return WAIT, f"чеки за {ddmm(p['d'])} не загружены"
        if p.get("paymentTypeId") in (T_PEREVOD, T_KORR):
            return WAIT, ""
        return BAD, "чека нет"

    # ── откуда деньги на списание: баланс ученика по порядку ────────────────
    def fund_map(self) -> dict:
        """id списания → [(приход, сколько из него), …].

        Баланс ученика проходим по времени создания: приход кладёт деньги, списание и
        возврат забирают. Списание берёт сначала из прихода, внесённого вместе с ним
        (±2 минуты — МойКласс пишет их в одну секунду), потом из самого старого
        прихода с остатком. Если денег на балансе нет (в долг), списание гасит
        следующий приход. Раньше «с баланса» показывался последний приход до списания,
        даже если его целиком съели другие списания."""
        TW = 120
        by_user = defaultdict(list)
        for p in self.P:
            if p.get("t") is not None and p["optype"] in ("income", "debit", "refund") and abs(p.get("summa") or 0) >= 0.005:
                by_user[p["userId"]].append(p)
        fund = {}
        for evs in by_user.values():
            # в одну секунду приход раньше списания (МойКласс так и пишет: id прихода меньше)
            evs = sorted(evs, key=lambda p: (p["t"], p["optype"] != "income", p["id"]))
            pool, debts = [], []          # [приход, остаток] / [списание, непокрыто]
            for p in evs:
                amt = round(abs(p.get("summa") or 0), 2)
                if p["optype"] == "income":
                    rest = amt
                    for dd in debts:      # сначала гасим долг
                        if rest < 0.005:
                            break
                        take = round(min(rest, dd[1]), 2)
                        if take >= 0.005:
                            dd[1] = round(dd[1] - take, 2)
                            rest = round(rest - take, 2)
                            if dd[0]["optype"] == "debit":
                                fund.setdefault(dd[0]["id"], []).append((p, take))
                    debts = [dd for dd in debts if dd[1] >= 0.005]
                    pool.append([p, rest])
                    continue
                need = amt
                twin = sorted((x for x in pool if x[1] >= 0.005 and abs((x[0]["t"] - p["t"]).total_seconds()) <= TW),
                              key=lambda x: (abs((x[0]["t"] - p["t"]).total_seconds()), abs(x[0]["id"] - p["id"])))
                tid = {id(x) for x in twin}
                for x in twin + [x for x in pool if id(x) not in tid]:
                    if need < 0.005:
                        break
                    if x[1] < 0.005:
                        continue
                    take = round(min(need, x[1]), 2)
                    x[1] = round(x[1] - take, 2)
                    need = round(need - take, 2)
                    if p["optype"] == "debit":
                        fund.setdefault(p["id"], []).append((x[0], take))
                pool = [x for x in pool if x[1] >= 0.005]
                if need >= 0.005:
                    debts.append([p, need])
        return fund

    # ── оплаты месяца ───────────────────────────────────────────────────────
    def build_rows(self) -> None:
        fund = self.fund_map()
        deb = defaultdict(list)
        for p in self.P:
            if p["optype"] == "debit" and p.get("userSubscriptionId"):
                deb[p["userSubscriptionId"]].append(p)
        inc_by_user = defaultdict(list)
        for p in self.inc:
            inc_by_user[p["userId"]].append(p)
        rows, nodeb = [], []
        for s in self.subs:
            cls = self.class_of(s)
            b, sd = s.get("beginDate") or "", s.get("sellDate") or ""
            if "праздник" in cls.lower() or (s.get("visitCount") or 0) <= 1 or s.get("period") == "1 day":
                continue
            v_mes = self.m1 <= b <= self.m2
            v_dop = sd >= self.start_sales and self.start_sales <= b < self.m1
            if not (v_mes or v_dop):
                continue
            ds = sorted(deb.get(s["id"], []), key=lambda p: p["t"] or now_msk())
            if not ds:
                if (s.get("payed") or 0) > 0:
                    nodeb.append(s)
                continue
            total = round(-sum(p["summa"] for p in ds), 2)
            if total <= 0.5:
                continue
            # приход, внесённый вместе со списанием (МойКласс пишет их в одну секунду, id подряд)
            def pair_of(dp):
                c = [p for p in inc_by_user.get(s["userId"], []) if p["t"] and dp["t"] and abs((p["t"] - dp["t"]).total_seconds()) <= 120]
                return min(c, key=lambda p: (abs((p["t"] - dp["t"]).total_seconds()), abs(p["id"] - dp["id"]))) if c else None
            pairs = [(dp, pair_of(dp)) for dp in ds]
            # главное списание — самое крупное из оплаченных «живыми» деньгами, иначе самое крупное
            main, pair = max(pairs, key=lambda x: (x[1] is not None, -x[0]["summa"]))
            row = {"sub": s["id"], "uid": s["userId"], "name": self.name(s["userId"]), "group": group_short(cls), "summa": total,
                   "deb": [p["id"] for p in ds]}
            if pair:
                r = self.res.get(pair["id"]) or {"st": WAIT, "txt": f"банк за {ddmm(pair['d'])} не загружен"}
                row.update(when_d=pair["d"], when_t=pair["t"], how=self.how(pair), pay=pair["id"], st=r["st"], bank=r["txt"])
                if abs(pair["summa"] - total) > 0.6:
                    row["bank"] += f" (приход {num(pair['summa'])} ₽, на абонемент {num(total)})"
            else:
                # с баланса: приходы, из которых по порядку баланса оплачено главное списание
                src = [(f, a) for f, a in fund.get(main["id"], []) if f["optype"] == "income"]
                row.update(when_d=main["date"], when_t=main["t"])
                if src:
                    def st_of(f):
                        r = self.res.get(f["id"])
                        if r is None:
                            r = {"st": WAIT, "txt": "до начала сверки" if (f.get("d") or f["date"]) < self.start_sales_window()
                                 else f"банк за {ddmm(f.get('d') or f['date'])} не загружен"}
                        return r
                    f, a = max(src, key=lambda x: (x[1], x[0]["t"]))
                    r = st_of(f)
                    sts = [st_of(x)["st"] for x, _ in src]
                    st = BAD if BAD in sts else OK if all(x == OK for x in sts) else WAIT
                    fd = f.get("d") or f["date"]
                    txt = f"с баланса; приход {ddmm(fd)} {num(f['summa'])} ₽ — {r['txt']}"
                    if len(src) > 1:
                        others = [(x, y) for x, y in src if x is not f]
                        txt += (f"; ещё {num(sum(y for _, y in others))} ₽ — из " +
                                ", ".join(f"прихода {ddmm(x.get('d') or x['date'])} {num(x['summa'])} ₽ ({TYPE_SHORT.get(x.get('paymentTypeId'), '?')}"
                                          + {OK: ", ✓", BAD: ", ✗", WAIT: ""}[st_of(x)["st"]] + ")" for x, _ in others[:3])
                                + (f" и ещё {len(others) - 3}" if len(others) > 3 else ""))
                    row.update(how=self.how(f), pay=f["id"], st=st, bank=txt, balans=True)
                else:
                    row.update(how="?, ", pay=None, st=BAD, bank="платёж не найден: прихода до списания в МойКлассе нет")
            if len(ds) > 1:
                rest = round(total + main["summa"], 2)
                row["bank"] += f"; ещё {num(rest)} ₽ — другим списанием" + (" с баланса" if not any(q for dp, q in pairs if dp is not main) else "")
            pay = self.byid.get(row.get("pay")) if row.get("pay") else None
            row["ck_st"], row["ck"] = self.chek_txt(pay)
            rows.append(row)
        rows.sort(key=lambda r: (r["when_d"], r["when_t"] or now_msk()))
        self.rows, self.nodeb = rows, nodeb

    def zametki(self) -> list:
        """Заметки владельцу из <папка>/zametki.json (данные, не код — там могут быть имена):
        [{"s": "2026-11-10", "do": "2026-11-30", "zagolovok": "…", "abzacy": ["…"], "spisok": ["…"]}].
        Показываются на странице с «s» по «do» включительно (без дат — всегда)."""
        z = load_json(sverka_dir() / "zametki.json", []) or []
        if isinstance(z, dict):
            z = [z]
        return [x for x in z if isinstance(x, dict) and (x.get("s") or "") <= self.today <= (x.get("do") or "9999")]

    # ── итоги ───────────────────────────────────────────────────────────────
    def itogi(self) -> dict:
        R = self.rows
        found = sum(r["summa"] for r in R if r["st"] == OK)
        win = self.start_sales_window()
        ck = self.cov.get("komtet", set())
        need = [p for p in self.inc if p["d"] >= self.start_sales and p["d"] in ck and p.get("paymentTypeId") in (T_NAL, T_ONLINE, T_TERM)]
        noch = [p for p in need if p["id"] not in self.chek]
        probs = [x for x in self.problems if x[0] >= self.start_sales]
        return {"oplat": len(R), "detey": len({r["uid"] for r in R}), "summa": round(sum(r["summa"] for r in R), 2),
                "naideno": round(found, 2), "s_chekom": sum(1 for r in R if r["ck_st"] == OK),
                "rashozhdeniy": len(probs), "bez_cheka": len(noch), "bez_cheka_summa": round(sum(p["summa"] for p in noch), 2),
                "bez_spisaniya": len(self.nodeb), "okno": win}

    # ── страница ────────────────────────────────────────────────────────────
    def html(self) -> tuple[str, str]:
        I = self.itogi()
        self.I = I
        now = self.now
        sync = ""
        if self.mk_sync:
            try:
                sync = to_msk(self.mk_sync).strftime("%d.%m %H:%M")
            except ValueError:
                sync = self.mk_sync
        if self.na:
            sync = now.strftime("%d.%m %H:%M") + " (данные, созданные до этого момента)"
        cb = sorted(self.cov.get("bank", set()))
        H = [HEAD.replace("{TITLE}", f"Сверка оплат {self.mes_r}")]
        H.append(f"<h1>Сверка оплат {self.mes_r}</h1>")
        H.append(f'<p class="sub">Обновлено {now:%d.%m}, {now:%H:%M} МСК, МойКласс — по копии базы {e(sync)}. '
                 "Каждая оплата МойКласса сверена с Т-Банком (терминал — по каждой операции, СБП — по каждой, "
                 "интернет-эквайринг — реестром за день, поступления на счёт), с чеками Комтета и с фото кассовой "
                 "тетради из чата «Администраторы»."
                 + (f" Банк и чеки загружены за {ddmm(cb[0])}–{ddmm(cb[-1])}." if cb else " Банк не загружен.") + "</p>")
        kp = [(str(I["oplat"]), f"оплат {self.mes_r} ({I['detey']} детей)"), (rub(I["summa"]), f"оплачено за {self.mes_i}"),
              (rub(I["naideno"]), "деньги нашлись в банке"), (f"{I['s_chekom']} из {I['oplat']}", "с чеком Комтет"),
              (str(I["rashozhdeniy"]), "расхождения по деньгам"), (str(I["bez_cheka"]), f"оплат без чека с {ddmm(self.start_sales)}")]
        H.append('<div class="kp">' + "".join(f"<div><b>{e(a)}</b><span>{e(b)}</span></div>" for a, b in kp) + "</div>\n")
        for z in self.zametki():
            H.append('<div class="card dec">' + (f"<h3>{e(z['zagolovok'])}</h3>" if z.get("zagolovok") else "")
                     + "".join(f"<p>{e(a)}</p>" for a in z.get("abzacy") or [])
                     + ("<ul>" + "".join(f"<li>{e(x)}</li>" for x in z["spisok"]) + "</ul>" if z.get("spisok") else "")
                     + "</div>\n")
        # 1. каждая оплата
        H.append(f'<h2 id="noyabr">1. Каждая оплата за {self.mes_i}</h2>\n<div class="tw"><table><tr><th>Когда</th><th>Ребёнок</th><th>Группа</th>'
                 '<th style="text-align:right">₽</th><th>Как внесено в МК</th><th>Деньги в банке</th><th>Чек</th></tr>\n')
        for r in self.rows:
            when = ddmm(r["when_d"]) + (" " + hm(r["when_t"]) if r.get("when_t") else "")
            H.append(f"<tr><td>{e(when)}</td><td>{e(r['name'])}</td><td title=\"{e(r['group'])}\">{e(cut(r['group'], 40))}</td>"
                     f"<td class='n'>{num(r['summa'])}</td><td>{e(r['how'])}</td><td>{SYM[r['st']]} {e(r['bank'])}</td>"
                     f"<td>{SYM[r['ck_st']]} {e(r['ck'])}</td></tr>")
        H.append("</table></div>\n")
        nd = ", ".join(f"{num(s.get('payed') or s.get('price') or 0)} ₽ ({e(self.name(s['userId']))}, продан {ddmm(s.get('sellDate') or '')})"
                       for s in sorted(self.nodeb, key=lambda s: (s.get("sellDate") or "", s["id"])))
        H.append(f'<p class="muted">{self.mes_i.capitalize()} — абонементы в группы с {ddmm(self.m1)} и абонементы на несколько занятий, '
                 f"купленные с {ddmm(self.start_sales)}. Разовые занятия и праздники не считаю. «С баланса» — абонемент оплачен с баланса "
                 "ребёнка, деньги пришли раньше (показан приход, из которого по порядку баланса оплачено списание; ✓ — если сошлись "
                 "все такие приходы). «Второй терминал» — у центра есть терминал, которого нет в выдаче "
                 "банка по операциям: такая оплата сходится с зачислением за день до рубля."
                 + (f" Не считаю {len(self.nodeb)} абонемент(а) без списания — оплачены в МойКлассе, но в выгрузке платежей нет ни одного "
                    f"списания на них (удалили, переоформили или оплата не выгрузилась): {nd}." if self.nodeb else "") + "</p>\n")
        # 2. по дням
        H.append('<h2 id="dni">2. По дням: МойКласс ↔ Т-Банк ↔ тетрадь ↔ чеки, ₽</h2>\n<div class="tw"><table><tr><th>День</th><th>МК картой</th>'
                 "<th>Терминал, по операциям</th><th>Зачислено за терминал</th><th>МК безнал</th><th>СБП</th><th>Интернет-эквайринг, до комиссии</th>"
                 "<th>МК наличные</th><th>Тетрадь, приход</th><th>Чеки</th></tr>\n")
        ct, ck = self.cov.get("terminal", set()), self.cov.get("komtet", set())
        for d in days(self.start_sales, self.today):
            ps = [p for p in self.inc if p["d"] == d]
            s_t = sum(p["summa"] for p in ps if p.get("paymentTypeId") == T_TERM)
            s_o = sum(p["summa"] for p in ps if p.get("paymentTypeId") == T_ONLINE)
            s_n = sum(p["summa"] for p in ps if p.get("paymentTypeId") == T_NAL)
            sbp = sum(o["amount"] for o in self.sbp if o["t"] and o["t"].date().isoformat() == d)
            tt = self.term_total(d, self.term_our)
            td = self.tetrad_day(d)
            tp = sum(x.get("summa") or 0 for x in (td or {}).get("prihody") or []) if td else None
            need = [p for p in ps if p.get("paymentTypeId") in (T_NAL, T_ONLINE, T_TERM)]
            chk = f"{sum(1 for p in need if p['id'] in self.chek)} из {len(need)}" if d in ck else "—"
            cell = lambda v: "—" if v is None else num(v)
            H.append(f"<tr><td>{ddmm(d)}</td><td class='n'>{num(s_t)}</td><td class='n'>{cell(tt)}</td>"
                     f"<td class='n'>{cell(self.credit.get(d))}</td><td class='n'>{num(s_o)}</td>"
                     f"<td class='n'>{num(sbp) if d in self.cov.get('bank', set()) else '—'}</td>"
                     f"<td class='n'>{cell(self.reestr.get(d))}</td><td class='n'>{num(s_n)}</td><td class='n'>{cell(tp)}</td><td class='n'>{chk}</td></tr>")
        H.append("</table></div>\n")
        rr = sorted(self.fee_day[d] / self.credit[d] for d in days(self.start_sales, self.today)
                    if self.credit.get(d) and self.fee_day.get(d))
        fee = f"≈ {rr[len(rr) // 2] * 100:.1f} %".replace(".", ",") if rr else "≈ 2 %"
        po = ("Поступлений на счёт от родителей по реквизитам за эти дни не было." if not self.credit_other else
              f"Поступлений на счёт по реквизитам: {len(self.credit_other)} на {rub(sum(o['amount'] for o in self.credit_other))}.")
        tdays = sorted(k for k in self.tetrad if re.fullmatch(r"\d{4}-\d\d-\d\d", k) and isinstance(self.tetrad[k], dict))
        if tdays:
            last = tdays[-1]
            ost = self.tetrad[last].get("ostatok_konec")
            tet = (f"Тетрадь — по фото из чата: перенесены дни {', '.join(ddmm(d) for d in tdays)}"
                   + (f"; остаток на конец {ddmm(last)} — {rub(ost)}" if ost is not None else "") + ".")
        else:
            tet = "Тетрадь: ни одного дня с фото ещё не перенесено."
        razob = set((self.tetrad.get("_skipped_foto") or {}).keys()) if isinstance(self.tetrad.get("_skipped_foto"), dict) else set()
        for v in self.tetrad.values():
            if isinstance(v, dict) and isinstance(v.get("foto"), list):
                razob.update(v["foto"])
        nerazob = [f for f in self.foto if (f.get("ts") or "") >= self.start_sales and f.get("ts") not in razob
                   and (self.na is None or datetime.fromisoformat(f["ts"]) <= self.na)]
        nf = [d for d in sorted(self.no_foto) if d >= self.start_sales]
        if nf:
            tet += f" Нет перенесённой страницы тетради за дни с наличными в МК: {', '.join(f'{ddmm(d)} ({num(self.no_foto[d])} ₽)' for d in nf)}."
        if nerazob:
            tet += (f" В чате {len(nerazob)} фото с {ddmm(nerazob[0]['ts'][:10])} ещё не разобраны (tetrad.json): "
                    "среди них могут быть страницы тетради за эти дни.")
        H.append(f'<p class="muted">Терминал банк зачисляет на следующий день одной суммой, комиссия {fee} — отдельной строкой. '
                 "Интернет-эквайринг (оплата по ссылке) — реестром на следующий день, за вычетом комиссии; в таблице сумма до комиссии. "
                 f"СБП — сразу. {po} {tet}</p>\n")
        # 3. что не сходится
        H.append('<h2 id="problemy">3. Что не сходится и кто разбирается</h2>\n<div class="card dec">\n')
        probs = sorted([x for x in self.problems if x[0] >= self.start_sales], key=lambda x: (x[0], x[1] != "Деньги"))
        if not probs:
            H.append('<p class="ok">Расхождений нет.</p>')
        for d, razdel, txt, kto in probs:
            H.append(f"<p><b>{e(razdel)} · {ddmm(d)}:</b> {e(txt)} <span class='muted'>→ {e(kto)}</span></p>")
        H.append("\n</div>\n")
        need = [p for p in self.inc if p["d"] >= self.start_sales and p["d"] in ck and p.get("paymentTypeId") in (T_NAL, T_ONLINE, T_TERM)]
        noch = [p for p in need if p["id"] not in self.chek]
        if noch:
            g = Counter((p["d"], self.mgr(p) or "онлайн") for p in noch)
            parts = "; ".join(f"{ddmm(d)} — {n} ({e(m)})" for (d, m), n in sorted(g.items()))
            full = sorted({p["d"] for p in need} - {p["d"] for p in noch})
            H.append(f"<p><b>Чеков нет — {len(noch)} оплат на {rub(sum(p['summa'] for p in noch))}:</b> {parts}. Деньги пришли (терминал, СБП, тетрадь), "
                     "но чек родителю не пробит — это нарушение закона о кассах, штраф за каждый случай."
                     + (f" Чек есть у каждой оплаты: {', '.join(ddmm(d) for d in full)}." if full else "") + "</p>\n")
        pre_s = (date.fromisoformat(self.start_sales) - timedelta(2)).isoformat()
        pre = [d for d in sorted(self.cov.get("bank", set()) | ck) if pre_s <= d < self.start_sales]
        if pre:
            pp = [x for x in self.problems if pre_s <= x[0] < self.start_sales]
            pn = [p for p in self.inc if p["d"] in pre and p["d"] in ck and p.get("paymentTypeId") in (T_NAL, T_ONLINE, T_TERM) and p["id"] not in self.chek]
            H.append(f"<p><b>До приёма оплат за {self.mes_i}, {ddmm(pre[0])}–{ddmm(pre[-1])}:</b> "
                     + (" ".join(e(f"{x[2]} → {x[3]}.") for x in pp) if pp else "по деньгам расхождений нет.")
                     + (f" Ещё {len(pn)} оплат без чека." if pn else "") + "</p>\n")
        bur = [c for c in self.cheki_bur if c.get("t") and self.start_sales <= c["t"].date().isoformat() <= self.today]
        if bur:
            H.append(f'<p class="muted">Касса клуба Буракова в Комтете (Люберцы) — не наша: её {len(bur)} чеков на '
                     f"{rub(sum(c.get('amount') or 0 for c in bur))} с {ddmm(self.start_sales)} в сверку не беру; счёт и терминал клуба смотрю только "
                     "как место, куда по ошибке могли уйти наши деньги.</p>\n")
        # 4. как сверяю
        H.append(f'<h2 id="kak">4. Как сверяю каждый день</h2>\n<ol>\n'
                 "<li>Утром за позавчера–сегодня: свежая копия МойКласса, выписка Т-Банка по счетам, операции терминала по одной, чеки Комтета, фото тетради из чата.</li>\n"
                 "<li>Карта: каждая оплата МК ↔ операция терминала (сумма, время, последние 4 цифры карты). СБП — так же по одной. Ссылка на оплату — реестр интернет-эквайринга за день + онлайн-чек.</li>\n"
                 "<li>Наличные: МК ↔ запись в тетради за день ↔ чек Комтет. Нет фото — пункт дежурной в пульт.</li>\n"
                 "<li>Каждое расхождение — пунктом в пульт: чек и тетрадь — дежурной, правка в МойКлассе — Лизе, деньги, которых нет в банке, — вам. Итог — в вечерней сводке.</li>\n"
                 "</ol>\n")
        H.append("<h3>Правила сверки</h3>\n<ul>\n" + "".join(f"<li>{e(x)}</li>\n" for x in PRAVILA) + "</ul>\n")
        H.append(f'<p class="muted">Составлено Клодом {now:%d.%m}, {now:%H:%M} МСК.</p>\n</div></body></html>')
        note = (f"Обновлено {now:%d.%m}, {now:%H:%M}: {I['oplat']} оплат {self.mes_r}, расхождений по деньгам {I['rashozhdeniy']}, "
                f"без чека {I['bez_cheka']}")
        return "".join(H), note


HEAD = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{TITLE}</title>
<style>
:root{--ink:#1d1a3d;--muted:#5f5b80;--line:#e2e0ef;--soft:#f4f3fa;--bg:#fbfbfe;--ind:#312783;--blue:#1592c7;--green:#5f9a17;--amber:#c97f00;--red:#c4050f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.w{max-width:980px;margin:0 auto;padding:22px max(16px,env(safe-area-inset-left)) 60px}
h1{font-size:26px;color:var(--ind);margin:0 0 4px;text-wrap:balance}.sub{color:var(--muted);margin:0 0 18px}.muted{color:var(--muted)}
h2{font-size:19px;color:var(--ind);margin:30px 0 10px;padding-top:14px;border-top:2px solid var(--line);text-wrap:balance}
h3{font-size:15px;margin:16px 0 6px}
p{margin:0 0 9px}ul,ol{margin:4px 0 10px;padding-left:20px}li{margin:3px 0}
a{color:var(--blue)}
.toc{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 6px}.toc a{font-size:13px;text-decoration:none;border:1px solid var(--line);background:#fff;border-radius:999px;padding:4px 10px;color:var(--ind)}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:10px 0}
.dec{border-left:4px solid var(--amber)}
.ok{color:var(--green);font-weight:600}.wait{color:var(--amber);font-weight:600}.bad{color:var(--red);font-weight:600}
.kp{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:8px 0 12px}
.kp div{background:#fff;border:1px solid var(--line);border-radius:10px;padding:9px 11px}.kp b{display:block;font-size:22px;font-variant-numeric:tabular-nums}.kp span{font-size:12.5px;color:var(--muted)}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px;background:#fff}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{background:var(--soft);font-weight:600}
td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.q{background:var(--soft);border-radius:10px;padding:10px 12px;white-space:pre-wrap;font-size:14px}
.tag{display:inline-block;font-size:12px;border-radius:999px;padding:1px 8px;margin-left:6px;vertical-align:1px}
.t-ok{background:#e8f4dc;color:#3f6c0c}.t-w{background:#fdf0d9;color:#8a5900}.t-b{background:#fde3e4;color:#9b0309}
</style></head><body><div class="w">
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Страница сверки оплат месяца")
    ap.add_argument("--post", action="store_true", help="выложить в /boris/docs/" + SLUG)
    ap.add_argument("--month", default="", help="YYYY-MM (по умолчанию — текущий месяц)")
    ap.add_argument("--prodazhi-s", default="", help="с какого дня принимаются оплаты месяца (по умолчанию за 8 дней до 1-го)")
    ap.add_argument("--out", default="", help="куда записать HTML (по умолчанию <папка>/page_new.html)")
    ap.add_argument("--na", default="", help="собрать «как на момент» YYYY-MM-DDTHH:MM (МСК), без выкладки")
    a = ap.parse_args(argv)
    na = datetime.fromisoformat(a.na).replace(tzinfo=MSK) if a.na else None
    month = a.month or (na or now_msk()).strftime("%Y-%m")
    start = a.prodazhi_s or (date.fromisoformat(month + "-01") - timedelta(8)).isoformat()
    S = Sverka(month, start, na)
    S.run()
    page, note = S.html()
    out = Path(a.out) if a.out else sverka_dir() / "page_new.html"
    out.write_text(page, encoding="utf-8")
    save_json(p_snap() / ("itogi_na.json" if na else "itogi.json"),
              {"at": S.now.isoformat(timespec="seconds"), "month": month, "itogi": S.I, "note": note,
               "rows": [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()} for r in S.rows],
               "problems": S.problems}, indent=1)
    print(f"{out}: {len(page)} байт")
    print(note)
    print("Итоги:", json.dumps(S.I, ensure_ascii=False))
    if a.post:
        if na:
            raise SystemExit("--post вместе с --na не выкладываю")
        title = f"Сверка оплат {S.mes_r}: МойКласс ↔ Т-Банк ↔ Комтет ↔ тетрадь"
        # owner() возвращает ответ только при HTTP 200, иначе — исключение
        r = owner("POST", "/api/boris-docs", payload={"slug": SLUG, "title": title, "html": page, "note": note, "order": 100})
        print("Выложено (HTTP 200):", json.dumps(r, ensure_ascii=False)[:300])
        got = owner("GET", f"/boris/docs/{SLUG}", raw=True)
        if got != page:
            print(f"ОШИБКА: GET /boris/docs/{SLUG} отдаёт не то, что выложено ({len(got)} против {len(page)} символов)")
            return 1
        print(f"Проверено: GET /boris/docs/{SLUG} (HTTP 200) отдаёт новую версию, {len(got.encode('utf-8'))} байт")
    return 0


if __name__ == "__main__":
    sys.exit(main())
