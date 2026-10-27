"""eSIM багцын жагсаалтын шүүлтүүр, эрэмбэ (/esim хуудас)."""

from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlencode

from ..catalog import Catalog, Package

GB_OPTIONS = (3, 5, 10, 20, 30)  # "3GB+" г.м.
DAY_OPTIONS = (7, 14, 30)  # "7+ хоног" г.м.
SORTS = {
    "price": ("Үнэ: хямдаас", lambda p: (p.price_mnt, p.days)),
    "price_desc": ("Үнэ: үнэтэйгээс", lambda p: (-p.price_mnt, p.days)),
    "per_day": ("Өдрийн үнэ: хямдаас", lambda p: (p.price_mnt / p.days, p.price_mnt)),
    "gb": ("Дата: ихээс", lambda p: (-(p.total_gb or 0), p.price_mnt)),
    "days": ("Хоног: уртаас", lambda p: (-p.days, p.price_mnt)),
}
SCOPES = {"single": "Нэг улс", "multi": "Олон улс"}


def _int(value: str | None, allowed: tuple[int, ...]) -> int:
    try:
        number = int(value or 0)
    except ValueError:
        return 0
    return number if number in allowed else 0


@dataclass(frozen=True)
class Filters:
    country: str = ""  # улсын код; хоосон бол бүх улс
    scope: str = ""  # single | multi
    gb: int = 0  # дор хаяж хэдэн GB
    days: int = 0  # дор хаяж хэдэн хоног
    q: str = ""
    sort: str = "price"
    view: str = "cards"  # cards | table

    @classmethod
    def parse(cls, catalog: Catalog, params: Any) -> "Filters":
        """URL-ийн параметрийг шалгаж уншина — буруу утгыг үл тоомсорлоно."""
        country = catalog.country(params.get("country", "") or "")
        scope = params.get("scope", "")
        sort = params.get("sort", "price")
        return cls(
            country=country.code if country else "",
            scope=scope if scope in SCOPES else "",
            gb=_int(params.get("gb"), GB_OPTIONS),
            days=_int(params.get("days"), DAY_OPTIONS),
            q=(params.get("q", "") or "").strip()[:60],
            sort=sort if sort in SORTS else "price",
            view="table" if params.get("view") == "table" else "cards",
        )

    @property
    def active(self) -> bool:
        """Эрэмбэ, харагдацаас бусад шүүлтүүр сонгогдсон эсэх."""
        return self != Filters(sort=self.sort, view=self.view)

    def url(self, **changes: Any) -> str:
        """Нэг утгыг сольсон шүүлтүүрийн URL (chip-ийн холбоос)."""
        values = vars(replace(self, **changes))
        defaults = vars(Filters())
        query = urlencode({k: v for k, v in values.items() if v and v != defaults[k]})
        return "/esim" + (f"?{query}" if query else "")


def search_text(catalog: Catalog, package: Package, names: dict[str, str]) -> str:
    """Нэрээр хайхад тааруулах текст: улс, бүс, өөр нэрс, sku, дата."""
    parts = [package.sku, package.data, package.region.name if package.region else ""]
    for code in package.countries:
        parts.append(names.get(code, code))
        if country := catalog.country(code):
            parts += [country.name, *country.aliases]
    return " ".join(parts).casefold()


def apply(catalog: Catalog, filters: Filters, names: dict[str, str]) -> list[Package]:
    packages = catalog.packages_covering(filters.country) if filters.country else list(catalog.packages)
    if filters.scope:
        packages = [p for p in packages if bool(p.region) == (filters.scope == "multi")]
    if filters.gb:
        packages = [p for p in packages if (p.total_gb or 0) >= filters.gb]
    if filters.days:
        packages = [p for p in packages if p.days >= filters.days]
    if filters.q:
        needle = filters.q.casefold()
        packages = [p for p in packages if needle in search_text(catalog, p, names)]
    return sorted(packages, key=SORTS[filters.sort][1])
