"""Страница видеонаблюдения для родителей: app.kidsup.ru/kamery.

Отдельный пароль, а не общий админский: его знают все сотрудники, и давать
его родителям нельзя. Пароль и список камер живут в настройках сервера
(cam_password, cam_embeds), поэтому меняются без выкладки кода.

cam_embeds — JSON-список [{"name": "Английский, кабинет 2", "src": "<ссылка
на плеер Ivideon>"}]. Ссылку даёт кабинет Ivideon: камера → «Поделиться» →
«Встроить на сайт», оттуда нужен адрес из атрибута src.
"""

from __future__ import annotations

import html as _html
import hmac
import json
import re

from . import db

COOKIE = "kam"


def _password() -> str:
    return (db.get_setting("cam_password") or "").strip()


def _cameras() -> list[dict]:
    try:
        raw = json.loads(db.get_setting("cam_embeds") or "[]")
    except Exception:  # noqa: BLE001
        return []
    out = []
    for x in raw if isinstance(raw, list) else []:
        src = str((x or {}).get("src") or "").strip()
        if src.startswith("https://"):
            out.append({"name": str(x.get("name") or "Камера"), "src": src})
    return out


def ok_token(token: str) -> bool:
    pwd = _password()
    return bool(pwd) and bool(token) and hmac.compare_digest(token, pwd)


HEAD = """<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Видеонаблюдение — KidsUP</title>
<link rel=preconnect href="https://fonts.googleapis.com">
<link rel=preconnect href="https://fonts.gstatic.com" crossorigin>
<link rel=stylesheet href="https://fonts.googleapis.com/css2?family=Rubik:wght@500;600;700&family=Inter:wght@400;500&display=swap">
<style>
:root{--indigo:#312783;--blue:#1DA7E0;--green:#7DB928;--ink:#221F3B;
      --muted:#6A6F87;--line:#E4E8F3;--soft:#F5F8FD;--r:16px}
*{box-sizing:border-box;margin:0}
body{background:#FBFCFE;color:var(--ink);
     font:16px/1.6 Inter,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:22px 16px 60px}
header{display:flex;align-items:center;gap:12px;margin-bottom:18px}
header img{height:38px;width:auto}
header b{font:700 18px/1.2 Rubik,sans-serif;color:var(--indigo)}
h1{font:700 25px/1.25 Rubik,sans-serif;color:var(--indigo);margin-bottom:8px;text-wrap:balance}
p.lead{color:var(--muted);margin-bottom:18px}
.cams{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
figure{margin:0;background:#fff;border:1px solid var(--line);border-radius:var(--r);
       overflow:hidden}
.frame{position:relative;aspect-ratio:16/9;max-width:100%;background:#0E1020}
.frame iframe{position:absolute;inset:0;width:100%;height:100%;border:0}
figcaption{padding:11px 14px;font-weight:600;font-size:15px}
.login{max-width:390px;margin:6vh auto 0;background:#fff;border:1px solid var(--line);
       border-radius:var(--r);padding:26px 24px}
.login label{display:block;font-size:14px;color:var(--muted);margin-bottom:6px}
.login input{width:100%;padding:12px 14px;font-size:16px;border:1px solid var(--line);
             border-radius:11px;background:var(--soft)}
.login input:focus{outline:2px solid var(--blue);outline-offset:1px}
.login button{width:100%;margin-top:14px;padding:12px 16px;font:600 16px Inter,sans-serif;
              color:#fff;background:var(--indigo);border:0;border-radius:11px;cursor:pointer}
.login button:hover{background:#3b2f9b}
.ok{background:#eef8e6;border:1px solid #cfe7b5;color:#3f6a12;padding:10px 12px;border-radius:12px;margin:12px 0}
.nast .row{margin:10px 0}
.nast label{display:block;font-size:13px;color:var(--muted);margin-bottom:4px}
.nast input{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:12px;font:15px Inter,sans-serif}
.nast hr{border:0;border-top:1px solid var(--line);margin:14px 0}
.err{margin-top:12px;padding:10px 12px;border-radius:11px;background:#FDECEC;
     color:#A3201A;font-size:14px}
.note{margin-top:26px;padding:16px 18px;background:var(--soft);border:1px solid #DCE6F5;
      border-radius:var(--r);font-size:14px;color:var(--muted)}
.empty{padding:26px;background:#fff;border:1px dashed var(--line);border-radius:var(--r);
       color:var(--muted)}
@media (max-width:640px){h1{font-size:21px}}
</style></head><body><div class=wrap>
<header><img src="/static/logo_color.png" alt="KidsUP"><b>KidsUP</b></header>"""

FOOT = "</div></body></html>"


def login_page(error: str = "") -> str:
    err = f'<div class=err>{_html.escape(error)}</div>' if error else ""
    return f"""{HEAD}
<div class=login>
  <h1>Видеонаблюдение</h1>
  <p class=lead>Введите пароль, который вам выдали на ресепшене.</p>
  <form method=post action="/kamery">
    <label for=p>Пароль</label>
    <input id=p name=password type=password autocomplete=current-password autofocus>
    <button type=submit>Смотреть камеры</button>
  </form>
  {err}
</div>{FOOT}"""


def page() -> str:
    cams = _cameras()
    if cams:
        body = '<div class=cams>' + "".join(
            f'<figure><div class=frame><iframe src="{_html.escape(c["src"])}" '
            f'allow="autoplay; fullscreen; encrypted-media" allowfullscreen '
            f'loading=lazy title="{_html.escape(c["name"])}"></iframe></div>'
            f'<figcaption>{_html.escape(c["name"])}</figcaption></figure>'
            for c in cams) + '</div>'
    else:
        body = ('<div class=empty>Камеры ещё не подключены. Если вы видите это '
                'сообщение — напишите нам в WhatsApp, поможем.</div>')
    return f"""{HEAD}
<h1>Видеонаблюдение</h1>
<p class=lead>Трансляция идёт в реальном времени. Звук не передаётся —
запись занятий и разговоров мы не ведём.</p>
{body}
<div class=note>Пароль личный: пожалуйста, не пересылайте его в чаты и
посторонним. Если видео не запускается — обновите страницу или откройте
её в другом браузере. Не работает и там: напишите нам в WhatsApp.</div>
{FOOT}"""

def save(raw: dict) -> dict:
    """Сохранить список камер из формы настройки.

    Приходит {"name_1": "...", "src_1": "...", ...}: строка попадает в список,
    только если заполнены оба поля и ссылка начинается с https. Пустые строки
    выбрасываются — так строка удаляется простым стиранием полей.
    """
    pairs = {}
    for k, v in (raw or {}).items():
        if "_" not in str(k):
            continue
        kind, _, idx = str(k).partition("_")
        if kind not in ("name", "src"):
            continue
        pairs.setdefault(idx, {})[kind] = str(v or "").strip()
    out = []
    for idx in sorted(pairs, key=lambda x: (len(x), x)):
        it = pairs[idx]
        src, name = it.get("src", ""), it.get("name", "")
        if not src:
            continue
        if not src.startswith("https://"):
            # из кабинета часто копируют целиком <iframe src="...">
            m = re.search(r'src=["\']([^"\']+)["\']', src)
            src = m.group(1) if m else ""
        if src.startswith("https://"):
            out.append({"name": name or "Камера", "src": src})
    db.set_setting("cam_embeds", json.dumps(out, ensure_ascii=False))
    return {"ok": True, "камер": len(out), "список": out}


def nastroyka(saved: int | None = None) -> str:
    """Форма владельца: вставить ссылки на камеры без правки JSON руками."""
    cams = _cameras() + [{"name": "", "src": ""}] * 3
    rows = "".join(
        f'<div class=row><label>Название кабинета</label>'
        f'<input name="name_{i}" value="{_html.escape(c["name"])}" '
        f'placeholder="Например: Кабинет 2, английский"></div>'
        f'<div class=row><label>Ссылка на трансляцию</label>'
        f'<input name="src_{i}" value="{_html.escape(c["src"])}" '
        f'placeholder="https://... или весь код &lt;iframe src=...&gt;"></div><hr>'
        for i, c in enumerate(cams, 1))
    msg = (f'<div class=ok>Сохранено: камер — {saved}. '
           f'<a href="/kamery">Открыть страницу камер</a></div>'
           if saved is not None else "")
    return f"""{HEAD}
<h1>Настройка камер</h1>
<p class=lead>Вставьте ссылку на трансляцию каждой камеры. В кабинете
видеонаблюдения это «Поделиться» → «Встроить на сайт»: можно скопировать
весь код, адрес я выну сам. Пустая строка удаляет камеру.</p>
{msg}
<form method=post action="/kamery/nastroyka" class=nast>
{rows}
<button type=submit>Сохранить</button>
</form>
<div class=note>Родителям страница открывается по адресу app.kidsup.ru/kamery
и просит пароль. Пароль меняется в настройках сервера (cam_password).</div>
{FOOT}"""
