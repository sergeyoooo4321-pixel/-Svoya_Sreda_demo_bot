"""REST API локальной CRM (раздел т.md §12). Базовый путь /api/local-crm."""
from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from app.crm.service import get_crm


routes = web.RouteTableDef()


def _json(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False))


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# ---------------- Leads (§12.1) ----------------

@routes.get("/api/local-crm/leads")
async def list_leads(request: web.Request) -> web.Response:
    stage = request.query.get("stage")
    limit = int(request.query.get("limit", "100"))
    return _json({"leads": await get_crm().list_leads(stage_code=stage, limit=limit)})


@routes.post("/api/local-crm/leads")
async def create_lead(request: web.Request) -> web.Response:
    body = await _body(request)
    body.setdefault("source", "api")
    return _json(await get_crm().create_lead(**body), status=201)


@routes.get("/api/local-crm/leads/{id}")
async def get_lead(request: web.Request) -> web.Response:
    lead = await get_crm().get_lead(int(request.match_info["id"]))
    return _json(lead) if lead else _json({"error": "not found"}, status=404)


@routes.patch("/api/local-crm/leads/{id}")
async def patch_lead(request: web.Request) -> web.Response:
    return _json(await get_crm().update_lead(int(request.match_info["id"]), **await _body(request)))


@routes.post("/api/local-crm/leads/{id}/move-stage")
async def move_stage(request: web.Request) -> web.Response:
    body = await _body(request)
    stage = body.get("stage_code") or body.get("stage")
    if not stage:
        return _json({"error": "stage_code required"}, status=400)
    return _json(await get_crm().move_lead_stage(int(request.match_info["id"]), stage))


@routes.post("/api/local-crm/leads/{id}/comments")
async def lead_comment(request: web.Request) -> web.Response:
    body = await _body(request)
    return _json(await get_crm().add_comment("lead", int(request.match_info["id"]),
                                             body.get("text", ""), body.get("author", "manager")))


# ---------------- Deals (§12.2) ----------------

@routes.get("/api/local-crm/deals")
async def list_deals(request: web.Request) -> web.Response:
    return _json({"deals": await get_crm().list_deals(int(request.query.get("limit", "100")))})


@routes.post("/api/local-crm/deals")
async def create_deal(request: web.Request) -> web.Response:
    body = await _body(request)
    body.setdefault("source", "api")
    res = await get_crm().create_deal(**body)
    return _json(res, status=201 if res.get("status") == "created" else 400)


@routes.get("/api/local-crm/deals/{id}")
async def get_deal(request: web.Request) -> web.Response:
    deal = await get_crm().get_deal(int(request.match_info["id"]))
    return _json(deal) if deal else _json({"error": "not found"}, status=404)


@routes.patch("/api/local-crm/deals/{id}")
async def patch_deal(request: web.Request) -> web.Response:
    return _json(await get_crm().update_deal(int(request.match_info["id"]), **await _body(request)))


@routes.post("/api/local-crm/deals/{id}/comments")
async def deal_comment(request: web.Request) -> web.Response:
    body = await _body(request)
    return _json(await get_crm().add_comment("deal", int(request.match_info["id"]),
                                             body.get("text", ""), body.get("author", "manager")))


# ---------------- Products (§12.3) ----------------

@routes.get("/api/local-crm/products")
async def list_products(request: web.Request) -> web.Response:
    crm = get_crm()
    category = request.query.get("category", "")
    filters: dict[str, Any] = {}
    if request.query.get("stock"):
        filters["in_stock"] = request.query["stock"].lower().startswith("в налич")
    res = await crm.list_products(category=category, filters=filters, limit=int(request.query.get("limit", "50")))
    q = (request.query.get("q") or "").lower()
    items = res["products"]
    if q:
        items = [p for p in items if q in (p.get("name") or "").lower()]
    return _json({"products": items})


@routes.get("/api/local-crm/products/slug/{slug}")
async def product_by_slug(request: web.Request) -> web.Response:
    slug = request.match_info["slug"]
    for p in await get_crm()._all_products(only_active=False):
        if p.get("slug") == slug:
            return _json(await get_crm().get_product(article=p["article"]))
    return _json({"error": "not found"}, status=404)


@routes.get("/api/local-crm/products/{article}")
async def product_by_article(request: web.Request) -> web.Response:
    res = await get_crm().get_product(article=request.match_info["article"])
    return _json(res) if res.get("product") else _json({"error": "not found"}, status=404)


# ---------------- Knowledge (§12.4) ----------------

@routes.get("/api/local-crm/knowledge")
async def knowledge_list(request: web.Request) -> web.Response:
    db = await get_crm().search_knowledge("", "unknown", 100) if False else None  # noqa
    from app.crm.db import get_crm_db
    conn = await get_crm_db()
    async with conn.execute("SELECT * FROM knowledge_items WHERE active=1 ORDER BY priority DESC") as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return _json({"items": rows})


@routes.post("/api/local-crm/knowledge/search")
async def knowledge_search(request: web.Request) -> web.Response:
    body = await _body(request)
    return _json(await get_crm().search_knowledge(
        query=body.get("query", ""), intent=body.get("intent", "unknown"), limit=int(body.get("limit", 3))))


@routes.get("/api/local-crm/knowledge/{id}")
async def knowledge_get(request: web.Request) -> web.Response:
    from app.crm.db import get_crm_db
    conn = await get_crm_db()
    async with conn.execute("SELECT * FROM knowledge_items WHERE id=?", (int(request.match_info["id"]),)) as cur:
        row = await cur.fetchone()
    return _json(dict(row)) if row else _json({"error": "not found"}, status=404)


# ---------------- Activities (§12.5) ----------------

@routes.get("/api/local-crm/activities")
async def activities_list(request: web.Request) -> web.Response:
    return _json({"activities": await get_crm().list_activities(int(request.query.get("limit", "100")))})


@routes.post("/api/local-crm/activities")
async def activities_create(request: web.Request) -> web.Response:
    body = await _body(request)
    act_id = await get_crm().create_activity(
        body.get("entity_type", "system"), body.get("entity_id"), body.get("type", "message"),
        body.get("title", ""), body.get("content", ""), body.get("payload"), body.get("created_by", "api"))
    return _json({"activity_id": act_id}, status=201)


@routes.get("/api/local-crm/entities/{entity_type}/{entity_id}/activities")
async def entity_activities(request: web.Request) -> web.Response:
    return _json({"activities": await get_crm().list_activities(
        100, request.match_info["entity_type"], request.match_info["entity_id"])})


# ---------------- Webhooks (§12.6, §19) ----------------

async def _webhook(request: web.Request, event_type: str, source: str) -> web.Response:
    payload = await _body(request)
    return _json(await get_crm().receive_webhook(event_type, source, payload), status=201)


@routes.post("/api/local-crm/webhooks/telegram-message")
async def wh_telegram(request: web.Request) -> web.Response:
    return await _webhook(request, "telegram.message", "telegram")


@routes.post("/api/local-crm/webhooks/site-lead")
async def wh_site(request: web.Request) -> web.Response:
    return await _webhook(request, "site.lead_submitted", "website")


@routes.post("/api/local-crm/webhooks/agent-event")
async def wh_agent(request: web.Request) -> web.Response:
    return await _webhook(request, "agent.event", "agent")


@routes.post("/api/local-crm/webhooks/test-event")
async def wh_test(request: web.Request) -> web.Response:
    return await _webhook(request, "test.event", "test")


@routes.get("/api/local-crm/health")
async def health(request: web.Request) -> web.Response:
    return _json({"status": "ok", "crm": "local"})
