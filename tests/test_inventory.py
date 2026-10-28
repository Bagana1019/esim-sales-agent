import pytest

from esim_bot.inventory import import_esims, import_licenses, stock_by_sku

HEADER = "package_sku,iccid,smdp_address,activation_code\n"


async def test_bad_csv_is_rejected_whole(db, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text(
        HEADER
        + "JP-5D-3GB,1,smdp.test,A\n"
        + "JP-5D-3G,2,smdp.test,B\n"  # sku-д үсгийн алдаа
        + "JP-5D-3GB,,smdp.test,C\n"  # ICCID хоосон
        + ",,,\n"  # Excel-ийн хоосон мөр — алгасна
        + "JP-5D-3GB,1,smdp.test,D\n",  # ICCID давхардсан
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as e:
        await import_esims(db, bad, {"JP-5D-3GB"})
    assert str(e.value).splitlines() == [
        "CSV алдаатай тул юу ч оруулсангүй:",
        "3-р мөр: JP-5D-3G гэсэн sku каталогт (data/catalog.yaml) байхгүй",
        "4-р мөр: iccid хоосон",
        "6-р мөр: iccid 1 файлд давхардсан",
    ]
    assert await stock_by_sku(db) == {}  # хагас импорт хийхгүй


async def test_good_csv_imports_once(db, tmp_path):
    good = tmp_path / "good.csv"
    good.write_text(HEADER + "JP-5D-3GB,1,smdp.test,A\n,,,\nJP-7D-5GB,2,smdp.test,B\n", encoding="utf-8")
    assert await import_esims(db, good, {"JP-5D-3GB", "JP-7D-5GB"}) == 2
    assert await import_esims(db, good, {"JP-5D-3GB", "JP-7D-5GB"}) == 0  # дахин оруулахад давхардуулахгүй
    assert await stock_by_sku(db) == {"JP-5D-3GB": 1, "JP-7D-5GB": 1}

    keys = tmp_path / "keys.csv"
    keys.write_text("package_sku,product_key\nWIN11-PRO,K1\nWIN11-PROO,K2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="WIN11-PROO"):
        await import_licenses(db, keys, {"WIN11-PRO"})
