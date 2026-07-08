"""Test ezbak domain exceptions."""

from ezbak.backup import Backup
from ezbak.constants import StorageType
from ezbak.exceptions import (
    BackendNotFoundError,
    BackupFailedError,
    ConfigurationError,
    EZBakError,
    RestoreFailedError,
    StorageDeleteError,
    StorageInitError,
    StorageReadError,
    StorageWriteError,
)


def test_backup_failed_error_lists_storage_locations():
    """Verify BackupFailedError stores and names each failed storage location."""
    # Given a set of failed storage locations
    failed = ["S3 bucket 'backups'", "dest1"]

    # When the error is created
    error = BackupFailedError(failed)

    # Then it stores them and names them in the message, and no backups are attached
    assert error.failed_storage_locations == failed
    assert "S3 bucket 'backups'" in str(error)
    assert "dest1" in str(error)
    assert error.created_backups == []


def test_backup_failed_error_carries_created_backups():
    """Verify BackupFailedError exposes the backups that succeeded before the failure."""
    # Given a backup that was written successfully before another destination failed
    created = [Backup(name="test-20240609T000000-yearly.tgz", storage_type=StorageType.LOCAL)]

    # When the error is created with those backups
    error = BackupFailedError(["S3 bucket 'backups'"], created_backups=created)

    # Then the successful backups are attached for the caller to observe
    assert error.created_backups == created


def test_exception_hierarchy():
    """Verify every ezbak error derives from EZBakError."""
    # Given the ezbak exception types
    # Then each subclasses the shared base
    assert issubclass(ConfigurationError, EZBakError)
    assert issubclass(BackendNotFoundError, EZBakError)
    assert issubclass(StorageInitError, EZBakError)
    assert issubclass(StorageWriteError, EZBakError)
    assert issubclass(StorageReadError, EZBakError)
    assert issubclass(StorageDeleteError, EZBakError)
    assert issubclass(BackupFailedError, EZBakError)
    assert issubclass(RestoreFailedError, EZBakError)
