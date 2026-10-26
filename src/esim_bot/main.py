"""FastAPI сервер: Telegram webhook, QPay callback.

Ажиллуулах: uv run uvicorn esim_bot.main:app --port 8000
"""

import hmac
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .channels.telegram import Incoming, parse_update
from .config import get_settings
from .services import Services, build_services

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if not settings.telegram_webhook_secret:
        log.warning("TELEGRAM_WEBHOOK_SECRET тохируулаагүй — webhook-ийг хэн ч дуудаж болно")
    app.state.settings = settings
    app.state.services = await build_services(settings)
    yield
    await app.state.services.aclose()


app = FastAPI(title="eSIM sales agent", lifespan=lifespan)


async def _run_logged(fn: Callable[..., Awaitable[Any]], *args: Any) -> None:
    try:
        await fn(*args)
    except Exception:
        log.exception("Background task %s failed", fn.__name__)


async def _handle_telegram(services: Services, incoming: Incoming) -> None:
    tg, bot = services.telegram, services.bot
    assert tg is not None
    if incoming.callback_id:
        await tg.answer_callback(incoming.callback_id)
    if incoming.kind == "start":
        await bot.handle_start(tg, incoming.chat_id)
    elif incoming.kind == "button":
        await bot.handle_button(tg, incoming.chat_id, incoming.value)
    else:
        await bot.handle_text(tg, incoming.chat_id, incoming.value)


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
    # Telegram-д шууд 200 буцааж, удаан ажиллах хэсгийг (Claude, QPay) ард нь хийнэ
    if incoming := parse_update(await request.json()):
        background.add_task(_run_logged, _handle_telegram, services, incoming)
    return {"ok": True}


@app.api_route("/qpay/callback", methods=["GET", "POST"])
async def qpay_callback(order_id: str, token: str, request: Request, background: BackgroundTasks) -> PlainTextResponse:
    services: Services = request.app.state.services
    if not services.orders.verify_callback_token(order_id, token):
        raise HTTPException(status_code=403)
    # Callback-д итгэхгүй: on_payment_notification QPay-ээс төлбөрийг дахин шалгана
    background.add_task(_run_logged, services.bot.on_payment_notification, order_id)
    return PlainTextResponse("SUCCESS")
