"""Tests for the merged EZBak core class."""

import os
import shutil
import sqlite3
import tarfile
from contextlib import closing
from pathlib import Path

import pytest
from pydantic import ValidationError

from ezbak import sqlite as ezbak_sqlite
from ezbak.constants import RestoreOutcome, StorageType
from ezbak.core import EZBak, _FsyncTarFile, ezbak
from ezbak.exceptions import BackupFailedError, ConfigurationError
from tests.helpers import make_db, row_count, values, write_uncheckpointed

fixture_archive_path = Path(__file__).parent / "fixtures" / "archive.tgz"


def _archive_file_members(dest: Path) -> list[str]:
    """Return the file member names of the single archive found in `dest`."""
    archive = next(dest.glob("*.tgz"))
    with tarfile.open(archive) as tar:
        return [member.name for member in tar.getmembers() if member.isfile()]


def test_ezbak_factory_returns_core(filesystem):
    """Verify the ezbak() convenience returns an EZBak instance."""
    # Given source and destination directories
    src, dest1, _ = filesystem

    # When building via the convenience factory
    app = ezbak(name="test", source_paths=[src], storage_paths=[dest1])

    # Then an EZBak core is returned with the config attached
    assert isinstance(app, EZBak)
    assert app.settings.name == "test"


def test_ezbak_create_backup_writes_archive(filesystem):
    """Verify create_backup produces a discoverable backup."""
    # Given a configured EZBak
    src, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src], storage_paths=[dest1])

    # When a backup is created
    app.create_backup()

    # Then it appears in the listing
    assert len(app.list_backups()) == 1


def test_index_excludes_names_sharing_a_prefix(tmp_path):
    """Verify a backup set only matches names followed by the '-' separator."""
    # Given a 'gitea' backup and an unrelated 'giteasave' backup with a later timestamp
    shutil.copy2(fixture_archive_path, tmp_path / "gitea-20260709T131941.tgz")
    shutil.copy2(fixture_archive_path, tmp_path / "giteasave-20260710T082850.tgz")
    app = ezbak(name="gitea", source_paths=[tmp_path], storage_paths=[tmp_path])

    # When listing backups and selecting the latest
    names = {b.name for b in app.list_backups()}

    # Then the 'giteasave' archive is not treated as a 'gitea' backup
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
    src, _, _ = filesystem
    # Given no destination at all
    # When constructing the config
    # Then validation fails
    with pytest.raises(ValidationError):
        ezbak(name="t", source_paths=[src])


def _seed_backups(directory: Path, timestamps: list[str]) -> None:
    """Copy the fixture archive into `directory` under `test-<timestamp>.tgz` names."""
    for ts in timestamps:
        shutil.copy2(fixture_archive_path, directory / f"test-{ts}.tgz")


def test_get_backup_as_of_at_or_before_picks_newest_older(tmp_path):
    """Verify the newest backup at or before the given day is selected."""
    # Given three backups across two days
    _seed_backups(tmp_path, ["20250101T120000", "20250102T090000", "20250103T090000"])
    app = ezbak(name="test", source_paths=[tmp_path], storage_paths=[tmp_path])

    # When selecting as of 2025-01-02 (end of that day)
    selected = app.get_backup_as_of("20250102")

    # Then the newest backup on or before that day is returned
    assert selected is not None
    assert selected.name == "test-20250102T090000.tgz"


def test_get_backup_as_of_exact_second_match(tmp_path):
    """Verify a full timestamp includes the backup at that exact second."""
    # Given two backups
    _seed_backups(tmp_path, ["20250102T090000", "20250102T090001"])
    app = ezbak(name="test", source_paths=[tmp_path], storage_paths=[tmp_path])

    # When selecting as of the exact second of the first backup
    selected = app.get_backup_as_of("20250102T090000")

    # Then that backup is chosen, not the later one
    assert selected is not None
    assert selected.name == "test-20250102T090000.tgz"


def test_get_backup_as_of_older_than_all_returns_none(tmp_path):
    """Verify a moment before every backup returns None."""
    # Given a single 2025 backup
    _seed_backups(tmp_path, ["20250102T090000"])
    app = ezbak(name="test", source_paths=[tmp_path], storage_paths=[tmp_path])

    # When selecting as of a year before it
    # Then nothing qualifies
    assert app.get_backup_as_of("2024") is None


def test_get_backup_as_of_empty_returns_none(tmp_path):
    """Verify an empty backup set returns None."""
    # Given no backups
    app = ezbak(name="test", source_paths=[tmp_path], storage_paths=[tmp_path])

    # When selecting any date
    # Then nothing qualifies
    assert app.get_backup_as_of("20250102") is None


def test_get_backup_as_of_month_boundary(tmp_path):
    """Verify a YYYYMM value includes the whole month."""
    # Given backups in June and July 2025
    _seed_backups(tmp_path, ["20250630T235900", "20250701T000100"])
    app = ezbak(name="test", source_paths=[tmp_path], storage_paths=[tmp_path])

    # When selecting as of June 2025
    selected = app.get_backup_as_of("202506")

    # Then the late-June backup is chosen and July is excluded
    assert selected is not None
    assert selected.name == "test-20250630T235900.tgz"


def test_get_backup_as_of_malformed_raises(tmp_path):
    """Verify a malformed date shape raises ConfigurationError."""
    # Given any configured app
    app = ezbak(name="test", source_paths=[tmp_path], storage_paths=[tmp_path])

    # When passing a value that is not a recognized shape
    # Then a ConfigurationError is raised
    with pytest.raises(ConfigurationError):
        app.get_backup_as_of("2025-01-02")


def test_get_backup_as_of_out_of_range_raises(tmp_path):
    """Verify an out-of-range field (month 13) raises ConfigurationError."""
    # Given any configured app
    app = ezbak(name="test", source_paths=[tmp_path], storage_paths=[tmp_path])

    # When passing month 13
    # Then a ConfigurationError is raised
    with pytest.raises(ConfigurationError):
        app.get_backup_as_of("202513")


def test_restore_backup_explicit_backup_arg(tmp_path, mocker):
    """Verify an explicit backup arg is restored instead of the latest."""
    # Given three backups and a restore destination
    _seed_backups(tmp_path, ["20250101T120000", "20250102T090000", "20250103T090000"])
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    app = ezbak(name="test", source_paths=[tmp_path], storage_paths=[tmp_path])
    older = next(b for b in app.list_backups() if b.name == "test-20250101T120000.tgz")
    spy = mocker.spy(app, "_do_restore")

    # When restoring that explicit (older) backup
    app.restore_backup(restore_dir, backup=older)

    # Then _do_restore received the older backup, not the latest
    assert spy.call_args.kwargs["backup"].name == "test-20250101T120000.tgz"


def test_restore_backup_uses_restore_date(tmp_path, mocker):
    """Verify a configured restore_date selects the point-in-time backup."""
    # Given backups and a config carrying a restore_date
    _seed_backups(tmp_path, ["20250101T120000", "20250102T090000", "20250103T090000"])
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    app = ezbak(
        name="test",
        source_paths=[tmp_path],
        storage_paths=[tmp_path],
        restore_date="20250102",
    )
    spy = mocker.spy(app, "_do_restore")

    # When restoring with no explicit backup
    app.restore_backup(restore_dir)

    # Then the restore_date point-in-time backup is used
    assert spy.call_args.kwargs["backup"].name == "test-20250102T090000.tgz"


def test_restore_backup_restore_date_unresolvable_returns_no_backup(tmp_path, mocker):
    """Verify an unresolvable restore_date fails instead of restoring the latest."""
    # Given a backup and a restore_date before it
    _seed_backups(tmp_path, ["20250102T090000"])
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    app = ezbak(
        name="test",
        source_paths=[tmp_path],
        storage_paths=[tmp_path],
        restore_date="2024",
    )
    spy = mocker.spy(app, "_do_restore")

    # When restoring
    result = app.restore_backup(restore_dir)

    # Then it fails and never restores the newest backup
    assert result is RestoreOutcome.NO_BACKUP
    spy.assert_not_called()


def test_get_backup_as_of_year_overflow_raises(tmp_path):
    """Verify a year whose boundary overflows PlainDateTime raises ConfigurationError."""
    # Given any configured app
    app = ezbak(name="test", source_paths=[tmp_path], storage_paths=[tmp_path])

    # When the +1-year boundary would exceed the max supported year
    # Then a clean ConfigurationError is raised, not a raw ValueError
    with pytest.raises(ConfigurationError):
        app.get_backup_as_of("9999")


def test_get_backup_as_of_trailing_newline_raises(tmp_path):
    """Verify a trailing newline is rejected as ConfigurationError, not AssertionError."""
    # Given any configured app
    app = ezbak(name="test", source_paths=[tmp_path], storage_paths=[tmp_path])

    # When the value carries a trailing newline (e.g. from EZBAK_RESTORE_DATE=$(cat file))
    # Then it is rejected cleanly rather than tripping the length assertion
    with pytest.raises(ConfigurationError):
        app.get_backup_as_of("20250101\n")


def test_restore_backup_explicit_backup_overrides_restore_date(tmp_path, mocker):
    """Verify an explicit backup arg wins over a configured restore_date."""
    # Given backups and a config whose restore_date points at a different backup
    _seed_backups(tmp_path, ["20250101T120000", "20250102T090000", "20250103T090000"])
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    app = ezbak(
        name="test",
        source_paths=[tmp_path],
        storage_paths=[tmp_path],
        restore_date="20250102",
    )
    chosen = next(b for b in app.list_backups() if b.name == "test-20250103T090000.tgz")
    spy = mocker.spy(app, "_do_restore")

    # When restoring with an explicit backup that differs from the restore_date target
    app.restore_backup(restore_dir, backup=chosen)

    # Then the explicit backup is restored, not the restore_date selection
    assert spy.call_args.kwargs["backup"].name == "test-20250103T090000.tgz"


def test_restore_backup_blank_restore_date_uses_latest(tmp_path, mocker):
    """Verify a whitespace-only restore_date falls back to the latest backup."""
    # Given backups and a blank (whitespace) restore_date
    _seed_backups(tmp_path, ["20250101T120000", "20250103T090000"])
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    app = ezbak(
        name="test",
        source_paths=[tmp_path],
        storage_paths=[tmp_path],
        restore_date="   ",
    )
    spy = mocker.spy(app, "_do_restore")

    # When restoring with no explicit backup
    result = app.restore_backup(restore_dir)

    # Then the latest backup is restored (blank treated as "no point in time requested")
    assert result is RestoreOutcome.RESTORED
    assert spy.call_args.kwargs["backup"].name == "test-20250103T090000.tgz"


def test_create_backup_exclude_regex_applies_in_subdirectories(filesystem):
    """Verify exclude_regex drops matching files nested in subdirectories."""
    # Given a source with a file to exclude nested inside a subdirectory
    src, dest1, _ = filesystem
    (src / "dir1" / "skipme.log").write_text("secret")
    app = ezbak(name="test", source_paths=[src], storage_paths=[dest1], exclude_regex="skipme")

    # When a backup is created
    app.create_backup()

    # Then the excluded file is absent from the archive
    members = _archive_file_members(dest1)
    assert not any("skipme.log" in m for m in members)


def test_create_backup_excludes_noise_names_in_subdirectories(filesystem):
    """Verify always-excluded names are dropped inside subdirectories."""
    # Given an always-excluded noise file nested inside a subdirectory
    src, dest1, _ = filesystem
    (src / "dir1" / ".DS_Store").write_text("noise")
    app = ezbak(name="test", source_paths=[src], storage_paths=[dest1])

    # When a backup is created
    app.create_backup()

    # Then the noise file is absent from the archive
    members = _archive_file_members(dest1)
    assert not any(".DS_Store" in m for m in members)


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


def test_fsync_tarfile_fsyncs_large_members(tmp_path, mocker):
    """Verify extraction fsyncs a member larger than the flush interval."""
    # Given an archive containing a member spanning multiple fsync intervals
    payload = bytes(range(256)) * 40
    src = tmp_path / "big.bin"
    src.write_bytes(payload)
    archive_path = tmp_path / "archive.tgz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(src, arcname="big.bin")
    mocker.patch.object(_FsyncTarFile, "fsync_interval", 4 * 1024)
    mocker.patch.object(_FsyncTarFile, "chunk_size", 1024)
    fsync_spy = mocker.spy(os, "fsync")
    staging = tmp_path / "staging"
    staging.mkdir()

    # When extracting with the fsync-aware tarfile
    with _FsyncTarFile.open(archive_path) as tar:
        tar.extractall(path=staging, filter="data")

    # Then fsync fired at interval boundaries and the content is intact
    assert fsync_spy.call_count >= 2
    assert (staging / "big.bin").read_bytes() == payload


def test_restore_backup_fsyncs_large_files(filesystem, tmp_path, mocker):
    """Verify a checksum-verified restore fsyncs large extracted files."""
    # Given a backup containing a file larger than the flush interval
    src, dest1, _ = filesystem
    payload = bytes(range(256)) * 40
    (src / "big.bin").write_bytes(payload)
    app = ezbak(name="test", source_paths=[src], storage_paths=[dest1])
    app.create_backup()
    mocker.patch.object(_FsyncTarFile, "fsync_interval", 4 * 1024)
    mocker.patch.object(_FsyncTarFile, "chunk_size", 1024)
    fsync_spy = mocker.spy(os, "fsync")
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()

    # When restoring
    result = app.restore_backup(restore_path=restore_dir)

    # Then extraction fsynced and the file round-tripped
    assert result is RestoreOutcome.RESTORED
    assert fsync_spy.call_count >= 2
    assert next(restore_dir.rglob("big.bin")).read_bytes() == payload


def test_restore_backup_without_checksum_fsyncs_large_files(filesystem, tmp_path, mocker):
    """Verify a restore with no checksum sidecar still fsyncs large files."""
    # Given a backup with its checksum sidecar removed
    src, dest1, _ = filesystem
    payload = bytes(range(256)) * 40
    (src / "big.bin").write_bytes(payload)
    app = ezbak(name="test", source_paths=[src], storage_paths=[dest1])
    app.create_backup()
    for sidecar in dest1.glob("*.sha256"):
        sidecar.unlink()
    mocker.patch.object(_FsyncTarFile, "fsync_interval", 4 * 1024)
    mocker.patch.object(_FsyncTarFile, "chunk_size", 1024)
    fsync_spy = mocker.spy(os, "fsync")
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()

    # When restoring
    result = app.restore_backup(restore_path=restore_dir)

    # Then extraction fsynced and the file round-tripped
    assert result is RestoreOutcome.RESTORED
    assert fsync_spy.call_count >= 2
    assert next(restore_dir.rglob("big.bin")).read_bytes() == payload


def test_create_backup_substitutes_a_sqlite_snapshot(tmp_path):
    """Verify the archive holds a consistent snapshot rather than the live database."""
    # Given a source tree containing a WAL database with an uncommitted write in flight
    src = tmp_path / "data"
    src.mkdir()
    (src / "plain.txt").write_text("hello")
    make_db(src / "app.db", rows=5)
    dest = tmp_path / "dest"
    dest.mkdir()

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        sqlite_paths=[src / "app.db"],
        strip_source_paths=True,
    )

    with closing(sqlite3.connect(src / "app.db")) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO t (v) VALUES ('uncommitted')")

        # When creating a backup
        backup.create_backup()

    # Then the archive holds the database and the ordinary file, but no journal siblings
    names = _archive_file_members(dest)

    assert "app.db" in names
    assert "plain.txt" in names
    assert not [n for n in names if n.endswith(("-wal", "-shm", "-journal"))]

    # And the archived database is the committed state, without the in-flight write
    extracted = tmp_path / "extracted"
    with tarfile.open(next(dest.glob("*.tgz"))) as tar:
        tar.extractall(path=extracted, filter="data")

    assert row_count(extracted / "app.db") == 5


def test_create_backup_restores_a_usable_sqlite_database(tmp_path):
    """Verify the snapshot round-trips through restore and opens with its rows intact."""
    # Given a backed-up source tree containing a database
    src = tmp_path / "data"
    src.mkdir()
    make_db(src / "app.db", rows=11)
    dest = tmp_path / "dest"
    dest.mkdir()
    restore_to = tmp_path / "restored"
    restore_to.mkdir()

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        sqlite_paths=[src / "app.db"],
        strip_source_paths=True,
        restore_path=restore_to,
    )
    backup.create_backup()

    # When restoring it
    backup.restore_backup()

    # Then the restored database opens with every row
    assert row_count(restore_to / "app.db") == 11


def test_create_backup_fails_when_a_sqlite_snapshot_fails(tmp_path):
    """Verify a corrupt or unreadable database aborts the run instead of shipping."""
    # Given a configured sqlite path that is not a database
    src = tmp_path / "data"
    src.mkdir()
    (src / "app.db").write_text("not a database")
    dest = tmp_path / "dest"
    dest.mkdir()

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        sqlite_paths=[src / "app.db"],
        strip_source_paths=True,
    )

    # When creating a backup
    # Then it fails and writes no archive
    with pytest.raises(BackupFailedError):
        backup.create_backup()

    assert not list(dest.glob("*.tgz"))


def test_create_backup_removes_staged_sqlite_snapshots(tmp_path):
    """Verify snapshots do not accumulate in temp between scheduled runs."""
    # Given a source tree with a database
    src = tmp_path / "data"
    src.mkdir()
    make_db(src / "app.db", rows=3)
    dest = tmp_path / "dest"
    dest.mkdir()

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        sqlite_paths=[src / "app.db"],
        strip_source_paths=True,
    )

    # When creating two backups in a row
    backup.create_backup()
    backup.create_backup()

    # Then no snapshot staging directory is left behind
    assert not list(backup.tmp_dir.glob("sqlite-*"))


def test_create_backup_filters_a_sqlite_snapshot(tmp_path):
    """Verify an excluded database stays out of the archive, snapshot included."""
    # Given a source tree whose database matches the exclude regex
    src = tmp_path / "data"
    src.mkdir()
    (src / "keep.txt").write_text("keep")
    make_db(src / "app.db", rows=5)
    dest = tmp_path / "dest"
    dest.mkdir()

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        sqlite_paths=[src / "app.db"],
        strip_source_paths=True,
        exclude_regex=r"\.db$",
    )

    # When creating a backup
    backup.create_backup()

    # Then the archive matches one taken of a quiesced tree: no database at all
    assert _archive_file_members(dest) == ["keep.txt"]


def test_create_backup_places_a_snapshot_without_stripping(tmp_path):
    """Verify a nested database lands at its live position under the default layout."""
    # Given a source tree with a database in a subdirectory, backed up without stripping
    src = tmp_path / "data"
    (src / "sub").mkdir(parents=True)
    (src / "keep.txt").write_text("keep")
    make_db(src / "sub" / "app.db", rows=7)
    dest = tmp_path / "dest"
    dest.mkdir()
    restore_to = tmp_path / "restored"
    restore_to.mkdir()

    # strip_source_paths is left at its default of False, the layout most backups use
    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        sqlite_paths=[src / "sub" / "app.db"],
        restore_path=restore_to,
    )

    # When creating a backup
    backup.create_backup()

    # Then the snapshot sits where the live database would have, and only once
    assert sorted(_archive_file_members(dest)) == ["data/keep.txt", "data/sub/app.db"]

    # And it restores to that path with every row
    backup.restore_backup()

    assert row_count(restore_to / "data" / "sub" / "app.db") == 7


@pytest.mark.parametrize("strip", [True, False])
def test_create_backup_when_the_source_is_the_database(tmp_path, strip):
    """Verify a source that is itself a database is archived once, as the snapshot."""
    # Given a database configured as both the source path and the sqlite path
    db = tmp_path / "app.db"
    make_db(db, rows=6)
    dest = tmp_path / "dest"
    dest.mkdir()

    backup = ezbak(
        name="test",
        source_paths=[db],
        storage_paths=[dest],
        sqlite_paths=[db],
        strip_source_paths=strip,
    )

    # When creating a backup
    backup.create_backup()

    # Then the archive holds one member, the snapshot under the database's own name
    assert _archive_file_members(dest) == ["app.db"]


def test_create_backup_deduplicates_repeated_sqlite_paths(tmp_path):
    """Verify a database listed twice is not archived twice."""
    # Given the same database configured twice
    src = tmp_path / "data"
    src.mkdir()
    make_db(src / "app.db", rows=2)
    dest = tmp_path / "dest"
    dest.mkdir()

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        sqlite_paths=[src / "app.db", src / "app.db"],
        strip_source_paths=True,
    )

    # When creating a backup
    backup.create_backup()

    # Then it appears once
    assert _archive_file_members(dest) == ["app.db"]


def test_create_backup_skips_a_symlinked_sqlite_path(tmp_path):
    """Verify a symlinked database is skipped rather than copied in from outside the source."""
    # Given a sqlite path that is a symlink to a database outside every source path
    outside = tmp_path / "outside"
    make_db(outside / "real.db", rows=3)
    src = tmp_path / "data"
    src.mkdir()
    (src / "keep.txt").write_text("keep")
    (src / "link.db").symlink_to(outside / "real.db")
    dest = tmp_path / "dest"
    dest.mkdir()

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        sqlite_paths=[src / "link.db"],
        strip_source_paths=True,
    )

    # When creating a backup
    backup.create_backup()

    # Then the archive holds no content from outside the source tree
    assert _archive_file_members(dest) == ["keep.txt"]


def test_create_backup_does_not_snapshot_a_filtered_database(tmp_path, mocker):
    """Verify a database the filters drop is never copied, and its journals stay out too."""
    # Given a source tree whose database and stale journal are both present, with an exclude
    # regex that matches only the database
    src = tmp_path / "data"
    src.mkdir()
    (src / "keep.txt").write_text("keep")
    make_db(src / "app.db", rows=5)
    (src / "app.db-wal").write_bytes(b"\x37\x7f\x06\x82" + b"\x00" * 28)
    dest = tmp_path / "dest"
    dest.mkdir()
    spy = mocker.spy(ezbak_sqlite, "snapshot_database")

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        sqlite_paths=[src / "app.db"],
        strip_source_paths=True,
        exclude_regex=r"app\.db$",
    )

    # When creating a backup
    backup.create_backup()

    # Then no snapshot was taken, and neither the database nor its journal was archived
    assert spy.call_count == 0
    assert _archive_file_members(dest) == ["keep.txt"]


def test_tar_add_filter_excludes_a_live_database_under_a_root_source(tmp_path):
    """Verify the exclusion set is built without a leading slash for a source at the root."""
    # Given a backup whose exclusion set names a database directly under a root source path
    dest = tmp_path / "dest"
    dest.mkdir()
    backup = ezbak(name="test", source_paths=[tmp_path], storage_paths=[dest])

    # When building the add-filter for that source without stripping
    add_filter = backup._tar_add_filter(
        Path("/"), strip=False, excluded_paths=frozenset({Path("/app.db")})
    )

    # Then the live database is dropped under its bare name and other files are kept
    assert add_filter(tarfile.TarInfo("app.db")) is None
    assert add_filter(tarfile.TarInfo("notes.txt")) is not None


def test_restore_clears_a_stale_wal_at_the_destination(tmp_path):
    """Verify a previous deployment's uncheckpointed -wal cannot roll a restore back.

    The archive carries no journal files, so a `-wal` already sitting in the restore target
    would be replayed over the restored database, silently reverting it to older rows.
    """
    # Given a restore target holding a database and the -wal a hard-killed run left behind
    src = tmp_path / "data"
    src.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    target = tmp_path / "target"
    target.mkdir()

    write_uncheckpointed(target / "app.db", "v1-old")
    assert (target / "app.db-wal").exists(), "expected a stale -wal at the restore target"

    # And a live service whose current state is newer
    write_uncheckpointed(src / "app.db", "v2-current")

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        sqlite_paths=[src / "app.db"],
        strip_source_paths=True,
        restore_path=target,
    )
    backup.create_backup()

    # When restoring over the populated target
    result = backup.restore_backup()

    # Then the stale journal is gone and the restored database holds the current rows
    assert result is RestoreOutcome.RESTORED
    assert not (target / "app.db-wal").exists()
    assert not (target / "app.db-shm").exists()
    assert values(target / "app.db") == ["v2-current"]


def test_restore_keeps_a_file_only_named_like_a_journal(tmp_path):
    """Verify a plain file whose name ends in -wal is never deleted by a restore."""
    # Given a restore target holding a text file named like a journal, beside a file the
    # archive replaces
    src = tmp_path / "data"
    src.mkdir()
    (src / "notes").write_text("new notes")
    dest = tmp_path / "dest"
    dest.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    (target / "notes").write_text("old notes")
    (target / "notes-wal").write_text("not a sqlite journal")

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        strip_source_paths=True,
        restore_path=target,
    )
    backup.create_backup()

    # When restoring over it
    backup.restore_backup()

    # Then the lookalike survives untouched and the restore still happened
    assert (target / "notes-wal").read_text() == "not a sqlite journal"
    assert (target / "notes").read_text() == "new notes"


def test_restore_keeps_a_shm_beside_a_non_database(tmp_path):
    """Verify a -shm is only removed when the file it sits beside is really a database.

    A `-shm` carries no identifying header, so the restored base file is what rules the
    removal in or out.
    """
    # Given a restore target holding a -shm beside a plain file the archive replaces
    src = tmp_path / "data"
    src.mkdir()
    (src / "notes").write_text("new notes")
    dest = tmp_path / "dest"
    dest.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    (target / "notes").write_text("old notes")
    (target / "notes-shm").write_bytes(b"unrelated shared memory")

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        strip_source_paths=True,
        restore_path=target,
    )
    backup.create_backup()

    # When restoring over it
    backup.restore_backup()

    # Then the unrelated file survives untouched
    assert (target / "notes-shm").read_bytes() == b"unrelated shared memory"
    assert (target / "notes").read_text() == "new notes"


def test_restore_keeps_a_journal_the_archive_supplies(tmp_path):
    """Verify a journal that came from the archive is restored rather than deleted."""
    # Given an archive holding a database and a real -wal beside it, taken with no
    # sqlite_paths so the journals are archived as ordinary files
    src = tmp_path / "data"
    src.mkdir()
    make_db(src / "app.db", rows=2)
    (src / "app.db-wal").write_bytes(b"\x37\x7f\x06\x82" + b"\x00" * 28)
    dest = tmp_path / "dest"
    dest.mkdir()
    target = tmp_path / "target"
    target.mkdir()

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        strip_source_paths=True,
        restore_path=target,
    )
    backup.create_backup()

    # When restoring
    backup.restore_backup()

    # Then the archived journal lands beside its database
    assert (target / "app.db-wal").read_bytes() == b"\x37\x7f\x06\x82" + b"\x00" * 28


def test_create_backup_snapshots_every_database_matched_by_a_pattern(tmp_path):
    """Verify a pattern snapshots every match, substituting a consistent copy for one a live writer holds open."""
    # Given a service directory of sharded databases, plus a plain file
    src = tmp_path / "data"
    src.mkdir()
    for shard in ("folder.0001-nhx4yzcl", "folder.0002-j4dkatqn", "main"):
        make_db(src / f"{shard}.db", rows=3)
    (src / "readme.txt").write_text("plain")
    dest = tmp_path / "dest"
    dest.mkdir()

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        sqlite_paths=[src / "*.db"],
        strip_source_paths=True,
    )

    # When creating a backup while one shard has an uncommitted write in flight
    with closing(sqlite3.connect(src / "folder.0001-nhx4yzcl.db")) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO t (v) VALUES ('uncommitted')")

        backup.create_backup()

    # Then every database is archived at its live position, with no journals beside them
    names = _archive_file_members(dest)

    assert "folder.0001-nhx4yzcl.db" in names
    assert "folder.0002-j4dkatqn.db" in names
    assert "main.db" in names
    assert "readme.txt" in names
    assert not [n for n in names if n.endswith(("-wal", "-shm", "-journal"))]

    # And the shard with the in-flight write holds only the committed rows
    extracted = tmp_path / "extracted"
    with tarfile.open(next(dest.glob("*.tgz"))) as tar:
        tar.extractall(path=extracted, filter="data")

    assert row_count(extracted / "folder.0001-nhx4yzcl.db") == 3


def test_create_backup_pattern_snapshots_restore_and_open(tmp_path):
    """Verify each database matched by a pattern restores as a usable database."""
    # Given two databases matched by one pattern, one left with an abandoned writer's WAL
    src = tmp_path / "data"
    src.mkdir()
    make_db(src / "one.db", rows=4)
    make_db(src / "two.db", rows=7)
    write_uncheckpointed(src / "two.db", "abandoned-write")
    dest = tmp_path / "dest"
    dest.mkdir()
    restore_to = tmp_path / "restored"
    restore_to.mkdir()

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        sqlite_paths=[src / "*.db"],
        strip_source_paths=True,
        restore_path=restore_to,
    )
    backup.create_backup()

    # Then the archive holds no journal siblings alongside the snapshotted databases
    names = _archive_file_members(dest)
    assert not [n for n in names if n.endswith(("-wal", "-shm", "-journal"))]

    # When restoring
    backup.restore_backup()

    # Then both open, and the abandoned writer's committed value survived the snapshot
    assert row_count(restore_to / "one.db") == 4
    assert values(restore_to / "two.db") == ["abandoned-write"]


def test_create_backup_pattern_picks_up_a_database_added_between_runs(tmp_path):
    """Verify expansion happens per run, so a cron sidecar sees new shards."""
    # Given one database and a backup instance configured with a pattern
    src = tmp_path / "data"
    src.mkdir()
    make_db(src / "first.db")
    dest = tmp_path / "dest"
    dest.mkdir()

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        sqlite_paths=[src / "*.db"],
        strip_source_paths=True,
    )
    backup.create_backup()

    # When a second database appears with an abandoned writer's WAL and another backup
    # runs on the same instance
    write_uncheckpointed(src / "second.db", "abandoned-write")
    backup.create_backup()

    # Then some archive holds both databases, but the ordinary source walk alone would
    # do that much regardless of pattern expansion. What only a fresh expansion produces
    # is a snapshot of second.db, so the real check is the absence of its journal siblings.
    def _members(archive: Path) -> list[str]:
        with tarfile.open(archive) as tar:
            return [member.name for member in tar.getmembers() if member.isfile()]

    archives = {archive: _members(archive) for archive in dest.glob("*.tgz")}

    assert any({"first.db", "second.db"} <= set(members) for members in archives.values())

    matching = next(
        members for members in archives.values() if {"first.db", "second.db"} <= set(members)
    )
    assert not [n for n in matching if n.endswith(("-wal", "-shm", "-journal"))]


def test_create_backup_pattern_leaves_a_non_database_to_the_file_walk(tmp_path):
    """Verify a text file wearing a .db extension is archived rather than failing the run."""
    # Given a real database with an abandoned writer's WAL, and an impostor sharing its extension
    src = tmp_path / "data"
    src.mkdir()
    make_db(src / "real.db", rows=2)
    write_uncheckpointed(src / "real.db", "abandoned-write")
    (src / "notes.db").write_text("not a database")
    dest = tmp_path / "dest"
    dest.mkdir()

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        sqlite_paths=[src / "*.db"],
        strip_source_paths=True,
    )

    # When creating a backup
    backup.create_backup()

    # Then the run succeeded, both files are present, and the snapshotted database
    # carries no journal siblings into the archive
    names = _archive_file_members(dest)

    assert "real.db" in names
    assert "notes.db" in names
    assert not [n for n in names if n.endswith(("-wal", "-shm", "-journal"))]


def test_create_backup_relative_pattern_matches_under_the_source_path(tmp_path):
    """Verify a relative pattern matches under the configured source, not the process cwd."""
    # Given a database nested below the source, with an abandoned writer's WAL
    src = tmp_path / "data"
    (src / "nested").mkdir(parents=True)
    make_db(src / "nested" / "app.db", rows=3)
    write_uncheckpointed(src / "nested" / "app.db", "abandoned-write")
    dest = tmp_path / "dest"
    dest.mkdir()

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        sqlite_paths=[Path("**/*.db")],
        strip_source_paths=True,
    )

    # When creating a backup
    backup.create_backup()

    # Then the database is archived at its live position under the source, with no
    # journal siblings beside it
    names = _archive_file_members(dest)

    assert "nested/app.db" in names
    assert not [n for n in names if n.endswith(("-wal", "-shm", "-journal"))]


def test_create_backup_reports_a_glob_failure_instead_of_raising(tmp_path, mocker):
    """Verify a pattern the interpreter rejects fails the run through the normal error path."""
    # Given a working pattern config whose expansion blows up at backup time, standing in
    # for a glob Python refuses (a '**' that is not a whole component on Python < 3.13)
    src = tmp_path / "data"
    src.mkdir()
    make_db(src / "app.db")
    dest = tmp_path / "dest"
    dest.mkdir()

    backup = ezbak(
        name="test",
        source_paths=[src],
        storage_paths=[dest],
        sqlite_paths=[src / "*.db"],
    )
    mocker.patch(
        "ezbak.core.expand_sqlite_paths",
        side_effect=ValueError("Invalid pattern: '**' can only be an entire path component"),
    )

    # When creating a backup
    # Then it fails as an EZBakError, which the container's scheduler logs and alerts on,
    # rather than as a bare ValueError that escapes it
    with pytest.raises(BackupFailedError):
        backup.create_backup()

    assert not list(dest.glob("*.tgz"))
