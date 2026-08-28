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
# Порядок отправителей МАССОВЫХ рассылок и напоминаний: первым 0918.
WHATSAPP_PREFERRED = "79160170918,79199683507"

TEMPLATES = {
    "nedozvon": (
        "Здравствуйте, {name}! Это KidsUP (Бульвар Рокоссовского) 🎈\n"
        "Звонили вам сегодня, но не дозвонились. Мы набираем группы на новый "
        "учебный год, и для {child} есть место в группе «{group}».\n"
        "До 31 августа действует цена прошлого года. Удобно будет созвониться "
        "сегодня или завтра? Или напишите здесь — всё расскажем 😊"
    ),
    "probnoe_reminder": (
        "{name}, напоминаем: завтра в {time} у {child} пробное занятие "
        "«{group}» в KidsUP (б-р Маршала Рокоссовского, 6к1В). Ждём вас! Если планы "
        "изменились — просто напишите сюда, подберём другое время."
    ),
    "rannyaya_cena": (
        "{name}, здравствуйте! Это KidsUP. Для своих — ранняя цена на "
        "2026/27 учебный год действует до 31 августа + диагностика в подарок. "
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


# --- единый предохранитель отправки ---------------------------------------
# Все проверки живут здесь, а не в вызывающем коде. 25.08 выяснилось,
# почему это принципиально: окно «9:00-20:00» и защита «клиент ждёт ответа»
# стояли в обёртке autopilot._wa, а половина сценариев — рассылка, автоответ,
# догоны, лагерная почта — звали send_via напрямую и проходили мимо. Один
# клиент получил за час десяток одинаковых писем сразу в трёх каналах
# и позвонил жаловаться. Ниже — тот самый заслон, который нельзя обойти.

# Виды, которым лимит на сутки не писан: ответ живому человеку в диалоге
# и служебные сообщения владельцу. Всё остальное — автоматика, и её
# количество на одного человека ограничено.
FREE_KINDS = {"reply", "digest", "owner", "apology"}
DAY_LIMIT = 2            # автосообщений одному человеку в сутки
# Сервисные виды: подтверждение, напоминание о пробном, догон недозвона.
# Всё остальное — реклама, и её человеку положено не больше ОДНОЙ в день:
# два разных конвейера (nabor и akciya) законно укладывались в общий
# лимит 2 и вдвоём засыпали семью предложениями.
SERVICE_KINDS = {"confirm", "trial_reminder", "reschedule", "missed",
                 "booking", "sms"}
MARKETING_DAY_LIMIT = 1
HOUR_FROM, HOUR_TO = 9, 20

# Приглашения с датой живут ровно до этой даты. Текст кампании готовится
# заранее и лежит в очередях, черновиках и списках сразу в нескольких
# модулях — уследить за всеми путями отправки не выходит. 26.08 маме
# Сутулова ушло приглашение записаться на смену 24–28 августа, шедшую
# третий день. Поэтому срок годности проверяется здесь, у самой двери:
# что бы ни поставило письмо в очередь, просроченное наружу не выйдет.
EXPIRED = (
    ("24–28 августа", "2026-08-24"),   # последняя смена лагеря
    ("24-28 августа", "2026-08-24"),
    ("ШОУ-БИЗНЕС: В ПОГОНЕ ЗА ПРОДЮСЕРОМ", "2026-08-24"),
    # Приглашение на праздник и день открытых дверей: 29 и 30 августа.
    # 31-го оба уже прошли, и звать на них — то же самое, что звать на
    # позавчерашнюю смену лагеря. Неделя открытых уроков идёт до 6.09,
    # поэтому по ней отдельного срока нет.
    ("праздник начала учебного года", "2026-08-31"),
    ("праздник открытия сезона", "2026-08-31"),
    ("день открытых дверей", "2026-08-31"),
    # акция «сентябрь по старой цене»: письма заморожены в очереди с
    # текстом «если оплатить до 31 августа» — 1 сентября они превращаются
    # в обман, каким бы путём ни дошли до отправки
    ("оплатить до 31 августа", "2026-09-01"),
    ("цены прошлого года", "2026-09-01"),
    ("цене прошлого учебного года", "2026-09-01"),
    ("цена прошлого учебного года", "2026-09-01"),
)


def _msk() -> "datetime.datetime":
    import datetime as _dt
    return _dt.datetime.utcnow() + _dt.timedelta(hours=3)


def _guard_tables(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS wazzup_guard (
        phone TEXT, day TEXT, kind TEXT, digest TEXT, ts TEXT,
        transport TEXT DEFAULT '')""")
    try:
        conn.execute("ALTER TABLE wazzup_guard ADD COLUMN transport TEXT DEFAULT ''")
    except Exception:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS wg_phone_day "
                 "ON wazzup_guard (phone, day)")


def guard(phone: str, text: str, kind: str = "",
          transport: str = "") -> str | None:
    """None — можно слать. Строка — причина, по которой отправка отменена.

    Проверки по порядку дешевизны: стоп-кран, часы, суточный лимит,
    повтор того же текста."""
    if db.get_setting("messages_off", "0") == "1":
        return "стоп-кран messages_off включён"
    owner = "".join(c for c in str(db.get_setting("digest_phone", "") or "")
                    if c.isdigit())[-10:]
    p = _msisdn(phone)[-10:]
    if p and p == owner:
        return None                      # владельцу пишем всегда
    # Без телефона предохранитель слеп: стоп-лист отказов, лимиты и
    # антидубль считаются по номеру. Ответ в открытый диалог (reply)
    # пропускаем — там адресат определён самим диалогом; инициирующее
    # сообщение «в никуда» не выпускаем.
    if not p and kind not in FREE_KINDS:
        return "нет телефона — предохранитель не может проверить адресата"
    # Письменный отказ важнее любого сценария. 26.08 семья Муралевых
    # получила подтверждение записи через два дня после просьбы снять
    # бронь: отказ жил только текстом в чате мессенджера, а рассылки
    # ходят по телефонам из CRM и того чата не видели.
    if kind not in ("reply", "apology", "owner", "digest"):
        try:
            from . import otkaz
            why = otkaz.is_refused(phone)
            if why:
                return why
        except Exception:
            pass
    now = _msk()
    if kind not in ("reply", "owner"):
        today = now.date().isoformat()
        # без учёта регистра: в одной кампании «день открытых дверей»,
        # в другой «День открытых дверей» — маркер обязан ловить оба
        low = (text or "").lower()
        for marker, dead in EXPIRED:
            if marker.lower() in low and today >= dead:
                return f"приглашение просрочено с {dead}: «{marker}»"
    if not (HOUR_FROM <= now.hour < HOUR_TO):
        return f"сейчас {now.hour}:00 — вне окна {HOUR_FROM}-{HOUR_TO}"
    import hashlib
    dig = hashlib.sha1(text.strip().lower().encode()).hexdigest()[:16]
    day = now.date().isoformat()
    with db.get_conn() as conn:
        _guard_tables(conn)
        # тот же текст тому же человеку за последнюю неделю — это дубль,
        # чем бы он ни был вызван: повтором сценария, сбоем сохранения
        # реестра или двумя разными сценариями с одинаковым текстом
        week = (now - __import__("datetime").timedelta(days=7)).isoformat()
        # Дубль считается ПО КАНАЛУ: одно и то же сообщение штатно уходит
        # и в WhatsApp, и в мессенджеры — это правило владельца, а не сбой.
        # Сбой — это когда один и тот же текст летит в один канал дважды.
        same = conn.execute("SELECT ts FROM wazzup_guard WHERE phone=? AND "
                            "digest=? AND COALESCE(transport,'')=? AND ts>=? "
                            "LIMIT 1", (p, dig, transport, week)).fetchone()
        if same:
            return f"тот же текст уже уходил в {transport} {str(same[0])[5:16]}"
        if kind not in FREE_KINDS:
            # считаем РАЗНЫЕ сообщения за сутки, а не отправки: письмо
            # в три канала — это одно сообщение, а не три
            n = conn.execute("SELECT COUNT(DISTINCT digest) FROM wazzup_guard "
                             "WHERE phone=? AND day=? AND kind NOT IN "
                             "('reply','digest','owner','apology')",
                             (p, day)).fetchone()[0]
            if n >= DAY_LIMIT:
                return f"за сегодня уже {n} автосообщения — лимит {DAY_LIMIT}"
            if kind not in SERVICE_KINDS:
                m = conn.execute(
                    "SELECT COUNT(DISTINCT digest) FROM wazzup_guard "
                    "WHERE phone=? AND day=? AND kind NOT IN "
                    "('reply','digest','owner','apology','confirm',"
                    "'trial_reminder','reschedule','missed','booking','sms')",
                    (p, day)).fetchone()[0]
                if m >= MARKETING_DAY_LIMIT:
                    return (f"рекламное сегодня уже уходило ({m}) — "
                            f"второй рекламы в день не бывает")
    return None


def guard_note(phone: str, text: str, kind: str = "",
               transport: str = "") -> None:
    """Записать факт отправки — по нему считаются лимит и дубли."""
    import hashlib
    now = _msk()
    p = _msisdn(phone)[-10:]
    dig = hashlib.sha1(text.strip().lower().encode()).hexdigest()[:16]
    try:
        with db.get_conn() as conn:
            _guard_tables(conn)
            conn.execute("INSERT INTO wazzup_guard (phone, day, kind, digest, "
                         "ts, transport) VALUES (?,?,?,?,?,?)",
                         (p, now.date().isoformat(), kind or "auto", dig,
                          now.isoformat(timespec="seconds"), transport))
    except Exception:
        logging.getLogger("kidsup.wazzup").warning("предохранитель не записал %s", p)


def _remember(resp, transport: str, phone: str, uid=None, kind: str = "") -> None:
    """Запомнить, какому человеку принадлежит отправленное сообщение.

    Wazzup присылает статусы доставки по messageId, но сам по себе он
    бесполезен: известно, что «сообщение X не доставлено», и неизвестно,
    кому. 25.08 из-за этого 77 писем сутки висели недоставленными, и
    заметили это администраторы глазами. Здесь мы связываем id с телефоном
    и карточкой — тогда недоставленное можно догнать СМС и увидеть, что
    канал перестал работать, в тот же час."""
    try:
        mid = ((resp.json() or {}).get("messageId")
               or (resp.json() or {}).get("id") or "")
    except Exception:
        mid = ""
    if not mid:
        return
    import datetime as _dt
    try:
        with db.get_conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS wazzup_sent (
                message_id TEXT PRIMARY KEY, ts TEXT, phone TEXT, uid TEXT,
                transport TEXT, kind TEXT, chased INTEGER DEFAULT 0)""")
            conn.execute(
                "INSERT OR IGNORE INTO wazzup_sent "
                "(message_id, ts, phone, uid, transport, kind) VALUES (?,?,?,?,?,?)",
                (str(mid), (_dt.datetime.utcnow() + _dt.timedelta(hours=3))
                 .isoformat(timespec="seconds"), _msisdn(phone),
                 str(uid or ""), transport, kind))
    except Exception:
        logging.getLogger("kidsup.wazzup").warning("не записал отправку %s", mid)


def send(phone: str, text: str, mode: str = "cascade", dry_run: bool = True,
         transports: list[str] | None = None, kind: str = "") -> list[str]:
    """Отправка сообщения. transports ограничивает каналы (например ["tgapi"])."""
    phone = _msisdn(phone)
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
            stop = guard(phone, text, kind, transport)
            if stop:
                log.append(f"{transport} → {phone}: отменено ({stop})")
                continue
            r = httpx.post(f"{API}/message", headers=_headers(), json={
                "channelId": ch["channelId"], "chatType": CHAT_TYPE.get(transport, transport),
                "chatId": phone, "text": text,
            }, timeout=30)
            ok = r.status_code in (200, 201)
            if ok:
                _remember(r, transport, phone, None, kind)
                guard_note(phone, text, kind, transport)
            log.append(f"{transport} → {phone}: HTTP {r.status_code} {r.text[:120]}")
        if ok and mode == "cascade":
            break
    return log




def chat_id_for(transport: str, phone: str = "", uid: str | int | None = None) -> str:
    """Идентификатор чата для этого транспорта.

    У WhatsApp и MAX chatId — номер телефона, и слать по номеру можно.
    У Telegram — внутренний числовой id аккаунта (953893756), телефона там
    нет вовсе. 22.08 рассылка по лагерю ушла в Telegram с chatId=телефон
    и все 47 сообщений вернулись с ошибкой BAD_CONTACT: Wazzup принял их
    и не доставил. Это выглядело как «Telegram не даёт писать по базе»,
    хотя дело было в неверном идентификаторе."""
    digits = "".join(c for c in str(phone or "") if c.isdigit())[-10:]
    kind = {"tgapi": "telegram", "telegram": "telegram",
            "max": "max"}.get(transport)
    if not kind:
        return digits and ("7" + digits) or str(phone)
    # У Telegram телефона нет вовсе, у MAX он тоже не всегда совпадает
    # с chatId: у части контактов там внутренний номер аккаунта (13009918).
    # Поэтому идентификатор берём из указателя по карточке МойКласс.
    if uid is not None:
        got = _msgr_index(kind).get(str(uid))
        if got:
            return got
    # Телеграму телефон не подходит принципиально, MAX его иногда принимает
    return "" if kind == "telegram" else (digits and ("7" + digits) or "")


_MSGR_INDEX: dict = {}


def _msgr_index(kind: str) -> dict[str, str]:
    """uid карточки → chatId в этом мессенджере. Указатель, а не перебор.

    Контактов 5618; линейный поиск по каждому адресату превращает рассылку
    на полсотни человек в четверть миллиона сравнений и не укладывается
    в таймаут."""
    if kind not in _MSGR_INDEX:
        idx: dict[str, str] = {}
        for cid, info in (contacts() or {}).items():
            if info.get("type") == kind and info.get("uid"):
                idx[str(info["uid"])] = cid
        _MSGR_INDEX[kind] = idx
    return _MSGR_INDEX[kind]


def _tg_index() -> dict[str, str]:
    """Оставлено для вызовов, которые ищут именно телеграм-чаты."""
    return _msgr_index("telegram")


def send_via(transport: str, phone: str, text: str, dry_run: bool = True,
             sender: str | None = None, template_id: str | None = None,
             template_values: list | None = None,
             uid: str | int | None = None, kind: str = "") -> bool:
    """Отправка строго через один канал. sender — plainId конкретного номера
    (для ротации WhatsApp). True = принял к доставке.

    Для WABA вне 24-часового окна Meta пропускает ТОЛЬКО утверждённый
    шаблон: произвольный текст Wazzup принимает и отвечает 201, но до
    получателя он не доходит и в статистике Meta не появляется вовсе
    (22.08: 24 «отправленных» сообщения — ноль в WhatsApp Manager).
    Поэтому у wapi при заданном waba_template_id уходит templateId,
    а текст идёт значениями подстановки."""
    phone = _msisdn(phone)
    chans = channels()
    if sender:
        chans = [c for c in chans if c.get("plainId") == sender] or chans
    ch = _pick(chans, transport)
    if not ch:
        return False
    # Запрет по НОМЕРУ, а не по транспорту. 23.08 владелец вывел из работы
    # номер 0077, я убрал его из wa_senders — но эта настройка относится
    # только к WhatsApp, а на том же номере висят Telegram и MAX. В итоге
    # 42 сообщения ушли ровно через тот аккаунт, который выводили. Настройка
    # blocked_senders режет любой транспорт, привязанный к номеру.
    # Формат записи: «номер» закрывает все каналы аккаунта, «номер:транспорт» —
    # только один. Второе нужно потому, что на 0077 висят сразу три канала:
    # WhatsApp там отвалился и выведен из работы, а Telegram и MAX живы
    # и по правилу владельца остаются рабочими.
    banned = {x.strip() for x in
              (db.get_setting("blocked_senders", "") or "").split(",") if x.strip()}
    num = str(ch.get("plainId") or "")
    if num in banned or f"{num}:{transport}" in banned:
        logging.getLogger("kidsup.wazzup").warning(
            "канал %s (%s) закрыт настройкой blocked_senders — отправка отменена",
            ch.get("plainId"), transport)
        return False
    if dry_run:
        return True
    stop = guard(phone, text, kind, transport)
    if stop:
        logging.getLogger("kidsup.wazzup").info(
            "предохранитель: %s → %s (%s)", phone[-4:], stop, kind or "auto")
        # None, а не False: вызывающему важно отличать «канал не смог»
        # (можно пробовать другой канал или позже) от «предохранитель
        # запретил» (ретраи бессмысленны — причина не в канале)
        return None
    if transport == "wapi" and template_id is None:
        template_id = db.get_setting("waba_template_id", "") or None
    if transport == "wapi" and not template_id:
        # Без шаблона WABA-отправка бессмысленна: Wazzup ответит 201, строка
        # пометится доставленной, а адресат не получит ничего и второй раз
        # ему уже не напишут. Лучше честный отказ — он вернёт строку в очередь.
        logging.getLogger("kidsup.wazzup").warning(
            "wapi: не задан waba_template_id — отправка отменена")
        return False
    if transport == "wapi" and template_id:
        body = {"channelId": ch["channelId"], "chatType": "whatsapp",
                "chatId": phone, "templateId": template_id}
        if template_values is not None:
            body["templateValues"] = template_values
        r = httpx.post(f"{API}/message", headers=_headers(), json=body, timeout=30)
        if r.status_code not in (200, 201):
            logging.getLogger("kidsup.wazzup").warning(
                "wazzup шаблон отклонён: %s %s", r.status_code, r.text[:200])
        else:
            _remember(r, transport, phone, uid, kind)
            guard_note(phone, text, kind, transport)
        return r.status_code in (200, 201)
    # MAX сюда добавлен 23.08: у части контактов там внутренний номер
    # аккаунта, и отправка по телефону возвращает CHANNEL_MAX_PHONE_NOT_OCCUPIED
    chat_id = chat_id_for(transport, phone, uid) \
        if transport in ("tgapi", "telegram", "max") else phone
    if not chat_id:
        logging.getLogger("kidsup.wazzup").warning(
            "%s: не нашёл chatId (uid=%s) — отправка отменена", transport, uid)
        return False
    r = httpx.post(f"{API}/message", headers=_headers(), json={
        "channelId": ch["channelId"], "chatType": CHAT_TYPE.get(transport, transport),
        "chatId": chat_id, "text": text,
    }, timeout=30)
    if r.status_code not in (200, 201):
        logging.getLogger("kidsup.wazzup").warning(
            "wazzup %s → %s: HTTP %s %s", transport, chat_id, r.status_code, r.text[:160])
    else:
        _remember(r, transport, phone, uid, kind)
        guard_note(phone, text, kind, transport)
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


def _msisdn(raw: str) -> str:
    """Номер в том виде, в каком его понимает WhatsApp: 11 цифр с семёркой.

    25.08: все 77 сообщений за сутки повисли с ошибкой доставки, и это
    выглядело как бан номера — на деле chatId уходил десятизначным
    («9013412303»), потому что вызывающий код повсюду режет телефон до
    последних десяти цифр. Такого абонента WhatsApp не находит и молча
    роняет сообщение. Нормализуем в одном месте, а не в каждом вызове.

    Telegram сюда не попадает: там chatId — внутренний id аккаунта,
    его берёт chat_id_for."""
    d = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(d) == 11 and d[0] == "8":
        d = "7" + d[1:]
    elif len(d) == 10 and d[0] == "9":
        d = "7" + d
    return d


def channels_for(phone: str = "", uid: str | int | None = None,
                 mass: bool = False) -> list[str]:
    """Все каналы, куда идёт это сообщение. Правило владельца от 23.08.

    Общее для обоих случаев: смотрим, где с семьёй УЖЕ есть переписка.
    Есть и в MAX, и в Telegram — пишем в оба, а не выбираем один: человек
    читает тот мессенджер, который открыл, и угадывать за него незачем.

    Дальше пути расходятся, и это принципиально:

    · РАЗОВОЕ сообщение (mass=False) — ответ конкретному человеку. К мессенджерам
      ВСЕГДА добавляется WhatsApp, даже если переписка уже нашлась: разовых
      немного, и лучше продублировать, чем не достучаться. Номер берётся
      из настройки chat_whatsapp — 0077 как канал переписки, а пока он выведен
      из работы, туда прописан 0918.

    · РАССЫЛКА (mass=True) — WhatsApp только через WABA 3507 и только
      утверждённым шаблоном. Обычный номер на массовом потоке отваливается:
      22.08 через 0077 ушла рассылка по лагерю, и он ушёл в «не авторизован».
      WABA добавляется даже при живой переписке в мессенджере: решение
      владельца от 25.08 — «WhatsApp/WABA всегда, мессенджеры сверху».
      Раньше здесь была экономия (есть MAX — WABA не шлём), она отменена.
    """
    digits = "".join(c for c in str(phone or "") if c.isdigit())[-10:]
    by_uid, by_phone = _contact_index()
    # Считается только НАСТОЯЩАЯ переписка. Указатель контактов Wazzup
    # содержит того, с кем диалог уже был: без диалога chatId не существует.
    # Пометку в имени карточки («(MAX)», «писать в телеграмм») сюда НЕ
    # берём — это пожелание администратора, а не факт переписки: у семьи
    # может стоять пометка, а диалога с нами в этом мессенджере нет,
    # и сообщение уйдёт в пустоту.
    found = list(by_uid.get(str(uid), [])) if uid is not None else []
    if not found and digits:
        found = list(by_phone.get(digits, []))
    live = _live_transports()

    out: list[str] = []
    for kind in ("max", "telegram"):
        if kind not in found:
            continue
        for transport in BY_CONTACT_TYPE.get(kind, [kind]):
            if transport in live and transport not in out:
                out.append(transport)
    # WhatsApp идёт ВСЕГДА и не требует переписки — в отличие от мессенджеров,
    # куда без диалога писать нельзя. Разовому это обычный номер, рассылке —
    # WABA с утверждённым шаблоном. Даже если сообщение уже ушло в MAX или
    # Telegram, WhatsApp добавляется: решение владельца от 23.08, человек
    # читает то, что открыл, и дубль здесь дешевле молчания.
    out.append("wapi" if mass else "whatsapp")
    return out


def best_channel(phone: str = "", uid: str | int | None = None,
                 mass: bool = False) -> str | None:
    """Первый канал из channels_for — для мест, где нужен ровно один."""
    got = channels_for(phone, uid, mass=mass)
    return got[0] if got else None


def templates() -> list[dict]:
    """Шаблоны WABA, заведённые в кабинете. API даёт только чтение —
    создать и отправить на модерацию можно лишь руками в кабинете
    Wazzup, POST на этот адрес возвращает 404.

    Поля приводим к общему виду: Wazzup зовёт идентификатор
    templateGuid, а имя — title, и код, искавший id/name, молча получал
    пустоту даже по одобренному шаблону."""
    r = httpx.get(f"{API}/templates/whatsapp", headers=_headers(), timeout=30)
    r.raise_for_status()
    d = r.json()
    raw = d if isinstance(d, list) else (d.get("data") or d.get("templates") or [])
    out = []
    for t in raw:
        text = ""
        for c in (t.get("components") or []):
            if c.get("text"):
                text = c["text"]
                break
        out.append({**t,
                    "id": t.get("templateGuid") or t.get("id") or t.get("templateId"),
                    "name": t.get("title") or t.get("name") or "",
                    "text": text})
    return out


def approved_template(prefer: str = "") -> dict | None:
    """Первый одобренный шаблон; prefer — часть имени для выбора нужного.

    Модерация Meta идёт от нескольких минут до суток, и ждать её вручную
    незачем: как только шаблон одобрен, его id можно подставить и
    возобновить рассылку без участия человека."""
    ok = [t for t in templates()
          if str(t.get("status") or "").lower() in {"approved", "active", "одобрен"}]
    if prefer:
        named = [t for t in ok if prefer.lower() in str(t.get("name") or "").lower()]
        if named:
            return named[0]
    return ok[0] if ok else None


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
# Запасной номер переписки. 0077 с 23.08 не авторизован (просит QR) и
# 24.08 успел молча съесть WhatsApp-ветку сообщения: локальная база после
# отката контейнера потеряла настройку chat_whatsapp и упала на этот
# умолчальный номер. Умолчание должно быть живым.
# Номер переписки (входящие + разовые ответы) — решение владельца 28.08:
# 0077 принимает входящие, 0918 остаётся под рассылки и напоминания.
CHAT_SENDER = "79165610077"


def send_smart(phone: str, text: str, uid: str | int | None = None,
               dry_run: bool = True, mass: bool = False,
               template_values: list | None = None, kind: str = "") -> list[str]:
    """Отправить по всем каналам, которые положены этому адресату.

    Разовому сообщению каналов может быть несколько — MAX, Telegram
    и обязательный WhatsApp; рассылке обычно один. Возвращает по строке
    на канал, чтобы в журнале было видно каждую попытку отдельно."""
    log = []
    for t in channels_for(phone, uid, mass=mass):
        sender = None
        if t == "wapi":
            sender = WABA_SENDER
        elif t == "whatsapp":
            # Канал переписки задаётся настройкой: пока 0077 выведен
            # из работы, разовые уходят с резервного номера.
            sender = db.get_setting("chat_whatsapp", CHAT_SENDER) or CHAT_SENDER
        ok = send_via(t, phone, text, dry_run=dry_run, sender=sender, uid=uid,
                      template_values=template_values, kind=kind)
        log.append(f"{t}({sender or '—'}) → {phone}: {'ok' if ok else 'fail'}")
    return log or [f"— → {phone}: каналов нет"]

