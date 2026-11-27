"""Tests for the merged EZBak core class."""

import os
import sqlite3
import tarfile
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from pytest_mock import MockerFixture

from ezbak.config import BackupConfig
from ezbak.constants import RestoreOutcome, StorageType
from ezbak.core import EZBak, _FsyncTarFile, ezbak
from ezbak.exceptions import BackupFailedError, ConfigurationError
from tests.helpers import (
    SEEDED_MARKER,
    make_db,
    row_count,
    seed_backups,
    values,
    write_uncheckpointed,
)

JOURNAL_SUFFIXES = ("-wal", "-shm", "-journal")


def _file_members(archive: Path) -> list[str]:
    """Return the file member names of `archive`."""
    with tarfile.open(archive) as tar:
        return [member.name for member in tar.getmembers() if member.isfile()]


def _archive_file_members(dest: Path) -> list[str]:
    """Return the file member names of the single archive found in `dest`."""
    return _file_members(next(dest.glob("*.tgz")))


def _journals(names: list[str]) -> list[str]:
    """Return the names that are SQLite journal siblings."""
    return [name for name in names if name.endswith(JOURNAL_SUFFIXES)]


@pytest.fixture
def data_tree(tmp_path: Path) -> tuple[Path, Path]:
    """Provide an empty source directory and an empty storage directory.

    Returns:
        tuple[Path, Path]: The source directory and the storage directory.
    """
    src = tmp_path / "data"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    return src, dest


@pytest.fixture
def make_sqlite_app(data_tree: tuple[Path, Path]) -> Callable[..., EZBak]:
    """Provide a factory for an app backing up `data_tree` with stripped source paths.

    Returns:
        Callable[..., EZBak]: Build an app from config overrides, such as `sqlite_paths`.
    """
    src, dest = data_tree

    def _make(**overrides: Any) -> EZBak:  # ruff:ignore[any-type]
        options: dict[str, Any] = {
            "name": "test",
            "source_paths": [src],
            "storage_paths": [dest],
            "strip_source_paths": True,
        }
        return ezbak(**{**options, **overrides})

    return _make


@pytest.fixture
def small_fsync_interval(mocker: MockerFixture) -> MagicMock:
    """Shrink the extraction fsync interval so a small file spans several intervals.

    Returns:
        MagicMock: A spy on `os.fsync`.
    """
    mocker.patch.object(_FsyncTarFile, "fsync_interval", 4 * 1024)
    mocker.patch.object(_FsyncTarFile, "chunk_size", 1024)
    return mocker.spy(os, "fsync")


@pytest.mark.parametrize(
    "foreign_name",
    ["giteasave-20260710T082850.tgz", "gitea-manual.tgz"],
    ids=["shares-name-prefix", "no-timestamp"],
)
def test_index_skips_files_that_are_not_backups_of_the_set(tmp_path, foreign_name):
    """Verify a file that is not one of the set's backups never becomes a backup or breaks indexing."""
    # Given a 'gitea' backup beside a foreign archive in the same storage location
    seed_backups(tmp_path, ["gitea-20260709T131941.tgz", foreign_name])
    app = ezbak(name="gitea", source_paths=[tmp_path], storage_paths=[tmp_path])

    # When listing backups and selecting the latest
    names = {b.name for b in app.list_backups()}

    # Then only the real backup is indexed and selected
    assert names == {"gitea-20260709T131941.tgz"}
    latest = app.get_latest_backup()
    assert latest is not None
    assert latest.name == "gitea-20260709T131941.tgz"


def test_backends_local_only_from_storage_paths(filesystem):
    """Verify only a local backend is built when only storage_paths are set."""
    # Given a config with local destinations and no bucket
    src, dest1, _ = filesystem
    app = ezbak(name="t", source_paths=[src], storage_paths=[dest1])

    # When inspecting derived backends
    types = {b.storage_type for b in app.backends}

    # Then only the local backend exists
    assert types == {StorageType.LOCAL}


def test_no_destination_is_rejected(filesystem):
    """Verify a config with neither storage_paths nor a bucket is invalid."""
    # Given no destination at all
    src, _, _ = filesystem

    # When constructing the config
    # Then validation fails
    with pytest.raises(ValidationError):
        ezbak(name="t", source_paths=[src])


@pytest.mark.parametrize(
    ("timestamps", "as_of", "expected"),
    [
        (
            ["20250101T120000", "20250102T090000", "20250103T090000"],
            "20250102",
            "test-20250102T090000.tgz",
        ),
        (["20250102T090000", "20250102T090001"], "20250102T090000", "test-20250102T090000.tgz"),
        (["20250102T090000"], "2024", None),
        ([], "20250102", None),
        (["20250630T235900", "20250701T000100"], "202506", "test-20250630T235900.tgz"),
        (["20250102T095959", "20250102T100000"], "20250102T09", "test-20250102T095959.tgz"),
        (["20250102T093059", "20250102T093100"], "20250102T0930", "test-20250102T093059.tgz"),
    ],
    ids=[
        "day-picks-newest-older",
        "exact-second",
        "before-all-returns-none",
        "empty-returns-none",
        "month-boundary",
        "hour-boundary",
        "minute-boundary",
    ],
)
def test_get_backup_as_of_selects_newest_at_or_before(tmp_path, timestamps, as_of, expected):
    """Verify the newest backup at or before the end of the named period is selected."""
    # Given backups at the listed timestamps
    seed_backups(tmp_path, [f"test-{ts}.tgz" for ts in timestamps])
    app = ezbak(name="test", source_paths=[tmp_path], storage_paths=[tmp_path])

    # When selecting as of the given point in time
    selected = app.get_backup_as_of(as_of)

    # Then the expected backup, or none, is returned
    assert (selected.name if selected else None) == expected


@pytest.mark.parametrize(
    "as_of",
    ["2025-01-02", "202513", "9999", "20250101\n"],
    ids=["wrong-shape", "month-out-of-range", "year-overflow", "trailing-newline"],
)
def test_get_backup_as_of_malformed_raises(tmp_path, as_of):
    """Verify a malformed point in time raises ConfigurationError."""
    # Given any configured app
    app = ezbak(name="test", source_paths=[tmp_path], storage_paths=[tmp_path])

    # When passing a malformed value
    # Then a ConfigurationError is raised
    with pytest.raises(ConfigurationError):
        app.get_backup_as_of(as_of)


@pytest.mark.parametrize(
    ("restore_date", "explicit", "expected"),
    [
        (None, "test-20250101T120000.tgz", "test-20250101T120000.tgz"),
        ("20250102", None, "test-20250102T090000.tgz"),
        ("20250102", "test-20250101T120000.tgz", "test-20250101T120000.tgz"),
        ("   ", None, "test-20250103T090000.tgz"),
    ],
    ids=[
        "explicit-backup",
        "restore-date",
        "explicit-overrides-restore-date",
        "blank-restore-date-uses-latest",
    ],
)
def test_restore_backup_selects_backup(tmp_path, restore_dir, restore_date, explicit, expected):
    """Verify the restored backup is the explicit one, else the restore_date match, else the latest."""
    # Given three backups and an app with the given restore_date
    seed_backups(
        tmp_path,
        ["test-20250101T120000.tgz", "test-20250102T090000.tgz", "test-20250103T090000.tgz"],
    )
    app = ezbak(
        name="test", source_paths=[tmp_path], storage_paths=[tmp_path], restore_date=restore_date
    )
    backup = next(b for b in app.list_backups() if b.name == explicit) if explicit else None

    # When restoring
    result = app.restore_backup(restore_dir, backup=backup)

    # Then the expected backup is the one restored
    assert result is RestoreOutcome.RESTORED
    assert (restore_dir / SEEDED_MARKER).read_text() == expected


def test_restore_backup_restore_date_unresolvable_returns_no_backup(tmp_path, restore_dir):
    """Verify an unresolvable restore_date fails instead of restoring the latest."""
    # Given a backup and a restore_date before it
    seed_backups(tmp_path, ["test-20250102T090000.tgz"])
    app = ezbak(name="test", source_paths=[tmp_path], storage_paths=[tmp_path], restore_date="2024")

    # When restoring
    result = app.restore_backup(restore_dir)

    # Then it fails and never restores the newest backup
    assert result is RestoreOutcome.NO_BACKUP
    assert not any(restore_dir.iterdir())


@pytest.mark.parametrize(
    ("planted", "exclude_regex"),
    [("dir1/skipme.log", "skipme"), ("dir1/.DS_Store", None), (".DS_Store", None)],
    ids=["exclude-regex-nested", "noise-name-nested", "noise-name-top-level"],
)
def test_create_backup_excludes_files(filesystem, planted, exclude_regex):
    """Verify excluded and always-excluded files stay out of the archive at any depth."""
    # Given a source tree holding a file that must be excluded
    src, dest1, _ = filesystem
    (src / planted).write_text("excluded")
    app = ezbak(name="test", source_paths=[src], storage_paths=[dest1], exclude_regex=exclude_regex)

    # When a backup is created
    app.create_backup()

    # Then the archive holds every other file and not the excluded one
    expected = {
        f"{src.name}/{path.relative_to(src).as_posix()}"
        for path in src.rglob("*")
        if path.is_file() and path != src / planted
    }
    assert set(_archive_file_members(dest1)) == expected


def test_create_backup_has_no_duplicate_members(filesystem):
    """Verify each file is archived once, not duplicated by recursive adds."""
    # Given a source tree with files nested in a subdirectory
    src, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src], storage_paths=[dest1])

    # When a backup is created
    app.create_backup()

    # Then no archive member appears more than once
    members = _archive_file_members(dest1)
    assert len(members) == len(set(members))


def test_fsync_tarfile_fsyncs_large_members(tmp_path, small_fsync_interval):
    """Verify extraction fsyncs a member larger than the flush interval."""
    # Given an archive containing a member spanning multiple fsync intervals
    payload = bytes(range(256)) * 40
    src = tmp_path / "big.bin"
    src.write_bytes(payload)
    archive_path = tmp_path / "archive.tgz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(src, arcname="big.bin")
    staging = tmp_path / "staging"
    staging.mkdir()

    # When extracting with the fsync-aware tarfile
    with _FsyncTarFile.open(archive_path) as tar:
        tar.extractall(path=staging, filter="data")

    # Then fsync fired at interval boundaries and the content is intact
    assert small_fsync_interval.call_count >= 2
    assert (staging / "big.bin").read_bytes() == payload


@pytest.mark.parametrize("keep_sidecar", [True, False], ids=["checksum-verified", "no-sidecar"])
def test_restore_backup_fsyncs_large_files(
    filesystem, restore_dir, small_fsync_interval, keep_sidecar
):
    """Verify a restore fsyncs large extracted files whether or not a checksum sidecar exists."""
    # Given a backup containing a file larger than the flush interval
    src, dest1, _ = filesystem
    payload = bytes(range(256)) * 40
    (src / "big.bin").write_bytes(payload)
    app = ezbak(name="test", source_paths=[src], storage_paths=[dest1])
    app.create_backup()
    if not keep_sidecar:
        for sidecar in dest1.glob("*.sha256"):
            sidecar.unlink()
    small_fsync_interval.reset_mock()

    # When restoring
    result = app.restore_backup(restore_path=restore_dir)

    # Then extraction fsynced and the file round-tripped
    assert result is RestoreOutcome.RESTORED
    assert small_fsync_interval.call_count >= 2
    assert next(restore_dir.rglob("big.bin")).read_bytes() == payload


@pytest.mark.parametrize("pattern", ["app.db", "*.db"], ids=["literal-path", "glob-pattern"])
def test_create_backup_substitutes_a_sqlite_snapshot(tmp_path, data_tree, make_sqlite_app, pattern):
    """Verify the archive holds a consistent snapshot rather than the live database."""
    # Given a source tree with two databases, one with an uncommitted write in flight
    src, dest = data_tree
    (src / "plain.txt").write_text("hello")
    make_db(src / "app.db", rows=5)
    make_db(src / "other.db", rows=3)
    app = make_sqlite_app(sqlite_paths=[src / pattern])

    with closing(sqlite3.connect(src / "app.db")) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO t (v) VALUES ('uncommitted')")

        # When creating a backup
        app.create_backup()

    # Then the archive holds the databases and the ordinary file, but no journal siblings
    names = _archive_file_members(dest)
    assert {"app.db", "other.db", "plain.txt"} <= set(names)
    assert not _journals(names)

    # And the archived database is the committed state, without the in-flight write
    extracted = tmp_path / "extracted"
    with tarfile.open(next(dest.glob("*.tgz"))) as tar:
        tar.extractall(path=extracted, filter="data")
    assert row_count(extracted / "app.db") == 5


@pytest.mark.parametrize("pattern", ["two.db", "*.db"], ids=["literal-path", "glob-pattern"])
def test_create_backup_restores_a_usable_sqlite_database(
    data_tree, make_sqlite_app, restore_dir, pattern
):
    """Verify the snapshot round-trips through restore and opens with its rows intact."""
    # Given two databases, one left with an abandoned writer's WAL
    src, _ = data_tree
    make_db(src / "one.db", rows=4)
    make_db(src / "two.db", rows=7)
    write_uncheckpointed(src / "two.db", "abandoned-write")
    app = make_sqlite_app(sqlite_paths=[src / pattern], restore_path=restore_dir)
    app.create_backup()

    # When restoring
    app.restore_backup()

    # Then both open, and the abandoned writer's committed value survived the snapshot
    assert row_count(restore_dir / "one.db") == 4
    assert values(restore_dir / "two.db") == ["abandoned-write"]


def test_create_backup_fails_when_a_sqlite_snapshot_fails(data_tree, make_sqlite_app):
    """Verify a corrupt or unreadable database aborts the run instead of shipping."""
    # Given a configured sqlite path that is not a database
    src, dest = data_tree
    (src / "app.db").write_text("not a database")
    app = make_sqlite_app(sqlite_paths=[src / "app.db"])

    # When creating a backup
    # Then it fails and writes no archive
    with pytest.raises(BackupFailedError):
        app.create_backup()

    assert not list(dest.glob("*.tgz"))


def test_create_backup_removes_staged_sqlite_snapshots(data_tree, make_sqlite_app):
    """Verify snapshots do not accumulate in temp between scheduled runs."""
    # Given a source tree with a database
    src, _ = data_tree
    make_db(src / "app.db", rows=3)
    app = make_sqlite_app(sqlite_paths=[src / "app.db"])

    # When creating two backups in a row
    app.create_backup()
    app.create_backup()

    # Then no snapshot staging directory is left behind
    assert not list(app.tmp_dir.glob("sqlite-*"))


def test_create_backup_places_a_snapshot_without_stripping(data_tree, make_sqlite_app, restore_dir):
    """Verify a nested database lands at its live position under the default layout."""
    # Given a source tree with a database in a subdirectory, backed up without stripping
    src, dest = data_tree
    (src / "sub").mkdir()
    (src / "keep.txt").write_text("keep")
    make_db(src / "sub" / "app.db", rows=7)
    app = make_sqlite_app(
        sqlite_paths=[src / "sub" / "app.db"], strip_source_paths=False, restore_path=restore_dir
    )

    # When creating a backup
    app.create_backup()

    # Then the snapshot sits where the live database would have, and only once
    assert sorted(_archive_file_members(dest)) == ["data/keep.txt", "data/sub/app.db"]

    # And it restores to that path with every row
    app.restore_backup()

    assert row_count(restore_dir / "data" / "sub" / "app.db") == 7


@pytest.mark.parametrize("strip", [True, False], ids=["stripped", "unstripped"])
def test_create_backup_when_the_source_is_the_database(data_tree, make_sqlite_app, strip):
    """Verify a source that is itself a database is archived once, as the snapshot."""
    # Given a database configured as both the source path and the sqlite path
    src, dest = data_tree
    db = src / "app.db"
    make_db(db, rows=6)
    app = make_sqlite_app(source_paths=[db], sqlite_paths=[db], strip_source_paths=strip)

    # When creating a backup
    app.create_backup()

    # Then the archive holds one member, the snapshot under the database's own name
    assert _archive_file_members(dest) == ["app.db"]


def test_create_backup_deduplicates_repeated_sqlite_paths(data_tree, make_sqlite_app):
    """Verify a database listed twice is not archived twice."""
    # Given the same database configured twice
    src, dest = data_tree
    make_db(src / "app.db", rows=2)
    app = make_sqlite_app(sqlite_paths=[src / "app.db", src / "app.db"])

    # When creating a backup
    app.create_backup()

    # Then it appears once
    assert _archive_file_members(dest) == ["app.db"]


def test_create_backup_skips_a_symlinked_sqlite_path(tmp_path, data_tree, make_sqlite_app):
    """Verify a symlinked database is skipped rather than copied in from outside the source."""
    # Given a sqlite path that is a symlink to a database outside every source path
    src, dest = data_tree
    outside = tmp_path / "outside"
    make_db(outside / "real.db", rows=3)
    (src / "keep.txt").write_text("keep")
    (src / "link.db").symlink_to(outside / "real.db")
    app = make_sqlite_app(sqlite_paths=[src / "link.db"])

    # When creating a backup
    app.create_backup()

    # Then the archive holds no content from outside the source tree
    assert _archive_file_members(dest) == ["keep.txt"]


def test_create_backup_does_not_snapshot_a_filtered_database(data_tree, make_sqlite_app):
    """Verify a database the filters drop is never copied, and its journals stay out too."""
    # Given a source tree whose database and stale journal are both present, with an exclude
    # regex that matches only the database
    src, dest = data_tree
    (src / "keep.txt").write_text("keep")
    make_db(src / "app.db", rows=5)
    (src / "app.db-wal").write_bytes(b"\x37\x7f\x06\x82" + b"\x00" * 28)
    app = make_sqlite_app(sqlite_paths=[src / "app.db"], exclude_regex=r"app\.db$")

    # When creating a backup
    app.create_backup()

    # Then neither the database nor its journal was archived
    assert _archive_file_members(dest) == ["keep.txt"]


def test_archive_name_filter_excludes_a_live_database_under_a_root_source(tmp_path):
    """Verify the exclusion set is built without a leading slash for a source at the root."""
    # Given a backup whose exclusion set names a database directly under a root source path
    dest = tmp_path / "dest"
    dest.mkdir()
    backup = ezbak(name="test", source_paths=[tmp_path], storage_paths=[dest])

    # When building the archive-name filter for that source without stripping
    keep_file = backup._archive_name_filter(
        Path("/"), strip=False, excluded_paths=frozenset({Path("/app.db")})
    )

    # Then the live database is dropped under its bare name and other files are kept
    assert not keep_file("app.db")
    assert keep_file("notes.txt")


def test_restore_clears_a_stale_wal_at_the_destination(data_tree, make_sqlite_app, restore_dir):
    """Verify a previous deployment's uncheckpointed -wal cannot roll a restore back.

    The archive carries no journal files, so a `-wal` already sitting in the restore target
    would be replayed over the restored database, silently reverting it to older rows.
    """
    # Given a restore target holding a database and the -wal a hard-killed run left behind
    src, _ = data_tree
    write_uncheckpointed(restore_dir / "app.db", "v1-old")
    assert (restore_dir / "app.db-wal").exists(), "expected a stale -wal at the restore target"

    # And a live service whose current state is newer
    write_uncheckpointed(src / "app.db", "v2-current")
    app = make_sqlite_app(sqlite_paths=[src / "app.db"], restore_path=restore_dir)
    app.create_backup()

    # When restoring over the populated target
    result = app.restore_backup()

    # Then the stale journal is gone and the restored database holds the current rows
    assert result is RestoreOutcome.RESTORED
    assert not (restore_dir / "app.db-wal").exists()
    assert not (restore_dir / "app.db-shm").exists()
    assert values(restore_dir / "app.db") == ["v2-current"]


@pytest.mark.parametrize(
    ("lookalike", "content"),
    [("notes-wal", b"not a sqlite journal"), ("notes-shm", b"unrelated shared memory")],
    ids=["wal-named-text-file", "shm-beside-non-database"],
)
def test_restore_keeps_a_file_only_named_like_a_journal(
    data_tree, make_sqlite_app, restore_dir, lookalike, content
):
    """Verify a journal-named file is kept unless the file it sits beside is really a database.

    A `-shm` carries no identifying header, so the restored base file is what rules the
    removal in or out.
    """
    # Given a restore target holding a journal lookalike beside a file the archive replaces
    src, _ = data_tree
    (src / "notes").write_text("new notes")
    (restore_dir / "notes").write_text("old notes")
    (restore_dir / lookalike).write_bytes(content)
    app = make_sqlite_app(restore_path=restore_dir)
    app.create_backup()

    # When restoring over it
    app.restore_backup()

    # Then the lookalike survives untouched and the restore still happened
    assert (restore_dir / lookalike).read_bytes() == content
    assert (restore_dir / "notes").read_text() == "new notes"


def test_restore_keeps_a_journal_the_archive_supplies(data_tree, make_sqlite_app, restore_dir):
    """Verify a journal that came from the archive is restored rather than deleted."""
    # Given an archive holding a database and a real -wal beside it, taken with no
    # sqlite_paths so the journals are archived as ordinary files
    src, _ = data_tree
    make_db(src / "app.db", rows=2)
    (src / "app.db-wal").write_bytes(b"\x37\x7f\x06\x82" + b"\x00" * 28)
    app = make_sqlite_app(restore_path=restore_dir)
    app.create_backup()

    # When restoring
    app.restore_backup()

    # Then the archived journal lands beside its database
    assert (restore_dir / "app.db-wal").read_bytes() == b"\x37\x7f\x06\x82" + b"\x00" * 28


def test_create_backup_pattern_picks_up_a_database_added_between_runs(data_tree, make_sqlite_app):
    """Verify expansion happens per run, so a cron sidecar sees new shards."""
    # Given one database and a backup instance configured with a pattern
    src, dest = data_tree
    make_db(src / "first.db")
    app = make_sqlite_app(sqlite_paths=[src / "*.db"])
    app.create_backup()

    # When a second database appears with an abandoned writer's WAL and another backup
    # runs on the same instance
    write_uncheckpointed(src / "second.db", "abandoned-write")
    app.create_backup()

    # Then some archive holds both databases, but the ordinary source walk alone would
    # do that much regardless of pattern expansion. What only a fresh expansion produces
    # is a snapshot of second.db, so the real check is the absence of its journal siblings.
    archives = [_file_members(archive) for archive in dest.glob("*.tgz")]
    matching = next(
        (members for members in archives if {"first.db", "second.db"} <= set(members)), None
    )
    assert matching is not None
    assert not _journals(matching)


def test_create_backup_pattern_leaves_a_non_database_to_the_file_walk(data_tree, make_sqlite_app):
    """Verify a text file wearing a .db extension is archived rather than failing the run."""
    # Given a real database with an abandoned writer's WAL, and an impostor sharing its extension
    src, dest = data_tree
    make_db(src / "real.db", rows=2)
    write_uncheckpointed(src / "real.db", "abandoned-write")
    (src / "notes.db").write_text("not a database")
    app = make_sqlite_app(sqlite_paths=[src / "*.db"])

    # When creating a backup
    app.create_backup()

    # Then the run succeeded, both files are present, and the snapshotted database
    # carries no journal siblings into the archive
    names = _archive_file_members(dest)
    assert {"real.db", "notes.db"} <= set(names)
    assert not _journals(names)


def test_create_backup_relative_pattern_matches_under_the_source_path(data_tree, make_sqlite_app):
    """Verify a relative pattern matches under the configured source, not the process cwd."""
    # Given a database nested below the source, with an abandoned writer's WAL
    src, dest = data_tree
    make_db(src / "nested" / "app.db", rows=3)
    write_uncheckpointed(src / "nested" / "app.db", "abandoned-write")
    app = make_sqlite_app(sqlite_paths=[Path("**/*.db")])

    # When creating a backup
    app.create_backup()

    # Then the database is archived at its live position under the source, with no
    # journal siblings beside it
    names = _archive_file_members(dest)
    assert "nested/app.db" in names
    assert not _journals(names)


def test_create_backup_reports_a_glob_failure_instead_of_raising(
    data_tree, make_sqlite_app, mocker
):
    """Verify a pattern the interpreter rejects fails the run through the normal error path."""
    # Given a working pattern config whose expansion blows up at backup time, standing in
    # for a glob Python refuses (a '**' that is not a whole component on Python < 3.13)
    src, dest = data_tree
    make_db(src / "app.db")
    app = make_sqlite_app(sqlite_paths=[src / "*.db"])
    mocker.patch(
        "ezbak.core.expand_sqlite_paths",
        side_effect=ValueError("Invalid pattern: '**' can only be an entire path component"),
    )

    # When creating a backup
    # Then it fails as an EZBakError, which the container's scheduler logs and alerts on,
    # rather than as a bare ValueError that escapes it
    with pytest.raises(BackupFailedError):
        app.create_backup()

    assert not list(dest.glob("*.tgz"))


def test_list_and_restore_without_source_paths(filesystem, tmp_path):
    """Verify listing and restoring do not require source paths."""
    # Given an app configured with no source paths (e.g. a container restore)
    _, dest1, _ = filesystem
    app = EZBak(BackupConfig(name="t", storage_paths=[dest1]))

    # When listing backups, then no error is raised for the missing source paths
    assert app.list_backups() == []

    # When restoring, then no backup is found and no "No source paths provided" error is raised
    assert app.restore_backup(restore_path=tmp_path) is RestoreOutcome.NO_BACKUP


def test_restore_empty_bucket_still_reports_no_backup(make_s3_app, restore_dir) -> None:
    """Verify a genuinely empty destination still reports NO_BACKUP, not a failure."""
    # Given: a readable but empty bucket, the real fresh-deployment case
    app = make_s3_app()

    # When restoring, then the fresh-deployment path is intact
    assert app.restore_backup(restore_path=restore_dir) is RestoreOutcome.NO_BACKUP
