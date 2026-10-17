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
b = hp.telo_posta("Сторис", nabor_id=7, nastrojki={"publish_as_story": 1, "publish_as_story_source_ids": "1,9"})
proverka("флаги публикации — вложением settings", b["attachments"] == [
    {"type": "settings", "data": {"publish_as_story": 1, "publish_as_story_source_ids": "1,9"}}])
for nazv, kw in (("пустой пост", {"stranicy_ids": [1]}), ("без страниц и набора", {}),
                 ("одни флаги без текста и картинки", {"stranicy_ids": [1], "nastrojki": {"pin_post": 1}})):
    try:
        hp.telo_posta("текст" if nazv == "без страниц и набора" else "", **kw)
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

print("Набор и расписание")
shablon = {"id": 0, "name": "", "publication_where_type": 1, "watermark_id": 0, "posts_caption": "",
           "tg_buttons": {"list": []}, "selected_pages_by_source_ids": {}, "social_pages_by_accounts": ["…"],
           "watermarks": [], "projects": []}
stranicy_otvet = Otvet(200, {"list": [{"id": 822454, "source_id": 1}, {"id": 22543, "source_id": 9}],
                             "is_has_more": False})
zaprosy.clear()
otvety[:] = [Otvet(200, dict(shablon)), stranicy_otvet, Otvet(200, {"id": 412})]
proverka("набор создаётся, id из ответа", hp.sozdat_nabor("KidsUP — все сети", [822454, 22543]) == 412)
telo = zaprosy[-1][2]["json"]
proverka("набор: страницы по сетям, поля шаблона на месте, справочники убраны",
         telo["selected_pages_by_source_ids"] == {"1": [822454], "9": [22543]} and telo["name"] == "KidsUP — все сети"
         and telo["tg_buttons"] == {"list": []} and "social_pages_by_accounts" not in telo and "id" not in telo)
otvety[:] = [Otvet(200, dict(shablon)), stranicy_otvet]
try:
    hp.sozdat_nabor("x", [999])
    proverka("чужая страница в наборе — ошибка", False)
except hp.HooppyError as e:
    proverka("чужая страница в наборе — ошибка", "999" in str(e))
otvety[:] = [Otvet(200, dict(shablon)), Otvet(200, {"id": 2871})]
nedelya = [["10:00"], [], ["10:00", "19:30"], [], [], ["9:05"], []]
proverka("расписание создаётся", hp.sozdat_raspisanie("Посты KidsUP", nedelya, 412) == 2871)
telo = zaprosy[-1][2]["json"]
proverka("расписание — остановлено, через набор, время с ведущим нулём",
         telo["state"] == 3 and telo["publication_how_type"] == 2 and telo["project_id"] == 412
         and telo["times"][2] == [{"hours": "10", "minutes": "00"}, {"hours": "19", "minutes": "30"}]
         and telo["times"][5] == [{"hours": "09", "minutes": "05"}] and len(telo["times"]) == 7)
for nazv, vr in (("не 7 дней", [["10:00"]]), ("кривое время", [["25:00"], [], [], [], [], [], []])):
    try:
        hp.sozdat_raspisanie("x", vr, 1)
        proverka(f"расписание: {nazv} — ошибка", False)
    except hp.HooppyError:
        proverka(f"расписание: {nazv} — ошибка", True)

print("Удаление и запуск")
zaprosy.clear()
otvety[:] = [Otvet(200, {"success": True}), Otvet(200, {"success": True}), Otvet(200, {"success": True})]
hp.udalit_posty([98891140, 98891141])
hp.zapustit_raspisaniya([110331])
hp.ostanovit_raspisaniya([110331])
proverka("удаление — id строкой через запятую", zaprosy[0][1].endswith("/posts/batch/delete")
         and zaprosy[0][2]["json"] == {"ids": "98891140,98891141"})
proverka("запуск и остановка расписания", zaprosy[1][1].endswith("/posts/schedules/batch/start")
         and zaprosy[2][1].endswith("/posts/schedules/batch/stop") and zaprosy[1][2]["json"] == {"ids": "110331"})
try:
    hp.udalit_posty([])
    proverka("пустой список — ошибка, без запроса", False)
except hp.HooppyError:
    proverka("пустой список — ошибка, без запроса", len(zaprosy) == 3)

print("Кнопка «Опубликовать» на /makety (app/publish.py)")
from app import publish as pub  # noqa: E402
nastr = {}
pub.db.get_setting = lambda k: nastr.get(k)
r = pub.publish(b"PNG", "Подпись", ["hooppy"])
proverka("без набора — понятная ошибка, ничего не отправлено",
         not r["hooppy"]["ok"] and "hooppy_nabor" in r["hooppy"]["msg"] and not otvety)
nastr["hooppy_nabor"] = "92384"
zaprosy.clear()
otvety[:] = [Otvet(200, {"photo": foto}), Otvet(200, {"id": 533300})]
r = pub.publish(b"PNG", "Свободные места", ["hooppy"])
proverka("картинка загружается, пост уходит в набор", r["hooppy"]["ok"] and "533300" in r["hooppy"]["msg"]
         and zaprosy[0][1].endswith("/files/media/upload") and zaprosy[1][2]["json"]["project_id"] == 92384
         and zaprosy[1][2]["json"]["attachments"] == [{"type": "photos", "data": [foto]}])
otvety[:] = [Otvet(403, {"message": "Tariff expired."})]
r = pub.publish(b"PNG", "x", ["hooppy"])
proverka("отказ Hooppy — в ответе кнопки", not r["hooppy"]["ok"] and "Tariff expired." in r["hooppy"]["msg"])

print("Истории (Борис 17.10: ВК, Instagram, ТГ-канал, ТГ-аккаунт, WhatsApp)")
zaprosy.clear()
otvety[:] = []
r = pub.publish(b"PNG", "", ["hooppy_story"])
proverka("без набора историй — понятная ошибка, ничего не отправлено",
         not r["hooppy_story"]["ok"] and "hooppy_nabor_istorii" in r["hooppy_story"]["msg"] and not zaprosy)
nastr["hooppy_nabor_istorii"] = "92400"
otvety[:] = [Otvet(200, {"photo": foto}), Otvet(200, {"id": 533301})]
r = pub.publish(b"PNG", "", ["hooppy_story"])
telo = zaprosy[1][2]["json"]
proverka("история уходит в свой набор с флагом историй во всех сетях",
         r["hooppy_story"]["ok"] and telo["project_id"] == 92400 and telo["texts"] == []
         and {"type": "settings", "data": {"publish_as_story": 1,
                                           "publish_as_story_source_ids": "1,2,7,9,10,22"}} in telo["attachments"])
proverka("в ленту история не дублируется",
         "share_stories_to_feed" not in hp.nastrojki_istorii())

print("\nошибок:", oshibok)
sys.exit(1 if oshibok else 0)
