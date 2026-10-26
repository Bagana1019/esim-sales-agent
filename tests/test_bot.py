async def test_button_flow_to_delivery(bot, channel, qpay):
    await bot.handle_start(channel, "u1")
    assert channel.button_data() == ["c:JP", "c:KR", "c:TH"]

    await bot.handle_button(channel, "u1", "c:JP")
    assert channel.button_data() == ["p:JP-5D-2GB", "p:JP-10D-2GB", "p:JP-15D-20GB", "menu"]
    assert "25,000₮" in channel.sent[-1]["buttons"][0][0].text

    await bot.handle_button(channel, "u1", "p:JP-5D-2GB")
    invoice_msg = channel.sent[-1]
    assert invoice_msg["kind"] == "photo"
    assert invoice_msg["photo"].startswith(b"\x89PNG")
    assert "25,000₮" in invoice_msg["text"]
    [check] = channel.button_data()
    assert check.startswith("chk:")

    await bot.handle_button(channel, "u1", check)
    assert "хараахан орж ирээгүй" in channel.sent[-1]["text"]

    qpay.mark_all_paid()
    await bot.handle_button(channel, "u1", check)
    qr, info = channel.sent[-2], channel.sent[-1]
    assert qr["kind"] == "photo"
    assert "SM-DP+ хаяг: rsp.example-provider.com" in info["text"]
    assert "TEST-ACTIVATION-CODE-0001" in info["text"]


async def test_qpay_callback_delivers(bot, channel, qpay, orders):
    order = await bot.start_checkout(channel, "u1", "TH-7D-3GB")
    qpay.mark_all_paid()
    channel.sent.clear()

    await bot.on_payment_notification(order.id)

    assert [m["kind"] for m in channel.sent] == ["photo", "text"]
    assert all(m["chat_id"] == "u1" for m in channel.sent)
    assert "TEST-ACTIVATION-CODE-0004" in channel.sent[-1]["text"]


async def test_cannot_check_someone_elses_order(bot, channel):
    order = await bot.start_checkout(channel, "u1", "TH-7D-3GB")
    await bot.handle_button(channel, "intruder", f"chk:{order.id}")
    assert channel.sent[-1] == {"kind": "text", "chat_id": "intruder", "text": "Захиалга олдсонгүй.", "buttons": []}


async def test_out_of_stock_notifies_admin_once(bot, channel, qpay):
    order = await bot.start_checkout(channel, "u1", "KR-30D-UNL")
    qpay.mark_all_paid()
    channel.sent.clear()

    await bot.handle_button(channel, "u1", f"chk:{order.id}")
    await bot.handle_button(channel, "u1", f"chk:{order.id}")

    admin_msgs = [m for m in channel.sent if m["chat_id"] == "admin"]
    user_msgs = [m for m in channel.sent if m["chat_id"] == "u1"]
    assert len(admin_msgs) == 1 and order.id in admin_msgs[0]["text"]
    assert len(user_msgs) == 2 and all("Төлбөр баталгаажлаа" in m["text"] for m in user_msgs)
