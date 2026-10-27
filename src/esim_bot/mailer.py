"""И-мэйл илгээх: Mailgun API, SMTP, эсвэл (локал туршилт) .eml файл болгон хадгална."""

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import httpx

log = logging.getLogger(__name__)

MAILGUN_API = {"us": "https://api.mailgun.net", "eu": "https://api.eu.mailgun.net"}


class MailError(Exception):
    pass


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

    def __str__(self) -> str:
        return f"SMTP ({self.host}:{self.port})"

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


class MailgunMailer:
    """Mailgun-ы HTTP API-аар илгээнэ (SMTP порт хаалттай серверт ч ажиллана)."""

    def __init__(self, http: httpx.AsyncClient, api_key: str, domain: str, sender: str, region: str = "us"):
        base = MAILGUN_API.get(region.strip().lower())
        if base is None:
            raise ValueError(f"MAILGUN_REGION нь us эсвэл eu байна, {region!r} биш")
        self.http = http
        self.api_key = api_key
        self.url = f"{base}/v3/{domain}/messages"
        self.sender = sender

    def __str__(self) -> str:
        return f"Mailgun ({self.url})"

    async def send(self, to: str, subject: str, body: str, attachments: list[Attachment] | None = None) -> None:
        resp = await self.http.post(
            self.url,
            auth=("api", self.api_key),
            data={"from": self.sender, "to": to, "subject": subject, "text": body},
            files=[("attachment", (a.filename, a.content, a.mime)) for a in attachments or []],
        )
        if resp.status_code != 200:
            raise MailError(f"Mailgun {resp.status_code}: {_mailgun_error(resp)}")
        log.info("И-мэйл Mailgun-аар илгээгдлээ: %s (%s)", to, resp.json().get("id"))


def _mailgun_error(resp: httpx.Response) -> str:
    try:
        return resp.json().get("message") or resp.text
    except ValueError:  # 401 үед JSON биш "Forbidden" гэж ирдэг
        return resp.text.strip()


class FileMailer:
    """Локал туршилтад: и-мэйлийг илгээхгүй, outbox хавтаст .eml файл болгон хадгална."""

    def __init__(self, outbox: str | Path, sender: str):
        self.outbox = Path(outbox)
        self.sender = sender

    def __str__(self) -> str:
        return f"файл ({self.outbox}) — Mailgun, SMTP тохируулаагүй тул илгээхгүй"

    async def send(self, to: str, subject: str, body: str, attachments: list[Attachment] | None = None) -> None:
        msg = build_message(self.sender, to, subject, body, attachments)
        self.outbox.mkdir(parents=True, exist_ok=True)
        path = self.outbox / f"{uuid4().hex[:12]}.eml"
        path.write_bytes(msg.as_bytes())
        log.info("И-мэйл (SMTP тохируулаагүй тул файлд хадгаллаа): %s → %s", to, path)
