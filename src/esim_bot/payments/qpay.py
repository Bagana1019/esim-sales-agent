"""QPay merchant API v2 клиент.

Баримт бичиг: https://developer.qpay.mn — sandbox: https://merchant-sandbox.qpay.mn/v2
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)


class QPayError(Exception):
    pass


@dataclass
class Invoice:
    invoice_id: str
    qr_text: str
    qr_image_b64: str
    short_url: str | None = None
    bank_urls: list[dict[str, Any]] = field(default_factory=list)


class PaymentGateway(Protocol):
    async def create_invoice(
        self, *, order_id: str, amount: int, description: str, callback_url: str
    ) -> Invoice: ...

    async def find_payment(self, invoice_id: str, expected_amount: int) -> str | None: ...


class QPayClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        base_url: str,
        client_id: str,
        client_secret: str,
        invoice_code: str,
    ):
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.invoice_code = invoice_code
        self._token: str | None = None
        self._token_expires_at = 0.0

    async def _get_token(self, force: bool = False) -> str:
        if self._token and not force and time.time() < self._token_expires_at:
            return self._token
        resp = await self.http.post(
            f"{self.base_url}/auth/token", auth=(self.client_id, self.client_secret)
        )
        if resp.status_code != 200:
            raise QPayError(f"QPay token авч чадсангүй: {resp.status_code} {resp.text}")
        data = resp.json()
        self._token = data["access_token"]
        expires_in = float(data.get("expires_in") or 0)
        # QPay заримдаа expires_in-д үргэлжлэх хугацаа биш unix timestamp буцаадаг
        expires_at = expires_in if expires_in > 1_000_000_000 else time.time() + expires_in
        self._token_expires_at = max(expires_at - 60, time.time() + 60)
        return self._token

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(2):
            token = await self._get_token(force=attempt > 0)
            resp = await self.http.post(
                f"{self.base_url}{path}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 401 and attempt == 0:
                continue
            if resp.status_code != 200:
                raise QPayError(f"QPay {path} алдаа: {resp.status_code} {resp.text}")
            return resp.json()
        raise QPayError(f"QPay {path}: нэвтрэх эрх баталгаажсангүй")

    async def create_invoice(
        self, *, order_id: str, amount: int, description: str, callback_url: str
    ) -> Invoice:
        data = await self._post(
            "/invoice",
            {
                "invoice_code": self.invoice_code,
                "sender_invoice_no": order_id,
                "invoice_receiver_code": "terminal",
                "invoice_description": description,
                "amount": amount,
                "callback_url": callback_url,
            },
        )
        return Invoice(
            invoice_id=data["invoice_id"],
            qr_text=data.get("qr_text", ""),
            qr_image_b64=data.get("qr_image", ""),
            short_url=data.get("qPay_shortUrl"),
            bank_urls=data.get("urls", []),
        )

    async def find_payment(self, invoice_id: str, expected_amount: int) -> str | None:
        """Нэхэмжлэх бүрэн төлөгдсөн бол payment_id буцаана, үгүй бол None."""
        data = await self._post(
            "/payment/check",
            {
                "object_type": "INVOICE",
                "object_id": invoice_id,
                "offset": {"page_number": 1, "page_limit": 100},
            },
        )
        paid_rows = [r for r in data.get("rows", []) if r.get("payment_status") == "PAID"]
        paid_total = sum(float(r.get("payment_amount") or 0) for r in paid_rows)
        if paid_rows and paid_total >= expected_amount:
            return str(paid_rows[0]["payment_id"])
        if paid_rows:
            log.warning("Invoice %s дутуу төлөгдсөн: %s / %s", invoice_id, paid_total, expected_amount)
        return None
