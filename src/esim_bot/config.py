from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # utf-8-sig: Windows Notepad-ийн BOM-той UTF-8 файлыг ч зөв уншина
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8-sig", extra="ignore")

    public_base_url: str = "http://localhost:8000"
    database_url: str = "sqlite+aiosqlite:///./esim.db"
    catalog_path: str = "data/catalog.yaml"
    shop_name: str = "NomadData"
    # Локал туршилт: QPay-ийн оронд хуурамч төлбөр, туршилтын eSIM/лиценз автоматаар санд орно
    fake_payments: bool = False
    # Борлуулалтын дараа нөөц энэ тоо буюу түүнээс доош болвол админд мэдэгдэнэ
    low_stock_threshold: int = 3

    # И-мэйл (вэб захиалгын хүргэлт): Mailgun, үгүй бол SMTP. Аль нь ч тохируулаагүй бол илгээхгүй,
    # email_outbox_dir-д .eml файл болгон хадгална
    mailgun_api_key: str = ""
    mailgun_domain: str = ""
    mailgun_region: str = "us"  # us | eu — домэйноо аль бүсэд үүсгэснээс хамаарна
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    # Илгээгч. Хоосон бол "SHOP_NAME <no-reply@MAILGUN_DOMAIN>". Хуучин SMTP_FROM нэрийг ч уншина
    mail_from: str = Field("", validation_alias=AliasChoices("mail_from", "smtp_from"))
    email_outbox_dir: str = "out/emails"

    @property
    def sender(self) -> str:
        # example.com бол .env.example-ийн жишээ утга — жинхэнэ илгээгч биш
        if self.mail_from and not self.mail_from.rstrip(">").endswith("@example.com"):
            return self.mail_from
        return f"{self.shop_name} <no-reply@{self.mailgun_domain or 'example.com'}>"

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

    @property
    def qpay_sandbox(self) -> bool:
        return "sandbox" in self.qpay_base_url

    def production_errors(self) -> list[str]:
        """Жинхэнэ QPay-д (sandbox биш) асахаас өмнө заавал засах тохиргоо."""
        if self.fake_payments or self.qpay_sandbox:
            return []
        errors = []
        if self.callback_secret == "change-me" or len(self.callback_secret) < 24:
            # Захиалгын холбоос (QR код, түлхүүр) энэ нууцаар гарын үсэглэгддэг
            errors.append(
                "CALLBACK_SECRET-д 24-өөс урт санамсаргүй утга бичнэ. Үүсгэх: "
                'uv run python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        if "TEST_" in self.qpay_client_id + self.qpay_invoice_code:
            errors.append("QPAY_CLIENT_ID, QPAY_CLIENT_SECRET, QPAY_INVOICE_CODE-д QPay-ээс авсан утгаа бичнэ")
        return errors


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
