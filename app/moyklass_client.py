"""Клиент API МойКласс (https://api.moyklass.com).

Авторизация: POST /v1/company/auth/getToken с телом {"apiKey": "..."}.
Ключ создаётся в CRM: Настройки → API (https://app.moyklass.com/settings/settings/api).

Все списки выгружаются постранично (offset/limit). API возвращает либо
объект вида {"users": [...], "stats": {"totalItems": N}}, либо просто
массив — клиент поддерживает оба варианта.
"""

import time
import threading
import logging

import httpx

from . import config

log = logging.getLogger("kidsup.moyklass")

PAGE_LIMIT = 500


class MoyklassError(Exception):
    pass


class MoyklassAuthError(MoyklassError):
    pass


class MoyklassClient:
    def __init__(self, api_key: str, base_url: str = config.MOYKLASS_API_URL,
                 rps: float = config.API_RATE_LIMIT_RPS):
        if not api_key:
            raise MoyklassAuthError("API-ключ не задан")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._min_interval = 1.0 / rps if rps > 0 else 0
        self._last_request = 0.0
        self._throttle_lock = threading.Lock()
        self._token = None
        self._client = httpx.Client(timeout=60)

    # --- низкоуровневые запросы ------------------------------------------

    def _throttle(self):
        with self._throttle_lock:
            wait = self._min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def authenticate(self) -> None:
        resp = self._client.post(
            f"{self.base_url}/v1/company/auth/getToken",
            json={"apiKey": self.api_key},
        )
        if resp.status_code in (400, 401, 403):
            raise MoyklassAuthError(
                f"Не удалось авторизоваться в МойКласс (HTTP {resp.status_code}). "
                "Проверьте API-ключ."
            )
        resp.raise_for_status()
        data = resp.json()
        self._token = data.get("accessToken")
        if not self._token:
            raise MoyklassAuthError(f"API не вернул accessToken: {data}")

    def close(self):
        try:
            if self._token:
                self._client.post(
                    f"{self.base_url}/v1/company/auth/revokeToken",
                    headers={"x-access-token": self._token},
                )
        except httpx.HTTPError:
            pass
        self._client.close()

    def _request(self, method: str, path: str, params: dict | None = None, retries: int = 4):
        if not self._token:
            self.authenticate()
        last_error = None
        for attempt in range(retries + 1):
            self._throttle()
            try:
                resp = self._client.request(
                    method, f"{self.base_url}{path}",
                    params=params,
                    headers={"x-access-token": self._token},
                )
            except httpx.HTTPError as e:
                last_error = e
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 401:
                # токен истёк — переавторизуемся
                self.authenticate()
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = MoyklassError(f"HTTP {resp.status_code} для {path}")
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        raise MoyklassError(f"Запрос {path} не удался после повторов: {last_error}")

    def get(self, path: str, params: dict | None = None):
        return self._request("GET", path, params)

    # --- выгрузка списков --------------------------------------------------

    @staticmethod
    def _extract_items(data, keys: list[str]) -> tuple[list, int | None]:
        """Достаёт список элементов и totalItems из ответа любой формы."""
        if isinstance(data, list):
            return data, None
        if isinstance(data, dict):
            total = None
            stats = data.get("stats")
            if isinstance(stats, dict):
                total = stats.get("totalItems")
            for key in keys:
                if isinstance(data.get(key), list):
                    return data[key], total
            # ответ-словарь без известного ключа — ищем первый список
            for value in data.values():
                if isinstance(value, list):
                    return value, total
        return [], None

    def fetch_all(self, path: str, item_keys: list[str],
                  params: dict | None = None,
                  progress=None) -> list[dict]:
        """Выгружает все страницы списка."""
        items: list[dict] = []
        offset = 0
        while True:
            page_params = dict(params or {})
            page_params.update({"offset": offset, "limit": PAGE_LIMIT})
            data = self.get(path, page_params)
            page, total = self._extract_items(data, item_keys)
            items.extend(page)
            if progress:
                progress(len(items), total)
            if not page:
                break
            offset += len(page)
            if total is not None and offset >= total:
                break
            if total is None and len(page) < PAGE_LIMIT:
                break
        return items

    def fetch_optional(self, path: str, item_keys: list[str],
                       params: dict | None = None) -> list[dict]:
        """То же, но 404/403 не считается ошибкой (эндпоинт может отсутствовать)."""
        try:
            return self.fetch_all(path, item_keys, params)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                log.info("Эндпоинт %s недоступен (%s) — пропускаем",
                         path, e.response.status_code)
                return []
            raise
