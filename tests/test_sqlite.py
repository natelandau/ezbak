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
from loguru import logger

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


def test_is_sqlite_path_pattern_detects_metacharacters(tmp_path):
    """Verify an entry holding a glob metacharacter is read as a pattern."""
    # Given entries that do not exist on disk
    # Then only the ones holding a metacharacter are patterns
    assert is_sqlite_path_pattern(tmp_path / "*.db")
    assert is_sqlite_path_pattern(tmp_path / "**" / "*.sqlite3")
    assert is_sqlite_path_pattern(tmp_path / "app-?.db")
    assert is_sqlite_path_pattern(tmp_path / "app-[0-9].db")
    assert not is_sqlite_path_pattern(tmp_path / "app.db")


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


def test_static_pattern_prefix_returns_the_rooted_directory():
    """Verify the prefix is every component before the first that globs."""
    # Given absolute patterns rooted at a real directory
    # Then the prefix stops at the first globbing component
    assert static_pattern_prefix(Path("/data/db/*.db")) == Path("/data/db")
    assert static_pattern_prefix(Path("/data/**/app.db")) == Path("/data")
    assert static_pattern_prefix(Path("/data/*/*.db")) == Path("/data")


def test_static_pattern_prefix_is_none_when_the_first_component_globs():
    """Verify a pattern with nothing static to check reports no prefix."""
    # Given patterns whose first component already globs
    # Then there is no prefix worth checking for existence
    assert static_pattern_prefix(Path("/**/*.db")) is None
    assert static_pattern_prefix(Path("**/*/*.db")) is None


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
    (source / "app.db-journal").write_bytes(b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7")

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


def test_expand_sqlite_paths_warns_when_a_pattern_matches_nothing(tmp_path):
    """Verify a pattern matching nothing warns rather than failing the run."""
    # Given a source with no databases and a sink capturing warnings
    source = tmp_path / "data"
    source.mkdir()
    messages: list[str] = []
    logger.add(messages.append, level="WARNING")

    # When expanding a pattern that matches nothing
    result = expand_sqlite_paths([source / "*.db"], source_paths=[source])

    # Then nothing is returned and the operator is told
    assert result == []
    assert any("matched no sqlite databases" in message for message in messages)


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


def test_unusable_pattern_component_flags_a_partial_double_star():
    """Verify a '**' glued to other characters is reported, since globbing it is unreliable."""
    # Given patterns that misuse the recursive wildcard
    # Then the offending component is named
    assert unusable_pattern_component(Path("/data/**.db")) == "**.db"
    assert unusable_pattern_component(Path("a**b/*.db")) == "a**b"

    # And a well-formed pattern reports nothing
    assert unusable_pattern_component(Path("/data/**/*.db")) is None
    assert unusable_pattern_component(Path("/data/*.db")) is None


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
