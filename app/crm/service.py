"""Svoya CRM — сервисный слой (раздел т.md §2, §12, §13, §19, §23).

Единственная точка доступа к данным CRM: лиды, сделки, товары, база знаний,
активности, вебхуки. Бот, агент, сайт и админка ходят сюда, а не в таблицы напрямую.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from app.crm.db import get_crm_db
from app.logger import logger
from app.services.normalization import normalize_text


# Категории: алиасы из запроса/кнопок → реальные категории каталога.
_CATEGORY_ALIASES: dict[str, list[str]] = {
    "диваны": ["Диваны"], "диван": ["Диваны"],
    "кресла": ["Кресла"], "кресло": ["Кресла"],
    "кровати": ["Кровати"], "кровать": ["Кровати"],
    "столы": ["Столы", "Журнальные столики"], "стол": ["Столы"],
    "комоды": ["Комоды"], "комод": ["Комоды"],
    "шкафы": ["Шкафы"], "шкаф": ["Шкафы"],
    "стеллажи": ["Стеллажи"], "стеллаж": ["Стеллажи"],
    "тумбы": ["ТВ-тумбы"], "тумба": ["ТВ-тумбы"],
    "прихожие": ["Прихожие"], "прихожая": ["Прихожие"],
}


def _row_to_dict(row) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _product_out(row: dict[str, Any]) -> dict[str, Any]:
    """Карточка товара в формате §13.2."""
    try:
        colors = json.loads(row.get("colors_json") or "[]")
    except (ValueError, TypeError):
        colors = []
    return {
        "name": row.get("name"),
        "article": row.get("article"),
        "category": row.get("category"),
        "price": row.get("price_text") or (f"{row.get('price')} ₽" if row.get("price") else None),
        "price_value": row.get("price"),
        "sizes": row.get("sizes"),
        "sleeping_place": row.get("sleeping_place"),
        "colors": colors,
        "material": row.get("material"),
        "stock": row.get("stock"),
        "delivery_time": row.get("delivery_time"),
        "description": row.get("description"),
        "manager_recommendation": row.get("manager_recommendation"),
        "image_folder": row.get("image_folder"),
    }


class SvoyaCRM:
    """Локальная CRM. Методы соответствуют tools §2/§13."""

    # ============== ЗНАНИЯ (§13.1, §23) ==============

    async def search_knowledge(self, query: str = "", intent: str = "unknown", limit: int = 3) -> dict[str, Any]:
        db = await get_crm_db()
        async with db.execute("SELECT * FROM knowledge_items WHERE active = 1") as cur:
            items = [_row_to_dict(r) for r in await cur.fetchall()]
        if not items:
            return {"items": [], "note": "база знаний пуста"}

        norm_query = normalize_text(query)
        scored: list[tuple[int, dict[str, Any]]] = []
        for it in items:
            score = 0
            if intent and it.get("intent") == intent:
                score += 50
            for kw in (it.get("keywords") or "").split(","):
                kw = normalize_text(kw)
                if kw and kw in norm_query:
                    score += 15
            title_norm = normalize_text(it.get("title", ""))
            if title_norm and title_norm in norm_query:
                score += 10
            if score:
                score += int(it.get("priority") or 0) // 10
                scored.append((score, it))

        scored.sort(key=lambda t: t[0], reverse=True)
        picked = [it for _, it in scored[: max(1, min(int(limit or 3), 5))]]
        if not picked and intent:
            picked = [it for it in items if it.get("intent") == intent][:1]

        return {"items": [
            {
                "title": it.get("title"), "intent": it.get("intent"), "answer": it.get("answer"),
                "clarify": it.get("clarify"), "restrictions": it.get("restrictions"),
                "priority": it.get("priority"),
            } for it in picked
        ]}

    # ============== ТОВАРЫ (§13.2, §13.3) ==============

    async def _all_products(self, only_active: bool = True) -> list[dict[str, Any]]:
        db = await get_crm_db()
        sql = "SELECT * FROM products" + (" WHERE active = 1" if only_active else "")
        async with db.execute(sql + " ORDER BY id") as cur:
            return [_row_to_dict(r) for r in await cur.fetchall()]

    async def get_product(self, query: str = "", product_name: str = "", article: str = "", **_: Any) -> dict[str, Any]:
        products = await self._all_products()
        if article:
            for p in products:
                if (p.get("article") or "").lower() == article.strip().lower():
                    return {"product": _product_out(p)}

        joint = normalize_text(" ".join(filter(None, [query, product_name])))
        if not joint:
            return {"product": None, "note": "пустой запрос"}

        # подбираем цвет, упомянутый в запросе
        matched_color: Optional[str] = None
        best: Optional[dict[str, Any]] = None
        best_score = 0
        for p in products:
            score = 0
            title_norm = normalize_text(p.get("name", ""))
            slug = (p.get("slug") or "").lower()
            art = (p.get("article") or "").lower()
            if title_norm and title_norm in joint:
                score += 5
            # отдельные значимые слова названия
            for word in title_norm.split():
                if len(word) >= 4 and word in joint:
                    score += 2
            if slug and slug in joint:
                score += 4
            if art and art in joint:
                score += 6
            try:
                colors = json.loads(p.get("colors_json") or "[]")
            except (ValueError, TypeError):
                colors = []
            for c in colors:
                cn = normalize_text(c)
                if cn and (cn in joint or any(part in joint for part in cn.split() if len(part) >= 4)):
                    score += 3
                    if best is None or score > best_score:
                        matched_color = c
            # категория по алиасу
            for alias, cats in _CATEGORY_ALIASES.items():
                if alias in joint and p.get("category") in cats:
                    score += 2
            if score > best_score:
                best_score = score
                best = p
                # зафиксировать matched_color только если цвет реально встретился у этого товара
                mc = None
                for c in colors:
                    cn = normalize_text(c)
                    if cn and (cn in joint or any(part in joint for part in cn.split() if len(part) >= 4)):
                        mc = c
                        break
                matched_color = mc

        if best is None or best_score == 0:
            return {"product": None, "note": "не нашёл по запросу — уточни товар"}
        out = _product_out(best)
        if matched_color:
            out["matched_color"] = matched_color
        return {"product": out}

    async def list_products(self, category: str = "", filters: Optional[dict[str, Any]] = None,
                            limit: int = 5, **_: Any) -> dict[str, Any]:
        filters = filters or {}
        products = await self._all_products()

        cats: list[str] = []
        if category:
            key = normalize_text(category)
            for alias, mapped in _CATEGORY_ALIASES.items():
                if alias in key or key in alias:
                    cats = mapped
                    break
            if not cats and any(p.get("category") == category for p in products):
                cats = [category]
        if cats:
            products = [p for p in products if p.get("category") in cats]

        max_price = _coerce_price(filters.get("max_price"))
        if max_price is not None:
            products = [p for p in products if int(p.get("price") or 0) <= max_price]

        color = (filters.get("color") or "").strip().lower()
        if color:
            def has_color(p):
                try:
                    return any(color in str(c).lower() for c in json.loads(p.get("colors_json") or "[]"))
                except (ValueError, TypeError):
                    return False
            products = [p for p in products if has_color(p)]

        if filters.get("in_stock") is True:
            products = [p for p in products if (p.get("stock") or "").lower().startswith("в наличии")]

        limit = max(1, min(int(limit or 5), 10))
        return {"products": [
            {
                "name": p.get("name"), "article": p.get("article"), "category": p.get("category"),
                "price": p.get("price_text"), "stock": p.get("stock"),
                "short_description": (p.get("description") or "")[:140],
            } for p in products[:limit]
        ]}

    # ============== ЛИДЫ (§12.1, §13.4, §13.5) ==============

    async def get_lead(self, lead_id: int) -> Optional[dict[str, Any]]:
        db = await get_crm_db()
        async with db.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)) as cur:
            return _row_to_dict(await cur.fetchone()) or None

    async def get_active_lead_by_chat(self, telegram_chat_id: int) -> Optional[dict[str, Any]]:
        db = await get_crm_db()
        async with db.execute(
            "SELECT * FROM leads WHERE telegram_chat_id = ? AND stage_code != 'rejected' "
            "ORDER BY id DESC LIMIT 1",
            (telegram_chat_id,),
        ) as cur:
            return _row_to_dict(await cur.fetchone()) or None

    async def list_leads(self, stage_code: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        db = await get_crm_db()
        if stage_code:
            sql = "SELECT * FROM leads WHERE stage_code = ? ORDER BY updated_at DESC LIMIT ?"
            args = (stage_code, limit)
        else:
            sql = "SELECT * FROM leads ORDER BY updated_at DESC LIMIT ?"
            args = (limit,)
        async with db.execute(sql, args) as cur:
            return [_row_to_dict(r) for r in await cur.fetchall()]

    _LEAD_FIELDS = {
        "external_id", "source", "telegram_chat_id", "client_name", "phone", "email",
        "interested_product", "product_article", "city", "delivery_type", "assembly_needed",
        "bot_comment", "manager_comment", "stage_code", "status", "raw_payload_json",
    }

    async def create_lead(self, **fields: Any) -> dict[str, Any]:
        data = {k: v for k, v in fields.items() if k in self._LEAD_FIELDS and v is not None}
        data.setdefault("source", "telegram")
        data.setdefault("stage_code", "new")
        cols = list(data.keys())
        db = await get_crm_db()
        cur = await db.execute(
            f"INSERT INTO leads ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [data[c] for c in cols],
        )
        await db.commit()
        lead_id = int(cur.lastrowid)
        await self.create_activity("lead", lead_id, "crm_update", "Лид создан",
                                   data.get("bot_comment") or "", data, created_by=data.get("source", "system"))
        await self.emit_webhook_event("lead.created", data.get("source", "telegram"),
                                      {"lead_id": lead_id, "client_name": data.get("client_name"),
                                       "product": data.get("interested_product")})
        logger.info(f"CRM: лид #{lead_id} создан (source={data.get('source')}, stage={data.get('stage_code')})")
        return {"lead_id": lead_id, "status": "created"}

    async def update_lead(self, lead_id: int, **fields: Any) -> dict[str, Any]:
        data = {k: v for k, v in fields.items() if k in self._LEAD_FIELDS and v is not None
                and not (isinstance(v, str) and not v.strip())}
        if not data:
            return {"lead_id": lead_id, "status": "noop"}
        cols = list(data.keys())
        db = await get_crm_db()
        await db.execute(
            f"UPDATE leads SET {', '.join(f'{c}=?' for c in cols)}, updated_at=datetime('now') WHERE id=?",
            [data[c] for c in cols] + [lead_id],
        )
        await db.commit()
        await self.emit_webhook_event("lead.updated", data.get("source", "system"),
                                      {"lead_id": lead_id, **{k: data[k] for k in cols}})
        return {"lead_id": lead_id, "status": "updated"}

    async def upsert_lead_for_chat(self, telegram_chat_id: int, *, source: str = "telegram",
                                   stage_code: Optional[str] = None, **fields: Any) -> int:
        """Один активный лид на чат: создать или обновить (идемпотентность §25)."""
        existing = await self.get_active_lead_by_chat(telegram_chat_id)
        if existing:
            patch = dict(fields)
            if stage_code:
                patch["stage_code"] = stage_code
            await self.update_lead(int(existing["id"]), **patch)
            return int(existing["id"])
        res = await self.create_lead(telegram_chat_id=telegram_chat_id, source=source,
                                     stage_code=stage_code or "new", **fields)
        return int(res["lead_id"])

    async def move_lead_stage(self, lead_id: int, stage_code: str) -> dict[str, Any]:
        db = await get_crm_db()
        await db.execute("UPDATE leads SET stage_code=?, updated_at=datetime('now') WHERE id=?",
                         (stage_code, lead_id))
        await db.commit()
        await self.create_activity("lead", lead_id, "crm_update", f"Стадия → {stage_code}", "", {"stage_code": stage_code})
        await self.emit_webhook_event("lead.updated", "system", {"lead_id": lead_id, "stage_code": stage_code})
        return {"lead_id": lead_id, "stage_code": stage_code, "status": "moved"}

    async def add_comment(self, entity_type: str, entity_id: int, text: str, author: str = "manager") -> dict[str, Any]:
        field = "manager_comment" if author != "bot" else "bot_comment"
        db = await get_crm_db()
        table = "leads" if entity_type == "lead" else "deals"
        await db.execute(f"UPDATE {table} SET {field}=?, updated_at=datetime('now') WHERE id=?", (text, entity_id))
        await db.commit()
        await self.create_activity(entity_type, entity_id, "message", "Комментарий", text, {"author": author}, created_by=author)
        return {"status": "ok"}

    # ============== СДЕЛКИ (§12.2, §13.6) ==============

    _DEAL_FIELDS = {
        "lead_id", "source", "client_name", "phone", "product_name", "product_article", "color",
        "city", "delivery_type", "assembly_needed", "expected_delivery_date", "amount", "status",
        "bot_comment", "manager_comment", "raw_payload_json",
    }

    async def get_deal(self, deal_id: int) -> Optional[dict[str, Any]]:
        db = await get_crm_db()
        async with db.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)) as cur:
            return _row_to_dict(await cur.fetchone()) or None

    async def list_deals(self, limit: int = 100) -> list[dict[str, Any]]:
        db = await get_crm_db()
        async with db.execute("SELECT * FROM deals ORDER BY id DESC LIMIT ?", (limit,)) as cur:
            return [_row_to_dict(r) for r in await cur.fetchall()]

    async def create_deal(self, **fields: Any) -> dict[str, Any]:
        # §13.6: не создавать без телефона
        if not (fields.get("phone") or "").strip():
            return {"deal_id": None, "status": "phone_required", "note": "Нельзя создавать сделку без телефона."}
        data = {k: v for k, v in fields.items() if k in self._DEAL_FIELDS and v is not None}
        data.setdefault("source", "telegram")
        data.setdefault("status", "new_order")
        cols = list(data.keys())
        db = await get_crm_db()
        cur = await db.execute(
            f"INSERT INTO deals ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [data[c] for c in cols],
        )
        await db.commit()
        deal_id = int(cur.lastrowid)
        if data.get("lead_id"):
            await self.move_lead_stage(int(data["lead_id"]), "order_created")
        await self.create_activity("deal", deal_id, "crm_update", "Сделка создана",
                                   data.get("bot_comment") or "", data, created_by=data.get("source", "system"))
        await self.emit_webhook_event("deal.created", data.get("source", "telegram"),
                                      {"deal_id": deal_id, "lead_id": data.get("lead_id"),
                                       "product": data.get("product_name"), "amount": data.get("amount")})
        logger.info(f"CRM: сделка #{deal_id} создана (lead={data.get('lead_id')})")
        return {"deal_id": deal_id, "status": "created", "lead_id": data.get("lead_id")}

    async def update_deal(self, deal_id: int, **fields: Any) -> dict[str, Any]:
        data = {k: v for k, v in fields.items() if k in self._DEAL_FIELDS and v is not None}
        if not data:
            return {"deal_id": deal_id, "status": "noop"}
        cols = list(data.keys())
        db = await get_crm_db()
        await db.execute(
            f"UPDATE deals SET {', '.join(f'{c}=?' for c in cols)}, updated_at=datetime('now') WHERE id=?",
            [data[c] for c in cols] + [deal_id],
        )
        await db.commit()
        return {"deal_id": deal_id, "status": "updated"}

    # ============== МЕНЕДЖЕР (§13.7) ==============

    async def call_manager(self, telegram_chat_id: Optional[int] = None, lead_id: Optional[int] = None,
                           reason: str = "", comment: str = "", client_name: str = "",
                           phone: str = "", **_: Any) -> dict[str, Any]:
        if lead_id is None and telegram_chat_id is not None:
            lead_id = await self.upsert_lead_for_chat(
                telegram_chat_id, stage_code="consultation",
                client_name=client_name or None, phone=phone or None,
                manager_comment=f"[manager_required] {reason}. {comment}".strip(),
            )
        elif lead_id is not None:
            await self.update_lead(int(lead_id), manager_comment=f"[manager_required] {reason}. {comment}".strip())
        await self.create_activity("lead", lead_id, "manager_required",
                                   "Нужен менеджер", f"{reason}. {comment}".strip(),
                                   {"reason": reason}, created_by="bot")
        await self.emit_webhook_event("manager.required", "telegram",
                                      {"lead_id": lead_id, "reason": reason, "phone": phone or None})
        return {"status": "manager_required", "lead_id": lead_id, "reason": reason}

    # ============== АКТИВНОСТИ (§12.5) ==============

    async def create_activity(self, entity_type: str, entity_id: Any, type: str, title: str = "",
                              content: str = "", payload: Optional[dict] = None,
                              created_by: str = "system") -> int:
        db = await get_crm_db()
        cur = await db.execute(
            "INSERT INTO activities (entity_type, entity_id, type, title, content, payload_json, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (entity_type, str(entity_id) if entity_id is not None else None, type, title, content,
             json.dumps(payload or {}, ensure_ascii=False), created_by),
        )
        await db.commit()
        return int(cur.lastrowid)

    async def list_activities(self, limit: int = 100, entity_type: Optional[str] = None,
                              entity_id: Optional[str] = None) -> list[dict[str, Any]]:
        db = await get_crm_db()
        if entity_type and entity_id is not None:
            sql = "SELECT * FROM activities WHERE entity_type=? AND entity_id=? ORDER BY id DESC LIMIT ?"
            args = (entity_type, str(entity_id), limit)
        else:
            sql = "SELECT * FROM activities ORDER BY id DESC LIMIT ?"
            args = (limit,)
        async with db.execute(sql, args) as cur:
            return [_row_to_dict(r) for r in await cur.fetchall()]

    # ============== ВЕБХУКИ (§12.6, §19) ==============

    async def emit_webhook_event(self, event_type: str, source: str, payload: dict[str, Any]) -> int:
        db = await get_crm_db()
        cur = await db.execute(
            "INSERT INTO webhook_events (event_type, source, payload_json, status) VALUES (?, ?, ?, 'new')",
            (event_type, source, json.dumps(payload, ensure_ascii=False)),
        )
        await db.commit()
        return int(cur.lastrowid)

    async def list_webhook_events(self, limit: int = 100) -> list[dict[str, Any]]:
        db = await get_crm_db()
        async with db.execute("SELECT * FROM webhook_events ORDER BY id DESC LIMIT ?", (limit,)) as cur:
            return [_row_to_dict(r) for r in await cur.fetchall()]

    async def receive_webhook(self, event_type: str, source: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Принять внешний вебхук (сайт/Telegram/тест), записать событие, при необходимости создать лид."""
        event_id = await self.emit_webhook_event(event_type, source, payload)
        result: dict[str, Any] = {"event_id": event_id, "status": "received"}
        if event_type in {"site.lead_submitted", "site_lead"} or source == "website":
            res = await self.create_lead(
                source="website",
                stage_code=payload.get("stage_code") or "new",
                client_name=payload.get("client_name") or payload.get("name"),
                phone=payload.get("phone"),
                interested_product=payload.get("interested_product") or payload.get("product"),
                product_article=payload.get("product_article"),
                city=payload.get("city"),
                bot_comment=payload.get("bot_comment") or payload.get("comment"),
                raw_payload_json=json.dumps(payload, ensure_ascii=False),
            )
            result["lead_id"] = res.get("lead_id")
        db = await get_crm_db()
        await db.execute("UPDATE webhook_events SET status='processed', processed_at=datetime('now') WHERE id=?",
                         (event_id,))
        await db.commit()
        return result

    # ============== метрики для дашборда ==============

    async def dashboard_counts(self) -> dict[str, Any]:
        db = await get_crm_db()
        out: dict[str, Any] = {}
        async with db.execute("SELECT stage_code, COUNT(*) c FROM leads GROUP BY stage_code") as cur:
            by_stage = {r[0]: r[1] for r in await cur.fetchall()}
        out["leads_total"] = sum(by_stage.values())
        for code in ("new", "consultation", "waiting", "order_created", "rejected"):
            out[f"leads_{code}"] = by_stage.get(code, 0)
        async with db.execute("SELECT COUNT(*) FROM deals") as cur:
            out["deals_total"] = (await cur.fetchone())[0]
        return out


def _coerce_price(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


_crm: Optional[SvoyaCRM] = None


def get_crm() -> SvoyaCRM:
    global _crm
    if _crm is None:
        _crm = SvoyaCRM()
    return _crm
