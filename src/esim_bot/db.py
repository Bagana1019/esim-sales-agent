from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    # SQLite timezone хадгалдаггүй тул бүх цагийг naive UTC-ээр хадгална
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(), dict[str, Any]: JSON, list[Any]: JSON}


class OrderStatus(StrEnum):
    PENDING = "pending"  # нэхэмжлэх үүссэн, төлбөр хүлээж байна
    PAID = "paid"  # төлбөр баталгаажсан, eSIM хараахан илгээгээгүй
    DELIVERED = "delivered"
    NEEDS_ATTENTION = "needs_attention"  # төлсөн ч eSIM олгож чадаагүй — гараар шийднэ


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))
    chat_id: Mapped[str] = mapped_column(String(64), index=True)
    package_sku: Mapped[str] = mapped_column(String(64))
    amount_mnt: Mapped[int]
    status: Mapped[str] = mapped_column(String(32), default=OrderStatus.PENDING)
    qpay_invoice_id: Mapped[str | None] = mapped_column(String(64))
    qpay_payment_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    paid_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]


class EsimProfile(Base):
    """Урьдчилан худалдаж авсан eSIM-ийн сан. order_id хоосон бол ашиглагдаагүй."""

    __tablename__ = "esim_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_sku: Mapped[str] = mapped_column(String(64), index=True)
    iccid: Mapped[str] = mapped_column(String(32), unique=True)
    smdp_address: Mapped[str] = mapped_column(String(255))
    activation_code: Mapped[str] = mapped_column(String(255))
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), unique=True)
    assigned_at: Mapped[datetime | None]

    @property
    def lpa_string(self) -> str:
        """Утасны камер уншдаг стандарт eSIM QR агуулга."""
        return f"LPA:1${self.smdp_address}${self.activation_code}"


class Conversation(Base):
    """LLM-тэй хийсэн ярианы түүх (OpenAI chat форматаар). Зөвхөн нэмж бичнэ."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))
    chat_id: Mapped[str] = mapped_column(String(64), index=True)
    messages: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class Database:
    def __init__(self, url: str):
        self.engine = create_async_engine(url)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
