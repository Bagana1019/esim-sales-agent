async def test_button_flow_to_delivery(bot, channel, qpay):
    await bot.handle_start(channel, "u1")
    assert channel.button_data() == ["c:CN", "c:KR", "c:JP", "c:TH", "lic"]

    await bot.handle_button(channel, "u1", "c:JP")
    assert channel.button_data() == ["p:JP-5D-3GB", "p:JP-7D-5GB", "menu"]
    assert "25,000₮" in channel.sent[-1]["buttons"][0][0].text

    await bot.handle_button(channel, "u1", "p:JP-5D-3GB")
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
    order = await bot.start_checkout(channel, "u1", ["TH-8D-15GB"])
    qpay.mark_all_paid()
    channel.sent.clear()

    await bot.on_payment_notification(order.id)

    assert [m["kind"] for m in channel.sent] == ["photo", "text"]
    assert all(m["chat_id"] == "u1" for m in channel.sent)
    assert "TEST-ACTIVATION-CODE-0004" in channel.sent[-1]["text"]


async def test_cannot_check_someone_elses_order(bot, channel):
    order = await bot.start_checkout(channel, "u1", ["TH-8D-15GB"])
    await bot.handle_button(channel, "intruder", f"chk:{order.id}")
    assert channel.sent[-1] == {"kind": "text", "chat_id": "intruder", "text": "Захиалга олдсонгүй.", "buttons": []}


async def test_out_of_stock_notifies_admin_once(bot, channel, qpay):
    order = await bot.start_checkout(channel, "u1", ["CN-7D-5GB"])
    qpay.mark_all_paid()
    channel.sent.clear()

    await bot.handle_button(channel, "u1", f"chk:{order.id}")
    await bot.handle_button(channel, "u1", f"chk:{order.id}")

    admin_msgs = [m for m in channel.sent if m["chat_id"] == "admin"]
    user_msgs = [m for m in channel.sent if m["chat_id"] == "u1"]
    assert len(admin_msgs) == 1 and order.id in admin_msgs[0]["text"]
    assert len(user_msgs) == 2 and all("Төлбөр баталгаажлаа" in m["text"] for m in user_msgs)


def test_catalog_understands_latin_country_names(catalog):
    assert catalog.country("Hytad").code == "CN"
    assert catalog.country(" solongos ").code == "KR"
    assert catalog.country("Япон").code == "JP"
    assert [p.sku for p in catalog.packages_for("thailand")] == ["TH-8D-15GB"]
    assert catalog.country("Франц") is None and catalog.packages_for("Франц") == []


async def test_agent_can_show_buttons_by_latin_name(bot, channel):
    assert await bot.show_packages(channel, "u1", "Hytad")
    assert channel.button_data() == ["p:CN-3D-1GB", "p:CN-5D-3GB", "p:CN-7D-5GB", "menu"]


async def test_bundle_checkout_and_delivery(bot, channel, qpay):
    order = await bot.start_checkout(channel, "u1", ["JP-5D-3GB", "CN-3D-1GB"])
    invoice = channel.sent[-1]
    assert invoice["kind"] == "photo"
    assert "Япон - 5 хоног" in invoice["text"] and "Хятад - 3 хоног" in invoice["text"]
    assert "Төлөх дүн: 40,000₮" in invoice["text"]

    qpay.mark_all_paid()
    channel.sent.clear()
    await bot.handle_button(channel, "u1", f"chk:{order.id}")

    assert [m["kind"] for m in channel.sent] == ["photo", "text", "photo", "text", "text"]
    assert "eSIM (1/2)" in channel.sent[1]["text"] and "eSIM (2/2)" in channel.sent[3]["text"]
    assert "Танд 2 eSIM ирлээ" in channel.sent[-1]["text"]


async def test_license_button_flow(bot, channel, qpay):
    await bot.handle_button(channel, "u1", "lic")
    assert channel.button_data() == [
        "p:WIN11-HOME", "p:WIN11-PRO", "p:WIN11-PRO-WS", "p:WINSRV25-STD", "p:WINSRV25-DC", "menu",
    ]

    await bot.handle_button(channel, "u1", "p:WIN11-PRO")
    invoice = channel.sent[-1]
    assert invoice["kind"] == "photo" and "🪟 Windows 11 Pro" in invoice["text"]
    assert "Төлөх дүн: 150,000₮" in invoice["text"]

    qpay.mark_all_paid()
    channel.sent.clear()
    await bot.handle_button(channel, "u1", invoice["buttons"][-1][0].data)

    [msg] = channel.sent
    assert msg["kind"] == "text"
    assert "AAAAA-BBBBB-CCCCC-DDDDD-00001" in msg["text"]
    assert "Settings → System → Activation" in msg["text"]
    assert "microsoft.com/software-download/windows11" in msg["text"]
