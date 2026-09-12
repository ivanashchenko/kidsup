"""Чистка имён карточек: в имени ребёнка остаётся только имя и фамилия.

Правило владельца: имя карточки — имя + фамилия ребёнка, ничего больше.
На практике в имя годами дописывали скидки, промо, каналы и пометки:
«Анищенкова Алена (скидка 10% многодет.)», «Кузнецов Федор (МАКС)».
Из-за этого имя нельзя подставлять в сообщения родителям, а поиск по
фамилии находит не то.

Пометка не выбрасывается: она уходит в комментарий карточки, а сам факт
скидки — ещё и в тег, если такой тег заведён в CRM.

Не трогаем: «ДУБЛЬ на удаление → …» (рабочая пометка владельца), карточки
без человеческого имени («Звонок от 79…», голые цифры) — там чистить нечего,
им нужен прозвон.

Обновление имени идёт только через safe_update_user: голый POST на /users/{id}
стирает телефоны и атрибуты (инцидент 14.08 — 1844 карточки).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from . import sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.names_fix")

DATA = Path(__file__).resolve().parent.parent / "data"
OUT = DATA / "names_fix.json"

# пометки, которые в имени быть не должны
BRACKETS = re.compile(r"\s*[\(\[]([^)\]]*)[\)\]]\s*")
TAIL = re.compile(r"\s*[-—]\s*(скидк|промо|макс|max|соцсет|инст|вк|whatsapp|wa)\w*.*$", re.I)
# имя не трогаем вовсе, если оно такое
KEEP = re.compile(r"ДУБЛЬ|на удаление|^\s*(звонок|заявка|лид)\b", re.I)
NOT_A_NAME = re.compile(r"^[\d\s\+\-\(\)]+$")
HAS_PHONE = re.compile(r"(?:\D*\d){7}")   # в имени спрятан телефон — чистить нечего

_lock = threading.Lock()
_state: dict = {"running": False, "step": "", "error": "", "data": None}


def _clean(name: str) -> tuple[str, list[str]]:
    """Возвращает (чистое имя, вынесенные пометки)."""
    notes = [m.strip(" .,;") for m in BRACKETS.findall(name) if m.strip(" .,;")]
    base = BRACKETS.sub(" ", name)
    m = TAIL.search(base)
    if m:
        notes.append(m.group(0).strip(" -—"))
        base = TAIL.sub("", base)
    base = re.sub(r"\s{2,}", " ", base).strip(" .,;-—")
    return base, notes


def audit(limit: int = 0) -> dict:
    """Кого надо почистить. Ничего не меняет."""
    mk = MoyklassClient(sync.get_api_key())
    try:
        users = taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=2)
    finally:
        mk.close()
    rows, skipped = [], []
    for u in users:
        name = (u.get("name") or "").strip()
        if not name or KEEP.search(name) or NOT_A_NAME.match(name) \
                or HAS_PHONE.search(name):
            continue
        clean, notes = _clean(name)
        if not notes or clean == name:
            continue
        if len(clean.split()) < 2 or len(clean) < 4:
            # после чистки не осталось человеческого имени — руками
            skipped.append({"id": u["id"], "name": name, "clean": clean})
            continue
        rows.append({"id": u["id"], "was": name, "now": clean, "notes": notes})
        if limit and len(rows) >= limit:
            break
    return {"total_users": len(users), "to_fix": len(rows),
            "manual": skipped, "rows": rows}


def _backup(users: list) -> str:
    DATA.mkdir(parents=True, exist_ok=True)
    p = DATA / f"backup_users_{datetime.now():%Y%m%d_%H%M}.json"
    p.write_text(json.dumps(users, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _run(dry: bool, limit: int) -> None:
    try:
        with _lock:
            _state["step"] = "выгрузка карточек"
        mk = MoyklassClient(sync.get_api_key())
        try:
            users = taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=0)
            back = _backup(users)
            with _lock:
                _state["step"] = f"копия: {back}"
            plan = audit(limit)
            done, errors = [], []
            for i, r in enumerate(plan["rows"], 1):
                if i % 25 == 0:
                    with _lock:
                        _state["step"] = f"правим {i} из {len(plan['rows'])}"
                if dry:
                    continue
                try:
                    mk.safe_update_user(r["id"], name=r["now"])
                    time.sleep(0.3)
                    mk.post("/v1/company/userComments",
                            {"userId": r["id"], "showToUser": False,
                             "comment": ("Чистка имени: из имени карточки вынесено «"
                                         + "; ".join(r["notes"]) + "». Было: " + r["was"])})
                    time.sleep(0.3)
                    done.append(r["id"])
                except Exception as e:  # noqa: BLE001
                    errors.append({"id": r["id"], "error": str(e)[:200]})
        finally:
            mk.close()
        data = {"dry_run": dry, "backup": back, "planned": len(plan["rows"]),
                "fixed": len(done), "errors": errors[:20],
                "manual": plan["manual"], "rows": plan["rows"]}
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        with _lock:
            _state.update(running=False, step="готово", error="", data=data)
    except Exception as e:  # noqa: BLE001
        log.exception("names_fix failed")
        with _lock:
            _state.update(running=False, step="ошибка", error=str(e)[:300])


def start(dry: bool = True, limit: int = 0) -> dict:
    with _lock:
        if _state["running"]:
            return {"ok": False, "running": True, "step": _state["step"]}
        _state.update(running=True, step="старт", error="", data=None)
    threading.Thread(target=_run, args=(dry, limit), daemon=True).start()
    return {"ok": True, "running": True, "dry_run": dry}


def status() -> dict:
    with _lock:
        st = {"running": _state["running"], "step": _state["step"], "error": _state["error"]}
    if not st["running"] and OUT.exists():
        st["data"] = json.loads(OUT.read_text(encoding="utf-8"))
    return st
