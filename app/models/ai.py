"""Структуры ответа от Ollama (structured output)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


Intent = Literal[
    "greeting",
    "catalog",
    "product_question",
    "delivery",
    "payment",
    "assembly",
    "warranty",
    "return",
    "availability",
    "price",
    "selection",
    "order",
    "handoff",
    "complaint",
    "spam",
    "other",
]


LeadStage = Literal[
    "new",
    "consultation",
    "waiting_decision",
    "order_created",
    "refused",
]


DeliveryType = Literal["До подъезда", "До квартиры", "Самовывоз"]


class ExtractedData(BaseModel):
    client_name: Optional[str] = None
    phone: Optional[str] = None
    product_title: Optional[str] = None
    product_id: Optional[str] = None
    color: Optional[str] = None
    city: Optional[str] = None
    address_or_area: Optional[str] = None
    delivery_type: Optional[DeliveryType] = None
    need_assembly: Optional[bool] = None
    comment: Optional[str] = None


class AIResponse(BaseModel):
    answer: str
    intent: Intent = "other"
    lead_stage: LeadStage = "new"
    extracted_data: ExtractedData = Field(default_factory=ExtractedData)
    missing_fields: list[str] = Field(default_factory=list)
    need_manager: bool = False
    should_create_order: bool = False


AI_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "intent": {
            "type": "string",
            "enum": list(Intent.__args__),
        },
        "lead_stage": {
            "type": "string",
            "enum": list(LeadStage.__args__),
        },
        "extracted_data": {
            "type": "object",
            "properties": {
                "client_name": {"type": ["string", "null"]},
                "phone": {"type": ["string", "null"]},
                "product_title": {"type": ["string", "null"]},
                "product_id": {"type": ["string", "null"]},
                "color": {"type": ["string", "null"]},
                "city": {"type": ["string", "null"]},
                "address_or_area": {"type": ["string", "null"]},
                "delivery_type": {
                    "type": ["string", "null"],
                    "enum": ["До подъезда", "До квартиры", "Самовывоз", None],
                },
                "need_assembly": {"type": ["boolean", "null"]},
                "comment": {"type": ["string", "null"]},
            },
        },
        "missing_fields": {
            "type": "array",
            "items": {"type": "string"},
        },
        "need_manager": {"type": "boolean"},
        "should_create_order": {"type": "boolean"},
    },
    "required": [
        "answer",
        "intent",
        "lead_stage",
        "extracted_data",
        "missing_fields",
        "need_manager",
        "should_create_order",
    ],
}
