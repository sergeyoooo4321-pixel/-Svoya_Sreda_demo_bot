"""SQL-миграции (выполняются при старте, идемпотентно)."""
from __future__ import annotations

import aiosqlite


SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS telegram_users (
        telegram_user_id INTEGER PRIMARY KEY,
        username         TEXT,
        first_name       TEXT,
        last_name        TEXT,
        phone            TEXT,
        created_at       TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_sessions (
        telegram_user_id    INTEGER PRIMARY KEY,
        current_state       TEXT,
        last_intent         TEXT,
        selected_product_id TEXT,
        selected_color      TEXT,
        client_name         TEXT,
        phone               TEXT,
        city                TEXT,
        delivery_type       TEXT,
        need_assembly       INTEGER,  -- 0/1
        bitrix_lead_id      TEXT,
        bitrix_deal_id      TEXT,
        created_at          TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_user_id INTEGER NOT NULL,
        role             TEXT NOT NULL,  -- user/assistant/system
        content          TEXT NOT NULL,
        created_at       TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_user_id  INTEGER NOT NULL,
        client_name       TEXT,
        phone             TEXT,
        product_id        TEXT,
        product_title     TEXT,
        color             TEXT,
        quantity          INTEGER DEFAULT 1,
        city              TEXT,
        address_or_area   TEXT,
        delivery_type     TEXT,
        floor             INTEGER,
        has_elevator      INTEGER,
        need_assembly     INTEGER,
        comment           TEXT,
        status            TEXT NOT NULL DEFAULT 'new',
        bitrix_lead_id    TEXT,
        bitrix_deal_id    TEXT,
        created_at        TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS crm_outbox (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_user_id  INTEGER NOT NULL,
        entity_type       TEXT NOT NULL,  -- lead / deal
        operation         TEXT NOT NULL,  -- create / update
        target_id         TEXT,           -- lead/deal id для update
        payload_json      TEXT NOT NULL,
        status            TEXT NOT NULL DEFAULT 'pending',  -- pending/synced/failed
        attempts          INTEGER NOT NULL DEFAULT 0,
        last_error        TEXT,
        created_at        TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_tg_id ON telegram_users(telegram_user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_tg_id ON conversation_sessions(telegram_user_id)",
    "CREATE INDEX IF NOT EXISTS idx_messages_tg_created ON messages(telegram_user_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)",
    "CREATE INDEX IF NOT EXISTS idx_orders_tg ON orders(telegram_user_id)",
    "CREATE INDEX IF NOT EXISTS idx_outbox_status ON crm_outbox(status, updated_at)",
)


# Доп. миграции — выполняются после основной схемы, идемпотентно (ALTER TABLE IF NOT EXISTS не существует
# в sqlite, поэтому проверяем PRAGMA table_info вручную).
async def _ensure_column(db: aiosqlite.Connection, table: str, column: str, ddl: str) -> None:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if column not in cols:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


async def apply_migrations(db: aiosqlite.Connection) -> None:
    for stmt in SCHEMA_STATEMENTS:
        await db.execute(stmt)
    # Поля для AgentCore (summary + extracted_data JSON) — добавляем мягко,
    # чтобы старые БД продолжали работать.
    await _ensure_column(
        db, "conversation_sessions", "summary",
        "summary TEXT",
    )
    await _ensure_column(
        db, "conversation_sessions", "extracted_data_json",
        "extracted_data_json TEXT",
    )
    await db.commit()
