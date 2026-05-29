"""Схема локальной CRM «Svoya CRM» (раздел т.md §6).

Идемпотентные DDL-операторы: выполняются при старте, повторный запуск безопасен.
Отдельная БД (data/svoya_crm.db), не пересекается со старой bot.db.
"""
from __future__ import annotations

import aiosqlite


SCHEMA_STATEMENTS: tuple[str, ...] = (
    # 6.1 Стадии лидов (канбан)
    """
    CREATE TABLE IF NOT EXISTS lead_stages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        code        TEXT NOT NULL UNIQUE,
        name        TEXT NOT NULL,
        description TEXT,
        sort_order  INTEGER NOT NULL DEFAULT 0,
        is_final    INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # 6.2 Лиды
    """
    CREATE TABLE IF NOT EXISTS leads (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        external_id        TEXT,
        source             TEXT NOT NULL DEFAULT 'telegram',
        telegram_chat_id   INTEGER,
        client_name        TEXT,
        phone              TEXT,
        email              TEXT,
        interested_product TEXT,
        product_article    TEXT,
        city               TEXT,
        delivery_type      TEXT,
        assembly_needed    TEXT,
        bot_comment        TEXT,
        manager_comment    TEXT,
        stage_code         TEXT NOT NULL DEFAULT 'new',
        status             TEXT NOT NULL DEFAULT 'active',
        raw_payload_json   TEXT,
        created_at         TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # 6.3 Сделки
    """
    CREATE TABLE IF NOT EXISTS deals (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id                INTEGER,
        source                 TEXT NOT NULL DEFAULT 'telegram',
        client_name            TEXT,
        phone                  TEXT,
        product_name           TEXT,
        product_article        TEXT,
        color                  TEXT,
        city                   TEXT,
        delivery_type          TEXT,
        assembly_needed        TEXT,
        expected_delivery_date TEXT,
        amount                 INTEGER,
        status                 TEXT NOT NULL DEFAULT 'new_order',
        bot_comment            TEXT,
        manager_comment        TEXT,
        raw_payload_json       TEXT,
        created_at             TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at             TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # 6.4 Товары
    """
    CREATE TABLE IF NOT EXISTS products (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        name                   TEXT NOT NULL,
        slug                   TEXT NOT NULL UNIQUE,
        article                TEXT NOT NULL UNIQUE,
        category               TEXT NOT NULL,
        price                  INTEGER NOT NULL DEFAULT 0,
        price_text             TEXT,
        sizes                  TEXT,
        sleeping_place         TEXT,
        colors_json            TEXT,
        material               TEXT,
        stock                  TEXT,
        delivery_time          TEXT,
        description            TEXT,
        room_json              TEXT,
        image_folder           TEXT,
        manager_recommendation TEXT,
        active                 INTEGER NOT NULL DEFAULT 1,
        created_at             TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at             TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # 6.5 Изображения товаров
    """
    CREATE TABLE IF NOT EXISTS product_images (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id   INTEGER,
        article      TEXT,
        color        TEXT,
        variant_slug TEXT,
        file_path    TEXT,
        alt_text     TEXT,
        sort_order   INTEGER NOT NULL DEFAULT 0,
        active       INTEGER NOT NULL DEFAULT 1,
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # 6.6 База знаний / интенты
    """
    CREATE TABLE IF NOT EXISTS knowledge_items (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        title              TEXT NOT NULL,
        intent             TEXT NOT NULL UNIQUE,
        keywords           TEXT,
        answer             TEXT,
        clarify            TEXT,
        restrictions       TEXT,
        related_crm_fields TEXT,
        priority           INTEGER NOT NULL DEFAULT 0,
        active             INTEGER NOT NULL DEFAULT 1,
        created_at         TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # 6.7 Шаблоны ответов бота
    """
    CREATE TABLE IF NOT EXISTS bot_reply_templates (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        title               TEXT NOT NULL,
        intent              TEXT NOT NULL UNIQUE,
        client_phrases      TEXT,
        bot_reply           TEXT,
        expected_crm_action TEXT,
        expected_stage      TEXT,
        check_comment       TEXT,
        active              INTEGER NOT NULL DEFAULT 1,
        created_at          TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # 6.8 Тестовые диалоги
    """
    CREATE TABLE IF NOT EXISTS test_dialogs (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        title                   TEXT NOT NULL UNIQUE,
        scenario                TEXT,
        client_messages_json    TEXT,
        expected_bot_replies_json TEXT,
        expected_crm_state_json TEXT,
        active                  INTEGER NOT NULL DEFAULT 1,
        created_at              TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # 6.9 Демо-сценарии
    """
    CREATE TABLE IF NOT EXISTS demo_scenarios (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        step_number       INTEGER NOT NULL DEFAULT 0,
        title             TEXT NOT NULL,
        client_action     TEXT,
        bot_reply         TEXT,
        crm_action        TEXT,
        screen_to_show    TEXT,
        presenter_comment TEXT,
        active            INTEGER NOT NULL DEFAULT 1,
        created_at        TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # 6.10 Сообщения чата (память)
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_chat_id INTEGER NOT NULL,
        role             TEXT NOT NULL,   -- user | assistant | tool | system
        content          TEXT NOT NULL,
        metadata_json    TEXT,
        created_at       TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # 6.11 Состояние чата (summary + извлечённые данные)
    """
    CREATE TABLE IF NOT EXISTS chat_states (
        telegram_chat_id    INTEGER PRIMARY KEY,
        summary             TEXT,
        extracted_data_json TEXT,
        lead_id             INTEGER,
        deal_id             INTEGER,
        current_stage       TEXT,
        updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # 6.12 Активности (лента событий)
    """
    CREATE TABLE IF NOT EXISTS activities (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type TEXT NOT NULL,   -- lead | deal | chat | system
        entity_id   TEXT,
        type        TEXT NOT NULL,   -- message|bot_reply|tool_call|crm_update|manager_required|form_submit|webhook_event|error
        title       TEXT,
        content     TEXT,
        payload_json TEXT,
        created_by  TEXT,
        created_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # 6.13 Вебхук-события
    """
    CREATE TABLE IF NOT EXISTS webhook_events (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type    TEXT NOT NULL,
        source        TEXT,
        payload_json  TEXT,
        status        TEXT NOT NULL DEFAULT 'new',  -- new | processed | error
        error_message TEXT,
        created_at    TEXT NOT NULL DEFAULT (datetime('now')),
        processed_at  TEXT
    )
    """,
    # --- индексы ---
    "CREATE INDEX IF NOT EXISTS idx_leads_stage     ON leads(stage_code)",
    "CREATE INDEX IF NOT EXISTS idx_leads_tg        ON leads(telegram_chat_id)",
    "CREATE INDEX IF NOT EXISTS idx_leads_phone     ON leads(phone)",
    "CREATE INDEX IF NOT EXISTS idx_deals_lead      ON deals(lead_id)",
    "CREATE INDEX IF NOT EXISTS idx_deals_status    ON deals(status)",
    "CREATE INDEX IF NOT EXISTS idx_products_cat    ON products(category)",
    "CREATE INDEX IF NOT EXISTS idx_know_intent     ON knowledge_items(intent)",
    "CREATE INDEX IF NOT EXISTS idx_chatmsg_tg      ON chat_messages(telegram_chat_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_act_entity      ON activities(entity_type, entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_act_created     ON activities(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_webhook_status  ON webhook_events(status)",
    # уникальность для идемпотентного seed демо-сценариев (у остальных таблиц ключи уже UNIQUE на колонках)
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_demo_title ON demo_scenarios(title)",
)


async def apply_schema(db: aiosqlite.Connection) -> None:
    for stmt in SCHEMA_STATEMENTS:
        await db.execute(stmt)
    await db.commit()
