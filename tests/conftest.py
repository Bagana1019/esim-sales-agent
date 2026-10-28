import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from openai.types.chat import ChatCompletion

from esim_bot.agent.loop import SalesAgent
from esim_bot.agent.prompts import build_system_prompt
from esim_bot.bot import SalesBot
from esim_bot.catalog import Catalog
from esim_bot.db import Database
from esim_bot.fulfillment import PoolProvider
from esim_bot.inventory import import_esims, import_licenses
from esim_bot.orders import OrderService
from esim_bot.payments.fake import FakeQPay

ROOT = Path(__file__).parent.parent

# Тестийн eSIM сан: JP-5D-3GB ×2, KR-5D-1GBD, TH-8D-15GB, CN-3D-1GB — бусад багц дууссан.
# Лиценз: WIN11-PRO ×1 — бусад лиценз дууссан.
TEST_STOCK = """package_sku,iccid,smdp_address,activation_code
JP-5D-3GB,8981000000000000001,rsp.example-provider.com,TEST-ACTIVATION-CODE-0001
JP-5D-3GB,8981000000000000002,rsp.example-provider.com,TEST-ACTIVATION-CODE-0002
KR-5D-1GBD,8982000000000000001,rsp.example-provider.com,TEST-ACTIVATION-CODE-0003
TH-8D-15GB,8966000000000000001,rsp.example-provider.com,TEST-ACTIVATION-CODE-0004
CN-3D-1GB,8986000000000000001,rsp.example-provider.com,TEST-ACTIVATION-CODE-0005
"""


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
    return Catalog.load(ROOT / "tests" / "catalog.yaml")


@pytest.fixture
def qpay() -> FakeQPay:
    return FakeQPay()


@pytest.fixture
async def orders(db, catalog, qpay, tmp_path) -> OrderService:
    stock = tmp_path / "stock.csv"
    stock.write_text(TEST_STOCK, encoding="utf-8")
    await import_esims(db, stock)
    keys = tmp_path / "keys.csv"
    keys.write_text("package_sku,product_key\nWIN11-PRO,AAAAA-BBBBB-CCCCC-DDDDD-00001\n", encoding="utf-8")
    await import_licenses(db, keys)
    return OrderService(
        db,
        catalog,
        qpay,
        PoolProvider(db, catalog),
        public_base_url="https://bot.example",
        callback_secret="secret",
        low_stock_threshold=-1,  # нөөц багассан мэдэгдэл бусад тестийн мессежтэй холилдохгүй (тусгай тест асаана)
    )


async def take_stock(db, sku: str) -> None:
    """Өөр харилцагч сүүлийн нөөцийг түрүүлж худалдаж авсныг дуурайна."""
    from sqlalchemy import update

    from esim_bot.db import EsimProfile, LicenseKey

    async with db.session() as s, s.begin():
        for model in (EsimProfile, LicenseKey):
            await s.execute(update(model).where(model.package_sku == sku, model.order_id.is_(None)).values(order_id="other"))


@pytest.fixture
def channel() -> RecordingChannel:
    return RecordingChannel()


@pytest.fixture
def fake_llm() -> FakeCompletions:
    return FakeCompletions([])


@pytest.fixture
def agent(db, catalog, fake_llm) -> SalesAgent:
    client = SimpleNamespace(chat=SimpleNamespace(completions=fake_llm))
    return SalesAgent(
        client,
        db,
        system_prompt=build_system_prompt(catalog),
        model="openai/gpt-oss-120b",
        reasoning_effort="low",
        idle_hours=12,
        max_messages=20,
    )


@pytest.fixture
def bot(catalog, orders, agent, channel) -> SalesBot:
    return SalesBot(catalog, orders, agent, {channel.name: channel}, admin=(channel.name, "admin"))
