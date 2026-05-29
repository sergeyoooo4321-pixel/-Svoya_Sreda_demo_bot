"""Объединяет rule-based детектор Codex с Ollama-фолбэком.

Сначала пробуем быстрые правила. Если уверенности нет — обращаемся в Ollama.
"""
from __future__ import annotations

from typing import Any

from app.logger import logger
from app.models.ai import AIResponse, ExtractedData
from app.services.catalog_service import catalog_for_prompt, load_catalog
from app.services.faq_service import load_faq, load_system_prompt
from app.services.intent_rules import IntentResult, detect_intent
from app.services.normalization import extract_phone
from app.services.ollama_client import OllamaClient


_INTENT_TO_STAGE: dict[str, str] = {
    "greeting": "new",
    "catalog": "new",
    "product_question": "consultation",
    "delivery": "consultation",
    "payment": "consultation",
    "assembly": "consultation",
    "warranty": "consultation",
    "return": "consultation",
    "availability": "consultation",
    "price": "consultation",
    "selection": "consultation",
    "order": "consultation",
    "handoff": "consultation",
    "complaint": "consultation",
    "spam": "refused",
    "other": "consultation",
}


def _quick_answer(intent_result: IntentResult) -> str | None:
    """Безопасные шаблоны для срабатываний rule-based, чтобы не дёргать Ollama зря."""
    intent = intent_result.intent
    if intent == "spam":
        return (
            "Я помощник мебельного магазина «Своя Среда». "
            "Помогаю с выбором мебели, доставкой и оформлением заказа."
        )
    if intent == "handoff":
        return (
            "Сейчас передам заявку менеджеру. Подскажите, как к вам обращаться "
            "и удобный номер для связи?"
        )
    if intent == "greeting":
        return (
            "Здравствуйте! Я помощник «Своей Среды». "
            "Могу показать каталог, ответить по доставке/оплате или помочь оформить заказ."
        )
    return None


def _context_block() -> str:
    """Собираем компактный контекст для Ollama: каталог + FAQ."""
    catalog = catalog_for_prompt()
    faq = load_faq()
    catalog_lines = []
    for item in catalog:
        colors = ", ".join(item.get("colors", []))
        catalog_lines.append(
            f"- {item['id']}: {item['title']} | {item['category']} | "
            f"{item['price']}₽ | цвета: {colors} | {item['availability']}"
        )
    return "КАТАЛОГ:\n" + "\n".join(catalog_lines) + "\n\nFAQ:\n" + faq


class IntentService:
    def __init__(self, ollama: OllamaClient) -> None:
        self.ollama = ollama

    async def classify_or_answer(
        self,
        text: str,
        history: list[dict[str, str]],
    ) -> AIResponse:
        products = [p.model_dump() for p in load_catalog()]
        rule_result = detect_intent(text, products)

        quick = _quick_answer(rule_result)
        if quick is not None and rule_result.confidence >= 0.7:
            response = AIResponse(
                answer=quick,
                intent=rule_result.intent,
                lead_stage=_INTENT_TO_STAGE.get(rule_result.intent, "consultation"),
                need_manager=rule_result.intent == "handoff",
                should_create_order=False,
            )
            self._enrich(response, text, rule_result.entities)
            logger.debug(f"intent (rule, quick): {rule_result.intent} c={rule_result.confidence}")
            return response

        # Идём в Ollama
        ai_response = await self.ollama.chat(
            system_prompt=load_system_prompt(),
            history=history,
            user_message=text,
            context_block=_context_block(),
        )
        # Если у rule-based высокая уверенность — доверяем её intent больше, чем модели.
        if rule_result.confidence >= 0.85:
            ai_response.intent = rule_result.intent
            ai_response.lead_stage = _INTENT_TO_STAGE.get(rule_result.intent, ai_response.lead_stage)
        self._enrich(ai_response, text, rule_result.entities)
        logger.debug(
            f"intent: rule={rule_result.intent}({rule_result.confidence:.2f}) "
            f"→ итог={ai_response.intent}"
        )
        return ai_response

    @staticmethod
    def _enrich(response: AIResponse, text: str, entities: dict[str, Any]) -> None:
        """Дотащить телефон/товар/цвет из rule-based слоя, если модель их не вытащила."""
        ed = response.extracted_data or ExtractedData()

        if not ed.phone:
            phone = extract_phone(text)
            if phone:
                ed.phone = phone

        if not ed.product_id and entities.get("product_id"):
            ed.product_id = entities["product_id"]
        if not ed.product_title and entities.get("product_title"):
            ed.product_title = entities["product_title"]
        if not ed.color and entities.get("color"):
            ed.color = entities["color"]

        response.extracted_data = ed
