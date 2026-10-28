import httpx
import pytest
import respx

from esim_bot.payments.qpay import QPayClient, QPayError

BASE = "https://merchant-sandbox.qpay.mn/v2"


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as http:
        yield QPayClient(http, base_url=BASE, client_id="TEST_MERCHANT", client_secret="123456", invoice_code="TEST_INVOICE")


def token_response(token="tok1"):
    return httpx.Response(200, json={"access_token": token, "refresh_token": "r", "expires_in": 86400})


@respx.mock
async def test_create_invoice_and_check_payment(client):
    auth = respx.post(f"{BASE}/auth/token").mock(return_value=token_response())
    invoice = respx.post(f"{BASE}/invoice").mock(
        return_value=httpx.Response(
            200,
            json={
                "invoice_id": "inv-1",
                "qr_text": "000201...",
                "qr_image": "iVBORw0KGgo=",
                "qPay_shortUrl": "https://s.qpay.mn/abc",
                "urls": [{"name": "Khan bank", "link": "khanbank://q?qPay_QRcode=..."}],
            },
        )
    )
    check = respx.post(f"{BASE}/payment/check").mock(
        return_value=httpx.Response(
            200,
            json={"count": 1, "rows": [{"payment_id": "p-1", "payment_status": "PAID", "payment_amount": "25000.00"}]},
        )
    )

    inv = await client.create_invoice(order_id="o1", amount=25000, description="eSIM", callback_url="https://x/cb")
    assert inv.invoice_id == "inv-1" and inv.short_url == "https://s.qpay.mn/abc"
    body = invoice.calls.last.request.read().decode()
    assert '"sender_invoice_no":"o1"' in body and '"invoice_code":"TEST_INVOICE"' in body
    assert invoice.calls.last.request.headers["authorization"] == "Bearer tok1"

    assert await client.find_payment("inv-1", 25000) == "p-1"
    assert auth.call_count == 1  # token дахин ашиглагдана
    assert '"object_id":"inv-1"' in check.calls.last.request.read().decode()


@respx.mock
async def test_partial_or_missing_payment_is_not_paid(client):
    respx.post(f"{BASE}/auth/token").mock(return_value=token_response())
    respx.post(f"{BASE}/payment/check").mock(
        side_effect=[
            httpx.Response(200, json={"count": 0, "rows": []}),
            httpx.Response(200, json={"count": 1, "rows": [{"payment_id": "p", "payment_status": "PAID", "payment_amount": 100}]}),
        ]
    )
    assert await client.find_payment("inv-1", 25000) is None
    assert await client.find_payment("inv-1", 25000) is None


@respx.mock
async def test_expired_token_is_refreshed(client):
    respx.post(f"{BASE}/auth/token").mock(side_effect=[token_response("old"), token_response("new")])
    route = respx.post(f"{BASE}/payment/check").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json={"rows": []})]
    )
    assert await client.find_payment("inv-1", 1) is None
    assert route.calls.last.request.headers["authorization"] == "Bearer new"


@respx.mock
async def test_errors_raise(client):
    respx.post(f"{BASE}/auth/token").mock(return_value=httpx.Response(401, text="bad credentials"))
    with pytest.raises(QPayError):
        await client.find_payment("inv-1", 1)


@respx.mock
async def test_qpay_test_command_waits_for_payment(tmp_path, monkeypatch, capsys):
    from esim_bot.cli import qpay_test
    from esim_bot.config import get_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QPAY_BASE_URL", BASE)
    get_settings.cache_clear()
    respx.post(f"{BASE}/auth/token").mock(return_value=token_response())
    invoice = respx.post(f"{BASE}/invoice").mock(
        return_value=httpx.Response(200, json={"invoice_id": "inv-9", "qr_text": "000201", "qPay_shortUrl": "https://s.qpay.mn/x"})
    )
    respx.post(f"{BASE}/payment/check").mock(
        side_effect=[
            httpx.Response(200, json={"rows": []}),
            httpx.Response(200, json={"rows": [{"payment_id": "p-9", "payment_status": "PAID", "payment_amount": 100}]}),
        ]
    )
    await qpay_test(100, poll_seconds=0)
    get_settings.cache_clear()

    out = capsys.readouterr().out
    assert "SANDBOX" in out and "https://s.qpay.mn/x" in out and "Төлбөр орлоо (payment_id p-9)" in out
    assert '"amount":100' in invoice.calls.last.request.read().decode()
    assert (tmp_path / "out" / "qpay-test.png").read_bytes().startswith(b"\x89PNG")
