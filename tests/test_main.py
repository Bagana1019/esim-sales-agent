import pytest
from fastapi.testclient import TestClient

from esim_bot import main
from esim_bot.config import get_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "hook-secret")
    get_settings.cache_clear()
    with TestClient(main.app) as c:
        yield c
    get_settings.cache_clear()


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_telegram_webhook_requires_secret(client):
    assert client.post("/telegram/webhook", json={"update_id": 1}).status_code == 403
    ok = client.post(
        "/telegram/webhook", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret"}
    )
    assert ok.status_code == 200


def test_qpay_callback_rejects_bad_token(client):
    assert client.get("/qpay/callback", params={"order_id": "o1", "token": "nope"}).status_code == 403
