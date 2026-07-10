"""Конфигурация приложения.

Настройки берутся из переменных окружения, а API-ключ МойКласс может
храниться либо в переменной окружения MOYKLASS_API_KEY, либо в базе
данных (задаётся на странице «Настройки»).
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("KIDSUP_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "kidsup.db"

MOYKLASS_API_URL = os.environ.get("MOYKLASS_API_URL", "https://api.moyklass.com")

# Ключ API из окружения имеет приоритет над сохранённым в базе.
ENV_API_KEY = os.environ.get("MOYKLASS_API_KEY", "")

# Необязательный пароль на приложение (HTTP Basic). Если не задан —
# доступ свободный (подходит для запуска на своём компьютере).
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
APP_USER = os.environ.get("APP_USER", "admin")

# Сколько месяцев истории выгружать по умолчанию (платежи, занятия).
DEFAULT_HISTORY_MONTHS = int(os.environ.get("HISTORY_MONTHS", "24"))

# Ограничение частоты запросов к API МойКласс (запросов в секунду).
API_RATE_LIMIT_RPS = float(os.environ.get("MOYKLASS_RPS", "4"))
