import pytest

from esim_bot.db import OrderStatus
from esim_bot.inventory import seed_demo_stock, stock_by_sku
from esim_bot.orders import OutOfStock, PaymentOutcome

from .conftest import take_stock


class DeliverySpy:
    def __init__(self):
        self.calls = []

    async def __call__(self, order, profiles):
        self.calls.append((order.id, [p.iccid for p in profiles]))


async def test_pays_and_delivers_exactly_once(orders, qpay):
    order, invoice = await orders.create_order("test", "u1", ["JP-5D-3GB"])
    assert order.amount_mnt == 25000
    assert order.qpay_invoice_id == invoice.invoice_id
    deliver = DeliverySpy()

    result = await orders.process_payment(order.id, deliver)
    assert result.outcome == PaymentOutcome.NOT_PAID
    assert deliver.calls == []

    qpay.mark_all_paid()
    result = await orders.process_payment(order.id, deliver)
    assert result.outcome == PaymentOutcome.DELIVERED
    assert result.order.status == OrderStatus.DELIVERED
    assert deliver.calls == [(order.id, ["8981000000000000001"])]

    result = await orders.process_payment(order.id, deliver)
    assert result.outcome == PaymentOutcome.ALREADY_DELIVERED
    assert len(deliver.calls) == 1


async def test_each_order_gets_its_own_profile(orders, qpay, db):
    first, _ = await orders.create_order("test", "u1", ["JP-5D-3GB"])
    second, _ = await orders.create_order("test", "u2", ["JP-5D-3GB"])
    third, _ = await orders.create_order("test", "u3", ["JP-5D-3GB"])
    qpay.mark_all_paid()
    deliver = DeliverySpy()

    assert (await orders.process_payment(first.id, deliver)).outcome == PaymentOutcome.DELIVERED
    assert (await orders.process_payment(second.id, deliver)).outcome == PaymentOutcome.DELIVERED
    assert (await orders.process_payment(third.id, deliver)).outcome == PaymentOutcome.OUT_OF_STOCK
    assert len({iccid for _, iccids in deliver.calls for iccid in iccids}) == 2
    assert "JP-5D-3GB" not in await stock_by_sku(db)


async def test_out_of_stock_items_cannot_be_ordered(orders):
    with pytest.raises(OutOfStock) as e:
        await orders.create_order("test", "u1", ["KR-7D-10GB", "JP-5D-3GB"])  # KR-7D-10GB санд байхгүй
    assert e.value.available == {"KR-7D-10GB": 0}
    with pytest.raises(OutOfStock) as e:
        await orders.create_order("test", "u1", ["JP-5D-3GB"] * 3)  # 2 л үлдсэн
    assert e.value.available == {"JP-5D-3GB": 2}
    with pytest.raises(OutOfStock):
        await orders.create_order("test", "u1", ["WIN11-HOME"])
    await orders.create_order("test", "u1", ["JP-5D-3GB"] * 2)


async def test_low_stock_is_reported_after_delivery(orders, qpay):
    orders.low_stock_threshold = 1
    first, _ = await orders.create_order("test", "u1", ["JP-5D-3GB", "TH-8D-15GB"])
    qpay.mark_all_paid()
    result = await orders.process_payment(first.id, DeliverySpy())
    assert result.outcome == PaymentOutcome.DELIVERED
    assert result.low_stock == {"JP-5D-3GB": 1, "TH-8D-15GB": 0}


async def test_out_of_stock_recovers_after_restock(orders, qpay, db):
    order, _ = await orders.create_order("test", "u1", ["KR-5D-1GBD"])
    await take_stock(db, "KR-5D-1GBD")  # сүүлийн ширхгийг өөр хүн түрүүлж авав
    qpay.mark_all_paid()
    deliver = DeliverySpy()

    result = await orders.process_payment(order.id, deliver)
    assert result.outcome == PaymentOutcome.OUT_OF_STOCK
    assert result.newly_failed
    assert result.order.status == OrderStatus.NEEDS_ATTENTION

    result = await orders.process_payment(order.id, deliver)
    assert not result.newly_failed  # админд дахин мэдэгдэхгүй

    from esim_bot.db import EsimProfile

    async with db.session() as s, s.begin():
        s.add(EsimProfile(package_sku="KR-5D-1GBD", iccid="999", smdp_address="smdp.test", activation_code="X"))

    result = await orders.process_payment(order.id, deliver)
    assert result.outcome == PaymentOutcome.DELIVERED
    assert deliver.calls == [(order.id, ["999"])]



async def test_demo_stock_fills_products_missing_from_an_existing_pool(orders, qpay, db, catalog):
    # Каталогт шинэ бүтээгдэхүүн нэмэгдсэн хуучин сан: сан хоосон биш ч зарим бүтээгдэхүүнд нөөц байхгүй
    await seed_demo_stock(db, catalog)
    stock = await stock_by_sku(db)
    assert {p.sku for p in catalog.packages} | {lic.sku for lic in catalog.licenses} <= stock.keys()
    assert stock["JP-5D-3GB"] == 2 and stock["WIN11-PRO"] == 1  # байсан нөөцөд хүрэхгүй

    order, _ = await orders.create_order("test", "u1", ["KR-7D-10GB", "KR-7D-10GB"])
    qpay.mark_all_paid()
    assert (await orders.process_payment(order.id, DeliverySpy())).outcome == PaymentOutcome.DELIVERED
    assert "KR-7D-10GB" not in await stock_by_sku(db)

    await seed_demo_stock(db, catalog)  # туршилтаар зарагдаж дууссан бол ICCID давхардуулахгүйгээр дахин нэмнэ
    assert (await stock_by_sku(db))["KR-7D-10GB"] == 2


async def test_order_lookup_is_scoped_to_chat(orders):
    order, _ = await orders.create_order("test", "u1", ["TH-8D-15GB"])
    assert await orders.get_for_chat(order.id, "test", "u1") is not None
    assert await orders.get_for_chat(order.id, "test", "intruder") is None
    assert await orders.get_for_chat(order.id, "telegram", "u1") is None


async def test_callback_token(orders):
    token = orders.callback_token("abc")
    assert orders.verify_callback_token("abc", token)
    assert not orders.verify_callback_token("abd", token)
    assert f"token={token}" in orders.callback_url("abc")


async def test_bundle_order_one_invoice_two_esims(orders, qpay):
    order, _ = await orders.create_order("test", "u1", ["JP-5D-3GB", "CN-3D-1GB"])
    assert order.amount_mnt == 25000 + 15000
    assert list(qpay.invoices.values()) == [40000]  # нийт дүнгийн ганц нэхэмжлэх
    qpay.mark_all_paid()
    deliver = DeliverySpy()

    result = await orders.process_payment(order.id, deliver)

    assert result.outcome == PaymentOutcome.DELIVERED
    assert deliver.calls == [(order.id, ["8981000000000000001", "8986000000000000001"])]


async def test_bundle_waits_until_every_package_is_in_stock(orders, qpay, db):
    from esim_bot.db import EsimProfile

    order, _ = await orders.create_order("test", "u1", ["KR-5D-1GBD", "CN-3D-1GB"])
    await take_stock(db, "KR-5D-1GBD")  # төлөхөөс өмнө KR дууссан
    qpay.mark_all_paid()
    deliver = DeliverySpy()

    assert (await orders.process_payment(order.id, deliver)).outcome == PaymentOutcome.OUT_OF_STOCK
    assert deliver.calls == []  # хагас хүргэлт хийхгүй
    assert [p.package_sku for p in await orders.items_for(order.id)] == ["CN-3D-1GB"]  # нөөцлөгдсөн

    async with db.session() as s, s.begin():
        s.add(EsimProfile(package_sku="KR-5D-1GBD", iccid="777", smdp_address="smdp.test", activation_code="Y"))

    assert (await orders.process_payment(order.id, deliver)).outcome == PaymentOutcome.DELIVERED
    assert deliver.calls == [(order.id, ["777", "8986000000000000001"])]  # захиалгын дарааллаар


async def test_license_order_delivers_key(orders, qpay):
    order, _ = await orders.create_order("test", "u1", ["WIN11-PRO"])
    assert order.amount_mnt == 150000
    qpay.mark_all_paid()
    delivered = []

    async def deliver(o, items):
        delivered.extend(items)

    assert (await orders.process_payment(order.id, deliver)).outcome == PaymentOutcome.DELIVERED
    assert [i.product_key for i in delivered] == ["AAAAA-BBBBB-CCCCC-DDDDD-00001"]


async def test_mixed_order_esim_and_license(orders, qpay):
    order, _ = await orders.create_order("test", "u1", ["WIN11-PRO", "TH-8D-15GB"])
    assert order.amount_mnt == 150000 + 30000
    qpay.mark_all_paid()
    delivered = []

    async def capture(o, items):
        delivered.extend(type(i).__name__ for i in items)

    assert (await orders.process_payment(order.id, capture)).outcome == PaymentOutcome.DELIVERED
    assert delivered == ["LicenseKey", "EsimProfile"]  # захиалгын дарааллаар


async def test_license_out_of_stock(orders, qpay, db):
    order, _ = await orders.create_order("test", "u1", ["WIN11-PRO"])
    await take_stock(db, "WIN11-PRO")
    qpay.mark_all_paid()
    result = await orders.process_payment(order.id, DeliverySpy())
    assert result.outcome == PaymentOutcome.OUT_OF_STOCK


async def test_old_database_gets_phone_column_without_losing_orders(tmp_path):
    from sqlalchemy import text

    from esim_bot.db import Database, Order

    db = Database(f"sqlite+aiosqlite:///{tmp_path}/old.db")
    await db.create_all()
    async with db.engine.begin() as conn:  # phone баганагүй хуучин сан болгоно
        await conn.execute(text("ALTER TABLE orders DROP COLUMN phone"))
        await conn.execute(
            text("INSERT INTO orders (id, channel, chat_id, package_skus, amount_mnt, status, created_at) "
                 "VALUES ('old1', 'web', 'a@example.com', '[\"CN-3D-1GB\"]', 15000, 'delivered', CURRENT_TIMESTAMP)")
        )
    await db.create_all()
    async with db.session() as s:
        old = await s.get(Order, "old1")
        assert old.chat_id == "a@example.com" and old.phone is None
    await db.dispose()
