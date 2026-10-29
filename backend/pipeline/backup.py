"""Daily database backup (Phase 8): pg_dump of the whole database into data/backups/.

The file name matches `make backup` (jap-YYYYmmdd-HHMMSS.sql.gz), so `make restore` takes
either. A dump is written to a .part file and renamed when pg_dump succeeds, so a half
written file is never mistaken for a backup. Files older than KEEP_DAYS are removed.
"""

import asyncio
import gzip
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.engine import make_url

from config import get_settings

KEEP_DAYS = 14
PREFIX = "jap-"


class BackupFailed(Exception):
    pass


@dataclass
class Backup:
    path: Path
    at: datetime
    size: int

    def as_dict(self) -> dict[str, object]:
        return {"file": self.path.name, "at": self.at.isoformat(), "bytes": self.size}


def backup_dir() -> Path:
    return Path(get_settings().jap_data_dir) / "backups"


def _when(path: Path) -> datetime | None:
    """The time in a backup's name (local time in JAP_TZ), or None for another file."""
    stem = path.name.removeprefix(PREFIX).removesuffix(".sql.gz")
    try:
        local = datetime.strptime(stem, "%Y%m%d-%H%M%S")
    except ValueError:
        return None
    return local.replace(tzinfo=ZoneInfo(get_settings().jap_tz))


def backups() -> list[Backup]:
    """Every finished backup, newest first."""
    folder = backup_dir()
    out = []
    for p in folder.glob(f"{PREFIX}*.sql.gz") if folder.exists() else []:
        if (at := _when(p)) is not None:
            out.append(Backup(path=p, at=at, size=p.stat().st_size))
    return sorted(out, key=lambda b: b.at, reverse=True)


def has_today(now: datetime | None = None) -> bool:
    tz = ZoneInfo(get_settings().jap_tz)
    today = (now or datetime.now(UTC)).astimezone(tz).date()
    return any(b.at.astimezone(tz).date() == today and b.size > 0 for b in backups())


def _libpq() -> tuple[str, dict[str, str]]:
    """The database URL for pg_dump, with the password passed in the environment."""
    url = make_url(get_settings().database_url)
    env = {**os.environ}
    if url.password:
        env["PGPASSWORD"] = str(url.password)
    plain = url.set(drivername="postgresql", password=None).render_as_string(hide_password=False)
    return plain, env


async def run_backup(now: datetime | None = None) -> Backup:
    """Dump the database now and prune old files."""
    tz = ZoneInfo(get_settings().jap_tz)
    at = (now or datetime.now(UTC)).astimezone(tz)
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / f"{PREFIX}{at:%Y%m%d-%H%M%S}.sql.gz"
    part = final.with_name(final.name + ".part")
    url, env = _libpq()
    try:
        proc = await asyncio.create_subprocess_exec(
            "pg_dump", "--clean", "--if-exists", "--no-owner", f"--dbname={url}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )  # fmt: skip
    except FileNotFoundError as exc:
        raise BackupFailed("pg_dump is not installed in the app container") from exc
    assert proc.stdout is not None and proc.stderr is not None
    with gzip.open(part, "wb") as out:
        while chunk := await proc.stdout.read(1 << 16):
            out.write(chunk)
    err = (await proc.stderr.read()).decode(errors="replace").strip()
    if await proc.wait() != 0:
        part.unlink(missing_ok=True)
        raise BackupFailed(f"pg_dump failed: {err.splitlines()[-1] if err else 'no output'}")
    part.rename(final)
    prune(at)
    return Backup(path=final, at=at, size=final.stat().st_size)


def prune(now: datetime) -> list[str]:
    """Remove backups older than KEEP_DAYS (and .part leftovers); the newest always stays."""
    removed = []
    keep_from = now - timedelta(days=KEEP_DAYS)
    for i, b in enumerate(backups()):
        if i > 0 and b.at < keep_from:
            b.path.unlink(missing_ok=True)
            removed.append(b.path.name)
    for leftover in backup_dir().glob("*.part"):
        if datetime.fromtimestamp(leftover.stat().st_mtime, UTC) < now - timedelta(hours=1):
            leftover.unlink(missing_ok=True)
    return removed
