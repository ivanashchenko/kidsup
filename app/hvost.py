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
