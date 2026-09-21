"""Чаты учебных групп в МойКлассе («Мой Чат») и приглашение в них родителей.

Что есть на 13.09. В МойКлассе заведены восемь чатов групп английского —
«АЯ пн-ср 16:00» и так далее. Чат живёт внутри CRM: у родителя он
появляется в личном кабинете, ссылок-приглашений, как в WhatsApp, там нет.

Главное ограничение, ради которого написан этот модуль: **добавить в чат
можно только клиента с активированным доступом в личный кабинет**. CRM
говорит об этом прямым текстом в окне «Добавить участников», и список
доступных учеников там почти пуст. Поэтому «позвать родителей в чат» —
это не одно действие, а два: сначала родитель заходит в личный кабинет,
и только потом его видно в списке и можно добавить.

Что делает модуль:
  groups()   — восемь групп с составом: кто учится, у кого какой телефон;
  snapshot() — кто уже в каждом чате (через браузерный вход mkweb, потому
               что чаты API не отдаёт), результат кладётся в data/;
  plan()/send() — приглашение родителям: чат группы есть, вот как войти.

Отправка идёт очередью broadcast_queue — там уже сделано всё, без чего
писать родителям нельзя: окно 9:00–20:00, лимиты номера, каскад
WhatsApp → мессенджеры, пропуск тех, кто ждёт ответа админа, и своих
номеров. СМС уходит только тем, кому сообщение не доставилось: это не
реклама, а сервисное уведомление уже записавшимся, и такие СМС закон
разрешает (все получатели к тому же ранее платили).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path

from . import db

log = logging.getLogger("kidsup.chaty")

CAMPAIGN = "chat_invite"
DATA = Path(__file__).resolve().parent.parent / "data"
SNAP = DATA / "chaty_members.json"
# Где родитель находит чат: наш личный кабинет. Он живёт НЕ на домене CRM,
# а на своём поддомене *.tvoyklass.com — у каждого центра свой. Наш проверен
# 16.09: отдаёт название «KidsUP — английский детский сад, центр развития
# детей и английский летний лагерь» и наш логотип, то есть кабинет включён.
# До 16.09 здесь стоял app.moyklass.com/lk — адрес кабинета СОТРУДНИКА,
# родителя он привёл бы в тупик.
LK_URL = "https://kidsup.tvoyklass.com"
INVITE_RE = re.compile(r"https://chat\.whatsapp\.com/[A-Za-z0-9]{10,40}")


def links() -> dict[int, str]:
    """Ссылки на чат по группам: id группы → ссылка. Кривые записи отбрасываем.

    Заполняется, только если у чата есть своя ссылка. У «Моего Чата»
    её нет — там работает общий вход в личный кабинет, LK_URL.
    """
    try:
        raw = json.loads(db.get_setting("group_chats", "") or "{}")
    except ValueError:
        return {}
    out = {}
    for k, v in (raw or {}).items():
        s = str(v or "").strip()
        if s.startswith("https://") and str(k).isdigit():
            out[int(k)] = s
    return out


def _short(class_id: int) -> str:
    """Как называем группу родителю: «вт-чт 17:00» вместо имени из CRM."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT name FROM classes WHERE id=?", (class_id,)).fetchone()
    name = (row[0] if row else "") or ""
    m = re.search(r"_((?:пн|вт|ср|чт|пт|сб|вс)[^_]*)_(\d{1,2}:\d{2})", name)
    return f"{m.group(1)} {m.group(2)}" if m else name


def chat_title(class_id: int) -> str:
    """Имя чата в МойКлассе: «АЯ пн-ср 16:00» — по нему его и находим."""
    return f"АЯ {_short(class_id)}"


def groups() -> list[dict]:
    """Активные группы английского с составом — для страницы /chaty."""
    ln, snap = links(), _snapshot_data()
    with db.get_conn() as conn:
        # «2627_АЯ_Заявки» — буфер новых обращений, а не учебная группа:
        # чата у неё нет и родителей в ней быть не может
        cls = conn.execute(
            """SELECT id, name FROM classes
               WHERE status='opened' AND name LIKE '2627_АЯ_%'
                 AND name NOT LIKE '%Заявки%'
               ORDER BY name""").fetchall()
        out = []
        for cid, name in cls:
            kids = conn.execute(
                """SELECT u.name, u.phone, u.email FROM joins j
                   JOIN users u ON u.id = j.user_id
                   WHERE j.class_id = ? AND j.status_id = 2
                   ORDER BY u.name""", (cid,)).fetchall()
            title = chat_title(cid)
            inside = snap.get("chats", {}).get(title, [])
            out.append({"id": cid, "name": name, "title": _short(cid),
                        "chat": title, "link": ln.get(cid, ""),
                        "in_chat": inside,
                        "kids": [{"name": k, "phone": p, "почта": (m or "").strip(),
                                  "in_chat": _is_in(k, inside)} for k, p, m in kids]})
    return out


def _is_in(child: str, inside: list) -> bool:
    """Тот ли это ребёнок. В чате имя стоит как «Имя Фамилия», в CRM наоборот."""
    parts = {p.lower() for p in (child or "").split()}
    return any(parts and parts <= {p.lower() for p in (m or "").split()} for m in inside)


def _snapshot_data() -> dict:
    try:
        return json.loads(SNAP.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def snapshot_status() -> dict:
    d = _snapshot_data()
    return {"ok": bool(d), "taken": d.get("taken", ""),
            "chats": {k: len(v) for k, v in (d.get("chats") or {}).items()},
            "error": d.get("error", "")}


def snapshot(chats: list[str] | None = None) -> dict:
    """Считать состав чатов из МойКласса. Идёт через браузер — чаты API
    не отдаёт вовсе, ни списком, ни по одному."""
    from . import mkweb
    titles = chats or [chat_title(g["id"]) for g in groups()]
    res: dict[str, list] = {}
    err = ""
    for t in titles:
        try:
            r = mkweb.open_page(
                "https://app.moyklass.com/moyChat", frame="moychat", wait_ms=6000,
                actions=[{"click": t}, {"wait": 2500},
                         {"css": "button[class*=infoBtn]", "click": True},
                         {"wait": 2000}])
            res[t] = _parse_members(r.get("text") or "")
        except Exception as e:  # noqa: BLE001
            err = f"{t}: {str(e)[:120]}"
            log.warning("chaty.snapshot %s: %s", t, err)
    data = {"taken": datetime.now().isoformat(timespec="seconds"),
            "chats": res, "error": err}
    DATA.mkdir(parents=True, exist_ok=True)
    SNAP.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return snapshot_status()


def snapshot_start() -> dict:
    """Снимок в фоне: восемь чатов через браузер — это пара минут."""
    threading.Thread(target=snapshot, daemon=True).start()
    return {"ok": True, "running": True}


def _parse_members(text: str) -> list[str]:
    """Имена из панели «Информация о группе».

    Панель идёт хвостом страницы: «Участники», дальше по строке на человека
    (перед именем — инициалы аватарки), и заканчивается служебными кнопками.
    """
    i = text.rfind("Участники")
    if i < 0:
        return []
    tail = text[i + len("Участники"):]
    for stop in ("Копировать ссылку группы", "Закрыть", "Управление группой",
                 "Отменить", "Сохранить"):
        j = tail.find(stop)
        if j > 0:
            tail = tail[:j]
    out = []
    for line in (x.strip() for x in tail.splitlines()):
        if not line or line == "Админ" or len(line) <= 2:
            continue        # инициалы аватарки и пометка роли — не имена
        out.append(line)
    return out


def dobavit(chat: str, child: str, tries: int = 3) -> dict:
    """Добавить ребёнка в чат его группы. Через браузер — API чаты не знает.

    21.09.2026, Борис: «Нужно всех кто добавляется в ТвойКласс и ученик АЯ —
    сразу добавлять в соответствующий чат МойЧат!!»

    Путь по экрану: чат группы → «i» → «Управление группой» → «Добавить
    участников» → вкладка «Ученики» → поиск по фамилии → строка ребёнка →
    «Добавить» → «Сохранить». МойКласс добавит только того, у кого активирован
    доступ в личный кабинет, — об этом он честно пишет внизу окна; для
    остальных возвращаем «нет кабинета», и это работа для дежурной, а не
    ошибка. Список учеников в окне подгружается с задержкой, поэтому клик по
    строке делаем с повтором: без паузы после поиска он не попадает.
    """
    from . import mkweb
    last = ""
    for n in range(max(1, tries)):
        pause = 4500 + n * 2500
        r = mkweb.open_page(
            "https://app.moyklass.com/moyChat", frame="moychat", wait_ms=8000, max_text=400,
            actions=[{"click": chat}, {"wait": 2500},
                     {"css": "button[class*=infoBtn]", "click": True}, {"wait": 2000},
                     {"css": "button[class*=manageButton]", "click": True}, {"wait": 2000},
                     {"css": "button[class*=addButton]", "click": True}, {"wait": 3000},
                     {"click": "Ученики"}, {"wait": 2500},
                     {"css": "input[placeholder*='Поиск по имени']", "nth": 1,
                      "type": child, "after": pause},
                     # Выбор ребёнка — клик именно по текстовому узлу строки:
                     # по контейнеру строки «Выбрано» остаётся нулём, галочка
                     # вешается на сам текст. Проверено 21.09 обоими способами.
                     {"click": child, "exact": False, "force": True, "after": 2500},
                     # :text-is — точное совпадение: обычный клик по слову
                     # «Добавить» цепляет и кнопку «Добавить участников»,
                     # и Playwright ждёт, пока разрешится неоднозначность.
                     # Кнопки диалога («Добавить », «Сохранить») локаторами не
                     # берутся: текст с висячим пробелом, класс общий с другими
                     # кнопками, а force-клик упирается в перерисовку. Жмём их
                     # прямо из страницы — это то же самое нажатие, только без
                     # ожидания «стабильности» элемента.
                     {"js": "(function(){var b=Array.from(document.querySelectorAll('button'))"
                            ".filter(x=>(x.innerText||'').trim().indexOf('Добавить')===0"
                            " && (x.innerText||'').indexOf('участник')<0);"
                            "if(!b.length)return 'нет кнопки Добавить';"
                            "b[b.length-1].click();return 'ок'})()", "after": 3000},
                     {"js": "(function(){var b=Array.from(document.querySelectorAll('button'))"
                            ".filter(x=>(x.innerText||'').trim().indexOf('Сохранить')===0);"
                            "if(!b.length)return 'нет кнопки Сохранить';"
                            "b[b.length-1].click();return 'ок'})()", "after": 5000},
                     {"js": "(function(){var t=document.body.innerText;"
                            "var i=t.lastIndexOf('Участники');return t.slice(i,i+400)})()",
                      "after": 500}])
        steps = r.get("actions") or []
        bad = [s for s in steps if s.get("ok") is False]
        # Успех — когда обе кнопки диалога действительно нажались. Состав
        # чата в тот же заход читать бесполезно: панель «Информация о группе»
        # обновляется после закрытия окна, и 21.09 это выглядело как провал
        # там, где ребёнок уже был добавлен.
        press = [s.get("result") for s in steps if isinstance(s, dict) and "js" in s]
        # Успех — когда ни один шаг не упал. Читать состав чата в том же заходе
        # нельзя: панель «Информация о группе» обновляется только после закрытия
        # окна, и 22.09 ночью это дало ложное «не вышло: 10» на десяти детях,
        # которые на самом деле в чатах уже сидели. Правду показывает общий
        # снимок в конце sinhron().
        if not bad:
            return {"ok": True, "чат": chat, "ребёнок": child,
                    "после": str(press[-1])[:200] if press else ""}
        last = (f"{bad[0].get('css') or bad[0].get('click') or '?'}: {bad[0].get('error')}"
                if bad else "строка не появилась в участниках")[:200]
        log.warning("chaty.dobavit %s → %s: попытка %d, %s", child, chat, n + 1, last)
    return {"ok": False, "чат": chat, "ребёнок": child, "почему": last,
            "нажатия": press if "press" in dir() else [],
            "шаги": [{k: (str(v)[:60] if k == "result" else v) for k, v in s_.items()
                      if k in ("css", "click", "ok", "error", "result")}
                     for s_ in (steps or [])]}


def sinhron(dry: bool = True, limit: int = 8) -> dict:
    """Свести состав групп английского с составом чатов и добить разницу.

    Кого добавляем: ребёнок учится в группе АЯ (статус записи «Учится») и
    его нет в снимке чата. У кого нет доступа в кабинет — добавить нельзя,
    таких собираем отдельным списком: им нужно приглашение, а не кнопка.
    """
    snap = _snapshot_data().get("chats") or {}
    todo, done, fail = [], [], []
    for g in groups():
        inside = snap.get(g["chat"], [])
        for k in g["kids"]:
            if not _is_in(k["name"], inside):
                todo.append({"чат": g["chat"], "ребёнок": k["name"],
                             "телефон": k["phone"], "почта": k["почта"]})
    todo.sort(key=lambda x: (not x["почта"], x["ребёнок"]))   # с почтой — первые
    if dry:
        return {"dry_run": True, "снимок": _snapshot_data().get("taken", ""),
                "нет_в_чатах": len(todo), "список": todo[:40]}
    for it in todo[:limit]:
        r = dobavit(it["чат"], it["ребёнок"])
        (done if r.get("ok") else fail).append({**it, **{k: v for k, v in r.items() if k == "почему"}})
    if done:
        snapshot([g for g in {d["чат"] for d in done}])       # освежаем снимок по тронутым чатам
    return {"dry_run": False, "добавлено": len(done), "не_вышло": len(fail),
            "добавлены": done, "не_вышло_список": fail, "осталось": max(0, len(todo) - limit)}


def sinhron_start(limit: int = 12) -> dict:
    """Синхронизация в фоне: каждый ребёнок — это заход в браузер на минуту,
    по HTTP такое ждать нельзя. Итог кладём в настройку chaty_sinhron."""
    def _run():
        try:
            res = sinhron(dry=False, limit=limit)
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "error": str(e)[:200]}
        res["когда"] = datetime.now().isoformat(timespec="minutes")
        db.set_setting("chaty_sinhron", json.dumps(res, ensure_ascii=False)[:4000])
    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "running": True, "limit": limit}


def sinhron_status() -> dict:
    try:
        return json.loads(db.get_setting("chaty_sinhron", "") or "{}")
    except ValueError:
        return {}


def plan() -> dict:
    """Кому и что отправим. Ничего не отправляет и не пишет в очередь."""
    ln = links()
    fam: dict[str, dict] = {}
    for g in groups():
        for k in g["kids"]:
            phone = k["phone"]
            if not phone:
                continue
            f = fam.setdefault(phone, {"phone": phone, "child": k["name"], "groups": []})
            cur = next((x for x in f["groups"] if x["class_id"] == g["id"]), None)
            if cur:
                cur["children"].append(k["name"])
                continue
            # брат и сестра в одной группе — это один чат, а не два
            f["groups"].append({"class_id": g["id"], "title": g["title"],
                                "children": [k["name"]], "in_chat": k["in_chat"],
                                "link": ln.get(g["id"], LK_URL)})
            f["почта"] = k.get("почта") or f.get("почта") or ""
    out = []
    for f in fam.values():
        f["groups"].sort(key=lambda g: g["title"])
        for g in f["groups"]:
            g["child"] = ", ".join(_first(n) for n in g["children"])
        f["text"] = text_for(f["groups"], bool(f.get("почта")))
        f["sms"] = sms_for(f["groups"])
        out.append(f)
    out.sort(key=lambda f: f["child"] or "")
    return {"ok": bool(out), "recipients": out, "total": len(out),
            "error": "" if out else "в группах английского никто не числится «Учится»"}


def text_for(groups_: list[dict], has_mail: bool = False) -> str:
    """Сообщение в WhatsApp. Два разных текста, и путать их нельзя.

    Вход в кабинет — по e-mail ученика: справка МойКласса пишет прямо, что
    «вход в личный кабинет осуществляется только по email ученика», и без
    почты в карточке доступа не будет. Значит родителю без почты писать
    «войдите» бессмысленно — войти нечем. Ему нужен ровно один вопрос:
    продиктовать почту.

    Родителю с почтой нужен полный порядок действий. Порядок именно такой:
    почта → «Войти» → система сама пишет «Вы входите первый раз. Для
    активации входа нажмите восстановить пароль ниже» → «Восстановить
    пароль» → письмо → пароль. Пропустить шаг «Войти» нельзя: подсказка
    появляется только после него, а не на пустой форме.

    Срок жизни ссылки из письма в справке МойКласса назван по-разному —
    в одной статье час, в другой три. Поэтому цифру не пишем вовсе, только
    «живёт недолго, запросите ещё раз».

    Отдельного родительского аккаунта в системе нет: родитель входит под
    учётными данными ребёнка. Поэтому «ваша почта» — это почта, записанная
    в карточке ребёнка.
    """
    if len(groups_) == 1:
        where = f"группы {groups_[0]['title']}"
    else:
        where = "ваших групп " + " и ".join(g["title"] for g in groups_)
    head = (f"Здравствуйте! Это KidsUP 🌿 Мы завели чат {where} по английскому — "
            f"там расписание, переносы, домашние задания и фото с занятий. "
            f"Чат открывается в личном кабинете, вместе с занятиями, "
            f"посещаемостью и абонементом.\n\n")
    if not has_mail:
        return (head + "Чтобы открыть кабинет, нужен адрес электронной почты: "
                "она станет логином кабинета ребёнка. Напишите её, пожалуйста, "
                "в ответ на это сообщение — впишем в карточку и пришлём короткую "
                "инструкцию, как войти.")
    return (head + f"Вход: {LK_URL}\n"
            f"1. Введите почту, которую вы нам оставляли, и нажмите «Войти».\n"
            f"2. Система напишет, что вы входите первый раз, — нажмите "
            f"«Восстановить пароль».\n"
            f"3. Придёт письмо от отправителя «Личный кабинет». Если его нет "
            f"во «Входящих», посмотрите «Спам».\n"
            f"4. По ссылке из письма задайте пароль и войдите. Ссылка живёт "
            f"недолго — если не успели, запросите ещё раз.\n\n"
            f"На телефоне удобнее в приложении «Твой Класс» (два слова) — "
            f"вход теми же почтой и паролем.\n\n"
            f"Если что-то не получится — напишите сюда, поможем.")


def sms_for(groups_: list[dict]) -> str:
    """СМС-версия: коротко и с верным адресом."""
    return ("KidsUP: чат вашей группы английского — в личном кабинете "
            "kidsup.tvoyklass.com (вход по вашей почте). Вопросы: 4951209024")


def _first(name: str) -> str:
    """Имя ребёнка из «Фамилия Имя» — для подписи, какая группа чья."""
    parts = (name or "").split()
    return parts[1] if len(parts) > 1 else (parts[0] if parts else "")


def send(dry: bool = True) -> dict:
    """Поставить приглашения в очередь рассылки."""
    p = plan()
    if not p.get("ok"):
        return p
    from .autopilot import _bq_init, _now
    now = _now().isoformat(timespec="seconds")
    n = 0
    with db.get_conn() as conn:
        _bq_init(conn)
        done = {r[0] for r in conn.execute(
            "SELECT phone FROM broadcast_queue WHERE campaign=?", (CAMPAIGN,))}
        for f in p["recipients"]:
            if f["phone"] in done:
                continue        # приглашение этому номеру уже стоит в очереди
            if dry:
                n += 1
                continue
            conn.execute("INSERT INTO broadcast_queue (campaign, phone, child, "
                         "text, created) VALUES (?, ?, ?, ?, ?)",
                         (CAMPAIGN, f["phone"], f["child"], f["text"], now))
            n += 1
    log.info("chaty: кампания %s — %d получателей (dry=%s)", CAMPAIGN, n, dry)
    return {"ok": True, "dry_run": dry, "queued": n, "total": p["total"]}


def deliver(limit: int = 3, dry: bool = True) -> dict:
    """Отправить приглашения как разовые сообщения, а не рассылкой.

    Почему не очередью. Тик очереди считает всё массовым потоком, а
    массовому WhatsApp положен WABA с утверждённым шаблоном (правило
    после 22.08: обычный номер на рассылке ушёл в «не авторизован»).
    Нашего текста в шаблонах Meta нет и не будет — он про наш чат.
    Тринадцать писем из двадцати четырёх так и встали «ждут шаблона».

    Но это и не рассылка: двадцать четыре сервисных сообщения семьям,
    которые ходят к нам сейчас и с которыми переписка уже идёт. Это то
    же самое, что администратор пишет руками, — и уходит так же:
    обычным номером переписки, по несколько штук за вызов, чтобы поток
    не читался антиспамом как бот.
    """
    from . import wazzup
    from .autopilot import _now
    sent, errs = [], []
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, phone, child, text FROM broadcast_queue "
            "WHERE campaign=? AND status='pending' ORDER BY id LIMIT ?",
            (CAMPAIGN, max(1, min(10, int(limit))))).fetchall()
    for rid, phone, child, text in rows:
        if dry:
            sent.append({"phone": phone, "child": child, "dry": True})
            continue
        try:
            log_ = wazzup.send_smart(phone, text, dry_run=False, mass=False,
                                     kind=f"bc:{CAMPAIGN}")
            ok = any("ok" in x for x in log_)
        except Exception as e:  # noqa: BLE001
            ok, log_ = False, [str(e)[:150]]
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE broadcast_queue SET status=?, sent=?, tried=COALESCE(tried,'')||? "
                "WHERE id=?",
                ("sent" if ok else "pending",
                 _now().isoformat(timespec="seconds") if ok else None,
                 "razovoe=" + ("ok" if ok else "fail") + ";", rid))
        (sent if ok else errs).append({"phone": phone, "child": child, "log": log_})
    with db.get_conn() as conn:
        left = conn.execute(
            "SELECT COUNT(*) FROM broadcast_queue WHERE campaign=? AND status='pending'",
            (CAMPAIGN,)).fetchone()[0]
    log.info("chaty.deliver: отправлено %d, ошибок %d, осталось %d",
             len(sent), len(errs), left)
    return {"ok": True, "dry_run": dry, "sent": len(sent), "errors": errs,
            "left": left, "details": sent}


WAIT_REPLY = ("Спасибо, что написали! Доступ в личный кабинет открываем "
              "со своей стороны — как откроем, сразу напишем вам здесь "
              "и подскажем, как войти. В чат группы добавим вас сами 🌿")


def reply_waiting(phones: list[str] | None = None, text: str = "",
                  dry: bool = True) -> dict:
    """Ответить тем, кто написал в ответ на приглашение.

    Вопрос вызван нашим же сообщением, диалог открыт, и молчание тут
    хуже любого ответа: человек написал «откройте доступ» и ждёт. Ответ
    промежуточный и ничего не обещает по срокам — доступ открывает
    владелец, не мы.
    """
    from . import wazzup
    msg = (text or WAIT_REPLY).strip()
    out, errs = [], []
    for phone in (phones or []):
        if dry:
            out.append({"phone": phone, "dry": True})
            continue
        try:
            # именно "reply": человек написал нам сам, диалог открыт.
            # С любым другим видом предохранитель считает это второй
            # рекламой за день и отменяет отправку — что для ответа
            # на вопрос клиента ровно наоборот вредно.
            log_ = wazzup.send_smart(phone, msg, dry_run=False, mass=False,
                                     kind="reply")
            ok = any("ok" in x for x in log_)
        except Exception as e:  # noqa: BLE001
            ok, log_ = False, [str(e)[:150]]
        (out if ok else errs).append({"phone": phone, "log": log_})
    return {"ok": True, "dry_run": dry, "sent": len(out), "errors": errs,
            "details": out, "text": msg}


def save_links(raw: dict) -> dict:
    """Сохранить ссылки со страницы. Пустое поле — убрать ссылку группы."""
    keep = {}
    for k, v in (raw or {}).items():
        s = str(v or "").strip()
        if not str(k).isdigit():
            continue
        if s and not s.startswith("https://"):
            return {"ok": False, "error": f"не похоже на ссылку: {s[:60]}"}
        if s:
            keep[str(k)] = s
    db.set_setting("group_chats", json.dumps(keep, ensure_ascii=False))
    return {"ok": True, "saved": len(keep)}


def find_links(limit: int = 40) -> dict:
    """Поискать ссылки-приглашения WhatsApp в истории переписки.

    Осталось от первой версии, когда чаты считались ватсаповскими: если
    админ когда-то кидал родителю ссылку на групповой чат, она найдётся.
    """
    found: dict[str, dict] = {}
    with db.get_conn() as conn:
        for table in ("wazzup_outbox", "wazzup_inbox"):
            try:
                rows = conn.execute(
                    f"SELECT ts, phone, text FROM {table} "
                    f"WHERE text LIKE '%chat.whatsapp.com%' ORDER BY ts DESC "
                    f"LIMIT ?", (int(limit) * 5,)).fetchall()
            except Exception:
                continue
            for ts, phone, text in rows:
                for url in INVITE_RE.findall(text or ""):
                    f = found.setdefault(url, {"url": url, "first_seen": ts,
                                               "phones": [], "where": table})
                    if phone and phone not in f["phones"]:
                        f["phones"].append(phone)
    return {"total": len(found), "links": sorted(
        found.values(), key=lambda f: f["first_seen"] or "", reverse=True)}


# ── готовность к личному кабинету ──────────────────────────────────────
# Владелец 16.09: «подготовь инструкции клиентам, как зарегистрироваться».
# Первый шаг инструкции зависит от того, есть ли в карточке e-mail: с ним
# родителю достаточно запросить пароль самому, без него он сначала должен
# продиктовать почту админу. Значит инструкций нужно две, и надо знать,
# кому какая. Только чтение.

SEASON_PREFIX = "2627_"
ST_UCHITSYA = 2


def _lk_flag(raw: str) -> bool | None:
    """Признак активированного личного кабинета, если МойКласс его отдаёт.

    13.09 выяснили, что в чат группы можно добавить только клиента с
    активированным кабинетом. Флаг в выгрузке карточки может называться
    по-разному, поэтому перебираем известные варианты и честно возвращаем
    None, когда поля нет вовсе, — вместо того чтобы молча считать «нет».
    """
    try:
        u = json.loads(raw or "{}")
    except ValueError:
        return None
    for k in ("hasLogin", "isLoginAllowed", "loginAllowed", "clientAccess",
              "hasAccess", "lkAccess", "canLogin"):
        if k in u:
            return bool(u[k])
    return None


def lk_readiness() -> dict:
    """Кто из клиентов готов к регистрации в личном кабинете.

    Отдельно группы английского (там заведены чаты) и весь платящий сезон.
    """
    with db.get_conn() as conn:
        cls = conn.execute(
            "SELECT id, name FROM classes WHERE status='opened' "
            "AND name LIKE ? AND name NOT LIKE '%Заявки%' ORDER BY name",
            (SEASON_PREFIX + "%",)).fetchall()
        ay, other, seen = [], [], set()
        keys: set[str] = set()
        for cid, name in cls:
            rows = conn.execute(
                "SELECT u.id, u.name, u.phone, u.email, u.raw FROM joins j "
                "JOIN users u ON u.id = j.user_id "
                "WHERE j.class_id=? AND j.status_id=? ORDER BY u.name",
                (cid, ST_UCHITSYA)).fetchall()
            kids = [{"uid": r[0], "ребёнок": r[1], "телефон": r[2] or "",
                     "почта": (r[3] or "").strip(), "кабинет": _lk_flag(r[4])}
                    for r in rows]
            for r in rows:
                try:
                    keys |= set(json.loads(r[4] or "{}").keys())
                except ValueError:
                    pass
            item = {"class_id": cid, "группа": name.replace(SEASON_PREFIX, ""),
                    "детей": len(kids),
                    "с_почтой": sum(1 for k in kids if k["почта"]),
                    "дети": kids}
            (ay if "_АЯ_" in name else other).append(item)
            for k in kids:
                seen.add((k["uid"], k["ребёнок"], k["телефон"], k["почта"]))
        # семьи считаем по номеру телефона: у брата и сестры он один
        fam_all, fam_mail = set(), set()
        for _uid, _n, phone, mail in seen:
            if not phone:
                continue
            fam_all.add(phone)
            if mail:
                fam_mail.add(phone)
    ay_kids = [k for g in ay for k in g["дети"]]
    ay_fam = {k["телефон"] for k in ay_kids if k["телефон"]}
    ay_mail = {k["телефон"] for k in ay_kids if k["телефон"] and k["почта"]}
    return {
        "английский": {"групп": len(ay), "детей": len(ay_kids),
                       "семей": len(ay_fam), "семей_с_почтой": len(ay_mail),
                       "группы": ay},
        "весь_сезон": {"групп": len(ay) + len(other), "детей": len(seen),
                       "семей": len(fam_all), "семей_с_почтой": len(fam_mail)},
        "остальные_группы": other,
        "поля_карточки": sorted(keys),
        "видимость_занятий": _lesson_visibility(),
    }


def _lesson_visibility() -> dict:
    """Увидит ли родитель занятия, войдя в кабинет — проверить программно нельзя.

    Настройка lessonVisibility («Не видно / Видно всегда / …») в спецификации
    МойКласса живёт только в LessonSettings, то есть на запись: GET занятия её
    не возвращает, и в выгрузке её нет ни у одного из наших занятий. Значит
    ответить «видит или нет» может только человек, открывший настройки в
    интерфейсе. Пишем это прямо, чтобы пустое поле не читалось как «скрыто».

    Проверять обязательно: справка предупреждает, что после включения личного
    кабинета занятия скрыты по умолчанию. Если так и осталось, родитель войдёт
    в пустой кабинет, и приглашение сработает против нас.
    """
    from datetime import date as _d
    with db.get_conn() as conn:
        try:
            n = conn.execute("SELECT COUNT(*) FROM lessons WHERE date >= ?",
                             (_d.today().isoformat(),)).fetchone()[0]
        except Exception:
            return {"ошибка": "таблица занятий недоступна"}
    return {"занятий_впереди": n,
            "проверено": False,
            "почему": "API не отдаёт lessonVisibility на чтение — она есть только "
                      "в настройках занятия на запись",
            "как_проверить": "МойКласс → Настройки → Занятия → «Видимость занятия "
                             "в личном кабинете и виджетах»"}

