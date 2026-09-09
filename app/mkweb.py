"""Браузерный вход в МойКласс на сервере (09.09.2026).

Зачем. API МойКласса не отдаёт чаты с родителями и не позволяет вносить
остатки на складе. Борис завёл технический аккаунт сотрудника и попросил
«подключиться через браузер на нашем сервере». Из контейнера Клода браузер
наружу не выходит (прокси режет Chromium), поэтому Chromium живёт здесь,
рядом с приложением, и управляется через эндпоинты /api/mkweb/*.

Что здесь есть:
  status()  — стоит ли playwright и браузер, чей это процесс, хвост лога установки;
  setup()   — ставит playwright и Chromium (фоновый поток, лог в data/mkweb_setup.log);
  login()   — входит под mk_web_login / mk_web_password из настроек, сохраняет
              cookies в data/mk_web_state.json и скриншот в data/mkweb_last.png;
  open_page(url) — открывает страницу под сохранённой сессией, возвращает текст
              и скриншот — этим читаем чаты.

Логин и пароль — только из настроек сервера (SECRET_KEYS), в ответы не попадают.
Никаких действий в CRM отсюда не делается — только чтение экрана.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import db

DATA = Path(__file__).resolve().parent.parent / "data"
DATA.mkdir(exist_ok=True)
SETUP_LOG = DATA / "mkweb_setup.log"
STATE = DATA / "mk_web_state.json"
SHOT = DATA / "mkweb_last.png"
LOGIN_URL = "https://app.moyklass.com/"

_lock = threading.Lock()
_setup_running = False


def _chromium_path() -> str | None:
    """Путь к Chromium, который поставил playwright (или системный).

    Раскладки у playwright разные: chromium-*/chrome-linux/chrome,
    chromium_headless_shell-*/chrome-linux/headless_shell и новая
    chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell.
    """
    bases = [Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])] if os.environ.get("PLAYWRIGHT_BROWSERS_PATH") else []
    bases += [Path.home() / ".cache" / "ms-playwright", Path("/root/.cache/ms-playwright"), Path("/opt/pw-browsers")]
    names = ("chrome", "headless_shell", "chrome-headless-shell")
    for base in bases:
        if not base.exists():
            continue
        for d in sorted(base.glob("chromium-*")) + sorted(base.glob("chromium_headless_shell-*")):
            for sub in d.iterdir():
                if sub.is_dir():
                    for n in names:
                        exe = sub / n
                        if exe.exists():
                            return str(exe)
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def status() -> dict:
    try:
        import playwright  # noqa: F401
        pw = getattr(playwright, "__version__", "ok")
    except Exception as e:  # noqa: BLE001
        pw = f"нет ({type(e).__name__})"
    tail = ""
    if SETUP_LOG.exists():
        tail = SETUP_LOG.read_text(encoding="utf-8", errors="replace")[-1500:]
    return {
        "python": sys.executable,
        "user": os.environ.get("USER") or os.environ.get("LOGNAME") or str(os.getuid()),
        "playwright": pw,
        "chromium": _chromium_path(),
        "setup_running": _setup_running,
        "setup_log_tail": tail,
        "session_saved": STATE.exists(),
        "session_mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(STATE.stat().st_mtime)) if STATE.exists() else None,
        "login_set": bool(db.get_setting("mk_web_login")),
        "password_set": bool(db.get_setting("mk_web_password")),
    }


def _run(cmd: list[str], log) -> int:
    log.write(f"\n$ {' '.join(cmd)}\n"); log.flush()
    p = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, text=True, timeout=1800)
    log.write(f"[exit {p.returncode}]\n"); log.flush()
    return p.returncode


def setup() -> dict:
    """Ставит playwright и Chromium. Долгая операция — уходит в фон."""
    global _setup_running
    if _setup_running:
        return {"ok": True, "already_running": True}

    def worker():
        global _setup_running
        _setup_running = True
        try:
            with SETUP_LOG.open("w", encoding="utf-8") as log:
                log.write(time.strftime("%Y-%m-%d %H:%M:%S") + " установка playwright + chromium\n")
                _run([sys.executable, "-m", "pip", "install", "-q", "playwright"], log)
                # --with-deps тянет системные библиотеки через apt (нужен root);
                # если не вышло — ставим сам браузер, библиотеки чаще всего уже есть.
                rc = _run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"], log)
                if rc != 0:
                    _run([sys.executable, "-m", "playwright", "install", "chromium"], log)
                log.write("готово: chromium=" + str(_chromium_path()) + "\n")
        finally:
            _setup_running = False

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "started": True}


def _launch(p):
    exe = _chromium_path()
    kw = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    if exe:
        kw["executable_path"] = exe
    return p.chromium.launch(**kw)


def login() -> dict:
    """Вход по логину/паролю из настроек, сохранение сессии и скриншота."""
    from playwright.sync_api import sync_playwright

    user = db.get_setting("mk_web_login")
    pwd = db.get_setting("mk_web_password")
    if not user or not pwd:
        return {"ok": False, "error": "mk_web_login / mk_web_password не заданы в настройках"}
    with _lock, sync_playwright() as p:
        b = _launch(p)
        ctx = b.new_context(viewport={"width": 1400, "height": 900}, locale="ru-RU")
        pg = ctx.new_page()
        pg.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(2500)
        inputs = []
        for i in pg.query_selector_all("input"):
            inputs.append({"type": i.get_attribute("type"), "name": i.get_attribute("name"),
                           "placeholder": i.get_attribute("placeholder")})
        try:
            email = pg.locator("input[type=email], input[name=email], input[name=login], input[name=username], input[type=text]").first
            email.fill(user)
            pw = pg.locator("input[type=password]").first
            pw.fill(pwd)
            pw.press("Enter")
        except Exception as e:  # noqa: BLE001
            pg.screenshot(path=str(SHOT))
            b.close()
            return {"ok": False, "error": f"поля входа не найдены: {e}", "inputs": inputs, "url": pg.url}
        # SPA после входа долго крутит лоадер: ждём до 25 с, пока не исчезнет поле пароля
        logged = False
        for _ in range(25):
            pg.wait_for_timeout(1000)
            if pg.locator("input[type=password]").count() == 0 and "login=yes" not in pg.url:
                logged = True
                break
        pg.wait_for_timeout(3000)
        url, title = pg.url, pg.title()
        body = pg.inner_text("body")[:2000]
        pg.screenshot(path=str(SHOT))
        if not logged:
            logged = pg.locator("input[type=password]").count() == 0
        if logged:
            ctx.storage_state(path=str(STATE))
        b.close()
        return {"ok": logged, "url": url, "title": title, "inputs": inputs, "text": body}


def open_page(url: str, wait_ms: int = 4000, max_text: int = 6000, click: str = "", links: bool = False,
              actions: list | None = None, rows: bool = False) -> dict:
    """Открыть страницу под сохранённой сессией; вернуть текст и сделать скриншот."""
    from playwright.sync_api import sync_playwright

    if not STATE.exists():
        return {"ok": False, "error": "сессии нет — сначала /api/mkweb/login"}
    with _lock, sync_playwright() as p:
        b = _launch(p)
        ctx = b.new_context(storage_state=str(STATE), viewport={"width": 1400, "height": 900}, locale="ru-RU")
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(wait_ms)
        if pg.locator("input[type=password]").count() > 0:
            # сессия протухла — перелогиниваемся тем же браузером и идём снова
            b.close()
            res = login()
            if not res.get("ok"):
                return {"ok": False, "error": "сессия истекла, повторный вход не удался", "login": res}
            return open_page(url, wait_ms, max_text, click, links, actions, rows)
        # баннер про cookies перекрывает низ экрана — принимаем один раз
        try:
            btn = pg.get_by_text("Согласен", exact=True)
            if btn.count() > 0:
                btn.first.click(timeout=3000)
                pg.wait_for_timeout(500)
        except Exception:  # noqa: BLE001
            pass
        if click:
            # клик по пункту меню/кнопке по видимому тексту — так добираемся до разделов,
            # у которых нет угадываемого URL (например «Мой Чат»)
            pg.get_by_text(click, exact=True).first.click(timeout=15000)
            pg.wait_for_timeout(wait_ms)
        done = []
        for st in actions or []:
            # шаги: {"click": "текст"} | {"css": "селектор", "click": true} | {"fill": "текст", "css"/"placeholder"/"label": ...}
            #       | {"press": "Enter", "css": ...} | {"wait": мс} | {"scroll": "bottom"} | {"select_text": "текст в выпадашке"}
            try:
                if "wait" in st and len(st) == 1:
                    pg.wait_for_timeout(int(st["wait"]))
                elif "js" in st:
                    # произвольный JS в странице — вернуть результат (для div-таблиц без <table>)
                    st = {**st, "result": pg.evaluate(st["js"])}
                elif st.get("scroll") == "bottom":
                    for _ in range(int(st.get("times", 5))):
                        pg.mouse.wheel(0, 4000)
                        pg.wait_for_timeout(600)
                elif "fill" in st:
                    loc = (pg.locator(st["css"]) if st.get("css") else
                           pg.get_by_placeholder(st["placeholder"]) if st.get("placeholder") else
                           pg.get_by_label(st["label"]))
                    loc.first.click(timeout=10000)
                    loc.first.fill(str(st["fill"]))
                    if st.get("press"):
                        loc.first.press(st["press"])
                elif st.get("press") and st.get("css"):
                    pg.locator(st["css"]).first.press(st["press"])
                elif st.get("css"):
                    pg.locator(st["css"]).nth(int(st.get("nth", 0))).click(timeout=15000)
                elif st.get("click"):
                    pg.get_by_text(str(st["click"]), exact=bool(st.get("exact", True))).nth(int(st.get("nth", 0))).click(timeout=15000)
                pg.wait_for_timeout(int(st.get("after", 1500)))
                done.append({**st, "ok": True})
            except Exception as e:  # noqa: BLE001
                done.append({**st, "ok": False, "error": str(e).splitlines()[0][:160]})
                if st.get("required", True):
                    break
        text = pg.inner_text("body")[:max_text]
        pg.screenshot(path=str(SHOT), full_page=False)
        out = {"ok": True, "url": pg.url, "title": pg.title(), "text": text, "actions": done}
        if rows:
            # строки таблиц целиком, по ячейкам — так читаем «Историю» и склад без угадывания по тексту
            out["rows"] = [[(td.inner_text() or "").strip() for td in tr.query_selector_all("td,th")]
                           for tr in pg.query_selector_all("table tr")][:400]
        if links:
            out["links"] = [{"text": (a.inner_text() or "").strip()[:60], "href": a.get_attribute("href")}
                            for a in pg.query_selector_all("a[href]")][:200]
        # сессия могла обновиться (cookies) — сохраняем
        try:
            ctx.storage_state(path=str(STATE))
        except Exception:  # noqa: BLE001
            pass
        b.close()
        return out


# ───────────────────────── История действий сотрудников ─────────────────────────

ROW_JS = """
Array.from(document.querySelectorAll('.layout-align-space-between-stretch'))
  .filter(e => e.children.length === 5)
  .map(e => Array.from(e.children).map(c => (c.innerText || '').trim()))
"""


def _parse_hist_row(cells: list[str]) -> dict:
    """Пять колонок «Истории»: дата/источник/ip · событие+кратко · подробности · причина · сотрудник."""
    c0 = [x.strip() for x in cells[0].split("\n") if x.strip()]
    c1 = [x.strip() for x in cells[1].split("\n") if x.strip()]
    who = cells[4].replace("\n", " ").strip()
    m = None
    import re as _re
    mm = _re.match(r"№(\d+)\s+(.*)", who)
    if mm:
        m = {"id": int(mm.group(1)), "name": mm.group(2).strip()}
    return {
        "ts": " ".join(c0[:2]) if len(c0) >= 2 else (c0[0] if c0 else ""),
        "source": c0[2] if len(c0) > 2 else "",
        "ip": (c0[3].replace("ip ", "") if len(c0) > 3 else ""),
        "event": c1[0] if c1 else "",
        "short": " · ".join(c1[1:]),
        "details": cells[2].replace("\n", " · ").strip(),
        "reason": cells[3].replace("\n", " ").strip(),
        "who": (m or {}).get("name", who),
        "who_id": (m or {}).get("id"),
    }


def history(period: str = "Сегодня", employee: str = "", event_type: str = "",
            max_pages: int = 40, date_from: str = "", date_to: str = "") -> dict:
    """Читает /history: период кнопкой («Вчера»/«Сегодня»/«Неделя»/«Месяц») или датами
    ДД.ММ.ГГГГ, необязательные фильтры по сотруднику и типу события; листает страницы.
    Возвращает события списком словарей. Только чтение."""
    from playwright.sync_api import sync_playwright

    if not STATE.exists():
        login()
    events: list[dict] = []
    with _lock, sync_playwright() as p:
        b = _launch(p)
        ctx = b.new_context(storage_state=str(STATE), viewport={"width": 1400, "height": 900}, locale="ru-RU")
        pg = ctx.new_page()
        pg.goto(LOGIN_URL.rstrip("/") + "/history", wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(4000)
        if pg.locator("input[type=password]").count() > 0:
            b.close()
            login()
            return history(period, employee, event_type, max_pages, date_from, date_to)
        try:
            pg.get_by_text("Согласен", exact=True).first.click(timeout=2000)
        except Exception:  # noqa: BLE001
            pass
        if date_from or date_to:
            inputs = pg.locator("input.md-datepicker-input")
            if date_from:
                inputs.nth(0).fill(date_from); inputs.nth(0).press("Enter")
            if date_to:
                inputs.nth(1).fill(date_to); inputs.nth(1).press("Enter")
        else:
            pg.get_by_text(period, exact=True).first.click(timeout=10000)
        pg.wait_for_timeout(500)

        def pick(label: str, value: str):
            pg.locator(f"md-select[aria-label='{label}']").first.click(timeout=10000)
            pg.wait_for_timeout(800)
            pg.locator(".md-select-menu-container.md-active md-option").filter(has_text=value).first.click(timeout=10000)
            pg.wait_for_timeout(500)
            pg.keyboard.press("Escape")

        if employee:
            pick("Сотрудник", employee)
        if event_type:
            pick("Тип события", event_type)
        pg.get_by_text("Найти", exact=True).first.click(timeout=10000)
        pg.wait_for_timeout(5000)
        total_txt = ""
        try:
            total_txt = pg.locator("text=/Всего:\\s*\\d+/").first.inner_text(timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        page_no = 1
        while True:
            rows = pg.evaluate(ROW_JS) or []
            events.extend(_parse_hist_row(r) for r in rows if len(r) == 5)
            page_no += 1
            if page_no > max_pages:
                break
            nxt = pg.locator(".mc-pagination-light-item").filter(has_text=re_compile(rf"^\s*{page_no}\s*$"))
            if nxt.count() == 0:
                break
            nxt.first.click(timeout=10000)
            pg.wait_for_timeout(3500)
        pg.screenshot(path=str(SHOT))
        b.close()
    # дедуп по (ts, event, details, who): при перелистывании одна строка может попасть дважды
    seen, out = set(), []
    for e in events:
        k = (e["ts"], e["event"], e["details"], e["who"])
        if k in seen:
            continue
        seen.add(k); out.append(e)
    return {"ok": True, "total_label": total_txt, "pages_read": page_no - 1, "events": out}


def re_compile(pattern: str):
    import re as _re
    return _re.compile(pattern)


# ───────────────────────── Склад ─────────────────────────

LEFTOVERS_JS = """
(() => {
  const t = document.body.innerText; const i = t.indexOf('Всего:');
  const lines = t.slice(i).split('\\n').map(s => s.trim()).filter(Boolean);
  const items = []; 
  for (let k = 1; k < lines.length; k++) {
    if (lines[k].startsWith('Цена д/кл')) {
      items.push({name: lines[k-1], price: (lines[k].match(/\\d+/)||[''])[0], filial: lines[k+1] || '', qty: (lines[k+2]||'').match(/-?\\d+/) ? parseInt((lines[k+2].match(/-?\\d+/)||['0'])[0]) : null});
      k += 2;
    }
  }
  return {total: lines[0], items};
})()
"""


def leftovers() -> dict:
    """Остатки склада: список {name, price, filial, qty}. Только чтение."""
    from playwright.sync_api import sync_playwright

    if not STATE.exists():
        login()
    with _lock, sync_playwright() as p:
        b = _launch(p)
        ctx = b.new_context(storage_state=str(STATE), viewport={"width": 1400, "height": 900}, locale="ru-RU")
        pg = ctx.new_page()
        pg.goto(LOGIN_URL.rstrip("/") + "/warehouse/leftovers", wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(5000)
        for _ in range(8):
            pg.mouse.wheel(0, 4000); pg.wait_for_timeout(400)
        res = pg.evaluate(LEFTOVERS_JS)
        b.close()
    return {"ok": True, **(res or {})}


def supply(product: str, count: int, filial: str = "Kids UP Богородский", supplier: str = "Корректировка остатков",
           payment: str = "", cashbox: str = "", cost: float = 0, comment: str = "", dry_run: bool = True) -> dict:
    """Оформить поставку одного товара через форму «Новая поставка».

    dry_run=True — заполнить форму, сделать скриншот и НЕ нажимать «Добавить».
    Себестоимость 0 + галочка «Провести товар с нулевой стоимостью» — чтобы
    корректировка остатков не создавала расход в кассе.
    """
    from playwright.sync_api import sync_playwright

    if not STATE.exists():
        login()
    with _lock, sync_playwright() as p:
        b = _launch(p)
        ctx = b.new_context(storage_state=str(STATE), viewport={"width": 1400, "height": 1000}, locale="ru-RU")
        pg = ctx.new_page()
        pg.goto(LOGIN_URL.rstrip("/") + "/warehouse/leftovers", wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(4500)
        try:
            pg.get_by_text("Согласен", exact=True).first.click(timeout=2000)
        except Exception:  # noqa: BLE001
            pass
        pg.get_by_text("Добавить", exact=False).first.click(timeout=10000)
        pg.wait_for_timeout(1500)
        pg.get_by_text("Поставка", exact=True).first.click(timeout=10000)
        pg.wait_for_timeout(3000)
        steps = []

        def pick(label: str, value: str, search: bool = False):
            sel = pg.locator(f"md-select[aria-label='{label}']").first
            sel.scroll_into_view_if_needed(timeout=10000)
            sel.click(timeout=15000)
            pg.wait_for_timeout(1000)
            menu = pg.locator("md-select-menu:visible").first
            if search:
                # поле поиска — только внутри ОТКРЫТОГО меню (на странице есть такое же в фильтре)
                sb = menu.locator("input")
                if sb.count():
                    sb.first.click()
                    sb.first.press_sequentially(value[:12], delay=30)
                    pg.wait_for_timeout(1200)

            def find():
                o = menu.locator("md-option:visible")
                cnt = o.count()
                import re as _re
                nv = _re.sub(r"\s+", " ", value).strip().lower()
                texts = [_re.sub(r"\s+", " ", o.nth(i).inner_text()).strip().lower() for i in range(cnt)]
                for i, t in enumerate(texts):
                    if t == nv:
                        return o, cnt, i
                for i, t in enumerate(texts):
                    if nv in t:
                        return o, cnt, i
                return o, cnt, None

            opts, n, idx = find()
            if idx is None and search and n == 0:
                # поиск ничего не дал (другое написание) — очищаем и идём по списку прокруткой
                sb = menu.locator("input")
                if sb.count():
                    sb.first.fill("")
                    pg.wait_for_timeout(800)
                opts, n, idx = find()
            tries = 0
            while idx is None and tries < 25:
                # длинный список подгружается при прокрутке — крутим контейнер меню
                try:
                    # md-virtual-repeat рисует ~30 опций; прокручиваем его скроллер, а не md-content
                    sc = menu.locator(".md-virtual-repeat-scroller, md-content").first
                    sc.evaluate("el => { el.scrollTop += 600; el.dispatchEvent(new Event('scroll')); }")
                except Exception:  # noqa: BLE001
                    pg.mouse.wheel(0, 600)
                pg.wait_for_timeout(450)
                opts, n, idx = find()
                tries += 1
            if idx is None:
                # последний шанс: опции есть в DOM, но не отрисованы — ищем по тексту с
                # нормализацией пробелов (в названиях бывают двойные) и кликаем через JS
                import re as _re
                target = _re.sub(r"\s+", " ", value).strip().lower()
                clicked = pg.evaluate(
                    """([target]) => {
                        const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
                        const menus = Array.from(document.querySelectorAll('md-select-menu'));
                        const vis = menus.filter(m => m.offsetParent !== null);
                        const pool = (vis.length ? vis : menus).flatMap(m => Array.from(m.querySelectorAll('md-option')));
                        let el = pool.find(o => norm(o.textContent) === target) || pool.find(o => norm(o.textContent).includes(target));
                        if (!el) return false;
                        el.scrollIntoView({block: 'center'});
                        el.click();
                        return norm(el.textContent);
                    }""", [target])
                if clicked:
                    pg.wait_for_timeout(800)
                    steps.append({"pick": label, "value": value, "via": "js", "matched": clicked})
                    return
                raise RuntimeError(f"в списке «{label}» нет варианта «{value}» (видно {n})")
            opts.nth(idx).scroll_into_view_if_needed(timeout=10000)
            opts.nth(idx).click(timeout=15000)
            pg.wait_for_timeout(800)
            steps.append({"pick": label, "value": value, "visible_options": n})

        try:
            pick("Вид товара", product, search=True)
            pick("Склад филиала", filial)
            pg.locator("input[name=price]").first.fill(str(cost))
            pg.locator("input[name=count]").first.fill(str(count))
            if not cost:
                pg.get_by_text("Провести товар с нулевой стоимостью", exact=True).first.click(timeout=5000)
                pg.wait_for_timeout(400)
            if payment:
                pick("Вид оплаты поставки", payment)
            if cashbox:
                pick("Касса", cashbox)
            pick("Поставщик", supplier)
            if comment:
                pg.locator("input[name=comment]").first.fill(comment)
        except Exception as e:  # noqa: BLE001
            pg.screenshot(path=str(SHOT))
            b.close()
            return {"ok": False, "error": str(e).splitlines()[0][:200], "steps": steps}
        pg.wait_for_timeout(600)
        form_text = ""
        try:
            hdr = pg.get_by_text("Новая поставка", exact=True).first
            form_text = hdr.locator("xpath=ancestor::div[contains(@class,'layout-column')][1]").inner_text()[:1500]
        except Exception:  # noqa: BLE001
            pass
        pg.screenshot(path=str(SHOT))
        submitted = False
        if not dry_run:
            btn = pg.locator("button.md-primary.md-button").filter(has_text=re_compile(r"^\s*Добавить\s*$")).last
            btn.click(timeout=10000)
            pg.wait_for_timeout(3500)
            submitted = pg.get_by_text("Новая поставка", exact=True).count() == 0
            pg.screenshot(path=str(SHOT))
        b.close()
    return {"ok": True, "dry_run": dry_run, "submitted": submitted, "steps": steps, "form": form_text}
