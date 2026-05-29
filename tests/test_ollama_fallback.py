"""Тесты на парсинг ответа Ollama (Codex чеклист: невалидный JSON не ломает бота)."""
from __future__ import annotations

import os

# Минимальный env, чтобы pydantic-settings не ругался при импорте.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1234:test")

from app.models.ai import AIResponse
from app.services.ollama_client import OllamaClient


def _make_client() -> OllamaClient:
    # Не открываем сеть — нам нужен только _parse / _fallback.
    return OllamaClient()


def test_parse_clean_json() -> None:
    client = _make_client()
    raw = (
        '{"answer":"Привет","intent":"greeting","lead_stage":"new",'
        '"extracted_data":{},"missing_fields":[],'
        '"need_manager":false,"should_create_order":false}'
    )
    result = client._parse(raw)
    assert isinstance(result, AIResponse)
    assert result.intent == "greeting"
    assert result.answer == "Привет"


def test_parse_json_inside_markdown_text() -> None:
    """Модель иногда оборачивает JSON в текст — должны достать."""
    client = _make_client()
    raw = (
        "Конечно, вот ответ:\n```json\n"
        '{"answer":"Доставим","intent":"delivery","lead_stage":"consultation",'
        '"extracted_data":{"city":"Химки"},"missing_fields":[],'
        '"need_manager":false,"should_create_order":false}'
        "\n```\nГотово."
    )
    result = client._parse(raw)
    assert result.intent == "delivery"
    assert result.extracted_data.city == "Химки"


def test_invalid_json_falls_back_to_other() -> None:
    """Полностью битый ответ → fallback intent=other, бот не падает."""
    client = _make_client()
    result = client._parse("извините, я не могу")
    assert result.intent == "other"
    assert result.need_manager is True
    assert "менеджер" in result.answer.lower()
