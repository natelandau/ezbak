"""Domain exceptions raised by ezbak.

Entrypoints (CLI, container) catch these to translate library failures into
clean, non-zero exits without importing storage-backend libraries such as
botocore.
"""

from __future__ import annotations


class EZBakError(Exception):
    """Base class for every error ezbak raises."""


class StorageInitError(EZBakError):
    """A configured storage destination could not be initialized.

    Covers missing or invalid credentials and unreachable buckets.
    """


class StorageWriteError(EZBakError):
    """A storage backend failed to write an archive.

    Backends translate their low-level errors into this so orchestration code
    catches a single domain type instead of backend-specific exceptions.
    """


class BackupFailedError(EZBakError):
    """One or more configured destinations could not be backed up.

    Destinations that succeeded are already written; this signals callers to
    exit non-zero because the run did not fully succeed.
    """

    def __init__(self, failed_destinations: list[str]) -> None:
        """Build a message naming every destination that failed.

        Args:
            failed_destinations (list[str]): Human-readable descriptions of the destinations that failed.
        """
        self.failed_destinations = failed_destinations
        destinations = ", ".join(failed_destinations)
        super().__init__(f"Backup failed for destination(s): {destinations}")
