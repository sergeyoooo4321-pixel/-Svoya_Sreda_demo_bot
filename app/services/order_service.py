"""Сборка и оформление заказа: локальное сохранение + сделка в Bitrix24."""
from __future__ import annotations

from typing import Any, Optional

from app.config import get_settings
from app.logger import logger
from app.models.crm import DealPayload, LeadPayload
from app.models.order import OrderDraft
from app.services.bitrix_client import BitrixClient
from app.services.catalog_service import get_product
from app.services.lead_service import LeadService, build_lead_payload
from app.storage import repositories as repo


class OrderService:
    def __init__(self, bitrix: BitrixClient, lead_service: LeadService) -> None:
        self.bitrix = bitrix
        self.lead_service = lead_service

    async def build_draft(self, telegram_user_id: int) -> OrderDraft:
        session = await repo.ensure_session(telegram_user_id)
        product_id = session.get("selected_product_id")
        product = get_product(product_id) if product_id else None
        return OrderDraft(
            telegram_user_id=telegram_user_id,
            client_name=session.get("client_name"),
            phone=session.get("phone"),
            product_id=product.id if product else None,
            product_title=product.title if product else None,
            color=session.get("selected_color"),
            city=session.get("city"),
            delivery_type=session.get("delivery_type"),
            need_assembly=_to_bool(session.get("need_assembly")),
        )

    async def submit(self, telegram_user_id: int, comment: Optional[str] = None) -> dict[str, Any]:
        """
        Финализация: лид → ORDER_CREATED, локальный заказ, опциональная сделка.
        Возвращает: {order_id, lead_id, deal_id, crm_status}.
        """
        draft = await self.build_draft(telegram_user_id)
        if not draft.is_ready():
            raise ValueError(f"Order draft is not ready: missing {draft.missing_fields()}")

        product = get_product(draft.product_id) if draft.product_id else None
        s = get_settings()

        # 1. Обновляем/создаём лид в стадии "Заказ оформлен"
        lead_payload = build_lead_payload(
            name=draft.client_name,
            phone=draft.phone,
            product_title=draft.product_title,
            city=draft.city,
            bot_comment=_compose_lead_comment(draft, comment),
            lead_stage_key="order_created",
        )
        lead_id = await self.lead_service.ensure_lead(telegram_user_id, payload=lead_payload)

        # 2. Локальный заказ
        order_id = await repo.create_order({
            "telegram_user_id": telegram_user_id,
            "client_name": draft.client_name,
            "phone": draft.phone,
            "product_id": draft.product_id,
            "product_title": draft.product_title,
            "color": draft.color,
            "quantity": draft.quantity,
            "city": draft.city,
            "address_or_area": draft.address_or_area,
            "delivery_type": draft.delivery_type,
            "floor": draft.floor,
            "has_elevator": _to_int(draft.has_elevator),
            "need_assembly": _to_int(draft.need_assembly),
            "comment": comment,
            "status": "new",
            "bitrix_lead_id": lead_id,
        })

        # 3. Сделка
        deal_id: Optional[str] = None
        crm_status = "ok" if lead_id else "crm_sync_failed"

        deal_payload = DealPayload(
            title=f"Заказ мебели из Telegram — {draft.product_title or 'товар'}",
            opportunity=int(product.price) if product else None,
            stage_id=s.bitrix_deal_stage_new,
            color=draft.color,
            delivery_type=draft.delivery_type,
            need_assembly=draft.need_assembly,
            comments=_compose_deal_comment(draft, comment),
        )

        if self.bitrix.enabled:
            result = await self.bitrix.create_deal(deal_payload)
            if result.ok and result.entity_id:
                deal_id = result.entity_id
                await repo.update_session(telegram_user_id, bitrix_deal_id=deal_id)
                await repo.update_order(order_id, bitrix_deal_id=deal_id, status="submitted")
                logger.info(f"CRM: создана сделка {deal_id} (order={order_id})")
            else:
                crm_status = "crm_sync_failed"
                await repo.enqueue_outbox(
                    telegram_user_id=telegram_user_id,
                    entity_type="deal",
                    operation="create",
                    payload=deal_payload.model_dump(),
                    last_error=result.error,
                )
                await repo.update_order(order_id, status="crm_sync_failed")
        else:
            # CRM выключен — заказ остаётся локально, сделку положили в outbox.
            await repo.enqueue_outbox(
                telegram_user_id=telegram_user_id,
                entity_type="deal",
                operation="create",
                payload=deal_payload.model_dump(),
                last_error="Bitrix24 disabled",
            )
            await repo.update_order(order_id, status="crm_sync_failed")
            crm_status = "crm_sync_failed"

        return {
            "order_id": order_id,
            "lead_id": lead_id,
            "deal_id": deal_id,
            "crm_status": crm_status,
        }


def _compose_lead_comment(draft: OrderDraft, extra: Optional[str]) -> str:
    parts = ["Готовая заявка из Telegram."]
    if draft.product_title:
        parts.append(f"Товар: {draft.product_title}.")
    if draft.color:
        parts.append(f"Цвет: {draft.color}.")
    if draft.city:
        parts.append(f"Город: {draft.city}.")
    if draft.delivery_type:
        parts.append(f"Доставка: {draft.delivery_type}.")
    if draft.need_assembly is not None:
        parts.append("Сборка: нужна." if draft.need_assembly else "Сборка: не нужна.")
    if extra:
        parts.append(extra)
    return " ".join(parts)


def _compose_deal_comment(draft: OrderDraft, extra: Optional[str]) -> str:
    return _compose_lead_comment(draft, extra)


def _to_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "да", "y"}:
            return True
        if lowered in {"0", "false", "no", "нет", "n"}:
            return False
    return None


def _to_int(value: Optional[bool]) -> Optional[int]:
    if value is None:
        return None
    return 1 if value else 0
