"""Публикация макетов в соцсети по кнопке с /makety.

Маша выбирает макет, пишет подпись — картинка уходит в выбранные каналы.
Что автоматизируется честно:
  • ВК (пост на стену сообщества) — официальный API, токен сообщества
  • Телеграм-канал — Bot API, бот должен быть админом канала
Instagram и статусы WhatsApp API не дают — для них кнопка «скачать» и руки.
Max добавим, когда появится токен их Bot API.
  • С 17.10.2026 — «все сети» через Hooppy: один пост во все страницы набора
    (ВК, Телеграм-канал, Instagram, Max — что подключено в кабинете Hooppy)

Настройки (страница /settings): vk_token, vk_group_id (число без минуса),
tg_bot_token, tg_channel (@имя_канала или -100…id), hooppy_nabor (id набора);
ключ Hooppy — hooppy_token на странице /owner.
"""

from __future__ import annotations

import json

import httpx

from . import db

VK_API = "https://api.vk.com/method"
VK_V = "5.199"


def _vk(method: str, token: str, **params):
    r = httpx.post(f"{VK_API}/{method}", timeout=30,
                   data={"access_token": token, "v": VK_V, **params})
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"VK {method}: {data['error'].get('error_msg')}")
    return data["response"]


def publish_vk(image: bytes, caption: str) -> str:
    token = db.get_setting("vk_token")
    group = (db.get_setting("vk_group_id") or "").lstrip("-")
    if not token or not group:
        raise RuntimeError("не настроено: vk_token и vk_group_id в /settings")
    up = _vk("photos.getWallUploadServer", token, group_id=group)
    r = httpx.post(up["upload_url"], timeout=60,
                   files={"photo": ("maket.png", image, "image/png")}).json()
    if not r.get("photo") or r["photo"] == "[]":
        raise RuntimeError("VK upload: пустой ответ сервера загрузки")
    saved = _vk("photos.saveWallPhoto", token, group_id=group,
                photo=r["photo"], server=r["server"], hash=r["hash"])[0]
    post = _vk("wall.post", token, owner_id=f"-{group}", from_group=1,
               message=caption, attachments=f"photo{saved['owner_id']}_{saved['id']}")
    return f"vk.com/wall-{group}_{post['post_id']}"


def publish_tg(image: bytes, caption: str) -> str:
    token = db.get_setting("tg_bot_token")
    chat = db.get_setting("tg_channel")
    if not token or not chat:
        raise RuntimeError("не настроено: tg_bot_token и tg_channel в /settings")
    r = httpx.post(f"https://api.telegram.org/bot{token}/sendPhoto", timeout=60,
                   data={"chat_id": chat, "caption": caption[:1024]},
                   files={"photo": ("maket.png", image, "image/png")}).json()
    if not r.get("ok"):
        raise RuntimeError(f"Telegram: {r.get('description')}")
    return f"пост №{r['result']['message_id']} в {chat}"


def publish_hooppy(image: bytes, caption: str) -> str:
    """17.10.2026: все сети разом через Hooppy (app/hooppy.py). Публикуем только в
    набор страниц hooppy_nabor, а не во всё подключённое: в кабинете могут быть
    WhatsApp и личные аккаунты Telegram, куда пост с макетом уходить не должен."""
    from . import hooppy
    nabor = (db.get_setting("hooppy_nabor") or "").strip()
    if not nabor.isdigit():
        raise RuntimeError("не настроено: hooppy_nabor (id набора страниц в Hooppy) в /settings")
    foto = hooppy.zagruzit_foto(image, "maket.png")
    pid = hooppy.post(caption, foto=[foto], nabor_id=int(nabor))
    return f"пост №{pid} принят, Hooppy публикует его в страницы набора; ошибки соцсетей — в его журнале"


CHANNELS = {"vk": publish_vk, "tg": publish_tg, "hooppy": publish_hooppy}


def publish(image: bytes, caption: str, channels: list[str]) -> dict:
    out = {}
    for ch in channels:
        fn = CHANNELS.get(ch)
        if not fn:
            out[ch] = {"ok": False, "msg": "неизвестный канал"}
            continue
        try:
            out[ch] = {"ok": True, "msg": fn(image, caption)}
        except Exception as e:  # noqa: BLE001
            out[ch] = {"ok": False, "msg": str(e)[:200]}
    return out
