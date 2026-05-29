"""aiohttp-приложение Svoya CRM: REST API + админка. Запуск: python -m app.web"""
from __future__ import annotations

from aiohttp import web

from app.config import get_settings
from app.crm.db import close_crm_db, init_crm_db
from app.crm.seed import seed
from app.crm.service import get_crm
from app.logger import logger, setup_logger
from app.web.admin import admin_routes
from app.web.api import routes as api_routes
from app.web.auth import admin_auth_middleware, api_key_middleware


async def _on_startup(app: web.Application) -> None:
    await init_crm_db()
    if not await get_crm()._all_products():
        counts = await seed()
        logger.info(f"Svoya CRM web: пустая база — автосев: {counts}")


async def _on_cleanup(app: web.Application) -> None:
    await close_crm_db()


def create_app() -> web.Application:
    app = web.Application(middlewares=[api_key_middleware, admin_auth_middleware])
    app.add_routes(api_routes)
    app.add_routes(admin_routes)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


def main() -> None:
    setup_logger()
    s = get_settings()
    logger.info(
        f"Svoya CRM web на http://{s.admin_host}:{s.admin_port}  |  "
        f"админка: /admin (login: {s.admin_login}), API: /api/local-crm"
    )
    web.run_app(create_app(), host=s.admin_host, port=s.admin_port, print=None)


if __name__ == "__main__":
    main()
