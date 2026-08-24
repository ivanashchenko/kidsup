"""Чистка имён карточек и сторож, чтобы их снова не засоряли.

Зачем. В МойКлассе имя клиента — это имя ребёнка, и по нему идёт поиск,
подстановка в шаблоны и обращение в письмах. Администраторы годами
дописывали туда служебное: «(MAX)», «(писать в телеграмм)», «3 года»,
«(скидка 10% многодет.)», «ПЕРЕЕХАЛ НЕ ЗВОНИТЬ». 24.08 таких карточек
404 из 8090.

Чем это плохо: в рассылку уходит «Здравствуйте! Ваш ребёнок (Виктория
3 года)…», поиск по фамилии не находит, а главное — важные пометки
(скидка, запрет звонить) живут в тексте имени, где их не видит ни один
отчёт и ни одна автоматика.

Что делает модуль. Разбирает имя на чистую часть и служебные пометки,
СНАЧАЛА переносит пометки туда, где им место (тег, комментарий), и
только потом сокращает имя. Ничего не теряется: перед правкой полный
исходник имени пишется в комментарий карточки.

Запуск:
    python -m app.imena           — показать, что будет сделано
    python -m app.imena apply     — почистить
    python -m app.imena watch     — сторож: найти новые засорённые за сутки
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, timedelta

from . import sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.imena")

# Пометки, которые встречаются в именах, и куда их девать.
CHANNEL = re.compile(
    r"\s*\((?:MAX|max|Макс|макс|телеграм\w*|Телеграм\w*|тг|ТГ|вотсап|ватсап|"
    r"whats\w*|WhatsApp|вайбер|только\s+[^)]*|писать\s+в[^)]*|пишет\s+в[^)]*)\)", re.I)
DISCOUNT = re.compile(r"\s*\((?:[^)]*(?:скидк|многодет)[^)]*)\)", re.I)
NOCALL = re.compile(
    r"\s*\(?(?:никогда\s+)?не\s*звонить[^)]*\)?|\s*ПЕРЕЕХАЛ[А]?\s*НЕ\s*ЗВОНИТЬ", re.I)
# Состояния, дописанные к имени: «Михаил болеет не звонить» после снятия
# запрета оставался «Михаил болеет» — половина пометки в имени хуже целой.
STATE = re.compile(r"\s+(?:болеет|уехал[а]?|переехал[а]?|в\s+отпуске|в\s+академ\w*)\b", re.I)
AGE = re.compile(r"\s*[,–-]?\s*\b\d{1,2}(?:[.,]\d)?\s*(?:год(?:а|ик)?|лет|г\.|мес(?:яц\w*)?)\.?", re.I)
NOTE_TAIL = re.compile(r"\s*\((?:болеет|уехал\w*|переехал\w*|в\s+отпуске)[^)]*\)", re.I)

# Теги существуют в CRM, ставятся по id. Канальные были заведены раньше —
# им и место для «(MAX)» из имени.
TAG_DISCOUNT_ID = 118513      # 🎁 Скидка 10% многодетным
TAG_NOCALL_ID = 118514        # 🚫 Не звонить
TAG_CHANNEL = {"whatsapp": 117413, "telegram": 117414, "max": 117415}


def _channel_tag(txt: str) -> int | None:
    t = txt.lower()
    if "max" in t or "макс" in t:
        return TAG_CHANNEL["max"]
    if "телеграм" in t or "тг" in t or "telegram" in t:
        return TAG_CHANNEL["telegram"]
    if "whats" in t or "вотсап" in t or "ватсап" in t:
        return TAG_CHANNEL["whatsapp"]
    return None


def parse(name: str) -> tuple[str, dict]:
    """Имя → (чистое имя, что вынули). Порядок важен: сначала длинные
    скобочные конструкции, возраст последним — иначе «10%» съедается
    как возраст."""
    found: dict[str, list[str]] = {}
    rest = name

    def take(rx, key):
        nonlocal rest
        got = rx.findall(rest)
        if got:
            found.setdefault(key, []).extend(
                g if isinstance(g, str) else " ".join(g) for g in got)
            rest = rx.sub(" ", rest)

    take(DISCOUNT, "скидка")
    take(NOCALL, "не звонить")
    take(NOTE_TAIL, "заметка")
    take(STATE, "состояние")
    take(CHANNEL, "канал")
    take(AGE, "возраст")
    rest = re.sub(r"\s{2,}", " ", rest).strip(" ,-–—()")
    return rest, found


def _tag_ids(mk) -> dict:
    """Карта «имя тега → id». Теги ставятся только по id."""
    out = {}
    try:
        users = taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=1)
        for u in users:
            for t in (u.get("tags") or []):
                if isinstance(t, dict) and t.get("name"):
                    out[t["name"]] = t["id"]
    except Exception:
        pass
    return out


def scan(mk) -> list[dict]:
    users = taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=1)
    out = []
    for u in users:
        name = (u.get("name") or "").strip()
        if not name:
            continue
        clean, found = parse(name)
        # Чистим только если осталось осмысленное имя: «3 года» без имени
        # трогать нельзя — станет пустой карточкой.
        if found and clean and len(clean) >= 2:
            out.append({"uid": u["id"], "was": name, "clean": clean,
                        "found": found, "user": u})
    return out


def apply(dry: bool = True, limit: int = 0) -> dict:
    mk = MoyklassClient(sync.get_api_key())
    stat = {"найдено": 0, "почищено": 0, "тегов": 0, "ошибок": 0}
    try:
        items = scan(mk)
        stat["найдено"] = len(items)
        if dry:
            return stat
        for it in (items[:limit] if limit else items):
            uid, u = it["uid"], it["user"]
            try:
                # 1) исходник в комментарий — до любых правок
                parts = "; ".join(f"{k}: {', '.join(v)}" for k, v in it["found"].items())
                mk.post("/v1/company/userComments", {
                    "userId": uid, "showToUser": False,
                    "comment": (f"Клод, чистка имени: было «{it['was']}», стало "
                                f"«{it['clean']}». Вынесено — {parts}.")[:1000]})
                # 2) важные пометки — в теги, чтобы их видели отчёты
                want = []
                if "скидка" in it["found"]:
                    want.append(TAG_DISCOUNT_ID)
                if "не звонить" in it["found"]:
                    want.append(TAG_NOCALL_ID)
                for txt in it["found"].get("канал", []):
                    ct = _channel_tag(txt)
                    if ct:
                        want.append(ct)
                cur = [t["id"] for t in (u.get("tags") or []) if isinstance(t, dict)]
                add = [w for w in want if w not in cur]
                if add:
                    mk.post(f"/v1/company/users/{uid}/tags", {"tags": cur + add})
                    stat["тегов"] += len(add)
                # 3) и только теперь имя
                mk.safe_update_user(uid, name=it["clean"])
                stat["почищено"] += 1
            except Exception as e:
                stat["ошибок"] += 1
                log.warning("uid=%s не почищен: %s", uid, str(e)[:90])
            time.sleep(0.35)
    finally:
        mk.close()
    return stat


def watch(mk, days: int = 1) -> int:
    """Сторож: карточки, засорённые заново. Ставит одну задачу на всех
    с перечислением — админ поправит и запомнит."""
    since = (date.today() - timedelta(days=days)).isoformat()
    fresh = []
    for it in scan(mk):
        u = it["user"]
        if str(u.get("updatedAt") or u.get("createdAt") or "")[:10] >= since:
            fresh.append(it)
    if not fresh:
        return 0
    names = "; ".join(f"«{x['was'][:38]}»" for x in fresh[:6])
    body = (f"В именах карточек снова служебные пометки ({len(fresh)}): {names}. "
            f"Имя — только имя и фамилия ребёнка. Канал общения виден в Wazzup, "
            f"возраст — в дате рождения, скидка и «не звонить» — теги.")
    mk.post("/v1/company/tasks", {
        "managerIds": [154181], "categoryId": 104578,
        "beginDate": f"{date.today()}T06:00:00+00:00",
        "endDate": f"{date.today()}T17:00:00+00:00", "body": body[:250]})
    log.info("сторож имён: %d новых засорённых", len(fresh))
    return len(fresh)


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "watch" in sys.argv:
        mk = MoyklassClient(sync.get_api_key())
        try:
            print("новых засорённых:", watch(mk))
        finally:
            mk.close()
        return
    if "apply" in sys.argv:
        print(apply(dry=False))
        return
    mk = MoyklassClient(sync.get_api_key())
    try:
        items = scan(mk)
    finally:
        mk.close()
    print(f"найдено засорённых имён: {len(items)}\n")
    for it in items[:25]:
        parts = "; ".join(f"{k}={', '.join(v)}" for k, v in it["found"].items())
        print(f"   «{it['was'][:46]:48s}» → «{it['clean'][:26]:28s}»  [{parts[:40]}]")


if __name__ == "__main__":
    main()
