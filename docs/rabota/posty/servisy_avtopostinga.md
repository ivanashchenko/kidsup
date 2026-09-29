# Сервисы автопостинга с поддержкой ВК (исследование 29.09.2026)

## Итог: сервисы автопостинга с поддержкой ВК для KidsUP (проверено 29.09.2026)

Главное:
- **Для ИИ-агента подходят три сервиса:** Postmypost, Onlypult и SmmBox. У всех трёх есть открытый API, через который программа сама создаёт посты и истории в ВК, Telegram и Instagram. Instagram у них работает через официальный API Meta, без риска блокировки за эмуляцию.
- **Истории WhatsApp официально не автоматизируются никак.** Способ есть только неофициальный, с риском бана номера. Подробнее в разделе 3.
- **Публиковать в ВК со своего сервера напрямую сложно.** По документации ВК метод публикации на стене (wall.post) требует право `wall`, а его выдают «в исключительных случаях» по письму в поддержку. Истории можно публиковать ключом сообщества. Публичного метода для клипов в документации нет. Поэтому ВК и Instagram лучше вести через сервис.

Цены — на 29.09.2026, взяты с официальных сайтов. «Не подтверждено» значит, что на официальном источнике я этого не нашёл.

### 1. Сводная таблица

| Сервис | ВК (посты / истории / клипы) | Telegram | Instagram (способ) | WhatsApp / MAX | Публичный API | Цена от | Пробный период | Заметки |
|---|---|---|---|---|---|---|---|---|
| **Postmypost** | да / да / да, карусель ВК через API | да | официальный API: посты, карусели, Stories, Reels | WA нет / MAX да | **да**: REST API v4.1 и MCP-сервер (help.postmypost.io/docs/api); типы публикаций в API: пост, история, reels/клипы | минимум 5 аккаунтов. API только на тарифе Advanced: ≈1 269 ₽/мес (публикация + модуль с API) или ≈1 490 ₽/мес со всеми модулями. Standard ≈1 190 ₽, но без API. Цены посчитаны по формуле калькулятора с сайта | 7 дней без карты | юрлицо — ТОО «ПОСТМАЙПОСТ» (Казахстан). Российские карты, СБП и безнал для юрлиц РФ заявлены на их форуме idea.postmypost.io. Есть согласование, календарь, аналитика, ответы на комментарии |
| **Onlypult** | да / да (`is_story`) / клипы не подтверждены; карусель или сетка через API | да | только официальный (подключение через Instagram или Facebook): посты, карусели, Stories, Reels | WA нет / MAX да | **да**: REST API (api.onlypult.com/v1, ключ `op_…`) и MCP; «на любом тарифе», в том числе на пробном | Start: 5 аккаунтов, 2 пользователя — $25 ≈ 2 110 ₽/мес; ≈1 477 ₽/мес при оплате за год | 7 дней без карты | цены на сайте есть в рублях. Юрлицо и оплата российской картой не подтверждены |
| **SmmBox** | да / да (с кнопками историй) / да; карусель через API | да | «только официальные API» | WA нет / MAX да | **да**: OpenAPI (smmbox.com/api), есть флаги `stories` и `reels`, отложенная дата. Доступен **с тарифа Эксперт** | Старт 500 ₽ (3 страницы, без API); Эксперт 1 700 ₽ (50 страниц, с API); при оплате за год −30% | пробного периода нет, есть «100% гарантия возврата» | ИП в Вологде, договор и безнал. Заявлена работа с n8n, Zapier и IFTTT |
| **LiveDune** | да / да (истории в календаре) / не подтверждено | да | официальный API: посты, карусели, Reels. Stories — противоречие: в FAQ «нельзя», в API есть тип `stories` | WA нет / MAX да | **да**: api.livedune.com, есть методы создания, изменения и удаления отложенных постов (`POST /posting/{id}`, тип post/reels/stories). Квота запросов от 2 тыс. на тарифе Блогер | Блогер 2 300 ₽/мес (5 аккаунтов, 1 пользователь); при оплате за год −10% | 7 дней | ООО «Дюна» (РФ), оплата в рублях, счёт для юрлиц. Сильная аналитика, мониторинг комментариев, согласование |
| **SMMplanner** (в январе 2026 к нему присоединился **Amplifr**) | да / да / не подтверждено; карусель да | да (альбомы, кнопки) | два способа: официальный API (бизнес-аккаунт) **или** эмуляция Android-устройства (есть риск блокировки) | WA нет / MAX да | **нет**: публичного API для клиентов не найдено. Коннекторы на ApiX-Drive/ApiMonster помечены «скоро» | Начальный 990 ₽/мес (5 страниц), 660 ₽/мес при оплате за год. Профи 1 350 / 900 ₽. Бизнес 3 375 / 2 250 ₽ | 7 дней | ИП (РФ). Лидер рынка по интерфейсу, но ИИ-агенту подключиться не к чему |
| **Hooppy** (нашёл дополнительно) | да / да / да (на тарифе Профи); карусель | да (бот или от имени аккаунта) | официальный (через Instagram или Facebook) **или** неофициальный через cookie | **WA: посты и истории** через шлюзы Green-API или Wappi.pro (неофициально). MAX да | **да**: openapi.yaml есть флаги `publish_as_story`, `publish_as_clips`, публикация по дате. На каком тарифе API — не подтверждено | Базовый 499 ₽; Профи 999 ₽ (клипы ВК, MAX). Лимиты аккаунтов не подтверждены | кнопка «Попробовать бесплатно», условия не подтверждены | маленький сервис с российским телефоном. Единственный, где в одном месте есть ВК, TG, IG и WhatsApp |
| **Roboposting** | только посты | да | заявлен | нет | API «в тестовом режиме, только текст», в описании ещё Google+ | 470 ₽ (5 аккаунтов) | 1 неделя | устарел, не рекомендую |
| **PostHunter** (TargetHunter) | посты ВК (истории и клипы не подтверждены) | да | нет | нет | не найден | не подтверждено | не подтверждено | уклон в маркировку рекламы (ОРД) |
| **NovaPress** | не подтверждено | не подтверждено | не подтверждено | — | не найден | ≈125–150 ₽ за аккаунт (по агрегаторам) | есть (по агрегаторам) | novapress.com открывается, но возможности не видны. Считать неактуальным |
| **Parasite (parasite.io)** | — | — | — | — | — | — | — | домен выставлен на продажу, сервиса нет |
| **PostingPRO (posting.pro)** | — | — | — | — | — | — | — | домен истёк; postingpro.ru — посторонний сайт про Авито |
| **Pepper.ninja** | парсер аудитории, автопостинг не подтверждён | — | — | — | — | — | — | не для нашей задачи |
| **«Облако постинга», «SMM-планировщик VK Рекламы»** | не нашёл таких продуктов | | | | | | | |
| **Kontentino, Buffer, Hootsuite, Later, Planable** | **ВК нет** (на сайтах нет ни ВК, ни Telegram) | нет | официальный API | нет | у части есть | в долларах или евро | есть | российские карты не принимают, нам не подходят |
| **Встроенные средства ВК** | вручную: отложенные записи. Через API: `wall.post` с `publish_date` — но нужен ключ пользователя с правом `wall`, которое выдают «в исключительных случаях». Истории: `stories.*` работают с ключом сообщества (право `stories`), отложенной даты нет. Клипы: публичного метода нет | — | — | — | VK API | бесплатно | — | обновление методов от 04.06.2026: авто-кадрирование в карусели |
| **Встроенные средства Telegram** | — | в приложении есть отложенные сообщения. В Bot API есть альбомы (`sendMediaGroup`), но **нет отложенной отправки**: расписание держит сам агент. Истории каналов через Bot API нельзя (`postStory` только для бизнес-аккаунтов) | — | — | Bot API | бесплатно | — | |
| **Официальные API Instagram и MAX** | — | — | Instagram API: посты, карусели, Reels, Stories; лимит 100 публикаций за 24 часа; своего расписания нет | MAX Bot API: публикация в каналы | да | бесплатно | — | |

### 2. Коротко о каждом сервисе

- **Postmypost.** Лучше всех покрывает нужные форматы: API и MCP, в API явные типы «история» и «reels/клипы», карусель ВК, Instagram через официальный API, есть MAX. Минусы: API только на старшем тарифе Advanced, юрлицо казахстанское, документация API частично грузится скриптами.
- **Onlypult.** Самый удобный для разработчика: понятная документация, спецификация OpenAPI, MCP-сервер для ИИ-агентов, API на любом тарифе и даже на пробном. Истории поддерживаются для Instagram, Facebook и ВК, Instagram только официально. Минусы: клипы ВК через API не подтверждены, цена около 2,1 тыс. ₽ в месяц, оплата российской картой не подтверждена.
- **SmmBox.** Российский, дешёвый, в API есть истории ВК с кнопками, клипы, карусель и дата публикации. Минусы: API только с тарифа Эксперт (1 700 ₽), пробного периода нет (только возврат денег), слабее по согласованию и аналитике.
- **LiveDune.** Лучшая аналитика и мониторинг комментариев, ООО в РФ, в API есть создание отложенных постов (включая stories и reels). Минусы: дороже (2 300 ₽), клипы ВК не подтверждены, про Stories в Instagram сайт противоречит сам себе.
- **SMMplanner (+Amplifr).** Лучший интерфейс и конструктор историй, есть истории ВК и MAX. Но **публичного API нет**, и в Instagram есть режим эмуляции устройства. Для ИИ-агента не подходит.
- **Hooppy.** Дёшево (499–999 ₽), в API есть истории и клипы ВК. Это единственный сервис, где есть WhatsApp, включая истории, но через неофициальные шлюзы. Минусы: маленькая компания, есть неофициальный режим Instagram через cookie. Годится как запасной вариант или отдельно для WhatsApp.
- **Roboposting, PostHunter, NovaPress, Parasite, PostingPRO, Pepper.ninja, западные сервисы:** не подходят по причинам из таблицы.

### 3. Вывод

**Топ-3 для сценария «ИИ-агент сам готовит и публикует посты и истории в ВК, TG и Instagram через API»:**
1. **Postmypost** (Advanced, 5 аккаунтов, ≈1,3–1,5 тыс. ₽/мес). Полнее всех по форматам: пост, история, клип ВК, Reels и Stories Instagram, TG, MAX. Есть API и MCP.
2. **Onlypult** (≈2,1 тыс. ₽/мес). Самый удобный API и MCP для агента, доступен на любом тарифе, Instagram только официально. Клипы ВК надо проверить на пробном периоде.
3. **SmmBox** (Эксперт, 1 700 ₽/мес). Российский и недорогой, в API есть истории и клипы ВК. Пробного периода нет, только гарантия возврата.

Практическая схема: Telegram и MAX агент может вести напрямую через Bot API (бесплатно; расписание держит сам). Сервис нужен прежде всего для ВК (из-за ограничений на `wall.post` и клипы) и для Instagram. Перед оплатой стоит на пробном периоде Postmypost и Onlypult проверить на реальном сообществе KidsUP историю ВК, клип ВК и Stories Instagram через API.

**Что делать с историями (статусами) WhatsApp:**
- Официальный WhatsApp Business API статусы не поддерживает. Официального публичного API Meta для постинга в каналы WhatsApp я тоже не нашёл.
- Неофициальные шлюзы умеют публиковать статусы: Green-API (методы `SendTextStatus` и `SendMediaStatus`, тариф «Бизнес» 690 ₽/мес) и Wappi.pro (от 550–700 ₽/мес); Hooppy работает поверх них. Но это эмуляция WhatsApp Web с риском бана. Под угрозой окажется именно основной номер центра: статусы видят только контакты, сохранившие этот номер.
- **Рекомендация:** не автоматизировать основной номер. Пусть агент готовит картинку или видео и текст для статуса и присылает администратору (в Telegram или MAX) с напоминанием, а администратор публикует с телефона за 30 секунд. Green-API или Hooppy имеет смысл пробовать только на отдельном, некритичном номере.

### 4. Источники
- **Postmypost:** https://postmypost.io/prices, https://postmypost.io/ru/prices/ (формула цен в скрипте https://postmypost.io/build/assets/main-ButG0p24.js), https://help.postmypost.io/docs/api/create-publication, https://help.postmypost.io/docs/mcp/, https://postmypost.io/ru/vkontakte, https://postmypost.io/ru/instagram, https://postmypost.io/ru/max, https://idea.postmypost.io/b/payment
- **Onlypult:** https://onlypult.com/ru/pricing, https://onlypult.com/ru/pages/social-media-api-mcp, https://onlypult.com/dev/index.html, https://onlypult.com/dev/openapi.yaml
- **SmmBox:** https://smmbox.com/, https://smmbox.com/api/ (спецификация https://smmbox.com/api/spec/openapi.yaml), https://smmbox.com/blog/work/api-smmbox-avtomatizatsiya-publikatsii-v-sotsset/
- **LiveDune:** https://livedune.com/ru/services/autoposting/, http://livedune.com/ru/services/autoposting/vk/, https://livedune.com/ru/pricing/, https://api.livedune.com/docs/index.html (спецификация https://api.livedune.com/docs/openapi.json), https://livedune.com/ru/blog/informaciya_dlya_yuridicheskih_lic
- **SMMplanner / Amplifr:** https://smmplanner.com/ (раздел #price), https://smmplanner.com/posts, https://faq.smmplanner.com/ru/articles/5051473, https://faq.smmplanner.com/ru/articles/5480887, https://apix-drive.com/en/integrations/smmplanner, https://amplifr.com/ru/ («Amplifr переехал в SMMplanner», 19.01.2026)
- **Hooppy:** https://hooppy.ru/, https://hooppy.ru/openapi.yaml, https://blog.hooppy.ru/podklyuchenie-insty/
- **Roboposting:** https://roboposting.ru/price, https://roboposting.ru/posting-api
- **PostHunter:** https://post.targethunter.help/, https://post.targethunter.help/llms.txt
- **NovaPress:** https://novapress.com, https://picktech.ru/product/novapress-publisher/, https://elama.ru/blog/zamechatelno-vyhodit-8-servisov-dlya-otlozhennogo-postinga/ (обзор от 29.01.2026)
- **Parasite и PostingPRO:** https://parasite.io (переадресация на страницу продажи домена GoDaddy), https://posting.pro (истёкший домен)
- **Kontentino, Buffer, Hootsuite, Later, Planable:** https://www.kontentino.com/, https://buffer.com/, https://www.hootsuite.com/, https://later.com/, https://planable.io/
- **VK API:** https://dev.vk.com/ru/method/wall.post, https://dev.vk.com/ru/method/stories.getPhotoUploadServer, https://dev.vk.com/ru/method/stories.getVideoUploadServer, https://dev.vk.com/ru/method/stories.save, https://dev.vk.com/ru/method/video.save
- **Telegram:** https://core.telegram.org/bots/api, https://core.telegram.org/bots/api-changelog
- **Instagram:** https://developers.facebook.com/docs/instagram-platform/content-publishing
- **MAX:** https://dev.max.ru/docs-api
- **WhatsApp:** https://green-api.com/, https://green-api.com/docs/api/statuses/, https://wappi.pro/, https://whapi.cloud/whatsapp-status-api, https://gnew.com.br/blog/artigo/whatsapp-api-postar-status-oficial-limitacoes-alternativas-empresas/