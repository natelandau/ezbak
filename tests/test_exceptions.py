"""Test ezbak domain exceptions."""

import pytest

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

    # Then it stores them and names them in the message
    assert error.failed_storage_locations == failed
    assert "S3 bucket 'backups'" in str(error)
    assert "dest1" in str(error)


@pytest.mark.parametrize(
    "error_type",
    [
        ConfigurationError,
        BackendNotFoundError,
        StorageInitError,
        StorageWriteError,
        StorageReadError,
        StorageDeleteError,
        BackupFailedError,
        RestoreFailedError,
    ],
    ids=lambda error_type: error_type.__name__,
)
def test_every_error_is_caught_as_ezbak_error(error_type: type[EZBakError]) -> None:
    """Verify every ezbak error derives from EZBakError, so one except clause catches any failure."""
    assert issubclass(error_type, EZBakError)
