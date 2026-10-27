from pathlib import Path

from esim_bot.catalog import Catalog
from esim_bot.delivery import product_label

ROOT = Path(__file__).parent.parent

REGIONAL = """
countries:
  - {code: CN, name: Хятад, flag: "🇨🇳"}
  - {code: TH, name: Тайланд, flag: "🇹🇭"}
regions:
  - {code: AS2, name: Ази 2 улс, countries: [CN, TH]}
packages:
  - {sku: CN-3D-1GB, country: CN, days: 3, data: "Нийт 1GB", gb: 1, price_mnt: 15000}
  - {sku: KR-5D-1GBD, country: CN, days: 5, data: "Өдөрт 1GB", gb: 1, daily: true, price_mnt: 25000}
  - {sku: AS2-7D-5GB, country: TH, region: AS2, days: 7, data: "Нийт 5GB", gb: 5, price_mnt: 20000}
"""


def test_real_catalog_is_consistent():
    catalog = Catalog.load(ROOT / "data" / "catalog.yaml")
    skus = [p.sku for p in catalog.packages] + [lic.sku for lic in catalog.licenses]
    assert len(skus) == len(set(skus))
    codes = {c.code for c in catalog.countries}
    for p in catalog.packages:
        assert p.country in codes and p.country in p.countries, p.sku  # бүсийн багц өөрийн улсаа хамарна
        assert p.gb and p.days > 0 and p.price_mnt > 0, p.sku
    assert all(catalog.packages_covering(code) for code in codes)


def test_regional_package_is_listed_under_every_country_it_covers(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text(REGIONAL, encoding="utf-8")
    catalog = Catalog.load(path)
    regional = catalog.package("AS2-7D-5GB")

    assert regional.countries == ("CN", "TH")
    assert regional in catalog.packages_covering("CN") and regional not in catalog.packages_for("CN")
    assert catalog.packages_for("TH") == [regional]  # бот Тайландын дор харуулна
    assert regional.title == "Ази 2 улс · 7 хоног / Нийт 5GB - 20,000₮"
    assert product_label(catalog, regional.sku) == "🌏 Ази 2 улс - 7 хоног / Нийт 5GB"
    assert catalog.package("KR-5D-1GBD").total_gb == 5  # өдөрт 1GB × 5 хоног
