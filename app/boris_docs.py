"""Документы владельца: app.kidsup.ru/boris/docs, только под логином boris.

15.10.2026 Борис: «мне нужны последние версии документов (страницы на сайте под
логином boris)» — прибыльность по педагогам, места по группам, разбор махинаций
и ошибок админов, презентация ИИ-сотрудника. В них зарплаты, имена детей и
выводы о сотрудниках, а репозиторий кода публичный, поэтому документы живут не в
docs/, а в data/boris_docs на сервере и выкладываются запросом владельца
(POST /api/boris-docs). Прежняя версия при замене уходит в data/boris_docs/arhiv —
«последняя версия» всегда одна, но старую можно достать.
"""
from __future__ import annotations

import html as _html
import json
import re
import threading
from datetime import datetime, timedelta, timezone

from . import config

MAX_BYTES = 15_000_000
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,60}")
MSK = timezone(timedelta(hours=3))
_lock = threading.Lock()


def ok_slug(slug: str) -> bool:
    return bool(SLUG_RE.fullmatch(slug or ""))


class Sklad:
    """Папка документов на сервере: файлы, индекс и архив прежних версий.

    16.10.2026 Борис: «сделай страницу для админов, кто ещё не оплатил октябрь по
    группам». В таком списке имена и телефоны детей, а репозиторий публичный, поэтому
    у админов теперь такая же папка, как у владельца: data/admin_docs, страницы
    /admin/docs под логином admin, выкладывает владелец (POST /api/admin-docs).
    """

    def __init__(self, papka: str, url: str, h1: str, lead: str, title: str):
        self.dir = config.DATA_DIR / papka
        self.arhiv = self.dir / "arhiv"
        self.index = self.dir / "index.json"
        self.url, self.h1, self.lead, self.title = url, h1, lead, title

    def _index(self) -> dict:
        try:
            d = json.loads(self.index.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:  # noqa: BLE001 — нет файла или битый: начинаем с пустого
            return {}

    def _write_index(self, idx: dict) -> None:
        tmp = self.dir / ".index.tmp"
        tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.index)

    def save(self, slug: str, title: str, html: str, note: str = "", order: int | None = None) -> dict:
        """Записать документ. Прежний файл с тем же slug переносится в архив."""
        if not ok_slug(slug):
            raise ValueError("slug: латиница, цифры, «-» и «_», 2–61 символ")
        title = (title or "").strip()[:200]
        if not title:
            raise ValueError("нужен title")
        data = (html or "").encode("utf-8")
        if len(data) < 50 or len(data) > MAX_BYTES:
            raise ValueError(f"html: от 50 байт до {MAX_BYTES // 1_000_000} МБ")
        # порядок проверяем до того, как трогать файлы: иначе старая версия уже в архиве,
        # а индекс остался прежним
        order_v = int(order) if order is not None else None
        now = datetime.now(MSK)
        with _lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            idx = self._index()
            f = self.dir / f"{slug}.html"
            if f.exists():
                self.arhiv.mkdir(parents=True, exist_ok=True)
                f.replace(self.arhiv / f"{slug}_{now:%Y%m%d_%H%M%S}.html")
            tmp = self.dir / f".{slug}.tmp"
            tmp.write_bytes(data)
            tmp.replace(f)
            old = idx.get(slug) or {}
            idx[slug] = {
                "title": title,
                "note": (note or "").strip()[:500],
                "updated": now.strftime("%Y-%m-%d %H:%M"),
                "size": len(data),
                "order": order_v if order_v is not None else int(old.get("order") or 100),
                "versions": int(old.get("versions") or 0) + 1,
            }
            self._write_index(idx)
            return {"slug": slug, **idx[slug]}

    def get(self, slug: str) -> str | None:
        if not ok_slug(slug):
            return None
        f = self.dir / f"{slug}.html"
        return f.read_text(encoding="utf-8") if f.exists() else None

    def remove(self, slug: str) -> bool:
        """Убрать документ из списка; сам файл уходит в архив, а не удаляется."""
        if not ok_slug(slug):
            return False
        with _lock:
            idx = self._index()
            f = self.dir / f"{slug}.html"
            if f.exists():
                self.arhiv.mkdir(parents=True, exist_ok=True)
                f.replace(self.arhiv / f"{slug}_{datetime.now(MSK):%Y%m%d_%H%M%S}_snyat.html")
            if idx.pop(slug, None) is None:
                return False
            self._write_index(idx)
            return True

    def spisok(self) -> list[dict]:
        idx = self._index()
        rows = [{"slug": k, **v} for k, v in idx.items() if (self.dir / f"{k}.html").exists()]
        rows.sort(key=lambda r: (int(r.get("order") or 100), r["slug"]))
        return rows

    def page(self) -> str:
        rows = self.spisok()
        e = _html.escape
        if rows:
            items = "".join(
                f'<a class=doc href="{self.url}/{e(r["slug"])}">'
                f'<span class=t>{e(r["title"])}</span>'
                + (f'<span class=n>{e(r["note"])}</span>' if r.get("note") else "")
                + f'<span class=m>обновлено {e(r["updated"])} МСК · версия {int(r.get("versions") or 1)}</span></a>'
                for r in rows)
        else:
            items = '<p class=empty>Документов пока нет.</p>'
        return f"""<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<meta name=robots content="noindex, nofollow">
<title>{e(self.title)}</title>
<style>
:root{{--indigo:#312783;--blue:#1DA7E0;--ink:#221F3B;--muted:#626783;--line:#E2E6F2;--bg:#F7F8FC;--card:#fff}}
@media (prefers-color-scheme:dark){{:root{{--indigo:#B9B2FF;--blue:#5CC4EE;--ink:#ECEBF7;--muted:#A3A6C2;
  --line:#2E3150;--bg:#141527;--card:#1C1E36}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
.w{{max-width:760px;margin:0 auto;padding:28px 16px 60px}}
h1{{font-size:26px;line-height:1.2;margin:0 0 6px;color:var(--indigo);text-wrap:balance}}
p.lead{{color:var(--muted);margin:0 0 22px}}
.list{{display:grid;gap:12px}}
a.doc{{display:grid;gap:4px;padding:16px 18px;background:var(--card);border:1px solid var(--line);
  border-radius:14px;text-decoration:none;color:inherit}}
a.doc:hover,a.doc:focus-visible{{border-color:var(--blue);outline:none}}
.t{{font-weight:650;font-size:18px;color:var(--indigo)}}
.n{{color:var(--ink)}}
.m{{color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}}
.empty{{color:var(--muted)}}
</style></head><body><div class=w>
<h1>{e(self.h1)}</h1>
<p class=lead>{e(self.lead)}</p>
<div class=list>{items}</div>
</div></body></html>"""


BORIS = Sklad("boris_docs", "/boris/docs", "Документы владельца",
              "Открываются только под логином boris. Данные лежат на сервере центра, в репозиторий "
              "кода не попадают; при обновлении прежняя версия уходит в архив.",
              "Документы владельца — KidsUP")
ADMIN = Sklad("admin_docs", "/admin/docs", "Списки для администраторов",
              "Рабочие списки с именами и телефонами семей. Лежат на сервере центра, не пересылайте "
              "их в чаты и мессенджеры.",
              "Списки для администраторов — KidsUP")

# Прежние имена модуля — для владельца, как до 16.10
DIR, ARHIV, INDEX = BORIS.dir, BORIS.arhiv, BORIS.index
save, get, remove, spisok, page = BORIS.save, BORIS.get, BORIS.remove, BORIS.spisok, BORIS.page
