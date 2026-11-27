"""Shared test helpers."""

import hashlib
import io
import os
import sqlite3
import subprocess
import sys
import tarfile
import textwrap
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from ezbak.checksums import format_sidecar, sidecar_name
from ezbak.core import EZBak
from ezbak.storage.s3 import S3Backend

_RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

# root bypasses the permission bits these tests rely on, so a file or directory stripped of a
# permission stays usable and the case under test cannot be reproduced.
skip_as_root = pytest.mark.skipif(_RUNNING_AS_ROOT, reason="root ignores permission bits")

# Dropping to another user needs root. These run only when the suite runs as root, such as
# inside the container.
only_as_root = pytest.mark.skipif(not _RUNNING_AS_ROOT, reason="dropping privileges needs root")

# A small real backup archive whose contents restore to `src/` with nested files
FIXTURE_ARCHIVE = Path(__file__).parent / "fixtures" / "archive.tgz"

# Backup names in the legacy format that appended a period label (and sometimes a uid) after
# the timestamp. Retention must keep reading them, so do not modernize this list.
LEGACY_BACKUP_NAMES = (
    "test-20250609T101857-hourly.tgz",
    "test-20250609T095745-minutely.tgz",
    "test-20250609T095804-minutely.tgz",
    "test-20250609T095730-weekly-k6lop.tgz",
    "test-20250609T095730-daily.tgz",
    "test-20250609T095751-minutely.tgz",
    "test-20250609T095749-minutely.tgz",
    "test-20250609T090932-yearly.tgz",
    "test-20250609T095737-minutely.tgz",
    "test-20250609T095804-minutely-p2we3r.tgz",
    "test-20240609T090932-yearly.tgz",
    "test-20250609T095625-monthly.tgz",
    "test-20250609T095737-minutely-6klf7.tgz",
)


def make_db(path: Path, *, rows: int = 5, wal: bool = True) -> None:
    """Create a SQLite database holding `rows` numbered records.

    Args:
        path (Path): Where to create the database. Parents are created as needed.
        rows (int): How many records to insert. Defaults to 5.
        wal (bool): Use WAL journalling rather than the default rollback mode. Defaults to True.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"row{i}",) for i in range(rows)])
        conn.commit()


def write_uncheckpointed(path: Path, value: str) -> None:
    """Write a single row into a WAL database and leave its `-wal` on disk.

    Closing a connection cleanly checkpoints the WAL and deletes it, so the writer has to be
    abandoned to reproduce what a hard-killed service leaves behind.

    Args:
        path (Path): Where to create the database. Parents are created as needed.
        value (str): The single value the database ends up holding.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    script = textwrap.dedent(f"""
        import os, sqlite3
        conn = sqlite3.connect({str(path)!r})
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("DELETE FROM t")
        conn.execute("INSERT INTO t (v) VALUES ({value!r})")
        conn.commit()
        os._exit(0)
    """)
    subprocess.run([sys.executable, "-c", script], check=True)


def values(path: Path) -> list[str]:
    """List the values in the `t` table of the database at `path`.

    Args:
        path (Path): The database to read.

    Returns:
        list[str]: The stored values, in insertion order.
    """
    with closing(sqlite3.connect(path)) as conn:
        return [row[0] for row in conn.execute("SELECT v FROM t")]


def row_count(path: Path) -> int:
    """Count the records in the `t` table of the database at `path`.

    Args:
        path (Path): The database to read.

    Returns:
        int: The number of records.
    """
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT count(*) FROM t").fetchone()[0]


def s3_backend(app: EZBak) -> S3Backend:
    """Return the app's only backend as an S3Backend, so a test can reach its AWS service.

    Args:
        app (EZBak): An app configured with an S3 bucket and no storage paths.

    Returns:
        S3Backend: The app's S3 backend.
    """
    backend = app.backends[0]
    assert isinstance(backend, S3Backend), f"Expected an S3Backend, got {type(backend).__name__}"
    return backend


def access_denied(operation: str) -> ClientError:
    """Build the ClientError S3 raises when the caller lacks permission for an operation.

    Args:
        operation (str): The S3 operation name, such as "ListObjectsV2" or "PutObject".

    Returns:
        ClientError: An AccessDenied error for that operation.
    """
    return ClientError(
        error_response={"Error": {"Code": "AccessDenied", "Message": "denied"}},
        operation_name=operation,
    )


def relative_tree(root: Path) -> set[Path]:
    """List every file and directory under a root as paths relative to it, to compare whole trees.

    Args:
        root (Path): The directory to walk.

    Returns:
        set[Path]: Each entry under `root`, relative to `root`.
    """
    return {path.relative_to(root) for path in root.rglob("*")}


# Name of the file each seeded backup carries at its archive root, holding the backup's filename
SEEDED_MARKER = "seeded-backup.txt"


def seed_backups(directory: Path, names: Iterable[str], *, with_sidecars: bool = False) -> None:
    """Write a restorable backup archive under each name, so a test can tell which one restored.

    Each archive holds the `FIXTURE_ARCHIVE` tree plus a `SEEDED_MARKER` file at its root whose
    text is the archive's own filename.

    Args:
        directory (Path): Where to write the archives.
        names (Iterable[str]): Archive paths relative to `directory`, such as
            "test-20250102T090000.tgz". Missing parent directories are created.
        with_sidecars (bool): Also write a valid checksum sidecar beside each archive.
    """
    for name in names:
        archive = directory / name
        archive.parent.mkdir(parents=True, exist_ok=True)
        marker = name.encode()
        marker_info = tarfile.TarInfo(SEEDED_MARKER)
        marker_info.size = len(marker)
        with tarfile.open(FIXTURE_ARCHIVE) as source, tarfile.open(archive, "w:gz") as tar:
            for member in source.getmembers():
                tar.addfile(member, source.extractfile(member) if member.isfile() else None)
            tar.addfile(marker_info, io.BytesIO(marker))
        if with_sidecars:
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            archive.with_name(sidecar_name(archive.name)).write_text(
                format_sidecar(digest=digest, archive_name=archive.name)
            )
