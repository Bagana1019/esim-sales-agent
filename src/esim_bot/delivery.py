import io

import qrcode

from .catalog import Catalog
from .db import EsimProfile, Order


def qr_png(data: str) -> bytes:
    img = qrcode.make(data, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def package_label(catalog: Catalog, sku: str) -> str:
    package = catalog.package(sku)
    if package is None:
        return sku
    country = catalog.country(package.country)
    prefix = country.label if country else package.country
    return f"{prefix} - {package.days} хоног / {package.data}"


def esim_message(catalog: Catalog, order: Order, profile: EsimProfile) -> str:
    return (
        "✅ Төлбөр баталгаажлаа! Таны eSIM бэлэн боллоо.\n"
        f"Багц: {package_label(catalog, order.package_sku)}\n"
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
