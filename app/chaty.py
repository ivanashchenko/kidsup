"""Приглашения в групповые чаты учебных групп.

Зачем. Владелец завёл в WhatsApp по чату на каждую группу английского:
там расписание, переносы, домашка и фото с занятий. Чат работает, только
если в нём есть родители, а зовут в него сейчас голосом на ресепшене —
доходит до половины.

Как устроено. Ссылки-приглашения живут в настройке `group_chats`:
{"727741": "https://chat.whatsapp.com/...", ...} — ключ это id группы
в МойКлассе. Кому писать, берём оттуда же: родители детей со статусом
записи «Учится» (2) в этих группах. Никаких «всех подряд»: чат группы
нужен только тем, кто в неё ходит.

Один родитель — одно сообщение. У двоих детей в разных группах телефон
общий, и два приглашения подряд читаются как рассылка; вместо этого в
одном сообщении идут обе ссылки, каждая подписана своей группой.

Отправка идёт очередью broadcast_queue — там уже сделано всё, без чего
писать родителям нельзя: окно 9:00–20:00, лимиты номера, каскад
WhatsApp → мессенджеры, пропуск тех, кто ждёт ответа админа, и своих
номеров. СМС отправляем только тем, кому сообщение не доставилось: это
не реклама, а сервисное уведомление уже записавшимся, и такие СМС закон
разрешает (все получатели к тому же ранее платили).

В СМС ссылка идёт короткая — app.kidsup.ru/chat/<код>: полная
chat.whatsapp.com съедает половину сообщения, а по короткой ещё и видно,
сколько человек перешло.
"""

from __future__ import annotations

import json
import logging
import re

from . import db

log = logging.getLogger("kidsup.chaty")

CAMPAIGN = "chat_invite"
# префикс имени группы в МойКлассе → как называем направление родителю
KINDS = {"2627_АЯ": "английского"}


def links() -> dict[int, str]:
    """Ссылки-приглашения: id группы → ссылка. Кривые записи отбрасываем."""
    try:
        raw = json.loads(db.get_setting("group_chats", "") or "{}")
    except ValueError:
        return {}
    out = {}
    for k, v in (raw or {}).items():
        s = str(v or "").strip()
        if s.startswith("https://") and str(k).isdigit():
            out[int(k)] = s
    return out


INVITE_RE = re.compile(r"https://chat\.whatsapp\.com/[A-Za-z0-9]{10,40}")


def find_links(limit: int = 40) -> dict:
    """Поискать ссылки-приглашения в истории переписки.

    Чаты заводят с телефона, а ссылку потом кидают родителю в диалог.
    Значит она уже лежит в переписке — и её не надо просить заново.
    Кому именно её отправляли, видно по телефону: так понятно, какой
    группе какая ссылка принадлежит."""
    found: dict[str, dict] = {}
    with db.get_conn() as conn:
        for table in ("wazzup_outbox", "wazzup_inbox"):
            try:
                rows = conn.execute(
                    f"SELECT ts, phone, text FROM {table} "
                    f"WHERE text LIKE '%chat.whatsapp.com%' ORDER BY ts DESC "
                    f"LIMIT ?", (int(limit) * 5,)).fetchall()
            except Exception:
                continue
            for ts, phone, text in rows:
                for url in INVITE_RE.findall(text or ""):
                    f = found.setdefault(url, {"url": url, "first_seen": ts,
                                               "phones": [], "where": table})
                    if phone and phone not in f["phones"]:
                        f["phones"].append(phone)
    # телефон получателя → группы, в которых учится его ребёнок
    who = _phone_classes()
    for f in found.values():
        cls: dict[int, str] = {}
        for ph in f["phones"]:
            for cid, title in who.get((ph or "")[-10:], []):
                cls[cid] = title
        f["classes"] = [{"id": c, "title": t} for c, t in cls.items()]
    return {"total": len(found), "links": sorted(
        found.values(), key=lambda f: f["first_seen"] or "", reverse=True)}


def _phone_classes() -> dict[str, list[tuple[int, str]]]:
    """Телефон (10 цифр) → группы его детей среди активных 2627_*."""
    out: dict[str, list[tuple[int, str]]] = {}
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT u.phone, c.id, c.name FROM joins j
               JOIN users u ON u.id = j.user_id
               JOIN classes c ON c.id = j.class_id
               WHERE j.status_id = 2 AND c.status = 'opened'
                 AND u.phone IS NOT NULL AND u.phone != ''""").fetchall()
    for phone, cid, name in rows:
        out.setdefault((phone or "")[-10:], []).append((cid, name))
    return out


def code_of(class_id: int) -> str:
    """Короткий код группы для ссылки в СМС: «Гр6» из имени → g6."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT name FROM classes WHERE id=?", (class_id,)).fetchone()
    m = re.search(r"\(Гр\s*(\d+)\)", (row[0] if row else "") or "")
    return f"g{m.group(1)}" if m else f"c{class_id}"


def by_code(code: str) -> str:
    """Ссылка на чат по короткому коду — для редиректа /chat/<код>."""
    for cid, url in links().items():
        if code_of(cid) == code:
            return url
    return ""


def _short(class_id: int) -> str:
    """Как называем группу родителю: «вт-чт 17:00» вместо имени из CRM."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT name FROM classes WHERE id=?", (class_id,)).fetchone()
    name = (row[0] if row else "") or ""
    m = re.search(r"_((?:пн|вт|ср|чт|пт|сб|вс)[^_]*)_(\d{1,2}:\d{2})", name)
    return f"{m.group(1)} {m.group(2)}" if m else name


def plan() -> dict:
    """Кому и что отправим. Ничего не отправляет и не пишет в очередь."""
    ln = links()
    if not ln:
        return {"ok": False, "error": "не заданы ссылки: настройка group_chats пуста",
                "recipients": []}
    marks = ",".join("?" for _ in ln)
    with db.get_conn() as conn:
        rows = conn.execute(
            f"""SELECT j.class_id, u.id, u.name, u.phone
                FROM joins j JOIN users u ON u.id = j.user_id
                WHERE j.class_id IN ({marks}) AND j.status_id = 2
                  AND u.phone IS NOT NULL AND u.phone != ''""",
            list(ln)).fetchall()
    # телефон → что писать: у второго ребёнка в семье тот же номер
    fam: dict[str, dict] = {}
    for class_id, uid, name, phone in rows:
        f = fam.setdefault(phone, {"phone": phone, "uid": uid, "child": name,
                                   "by_class": {}})
        # брат и сестра в одной группе — это один чат, а не два: иначе
        # родитель получает одну и ту же ссылку дважды
        g = f["by_class"].setdefault(class_id, {
            "class_id": class_id, "title": _short(class_id),
            "children": [], "link": ln[class_id], "code": code_of(class_id)})
        g["children"].append(name)
    out = []
    for f in fam.values():
        f["groups"] = sorted(f.pop("by_class").values(), key=lambda g: g["title"])
        for g in f["groups"]:
            g["child"] = ", ".join(_first(n) for n in g["children"])
        f["text"] = text_for(f["groups"])
        f["sms"] = sms_for(f["groups"])
        out.append(f)
    out.sort(key=lambda f: f["child"] or "")
    return {"ok": True, "classes": len(ln), "recipients": out, "total": len(out)}


def text_for(groups: list[dict]) -> str:
    """Сообщение в WhatsApp. Одна группа — коротко, две — с подписями."""
    kind = "английского"
    if len(groups) == 1:
        g = groups[0]
        return (f"Здравствуйте! Это KidsUP. Мы завели чат группы {kind} "
                f"{g['title']} — там расписание, переносы, домашние задания "
                f"и фото с занятий. Заходите: {g['link']}\n\n"
                f"В чате только родители этой группы и педагог. "
                f"Если что-то не открывается — напишите нам, поможем.")
    lines = "\n".join(f"· {g['title']} ({g['child']}) — {g['link']}"
                      for g in groups)
    return (f"Здравствуйте! Это KidsUP. Мы завели чаты групп {kind} — там "
            f"расписание, переносы, домашние задания и фото с занятий. "
            f"Ваши группы:\n{lines}\n\nВ каждом чате только родители этой "
            f"группы и педагог. Если что-то не открывается — напишите нам, поможем.")


def sms_for(groups: list[dict]) -> str:
    """СМС-версия: коротко и со ссылкой через наш домен."""
    if len(groups) == 1:
        return (f"KidsUP: чат группы английского {groups[0]['title']} — "
                f"app.kidsup.ru/chat/{groups[0]['code']}")
    return ("KidsUP: чаты ваших групп английского — "
            + ", ".join(f"app.kidsup.ru/chat/{g['code']}" for g in groups))


def _first(name: str) -> str:
    """Имя ребёнка из «Фамилия Имя» — для подписи, какая группа чья."""
    parts = (name or "").split()
    return parts[1] if len(parts) > 1 else (parts[0] if parts else "")


def groups() -> list[dict]:
    """Активные группы направления с составом — для страницы /chaty.

    Нужна и до рассылки: пока ссылок нет, админ добавляет родителей
    в чат руками, и ему нужен точный список телефонов по группе.
    """
    ln = links()
    with db.get_conn() as conn:
        # «2627_АЯ_Заявки» — буфер новых обращений, а не учебная группа:
        # чата у неё нет и родителей в ней быть не может
        cls = conn.execute(
            """SELECT id, name FROM classes
               WHERE status='opened' AND name LIKE '2627_АЯ_%'
                 AND name NOT LIKE '%Заявки%'
               ORDER BY name""").fetchall()
        out = []
        for cid, name in cls:
            kids = conn.execute(
                """SELECT u.name, u.phone FROM joins j
                   JOIN users u ON u.id = j.user_id
                   WHERE j.class_id = ? AND j.status_id = 2
                   ORDER BY u.name""", (cid,)).fetchall()
            out.append({"id": cid, "name": name, "title": _short(cid),
                        "code": code_of(cid), "link": ln.get(cid, ""),
                        "kids": [{"name": k, "phone": p} for k, p in kids]})
    return out


def save_links(raw: dict) -> dict:
    """Сохранить ссылки со страницы. Пустое поле — убрать ссылку группы."""
    keep = {}
    for k, v in (raw or {}).items():
        s = str(v or "").strip()
        if not str(k).isdigit():
            continue
        if s and not s.startswith("https://chat.whatsapp.com/"):
            return {"ok": False, "error": f"не похоже на ссылку-приглашение: {s[:60]}"}
        if s:
            keep[str(k)] = s
    db.set_setting("group_chats", json.dumps(keep, ensure_ascii=False))
    return {"ok": True, "saved": len(keep)}


def send(dry: bool = True) -> dict:
    """Поставить приглашения в очередь рассылки."""
    p = plan()
    if not p.get("ok"):
        return p
    from .autopilot import _bq_init, _now
    now = _now().isoformat(timespec="seconds")
    n = 0
    with db.get_conn() as conn:
        _bq_init(conn)
        done = {r[0] for r in conn.execute(
            "SELECT phone FROM broadcast_queue WHERE campaign=?", (CAMPAIGN,))}
        for f in p["recipients"]:
            if f["phone"] in done:
                continue        # приглашение этому номеру уже стоит в очереди
            if dry:
                n += 1
                continue
            conn.execute("INSERT INTO broadcast_queue (campaign, phone, child, "
                         "text, created) VALUES (?, ?, ?, ?, ?)",
                         (CAMPAIGN, f["phone"], f["child"], f["text"], now))
            n += 1
    log.info("chaty: кампания %s — %d получателей (dry=%s)", CAMPAIGN, n, dry)
    return {"ok": True, "dry_run": dry, "queued": n, "total": p["total"]}
