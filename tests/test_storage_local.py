"""Tests for the local-filesystem storage backend."""

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import pytest
import time_machine

from ezbak import ezbak
from ezbak.checksums import sidecar_name
from ezbak.constants import DEFAULT_DATE_FORMAT
from ezbak.exceptions import RestoreFailedError, StorageWriteError
from ezbak.naming import build_backup_name
from ezbak.storage import local as local_module
from ezbak.storage.local import copy_with_periodic_fsync
from tests.helpers import SEEDED_MARKER, seed_backups

FROZEN_TIME = datetime(2025, 6, 9, tzinfo=UTC)


@pytest.mark.parametrize("content", [b"x" * 1_000, b""], ids=["1kb", "empty"])
def test_copy_with_periodic_fsync_copies_content_and_mode(tmp_path, content):
    """Verify the chunked copy reproduces the source content and permissions."""
    # Given a source file with known content and a non-default mode
    src = tmp_path / "src.tgz"
    src.write_bytes(content)
    src.chmod(0o640)
    dst = tmp_path / "dst.tgz"

    # When copying
    copy_with_periodic_fsync(src=src, dst=dst)

    # Then the destination matches the source byte-for-byte and mode-for-mode
    assert dst.read_bytes() == src.read_bytes()
    assert dst.stat().st_mode & 0o777 == 0o640


def test_copy_with_periodic_fsync_fsyncs_each_interval(tmp_path, mocker):
    """Verify fsync fires once per interval so dirty pages stay bounded."""
    # Given a source file spanning multiple fsync intervals
    src = tmp_path / "src.tgz"
    src.write_bytes(bytes(10 * 1024))
    dst = tmp_path / "dst.tgz"
    fsync_spy = mocker.spy(os, "fsync")

    # When copying with a 4KB interval
    copy_with_periodic_fsync(src=src, dst=dst, fsync_interval=4 * 1024, chunk_size=1024)

    # Then fsync ran at each interval boundary plus once at the end
    assert fsync_spy.call_count >= 3
    assert dst.read_bytes() == src.read_bytes()


def test_copy_with_periodic_fsync_failure_leaves_no_partial_archive(tmp_path, mocker):
    """Verify a copy that fails mid-write publishes nothing under the backup name."""
    # Given a source file and an fsync that fails partway through the copy
    src = tmp_path / "src.tgz"
    src.write_bytes(bytes(10 * 1024))
    dst = tmp_path / "dst.tgz"
    mocker.patch("ezbak.storage.local.os.fsync", autospec=True, side_effect=OSError("stale handle"))

    # When copying, then the error propagates
    with pytest.raises(OSError, match="stale handle"):
        copy_with_periodic_fsync(src=src, dst=dst, fsync_interval=4 * 1024, chunk_size=1024)

    # Then no truncated archive or staging litter remains, only the source
    assert list(tmp_path.iterdir()) == [src]


def test_local_backend_write_fsyncs_archive(filesystem, mocker):
    """Verify writing a backup to a local destination fsyncs the copy."""
    # Given an ezbak app with a local destination and a staged archive
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    backend = app.backends[0]
    location = app.storage_locations[0]
    tmp_backup = app.tmp_dir / "staged.tgz"
    tmp_backup.write_bytes(b"data")
    fsync_spy = mocker.spy(os, "fsync")

    # When writing the backup
    backup = backend.write(tmp_backup=tmp_backup, storage_location=location, checksum=None)

    # Then the stored archive was fsynced and matches the staged bytes
    assert fsync_spy.call_count >= 1
    assert backup.path is not None
    assert backup.path.read_bytes() == b"data"


@pytest.mark.parametrize("operation", ["create", "restore"], ids=["create", "restore"])
def test_missing_storage_directory_is_created_on_first_use(filesystem, restore_dir, operation):
    """Verify a storage path that does not exist yet is created instead of failing the run."""
    # Given a storage path that does not exist yet
    src_dir, dest1, _ = filesystem
    new_dest = dest1 / "new_dir"
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[new_dest])

    # When the first backup or restore runs
    if operation == "create":
        app.create_backup()
    else:
        app.restore_backup(restore_path=restore_dir)

    # Then the directory exists
    assert new_dest.is_dir()


def test_local_backend_write_raises_storage_write_error(filesystem, mocker):
    """Verify LocalBackend.write raises StorageWriteError when the copy fails."""
    # Given an ezbak app with a local destination
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    backend = app.backends[0]
    location = app.storage_locations[0]
    tmp_backup = app.tmp_dir / "staged.tgz"
    tmp_backup.write_bytes(b"data")

    # Given the underlying copy fails
    mocker.patch("ezbak.storage.local.copy_with_periodic_fsync", side_effect=OSError("disk full"))

    # When writing, then a StorageWriteError is raised
    with pytest.raises(StorageWriteError, match="Local write failed"):
        backend.write(tmp_backup=tmp_backup, storage_location=location, checksum=None)


def _victim(tmp_path: Path) -> Path:
    """Create a 0600 file outside storage for a planted link to point at.

    Returns:
        Path: The victim file.
    """
    victim = tmp_path / "victim"
    victim.write_text("SECRET")
    victim.chmod(0o600)
    return victim


@time_machine.travel(FROZEN_TIME, tick=False)
def test_sidecar_write_replaces_planted_symlink(filesystem, tmp_path):
    """Verify a symlink planted at the sidecar name is replaced, not written through."""
    # Given a symlink planted at the name the next backup's sidecar will take
    src_dir, dest1, _ = filesystem
    victim = _victim(tmp_path)
    archive_name = build_backup_name(
        name="test", timestamp=FROZEN_TIME.strftime(DEFAULT_DATE_FORMAT)
    )
    sidecar = dest1 / sidecar_name(archive_name)
    sidecar.symlink_to(victim)
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1], tz="Etc/UTC")

    # When creating a backup
    app.create_backup()

    # Then the victim is untouched and the sidecar is a fresh regular file for the archive
    assert victim.read_text() == "SECRET"
    assert not sidecar.is_symlink()
    assert archive_name in sidecar.read_text()


def test_copy_refuses_staging_file_swapped_for_symlink(tmp_path, mocker):
    """Verify a staging file swapped for a link fails the copy without touching the link target."""
    # Given a chunk writer after which the staging file is replaced by a link to a victim
    victim = _victim(tmp_path)
    storage = tmp_path / "storage"
    storage.mkdir()
    src = tmp_path / "src.tgz"
    src.write_bytes(b"data")
    src.chmod(0o644)
    dst = storage / "test-20250609T000000.tgz"
    staging_glob = f".{dst.name}.*.partial"
    real_writer = local_module._write_chunks_with_fsync

    def write_then_swap(
        *, src: BinaryIO, dst: BinaryIO, fsync_interval: int, chunk_size: int
    ) -> None:
        real_writer(src=src, dst=dst, fsync_interval=fsync_interval, chunk_size=chunk_size)
        (staging,) = storage.glob(staging_glob)
        staging.unlink()
        staging.symlink_to(victim)

    mocker.patch.object(local_module, "_write_chunks_with_fsync", side_effect=write_then_swap)

    # When copying, then the copy fails
    with pytest.raises(OSError, match="not the file ezbak wrote"):
        copy_with_periodic_fsync(src=src, dst=dst)

    # Then the victim keeps its mode and content, and nothing is published in storage
    assert victim.stat().st_mode & 0o777 == 0o600
    assert victim.read_text() == "SECRET"
    assert list(storage.iterdir()) == []


def test_copy_keeps_concurrent_writers_archive(tmp_path, mocker):
    """Verify a copy that loses a same-name race fails without deleting the winner's archive."""
    # Given another writer that publishes the same name between this copy's rename and check
    src = tmp_path / "src.tgz"
    src.write_bytes(b"mine")
    dst = tmp_path / "test-20250609T000000.tgz"
    peer = tmp_path / ".peer.partial"
    real_check = local_module._require_published_file

    def peer_publishes_first(*, dst: Path, written: os.stat_result) -> None:
        peer.write_bytes(b"theirs")
        peer.replace(dst)
        real_check(dst=dst, written=written)

    mocker.patch.object(local_module, "_require_published_file", side_effect=peer_publishes_first)

    # When copying, then the copy fails
    with pytest.raises(OSError, match="not the file ezbak wrote"):
        copy_with_periodic_fsync(src=src, dst=dst)

    # Then the other writer's archive stays published
    assert dst.read_bytes() == b"theirs"


def test_index_skips_symlinked_archives(filesystem, tmp_path):
    """Verify an archive name that is a symlink is neither listed nor restored."""
    # Given a real backup and a newer-named symlink to an archive outside storage
    src_dir, dest1, _ = filesystem
    outside = tmp_path / "outside"
    outside.mkdir()
    seed_backups(dest1, ["test-20250101T000000.tgz"])
    seed_backups(outside, ["test-20991231T000000.tgz"])
    (dest1 / "test-20991231T000000.tgz").symlink_to(outside / "test-20991231T000000.tgz")
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])

    # When listing backups
    names = [b.name for b in app.list_backups()]

    # Then only the regular file is a backup
    assert names == ["test-20250101T000000.tgz"]


def test_restore_refuses_archive_swapped_for_symlink(filesystem, tmp_path, restore_dir):
    """Verify a restore fails when the indexed archive becomes a symlink before it is read."""
    # Given an indexed backup whose file is then replaced by a link to an outside archive
    src_dir, dest1, _ = filesystem
    outside = tmp_path / "outside"
    outside.mkdir()
    seed_backups(dest1, ["test-20250101T000000.tgz"])
    seed_backups(outside, ["test-20250101T000000.tgz"])
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1], use_checksums=False)
    (backup,) = app.list_backups()
    (dest1 / backup.name).unlink()
    (dest1 / backup.name).symlink_to(outside / backup.name)

    # When restoring that backup, then the restore fails and nothing is extracted
    with pytest.raises(RestoreFailedError):
        app.restore_backup(restore_path=restore_dir, backup=backup)
    assert not (restore_dir / SEEDED_MARKER).exists()


@pytest.mark.parametrize("planted", ["symlink", "fifo"])
def test_sidecar_read_ignores_non_regular_file(filesystem, tmp_path, planted):
    """Verify a sidecar that is a symlink or FIFO is treated as unreadable, not followed or awaited."""
    # Given a backup whose sidecar name is a symlink to a valid sidecar, or a FIFO
    src_dir, dest1, _ = filesystem
    seed_backups(dest1, ["test-20250101T000000.tgz"])
    sidecar = dest1 / sidecar_name("test-20250101T000000.tgz")
    if planted == "symlink":
        elsewhere = tmp_path / "elsewhere.sha256"
        elsewhere.write_text(f"{'0' * 64}  test-20250101T000000.tgz\n")
        sidecar.symlink_to(elsewhere)
    else:
        os.mkfifo(sidecar)
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    (backup,) = app.list_backups()

    # When reading its checksum, then there is none
    assert app.backends[0].get_checksum(backup) is None
