"""Клиент Ollama: structured output по нашей JSON-схеме."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx
from pydantic import ValidationError

from app.config import get_settings
from app.logger import logger
from app.models.ai import AIResponse, AI_JSON_SCHEMA


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


class OllamaClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        headers = {"Content-Type": "application/json"}
        if self.settings.ollama_api_key:
            headers["Authorization"] = f"Bearer {self.settings.ollama_api_key}"
        self._client = httpx.AsyncClient(
            base_url=self.settings.ollama_base_url.rstrip("/"),
            timeout=self.settings.ollama_timeout_seconds,
            headers=headers,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        system_prompt: str,
        history: list[dict[str, str]],
        user_message: str,
        context_block: str,
    ) -> AIResponse:
        """
        history — список {role, content} (последние N сообщений).
        context_block — каталог + faq, склеенный в одну строку.
        """
        if not self.settings.ollama_enabled:
            return self._fallback("Ollama выключена в конфигурации.")

        messages: list[dict[str, str]] = [
            {"role": "system", "content": f"{system_prompt}\n\n---\nКОНТЕКСТ:\n{context_block}"},
        ]
        for msg in history:
            role = msg.get("role")
            if role in {"user", "assistant"} and msg.get("content"):
                messages.append({"role": role, "content": msg["content"]})
        messages.append({"role": "user", "content": user_message})

        body: dict[str, Any] = {
            "model": self.settings.ollama_model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": self.settings.ollama_temperature},
        }
        if self.settings.ollama_use_structured_output:
            body["format"] = AI_JSON_SCHEMA

        try:
            response = await self._client.post("/api/chat", json=body)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            logger.warning(f"Ollama HTTP error: {exc.__class__.__name__}: {exc}")
            return self._fallback(f"HTTP error: {exc.__class__.__name__}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Ollama unexpected error: {exc!r}")
            return self._fallback(f"unexpected: {exc.__class__.__name__}")

        content = self._extract_content(data)
        if not content:
            logger.warning("Ollama: пустой content в ответе")
            return self._fallback("Пустой ответ модели.")

        return self._parse(content)

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> Optional[str]:
        msg = data.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
        # некоторые билды Ollama кладут под "response"
        resp = data.get("response")
        if isinstance(resp, str):
            return resp
        return None

    def _parse(self, content: str) -> AIResponse:
        # Сначала пробуем как чистый JSON.
        try:
            return AIResponse.model_validate_json(content)
        except (ValidationError, ValueError):
            pass

        # Достаём JSON из текста (модель могла обернуть в текст или ```json).
        match = _JSON_BLOCK_RE.search(content)
        if match:
            try:
                data = json.loads(match.group(0))
                return AIResponse.model_validate(data)
            except (ValidationError, ValueError) as exc:
                logger.warning(f"Ollama: не удалось распарсить JSON ({exc.__class__.__name__}).")

        logger.warning("Ollama: невалидный JSON, fallback intent=other.")
        return self._fallback(content[:400])

    def _fallback(self, reason: str) -> AIResponse:
        logger.debug(f"Ollama fallback: {reason}")
        return AIResponse(
            answer=(
                "Сейчас не хочу ошибиться с ответом. Передам вопрос менеджеру, "
                "чтобы он уточнил информацию."
            ),
            intent="other",
            lead_stage="consultation",
            need_manager=True,
            should_create_order=False,
        )

    # ---------------- Agent Core: tool calling ----------------

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: Optional[float] = None,
    ) -> dict[str, Any]:
        """Низкоуровневый /api/chat с поддержкой tools.

        Возвращает сырой message-блок: {role, content, tool_calls?}.
        НЕ задействует structured output (`format`), чтобы не конфликтовать с tools.
        """
        body: dict[str, Any] = {
            "model": self.settings.ollama_model,
            "messages": messages,
            "stream": False,
            "tools": tools,
            "options": {
                "temperature": temperature if temperature is not None else self.settings.ollama_temperature,
                # Окно контекста: иначе дефолт llama3.1 (часто 2048-4096) обрежет 50 сообщений
                # + tool-результаты, и контекстные фразы вроде «а доставка?» потеряются (раздел 10).
                "num_ctx": self.settings.ollama_num_ctx,
            },
        }
        try:
            response = await self._client.post("/api/chat", json=body)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            logger.warning(f"Ollama HTTP error (chat_with_tools): {exc.__class__.__name__}: {exc}")
            return {"role": "assistant", "content": "", "error": f"http:{exc.__class__.__name__}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Ollama unexpected error (chat_with_tools): {exc!r}")
            return {"role": "assistant", "content": "", "error": f"unexpected:{exc.__class__.__name__}"}

        msg = data.get("message") or {}
        # Нормализуем tool_calls в единый формат: list[{name, arguments}]
        raw_tool_calls = msg.get("tool_calls") or []
        normalized_calls: list[dict[str, Any]] = []
        for call in raw_tool_calls:
            fn = call.get("function") if isinstance(call, dict) else None
            if not isinstance(fn, dict):
                continue
            name = fn.get("name")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (ValueError, TypeError):
                    args = {}
            if name:
                normalized_calls.append({"name": name, "arguments": args})

        return {
            "role": "assistant",
            "content": msg.get("content") or "",
            "tool_calls": normalized_calls,
        }
