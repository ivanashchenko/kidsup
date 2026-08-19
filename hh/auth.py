"""OAuth-авторизация пользователя hh.ru (authorization_code + PKCE).

Схема из документации hh:
  1) открываем https://hh.ru/oauth/authorize?response_type=code&client_id=...
  2) hh редиректит на redirect_uri с ?code=...
  3) меняем code на пару access/refresh токенов: POST https://api.hh.ru/token
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import time
import urllib.parse
import webbrowser

from . import config
from .client import _request, HHClient


def _pkce_pair():
    verifier = base64.urlsafe_b64encode(os.urandom(48)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    return verifier, challenge


def _save(tokens):
    tokens = dict(tokens)
    tokens["expires_at"] = int(time.time()) + int(tokens.get("expires_in", 0))
    config.TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.TOKEN_PATH.write_text(json.dumps(tokens, ensure_ascii=False, indent=2))
    config.TOKEN_PATH.chmod(0o600)
    return tokens


def load():
    if not config.TOKEN_PATH.exists():
        return None
    return json.loads(config.TOKEN_PATH.read_text())


def _exchange(form):
    config.require("CLIENT_ID", "CLIENT_SECRET")
    return _request("POST", config.TOKEN_URL, form=form)


def refresh(tokens):
    """refresh_token одноразовый — после обмена сохраняем новую пару."""
    return _save(
        _exchange({"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
    )


def login(open_browser=True):
    """Полный интерактивный вход: поднимает локальный сервер под redirect_uri."""
    config.require("CLIENT_ID", "CLIENT_SECRET", "APP_EMAIL")
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": config.CLIENT_ID,
            "state": state,
            "redirect_uri": config.REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            # интеграция работает от лица работодателя, а не соискателя
            "role": "employer",
            "force_role": "true",
        }
    )
    url = f"{config.OAUTH_AUTHORIZE_URL}?{query}"
    print("Откройте ссылку и подтвердите доступ приложению:\n")
    print(url, "\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    code = _wait_for_code(state)
    tokens = _exchange(
        {
            "grant_type": "authorization_code",
            "client_id": config.CLIENT_ID,
            "client_secret": config.CLIENT_SECRET,
            "redirect_uri": config.REDIRECT_URI,
            "code": code,
            "code_verifier": verifier,
        }
    )
    saved = _save(tokens)
    print(f"Токен сохранён: {config.TOKEN_PATH}")
    return saved


def _wait_for_code(expected_state):
    parsed = urllib.parse.urlparse(config.REDIRECT_URI)
    host, port = parsed.hostname or "localhost", parsed.port or 80
    received = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            received.update({k: v[0] for k, v in params.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in received
            self.wfile.write(
                ("<h3>Готово, можно закрыть вкладку.</h3>" if ok else
                 "<h3>Авторизация не выдана.</h3>").encode()
            )

        def log_message(self, *args):
            pass

    print(f"Жду редирект на {config.REDIRECT_URI} ...")
    with http.server.HTTPServer((host, port), Handler) as httpd:
        httpd.handle_request()

    if received.get("error"):
        raise SystemExit(f"hh вернул ошибку авторизации: {received['error']}")
    if received.get("state") != expected_state:
        raise SystemExit("Не совпал state — возможна подмена запроса, повторите вход.")
    if "code" not in received:
        raise SystemExit("Не получен authorization_code.")
    return received["code"]


def client():
    """Готовый клиент с валидным токеном (обновляет его при необходимости)."""
    tokens = load()
    if not tokens:
        raise SystemExit("Сначала выполните: python3 -m hh login")
    if tokens.get("expires_at", 0) - 60 < time.time() and tokens.get("refresh_token"):
        tokens = refresh(tokens)
    return HHClient(tokens["access_token"])
