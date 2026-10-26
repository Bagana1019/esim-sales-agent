import re
from email import message_from_bytes, policy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from esim_bot import main
from esim_bot.config import get_settings


@pytest.fixture
def outbox(tmp_path) -> Path:
    return tmp_path / "outbox"


@pytest.fixture
def client(tmp_path, outbox, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/web.db")
    monkeypatch.setenv("FAKE_PAYMENTS", "true")
    monkeypatch.setenv("EMAIL_OUTBOX_DIR", str(outbox))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://shop.example")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    get_settings.cache_clear()
    with TestClient(main.app) as c:
        yield c
    get_settings.cache_clear()


def emails(outbox: Path):
    return [message_from_bytes(p.read_bytes(), policy=policy.default) for p in sorted(outbox.glob("*.eml"))] if outbox.exists() else []


def buy(client, sku, email="bat@example.com"):
    resp = client.post("/checkout", data={"sku": sku, "email": email}, follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"]


def test_home_lists_esims_and_licenses(client):
    html = client.get("/").text
    assert "eSIM <span>Mongolia</span>" in html
    assert "🇨🇳 Хятад" in html and "VPN-гүйгээр" in html  # улсын тэмдэглэл
    assert "/checkout?sku=JP-7D-5GB" in html and "38,000₮" in html
    assert "Windows 11 Pro" in html and "/checkout?sku=WIN11-PRO" in html


def test_checkout_validates_input(client):
    assert client.get("/checkout?sku=NOPE").status_code == 404
    page = client.get("/checkout?sku=TH-8D-15GB").text
    assert "🇹🇭 Тайланд - 8 хоног / Нийт 15GB" in page and "30,000₮" in page
    bad = client.post("/checkout", data={"sku": "TH-8D-15GB", "email": "not-an-email"})
    assert bad.status_code == 400 and "И-мэйл хаяг буруу" in bad.text


def test_esim_purchase_end_to_end(client, outbox):
    order_url = buy(client, "JP-7D-5GB")
    order_id, token = re.match(r"/order/(\w+)\?t=(\w+)", order_url).groups()

    page = client.get(order_url).text
    assert "Төлбөр хүлээгдэж байна" in page and 'src="data:image/png;base64,' in page
    assert client.get(f"/order/{order_id}?t=wrong").status_code == 404
    assert client.get(f"/api/orders/{order_id}/status?t={token}").json() == {"status": "pending"}

    client.post(f"/dev/pay/{order_id}?t={token}")
    assert client.get(f"/api/orders/{order_id}/status?t={token}").json() == {"status": "delivered"}

    page = client.get(order_url).text
    assert "Төлбөр баталгаажлаа" in page and "TEST-ACTIVATION-CODE-0013" in page

    [mail] = emails(outbox)
    assert mail["To"] == "bat@example.com"
    assert "TEST-ACTIVATION-CODE-0013" in mail.get_body(("plain",)).get_content()
    assert f"https://shop.example/order/{order_id}?t={token}" in mail.get_body(("plain",)).get_content()
    assert [a.get_filename() for a in mail.iter_attachments()] == ["esim-1.png"]

    # Дахин шалгахад давхар и-мэйл илгээхгүй
    client.get(f"/api/orders/{order_id}/status?t={token}")
    assert len(emails(outbox)) == 1


def test_license_purchase_shows_key(client, outbox):
    order_url = buy(client, "WIN11-PRO", "office@example.com")
    order_id, token = re.match(r"/order/(\w+)\?t=(\w+)", order_url).groups()
    client.post(f"/dev/pay/{order_id}?t={token}")
    client.get(f"/api/orders/{order_id}/status?t={token}")

    page = client.get(order_url).text
    assert "TEST-WIN11-PRO-0001" in page and "Change product key" in page
    [mail] = emails(outbox)
    assert "TEST-WIN11-PRO-0001" in mail.get_body(("plain",)).get_content()


def test_qpay_callback_delivers_web_order(client, outbox):
    order_url = buy(client, "CN-3D-1GB")
    order_id, token = re.match(r"/order/(\w+)\?t=(\w+)", order_url).groups()
    services = client.app.state.services
    services.payments.mark_all_paid()

    callback = services.orders.callback_url(order_id).replace("https://shop.example", "")
    assert client.get(callback).text == "SUCCESS"

    assert len(emails(outbox)) == 1
    assert "Төлбөр баталгаажлаа" in client.get(order_url).text


def test_dev_pay_disabled_without_fake_payments(client, monkeypatch):
    order_url = buy(client, "CN-3D-1GB")
    order_id, token = re.match(r"/order/(\w+)\?t=(\w+)", order_url).groups()
    monkeypatch.setattr(client.app.state.services, "payments", object())
    assert client.post(f"/dev/pay/{order_id}?t={token}").status_code == 404
