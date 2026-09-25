"""Видео детей с английского: хранение, сжатие, ссылка для отправки родителю.

Решение владельца 25.09: видео каждого ребёнка раз в месяц уходит родителю
лично, и отправляет его сервер центра, а не педагог со своего телефона. У
педагогов нет доступа к WhatsApp центра, а личный номер педагога для этого
не годится.

Как устроено:
• педагог загружает ролик со страницы карточки речи — телом запроса, без
  multipart (библиотеки для форм на сервере может не быть);
• файл лежит в data/deti_video (вне app/: деплой распаковывает app/ поверх,
  и держать там живые файлы нельзя), имя — случайный токен;
• WhatsApp не принимает видео больше 16 МБ, телефон снимает 60–100 МБ на
  минуту. Если ролик больше порога или не mp4 — пережимаем в 720p H.264
  через ffmpeg из пакета imageio-ffmpeg (ставится один раз, setup());
• отдаём по /v/<токен>.mp4 без пароля: Wazzup забирает файл по ссылке.
  Токен из 24 случайных символов — ссылку нельзя подобрать, но знающий её
  видит ролик, поэтому в чат группы и на сайт ссылки не попадают.

Согласие семей на съёмку и хранение видео до апреля (для «было / стало») —
подтвердил владелец 25.09.
"""
from __future__ import annotations

import logging
import secrets
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path

from . import config, db

log = logging.getLogger("kidsup.deti_video")

DIR = config.DATA_DIR / "deti_video"
SETUP_LOG = config.DATA_DIR / "ffmpeg_setup.log"
PUBLIC = "https://app.kidsup.ru"
MAX_UPLOAD = 400 * 1024 * 1024      # сырой файл с телефона
SEND_LIMIT = 15 * 1024 * 1024       # WhatsApp — 16 МБ, берём с запасом
_setup_running = False


def _ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS deti_video (
        token TEXT PRIMARY KEY, user_id INTEGER, date TEXT, file TEXT,
        size INTEGER, note TEXT, ts TEXT, author TEXT)""")


def ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        return shutil.which("ffmpeg")


def setup() -> dict:
    """Ставит imageio-ffmpeg (статический ffmpeg внутри колеса pip). В фоне."""
    global _setup_running
    if ffmpeg():
        return {"ok": True, "ffmpeg": ffmpeg()}
    if _setup_running:
        return {"ok": True, "already_running": True}

    def worker():
        global _setup_running
        _setup_running = True
        try:
            with SETUP_LOG.open("w", encoding="utf-8") as lg:
                lg.write(time.strftime("%Y-%m-%d %H:%M:%S") + " pip install imageio-ffmpeg\n")
                lg.flush()
                p = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "imageio-ffmpeg"],
                                   stdout=lg, stderr=subprocess.STDOUT, text=True, timeout=900)
                lg.write(f"[exit {p.returncode}] ffmpeg={ffmpeg()}\n")
        finally:
            _setup_running = False

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "started": True}


def status() -> dict:
    tail = ""
    try:
        tail = SETUP_LOG.read_text(encoding="utf-8")[-600:]
    except Exception:
        pass
    return {"ffmpeg": ffmpeg(), "running": _setup_running, "log": tail}


def _szhat(src: Path, dst: Path) -> bool:
    """720p H.264 + AAC, битрейт под 14 МБ на длительность ролика (до 2 минут)."""
    exe = ffmpeg()
    if not exe:
        return False
    cmd = [exe, "-y", "-i", str(src), "-t", "120",
           "-vf", "scale='min(1280,iw)':'-2'", "-c:v", "libx264", "-preset", "veryfast",
           "-b:v", "1400k", "-maxrate", "1600k", "-bufsize", "3000k",
           "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(dst)]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=600)
    except Exception as e:  # noqa: BLE001
        log.warning("ffmpeg упал: %s", e)
        return False
    return p.returncode == 0 and dst.exists() and dst.stat().st_size > 10_000


def save(user_id: int, raw: bytes, ext: str = "mp4", day: str = "", author: str = "") -> dict:
    if int(user_id) <= 0:
        raise ValueError("нужен ребёнок")
    if len(raw) < 20_000:
        raise ValueError("файл пустой или это не видео")
    if len(raw) > MAX_UPLOAD:
        raise ValueError("файл больше 400 МБ — снимите короче (30–60 секунд)")
    ext = (ext or "mp4").lower().strip(".")[:5]
    day = (day or date.today().isoformat())[:10]
    date.fromisoformat(day)
    DIR.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(18).replace("-", "a").replace("_", "b")
    src = DIR / f"{token}.src.{ext}"
    src.write_bytes(raw)
    out = DIR / f"{token}.mp4"
    if ext == "mp4" and len(raw) <= SEND_LIMIT:
        src.rename(out)
    else:
        if not _szhat(src, out):
            src.unlink(missing_ok=True)
            if not ffmpeg():
                setup()
                raise ValueError("видео большое, а сжатие на сервере ещё ставится — попробуйте "
                                 "через 5 минут или снимите в 720p короче минуты")
            raise ValueError("не получилось сжать видео — снимите в 720p, 30–60 секунд")
        src.unlink(missing_ok=True)
        if out.stat().st_size > SEND_LIMIT:
            out.unlink(missing_ok=True)
            raise ValueError("даже после сжатия больше 15 МБ — нужен ролик до минуты")
    with db.get_conn() as conn:
        _ensure(conn)
        conn.execute("INSERT INTO deti_video (token, user_id, date, file, size, ts, author) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (token, int(user_id), day, out.name, out.stat().st_size,
                      time.strftime("%Y-%m-%d %H:%M"), author[:40]))
    return {"ok": True, "token": token, "date": day, "url": url(token),
            "size_mb": round(out.stat().st_size / 1048576, 1)}


def url(token: str) -> str:
    return f"{PUBLIC}/v/{token}.mp4"


def path(token: str) -> Path | None:
    token = "".join(c for c in token if c.isalnum())
    p = DIR / f"{token}.mp4"
    return p if token and p.exists() else None


def spisok(user_id: int) -> list[dict]:
    with db.get_conn() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT token, date, size FROM deti_video WHERE user_id=? "
                            "ORDER BY date", (int(user_id),)).fetchall()
    return [{"token": r["token"], "date": r["date"], "url": url(r["token"]),
             "size_mb": round((r["size"] or 0) / 1048576, 1)} for r in rows]


def udalit(token: str) -> dict:
    p = path(token)
    if p:
        p.unlink(missing_ok=True)
    with db.get_conn() as conn:
        _ensure(conn)
        conn.execute("DELETE FROM deti_video WHERE token=?", (token,))
    return {"ok": True}
