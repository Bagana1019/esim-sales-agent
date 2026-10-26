from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, select
from sqlalchemy.exc import OperationalError
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
    # Нэг захиалгад нэг эсвэл хэд хэдэн багц (жишээ нь 7 хоног + 3 хоног) — нэг QR-аар төлнө
    package_skus: Mapped[list[Any]]
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
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), index=True)
    assigned_at: Mapped[datetime | None]

    @property
    def lpa_string(self) -> str:
        """Утасны камер уншдаг стандарт eSIM QR агуулга."""
        return f"LPA:1${self.smdp_address}${self.activation_code}"


class LicenseKey(Base):
    """Урьдчилан худалдаж авсан лицензийн түлхүүрийн сан. order_id хоосон бол ашиглагдаагүй."""

    __tablename__ = "license_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_sku: Mapped[str] = mapped_column(String(64), index=True)  # catalog.yaml licenses[].sku
    product_key: Mapped[str] = mapped_column(String(255), unique=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), index=True)
    assigned_at: Mapped[datetime | None]


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
        # create_all хуучин хүснэгтийг өөрчилдөггүй — бүтэц өөрчлөгдсөн бол ойлгомжтой мессеж өгнө
        try:
            async with self.session() as s:
                await s.execute(select(Order.package_skus).limit(1))
        except OperationalError:
            raise SystemExit(
                f"Өгөгдлийн сангийн бүтэц хуучирсан байна ({self.engine.url.database}). "
                "Туршилтын өгөгдөл бол файлыг устгаад дахин асаана уу."
            ) from None

    async def dispose(self) -> None:
        await self.engine.dispose()
