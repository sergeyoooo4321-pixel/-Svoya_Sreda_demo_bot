"""Модель заказа клиента."""
from __future__ import annotations

from typing import ClassVar, Optional

from pydantic import BaseModel


class OrderDraft(BaseModel):
    """Сбор данных для будущего заказа из сессии."""

    telegram_user_id: int
    client_name: Optional[str] = None
    phone: Optional[str] = None
    product_id: Optional[str] = None
    product_title: Optional[str] = None
    color: Optional[str] = None
    quantity: int = 1
    city: Optional[str] = None
    address_or_area: Optional[str] = None
    delivery_type: Optional[str] = None
    floor: Optional[int] = None
    has_elevator: Optional[bool] = None
    need_assembly: Optional[bool] = None
    comment: Optional[str] = None

    REQUIRED_FIELDS: ClassVar[tuple[str, ...]] = (
        "client_name",
        "phone",
        "product_id",
        "color",
        "city",
        "delivery_type",
        "need_assembly",
    )

    def missing_fields(self) -> list[str]:
        missing: list[str] = []
        for field in self.REQUIRED_FIELDS:
            value = getattr(self, field, None)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(field)
        return missing

    def is_ready(self) -> bool:
        return not self.missing_fields()
