"""Единое API памяти для Agent Core: сообщения, состояние, извлечённые данные.

Поверх существующих репозиториев — без дублирования таблиц.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import get_settings
from app.storage import repositories as repo


@dataclass
class ExtractedData:
    """То, что нужно агенту запомнить про клиента между сообщениями."""
    client_name: Optional[str] = None
    phone: Optional[str] = None
    product: Optional[str] = None        # человекочитаемое имя/описание
    product_id: Optional[str] = None     # id из каталога
    color: Optional[str] = None
    city: Optional[str] = None
    delivery_type: Optional[str] = None
    assembly_needed: Optional[str] = None  # "Да"|"Нет"|"Не указано"|None
    budget: Optional[str] = None
    last_intent: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: Optional[str]) -> "ExtractedData":
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})

    def merge(self, patch: dict[str, Any]) -> None:
        """Аккуратное обновление: пустые значения не затирают существующие."""
        for key, value in patch.items():
            if key not in self.__dataclass_fields__:
                continue
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            setattr(self, key, value)


@dataclass
class ChatState:
    telegram_chat_id: int
    summary: str = ""
    extracted: ExtractedData = field(default_factory=ExtractedData)
    lead_id: Optional[str] = None
    deal_id: Optional[str] = None
    current_stage: Optional[str] = None


@dataclass
class ChatMessage:
    role: str          # user | assistant | tool | system
    content: str


class MemoryStore:
    """Память по telegram_chat_id (для приватных чатов = telegram_user_id)."""

    async def save_user_message(self, chat_id: int, text: str) -> None:
        await repo.ensure_session(chat_id)
        await repo.append_message(chat_id, "user", text)

    async def save_assistant_message(self, chat_id: int, text: str) -> None:
        await repo.append_message(chat_id, "assistant", text)

    async def save_tool_message(self, chat_id: int, tool_name: str, result_json: str) -> None:
        # Префиксуем tool-name для удобства, чтобы было видно в истории.
        await repo.append_message(chat_id, "tool", f"[{tool_name}] {result_json}")

    async def load_last_messages(
        self, chat_id: int, limit: Optional[int] = None
    ) -> list[ChatMessage]:
        s = get_settings()
        cap = limit if limit is not None else s.memory_last_messages
        rows = await repo.get_recent_messages(chat_id, cap)
        return [ChatMessage(role=row["role"], content=row["content"]) for row in rows]

    async def load_state(self, chat_id: int) -> ChatState:
        session = await repo.ensure_session(chat_id)
        return ChatState(
            telegram_chat_id=chat_id,
            summary=session.get("summary") or "",
            extracted=ExtractedData.from_json(session.get("extracted_data_json")),
            lead_id=session.get("bitrix_lead_id"),
            deal_id=session.get("bitrix_deal_id"),
            current_stage=session.get("last_intent"),
        )

    async def save_state(self, state: ChatState) -> None:
        await repo.update_session(
            state.telegram_chat_id,
            summary=state.summary or None,
            extracted_data_json=state.extracted.to_json(),
        )
        # Зеркалим извлечённые поля в плоские столбцы — чтобы продолжали работать
        # репозитории заказов и FSM-оформление.
        ed = state.extracted
        flat: dict[str, Any] = {}
        if ed.client_name:
            flat["client_name"] = ed.client_name
        if ed.phone:
            flat["phone"] = ed.phone
        if ed.product_id:
            flat["selected_product_id"] = ed.product_id
        if ed.color:
            flat["selected_color"] = ed.color
        if ed.city:
            flat["city"] = ed.city
        if ed.delivery_type:
            flat["delivery_type"] = ed.delivery_type
        if ed.assembly_needed and ed.assembly_needed.lower() in {"да", "yes", "true"}:
            flat["need_assembly"] = 1
        elif ed.assembly_needed and ed.assembly_needed.lower() in {"нет", "no", "false"}:
            flat["need_assembly"] = 0
        if ed.last_intent:
            flat["last_intent"] = ed.last_intent
        if flat:
            await repo.update_session(state.telegram_chat_id, **flat)

    async def update_extracted(self, chat_id: int, patch: dict[str, Any]) -> ChatState:
        state = await self.load_state(chat_id)
        state.extracted.merge(patch)
        await self.save_state(state)
        return state

    async def set_lead_id(self, chat_id: int, lead_id: str) -> None:
        await repo.update_session(chat_id, bitrix_lead_id=lead_id)

    async def set_deal_id(self, chat_id: int, deal_id: str) -> None:
        await repo.update_session(chat_id, bitrix_deal_id=deal_id)
