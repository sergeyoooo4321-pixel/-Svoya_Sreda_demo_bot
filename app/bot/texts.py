# Тексты для бота (HTML ParseMode)

MAIN_MENU_TEXT = (
    "👋 <b>Здравствуйте!</b> Я помощник магазина мебели «Своя Среда».\n\n"
    "🛋 Помогу подобрать идеальную мебель для вашего дома, покажу наш каталог, "
    "отвечу на вопросы по доставке и оплате, а также помогу оформить заказ.\n\n"
    "<i>Чем я могу помочь вам сегодня?</i>"
)

# Текст-подпись для приветствия (/start)
DEMO_NOTE = (
    "\n\n———\n"
    "ℹ️ <b>Демо-версия</b>: ИИ-менеджер техподдержки и продаж.\n"
    "🌐 Сайт магазина (тоже демо): https://svoyasredademo.ru\n"
    "👤 Автор: @cvvjesuss — хотите такого же бота? Напишите, обсудим."
)

def get_product_text(product: dict) -> str:
    """Форматирует карточку товара для Telegram"""
    title = product.get('title', 'Без названия')
    price = product.get('price_text', 'По запросу')
    dimensions = product.get('dimensions', 'Не указано')
    colors = ", ".join(product.get('colors', []))
    availability = product.get('availability', 'Уточняйте')
    description = product.get('description', '')

    text = f"🛋 <b>{title}</b>\n\n"
    text += f"💰 <b>Цена:</b> {price}\n"
    text += f"📏 <b>Размер:</b> {dimensions}\n"
    if colors:
        text += f"🎨 <b>Цвета:</b> {colors}\n"
    text += f"✅ <b>Наличие:</b> {availability}\n\n"
    text += f"<i>{description}</i>"
    
    return text

ASK_SELECTION_Q1 = "Для какой комнаты вам нужна мебель?"
ASK_SELECTION_Q2 = "Что примерно ищете: диван, кровать, шкаф, стол, комплект?"
ASK_SELECTION_Q3 = "Какой размер комнаты или свободной стены?"
ASK_SELECTION_Q4 = "Есть ли у вас ориентир по бюджету?"

ORDER_MISSING_DATA = "Отлично, почти всё готово. Для оформления заказа мне не хватает некоторых данных:\n"
ORDER_SUCCESS = "Спасибо! Заявка передана нашему менеджеру. 🌟 Он проверит наличие, рассчитает доставку и свяжется с вами в ближайшее время."
