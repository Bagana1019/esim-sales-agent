from collections import Counter
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .catalog import Catalog
from .db import Database, EsimProfile, LicenseKey, Order, utcnow

# Захиалгын нэг багцад олгогдох зүйл: eSIM профайл эсвэл лицензийн түлхүүр
Item = EsimProfile | LicenseKey


async def _assigned(s: AsyncSession, order_id: str) -> list[Item]:
    items: list[Item] = []
    for model in (EsimProfile, LicenseKey):
        items += await s.scalars(select(model).where(model.order_id == order_id).order_by(model.id))
    return items


def _in_order(order: Order, items: list[Item]) -> list[Item]:
    """Олгосон зүйлсийг order.package_skus-ийн дарааллаар эрэмбэлнэ."""
    by_sku = {sku: [i for i in items if i.package_sku == sku] for sku in set(order.package_skus)}
    return [by_sku[sku].pop(0) for sku in order.package_skus if by_sku[sku]]


async def order_items(db: Database, order: Order) -> list[Item]:
    """Захиалгад аль хэдийн олгогдсон eSIM, лицензүүд."""
    async with db.session() as s:
        return _in_order(order, await _assigned(s, order.id))


class FulfillmentProvider(Protocol):
    async def issue(self, order: Order) -> list[Item] | None:
        """Захиалгын sku бүрд нэг зүйл олгоно (order.package_skus-ийн дарааллаар).

        Дахин дуудахад ижил зүйлсийг буцаана. Аль нэгийг олгож чадахгүй бол None.
        """
        ...


class PoolProvider:
    """Урьдчилан импортолсон сангаас олгоно: eSIM — esim_profiles, лиценз — license_keys.

    Дараа нь нийлүүлэгчийн API-аар (Airalo, eSIM Access, лицензийн дистрибьютер г.м.) шууд
    үүсгэдэг provider-оор сольж болно — FulfillmentProvider интерфейсийг л хэрэгжүүлэхэд хангалттай.
    """

    def __init__(self, db: Database, catalog: Catalog):
        self.db = db
        self.catalog = catalog

    def _model(self, sku: str) -> type[Item]:
        return LicenseKey if self.catalog.license(sku) else EsimProfile

    async def issue(self, order: Order) -> list[Item] | None:
        async with self.db.session() as s, s.begin():
            assigned = await _assigned(s, order.id)
            missing = Counter(order.package_skus) - Counter(i.package_sku for i in assigned)
            complete = True
            for sku, count in missing.items():
                for _ in range(count):
                    # Олгосон хэсэг нь захиалгад нөөцлөгдөж үлдэнэ — сан нэмэгдэхэд дахин дуудахад гүйцээнэ
                    complete = await self._allocate(s, order.id, sku) and complete
            if not complete:
                return None
            assigned = await _assigned(s, order.id)
        return _in_order(order, assigned)

    async def _allocate(self, s: AsyncSession, order_id: str, sku: str) -> bool:
        model = self._model(sku)
        # Өөр захиалга яг тэр мөчид ижил зүйлийг авбал дахин оролдоно
        for _ in range(3):
            candidate = (
                select(model.id)
                .where(model.package_sku == sku, model.order_id.is_(None))
                .order_by(model.id)
                .limit(1)
                .scalar_subquery()
            )
            result = await s.execute(
                update(model)
                .where(model.id == candidate, model.order_id.is_(None))
                .values(order_id=order_id, assigned_at=utcnow())
            )
            if result.rowcount:
                return True
        return False
