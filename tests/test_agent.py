import json

import httpx2
import openai
from sqlalchemy import select

from esim_bot.agent.loop import BUSY_REPLY, DISABLED_REPLY, SalesAgent
from esim_bot.agent.prompts import SYSTEM_PROMPT
from esim_bot.agent.tools import TOOLS, ToolContext
from esim_bot.db import Conversation

from .conftest import make_completion


def tool_call(name, args, id="call_1"):
    return make_completion(tool_calls=[(id, name, args)])


def text(t):
    return make_completion(content=t)


def groq_error(status, code):
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://api.groq.com/openai/v1/chat/completions"))
    error_cls = openai.BadRequestError if status == 400 else openai.RateLimitError
    return error_cls(code, response=response, body={"code": code, "message": code})


async def saved_messages(db):
    async with db.session() as s:
        conv = await s.scalar(select(Conversation).order_by(Conversation.id.desc()))
        return conv.messages


async def test_tool_loop_and_request_shape(agent, bot, channel, fake_llm, db):
    fake_llm.responses = [tool_call("list_packages", {"country_code": "JP"}), text("Японд 2 багц байна.")]

    reply = await agent.reply(ToolContext(bot, channel, "u1"), "Японы багцууд?")

    assert reply == "Японд 2 багц байна."
    first, second = fake_llm.calls
    assert first["model"] == "openai/gpt-oss-120b"
    assert first["tools"] == TOOLS and first["reasoning_effort"] == "low"
    assert first["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}

    tool_msg = second["messages"][3]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_1"
    assert [p["sku"] for p in json.loads(tool_msg["content"])] == ["JP-5D-3GB", "JP-7D-5GB"]

    history = await saved_messages(db)
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "assistant"]
    assert history[1]["tool_calls"][0]["function"]["name"] == "list_packages"


async def test_history_is_append_only(agent, bot, channel, fake_llm, db):
    ctx = ToolContext(bot, channel, "u1")
    fake_llm.responses = [tool_call("list_countries", {}), text("Хятад, БНСУ, Япон, Тайланд.")]
    await agent.reply(ctx, "Аль улсууд байна?")
    first_turn = await saved_messages(db)

    fake_llm.responses = [text("Тийм.")]
    await agent.reply(ctx, "Баярлалаа")

    sent = fake_llm.calls[-1]["messages"][1:]  # system-ийг алгасна
    assert sent[: len(first_turn)] == first_turn
    assert sent[len(first_turn)] == {"role": "user", "content": "Баярлалаа"}


async def test_create_payment_tool_sends_invoice(agent, bot, channel, fake_llm):
    fake_llm.responses = [tool_call("create_payment", {"package_sku": "KR-5D-1GBD"}), text("QR илгээлээ.")]

    await agent.reply(ToolContext(bot, channel, "u1"), "БНСУ 5 хоногийнхыг авъя")

    assert channel.sent[-1]["kind"] == "photo"
    assert "25,000₮" in channel.sent[-1]["text"]
    result = json.loads(fake_llm.calls[1]["messages"][3]["content"])
    assert result["qr_sent"] is True


async def test_bad_tool_input_is_reported_to_model(agent, bot, channel, fake_llm):
    fake_llm.responses = [
        make_completion(
            tool_calls=[
                ("call_1", "create_payment", {"package_sku": "XX-1"}),  # байхгүй багц
                ("call_2", "list_packages", "{not json"),  # эвдэрсэн JSON
                ("call_3", "list_packages", {"country": "JP"}),  # буруу аргументын нэр
            ]
        ),
        text("Уучлаарай."),
    ]

    assert await agent.reply(ToolContext(bot, channel, "u1"), "XX авъя") == "Уучлаарай."

    results = [m["content"] for m in fake_llm.calls[1]["messages"] if m["role"] == "tool"]
    assert len(results) == 3 and all(r.startswith("Алдаа") for r in results)
    assert channel.sent == []


async def test_retries_once_on_groq_tool_use_failed(agent, bot, channel, fake_llm):
    fake_llm.responses = [groq_error(400, "tool_use_failed"), text("Сайн байна уу!")]

    assert await agent.reply(ToolContext(bot, channel, "u1"), "Сайн уу") == "Сайн байна уу!"
    assert len(fake_llm.calls) == 2


async def test_rate_limit_reply_and_nothing_saved(agent, bot, channel, fake_llm, db):
    ctx = ToolContext(bot, channel, "u1")
    fake_llm.responses = [text("Сайн байна уу!")]
    await agent.reply(ctx, "Сайн уу")
    before = await saved_messages(db)

    fake_llm.responses = [groq_error(429, "rate_limit_exceeded")]
    assert await agent.reply(ctx, "...") == BUSY_REPLY
    assert await saved_messages(db) == before


async def test_only_recent_messages_are_sent(agent, bot, channel, fake_llm):
    ctx = ToolContext(bot, channel, "u1")
    for i in range(15):
        fake_llm.responses = [tool_call("list_countries", {}, id=f"c{i}"), text(f"хариу {i}")]
        await agent.reply(ctx, f"асуулт {i}")

    sent = fake_llm.calls[-1]["messages"][1:]
    assert len(sent) <= 20
    assert sent[0]["role"] == "user"  # tool хариу өнчрөөгүй
    user_texts = [m["content"] for m in sent if m["role"] == "user"]
    assert "асуулт 14" in user_texts and "асуулт 0" not in user_texts


async def test_without_api_key_replies_with_menu_hint(db, bot, channel):
    agent = SalesAgent(None, db, model="m", idle_hours=12, max_messages=20)
    assert await agent.reply(ToolContext(bot, channel, "u1"), "Сайн уу") == DISABLED_REPLY


async def test_start_resets_conversation(agent, bot, channel, fake_llm):
    ctx = ToolContext(bot, channel, "u1")
    fake_llm.responses = [text("Сайн байна уу!")]
    await agent.reply(ctx, "Сайн уу")

    await bot.handle_start(channel, "u1")
    fake_llm.responses = [text("Сайн байна уу!")]
    await agent.reply(ctx, "Дахин сайн уу")

    assert fake_llm.calls[-1]["messages"][1:] == [{"role": "user", "content": "Дахин сайн уу"}]


def test_to_plain_text_strips_markdown():
    from esim_bot.agent.loop import to_plain_text

    raw = "## Багцууд\n* **7 хоног / Нийт 5GB** – 38,000 ₮\n- `JP` __тохиромжтой__\n\n5 * 2 = 10"
    assert to_plain_text(raw) == "Багцууд\n• 7 хоног / Нийт 5GB – 38,000 ₮\n• JP тохиромжтой\n\n5 * 2 = 10"
