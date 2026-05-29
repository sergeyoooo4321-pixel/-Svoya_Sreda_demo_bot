"""Мини-админка «Svoya CRM» (раздел т.md §14) на aiohttp + Jinja2."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jinja2
from aiohttp import web

from app.config import get_settings
from app.crm.db import get_crm_db
from app.crm.service import get_crm


_TEMPLATES = Path(__file__).resolve().parent / "templates"
_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATES)),
    autoescape=jinja2.select_autoescape(["html"]),
    enable_async=False,
)
_env.filters["loadjson"] = lambda s: (json.loads(s) if s else [])

STAGES = [
    ("new", "Новый лид"), ("consultation", "Консультация"), ("waiting", "Ждёт решения"),
    ("order_created", "Заказ оформлен"), ("rejected", "Отказ"),
]
STAGE_NAMES = dict(STAGES)

admin_routes = web.RouteTableDef()


def render(template: str, **ctx: Any) -> web.Response:
    ctx.setdefault("stages", STAGES)
    ctx.setdefault("stage_names", STAGE_NAMES)
    ctx.setdefault("site_url", get_settings().site_url)
    html = _env.get_template(template).render(**ctx)
    return web.Response(text=html, content_type="text/html")


@admin_routes.get("/admin")
async def admin_root(request: web.Request) -> web.Response:
    raise web.HTTPFound("/admin/dashboard")


@admin_routes.get("/admin/dashboard")
async def dashboard(request: web.Request) -> web.Response:
    crm = get_crm()
    counts = await crm.dashboard_counts()
    recent = await crm.list_activities(12)
    return render("dashboard.html", active="dashboard", counts=counts, recent=recent)


@admin_routes.get("/admin/leads")
async def leads_kanban(request: web.Request) -> web.Response:
    crm = get_crm()
    columns = {code: [] for code, _ in STAGES}
    for lead in await crm.list_leads(limit=500):
        columns.setdefault(lead.get("stage_code", "new"), []).append(lead)
    return render("leads.html", active="leads", columns=columns)


@admin_routes.get("/admin/leads/{id}")
async def lead_detail(request: web.Request) -> web.Response:
    crm = get_crm()
    lead = await crm.get_lead(int(request.match_info["id"]))
    if not lead:
        raise web.HTTPNotFound(text="Лид не найден")
    acts = await crm.list_activities(50, "lead", str(lead["id"]))
    msgs = []
    if lead.get("telegram_chat_id"):
        db = await get_crm_db()
        async with db.execute(
            "SELECT role, content, created_at FROM chat_messages WHERE telegram_chat_id=? ORDER BY id DESC LIMIT 30",
            (lead["telegram_chat_id"],),
        ) as cur:
            msgs = [dict(r) for r in reversed(await cur.fetchall())]
    return render("lead_detail.html", active="leads", lead=lead, activities=acts, messages=msgs)


@admin_routes.post("/admin/leads/{id}/stage")
async def lead_set_stage(request: web.Request) -> web.Response:
    data = await request.post()
    lead_id = int(request.match_info["id"])
    await get_crm().move_lead_stage(lead_id, data.get("stage_code", "new"))
    raise web.HTTPFound(f"/admin/leads/{lead_id}")


@admin_routes.post("/admin/leads/{id}/comment")
async def lead_add_comment(request: web.Request) -> web.Response:
    data = await request.post()
    lead_id = int(request.match_info["id"])
    if data.get("text"):
        await get_crm().add_comment("lead", lead_id, data["text"], "manager")
    raise web.HTTPFound(f"/admin/leads/{lead_id}")


@admin_routes.post("/admin/leads/{id}/create-deal")
async def lead_create_deal(request: web.Request) -> web.Response:
    crm = get_crm()
    lead_id = int(request.match_info["id"])
    lead = await crm.get_lead(lead_id)
    if lead and (lead.get("phone")):
        amount = None
        prod = await crm.get_product(query=lead.get("interested_product") or "",
                                     article=lead.get("product_article") or "")
        if prod.get("product"):
            amount = prod["product"].get("price_value")
        await crm.create_deal(
            lead_id=lead_id, source="admin", client_name=lead.get("client_name"), phone=lead.get("phone"),
            product_name=lead.get("interested_product"), product_article=lead.get("product_article"),
            city=lead.get("city"), delivery_type=lead.get("delivery_type"),
            assembly_needed=lead.get("assembly_needed"), amount=amount,
            bot_comment="Сделка создана из админки.",
        )
    raise web.HTTPFound(f"/admin/leads/{lead_id}")


@admin_routes.get("/admin/deals")
async def deals_list(request: web.Request) -> web.Response:
    return render("deals.html", active="deals", deals=await get_crm().list_deals(500))


@admin_routes.get("/admin/deals/{id}")
async def deal_detail(request: web.Request) -> web.Response:
    crm = get_crm()
    deal = await crm.get_deal(int(request.match_info["id"]))
    if not deal:
        raise web.HTTPNotFound(text="Сделка не найдена")
    acts = await crm.list_activities(50, "deal", str(deal["id"]))
    statuses = ["new_order", "confirmed", "in_delivery", "completed", "cancelled"]
    return render("deal_detail.html", active="deals", deal=deal, activities=acts, statuses=statuses)


@admin_routes.post("/admin/deals/{id}/status")
async def deal_set_status(request: web.Request) -> web.Response:
    data = await request.post()
    deal_id = int(request.match_info["id"])
    await get_crm().update_deal(deal_id, status=data.get("status", "new_order"))
    raise web.HTTPFound(f"/admin/deals/{deal_id}")


@admin_routes.get("/admin/products")
async def products_list(request: web.Request) -> web.Response:
    products = await get_crm()._all_products(only_active=False)
    return render("products.html", active="products", products=products)


@admin_routes.get("/admin/products/{id}/edit")
async def product_edit_form(request: web.Request) -> web.Response:
    db = await get_crm_db()
    async with db.execute("SELECT * FROM products WHERE id=?", (int(request.match_info["id"]),)) as cur:
        row = await cur.fetchone()
    if not row:
        raise web.HTTPNotFound()
    return render("product_edit.html", active="products", p=dict(row))


@admin_routes.post("/admin/products/{id}")
async def product_save(request: web.Request) -> web.Response:
    data = await request.post()
    pid = int(request.match_info["id"])
    db = await get_crm_db()
    await db.execute(
        "UPDATE products SET price=?, price_text=?, stock=?, description=?, "
        "manager_recommendation=?, active=?, updated_at=datetime('now') WHERE id=?",
        (int(data.get("price") or 0), data.get("price_text", ""), data.get("stock", ""),
         data.get("description", ""), data.get("manager_recommendation", ""),
         1 if data.get("active") == "on" else 0, pid),
    )
    await db.commit()
    raise web.HTTPFound("/admin/products")


@admin_routes.get("/admin/knowledge")
async def knowledge_list(request: web.Request) -> web.Response:
    db = await get_crm_db()
    async with db.execute("SELECT * FROM knowledge_items ORDER BY priority DESC") as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return render("knowledge.html", active="knowledge", items=rows)


@admin_routes.get("/admin/knowledge/{id}/edit")
async def knowledge_edit_form(request: web.Request) -> web.Response:
    db = await get_crm_db()
    async with db.execute("SELECT * FROM knowledge_items WHERE id=?", (int(request.match_info["id"]),)) as cur:
        row = await cur.fetchone()
    if not row:
        raise web.HTTPNotFound()
    return render("knowledge_edit.html", active="knowledge", k=dict(row))


@admin_routes.post("/admin/knowledge/{id}")
async def knowledge_save(request: web.Request) -> web.Response:
    data = await request.post()
    kid = int(request.match_info["id"])
    db = await get_crm_db()
    await db.execute(
        "UPDATE knowledge_items SET keywords=?, answer=?, clarify=?, restrictions=?, "
        "priority=?, active=?, updated_at=datetime('now') WHERE id=?",
        (data.get("keywords", ""), data.get("answer", ""), data.get("clarify", ""),
         data.get("restrictions", ""), int(data.get("priority") or 0),
         1 if data.get("active") == "on" else 0, kid),
    )
    await db.commit()
    raise web.HTTPFound("/admin/knowledge")


@admin_routes.get("/admin/templates")
async def templates_list(request: web.Request) -> web.Response:
    db = await get_crm_db()
    async with db.execute("SELECT * FROM bot_reply_templates ORDER BY intent") as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return render("templates.html", active="templates", items=rows)


@admin_routes.get("/admin/test-dialogs")
async def test_dialogs_list(request: web.Request) -> web.Response:
    db = await get_crm_db()
    async with db.execute("SELECT * FROM test_dialogs ORDER BY id") as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return render("test_dialogs.html", active="test-dialogs", items=rows)


@admin_routes.get("/admin/activities")
async def activities_list(request: web.Request) -> web.Response:
    return render("activities.html", active="activities", items=await get_crm().list_activities(200))


@admin_routes.get("/admin/settings")
async def settings_view(request: web.Request) -> web.Response:
    s = get_settings()
    info = {
        "CRM_MODE": s.crm_mode,
        "DATABASE_URL (svoya_crm)": s.svoya_crm_database_url,
        "OLLAMA_MODEL": s.ollama_model,
        "OLLAMA_BASE_URL": s.ollama_base_url,
        "MEMORY_LAST_MESSAGES": s.memory_last_messages,
        "AGENT_MAX_TOOL_CALLS": s.agent_max_tool_calls,
        "SITE_URL": s.site_url,
    }
    return render("settings.html", active="settings", info=info)
