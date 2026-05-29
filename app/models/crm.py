"""Структуры запросов/ответов Bitrix24."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class LeadPayload(BaseModel):
    title: str
    name: Optional[str] = None
    phone: Optional[str] = None
    source_id: str = "OTHER"
    source_description: str = "Telegram-бот «Своя Среда»"
    stage_id: Optional[str] = None
    product_title: Optional[str] = None
    city: Optional[str] = None
    bot_comment: Optional[str] = None


class DealPayload(BaseModel):
    title: str
    opportunity: Optional[int] = None
    currency_id: str = "RUB"
    source_id: str = "OTHER"
    source_description: str = "Telegram-бот «Своя Среда»"
    stage_id: Optional[str] = None
    color: Optional[str] = None
    delivery_type: Optional[str] = None
    need_assembly: Optional[bool] = None
    delivery_date: Optional[str] = None
    comments: Optional[str] = None


class CRMResult(BaseModel):
    ok: bool
    entity_id: Optional[str] = None
    raw: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
