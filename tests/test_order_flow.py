"""Тесты на оформление заказа и CRM outbox."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

from app.models.order import OrderDraft


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch) -> Iterator[None]:
    """Каждый тест использует свежую in-memory БД."""
    tmp = tempfile.mkdtemp()
    db_file = Path(tmp) / "test_bot.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:test")
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "")  # CRM выключен — outbox-режим
    # сбрасываем кеш настроек и соединения
    import app.config as cfg
    import app.storage.database as db
    cfg._settings = None
    db._connection = None
    yield
    db._connection = None
    cfg._settings = None
    try:
        db_file.unlink(missing_ok=True)
    except Exception:
        pass


# ----------------- draft -----------------

def test_order_draft_missing_fields() -> None:
    draft = OrderDraft(telegram_user_id=1)
    missing = draft.missing_fields()
    for required in OrderDraft.REQUIRED_FIELDS:
        assert required in missing
    assert not draft.is_ready()


def test_order_not_ready_without_phone() -> None:
    draft = OrderDraft(
        telegram_user_id=1,
        client_name="Сергей",
        product_id="SS-DV-210",
        product_title="Диван «Линия 210»",
        color="Графитовый серый",
        city="Химки",
        delivery_type="До квартиры",
        need_assembly=False,
    )
    assert "phone" in draft.missing_fields()
    assert not draft.is_ready()


def test_order_ready_with_all_fields() -> None:
    draft = OrderDraft(
        telegram_user_id=1,
        client_name="Сергей",
        phone="+79991234567",
        product_id="SS-DV-210",
        product_title="Диван «Линия 210»",
        color="Графитовый серый",
        city="Химки",
        delivery_type="До квартиры",
        need_assembly=False,
    )
    assert draft.is_ready()
    assert draft.missing_fields() == []


# ----------------- outbox -----------------

def test_crm_outbox_when_bitrix_disabled() -> None:
    """При выключенном Bitrix24 лид должен попасть в outbox, заказ — сохраниться локально."""

    async def scenario() -> None:
        from app.services.bitrix_client import BitrixClient
        from app.services.lead_service import LeadService, build_lead_payload
        from app.services.order_service import OrderService
        from app.storage import repositories as repo
        from app.storage.database import init_db

        await init_db()

        bitrix = BitrixClient()
        assert bitrix.enabled is False, "В тесте Bitrix должен быть выключен"

        lead = LeadService(bitrix)
        order = OrderService(bitrix, lead)

        tg_user = 42
        await repo.upsert_user(tg_user, "test", "Сергей", None)
        await repo.ensure_session(tg_user)
        await repo.update_session(
            tg_user,
            selected_product_id="SS-DV-210",
            selected_color="Графитовый серый",
            client_name="Сергей",
            phone="+79991234567",
            city="Химки",
            delivery_type="До квартиры",
            need_assembly=1,
        )

        draft = await order.build_draft(tg_user)
        assert draft.is_ready()

        outcome = await order.submit(tg_user)
        assert outcome["crm_status"] == "crm_sync_failed"
        assert outcome["order_id"] > 0
        assert outcome["deal_id"] is None

        # И лид, и сделка лежат в outbox
        outbox = await repo.get_pending_outbox()
        entities = {row["entity_type"] for row in outbox}
        assert "lead" in entities
        assert "deal" in entities

        await bitrix.close()

    asyncio.run(scenario())


# ----------------- история сообщений -----------------

def test_history_limit() -> None:
    async def scenario() -> None:
        from app.storage import repositories as repo
        from app.storage.database import init_db

        await init_db()
        await repo.upsert_user(1, "u", "u", None)
        for i in range(20):
            await repo.append_message(1, "user", f"msg {i}")
        history = await repo.get_recent_messages(1, 5)
        assert len(history) == 5
        assert history[-1]["content"] == "msg 19"

    asyncio.run(scenario())
