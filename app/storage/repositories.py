"""Репозитории для работы с таблицами SQLite."""
from __future__ import annotations

import json
from typing import Any, Optional

from app.storage.database import get_db


# ------------------- USERS -------------------

async def upsert_user(
    telegram_user_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
    phone: Optional[str] = None,
) -> None:
    db = await get_db()
    await db.execute(
        """
        INSERT INTO telegram_users (telegram_user_id, username, first_name, last_name, phone)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(telegram_user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            phone = COALESCE(excluded.phone, telegram_users.phone),
            updated_at = datetime('now')
        """,
        (telegram_user_id, username, first_name, last_name, phone),
    )
    await db.commit()


async def set_user_phone(telegram_user_id: int, phone: str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE telegram_users SET phone = ?, updated_at = datetime('now') "
        "WHERE telegram_user_id = ?",
        (phone, telegram_user_id),
    )
    await db.commit()


async def count_users() -> int:
    db = await get_db()
    async with db.execute("SELECT COUNT(*) FROM telegram_users") as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


# ------------------- SESSIONS -------------------

async def get_session(telegram_user_id: int) -> Optional[dict[str, Any]]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM conversation_sessions WHERE telegram_user_id = ?",
        (telegram_user_id,),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def ensure_session(telegram_user_id: int) -> dict[str, Any]:
    session = await get_session(telegram_user_id)
    if session is not None:
        return session
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO conversation_sessions (telegram_user_id, current_state) VALUES (?, ?)",
        (telegram_user_id, "idle"),
    )
    await db.commit()
    return (await get_session(telegram_user_id)) or {"telegram_user_id": telegram_user_id}


_SESSION_COLS = {
    "current_state",
    "last_intent",
    "selected_product_id",
    "selected_color",
    "client_name",
    "phone",
    "city",
    "delivery_type",
    "need_assembly",
    "bitrix_lead_id",
    "bitrix_deal_id",
    "summary",
    "extracted_data_json",
}


async def update_session(telegram_user_id: int, **fields: Any) -> None:
    if not fields:
        return
    safe = {k: v for k, v in fields.items() if k in _SESSION_COLS and v is not None}
    if not safe:
        return
    await ensure_session(telegram_user_id)
    db = await get_db()
    set_clause = ", ".join(f"{k} = ?" for k in safe.keys())
    values = list(safe.values()) + [telegram_user_id]
    await db.execute(
        f"UPDATE conversation_sessions SET {set_clause}, updated_at = datetime('now') "
        f"WHERE telegram_user_id = ?",
        values,
    )
    await db.commit()


# ------------------- MESSAGES -------------------

async def append_message(telegram_user_id: int, role: str, content: str) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO messages (telegram_user_id, role, content) VALUES (?, ?, ?)",
        (telegram_user_id, role, content),
    )
    await db.commit()


async def get_recent_messages(telegram_user_id: int, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    db = await get_db()
    async with db.execute(
        """
        SELECT role, content, created_at FROM messages
        WHERE telegram_user_id = ?
        ORDER BY id DESC LIMIT ?
        """,
        (telegram_user_id, limit),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(row) for row in reversed(rows)]


async def count_messages_today() -> int:
    db = await get_db()
    async with db.execute(
        "SELECT COUNT(*) FROM messages WHERE date(created_at) = date('now')"
    ) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


# ------------------- ORDERS -------------------

async def create_order(payload: dict[str, Any]) -> int:
    db = await get_db()
    columns = (
        "telegram_user_id",
        "client_name",
        "phone",
        "product_id",
        "product_title",
        "color",
        "quantity",
        "city",
        "address_or_area",
        "delivery_type",
        "floor",
        "has_elevator",
        "need_assembly",
        "comment",
        "status",
        "bitrix_lead_id",
        "bitrix_deal_id",
    )
    values = tuple(payload.get(col) for col in columns)
    placeholders = ", ".join("?" for _ in columns)
    cur = await db.execute(
        f"INSERT INTO orders ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    await db.commit()
    return int(cur.lastrowid)


async def update_order(order_id: int, **fields: Any) -> None:
    if not fields:
        return
    allowed = {"status", "bitrix_lead_id", "bitrix_deal_id", "comment"}
    safe = {k: v for k, v in fields.items() if k in allowed}
    if not safe:
        return
    db = await get_db()
    set_clause = ", ".join(f"{k} = ?" for k in safe.keys())
    values = list(safe.values()) + [order_id]
    await db.execute(f"UPDATE orders SET {set_clause} WHERE id = ?", values)
    await db.commit()


async def get_recent_orders(limit: int = 5) -> list[dict[str, Any]]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def count_orders() -> int:
    db = await get_db()
    async with db.execute("SELECT COUNT(*) FROM orders") as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def count_orders_with_status(status: str) -> int:
    db = await get_db()
    async with db.execute("SELECT COUNT(*) FROM orders WHERE status = ?", (status,)) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


# ------------------- CRM OUTBOX -------------------

async def enqueue_outbox(
    telegram_user_id: int,
    entity_type: str,
    operation: str,
    payload: dict[str, Any],
    target_id: Optional[str] = None,
    last_error: Optional[str] = None,
) -> int:
    db = await get_db()
    cur = await db.execute(
        """
        INSERT INTO crm_outbox
            (telegram_user_id, entity_type, operation, target_id, payload_json, status, attempts, last_error)
        VALUES (?, ?, ?, ?, ?, 'pending', 0, ?)
        """,
        (
            telegram_user_id,
            entity_type,
            operation,
            target_id,
            json.dumps(payload, ensure_ascii=False),
            last_error,
        ),
    )
    await db.commit()
    return int(cur.lastrowid)


async def get_pending_outbox() -> list[dict[str, Any]]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM crm_outbox WHERE status IN ('pending', 'failed') ORDER BY id ASC"
    ) as cur:
        rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def mark_outbox_synced(outbox_id: int, target_id: Optional[str]) -> None:
    db = await get_db()
    await db.execute(
        """
        UPDATE crm_outbox SET
            status = 'synced',
            target_id = COALESCE(?, target_id),
            last_error = NULL,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (target_id, outbox_id),
    )
    await db.commit()


async def mark_outbox_failed(outbox_id: int, error: str) -> None:
    db = await get_db()
    await db.execute(
        """
        UPDATE crm_outbox SET
            status = 'failed',
            attempts = attempts + 1,
            last_error = ?,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (error[:1000], outbox_id),
    )
    await db.commit()


async def count_outbox_with_status(status: str) -> int:
    db = await get_db()
    async with db.execute("SELECT COUNT(*) FROM crm_outbox WHERE status = ?", (status,)) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0
