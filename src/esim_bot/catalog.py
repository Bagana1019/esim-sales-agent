from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    flag: str

    @property
    def label(self) -> str:
        return f"{self.flag} {self.name}"


@dataclass(frozen=True)
class Package:
    sku: str
    country: str
    days: int
    data: str
    price_mnt: int

    @property
    def title(self) -> str:
        return f"{self.days} хоног / {self.data} - {format_mnt(self.price_mnt)}"


def format_mnt(amount: int) -> str:
    return f"{amount:,}₮"


class Catalog:
    def __init__(self, countries: list[Country], packages: list[Package]):
        self.countries = countries
        self.packages = packages
        self._countries = {c.code: c for c in countries}
        self._packages = {p.sku: p for p in packages}

    @classmethod
    def load(cls, path: str | Path) -> "Catalog":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(
            countries=[Country(**c) for c in raw["countries"]],
            packages=[Package(**p) for p in raw["packages"]],
        )

    def country(self, code: str) -> Country | None:
        return self._countries.get(code.upper())

    def package(self, sku: str) -> Package | None:
        return self._packages.get(sku)

    def packages_for(self, country_code: str) -> list[Package]:
        code = country_code.upper()
        return [p for p in self.packages if p.country == code]
