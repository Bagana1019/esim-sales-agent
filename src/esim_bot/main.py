"""FastAPI сервер: Telegram webhook, QPay callback.

Ажиллуулах: uv run uvicorn esim_bot.main:app --port 8000
"""

import asyncio
import hmac
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .backup import backup_database
from .channels.telegram import dispatch, parse_update
from .config import Settings, get_settings
from .payments.fake import FakeQPay
from .services import ConfigError, Services, build_services
from .web.routes import STATIC_DIR
from .web.routes import router as web_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # хүсэлтийн URL-д bot token орсон байдаг
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if not settings.telegram_webhook_secret:
        log.warning("TELEGRAM_WEBHOOK_SECRET тохируулаагүй — webhook-ийг хэн ч дуудаж болно")
    app.state.settings = settings
    try:
        app.state.services = await build_services(settings)
    except ConfigError as e:
        log.error("%s", e)
        raise
    payments = app.state.services.payments
    if isinstance(payments, FakeQPay):
        log.warning("FAKE_PAYMENTS=true — хуурамч төлбөр, жинхэнэ мөнгө авахгүй")
    elif settings.qpay_sandbox:
        log.warning("QPay sandbox горим — жинхэнэ мөнгө хөдлөхгүй")
    elif "localhost" in settings.public_base_url or "127.0.0.1" in settings.public_base_url:
        log.warning("PUBLIC_BASE_URL локал тул QPay callback ирэхгүй — төлбөрийг захиалгын хуудас нээлттэй үед шалгана")
    log.info("И-мэйл: %s", app.state.services.web.mailer)
    if settings.live_payments and (missing := settings.missing_company_info()):
        log.warning("Үйлчилгээний нөхцөл, нууцлалын хуудсанд хоосон харагдана — .env-д бичнэ үү: %s", ", ".join(missing))
    backups = None
    if settings.live_payments and settings.backup_interval_hours > 0:
        backups = asyncio.create_task(_backup_loop(settings, app.state.services))
    yield
    if backups:
        backups.cancel()
    await app.state.services.aclose()


async def _backup_loop(settings: Settings, services: Services) -> None:
    """Асахдаа, дараа нь BACKUP_INTERVAL_HOURS тутам сангийн нөөц хуулбар хийнэ. Амжилтгүй бол админд мэдэгдэнэ."""
    while True:
        try:
            info = await asyncio.to_thread(
                backup_database, settings.database_url, settings.backup_dir, settings.backup_keep
            )
            log.info("Нөөц хуулбар: %s (%d захиалга)", info.path, info.orders)
        except Exception as e:
            log.exception("Нөөц хуулбар хийж чадсангүй")
            await services.bot.notify_admin(f"⚠️ Өгөгдлийн сангийн нөөц хуулбар хийж чадсангүй: {e}")
        await asyncio.sleep(settings.backup_interval_hours * 3600)


app = FastAPI(title="NomadData", lifespan=lifespan)
app.include_router(web_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


async def _run_logged(fn: Callable[..., Awaitable[Any]], *args: Any) -> None:
    try:
        await fn(*args)
    except Exception:
        log.exception("Background task %s failed", fn.__name__)


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    background: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    settings, services = request.app.state.settings, request.app.state.services
    secret = settings.telegram_webhook_secret
    if secret and not hmac.compare_digest(x_telegram_bot_api_secret_token or "", secret):
        raise HTTPException(status_code=403)
    if services.telegram is None:
        raise HTTPException(status_code=503, detail="Telegram not configured")
    # Telegram-д шууд 200 буцааж, удаан ажиллах хэсгийг (LLM, QPay) ард нь хийнэ
    if incoming := parse_update(await request.json()):
        background.add_task(dispatch, services.bot, services.telegram, incoming)
    return {"ok": True}


@app.api_route("/qpay/callback", methods=["GET", "POST"])
async def qpay_callback(order_id: str, token: str, request: Request, background: BackgroundTasks) -> PlainTextResponse:
    services: Services = request.app.state.services
    if not services.orders.verify_callback_token(order_id, token):
        raise HTTPException(status_code=403)
    # Callback-д итгэхгүй: аль аль нь QPay-ээс төлбөрийг дахин шалгана
    order = await services.orders.get(order_id)
    if order is not None and order.channel == services.web.channel:
        background.add_task(_run_logged, services.web.process, order_id)
    else:
        background.add_task(_run_logged, services.bot.on_payment_notification, order_id)
    return PlainTextResponse("SUCCESS")
