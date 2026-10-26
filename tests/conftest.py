import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from openai.types.chat import ChatCompletion

from esim_bot.agent.loop import SalesAgent
from esim_bot.bot import SalesBot
from esim_bot.catalog import Catalog
from esim_bot.db import Database
from esim_bot.esim import PoolProvider
from esim_bot.inventory import import_esims
from esim_bot.orders import OrderService
from esim_bot.payments.fake import FakeQPay

ROOT = Path(__file__).parent.parent


class RecordingChannel:
    name = "test"

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, chat_id, text, buttons=None):
        self.sent.append({"kind": "text", "chat_id": chat_id, "text": text, "buttons": buttons or []})

    async def send_photo(self, chat_id, photo, caption="", buttons=None):
        self.sent.append({"kind": "photo", "chat_id": chat_id, "text": caption, "buttons": buttons or [], "photo": photo})

    def button_data(self, index: int = -1) -> list[str]:
        return [b.data for row in self.sent[index]["buttons"] for b in row if b.data]


class FakeCompletions:
    """client.chat.completions-ийн оронд: бэлдсэн хариунууд (эсвэл exception)-ыг дарааллаар буцаана."""

    def __init__(self, responses: list[Any]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_completion(content: str | None = None, tool_calls: list[tuple[str, str, dict[str, Any] | str]] | None = None) -> ChatCompletion:
    """tool_calls: [(id, нэр, аргумент)] — аргумент dict эсвэл түүхий JSON мөр."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)},
            }
            for call_id, name, args in tool_calls
        ]
    return ChatCompletion.model_validate(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "openai/gpt-oss-120b",
            "choices": [{"index": 0, "finish_reason": "tool_calls" if tool_calls else "stop", "message": message}],
        }
    )


@pytest.fixture
async def db(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await database.create_all()
    yield database
    await database.dispose()


@pytest.fixture
def catalog() -> Catalog:
    return Catalog.load(ROOT / "data" / "catalog.yaml")


@pytest.fixture
def qpay() -> FakeQPay:
    return FakeQPay()


@pytest.fixture
async def orders(db, catalog, qpay) -> OrderService:
    await import_esims(db, ROOT / "data" / "esims.example.csv")
    return OrderService(
        db, catalog, qpay, PoolProvider(db), public_base_url="https://bot.example", callback_secret="secret"
    )


@pytest.fixture
def channel() -> RecordingChannel:
    return RecordingChannel()


@pytest.fixture
def fake_llm() -> FakeCompletions:
    return FakeCompletions([])


@pytest.fixture
def agent(db, fake_llm) -> SalesAgent:
    client = SimpleNamespace(chat=SimpleNamespace(completions=fake_llm))
    return SalesAgent(
        client, db, model="openai/gpt-oss-120b", reasoning_effort="low", idle_hours=12, max_messages=20
    )


@pytest.fixture
def bot(catalog, orders, agent, channel) -> SalesBot:
    return SalesBot(catalog, orders, agent, {channel.name: channel}, admin=(channel.name, "admin"))
