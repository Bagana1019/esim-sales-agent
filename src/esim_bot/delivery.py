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


def order_label(catalog: Catalog, skus: list[str]) -> str:
    return " + ".join(package_label(catalog, sku) for sku in skus)


def esim_message(catalog: Catalog, order: Order, profile: EsimProfile, index: int = 1, total: int = 1) -> str:
    numbering = f" ({index}/{total})" if total > 1 else ""
    return (
        f"✅ Төлбөр баталгаажлаа! Таны eSIM{numbering} бэлэн боллоо.\n"
        f"Багц: {package_label(catalog, profile.package_sku)}\n"
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


def multi_esim_note(total: int) -> str:
    return (
        f"ℹ️ Танд {total} eSIM ирлээ. Бүгдийг нь аялахаасаа өмнө суулгаарай. Эхлээд 1-р eSIM-ээр "
        "мобайл дата ашиглаж, хугацаа эсвэл дата нь дуусахад Settings → Cellular (Mobile data) хэсгээс "
        "мобайл датаг дараагийн eSIM руу шилжүүлнэ."
    )
