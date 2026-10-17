# -*- coding: utf-8 -*-
"""Клиент Hooppy (app/hooppy.py) без сети: какие запросы уходят и что из ответа берём.

17.10.2026, Борис: «давай hooppy.ru настроим». Формат — по hooppy.ru/api-docs.

  python3 docs/rabota/hooppy_testy.py
"""
import sys
from datetime import datetime

sys.path.insert(0, "/home/user/kidsup")

from app import hooppy as hp  # noqa: E402

oshibok = 0


def proverka(nazvanie, ok):
    global oshibok
    oshibok += not ok
    print(("  OK   " if ok else "  ОШИБКА "), nazvanie)


class Otvet:
    def __init__(self, code, body):
        self.status_code, self._b = code, body
        self.text = str(body)

    def json(self):
        return self._b


zaprosy = []
otvety = []


def lzhe_zapros(method, url, **kw):
    zaprosy.append((method, url, kw))
    return otvety.pop(0)


hp.httpx.request = lzhe_zapros
hp._token = lambda: "test-token"

print("Тело поста")
b = hp.telo_posta("Свободные места на неделе\n\nПервое занятие — условно-бесплатное", stranicy_ids=[822454, 22543])
proverka("сразу, в выбранные страницы", b["publication_when_type"] == 1 and b["publication_how_type"] == 1
         and b["selected_pages_ids"] == [822454, 22543])
proverka("абзацы — <p>…</p>", b["texts"][0]["text"] == "<p>Свободные места на неделе</p><p>Первое занятие — условно-бесплатное</p>"
         and b["texts"][0]["source_id"] == 0)
proverka("спецсимволы экранируются", hp._abzacy("5 < 7 & «да»") == "<p>5 &lt; 7 &amp; «да»</p>")
b = hp.telo_posta("Анонс", nabor_id=92384, kogda=datetime(2026, 10, 25, 7, 30))
proverka("в момент — дата и часы строками", b["publication_when_type"] == 2 and b["publication_how_type"] == 2
         and b["project_id"] == 92384 and b["publication_date"] == {"date": "25.10.2026", "hours": "07", "minutes": "30"})
b = hp.telo_posta("Совет дня", raspisanie_ids=[101820])
proverka("в расписание", b["publication_when_type"] == 3 and b["schedules_ids"] == [101820])
foto = {"id": "abc", "type": "photo", "name": "abc.png"}
b = hp.telo_posta("", foto=[foto], stranicy_ids=[1], ssylka="https://kidsup.ru")
proverka("фото без текста и ссылка", b["texts"] == [] and b["attachments"] == [
    {"type": "photos", "data": [foto]}, {"type": "link", "data": "https://kidsup.ru"}])
for nazv, kw in (("пустой пост", {"stranicy_ids": [1]}), ("без страниц и набора", {})):
    try:
        hp.telo_posta("" if nazv == "пустой пост" else "текст", **kw)
        proverka(f"{nazv} — ошибка", False)
    except hp.HooppyError:
        proverka(f"{nazv} — ошибка", True)

print("Запросы")
otvety[:] = [Otvet(200, {"list": [{"id": 822454, "source_id": 1, "social_page_name": "KidsUP", "social_page_link": "https://vk.ru/kidsup_ru"}],
                         "is_has_more": True}),
             Otvet(200, {"list": [{"id": 22543, "source_id": 9, "social_page_name": "KidsUP_ru"}], "is_has_more": False})]
s = hp.stranicy()
proverka("страницы собираются со всех листов", [x["id"] for x in s] == [822454, 22543] and s[0]["set"] == 1)
proverka("токен — в заголовке Bearer", zaprosy[0][2]["headers"]["Authorization"] == "Bearer test-token")
otvety[:] = [Otvet(200, {"id": 533241})]
proverka("пост возвращает id", hp.post("текст", stranicy_ids=[822454]) == 533241)
proverka("POST /posts с телом JSON", zaprosy[-1][0] == "POST" and zaprosy[-1][1].endswith("/api/posts")
         and zaprosy[-1][2]["json"]["selected_pages_ids"] == [822454])
otvety[:] = [Otvet(500, {"message": "No pages selected."})]
try:
    hp.post("текст", stranicy_ids=[1])
    proverka("ошибка сервера — с текстом причины", False)
except hp.HooppyError as e:
    proverka("ошибка сервера — с текстом причины", "No pages selected." in str(e))
otvety[:] = [Otvet(200, {"list": [{"is_error": 1, "operation_date": "05.10.2026, 12:30", "source_id": 1,
                                   "data": "Нет прав для публикации в данную группу.", "page": {"social_page_name": "KidsUP"}},
                                  {"is_error": 0, "data": "ок"}]})]
o = hp.oshibki()
proverka("журнал: только ошибки, с названием страницы", len(o) == 1 and o[0]["stranica"] == "KidsUP"
         and "Нет прав" in o[0]["chto"])

print("\nошибок:", oshibok)
sys.exit(1 if oshibok else 0)
