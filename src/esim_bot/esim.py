from collections import Counter
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .db import Database, EsimProfile, Order, utcnow


class EsimProvider(Protocol):
    async def issue(self, order: Order) -> list[EsimProfile] | None:
        """Захиалгын багц бүрд eSIM олгоно (order.package_skus-ийн дарааллаар).

        Дахин дуудахад ижил профайлуудыг буцаана. Аль нэг багцад олгож чадахгүй бол None.
        """
        ...


class PoolProvider:
    """Урьдчилан импортолсон eSIM сангаас (esim_profiles хүснэгт) олгоно.

    Дараа нь нийлүүлэгчийн API-аар (Airalo, eSIM Access г.м.) шууд үүсгэдэг
    provider-оор сольж болно — EsimProvider интерфейсийг л хэрэгжүүлэхэд хангалттай.
    """

    def __init__(self, db: Database):
        self.db = db

    async def issue(self, order: Order) -> list[EsimProfile] | None:
        async with self.db.session() as s, s.begin():
            assigned = await self._assigned(s, order.id)
            missing = Counter(order.package_skus) - Counter(p.package_sku for p in assigned)
            complete = True
            for sku, count in missing.items():
                for _ in range(count):
                    # Олгосон хэсэг нь захиалгад нөөцлөгдөж үлдэнэ — сан нэмэгдэхэд дахин дуудахад гүйцээнэ
                    complete = await self._allocate(s, order.id, sku) and complete
            if not complete:
                return None
            assigned = await self._assigned(s, order.id)
        by_sku = {sku: [p for p in assigned if p.package_sku == sku] for sku in set(order.package_skus)}
        return [by_sku[sku].pop(0) for sku in order.package_skus]

    async def _assigned(self, s: AsyncSession, order_id: str) -> list[EsimProfile]:
        rows = await s.scalars(select(EsimProfile).where(EsimProfile.order_id == order_id).order_by(EsimProfile.id))
        return list(rows)

    async def _allocate(self, s: AsyncSession, order_id: str, sku: str) -> bool:
        # Өөр захиалга яг тэр мөчид ижил профайлыг авбал дахин оролдоно
        for _ in range(3):
            candidate = (
                select(EsimProfile.id)
                .where(EsimProfile.package_sku == sku, EsimProfile.order_id.is_(None))
                .order_by(EsimProfile.id)
                .limit(1)
                .scalar_subquery()
            )
            result = await s.execute(
                update(EsimProfile)
                .where(EsimProfile.id == candidate, EsimProfile.order_id.is_(None))
                .values(order_id=order_id, assigned_at=utcnow())
            )
            if result.rowcount:
                return True
        return False
