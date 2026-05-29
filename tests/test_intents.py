"""Тесты rule-based intent-классификатора и нормализации."""
from __future__ import annotations

import pytest

from app.services.intent_rules import detect_intent
from app.services.normalization import extract_phone, mask_phone, normalize_phone


CATALOG_SAMPLE = [
    {
        "id": "SS-DV-210",
        "title": "Диван «Линия 210»",
        "category": "Диваны",
        "price": 58900,
        "colors": ["Тёплый бежевый", "Графитовый серый", "Оливковый зелёный"],
    },
    {
        "id": "SS-KV-160",
        "title": "Кровать «Соната 160»",
        "category": "Кровати",
        "price": 49900,
        "colors": ["Светло-серый", "Бежевый", "Пыльно-синий"],
    },
]


@pytest.mark.parametrize("phrase", [
    "сколько доставка",
    "а доставка есть",
    "когда привезёте",
    "в химки доставите",
    "до квартиры поднимете",
    "на этаж занесете",
    "сколько ждать",
])
def test_delivery_intent_variants(phrase: str) -> None:
    result = detect_intent(phrase, CATALOG_SAMPLE)
    assert result.intent == "delivery", f"phrase={phrase}, got={result.intent}"
    assert result.confidence >= 0.7


@pytest.mark.xfail(reason="Падежи слова «доставка» — улучшение Codex по корню «доставк».")
def test_delivery_intent_inflected() -> None:
    """«за доставку сколько» — словоформа, которую rule-based пока не ловит."""
    assert detect_intent("за доставку сколько", CATALOG_SAMPLE).intent == "delivery"


@pytest.mark.parametrize("phrase", [
    "хочу заказать",
    "беру",
    "оформляем",
    "хочу купить",
])
def test_order_intent_variants(phrase: str) -> None:
    result = detect_intent(phrase, CATALOG_SAMPLE)
    assert result.intent == "order"


@pytest.mark.parametrize("phrase", [
    "позовите менеджера",
    "оператор",
    "не хочу с ботом",
])
def test_handoff_intent_variants(phrase: str) -> None:
    result = detect_intent(phrase, CATALOG_SAMPLE)
    assert result.intent == "handoff"


def test_spam_intent() -> None:
    result = detect_intent("купите рекламу в нашем канале", CATALOG_SAMPLE)
    assert result.intent == "spam"


def test_catalog_search_by_category_and_color() -> None:
    """Фраза «графитовый диван» должна найти SS-DV-210 и цвет «Графитовый серый»."""
    result = detect_intent("хочу графитовый диван", CATALOG_SAMPLE)
    assert result.entities.get("product_id") == "SS-DV-210"
    assert result.entities.get("color") == "Графитовый серый"


@pytest.mark.parametrize("raw,expected", [
    ("+79991234567", "+79991234567"),
    ("89991234567", "+79991234567"),
    ("8 999 123-45-67", "+79991234567"),
    ("+7 (999) 123-45-67", "+79991234567"),
    ("999 123 45 67", "+79991234567"),
])
def test_phone_normalization(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


def test_phone_extract_from_text() -> None:
    assert extract_phone("Сергей, +7 (999) 123-45-67, спасибо") == "+79991234567"


def test_phone_mask() -> None:
    assert mask_phone("+79991234567") == "+7999******67"
    assert mask_phone(None) == ""
