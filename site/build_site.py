#!/usr/bin/env python3
"""Сборка нового сайта kidsup.ru: подставляет логотипы base64 в шаблон.

Запуск из корня репозитория:  python3 site/build_site.py
Результат: site/kidsup_site.html — готовый одностраничник, можно класть
на любой хостинг (или публиковать как артефакт для согласования).

Интеграции внутри шаблона:
- форма → POST https://app.kidsup.ru/api/public/lead (карточка в МойКласс,
  задача дежурному админу, ТГ-уведомление); запасной путь — WhatsApp;
- бейджи «осталось N мест» ← GET https://app.kidsup.ru/api/public/schedule;
- Roistat: счётчик проекта 228571 (подмена номеров + roistat_visit в заявке);
- плавающая кнопка WhatsApp (79160170918), квиз подбора занятия.
"""
import base64
from pathlib import Path

root = Path(__file__).resolve().parent.parent
tpl = (Path(__file__).parent / "kidsup_site_body.html").read_text(encoding="utf-8")
for ph, png in (("LOGO_COLOR", "logo_color.png"), ("LOGO_WHITE", "logo_white.png")):
    b64 = base64.b64encode((root / "app" / "static" / png).read_bytes()).decode()
    tpl = tpl.replace(ph, "data:image/png;base64," + b64)
out = Path(__file__).parent / "kidsup_site.html"
out.write_text(tpl, encoding="utf-8")
print("собрано:", out, out.stat().st_size, "байт")
