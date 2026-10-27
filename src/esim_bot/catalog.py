from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    flag: str
    aliases: tuple[str, ...] = ()  # харилцагчийн бичиж болох өөр нэрс (латинаар г.м.)
    notes: str = ""  # бот харилцагчид баттай хэлэх баримт (жишээ нь ямар апп ажиллах)

    @property
    def label(self) -> str:
        return f"{self.flag} {self.name}"


@dataclass(frozen=True)
class Region:
    """Олон улсад нэг дор ажилладаг багцын бүс (жишээ нь Ази 15 улс)."""

    code: str
    name: str
    countries: tuple[str, ...]


@dataclass(frozen=True)
class Package:
    sku: str
    country: str  # бот, вэбд энэ улсын дор харагдана
    days: int
    data: str
    price_mnt: int
    gb: float | None = None  # дата хэмжээ (өдрийн багцад өдөрт ногдох) — вэбийн шүүлтүүрт
    daily: bool = False  # өдөр бүр шинэчлэгддэг дата
    region: Region | None = None  # олон улсын багц бол хамрах бүс

    @property
    def countries(self) -> tuple[str, ...]:
        """Багц ажиллах улсуудын код."""
        return self.region.countries if self.region else (self.country,)

    @property
    def total_gb(self) -> float | None:
        if self.gb is None:
            return None
        return self.gb * self.days if self.daily else self.gb

    @property
    def title(self) -> str:
        prefix = f"{self.region.name} · " if self.region else ""
        return f"{prefix}{self.days} хоног / {self.data} - {format_mnt(self.price_mnt)}"


@dataclass(frozen=True)
class License:
    """Програм хангамжийн лиценз (бүтээгдэхүүний түлхүүрээр хүргэнэ)."""

    sku: str
    name: str
    price_mnt: int
    description: str = ""  # хэнд тохирох, юу багтах — бот харилцагчид тайлбарлана
    activation: str = ""  # идэвхжүүлэх заавар — түлхүүрийн хамт илгээнэ
    category: str = "windows"  # LICENSE_CATEGORIES-ийн түлхүүр
    devices: str = ""  # хэдэн төхөөрөмж/хэрэглэгчид (жишээ нь "1 компьютер")
    term: str = ""  # хугацаа (жишээ нь "Насан туршийн", "12 сар")
    features: tuple[str, ...] = ()  # танилцуулга хуудсанд жагсаана

    @property
    def title(self) -> str:
        return f"{self.name} - {format_mnt(self.price_mnt)}"

    @property
    def emoji(self) -> str:
        return LICENSE_CATEGORIES.get(self.category, LICENSE_CATEGORIES["windows"])[1]


# Лицензийн ангилал: түлхүүр -> (нэр, emoji)
LICENSE_CATEGORIES = {"windows": ("Windows", "🪟"), "server": ("Windows Server", "🖥"), "office": ("Office, Microsoft 365", "📝")}


Product = Package | License


def format_mnt(amount: int) -> str:
    return f"{amount:,}₮"


class Catalog:
    def __init__(self, countries: list[Country], packages: list[Package], licenses: list[License] | None = None):
        self.countries = countries
        self.packages = packages
        self.licenses = licenses or []
        self._licenses = {lic.sku: lic for lic in self.licenses}
        self._countries = {
            key.casefold(): c for c in countries for key in (c.code, c.name, *c.aliases)
        }
        self._packages = {p.sku: p for p in packages}

    @classmethod
    def load(cls, path: str | Path) -> "Catalog":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        regions = {r["code"]: Region(r["code"], r["name"], tuple(r["countries"])) for r in raw.get("regions", [])}
        for region in regions.values():
            if bad := [c for c in region.countries if not isinstance(c, str)]:
                # YAML нь NO (Норвеги) гэх мэт кодыг true/false болгож уншдаг
                raise ValueError(f"{region.code} бүсийн улсын код текст биш: {bad} — кодыг хашилтад бичнэ үү (\"NO\")")
        return cls(
            countries=[Country(**{**c, "aliases": tuple(c.get("aliases", ()))}) for c in raw["countries"]],
            packages=[Package(**{**p, "region": regions[p["region"]] if "region" in p else None}) for p in raw["packages"]],
            licenses=[License(**{**lic, "features": tuple(lic.get("features", ()))}) for lic in raw.get("licenses", [])],
        )

    def country(self, query: str) -> Country | None:
        """Улсын код (CN), нэр (Хятад) эсвэл өөр бичлэгээр (Hytad) хайна."""
        return self._countries.get(query.strip().casefold())

    def package(self, sku: str) -> Package | None:
        return self._packages.get(sku)

    def license(self, sku: str) -> License | None:
        return self._licenses.get(sku)

    def product(self, sku: str) -> Product | None:
        """eSIM багц эсвэл лиценз — sku-аар."""
        return self._packages.get(sku) or self._licenses.get(sku)

    def packages_for(self, country: str) -> list[Package]:
        found = self.country(country)
        return [p for p in self.packages if found and p.country == found.code]

    def packages_covering(self, country_code: str) -> list[Package]:
        """Тухайн улсад ажилладаг бүх багц — олон улсын багцыг оролцуулан."""
        return [p for p in self.packages if country_code in p.countries]
