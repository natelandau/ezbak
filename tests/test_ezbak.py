"""Test ezbak."""

import shutil
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp
from zoneinfo import ZoneInfo

import pytest
import time_machine

from ezbak import ezbak
from ezbak.backup import Backup
from ezbak.checksums import sha256_file
from ezbak.constants import DEFAULT_DATE_FORMAT, StorageType
from ezbak.core import _commit_restore, _is_within, _merge_move
from ezbak.exceptions import ConfigurationError, RestoreFailedError

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


def test_prune_keep_last(debug, capsys, tmp_path):
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
        keep_last=3,
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
        name="test", source_paths=[tmp_path], storage_paths=[tmp_path], keep_last=2
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
        keep_last=3,
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


def test_prune_union_policy(debug, capsys, tmp_path):
    """Verify union rules keep the newest per period plus the recent N."""
    # Given daily backups over four days
    for stamp in ("20250104T090000", "20250103T090000", "20250102T090000", "20250101T090000"):
        (tmp_path / f"test-{stamp}-daily.tgz").touch()

    # When pruning with keep_daily=2
    app = ezbak(name="test", source_paths=[tmp_path], storage_paths=[tmp_path], keep_daily=2)
    app.prune_backups()

    # Then only the two most recent days survive
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == ["test-20250103T090000-daily.tgz", "test-20250104T090000-daily.tgz"]


def test_prune_all_zero_policy_refuses(debug, capsys, tmp_path):
    """Verify an all-zero policy logs an error and deletes nothing."""
    # Given two backups and a policy that would keep zero
    for stamp in ("20250102T090000", "20250101T090000"):
        (tmp_path / f"test-{stamp}-daily.tgz").touch()
    app = ezbak(name="test", source_paths=[tmp_path], storage_paths=[tmp_path], keep_last=0)

    # When pruning (must not raise)
    deleted = app.prune_backups()

    # Then nothing is deleted and an error is logged
    assert deleted == []
    assert len(list(tmp_path.iterdir())) == 2
    assert "would delete every backup" in capsys.readouterr().err


def test_prune_mixed_zero_and_positive_policy(debug, capsys, tmp_path):
    """Verify keep_last=0 paired with a positive calendar rule prunes normally."""
    # Given daily backups over four days and a policy with keep_last=0
    for stamp in ("20250104T090000", "20250103T090000", "20250102T090000", "20250101T090000"):
        (tmp_path / f"test-{stamp}-daily.tgz").touch()
    app = ezbak(
        name="test", source_paths=[tmp_path], storage_paths=[tmp_path], keep_last=0, keep_daily=2
    )

    # When pruning (must not trip the all-zero safety floor)
    deleted = app.prune_backups()
    output = capsys.readouterr().err

    # Then the two most-recent daily representatives survive and the rest are gone
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == ["test-20250103T090000-daily.tgz", "test-20250104T090000-daily.tgz"]
    assert len(deleted) == 2
    assert "would delete every backup" not in output


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
        keep_last=2,
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


def test_restore_clean_is_atomic(tmp_path, filesystem):
    """Verify a clean restore replaces contents and leaves no staging dir behind."""
    src_dir, dest1, _ = filesystem
    mgr = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1], log_level="error")
    mgr.create_backup()

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    (restore_dir / "stale.txt").write_text("stale")

    assert mgr.restore_backup(restore_dir, clean_before_restore=True) is True

    for file in src_dir.rglob("*"):
        assert (restore_dir / src_dir.name / file.name).exists()
    assert not (restore_dir / "stale.txt").exists()
    assert not any(p.name.startswith(".ezbak-restore-") for p in restore_dir.iterdir())


def test_restore_extract_failure_leaves_dest_intact(tmp_path, filesystem, monkeypatch):
    """Verify a mid-extract failure leaves the destination untouched."""
    src_dir, dest1, _ = filesystem
    mgr = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1], log_level="error")
    mgr.create_backup()

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    (restore_dir / "keep.txt").write_text("keep")

    def boom(self, *args: object, **kwargs: object):
        msg = "simulated disk full during extract"
        raise OSError(msg)

    monkeypatch.setattr("tarfile.TarFile.extractall", boom)

    with pytest.raises(RestoreFailedError):
        mgr.restore_backup(restore_dir, clean_before_restore=True)

    # Destination is untouched: pre-existing file survives, nothing extracted,
    # no staging directory left behind.
    assert (restore_dir / "keep.txt").read_text() == "keep"
    assert not (restore_dir / src_dir.name).exists()
    assert not any(p.name.startswith(".ezbak-restore-") for p in restore_dir.iterdir())


def test_restore_chowns_staging_tree(tmp_path, filesystem, monkeypatch):
    """Verify chown targets the staging tree (a child of the restore path)."""
    src_dir, dest1, _ = filesystem
    mgr = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        chown_uid=0,
        chown_gid=0,
        log_level="error",
    )
    mgr.create_backup()

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()

    called = {}

    def fake_chown(directory, uid, gid):
        called["directory"] = Path(directory)

    monkeypatch.setattr("ezbak.core.chown_files", fake_chown)

    mgr.restore_backup(restore_dir, clean_before_restore=True)

    assert called["directory"].name.startswith(".ezbak-restore-")
    assert called["directory"].parent.resolve() == restore_dir.resolve()


def test_restore_overlay_reaps_orphaned_staging(tmp_path, filesystem):
    """Verify an overlay restore removes staging dirs orphaned by a prior crash."""
    src_dir, dest1, _ = filesystem
    mgr = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1], log_level="error")
    mgr.create_backup()

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    (restore_dir / "keep.txt").write_text("keep")
    # Simulate a staging dir orphaned by a hard kill of a prior restore.
    orphan = restore_dir / ".ezbak-restore-deadbeef"
    orphan.mkdir()
    (orphan / "leftover.txt").write_text("leftover")

    # Overlay restore (no clean): must still reap the orphan.
    assert mgr.restore_backup(restore_dir) is True

    assert (restore_dir / "keep.txt").read_text() == "keep"  # overlay preserved existing file
    assert not orphan.exists()  # orphaned staging reaped
    assert not any(p.name.startswith(".ezbak-restore-") for p in restore_dir.iterdir())


def test_restore_preserves_staging_on_commit_failure(tmp_path, filesystem, monkeypatch):
    """Verify a commit failure keeps the extracted staging tree for manual recovery."""
    src_dir, dest1, _ = filesystem
    mgr = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1], log_level="error")
    mgr.create_backup()

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()

    def boom(*args: object, **kwargs: object):
        msg = "simulated commit failure"
        raise OSError(msg)

    # Fail during the swap, after the archive has been extracted into staging.
    monkeypatch.setattr("ezbak.core._commit_restore", boom)

    with pytest.raises(RestoreFailedError):
        mgr.restore_backup(restore_dir, clean_before_restore=True)

    # Extract-failure staging is thrown away, but a commit failure preserves it:
    # the destination may be partial, so staging holds the sole clean copy.
    staging_dirs = [p for p in restore_dir.iterdir() if p.name.startswith(".ezbak-restore-")]
    assert len(staging_dirs) == 1
    assert any(staging_dirs[0].iterdir())  # holds the extracted files


def test_is_within_matches_nested_and_equal_paths(tmp_path):
    """Verify the lexical overlap cases of _is_within."""
    outer = tmp_path / "outer"
    (outer / "inner").mkdir(parents=True)
    sibling = tmp_path / "sibling"
    sibling.mkdir()

    assert _is_within(outer, outer)  # equal
    assert _is_within(outer / "inner", outer)  # nested
    assert not _is_within(outer, outer / "inner")  # outer is not within inner
    assert not _is_within(sibling, outer)  # unrelated


def test_is_within_detects_aliased_directory(tmp_path):
    """Verify _is_within catches two paths that alias one directory (as a bind mount does)."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)  # same directory, two different paths (device+inode identical)

    # A pure path comparison would miss the overlap; the device+inode check catches it.
    assert not link.is_relative_to(real)
    assert _is_within(link, real)


def test_restore_into_storage_path_is_rejected(tmp_path, filesystem):
    """Verify restoring into or above a storage location is refused before any deletion."""
    src_dir = filesystem[0]
    storage = tmp_path / "store"
    storage.mkdir()
    mgr = ezbak(name="test", source_paths=[src_dir], storage_paths=[storage], log_level="error")
    mgr.create_backup()

    archives_before = {p.name for p in storage.iterdir() if p.is_file()}
    assert archives_before  # the backup archive lives in the storage dir

    # Restoring into the storage dir itself is rejected...
    with pytest.raises(ConfigurationError):
        mgr.restore_backup(storage, clean_before_restore=True)

    # ...and into a parent that contains the storage dir (a clean restore there
    # would empty the storage subtree).
    with pytest.raises(ConfigurationError):
        mgr.restore_backup(tmp_path, clean_before_restore=True)

    # No archive was deleted by either rejected restore.
    assert {p.name for p in storage.iterdir() if p.is_file()} == archives_before


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


def test_commit_restore_clean_replaces_contents(tmp_path):
    """Verify clean commit removes existing dest entries but not the staging dir."""
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "old.txt").write_text("old")
    staging = Path(mkdtemp(dir=dest, prefix=".ezbak-restore-"))
    (staging / "new.txt").write_text("new")

    _commit_restore(staging, dest, clean=True)

    remaining = {p.name for p in dest.iterdir() if p != staging}
    assert remaining == {"new.txt"}
    assert not (dest / "old.txt").exists()
    assert staging.exists()  # staging survives the clean loop
    assert not any(staging.iterdir())  # emptied; caller removes it


def test_commit_restore_overlay_keeps_existing(tmp_path):
    """Verify overlay commit keeps non-colliding existing files."""
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "keep.txt").write_text("keep")
    staging = Path(mkdtemp(dir=dest, prefix=".ezbak-restore-"))
    (staging / "new.txt").write_text("new")

    _commit_restore(staging, dest, clean=False)

    assert (dest / "keep.txt").read_text() == "keep"
    assert (dest / "new.txt").read_text() == "new"
    assert staging.exists()


def test_create_writes_local_sidecar(filesystem) -> None:
    """Verify create_backup writes a .sha256 sidecar next to the local archive."""
    # Given: A backup manager configured with a single local destination
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])

    # When: Creating a backup
    backups = app.create_backup()

    # Then: A sidecar file exists next to the archive and matches its digest
    archive = backups[0].path
    sidecar = archive.parent / (archive.name + ".sha256")
    assert sidecar.exists()
    assert sha256_file(archive) in sidecar.read_text()


def test_create_no_checksum_when_disabled(filesystem) -> None:
    """Verify no sidecar is written when write_checksums is disabled."""
    # Given: A backup manager with checksum writing disabled
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1], write_checksums=False)

    # When: Creating a backup
    app.create_backup()

    # Then: No sidecar file is created
    assert not list(dest1.glob("*.sha256"))


def test_restore_rejects_corrupt_archive(filesystem, tmp_path) -> None:
    """Verify a corrupted archive fails checksum verification before extraction."""
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    backup = app.create_backup()[0]

    # Corrupt the archive so its bytes no longer match the sidecar digest.
    with backup.path.open("r+b") as handle:
        handle.write(b"\x00\x01\x02\x03")

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    with pytest.raises(RestoreFailedError, match="Checksum mismatch"):
        app.restore_backup(restore_path=restore_dir)


def test_restore_missing_sidecar_warns_and_succeeds(filesystem, tmp_path, capsys) -> None:
    """Verify a restore proceeds with a warning when no checksum sidecar exists."""
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    backup = app.create_backup()[0]
    (backup.path.parent / (backup.path.name + ".sha256")).unlink()

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    assert app.restore_backup(restore_path=restore_dir) is True
    assert "without integrity verification" in capsys.readouterr().err


def test_restore_non_utf8_sidecar_warns_and_succeeds(filesystem, tmp_path, capsys) -> None:
    """Verify a non-UTF-8 sidecar degrades to warn-and-proceed instead of crashing."""
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    backup = app.create_backup()[0]

    sidecar = backup.path.parent / (backup.path.name + ".sha256")
    sidecar.write_bytes(b"\xff\xfe\x00\x01garbage")

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    assert app.restore_backup(restore_path=restore_dir) is True
    assert "without integrity verification" in capsys.readouterr().err


def test_restore_verifies_good_archive(filesystem, tmp_path) -> None:
    """Verify a restore succeeds when the archive matches its checksum sidecar."""
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    app.create_backup()
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    assert app.restore_backup(restore_path=restore_dir) is True
    # strip_source_paths defaults to False, so the archive nests files under the
    # source directory's own name (matches the convention used by other restore
    # tests in this file, e.g. test_exclude_regex).
    assert (restore_dir / src_dir.name / "foo.txt").exists()


def test_local_prune_deletes_sidecar(filesystem) -> None:
    """Verify prune removes a pruned archive's sidecar, leaving none orphaned."""
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1], keep_last=1)
    app.create_backup()
    app.create_backup()
    app.prune_backups()

    assert len(list(dest1.glob("*.tgz"))) == 1
    assert len(list(dest1.glob("*.sha256"))) == 1


def test_backup_period_keys_unique_across_years(tmp_path):
    """Verify same month in different years yields distinct period keys."""
    # Given two backups in June of consecutive years
    older = tmp_path / "test-20250609T090000-monthly.tgz"
    newer = tmp_path / "test-20260609T090000-monthly.tgz"
    older.touch()
    newer.touch()
    b_old = Backup(path=older, name=older.name, storage_type=StorageType.LOCAL)
    b_new = Backup(path=newer, name=newer.name, storage_type=StorageType.LOCAL)

    # When comparing their monthly, weekly, daily, hourly, minutely keys
    # Then none collide across the two years
    assert b_old.month != b_new.month
    assert b_old.week != b_new.week
    assert b_old.day != b_new.day
    assert b_old.hour != b_new.hour
    assert b_old.minute != b_new.minute
    assert b_old.year != b_new.year


def test_backup_period_keys_unique_across_months(tmp_path):
    """Verify same day-of-month in different months yields distinct period keys."""
    # Given two backups on the same day-of-month but in different months of the same year
    may = tmp_path / "test-20250508T090000-daily.tgz"
    june = tmp_path / "test-20250608T090000-daily.tgz"
    may.touch()
    june.touch()
    b_may = Backup(path=may, name=may.name, storage_type=StorageType.LOCAL)
    b_june = Backup(path=june, name=june.name, storage_type=StorageType.LOCAL)

    # When comparing their period keys
    # Then day, hour, and minute keys differ (month prefix guards uniqueness)
    # and month keys differ
    assert b_may.day != b_june.day
    assert b_may.hour != b_june.hour
    assert b_may.minute != b_june.minute
    assert b_may.month != b_june.month
    # And year remains the same (not the distinguishing factor)
    assert b_may.year == b_june.year
