import csv
from collections.abc import Callable, Collection
from pathlib import Path

from sqlalchemy import func, select

from .catalog import Catalog
from .db import Database, EsimProfile, LicenseKey

REQUIRED_COLUMNS = {"package_sku", "iccid", "smdp_address", "activation_code"}
LICENSE_COLUMNS = {"package_sku", "product_key"}


def _read_csv(
    csv_path: str | Path, columns: set[str], unique: str, known_skus: Collection[str] | None
) -> list[dict[str, str]]:
    """CSV-г уншиж шалгана. Алдаатай мөр байвал юу ч оруулахгүйгээр бүх алдааг нэг дор ValueError болгоно."""
    rows: list[dict[str, str]] = []
    errors: list[str] = []
    seen: set[str] = set()
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = columns - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV-д дутуу багана: {', '.join(sorted(missing))}")
        for row in reader:
            values = {k: (row[k] or "").strip() for k in columns}
            if not any(values.values()):
                continue  # Excel-ийн хоосон мөр
            line = f"{reader.line_num}-р мөр"
            if empty := sorted(k for k, v in values.items() if not v):
                errors.append(f"{line}: {', '.join(empty)} хоосон")
            elif known_skus is not None and values["package_sku"] not in known_skus:
                errors.append(f"{line}: {values['package_sku']} гэсэн sku каталогт (data/catalog.yaml) байхгүй")
            elif values[unique] in seen:
                errors.append(f"{line}: {unique} {values[unique]} файлд давхардсан")
            else:
                seen.add(values[unique])
                rows.append(values)
    if errors:
        more = [f"... бас {len(errors) - 10} алдаа"] if len(errors) > 10 else []
        raise ValueError("\n".join(["CSV алдаатай тул юу ч оруулсангүй:", *errors[:10], *more]))
    return rows


async def import_esims(db: Database, csv_path: str | Path, known_skus: Collection[str] | None = None) -> int:
    """CSV-ээс eSIM санд нэмнэ. Аль хэдийн байгаа ICCID-ийг алгасна. Нэмэгдсэн тоог буцаана.

    known_skus өгвөл каталогт байхгүй sku-тай мөрийг алдаа гэж үзнэ (үсгийн алдаатай sku-тай нөөц
    хэзээ ч зарагдахгүй үлдэхээс сэргийлнэ).
    """
    rows = _read_csv(csv_path, REQUIRED_COLUMNS, "iccid", known_skus)

    async with db.session() as s, s.begin():
        existing = set(await s.scalars(select(EsimProfile.iccid)))
        new = [EsimProfile(**r) for r in rows if r["iccid"] not in existing]
        s.add_all(new)
    return len(new)


async def import_licenses(db: Database, csv_path: str | Path, known_skus: Collection[str] | None = None) -> int:
    """CSV-ээс (package_sku, product_key) лицензийн санд нэмнэ. Давхардсан түлхүүрийг алгасна."""
    rows = _read_csv(csv_path, LICENSE_COLUMNS, "product_key", known_skus)
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


def _fresh(make: Callable[[int], str], taken: set[str], count: int = 2) -> list[str]:
    """make(1), make(2)...-оос taken-д ороогүй count утгыг сонгож, taken-д нэмнэ."""
    values: list[str] = []
    n = 1
    while len(values) < count:
        if (value := make(n)) not in taken:
            values.append(value)
            taken.add(value)
        n += 1
    return values


async def seed_demo_stock(db: Database, catalog: Catalog) -> None:
    """Хуурамч төлбөрийн горимд нөөцгүй багц, лиценз бүрд туршилтын 2 ширхэг нэмнэ.

    Сан хоосон бол эхлээд жишээ CSV-г оруулна. Каталогт шинэ бүтээгдэхүүн нэмэгдсэн эсвэл туршилтаар
    зарагдаж дууссан хуучин сантай ч локал дээр бүх бүтээгдэхүүнийг худалдаж үзэх боломжтой.
    """
    added = 0
    if not await stock_by_sku(db, EsimProfile):
        added += await import_esims(db, "data/esims.example.csv", {p.sku for p in catalog.packages})
    if not await stock_by_sku(db, LicenseKey):
        added += await import_licenses(db, "data/licenses.example.csv", {lic.sku for lic in catalog.licenses})

    stocked = await stock_by_sku(db)
    async with db.session() as s, s.begin():
        iccids = set(await s.scalars(select(EsimProfile.iccid)))
        keys = set(await s.scalars(select(LicenseKey.product_key)))
        demo: list[EsimProfile | LicenseKey] = [
            EsimProfile(
                package_sku=p.sku,
                iccid=iccid,
                smdp_address="rsp.example-provider.com",
                activation_code=f"TEST-{iccid}",
            )
            for i, p in enumerate(catalog.packages)
            if p.sku not in stocked
            for iccid in _fresh(lambda n, i=i: f"8900{i:05d}{n}", iccids)
        ]
        demo += [
            LicenseKey(package_sku=lic.sku, product_key=key)
            for lic in catalog.licenses
            if lic.sku not in stocked
            for key in _fresh(lambda n, lic=lic: f"TEST-{lic.sku}-DEMO{n}", keys)
        ]
        s.add_all(demo)
    if added + len(demo):
        print(f"(Туршилтын {added + len(demo)} eSIM, лиценз санд нэмэгдлээ)")
