# -*- coding: utf-8 -*-
"""Видеозапись занятий «Музыка и речь» — архив методики (решение Бориса 14.11.2026).

Все занятия групп «Музыка и речь» (кабинет К1, 14 занятий в неделю по 50 минут) пишутся
на видео и складываются на Яндекс.Диск центра — «архив методики, только для внутреннего
пользования». Пишет наш сервер; в Ivideon ничего не меняем (ни настроек, ни записи в облаке).

Как устроено:
• расписание — локальная таблица lessons (синк МойКласса). Минутный цикл автопилота зовёт
  tick(): за 30 минут до начала занятия, подходящего под фильтр групп (по умолчанию имя группы
  содержит «Музыка и речь»; отменённые и удалённые в МойКлассе не пишем), запускается поток
  записи этого занятия. Дальше поток сам ждёт нужного момента — цикл автопилота бывает занят
  по несколько минут, а запись должна начаться вовремя;
• кабинет занятия → камера: настройка zapis_kamery («К1 = Комната 1 = <id камеры>»). У занятия
  без кабинета — первая камера списка (все занятия МиР идут в К1);
• за 2 минуты до начала — свежая ссылка на живой поток (ivideon.potok_ssylka: браузер на
  ~30 секунд под общей с фото-явкой блокировкой kadry._snap_lock — два Chromium сервер с 2 ГБ
  памяти не тянет). С начала−1 мин до конца+1 мин ffmpeg пишет поток БЕЗ перекодирования
  (-c copy, -t — длительность, чтобы файл закрывался сам; mp4 с +faststart) в
  data/zapisi/<дата>/«2026-11-16 10-00 МиР Гр4.mp4». Имён детей в имени файла нет;
• оборвался поток — новая ссылка, и запись продолжается во вторую часть («… (часть 2).mp4»)
  до конца занятия;
• после занятия файл уходит на Яндекс.Диск по WebDAV (логин и пароль приложения — настройки
  yadisk_login / yadisk_password; пароль — секрет, не печатается и не пишется в журнал):
  папки создаются MKCOL («/Архив методики/Музыка и речь/2026-11»), размер на Диске
  проверяется PROPFIND, и только после совпадения локальный файл удаляется. Не принял Диск —
  повтор раз в час. Публичных ссылок сервер не создаёт;
• диск сервера (20 ГБ): свободно меньше 3 ГБ — запись не начинается; невыгруженных записей
  больше 6 ГБ — занятие пропускается с причиной. Пока пароль Диска не введён, запись идёт,
  файлы ждут на сервере в пределах этих 6 ГБ;
• сбои (поток, ffmpeg, Диск, место, нет пароля) — один пункт Борису в пульт в день на тип
  сбоя, без телефонов и имён детей; подробности — на /owner, блок «Запись занятий».
"""
from __future__ import annotations

import base64
import html
import http.client
import logging
import re
import secrets
import shutil
import ssl
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

from . import config, db

log = logging.getLogger("kidsup.zapis")

DIR = config.DATA_DIR / "zapisi"
# Адрес WebDAV — постоянная, а не настройка: пароль приложения уходит только сюда, и сменить
# адрес запросом к /api/settings (увести пароль на чужой сервер) нельзя. Тесты подменяют его
# на свой локальный сервер.
WEBDAV = "https://webdav.yandex.ru"

FILTR_PO_UMOLCHANIYU = "Музыка и речь"
KAMERY_PO_UMOLCHANIYU = "К1 = Комната 1 = 100-TYpMigSeczbQqK032w6QzZ"
PAPKA_PO_UMOLCHANIYU = "/Архив методики/Музыка и речь"
LOGIN_PO_UMOLCHANIYU = "hello@kidsup.ru"
# сокращения названий для имени файла: «2627_РР.Музыка и речь_Группа 4_…» → «МиР Гр4»
KOROTKO = (("Музыка и речь", "МиР"),)

ZARANEE = timedelta(minutes=30)       # поток записи занятия запускается за 30 минут
SSYLKA_ZA = timedelta(minutes=2)      # свежая ссылка на поток — за 2 минуты до начала
NACHALO_ZA = timedelta(minutes=1)     # пишем с начала−1 мин…
KONEC_POSLE = timedelta(minutes=1)    # …до конца+1 мин
DLIT_BEZ_KONCA = timedelta(minutes=50)  # у занятия не указан конец — считаем 50 минут
PROBA_S = 120                         # пробная запись — 2 минуты
GB = 1024 ** 3
MIN_SVOBODNO = 3 * GB                 # свободно меньше — запись не начинается
MAX_LOKALNO = 6 * GB                  # невыгруженных больше — занятие пропускается
REZERV = 1 * GB                       # столько оставляем свободным во время записи (-fs)
MAX_FAYL = 4 * GB                     # одна часть записи не больше
MIN_FAYL = 100_000                    # меньше — не запись (плеер отдал заставку или поток пуст)
POVTOR_VYGRUZKI = timedelta(hours=1)
RW_TIMEOUT_S = 20                     # поток молчит дольше — ffmpeg закрывает файл, берём новую ссылку
SROK_METKI = 24 * 3600

_lock = threading.Lock()
_busy: set[int] = set()               # id записей, у которых сейчас идёт поток
_vygr_lock = threading.Lock()         # выгрузка на Диск — по одному файлу
_METKI: dict = {}                     # одноразовые метки формы блока на /owner
_vosstanovleno = [False]
_zvuk_aac = [False]                   # контейнер не принял звук как есть — звук в AAC (видео всё равно copy)


# --- подменяемые точки (тесты) ----------------------------------------------------------------

def _seychas() -> datetime:
    """Московское время без пояса — как begin_time/end_time занятий."""
    from .autopilot import _now
    return _now().replace(tzinfo=None)


def _spat(sek: float) -> None:
    if sek > 0:
        time.sleep(sek)


def _potok(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True, name=f"zapis-{getattr(fn, '__name__', '')}").start()


def _ffmpeg_exe() -> str | None:
    from .deti_video import ffmpeg
    return ffmpeg()


def _disk_usage():
    return shutil.disk_usage(config.DATA_DIR)


def _poluchit_ssylku(kam: dict) -> dict:
    """{"ok": True, "url": …} или {"ok": False, "error": …}. Настоящая — браузер и Ivideon."""
    from . import ivideon
    return ivideon.potok_ssylka(kam.get("imya") or "", kam.get("cam_id") or "")


# --- настройки ----------------------------------------------------------------------------------

def vklyucheno() -> bool:
    """По умолчанию включено: решение владельца 14.11 — записывать все занятия МиР."""
    return (db.get_setting("zapis_on", "1") or "1").strip() != "0"


def filtry() -> list[str]:
    s = db.get_setting("zapis_filtr") or FILTR_PO_UMOLCHANIYU
    out = [x.strip() for x in re.split(r"[;\n]", s) if x.strip()]
    return out or [FILTR_PO_UMOLCHANIYU]


def komnata_norm(s: str) -> str:
    """«к1», «K1» (латиница), «Кабинет 1» → «К1»; другое — как есть."""
    m = re.fullmatch(r"\s*[кКkK]\s*(?:аб(?:инет)?\.?)?\s*(\d+)\s*", s or "")
    return "К" + m.group(1) if m else (s or "").strip()


def razobrat_kamery(s: str) -> tuple[dict, list[str]]:
    """«К1 = Комната 1 = <id>; К2 = Комната 2» → ({"К1": {...}}, [ошибки])."""
    out: dict = {}
    osh: list[str] = []
    for chast in re.split(r"[;\n]", s or ""):
        if not chast.strip():
            continue
        p = [x.strip() for x in chast.split("=")]
        kom = komnata_norm(p[0])
        if not re.fullmatch(r"К\d{1,2}", kom):
            osh.append(f"«{chast.strip()[:40]}»: сначала кабинет, например К1")
            continue
        imya = p[1] if len(p) > 1 and p[1] else "Комната " + kom[1:]
        cam = p[2] if len(p) > 2 else ""
        if len(imya) > 60 or re.search(r"[<>\"'`]", imya):
            osh.append(f"«{chast.strip()[:40]}»: название камеры — как в Ivideon, например «Комната 1»")
            continue
        if cam and not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", cam):
            osh.append(f"«{chast.strip()[:40]}»: id камеры — латиница, цифры, «-» и «_»")
            continue
        if len(p) > 3:
            osh.append(f"«{chast.strip()[:40]}»: лишнее «=»")
            continue
        out[kom] = {"komnata": kom, "imya": imya, "cam_id": cam}
    return out, osh


def kamery() -> dict:
    k, _ = razobrat_kamery(db.get_setting("zapis_kamery") or KAMERY_PO_UMOLCHANIYU)
    if not k:
        k, _ = razobrat_kamery(KAMERY_PO_UMOLCHANIYU)
    return k


def papka() -> str:
    return _papka_norm(db.get_setting("zapis_papka") or PAPKA_PO_UMOLCHANIYU) or PAPKA_PO_UMOLCHANIYU


def _papka_norm(s: str) -> str:
    """Путь папки на Диске: «/» в начале, без «..», без служебных знаков. Плохой → ""."""
    s = (s or "").strip().replace("\\", "/")
    if not s:
        return ""
    if not s.startswith("/"):
        s = "/" + s
    chasti = [c.strip() for c in s.split("/") if c.strip()]
    if not chasti or any(c in (".", "..") for c in chasti) or re.search(r"[\x00-\x1f<>:\"|?*#%]", s):
        return ""
    return "/" + "/".join(chasti)


def login_diska() -> str:
    return (db.get_setting("yadisk_login") or LOGIN_PO_UMOLCHANIYU).strip()


def _parol_diska() -> str:
    return (db.get_setting("yadisk_password") or "").strip()


def parol_zadan() -> bool:
    return bool(_parol_diska())


# --- занятия ------------------------------------------------------------------------------------

def korotkoe_imya(cname: str, class_id=None) -> str:
    """Имя группы для файла: «2627_РР.Музыка и речь_Группа 4_пн - пт_10:00_1,3 - 1,8» → «МиР Гр4».
    В имени файла — только сокращение и номер группы: в названиях индивидуальных занятий
    бывают имена детей, поэтому само название группы в файл не попадает."""
    s = cname or ""
    kor = next((ab for full, ab in KOROTKO if full.casefold() in s.casefold()), "Занятие")
    m = re.search(r"(?:Группа|Гр)\.?\s*(\d{1,3})", s, re.IGNORECASE)
    if m:
        return f"{kor} Гр{m.group(1)}"
    return f"{kor} группа {int(class_id)}" if str(class_id or "").isdigit() else kor


def _podhodit(cname: str) -> bool:
    n = (cname or "").casefold()
    if "заявк" in n:
        return False
    return any(f.casefold() in n for f in filtry())


def _vremya(day: str, t: str | None) -> datetime | None:
    try:
        return datetime.strptime(f"{day} {(t or '')[:5]}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _razobrat(row, rooms: dict, udal: set, kam: dict) -> dict | None:
    """Строка lessons+classes → занятие для записи (или None, если группа не та)."""
    import json
    lid, day, bt, et, st, raw, cid, cname = row
    if not _podhodit(cname):
        return None
    try:
        j = json.loads(raw or "{}")
    except ValueError:
        j = {}
    begin = _vremya(day, bt)
    if begin is None:
        return None
    end = _vremya(day, et) if et else None
    if end is None or end <= begin:
        end = begin + DLIT_BEZ_KONCA
    try:
        rid = int(j.get("roomId") or 0) if isinstance(j, dict) else 0
    except (TypeError, ValueError):
        rid = 0
    kom = komnata_norm(rooms.get(rid, ""))
    kamera = kam.get(kom) if kom else None
    pochemu = ""
    pervaya = next(iter(kam.values()), None)
    if not kom and pervaya:
        kamera = pervaya
        pochemu = f"кабинет в МойКлассе не указан — пишем {pervaya['komnata']}"
    if st == 2:
        pochemu = "отменено в МойКлассе"
    elif int(lid) in udal:
        pochemu = "удалено в МойКлассе"
    elif kamera is None:
        pochemu = f"кабинет {kom} — камеры для записи нет (настройка «Камера»)"
    return {"lesson_id": int(lid), "day": day, "begin": begin, "end": end, "class_id": cid,
            "gruppa": korotkoe_imya(cname, cid), "komnata": kom or (kamera or {}).get("komnata", ""),
            "kamera": kamera, "pisat": st != 2 and int(lid) not in udal and kamera is not None,
            "pochemu": pochemu}


def _zanyatiya_gde(where: str, args: tuple) -> list[dict]:
    kam = kamery()
    with db.get_conn() as conn:
        try:
            rooms = {int(r[0]): (r[1] or "") for r in conn.execute("SELECT id, name FROM rooms").fetchall()}
        except Exception:  # noqa: BLE001
            rooms = {}
        udal = db.udalennye("lessons", conn)
        rows = conn.execute(
            "SELECT l.id, l.date, l.begin_time, l.end_time, l.status, l.raw, l.class_id, c.name FROM lessons l "
            "JOIN classes c ON c.id = l.class_id WHERE " + where + " ORDER BY l.date, l.begin_time", args).fetchall()
    out = []
    for r in rows:
        z = _razobrat(tuple(r), rooms, udal, kam)
        if z:
            out.append(z)
    return out


def zanyatiya(day: str) -> list[dict]:
    """Занятия дня, подходящие под фильтр групп (и те, что писать не будем, — с причиной)."""
    return _zanyatiya_gde("l.date = ?", (day,))


def _zanyatie(lesson_id: int) -> dict | None:
    z = _zanyatiya_gde("l.id = ?", (int(lesson_id),))
    return z[0] if z else None


# --- база ---------------------------------------------------------------------------------------

def _init(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS zapis (
        id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, lesson_id INTEGER, day TEXT,
        begin_time TEXT, end_time TEXT, gruppa TEXT, komnata TEXT, kamera TEXT,
        status TEXT, prichina TEXT, proba INTEGER DEFAULT 0, sozdano TEXT, obnovleno TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS zapis_fayly (
        id INTEGER PRIMARY KEY AUTOINCREMENT, zapis_id INTEGER, chast INTEGER, fayl TEXT, imya TEXT,
        razmer INTEGER DEFAULT 0, status TEXT, oshibka TEXT, popytok INTEGER DEFAULT 0,
        posl_popytka TEXT, disk_put TEXT, vygruzheno TEXT, nachalo TEXT, konec TEXT, zametka TEXT)""")
    # zametka — как прошла запись части (ffmpeg, перезапуск сервера); oshibka — почему не ушла на Диск
    conn.execute("""CREATE TABLE IF NOT EXISTS zapis_sboi (
        day TEXT, tip TEXT, ts TEXT, tekst TEXT, n INTEGER DEFAULT 1, PRIMARY KEY (day, tip))""")


def _iso(t: datetime | None = None) -> str:
    return (t or _seychas()).isoformat(timespec="seconds")


def _status(zid: int, status: str, prichina: str | None = None) -> None:
    with db.get_conn() as conn:
        _init(conn)
        if prichina is None:
            conn.execute("UPDATE zapis SET status=?, obnovleno=? WHERE id=?", (status, _iso(), zid))
        else:
            conn.execute("UPDATE zapis SET status=?, prichina=?, obnovleno=? WHERE id=?",
                         (status, _maska(prichina)[:400], _iso(), zid))


def _zapis(zid: int) -> dict | None:
    with db.get_conn() as conn:
        _init(conn)
        r = conn.execute("SELECT * FROM zapis WHERE id=?", (zid,)).fetchone()
    return dict(r) if r else None


def _fayly(zid: int) -> list[dict]:
    with db.get_conn() as conn:
        _init(conn)
        return [dict(r) for r in conn.execute("SELECT * FROM zapis_fayly WHERE zapis_id=? ORDER BY chast, id", (zid,))]


def _maska(s: str) -> str:
    """Убрать из текста ошибки токен потока, адрес с ним и пароль Диска."""
    s = str(s or "")
    s = re.sub(r"access_token=[^&\s'\"<>()\[\],;]+", "access_token=***", s)
    s = re.sub(r"(?i)(authorization:\s*basic\s+)[A-Za-z0-9+/=]+", r"\1***", s)
    p = _parol_diska()
    if p:
        s = s.replace(p, "***")
    return s


# --- сбои → пульт -------------------------------------------------------------------------------

SBOI = {
    "potok": "не удалось получить видеопоток камеры",
    "ffmpeg": "запись оборвалась или ffmpeg не смог писать",
    "disk": "Яндекс.Диск не принял файл",
    "mesto": "не хватает места на сервере — занятие не записано",
    "parol": "записи ждут на сервере — нет пароля приложения Яндекса",
}


def _sboy_bylo(tip: str) -> bool:
    with db.get_conn() as conn:
        _init(conn)
        return conn.execute("SELECT 1 FROM zapis_sboi WHERE day=? AND tip=?",
                            (_seychas().date().isoformat(), tip)).fetchone() is not None


def _sboy(tip: str, podrobno: str) -> bool:
    """Один пункт Борису в пульт в день на тип сбоя; остальные — счётчиком на /owner."""
    day = _seychas().date().isoformat()
    podrobno = _maska(podrobno)[:300]
    with db.get_conn() as conn:
        _init(conn)
        cur = conn.execute("INSERT OR IGNORE INTO zapis_sboi (day, tip, ts, tekst, n) VALUES (?,?,?,?,1)",
                           (day, tip, _iso(), podrobno))
        novyy = bool(cur.rowcount)
        if not novyy:
            conn.execute("UPDATE zapis_sboi SET n = n + 1 WHERE day=? AND tip=?", (day, tip))
    log.warning("запись занятий, сбой %s: %s", tip, podrobno)
    if not novyy:
        return False
    tekst = (f"📹 Запись занятий: {SBOI.get(tip, tip)}. {podrobno} "
             "Подробности и пробная запись — app.kidsup.ru/owner#zapis")
    try:
        from . import autopilot
        autopilot.inbox_add(tekst, "", "Борис", source="запись занятий")
    except Exception:  # noqa: BLE001
        log.exception("пункт о сбое записи в пульт не поставлен")
    return True


# --- место на диске -----------------------------------------------------------------------------

def _lokalno() -> tuple[int, int]:
    """(байт, файлов) записей, лежащих на сервере (ещё не выгруженных или пишущихся)."""
    n = b = 0
    if DIR.exists():
        for p in DIR.rglob("*"):
            try:
                if p.is_file():
                    b += p.stat().st_size
                    n += p.suffix == ".mp4"
            except OSError:
                pass
    return b, n


def mesto() -> dict:
    try:
        u = _disk_usage()
        vsego, svob = int(u.total), int(u.free)
    except Exception:  # noqa: BLE001
        vsego = svob = 0
    lok, n = _lokalno()
    return {"vsego": vsego, "svobodno": svob, "lokalno": lok, "lokalno_faylov": n}


def _gb(b: int) -> str:
    return f"{b / GB:.1f}".replace(".", ",")


def _mb(b: int) -> str:
    if b < 1024 * 1024:
        return f"{max(1, round(b / 1024)) if b else 0} КБ"
    return f"{round(b / 1024 / 1024)} МБ" if b < GB else f"{_gb(b)} ГБ"


def _net_mesta(m: dict) -> str:
    """Почему запись начинать нельзя; "" — можно."""
    if m["svobodno"] < MIN_SVOBODNO:
        return (f"на сервере свободно {_gb(m['svobodno'])} ГБ — меньше {MIN_SVOBODNO // GB} ГБ, "
                "запись не начинаю")
    if m["lokalno"] >= MAX_LOKALNO:
        return (f"на сервере ждут выгрузки {_gb(m['lokalno'])} ГБ записей — больше {MAX_LOKALNO // GB} ГБ: "
                "занятие пропущено, пока записи не уйдут на Диск")
    return ""


def _predel_fayla(m: dict) -> int:
    return int(min(MAX_FAYL, MAX_LOKALNO - m["lokalno"], m["svobodno"] - REZERV))


# --- ffmpeg -------------------------------------------------------------------------------------

def mp4_celyy(path: Path) -> bool:
    """Файл mp4 закрыт: верхние коробки идут до конца файла и среди них есть оглавление (moov).
    Оборванная без завершения запись оглавления не имеет — такой файл не открывается."""
    try:
        razmer = path.stat().st_size
        vidno = set()
        with open(path, "rb") as f:
            pos = 0
            while pos + 8 <= razmer:
                f.seek(pos)
                h = f.read(8)
                n, tip = int.from_bytes(h[:4], "big"), h[4:8]
                if n == 1:
                    n = int.from_bytes(f.read(8), "big")
                elif n == 0:
                    n = razmer - pos
                if n < 8:
                    return False
                vidno.add(tip)
                pos += n
        return pos == razmer and b"moov" in vidno
    except OSError:
        return False


def komanda(exe: str, url: str, path: Path, dlit: int, predel: int) -> list[str]:
    zvuk = ["-c:v", "copy", "-c:a", "aac", "-b:a", "64k"] if _zvuk_aac[0] else ["-c", "copy"]
    return [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-rw_timeout", str(int(RW_TIMEOUT_S * 1_000_000)),   # столько секунд без данных — поток оборвался
            "-i", url, "-t", str(int(dlit)),
            "-map", "0:v:0", "-map", "0:a:0?", *zvuk,
            "-fs", str(int(predel)), "-movflags", "+faststart", "-f", "mp4", str(path)]


def _ffmpeg(url: str, path: Path, dlit: int, predel: int, stop=None) -> tuple[int, str]:
    """Пишет поток в файл; → (код выхода, хвост ошибки без токена)."""
    exe = _ffmpeg_exe()
    if not exe:
        return -1, "ffmpeg не найден на сервере"
    path.parent.mkdir(parents=True, exist_ok=True)
    zhurnal = path.with_name("." + path.stem + ".ffmpeg.log")
    t0 = time.monotonic()
    with open(zhurnal, "wb") as err:
        p = subprocess.Popen(komanda(exe, url, path, dlit, predel), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=err)
        i = 0
        while True:
            try:
                rc = p.wait(timeout=5)
                break
            except subprocess.TimeoutExpired:
                i += 1
                prosrocheno = time.monotonic() - t0 > dlit + 180
                if prosrocheno or (stop is not None and i % 6 == 0 and stop()):
                    p.terminate()            # ffmpeg по SIGTERM закрывает файл как положено
                    try:
                        rc = p.wait(timeout=60)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        rc = p.wait()
                    break
    try:
        hvost = zhurnal.read_bytes()[-2000:].decode("utf-8", "replace")
    except OSError:
        hvost = ""
    zhurnal.unlink(missing_ok=True)
    hvost = " ".join(hvost.split())
    return rc, _maska(hvost)[-1000:]          # хранится и показывается хвост в 300 знаков (_pisat)


def _imya_fayla(z: dict, chast: int) -> str:
    vrem = (z.get("begin_time") or "00:00")[:5].replace(":", "-")
    if z.get("proba"):
        osnova = f"{z['day']} {vrem} Проба {z.get('komnata') or ''}".strip()
    else:
        osnova = f"{z['day']} {vrem} {z.get('gruppa') or 'Занятие'}"
    if chast > 1:
        osnova += f" (часть {chast})"
    return re.sub(r"[\\/:*?\"<>|#%\x00-\x1f]", "-", osnova) + ".mp4"


# --- поток записи одного занятия ----------------------------------------------------------------

AKTIVNYE = ("zhdyot", "ssylka", "pishetsya")
# так ffmpeg говорит, что звук потока (G.711 и т. п.) в mp4 как есть не ложится
ZVUK_NE_DLYA_MP4 = re.compile(r"not currently supported in container|Could not find tag for codec")
# итог записи, при котором занятие «пропущено», а не «не записано из-за сбоя»
PROPUSK = ("на сервере", "запись выключена", "занятие уже")


def _klyuch(zan: dict) -> str:
    """Ключ записи — день и занятие: перенесённое на другой день занятие пишется заново в свой день."""
    return f"{zan['day']}:{zan['lesson_id']}"


def _start(zid: int) -> bool:
    with _lock:
        if zid in _busy:
            return False
        _busy.add(zid)
    try:
        _potok(_rabota, zid)
    except Exception:
        with _lock:
            _busy.discard(zid)
        raise
    return True


def _rabota(zid: int) -> None:
    try:
        _rabota_(zid)
    except Exception as e:  # noqa: BLE001
        log.exception("запись %s упала", zid)
        _status(zid, "oshibka", f"сбой записи: {type(e).__name__}")
    finally:
        with _lock:
            _busy.discard(zid)


def _rabota_(zid: int) -> None:
    z = _zapis(zid)
    if not z:
        return
    proba = bool(z["proba"])
    if proba:
        t_start = _seychas()
        t_stop = t_start + timedelta(seconds=PROBA_S)
        kam = kamery().get(z["komnata"]) or next(iter(kamery().values()))
    else:
        # ждём «за 2 минуты до начала», заглядывая в расписание раз в минуту: занятие могли
        # отменить или перенести, владелец — выключить запись
        while True:
            zan = _zanyatie(z["lesson_id"])
            if zan is None:
                _status(zid, "propusk", "занятие пропало из расписания или группа больше не подходит под фильтр")
                return
            if zan["day"] != z["day"]:
                _status(zid, "propusk", f"занятие перенесено на {_den(zan['day'])} — запишется в тот день")
                return
            if not zan["pisat"]:
                _status(zid, "propusk", zan["pochemu"])
                return
            if not vklyucheno():
                _status(zid, "propusk", "запись выключена владельцем")
                return
            t_ssylka = zan["begin"] - SSYLKA_ZA
            now = _seychas()
            if now >= t_ssylka:
                break
            _spat(min(60.0, (t_ssylka - now).total_seconds()))
        kam = zan["kamera"]
        t_start, t_stop = zan["begin"] - NACHALO_ZA, zan["end"] + KONEC_POSLE
        with db.get_conn() as conn:
            conn.execute("UPDATE zapis SET begin_time=?, end_time=?, gruppa=?, komnata=?, kamera=? WHERE id=?",
                         (zan["begin"].strftime("%H:%M"), zan["end"].strftime("%H:%M"), zan["gruppa"],
                          kam["komnata"], kam["imya"], zid))
        z = _zapis(zid)
    est = [f for f in _fayly(zid) if f["status"] in ("gotov", "vygruzhen")]
    chast = max([f["chast"] for f in est] or [0]) + 1
    itog = ""
    if _seychas() >= t_stop - timedelta(seconds=15):
        itog = "занятие уже закончилось — сервер не успел начать запись" if not est else ""
    else:
        m = mesto()
        itog = _net_mesta(m)
        if itog:
            _sboy("mesto", f"{_kto(z)}: {itog}.")
        else:
            itog = _pisat(zid, z, kam, t_start, t_stop, chast, proba)
    gotovye = [f for f in _fayly(zid) if f["status"] in ("gotov", "vygruzhen")]
    z = _zapis(zid) or z
    if gotovye:
        n = len(gotovye)
        _status(zid, "zapisano", "; ".join(x for x in (f"{n} части — поток обрывался" if n > 1 else "", itog) if x))
        vygruzit_zapis(zid)
    elif itog.startswith(PROPUSK):
        _status(zid, "propusk", itog)
    else:
        _status(zid, "oshibka", itog or z.get("prichina") or "запись не получилась")


def _kto(z: dict) -> str:
    if z.get("proba"):
        return f"пробная запись {z.get('komnata') or ''}".strip()
    return f"{(z.get('begin_time') or '')[:5]} {z.get('gruppa') or ''}, {z.get('komnata') or ''}".strip(", ")


def _pisat(zid: int, z: dict, kam: dict, t_start: datetime, t_stop: datetime, chast: int, proba: bool) -> str:
    """Ссылка → ffmpeg → часть; оборвалось — новая ссылка и следующая часть, до t_stop.
    → "" (дописали до конца) или почему остановились раньше."""
    neudach_ssylki = 0
    bystryh_padeniy = 0
    posl_osh = ""
    stop = (lambda: False) if proba else (lambda: not vklyucheno())
    while True:
        now = _seychas()
        if now >= t_stop - timedelta(seconds=15):
            return "" if any(f["status"] in ("gotov", "vygruzhen") for f in _fayly(zid)) else posl_osh
        if not proba and not vklyucheno():
            return "запись выключена владельцем во время занятия"
        _status(zid, "ssylka")
        try:
            r = _poluchit_ssylku(kam) or {}
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:150]}"}
        url = str(r.get("url") or "") if r.get("ok") else ""
        if not url:
            neudach_ssylki += 1
            osh = _maska(r.get("error") or "нет ссылки")[:200]
            posl_osh = f"не удалось получить поток: {osh}"
            _status(zid, "ssylka", f"не удалось получить поток ({neudach_ssylki}-я попытка): {osh}")
            _sboy("potok", f"{_kto(z)}: {osh}.")
            _spat(30 if neudach_ssylki < 4 else 60)
            continue
        now = _seychas()
        if now < t_start:
            _spat((t_start - now).total_seconds())
            now = _seychas()
        dlit = int((t_stop - now).total_seconds())
        if dlit < 15:
            return ""
        m = mesto()
        predel = _predel_fayla(m)
        if predel < 50 * 1024 * 1024:
            pochemu = _net_mesta(m) or f"на сервере свободно {_gb(m['svobodno'])} ГБ — писать некуда"
            _sboy("mesto", f"{_kto(z)}: {pochemu}.")
            return pochemu
        imya = _imya_fayla(z, chast)
        otn = f"{z['day']}/{imya}"
        path = DIR / otn
        with db.get_conn() as conn:
            _init(conn)
            fid = conn.execute("INSERT INTO zapis_fayly (zapis_id, chast, fayl, imya, status, nachalo) "
                               "VALUES (?,?,?,?,'pishetsya',?)", (zid, chast, otn, imya, _iso(now))).lastrowid
        _status(zid, "pishetsya", "")
        rc, err = _ffmpeg(url, path, dlit, predel, stop)
        razmer = path.stat().st_size if path.exists() else 0
        celyy = razmer >= MIN_FAYL and mp4_celyy(path)
        konec = _seychas()
        zvuk_ne_tot = not _zvuk_aac[0] and rc != 0 and bool(ZVUK_NE_DLYA_MP4.search(err))
        err = err[-300:]
        with db.get_conn() as conn:
            if celyy:
                conn.execute("UPDATE zapis_fayly SET status='gotov', razmer=?, konec=?, zametka=? WHERE id=?",
                             (razmer, _iso(konec), "" if rc == 0 else f"ffmpeg {rc}: {err}"[:300], fid))
            else:
                conn.execute("UPDATE zapis_fayly SET status='bityy', razmer=0, konec=?, zametka=? WHERE id=?",
                             (_iso(konec), (f"ffmpeg {rc}: {err}" if err or rc else "файл пустой")[:300], fid))
        if not celyy:
            path.unlink(missing_ok=True)
            posl_osh = f"ffmpeg не записал файл ({rc}: {err[-120:]})" if (rc or err) else "ffmpeg не записал файл"
        if zvuk_ne_tot:
            _zvuk_aac[0] = True              # mp4 не берёт этот звук как есть: видео по-прежнему copy, звук — в AAC
            log.warning("звук потока не ложится в mp4 как есть — дальше звук в AAC (видео без перекодирования)")
            continue
        if konec >= t_stop - timedelta(seconds=15):
            return "" if any(f["status"] in ("gotov", "vygruzhen") for f in _fayly(zid)) else posl_osh
        if not proba and not vklyucheno():
            return "запись выключена владельцем во время занятия"
        # оборвалось раньше конца занятия: новая ссылка и следующая часть
        ushlo = (konec - now).total_seconds()
        opis = (f"{_kto(z)}: часть {chast} оборвалась через {int(ushlo // 60)} мин"
                + (f" (ffmpeg {rc}: {err[-120:]})" if (rc or err) else "") + " — продолжаю в следующую часть.")
        _status(zid, "pishetsya", opis)
        _sboy("ffmpeg", opis)
        if celyy:
            chast += 1
        bystryh_padeniy = bystryh_padeniy + 1 if ushlo < 30 else 0
        _spat(5 if bystryh_padeniy == 0 else min(120, 15 * bystryh_padeniy))


# --- выгрузка на Яндекс.Диск по WebDAV ----------------------------------------------------------

class DiskOshibka(Exception):
    pass


PROPFIND_RAZMER = b'<?xml version="1.0" encoding="utf-8"?><propfind xmlns="DAV:"><prop><getcontentlength/></prop></propfind>'
PROPFIND_MESTO = (b'<?xml version="1.0" encoding="utf-8"?><propfind xmlns="DAV:"><prop><quota-available-bytes/>'
                  b'<quota-used-bytes/></prop></propfind>')


class _Dav:
    """Минимальный WebDAV: MKCOL, PUT, PROPFIND. Пароль — только в заголовке Authorization."""

    def __init__(self, login: str, parol: str, baza: str | None = None):
        u = urlsplit(baza or WEBDAV)
        self.https = u.scheme == "https"
        self.host, self.port = u.hostname, u.port
        self.pref = u.path.rstrip("/")
        self._auth = "Basic " + base64.b64encode(f"{login}:{parol}".encode("utf-8")).decode("ascii")
        self.est: set[str] = set()

    def _conn(self, timeout: float):
        if self.https:
            return http.client.HTTPSConnection(self.host, self.port or 443, timeout=timeout,
                                               context=ssl.create_default_context(), blocksize=256 * 1024)
        return http.client.HTTPConnection(self.host, self.port or 80, timeout=timeout, blocksize=256 * 1024)

    def _zapros(self, metod: str, put: str, body=None, hdr: dict | None = None, timeout: float = 60) -> tuple[int, bytes]:
        c = self._conn(timeout)
        try:
            c.request(metod, self.pref + quote(put, safe="/"), body=body,
                      headers={"Authorization": self._auth, "User-Agent": "KidsUP-zapis/1", **(hdr or {})})
            r = c.getresponse()
            return r.status, r.read(1 << 20)
        except (OSError, http.client.HTTPException) as e:
            raise DiskOshibka(f"Диск не ответил ({metod}): {type(e).__name__}") from None
        finally:
            c.close()

    @staticmethod
    def _proverit(st: int, chto: str) -> None:
        if st in (401, 403):
            raise DiskOshibka(f"Диск не принял логин или пароль приложения ({st}) — проверьте пароль на /owner")
        if st == 507:
            raise DiskOshibka("на Яндекс.Диске кончилось место (507)")
        if st >= 300:
            raise DiskOshibka(f"{chto}: Диск ответил {st}")

    def papki(self, put: str) -> None:
        akk = ""
        for seg in [s for s in put.split("/") if s]:
            akk += "/" + seg
            if akk in self.est:
                continue
            st, _ = self._zapros("MKCOL", akk)
            if st not in (201, 405):           # 405 — папка уже есть
                self._proverit(st if st != 200 else 201, f"папка {akk}")
            self.est.add(akk)

    def razmer(self, put: str) -> int | None:
        st, data = self._zapros("PROPFIND", put, PROPFIND_RAZMER, {"Depth": "0", "Content-Type": "application/xml"})
        if st == 404:
            return None
        self._proverit(st, "проверка размера")
        m = re.search(rb"<(?:[\w-]+:)?getcontentlength[^>]*>\s*(\d+)\s*<", data)
        return int(m.group(1)) if m else None

    def svobodno(self) -> int | None:
        st, data = self._zapros("PROPFIND", "/", PROPFIND_MESTO, {"Depth": "0", "Content-Type": "application/xml"})
        self._proverit(st, "вход на Диск")
        m = re.search(rb"<(?:[\w-]+:)?quota-available-bytes[^>]*>\s*(\d+)\s*<", data)
        return int(m.group(1)) if m else None

    def zalit(self, put: str, path: Path, razmer: int) -> None:
        c = self._conn(600)
        try:
            with open(path, "rb") as f:
                c.request("PUT", self.pref + quote(put, safe="/"), body=f,
                          headers={"Authorization": self._auth, "User-Agent": "KidsUP-zapis/1",
                                   "Content-Type": "video/mp4", "Content-Length": str(razmer)})
                r = c.getresponse()
                st = r.status
                r.read(1 << 16)
        except (OSError, http.client.HTTPException) as e:
            raise DiskOshibka(f"файл не ушёл на Диск: {type(e).__name__}") from None
        finally:
            c.close()
        # 202 — Диск принял и дообрабатывает: размер сверит PROPFIND, не сошлось — повтор через час,
        # и тогда PROPFIND первым увидит, что файл уже целиком на Диске
        if st not in (200, 201, 202, 204):
            self._proverit(st if st >= 300 else 500, "загрузка файла")


def _disk_put(f: dict, z: dict) -> str:
    return f"{papka()}/{(z.get('day') or '')[:7]}/{f['imya']}"


def proverit_disk() -> dict:
    """Вход на Диск этим логином и паролем приложения: {"ok", "svobodno"|"error"}."""
    parol = _parol_diska()
    if not parol:
        return {"ok": False, "error": "пароль приложения не введён"}
    try:
        sv = _Dav(login_diska(), parol).svobodno()
        return {"ok": True, "svobodno": sv}
    except DiskOshibka as e:
        return {"ok": False, "error": _maska(str(e))}


def _vygruzit_fayl(fid: int) -> bool:
    with db.get_conn() as conn:
        _init(conn)
        f = conn.execute("SELECT * FROM zapis_fayly WHERE id=?", (fid,)).fetchone()
        f = dict(f) if f else None
        z = conn.execute("SELECT * FROM zapis WHERE id=?", (f["zapis_id"],)).fetchone() if f else None
    if not f or f["status"] != "gotov" or not z:
        return False
    z = dict(z)
    parol = _parol_diska()
    if not parol:
        with db.get_conn() as conn:
            conn.execute("UPDATE zapis_fayly SET oshibka=? WHERE id=?", ("ждёт пароля приложения Яндекса", fid))
        return False
    path = DIR / f["fayl"]
    if not path.exists():
        with db.get_conn() as conn:
            conn.execute("UPDATE zapis_fayly SET status='propal', oshibka=? WHERE id=?",
                         ("файла нет на сервере — выгружать нечего", fid))
        return False
    razmer = path.stat().st_size
    kuda = _disk_put(f, z)
    dav = _Dav(login_diska(), parol)
    try:
        dav.papki(kuda.rsplit("/", 1)[0])
        est = dav.razmer(kuda)
        if est != razmer:
            sv = dav.svobodno()
            if sv is not None and sv < razmer:
                raise DiskOshibka(f"на Яндекс.Диске свободно {_mb(sv)}, а файл — {_mb(razmer)}")
            dav.zalit(kuda, path, razmer)
            est = dav.razmer(kuda)
        if est != razmer:
            raise DiskOshibka(f"размер на Диске ({est if est is not None else 'нет файла'}) не совпал с файлом "
                              f"на сервере ({razmer}) — файл оставлен на сервере")
    except DiskOshibka as e:
        osh = _maska(str(e))[:300]
        with db.get_conn() as conn:
            conn.execute("UPDATE zapis_fayly SET popytok=COALESCE(popytok,0)+1, posl_popytka=?, oshibka=? WHERE id=?",
                         (_iso(), osh, fid))
        _sboy("disk", f"{f['imya']}: {osh}. Повтор раз в час, файл ждёт на сервере.")
        return False
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()                 # папка дня опустела — убираем
    except OSError:
        pass
    with db.get_conn() as conn:
        conn.execute("UPDATE zapis_fayly SET status='vygruzhen', vygruzheno=?, disk_put=?, oshibka='', "
                     "popytok=COALESCE(popytok,0)+1, posl_popytka=? WHERE id=?", (_iso(), kuda, _iso(), fid))
        ostalos = conn.execute("SELECT COUNT(*) FROM zapis_fayly WHERE zapis_id=? AND status IN ('gotov','pishetsya')",
                               (f["zapis_id"],)).fetchone()[0]
        if not ostalos:
            conn.execute("UPDATE zapis SET status='vygruzheno', obnovleno=? WHERE id=? AND status='zapisano'",
                         (_iso(), f["zapis_id"]))
    log.info("запись выгружена на Диск: %s (%s)", f["imya"], _mb(razmer))
    return True


def vygruzit_zapis(zid: int) -> None:
    """Сразу после занятия: все готовые части этой записи — на Диск."""
    with _vygr_lock:
        for f in _fayly(zid):
            if f["status"] == "gotov":
                _vygruzit_fayl(f["id"])


def vygruzit_vse(srazu: bool = False) -> int:
    """Готовые файлы на Диск: srazu — все; иначе — те, чья прошлая попытка была час назад и раньше."""
    n = 0
    with _vygr_lock:
        for fid in _k_vygruzke(srazu):
            n += _vygruzit_fayl(fid)
    return n


def _k_vygruzke(srazu: bool) -> list[int]:
    gran = _iso(_seychas() - POVTOR_VYGRUZKI)
    with db.get_conn() as conn:
        _init(conn)
        rows = conn.execute("SELECT f.id, f.posl_popytka FROM zapis_fayly f JOIN zapis z ON z.id=f.zapis_id "
                            "WHERE f.status='gotov' ORDER BY f.id").fetchall()
    with _lock:
        zanyaty = set(_busy)
    out = []
    for fid, posl in rows:
        if srazu or not posl or posl <= gran:
            out.append(int(fid))
    # части записи, которая ещё идёт, выгрузит её поток сам, после занятия
    if zanyaty:
        with db.get_conn() as conn:
            svoi = {int(r[0]) for r in conn.execute(
                "SELECT id FROM zapis_fayly WHERE zapis_id IN (%s)" % ",".join("?" * len(zanyaty)), tuple(zanyaty))}
        out = [x for x in out if x not in svoi]
    return out


# --- минутный цикл ------------------------------------------------------------------------------

def _vosstanovit() -> None:
    """После перезапуска сервера: части, которые «писались», — закрытый файл в выгрузку, битый — прочь."""
    if _vosstanovleno[0]:
        return
    _vosstanovleno[0] = True
    with db.get_conn() as conn:
        _init(conn)
        rows = [dict(r) for r in conn.execute("SELECT * FROM zapis_fayly WHERE status='pishetsya'")]
    with _lock:
        zanyaty = set(_busy)
    for f in rows:
        if f["zapis_id"] in zanyaty:
            continue
        path = DIR / f["fayl"]
        ok = path.exists() and path.stat().st_size >= MIN_FAYL and mp4_celyy(path)
        with db.get_conn() as conn:
            if ok:
                conn.execute("UPDATE zapis_fayly SET status='gotov', razmer=?, zametka=? WHERE id=?",
                             (path.stat().st_size, "запись прервал перезапуск сервера", f["id"]))
            else:
                conn.execute("UPDATE zapis_fayly SET status='bityy', razmer=0, zametka=? WHERE id=?",
                             ("запись прервал перезапуск сервера — файл не закрылся", f["id"]))
        if not ok:
            path.unlink(missing_ok=True)


def tick(now: datetime | None = None) -> list[int]:
    """Раз в минуту из цикла автопилота: запустить потоки записи ближайших занятий,
    повторить выгрузку, напомнить о пароле. → id записей, чьи потоки запущены."""
    now = (now or _seychas()).replace(tzinfo=None)
    _vosstanovit()
    zapushcheno: list[int] = []
    if vklyucheno():
        day = now.date().isoformat()
        for zan in zanyatiya(day):
            if not zan["pisat"]:
                continue
            t_stop = zan["end"] + KONEC_POSLE
            if not (zan["begin"] - ZARANEE <= now < t_stop - timedelta(minutes=1)):
                continue
            key = _klyuch(zan)
            with db.get_conn() as conn:
                _init(conn)
                conn.execute("INSERT OR IGNORE INTO zapis (key, lesson_id, day, begin_time, end_time, gruppa, komnata, "
                             "kamera, status, prichina, proba, sozdano, obnovleno) VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?)",
                             (key, zan["lesson_id"], day, zan["begin"].strftime("%H:%M"), zan["end"].strftime("%H:%M"),
                              zan["gruppa"], zan["kamera"]["komnata"], zan["kamera"]["imya"], "zhdyot", zan["pochemu"],
                              _iso(now), _iso(now)))
                r = conn.execute("SELECT id, status FROM zapis WHERE key=?", (key,)).fetchone()
            if r and r["status"] in AKTIVNYE and _start(int(r["id"])):
                zapushcheno.append(int(r["id"]))
    # выгрузка: повтор раз в час; без пароля — напоминание владельцу
    if parol_zadan():
        if _k_vygruzke(False) and not _vygr_lock.locked():
            _potok(vygruzit_vse)
    else:
        b, n = _zhdut()
        if n and not _sboy_bylo("parol"):            # раз в день, а не каждую минуту
            _sboy("parol", f"Ждут выгрузки: {n} {_mnogo(n, 'файл', 'файла', 'файлов')}, {_mb(b)}. При "
                           f"{MAX_LOKALNO // GB} ГБ на сервере запись остановится.")
    return zapushcheno


def _zhdut() -> tuple[int, int]:
    with db.get_conn() as conn:
        _init(conn)
        r = conn.execute("SELECT COALESCE(SUM(razmer),0), COUNT(*) FROM zapis_fayly WHERE status='gotov'").fetchone()
    return int(r[0] or 0), int(r[1] or 0)


def _mnogo(n: int, odin: str, dva: str, pyat: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return odin
    return dva if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else pyat


def proba(komnata: str = "") -> dict:
    """«Пробная запись 2 минуты сейчас» с первой камеры списка (К1): пишет и выгружает."""
    kam = kamery()
    k = kam.get(komnata_norm(komnata)) if komnata else None
    k = k or next(iter(kam.values()))
    now = _seychas()
    key = "proba-" + now.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    with db.get_conn() as conn:
        _init(conn)
        zid = conn.execute("INSERT INTO zapis (key, lesson_id, day, begin_time, end_time, gruppa, komnata, kamera, "
                           "status, prichina, proba, sozdano, obnovleno) VALUES (?,NULL,?,?,?,?,?,?,'zhdyot','',1,?,?)",
                           (key, now.date().isoformat(), now.strftime("%H:%M"),
                            (now + timedelta(seconds=PROBA_S)).strftime("%H:%M"), "Проба", k["komnata"], k["imya"],
                            _iso(now), _iso(now))).lastrowid
    _start(int(zid))
    return {"ok": True, "zapis_id": int(zid), "kamera": k}


# --- состояние: /owner и /api/zapis/sostoyanie --------------------------------------------------

STATUSY = {"zhdyot": "ждёт начала", "ssylka": "получаю поток", "pishetsya": "пишется", "zapisano": "записано",
           "vygruzheno": "на Диске", "propusk": "пропущено", "oshibka": "не записано"}
DNI = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def _den(day: str) -> str:
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return day
    return f"{DNI[d.weekday()]} {d.strftime('%d.%m')}"


def blizhaishie(now: datetime | None = None, n: int = 10) -> list[dict]:
    now = (now or _seychas()).replace(tzinfo=None)
    d0 = now.date()
    zan = _zanyatiya_gde("l.date >= ? AND l.date <= ?", (d0.isoformat(), (d0 + timedelta(days=7)).isoformat()))
    with db.get_conn() as conn:
        _init(conn)
        po_klyuchu = {r["key"]: dict(r) for r in conn.execute(
            "SELECT key, status, prichina FROM zapis WHERE day >= ? AND proba=0", (d0.isoformat(),))}
    out = []
    for z in zan:
        if z["end"] + KONEC_POSLE <= now:
            continue
        st = po_klyuchu.get(_klyuch(z))
        if not vklyucheno():
            sost = "запись выключена"
        elif st and st["status"] != "zhdyot":
            sost = STATUSY.get(st["status"], st["status"]) + (f": {st['prichina']}" if st.get("prichina") else "")
        elif z["pisat"]:
            sost = "будет записано" + (f" ({z['pochemu']})" if z["pochemu"] else "")
        else:
            sost = "не пишем: " + z["pochemu"]
        out.append({"day": z["day"], "den": _den(z["day"]), "begin": z["begin"].strftime("%H:%M"),
                    "end": z["end"].strftime("%H:%M"), "gruppa": z["gruppa"], "komnata": z["komnata"],
                    "kamera": (z["kamera"] or {}).get("imya", ""), "pisat": z["pisat"], "sostoyanie": sost})
        if len(out) >= n:
            break
    return out


def poslednie(n: int = 15) -> list[dict]:
    with db.get_conn() as conn:
        _init(conn)
        zz = [dict(r) for r in conn.execute(
            "SELECT * FROM zapis WHERE status != 'zhdyot' OR proba=1 ORDER BY day DESC, begin_time DESC, id DESC LIMIT ?",
            (n,))]
        ff = {}
        for f in conn.execute("SELECT * FROM zapis_fayly WHERE zapis_id IN (%s) ORDER BY chast, id"
                              % ",".join("?" * len(zz)), tuple(z["id"] for z in zz)) if zz else []:
            ff.setdefault(f["zapis_id"], []).append(dict(f))
    parol = parol_zadan()
    out = []
    for z in zz:
        chasti = [f for f in ff.get(z["id"], []) if f["status"] in ("gotov", "vygruzhen", "pishetsya")]
        razmer = sum(int(f["razmer"] or 0) for f in chasti)
        na_diske = [f for f in chasti if f["status"] == "vygruzhen"]
        zhdut = [f for f in chasti if f["status"] == "gotov"]
        if z["status"] in AKTIVNYE:
            sost, vid = STATUSY.get(z["status"]) + (f" — {z['prichina']}" if z.get("prichina") else ""), "idet"
        elif chasti and len(na_diske) == len(chasti):
            sost, vid = "выгружено на Диск" + (f" ({len(chasti)} части)" if len(chasti) > 1 else ""), "ok"
        elif zhdut and not parol:
            sost, vid = "ждёт на сервере: введите пароль приложения Яндекса", "zhdet"
        elif zhdut:
            osh = next((f["oshibka"] for f in zhdut if f.get("oshibka")), "")
            posl = max((f["posl_popytka"] or "" for f in zhdut), default="")
            sled = ""
            if posl:
                try:
                    sled = (datetime.fromisoformat(posl) + POVTOR_VYGRUZKI).strftime("%H:%M")
                except ValueError:
                    sled = ""
            sost = ("ошибка выгрузки: " + osh + (f" · повтор около {sled}" if sled else "")) if osh and posl \
                else "выгружается на Диск"
            vid = "osh" if osh and posl else "idet"
        elif z["status"] in ("propusk", "oshibka"):
            sost, vid = STATUSY[z["status"]] + (f": {z['prichina']}" if z.get("prichina") else ""), "osh"
        else:
            sost, vid = STATUSY.get(z["status"], z["status"]) + (f": {z['prichina']}" if z.get("prichina") else ""), "idet"
        out.append({"id": z["id"], "day": z["day"], "den": _den(z["day"]), "begin": (z["begin_time"] or "")[:5],
                    "gruppa": "Проба" if z["proba"] else z["gruppa"], "komnata": z["komnata"], "proba": bool(z["proba"]),
                    "razmer": razmer, "razmer_tekst": _mb(razmer) if razmer else "—", "chastey": len(chasti),
                    "na_diske": len(na_diske), "sostoyanie": sost, "vid": vid,
                    "fayly": [f["imya"] for f in chasti]})
    return out


def sboi(dney: int = 3) -> list[dict]:
    d0 = (_seychas().date() - timedelta(days=dney - 1)).isoformat()
    with db.get_conn() as conn:
        _init(conn)
        return [{"day": r["day"], "tip": r["tip"], "chto": SBOI.get(r["tip"], r["tip"]), "pervyy": r["tekst"],
                 "raz": r["n"], "ts": r["ts"]}
                for r in conn.execute("SELECT * FROM zapis_sboi WHERE day >= ? ORDER BY day DESC, ts DESC", (d0,))]


def sostoyanie() -> dict:
    """Всё о записи без секретов: пароля Диска и токенов потока здесь нет."""
    m = mesto()
    b, n = _zhdut()
    return {"vklyucheno": vklyucheno(), "filtr": filtry(), "kamery": list(kamery().values()), "papka": papka(),
            "login": login_diska(), "parol_zadan": parol_zadan(), "ffmpeg": bool(_ffmpeg_exe()),
            "mesto": {"vsego_gb": round(m["vsego"] / GB, 1), "svobodno_gb": round(m["svobodno"] / GB, 1),
                      "na_servere_gb": round(m["lokalno"] / GB, 2), "zhdut_vygruzki": n,
                      "zhdut_vygruzki_gb": round(b / GB, 2), "zhdut_vygruzki_bayt": b, "net_mesta": _net_mesta(m)},
            "blizhaishie": blizhaishie(), "poslednie": poslednie(), "sboi": sboi()}


# --- блок «Запись занятий» на /owner ------------------------------------------------------------

def _e(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _metka_zanyat(klyuch: tuple) -> bool:
    t = time.time()
    with _lock:
        if klyuch in _METKI and t - _METKI[klyuch] < SROK_METKI:
            return False
        _METKI[klyuch] = t
        if len(_METKI) > 2000:
            for k_, v_ in list(_METKI.items()):
                if t - v_ >= SROK_METKI:
                    del _METKI[k_]
    return True


def _metka_osvobodit(klyuch: tuple) -> None:
    with _lock:
        _METKI.pop(klyuch, None)


def _iz_formy(f) -> tuple[dict, list[str], list[str]]:
    """Поля формы → (что записать в настройки, что поменялось — названия, отказы)."""
    def pole(n):
        return str(f.get(n) or "")
    zapisat: dict = {}
    chto: list[str] = []
    otkazy: list[str] = []
    vkl = "1" if pole("vkl") == "1" else "0"
    if pole("polya") == "1" and vkl != ("1" if vklyucheno() else "0"):
        zapisat["zapis_on"] = vkl
        chto.append("запись включена" if vkl == "1" else "запись выключена")
    if pole("polya") == "1":
        flt = pole("filtr").strip()
        if not flt:
            otkazy.append("Группы: поле не может быть пустым — иначе под запись попали бы все занятия кабинета.")
        elif len(flt) > 200:
            otkazy.append("Группы: слишком длинно.")
        elif "; ".join(filtry()) != "; ".join(x.strip() for x in re.split(r"[;\n]", flt) if x.strip()):
            zapisat["zapis_filtr"] = "; ".join(x.strip() for x in re.split(r"[;\n]", flt) if x.strip())
            chto.append("группы")
        kam_s = pole("kamery").strip()
        k, osh = razobrat_kamery(kam_s)
        if osh or not k:
            otkazy.append("Камера: " + ("; ".join(osh) if osh else "нужна хотя бы одна строка «К1 = Комната 1»") + ".")
        elif k != kamery():
            zapisat["zapis_kamery"] = "; ".join(f"{v['komnata']} = {v['imya']}" + (f" = {v['cam_id']}" if v["cam_id"] else "")
                                                for v in k.values())
            chto.append("камера")
        pp = pole("papka").strip()
        pn = _papka_norm(pp)
        if not pn:
            otkazy.append("Папка на Диске: путь вроде «/Архив методики/Музыка и речь», без «..» и знаков : * ? \" < > | # %.")
        elif pn != papka():
            zapisat["zapis_papka"] = pn
            chto.append("папка на Диске")
        lg = pole("yadisk_login").strip()
        if not lg or len(lg) > 100 or re.search(r"[\s:<>\"']", lg):
            otkazy.append("Логин Яндекса: адрес почты вроде hello@kidsup.ru (без пробелов и двоеточия).")
        elif lg != login_diska():
            zapisat["yadisk_login"] = lg
            chto.append("логин Яндекса")
    pw = "".join(pole("yadisk_password").split())
    if pw:
        if len(pw) < 8 or len(pw) > 64:
            otkazy.append("Пароль приложения: в нём 16 латинских букв — скопируйте его целиком со страницы Яндекс ID.")
        elif pw != _parol_diska():
            zapisat["yadisk_password"] = pw
            chto.append("пароль приложения Яндекса")
    return zapisat, chto, otkazy


def deystvie_vladelca(request, f) -> dict:
    """POST /owner/zapis. Обработчик уже проверил: человек-владелец, вошёл через форму. Каждое
    действие подтверждается паролем владельца (vhod.podtverdit), как в блоках «Входы» и «Ключи
    педагогов». Сначала сохраняются поля формы (что видно на экране — то и записано), затем
    действие кнопки. В журнал идут только названия: ни пароля приложения, ни ссылок."""
    from . import vhod

    def pole(n):
        return str(f.get(n) or "")
    d = pole("deystvie").strip()[:20]
    request.state.chto = "запись занятий: " + (d or "?") + " — отказ"
    prichina = vhod.podtverdit(request, pole("moy_parol"))
    if prichina:
        return {"osh": prichina}
    metka = pole("metka").strip()[:64]
    klyuch = (metka, d)
    if metka and not _metka_zanyat(klyuch):
        request.state.chto = "запись занятий: повтор формы — не выполнено"
        return {"msg": "Уже сделано: эта форма уже отправлялась (страницу обновили) — повторять не стали."}
    zapisat, chto, otkazy = _iz_formy(f)
    if otkazy:
        if metka:
            _metka_osvobodit(klyuch)
        return {"osh": " ".join(otkazy) + " Ничего не изменено."}
    if d not in ("sohranit", "proba", "vygruzit"):
        if metka:
            _metka_osvobodit(klyuch)
        return {"osh": "Неизвестное действие — ничего не изменено."}
    for k_, v_ in zapisat.items():
        db.set_setting(k_, v_)
    msg = []
    if chto:
        msg.append("Сохранено: " + ", ".join(chto) + ".")
    novyy_parol = "yadisk_password" in zapisat or "yadisk_login" in zapisat
    if novyy_parol:
        pr = proverit_disk()
        if pr["ok"]:
            sv = pr.get("svobodno")
            msg.append("Яндекс.Диск принял вход" + (f", свободно на Диске {_gb(sv)} ГБ." if sv is not None else "."))
        else:
            msg.append("Яндекс.Диск вход не принял: " + pr["error"] + ".")
        with db.get_conn() as conn:          # новый пароль — повторить выгрузку сразу, не ждать часа
            _init(conn)
            conn.execute("UPDATE zapis_fayly SET posl_popytka=NULL WHERE status='gotov'")
    b, n = _zhdut()
    if d == "proba":
        r = proba()
        k = r["kamera"]
        msg.append(f"Пробная запись началась: 2 минуты с камеры {k['komnata']} («{k['imya']}»). Через 4–5 минут "
                   "обновите страницу — в «Последних записях» появится строка «Проба»"
                   + (", а файл — на Диске в папке " + papka() + "." if parol_zadan()
                      else ". Пароль приложения не введён — файл подождёт на сервере."))
        chto.append("пробная запись")
    elif (d == "vygruzit" or novyy_parol) and n and parol_zadan():
        _potok(vygruzit_vse, True)
        msg.append(f"Выгрузка на Диск началась: {n} {_mnogo(n, 'файл', 'файла', 'файлов')}, {_mb(b)}. "
                   "Обновите страницу через несколько минут.")
        chto.append("выгрузка")
    elif d == "vygruzit":
        msg.append("Выгружать нечего." if not n else "Сначала введите пароль приложения Яндекса.")
    if not msg:
        msg.append("Ничего не изменено.")
    request.state.chto = "запись занятий: " + (", ".join(chto) if chto else "без изменений")
    return {"msg": " ".join(msg)}


SKRIPT = """
(function(){
  var f=document.getElementById('zapis-forma'); if(!f)return;
  f.addEventListener('keydown',function(e){if(e.key==='Enter'&&e.target.tagName==='INPUT')e.preventDefault();});
  var sp=f.querySelectorAll('button[data-sprosit]'),i;
  for(i=0;i<sp.length;i++)sp[i].addEventListener('click',function(e){if(!confirm(this.getAttribute('data-sprosit')))e.preventDefault()});
  f.addEventListener('submit',function(e){var m=f.elements['moy_parol'];
    if(m&&!m.value){e.preventDefault();m.focus();m.scrollIntoView({block:'center'});
      var t=document.getElementById('zapis-nado');if(t)t.hidden=false}});
  var it=document.getElementById('zapis-itog'); if(it)it.scrollIntoView({block:'center'});
})();
"""

_STIL = ("<style>.vh .zp-pole{margin:10px 0}.vh .zp-pole b{display:block}.vh .zp-pole small{display:block;color:#6c6a86;"
         "font-size:13.5px;margin:1px 0 4px}.vh .zp-sp{margin:6px 0}"
         ".vh .zp-r{border-top:1px solid #eceaf5;padding:6px 0;font-size:14.5px}.vh .zp-r b,.vh .zp-g{white-space:nowrap}"
         ".vh .zp-ok{color:#3f7d12}.vh .zp-osh{color:#B3261E}.vh .zp-zhdet{color:#7a4b00}.vh .zp-idet{color:#312783}"
         ".vh .zp-sv{display:flex;gap:6px 18px;flex-wrap:wrap;margin:6px 0}"
         ".vh details{margin:10px 0}.vh summary{cursor:pointer;color:#312783;font-weight:600}</style>")


def blok_vladelca(request, itog: dict | None = None) -> str:
    """HTML блока «Запись занятий» для /owner. Управляет только владелец, вошедший через форму
    (как блоки «Входы» и «Ключи педагогов»); остальным — ссылка «войти через форму».
    Пароль приложения Яндекса в страницу не попадает: только «введён / не введён»."""
    from . import vhod
    k = vhod.iz(request)
    if not (k.get("rol") == "owner" and k.get("sposob") == "forma"):
        return ("<section class='vh' id='zapis'><h2>Запись занятий</h2><p><a href='/vhod?next=/owner'><b>Управлять "
                "записью занятий → войти через форму</b></a></p><p class='tih'>Включение записи, пароль приложения "
                "Яндекса и пробная запись открываются только после входа под своим именем.</p></section>")
    itog = itog or {}
    s = sostoyanie()
    m = s["mesto"]
    h = [_STIL, "<section class='vh' id='zapis'><h2>Запись занятий</h2>",
         "<p class='tih'>Видео занятий «Музыка и речь» — архив методики, только для внутреннего пользования. Пишет сервер "
         "центра с камеры кабинета (в Ivideon ничего не меняется), без перекодирования; после занятия файл уходит на "
         "Яндекс.Диск и с сервера удаляется, когда размер на Диске совпал. Публичных ссылок сервер не создаёт. "
         "В имени файла — дата, время и группа, имён детей нет.</p>"]
    if itog.get("osh"):
        h.append(f"<div class='osh' role='alert' id='zapis-itog'>{_e(itog['osh'])}</div>")
    if itog.get("msg"):
        h.append(f"<div class='ok' id='zapis-itog'>{_e(itog['msg'])}</div>")
    if not s["parol_zadan"]:
        h.append("<div class='pr' id='zapis-net-parolya'><b>Введите пароль приложения Яндекса</b> (ниже, как его получить). "
                 "Пока его нет, запись идёт, а файлы ждут на сервере"
                 + (f" — сейчас {m['zhdut_vygruzki']} {_mnogo(m['zhdut_vygruzki'], 'файл', 'файла', 'файлов')}, "
                    f"{_mb(m['zhdut_vygruzki_bayt'])}" if m["zhdut_vygruzki"] else "")
                 + f". Когда на сервере наберётся {MAX_LOKALNO // GB} ГБ, запись остановится.</div>")
    if m["net_mesta"]:
        h.append(f"<div class='osh' role='alert'><b>Место на сервере:</b> {_e(m['net_mesta'])}.</div>")
    if not s["ffmpeg"]:
        h.append("<div class='osh'>На сервере не найден ffmpeg — записывать нечем.</div>")
    sv = [f"Запись: <b>{'включена' if s['vklyucheno'] else 'выключена'}</b>",
          f"Свободно на сервере: <b>{str(m['svobodno_gb']).replace('.', ',')} ГБ</b> из "
          f"{str(m['vsego_gb']).replace('.', ',')} ГБ",
          f"Ждут выгрузки: <b>{m['zhdut_vygruzki']}</b>"
          + (f" ({_mb(m['zhdut_vygruzki_bayt'])})" if m["zhdut_vygruzki"] else ""),
          f"Пароль приложения: <b>{'введён' if s['parol_zadan'] else 'не введён'}</b>"]
    h.append("<div class='zp-sv' id='zapis-svodka'>" + "".join(f"<span>{x}</span>" for x in sv) + "</div>")

    if s["sboi"]:
        h.append("<h3>Сбои за 3 дня</h3><ul id='zapis-sboi'>" + "".join(
            f"<li>{_e(_den(x['day']))}: {_e(x['chto'])}" + (f" — {x['raz']} {_mnogo(x['raz'], 'раз', 'раза', 'раз')}" if x["raz"] > 1 else "")
            + f". <span class='tih'>Первый: {_e(x['pervyy'])}</span></li>" for x in s["sboi"]) + "</ul>")

    h.append("<h3>Ближайшие занятия</h3>")
    if s["blizhaishie"]:
        h.append("<div class='zp-sp' id='zapis-blizh'>" + "".join(
            f"<div class='zp-r'><b>{_e(x['den'])} {_e(x['begin'])}–{_e(x['end'])}</b> · <span class='zp-g'>{_e(x['gruppa'])}"
            f"</span> · {_e(x['komnata'] or '—')}<div class='{'zp-ok' if x['pisat'] else 'zp-osh'}'>{_e(x['sostoyanie'])}</div></div>"
            for x in s["blizhaishie"]) + "</div>")
    else:
        h.append("<p class='tih' id='zapis-blizh'>На неделю вперёд занятий под фильтр групп нет (или расписание ещё "
                 "не синхронизировано).</p>")

    h.append("<h3>Последние записи</h3>")
    if s["poslednie"]:
        h.append("<div class='zp-sp' id='zapis-posl'>" + "".join(
            f"<div class='zp-r'><b>{_e(x['den'])} {_e(x['begin'])}</b> · <span class='zp-g'>{_e(x['gruppa'])}</span>"
            f" · {_e(x['komnata'] or '—')} · {_e(x['razmer_tekst'])}<div class='zp-{_e(x['vid'])}'>{_e(x['sostoyanie'])}</div></div>"
            for x in s["poslednie"]) + "</div>")
    else:
        h.append("<p class='tih' id='zapis-posl'>Записей ещё не было.</p>")

    kam_s = "; ".join(f"{v['komnata']} = {v['imya']}" + (f" = {v['cam_id']}" if v["cam_id"] else "") for v in s["kamery"])
    h.append("<form method='post' action='/owner/zapis' id='zapis-forma' autocomplete='off'>"
             f"<input type='hidden' name='metka' value='{_e(secrets.token_urlsafe(18))}'>"
             "<input type='hidden' name='polya' value='1'>"
             "<input type='text' name='username' value='boris' autocomplete='username' hidden>"
             "<div class='moy'><b>Ваш нынешний пароль</b><input type='password' name='moy_parol' "
             "autocomplete='current-password'><p class='tih'>Нужен для любого действия в этом блоке: введите и нажмите "
             "нужную кнопку.</p><p class='osh' id='zapis-nado' hidden>Сначала введите ваш нынешний пароль.</p></div>"
             "<h3>Настройки</h3>"
             f"<p><label><input type='checkbox' name='vkl' value='1'{' checked' if s['vklyucheno'] else ''}> "
             "<b>Записывать занятия</b></label></p>"
             "<div class='zp-pole'><b>Какие группы</b><small>Часть названия группы в МойКлассе; несколько — через «;». "
             "Отменённые и удалённые занятия не пишутся.</small>"
             f"<input type='text' name='filtr' value='{_e('; '.join(s['filtr']))}' maxlength='200'></div>"
             "<div class='zp-pole'><b>Камера</b><small>Кабинет = камера в Ivideon = id камеры, например «К1 = Комната 1 = "
             "100-…». Несколько кабинетов — через «;».</small>"
             f"<input type='text' name='kamery' value='{_e(kam_s)}' maxlength='300'></div>"
             "<div class='zp-pole'><b>Папка на Яндекс.Диске</b><small>Внутри — папки по месяцам: «…/2026-11».</small>"
             f"<input type='text' name='papka' value='{_e(s['papka'])}' maxlength='200'></div>"
             "<div class='zp-pole'><b>Логин Яндекса</b><small>Аккаунт, на чей Диск уходят записи.</small>"
             f"<input type='text' name='yadisk_login' value='{_e(s['login'])}' maxlength='100' autocomplete='off'></div>"
             "<div class='zp-pole'><b>Пароль приложения Яндекса</b><small>"
             + ("Введён. Пустое поле — оставить как есть; новый — вставьте, чтобы заменить."
                if s["parol_zadan"] else "Не введён: вставьте пароль приложения «Файлы (WebDAV)».")
             + "</small><input type='password' name='yadisk_password' autocomplete='new-password' "
             "placeholder='пароль приложения (16 букв)'></div>"
             "<div class='vb'><button type='submit' class='gl' name='deystvie' value='sohranit'>Сохранить</button></div>"
             "<h3>Проверка</h3>"
             "<div class='vb'><button type='submit' name='deystvie' value='proba' data-sprosit='Записать 2 минуты с камеры "
             "кабинета сейчас и выгрузить на Диск?'>Пробная запись 2 минуты сейчас</button>"
             + ("<button type='submit' name='deystvie' value='vygruzit'>Выгрузить на Диск сейчас</button>"
                if m["zhdut_vygruzki"] else "")
             + "</div><p class='tih'>Пробная запись пишет 2 минуты с первой камеры списка и выгружает файл «… Проба К1.mp4» "
             "в ту же папку месяца — так видно, что камера, сервер и Диск работают, до первого занятия.</p>"
             "<details id='zapis-kak'><summary>Как получить пароль приложения Яндекса</summary><ol>"
             f"<li>Откройте <a href='https://id.yandex.ru/security' target='_blank' rel='noopener'>id.yandex.ru</a> под "
             f"<b>{_e(s['login'])}</b>.</li><li>«Безопасность» → «Пароли приложений».</li>"
             "<li>Выберите «Файлы (WebDAV)», назовите, например, «Сервер KidsUP» и нажмите «Создать».</li>"
             "<li>Скопируйте пароль (16 букв) и вставьте в поле выше, введите свой пароль и нажмите «Сохранить» — "
             "сервер сразу проверит вход на Диск.</li></ol>"
             "<p class='tih'>Обычный пароль от почты не подойдёт. Отозвать доступ сервера — там же, удалить этот пароль "
             "приложения. Одно занятие — примерно 0,4–1 ГБ, неделя — до 10–14 ГБ: проверьте, что места на Диске "
             "хватает.</p></details>"
             "</form></section>")
    return "".join(h)


def blok_vladelca_bezopasno(request, itog: dict | None = None) -> str:
    """Блок или, при сбое, короткая строка: остальная страница владельца должна открыться."""
    try:
        return blok_vladelca(request, itog)
    except Exception:  # noqa: BLE001
        log.exception("блок «Запись занятий» не собран")
        return ("<section class='vh' id='zapis'><h2>Запись занятий</h2><p class='osh'>Блок «Запись занятий» не "
                "открылся — ошибка записана в журнал сервера. Остальные настройки работают.</p></section>")
