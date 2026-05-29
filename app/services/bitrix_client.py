"""Клиент Bitrix24: универсальные методы crm.item.* + fallback на crm.lead/deal.*."""
from __future__ import annotations

from typing import Any, Optional

import httpx

from app.config import get_settings
from app.logger import logger
from app.models.crm import CRMResult, DealPayload, LeadPayload


class BitrixClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._client = httpx.AsyncClient(timeout=self.settings.bitrix_timeout_seconds)

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def enabled(self) -> bool:
        return self.settings.bitrix_enabled

    # ---------- LEAD ----------

    async def create_lead(self, payload: LeadPayload) -> CRMResult:
        if not self.enabled:
            return CRMResult(ok=False, error="Bitrix24 disabled")
        fields = self._lead_fields(payload)
        if self.settings.bitrix_use_universal_crm:
            result = await self._call("crm.item.add", {
                "entityTypeId": self.settings.bitrix_entity_type_id_lead,
                "fields": fields,
            })
            entity_id = self._extract_universal_id(result)
            if result.ok and entity_id:
                result.entity_id = entity_id
                return result
            logger.warning("Bitrix: crm.item.add для лида не сработал, пробую crm.lead.add")
        # fallback
        result = await self._call("crm.lead.add", {"fields": fields})
        if result.ok:
            value = result.raw.get("result")
            if value:
                result.entity_id = str(value)
        return result

    async def update_lead(self, lead_id: str, payload: LeadPayload) -> CRMResult:
        if not self.enabled:
            return CRMResult(ok=False, error="Bitrix24 disabled")
        fields = self._lead_fields(payload)
        if self.settings.bitrix_use_universal_crm:
            result = await self._call("crm.item.update", {
                "entityTypeId": self.settings.bitrix_entity_type_id_lead,
                "id": lead_id,
                "fields": fields,
            })
            if result.ok:
                result.entity_id = lead_id
                return result
            logger.warning("Bitrix: crm.item.update для лида не сработал, пробую crm.lead.update")
        result = await self._call("crm.lead.update", {"id": lead_id, "fields": fields})
        if result.ok:
            result.entity_id = lead_id
        return result

    # ---------- DEAL ----------

    async def create_deal(self, payload: DealPayload) -> CRMResult:
        if not self.enabled:
            return CRMResult(ok=False, error="Bitrix24 disabled")
        fields = self._deal_fields(payload)
        if self.settings.bitrix_use_universal_crm:
            result = await self._call("crm.item.add", {
                "entityTypeId": self.settings.bitrix_entity_type_id_deal,
                "fields": fields,
            })
            entity_id = self._extract_universal_id(result)
            if result.ok and entity_id:
                result.entity_id = entity_id
                return result
            logger.warning("Bitrix: crm.item.add для сделки не сработал, пробую crm.deal.add")
        result = await self._call("crm.deal.add", {"fields": fields})
        if result.ok:
            value = result.raw.get("result")
            if value:
                result.entity_id = str(value)
        return result

    # ---------- field adapters ----------

    def _lead_fields(self, payload: LeadPayload) -> dict[str, Any]:
        s = self.settings
        fields: dict[str, Any] = {
            "TITLE": payload.title,
            "SOURCE_ID": payload.source_id,
            "SOURCE_DESCRIPTION": payload.source_description,
        }
        if payload.name:
            fields["NAME"] = payload.name
        if payload.phone:
            fields["PHONE"] = [{"VALUE": payload.phone, "VALUE_TYPE": "WORK"}]
        if payload.stage_id:
            fields["STATUS_ID"] = payload.stage_id
        if payload.product_title:
            fields[s.bitrix_lead_field_product] = payload.product_title
        if payload.city:
            fields[s.bitrix_lead_field_city] = payload.city
        if payload.bot_comment:
            fields[s.bitrix_lead_field_bot_comment] = payload.bot_comment
        return fields

    def _deal_fields(self, payload: DealPayload) -> dict[str, Any]:
        s = self.settings
        fields: dict[str, Any] = {
            "TITLE": payload.title,
            "CURRENCY_ID": payload.currency_id,
            "SOURCE_ID": payload.source_id,
            "SOURCE_DESCRIPTION": payload.source_description,
        }
        if payload.opportunity is not None:
            fields["OPPORTUNITY"] = payload.opportunity
        if payload.stage_id:
            fields["STAGE_ID"] = payload.stage_id
        if payload.color:
            fields[s.bitrix_deal_field_color] = payload.color
        if payload.delivery_type:
            fields[s.bitrix_deal_field_delivery_type] = payload.delivery_type
        if payload.need_assembly is not None:
            fields[s.bitrix_deal_field_assembly] = "Y" if payload.need_assembly else "N"
        if payload.delivery_date:
            fields[s.bitrix_deal_field_delivery_date] = payload.delivery_date
        if payload.comments:
            fields["COMMENTS"] = payload.comments
        return fields

    # ---------- low-level ----------

    @staticmethod
    def _extract_universal_id(result: CRMResult) -> Optional[str]:
        if not result.ok:
            return None
        raw = result.raw.get("result")
        if isinstance(raw, dict):
            item = raw.get("item")
            if isinstance(item, dict) and item.get("id") is not None:
                return str(item["id"])
        return None

    async def _call(self, method: str, params: dict[str, Any]) -> CRMResult:
        base = self.settings.bitrix_webhook_url.rstrip("/")
        url = f"{base}/{method}.json"
        try:
            response = await self._client.post(url, json=params)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            logger.warning(f"Bitrix HTTP error ({method}): {exc.__class__.__name__}")
            return CRMResult(ok=False, error=f"{exc.__class__.__name__}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Bitrix unexpected error ({method}): {exc!r}")
            return CRMResult(ok=False, error=str(exc)[:200])

        if "error" in data:
            err = data.get("error_description") or data.get("error") or "unknown error"
            logger.warning(f"Bitrix error ({method}): {err}")
            return CRMResult(ok=False, raw=data, error=str(err)[:200])
        return CRMResult(ok=True, raw=data)
