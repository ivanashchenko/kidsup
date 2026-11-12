"""Синхронизация: выгрузка данных из МойКласс в локальную базу.

Запускается в фоновом потоке; прогресс пишется в таблицу sync_state,
фронтенд опрашивает /api/sync/status.
"""

import json
import logging
import threading
import traceback
from datetime import date, datetime, timedelta

from . import config, db
from .moyklass_client import MoyklassClient, MoyklassError

KURS_PEREIMENOVAT = {"Английский детский сад": "Мини-сад"}   # страховка, если курс в CRM вернут к старому имени

log = logging.getLogger("kidsup.sync")

_sync_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


def get_api_key() -> str:
    return config.ENV_API_KEY or db.get_setting("moyklass_api_key")


def sync_running() -> bool:
    return _sync_thread is not None and _sync_thread.is_alive()


def get_status() -> dict:
    return {
        "running": sync_running(),
        "stage": db.get_state("sync_stage"),
        "detail": db.get_state("sync_detail"),
        "error": db.get_state("sync_error"),
        "last_sync": db.get_state("last_sync"),
        "counts": db.table_counts(),
    }


def start_sync(history_months: int | None = None) -> bool:
    """Запускает фоновую синхронизацию. False — если уже идёт."""
    global _sync_thread
    with _thread_lock:
        if sync_running():
            return False
        db.set_state("sync_error", "")
        db.set_state("sync_stage", "Запуск…")
        db.set_state("sync_detail", "")
        _sync_thread = threading.Thread(
            target=_run_sync, args=(history_months,), daemon=True
        )
        _sync_thread.start()
        return True


def _stage(name: str):
    log.info("Синхронизация: %s", name)
    db.set_state("sync_stage", name)
    db.set_state("sync_detail", "")


def _progress_cb(entity: str):
    def cb(done, total):
        suffix = f" из {total}" if total else ""
        db.set_state("sync_detail", f"{entity}: {done}{suffix}")
    return cb


def _prune_payments(client: MoyklassClient, date_from: str, date_to: str,
                    items: list[dict], limit: int = 50) -> int:
    """Убрать из базы платежи окна, удалённые в МойКлассе (см. db.payments_missing).

    По id МойКласс отдаёт и удалённый платёж (проверено 26.10: 200 и все поля,
    признака удаления нет), поэтому подтверждаем иначе — вторым, независимым
    списком: платежи этого же ученика. Кандидата нет и там — удаляем. Список
    ученика проверяем на полноту: живые платежи ученика из выгрузки окна
    обязаны в нём быть, иначе ученика пропускаем. Кандидатов подозрительно
    много — выгрузка неполная, ничего не трогаем. limit — не больше стольких
    учеников за проход (~3 запроса в секунду); остаток доберёт следующий синк."""
    kandidaty = db.payments_missing(date_from, date_to, items)
    if not kandidaty or not items:
        return 0
    if len(kandidaty) > max(10, len(items) // 5):
        log.warning("чистка удалённых платежей пропущена: кандидатов %s при выгрузке %s",
                    len(kandidaty), len(items))
        return 0
    zhivye = {}
    for p in items:
        if p.get("id") is not None and p.get("userId") is not None:
            zhivye.setdefault(int(p["userId"]), set()).add(int(p["id"]))
    po_ucheniku = {}
    for k in kandidaty:
        if k["user_id"] is not None:
            po_ucheniku.setdefault(int(k["user_id"]), []).append(int(k["id"]))
    udalit = []
    for uid, pids in list(po_ucheniku.items())[:limit]:
        try:
            spisok = client.fetch_all("/v1/company/payments", ["payments"], params={"userId": uid})
        except Exception:
            continue
        est = {int(p["id"]) for p in spisok if p.get("id") is not None}
        if not est or not zhivye.get(uid, set()) <= est:
            continue
        udalit += [pid for pid in pids if pid not in est]
    db.delete_payments(udalit)
    if udalit:
        log.info("платежи, удалённые в МойКлассе, убраны из базы: %s", udalit)
    return len(udalit)


def _polnyi_spisok(client: MoyklassClient, path: str, keys: list[str],
                   params: dict | None = None, progress=None) -> tuple[list[dict], bool, str]:
    """Все страницы списка и признак «список полный» — для пометки удалённого.

    (строки, polnyi, почему_не_полный). Полный — только если каждая страница пришла
    без ошибки, на каждой странице API назвал один и тот же totalItems и он равен
    числу разных id в выгрузке. Строку, удалённую или добавленную в МойКлассе, пока мы
    листали, видно по сменившемуся счётчику — такой список полным не считаем.
    Сетевые ошибки и 5xx клиент повторяет сам; не получилось — исключение наружу,
    как у fetch_all. 403/404 — пустой неполный список (как fetch_optional)."""
    import httpx
    from .moyklass_client import PAGE_LIMIT
    items: list[dict] = []
    totals: set = set()
    offset, prev_ids = 0, None
    polnyi, pochemu = True, ""
    while True:
        page_params = dict(params or {})
        page_params.update({"offset": offset, "limit": PAGE_LIMIT})
        try:
            data = client.get(path, page_params)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                return [], False, f"HTTP {e.response.status_code}"
            raise
        page, total = client._extract_items(data, keys)
        ids = {str(x.get("id")) for x in page if isinstance(x, dict)}
        if page and ids == prev_ids:
            polnyi, pochemu = False, "страница повторилась"
            break
        prev_ids = ids
        items.extend(page)
        if progress:
            progress(len(items), total)
        if total is None:
            polnyi, pochemu = False, "API не назвал число строк"
        else:
            totals.add(int(total))
        if not page:
            break
        offset += len(page)
        if total is not None and offset >= int(total):
            break
        if total is None and len(page) < PAGE_LIMIT and len(page) != 100:
            break
    if polnyi:
        n_id = len({x.get("id") for x in items if isinstance(x, dict) and x.get("id") is not None})
        if len(totals) != 1:
            polnyi, pochemu = False, f"счётчик менялся, пока листали: {sorted(totals)}"
        elif n_id != next(iter(totals)) or n_id != len(items):
            polnyi, pochemu = False, f"строк {n_id} (повторов {len(items) - n_id}), а API насчитал {next(iter(totals))}"
    return items, polnyi, pochemu


def _lokalnye_ids(sql: str, args: tuple = ()) -> set[int]:
    with db.get_conn() as conn:
        return {int(r[0]) for r in conn.execute(sql, args)}


def _pometit(tbl: str, items: list[dict], polnyi: bool, pochemu: str, lokalnye: set[int],
             istochnik: str) -> dict:
    """Пометка удалённого по одной выгрузке — только при полном списке."""
    if not polnyi:
        log.info("%s: удалённое не помечаем — список неполный (%s)", tbl, pochemu)
        return {"tbl": tbl, "ok": False, "prichina": f"список неполный: {pochemu}", "pomecheno": 0}
    return db.pometit_udalennye(tbl, lokalnye, [x.get("id") for x in items], istochnik)


def _zhurnal_pometok(istochnik: str, pometki: list[dict]) -> None:
    """Итог пометки удалённого — в журнал приложения и в sync_state (последние 40 прогонов):
    сколько строк в окне, сколько пропало, сколько помечено, почему пропущено."""
    from datetime import datetime as _dt
    for p in pometki:
        if p.get("pomecheno") or not p.get("ok"):
            log.info("%s: %s — окно %s, пропало %s, помечено %s%s", istochnik, p.get("tbl"),
                     p.get("okno"), p.get("propalo"), p.get("pomecheno"),
                     f", НЕ помечено: {p['prichina']}" if p.get("prichina") else "")
    try:
        zh = json.loads(db.get_state("mk_udaleno_zhurnal") or "[]")
    except ValueError:
        zh = []
    zh.append({"ts": _dt.now().isoformat(timespec="seconds"), "istochnik": istochnik, "pometki": pometki})
    db.set_state("mk_udaleno_zhurnal", json.dumps(zh[-40:], ensure_ascii=False))


def _run_sync(history_months: int | None):
    api_key = get_api_key()
    client = None
    try:
        client = MoyklassClient(api_key)
        client.authenticate()

        months = history_months or int(
            db.get_setting("history_months", str(config.DEFAULT_HISTORY_MONTHS))
        )
        date_from = (date.today() - timedelta(days=months * 31)).isoformat()
        date_to = (date.today() + timedelta(days=62)).isoformat()  # + будущие занятия

        _stage("Справочники (филиалы, сотрудники, курсы, группы)")
        db.save_simple("filials", client.fetch_optional("/v1/company/filials", ["filials"]))
        db.save_simple("managers", client.fetch_optional("/v1/company/managers", ["managers"]))
        kursy = client.fetch_optional("/v1/company/courses", ["courses"])
        # 28.09 Борис: «Английский детский сад» вводит родителей в заблуждение
        # (там английский 2 раза в неделю) — везде «Мини-сад», как «Нулевой класс».
        # Пока курс в МойКлассе не переименован, приводим имя здесь: по нему
        # ищутся цены (PRICES), тексты и описания.
        for k in kursy or []:
            if (k.get("name") or "").strip() in KURS_PEREIMENOVAT:
                k["name"] = KURS_PEREIMENOVAT[k["name"].strip()]
        db.save_simple("courses", kursy)
        db.save_classes(client.fetch_optional("/v1/company/classes", ["classes"]))
        db.save_simple("subscriptions",
                       client.fetch_optional("/v1/company/subscriptions", ["subscriptions"]))
        db.save_simple("client_statuses",
                       client.fetch_optional("/v1/company/clientStatuses", ["clientStatuses"]))
        db.save_simple("join_statuses",
                       client.fetch_optional("/v1/company/joinStatuses", ["joinStatuses"]))
        db.save_simple("payment_types",
                       client.fetch_optional("/v1/company/paymentTypes", ["paymentTypes"]))
        db.save_simple("rooms", client.fetch_optional("/v1/company/rooms", ["rooms"]))

        # 12.11.2026: ученики, счета, абонементы и занятия выгружаются целиком —
        # здесь же помечаем то, что удалили в МойКлассе (db.pometit_udalennye):
        # карточки «ДУБЛЬ», склеенные Борисом, и удалённые счета иначе висели бы
        # на странице «Проверка МойКласса» вечно. Сами строки остаются в базе.
        pometki = []
        _stage("Ученики")
        users, polnyi, pochemu = _polnyi_spisok(client, "/v1/company/users", ["users"],
                                                progress=_progress_cb("Ученики"))
        db.save_users(users)
        pometki.append(_pometit("users", users, polnyi, pochemu,
                                _lokalnye_ids("SELECT id FROM users"), "полный синк"))

        _stage("Заявки")
        db.save_joins(client.fetch_optional("/v1/company/joins", ["joins"]))

        _stage("Платежи")
        payments = client.fetch_all(
            "/v1/company/payments", ["payments"],
            params={"date[]": [date_from, date_to]},
            progress=_progress_cb("Платежи"),
        )
        db.save_payments(payments)
        _prune_payments(client, date_from, date_to, payments, limit=200)

        _stage("Счета")
        scheta, polnyi, pochemu = _polnyi_spisok(client, "/v1/company/invoices", ["invoices"])
        db.save_invoices(scheta)
        pometki.append(_pometit("invoices", scheta, polnyi, pochemu,
                                _lokalnye_ids("SELECT id FROM invoices"), "полный синк"))

        _stage("Абонементы учеников")
        abon, polnyi, pochemu = _polnyi_spisok(client, "/v1/company/userSubscriptions",
                                               ["subscriptions", "userSubscriptions"])
        db.save_user_subscriptions(abon)
        pometki.append(_pometit("user_subscriptions", abon, polnyi, pochemu,
                                _lokalnye_ids("SELECT id FROM user_subscriptions"), "полный синк"))

        _stage("Занятия и посещаемость")
        lessons, polnyi, pochemu = _polnyi_spisok(
            client, "/v1/company/lessons", ["lessons"],
            params={"date[]": [date_from, date_to], "includeRecords": "true"},
            progress=_progress_cb("Занятия"))
        db.save_lessons(lessons)
        # сравниваем строго внутри окна: включает ли API крайние даты — не проверено
        d_lo = (date.fromisoformat(date_from) + timedelta(days=1)).isoformat()
        d_hi = (date.fromisoformat(date_to) - timedelta(days=1)).isoformat()
        pometki.append(_pometit("lessons", lessons, polnyi, pochemu,
                                _lokalnye_ids("SELECT id FROM lessons WHERE date BETWEEN ? AND ?", (d_lo, d_hi)),
                                "полный синк"))
        _zhurnal_pometok("полный синк", pometki)
        # Если записи не пришли вместе с занятиями — добираем отдельно.
        if lessons and not any(l.get("records") for l in lessons):
            db.save_lesson_records(
                client.fetch_optional("/v1/company/lessonRecords",
                                      ["lessonRecords", "records"],
                                      params={"date[]": [date_from, date_to]})
            )

        db.set_state("last_sync", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        db.set_state("sync_range", json.dumps([date_from, date_to]))
        _stage("Готово")
    except MoyklassError as e:
        log.error("Ошибка синхронизации: %s", e)
        db.set_state("sync_error", str(e))
        db.set_state("sync_stage", "Ошибка")
    except Exception as e:  # noqa: BLE001 — фон, наружу не пробросить
        log.error("Ошибка синхронизации: %s\n%s", e, traceback.format_exc())
        db.set_state("sync_error", f"{type(e).__name__}: {e}")
        db.set_state("sync_stage", "Ошибка")
    finally:
        if client is not None:
            client.close()


def test_connection(api_key: str) -> tuple[bool, str]:
    """Проверка ключа: авторизация + чтение информации о компании."""
    client = None
    try:
        client = MoyklassClient(api_key)
        client.authenticate()
        filials = client.get("/v1/company/filials")
        n = len(filials) if isinstance(filials, list) else 0
        return True, f"Подключение успешно. Филиалов в CRM: {n}."
    except MoyklassError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    finally:
        if client is not None:
            client.close()


def light_sync() -> None:
    """Быстрое обновление только групп и записей (для «Набора 26/27»).

    Полная синхронизация тяжёлая (уроки, платежи, визиты), а записи в группы
    меняются каждый час — обновляем их отдельно раз в 5 минут."""
    if sync_running():
        return
    from .moyklass_client import MoyklassClient
    client = MoyklassClient(get_api_key())
    try:
        db.save_classes(client.fetch_optional("/v1/company/classes", ["classes"]))
        # 12.11.2026: список записей в группы приходит целиком каждые 5 минут —
        # удалённую в МойКлассе запись помечаем здесь же (страница «Проверка
        # МойКласса», разделы 8в и 11г), строку из базы не стираем.
        zayavki_gr, polnyi, pochemu = _polnyi_spisok(client, "/v1/company/joins", ["joins"])
        db.save_joins(zayavki_gr)
        p = _pometit("joins", zayavki_gr, polnyi, pochemu, _lokalnye_ids("SELECT id FROM joins"), "лёгкий синк")
        if p.get("pomecheno") or not p.get("ok") and polnyi:
            _zhurnal_pometok("лёгкий синк", [p])
        # инкремент карточек: только изменённые за 2 дня (несколько десятков) —
        # /promo и /waiting видят свежие статусы и теги, не дожидаясь полного синка
        import datetime as _dt
        since = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
        db.save_users(client.fetch_all(
            "/v1/company/users", ["users"],
            params={"updatedAt[]": [since, _dt.date.today().isoformat()]}))
        # оплаты и абонементы за последние три дня — чтобы /spiski показывал
        # «оплачено» через 5 минут после кассы, а не после утреннего полного синка
        since3 = (_dt.date.today() - _dt.timedelta(days=3)).isoformat()
        today = _dt.date.today().isoformat()
        try:
            platezhi = client.fetch_all("/v1/company/payments", ["payments"],
                                        params={"date": [since3, today]})
            db.save_payments(platezhi)
            # удалённый в МойКлассе платёж (исправили «нал» на «карту») уходит
            # из базы за минуты, а не висит призраком в контроле оплат
            _prune_payments(client, since3, today, platezhi)
            db.save_user_subscriptions(client.fetch_all("/v1/company/userSubscriptions", ["subscriptions"],
                                                        params={"sellDate": [since3, today]}))
        except Exception:
            log.exception("лёгкий синк: оплаты/абонементы не обновились")
        # Записи на занятия. До 16.09 они обновлялись только полным синком раз
        # в сутки, и /voronka показывала админу работу, которой нет: ребёнка
        # записали вечером на пробное, а страница утром по-прежнему числила его
        # «не пришёл» или «без даты». Семь строк из пятнадцати были такими.
        # Окно узкое — от вчера до двух недель вперёд: этого хватает и воронке,
        # и напоминаниям, а весит немного.
        try:
            lessons = client.fetch_all(
                "/v1/company/lessons", ["lessons"],
                params={"date[]": [(_dt.date.today() - _dt.timedelta(days=1)).isoformat(),
                                   (_dt.date.today() + _dt.timedelta(days=14)).isoformat()],
                        "includeRecords": "true"})
            db.save_lessons(lessons)
            if lessons and not any(l.get("records") for l in lessons):
                db.save_lesson_records(client.fetch_optional(
                    "/v1/company/lessonRecords", ["lessonRecords", "records"],
                    params={"date[]": [(_dt.date.today() - _dt.timedelta(days=1)).isoformat(),
                                       (_dt.date.today() + _dt.timedelta(days=14)).isoformat()]}))
        except Exception:
            log.exception("лёгкий синк: записи на занятия не обновились")
        db.set_state("last_light_sync", __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    finally:
        client.close()


# --- средний синк ---------------------------------------------------------
#
# 12.11.2026, страница «Проверка МойКласса» (правило 2 Бориса, шаг 0 выкатки).
# Лёгкий синк берёт занятия только «вчера … +14 дней» и абонементы, проданные за
# 3 дня, полный — раз в сутки в 07:00. Лиза исправила занятие 20.10 или удалила
# дубль сентябрьского абонемента — строка страницы ушла бы только завтра утром,
# а удалённое не уходило бы никогда. Средний синк раз в 30 минут (08–21) и по
# кнопке «Перепроверить сейчас»: занятия с записями с 1-го числа прошлого месяца по
# +14 дней и абонементы сезона — 5–6 запросов вместо живой проверки каждой строки
# (≈260 запросов на пересчёт). Только чтение МойКласса.
#
# Чистка — пометкой, а не удалением (db.pometit_udalennye), и только при полном
# списке: все страницы, счётчик API совпал. Зарплата, явка и прибыль читают те же
# таблицы — для них ничего не меняется, кроме того, что занятия окна обновляются
# чаще (то же самое полный синк делает каждое утро).

SEZON_OT = "2026-08-25"          # абонементы сезона 2026/27 — проданные с 25.08
SREDNIY_RPS = 1 / 0.3            # пауза 0,3 с между запросами (у МойКласса ~3 в секунду)

_sredniy_lock = threading.Lock()


def sredniy_okno(segodnya: date | None = None) -> tuple[str, str]:
    """Окно занятий среднего синка: с 1-го числа прошлого месяца по +14 дней."""
    d = segodnya or date.today()
    pervoe = (d.replace(day=1) - timedelta(days=1)).replace(day=1)
    return pervoe.isoformat(), (d + timedelta(days=14)).isoformat()


def sredniy_sync(client: MoyklassClient | None = None, segodnya: date | None = None) -> dict:
    """Средний синк: занятия окна и абонементы сезона + пометка удалённого в окне.

    Не идёт, пока идёт полный синк (он и так всё перезапишет) или уже идёт другой
    средний. Возвращает итог по таблицам; он же — в sync_state «sredniy_sync»."""
    from datetime import datetime as _dt
    if sync_running():
        return {"ok": False, "prichina": "идёт полный синк"}
    if not _sredniy_lock.acquire(blocking=False):
        return {"ok": False, "prichina": "средний синк уже идёт"}
    own = client is None
    itog: dict = {"nachalo": _dt.now().isoformat(timespec="seconds"), "ok": True, "tablicy": []}
    try:
        if own:
            client = MoyklassClient(get_api_key(), rps=SREDNIY_RPS)
        d = segodnya or date.today()
        lo, hi = sredniy_okno(d)
        # Выгружаем на день шире и сравниваем строго внутри окна: включает ли фильтр
        # МойКласса крайние даты, мы не проверяли, а ошибка на краю пометила бы
        # удалёнными все занятия первого или последнего дня.
        f_lo = (date.fromisoformat(lo) - timedelta(days=1)).isoformat()
        f_hi = (date.fromisoformat(hi) + timedelta(days=1)).isoformat()
        try:
            lessons, polnyi, pochemu = _polnyi_spisok(
                client, "/v1/company/lessons", ["lessons"],
                params={"date[]": [f_lo, f_hi], "includeRecords": "true"})
            db.save_lessons(lessons)
            if polnyi and lessons and not any(isinstance(l.get("records"), list) for l in lessons):
                polnyi, pochemu = False, "занятия пришли без записей"
            itog["tablicy"].append(_pometit(
                "lessons", lessons, polnyi, pochemu,
                _lokalnye_ids("SELECT id FROM lessons WHERE date BETWEEN ? AND ?", (lo, hi)), "средний синк")
                | {"zanyatiy": len(lessons)})
        except Exception as e:  # noqa: BLE001 — неудача одной выгрузки не трогает другую
            log.exception("средний синк: занятия не обновились")
            itog["ok"] = False
            itog["tablicy"].append({"tbl": "lessons", "ok": False, "prichina": f"ошибка: {str(e)[:120]}"})
        try:
            s_hi = (d + timedelta(days=1)).isoformat()
            s_lo = (date.fromisoformat(SEZON_OT) - timedelta(days=1)).isoformat()
            abon, polnyi, pochemu = _polnyi_spisok(
                client, "/v1/company/userSubscriptions", ["subscriptions", "userSubscriptions"],
                params={"sellDate": [s_lo, s_hi]})
            db.save_user_subscriptions(abon)
            with db.get_conn() as conn:
                lok = {int(r[0]) for r in conn.execute(
                    "SELECT id FROM user_subscriptions WHERE substr(json_extract(raw,'$.sellDate'),1,10) "
                    "BETWEEN ? AND ?", (SEZON_OT, d.isoformat()))}
            itog["tablicy"].append(_pometit("user_subscriptions", abon, polnyi, pochemu, lok, "средний синк")
                                   | {"abonementov": len(abon)})
        except Exception as e:  # noqa: BLE001
            log.exception("средний синк: абонементы не обновились")
            itog["ok"] = False
            itog["tablicy"].append({"tbl": "user_subscriptions", "ok": False, "prichina": f"ошибка: {str(e)[:120]}"})
        itog["konec"] = _dt.now().isoformat(timespec="seconds")
        itog["okno"] = [lo, hi]
        db.set_state("sredniy_sync", json.dumps(itog, ensure_ascii=False))
        _zhurnal_pometok("средний синк", itog["tablicy"])
        return itog
    finally:
        if own and client is not None:
            client.close()
        _sredniy_lock.release()
