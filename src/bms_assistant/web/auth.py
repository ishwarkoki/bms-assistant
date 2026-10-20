"""Signed-cookie authentication for the web dashboard.

Local development runs without a password. Public deployments set
``BMS_AUTH_PASSWORD`` (and optionally ``BMS_API_TOKEN`` for scripts).
"""

from __future__ import annotations

import secrets
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from bms_assistant.config import Settings

SESSION_COOKIE = "bms_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 14
PUBLIC_PATHS = frozenset({"/login", "/logout", "/health"})
PUBLIC_PREFIXES = ("/static/", "/favicon")
_FALLBACK_SESSION_SECRET = secrets.token_urlsafe(32)


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.session_secret or _FALLBACK_SESSION_SECRET,
        salt="bms-session",
    )


def issue_session(settings: Settings) -> str:
    return _serializer(settings).dumps({"authenticated": True})


def valid_session(settings: Settings, token: str | None) -> bool:
    if not token:
        return False
    try:
        payload = _serializer(settings).loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return isinstance(payload, dict) and payload.get("authenticated") is True


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def is_authorized(request: Request, settings: Settings) -> bool:
    if not settings.auth_enabled:
        return True
    token = bearer_token(request)
    if token and settings.api_token and secrets.compare_digest(token, settings.api_token):
        return True
    return valid_session(settings, request.cookies.get(SESSION_COOKIE))


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def safe_next_url(value: str | None) -> str:
    """Only allow same-site relative redirects after login."""
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def unauthorized_response(request: Request) -> JSONResponse | RedirectResponse:
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"error": "authentication required"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    target = quote(request.url.path, safe="/")
    return RedirectResponse(url=f"/login?next={target}", status_code=303)
