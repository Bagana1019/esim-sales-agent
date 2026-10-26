from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    flag: str
    aliases: tuple[str, ...] = ()  # харилцагчийн бичиж болох өөр нэрс (латинаар г.м.)

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
        self._countries = {
            key.casefold(): c for c in countries for key in (c.code, c.name, *c.aliases)
        }
        self._packages = {p.sku: p for p in packages}

    @classmethod
    def load(cls, path: str | Path) -> "Catalog":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(
            countries=[Country(**{**c, "aliases": tuple(c.get("aliases", ()))}) for c in raw["countries"]],
            packages=[Package(**p) for p in raw["packages"]],
        )

    def country(self, query: str) -> Country | None:
        """Улсын код (CN), нэр (Хятад) эсвэл өөр бичлэгээр (Hytad) хайна."""
        return self._countries.get(query.strip().casefold())

    def package(self, sku: str) -> Package | None:
        return self._packages.get(sku)

    def packages_for(self, country: str) -> list[Package]:
        found = self.country(country)
        return [p for p in self.packages if found and p.country == found.code]
