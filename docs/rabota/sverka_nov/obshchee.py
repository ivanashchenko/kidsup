# -*- coding: utf-8 -*-
"""Общее для sverka.py и page.py: папка данных, запросы к app.kidsup.ru, время МСК.

Данные (выгрузки, снимок, страница) живут НЕ в репозитории, а в рабочей папке —
её задаёт переменная SVERKA_DIR (обёртки в рабочей папке ставят её сами).
Пароли в код не попадают: пароль владельца читается из файла (SVERKA_OWNER_PW,
по умолчанию <папка>/../owner/.pw) и уходит в curl через stdin (-K -), а не
аргументом командной строки; логин админа — функция call() модуля api.py из
SVERKA_ADMIN_API (по умолчанию <папка>/../r0910).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

MSK = timezone(timedelta(hours=3))
BASE = "https://app.kidsup.ru"
OWNER_LOGIN = "boris"


def sverka_dir() -> Path:
    d = os.environ.get("SVERKA_DIR")
    p = Path(d) if d else Path.cwd() / "sverka_nov"
    return p.resolve()


def p_in() -> Path:
    p = sverka_dir() / "in"
    p.mkdir(parents=True, exist_ok=True)
    return p


def p_snap() -> Path:
    p = sverka_dir() / "snap"
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — нет файла или битый
        return default


def save_json(path: Path, data, indent=None) -> None:
    path = Path(path)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
    tmp.replace(path)


# ── время ────────────────────────────────────────────────────────────────────
def to_msk(s: str) -> datetime:
    """ISO-время (с Z/смещением; без зоны — считаем UTC, как createdAt МойКласса) → МСК."""
    s = (s or "").strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(MSK)


def now_msk() -> datetime:
    return datetime.now(MSK)


def days(d1: str, d2: str) -> list[str]:
    a, b = date.fromisoformat(d1), date.fromisoformat(d2)
    return [(a + timedelta(i)).isoformat() for i in range((b - a).days + 1)]


def ddmm(d: str) -> str:
    return f"{d[8:10]}.{d[5:7]}"


# ── сеть ─────────────────────────────────────────────────────────────────────
def _owner_cfg() -> str:
    f = Path(os.environ.get("SVERKA_OWNER_PW") or (sverka_dir().parent / "owner" / ".pw"))
    pw = f.read_text(encoding="utf-8").strip()
    if not pw:
        raise RuntimeError(f"пустой файл пароля владельца: {f}")
    esc = pw.replace("\\", "\\\\").replace('"', '\\"')
    return f'user = "{OWNER_LOGIN}:{esc}"\n'


def owner(method: str, path: str, params: dict | None = None, payload=None,
          timeout: int = 300, tries: int = 3, raw: bool = False):
    """Запрос под логином владельца. Пароль — только через stdin curl (-K -)."""
    url = BASE + path + (("&" if "?" in path else "?") + urllib.parse.urlencode(params) if params else "")
    last = ""
    for i in range(tries):
        args = ["curl", "-sS", "-m", str(timeout), "-K", "-", "-X", method, url, "-w", "\n%{http_code}"]
        tmp = None
        if payload is not None:
            fd, tmp = tempfile.mkstemp(prefix=".post_", suffix=".json", dir=str(sverka_dir()))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            args += ["-H", "Content-Type: application/json", "--data-binary", "@" + tmp]
        try:
            p = subprocess.run(args, input=_owner_cfg(), capture_output=True, text=True)
        finally:
            if tmp:
                os.unlink(tmp)
        body, _, code = p.stdout.rpartition("\n")
        code = code.strip()
        if code == "200":
            if raw:
                return body
            try:
                return json.loads(body)
            except ValueError:
                return body
        last = f"HTTP {code} {body[:300]} {p.stderr.strip()[:200]}"
        if code in ("400", "401", "403", "404", "422"):
            break
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"{method} {path}: {last}")


def admin(path: str):
    """GET под логином админа — через call() из api.py рабочей папки."""
    d = Path(os.environ.get("SVERKA_ADMIN_API") or (sverka_dir().parent / "r0910"))
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))
    import api  # noqa: E402 — модуль рабочей папки, в репозитории его нет
    return api.call("GET", path)
