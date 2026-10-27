from dataclasses import dataclass

import httpx
from openai import AsyncOpenAI

from .agent.loop import SalesAgent
from .agent.prompts import build_system_prompt
from .bot import SalesBot
from .catalog import Catalog
from .channels.base import Channel
from .channels.telegram import TelegramChannel
from .config import Settings
from .db import Database
from .fulfillment import PoolProvider
from .inventory import seed_demo_stock
from .mailer import FileMailer, Mailer, MailgunMailer, SmtpMailer
from .orders import OrderService
from .payments.fake import FakeQPay
from .payments.qpay import PaymentGateway, QPayClient
from .web.store import WebStore


@dataclass
class Services:
    http: httpx.AsyncClient
    db: Database
    orders: OrderService
    bot: SalesBot
    web: WebStore
    payments: PaymentGateway
    telegram: TelegramChannel | None

    async def aclose(self) -> None:
        await self.http.aclose()
        await self.db.dispose()


def build_mailer(settings: Settings, http: httpx.AsyncClient) -> Mailer:
    """Mailgun тохируулсан бол Mailgun, үгүй бол SMTP, аль нь ч үгүй бол .eml файл."""
    if settings.mailgun_api_key and settings.mailgun_domain:
        return MailgunMailer(
            http, settings.mailgun_api_key, settings.mailgun_domain, settings.sender, settings.mailgun_region
        )
    if settings.smtp_host:
        return SmtpMailer(
            settings.smtp_host, settings.smtp_port, settings.smtp_username, settings.smtp_password, settings.sender
        )
    return FileMailer(settings.email_outbox_dir, settings.sender)


async def build_services(
    settings: Settings,
    *,
    payments: PaymentGateway | None = None,
    extra_channels: dict[str, Channel] | None = None,
) -> Services:
    http = httpx.AsyncClient(timeout=30)
    db = Database(settings.database_url)
    await db.create_all()
    catalog = Catalog.load(settings.catalog_path)

    if payments is None and settings.fake_payments:
        payments = FakeQPay()
    if isinstance(payments, FakeQPay):
        await seed_demo_stock(db, catalog)
    if payments is None:
        payments = QPayClient(
            http,
            base_url=settings.qpay_base_url,
            client_id=settings.qpay_client_id,
            client_secret=settings.qpay_client_secret,
            invoice_code=settings.qpay_invoice_code,
        )
    orders = OrderService(
        db,
        catalog,
        payments,
        PoolProvider(db, catalog),
        public_base_url=settings.public_base_url,
        callback_secret=settings.callback_secret,
    )
    llm = AsyncOpenAI(api_key=settings.groq_api_key, base_url=settings.groq_base_url) if settings.groq_api_key else None
    agent = SalesAgent(
        llm,
        db,
        system_prompt=build_system_prompt(catalog),
        model=settings.groq_model,
        reasoning_effort=settings.groq_reasoning_effort,
        idle_hours=settings.conversation_idle_hours,
        max_messages=settings.conversation_max_messages,
    )

    channels: dict[str, Channel] = dict(extra_channels or {})
    telegram = None
    if settings.telegram_bot_token:
        telegram = TelegramChannel(http, settings.telegram_bot_token)
        channels[telegram.name] = telegram
    admin = ("telegram", settings.admin_chat_id) if telegram and settings.admin_chat_id else None

    bot = SalesBot(catalog, orders, agent, channels, admin)

    web = WebStore(
        catalog,
        orders,
        build_mailer(settings, http),
        shop_name=settings.shop_name,
        public_base_url=settings.public_base_url,
        notify_admin=bot.notify_admin,
    )
    return Services(http=http, db=db, orders=orders, bot=bot, web=web, payments=payments, telegram=telegram)
