import base64

import httpx
import pytest
import respx

from esim_bot.config import Settings
from esim_bot.mailer import Attachment, FileMailer, MailError, MailgunMailer, SmtpMailer
from esim_bot.services import build_mailer

US = "https://api.mailgun.net/v3/mg.shop.mn/messages"


def settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


@respx.mock
async def test_mailgun_sends_text_and_attachments():
    route = respx.post(US).mock(return_value=httpx.Response(200, json={"id": "<1@mg.shop.mn>", "message": "Queued"}))
    async with httpx.AsyncClient() as http:
        mailer = MailgunMailer(http, "key-123", "mg.shop.mn", "NomadData <no-reply@mg.shop.mn>")
        await mailer.send("bat@example.com", "Захиалга #1", "Сайн байна уу", [Attachment("esim-1.png", b"PNGDATA")])

    request = route.calls.last.request
    assert request.headers["authorization"] == "Basic " + base64.b64encode(b"api:key-123").decode()
    body = request.content.decode()
    for name, value in [("from", "NomadData <no-reply@mg.shop.mn>"), ("to", "bat@example.com"),
                        ("subject", "Захиалга #1"), ("text", "Сайн байна уу")]:
        assert f'name="{name}"\r\n\r\n{value}\r\n' in body
    assert 'name="attachment"; filename="esim-1.png"' in body and "PNGDATA" in body


@respx.mock
async def test_mailgun_eu_region_and_errors():
    eu = respx.post("https://api.eu.mailgun.net/v3/mg.shop.mn/messages")
    eu.side_effect = [httpx.Response(401, text="Forbidden"), httpx.Response(400, json={"message": "to parameter is not a valid address"})]
    async with httpx.AsyncClient() as http:
        mailer = MailgunMailer(http, "bad", "mg.shop.mn", "a@mg.shop.mn", region="EU")
        with pytest.raises(MailError, match="Mailgun 401: Forbidden"):
            await mailer.send("bat@example.com", "s", "b")
        with pytest.raises(MailError, match="not a valid address"):
            await mailer.send("bat", "s", "b")
        with pytest.raises(ValueError, match="us эсвэл eu"):
            MailgunMailer(http, "k", "mg.shop.mn", "a@mg.shop.mn", region="asia")


async def test_build_mailer_picks_mailgun_then_smtp_then_file(tmp_path):
    async with httpx.AsyncClient() as http:
        mailgun = build_mailer(settings(mailgun_api_key="k", mailgun_domain="mg.shop.mn", smtp_host="smtp.x"), http)
        assert isinstance(mailgun, MailgunMailer) and mailgun.sender == "NomadData <no-reply@mg.shop.mn>"
        assert isinstance(build_mailer(settings(smtp_host="smtp.x", mailgun_api_key=""), http), SmtpMailer)
        assert isinstance(build_mailer(settings(mailgun_api_key="k"), http), FileMailer)  # домэйнгүй бол илгээхгүй


def test_sender_setting(monkeypatch):
    monkeypatch.delenv("MAIL_FROM", raising=False)
    assert settings(mail_from="Shop <hi@shop.mn>").sender == "Shop <hi@shop.mn>"
    # .env.example-ийн хуучин жишээ утга Mailgun домэйноор солигдоно
    monkeypatch.setenv("SMTP_FROM", "NomadData <no-reply@example.com>")
    assert settings(mailgun_domain="mg.shop.mn").sender == "NomadData <no-reply@mg.shop.mn>"
    monkeypatch.setenv("SMTP_FROM", "Shop <orders@shop.mn>")  # хуучин SMTP_FROM нэрийг уншсаар
    assert settings().sender == "Shop <orders@shop.mn>"
