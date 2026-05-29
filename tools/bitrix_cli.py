"""MCP-like CLI-обёртка над инструментами агента (вариант B, раздел 8 ТЗ).

Даёт единый внешний интерфейс к tools из командной строки — такой же, как у
`ToolRegistry.call(name, args)`. Позже его можно заменить на полноценный MCP-сервер
без изменения Agent Core.

Примеры:
    python tools/bitrix_cli.py search_knowledge '{"query":"сколько доставка в химки","intent":"delivery","limit":3}'
    python tools/bitrix_cli.py get_product '{"query":"графитовый диван"}'
    python tools/bitrix_cli.py list_products '{"category":"диваны","filters":{"in_stock":true},"limit":3}'
    python tools/bitrix_cli.py create_lead '{"client_name":"Андрей","phone":"+79991234567","product":"Диван Линия 210"}' 12345

Третий необязательный аргумент — telegram_chat_id (по умолчанию 0).
Модель НЕ ходит в Bitrix24 напрямую: всё идёт через эту обёртку, как в боте.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Делаем пакет app импортируемым при запуске скрипта напрямую.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.memory.memory_store import MemoryStore
from app.services.bitrix_client import BitrixClient
from app.services.lead_service import LeadService
from app.storage.database import close_db, init_db
from app.tools.bitrix24_tools import ToolContext, ToolRegistry
from app.tools.tool_schemas import TOOL_NAMES


async def _run(name: str, args: dict, chat_id: int) -> dict:
    await init_db()
    bitrix = BitrixClient()
    try:
        registry = ToolRegistry(ToolContext(
            chat_id=chat_id,
            bitrix=bitrix,
            lead_service=LeadService(bitrix),
            memory=MemoryStore(),
        ))
        return await registry.call(name, args)
    finally:
        await bitrix.close()
        await close_db()


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in {"-h", "--help"}:
        print("Использование: python tools/bitrix_cli.py <tool_name> '<json_args>' [telegram_chat_id]")
        print("Доступные tools:", ", ".join(TOOL_NAMES))
        return 0

    name = argv[1]
    raw_args = argv[2] if len(argv) > 2 else "{}"
    chat_id = int(argv[3]) if len(argv) > 3 else 0

    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"invalid json args: {exc}"}, ensure_ascii=False))
        return 2
    if not isinstance(args, dict):
        print(json.dumps({"error": "json args must be an object"}, ensure_ascii=False))
        return 2

    result = asyncio.run(_run(name, args, chat_id))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
