"""Бизнес-логика лидов: создание/обновление, синхронизация через Bitrix24 + outbox."""
from __future__ import annotations

from typing import Any, Optional

from app.config import get_settings
from app.logger import logger
from app.models.crm import LeadPayload
from app.services.bitrix_client import BitrixClient
from app.storage import repositories as repo


_STAGE_BY_AI: dict[str, str] = {}


def _stages() -> dict[str, str]:
    global _STAGE_BY_AI
    if _STAGE_BY_AI:
        return _STAGE_BY_AI
    s = get_settings()
    _STAGE_BY_AI = {
        "new": s.bitrix_lead_stage_new,
        "consultation": s.bitrix_lead_stage_consultation,
        "waiting_decision": s.bitrix_lead_stage_waiting,
        "order_created": s.bitrix_lead_stage_order_created,
        "refused": s.bitrix_lead_stage_refused,
    }
    return _STAGE_BY_AI


def build_lead_payload(
    *,
    name: Optional[str],
    phone: Optional[str],
    product_title: Optional[str],
    city: Optional[str],
    bot_comment: Optional[str],
    lead_stage_key: Optional[str],
) -> LeadPayload:
    # lead_stage_key=None → stage_id=None → STATUS_ID не отправляется, стадия не меняется.
    stage_id = _stages().get(lead_stage_key) if lead_stage_key else None
    return LeadPayload(
        title=f"Заявка из Telegram — Своя Среда{(' / ' + product_title) if product_title else ''}",
        name=name,
        phone=phone,
        stage_id=stage_id,
        product_title=product_title,
        city=city,
        bot_comment=bot_comment,
    )


class LeadService:
    def __init__(self, bitrix: BitrixClient) -> None:
        self.bitrix = bitrix

    async def ensure_lead(
        self,
        telegram_user_id: int,
        *,
        payload: LeadPayload,
    ) -> Optional[str]:
        """Создать лид, если его ещё нет, иначе — обновить. Возвращает bitrix_lead_id."""
        session = await repo.ensure_session(telegram_user_id)
        existing_id = session.get("bitrix_lead_id")

        if existing_id:
            await self._update(telegram_user_id, existing_id, payload)
            return existing_id

        # создание
        if self.bitrix.enabled:
            result = await self.bitrix.create_lead(payload)
            if result.ok and result.entity_id:
                await repo.update_session(telegram_user_id, bitrix_lead_id=result.entity_id)
                logger.info(
                    f"CRM: создан лид {result.entity_id} для tg_user={telegram_user_id}"
                )
                return result.entity_id
            logger.warning(
                f"CRM: не удалось создать лид для tg_user={telegram_user_id}: {result.error}"
            )
            await repo.enqueue_outbox(
                telegram_user_id=telegram_user_id,
                entity_type="lead",
                operation="create",
                payload=_dump(payload),
                last_error=result.error,
            )
            return None

        # CRM выключен — просто складываем в outbox, чтобы не потерять
        await repo.enqueue_outbox(
            telegram_user_id=telegram_user_id,
            entity_type="lead",
            operation="create",
            payload=_dump(payload),
            last_error="Bitrix24 disabled",
        )
        return None

    async def _update(self, telegram_user_id: int, lead_id: str, payload: LeadPayload) -> None:
        if not self.bitrix.enabled:
            await repo.enqueue_outbox(
                telegram_user_id=telegram_user_id,
                entity_type="lead",
                operation="update",
                payload=_dump(payload),
                target_id=lead_id,
                last_error="Bitrix24 disabled",
            )
            return
        result = await self.bitrix.update_lead(lead_id, payload)
        if not result.ok:
            logger.warning(f"CRM: не удалось обновить лид {lead_id}: {result.error}")
            await repo.enqueue_outbox(
                telegram_user_id=telegram_user_id,
                entity_type="lead",
                operation="update",
                payload=_dump(payload),
                target_id=lead_id,
                last_error=result.error,
            )
        else:
            logger.info(f"CRM: обновлён лид {lead_id} для tg_user={telegram_user_id}")


def _dump(payload: LeadPayload) -> dict[str, Any]:
    return payload.model_dump()
