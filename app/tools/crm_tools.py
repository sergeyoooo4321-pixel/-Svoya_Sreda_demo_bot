"""Инструменты ИИ-агента поверх локальной Svoya CRM (раздел т.md §13).

Интерфейс намеренно совпадает со старым ToolRegistry (Bitrix24), чтобы Agent Core
почти не переписывать: registry.call(name, args) -> dict. Модель НЕ ходит в БД
напрямую — только через эти инструменты, backend выполняет операции.
"""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Optional

from app.crm.memory import MemoryStore
from app.crm.service import SvoyaCRM
from app.logger import logger
from app.services.normalization import normalize_phone


# Стадии: enum инструмента → код стадии локальной CRM.
_STAGE_ALIASES = {
    "new": "new", "новый": "new",
    "consultation": "consultation", "консультация": "consultation",
    "waiting": "waiting", "waiting_decision": "waiting", "ждёт": "waiting", "ждет": "waiting",
    "order_created": "order_created", "заказ оформлен": "order_created",
    "rejected": "rejected", "refused": "rejected", "отказ": "rejected",
}


def _resolve_stage(value: Optional[str]) -> Optional[str]:
    if not value or not value.strip():
        return None
    return _STAGE_ALIASES.get(value.strip().lower(), "consultation")


@dataclass
class ToolContext:
    chat_id: int
    crm: SvoyaCRM
    memory: MemoryStore


class ToolRegistry:
    """Маршрутизатор tool calls агента → Svoya CRM."""

    def __init__(self, context: ToolContext) -> None:
        self.ctx = context
        self._handlers = {
            "search_knowledge": self.search_knowledge,
            "get_product": self.get_product,
            "list_products": self.list_products,
            "create_lead": self.create_lead,
            "update_lead": self.update_lead,
            "create_deal": self.create_deal,
            "call_manager": self.call_manager,
        }

    @property
    def known_tools(self) -> list[str]:
        return list(self._handlers.keys())

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
            return {"error": f"unknown tool: {name}", "known": list(self._handlers.keys())}
        try:
            args = args if isinstance(args, dict) else {}
            result = await handler(**args) if inspect.iscoroutinefunction(handler) else handler(**args)
            result = result if isinstance(result, dict) else {"result": result}
            logger.info(f"tool {name}: ok | args={list(args.keys())} | result={_result_summary(result)}")
            await self.ctx.crm.create_activity("chat", self.ctx.chat_id, "tool_call",
                                               f"tool: {name}", _result_summary(result),
                                               {"args": list(args.keys())}, created_by="bot")
            return result
        except TypeError as exc:
            logger.warning(f"tool {name}: bad args: {exc}")
            return {"error": f"bad arguments: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"tool {name}: {exc!r}")
            return {"error": str(exc)[:300]}

    # ---------------- факты ----------------

    async def search_knowledge(self, query: str = "", intent: str = "unknown", limit: int = 3, **_: Any) -> dict[str, Any]:
        return await self.ctx.crm.search_knowledge(query=query, intent=intent, limit=limit)

    async def get_product(self, query: str = "", product_name: str = "", article: str = "", **_: Any) -> dict[str, Any]:
        return await self.ctx.crm.get_product(query=query, product_name=product_name, article=article)

    async def list_products(self, category: str = "", filters: Optional[dict[str, Any]] = None,
                            limit: int = 5, **_: Any) -> dict[str, Any]:
        return await self.ctx.crm.list_products(category=category, filters=filters, limit=limit)

    # ---------------- CRM ----------------

    async def create_lead(self, client_name: str = "", phone: str = "", product: str = "",
                          city: str = "", comment: str = "", stage: str = "new", **_: Any) -> dict[str, Any]:
        lead_id = await self.ctx.crm.upsert_lead_for_chat(
            self.ctx.chat_id, source="telegram",
            stage_code=_resolve_stage(stage) or "new",
            client_name=client_name or None,
            phone=normalize_phone(phone) if phone else None,
            interested_product=product or None,
            city=city or None,
            bot_comment=comment or None,
        )
        await self.ctx.memory.set_lead_id(self.ctx.chat_id, lead_id)
        return {"lead_id": lead_id, "status": "created"}

    async def update_lead(self, client_name: str = "", phone: str = "", product: str = "",
                          city: str = "", comment: str = "", stage: str = "", **_: Any) -> dict[str, Any]:
        lead_id = await self.ctx.crm.upsert_lead_for_chat(
            self.ctx.chat_id, source="telegram",
            stage_code=_resolve_stage(stage),  # None → стадию не трогаем
            client_name=client_name or None,
            phone=normalize_phone(phone) if phone else None,
            interested_product=product or None,
            city=city or None,
            bot_comment=comment or None,
        )
        await self.ctx.memory.set_lead_id(self.ctx.chat_id, lead_id)
        return {"lead_id": lead_id, "status": "updated"}

    async def create_deal(self, client_name: str = "", phone: str = "", product: str = "",
                          color: str = "", city: str = "", delivery_type: str = "",
                          assembly_needed: str = "Не указано", expected_delivery_date: str = "",
                          comment: str = "", **_: Any) -> dict[str, Any]:
        state = await self.ctx.memory.load_state(self.ctx.chat_id)
        ed = state.extracted

        # идемпотентность (§25)
        if state.deal_id:
            return {"deal_id": state.deal_id, "status": "already_created"}

        normalized_phone = normalize_phone(phone) if phone else ed.phone
        if not normalized_phone:
            return {"deal_id": None, "status": "phone_required",
                    "note": "Нельзя создавать сделку без телефона клиента."}

        client_name = client_name or ed.client_name or ""
        product = product or ed.product or ""
        color = color or ed.color or ""
        city = city or ed.city or ""
        delivery_type = delivery_type or ed.delivery_type or ""
        if (not assembly_needed or assembly_needed == "Не указано") and ed.assembly_needed:
            assembly_needed = ed.assembly_needed

        # цена из каталога CRM
        amount = None
        article = ed.product_article or ed.product_id
        prod = await self.ctx.crm.get_product(query=product, article=article or "")
        if prod.get("product"):
            amount = prod["product"].get("price_value")
            article = prod["product"].get("article") or article

        # лид: берём из памяти или создаём в стадии order_created
        lead_id = state.lead_id or await self.ctx.crm.upsert_lead_for_chat(
            self.ctx.chat_id, stage_code="order_created",
            client_name=client_name or None, phone=normalized_phone,
            interested_product=product or None, product_article=article,
            city=city or None, delivery_type=delivery_type or None,
            assembly_needed=assembly_needed or None,
        )

        res = await self.ctx.crm.create_deal(
            lead_id=lead_id, source="telegram",
            client_name=client_name or None, phone=normalized_phone,
            product_name=product or None, product_article=article,
            color=color or None, city=city or None,
            delivery_type=delivery_type or None,
            assembly_needed=assembly_needed if assembly_needed != "Не указано" else None,
            expected_delivery_date=expected_delivery_date or None,
            amount=amount,
            bot_comment=comment or f"Готовая заявка из Telegram. {product}, {color}, {city}.",
        )
        if res.get("deal_id"):
            await self.ctx.memory.set_deal_id(self.ctx.chat_id, int(res["deal_id"]))
            await self.ctx.memory.set_stage(self.ctx.chat_id, "order_created")
        # сохраняем собранные данные в память
        await self.ctx.memory.update_extracted(self.ctx.chat_id, {
            "client_name": client_name or None, "phone": normalized_phone,
            "product": product or None, "product_article": article,
            "color": color or None, "city": city or None, "delivery_type": delivery_type or None,
        })
        return res

    async def ensure_lead(self, create_stage: str = "consultation") -> Optional[int]:
        """Гарантирует лид для чата (§22). Создаёт при отсутствии, НЕ меняет стадию существующего."""
        state = await self.ctx.memory.load_state(self.ctx.chat_id)
        if state.lead_id:
            return state.lead_id
        existing = await self.ctx.crm.get_active_lead_by_chat(self.ctx.chat_id)
        if existing:
            await self.ctx.memory.set_lead_id(self.ctx.chat_id, int(existing["id"]))
            return int(existing["id"])
        ed = state.extracted
        res = await self.ctx.crm.create_lead(
            telegram_chat_id=self.ctx.chat_id, source="telegram", stage_code=create_stage,
            client_name=ed.client_name or None, phone=ed.phone or None,
            interested_product=ed.product or None, product_article=ed.product_article or None,
            city=ed.city or None, delivery_type=ed.delivery_type or None,
        )
        lead_id = int(res["lead_id"])
        await self.ctx.memory.set_lead_id(self.ctx.chat_id, lead_id)
        return lead_id

    async def call_manager(self, reason: str = "", comment: str = "", client_name: str = "",
                          phone: str = "", **_: Any) -> dict[str, Any]:
        state = await self.ctx.memory.load_state(self.ctx.chat_id)
        res = await self.ctx.crm.call_manager(
            telegram_chat_id=self.ctx.chat_id, lead_id=state.lead_id,
            reason=reason, comment=comment,
            client_name=client_name or state.extracted.client_name or "",
            phone=(normalize_phone(phone) if phone else state.extracted.phone) or "",
        )
        if res.get("lead_id"):
            await self.ctx.memory.set_lead_id(self.ctx.chat_id, int(res["lead_id"]))
        return res


def _result_summary(result: Any) -> str:
    if not isinstance(result, dict):
        return type(result).__name__
    parts: list[str] = []
    for k in ("status", "lead_id", "deal_id"):
        if result.get(k) is not None:
            parts.append(f"{k}={result[k]}")
    if "items" in result:
        parts.append(f"items={len(result.get('items') or [])}")
    if "products" in result:
        parts.append(f"products={len(result.get('products') or [])}")
    if "product" in result:
        prod = result.get("product")
        parts.append(f"product={prod.get('article') if isinstance(prod, dict) else None}")
    if result.get("error"):
        parts.append("error")
    return ", ".join(parts) or "ok"
