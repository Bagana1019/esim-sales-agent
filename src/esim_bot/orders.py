import asyncio
import hashlib
import hmac
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import select

from .catalog import Catalog
from .db import Database, EsimProfile, Order, OrderStatus, utcnow
from .esim import EsimProvider
from .payments.qpay import Invoice, PaymentGateway

log = logging.getLogger(__name__)

Deliver = Callable[[Order, EsimProfile], Awaitable[None]]


class PaymentOutcome(StrEnum):
    NOT_PAID = "not_paid"
    DELIVERED = "delivered"  # яг одоо илгээлээ
    ALREADY_DELIVERED = "already_delivered"
    OUT_OF_STOCK = "out_of_stock"  # төлсөн, гэхдээ eSIM олгож чадсангүй


@dataclass
class PaymentResult:
    outcome: PaymentOutcome
    order: Order
    newly_failed: bool = False  # NEEDS_ATTENTION төлөвт анх удаа орсон эсэх


class OrderService:
    def __init__(
        self,
        db: Database,
        catalog: Catalog,
        qpay: PaymentGateway,
        esim: EsimProvider,
        *,
        public_base_url: str,
        callback_secret: str,
    ):
        self.db = db
        self.catalog = catalog
        self.qpay = qpay
        self.esim = esim
        self.public_base_url = public_base_url.rstrip("/")
        self.callback_secret = callback_secret.encode()
        # Нэг процесс доторх давхар боловсруулалтаас сэргийлнэ (callback + "Төлбөр шалгах" зэрэг ирэх)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def callback_token(self, order_id: str) -> str:
        return hmac.new(self.callback_secret, order_id.encode(), hashlib.sha256).hexdigest()[:32]

    def verify_callback_token(self, order_id: str, token: str) -> bool:
        return hmac.compare_digest(self.callback_token(order_id), token)

    def callback_url(self, order_id: str) -> str:
        return (
            f"{self.public_base_url}/qpay/callback"
            f"?order_id={order_id}&token={self.callback_token(order_id)}"
        )

    async def create_order(self, channel: str, chat_id: str, sku: str) -> tuple[Order, Invoice]:
        package = self.catalog.package(sku)
        if package is None:
            raise ValueError(f"Үл мэдэгдэх багц: {sku}")
        country = self.catalog.country(package.country)
        order = Order(
            id=uuid4().hex,
            channel=channel,
            chat_id=chat_id,
            package_sku=sku,
            amount_mnt=package.price_mnt,
            status=OrderStatus.PENDING,
        )
        async with self.db.session() as s, s.begin():
            s.add(order)
        invoice = await self.qpay.create_invoice(
            order_id=order.id,
            amount=package.price_mnt,
            description=f"eSIM {country.name if country else package.country} {package.days} хоног",
            callback_url=self.callback_url(order.id),
        )
        order = await self._update(order.id, qpay_invoice_id=invoice.invoice_id)
        return order, invoice

    async def get(self, order_id: str) -> Order | None:
        async with self.db.session() as s:
            return await s.get(Order, order_id)

    async def get_for_chat(self, order_id: str, channel: str, chat_id: str) -> Order | None:
        order = await self.get(order_id)
        if order is None or order.channel != channel or order.chat_id != chat_id:
            return None
        return order

    async def recent_orders(self, channel: str, chat_id: str, limit: int = 5) -> list[Order]:
        async with self.db.session() as s:
            rows = await s.scalars(
                select(Order)
                .where(Order.channel == channel, Order.chat_id == chat_id)
                .order_by(Order.created_at.desc())
                .limit(limit)
            )
            return list(rows)

    async def profile_for(self, order_id: str) -> EsimProfile | None:
        async with self.db.session() as s:
            return await s.scalar(select(EsimProfile).where(EsimProfile.order_id == order_id))

    async def process_payment(self, order_id: str, deliver: Deliver) -> PaymentResult:
        """Төлбөрийг QPay-ээс шалгаж, төлөгдсөн бол eSIM олгоод deliver()-ээр илгээнэ.

        QPay callback-д шууд итгэхгүй — үргэлж payment/check-ээр баталгаажуулна.
        Хэдэн ч удаа дуудсан аюулгүй (idempotent).
        """
        async with self._locks[order_id]:
            order = await self.get(order_id)
            if order is None:
                raise KeyError(order_id)

            if order.status == OrderStatus.PENDING:
                if not order.qpay_invoice_id:
                    return PaymentResult(PaymentOutcome.NOT_PAID, order)
                payment_id = await self.qpay.find_payment(order.qpay_invoice_id, order.amount_mnt)
                if payment_id is None:
                    return PaymentResult(PaymentOutcome.NOT_PAID, order)
                order = await self._update(
                    order_id, status=OrderStatus.PAID, qpay_payment_id=payment_id, paid_at=utcnow()
                )
                log.info("Order %s paid (payment %s)", order_id, payment_id)

            if order.status == OrderStatus.DELIVERED:
                return PaymentResult(PaymentOutcome.ALREADY_DELIVERED, order)

            profile = await self.esim.issue(order)
            if profile is None:
                newly_failed = order.status != OrderStatus.NEEDS_ATTENTION
                order = await self._update(order_id, status=OrderStatus.NEEDS_ATTENTION)
                log.error("Order %s: %s багцын eSIM дууссан", order_id, order.package_sku)
                return PaymentResult(PaymentOutcome.OUT_OF_STOCK, order, newly_failed=newly_failed)

            await deliver(order, profile)
            order = await self._update(order_id, status=OrderStatus.DELIVERED, delivered_at=utcnow())
            return PaymentResult(PaymentOutcome.DELIVERED, order)

    async def _update(self, order_id: str, **values: object) -> Order:
        async with self.db.session() as s, s.begin():
            order = await s.get(Order, order_id)
            if order is None:
                raise KeyError(order_id)
            for key, value in values.items():
                setattr(order, key, value)
        return order
