"""Hooppy — автопостинг в ВК, Telegram, Max и ОК через один API.

17.10.2026, Борис: «давай hooppy.ru настроим». Сервис, а не прямые API соцсетей:
право wall на стену сообщества ВК выдают «в исключительных случаях» (исследование
29.09, docs/rabota/posty/servisy_avtopostinga.md), а Max и ОК без сервиса нам
недоступны. Hooppy публикует в страницы, подключённые в его кабинете.

Ключ — настройка hooppy_token (страница /owner), тот же, что в кабинете Hooppy
(Настройки → API). Документация: hooppy.ru/api-docs, база https://api.hooppy.ru/api.
Здесь только нужное нам: страницы, наборы, расписания, загрузка картинки, пост,
журнал ошибок публикаций.
"""
from __future__ import annotations

import html
import secrets
from datetime import datetime

import httpx

from . import db

BASE = "https://api.hooppy.ru/api"
# Номера соцсетей в Hooppy (source_id); остальные приходят в ответе /accounts/pages.
VK, OK, IG_COOKIE, TG, IG, WA = 1, 2, 7, 9, 10, 22
# Где Hooppy умеет истории (из его кабинета, 17.10.2026). Telegram — только от
# аккаунта с Telegram Premium (в канал — ещё и бусты), WhatsApp — только через Wappi.pro.
ISTORII_SETI = (VK, OK, IG_COOKIE, TG, IG, WA)


class HooppyError(RuntimeError):
    pass


def _token() -> str:
    t = (db.get_setting("hooppy_token") or "").strip()
    if not t:
        raise HooppyError("не настроено: hooppy_token на странице /owner")
    return t


def _req(method: str, path: str, *, json=None, params=None, files=None, data=None, timeout: float = 60):
    try:
        r = httpx.request(method, BASE + path, json=json, params=params, files=files, data=data, timeout=timeout,
                          headers={"Authorization": f"Bearer {_token()}", "Accept": "application/json"})
    except httpx.HTTPError as e:
        raise HooppyError(f"Hooppy {method} {path}: нет связи ({type(e).__name__})") from e
    try:
        body = r.json()
    except ValueError:
        body = {"message": r.text[:300]}
    if r.status_code >= 400:
        msg = body.get("message") if isinstance(body, dict) else str(body)[:300]
        raise HooppyError(f"Hooppy {method} {path}: {r.status_code} {msg}")
    return body


def stranicy(source_id: int | None = None) -> list[dict]:
    """Подключённые страницы: [{id, set, name, link}]; id идёт в пост."""
    out, page = [], 1
    while page <= 20:
        params = {"page": page, **({"source_id": source_id} if source_id else {})}
        b = _req("GET", "/accounts/pages", params=params)
        out += [{"id": x.get("id"), "set": x.get("source_id"), "name": x.get("social_page_name"),
                 "link": x.get("social_page_link")} for x in (b.get("list") or [])]
        if not b.get("is_has_more"):
            break
        page += 1
    return out


def nabory() -> list[dict]:
    return _req("GET", "/posts/projects/json").get("projects") or []


def raspisaniya() -> list[dict]:
    return _req("GET", "/posts/schedules/json").get("schedules") or []


_SPRAVOCHNIKI = ("id", "social_pages_by_accounts", "social_albums_by_pages", "watermarks", "projects")


def _shablon(path: str) -> dict:
    """Шаблон нового набора или расписания (GET …/0/edit) без справочников. Поля
    обработки контента (подпись, теги, рерайт…) Hooppy читает каждое напрямую,
    поэтому передаём их все — со значениями по умолчанию из шаблона."""
    b = _req("GET", f"{path}/0/edit")
    return {k: v for k, v in b.items() if k not in _SPRAVOCHNIKI}


def _po_setyam(stranicy_ids: list[int]) -> dict[str, list[int]]:
    """{source_id: [id страниц]} — так Hooppy принимает выбор страниц в наборе."""
    ids = [int(x) for x in stranicy_ids]
    vse = {p["id"]: p["set"] for p in stranicy()}
    chuzhie = [i for i in ids if i not in vse]
    if not ids or chuzhie:
        raise HooppyError(f"страницы не подключены в Hooppy: {chuzhie or 'список пуст'}")
    out: dict[str, list[int]] = {}
    for i in ids:
        out.setdefault(str(vse[i]), []).append(i)
    return out


def sozdat_nabor(name: str, stranicy_ids: list[int]) -> int:
    """Набор страниц — куда уходит пост одной кнопкой. Возвращает id набора."""
    body = _shablon("/posts/projects")
    body.update(name=name, publication_where_type=1, selected_pages_by_source_ids=_po_setyam(stranicy_ids))
    return int(_req("POST", "/posts/projects", json=body).get("id") or 0)


def sozdat_raspisanie(name: str, vremena: list[list[str]], nabor_id: int) -> int:
    """Расписание — очередь постов по времени недели (vremena: 7 списков «ЧЧ:ММ»,
    с понедельника). Создаём ВСЕГДА остановленным (state 3): запускает владелец
    в кабинете Hooppy, когда посмотрит очередь."""
    if len(vremena) != 7:
        raise HooppyError("нужно 7 дней времени публикаций, с понедельника")
    times = []
    for den in vremena:
        sloty = []
        for t in den:
            h, _, m = str(t).partition(":")
            if not (h.isdigit() and m.isdigit() and int(h) < 24 and int(m) < 60):
                raise HooppyError(f"время «{t}» — нужно ЧЧ:ММ")
            sloty.append({"hours": f"{int(h):02d}", "minutes": f"{int(m):02d}"})
        times.append(sloty)
    body = _shablon("/posts/schedules")
    body.update(name=name, times=times, state=3, publication_how_type=2, publication_where_type=1,
                project_id=int(nabor_id))
    return int(_req("POST", "/posts/schedules", json=body).get("id") or 0)


def _ids(ids) -> str:
    out = [str(int(x)) for x in ids or []]
    if not out:
        raise HooppyError("пустой список id")
    return ",".join(out)


def udalit_posty(ids: list[int]) -> None:
    """Удалить посты из Hooppy (уже опубликованные в соцсетях записи остаются)."""
    _req("POST", "/posts/batch/delete", json={"ids": _ids(ids)})


def zapustit_raspisaniya(ids: list[int]) -> None:
    """Запустить остановленные расписания — только после «да» владельца."""
    _req("POST", "/posts/schedules/batch/start", json={"ids": _ids(ids)})


def ostanovit_raspisaniya(ids: list[int]) -> None:
    _req("POST", "/posts/schedules/batch/stop", json={"ids": _ids(ids)})


def oshibki(limit: int = 30) -> list[dict]:
    """Журнал ошибок публикаций, новые сверху: [{kogda, set, stranica, chto}]."""
    b = _req("GET", "/notifications", params={"page": 1, "limit": limit})
    return [{"kogda": x.get("operation_date"), "set": x.get("source_id"),
             "stranica": (x.get("page") or {}).get("social_page_name") or "",
             "chto": x.get("data") or "", "post": x.get("object_id")}
            for x in (b.get("list") or []) if x.get("is_error")]


def zagruzit_foto(image: bytes, name: str = "post.png") -> dict:
    """Картинка в хранилище Hooppy; объект из ответа вкладывается в пост."""
    tip = "image/jpeg" if name.lower().endswith((".jpg", ".jpeg")) else "image/png"
    b = _req("POST", "/files/media/upload", files={"file": (name, image, tip)},
             data={"file_id": secrets.token_hex(10)}, timeout=120)
    return b["photo"]


def zagruzit_po_ssylke(url: str) -> dict:
    """Картинка по прямой ссылке (JPEG, PNG, WebP) — Hooppy скачает её сам."""
    b = _req("POST", "/files/media/upload_from_url", json={"file_id": secrets.token_hex(10), "file_url": url},
             timeout=120)
    return b["photo"]


def _abzacy(text: str) -> str:
    """Hooppy принимает абзацы как <p>…</p>; пустая строка — граница абзаца."""
    parts = [p.strip() for p in str(text or "").replace("\r", "").split("\n\n") if p.strip()]
    return "".join("<p>" + html.escape(p, quote=False).replace("\n", "<br>") + "</p>" for p in parts)


def telo_posta(text: str, *, foto: list[dict] | None = None, stranicy_ids: list[int] | None = None,
               nabor_id: int | None = None, kogda: datetime | None = None,
               raspisanie_ids: list[int] | None = None, ssylka: str = "",
               nastrojki: dict | None = None) -> dict:
    """Тело POST /posts. Куда: страницы (stranicy_ids) или набор (nabor_id), либо в
    расписание (raspisanie_ids). Когда: сразу, в момент kogda (часовой пояс кабинета)
    или по расписанию. nastrojki — флаги публикации Hooppy как есть, например
    {"publish_as_story": 1, "publish_as_story_source_ids": "1,9"} — ещё и в истории."""
    vlozheniya = []
    if foto:
        vlozheniya.append({"type": "photos", "data": foto})
    if ssylka:
        vlozheniya.append({"type": "link", "data": ssylka})
    if not text and not vlozheniya:
        raise HooppyError("пустой пост: нет ни текста, ни вложений")
    if nastrojki:
        vlozheniya.append({"type": "settings", "data": dict(nastrojki)})
    body = {"texts": [{"text": _abzacy(text), "source_id": 0}] if text else [],
            "attachments": vlozheniya}
    if raspisanie_ids:
        body.update(publication_when_type=3, publication_how_type=1, schedules_ids=list(raspisanie_ids))
        return body
    if nabor_id:
        body.update(publication_how_type=2, project_id=int(nabor_id))
    elif stranicy_ids:
        body.update(publication_how_type=1, selected_pages_ids=[int(x) for x in stranicy_ids])
    else:
        raise HooppyError("не выбраны страницы: нужен набор или список страниц")
    if kogda:
        body.update(publication_when_type=2, publication_date={
            "date": kogda.strftime("%d.%m.%Y"), "hours": kogda.strftime("%H"), "minutes": kogda.strftime("%M")})
    else:
        body["publication_when_type"] = 1
    return body


def nastrojki_istorii() -> dict:
    """Флаги «опубликовать историей» во всех сетях, где Hooppy это умеет. В ленту
    историю не дублируем: пост для ленты — отдельный, в формате 4:5."""
    return {"publish_as_story": 1, "publish_as_story_source_ids": ",".join(map(str, ISTORII_SETI))}


def post(text: str, **kw) -> int:
    """Создать пост; публикация у Hooppy асинхронная. Возвращает id поста."""
    return int(_req("POST", "/posts", json=telo_posta(text, **kw)).get("id") or 0)
