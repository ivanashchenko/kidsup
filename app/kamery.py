"""Страница видеонаблюдения для родителей: app.kidsup.ru/kamery.

Отдельный пароль, а не общий админский: его знают все сотрудники, и давать
его родителям нельзя. Пароль и список камер живут в настройках сервера
(cam_password, cam_embeds), поэтому меняются без выкладки кода.

cam_embeds — JSON-список [{"name": "Английский, кабинет 2", "src": "<ссылка
на плеер Ivideon>"}]. Ссылку даёт кабинет Ivideon: камера → «Поделиться» →
«Встроить на сайт», оттуда нужен адрес из атрибута src.

cam_okna (16.10.2026, план «Камеры: трансляция по правилам») — какие кабинеты
и в какие часы показывать родителям: «К4 пн-пт 09:00-13:00; К2 пн-пт
10:00-14:00», окна через «;» или с новой строки, у кабинета их может быть
несколько. Пусто — как раньше: все камеры круглые сутки. Задано — кабинеты без
окна не показываются вовсе, а вне окна вместо плеера стоит расписание: ссылки
на плеер в странице тогда нет, скопировать её нечем. cam_embeds не трогаем —
по нему же фото-явка снимает все кабинеты (app/kadry.py).
"""

from __future__ import annotations

import html as _html
import hmac
import json
import re
from datetime import datetime, timedelta, timezone

from . import db

COOKIE = "kam"
MSK = timezone(timedelta(hours=3))
DNI = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


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


def _komnata(name: str) -> str:
    """«Комната 4» → «К4» — как в kadry.cam_rooms() и справочнике rooms."""
    m = re.search(r"(\d+)", name or "")
    return "К" + m.group(1) if m else ""


def _dni(s: str) -> set[int] | None:
    """«пн-пт» → {0..4}, «вт,чт,пт» → {1, 3, 4}; непонятное → None."""
    out: set[int] = set()
    for part in s.split(","):
        a, _, b = part.strip().partition("-")
        if a not in DNI or (b and b not in DNI):
            return None
        i, j = DNI.index(a), DNI.index(b or a)
        out.update(range(i, j + 1) if i <= j else [*range(i, 7), *range(0, j + 1)])
    return out


def okna(raw: str | None = None) -> dict[str, list[tuple[set[int], int, int, str]]]:
    """cam_okna → {"К4": [(дни, с минуты, до минуты, «пн-пт 09:00-13:00»)]}.

    Строку, которую не разобрали, пропускаем: лучше не показать кабинет, чем
    показать его круглые сутки из-за опечатки.
    """
    raw = db.get_setting("cam_okna") if raw is None else raw
    out: dict[str, list] = {}
    for line in re.split(r"[;\n]", (raw or "").lower().replace("–", "-").replace("—", "-")):
        m = re.fullmatch(r"\s*[кk]\s*(\d+)\s+([а-я,\-\s]+?)\s+(\d{1,2})[:.](\d\d)\s*-\s*(\d{1,2})[:.](\d\d)\s*", line)
        if not m:
            continue
        dni = _dni(m.group(2).replace(" ", ""))
        ot, do = int(m.group(3)) * 60 + int(m.group(4)), int(m.group(5)) * 60 + int(m.group(6))
        if not dni or ot >= do or do > 24 * 60:
            continue
        tekst = f"{m.group(2).replace(' ', '')} {m.group(3)}:{m.group(4)}–{m.group(5)}:{m.group(6)}"
        out.setdefault("К" + m.group(1), []).append((dni, ot, do, tekst))
    return out


def _seychas_vidno(windows: list, now: datetime) -> bool:
    minute = now.hour * 60 + now.minute
    return any(now.weekday() in dni and ot <= minute < do for dni, ot, do, _ in windows)


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
figcaption{padding:10px 14px;font-weight:600;font-size:15px;display:flex;align-items:center;
           justify-content:space-between;gap:10px;flex-wrap:wrap}
.fs{font:600 14px Inter,sans-serif;color:var(--indigo);background:var(--soft);border:1px solid #DCE6F5;
    border-radius:10px;padding:7px 12px;cursor:pointer;text-decoration:none}
.fs:hover{border-color:var(--blue)}
.frame:fullscreen,.frame:-webkit-full-screen{aspect-ratio:auto;width:100vw;height:100vh;background:#000}
.frame.off{display:flex;align-items:center;justify-content:center;background:var(--soft)}
.frame.off p{margin:0;padding:0 18px;text-align:center;color:var(--muted);font-size:15px}
.how{margin-top:18px;padding:14px 18px;background:#fff;border:1px solid var(--line);border-radius:var(--r);font-size:15px}
.how b{color:var(--indigo)}
.how ul{margin:8px 0 0;padding-left:20px}
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


# Полный экран: кнопка разворачивает рамку с камерой (Android, компьютер,
# iPad); на iPhone Safari не умеет разворачивать чужой блок — открываем видео
# отдельной вкладкой, там его разворачивает поворот телефона.
FS_HOW = """
<div class=how><b>Как смотреть на весь экран</b>
<ul>
<li>Нажмите «⛶ На весь экран» под нужной камерой. Выйти — кнопка «Назад» или Esc.</li>
<li>На iPhone видео откроется отдельной вкладкой — поверните телефон горизонтально.</li>
<li>Можно и в самом плеере: значок ⛶ в правом нижнем углу видео.</li>
</ul></div>
<script>
document.querySelectorAll('.fs').forEach(function (b) {
  b.addEventListener('click', function () {
    var f = b.closest('figure').querySelector('.frame');
    var go = f.requestFullscreen || f.webkitRequestFullscreen;
    if (go) {
      try { var r = go.call(f); if (r && r.catch) r.catch(function () { window.open(b.dataset.src, '_blank'); }); return; }
      catch (e) {}
    }
    window.open(b.dataset.src, '_blank');
  });
});
</script>"""


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


def _plitka(c: dict) -> str:
    return (f'<figure><div class=frame><iframe src="{_html.escape(c["src"])}" '
            f'allow="autoplay; fullscreen; encrypted-media" allowfullscreen '
            f'loading=lazy title="{_html.escape(c["name"])}"></iframe></div>'
            f'<figcaption><span>{_html.escape(c["name"])}</span>'
            f'<button class=fs type=button data-src="{_html.escape(c["src"])}">⛶ На весь экран</button>'
            f'</figcaption></figure>')


def _zaglushka(c: dict, windows: list) -> str:
    kogda = ", ".join(t for *_, t in windows)
    return (f'<figure><div class="frame off"><p>Трансляция идёт в часы занятий группы: '
            f'{_html.escape(kogda)}</p></div>'
            f'<figcaption><span>{_html.escape(c["name"])}</span></figcaption></figure>')


def page(now: datetime | None = None) -> str:
    cams = _cameras()
    ok = okna()
    if ok:
        # Окна заданы: только кабинеты с окном, плеер — только внутри окна.
        now = now or datetime.now(MSK)
        cams = [(c, ok[_komnata(c["name"])]) for c in cams if _komnata(c["name"]) in ok]
        plitki = [_plitka(c) if _seychas_vidno(w, now) else _zaglushka(c, w) for c, w in cams]
        live = any(_seychas_vidno(w, now) for _, w in cams)
    else:
        plitki = [_plitka(c) for c in cams]
        live = bool(cams)
    if plitki:
        body = '<div class=cams>' + "".join(plitki) + '</div>' + (FS_HOW if live else "")
    else:
        body = ('<div class=empty>Камеры ещё не подключены. Если вы видите это '
                'сообщение — напишите нам в WhatsApp, поможем.</div>')
    return f"""{HEAD}
<h1>Мини-сад и нулевой класс</h1>
<p class=lead>Трансляция из кабинетов в реальном времени.
Мини-сад — с 9:00 до 13:00, нулевой класс — с 10:00 до 14:00.</p>
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
