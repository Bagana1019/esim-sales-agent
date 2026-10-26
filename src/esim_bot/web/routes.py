from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..catalog import Package, format_mnt
from ..db import OrderStatus
from ..delivery import product_label
from ..payments.fake import FakeQPay
from ..payments.qpay import QPayError
from .store import WebStore, is_valid_email

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _store(request: Request) -> WebStore:
    return request.app.state.services.web


def _render(request: Request, name: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    store = _store(request)
    context |= {"shop": store.shop_name, "format_mnt": format_mnt}
    return templates.TemplateResponse(request, name, context, status_code=status_code)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    catalog = _store(request).catalog
    groups = [(c, catalog.packages_for(c.code)) for c in catalog.countries]
    return _render(request, "index.html", groups=[g for g in groups if g[1]], licenses=catalog.licenses)


def _checkout_context(request: Request, sku: str) -> dict[str, Any]:
    catalog = _store(request).catalog
    product = catalog.product(sku)
    if product is None:
        raise HTTPException(status_code=404)
    return {"product": product, "label": product_label(catalog, sku), "is_esim": isinstance(product, Package)}


@router.get("/checkout", response_class=HTMLResponse)
async def checkout_page(request: Request, sku: str) -> HTMLResponse:
    return _render(request, "checkout.html", **_checkout_context(request, sku))


@router.post("/checkout")
async def checkout_submit(request: Request, sku: str = Form(...), email: str = Form(...)) -> Any:
    context = _checkout_context(request, sku)
    email = email.strip()
    if not is_valid_email(email):
        return _render(request, "checkout.html", 400, email=email, error="И-мэйл хаяг буруу байна.", **context)
    try:
        order = await _store(request).checkout(sku, email)
    except QPayError:
        error = "Төлбөрийн систем түр ажиллахгүй байна. Хэсэг хугацааны дараа дахин оролдоно уу."
        return _render(request, "checkout.html", 503, email=email, error=error, **context)
    return RedirectResponse(_store(request).order_path(order), status_code=303)


async def _order_or_404(request: Request, order_id: str, token: str):
    order = await _store(request).get_order(order_id, token)
    if order is None:
        raise HTTPException(status_code=404)
    return order


@router.get("/order/{order_id}", response_class=HTMLResponse)
async def order_page(request: Request, order_id: str, t: str = "") -> HTMLResponse:
    order = await _order_or_404(request, order_id, t)
    view = await _store(request).order_view(order)
    fake = isinstance(request.app.state.services.payments, FakeQPay)
    return _render(request, "order.html", token=t, fake_payments=fake, **view)


@router.get("/api/orders/{order_id}/status")
async def order_status(request: Request, order_id: str, t: str = "") -> dict[str, str]:
    order = await _order_or_404(request, order_id, t)
    if order.status != OrderStatus.DELIVERED:
        # QPay callback ирээгүй ч (локал туршилт г.м.) хуудас нээлттэй байхад төлбөрийг шалгаж хүргэнэ
        if result := await _store(request).process(order_id):
            order = result.order
    return {"status": order.status}


@router.post("/dev/pay/{order_id}")
async def dev_pay(request: Request, order_id: str, t: str = "") -> RedirectResponse:
    """Зөвхөн FAKE_PAYMENTS горимд: захиалгыг төлсөнд тооцно."""
    payments = request.app.state.services.payments
    if not isinstance(payments, FakeQPay):
        raise HTTPException(status_code=404)
    order = await _order_or_404(request, order_id, t)
    payments.mark_paid(order.qpay_invoice_id or "")
    return RedirectResponse(f"/order/{order_id}?t={t}", status_code=303)
