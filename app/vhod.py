# -*- coding: utf-8 -*-
"""Личные входы на app.kidsup.ru.

12.11.2026, Борис: «да» личным логинам администраторов. Каждый входит под своим именем
на странице /vhod; сервер знает, кто сделал действие. Раньше был один общий логин admin
на всех и отдельный пароль владельца boris в сером окне браузера.

Кто пришёл, решает одна функция kto() — для всех способов входа:
  1. чужой фоновый запрос (Sec-Fetch-Site: cross-site, не переход по ссылке) — 403 всегда;
  2. режим без входа — только KIDSUP_BEZ_VHODA=1 при пустом APP_PASSWORD;
  3. новые способы: служебный ключ ассистента (Bearer или Basic klod:<ключ>), ключ
     автоматики самого сервера (живёт только в памяти процесса), метка входа через форму;
  4. старые входы, пока они включены: общий admin и прежний пароль владельца в окне —
     сравниваются так же, как раньше. Ошибка в новом коде или в vhody.db их не закрывает.

Пока владелец ничего не завёл (в настройках нет отметки vhod_zavedeny), всё работает
ровно как раньше: старые входы, серое окно браузера. Новое включается его действиями.

Хранение. Пароли — только соль + хеш (PBKDF2-SHA256, 600 000 кругов), служебные ключи —
SHA-256 (ключ — 43 случайных знака, медленный хеш ему не нужен). Всё это — в отдельном
файле data/vhody.db (права 0600); в /api/backup/db он не входит: та копирует только
kidsup.db. Рядом сервер держит vhody.kopiya.db — только учётные записи, без сеансов:
если основной файл пропал или испорчен, берётся она. Наружу копия не уезжает.
Метка входа в браузере — случайный номер; в базе — его SHA-256, сеанс строкой: выход и
«Выйти везде» действуют сразу, ключа подписи, который можно украсть, нет.

Пароли и ключи нигде не печатаются и не пишутся в журнал. Тело запросов в журнал не пишется.

Что делают другие исполнители плана: страницу /owner и управление входами (spisok,
sostoyanie, starye_vyklyuchit, консольная команда) — Б; права страниц владельца
(mozhno_vladelcu целиком, списки SLUZH_*, PEREHOD_ADMIN, X-Kak) — В.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException

from . import config, db

log = logging.getLogger("kidsup.vhod")

try:
    from zoneinfo import ZoneInfo
    MSK = ZoneInfo("Europe/Moscow")
except Exception:  # noqa: BLE001 — нет базы часовых поясов: Москва без перехода на летнее время
    MSK = timezone(timedelta(hours=3))

# --- постоянные ---------------------------------------------------------------

COOKIE = "__Host-vhod"            # метка входа: HttpOnly, Secure, SameSite=Lax, Path=/, без Domain
COOKIE_USTR = "__Host-ustr"       # метка устройства: «с этого браузера уже входили через форму»
KRUGI = 600_000                   # PBKDF2-SHA256
SROK_ZAPOMNIT = 30 * 86400        # «Запомнить на этом устройстве»
SROK_PREDEL = 90 * 86400          # дольше — только новым входом
SROK_USTR = 2 * 365 * 86400       # метка устройства
SROK_STARYH = 7 * 86400           # старые входы живут после первого заданного пароля или ключа
SROK_STAROGO_KLYUCHA = 86400      # прежний служебный ключ после выпуска нового
SROK_VREMENNOGO = 86400           # «временный вход на 24 часа»
BEZOPASNYE = ("GET", "HEAD", "OPTIONS")
_METKA = dict(httponly=True, secure=True, samesite="lax", path="/")

LYUDI = ("admin", "owner")                      # входят через форму
SLUZHEBNYE = ("sluzh", "vykladka")              # входят ключом
SLUZH_LOGINY = ("klod", "klod-vykladka")
AVTO_LOGIN = "avtomatika"
# Логины, которые нельзя выдать новому сотруднику.
ZANYATYE = frozenset({"admin", "boris", "klod", "klod-vykladka", "avtomatika", "vyhod", "owner", "root"})
LOGIN_RE = re.compile(r"^[a-z][a-z0-9-]{2,19}$")

# Шесть записей заводятся сами при первом запуске, без пароля и ключа: пока секрет не задан,
# войти такой записью нельзя. mk_id — номер сотрудника в МойКлассе (calltools.MANAGER_BY_NAME).
ZAPISI = (
    ("lena", "Лена", "admin", 202856),
    ("ira", "Ира", "admin", 232763),
    ("liza", "Лиза", "admin", 154181),
    ("boris", "Борис", "owner", 84116),
    ("klod", "Клод", "sluzh", None),
    ("klod-vykladka", "Клод (выкладка)", "vykladka", None),
)

# Права по адресам (раздел 5 плана). Пути — строки; сравнение «равен или начинается с
# записи + "/"». Списки заполняет исполнитель В; здесь — заготовки, чтобы имена были.
SLUZH_TOCHNO: frozenset = frozenset()        # адреса владельца, открытые ключу рутин (точно)
SLUZH_PREFIKS: tuple = ()                    # … и с продолжением
TOLKO_VYKLADKA: frozenset = frozenset()      # выкладка, перезапуск, копия базы, настройки
TOLKO_CHELOVEK: frozenset = frozenset()      # /owner, /owner/vhody, /settings — только человеку
PEREHOD_ADMIN: frozenset = frozenset()       # вновь закрытые от админов — общему admin пока можно
# Автоматика самого сервера (perepiska, nadezhda) ходит только сюда.
AVTO_PUTI = frozenset({("POST", "/api/plan/inbox"), ("GET", "/api/duty"), ("GET", "/api/dialogs")})

# Публичные домены (kidsup.ru и др.): там формы нет, /vhod отвечает 404. Заполняет main.py
# из своего PUBLIC_HOSTS, чтобы список был один.
PUBLICHNYE: set = set()

ZAPROS_CHUZHOY = "Запрос пришёл не со страницы app.kidsup.ru"
TEKST_NE_PODOSHLI = "Логин или пароль не подошли."


class NuzhenVhod(Exception):
    """Человек открыл страницу без входа — main.py отвечает 303 на форму входа."""

    def __init__(self, kuda: str):
        super().__init__(kuda)
        self.kuda = kuda


class Zanyat(Exception):
    """Сервер занят проверкой других паролей — попробовать чуть позже."""


# --- часы ------------------------------------------------------------------------

SDVIG = 0.0          # тесты двигают часы: vhod.SDVIG = …


def seychas() -> float:
    return time.time() + SDVIG


def _iso(t: float | None = None) -> str:
    return datetime.fromtimestamp(seychas() if t is None else t, MSK).strftime("%Y-%m-%dT%H:%M:%S")


def _iz_iso(s: str | None) -> float | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).strip())
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=MSK)
    return d.timestamp()


def do_chetyreh(t: float | None = None) -> float:
    """Ближайшие 4:00 по Москве: до них живёт вход без «Запомнить» и вход на общем компьютере."""
    d = datetime.fromtimestamp(seychas() if t is None else t, MSK)
    gr = d.replace(hour=4, minute=0, second=0, microsecond=0)
    if d >= gr:
        gr = (gr + timedelta(days=1)).replace(hour=4)
    return gr.timestamp()


def _sha(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


# --- файл vhody.db ----------------------------------------------------------------

SHEMA = """
CREATE TABLE IF NOT EXISTS vhody (
    login TEXT PRIMARY KEY, imya TEXT NOT NULL DEFAULT '', rol TEXT NOT NULL DEFAULT 'admin',
    hesh TEXT NOT NULL DEFAULT '', vkl INTEGER NOT NULL DEFAULT 1, sozdan TEXT, parol_ot TEXT,
    "do" TEXT, forma_vhod TEXT, posl_zapros TEXT, posl_adres TEXT,
    hesh_staryy TEXT, staryy_do TEXT, mk_id INTEGER);
CREATE TABLE IF NOT EXISTS seansy (
    id TEXT PRIMARY KEY, login TEXT NOT NULL, sozdan REAL NOT NULL, posl REAL NOT NULL,
    "do" REAL NOT NULL, zapomnit INTEGER NOT NULL DEFAULT 0, ustr TEXT, adres TEXT);
CREATE INDEX IF NOT EXISTS idx_seansy_login ON seansy(login);
CREATE TABLE IF NOT EXISTS ustroystva (
    id TEXT PRIMARY KEY, perviy REAL, posl REAL, loginy TEXT NOT NULL DEFAULT '[]',
    obshchiy INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS popytki (ts REAL NOT NULL, login TEXT NOT NULL DEFAULT '', adres TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS idx_popytki ON popytki(adres, login, ts);
CREATE TABLE IF NOT EXISTS hvost (
    den TEXT NOT NULL, login TEXT NOT NULL, adres TEXT NOT NULL, chem TEXT NOT NULL,
    n INTEGER NOT NULL DEFAULT 0, posl TEXT, PRIMARY KEY (den, login, adres, chem));
"""

_GOTOVO: set = set()                 # файлы, уже проверенные и заведённые в этом процессе
_ZAMOK_BAZY = threading.RLock()


def _put() -> Path:
    return Path(config.VHODY_DB)


def _put_kopii() -> Path:
    p = _put()
    return p.with_name(p.stem + ".kopiya" + (p.suffix or ".db"))


def _sozdat(p: Path) -> None:
    con = sqlite3.connect(p, timeout=30)
    try:
        con.executescript(SHEMA)
        ok = con.execute("PRAGMA quick_check").fetchone()
        if not ok or ok[0] != "ok":
            raise sqlite3.DatabaseError("quick_check: " + str(ok and ok[0]))
        sozdan = _iso()
        con.executemany("INSERT OR IGNORE INTO vhody (login, imya, rol, sozdan, mk_id) VALUES (?,?,?,?,?)",
                        [(lg, im, rl, sozdan, mk) for lg, im, rl, mk in ZAPISI])
        con.commit()
    finally:
        con.close()
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _iz_kopii(p: Path) -> bool:
    """Основной файл пропал или испорчен — восстановить учётные записи из копии рядом."""
    k = _put_kopii()
    if not k.exists() or k.stat().st_size == 0:
        return False
    try:
        src = sqlite3.connect(k, timeout=30)
        dst = sqlite3.connect(p, timeout=30)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        os.chmod(p, 0o600)
        log.warning("vhody.db восстановлен из копии рядом (сеансы не восстанавливаются — войти заново)")
        return True
    except Exception:  # noqa: BLE001
        log.exception("vhody.db: копия не читается")
        return False


def _podgotovit(p: Path) -> None:
    with _ZAMOK_BAZY:
        if str(p) in _GOTOVO and p.exists():
            return
        _GOTOVO.discard(str(p))
        if not p.exists() or p.stat().st_size == 0:
            _iz_kopii(p)
        try:
            _sozdat(p)
        except sqlite3.DatabaseError:
            log.error("vhody.db испорчен — откладываю его в сторону и беру копию")
            try:
                p.rename(p.with_name(p.name + ".isporchen-" + str(int(time.time()))))
            except OSError:
                log.exception("vhody.db: не отложить испорченный файл")
                raise
            _iz_kopii(p)
            _sozdat(p)
        _GOTOVO.add(str(p))


@contextmanager
def _baza():
    p = _put()
    if str(p) not in _GOTOVO or not p.exists():
        _podgotovit(p)
    con = sqlite3.connect(p, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _obnovit_kopiyu() -> None:
    """Копия рядом: только учётные записи (соль + хеш), без сеансов и попыток. Обновляется при
    каждом изменении входов; восстановление не воскрешает закрытые сеансы."""
    try:
        k = _put_kopii()
        tmp = k.with_name(k.name + ".tmp")
        if tmp.exists():
            tmp.unlink()
        with _baza() as c:
            stroki = [tuple(r) for r in c.execute("SELECT * FROM vhody")]
            stolbcy = [d[0] for d in c.execute("SELECT * FROM vhody LIMIT 0").description]
        con = sqlite3.connect(tmp)
        try:
            con.executescript(SHEMA)
            con.executemany(f"INSERT INTO vhody ({','.join(chr(34) + s + chr(34) for s in stolbcy)}) "
                            f"VALUES ({','.join('?' * len(stolbcy))})", stroki)
            con.commit()
        finally:
            con.close()
        os.chmod(tmp, 0o600)
        os.replace(tmp, k)
    except Exception:  # noqa: BLE001
        log.exception("vhody.kopiya.db не обновлена")


def _zapis(login: str):
    if not login:
        return None
    with _baza() as c:
        return c.execute("SELECT * FROM vhody WHERE login=?", (login,)).fetchone()


def _istekla(z) -> bool:
    """Временная запись (поле do) после своего срока не действует."""
    do = _iz_iso(z["do"]) if z is not None and z["do"] else None
    return do is not None and seychas() >= do


# --- настройки в основной базе ------------------------------------------------------

def _nastroyki(*klyuchi: str) -> dict:
    """Читаем прямо из settings, без запасного пути через переменные окружения
    (db.get_setting подставил бы KIDSUP_VHOD_… из окружения)."""
    with db.get_conn() as c:
        rows = c.execute(f"SELECT key, value FROM settings WHERE key IN ({','.join('?' * len(klyuchi))})",
                         klyuchi).fetchall()
    return {r["key"]: r["value"] for r in rows if r["value"]}


_POSL_STARYE = [True]     # последнее прочитанное значение — на случай сбоя чтения


def zavedeny() -> bool:
    """Входы уже заводились (первый заданный пароль или ключ)."""
    return bool(_nastroyki("vhod_zavedeny").get("vhod_zavedeny"))


def starye_vklyucheny() -> bool:
    """Работают ли старые входы (общий admin и прежний пароль владельца в окне браузера).
    Отметки vhod_zavedeny нет — да, как раньше. Есть — да, пока не наступил vhod_starye_do;
    записи о сроке нет — выключены. Сбой чтения — последнее прочитанное значение."""
    try:
        s = _nastroyki("vhod_zavedeny", "vhod_starye_do")
        if not s.get("vhod_zavedeny"):
            ok = True
        else:
            do = _iz_iso(s.get("vhod_starye_do"))
            ok = do is not None and seychas() < do
        _POSL_STARYE[0] = ok
        return ok
    except Exception:  # noqa: BLE001
        log.exception("vhod: не прочитать срок старых входов — беру прежнее значение")
        return _POSL_STARYE[0]


def otmetit_zavedeny() -> None:
    """Первый заданный пароль или ключ: отметка «входы заведены» и срок старых входов +7 дней.
    Повторно ничего не меняет. Срок пишется раньше отметки: отметка без срока значит
    «старые входы выключены»."""
    s = _nastroyki("vhod_zavedeny", "vhod_starye_do")
    if s.get("vhod_zavedeny"):
        return
    if not s.get("vhod_starye_do"):
        db.set_setting("vhod_starye_do", _iso(seychas() + SROK_STARYH))
    db.set_setting("vhod_zavedeny", "1")


def otkryto() -> bool:
    """Режим без входа: только KIDSUP_BEZ_VHODA=1 и пустой APP_PASSWORD (тесты, свой компьютер)."""
    return bool(config.BEZ_VHODA) and not config.APP_PASSWORD


# --- пароли и ключи ------------------------------------------------------------------

_SEMAFOR = threading.BoundedSemaphore(2)       # не больше двух расчётов хеша одновременно


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _norm_parol(parol: str) -> bytes:
    return unicodedata.normalize("NFC", parol or "").encode("utf-8")


def hesh_parolya(parol: str) -> str:
    sol = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", _norm_parol(parol), sol, KRUGI, 32)
    return f"pbkdf2_sha256${KRUGI}${_b64(sol)}${_b64(h)}"


_PUSTOY_HESH = f"pbkdf2_sha256${KRUGI}${_b64(bytes(16))}${_b64(bytes(32))}"


def parol_veren(parol: str, hesh: str | None) -> bool:
    """Пароль совпал с хешем. При hesh=None (незнакомый логин, служебная запись) считаем
    пустой хеш — ответ по времени такой же, как на неверный пароль настоящего входа.
    Вокруг расчёта — семафор на два места; ждать дольше 20 с — Zanyat."""
    nastoyashchiy = bool(hesh) and str(hesh).startswith("pbkdf2_sha256$")
    try:
        alg, krugi, sol, h = (hesh if nastoyashchiy else _PUSTOY_HESH).split("$")
        krugi, sol, h = int(krugi), base64.b64decode(sol), base64.b64decode(h)
    except (ValueError, TypeError):
        nastoyashchiy = False
        alg, krugi, sol, h = _PUSTOY_HESH.split("$")
        krugi, sol, h = int(krugi), base64.b64decode(sol), base64.b64decode(h)
    if not _SEMAFOR.acquire(timeout=20):
        raise Zanyat()
    try:
        got = hashlib.pbkdf2_hmac("sha256", _norm_parol(parol), sol, krugi, len(h) or 32)
    finally:
        _SEMAFOR.release()
    return hmac.compare_digest(got, h) and nastoyashchiy


def hesh_klyucha(klyuch: str) -> str:
    return "sha256$" + hashlib.sha256((klyuch or "").encode("utf-8")).hexdigest()


def _ravny(a: str, b: str) -> bool:
    """Сравнение без утечки по времени — байтами: строки с русскими буквами
    secrets.compare_digest не принимает (было бы 500)."""
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))


# Те же клавиши в русской и в английской раскладке (ЙЦУКЕН → QWERTY), включая Shift.
_RU = "ёйцукенгшщзхъфывапролджэячсмитьбю.ЁЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,\"№;:?/"
_EN = "`qwertyuiop[]asdfghjkl;'zxcvbnm,./~QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>?@#$^&|"
assert len(_RU) == len(_EN) == len(set(_RU))
_RASKLADKA = str.maketrans(_RU, _EN)
_KIRILLICA = re.compile(r"[А-Яа-яЁё]")


def est_kirillica(s: str) -> bool:
    return bool(_KIRILLICA.search(s or ""))


def na_latinicu(s: str) -> str:
    """Пароль набран в русской раскладке — те же клавиши в английской."""
    return (s or "").translate(_RASKLADKA)


def parol_goden(parol: str, login: str, imya: str = "") -> str:
    """"" — пароль годится; иначе причина отказа словами (для страницы владельца)."""
    parol = parol or ""
    if login in SLUZH_LOGINY:
        return "служебной записи обычный пароль задать нельзя"
    if len(parol) < 10:
        return "короче 10 знаков"
    if len(set(parol)) < 5:
        return "слишком простой: мало разных знаков"
    p = _norm(parol)
    if p == _norm(login):
        return "совпадает с логином"
    if imya and p == _norm(imya):
        return "совпадает с именем"
    starye = [config.APP_PASSWORD or "", db.get_setting("owner_password") or ""]
    if any(s and _ravny(parol, s) for s in starye):
        return "совпадает со старым паролем"
    return ""


def _norm(s: str) -> str:
    return unicodedata.normalize("NFC", (s or "").strip()).casefold().replace("ё", "е")


def login_po_vvodu(vvod: str) -> str:
    """«Лена», «лена», «LENA», «lena» → lena. Сравнение в Python через casefold, не в SQL:
    lower() в SQLite русские буквы не понижает. Незнакомое — ""."""
    v = _norm(vvod)
    if not v:
        return ""
    with _baza() as c:
        rows = c.execute("SELECT login, imya FROM vhody").fetchall()
    for r in rows:
        if v == _norm(r["login"]) or (r["imya"] and v == _norm(r["imya"])):
            return r["login"]
    return ""


# --- откуда запрос -----------------------------------------------------------------

def adres(request) -> str:
    """Адрес посетителя. За Caddy (запрос пришёл с 127.0.0.1) — последний адрес из
    X-Forwarded-For: его ставит сам Caddy, остальные мог написать кто угодно.
    X-Real-IP не читаем — его подставляет любой клиент."""
    host = request.client.host if request.client else ""
    if host in ("127.0.0.1", "::1"):
        xff = (request.headers.get("x-forwarded-for") or "").split(",")[-1].strip()
        if xff:
            return xff[:64]
    return host[:64]


def _host(request) -> str:
    return (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(":")[0].lower()


def publichnyy(request) -> bool:
    return _host(request) in PUBLICHNYE


def chuzhoy_fon(request) -> bool:
    """Фоновый запрос с чужого сайта (картинка, fetch, форма из скрипта) — не переход по ссылке."""
    return ((request.headers.get("sec-fetch-site") or "").lower() == "cross-site"
            and (request.headers.get("sec-fetch-mode") or "").lower() != "navigate")


def _istochnik(request) -> str:
    """"svoy" — со своей страницы, "chuzhoy" — с чужой, "net" — ни Sec-Fetch-Site, ни Origin
    (скрипт, а не браузер)."""
    sfs = (request.headers.get("sec-fetch-site") or "").lower()
    if sfs:
        return "svoy" if sfs in ("same-origin", "none") else "chuzhoy"
    origin = request.headers.get("origin")
    if origin is not None:
        return "svoy" if origin.strip() == config.SVOY_ADRES else "chuzhoy"
    return "net"


def svoy_zapros(request) -> bool:
    """Изменяющий запрос пришёл со своей страницы: Sec-Fetch-Site same-origin/none, а без
    него — Origin, равный постоянному https://app.kidsup.ru (не заголовку Host)."""
    return _istochnik(request) == "svoy"


def _brauzer(request) -> bool:
    ua = request.headers.get("user-agent") or ""
    return bool(request.headers.get("sec-fetch-mode") or request.headers.get("sec-fetch-site")
                or "Mozilla/" in ua or request.cookies.get(COOKIE_USTR))


# --- кто пришёл ------------------------------------------------------------------------

def _k(login, imya, rol, sposob, seans="") -> dict:
    return {"login": login, "imya": imya, "rol": rol, "sposob": sposob, "seans": seans}


def _klyuch_iz(avt: str, credentials) -> tuple[dict | None, bool]:
    """Служебный ключ: Authorization: Bearer <ключ> или Basic <служебный логин>:<ключ>.
    → (вошедший или None, погашен ли сейчас прежний ключ — тогда обновить копию файла)."""
    if avt[:7].lower() == "bearer ":
        klyuch, login = avt[7:].strip(), ""
    elif credentials is not None and credentials.username in SLUZH_LOGINY:
        klyuch, login = credentials.password, credentials.username
    else:
        return None, False
    if not klyuch:
        return None, False
    h = hesh_klyucha(klyuch)
    t = seychas()
    with _baza() as c:
        for r in c.execute("SELECT * FROM vhody WHERE rol IN ('sluzh', 'vykladka') AND vkl=1").fetchall():
            if login and r["login"] != login:
                continue
            novyy = bool(r["hesh"]) and hmac.compare_digest(r["hesh"], h)
            sdo = _iz_iso(r["staryy_do"])
            staryy = (bool(r["hesh_staryy"]) and hmac.compare_digest(r["hesh_staryy"], h)
                      and sdo is not None and t < sdo)
            if not (novyy or staryy) or _istekla(r):
                continue
            pogas = False
            if novyy and r["hesh_staryy"]:
                # прежний ключ гаснет при первом удачном запросе новым
                c.execute("UPDATE vhody SET hesh_staryy=NULL, staryy_do=NULL WHERE login=?", (r["login"],))
                pogas = True
            return _k(r["login"], r["imya"], r["rol"], "klyuch"), pogas
    return None, False


def _klyuch(request, credentials):
    k, pogas = _klyuch_iz(request.headers.get("authorization") or "", credentials)
    if pogas:
        request.state.vhod_kopiya = True
    return k


_VNUTR: list = [None]          # ключ автоматики — только в памяти процесса сервера


def vnutr_sozdat() -> None:
    """Зовёт main.py при запуске сервера: ключ живёт до перезапуска, на диск не пишется."""
    if _VNUTR[0] is None:
        _VNUTR[0] = secrets.token_urlsafe(32)


def vnutr_klyuch() -> str | None:
    """Ключ автоматики, или None — если это не процесс сервера (контейнер, скрипт)."""
    return _VNUTR[0]


def _avto(credentials):
    k = _VNUTR[0]
    if k and credentials is not None and credentials.username == AVTO_LOGIN and _ravny(credentials.password, k):
        return _k(AVTO_LOGIN, "Автоматика", "avto", "vnutr")
    return None


def _seans(request):
    """Метка входа → (вошедший, продление метки или None)."""
    m = request.cookies.get(COOKIE)
    if not m:
        return None
    sid = _sha(m)
    t = seychas()
    with _baza() as c:
        r = c.execute('SELECT s.id, s.login, s.sozdan, s.posl, s."do" AS s_do, s.zapomnit, '
                      'v.imya, v.rol, v.vkl, v."do" AS v_do FROM seansy s JOIN vhody v ON v.login = s.login '
                      'WHERE s.id=?', (sid,)).fetchone()
        if r is None:
            return None
        v_do = _iz_iso(r["v_do"])
        if r["s_do"] <= t or not r["vkl"] or r["rol"] not in LYUDI or (v_do is not None and t >= v_do):
            c.execute("DELETE FROM seansy WHERE id=?", (sid,))
            return None
        prodlit = None
        if r["zapomnit"] and t - (r["posl"] or 0) >= 86400:
            do = min(t + SROK_ZAPOMNIT, r["sozdan"] + SROK_PREDEL)
            c.execute('UPDATE seansy SET "do"=?, posl=? WHERE id=?', (do, t, sid))
            prodlit = (m, max(0, int(do - t)))
    return _k(r["login"], r["imya"], r["rol"], "forma", sid), prodlit


def _ustr_id(request) -> str:
    """SHA-256 метки устройства, если такое устройство сервер знает; иначе ""."""
    tok = request.cookies.get(COOKIE_USTR)
    if not tok:
        return ""
    uid = _sha(tok)
    with _baza() as c:
        return uid if c.execute("SELECT 1 FROM ustroystva WHERE id=?", (uid,)).fetchone() else ""


def _ustr_izvestno(request) -> bool:
    return bool(_ustr_id(request))


def _staryy_boris_v_okne() -> bool:
    """Прежний пароль владельца в окне — пока не выполнено разом: у boris задан новый пароль
    и ключ выкладки сделал хотя бы один удачный запрос. Сбой — принимается (как раньше)."""
    try:
        with _baza() as c:
            b = c.execute("SELECT hesh FROM vhody WHERE login='boris'").fetchone()
            v = c.execute("SELECT posl_zapros FROM vhody WHERE login='klod-vykladka'").fetchone()
        return not (b is not None and b["hesh"] and v is not None and v["posl_zapros"])
    except Exception:  # noqa: BLE001
        log.exception("vhod: не проверить прежний пароль владельца — принимаю, как раньше")
        return True


def _sverit_staryy(user: str, pw: str, s_proverkoy=True):
    """Старые входы — тем же сравнением, что раньше, но байтами."""
    app_pw = config.APP_PASSWORD
    own = db.get_setting("owner_password")
    if app_pw and _ravny(user, config.APP_USER) and _ravny(pw, app_pw):
        # как раньше: пока пароль владельца не задан и входы не заводились, вошедший — владелец
        vladelec = not own
        if vladelec:
            try:
                vladelec = not zavedeny()
            except Exception:  # noqa: BLE001
                vladelec = False
        return _k("admin", "", "owner" if vladelec else "admin", "staryy")
    if own and _ravny(user, "boris") and _ravny(pw, own) and (not s_proverkoy or _staryy_boris_v_okne()):
        return _k("boris", "Борис", "owner", "staryy")
    return None


def _starye(request, credentials):
    if credentials is None or not starye_vklyucheny():
        return None
    try:
        if _ustr_izvestno(request):
            return None          # с этого браузера уже входили через форму: общий вход здесь не годится
    except Exception:  # noqa: BLE001
        log.exception("vhod: метка устройства не проверена")
    user, pw = credentials.username, credentials.password
    adr = adres(request)
    schet = ("admin" if _ravny(user, config.APP_USER or "admin") else
             "boris" if _ravny(user, "boris") else "")
    try:
        p = _pauza_okna(schet, adr)
    except Exception:  # noqa: BLE001
        log.exception("vhod: счётчик попыток не прочитан")
        p = 0
    if p > 0:
        raise HTTPException(429, tekst_pauzy(p), headers={"Retry-After": str(int(p) + 1)})
    k = _sverit_staryy(user, pw)
    if k is None and est_kirillica(pw):
        k = _sverit_staryy(user, na_latinicu(pw))
    if k is not None:
        return k
    try:
        _promah_okna(schet, adr, pw)
    except Exception:  # noqa: BLE001
        log.exception("vhod: промах не записан")
    return None


def kto(request, credentials):
    """Кто пришёл: словарь {login, imya, rol, sposob, seans} или None.
    Бросает только 403 на чужой запрос и 429 на паузу старого входа после промахов."""
    if chuzhoy_fon(request):
        raise HTTPException(403, ZAPROS_CHUZHOY)
    if otkryto():
        return _k("", "", "owner", "otkryto")
    izm = request.method.upper() not in BEZOPASNYE
    ist = _istochnik(request)
    if izm and ist == "chuzhoy":
        raise HTTPException(403, ZAPROS_CHUZHOY)
    try:
        k = _klyuch(request, credentials) or _avto(credentials)
        if k is not None:
            return k
        s = _seans(request)
        if s is not None:
            k, prodlit = s
            if izm and ist != "svoy":
                raise HTTPException(403, ZAPROS_CHUZHOY)
            if prodlit:
                request.state.prodlit = prodlit
            return k
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 — ошибка в новом коде или в vhody.db старым входам не мешает
        log.exception("vhod: новая проверка входа не сработала — смотрю старые входы")
    return _starye(request, credentials)


def kto_po_staromu(credentials):
    """Прямой вызов check_auth(credentials) без request: только то, что видно из окна."""
    if otkryto():
        return _k("", "", "owner", "otkryto")
    if credentials is None:
        return None
    try:
        k, pogas = _klyuch_iz("", credentials)
        if pogas:
            _obnovit_kopiyu()
        k = k or _avto(credentials)
        if k is not None:
            return k
    except Exception:  # noqa: BLE001
        log.exception("vhod: ключ без request не проверен")
    if not starye_vklyucheny():
        return None
    return _sverit_staryy(credentials.username, credentials.password)


def forma_vklyuchena(request) -> bool:
    """Форма для этого браузера включена: старые входы выключены или с браузера уже
    входили через форму (есть метка устройства). Отдельного переключателя нет."""
    try:
        if not starye_vklyucheny():
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        return _ustr_izvestno(request)
    except Exception:  # noqa: BLE001
        return False


def ssylka_vhoda(request) -> str:
    """/vhod?next=<путь и запрос, закодированные>."""
    q = request.url.query
    return "/vhod?next=" + quote(request.url.path + ("?" + q if q else ""), safe="/")


def otkaz(request):
    """Входа нет. Человек открыл страницу в браузере, где форма включена, — переход на форму
    (даже если браузер прислал старый пароль). Запрос со страницы — 401 с X-Vhod (vhod.js
    уведёт на форму). Скрипт или браузер, где форма ещё не включена, — 401 с окном Basic, как
    раньше. Публичные домены — как раньше."""
    basic = HTTPException(401, "Требуется авторизация", headers={"WWW-Authenticate": "Basic"})
    if publichnyy(request):
        raise basic
    if not forma_vklyuchena(request):
        raise basic
    put = request.url.path
    stranica = (request.method.upper() in ("GET", "HEAD")
                and "text/html" in (request.headers.get("accept") or "")
                and not put.startswith(("/api/", "/export/")))
    if stranica:
        raise NuzhenVhod(ssylka_vhoda(request))
    if _brauzer(request):
        raise HTTPException(401, "Вход истёк — войдите снова: app.kidsup.ru/vhod", headers={"X-Vhod": "nuzhen"})
    raise basic


_PUSTO = _k("", "", "", "")


def iz(request) -> dict:
    """Вошедший из request.state.kto; нет — словарь с пустыми полями."""
    k = getattr(getattr(request, "state", None), "kto", None) if request is not None else None
    return k if k else dict(_PUSTO)


def _vinitelnyy(imya: str) -> str:
    """«Клод за Лену»: Лена → Лену, Ира → Иру, Борис → Бориса."""
    s = (imya or "").strip()
    if not s:
        return s
    if s.endswith("а"):
        return s[:-1] + "у"
    if s.endswith("я"):
        return s[:-1] + "ю"
    if s.endswith("ь"):
        return s[:-1] + "я"
    if re.search(r"[бвгджзклмнпрстфхцчшщ]$", s):
        return s + "а"
    return s


def podpis(request, ot_imeni: str = "") -> str:
    """Имя для отметки: человеку — всегда его имя, что бы ни стояло в запросе; ключу рутин —
    «Клод» или «Клод за Лену»; автоматике — «Автоматика»; общему входу — ot_imeni, как раньше."""
    k = iz(request)
    s, r = k.get("sposob"), k.get("rol")
    if s == "forma":
        return k.get("imya") or ot_imeni
    if s == "klyuch":
        if r == "vykladka":
            return "Клод (выкладка)"
        return f"Клод за {_vinitelnyy(ot_imeni)}" if (ot_imeni or "").strip() else "Клод"
    if s == "vnutr":
        return "Автоматика"
    return ot_imeni


def rol_menyu(k: dict | None) -> str:
    """Роль для меню шага 1: "owner" или "admin"."""
    return "owner" if k and k.get("rol") in ("owner", "vykladka") else "admin"


def mozhno_vladelcu(k: dict | None, metod: str = "", put: str = "") -> bool:
    """Пускать ли на адрес под OWNER_AUTH. Заготовка исполнителя А: владелец и оба служебных
    ключа — да, остальные — нет. Правила раздела 5 (списки, четыре адреса выкладки,
    X-Kak, аварийное окно) дописывает исполнитель В."""
    return bool(k) and k.get("rol") in ("owner", "sluzh", "vykladka")


def avto_mozhno(request) -> bool:
    return (request.method.upper(), request.url.path) in AVTO_PUTI


# --- сеансы и устройства ----------------------------------------------------------------

def seans_otkryt(login: str, zapomnit: bool, request) -> tuple[str, int | None]:
    """→ (номер для метки, срок метки в секундах или None — до закрытия браузера).
    Новую метку устройства (если её не было) кладёт в request.state.novoe_ustr."""
    t = seychas()
    adr = adres(request)
    tok = request.cookies.get(COOKIE_USTR) or ""
    uid = _sha(tok) if tok else ""
    with _baza() as c:
        u = c.execute("SELECT * FROM ustroystva WHERE id=?", (uid,)).fetchone() if uid else None
        if u is None:
            tok = secrets.token_urlsafe(32)
            uid = _sha(tok)
            c.execute("INSERT INTO ustroystva (id, perviy, posl, loginy, obshchiy) VALUES (?,?,?,?,0)",
                      (uid, t, t, json.dumps([login])))
            obshchiy = False
            request.state.novoe_ustr = tok
        else:
            try:
                loginy = list(json.loads(u["loginy"] or "[]"))
            except ValueError:
                loginy = []
            obshchiy = bool(u["obshchiy"])
            if login not in loginy:
                loginy.append(login)
                if len(loginy) > 1 and not obshchiy:
                    # второй другой человек на том же браузере: общий компьютер — запомненные
                    # на нём сеансы живут только до 4:00
                    obshchiy = True
                    c.execute('UPDATE seansy SET "do"=MIN("do", ?), zapomnit=0 WHERE ustr=? AND zapomnit=1',
                              (do_chetyreh(t), uid))
            c.execute("UPDATE ustroystva SET posl=?, loginy=?, obshchiy=? WHERE id=?",
                      (t, json.dumps(loginy[-20:]), int(obshchiy), uid))
        zapomnit = bool(zapomnit) and not obshchiy
        nomer = secrets.token_urlsafe(32)
        do = t + SROK_ZAPOMNIT if zapomnit else do_chetyreh(t)
        c.execute('INSERT INTO seansy (id, login, sozdan, posl, "do", zapomnit, ustr, adres) VALUES (?,?,?,?,?,?,?,?)',
                  (_sha(nomer), login, t, t, do, int(zapomnit), uid, adr))
        c.execute("UPDATE vhody SET forma_vhod=? WHERE login=?", (_iso(t), login))
        c.execute('DELETE FROM seansy WHERE "do" < ?', (t,))
    return nomer, (SROK_ZAPOMNIT if zapomnit else None)


def seans_zakryt(nomer: str) -> None:
    """Выход: сеанс стирается на сервере — скопированная метка больше не действует."""
    if nomer:
        with _baza() as c:
            c.execute("DELETE FROM seansy WHERE id=?", (_sha(nomer),))


def seansy_zakryt(login: str, krome: str = "") -> None:
    """«Выйти везде». krome — id сеанса (iz(request)["seans"]), который оставить."""
    with _baza() as c:
        c.execute("DELETE FROM seansy WHERE login=? AND id<>?", (login, krome or ""))


def ustroystvo_obshchee(request) -> bool:
    """Браузер помечен общим компьютером — галочку «Запомнить» не показываем."""
    tok = request.cookies.get(COOKIE_USTR)
    if not tok:
        return False
    try:
        with _baza() as c:
            r = c.execute("SELECT obshchiy FROM ustroystva WHERE id=?", (_sha(tok),)).fetchone()
        return bool(r and r["obshchiy"])
    except Exception:  # noqa: BLE001
        return False


def ustr_lichnoe(uid: str) -> None:
    """Владелец вернул устройству «личное» (для блока «Входы»)."""
    with _baza() as c:
        c.execute("UPDATE ustroystva SET obshchiy=0, loginy='[]' WHERE id=?", (uid,))


def postavit_metki(resp, request, nomer: str | None = None, srok: int | None = None) -> None:
    """Метка входа и (впервые) метка устройства в ответ."""
    if nomer:
        resp.set_cookie(COOKIE, nomer, max_age=srok, **_METKA)
    tok = getattr(request.state, "novoe_ustr", None)
    if tok:
        resp.set_cookie(COOKIE_USTR, tok, max_age=SROK_USTR, **_METKA)


def ubrat_metku(resp) -> None:
    resp.delete_cookie(COOKIE, **_METKA)


# --- перебор ---------------------------------------------------------------------------

_ZAMOK = threading.Lock()                # память: последние промахи, «последний запрос», хвост
_ZAMOK_POPYTOK = threading.Lock()        # пауза + запись попытки — одним шагом
_NEUD: dict = {}                         # адрес → время последнего промаха (короткий путь без базы)
_NEUD_ZAGRUZHEN = [False]
_POVTOR: dict = {}                       # (адрес, логин, отпечаток пароля) → когда засчитан
_PROC_KLYUCH = secrets.token_bytes(32)   # отпечатки неверных паролей — только в памяти процесса
_SOOBSHCHENO: dict = {}                  # логин → когда сообщили Борису о 50 промахах


def tekst_pauzy(sek: float) -> str:
    n = max(1, int((sek + 59) // 60))
    slovo = "минуту" if n % 10 == 1 and n % 100 != 11 else (
        "минуты" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "минут")
    return f"Слишком много попыток. Подождите {n} {slovo} или позвоните Борису."


def pauza(login: str, adres_: str, ustr: str = "") -> float:
    """Сколько секунд ждать (0 — можно пробовать).
    Один логин с одного адреса: 5 промахов подряд (за час) — минута, 10 — 5 минут, 15 — 15.
    Один адрес, любые логины: 20 промахов за 15 минут — 15 минут, но только браузерам без
    метки устройства (стойка и телефоны в Wi-Fi центра — один адрес)."""
    t = seychas()
    p = 0.0
    with _baza() as c:
        if login:
            rows = c.execute("SELECT ts FROM popytki WHERE login=? AND adres=? AND ts>? ORDER BY ts DESC",
                             (login, adres_, t - 3600)).fetchall()
            n = len(rows)
            if n >= 5:
                dl = 900 if n >= 15 else 300 if n >= 10 else 60
                p = max(p, rows[0]["ts"] + dl - t)
        znakom = bool(ustr) and c.execute("SELECT 1 FROM ustroystva WHERE id=?", (ustr,)).fetchone() is not None
        if not znakom:
            rows = c.execute("SELECT ts FROM popytki WHERE adres=? AND ts>? ORDER BY ts DESC LIMIT 20",
                             (adres_, t - 1800)).fetchall()
            if len(rows) >= 20 and rows[0]["ts"] - rows[19]["ts"] <= 900:
                p = max(p, rows[0]["ts"] + 900 - t)
    return max(0.0, p)


def popytka(login: str, adres_: str) -> int:
    """Записать промах (форма пишет его ДО проверки пароля, удача стирает)."""
    t = seychas()
    with _baza() as c:
        rid = c.execute("INSERT INTO popytki (ts, login, adres) VALUES (?,?,?)", (t, login or "", adres_)).lastrowid
        if secrets.randbelow(50) == 0:
            c.execute("DELETE FROM popytki WHERE ts < ?", (t - 86400,))
    with _ZAMOK:
        _NEUD[adres_] = t
    return rid


def uspeh(login: str, adres_: str) -> None:
    """Удачный вход стирает промахи этого логина с этого адреса."""
    if login:
        with _baza() as c:
            c.execute("DELETE FROM popytki WHERE login=? AND adres=?", (login, adres_))


def snyat_pauzu(login: str) -> None:
    """Кнопка владельца «Снять паузу»: промахи логина стираются со всех адресов."""
    with _baza() as c:
        c.execute("DELETE FROM popytki WHERE login=?", (login,))


def _pauza_okna(login: str, adres_: str) -> float:
    """Пауза для старого входа в окне. Короткий путь: с адреса за час не было промахов —
    в базу не ходим (окно шлёт пароль с каждым запросом страницы)."""
    t = seychas()
    with _ZAMOK:
        if not _NEUD_ZAGRUZHEN[0]:
            _NEUD_ZAGRUZHEN[0] = True
            zagruzit = True
        else:
            zagruzit = False
    if zagruzit:
        with _baza() as c:
            for r in c.execute("SELECT adres, MAX(ts) AS ts FROM popytki WHERE ts>? GROUP BY adres", (t - 3600,)):
                with _ZAMOK:
                    _NEUD[r["adres"]] = max(_NEUD.get(r["adres"], 0), r["ts"])
    with _ZAMOK:
        posl = _NEUD.get(adres_)
    if posl is None or t - posl > 3600:
        return 0.0
    return pauza(login, adres_, "")


def _promah_okna(login: str, adres_: str, pw: str) -> None:
    """Неверный пароль в окне. Один и тот же неверный пароль с одного адреса засчитывается
    один раз в час: старая вкладка с устаревшим паролем не должна запереть весь центр."""
    t = seychas()
    otp = hmac.new(_PROC_KLYUCH, (pw or "").encode("utf-8"), hashlib.sha256).hexdigest()
    kl = (adres_, login, otp)
    with _ZAMOK:
        bylo = _POVTOR.get(kl)
        if bylo is not None and t - bylo < 3600:
            return
        _POVTOR[kl] = t
        if len(_POVTOR) > 5000:
            for k_, v_ in list(_POVTOR.items()):
                if t - v_ >= 3600:
                    del _POVTOR[k_]
    popytka(login, adres_)
    _posle_promaha(login)


def _posle_promaha(login: str) -> None:
    """Один логин с разных адресов: 50 промахов за час — сообщение Борису (вход не закрывается)."""
    if not login:
        return
    t = seychas()
    with _baza() as c:
        n = c.execute("SELECT COUNT(*) FROM popytki WHERE login=? AND ts>?", (login, t - 3600)).fetchone()[0]
    if n < 50:
        return
    with _ZAMOK:
        if t - _SOOBSHCHENO.get(login, 0) < 3600:
            return
        _SOOBSHCHENO[login] = t
    soobshchit_borisu(f"app.kidsup.ru: за час {n} неверных паролей ко входу «{login}» с разных адресов. "
                      "Вход не закрыт. Если это не вы и не админы — задайте этому входу новый пароль на /owner.",
                      chto=f"много неверных паролей: {login}")


def voyti(request, vvod: str, parol: str) -> dict:
    """Проверка формы /vhod → {"login": …, "zapis": …} при удаче;
    {"pauza": сек} при отказе (0 — просто не подошло); {"zanyat": True} — сервер занят.
    Попытка пишется ДО проверки пароля (иначе пачка одновременных запросов обходит счётчик)
    и стирается при удаче. Ответ на незнакомый логин и неверный пароль одинаков по тексту и
    времени. Пароль в русской раскладке — один раз пробуем те же клавиши в английской."""
    adr = adres(request)
    try:
        ustr = _ustr_id(request)
    except Exception:  # noqa: BLE001
        ustr = ""
    login = login_po_vvodu(vvod)
    z = _zapis(login) if login else None
    chelovek = z is not None and z["rol"] in LYUDI and bool(z["vkl"]) and not _istekla(z)
    schet = login if chelovek else ""
    with _ZAMOK_POPYTOK:
        p = pauza(schet, adr, ustr)
        if p > 0:
            return {"pauza": p}
        rid = popytka(schet, adr)
    varianty = [parol or ""] + ([na_latinicu(parol)] if est_kirillica(parol) else [])
    ok = False
    try:
        for v in varianty:
            if chelovek and z["hesh"]:
                ok = parol_veren(v, z["hesh"])
            elif chelovek and login == "boris":
                # новый пароль владельцу ещё не задан — в форме годится прежний (owner_password)
                parol_veren(v, None)
                own = db.get_setting("owner_password")
                ok = bool(own) and _ravny(v, own)
            else:
                parol_veren(v, None)
            if ok:
                break
    except Zanyat:
        with _baza() as c:
            c.execute("DELETE FROM popytki WHERE rowid=?", (rid,))
        return {"zanyat": True}
    if ok:
        uspeh(schet, adr)
        return {"login": login, "zapis": z}
    try:
        _posle_promaha(schet)
    except Exception:  # noqa: BLE001
        log.exception("vhod: проверка «50 промахов» не сработала")
    return {"pauza": 0}


# --- управление (зовёт только обработчик владельца — исполнитель Б) ---------------------------

def zadat_parol(login: str, parol: str, krome: str = "") -> str:
    """Задать пароль человеку. "" — готово, иначе причина отказа словами.
    Сеансы этого человека закрываются (кроме krome — id сеанса, который оставить: владелец,
    сменивший пароль себе, не выходит), промахи стираются. Первый заданный пароль ставит
    отметку «входы заведены» и срок старых входов +7 дней."""
    z = _zapis(login)
    if z is None:
        return "нет такого входа"
    if z["rol"] not in LYUDI:
        return "служебной записи обычный пароль задать нельзя"
    prichina = parol_goden(parol, login, z["imya"])
    if prichina:
        return prichina
    if login == "boris" and z["hesh"] and parol_veren(parol, z["hesh"]):
        return "совпадает со старым паролем"
    h = hesh_parolya(parol)
    with _baza() as c:
        c.execute("UPDATE vhody SET hesh=?, parol_ot=? WHERE login=?", (h, _iso(), login))
        c.execute("DELETE FROM seansy WHERE login=? AND id<>?", (login, krome or ""))
        c.execute("DELETE FROM popytki WHERE login=?", (login,))
    _obnovit_kopiyu()
    otmetit_zavedeny()
    return ""


def vydat_klyuch(login: str, srazu: bool = False) -> str:
    """Выпустить служебный ключ: 43 случайных знака, показываются один раз; хранится SHA-256.
    Прежний ключ живёт до первого удачного запроса новым, но не дольше 24 часов;
    srazu=True (подозрение на утечку) — гаснет сразу. Не служебный логин — ValueError."""
    z = _zapis(login)
    if z is None or z["rol"] not in SLUZHEBNYE:
        raise ValueError("ключ выдаётся только служебной записи")
    klyuch = secrets.token_urlsafe(32)
    t = seychas()
    with _baza() as c:
        if z["hesh"] and not srazu:
            c.execute("UPDATE vhody SET hesh_staryy=?, staryy_do=? WHERE login=?",
                      (z["hesh"], _iso(t + SROK_STAROGO_KLYUCHA), login))
        else:
            c.execute("UPDATE vhody SET hesh_staryy=NULL, staryy_do=NULL WHERE login=?", (login,))
        c.execute("UPDATE vhody SET hesh=?, parol_ot=? WHERE login=?", (hesh_klyucha(klyuch), _iso(t), login))
    _obnovit_kopiyu()
    otmetit_zavedeny()
    return klyuch


def vklyuchit(login: str, vkl: bool) -> str:
    """«Отключить вход» (увольнение): вход закрыт сразу, сеансы стёрты, история действий
    остаётся. «Включить» возвращает. Свой вход владельцу не отключить."""
    z = _zapis(login)
    if z is None:
        return "нет такого входа"
    if login == "boris" and not vkl:
        return "вход владельца отключить нельзя"
    with _baza() as c:
        c.execute("UPDATE vhody SET vkl=? WHERE login=?", (1 if vkl else 0, login))
        if not vkl:
            c.execute("DELETE FROM seansy WHERE login=?", (login,))
    _obnovit_kopiyu()
    return ""


def dobavit(login: str, imya: str, sutki: bool = False) -> str:
    """«Добавить вход»: роль всегда «админ»; sutki=True — временный, на 24 часа
    (аварийный вход вместо общего пароля). "" — готово, иначе причина словами."""
    login = (login or "").strip()
    imya = " ".join((imya or "").split())[:30]
    if not LOGIN_RE.match(login):
        return "логин — латиницей, 3–20 знаков: буквы, цифры, дефис; начинается с буквы"
    if login in ZANYATYE:
        return "этот логин занят"
    if not imya:
        return "нужно имя"
    if _zapis(login) is not None:
        return "такой вход уже есть"
    if login_po_vvodu(imya):
        return "вход с таким именем уже есть"
    with _baza() as c:
        c.execute('INSERT INTO vhody (login, imya, rol, sozdan, "do") VALUES (?,?,?,?,?)',
                  (login, imya, "admin", _iso(), _iso(seychas() + SROK_VREMENNOGO) if sutki else None))
    _obnovit_kopiyu()
    return ""


# --- журнал «кто что сделал» (основная база, таблица deystviya) ------------------------------

_ZHURNAL_GOTOV: set = set()


def _zhurnal_init(c) -> None:
    kl = str(config.DB_PATH)
    if kl in _ZHURNAL_GOTOV:
        return
    c.execute("""CREATE TABLE IF NOT EXISTS deystviya (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, login TEXT, imya TEXT, rol TEXT, sposob TEXT,
        metod TEXT, put TEXT, kod INTEGER, chto TEXT, adres TEXT, rutina TEXT, vazhnoe INTEGER DEFAULT 0)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_deystviya_ts ON deystviya(ts)")
    _ZHURNAL_GOTOV.add(kl)


def imya_v_zhurnal(k: dict) -> str:
    s, r = k.get("sposob"), k.get("rol")
    if s == "forma":
        return k.get("imya") or k.get("login") or ""
    if s == "klyuch":
        return "Клод (выкладка)" if r == "vykladka" else "Клод"
    if s == "vnutr":
        return "Автоматика"
    if s == "staryy":
        return "Борис (прежний пароль)" if k.get("login") == "boris" else "общий вход"
    if s == "otkryto":
        return "без входа"
    return k.get("imya") or ""


def _rutina(request) -> str:
    return re.sub(r"[^\w .:()-]", "", request.headers.get("x-rutina") or "")[:40] if request is not None else ""


def sled(request, chto: str = "", vazhnoe: int = 0, kod: int | None = None) -> None:
    """Строка в журнал deystviya: кто, каким входом, метод, путь (без запроса и тела), код, что."""
    k = iz(request)
    try:
        metod = request.method if request is not None else ""
        put = request.url.path if request is not None else ""
        adr = adres(request) if request is not None else ""
    except Exception:  # noqa: BLE001
        metod, put, adr = "", "", ""
    with db.get_conn() as c:
        _zhurnal_init(c)
        c.execute("INSERT INTO deystviya (ts, login, imya, rol, sposob, metod, put, kod, chto, adres, rutina, vazhnoe) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (_iso(), k.get("login") or "", imya_v_zhurnal(k), k.get("rol") or "", k.get("sposob") or "",
                   metod, put[:300], kod, (chto or "")[:300], adr, _rutina(request), int(vazhnoe)))


def _otpravit(tekst: str) -> None:
    from . import autopilot
    autopilot._wa(db.get_setting("digest_phone") or "", tekst)


def soobshchit_borisu(tekst: str, chto: str = "") -> None:
    """Сообщение владельцу (autopilot._wa на digest_phone) и строка «Важное» в журнале.
    Отправка — в фоне; её сбой ничего не ломает. Секретов в тексте быть не должно."""
    try:
        with db.get_conn() as c:
            _zhurnal_init(c)
            c.execute("INSERT INTO deystviya (ts, login, imya, rol, sposob, metod, put, kod, chto, adres, rutina, vazhnoe) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
                      (_iso(), "", "сервер", "", "", "", "", None, (chto or tekst)[:300], "", ""))
    except Exception:  # noqa: BLE001
        log.exception("vhod: строка «Важное» не записана")
    if not (db.get_setting("digest_phone") or ""):
        return

    def _f():
        try:
            _otpravit(tekst)
        except Exception:  # noqa: BLE001
            log.exception("vhod: сообщение владельцу не отправлено")
    threading.Thread(target=_f, daemon=True).start()


# --- слой после ответа (main._vhod_sloy) ------------------------------------------------------

_POSL: dict = {}             # логин → когда «последний запрос» писался в базу
_HVOST: dict = {}            # (день, логин, адрес, чем) → [накоплено, когда писали]
POSL_AVTO = [0.0]            # когда автоматика сервера последний раз ходила на сайт


def sloy_zagolovki(request, resp) -> None:
    """Без базы: продление метки входа; для HTML на служебном хосте — не кешировать и не
    показывать в рамке на чужом сайте."""
    pr = getattr(request.state, "prodlit", None)
    if pr:
        resp.set_cookie(COOKIE, pr[0], max_age=pr[1], **_METKA)
    if not publichnyy(request) and (resp.headers.get("content-type") or "").startswith("text/html"):
        if "cache-control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store"
        if "x-frame-options" not in resp.headers:
            resp.headers["X-Frame-Options"] = "DENY"


def sloy_zapis(request, kod: int) -> None:
    """С базой (зовётся в потоке): изменяющий запрос — строка в журнал; «последний запрос»
    не чаще раза в минуту на логин; запросы старым входом — в хвост, тоже раз в минуту."""
    k = iz(request)
    t = seychas()
    if request.method.upper() not in BEZOPASNYE:
        try:
            sled(request, getattr(request.state, "chto", "") or "", kod=kod)
        except Exception:  # noqa: BLE001
            log.exception("vhod: строка журнала не записана")
    if getattr(request.state, "vhod_kopiya", False):
        _obnovit_kopiyu()
    try:
        s = k.get("sposob")
        adr = adres(request)
        if s in ("forma", "klyuch"):
            with _ZAMOK:
                pisat = t - _POSL.get(k["login"], -1e18) >= 60
                if pisat:
                    _POSL[k["login"]] = t
            if pisat:
                with _baza() as c:
                    c.execute("UPDATE vhody SET posl_zapros=?, posl_adres=? WHERE login=?", (_iso(t), adr, k["login"]))
        elif s == "vnutr":
            POSL_AVTO[0] = t
        elif s == "staryy":
            ua = request.headers.get("user-agent") or ""
            chem = "браузер" if (request.headers.get("sec-fetch-mode") or "Mozilla/" in ua) else "скрипт"
            kl = (_iso(t)[:10], k["login"], adr, chem)
            with _ZAMOK:
                e = _HVOST.setdefault(kl, [0, -1e18])
                e[0] += 1
                pisat = t - e[1] >= 60
                n = e[0]
                if pisat:
                    e[0], e[1] = 0, t
            if pisat:
                with _baza() as c:
                    c.execute("INSERT INTO hvost (den, login, adres, chem, n, posl) VALUES (?,?,?,?,?,?) "
                              "ON CONFLICT(den, login, adres, chem) DO UPDATE SET n = n + excluded.n, posl = excluded.posl",
                              (*kl, n, _iso(t)))
    except Exception:  # noqa: BLE001
        log.exception("vhod: «последний запрос» не записан")


def zapusk() -> None:
    """Запуск процесса сервера (main.py): ключ автоматики в памяти, файл входов, журнал."""
    vnutr_sozdat()
    try:
        with _baza():
            pass
        with db.get_conn() as c:
            _zhurnal_init(c)
    except Exception:  # noqa: BLE001
        log.exception("vhod: файл входов не подготовлен — старые входы работают как раньше")


# --- страница /vhod -----------------------------------------------------------------------------

def bezopasnyy_next(n: str) -> str:
    """Куда вернуть после входа: путь с запросом, только внутри сайта. «//…», «/\\…», обратная
    косая черта или управляющие знаки (браузер выкидывает табуляцию: «/\\t/чужой» = «//чужой»)
    — на «/»."""
    n = (n or "").strip()
    if (not n.startswith("/") or n.startswith(("//", "/\\")) or "\\" in n or len(n) > 2000
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in n)):
        return "/"
    if n.split("?", 1)[0].rstrip("/") in ("/vhod", "/vyhod"):
        return "/"
    return n


def _e(s) -> str:
    import html
    return html.escape(str(s or ""), quote=True)


_STIL = """
:root{--ink:#312783;--blue:#1DA7E0;--muted:#5F6478;--line:#E2E0EE;--bad:#B3261E;--paper:#FAFAFD}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);
font:17px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:420px;margin:0 auto;padding:28px 18px 40px}
h1{font-size:22px;margin:0 0 18px;display:flex;align-items:center;gap:10px}
h1 img{width:34px;height:34px}
.kartochka{background:#fff;border:1px solid var(--line);border-radius:16px;padding:20px}
label{display:block;font-weight:600;margin:14px 0 6px}
input[type=text],input[type=password]{width:100%;font:inherit;padding:12px 14px;border:1.5px solid #C9C6DD;
border-radius:12px;background:#fff;color:inherit}
input:focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 3px rgba(29,167,224,.2)}
.pw{display:flex;gap:8px}.pw input{flex:1}
.glaz{font:inherit;font-size:15px;padding:0 12px;border:1.5px solid #C9C6DD;border-radius:12px;background:#fff;
color:var(--ink);cursor:pointer;white-space:nowrap}
.gal{display:flex;gap:10px;align-items:flex-start;font-weight:400;margin-top:16px}
.gal input{width:22px;height:22px;margin:1px 0 0;flex:none}
.knopka{display:block;width:100%;margin-top:20px;font:inherit;font-weight:700;padding:13px;border:0;
border-radius:12px;background:var(--ink);color:#fff;cursor:pointer;text-align:center;text-decoration:none}
.knopka.vtor{background:#fff;color:var(--ink);border:1.5px solid var(--ink)}
.pod{color:var(--muted);font-size:14px;margin:8px 0 0}
.oshibka{background:#FDECEA;color:var(--bad);border-radius:12px;padding:12px 14px;margin:0 0 6px}
.caps{color:var(--bad);font-size:14px;margin:6px 0 0}
.imena{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 4px}
.imena button{font:inherit;padding:8px 16px;border-radius:999px;border:1.5px solid var(--blue);background:#fff;
color:var(--ink);cursor:pointer}
form{margin:0}
"""


def voshedshiy_po_metke(request) -> dict | None:
    """Кто вошёл через форму в этом браузере (для страницы входа и выхода); сбой — None."""
    try:
        s = _seans(request)
        return s[0] if s else None
    except Exception:  # noqa: BLE001
        log.exception("vhod: метка входа не проверена")
        return None


def stranica(next_: str = "", login: str = "", oshibka: str = "", podskazka: bool = False,
             kto_voshel: dict | None = None, galochka: bool = True, prodolzhit: str = "") -> str:
    """HTML страницы входа. next_ — куда вернуть после входа (уже проверенный путь);
    prodolzhit — куда ведёт «Продолжить» у вошедшего. Пароли в страницу не попадают никогда."""
    nxt = _e(next_)
    golova = (f"<!doctype html><html lang=ru><head><meta charset=utf-8>"
              f"<meta name=viewport content='width=device-width,initial-scale=1'>"
              f"<meta name=robots content='noindex, nofollow'><title>Вход — KidsUP</title>"
              f"<link rel=icon type='image/svg+xml' href='/static/logo.svg'><style>{_STIL}</style></head><body><main>"
              f"<h1><img src='/static/logo.svg' alt=''> Вход на app.kidsup.ru</h1>")
    if kto_voshel:
        kuda = _e(prodolzhit or next_ or "/")
        return (golova + f"<div class=kartochka><p style='margin:0'>Вы вошли как <b>{_e(kto_voshel.get('imya'))}</b>.</p>"
                f"<a class=knopka href='{kuda}'>Продолжить</a>"
                f"<form method=post action='/vyhod'><input type=hidden name=next value='{nxt}'>"
                f"<button class='knopka vtor' type=submit>Выйти / сменить</button></form></div></main></body></html>")
    osh = f"<p class=oshibka role=alert>{_e(oshibka)}</p>" if oshibka else ""
    if podskazka:
        osh += "<p class=pod>Проверьте раскладку (русская или английская) и не включён ли Caps Lock.</p>"
    zabyt = "try{sessionStorage.removeItem(ZH)}catch(e){}" if oshibka else ""
    gal = ("<label class=gal><input type=checkbox name=zapomnit value=1> "
           "<span>Запомнить на этом устройстве<br><span class=pod>только на своём телефоне — не на общем ноутбуке</span>"
           "</span></label>") if galochka else ""
    telo = f"""<div class=kartochka>
{osh}
<div id=imena class=imena hidden></div>
<form method=post action="/vhod" id=forma>
<input type=hidden name=next value="{nxt}">
<label for=login>Логин</label>
<input type=text id=login name=login value="{_e(login)}" autocomplete=username autocapitalize=none
 autocorrect=off spellcheck=false required>
<p class=pod>Ваше имя — можно по-русски</p>
<label for=parol>Пароль</label>
<div class=pw><input type=password id=parol name=parol autocomplete=current-password required>
<button type=button class=glaz id=glaz aria-controls=parol aria-pressed=false>Показать</button></div>
<p class=caps id=caps hidden>Включён Caps Lock</p>
{gal}
<button class=knopka type=submit>Войти</button>
</form>
<p class=pod>Забыли пароль — позвоните Борису: он задаст новый за минуту.</p>
</div></main>
<script>
(function(){{
  var KL='kidsup_vhod_imena', ZH='kidsup_vhod_zhdu';
  var login=document.getElementById('login'), parol=document.getElementById('parol');
  var box=document.getElementById('imena'), imena=[];
  try{{imena=JSON.parse(localStorage.getItem(KL)||'[]')}}catch(e){{}}
  if(Array.isArray(imena)&&imena.length){{
    box.hidden=false;
    imena.slice(0,6).forEach(function(n){{
      var b=document.createElement('button'); b.type='button'; b.textContent=n;
      b.onclick=function(){{login.value=n; parol.focus();}};
      box.appendChild(b);
    }});
  }}
  {zabyt}
  if(!login.value&&!imena.length)login.focus(); else parol.focus();
  document.getElementById('forma').addEventListener('submit',function(){{
    try{{sessionStorage.setItem(ZH,'1')}}catch(e){{}}
  }});
  var glaz=document.getElementById('glaz');
  glaz.onclick=function(){{
    var vid=parol.type==='password'; parol.type=vid?'text':'password';
    glaz.textContent=vid?'Скрыть':'Показать'; glaz.setAttribute('aria-pressed',vid?'true':'false'); parol.focus();
  }};
  var caps=document.getElementById('caps');
  function c(e){{if(e.getModifierState)caps.hidden=!e.getModifierState('CapsLock');}}
  parol.addEventListener('keydown',c); parol.addEventListener('keyup',c);
}})();
</script></body></html>"""
    return golova + telo
