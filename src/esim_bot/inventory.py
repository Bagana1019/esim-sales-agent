import csv
from pathlib import Path

from sqlalchemy import func, select

from .db import Database, EsimProfile, LicenseKey

REQUIRED_COLUMNS = {"package_sku", "iccid", "smdp_address", "activation_code"}
LICENSE_COLUMNS = {"package_sku", "product_key"}


def _read_csv(csv_path: str | Path, columns: set[str]) -> list[dict[str, str]]:
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = columns - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV-д дутуу багана: {', '.join(sorted(missing))}")
        return [{k: row[k].strip() for k in columns} for row in reader]


async def import_esims(db: Database, csv_path: str | Path) -> int:
    """CSV-ээс eSIM санд нэмнэ. Аль хэдийн байгаа ICCID-ийг алгасна. Нэмэгдсэн тоог буцаана."""
    rows = _read_csv(csv_path, REQUIRED_COLUMNS)

    async with db.session() as s, s.begin():
        existing = set(await s.scalars(select(EsimProfile.iccid)))
        new = [EsimProfile(**r) for r in rows if r["iccid"] not in existing]
        s.add_all(new)
    return len(new)


async def import_licenses(db: Database, csv_path: str | Path) -> int:
    """CSV-ээс (package_sku, product_key) лицензийн санд нэмнэ. Давхардсан түлхүүрийг алгасна."""
    rows = _read_csv(csv_path, LICENSE_COLUMNS)
    async with db.session() as s, s.begin():
        existing = set(await s.scalars(select(LicenseKey.product_key)))
        new = [LicenseKey(**r) for r in rows if r["product_key"] not in existing]
        s.add_all(new)
    return len(new)


async def stock_by_sku(db: Database, *models: type[EsimProfile | LicenseKey]) -> dict[str, int]:
    """Ашиглагдаагүй eSIM болон лицензийн тоо, sku тус бүрээр (models-оор шүүж болно)."""
    stock: dict[str, int] = {}
    async with db.session() as s:
        for model in models or (EsimProfile, LicenseKey):
            rows = await s.execute(
                select(model.package_sku, func.count()).where(model.order_id.is_(None)).group_by(model.package_sku)
            )
            stock.update({sku: count for sku, count in rows})
    return stock
