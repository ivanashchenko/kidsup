"""Личный кабинет «Твой Класс» для всех, кто ходит в этом сезоне.

Владелец 17.09: разослать всем семьям сезона 2026/27 сообщение про кабинет —
преимущества, и дальше по ситуации: у кого почта в карточке есть — напомнить
и дать инструкцию входа; у кого нет — попросить почту, а когда ответят,
самим вписать её в карточку и ответным сообщением дать инструкцию. Семьям
из групп английского — отдельно сказать, что домашние задания, фото и
объявления педагога живут в чате группы внутри кабинета.

Что здесь важно и почему именно так:

  · Вход в кабинет — только по e-mail ученика (справка МойКласса). Значит
    родителю без почты писать «войдите» бессмысленно, ему нужен один
    вопрос — продиктовать почту. Отсюда два разных текста.

  · Отличить «уже пользовался» от «ещё нет» по данным нельзя: карточка
    МойКласса не отдаёт ни флага активации, ни даты входа (проверено 17.09 —
    поле отсутствует у всех 208 детей сезона). Поэтому текст для семей с
    почтой написан так, чтобы годиться обоим: кто уже входил — читает как
    напоминание, кто нет — как инструкцию.

  · Канал — «тот мессенджер, где с семьёй уже есть переписка, плюс WhatsApp»:
    это правило владельца от 23.08, и его реализует wazzup.send_smart с
    mass=False. Отправка разовая, а не рассылкой: это сервисное сообщение
    действующим клиентам, с которыми переписка идёт, — так же ушли
    приглашения в чаты 13.09.

  · 13.09 в этих же чатах ушёл неверный текст (вход «по номеру телефона» на
    app.moyklass.com/lk — кабинет сотрудника). Здесь адрес kidsup.tvoyklass.com,
    вход по почте, и это единственная причина, по которой текст собран в коде,
    а не набран руками в Wazzup.

  · Ответ с почтой ловим на вебхуке Wazzup: если от семьи, которую мы
    спрашивали, пришло сообщение с адресом — вписываем его в карточку ребёнка
    через safe_update_user (полная замена карточки иначе стирает всё) и тут
    же отвечаем инструкцией. Пароль клиент получает сам, через «Восстановить
    пароль», — никаких паролей мы не создаём и не пересылаем.

Отправка только через серверные функции; локально этот модуль ничего не шлёт.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from . import db

log = logging.getLogger("kidsup.tvoyklass")

CAMPAIGN = "tvoyklass_lk"
LK_URL = "https://kidsup.tvoyklass.com"
SEASON_PREFIX = "2627_"
ST_UCHITSYA = 2

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _first(name: str) -> str:
    parts = (name or "").split()
    return parts[1] if len(parts) > 1 else (parts[0] if parts else "")


def _p10(x) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())[-10:]


# ---------------------------------------------------------------- тексты

PLYUSY = ("В личном кабинете «Твой Класс» видно всё по ребёнку в одном месте: "
          "расписание и переносы занятий, посещаемость, остаток абонемента и "
          "оплата онлайн, домашние задания и материалы от педагога, напоминания "
          "о занятиях. Работает в браузере и в приложении «Твой Класс» "
          "(App Store, Google Play, RuStore).")

AY_CHAT = ("Для групп английского там же работает чат группы: домашние задания, "
           "фото с занятий и объявления педагог выкладывает именно в чат кабинета, "
           "а не в WhatsApp — так ничего не теряется.")

INSTRUKTSIYA = (f"Как войти (если ещё не входили):\n"
                f"1. Откройте {LK_URL}, введите почту {{почта}} и нажмите «Войти».\n"
                f"2. Система напишет, что вы входите первый раз, — нажмите "
                f"«Восстановить пароль».\n"
                f"3. Придёт письмо от отправителя «Личный кабинет» "
                f"(lk-noreply@tvoyklass.com). Если его нет во «Входящих» — "
                f"посмотрите «Спам».\n"
                f"4. По ссылке из письма задайте пароль: не короче 6 символов, "
                f"с заглавной и строчной буквой, цифрой и спецсимволом. Ссылка "
                f"живёт недолго — если не успели, запросите ещё раз.\n\n"
                f"В приложении «Твой Класс» вход теми же почтой и паролем. "
                f"Если что-то не получится — напишите сюда, поможем.")


def text_for(children: list[str], email: str, ay: bool) -> str:
    # Имена не склоняем: «для Василиса» хуже, чем именительный в скобках
    kids = " и ".join(_first(c) for c in children) or "ваш ребёнок"
    head = (f"Здравствуйте! Это KidsUP 🌿 У вас есть личный кабинет «Твой Класс» "
            f"({kids}) — напоминаем, что им можно пользоваться.\n\n{PLYUSY}")
    if ay:
        head += "\n\n" + AY_CHAT
    if email:
        return (head + "\n\nЕсли вы уже пользуетесь кабинетом — просто напоминаем, что "
                "он есть. Если ещё нет — вот порядок:\n\n"
                + INSTRUKTSIYA.replace("{почта}", email))
    return (head + "\n\nЛогин кабинета — адрес электронной почты, а у нас в карточке "
            "почты пока нет. Напишите её, пожалуйста, в ответ на это "
            "сообщение: мы впишем её в карточку и сразу пришлём короткую "
            "инструкцию, как войти.")


def reply_after_email(children: list[str], email: str, ay: bool) -> str:
    kids = " и ".join(_first(c) for c in children) or "ребёнок"
    t = (f"Спасибо! Почта {email} записана в карточку ({kids}). Теперь можно входить "
         f"в кабинет.\n\n" + INSTRUKTSIYA.replace("{почта}", email))
    if ay:
        t += "\n\n" + AY_CHAT
    return t


# ---------------------------------------------------------------- план

def _ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS lk_email_asked (
        phone TEXT PRIMARY KEY, uids TEXT, children TEXT, ay INTEGER,
        asked_at TEXT, answered_at TEXT, email TEXT, note TEXT)""")


def plan() -> dict:
    """Кому и что отправим. Ничего не отправляет."""
    fam: dict[str, dict] = {}
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT u.id, u.name, u.phone, u.email, c.name FROM joins j "
            "JOIN users u ON u.id = j.user_id JOIN classes c ON c.id = j.class_id "
            "WHERE j.status_id=? AND c.status='opened' AND c.name LIKE ? "
            "AND c.name NOT LIKE '%Заявки%' AND u.client_state_id NOT IN (146328, 215202, 125954)",
            (ST_UCHITSYA, SEASON_PREFIX + "%")).fetchall()
    for uid, name, phone, email, cls in rows:
        p = _p10(phone)
        if len(p) != 10 or not p.startswith("9"):
            continue
        f = fam.setdefault(p, {"phone": "7" + p, "uids": [], "children": [],
                               "email": "", "ay": False, "groups": []})
        if uid not in f["uids"]:
            f["uids"].append(uid)
            f["children"].append(name or "")
        if (email or "").strip() and not f["email"]:
            f["email"] = email.strip()
        if "_АЯ_" in cls:
            f["ay"] = True
        g = cls.replace(SEASON_PREFIX, "")
        if g not in f["groups"]:
            f["groups"].append(g)
    out = []
    for f in fam.values():
        f["variant"] = "с_почтой" if f["email"] else "без_почты"
        f["text"] = text_for(f["children"], f["email"], f["ay"])
        out.append(f)
    out.sort(key=lambda f: (not f["ay"], f["variant"], f["children"][0]))
    return {"total": len(out),
            "с_почтой": sum(1 for f in out if f["email"]),
            "без_почты": sum(1 for f in out if not f["email"]),
            "английский": sum(1 for f in out if f["ay"]),
            "recipients": out}


def enqueue(dry: bool = True, only_ay: bool = False) -> dict:
    """Поставить в очередь разовых сообщений. Повторно один номер не ставится."""
    p = plan()
    from .autopilot import _bq_init, _now
    now = _now().isoformat(timespec="seconds")
    n = 0
    with db.get_conn() as conn:
        _bq_init(conn)
        _ensure(conn)
        done = {r[0] for r in conn.execute(
            "SELECT phone FROM broadcast_queue WHERE campaign=?", (CAMPAIGN,))}
        for f in p["recipients"]:
            if f["phone"] in done or (only_ay and not f["ay"]):
                continue
            n += 1
            if dry:
                continue
            conn.execute("INSERT INTO broadcast_queue (campaign, phone, child, text, created) "
                         "VALUES (?, ?, ?, ?, ?)",
                         (CAMPAIGN, f["phone"], ", ".join(f["children"]), f["text"], now))
            if not f["email"]:
                conn.execute("INSERT OR REPLACE INTO lk_email_asked (phone, uids, children, ay, asked_at) "
                             "VALUES (?, ?, ?, ?, ?)",
                             (f["phone"][-10:], json.dumps(f["uids"]),
                              json.dumps(f["children"], ensure_ascii=False), int(f["ay"]), now))
    return {"ok": True, "dry_run": dry, "queued": n, **{k: v for k, v in p.items() if k != "recipients"}}


def deliver(limit: int = 5, dry: bool = True) -> dict:
    """Отправить пачку как разовые сообщения (см. chaty.deliver — та же логика).

    Разово, по несколько за вызов и только в рабочие часы: обычный номер
    переписки на массовом потоке отваливается (22.08 так ушёл в «не
    авторизован» 0077). Сто шестьдесят семей — это два-три дня по пачкам.
    """
    from . import wazzup
    from .autopilot import _now
    now = _now()
    if not (9 <= now.hour < 20):
        return {"ok": False, "error": "вне окна 9:00–20:00", "sent": 0}
    sent, errs = [], []
    with db.get_conn() as conn:
        # Номер, который дважды не принял сообщение, откладываем: иначе он
        # занимает место в каждой пачке и остальные ждут (17.09: два номера
        # пять пачек подряд «fail», остальные — по три вместо пяти).
        conn.execute(
            "UPDATE broadcast_queue SET status='hold' WHERE campaign=? AND status='pending' "
            "AND (LENGTH(COALESCE(tried,'')) - LENGTH(REPLACE(COALESCE(tried,''),'fail',''))) >= 8",
            (CAMPAIGN,))
        rows = conn.execute(
            "SELECT id, phone, child, text FROM broadcast_queue "
            "WHERE campaign=? AND status='pending' ORDER BY id LIMIT ?",
            (CAMPAIGN, max(1, min(15, int(limit))))).fetchall()
    for rid, phone, child, text in rows:
        if dry:
            sent.append({"phone": phone, "child": child, "dry": True})
            continue
        try:
            log_ = wazzup.send_smart(phone, text, dry_run=False, mass=False,
                                     kind=f"bc:{CAMPAIGN}")
            ok = any("ok" in x for x in log_)
        except Exception as e:  # noqa: BLE001
            ok, log_ = False, [str(e)[:150]]
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE broadcast_queue SET status=?, sent=?, tried=COALESCE(tried,'')||? WHERE id=?",
                ("sent" if ok else "pending",
                 now.isoformat(timespec="seconds") if ok else None,
                 "razovoe=" + ("ok" if ok else "fail") + ";", rid))
        (sent if ok else errs).append({"phone": phone, "child": child, "log": log_})
    with db.get_conn() as conn:
        left = conn.execute("SELECT COUNT(*) FROM broadcast_queue WHERE campaign=? AND status='pending'",
                            (CAMPAIGN,)).fetchone()[0]
    log.info("tvoyklass.deliver: отправлено %d, ошибок %d, осталось %d", len(sent), len(errs), left)
    return {"ok": True, "dry_run": dry, "sent": len(sent), "errors": errs, "left": left, "details": sent}


def unhold() -> dict:
    """Вернуть отложенные номера в очередь и обнулить счётчик неудач.

    Номер уходит в hold, когда сообщение дважды не приняли за один день:
    почти всегда причина — суточный предохранитель отправки, а не сам
    номер. Назавтра он снова годится, и держать его в стороне незачем.
    """
    with db.get_conn() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT id, phone, child FROM broadcast_queue "
                            "WHERE campaign=? AND status='hold'", (CAMPAIGN,)).fetchall()
        conn.execute("UPDATE broadcast_queue SET status='pending', tried='' "
                     "WHERE campaign=? AND status='hold'", (CAMPAIGN,))
    return {"ok": True, "вернули": len(rows),
            "номера": [{"телефон": r[1], "ребёнок": r[2]} for r in rows]}


def status() -> dict:
    with db.get_conn() as conn:
        _ensure(conn)
        q = dict(conn.execute(
            "SELECT status, COUNT(*) FROM broadcast_queue WHERE campaign=? GROUP BY status",
            (CAMPAIGN,)).fetchall())
        asked = conn.execute("SELECT COUNT(*) FROM lk_email_asked").fetchone()[0]
        answered = conn.execute(
            "SELECT phone, children, email, answered_at, note FROM lk_email_asked "
            "WHERE answered_at IS NOT NULL ORDER BY answered_at DESC").fetchall()
    return {"очередь": q, "спросили_почту": asked,
            "ответили": [{"телефон": "7" + r[0], "дети": json.loads(r[1] or "[]"),
                          "почта": r[2], "когда": r[3], "итог": r[4]} for r in answered]}


# ---------------------------------------------------------------- ответ с почтой

def _inbound_texts(payload: dict) -> list[tuple[str, str]]:
    """(телефон, текст) по всем входящим сообщениям события Wazzup."""
    out = []
    for m in payload.get("messages") or []:
        if m.get("isEcho") or m.get("status") == "outbound":
            continue
        chat = m.get("chatId") or ""
        phone = _p10(chat if str(chat).isdigit() else (m.get("contact") or {}).get("phone", ""))
        text = m.get("text") or ""
        if phone and text:
            out.append((phone, text))
    return out


def on_inbound(payload: dict) -> None:
    """Если семья, которую спрашивали про почту, прислала адрес — вписать и ответить."""
    pairs = _inbound_texts(payload)
    if not pairs:
        return
    with db.get_conn() as conn:
        _ensure(conn)
        asked = {r[0]: r for r in conn.execute(
            "SELECT phone, uids, children, ay FROM lk_email_asked WHERE answered_at IS NULL")}
    for phone, text in pairs:
        row = asked.get(phone)
        if not row:
            continue
        m = _EMAIL.search(text)
        if not m:
            continue
        email = m.group(0).strip().rstrip(".").lower()
        uids = json.loads(row[1] or "[]")
        children = json.loads(row[2] or "[]")
        ay = bool(row[3])
        note = _write_email(uids, email)
        try:
            from . import wazzup
            wazzup.send_smart("7" + phone, reply_after_email(children, email, ay),
                              dry_run=False, mass=False, kind="reply")
            note += "; ответ отправлен"
        except Exception as e:  # noqa: BLE001
            note += f"; ответ не ушёл: {str(e)[:80]}"
        with db.get_conn() as conn:
            conn.execute("UPDATE lk_email_asked SET answered_at=?, email=?, note=? WHERE phone=?",
                         (datetime.now().isoformat(timespec="seconds"), email, note, phone))
        log.info("tvoyklass: почта %s для %s — %s", email, phone, note)


def _write_email(uids: list[int], email: str) -> str:
    """Почта — в карточки всех детей на номере. Логин у каждого ребёнка свой,
    поэтому родителю с двумя детьми проще одна почта на обе карточки; если
    МойКласс не даст дубль — вторую пропускаем и пишем об этом в заметку."""
    from . import sync
    from .moyklass_client import MoyklassClient
    mk = MoyklassClient(sync.get_api_key())
    done, skipped = [], []
    try:
        for uid in uids:
            try:
                mk.safe_update_user(int(uid), email=email)
                mk.post("/v1/company/userComments",
                        {"userId": int(uid), "showToUser": False,
                         "comment": f"{datetime.now():%d.%m %H:%M} — родитель прислал почту для "
                                    f"личного кабинета «Твой Класс»: {email}. Вписана в карточку, "
                                    f"инструкция входа отправлена в ответ."})
                done.append(uid)
            except Exception as e:  # noqa: BLE001
                skipped.append(f"{uid}: {str(e)[:60]}")
    finally:
        mk.close()
    with db.get_conn() as conn:
        for uid in done:
            conn.execute("UPDATE users SET email=? WHERE id=?", (email, uid))
    return f"вписана: {done}" + (f"; не удалось: {skipped}" if skipped else "")
