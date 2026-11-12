# -*- coding: utf-8 -*-
"""Страница «Проверка МойКласса» для Лизы: /proverka-mk.

12.11.2026, Борис сказал «да» правилу (2): правки Лизы в МойКлассе перестают
приходить в пульт отдельными пунктами. Вместо них — одна страница, которая сама
собирается из данных МойКласса. Лиза исправила в МойКлассе — строка пропала при
следующей проверке, галочек нет. Проект и проверка скептика — в рабочих файлах
pravila1211 (stranica_lizy.md, stranica_lizy_proverka.md); правила разделов ниже —
с поправками скептика, где они были.

Разделы (по порядку важности):
  1  прошло — не закрыто              7  ложный долг: лишний/висящий абонемент или счёт
  2  «проведено», а занятия не было   8  записи не по факту
  3  явка ≠ отчёт педагога            9  камера ≠ МойКласс
  4  был — ничего не списано          10 правка без объяснения (сверка честности)
  5  деньги в МойКлассе ≠ банк        11 карточки и статусы
  6  деньги на балансе — абонемента нет  12 расписание

Откуда данные. Страница читает ТОЛЬКО локальную базу сервера и готовые файлы проверок
(контроль оплат, сверка честности, сверка явки, кадры камер); к МойКлассу она не
обращается и ничего в него не пишет. Свежесть даёт синк: лёгкий раз в 5 минут,
средний раз в 30 минут и по кнопке «Перепроверить сейчас» (sync.sredniy_sync),
полный в 07:00. Удалённое в МойКлассе синк помечает в mk_udaleno — такие строки
(«призраки») на странице не показываются. Ещё один источник удалений — подтверждённые
ответом 404 удаления сверки честности (sverka_chestnosti_udaleno), но только абонементов
и платежей: для записей на занятия ответ 404 бывает и у живой записи.

Сверка строк на копии базы 12.11 (что оказалось ложным и как поправлено) — в комментариях
у r1, r3, r10 и в тесте test_sverka_1211.

Строки живут в таблице proverka_mk_stroki (ключ, раздел, впервые, последний раз,
закрыта, заметка): по ней считаются «новых и закрыто за сутки», возраст строки и
сводка Бориса. «Не ошибка» с обязательной причиной — только там, где правило —
догадка (2а, 3, 8в, 9, 11в): строка скрывается на 30 дней или до изменения данных.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import db

log = logging.getLogger("kidsup.proverka_mk")

MK_USER = "https://app.moyklass.com/user/{}"
MK_CLASS = "https://app.moyklass.com/class/{}"

# (номер, название, про деньги)
RAZDELY = [
    ("1", "Прошло — не закрыто", False),
    ("2", "«Проведено», а занятия не было", True),
    ("3", "Явка не сходится с отчётом педагога", False),
    ("4", "Был на занятии — ничего не списано", True),
    ("5", "Деньги в МойКлассе не сходятся с банком", True),
    ("6", "Деньги на балансе — абонемента нет", True),
    ("7", "Ложный долг: лишний или висящий абонемент или счёт", True),
    ("8", "Записи не по факту", False),
    ("9", "Камера не сходится с МойКлассом", False),
    ("10", "Правка без объяснения", False),
    ("11", "Карточки и статусы", False),
    ("12", "Расписание", False),
]
DENGI = {n for n, _, d in RAZDELY if d}
# Разделы, где правило — догадка: только тут есть «не ошибка» (с причиной).
# 2а — поправка скептика: «было ли занятие» по данным не понять, нужна кнопка
# «не ошибка — дети просто не пришли».
NE_OSHIBKA = {"2а", "3", "8в", "9", "11в"}
NE_OSHIBKA_DNEY = 30
HVOST_DNEY = 14                    # строки старше — свёрнуты «хвост: N»
OKNO_PREDEL = "2026-09-01"         # окно не уходит раньше начала сезона
SEZON_OT = "2026-08-25"            # абонементы сезона — проданные с 25.08
RASPISANIE_DO = "2027-05-31"
KESH_MINUT = 10
R1_DEN = 5                         # столько незакрытых занятий за день — уже «день не закрыт», одна строка
# Разделы и подразделы, которые страница пока НЕ показывает и не считает в «осталось N»
# (проверка скептика 12.11 вечером): 5 — на настоящих файлах контроля оплат не прогонялся
# ни разу (его пункты поэтому идут Лизе в пульт, как раньше, см. kontrol_oplat);
# 11г — 2 строки из 5 были про запись в группу, которой в МойКлассе уже нет (ждёт, пока
# заработает пометка удалённых записей в группы). Включить без выкладки — настройка
# proverka_mk_vyklyucheny (номера через запятую; пустая строка «-» — показывать всё).
VYKLYUCHENY_UMOLCH = "5,11г"
# Оценка кадра сама говорит, что это не пропуск (группа в другой комнате, перенос, пришёл
# в другое время) — раздел 9 такую строку не показывает (2 ложных из 4, проверка 12.11).
_KADR_NE_PROPUSK = re.compile(r"не\s+пропуск|перенесл|перенос|в\s+друг\w+\s+(комнат|кабинет|зал)|занима\w+\s+в\s+К\d", re.I)

# статусы карточек
NOVYI_LID, NET_OTVETA, DUMAET, ZAPISALSYA, POSETIL = 125951, 345768, 146950, 125952, 125953
KLIENT, OTKAZ, NE_PISAT, ARHIV, DUMAET_POSLE = 125955, 125957, 146328, 345759, 345767
ZHIVYE = {NOVYI_LID, NET_OTVETA, DUMAET, ZAPISALSYA}
POSLE_RAZGOVORA = {DUMAET, ZAPISALSYA, POSETIL, KLIENT, DUMAET_POSLE}
# статусы записи в группу
J_UCHITSYA, J_VYBYL, J_OTRABOTKA, J_NOVAYA, J_PROBNOE, J_POSETIL_PROBNOE, J_PODTVERDIL = 2, 4, 5, 50509, 58132, 58131, 83760
J_AKTIVNYE = {J_UCHITSYA, J_OTRABOTKA, J_PROBNOE, J_POSETIL_PROBNOE, J_PODTVERDIL}
# типы платежей
T_ONLINE, T_TERM, T_PEREVOD, T_VOZVRAT = 2, 64725, 63934, 97845

DNI = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MSG_V_IMENI = re.compile(r"\((телеграмм?|telegram|tg|max|макс|whats?app|ватсап|вайбер)\)", re.I)
VOZRAST_V_IMENI = re.compile(r"\b\d{1,2}\s*(лет|год|года|мес)\b")
NOMER_V_IMENI = re.compile(r"^[\d\s()+\-]{10,}$")
SLUZH_IMYA = re.compile(r"^(заявка|звонок от|лид|без имени|тест)\b", re.I)

_lock = threading.Lock()


# ── мелочи ────────────────────────────────────────────────────────────────

def _j(s):
    try:
        return json.loads(s) if isinstance(s, str) else (s or {})
    except ValueError:
        return {}


def _spisok(s) -> list:
    try:
        v = json.loads(s) if isinstance(s, str) and s else []
    except ValueError:
        v = []
    return v if isinstance(v, list) else []


def _num(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _now() -> datetime:
    from . import autopilot
    return autopilot._now()


def rub(x) -> str:
    x = _num(x)
    if abs(x - round(x)) < 0.005:
        return f"{round(x):,} ₽".replace(",", " ")
    return f"{x:,.2f}".replace(",", " ").replace(".", ",").rstrip("0").rstrip(",") + " ₽"


def _dm(d: str) -> str:
    return f"{d[8:10]}.{d[5:7]}" if d and len(d) >= 10 else (d or "")


def _kogda(d: str, t: str = "") -> str:
    try:
        wd = DNI[date.fromisoformat(d[:10]).weekday()] + " "
    except ValueError:
        wd = ""
    return f"{wd}{_dm(d)}" + (f" {t[:5]}" if t else "")


def _bill(r: dict) -> float:
    b = r.get("bill")
    return _num((b or {}).get("summa")) if isinstance(b, dict) else _num(b)


def _otpechatok(fakty) -> str:
    return hashlib.sha1(json.dumps(fakty, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]


def pervoe_proshlogo_mesyaca(d: date) -> str:
    return (d.replace(day=1) - timedelta(days=1)).replace(day=1).isoformat()


def vyklyucheny() -> set[str]:
    """Номера выключенных разделов и подразделов («5», «11г»)."""
    try:
        v = db.get_setting("proverka_mk_vyklyucheny", VYKLYUCHENY_UMOLCH)
    except Exception:  # noqa: BLE001
        v = VYKLYUCHENY_UMOLCH
    return {x.strip().lower() for x in str(v or "").replace(";", ",").split(",") if x.strip() and x.strip() != "-"}


def _init(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS proverka_mk_stroki (
        key TEXT PRIMARY KEY, razdel TEXT, pervyi TEXT, posledniy TEXT, zakryta TEXT,
        zametka TEXT, dannye TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS proverka_mk_ne_oshibka (
        key TEXT PRIMARY KEY, razdel TEXT, prichina TEXT, kto TEXT, ts TEXT, do TEXT,
        otpechatok TEXT, tekst TEXT)""")


# ── данные одним заходом ──────────────────────────────────────────────────

class Dannye:
    """Всё нужное разделам из локальной базы: без удалённого в МойКлассе."""

    def __init__(self, seychas: datetime, otkrytye: dict[str, dict] | None = None):
        self.seychas = seychas
        self.segodnya = seychas.date()
        self.today = self.segodnya.isoformat()
        self.hm = seychas.strftime("%H:%M")
        self.m1 = pervoe_proshlogo_mesyaca(self.segodnya)
        self.otkrytye = otkrytye or {}
        with db.get_conn() as conn:
            self.ud = {t: db.udalennye(t, conn) for t in
                       ("lessons", "user_subscriptions", "invoices", "users", "joins", "lesson_records", "payments")}
            # Удаления, подтверждённые сверкой честности (404 по id), берём только для
            # абонементов и платежей. Для записей на занятия им верить нельзя (сверка 12.11):
            # GET /lessonRecords/{id} отвечает 404 и на живую запись непроведённого занятия —
            # 10 из 113 таких «удалённых» записей были живы, и раздел 1 терял 6 строк из 8.
            # Сама таблица lesson_records чистится синком: занятие приходит со всем списком
            # записей, чего в списке нет — стирается (db.save_lessons).
            izv = _j(db.get_state("sverka_chestnosti_udaleno") or "{}")
            for k in (izv if isinstance(izv, dict) else {}):
                tbl, _, rid = str(k).partition(":")
                if tbl in ("user_subscriptions", "payments") and rid.isdigit():
                    self.ud[tbl].add(int(rid))
            self.classes = {}
            for r in conn.execute("SELECT id, name, status FROM classes"):
                self.classes[r["id"]] = {"name": r["name"] or "", "status": r["status"] or ""}
            self.users = {}
            for r in conn.execute("SELECT id, name, phone, balance, client_state_id, created_at, raw FROM users"):
                if r["id"] in self.ud["users"]:
                    continue
                raw = _j(r["raw"])
                self.users[r["id"]] = {"name": (r["name"] or "").strip(), "phone": r["phone"] or "",
                                       "balans": _num(r["balance"]), "state": r["client_state_id"],
                                       "created": str(r["created_at"] or "")[:10],
                                       "attrs": {a.get("attributeAlias"): a.get("value") for a in (raw.get("attributes") or [])}}
            self.lessons = {}
            for r in conn.execute("SELECT id, date, begin_time, end_time, class_id, status, raw FROM lessons "
                                  "WHERE date >= ?", ("2026-08-01",)):
                if r["id"] in self.ud["lessons"]:
                    continue
                raw = _j(r["raw"])
                self.lessons[r["id"]] = {"id": r["id"], "date": r["date"] or "", "begin": (r["begin_time"] or "")[:5],
                                         "end": (r["end_time"] or "")[:5], "cid": r["class_id"],
                                         "status": r["status"], "teachers": raw.get("teacherIds") or [],
                                         "room": raw.get("roomId")}
            self.recs = []
            for r in conn.execute("SELECT lr.id, lr.lesson_id, lr.user_id, lr.visit, lr.raw FROM lesson_records lr "
                                  "JOIN lessons l ON l.id = lr.lesson_id WHERE l.date >= ?", ("2026-08-01",)):
                if r["lesson_id"] not in self.lessons or r["id"] in self.ud["lesson_records"]:
                    continue
                raw = _j(r["raw"])
                raw.update({"id": r["id"], "lessonId": r["lesson_id"], "userId": r["user_id"],
                            "visit": bool(raw.get("visit", r["visit"]))})
                self.recs.append(raw)
            self.subs = []
            self.udal_abon_uid = set()        # у кого удаляли абонемент (для механики раздела 10)
            for r in conn.execute("SELECT id, user_id, raw FROM user_subscriptions"):
                if r["id"] in self.ud["user_subscriptions"]:
                    self.udal_abon_uid.add(r["user_id"])
                    continue
                s = _j(r["raw"])
                s["id"] = r["id"]
                self.subs.append(s)
            self.joins = [dict(r) for r in conn.execute("SELECT id, user_id, class_id, status_id FROM joins")
                          if r["id"] not in self.ud["joins"] and r["user_id"] not in self.ud["users"]]
            self.pays = []
            for r in conn.execute("SELECT id, user_id, date, summa, optype, raw FROM payments WHERE date >= ?",
                                  ("2026-05-01",)):
                if r["id"] in self.ud["payments"]:
                    continue
                p = _j(r["raw"])
                p.update({"id": r["id"], "userId": r["user_id"], "date": str(r["date"] or "")[:10],
                          "summa": _num(r["summa"]), "optype": r["optype"]})
                self.pays.append(p)
            # возвраты — за всё время: 7г ищет и летние абонементы прошлого сезона (п. 3767)
            self.vozvraty = defaultdict(list)
            for r in conn.execute("SELECT id, user_id, date, summa, raw FROM payments WHERE optype='refund'"):
                if r["id"] in self.ud["payments"]:
                    continue
                p = _j(r["raw"])
                p.update({"id": r["id"], "userId": r["user_id"], "date": str(r["date"] or "")[:10], "summa": _num(r["summa"])})
                self.vozvraty[r["user_id"]].append(p)
            self.invoices = [dict(r) for r in conn.execute(
                "SELECT id, user_id, price, payed, created_at FROM invoices WHERE price > 0 AND price <= 1.0001 "
                "AND COALESCE(payed, 0) < price") if r["id"] not in self.ud["invoices"]]
            try:
                self.comments = [dict(r) for r in conn.execute(
                    "SELECT user_id, ts, manager_id, text FROM crm_comments WHERE ts >= ?", ("2026-08-15",))]
                # Сверка 12.11: 7 из 10 комментариев в карточках пишет автоматика (журнал
                # звонков и переписок, от имени API — без manager_id). Такой комментарий —
                # не объяснение админа: строку раздела 10 он закрывать не должен.
                for c in self.comments:
                    c["chelovek"] = bool(c.get("manager_id")) and not _robot(c.get("text"))
            except Exception:  # noqa: BLE001 — кэша комментариев ещё нет
                self.comments = []
            try:
                self.kadry = [dict(r) for r in conn.execute(
                    "SELECT key, lesson_id, day, ts, room, class_name, begin_time, deti, vzroslye, ocenka FROM kadry "
                    "WHERE day >= ? AND deti IS NOT NULL", ((self.segodnya - timedelta(days=7)).isoformat(),))]
            except Exception:  # noqa: BLE001
                self.kadry = []
            try:
                lo = min(self.m1, self._lo_otkrytyh(("3",)))
                self.yavka = {r["day"]: _j(r["data"]) for r in conn.execute(
                    "SELECT day, data FROM yavka_sverka WHERE day BETWEEN ? AND ?", (lo, self.today))}
            except Exception:  # noqa: BLE001
                self.yavka = {}
        self._indeksy()

    def _lo_otkrytyh(self, pods) -> str:
        """Самая ранняя дата открытой строки этих подразделов (окно не убирает строку само)."""
        ds = [v.get("data") or "" for k, v in self.otkrytye.items() if v.get("razdel") in pods and v.get("data")]
        return max(OKNO_PREDEL, min(ds)) if ds else self.m1

    def lo(self, *pods) -> str:
        return min(self.m1, self._lo_otkrytyh(pods))

    def _indeksy(self) -> None:
        self.rbl = defaultdict(list)
        self.rec_by_id = {}
        self.recs_by_user = defaultdict(list)
        self.linked = set()
        for r in self.recs:
            self.rbl[r["lessonId"]].append(r)
            self.rec_by_id[r["id"]] = r
            self.recs_by_user[r["userId"]].append(r)
            if r.get("missedLessonRecordId"):
                self.linked.add(r["missedLessonRecordId"])
        self.subs_by_user = defaultdict(list)
        for s in self.subs:
            self.subs_by_user[s.get("userId")].append(s)
        self.jst = {(j["user_id"], j["class_id"]): j["status_id"] for j in self.joins}
        self.active_join = Counter(j["class_id"] for j in self.joins if j["status_id"] in J_AKTIVNYE)
        self.pays_by_user = defaultdict(list)
        self.pays_by_day = defaultdict(list)
        self.pay_by_id = {}
        for p in self.pays:
            self.pays_by_user[p["userId"]].append(p)
            self.pays_by_day[p["date"]].append(p)
            self.pay_by_id[p["id"]] = p
        self.comm_by_user = defaultdict(list)
        for c in self.comments:
            self.comm_by_user[c["user_id"]].append(c)
        self.lessons_by_class = defaultdict(list)
        for l in self.lessons.values():
            self.lessons_by_class[l["cid"]].append(l)

    # имена и признаки
    def cname(self, cid) -> str:
        return (self.classes.get(cid) or {}).get("name") or ""

    def gruppa(self, cid) -> str:
        n = self.cname(cid)
        return n[5:] if n.startswith("2627_") else n

    def is27(self, cid) -> bool:
        n = self.cname(cid)
        return n.startswith("2627") and "аявк" not in n

    def fam(self, cid) -> str:
        p = self.cname(cid).split("_")
        return p[1] if len(p) > 1 else ""

    def imya(self, uid) -> str:
        return (self.users.get(uid) or {}).get("name") or f"№{uid}"

    def nachalos(self, l) -> bool:
        return l["date"] < self.today or (l["date"] == self.today and (l["begin"] or "99") <= self.hm)

    def konchilos(self, l) -> bool:
        return l["date"] < self.today or (l["date"] == self.today and (l["end"] or "99") <= self.hm)

    def dd(self, n: int) -> str:
        return (self.segodnya + timedelta(days=n)).isoformat()

    def obnovitsya(self, vid: str, d: str = "") -> str:
        """Когда строка уйдёт сама после правки в МойКлассе (поправка скептика: писать на строке)."""
        if vid == "zanyatie":
            if d and self.dd(-1) <= d <= self.dd(14):
                return "уйдёт сама через 5–10 минут после правки"
            if d and d >= self.m1:
                return "уйдёт сама в течение получаса после правки"
            return "уйдёт завтра утром после правки"
        if vid == "abonement":
            if d and d >= self.dd(-3):
                return "уйдёт сама через 5–10 минут после правки"
            if d and d >= SEZON_OT:
                return "уйдёт сама в течение получаса после правки"
            return "уйдёт завтра утром после правки"
        if vid == "platezh":
            return ("уйдёт сама через 5–10 минут после правки" if d and d >= self.dd(-3)
                    else "уйдёт завтра утром после правки")
        if vid in ("kartochka", "zapis"):
            return "уйдёт сама через 5–10 минут после правки (удалённая карточка — завтра утром)"
        if vid == "kommentariy":
            return "уйдёт сама в течение получаса после комментария в карточке"
        if vid == "schet":
            return "уйдёт завтра утром после правки"
        if vid == "noch":
            return "уйдёт после ночной проверки расписания"
        return ""


def _robot(text) -> bool:
    try:
        from . import voronka
        return voronka._robot(text)
    except Exception:  # noqa: BLE001
        return str(text or "").lstrip().startswith(("Клод", "🤖", "Авто", "📞"))


def _stroka(pod: str, key: str, **kw) -> dict:
    r = {"razdel": re.match(r"\d+", pod).group(), "pod": pod, "key": key, "rebenok": "", "uid": None,
         "gruppa": "", "class_id": None, "data": "", "kogda": "", "chto": "", "sdelat": "", "ssylka": "",
         "ssylka_tekst": "", "obnovitsya": "", "dogadka": pod in NE_OSHIBKA, "seraya": "", "fakty": {}}
    r.update(kw)
    if not r["ssylka"]:
        if r.get("uid"):
            r["ssylka"], r["ssylka_tekst"] = MK_USER.format(r["uid"]), "карточка в МойКлассе"
        elif r.get("class_id"):
            r["ssylka"], r["ssylka_tekst"] = MK_CLASS.format(r["class_id"]), "группа в МойКлассе"
    return r


# ── Р1. Прошло — не закрыто ───────────────────────────────────────────────

def r1(D: Dannye) -> list[dict]:
    """Занятие прошло, записи есть, а оно не «проведено».

    Сверка 12.11: занятия закрывают люди, обычно на следующий день, а то и через день
    (вечером 11.11 не были закрыты 17 занятий 10.11 и 23 занятия 11.11). Поштучно это
    40 строк каждый вечер, в которых тонут 8 настоящих забытых. Поэтому: день, где не
    закрыто R1_DEN занятий и больше, — одна строка «день не закрыт»; меньше — по занятию."""
    lo = D.lo("1")
    vecher = D.hm >= "21:30"
    po_dnyam = defaultdict(list)
    # Проверка 12.11: 4 строки из 8 были дублями — в той же группе в тот же день и час есть
    # второе занятие, оно проведено, детям стоит «был» и списано. Совет «провести» дал бы
    # второе списание. Для дубля совет другой: не проводить, записи снять, занятие удалить.
    provedennye = defaultdict(list)
    for l in D.lessons.values():
        if l["status"] == 1:
            provedennye[(l["cid"], l["date"], l["begin"])].append(l)
    out = []
    for l in D.lessons.values():
        if not D.is27(l["cid"]) or l["status"] != 0 or not D.rbl.get(l["id"]):
            continue
        if not (lo <= l["date"] <= D.dd(-1) or (vecher and l["date"] == D.today and D.konchilos(l))):
            continue
        dv = provedennye.get((l["cid"], l["date"], l["begin"]))
        if dv:
            n = len(D.rbl[l["id"]])
            te_zhe = {r["userId"] for r in D.rbl[l["id"]]} & {r["userId"] for x in dv for r in D.rbl.get(x["id"], [])}
            out.append(_stroka("1", f"r1:{l['id']}", gruppa=D.gruppa(l["cid"]), class_id=l["cid"], data=l["date"],
                               kogda=_kogda(l["date"], l["begin"]),
                               chto=(f"дубль занятия: в этот же день и час у группы есть второе занятие, оно проведено"
                                     + (f" (те же дети: {len(te_zhe)})" if te_zhe else "") + f"; на этом записано {n}, оно не закрыто"),
                               sdelat=("НЕ проводить (будет второе списание): снять записи и удалить это занятие"
                                       if len(te_zhe) == n else
                                       "НЕ проводить как есть: детей, которых нет на проведённом занятии, записать туда "
                                       "и отметить явку по факту, а это занятие удалить"),
                               obnovitsya=D.obnovitsya("zanyatie", l["date"]),
                               fakty={"status": 0, "n": n, "dubl": sorted(x["id"] for x in dv)}))
            continue
        po_dnyam[l["date"]].append(l)
    for den, ls in sorted(po_dnyam.items()):
        if len(ls) >= R1_DEN:
            vsego = sum(1 for l in D.lessons.values() if l["date"] == den and D.is27(l["cid"]) and D.rbl.get(l["id"]))
            po = Counter(D.fam(l["cid"]) or D.gruppa(l["cid"]) for l in ls)
            out.append(_stroka(
                "1", f"r1:den:{den}", gruppa="весь день", data=den, kogda=_kogda(den),
                chto=(f"день не закрыт: не проведено {len(ls)} из {vsego} занятий с записями ("
                      + ", ".join(f"{k} {v}" for k, v in po.most_common(6)) + ")"),
                sdelat=("провести занятия дня с явкой по отчётам педагогов; если занятия не было — "
                        "снять записи или удалить занятие"),
                ssylka=f"/yavka?day={den}", ssylka_tekst="явка за день",
                obnovitsya=D.obnovitsya("zanyatie", den),
                fakty={"status": 0, "zanyatiy": len(ls)}))
            continue
        for l in ls:
            n = len(D.rbl[l["id"]])
            out.append(_stroka("1", f"r1:{l['id']}", gruppa=D.gruppa(l["cid"]), class_id=l["cid"], data=l["date"],
                               kogda=_kogda(l["date"], l["begin"]),
                               chto=f"занятие не закрыто, записано {n}",
                               sdelat="провести с явкой; если занятия не было — снять записи или удалить занятие",
                               obnovitsya=D.obnovitsya("zanyatie", l["date"]),
                               fakty={"status": 0, "n": n}))
    return out


# ── Р2. «Проведено», а занятия не было (+ Р12г) ───────────────────────────

def _kadr_lesson(D: Dannye) -> dict:
    out = defaultdict(list)
    for k in D.kadry:
        out[k["lesson_id"]].append(k)
    return out


def r2(D: Dannye) -> list[dict]:
    out = []
    lo = D.lo("2а")
    kadry = _kadr_lesson(D)
    pustye = defaultdict(list)
    for l in D.lessons.values():
        if not D.is27(l["cid"]):
            continue
        rs = D.rbl.get(l["id"], [])
        if not D.nachalos(l):
            # 2в: «проведено» или «был», а начало ещё не наступило
            if l["status"] == 1 or any(r.get("visit") for r in rs):
                out.append(_stroka("2в", f"r2v:{l['id']}", gruppa=D.gruppa(l["cid"]), class_id=l["cid"], data=l["date"],
                                   kogda=_kogda(l["date"], l["begin"]),
                                   chto="«проведено» раньше начала занятия" if l["status"] == 1 else "«был» отмечен раньше начала занятия",
                                   sdelat="вернуть «не проведено» и снять отметки до конца занятия",
                                   obnovitsya=D.obnovitsya("zanyatie", l["date"]),
                                   fakty={"status": l["status"], "byl": sum(1 for r in rs if r.get("visit"))}))
            continue
        if l["status"] != 1 or not D.konchilos(l):
            continue
        # Проверка 12.11 (5 ложных из 12): сегодняшнее и вчерашнее занятие «проведено» без
        # явки — явку ещё вносят (такая строка уходила сама через день), поэтому берём не
        # позже позавчерашнего дня; а если отчёт педагога по занятию есть — занятие было:
        # кто по отчёту «был», тот уже строка раздела 3, остальные действительно не пришли.
        ya = (D.yavka.get(l["date"]) or {}).get("lessons") or []
        yl = next((x for x in ya if x.get("lesson_id") == l["id"]), None)
        s_otchetom = bool(yl and yl.get("reports"))
        if rs and lo <= l["date"] <= D.dd(-2) and not s_otchetom and not any(r.get("visit") for r in rs) \
                and (len(rs) >= 2 or not any(_bill(r) > 0 for r in rs)):
            spisano = sum(_bill(r) for r in rs)
            kd = kadry.get(l["id"]) or []
            kadr = (f"на кадре детей {max(int(k['deti'] or 0) for k in kd)}" if kd else "кадра с камеры нет")
            otchet = "отчёта педагога нет"
            out.append(_stroka(
                "2а", f"r2a:{l['id']}", gruppa=D.gruppa(l["cid"]), class_id=l["cid"], data=l["date"],
                kogda=_kogda(l["date"], l["begin"]),
                chto=(f"«проведено», а все записанные ({len(rs)}) отсутствуют"
                      + (f", списано {rub(spisano)}" if spisano else "") + f" — было ли занятие? ({kadr}, {otchet})"),
                sdelat=("если занятие отменили мы — поставить «не проведено» (списания уйдут) и проверить, что деньги "
                        "вернулись на баланс; если дети просто не пришли — «не ошибка»"),
                obnovitsya=D.obnovitsya("zanyatie", l["date"]),
                fakty={"n": len(rs), "byl": 0, "spisano": spisano, "status": 1}))
        if not rs and l["date"] >= D.dd(-14):
            pustye[l["cid"]].append(l)
    # 2б → поправка скептика: группы без единого ученика — строка на группу в 12г;
    # остальные пустые «проведено» (слоты логопедов и т. п.) — одной сводной строкой
    # до решения Бориса (вопрос 2), это не работа Лизы.
    ostalnye = []
    for cid, ls in pustye.items():
        if D.active_join.get(cid, 0) == 0:
            bud = sum(1 for l in D.lessons_by_class.get(cid, []) if l["date"] >= D.today)
            out.append(_stroka("12г", f"r12g:{cid}", gruppa=D.gruppa(cid), class_id=cid, data=min(l["date"] for l in ls),
                               kogda=f"с {_dm(min(l['date'] for l in ls))}",
                               chto=(f"в группе нет ни одного ученика, а занятия идут и «проводятся»: {len(ls)} за 14 дней"
                                     + (f", впереди ещё {bud}" if bud else "")),
                               sdelat="закрыть группу или удалить будущие занятия",
                               obnovitsya=D.obnovitsya("zanyatie", D.today),
                               fakty={"pustyh": len(ls), "uchenikov": 0}))
        else:
            ostalnye += ls
    if ostalnye:
        po = Counter(D.fam(l["cid"]) or D.gruppa(l["cid"]) for l in ostalnye)
        out.append(_stroka("2б", "r2b:svodka", data=min(l["date"] for l in ostalnye),
                           gruppa="разные группы", kogda=f"{_dm(min(l['date'] for l in ostalnye))}–{_dm(max(l['date'] for l in ostalnye))}",
                           chto=(f"{len(ostalnye)} занятий за 14 дней стоят «проведено» без единой записи: "
                                 + ", ".join(f"{k} {v}" for k, v in po.most_common(5))),
                           sdelat="ждёт решения Бориса: отменять ли такие занятия (от этого зависит зарплата педагогов)",
                           seraya="ждёт решения Бориса", obnovitsya=D.obnovitsya("zanyatie", D.today),
                           fakty={"n": len(ostalnye)}))
    return out


# ── Р3. Явка не сходится с отчётом педагога ───────────────────────────────

_SLUZH_SLOVA = {"был", "была", "было", "были", "нет", "не"}


def _najti_rebenka(D: Dannye, imya: str, cid=None):
    """Карточка по имени из отчёта: имя и фамилия должны найтись (поправка скептика —
    8 из 23 «был без записи» оказались темами занятий, разобранными как имена)."""
    toks = [t for t in re.findall(r"[а-яё]{3,}", (imya or "").lower().replace("ё", "е")) if t not in _SLUZH_SLOVA]
    if len(toks) < 2:
        return None
    hits = [uid for uid, u in D.users.items()
            if all(t in u["name"].lower().replace("ё", "е") for t in toks[:2])]
    if not hits:
        return None
    if cid is not None:
        svoi = [u for u in hits if (u, cid) in D.jst]
        if svoi:
            return svoi[0]
        # не из этой группы — тогда из этого направления (отработка в соседней группе)
        f = D.fam(cid)
        v_napr = [u for u in hits if any(uu == u and D.fam(c) == f for (uu, c) in D.jst)]
        if len(v_napr) == 1:
            return v_napr[0]
    # тёзки, и ни один не из этой группы и направления — чью запись заводить, не понять
    return hits[0] if len(hits) == 1 else None


# Пометка педагога рядом с именем говорит, что ребёнка НЕ было, хотя строка попала в «был»
# (сверка 12.11: «Иванов — не было», «Петрова (заморозка)» разобраны как присутствие —
# 7 из 29 строк «по отчёту был, в МойКлассе не был» и 2 из 13 «был без записи»).
_NE_BYLO = re.compile(r"не\s*был[оаи]?(?![а-яё])|отсутств|заморозк|боле[лею]|больн|не\s+приш|не\s+прид", re.I)


def _byl_ryadom(D: Dannye, uid, day: str, cid, krome_lid=None) -> bool:
    """Ребёнку в этот день уже стоит «был» на другом занятии того же направления.

    Сверка 12.11: логопед ведёт субботу слотами (10:00, 10:50, 11:40…), ребёнок записан
    в один слот, а в отчёте педагога оказался под соседним; то же с переходом между
    группами ПШ. 16 из 27 строк «был без записи» были такими — явка и списание верны."""
    f = D.fam(cid)
    for r in D.recs_by_user.get(uid, []):
        l = D.lessons.get(r["lessonId"])
        if l and l["date"] == day and l["id"] != krome_lid and r.get("visit") and D.fam(l["cid"]) == f:
            return True
    return False


def r3(D: Dannye) -> list[dict]:
    lo = D.lo("3")
    sklejka: dict[str, dict] = {}
    spor_staryi: set = set()          # (ребёнок, группа): отчёты спорили больше 14 дней назад

    def dobavit(key, d, l, **kw):
        if key in sklejka:
            x = sklejka[key]
            if d not in x["_dni"]:
                x["_dni"].append(d)
            return
        sklejka[key] = dict(kw, _dni=[d], _cid=l.get("class_id"), _time=l.get("time") or "")

    # по вчерашний день: сегодняшнюю явку отмечают вечером, до этого расхождение — не ошибка
    for day in sorted(D.yavka):
        if not (lo <= day <= D.dd(-1)):
            continue
        for L in (D.yavka[day] or {}).get("lessons") or []:
            if not L.get("reports"):
                continue
            lid = L.get("lesson_id")
            if lid in D.ud["lessons"] or "аявк" in (L.get("class") or ""):
                continue
            for rec in L.get("records") or []:
                act = rec.get("action")
                if act not in ("rashozhdenie", "conflict_visit"):
                    continue
                cur = D.rec_by_id.get(rec.get("record_id"))
                if cur is None:
                    continue                       # записи уже нет — снята
                if act == "rashozhdenie" and cur.get("visit"):
                    continue                       # «был» уже поставлен
                if act == "rashozhdenie" and (D.lessons.get(lid) or {}).get("status") != 1:
                    continue                       # занятие ещё не проведено: явку ставят при проведении,
                                                   # это строка раздела 1, а не расхождение (сверка 12.11)
                if act == "rashozhdenie" and _NE_BYLO.search(str(rec.get("note") or "")):
                    continue                       # в пометке педагога «не было» — отчёт разобран неверно
                if act == "rashozhdenie" and _byl_ryadom(D, rec.get("uid"), day, L.get("class_id"), lid):
                    continue                       # «был» стоит на соседнем занятии того же направления
                if act == "conflict_visit" and not cur.get("visit"):
                    continue                       # «был» уже снят
                uid = rec.get("uid")
                if uid in D.ud["users"]:
                    continue
                key = f"r3:{act}:{uid}:{L.get('class_id')}"
                dobavit(key, day, L, uid=uid, vid=act,
                        chto=("по отчёту педагога был, в МойКлассе «не был»" if act == "rashozhdenie"
                              else "по отчёту педагога не был, в МойКлассе «был»"
                              + (f" (списано {rub(_bill(cur))})" if _bill(cur) else "")),
                        sdelat=("поставить «был»" if act == "rashozhdenie" else "поставить пропуск («не был»)"),
                        rebenok=D.imya(uid))
            for c in L.get("conflicts") or []:
                # Два отчёта спорят между собой. По данным МойКласса такая строка закрыться
                # не может, поэтому: старше 14 дней не показываем (уточнять у педагога уже
                # поздно), а если по этому ребёнку и группе есть своя строка расхождения —
                # отдельной строки не даём (сверка 12.11: 5 из 8 были такими повторами).
                # Приписка «отчёты расходятся» к своей строке ребёнка нужна и для старых дней
                # (проверка 12.11: без неё оставался голый совет «поставить пропуск»).
                nm = str(c).split(":")[0].strip()
                k_uid = next((r.get("uid") for r in L.get("records") or [] if (r.get("name") or "").strip() == nm), None)
                if day < D.dd(-HVOST_DNEY):
                    if k_uid:
                        spor_staryi.add((k_uid, L.get("class_id")))
                    continue
                dobavit(f"r3k:{L.get('class_id')}:{k_uid or nm.lower()}", day, L, uid=k_uid, vid="conflict",
                        chto="два отчёта расходятся: в одном «был», в другом «не был»",
                        sdelat="уточнить у педагога и поставить явку по факту", rebenok=nm)
            for u in L.get("unmatched") or []:
                if u.get("kind") != "был" or _NE_BYLO.search(str(u.get("note") or "")):
                    continue
                uid = _najti_rebenka(D, u.get("name") or "", L.get("class_id"))
                if not uid:
                    continue
                # запись уже завели — на этом занятии или в той же группе в этот день
                same_day = [l2["id"] for l2 in D.lessons_by_class.get(L.get("class_id"), []) if l2["date"] == day]
                if lid not in same_day:
                    same_day.append(lid)
                if any(r["userId"] == uid for x in same_day for r in D.rbl.get(x, [])):
                    continue
                if _byl_ryadom(D, uid, day, L.get("class_id")):
                    continue
                dobavit(f"r3u:{uid}:{L.get('class_id')}", day, L, uid=uid, vid="unmatched",
                        chto="в отчёте педагога «был», а записи на занятие нет",
                        sdelat="завести запись (отработка или пробное) и отметить «был»", rebenok=D.imya(uid))
    out = []
    for key, x in list(sklejka.items()):
        if x["vid"] != "conflict" or not x.get("uid"):
            continue
        svoya = next((sklejka[k] for k in (f"r3:rashozhdenie:{x['uid']}:{x['_cid']}", f"r3:conflict_visit:{x['uid']}:{x['_cid']}")
                      if k in sklejka), None)
        if svoya:
            if "отчёты педагогов расходятся" not in svoya["chto"]:
                svoya["chto"] += "; отчёты педагогов расходятся между собой — уточнить у педагога"
            del sklejka[key]
    for uid, cid in spor_staryi:
        for k in (f"r3:rashozhdenie:{uid}:{cid}", f"r3:conflict_visit:{uid}:{cid}"):
            if k in sklejka and "отчёты педагогов расходятся" not in sklejka[k]["chto"]:
                sklejka[k]["chto"] += "; отчёты педагогов расходятся между собой — уточнить у педагога"
                sklejka[k]["sdelat"] = "сначала уточнить у педагога, потом поставить явку по факту"
    for key, x in sklejka.items():
        dni = sorted(x["_dni"])
        cid = x["_cid"]
        out.append(_stroka("3", key, rebenok=x.get("rebenok") or "", uid=x.get("uid"), gruppa=D.gruppa(cid),
                           class_id=cid, data=dni[0],
                           kogda=", ".join(_kogda(d) for d in dni[:4]) + (f" и ещё {len(dni) - 4}" if len(dni) > 4 else "")
                                 + (f" {x['_time']}" if x["_time"] and len(dni) == 1 else ""),
                           chto=x["chto"], sdelat=x["sdelat"], obnovitsya=D.obnovitsya("zanyatie", dni[-1]),
                           fakty={"vid": x["vid"], "dni": dni}))
    return out


# ── Р4. Был на занятии — ничего не списано ────────────────────────────────

def _fams_sub(D: Dannye, s: dict) -> set:
    f = {D.fam(c) for c in (s.get("classIds") or [])}
    if s.get("mainClassId"):
        f.add(D.fam(s.get("mainClassId")))
    return {x for x in f if x}


def _prichina_4a(D: Dannye, uid, f: str, d0: str, d_posl: str = ""):
    """Одна из четырёх причин (поправка скептика) и что сделать.

    Проверка 12.11: отработать можно только пропуск, который уже случился, — пропуск ищем
    строго раньше последнего посещения без списания (было: посещение 30.10 сверх абонемента,
    а совет — «отметить как отработку пропуска 05.11»)."""
    d_posl = d_posl or d0
    subs_f = [s for s in D.subs_by_user.get(uid, []) if f in _fams_sub(D, s)]
    paid_f = [s for s in subs_f if _num(s.get("payed")) > 0]
    svoi = [r for r in D.recs_by_user.get(uid, []) if D.fam(D.lessons[r["lessonId"]]["cid"]) == f]
    pervoe = min((D.lessons[r["lessonId"]]["date"] for r in svoi), default=d0)
    if not paid_f and pervoe >= d0:
        return "пробное не отмечено «пробным»", "поставить пометку «пробное» на первом занятии"
    for r in svoi:
        l = D.lessons[r["lessonId"]]
        if not r.get("visit") and l["status"] == 1 and _bill(r) > 0 and r["id"] not in D.linked:
            try:
                if l["date"] < d_posl and (date.fromisoformat(d0) - date.fromisoformat(l["date"])).days <= 30:
                    return (f"не отмечена отработка (пропуск {_dm(l['date'])} со списанием)",
                            f"отметить посещение как отработку пропуска {_dm(l['date'])}")
            except ValueError:
                pass
    if paid_f:
        return "абонемент кончился — ходил сверх него", "продлить абонемент или оформить разовое (ссылку на оплату — как обычно)"
    return "абонемента нет", "оформить абонемент или разовое (ссылку на оплату — как обычно)"


def _kommentariy_ob_otrabotke(D: Dannye, uid, d: str) -> bool:
    """Объяснение в карточке: упомянута дата занятия или «отработ» (поправка скептика:
    комментарий может быть написан и до занятия)."""
    try:
        d0 = date.fromisoformat(d)
    except ValueError:
        return False
    lo, hi = (d0 - timedelta(days=45)).isoformat(), D.today + "T99"
    dm = _dm(d)
    for c in D.comm_by_user.get(uid, []):
        if not (lo <= str(c.get("ts") or "") <= hi):
            continue
        t = (c.get("text") or "").lower()
        # дата занятия считается объяснением только в комментарии человека: журнал звонков
        # автоматики сам начинается с даты («09.10 13:55, исходящий…») — сверка 12.11
        if "отработ" in t or (c.get("chelovek") and re.search(r"(?<!\d)" + re.escape(dm) + r"(?!\d)", t)):
            return True
    return False


def r4(D: Dannye) -> list[dict]:
    out = []
    lo = D.lo("4а", "4б")
    r4a = defaultdict(list)
    for r in D.recs:
        l = D.lessons[r["lessonId"]]
        if not D.is27(l["cid"]) or l["date"] < lo or not D.konchilos(l) or l["status"] != 1:
            continue
        if not r.get("visit") or r.get("test"):
            continue
        uid = r["userId"]
        if uid in D.ud["users"]:
            continue
        if r.get("free"):
            if r.get("missedLessonRecordId"):
                continue
            f0 = D.fam(l["cid"])
            d0 = date.fromisoformat(l["date"])
            # поправка скептика: рядом пропуск со списанием без привязанной отработки —
            # «бесплатно» поставили вместо отработки, деньги верные; не показываем
            para = False
            for r2 in D.recs_by_user.get(uid, []):
                l2 = D.lessons[r2["lessonId"]]
                if (D.fam(l2["cid"]) == f0 and not r2.get("visit") and l2["status"] == 1 and _bill(r2) > 0
                        and r2["id"] not in D.linked and abs((date.fromisoformat(l2["date"]) - d0).days) <= 30):
                    para = True
                    break
            if para or _kommentariy_ob_otrabotke(D, uid, l["date"]):
                continue
            out.append(_stroka("4б", f"r4b:{r['id']}", rebenok=D.imya(uid), uid=uid, gruppa=D.gruppa(l["cid"]),
                               class_id=l["cid"], data=l["date"], kogda=_kogda(l["date"], l["begin"]),
                               chto="«бесплатно», не пробное и не отработка",
                               sdelat=f"привязать как отработку пропуска или одной строкой в карточке: «{_dm(l['date'])} бесплатно — потому что …»",
                               obnovitsya=D.obnovitsya("kommentariy"),
                               fakty={"free": True, "mlr": None}))
        elif not r.get("bill"):
            r4a[(uid, D.fam(l["cid"]))].append((l, r))
    for (uid, f), xs in r4a.items():
        xs.sort(key=lambda x: x[0]["date"])
        d0 = xs[0][0]["date"]
        pochemu, sdelat = _prichina_4a(D, uid, f, d0, xs[-1][0]["date"])
        gr = sorted({D.gruppa(l["cid"]) for l, _ in xs})
        out.append(_stroka("4а", f"r4a:{uid}:{f}", rebenok=D.imya(uid), uid=uid, gruppa=", ".join(gr)[:80],
                           data=d0, kogda=", ".join(_dm(l["date"]) for l, _ in xs[:5]) + (f" и ещё {len(xs) - 5}" if len(xs) > 5 else ""),
                           chto=f"посещений без списания: {len(xs)} — {pochemu}",
                           sdelat=sdelat, obnovitsya=D.obnovitsya("zanyatie", xs[-1][0]["date"]),
                           fakty={"zapisi": sorted(r["id"] for _, r in xs)}))
    return out


# ── Р5. Деньги в МойКлассе ≠ банк (по файлам контроля оплат) ──────────────

def _kontrol_dir() -> Path:
    from . import kontrol_oplat
    return Path(kontrol_oplat.OUT_DIR)


def _uid_po_imeni(D: Dannye, imya: str):
    imya = (imya or "").strip()
    return next((uid for uid, u in D.users.items() if u["name"] == imya), None)


def _rub_iz(t: str) -> float:
    return _num(re.sub(r"[^\d,.]", "", t).replace(",", "."))


def r5(D: Dannye) -> list[dict]:
    out = []
    papka = _kontrol_dir()
    if not papka.exists():
        return out
    lo = D.dd(-31)
    for f in sorted(papka.glob("*.json")):
        day = f.stem
        if not re.fullmatch(r"\d{4}-\d\d-\d\d", day) or not (lo <= day <= D.today):
            continue
        try:
            R = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        bank = R.get("bank") or {}
        max_pid = int(_num(R.get("max_pid")))
        dnya = [p for p in D.pays_by_day.get(day, []) if p.get("optype") == "income" and p["summa"] > 1]
        obn = D.obnovitsya("platezh", day)
        sbp_term = bank.get("sbp_as_term") or []
        # — СБП записано картой
        isklyuchit = set()
        for i, x in enumerate(sbp_term):
            man, imya, summa = (list(x) + [None, None, None])[:3]
            pid = x[3] if len(x) > 3 else None
            if pid:
                p = D.pay_by_id.get(pid)
                isklyuchit.add(pid)
                if not p or p.get("paymentTypeId") != T_TERM:
                    continue
                uid = p["userId"]
            else:
                uid = _uid_po_imeni(D, imya)
                kand = [p for p in dnya if p["userId"] == uid and p.get("paymentTypeId") == T_TERM
                        and abs(p["summa"] - _num(summa)) < 0.5]
                isklyuchit |= {p["id"] for p in kand[:1]}
                if not kand:
                    continue
            out.append(_stroka("5", f"r5:{day}:sbpterm:{pid or (str(imya) + ':' + str(summa))}", rebenok=imya or D.imya(uid),
                               uid=uid, data=day, kogda=_kogda(day),
                               chto=f"{rub(summa)} записано «карта», а по банку это СБП",
                               sdelat="сменить тип платежа на «онлайн» (СБП)", obnovitsya=obn,
                               fakty={"pid": pid, "summa": summa}))
        # — терминал за день ≠ банк
        if "diff" in bank and abs(_num(bank.get("diff"))) > 100:
            mk_card = sum(p["summa"] for p in dnya if p.get("paymentTypeId") == T_TERM and p["id"] not in isklyuchit)
            diff = round(mk_card - _num(bank.get("bank_card")), 2)
            if abs(diff) > 100:
                out.append(_stroka(
                    "5", f"r5:{day}:bank", data=day, kogda=_kogda(day), gruppa="терминал",
                    chto=(f"в банк пришло на {rub(-diff)} больше, чем записано картой в МойКлассе" if diff < 0
                          else f"в МойКлассе картой записано на {rub(diff)} больше, чем пришло в банк")
                         + f" (МойКласс {rub(mk_card)}, банк {rub(bank.get('bank_card'))})",
                    sdelat=(f"найти по чекам терминала, чья оплата не внесена, и внести «карта» датой {_dm(day)}" if diff < 0
                            else "проверить оплаты «карта» за день: какая не прошла по терминалу"),
                    ssylka="", obnovitsya=obn, fakty={"diff": diff}))
        # — СБП есть в банке, в МойКлассе нет
        # Файл, записанный до выкладки страницы, не знает max_pid: по нему не понять, внесли
        # оплату после контроля или нет, и строка висела бы 31 день даже после правки
        # (проверка 12.11). По таким файлам строку «СБП без оплаты» не показываем.
        for i, a in enumerate((bank.get("free_sbp") or []) if max_pid else []):
            novye = [p for p in dnya if p.get("paymentTypeId") == T_ONLINE and abs(p["summa"] - _num(a)) < 0.5
                     and p["id"] > max_pid]
            if novye:
                continue
            out.append(_stroka("5", f"r5:{day}:sbp:{a}:{i}", data=day, kogda=_kogda(day), gruppa="СБП",
                               chto=f"СБП {rub(a)} от {_dm(day)} есть в банке, в МойКлассе оплаты нет",
                               sdelat=f"найти, чья это оплата, и внести «онлайн» датой {_dm(day)}", obnovitsya=obn,
                               fakty={"summa": a}))
        # — маткапитал пришёл, прихода «перевод на счёт» нет
        mk = list(R.get("matkap_net") or [])
        if not mk:
            for x in (R.get("bloki") or {}).get("A. Маткапитал") or []:
                m = re.search(r"Маткапитал ([\d\s  ,]+) ₽ за (.+?) пришёл (\d\d)\.(\d\d) на счёт Буракова — в МойКлассе",
                              x.get("text") or "")
                if m:
                    mk.append({"summa": _rub_iz(m[1]), "fio": m[2], "data": f"{day[:4]}-{m[4]}-{m[3]}"})
        for x in mk:
            amt, od = _num(x.get("summa")), str(x.get("data") or day)[:10]
            if any(p.get("paymentTypeId") == T_PEREVOD and abs(p["summa"] - amt) < 1 and p["date"] >= od for p in D.pays):
                continue
            out.append(_stroka("5", f"r5:matkap:{od}:{round(amt)}", rebenok=x.get("fio") or "", data=od, kogda=_kogda(od),
                               gruppa="маткапитал",
                               chto=f"маткапитал {rub(amt)} пришёл {_dm(od)}, прихода «перевод на счёт» в МойКлассе нет",
                               sdelat="внести на баланс «перевод на счёт» и создать абонемент со списанием",
                               obnovitsya=D.obnovitsya("platezh", od), fakty={"summa": amt}))
        # — возвраты
        vz = list(R.get("vozvraty") or [])
        if not vz:
            for x in (R.get("bloki") or {}).get("A. Возвраты") or []:
                t = x.get("text") or ""
                imya = t.split(":")[0]
                uid = _uid_po_imeni(D, imya)
                for p in D.pays_by_day.get(day, []):
                    if p["userId"] == uid and p.get("optype") == "refund":
                        vz.append({"pid": p["id"], "uid": uid, "vid": "tip" if "записан типом" in t else "ostatok"})
        for x in vz:
            uid, pid = x.get("uid"), x.get("pid")
            if uid in D.ud["users"]:
                continue
            if x.get("vid") == "tip":
                p = D.pay_by_id.get(pid)
                if not p or p.get("paymentTypeId") == T_VOZVRAT:
                    continue
                chto, sdelat = "возврат записан не типом «возврат»", "сменить тип платежа на «возврат»"
            else:
                u = D.users.get(uid) or {}
                akt = [s for s in D.subs_by_user.get(uid, []) if s.get("statusId") == 2 and _num(s.get("payed")) > 0
                       and _num(s.get("visitedCount")) < _num(s.get("visitCount"))]
                if abs(u.get("balans") or 0) <= 1 and not akt:
                    continue
                chto = (f"после возврата баланс {rub(u.get('balans') or 0)}"
                        + (" и не закрыт оплаченный абонемент" if akt else ""))
                sdelat = "остаток — на баланс, возврат — с баланса, абонемент закрыть"
            out.append(_stroka("5", f"r5:{day}:voz:{pid}:{x.get('vid')}", rebenok=D.imya(uid), uid=uid, data=day,
                               kogda=_kogda(day), chto=chto, sdelat=sdelat, obnovitsya=obn,
                               fakty={"pid": pid, "vid": x.get("vid")}))
    # одна строка на ключ (маткапитал видят файлы нескольких дней)
    vid = {}
    for r in out:
        vid.setdefault(r["key"], r)
    return list(vid.values())


# ── Р6. Деньги на балансе — абонемента нет ────────────────────────────────

def r6(D: Dannye) -> list[dict]:
    out = []
    akt = defaultdict(list)
    for s in D.subs:
        if s.get("statusId") in (2, 3) and (s.get("endDate") or "9999") >= D.today:
            akt[s.get("userId")].append(s)
    posl = defaultdict(str)
    perevod = {}
    for p in D.pays:
        if p.get("optype") == "income" and p["summa"] > 1:
            posl[p["userId"]] = max(posl[p["userId"]], p["date"])
            if p.get("paymentTypeId") == T_PEREVOD and p["date"] >= "2026-08-01":
                perevod[p["userId"]] = max(perevod.get(p["userId"], ""), p["date"])
    for uid, u in D.users.items():
        b = u["balans"]
        # поправка скептика: порог 5 000 ₽ или приход «перевод на счёт» (маткапитал) —
        # остатки 2 100 и 2 450 ₽ у семей на паузе ложные
        if b < 1000 or akt.get(uid) or not (posl[uid] >= D.dd(-60) or uid in perevod):
            continue
        if not (b >= 5000 or uid in perevod):
            continue
        mk = f" (маткапитал {_dm(perevod[uid])})" if uid in perevod else ""
        out.append(_stroka("6", f"r6:{uid}", rebenok=u["name"], uid=uid, data=perevod.get(uid) or posl[uid],
                           kogda=f"приход {_dm(perevod.get(uid) or posl[uid])}",
                           chto=f"на балансе {rub(b)}{mk}, активного абонемента нет",
                           # проверка 12.11: семья могла уйти или отложить курс — тогда абонемент не нужен
                           sdelat=("если ребёнок ходит — создать абонемент со списанием с баланса; если ушёл или курс "
                                   "отложили — абонемент НЕ создавать: вернуть деньги или оставить по договорённости "
                                   "и записать это в карточке"),
                           obnovitsya=D.obnovitsya("abonement", D.today), fakty={"balans": b}))
    return out


# ── Р7. Ложный долг ───────────────────────────────────────────────────────

def _perekrytie(a: dict, b: dict) -> float:
    """Какую долю периода абонемента a перекрывает абонемент b."""
    a0, a1 = (a.get("beginDate") or "")[:10], (a.get("endDate") or "")[:10]
    b0, b1 = (b.get("beginDate") or "")[:10], (b.get("endDate") or "")[:10]
    try:
        da0, db0 = date.fromisoformat(a0), date.fromisoformat(b0)
    except ValueError:
        return 1.0 if a0[:7] == b0[:7] else 0.0
    da1 = date.fromisoformat(a1) if a1 else da0 + timedelta(days=30)
    db1 = date.fromisoformat(b1) if b1 else db0 + timedelta(days=30)
    lo, hi = max(da0, db0), min(da1, db1)
    if lo > hi:
        return 0.0
    return ((hi - lo).days + 1) / max((da1 - da0).days + 1, 1)


def r7(D: Dannye) -> list[dict]:
    out = []
    fut = defaultdict(set)
    for r in D.recs:
        l = D.lessons[r["lessonId"]]
        if l["date"] >= D.today:
            fut[r["userId"]].add(l["cid"])
    vozvraty = D.vozvraty
    for uid, ss in D.subs_by_user.items():
        if uid in D.ud["users"] or uid not in D.users:
            continue
        for s in ss:
            st, price, payed = s.get("statusId"), _num(s.get("price")), _num(s.get("payed"))
            f = D.fam(s.get("mainClassId"))
            gr = D.gruppa(s.get("mainClassId"))
            prodan = (s.get("sellDate") or "")[:10]
            baza = dict(rebenok=D.imya(uid), uid=uid, gruppa=gr, data=prodan or (s.get("beginDate") or "")[:10],
                        obnovitsya=D.obnovitsya("abonement", prodan))
            sezon = st in (2, 3) and prodan >= SEZON_OT
            if sezon and payed < price - 1:
                # 7а: рядом оплаченный того же направления и месяца, перекрывает хотя бы наполовину
                para = [x for x in ss if x is not s and _num(x.get("payed")) > 0 and _num(x.get("payed")) >= _num(x.get("price")) - 1
                        and D.fam(x.get("mainClassId")) == f and (x.get("beginDate") or "")[:7] == (s.get("beginDate") or "")[:7]
                        and _perekrytie(s, x) >= 0.5]
                # Проверка 12.11: неоплаченный абонемент на 1–2 занятия рядом с оплаченным на
                # месяц — это доплата за занятия сверх абонемента, а не дубль; «удалить» стёрло
                # бы настоящий долг. Дубль — когда занятий сопоставимо.
                para = [x for x in para if not (0 < _num(s.get("visitCount")) <= 2
                                                and _num(x.get("visitCount")) >= 2 * _num(s.get("visitCount")) + 2)]
                if para:
                    t = para[0]
                    s_poseshch = _num(s.get("visitedCount")) > 0
                    out.append(_stroka(
                        "7а", f"r7a:{s['id']}", kogda=f"{_dm((s.get('beginDate') or '')[:10])}–{_dm((s.get('endDate') or '')[:10])}",
                        chto=(f"абонемент {rub(price)} не оплачен (оплачено {rub(payed)}), рядом оплаченный {rub(t.get('price'))} "
                              f"того же курса — похоже на дубль" + (f"; на неоплаченном уже {int(_num(s.get('visitedCount')))} посещ." if s_poseshch else "")),
                        sdelat=("перенести списания на оплаченный абонемент, потом удалить неоплаченный" if s_poseshch
                                else "удалить неоплаченный, проверив, что к нему не привязаны списания"),
                        fakty={"payed": payed, "price": price, "visited": s.get("visitedCount"), "para": t["id"]}, **baza))
                    continue
            if sezon and payed == 0 and price > 1 and _num(s.get("visitedCount")) == 0 \
                    and not (set(s.get("classIds") or []) & fut[uid]):
                out.append(_stroka("7б", f"r7b:{s['id']}", kogda=f"продан {_dm(prodan)}",
                                   chto=f"неоплаченный абонемент {rub(price)} без единого занятия и без будущих записей",
                                   sdelat="удалить или закрыть абонемент", fakty={"payed": 0, "price": price}, **baza))
                continue
            # 7г: открыт после возврата всей оплаты (сумма возвратов по модулю, без ограничения датой продажи)
            if st in (2, 3) and payed > 0:
                rf = [p for p in vozvraty.get(uid, []) if p["date"] >= (prodan or "9")]
                vozvr = sum(abs(p["summa"]) for p in rf)
                if rf and vozvr >= payed - 1 and (
                        any(p.get("userSubscriptionId") == s["id"] for p in rf)
                        or len([y for y in ss if y.get("statusId") in (2, 3) and _num(y.get("payed")) > 0]) == 1
                        # возврат ровно на оплату абонемента, по которому никто не ходил (п. 3767, летние)
                        or (any(abs(abs(p["summa"]) - payed) < 1 for p in rf) and not _num(s.get("visitedCount")))):
                    vd = max(p["date"] for p in rf)
                    # Проверка 12.11: возврат переплаты (пришёл маткапитал — вернули прежние
                    # оплаты семьи) принят за возврат абонемента, а ребёнок ходит. Абонемент,
                    # по которому после возврата есть посещения или впереди записи, не трогаем.
                    svoi_gr = set(s.get("classIds") or []) | {s.get("mainClassId")}
                    hodit = bool(svoi_gr & fut[uid]) or any(
                        r.get("visit") and D.lessons[r["lessonId"]]["cid"] in svoi_gr
                        and D.lessons[r["lessonId"]]["date"] >= vd for r in D.recs_by_user.get(uid, []))
                    if hodit:
                        continue
                    out.append(_stroka("7г", f"r7g:{s['id']}", kogda=f"возврат {_dm(vd)}",
                                       chto=f"абонемент открыт, хотя вся оплата {rub(payed)} возвращена",
                                       sdelat="закрыть абонемент", fakty={"payed": payed, "st": st},
                                       **(baza | {"data": vd})))
    # 7д: баланс в минусе из-за неоплаченного «оконченного» абонемента без посещений
    for uid, u in D.users.items():
        b = u["balans"]
        if b >= -1:
            continue
        un = [s for s in D.subs_by_user.get(uid, []) if s.get("statusId") == 4
              and _num(s.get("payed")) < _num(s.get("price")) - 1 and not _num(s.get("visitedCount"))]
        if un and sum(_num(s.get("price")) - _num(s.get("payed")) for s in un) >= -b - 1:
            out.append(_stroka("7д", f"r7d:{uid}", rebenok=u["name"], uid=uid,
                               gruppa=", ".join(sorted({D.gruppa(s.get('mainClassId')) for s in un}))[:80],
                               data=min((s.get("sellDate") or s.get("beginDate") or D.today)[:10] for s in un),
                               kogda="", chto=f"баланс {rub(b)} из-за неоплаченного «оконченного» абонемента без посещений",
                               sdelat="удалить такой абонемент (баланс вернётся к нулю)",
                               obnovitsya=D.obnovitsya("abonement", ""), fakty={"balans": b, "subs": sorted(s["id"] for s in un)}))
    # 7в: неоплаченный счёт до 1 ₽ (счета обновляет и чистит полный синк)
    for inv in D.invoices:
        uid = inv["user_id"]
        if uid in D.ud["users"]:
            continue
        out.append(_stroka("7в", f"r7v:{inv['id']}", rebenok=D.imya(uid), uid=uid, data=str(inv.get("created_at") or "")[:10],
                           kogda=f"счёт от {_dm(str(inv.get('created_at') or '')[:10])}",
                           chto=f"неоплаченный счёт на {rub(inv.get('price'))}", sdelat="удалить счёт",
                           obnovitsya=D.obnovitsya("schet"), fakty={"price": inv.get("price"), "payed": inv.get("payed")}))
    return out


# ── Р8. Записи не по факту ────────────────────────────────────────────────

def _min(t: str) -> int:
    try:
        h, m = (t or "0:0").split(":")[:2]
        return int(h) * 60 + int(m)
    except ValueError:
        return 0


def _nedelya_8v(D: Dannye, kandidaty: set[str]) -> set[str]:
    """8в показываем раз в неделю как «проверить» (поправка скептика): новые строки
    приходят по понедельникам (или в первый пересчёт недели), исправленные уходят сразу."""
    ned = f"{D.segodnya.isocalendar()[0]}-{D.segodnya.isocalendar()[1]:02d}"
    st = _j(db.get_state("proverka_mk_8v") or "{}")
    if st.get("nedelya") != ned:
        st = {"nedelya": ned, "keys": sorted(kandidaty)}
        try:
            db.set_state("proverka_mk_8v", json.dumps(st, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            log.exception("8в: снимок недели не сохранился")
    return set(st.get("keys") or [])


def r8(D: Dannye) -> list[dict]:
    out = []
    lo8, hi8 = D.dd(-7), D.dd(21)
    paid_subs = defaultdict(list)
    for s in D.subs:
        if s.get("statusId") in (2, 3, 4) and _num(s.get("payed")) > 0:
            paid_subs[s.get("userId")].append(s)
    # 8а: одна дата, одно направление, две записи — без отработок и пробных
    den = defaultdict(list)
    for r in D.recs:
        l = D.lessons[r["lessonId"]]
        if D.is27(l["cid"]) and lo8 <= l["date"] <= hi8 and not r.get("missedLessonRecordId") and not r.get("test"):
            den[(r["userId"], l["date"], D.fam(l["cid"]))].append(l)
    po = defaultdict(list)
    for (uid, d, f), ls in den.items():
        if len(ls) < 2 or len({l["cid"] for l in ls}) < 2 or uid in D.ud["users"]:
            continue     # повтор в одной группе в одно время — это дубль занятия (12а), не запись
        ls = sorted(ls, key=lambda l: l["begin"])
        nakl = any(_min(b["begin"]) < _min(a["end"] or a["begin"]) for a, b in zip(ls, ls[1:]))
        npaid = len({s.get("mainClassId") for s in paid_subs.get(uid, []) if D.fam(s.get("mainClassId")) == f
                     and (s.get("beginDate") or "") <= d <= (s.get("endDate") or "9")})
        if nakl or npaid == 1:
            po[(uid, tuple(sorted({l["cid"] for l in ls})))].append(d)
    for (uid, cids), dni in po.items():
        dni = sorted(dni)
        out.append(_stroka("8а", f"r8a:{uid}:{'-'.join(map(str, cids))}", rebenok=D.imya(uid), uid=uid,
                           gruppa=" и ".join(D.gruppa(c) for c in cids)[:90], data=dni[0],
                           kogda=", ".join(_kogda(d) for d in dni[:4]),
                           chto="в один день записан в две группы одного направления",
                           sdelat="лишнюю запись снять без списания", obnovitsya=D.obnovitsya("zanyatie", dni[0]),
                           fakty={"dni": dni}))
    # 8б: будущие записи, а из группы выбыл — без отработок и пробных
    agg = defaultdict(list)
    for r in D.recs:
        l = D.lessons[r["lessonId"]]
        if (D.is27(l["cid"]) and D.today <= l["date"] <= hi8 and D.jst.get((r["userId"], l["cid"])) == J_VYBYL
                and not r.get("missedLessonRecordId") and not r.get("test") and r["userId"] not in D.ud["users"]):
            agg[(r["userId"], l["cid"])].append(l["date"])
    for (uid, cid), dni in agg.items():
        dni = sorted(dni)
        out.append(_stroka("8б", f"r8b:{uid}:{cid}", rebenok=D.imya(uid), uid=uid, gruppa=D.gruppa(cid), class_id=cid,
                           data=dni[0], kogda="записи на " + ", ".join(_dm(d) for d in dni[:5]),
                           chto="из группы выбыл, а записи на занятия остались", sdelat="снять эти записи",
                           obnovitsya=D.obnovitsya("zanyatie", dni[0]), fakty={"dni": dni}))
    # 8в: «учится», группа идёт, записей на 3 недели нет; без заморозки, перекрывающей окно
    fut_cls = Counter(l["cid"] for l in D.lessons.values() if D.is27(l["cid"]) and D.today <= l["date"] <= hi8)
    fut_rec = {(r["userId"], D.lessons[r["lessonId"]]["cid"]) for r in D.recs
               if D.today <= D.lessons[r["lessonId"]]["date"] <= hi8}
    kand = {}
    for j in D.joins:
        uid, cid = j["user_id"], j["class_id"]
        if j["status_id"] != J_UCHITSYA or not D.is27(cid) or not fut_cls.get(cid) or (uid, cid) in fut_rec:
            continue
        if uid not in D.users:
            continue
        if any(s.get("freezeFrom") and (s.get("freezeTo") or "") >= D.today for s in D.subs_by_user.get(uid, [])):
            continue
        kand[f"r8v:{uid}:{cid}"] = (uid, cid)
    nedelya = _nedelya_8v(D, set(kand))
    for key, (uid, cid) in kand.items():
        if key not in nedelya and key not in D.otkrytye:
            continue
        out.append(_stroka("8в", key, rebenok=D.imya(uid), uid=uid, gruppa=D.gruppa(cid), class_id=cid, data=D.today,
                           kogda="на 3 недели вперёд",
                           chto="«учится» в группе, а записей на 3 недели нет — проверить",
                           sdelat="записать на занятия или сменить статус (пауза, заморозка, ушёл — «не ошибка» с причиной)",
                           obnovitsya=D.obnovitsya("zapis"), fakty={"zapisey": 0}))
    # 8г: первая запись по направлению — на пробное, а у записи нет «пробное»
    pervaya = {}
    for r in sorted(D.recs, key=lambda r: D.lessons[r["lessonId"]]["date"]):
        l = D.lessons[r["lessonId"]]
        if D.is27(l["cid"]):
            pervaya.setdefault((r["userId"], D.fam(l["cid"])), (l, r))
    for (uid, f), (l, r) in pervaya.items():
        if D.nachalos(l) or r.get("test") or l["date"] > hi8 or f.startswith("ЛГ") or "огопед" in D.cname(l["cid"]):
            continue
        if uid in D.ud["users"] or D.jst.get((uid, l["cid"])) != J_PROBNOE:
            continue
        if any(D.fam(s.get("mainClassId")) == f and _num(s.get("payed")) > 0 for s in D.subs_by_user.get(uid, [])):
            continue
        out.append(_stroka("8г", f"r8g:{uid}:{f}", rebenok=D.imya(uid), uid=uid, gruppa=D.gruppa(l["cid"]),
                           class_id=l["cid"], data=l["date"], kogda=_kogda(l["date"], l["begin"]),
                           chto="записан на пробное, а у записи нет пометки «пробное»",
                           sdelat="поставить пометку «пробное»", obnovitsya=D.obnovitsya("zanyatie", l["date"]),
                           fakty={"test": False}))
    return out


# ── Р9. Камера ≠ МойКласс ─────────────────────────────────────────────────

def r9(D: Dannye) -> list[dict]:
    out = []
    po = defaultdict(list)
    for k in D.kadry:
        po[k["lesson_id"]].append(k)
    for lid, kd in po.items():
        l = D.lessons.get(lid)
        if not l:
            continue
        rs = D.rbl.get(lid, [])
        deti = max(int(k["deti"] or 0) for k in kd)
        byl = sum(1 for r in rs if r.get("visit"))
        ne_propusk = any(_KADR_NE_PROPUSK.search(str(k.get("ocenka") or "")) for k in kd)
        vremya = ", ".join(sorted({str(k.get("ts") or "")[11:16] for k in kd if k.get("ts")}))
        baza = dict(gruppa=D.gruppa(l["cid"]) or (kd[0].get("class_name") or ""), class_id=l["cid"], data=l["date"],
                    kogda=_kogda(l["date"], l["begin"]), obnovitsya=D.obnovitsya("zanyatie", l["date"]))
        zakryto = l["status"] == 1
        # без единой записи кадр показывает чужое занятие в той же комнате (логопед в
        # кабинете пустой группы — так в оценках кадров 05–10.11): это не наш случай
        if rs and deti > len(rs):
            out.append(_stroka("9", f"r9:{lid}:bolshe", **baza,
                               chto=f"на кадре ({vremya}) детей {deti}, а записей {len(rs)}",
                               sdelat="завести запись лишнему ребёнку (отработка или пробное) — уточнить у педагога, кто это",
                               seraya="" if zakryto or l["date"] < D.today else "занятие ещё не закрыто",
                               fakty={"deti": deti, "zapisey": len(rs)}))
        if not zakryto:
            continue        # (а) и (в) — только по закрытому занятию (поправка скептика)
        if ne_propusk:
            continue        # оценка кадра: группа в другой комнате или ребёнок пришёл в другое время
        if deti == 0 and byl:
            out.append(_stroka("9", f"r9:{lid}:pusto", **baza,
                               chto=f"на кадрах ({vremya}) детей нет, а занятие «проведено» и «был» у {byl}",
                               sdelat="уточнить у педагога; явку ставить по факту", fakty={"deti": 0, "byl": byl}))
        elif byl > deti + 1:
            out.append(_stroka("9", f"r9:{lid}:byl", **baza,
                               chto=f"«был» отмечен у {byl}, а на кадрах ({vremya}) детей не больше {deti}",
                               sdelat="уточнить у педагога; лишние «был» снять", fakty={"deti": deti, "byl": byl}))
    return out


# ── Р10. Правка без объяснения (по файлам сверки честности) ───────────────

def _sverka_dir() -> Path:
    from . import sverka_chestnosti
    return Path(sverka_chestnosti.OUT_DIR)


def r10(D: Dannye) -> list[dict]:
    papka = _sverka_dir()
    if not papka.exists():
        return []
    lo = D.dd(-14)
    fayly = [f for f in sorted(papka.glob("*.json")) if re.fullmatch(r"\d{4}-\d\d-\d\d", f.stem) and lo <= f.stem <= D.today]
    nakhodki = []
    udal_abon = defaultdict(set)          # клиент → дни, когда у него удаляли абонемент
    for f in fayly:
        try:
            R = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for x in R.get("nakhodki") or []:
            nakhodki.append((f.stem, x))
            if str(x.get("kind") or "").startswith("удаление: user_subscriptions") and x.get("userId"):
                udal_abon[x["userId"]].add(f.stem)
    # правки абонементов по клиентам (любого уровня): по ним узнаём «эхо» — списание в оплату
    # абонемента (debit) меняется само, когда меняют цену абонемента
    abon_pravki = defaultdict(list)
    for den, x in nakhodki:
        if str(x.get("kind") or "") == "правка: user_subscriptions" and x.get("level") in ("red", "yellow") \
                and x.get("userId") and x.get("rid"):
            abon_pravki[x["userId"]].append((den, x["rid"], str(x.get("text") or "")))
    sklejka: dict[str, dict] = {}
    for den, x in nakhodki:
        kind, uid = str(x.get("kind") or ""), x.get("userId")
        if x.get("level") != "yellow" or not uid or uid in D.ud["users"]:
            continue
        if not (kind.startswith(("правка:", "удаление:")) or kind == "баланс"):
            continue
        polya = x.get("extra") or {}
        tekst = str(x.get("text") or "")
        tbl, rid = x.get("tbl"), x.get("rid")
        # механика, а не нарушение (поправка скептика: 70 из 163 жёлтых)
        if tbl == "lesson_records" and "bill" in polya:
            try:
                a, b = (polya["bill"] + [None, None])[:2]
                a_s = _num((a or {}).get("summa")) if isinstance(a, dict) else _num(a)
                b_s = _num((b or {}).get("summa")) if isinstance(b, dict) else _num(b)
            except Exception:  # noqa: BLE001
                a_s = b_s = 0
            if a_s > 0 and b_s == 0 and rid in D.linked:
                continue         # списание за пропуск ушло на привязанную отработку
            if a_s > 0 and 0 < b_s < a_s and (udal_abon.get(uid) or uid in D.udal_abon_uid):
                continue         # пересчёт после удаления неоплаченного дубля
            cur = D.rec_by_id.get(rid)
            if cur is not None and abs(_bill(cur) - a_s) < 0.01 and a_s > 0:
                continue         # правку откатили
        if kind.startswith("удаление: user_subscriptions"):
            raw = (polya or {}).get("raw") or {}
            if _num(raw.get("payed")) == 0:
                # удалён неоплаченный абонемент — это действие раздела 7 (дубль, висящий);
                # посещения с него переносят на оплаченный. Иначе петля: исправленная
                # строка 7а рождала бы строку «объясни в карточке» (поправка скептика)
                continue
        if tbl == "payments" and kind.startswith("правка:") and "summa" in polya:
            cur = D.pay_by_id.get(rid)
            if cur is not None and abs(cur["summa"] - _num((polya["summa"] + [0])[0])) < 0.01:
                continue         # сумму вернули
        key = f"r10:{tbl}:{rid}" if rid else f"r10:{kind}:{uid}"
        if tbl == "payments" and kind.startswith("правка:") and "summa" in polya:
            # Сверка 12.11: 16 из 30 строк были «сумма -9450 → -8400» — это не отдельная правка,
            # а списание в оплату абонемента, пересчитанное вслед за ценой абонемента. Строка
            # одна — про абонемент, и словами, которые понятны («цена … снижена 9450 → 8400»).
            try:
                a_s, b_s = _num(polya["summa"][0]), _num(polya["summa"][1])
            except Exception:  # noqa: BLE001
                a_s = b_s = 0
            if a_s < 0 and b_s < 0:
                sid = (D.pay_by_id.get(rid) or {}).get("userSubscriptionId")
                d0 = date.fromisoformat(den)
                para = [p for p in abon_pravki.get(uid, [])
                        if abs((date.fromisoformat(p[0]) - d0).days) <= 1 and (not sid or p[1] == sid)]
                if para:
                    key, tekst = f"r10:user_subscriptions:{para[0][1]}", para[0][2]
        if key in sklejka:
            if den not in sklejka[key]["dni"]:
                sklejka[key]["dni"].append(den)
            continue
        sklejka[key] = {"uid": uid, "dni": [den], "tekst": tekst, "kind": kind}
    out = []
    for key, x in sklejka.items():
        uid, dni = x["uid"], sorted(x["dni"])
        # исчезает: в карточке появился комментарий человека не раньше находки
        # (с вечера накануне: сверка замечает правку наутро, а объяснение пишут сразу)
        s_dnya = (date.fromisoformat(dni[0]) - timedelta(days=1)).isoformat()
        if any(c.get("chelovek") and str(c.get("ts") or "")[:10] >= s_dnya for c in D.comm_by_user.get(uid, [])):
            continue
        chto = x["tekst"].split(": ", 1)[1] if ": " in x["tekst"] else x["tekst"]
        chto = re.sub(r"\s*\(замечено [^)]*\)", "", chto)
        out.append(_stroka("10", key, rebenok=D.imya(uid), uid=uid, data=dni[0],
                           kogda="замечено " + ", ".join(_dm(d) for d in dni[:3]),
                           chto=chto[:300], sdelat="одна строка в карточке: кто и почему (если ошибка — вернуть как было)",
                           obnovitsya=D.obnovitsya("kommentariy"), fakty={"dni": dni}))
    return out


# ── Р11. Карточки и статусы ───────────────────────────────────────────────

def _p10(x) -> str:
    d = re.sub(r"\D", "", str(x or ""))
    return d[-10:] if len(d) >= 10 else ""


def r11(D: Dannye) -> list[dict]:
    out = []
    po_nomeru = defaultdict(list)
    for uid, u in D.users.items():
        p = _p10(u["phone"])
        if p:
            po_nomeru[p].append(uid)
    # номер в имени карточки (заявка из мессенджера) — тоже номер: п. 4824, две пустышки
    po_nomeru_imya = defaultdict(set)
    for uid, u in D.users.items():
        for p in {_p10(u["phone"]), _p10(u["name"]) if NOMER_V_IMENI.fullmatch(u["name"] or "") else ""}:
            if p:
                po_nomeru_imya[p].add(uid)
    obn = D.obnovitsya("kartochka")
    for uid, u in D.users.items():
        n, st = u["name"], u["state"]
        if st in ZHIVYE and re.search(r"дубл", n, re.I):
            # поправка скептика: «архив» без проверки не советуем — бывает настоящий ребёнок
            out.append(_stroka("11а", f"r11a:{uid}", rebenok=n, uid=uid, data="", kogda=f"заведена {_dm(u['created'])}",
                               chto="«ДУБЛЬ» в имени, а статус живой — по ней ставятся звонки",
                               sdelat="если это дубль — объединить с основной карточкой или в архив; если настоящий ребёнок — убрать «ДУБЛЬ» из имени",
                               obnovitsya=obn, fakty={"name": n, "state": st}))
        elif st in ZHIVYE and NOMER_V_IMENI.fullmatch(n or ""):
            drugie = sorted({x for p in {_p10(u["phone"]), _p10(n)} if p for x in po_nomeru_imya.get(p, ()) if x != uid})
            # поправка скептика: только если на номер есть вторая карточка или разговор уже был
            if not drugie and st in (NOVYI_LID, NET_OTVETA):
                continue
            out.append(_stroka("11б", f"r11b:{uid}", rebenok="номер вместо имени", uid=uid, data="",
                               kogda=f"заведена {_dm(u['created'])}",
                               chto="вместо имени ребёнка — номер телефона",
                               sdelat=(f"похоже на дубль карточки №{drugie[0]} — пометить «ДУБЛЬ» или объединить" if drugie
                                       else "поставить статус по разговору или вписать имя ребёнка"),
                               obnovitsya=obn, fakty={"name": n, "state": st, "drugie": drugie[:3]}))
    # 11в: один номер — новая карточка «в работе» и старая в отказе или «клиент»
    for p, uids in po_nomeru.items():
        if len(uids) < 2:
            continue
        zhiv = [x for x in uids if D.users[x]["state"] in ZHIVYE and D.users[x]["created"] >= "2026-10-01"
                and not re.search(r"дубл", D.users[x]["name"], re.I)]
        star = [x for x in uids if x not in zhiv and D.users[x]["state"] in (OTKAZ, KLIENT)]
        if not zhiv or not star:
            continue
        def imya2(x):
            w = D.users[x]["name"].split()
            return w[1].lower() if len(w) > 1 else ""
        pary = [z for z in zhiv if len(D.users[z]["name"].split()) <= 1 or any(imya2(z) and imya2(z) == imya2(s) for s in star)]
        if not pary:
            continue
        z, s = pary[0], star[0]
        out.append(_stroka("11в", f"r11v:{'-'.join(map(str, sorted([z] + star)))}", rebenok=D.users[z]["name"], uid=z,
                           data=D.users[z]["created"], kogda=f"заведена {_dm(D.users[z]['created'])}",
                           chto=f"на тот же номер есть старая карточка №{s} в статусе «{'отказ' if D.users[s]['state'] == OTKAZ else 'клиент'}»",
                           sdelat="склеить карточки или поставить один статус (братья и сёстры — «не ошибка»)",
                           obnovitsya=obn, fakty={"uids": sorted([z] + star),
                                                  "states": [D.users[x]["state"] for x in sorted([z] + star)]}))
    # 11г: заявочная запись «2627_…Заявки» висит «новой», а карточка в отказе, архиве или «не писать»
    for j in D.joins:
        n = D.cname(j["class_id"])
        if not (n.startswith("2627") and "аявк" in n) or j["status_id"] != J_NOVAYA:
            continue
        u = D.users.get(j["user_id"])
        if not u or u["state"] not in (OTKAZ, ARHIV, NE_PISAT):
            continue
        out.append(_stroka("11г", f"r11g:{j['id']}", rebenok=u["name"], uid=j["user_id"], gruppa=D.gruppa(j["class_id"]),
                           data="", kogda="",
                           chto="заявка висит «новой», а карточка в отказе, архиве или «не писать»",
                           sdelat="поставить итоговый статус заявки", obnovitsya=D.obnovitsya("zapis"),
                           fakty={"join": j["status_id"], "state": u["state"]}))
    # 11д: новые карточки за 14 дней с пробелами — только после разговора
    for uid, u in D.users.items():
        if u["created"] < D.dd(-14) or u["state"] not in POSLE_RAZGOVORA:
            continue
        n = u["name"]
        chego = []
        if not u["attrs"].get("birthday"):
            chego.append("дата рождения")
        if not u["phone"]:
            chego.append("телефон")
        if (MSG_V_IMENI.search(n) or VOZRAST_V_IMENI.search(n) or (n[:1].islower() if n else True)
                or NOMER_V_IMENI.match(n) or SLUZH_IMYA.match(n) or len(n.split()) <= 1):
            chego.append("имя и фамилия ребёнка")
        if not chego:
            continue
        out.append(_stroka("11д", f"r11d:{uid}", rebenok=n or "без имени", uid=uid, data=u["created"],
                           kogda=f"заведена {_dm(u['created'])}",
                           chto="в карточке не заполнено: " + ", ".join(chego),
                           sdelat="дозаполнить карточку", obnovitsya=obn, fakty={"chego": chego}))
    return out


# ── Р12. Расписание ───────────────────────────────────────────────────────

def r12(D: Dannye) -> list[dict]:
    out = []
    slot = defaultdict(list)
    vremena = defaultdict(Counter)
    for l in D.lessons.values():
        if D.is27(l["cid"]) and l["date"] >= D.today:
            slot[(l["cid"], l["date"], l["begin"])].append(l["id"])
            vremena[l["cid"]][l["begin"]] += 1
    dubli = defaultdict(list)
    for (cid, d, t), v in slot.items():
        if len(v) >= 2:
            dubli[(cid, t)].append(d)
    for (cid, t), dni in dubli.items():
        dni = sorted(dni)
        out.append(_stroka("12а", f"r12a:{cid}:{t}", gruppa=D.gruppa(cid), class_id=cid, data=D.today,
                           kogda=f"{t}, {len(dni)} дат с {_dm(dni[0])}",
                           chto="два одинаковых занятия группы в один день и час", sdelat="удалить лишние занятия",
                           obnovitsya=D.obnovitsya("zanyatie", dni[0]), fakty={"dni": dni}))
    for cid, tc in vremena.items():
        m = re.search(r"(\d{1,2})[:.](\d{2})", D.cname(cid))
        if not m or len(tc) != 1 or "+" in D.cname(cid):
            continue
        t = f"{int(m[1]):02d}:{m[2]}"
        main = tc.most_common(1)[0][0]
        if main != t:
            out.append(_stroka("12б", f"r12b:{cid}", gruppa=D.gruppa(cid), class_id=cid, data=D.today,
                               kogda=f"в названии {t}, занятия в {main}",
                               chto="время занятий не совпадает со временем в названии группы (сайт берёт время из названия)",
                               sdelat="поправить название группы или время занятий",
                               obnovitsya=D.obnovitsya("zanyatie", D.today), fakty={"t": t, "main": main}))
    # 12в: занятия открытой группы с учениками кончаются раньше 31.05
    noch = _j(db.get_state("proverka_mk_raspisanie") or "{}")
    svezho = str(noch.get("ts") or "") >= D.dd(-3)
    posl = {int(k): v for k, v in (noch.get("posledniy") or {}).items()} if svezho else {}
    for cid, c in D.classes.items():
        if not D.is27(cid) or c["status"] != "opened" or not D.active_join.get(cid):
            continue
        if re.search(r"Мини|Нулев|Праздник|ник \d", D.cname(cid)):
            continue
        bud = [l["date"] for l in D.lessons_by_class.get(cid, []) if l["date"] >= D.today]
        if not bud and cid not in posl:
            continue
        konec = posl.get(cid) or max(bud)
        # Проверка 12.11: без ночной проверки расписания до 31.05 правило видит только край
        # локальной выгрузки (+62 дня), и 5 строк из 6 были ложными (группа «кончалась»
        # перед новогодними каникулами). Поэтому 12в — только по свежей ночной проверке.
        if svezho and konec < "2027-05-24":
            out.append(_stroka("12в", f"r12v:{cid}", gruppa=D.gruppa(cid), class_id=cid, data=D.today,
                               kogda=f"последнее занятие {_dm(konec)}",
                               chto=f"занятия в расписании кончаются {_dm(konec)}, раньше 31.05",
                               sdelat="продлить расписание группы до 31.05", obnovitsya=D.obnovitsya("noch"),
                               fakty={"konec": konec}))
    return out


SEKCII = [("1", r1, {"1"}), ("2", r2, {"2а", "2б", "2в", "12г"}), ("3", r3, {"3"}), ("4", r4, {"4а", "4б"}),
          ("5", r5, {"5"}), ("6", r6, {"6"}), ("7", r7, {"7а", "7б", "7в", "7г", "7д"}),
          ("8", r8, {"8а", "8б", "8в", "8г"}), ("9", r9, {"9"}), ("10", r10, {"10"}),
          ("11", r11, {"11а", "11б", "11в", "11г", "11д"}), ("12", r12, {"12а", "12б", "12в"})]
OKONNYE = {"1", "2а", "3", "4а", "4б"}       # строки с окном «с 1-го числа прошлого месяца»


# ── сборка ────────────────────────────────────────────────────────────────

def _otkrytye(conn) -> dict[str, dict]:
    out = {}
    for r in conn.execute("SELECT key, razdel, pervyi, dannye, zametka FROM proverka_mk_stroki WHERE zakryta IS NULL"):
        d = _j(r["dannye"])
        out[r["key"]] = {"razdel": r["razdel"], "pervyi": r["pervyi"], "data": d.get("data") or "",
                         "zametka": r["zametka"] or ""}
    return out


def sobrat(seychas: datetime | None = None, zapisat: bool = False) -> dict:
    """Все разделы 1–12. zapisat=True — обновить proverka_mk_stroki и кэш страницы.

    Раздел, который упал (битый файл, нет таблицы), строк не закрывает: его открытые
    строки остаются как были, а на странице видно «раздел не собрался»."""
    seychas = seychas or _now()
    ts = seychas.isoformat(timespec="minutes")
    with db.get_conn() as conn:
        _init(conn)
        otkr = _otkrytye(conn)
        ne_osh = {r["key"]: dict(r) for r in conn.execute("SELECT * FROM proverka_mk_ne_oshibka")}
    D = Dannye(seychas, otkr)
    stroki, oshibki, ne_trogat = [], {}, set()
    for nomer, fn, pods in SEKCII:
        try:
            stroki += fn(D)
        except Exception as e:  # noqa: BLE001 — один раздел не роняет страницу
            log.exception("проверка МК: раздел %s не собрался", nomer)
            oshibki[nomer] = f"{type(e).__name__}: {str(e)[:160]}"
            ne_trogat |= pods
    # окно с 1-го числа прошлого месяца берёт только НОВЫЕ строки; открытая строка
    # живёт, пока данные не исправлены (поправка скептика)
    stroki = [r for r in stroki if not (r["pod"] in OKONNYE and r["data"] and r["data"] < D.m1 and r["key"] not in otkr)]
    vykl = vyklyucheny()
    stroki = [r for r in stroki if r["pod"].lower() not in vykl and r["razdel"] not in vykl]
    uniq = {}
    for r in stroki:
        uniq.setdefault(r["key"], r)
    stroki = list(uniq.values())
    for r in stroki:
        r["otpechatok"] = _otpechatok(r.get("fakty"))
        no = ne_osh.get(r["key"])
        r["ne_oshibka"] = None
        if no and r["dogadka"] and str(no.get("do") or "") > ts and no.get("otpechatok") == r["otpechatok"]:
            r["ne_oshibka"] = {"prichina": no.get("prichina"), "kto": no.get("kto"), "ts": no.get("ts")}
        o = otkr.get(r["key"]) or {}
        r["pervyi"] = o.get("pervyi") or ts
        r["zametka"] = o.get("zametka") or ""
    if zapisat:
        _zapisat(stroki, otkr, ne_trogat, ts)
    return _itog(D, stroki, oshibki, seychas, ne_osh)


def _zapisat(stroki: list[dict], otkr: dict, ne_trogat: set, ts: str) -> None:
    tek = {r["key"] for r in stroki}
    with db.get_conn() as conn:
        _init(conn)
        for r in stroki:
            dannye = json.dumps({k: v for k, v in r.items() if k not in ("zametka", "pervyi")}, ensure_ascii=False, default=str)
            if r["key"] in otkr:
                conn.execute("UPDATE proverka_mk_stroki SET razdel=?, posledniy=?, dannye=? WHERE key=?",
                             (r["pod"], ts, dannye, r["key"]))
            else:
                # новая или вернувшаяся строка: «впервые» — сейчас, заметка остаётся
                cur = conn.execute("UPDATE proverka_mk_stroki SET razdel=?, pervyi=?, posledniy=?, zakryta=NULL, dannye=? "
                                   "WHERE key=?", (r["pod"], ts, ts, dannye, r["key"]))
                if not cur.rowcount:
                    conn.execute("INSERT INTO proverka_mk_stroki (key, razdel, pervyi, posledniy, zakryta, zametka, dannye) "
                                 "VALUES (?,?,?,?,NULL,'',?)", (r["key"], r["pod"], ts, ts, dannye))
                r["pervyi"] = ts
        for key, o in otkr.items():
            if key not in tek and o.get("razdel") not in ne_trogat:
                conn.execute("UPDATE proverka_mk_stroki SET zakryta=? WHERE key=?", (ts, key))


def _vozrast(r: dict, segodnya: date) -> int:
    ds = [x for x in (r.get("data") or "", (r.get("pervyi") or "")[:10]) if re.fullmatch(r"\d{4}-\d\d-\d\d", x)]
    if not ds:
        return 0
    return max(0, (segodnya - date.fromisoformat(min(ds))).days)


def _itog(D: Dannye, stroki: list[dict], oshibki: dict, seychas: datetime, ne_osh: dict) -> dict:
    sutki = (seychas - timedelta(hours=24)).isoformat(timespec="minutes")
    nedelya = (seychas - timedelta(days=7)).isoformat(timespec="minutes")
    for r in stroki:
        r["vozrast"] = _vozrast(r, D.segodnya)
        r["hvost"] = r["vozrast"] > HVOST_DNEY
    vidimye = [r for r in stroki if not r["ne_oshibka"] and not r["seraya"]]
    try:
        with db.get_conn() as conn:
            _init(conn)
            zakryto = conn.execute("SELECT COUNT(*) FROM proverka_mk_stroki WHERE zakryta >= ?", (sutki,)).fetchone()[0]
    except Exception:  # noqa: BLE001
        zakryto = 0
    razdely = []
    vykl = vyklyucheny()
    for nomer, nazv, dengi in RAZDELY:
        if nomer in vykl:
            continue
        rs = sorted([r for r in stroki if r["razdel"] == nomer], key=lambda r: (r["pod"], r["data"] or "", r["key"]))
        vid = [r for r in rs if not r["ne_oshibka"] and not r["seraya"]]
        star = max((r["vozrast"] for r in vid), default=0)
        razdely.append({"nomer": nomer, "nazvanie": nazv, "dengi": dengi, "stroki": rs, "vsego": len(vid),
                        "samoy_staroy": star, "krasnyi": dengi and star > 7, "oshibka": oshibki.get(nomer, "")})
    ne_osh_ned = sorted([dict(v) for v in ne_osh.values() if str(v.get("ts") or "") >= nedelya],
                        key=lambda v: v.get("ts") or "", reverse=True)
    itog = {
        "ostalos": len(vidimye),
        "pro_dengi": sum(1 for r in vidimye if r["razdel"] in DENGI),
        "novyh_za_sutki": sum(1 for r in vidimye if (r.get("pervyi") or "") >= sutki),
        "zakryto_za_sutki": zakryto,
        "samoy_staroy": max((r["vozrast"] for r in vidimye), default=0),
        "ne_oshibka_skryto": sum(1 for r in stroki if r["ne_oshibka"]),
        "zhdut_resheniya": [r for r in stroki if r["seraya"] == "ждёт решения Бориса"],
        "ne_oshibka_za_nedelyu": ne_osh_ned,
    }
    return {"ts": seychas.isoformat(timespec="minutes"), "itog": itog, "razdely": razdely, "oshibki": oshibki,
            "sredniy_sync": _j(db.get_state("sredniy_sync") or "{}"),
            "udaleno_pomecheno": {t: len(v) for t, v in D.ud.items()}, "vyklyucheny": sorted(vykl),
            # для проверки после выкладки одним GET /api/proverka-mk: что решил синк про удалённое
            # (в т. ч. «пропало больше 10 % — не помечаю») и когда прошёл ночной шаг
            "udaleno_zhurnal": _spisok(db.get_state("mk_udaleno_zhurnal"))[-12:],
            "noch": _j(db.get_state("proverka_mk_noch") or "{}"),
            "raspisanie_provereno": str(_j(db.get_state("proverka_mk_raspisanie") or "{}").get("ts") or "")}


# ── кэш, фон, счётчик ─────────────────────────────────────────────────────

def _kesh() -> dict | None:
    k = _j(db.get_state("proverka_mk_kesh") or "")
    return k if isinstance(k, dict) and k.get("ts") else None


def peresschet(seychas: datetime | None = None) -> dict | None:
    """Пересчёт со записью строк и кэша; второй одновременный — пропускается."""
    if not _lock.acquire(blocking=False):
        return None
    try:
        res = sobrat(seychas, zapisat=True)
        db.set_state("proverka_mk_kesh", json.dumps(res, ensure_ascii=False, default=str))
        return res
    finally:
        _lock.release()


def dannye(max_minut: int = KESH_MINUT, seychas: datetime | None = None) -> dict:
    """Данные страницы: свежий кэш или пересчёт (страница к МойКлассу не обращается)."""
    seychas = seychas or _now()
    k = _kesh()
    if k and k["ts"] >= (seychas - timedelta(minutes=max_minut)).isoformat(timespec="minutes"):
        return k
    res = peresschet(seychas)
    return res or k or sobrat(seychas)


def schetchik(seychas: datetime | None = None) -> dict:
    """Число для пульта и /api/pult/tasks: {ostalos, novyh_za_sutki, ssylka}."""
    d = dannye(30, seychas)
    it = d.get("itog") or {}
    return {"ostalos": int(it.get("ostalos") or 0), "novyh_za_sutki": int(it.get("novyh_za_sutki") or 0),
            "pro_dengi": int(it.get("pro_dengi") or 0), "ts": d.get("ts"), "ssylka": "/proverka-mk"}


def stroka_dlya_borisa(seychas: datetime | None = None) -> str:
    """Одна строка в вечернее саммари Бориса."""
    d = dannye(30, seychas)
    it = d.get("itog") or {}
    s = (f"Проверка МК: осталось {it.get('ostalos', 0)}, про деньги {it.get('pro_dengi', 0)}, "
         f"самой старой {it.get('samoy_staroy', 0)} дн., за день +{it.get('novyh_za_sutki', 0)} / "
         f"−{it.get('zakryto_za_sutki', 0)}, «не ошибка» за неделю {len(it.get('ne_oshibka_za_nedelyu') or [])}")
    if it.get("zhdut_resheniya"):
        s += f"; ждёт решения: {len(it['zhdut_resheniya'])}"
    return s + ". app.kidsup.ru/proverka-mk"


# ── действия со страницы ──────────────────────────────────────────────────

def ne_oshibka(key: str, prichina: str, kto: str = "", seychas: datetime | None = None) -> dict:
    """«Не ошибка» с причиной: строка скрывается на 30 дней или до изменения данных.
    Только в разделах, где правило — догадка. ValueError — с понятной причиной."""
    prichina = (prichina or "").strip()
    if len(prichina) < 5:
        raise ValueError("Нужна причина своими словами: почему это не ошибка")
    seychas = seychas or _now()
    k = dannye(60, seychas)
    r = next((x for rz in k.get("razdely") or [] for x in rz.get("stroki") or [] if x.get("key") == key), None)
    if r is None:
        raise ValueError("Такой строки сейчас нет — обновите страницу")
    if r.get("pod") not in NE_OSHIBKA:
        raise ValueError("В этом разделе «не ошибка» не ставится: исправьте в МойКлассе")
    ts = seychas.isoformat(timespec="minutes")
    do = (seychas + timedelta(days=NE_OSHIBKA_DNEY)).isoformat(timespec="minutes")
    tekst = " · ".join(x for x in (r.get("rebenok"), r.get("gruppa"), r.get("kogda"), r.get("chto")) if x)[:300]
    with db.get_conn() as conn:
        _init(conn)
        conn.execute("INSERT OR REPLACE INTO proverka_mk_ne_oshibka (key, razdel, prichina, kto, ts, do, otpechatok, tekst) "
                     "VALUES (?,?,?,?,?,?,?,?)", (key, r.get("pod"), prichina[:300], (kto or "")[:40], ts, do,
                                                   r.get("otpechatok") or _otpechatok(r.get("fakty")), tekst))
    peresschet(seychas)
    return {"ok": True, "key": key, "do": do}


def zametka(key: str, tekst: str) -> dict:
    """Заметка к ключу строки — текст закрытого пункта пульта (что обещано семье и т. п.)."""
    key, tekst = (key or "").strip(), (tekst or "").strip()[:1000]
    if not key:
        raise ValueError("нужен ключ строки")
    with db.get_conn() as conn:
        _init(conn)
        cur = conn.execute("UPDATE proverka_mk_stroki SET zametka=? WHERE key=?", (tekst, key))
        if not cur.rowcount:
            conn.execute("INSERT INTO proverka_mk_stroki (key, razdel, pervyi, posledniy, zakryta, zametka, dannye) "
                         "VALUES (?,?,NULL,NULL,NULL,?,'{}')", (key, "", tekst))
    k = _kesh()
    if k:
        for rz in k.get("razdely") or []:
            for x in rz.get("stroki") or []:
                if x.get("key") == key:
                    x["zametka"] = tekst
        db.set_state("proverka_mk_kesh", json.dumps(k, ensure_ascii=False, default=str))
    return {"ok": True, "key": key}


_perepoverka = {"idet": False, "nachalo": "", "itog": None}


def perepoverit_fon() -> bool:
    """Кнопка «Перепроверить сейчас»: средний синк (только чтение МойКласса) и пересчёт.
    Ничего не отмечает. Чаще раза в 2 минуты синк не зовём — только пересчёт."""
    if _perepoverka["idet"]:
        return False
    _perepoverka.update(idet=True, nachalo=_now().isoformat(timespec="seconds"), itog=None)

    def rabota():
        try:
            from . import sync
            prosh = _j(db.get_state("sredniy_sync") or "{}")
            svezh = str(prosh.get("konec") or "") >= (datetime.now() - timedelta(minutes=2)).isoformat(timespec="seconds")
            if not svezh:
                try:
                    _perepoverka["itog"] = sync.sredniy_sync()
                except Exception as e:  # noqa: BLE001
                    log.exception("перепроверка: средний синк не прошёл")
                    _perepoverka["itog"] = {"ok": False, "prichina": str(e)[:160]}
            peresschet()
        except Exception:  # noqa: BLE001
            log.exception("перепроверка: пересчёт упал")
        finally:
            _perepoverka["idet"] = False
    threading.Thread(target=rabota, daemon=True, name="proverka-mk").start()
    return True


def kratko() -> dict:
    k = _kesh() or {}
    return {"idet": _perepoverka["idet"], "ts": k.get("ts"), "ostalos": (k.get("itog") or {}).get("ostalos"),
            "sredniy_sync": _perepoverka.get("itog")}


# ── ночью: явка за окно и расписание до 31.05 ─────────────────────────────

def _uroki_dnya(day: str) -> list[dict]:
    """Занятия дня из локальной базы в виде ответа API (для yavka.sverka без запроса к МойКлассу)."""
    out = []
    with db.get_conn() as conn:
        ud = db.udalennye("lessons", conn)
        for l in conn.execute("SELECT id, raw FROM lessons WHERE date=?", (day,)):
            if l["id"] in ud:
                continue
            raw = _j(l["raw"])
            raw["records"] = [_j(r["raw"]) | {"id": r["id"], "userId": r["user_id"]} for r in conn.execute(
                "SELECT id, user_id, raw FROM lesson_records WHERE lesson_id=?", (l["id"],))]
            out.append(raw)
    return out


def noch(seychas: datetime | None = None, s_raspisaniem: bool = True) -> dict:
    """Ночной шаг: сверка явки (yavka.sverka) за каждый день окна по локальным занятиям —
    опоздавший отчёт педагога (п. 5923) доходит до раздела 3; и последнее занятие
    каждой группы до 31.05 по живому API (только чтение) — для раздела 12в."""
    from . import yavka
    seychas = seychas or _now()
    d = seychas.date()
    lo = date.fromisoformat(pervoe_proshlogo_mesyaca(d))
    itog = {"yavka_dney": 0, "raspisanie": None}
    while lo < d:
        try:
            yavka.sverka(lo.isoformat(), lessons=_uroki_dnya(lo.isoformat()))
            itog["yavka_dney"] += 1
        except Exception:  # noqa: BLE001
            log.exception("ночная сверка явки за %s не прошла", lo)
        lo += timedelta(days=1)
    if s_raspisaniem:
        try:
            from . import sync
            from .moyklass_client import MoyklassClient
            mk = MoyklassClient(sync.get_api_key(), rps=sync.SREDNIY_RPS)
            try:
                uroki = mk.fetch_all("/v1/company/lessons", ["lessons"],
                                     params={"date[]": [d.isoformat(), RASPISANIE_DO]})
            finally:
                mk.close()
            posl = {}
            for l in uroki:
                if l.get("classId") and l.get("date"):
                    posl[str(l["classId"])] = max(posl.get(str(l["classId"]), ""), l["date"][:10])
            db.set_state("proverka_mk_raspisanie", json.dumps(
                {"ts": d.isoformat(), "do": RASPISANIE_DO, "posledniy": posl}, ensure_ascii=False))
            itog["raspisanie"] = len(posl)
        except Exception:  # noqa: BLE001
            log.exception("ночная проверка расписания до 31.05 не прошла")
    try:
        db.set_state("proverka_mk_noch", json.dumps({"ts": seychas.isoformat(timespec="minutes"), **itog}, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        log.exception("отметка ночного шага не сохранилась")
    peresschet(seychas)
    return itog


def noch_nuzhen(seychas: datetime | None = None) -> bool:
    """Пора ли запускать ночной шаг: в 22 часа — всегда; в остальное время — догон, если
    последний успешный шаг старше 26 часов или его не было вовсе (проверка 12.11: при
    выкладке после 23:00 страница сутки жила бы без пересчёта явки и без раздела 12в)."""
    seychas = seychas or _now()
    if seychas.hour == 22:
        return True
    ts = str(_j(db.get_state("proverka_mk_noch") or "{}").get("ts") or "")
    return ts < (seychas - timedelta(hours=26)).isoformat(timespec="minutes")
