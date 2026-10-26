import asyncio
import json

import httpx
import pytest
import respx

from esim_bot.channels.base import Button
from esim_bot.channels.telegram import _markup, parse_update
from esim_bot.cli import poll
from esim_bot.config import get_settings


def message(text, chat_type="private"):
    return {"update_id": 1, "message": {"message_id": 5, "chat": {"id": 42, "type": chat_type}, "text": text}}


def test_parse_start_and_text():
    start = parse_update(message("/start"))
    assert start.kind == "start" and start.chat_id == "42"
    txt = parse_update(message("  Японд явна  "))
    assert txt.kind == "text" and txt.value == "Японд явна"


def test_parse_callback():
    update = {"callback_query": {"id": "cb1", "data": "c:JP", "message": {"chat": {"id": 42, "type": "private"}}}}
    incoming = parse_update(update)
    assert (incoming.kind, incoming.chat_id, incoming.value, incoming.callback_id) == ("button", "42", "c:JP", "cb1")


def test_ignores_groups_and_non_text():
    assert parse_update(message("hi", chat_type="group")) is None
    assert parse_update({"update_id": 1, "message": {"chat": {"id": 1, "type": "private"}, "photo": []}}) is None


def test_markup():
    markup = _markup([[Button("Япон", data="c:JP")], [Button("Төлөх", url="https://s.qpay.mn/x")]])
    assert markup == {
        "inline_keyboard": [
            [{"text": "Япон", "callback_data": "c:JP"}],
            [{"text": "Төлөх", "url": "https://s.qpay.mn/x"}],
        ]
    }
    assert _markup(None) is None


# --- Polling горим: Telegram API-г respx-ээр дуурайлгаж бүтэн давталтыг турших ---

TG = "https://api.telegram.org/bot123:abc"


def ok(result):
    return httpx.Response(200, json={"ok": True, "result": result})


def callback(update_id, data):
    chat = {"chat": {"id": 42, "type": "private"}}
    return {"update_id": update_id, "callback_query": {"id": f"cb{update_id}", "data": data, "message": chat}}


@respx.mock
async def test_poll_handles_updates_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/poll.db")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    get_settings.cache_clear()

    respx.post(f"{TG}/deleteWebhook").mock(return_value=ok(True))
    respx.post(f"{TG}/getMe").mock(return_value=ok({"username": "test_esim_bot"}))
    answer = respx.post(f"{TG}/answerCallbackQuery").mock(return_value=ok(True))
    send = respx.post(f"{TG}/sendMessage").mock(return_value=ok({}))
    photo = respx.post(f"{TG}/sendPhoto").mock(return_value=ok({}))

    batches = iter(
        [
            [message("/start") | {"update_id": 1}],
            [callback(2, "c:JP")],
            [callback(3, "p:JP-5D-3GB")],
            [message("/pay") | {"update_id": 4}],
        ]
    )

    def get_updates(request):
        try:
            return ok(next(batches))
        except StopIteration:
            raise asyncio.CancelledError  # давталтыг зогсооно (Ctrl+C-тэй адил)

    updates = respx.post(f"{TG}/getUpdates").mock(side_effect=get_updates)

    with pytest.raises(asyncio.CancelledError):
        await poll(fake_payments=True)
    get_settings.cache_clear()

    texts = [json.loads(c.request.read())["text"] for c in send.calls]
    assert any("Сайн байна уу" in t for t in texts)
    assert any("Япон - багцаа сонгоно уу" in t for t in texts)
    assert any(t.startswith("(Туршилт)") for t in texts)
    assert photo.call_count == 1  # нэхэмжлэхийн QR
    assert answer.call_count == 2
    # offset бүр өмнөх update_id + 1 байх ёстой (нэг мессежийг хоёр удаа боловсруулахгүй)
    offsets = [json.loads(c.request.read()).get("offset") for c in updates.calls]
    assert offsets[:4] == [None, 2, 3, 4]
