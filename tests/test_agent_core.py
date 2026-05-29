"""Тесты Agent Core поверх локальной Svoya CRM.

Ollama мокается скриптованной последовательностью ответов; CRM — реальная (временная SQLite).
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, Iterator

import pytest


@pytest.fixture(autouse=True)
def _isolated_crm(monkeypatch) -> Iterator[None]:
    tmp = tempfile.mkdtemp()
    db_file = Path(tmp) / "test_svoya_crm.db"
    monkeypatch.setenv("SVOYA_CRM_DATABASE_URL", f"file:{db_file.as_posix()}")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:test")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "5")
    monkeypatch.setenv("MEMORY_LAST_MESSAGES", "50")

    import app.config as cfg
    import app.crm.db as crmdb
    import app.crm.service as crmsvc
    import app.services.catalog_service as catsvc
    cfg._settings = None
    crmdb._connection = None
    crmsvc._crm = None
    catsvc.load_catalog.cache_clear()
    yield
    crmdb._connection = None
    crmsvc._crm = None
    cfg._settings = None
    catsvc.load_catalog.cache_clear()
    try:
        db_file.unlink(missing_ok=True)
    except Exception:
        pass


def _run(coro):
    return asyncio.run(coro)


class FakeOllama:
    def __init__(self, scripted: list[dict[str, Any]]) -> None:
        self.scripted = scripted
        self.calls = 0

    async def chat_with_tools(self, messages, tools, temperature=None):
        if self.calls >= len(self.scripted):
            return {"role": "assistant", "content": "Готово.", "tool_calls": []}
        item = self.scripted[self.calls]
        self.calls += 1
        return item


async def _build_agent(fake_ollama):
    from app.agent.agent_core import AgentCore
    from app.crm.db import init_crm_db
    from app.crm.memory import MemoryStore
    from app.crm.seed import seed
    from app.crm.service import get_crm
    from app.tools.crm_tools import ToolContext, ToolRegistry

    await init_crm_db()
    await seed()
    crm = get_crm()
    memory = MemoryStore()
    agent = AgentCore(
        ollama=fake_ollama, memory=memory,
        tools_factory=lambda chat_id: ToolRegistry(ToolContext(chat_id=chat_id, crm=crm, memory=memory)),
    )
    return agent, memory, crm


# ---------------- сценарии ----------------

def test_agent_uses_search_knowledge_for_delivery() -> None:
    async def scenario():
        scripted = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"name": "search_knowledge",
                             "arguments": {"query": "сколько доставка", "intent": "delivery", "limit": 3}}]},
            {"role": "assistant", "content": "Доставим за 1-3 дня по Москве.", "tool_calls": []},
        ]
        agent, memory, _ = await _build_agent(FakeOllama(scripted))
        result = await agent.handle(chat_id=111, user_text="сколько доставка?")
        assert "search_knowledge" in [a["tool"] for a in result.actions]
        assert "Доставим" in result.reply
    _run(scenario())


def test_agent_refuses_deal_without_phone() -> None:
    async def scenario():
        scripted = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"name": "create_deal", "arguments": {
                 "client_name": "Андрей", "phone": "", "product": "Диван «Линия 210»",
                 "color": "Графитовый серый", "city": "Химки", "delivery_type": "До квартиры"}}]},
            {"role": "assistant", "content": "Подскажите номер телефона.", "tool_calls": []},
        ]
        agent, _, _ = await _build_agent(FakeOllama(scripted))
        result = await agent.handle(chat_id=222, user_text="оформляйте")
        deal = next(a for a in result.actions if a["tool"] == "create_deal")
        assert deal["result"]["status"] == "phone_required"
        assert deal["result"]["deal_id"] is None
    _run(scenario())


def test_agent_extracts_phone_from_user_text() -> None:
    async def scenario():
        agent, memory, _ = await _build_agent(FakeOllama([{"role": "assistant", "content": "Записал.", "tool_calls": []}]))
        await agent.handle(chat_id=333, user_text="Андрей +7 999 123 45 67")
        state = await memory.load_state(333)
        assert state.extracted.phone == "+79991234567"
    _run(scenario())


def test_agent_remembers_product_from_native_tool() -> None:
    """T2: native get_product → товар/цвет в памяти, даже если финал — обычный текст."""
    async def scenario():
        scripted = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"name": "get_product", "arguments": {"query": "графитовый диван"}}]},
            {"role": "assistant", "content": "Это диван «Линия 210», графитовый.", "tool_calls": []},
        ]
        agent, memory, _ = await _build_agent(FakeOllama(scripted))
        await agent.handle(chat_id=444, user_text="хочу графитовый диван")
        state = await memory.load_state(444)
        assert state.extracted.product_article == "SS-DV-210"
        assert state.extracted.color == "Графитовый серый"
    _run(scenario())


def test_agent_call_manager_marks_manager_required() -> None:
    async def scenario():
        scripted = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"name": "call_manager", "arguments": {"reason": "Клиент просит человека"}}]},
            {"role": "assistant", "content": "Передам менеджеру.", "tool_calls": []},
        ]
        agent, _, crm = await _build_agent(FakeOllama(scripted))
        result = await agent.handle(chat_id=555, user_text="позови менеджера")
        assert result.manager_required is True
        assert any(a["tool"] == "call_manager" for a in result.actions)
    _run(scenario())


def test_agent_tool_loop_capped() -> None:
    async def scenario():
        loop_call = {"role": "assistant", "content": "",
                     "tool_calls": [{"name": "search_knowledge", "arguments": {"query": "x", "intent": "unknown"}}]}
        agent, _, _ = await _build_agent(FakeOllama([loop_call] * 10))
        result = await agent.handle(chat_id=666, user_text="зацикливай")
        assert result.tool_calls_made <= 5
        assert result.used_fallback is True
    _run(scenario())


def test_agent_ensures_consultation_lead_on_plain_reply() -> None:
    """§22: первое осмысленное обращение становится лидом (Консультация), даже без CRM-tool от модели."""
    async def scenario():
        agent, _, crm = await _build_agent(FakeOllama([{"role": "assistant", "content": "Здравствуйте!", "tool_calls": []}]))
        await agent.handle(chat_id=777, user_text="диван есть?")
        lead = await crm.get_active_lead_by_chat(777)
        assert lead is not None
        assert lead["stage_code"] == "consultation"
    _run(scenario())


def test_agent_creates_deal_and_moves_stage() -> None:
    """T3/T5-оформление: create_deal с телефоном → сделка, лид order_created, deal_id в памяти."""
    async def scenario():
        scripted = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"name": "create_deal", "arguments": {
                 "client_name": "Андрей", "phone": "89991234567", "product": "Диван «Линия 210»",
                 "color": "Графитовый серый", "city": "Химки", "delivery_type": "До квартиры"}}]},
            {"role": "assistant", "content": "Заявка оформлена!", "tool_calls": []},
        ]
        agent, memory, crm = await _build_agent(FakeOllama(scripted))
        result = await agent.handle(chat_id=888, user_text="оформляем")
        deal = next(a for a in result.actions if a["tool"] == "create_deal")
        assert deal["result"]["status"] == "created"
        lead = await crm.get_active_lead_by_chat(888)
        assert lead["stage_code"] == "order_created"
        state = await memory.load_state(888)
        assert state.deal_id is not None
    _run(scenario())


def test_waiting_phrase_sets_waiting_stage() -> None:
    """T4: «я подумаю покажу жене» → лид в стадии Ждёт решения."""
    async def scenario():
        agent, _, crm = await _build_agent(FakeOllama([{"role": "assistant", "content": "Хорошо, подумайте.", "tool_calls": []}]))
        await agent.handle(chat_id=999, user_text="я подумаю покажу жене")
        lead = await crm.get_active_lead_by_chat(999)
        assert lead is not None and lead["stage_code"] == "waiting"
    _run(scenario())


# ---------------- tools / CRM sanity ----------------

def test_search_knowledge_returns_delivery_item() -> None:
    async def scenario():
        from app.crm.db import init_crm_db
        from app.crm.seed import seed
        from app.crm.service import get_crm
        await init_crm_db(); await seed()
        res = await get_crm().search_knowledge("сколько доставка в химки", "delivery", 3)
        assert res["items"] and any(i["intent"] == "delivery" for i in res["items"])
    _run(scenario())


def test_get_product_finds_graphite_sofa() -> None:
    async def scenario():
        from app.crm.db import init_crm_db
        from app.crm.seed import seed
        from app.crm.service import get_crm
        await init_crm_db(); await seed()
        res = await get_crm().get_product(query="графитовый диван")
        assert res["product"] and res["product"]["article"] == "SS-DV-210"
        assert res["product"].get("matched_color") == "Графитовый серый"
    _run(scenario())


def test_list_products_filters_category_and_stock() -> None:
    async def scenario():
        from app.crm.db import init_crm_db
        from app.crm.seed import seed
        from app.crm.service import get_crm
        await init_crm_db(); await seed()
        res = await get_crm().list_products("диваны", {"in_stock": True}, 5)
        assert res["products"] and all(p["category"] == "Диваны" for p in res["products"])
    _run(scenario())


def test_site_lead_webhook_creates_lead() -> None:
    """§18/§19: заявка с сайта через webhook → лид source=website + событие."""
    async def scenario():
        from app.crm.db import init_crm_db
        from app.crm.seed import seed
        from app.crm.service import get_crm
        await init_crm_db(); await seed()
        crm = get_crm()
        res = await crm.receive_webhook("site.lead_submitted", "website",
                                        {"client_name": "Сайт", "phone": "+79990001122", "product": "Диван «Линия 210»", "city": "Москва"})
        assert res.get("lead_id")
        lead = await crm.get_lead(int(res["lead_id"]))
        assert lead["source"] == "website" and lead["client_name"] == "Сайт"
    _run(scenario())


def test_entities_city_delivery_persist() -> None:
    """§24: город и формат доставки извлекаются из текста и сохраняются в память."""
    async def scenario():
        agent, memory, _ = await _build_agent(FakeOllama([{"role": "assistant", "content": "Ок", "tool_calls": []}]))
        await agent.handle(chat_id=1201, user_text="доставка в химки до квартиры")
        st = await memory.load_state(1201)
        assert st.extracted.city == "Химки"
        assert st.extracted.delivery_type == "До квартиры"
    _run(scenario())


def test_auto_close_deal_on_order_intent() -> None:
    """§13.4: товар (память) + телефон + интент заказа → сделка создаётся детерминированно."""
    async def scenario():
        scripted = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"name": "get_product", "arguments": {"query": "графитовый диван"}}]},
            {"role": "assistant", "content": "Это «Линия 210».", "tool_calls": []},
            {"role": "assistant", "content": "Принял.", "tool_calls": []},
        ]
        agent, memory, crm = await _build_agent(FakeOllama(scripted))
        await agent.handle(1202, "хочу графитовый диван оформить")  # товар + интент, без телефона
        r2 = await agent.handle(1202, "Андрей, 89991234567, до квартиры")  # телефон → авто-закрытие
        assert any(a["tool"] == "create_deal" and a["result"].get("status") == "created" for a in r2.actions)
        lead = await crm.get_active_lead_by_chat(1202)
        assert lead["stage_code"] == "order_created"
        st = await memory.load_state(1202)
        assert st.deal_id is not None
    _run(scenario())


def test_catalog_loads_from_crm_db() -> None:
    """Каталог-кнопки читают товары из локальной CRM, а не из catalog.json."""
    async def prep():
        from app.crm.db import init_crm_db
        from app.crm.seed import seed
        await init_crm_db()
        await seed()
    _run(prep())
    import app.services.catalog_service as catsvc
    catsvc.load_catalog.cache_clear()
    products = catsvc.load_catalog()
    assert any(p.id == "SS-DV-210" for p in products)
    p = next(p for p in products if p.id == "SS-DV-210")
    assert p.image_path and p.image_path.startswith("фотки товара/")


def test_memory_extracted_roundtrip() -> None:
    from app.crm.memory import ExtractedData
    ed = ExtractedData(client_name="Андрей", phone="+79991234567", city="Химки", product_article="SS-DV-210")
    restored = ExtractedData.from_json(ed.to_json())
    assert restored.client_name == "Андрей"
    assert restored.product_article == "SS-DV-210"
    assert restored.product_id == "SS-DV-210"  # синхронизируется с article
