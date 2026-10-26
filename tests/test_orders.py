from esim_bot.db import OrderStatus
from esim_bot.inventory import stock_by_sku
from esim_bot.orders import PaymentOutcome


class DeliverySpy:
    def __init__(self):
        self.calls = []

    async def __call__(self, order, profile):
        self.calls.append((order.id, profile.iccid))


async def test_pays_and_delivers_exactly_once(orders, qpay):
    order, invoice = await orders.create_order("test", "u1", "JP-5D-3GB")
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
    assert deliver.calls == [(order.id, "8981000000000000001")]

    result = await orders.process_payment(order.id, deliver)
    assert result.outcome == PaymentOutcome.ALREADY_DELIVERED
    assert len(deliver.calls) == 1


async def test_each_order_gets_its_own_profile(orders, qpay, db):
    first, _ = await orders.create_order("test", "u1", "JP-5D-3GB")
    second, _ = await orders.create_order("test", "u2", "JP-5D-3GB")
    third, _ = await orders.create_order("test", "u3", "JP-5D-3GB")
    qpay.mark_all_paid()
    deliver = DeliverySpy()

    assert (await orders.process_payment(first.id, deliver)).outcome == PaymentOutcome.DELIVERED
    assert (await orders.process_payment(second.id, deliver)).outcome == PaymentOutcome.DELIVERED
    assert (await orders.process_payment(third.id, deliver)).outcome == PaymentOutcome.OUT_OF_STOCK
    assert len({iccid for _, iccid in deliver.calls}) == 2
    assert "JP-5D-3GB" not in await stock_by_sku(db)


async def test_out_of_stock_recovers_after_restock(orders, qpay, db):
    order, _ = await orders.create_order("test", "u1", "KR-7D-10GB")  # санд байхгүй багц
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
        s.add(EsimProfile(package_sku="KR-7D-10GB", iccid="999", smdp_address="smdp.test", activation_code="X"))

    result = await orders.process_payment(order.id, deliver)
    assert result.outcome == PaymentOutcome.DELIVERED
    assert deliver.calls == [(order.id, "999")]


async def test_order_lookup_is_scoped_to_chat(orders):
    order, _ = await orders.create_order("test", "u1", "TH-8D-15GB")
    assert await orders.get_for_chat(order.id, "test", "u1") is not None
    assert await orders.get_for_chat(order.id, "test", "intruder") is None
    assert await orders.get_for_chat(order.id, "telegram", "u1") is None


async def test_callback_token(orders):
    token = orders.callback_token("abc")
    assert orders.verify_callback_token("abc", token)
    assert not orders.verify_callback_token("abd", token)
    assert f"token={token}" in orders.callback_url("abc")
