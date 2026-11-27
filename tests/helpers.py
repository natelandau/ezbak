"""Shared test helpers."""

import sqlite3
import subprocess
import sys
import textwrap
from contextlib import closing
from pathlib import Path

from ezbak.core import EZBak
from ezbak.storage.s3 import S3Backend


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
