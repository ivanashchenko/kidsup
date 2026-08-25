"""Что осталось от вчерашних листов обзвона.

Зачем. Лист прозвона пересобирается каждое утро заново, и вчерашний
хвост в нём растворяется: человек, которого вчера не набрали, завтра
может не попасть в выборку вовсе. 25.08 проверка показала, насколько
это дорого: Ира прошла 61% своего листа, Лена — 6%, и 128 семей просто
выпали из работы, хотя вчера считались приоритетом.

Как считаем «не тронут». По журналу АТС: не было ни одного набора этого
номера за вчера и сегодня. Именно набора, а не разговора — если админ
позвонил и не дозвонился, строка отработана, дальше её ведёт догон
недозвонов, а не второй заход по тому же листу.

Лист остатка собирается из самого вчерашнего файла: там уже есть имя,
возраст, что ребёнок посещал и куда его звать. Пересобирать эти данные
заново незачем, а формулировки должны совпадать — админ работает
с той же строкой, что видел вчера.

Запуск:
    python -m app.ostatki            — сколько осталось по каждому листу
    python -m app.ostatki build      — собрать листы остатков
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("kidsup.ostatki")

DOCS = Path(__file__).resolve().parent.parent / "docs"
# какой лист чей и как назвать остаток
SHEETS = [
    ("list_irina", "ostatok_ira", "Ира", "английский, 7–12 лет"),
    ("list_lena", "ostatok_lena", "Лена", "подготовка к школе, Катя и Инга"),
    ("list_anya_sad", "ostatok_anya", "Аня", "мини-сад и нулевой класс"),
]


def _dialed(days: int = 2) -> set[str]:
    """Все номера, которые набирали за последние `days` дней."""
    from . import mango
    out: set[str] = set()
    for dd in range(days):
        day = date.today() - timedelta(days=dd)
        try:
            rows = mango.calls(datetime.combine(day, datetime.min.time()),
                               datetime.combine(day, datetime.max.time()))
        except Exception:
            log.warning("журнал АТС за %s недоступен", day)
            continue
        for r in rows:
            n = (r.get("to_num") if r.get("from_ext") else r.get("from_num")) or ""
            d = "".join(c for c in str(n) if c.isdigit())[-10:]
            if len(d) == 10:
                out.add(d)
    return out


def _yesterday_html(slug: str) -> str | None:
    """Вчерашняя версия листа. Сегодняшний файл уже пересобран, поэтому
    берём последнюю версию из git, сделанную до сегодняшнего утра."""
    today = date.today().isoformat()
    try:
        log_out = subprocess.run(
            ["git", "log", "--format=%H %aI", "--", f"docs/{slug}.html"],
            capture_output=True, text=True, timeout=30,
            cwd=DOCS.parent).stdout.splitlines()
    except Exception:
        return None
    for line in log_out:
        sha, when = (line.split(" ", 1) + [""])[:2]
        if when[:10] < today:
            try:
                return subprocess.run(["git", "show", f"{sha}:docs/{slug}.html"],
                                      capture_output=True, text=True, timeout=30,
                                      cwd=DOCS.parent).stdout
            except Exception:
                return None
    return None


def rest(slug: str, dialed: set[str] | None = None) -> tuple[list[str], int]:
    """(строки таблицы, которых вчера не набрали; сколько было всего)."""
    html = _yesterday_html(slug)
    if not html:
        return [], 0
    dialed = _dialed() if dialed is None else dialed
    rows = re.findall(r"<tr>(?!<th).*?</tr>", html, re.S)
    keep, total = [], 0
    for row in rows:
        m = re.search(r"\+7(\d{10})", row)
        if not m:
            continue
        total += 1
        if m.group(1) not in dialed:
            keep.append(row)
    return keep, total


CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:12px;color:#222}
h1{font-size:19px;margin:0 0 3px} .sub{color:#666;font-size:12px;margin-bottom:9px}
table{border-collapse:collapse;width:100%} thead{display:table-header-group}
th{background:#312783;color:#fff;font-size:11px;padding:5px 4px;text-align:left}
td{border-bottom:1px solid #ddd;padding:6px 4px;font-size:11pt;vertical-align:top}
.ph{font-size:12.5pt;font-weight:600;white-space:nowrap}
.ag{text-align:center;font-weight:600} .of{color:#1a6b1a;font-weight:600}
.res{width:170px;border-bottom:1px solid #999} .new{color:#B26F00;font-weight:600}
@media print{body{margin:6px}}
"""


def build() -> dict:
    dialed = _dialed()
    out = {}
    for slug, target, who, what in SHEETS:
        keep, total = rest(slug, dialed)
        out[who] = {"осталось": len(keep), "было": total}
        if not total:
            continue
        head = re.search(r"<thead>.*?</thead>", _yesterday_html(slug) or "", re.S)
        done = total - len(keep)
        body = (f"<style>{CSS}</style>"
                f"<h1>{who} — хвост вчерашнего листа ({what})</h1>"
                f"<div class=sub>{len(keep)} из {total}: вчера и сегодня этих "
                f"номеров не набирали ни разу. Пройдено {done} "
                f"({round(100 * done / total)}%). Строки те же, что вчера — "
                f"имя, возраст и куда звать не менялись. Печатать "
                f"в альбомной.</div><table>"
                + (head.group(0) if head else "")
                + "<tbody>" + "".join(keep) + "</tbody></table>")
        (DOCS / f"{target}.html").write_text(body, encoding="utf-8")
        log.info("%s.html: %d из %d", target, len(keep), total)
    return out


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "build" in sys.argv:
        print(json.dumps(build(), ensure_ascii=False))
        return
    dialed = _dialed()
    for slug, _t, who, _w in SHEETS:
        keep, total = rest(slug, dialed)
        if total:
            print(f"{who:6} прошли {total - len(keep):>3} из {total:>3} "
                  f"({round(100 * (total - len(keep)) / total)}%), "
                  f"осталось {len(keep)}")


if __name__ == "__main__":
    main()
