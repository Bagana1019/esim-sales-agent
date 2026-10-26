from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    public_base_url: str = "http://localhost:8000"
    database_url: str = "sqlite+aiosqlite:///./esim.db"
    catalog_path: str = "data/catalog.yaml"

    # Claude
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "low"
    # Яриаг шинээр эхлүүлэх нөхцөл: удаан идэвхгүй байх эсвэл хэт урт болох
    conversation_idle_hours: int = 12
    conversation_max_messages: int = 60

    # Telegram
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    admin_chat_id: str | None = None

    # QPay
    qpay_base_url: str = "https://merchant-sandbox.qpay.mn/v2"
    qpay_client_id: str = "TEST_MERCHANT"
    qpay_client_secret: str = "123456"
    qpay_invoice_code: str = "TEST_INVOICE"
    callback_secret: str = "change-me"


@lru_cache
def get_settings() -> Settings:
    return Settings()
