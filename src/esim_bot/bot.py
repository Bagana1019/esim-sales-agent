"""Сувгаас хамааралгүй ботын логик: товчлуурын урсгал + чөлөөт текстийг агент руу дамжуулах."""

import asyncio
import base64
import logging
from collections import defaultdict
from collections.abc import Sequence

from .agent.loop import SalesAgent
from .agent.tools import ToolContext
from .catalog import Catalog, Product, format_mnt
from .channels.base import Button, Channel, Keyboard
from .db import EsimProfile, LicenseKey, Order, OrderStatus
from .delivery import (
    esim_message,
    license_message,
    low_stock_text,
    multi_esim_note,
    order_label,
    out_of_stock_text,
    product_label,
    qr_png,
)
from .fulfillment import Item
from .orders import OrderService, OutOfStock, PaymentOutcome, PaymentResult
from .payments.qpay import QPayError

log = logging.getLogger(__name__)

WELCOME = (
    "Сайн байна уу! 👋 Гадаадад аялахдаа роуминг төлөхгүйгээр интернэт хэрэглэх eSIM багц болон "
    "Windows, Office лиценз санал болгож байна.\n\n"
    "eSIM авах бол очих улсаа, лиценз авах бол \"Windows, Office лиценз\"-ийг сонгоно уу. Асуух зүйлээ чөлөөтэй бичиж болно."
)


class SalesBot:
    def __init__(
        self,
        catalog: Catalog,
        orders: OrderService,
        agent: SalesAgent,
        channels: dict[str, Channel],
        admin: tuple[str, str] | None = None,  # (channel, chat_id)
    ):
        self.catalog = catalog
        self.orders = orders
        self.agent = agent
        self.channels = channels
        self.admin = admin
        self._chat_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # --- Оролт ---

    async def handle_start(self, channel: Channel, chat_id: str) -> None:
        await self.agent.reset(channel.name, chat_id)
        await self.show_countries(channel, chat_id, WELCOME)

    async def handle_button(self, channel: Channel, chat_id: str, data: str) -> None:
        kind, _, value = data.partition(":")
        if kind == "menu":
            await self.show_countries(channel, chat_id)
        elif kind == "lic":
            await self.show_licenses(channel, chat_id)
        elif kind == "c":
            await self.show_packages(channel, chat_id, value)
        elif kind == "p":
            await self.start_checkout(channel, chat_id, [value])
        elif kind == "chk":
            await self.check_payment(channel, chat_id, value)
        else:
            log.warning("Unknown button data: %s", data)

    async def handle_text(self, channel: Channel, chat_id: str, text: str) -> None:
        # Нэг харилцагчийн мессежүүдийг дараалуулж боловсруулна (ярианы түүх зөрөхгүй)
        async with self._chat_locks[f"{channel.name}:{chat_id}"]:
            reply = await self.agent.reply(ToolContext(self, channel, chat_id), text)
        if reply:
            await channel.send_text(chat_id, reply)

    async def on_payment_notification(self, order_id: str) -> None:
        """QPay callback ирэхэд дуудагдана."""
        order = await self.orders.get(order_id)
        if order is None or order.channel not in self.channels:
            log.warning("Callback for unknown order %s", order_id)
            return
        channel = self.channels[order.channel]
        result = await self._process_payment(channel, order)
        if result and result.outcome == PaymentOutcome.OUT_OF_STOCK and result.newly_failed:
            await channel.send_text(order.chat_id, self._out_of_stock_text())

    # --- Урсгалын алхмууд (агентын хэрэгслүүд ч эдгээрийг ашиглана) ---

    async def show_countries(self, channel: Channel, chat_id: str, text: str = "Очих улсаа сонгоно уу:") -> None:
        buttons = [[Button(c.label, data=f"c:{c.code}")] for c in self.catalog.countries]
        if self.catalog.licenses:
            buttons.append([Button("🔑 Windows, Office лиценз", data="lic")])
        await channel.send_text(chat_id, text, buttons)

    async def show_licenses(self, channel: Channel, chat_id: str) -> bool:
        if not self.catalog.licenses:
            return False
        licenses = await self._in_stock_first(self.catalog.licenses)
        buttons: Keyboard = [[Button(f"{lic.emoji} {label}", data=f"p:{lic.sku}")] for lic, label in licenses]
        buttons.append([Button("⬅️ Буцах", data="menu")])
        await channel.send_text(chat_id, "🔑 Лиценз - сонгоно уу:", buttons)
        return True

    async def show_packages(self, channel: Channel, chat_id: str, country_code: str) -> bool:
        country = self.catalog.country(country_code)
        packages = self.catalog.packages_for(country.code) if country else []
        if country is None or not packages:
            return False
        buttons: Keyboard = [[Button(label, data=f"p:{p.sku}")] for p, label in await self._in_stock_first(packages)]
        buttons.append([Button("⬅️ Улс солих", data="menu")])
        await channel.send_text(chat_id, f"{country.label} - багцаа сонгоно уу:", buttons)
        return True

    async def start_checkout(self, channel: Channel, chat_id: str, skus: list[str]) -> Order | None:
        try:
            order, invoice = await self.orders.create_order(channel.name, chat_id, skus)
        except ValueError:
            await channel.send_text(chat_id, "Ийм бүтээгдэхүүн олдсонгүй. Дахин сонгоно уу.")
            await self.show_countries(channel, chat_id)
            return None
        except OutOfStock as e:
            await channel.send_text(chat_id, out_of_stock_text(self.catalog, e.available))
            await self.show_countries(channel, chat_id, "Өөр багц сонгоно уу:")
            return None
        except QPayError:
            log.exception("Invoice creation failed")
            await channel.send_text(chat_id, "Төлбөрийн систем түр ажиллахгүй байна. Хэсэг хугацааны дараа дахин оролдоно уу.")
            return None

        photo = base64.b64decode(invoice.qr_image_b64) if invoice.qr_image_b64 else qr_png(invoice.qr_text)
        caption = (
            f"🧾 Захиалга #{order.id[:8]}\n"
            + "".join(f"{product_label(self.catalog, sku)}\n" for sku in skus)
            + f"Төлөх дүн: {format_mnt(order.amount_mnt)}\n\n"
            "Банкны аппаараа QR кодыг уншуулж төлнө үү. Төлбөр орсны дараа захиалга автоматаар энд ирнэ."
        )
        buttons: Keyboard = []
        if invoice.short_url:
            # Telegram-ын товч зөвхөн https холбоос зөвшөөрдөг тул банкны deeplink-үүдийн оронд QPay-ийн хуудсыг нээнэ
            buttons.append([Button("📱 Банкны аппаар төлөх", url=invoice.short_url)])
        buttons.append([Button("✅ Төлбөр шалгах", data=f"chk:{order.id}")])
        await channel.send_photo(chat_id, photo, caption, buttons)
        return order

    async def check_payment(self, channel: Channel, chat_id: str, order_id: str) -> None:
        order = await self.orders.get_for_chat(order_id, channel.name, chat_id)
        if order is None:
            await channel.send_text(chat_id, "Захиалга олдсонгүй.")
            return
        result = await self._process_payment(channel, order)
        if result is None:
            await channel.send_text(chat_id, "Төлбөр шалгах үед алдаа гарлаа. Хэсэг хугацааны дараа дахин оролдоно уу.")
        elif result.outcome == PaymentOutcome.NOT_PAID:
            await channel.send_text(
                chat_id,
                "Төлбөр хараахан орж ирээгүй байна. Төлсний дараа хэдэн секунд хүлээгээд дахин шалгана уу.",
                [[Button("✅ Төлбөр шалгах", data=f"chk:{order.id}")]],
            )
        elif result.outcome == PaymentOutcome.ALREADY_DELIVERED:
            await self.resend_order(channel, chat_id, order.id)
        elif result.outcome == PaymentOutcome.OUT_OF_STOCK:
            await channel.send_text(chat_id, self._out_of_stock_text())

    async def resend_order(self, channel: Channel, chat_id: str, order_id: str) -> bool:
        order = await self.orders.get_for_chat(order_id, channel.name, chat_id)
        if order is None or order.status != OrderStatus.DELIVERED:
            return False
        items = await self.orders.items_for(order.id)
        if not items:
            return False
        await self._send_items(channel, order, items)
        return True

    # --- Дотоод ---

    async def _in_stock_first(self, products: Sequence[Product]) -> list[tuple[Product, str]]:
        """Товчлуурын нэр: дууссаныг тэмдэглэж, жагсаалтын төгсгөлд гаргана."""
        stock = await self.orders.fulfillment.available(p.sku for p in products)
        ordered = sorted(products, key=lambda p: not stock[p.sku])
        return [(p, p.title if stock[p.sku] else f"{p.title} · түр дууссан") for p in ordered]

    async def _process_payment(self, channel: Channel, order: Order) -> PaymentResult | None:
        async def deliver(o: Order, items: list[Item]) -> None:
            await self._send_items(channel, o, items)

        try:
            result = await self.orders.process_payment(order.id, deliver)
        except QPayError:
            log.exception("Payment check failed for %s", order.id)
            return None
        if result.outcome == PaymentOutcome.OUT_OF_STOCK and result.newly_failed:
            await self.notify_admin(
                f"⚠️ Захиалга {order.id} төлөгдсөн боловч сан дутуу ({order_label(self.catalog, order.package_skus)}). "
                "Сан руу нэмээд 'Төлбөр шалгах'-ыг дахин дарахыг харилцагчид хэлнэ үү."
            )
        if result.low_stock:
            await self.notify_admin(low_stock_text(self.catalog, result.low_stock))
        return result

    async def _send_items(self, channel: Channel, order: Order, items: list[Item]) -> None:
        esims = [i for i in items if isinstance(i, EsimProfile)]
        total = len(esims)
        for i, profile in enumerate(esims, 1):
            caption = f"eSIM QR код ({i}/{total})" if total > 1 else "eSIM QR код"
            await channel.send_photo(order.chat_id, qr_png(profile.lpa_string), caption)
            await channel.send_text(order.chat_id, esim_message(self.catalog, order, profile, i, total))
        if total > 1:
            await channel.send_text(order.chat_id, multi_esim_note(total))
        for key in items:
            if isinstance(key, LicenseKey):
                await channel.send_text(order.chat_id, license_message(self.catalog, order, key))

    def _out_of_stock_text(self) -> str:
        return (
            "Төлбөр баталгаажлаа ✅ Таны захиалгыг бэлтгэж байна, удахгүй энд илгээнэ. "
            "Түр хүлээнэ үү, саатал гарсанд уучлаарай."
        )

    async def notify_admin(self, text: str) -> None:
        if self.admin is None:
            log.warning("ADMIN: %s", text)
            return
        channel_name, chat_id = self.admin
        if channel := self.channels.get(channel_name):
            await channel.send_text(chat_id, text)
