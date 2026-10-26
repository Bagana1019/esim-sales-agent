from typing import Protocol

from sqlalchemy import select, update

from .db import Database, EsimProfile, Order, utcnow


class EsimProvider(Protocol):
    async def issue(self, order: Order) -> EsimProfile | None:
        """Захиалгад eSIM олгоно. Дахин дуудахад ижил профайл буцаана. Боломжгүй бол None."""
        ...


class PoolProvider:
    """Урьдчилан импортолсон eSIM сангаас (esim_profiles хүснэгт) олгоно.

    Дараа нь нийлүүлэгчийн API-аар (Airalo, eSIM Access г.м.) шууд үүсгэдэг
    provider-оор сольж болно — EsimProvider интерфейсийг л хэрэгжүүлэхэд хангалттай.
    """

    def __init__(self, db: Database):
        self.db = db

    async def issue(self, order: Order) -> EsimProfile | None:
        assigned = select(EsimProfile).where(EsimProfile.order_id == order.id)
        async with self.db.session() as s, s.begin():
            if existing := await s.scalar(assigned):
                return existing
            # Өөр захиалга яг тэр мөчид ижил профайлыг авбал дахин оролдоно
            for _ in range(3):
                candidate = (
                    select(EsimProfile.id)
                    .where(EsimProfile.package_sku == order.package_sku, EsimProfile.order_id.is_(None))
                    .order_by(EsimProfile.id)
                    .limit(1)
                    .scalar_subquery()
                )
                result = await s.execute(
                    update(EsimProfile)
                    .where(EsimProfile.id == candidate, EsimProfile.order_id.is_(None))
                    .values(order_id=order.id, assigned_at=utcnow())
                )
                if result.rowcount:
                    return await s.scalar(assigned)
        return None
