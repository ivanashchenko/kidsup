"""Перепроверка списка «Заявки без обработки» — по каждой строке отдельно.

11.09 Борис: «очень внимательно перепроверь, что эти 68 заявок реально
необработанные, может это дубли других карточек — оставь только те, где
действительно не доделали».

Блок на пульте (app/zayavki.py) считает касания по ОДНОЙ карточке: её
собственные записи, её сообщения, звонки на её телефон. Три дыры, из-за
которых строка может висеть зря:

  1. У семьи вторая карточка с тем же телефоном, и там ребёнок уже учится —
     заявка на первой карточке всё равно выглядит нетронутой;
  2. Админ поговорил и написал комментарий в карточку, но статус записи
     не сменил — по комментариям блок не смотрит вообще;
  3. Переписка считается только с даты заявки: писали раньше — не учтено.

Здесь по каждой заявке собираем всё, что есть на номере: карточки, их
записи, комментарии, переписку без ограничения по дате и звонки за сезон.
Только чтение: ни статусов, ни сообщений эта страница не трогает.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from . import db, sync, zayavki

log = logging.getLogger(__name__)

WORKING_JOIN = zayavki.WORKING_JOIN          # подтвердил / записался / посетил / учится / отработка
LIVE_STATE = {125952, 146950, 125951, 345768}  # записался, думает, новый лид, недозвон


# Комментарии, которые пишет сама система: подсказка для звонка, служебные
# пометки про дубли и переезды. Следом разговора они не являются.
ROBOT_RE = re.compile(
    r"^(🎯|🤖)|ПОДСКАЗКА ДЛЯ ЗВОНКА|^Перенесён номер визита Roistat|"
    r"^Дубль карточки|^Переехали — статус изменён автоматически|"
    # «Статус изменён с … на …» пишет сама CRM при смене статуса, а
    # «Звонок от 7…. Страница захвата» — метка Roistat о происхождении
    # заявки. Ни то, ни другое не значит, что с семьёй поговорили.
    r"^Статус изменён с |^Звонок от \d+\.? ?Страница захвата|Страница захвата: kidsup|"
    # авторассылки: сообщение ушло само, разговора с семьёй не было
    r"^Авто-реактивация|^Авто: подтверждение")

# Комментарий, которым админ сам отметил, что это вообще не семья:
# соискатель, рабочий номер компании, чужая студия, продажа услуг.
NOT_CLIENT_RE = re.compile(
    r"собеседовани|номер компании|не клиент|хочет познакомиться с управляющим|"
    r"предлага(ет|ют) (сотрудничеств|реклам|услуги)", re.I)


def _robot(text: str) -> bool:
    return bool(ROBOT_RE.search((text or "").strip()))


def _not_client(text: str) -> bool:
    """Ищем только в начале комментария: дальше по тексту слова попадаются
    в обычном разговоре («ходили в студию танцев») и дают ложные срабатывания."""
    return bool(NOT_CLIENT_RE.search((text or "").strip()[:120]))


# от «делать нечего» к «надо звонить»: у семьи берём самый рабочий вердикт
KIND_ORDER = ["не клиент", "работает", "говорили", "переписка", "писали", "звонили", "не доделали"]


def _rows(conn, sql: str, args: tuple) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args)]


def audit(limit: int = 0) -> dict:
    """Проходит горячие заявки блока и по каждой выносит вердикт."""
    data = zayavki.collect()
    hot = data["untouched"] + data["tried"]
    if limit:
        hot = hot[:limit]

    from .moyklass_client import MoyklassClient
    mk = MoyklassClient(sync.get_api_key())
    season = zayavki.SEASON_FROM
    out: list[dict] = []
    def _dict(path: str) -> dict:
        """Справочник статусов: МойКласс отдаёт то список, то объект с ключом."""
        try:
            d = mk.get(path)
        except Exception as e:  # noqa: BLE001
            log.warning("%s: %s", path, e)
            return {}
        if isinstance(d, dict):
            for v in d.values():
                if isinstance(v, list):
                    d = v
                    break
        return {x["id"]: x.get("name", "") for x in (d or []) if isinstance(x, dict) and x.get("id")}

    try:
        jstat = _dict("/v1/company/joinStatuses")
        cstat = _dict("/v1/company/clientStatuses")
        with db.get_conn() as conn:
            def _has(t: str) -> bool:
                return bool(conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name=?",
                                         (t,)).fetchone()[0])
            has_in, has_out = _has("wazzup_inbox"), _has("wazzup_outbox")
            classes = {r["id"]: r["name"] or "" for r in conn.execute("SELECT id, name FROM classes")}
            for r in hot:
                p10 = r["phone"]
                # --- все карточки на этом номере, а не только та, что в заявке
                cards = []
                try:
                    found = mk.get("/v1/company/users", {"phone": p10})
                    found = (found.get("users") if isinstance(found, dict) else found) or []
                except Exception as e:  # noqa: BLE001
                    log.warning("users?phone=%s: %s", p10, e)
                    found = []
                if not any(u.get("id") == r["uid"] for u in found):
                    try:
                        found.append(mk.get(f"/v1/company/users/{r['uid']}"))
                    except Exception:  # noqa: BLE001
                        pass
                works = []      # записи семьи в рабочем статусе (учится/записался/посетил)
                comments = []
                for u in found:
                    uid = u.get("id")
                    if not uid:
                        continue
                    jj = _rows(conn, "SELECT class_id, status_id, created_at FROM joins WHERE user_id=?", (uid,))
                    for j in jj:
                        if j["status_id"] in WORKING_JOIN:
                            works.append({"uid": uid, "class": re.sub(r"^2627_", "", classes.get(j["class_id"], "")),
                                          "status": jstat.get(j["status_id"], j["status_id"]),
                                          "created": (j["created_at"] or "")[:10]})
                    try:
                        cm = mk.get("/v1/company/userComments", {"userId": uid, "limit": 100})
                        cm = (cm.get("userComments") if isinstance(cm, dict) else cm) or []
                    except Exception:  # noqa: BLE001
                        cm = []
                    for c in cm:
                        comments.append({"uid": uid, "date": (c.get("createdAt") or "")[:10],
                                         "text": (c.get("comment") or "").strip()[:400]})
                    cards.append({"id": uid, "name": u.get("name") or "",
                                  "state": cstat.get(u.get("clientStateId"), u.get("clientStateId")),
                                  "state_id": u.get("clientStateId"),
                                  "created": (u.get("createdAt") or "")[:10],
                                  "joins": len(jj)})
                # --- переписка по номеру за весь сезон, без привязки к дате заявки
                n_in = conn.execute("SELECT COUNT(*) FROM wazzup_inbox WHERE substr(phone,-10)=? AND ts>=?",
                                    (p10, season)).fetchone()[0] if has_in else 0
                n_out = conn.execute("SELECT COUNT(*) FROM wazzup_outbox WHERE substr(phone,-10)=? AND ts>=?",
                                     (p10, season)).fetchone()[0] if has_out else 0
                comments.sort(key=lambda c: c["date"], reverse=True)
                # Машинные подсказки нашей же системы за касание не считаются:
                # «🎯 ПОДСКАЗКА ДЛЯ ЗВОНКА» админ не читал и клиенту не звонил.
                human = [c for c in comments if c["date"] >= r["created"] and not _robot(c["text"])]

                dup = len(cards) > 1
                if any(_not_client(c["text"]) for c in comments):
                    kind, why = "не клиент", ("админ уже пометил, что это не семья: "
                                              + next(c["text"][:90] for c in comments
                                                     if _not_client(c["text"])))
                elif works:
                    kind, why = "работает", ("у семьи есть запись в рабочем статусе: "
                                             + "; ".join(f"{w['class']} — {w['status']}" for w in works[:3]))
                elif human:
                    kind, why = "говорили", (f"после заявки в карточке живой комментарий "
                                             f"({human[0]['date']}): {human[0]['text'][:90]}")
                elif n_in:
                    kind, why = "переписка", f"клиент писал нам в мессенджер ({n_in} сообщ.)"
                elif n_out:
                    kind, why = "писали", f"мы писали в мессенджер ({n_out}), ответа нет"
                elif r.get("calls"):
                    kind, why = "звонили", f"звонков по номеру: {r['calls']}, разговора не вышло"
                else:
                    kind, why = "не доделали", "ни записи, ни живого комментария, ни звонка, ни переписки"
                if dup and kind == "не доделали":
                    why += f" · и на номере {len(cards)} карточки — проверить дубль"
                out.append({**r, "kind": kind, "why": why, "dup": dup, "cards": cards, "works": works,
                            "msg_in": n_in, "msg_out": n_out, "human_comments": len(human),
                            "comments": [c for c in comments if not _robot(c["text"])][:4]})
    finally:
        mk.close()

    # Одна семья оставляет несколько заявок (футбол, танцы, акробатика — один
    # телефон, шесть строк). Для работы это один звонок, а не шесть.
    fam: dict[str, dict] = {}
    for x in sorted(out, key=lambda x: KIND_ORDER.index(x["kind"]) if x["kind"] in KIND_ORDER else 9):
        f = fam.setdefault(x["phone"], {"phone": x["phone"], "name": x["name"], "kind": x["kind"],
                                        "why": x["why"], "days": x["days"], "uid": x["uid"],
                                        "cards": len(x["cards"]), "хочет": []})
        f["хочет"].append(x["class"] or x["comment"])
        f["days"] = max(f["days"], x["days"])
    families = sorted(fam.values(), key=lambda f: -f["days"])

    by: dict[str, int] = {}
    for x in out:
        by[x["kind"]] = by.get(x["kind"], 0) + 1
    by_fam: dict[str, int] = {}
    for f in families:
        by_fam[f["kind"]] = by_fam.get(f["kind"], 0) + 1
    return {"проверено": len(out), "итог": by, "семей": len(families), "итог_по_семьям": by_fam,
            "заявки": out, "семьи": families,
            "было_в_блоке": len(data["untouched"]) + len(data["tried"])}
