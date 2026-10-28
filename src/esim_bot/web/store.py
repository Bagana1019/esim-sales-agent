"""Вэб дэлгүүр: захиалга, төлбөр шалгах, и-мэйлээр хүргэх."""

import base64
import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from ..catalog import Catalog
from ..db import EsimProfile, LicenseKey, Order, OrderStatus
from ..delivery import (
    DEFAULT_ACTIVATION,
    esim_message,
    license_message,
    low_stock_text,
    multi_esim_note,
    product_label,
    qr_png,
)
from ..fulfillment import Item
from ..mailer import Attachment, Mailer
from ..orders import OrderService, PaymentOutcome, PaymentResult
from ..payments.qpay import QPayError

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(email: str) -> bool:
    return len(email) <= 254 and bool(EMAIL_RE.match(email))


def normalize_phone(phone: str) -> str | None:
    """Монголын 8 оронтой дугаар (99112233) эсвэл +-тэй олон улсын дугаар; буруу бол None."""
    compact = re.sub(r"[\s\-().]", "", phone)
    if re.fullmatch(r"(\+?976)?\d{8}", compact):
        return compact[-8:]  # Монголын дугаарыг 8 оронгоор хадгална
    if compact.lstrip("+").startswith("976"):
        return None  # +976-ийн дараа 8 оронтой байх ёстой
    if re.fullmatch(r"\+\d{8,15}", compact):
        return compact
    return None


def png_data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


class WebStore:
    channel = "web"

    def __init__(
        self,
        catalog: Catalog,
        orders: OrderService,
        mailer: Mailer,
        *,
        shop_name: str,
        public_base_url: str,
        notify_admin: Callable[[str], Awaitable[None]],
    ):
        self.catalog = catalog
        self.orders = orders
        self.mailer = mailer
        self.shop_name = shop_name
        self.public_base_url = public_base_url.rstrip("/")
        self.notify_admin = notify_admin

    def order_path(self, order: Order) -> str:
        return f"/order/{order.id}?t={self.orders.access_token(order.id)}"

    async def stock(self, skus: Iterable[str]) -> dict[str, int]:
        """Одоо худалдаж болох тоо, sku тус бүрээр."""
        return await self.orders.fulfillment.available(skus)

    async def checkout(self, sku: str, email: str, phone: str, quantity: int = 1) -> Order:
        order, _ = await self.orders.create_order(self.channel, email, [sku] * quantity, phone=phone)
        return order

    async def get_order(self, order_id: str, token: str) -> Order | None:
        order = await self.orders.get(order_id)
        if order is None or order.channel != self.channel or not self.orders.verify_access_token(order_id, token):
            return None
        return order

    async def find_order(self, number: str, email: str) -> Order | None:
        """Захиалгын дугаар (#-ийн дараах 8 тэмдэгт) ба захиалахдаа бичсэн и-мэйлээр захиалга хайна."""
        number = number.strip().lstrip("#").lower()
        if len(number) < 8:
            return None
        orders = await self.orders.recent_orders(self.channel, email.strip(), limit=50)
        return next((o for o in orders if o.id.startswith(number)), None)

    async def process(self, order_id: str) -> PaymentResult | None:
        """Төлбөр шалгаж, төлөгдсөн бол и-мэйлээр хүргэнэ. QPay callback, хуудасны polling хоёулаа дуудна."""
        try:
            result = await self.orders.process_payment(order_id, self._deliver)
        except QPayError:
            log.exception("Payment check failed for %s", order_id)
            return None
        if result.low_stock:
            await self.notify_admin(low_stock_text(self.catalog, result.low_stock))
        if result.outcome == PaymentOutcome.OUT_OF_STOCK and result.newly_failed:
            order = result.order
            await self._mail(
                order.chat_id,
                f"{self.shop_name} - захиалга #{order.id[:8]} төлбөр баталгаажлаа",
                "Төлбөр баталгаажлаа. Таны захиалгыг бэлтгэж байна, удахгүй энэ и-мэйлээр илгээнэ.\n\n"
                f"Захиалгаа эндээс харж болно: {self.public_base_url}{self.order_path(order)}",
            )
            await self.notify_admin(
                f"⚠️ Вэб захиалга {order.id} төлөгдсөн боловч сан дутуу ({', '.join(order.package_skus)}). "
                f"Харилцагч: {order.chat_id}, утас {order.phone or '-'}"
            )
        return result

    async def _deliver(self, order: Order, items: list[Item]) -> None:
        esims = [i for i in items if isinstance(i, EsimProfile)]
        parts = [f"Сайн байна уу,\n\n{self.shop_name}-аас захиалсанд баярлалаа."]
        attachments = []
        for n, profile in enumerate(esims, 1):
            parts.append(esim_message(self.catalog, order, profile, n, len(esims)).replace("дээрх QR", "хавсаргасан QR"))
            attachments.append(Attachment(f"esim-{n}.png", qr_png(profile.lpa_string)))
        if len(esims) > 1:
            parts.append(multi_esim_note(len(esims)))
        if esims:
            parts.append(f"Алхам алхмаар суулгах заавар: {self.public_base_url}/install")
        parts += [license_message(self.catalog, order, key) for key in items if isinstance(key, LicenseKey)]
        parts.append(f"Захиалгаа хэзээ ч эндээс харж болно: {self.public_base_url}{self.order_path(order)}")
        await self._mail(order.chat_id, f"{self.shop_name} - захиалга #{order.id[:8]}", "\n\n".join(parts), attachments)

    async def _mail(self, to: str, subject: str, body: str, attachments: list[Attachment] | None = None) -> None:
        """И-мэйл илгээнэ. Илгээж чадаагүй ч захиалгыг зогсоохгүй (QR код, түлхүүр захиалгын хуудсанд
        гарсаар) — админд мэдэгдэнэ."""
        try:
            await self.mailer.send(to, subject, body, attachments)
        except Exception as e:
            log.exception("И-мэйл илгээж чадсангүй: %s", to)
            await self.notify_admin(f"⚠️ {to} руу и-мэйл илгээж чадсангүй ({subject}): {e}")

    async def order_view(self, order: Order) -> dict[str, Any]:
        """Захиалгын хуудсанд харуулах өгөгдөл."""
        view: dict[str, Any] = {
            "order": order,
            "lines": [(product_label(self.catalog, sku), self.catalog.product(sku)) for sku in order.package_skus],
            "pending": order.status == OrderStatus.PENDING,
            "delivered": order.status == OrderStatus.DELIVERED,
        }
        info = order.payment_info or {}
        if view["pending"] and info.get("qr_text"):
            view["payment_qr"] = png_data_uri(qr_png(info["qr_text"]))
            view["short_url"] = info.get("short_url")
            view["bank_urls"] = [u for u in info.get("urls", []) if u.get("link")]
        if view["delivered"]:
            view["items"] = [self._item_view(i) for i in await self.orders.items_for(order.id)]
        return view

    def _item_view(self, item: Item) -> dict[str, Any]:
        label = product_label(self.catalog, item.package_sku)
        if isinstance(item, EsimProfile):
            return {
                "kind": "esim",
                "label": label,
                "qr": png_data_uri(qr_png(item.lpa_string)),
                "smdp": item.smdp_address,
                "code": item.activation_code,
                "iccid": item.iccid,
            }
        lic = self.catalog.license(item.package_sku)
        return {
            "kind": "license",
            "label": label,
            "key": item.product_key,
            "activation": (lic.activation if lic and lic.activation else DEFAULT_ACTIVATION).strip(),
        }
