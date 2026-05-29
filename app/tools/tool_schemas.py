"""JSON-схемы tools — формат Ollama tool calling (совместим с OpenAI tools).

Эти же схемы используются в JSON-fallback режиме как enum допустимых tool_name.
"""
from __future__ import annotations


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "Ищет факты в базе знаний (FAQ): доставка, оплата, сборка, гарантия, возврат, "
                "наличие, оформление заказа, связь с менеджером, жалобы, правила CRM. "
                "Использовать перед любым фактическим ответом по этим темам."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Свободная формулировка вопроса клиента",
                    },
                    "intent": {
                        "type": "string",
                        "enum": [
                            "delivery", "delivery_time", "payment", "assembly",
                            "warranty", "return", "availability", "order",
                            "manager", "complaint", "spam", "unknown",
                        ],
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product",
            "description": "Возвращает карточку конкретного товара по названию, артикулу или цвету+категории.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Свободный запрос: «графитовый диван», «соната 160»…"},
                    "product_name": {"type": "string"},
                    "article": {"type": "string", "description": "Артикул, например SS-DV-210"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_products",
            "description": "Возвращает список товаров по категории и фильтрам.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "диваны|кресла|кровати|столы|комоды|шкафы|стеллажи|тумбы|прихожие",
                    },
                    "filters": {
                        "type": "object",
                        "properties": {
                            "in_stock": {"type": "boolean"},
                            "max_price": {"type": ["integer", "null"]},
                            "color": {"type": "string"},
                        },
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_lead",
            "description": "Создаёт лид в Bitrix24 для нового клиента, проявившего интерес.",
            "parameters": {
                "type": "object",
                "properties": {
                    "client_name": {"type": "string"},
                    "phone": {"type": "string"},
                    "product": {"type": "string"},
                    "city": {"type": "string"},
                    "comment": {"type": "string"},
                    "stage": {
                        "type": "string",
                        "enum": ["new", "consultation", "waiting", "order_created", "rejected"],
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_lead",
            "description": "Обновляет существующий лид по мере получения новых данных от клиента.",
            "parameters": {
                "type": "object",
                "properties": {
                    "client_name": {"type": "string"},
                    "phone": {"type": "string"},
                    "product": {"type": "string"},
                    "city": {"type": "string"},
                    "comment": {"type": "string"},
                    "stage": {
                        "type": "string",
                        "enum": ["new", "consultation", "waiting", "order_created", "rejected"],
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_deal",
            "description": (
                "Создаёт сделку в Bitrix24. ВЫЗЫВАТЬ ТОЛЬКО когда у клиента собраны: "
                "имя, телефон, товар, цвет, город, формат доставки. Без телефона — нельзя."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "client_name": {"type": "string"},
                    "phone": {"type": "string"},
                    "product": {"type": "string"},
                    "color": {"type": "string"},
                    "city": {"type": "string"},
                    "delivery_type": {
                        "type": "string",
                        "enum": ["До подъезда", "До квартиры", "Самовывоз"],
                    },
                    "assembly_needed": {
                        "type": "string",
                        "enum": ["Да", "Нет", "Не указано"],
                    },
                    "expected_delivery_date": {"type": "string"},
                    "comment": {"type": "string"},
                },
                "required": ["phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_manager",
            "description": (
                "Передаёт обращение живому менеджеру: явная просьба, жалоба, нестандартный вопрос, "
                "когда нужного ответа нет в инструментах."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "comment": {"type": "string"},
                    "client_name": {"type": "string"},
                    "phone": {"type": "string"},
                },
                "required": ["reason"],
            },
        },
    },
]


TOOL_NAMES: list[str] = [t["function"]["name"] for t in TOOL_SCHEMAS]
