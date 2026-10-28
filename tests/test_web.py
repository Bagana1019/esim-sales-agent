import html
import re
from email import message_from_bytes, policy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from esim_bot import main
from esim_bot.config import get_settings


@pytest.fixture
def outbox(tmp_path) -> Path:
    return tmp_path / "outbox"


@pytest.fixture
def client(tmp_path, outbox, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/web.db")
    monkeypatch.setenv("FAKE_PAYMENTS", "true")
    monkeypatch.setenv("EMAIL_OUTBOX_DIR", str(outbox))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://shop.example")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    # .env файлд Mailgun, SMTP тохируулсан байсан ч тестээс жинхэнэ и-мэйл илгээхгүй
    monkeypatch.setenv("MAILGUN_API_KEY", "")
    monkeypatch.setenv("SMTP_HOST", "")
    get_settings.cache_clear()
    with TestClient(main.app) as c:
        yield c
    get_settings.cache_clear()


def emails(outbox: Path):
    return [message_from_bytes(p.read_bytes(), policy=policy.default) for p in sorted(outbox.glob("*.eml"))] if outbox.exists() else []


def buy(client, sku, email="bat@example.com"):
    resp = client.post("/checkout", data={"sku": sku, "email": email, "phone": "9911 2233"}, follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"]


def catalog_of(client):
    return client.app.state.services.web.catalog


def test_home_links_countries_to_listing(client):
    html = client.get("/").text
    catalog = catalog_of(client)
    assert "Nomad <span>Data</span>" in html and "Дэлхийн дата" in html
    assert 'src="/static/flags/cn.svg"' in html and '/esim?country=CN' in html
    cheapest = min(catalog.packages, key=lambda p: p.price_mnt)
    assert f'href="/esim/{cheapest.sku}"' in html  # hero-ийн хамгийн хямд багц
    assert f"Бүх {len(catalog.packages)} багц" in html
    assert '/license?category=office' in html and '/license?category=windows' in html
    # толгойн цэс, тусламжийн хэсэг
    assert all(f'href="{path}"' in html for path in ("/esim", "/license", "/install", "/faq", "/check"))


def test_help_pages(client):
    faq = client.get("/faq").text
    assert "Түгээмэл асуулт" in faq and "*#06#" in faq and "Microsoft 365" in faq and 'aria-current="page"' in faq
    install = client.get("/install").text
    assert all(name in install for name in ("iPhone", "Samsung", "Pixel")) and "Data Roaming" in install


def test_license_listing_and_detail(client):
    catalog = catalog_of(client)
    listing_page = client.get("/license").text
    assert all(f'href="/license/{lic.sku}"' in listing_page for lic in catalog.licenses)
    office = client.get("/license?category=office").text
    assert 'href="/license/OFFICE24-HB"' in office and 'href="/license/WIN11-PRO"' not in office
    assert 'href="/license/OFFICE24-HB"' in client.get("/license?q=outlook").text
    assert "data-sortable" in client.get("/license?view=table").text

    page = client.get("/license/OFFICE24-HB").text
    lic = catalog.license("OFFICE24-HB")
    assert html.escape(lic.name) in page and "Outlook" in page and "setup.office.com" in page and "Хураангуй" in page
    assert client.get("/license/NOPE").status_code == 404
    redirect = client.get("/checkout?sku=WIN11-PRO", follow_redirects=False)
    assert redirect.status_code == 303 and redirect.headers["location"] == "/license/WIN11-PRO"


def test_checkout_requires_valid_phone(client):
    bad = client.post("/checkout", data={"sku": "JP-7D-5GB", "email": "bat@example.com", "phone": "12345"})
    assert bad.status_code == 400 and "Утасны дугаар буруу" in bad.text and 'value="12345"' in bad.text
    bad_license = client.post("/checkout", data={"sku": "WIN11-PRO", "email": "bat@example.com", "phone": ""})
    assert bad_license.status_code == 400 and "Windows 11 Pro" in bad_license.text  # лицензийн хуудсаараа
    too_long = client.post("/checkout", data={"sku": "JP-7D-5GB", "email": "bat@example.com", "phone": "+976 3232078554"})
    assert too_long.status_code == 400  # +976-ийн дараа 8 орон

    resp = client.post(
        "/checkout", data={"sku": "JP-7D-5GB", "email": "bat@example.com", "phone": "+976 9911-2233"}, follow_redirects=False
    )
    assert "bat@example.com · 99112233" in client.get(resp.headers["location"]).text


def test_esim_listing_filters(client):
    catalog = catalog_of(client)
    html = client.get("/esim").text
    assert all(f'href="/esim/{p.sku}"' in html for p in catalog.packages)

    japan = client.get("/esim?country=JP").text
    covering = catalog.packages_covering("JP")
    assert any(p.region for p in covering)  # олон улсын багц Японд ч харагдана
    assert all(f'href="/esim/{p.sku}"' in japan for p in covering)
    assert 'href="/esim/CN-3D-1GB"' not in japan

    multi = client.get("/esim?scope=multi&gb=20").text
    shown = re.findall(r'href="/esim/([\w-]+)"', multi)
    assert shown and all(catalog.package(s).region and catalog.package(s).total_gb >= 20 for s in shown)

    search = client.get("/esim?q=yapon").text  # латин нэрээр
    assert re.findall(r'href="/esim/([\w-]+)"', search) and "CN-3D-1GB" not in search
    assert "Таны шүүлтүүрт тохирох багц олдсонгүй" in client.get("/esim?q=zzzz").text
    assert client.get("/esim?country=XX&gb=7&sort=nope").status_code == 200  # буруу утгыг үл тоомсорлоно

    table = client.get("/esim?view=table&sort=price").text
    assert "data-sortable" in table and "≈3,750₮" in table  # Тайланд 30,000₮ / 8 хоног


def test_package_detail_page(client):
    regional = next(p for p in catalog_of(client).packages if p.region)
    page = client.get(f"/esim/{regional.sku}").text
    assert regional.region.name in page and f"{len(regional.countries)} улс" in page and "Хураангуй" in page
    assert client.get("/esim/NOPE").status_code == 404
    assert client.get("/esim/WIN11-PRO").status_code == 404  # лиценз танилцуулгагүй


def test_checkout_validates_input(client):
    assert client.get("/checkout?sku=NOPE").status_code == 404
    redirect = client.get("/checkout?sku=TH-8D-15GB", follow_redirects=False)
    assert redirect.status_code == 303 and redirect.headers["location"] == "/esim/TH-8D-15GB"
    page = client.get("/esim/TH-8D-15GB").text
    assert "Тайланд · 8 хоног, Нийт 15GB" in page and "30,000₮" in page and 'name="phone"' in page
    bad = client.post("/checkout", data={"sku": "TH-8D-15GB", "email": "not-an-email", "phone": "99112233", "quantity": "2"})
    assert bad.status_code == 400 and "И-мэйл хаяг буруу" in bad.text and "60,000₮" in bad.text


def test_checkout_quantity_makes_one_order_with_several_esims(client, outbox):
    # Туршилтын горимд багц бүрд 2 eSIM байна
    resp = client.post("/checkout", data={"sku": "JP-7D-5GB", "email": "bat@example.com", "phone": "99112233", "quantity": "2"}, follow_redirects=False)
    page = client.get(resp.headers["location"]).text
    price = catalog_of(client).package("JP-7D-5GB").price_mnt
    assert page.count("Япон - 7 хоног / Нийт 5GB") == 2 and f"{price * 2:,}₮" in page

    too_many = client.post("/checkout", data={"sku": "JP-5D-3GB", "email": "bat@example.com", "phone": "99112233", "quantity": "9"})
    assert too_many.status_code == 409 and "зөвхөн 2 ширхэг үлдсэн" in too_many.text
    assert 'max="2"' in too_many.text and "Сүүлийн 2 ширхэг" in too_many.text


def test_sold_out_package_is_shown_but_cannot_be_bought(client):
    resp = client.post("/checkout", data={"sku": "JP-7D-5GB", "email": "bat@example.com", "phone": "99112233", "quantity": "2"}, follow_redirects=False)
    order_id, token = re.match(r"/order/(\w+)\?t=(\w+)", resp.headers["location"]).groups()
    client.post(f"/dev/pay/{order_id}?t={token}")
    assert client.get(f"/api/orders/{order_id}/status?t={token}").json() == {"status": "delivered"}

    detail = client.get("/esim/JP-7D-5GB").text
    assert "Түр дууссан" in detail and "disabled" in detail and 'name="email"' not in detail
    listing = client.get("/esim?country=JP").text
    assert re.findall(r'href="/esim/([\w-]+)"', listing)[-1] == "JP-7D-5GB"  # дууссан нь сүүлд
    assert '<span class="badge gray">Түр дууссан</span>' in listing

    again = client.post("/checkout", data={"sku": "JP-7D-5GB", "email": "bat@example.com", "phone": "99112233"})
    assert again.status_code == 409 and "Түр дууссан. Удахгүй нэмэгдэнэ" in again.text


def test_esim_purchase_end_to_end(client, outbox):
    order_url = buy(client, "JP-7D-5GB")
    order_id, token = re.match(r"/order/(\w+)\?t=(\w+)", order_url).groups()

    page = client.get(order_url).text
    assert "Төлбөр хүлээгдэж байна" in page and 'src="data:image/png;base64,' in page
    assert client.get(f"/order/{order_id}?t=wrong").status_code == 404
    assert client.get(f"/api/orders/{order_id}/status?t={token}").json() == {"status": "pending"}

    client.post(f"/dev/pay/{order_id}?t={token}")
    assert client.get(f"/api/orders/{order_id}/status?t={token}").json() == {"status": "delivered"}

    page = client.get(order_url).text
    assert "Төлбөр баталгаажлаа" in page and "TEST-ACTIVATION-CODE-0013" in page

    [mail] = emails(outbox)
    assert mail["To"] == "bat@example.com"
    assert "TEST-ACTIVATION-CODE-0013" in mail.get_body(("plain",)).get_content()
    assert f"https://shop.example/order/{order_id}?t={token}" in mail.get_body(("plain",)).get_content()
    assert "https://shop.example/install" in mail.get_body(("plain",)).get_content()
    assert [a.get_filename() for a in mail.iter_attachments()] == ["esim-1.png"]

    # Дахин шалгахад давхар и-мэйл илгээхгүй
    client.get(f"/api/orders/{order_id}/status?t={token}")
    assert len(emails(outbox)) == 1


def test_mail_failure_does_not_block_delivery(client, caplog):
    class BrokenMailer:
        async def send(self, *args, **kwargs):
            raise RuntimeError("Mailgun 401: Forbidden")

    client.app.state.services.web.mailer = BrokenMailer()
    order_url = buy(client, "JP-7D-5GB")
    order_id, token = re.match(r"/order/(\w+)\?t=(\w+)", order_url).groups()
    client.post(f"/dev/pay/{order_id}?t={token}")

    assert client.get(f"/api/orders/{order_id}/status?t={token}").json() == {"status": "delivered"}
    assert "TEST-ACTIVATION-CODE-0013" in client.get(order_url).text  # QR код хуудсанд гарсаар
    assert "bat@example.com руу и-мэйл илгээж чадсангүй" in caplog.text and "Mailgun 401" in caplog.text


def test_order_lookup_by_number_and_email(client):
    order_url = buy(client, "JP-7D-5GB")
    order_id = re.match(r"/order/(\w+)\?", order_url).group(1)
    assert "Захиалгын дугаар" in client.get("/check").text

    found = client.post("/check", data={"number": f" #{order_id[:8].upper()}", "email": "bat@example.com"}, follow_redirects=False)
    assert found.status_code == 303 and found.headers["location"] == order_url

    wrong_email = client.post("/check", data={"number": order_id[:8], "email": "other@example.com"})
    assert wrong_email.status_code == 404 and "Захиалга олдсонгүй" in wrong_email.text
    assert client.post("/check", data={"number": order_id[:4], "email": "bat@example.com"}).status_code == 404


def test_license_purchase_shows_key(client, outbox):
    order_url = buy(client, "WIN11-PRO", "office@example.com")
    order_id, token = re.match(r"/order/(\w+)\?t=(\w+)", order_url).groups()
    client.post(f"/dev/pay/{order_id}?t={token}")
    client.get(f"/api/orders/{order_id}/status?t={token}")

    page = client.get(order_url).text
    assert "TEST-WIN11-PRO-0001" in page and "Change product key" in page
    [mail] = emails(outbox)
    assert "TEST-WIN11-PRO-0001" in mail.get_body(("plain",)).get_content()


def test_qpay_callback_delivers_web_order(client, outbox):
    order_url = buy(client, "CN-3D-1GB")
    order_id, token = re.match(r"/order/(\w+)\?t=(\w+)", order_url).groups()
    services = client.app.state.services
    services.payments.mark_all_paid()

    callback = services.orders.callback_url(order_id).replace("https://shop.example", "")
    assert client.get(callback).text == "SUCCESS"

    assert len(emails(outbox)) == 1
    assert "Төлбөр баталгаажлаа" in client.get(order_url).text


def test_dev_pay_disabled_without_fake_payments(client, monkeypatch):
    order_url = buy(client, "CN-3D-1GB")
    order_id, token = re.match(r"/order/(\w+)\?t=(\w+)", order_url).groups()
    monkeypatch.setattr(client.app.state.services, "payments", object())
    assert client.post(f"/dev/pay/{order_id}?t={token}").status_code == 404


def test_static_assets_are_served(client):
    html = client.get("/").text
    css = re.search(r'href="(/static/site\.css\?v=\w+)"', html).group(1)
    assert client.get(css).status_code == 200
    assert client.get("/static/icons/globe.webp").headers["content-type"] == "image/webp"
    assert client.get("/static/flags/jp.svg").status_code == 200


def test_legal_pages_show_company_details(client, monkeypatch):
    for path, title in [("/terms", "Үйлчилгээний нөхцөл"), ("/privacy", "Нууцлалын бодлого"), ("/refund", "Буцаалтын бодлого")]:
        page = client.get(path)
        assert page.status_code == 200 and f"<h1>{title}</h1>" in page.text
        assert f'href="{path}" aria-current="page"' in page.text
        assert "<mark" in page.text and "[COMPANY_NAME]" in page.text  # тохируулаагүйг тод харуулна
    home = client.get("/").text
    assert all(f'href="{p}"' in home for p in ("/terms", "/privacy", "/refund"))
    assert 'href="/terms">Үйлчилгээний нөхцөл</a>, <a href="/refund">' in client.get("/esim/JP-7D-5GB").text  # төлөхийн өмнө
    # Тусдаа замаар бүртгэсэн тул бусад зам халхлагдахгүй
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/checkout?sku=JP-7D-5GB", follow_redirects=False).status_code == 303

    settings = client.app.state.settings
    monkeypatch.setattr(settings, "company_name", "Номад Дата ХХК")
    monkeypatch.setattr(settings, "company_register", "1234567")
    monkeypatch.setattr(settings, "support_email", "help@nomaddata.mn")
    terms = client.get("/terms").text
    assert "Номад Дата ХХК" in terms and 'href="mailto:help@nomaddata.mn"' in terms and "[COMPANY_NAME]" not in terms
    assert "[SUPPORT_PHONE]" in terms
    assert "Номад Дата ХХК (УБД 1234567)" in client.get("/").text
