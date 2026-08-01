"""Tests for consistent SQLite snapshots."""

import os
import sqlite3
import stat
import subprocess  # ruff:ignore[suspicious-subprocess-import]
import sys
import textwrap
from contextlib import closing
from pathlib import Path

import pytest

from ezbak import sqlite as ezbak_sqlite
from ezbak.exceptions import SqliteSnapshotError
from ezbak.sqlite import (
    archive_name_for,
    containing_source_paths,
    is_sqlite_database,
    is_sqlite_sidecar,
    sidecar_paths,
    snapshot_database,
    snapshot_databases,
)
from tests.helpers import make_db, row_count

# root bypasses the permission bits these tests rely on, so a directory stripped of write
# permission stays writable and the case under test cannot be reproduced.
skip_as_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores permission bits",
)


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


def test_snapshot_database_rejects_a_corrupt_database(tmp_path):
    """Verify a database with a damaged page fails rather than shipping silently."""
    # Given a database whose first b-tree page header has been overwritten
    source = tmp_path / "corrupt.db"
    make_db(source, rows=50, wal=False)
    data = bytearray(source.read_bytes())
    data[100:120] = b"\xff" * 20
    source.write_bytes(bytes(data))
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
    rollback journal it needs to become consistent again, so the copy restores corrupt.
    """
    # Given a DELETE-mode database whose writer was killed mid-transaction, leaving a hot
    # journal on disk
    source = tmp_path / "hot.db"
    make_db(source, rows=200, wal=False)

    # ruff:ignore[hardcoded-sql-expression]
    script = textwrap.dedent(f"""
        import os, sqlite3
        conn = sqlite3.connect({str(source)!r})
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE t SET v = 'clobbered'")
        # Abandon the process without commit or rollback, leaving the journal behind.
        os._exit(1)
    """)
    subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]
        [sys.executable, "-c", script], check=False
    )
    assert source.with_name("hot.db-journal").exists(), "expected a hot journal on disk"

    dest = tmp_path / "snap" / "hot.db"

    # When snapshotting it
    snapshot_database(source, dest=dest)

    # Then the snapshot holds the rolled-back state, not the abandoned write
    assert row_count(dest) == 200
    with closing(sqlite3.connect(dest)) as conn:
        assert conn.execute("SELECT count(*) FROM t WHERE v = 'clobbered'").fetchone()[0] == 0


@skip_as_root
def test_snapshot_database_reads_a_rollback_database_in_a_readonly_directory(
    tmp_path, readonly_dir
):
    """Verify a DELETE-mode database snapshots from a directory that cannot be written.

    A rollback-mode database needs no sidecar file to be read, so the ordinary read-write
    open succeeds and the backup never has to fall back.
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
    directory it may not write. The read-only retry runs and fails too, so the whole backup
    fails rather than archiving a database the run could not copy consistently. This is why
    a source holding WAL databases must be mounted read-write.
    """
    # Given an idle WAL database whose -wal and -shm were checkpointed away on close,
    # in a directory with no write permission
    data = tmp_path / "data"
    source = data / "live.db"
    make_db(source, rows=5)
    assert not source.with_name("live.db-shm").exists()
    readonly_dir(data)

    # When snapshotting it
    with pytest.raises(SqliteSnapshotError) as excinfo:
        snapshot_database(source, dest=tmp_path / "snap" / "live.db")

    # Then the read-only retry was attempted and reported as the failure
    assert str(excinfo.value).startswith("Failed to snapshot read-only sqlite database")


@skip_as_root
def test_snapshot_database_copies_a_readonly_database_file(tmp_path):
    """Verify a database file with no write permission snapshots when its directory is writable.

    SQLite can still create the -shm beside it, so this needs no fallback.
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


def test_snapshot_database_creates_missing_parent_directories(tmp_path):
    """Verify the snapshot destination tree is created on demand."""
    # Given a destination several levels below an existing directory
    source = tmp_path / "live.db"
    make_db(source)
    dest = tmp_path / "a" / "b" / "c" / "live.db"

    # When snapshotting
    snapshot_database(source, dest=dest)

    # Then the intermediate directories were created
    assert dest.exists()


def test_sidecar_paths_covers_wal_shm_and_journal(tmp_path):
    """Verify every journal sibling SQLite may leave behind is named."""
    # Given a database path
    db = tmp_path / "gitea.db"

    # When listing its sidecars
    result = [p.name for p in sidecar_paths(db)]

    # Then all three journal forms are covered
    assert result == ["gitea.db-wal", "gitea.db-shm", "gitea.db-journal"]


def test_containing_source_paths_finds_the_enclosing_source():
    """Verify a nested database resolves to the source directory holding it."""
    # Given two configured sources, one of which contains the database
    sources = [Path("/data"), Path("/other")]

    # When resolving the containing source
    result = containing_source_paths(Path("/data/gitea/gitea.db"), source_paths=sources)

    # Then only the enclosing source matches
    assert result == [Path("/data")]


def test_containing_source_paths_reports_nested_sources():
    """Verify overlapping sources are all reported so callers can reject the ambiguity."""
    # Given nested configured sources that both contain the database
    sources = [Path("/data"), Path("/data/gitea")]

    # When resolving the containing source
    result = containing_source_paths(Path("/data/gitea/gitea.db"), source_paths=sources)

    # Then both are returned
    assert result == [Path("/data"), Path("/data/gitea")]


def test_containing_source_paths_returns_empty_for_an_outside_path():
    """Verify a database outside every source is reported as unmatched."""
    # Given a database outside the configured sources
    # When resolving the containing source
    result = containing_source_paths(Path("/elsewhere/x.db"), source_paths=[Path("/data")])

    # Then nothing matches
    assert result == []


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


def test_archive_name_for_rejects_a_path_outside_every_source():
    """Verify a database with no enclosing source is a hard error."""
    # Given a database outside the configured sources
    # When computing its archive name
    # Then it fails rather than guessing a location
    with pytest.raises(SqliteSnapshotError, match="not inside any configured source path"):
        archive_name_for(Path("/elsewhere/x.db"), source_paths=[Path("/data")], strip=True)


def test_archive_name_for_rejects_a_path_under_overlapping_sources():
    """Verify a database inside two configured sources is rejected as ambiguous."""
    # Given nested sources that both contain the database
    # When computing its archive name
    # Then it fails rather than guessing which source wins
    with pytest.raises(SqliteSnapshotError, match="more than one configured source path"):
        archive_name_for(
            Path("/data/gitea/gitea.db"),
            source_paths=[Path("/data"), Path("/data/gitea")],
            strip=True,
        )


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


def test_snapshot_databases_skips_a_database_behind_a_symlinked_directory(tmp_path):
    """Verify a symlinked parent is skipped too, since the walk never follows one.

    Checking only the final component would archive the database at a position the live tree
    never held, holding content from outside every source path.
    """
    # Given a database reached through a symlinked directory inside the source
    outside = tmp_path / "releases" / "v2"
    make_db(outside / "app.db", rows=3)
    source = tmp_path / "data"
    source.mkdir()
    (source / "current").symlink_to(outside)

    # When snapshotting it
    result = snapshot_databases(
        [source / "current" / "app.db"],
        source_paths=[source],
        strip=True,
        dest_dir=tmp_path / "snaps",
    )

    # Then nothing is copied out of the linked directory
    assert result == []
    assert not (tmp_path / "snaps").exists()


def test_snapshot_databases_follows_a_symlinked_source_path(tmp_path):
    """Verify a symlinked source path is still walked, matching how the archive treats it."""
    # Given a source path that is itself a symlink to the directory holding the database
    real = tmp_path / "real"
    make_db(real / "app.db", rows=4)
    source = tmp_path / "data"
    source.symlink_to(real)

    # When snapshotting a database directly inside it
    result = snapshot_databases(
        [source / "app.db"],
        source_paths=[source],
        strip=True,
        dest_dir=tmp_path / "snaps",
    )

    # Then the database is snapshotted as usual
    assert [s.arcname for s in result] == ["app.db"]
    assert row_count(result[0].snapshot) == 4


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
        pytest.param("-journal", b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7", True, id="journal"),
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
