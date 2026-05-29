"""Тесты загрузки каталога."""
from __future__ import annotations

from app.services.catalog_service import (
    CATEGORY_GROUPS,
    _load_image_map,
    get_product,
    get_products_by_group,
    list_categories,
    load_catalog,
    resolve_image_path,
)


def test_catalog_loads() -> None:
    products = load_catalog()
    assert products, "catalog.json должен содержать товары"
    ids = {p.id for p in products}
    assert "SS-DV-210" in ids


def test_categories_match_groups() -> None:
    cats = set(list_categories())
    # каждая группа из CATEGORY_GROUPS (кроме "all") должна ссылаться хотя бы
    # на одну реальную категорию из каталога
    for group, cats_in_group in CATEGORY_GROUPS.items():
        if group == "all" or not cats_in_group:
            continue
        assert any(c in cats for c in cats_in_group), (
            f"группа {group} не имеет ни одной из категорий {cats_in_group} в каталоге"
        )


def test_get_products_by_group_all_returns_everything() -> None:
    assert len(get_products_by_group("all")) == len(load_catalog())


def test_get_product_by_id() -> None:
    p = get_product("SS-DV-210")
    assert p is not None
    assert p.title.startswith("Диван")


def test_image_map_covers_every_product() -> None:
    """Каждый товар каталога должен иметь запись в product_images.json."""
    image_map = _load_image_map()
    missing = [p.id for p in load_catalog() if p.id not in image_map]
    assert not missing, f"нет фото для товаров: {missing}"


def test_product_image_path_resolves() -> None:
    """Маппинг ведёт на существующий файл (картинки лежат в «фотки товара/»)."""
    p = get_product("SS-DV-210")
    assert p is not None and p.image_path
    resolved = resolve_image_path(p.image_path)
    assert resolved is not None and resolved.exists()
