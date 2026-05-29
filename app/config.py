"""Конфигурация приложения. Все секреты — только из .env."""
from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_env: str = "dev"
    app_name: str = "svoya-sreda-bot"
    log_level: str = "INFO"
    bot_mode: str = "polling"  # polling | webhook

    # Telegram
    telegram_bot_token: str = ""
    telegram_admin_ids: str = ""  # "123,456"

    # Webhook
    webhook_base_url: str = ""
    webhook_path: str = "/telegram/webhook"
    webhook_secret_token: str = ""
    web_server_host: str = "0.0.0.0"
    web_server_port: int = 8080

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_api_key: str = ""
    ollama_timeout_seconds: int = 60
    ollama_temperature: float = 0.3
    ollama_use_structured_output: bool = True
    ollama_num_ctx: int = 8192

    # Bitrix24
    bitrix_webhook_url: str = ""
    bitrix_use_universal_crm: bool = True
    bitrix_timeout_seconds: int = 30

    bitrix_entity_type_id_lead: int = 1
    bitrix_entity_type_id_deal: int = 2

    bitrix_lead_stage_new: str = "NEW"
    bitrix_lead_stage_consultation: str = "CONSULTATION"
    bitrix_lead_stage_waiting: str = "WAITING"
    bitrix_lead_stage_order_created: str = "ORDER_CREATED"
    bitrix_lead_stage_refused: str = "JUNK"

    bitrix_deal_stage_new: str = "NEW"
    bitrix_deal_stage_prepayment: str = "PREPAYMENT"
    bitrix_deal_stage_delivery: str = "DELIVERY"
    bitrix_deal_stage_won: str = "WON"
    bitrix_deal_stage_lose: str = "LOSE"

    bitrix_lead_field_product: str = "UF_CRM_INTEREST_PRODUCT"
    bitrix_lead_field_city: str = "UF_CRM_DELIVERY_CITY"
    bitrix_lead_field_bot_comment: str = "UF_CRM_BOT_COMMENT"

    bitrix_deal_field_color: str = "UF_CRM_FURNITURE_COLOR"
    bitrix_deal_field_delivery_type: str = "UF_CRM_DELIVERY_TYPE"
    bitrix_deal_field_assembly: str = "UF_CRM_NEED_ASSEMBLY"
    bitrix_deal_field_delivery_date: str = "UF_CRM_ESTIMATED_DELIVERY_DATE"

    # Storage
    database_url: str = "sqlite+aiosqlite:///./data/bot.db"
    history_limit: int = 10
    session_ttl_hours: int = 24

    # ---------------- Agent / Memory / Knowledge (раздел "рю.md") ----------------

    # Память диалога (принимаем и старые MEMORY_*, и новые AGENT_MEMORY_* имена из ТЗ)
    memory_last_messages: int = Field(
        50, validation_alias=AliasChoices("memory_last_messages", "agent_memory_last_messages")
    )
    memory_summary_after_messages: int = Field(
        30, validation_alias=AliasChoices("memory_summary_after_messages", "agent_memory_summary_after_messages")
    )

    # Agent
    agent_max_tool_calls: int = 5
    agent_debug: bool = False

    # Bitrix24 Knowledge Base (legacy — оставлено за флагом CRM_MODE=bitrix)
    bitrix24_kb_entity_type_id: int = 0
    bitrix24_products_entity_type_id: int = 0
    bitrix24_base_url: str = ""

    # ---------------- Svoya CRM (локальный аналог Bitrix24, раздел т.md §5) ----------------
    crm_mode: str = "local"  # local | bitrix
    svoya_crm_database_url: str = "file:./data/svoya_crm.db"
    local_crm_base_url: str = "http://localhost:3000/api/local-crm"
    local_crm_api_key: str = "demo-local-crm-secret"
    admin_login: str = "admin"
    admin_password: str = "admin"
    admin_host: str = "0.0.0.0"
    admin_port: int = 3000
    site_url: str = "http://localhost:3000"
    form_secret: str = "demo-form-secret"
    webhook_secret: str = "demo-webhook-secret"

    @field_validator("bot_mode")
    @classmethod
    def _validate_bot_mode(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {"polling", "webhook"}:
            raise ValueError("BOT_MODE must be 'polling' or 'webhook'")
        return v

    @property
    def admin_ids(self) -> list[int]:
        ids: list[int] = []
        for chunk in self.telegram_admin_ids.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                ids.append(int(chunk))
            except ValueError:
                continue
        return ids

    @property
    def db_path(self) -> Path:
        """SQLAlchemy-стиль URL `sqlite+aiosqlite:///./data/bot.db` -> Path."""
        url = self.database_url
        marker = "///"
        if marker in url:
            raw = url.split(marker, 1)[1]
        else:
            raw = url
        path = Path(raw)
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        return path

    @property
    def svoya_crm_db_path(self) -> Path:
        """`file:./data/svoya_crm.db` (или sqlite-URL) -> абсолютный Path."""
        url = self.svoya_crm_database_url
        raw = url
        if raw.startswith("file:"):
            raw = raw[len("file:"):]
        if "///" in raw:
            raw = raw.split("///", 1)[1]
        path = Path(raw)
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        return path

    @property
    def crm_is_local(self) -> bool:
        return (self.crm_mode or "local").strip().lower() != "bitrix"

    @property
    def bitrix_enabled(self) -> bool:
        return bool(self.bitrix_webhook_url) and "your-domain" not in self.bitrix_webhook_url

    @property
    def ollama_enabled(self) -> bool:
        return bool(self.ollama_base_url)

    @property
    def webhook_url(self) -> str:
        return f"{self.webhook_base_url.rstrip('/')}{self.webhook_path}"

    def ensure_critical(self) -> None:
        """Падать с понятной ошибкой до старта polling, если нет токена."""
        if not self.telegram_bot_token or self.telegram_bot_token.startswith("put_"):
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN не задан в .env. "
                "Скопируйте .env.example в .env и впишите токен от @BotFather."
            )
        if self.bot_mode == "webhook":
            if not self.webhook_base_url:
                raise RuntimeError("BOT_MODE=webhook требует WEBHOOK_BASE_URL в .env.")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
