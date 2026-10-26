"""LLM-тэй ярилцах agent loop (OpenAI-compatible Chat Completions + tool calling, Groq)."""

import json
import logging
import re
from datetime import timedelta
from typing import Any

import openai
from openai import AsyncOpenAI
from sqlalchemy import select

from ..db import Conversation, Database, utcnow
from .tools import TOOLS, ToolContext, ToolError, run_tool

log = logging.getLogger(__name__)

ERROR_REPLY = "Уучлаарай, түр алдаа гарлаа. Дахин бичнэ үү эсвэл /start дарж цэснээс сонгоорой."
BUSY_REPLY = "Уучлаарай, яг одоо ачаалал ихтэй байна. Хэсэг хүлээгээд дахин бичнэ үү эсвэл /start дарж цэснээс сонгоорой."
DISABLED_REPLY = "Уучлаарай, одоогоор зөвхөн цэсээр үйлчилж байна. /start дарж улсаа сонгоорой."
STEP_LIMIT_REPLY = "Уучлаарай, хүсэлтийг гүйцээж чадсангүй. Арай өөрөөр асууж үзнэ үү эсвэл /start дарна уу."


def to_plain_text(text: str) -> str:
    """Загвар prompt-ыг үл тоон Markdown бичвэл чатад ** # ` тэмдэгтүүд харагдахгүйгээр цэвэрлэнэ."""
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text, flags=re.S)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*[*-]\s+", "• ", text, flags=re.M)
    return re.sub(r"`([^`\n]+)`", r"\1", text).strip()


class SalesAgent:
    def __init__(
        self,
        client: AsyncOpenAI | None,
        db: Database,
        *,
        system_prompt: str,
        model: str,
        idle_hours: int,
        max_messages: int,
        reasoning_effort: str | None = None,
        max_steps: int = 8,
    ):
        self.client = client  # None бол API key тохируулаагүй — зөвхөн товчлуурын урсгал ажиллана
        self.db = db
        self.system_prompt = system_prompt
        self.model = model
        self.reasoning_effort = reasoning_effort or None
        self.idle_after = timedelta(hours=idle_hours)
        self.max_messages = max_messages
        self.max_steps = max_steps

    async def reset(self, channel: str, chat_id: str) -> None:
        async with self.db.session() as s, s.begin():
            s.add(Conversation(channel=channel, chat_id=chat_id, messages=[]))

    async def reply(self, ctx: ToolContext, text: str) -> str:
        if self.client is None:
            return DISABLED_REPLY
        conv = await self._current_conversation(ctx.channel.name, ctx.chat_id)
        messages = [*conv.messages, {"role": "user", "content": text}]
        try:
            reply = await self._run(ctx, messages)
        except openai.RateLimitError:
            log.warning("LLM rate limit, chat %s", ctx.chat_id)
            return BUSY_REPLY
        except openai.APIError:
            # 4xx/5xx, сүлжээний алдаа — SDK өөрөө дахин оролдсоны дараа ч бүтэлгүйтсэн
            log.exception("LLM API алдаа, chat %s", ctx.chat_id)
            return ERROR_REPLY
        await self._save(conv.id, messages)
        return reply

    async def _run(self, ctx: ToolContext, messages: list[dict[str, Any]]) -> str:
        """messages-д шинэ эргэлтүүдийг нэмээд эцсийн хариуг буцаана."""
        for _ in range(self.max_steps):
            message = await self._complete(messages)
            assistant: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
            tool_calls = [tc for tc in message.tool_calls or [] if tc.type == "function"]
            if tool_calls:
                assistant["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant)
            if not tool_calls:
                return to_plain_text(message.content or "")
            for tc in tool_calls:
                content = await self._call_tool(ctx, tc.function.name, tc.function.arguments)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})

        log.warning("Agent step limit reached, chat %s", ctx.chat_id)
        return STEP_LIMIT_REPLY

    async def _complete(self, messages: list[dict[str, Any]]) -> Any:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": self.system_prompt}, *self._window(messages)],
            "tools": TOOLS,
            "tool_choice": "auto",
            "temperature": 0.3,
            # gpt-oss-ийн далд reasoning токен ч энэ хязгаарт тооцогдоно
            "max_tokens": 4096,
        }
        if self.reasoning_effort:
            params["reasoning_effort"] = self.reasoning_effort
        for attempt in range(2):
            try:
                response = await self.client.chat.completions.create(**params)
                return response.choices[0].message
            except openai.BadRequestError as e:
                # Groq: загвар буруу хэлбэртэй tool call үүсгэвэл 400 tool_use_failed буцаана — нэг удаа дахин оролдоно
                if e.code != "tool_use_failed" or attempt == 1:
                    raise
                log.warning("tool_use_failed, retrying")
        raise AssertionError("unreachable")

    def _window(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Сүүлийн max_messages мессежийг авна. Tool хариу өнчрөхгүйн тулд user мессежээс эхлүүлнэ."""
        start = max(len(messages) - self.max_messages, 0)
        while start < len(messages) and messages[start]["role"] != "user":
            start += 1
        if start == len(messages):  # цонхонд user мессеж алга — сүүлийн user мессежээс эхэлнэ
            start = max(i for i, m in enumerate(messages) if m["role"] == "user")
        return messages[start:]

    async def _call_tool(self, ctx: ToolContext, name: str, raw_args: str) -> str:
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ToolError("Аргумент JSON объект байх ёстой.")
            content = await run_tool(ctx, name, args)
        except ToolError as e:
            content = f"Алдаа: {e}"
        except (json.JSONDecodeError, TypeError):
            content = f"Алдаа: {name} хэрэгслийн аргумент буруу байна ({raw_args})."
        except Exception:
            log.exception("Tool %s failed", name)
            content = "Алдаа: дотоод алдаа гарлаа. Харилцагчид түр хүлээхийг эсвэл дахин оролдохыг санал болго."
        log.info("tool %s(%s) -> %s", name, raw_args, content[:200])
        return content

    async def _current_conversation(self, channel: str, chat_id: str) -> Conversation:
        async with self.db.session() as s, s.begin():
            conv = await s.scalar(
                select(Conversation)
                .where(Conversation.channel == channel, Conversation.chat_id == chat_id)
                .order_by(Conversation.id.desc())
                .limit(1)
            )
            if conv is None or conv.updated_at < utcnow() - self.idle_after:
                conv = Conversation(channel=channel, chat_id=chat_id, messages=[])
                s.add(conv)
        return conv

    async def _save(self, conversation_id: int, messages: list[Any]) -> None:
        async with self.db.session() as s, s.begin():
            conv = await s.get(Conversation, conversation_id)
            conv.messages = messages
            conv.updated_at = utcnow()
