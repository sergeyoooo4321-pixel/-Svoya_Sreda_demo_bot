"""Главный цикл агента.

Последовательность (раздел 11 рю.md):
1. save_user_message
2. load_memory + state
3. собрать вход для модели
4. вызвать ollama.chat_with_tools (native tools)
5. если tool_calls — выполнить, передать tool_result обратно (до AGENT_MAX_TOOL_CALLS)
6. финальный текст → send_to_telegram
7. save_assistant_message + update extracted + summary
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from app.agent.response_parser import AgentTurn, parse_agent_response
from app.agent.short_prompt import SHORT_SYSTEM_PROMPT, build_context_message
from app.config import get_settings
from app.logger import logger
from app.crm.memory import ChatMessage, MemoryStore
from app.memory.summarizer import maybe_summarize
from app.services.normalization import extract_phone, mask_phone, normalize_phone, normalize_text
from app.services.ollama_client import OllamaClient
from app.tools.crm_tools import ToolRegistry
from app.tools.tool_schemas import TOOL_NAMES, TOOL_SCHEMAS


SAFE_FALLBACK_REPLY = (
    "Сейчас есть небольшая техническая пауза. Я зафиксировал обращение, "
    "менеджер сможет вернуться к нему."
)

# Поля, которые CRM/каталожные tools несут о клиенте — забираем их в память,
# чтобы EXTRACTED_DATA наполнялась и в нативном tool-режиме (не только в JSON-fallback).
_ARG_TO_MEMORY_KEYS = (
    "client_name", "phone", "product", "color", "city",
    "delivery_type", "assembly_needed",
)

# Детерминированная страховка стадий CRM (раздел 13), не зависящая от модели.
_WAITING_PHRASES = (
    "подума", "посоветуюс", "посоветуем", "покажу жен", "покажу муж",
    "обсужу с", "вернус", "попозже", "позже реш", "не сейчас", "надо подумать",
)
_SPAM_PHRASES = (
    "куплю рекламу", "купите рекламу", "реклама в", "продвижени",
    "накрутк", "заработок без вложен",
)


def _detect_stage_override(text: str) -> Optional[str]:
    """Возвращает stage для update_lead, если текст явно про «подумаю» или спам."""
    t = normalize_text(text)
    if not t:
        return None
    if any(p in t for p in _SPAM_PHRASES):
        return "rejected"
    if any(p in t for p in _WAITING_PHRASES):
        return "waiting"
    return None


# Москва и Подмосковье — чтобы запоминать город прямо из реплики (раздел 24).
_CITIES = (
    "москва", "химки", "балашиха", "мытищи", "люберцы", "подольск", "королев", "одинцово",
    "красногорск", "реутов", "домодедово", "щелково", "пушкино", "видное", "котельники",
    "лобня", "жуковский", "раменское", "серпухов", "клин", "истра", "зеленоград",
    "апрелевка", "дзержинский", "нахабино", "сходня", "ивантеевка", "фрязино", "долгопрудный",
)
_DELIVERY_PHRASES = {
    "до квартиры": "До квартиры", "до подъезда": "До подъезда",
    "самовывоз": "Самовывоз", "заберу сам": "Самовывоз", "сам заберу": "Самовывоз",
}
_ORDER_PHRASES = ("оформ", "офрмл", "беру", "берем", "готов купить", "покупаю", "заказыва", "куплю", "давайте этот")


def _extract_entities(text: str) -> dict[str, str]:
    """Достаёт город и формат доставки из текста клиента."""
    t = normalize_text(text)
    out: dict[str, str] = {}
    if not t:
        return out
    for city in _CITIES:
        if city in t:
            out["city"] = city[:1].upper() + city[1:]
            break
    for kw, val in _DELIVERY_PHRASES.items():
        if kw in t:
            out["delivery_type"] = val
            break
    return out


def _is_order_intent(text: str) -> bool:
    t = normalize_text(text)
    return any(p in t for p in _ORDER_PHRASES)


@dataclass
class AgentResult:
    reply: str
    tool_calls_made: int = 0
    actions: list[dict[str, Any]] = field(default_factory=list)  # для логов/тестов
    used_fallback: bool = False
    manager_required: bool = False


class AgentCore:
    def __init__(self, ollama: OllamaClient, memory: MemoryStore, tools_factory) -> None:
        """tools_factory(chat_id) -> ToolRegistry — фабрика, чтобы chat_id попадал в контекст."""
        self.ollama = ollama
        self.memory = memory
        self.tools_factory = tools_factory

    async def handle(self, chat_id: int, user_text: str) -> AgentResult:
        settings = get_settings()
        s = settings

        # 1. сохраняем входящее
        await self.memory.save_user_message(chat_id, user_text)
        logger.info(f"AgentCore: chat={chat_id} incoming='{user_text[:80]}'")

        # 2. подтягиваем телефон/город/доставку/интент прямо из текста (раздел 24) — нужно всем веткам
        phone_from_text = extract_phone(user_text)
        if phone_from_text:
            await self.memory.update_extracted(chat_id, {"phone": phone_from_text})
        ents = _extract_entities(user_text)
        if ents:
            await self.memory.update_extracted(chat_id, ents)
        if _is_order_intent(user_text):
            await self.memory.update_extracted(chat_id, {"last_intent": "order"})

        # 3. загружаем память
        state = await self.memory.load_state(chat_id)
        history = await self.memory.load_last_messages(chat_id, s.memory_last_messages)

        # 4. если Ollama не настроена — безопасный путь без LLM
        if not s.ollama_enabled:
            logger.warning("AgentCore: Ollama выключена — отвечаю шаблоном и зову менеджера.")
            return await self._handle_without_llm(chat_id)

        # 5. собираем сообщения для модели
        messages = self._build_messages(history, state, user_text)
        tools = TOOL_SCHEMAS

        tool_calls_made = 0
        actions: list[dict[str, Any]] = []
        registry = self.tools_factory(chat_id)

        for iteration in range(s.agent_max_tool_calls + 1):
            assistant_msg = await self.ollama.chat_with_tools(messages, tools)

            # Сетевая/системная ошибка Ollama
            if assistant_msg.get("error"):
                if iteration == 0:
                    return await self._handle_without_llm(chat_id)
                logger.warning("AgentCore: Ollama упала в середине цикла — выдаём fallback-ответ.")
                final_text = SAFE_FALLBACK_REPLY
                await self.memory.save_assistant_message(chat_id, final_text)
                return AgentResult(
                    reply=final_text, tool_calls_made=tool_calls_made,
                    actions=actions, used_fallback=True,
                )

            content = assistant_msg.get("content") or ""
            native_calls = assistant_msg.get("tool_calls") or []

            # Native tool_calls — выполнили, добавили результат, идём ещё раз.
            if native_calls and tool_calls_made < s.agent_max_tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {"function": {"name": c["name"], "arguments": c["arguments"]}}
                        for c in native_calls
                    ],
                })
                for call in native_calls:
                    if tool_calls_made >= s.agent_max_tool_calls:
                        break
                    result = await registry.call(call["name"], call["arguments"])
                    actions.append({"tool": call["name"], "args": call["arguments"], "result": result})
                    tool_calls_made += 1
                    await self._harvest_tool_to_memory(chat_id, call["name"], call["arguments"], result)
                    messages.append({
                        "role": "tool",
                        "name": call["name"],
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                    await self.memory.save_tool_message(
                        chat_id, call["name"], json.dumps(result, ensure_ascii=False)[:1500],
                    )
                continue  # ещё одна итерация

            # Если модель опять хочет tool, но лимит уже исчерпан — аварийный выход.
            # Если в этом же ответе уже есть готовый текст — используем его, чтобы не терять ответ.
            if native_calls and tool_calls_made >= s.agent_max_tool_calls:
                logger.warning(
                    f"AgentCore: модель ещё просит tools, но лимит {s.agent_max_tool_calls} достигнут."
                )
                final_text = content.strip() or SAFE_FALLBACK_REPLY
                used_fallback = not content.strip()
                await self.memory.save_assistant_message(chat_id, final_text)
                return AgentResult(
                    reply=final_text,
                    tool_calls_made=tool_calls_made,
                    actions=actions,
                    used_fallback=used_fallback,
                )

            # Native tool_calls не было — пробуем JSON-fallback
            turn = parse_agent_response(content)
            if (
                turn.need_tool
                and turn.tool_name in TOOL_NAMES
                and tool_calls_made < s.agent_max_tool_calls
            ):
                result = await registry.call(turn.tool_name, turn.tool_args)
                actions.append({"tool": turn.tool_name, "args": turn.tool_args, "result": result})
                tool_calls_made += 1
                await self._harvest_tool_to_memory(chat_id, turn.tool_name, turn.tool_args, result)
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "tool",
                    "name": turn.tool_name,
                    "content": json.dumps(result, ensure_ascii=False),
                })
                await self.memory.save_tool_message(
                    chat_id, turn.tool_name, json.dumps(result, ensure_ascii=False)[:1500],
                )
                # Если модель в этом же ответе уже извлекла данные клиента — сохраним
                if turn.extracted_data:
                    await self.memory.update_extracted(chat_id, turn.extracted_data)
                continue

            # Финальный ответ — берём reply из JSON либо весь content
            final_text = (turn.reply or content or SAFE_FALLBACK_REPLY).strip()
            if turn.extracted_data:
                await self.memory.update_extracted(chat_id, turn.extracted_data)

            manager_required = self._actions_required_manager(actions)

            # Детерминированная страховка стадий CRM (раздел 13): «подумаю/покажу жене» →
            # Ждёт решения, спам → Отказ. Не зависим от того, вызвала ли это модель сама.
            if not manager_required:
                stage_override = _detect_stage_override(user_text)
                if stage_override:
                    res = await registry.call("update_lead", {"stage": stage_override})
                    actions.append({"tool": "update_lead", "args": {"stage": stage_override}, "result": res})
                else:
                    # §22: первое осмысленное обращение должно стать лидом (Консультация),
                    # даже если модель не вызвала CRM-инструмент. Стадию существующего лида не трогаем.
                    try:
                        await registry.ensure_lead("consultation")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"ensure_lead: {exc!r}")
                    # §13.4: товар + телефон + интент заказа → создаём сделку детерминированно,
                    # если модель сама её не создала (idempotency защитит от дублей).
                    try:
                        st = await self.memory.load_state(chat_id)
                        ed = st.extracted
                        wants_order = _is_order_intent(user_text) or ed.last_intent == "order"
                        already = any(a.get("tool") == "create_deal" for a in actions)
                        if (wants_order and ed.phone and (ed.product or ed.product_article)
                                and not st.deal_id and not already):
                            res = await registry.call("create_deal", {})
                            actions.append({"tool": "create_deal", "args": {}, "result": res})
                            if res.get("status") == "created":
                                final_text = (
                                    "Спасибо! Оформил заявку: " + (ed.product or "товар")
                                    + (f", {ed.color}" if ed.color else "")
                                    + (f", доставка в {ed.city}" if ed.city else "")
                                    + ". Менеджер свяжется для подтверждения деталей и стоимости."
                                )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"auto create_deal: {exc!r}")

            await self.memory.save_assistant_message(chat_id, final_text)
            await self._maybe_update_summary(chat_id, state)
            logger.info(
                f"AgentCore: chat={chat_id} done tools={tool_calls_made} "
                f"manager={manager_required} reply='{final_text[:80]}'"
            )

            return AgentResult(
                reply=final_text,
                tool_calls_made=tool_calls_made,
                actions=actions,
                used_fallback=False,
                manager_required=manager_required,
            )

        # Превысили лимит итераций
        logger.warning(f"AgentCore: лимит {s.agent_max_tool_calls} tool calls превышен.")
        final_text = SAFE_FALLBACK_REPLY
        await self.memory.save_assistant_message(chat_id, final_text)
        return AgentResult(
            reply=final_text,
            tool_calls_made=tool_calls_made,
            actions=actions,
            used_fallback=True,
        )

    # ---------------- helpers ----------------

    def _build_messages(
        self,
        history: list[ChatMessage],
        state,
        user_text: str,
    ) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = [
            {"role": "system", "content": SHORT_SYSTEM_PROMPT},
        ]
        context_block = build_context_message(
            state.extracted.to_json(), state.summary,
        )
        if context_block:
            msgs.append({"role": "system", "content": context_block})

        # последние сообщения (без самой свежей user-реплики, её добавим в конец)
        for m in history[:-1] if history else []:
            if m.role in {"user", "assistant"}:
                msgs.append({"role": m.role, "content": m.content})

        msgs.append({"role": "user", "content": user_text})
        return msgs

    async def _harvest_tool_to_memory(
        self, chat_id: int, tool_name: str, args: Any, result: Any
    ) -> None:
        """Складывает в память данные клиента из аргументов tool и результата get_product.

        Нужно, чтобы EXTRACTED_DATA наполнялась товаром/цветом/городом и в нативном
        tool-режиме, когда финальный ответ модели — обычный текст без JSON (раздел 10).
        """
        patch: dict[str, Any] = {}
        if isinstance(args, dict):
            for key in _ARG_TO_MEMORY_KEYS:
                val = args.get(key)
                if isinstance(val, str) and val.strip():
                    patch[key] = val.strip()
        if patch.get("phone"):
            norm = normalize_phone(patch["phone"])
            if norm:
                patch["phone"] = norm
            else:
                patch.pop("phone")
        if isinstance(result, dict):
            product = result.get("product")
            if isinstance(product, dict):
                if product.get("name"):
                    patch.setdefault("product", product["name"])
                if product.get("article"):
                    patch["product_id"] = product["article"]
                if product.get("matched_color"):
                    patch.setdefault("color", product["matched_color"])
        if patch:
            await self.memory.update_extracted(chat_id, patch)

    async def _handle_without_llm(self, chat_id: int) -> AgentResult:
        """Безопасный режим: создаём лид с пометкой и зовём менеджера."""
        registry = self.tools_factory(chat_id)
        state = await self.memory.load_state(chat_id)
        reason = "Ollama недоступна / выключена"
        result = await registry.call("call_manager", {
            "reason": reason,
            "comment": "Бот не может ответить сам, нужно подключение менеджера.",
            "client_name": state.extracted.client_name or "",
            "phone": state.extracted.phone or "",
        })
        text = SAFE_FALLBACK_REPLY
        await self.memory.save_assistant_message(chat_id, text)
        logger.info(
            f"AgentCore fallback (no LLM): chat={chat_id} "
            f"phone={mask_phone(state.extracted.phone)} -> manager"
        )
        return AgentResult(
            reply=text, tool_calls_made=1,
            actions=[{"tool": "call_manager", "args": {"reason": reason}, "result": result}],
            used_fallback=True, manager_required=True,
        )

    async def _maybe_update_summary(self, chat_id: int, state) -> None:
        history = await self.memory.load_last_messages(chat_id)
        new_summary = await maybe_summarize(self.ollama, history, state.summary)
        if new_summary and new_summary != state.summary:
            state.summary = new_summary
            await self.memory.save_state(state)

    @staticmethod
    def _actions_required_manager(actions: list[dict[str, Any]]) -> bool:
        for a in actions:
            if a.get("tool") == "call_manager":
                return True
            res = a.get("result") or {}
            if isinstance(res, dict) and res.get("status") == "manager_required":
                return True
        return False
