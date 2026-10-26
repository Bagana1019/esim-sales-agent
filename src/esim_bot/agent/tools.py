from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..catalog import format_mnt
from ..db import OrderStatus
from ..delivery import package_label

if TYPE_CHECKING:
    from ..bot import SalesBot
    from ..channels.base import Channel


@dataclass
class ToolContext:
    """Хэрэгслүүд зөвхөн одоогийн харилцагчийн өгөгдөлд хандана — chat_id-г
    загвар биш, систем өгдөг."""

    bot: SalesBot
    channel: Channel
    chat_id: str


class ToolError(Exception):
    pass


def _schema(properties: dict[str, Any] | None = None) -> dict[str, Any]:
    properties = properties or {}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_COUNTRY_CODE = {"country_code": {"type": "string", "description": "Улсын ISO код, жишээ нь CN, KR, JP, TH"}}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_countries",
        "description": "Манай eSIM зарж буй улсуудын жагсаалт.",
        "input_schema": _schema(),
        "strict": True,
    },
    {
        "name": "list_packages",
        "description": "Тухайн улсын бүх багц: sku, хоног, дата, үнэ. Үнэ хэлэхээс өмнө заавал дуудна.",
        "input_schema": _schema(_COUNTRY_CODE),
        "strict": True,
    },
    {
        "name": "show_package_buttons",
        "description": "Тухайн улсын багцуудыг харилцагчид товчлуураар харуулна. Харилцагч товч дарж сонгоно.",
        "input_schema": _schema(_COUNTRY_CODE),
        "strict": True,
    },
    {
        "name": "create_payment",
        "description": (
            "Сонгосон багцад захиалга үүсгэж, QPay төлбөрийн QR кодыг харилцагчид илгээнэ. "
            "Харилцагч тодорхой багц сонгож, авахаа илэрхийлсэн үед л дуудна."
        ),
        "input_schema": _schema(
            {"package_sku": {"type": "string", "description": "list_packages-ийн буцаасан sku"}}
        ),
        "strict": True,
    },
    {
        "name": "get_my_orders",
        "description": "Энэ харилцагчийн сүүлийн захиалгууд, тэдгээрийн төлөв.",
        "input_schema": _schema(),
        "strict": True,
    },
    {
        "name": "resend_esim",
        "description": "Хүргэгдсэн захиалгын eSIM QR код, суулгах мэдээллийг дахин илгээнэ.",
        "input_schema": _schema(
            {"order_id": {"type": "string", "description": "get_my_orders-ийн буцаасан order_id"}}
        ),
        "strict": True,
    },
]

_STATUS_TEXT = {
    OrderStatus.PENDING: "төлбөр хүлээгдэж байна",
    OrderStatus.PAID: "төлөгдсөн, eSIM илгээгдэж байна",
    OrderStatus.DELIVERED: "eSIM илгээгдсэн",
    OrderStatus.NEEDS_ATTENTION: "төлөгдсөн, eSIM-ийг оператор гараар илгээнэ",
}


async def list_countries(ctx: ToolContext) -> Any:
    return [{"code": c.code, "name": c.name} for c in ctx.bot.catalog.countries]


async def list_packages(ctx: ToolContext, country_code: str) -> Any:
    packages = ctx.bot.catalog.packages_for(country_code)
    if not packages:
        raise ToolError(f"{country_code} улсад багц байхгүй. list_countries-оор боломжит улсуудыг харна уу.")
    return [
        {"sku": p.sku, "days": p.days, "data": p.data, "price": format_mnt(p.price_mnt)} for p in packages
    ]


async def show_package_buttons(ctx: ToolContext, country_code: str) -> Any:
    if not await ctx.bot.show_packages(ctx.channel, ctx.chat_id, country_code):
        raise ToolError(f"{country_code} улсад багц байхгүй.")
    return {"shown": True}


async def create_payment(ctx: ToolContext, package_sku: str) -> Any:
    if ctx.bot.catalog.package(package_sku) is None:
        raise ToolError(f"{package_sku} гэсэн багц байхгүй. list_packages-оор sku-г шалгана уу.")
    order = await ctx.bot.start_checkout(ctx.channel, ctx.chat_id, package_sku)
    if order is None:
        raise ToolError("Нэхэмжлэх үүсгэж чадсангүй, төлбөрийн систем түр ажиллахгүй байна.")
    return {"order_id": order.id, "amount": format_mnt(order.amount_mnt), "qr_sent": True}


async def get_my_orders(ctx: ToolContext) -> Any:
    orders = await ctx.bot.orders.recent_orders(ctx.channel.name, ctx.chat_id)
    return [
        {
            "order_id": o.id,
            "package": package_label(ctx.bot.catalog, o.package_sku),
            "amount": format_mnt(o.amount_mnt),
            "status": _STATUS_TEXT.get(o.status, o.status),
            "created_at": o.created_at.strftime("%Y-%m-%d %H:%M UTC"),
        }
        for o in orders
    ]


async def resend_esim(ctx: ToolContext, order_id: str) -> Any:
    if not await ctx.bot.resend_esim(ctx.channel, ctx.chat_id, order_id):
        raise ToolError("Энэ дугаартай хүргэгдсэн захиалга олдсонгүй.")
    return {"sent": True}


_HANDLERS: dict[str, Callable[..., Awaitable[Any]]] = {
    "list_countries": list_countries,
    "list_packages": list_packages,
    "show_package_buttons": show_package_buttons,
    "create_payment": create_payment,
    "get_my_orders": get_my_orders,
    "resend_esim": resend_esim,
}


async def run_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> str:
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ToolError(f"Үл мэдэгдэх хэрэгсэл: {name}")
    return json.dumps(await handler(ctx, **args), ensure_ascii=False)
