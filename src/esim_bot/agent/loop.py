"""Claude-тэй ярилцах agent loop (Messages API + tool use, гараар бичсэн давталт)."""

import logging
from datetime import timedelta
from typing import Any

import anthropic
from anthropic import AsyncAnthropic
from sqlalchemy import select

from ..db import Conversation, Database, utcnow
from .prompts import SYSTEM_PROMPT
from .tools import TOOLS, ToolContext, ToolError, run_tool

log = logging.getLogger(__name__)

# Аюулгүй байдлын шүүлтүүр хүсэлтээс татгалзвал сервер өөр загвар дээр дахин ажиллуулна
FALLBACK_BETA = "server-side-fallback-2026-07-01"

ERROR_REPLY = "Уучлаарай, түр алдаа гарлаа. Дахин бичнэ үү эсвэл /start дарж цэснээс сонгоорой."
REFUSAL_REPLY = "Уучлаарай, энэ асуултад хариулж чадахгүй. eSIM багц, захиалгын талаар асуух зүйл байвал бичээрэй."
STEP_LIMIT_REPLY = "Уучлаарай, хүсэлтийг гүйцээж чадсангүй. Арай өөрөөр асууж үзнэ үү эсвэл /start дарна уу."


class SalesAgent:
    def __init__(
        self,
        client: AsyncAnthropic,
        db: Database,
        *,
        model: str,
        effort: str,
        idle_hours: int,
        max_messages: int,
        max_steps: int = 8,
    ):
        self.client = client
        self.db = db
        self.model = model
        self.effort = effort
        self.idle_after = timedelta(hours=idle_hours)
        self.max_messages = max_messages
        self.max_steps = max_steps

    async def reset(self, channel: str, chat_id: str) -> None:
        async with self.db.session() as s, s.begin():
            s.add(Conversation(channel=channel, chat_id=chat_id, messages=[]))

    async def reply(self, ctx: ToolContext, text: str) -> str:
        conv = await self._current_conversation(ctx.channel.name, ctx.chat_id)
        # Түүхийг зөвхөн нэмж бичнэ: өмнөх мессежийг засвал thinking блокууд хүчингүй болно
        messages = [*conv.messages, {"role": "user", "content": text}]
        try:
            reply, keep = await self._run(ctx, messages)
        except anthropic.BadRequestError:
            # Түүх эвдэрсэн байж болзошгүй — шинэ яриа эхлүүлнэ
            log.exception("Claude 400, chat %s: яриаг шинээр эхлүүлж байна", ctx.chat_id)
            await self.reset(ctx.channel.name, ctx.chat_id)
            return ERROR_REPLY
        except anthropic.APIError:
            # 429, 5xx, сүлжээний алдаа — SDK өөрөө дахин оролдсоны дараа ч бүтэлгүйтсэн
            log.exception("Claude API алдаа, chat %s", ctx.chat_id)
            return ERROR_REPLY
        if keep:
            await self._save(conv.id, messages)
        return reply

    async def _run(self, ctx: ToolContext, messages: list[Any]) -> tuple[str, bool]:
        """messages-д шинэ эргэлтүүдийг нэмнэ. (хариу, түүхийг хадгалах эсэх) буцаана."""
        for _ in range(self.max_steps):
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
                output_config={"effort": self.effort},
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
            if response.stop_reason == "refusal":
                # Татгалзсан эргэлтийг түүхэнд оруулахгүй — хадгалсан түүх өөрчлөгдөхгүй
                log.warning("Refusal, chat %s: %s", ctx.chat_id, response.stop_details)
                return REFUSAL_REPLY, False

            messages.append(
                {"role": "assistant", "content": [b.to_dict(mode="json") for b in response.content]}
            )
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason == "tool_use":
                results = [await self._call_tool(ctx, b.id, b.name, b.input) for b in tool_uses]
                messages.append({"role": "user", "content": results})
                continue
            if tool_uses:
                # max_tokens-оор таслагдсан tool_use бүрд хариу байх ёстой, эс бөгөөс дараагийн хүсэлт 400 болно
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": b.id, "content": "Not executed.", "is_error": True}
                            for b in tool_uses
                        ],
                    }
                )
            return "\n".join(b.text for b in response.content if b.type == "text").strip(), True

        log.warning("Agent step limit reached, chat %s", ctx.chat_id)
        return STEP_LIMIT_REPLY, True

    async def _call_tool(self, ctx: ToolContext, tool_use_id: str, name: str, args: Any) -> dict[str, Any]:
        try:
            content, is_error = await run_tool(ctx, name, args), False
        except ToolError as e:
            content, is_error = str(e), True
        except Exception:
            log.exception("Tool %s failed", name)
            content, is_error = "Дотоод алдаа гарлаа. Харилцагчид түр хүлээхийг эсвэл дахин оролдохыг санал болго.", True
        log.info("tool %s(%s) -> %s", name, args, content[:200])
        result: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
        if is_error:
            result["is_error"] = True
        return result

    async def _current_conversation(self, channel: str, chat_id: str) -> Conversation:
        async with self.db.session() as s, s.begin():
            conv = await s.scalar(
                select(Conversation)
                .where(Conversation.channel == channel, Conversation.chat_id == chat_id)
                .order_by(Conversation.id.desc())
                .limit(1)
            )
            if (
                conv is None
                or conv.updated_at < utcnow() - self.idle_after
                or len(conv.messages) >= self.max_messages
            ):
                conv = Conversation(channel=channel, chat_id=chat_id, messages=[])
                s.add(conv)
        return conv

    async def _save(self, conversation_id: int, messages: list[Any]) -> None:
        async with self.db.session() as s, s.begin():
            conv = await s.get(Conversation, conversation_id)
            conv.messages = messages
            conv.updated_at = utcnow()
