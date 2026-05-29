"""Подключение к локальной БД Svoya CRM (отдельный SQLite-файл data/svoya_crm.db).

Бот и веб-слой (API/админка) могут работать как один процесс или раздельно — у каждого
процесса своё соединение, WAL допускает параллельное чтение и одного писателя.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import aiosqlite

from app.config import get_settings
from app.crm.schema import apply_schema


_connection: Optional[aiosqlite.Connection] = None


async def init_crm_db() -> aiosqlite.Connection:
    global _connection
    if _connection is not None:
        return _connection

    settings = get_settings()
    path = settings.svoya_crm_db_path
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(str(path))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await apply_schema(conn)
    _connection = conn
    return conn


async def get_crm_db() -> aiosqlite.Connection:
    if _connection is None:
        return await init_crm_db()
    return _connection


async def close_crm_db() -> None:
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None


@asynccontextmanager
async def crm_transaction() -> AsyncIterator[aiosqlite.Connection]:
    db = await get_crm_db()
    try:
        yield db
        await db.commit()
    except Exception:
        await db.rollback()
        raise
