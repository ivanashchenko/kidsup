"""Прогон свежих подсказок по всем карточкам, где есть открытая задача обзвона.

Зачем отдельно от hintrefresh. Тот модуль работает по снимку taskplan.json,
собранному во время разбора задач: он хорош сразу после разбора, но через
несколько часов список карточек уже другой — задачи закрылись, появились
новые. Этот прогон берёт карточки из CRM напрямую, поэтому его можно
запускать когда угодно.

Что кладёт. Подсказку из app.hint — ту, где есть АРГУМЕНТ («с чего начать»),
ИЕРАРХИЯ предложения (главное и вторым предметом со скидкой), диагностика
на первом занятии и все три запускающих события. В карточках до сих пор
лежали подсказки старого поколения — «ЧТО СКАЗАТЬ ПРИ ЗВОНКЕ» от 16.08,
где перечислены факты, но не сказано, с чего начать разговор.

Старые подсказки не удаляет: комментарии в МойКласс — это история клиента,
а не доска объявлений. Новая ложится сверху и видна первой.

Запуск:
    python -m app.hintsweep show     — посмотреть, кому и что
    python -m app.hintsweep apply    — записать в карточки
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date

from . import brain
from . import hint, sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.hintsweep")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"

CALL_TASK = re.compile(
    r"обзвон|позвони|перезвон|продолжение занятий|дожать|дожим|пригласи|"
    r"продающий звонок|📞", re.I)
FRESH = "🎯 ПОДСКАЗКА ДЛЯ ЗВОНКА"
STAFF = (232763, 232805, 202856, 154181)


def _subject_of(name: str) -> str | None:
    n = name or ""
    if n.startswith(("ДОД", "МК")):
        return None
    if "_ПШ" in n or n.startswith("ПШ") or "одготовка" in n:
        return "подготовку к школе"
    if "_АЯ" in n or n.startswith("АЯ") or "_ЛК" in n:
        return "английский"
    if "ини-сад" in n or "нулевой" in n.lower():
        return "детский сад"
    if ("МсМ" in n or "узыка и речь" in n or "_РР" in n or n.startswith("РР")
            or "азвити" in n or "ицей" in n):
        return "раннее развитие"
    if "_ЛГ" in n or "огопед" in n:
        return "логопеда"
    if "ШАХ" in n:
        return "шахматы"
    if "ИЗО" in n:
        return "ИЗО"
    return None


def targets(mk: MoyklassClient) -> dict[int, dict]:
    """Карточки, по которым сейчас висит открытая задача обзвона."""
    out: dict[int, dict] = {}
    for mid in STAFF:
        for t in taskguard.all_tasks(mk, mid):
            if t.get("isComplete") or t.get("isCompleted"):
                continue
            uid = t.get("userId")
            if uid and CALL_TASK.search(t.get("body") or ""):
                out.setdefault(uid, {"task": t["id"]})
    return out


def _facts(mk: MoyklassClient, uid: int, cache: dict, classes: dict) -> dict:
    """Имя, возраст и история посещений. Сначала из снимка разбора —
    он уже собран и стоил вызовов API; чего нет, доспрашиваем."""
    c = dict(cache.get(str(uid)) or {})
    if c.get("name") and c.get("was_classes") is not None:
        return c
    try:
        u = mk.get(f"/v1/company/users/{uid}")
        c["name"] = c.get("name") or u.get("name") or ""
        for a in (u.get("attributes") or []):
            if a.get("attributeAlias") == "birthday" and a.get("value"):
                c["birthday"] = a["value"][:10]
    except Exception:
        return c
    try:
        lr = mk.get("/v1/company/lessonRecords",
                    {"userId": uid, "limit": 200,
                     "includeLessons": True}).get("lessonRecords") or []
    except Exception:
        lr = []
    was, last = set(), ""
    for r in lr:
        if not r.get("visit"):
            continue
        les = r.get("lesson") or {}
        nm = classes.get(les.get("classId"), "")
        if nm:
            was.add(nm)
        d = (les.get("date") or "")[:10]
        if d > last:
            last = d
    c["was_classes"] = sorted(was)[:6]
    c["last_visit"] = last
    return c


def _profile(mk: MoyklassClient, uid: int, c: dict, was) -> str:
    """Всё, что мы знаем о семье, — одним текстом для модели.

    Комментарии в карточке важнее статистики: там лежат обещания, обиды
    и договорённости, из-за которых один и тот же по цифрам клиент
    требует совершенно разного разговора."""
    parts = [f"Ребёнок: {c.get('name') or '—'}"]
    if c.get("birthday"):
        a = hint.age_on_season(c["birthday"])
        if a:
            parts.append(f"Возраст на 1 сентября: {a}")
    if was:
        parts.append("Посещал: " + ", ".join(sorted(set(was))))
    if c.get("last_visit"):
        parts.append(f"Последний визит: {c['last_visit']}")
    if c.get("enrolled"):
        parts.append("Записан на 2026/27: " + str(c["enrolled"][0])[:60])
    else:
        parts.append("На 2026/27 НЕ записан")
    try:
        r = mk.get("/v1/company/userComments", {"userId": uid, "limit": 8})
        cm = r.get("comments") or r.get("userComments") or []
    except Exception:
        cm = []
    human = []
    for x in cm:
        t = (x.get("comment") or "").strip()
        # Свои же подсказки в профиль не кладём — иначе модель пересказывает
        # саму себя вместо того, чтобы читать историю клиента.
        if not t or t.startswith(("🎯", "🤖")) or "ПОДСКАЗКА ДЛЯ ЗВОНКА" in t:
            continue
        human.append(f"· {(x.get('createdAt') or '')[:10]}: {t[:220]}")
    if human:
        parts.append("Из карточки:\n" + "\n".join(human[:6]))
    return "\n".join(parts)


def _personal(mk: MoyklassClient, uid, c: dict, was) -> str | None:
    if not brain.enabled():
        return None
    h = brain.call_hint(_profile(mk, int(uid), c, was))
    if not h or not h.get("начать"):
        return None
    out = ["👤 ПОД ЭТУ СЕМЬЮ",
           "НАЧАТЬ ТАК: " + h["начать"]]
    if h.get("вопрос"):
        out.append("СПРОСИТЬ И ЗАМОЛЧАТЬ: " + h["вопрос"])
    if h.get("главное"):
        out.append("ГЛАВНОЕ: " + h["главное"])
    if h.get("вторым"):
        out.append("ВТОРЫМ (−10%): " + h["вторым"])
    if h.get("закрыть"):
        out.append("ЗАКРЫТЬ: " + h["закрыть"])
    if h.get("внимание"):
        out.append("⚠️ " + h["внимание"])
    return "\n".join(out)


def run(apply: bool = False, limit: int = 0) -> dict:
    mk = MoyklassClient(sync.get_api_key())
    stat = {"карточек": 0, "подсказок": 0, "уже была": 0, "без имени": 0,
            "ошибок": 0}
    try:
        try:
            cache = json.load(open(f"{SP}/taskplan.json"))["clients"]
        except Exception:
            cache = {}
        try:
            rc = mk.get("/v1/company/classes", {"limit": 500})
            classes = {c["id"]: c.get("name") or ""
                       for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
        except Exception:
            classes = {}

        tg = targets(mk)
        stat["карточек"] = len(tg)
        today = date.today().isoformat()
        items = list(tg.items())[:limit] if limit else list(tg.items())
        samples = []

        for i, (uid, info) in enumerate(items):
            try:
                r = mk.get("/v1/company/userComments", {"userId": uid, "limit": 6})
                cm = r.get("comments") or r.get("userComments") or []
            except Exception:
                cm = []
            if any(FRESH in (c.get("comment") or "")
                   and (c.get("createdAt") or "").startswith(today) for c in cm):
                stat["уже была"] += 1
                continue

            c = _facts(mk, uid, cache, classes)
            if not c.get("name"):
                stat["без имени"] += 1
                continue
            was = [s for s in (_subject_of(x) for x in
                               (c.get("was_classes") or [])) if s] or None
            text = hint.build(c["name"], birthday=c.get("birthday"), was=was,
                              last_visit=c.get("last_visit"),
                              enrolled=c.get("enrolled") or None)
            # Поверх шаблонной подсказки — разбор под конкретную семью.
            # Шаблон остаётся основой: он гарантирует, что обязательные
            # формулировки и события названы, а модель добавляет то, чего
            # шаблон знать не может — историю, обиды, незакрытые обещания.
            personal = _personal(mk, uid, c, was)
            if personal:
                text = personal + "\n\n" + text
            if len(samples) < 3:
                samples.append(text)
            if apply:
                try:
                    mk.post("/v1/company/userComments",
                            {"userId": uid, "showToUser": False,
                             "comment": text[:hint.LIMIT]})
                    stat["подсказок"] += 1
                except Exception:
                    stat["ошибок"] += 1
            else:
                stat["подсказок"] += 1
            if i % 25 == 0:
                log.info("... %d/%d", i, len(items))
            time.sleep(0.3)
        stat["примеры"] = samples
    finally:
        mk.close()
    return stat


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    apply = "apply" in sys.argv
    lim = next((int(a) for a in sys.argv[1:] if a.isdigit()), 0)
    r = run(apply=apply, limit=lim)
    for s in r.pop("примеры", [])[:2]:
        print("=" * 68); print(s); print()
    print(r)


if __name__ == "__main__":
    main()
