"""Парсинг ответа модели: вытаскиваем reply + tool_call + extracted_data + crm_action.

Используется в fallback-режиме, когда модель не умеет native tool_calls и возвращает JSON.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class AgentTurn:
    """Декодированный план модели."""
    reply: str = ""
    intent: Optional[str] = None
    need_tool: bool = False
    tool_name: Optional[str] = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    crm_action: Optional[str] = None
    extracted_data: dict[str, Any] = field(default_factory=dict)
    missing_data: list[str] = field(default_factory=list)
    manager_comment: Optional[str] = None
    raw: str = ""


def parse_agent_response(content: str) -> AgentTurn:
    """Достаёт JSON-план из ответа модели; если ничего нет — отдаёт plain reply."""
    if not content:
        return AgentTurn(reply="", raw="")

    cleaned = content.strip()
    # Сначала пробуем как чистый JSON.
    data = _try_json(cleaned)
    if data is None:
        # ищем фрагмент JSON в тексте (модель могла обернуть текстом или ```json```)
        m = _JSON_BLOCK_RE.search(cleaned)
        if m:
            data = _try_json(m.group(0))

    if not isinstance(data, dict):
        # Plain text — это финальный ответ клиенту.
        return AgentTurn(reply=cleaned, raw=cleaned)

    turn = AgentTurn(raw=cleaned)
    turn.reply = str(data.get("reply") or "").strip()
    turn.intent = data.get("intent")
    turn.need_tool = bool(data.get("need_tool"))
    turn.tool_name = data.get("tool_name") or None
    args = data.get("tool_args")
    if isinstance(args, dict):
        turn.tool_args = args
    turn.crm_action = data.get("crm_action")
    if isinstance(data.get("extracted_data"), dict):
        turn.extracted_data = data["extracted_data"]
    if isinstance(data.get("missing_data"), list):
        turn.missing_data = [str(x) for x in data["missing_data"]]
    turn.manager_comment = data.get("manager_comment")
    return turn


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None
