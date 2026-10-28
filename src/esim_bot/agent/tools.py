from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..catalog import format_mnt
from ..db import OrderStatus
from ..delivery import order_label

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


def _tool(name: str, description: str, properties: dict[str, Any] | None = None) -> dict[str, Any]:
    """OpenAI-compatible (Groq) function tool тодорхойлолт."""
    properties = properties or {}
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }


_COUNTRY_CODE = {"country_code": {"type": "string", "description": "Улсын код: CN, KR, JP эсвэл TH"}}

TOOLS: list[dict[str, Any]] = [
    _tool("list_countries", "Манай eSIM зарж буй улсуудын жагсаалт."),
    _tool(
        "list_packages",
        "Тухайн улсын бүх багц: sku, хоног, дата, үнэ, in_stock (false бол түр дууссан). Үнэ хэлэхээс өмнө заавал дуудна.",
        _COUNTRY_CODE,
    ),
    _tool(
        "show_package_buttons",
        "Тухайн улсын багцуудыг харилцагчид товчлуураар харуулна. Харилцагч товч дарж сонгоно.",
        _COUNTRY_CODE,
    ),
    _tool("show_license_buttons", "Windows, Office лицензүүдийг харилцагчид товчлуураар харуулна."),
    _tool(
        "create_payment",
        "Сонгосон бүтээгдэхүүн(үүд)-д (eSIM багц, Windows/Office лиценз) нэг захиалга үүсгэж, нийт дүнгийн ганц "
        "QPay QR кодыг харилцагчид илгээнэ. Хоёр бүтээгдэхүүн хамт авах бол хоёуланг нь нэг дуудлагад өгнө. "
        "Харилцагч тодорхой бүтээгдэхүүн сонгож, авахаа илэрхийлсэн үед л дуудна.",
        {
            "skus": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 3,
                "description": "Каталогийн sku-ууд, жишээ нь [\"CN-7D-5GB\", \"CN-3D-1GB\"] эсвэл [\"WIN11-PRO\"]",
            }
        },
    ),
    _tool("get_my_orders", "Энэ харилцагчийн сүүлийн захиалгууд, тэдгээрийн төлөв."),
    _tool(
        "resend_order",
        "Хүргэгдсэн захиалгын eSIM QR код эсвэл лицензийн түлхүүрийг дахин илгээнэ.",
        {"order_id": {"type": "string", "description": "get_my_orders-ийн буцаасан order_id"}},
    ),
]

_STATUS_TEXT = {
    OrderStatus.PENDING: "төлбөр хүлээгдэж байна",
    OrderStatus.PAID: "төлөгдсөн, илгээгдэж байна",
    OrderStatus.DELIVERED: "илгээгдсэн",
    OrderStatus.NEEDS_ATTENTION: "төлөгдсөн, оператор гараар илгээнэ",
}


async def list_countries(ctx: ToolContext) -> Any:
    return [{"code": c.code, "name": c.name} for c in ctx.bot.catalog.countries]


async def list_packages(ctx: ToolContext, country_code: str) -> Any:
    packages = ctx.bot.catalog.packages_for(country_code)
    if not packages:
        raise ToolError(f"{country_code} улсад багц байхгүй. list_countries-оор боломжит улсуудыг харна уу.")
    stock = await ctx.bot.orders.fulfillment.available(p.sku for p in packages)
    return [
        {"sku": p.sku, "days": p.days, "data": p.data, "price": format_mnt(p.price_mnt), "in_stock": stock[p.sku] > 0}
        | ({"region": p.region.name, "countries": list(p.region.countries)} if p.region else {})
        for p in packages
    ]


async def show_package_buttons(ctx: ToolContext, country_code: str) -> Any:
    if not await ctx.bot.show_packages(ctx.channel, ctx.chat_id, country_code):
        raise ToolError(f"{country_code} улсад багц байхгүй.")
    return {"shown": True}


async def show_license_buttons(ctx: ToolContext) -> Any:
    if not await ctx.bot.show_licenses(ctx.channel, ctx.chat_id):
        raise ToolError("Одоогоор лиценз зарагдахгүй байна.")
    return {"shown": True}


async def create_payment(ctx: ToolContext, skus: list[str]) -> Any:
    if not 1 <= len(skus) <= 3:
        raise ToolError("Нэг захиалгад 1-3 бүтээгдэхүүн багтана.")
    if unknown := [sku for sku in skus if ctx.bot.catalog.product(sku) is None]:
        raise ToolError(f"{', '.join(unknown)} гэсэн бүтээгдэхүүн байхгүй. Каталогийн sku-г шалгана уу.")
    available = await ctx.bot.orders.fulfillment.available(set(skus))
    if sold_out := [sku for sku in skus if available[sku] < skus.count(sku)]:
        raise ToolError(f"{', '.join(sold_out)} түр дууссан (in_stock=false). Нөөцтэй өөр багц санал болгоно уу.")
    order = await ctx.bot.start_checkout(ctx.channel, ctx.chat_id, skus)
    if order is None:
        raise ToolError("Нэхэмжлэх үүсгэж чадсангүй, төлбөрийн систем түр ажиллахгүй байна.")
    return {"order_id": order.id, "amount": format_mnt(order.amount_mnt), "qr_sent": True}


async def get_my_orders(ctx: ToolContext) -> Any:
    orders = await ctx.bot.orders.recent_orders(ctx.channel.name, ctx.chat_id)
    return [
        {
            "order_id": o.id,
            "products": order_label(ctx.bot.catalog, o.package_skus),
            "amount": format_mnt(o.amount_mnt),
            "status": _STATUS_TEXT.get(o.status, o.status),
            "created_at": o.created_at.strftime("%Y-%m-%d %H:%M UTC"),
        }
        for o in orders
    ]


async def resend_order(ctx: ToolContext, order_id: str) -> Any:
    if not await ctx.bot.resend_order(ctx.channel, ctx.chat_id, order_id):
        raise ToolError("Энэ дугаартай хүргэгдсэн захиалга олдсонгүй.")
    return {"sent": True}


_HANDLERS: dict[str, Callable[..., Awaitable[Any]]] = {
    "list_countries": list_countries,
    "list_packages": list_packages,
    "show_package_buttons": show_package_buttons,
    "show_license_buttons": show_license_buttons,
    "create_payment": create_payment,
    "get_my_orders": get_my_orders,
    "resend_order": resend_order,
}


async def run_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> str:
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ToolError(f"Үл мэдэгдэх хэрэгсэл: {name}")
    return json.dumps(await handler(ctx, **args), ensure_ascii=False)
