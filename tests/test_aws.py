"""Test AWS backups."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import time_machine
from botocore.exceptions import ClientError, EndpointConnectionError

from ezbak import ezbak
from ezbak.backup import Backup
from ezbak.constants import DEFAULT_DATE_FORMAT, LogLevel, StorageType
from ezbak.exceptions import (
    BackupFailedError,
    RestoreFailedError,
    StorageDeleteError,
    StorageInitError,
    StorageReadError,
    StorageWriteError,
)
from ezbak.logging import instantiate_logger
from ezbak.storage.aws import AWSService

UTC = ZoneInfo("UTC")
frozen_time = datetime(2025, 6, 9, tzinfo=UTC)
frozen_time_str = frozen_time.strftime(DEFAULT_DATE_FORMAT)
fixture_archive_path = Path(__file__).parent / "fixtures" / "archive.tgz"


@pytest.fixture(autouse=True)
def mock_aws_client(mocker) -> None:
    """Mock AWS credentials."""
    mock_paginator = mocker.MagicMock()
    mock_paginator.paginate.return_value = [
        {"Contents": [{"Key": "test-20240609T000000-yearly.tgz"}]}
    ]

    mock_s3_client = mocker.MagicMock()
    mock_s3_client.get_bucket_location.return_value = {"LocationConstraint": "us-east-1"}
    mock_s3_client.get_paginator.return_value = mock_paginator
    mock_s3_client.delete_objects.return_value = {
        "Deleted": [{"Key": "test-20240609T000000-yearly.tgz"}],
        "Errors": [
            {"Key": "test-20240609T000000-yearly.tgz", "Code": "404", "Message": "Not Found"}
        ],
    }

    mocker.patch("ezbak.storage.aws.boto3.client", return_value=mock_s3_client)


@time_machine.travel(frozen_time, tick=False)
def test_aws_create_backup(
    filesystem: tuple[Path, Path, Path],
    debug: Callable[[str], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test AWS create backup."""
    # Given: Source and destination directories from fixture
    src_dir, _, _ = filesystem

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        log_level="trace",
        tz="Etc/UTC",
        aws_s3_bucket_name="test-bucket",
        aws_access_key="test-access-key-id",
        aws_secret_key="test-secret-access-key",
    )

    # When: Creating a backup
    backup_manager.create_backup()
    output = capsys.readouterr().err
    # debug(output)
    assert "test-20250609T000000.tgz" in output


def test_get_latest_backup(filesystem, debug, capsys, tmp_path):
    """Test AWS get latest backup."""
    # Given: Source and destination directories from fixture
    src_dir, _, _ = filesystem
    tmp_dst = tmp_path / "dst"
    tmp_dst.mkdir()
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        log_level="TRACE",
        tz="Etc/UTC",
        aws_s3_bucket_name="test-bucket",
        aws_access_key="test-access-key-id",
        aws_secret_key="test-secret-access-key",
    )

    # When: Restoring the latest backup
    # The aws service returns a file, but the restore fails loudly because the downloaded archive is not a valid tarball.
    with pytest.raises(RestoreFailedError, match="Failed to extract backup archive"):
        backup_manager.restore_backup(restore_dir)
    output = capsys.readouterr().err
    # debug(output)
    assert "Restoring backup: test-20240609T000000-yearly.tgz" in output
    assert "S3 file exists: 'test-20240609T000000-yearly.tgz'" in output


def test_delete_object(mocker, debug, capsys, tmp_path):
    """Verify the aws service deletes an object."""
    backup_manager = ezbak(
        name="test",
        source_paths=[tmp_path],
        log_level="TRACE",
        tz="Etc/UTC",
        aws_s3_bucket_name="test-bucket",
        aws_access_key="test-access-key-id",
        aws_secret_key="test-secret-access-key",
    )

    backup = Backup(name="test-20240609T000000-yearly.tgz", storage_type=StorageType.AWS)
    backup_manager._delete_backup(backup)
    output = capsys.readouterr().err
    # debug(output)
    assert "S3: Deleted test-20240609T000000-yearly.tgz" in output


def test_delete_objects(mocker, debug, capsys, tmp_path):
    """Verify the aws service deletes multiple objects."""
    # Given: A backup manager configured with test parameters
    instantiate_logger(LogLevel.TRACE)

    aws_service = AWSService(
        bucket_name="test-bucket",
        aws_access_key="test-access-key-id",
        aws_secret_key="test-secret-access-key",
    )

    # When: Deleting objects
    assert aws_service.delete_objects(
        ["test-20240609T000000-yearly.tgz", "test-20240609T000000-yearly.tgz"]
    ) == ["test-20240609T000000-yearly.tgz"]
    output = capsys.readouterr().err
    # debug(output)
    assert "S3: Attempting to delete 2 objects" in output
    assert "S3: Deleted test-20240609T000000-yearly.tgz" in output
    assert "S3: Failed to delete 'test-20240609T000000-yearly.tgz': 404 - Not Found" in output


def test_both_backends_configured(filesystem):
    """Verify an app with both local and S3 destinations builds one backend per type."""
    # Given source and local destination directories plus S3 bucket settings
    src_dir, dest1, _ = filesystem

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        aws_s3_bucket_name="test-bucket",
        aws_access_key="test-access-key-id",
        aws_secret_key="test-secret-access-key",
    )

    # When inspecting the derived backends
    types = {b.storage_type for b in backup_manager.backends}

    # Then both a local and an S3 backend exist
    assert types == {StorageType.LOCAL, StorageType.AWS}


def test_aws_service_missing_credentials():
    """Verify AWSService raises StorageInitError when credentials are missing."""
    # Given empty credentials
    # When constructing the service, then a StorageInitError is raised
    with pytest.raises(StorageInitError, match="AWS credentials are not set"):
        AWSService(
            aws_access_key="",
            aws_secret_key="",
            bucket_name="test-bucket",
        )


def test_aws_service_unreachable_bucket(mocker):
    """Verify AWSService raises StorageInitError when the bucket is unreachable."""
    # Given an S3 client whose bucket lookup fails
    mock_client = mocker.MagicMock()
    mock_client.get_bucket_location.side_effect = ClientError(
        error_response={"Error": {"Code": "AccessDenied", "Message": "denied"}},
        operation_name="GetBucketLocation",
    )
    mocker.patch("ezbak.storage.aws.boto3.client", return_value=mock_client)

    # When constructing the service, then a StorageInitError is raised (not SystemExit)
    with pytest.raises(StorageInitError, match="Cannot access S3 bucket"):
        AWSService(
            aws_access_key="key",
            aws_secret_key="secret",
            bucket_name="test-bucket",
        )


def test_aws_service_network_error_raises_storage_init_error(mocker):
    """Verify a network-level failure at init becomes a StorageInitError, not a raw crash."""
    # Given an S3 client whose bucket lookup hits a connectivity error (a BotoCoreError, not ClientError)
    mock_client = mocker.MagicMock()
    mock_client.get_bucket_location.side_effect = EndpointConnectionError(
        endpoint_url="https://s3.amazonaws.com"
    )
    mocker.patch("ezbak.storage.aws.boto3.client", return_value=mock_client)

    # When constructing the service, then it degrades to a StorageInitError
    with pytest.raises(StorageInitError, match="Cannot access S3 bucket"):
        AWSService(
            aws_access_key="key",
            aws_secret_key="secret",
            bucket_name="test-bucket",
        )


def test_s3_backend_write_network_error_raises_storage_write_error(filesystem, mocker):
    """Verify a network-level failure during upload becomes a StorageWriteError, not a raw crash."""
    # Given an ezbak app whose only destination is S3
    src_dir, _, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        aws_s3_bucket_name="test-bucket",
        aws_access_key="key",
        aws_secret_key="secret",
        tz="Etc/UTC",
    )
    backend = app.backends[0]
    location = app.storage_locations[0]
    tmp_backup = app.tmp_dir / "staged.tgz"
    tmp_backup.write_bytes(b"data")

    # Given the upload hits a connectivity error (a BotoCoreError, not ClientError)
    mocker.patch.object(
        backend.aws_service,
        "upload_object",
        side_effect=EndpointConnectionError(endpoint_url="https://s3.amazonaws.com"),
    )

    # When writing, then a StorageWriteError is raised
    with pytest.raises(StorageWriteError, match="S3 upload failed"):
        backend.write(tmp_backup=tmp_backup, storage_location=location)


def test_ezbak_init_local_backend_survives_bad_s3_credentials(filesystem):
    """Verify EZBak construction falls back to the local backend when S3 credentials are missing."""
    # Given a config with a local destination and unusable S3 credentials
    src_dir, dest1, _ = filesystem

    # When constructing EZBak, then it does not raise and keeps the local backend
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        aws_s3_bucket_name="test-bucket",
        aws_access_key="",
        aws_secret_key="",
    )

    # Then no S3 service was created but the local backend is present
    assert app.aws_service is None
    assert any(b.storage_type.value == "local" for b in app.backends)
    assert len(app.backends) == 1


def test_s3_backend_write_raises_storage_write_error(filesystem, mocker):
    """Verify S3Backend.write raises StorageWriteError when the upload fails."""
    # Given an ezbak app whose only destination is S3
    src_dir, _, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        aws_s3_bucket_name="test-bucket",
        aws_access_key="key",
        aws_secret_key="secret",
        tz="Etc/UTC",
    )
    backend = app.backends[0]
    location = app.storage_locations[0]
    tmp_backup = app.tmp_dir / "staged.tgz"
    tmp_backup.write_bytes(b"data")

    # Given the S3 upload fails
    mocker.patch.object(
        backend.aws_service,
        "upload_object",
        side_effect=ClientError(
            error_response={"Error": {"Code": "AccessDenied", "Message": "denied"}},
            operation_name="PutObject",
        ),
    )

    # When writing, then a StorageWriteError is raised
    with pytest.raises(StorageWriteError, match="S3 upload failed"):
        backend.write(tmp_backup=tmp_backup, storage_location=location)


def test_create_backup_local_written_when_s3_fails(filesystem, mocker):
    """Verify the local backup is written even when the S3 upload fails, then the run fails."""
    # Given a config with both local and S3 destinations
    src_dir, dest1, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        aws_s3_bucket_name="test-bucket",
        aws_access_key="key",
        aws_secret_key="secret",
        tz="Etc/UTC",
    )

    # Given the S3 upload fails
    mocker.patch.object(
        app.aws_service,
        "upload_object",
        side_effect=ClientError(
            error_response={"Error": {"Code": "AccessDenied", "Message": "denied"}},
            operation_name="PutObject",
        ),
    )

    # When creating a backup, then it fails loudly
    with pytest.raises(BackupFailedError) as exc_info:
        app.create_backup()

    # Then the local backup was still written and S3 was recorded as failed
    assert exc_info.value.failed_destinations
    assert list(dest1.glob("test-*.tgz"))


def test_s3_prepare_for_restore_network_error_raises_storage_read_error(filesystem, mocker):
    """Verify a network-level failure during download becomes a StorageReadError, not a raw crash."""
    # Given an S3-only app whose download hits a connectivity error (a BotoCoreError)
    src_dir, _, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        aws_s3_bucket_name="test-bucket",
        aws_access_key="key",
        aws_secret_key="secret",
        tz="Etc/UTC",
    )
    backend = app.backends[0]
    backup = Backup(name="test-20240609T000000-yearly.tgz", storage_type=StorageType.AWS)
    mocker.patch.object(
        backend.aws_service,
        "get_object",
        side_effect=EndpointConnectionError(endpoint_url="https://s3.amazonaws.com"),
    )

    # When preparing for restore, then a StorageReadError is raised
    with pytest.raises(StorageReadError, match="S3 download failed"):
        backend.prepare_for_restore(backup)


def test_s3_prepare_for_restore_client_error_raises_storage_read_error(filesystem, mocker):
    """Verify a download ClientError becomes a StorageReadError instead of leaking botocore."""
    # Given an S3-only app whose download is denied
    src_dir, _, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        aws_s3_bucket_name="test-bucket",
        aws_access_key="key",
        aws_secret_key="secret",
        tz="Etc/UTC",
    )
    backend = app.backends[0]
    backup = Backup(name="test-20240609T000000-yearly.tgz", storage_type=StorageType.AWS)
    mocker.patch.object(
        backend.aws_service,
        "get_object",
        side_effect=ClientError(
            error_response={"Error": {"Code": "AccessDenied", "Message": "denied"}},
            operation_name="GetObject",
        ),
    )

    # When preparing for restore, then a StorageReadError is raised
    with pytest.raises(StorageReadError, match="S3 download failed"):
        backend.prepare_for_restore(backup)


def test_s3_delete_client_error_raises_storage_delete_error(filesystem, mocker):
    """Verify a delete ClientError becomes a StorageDeleteError instead of leaking botocore."""
    # Given an S3-only app whose single-object delete is denied
    src_dir, _, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        aws_s3_bucket_name="test-bucket",
        aws_access_key="key",
        aws_secret_key="secret",
        tz="Etc/UTC",
    )
    backend = app.backends[0]
    backup = Backup(name="test-20240609T000000-yearly.tgz", storage_type=StorageType.AWS)
    mocker.patch.object(
        backend.aws_service,
        "delete_object",
        side_effect=ClientError(
            error_response={"Error": {"Code": "AccessDenied", "Message": "denied"}},
            operation_name="DeleteObject",
        ),
    )

    # When deleting, then a StorageDeleteError is raised
    with pytest.raises(StorageDeleteError, match="S3 delete failed"):
        backend.delete(backup)


def test_s3_delete_many_network_error_raises_storage_delete_error(filesystem, mocker):
    """Verify a batch-delete connectivity failure becomes a StorageDeleteError, not a raw crash."""
    # Given an S3-only app whose batch delete hits a connectivity error (a BotoCoreError)
    src_dir, _, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        aws_s3_bucket_name="test-bucket",
        aws_access_key="key",
        aws_secret_key="secret",
        tz="Etc/UTC",
    )
    backend = app.backends[0]
    backup = Backup(name="test-20240609T000000-yearly.tgz", storage_type=StorageType.AWS)
    mocker.patch.object(
        backend.aws_service,
        "delete_objects",
        side_effect=EndpointConnectionError(endpoint_url="https://s3.amazonaws.com"),
    )

    # When batch-deleting, then a StorageDeleteError is raised
    with pytest.raises(StorageDeleteError, match="S3 batch delete failed"):
        backend.delete_many([backup])


def test_prune_backups_tolerates_s3_delete_failure(filesystem, mocker):
    """Verify pruning tolerates a failing S3 backend instead of crashing the whole run."""
    # Given an S3-only app with a backup targeted for deletion
    src_dir, _, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        aws_s3_bucket_name="test-bucket",
        aws_access_key="key",
        aws_secret_key="secret",
        tz="Etc/UTC",
    )
    backup = Backup(name="test-20240609T000000-yearly.tgz", storage_type=StorageType.AWS)
    mocker.patch.object(app, "_identify_backups_to_delete", return_value=[backup])

    # Given the batch delete fails
    mocker.patch.object(
        app.aws_service,
        "delete_objects",
        side_effect=ClientError(
            error_response={"Error": {"Code": "AccessDenied", "Message": "denied"}},
            operation_name="DeleteObjects",
        ),
    )

    # When pruning, then it does not raise despite the backend failure
    assert app.prune_backups() == [backup]


def test_s3_delete_many_chunks_large_batches(filesystem, mocker):
    """Verify deleting more than 1000 backups is chunked instead of exceeding the S3 limit."""
    # Given an S3-only app and more than 1000 backups to delete
    src_dir, _, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        aws_s3_bucket_name="test-bucket",
        aws_access_key="key",
        aws_secret_key="secret",
        tz="Etc/UTC",
    )
    backend = app.backends[0]
    backups = [
        Backup(name=f"test-20240609T000000-{i:05d}.tgz", storage_type=StorageType.AWS)
        for i in range(1500)
    ]

    # Given delete_objects echoes back the keys it received
    delete_spy = mocker.patch.object(
        backend.aws_service, "delete_objects", side_effect=lambda keys: keys
    )

    # When deleting, then it issues two chunked requests (1000 + 500) and counts all deleted
    assert backend.delete_many(backups) == 1500
    assert delete_spy.call_count == 2
    assert len(delete_spy.call_args_list[0].kwargs["keys"]) == 1000
    assert len(delete_spy.call_args_list[1].kwargs["keys"]) == 500
