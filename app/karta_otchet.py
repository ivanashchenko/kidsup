"""Отчёт о прогрессе родителю, весточки и вводное сообщение — английский.

Формат выбран 24–25.09 после разбора (два исследователя, три варианта формата,
критик; итог — в методичке, раздел 10 и 12). Коротко, почему он такой:

• Три строки «умеет / над чем работаем / что дома» остаются ядром: так пишет
  родителям Cambridge в результатах Starters/Movers/Flyers, и это модель
  обратной связи Hattie & Timperley. Конкретный совет «что улучшить» снижал
  отсев, похвала — нет (Kraft & Rogers 2015).
• Добавлены «Где мы» — ориентир точки и цель к следующей (родитель иначе не
  понимает, много это или мало) и «было → стало» вместо снимка «умеет».
• Два сообщения: видео с подписью (его пересылают бабушке — работает на
  рекомендации) и отчёт. Шапка «плановый отчёт, такие получают все семьи» —
  чтобы личное письмо не читалось как «раз пишут — значит, беда».
• Вопрос родителю в конце: разговор в обе стороны.
• Раз в две недели — короткая весточка о ребёнке («двое в фокусе» на каждом
  занятии): во всех сильных исследованиях сигнал родителю шёл раз в 1–2 недели.
• Если ребёнок отстаёт — сначала звонок, потом письмо (флаг ставит система).
• Никогда: чат группы, сравнение с детьми, «молодец/способный», баллы,
  «сертификат». Проверка стоп-слов стоит перед отправкой.

Черновик собирается из отметок карточки речи; руками педагог пишет только
живой эпизод с английскими словами ребёнка — около 4 минут на ребёнка.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta

from . import db

# --- ориентиры точек по дорожкам (методичка, разделы 8 и 7) -----------------
ORIENTIR = {
    "base": {
        "t1": ("понимать команды занятия без перевода, называть имя и возраст, знать 4–5 стихов",
               ["komanda", "fraza"]),
        "t2": ("рассказывать о себе и семье, узнавать знакомые слова в обычной речи, знать 12–15 стихов",
               ["fraza", "potok"]),
        "t3": ("понимать короткую сказку без перевода, связно говорить о себе 4–6 фразами, "
               "задавать вопросы, читать знакомый стих хором", ["potok", "sam", "chtenie"]),
        "t4": ("говорить о себе 6–8 фразами без подсказки, читать знакомый текст вслух",
               ["fraza", "sam", "chtenie"]),
    },
    "malyshi": {
        "t1": ("понимать команды занятия и включаться в песни и игры", ["komanda"]),
        "t2": ("подпевать целые строчки и отвечать словом на простые вопросы", ["potok"]),
        "t3": ("понимать короткую сказку, показывать героев и отвечать словом", ["potok", "fraza"]),
        "t4": ("отвечать о себе словом или короткой фразой, знать 10–12 песен и стихов",
               ["fraza", "repliki"]),
    },
    "pro": {
        "t1": ("свободно отвечать и задавать вопросы о себе и своём дне, рассказывать о себе "
               "5–6 фразами, писать 3–4 предложения по образцу", ["warmup", "o_sebe", "pismo"]),
        "t2": ("описывать картинку и называть отличия между двумя целой фразой, понимать "
               "короткий текст по теме", ["kartinka", "tekst"]),
        "t3": ("рассказывать историю по картинкам в прошедшем времени, писать 5–6 предложений "
               "без образца", ["istoriya", "pismo"]),
        "t4": ("рассказывать историю по картинкам самостоятельно и пройти наш внутренний экзамен "
               "по всем частям", ["istoriya", "tekst", "pismo"]),
    },
}
TOCHKA = {  # (за какой период, «к концу …», следующая точка)
    "start": ("старт", "на старте", "t1"),
    "t1": ("октябрь", "к концу октября", "t2"),
    "t2": ("декабрь", "к концу декабря", "t3"),
    "t3": ("март", "к концу марта", "t4"),
    "t4": ("год", "к апрелю", ""),
}
PORYADOK = {"base": ["komanda", "potok", "fraza", "repliki", "sam", "chtenie"],
            "malyshi": ["komanda", "potok", "fraza", "repliki", "sam"],
            "pro": ["warmup", "o_sebe", "kartinka", "tekst", "pismo", "istoriya"]}
TOCHKI_PORYADOK = ["start", "t1", "t2", "t3", "t4"]

# --- банк формулировок по пунктам карточки ---------------------------------
# Глаголы — в настоящем времени: они не зависят от пола ребёнка.
BANK = {
    "komanda": dict(
        bylo="команды — вместе с группой и по показу",
        stalo="выполняет новые команды с первого раза, без показа",
        pochti="начинает выполнять новые команды сам, иногда пока по показу",
        uchimsya="понимать новые команды с первого раза, без показа",
        kak="на занятии — игры с командами в движении",
        doma=["поиграйте дома в «Simon says»: вы даёте команду по-английски из песни, ребёнок "
              "выполняет — 5 минут, пока собираетесь",
              "утром включайте видео-зарядку с командами — ребёнок двигается и слушает, "
              "объяснять ничего не нужно"],
        vopros="Узнаёт ли дома песни и команды с занятий?"),
    "potok": dict(
        bylo="знакомые слова узнаёт, только когда говорю медленно",
        stalo="узнаёт знакомые слова, когда я говорю в обычном темпе",
        pochti="узнаёт знакомые слова в быстрой речи, пока не все",
        uchimsya="узнавать знакомые слова в обычной, быстрой речи",
        kak="на занятии — сказки и истории в нормальном темпе, с картинками",
        doma=["плейлист группы фоном 15 минут — в машине или за завтраком: чем чаще звучит "
              "знакомое, тем быстрее ребёнок узнаёт его в потоке",
              "мультфильм по теме месяца на английском, 10 минут, без перевода — можно вместе"],
        vopros="Подпевает ли дома песни из плейлиста?"),
    "fraza": dict(
        bylo="на вопрос о себе — одно слово",
        stalo="отвечает о себе целой фразой",
        pochti="начинает отвечать о себе целой фразой, пока с подсказкой",
        uchimsya="отвечать о себе целой фразой, а не одним словом",
        kak="на занятии — интервью и «микрофон» по кругу",
        doma=["за ужином спросите по-английски одно: How are you? или How old are you? — ответ "
              "целиком, без исправлений, 1 минута",
              "попросите показать игрушке, как отвечать о себе по-английски — игрушке дети "
              "отвечают охотнее, чем взрослому"],
        vopros="Отвечает ли дома по-английски — словом или целой фразой?"),
    "repliki": dict(
        bylo="говорит в основном хором",
        stalo="много говорит вслух — в играх, в парах, хором",
        pochti="говорит вслух всё чаще, пока больше хором",
        uchimsya="говорить вслух больше — в парах и в игре, не только хором",
        kak="на занятии — игры, где без фразы не сделать ход",
        doma=["попросите научить вас стиху или песне с занятия — в роли учителя ребёнок "
              "говорит больше всего",
              "поиграйте в магазин: вы продавец, ребёнок покупает по-английски — Can I have…? "
              "— 5 минут"],
        vopros="Рассказывает ли дома стихи и песни с английского?"),
    "sam": dict(
        bylo="по-английски отвечает, сам не спрашивает",
        stalo="задаёт вопросы и обращается к детям по-английски по своей инициативе",
        pochti="начинает спрашивать, пока с подсказкой",
        uchimsya="спрашивать по своей инициативе: Do you like…? Have you got…? Can I have…?",
        kak="на занятии — играем в интервью и магазин, первым спрашивает ребёнок",
        doma=["пусть раз в день задаст вам любой вопрос по-английски; отвечать можно по-русски — "
              "главное, что спрашивает ребёнок",
              "5 минут за игрой разговаривайте «только по-английски» с его стороны — вы можете "
              "отвечать по-русски"],
        vopros="Просит ли дома что-нибудь по-английски или спрашивает вас?"),
    "chtenie": dict(
        bylo="знакомые стихи знает только на слух",
        stalo="читает знакомый стих хором, ведя пальцем по строке",
        pochti="следит пальцем по строке знакомого стиха, пока с моей помощью",
        uchimsya="читать знакомые стихи по карточке, ведя пальцем по строке",
        kak="на занятии — читаем хором знакомые стихи с цветной разметкой",
        doma=["перед сном 5 минут: включите аудио стиха с занятия, ребёнок ведёт пальцем по "
              "карточке — читать не нужно, только следить",
              "знакомую сказку читайте вместе только под аудио — так закрепляется правильное "
              "произношение"],
        vopros="Показывает ли дома карточки со стихами?"),
    "warmup": dict(
        bylo="на вопросы в начале урока — короткие ответы с подсказкой",
        stalo="задаёт вопросы в начале урока и отвечает на них без подсказки",
        pochti="на вопросы в начале урока отвечает без подсказки, спрашивает пока с подсказкой",
        uchimsya="задавать вопросы друг другу в начале урока без подсказки",
        kak="на занятии — разминка в парах: вопросы о дне, погоде, выходных",
        doma=["по дороге с занятия спросите: What did you do today? — ответ по-английски, "
              "2 минуты, ошибки не исправляйте"],
        vopros="Говорит ли дома что-нибудь по-английски без просьбы?"),
    "o_sebe": dict(
        bylo="о себе — короткими фразами, с переходом на русский",
        stalo="рассказывает о себе и своём дне 5–6 фразами подряд",
        pochti="рассказывает о себе 3–4 фразами, иногда переходит на русский",
        uchimsya="связывать фразы в рассказ: because, but, and then",
        kak="на занятии — рассказ о своём дне и выходных по цепочке",
        doma=["в воскресенье вечером пусть расскажет по-английски три вещи о выходных, одна — "
              "с because; 3 минуты"],
        vopros="Рассказывает ли дома, что было на английском?"),
    "kartinka": dict(
        bylo="картинку описывает отдельными словами",
        stalo="описывает картинку и называет отличия между двумя целой фразой",
        pochti="описывает картинку фразами, отличия пока называет словами",
        uchimsya="описывать картинку и находить отличия целыми фразами",
        kak="на занятии — «найди отличия» в парах, каждое отличие — фразой",
        doma=["в книге или журнале попросите описать одну картинку по-английски: кто, где, "
              "что делает — 3 фразы"],
        vopros="Есть ли дома книги или журналы на английском с картинками?"),
    "tekst": dict(
        bylo="короткий текст понимает с переводом отдельных слов",
        stalo="читает короткий текст по теме и отвечает на вопросы к нему",
        pochti="понимает короткий текст, на вопросы к нему отвечает пока с подсказкой",
        uchimsya="понимать короткий незнакомый текст и отвечать на вопросы по нему",
        kak="на занятии — короткие тексты по теме и вопросы к ним в формате экзамена",
        doma=["10 минут в день простая книга или комикс на английском по интересам — подберу, "
              "если напишете, что любит"],
        vopros="Читает или смотрит что-нибудь на английском без вашей просьбы? Подберу тексты под интересы."),
    "pismo": dict(
        bylo="пишет отдельные слова и фразы по образцу",
        stalo="пишет несколько предложений о себе",
        pochti="пишет предложения о себе по образцу",
        uchimsya="писать 5–6 предложений без образца",
        kak="на занятии — короткое письмо другу и подписи к картинкам",
        doma=["раз в неделю пусть напишет 3–4 предложения о своей неделе по-английски; "
              "ошибки не проверяйте — посмотрю на занятии"],
        vopros="Пишет ли что-нибудь по-английски — в играх, в переписке?"),
    "istoriya": dict(
        bylo="историю по картинкам рассказывает с подсказками",
        stalo="рассказывает историю по четырём картинкам",
        pochti="рассказывает историю по картинкам, пока с подсказками",
        uchimsya="рассказывать историю по картинкам без подсказок, в прошедшем времени",
        kak="на занятии — истории по серии картинок: сначала вместе, потом каждый",
        doma=["попросите пересказать по-английски мультфильм или сюжет книги в 4–5 фразах"],
        vopros="Любит ли сочинять истории — на каком языке?"),
}
# малышам первый год — ответ словом, а не фразой
BANK_MALYSHI = {"fraza": dict(BANK["fraza"], stalo="отвечает словом на простые вопросы",
                              pochti="начинает отвечать словом, пока чаще жестом",
                              uchimsya="отвечать словом в игре: выбрать цвет, назвать зверька",
                              kak="на занятии — отвечать одного при всех не просим, только в игре",
                              doma=["плейлист группы в машине или за завтраком, 15 минут. Просить "
                                    "«скажи по-английски» не нужно — малыши от этого замолкают. "
                                    "Просто пойте вместе"])}

PEDAGOG_POL = {"Маша": "ж", "Илья": "м"}
UROVEN = {"Starters": "Starters — первый из трёх детских уровней Cambridge",
          "Movers": "Movers — второй из трёх детских уровней Cambridge",
          "Flyers": "Flyers — третий, старший детский уровень Cambridge"}

# --- стоп-слова (методичка: «чего не делаем») ------------------------------
STOP = re.compile(r"молодец|молодч|умниц|способн|талант|лучше всех|первым в группе|первой в группе|"
                  r"отста[её]т от|в отличие от|сертификат", re.I)


def _b(item: str, dorozhka: str) -> dict:
    if dorozhka == "malyshi" and item in BANK_MALYSHI:
        return BANK_MALYSHI[item]
    return BANK[item]


def _marks(uid: int) -> dict:
    out = {}
    with db.get_conn() as conn:
        for r in conn.execute("SELECT tochka, marks FROM speech_cards WHERE user_id=?", (uid,)):
            try:
                out[r["tochka"]] = json.loads(r["marks"] or "{}")
            except ValueError:
                out[r["tochka"]] = {}
    return out


def _prev(marks: dict, tochka: str) -> dict:
    i = TOCHKI_PORYADOK.index(tochka)
    for t in reversed(TOCHKI_PORYADOK[:i]):
        if marks.get(t):
            return marks[t]
    return {}


def poseshchaemost(uid: int, days: int = 45, class_like: str = "2627_АЯ%") -> tuple[int, int]:
    """Посещения / записи на прошедшие занятия английского за последние дни."""
    since = (date.today() - timedelta(days=days)).isoformat()
    today = date.today().isoformat()
    with db.get_conn() as conn:
        r = conn.execute(
            # Только занятия, где явку вообще отметили (есть хоть одно «был»):
            # 25.09 явка не закрывалась по неделе, и неотмеченное занятие
            # читалось как пропуск — флаг «сначала звонок» вставал зря.
            "SELECT SUM(CASE WHEN lr.visit=1 THEN 1 ELSE 0 END), COUNT(*) FROM lesson_records lr "
            "JOIN lessons l ON l.id = lr.lesson_id JOIN classes c ON c.id = l.class_id "
            "WHERE lr.user_id=? AND l.date>=? AND l.date<? AND c.name LIKE ? "
            "AND EXISTS (SELECT 1 FROM lesson_records x WHERE x.lesson_id = l.id AND x.visit = 1)",
            (uid, since, today, class_like)).fetchone()
    return int(r[0] or 0), int(r[1] or 0)


def flag(uid: int, dorozhka: str, gruppa: str = "", status: str = "учится") -> str:
    """Причина «сначала звонок» или пустая строка."""
    m = _marks(uid)
    filled = [t for t in TOCHKI_PORYADOK if m.get(t)]
    for a, b in zip(filled, filled[1:]):
        for k, v in m[b].items():
            if v == "пока нет" and m[a].get(k) == "пока нет":
                return f"«пока нет» по пункту «{_b(k, dorozhka)['uchimsya']}» на двух точках подряд"
    if dorozhka != "malyshi" and " · " not in gruppa and status == "учится":
        v, n = poseshchaemost(uid, 30)
        if n >= 4 and v / n < 0.6:
            return f"посещаемость за месяц {v} из {n}"
    return ""


def _kto(tochka_marks: dict, items: list[str]) -> tuple[list[str], list[str]]:
    da = [k for k in items if tochka_marks.get(k) == "да"]
    net = [k for k in items if tochka_marks.get(k) and tochka_marks.get(k) != "да"]
    return da, net


def chernovik(d: dict, tochka: str, vvod: dict) -> dict:
    """Черновик двух сообщений: подпись к видео и отчёт. d — строка ребёнка из karta.rows();
    vvod — поля педагога: epizod, tajm, uchimsya (ключ пункта), doma_i, menyaem, cel, zvonok."""
    uid, dor, imya, ped = d["user_id"], d["дорожка"], d["имя_короткое"] or d["имя"], d["педагог"] or "педагог"
    m = _marks(uid)
    cur = m.get(tochka) or {}
    if len([v for v in cur.values() if v]) < 3:
        raise ValueError("сначала отметьте в карточке хотя бы три пункта этой точки")
    prev = _prev(m, tochka)
    period, k_kontsu, nxt = TOCHKA[tochka]
    por = [k for k in PORYADOK[dor] if not (k == "chtenie" and tochka in ("start", "t1", "t2"))]
    # было → стало
    novye = [k for k in por if cur.get(k) == "да" and prev.get(k) != "да"]
    kogda = {"start": "В сентябре", "t1": "В октябре", "t2": "В декабре", "t3": "В марте"}.get(
        next((t for t in reversed(TOCHKI_PORYADOK[:TOCHKI_PORYADOK.index(tochka)]) if m.get(t)), "start"),
        "Раньше")
    if novye:
        k0 = novye[0]
        bs = [f"{kogda}: {_b(k0, dor)['bylo']}; сейчас {_b(k0, dor)['stalo']}."]
        if len(novye) > 1:
            bs.append(f"И ещё: {_b(novye[1], dor)['stalo']}.")
    else:
        est = [k for k in por if cur.get(k) == "да"]
        if est:
            bs = [f"Уверенно {_b(k, dor)['stalo']}." for k in est[:2]]
        else:
            poch = [k for k in por if cur.get(k) == "почти"]
            bs = [_b(k, dor)["pochti"].capitalize() + "." for k in poch[:2]] or \
                 ["Понимает всё больше на занятии и включается в игры."]
    epizod = (vvod.get("epizod") or "").strip()
    tajm = (vvod.get("tajm") or "").strip()
    podpis = f"{imya} на английском, {period}. " + " ".join(bs)
    if epizod:
        podpis += " " + epizod[0].upper() + epizod[1:].rstrip(".") + "."
    if tajm:
        podpis += f" На видео с {tajm}."
    # где мы
    ori, ori_items = ORIENTIR[dor].get(tochka, ORIENTIR[dor]["t1"])
    da, net = _kto(cur, ori_items)
    if net:
        gde_sost = ("Часть этого пока в работе: " +
                    ", ".join(_b(k, dor)["uchimsya"] for k in net) + " — над этим и работаем.")
    elif da:
        gde_sost = "Это уже получается."
    else:
        gde_sost = ""
    gde = f"{k_kontsu.capitalize()} мы ждали: {ori}. {gde_sost}".strip()
    if nxt:
        ori_next = ORIENTIR[dor][nxt][0]
        gde += f" {TOCHKA[nxt][1].capitalize()} — {ori_next}."
    uroven = ""
    if dor == "pro" and tochka in ("t1", "start"):
        for lvl, txt in UROVEN.items():
            if lvl in d.get("группа", ""):
                uroven = f"Группа идёт по программе {txt}. "
                break
    # сейчас учимся
    uch = vvod.get("uchimsya") or next((k for k in por if cur.get(k) == "почти"), None) \
        or next((k for k in por if cur.get(k) == "пока нет"), None) or por[-1]
    ub = _b(uch, dor)
    doma_list = ub["doma"]
    try:
        di = int(vvod.get("doma_i") or 0)
    except ValueError:
        di = 0
    doma = doma_list[di % len(doma_list)]
    v_, n_ = poseshchaemost(uid, 45)
    fakt = f"За последние полтора месяца — {v_} занятий из {n_}." \
        if dor != "malyshi" and n_ >= 4 and v_ / n_ < 0.75 else ""
    pol = PEDAGOG_POL.get(ped, "ж")
    shapka = f"Здравствуйте! Это {ped}, английский в KidsUP."
    zvonok = bool(vvod.get("zvonok"))
    fl = flag(uid, dor, d.get("группа", ""), d.get("статус", "учится"))
    if fl and zvonok:
        zap = "записала" if pol == "ж" else "записал"
        cel = (vvod.get("cel") or ub["uchimsya"]).strip()
        menyaem = (vvod.get("menyaem") or "").strip() or "____ (что меняем на занятии)"
        tekst = "\n".join(x for x in [
            f"{shapka} Спасибо за разговор — коротко {zap}, о чём договорились.",
            f"Где мы. {uroven}{gde}",
            fakt,
            f"Что меняем мы: {menyaem}.",
            f"Дома одно: {doma}.",
            f"Цель на ближайшие 3–4 недели: {cel}. Через 3–4 недели напишу, как идёт.",
            "Вопрос вам: хочет ли ребёнок идти на занятия, что рассказывает дома?",
            ped] if x)
    else:
        tekst = "\n".join(x for x in [
            f"{shapka} Плановый отчёт за {period}: {imya}. Такие отчёты получают все семьи группы.",
            f"Где мы. {uroven}{gde}",
            f"Сейчас учимся {ub['uchimsya']}; {ub['kak']}.",
            fakt,
            f"Дома: {doma}.",
            f"Вопрос вам: {ub['vopros'][0].lower() + ub['vopros'][1:]}",
            ped] if x)
        if tochka == "t4":
            tekst = tekst.replace(f"\n{ped}", "\nВ апреле — наш внутренний экзамен в формате "
                                  "Cambridge, это не сертификат Cambridge: покажем результат по "
                                  f"каждой части словами.\n{ped}", 1)
    return {"podpis": podpis, "tekst": tekst, "flag": fl, "zvonok_nuzhen": bool(fl) and not zvonok,
            "uchimsya": uch, "uchimsya_varianty": [(k, _b(k, dor)["uchimsya"]) for k in por],
            "doma_varianty": doma_list, "doma_i": di % len(doma_list)}


def vvodnoe(d: dict, pochemu: str = "") -> str:
    """Договор о связи: одно сообщение каждой семье 1–10 октября."""
    imya, ped, dor = d["имя_короткое"] or d["имя"], d["педагог"] or "педагог", d["дорожка"]
    gr = gruppa_chelovecheski(d["группа"])
    t1 = ORIENTIR[dor]["t1"][0]
    t4 = ORIENTIR[dor]["t4"][0]
    stroki = [
        f"Здравствуйте! Это {ped}, английский в KidsUP ({imya}). Пишу, чтобы вы знали, как и "
        f"когда будете получать от нас новости.",
        f"• Группа: {gr}." + (f" {pochemu.strip().rstrip('.')}." if pochemu.strip() else ""),
        f"• К концу октября ждём: {t1}. К апрелю: {t4}.",
        "• Как вы будете узнавать новости: раз в неделю в чате группы — что прошли и аудио для "
        "дома; раз в две недели лично вам — короткая новость о ребёнке; раз в месяц — видео, где "
        "ребёнок говорит сам; в октябре, декабре, марте и апреле — отчёт о прогрессе.",
        "• Вопросы — сюда, отвечаем в течение суток.",
        ped]
    return "\n".join(stroki)


DNI = {"пн": "понедельникам", "вт": "вторникам", "ср": "средам", "чт": "четвергам",
       "пт": "пятницам", "сб": "субботам", "вс": "воскресеньям"}
WD = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}


def gruppa_chelovecheski(g: str) -> str:
    if " · " in g:
        return g.split(" · ")[0] + ", " + g.split(" · ")[1]
    parts = g.split("_")
    try:
        dni = [DNI.get(x, x) for x in parts[0].split("-")]
        return f"по {' и '.join(dni)} в {parts[1]}, {parts[2]}"
    except Exception:
        return g


def dni_gruppy(g: str) -> list[int]:
    if g.startswith("Мини-сад") or g.startswith("Нулевой класс"):
        return [0, 2]
    return [WD[x] for x in g.split("_")[0].split("-") if x in WD]


def proverka(tekst: str, d: dict, drugie_imena: list[str]) -> tuple[list[str], list[str]]:
    """(ошибки, предупреждения) перед отправкой."""
    oshibki, preduprezhdeniya = [], []
    for m in STOP.finditer(tekst or ""):
        oshibki.append(f"«{m.group(0)}» — так родителю не пишем (методичка, раздел 12)")
    if "____" in (tekst or ""):
        oshibki.append("остался пропуск ____ — допишите")
    svoe = (d.get("имя_короткое") or "").lower()
    for nm in drugie_imena:
        if len(nm) >= 3 and nm.lower() != svoe and re.search(rf"\b{re.escape(nm)}\b", tekst or "", re.I):
            preduprezhdeniya.append(f"в тексте имя другого ребёнка: {nm}")
    return oshibki, preduprezhdeniya


# --- журнал личных сообщений родителю --------------------------------------
def _ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS speech_contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, kind TEXT, tochka TEXT,
        text TEXT, video TEXT, ts TEXT, author TEXT, log TEXT, ok INTEGER)""")


def poslednie_kontakty() -> dict[int, str]:
    with db.get_conn() as conn:
        _ensure(conn)
        return {r[0]: r[1] for r in conn.execute(
            "SELECT user_id, MAX(ts) FROM speech_contacts WHERE ok=1 GROUP BY user_id")}


def zhurnal(uid: int) -> list[dict]:
    with db.get_conn() as conn:
        _ensure(conn)
        return [dict(r) for r in conn.execute(
            "SELECT kind, tochka, text, video, ts, author, ok FROM speech_contacts "
            "WHERE user_id=? ORDER BY ts DESC LIMIT 20", (uid,))]


def otpravit(d: dict, kind: str, teksty: list[str], video_url: str = "", tochka: str = "",
             author: str = "") -> dict:
    """Отправить родителю от имени центра. kind: otchet | vestochka | vvodnoe | video."""
    from . import wazzup
    phone = "".join(c for c in (d.get("телефон") or "") if c.isdigit())
    if len(phone) == 10:
        phone = "7" + phone
    if len(phone) != 11:
        return {"ok": False, "error": "в карточке нет телефона родителя"}
    dry = db.get_setting("wazzup_dry_run", "1") == "1"
    log = wazzup.send_pedagog(phone, [t for t in teksty if t and t.strip()], media=video_url,
                              uid=d["user_id"], dry_run=dry)
    ok = any(x.endswith(": ok") for x in log)
    with db.get_conn() as conn:
        _ensure(conn)
        conn.execute("INSERT INTO speech_contacts (user_id, kind, tochka, text, video, ts, author, log, ok) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     (d["user_id"], kind, tochka, "\n\n".join(teksty)[:4000], video_url,
                      time.strftime("%Y-%m-%d %H:%M"), author[:40], "; ".join(log)[:500], 1 if ok else 0))
    if ok:
        try:
            from .moyklass_client import MoyklassClient
            from . import sync
            mk = MoyklassClient(sync.get_api_key())
            try:
                vid = {"otchet": "отчёт о прогрессе", "vestochka": "весточка о ребёнке",
                       "vvodnoe": "вводное сообщение (как будем держать в курсе)",
                       "video": "видео с занятия"}.get(kind, kind)
                mk.post("/v1/company/userComments", {
                    "userId": int(d["user_id"]), "showToUser": False,
                    "comment": (f"Английский: родителю отправлено — {vid} ({d.get('педагог') or ''}, "
                                f"{time.strftime('%d.%m %H:%M')}). " + " / ".join(teksty))[:1500]})
            finally:
                mk.close()
        except Exception:
            pass
    stop = [x for x in log if x.endswith(": стоп")]
    return {"ok": ok, "dry_run": dry, "log": log,
            "error": "" if ok else ("предохранитель не пропустил (вне 9–20, выходные до 10 или "
                                     "семья в стоп-листе)" if stop else "ни один канал не принял")}
