"""Tests for consistent SQLite snapshots."""

import os
import sqlite3
import stat
import struct
import subprocess
import sys
import textwrap
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from ezbak import sqlite as ezbak_sqlite
from ezbak.exceptions import SqliteSnapshotError
from ezbak.sqlite import (
    archive_name_for,
    containing_source_paths,
    expand_sqlite_paths,
    is_sqlite_database,
    is_sqlite_path_pattern,
    is_sqlite_sidecar,
    sidecar_paths,
    snapshot_database,
    snapshot_databases,
    static_pattern_prefix,
    unusable_pattern_component,
)
from tests.helpers import make_db, only_as_root, row_count, skip_as_root

# Header magic of a SQLite rollback journal, from the file format.
ROLLBACK_JOURNAL_MAGIC = bytes.fromhex("d9d505f920a163d7")

# A user that owns nothing on the host, for root-only tests that drop privileges.
NOBODY_UID = 65534


@pytest.fixture
def others_can_traverse_tmp_path(tmp_path):
    """Open `tmp_path` and every parent to traversal, as a service's data path is.

    pytest creates its temporary root as 0700, which would stop a dropped worker before it
    reaches the permission a test means to exercise. The original modes are restored after
    the test, since the parents are shared with every other test and the host.
    """
    original_modes = {path: path.stat().st_mode for path in (tmp_path, *tmp_path.parents)}
    for path, mode in original_modes.items():
        path.chmod(mode | 0o111)
    yield
    for path, mode in original_modes.items():
        path.chmod(stat.S_IMODE(mode))


@pytest.fixture
def readonly_dir():
    """Strip write permission from a directory, restoring it so pytest can clean up.

    Yields:
        Callable[[Path], None]: Makes the given directory non-writable for the test.
    """
    stripped: list[Path] = []

    def _strip(directory: Path) -> None:
        directory.chmod(0o555)
        stripped.append(directory)

    yield _strip

    for directory in stripped:
        directory.chmod(0o755)


def test_snapshot_database_copies_committed_rows(tmp_path):
    """Verify a snapshot of an idle database reproduces its contents."""
    # Given a WAL database with five rows
    source = tmp_path / "live.db"
    make_db(source, rows=5)
    dest = tmp_path / "snap" / "live.db"

    # When snapshotting it
    snapshot_database(source, dest=dest)

    # Then the snapshot holds the same rows
    assert dest.exists()
    assert row_count(dest) == 5


def test_snapshot_database_excludes_uncommitted_writes(tmp_path):
    """Verify a snapshot taken under a live writer captures only committed state."""
    # Given a WAL database with an open, uncommitted transaction from a second connection
    source = tmp_path / "live.db"
    make_db(source, rows=5)
    dest = tmp_path / "snap" / "live.db"

    with closing(sqlite3.connect(source)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO t (v) VALUES ('uncommitted')")

        # When snapshotting while that transaction is in flight
        snapshot_database(source, dest=dest)

    # Then the snapshot is consistent and holds only the committed rows
    assert row_count(dest) == 5


def test_snapshot_database_rejects_a_non_database(tmp_path):
    """Verify a file that is not a SQLite database fails the snapshot."""
    # Given a plain text file
    source = tmp_path / "notadb.db"
    source.write_text("this is not a database")
    dest = tmp_path / "snap" / "notadb.db"

    # When snapshotting it
    # Then the failure is reported as a snapshot error
    with pytest.raises(SqliteSnapshotError):
        snapshot_database(source, dest=dest)


def _damage_btree_page_header(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[100:120] = b"\xff" * 20
    path.write_bytes(bytes(data))


def _damage_interior_page(path: Path) -> None:
    data = bytearray(path.read_bytes())
    start = 4096 * 3 + 8
    data[start : start + 32] = b"\xff" * 32
    path.write_bytes(bytes(data))


@pytest.mark.parametrize(
    ("rows", "damage"),
    [
        pytest.param(50, _damage_btree_page_header, id="first-page-header"),
        pytest.param(2000, _damage_interior_page, id="later-page"),
    ],
)
def test_snapshot_database_rejects_a_corrupt_database(
    tmp_path: Path, rows: int, damage: Callable[[Path], None]
) -> None:
    """Verify a database with a damaged page fails rather than shipping silently."""
    # Given a rollback-mode database with one of its pages overwritten
    source = tmp_path / "corrupt.db"
    make_db(source, rows=rows, wal=False)
    damage(source)
    dest = tmp_path / "snap" / "corrupt.db"

    # When snapshotting it
    # Then it fails, whether the copy or the integrity check catches it
    with pytest.raises(SqliteSnapshotError):
        snapshot_database(source, dest=dest)


def test_snapshot_database_handles_a_rollback_journal_database(tmp_path):
    """Verify a DELETE-mode database, which never has -wal or -shm, snapshots correctly."""
    # Given a DELETE-mode database
    source = tmp_path / "rollback.db"
    make_db(source, rows=7, wal=False)
    assert not source.with_name("rollback.db-wal").exists()
    assert not source.with_name("rollback.db-shm").exists()
    dest = tmp_path / "snap" / "rollback.db"

    # When snapshotting it
    snapshot_database(source, dest=dest)

    # Then the snapshot opens with the full contents
    assert row_count(dest) == 7


def test_snapshot_database_recovers_a_hot_journal(tmp_path):
    """Verify a database abandoned mid-transaction snapshots to a recovered, clean file.

    This is the case a raw file copy gets wrong: copying the database alone discards the
    rollback journal it needs to become consistent again, so the copy restores corrupt. The
    rollback happens on ezbak's own copy, so the live files stay as found for the service to
    recover on its next start.
    """
    # Given a DELETE-mode database whose writer was killed mid-transaction, leaving its
    # uncommitted pages in the database file and a hot journal on disk
    source = tmp_path / "data" / "hot.db"
    make_db(source, rows=200, wal=False)

    _crash_mid_transaction(source)
    live_files = {path.name: path.read_bytes() for path in source.parent.iterdir()}

    dest = tmp_path / "snap" / "hot.db"

    # When snapshotting it
    snapshot_database(source, dest=dest)

    # Then the snapshot holds the rolled-back state, not the abandoned write
    assert row_count(dest) == 200
    with closing(sqlite3.connect(dest)) as conn:
        assert conn.execute("SELECT count(*) FROM t WHERE v = 'clobbered'").fetchone()[0] == 0

    # And the live database and its journal are untouched
    assert {path.name: path.read_bytes() for path in source.parent.iterdir()} == live_files


@skip_as_root
def test_snapshot_database_reads_a_rollback_database_in_a_readonly_directory(
    tmp_path, readonly_dir
):
    """Verify a DELETE-mode database snapshots from a directory that cannot be written.

    A rollback-mode database needs no sidecar file to be read.
    """
    # Given a DELETE-mode database in a directory with no write permission
    data = tmp_path / "data"
    source = data / "rollback.db"
    make_db(source, rows=7, wal=False)
    readonly_dir(data)

    # When snapshotting it
    snapshot_database(source, dest=tmp_path / "snap" / "rollback.db")

    # Then the snapshot holds the full contents
    assert row_count(tmp_path / "snap" / "rollback.db") == 7


@skip_as_root
def test_snapshot_database_fails_for_a_wal_database_in_a_readonly_directory(tmp_path, readonly_dir):
    """Verify a WAL database with no -shm on disk cannot be snapshotted read-only.

    SQLite cannot read a WAL database without a usable -shm, and it cannot create one in a
    directory it may not write, so the whole backup fails rather than archiving a database
    the run could not copy consistently. This is why a source holding WAL databases must be
    mounted read-write.
    """
    # Given an idle WAL database whose -wal and -shm were checkpointed away on close,
    # in a directory with no write permission
    data = tmp_path / "data"
    source = data / "live.db"
    make_db(source, rows=5)
    assert not source.with_name("live.db-shm").exists()
    readonly_dir(data)

    # When snapshotting it
    # Then the snapshot fails
    with pytest.raises(SqliteSnapshotError):
        snapshot_database(source, dest=tmp_path / "snap" / "live.db")


@skip_as_root
def test_snapshot_database_copies_a_readonly_database_file(tmp_path):
    """Verify a database file with no write permission snapshots when its directory is writable.

    SQLite can still create the -shm beside it.
    """
    # Given a WAL database whose file is not writable, in a writable directory
    data = tmp_path / "data"
    source = data / "live.db"
    make_db(source, rows=4)
    source.chmod(0o444)

    # When snapshotting it
    snapshot_database(source, dest=tmp_path / "snap" / "live.db")

    # Then the snapshot holds the full contents
    assert row_count(tmp_path / "snap" / "live.db") == 4


def _write_hot_journal(database: Path, *, super_journal: Path | None) -> None:
    """Plant the rollback journal a writer killed mid-transaction leaves beside `database`.

    The journal holds no page records, so replaying it changes nothing in the database. A
    super-journal record makes rollback delete the file it names.

    Args:
        database (Path): The DELETE-mode database to plant the journal beside.
        super_journal (Path | None): The file the journal names as its super-journal, if any.
    """
    page_size = 4096
    header = ROLLBACK_JOURNAL_MAGIC + struct.pack(
        ">5I", 0, 0, database.stat().st_size // page_size, page_size, page_size
    )
    journal = header.ljust(page_size, b"\x00")
    if super_journal is not None:
        name = str(super_journal).encode()
        journal += (
            struct.pack(">I", 0)
            + name
            + struct.pack(">2I", len(name), sum(name) & 0xFFFFFFFF)
            + ROLLBACK_JOURNAL_MAGIC
        )
    database.with_name(database.name + "-journal").write_bytes(journal)


def test_snapshot_database_refuses_a_journal_that_names_a_super_journal(tmp_path):
    """Verify a planted journal cannot make the snapshot delete a file it names.

    Rolling back a journal deletes the super-journal it names. The service owns the database
    directory and so controls that name, while ezbak may run with more privilege than it.
    """
    # Given a DELETE-mode database whose hot journal names an unrelated file
    source = tmp_path / "data" / "app.db"
    make_db(source, rows=3, wal=False)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    _write_hot_journal(source, super_journal=victim)

    # When snapshotting it
    # Then the snapshot is refused and the named file survives
    with pytest.raises(SqliteSnapshotError, match="super-journal"):
        snapshot_database(source, dest=tmp_path / "snap" / "app.db")
    assert victim.read_text() == "keep"


def test_snapshot_database_refuses_a_symlinked_hot_journal(tmp_path):
    """Verify a hot journal that is a symlink is not followed out of the source directory."""
    # Given a database whose hot journal is a symlink to a journal elsewhere
    source = tmp_path / "data" / "app.db"
    make_db(source, rows=3, wal=False)
    _write_hot_journal(source, super_journal=None)
    elsewhere = tmp_path / "elsewhere-journal"
    source.with_name("app.db-journal").rename(elsewhere)
    source.with_name("app.db-journal").symlink_to(elsewhere)

    # When snapshotting it
    # Then the snapshot is refused
    with pytest.raises(SqliteSnapshotError):
        snapshot_database(source, dest=tmp_path / "snap" / "app.db")


def test_snapshot_database_refuses_a_hot_journal_pair_that_changes_mid_copy(tmp_path, monkeypatch):
    """Verify a database written to while its hot journal is copied is not archived.

    Nothing locks the live files during that copy, so a service that writes in the meantime
    would pair a journal and a database from different moments.
    """
    # Given a database with a hot journal that the service writes to during the copy
    source = tmp_path / "data" / "app.db"
    make_db(source, rows=3, wal=False)
    _write_hot_journal(source, super_journal=None)
    copy_regular_file = ezbak_sqlite._copy_regular_file

    def copy_then_write(path: Path, *, dest: Path) -> None:
        copy_regular_file(path, dest=dest)
        with source.open("ab") as live:
            live.write(b"\x00" * 4096)

    monkeypatch.setattr(ezbak_sqlite, "_copy_regular_file", copy_then_write)

    # When snapshotting it
    # Then the snapshot is refused
    with pytest.raises(SqliteSnapshotError, match="changed while ezbak copied"):
        snapshot_database(source, dest=tmp_path / "snap" / "app.db")


def test_snapshot_database_copies_normally_when_the_hot_journal_is_recovered_first(
    tmp_path, monkeypatch
):
    """Verify a hot journal the service recovers before ezbak copies it does not fail the run."""
    # Given a database whose hot journal the service recovers right after ezbak finds it
    source = tmp_path / "data" / "app.db"
    make_db(source, rows=3, wal=False)
    _write_hot_journal(source, super_journal=None)
    copy_regular_file = ezbak_sqlite._copy_regular_file

    def recover_then_copy(path: Path, *, dest: Path) -> None:
        source.with_name("app.db-journal").unlink(missing_ok=True)
        copy_regular_file(path, dest=dest)

    monkeypatch.setattr(ezbak_sqlite, "_copy_regular_file", recover_then_copy)

    # When snapshotting it
    snapshot_database(source, dest=tmp_path / "snap" / "app.db")

    # Then the snapshot holds the database
    assert row_count(tmp_path / "snap" / "app.db") == 3


def _crash_mid_transaction(source: Path) -> None:
    """Kill a writer mid-transaction, leaving uncommitted pages in `source` and a hot journal.

    Args:
        source (Path): An existing DELETE-mode database made by `make_db`.
    """
    script = textwrap.dedent(f"""
        import os, sqlite3
        conn = sqlite3.connect({str(source)!r})
        # A tiny cache forces modified pages into the database file before any commit.
        conn.execute("PRAGMA cache_size=1")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE t SET v = 'clobbered'")
        conn.executemany("INSERT INTO t (v) VALUES (?)", [("clobbered",)] * 2000)
        # Abandon the process without commit or rollback, leaving the journal behind.
        os._exit(1)
    """)
    subprocess.run([sys.executable, "-c", script], check=False)
    assert source.with_name(source.name + "-journal").exists(), "expected a hot journal on disk"


def test_snapshot_database_refuses_a_path_through_a_symlinked_directory(tmp_path):
    """Verify a directory swapped for a symlink cannot redirect the snapshot elsewhere.

    The service can replace a directory above its database at any moment, so the snapshot
    itself refuses such a path, not only the earlier scan for configured databases.
    """
    # Given a database reached through a symlinked directory
    real = tmp_path / "elsewhere"
    make_db(real / "app.db", rows=3, wal=False)
    (tmp_path / "data").symlink_to(real)

    # When snapshotting it
    # Then the snapshot is refused
    with pytest.raises(SqliteSnapshotError, match="symlink"):
        snapshot_database(tmp_path / "data" / "app.db", dest=tmp_path / "snap" / "app.db")


@pytest.fixture
def snapshot_through_worker(monkeypatch):
    """Send every snapshot through the owner worker, running as the test's own user.

    The worker path is otherwise taken only by root for a database another user owns, so
    this exercises it whatever user runs the suite.
    """
    owner = ezbak_sqlite._FileOwner(uid=os.getuid(), gid=os.getgid())
    monkeypatch.setattr(ezbak_sqlite, "_owner_to_snapshot_as", lambda _: owner)


@pytest.mark.parametrize(
    "journal", [pytest.param(False, id="clean"), pytest.param(True, id="hot-journal")]
)
def test_snapshot_database_copies_through_the_owner_worker(
    tmp_path, snapshot_through_worker, journal
):
    """Verify a snapshot through the owner worker holds the committed rows."""
    # Given a database copied through the owner worker
    source = tmp_path / "data" / "app.db"
    make_db(source, rows=200, wal=False)
    if journal:
        _crash_mid_transaction(source)
    dest = tmp_path / "snap" / "app.db"

    # When snapshotting it
    snapshot_database(source, dest=dest)

    # Then the snapshot holds only the committed rows
    assert row_count(dest) == 200
    with closing(sqlite3.connect(dest)) as conn:
        assert conn.execute("SELECT count(*) FROM t WHERE v = 'clobbered'").fetchone()[0] == 0


def test_snapshot_database_copies_a_relative_path_through_the_owner_worker(
    tmp_path, monkeypatch, snapshot_through_worker
):
    """Verify a relative database path names the same file inside the worker as outside it."""
    # Given a database named relative to the working directory, copied through the worker
    make_db(tmp_path / "data" / "app.db", rows=3, wal=False)
    monkeypatch.chdir(tmp_path)
    dest = tmp_path / "snap" / "app.db"

    # When snapshotting it
    snapshot_database(Path("data") / "app.db", dest=dest)

    # Then the snapshot holds that database
    assert row_count(dest) == 3


def test_snapshot_database_reports_a_failure_inside_the_owner_worker(
    tmp_path, snapshot_through_worker
):
    """Verify a failure inside the worker fails the snapshot with the worker's reason."""
    # Given a database copied through the owner worker, whose journal names a super-journal
    source = tmp_path / "data" / "app.db"
    make_db(source, rows=3, wal=False)
    _write_hot_journal(source, super_journal=tmp_path / "victim.txt")

    # When snapshotting it
    # Then the snapshot fails with the worker's reason
    with pytest.raises(SqliteSnapshotError, match="super-journal"):
        snapshot_database(source, dest=tmp_path / "snap" / "app.db")


def _stat_owned_by(uid: int) -> os.stat_result:
    """Build stat metadata owned by `uid`, with matching group, for owner-choice tests.

    Args:
        uid (int): The owner, also used as the group.

    Returns:
        os.stat_result: Metadata whose only meaningful fields are the owner and group.
    """
    return os.stat_result((0o100644, 0, 0, 1, uid, uid, 0, 0, 0, 0))


@pytest.mark.parametrize(
    ("euid", "database_uid", "directory_uid", "expected_uid"),
    [
        pytest.param(1000, 1000, 1000, None, id="not-root"),
        pytest.param(0, 1000, 1000, 1000, id="service-owns-both"),
        pytest.param(0, 1000, 0, 1000, id="service-owns-database"),
        pytest.param(0, 0, 1000, 1000, id="root-database-in-service-directory"),
        pytest.param(0, 0, 0, None, id="root-owns-both"),
    ],
)
def test_snapshot_runs_as_a_user_who_controls_the_database(
    monkeypatch, euid, database_uid, directory_uid, expected_uid
):
    """Verify a root snapshot drops to whoever controls the database or its directory.

    A root-owned database in a directory the service owns is still the service's to tamper
    with, so only a database where root owns both is copied as root.
    """
    # Given ezbak's user and the owners of a database and its directory
    monkeypatch.setattr(os, "geteuid", lambda: euid)
    source_stat = ezbak_sqlite._DatabaseStat(
        database=_stat_owned_by(database_uid), directory=_stat_owned_by(directory_uid)
    )

    # When choosing the user to snapshot as
    owner = ezbak_sqlite._owner_to_snapshot_as(source_stat)

    # Then the copy runs as the expected user, or in this process
    assert (owner.uid if owner else None) == expected_uid


@pytest.mark.parametrize("planted", ["symlink", "hardlink"])
def test_snapshot_database_refuses_worker_output_that_is_not_its_own_file(
    tmp_path, monkeypatch, snapshot_through_worker, planted
):
    """Verify the worker's output is refused when it is a link to another file.

    The worker's user owns its work directory, so it can replace the output with a link to a
    file only root can read. Moving that into ezbak's staging would archive the file.
    """
    # Given a worker whose output is a link to another file
    source = tmp_path / "data" / "app.db"
    make_db(source, rows=3, wal=False)
    other = tmp_path / "other.db"
    make_db(other, rows=9, wal=False)

    def _plant_link(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        worker_dest = Path(command[-1])
        if planted == "symlink":
            worker_dest.symlink_to(other)
        else:
            worker_dest.hardlink_to(other)
        return subprocess.CompletedProcess(command, returncode=0, stdout="copied", stderr="")

    monkeypatch.setattr(subprocess, "run", _plant_link)
    dest = tmp_path / "snap" / "app.db"

    # When snapshotting it
    # Then the output is refused and nothing is left in staging
    with pytest.raises(SqliteSnapshotError, match="one link"):
        snapshot_database(source, dest=dest)
    assert not dest.exists()
    assert row_count(other) == 9


@only_as_root
@pytest.mark.usefixtures("others_can_traverse_tmp_path")
def test_snapshot_worker_cannot_read_what_the_owner_cannot(tmp_path):
    """Verify a path redirected after the owner lookup reaches only what the owner can read.

    The service can swap a directory for a symlink between the lookup and the worker's open.
    The worker then opens the redirected path with the service's own privileges.
    """
    # Given a root-only database, and a worker for an unprivileged owner whose path leads to it
    secret = tmp_path / "secret" / "root.db"
    make_db(secret, rows=3, wal=False)
    secret.parent.chmod(0o700)
    owner = ezbak_sqlite._FileOwner(uid=NOBODY_UID, gid=NOBODY_UID)
    dest = tmp_path / "snap" / "root.db"
    dest.parent.mkdir()

    # When the worker copies it
    # Then the copy fails on the owner's permissions
    with pytest.raises(SqliteSnapshotError, match="unable to open database file"):
        ezbak_sqlite._copy_live_database_as(owner, source=secret, dest=dest)


def test_snapshot_database_preserves_the_source_permissions(tmp_path):
    """Verify a private database does not become world-readable through its snapshot.

    The archive records the snapshot's mode, not the live file's, and SQLite creates the
    snapshot with default permissions.
    """
    # Given a database readable only by its owner
    source = tmp_path / "private.db"
    make_db(source, rows=3)
    source.chmod(0o600)
    dest = tmp_path / "snap" / "private.db"

    # When snapshotting it
    snapshot_database(source, dest=dest)

    # Then the snapshot carries the same permissions
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600


def test_snapshot_database_fails_when_the_lock_wait_times_out(tmp_path, monkeypatch):
    """Verify a database that cannot be locked fails instead of waiting forever.

    `Connection.backup()` retries SQLITE_BUSY with no bound of its own, so one long write
    transaction would otherwise wedge a scheduled backup indefinitely.
    """
    # Given a database and a lock wait that has already expired
    source = tmp_path / "live.db"
    make_db(source, rows=3)
    monkeypatch.setattr(ezbak_sqlite, "SQLITE_BACKUP_LOCK_TIMEOUT_SECONDS", -1.0)

    # When snapshotting it
    # Then the wait is abandoned and reported as a snapshot failure
    with pytest.raises(SqliteSnapshotError, match="holding a write lock"):
        snapshot_database(source, dest=tmp_path / "snap" / "live.db")


def test_sidecar_paths_covers_wal_shm_and_journal(tmp_path):
    """Verify every journal sibling SQLite may leave behind is named."""
    # Given a database path
    db = tmp_path / "gitea.db"

    # When listing its sidecars
    result = [p.name for p in sidecar_paths(db)]

    # Then all three journal forms are covered
    assert result == ["gitea.db-wal", "gitea.db-shm", "gitea.db-journal"]


@pytest.mark.parametrize(
    ("database", "sources", "expected"),
    [
        pytest.param("/data/gitea/gitea.db", ["/data", "/other"], ["/data"], id="enclosing"),
        pytest.param(
            "/data/gitea/gitea.db",
            ["/data", "/data/gitea"],
            ["/data", "/data/gitea"],
            id="nested-sources",
        ),
        pytest.param("/elsewhere/x.db", ["/data"], [], id="outside"),
    ],
)
def test_containing_source_paths_reports_every_enclosing_source(
    database: str, sources: list[str], expected: list[str]
) -> None:
    """Verify every source holding a database is reported, so callers can reject ambiguity."""
    # Given configured sources and a database path
    # When resolving the containing sources
    result = containing_source_paths(Path(database), source_paths=[Path(s) for s in sources])

    # Then exactly the enclosing sources are returned
    assert result == [Path(s) for s in expected]


@pytest.mark.parametrize(
    ("database", "sources", "strip", "expected"),
    [
        pytest.param("/data/gitea/gitea.db", ["/data"], True, "gitea/gitea.db", id="nested-strip"),
        pytest.param(
            "/data/gitea/gitea.db", ["/data"], False, "data/gitea/gitea.db", id="nested-no-strip"
        ),
        pytest.param("/data/oplog.sqlite", ["/data"], True, "oplog.sqlite", id="top-level-strip"),
        pytest.param(
            "/data/oplog.sqlite", ["/data"], False, "data/oplog.sqlite", id="top-level-no-strip"
        ),
        pytest.param(
            "/data/gitea.db", ["/data/gitea.db"], True, "gitea.db", id="file-source-strip"
        ),
        pytest.param(
            "/data/gitea.db", ["/data/gitea.db"], False, "gitea.db", id="file-source-no-strip"
        ),
        pytest.param("/data/gitea.db", ["/"], False, "data/gitea.db", id="root-source-no-strip"),
    ],
)
def test_archive_name_for_matches_the_tar_layout(database, sources, strip, expected):
    """Verify snapshots land at the archive name the live file would have had."""
    # Given a database and the sources it sits under
    # When computing its archive name
    result = archive_name_for(Path(database), source_paths=[Path(s) for s in sources], strip=strip)

    # Then it matches what _archive_source would have produced
    assert result == expected


@pytest.mark.parametrize(
    ("database", "sources"),
    [
        pytest.param("/elsewhere/x.db", ["/data"], id="outside-every-source"),
        pytest.param("/data/gitea/gitea.db", ["/data", "/data/gitea"], id="overlapping-sources"),
    ],
)
def test_archive_name_for_rejects_an_unplaceable_database(
    database: str, sources: list[str]
) -> None:
    """Verify a database with no single enclosing source is a hard error."""
    # Given a database outside every source, or inside two of them
    # When computing its archive name
    # Then it fails rather than guessing a location
    with pytest.raises(SqliteSnapshotError):
        archive_name_for(Path(database), source_paths=[Path(s) for s in sources], strip=True)


def test_snapshot_databases_returns_a_snapshot_per_database(tmp_path):
    """Verify every configured database is snapshotted and placed."""
    # Given two databases inside one source tree
    source = tmp_path / "data"
    make_db(source / "oplog.sqlite", rows=3)
    make_db(source / "tasklogs" / "logs.sqlite", rows=4)
    dest_dir = tmp_path / "snaps"

    # When snapshotting both
    result = snapshot_databases(
        [source / "oplog.sqlite", source / "tasklogs" / "logs.sqlite"],
        source_paths=[source],
        strip=True,
        dest_dir=dest_dir,
    )

    # Then each is copied and named for its position in the archive
    assert [s.arcname for s in result] == ["oplog.sqlite", "tasklogs/logs.sqlite"]
    assert row_count(result[0].snapshot) == 3
    assert row_count(result[1].snapshot) == 4


def test_snapshot_databases_skips_a_missing_path(tmp_path):
    """Verify an absent database is skipped rather than failing the run."""
    # Given one real database and one path a service has not created yet
    source = tmp_path / "data"
    make_db(source / "oplog.sqlite")
    dest_dir = tmp_path / "snaps"

    # When snapshotting both
    result = snapshot_databases(
        [source / "oplog.sqlite", source / "tasklogs" / "logs.sqlite"],
        source_paths=[source],
        strip=True,
        dest_dir=dest_dir,
    )

    # Then only the existing database is snapshotted
    assert len(result) == 1
    assert result[0].source == source / "oplog.sqlite"


def test_snapshot_databases_skips_a_symlinked_path(tmp_path):
    """Verify a symlinked database is skipped, matching how the source walk treats symlinks."""
    # Given a configured path that links to a database outside every source path
    outside = tmp_path / "outside"
    make_db(outside / "real.db", rows=3)
    source = tmp_path / "data"
    source.mkdir()
    (source / "link.db").symlink_to(outside / "real.db")

    # When snapshotting it
    result = snapshot_databases(
        [source / "link.db"],
        source_paths=[source],
        strip=True,
        dest_dir=tmp_path / "snaps",
    )

    # Then nothing is copied out of the linked target
    assert result == []
    assert not (tmp_path / "snaps").exists()


@pytest.mark.parametrize(
    ("link", "database"),
    [
        ("data/current", "data/current/app.db"),
        ("data", "data/app.db"),
    ],
    ids=["inside-source", "source-itself"],
)
def test_snapshot_databases_skips_a_database_behind_a_symlinked_directory(tmp_path, link, database):
    """Verify a database reached through any symlinked directory is skipped.

    The archive walk never follows a symlink and a source path containing one is refused, so
    snapshotting through it would archive content from outside every source path.
    """
    # Given a database reached through a symlinked directory
    outside = tmp_path / "releases" / "v2"
    make_db(outside / "app.db", rows=3)
    (tmp_path / link).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / link).symlink_to(outside)

    # When snapshotting it
    result = snapshot_databases(
        [tmp_path / database],
        source_paths=[tmp_path / "data"],
        strip=True,
        dest_dir=tmp_path / "snaps",
    )

    # Then nothing is copied out of the linked directory
    assert result == []
    assert not (tmp_path / "snaps").exists()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param(b"SQLite format 3\x00rest of the header", True, id="database"),
        pytest.param(b"SQLite format 2\x00rest of the header", False, id="wrong-version"),
        pytest.param(b"just a text file", False, id="text"),
        pytest.param(b"", False, id="empty"),
    ],
)
def test_is_sqlite_database_identifies_by_header(tmp_path, content, expected):
    """Verify a file is only treated as a database when its header says so."""
    # Given a file with the given first bytes
    path = tmp_path / "candidate"
    path.write_bytes(content)

    # When identifying it
    # Then only the real database header counts
    assert is_sqlite_database(path) is expected


def test_is_sqlite_database_rejects_an_unreadable_file(tmp_path):
    """Verify a path that cannot be read is not assumed to be a database."""
    # Given a path that does not exist
    # When identifying it
    # Then it does not qualify
    assert is_sqlite_database(tmp_path / "missing.db") is False


@pytest.mark.parametrize(
    ("suffix", "header", "expected"),
    [
        pytest.param("-wal", b"\x37\x7f\x06\x82", True, id="wal-big-endian-checksums"),
        pytest.param("-wal", b"\x37\x7f\x06\x83", True, id="wal-little-endian-checksums"),
        pytest.param("-wal", b"not a wal file", False, id="wal-lookalike"),
        pytest.param("-wal", b"", False, id="wal-zeroed"),
        pytest.param("-journal", ROLLBACK_JOURNAL_MAGIC, True, id="journal"),
        pytest.param("-journal", b"plain text file", False, id="journal-lookalike"),
        pytest.param("-shm", b"anything at all", True, id="shm-has-no-magic"),
    ],
)
def test_is_sqlite_sidecar_identifies_journals_by_header(tmp_path, suffix, header, expected):
    """Verify a file is only treated as a journal when its header says so."""
    # Given a file named like a sidecar
    path = tmp_path / f"app.db{suffix}"
    path.write_bytes(header + b"\x00" * 32)

    # When identifying it
    # Then only a real header (or a -shm, which has none) counts
    assert is_sqlite_sidecar(path, suffix=suffix) is expected


def test_is_sqlite_sidecar_rejects_an_unreadable_file(tmp_path):
    """Verify a sidecar that cannot be read is left alone rather than assumed."""
    # Given a path that is not a readable file
    # When identifying it
    # Then it does not qualify
    assert is_sqlite_sidecar(tmp_path / "missing.db-wal", suffix="-wal") is False


def test_snapshot_databases_isolates_identical_filenames(tmp_path):
    """Verify same-named databases in different directories do not overwrite each other."""
    # Given two databases that share a filename
    source = tmp_path / "data"
    make_db(source / "a" / "logs.sqlite", rows=2)
    make_db(source / "b" / "logs.sqlite", rows=9)
    dest_dir = tmp_path / "snaps"

    # When snapshotting both
    result = snapshot_databases(
        [source / "a" / "logs.sqlite", source / "b" / "logs.sqlite"],
        source_paths=[source],
        strip=True,
        dest_dir=dest_dir,
    )

    # Then each snapshot is a distinct file with its own contents
    assert result[0].snapshot != result[1].snapshot
    assert row_count(result[0].snapshot) == 2
    assert row_count(result[1].snapshot) == 9


def test_snapshot_databases_propagates_a_snapshot_failure(tmp_path):
    """Verify a database that is present but unreadable aborts rather than being skipped."""
    # Given a file that is not a database
    source = tmp_path / "data"
    source.mkdir()
    (source / "broken.db").write_text("not a database")

    # When snapshotting it
    # Then the error propagates to the caller
    with pytest.raises(SqliteSnapshotError):
        snapshot_databases(
            [source / "broken.db"],
            source_paths=[source],
            strip=True,
            dest_dir=tmp_path / "snaps",
        )


def test_snapshot_databases_with_no_paths_is_a_noop(tmp_path):
    """Verify the common case of no configured databases does no work."""
    # Given no configured databases
    # When snapshotting
    result = snapshot_databases([], source_paths=[tmp_path], strip=True, dest_dir=tmp_path / "s")

    # Then nothing is produced and no destination is created
    assert result == []
    assert not (tmp_path / "s").exists()


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        pytest.param("*.db", True, id="star"),
        pytest.param("**/*.sqlite3", True, id="double-star"),
        pytest.param("app-?.db", True, id="question"),
        pytest.param("app-[0-9].db", True, id="bracket"),
        pytest.param("app.db", False, id="literal"),
    ],
)
def test_is_sqlite_path_pattern_detects_metacharacters(
    tmp_path: Path, entry: str, *, expected: bool
) -> None:
    """Verify an entry holding a glob metacharacter is read as a pattern."""
    # Given an entry that does not exist on disk
    # When classifying it
    # Then only an entry holding a metacharacter is a pattern
    assert is_sqlite_path_pattern(tmp_path / entry) is expected


def test_is_sqlite_path_pattern_prefers_an_existing_literal(tmp_path):
    """Verify a real file whose name holds a metacharacter stays a literal path."""
    # Given a database genuinely named with a bracket
    literal = tmp_path / "weird[1].db"
    make_db(literal)

    # Then the literal reading wins over the glob reading
    assert not is_sqlite_path_pattern(literal)


def test_is_sqlite_path_pattern_treats_a_broken_symlink_as_a_literal(tmp_path):
    """Verify a dangling link is a literal, so it is reported as a skipped symlink later."""
    # Given a symlink with a metacharacter in its name pointing nowhere
    link = tmp_path / "gone[1].db"
    link.symlink_to(tmp_path / "missing.db")

    # Then it is still a literal, since the path exists even though the target does not
    assert not is_sqlite_path_pattern(link)


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        pytest.param("/data/db/*.db", Path("/data/db"), id="star-file"),
        pytest.param("/data/**/app.db", Path("/data"), id="double-star-dir"),
        pytest.param("/data/*/*.db", Path("/data"), id="star-dir"),
        pytest.param("/**/*.db", None, id="root-double-star"),
        pytest.param("**/*/*.db", None, id="relative-double-star"),
    ],
)
def test_static_pattern_prefix_stops_at_the_first_globbing_component(
    pattern: str, expected: Path | None
) -> None:
    """Verify the prefix is every component before the first that globs, or None if none is static."""
    # Given a pattern
    # When computing its static prefix
    # Then it stops at the first globbing component
    assert static_pattern_prefix(Path(pattern)) == expected


def test_expand_sqlite_paths_passes_literals_through(tmp_path):
    """Verify a literal entry is untouched, including one that does not exist yet."""
    # Given a source holding one database and a literal entry for a database not yet created
    source = tmp_path / "data"
    source.mkdir()
    make_db(source / "app.db")
    missing = source / "later.db"

    # When expanding
    result = expand_sqlite_paths([source / "app.db", missing], source_paths=[source])

    # Then both literals survive, so snapshot_databases still reports the missing one
    assert result == [source / "app.db", missing]


def test_expand_sqlite_paths_expands_an_absolute_pattern(tmp_path):
    """Verify an absolute pattern resolves to every matching database."""
    # Given a directory of generated database names
    source = tmp_path / "data"
    source.mkdir()
    make_db(source / "folder.0001-nhx4yzcl.db")
    make_db(source / "folder.0002-j4dkatqn.db")
    make_db(source / "main.db")

    # When expanding an absolute pattern
    result = expand_sqlite_paths([source / "*.db"], source_paths=[source])

    # Then every database is matched, in sorted order
    assert result == [
        source / "folder.0001-nhx4yzcl.db",
        source / "folder.0002-j4dkatqn.db",
        source / "main.db",
    ]


def test_expand_sqlite_paths_anchors_a_relative_pattern_to_each_source(tmp_path):
    """Verify a relative pattern expands against every source, never the process CWD."""
    # Given two sources that each hold a database
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    make_db(first / "a.db")
    make_db(second / "b.db")

    # When expanding a relative pattern
    result = expand_sqlite_paths([Path("*.db")], source_paths=[first, second])

    # Then it matched inside both sources, in configured source order
    assert result == [first / "a.db", second / "b.db"]


def test_expand_sqlite_paths_recurses_with_double_star(tmp_path):
    """Verify ** reaches databases nested below the pattern's root."""
    # Given databases at two depths
    source = tmp_path / "data"
    (source / "nested" / "deep").mkdir(parents=True)
    make_db(source / "top.db")
    make_db(source / "nested" / "deep" / "bottom.db")

    # When expanding a recursive pattern
    result = expand_sqlite_paths([source / "**" / "*.db"], source_paths=[source])

    # Then both depths are matched
    assert set(result) == {source / "top.db", source / "nested" / "deep" / "bottom.db"}


def test_expand_sqlite_paths_drops_journal_siblings(tmp_path):
    """Verify a broad pattern never returns a wal, shm, or journal file."""
    # Given a database with journal siblings beside it
    source = tmp_path / "data"
    source.mkdir()
    make_db(source / "app.db")
    (source / "app.db-wal").write_bytes(b"\x37\x7f\x06\x82" + b"\x00" * 28)
    (source / "app.db-shm").write_bytes(b"\x00" * 32)
    (source / "app.db-journal").write_bytes(ROLLBACK_JOURNAL_MAGIC)

    # When expanding a pattern broad enough to match them
    result = expand_sqlite_paths([source / "app.db*"], source_paths=[source])

    # Then only the database itself survives
    assert result == [source / "app.db"]


def test_expand_sqlite_paths_drops_non_databases(tmp_path):
    """Verify a match that is not a database is left to the ordinary file walk."""
    # Given a text file wearing a database extension, and a real database
    source = tmp_path / "data"
    source.mkdir()
    make_db(source / "real.db")
    (source / "notes.db").write_text("not a database")
    (source / "subdir.db").mkdir()

    # When expanding
    result = expand_sqlite_paths([source / "*.db"], source_paths=[source])

    # Then only the real database is returned
    assert result == [source / "real.db"]


def test_expand_sqlite_paths_drops_matches_outside_sources(tmp_path):
    """Verify a pattern reaching past every source path yields nothing for those matches."""
    # Given a database outside the configured source
    source = tmp_path / "data"
    outside = tmp_path / "outside"
    source.mkdir()
    outside.mkdir()
    make_db(source / "inside.db")
    make_db(outside / "stray.db")

    # When expanding a pattern that spans both directories
    result = expand_sqlite_paths([tmp_path / "*" / "*.db"], source_paths=[source])

    # Then only the match inside the source survives
    assert result == [source / "inside.db"]


def test_expand_sqlite_paths_dedupes_overlapping_patterns(tmp_path):
    """Verify a database matched twice is snapshotted once."""
    # Given one database matched by two patterns
    source = tmp_path / "data"
    source.mkdir()
    make_db(source / "app.db")

    # When expanding both
    result = expand_sqlite_paths([source / "*.db", source / "app.*"], source_paths=[source])

    # Then it appears once
    assert result == [source / "app.db"]


def test_expand_sqlite_paths_warns_when_a_pattern_matches_nothing(
    tmp_path: Path, log_messages: list[str]
) -> None:
    """Verify a pattern matching nothing warns rather than failing the run."""
    # Given a source with no databases
    source = tmp_path / "data"
    source.mkdir()

    # When expanding a pattern that matches nothing
    result = expand_sqlite_paths([source / "*.db"], source_paths=[source])

    # Then nothing is returned and the operator is told
    assert result == []
    assert any("matched no sqlite databases" in message for message in log_messages)


def test_expand_sqlite_paths_preserves_order_across_literals_and_patterns(tmp_path):
    """Verify a list mixing literal and pattern entries expands in configured order."""
    # Given a literal, a pattern matching two databases, and another literal, in that order
    source = tmp_path / "data"
    source.mkdir()
    make_db(source / "first.db")
    make_db(source / "shard-a.db")
    make_db(source / "shard-b.db")
    make_db(source / "last.db")

    # When expanding the mixed list
    result = expand_sqlite_paths(
        [source / "first.db", source / "shard-*.db", source / "last.db"],
        source_paths=[source],
    )

    # Then each literal keeps its configured position, with the pattern's matches
    # inserted in its place
    assert result == [
        source / "first.db",
        source / "shard-a.db",
        source / "shard-b.db",
        source / "last.db",
    ]


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        pytest.param("/data/**.db", "**.db", id="glued-suffix"),
        pytest.param("a**b/*.db", "a**b", id="glued-both"),
        pytest.param("/data/**/*.db", None, id="valid-recursive"),
        pytest.param("/data/*.db", None, id="valid-star"),
    ],
)
def test_unusable_pattern_component_flags_a_partial_double_star(
    pattern: str, expected: str | None
) -> None:
    """Verify a '**' glued to other characters is reported, since globbing it is unreliable."""
    # Given a pattern
    # When checking it for a misused recursive wildcard
    # Then only the offending component is named
    assert unusable_pattern_component(Path(pattern)) == expected


def test_expand_sqlite_paths_skips_a_fifo_without_opening_it(tmp_path):
    """Verify a fifo match is dropped by stat, since reading its header would block forever."""
    # Given a fifo sharing the pattern's extension, beside a real database
    source = tmp_path / "data"
    source.mkdir()
    make_db(source / "real.db")
    os.mkfifo(source / "pipe.db")

    # When expanding a pattern that matches both
    result = expand_sqlite_paths([source / "*.db"], source_paths=[source])

    # Then only the database is returned, and the call did not hang on the fifo
    assert result == [source / "real.db"]
