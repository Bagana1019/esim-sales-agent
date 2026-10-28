"""SQLite өгөгдлийн сангийн нөөц хуулбар.

SQLite-ийн backup API-г ашигладаг тул сервер ажиллаж, захиалга бичигдэж байхад ч зөв хуулбар гарна.
Хуулбарт харилцагчийн и-мэйл, утас, eSIM-ийн идэвхжүүлэх код байгаа тул нууцаар хадгална.
"""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import make_url

PREFIX = "esim-"


@dataclass
class BackupInfo:
    path: Path
    size_bytes: int
    orders: int
    unused_esims: int


def sqlite_path(database_url: str) -> Path:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError(f"Нөөц хуулбар зөвхөн SQLite файлд ажиллана: {url.render_as_string(hide_password=True)}")
    return Path(url.database)


def backup_database(database_url: str, backup_dir: str | Path, keep: int = 30) -> BackupInfo:
    """Сангийн хуулбарыг backup_dir/esim-ОГНОО-ЦАГ.db болгож шалгана. Хамгийн сүүлийн keep хуулбарыг үлдээнэ."""
    if keep < 1:
        raise ValueError("BACKUP_KEEP 1-ээс багагүй байна")
    source = sqlite_path(database_url)
    if not source.is_file():
        raise FileNotFoundError(f"Өгөгдлийн сан олдсонгүй: {source.resolve()}")
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{PREFIX}{datetime.now().astimezone():%Y%m%d-%H%M%S}.db"  # локал цагаар
    partial = target.with_suffix(".part")

    try:
        with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(partial)) as dst:
            src.backup(dst)
            if (check := dst.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
                raise RuntimeError(f"Хуулбар эвдэрсэн байна: {check}")
            orders = dst.execute("SELECT count(*) FROM orders").fetchone()[0]
            unused = dst.execute("SELECT count(*) FROM esim_profiles WHERE order_id IS NULL").fetchone()[0]
    except BaseException:
        partial.unlink(missing_ok=True)  # дутуу хуулбар үлдээхгүй
        raise
    partial.replace(target)  # бүрэн бичигдэж шалгагдсан хуулбар л esim-*.db нэртэй болно

    for old in sorted(backup_dir.glob(f"{PREFIX}*.db"))[:-keep]:
        old.unlink()
    return BackupInfo(target, target.stat().st_size, orders, unused)
