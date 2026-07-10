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
        db.save_simple("courses", client.fetch_optional("/v1/company/courses", ["courses"]))
        db.save_classes(client.fetch_optional("/v1/company/classes", ["classes"]))
        db.save_simple("subscriptions",
                       client.fetch_optional("/v1/company/subscriptions", ["subscriptions"]))

        _stage("Ученики")
        users = client.fetch_all("/v1/company/users", ["users"],
                                 progress=_progress_cb("Ученики"))
        db.save_users(users)

        _stage("Заявки")
        db.save_joins(client.fetch_optional("/v1/company/joins", ["joins"]))

        _stage("Платежи")
        payments = client.fetch_all(
            "/v1/company/payments", ["payments"],
            params={"date[]": [date_from, date_to]},
            progress=_progress_cb("Платежи"),
        )
        db.save_payments(payments)

        _stage("Счета")
        db.save_invoices(client.fetch_optional("/v1/company/invoices", ["invoices"]))

        _stage("Абонементы учеников")
        db.save_user_subscriptions(
            client.fetch_optional("/v1/company/userSubscriptions",
                                  ["subscriptions", "userSubscriptions"])
        )

        _stage("Занятия и посещаемость")
        lessons = client.fetch_all(
            "/v1/company/lessons", ["lessons"],
            params={"date[]": [date_from, date_to], "includeRecords": "true"},
            progress=_progress_cb("Занятия"),
        )
        db.save_lessons(lessons)
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
        info = client.get("/v1/company/company")
        name = ""
        if isinstance(info, dict):
            name = info.get("name") or ""
        return True, f"Подключение успешно. Компания: {name}" if name else "Подключение успешно."
    except MoyklassError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    finally:
        if client is not None:
            client.close()
