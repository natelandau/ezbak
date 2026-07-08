"""Domain exceptions raised by ezbak.

Entrypoints (CLI, container) catch these to translate library failures into
clean, non-zero exits without importing storage-backend libraries such as
botocore.
"""

from __future__ import annotations


class EZBakError(Exception):
    """Base class for every error ezbak raises."""


class ConfigurationError(EZBakError):
    """A precondition on the configuration was not met.

    Covers missing source or storage paths and sources that do not exist, so a
    caller can distinguish a bad request from a runtime storage failure.
    """


class BackendNotFoundError(EZBakError):
    """No configured storage backend handles the requested storage type.

    An internal invariant failure: the routing tables and the configured
    backends have drifted out of sync.
    """


class StorageInitError(EZBakError):
    """A configured storage location could not be initialized.

    Covers missing or invalid credentials and unreachable buckets.
    """


class StorageWriteError(EZBakError):
    """A storage backend failed to write an archive.

    Backends translate their low-level errors into this so orchestration code
    catches a single domain type instead of backend-specific exceptions.
    """


class StorageReadError(EZBakError):
    """A storage backend failed to read an archive back for restore.

    Backends translate their low-level errors into this so orchestration code
    catches a single domain type instead of backend-specific exceptions.
    """


class StorageDeleteError(EZBakError):
    """A storage backend failed to delete an archive during pruning.

    Backends translate their low-level errors into this so orchestration code
    catches a single domain type instead of backend-specific exceptions.
    """


class BackupFailedError(EZBakError):
    """One or more configured storage locations could not be backed up.

    Storage locations that succeeded are already written; this signals callers to
    exit non-zero because the run did not fully succeed.
    """

    def __init__(self, failed_storage_locations: list[str]) -> None:
        """Build a message naming every storage location that failed.

        Args:
            failed_storage_locations (list[str]): Human-readable descriptions of the storage locations that failed.
        """
        self.failed_storage_locations = failed_storage_locations
        storage_locations = ", ".join(failed_storage_locations)
        super().__init__(f"Backup failed for storage location(s): {storage_locations}")


class RestoreFailedError(EZBakError):
    """A backup could not be restored.

    Covers a source archive that could not be downloaded or read and an archive
    that could not be extracted, so a failed restore never looks like a success.
    This is especially important after a clean-before-restore wiped the target.
    """
