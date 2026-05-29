"""Память диалога на таблицах chat_messages / chat_states (раздел т.md §6.10–6.11, §24).

Интерфейс совместим со старым app.memory.memory_store.MemoryStore, чтобы Agent Core
почти не переписывать.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import get_settings
from app.crm.db import get_crm_db


@dataclass
class ExtractedData:
    client_name: Optional[str] = None
    phone: Optional[str] = None
    product: Optional[str] = None
    product_article: Optional[str] = None   # §6.11
    product_id: Optional[str] = None         # алиас article (совместимость со старым агентом)
    color: Optional[str] = None
    city: Optional[str] = None
    delivery_type: Optional[str] = None
    assembly_needed: Optional[str] = None
    budget: Optional[str] = None
    last_intent: Optional[str] = None

    def __post_init__(self) -> None:
        # держим product_article и product_id согласованными (это синонимы)
        if self.product_article and not self.product_id:
            self.product_id = self.product_article
        elif self.product_id and not self.product_article:
            self.product_article = self.product_id

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
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def merge(self, patch: dict[str, Any]) -> None:
        for key, value in patch.items():
            if key not in self.__dataclass_fields__:
                continue
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            setattr(self, key, value)
        # держим product_article и product_id синхронными
        if self.product_article and not self.product_id:
            self.product_id = self.product_article
        if self.product_id and not self.product_article:
            self.product_article = self.product_id


@dataclass
class ChatState:
    telegram_chat_id: int
    summary: str = ""
    extracted: ExtractedData = field(default_factory=ExtractedData)
    lead_id: Optional[int] = None
    deal_id: Optional[int] = None
    current_stage: Optional[str] = None


@dataclass
class ChatMessage:
    role: str
    content: str


class MemoryStore:
    """Память по telegram_chat_id в БД Svoya CRM."""

    async def _ensure_state(self, chat_id: int) -> None:
        db = await get_crm_db()
        await db.execute(
            "INSERT OR IGNORE INTO chat_states (telegram_chat_id, extracted_data_json) VALUES (?, ?)",
            (chat_id, ExtractedData().to_json()),
        )
        await db.commit()

    async def save_user_message(self, chat_id: int, text: str) -> None:
        await self._append(chat_id, "user", text)

    async def save_assistant_message(self, chat_id: int, text: str) -> None:
        await self._append(chat_id, "assistant", text)

    async def save_tool_message(self, chat_id: int, tool_name: str, result_json: str) -> None:
        await self._append(chat_id, "tool", f"[{tool_name}] {result_json}",
                           metadata={"tool": tool_name})

    async def _append(self, chat_id: int, role: str, content: str, metadata: Optional[dict] = None) -> None:
        await self._ensure_state(chat_id)
        db = await get_crm_db()
        await db.execute(
            "INSERT INTO chat_messages (telegram_chat_id, role, content, metadata_json) VALUES (?, ?, ?, ?)",
            (chat_id, role, content, json.dumps(metadata or {}, ensure_ascii=False)),
        )
        await db.commit()

    async def load_last_messages(self, chat_id: int, limit: Optional[int] = None) -> list[ChatMessage]:
        cap = limit if limit is not None else get_settings().memory_last_messages
        if cap <= 0:
            return []
        db = await get_crm_db()
        async with db.execute(
            "SELECT role, content FROM chat_messages WHERE telegram_chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, cap),
        ) as cur:
            rows = await cur.fetchall()
        return [ChatMessage(role=r["role"], content=r["content"]) for r in reversed(rows)]

    async def load_state(self, chat_id: int) -> ChatState:
        await self._ensure_state(chat_id)
        db = await get_crm_db()
        async with db.execute("SELECT * FROM chat_states WHERE telegram_chat_id = ?", (chat_id,)) as cur:
            row = await cur.fetchone()
        row = dict(row) if row else {}
        return ChatState(
            telegram_chat_id=chat_id,
            summary=row.get("summary") or "",
            extracted=ExtractedData.from_json(row.get("extracted_data_json")),
            lead_id=row.get("lead_id"),
            deal_id=row.get("deal_id"),
            current_stage=row.get("current_stage"),
        )

    async def save_state(self, state: ChatState) -> None:
        await self._ensure_state(state.telegram_chat_id)
        db = await get_crm_db()
        await db.execute(
            "UPDATE chat_states SET summary=?, extracted_data_json=?, lead_id=?, deal_id=?, "
            "current_stage=?, updated_at=datetime('now') WHERE telegram_chat_id=?",
            (state.summary or None, state.extracted.to_json(), state.lead_id, state.deal_id,
             state.current_stage, state.telegram_chat_id),
        )
        await db.commit()

    async def update_extracted(self, chat_id: int, patch: dict[str, Any]) -> ChatState:
        state = await self.load_state(chat_id)
        state.extracted.merge(patch)
        await self.save_state(state)
        return state

    async def set_lead_id(self, chat_id: int, lead_id: int) -> None:
        state = await self.load_state(chat_id)
        state.lead_id = lead_id
        await self.save_state(state)

    async def set_deal_id(self, chat_id: int, deal_id: int) -> None:
        state = await self.load_state(chat_id)
        state.deal_id = deal_id
        await self.save_state(state)

    async def set_stage(self, chat_id: int, stage_code: str) -> None:
        state = await self.load_state(chat_id)
        state.current_stage = stage_code
        await self.save_state(state)
