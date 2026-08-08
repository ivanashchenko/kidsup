"""Контент-фабрика: недельный план публикаций и постинг в соцсети.

План строится не «из головы», а из данных CRM: чем свободнее места в
направлении, тем чаще оно попадает в контент. Публикация — во ВКонтакте
и Telegram по API (ключи берутся из переменных окружения).

Запуск:
    python -m app.content plan            — показать план на неделю
    python -m app.content post --dry-run  — что было бы опубликовано сегодня
    python -m app.content post            — опубликовать (нужны токены)
"""

import argparse
import json
import os
from datetime import date, timedelta

import httpx

from . import db

VK_TOKEN = os.environ.get("VK_TOKEN", "")
VK_GROUP_ID = os.environ.get("VK_GROUP_ID", "")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT", "")

# нормативы вместимости
CAPS = {"мини-сад": 12, "нулевой": 12, "раннее развитие": 7, "музыка": 7, "логопед": 1}
DEFAULT_CAP = 9

# рубрики: доля в неделе и шаблоны заголовков
RUBRICS = [
    ("Польза", 0.4, [
        "5 признаков, что ребёнку пора к логопеду",
        "Как понять, готов ли ребёнок к школе: чек-лист для родителей",
        "Что должен уметь малыш в 2 года — и когда не стоит волноваться",
        "Как выбрать частный сад: 7 вопросов, которые стоит задать",
        "Почему ребёнок учит английский 3 года и не говорит",
        "Ребёнок читает медленно — почему из-за этого страдают все предметы",
    ]),
    ("Доказательство", 0.3, [
        "День из жизни мини-сада KidsUP",
        "Дети говорят по-английски: видео с летнего клуба",
        "Было в сентябре — стало в мае: тетради подготовки к школе",
        "Отзыв мамы: как {child} перестал(а) бояться школы",
        "Считает в уме быстрее калькулятора: ментальная арифметика",
    ]),
    ("Люди", 0.2, [
        "Знакомьтесь: педагог {teacher} — почему я работаю с малышами",
        "Как мы готовимся к Дню открытых дверей",
        "Наша команда: кто встречает вашего ребёнка каждый день",
    ]),
    ("Продажа", 0.1, [
        "Оффер месяца: запишитесь до {deadline} и получите диагностику в подарок",
        "Осталось {n} мест в группе {group}",
        "{event} — {when}. Регистрация открыта",
    ]),
]


def _cap(course: str, group: str) -> int:
    s = ((course or "") + " " + (group or "")).lower()
    for key, cap in CAPS.items():
        if key in s:
            return cap
    return DEFAULT_CAP


def gaps(limit: int = 6) -> list[dict]:
    """Направления с самыми большими свободными местами — им больше эфира."""
    since = (date.today() - timedelta(days=270)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT co.name course, cl.name grp,
                   COUNT(DISTINCT l.id) lessons,
                   ROUND(1.0*SUM(lr.visit)/NULLIF(COUNT(DISTINCT l.id),0),1) occ
            FROM lessons l
            JOIN classes cl ON cl.id = l.class_id
            LEFT JOIN courses co ON co.id = cl.course_id
            LEFT JOIN lesson_records lr ON lr.lesson_id = l.id
            WHERE l.status = 1 AND l.date >= ?
            GROUP BY cl.id HAVING lessons >= 8
        """, (since,)).fetchall()
    agg = {}
    for r in rows:
        course = r["course"] or "Прочее"
        cap = _cap(course, r["grp"])
        a = agg.setdefault(course, {"course": course, "cap": 0, "occ": 0.0})
        a["cap"] += cap
        a["occ"] += r["occ"] or 0
    out = []
    for a in agg.values():
        a["free"] = round(a["cap"] - a["occ"], 1)
        a["fill"] = round(100 * a["occ"] / a["cap"]) if a["cap"] else 0
        out.append(a)
    return sorted(out, key=lambda x: -x["free"])[:limit]


def weekly_plan(start: date | None = None) -> list[dict]:
    """Недельный план: 7 постов ВК + 5 Telegram, с приоритетом на дыры."""
    start = start or date.today()
    focus = [g["course"] for g in gaps(4)]
    plan = []
    slots = [
        ("ВКонтакте", "Польза"), ("Telegram", "Польза"),
        ("ВКонтакте", "Доказательство"), ("Telegram", "Продажа"),
        ("ВКонтакте", "Польза"), ("ВКонтакте", "Доказательство"),
        ("Telegram", "Доказательство"), ("ВКонтакте", "Люди"),
        ("Telegram", "Польза"), ("ВКонтакте", "Продажа"),
        ("ВКонтакте", "Доказательство"), ("Telegram", "Люди"),
    ]
    titles = {name: list(items) for name, _, items in RUBRICS}
    for i, (channel, rubric) in enumerate(slots):
        day = start + timedelta(days=i % 7)
        pool = titles.get(rubric) or ["Пост"]
        title = pool[i % len(pool)]
        plan.append({
            "date": day.isoformat(),
            "channel": channel,
            "rubric": rubric,
            "topic": title,
            "focus": focus[i % len(focus)] if focus else "",
        })
    return plan


# --- публикация -------------------------------------------------------------

def post_vk(message: str, dry_run: bool = True) -> str:
    if dry_run:
        return f"[dry-run VK] {message[:80]}…"
    if not (VK_TOKEN and VK_GROUP_ID):
        return "VK: не заданы VK_TOKEN / VK_GROUP_ID"
    r = httpx.post("https://api.vk.com/method/wall.post", data={
        "owner_id": f"-{VK_GROUP_ID}", "from_group": 1, "message": message,
        "access_token": VK_TOKEN, "v": "5.199",
    }, timeout=30)
    return r.text[:300]


def post_telegram(message: str, dry_run: bool = True) -> str:
    if dry_run:
        return f"[dry-run TG] {message[:80]}…"
    if not (TG_TOKEN and TG_CHAT):
        return "Telegram: не заданы TELEGRAM_TOKEN / TELEGRAM_CHAT"
    r = httpx.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json={
        "chat_id": TG_CHAT, "text": message, "parse_mode": "HTML",
    }, timeout=30)
    return r.text[:300]


def main():
    ap = argparse.ArgumentParser(description="Контент-фабрика KidsUP")
    ap.add_argument("command", choices=["plan", "gaps", "post"])
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--text", default="")
    args = ap.parse_args()

    if args.command == "gaps":
        for g in gaps(10):
            print(f"{g['course'][:45]:45s} мест {g['cap']:4d} занято {g['occ']:6.1f} "
                  f"свободно {g['free']:6.1f} ({g['fill']}%)")
    elif args.command == "plan":
        print(f"{'дата':12s} {'канал':12s} {'рубрика':16s} {'фокус':28s} тема")
        for p in weekly_plan():
            print(f"{p['date']:12s} {p['channel']:12s} {p['rubric']:16s} "
                  f"{(p['focus'] or '')[:26]:28s} {p['topic']}")
    elif args.command == "post":
        text = args.text or "Тестовая публикация KidsUP"
        print(post_vk(text, dry_run=args.dry_run or not VK_TOKEN))
        print(post_telegram(text, dry_run=args.dry_run or not TG_TOKEN))


if __name__ == "__main__":
    main()
