"""Рассылка по набору 2026/27 в мессенджеры, пока WABA на модерации.

Зачем именно так. Пять возрастных шаблонов ушли в Meta на модерацию
25.08 и раньше чем через сутки-двое не вернутся, а группы забивать надо
сейчас. Telegram и MAX модерации не требуют: там, где с семьёй уже есть
живая переписка, писать можно немедленно и бесплатно. Это 265 семей из
очереди — треть аудитории, и как раз самая тёплая её часть.

Чем текст здесь отличается от WABA-шаблона. Шаблон Meta ограничен по
длине и не терпит ссылок на молодые домены — оттуда пришлось вырезать
и цены, и «условно-бесплатное». В мессенджере таких ограничений нет,
поэтому текст живой и полный: возрастная выборка предметов, три даты
начала сезона и честная формулировка про первое занятие.

Две вещи, на которых легко ошибиться и которые здесь разведены:

· «Занимался у нас» пишем ТОЛЬКО тем, кто действительно платил. Для
  остальных (в очереди это 97 семей, в основном старые заявки) заход
  другой — «вы интересовались занятиями». Иначе первая же строка врёт,
  и дальше текст читать не будут.

· Пол ребёнка. «Вероника занимался» убивает письмо вернее, чем любая
  ошибка в цене.

WhatsApp сюда НЕ входит осознанно: обычный номер на массовом потоке
банится (22.08 так потеряли 0077), а WABA ждёт модерации. Как шаблоны
одобрят — WABA и СМС уйдут вторым заходом по тем, кто промолчал.

Запуск:
    python -m app.nabormail show          — кому и что уйдёт
    python -m app.nabormail build         — собрать очередь и сохранить
    python -m app.nabormail send [N]      — пробный прогон
    python -m app.nabormail send --real   — отправить
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from datetime import date

from . import db, sync, taskguard, wazzup
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.nabormail")

SEASON = date(2026, 9, 1)
QUEUE_KEY = "nabormail_queue"      # что осталось отправить
DONE_KEY = "nabormail_done"        # кому уже ушло, чтобы не задвоить
NEXT_KEY = "nabormail_next"        # когда разрешена следующая отправка
BATCH = 1                          # по одному письму за заход — темп живого человека
MSGR = ("tgapi", "max")            # только мессенджеры: WABA на модерации
ACTIVE_JOIN = {2, 50509, 58131, 58132, 83760}
SKIP_STATE = {146328, 125954, 125957}

DATES = ("29 августа — праздник открытия сезона\n"
         "30 августа — день открытых дверей\n"
         "31 августа – 6 сентября — неделя открытых уроков")

TRIAL = ("Первое занятие условно-бесплатное: не понравится — платить "
         "не нужно, понравится — оно входит в первый абонемент.")

# Что предлагаем в каждом возрасте. Списки короткие намеренно: длинное
# меню родитель не дочитывает, а выбирает из первых двух строк.
OFFER = {
    "1-3": ("Для малышей до 3 лет:\n"
            "• Раннее развитие «Первая школа» — вторник и четверг, "
            "есть воскресные группы\n"
            "• Мини-сад с английским, с 9:00 до 13:00 — два, три "
            "или пять дней в неделю\n"
            "• Занятия с логопедом — индивидуально"),
    "3-5": ("Для возраста 3–5 лет:\n"
            "• «Лицей для малышей» — среда и пятница, днём\n"
            "• Подготовка к школе с 4 лет — будни с 16:00 до 19:00\n"
            "• Английский по уровням Cambridge\n"
            "• ИЗО-студия, занятия с логопедом"),
    "5-7": ("Главное в 5–7 лет — подготовка к школе: 15 групп, будни "
            "с 16:00 до 19:00, есть субботние. Перед началом педагог "
            "смотрит, как ребёнок читает, и подбирает ступень: ПШ1 для "
            "нечитающих или ПШ2 для читающих.\n\n"
            "Что ещё есть:\n"
            "• Английский по уровням Cambridge — будни с 17:00\n"
            "• Ментальная арифметика, шахматы, ИЗО-студия\n"
            "• Нулевой класс и занятия с логопедом"),
    "7-12": ("Для школьников 7–12 лет:\n"
             "• Английский по уровням Cambridge — Starters, Movers, Flyers\n"
             "• Ментальная арифметика — воскресенье, днём\n"
             "• Шахматы с 6 лет, две группы\n"
             "• ИЗО-студия «Шедевры великих художников»\n\n"
             "Собираем группы скорочтения и робототехники — если интересно, "
             "отметим вас в списке и позовём первыми."),
    "?": ("Что открыто:\n"
          "• С 1,5 лет — раннее развитие и мини-сад с английским\n"
          "• С 3 лет — «Лицей для малышей», английский, ИЗО-студия\n"
          "• С 4 лет — подготовка к школе, 15 групп, будни с 16:00 до 19:00\n"
          "• С 6 лет — шахматы и ментальная арифметика\n"
          "• 7–12 лет — английский по уровням Cambridge\n"
          "• Логопед — в любом возрасте, индивидуально"),
}


def _first_name(full: str) -> str:
    parts = [w for w in (full or "").split("(")[0].split() if w]
    if len(parts) >= 2 and parts[1][:1].isupper():
        return parts[1]
    return parts[0] if parts else ""


def _zanimalsya(name: str) -> str:
    from .hint import is_female
    return "занималась" if is_female(name) else "занимался"


def text_for(name: str, seg: str, paid: bool) -> str:
    """Письмо семье. Первая строка разная у тех, кто у нас учился,
    и у тех, кто только оставлял заявку, — вторым нельзя писать
    «ваш ребёнок занимался», это неправда с первого слова."""
    child = _first_name(name)
    if paid:
        who = (f"{child} {_zanimalsya(child)} у нас в KidsUP на бульваре "
               f"Рокоссовского" if child else
               "Ваш ребёнок занимался у нас в KidsUP на бульваре Рокоссовского")
        hello = f"Здравствуйте! {who}."
    else:
        hello = ("Здравствуйте! Вы интересовались занятиями в KidsUP "
                 "на бульваре Рокоссовского.")
    return (f"{hello} 31 августа начинается учебный год, и мы набираем "
            f"группы.\n\n{OFFER.get(seg, OFFER['?'])}\n\n{DATES}\n\n{TRIAL}\n\n"
            f"Напишите, если удобно подобрать время, — подскажу, "
            f"где ещё есть места.")


def _seg(age: float | None) -> str:
    if age is None:
        return "?"
    if age < 3:
        return "1-3"
    if age < 5:
        return "3-5"
    if age < 7.5:
        return "5-7"
    if age <= 12.5:
        return "7-12"
    return "?"


def collect() -> list[dict]:
    """Кому можно писать прямо сейчас: не записан на новый сезон и есть
    живая переписка в Telegram или MAX."""
    mk = MoyklassClient(sync.get_api_key())
    try:
        users = taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=2)
        joins = taskguard.pull_all(mk, "/v1/company/joins", "joins")
        subs = taskguard.pull_all(mk, "/v1/company/userSubscriptions",
                                  "subscriptions", cache_hours=6)
        rc = mk.get("/v1/company/classes", {"limit": 500})
        cls = {c["id"]: (c.get("name") or "")
               for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
    finally:
        mk.close()

    booked = {j["userId"] for j in joins
              if cls.get(j.get("classId"), "").startswith("2627")
              and j.get("statusId") in ACTIVE_JOIN}
    paid = {s["userId"] for s in subs
            if (s.get("stats") or {}).get("totalPayed", 0) > 0}

    out = []
    for u in users:
        uid = u["id"]
        if uid in booked or u.get("clientStateId") in SKIP_STATE:
            continue
        phone = "".join(c for c in str(u.get("phone") or "") if c.isdigit())[-10:]
        msgr = [t for t in wazzup.channels_for(phone, uid=uid, mass=True)
                if t in MSGR]
        if not msgr:
            continue
        bd = next((a.get("value") for a in (u.get("attributes") or [])
                   if a.get("attributeAlias") == "birthday"), None)
        age = None
        if bd:
            try:
                age = round((SEASON - date.fromisoformat(bd[:10])).days / 365.25, 1)
            except ValueError:
                pass
        out.append({"uid": uid, "phone": phone, "name": (u.get("name") or "").strip(),
                    "seg": _seg(age), "paid": uid in paid, "msgr": msgr})
    # старшие группы вперёд: подготовка к школе и английский — то, что
    # владелец просил забивать в первую очередь
    order = {"5-7": 0, "7-12": 1, "3-5": 2, "1-3": 3, "?": 4}
    out.sort(key=lambda r: (order.get(r["seg"], 9), not r["paid"]))
    return out


def build() -> int:
    rows = collect()
    done = {str(x) for x in json.loads(db.get_setting(DONE_KEY, "[]") or "[]")}
    # в реестре встречаются оба формата: голый id (до 25.08) и «вид:id»
    rows = [r for r in rows
            if f"nabor:{r['uid']}" not in done and str(r["uid"]) not in done]
    db.set_setting(QUEUE_KEY, json.dumps(rows, ensure_ascii=False))
    log.info("очередь рассылки: %d семей", len(rows))
    return len(rows)


def tick(dry_run: bool = False, batch: int = 1) -> dict:
    """Одно сообщение за заход, в темпе живого человека.

    Первая версия слала по 40 штук с паузой в полсекунды — сорок
    сообщений за двадцать секунд. Для Telegram и MAX это подпись
    рассылочного бота: аккаунт с таким поведением блокируют, и тогда
    мы теряем не одну рассылку, а единственный бесплатный канал связи
    с семьями. Владелец остановил это 25.08 на сороковом сообщении.

    Теперь автопилот зовёт tick каждую минуту, а темп задаёт сам модуль:
    следующая отправка разрешена не раньше времени в NEXT_KEY, и после
    каждого сообщения оно сдвигается на случайные 60–150 секунд. Примерно
    раз в пятнадцать сообщений — «перерыв» на 5–12 минут: человек не пишет
    ровным метрономом весь день, и именно ровность выдаёт машину.

    Выходит около тридцати сообщений в час. Двести с лишним писем
    занимают рабочий день целиком — и это правильная цена за то, чтобы
    каналы остались живыми."""
    import random
    from datetime import datetime, timedelta

    now = datetime.utcnow() + timedelta(hours=3)          # МСК
    if not dry_run:
        nxt = db.get_setting(NEXT_KEY, "") or ""
        if nxt and now.isoformat(timespec="seconds") < nxt:
            return {}
    queue = json.loads(db.get_setting(QUEUE_KEY, "[]") or "[]")
    if not queue:
        return {}
    # Реестр отправленных начинался как список голых id карточек, а стал
    # списком ключей «вид:id». Старые записи приводим к строкам при чтении:
    # без этого sorted() на смеси чисел и строк роняет всю отправку —
    # 25.08 рассылка встала на час именно так.
    done = {str(x) for x in json.loads(db.get_setting(DONE_KEY, "[]") or "[]")}
    stat: Counter = Counter()
    sent_uids = []
    # Ключ «кому уже ушло» учитывает вид письма: одна и та же семья может
    # получить и рассылку по набору, и подтверждение записи — это разные
    # сообщения, и второе не должно пропасть только потому, что первое ушло.
    def _key(row):
        kind = row.get("kind") or "nabor"
        # старые записи лежат как голый id — считаем их отправленной рассылкой
        return str(row["uid"]) if kind == "nabor" and str(row["uid"]) in done \
            else f"{kind}:{row['uid']}"

    for r in queue[:max(1, batch)]:
        if _key(r) in done:
            sent_uids.append(_key(r))
            continue
        # Строка очереди может нести собственный текст — так сюда
        # попадает переотправка подтверждений записи: у неё тот же
        # предохранитель по темпу, что и у рассылки, а гнать её отдельным
        # циклом значило бы иметь два независимых крана в один канал.
        txt = r.get("text") or text_for(r["name"], r["seg"], r["paid"])
        ok = False
        for t in r["msgr"]:
            try:
                if wazzup.send_via(t, r["phone"], txt, dry_run=dry_run,
                                   uid=r["uid"],
                                   kind=r.get("kind") or "nabor"):
                    stat[t] += 1
                    ok = True
            except Exception as e:
                log.warning("uid=%s %s: %s", r["uid"], t, str(e)[:90])
        stat["доставлено" if ok else "отказ"] += 1
        if ok:
            sent_uids.append(_key(r))
            if r.get("task_id") and not dry_run:
                close_task(r["task_id"])
    if not dry_run:
        n = len(done) + len(sent_uids)
        pause = random.uniform(300, 720) if n and n % 15 == 0 \
            else random.uniform(60, 150)
        if not stat.get("доставлено"):
            pause = max(pause, 240)      # отказ — притормозить, не долбить
        db.set_setting(NEXT_KEY,
                       (now + timedelta(seconds=pause)).isoformat(timespec="seconds"))
        if sent_uids:
            done |= set(sent_uids)
            db.set_setting(DONE_KEY, json.dumps(sorted(done)))
            left = [r for r in queue if _key(r) not in done]
            db.set_setting(QUEUE_KEY, json.dumps(left, ensure_ascii=False))
            stat["осталось"] = len(left)
    return dict(stat)


def liza_to_queue() -> int:
    """Задачи Лизы «звонки не помогают — написать» берём на себя.

    25.08 у неё висело 288 открытых задач, из них 227 со сроком «сегодня».
    Восемнадцать из них — написать клиенту предложение по новому году:
    это ровно то, что делает рассылка, только адресно. Ставим их в общую
    очередь, чтобы они шли тем же спокойным темпом и не устроили
    администратору вал ответов, а задачу закрываем по факту отправки.

    Тех, кому сообщение уже ушло сегодня, пропускаем: получить два письма
    в один день от одного центра — это ровно то, на что клиенты жаловались."""
    from .moyklass_client import MoyklassClient
    mk = MoyklassClient(sync.get_api_key())
    try:
        tasks = mk.fetch_all("/v1/company/tasks", ["tasks"], params={"limit": 500}) or []
        users = {u["id"]: u for u in
                 taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=2)}
    finally:
        mk.close()
    mine = [t for t in tasks
            if not t.get("isComplete") and 154181 in (t.get("managerIds") or [])
            and t.get("categoryId") == 104575 and t.get("userId")
            and "НАПИСАТЬ" in str(t.get("body") or "")]
    done = {str(x) for x in json.loads(db.get_setting(DONE_KEY, "[]") or "[]")}
    queue = json.loads(db.get_setting(QUEUE_KEY, "[]") or "[]")
    have = {f"{r.get('kind') or 'nabor'}:{r['uid']}" for r in queue}
    rows = []
    for t in mine:
        uid = t["userId"]
        key = f"liza:{uid}"
        if key in done or key in have:
            continue
        if f"nabor:{uid}" in done or str(uid) in done:
            continue                       # сегодня уже писали
        u = users.get(uid)
        if not u:
            continue
        phone = "".join(c for c in str(u.get("phone") or "") if c.isdigit())[-10:]
        if len(phone) != 10:
            continue
        bd = next((a.get("value") for a in (u.get("attributes") or [])
                   if a.get("attributeAlias") == "birthday"), None)
        age = None
        if bd:
            try:
                age = round((SEASON - date.fromisoformat(bd[:10])).days / 365.25, 1)
            except ValueError:
                pass
        rows.append({"uid": uid, "phone": phone, "name": (u.get("name") or "").strip(),
                     "seg": _seg(age), "paid": True, "kind": "liza",
                     "task_id": t["id"],
                     "msgr": wazzup.channels_for(phone, uid=uid, mass=True)})
    db.set_setting(QUEUE_KEY, json.dumps(queue + rows, ensure_ascii=False))
    log.info("задач Лизы в очередь: %d", len(rows))
    return len(rows)


def close_task(task_id: int) -> None:
    """Закрыть задачу Лизы после того, как сообщение ушло."""
    from .moyklass_client import MoyklassClient
    mk = MoyklassClient(sync.get_api_key())
    try:
        t = mk.get(f"/v1/company/tasks/{task_id}")
        payload = {k: t.get(k) for k in ("body", "beginDate", "endDate", "isAllDay",
                                         "managerIds", "userId", "classIds",
                                         "filialIds", "categoryId")}
        payload["isComplete"] = True
        payload["body"] = ("🤖 Клод написал клиенту предложение по новому году. "
                           + str(payload.get("body") or ""))[:250]
        mk.post(f"/v1/company/tasks/{task_id}", payload)
    except Exception as e:
        log.warning("задача %s не закрылась: %s", task_id, str(e)[:80])
    finally:
        mk.close()


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "build":
        print("в очереди:", build())
        return
    if cmd == "send":
        dry = "--real" not in sys.argv
        n = next((int(a) for a in sys.argv[2:] if a.isdigit()), BATCH)
        print("ПРОБНЫЙ ПРОГОН" if dry else "ОТПРАВКА", tick(dry_run=dry, batch=n))
        return
    rows = collect()
    print(f"кому уйдёт: {len(rows)}")
    print("по возрастам:", dict(Counter(r["seg"] for r in rows)))
    print("по каналам  :", dict(Counter(t for r in rows for t in r["msgr"])))
    print("платили нам :", sum(1 for r in rows if r["paid"]))
    for seg in ("5-7", "7-12", "?"):
        r = next((x for x in rows if x["seg"] == seg), None)
        if r:
            print("\n" + "=" * 60 + f"\n{seg}, {'платил' if r['paid'] else 'не платил'}"
                  f", {r['name']}\n" + "=" * 60)
            print(text_for(r["name"], r["seg"], r["paid"]))



def confirms_to_queue(days=("2026-08-24", "2026-08-25"),
                      rebuild: bool = False) -> int:
    """Поставить в начало очереди подтверждения записи, не дошедшие из-за
    десятизначного chatId (25.08). Одно письмо на семью, все её записи
    списком — как и в confirm_joins после правки того же дня."""
    from . import autopilot
    from .moyklass_client import MoyklassClient
    mk = MoyklassClient(sync.get_api_key())
    try:
        rc = mk.get("/v1/company/classes", {"limit": 500})
        cls = {c["id"]: (c.get("name") or "")
               for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
        by_user: dict = {}
        new_cls: dict = {}
        for d in days:
            for j in (mk.fetch_all("/v1/company/joins", ["joins"],
                                   params={"createdAt": d}) or []):
                nm = cls.get(j.get("classId"), "")
                if not nm.startswith("2627") or "аявк" in nm.lower():
                    continue
                if str(j.get("createdAt") or "")[:10] != d:
                    continue
                if j.get("statusId") not in ACTIVE_JOIN:
                    continue
                by_user.setdefault(j["userId"], []).append(autopilot._join_title(nm))
                new_cls.setdefault(j["userId"], []).append(j.get("classId"))
        past: dict = {}
        for j in taskguard.pull_all(mk, "/v1/company/joins", "joins"):
            if j.get("userId") in by_user:
                past.setdefault(j["userId"], []).append(j)
        rows = []
        for uid, titles in by_user.items():
            try:
                u = mk.get(f"/v1/company/users/{uid}")
            except Exception:
                continue
            phone = "".join(c for c in str(u.get("phone") or "") if c.isdigit())[-10:]
            if len(phone) != 10:
                continue
            titles = list(dict.fromkeys(titles))
            what = (f"Подтверждаем запись: {titles[0]}." if len(titles) == 1
                    else "Подтверждаем записи:\n" + "\n".join(f"• {t}" for t in titles))
            # Продолжающему — без «условно-бесплатного» и диагностики:
            # он ходит второй год на то же самое (решение владельца 25.08).
            cont = all(autopilot._continuing(past.get(uid, []), cls, cid)
                       for cid in new_cls.get(uid, []))
            tail = ("Занятия начинаются 31 августа, всё как обычно — б-р "
                    "Маршала Рокоссовского, 6к1В. Рады, что продолжаете "
                    "с нами. Если что-то поменяется, просто ответьте здесь."
                    if cont else
                    "Занятия начинаются 31 августа. Адрес: б-р Маршала "
                    "Рокоссовского, 6к1В (напротив ТЦ «Янтарь»), 2 минуты "
                    "от метро Бульвар Рокоссовского. Первое занятие "
                    "условно-бесплатное, и на нём же бесплатная диагностика — "
                    "педагог посмотрит уровень и подберёт ступень. Если "
                    "что-то поменяется, просто ответьте здесь.")
            rows.append({
                "uid": uid, "phone": phone, "name": (u.get("name") or "").strip(),
                "seg": "?", "paid": True, "kind": "confirm",
                "msgr": wazzup.channels_for(phone, uid=uid),
                "text": f"Здравствуйте! {what}\n\n{tail}"})
    finally:
        mk.close()
    # Кому подтверждение уже ушло — второй раз не отправляем. 25.08 из-за
    # пропущенной проверки один клиент дважды оказывался первым в очереди.
    done = {str(x) for x in json.loads(db.get_setting(DONE_KEY, "[]") or "[]")}
    rows = [r for r in rows if f"confirm:{r['uid']}" not in done]
    queue = json.loads(db.get_setting(QUEUE_KEY, "[]") or "[]")
    if rebuild:
        # Текст письма лежит прямо в строке очереди, поэтому правка
        # формулировки не догоняет то, что уже поставлено. Пересборка
        # выбрасывает неотправленные подтверждения и кладёт их заново.
        queue = [r for r in queue if (r.get("kind") or "nabor") != "confirm"]
    else:
        have = {r["uid"] for r in queue if r.get("text")}
        rows = [r for r in rows if r["uid"] not in have]
    # в начало: подтверждение записи ждать не должно
    db.set_setting(QUEUE_KEY, json.dumps(rows + queue, ensure_ascii=False))
    log.info("подтверждений в очередь: %d", len(rows))
    return len(rows)


if __name__ == "__main__":
    main()
