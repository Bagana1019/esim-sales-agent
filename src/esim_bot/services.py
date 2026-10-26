from dataclasses import dataclass

import httpx
from anthropic import AsyncAnthropic

from .agent.loop import SalesAgent
from .bot import SalesBot
from .catalog import Catalog
from .channels.base import Channel
from .channels.telegram import TelegramChannel
from .config import Settings
from .db import Database
from .esim import PoolProvider
from .orders import OrderService
from .payments.qpay import PaymentGateway, QPayClient


@dataclass
class Services:
    http: httpx.AsyncClient
    db: Database
    orders: OrderService
    bot: SalesBot
    telegram: TelegramChannel | None

    async def aclose(self) -> None:
        await self.http.aclose()
        await self.db.dispose()


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
        PoolProvider(db),
        public_base_url=settings.public_base_url,
        callback_secret=settings.callback_secret,
    )
    agent = SalesAgent(
        AsyncAnthropic(api_key=settings.anthropic_api_key),
        db,
        model=settings.anthropic_model,
        effort=settings.anthropic_effort,
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
    return Services(http=http, db=db, orders=orders, bot=bot, telegram=telegram)
