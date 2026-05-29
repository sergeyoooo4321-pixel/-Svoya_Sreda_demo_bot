"""Загрузка и фильтрация каталога товаров."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from app.models.catalog import Product


CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "catalog.json"
IMAGES_MAP_PATH = Path(__file__).resolve().parent.parent / "data" / "product_images.json"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# Группы кнопок Gemini → реальные категории из catalog.json.
CATEGORY_GROUPS: dict[str, tuple[str, ...]] = {
    "divany": ("Диваны",),
    "kresla": ("Кресла",),
    "krovati": ("Кровати",),
    "stoly": ("Столы",),
    "hranenie": ("Комоды", "Шкафы", "Стеллажи", "Журнальные столики", "ТВ-тумбы"),
    "prihozhie": ("Прихожие",),
    "all": (),  # все
}


@lru_cache(maxsize=1)
def _load_image_map() -> dict[str, str]:
    if not IMAGES_MAP_PATH.exists():
        return {}
    with IMAGES_MAP_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, str)}


@lru_cache(maxsize=1)
def load_catalog() -> list[Product]:
    """Каталог из локальной CRM (svoya_crm.db). Фолбэк — catalog.json, если БД пуста/недоступна."""
    crm_products = _load_from_crm_db()
    return crm_products if crm_products else _load_from_json()


def _load_from_crm_db() -> list[Product]:
    """Синхронное чтение товаров из svoya_crm.db (10 строк — дёшево, не тянем async)."""
    import sqlite3

    from app.config import get_settings
    try:
        path = get_settings().svoya_crm_db_path
        if not path.exists():
            return []
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM products WHERE active = 1 ORDER BY id").fetchall()
        finally:
            conn.close()
    except Exception:
        return []

    products: list[Product] = []
    for r in rows:
        try:
            colors = json.loads(r["colors_json"] or "[]")
        except (ValueError, TypeError):
            colors = []
        products.append(Product(
            id=r["article"], title=r["name"], category=r["category"],
            price=r["price"], price_text=r["price_text"] or f"{r['price']} ₽",
            dimensions=r["sizes"], sleeping_place=r["sleeping_place"], material=r["material"],
            colors=colors, availability=r["stock"], description=r["description"],
            manager_hint=r["manager_recommendation"],
            image_path=(f"фотки товара/{r['image_folder']}" if r["image_folder"] else None),
        ))
    return products


def _load_from_json() -> list[Product]:
    if not CATALOG_PATH.exists():
        return []
    with CATALOG_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    image_map = _load_image_map()
    products: list[Product] = []
    for item in raw:
        if "image_path" not in item:
            mapped = image_map.get(item.get("id", ""))
            if mapped:
                item["image_path"] = mapped
        products.append(Product(**item))
    return products


_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def resolve_image_path(image_path: Optional[str]) -> Optional[Path]:
    """Возвращает абсолютный путь к файлу-изображению товара или None.

    Устойчиво к двум ситуациям:
    1. Папка «фотки товара» может лежать как внутри проекта, так и рядом с ним
       (на уровень выше) — проверяем оба варианта.
    2. Запись маппинга может указывать на папку с цветовыми вариантами товара —
       тогда берём первый по алфавиту файл-изображение внутри неё (FSInputFile
       не умеет отправлять каталог, ему нужен конкретный файл).
    """
    if not image_path:
        return None
    candidate = Path(image_path)
    if candidate.is_absolute():
        roots = [candidate]
    else:
        roots = [PROJECT_ROOT / candidate, PROJECT_ROOT.parent / candidate]

    for root in roots:
        resolved = root.resolve()
        if not resolved.exists():
            continue
        if resolved.is_dir():
            images = sorted(
                f for f in resolved.iterdir()
                if f.is_file() and f.suffix.lower() in _IMAGE_EXTS
            )
            if images:
                return images[0]
            continue
        return resolved
    return None


def list_categories() -> list[str]:
    return sorted({p.category for p in load_catalog()})


def get_products_by_group(group: str) -> list[Product]:
    products = load_catalog()
    categories = CATEGORY_GROUPS.get(group)
    if not categories:
        return list(products)
    cats = set(categories)
    return [p for p in products if p.category in cats]


def get_product(product_id: str) -> Optional[Product]:
    for p in load_catalog():
        if p.id == product_id:
            return p
    return None


def find_product_by_title(title: str) -> Optional[Product]:
    """Точечный поиск по полному названию."""
    if not title:
        return None
    target = title.strip().lower()
    for p in load_catalog():
        if p.title.lower() == target:
            return p
    return None


def catalog_for_prompt() -> list[dict]:
    """Компактный список товаров для передачи в Ollama-промт."""
    return [
        {
            "id": p.id,
            "title": p.title,
            "category": p.category,
            "price": p.price,
            "colors": p.colors,
            "availability": p.availability,
        }
        for p in load_catalog()
    ]
