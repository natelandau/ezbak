"""Tests for the merged EZBak core class."""

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from ezbak.constants import StorageType
from ezbak.core import EZBak, ezbak
from ezbak.exceptions import ConfigurationError

fixture_archive_path = Path(__file__).parent / "fixtures" / "archive.tgz"


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


def test_restore_backup_restore_date_unresolvable_returns_false(tmp_path, mocker):
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
    assert result is False
    spy.assert_not_called()
