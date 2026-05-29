import json
from pathlib import Path

from app.services.intent_rules import detect_intent
from app.services.normalization import extract_phone, mask_phone, normalize_phone


CATALOG_PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "catalog.json"


def load_catalog() -> list[dict]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def test_phone_normalization_variants() -> None:
    assert normalize_phone("+7 (999) 123-45-67") == "+79991234567"
    assert normalize_phone("8 999 123-45-67") == "+79991234567"
    assert normalize_phone("9991234567") == "+79991234567"
    assert extract_phone("Сергей, 8 999 123-45-67, Химки") == "+79991234567"
    assert mask_phone("+79991234567") == "+7999******67"


def test_delivery_intent_variants() -> None:
    catalog = load_catalog()
    result = detect_intent("а за сколько доставите диван в Химки", catalog)

    assert result.intent == "delivery"
    assert result.confidence >= 0.7
    assert result.entities["category"] == "Диваны"


def test_order_intent_extracts_product_and_color() -> None:
    catalog = load_catalog()
    result = detect_intent("хочу графитовый диван оформить", catalog)

    assert result.intent == "order"
    assert result.entities["product_id"] == "SS-DV-210"
    assert result.entities["color"] == "Графитовый серый"


def test_spam_is_detected_before_ai() -> None:
    result = detect_intent("купите рекламу в нашем канале")

    assert result.intent == "spam"
    assert result.confidence >= 0.9
