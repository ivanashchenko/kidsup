"""Wazzup24: рассылки и «догон недозвона» в WhatsApp / Telegram / MAX.

Режимы отправки:
  cascade   — по очереди: WhatsApp → Telegram → MAX, до первой удачной доставки
  broadcast — во все мессенджеры сразу (для важных объявлений)

Запуск:
    python -m app.wazzup channels                       — список каналов
    python -m app.wazzup send --phone 79... --template nedozvon --dry-run
    python -m app.wazzup send --phone 79... --text "..." --all
"""

import argparse

import logging

import os
import json
import httpx

from . import db

API = "https://api.wazzup24.com/v3"

# приоритет транспортов (у Wazzup телеграм-канал зовётся tgapi).
# ВАЖНО (12.08): инициирующие сообщения незнакомым номерам — ТОЛЬКО WhatsApp.
# Личный Telegram не может писать чужим номерам массово: Wazzup принимает
# сообщение (HTTP 200), но доставка падает у ВСЕХ получателей (приватность
# «не находить по номеру» + PeerFlood-лимит Телеграма), а канал рискует
# блокировкой. MAX-канал уже заблокирован. tgapi/max оставлены только для
# явной отправки через send_via (например, ответ в уже открытый диалог).
CASCADE = ["whatsapp"]
# chatType — это тип ДИАЛОГА, а не название транспорта. У WABA транспорт
# зовётся «wapi», но диалог всё равно whatsapp: без этой строки уходило
# chatType="wapi", которого у Wazzup нет.
CHAT_TYPE = {"whatsapp": "whatsapp", "wapi": "whatsapp",
             "tgapi": "telegram", "max": "max", "vk": "vk"}
# Порядок предпочтения WhatsApp-номеров (настройка wa_senders, через запятую).
# 0077 стоит ПОСЛЕДНИМ намеренно: это канал переписки с историей банов, его
# квота — считанные сообщения в день. 22.08 он был первым в этой константе,
# и запуск рассылки в обход сервера (где wa_senders задан правильно) увёл
# через него весь поток — номер отвалился в «не авторизован», а сообщения
# перестали доставляться. Порядок здесь обязан совпадать с боевой настройкой.
WHATSAPP_PREFERRED = "79160170918,79199683507,79165610077"

TEMPLATES = {
    "nedozvon": (
        "Здравствуйте, {name}! Это KidsUP (Бульвар Рокоссовского) 🎈\n"
        "Звонили вам сегодня, но не дозвонились. Мы набираем группы на новый "
        "учебный год, и для {child} есть место в группе «{group}».\n"
        "До 25 августа действует цена прошлого года. Удобно будет созвониться "
        "сегодня или завтра? Или напишите здесь — всё расскажем 😊"
    ),
    "probnoe_reminder": (
        "{name}, напоминаем: завтра в {time} у {child} пробное занятие "
        "«{group}» в KidsUP (Открытое шоссе, 21к11). Ждём вас! Если планы "
        "изменились — просто напишите сюда, подберём другое время."
    ),
    "rannyaya_cena": (
        "{name}, здравствуйте! Это KidsUP. Для своих — ранняя цена на "
        "2026/27 учебный год действует до 25 августа + диагностика в подарок. "
        "Забронировать место для {child}? Ответьте «да» — и мы всё оформим."
    ),
}


def _headers() -> dict:
    key = db.get_setting("wazzup_key")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def channels() -> list[dict]:
    r = httpx.get(f"{API}/channels", headers=_headers(), timeout=30)
    r.raise_for_status()
    return [c for c in r.json() if c.get("state") == "active"]


def all_channels() -> list[dict]:
    """Все каналы, включая blocked, — нужно, чтобы заметить отвалившийся номер."""
    r = httpx.get(f"{API}/channels", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


OUR_HOOK = "https://app.kidsup.ru/wazzup/webhook"


def webhook_uri() -> str:
    r = httpx.get(f"{API}/webhooks", headers=_headers(), timeout=30)
    r.raise_for_status()
    return (r.json() or {}).get("webhooksUri") or ""


def set_webhook(uri: str = OUR_HOOK) -> bool:
    """Вернуть вебхук на наш портал.

    Wazzup хранит ровно ОДИН адрес. Интеграция МойКласс при пересохранении
    перезаписывает его на свой — и мы перестаём видеть переписку, а
    рассылка перестаёт получать статусы доставки. Инцидент 19.08.2026:
    события пропали в 13:02 и не приходили 20 часов, пока не заметили.
    Наш эндпоинт пересылает всё в МойКласс сам, поэтому чаты в CRM
    от возврата адреса не страдают."""
    r = httpx.patch(f"{API}/webhooks", headers=_headers(), timeout=40, json={
        "webhooksUri": uri,
        "subscriptions": {"messagesAndStatuses": True, "contactsAndDealsCreation": True,
                          "channelsUpdates": True, "wabaTemplatesStatus": False}})
    return r.status_code < 300


def _pick(chans: list[dict], transport: str) -> dict | None:
    cand = [c for c in chans if c.get("transport") == transport]
    # Заблокированный канал Wazzup принимает сообщение и отвечает 201, но не
    # доставляет — молча теряется вся порция. Живые состояния отсекаем здесь,
    # а не только в channels(): сюда попадают и списки из all_channels().
    live = [c for c in cand
            if str(c.get("state") or "").lower() in {"active", "opened", "ready"}]
    cand = live or cand
    if transport == "whatsapp":
        pref = [p.strip() for p in
                (db.get_setting("wa_senders", WHATSAPP_PREFERRED) or WHATSAPP_PREFERRED).split(",")]
        cand.sort(key=lambda c: pref.index(c.get("plainId"))
                  if c.get("plainId") in pref else len(pref))
    return cand[0] if cand else None


def send(phone: str, text: str, mode: str = "cascade", dry_run: bool = True,
         transports: list[str] | None = None) -> list[str]:
    """Отправка сообщения. transports ограничивает каналы (например ["tgapi"])."""
    phone = "".join(ch for ch in phone if ch.isdigit())
    if phone.startswith("8") and len(phone) == 11:
        phone = "7" + phone[1:]
    log, chans = [], channels()
    order = transports or CASCADE  # broadcast шлёт во все перечисленные
    for transport in order:
        ch = _pick(chans, transport)
        if not ch:
            log.append(f"{transport}: активного канала нет")
            continue
        if dry_run:
            log.append(f"[dry-run] {transport} ({ch['channelId'][:8]}…) → {phone}: {text[:60]}…")
            ok = True
        else:
            r = httpx.post(f"{API}/message", headers=_headers(), json={
                "channelId": ch["channelId"], "chatType": CHAT_TYPE.get(transport, transport),
                "chatId": phone, "text": text,
            }, timeout=30)
            ok = r.status_code in (200, 201)
            log.append(f"{transport} → {phone}: HTTP {r.status_code} {r.text[:120]}")
        if ok and mode == "cascade":
            break
    return log




def send_via(transport: str, phone: str, text: str, dry_run: bool = True,
             sender: str | None = None, template_id: str | None = None,
             template_values: list | None = None) -> bool:
    """Отправка строго через один канал. sender — plainId конкретного номера
    (для ротации WhatsApp). True = принял к доставке.

    Для WABA вне 24-часового окна Meta пропускает ТОЛЬКО утверждённый
    шаблон: произвольный текст Wazzup принимает и отвечает 201, но до
    получателя он не доходит и в статистике Meta не появляется вовсе
    (22.08: 24 «отправленных» сообщения — ноль в WhatsApp Manager).
    Поэтому у wapi при заданном waba_template_id уходит templateId,
    а текст идёт значениями подстановки."""
    phone = "".join(ch for ch in phone if ch.isdigit())
    if phone.startswith("8") and len(phone) == 11:
        phone = "7" + phone[1:]
    chans = channels()
    if sender:
        chans = [c for c in chans if c.get("plainId") == sender] or chans
    ch = _pick(chans, transport)
    if not ch:
        return False
    if dry_run:
        return True
    if transport == "wapi" and template_id is None:
        template_id = db.get_setting("waba_template_id", "") or None
    if transport == "wapi" and not template_id:
        # Без шаблона WABA-отправка бессмысленна: Wazzup ответит 201, строка
        # пометится доставленной, а адресат не получит ничего и второй раз
        # ему уже не напишут. Лучше честный отказ — он вернёт строку в очередь.
        log.warning("wapi: не задан waba_template_id — отправка отменена")
        return False
    if transport == "wapi" and template_id:
        body = {"channelId": ch["channelId"], "chatType": "whatsapp",
                "chatId": phone, "templateId": template_id}
        if template_values is not None:
            body["templateValues"] = template_values
        r = httpx.post(f"{API}/message", headers=_headers(), json=body, timeout=30)
        if r.status_code not in (200, 201):
            log.warning("wazzup шаблон отклонён: %s %s", r.status_code, r.text[:200])
        return r.status_code in (200, 201)
    r = httpx.post(f"{API}/message", headers=_headers(), json={
        "channelId": ch["channelId"], "chatType": CHAT_TYPE.get(transport, transport),
        "chatId": phone, "text": text,
    }, timeout=30)
    return r.status_code in (200, 201)


def _log_outbox(phone: str, text: str) -> None:
    """Запасной путь записи своих отправок. Обычно не нужен: Wazzup присылает
    наши исходящие в вебхук как isEcho — проверено 14.08."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from . import db
        ts = datetime.now(ZoneInfo("Europe/Moscow")).isoformat(timespec="seconds")
        with db.get_conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS wazzup_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
                message_id TEXT UNIQUE, text TEXT)""")
            conn.execute(
                "INSERT OR IGNORE INTO wazzup_outbox (ts, phone, message_id, text) "
                "VALUES (?, ?, ?, ?)", (ts, phone, f"self-{phone}-{ts}", text[:500]))
    except Exception:  # логирование не должно ломать отправку
        logging.getLogger("kidsup.wazzup").warning("outbox: не записалось для %s", phone)

def main():
    ap = argparse.ArgumentParser(description="Wazzup-рассылки KidsUP")
    ap.add_argument("command", choices=["channels", "send"])
    ap.add_argument("--phone", default="")
    ap.add_argument("--text", default="")
    ap.add_argument("--template", choices=list(TEMPLATES), default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--child", default="ребёнка")
    ap.add_argument("--group", default="")
    ap.add_argument("--time", default="")
    ap.add_argument("--all", action="store_true", help="во все мессенджеры сразу")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--send", action="store_true", help="реальная отправка")
    args = ap.parse_args()

    if args.command == "channels":
        for c in channels():
            print(f"{c['transport']:10s} {c.get('plainId','')} {c['channelId']}")
        return

    text = args.text or TEMPLATES.get(args.template, "").format(
        name=args.name or "родитель", child=args.child,
        group=args.group, time=args.time)
    mode = "broadcast" if args.all else "cascade"
    for line in send(args.phone, text, mode=mode, dry_run=not args.send):
        print(line)


if __name__ == "__main__":
    main()

# --- кто стоит за идентификатором чата --------------------------------------

_CONTACTS_CACHE = f"{os.environ.get('KIDSUP_SCRATCH') or '/tmp/kidsup-calls'}/wz_contacts.json"


def contacts(refresh: bool = False) -> dict:
    """chatId → {name, uid, type}. Ключ к тому, кто пишет из Telegram и MAX.

    У таких клиентов номер не передаётся — приходит внутренний id аккаунта,
    и задача выглядела как «+5113895858»: набрать нельзя, найти человека
    тоже. А Wazzup знает связь: в его контактах у каждого лежит id карточки
    МойКласс. Значит по id чата восстанавливается и имя, и карточка.

    Кэш на сутки: список меняется медленно, а тянуть 5800 контактов
    на каждую задачу незачем."""
    if not refresh:
        try:
            d = json.load(open(_CONTACTS_CACHE))
            if d.get("день") == date.today().isoformat():
                return d.get("map") or {}
        except Exception:
            pass
    out, off = {}, 0
    try:
        while off < 20000:
            r = httpx.get(f"{API}/contacts", headers=_headers(), timeout=40,
                          params={"limit": 100, "offset": off})
            rows = (r.json().get("data") if r.status_code == 200 else None) or []
            if not rows:
                break
            for c in rows:
                uid = c.get("id")
                name = c.get("name") or ""
                for cd in (c.get("contactData") or []):
                    cid = str(cd.get("chatId") or "")
                    if cid:
                        out[cid] = {"name": name, "uid": uid,
                                    "type": cd.get("chatType") or ""}
            off += 100
    except Exception as e:  # noqa: BLE001
        log.warning("wazzup.contacts: %s", type(e).__name__)
        return out
    try:
        json.dump({"день": date.today().isoformat(), "map": out},
                  open(_CONTACTS_CACHE, "w"), ensure_ascii=False)
    except Exception:
        pass
    return out


def who_is(chat_id: str) -> dict | None:
    """Контакт по идентификатору чата. Хвост id тоже подойдёт: в задачах
    он сохранён обрезанным."""
    cid = str(chat_id or "").strip()
    if not cid:
        return None
    m = contacts()
    if cid in m:
        return m[cid]
    tail = cid[-6:]
    hits = [v for k, v in m.items() if k.endswith(tail)]
    return hits[0] if len(hits) == 1 else None

# --- выбор канала: где человеку уже удобно ----------------------------------

# Порядок предпочтения. Первыми — бесплатные каналы, где человек уже пишет
# сам; WABA последним: там каждое сообщение платное, и это единственный
# канал, где можно написать первым по номеру телефона.
# Тип контакта и название транспорта — разные слова для одного канала:
# в контактах лежит «telegram», а канал в списке называется «tgapi».
# Без сопоставления выбор канала молча не находил ничего.
BY_CONTACT_TYPE = {
    "telegram": ["tgapi"],
    "max": ["max"],
    "vk": ["vk"],
    "instagram": ["instagram"],
    "whatsapp": ["whatsapp", "wapi"],
}
# Порядок предпочтения по типу контакта: сначала бесплатные каналы,
# где человек пишет сам; WABA последним — там каждое сообщение платное.
PREFERRED = ["telegram", "max", "vk", "instagram", "whatsapp"]


_LIVE_CACHE: dict = {}
_INDEX_CACHE: dict = {}


def _contact_index() -> tuple[dict, dict]:
    """Указатели «кто где пишет»: по id карточки и по телефону.

    Раньше выбор канала перебирал все 5800 контактов для КАЖДОГО получателя.
    На рассылке в 540 человек это три миллиона сравнений — минуты ожидания
    на ровном месте. Указатель строится один раз за прогон."""
    if not _INDEX_CACHE.get("ready"):
        by_uid: dict = {}
        by_phone: dict = {}
        for cid, info in contacts().items():
            t = info.get("type") or ""
            u = str(info.get("uid") or "")
            if u:
                by_uid.setdefault(u, []).append(t)
            tail = "".join(c for c in cid if c.isdigit())[-10:]
            if len(tail) == 10:
                by_phone.setdefault(tail, []).append(t)
        _INDEX_CACHE.update(ready=True, uid=by_uid, phone=by_phone)
    return _INDEX_CACHE["uid"], _INDEX_CACHE["phone"]


def _live_transports() -> set:
    """Какие каналы сейчас живые. Кэш на процесс: без него выбор канала
    для каждого получателя заново дёргает список каналов, и рассылка
    на четыреста человек превращается в четыреста лишних запросов."""
    if not _LIVE_CACHE.get("set"):
        try:
            _LIVE_CACHE["set"] = {c.get("transport") for c in all_channels()
                                  if c.get("state") == "active"}
        except Exception:
            return set()
    return _LIVE_CACHE["set"]


def best_channel(phone: str = "", uid: str | int | None = None,
                 mass: bool = False) -> str | None:
    """В каком канале писать этому человеку. Правило владельца от 22.08.

    Первым делом — MAX или Telegram, если там уже есть переписка: это
    бесплатно и там, где семье удобно. Если переписки нет, канал зависит
    от того, что мы шлём:
      · массовое (mass=True) — только WABA 3507. Это официальный канал
        Meta, у него нет риска бана и есть суточный лимит в тысячи;
      · разовое (mass=False) — обычный WhatsApp 0077, канал переписки.

    Разделение принципиальное: 0077 живёт под угрозой бана и на массовом
    потоке отваливается (22.08 так и вышло — номер ушёл в «не авторизован»),
    а WABA вне 24-часового окна пропускает только утверждённые шаблоны и
    для живого разового ответа не годится."""
    digits = "".join(c for c in str(phone or "") if c.isdigit())[-10:]
    by_uid, by_phone = _contact_index()
    found = list(by_uid.get(str(uid), [])) if uid is not None else []
    if not found and digits:
        found = list(by_phone.get(digits, []))
    found += _marked_in_crm(digits)
    live = _live_transports()
    # ступень 1: переписка в MAX или Telegram
    for kind in ("telegram", "max"):
        if kind not in found:
            continue
        for transport in BY_CONTACT_TYPE.get(kind, [kind]):
            if transport in live:
                return transport
    # ступень 2: WhatsApp — какой именно, решает тип отправки
    return "wapi" if mass else "whatsapp"


def _marked_in_crm(digits: str) -> list[str]:
    """Канал по пометке в имени карточки: «(MAX)», «писать в телеграмм».

    Указатель контактов Wazzup знает только тех, кто писал нам через него:
    из 5618 контактов там 114 MAX и 220 Telegram, тогда как в CRM таких
    пометок 83 и 112 — и совпадают они не полностью. Администраторы ставят
    пометку руками именно там, где человек просил писать ему в этот
    мессенджер, так что это второй равноправный источник, а не догадка."""
    if not digits:
        return []
    try:
        from . import db as _db
        with _db.get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM users WHERE substr("
                "replace(replace(replace(phone,' ',''),'-',''),'+',''), -10)=? LIMIT 1",
                (digits,)).fetchone()
    except Exception:
        return []
    name = (row[0] if row else "") or ""
    out = []
    low = name.lower()
    if "max" in low or "(мах)" in low:
        out.append("max")
    if "телеграм" in low or "telegram" in low:
        out.append("telegram")
    return out


# Номера WhatsApp под каждую задачу. Массовое — только WABA, разовое —
# только канал переписки: смешивать нельзя, у них разные лимиты и разная
# цена ошибки.
WABA_SENDER = "79199683507"
CHAT_SENDER = "79165610077"


def send_smart(phone: str, text: str, uid: str | int | None = None,
               dry_run: bool = True, mass: bool = False) -> list[str]:
    """Отправить туда, где человеку удобно, по правилу best_channel."""
    t = best_channel(phone, uid, mass=mass) or ("wapi" if mass else "whatsapp")
    sender = WABA_SENDER if t == "wapi" else (CHAT_SENDER if t == "whatsapp" else None)
    ok = send_via(t, phone, text, dry_run=dry_run, sender=sender)
    return [f"{t}({sender or '—'}) → {phone}: {'ok' if ok else 'fail'}"]

