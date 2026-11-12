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

# Старый общий вход (HTTP Basic, логин admin). 12.11.2026: пустой пароль больше НЕ
# открывает сайт — свободный доступ только в режиме без входа (BEZ_VHODA ниже).
# Код читает config.APP_PASSWORD в момент проверки, а не копирует при импорте.
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
APP_USER = os.environ.get("APP_USER", "admin")

# 12.11.2026: личные входы (app/vhod.py). Соль и хеши паролей — в отдельном файле,
# в ночную копию базы (/api/backup/db берёт только DB_PATH) он не попадает.
VHODY_DB = DATA_DIR / "vhody.db"
# Режим без входа — только явно: KIDSUP_BEZ_VHODA=1 (тесты, свой компьютер) и пустой
# APP_PASSWORD. Забытая переменная на новом сервере сайт больше не открывает.
BEZ_VHODA = os.environ.get("KIDSUP_BEZ_VHODA") == "1"
# Свой адрес для проверки источника изменяющих запросов (заголовок Origin).
# Постоянная строка, а не заголовок Host из запроса.
SVOY_ADRES = "https://app.kidsup.ru"

# Сколько месяцев истории выгружать по умолчанию (платежи, занятия).
DEFAULT_HISTORY_MONTHS = int(os.environ.get("HISTORY_MONTHS", "24"))

# Ограничение частоты запросов к API МойКласс (запросов в секунду).
API_RATE_LIMIT_RPS = float(os.environ.get("MOYKLASS_RPS", "4"))
