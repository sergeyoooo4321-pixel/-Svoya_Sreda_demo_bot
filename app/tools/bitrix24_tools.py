"""Реализация всех инструментов агента.

Архитектура: модель НЕ ходит в Bitrix24 напрямую, только через эти tools.
Bitrix24 KB пока опциональна — есть локальный fallback knowledge/faq.json.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings
from app.logger import logger
from app.memory.memory_store import MemoryStore
from app.models.crm import DealPayload
from app.services.bitrix_client import BitrixClient
from app.services.catalog_service import get_product as catalog_get_product
from app.services.catalog_service import load_catalog
from app.services.intent_rules import extract_catalog_entities, normalize_text
from app.services.lead_service import LeadService, build_lead_payload
from app.services.normalization import extract_phone, normalize_phone
from app.storage import repositories as repo


KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent.parent / "knowledge"
FAQ_JSON_PATH = KNOWLEDGE_DIR / "faq.json"


# ---------------- knowledge fallback ----------------

@lru_cache(maxsize=1)
def _load_faq_fallback() -> list[dict[str, Any]]:
    if not FAQ_JSON_PATH.exists():
        return []
    with FAQ_JSON_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------- категории ----------------

_CATEGORY_ALIASES: dict[str, list[str]] = {
    "диваны": ["Диваны"],
    "кресла": ["Кресла"],
    "кровати": ["Кровати"],
    "столы": ["Столы", "Журнальные столики"],
    "комоды": ["Комоды"],
    "шкафы": ["Шкафы"],
    "стеллажи": ["Стеллажи"],
    "тумбы": ["ТВ-тумбы"],
    "прихожие": ["Прихожие"],
}


def _resolve_categories(value: str) -> list[str]:
    if not value:
        return []
    key = normalize_text(value).strip()
    for alias, cats in _CATEGORY_ALIASES.items():
        if alias in key or key in alias:
            return cats
    # точное совпадение с реальной категорией
    return [value] if any(p.category == value for p in load_catalog()) else []


# ---------------- стадии CRM ----------------

_STAGE_ALIASES = {
    "new": "new",
    "новый": "new",
    "consultation": "consultation",
    "консультация": "consultation",
    "waiting": "waiting_decision",
    "waiting_decision": "waiting_decision",
    "ждёт": "waiting_decision",
    "ждет": "waiting_decision",
    "order_created": "order_created",
    "заказ оформлен": "order_created",
    "rejected": "refused",
    "refused": "refused",
    "отказ": "refused",
}


def _resolve_stage(value: Optional[str]) -> Optional[str]:
    """Маппит человеческую/enum-стадию в ключ.

    Пустая строка → None: значит «стадию не трогаем» (важно для update_lead, чтобы
    он не сбрасывал уже выставленную стадию вроде order_created обратно в консультацию).
    """
    if not value or not value.strip():
        return None
    key = value.strip().lower()
    return _STAGE_ALIASES.get(key, "consultation")


# ---------------- ToolRegistry ----------------

@dataclass
class ToolContext:
    """Передаётся в каждый tool — нужен chat_id и общие сервисы."""
    chat_id: int
    bitrix: BitrixClient
    lead_service: LeadService
    memory: MemoryStore


class ToolRegistry:
    """Маршрутизатор tool calls."""

    def __init__(self, context: ToolContext) -> None:
        self.ctx = context
        self._handlers = {
            "search_knowledge": self.search_knowledge,
            "get_product": self.get_product,
            "list_products": self.list_products,
            "create_lead": self.create_lead,
            "update_lead": self.update_lead,
            "create_deal": self.create_deal,
            "call_manager": self.call_manager,
        }

    @property
    def known_tools(self) -> list[str]:
        return list(self._handlers.keys())

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
            return {"error": f"unknown tool: {name}", "known": list(self._handlers.keys())}
        try:
            args = args if isinstance(args, dict) else {}
            result = await handler(**args) if _is_async(handler) else handler(**args)
            logger.info(f"tool {name}: ok | args={list(args.keys())} | result={_result_summary(result)}")
            return result if isinstance(result, dict) else {"result": result}
        except TypeError as exc:
            logger.warning(f"tool {name}: bad args: {exc}")
            return {"error": f"bad arguments: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"tool {name}: {exc!r}")
            return {"error": str(exc)[:300]}

    # ---------------- search_knowledge ----------------

    def search_knowledge(
        self,
        query: str = "",
        intent: str = "unknown",
        limit: int = 3,
        **_: Any,
    ) -> dict[str, Any]:
        faq = _load_faq_fallback()
        if not faq:
            return {"items": [], "note": "knowledge base пуста"}

        norm_query = normalize_text(query)
        items: list[tuple[int, dict[str, Any]]] = []
        for entry in faq:
            score = 0
            if intent and entry.get("intent") == intent:
                score += 50
            for kw in entry.get("keywords", []):
                if normalize_text(kw) in norm_query:
                    score += 15
            title_norm = normalize_text(entry.get("title", ""))
            if title_norm and title_norm in norm_query:
                score += 10
            if score:
                score += int(entry.get("priority", 0)) // 10
                items.append((score, entry))

        items.sort(key=lambda t: t[0], reverse=True)
        picked = [e for _, e in items[: max(1, min(limit, 5))]]

        # Если ничего не нашли по баллам — отдадим топ-1 по интенту.
        if not picked and intent:
            picked = [e for e in faq if e.get("intent") == intent][:1]

        return {
            "items": [
                {
                    "title": e.get("title"),
                    "intent": e.get("intent"),
                    "answer": e.get("answer"),
                    "clarify": e.get("clarify"),
                    "restrictions": e.get("restrictions"),
                    "priority": e.get("priority"),
                }
                for e in picked
            ]
        }

    # ---------------- get_product / list_products ----------------

    def get_product(
        self,
        query: str = "",
        product_name: str = "",
        article: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        if article:
            p = catalog_get_product(article)
            if p:
                return {"product": _serialize_product(p)}

        joint = " ".join(filter(None, [query, product_name]))
        if not joint.strip():
            return {"product": None, "note": "пустой запрос"}

        products = [p.model_dump() for p in load_catalog()]
        entities = extract_catalog_entities(normalize_text(joint), products)
        product_id = entities.get("product_id")
        if product_id:
            p = catalog_get_product(product_id)
            if p:
                serialized = _serialize_product(p)
                if entities.get("color"):
                    serialized["matched_color"] = entities["color"]
                return {"product": serialized}
        return {"product": None, "note": "не нашёл по запросу — уточни товар"}

    def list_products(
        self,
        category: str = "",
        filters: Optional[dict[str, Any]] = None,
        limit: int = 5,
        **_: Any,
    ) -> dict[str, Any]:
        filters = filters or {}
        cats = _resolve_categories(category) if category else []
        products = list(load_catalog())
        if cats:
            products = [p for p in products if p.category in cats]

        max_price = _coerce_price(filters.get("max_price"))
        if max_price is not None:
            products = [p for p in products if p.price <= max_price]

        wanted_color = (filters.get("color") or "").strip().lower()
        if wanted_color:
            products = [
                p for p in products
                if any(wanted_color in c.lower() for c in p.colors)
            ]

        if filters.get("in_stock") is True:
            products = [p for p in products if (p.availability or "").lower().startswith("в наличии")]

        limit = max(1, min(int(limit or 5), 10))
        items = [
            {
                "name": p.title,
                "article": p.id,
                "category": p.category,
                "price": p.price_text,
                "stock": p.availability,
                "short_description": (p.description or "")[:140],
            }
            for p in products[:limit]
        ]
        return {"products": items}

    # ---------------- CRM tools ----------------

    async def create_lead(
        self,
        client_name: str = "",
        phone: str = "",
        product: str = "",
        city: str = "",
        comment: str = "",
        stage: str = "new",
        **_: Any,
    ) -> dict[str, Any]:
        payload = build_lead_payload(
            name=client_name or None,
            phone=normalize_phone(phone) if phone else None,
            product_title=product or None,
            city=city or None,
            bot_comment=comment or None,
            lead_stage_key=_resolve_stage(stage) or "new",
        )
        lead_id = await self.ctx.lead_service.ensure_lead(self.ctx.chat_id, payload=payload)
        if lead_id:
            await self.ctx.memory.set_lead_id(self.ctx.chat_id, lead_id)
            return {"lead_id": lead_id, "status": "created"}
        return {
            "lead_id": None,
            "status": "queued_locally",
            "note": "CRM недоступен — сохранил в очередь, заявка не потеряна",
        }

    async def update_lead(
        self,
        client_name: str = "",
        phone: str = "",
        product: str = "",
        city: str = "",
        comment: str = "",
        stage: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        state = await self.ctx.memory.load_state(self.ctx.chat_id)
        payload = build_lead_payload(
            name=client_name or state.extracted.client_name,
            phone=normalize_phone(phone) if phone else state.extracted.phone,
            product_title=product or state.extracted.product,
            city=city or state.extracted.city,
            bot_comment=comment or None,
            lead_stage_key=_resolve_stage(stage),
        )
        lead_id = await self.ctx.lead_service.ensure_lead(self.ctx.chat_id, payload=payload)
        return {
            "lead_id": lead_id,
            "status": "updated" if lead_id else "queued_locally",
        }

    async def create_deal(
        self,
        client_name: str = "",
        phone: str = "",
        product: str = "",
        color: str = "",
        city: str = "",
        delivery_type: str = "",
        assembly_needed: str = "Не указано",
        expected_delivery_date: str = "",
        comment: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        state = await self.ctx.memory.load_state(self.ctx.chat_id)
        ed = state.extracted

        # Идемпотентность (раздел 15): если сделка по этому чату уже создана — не дублируем.
        if state.deal_id:
            return {"deal_id": state.deal_id, "status": "already_created"}

        # Телефон обязателен (раздел 13 п.4). Берём из аргумента либо из памяти.
        normalized_phone = normalize_phone(phone) if phone else ed.phone
        if not normalized_phone:
            return {
                "deal_id": None,
                "status": "phone_required",
                "note": "Нельзя создавать сделку без телефона клиента.",
            }

        # Недостающие поля добираем из памяти диалога (тест 4: товар/цвет/город уже названы).
        client_name = client_name or ed.client_name or ""
        product = product or ed.product or ""
        color = color or ed.color or ""
        city = city or ed.city or ""
        delivery_type = delivery_type or ed.delivery_type or ""
        if (not assembly_needed or assembly_needed == "Не указано") and ed.assembly_needed:
            assembly_needed = ed.assembly_needed

        s = get_settings()
        # Перед сделкой — гарантируем, что лид существует и в стадии «order_created».
        lead_payload = build_lead_payload(
            name=client_name or None,
            phone=normalized_phone,
            product_title=product or None,
            city=city or None,
            bot_comment=comment or f"Готовая заявка из Telegram. {product}, {color}, {city}.",
            lead_stage_key="order_created",
        )
        lead_id = await self.ctx.lead_service.ensure_lead(self.ctx.chat_id, payload=lead_payload)

        # Цена из каталога: по product_id из памяти → по точному названию → по подстроке.
        opportunity = self._resolve_opportunity(product, ed.product_id)

        # В комментарий сделки добавляем контакт клиента (DealPayload не имеет полей имени/телефона).
        deal_comment = comment or None
        contact_line = ", ".join(filter(None, [client_name, normalized_phone]))
        if contact_line:
            deal_comment = (f"{deal_comment}. " if deal_comment else "") + f"Клиент: {contact_line}."

        deal_payload = DealPayload(
            title=f"Заказ мебели из Telegram — {product or 'товар'}",
            opportunity=opportunity,
            stage_id=s.bitrix_deal_stage_new,
            color=color or None,
            delivery_type=delivery_type or None,
            need_assembly=_assembly_to_bool(assembly_needed),
            delivery_date=expected_delivery_date or None,
            comments=deal_comment,
        )

        # Сохраняем собранные данные в память, чтобы они не потерялись между ходами.
        await self.ctx.memory.update_extracted(self.ctx.chat_id, {
            "client_name": client_name or None,
            "phone": normalized_phone,
            "product": product or None,
            "color": color or None,
            "city": city or None,
            "delivery_type": delivery_type or None,
        })

        if not self.ctx.bitrix.enabled:
            await repo.enqueue_outbox(
                telegram_user_id=self.ctx.chat_id,
                entity_type="deal",
                operation="create",
                payload=deal_payload.model_dump(),
                last_error="Bitrix24 disabled",
            )
            return {"deal_id": None, "status": "queued_locally", "lead_id": lead_id}

        result = await self.ctx.bitrix.create_deal(deal_payload)
        if result.ok and result.entity_id:
            await self.ctx.memory.set_deal_id(self.ctx.chat_id, result.entity_id)
            return {"deal_id": result.entity_id, "status": "created", "lead_id": lead_id}

        await repo.enqueue_outbox(
            telegram_user_id=self.ctx.chat_id,
            entity_type="deal",
            operation="create",
            payload=deal_payload.model_dump(),
            last_error=result.error,
        )
        return {"deal_id": None, "status": "queued_locally", "lead_id": lead_id, "error": result.error}

    async def call_manager(
        self,
        reason: str = "",
        comment: str = "",
        client_name: str = "",
        phone: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        state = await self.ctx.memory.load_state(self.ctx.chat_id)
        normalized_phone = normalize_phone(phone) if phone else state.extracted.phone
        payload = build_lead_payload(
            name=client_name or state.extracted.client_name,
            phone=normalized_phone,
            product_title=state.extracted.product,
            city=state.extracted.city,
            bot_comment=f"[manager_required] {reason}. {comment}".strip(),
            lead_stage_key="consultation",
        )
        lead_id = await self.ctx.lead_service.ensure_lead(self.ctx.chat_id, payload=payload)
        return {
            "status": "manager_required",
            "lead_id": lead_id,
            "reason": reason,
        }

    @staticmethod
    def _resolve_opportunity(product: str, product_id: Optional[str]) -> Optional[int]:
        """Цена товара для сделки: по точному id → по точному названию → по подстроке."""
        catalog = load_catalog()
        if product_id:
            for p in catalog:
                if p.id == product_id:
                    return int(p.price)
        target = (product or "").strip().lower()
        if not target:
            return None
        for p in catalog:  # точное совпадение названия
            if p.title.lower() == target:
                return int(p.price)
        for p in catalog:  # подстрока — наименее надёжный вариант
            if target in p.title.lower():
                return int(p.price)
        return None


# ---------------- helpers ----------------


def _coerce_price(value: Any) -> Optional[int]:
    """Приводит max_price из tool-аргументов (int/float/строка) к int или None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _result_summary(result: Any) -> str:
    """Компактная сводка результата tool для логов (без секретов и больших текстов)."""
    if not isinstance(result, dict):
        return type(result).__name__
    parts: list[str] = []
    for k in ("status", "lead_id", "deal_id"):
        if result.get(k) is not None:
            parts.append(f"{k}={result[k]}")
    if "items" in result:
        parts.append(f"items={len(result.get('items') or [])}")
    if "products" in result:
        parts.append(f"products={len(result.get('products') or [])}")
    if "product" in result:
        prod = result.get("product")
        parts.append(f"product={prod.get('article') if isinstance(prod, dict) else None}")
    if result.get("error"):
        parts.append("error")
    return ", ".join(parts) or "ok"

def _serialize_product(p) -> dict[str, Any]:
    return {
        "name": p.title,
        "article": p.id,
        "category": p.category,
        "price": p.price_text,
        "sizes": p.dimensions,
        "sleeping_place": p.sleeping_place,
        "colors": p.colors,
        "material": p.material,
        "stock": p.availability,
        "description": p.description,
        "manager_recommendation": p.manager_hint,
    }


def _assembly_to_bool(value: str) -> Optional[bool]:
    if not value:
        return None
    v = value.strip().lower()
    if v in {"да", "yes", "true", "1"}:
        return True
    if v in {"нет", "no", "false", "0"}:
        return False
    return None


def _is_async(fn) -> bool:
    import inspect

    return inspect.iscoroutinefunction(fn)
