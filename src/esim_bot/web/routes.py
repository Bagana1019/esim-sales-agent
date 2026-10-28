import hashlib
import json
from functools import cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..catalog import LICENSE_CATEGORIES, Package, format_mnt
from ..db import OrderStatus
from ..delivery import out_of_stock_text
from ..orders import OutOfStock
from ..payments.fake import FakeQPay
from ..payments.qpay import QPayError
from . import listing
from .store import WebStore, is_valid_email, normalize_phone

STATIC_DIR = Path(__file__).parent / "static"
# ISO кодын монгол нэр (Unicode CLDR-ээс үүсгэж, урт нэрсийг товчилсон)
COUNTRY_NAMES: dict[str, str] = json.loads((Path(__file__).parent / "country_names.json").read_text(encoding="utf-8"))
MAX_QUANTITY = 5  # нэг захиалгад нэг багцаас авах дээд тоо

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@cache
def asset(path: str) -> str:
    """Статик файлын URL — агуулгын hash-тай тул файл өөрчлөгдөхөд хөтчийн кэш шинэчлэгдэнэ."""
    digest = hashlib.sha256((STATIC_DIR / path).read_bytes()).hexdigest()[:10]
    return f"/static/{path}?v={digest}"


@cache
def flag_url(country_code: str) -> str | None:
    """Улсын дугуй тугны зураг; байхгүй бол None (загвар emoji туг харуулна)."""
    path = f"flags/{country_code.lower()}.svg"
    return f"/static/{path}" if (STATIC_DIR / path).is_file() else None


def country_name(code: str) -> str:
    return COUNTRY_NAMES.get(code, code)


LICENSE_ICONS = {"windows": "laptop", "server": "server", "office": "office"}  # static/icons-ийн 3D дүрс


def license_category(key: str) -> dict[str, str]:
    """Лицензийн ангиллын нэр, 3D дүрс тэмдэг (template-д)."""
    return {"label": LICENSE_CATEGORIES.get(key, (key, ""))[0], "icon": LICENSE_ICONS.get(key, "key")}


templates.env.globals.update(
    asset=asset, flag_url=flag_url, format_mnt=format_mnt, country_name=country_name, license_category=license_category
)
templates.env.filters["country_name"] = country_name


def _store(request: Request) -> WebStore:
    return request.app.state.services.web


def _render(request: Request, name: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    store = _store(request)
    context |= {"shop": store.shop_name}
    return templates.TemplateResponse(request, name, context, status_code=status_code)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    catalog = _store(request).catalog
    tiles = [(c, packages) for c in catalog.countries if (packages := catalog.packages_covering(c.code))]
    stock = await _store(request).stock(p.sku for p in catalog.packages)
    # Нүүрний "...₮-өөс" үнэ, хямд багцын холбоос нөөцтэй багцаас (бүгд дууссан бол аль нэгээс)
    deal = min(catalog.packages, key=lambda p: (not stock[p.sku], p.price_mnt), default=None)
    covered = len({code for p in catalog.packages for code in p.countries})
    license_groups = [
        (key, label, group)
        for key, (label, _) in LICENSE_CATEGORIES.items()
        if (group := [lic for lic in catalog.licenses if lic.category == key])
    ]
    return _render(
        request,
        "index.html",
        tiles=tiles,
        license_groups=license_groups,
        deal=deal,
        total=len(catalog.packages),
        covered=covered,
    )


@router.get("/esim", response_class=HTMLResponse)
async def esim_list(request: Request) -> HTMLResponse:
    catalog = _store(request).catalog
    filters = listing.Filters.parse(catalog, request.query_params)
    packages = listing.apply(catalog, filters, COUNTRY_NAMES)
    stock = await _store(request).stock(p.sku for p in packages)
    return _render(
        request,
        "esim_list.html",
        filters=filters,
        packages=sorted(packages, key=lambda p: not stock[p.sku]),  # дууссаныг сүүлд
        stock=stock,
        countries=catalog.countries,
        country=catalog.country(filters.country) if filters.country else None,
        listing=listing,
    )


async def _detail_context(request: Request, sku: str) -> dict[str, Any]:
    catalog = _store(request).catalog
    package = catalog.package(sku)
    if package is None:
        raise HTTPException(status_code=404)
    country = catalog.country(package.country)
    if package.region:  # ижил бүсийн бусад багц
        similar = [p for p in catalog.packages if p.region == package.region]
    else:  # ижил улсын бусад багц
        similar = [p for p in catalog.packages_for(package.country) if not p.region]
    stock = await _store(request).stock([sku] + [p.sku for p in similar])
    # Ойролцоо үнэтэй, нөөцтэй багцуудыг эхэнд санал болгоно
    related = sorted(
        (p for p in similar if p.sku != sku), key=lambda p: (not stock[p.sku], abs(p.price_mnt - package.price_mnt))
    )[:4]
    return {
        "package": package,
        "country": country,
        "notes": country.notes if country and not package.region else "",
        "related": sorted(related, key=lambda p: p.price_mnt),
        "stock": stock,
        "max_quantity": min(MAX_QUANTITY, stock[sku]),
    }


@router.get("/esim/{sku}", response_class=HTMLResponse)
async def esim_detail(request: Request, sku: str) -> HTMLResponse:
    return _render(request, "esim_detail.html", **await _detail_context(request, sku))


@router.get("/license", response_class=HTMLResponse)
async def license_list(request: Request) -> HTMLResponse:
    catalog = _store(request).catalog
    filters = listing.LicenseFilters.parse(request.query_params)
    licenses = listing.apply_licenses(catalog, filters)
    stock = await _store(request).stock(lic.sku for lic in licenses)
    return _render(
        request,
        "license_list.html",
        filters=filters,
        licenses=sorted(licenses, key=lambda lic: not stock[lic.sku]),
        stock=stock,
        categories=[(key, label) for key, (label, _) in LICENSE_CATEGORIES.items() if any(lic.category == key for lic in catalog.licenses)],
        listing=listing,
    )


async def _license_context(request: Request, sku: str) -> dict[str, Any]:
    catalog = _store(request).catalog
    product = catalog.license(sku)
    if product is None:
        raise HTTPException(status_code=404)
    related = [lic for lic in catalog.licenses if lic.category == product.category and lic.sku != sku]
    stock = await _store(request).stock([sku] + [lic.sku for lic in related])
    related.sort(key=lambda lic: not stock[lic.sku])
    return {"license": product, "related": related[:4], "stock": stock, "max_quantity": min(MAX_QUANTITY, stock[sku])}


@router.get("/license/{sku}", response_class=HTMLResponse)
async def license_detail(request: Request, sku: str) -> HTMLResponse:
    return _render(request, "license_detail.html", **await _license_context(request, sku))


@router.get("/faq", response_class=HTMLResponse)
async def faq_page(request: Request) -> HTMLResponse:
    return _render(request, "faq.html")


@router.get("/install", response_class=HTMLResponse)
async def install_page(request: Request) -> HTMLResponse:
    return _render(request, "install.html")


@router.get("/checkout")
async def checkout_page(request: Request, sku: str) -> RedirectResponse:
    """Хуучин холбоос: бүтээгдэхүүнийг танилцуулга хуудаснаас нь захиална."""
    product = _store(request).catalog.product(sku)
    if product is None:
        raise HTTPException(status_code=404)
    return RedirectResponse(f"/esim/{sku}" if isinstance(product, Package) else f"/license/{sku}", status_code=303)


@router.post("/checkout")
async def checkout_submit(
    request: Request, sku: str = Form(...), email: str = Form(...), phone: str = Form(""), quantity: int = Form(1)
) -> Any:
    product = _store(request).catalog.product(sku)
    if product is None:
        raise HTTPException(status_code=404)
    quantity = min(max(quantity, 1), MAX_QUANTITY)
    email = email.strip()

    async def form_error(status_code: int, error: str) -> HTMLResponse:
        if isinstance(product, Package):
            template, context = "esim_detail.html", await _detail_context(request, sku)
        else:
            template, context = "license_detail.html", await _license_context(request, sku)
        qty = max(min(quantity, context["max_quantity"]), 1)
        return _render(request, template, status_code, email=email, phone=phone, quantity=qty, error=error, **context)

    if not is_valid_email(email):
        return await form_error(400, "И-мэйл хаяг буруу байна.")
    if not (normalized_phone := normalize_phone(phone)):
        return await form_error(400, "Утасны дугаар буруу байна. Жишээ нь 99112233.")
    try:
        order = await _store(request).checkout(sku, email, normalized_phone, quantity)
    except OutOfStock as e:
        return await form_error(409, out_of_stock_text(_store(request).catalog, e.available))
    except QPayError:
        return await form_error(503, "Төлбөрийн систем түр ажиллахгүй байна. Хэсэг хугацааны дараа дахин оролдоно уу.")
    return RedirectResponse(_store(request).order_path(order), status_code=303)


@router.get("/check", response_class=HTMLResponse)
async def check_page(request: Request) -> HTMLResponse:
    return _render(request, "check.html")


@router.post("/check")
async def check_submit(request: Request, number: str = Form(...), email: str = Form(...)) -> Any:
    order = await _store(request).find_order(number, email)
    if order is None:
        error = "Захиалга олдсонгүй. Захиалгын дугаар, и-мэйлээ шалгаад дахин оролдоно уу."
        return _render(request, "check.html", 404, number=number, email=email, error=error)
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
