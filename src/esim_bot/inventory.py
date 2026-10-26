import csv
from pathlib import Path

from sqlalchemy import func, select

from .db import Database, EsimProfile

REQUIRED_COLUMNS = {"package_sku", "iccid", "smdp_address", "activation_code"}


async def import_esims(db: Database, csv_path: str | Path) -> int:
    """CSV-ээс eSIM санд нэмнэ. Аль хэдийн байгаа ICCID-ийг алгасна. Нэмэгдсэн тоог буцаана."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV-д дутуу багана: {', '.join(sorted(missing))}")
        rows = [{k: row[k].strip() for k in REQUIRED_COLUMNS} for row in reader]

    async with db.session() as s, s.begin():
        existing = set(await s.scalars(select(EsimProfile.iccid)))
        new = [EsimProfile(**r) for r in rows if r["iccid"] not in existing]
        s.add_all(new)
    return len(new)


async def stock_by_sku(db: Database) -> dict[str, int]:
    async with db.session() as s:
        rows = await s.execute(
            select(EsimProfile.package_sku, func.count())
            .where(EsimProfile.order_id.is_(None))
            .group_by(EsimProfile.package_sku)
        )
        return {sku: count for sku, count in rows}
