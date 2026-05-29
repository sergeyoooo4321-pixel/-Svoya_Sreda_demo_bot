# Telegram-бот «Своя Среда» + Svoya CRM (локальный аналог Bitrix24)

Демо ИИ-менеджера мебельного магазина: Telegram-бот на ИИ-агенте (Ollama, tool calling),
каталог, база знаний, оформление заявок — и **локальная CRM «Svoya CRM»** с лидами,
сделками, канбаном стадий, активностями, вебхуками и **админкой**.

> **Bitrix24 больше не нужен.** Раньше CRM-функции планировались через Bitrix24, но платные
> возможности не подошли для демо. Теперь весь CRM-функционал реализован **локально в коде**
> (модуль `app/crm`, отдельная БД `data/svoya_crm.db`). Bitrix24 оставлен как будущая опция
> за флагом `CRM_MODE=bitrix`.

Стек: Python 3.11+, aiogram 3.x (бот), aiohttp + Jinja2 (API + админка), aiosqlite (SQLite),
httpx (Ollama), pydantic v2, loguru.

## Архитектура

```
Telegram Bot → Agent Core → Ollama → Local CRM Tools → Svoya CRM (SQLite) → ответ клиенту
Website      → Local CRM API (/api/local-crm) → Svoya CRM
Менеджер     → Админка /admin → Svoya CRM
```

Вся CRM-логика — в одном слое `app/crm` (SvoyaCRM). Бот, агент и сайт ходят туда, а не в таблицы напрямую.

## Быстрый старт

```bash
python -m venv .venv
.venv\Scripts\activate              # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env              # Windows  (Linux/macOS: cp .env.example .env)
# впишите TELEGRAM_BOT_TOKEN, OLLAMA_* (см. ниже)

python -m app.crm.seed              # заполнить локальную CRM (стадии, товары, знания, шаблоны, тест-диалоги)
python run.py                       # запустить Telegram-бота

# в отдельном терминале — API + админка Svoya CRM:
python -m app.web
```

- Миграции выполняются **автоматически** при старте (бот и веб создают таблицы сами).
- `seed` идемпотентен: повторный запуск не плодит дубли, обновляет по `code/article/intent/slug/title`.
- Если запустить бота на пустой базе — он сделает автосев сам (полный seed — командой выше).

## Админка

```
http://localhost:3000/admin      (логин/пароль из .env: ADMIN_LOGIN / ADMIN_PASSWORD, по умолчанию admin/admin)
```

Разделы: Дашборд, Лиды (канбан + карточка, смена стадии, комментарий, создание сделки),
Сделки, Товары (редактирование), База знаний (редактирование), Шаблоны, Тест-диалоги,
Активности, Настройки.

## Настройка `.env`

1. **Telegram:** `TELEGRAM_BOT_TOKEN` (от @BotFather), `TELEGRAM_ADMIN_IDS` (ваш числовой id от @userinfobot).
2. **Ollama:** `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, при облаке — `OLLAMA_API_KEY`.
3. **Svoya CRM:** `CRM_MODE=local` (по умолчанию), `LOCAL_CRM_API_KEY`, `WEBHOOK_SECRET`,
   `ADMIN_LOGIN`/`ADMIN_PASSWORD`, `ADMIN_PORT`.

## Проверка Telegram-бота

В чате с ботом: «диван есть?», «хочу графитовый диван» → «а доставка в химки сколько?»,
«хочу графитовый диван оформить» → «Андрей, 89991234567, Химки, до квартиры», «я подумаю, покажу жене»,
«хочу с менеджером». После — откройте `/admin/leads` и `/admin/deals`, чтобы увидеть лиды/сделки/стадии.

## Проверка сайта (форма заявки)

Сайт (`,fyfy/сайт`, не входит в этот проект) при отправке формы шлёт:

```
POST /api/local-crm/webhooks/site-lead     (заголовок X-Webhook-Secret: <WEBHOOK_SECRET>)
{ "client_name": "...", "phone": "...", "product": "...", "city": "...", "comment": "..." }
```

Создаётся лид с `source=website`; заявка видна в админке.

## API (Svoya CRM, base `/api/local-crm`)

Все запросы (кроме вебхуков) требуют заголовок `X-API-Key: <LOCAL_CRM_API_KEY>`.
Вебхуки требуют `X-Webhook-Secret: <WEBHOOK_SECRET>`.

```
GET    /leads            POST /leads          GET /leads/{id}   PATCH /leads/{id}
POST   /leads/{id}/move-stage                 POST /leads/{id}/comments
GET    /deals            POST /deals          GET /deals/{id}   PATCH /deals/{id}
POST   /deals/{id}/comments
GET    /products         GET /products/{article}   GET /products/slug/{slug}     (?category= ?stock= ?q=)
GET    /knowledge        POST /knowledge/search     GET /knowledge/{id}
GET    /activities       POST /activities           GET /entities/{type}/{id}/activities
POST   /webhooks/telegram-message  /site-lead  /agent-event  /test-event
GET    /health
```

Инструменты ИИ-агента (`app/tools/crm_tools.py`) поверх того же слоя: `search_knowledge`,
`get_product`, `list_products`, `create_lead`, `update_lead`, `create_deal`, `call_manager`.

## Память диалога и стадии

- Память по `telegram_chat_id`: последние 30–50 сообщений (`chat_messages`), summary после 30 (`chat_states`).
  Бот не переспрашивает товар/цвет/город/телефон, если клиент их уже называл.
- Стадии лида: `new` → `consultation` → `waiting` → `order_created` → `rejected`.
  «Подумаю/покажу жене» → Ждёт решения; выбор товара + телефон → сделка + Заказ оформлен; спам → Отказ.
- Сделка **не создаётся без телефона**.

## Как вернуть Bitrix24 (на будущее)

Слой `app/crm/client.py` выбирает реализацию по `CRM_MODE`. Чтобы вернуть Bitrix24:
добавьте адаптер с тем же интерфейсом, что у `SvoyaCRM`, и переключите `CRM_MODE=bitrix`.
Agent Core и боту переписывать не нужно.

## Тесты

```bash
pytest
```

## Структура

```
app/
  bot/        — aiogram-хендлеры, клавиатуры, тексты
  agent/      — Agent Core (цикл, короткий промт, парсер)
  crm/        — Svoya CRM: db, schema, seed, service, memory, client   ← локальный «Брикс»
  tools/      — crm_tools (инструменты агента поверх Svoya CRM), tool_schemas
  web/        — aiohttp: api (/api/local-crm) + admin (/admin) + templates
  services/   — Ollama-клиент, нормализация, каталог (для кнопок)
data/         — bot.db (FSM/пользователи) + svoya_crm.db (CRM + память агента)
tests/        — pytest
```

## Безопасность

- Все секреты — только в `.env` (в `.gitignore`). Токены/ключи не попадают в промт модели и логи (маскируются).
- API защищён `LOCAL_CRM_API_KEY`, вебхуки — `WEBHOOK_SECRET`, админка — basic-auth.
