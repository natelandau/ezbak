"""Tests for the local-filesystem storage backend."""

import os

import pytest

from ezbak import ezbak
from ezbak.storage.local import copy_with_periodic_fsync


def test_copy_with_periodic_fsync_copies_content_and_mode(tmp_path):
    """Verify the chunked copy reproduces the source content and permissions."""
    # Given a source file with known content and a non-default mode
    src = tmp_path / "src.tgz"
    src.write_bytes(b"x" * 1_000)
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


def test_copy_with_periodic_fsync_empty_file(tmp_path):
    """Verify a zero-byte source copies cleanly without error."""
    # Given an empty source file
    src = tmp_path / "src.tgz"
    src.touch()
    dst = tmp_path / "dst.tgz"

    # When copying
    copy_with_periodic_fsync(src=src, dst=dst)

    # Then the destination exists and is empty
    assert dst.read_bytes() == b""


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
    assert backup.path.read_bytes() == b"data"
