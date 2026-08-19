"""Тонкий HTTP-клиент к api.hh.ru (только стандартная библиотека)."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config


class HHError(RuntimeError):
    def __init__(self, status, payload, url):
        self.status = status
        self.payload = payload
        self.url = url
        super().__init__(f"HTTP {status} {url}: {json.dumps(payload, ensure_ascii=False)[:500]}")


def _request(method, url, *, token=None, params=None, form=None, body=None, retries=3):
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{url}?{urllib.parse.urlencode(clean, doseq=True)}"

    headers = {
        # Обязателен для любого запроса к api.hh.ru
        "HH-User-Agent": config.USER_AGENT,
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"

    last_error = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8") or "{}"
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                payload = {"raw": raw[:500]}
            # 429/5xx — можно повторить, остальное отдаём наверх сразу
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                last_error = HHError(exc.code, payload, url)
                time.sleep(2 ** attempt)
                continue
            raise HHError(exc.code, payload, url) from None
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    raise last_error


class HHClient:
    """Клиент, подставляющий access-токен во все запросы."""

    def __init__(self, token):
        self.token = token

    def get(self, path, **params):
        return _request("GET", config.API_BASE + path, token=self.token, params=params)

    def get_url(self, url, **params):
        """Многие ответы hh содержат готовые ссылки (actions[].url, messages_url)."""
        return _request("GET", url, token=self.token, params=params)

    def post(self, path, *, body=None, form=None):
        return _request("POST", config.API_BASE + path, token=self.token, body=body, form=form)

    def put(self, path, *, body=None):
        return _request("PUT", config.API_BASE + path, token=self.token, body=body)

    # --- обёртки над конкретными методами API ---

    def me(self):
        """GET /me — текущий пользователь, его роль и employer.id менеджера."""
        return self.get("/me")

    def active_vacancies(self, employer_id, per_page=50, page=0):
        return self.get(
            f"/employers/{employer_id}/vacancies/active", per_page=per_page, page=page
        )

    def negotiation_collections(self, vacancy_id):
        """GET /negotiations — коллекции откликов по вакансии (response, consider, ...)."""
        return self.get("/negotiations", vacancy_id=vacancy_id)

    def responses(self, vacancy_id, *, page=0, per_page=50, only_new=None, order_by=None):
        """GET /negotiations/response — отклики соискателей на вакансию."""
        return self.get(
            "/negotiations/response",
            vacancy_id=vacancy_id,
            page=page,
            per_page=per_page,
            show_only_new_responses=only_new,
            order_by=order_by,
        )

    def negotiation(self, negotiation_id):
        return self.get(f"/negotiations/{negotiation_id}")

    def resume(self, resume_id):
        """GET /resumes/{id} — полное резюме (просмотр контактов может быть платным)."""
        return self.get(f"/resumes/{resume_id}")

    def chat_messages(self, chat_id, per_page=50, page=0):
        return self.get(f"/common/chats/{chat_id}/messages", per_page=per_page, page=page)

    def send_chat_message(self, chat_id, text, idempotency_key, is_automated=False):
        """POST /common/chats/{chat_id}/messages — актуальный способ писать кандидату."""
        return self.post(
            f"/common/chats/{chat_id}/messages",
            body={
                "text": text,
                "idempotency_key": idempotency_key,
                "is_automated": is_automated,
            },
        )

    def send_negotiation_message(self, negotiation_id, text):
        """Устаревший метод переписки. Используется как запасной, если нет chat_id."""
        return self.post(f"/negotiations/{negotiation_id}/messages", form={"message": text})

    def legacy_messages(self, negotiation_id, per_page=50, page=0):
        return self.get(
            f"/negotiations/{negotiation_id}/messages", per_page=per_page, page=page
        )
