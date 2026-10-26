"""Удирдлагын командууд.

  uv run esim chat --fake-payments   # терминал дээр ботыг турших (Telegram хэрэггүй)
  uv run esim poll --fake-payments   # Telegram ботыг компьютер дээрээ ажиллуулах (ngrok хэрэггүй)
  uv run esim import-esims esims.csv # eSIM санд нэмэх
  uv run esim import-licenses keys.csv # лицензийн түлхүүр санд нэмэх
  uv run esim stock                  # үлдэгдэл харах
  uv run esim set-webhook            # Telegram webhook бүртгэх
"""

import argparse
import asyncio
import logging
from pathlib import Path

import httpx

from .channels.base import Button, Keyboard
from .channels.telegram import dispatch, parse_update
from .config import get_settings, missing_token_hint
from .db import Database, EsimProfile, LicenseKey
from .inventory import import_esims, import_licenses, stock_by_sku
from .payments.fake import FakeQPay
from .services import build_services

OUT_DIR = Path("out")
log = logging.getLogger(__name__)


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
    if fake:
        await seed_demo_stock(services.db)

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


async def poll(fake_payments: bool) -> None:
    """Telegram-аас long polling-оор мессеж татна — нийтийн URL, ngrok хэрэггүй."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # хүсэлтийн URL-д bot token орсон байдаг

    settings = get_settings()
    fake = FakeQPay() if fake_payments else None
    services = await build_services(settings, payments=fake)
    tg = services.telegram
    tasks: set[asyncio.Task[None]] = set()
    try:
        if tg is None:
            raise SystemExit(missing_token_hint("TELEGRAM_BOT_TOKEN"))
        if fake:
            await seed_demo_stock(services.db)
        await tg.delete_webhook()  # webhook бүртгэлтэй үед getUpdates ажиллахгүй
        me = await tg.get_me()
        print(f"@{me['username']} ажиллаж байна. Telegram-аас ботоо хайж олоод Start дарна уу.")
        if fake:
            print("Хуурамч төлбөрийн горим: QR гарсны дараа чатад /pay гэж бичээд 'Төлбөр шалгах' дарна.")
        print("Зогсоох: Ctrl+C")

        offset: int | None = None
        while True:
            try:
                updates = await tg.get_updates(offset)
            except (httpx.HTTPError, RuntimeError):
                log.exception("getUpdates алдаа, 3 секундын дараа дахин оролдоно")
                await asyncio.sleep(3)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                incoming = parse_update(update)
                if incoming is None:
                    continue
                log.info("chat_id=%s %s: %s", incoming.chat_id, incoming.kind, incoming.value[:80])
                if fake and incoming.kind == "text" and incoming.value == "/pay":
                    count = fake.mark_all_paid()
                    await tg.send_text(incoming.chat_id, f"(Туршилт) {count} нэхэмжлэх төлөгдсөнд тооцлоо. 'Төлбөр шалгах' дарна уу.")
                    continue
                # LLM-ийн хариуг хүлээх зуур бусад мессежийг зогсоохгүйн тулд тусад нь ажиллуулна
                task = asyncio.create_task(dispatch(services.bot, tg, incoming))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
    finally:
        if tasks:  # эхэлсэн хариултуудыг дуусгаж байж хаана
            await asyncio.wait(tasks, timeout=15)
        await services.aclose()


async def seed_demo_stock(db: Database) -> None:
    """Хуурамч төлбөрийн горимд сан хоосон бол туршилтын eSIM, лиценз нэмнэ."""
    if not await stock_by_sku(db, EsimProfile):
        print(f"(Туршилтын {await import_esims(db, 'data/esims.example.csv')} eSIM санд нэмэгдлээ)")
    if not await stock_by_sku(db, LicenseKey):
        print(f"(Туршилтын {await import_licenses(db, 'data/licenses.example.csv')} лиценз санд нэмэгдлээ)")


async def import_cmd(path: str, licenses: bool = False) -> None:
    db = Database(get_settings().database_url)
    await db.create_all()
    if licenses:
        print(f"{await import_licenses(db, path)} лиценз нэмэгдлээ")
    else:
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
            raise SystemExit(missing_token_hint("TELEGRAM_BOT_TOKEN"))
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
    p_poll = sub.add_parser("poll", help="Telegram ботыг локалаар ажиллуулах (webhook-гүй)")
    p_poll.add_argument("--fake-payments", action="store_true", help="QPay-ийн оронд хуурамч төлбөр")
    p_import = sub.add_parser("import-esims", help="CSV-ээс eSIM санд нэмэх")
    p_import.add_argument("path")
    p_import_lic = sub.add_parser("import-licenses", help="CSV-ээс (package_sku,product_key) лиценз санд нэмэх")
    p_import_lic.add_argument("path")
    sub.add_parser("stock", help="eSIM үлдэгдэл")
    sub.add_parser("set-webhook", help="Telegram webhook бүртгэх")
    args = parser.parse_args()

    if args.cmd == "chat":
        asyncio.run(chat(args.fake_payments))
    elif args.cmd == "poll":
        try:
            asyncio.run(poll(args.fake_payments))
        except KeyboardInterrupt:
            print("\nЗогслоо")
    elif args.cmd == "import-esims":
        asyncio.run(import_cmd(args.path))
    elif args.cmd == "import-licenses":
        asyncio.run(import_cmd(args.path, licenses=True))
    elif args.cmd == "stock":
        asyncio.run(stock_cmd())
    elif args.cmd == "set-webhook":
        asyncio.run(set_webhook())


if __name__ == "__main__":
    main()
