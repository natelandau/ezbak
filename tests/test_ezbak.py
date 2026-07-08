"""Test ezbak."""

import shutil
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp  # noqa: F401
from zoneinfo import ZoneInfo

import time_machine

from ezbak import ezbak
from ezbak.constants import DEFAULT_DATE_FORMAT
from ezbak.core import _merge_move

UTC = ZoneInfo("UTC")
frozen_time = datetime(2025, 6, 9, tzinfo=UTC)
frozen_time_str = frozen_time.strftime(DEFAULT_DATE_FORMAT)
fixture_archive_path = Path(__file__).parent / "fixtures" / "archive.tgz"


@time_machine.travel(frozen_time, tick=False)
def test_create_backup(filesystem, debug, capsys, tmp_path):
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
    # Create an empty directory
    empty_dir = src_dir / "empty_dir"
    empty_dir.mkdir()

    # Given: A backup manager configured with test parameters
    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir, test_file],
        storage_paths=[dest1, dest2],
        log_level="trace",
        delete_source_after_backup=False,
        tz="Etc/UTC",
    )

    # When: Creating multiple backups at the same instant
    for _ in range(7):
        backup_manager.create_backup()

    output = capsys.readouterr().err
    assert "Skip backup of symlink" in output
    assert "TRACE    | Add to tar: src/empty_dir" in output
    assert "Excluded file: .DS_Store" in output

    base_name = f"test-{frozen_time_str}.tgz"
    for dest in [dest1, dest2]:
        # Then: exactly one unlabeled base file plus six uid-suffixed collisions
        assert (dest / base_name).exists()
        collisions = list(dest.glob(f"test-{frozen_time_str}-*.tgz"))
        assert len(collisions) == 6
        assert len(list(dest.glob("test-*.tgz"))) == 7

    # Then: List backups returns every file across both storage paths
    assert len(backup_manager.list_backups()) == 14


@time_machine.travel(frozen_time, tick=False)
def test_exclude_regex(filesystem, debug, capsys, tmp_path):
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
        log_level="error",
        tz="Etc/UTC",
    )

    # When: Creating a backup
    backup_manager.create_backup()
    backup_manager.restore_backup(restore_dir)
    # output = capsys.readouterr().err
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
def test_include_regex(filesystem, debug, capsys, tmp_path):
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
        log_level="error",
        tz="Etc/UTC",
    )

    # When: Creating a backup
    backup_manager.create_backup()
    backup_manager.restore_backup(restore_dir)
    # output = capsys.readouterr().err
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


def test_restore_backup(filesystem, debug, capsys, tmp_path):
    """Verify the correct backup is selected and restored."""
    # Backwards-compat guard: seeds legacy period-labeled filenames on purpose; do not modernize.
    # Given: Source and destination directories from fixture
    src_dir, _, _ = filesystem
    tmp_dst = tmp_path / "dst"
    tmp_dst.mkdir()

    backup_names = [
        "test-20250623T182710-yearly.tgz",
        "test-20250623T184301-weekly.tgz",
        "test-20250623T190750-daily.tgz",
        "test-20250623T193930-hourly.tgz",
        "test-20250623T193951-minutely.tgz",
        "test-20250624T084658-daily.tgz",
        "test-20250624T084727-hourly-Tr5J7.tgz",
    ]

    for backup_name in backup_names:
        shutil.copy2(fixture_archive_path, tmp_dst / backup_name)

    # Given: A backup manager configured with test parameters
    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[tmp_dst],
        log_level="error",
    )
    latest_backup = backup_manager.get_latest_backup()
    assert latest_backup.name == "test-20250624T084727-hourly-Tr5J7.tgz"

    # When: Restoring the latest backup
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    existing_file = restore_dir / "existing_file.txt"
    existing_file.touch()
    backup_manager.restore_backup(restore_dir)

    # Then: All source files are restored correctly
    for file in src_dir.rglob("*"):
        assert (restore_dir / src_dir.name / file.name).exists()


def test_create_backup_strip_path(filesystem, debug, capsys, tmp_path):
    """Verify that the path is stripped from the backup."""
    # Given: Source and destination directories from fixture
    src_dir, dst1, _ = filesystem

    # Given: A backup manager configured with test parameters
    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dst1],
        strip_source_paths=True,
        log_level="error",
    )

    backup_manager.create_backup()

    # When: Restoring the latest backup
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    backup_manager.restore_backup(restore_dir)

    # debug(src_dir, "src_dir")
    # debug(restore_dir)

    # Then: All source files are restored correctly
    for file in src_dir.rglob("*"):
        assert (restore_dir / file.name).exists()


def test_prune_max_backups(debug, capsys, tmp_path):
    """Verify that backups are pruned correctly."""
    # Backwards-compat guard: seeds legacy period-labeled filenames on purpose; do not modernize.
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
    output = capsys.readouterr().err
    # debug(output)
    # debug(tmp_path)

    assert "Pruned 10 backups" in output
    existing_files = list(tmp_path.iterdir())
    assert len(existing_files) == 3
    for filename in [
        "test-20250609T101857-hourly.tgz",
        "test-20250609T095804-minutely.tgz",
        "test-20250609T095804-minutely-p2we3r.tgz",
    ]:
        assert Path(tmp_path / filename).exists()


def test_prune_returns_confirmed_deletions(tmp_path):
    """Verify prune returns the backups actually removed, not just those targeted."""
    # Given more backups on disk than the retention policy keeps
    filenames = [
        "test-20250609T101857-hourly.tgz",
        "test-20250609T095745-minutely.tgz",
        "test-20250609T095804-minutely.tgz",
        "test-20250609T095751-minutely.tgz",
        "test-20250609T090932-yearly.tgz",
    ]
    for filename in filenames:
        Path(tmp_path / filename).touch()
    backup_manager = ezbak(
        name="test", source_paths=[tmp_path], storage_paths=[tmp_path], max_backups=2
    )

    # When pruning
    deleted = backup_manager.prune_backups()

    # Then the returned backups are exactly the ones no longer on disk
    remaining = {p.name for p in tmp_path.iterdir()}
    assert len(deleted) == 3
    assert all(backup.name not in remaining for backup in deleted)


def test_prune_dry_run(debug, capsys, tmp_path):
    """Verify a dry run reports prune targets without deleting anything."""
    # Backwards-compat guard: seeds legacy period-labeled filenames on purpose; do not modernize.
    # Given: 13 backup files on disk
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

    # Given: A backup manager with a count-based retention policy
    backup_manager = ezbak(
        name="test",
        source_paths=[tmp_path],
        storage_paths=[tmp_path],
        log_level="debug",
        max_backups=3,
    )

    # When: pruning runs in dry-run mode
    targets = backup_manager.prune_backups(dry_run=True)
    output = capsys.readouterr().err

    # Then: the targets are reported but every file remains on disk
    assert len(targets) == 10
    assert "Dry run" in output
    assert "Pruned" not in output
    existing_files = list(tmp_path.iterdir())
    assert len(existing_files) == 13
    for filename in filenames:
        assert Path(tmp_path / filename).exists()


def test_prune_policy(debug, capsys, tmp_path):
    """Verify that backups are pruned correctly."""
    # Backwards-compat guard: seeds legacy period-labeled filenames on purpose; do not modernize.
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
    output = capsys.readouterr().err
    # debug(output)
    # debug(tmp_path)

    assert "Pruned 3 backups" in output
    existing_files = list(tmp_path.iterdir())
    assert len(existing_files) == 10
    for filename in [
        "test-20240609T090932-yearly.tgz",
        "test-20250609T095745-minutely.tgz",
        "test-20250609T095737-minutely.tgz",
    ]:
        assert not Path(tmp_path / filename).exists()


def test_prune_no_policy(debug, capsys, tmp_path):
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
    output = capsys.readouterr().err
    # debug(output)
    # debug(tmp_path)

    assert "Will not delete backups " in output
    existing_files = list(tmp_path.iterdir())
    assert len(existing_files) == 13
    for filename in filenames:
        assert Path(tmp_path / filename).exists()


def test_prune_missing_file(debug, capsys, tmp_path, mocker):
    """Verify pruning tolerates a backup that vanished before deletion."""
    # Given: real backup files on disk
    real_files = [
        "test-20250609T101857-hourly.tgz",
        "test-20250609T095804-minutely.tgz",
    ]
    for filename in real_files:
        Path(tmp_path / filename).touch()

    # Given: the index also reports a file that no longer exists on disk, simulating a
    # concurrent host having already pruned it from a shared storage location
    phantom = tmp_path / "test-20200101T000000-yearly.tgz"
    mocker.patch(
        "ezbak.storage.local.find_files",
        autospec=True,
        return_value=[tmp_path / f for f in real_files] + [phantom],
    )

    # Given: A backup manager configured with test parameters
    backup_manager = ezbak(
        name="test",
        source_paths=[tmp_path],
        storage_paths=[tmp_path],
        log_level="debug",
        max_backups=2,
    )

    # When: pruning runs with the phantom (oldest) targeted for deletion
    backup_manager.prune_backups()

    # Then: the missing file is logged as such, the job completes, and real files remain.
    # The phantom was never actually removed, so it does not count toward the confirmed total.
    output = capsys.readouterr().err
    assert "Missing, not deleted:" in output
    assert phantom.name in output
    assert "Deleted:" not in output
    assert "Pruned 0 backups" in output
    for filename in real_files:
        assert Path(tmp_path / filename).exists()


def test_restore_with_clean(debug, tmp_path, capsys, filesystem):
    """Verify that a backup directory is cleaned before restoring."""
    # Given: Source and destination directories from fixture
    src_dir, dest1, _ = filesystem

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        log_level="info",
    )
    backup_manager.create_backup()

    # When: Restoring the latest backup
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    test_file = restore_dir / "test_file.txt"
    test_file.touch()
    backup_manager.restore_backup(restore_dir, clean_before_restore=True)
    capsys.readouterr()

    # Then: All source files are restored correctly
    for file in src_dir.rglob("*"):
        assert (restore_dir / src_dir.name / file.name).exists()

    assert not (restore_dir / test_file.name).exists()


def test_delete_source_after_backup(debug, capsys, tmp_path, filesystem):
    """Verify that source paths are deleted after backup."""
    src_dir, dest1, _ = filesystem
    test_file = tmp_path / "test_file.txt"
    test_file.touch()

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir, test_file],
        storage_paths=[dest1],
        log_level="trace",
        delete_source_after_backup=True,
    )
    assert len(list(src_dir.iterdir())) != 0
    backup_manager.create_backup()
    output = capsys.readouterr().err
    # debug(output)
    # debug(tmp_path)

    assert "Cleaned source: " in output
    assert src_dir.exists()
    assert src_dir.is_dir()
    assert len(list(src_dir.iterdir())) == 0
    assert "Deleted source: " in output
    assert not test_file.exists()


def test_merge_move_overlays_and_overwrites(tmp_path):
    """Verify _merge_move keeps non-colliding files, overwrites collisions, merges nested dirs."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    # Existing destination tree
    (dst / "keep.txt").write_text("old-keep")
    (dst / "shared.txt").write_text("old-shared")
    (dst / "sub").mkdir()
    (dst / "sub" / "existing.txt").write_text("old-existing")

    # Staged tree to move in
    (src / "new.txt").write_text("new")
    (src / "shared.txt").write_text("new-shared")
    (src / "sub").mkdir()
    (src / "sub" / "added.txt").write_text("added")

    _merge_move(src, dst)

    assert (dst / "keep.txt").read_text() == "old-keep"  # non-colliding survives
    assert (dst / "shared.txt").read_text() == "new-shared"  # collision overwritten
    assert (dst / "new.txt").read_text() == "new"  # new moved in
    assert (dst / "sub" / "existing.txt").read_text() == "old-existing"  # nested survives
    assert (dst / "sub" / "added.txt").read_text() == "added"  # nested merged
    assert not any(src.iterdir())  # staging emptied


def test_merge_move_handles_type_collisions(tmp_path):
    """Verify _merge_move replaces across type mismatches without following symlinks."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    # dir in src replaces a file in dst
    (dst / "a").write_text("old-file")
    (src / "a").mkdir()
    (src / "a" / "inner.txt").write_text("inner")

    # file in src replaces a dir in dst
    (dst / "b").mkdir()
    (dst / "b" / "leftover.txt").write_text("leftover")
    (src / "b").write_text("new-file")

    # file in src replaces a symlink in dst (symlink must be unlinked, not followed)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (dst / "c").symlink_to(outside)
    (src / "c").write_text("replacement")

    _merge_move(src, dst)

    assert (dst / "a").is_dir()
    assert (dst / "a" / "inner.txt").read_text() == "inner"
    assert (dst / "b").is_file()
    assert (dst / "b").read_text() == "new-file"
    assert (dst / "c").is_file()
    assert not (dst / "c").is_symlink()
    assert (dst / "c").read_text() == "replacement"
    assert outside.read_text() == "outside"  # symlink target untouched (not followed)
    assert not any(src.iterdir())
