"""Оркестрация свободного диалога: история → intent → сессия → CRM-лид → ответ."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.config import get_settings
from app.logger import logger
from app.models.ai import AIResponse, ExtractedData
from app.services.bitrix_client import BitrixClient
from app.services.intent_service import IntentService
from app.services.lead_service import LeadService, build_lead_payload
from app.services.normalization import mask_phone, normalize_phone
from app.services.order_service import OrderService
from app.storage import repositories as repo


@dataclass
class ConversationResult:
    text: str
    ai: AIResponse
    order_submitted: bool = False
    order_id: Optional[int] = None
    crm_status: Optional[str] = None


class ConversationService:
    def __init__(
        self,
        intent_service: IntentService,
        lead_service: LeadService,
        order_service: OrderService,
        bitrix: BitrixClient,
    ) -> None:
        self.intent_service = intent_service
        self.lead_service = lead_service
        self.order_service = order_service
        self.bitrix = bitrix

    async def handle_message(
        self,
        telegram_user_id: int,
        text: str,
    ) -> ConversationResult:
        settings = get_settings()

        await repo.ensure_session(telegram_user_id)
        await repo.append_message(telegram_user_id, "user", text)

        history = await repo.get_recent_messages(telegram_user_id, settings.history_limit)
        # last item in history is the just-saved user message — убираем дубль
        history_for_model = history[:-1] if history else []

        ai = await self.intent_service.classify_or_answer(text, history_for_model)
        await repo.append_message(telegram_user_id, "assistant", ai.answer)

        # Сохраняем извлечённые данные в сессию (и нормализуем телефон).
        await self._save_extracted(telegram_user_id, ai.extracted_data)
        await repo.update_session(
            telegram_user_id,
            current_state="conversation",
            last_intent=ai.intent,
        )

        # Решаем, что делать с CRM.
        order_submitted = False
        order_id: Optional[int] = None
        crm_status: Optional[str] = None

        if ai.intent != "spam":
            await self._sync_lead(telegram_user_id, ai)

        if ai.should_create_order or ai.intent == "order":
            draft = await self.order_service.build_draft(telegram_user_id)
            if draft.is_ready():
                outcome = await self.order_service.submit(
                    telegram_user_id,
                    comment=ai.extracted_data.comment if ai.extracted_data else None,
                )
                order_submitted = True
                order_id = outcome["order_id"]
                crm_status = outcome["crm_status"]
                logger.info(
                    f"Order submitted: id={order_id} tg={telegram_user_id} "
                    f"phone={mask_phone(draft.phone)} crm={crm_status}"
                )

        return ConversationResult(
            text=ai.answer,
            ai=ai,
            order_submitted=order_submitted,
            order_id=order_id,
            crm_status=crm_status,
        )

    async def _save_extracted(self, telegram_user_id: int, ed: ExtractedData) -> None:
        if not ed:
            return
        update_kwargs: dict = {}
        if ed.product_id:
            update_kwargs["selected_product_id"] = ed.product_id
        if ed.color:
            update_kwargs["selected_color"] = ed.color
        if ed.client_name:
            update_kwargs["client_name"] = ed.client_name
        if ed.city:
            update_kwargs["city"] = ed.city
        if ed.delivery_type:
            update_kwargs["delivery_type"] = ed.delivery_type
        if ed.need_assembly is not None:
            update_kwargs["need_assembly"] = 1 if ed.need_assembly else 0
        if ed.phone:
            normalized = normalize_phone(ed.phone) or ed.phone
            update_kwargs["phone"] = normalized
            await repo.set_user_phone(telegram_user_id, normalized)
        if update_kwargs:
            await repo.update_session(telegram_user_id, **update_kwargs)

    async def _sync_lead(self, telegram_user_id: int, ai: AIResponse) -> None:
        session = await repo.ensure_session(telegram_user_id)
        ed = ai.extracted_data

        payload = build_lead_payload(
            name=ed.client_name or session.get("client_name"),
            phone=ed.phone or session.get("phone"),
            product_title=ed.product_title,  # title для CRM лучше человекочитаемый
            city=ed.city or session.get("city"),
            bot_comment=ai.answer[:500],
            lead_stage_key=ai.lead_stage or "consultation",
        )
        await self.lead_service.ensure_lead(telegram_user_id, payload=payload)
