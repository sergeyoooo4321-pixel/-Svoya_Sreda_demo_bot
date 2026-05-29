"""Модель товара каталога."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class Product(BaseModel):
    id: str
    title: str
    category: str
    price: int
    price_text: str
    dimensions: Optional[str] = None
    sleeping_place: Optional[str] = None
    material: Optional[str] = None
    colors: list[str] = []
    availability: Optional[str] = None
    delivery_note: Optional[str] = None
    description: Optional[str] = None
    manager_hint: Optional[str] = None
    image_path: Optional[str] = None  # на будущее: путь к фото товара
