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
    test_file = tmp_path / "test_file.txt"
    test_file.touch()

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
        sources=[src_dir, test_file],
        destinations=[dest1, dest2],
        label_time_units=True,
        log_level="trace",
    )

    # When: Creating multiple backups
    for _ in range(7):
        backup_manager.create_backup()

    # When: Capturing stderr output
    output = clean_stderr()
    # debug(output)

    # Then: All expected backup files exist in both destinations
    for filename in filenames:
        assert Path(dest1 / filename).exists()
        assert Path(dest2 / filename).exists()
        assert f"INFO     | Created: …/dest1/{filename}" in output
        assert f"INFO     | Created: …/dest2/{filename}" in output

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
    backup_manager.restore_latest_backup(restore_dir)

    # Then: All source files are restored correctly
    for file in src_dir.rglob("*"):
        assert (restore_dir / src_dir.name / file.name).exists()
    assert (restore_dir / test_file.name).exists()


@time_machine.travel(frozen_time, tick=False)
def test_without_labels(debug, clean_stderr, filesystem, tmp_path):
    """Verify that backups are created without labels."""
    # Given: Source and destination directories from fixture
    src_dir, dest1, dest2 = filesystem

    # Given: A backup manager configured with test parameters
    backup_manager = ezbak(
        name="test",
        sources=[src_dir],
        destinations=[dest1, dest2],
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

    backup_manager.rename_backups()
    output = clean_stderr()
    # debug(output)
    # debug(tmp_path)

    assert "INFO     | Will not rename backups" in output


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
        sources=[src_dir],
        destinations=[dest1],
        exclude_regex=r"foo\.txt$",
    )

    # When: Creating a backup
    backup_manager.create_backup()
    backup_manager.restore_latest_backup(restore_dir)
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
        sources=[src_dir],
        destinations=[dest1],
        include_regex=r"foo\.txt$",
    )

    # When: Creating a backup
    backup_manager.create_backup()
    backup_manager.restore_latest_backup(restore_dir)
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


def test_rename_backups(debug, clean_stderr, tmp_path):
    """Verify that backups are renamed correctly."""
    # Given: Source and destination directories from fixture

    # Given: A backup manager configured with test parameters
    filenames = [
        "test-20240609T090932-yearly.tgz",
        "test-20250609T095737-daily.tgz",
        "test-20250609T095751-minutely.tgz",
        "test-20250609T090932-yearly.tgz",
        "test-20250609T095737-hourly.tgz",
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
        sources=[tmp_path],
        destinations=[tmp_path],
        log_level="debug",
    )
    backup_manager.rename_backups()
    output = clean_stderr()
    # debug(output)
    # debug(tmp_path)

    assert "test-20250609T095730-weekly-pl9kj.tgz -> test-20250609T095730-daily.tgz" in output
    assert "test-20250609T095737-daily.tgz -> test-20250609T095737-hourly.tgz" in output
    assert re.search(
        r"test-20250609T095737-hourly\.tgz -> test-20250609T095737-minutely-[a-z0-9]{5}\.tgz",
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
        sources=[tmp_path],
        destinations=[tmp_path],
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
        sources=[tmp_path],
        destinations=[tmp_path],
        log_level="debug",
        time_based_policy={
            "yearly": 1,
            "monthly": 4,
            "weekly": 4,
            "daily": 4,
            "hourly": 4,
            "minutely": 4,
        },
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
        sources=[tmp_path],
        destinations=[tmp_path],
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
