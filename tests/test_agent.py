import json

from sqlalchemy import select

from esim_bot.agent.loop import REFUSAL_REPLY
from esim_bot.agent.prompts import SYSTEM_PROMPT
from esim_bot.agent.tools import TOOLS, ToolContext
from esim_bot.db import Conversation

from .conftest import make_message

THINKING = {"type": "thinking", "thinking": "", "signature": "sig-abc"}


def tool_use(name, args, id="tu_1"):
    return make_message([THINKING, {"type": "tool_use", "id": id, "name": name, "input": args}], "tool_use")


def text(t):
    return make_message([{"type": "text", "text": t}], "end_turn")


async def saved_messages(db):
    async with db.session() as s:
        conv = await s.scalar(select(Conversation).order_by(Conversation.id.desc()))
        return conv.messages


async def test_tool_loop_and_request_shape(agent, bot, channel, fake_messages, db):
    fake_messages.responses = [tool_use("list_packages", {"country_code": "JP"}), text("Японд 3 багц байна.")]

    reply = await agent.reply(ToolContext(bot, channel, "u1"), "Японы багцууд?")

    assert reply == "Японд 3 багц байна."
    first, second = fake_messages.calls
    assert first["model"] == "claude-opus-5-5"
    assert first["system"] == SYSTEM_PROMPT and first["tools"] == TOOLS
    assert first["fallbacks"] == "default" and first["betas"] == ["server-side-fallback-2026-07-01"]
    assert first["output_config"] == {"effort": "low"}

    tool_result = second["messages"][2]["content"][0]
    assert tool_result["tool_use_id"] == "tu_1" and "is_error" not in tool_result
    assert [p["sku"] for p in json.loads(tool_result["content"])] == ["JP-5D-2GB", "JP-10D-2GB", "JP-15D-20GB"]
    # thinking блок өөрчлөлтгүй буцаж очно
    assert second["messages"][1]["content"][0] == THINKING

    history = await saved_messages(db)
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]


async def test_history_is_append_only(agent, bot, channel, fake_messages, db):
    ctx = ToolContext(bot, channel, "u1")
    fake_messages.responses = [tool_use("list_countries", {}), text("Япон, Солонгос, Тайланд.")]
    await agent.reply(ctx, "Аль улсууд байна?")
    first_turn = await saved_messages(db)

    fake_messages.responses = [text("Тийм.")]
    await agent.reply(ctx, "Баярлалаа")

    sent = fake_messages.calls[-1]["messages"]
    assert sent[: len(first_turn)] == first_turn
    assert sent[len(first_turn)] == {"role": "user", "content": "Баярлалаа"}


async def test_create_payment_tool_sends_invoice(agent, bot, channel, fake_messages):
    fake_messages.responses = [tool_use("create_payment", {"package_sku": "KR-5D-2GB"}), text("QR илгээлээ.")]

    await agent.reply(ToolContext(bot, channel, "u1"), "KR 5 хоногийнхыг авъя")

    assert channel.sent[-1]["kind"] == "photo"
    assert "22,000₮" in channel.sent[-1]["text"]
    result = json.loads(fake_messages.calls[1]["messages"][2]["content"][0]["content"])
    assert result["qr_sent"] is True


async def test_unknown_package_is_tool_error(agent, bot, channel, fake_messages):
    fake_messages.responses = [tool_use("create_payment", {"package_sku": "XX-1"}), text("Уучлаарай.")]

    await agent.reply(ToolContext(bot, channel, "u1"), "XX авъя")

    tool_result = fake_messages.calls[1]["messages"][2]["content"][0]
    assert tool_result["is_error"] is True
    assert channel.sent == []


async def test_refusal_is_not_saved(agent, bot, channel, fake_messages, db):
    ctx = ToolContext(bot, channel, "u1")
    fake_messages.responses = [text("Сайн байна уу!")]
    await agent.reply(ctx, "Сайн уу")
    before = await saved_messages(db)

    fake_messages.responses = [make_message([], "refusal")]
    assert await agent.reply(ctx, "...") == REFUSAL_REPLY
    assert await saved_messages(db) == before


async def test_start_resets_conversation(agent, bot, channel, fake_messages):
    ctx = ToolContext(bot, channel, "u1")
    fake_messages.responses = [text("Сайн байна уу!")]
    await agent.reply(ctx, "Сайн уу")

    await bot.handle_start(channel, "u1")
    fake_messages.responses = [text("Сайн байна уу!")]
    await agent.reply(ctx, "Дахин сайн уу")

    assert fake_messages.calls[-1]["messages"] == [{"role": "user", "content": "Дахин сайн уу"}]
