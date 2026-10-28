import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from esim_bot import main
from esim_bot.backup import backup_database, sqlite_path
from esim_bot.config import get_settings


async def test_backup_is_complete_and_old_copies_are_pruned(orders, db, tmp_path):
    await orders.create_order("test", "u1", ["JP-5D-3GB"])
    backups = tmp_path / "backups"
    backups.mkdir()
    for stamp in ("20250101-000000", "20250102-000000", "20250103-000000"):
        (backups / f"esim-{stamp}.db").write_bytes(b"old")

    info = backup_database(str(db.engine.url), backups, keep=2)

    assert info.orders == 1 and info.unused_esims == 5
    with sqlite3.connect(info.path) as copy:
        assert copy.execute("SELECT package_skus FROM orders").fetchone()[0] == '["JP-5D-3GB"]'
    assert sorted(p.name for p in backups.iterdir()) == ["esim-20250103-000000.db", info.path.name]


def test_backup_needs_a_sqlite_file(tmp_path):
    with pytest.raises(ValueError, match="зөвхөн SQLite"):
        sqlite_path("postgresql+asyncpg://user:secret@db/esim")
    with pytest.raises(FileNotFoundError):
        backup_database(f"sqlite+aiosqlite:///{tmp_path}/missing.db", tmp_path / "b")


def test_backup_command(tmp_path, monkeypatch, capsys):
    from esim_bot.cli import backup_cmd

    sqlite3.connect(tmp_path / "shop.db").executescript(
        "CREATE TABLE orders (id TEXT); CREATE TABLE esim_profiles (order_id TEXT); INSERT INTO esim_profiles VALUES (NULL);"
    )
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/shop.db")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "copies"))
    get_settings.cache_clear()
    backup_cmd()
    get_settings.cache_clear()
    assert "0 захиалга, 1 ашиглагдаагүй eSIM" in capsys.readouterr().out
    assert len(list((tmp_path / "copies").glob("esim-*.db"))) == 1


def test_live_server_backs_up_on_start(tmp_path, monkeypatch):
    for key, value in {
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path}/live.db",
        "BACKUP_DIR": str(tmp_path / "backups"),
        "FAKE_PAYMENTS": "false",
        "QPAY_BASE_URL": "https://merchant.qpay.mn/v2",
        "QPAY_CLIENT_ID": "NOMADDATA",
        "QPAY_INVOICE_CODE": "NOMADDATA_INVOICE",
        "CALLBACK_SECRET": "x" * 32,
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    with TestClient(main.app):
        deadline = time.monotonic() + 5
        while not list((tmp_path / "backups").glob("esim-*.db")) and time.monotonic() < deadline:
            time.sleep(0.05)
    get_settings.cache_clear()
    assert len(list((tmp_path / "backups").glob("esim-*.db"))) == 1
