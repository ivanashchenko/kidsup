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

import httpx

from . import db

API = "https://api.wazzup24.com/v3"

# приоритет транспортов для каскада (у Wazzup телеграм-канал зовётся tgapi);
# whatsapp-канал 79165610077 заблокирован — предпочитаем 79199683507
CASCADE = ["whatsapp", "tgapi", "max"]
CHAT_TYPE = {"whatsapp": "whatsapp", "tgapi": "telegram", "max": "max", "vk": "vk"}
WHATSAPP_PREFERRED = "79199683507"

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


def _pick(chans: list[dict], transport: str) -> dict | None:
    cand = [c for c in chans if c.get("transport") == transport]
    cand.sort(key=lambda c: c.get("plainId") != WHATSAPP_PREFERRED)
    return cand[0] if cand else None


def send(phone: str, text: str, mode: str = "cascade", dry_run: bool = True) -> list[str]:
    """Отправка сообщения. Возвращает журнал попыток по каналам."""
    phone = "".join(ch for ch in phone if ch.isdigit())
    if phone.startswith("8") and len(phone) == 11:
        phone = "7" + phone[1:]
    log, chans = [], channels()
    order = CASCADE if mode == "cascade" else CASCADE  # broadcast шлёт во все
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
