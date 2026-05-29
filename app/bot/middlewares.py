"""Middleware для инъекции сервисов в handler через data['services']."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.agent.agent_core import AgentCore
from app.crm.memory import MemoryStore
from app.crm.service import SvoyaCRM
from app.services.ollama_client import OllamaClient


@dataclass
class Services:
    ollama: OllamaClient
    crm: SvoyaCRM
    memory: MemoryStore
    agent: AgentCore


class ServicesMiddleware(BaseMiddleware):
    def __init__(self, services: Services) -> None:
        self.services = services

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["services"] = self.services
        return await handler(event, data)
