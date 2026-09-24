"""Разбор хвоста: незакрытые дела прошлых дней.

22.09.2026. Аудит пульта показал, что незакрытый пункт в полночь просто
исчезал с глаз — колонка строилась по сегодняшнему дню. За три недели так
накопилось 529 дел с 03.09: обещания семьям, заявки без ответа, напоминания
по деньгам. Их никто не отменял и никто не делал — они просто перестали
показываться.

Теперь они видны, но 529 строк в колонку — это не план, а свалка. Здесь мы
их разбираем: что протухло — закрываем с честной пометкой, что живое —
переносим на сегодня дежурной. Ничего не удаляем: закрытый пункт остаётся
в истории дня со своей пометкой, и по ней всегда видно, почему он закрыт.

  GET  /api/hvost?dry=1   — что будет сделано (ничего не меняет)
  POST /api/hvost/razobrat — сделать
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from . import db

log = logging.getLogger("kidsup.hvost")

# Автоматические источники: их пункты — это сигнал на момент времени.
# Через неделю сигнал уже не сигнал: либо с семьёй с тех пор говорили,
# либо повод отпал сам.
AVTO = ("автоматика", "наряд", "возврат", "сторож имён")

# Статусы, при которых человека трогать нельзя вообще.
STOP_STATUSY = {146328,    # 0.1. Не писать / не звонить
                215202,    # 0.2. Не работаем с ним
                125954}    # Некачественный лид
# Статусы, при которых повод звать отпал.
OTRABOTANO = {125955,      # Клиент
              125957,      # Отказ
              345759}      # 0. Архив набора

PUSTYSHKA = re.compile(r"действий не требуется|не требует действий|\[непонятно\]", re.I)

# Обязательство перед семьёй или перед законом не гаснет от того, что семья
# заплатила или сменила статус: деньги вернуть, документ выдать, на жалобу
# ответить. Такие пункты автоматически не закрываем никогда — только человек.
OBYAZATELSTVO = re.compile(
    r"вернуть деньги|возврат денег|возврат остатка|забрать остаток|"
    r"жалоб|претенз|договор|справк|чек|акт |документ|налогов|маткапитал|"
    r"долг|задолж|извинит|компенсац|не понравил|соискател|резюме|"
    r"ждёт ответа|ждет ответа", re.I)


def _p10(x) -> str:
    d = "".join(c for c in str(x or "") if c.isdigit())
    return d[-10:] if len(d) >= 10 else ""


def _kontakty(conn, s_daty: str) -> dict[str, str]:
    """Телефон → когда мы последний раз реально касались этой семьи."""
    out: dict[str, str] = {}

    def _vzyat(sql, args=()):
        try:
            for ph, ts in conn.execute(sql, args).fetchall():
                p = _p10(ph)
                if p and str(ts or "") > out.get(p, ""):
                    out[p] = str(ts)
        except Exception:
            pass

    _vzyat("SELECT phone, MAX(ts) FROM wazzup_outbox WHERE ts >= ? GROUP BY phone", (s_daty,))
    _vzyat("SELECT phone, MAX(ts) FROM wazzup_inbox WHERE ts >= ? GROUP BY phone", (s_daty,))
    _vzyat("SELECT phone, MAX(ts) FROM mango_calls WHERE state='talked' AND ts >= ? "
           "GROUP BY phone", (s_daty,))
    return out


def _statusy(conn) -> dict[str, int]:
    """Телефон → статус карточки (берём самый «продвинутый» на номере)."""
    out: dict[str, int] = {}
    try:
        for ph, st in conn.execute(
                "SELECT phone, client_state_id FROM users WHERE phone IS NOT NULL").fetchall():
            p = _p10(ph)
            if not p:
                continue
            st = int(st or 0)
            # Клиент важнее отказа: если на номере двое детей и один ходит,
            # семья живая.
            if out.get(p) == 125955:
                continue
            out[p] = st
    except Exception:
        pass
    return out


def _platili(conn, s_daty: str) -> set[str]:
    """Телефоны, с которых платили после этой даты — повод звать отпал."""
    out: set[str] = set()
    try:
        for (ph,) in conn.execute(
                "SELECT u.phone FROM payments p JOIN users u ON u.id = p.user_id "
                "WHERE p.date >= ?", (s_daty,)).fetchall():
            p = _p10(ph)
            if p:
                out.add(p)
    except Exception:
        pass
    return out


def razobrat(dry: bool = True, den_starosti: int = 7, perenesti: bool = False) -> dict:
    """Разложить хвост на живое и протухшее.

    den_starosti — с какого возраста автоматический пункт считается
    просроченным сигналом (по умолчанию неделя).

    perenesti=False (по умолчанию): живое НЕ переносим на сегодня. Оно и так
    видно в колонке блоком «Не закрыто с прошлых дней», а свалить 231 строку
    в сегодняшний план — значит снова сделать из плана гору.
    """
    from . import pult
    segodnya = pult.today()
    porog = (date.fromisoformat(segodnya) - timedelta(days=den_starosti)).isoformat()
    itog = {"день": segodnya, "просмотрено": 0, "закрыть": [], "перенести": [],
            "по причинам": {}, "сделано": not dry}
    with db.get_conn() as conn:
        try:
            rows = conn.execute(
                "SELECT id, day, who, text, phone, source FROM plan_inbox "
                "WHERE done=0 AND day<? ORDER BY day, id", (segodnya,)).fetchall()
        except Exception as e:  # noqa: BLE001
            return {**itog, "ошибка": str(e)[:120]}
        samoe_staroe = min((r["day"] for r in rows), default=segodnya)
        kontakty = _kontakty(conn, samoe_staroe)
        statusy = _statusy(conn)
        platili = _platili(conn, samoe_staroe)
        # свежие открытые пункты по тем же семьям — старый дубль не нужен
        svezhie = set()
        try:
            for (ph,) in conn.execute(
                    "SELECT phone FROM plan_inbox WHERE done=0 AND day=?",
                    (segodnya,)).fetchall():
                p = _p10(ph)
                if p:
                    svezhie.add(p)
        except Exception:
            pass

        vidno: set[str] = set()
        for r in rows:
            itog["просмотрено"] += 1
            p = _p10(r["phone"])
            avto = (r["source"] or "").split()[0] in AVTO if r["source"] else False
            star = r["day"] < porog
            prichina = ""

            # Пункты владельца автоматически не закрываем. Среди них не
            # только клиенты: соискатель, который ждёт ответа с 13:47,
            # партнёрское предложение, решение по группе. Их номера иногда
            # совпадают с карточками «некачественный лид», и правило про
            # стоп-статус закрыло бы живого человека (22.09, проверка разбора).
            obyaz = bool(OBYAZATELSTVO.search(r["text"] or "")) or r["who"] == "Борис"
            if obyaz:
                prichina = ""      # обязательства закрывает только человек
            elif PUSTYSHKA.search(r["text"] or ""):
                prichina = "по пункту нечего делать"
            elif p and statusy.get(p) in STOP_STATUSY:
                prichina = "карточка в стоп-статусе — трогать нельзя"
            elif p and p in platili:
                prichina = "семья с тех пор оплатила — повод отпал"
            elif p and statusy.get(p) in OTRABOTANO:
                prichina = "статус карточки уже закрыт (клиент, отказ или архив)"
            elif p and p in svezhie:
                prichina = "по этой семье уже стоит свежий пункт на сегодня"
            elif avto and star and p and kontakty.get(p, "") > r["day"]:
                prichina = f"после {r['day'][8:10]}.{r['day'][5:7]} мы с семьёй уже говорили"
            elif avto and star and not p:
                prichina = "автоматический сигнал недельной давности без телефона"
            elif p and p in vidno:
                prichina = "по этой семье в хвосте уже есть другой пункт"

            if prichina:
                itog["закрыть"].append({"id": r["id"], "день": r["day"], "кто": r["who"],
                                        "почему": prichina, "текст": (r["text"] or "")[:90]})
                itog["по причинам"][prichina] = itog["по причинам"].get(prichina, 0) + 1
            else:
                if p:
                    vidno.add(p)
                itog["перенести"].append({"id": r["id"], "день": r["day"], "кто": r["who"],
                                          "текст": (r["text"] or "")[:90]})

        if not dry:
            for x in itog["закрыть"]:
                conn.execute(
                    "UPDATE plan_inbox SET done=1, text = text || ? WHERE id=?",
                    (f" — закрыто разбором хвоста {segodnya}: {x['почему']}", x["id"]))
            # Живое переносим на сегодня, сохраняя исходную дату в тексте,
            # чтобы было видно, сколько семья ждёт.
            if perenesti:
                for x in itog["перенести"]:
                    conn.execute("UPDATE plan_inbox SET day=? WHERE id=?", (segodnya, x["id"]))
            log.warning("разбор хвоста: закрыто %d, перенесено на сегодня %d",
                        len(itog["закрыть"]), len(itog["перенести"]))
    itog["итого закрыть"] = len(itog["закрыть"])
    itog["итого перенести"] = len(itog["перенести"])
    return itog


# Пункты, которые владелец велел закрыть насовсем: подбор персонала мы не
# ведём (решение Бориса 22.09.2026 — «соискатели, нам не нужны»).
NE_NUZHNO = re.compile(r"соискател|резюме|ваканси|hh\.ru|на работу устро|"
                       r"ищет работу|трудоустрой", re.I)


def _semya(conn) -> dict[str, set[int]]:
    """Телефон → все карточки на нём. Обещание даётся семье, а работа может
    быть записана в карточку другого ребёнка того же родителя."""
    out: dict[str, set[int]] = {}
    try:
        for uid, ph in conn.execute("SELECT id, phone FROM users").fetchall():
            p = _p10(ph)
            if p:
                out.setdefault(p, set()).add(int(uid))
    except Exception:
        pass
    return out


def _posle(conn, sql: str, args=()) -> dict:
    """uid → максимальная дата события. Молча переживает отсутствие таблицы."""
    out: dict[int, str] = {}
    try:
        for uid, ts in conn.execute(sql, args).fetchall():
            if uid is None:
                continue
            t = str(ts or "")
            if t > out.get(int(uid), ""):
                out[int(uid)] = t
    except Exception:
        pass
    return out


def proverit(zakryt: bool = False, dry: bool = True) -> dict:
    """Пройти по каждому живому пункту хвоста и проверить доказательствами,
    сделан он или нет.

    22.09.2026, Борис: «можешь перепроверить по каждой задаче, что это реально
    ещё не сделано??» — могу, но только по следам, которые остаются в системе.
    Их четыре, и они разной силы:
      • оплата семьи после даты пункта — повод точно отработан;
      • ребёнок был на занятии после даты — семья дошла;
      • разговор от 30 секунд по журналу Манго — с семьёй говорили;
      • комментарий в карточке после даты — админ карточку открывал и писал.
    Слабее: наше исходящее сообщение или входящее от клиента — контакт был,
    но обещание могло остаться невыполненным. Такие в «сделано» не идут:
    их показываем отдельно, решает человек.
    """
    from . import pult
    segodnya = pult.today()
    itog = {"день": segodnya, "проверено": 0,
            "сделано": [], "касались": [], "не трогали": [], "не нужно": [],
            "по доказательствам": {}, "сделано_записано": bool(zakryt and not dry)}
    with db.get_conn() as conn:
        try:
            rows = conn.execute(
                "SELECT id, day, who, text, phone, source FROM plan_inbox "
                "WHERE done=0 AND day<? ORDER BY day, id", (segodnya,)).fetchall()
        except Exception as e:  # noqa: BLE001
            return {**itog, "ошибка": str(e)[:120]}
        if not rows:
            return itog
        s_daty = min(r["day"] for r in rows)
        semya = _semya(conn)
        oplaty = _posle(conn, "SELECT user_id, MAX(date) FROM payments "
                              "WHERE date >= ? GROUP BY user_id", (s_daty,))
        vizity = _posle(conn, "SELECT r.user_id, MAX(l.date) FROM lesson_records r "
                              "JOIN lessons l ON l.id = r.lesson_id "
                              "WHERE r.visit = 1 AND l.date >= ? GROUP BY r.user_id", (s_daty,))
        # Комментарий в карточке — доказательство только когда его написал
        # ЖИВОЙ администратор. 22.09.2026: первая версия проверки засчитала
        # 143 пункта «сделано» по комментариям, а туда пишет и автопилот
        # («🎯 ПОДСКАЗКА ДЛЯ ЗВОНКА» в каждого нового лида), и мой же разбор
        # звонков — от того же аккаунта, что админы. Правило «кто человек,
        # а кто робот» уже выверено в воронке, берём его оттуда.
        from .voronka import MANAGERS as _MGR, _robot as _robot_text
        kommenty: dict[int, str] = {}
        robotskie = 0
        try:
            for uid, ts, mid, txt in conn.execute(
                    "SELECT user_id, ts, manager_id, text FROM crm_comments "
                    "WHERE ts >= ?", (s_daty,)).fetchall():
                if mid not in _MGR or _robot_text(txt):
                    robotskie += 1
                    continue
                if str(ts or "") > kommenty.get(int(uid), ""):
                    kommenty[int(uid)] = str(ts)
        except Exception:
            pass
        itog["комментариев_автоматики_не_в_счёт"] = robotskie
        # телефонные следы — по номеру, не по карточке
        razgovory: dict[str, str] = {}
        try:
            cols = {c[1] for c in conn.execute("PRAGMA table_info(mango_calls)")}
            secs = "AND COALESCE(secs,999) >= 30" if "secs" in cols else ""
            for ph, ts in conn.execute(
                    f"SELECT phone, MAX(ts) FROM mango_calls WHERE state='talked' "
                    f"AND ts >= ? {secs} GROUP BY phone", (s_daty,)).fetchall():
                p = _p10(ph)
                if p and str(ts or "") > razgovory.get(p, ""):
                    razgovory[p] = str(ts)
        except Exception:
            pass
        pisali: dict[str, str] = {}
        for tbl in ("wazzup_outbox", "wazzup_inbox"):
            try:
                for ph, ts in conn.execute(
                        f"SELECT phone, MAX(ts) FROM {tbl} WHERE ts >= ? GROUP BY phone",
                        (s_daty,)).fetchall():
                    p = _p10(ph)
                    if p and str(ts or "") > pisali.get(p, ""):
                        pisali[p] = str(ts)
            except Exception:
                pass

        zakryvat: list[tuple[int, str]] = []
        for r in rows:
            itog["проверено"] += 1
            den = r["day"]
            p = _p10(r["phone"])
            uids = semya.get(p, set()) if p else set()
            karta = {"id": r["id"], "день": den, "кто": r["who"],
                     "текст": (r["text"] or "")[:100]}

            if NE_NUZHNO.search(r["text"] or ""):
                karta["почему"] = "подбор персонала мы не ведём (решение владельца 22.09)"
                itog["не нужно"].append(karta)
                zakryvat.append((r["id"], karta["почему"]))
                continue

            dokazatelstva = []
            if any(oplaty.get(u, "") > den for u in uids):
                dokazatelstva.append("семья оплатила после этой даты")
            if any(vizity.get(u, "") > den for u in uids):
                dokazatelstva.append("ребёнок был на занятии после этой даты")
            if p and razgovory.get(p, "") > den:
                dokazatelstva.append(f"разговор от 30 с — {razgovory[p][:10]}")
            if any(kommenty.get(u, "") > den for u in uids):
                dokazatelstva.append("администратор писал в карточку после этой даты")

            # Пункты владельца по следам в карточках клиентов не закрываем:
            # у него организационное («групп робототехники в CRM нет»,
            # «партнёрское предложение»), и запись админа в чужой карточке
            # про это ничего не говорит. Показываем как подсказку — решает он.
            if dokazatelstva and r["who"] == "Борис":
                karta["след"] = "по семье работа была: " + "; ".join(dokazatelstva) + \
                                " — но это твой пункт, закрывать тебе"
                itog["касались"].append(karta)
                continue
            if dokazatelstva:
                karta["доказательства"] = dokazatelstva
                itog["сделано"].append(karta)
                for d_ in dokazatelstva:
                    k = d_.split(" —")[0]
                    itog["по доказательствам"][k] = itog["по доказательствам"].get(k, 0) + 1
                zakryvat.append((r["id"], "проверено по следам: " + "; ".join(dokazatelstva)))
            elif p and pisali.get(p, "") > den:
                karta["след"] = f"переписка была {pisali[p][:10]}, но обещание могло остаться"
                itog["касались"].append(karta)
            elif not p:
                karta["след"] = "телефона в пункте нет — проверить нечем, смотрит человек"
                itog["не трогали"].append(karta)
            else:
                karta["след"] = "ни звонка, ни сообщения, ни комментария — не сделано"
                itog["не трогали"].append(karta)

        if zakryt and not dry:
            for iid, pochemu in zakryvat:
                conn.execute("UPDATE plan_inbox SET done=1, text = text || ? WHERE id=?",
                             (f" — закрыто {segodnya}: {pochemu}", iid))
            log.warning("перепроверка хвоста: закрыто %d пунктов", len(zakryvat))
    for k in ("сделано", "касались", "не трогали", "не нужно"):
        itog[f"итого {k}"] = len(itog[k])
    return itog


def utro(dry: bool = False) -> dict:
    """Утренняя актуализация пульта — раз в день, до начала смены.

    24.09.2026, Борис: «Пульт регулярно обновляется??!!» Новые дела приходили
    сами (наряд, разбор звонков, автопилот), а вчерашние недоделанные в
    полночь уезжали в свёрнутый хвост — к тому, кто сегодня может и не
    работать. Разбирали их только вручную.

    Теперь каждое утро:
      1. Закрываем пункт хвоста только по сильному следу после его даты:
         оплата семьи, визит ребёнка, разговор от 30 секунд. Комментарий
         админа не в счёт — «не берут трубку» тоже комментарий. Обязательства
         (деньги, документы, жалобы) и дела Бориса не закрываем никогда.
      2. Остальное живое переносим на сегодня: своему человеку, если он в
         смене, иначе — дежурной, у которой меньше дел. Уровень важности
         (prio) сохраняется, пометка «⏳ с ДД.ММ» — чтобы было видно возраст.
    Задачи смены (pult_tasks) не переносим: они собраны под свой день.
    """
    from . import pult
    segodnya = pult.today()
    smena = [w for w in pult.duty(segodnya) if w in set(pult.SHORT.values())]
    itog = {"день": segodnya, "смена": smena, "закрыто": [], "перенесено": 0,
            "по_людям": {}, "dry": dry}
    with db.get_conn() as conn:
        pult._inbox_tries(conn)
        rows = conn.execute(
            "SELECT id, day, who, text, phone FROM plan_inbox "
            "WHERE done=0 AND day<? ORDER BY day, id", (segodnya,)).fetchall()
        if not rows:
            return itog
        s_daty = min(r["day"] for r in rows)
        semya = _semya(conn)
        oplaty = _posle(conn, "SELECT user_id, MAX(date) FROM payments "
                              "WHERE date >= ? AND summa > 0 GROUP BY user_id", (s_daty,))
        vizity = _posle(conn, "SELECT r.user_id, MAX(l.date) FROM lesson_records r "
                              "JOIN lessons l ON l.id = r.lesson_id "
                              "WHERE r.visit = 1 AND l.date >= ? GROUP BY r.user_id", (s_daty,))
        razgovory: dict[str, str] = {}
        try:
            cols = {c[1] for c in conn.execute("PRAGMA table_info(mango_calls)")}
            if "secs" in cols:
                for ph, ts in conn.execute(
                        "SELECT phone, MAX(ts) FROM mango_calls WHERE state='talked' "
                        "AND COALESCE(secs, 0) >= 30 AND ts >= ? GROUP BY phone", (s_daty,)).fetchall():
                    p = _p10(ph)
                    if p and str(ts or "") > razgovory.get(p, ""):
                        razgovory[p] = str(ts)
        except Exception:
            pass
        nagruzka = {w: conn.execute(
            "SELECT COUNT(*) FROM plan_inbox WHERE done=0 AND day=? AND who=?",
            (segodnya, w)).fetchone()[0] for w in smena}
        for r in rows:
            den, kto, tekst = r["day"], r["who"], r["text"] or ""
            p = _p10(r["phone"])
            uids = semya.get(p, set()) if p else set()
            sled = []
            if kto != "Борис" and not OBYAZATELSTVO.search(tekst):
                if any(oplaty.get(u, "") > den for u in uids):
                    sled.append("семья оплатила после этой даты")
                if any(vizity.get(u, "") > den for u in uids):
                    sled.append("ребёнок был на занятии после этой даты")
                if p and razgovory.get(p, "")[:10] > den:
                    sled.append(f"разговор от 30 с {razgovory[p][:10]}")
            if sled:
                itog["закрыто"].append({"id": r["id"], "кто": kto, "почему": "; ".join(sled)})
                if not dry:
                    conn.execute("UPDATE plan_inbox SET done=1, text = text || ? WHERE id=?",
                                 (f" — закрыто {segodnya} утром: {'; '.join(sled)}", r["id"]))
                continue
            novyj = kto
            if kto in set(pult.SHORT.values()) and smena and kto not in smena:
                novyj = min(smena, key=lambda w: nagruzka.get(w, 0))
            if novyj in nagruzka:
                nagruzka[novyj] += 1
            metka = "" if tekst.startswith("⏳") else f"⏳ с {den[8:10]}.{den[5:7]}: "
            if len(metka) + len(tekst) > 400:
                metka = ""          # обрезанный на полуслове текст хуже, чем без пометки
            itog["перенесено"] += 1
            itog["по_людям"][novyj] = itog["по_людям"].get(novyj, 0) + 1
            if not dry:
                conn.execute("UPDATE plan_inbox SET day=?, who=?, text=? WHERE id=?",
                             (segodnya, novyj, (metka + tekst)[:400], r["id"]))
    if not dry:
        log.warning("утро пульта: закрыто %d, перенесено %d (%s)", len(itog["закрыто"]),
                    itog["перенесено"], itog["по_людям"])
    return itog
