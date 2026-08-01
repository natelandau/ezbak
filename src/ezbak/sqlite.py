"""Take consistent point-in-time copies of live SQLite databases.

Copying a SQLite file while a service holds it open can capture a torn page, producing an
archive that will not open. Use these helpers to copy a database through SQLite's online-backup
API instead, which is safe against a concurrent writer and folds in any WAL or rollback journal.
"""

from __future__ import annotations

import sqlite3
import stat
import time
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

from loguru import logger

from ezbak.exceptions import SqliteSnapshotError

if TYPE_CHECKING:
    from pathlib import Path

# Longest a snapshot waits on another process's write lock. `Connection.backup()` retries
# SQLITE_BUSY forever on its own, so without a bound one long write transaction wedges a
# scheduled backup indefinitely; failing the run surfaces the problem instead.
SQLITE_BACKUP_LOCK_TIMEOUT_SECONDS: float = 300.0


def _is_readonly_error(error: sqlite3.Error) -> bool:
    """Report whether a SQLite error was caused by a read-only file or filesystem.

    Returns:
        bool: True if the error message indicates a read-only file or filesystem.
    """
    return "readonly" in str(error).lower() or "read-only" in str(error).lower()


def _copy(source: Path, *, dest: Path, read_only: bool) -> None:
    """Copy `source` to `dest` through the online-backup API.

    Raises `TimeoutError` (an `OSError`, so callers already handle it) when another process
    holds a write lock past `SQLITE_BACKUP_LOCK_TIMEOUT_SECONDS`.

    Args:
        source (Path): The live database to copy.
        dest (Path): The destination file, overwritten if present.
        read_only (bool): Open the source through a `mode=ro` URI rather than read-write.
    """
    # The backup API must create and write the -shm file for a WAL database, so the source is
    # opened read-write by default. A read-only mount cannot support that, but it also
    # guarantees no writer, which makes the read-only URI safe there. The path is percent-encoded
    # because SQLite parses a URI filename, so a bare '?', '#', or '%' in it would be structure.
    dsn = f"file:{quote(str(source))}?mode=ro" if read_only else str(source)
    deadline = time.monotonic() + SQLITE_BACKUP_LOCK_TIMEOUT_SECONDS

    def _abort_when_stuck(*_: int) -> None:
        # backup() invokes this after every step, including the ones it retries on
        # SQLITE_BUSY, which is the only way out of its otherwise unbounded retry loop.
        if time.monotonic() >= deadline:
            msg = (
                f"Timed out after {SQLITE_BACKUP_LOCK_TIMEOUT_SECONDS:.0f}s waiting to copy "
                f"'{source}': another process is holding a write lock"
            )
            raise TimeoutError(msg)

    with (
        closing(sqlite3.connect(dsn, uri=read_only)) as src_conn,
        closing(sqlite3.connect(dest)) as dest_conn,
    ):
        src_conn.backup(dest_conn, progress=_abort_when_stuck)


# Journal files SQLite keeps beside a database. They belong to the live file, not to a
# snapshot, so they are excluded from the archive. Master journals (-mj*) only appear for
# multi-database transactions, which ezbak does not support.
SQLITE_SIDECAR_SUFFIXES: tuple[str, ...] = ("-wal", "-shm", "-journal")

# Header bytes SQLite writes at the front of each journal form. A WAL carries one of two
# magic numbers, chosen by the checksum byte order it was written with.
_WAL_MAGIC: tuple[bytes, ...] = (b"\x37\x7f\x06\x82", b"\x37\x7f\x06\x83")
_ROLLBACK_JOURNAL_MAGIC: tuple[bytes, ...] = (b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7",)
_DATABASE_MAGIC = b"SQLite format 3\x00"


def is_sqlite_database(path: Path) -> bool:
    """Confirm by content that a file is a SQLite database.

    Use this to gate anything that acts on a file's journal siblings, so a file that merely
    shares the naming shape of a database is left alone.

    Args:
        path (Path): The file to inspect.

    Returns:
        bool: True if the file carries SQLite's database header.
    """
    try:
        with path.open("rb") as f:
            return f.read(len(_DATABASE_MAGIC)) == _DATABASE_MAGIC
    except OSError as e:
        logger.debug(f"Could not read '{path}' to identify it as a sqlite database: {e}")
        return False


def is_sqlite_sidecar(path: Path, *, suffix: str) -> bool:
    """Confirm by content that a file named like a SQLite sidecar really is one.

    Use this before deleting a journal beside a database: the caller never named the file,
    so an unrelated `notes-wal` must survive. A `-shm` is a rebuildable shared-memory cache
    with no stable header, so its name is all there is to judge it by.

    Args:
        path (Path): The candidate sidecar to inspect.
        suffix (str): The sidecar suffix `path` was matched on.

    Returns:
        bool: True if the file's header identifies it as that kind of sidecar.
    """
    if suffix == "-shm":
        return True

    magics = _WAL_MAGIC if suffix == "-wal" else _ROLLBACK_JOURNAL_MAGIC
    try:
        with path.open("rb") as f:
            header = f.read(max(len(magic) for magic in magics))
    except OSError as e:
        logger.debug(f"Could not read '{path}' to identify it as a sqlite sidecar: {e}")
        return False

    return any(header.startswith(magic) for magic in magics)


def sidecar_paths(database: Path) -> list[Path]:
    """List the journal files SQLite may keep beside a database.

    Use this to exclude a live database's journals from an archive, where they would be
    inconsistent with the snapshot that replaces the database itself.

    Args:
        database (Path): The database whose siblings to name.

    Returns:
        list[Path]: The sidecar paths, which may or may not exist on disk.
    """
    return [database.with_name(database.name + suffix) for suffix in SQLITE_SIDECAR_SUFFIXES]


def containing_source_paths(path: Path, *, source_paths: list[Path]) -> list[Path]:
    """List every configured source path that contains `path`.

    Returning all matches rather than the first lets callers reject an ambiguous configuration
    (overlapping sources) instead of silently picking one and producing a surprising layout.

    Args:
        path (Path): The path to locate.
        source_paths (list[Path]): The configured source paths to search.

    Returns:
        list[Path]: The matching sources, in the order configured.
    """
    return [source for source in source_paths if path.is_relative_to(source)]


def archive_name_for(path: Path, *, source_paths: list[Path], strip: bool) -> str:
    """Compute the archive name a file would receive when added from its source path.

    Mirror the naming that `EZBak._archive_source` applies so a snapshot substituted for a live
    database lands at exactly the position the live file would have occupied, leaving the archive
    layout, and therefore every existing restore, unchanged.

    Args:
        path (Path): The file to place.
        source_paths (list[Path]): The configured source paths.
        strip (bool): Whether archive names omit the source directory's own name.

    Returns:
        str: The archive name.

    Raises:
        SqliteSnapshotError: If `path` is not inside exactly one configured source path.
    """
    matches = containing_source_paths(path, source_paths=source_paths)
    if not matches:
        msg = f"'{path}' is not inside any configured source path"
        raise SqliteSnapshotError(msg)
    if len(matches) > 1:
        listed = ", ".join(f"'{m}'" for m in matches)
        msg = f"'{path}' is inside more than one configured source path ({listed})"
        raise SqliteSnapshotError(msg)

    source = matches[0]
    # A source that is the database itself is archived under its bare filename, with no
    # directory prefix, whether or not stripping is on.
    if source == path:
        return source.name

    relative = path.relative_to(source).as_posix()
    # A source at the filesystem root has no name to prefix with, and the walk archives its
    # children under their bare names, so an empty name must not produce an absolute arcname.
    return relative if strip or not source.name else f"{source.name}/{relative}"


def _verify(snapshot: Path, *, source: Path) -> None:
    """Confirm a snapshot opens and its pages are coherent.

    `quick_check` skips the index cross-checks of a full `integrity_check`, so it costs roughly
    one sequential read of a file that was just written and is still in page cache.

    Args:
        snapshot (Path): The freshly written snapshot to check.
        source (Path): The live database it came from, named in any error.

    Raises:
        SqliteSnapshotError: If the snapshot cannot be opened or fails the check.
    """
    try:
        with closing(sqlite3.connect(snapshot)) as conn:
            result = conn.execute("PRAGMA quick_check").fetchone()
    except (sqlite3.Error, OSError) as e:
        msg = f"Snapshot of '{source}' could not be reopened for verification: {e}"
        raise SqliteSnapshotError(msg) from e

    if not result or result[0] != "ok":
        detail = result[0] if result else "no result"
        msg = f"Snapshot of '{source}' failed its integrity check: {detail}"
        raise SqliteSnapshotError(msg)


def snapshot_database(source: Path, *, dest: Path) -> None:
    """Write a consistent, verified copy of a live SQLite database.

    Use this instead of copying the file when a service may hold the database open. The copy is
    taken through SQLite's online-backup API, so it is safe against a concurrent writer, and any
    WAL or hot rollback journal is folded into the result.

    Args:
        source (Path): The live database to snapshot.
        dest (Path): Where to write the snapshot. Parent directories are created as needed.

    Raises:
        SqliteSnapshotError: If the database cannot be copied or the copy fails verification.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        _copy(source, dest=dest, read_only=False)
    except sqlite3.OperationalError as e:
        if not _is_readonly_error(e):
            msg = f"Failed to snapshot sqlite database '{source}': {e}"
            raise SqliteSnapshotError(msg) from e

        logger.debug(f"'{source}' is not writable; retrying snapshot read-only")
        dest.unlink(missing_ok=True)
        try:
            _copy(source, dest=dest, read_only=True)
        except (sqlite3.Error, OSError) as retry_error:
            msg = f"Failed to snapshot read-only sqlite database '{source}': {retry_error}"
            raise SqliteSnapshotError(msg) from retry_error
    except (sqlite3.Error, OSError) as e:
        msg = f"Failed to snapshot sqlite database '{source}': {e}"
        raise SqliteSnapshotError(msg) from e

    _verify(dest, source=source)

    # The archive records the snapshot's metadata, not the live file's, and SQLite creates the
    # snapshot at 0666 less the umask. Carry the database's own permissions across so a 0600
    # database does not restore world-readable. Done after verification, which needs to open
    # the snapshot for writing.
    try:
        dest.chmod(stat.S_IMODE(source.stat().st_mode))
    except OSError as e:
        logger.warning(f"Could not copy permissions of '{source}' onto its snapshot: {e}")

    logger.debug(f"Snapshotted sqlite database '{source}'")


def _reached_through_symlink(path: Path, *, source_paths: list[Path]) -> bool:
    """Report whether a database is a symlink or sits behind one inside its source path.

    The source walk refuses to follow symlinks, so a database reached through one would be
    archived at a position the live tree never held, holding content from outside every source.
    A symlinked source path is the operator's own choice and is walked as usual, so only the
    directories below a source count.

    Args:
        path (Path): The configured database path.
        source_paths (list[Path]): The configured backup source paths.

    Returns:
        bool: True if the path itself or an intervening directory is a symlink.
    """
    if path.is_symlink():
        return True

    return any(
        parent.is_symlink()
        for source in containing_source_paths(path, source_paths=source_paths)
        for parent in path.parents
        if parent != source and parent.is_relative_to(source)
    )


@dataclass(frozen=True)
class SqliteSnapshot:
    """A consistent copy of one live SQLite database and where it belongs in an archive."""

    source: Path
    snapshot: Path
    arcname: str


def snapshot_databases(
    paths: list[Path],
    *,
    source_paths: list[Path],
    strip: bool,
    dest_dir: Path,
) -> list[SqliteSnapshot]:
    """Snapshot every configured SQLite database and report where each belongs in the archive.

    Use this before archiving a live service directory so the archive holds consistent copies of
    its databases rather than files that may have been read mid-write.

    A path that does not exist is warned about and skipped: a service may not have created an
    optional database yet, and failing there would break the first backup after a fresh deploy. A
    path that is a symlink, or that is reached through one below its source path, is warned about
    and skipped too, matching how the source walk treats symlinks. A path that exists but cannot
    be snapshotted aborts, because the resulting archive would not be the archive the operator
    asked for.

    Args:
        paths (list[Path]): The databases to snapshot.
        source_paths (list[Path]): The configured backup source paths, used to place each
            snapshot in the archive.
        strip (bool): Whether archive names omit the source directory's own name.
        dest_dir (Path): Directory to stage snapshots in. Created only if there is work to do.

    Returns:
        list[SqliteSnapshot]: One entry per database that was snapshotted.
    """
    snapshots: list[SqliteSnapshot] = []

    for i, path in enumerate(paths):
        # Checked before exists(), which follows the link: a symlinked database would
        # otherwise be copied into the archive as a regular file holding content from
        # outside every configured source, which the rest of the backup refuses to do.
        if _reached_through_symlink(path, source_paths=source_paths):
            logger.warning(f"Skip backup of symlink: {path}")
            continue

        if not path.exists():
            logger.warning(f"Configured sqlite database not found, skipping: {path}")
            continue

        arcname = archive_name_for(path, source_paths=source_paths, strip=strip)
        # Index-prefixed subdirectory so two databases sharing a filename in different
        # directories cannot overwrite one another.
        dest = dest_dir / f"{i:03d}" / path.name
        snapshot_database(path, dest=dest)
        snapshots.append(SqliteSnapshot(source=path, snapshot=dest, arcname=arcname))

    if snapshots:
        logger.info(f"Snapshotted {len(snapshots)} of {len(paths)} configured sqlite databases")

    return snapshots
