"""Abstract storage backend interface shared by all backup storage kinds."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from ezbak.backup import Backup, StorageLocation
from ezbak.config import BackupConfig
from ezbak.constants import StorageType


class StorageBackend(ABC):
    """Common interface for a single backup storage kind."""

    storage_type: ClassVar[StorageType]

    def __init__(self, settings: BackupConfig) -> None:
        """Store the settings shared by every storage operation.

        Args:
            settings (BackupConfig): The validated backup configuration.
        """
        self.settings = settings

    def _build_storage_location(
        self, *, storage_path: str | Path | None, backups: list[Backup]
    ) -> StorageLocation:
        """Assemble a StorageLocation with its backups sorted oldest to newest.

        Args:
            storage_path (str | Path | None): The path or bucket prefix the backups live under.
            backups (list[Backup]): The discovered backups to organize.

        Returns:
            StorageLocation: The populated, sorted storage location.
        """
        return StorageLocation(
            name=self.settings.name,
            tz=self.settings.tz,
            storage_path=storage_path,
            storage_type=self.storage_type,
            backups=sorted(backups, key=lambda x: x.zoned_datetime),
        )

    @abstractmethod
    def index(self) -> list[StorageLocation]:
        """Discover existing backups and organize them into storage locations."""

    @abstractmethod
    def write(
        self, *, tmp_backup: Path, storage_location: StorageLocation, checksum: str | None
    ) -> Backup:
        """Store the staged archive under a freshly generated name and return its Backup.

        Args:
            tmp_backup (Path): The staged archive to store.
            storage_location (StorageLocation): The destination and naming context.
            checksum (str | None): Precomputed hex SHA-256 to store as a sidecar, or
                None to skip sidecar creation.
        """

    @abstractmethod
    def delete(self, backup: Backup) -> bool:
        """Delete a single backup and report whether it was confirmed removed."""

    @abstractmethod
    def delete_many(self, backups: list[Backup]) -> list[Backup]:
        """Delete several backups and return the ones confirmed removed from storage."""

    @abstractmethod
    def prepare_for_restore(self, backup: Backup) -> Path | None:
        """Return a local path to the backup archive, or None if it cannot be retrieved."""
