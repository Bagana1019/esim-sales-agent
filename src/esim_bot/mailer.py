"""И-мэйл илгээх. SMTP тохируулаагүй бол (локал туршилт) .eml файл болгон хадгална."""

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol
from uuid import uuid4

log = logging.getLogger(__name__)


@dataclass
class Attachment:
    filename: str
    content: bytes
    mime: str = "image/png"


class Mailer(Protocol):
    async def send(self, to: str, subject: str, body: str, attachments: list[Attachment] | None = None) -> None: ...


def build_message(sender: str, to: str, subject: str, body: str, attachments: list[Attachment] | None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    for a in attachments or []:
        maintype, subtype = a.mime.split("/", 1)
        msg.add_attachment(a.content, maintype=maintype, subtype=subtype, filename=a.filename)
    return msg


class SmtpMailer:
    def __init__(self, host: str, port: int, username: str, password: str, sender: str):
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.sender = sender

    async def send(self, to: str, subject: str, body: str, attachments: list[Attachment] | None = None) -> None:
        msg = build_message(self.sender, to, subject, body, attachments)
        await asyncio.to_thread(self._send, msg)

    def _send(self, msg: EmailMessage) -> None:
        if self.port == 465:
            server: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, timeout=30)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=30)
            server.starttls()
        with server:
            if self.username:
                server.login(self.username, self.password)
            server.send_message(msg)


class FileMailer:
    """Локал туршилтад: и-мэйлийг илгээхгүй, outbox хавтаст .eml файл болгон хадгална."""

    def __init__(self, outbox: str | Path, sender: str):
        self.outbox = Path(outbox)
        self.sender = sender

    async def send(self, to: str, subject: str, body: str, attachments: list[Attachment] | None = None) -> None:
        msg = build_message(self.sender, to, subject, body, attachments)
        self.outbox.mkdir(parents=True, exist_ok=True)
        path = self.outbox / f"{uuid4().hex[:12]}.eml"
        path.write_bytes(msg.as_bytes())
        log.info("И-мэйл (SMTP тохируулаагүй тул файлд хадгаллаа): %s → %s", to, path)
