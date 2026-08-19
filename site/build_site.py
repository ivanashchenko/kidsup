#!/usr/bin/env python3
"""Сборка сайтов KidsUP: подставляет логотипы и фото как data-URI.

Запуск из корня репозитория:  python3 site/build_site.py

Собирает три страницы:
    site/kidsup_site.html   → kidsup.ru        (главная)
    site/kidsupday.html     → kidsupday.ru     (день открытых дверей 30.08)
    site/kidsupweek.html    → kidsupweek.ru    (неделя открытых уроков 31.08–06.09)

Каждая — самодостаточный файл: логотипы и фото вшиты, внешних зависимостей
нет кроме Google Fonts, Метрики, Roistat и пикселя ВК. Копии кладутся
в app/static/, чтобы портал отдавал их на /site, /day и /week — так живые
интеграции можно проверить до переноса доменов.

Интеграции внутри шаблонов:
- форма → POST https://app.kidsup.ru/api/public/lead (карточка в МойКласс,
  задача дежурному админу, ТГ-уведомление); запасной путь — WhatsApp;
- свободные места ← GET https://app.kidsup.ru/api/public/schedule;
- Roistat: счётчик проекта 228571 (подмена номеров + roistat_visit в заявке);
- Яндекс.Метрика 69569509 с целью lead.
"""
import base64
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC = ROOT / "app" / "static"

# (исходник, имя собранного файла, куда положить копию для портала)
PAGES = [
    ("kidsup_site_body.html", "kidsup_site.html", "site.html"),
    ("kidsupday_body.html", "kidsupday.html", "day.html"),
    ("kidsupweek_body.html", "kidsupweek.html", "week.html"),
]

_cache: dict[str, str] = {}


def _data_uri(path: Path, mime: str) -> str:
    key = str(path)
    if key not in _cache:
        _cache[key] = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()
    return _cache[key]


def build(src: Path) -> str:
    tpl = src.read_text(encoding="utf-8")
    for ph, png in (("LOGO_COLOR", "logo_color.png"), ("LOGO_WHITE", "logo_white.png")):
        if ph in tpl:
            tpl = tpl.replace(ph, _data_uri(STATIC / png, "image/png"))
    # фото педагогов и занятий: PHOTO_T_DUDUEVA -> site/assets/t_dudueva.jpg
    for ph in set(re.findall(r"PHOTO_[A-Z_]+", tpl)):
        tpl = tpl.replace(ph, _data_uri(HERE / "assets" / (ph[6:].lower() + ".jpg"), "image/jpeg"))
    return tpl


def main() -> None:
    STATIC.mkdir(parents=True, exist_ok=True)
    for body, out_name, static_name in PAGES:
        src = HERE / body
        if not src.exists():
            print(f"пропущено: нет {src.name}")
            continue
        html = build(src)
        out = HERE / out_name
        out.write_text(html, encoding="utf-8")
        shutil.copyfile(out, STATIC / static_name)
        print(f"собрано: {out.name} ({out.stat().st_size:,} байт) → app/static/{static_name}"
              .replace(",", " "))


if __name__ == "__main__":
    main()
