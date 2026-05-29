"""Авторизация веб-слоя: API-ключ для /api, basic-auth для /admin, секрет для вебхуков."""
from __future__ import annotations

import base64

from aiohttp import web

from app.config import get_settings


def _unauthorized_json(msg: str) -> web.Response:
    return web.json_response({"error": msg}, status=401)


@web.middleware
async def api_key_middleware(request: web.Request, handler):
    """Защита /api/local-crm: вебхуки — по WEBHOOK_SECRET, остальное — по LOCAL_CRM_API_KEY."""
    path = request.path
    if not path.startswith("/api/local-crm"):
        return await handler(request)

    s = get_settings()
    if "/webhooks/" in path:
        secret = request.headers.get("X-Webhook-Secret") or request.query.get("secret")
        if secret != s.webhook_secret:
            return _unauthorized_json("invalid webhook secret")
    else:
        key = request.headers.get("X-API-Key") or request.query.get("api_key")
        if key != s.local_crm_api_key:
            return _unauthorized_json("invalid api key")
    return await handler(request)


@web.middleware
async def admin_auth_middleware(request: web.Request, handler):
    """Basic-auth для /admin по ADMIN_LOGIN / ADMIN_PASSWORD из .env."""
    if not request.path.startswith("/admin"):
        return await handler(request)

    s = get_settings()
    auth = request.headers.get("Authorization", "")
    ok = False
    if auth.startswith("Basic "):
        try:
            login, _, pwd = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
            ok = (login == s.admin_login and pwd == s.admin_password)
        except Exception:  # noqa: BLE001
            ok = False
    if not ok:
        return web.Response(
            status=401,
            headers={"WWW-Authenticate": 'Basic realm="Svoya CRM Admin"'},
            text="Требуется авторизация администратора.",
        )
    return await handler(request)
