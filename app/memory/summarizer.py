"""Краткое summary истории — чтобы не передавать в Ollama 50 сообщений каждый раз.

Тяжёлую работу делает сама Ollama: ниже — детерминированный фолбэк, если ИИ недоступен.
"""
from __future__ import annotations

from app.config import get_settings
from app.logger import logger
from app.crm.memory import ChatMessage
from app.services.ollama_client import OllamaClient


SUMMARIZE_PROMPT = (
    "Ты — внутренний помощник. Сожми переписку клиента и менеджера в 5-8 строк по-русски: "
    "что выбрали, что обсудили, какие цифры и города, что осталось уточнить. "
    "Никаких рассуждений, только факты в одном абзаце."
)


async def maybe_summarize(
    ollama: OllamaClient,
    messages: list[ChatMessage],
    previous_summary: str = "",
) -> str:
    """Возвращает summary; если Ollama недоступна — собирает короткий список фактов сам."""
    s = get_settings()
    # Порог «30 сообщений» считаем по репликам клиента/бота, без служебных tool-строк.
    relevant = [m for m in messages if m.role in {"user", "assistant"}]
    if len(relevant) < s.memory_summary_after_messages:
        return previous_summary
    messages = relevant

    # 1) Сначала пробуем Ollama (без structured output, plain text).
    if s.ollama_enabled:
        try:
            text = await _summarize_with_ollama(ollama, messages, previous_summary)
            if text:
                return text.strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Summarizer: Ollama упала, fallback на эвристику: {exc!r}")

    # 2) Фолбэк — простая эвристика.
    return _heuristic_summary(messages, previous_summary)


async def _summarize_with_ollama(
    ollama: OllamaClient,
    messages: list[ChatMessage],
    previous_summary: str,
) -> str:
    conversation = "\n".join(
        f"{m.role}: {m.content[:400]}" for m in messages if m.role in {"user", "assistant"}
    )
    prefix = f"Старое summary:\n{previous_summary}\n\n" if previous_summary else ""
    user_block = (
        f"{prefix}Переписка:\n{conversation}\n\nОбнови краткое summary одним абзацем."
    )
    # Используем низкоуровневый POST в /api/generate, чтобы не задействовать structured output
    resp = await ollama._client.post(
        "/api/generate",
        json={
            "model": ollama.settings.ollama_model,
            "prompt": f"{SUMMARIZE_PROMPT}\n\n{user_block}",
            "stream": False,
            "options": {"temperature": 0.2},
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("response", "")


def _heuristic_summary(messages: list[ChatMessage], previous_summary: str) -> str:
    last_user = next(
        (m.content for m in reversed(messages) if m.role == "user"), ""
    )
    user_count = sum(1 for m in messages if m.role == "user")
    base = previous_summary.strip()
    addon = f"Всего реплик клиента: {user_count}. Последний вопрос: «{last_user[:200]}»."
    return (base + "\n" + addon).strip() if base else addon
