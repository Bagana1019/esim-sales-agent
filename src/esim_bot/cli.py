"""Удирдлагын командууд.

  uv run esim chat --fake-payments   # терминал дээр ботыг турших (Telegram хэрэггүй)
  uv run esim import-esims esims.csv # eSIM санд нэмэх
  uv run esim stock                  # үлдэгдэл харах
  uv run esim set-webhook            # Telegram webhook бүртгэх
"""

import argparse
import asyncio
from pathlib import Path

from .channels.base import Button, Keyboard
from .config import get_settings
from .db import Database
from .inventory import import_esims, stock_by_sku
from .payments.fake import FakeQPay
from .services import build_services

OUT_DIR = Path("out")


class ConsoleChannel:
    name = "console"

    def __init__(self) -> None:
        self.buttons: list[Button] = []
        self._photos = 0

    async def send_text(self, chat_id: str, text: str, buttons: Keyboard | None = None) -> None:
        print(f"\n🤖 {text}")
        self._show(buttons)

    async def send_photo(self, chat_id: str, photo: bytes, caption: str = "", buttons: Keyboard | None = None) -> None:
        OUT_DIR.mkdir(exist_ok=True)
        self._photos += 1
        path = OUT_DIR / f"photo_{self._photos}.png"
        path.write_bytes(photo)
        print(f"\n🖼  [зураг: {path}]\n{caption}")
        self._show(buttons)

    def _show(self, buttons: Keyboard | None) -> None:
        if not buttons:
            return
        self.buttons = [b for row in buttons for b in row]
        for i, b in enumerate(self.buttons, 1):
            print(f"   [{i}] {b.text}" + (f"  → {b.url}" if b.url else ""))


async def chat(fake_payments: bool) -> None:
    settings = get_settings()
    console = ConsoleChannel()
    fake = FakeQPay() if fake_payments else None
    services = await build_services(settings, payments=fake, extra_channels={console.name: console})
    if fake and not await stock_by_sku(services.db):
        added = await import_esims(services.db, "data/esims.example.csv")
        print(f"(Туршилтын {added} eSIM санд нэмэгдлээ)")

    print("Товчлуур дарахдаа дугаарыг нь бичнэ. /start — эхнээс, /pay — хуурамч төлбөр төлөх, /quit — гарах")
    chat_id = "local"
    bot = services.bot
    await bot.handle_start(console, chat_id)
    try:
        while True:
            text = (await asyncio.to_thread(input, "\n> ")).strip()
            if not text:
                continue
            if text == "/quit":
                break
            if text == "/start":
                await bot.handle_start(console, chat_id)
            elif text == "/pay" and fake:
                print(f"({fake.mark_all_paid()} нэхэмжлэх төлөгдсөн гэж тэмдэглэлээ — 'Төлбөр шалгах' дарна уу)")
            elif text.isdigit() and 0 < int(text) <= len(console.buttons):
                button = console.buttons[int(text) - 1]
                if button.data:
                    await bot.handle_button(console, chat_id, button.data)
                else:
                    print(f"(Холбоос: {button.url})")
            else:
                await bot.handle_text(console, chat_id, text)
    finally:
        await services.aclose()


async def import_cmd(path: str) -> None:
    db = Database(get_settings().database_url)
    await db.create_all()
    print(f"{await import_esims(db, path)} eSIM нэмэгдлээ")
    await stock_cmd(db)


async def stock_cmd(db: Database | None = None) -> None:
    db = db or Database(get_settings().database_url)
    await db.create_all()
    stock = await stock_by_sku(db)
    for sku, count in sorted(stock.items()):
        print(f"{sku:20} {count}")
    if not stock:
        print("Сан хоосон байна")
    await db.dispose()


async def set_webhook() -> None:
    settings = get_settings()
    services = await build_services(settings)
    try:
        if services.telegram is None:
            raise SystemExit("TELEGRAM_BOT_TOKEN тохируулаагүй байна")
        url = f"{settings.public_base_url.rstrip('/')}/telegram/webhook"
        await services.telegram.set_webhook(url, settings.telegram_webhook_secret)
        print(f"Webhook бүртгэгдлээ: {url}")
    finally:
        await services.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="esim")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_chat = sub.add_parser("chat", help="терминал дээр ботыг турших")
    p_chat.add_argument("--fake-payments", action="store_true", help="QPay-ийн оронд хуурамч төлбөр")
    p_import = sub.add_parser("import-esims", help="CSV-ээс eSIM санд нэмэх")
    p_import.add_argument("path")
    sub.add_parser("stock", help="eSIM үлдэгдэл")
    sub.add_parser("set-webhook", help="Telegram webhook бүртгэх")
    args = parser.parse_args()

    if args.cmd == "chat":
        asyncio.run(chat(args.fake_payments))
    elif args.cmd == "import-esims":
        asyncio.run(import_cmd(args.path))
    elif args.cmd == "stock":
        asyncio.run(stock_cmd())
    elif args.cmd == "set-webhook":
        asyncio.run(set_webhook())


if __name__ == "__main__":
    main()
