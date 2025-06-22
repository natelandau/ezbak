"""Test ezbak."""

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import time_machine

from ezbak import ezbak
from ezbak.constants import BACKUP_NAME_REGEX, DEFAULT_DATE_FORMAT

UTC = ZoneInfo("UTC")
frozen_time = datetime(2025, 6, 9, tzinfo=UTC)
frozen_time_str = frozen_time.strftime(DEFAULT_DATE_FORMAT)
fixture_archive_path = Path(__file__).parent / "fixtures" / "archive.tgz"


@time_machine.travel(frozen_time, tick=False)
def test_create_and_restore_backup(filesystem, debug, clean_stderr, tmp_path):
    """Verify that a backups are created and restored correctly."""
    # Given: Source and destination directories from fixture
    src_dir, dest1, dest2 = filesystem

    simlink_file = tmp_path / "simlink_file.txt"
    simlink_file.touch()
    (src_dir / "symlink").symlink_to(simlink_file)
    test_file = tmp_path / "test_file.txt"
    test_exclude_file = src_dir / ".DS_Store"
    test_file.touch()
    test_exclude_file.touch()

    # Given: Expected backup filenames for different time units
    filenames = [
        f"test-{frozen_time_str}-weekly.tgz",
        f"test-{frozen_time_str}-hourly.tgz",
        f"test-{frozen_time_str}-daily.tgz",
        f"test-{frozen_time_str}-minutely.tgz",
        f"test-{frozen_time_str}-monthly.tgz",
        f"test-{frozen_time_str}-yearly.tgz",
    ]

    # Given: A backup manager configured with test parameters
    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir, test_file],
        storage_paths=[dest1, dest2],
        label_time_units=True,
        log_level="trace",
    )

    # When: Creating multiple backups
    for _ in range(7):
        backup_manager.create_backup()

    # When: Capturing stderr output
    output = clean_stderr()
    # debug(output)
    # debug(src_dir)

    assert "Skip backup of symlink" in output

    # Then: All expected backup files exist in both storage paths
    for filename in filenames:
        assert Path(dest1 / filename).exists()
        assert Path(dest2 / filename).exists()
        assert f"INFO     | Created: …/dest1/{filename}" in output
        assert f"INFO     | Created: …/dest2/{filename}" in output
        assert "Excluded file: .DS_Store" in output

    # Then: Minutely backups have UUID suffixes
    for dest in [dest1, dest2]:
        files_with_uuid = list(dest.glob(f"test-{frozen_time_str}-minutely-*.tgz"))
        assert len(files_with_uuid) == 1

    # Then: List backups returns correct count
    list_backups = backup_manager.list_backups()
    assert len(list_backups) == 14
    assert all(Path(dest1 / filename).exists() for filename in filenames)
    assert all(Path(dest2 / filename).exists() for filename in filenames)

    # Then: Latest backup has correct name structure
    latest_backup = backup_manager.get_latest_backup()
    latest_backup_name_parts = BACKUP_NAME_REGEX.match(latest_backup.name).groupdict()
    assert latest_backup_name_parts["name"] == "test"
    assert latest_backup_name_parts["timestamp"] == frozen_time_str
    assert latest_backup_name_parts["period"] == "minutely"
    assert len(latest_backup_name_parts["uuid"]) == 5
    assert latest_backup_name_parts["extension"] == "tgz"

    # When: Restoring the latest backup
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    existing_file = restore_dir / "existing_file.txt"
    existing_file.touch()
    backup_manager.restore_backup(restore_dir)

    # Then: All source files are restored correctly
    for file in src_dir.rglob("*"):
        if file.name in {test_exclude_file.name, "symlink"}:
            assert not (restore_dir / src_dir.name / file.name).exists()
            continue
        assert (restore_dir / src_dir.name / file.name).exists()

    assert (restore_dir / test_file.name).exists()
    assert (restore_dir / existing_file.name).exists()


@time_machine.travel(frozen_time, tick=False)
def test_without_labels(debug, clean_stderr, filesystem, tmp_path):
    """Verify that backups are created without labels."""
    # Given: Source and destination directories from fixture
    src_dir, dest1, dest2 = filesystem

    # Given: A backup manager configured with test parameters
    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1, dest2],
        label_time_units=False,
        log_level="info",
    )

    # When: Creating multiple backups
    for _ in range(2):
        backup_manager.create_backup()

    output = clean_stderr()
    # debug(output)
    # debug(tmp_path)

    assert f"INFO     | Created: …/dest2/test-{frozen_time_str}.tgz" in output
    assert f"INFO     | Created: …/dest1/test-{frozen_time_str}.tgz" in output
    assert re.search(rf"dest1/test-{frozen_time_str}-[a-z0-9]{{5}}\.tgz", output)
    assert re.search(rf"dest2/test-{frozen_time_str}-[a-z0-9]{{5}}\.tgz", output)


@time_machine.travel(frozen_time, tick=False)
def test_exclude_regex(filesystem, debug, clean_stderr, tmp_path):
    """Verify that files are excluded from the backup."""
    # Given: Source and destination directories from fixture
    src_dir, dest1, _ = filesystem

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()

    # Given: A backup manager configured with test parameters
    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        exclude_regex=r"foo\.txt$",
    )

    # When: Creating a backup
    backup_manager.create_backup()
    backup_manager.restore_backup(restore_dir)
    # output = clean_stderr()
    # debug(output)
    # debug(restore_dir)

    i = 0
    for file in src_dir.rglob("*"):
        if file.name == "foo.txt":
            assert not (restore_dir / src_dir.name / file.name).exists()
            i += 1
        else:
            assert (restore_dir / src_dir.name / file.name).exists()
            i += 1
    assert i == len(list(src_dir.rglob("*")))


@time_machine.travel(frozen_time, tick=False)
def test_include_regex(filesystem, debug, clean_stderr, tmp_path):
    """Verify that files are excluded from the backup."""
    # Given: Source and destination directories from fixture
    src_dir, dest1, _ = filesystem

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()

    # Given: A backup manager configured with test parameters
    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        include_regex=r"foo\.txt$",
    )

    # When: Creating a backup
    backup_manager.create_backup()
    backup_manager.restore_backup(restore_dir)
    # output = clean_stderr()
    # debug(output)
    # debug(restore_dir)

    i = 0
    for file in src_dir.rglob("*"):
        if file.name == "foo.txt" or file.is_dir():
            assert (restore_dir / src_dir.name / file.name).exists()
            i += 1
        else:
            assert not (restore_dir / src_dir.name / file.name).exists()
            i += 1
    assert i == len(list(src_dir.rglob("*")))


def test_create_backup_strip_path(filesystem, debug, clean_stderr, tmp_path):
    """Verify that the path is stripped from the backup."""
    # Given: Source and destination directories from fixture
    src_dir, dest1, _ = filesystem
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()

    # Given: A backup manager configured with test parameters
    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        strip_source_paths=True,
    )
    backup_manager.create_backup()
    backup_manager.restore_backup(restore_dir)
    # output = clean_stderr()
    # debug(output)

    for file in src_dir.rglob("*"):
        assert (restore_dir / file.name).exists()


def test_rename_backups_with_labels(debug, clean_stderr, tmp_path):
    """Verify that backups are renamed correctly."""
    # Given: Source and destination directories from fixture

    # Given: A backup manager configured with test parameters
    filenames = [
        "test-20240609T090932-yearly.tgz",
        "test-20250609T095737-daily.tgz",
        "test-20250609T095751-minutely.tgz",
        "test-20250609T090932-yearly.tgz",
        "test-20250609T095737.tgz",
        "test-20250609T095804-minutely-p2we3r.tgz",
        "test-20250609T095625-monthly.tgz",
        "test-20250609T095737-minutely.tgz",
        "test-20250609T095804-minutely.tgz",
        "test-20250609T095730-weekly-k6lop.tgz",
        "test-20250609T095745-hourly.tgz",
        "test-20250609T101857-hourly.tgz",
        "test-20250609T095730-weekly-pl9kj.tgz",
        "test-20250609T095749-minutely.tgz",
    ]
    for filename in filenames:
        Path(tmp_path / filename).touch()

    # Given: A backup manager configured with test parameters
    backup_manager = ezbak(
        name="test",
        source_paths=[tmp_path],
        storage_paths=[tmp_path],
        log_level="trace",
        label_time_units=True,
    )
    backup_manager.rename_backups()
    output = clean_stderr()
    # debug(output)
    # debug(tmp_path)

    assert "test-20250609T095730-weekly-pl9kj.tgz -> test-20250609T095730-daily.tgz" in output
    assert "test-20250609T095737-daily.tgz -> test-20250609T095737-hourly.tgz" in output
    assert re.search(
        r"test-20250609T095737\.tgz -> test-20250609T095737-minutely-[a-z0-9]{5}\.tgz",
        output,
        re.IGNORECASE,
    )
    assert "test-20250609T095745-hourly.tgz -> test-20250609T095745-minutely.tgz" in output
    assert "Renamed 4 backups" in output

    renamed_files = [
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
    ]
    for filename in renamed_files:
        assert Path(tmp_path / filename).exists()


def test_rename_backups_without_labels(debug, clean_stderr, tmp_path):
    """Verify that backups are renamed correctly."""
    # Given: A backup manager configured with test parameters
    filenames = [
        "test-20240609T090932-yearly.tgz",
        "test-20250609T095737-daily.tgz",
        "test-20250609T095751-minutely.tgz",
        "test-20250609T090932-yearly.tgz",
        "test-20250609T095737.tgz",
        "test-20250609T095804-minutely-p2we3r.tgz",
    ]
    for filename in filenames:
        Path(tmp_path / filename).touch()

    # Given: A backup manager configured with test parameters
    backup_manager = ezbak(
        name="test",
        source_paths=[tmp_path],
        storage_paths=[tmp_path],
        log_level="trace",
        label_time_units=False,
    )
    backup_manager.rename_backups()
    output = clean_stderr()
    # debug(output)
    # debug(tmp_path)

    assert "Renamed: test-20240609T090932-yearly.tgz -> test-20240609T090932.tgz" in output
    assert "Renamed: test-20250609T090932-yearly.tgz -> test-20250609T090932.tgz" in output
    assert "Renamed: test-20250609T095751-minutely.tgz -> test-20250609T095751.tgz" in output
    assert (
        "DEBUG    | Renamed: test-20250609T095804-minutely-p2we3r.tgz -> test-20250609T095804.tgz"
        in output
    )
    assert re.search(
        r"test-20250609T095737-daily\.tgz -> test-20250609T095737-[a-z0-9]{5,6}\.tgz",
        output,
        re.IGNORECASE,
    )

    ###################################

    renamed_files = [
        "test-20250609T095751.tgz",
        "test-20250609T095804.tgz",
        "test-20250609T090932.tgz",
        "test-20240609T090932.tgz",
        "test-20250609T095737.tgz",
    ]
    for filename in renamed_files:
        assert Path(tmp_path / filename).exists()


def test_prune_max_backups(debug, clean_stderr, tmp_path):
    """Verify that backups are pruned correctly."""
    # Given: A backup manager configured with test parameters
    filenames = [
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
    ]
    for filename in filenames:
        Path(tmp_path / filename).touch()

    # Given: A backup manager configured with test parameters
    backup_manager = ezbak(
        name="test",
        source_paths=[tmp_path],
        storage_paths=[tmp_path],
        log_level="debug",
        max_backups=3,
    )
    backup_manager.prune_backups()
    output = clean_stderr()
    # debug(output)
    # debug(tmp_path)

    assert "Deleted 10 old backups" in output
    existing_files = list(tmp_path.iterdir())
    assert len(existing_files) == 3
    for filename in [
        "test-20250609T101857-hourly.tgz",
        "test-20250609T095804-minutely.tgz",
        "test-20250609T095804-minutely-p2we3r.tgz",
    ]:
        assert Path(tmp_path / filename).exists()


def test_prune_policy(debug, clean_stderr, tmp_path):
    """Verify that backups are pruned correctly."""
    # Given: A backup manager configured with test parameters
    filenames = [
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
    ]
    for filename in filenames:
        Path(tmp_path / filename).touch()

    # Given: A backup manager configured with test parameters
    backup_manager = ezbak(
        name="test",
        source_paths=[tmp_path],
        storage_paths=[tmp_path],
        log_level="debug",
        retention_yearly=1,
        retention_monthly=4,
        retention_weekly=4,
        retention_daily=4,
        retention_hourly=4,
        retention_minutely=4,
    )
    backup_manager.prune_backups()
    output = clean_stderr()
    # debug(output)
    # debug(tmp_path)

    assert "Deleted 3 old backups" in output
    existing_files = list(tmp_path.iterdir())
    assert len(existing_files) == 10
    for filename in [
        "test-20240609T090932-yearly.tgz",
        "test-20250609T095745-minutely.tgz",
        "test-20250609T095737-minutely.tgz",
    ]:
        assert not Path(tmp_path / filename).exists()


def test_prune_no_policy(debug, clean_stderr, tmp_path):
    """Verify that backups are pruned correctly."""
    # Given: Source and destination directories from fixture

    # Given: A backup manager configured with test parameters
    filenames = [
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
    ]
    for filename in filenames:
        Path(tmp_path / filename).touch()

    # Given: A backup manager configured with test parameters
    backup_manager = ezbak(
        name="test",
        source_paths=[tmp_path],
        storage_paths=[tmp_path],
        log_level="debug",
    )
    backup_manager.prune_backups()
    output = clean_stderr()
    # debug(output)
    # debug(tmp_path)

    assert "Will not delete backups " in output
    existing_files = list(tmp_path.iterdir())
    assert len(existing_files) == 13
    for filename in filenames:
        assert Path(tmp_path / filename).exists()


def test_restore_with_clean(debug, tmp_path, clean_stderr, filesystem):
    """Verify that a backup directory is cleaned before restoring."""
    # Given: Source and destination directories from fixture
    src_dir, dest1, _ = filesystem

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        label_time_units=False,
        log_level="info",
    )
    backup_manager.create_backup()

    # When: Restoring the latest backup
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    test_file = restore_dir / "test_file.txt"
    test_file.touch()
    backup_manager.restore_backup(restore_dir, clean_before_restore=True)

    # Then: All source files are restored correctly
    for file in src_dir.rglob("*"):
        assert (restore_dir / src_dir.name / file.name).exists()

    assert not (restore_dir / test_file.name).exists()
