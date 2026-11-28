"""Test ezbak."""

import hashlib
import io
import os
import shutil
import socket
import tarfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import time_machine

from ezbak import ezbak
from ezbak.backup import Backup, StorageLocation
from ezbak.checksums import format_sidecar, sidecar_name
from ezbak.constants import DEFAULT_DATE_FORMAT, RestoreOutcome
from ezbak.core import EZBak
from ezbak.exceptions import ConfigurationError, RestoreFailedError
from ezbak.sources import SKIPPED_SPECIAL_FILE_WARNING
from ezbak.storage.local import LocalBackend
from tests.helpers import (
    LEGACY_BACKUP_NAMES,
    SEEDED_MARKER,
    relative_tree,
    seed_backups,
    skip_as_root,
)

UTC = ZoneInfo("UTC")
frozen_time = datetime(2025, 6, 9, tzinfo=UTC)
frozen_time_str = frozen_time.strftime(DEFAULT_DATE_FORMAT)


@pytest.fixture
def backed_up(filesystem: tuple[Path, Path, Path]) -> Callable[..., EZBak]:
    """Provide a factory for an app that has already backed up the `filesystem` source once.

    Returns:
        Callable[..., EZBak]: Build an app from config overrides and take one backup to `dest1`.
    """
    src_dir, dest1, _ = filesystem

    def _make(**overrides: Any) -> EZBak:  # ruff:ignore[any-type]
        app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1], **overrides)
        app.create_backup()
        return app

    return _make


def _staging_dirs(directory: Path) -> list[Path]:
    """Return the restore staging directories left in `directory`."""
    return [p for p in directory.iterdir() if p.name.startswith(".ezbak-restore-")]


@time_machine.travel(frozen_time, tick=False)
def test_create_backup(filesystem, tmp_path):
    """Verify backups taken at the same instant get unique names in every storage path."""
    # Given: Source and destination directories from fixture
    src_dir, dest1, dest2 = filesystem

    simlink_file = tmp_path / "simlink_file.txt"
    simlink_file.touch()
    (src_dir / "symlink").symlink_to(simlink_file)
    test_file = tmp_path / "test_file.txt"
    test_file.touch()
    (src_dir / "empty_dir").mkdir()

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

    # Then: the symlink is skipped and the empty directory is kept
    base_name = f"test-{frozen_time_str}.tgz"
    with tarfile.open(dest1 / base_name) as tar:
        members = set(tar.getnames())
    assert "src/symlink" not in members
    assert "src/empty_dir" in members

    for dest in [dest1, dest2]:
        # Then: exactly one unlabeled base file plus six uid-suffixed collisions
        assert (dest / base_name).exists()
        collisions = list(dest.glob(f"test-{frozen_time_str}-*.tgz"))
        assert len(collisions) == 6
        assert len(list(dest.glob("test-*.tgz"))) == 7

    # Then: List backups returns every file across both storage paths
    assert len(backup_manager.list_backups()) == 14


@time_machine.travel(frozen_time, tick=False)
def test_create_backup_keeps_same_instant_backups_of_a_nested_set(filesystem):
    """Verify a set name with "/" never overwrites a backup taken at the same instant."""
    # Given a set whose name puts its backups in a subdirectory of the storage path
    src_dir, dest, _ = filesystem
    (dest / "team").mkdir()
    app = ezbak(name="team/test", source_paths=[src_dir], storage_paths=[dest], tz="Etc/UTC")

    # When creating two backups at the same instant
    app.create_backup()
    app.create_backup()

    # Then both archives are kept
    assert len(list((dest / "team").glob(f"test-{frozen_time_str}*.tgz"))) == 2


@time_machine.travel(frozen_time, tick=False)
def test_include_regex(filesystem, backed_up, restore_dir):
    """Verify only files matching include_regex are backed up."""
    # Given: A backup of the source taken with an include regex
    src_dir, _, _ = filesystem
    backup_manager = backed_up(include_regex=r"foo\.txt$", tz="Etc/UTC")

    # When: Restoring the backup
    backup_manager.restore_backup(restore_dir)

    # Then: only the matching files and their directories are restored
    expected = {
        path
        for path in relative_tree(src_dir)
        if path.name == "foo.txt" or (src_dir / path).is_dir()
    }
    assert relative_tree(restore_dir / src_dir.name) == expected


def test_restore_backup(filesystem, tmp_path, restore_dir):
    """Verify the correct backup is selected and restored."""
    # Backwards-compat guard: seeds legacy period-labeled filenames on purpose; do not modernize.
    # Given: Source and destination directories from fixture
    src_dir, _, _ = filesystem
    tmp_dst = tmp_path / "dst"
    tmp_dst.mkdir()
    seed_backups(
        tmp_dst,
        [
            "test-20250623T182710-yearly.tgz",
            "test-20250623T184301-weekly.tgz",
            "test-20250623T190750-daily.tgz",
            "test-20250623T193930-hourly.tgz",
            "test-20250623T193951-minutely.tgz",
            "test-20250624T084658-daily.tgz",
            "test-20250624T084727-hourly-Tr5J7.tgz",
        ],
    )

    # Given: A backup manager configured with test parameters
    backup_manager = ezbak(name="test", source_paths=[src_dir], storage_paths=[tmp_dst])

    # When: Restoring the latest backup
    backup_manager.restore_backup(restore_dir)

    # Then: The newest backup is restored with every source file
    assert (restore_dir / SEEDED_MARKER).read_text() == "test-20250624T084727-hourly-Tr5J7.tgz"
    assert relative_tree(restore_dir / src_dir.name) == relative_tree(src_dir)


def test_create_backup_strip_path(filesystem, backed_up, restore_dir):
    """Verify that the path is stripped from the backup."""
    # Given: A backup taken with source paths stripped
    src_dir, _, _ = filesystem
    backup_manager = backed_up(strip_source_paths=True)

    # When: Restoring the latest backup
    backup_manager.restore_backup(restore_dir)

    # Then: All source files are restored directly under the restore path
    assert relative_tree(restore_dir) == relative_tree(src_dir)


@time_machine.travel(frozen_time, tick=False)
def test_create_backup_complete_when_rglob_under_reports(filesystem, monkeypatch):
    """Verify the archive holds every source file even when Path.rglob returns a partial listing."""
    # Given: a source tree with three sibling subdirectories ("repos")
    src_dir, dest1, _ = filesystem
    for repo in ("repo_a", "repo_b", "repo_c"):
        (src_dir / repo).mkdir()
        (src_dir / repo / f"{repo}.txt").write_text(repo)

    # Given: Path.rglob silently drops two of the repos, reproducing an NFS scandir
    # error that pathlib.glob swallows mid-walk (the production OOM-adjacent bug).
    original_rglob = Path.rglob

    def partial_rglob(self, pattern, *args: object, **kwargs: object):
        for entry in original_rglob(self, pattern, *args, **kwargs):
            if "repo_b" not in entry.parts and "repo_c" not in entry.parts:
                yield entry

    monkeypatch.setattr(Path, "rglob", partial_rglob)

    # Given: a backup manager writing to a local destination
    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        use_checksums=False,
        tz="Etc/UTC",
    )

    # When: creating a backup
    backup_manager.create_backup()

    # Then: the archive still contains all three repos' files
    archive = next(dest1.glob("test-*.tgz"))
    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
    assert "src/repo_a/repo_a.txt" in names
    assert "src/repo_b/repo_b.txt" in names
    assert "src/repo_c/repo_c.txt" in names


def test_prune_keep_last(legacy_backups):
    """Verify that backups are pruned correctly."""
    # Backwards-compat guard: seeds legacy period-labeled filenames on purpose; do not modernize.
    # Given: A backup manager keeping the last three backups
    backup_manager = ezbak(
        name="test", source_paths=[legacy_backups], storage_paths=[legacy_backups], keep_last=3
    )

    # When: Pruning
    backup_manager.prune_backups()

    # Then: Only the three newest backups remain
    assert {p.name for p in legacy_backups.iterdir()} == {
        "test-20250609T101857-hourly.tgz",
        "test-20250609T095804-minutely.tgz",
        "test-20250609T095804-minutely-p2we3r.tgz",
    }


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


def test_prune_dry_run(legacy_backups):
    """Verify a dry run reports prune targets without deleting anything."""
    # Backwards-compat guard: seeds legacy period-labeled filenames on purpose; do not modernize.
    # Given: A backup manager with a count-based retention policy
    backup_manager = ezbak(
        name="test", source_paths=[legacy_backups], storage_paths=[legacy_backups], keep_last=3
    )

    # When: pruning runs in dry-run mode
    targets = backup_manager.prune_backups(dry_run=True)

    # Then: the targets are reported but every file remains on disk
    assert len(targets) == 10
    assert {p.name for p in legacy_backups.iterdir()} == set(LEGACY_BACKUP_NAMES)


def test_prune_all_zero_policy_refuses(tmp_path):
    """Verify an all-zero policy deletes nothing."""
    # Given two backups and a policy that would keep zero
    for stamp in ("20250102T090000", "20250101T090000"):
        (tmp_path / f"test-{stamp}-daily.tgz").touch()
    app = ezbak(name="test", source_paths=[tmp_path], storage_paths=[tmp_path], keep_last=0)

    # When pruning (must not raise)
    deleted = app.prune_backups()

    # Then nothing is deleted
    assert deleted == []
    assert len(list(tmp_path.iterdir())) == 2


def test_prune_mixed_zero_and_positive_policy(tmp_path):
    """Verify keep_last=0 paired with a positive calendar rule prunes normally."""
    # Given daily backups over four days and a policy with keep_last=0
    for stamp in ("20250104T090000", "20250103T090000", "20250102T090000", "20250101T090000"):
        (tmp_path / f"test-{stamp}-daily.tgz").touch()
    app = ezbak(
        name="test", source_paths=[tmp_path], storage_paths=[tmp_path], keep_last=0, keep_daily=2
    )

    # When pruning (must not trip the all-zero safety floor)
    deleted = app.prune_backups()

    # Then the two most-recent daily representatives survive and the rest are gone
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == ["test-20250103T090000-daily.tgz", "test-20250104T090000-daily.tgz"]
    assert len(deleted) == 2


def test_prune_no_policy(legacy_backups):
    """Verify pruning with no retention policy deletes nothing."""
    # Given: A backup manager with no retention policy
    backup_manager = ezbak(
        name="test", source_paths=[legacy_backups], storage_paths=[legacy_backups]
    )

    # When: Pruning
    deleted = backup_manager.prune_backups()

    # Then: Every backup remains
    assert deleted == []
    assert {p.name for p in legacy_backups.iterdir()} == set(LEGACY_BACKUP_NAMES)


def test_prune_no_policy_skips_reindex(backed_up, mocker):
    """Verify prune with no retention policy does not re-index storage locations."""
    # Given: a fresh backup, which invalidates the cached storage index
    backup_manager = backed_up()
    index_spy = mocker.spy(backup_manager.backends[0], "index")

    # When: pruning with no retention policy configured
    backup_manager.prune_backups()

    # Then: no backend re-index happened (nothing can be deleted, so a fresh
    # index would be paid, an S3 LIST per cron tick, purely for logging)
    assert index_spy.call_count == 0


def test_prune_missing_file(tmp_path, mocker):
    """Verify pruning tolerates a backup that vanished before deletion."""
    # Given: real backup files on disk
    real_files = [
        "test-20250609T101857-hourly.tgz",
        "test-20250609T095804-minutely.tgz",
    ]
    for filename in real_files:
        Path(tmp_path / filename).touch()

    # Given: the index also reports a file that vanishes right after indexing, simulating
    # a concurrent host having already pruned it from a shared storage location
    phantom = tmp_path / "test-20200101T000000-yearly.tgz"
    phantom.touch()
    original_index = LocalBackend.index

    def index_then_vanish(self: LocalBackend) -> list[StorageLocation]:
        locations = original_index(self)
        phantom.unlink()
        return locations

    mocker.patch.object(LocalBackend, "index", autospec=True, side_effect=index_then_vanish)

    # Given: A backup manager configured with test parameters
    backup_manager = ezbak(
        name="test", source_paths=[tmp_path], storage_paths=[tmp_path], keep_last=2
    )

    # When: pruning runs with the phantom (oldest) targeted for deletion
    deleted = backup_manager.prune_backups()

    # Then: the job completes, the phantom does not count as deleted, and real files remain
    assert deleted == []
    for filename in real_files:
        assert Path(tmp_path / filename).exists()


def test_restore_clean_is_atomic(filesystem, backed_up, restore_dir):
    """Verify a clean restore replaces contents and leaves no staging dir behind."""
    # Given a backup and a restore target holding a stale file
    src_dir, _, _ = filesystem
    mgr = backed_up()
    (restore_dir / "stale.txt").write_text("stale")

    # When restoring with clean_before_restore
    outcome = mgr.restore_backup(restore_dir, clean_before_restore=True)

    # Then only the backup's tree remains
    assert outcome is RestoreOutcome.RESTORED
    assert relative_tree(restore_dir) == {
        Path(src_dir.name),
        *(Path(src_dir.name) / p for p in relative_tree(src_dir)),
    }


def test_restore_extract_failure_leaves_dest_intact(
    filesystem, backed_up, restore_dir, monkeypatch
):
    """Verify a mid-extract failure leaves the destination untouched."""
    src_dir, _, _ = filesystem
    mgr = backed_up()
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
    assert not _staging_dirs(restore_dir)


def test_restore_chowns_staging_tree(backed_up, restore_dir, monkeypatch):
    """Verify chown targets the staging tree (a child of the restore path)."""
    mgr = backed_up(chown_uid=0, chown_gid=0)

    called = {}

    def fake_chown(directory, uid, gid):
        called["directory"] = Path(directory)

    monkeypatch.setattr("ezbak.core.chown_files", fake_chown)

    mgr.restore_backup(restore_dir, clean_before_restore=True)

    assert called["directory"].name.startswith(".ezbak-restore-")
    assert called["directory"].parent.resolve() == restore_dir.resolve()


def test_restore_overlay_reaps_orphaned_staging(backed_up, restore_dir):
    """Verify an overlay restore removes staging dirs orphaned by a prior crash."""
    mgr = backed_up()
    (restore_dir / "keep.txt").write_text("keep")
    # Simulate a staging dir orphaned by a hard kill of a prior restore.
    orphan = restore_dir / ".ezbak-restore-deadbeef"
    orphan.mkdir()
    (orphan / "leftover.txt").write_text("leftover")

    # Overlay restore (no clean): must still reap the orphan.
    assert mgr.restore_backup(restore_dir) is RestoreOutcome.RESTORED

    assert (restore_dir / "keep.txt").read_text() == "keep"  # overlay preserved existing file
    assert not _staging_dirs(restore_dir)


def test_restore_skips_populated_target(filesystem, backed_up, restore_dir):
    """Verify a restore is a no-op when the target holds data and the guard is set."""
    # Given a backup and a restore target that already contains a live file
    src_dir, _, _ = filesystem
    mgr = backed_up(skip_restore_if_populated=True)
    (restore_dir / "live.txt").write_text("live")

    # When restoring into the populated target
    outcome = mgr.restore_backup(restore_dir)

    # Then nothing is extracted and the existing data is untouched
    assert outcome is RestoreOutcome.SKIPPED_POPULATED
    assert (restore_dir / "live.txt").read_text() == "live"
    assert not (restore_dir / src_dir.name).exists()


def test_restore_populated_ignores_benign_entries(filesystem, backed_up, restore_dir):
    """Verify lost+found and staging dirs do not count as data for the guard."""
    # Given a target holding only ignored noise and an orphaned staging dir
    src_dir, _, _ = filesystem
    mgr = backed_up(skip_restore_if_populated=True)
    (restore_dir / "lost+found").mkdir()
    (restore_dir / ".ezbak-restore-abc123").mkdir()

    # When restoring, then the guard treats the target as empty and restores
    assert mgr.restore_backup(restore_dir) is RestoreOutcome.RESTORED
    assert relative_tree(restore_dir / src_dir.name) == relative_tree(src_dir)


def test_restore_populated_guard_bypassed_by_clean(backed_up, restore_dir):
    """Verify clean_before_restore overrides the populated guard and replaces data."""
    # Given a populated target with both the guard and clean set
    mgr = backed_up(skip_restore_if_populated=True)
    (restore_dir / "stale.txt").write_text("stale")

    # When restoring with clean_before_restore
    outcome = mgr.restore_backup(restore_dir, clean_before_restore=True)

    # Then the guard is bypassed: the target is wiped and restored
    assert outcome is RestoreOutcome.RESTORED
    assert not (restore_dir / "stale.txt").exists()


def test_restore_populated_guard_off_overlays(filesystem, backed_up, restore_dir):
    """Verify the default (guard off) still overlays into a populated target."""
    # Given a populated target and the guard left at its default (off)
    src_dir, _, _ = filesystem
    mgr = backed_up()
    (restore_dir / "live.txt").write_text("live")

    # When restoring, then it overlays and reports RESTORED
    assert mgr.restore_backup(restore_dir) is RestoreOutcome.RESTORED
    assert (restore_dir / "live.txt").read_text() == "live"
    assert relative_tree(restore_dir / src_dir.name) == relative_tree(src_dir)


def test_restore_overlay_merges_into_existing_entries(filesystem, backed_up, restore_dir, tmp_path):
    """Verify an overlay restore lets the backup win collisions, merges folders, and never follows symlinks."""
    # Given a backup with a nested folder, and a target already holding entries that collide
    src_dir, _, _ = filesystem
    (src_dir / "dir2").mkdir()
    (src_dir / "dir2" / "inner.txt").write_text("inner")
    (src_dir / "baz.txt").write_text("from-backup")
    mgr = backed_up()
    target = restore_dir / src_dir.name
    target.mkdir()
    (target / "baz.txt").write_text("stale")
    (target / "dir1").mkdir()
    (target / "dir1" / "existing.txt").write_text("existing")
    (target / "dir2").write_text("a file where the backup has a folder")
    (target / "bar.txt").mkdir()
    (target / "bar.txt" / "leftover.txt").write_text("a folder where the backup has a file")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (target / "foo.txt").symlink_to(outside)

    # When restoring without cleaning the target
    assert mgr.restore_backup(restore_dir) is RestoreOutcome.RESTORED

    # Then the backup's entries replace colliding ones of any type
    assert (target / "baz.txt").read_text() == "from-backup"
    assert (target / "dir2" / "inner.txt").read_text() == "inner"
    assert (target / "bar.txt").is_file()
    assert (target / "foo.txt").is_file()
    assert not (target / "foo.txt").is_symlink()
    # And existing nested entries survive, and the symlink target outside is untouched
    assert (target / "dir1" / "existing.txt").read_text() == "existing"
    assert outside.read_text() == "outside"


def test_restore_populated_guard_fails_on_unreadable_target(backed_up, restore_dir, monkeypatch):
    """Verify an unreadable restore target fails loudly instead of restoring silently."""
    # Given a populated-guard restore where the target cannot be listed
    mgr = backed_up(skip_restore_if_populated=True)

    # Only restore_dir's own iterdir() raises; staging's iterdir() (used later
    # by the merge-move commit) must keep working, or the failure would come
    # from the commit step instead of the guard.
    real_iterdir = Path.iterdir

    def boom(self, *args: object, **kwargs: object):
        if self == restore_dir:
            msg = "simulated permission denied listing restore target"
            raise OSError(msg)
        return real_iterdir(self, *args, **kwargs)

    monkeypatch.setattr("pathlib.Path.iterdir", boom)

    # When restoring, then the guard aborts loudly rather than treating the
    # unreadable target as empty and restoring into it
    with pytest.raises(RestoreFailedError):
        mgr.restore_backup(restore_dir)


def test_restore_preserves_staging_on_commit_failure(backed_up, restore_dir, monkeypatch):
    """Verify a commit failure keeps the extracted staging tree for manual recovery."""
    mgr = backed_up()

    def boom(*args: object, **kwargs: object):
        msg = "simulated commit failure"
        raise OSError(msg)

    # Fail during the swap, after the archive has been extracted into staging.
    monkeypatch.setattr("ezbak.core._commit_restore", boom)

    with pytest.raises(RestoreFailedError):
        mgr.restore_backup(restore_dir, clean_before_restore=True)

    # Extract-failure staging is thrown away, but a commit failure preserves it:
    # the destination may be partial, so staging holds the sole clean copy.
    staging_dirs = _staging_dirs(restore_dir)
    assert len(staging_dirs) == 1
    assert any(staging_dirs[0].iterdir())  # holds the extracted files


def test_restore_into_storage_path_is_rejected(filesystem, backed_up, tmp_path, monkeypatch):
    """Verify restoring into or above a storage location is refused before any deletion."""
    _, storage, _ = filesystem
    mgr = backed_up()
    archives_before = {p.name for p in storage.iterdir() if p.is_file()}
    assert archives_before  # the backup archive lives in the storage dir

    # Restoring into the storage dir itself is rejected...
    with pytest.raises(ConfigurationError):
        mgr.restore_backup(storage, clean_before_restore=True)

    # ...and into a parent that contains the storage dir (a clean restore there
    # would empty the storage subtree).
    with pytest.raises(ConfigurationError):
        mgr.restore_backup(tmp_path, clean_before_restore=True)

    # ...and into a second path to the storage dir. With resolve() unable to collapse the
    # symlink, it stands in for a bind mount, which only the device+inode check can catch.
    alias = tmp_path / "alias"
    alias.symlink_to(storage)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", Path.absolute)
        with pytest.raises(ConfigurationError):
            mgr.restore_backup(alias, clean_before_restore=True)

    # No archive was deleted by any rejected restore.
    assert {p.name for p in storage.iterdir() if p.is_file()} == archives_before


def test_delete_source_after_backup(tmp_path, filesystem):
    """Verify that source paths are deleted after backup."""
    src_dir, dest1, _ = filesystem
    test_file = tmp_path / "test_file.txt"
    test_file.touch()

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir, test_file],
        storage_paths=[dest1],
        delete_source_after_backup=True,
    )
    assert len(list(src_dir.iterdir())) != 0
    backup_manager.create_backup()

    assert src_dir.is_dir()
    assert not any(src_dir.iterdir())
    assert not test_file.exists()


def _swap_for_symlink(path: Path, target: Path) -> None:
    """Replace the directory at `path` with a symlink to `target`, as a hostile app would."""
    shutil.rmtree(path)
    path.symlink_to(target)


@pytest.mark.parametrize("swapped", ["source", "parent"])
def test_create_backup_refuses_a_source_path_swapped_for_a_symlink(tmp_path, swapped):
    """Verify a symlink planted at a source path or its parent is refused, not followed.

    The application owns the directory around its source, so it can replace either one with a
    link to a directory only ezbak can reach. Following it would archive that directory and,
    with delete_source_after_backup, empty it.
    """
    # Given an app built while the source was a real directory
    source = tmp_path / "volume" / "app" / "uploads"
    source.mkdir(parents=True)
    (source / "legit.txt").write_text("legit")
    outside = tmp_path / "outside"
    (outside / "uploads").mkdir(parents=True)
    markers = [outside / "marker.txt", outside / "uploads" / "marker.txt"]
    for marker in markers:
        marker.write_text("secret")
    storage = tmp_path / "storage"
    storage.mkdir()
    app = ezbak(
        name="test",
        source_paths=[source],
        storage_paths=[storage],
        delete_source_after_backup=True,
    )

    # When the source, or its parent, is then swapped for a symlink to the outside directory
    _swap_for_symlink(source if swapped == "source" else source.parent, outside)

    # Then the backup is refused, nothing is archived, and nothing outside is deleted
    with pytest.raises(ConfigurationError, match="symlink"):
        app.create_backup()
    assert not list(storage.iterdir())
    assert all(marker.read_text() == "secret" for marker in markers)


def test_create_backup_skips_a_file_swapped_for_a_symlink_mid_walk(
    filesystem, tmp_path, monkeypatch
):
    """Verify a file replaced by a symlink after it is listed is skipped, not read through.

    A path-based walk checks an entry's type and then reopens it by name, so an application
    that swaps the file in between gets the link target's bytes archived under its own name.
    """
    # Given a source file that is swapped for a symlink to a secret just before it is opened
    src_dir, dest1, _ = filesystem
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")
    victim = src_dir / "foo.txt"
    victim.write_text("plain")
    real_open = os.open

    def swap_then_open(
        path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if path == victim.name and dir_fd is not None and not victim.is_symlink():
            victim.unlink()
            victim.symlink_to(secret)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_then_open)
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])

    # When backing up
    backups = app.create_backup()
    monkeypatch.undo()

    # Then the swapped file is left out and the secret never reaches the archive
    assert victim.is_symlink()
    assert backups[0].path is not None
    with tarfile.open(backups[0].path) as tar:
        assert "src/foo.txt" not in tar.getnames()
        contents = [
            extracted.read()
            for member in tar.getmembers()
            if (extracted := tar.extractfile(member)) is not None
        ]
    assert all(b"TOP-SECRET" not in content for content in contents)


@skip_as_root
def test_create_backup_never_opens_a_file_the_filters_exclude(filesystem):
    """Verify an unreadable file that exclude_regex leaves out does not fail the backup."""
    # Given a source holding an unreadable file that the exclude regex matches
    src_dir, dest1, _ = filesystem
    unreadable = src_dir / "secret.key"
    unreadable.write_text("key")
    unreadable.chmod(0)
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1], exclude_regex=r"\.key$")

    # When backing up
    try:
        backups = app.create_backup()
    finally:
        unreadable.chmod(0o600)

    # Then the backup succeeds without the excluded file
    assert backups[0].path is not None
    with tarfile.open(backups[0].path) as tar:
        assert "src/foo.txt" in tar.getnames()
        assert "src/secret.key" not in tar.getnames()


@skip_as_root
def test_create_backup_reaches_a_source_through_a_search_only_parent(tmp_path):
    """Verify a parent directory needs only execute permission, as a path lookup does."""
    # Given a source whose parent directory grants search but not read permission
    parent = tmp_path / "parent"
    source = parent / "data"
    source.mkdir(parents=True)
    (source / "file.txt").write_text("data")
    storage = tmp_path / "storage"
    storage.mkdir()
    app = ezbak(name="test", source_paths=[source], storage_paths=[storage])
    parent.chmod(0o311)

    # When backing up
    try:
        backups = app.create_backup()
    finally:
        parent.chmod(0o755)

    # Then the source is archived
    assert backups[0].path is not None
    with tarfile.open(backups[0].path) as tar:
        assert "data/file.txt" in tar.getnames()


def test_create_backup_stores_second_hardlink_as_link(filesystem):
    """Verify the walk stores a second hard link as a link member."""
    # Given a source holding two hard links to one file
    src_dir, dest1, _ = filesystem
    (src_dir / "dir1" / "foo.txt").write_text("shared")
    (src_dir / "zlink.txt").hardlink_to(src_dir / "dir1" / "foo.txt")
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])

    # When backing up
    backups = app.create_backup()

    # Then the second link is a link member pointing at the first
    assert backups[0].path is not None
    with tarfile.open(backups[0].path) as tar:
        link = tar.getmember("src/zlink.txt")
        assert link.islnk()
        assert link.linkname == "src/dir1/foo.txt"


def _make_fifo(name: str) -> None:
    os.mkfifo(name)


def _make_socket(name: str) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.bind(name)


@pytest.mark.parametrize("make_special", [_make_fifo, _make_socket], ids=["fifo", "socket"])
def test_backup_skips_special_files_so_restore_succeeds(
    tmp_path, restore_dir, monkeypatch, make_special
):
    """Verify a FIFO or socket in a source is skipped with a warning and the backup restores."""
    # Given a source holding a regular file and a special file
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "data.txt").write_text("payload")
    # Create by relative name: an absolute tmp path can exceed the AF_UNIX path limit
    monkeypatch.chdir(src_dir)
    make_special("special")
    dest = tmp_path / "dest"
    dest.mkdir()
    log_file = tmp_path / "ezbak.log"
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest], log_file=log_file)

    # When backing up and restoring
    backups = app.create_backup()
    outcome = app.restore_backup(restore_dir)

    # Then the backup warns about the skipped file, and the restore brings back only the data
    assert len(backups) == 1
    assert f"{SKIPPED_SPECIAL_FILE_WARNING}: {src_dir / 'special'}" in log_file.read_text()
    assert outcome == RestoreOutcome.RESTORED
    assert (restore_dir / "src" / "data.txt").read_text() == "payload"
    assert not (restore_dir / "src" / "special").exists()


@pytest.mark.parametrize("make_special", [_make_fifo, _make_socket], ids=["fifo", "socket"])
def test_create_backup_skips_a_file_swapped_for_a_special_file_mid_walk(
    tmp_path, monkeypatch, make_special
):
    """Verify a file replaced by a FIFO or socket after it is listed is left out of the archive."""
    # Given a source file that is swapped for a special file just before it is opened
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "data.txt").write_text("payload")
    victim = src_dir / "victim.txt"
    victim.write_text("plain")
    dest = tmp_path / "dest"
    dest.mkdir()
    # Create by relative name: an absolute tmp path can exceed the AF_UNIX path limit
    monkeypatch.chdir(src_dir)
    real_open = os.open

    def swap_then_open(
        path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if path == victim.name and dir_fd is not None and victim.is_file():
            victim.unlink()
            make_special(victim.name)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_then_open)
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest])

    # When backing up
    backups = app.create_backup()
    monkeypatch.undo()

    # Then the swapped entry is left out and the rest of the source is archived
    assert not victim.is_file()
    assert backups[0].path is not None
    with tarfile.open(backups[0].path) as tar:
        assert tar.getnames() == ["src/data.txt"]


def test_restore_skips_a_fifo_member_in_a_stored_archive(tmp_path, restore_dir):
    """Verify an archive that holds a FIFO member restores its files and leaves the FIFO out."""
    # Given a stored archive holding a regular file and a FIFO member
    storage = tmp_path / "storage"
    storage.mkdir()
    archive = storage / "test-20250624T084727.tgz"
    payload = b"payload"
    with tarfile.open(archive, "w:gz") as tar:
        data = tarfile.TarInfo("src/data.txt")
        data.size = len(payload)
        tar.addfile(data, io.BytesIO(payload))
        pipe = tarfile.TarInfo("src/pipe")
        pipe.type = tarfile.FIFOTYPE
        tar.addfile(pipe)
    app = ezbak(name="test", storage_paths=[storage])

    # When restoring
    outcome = app.restore_backup(restore_path=restore_dir)

    # Then the file is restored and the FIFO is not
    assert outcome == RestoreOutcome.RESTORED
    assert (restore_dir / "src" / "data.txt").read_bytes() == payload
    assert not (restore_dir / "src" / "pipe").exists()


@pytest.mark.parametrize(
    ("dropped_name", "overrides"),
    [(".DS_Store", {}), ("a.txt", {"exclude_regex": r"/a\.txt$"})],
    ids=["always-excluded", "exclude-regex"],
)
def test_restore_keeps_content_when_first_hardlink_name_is_filtered(
    filesystem, restore_dir, dropped_name, overrides
):
    """Verify a file restores with its content when the filters drop its first-walked link."""
    # Given a file whose other hard link sorts first in the walk and is filtered out
    src_dir, dest1, _ = filesystem
    (src_dir / "kept.txt").write_text("shared content")
    (src_dir / dropped_name).hardlink_to(src_dir / "kept.txt")
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1], **overrides)

    # When backing up and restoring
    app.create_backup()
    app.restore_backup(restore_path=restore_dir)

    # Then the kept name restores with the file's content and the dropped name does not
    restored = restore_dir / src_dir.name
    assert (restored / "kept.txt").read_text() == "shared content"
    assert not (restored / dropped_name).exists()


@pytest.mark.parametrize("with_sidecar", [True, False], ids=["checksum-verified", "no-sidecar"])
def test_restore_fails_cleanly_on_a_hardlink_to_a_missing_member(
    tmp_path, restore_dir, with_sidecar
):
    """Verify an archive whose hard link names an absent member fails as RestoreFailedError."""
    # Given a stored archive holding a hard link whose target member is not in the archive
    storage = tmp_path / "storage"
    storage.mkdir()
    archive = storage / "test-20250624T084727.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        link = tarfile.TarInfo("src/data.txt")
        link.type = tarfile.LNKTYPE
        link.linkname = "src/.DS_Store"
        tar.addfile(link)
    if with_sidecar:
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (storage / sidecar_name(archive.name)).write_text(
            format_sidecar(digest=digest, archive_name=archive.name)
        )
    app = ezbak(name="test", storage_paths=[storage])

    # When/Then the restore fails as a restore failure and leaves the destination untouched
    with pytest.raises(RestoreFailedError, match="hard link"):
        app.restore_backup(restore_path=restore_dir)
    assert not any(restore_dir.iterdir())


def test_delete_source_after_backup_keeps_a_file_replaced_after_archiving(
    tmp_path, filesystem, mocker
):
    """Verify a file source swapped for another file during the run is not deleted.

    Deletion must remove only what was archived, so a replacement is left for the next run.
    """
    # Given a file source that is replaced while the archive is being written to storage
    _, dest1, _ = filesystem
    source = tmp_path / "data.txt"
    source.write_text("archived")
    app = ezbak(
        name="test", source_paths=[source], storage_paths=[dest1], delete_source_after_backup=True
    )
    write_to_backends = app._write_to_backends

    def replace_then_write(
        tmp_backup: Path, checksum: str | None
    ) -> tuple[list[Backup], list[str]]:
        source.unlink()
        source.write_text("replacement")
        return write_to_backends(tmp_backup, checksum)

    mocker.patch.object(app, "_write_to_backends", side_effect=replace_then_write)

    # When backing up
    app.create_backup()

    # Then the replacement survives
    assert source.read_text() == "replacement"


def test_create_writes_local_sidecar(filesystem) -> None:
    """Verify create_backup writes a .sha256 sidecar next to the local archive."""
    # Given: A backup manager configured with a single local destination
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])

    # When: Creating a backup
    backups = app.create_backup()

    # Then: A sidecar file exists next to the archive and matches its digest
    archive = backups[0].path
    assert archive is not None
    sidecar = archive.parent / (archive.name + ".sha256")
    assert sidecar.exists()
    assert hashlib.sha256(archive.read_bytes()).hexdigest() in sidecar.read_text()


def test_create_no_checksum_when_disabled(filesystem) -> None:
    """Verify no sidecar is written when use_checksums is disabled."""
    # Given: A backup manager with checksums disabled
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1], use_checksums=False)

    # When: Creating a backup
    app.create_backup()

    # Then: No sidecar file is created
    assert not list(dest1.glob("*.sha256"))


def _corrupt_archive_bytes(archive: Path) -> None:
    """Overwrite the start of `archive` so its bytes no longer match the sidecar digest."""
    with archive.open("r+b") as handle:
        handle.write(b"\x00\x01\x02\x03")


def _write_wrong_sidecar_digest(archive: Path) -> None:
    """Rewrite the sidecar to a well-formed digest that cannot match `archive`."""
    sidecar = archive.parent / (archive.name + ".sha256")
    sidecar.write_text(f"{'0' * 64}  {archive.name}\n")


@pytest.mark.parametrize(
    "tamper",
    [_corrupt_archive_bytes, _write_wrong_sidecar_digest],
    ids=["corrupt-archive-bytes", "wrong-sidecar-digest"],
)
def test_restore_rejects_digest_mismatch(filesystem, backed_up, restore_dir, tamper) -> None:
    """Verify an archive that does not match its sidecar digest fails before the live dir is touched."""
    # Given: a backup whose archive and sidecar disagree
    _, dest1, _ = filesystem
    app = backed_up()
    tamper(next(dest1.glob("*.tgz")))

    # When/Then: the restore fails and leaves the destination untouched
    with pytest.raises(RestoreFailedError, match="Checksum mismatch"):
        app.restore_backup(restore_path=restore_dir)
    assert not any(restore_dir.iterdir())


def test_restore_skips_verification_when_checksums_disabled(
    filesystem, backed_up, restore_dir
) -> None:
    """Verify restore ignores the sidecar entirely when use_checksums is disabled."""
    # Given: a backup with an intact archive but a sidecar whose digest cannot match
    src_dir, dest1, _ = filesystem
    backed_up()
    _write_wrong_sidecar_digest(next(dest1.glob("*.tgz")))

    # Given: a manager with checksums disabled
    app_off = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1], use_checksums=False)

    # When/Then: the mismatching sidecar is ignored and the restore succeeds
    assert app_off.restore_backup(restore_path=restore_dir) is RestoreOutcome.RESTORED


def _delete_sidecar(sidecar: Path) -> None:
    """Remove the checksum sidecar."""
    sidecar.unlink()


def _write_non_utf8_sidecar(sidecar: Path) -> None:
    """Replace the checksum sidecar with bytes that do not decode as UTF-8."""
    sidecar.write_bytes(b"\xff\xfe\x00\x01garbage")


@pytest.mark.parametrize(
    "spoil",
    [_delete_sidecar, _write_non_utf8_sidecar],
    ids=["missing-sidecar", "non-utf8-sidecar"],
)
def test_restore_proceeds_without_a_usable_sidecar(
    filesystem, backed_up, restore_dir, spoil
) -> None:
    """Verify a restore proceeds unverified when the checksum sidecar is missing or unreadable."""
    # Given: a backup whose sidecar cannot be used
    src_dir, dest1, _ = filesystem
    app = backed_up()
    spoil(next(dest1.glob("*.sha256")))

    # When: restoring
    outcome = app.restore_backup(restore_path=restore_dir)

    # Then: the restore succeeds with every file
    assert outcome is RestoreOutcome.RESTORED
    assert relative_tree(restore_dir / src_dir.name) == relative_tree(src_dir)


def test_local_prune_deletes_sidecar(filesystem, backed_up) -> None:
    """Verify prune removes a pruned archive's sidecar, leaving none orphaned."""
    _, dest1, _ = filesystem
    app = backed_up(keep_last=1)
    app.create_backup()
    app.prune_backups()

    assert len(list(dest1.glob("*.tgz"))) == 1
    assert len(list(dest1.glob("*.sha256"))) == 1
