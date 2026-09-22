"""Дети, которые ходили в прошлом сезоне и летом, но которым ни разу не звонили.

Владелец 17.09: «вернёмся к тем, кто ходил прошлый учебный год и это лето,
НО ни разу не прозвонен — отдельная страница с фамилией и именем ребёнка,
хочу сегодня поручить Ане».

Отличие от /nezvonili принципиальное, и ради него написан отдельный модуль.
На /nezvonili список строится по КАРТОЧКАМ, заведённым в окне: человек
оставил номер — и всё. Оттуда и берутся сорок строк «Звонок от 79…»
и городские номера салонов красоты: имени нет, потому что разговора не было.

Здесь список строится по ПОСЕЩЁННЫМ ЗАНЯТИЯМ. Ребёнок сидел в нашем классе,
педагог отметил его в журнале, родитель платил. Имя есть всегда — иначе бы
его не записали в группу. Это самая тёплая база, какая у нас вообще бывает:
звонок начинается не с «вы оставляли заявку», а с «ваш ребёнок у нас
занимался».

Что считаем:
  ходил       — есть хотя бы одно занятие с отметкой о посещении в одном из
                двух окон: учебный год 01.09.2025–31.05.2026 или лето
                01.06.2026–30.08.2026. В таблице видно, в каком именно;
  не связались — за КАМПАНИЮ НАБОРА, с 01.08.2026 по сегодня, с этой семьёй
                не состоялось ни одного живого контакта.

Про второе владелец 17.09 поправил меня отдельно, и поправка важная. Сначала
я отсеивал всех, с кем разговор случался когда-либо за год: поговорили в
октябре — значит прозвонен. Но к набору на этот сезон октябрьский разговор
отношения не имеет; родителю тогда звонили про другое, и приглашения
вернуться он не слышал. Из-за этого из списка выпадали дети, которых как раз
и надо звать. Окно контакта теперь равно окну набора.

Живой контакт — это:
  — телефонный разговор от двадцати секунд (talked), в любую сторону:
    набранный и не взятый номер контактом не является, там просто не знают,
    что мы звонили;
  — входящее сообщение от клиента: написал нам сам — значит связь есть.
Наша рассылка контактом НЕ считается: отправить текст в WhatsApp и связаться
с человеком — разные вещи. В таблице видно, уходила ли рассылка.

Оговорка про журнал: «не звонили» мы можем утверждать только за период,
покрытый журналом Манго. Он начинается 30.08.2025 и оба окна покрывает
целиком, так что здесь эта дыра не мешает.

Кто сюда НЕ попадает:
  — те, кто ходит сейчас: звонить действующему клиенту со словами «вы у нас
    занимались» — позориться. Они вынесены отдельным блоком, просто чтобы
    было видно, что их не потеряли;
  — статусы «не писать», «не работаем», «некачественный лид».

Только чтение.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import db, nabor

# Два окна, как их назвал владелец
GOD_S, GOD_PO = "2025-09-01", "2026-05-31"      # прошлый учебный год
LETO_S, LETO_PO = "2026-06-01", "2026-08-30"    # лето

# Кампания набора на сезон 2026/27: в этом окне и смотрим, связались ли
NABOR_S = "2026-08-01"

# Текущий сезон: кто ходит сейчас — не для обзвона
SEZON_S = "2026-08-25"

DEAD = {146328,   # 0.1. Не писать / не звонить
        215202,   # 0.2. Не работаем с ним
        125954}   # Некачественный лид

# 22.09.2026. Борис прислал видео экрана: «в списке Клода прямо список
# хвостов». И правда — из 81 строки 19 были «Отказ» и 6 «Неактивный /
# архив набора»: люди, которые уже сказали нет. Звать их «вернём ушедшего»
# нельзя, это не возврат, а хождение по кругу. Держим их отдельным блоком:
# видеть их полезно (среди отказников 15 платящих), но в работе смены их нет.
# «Неактивный клиент» (125956) сюда НЕ входит: это как раз тот, кто просто
# перестал ходить и ничего нам не говорил, — главная цель возврата.
OTKAZ = {125957,   # Отказ
         345759}   # 0. Архив набора


def _p10(x) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())[-10:]


def _predmet(cls: str) -> str:
    """Из «2627_АЯ_пн-ср_16:00_8-12 лет_Pre-A1 Starters (Гр1)» — «АЯ».

    Имя группы у нас собрано из кусков через подчёркивание, первый кусок
    после префикса сезона — предмет. Админу в разговоре нужен именно он:
    «ваш ребёнок ходил на английский», а не вся строка расписания.
    """
    s = (cls or "").replace("2627_", "").replace("2526_", "")
    s = s.split("_")[0].strip()
    return s[:40]


def _done_init(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS obzvon_done (
        uid INTEGER PRIMARY KEY, ts TEXT, who TEXT, note TEXT)""")


def otmetki() -> dict[int, dict]:
    """Ручные отметки «сделано» по детям из списка обзвона."""
    with db.get_conn() as conn:
        _done_init(conn)
        rows = conn.execute("SELECT uid, ts, who, note FROM obzvon_done").fetchall()
    return {r[0]: {"когда": r[1], "кто": r[2], "заметка": r[3]} for r in rows}


MIN_TALK = 30          # секунд: короче — это не разговор, а «алло, не могу»


def _osnovanie(conn, uid: int, days: int = 2, do: str = "") -> dict:
    """Чем подтверждается, что с семьёй сегодня действительно говорили.

    Смотрим журнал Манго и входящие сообщения за последние двое суток по
    ВСЕМ телефонам этой семьи (у карточек бывает второй номер родителя).

    22.09.2026, аудит. У окна не было ВЕРХНЕЙ границы: проверка стоящей
    галочки брала всё «от today−N до сейчас». Админ отмечала семью 19.09
    без разговора, 21.09 родитель сам перезванивал по другому поводу — и
    проверка засчитывала этот звонок как основание для позавчерашней
    галочки. Параметр `do` закрывает окно сверху моментом отметки.
    """
    since = (date.today() - timedelta(days=days)).isoformat()
    # немного воздуха вперёд: админ ставит галочку через минуту-другую
    # после разговора, а бывает и наоборот — сперва отметила, потом набрала
    verh = (do or "")[:19]
    if verh:
        try:
            verh = (datetime.fromisoformat(verh) + timedelta(hours=2)).isoformat(timespec="seconds")
        except ValueError:
            verh = ""
    phones = set()
    try:
        for r in conn.execute("SELECT phone FROM users WHERE id=?", (int(uid),)):
            p = _p10(r[0] if not isinstance(r, dict) else r["phone"])
            if p:
                phones.add(p)
    except Exception:
        pass
    if not phones:
        return {"есть": False, "почему": "в карточке нет телефона"}
    q = ",".join("?" * len(phones))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(mango_calls)")}
        has_secs = "secs" in cols
        sql = (f"SELECT ts, state{', secs' if has_secs else ''} FROM mango_calls "
               f"WHERE ts >= ? {'AND ts <= ?' if verh else ''} "
               f"AND substr(replace(replace(replace(phone,'+',''),'-',''),' ',''),-10) IN ({q}) "
               f"ORDER BY ts")
        for row in conn.execute(sql, (since, *((verh,) if verh else ()), *phones)):
            state = str(row[1] or "")
            if state != "talked":
                continue
            secs = int(row[2] or 0) if has_secs and len(row) > 2 else 0
            # Секунды пишутся с 21.09; у старых строк их нет, и там признак
            # talked (порог Манго — 20 с) остаётся единственным, что есть.
            if secs and secs < MIN_TALK:
                continue
            return {"есть": True, "чем": f"разговор {str(row[0])[:16].replace('T', ' ')}"
                                         + (f", {secs} с" if secs else "")}
    except Exception:
        pass
    try:
        for row in conn.execute(
                f"SELECT ts FROM wazzup_inbox WHERE ts >= ? {'AND ts <= ?' if verh else ''} "
                f"AND substr(phone,-10) IN ({q}) ORDER BY ts",
                (since, *((verh,) if verh else ()), *phones)):
            return {"есть": True, "чем": f"клиент написал нам {str(row[0])[5:16]}"}
    except Exception:
        pass
    return {"есть": False, "почему": f"в журнале нет разговора от {MIN_TALK} секунд и входящих сообщений"}


def otmetit(uid: int, done: bool = True, who: str = "", note: str = "") -> dict:
    """Поставить или снять отметку «обзвонили».

    19.09 Борис: «сделай, чтобы Аня могла галочками ставить что уже сделано».

    21.09.2026, решение владельца. 19.09 список закрыли 88 галочками при нуле
    исходящих звонков, 20.09 — ещё 28. Так из списка пропали Кузнецов Никита,
    ответивший на рассылку «Да», и Ковалева Аида со ста занятиями у нас: их
    «обзвонили», не позвонив. Галочка, которая не стоит за работой, хуже
    отсутствия галочки — она скрывает деньги.

    Теперь отметка принимается, только если она чем-то подтверждена:
      • разговор от 30 секунд по журналу Манго за последние двое суток
        (любой из телефонов семьи, в любую сторону), или
      • входящее сообщение от клиента за тот же срок, или
      • заметка от админа своими словами (от 12 символов) — что именно
        произошло: «мама написала в чате, что переехали», «ошиблись номером».
    Заметка видна в отчёте дня, и по ней всегда можно проверить.
    """
    from .autopilot import _now
    with db.get_conn() as conn:
        _done_init(conn)
        if done:
            osn = _osnovanie(conn, int(uid))
            note = (note or "").strip()
            if not osn["есть"] and len(note) < 12:
                return {"ok": False, "uid": int(uid),
                        "нужно": "разговор или заметка",
                        "почему": osn["почему"],
                        "подсказка": "Позвони и поговори — строка закроется сама. "
                                     "Если закрываешь без звонка (семья написала в чате, "
                                     "переехали, ошиблись номером) — напиши это словами в заметке."}
            conn.execute(
                "INSERT OR REPLACE INTO obzvon_done (uid, ts, who, note) VALUES (?,?,?,?)",
                (int(uid), _now().isoformat(timespec="minutes"), who[:20],
                 (note or osn.get("чем", ""))[:200]))
            return {"ok": True, "uid": int(uid), "done": True,
                    "основание": osn.get("чем") or f"заметка: {note[:60]}"}
        conn.execute("DELETE FROM obzvon_done WHERE uid=?", (int(uid),))
    return {"ok": True, "uid": int(uid), "done": False}


def audit(vernut: bool = False, days: int = 7) -> dict:
    """Проверить уже стоящие галочки тем же правилом и вернуть пустые.

    21.09, решение владельца. 19.09 список закрыли 88 галочками при нуле
    исходящих звонков, 20.09 — ещё 28. Новое правило действует с сегодня, но
    семьи, потерянные вчера, от этого не возвращаются: их надо вернуть руками.
    Здесь каждая отметка проверяется по журналу и переписке; те, за которыми
    ничего нет и нет заметки, снимаются — строка возвращается в список.
    """
    since = (date.today() - timedelta(days=days)).isoformat()
    out = {"проверено": 0, "с разговором": [], "по заметке": [], "пустые": []}
    with db.get_conn() as conn:
        _done_init(conn)
        rows = conn.execute("SELECT uid, ts, who, note FROM obzvon_done WHERE ts >= ?",
                            (since,)).fetchall()
        names = {}
        for r in rows:
            try:
                n = conn.execute("SELECT name FROM users WHERE id=?", (int(r["uid"]),)).fetchone()
                names[r["uid"]] = (n[0] if n else "") or str(r["uid"])
            except Exception:
                names[r["uid"]] = str(r["uid"])
        for r in rows:
            out["проверено"] += 1
            # Основание ищем на дату отметки, а не на сегодня: разговор был
            # тогда, и через неделю его «за двое суток» уже не видно.
            osn = _osnovanie(conn, int(r["uid"]), days=_dney_nazad(r["ts"]) + 2,
                             do=r["ts"])
            item = {"uid": r["uid"], "имя": names.get(r["uid"], ""),
                    "когда": (r["ts"] or "")[:16], "кто": r["who"],
                    "заметка": (r["note"] or "")[:80]}
            if osn["есть"]:
                item["чем"] = osn["чем"]
                out["с разговором"].append(item)
            elif len((r["note"] or "").strip()) >= 12:
                out["по заметке"].append(item)
            else:
                out["пустые"].append(item)
        if vernut and out["пустые"]:
            ids = [int(x["uid"]) for x in out["пустые"]]
            conn.execute("DELETE FROM obzvon_done WHERE uid IN (%s)" % ",".join("?" * len(ids)), ids)
            out["вернули"] = len(ids)
    return out


def _dney_nazad(ts: str) -> int:
    try:
        return max(0, (date.today() - date.fromisoformat(str(ts)[:10])).days)
    except Exception:
        return 7


def spisok() -> dict:
    today = date.today()
    with db.get_conn() as conn:
        # Кому мы звонили — и, отдельно, с кем в итоге поговорили.
        #
        # Первый список вышел на четырнадцать человек, потому что «звонили»
        # считалось по факту набранного номера. Но набранный номер и
        # состоявшийся разговор — разные вещи: телефон прозвонил, никто не
        # взял, и для семьи не произошло ничего. Такого человека звать
        # «прозвоненным» нельзя. Манго различает исходы: talked — разговор
        # от двадцати секунд, missed — не сняли, short — сняли и положили.
        # Всё нижеследующее считается ТОЛЬКО за кампанию набора, с 01.08.2026.
        zvonili: dict[str, str] = {}      # набирали, но не дозвонились
        govorili: dict[str, str] = {}     # разговор состоялся, в любую сторону
        ranshe: dict[str, str] = {}       # разговор был, но ДО кампании
        # Что случилось по номеру СЕГОДНЯ, с точностью до минуты. Нужно, чтобы
        # админ видел свою работу сразу: строка не просто исчезает из списка,
        # а переезжает в «сделано сегодня» с временем и исходом. И чтобы не
        # набирать повторно номер, который уже пробовали полчаса назад.
        segodnya: dict[str, list[dict]] = {}
        today_s = today.isoformat()
        pokrytie = {"есть": False}
        try:
            row = conn.execute(
                "SELECT MIN(ts), MAX(ts), COUNT(*) FROM mango_calls").fetchone()
            if row and row[0]:
                pokrytie = {"есть": True, "с": row[0][:10], "по": row[1][:10],
                            "всего": row[2]}
                for ts, phone, direction, state in conn.execute(
                        "SELECT ts, phone, direction, state FROM mango_calls"):
                    p = _p10(phone)
                    if not p:
                        continue
                    day = str(ts)[:10]
                    govoril = str(state or "") == "talked"
                    ish = str(direction).startswith(("out", "исх"))
                    if day == today_s:
                        segodnya.setdefault(p, []).append({
                            "время": str(ts)[11:16],
                            "что": ("поговорили" if govoril
                                    else "набирали — не взяли" if ish
                                    else "звонили нам — не сняли")})
                    if day < NABOR_S:
                        # разговор до набора — не контакт по этому поводу,
                        # но админу полезно знать, что человек уже общался
                        if govoril and day > ranshe.get(p, ""):
                            ranshe[p] = day
                        continue
                    if govoril:
                        if day > govorili.get(p, ""):
                            govorili[p] = day
                    elif ish:
                        if day > zvonili.get(p, ""):
                            zvonili[p] = day
        except Exception:
            pass

        # Переписка за кампанию: входящее от клиента — это связь состоялась,
        # наша рассылка — нет. Разводим их намеренно.
        pisali: dict[str, str] = {}       # мы отправили (в т.ч. рассылку)
        otvechali: dict[str, str] = {}    # клиент написал сам
        for table, store in (("wazzup_outbox", pisali), ("wazzup_inbox", otvechali)):
            try:
                rows_w = conn.execute(
                    f"SELECT ts, phone FROM {table} WHERE ts >= ?", (NABOR_S,)).fetchall()
            except Exception:
                continue
            for ts, phone in rows_w:
                p = _p10(phone)
                if not p:
                    continue
                if str(ts)[:10] > store.get(p, ""):
                    store[p] = str(ts)[:10]
                if str(ts)[:10] == today_s and store is otvechali:
                    segodnya.setdefault(p, []).append(
                        {"время": str(ts)[11:16], "что": "написали нам сами"})

        # Посещения в двух окнах: ребёнок, дата, предмет
        hodil: dict[int, dict] = {}
        for uid, d_, cls in conn.execute(
                "SELECT lr.user_id, l.date, c.name "
                "FROM lesson_records lr JOIN lessons l ON l.id = lr.lesson_id "
                "LEFT JOIN classes c ON c.id = l.class_id "
                "WHERE lr.visit = 1 AND ((l.date >= ? AND l.date <= ?) "
                "OR (l.date >= ? AND l.date <= ?))",
                (GOD_S, GOD_PO, LETO_S, LETO_PO)):
            h = hodil.setdefault(uid, {"занятий": 0, "первое": "", "последнее": "",
                                       "предметы": set(), "год": 0, "лето": 0})
            h["занятий"] += 1
            if d_ <= GOD_PO:
                h["год"] += 1
            else:
                h["лето"] += 1
            if not h["первое"] or d_ < h["первое"]:
                h["первое"] = d_
            if d_ > h["последнее"]:
                h["последнее"] = d_
            pr = _predmet(cls)
            if pr:
                h["предметы"].add(pr)

        # Кто ходит сейчас — этих в обзвон не отдаём
        seychas = {r[0] for r in conn.execute(
            "SELECT DISTINCT lr.user_id FROM lesson_records lr "
            "JOIN lessons l ON l.id = lr.lesson_id WHERE l.date >= ?", (SEZON_S,))}

        platili = {r[0] for r in conn.execute(
            "SELECT DISTINCT user_id FROM payments WHERE user_id IS NOT NULL")}

        try:
            st_names = {r[0]: r[1] for r in conn.execute(
                "SELECT id, name FROM client_statuses")}
        except Exception:
            st_names = {}

        users = {r[0]: r for r in conn.execute(
            "SELECT id, name, phone, client_state_id, raw FROM users")}

    otkazniki: list[dict] = []
    itogo = {"ходили_всего": len(hodil), "без_телефона": 0, "мёртвый_статус": 0,
             "поговорили": 0, "написали_нам": 0, "ходят_сейчас": 0,
             "нет_карточки": 0}
    deti: list[dict] = []
    aktivnye: list[dict] = []
    sdelano: list[dict] = []
    otm: list[dict] = []
    otmecheno = otmetki()
    vchera_s = (date.today() - timedelta(days=1)).isoformat()
    vchera: list[dict] = []
    for uid, h in hodil.items():
        u = users.get(uid)
        if not u:
            itogo["нет_карточки"] += 1
            continue
        _, name, phone, st, raw = u
        p = _p10(phone)
        rec = {
            "uid": uid,
            "имя": (name or "").strip() or f"без имени ({uid})",
            "телефон": ("7" + p) if p else "",
            "возраст": nabor._age(nabor._birthday(raw), today),
            "статус": st,
            "статус_текст": st_names.get(st, ""),
            "занятий": h["занятий"],
            "первое": h["первое"],
            "последнее": h["последнее"],
            "предметы": sorted(h["предметы"]),
            "платил": uid in platili,
            "занятий_год": h["год"],
            "занятий_лето": h["лето"],
            "период": ("год и лето" if h["год"] and h["лето"]
                       else "учебный год" if h["год"] else "лето"),
        }
        if uid in seychas:
            itogo["ходят_сейчас"] += 1
            aktivnye.append(rec)
            continue
        if not p:
            itogo["без_телефона"] += 1
            continue
        if st in DEAD:
            itogo["мёртвый_статус"] += 1
            continue
        rec["события_сегодня"] = sorted(segodnya.get(p, []),
                                        key=lambda e: e["время"])
        if p in govorili:
            itogo["поговорили"] += 1
            # Связались именно сегодня — это работа текущей смены. Такую
            # строку не прячем молча: админ должен видеть, что сделал.
            if govorili[p] == today_s:
                rec["итог"] = "поговорили"
                sdelano.append(rec)
            elif govorili[p] == vchera_s:
                # 18.09 Борис: «оставь только невыполненное». Вчерашние
                # разговоры из списка уже ушли, но владельцу нужно видеть,
                # что смена отработала, — поэтому отдельным блоком.
                vchera.append({**rec, "итог": "поговорили"})
            continue
        if p in otvechali:
            itogo["написали_нам"] += 1
            if otvechali[p] == today_s:
                rec["итог"] = "написали нам сами"
                sdelano.append(rec)
            elif otvechali[p] == vchera_s:
                vchera.append({**rec, "итог": "написали нам сами"})
            continue
        if uid in otmecheno:
            itogo["отмечено_руками"] = itogo.get("отмечено_руками", 0) + 1
            otm.append({**rec, "итог": "отмечено вручную",
                        "отметка": otmecheno[uid]})
            continue
        rec["набирали"] = zvonili.get(p, "")
        rec["рассылка"] = pisali.get(p, "")
        rec["говорили_раньше"] = ranshe.get(p, "")
        rec["пробовали_сегодня"] = bool(rec["события_сегодня"])
        if st in OTKAZ:
            itogo["сказали_нет"] = itogo.get("сказали_нет", 0) + 1
            otkazniki.append(rec)
            continue
        deti.append(rec)

    # Сортировка под разговор, а не под отчёт. Сверху те, кто ушёл недавно:
    # у них свежая память о нас и о педагоге, и разговор начинается сам собой.
    # Дальше — кто ходил дольше (больше занятий = больше причин вернуться).
    deti.sort(key=lambda r: (r["последнее"], r["занятий"]), reverse=True)
    # Кого уже пробовали сегодня — вниз: перезванивать через полчаса бессмысленно.
    # Сортировка устойчивая, поэтому порядок внутри каждой группы сохраняется.
    deti.sort(key=lambda r: r["пробовали_сегодня"])
    sdelano.sort(key=lambda r: (r["события_сегодня"][0]["время"]
                                if r["события_сегодня"] else ""), reverse=True)

    # Один номер — одна семья. Аня звонит на телефон, а не ребёнку: если на
    # номере двое детей, это один звонок, и знать про обоих надо заранее.
    semyi: dict[str, list[dict]] = {}
    for r in deti:
        semyi.setdefault(r["телефон"], []).append(r)
    for rs in semyi.values():
        for r in rs:
            r["братья"] = [x["имя"] for x in rs if x["uid"] != r["uid"]]

    # 22.09. На видео от Бориса Карнажицкие шли двумя строками подряд с одним
    # номером: Владимир и Владислава. Для звонка это ОДНА семья и один набор.
    # Оставляем в списке старшего по числу занятий, второго ребёнка показываем
    # в его строке — «братья» уже посчитаны выше.
    vidno: dict[str, dict] = {}
    for r in deti:
        cur = vidno.get(r["телефон"])
        if not cur or r["занятий"] > cur["занятий"]:
            vidno[r["телефон"]] = r
    deti = [r for r in deti if vidno.get(r["телефон"], {}).get("uid") == r["uid"]]

    nikogda = [r for r in deti if not r["набирали"]]
    ne_dozvonilis = [r for r in deti if r["набирали"]]

    po_predmetam: dict[str, int] = {}
    for r in deti:
        for pr in r["предметы"] or ["не указан"]:
            po_predmetam[pr] = po_predmetam.get(pr, 0) + 1
    po_mesyacam: dict[str, int] = {}
    for r in deti:
        m = r["последнее"][:7]
        po_mesyacam[m] = po_mesyacam.get(m, 0) + 1

    return {
        "дата": today.isoformat(),
        "обновлено": datetime.now().strftime("%H:%M"),
        "сделано_сегодня": sdelano,
        "сделано_вчера": vchera,
        "отмечено": sorted(otm, key=lambda r: r["отметка"]["когда"], reverse=True),
        "пробовали_сегодня": sum(1 for r in deti if r["пробовали_сегодня"]),
        "окно": {"год_с": GOD_S, "год_по": GOD_PO,
                 "лето_с": LETO_S, "лето_по": LETO_PO,
                 "набор_с": NABOR_S, "набор_по": today.isoformat()},
        "по_периодам": {k: sum(1 for r in deti if r["период"] == k)
                        for k in ("год и лето", "учебный год", "лето")},
        "рассылка_уходила": sum(1 for r in deti if r["рассылка"]),
        "говорили_раньше": sum(1 for r in deti if r["говорили_раньше"]),
        "покрытие_журнала": pokrytie,
        "итоги": itogo,
        "детей": len(deti),
        "семей": len(semyi),
        "платили": sum(1 for r in deti if r["платил"]),
        "дети": deti,
        "сказали_нет": otkazniki,
        "никогда_не_набирали": len(nikogda),
        "не_дозвонились": len(ne_dozvonilis),
        "по_предметам": dict(sorted(po_predmetam.items(), key=lambda kv: -kv[1])),
        "по_месяцам": dict(sorted(po_mesyacam.items(), reverse=True)),
        "ходят_сейчас": sorted(aktivnye, key=lambda r: r["имя"]),
    }
