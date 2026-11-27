"""Take consistent point-in-time copies of live SQLite databases.

Copying a SQLite file while a service holds it open can capture a torn page, producing an
archive that will not open. Use these helpers to copy a database through SQLite's online-backup
API instead, which is safe against a concurrent writer and folds in any WAL or rollback journal.
"""

from __future__ import annotations

import errno
import os
import shutil
import sqlite3
import stat
import subprocess  # ruff:ignore[suspicious-subprocess-import]
import sys
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote

from loguru import logger

from ezbak.exceptions import ConfigurationError, SqliteSnapshotError
from ezbak.no_follow import NO_FOLLOW_READ_FLAGS
from ezbak.sources import open_source

# Longest a snapshot waits on another process's write lock. `Connection.backup()` retries
# SQLITE_BUSY forever on its own, so without a bound one long write transaction wedges a
# scheduled backup indefinitely; failing the run surfaces the problem instead.
SQLITE_BACKUP_LOCK_TIMEOUT_SECONDS: float = 300.0


def _copy_read_only(source: Path, *, dest: Path) -> None:
    """Copy `source` to `dest` through the online-backup API over a read-only connection.

    Raises `TimeoutError` (an `OSError`, so callers already handle it) when another process
    holds a write lock past `SQLITE_BACKUP_LOCK_TIMEOUT_SECONDS`.

    Args:
        source (Path): The database to copy.
        dest (Path): The destination file, overwritten if present.
    """
    # The path is percent-encoded because SQLite parses a URI filename, so a bare '?', '#', or
    # '%' in it would be structure.
    dsn = f"file:{quote(str(source))}?mode=ro"
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
        closing(sqlite3.connect(dsn, uri=True)) as src_conn,
        closing(sqlite3.connect(dest)) as dest_conn,
    ):
        src_conn.backup(dest_conn, progress=_abort_when_stuck)


def _copy_regular_file(source: Path, *, dest: Path) -> None:
    """Copy a file the backed-up service controls into ezbak's staging.

    Use this instead of `shutil.copyfile` for any file the service can replace. `O_NOFOLLOW`
    refuses a planted symlink that would pull in a file from outside the source, and
    `O_NONBLOCK` keeps a planted fifo from blocking the open forever.

    Args:
        source (Path): The file to copy.
        dest (Path): The new file to create. It must not exist.

    Raises:
        SqliteSnapshotError: If `source` is not a regular file.
    """
    fd = os.open(source, flags=NO_FOLLOW_READ_FLAGS)
    with os.fdopen(fd, mode="rb") as src_file:
        if not stat.S_ISREG(os.fstat(src_file.fileno()).st_mode):
            msg = f"Expected '{source}' to be a regular file"
            raise SqliteSnapshotError(msg)
        with dest.open("xb") as dest_file:
            shutil.copyfileobj(fsrc=src_file, fdst=dest_file)


def _file_identity(path: Path) -> tuple[int, int, int]:
    """Fingerprint a file so a change between two reads of it can be detected.

    Args:
        path (Path): The file to fingerprint. A symlink is fingerprinted, not followed.

    Returns:
        tuple[int, int, int]: The inode, size, and modification time in nanoseconds.
    """
    file_stat = path.lstat()
    return (file_stat.st_ino, file_stat.st_size, file_stat.st_mtime_ns)


def _names_super_journal(journal: Path) -> bool:
    """Report whether a rollback journal names a super-journal that its rollback would delete.

    SQLite reads a super-journal name only from a journal that ends with the journal magic, so
    this check is never narrower than SQLite's own.

    Args:
        journal (Path): A rollback journal in ezbak's private staging.

    Returns:
        bool: True if the journal ends with the journal magic.
    """
    with journal.open("rb") as f:
        f.seek(max(journal.stat().st_size - len(_ROLLBACK_JOURNAL_MAGIC), 0))
        return f.read() == _ROLLBACK_JOURNAL_MAGIC


def _copy_rolling_back_hot_journal(source: Path, *, dest: Path) -> None:
    """Copy a database whose hot rollback journal holds an unfinished transaction.

    Use this when a read-only open reports a hot journal, which means a writer crashed mid
    transaction. The database and journal are copied into ezbak's own staging so the rollback
    writes nothing beside the live database.

    A journal can name a super-journal, which rollback deletes. The service controls that name,
    so honoring it would let the service make ezbak delete any file ezbak can reach. Such a
    journal is refused rather than replayed. Stripping the name instead would roll back a
    transaction that may have committed in another attached database.

    Args:
        source (Path): The live database.
        dest (Path): The destination file, overwritten if present.

    Raises:
        SqliteSnapshotError: If the journal names a super-journal, either file is not a
            regular file, or either file changed while it was copied.
    """
    journal = source.with_name(source.name + "-journal")
    # No lock is held on the live files, so a service that recovers or writes during the copy
    # would pair a database and journal from different moments. Refuse such a pair.
    identity_before_copy = (_file_identity(source), _file_identity(journal))
    with TemporaryDirectory(dir=dest.parent) as tmp:
        staged = Path(tmp) / source.name
        staged_journal = staged.with_name(staged.name + "-journal")
        _copy_regular_file(journal, dest=staged_journal)
        if _names_super_journal(staged_journal):
            msg = (
                f"Refusing to roll back the journal of '{source}': it names a super-journal "
                "from a multi-database transaction, which ezbak does not recover. Start the "
                "service once to recover it"
            )
            raise SqliteSnapshotError(msg)

        _copy_regular_file(source, dest=staged)
        if (_file_identity(source), _file_identity(journal)) != identity_before_copy:
            msg = (
                f"'{source}' or its hot journal changed while ezbak copied them; expected both "
                "to stay unchanged until the copy finished"
            )
            raise SqliteSnapshotError(msg)
        # Any read through a read-write connection replays the hot journal into the staged copy.
        with closing(sqlite3.connect(staged)) as conn:
            conn.execute("PRAGMA schema_version").fetchone()
        # Staging shares dest's directory, so this is a rename rather than another full copy.
        staged.replace(dest)


# Journal files SQLite keeps beside a database. They belong to the live file, not to a
# snapshot, so they are excluded from the archive. Master journals (-mj*) only appear for
# multi-database transactions, which ezbak does not support.
SQLITE_SIDECAR_SUFFIXES: tuple[str, ...] = ("-wal", "-shm", "-journal")

GLOB_METACHARACTERS: tuple[str, ...] = ("*", "?", "[")


def is_sqlite_path_pattern(entry: Path) -> bool:
    """Report whether a configured sqlite entry should be read as a glob pattern.

    Check the filesystem before the spelling so a database genuinely named `weird[1].db`
    keeps working: the glob reading applies only once the literal reading has failed. The
    symlink check covers a dangling link, which `exists()` reports as missing but which the
    snapshot step should still reject by name rather than silently reinterpret.

    Args:
        entry (Path): The configured sqlite path entry.

    Returns:
        bool: True when the entry is a pattern to expand.
    """
    if entry.is_symlink() or entry.exists():
        return False

    return any(char in str(entry) for char in GLOB_METACHARACTERS)


def static_pattern_prefix(pattern: Path) -> Path | None:
    """Return the deepest directory a pattern is rooted in, before any globbing component.

    Use this to validate a pattern at configuration time and to anchor glob expansion
    at the pattern's literal prefix rather than the filesystem root. A pattern whose
    first component already globs has nothing checkable and is reported as None rather
    than as the filesystem root, which would always exist and prove nothing.

    Args:
        pattern (Path): The pattern to inspect.

    Returns:
        Path | None: The rooted directory, or None when the pattern globs from its start.
    """
    static: list[str] = []
    for part in pattern.parts:
        if any(char in part for char in GLOB_METACHARACTERS):
            break
        static.append(part)

    if not static:
        return None

    prefix = Path(*static)
    return None if prefix == Path(pattern.anchor) else prefix


def unusable_pattern_component(pattern: Path) -> str | None:
    """Return the first component that misuses `**`, or None when the pattern is usable.

    Use this to reject a pattern at configuration time. `Path.glob` raises `ValueError` for
    a `**` that is not an entire component on Python before 3.13 and silently reads it as a
    single `*` from 3.13 on, so an unchecked pattern is either a crash or a different result
    depending on the interpreter.

    Args:
        pattern (Path): The pattern to inspect.

    Returns:
        str | None: The offending component, or None when every component is usable.
    """
    return next((part for part in pattern.parts if "**" in part and part != "**"), None)


def _keep_databases(matches: list[Path], *, source_paths: list[Path]) -> list[Path]:
    """Reduce a pattern's raw matches to the databases worth snapshotting.

    A pattern match is a candidate rather than an assertion: the operator described a shape,
    not a file, so anything that cannot be snapshotted is dropped and left to the ordinary
    file walk instead of failing the run. A literal path is the opposite, and keeps failing
    loudly, because there the operator named the file.

    Args:
        matches (list[Path]): The raw glob matches.
        source_paths (list[Path]): The configured backup source paths.

    Returns:
        list[Path]: The matches that name a snapshottable database.
    """
    kept: list[Path] = []

    for match in matches:
        # Also rules out a fifo, socket, device, or dangling link. Opening a fifo to read
        # its header blocks until a writer arrives, which would wedge the run indefinitely.
        if not match.is_file():
            logger.debug(f"Skip sqlite pattern match that is not a regular file: {match}")
            continue

        # A sidecar has no meaning without the live database beside it, and the database
        # itself is matched separately when the pattern reaches it.
        if match.name.endswith(SQLITE_SIDECAR_SUFFIXES):
            logger.debug(f"Skip sqlite journal matched by pattern: {match}")
            continue

        if not is_sqlite_database(match):
            logger.debug(f"Skip pattern match that is not a sqlite database: {match}")
            continue

        containers = containing_source_paths(match, source_paths=source_paths)
        if len(containers) != 1:
            logger.warning(f"Skip sqlite pattern match not inside exactly one source path: {match}")
            continue

        kept.append(match)

    return kept


def _expand_pattern(pattern: Path, *, source_paths: list[Path]) -> list[Path]:
    """Resolve one pattern against the filesystem.

    An absolute pattern names its own root. A relative one is expanded against each
    configured source path, so it means "anywhere under what I am backing up" rather than
    resolving against the process working directory, which in a container is whatever the
    image happened to set.

    Args:
        pattern (Path): The pattern to expand.
        source_paths (list[Path]): The configured backup source paths.

    Returns:
        list[Path]: The matching databases.
    """
    if pattern.is_absolute():
        # A static prefix lets the glob start below the root, rather than walking every
        # literal directory component from `/` on each call. `static_pattern_prefix`
        # returns None when the pattern globs from its first component, which is exactly
        # when there is no better root than the anchor itself.
        prefix = static_pattern_prefix(pattern)
        root = prefix if prefix is not None else Path(pattern.anchor)
        roots = [root]
        relative = pattern.relative_to(root)
    else:
        roots = source_paths
        relative = pattern

    matches: list[Path] = []
    for root in roots:
        matches.extend(_keep_databases(sorted(root.glob(str(relative))), source_paths=source_paths))

    return matches


def expand_sqlite_paths(entries: list[Path], *, source_paths: list[Path]) -> list[Path]:
    """Resolve configured sqlite entries into the concrete databases to snapshot.

    Call this once per backup run rather than at configuration time. A cron sidecar lives
    for weeks, and a set of databases frozen at startup would silently miss every one
    created afterwards, which is exactly the case patterns exist to serve.

    Ordering is fully determined, since `snapshot_databases` stages each snapshot in a
    subdirectory named for its index: entries in configured order, a relative pattern's
    source paths in configured order, and each glob call's matches sorted.

    Args:
        entries (list[Path]): The configured sqlite paths, literal or pattern.
        source_paths (list[Path]): The configured backup source paths.

    Returns:
        list[Path]: The databases to snapshot, deduplicated, first occurrence kept.
    """
    expanded: list[Path] = []

    for entry in entries:
        if not is_sqlite_path_pattern(entry):
            expanded.append(entry)
            continue

        matches = _expand_pattern(entry, source_paths=source_paths)
        if not matches:
            # Not an error: a pre-start restore on a fresh deployment has an empty data
            # directory, and the databases arrive with the service.
            logger.warning(f"Pattern matched no sqlite databases: {entry}")

        expanded.extend(matches)

    return list(dict.fromkeys(expanded))


# Header bytes SQLite writes at the front of each journal form. A WAL carries one of two
# magic numbers, chosen by the checksum byte order it was written with.
_WAL_MAGIC: tuple[bytes, ...] = (b"\x37\x7f\x06\x82", b"\x37\x7f\x06\x83")
_ROLLBACK_JOURNAL_MAGIC = b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7"
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

    magics = _WAL_MAGIC if suffix == "-wal" else (_ROLLBACK_JOURNAL_MAGIC,)
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


def _copy_live_database(source: Path, *, dest: Path) -> bool:
    """Copy a live database without ever opening it read-write.

    A read-write open replays a hot rollback journal in place, and the service that owns the
    directory controls that journal. A read-only open refuses the replay instead, checked under
    SQLite's own lock, so there is no window between a check and the open for the service to
    plant a journal. A refused replay is redone on a private copy.

    Args:
        source (Path): The live database.
        dest (Path): The destination file, overwritten if present.

    Returns:
        bool: True if an unfinished transaction was rolled back in the copy.

    Raises:
        sqlite3.Error: If the database cannot be read for any reason other than a hot journal.
        FileNotFoundError: If the database vanishes while its hot journal is still present.
    """
    try:
        _copy_read_only(source, dest=dest)
    except sqlite3.Error as e:
        # Errors raised by the sqlite3 module itself, not by SQLite, carry no error code.
        if getattr(e, "sqlite_errorcode", None) != sqlite3.SQLITE_READONLY_ROLLBACK:
            raise
        try:
            _copy_rolling_back_hot_journal(source, dest=dest)
        except FileNotFoundError:
            if source.with_name(source.name + "-journal").exists():
                raise
            # The service recovered the journal after the read-only open refused it.
            _copy_read_only(source, dest=dest)
            return False
        return True

    return False


@dataclass(frozen=True)
class _FileOwner:
    """The user and group a snapshot worker runs as."""

    uid: int
    gid: int


@dataclass(frozen=True)
class _DatabaseStat:
    """Metadata of a database and of the directory that holds it, read without symlinks."""

    database: os.stat_result
    directory: os.stat_result


def _stat_without_symlinks(source: Path) -> _DatabaseStat:
    """Stat a database, and its directory, reached without following a symlink in the path.

    Use this for any decision about the database's owner or permissions, so a symlink the
    service plants in the path cannot substitute another file's metadata.

    Args:
        source (Path): The live database.

    Returns:
        _DatabaseStat: The metadata of the database file and of its directory.

    Raises:
        SqliteSnapshotError: If the path contains a symlink or cannot be opened.
    """
    try:
        with open_source(source.absolute()) as handle:
            return _DatabaseStat(database=os.fstat(handle.fd), directory=os.fstat(handle.parent_fd))
    except ConfigurationError as e:
        msg = f"Failed to snapshot sqlite database '{source}': {e}"
        raise SqliteSnapshotError(msg) from e


def _owner_to_snapshot_as(source_stat: _DatabaseStat) -> _FileOwner | None:
    """Choose the user a snapshot runs as, so ezbak never reaches further than the service.

    Running as root, every file SQLite opens or creates beside the database, and every path
    the service redirects with a symlink, would be reached with root's privileges. Copying as
    a user who controls the database limits all of that to files that user can already reach.
    The database's owner is preferred because it can read the file. A root-owned database in a
    directory another user owns is copied as the directory's owner, who controls the journals
    and the path. Only a database where root owns both is copied as root.

    Args:
        source_stat (_DatabaseStat): The database's metadata, from `_stat_without_symlinks`.

    Returns:
        _FileOwner | None: The user to run a worker as, or None to copy in this process.
    """
    if os.geteuid() != 0:
        return None
    for owned in (source_stat.database, source_stat.directory):
        if owned.st_uid != 0:
            return _FileOwner(uid=owned.st_uid, gid=owned.st_gid)
    return None


# Runs the worker with -I so neither the environment nor the working directory can change
# which code the worker imports.
_SNAPSHOT_WORKER_CODE = "from ezbak.sqlite import run_snapshot_worker; run_snapshot_worker()"
_WORKER_ROLLED_BACK = "rolled-back"
_WORKER_COPIED = "copied"
_OWNER_CAPABILITIES_HINT = (
    "copying a database as its owner needs the CHOWN, SETUID, and SETGID capabilities"
)


def _drop_privileges_to(owner: _FileOwner) -> None:
    """Switch this process to `owner` so every later file access has only that user's rights.

    Args:
        owner (_FileOwner): The user and group to switch to.

    Raises:
        SqliteSnapshotError: If the process lacks the capabilities to switch users.
    """
    # A worker already running as the owner has nothing to drop, and without root the
    # setgroups call below would fail.
    if (os.geteuid(), os.getegid()) == (owner.uid, owner.gid):
        return
    try:
        os.setgroups([])
        os.setgid(owner.gid)
        os.setuid(owner.uid)
    except PermissionError as e:
        msg = f"Cannot switch to uid {owner.uid}, gid {owner.gid}: {e}; {_OWNER_CAPABILITIES_HINT}"
        raise SqliteSnapshotError(msg) from e


def run_snapshot_worker() -> None:
    """Copy one live database as its owner, inside a worker process `snapshot_database` starts.

    Not for direct use. Arguments arrive in `sys.argv`: owner uid, owner gid, source, dest.
    Privileges are dropped before any file is opened. The outcome is reported on stdout and
    any failure on stderr with exit status 1.
    """
    uid, gid, source, dest = sys.argv[1:]
    owner = _FileOwner(uid=int(uid), gid=int(gid))
    try:
        _drop_privileges_to(owner)
        rolled_back = _copy_live_database(Path(source), dest=Path(dest))
    except (sqlite3.Error, OSError, SqliteSnapshotError) as e:
        sys.stderr.write(f"{e}\n")
        sys.exit(1)
    sys.stdout.write(_WORKER_ROLLED_BACK if rolled_back else _WORKER_COPIED)


def _take_worker_output(worker_dest: Path, *, dest: Path, owner: _FileOwner, source: Path) -> None:
    """Move the worker's snapshot into ezbak's staging, refusing anything but its own file.

    The worker's user controls the entries in its directory, so the output is opened without
    following a symlink and checked through that descriptor before root moves it. A file that
    user does not own, or that has another link, is not the worker's output: it could be a hard
    link to a file only root can read.

    Args:
        worker_dest (Path): The worker's output, inside its directory.
        dest (Path): The destination in ezbak's staging, overwritten if present.
        owner (_FileOwner): The user the worker ran as.
        source (Path): The live database, named in any error.

    Raises:
        SqliteSnapshotError: If the output is not a regular file that the worker's user owns
            with one link, or is replaced while it is moved.
        OSError: If the move into staging fails for a reason other than crossing filesystems.
    """
    expected = (
        f"Expected the snapshot worker for '{source}' to leave a regular file owned by uid "
        f"{owner.uid} with one link at '{worker_dest}'"
    )
    try:
        fd = os.open(worker_dest, flags=NO_FOLLOW_READ_FLAGS)
    except OSError as e:
        msg = f"{expected}: {e}"
        raise SqliteSnapshotError(msg) from e

    with os.fdopen(fd, mode="rb") as output:
        output_stat = os.fstat(output.fileno())
        if (
            not stat.S_ISREG(output_stat.st_mode)
            or output_stat.st_uid != owner.uid
            or output_stat.st_nlink != 1
        ):
            raise SqliteSnapshotError(expected)

        try:
            worker_dest.replace(dest)
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
            # Another filesystem: copy from the descriptor already checked, never by path.
            dest.unlink(missing_ok=True)
            with dest.open("xb") as dest_file:
                shutil.copyfileobj(fsrc=output, fdst=dest_file)
            return

    moved = dest.lstat()
    if (moved.st_dev, moved.st_ino) != (output_stat.st_dev, output_stat.st_ino):
        # The entry was swapped between the check and the move. What arrived came from the
        # worker's own directory, so removing it from staging touches nothing outside.
        if stat.S_ISDIR(moved.st_mode):
            shutil.rmtree(dest)
        else:
            dest.unlink()
        raise SqliteSnapshotError(expected)


def _copy_live_database_as(owner: _FileOwner, *, source: Path, dest: Path) -> bool:
    """Copy a live database in a worker process that runs as the database's owner.

    The worker writes into a directory its user owns, outside ezbak's staging, and the result
    is moved into staging only after the worker exits.

    Args:
        owner (_FileOwner): The user and group to run the worker as.
        source (Path): The live database.
        dest (Path): The destination file, overwritten if present.

    Returns:
        bool: True if an unfinished transaction was rolled back in the copy.

    Raises:
        SqliteSnapshotError: If the worker fails or leaves something other than a regular file.
    """
    with TemporaryDirectory(prefix="ezbak-sqlite-") as tmp:
        # The worker's directory sits inside one root owns and only lets others traverse, so
        # the worker's user can change what is inside its directory but cannot rename or
        # replace the directory itself while root reads from it.
        Path(tmp).chmod(0o711)
        workdir = Path(tmp) / "work"
        workdir.mkdir(mode=0o700)
        try:
            os.chown(workdir, uid=owner.uid, gid=owner.gid)
        except PermissionError as e:
            msg = (
                f"Cannot give the snapshot worker for '{source}' a directory owned by uid "
                f"{owner.uid}: {e}; {_OWNER_CAPABILITIES_HINT}"
            )
            raise SqliteSnapshotError(msg) from e
        worker_dest = workdir / dest.name
        result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]
            [
                sys.executable,
                "-I",
                "-c",
                _SNAPSHOT_WORKER_CODE,
                str(owner.uid),
                str(owner.gid),
                # The worker runs from "/", so a relative path would name another file there.
                str(source.absolute()),
                str(worker_dest),
            ],
            capture_output=True,
            text=True,
            errors="replace",
            env={},
            cwd="/",
            check=False,
        )
        if result.returncode != 0:
            msg = (
                f"Failed to snapshot sqlite database '{source}': {result.stderr.strip()} "
                f"(copied as uid {owner.uid})"
            )
            raise SqliteSnapshotError(msg)

        _take_worker_output(worker_dest, dest=dest, owner=owner, source=source)

    return result.stdout == _WORKER_ROLLED_BACK


def snapshot_database(source: Path, *, dest: Path) -> None:
    """Write a consistent, verified copy of a live SQLite database.

    Use this instead of copying the file when a service may hold the database open. The copy is
    taken through SQLite's online-backup API, so it is safe against a concurrent writer, and any
    WAL or hot rollback journal is folded into the result. When ezbak runs as root, the copy
    runs as the database's owner.

    Args:
        source (Path): The live database to snapshot.
        dest (Path): Where to write the snapshot. Parent directories are created as needed.

    Raises:
        SqliteSnapshotError: If the database cannot be copied or the copy fails verification.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    source_stat = _stat_without_symlinks(source)
    owner = _owner_to_snapshot_as(source_stat)

    try:
        if owner is None:
            rolled_back = _copy_live_database(source, dest=dest)
        else:
            rolled_back = _copy_live_database_as(owner, source=source, dest=dest)
    except (sqlite3.Error, OSError) as e:
        msg = f"Failed to snapshot sqlite database '{source}': {e}"
        raise SqliteSnapshotError(msg) from e

    if rolled_back:
        logger.info(f"Rolled back an unfinished transaction of '{source}' in its snapshot")

    _verify(dest, source=source)

    # The archive records the snapshot's metadata, not the live file's, and SQLite creates the
    # snapshot at 0666 less the umask. Carry the database's own permissions across so a 0600
    # database does not restore world-readable. Done after verification, which needs to open
    # the snapshot for writing.
    try:
        dest.chmod(stat.S_IMODE(source_stat.database.st_mode))
    except OSError as e:
        logger.warning(f"Could not copy permissions of '{source}' onto its snapshot: {e}")

    logger.debug(f"Snapshotted sqlite database '{source}'")


def _reached_through_symlink(path: Path) -> bool:
    """Report whether a database is a symlink or sits behind one anywhere in its path.

    The backup refuses a source path that contains a symlink and never follows one inside a
    source, so a database reached through one would be archived holding content from outside
    every source.

    Args:
        path (Path): The configured database path.

    Returns:
        bool: True if the path itself or any parent directory is a symlink.
    """
    return any(candidate.is_symlink() for candidate in (path, *path.parents))


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
    path that is a symlink, or that is reached through one, is warned about and skipped too,
    matching how the source walk treats symlinks. A path that exists but cannot
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
        if _reached_through_symlink(path):
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
