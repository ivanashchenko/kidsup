"""Капельная рассылка в Telegram: паузы и живые формулировки.

Зачем. Telegram банит не за содержание, а за поведение: полсотни
одинаковых сообщений подряд с интервалом в доли секунды — это ровно
то, как выглядит спам-бот. 22.08 рассылка по лагерю ушла за пять минут
с шагом 0,7 секунды и одним текстом на всех; канал выжил, но так
проверять судьбу второй раз незачем.

Как здесь. Между сообщениями пауза в десятки секунд со случайным
разбросом, а текст собирается из вариантов: приветствие, связка,
концовка. Факты — даты, программа, цена — не трогаются никогда:
меняется только обёртка вокруг них, как у живого человека, который
пишет одно и то же двадцати людям и поневоле формулирует по-разному.

Ограничения. Не больше HOURLY_CAP сообщений в час и DAILY_CAP в сутки:
темп важнее объёма, а отправка, растянутая на день, выглядит естественно.

Запуск:
    python -m app.tgdrip show     — что и кому уйдёт, без отправки
    python -m app.tgdrip send     — отправить
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import datetime

from . import wazzup

log = logging.getLogger("kidsup.tgdrip")
SP = os.environ.get("KIDSUP_SCRATCH") or "/tmp/kidsup-calls"

# Пауза между сообщениями, секунды. Нижняя граница важнее верхней:
# именно частота, а не объём, отличает рассылку от переписки.
GAP_MIN, GAP_MAX = 25, 95
HOURLY_CAP = 40
DAILY_CAP = 120

# Варианты обёртки. Смысл во всех одинаковый, различается только то,
# как человек это скажет. Факты сюда не попадают принципиально.
HELLO = [
    "Здравствуйте!",
    "Добрый день!",
    "Здравствуйте 🙂",
]
BRIDGE = [
    "Хотела рассказать про последнюю неделю августа.",
    "Пишу про последнюю смену лета.",
    "Коротко про то, что у нас на этой неделе.",
    "Расскажу, что придумали на последнюю неделю.",
]
CLOSING = [
    "Напишите, если интересно, — расскажу про места и время.",
    "Если интересно, напишите: подскажу, что со свободными местами.",
    "Напишите, и я расскажу подробности про время и места.",
    "Отзовитесь, если хочется, — отвечу про места и расписание.",
]


def variant(base: str, seed: int) -> str:
    """Собрать письмо: своя обёртка, общий неизменный середняк.

    seed привязан к получателю, а не к случаю: если сообщение придётся
    отправить повторно, человек увидит тот же текст, а не другой —
    иначе выходит, что мы пишем ему разными словами об одном и том же."""
    rnd = random.Random(seed)
    body = base
    # У готовых текстов campmail первая строка — приветствие с именем;
    # заменяем только её, всё остальное остаётся дословно.
    lines = body.split("\n")
    first = lines[0]
    for h in ("Здравствуйте! ", "Здравствуйте, "):
        if first.startswith(h):
            first = rnd.choice(HELLO) + " " + first[len(h):]
            break
    lines[0] = first
    out = "\n".join(lines)
    # Концовку меняем всегда, когда узнаём стандартную: именно повтор
    # последней фразы у полусотни писем и выглядит как рассылка.
    for old in CLOSING + [
            "Напишите, если интересно, — расскажу про места и время.",
            "Напишите, если интересно, - расскажу про места и время.",
            "Напишите, если хотите подобрать время.",
            "Напишите, подберём время."]:
        if old in out:
            return out.replace(old, rnd.choice(CLOSING))
    return out


def send(rows: list[dict], text_of, dry_run: bool = True,
         gap: tuple[int, int] = (GAP_MIN, GAP_MAX)) -> dict:
    """rows: [{"uid", "name", ...}], text_of(row) -> текст письма."""
    done = _today_count()
    sent, skipped, hour_start, in_hour = 0, 0, time.time(), 0
    for r in rows:
        if done + sent >= DAILY_CAP:
            log.info("дневной предел %d — остальное завтра", DAILY_CAP)
            break
        if in_hour >= HOURLY_CAP:
            wait = max(0, 3600 - (time.time() - hour_start))
            log.info("часовой предел: пауза %d мин", int(wait // 60))
            if not dry_run:
                time.sleep(wait)
            hour_start, in_hour = time.time(), 0
        txt = variant(text_of(r), int(r.get("uid") or 0))
        try:
            # Телефон обязателен: с пустым phone предохранитель слепнет —
            # otkaz.is_refused("") молчит, суточный лимит не считается,
            # и капля уходит даже тому, кто письменно отказался.
            ok = wazzup.send_via("tgapi", r.get("phone") or "", txt,
                                 dry_run=dry_run, uid=r.get("uid"))
        except Exception as e:  # noqa: BLE001
            log.warning("tgdrip: %s — %s", r.get("name"), type(e).__name__)
            ok = False
        if ok:
            sent += 1
            in_hour += 1
        else:
            skipped += 1
        if not dry_run and sent + skipped < len(rows):
            time.sleep(random.uniform(*gap))
    if not dry_run:
        _remember(sent)
    return {"отправлено": sent, "не ушло": skipped, "за сегодня всего": done + sent}


def _today_count() -> int:
    try:
        d = json.load(open(f"{SP}/tg_drip.json"))
        if d.get("день") == datetime.now().strftime("%Y-%m-%d"):
            return int(d.get("послано") or 0)
    except Exception:
        pass
    return 0


def _remember(n: int) -> None:
    try:
        json.dump({"день": datetime.now().strftime("%Y-%m-%d"),
                   "послано": _today_count() + n},
                  open(f"{SP}/tg_drip.json", "w"))
    except Exception:
        pass


def main():
    import sys
    from . import campmail
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    try:
        rows = json.load(open(f"{SP}/tg_ready.json"))
    except Exception:
        print("нет списка (tg_ready.json)")
        return

    def text_of(r):
        return campmail.text_past(r["name"]) if r.get("wave") == "в прошлые годы" \
            else campmail.text_for(r["name"])

    if cmd == "send":
        print(send(rows, text_of, dry_run=False))
        return
    print(f"в списке: {len(rows)}, за сегодня уже послано {_today_count()}")
    print(f"пауза между сообщениями: {GAP_MIN}–{GAP_MAX} сек, "
          f"в час не больше {HOURLY_CAP}, в сутки {DAILY_CAP}\n")
    for r in rows[:3]:
        print("─" * 56)
        print(variant(text_of(r), int(r.get("uid") or 0))[:260])


if __name__ == "__main__":
    main()
