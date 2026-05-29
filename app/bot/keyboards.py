from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ---- тексты кнопок главного меню (reply-клавиатура под полем ввода) ----
BTN_CATALOG = "🛋 Каталог"
BTN_SELECTION = "🪄 Подобрать мебель"
BTN_DELIVERY = "🚚 Доставка и оплата"
BTN_ORDER = "🛒 Оформить заказ"
BTN_MANAGER = "💬 Чат с менеджером"
BTN_MANAGER_END = "↩️ Завершить чат с менеджером"


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню — постоянная клавиатура под полем ввода (а не кнопки под сообщениями)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CATALOG), KeyboardButton(text=BTN_SELECTION)],
            [KeyboardButton(text=BTN_DELIVERY), KeyboardButton(text=BTN_ORDER)],
            [KeyboardButton(text=BTN_MANAGER)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Напишите сообщение или выберите в меню…",
    )


def get_manager_chat_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура во время живого чата с менеджером — только выход."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_MANAGER_END)]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Напишите менеджеру…",
    )


# ---- контекстные inline-кнопки (остаются под сообщениями) ----

def get_categories_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🛋 Диваны", callback_data="category_divany")
    builder.button(text="🪑 Кресла", callback_data="category_kresla")
    builder.button(text="🛏 Кровати", callback_data="category_krovati")
    builder.button(text="🪚 Столы", callback_data="category_stoly")
    builder.button(text="📦 Хранение", callback_data="category_hranenie")
    builder.button(text="🚪 Прихожие", callback_data="category_prihozhie")
    builder.adjust(2, 2, 2)
    builder.row(InlineKeyboardButton(text="🌟 Показать всё", callback_data="category_all"))
    return builder.as_markup()


def get_product_keyboard(product_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🛒 Выбрать этот товар", callback_data=f"select_{product_id}"))
    builder.row(InlineKeyboardButton(text="🚚 Уточнить доставку", callback_data=f"ask_delivery_{product_id}"))
    builder.row(InlineKeyboardButton(text="💳 Оформить заказ", callback_data=f"order_{product_id}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад в каталог", callback_data="catalog"))
    return builder.as_markup()
