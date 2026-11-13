"""Меню app.kidsup.ru — один источник для общей шапки, пульта и страницы «Архив».

12.11.2026, Борис: «Давай уже сейчас делать!! И главное скрой не нужные / не актуальные /
старые страницы». До этого дня в шапке стояло 33 пункта, из них около двадцати — страницы
разовых кампаний августа–сентября, а пульта, бланков групп и списков админов в меню не
было вовсе. Теперь в меню только то, чем работают каждый день: 16 пунктов у админов,
у владельца — они же плюс свой раздел.

Раздел владельца админу не рисуется совсем (а не прячется стилем): его нет в menu("admin").
Педагогу, открывшему страницу по ключу из ссылки, меню не положено — menu("pedagog") пуст.

Меню — не защита: оно только показывает или не показывает ссылку. Кто что может открыть,
решают AUTH и OWNER_AUTH на самих маршрутах (app/main.py).

Модуль ничего не берёт из main.py: его импортируют и main.py, и pult.py.
"""
from __future__ import annotations

import html
import re
from urllib.parse import quote

# Разделы админов: (название, [(пункт, адрес), …]). Порядок — как идёт смена:
# сначала то, что открыто весь день, потом группы, деньги и справка.
ADMIN = [
    ("Смена", [("Пульт", "/pult"), ("Ответы", "/waiting"), ("Линия", "/liniya"),
               ("Обзвон", "/obzvon-dnya"), ("Явка", "/yavka")]),
    ("Группы", [("Подбор группы", "/enrollment"), ("Места", "/mesta"), ("Шахматка", "/grid"),
                ("Бланки групп", "/blank-grupp")]),
    ("Деньги", [("Без оплаты", "/voronka"), ("Долги", "/dolgi")]),
    ("Справка", [("Списки", "/admin/docs"), ("Описания занятий", "/courses"), ("Стоп-лист", "/otkazy"),
                 ("Промоутеры", "/promo/grafik"), ("База знаний", "/base")]),
]

# Раздел владельца — виден только под входом boris. В menu() он всегда последний:
# по этому месту шаблон и пульт отличают его от разделов админов.
OWNER = ("Владельцу", [
    ("Мои документы", "/boris/docs"), ("Мои дела", "/pult?who=Борис"),
    ("Контроль оплат", "/kontrol-oplat"), ("Сверка честности", "/sverka-chestnosti"),
    ("Аномалии", "/audit"), ("Журнал", "/journal"), ("Откуда заявки", "/istochniki"),
    ("Итоги промоутеров", "/promo"), ("Настройки", "/owner"), ("Камеры", "/kamery/nastroyka"),
    ("Архив", "/arhiv"),
])

# Что за страница — одной строкой для главной (app.kidsup.ru/). 12.11.2026, Борис о
# переходе главной на пульт: «Почему https://app.kidsup.ru/ под админами перекидывает на
# Пульт?! А подомной на документы владельца?!» — решил: главная — страница разделов.
OPIS = {
    "/pult": "дела смены по колонкам: сверху вниз, галочка сразу",
    "/waiting": "кто из семей ждёт ответа в переписке",
    "/liniya": "всё о семье по номеру: звонки, переписка, записи, оплаты",
    "/obzvon-dnya": "кому позвонить сегодня и что сказать",
    "/yavka": "кто был на занятиях, сверка с отчётами педагогов",
    "/enrollment": "куда записать ребёнка: группы по возрасту и уровню, свободные места",
    "/mesta": "сколько мест занято и свободно в каждой группе",
    "/grid": "все занятия недели по комнатам и часам",
    "/blank-grupp": "состав группы на сегодня: кто учится, кто на пробном",
    "/voronka": "кто был на пробном и ещё не оплатил",
    "/dolgi": "кто должен и сколько",
    "/admin/docs": "списки и инструкции, которые готовит Клод",
    "/courses": "описания занятий для родителей",
    "/otkazy": "кому не писать и не звонить",
    "/promo/grafik": "смены промоутеров и их контакты",
    "/base": "скрипты, правила, ответы на частые вопросы",
    "/boris/docs": "все ваши страницы: прибыль, зарплата, сверки, планы",
    "/pult?who=Борис": "ваши решения: 10 главных сверху",
    "/kontrol-oplat": "оплаты против банка и чеков",
    "/sverka-chestnosti": "правки в МойКлассе без объяснения",
    "/audit": "странности в данных МойКласса",
    "/journal": "кто что делал в смене",
    "/istochniki": "откуда приходят заявки",
    "/promo": "итоги промоутеров: выходы, контакты, записи",
    "/owner": "ключи, пароли, настройки сервера",
    "/kamery/nastroyka": "камеры в комнатах и кадры",
    "/arhiv": "старые страницы, которые убрали из меню",
}

# Страницы без пункта меню, которые нужны время от времени, — второй строкой на пульте.
# «скрипты» (/base/skripty_v3) убраны 12.11: сама база знаний держит их в «Архиве —
# устаревшее». Какие скрипты сейчас рабочие, решает владелец — тогда вернуть строкой сюда.
ESHCHE = [
    ("вернуть ушедших", "/obzvon"), ("карта развития", "/karta"),
    ("2–3 предмета в один вечер", "/constructor"),
    ("куда зовём, какие группы сливаем", "/base/gruppy_reshenia"),
]

# Страница «Архив» (только владельцу): (адрес, название, что это, почему в архиве).
# Сами страницы не удалены и открываются по адресу — на них ведут только эти строки.
ARHIV = [
    ("/students", "Ученики",
     "Вся база карточек МойКласса списком, с поиском и выгрузкой в CSV.",
     "Клиента находят поиском в шапке и «Линией». Страница и её CSV — только владельцу; "
     "сырую выгрузку /export/raw рутины читают под общим входом до шага 3."),
    ("/payments", "Платежи",
     "Журнал операций МойКласса за период с суммами по типам.",
     "За оплатами следят «Контроль оплат» и сверка оплат в документах владельца."),
    ("/content", "Тексты набора 2026/27",
     "Готовые тексты по каналам для августовской кампании набора.",
     "Кампания закончилась, даты и условия в текстах устарели — семьям без проверки не отправлять."),
    ("/callplan", "Обзвон «перезапись на 2026/27»",
     "Кого из прошлых лет звать в новый сезон, с отметками «прозвонён».",
     "Кампания августа; сегодняшние звонки идут через «Обзвон» и пульт."),
    ("/chaty", "Чаты групп английского",
     "Состав родительских чатов и ссылки-приглашения, сентябрь.",
     "Детей в чаты по утрам добавляет автопилот. На странице есть кнопка рассылки приглашений — "
     "без нужды не нажимать."),
    ("/nezvonili", "Кому не звонили",
     "Заявки прошлого учебного года, по которым не было ни одного звонка, — ответ на вопрос от 16.09.",
     "Разовый разбор, в ежедневной работе не используется."),
    ("/gruppy/media", "Фото и видео педагогов",
     "Материалы из рабочих чатов педагогов — отбор для постов и историй.",
     "Посты собирают рутины; на странице фото детей, поэтому она открыта только владельцу."),
    ("/prelaunch", "Проверка перед рассылкой",
     "Светофор перед массовой рассылкой: каналы, стоп-лист, предохранитель.",
     "Сделана под августовские рассылки; пригодится перед следующей массовой."),
    ("/nabor", "Где теряем набор",
     "Воронка по предметам и тёплые списки сезона, разбор от 15.09.",
     "Эти списки теперь стоят в пульте и на «Обзвоне», с отметкой итога звонка."),
]

# Страницы, которые переезжают на другие, но пока открываются по адресу: на каждой
# есть то, чего на новой странице ещё нет. (адрес, название, куда переезжает, адрес
# новой страницы, что там осталось). Блок уйдёт вместе со страницами на шаге 2.
PEREEZD = [
    ("/segodnya", "Горит сегодня", "Пульт", "/pult",
     "Первые занятия на сегодня и два дня вперёд с отметкой «звонили / писали»; занятия без отметок явки."),
    ("/spiski", "Списки по группам", "Бланки групп", "/blank-grupp",
     "Все группы дня по времени, с телефонами."),
    ("/zayavki", "Заявки сезона", "Бланки групп", "/blank-grupp",
     "Поимённый лист ожидания направлений, которых ещё нет в расписании."),
    ("/leads", "Лиды", "Откуда заявки", "/istochniki",
     "Заявки с сайта, не дошедшие до CRM, и выгрузка в CSV."),
    ("/clicks", "Клики по мессенджерам", "Откуда заявки", "/istochniki",
     "Построчный список переходов с сайта в мессенджеры."),
]

# 13.11.2026, шаг 2: эти страницы уже переехали — содержимое перенесено, старый адрес
# ведёт 303 на новое место. В PEREEZD их больше нет: там только то, что ещё открывается
# по адресу, и страница «Архив» не должна обещать «открывается здесь» про то, чего нет.
# (старый адрес, название, куда переехала, адрес нового места, что перенесено, когда)
PEREEHALI = [
    ("/settings", "Ключ МойКласса и выгрузка", "Настройки", "/owner#mk",
     "Ключ МойКласса, глубина выгрузки и кнопка «Проверить подключение».", "13.11.2026"),
    ("/metrics", "Дисциплина", "Журнал", "/journal#schetchiki",
     "Звонки и сообщения за день, записи по админам за 7 дней. Сроки (SLA) и «нагрузка» "
     "не перенесены: ими не пользовались.", "13.11.2026"),
]

# Куда идти вместо архивной страницы — ссылки в плашке вверху неё. Нет записи — замены нет.
VMESTO = {
    "/students": [("Линия", "/liniya")],
    "/payments": [("Контроль оплат", "/kontrol-oplat")],
    "/content": [("Подбор группы", "/enrollment"), ("Описания занятий", "/courses")],
    "/callplan": [("Обзвон", "/obzvon-dnya")],
    "/nezvonili": [("Обзвон", "/obzvon-dnya")],
    "/nabor": [("Обзвон", "/obzvon-dnya"), ("Пульт", "/pult")],
}
# Пункты владельца, которых админ не откроет: в плашке у админа на них не ссылаемся.
# «Мои дела» — это пульт, а «Итоги промоутеров» админы смотрят каждый день (решение 28.10).
TOLKO_VLADELCU = {adres for _, adres in OWNER[1]} - {"/pult?who=Борис", "/promo"}


def plashka(adres: str, rol: str) -> dict | None:
    """Плашка вверху архивной или переезжающей страницы.

    12.11.2026: эти страницы открываются по адресу — у админов они в закладках и в
    подсказках браузера, а ещё вчера стояли в меню. Без пометки «Фабрика контента»
    выглядит рабочей, и устаревшие условия уходят семье. Предупреждение из ARHIV
    раньше было только на /arhiv, которую видит один владелец."""
    for a, _, _, pochemu in ARHIV:
        if a == adres:
            return {"vid": "Архивная страница", "tekst": pochemu, "podpis": "В работе —", "kuda": VMESTO.get(a, [])}
    for a, _, kuda_imya, kuda, ostalos in PEREEZD:
        if a != adres:
            continue
        if rol != "owner" and kuda in TOLKO_VLADELCU:
            # новая страница админу не откроется — не зовём туда, где его уведут на пульт
            return {"vid": "Страница скоро переедет", "tekst": "Пока работает здесь.", "podpis": "", "kuda": []}
        return {"vid": "Страница переезжает",
                "tekst": f"Пока открывается здесь: на новой ещё нет — {ostalos[0].lower()}{ostalos[1:]}",
                "podpis": "Новая страница —", "kuda": [(kuda_imya, kuda)]}
    return None


def menu(rol: str) -> list:
    """Разделы меню для роли: "owner" — владелец, "admin" — админ, "pedagog" — педагог по ключу."""
    if rol == "pedagog":
        return []
    return ADMIN + ([OWNER] if rol == "owner" else [])


def _ssylki(punkty) -> str:
    return " · ".join(f"<a href='{html.escape(adres)}'>{html.escape(imya)}</a>" for imya, adres in punkty)


def stroka(rol: str) -> str:
    """Меню одним блоком для пульта: у него свой HTML, общей шапки на нём нет.
    Сам «Пульт» в строке не нужен — человек уже на нём."""
    chasti = []
    for nazvanie, punkty in menu(rol):
        punkty = [p for p in punkty if p[1] != "/pult"]
        chasti.append(f"<span class='razdel'><b>{html.escape(nazvanie)}:</b> {_ssylki(punkty)}</span>")
    return "<br>".join(chasti)


def eshche() -> str:
    """Вторая строка на пульте: нужные изредка страницы, которым не нашлось места в меню."""
    return _ssylki(ESHCHE)


# ---------------------------------------------------------------------------
# Шаг 2, 13.11.2026: одна шапка на всех рабочих страницах.
#
# Причина: после шага 1 меню было только на страницах через base.html. Около 16 страниц
# рисуют свой HTML (пульт, бланки групп, долги, стоп-лист, контроль оплат…) — на них
# стояла временная строка «← Пульт · Разделы» либо не было ничего, и с телефона с такой
# страницы некуда было уйти. Владелец 12.11 одобрил «новое меню и общую шапку» и нижнюю
# панель на телефоне.
#
# Шапка — <div id="kidsup-nav">, а не <header>: у /blank-grupp свой header{position:sticky},
# на /promo/grafik и /kamery свои <header>. Все классы — с префиксом kn-, стили и скрипт —
# в /static/nav.css и /static/nav.js. «Кто я» — полоска личного входа vhod.shapka()
# (её передаёт main.py готовой строкой): выбор «Я — …» через cookie не делаем, имя
# вошедшего сервер уже знает.
# ---------------------------------------------------------------------------

VERSIYA = "1"          # ?v= у nav.css и nav.js: менять вместе с файлами
KOREN = 'id="kidsup-nav"'

# Нижняя панель на телефоне: три частые страницы и «☰ Разделы». У «Ответов» — число
# ждущих (ставит nav.js из /api/waiting/schet). Пятая, «личная» кнопка по роли админа
# отложена до решения владельца о ролях (PLAN.md, риск 4).
PANEL = {
    "admin": [("Пульт", "/pult", "📋"), ("Ответы", "/waiting", "💬"), ("Линия", "/liniya", "📞")],
    "owner": [("Главная", "/", "🏠"), ("Мои дела", "/pult?who=Борис", "✅"),
              ("Контроль оплат", "/kontrol-oplat", "₽")],
}


def _e(s) -> str:
    return html.escape(str(s), quote=True)


def _tut(adres: str, path: str) -> bool:
    """Пункт — текущая страница. Сравнение точное: «/promo» и «/promo/grafik» — разные
    пункты, а «Мои дела» (/pult?who=Борис) не подсвечивается на общем пульте."""
    return adres == path


def _css() -> str:
    return f'<link rel="stylesheet" href="/static/nav.css?v={VERSIYA}">'


def _shapka_pedagoga(path: str, k: str) -> str:
    """Педагог открыл страницу по ключу из ссылки: одна строка, один пункт. Ни поиска,
    ни бейджа, ни панели, ни скрипта — шапка ничего не опрашивает (опросы /api без входа
    давали окно пароля каждые три минуты, 08.11). Ключ — только в ссылках и только
    проверенный: main.py передаёт его, если он совпал с настройкой pedagog_key."""
    adres = "/english/karta" + (f"?k={quote(str(k), safe='')}" if k else "")
    tut = " kn-tut" if path == "/english/karta" else ""
    tekushchaya = ' aria-current="page"' if tut else ""
    return (f'<div {KOREN} class="kn kn-ped">{_css()}<div class="kn-verh">'
            f'<a class="kn-logo" href="{_e(adres)}"><img src="/static/logo.svg" alt="" width="30" height="30">'
            f'<span>KidsUP</span><span class="kn-ped-podpis">· педагогам</span></a>'
            f'<a class="kn-ped-punkt{tut}" href="{_e(adres)}"'
            f'{tekushchaya}>Карточка речи</a></div></div>')


def shapka(rol: str, path: str, k: str = "", kto_html: str = "",
           schet_url: str = "/api/waiting/schet") -> str:
    """Общая шапка страницы под роль: "owner", "admin" или "pedagog".

    path — адрес страницы (request.url.path), по нему подсвечивается пункт и раскрывается
    раздел в листе на телефоне. kto_html — готовая полоска «Вы: Лена · Выйти / сменить»
    (vhod.shapka). schet_url — откуда бейдж берёт числа: только лёгкий счётчик из кэша,
    не /api/waiting (тот идёт в Mango до минуты; опрос с каждой вкладки положил бы сервер).

    Компьютер (от 920 px): строка с логотипом, поиском и бейджем, под ней разделы строкой.
    Телефон: одна строка сверху (не липкая), внизу панель из четырёх кнопок; «☰ Разделы»
    открывает лист на весь экран. Раскладку делает nav.css, разметка одна."""
    if rol == "pedagog":
        return _shapka_pedagoga(path, k)
    razdely = menu(rol)
    if not razdely:
        return ""
    # какой раздел раскрыть в листе: тот, где текущая страница; иначе первый
    tekushchiy = next((i for i, (_, punkty) in enumerate(razdely)
                       if any(_tut(a, path) for _, a in punkty)), 0)
    bloki = []
    for i, (nazvanie, punkty) in enumerate(razdely):
        vlad = rol == "owner" and i == len(razdely) - 1
        ssylki = "".join(
            f'<a href="{_e(a)}"' + (' class="kn-tut" aria-current="page"' if _tut(a, path) else "") + f">{_e(n)}</a>"
            for n, a in punkty)
        klass = "kn-razdel" + (" kn-vlad" if vlad else "") + (" kn-raskryt" if i == tekushchiy else "") \
                + (" kn-zdes" if any(_tut(a, path) for _, a in punkty) else "")
        bloki.append(
            f'<div class="{klass}"><button type="button" class="kn-razdel-imya" '
            f'aria-expanded="{"true" if i == tekushchiy else "false"}">{_e(nazvanie)}</button>'
            f'<div class="kn-punkty">{ssylki}</div></div>')
    knopki = "".join(
        f'<a class="kn-knopka{" kn-tut" if _tut(a, path) else ""}" href="{_e(a)}">'
        f'<span class="kn-znak" aria-hidden="true">{z}</span><span class="kn-podpis">{_e(n)}</span>'
        + ('<span class="kn-chislo" id="kn-chislo" hidden></span>' if a == "/waiting" else "") + "</a>"
        for n, a, z in PANEL.get(rol, PANEL["admin"]))
    kto = ""
    if kto_html:
        # жёлтую полосу общего входа на телефоне не прячем в лист: это предупреждение
        vazhno = " kn-kto-vazhno" if "obshchiy" in kto_html else ""
        kto = f'<div class="kn-kto{vazhno}">{kto_html}</div>'
    return (
        f'<div {KOREN} class="kn kn-rol-{_e(rol)}" data-schet="{_e(schet_url)}">{_css()}{kto}'
        '<div class="kn-verh">'
        '<a class="kn-logo" href="/" title="На свою главную"><img src="/static/logo.svg" alt="" width="30" height="30">'
        '<span>KidsUP</span></a>'
        '<div class="kn-poisk"><input type="search" id="gs-input" class="kn-poisk-pole" '
        'placeholder="Поиск: группа, ребёнок, телефон…" autocomplete="off" spellcheck="false" '
        'aria-label="Поиск по группам, детям и телефонам">'
        '<div id="gs-drop" class="kn-drop" hidden></div></div>'
        '<button type="button" class="kn-lupa" id="kn-lupa" aria-label="Поиск" aria-expanded="false">🔍</button>'
        '<a id="wait-badge" class="kn-badge" href="/waiting" hidden></a>'
        '</div>'
        '<div id="nav-razdely" class="kn-razdely">'
        '<div class="kn-list-verh"><b>Разделы</b><span class="kn-list-kto" id="kn-list-kto"></span>'
        '<button type="button" class="kn-zakryt" id="kn-zakryt" aria-label="Закрыть разделы">×</button></div>'
        + "".join(bloki) +
        '</div>'
        f'<nav class="kn-panel" aria-label="Быстрые кнопки">{knopki}'
        '<button type="button" class="kn-knopka" id="nav-toggle" aria-expanded="false" aria-controls="nav-razdely">'
        '<span class="kn-znak" aria-hidden="true">☰</span><span class="kn-podpis">Разделы</span></button></nav>'
        f'<script src="/static/nav.js?v={VERSIYA}" defer></script>'
        '</div>')


def doc_bar(nazad_url: str, nazad_label: str, podpis: str = "") -> str:
    """Тонкая строка над документом (/boris/docs/…, /admin/docs/…, /base/…): «← Списки ·
    🖨 Печать». Полной шапки и нижней панели документам не даём: это листы, которые читают
    и печатают, а у части свой скрипт (doc-state в «Пульте внедрения»). При печати скрыта."""
    return (f'<div id="kidsup-doc-bar" class="kn-doc">{_css()}'
            f'<a class="kn-doc-nazad" href="{_e(nazad_url)}">← {_e(nazad_label)}</a>'
            f'<span class="kn-doc-podpis">{_e(podpis or "KidsUP")}</span>'
            '<button type="button" class="kn-doc-pechat" onclick="window.print()">🖨 Печать</button></div>')


_BODY = re.compile(r"<body\b[^>]*>", re.I)
# Всё, что стоит до содержимого страницы, когда тега <body> в ней нет: doctype, html, head,
# meta, title, стили и скрипты. Шапку ставим сразу за этим — то есть после <meta charset…>
# и остального «заголовочного», а без них — сразу после <!doctype>/<html>.
_DO_TELA = re.compile(
    r"\s+|<!--.*?-->|<!doctype[^>]*>|</?html\b[^>]*>|</?head\b[^>]*>|<meta\b[^>]*>|<link\b[^>]*>|<base\b[^>]*>"
    r"|<title\b[^>]*>.*?</title>|<style\b[^>]*>.*?</style>|<script\b[^>]*>.*?</script>", re.I | re.S)


def vstavit(stranica: str, kusok: str) -> str:
    """Вставить кусок в начало тела страницы: сразу после первого <body…>; тега нет —
    после <meta charset…> и прочего заголовочного; нет и его — после <!doctype>/<html>.
    Перед <!doctype> не ставим никогда: браузер уйдёт в режим совместимости (так было
    на /base/{slug} с полосой «← Все методички»)."""
    if not kusok:
        return stranica
    m = _BODY.search(stranica)
    if m:
        i = m.end()
    else:
        i = 0
        while True:
            m = _DO_TELA.match(stranica, i)
            if not m or m.end() == i:
                break
            i = m.end()
    return stranica[:i] + kusok + stranica[i:]


def wrap(stranica: str, chto) -> str:
    """Страница со своим HTML → она же с общей шапкой. chto — готовая шапка строкой или
    словарь параметров shapka() (rol, path, k, kto_html, schet_url). Вызывается явно в
    маршруте (не прослойкой на все ответы: документам, JSON и публичным страницам шапка
    не нужна). Если шапка на странице уже есть, вторую не ставим."""
    if not isinstance(stranica, str) or KOREN in stranica:
        return stranica
    kusok = shapka(**chto) if isinstance(chto, dict) else (chto or "")
    return vstavit(stranica, kusok)
