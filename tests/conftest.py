import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from anthropic.types.beta import BetaMessage

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


class FakeMessages:
    """client.beta.messages-ийн оронд: бэлдсэн хариунуудыг дарааллаар буцаана."""

    def __init__(self, responses: list[BetaMessage]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return self.responses.pop(0)


def make_message(content: list[dict[str, Any]], stop_reason: str) -> BetaMessage:
    return BetaMessage.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10},
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
def fake_messages() -> FakeMessages:
    return FakeMessages([])


@pytest.fixture
def agent(db, fake_messages) -> SalesAgent:
    client = SimpleNamespace(beta=SimpleNamespace(messages=fake_messages))
    return SalesAgent(client, db, model="claude-opus-5-5", effort="low", idle_hours=12, max_messages=60)


@pytest.fixture
def bot(catalog, orders, agent, channel) -> SalesBot:
    return SalesBot(catalog, orders, agent, {channel.name: channel}, admin=(channel.name, "admin"))
