"""Локал туршилт, тестэд зориулсан хуурамч QPay. Жинхэнэ мөнгө хөдлөхгүй."""

from uuid import uuid4

from .qpay import Invoice


class FakeQPay:
    def __init__(self) -> None:
        self.invoices: dict[str, int] = {}  # invoice_id -> amount
        self.paid: set[str] = set()

    async def create_invoice(self, *, order_id: str, amount: int, description: str, callback_url: str) -> Invoice:
        invoice_id = f"fake-{uuid4().hex[:12]}"
        self.invoices[invoice_id] = amount
        return Invoice(invoice_id=invoice_id, qr_text=f"FAKE-QPAY:{invoice_id}:{amount}", qr_image_b64="")

    async def find_payment(self, invoice_id: str, expected_amount: int) -> str | None:
        if invoice_id in self.paid and self.invoices.get(invoice_id, 0) >= expected_amount:
            return f"pay-{invoice_id}"
        return None

    def mark_all_paid(self) -> int:
        unpaid = set(self.invoices) - self.paid
        self.paid |= unpaid
        return len(unpaid)
