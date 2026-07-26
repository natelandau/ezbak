"""Test EZBak errors."""

import boto3
import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError

from ezbak import EZBak, ezbak
from ezbak.backup import Backup
from ezbak.config import BackupConfig
from ezbak.constants import RestoreOutcome, StorageType
from ezbak.exceptions import (
    BackendNotFoundError,
    BackupFailedError,
    ConfigurationError,
    RestoreFailedError,
    StorageInitError,
    StorageReadError,
    StorageWriteError,
)
from ezbak.storage.aws import AWSService


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
    assert backup_manager.restore_backup(tmp_path) is RestoreOutcome.NO_BACKUP
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
    assert result is RestoreOutcome.NO_BACKUP
    assert missing_storage_path.exists()


def test_list_and_restore_without_source_paths(filesystem, tmp_path):
    """Verify listing and restoring do not require source paths."""
    # Given an app configured with no source paths (e.g. a container restore)
    _, dest1, _ = filesystem
    app = EZBak(BackupConfig(name="t", storage_paths=[dest1]))

    # When listing backups, then no error is raised for the missing source paths
    assert app.list_backups() == []

    # When restoring, then no backup is found and no "No source paths provided" error is raised
    assert app.restore_backup(restore_path=tmp_path) is RestoreOutcome.NO_BACKUP


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
    """Verify a corrupt archive fails the restore loudly instead of a silent NO_BACKUP."""
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

    # When restoring with no backup available, then it returns NO_BACKUP without wiping the target
    assert app.restore_backup(restore_dir) is RestoreOutcome.NO_BACKUP
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


def test_list_objects_propagates_client_error(s3_bucket: str, mocker) -> None:
    """Verify a failed bucket listing raises instead of reporting an empty bucket."""
    # Given: a service whose paginator raises
    svc = AWSService(bucket_name=s3_bucket)
    mocker.patch.object(
        svc.s3,
        "get_paginator",
        side_effect=ClientError(
            error_response={"Error": {"Code": "AccessDenied", "Message": "denied"}},
            operation_name="ListObjectsV2",
        ),
    )

    # When listing objects, then the error surfaces rather than becoming []
    with pytest.raises(ClientError):
        svc.list_objects()


def test_s3_index_wraps_listing_failure(s3_bucket: str, filesystem, break_s3_listing) -> None:
    """Verify a failed listing becomes a StorageReadError at the backend boundary."""
    # Given: an S3-backed app whose listing fails
    src_dir, _, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], aws_s3_bucket_name=s3_bucket)
    break_s3_listing()
    backend = app.backends[0]

    # When indexing, then the botocore error is translated to the domain type
    with pytest.raises(StorageReadError):
        backend.index()


def test_create_backup_keeps_local_copy_when_s3_listing_fails(
    s3_bucket: str, filesystem, break_s3_listing
) -> None:
    """Verify a failed S3 listing still writes every destination and fails the run loudly."""
    # Given: a healthy local destination alongside an S3 bucket that cannot be listed
    src_dir, dest1, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        aws_s3_bucket_name=s3_bucket,
    )
    break_s3_listing()

    # When creating a backup
    with pytest.raises(BackupFailedError) as exc:
        app.create_backup()

    # Then S3 is reported failed and both copies were still written
    assert exc.value.failed_storage_locations == [f"S3 bucket '{s3_bucket}'"]
    assert {x.storage_type for x in exc.value.created_backups} == {
        StorageType.LOCAL,
        StorageType.AWS,
    }


def test_index_failures_reset_between_passes(s3_bucket: str, filesystem, break_s3_listing) -> None:
    """Verify a recovered destination clears the recorded failure on the next index."""
    # Given: an app whose S3 listing fails once, as a long-lived container would see
    src_dir, dest1, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        aws_s3_bucket_name=s3_bucket,
    )
    mock_list = break_s3_listing()
    with pytest.raises(BackupFailedError):
        app.create_backup()

    # When the destination recovers and a later scheduled run indexes again
    mock_list.side_effect = None
    mock_list.return_value = []

    # Then the stale failure is gone and the run succeeds
    assert app.create_backup()
    assert app._index_failures == []


def test_local_index_failure_is_recorded(filesystem, mocker) -> None:
    """Verify an unreadable local destination is recorded instead of raising an OSError."""
    # Given: a local-only app whose storage path becomes unusable after construction
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    mocker.patch(
        "ezbak.storage.local.validate_storage_paths",
        side_effect=OSError("permission denied"),
    )
    app.rebuild_storage_locations = True

    # When creating a backup, then the failure is reported as a storage location, not a
    # raw OSError escaping the run
    with pytest.raises(BackupFailedError, match="local storage paths"):
        app.create_backup()


def test_restore_refuses_when_destination_unreadable(
    s3_bucket: str, filesystem, tmp_path, break_s3_listing
) -> None:
    """Verify an unreadable destination fails a restore even with skip_if_no_backup set."""
    # Given: a stored backup, then a bucket that can no longer be listed, and the
    # fresh-deployment flag an orchestrated pre-start task would set
    src_dir, _, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], aws_s3_bucket_name=s3_bucket)
    app.create_backup()
    break_s3_listing()
    app.rebuild_storage_locations = True
    app.settings.skip_if_no_backup = True

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()

    # When restoring, then it fails instead of reporting a clean "nothing to restore"
    with pytest.raises(RestoreFailedError, match="Cannot determine available backups"):
        app.restore_backup(restore_path=restore_dir)


def test_restore_refuses_when_destination_unreadable_with_restore_date(
    s3_bucket: str, filesystem, tmp_path, break_s3_listing
) -> None:
    """Verify an unreadable destination fails a restore even with a restore_date configured."""
    # Given: a stored backup, then a bucket that can no longer be listed, and a
    # restore_date configured to select a point in time rather than the latest backup
    src_dir, _, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], aws_s3_bucket_name=s3_bucket)
    app.create_backup()
    break_s3_listing()
    app.rebuild_storage_locations = True
    app.settings.restore_date = "20250102"

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()

    # When restoring, then it fails before ever resolving the restore_date
    with pytest.raises(RestoreFailedError, match="Cannot determine available backups"):
        app.restore_backup(restore_path=restore_dir)


def test_restore_empty_bucket_still_reports_no_backup(s3_bucket: str, filesystem, tmp_path) -> None:
    """Verify a genuinely empty destination still reports NO_BACKUP, not a failure."""
    # Given: a readable but empty bucket, the real fresh-deployment case
    src_dir, _, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], aws_s3_bucket_name=s3_bucket)
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()

    # When restoring, then the fresh-deployment path is intact
    assert app.restore_backup(restore_path=restore_dir) is RestoreOutcome.NO_BACKUP


def test_restore_explicit_backup_skips_index_check(
    s3_bucket: str, filesystem, tmp_path, break_s3_listing
) -> None:
    """Verify an explicitly supplied backup restores without needing a complete index."""
    # Given: a stored backup held by the caller, then a bucket that cannot be listed
    src_dir, _, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], aws_s3_bucket_name=s3_bucket)
    backup = app.create_backup()[0]
    break_s3_listing()
    app.rebuild_storage_locations = True

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()

    # When restoring that specific backup, then no index is required
    assert app.restore_backup(restore_path=restore_dir, backup=backup) is RestoreOutcome.RESTORED
    assert (restore_dir / "src" / "foo.txt").exists()


def test_unreadable_locations_exposes_failed_index(
    s3_bucket: str, filesystem, break_s3_listing
) -> None:
    """Verify a failed index is observable through the public unreadable_locations property."""
    # Given: a local destination alongside a bucket that cannot be listed
    src_dir, dest1, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        aws_s3_bucket_name=s3_bucket,
    )
    break_s3_listing()

    # When listing backups
    app.list_backups()

    # Then the unreadable destination is reported, so a caller can tell the list is partial
    assert app.unreadable_locations == [f"S3 bucket '{s3_bucket}'"]


def test_unreadable_locations_empty_when_all_locations_readable(s3_bucket: str, filesystem) -> None:
    """Verify a healthy index reports no unreadable locations."""
    # Given: a readable bucket and local path
    src_dir, dest1, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        aws_s3_bucket_name=s3_bucket,
    )

    # When listing backups
    app.list_backups()

    # Then nothing is reported unreadable
    assert app.unreadable_locations == []


def test_unreadable_locations_returns_a_copy(s3_bucket: str, filesystem, break_s3_listing) -> None:
    """Verify mutating the returned list cannot corrupt the manager's internal state."""
    # Given: an app with one unreadable destination
    src_dir, _, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], aws_s3_bucket_name=s3_bucket)
    break_s3_listing()

    # When a caller mutates what it was handed
    app.unreadable_locations.clear()

    # Then the manager still knows the destination failed
    assert app.unreadable_locations == [f"S3 bucket '{s3_bucket}'"]


def test_restore_refuses_when_destination_failed_construction(
    s3_bucket: str, filesystem, tmp_path
) -> None:
    """Verify a destination that failed at construction fails a restore, not reports NO_BACKUP."""
    # Given: an app pointed at a bucket that does not exist, so the backend is never
    # built, plus the fresh-deployment flag an orchestrated pre-start task would set
    src_dir, _, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        aws_s3_bucket_name="missing-bucket",
        skip_if_no_backup=True,
    )
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()

    # When restoring, then it fails instead of reporting a clean "nothing to restore"
    with pytest.raises(RestoreFailedError, match="Cannot determine available backups"):
        app.restore_backup(restore_path=restore_dir)


def test_create_backup_uploads_when_s3_listing_fails(
    s3_bucket: str, filesystem, break_s3_listing
) -> None:
    """Verify an S3-only run whose listing fails still uploads the archive and fails loudly."""
    # Given: an S3-only destination that can no longer be listed, as an S3-only
    # post-stop final backup with no retry would find it
    src_dir, _, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], aws_s3_bucket_name=s3_bucket)
    break_s3_listing()

    # When creating a backup
    with pytest.raises(BackupFailedError) as exc:
        app.create_backup()

    # Then the run names the unreadable destination and the archive still reached it
    assert exc.value.failed_storage_locations == [f"S3 bucket '{s3_bucket}'"]
    assert len(exc.value.created_backups) == 1
    keys = [
        x["Key"] for x in boto3.client("s3").list_objects_v2(Bucket=s3_bucket).get("Contents", [])
    ]
    assert any(key.startswith("test-") and key.endswith(".tgz") for key in keys)


def test_create_backup_names_a_failed_destination_once(
    s3_bucket: str, filesystem, break_s3_listing, mocker
) -> None:
    """Verify a destination that fails to index and to write is named once in the error."""
    # Given: a bucket that can neither be listed nor written
    src_dir, _, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], aws_s3_bucket_name=s3_bucket)
    break_s3_listing()
    mocker.patch.object(
        AWSService,
        "upload_object",
        autospec=True,
        side_effect=ClientError(
            error_response={"Error": {"Code": "AccessDenied", "Message": "denied"}},
            operation_name="PutObject",
        ),
    )

    # When creating a backup
    with pytest.raises(BackupFailedError) as exc:
        app.create_backup()

    # Then one destination failing once reads as one failure, not two
    assert exc.value.failed_storage_locations == [f"S3 bucket '{s3_bucket}'"]


def test_unusable_destination_is_rebuilt_after_it_recovers(
    s3_bucket: str, filesystem, mocker
) -> None:
    """Verify a destination that could not be constructed is retried on a later pass."""
    # Given: a bucket unreachable for the first two attempts, as a container starting
    # before its network or instance role is ready would see
    src_dir, _, _ = filesystem
    recovered = AWSService(bucket_name=s3_bucket)
    mocker.patch(
        "ezbak.core.AWSService",
        side_effect=[StorageInitError("unreachable"), StorageInitError("unreachable"), recovered],
    )
    app = ezbak(name="test", source_paths=[src_dir], aws_s3_bucket_name=s3_bucket)
    assert app.aws_service is None
    assert app.unreadable_locations == [f"S3 bucket '{s3_bucket}'"]

    # When the destination recovers and a later scheduled run indexes again
    # Then the backend is rebuilt rather than staying broken for the process's lifetime
    assert app.create_backup()
    assert app.unreadable_locations == []


def test_create_backup_does_not_report_a_stale_index_failure(
    s3_bucket: str, filesystem, break_s3_listing
) -> None:
    """Verify a backup run reports the index failures of its own pass, not a cached one."""
    # Given: an app that indexed while the bucket could not be listed
    src_dir, dest1, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        aws_s3_bucket_name=s3_bucket,
    )
    mock_list = break_s3_listing()
    assert app.unreadable_locations == [f"S3 bucket '{s3_bucket}'"]

    # When the bucket recovers before the backup runs
    mock_list.side_effect = None
    mock_list.return_value = []

    # Then the run indexes again and succeeds instead of failing on the cached record
    assert app.create_backup()
