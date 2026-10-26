import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from .base import Keyboard

log = logging.getLogger(__name__)

MAX_TEXT = 4000  # Telegram-ын хязгаар 4096


@dataclass
class Incoming:
    kind: Literal["start", "text", "button"]
    chat_id: str
    value: str = ""  # текст эсвэл товчны data
    callback_id: str | None = None


def parse_update(update: dict[str, Any]) -> Incoming | None:
    if cq := update.get("callback_query"):
        message = cq.get("message") or {}
        chat = message.get("chat") or {}
        if "id" not in chat:
            return None
        return Incoming("button", str(chat["id"]), cq.get("data", ""), cq["id"])
    message = update.get("message")
    if not message or "text" not in message:
        return None
    if message["chat"].get("type") != "private":
        return None  # бүлгийн чатад хариулахгүй
    text = message["text"].strip()
    chat_id = str(message["chat"]["id"])
    if text.startswith("/start"):
        return Incoming("start", chat_id)
    return Incoming("text", chat_id, text)


def _markup(buttons: Keyboard | None) -> dict[str, Any] | None:
    if not buttons:
        return None
    return {
        "inline_keyboard": [
            [{"text": b.text, "url": b.url} if b.url else {"text": b.text, "callback_data": b.data} for b in row]
            for row in buttons
        ]
    }


class TelegramChannel:
    name = "telegram"

    def __init__(self, http: httpx.AsyncClient, token: str):
        self.http = http
        self.base = f"https://api.telegram.org/bot{token}"

    async def _call(self, method: str, files: dict[str, Any] | None = None, **params: Any) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        if files:
            data = {k: json.dumps(v) if isinstance(v, dict) else str(v) for k, v in params.items()}
            resp = await self.http.post(f"{self.base}/{method}", data=data, files=files)
        else:
            resp = await self.http.post(f"{self.base}/{method}", json=params)
        body = resp.json()
        if not body.get("ok"):
            log.error("Telegram %s failed: %s", method, body)
            raise RuntimeError(f"Telegram {method}: {body.get('description')}")
        return body["result"]

    async def send_text(self, chat_id: str, text: str, buttons: Keyboard | None = None) -> None:
        chunks = [text[i : i + MAX_TEXT] for i in range(0, len(text), MAX_TEXT)] or [""]
        for i, chunk in enumerate(chunks):
            last = i == len(chunks) - 1
            await self._call("sendMessage", chat_id=chat_id, text=chunk, reply_markup=_markup(buttons) if last else None)

    async def send_photo(self, chat_id: str, photo: bytes, caption: str = "", buttons: Keyboard | None = None) -> None:
        await self._call(
            "sendPhoto",
            files={"photo": ("qr.png", photo, "image/png")},
            chat_id=chat_id,
            caption=caption[:1024] or None,
            reply_markup=_markup(buttons),
        )

    async def answer_callback(self, callback_id: str) -> None:
        await self._call("answerCallbackQuery", callback_query_id=callback_id)

    async def set_webhook(self, url: str, secret: str) -> None:
        await self._call(
            "setWebhook",
            url=url,
            secret_token=secret or None,
            allowed_updates=["message", "callback_query"],
        )
