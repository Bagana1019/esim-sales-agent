from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Button:
    text: str
    data: str | None = None  # дарахад бот руу буцаж ирэх утга (жишээ: "c:JP")
    url: str | None = None  # эсвэл холбоос нээх товч


Keyboard = list[list[Button]]


class Channel(Protocol):
    """Мессенжер платформын (Telegram, Messenger, ...) нийтлэг интерфейс."""

    name: str

    async def send_text(self, chat_id: str, text: str, buttons: Keyboard | None = None) -> None: ...

    async def send_photo(
        self, chat_id: str, photo: bytes, caption: str = "", buttons: Keyboard | None = None
    ) -> None: ...
