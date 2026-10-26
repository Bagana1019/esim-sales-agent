from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # utf-8-sig: Windows Notepad-ийн BOM-той UTF-8 файлыг ч зөв уншина
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8-sig", extra="ignore")

    public_base_url: str = "http://localhost:8000"
    database_url: str = "sqlite+aiosqlite:///./esim.db"
    catalog_path: str = "data/catalog.yaml"
    shop_name: str = "eSIM Mongolia"
    # Локал туршилт: QPay-ийн оронд хуурамч төлбөр, туршилтын eSIM/лиценз автоматаар санд орно
    fake_payments: bool = False

    # И-мэйл (вэб захиалгын хүргэлт). smtp_host хоосон бол и-мэйлийг email_outbox_dir-д .eml болгон хадгална
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "eSIM Mongolia <no-reply@example.com>"
    email_outbox_dir: str = "out/emails"

    # LLM (Groq, OpenAI-compatible API)
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # gpt-oss загваруудад: low | medium | high. Дэмждэггүй загвар сонговол хоосон үлдээнэ
    groq_reasoning_effort: str | None = "low"
    # Удаан идэвхгүй байвал шинэ яриа эхэлнэ
    conversation_idle_hours: int = 12
    # Загварт илгээх сүүлийн мессежийн тоо (үнэгүй түвшний токены хязгаарт багтаана)
    conversation_max_messages: int = 20

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


def missing_token_hint(name: str) -> str:
    """Тохиргоо олдохгүй үед .env файлын байдлыг тайлбарласан мессеж."""
    env = Path(".env").resolve()
    if env.exists():
        return f"{env} файлд {name}= мөрийн ард утга бичигдээгүй байна."
    if Path(".env.txt").exists():
        return f"{env} олдсонгүй, харин .env.txt байна. Нэрийг нь солино уу: Rename-Item .env.txt .env"
    return f"{env} файл олдсонгүй. Үүсгэнэ үү: copy .env.example .env  (Mac/Linux: cp .env.example .env)"


@lru_cache
def get_settings() -> Settings:
    return Settings()
