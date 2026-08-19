"""Конфигурация интеграции с hh.ru.

Все секреты берутся из переменных окружения (см. hh/.env.example),
ничего не хранится в репозитории.
"""

import os
from pathlib import Path

API_BASE = "https://api.hh.ru"
OAUTH_AUTHORIZE_URL = "https://hh.ru/oauth/authorize"
TOKEN_URL = f"{API_BASE}/token"

# hh.ru требует заголовок HH-User-Agent: "Название приложения (почта разработчика)".
# Запросы без него или с "мусорным" значением отклоняются с bad_user_agent.
APP_NAME = os.environ.get("HH_APP_NAME", "KidsUP-HR")
APP_EMAIL = os.environ.get("HH_APP_EMAIL", "")
USER_AGENT = f"{APP_NAME}/1.0 ({APP_EMAIL})" if APP_EMAIL else f"{APP_NAME}/1.0"

CLIENT_ID = os.environ.get("HH_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("HH_CLIENT_SECRET", "")
# Должен совпадать с Redirect URI, указанным в настройках приложения на dev.hh.ru
REDIRECT_URI = os.environ.get("HH_REDIRECT_URI", "http://localhost:8765/callback")

# Куда складываем токены. По умолчанию — вне репозитория, в домашней директории.
TOKEN_PATH = Path(
    os.environ.get("HH_TOKEN_PATH", Path.home() / ".kidsup" / "hh_token.json")
)


def require(*names: str) -> None:
    """Падает с понятным сообщением, если не заданы обязательные переменные."""
    missing = [n for n in names if not globals().get(n)]
    if missing:
        env_names = {
            "CLIENT_ID": "HH_CLIENT_ID",
            "CLIENT_SECRET": "HH_CLIENT_SECRET",
            "APP_EMAIL": "HH_APP_EMAIL",
        }
        raise SystemExit(
            "Не заданы переменные окружения: "
            + ", ".join(env_names.get(n, n) for n in missing)
            + "\nСкопируйте hh/.env.example в .env, заполните и выполните: set -a; . ./.env; set +a"
        )
