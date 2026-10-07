# -*- coding: utf-8 -*-
"""Кадры с камер по расписанию — «фото-явка» (пилот, решение Бориса 06.10.2026).

Через 10 минут после начала каждого занятия в комнатах с камерами сервер
делает кадр публичного плеера Ivideon (тот же, что видят родители на
/kamery) и кладёт PNG в data/kadry/<дата>/. Клод в ежечасном разборе
считает по кадру детей и взрослых и сверяет с отметками явки в МойКлассе:
«проведено» при пустой комнате, детей больше, чем отмечено, занятие не
началось. Лица не распознаём и не храним ничего, кроме кадра и подсчёта.

Камера ↔ комната: настройка cam_embeds (имена «Комната N») ↔ справочник
rooms МойКласса («КN»). Для мини-сада и нулевого класса (4 часа) — ещё один
кадр через 2 часа.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

from . import db

log = logging.getLogger("kidsup.kadry")

DIR = Path(__file__).resolve().parent.parent / "data" / "kadry"
AFTER_MIN = 10            # первый кадр — через 10 минут после начала
LONG_AFTER_MIN = 120      # второй кадр для занятий длиннее 3 часов
_lock = threading.Lock()
_busy: set[str] = set()


def _init(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS kadry (
        id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, lesson_id INTEGER, day TEXT,
        ts TEXT, room TEXT, class_name TEXT, begin_time TEXT, end_time TEXT,
        zapisano INTEGER, otmecheno INTEGER, status INTEGER, file TEXT, error TEXT,
        deti INTEGER, vzroslye INTEGER, ocenka TEXT, ocenka_ts TEXT)""")


def cam_rooms() -> dict[str, str]:
    """{'К1': 'https://open.ivideon.com/embed/v3/…/', …} по именам камер «Комната N»."""
    out: dict[str, str] = {}
    try:
        for x in json.loads(db.get_setting("cam_embeds") or "[]"):
            m = re.search(r"(\d+)", str(x.get("name") or ""))
            if m and str(x.get("src") or "").startswith("https://"):
                out["К" + m.group(1)] = x["src"]
    except Exception:  # noqa: BLE001
        pass
    return out


def _room_names(conn) -> dict[int, str]:
    try:
        return {int(r[0]): (r[1] or "") for r in conn.execute("SELECT id, name FROM rooms").fetchall()}
    except Exception:  # noqa: BLE001
        return {}


def snap(src: str, path: Path, wait_ms: int = 9000) -> dict:
    """Кадр публичного плеера Ivideon. Плеер стартует сам, звук выключен."""
    from playwright.sync_api import sync_playwright
    from . import mkweb
    path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = mkweb._launch(p)
        ctx = b.new_context(viewport={"width": 1280, "height": 720}, locale="ru-RU")
        pg = ctx.new_page()
        url = src + ("&" if "?" in src else "?") + "autoplay=1&mute=1"
        pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(2500)
        # если плеер ждёт клика — кликаем в центр
        try:
            pg.mouse.click(640, 360)
        except Exception:  # noqa: BLE001
            pass
        pg.wait_for_timeout(wait_ms)
        pg.screenshot(path=str(path), type="png")
        txt = ""
        try:
            txt = pg.inner_text("body")[:200]
        except Exception:  # noqa: BLE001
            pass
        b.close()
    return {"ok": path.exists() and path.stat().st_size > 20000, "bytes": path.stat().st_size if path.exists() else 0,
            "text": txt}


def _do_snap(key: str, meta: dict, src: str) -> None:
    try:
        day = meta["day"]
        fname = f"{meta['ts'][11:16].replace(':', '')}_{meta['room']}_{meta['lesson_id']}{meta.get('suffix', '')}.png"
        path = DIR / day / fname
        r = snap(src, path)
        err = "" if r["ok"] else f"кадр пустой ({r['bytes']} байт) {r['text'][:80]}"
        with db.get_conn() as conn:
            _init(conn)
            conn.execute("UPDATE kadry SET file=?, error=? WHERE key=?", (f"{day}/{fname}", err, key))
    except Exception as e:  # noqa: BLE001
        log.exception("кадр %s не снялся", key)
        with db.get_conn() as conn:
            _init(conn)
            conn.execute("UPDATE kadry SET error=? WHERE key=?", (f"{type(e).__name__}: {str(e)[:150]}", key))
    finally:
        with _lock:
            _busy.discard(key)


def tick(now: datetime | None = None) -> list[str]:
    """Раз в минуту: какие занятия начались AFTER_MIN минут назад в комнатах с камерами."""
    from .autopilot import _now
    now = now or _now()
    now = now.replace(tzinfo=None)      # сравниваем с наивными begin/end — иначе TypeError каждую минуту
    cams = cam_rooms()
    if not cams:
        return []
    day = now.strftime("%Y-%m-%d")
    started = []
    with db.get_conn() as conn:
        _init(conn)
        rooms = _room_names(conn)
        rows = conn.execute(
            "SELECT l.id, l.begin_time, l.end_time, l.status, l.raw, c.name FROM lessons l "
            "JOIN classes c ON c.id = l.class_id WHERE l.date = ? AND c.name LIKE '2627_%' "
            "AND c.name NOT LIKE '%Заявк%'", (day,)).fetchall()
        for lid, bt, et, st, raw, cname in rows:
            try:
                j = json.loads(raw or "{}")
            except ValueError:
                j = {}
            if st == 2:                              # отменено
                continue
            room = rooms.get(j.get("roomId") or 0, "")
            src = cams.get(room)
            if not src or not bt:
                continue
            try:
                begin = datetime.strptime(f"{day} {bt[:5]}", "%Y-%m-%d %H:%M")
                end = datetime.strptime(f"{day} {(et or bt)[:5]}", "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            moments = [(begin + timedelta(minutes=AFTER_MIN), "")]
            if (end - begin) >= timedelta(hours=3):
                moments.append((begin + timedelta(minutes=LONG_AFTER_MIN), "_2"))
            for when, suffix in moments:
                # окно 6 минут: минутный цикл автопилота утром бывает занят дольше
                # двух минут (07.10 пропущен кадр мини-сада 09:10)
                if not (when <= now < when + timedelta(minutes=6)):
                    continue
                key = f"{lid}{suffix}"
                zap, otm = conn.execute("SELECT COUNT(*), COALESCE(SUM(visit),0) FROM lesson_records WHERE lesson_id=?",
                                        (lid,)).fetchone()
                cur = conn.execute("INSERT OR IGNORE INTO kadry (key, lesson_id, day, ts, room, class_name, begin_time, "
                                   "end_time, zapisano, otmecheno, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                   (key, lid, day, now.isoformat(timespec="minutes"), room, cname[5:], bt[:5],
                                    (et or "")[:5], int(zap or 0), int(otm or 0), st))
                if cur.rowcount:
                    with _lock:
                        if key in _busy:
                            continue
                        _busy.add(key)
                    meta = {"day": day, "ts": now.isoformat(timespec="minutes"), "room": room, "lesson_id": lid, "suffix": suffix}
                    threading.Thread(target=_do_snap, args=(key, meta, src), daemon=True).start()
                    started.append(key)
    return started


def snap_room(room: str) -> dict:
    """Ручной кадр комнаты «К1»…«К4» — для проверки привязки камер."""
    from .autopilot import _now
    src = cam_rooms().get(room)
    if not src:
        return {"ok": False, "error": f"нет камеры для {room}", "камеры": sorted(cam_rooms())}
    now = _now()
    path = DIR / now.strftime("%Y-%m-%d") / f"{now.strftime('%H%M')}_{room}_manual.png"
    r = snap(src, path)
    return {**r, "file": f"{now.strftime('%Y-%m-%d')}/{path.name}"}


def spisok(day: str = "") -> list[dict]:
    from .autopilot import _today
    day = day or _today().isoformat()
    with db.get_conn() as conn:
        _init(conn)
        rows = conn.execute("SELECT * FROM kadry WHERE day=? ORDER BY begin_time, room", (day,)).fetchall()
    return [dict(r) for r in rows]


def ocenka(key: str, deti: int | None, vzroslye: int | None, note: str) -> dict:
    from .autopilot import _now
    with db.get_conn() as conn:
        _init(conn)
        cur = conn.execute("UPDATE kadry SET deti=?, vzroslye=?, ocenka=?, ocenka_ts=? WHERE key=?",
                           (deti, vzroslye, note[:300], _now().isoformat(timespec="minutes"), key))
    return {"ok": bool(cur.rowcount)}
