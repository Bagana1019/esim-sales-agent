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


def test_env_file_with_bom_and_cyrillic(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_bytes("﻿# Тохиргоо\nTELEGRAM_BOT_TOKEN=123:abc\n".encode("utf-8"))
    from esim_bot.config import Settings

    assert Settings(_env_file=env).telegram_bot_token == "123:abc"


def test_missing_token_hint(tmp_path, monkeypatch):
    from esim_bot.config import missing_token_hint

    monkeypatch.chdir(tmp_path)
    assert "олдсонгүй. Үүсгэнэ үү" in missing_token_hint("TELEGRAM_BOT_TOKEN")
    (tmp_path / ".env.txt").write_text("x")
    assert "Rename-Item .env.txt .env" in missing_token_hint("TELEGRAM_BOT_TOKEN")
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=\n")
    assert "утга бичигдээгүй" in missing_token_hint("TELEGRAM_BOT_TOKEN")


def test_live_qpay_refuses_default_secrets(tmp_path, monkeypatch):
    from esim_bot.config import Settings
    from esim_bot.services import ConfigError

    live = {"qpay_base_url": "https://merchant.qpay.mn/v2", "database_url": f"sqlite+aiosqlite:///{tmp_path}/live.db"}
    errors = Settings(_env_file=None, **live).production_errors()
    assert len(errors) == 2 and "CALLBACK_SECRET" in errors[0] and "QPAY_CLIENT_ID" in errors[1]
    assert Settings(_env_file=None).production_errors() == []  # sandbox
    good = Settings(_env_file=None, **live, callback_secret="x" * 32, qpay_client_id="NOMADDATA", qpay_invoice_code="NOMADDATA_INVOICE")
    assert good.production_errors() == []

    monkeypatch.setenv("DATABASE_URL", live["database_url"])
    monkeypatch.setenv("QPAY_BASE_URL", live["qpay_base_url"])
    monkeypatch.setenv("FAKE_PAYMENTS", "false")
    monkeypatch.setenv("CALLBACK_SECRET", "change-me")
    get_settings.cache_clear()
    with pytest.raises(ConfigError, match="CALLBACK_SECRET"), TestClient(main.app):
        pass
    get_settings.cache_clear()
