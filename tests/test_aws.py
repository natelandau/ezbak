"""Test AWS backups."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import time_machine

from ezbak import ezbak
from ezbak.backup import Backup
from ezbak.constants import DEFAULT_DATE_FORMAT, LogLevel, StorageType
from ezbak.logging import instantiate_logger
from ezbak.storage.aws import AWSService

UTC = ZoneInfo("UTC")
frozen_time = datetime(2025, 6, 9, tzinfo=UTC)
frozen_time_str = frozen_time.strftime(DEFAULT_DATE_FORMAT)
fixture_archive_path = Path(__file__).parent / "fixtures" / "archive.tgz"


@pytest.fixture(autouse=True)  # noqa: RUF076
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
    assert "test-20250609T000000-yearly.tgz" in output


@time_machine.travel(frozen_time, tick=False)
def test_aws_create_backup_no_labels(filesystem, debug, capsys, tmp_path):
    """Test AWS create backup."""
    # Given: Source and destination directories from fixture
    src_dir, _, _ = filesystem

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        label_time_units=False,
        log_level="TRACE",
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
    # Assert the aws service returns a file. However, the restore fails because the file does not actually exist.
    assert not backup_manager.restore_backup(restore_dir)
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


def test_rename_object(mocker, debug, capsys, tmp_path):
    """Verify the aws service renames a file."""
    # Given: A backup manager configured with test parameters
    instantiate_logger(LogLevel.TRACE)

    aws_service = AWSService(
        bucket_name="test-bucket",
        aws_access_key="test-access-key-id",
        aws_secret_key="test-secret-access-key",
    )
    aws_service.rename_object(
        current_name="test-20240609T000000-yearly.tgz", new_name="test-20240609T000000-yearly.tgz"
    )
    output = capsys.readouterr().err
    # debug(output)
    assert (
        "S3: Attempting to rename 'test-20240609T000000-yearly.tgz' to 'test-20240609T000000-yearly.tgz'"
        in output
    )
    assert (
        "S3: Copied 'test-20240609T000000-yearly.tgz' to 'test-20240609T000000-yearly.tgz'."
        in output
    )


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
