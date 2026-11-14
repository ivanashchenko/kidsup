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

Управление входами — только владелец, блок «Входы» на /owner (blok_vladelca,
deystvie_vladelca): каждое действие подтверждается его паролем. Запасной путь владельца —
консольная команда на сервере: python3 -m app.vhod parol boris.

Права страниц владельца (раздел 5 плана) — mozhno_vladelcu() и списки SLUZH_*, TOLKO_*,
PEREHOD_ADMIN: владельцу через форму — всё, кроме выкладки; ключу рутин — только список;
личному админу — страница «Только для владельца». Заголовок X-Kak: admin понижает вошедшего
до личного админа (проверка после выкладки). Пока входы не заведены, для общего admin и
прежнего boris ничего не меняется.
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
import sys
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
# --- права (раздел 5 плана) -------------------------------------------------------------
# Адреса владельца (OWNER_AUTH), открытые ключу рутин. Список собран по описи рутин и
# помощников ассистента; всё остальное под OWNER_AUTH ключу рутин — 403, и отказ виден
# владельцу на /owner строкой «Клоду отказано». Сравнение: путь равен записи (SLUZH_TOCHNO)
# либо равен записи или начинается с «запись/» (SLUZH_PREFIKS) — так /api/ads/vk не
# открывает /api/ads/vk-users-list и /api/ads/vk/image.
SLUZH_TOCHNO: frozenset = frozenset({
    "/api/vhod/sostoyanie",                                   # состояние входов без секретов
    "/api/pedagogi/sostoyanie",                               # ключи педагогов: состояние без секретов (14.11.2026)
    "/api/boris-docs", "/api/boris-docs/remove",              # страницы владельцу («Места в группах», «Сверка оплат»)
    "/api/admin-docs", "/api/admin-docs/remove",              # списки-задания админам
    "/api/hooppy/post", "/api/hooppy/posty", "/api/hooppy/udalit",   # «Посты из фото и видео педагогов»
    "/api/mkweb/history", "/api/mkweb/history/start", "/api/mkweb/history/result",   # «Контроль работы админов в МойКлассе»
    "/api/tbank/get", "/api/komtet/cheki",                    # «Сверка оплат»
    "/api/ads/vk", "/api/ads/direct",                         # «Вечернее саммари»
    "/api/crm/raw-user",                                      # разборы карточек
    "/api/istochniki",                                        # источники заявок для вечерней сводки
})
SLUZH_PREFIKS: tuple = (
    "/api/doc-state",        # состояние документов
    "/boris/docs",           # чтение страниц владельца
    "/export/raw",           # «Места в группах», «Сверка оплат», снимки
    "/kadry/file",           # «Фото-явка»
)
SLUZH_TOLKO_CHTENIE: tuple = ("/boris/docs", "/export/raw", "/kadry/file")   # из них ключу рутин — только GET
# Выкладка кода, перезапуск, копия базы, настройки: только ключ выкладки (и прежний пароль
# владельца, пока он действует). Владельцу через форму — 403: из браузера код не выкладывается.
TOLKO_VYKLADKA: frozenset = frozenset({"/api/deploy", "/api/restart", "/api/backup/db", "/api/settings"})
OKNO_VYKLADKI: frozenset = frozenset({"/api/deploy", "/api/restart"})   # их ключ рутин получает в аварийное окно
# Страницы настроек — только человеку-владельцу: служебным ключам 403.
# 14.11.2026: /owner/pedagogi — блок «Ключи педагогов» (app/pedklyuchi.py), те же правила, что у /owner/vhody.
TOLKO_CHELOVEK: frozenset = frozenset({"/owner", "/owner/vhody", "/owner/pedagogi", "/settings", "/settings/test"})
# Вновь закрытые от админов 12.11.2026 (список 5.2). До этого дня они были открыты общему
# входу admin, и в день выкладки для него ничего не меняется: общий admin проходит по ним,
# пока ключ рутин ни разу не сработал (у klod пуст posl_zapros). После первого запроса ключом
# рутин — 403. Личному админу (вход через форму) они закрыты сразу. Во втором выпуске список
# убирается вместе со старыми входами.
# Отчёты о самих админах (/api/audit, /api/istochniki, /api/sverka-chestnosti) в списке нет:
# их закрыла от общего входа ещё выкатка шага 1 (12.11, поправка Д2 в nav_testy), и общему
# admin они отвечают 401, как до личных входов.
PEREHOD_ADMIN: frozenset = frozenset({
    "/export/leads.csv", "/export/lessons.csv", "/export/attendance.csv",
    "/api/audit/run", "/api/nabor/analiz",
    "/clicks", "/api/clicks/summary",
    "/api/istochniki/spravochnik",
})
PEREHOD_PREFIKS: tuple = ("/export/raw", "/kadry/file")
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
CREATE TABLE IF NOT EXISTS otmetki (klyuch TEXT PRIMARY KEY, znachenie TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS pedagog_klyuchi (
    id INTEGER PRIMARY KEY AUTOINCREMENT, vid TEXT NOT NULL DEFAULT 'lichnyy', imya TEXT NOT NULL DEFAULT '',
    mk_id INTEGER, hesh TEXT NOT NULL DEFAULT '', hesh_prezhniy TEXT, vydan TEXT, posl TEXT,
    vkl INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS pedagog_vhody (
    den TEXT NOT NULL, vid TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0, posl TEXT, PRIMARY KEY (den, vid));
"""
# 14.11.2026, ключи педагогов (app/pedklyuchi.py): pedagog_klyuchi — кому выдан личный ключ
# (имя и id сотрудника МойКласса), SHA-256 ключа, когда выдан, когда последний раз открывал,
# включён ли; hesh_prezhniy — отпечатки прежних ключей этой строки (по ним старая ссылка
# отличается от перебора; войти ими нельзя); строка vid='obshchiy' — общий ключ после того,
# как владелец его сменил или выключил. pedagog_vhody — сколько раз за день страницы открывали общим ключом. Лежат здесь,
# а не в основной базе: kidsup.db возвращается с ночной копией, и восстановление из неё
# оживило бы отменённые ключи.
KOPIYA_TABLICY = ("pedagog_klyuchi",)     # кроме vhody и otmetki, целиком уходят в копию рядом

TAYMAUT = 5                          # сколько секунд ждать запертый файл (было 30: вход не должен висеть)
SBOY_PAUZA = 60                      # после неудачной подготовки файла — минуту не пробовать заново
ZAPERT_PAUZA = 10                    # файл оказался заперт дольше TAYMAUT — 10 секунд не ждать его заново

_GOTOVO: set = set()                 # файлы, уже проверенные и заведённые в этом процессе
_ZAMOK_BAZY = threading.RLock()
_SBOY: dict = {}                     # файл → когда его не удалось подготовить (time.monotonic)
_ZAPERT: dict = {}                   # файл → когда он оказался заперт дольше TAYMAUT
_ZHALOBY: dict = {}                  # текст жалобы → [когда писали в лог, сколько раз промолчали]


class FaylNedostupen(sqlite3.OperationalError):
    """Файл входов недавно не удалось подготовить — минуту не пробуем заново (без ожиданий
    и без полных трассировок в логе на каждый запрос)."""


def _zhaloba(tekst: str) -> None:
    """Запись об ошибке в лог — не чаще раза в минуту на один текст. Когда vhody.db
    непригоден, каждый запрос под старым входом натыкается на него несколько раз; без этого
    журнал службы раздувался бы (а если причина — кончившийся диск, это только хуже)."""
    t = time.monotonic()
    with _ZAMOK_BAZY:
        b = _ZHALOBY.get(tekst)
        if b is not None and t - b[0] < 60:
            b[1] += 1
            return
        propushcheno = b[1] if b is not None else 0
        _ZHALOBY[tekst] = [t, 0]
    hvost = f" (и ещё {propushcheno} раз — не записаны)" if propushcheno else ""
    oshibka = sys.exc_info()[1]
    if oshibka is None or isinstance(oshibka, FaylNedostupen):
        log.error("%s%s%s", tekst, hvost, f": {oshibka}" if oshibka is not None else "")
    else:
        log.exception(tekst + hvost)


def _put() -> Path:
    return Path(config.VHODY_DB)


def _put_kopii() -> Path:
    p = _put()
    return p.with_name(p.stem + ".kopiya" + (p.suffix or ".db"))


def _sozdat(p: Path) -> None:
    con = sqlite3.connect(p, timeout=TAYMAUT)
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
        src = sqlite3.connect(k, timeout=TAYMAUT)
        dst = sqlite3.connect(p, timeout=TAYMAUT)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        os.chmod(p, 0o600)
        log.warning("vhody.db восстановлен из копии рядом (сеансы не восстанавливаются — войти заново)")
        return True
    except Exception:  # noqa: BLE001
        _zhaloba("vhody.db: копия не читается")
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
                _zhaloba("vhody.db: не отложить испорченный файл")
                raise
            _iz_kopii(p)
            _sozdat(p)
        _GOTOVO.add(str(p))


@contextmanager
def _baza():
    p = _put()
    if str(p) not in _GOTOVO or not p.exists():
        bylo = _SBOY.get(str(p))
        if bylo is not None and time.monotonic() - bylo < SBOY_PAUZA:
            raise FaylNedostupen("файл входов недавно не удалось подготовить — следующая попытка через минуту")
        try:
            _podgotovit(p)
        except Exception:
            _SBOY[str(p)] = time.monotonic()
            raise
        _SBOY.pop(str(p), None)
    bylo = _ZAPERT.get(str(p))
    if bylo is not None and time.monotonic() - bylo < ZAPERT_PAUZA:
        raise FaylNedostupen("файл входов заперт — следующая попытка через несколько секунд")
    con = sqlite3.connect(p, timeout=TAYMAUT)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except sqlite3.OperationalError as e:
        # файл заперт дольше TAYMAUT: каждый запрос проверки входа обращается к нему несколько
        # раз — без этой отметки он ждал бы столько же раз по TAYMAUT секунд
        if "locked" in str(e) and not isinstance(e, FaylNedostupen):
            _ZAPERT[str(p)] = time.monotonic()
        raise
    finally:
        con.close()


def _obnovit_kopiyu() -> None:
    """Копия рядом: только учётные записи (соль + хеш) и отметки, без сеансов и попыток. Обновляется при
    каждом изменении входов; восстановление не воскрешает закрытые сеансы.
    14.11.2026: туда же — ключи педагогов (KOPIYA_TABLICY, только хеши): без них пропажа
    основного файла оставила бы всех педагогов с недействительными ссылками."""
    try:
        k = _put_kopii()
        tmp = k.with_name(k.name + ".tmp")
        if tmp.exists():
            tmp.unlink()
        with _baza() as c:
            celikom = {}
            for tab in ("vhody",) + KOPIYA_TABLICY:
                celikom[tab] = ([d[0] for d in c.execute(f"SELECT * FROM {tab} LIMIT 0").description],
                                [tuple(r) for r in c.execute(f"SELECT * FROM {tab}")])
            otmetki = [tuple(r) for r in c.execute("SELECT klyuch, znachenie FROM otmetki")]
        con = sqlite3.connect(tmp)
        try:
            con.executescript(SHEMA)
            for tab, (stolbcy, stroki) in celikom.items():
                con.executemany(f"INSERT INTO {tab} ({','.join(chr(34) + s + chr(34) for s in stolbcy)}) "
                                f"VALUES ({','.join('?' * len(stolbcy))})", stroki)
            con.executemany("INSERT OR REPLACE INTO otmetki (klyuch, znachenie) VALUES (?,?)", otmetki)
            con.commit()
        finally:
            con.close()
        os.chmod(tmp, 0o600)
        os.replace(tmp, k)
    except Exception:  # noqa: BLE001
        _zhaloba("vhody.kopiya.db не обновлена")


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


_POSL_STARYE = [True]     # последнее прочитанное значение — на случай сбоя чтения обоих хранилищ

# Отметки «входы заведены», срок старых входов и «старые входы отключены» лежат в ДВУХ местах:
# в settings основной базы (kidsup.db — она возвращается с ночной копией) и в таблице otmetki
# файла входов (vhody.db — он в копию не входит). Старые входы включены, только если ОБА
# хранилища говорят «да». Иначе основная база, восстановленная из копии, снятой до заведения
# входов (или сбой её чтения сразу после перезапуска), снова открыла бы общий пароль admin,
# хотя личные пароли давно заданы. Расхождение чинится само — в строгую сторону.
OTMETKI = ("vhod_zavedeny", "vhod_starye_do", "vhod_starye_vykl")


def _otmetki_fayla() -> dict:
    with _baza() as c:
        return {r["klyuch"]: r["znachenie"] for r in c.execute("SELECT klyuch, znachenie FROM otmetki")
                if r["klyuch"] in OTMETKI and r["znachenie"]}


def _otmetku_v_fayl(klyuch: str, znachenie: str) -> None:
    with _baza() as c:
        c.execute("INSERT INTO otmetki (klyuch, znachenie) VALUES (?,?) "
                  "ON CONFLICT(klyuch) DO UPDATE SET znachenie=excluded.znachenie", (klyuch, znachenie))


def _otmetka(klyuch: str, znachenie: str) -> None:
    """Записать отметку в оба хранилища. Сбой записи в файл входов не мешает: при следующей
    проверке starye_vklyucheny() сведёт хранилища сама."""
    db.set_setting(klyuch, znachenie)
    try:
        _otmetku_v_fayl(klyuch, znachenie)
        _obnovit_kopiyu()
    except Exception:  # noqa: BLE001
        _zhaloba("vhod: отметка не записана в файл входов — сведу при следующей проверке")


def _starye_po(o: dict) -> bool:
    """Что говорит одно хранилище: работают ли старые входы."""
    if o.get("vhod_starye_vykl"):
        return False
    if not o.get("vhod_zavedeny"):
        return True
    do = _iz_iso(o.get("vhod_starye_do"))
    return do is not None and seychas() < do


_SVEDENO = [-1e18]        # когда владельцу сообщали о восстановленных отметках (time.monotonic)


def _svesti_otmetki(s: dict, f: dict) -> None:
    """Хранилища разошлись — привести оба к строгому: отметка есть хоть в одном → есть в обоих;
    срок — меньший из двух. Основная база потеряла отметку «входы заведены» (её восстановили
    из старой копии) — строка «Важное» и сообщение владельцу."""
    nado = {}
    for k in ("vhod_zavedeny", "vhod_starye_vykl"):
        v = s.get(k) or f.get(k)
        if v:
            nado[k] = v
    sroki = [x for x in (s.get("vhod_starye_do"), f.get("vhod_starye_do")) if x and _iz_iso(x) is not None]
    if sroki:
        nado["vhod_starye_do"] = min(sroki, key=_iz_iso)
    # срок раньше отметки: отметка без срока значит «старые входы выключены»
    for k in ("vhod_starye_do", "vhod_starye_vykl", "vhod_zavedeny"):
        if k in nado and s.get(k) != nado[k]:
            db.set_setting(k, nado[k])
        if k in nado and f.get(k) != nado[k]:
            _otmetku_v_fayl(k, nado[k])
    _obnovit_kopiyu()
    if f.get("vhod_zavedeny") and not s.get("vhod_zavedeny"):
        log.warning("vhod: в основной базе не было отметки «входы заведены» — восстановлена из файла входов")
        t = time.monotonic()
        if t - _SVEDENO[0] < 3600:           # владельцу — не чаще раза в час
            return
        _SVEDENO[0] = t
        soobshchit_borisu("app.kidsup.ru: в основной базе пропала отметка о личных входах (похоже, базу "
                          "восстановили из старой копии). Отметка возвращена: общий пароль admin заново не "
                          "открылся. Проверьте блок «Входы» на /owner.",
                          chto="отметки входов восстановлены из файла входов (основная база была без них)")
    else:
        log.warning("vhod: отметки входов в основной базе и в файле входов расходились — сведены")


def zavedeny() -> bool:
    """Входы уже заводились (первый заданный пароль или ключ) — по любому из двух хранилищ."""
    if _nastroyki("vhod_zavedeny").get("vhod_zavedeny"):
        return True
    try:
        return bool(_otmetki_fayla().get("vhod_zavedeny"))
    except Exception:  # noqa: BLE001
        return False


def starye_vklyucheny() -> bool:
    """Работают ли старые входы (общий admin и прежний пароль владельца в окне браузера).
    Отметки vhod_zavedeny нет — да, как раньше. Есть — да, пока не наступил vhod_starye_do;
    записи о сроке нет или стоит vhod_starye_vykl — выключены. Смотрим оба хранилища (settings
    основной базы и otmetki файла входов): включены, только если оба говорят «да». Одно не
    читается — решает второе; не читаются оба — последнее прочитанное значение."""
    try:
        s = _nastroyki(*OTMETKI)
    except Exception:  # noqa: BLE001
        _zhaloba("vhod: не прочитать срок старых входов в основной базе — смотрю файл входов")
        s = None
    try:
        f = _otmetki_fayla()
    except Exception:  # noqa: BLE001
        _zhaloba("vhod: не прочитать отметки в файле входов — решает основная база")
        f = None
    if s is None and f is None:
        return _POSL_STARYE[0]
    if s is not None and f is not None and s != f:
        try:
            _svesti_otmetki(s, f)
        except Exception:  # noqa: BLE001
            _zhaloba("vhod: отметки входов не сведены")
    ok = all(_starye_po(o) for o in (s, f) if o is not None)
    _POSL_STARYE[0] = ok
    return ok


def otmetit_zavedeny() -> None:
    """Первый заданный пароль или ключ: отметка «входы заведены» и срок старых входов +7 дней.
    Повторно ничего не меняет. Срок пишется раньше отметки: отметка без срока значит
    «старые входы выключены»."""
    s = _nastroyki("vhod_zavedeny", "vhod_starye_do")
    if s.get("vhod_zavedeny"):
        return
    if not s.get("vhod_starye_do"):
        _otmetka("vhod_starye_do", _iso(seychas() + SROK_STARYH))
    _otmetka("vhod_zavedeny", "1")


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
    for r in rows:                       # сначала точное совпадение по логину, потом по имени
        if v == _norm(r["login"]):
            return r["login"]
    for r in rows:
        if r["imya"] and v == _norm(r["imya"]):
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
        _zhaloba("vhod: не проверить прежний пароль владельца — принимаю, как раньше")
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


class PauzaVhoda(HTTPException):
    """Пауза старого входа в сером окне после промахов: 429 с Retry-After. main.py отвечает
    человеку, открывшему страницу, понятной страницей, а скрипту — тем же JSON, что и раньше."""

    def __init__(self, sek: float):
        super().__init__(429, tekst_pauzy(sek), headers={"Retry-After": str(int(sek) + 1)})


def _starye(request, credentials):
    """Старые входы в сером окне: общий admin и прежний пароль владельца.
    Пароль сверяется ПЕРВЫМ. Пауза после промахов останавливает только тех, кого сервер с этого
    адреса ещё не видел с верным паролем: неверные пароли и верный пароль, присланный впервые
    (иначе перебор узнавал бы удачу по ответу). Браузеры и скрипты, которые за последние 12
    часов уже ходили с этого адреса с верным паролем, паузу не замечают — чужие промахи с того
    же адреса (один Wi-Fi на центр) не запирают общий вход всем. Удачный вход вне паузы стирает
    промахи («5 промахов подряд»). Запрета «по адресу, любые логины» у окна нет: незнакомый
    логин войти не может, а admin и boris считаются каждый своим счётчиком; промахи формы
    /vhod в счёт окна не идут."""
    if credentials is None or not starye_vklyucheny():
        return None
    try:
        if _ustr_izvestno(request):
            return None          # с этого браузера уже входили через форму: общий вход здесь не годится
    except Exception:  # noqa: BLE001
        _zhaloba("vhod: метка устройства не проверена")
    user, pw = credentials.username, credentials.password
    k = _sverit_staryy(user, pw)
    if k is None and est_kirillica(pw):
        k = _sverit_staryy(user, na_latinicu(pw))
    kogo = ("admin" if _ravny(user, config.APP_USER or "admin") else
            "boris" if _ravny(user, "boris") else "")
    if not kogo:
        return k                 # незнакомый логин: войти им нельзя, считать нечего
    p = schet_sekreta(OKNO + kogo, adres(request), pw, k is not None, kogo)
    if p > 0:
        raise PauzaVhoda(p)
    return k


def schet_sekreta(schet: str, adr: str, sekret: str, veren: bool, nazvanie: str = "",
                  schitat: bool = True, trevoga: str = "", otkazyvat: bool = True) -> float:
    """Счёт перебора для входа, где секрет приходит с каждым запросом: серое окно (общий admin,
    прежний boris — _starye) и ключи педагогов из ссылки (app/pedklyuchi.py, 14.11.2026).
    → сколько секунд паузы: 0 — секрет рассмотрен (верный пускать, неверный записан промахом);
    больше 0 — отказать, даже если секрет верный.
    Правила — те, что описаны у _starye: пауза останавливает неверные секреты и верный,
    присланный с этого адреса впервые; знакомый верный (был за последние 12 часов) её не
    замечает; удача вне паузы стирает промахи; один и тот же неверный секрет с одного адреса
    считается раз в час. schet — имя счётчика, начинается с OKNO (в правило формы «один адрес,
    любые логины» такие промахи не входят). schitat=False — неверный секрет промахом не
    считать (устаревшая ссылка педагога — не перебор). trevoga — свой текст сообщения
    владельцу о 50 промахах за час (см. _posle_promaha).
    otkazyvat=False (14.11.2026, прежний общий ключ педагогов — app/pedklyuchi.py): паузой
    не отказывать никому — верный секрет пускается и в паузу, как было до счётчика; неверный
    промахом считается и в паузу (иначе пауза глушила бы и сам счёт, и тревогу о 50 промахах).
    Удача стирает промахи адреса, как всегда, — только вне паузы."""
    t = seychas()
    if veren:
        otp = _otpechatok(adr, schet, sekret)
        with _ZAMOK:
            znakom = abs(t - _UDACHI.get(otp, -1e18)) < SROK_UDACHI
        try:
            p = _pauza_okna(schet, adr) if _est_promahi(schet, adr) else 0
        except Exception:  # noqa: BLE001
            _zhaloba("vhod: счётчик попыток не прочитан")
            p = 0
        if p > 0 and not znakom and otkazyvat:
            return p
        with _ZAMOK:
            _UDACHI[otp] = t
            if len(_UDACHI) > 5000:
                for k_, v_ in list(_UDACHI.items()):
                    if abs(t - v_) >= SROK_UDACHI:
                        del _UDACHI[k_]
        if p <= 0 and _est_promahi(schet, adr):
            try:
                uspeh(schet, adr)
                with _ZAMOK:
                    _EST_PROMAHI.discard((schet, adr))
            except Exception:  # noqa: BLE001
                _zhaloba("vhod: промахи после удачного входа не стёрты")
        return 0.0
    try:
        p = _pauza_okna(schet, adr)
    except Exception:  # noqa: BLE001
        _zhaloba("vhod: счётчик попыток не прочитан")
        p = 0
    if p > 0 and otkazyvat:
        return p
    if schitat:
        try:
            _promah_okna(schet, adr, sekret, nazvanie, trevoga)
        except Exception:  # noqa: BLE001
            _zhaloba("vhod: промах не записан")
    return 0.0 if otkazyvat else max(0.0, p)


def kto(request, credentials):
    """Кто пришёл: словарь {login, imya, rol, sposob, seans} или None.
    Бросает только 403 на чужой запрос и 429 (PauzaVhoda) на паузу старого входа после промахов.
    Заголовок X-Kak: admin понижает вошедшего до прав личного админа (см. _ponizit)."""
    return _ponizit(request, _kto(request, credentials))


def _ponizit(request, k):
    """X-Kak: admin — только понижает: владелец (в том числе прежний boris в окне и режим без
    входа) и оба служебных ключа получают права ЛИЧНОГО админа; переходная поблажка общего
    admin (PEREHOD_ADMIN) при этом заголовке тоже не действует. Админа не повышает,
    автоматику не трогает. Так ассистент после выкладки проверяет, что админам закрыто, не
    зная их паролей. Логин и способ входа остаются — в журнале видно, кто проверял."""
    try:
        kak = (request.headers.get("x-kak") or "").strip().lower()
    except Exception:  # noqa: BLE001
        kak = ""
    if not k or kak != "admin" or k.get("rol") == "avto":
        return k
    k = dict(k)
    k["rol"], k["kak"] = "admin", "admin"
    return k


def _kto(request, credentials):
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
        _zhaloba("vhod: новая проверка входа не сработала — смотрю старые входы")
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
        _zhaloba("vhod: ключ без request не проверен")
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


def _put_norm(put: str) -> str:
    p = (put or "").split("?", 1)[0]
    return p.rstrip("/") or ("/" if p else "")


def _v_spiske(put: str, tochno=(), prefiksy=()) -> bool:
    """Путь равен записи из tochno; либо равен записи из prefiksy или начинается с «запись/»."""
    return put in tochno or any(put == p or put.startswith(p + "/") for p in prefiksy)


def okno_vykladki_otkryto() -> bool:
    """Аварийное окно: владелец на /owner разрешил выкладку ключом рутин на 2 часа."""
    try:
        do = _iz_iso(_nastroyki("vhod_okno_vykladki_do").get("vhod_okno_vykladki_do"))
        return do is not None and seychas() < do
    except Exception:  # noqa: BLE001
        _zhaloba("vhod: аварийное окно выкладки не прочитано — считаю закрытым")
        return False


def perehod_admina_deystvuet() -> bool:
    """Общий admin ещё проходит по вновь закрытым адресам (PEREHOD_ADMIN): да, пока ключ рутин
    ни разу не сработал. Сбой чтения — да, как было до 12.11.2026 (старым входам новый код не мешает)."""
    try:
        with _baza() as c:
            r = c.execute("SELECT posl_zapros FROM vhody WHERE login='klod'").fetchone()
        return not (r is not None and r["posl_zapros"])
    except Exception:  # noqa: BLE001
        _zhaloba("vhod: не проверить, работал ли ключ рутин — общий вход пускаю, как раньше")
        return True


def mozhno_vladelcu(k: dict | None, metod: str = "", put: str = "") -> bool:
    """Пускать ли на адрес под OWNER_AUTH (раздел 5 плана).

    | кто                                   | что можно                                                   |
    | владелец через форму                  | всё, кроме TOLKO_VYKLADKA (из браузера код не выкладывается) |
    | прежний boris в окне, режим без входа | всё, как раньше                                             |
    | ключ выкладки                         | всё, кроме TOLKO_CHELOVEK (/owner, /settings)               |
    | ключ рутин                            | только SLUZH_TOCHNO и SLUZH_PREFIKS; выкладка и перезапуск — в аварийное окно |
    | общий admin в окне                    | только PEREHOD_ADMIN и только пока ключ рутин ни разу не сработал |
    | личный админ, автоматика, X-Kak: admin| ничего                                                      |

    Без адреса (прямой вызов по-старому) служебным ключам — нет: списки сверять не с чем."""
    if not k or k.get("kak"):
        return False
    rol, sposob = k.get("rol"), k.get("sposob")
    p = _put_norm(put)
    m = (metod or "GET").upper()
    if rol == "owner":
        return not (sposob == "forma" and p in TOLKO_VYKLADKA)
    if rol == "vykladka":
        return bool(p) and p not in TOLKO_CHELOVEK
    if rol == "sluzh":
        if not p or p in TOLKO_CHELOVEK:
            return False
        if p in TOLKO_VYKLADKA:
            return p in OKNO_VYKLADKI and m == "POST" and okno_vykladki_otkryto()
        if not _v_spiske(p, SLUZH_TOCHNO, SLUZH_PREFIKS):
            return False
        return m in BEZOPASNYE or not _v_spiske(p, (), SLUZH_TOLKO_CHTENIE)
    if rol == "admin" and sposob == "staryy" and k.get("login") == "admin":
        return _v_spiske(p, PEREHOD_ADMIN, PEREHOD_PREFIKS) and perehod_admina_deystvuet()
    return False


def tekst_otkaza(k: dict | None) -> str:
    """Что сказать тому, кого не пустили на адрес владельца."""
    rol, sposob = (k or {}).get("rol"), (k or {}).get("sposob")
    if (k or {}).get("kak"):
        return "Только для владельца (проверка с правами админа: X-Kak)"
    if rol == "owner" and sposob == "forma":
        return ("Выкладка кода, перезапуск, копия базы и настройки сервера идут только ключом выкладки — "
                "из браузера они закрыты")
    if rol == "sluzh":
        return "Ключу рутин этот адрес закрыт. Список разрешённых адресов — SLUZH_TOCHNO и SLUZH_PREFIKS в app/vhod.py"
    if rol == "vykladka":
        return "Ключу выкладки страницы настроек закрыты: их открывает только владелец"
    return "Только для владельца"


def stranica_otkaza(k: dict | None = None) -> str:
    """Страница 403 «Только для владельца · ← На пульт» — вместо окна пароля."""
    imya = (k or {}).get("imya") or ""
    kto_ = f"Вы вошли как {_e(imya)}. " if imya and (k or {}).get("sposob") == "forma" else ""
    return ("<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta name='robots' content='noindex,nofollow'><title>Только для владельца · KidsUP</title>"
            "<script src='/static/vhod.js'></script>"
            "<style>body{margin:0;background:#f8f7fc;color:#15132e;font:17px/1.5 -apple-system,BlinkMacSystemFont,"
            "'Segoe UI',Roboto,Helvetica,Arial,sans-serif}.k{max-width:520px;margin:12vh auto 0;padding:26px 22px;"
            "background:#fff;border:1px solid #e4e2f0;border-radius:16px;border-top:5px solid #312783}"
            "h1{margin:0 0 8px;font-size:24px;color:#312783}p{margin:8px 0;color:#4a4866}"
            "a.b{display:inline-block;margin-top:12px;padding:12px 18px;border-radius:10px;background:#312783;"
            "color:#fff;text-decoration:none;font-weight:700}</style></head><body><div class='k'>"
            "<h1>Только для владельца</h1>"
            f"<p>{kto_}Эта страница открыта только Борису. Для работы она не нужна — всё, что нужно сегодня, "
            "есть на пульте.</p>"
            "<a class='b' href='/pult'>← На пульт</a></div></body></html>")


def stranica_pauzy(tekst: str) -> str:
    """Страница 429 для человека в сером окне — вместо сырого {"detail": …}."""
    return ("<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta name='robots' content='noindex,nofollow'><title>Слишком много попыток · KidsUP</title>"
            "<style>body{margin:0;background:#f8f7fc;color:#15132e;font:17px/1.5 -apple-system,BlinkMacSystemFont,"
            "'Segoe UI',Roboto,Helvetica,Arial,sans-serif}.k{max-width:520px;margin:12vh auto 0;padding:26px 22px;"
            "background:#fff;border:1px solid #e4e2f0;border-radius:16px;border-top:5px solid #312783}"
            "h1{margin:0 0 8px;font-size:24px;color:#312783}p{margin:8px 0;color:#4a4866}"
            "a.b{display:inline-block;margin-top:12px;padding:12px 18px;border-radius:10px;background:#312783;"
            "color:#fff;text-decoration:none;font-weight:700}</style></head><body><div class='k'>"
            "<h1>Слишком много попыток</h1>"
            f"<p>{_e(tekst)}</p>"
            "<p>С этого адреса несколько раз подряд ввели неверный пароль. Это защита, а не поломка: "
            "подождите и обновите страницу — пароль заново вводить не нужно.</p>"
            "<a class='b' href=''>Обновить страницу</a></div></body></html>")


def v_sisteme(minut: int = 30) -> list:
    """Кто из людей сейчас в системе: [(имя, сколько минут назад был последний запрос)].
    «Последний запрос» пишется не чаще раза в минуту на логин. Сбой — пустой список."""
    try:
        t = seychas()
        with _baza() as c:
            rows = c.execute("SELECT imya, posl_zapros FROM vhody WHERE rol IN ('admin', 'owner') AND vkl=1 "
                             "AND posl_zapros IS NOT NULL").fetchall()
        out = []
        for r in rows:
            pz = _iz_iso(r["posl_zapros"])
            if pz is not None and 0 <= t - pz <= minut * 60:
                out.append((r["imya"], int((t - pz) // 60)))
        return sorted(out, key=lambda x: x[1])
    except Exception:  # noqa: BLE001
        _zhaloba("vhod: «сейчас в системе» не прочитано")
        return []


def shapka(k: dict | None, put: str = "/", cvet: str = "#312783") -> str:
    """Полоска «кто вошёл» для шапки страниц и пульта.
    Вошедшему через форму: «Вы: Лена · Выйти / сменить» (выход настоящий — сеанс стирается).
    Под общим входом admin, когда личные входы уже заведены: жёлтая полоса «Вы под общим
    входом. Он отключится ДД.ММ — войдите под своим именем». Пока входы не заведены, а также
    ключам, автоматике и режиму без входа — ничего (в день выкладки шапка не меняется)."""
    try:
        if not k:
            return ""
        nxt = _e(bezopasnyy_next(put or "/") or "/")
        if k.get("sposob") == "forma" and k.get("imya"):
            return ("<div class='kto-polosa' style='display:flex;gap:10px;align-items:center;justify-content:flex-end;"
                    "flex-wrap:wrap;padding:6px 14px;background:#fff;border-bottom:1px solid #e4e2f0;font:14px/1.3 "
                    "-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,Helvetica,Arial,sans-serif;color:#15132e'>"
                    f"<span>Вы: <b style='color:{_e(cvet)}'>{_e(k['imya'])}</b></span>"
                    "<form method='post' action='/vyhod' style='margin:0'>"
                    f"<input type='hidden' name='next' value='{nxt}'>"
                    "<button type='submit' style='font:inherit;padding:5px 12px;border-radius:8px;border:1px solid #c9c6e0;"
                    "background:#f8f7fc;color:#312783;cursor:pointer'>Выйти / сменить</button></form></div>")
        if k.get("sposob") == "staryy" and k.get("login") == "admin" and not k.get("kak") and zavedeny():
            do = _iz_iso(_nastroyki("vhod_starye_do").get("vhod_starye_do"))
            kogda_ = ""
            if do is not None:
                d = datetime.fromtimestamp(do, MSK)
                kogda_ = f" Он отключится {d.day:02d}.{d.month:02d}"
            return ("<div class='kto-polosa obshchiy' style='padding:8px 14px;background:#fff1d6;border-bottom:2px solid "
                    "#F59C00;color:#5a3d00;font:15px/1.35 -apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,Helvetica,"
                    f"Arial,sans-serif'>Вы под общим входом.{kogda_} — "
                    f"<a href='/vhod?next={quote(bezopasnyy_next(put or '/') or '/', safe='/')}' style='color:#312783;"
                    "font-weight:700'>войдите под своим именем</a></div>")
    except Exception:  # noqa: BLE001
        _zhaloba("vhod: шапка «кто вошёл» не собрана")
    return ""


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
OKNO = "okno:"                           # счётчики серого окна — отдельно от формы: okno:admin, okno:boris
NEZNAKOMYY = "~"                         # счётчик формы для логина, которого нет: ~<отпечаток введённого>
_EST_PROMAHI: set = set()                # (счётчик окна, адрес), по которым в базе есть промахи
_UDACHI: dict = {}                       # отпечаток (адрес, логин, верный пароль) → когда был удачный вход
SROK_UDACHI = 12 * 3600                  # сколько «знакомый верный» вход в окне не замечает паузу
_POVTOR: dict = {}                       # (адрес, логин, отпечаток пароля) → когда засчитан
_PROC_KLYUCH = secrets.token_bytes(32)   # отпечатки неверных паролей — только в памяти процесса
_SOOBSHCHENO: dict = {}                  # логин → когда сообщили Борису о 50 промахах


def tekst_pauzy(sek: float) -> str:
    n = max(1, int((sek + 59) // 60))
    slovo = "минуту" if n % 10 == 1 and n % 100 != 11 else (
        "минуты" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "минут")
    return f"Слишком много попыток. Подождите {n} {slovo} или позвоните Борису."


def pauza(login: str, adres_: str, ustr: str = "", po_adresu: bool = True) -> float:
    """Сколько секунд ждать (0 — можно пробовать).
    Один логин с одного адреса: 5 промахов подряд (за час) — минута, 10 — 5 минут, 15 — 15.
    Один адрес, любые логины: 20 промахов за 15 минут — 15 минут, но только браузерам без
    метки устройства (стойка и телефоны в Wi-Fi центра — один адрес). Это правило — только
    для формы (po_adresu=False у серого окна), и промахи серого окна в него не входят."""
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
        znakom = (not po_adresu or bool(ustr)
                  and c.execute("SELECT 1 FROM ustroystva WHERE id=?", (ustr,)).fetchone() is not None)
        if not znakom:
            rows = c.execute("SELECT ts FROM popytki WHERE adres=? AND ts>? AND login NOT LIKE ? "
                             "ORDER BY ts DESC LIMIT 20", (adres_, t - 1800, OKNO + "%")).fetchall()
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


def _otpechatok(adres_: str, login: str, pw: str) -> str:
    """Отпечаток «адрес + логин + пароль» — только в памяти процесса, с ключом процесса."""
    return hmac.new(_PROC_KLYUCH, "\x00".join((adres_ or "", login or "", pw or "")).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def _zagruzit_promahi() -> None:
    """Один раз за процесс: по каким адресам за последний час были промахи (после перезапуска
    счёт не теряется)."""
    with _ZAMOK:
        if _NEUD_ZAGRUZHEN[0]:
            return
        _NEUD_ZAGRUZHEN[0] = True
    t = seychas()
    with _baza() as c:
        rows = c.execute("SELECT login, adres, MAX(ts) AS ts FROM popytki WHERE ts>? GROUP BY login, adres",
                         (t - 3600,)).fetchall()
    with _ZAMOK:
        for r in rows:
            _NEUD[r["adres"]] = max(_NEUD.get(r["adres"], 0), r["ts"])
            if r["login"].startswith(OKNO):
                _EST_PROMAHI.add((r["login"], r["adres"]))


def _est_promahi(schet: str, adres_: str) -> bool:
    try:
        _zagruzit_promahi()
    except Exception:  # noqa: BLE001
        _zhaloba("vhod: промахи за последний час не прочитаны")
    with _ZAMOK:
        return (schet, adres_) in _EST_PROMAHI


def _pauza_okna(schet: str, adres_: str) -> float:
    """Пауза для старого входа в окне (schet — okno:admin или okno:boris): только счёт «логин +
    адрес» по промахам самого окна. Короткий путь: с адреса за час не было промахов — в базу
    не ходим (окно шлёт пароль с каждым запросом страницы)."""
    t = seychas()
    _zagruzit_promahi()
    with _ZAMOK:
        posl = _NEUD.get(adres_)
    if posl is None or t - posl > 3600:
        return 0.0
    return pauza(schet, adres_, "", po_adresu=False)


def _promah_okna(schet: str, adres_: str, pw: str, nazvanie: str = "", trevoga: str = "") -> None:
    """Неверный пароль в окне. Один и тот же неверный пароль с одного адреса засчитывается
    один раз в час: старая вкладка с устаревшим паролем не должна запереть весь центр."""
    t = seychas()
    kl = _otpechatok(adres_, schet, pw)
    with _ZAMOK:
        bylo = _POVTOR.get(kl)
        if bylo is not None and t - bylo < 3600:
            return
        _POVTOR[kl] = t
        if len(_POVTOR) > 5000:
            for k_, v_ in list(_POVTOR.items()):
                if t - v_ >= 3600:
                    del _POVTOR[k_]
    popytka(schet, adres_)
    with _ZAMOK:
        _EST_PROMAHI.add((schet, adres_))
    _posle_promaha(schet, nazvanie, trevoga)


def _posle_promaha(login: str, nazvanie: str = "", trevoga: str = "") -> None:
    """Один логин с разных адресов: 50 промахов за час — сообщение Борису (вход не закрывается).
    nazvanie — как назвать вход в сообщении (у счётчиков серого окна — admin или boris).
    trevoga — свой текст сообщения с местом {n} для числа промахов (ключи педагогов)."""
    if not login:
        return
    nazvanie = nazvanie or login
    t = seychas()
    with _baza() as c:
        n = c.execute("SELECT COUNT(*) FROM popytki WHERE login=? AND ts>?", (login, t - 3600)).fetchone()[0]
    if n < 50:
        return
    with _ZAMOK:
        if t - _SOOBSHCHENO.get(login, 0) < 3600:
            return
        _SOOBSHCHENO[login] = t
    if trevoga:
        soobshchit_borisu(trevoga.replace("{n}", str(n)), chto=f"много неверных ключей: {nazvanie}")
        return
    soobshchit_borisu(f"app.kidsup.ru: за час {n} неверных паролей ко входу «{nazvanie}» с разных адресов. "
                      "Вход не закрыт. Если это не вы и не админы — задайте этому входу новый пароль на /owner.",
                      chto=f"много неверных паролей: {nazvanie}")


def voyti(request, vvod: str, parol: str) -> dict:
    """Проверка формы /vhod → {"login": …, "zapis": …} при удаче;
    {"pauza": сек} при отказе (0 — просто не подошло); {"zanyat": True} — сервер занят.
    Попытка пишется ДО проверки пароля (иначе пачка одновременных запросов обходит счётчик)
    и стирается при удаче. Ответ на незнакомый логин и неверный пароль одинаков по тексту,
    времени и по тому, когда наступает пауза. Пароль в русской раскладке — один раз пробуем те же клавиши в английской."""
    adr = adres(request)
    try:
        ustr = _ustr_id(request)
    except Exception:  # noqa: BLE001
        ustr = ""
    login = login_po_vvodu(vvod)
    z = _zapis(login) if login else None
    chelovek = z is not None and z["rol"] in LYUDI and bool(z["vkl"]) and not _istekla(z)
    # Счётчик промахов одинаков для любого логина: настоящего, отключённого, служебного и
    # такого, которого нет вовсе (по введённой строке). Иначе по тому, наступает ли пауза
    # после пяти промахов, было бы видно, существует ли вход.
    # В счётчик идёт отпечаток введённого, а не сам текст: в поле логина по ошибке набирают пароль.
    schet = login or (NEZNAKOMYY + _sha(_norm(vvod)[:40])[:24])
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
        if chelovek:
            _posle_promaha(schet)
    except Exception:  # noqa: BLE001
        _zhaloba("vhod: проверка «50 промахов» не сработала")
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
    if _norm(imya) in ZANYATYE:
        return "это имя занято"
    if login_po_vvodu(imya):
        return "вход с таким именем уже есть"
    if login_po_vvodu(login):
        return "логин совпадает с именем другого входа"
    with _baza() as c:
        c.execute('INSERT INTO vhody (login, imya, rol, sozdan, "do") VALUES (?,?,?,?,?)',
                  (login, imya, "admin", _iso(), _iso(seychas() + SROK_VREMENNOGO) if sutki else None))
    _obnovit_kopiyu()
    return ""


# --- страница владельца: блок «Входы» (исполнитель Б) ----------------------------------------
# Данные для блока и для /api/vhod/sostoyanie — без хешей, паролей и ключей. Действия —
# deystvie_vladelca(): каждое подтверждается паролем владельца (поле moy_parol): без этого
# скрипт из документа, который служебный вход положил в /boris/docs, мог бы менять входы от
# имени владельца. Окна «подтверждено на 10 минут» нет нарочно.

PODTV = "boris:podtv"            # счётчик неверных подтверждений — отдельно от входа boris
SROK_OKNA_VYKLADKI = 2 * 3600    # «Разрешить Клоду выкладку на 2 часа»
NAZVANIE_KLYUCHA = {"klod": "Ключ рутин", "klod-vykladka": "Ключ выкладки"}
KUDA_KLYUCH = {
    "klod": "в защищённую форму Claude для app.kidsup.ru (тип «Bearer») в основном окружении — там, где работают рутины",
    "klod-vykladka": "в защищённую форму Claude для app.kidsup.ru (тип «Bearer») в отдельном окружении «KidsUP выкладка»",
}
ROL_SLOVOM = {"admin": "админ", "owner": "владелец", "sluzh": "ключ рутин", "vykladka": "ключ выкладки"}


def kogda(x) -> str:
    """Время словами: «3 мин назад», «сегодня 09:02», «вчера 23:13», «12.11 22:41»."""
    t = _iz_iso(x) if isinstance(x, str) else x
    if not t:
        return ""
    d, n = datetime.fromtimestamp(t, MSK), datetime.fromtimestamp(seychas(), MSK)
    dt = seychas() - t
    if 0 <= dt < 60:
        return "только что"
    if 0 <= dt < 3600:
        return f"{int(dt // 60)} мин назад"
    if d.date() == n.date():
        return "сегодня " + d.strftime("%H:%M")
    if d.date() == (n - timedelta(days=1)).date():
        return "вчера " + d.strftime("%H:%M")
    return d.strftime("%d.%m %H:%M")


def spisok() -> list:
    """Учётные записи для блока «Входы»: без хешей. Сначала шесть постоянных, потом добавленные."""
    t = seychas()
    with _baza() as c:
        rows = c.execute("SELECT * FROM vhody").fetchall()
        seansy = {r[0]: r[1] for r in c.execute('SELECT login, COUNT(*) FROM seansy WHERE "do">? GROUP BY login', (t,))}
        zaperty = set()
        for r in c.execute("SELECT login, COUNT(*) AS n, MAX(ts) AS m FROM popytki WHERE ts>? AND login<>'' "
                           "AND login NOT LIKE 'okno:%' GROUP BY login, adres HAVING n>=5", (t - 3600,)):
            if r["m"] + (900 if r["n"] >= 15 else 300 if r["n"] >= 10 else 60) > t:
                zaperty.add(r["login"])
    poryadok = {z[0]: i for i, z in enumerate(ZAPISI)}
    rows = sorted(rows, key=lambda r: (poryadok.get(r["login"], 99), r["sozdan"] or "", r["login"]))
    out = []
    for r in rows:
        sdo = _iz_iso(r["staryy_do"])
        out.append({
            "login": r["login"], "imya": r["imya"], "rol": r["rol"], "vkl": bool(r["vkl"]),
            "zadan": bool(r["hesh"]),                 # задан пароль (человеку) или выпущен ключ (служебной)
            "parol_ot": r["parol_ot"], "forma_vhod": r["forma_vhod"],
            "posl_zapros": r["posl_zapros"], "posl_adres": r["posl_adres"],
            "do": r["do"], "istek": _istekla(r), "seansov": int(seansy.get(r["login"], 0)),
            "pauza": r["login"] in zaperty,
            "prezhniy_klyuch_do": r["staryy_do"] if (r["hesh_staryy"] and sdo and sdo > t) else None,
        })
    return out


def obshchie_ustroystva() -> list:
    """Браузеры, помеченные общим компьютером: кто входил и когда последний раз."""
    with _baza() as c:
        imena = {r["login"]: r["imya"] or r["login"] for r in c.execute("SELECT login, imya FROM vhody")}
        rows = c.execute("SELECT id, loginy, posl FROM ustroystva WHERE obshchiy=1 ORDER BY posl DESC").fetchall()
    out = []
    for r in rows:
        try:
            loginy = list(json.loads(r["loginy"] or "[]"))
        except ValueError:
            loginy = []
        out.append({"id": r["id"], "imena": [imena.get(x, x) for x in loginy], "posl": _iso(r["posl"]) if r["posl"] else None})
    return out


def _prezhniy_parol_deystvuet() -> bool:
    """Прежний пароль владельца ещё принимается в сером окне."""
    return bool(db.get_setting("owner_password")) and starye_vklyucheny() and _staryy_boris_v_okne()


def sostoyanie(s_ustroystvami: bool = False) -> dict:
    """Всё о входах без секретов: у кого задан пароль, даты входов, выпущены ли ключи, срок
    старых входов, хвост общего входа, отказы ключу рутин, автоматика, строки «Важное».
    Файл входов не читается (испорчен, заперт, нет папки) — отдаём то, что есть в основной
    базе, и поле fayl_vhodov: «не читается»: адрес нужен как раз тогда, когда что-то сломано."""
    t = seychas()
    s = _nastroyki("vhod_zavedeny", "vhod_starye_do", "vhod_starye_vykl", "vhod_okno_vykladki_do")
    fayl = "в порядке"
    hv, vhody, ustr = [], [], []
    try:
        with _baza() as c:
            hv = [dict(r) for r in c.execute(
                "SELECT login, adres, chem, SUM(n) AS n, MAX(posl) AS posl FROM hvost WHERE posl>? "
                "GROUP BY login, adres, chem ORDER BY posl DESC", (_iso(t - 3 * 86400),))]
        vhody = spisok()
        ustr = obshchie_ustroystva()
    except Exception:  # noqa: BLE001
        _zhaloba("vhod: состояние входов — файл входов не читается")
        fayl = "не читается"
    sutki = _iso(t - 86400)
    with db.get_conn() as c:
        _zhurnal_init(c)
        vazhnoe = [dict(r) for r in c.execute(
            "SELECT ts, imya, chto FROM deystviya WHERE vazhnoe=1 AND ts>? ORDER BY id DESC LIMIT 30",
            (_iso(t - 7 * 86400),))]
        otkazy = [dict(r) for r in c.execute(
            "SELECT put, COUNT(*) AS n, MAX(ts) AS posl FROM deystviya WHERE login='klod' AND kod=403 AND ts>? "
            "GROUP BY put ORDER BY n DESC LIMIT 30", (_iso(t - 7 * 86400),))]
    okno = _iz_iso(s.get("vhod_okno_vykladki_do"))
    return {
        "seychas": _iso(t),
        "fayl_vhodov": fayl,
        "vhody": vhody,
        "starye": {
            "zavedeny": bool(s.get("vhod_zavedeny")),
            "vklyucheny": starye_vklyucheny(),
            "do": s.get("vhod_starye_do"),
            "vyklyucheny_kogda": s.get("vhod_starye_vykl"),
            "prezhniy_parol_vladelca": _prezhniy_parol_deystvuet(),
            "hvost": hv,
            "obshchiy_za_sutki": sum(int(h["n"] or 0) for h in hv if h["login"] == "admin" and (h["posl"] or "") > sutki),
        },
        "avtomatika_posl": _iso(POSL_AVTO[0]) if POSL_AVTO[0] else None,
        "vazhnoe": vazhnoe,
        "klodu_otkazano": otkazy,
        "okno_vykladki_do": s.get("vhod_okno_vykladki_do") if (okno and okno > t) else None,
        "obshchie_kompyutery": ustr if s_ustroystvami else [{"imena": u["imena"], "posl": u["posl"]} for u in ustr],
    }


def otmetit_vazhnoe(chto: str, imya: str = "сервер") -> None:
    """Строка «Важное» в журнал без запроса (консольная команда, автопилот). Секретов не писать."""
    with db.get_conn() as c:
        _zhurnal_init(c)
        c.execute("INSERT INTO deystviya (ts, login, imya, rol, sposob, metod, put, kod, chto, adres, rutina, vazhnoe) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,1)", (_iso(), "", imya, "", "", "", "", None, (chto or "")[:300], "", ""))


def starye_proverit() -> tuple:
    """Предохранители кнопки «Отключить старые входы» → (отказ словами или "", [предупреждения])."""
    t = seychas()
    lyudi = spisok()
    boris = next((z for z in lyudi if z["login"] == "boris"), None)
    if boris is None or not boris["zadan"]:
        return ("Сначала задайте себе новый пароль: без него после отключения вы не войдёте на эту страницу.", [])
    pred = []
    klod = next((z for z in lyudi if z["login"] == "klod"), None)
    posl = _iz_iso(klod["posl_zapros"]) if klod else None
    if klod is None or not klod["zadan"]:
        pred.append("Ключ рутин Клода ещё не выпущен: рутины ходят общим входом и после отключения остановятся.")
    elif posl is None or t - posl > 86400:
        pred.append("Ключ рутин Клода не делал запросов последние сутки: похоже, рутины ещё ходят общим входом.")
    with _baza() as c:
        n = c.execute("SELECT COALESCE(SUM(n), 0) FROM hvost WHERE login='admin' AND posl>?",
                      (_iso(t - 3 * 86400),)).fetchone()[0]
    if n:
        pred.append(f"Общим входом admin пользовались за последние 3 дня ({n} запросов) — кто и откуда, видно ниже.")
    ne_vhodili = [z["imya"] for z in lyudi if z["rol"] == "admin" and z["vkl"] and not z["do"] and not z["forma_vhod"]]
    if ne_vhodili:
        pred.append("Ещё не входили через форму: " + ", ".join(ne_vhodili) + ".")
    return ("", pred)


def starye_vyklyuchit(kem: str = "владелец") -> str:
    """«Отключить старые входы»: общий admin и прежний пароль владельца больше не принимаются
    нигде, любой браузер без входа получает форму, а не серое окно. "" — готово, иначе отказ.
    owner_password затирается длинной случайной строкой, а не удаляется: прежний код при
    пустом значении открыл бы страницы владельца общему логину (откат на старую версию).
    Кнопки «включить обратно» нет: аварийный вход — временная личная запись на 24 часа."""
    otkaz_, _ = starye_proverit()
    if otkaz_:
        return otkaz_
    ts = _iso()
    _otmetka("vhod_starye_do", ts)                   # срок «сейчас» — раньше всего остального
    _otmetka("vhod_zavedeny", "1")
    db.set_setting("owner_password", secrets.token_urlsafe(48))
    _otmetka("vhod_starye_vykl", ts)
    _POSL_STARYE[0] = False
    soobshchit_borisu("app.kidsup.ru: старые входы отключены (" + kem + "). Общий пароль admin и прежний пароль "
                      "владельца больше не принимаются; вход — только личный, на странице app.kidsup.ru/vhod. "
                      "Аварийный вход — «Добавить вход» на /owner, временный на 24 часа.",
                      chto="старые входы отключены: " + kem)
    return ""


def starye_po_sroku() -> str:
    """Автовыключение по сроку (зовёт автопилот). До срока меньше двух часов: ключ рутин
    работал последние сутки и у владельца задан новый пароль — выключить (с затиранием
    owner_password); иначе сдвинуть срок на сутки и сообщить владельцу причину.
    → "" (рано или уже выключены), "vyklyucheny", "sdvinuto"."""
    s = _nastroyki("vhod_zavedeny", "vhod_starye_do", "vhod_starye_vykl")
    do = _iz_iso(s.get("vhod_starye_do"))
    if not s.get("vhod_zavedeny") or s.get("vhod_starye_vykl") or do is None:
        return ""
    t = seychas()
    if do - t > 7200:
        return ""
    boris, klod = _zapis("boris"), _zapis("klod")
    posl = _iz_iso(klod["posl_zapros"]) if klod is not None else None
    prichiny = []
    if boris is None or not boris["hesh"]:
        prichiny.append("у вас не задан новый пароль")
    if klod is None or not klod["hesh"]:
        prichiny.append("ключ рутин Клода не выпущен")
    elif posl is None or t - posl > 86400:
        prichiny.append("ключ рутин Клода не работал последние сутки")
    if not prichiny:
        return "vyklyucheny" if starye_vyklyuchit("сами, по сроку") == "" else ""
    novyy = _iso(max(do, t) + 86400)
    _otmetka("vhod_starye_do", novyy)
    soobshchit_borisu("app.kidsup.ru: общий вход не отключён — " + "; ".join(prichiny) + ". Срок сдвинут на сутки, до "
                      + kogda(novyy) + ". Отключить можно кнопкой на /owner.",
                      chto="общий вход не отключён, срок сдвинут на сутки: " + "; ".join(prichiny))
    return "sdvinuto"


_POSL_SROK = [0.0]


def avtopilot_tik() -> None:
    """Из цикла автопилота: проверка срока старых входов — не чаще раза в 10 минут."""
    t = time.time()
    if t - _POSL_SROK[0] < 600:
        return
    _POSL_SROK[0] = t
    starye_po_sroku()


def podtverdit(request, moy_parol: str) -> str:
    """Подтверждение действия паролем владельца: "" — подошёл, иначе отказ словами.
    По хешу владельца, а пока новый пароль не задан — по прежнему. Промахи считаются, как
    на форме входа (попытка пишется до проверки): 5 подряд — пауза."""
    if not moy_parol:
        return "Введите ваш нынешний пароль: без него ничего не меняется."
    adr = adres(request)
    try:
        ustr = _ustr_id(request)
    except Exception:  # noqa: BLE001
        ustr = ""
    with _ZAMOK_POPYTOK:
        p = pauza(PODTV, adr, ustr)
        if p > 0:
            return tekst_pauzy(p)
        popytka(PODTV, adr)
    z = _zapis("boris")
    ok = False
    try:
        for v in [moy_parol] + ([na_latinicu(moy_parol)] if est_kirillica(moy_parol) else []):
            if z is not None and z["hesh"]:
                ok = parol_veren(v, z["hesh"])
            else:
                parol_veren(v, None)
                own = db.get_setting("owner_password")
                ok = bool(own) and _ravny(v, own)
            if ok:
                break
    except Zanyat:
        return "Сервер занят. Попробуйте ещё раз через минуту."
    if not ok:
        return "Ваш пароль не подошёл — ничего не изменено."
    uspeh(PODTV, adr)
    return ""


def deystvie_vladelca(request, f) -> dict:
    """POST /owner/vhody. f — поля формы. Зовёт только обработчик владельца, уже проверивший:
    человек-владелец, вошёл через форму. → {"msg"|"osh": текст, "klyuch": (логин, ключ) —
    показать один раз, "predupr": [предупреждения — нужно второе подтверждение]}.
    В журнал идёт только название действия и логины — паролей и ключей там нет."""
    def pole(n):
        return str(f.get(n) or "")
    d, _, arg = pole("deystvie").strip().partition(":")
    request.state.chto = "входы: " + (d or "?")[:20] + " — отказ"
    prichina = podtverdit(request, pole("moy_parol"))
    if prichina:
        return {"osh": prichina}
    ya = iz(request)

    def gotovo(chto, msg, **kw):
        request.state.chto = "входы: " + chto
        return {"msg": msg, **kw}

    def zapis_ili_none(login):
        return _zapis((login or "").strip())

    if d == "paroli":
        novye = {}
        for k_ in f.keys():
            if str(k_).startswith("parol_") and pole(k_):
                novye[str(k_)[6:]] = pole(k_)
        if not novye:
            return {"osh": "Не введено ни одного нового пароля. Ничего не изменено."}
        plohie, imena = [], {}
        for lg, p in novye.items():
            z = _zapis(lg)
            imena[lg] = (z["imya"] if z is not None else "") or lg
            pr = "нет такого входа" if z is None else parol_goden(p, lg, z["imya"])
            if not pr and z["rol"] not in LYUDI:
                pr = "служебной записи обычный пароль задать нельзя"
            if not pr and lg == "boris" and z["hesh"] and parol_veren(p, z["hesh"]):
                pr = "совпадает со старым паролем"
            if not pr and sum(1 for x in novye.values() if x == p) > 1:
                pr = "одинаковый пароль у двух человек"
            if pr:
                plohie.append(f"{imena[lg]} — {pr}")
        if plohie:
            return {"osh": "Пароли не сохранены: " + "; ".join(plohie) + ". Ничего не изменено."}
        for lg, p in novye.items():
            pr = zadat_parol(lg, p, krome=ya.get("seans", "") if lg == ya.get("login") else "")
            if pr:
                plohie.append(f"{imena[lg]} — {pr}")
        if plohie:
            return {"osh": "Не все пароли сохранены: " + "; ".join(plohie) + "."}
        return gotovo("пароли — " + ", ".join(sorted(novye)),
                      "Пароли сохранены: " + ", ".join(imena[x] for x in novye) + ". Скажите каждой её пароль лично "
                      "или по телефону — не в чатах. Прежние сеансы этих людей закрыты.")

    if d == "klyuch":
        login = (arg or pole("login")).strip()
        if login not in SLUZH_LOGINY:
            return {"osh": "Ключ выпускается только для Клода: ключ рутин или ключ выкладки."}
        srazu = pole("srazu") == "1"
        klyuch = vydat_klyuch(login, srazu=srazu)
        soobshchit_borisu(f"app.kidsup.ru: выпущен новый {NAZVANIE_KLYUCHA[login].lower()} Клода. Прежний ключ "
                          + ("погашен сразу." if srazu else "гаснет при первом запросе новым, самое позднее через 24 часа.")
                          + " Если это были не вы — выпустите ключ заново с галочкой «подозрение на утечку».",
                          chto=f"новый {NAZVANIE_KLYUCHA[login].lower()}" + (" (прежний погашен сразу)" if srazu else ""))
        return gotovo("ключ — " + login, NAZVANIE_KLYUCHA[login] + " выпущен.", klyuch=(login, klyuch))

    if d == "vyiti_vezde":
        z = zapis_ili_none(arg or pole("login"))
        if z is None:
            return {"osh": "Нет такого входа."}
        svoy = z["login"] == ya.get("login")
        seansy_zakryt(z["login"], krome=ya.get("seans", "") if svoy else "")
        return gotovo("выйти везде — " + z["login"],
                      "Вы вышли на всех устройствах, кроме этого." if svoy
                      else f"{z['imya'] or z['login']}: все сеансы закрыты — на каждом устройстве нужно войти заново.")

    if d == "vkl":
        login, _, vkl_ = (arg or (pole("login") + ":" + pole("vkl"))).partition(":")
        z = zapis_ili_none(login)
        if z is None or vkl_ not in ("0", "1"):
            return {"osh": "Нет такого входа."}
        pr = vklyuchit(z["login"], vkl_ == "1")
        if pr:
            return {"osh": pr[:1].upper() + pr[1:] + "."}
        return gotovo(("включён" if vkl_ == "1" else "отключён") + " вход — " + z["login"],
                      f"{z['imya'] or z['login']}: вход " + ("включён." if vkl_ == "1"
                                                               else "отключён, сеансы закрыты. История действий остаётся."))

    if d == "dobavit":
        login, imya, sutki = pole("novyy_login").strip().lower(), pole("novoe_imya"), pole("sutki") == "1"
        pr = dobavit(login, imya, sutki=sutki)
        if pr:
            return {"osh": "Вход не добавлен: " + pr + "."}
        return gotovo("добавлен вход — " + login + (" (на 24 часа)" if sutki else ""),
                      "Вход добавлен" + (" на 24 часа" if sutki else "") + ". Теперь задайте ему пароль — "
                      "поле «Новый пароль» в его карточке.")

    if d == "snyat_pauzu":
        z = zapis_ili_none(arg or pole("login"))
        if z is None:
            return {"osh": "Нет такого входа."}
        snyat_pauzu(z["login"])
        return gotovo("снята пауза — " + z["login"], f"{z['imya'] or z['login']}: пауза снята, можно входить.")

    if d == "ustr_lichnoe":
        uid = (arg or pole("ustr")).strip()
        if not re.fullmatch(r"[0-9a-f]{64}", uid) or not any(u["id"] == uid for u in obshchie_ustroystva()):
            return {"osh": "Нет такого компьютера в списке общих."}
        ustr_lichnoe(uid)
        return gotovo("компьютер считается личным",
                      "Компьютер снова считается личным: галочка «Запомнить» на нём появится. "
                      "Если с него войдёт второй человек — он опять станет общим.")

    if d == "okno_vykladki":
        do = _iso(seychas() + SROK_OKNA_VYKLADKI)
        db.set_setting("vhod_okno_vykladki_do", do)
        soobshchit_borisu("app.kidsup.ru: Клоду разрешена выкладка ключом рутин на 2 часа, до " + kogda(do) + ".",
                          chto="аварийное окно выкладки на 2 часа")
        return gotovo("окно выкладки на 2 часа", "Клоду разрешена выкладка на 2 часа — до " + kogda(do) + ".")

    if d == "starye_vykl":
        otkaz_, pred = starye_proverit()
        if otkaz_:
            return {"osh": otkaz_}
        if pred and arg != "tochno":
            request.state.chto = "входы: отключение старых входов — предупреждения"
            return {"predupr": pred}
        pr = starye_vyklyuchit("владелец")
        if pr:
            return {"osh": pr}
        return gotovo("старые входы отключены",
                      "Старые входы отключены: общий пароль admin и ваш прежний пароль больше не принимаются. "
                      "Любой браузер без входа теперь получает форму входа.")

    return {"osh": "Неизвестное действие — ничего не изменено."}


_STIL_VH = """
.vh{background:#fff;border:1px solid #e4e2f0;border-radius:14px;padding:14px 16px;margin:14px 0 22px}
.vh h2{color:#312783;font-size:20px;margin:0 0 4px}.vh h3{color:#312783;font-size:16.5px;margin:20px 0 6px}
.vh label{display:inline;background:none;border:0;border-radius:0;padding:0;margin:0}
.vh .vk{border:1px solid #e4e2f0;border-radius:12px;padding:10px 12px;margin:8px 0;background:#fbfbfe}
.vh .vk.net{opacity:.72}.vh .vz{font-size:16px}.vh .vs{color:#6c6a86;font-size:14px;margin:2px 0 8px}
.vh .vp{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.vh .vp span{font-weight:600;font-size:14.5px}
.vh .vp input{flex:1 1 180px;width:auto;min-width:0}
.vh input[type=checkbox]{width:20px;height:20px;vertical-align:-4px;margin:0 6px 0 0;padding:0}
.vh .vb{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
.vh button{background:#fff;color:#312783;border:1.5px solid #312783;border-radius:10px;padding:8px 12px;font-size:14.5px;
font-weight:600;cursor:pointer}
.vh button.gl{background:#312783;color:#fff;padding:11px 18px;font-size:16px}
.vh button.op{border-color:#B3261E;color:#B3261E}
.vh .moy{background:#f1f0fa;border-radius:12px;padding:10px 12px;margin:10px 0}
.vh .moy b{display:block;margin-bottom:4px}.vh p{margin:6px 0}.vh .tih{color:#6c6a86;font-size:14px}
.vh .osh{background:#FDECEA;color:#B3261E;border-radius:10px;padding:10px 12px;margin:10px 0}
.vh .ok{background:#eef8e6;border-left:4px solid #7DB928;border-radius:8px;padding:10px 12px;margin:10px 0}
.vh .pr{background:#FFF6DA;border:1.5px solid #E0B100;border-radius:12px;padding:10px 12px;margin:10px 0}
.vh .kl{background:#FFF6DA;border:1.5px solid #E0B100;border-radius:12px;padding:12px;margin:10px 0}
.vh .kl input{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:14px}
.vh ul{margin:6px 0;padding-left:20px}.vh .ryad{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.vh .ryad input[type=text]{flex:1 1 150px;width:auto;min-width:0}
"""

_SKRIPT_VH = """
(function(){
  var f=document.getElementById('vhody-forma'); if(!f)return;
  f.addEventListener('keydown',function(e){if(e.key==='Enter'&&e.target.tagName==='INPUT')e.preventDefault();});
  function sl(n){var a=new Uint32Array(1),p=4294967296-4294967296%n;do{crypto.getRandomValues(a)}while(a[0]>=p);return a[0]%n}
  function pridumat(){var s='bdfgklmnprstvz',g='aeiou',w=[],i,j,x;
    for(i=0;i<4;i++){x='';for(j=0;j<2;j++)x+=s.charAt(sl(s.length))+g.charAt(sl(g.length));w.push(x)}
    return w.join('-')+'-'+(10+sl(90))}
  var novye=f.querySelectorAll('input.np'), glaz=document.getElementById('vh-glaz'), vidno=false;
  function pokazat(v){vidno=v;for(var i=0;i<novye.length;i++)novye[i].type=v?'text':'password';
    if(glaz)glaz.textContent=v?'Скрыть новые пароли':'Показать новые пароли'}
  if(glaz)glaz.onclick=function(){pokazat(!vidno)};
  var pr=f.querySelectorAll('button.prid');
  for(var i=0;i<pr.length;i++)pr[i].onclick=function(){var p=f.elements[this.getAttribute('data-dlya')];
    if(p){p.value=pridumat();pokazat(true);p.focus()}};
  var sp=f.querySelectorAll('button[data-sprosit]');
  for(i=0;i<sp.length;i++)sp[i].addEventListener('click',function(e){if(!confirm(this.getAttribute('data-sprosit')))e.preventDefault()});
  f.addEventListener('submit',function(e){var m=f.elements['moy_parol'];
    if(m&&!m.value){e.preventDefault();m.focus();m.scrollIntoView({block:'center'});
      var t=document.getElementById('vh-nado');if(t)t.hidden=false}});
  var kop=document.getElementById('vh-kop');
  if(kop)kop.onclick=function(){var k=document.getElementById('vh-klyuch');k.select();
    function ok(){kop.textContent='Скопировано'}
    if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(k.value).then(ok,function(){document.execCommand('copy');ok()});
    else{document.execCommand('copy');ok()}};
})();
"""


def blok_vladelca(request, itog: dict | None = None) -> str:
    """HTML блока «Входы» для /owner. Виден только человеку-владельцу, вошедшему через форму;
    остальным (серое окно) — ссылка «войти через форму». Паролей и хешей в странице нет;
    только что выпущенный ключ показывается один раз — в ответе на сам запрос выпуска."""
    k = iz(request)
    if not (k.get("rol") == "owner" and k.get("sposob") == "forma"):
        return ("<section class='vh'><h2>Входы на app.kidsup.ru</h2><p><a href='/vhod?next=/owner'><b>Управлять входами → "
                "войти через форму</b></a></p><p class='tih'>Пароли админов, ключи Клода и отключение старых входов "
                "открываются только после входа под своим именем на странице входа. Логин — boris, пароль — ваш нынешний."
                "</p></section>")
    itog = itog or {}
    s = sostoyanie(s_ustroystvami=True)
    h = ["<section class='vh'><h2>Входы на app.kidsup.ru</h2>"]
    if itog.get("osh"):
        h.append(f"<div class='osh' role='alert'>{_e(itog['osh'])}</div>")
    if itog.get("msg"):
        h.append(f"<div class='ok'>{_e(itog['msg'])}</div>")
    h.append("<form method='post' action='/owner/vhody' id='vhody-forma' autocomplete='off'>"
             "<input type='text' name='username' value='boris' autocomplete='username' hidden>")
    if itog.get("klyuch"):
        lg, kl_ = itog["klyuch"]
        h.append(f"<div class='kl'><b>{_e(NAZVANIE_KLYUCHA.get(lg, lg))} — показывается один раз</b>"
                 f"<div class='ryad' style='margin:8px 0'><input type='text' id='vh-klyuch' readonly value='{_e(kl_)}'>"
                 "<button type='button' id='vh-kop'>Скопировать</button></div>"
                 f"<p>Вставьте его {_e(KUDA_KLYUCH.get(lg, ''))}. В чат ключ не отправляйте и нигде не сохраняйте: "
                 "после закрытия этой страницы его не увидеть — только выпустить новый.</p></div>")
    if itog.get("predupr"):
        h.append("<div class='pr'><b>Перед отключением старых входов — проверьте:</b><ul>"
                 + "".join(f"<li>{_e(x)}</li>" for x in itog["predupr"])
                 + "</ul><p>Обратной кнопки нет. Если всё равно отключаете — введите пароль ниже ещё раз и нажмите:</p>"
                 "<button type='submit' class='op' name='deystvie' value='starye_vykl:tochno'>Всё равно отключить старые входы"
                 "</button></div>")
    h.append("<div class='moy'><b>Ваш нынешний пароль</b><input type='password' name='moy_parol' "
             "autocomplete='current-password'><p class='tih'>Нужен для любого действия в этом блоке: введите и нажмите "
             "нужную кнопку.</p><p class='osh' id='vh-nado' hidden>Сначала введите ваш нынешний пароль.</p></div>")

    lyudi = [z for z in s["vhody"] if z["rol"] in LYUDI]
    for z in lyudi:
        lg, svoy = z["login"], z["login"] == k.get("login")
        zag = f"<b>{_e(z['imya'] or lg)}</b> · {_e(lg)} · {ROL_SLOVOM.get(z['rol'], z['rol'])}"
        if z["do"]:
            zag += " · временный, " + ("срок вышел" if z["istek"] else "до " + _e(kogda(z["do"])))
        if not z["vkl"]:
            st = "вход отключён"
        elif z["istek"]:
            st = "срок временного входа вышел — войти не может"
        elif not z["zadan"]:
            st = ("новый пароль не задан — на форме входа действует ваш прежний пароль" if lg == "boris"
                  else "пароль не задан — войти не может")
        else:
            st = "пароль задан " + _e(kogda(z["parol_ot"]))
            st += (" · вход через форму: " + _e(kogda(z["forma_vhod"]))) if z["forma_vhod"] else " · через форму ещё не входили"
            if z["posl_zapros"]:
                st += " · последний запрос " + _e(kogda(z["posl_zapros"]))
            if z["seansov"]:
                st += f" · открыто сеансов: {z['seansov']}"
        if z["pauza"]:
            st += " · <b>пауза после неверных паролей</b>"
        kn = []
        if z["seansov"] or svoy:
            kn.append(f"<button type='submit' name='deystvie' value='vyiti_vezde:{_e(lg)}'>"
                      + ("Выйти на других устройствах" if svoy else "Выйти везде") + "</button>")
        if z["pauza"]:
            kn.append(f"<button type='submit' name='deystvie' value='snyat_pauzu:{_e(lg)}'>Снять паузу</button>")
        if lg != "boris":
            if z["vkl"]:
                kn.append(f"<button type='submit' class='op' name='deystvie' value='vkl:{_e(lg)}:0' data-sprosit='Отключить вход: "
                          f"{_e(z['imya'] or lg)}? Войти будет нельзя, открытые сеансы закроются.'>Отключить вход</button>")
            else:
                kn.append(f"<button type='submit' name='deystvie' value='vkl:{_e(lg)}:1'>Включить вход</button>")
        h.append(f"<div class='vk{'' if z['vkl'] and not z['istek'] else ' net'}'><div class='vz'>{zag}</div>"
                 f"<div class='vs'>{st}</div><div class='vp'><span>{'Мой новый пароль' if svoy else 'Новый пароль'}</span>"
                 f"<input type='password' class='np' name='parol_{_e(lg)}' autocomplete='new-password'>"
                 f"<button type='button' class='prid' data-dlya='parol_{_e(lg)}'>Придумать</button></div>"
                 + (f"<div class='vb'>{''.join(kn)}</div>" if kn else "") + "</div>")
    h.append("<div class='vb'><button type='submit' class='gl' name='deystvie' value='paroli'>Сохранить пароли</button>"
             "<button type='button' id='vh-glaz'>Показать новые пароли</button></div>"
             "<p class='tih'>Пустое поле — пароль не меняется. Пароль — не короче 10 знаков. «Придумать» собирает пароль прямо "
             "в вашем браузере: его легко продиктовать. После сохранения пароль нигде не показывается — запишите или "
             "продиктуйте сразу.</p>")

    h.append("<h3>Ключи Клода</h3>")
    for lg in ("klod-vykladka", "klod"):
        z = next((x for x in s["vhody"] if x["login"] == lg), None)
        if z is None:
            continue
        if z["zadan"]:
            st = "выпущен " + _e(kogda(z["parol_ot"]))
            st += (" · последний запрос " + _e(kogda(z["posl_zapros"]))) if z["posl_zapros"] else " · запросов этим ключом ещё не было"
            if z["prezhniy_klyuch_do"]:
                st += " · прежний ключ ещё действует — самое позднее до " + _e(kogda(z["prezhniy_klyuch_do"]))
        else:
            st = "не выпущен"
        h.append(f"<div class='vk'><div class='vz'><b>{NAZVANIE_KLYUCHA[lg]}</b> · {lg}</div><div class='vs'>{st}</div>"
                 f"<div class='vb'><button type='submit' name='deystvie' value='klyuch:{lg}'"
                 + (" data-sprosit='Выпустить новый ключ? Его нужно будет сразу вставить в форму Claude.'" if z["zadan"] else "")
                 + ">Выпустить новый</button></div></div>")
    h.append("<p><label><input type='checkbox' name='srazu' value='1'>подозрение на утечку — погасить прежний ключ сразу"
             "</label></p><p class='tih'>Сначала ключ выкладки, потом ключ рутин — когда вы с Клодом оба на связи. "
             "Ключ показывается один раз; Клод его не видит.</p>")
    if s["klodu_otkazano"]:
        h.append("<p>Клоду отказано за 7 дней: " + "; ".join(
            f"{_e(o['put'])} — {int(o['n'])} раз" for o in s["klodu_otkazano"][:10]) + "</p>")
    if s["avtomatika_posl"]:
        h.append(f"<p class='tih'>Автоматика сервера: последний запрос {_e(kogda(s['avtomatika_posl']))}.</p>")
    if s["okno_vykladki_do"]:
        h.append(f"<p><b>Клоду разрешена выкладка ключом рутин до {_e(kogda(s['okno_vykladki_do']))}.</b></p>")

    st_ = s["starye"]
    h.append("<h3>Старые входы</h3>")
    if not st_["vklyucheny"]:
        h.append("<p>Отключены" + (" " + _e(kogda(st_["vyklyucheny_kogda"])) if st_["vyklyucheny_kogda"] else "")
                 + ": общий пароль admin и прежний пароль владельца не принимаются нигде. Аварийный вход — «Добавить вход» "
                 "ниже, с галочкой «временный, на 24 часа».</p>")
    else:
        if not st_["zavedeny"]:
            h.append("<p>Работают как раньше: общий admin и ваш прежний пароль в сером окне браузера. Срок (7 дней) "
                     "начнётся, когда вы зададите первый пароль или выпустите первый ключ.</p>")
        else:
            hv = [x for x in st_["hvost"] if x["login"] == "admin"]
            kto_ = "; ".join(f"{_e(x['adres'])} — {_e(x['chem'])}, последний раз {_e(kogda(x['posl']))}" for x in hv[:6])
            h.append(f"<p>Общий admin: работает до {_e(kogda(st_['do']))} · за сутки запросов: {int(st_['obshchiy_za_sutki'])}"
                     + (f" · за 3 дня: {kto_}" if kto_ else " · за 3 дня им никто не пользовался") + "</p>")
            h.append("<p>Прежний пароль владельца: " + ("в сером окне ещё действует (пока не задан ваш новый пароль или "
                     "ключ выкладки не сделал первый запрос)" if st_["prezhniy_parol_vladelca"] else "уже не действует") + "</p>")
        h.append("<div class='vb'><button type='submit' class='op' name='deystvie' value='starye_vykl' data-sprosit='Отключить "
                 "старые входы? Общий пароль admin перестанет приниматься везде. Обратной кнопки нет.'>Отключить старые входы"
                 "</button></div><p class='tih'>Обратной кнопки нет. В срок старые входы отключатся сами — если ключ рутин "
                 "Клода работает и у вас задан новый пароль; иначе срок сдвинется на сутки, а вам придёт сообщение.</p>")

    if s["vazhnoe"]:
        h.append("<h3>Важное за 7 дней</h3><ul>" + "".join(
            f"<li>{_e(kogda(v['ts']))} — {_e(v['chto'])}</li>" for v in s["vazhnoe"][:15]) + "</ul>")

    h.append("<h3>Добавить вход</h3><div class='ryad'><input type='text' name='novoe_imya' placeholder='Имя: Оля' maxlength='30'>"
             "<input type='text' name='novyy_login' placeholder='Логин латиницей: olya' maxlength='20' autocapitalize='none'>"
             "<label><input type='checkbox' name='sutki' value='1'>временный, на 24 часа</label>"
             "<button type='submit' name='deystvie' value='dobavit'>Добавить вход</button></div>"
             "<p class='tih'>Роль — всегда «админ». Временный вход на 24 часа — аварийный, вместо общего пароля. "
             "После добавления задайте пароль в карточке.</p>")
    h.append("<h3>Выкладка Клодом в аварийном случае</h3><div class='vb'><button type='submit' name='deystvie' "
             "value='okno_vykladki' data-sprosit='Разрешить Клоду выкладку кода ключом рутин на 2 часа?'>"
             "Разрешить Клоду выкладку на 2 часа</button></div>"
             "<p class='tih'>Обычно код выкладывает только ключ выкладки. Эта кнопка — на случай, если им не получается.</p>")
    if s["obshchie_kompyutery"]:
        h.append(f"<h3>Общие компьютеры: {len(s['obshchie_kompyutery'])}</h3>")
        for u in s["obshchie_kompyutery"]:
            h.append("<div class='vk'><div class='vs'>входили: " + _e(", ".join(u["imena"]) or "—")
                     + (" · последний вход " + _e(kogda(u["posl"])) if u["posl"] else "") + "</div><div class='vb'>"
                     f"<button type='submit' name='deystvie' value='ustr_lichnoe:{_e(u['id'])}'>Считать личным</button></div></div>")
        h.append("<p class='tih'>На общем компьютере вход держится до 4:00 утра и галочки «Запомнить» нет.</p>")
    h.append("</form></section>")
    return "".join(h)


def blok_vladelca_bezopasno(request, itog: dict | None = None) -> str:
    """Блок «Входы» или, при сбое, короткая строка: остальная страница владельца должна открыться."""
    try:
        return blok_vladelca(request, itog)
    except Exception:  # noqa: BLE001
        _zhaloba("vhod: блок «Входы» не собран")
        return ("<section class='vh'><h2>Входы на app.kidsup.ru</h2><p class='osh'>Блок «Входы» не открылся — "
                "ошибка записана в журнал сервера. Остальные настройки ниже работают.</p></section>")


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
    if s == "pedklyuch":                     # 14.11.2026: педагог с личным ключом из ссылки
        return "педагог " + (k.get("imya") or "")
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
        _zhaloba("vhod: строка «Важное» не записана")
    if not (db.get_setting("digest_phone") or ""):
        return

    def _f():
        try:
            _otpravit(tekst)
        except Exception:  # noqa: BLE001
            _zhaloba("vhod: сообщение владельцу не отправлено")
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
        # Старым входам в сером окне (общий admin, прежний boris) кеш не трогаем: пока владелец
        # ничего не включил, страницы ведут себя как раньше (кнопка «Назад» возвращает страницу
        # с прокруткой и недописанным текстом). У окна браузера выхода всё равно нет.
        # 14.11.2026: педагогу с личным ключом (sposob "pedklyuch") — тоже как раньше: страницы
        # педагога открывались по ссылке без этих заголовков, и «Назад» не должен стирать
        # недописанные три строки.
        if "cache-control" not in resp.headers and iz(request).get("sposob") not in ("staryy", "pedklyuch"):
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
            _zhaloba("vhod: строка журнала не записана")
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
        _zhaloba("vhod: «последний запрос» не записан")


def eto_server() -> bool:
    """Похоже на процесс сервера: приложение загрузил uvicorn (или gunicorn с его рабочими)."""
    return "uvicorn" in sys.modules or "gunicorn" in sys.modules


def zapusk(server: bool | None = None) -> None:
    """Запуск процесса сервера: ключ автоматики в памяти, файл входов, журнал.
    main.py зовёт её дважды: при импорте (server=None — только если приложение загрузил
    сервер) и в обработчике запуска приложения (server=True). Вне сервера — контейнер
    ассистента, где модуль вроде perepiska лениво тянет app.main, — ничего не делает: иначе
    db.admin_auth() начинал бы отдавать ключ автоматики, которого боевой сервер не знает, а в
    контейнере появлялся бы data/vhody.db. Тесты зовут vnutr_sozdat() сами."""
    if server is None:
        server = eto_server()
    if not server:
        return
    vnutr_sozdat()
    try:
        with _baza():
            pass
        with db.get_conn() as c:
            _zhurnal_init(c)
    except Exception:  # noqa: BLE001
        _zhaloba("vhod: файл входов не подготовлен — старые входы работают как раньше")


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
        _zhaloba("vhod: метка входа не проверена")
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


# --- консольная команда: python3 -m app.vhod parol <логин> -------------------------------------
# Запасной путь владельца (забыл пароль, пропал vhody.db): консоль сервера в панели Рег.облака.
# Запускать из каталога кода, от того же пользователя и с теми же переменными, что служба
# kidsup (KIDSUP_DATA_DIR). Пошагово — docs/rabota/vosstanovlenie.md.

def _konsol(argv: list, vvod=None, pechat=print) -> int:
    """parol <логин>: дважды спрашивает новый пароль (при вводе не виден), пишет хеш.
    Сам пароль не печатается и никуда, кроме хеша, не попадает. → код выхода."""
    if len(argv) != 2 or argv[0] != "parol":
        pechat("Использование: python3 -m app.vhod parol <логин>   (например: parol boris)")
        return 2
    login = login_po_vvodu(argv[1]) or argv[1].strip()
    z = _zapis(login)
    if z is None:
        pechat("Нет такого входа. Есть: " + ", ".join(x["login"] for x in spisok() if x["rol"] in LYUDI))
        return 1
    if z["rol"] not in LYUDI:
        pechat("Это служебная запись: ей нужен ключ, а не пароль. Ключ выпускает владелец на странице /owner.")
        return 1
    if vvod is None:
        import getpass
        vvod = getpass.getpass
    imya = z["imya"] or login
    p1 = vvod(f"Новый пароль для входа «{imya}» ({login}), при вводе не виден: ")
    p2 = vvod("Ещё раз тот же пароль: ")
    if p1 != p2:
        pechat("Пароли не совпали — ничего не изменено.")
        return 1
    prichina = zadat_parol(login, p1)
    if prichina:
        pechat("Пароль не задан: " + prichina + ". Ничего не изменено.")
        return 1
    try:
        otmetit_vazhnoe(f"пароль задан из консоли сервера: {login}", imya="консоль сервера")
    except Exception:  # noqa: BLE001
        _zhaloba("vhod: строка «Важное» не записана")
    pechat(f"Пароль задан: {imya} ({login}). Прежние сеансы этого входа закрыты. Вход — на странице app.kidsup.ru/vhod.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_konsol(sys.argv[1:]))
