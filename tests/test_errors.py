"""Test EZBak errors."""

import pytest
from pydantic import ValidationError

from ezbak import EZBak, ezbak
from ezbak.backup import Backup
from ezbak.config import BackupConfig
from ezbak.constants import StorageType
from ezbak.exceptions import (
    BackendNotFoundError,
    BackupFailedError,
    ConfigurationError,
    RestoreFailedError,
    StorageWriteError,
)


def test_no_name(filesystem):
    """Verify building an EZBak without a name is rejected."""
    # Given source and destination directories
    src_dir, dest1, _ = filesystem

    # When building without a name, then a validation error is raised
    with pytest.raises(ValidationError, match="No backup name provided"):
        ezbak(
            # name="test",
            source_paths=[src_dir],
            storage_paths=[dest1],
        )


def test_source_paths(filesystem):
    """Test EZBak errors."""
    _, dest1, _ = filesystem
    backup_manager = ezbak(
        name="test",
        source_paths=[],
        storage_paths=[dest1],
    )
    with pytest.raises(ConfigurationError, match="No source paths provided"):
        backup_manager.create_backup()


def test_source_paths_not_found(filesystem):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir / "not_found"],
        storage_paths=[dest1],
    )
    with pytest.raises(ConfigurationError, match="Source does not exist"):
        backup_manager.create_backup()


def test_source_paths_symlink(tmp_path, capsys, debug):
    """Test EZBak errors."""
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "file.txt").touch()
    (src_dir / "symlink").symlink_to(src_dir / "file.txt")

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir / "symlink"],
        storage_paths=[dest_dir],
    )
    with pytest.raises(ConfigurationError, match="Not a file or directory"):
        backup_manager.create_backup()


def test_storage_paths(filesystem):
    """Test EZBak errors."""
    src_dir, _, _ = filesystem
    with pytest.raises(ValueError, match="No storage configured"):
        ezbak(
            name="test",
            source_paths=[src_dir],
            storage_paths=[],
        )


def test_create_storage_path_dir(filesystem):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem

    new_dest = dest1 / "new_dir"
    assert not new_dest.exists()

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[new_dest],
    )
    backup_manager.create_backup()

    assert new_dest.exists()
    assert new_dest.is_dir()


def test_restore_no_dest(filesystem, tmp_path, debug, capsys):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
    )
    with pytest.raises(ConfigurationError, match="Restore path does not exist"):
        backup_manager.restore_backup(tmp_path / "new_dest")


def test_restore_dest_not_dir(filesystem, tmp_path, debug, capsys):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem

    new_dest = dest1 / "file.txt"
    new_dest.touch()

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
    )
    backup_manager.create_backup()
    with pytest.raises(ConfigurationError, match="Restore path does not exist"):
        backup_manager.restore_backup(new_dest)


def test_restore_no_backup(filesystem, tmp_path, debug, capsys):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        log_level="DEBUG",
    )
    # backup_manager.create_backup()
    assert not backup_manager.restore_backup(tmp_path)
    output = capsys.readouterr().err
    # debug(output)
    assert "ERROR    | No backup found to restore" in output


def test_no_restore_destination(filesystem, tmp_path, debug, capsys):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
    )
    with pytest.raises(ConfigurationError, match="Invalid restore path: None"):
        backup_manager.restore_backup(None)


def test_delete_unmapped_backend_raises_clear_error(filesystem):
    """Verify deleting a backup whose backend is not configured fails loudly."""
    # Given an app with only a local backend
    src, dest1, _ = filesystem
    app = ezbak(name="t", source_paths=[src], storage_paths=[dest1])

    # And a backup tagged for a backend that was never built
    orphan = Backup(name="t-20200101T000000-daily.tgz", storage_type=StorageType.AWS)

    # When attempting to delete it, then a clear error names the missing backend
    with pytest.raises(BackendNotFoundError, match="No configured backend for storage type: aws"):
        app._delete_backup(orphan)


def test_restore_backup_missing_local_storage_path(filesystem, tmp_path):
    """Verify restoring with a missing local storage path fails gracefully and indexes it."""
    # Given an app whose storage path does not exist on disk yet
    src, dest1, _ = filesystem
    missing_storage_path = dest1 / "not_yet_created"
    app = ezbak(name="t", source_paths=[src], storage_paths=[missing_storage_path])

    # When restoring to an existing directory
    result = app.restore_backup(restore_path=tmp_path)

    # Then no backup is found, but the storage path now exists (created during indexing)
    assert result is False
    assert missing_storage_path.exists()


def test_list_and_restore_without_source_paths(filesystem, tmp_path):
    """Verify listing and restoring do not require source paths."""
    # Given an app configured with no source paths (e.g. a container restore)
    _, dest1, _ = filesystem
    app = EZBak(BackupConfig(name="t", storage_paths=[dest1]))

    # When listing backups, then no error is raised for the missing source paths
    assert app.list_backups() == []

    # When restoring, then no backup is found and no "No source paths provided" error is raised
    assert app.restore_backup(restore_path=tmp_path) is False


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
    mocker.patch("ezbak.storage.local.copy_file", side_effect=OSError("disk full"))

    # When writing, then a StorageWriteError is raised
    with pytest.raises(StorageWriteError, match="Local write failed"):
        backend.write(tmp_backup=tmp_backup, storage_location=location, checksum=None)


def test_create_backup_s3_only_bad_credentials_raises(filesystem):
    """Verify an S3-only run with missing credentials fails instead of a silent success."""
    # Given an S3-only config with no credentials
    src_dir, _, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        aws_s3_bucket_name="test-bucket",
        aws_access_key="",
        aws_secret_key="",
    )

    # When creating a backup, then it raises rather than reporting a silent success
    with pytest.raises(BackupFailedError, match="S3 bucket 'test-bucket'"):
        app.create_backup()


def test_create_backup_partial_failure_attaches_created_backups(filesystem):
    """Verify a partial-destination failure still exposes the backups that succeeded."""
    # Given a healthy local destination alongside an S3 bucket with bad credentials
    src_dir, dest1, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        aws_s3_bucket_name="test-bucket",
        aws_access_key="",
        aws_secret_key="",
    )

    # When the backup partially fails
    with pytest.raises(BackupFailedError) as exc:
        app.create_backup()

    # Then only S3 is reported failed and the successful local backup is attached
    assert exc.value.failed_storage_locations == ["S3 bucket 'test-bucket'"]
    assert len(exc.value.created_backups) == 1
    assert exc.value.created_backups[0].storage_type == StorageType.LOCAL


def test_create_backup_keeps_source_when_destination_fails(filesystem):
    """Verify sources are not deleted when the only destination is unusable."""
    # Given an S3-only config with delete_source_after_backup and no credentials
    src_dir, _, _ = filesystem
    marker = src_dir / "keep.txt"
    marker.write_text("important")
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        aws_s3_bucket_name="test-bucket",
        aws_access_key="",
        aws_secret_key="",
        delete_source_after_backup=True,
    )

    # When the backup fails, then the source is left intact
    with pytest.raises(BackupFailedError):
        app.create_backup()
    assert marker.exists()


def test_create_backup_raises_when_archive_creation_fails(filesystem, mocker):
    """Verify create_backup fails loudly when the tmp archive cannot be built."""
    # Given an app with a valid local destination
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])

    # Given archive creation fails
    mocker.patch.object(app, "_create_tmp_backup_file", return_value=None)

    # When creating a backup, then it raises instead of returning silently
    with pytest.raises(BackupFailedError):
        app.create_backup()


def test_create_backup_uncreatable_local_path_fails_loudly(filesystem, mocker):
    """Verify an uncreatable local storage path fails cleanly instead of a raw OSError crash."""
    # Given a local destination whose directory cannot be created (e.g. a read-only mount)
    src_dir, dest1, _ = filesystem
    mocker.patch(
        "ezbak.core.validate_storage_paths",
        side_effect=OSError("Read-only file system"),
    )

    # When constructing EZBak, then it does not crash and registers no local backend
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    assert app.backends == []

    # When creating a backup, then it fails loudly instead of raising a raw OSError
    with pytest.raises(BackupFailedError):
        app.create_backup()


def test_restore_backup_raises_when_archive_corrupt(filesystem, tmp_path):
    """Verify a corrupt archive fails the restore loudly instead of a silent False."""
    # Given a valid backup that has since been corrupted on disk
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    app.create_backup()
    for archive in dest1.glob("test-*.tgz"):
        archive.write_bytes(b"not a tarball")

    # When restoring, then it raises rather than returning a silent failure. The
    # checksum sidecar written at backup time no longer matches the corrupted
    # bytes, so checksum verification now catches this before extraction is
    # ever attempted.
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    with pytest.raises(RestoreFailedError, match="Checksum mismatch"):
        app.restore_backup(restore_dir)


def test_restore_backup_raises_after_clean_when_archive_corrupt(filesystem, tmp_path):
    """Verify a failed restore with clean-before-restore fails loudly and leaves the destination untouched."""
    # Given a valid backup corrupted on disk and a restore target that was pre-populated
    src_dir, dest1, _ = filesystem
    app = ezbak(
        name="test", source_paths=[src_dir], storage_paths=[dest1], clean_before_restore=True
    )
    app.create_backup()
    for archive in dest1.glob("test-*.tgz"):
        archive.write_bytes(b"not a tarball")

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    existing_file = restore_dir / "existing.txt"
    existing_file.write_text("pre-existing")

    # When restoring, then it raises loudly, and the destination is never touched:
    # the corrupt archive fails to extract into staging before any clean/commit happens.
    with pytest.raises(RestoreFailedError):
        app.restore_backup(restore_dir)
    assert existing_file.exists()


def test_restore_backup_raises_when_archive_missing_from_storage(filesystem, tmp_path, mocker):
    """Verify a backup that vanished from storage fails the restore loudly."""
    # Given an app whose backend reports the archive is gone from storage
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    app.create_backup()
    mocker.patch(
        "ezbak.storage.local.LocalBackend.prepare_for_restore",
        return_value=None,
    )

    # When restoring, then it raises rather than reporting a silent failure
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    with pytest.raises(RestoreFailedError, match="missing from storage"):
        app.restore_backup(restore_dir)


def test_restore_backup_does_not_clean_when_no_backup(filesystem, tmp_path):
    """Verify clean_before_restore does not empty the target when there is no backup to restore."""
    # Given an app with no backups and a pre-populated restore target
    src_dir, dest1, _ = filesystem
    app = ezbak(
        name="test", source_paths=[src_dir], storage_paths=[dest1], clean_before_restore=True
    )
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    keep = restore_dir / "keep.txt"
    keep.write_text("important")

    # When restoring with no backup available, then it returns False without wiping the target
    assert app.restore_backup(restore_dir) is False
    assert keep.exists()


def test_restore_backup_unresolvable_destination_raises_configuration_error(
    filesystem, tmp_path, mocker
):
    """Verify a non-TypeError failure resolving the destination becomes a ConfigurationError."""
    # Given a destination whose resolution raises RuntimeError (e.g. an unresolvable ~ home)
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    mocker.patch(
        "ezbak.core.Path.expanduser",
        side_effect=RuntimeError("Could not determine home directory"),
    )

    # When restoring, then it surfaces a ConfigurationError, not a raw RuntimeError
    with pytest.raises(ConfigurationError, match="Invalid restore path"):
        app.restore_backup("~/restore")
