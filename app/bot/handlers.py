"""Telegram-хендлеры: главное меню на reply-клавиатуре, каталог (inline),
живой чат с менеджером (релей клиент↔менеджер) и свободное оформление через ИИ-агента."""
from __future__ import annotations

from typing import Optional

from aiogram import F, Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards import (
    BTN_CATALOG,
    BTN_DELIVERY,
    BTN_MANAGER,
    BTN_MANAGER_END,
    BTN_ORDER,
    BTN_SELECTION,
    get_categories_keyboard,
    get_main_reply_keyboard,
    get_manager_chat_keyboard,
    get_product_keyboard,
)
from app.bot.middlewares import Services
from app.bot.states import ManagerChat
from app.bot.texts import ASK_SELECTION_Q1, DEMO_NOTE, MAIN_MENU_TEXT, get_product_text
from app.config import get_settings
from app.logger import logger
from app.services.catalog_service import (
    CATEGORY_GROUPS,
    get_product,
    get_products_by_group,
    resolve_image_path,
)
from app.services.normalization import extract_phone, mask_phone
from app.storage import repositories as repo

router = Router()

# message_id уведомления у менеджера -> кто клиент. Для двустороннего релея (in-memory, демо).
_relay: dict[int, dict] = {}

DELIVERY_TEXT = (
    "<b>🚚 Доставка</b>\n"
    "• По Москве — 1–3 дня (товары в наличии).\n"
    "• По Подмосковью — 2–5 дней.\n"
    "• В регионы — транспортной компанией по согласованию.\n\n"
    "<b>💳 Оплата</b>\n"
    "• Карта, перевод по ссылке, наличные при получении.\n"
    "• Для юрлиц — безналичный расчёт. Под заказ — обычно предоплата 30%.\n\n"
    "<i>Для точного расчёта напишите город, адрес, этаж и наличие лифта.</i>"
)
ORDER_PROMPT = (
    "Оформим заявку. Напишите <b>одним сообщением</b>, как вам удобно: имя, телефон, "
    "какой товар и цвет, город и формат доставки (до квартиры / до подъезда / самовывоз).\n\n"
    "<i>Можно свободно, без строгого формата — я всё пойму и зафиксирую. Телефон в любом виде, "
    "например 89991234567.</i> ✍️"
)


def _is_admin(user_id: int) -> bool:
    return user_id in get_settings().admin_ids


# ---------------- Команды ----------------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if user is None:
        return
    await state.clear()
    await repo.upsert_user(user.id, user.username, user.first_name, user.last_name)
    await repo.ensure_session(user.id)
    await message.answer(MAIN_MENU_TEXT + DEMO_NOTE, parse_mode=ParseMode.HTML, reply_markup=get_main_reply_keyboard())


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    await message.answer(
        "Админ-команды:\n/stats — статистика\n/recent_orders — последние сделки\n"
        "/retry_crm_sync — (локальная CRM, не требуется)\n\n"
        "💬 Чтобы ответить клиенту в чате — <b>ответьте reply</b> на его сообщение-уведомление.",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message, services: Services) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    d = await services.crm.dashboard_counts()
    await message.answer(
        "<b>📊 Статистика (Svoya CRM)</b>\n"
        f"Лидов всего: {d['leads_total']}\n"
        f"• Новый: {d['leads_new']} · Консультация: {d['leads_consultation']} · "
        f"Ждёт: {d['leads_waiting']} · Заказ: {d['leads_order_created']} · Отказ: {d['leads_rejected']}\n"
        f"Сделок: {d['deals_total']}\n\n"
        f"Полная админка: {get_settings().site_url}/admin",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("recent_orders"))
async def cmd_recent_orders(message: Message, services: Services) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    deals = await services.crm.list_deals(5)
    if not deals:
        await message.answer("Сделок пока нет.")
        return
    lines = [
        f"#{d['id']} {d.get('client_name') or '—'}, {mask_phone(d.get('phone'))}\n"
        f"  {d.get('product_name') or '—'}, {d.get('color') or '—'}\n"
        f"  {d.get('city') or '—'}, {d.get('delivery_type') or '—'} | {d.get('status') or '—'}"
        for d in deals
    ]
    await message.answer("\n\n".join(lines))


@router.message(Command("retry_crm_sync"))
async def cmd_retry_crm_sync(message: Message) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    await message.answer("Локальная CRM (Svoya CRM): данные пишутся сразу в базу, синхронизация не требуется.")


# ---------------- Каталог (inline) ----------------

@router.callback_query(F.data == "catalog")
async def cb_catalog(call: CallbackQuery) -> None:
    await _edit_or_answer(call, "<b>🛋 Каталог мебели</b>\n\nВыберите категорию:", reply_markup=get_categories_keyboard())
    await call.answer()


@router.callback_query(F.data.startswith("category_"))
async def cb_category(call: CallbackQuery) -> None:
    group = call.data.split("_", 1)[1]
    if group not in CATEGORY_GROUPS:
        await call.answer("Категория не найдена", show_alert=False)
        return
    products = get_products_by_group(group)
    if not products:
        await _edit_or_answer(call, "Пока в этой категории ничего нет.", reply_markup=get_categories_keyboard())
        await call.answer()
        return
    builder = InlineKeyboardBuilder()
    for product in products:
        builder.row(InlineKeyboardButton(text=f"{product.title} — {product.price_text}",
                                         callback_data=f"product_{product.id}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад к категориям", callback_data="catalog"))
    await _edit_or_answer(call, f"<b>Категория:</b> найдено {len(products)} товаров", reply_markup=builder.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("product_"))
async def cb_product(call: CallbackQuery) -> None:
    product = get_product(call.data.split("_", 1)[1])
    if not product:
        await call.answer("Товар не найден", show_alert=False)
        return
    await _show_product_card(call, product)
    await call.answer()


@router.callback_query(F.data.startswith("select_"))
async def cb_select_product(call: CallbackQuery, services: Services) -> None:
    product = get_product(call.data.split("_", 1)[1])
    if not product or call.from_user is None:
        await call.answer("Не получилось выбрать", show_alert=False)
        return
    await services.memory.update_extracted(call.from_user.id, {
        "product": product.title, "product_article": product.id,
    })
    await services.crm.upsert_lead_for_chat(
        call.from_user.id, source="telegram", stage_code="consultation",
        interested_product=product.title, product_article=product.id,
        bot_comment=f"Клиент выбрал товар {product.title} в Telegram-боте.",
    )
    await call.answer(f"Выбрано: {product.title}", show_alert=False)
    await _edit_or_answer(
        call,
        f"Отметил, что вам интересен <b>{product.title}</b>. "
        f"Нажмите «Оформить заказ» или просто напишите детали — оформлю заявку.",
        reply_markup=get_product_keyboard(product.id),
    )


@router.callback_query(F.data.startswith("ask_delivery_"))
async def cb_ask_delivery(call: CallbackQuery) -> None:
    product = get_product(call.data.split("ask_delivery_", 1)[1])
    if not product or call.from_user is None:
        await call.answer()
        return
    text = (
        f"<b>🚚 Доставка для {product.title}:</b>\n"
        "• По Москве — 1–3 дня (для товаров в наличии).\n"
        "• По Московской области — 2–5 дней.\n"
        "• Подъём и сборка считаются отдельно.\n\n"
        "Напишите город или район, этаж и нужен ли подъём — сориентирую точнее."
    )
    await _edit_or_answer(call, text, reply_markup=get_product_keyboard(product.id))
    await call.answer()


@router.callback_query(F.data.startswith("order_"))
async def cb_order_product(call: CallbackQuery, services: Services) -> None:
    """«Оформить» на карточке товара — без жёсткой формы: фиксируем товар и просим написать детали свободно."""
    if call.from_user is None:
        return
    product = get_product(call.data.split("_", 1)[1])
    if product:
        await services.memory.update_extracted(call.from_user.id, {
            "product": product.title, "product_article": product.id, "last_intent": "order",
        })
        await services.crm.upsert_lead_for_chat(
            call.from_user.id, source="telegram", stage_code="consultation",
            interested_product=product.title, product_article=product.id,
            bot_comment="Клиент нажал «Оформить заказ» в боте.",
        )
    await _edit_or_answer(
        call,
        f"Оформляем <b>{product.title if product else 'товар'}</b>.\n\n{ORDER_PROMPT}",
    )
    await call.answer()


# ---------------- Выход из чата с менеджером (любое состояние, до общих хендлеров) ----------------

@router.message(F.text == BTN_MANAGER_END)
async def manager_chat_end(message: Message, state: FSMContext, services: Services) -> None:
    await state.clear()
    await message.answer("Чат с менеджером завершён. Возвращаю в меню.", reply_markup=get_main_reply_keyboard())
    if message.from_user:
        for admin_id in get_settings().admin_ids:
            try:
                await message.bot.send_message(admin_id, f"⏹ Клиент #{message.from_user.id} завершил чат с менеджером.")
            except Exception:  # noqa: BLE001
                pass


# ---------------- Чат с менеджером: форма и живой релей ----------------

@router.message(ManagerChat.awaiting_contact)
async def manager_contact(message: Message, state: FSMContext, services: Services) -> None:
    if message.from_user is None:
        return
    text = message.text or ""
    phone = extract_phone(text)
    name = _extract_name_excluding_phone(text, phone)
    await services.memory.update_extracted(message.from_user.id, {
        "client_name": name or None, "phone": phone or None,
    })
    await services.crm.call_manager(
        telegram_chat_id=message.from_user.id, reason="Клиент открыл чат с менеджером",
        client_name=name or "", phone=phone or "",
    )
    await state.set_state(ManagerChat.active)
    await message.answer(
        "Передал менеджеру — он скоро напишет вам прямо здесь. "
        "Можете пока коротко описать вопрос. ✍️",
        reply_markup=get_manager_chat_keyboard(),
    )
    await _relay_to_managers(
        message, services, name,
        f"открыл чат с менеджером. Имя: {name or '—'}, телефон: {phone or '—'}. "
        f"Если в сообщении был вопрос: «{text}». Ответьте reply на это сообщение, чтобы написать клиенту.",
    )


@router.message(ManagerChat.active)
async def manager_chat_relay(message: Message, services: Services) -> None:
    """Сообщения клиента в режиме чата уходят менеджеру (не в ИИ-агента)."""
    if message.from_user is None:
        return
    text = message.text or "(вложение/сообщение без текста)"
    await services.memory.save_user_message(message.from_user.id, text)
    state = await services.memory.load_state(message.from_user.id)
    await _relay_to_managers(message, services, state.extracted.client_name, text)


# ---------------- Кнопки меню (reply) ----------------

@router.message(F.text == BTN_CATALOG)
async def menu_catalog(message: Message) -> None:
    await message.answer("<b>🛋 Каталог мебели</b>\n\nВыберите категорию:",
                         parse_mode=ParseMode.HTML, reply_markup=get_categories_keyboard())


@router.message(F.text == BTN_SELECTION)
async def menu_selection(message: Message) -> None:
    await message.answer(
        "🪄 <b>Подбор мебели</b>\n\n" + ASK_SELECTION_Q1 +
        "\n\n<i>Просто напишите ответ — я уточню детали и предложу варианты.</i>",
        parse_mode=ParseMode.HTML,
    )


@router.message(F.text == BTN_DELIVERY)
async def menu_delivery(message: Message) -> None:
    await message.answer(DELIVERY_TEXT, parse_mode=ParseMode.HTML)


@router.message(F.text == BTN_ORDER)
async def menu_order(message: Message, services: Services) -> None:
    if message.from_user:
        await services.memory.update_extracted(message.from_user.id, {"last_intent": "order"})
    await message.answer(ORDER_PROMPT, parse_mode=ParseMode.HTML)


@router.message(F.text == BTN_MANAGER)
async def menu_manager(message: Message, state: FSMContext) -> None:
    await state.set_state(ManagerChat.awaiting_contact)
    await message.answer(
        "💬 Подключаю менеджера.\n\nНапишите, пожалуйста, <b>ваше имя и телефон</b> одним сообщением "
        "(и сразу вопрос, если хотите). Менеджер ответит вам прямо в этом чате.",
        parse_mode=ParseMode.HTML,
        reply_markup=get_manager_chat_keyboard(),
    )


# ---------------- Свободный диалог + ответы менеджера ----------------

@router.message(F.text)
async def free_text(message: Message, services: Services) -> None:
    user = message.from_user
    if user is None or not message.text:
        return

    # 1) Ответ менеджера: админ отвечает reply на уведомление клиента → шлём клиенту.
    if _is_admin(user.id) and message.reply_to_message and message.reply_to_message.message_id in _relay:
        target = _relay[message.reply_to_message.message_id]
        try:
            await message.bot.send_message(
                target["client_id"], f"👤 <b>Менеджер:</b> {message.text}", parse_mode=ParseMode.HTML
            )
            await services.crm.create_activity(
                "chat", target["client_id"], "message", "Ответ менеджера", message.text,
                {"to": target["client_id"]}, created_by="manager",
            )
            await message.answer("✅ Отправлено клиенту.")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"manager reply failed: {exc!r}")
            await message.answer("⚠️ Не удалось доставить сообщение клиенту.")
        return

    # 2) Обычный клиент → ИИ-агент (свободный диалог).
    await repo.upsert_user(user.id, user.username, user.first_name, user.last_name)
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    except Exception:  # noqa: BLE001
        pass

    logger.info(f"free_text: chat={user.id} text='{message.text[:80]}'")
    try:
        result = await services.agent.handle(user.id, message.text)
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"AgentCore упал для chat={user.id}: {exc!r}")
        await message.answer(
            "Сейчас есть небольшая техническая пауза. Я зафиксировал обращение, "
            "менеджер сможет вернуться к нему."
        )
        return
    await message.answer(result.reply)  # главное меню — на постоянной reply-клавиатуре

    for action in result.actions:
        if action.get("tool") == "create_deal" and (action.get("result") or {}).get("status") == "created":
            state = await services.memory.load_state(user.id)
            await _notify_admins(message, services, text=(
                f"🆕 Сделка #{(action['result']).get('deal_id')} (agent)\n"
                f"Клиент: {state.extracted.client_name or '—'}, {mask_phone(state.extracted.phone)}\n"
                f"Товар: {state.extracted.product or '—'} ({state.extracted.color or '—'})\n"
                f"Город: {state.extracted.city or '—'}, {state.extracted.delivery_type or '—'}"
            ))
    if result.manager_required:
        state = await services.memory.load_state(user.id)
        await _notify_admins(message, services, text=(
            f"📞 Нужен менеджер (agent)\nКлиент: {state.extracted.client_name or '—'}, "
            f"{mask_phone(state.extracted.phone)}"
        ))


# ---------------- helpers ----------------

async def _relay_to_managers(message: Message, services: Services, client_name: Optional[str], text: str) -> None:
    """Шлёт сообщение клиента всем админам-менеджерам и запоминает связь для ответа."""
    if message.from_user is None:
        return
    client_id = message.from_user.id
    title = client_name or (message.from_user.first_name or "Клиент")
    body = f"💬 <b>{title}</b> (#{client_id}):\n{text}"
    sent_any = False
    for admin_id in get_settings().admin_ids:
        try:
            sent = await message.bot.send_message(admin_id, body, parse_mode=ParseMode.HTML)
            _relay[sent.message_id] = {"client_id": client_id, "client_name": client_name}
            sent_any = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"relay to admin {admin_id} failed: {exc!r}")
    await services.crm.create_activity("chat", client_id, "message", "Сообщение в чат менеджеру", text,
                                       {"client_name": client_name}, created_by="client")
    if not sent_any:
        await message.answer("Менеджер сейчас недоступен, но я зафиксировал обращение — с вами свяжутся.")


async def _edit_or_answer(call: CallbackQuery, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None) -> None:
    if call.message is None:
        return
    try:
        await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    except TelegramBadRequest:
        try:
            await call.message.delete()
        except TelegramBadRequest:
            pass
        await call.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


async def _show_product_card(call: CallbackQuery, product) -> None:
    if call.message is None:
        return
    caption = get_product_text(product.model_dump())
    keyboard = get_product_keyboard(product.id)
    image_path = resolve_image_path(product.image_path)
    if image_path is None:
        await _edit_or_answer(call, caption, reply_markup=keyboard)
        return
    if len(caption) > 1000:
        caption = caption[:997] + "…"
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass
    await call.message.answer_photo(photo=FSInputFile(str(image_path)), caption=caption,
                                    parse_mode=ParseMode.HTML, reply_markup=keyboard)


def _extract_name_excluding_phone(text: str, phone: Optional[str]) -> Optional[str]:
    import re
    cleaned = re.sub(r"[\d+\-()]", " ", text) if phone else text
    cleaned = " ".join(cleaned.split()).strip(" ,.;:-—")
    return cleaned or None


async def _notify_admins(event, services: Services, text: str) -> None:
    bot = event.bot if hasattr(event, "bot") else None
    if bot is None and isinstance(event, CallbackQuery):
        bot = event.message.bot if event.message else None
    if bot is None:
        return
    for admin_id in get_settings().admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Не удалось уведомить админа {admin_id}: {exc!r}")
