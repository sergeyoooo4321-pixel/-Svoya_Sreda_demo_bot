from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.services.normalization import normalize_text


@dataclass(frozen=True)
class IntentResult:
    intent: str
    confidence: float
    entities: dict[str, Any] = field(default_factory=dict)


_INTENT_PHRASES: dict[str, tuple[str, ...]] = {
    "spam": (
        "купите рекламу",
        "реклама в нашем",
        "продвижение канала",
        "накрутка",
        "заработок без вложений",
    ),
    "handoff": (
        "позовите менеджера",
        "позови менеджера",
        "позови человека",
        "оператор",
        "живой менеджер",
        "пусть мне позвонят",
        "не хочу с ботом",
    ),
    "order": (
        "хочу заказать",
        "хочу купить",
        "оформить",
        "оформляем",
        "беру",
        "давайте этот",
        "заказ",
        "купить",
    ),
    "delivery": (
        "доставка",
        "доставите",
        "привезете",
        "привезёте",
        "до квартиры",
        "до подъезда",
        "на этаж",
        "лифт",
        "химки",
        "сколько ждать",
        "когда привез",
    ),
    "payment": (
        "оплата",
        "оплатить",
        "как платить",
        "картой",
        "наличными",
        "счет",
        "счёт",
        "предоплата",
    ),
    "price": (
        "цена",
        "стоимость",
        "сколько стоит",
        "почем",
        "почём",
        "дорого",
        "бюджет",
    ),
    "availability": (
        "в наличии",
        "есть в наличии",
        "есть сейчас",
        "под заказ",
        "когда будет",
        "наличие",
    ),
    "selection": (
        "что посоветуете",
        "помогите выбрать",
        "помоги выбрать",
        "что взять",
        "подобрать",
        "нужен диван",
        "нужна кровать",
        "маленькая квартира",
        "хочу уютно",
    ),
    "catalog": (
        "каталог",
        "покажи товары",
        "показать все",
        "что есть",
        "ассортимент",
    ),
    "greeting": (
        "привет",
        "здравствуйте",
        "добрый день",
        "добрый вечер",
        "начать",
        "старт",
    ),
    "complaint": (
        "жалоба",
        "проблема",
        "не привезли",
        "сломано",
        "брак",
        "не отвечает менеджер",
    ),
}

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Диваны": ("диван", "софа"),
    "Кресла": ("кресло", "кресла"),
    "Кровати": ("кровать", "кровати", "спальн"),
    "Столы": ("стол", "обеденный стол"),
    "Журнальные столики": ("журнальный", "столик"),
    "Комоды": ("комод", "комоды"),
    "Шкафы": ("шкаф", "шкафы"),
    "Стеллажи": ("стеллаж", "полка", "полки"),
    "ТВ-тумбы": ("тумба", "тв", "телевизор"),
    "Прихожие": ("прихож", "обувница", "вешалка"),
}


def detect_intent(
    text: str,
    catalog_products: Iterable[dict[str, Any]] | None = None,
) -> IntentResult:
    """Fast deterministic detector for common sales/support intents."""
    normalized = normalize_text(text)
    if not normalized:
        return IntentResult(intent="other", confidence=0.0)

    entities = extract_catalog_entities(normalized, catalog_products or ())
    best_intent = "other"
    best_score = 0

    for intent, phrases in _INTENT_PHRASES.items():
        score = _score_phrases(normalized, phrases)
        if score > best_score:
            best_intent = intent
            best_score = score

    if best_intent == "other" and entities:
        best_intent = "product_question"
        best_score = 1

    if best_intent == "order" and entities:
        confidence = 0.92
    elif best_intent in {"spam", "handoff"} and best_score:
        confidence = 0.9
    elif best_score >= 2:
        confidence = 0.85
    elif best_score == 1:
        confidence = 0.72
    else:
        confidence = 0.2

    return IntentResult(intent=best_intent, confidence=confidence, entities=entities)


def extract_catalog_entities(
    normalized_text: str,
    catalog_products: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    entities: dict[str, Any] = {}

    category = _find_category(normalized_text)
    if category:
        entities["category"] = category

    products = list(catalog_products)
    color = _find_color(normalized_text, products)
    if color:
        entities["color"] = color

    product = _find_product(normalized_text, products, category, color)
    if product:
        entities["product_id"] = product.get("id")
        entities["product_title"] = product.get("title")
        entities.setdefault("category", product.get("category"))

    return {key: value for key, value in entities.items() if value}


def _score_phrases(normalized_text: str, phrases: tuple[str, ...]) -> int:
    return sum(1 for phrase in phrases if normalize_text(phrase) in normalized_text)


def _find_category(normalized_text: str) -> str | None:
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(keyword in normalized_text for keyword in map(normalize_text, keywords)):
            return category
    return None


def _find_color(normalized_text: str, products: Iterable[dict[str, Any]]) -> str | None:
    for product in products:
        for color in product.get("colors", []):
            normalized_color = normalize_text(str(color))
            if normalized_color and (
                normalized_color in normalized_text
                or any(part in normalized_text for part in normalized_color.split())
            ):
                return str(color)
    return None


def _find_product(
    normalized_text: str,
    products: Iterable[dict[str, Any]],
    category: str | None,
    color: str | None,
) -> dict[str, Any] | None:
    scored: list[tuple[int, dict[str, Any]]] = []

    for product in products:
        score = 0
        product_title = normalize_text(str(product.get("title", "")))
        product_category = product.get("category")

        if product_title and product_title in normalized_text:
            score += 4
        if category and product_category == category:
            score += 3
        if color and color in product.get("colors", []):
            score += 2
        if str(product.get("id", "")).lower() in normalized_text:
            score += 5

        if score:
            scored.append((score, product))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]
