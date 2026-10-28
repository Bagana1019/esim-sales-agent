import io

import qrcode

from .catalog import Catalog
from .db import EsimProfile, LicenseKey, Order


def qr_png(data: str) -> bytes:
    img = qrcode.make(data, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


DEFAULT_ACTIVATION = "Settings → System → Activation → Change product key → түлхүүрээ оруулна."


def out_of_stock_text(catalog: Catalog, available: dict[str, int]) -> str:
    """Захиалах үед нөөц хүрэхгүй бол харилцагчид харуулах текст (available: sku → үлдсэн тоо)."""
    parts = [
        f"{product_label(catalog, sku)}: " + (f"зөвхөн {count} ширхэг үлдсэн" if count else "түр дууссан")
        for sku, count in available.items()
    ]
    return "Уучлаарай, " + "; ".join(parts) + "."


def low_stock_text(catalog: Catalog, low_stock: dict[str, int]) -> str:
    """Борлуулалтын дараа нөөц багассан тухай админд очих мэдэгдэл."""
    lines = [
        f"• {sku} ({product_label(catalog, sku)}): " + (f"{count} үлдлээ" if count else "ДУУСЛАА, зарагдахгүй")
        for sku, count in low_stock.items()
    ]
    return "📉 Нөөц багасаж байна:\n" + "\n".join(lines) + "\nНэмэх: uv run esim import-esims файл.csv (лиценз: import-licenses)"


def product_label(catalog: Catalog, sku: str) -> str:
    if lic := catalog.license(sku):
        return f"{lic.emoji} {lic.name}"
    package = catalog.package(sku)
    if package is None:
        return sku
    if package.region:
        prefix = f"🌏 {package.region.name}"
    else:
        country = catalog.country(package.country)
        prefix = country.label if country else package.country
    return f"{prefix} - {package.days} хоног / {package.data}"


def order_label(catalog: Catalog, skus: list[str]) -> str:
    return " + ".join(product_label(catalog, sku) for sku in skus)


def esim_message(catalog: Catalog, order: Order, profile: EsimProfile, index: int = 1, total: int = 1) -> str:
    numbering = f" ({index}/{total})" if total > 1 else ""
    return (
        f"✅ Төлбөр баталгаажлаа! Таны eSIM{numbering} бэлэн боллоо.\n"
        f"Багц: {product_label(catalog, profile.package_sku)}\n"
        f"Захиалга: #{order.id[:8]}\n\n"
        "📲 Суулгах заавар\n"
        "iPhone: Settings → Cellular (Үүрэн холбоо) → Add eSIM → Use QR Code → дээрх QR-ыг уншуулна.\n"
        "Android: Settings → Connections → SIM manager → Add eSIM → Scan QR code.\n\n"
        "QR уншуулж чадахгүй бол гараар оруулна:\n"
        f"SM-DP+ хаяг: {profile.smdp_address}\n"
        f"Идэвхжүүлэх код: {profile.activation_code}\n"
        f"ICCID: {profile.iccid}\n\n"
        "⚠️ Анхаарах зүйлс\n"
        "• Аялахаасаа өмнө Wi-Fi-тэй газар суулгаарай.\n"
        "• QR кодыг ихэвчлэн нэг л удаа суулгах боломжтой тул eSIM-ээ утаснаасаа устгаж болохгүй.\n"
        "• Очсон газраа энэ eSIM дээр Data Roaming-ийг асааж, мобайл датаг энэ шугам руу шилжүүлнэ."
    )


def license_message(catalog: Catalog, order: Order, key: LicenseKey) -> str:
    lic = catalog.license(key.package_sku)
    activation = (lic.activation if lic and lic.activation else DEFAULT_ACTIVATION).strip()
    return (
        "✅ Төлбөр баталгаажлаа! Таны лиценз бэлэн боллоо.\n"
        f"Бүтээгдэхүүн: {product_label(catalog, key.package_sku)}\n"
        f"Захиалга: #{order.id[:8]}\n\n"
        f"🔑 Бүтээгдэхүүний түлхүүр:\n{key.product_key}\n\n"
        f"Идэвхжүүлэх заавар\n{activation}\n\n"
        "⚠️ Түлхүүрийг бусдад бүү дамжуулаарай. Нэг түлхүүр нэг төхөөрөмжид идэвхждэг."
    )


def multi_esim_note(total: int) -> str:
    return (
        f"ℹ️ Танд {total} eSIM ирлээ. Бүгдийг нь аялахаасаа өмнө суулгаарай. Эхлээд 1-р eSIM-ээр "
        "мобайл дата ашиглаж, хугацаа эсвэл дата нь дуусахад Settings → Cellular (Mobile data) хэсгээс "
        "мобайл датаг дараагийн eSIM руу шилжүүлнэ."
    )
