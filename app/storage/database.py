"""Подключение к SQLite через aiosqlite."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import aiosqlite

from app.config import get_settings
from app.storage.migrations import apply_migrations


_connection: Optional[aiosqlite.Connection] = None


async def init_db() -> aiosqlite.Connection:
    """Создать каталог под БД, открыть соединение и применить миграции."""
    global _connection
    if _connection is not None:
        return _connection

    settings = get_settings()
    db_path = settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(str(db_path))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await apply_migrations(conn)
    _connection = conn
    return conn


async def get_db() -> aiosqlite.Connection:
    if _connection is None:
        return await init_db()
    return _connection


async def close_db() -> None:
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None


@asynccontextmanager
async def transaction() -> AsyncIterator[aiosqlite.Connection]:
    db = await get_db()
    try:
        yield db
        await db.commit()
    except Exception:
        await db.rollback()
        raise
