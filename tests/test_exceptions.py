"""Test ezbak domain exceptions."""

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


def test_backup_failed_error_lists_destinations():
    """Verify BackupFailedError stores and names each failed destination."""
    # Given a set of failed destinations
    failed = ["S3 bucket 'backups'", "dest1"]

    # When the error is created
    error = BackupFailedError(failed)

    # Then it stores them and names them in the message
    assert error.failed_destinations == failed
    assert "S3 bucket 'backups'" in str(error)
    assert "dest1" in str(error)


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
