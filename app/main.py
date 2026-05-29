"""Точка сборки: aiogram Dispatcher, сервисы, polling/webhook."""
from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.agent.agent_core import AgentCore
from app.bot.handlers import router as bot_router
from app.bot.middlewares import Services, ServicesMiddleware
from app.config import get_settings
from app.crm.db import close_crm_db, init_crm_db
from app.crm.memory import MemoryStore
from app.crm.seed import seed
from app.crm.service import get_crm
from app.logger import logger, setup_logger
from app.services.ollama_client import OllamaClient
from app.storage.database import close_db, init_db
from app.tools.crm_tools import ToolContext, ToolRegistry


async def build_services() -> Services:
    ollama = OllamaClient()
    crm = get_crm()
    memory = MemoryStore()

    def tools_factory(chat_id: int) -> ToolRegistry:
        return ToolRegistry(ToolContext(chat_id=chat_id, crm=crm, memory=memory))

    agent = AgentCore(ollama=ollama, memory=memory, tools_factory=tools_factory)

    return Services(ollama=ollama, crm=crm, memory=memory, agent=agent)


async def run() -> None:
    setup_logger()
    settings = get_settings()
    settings.ensure_critical()

    await init_db()           # bot.db — пользователи и пошаговое оформление (FSM)
    await init_crm_db()       # svoya_crm.db — локальная CRM + память агента
    # Автосев при пустой базе, чтобы бот работал из коробки (полноценный seed: python -m app.crm.seed)
    crm = get_crm()
    if not await crm._all_products():
        counts = await seed()
        logger.info(f"Svoya CRM: база была пустой — выполнен автосев: {counts}")
    # каталог-кнопки читают товары из CRM — сбрасываем возможный пустой кэш
    from app.services.catalog_service import load_catalog
    load_catalog.cache_clear()

    services = await build_services()

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(ServicesMiddleware(services))
    dp.include_router(bot_router)

    logger.info(f"Старт: {settings.app_name}, режим={settings.bot_mode}")
    logger.info(f"Bitrix24: {'on' if settings.bitrix_enabled else 'off'} | "
                f"Ollama: {'on' if settings.ollama_enabled else 'off'}")

    try:
        if settings.bot_mode == "webhook":
            await _run_webhook(bot, dp, settings)
        else:
            await _run_polling(bot, dp)
    finally:
        await services.ollama.close()
        await bot.session.close()
        await close_db()
        await close_crm_db()
        logger.info("Бот остановлен.")


async def _run_polling(bot: Bot, dp: Dispatcher) -> None:
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Polling запущен.")
    await dp.start_polling(bot)


async def _run_webhook(bot: Bot, dp: Dispatcher, settings) -> None:
    """Поднимаем aiohttp-сервер и регистрируем webhook в Telegram."""
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    app = web.Application()
    handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.webhook_secret_token or None,
    )
    handler.register(app, path=settings.webhook_path)
    setup_application(app, dp, bot=bot)

    await bot.set_webhook(
        url=settings.webhook_url,
        secret_token=settings.webhook_secret_token or None,
        drop_pending_updates=True,
    )
    logger.info(f"Webhook: {settings.webhook_url}")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.web_server_host, settings.web_server_port)
    await site.start()
    logger.info(f"HTTP-сервер: http://{settings.web_server_host}:{settings.web_server_port}")

    # держимся бесконечно, выход — по SIGINT/SIGTERM
    import asyncio

    stop = asyncio.Event()
    try:
        await stop.wait()
    finally:
        await runner.cleanup()
