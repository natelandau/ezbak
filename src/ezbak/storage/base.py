"""Abstract storage backend interface shared by all backup storage kinds."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from ezbak.backup import Backup, StorageLocation
from ezbak.checksums import format_sidecar, is_sidecar, parse_sidecar
from ezbak.config import BackupConfig
from ezbak.constants import StorageType


class StorageBackend(ABC):
    """Common interface for a single backup storage kind.

    Concrete backends implement the archive operations (index, write, delete,
    prepare_for_restore) plus three small sidecar primitives (`_write_sidecar`,
    `_read_sidecar`, `_remove_sidecar`) that do only raw byte I/O. The checksum
    sidecar policy, when to write one, how to name, format, and parse it, and how
    to keep sidecars out of the backup index, lives here so every backend shares
    one implementation and a new backend cannot silently diverge.
    """

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

    @staticmethod
    def _exclude_sidecars(names: list[str]) -> list[str]:
        """Drop checksum sidecars from a list of discovered object names.

        Use so a `.sha256` sidecar is never parsed as a backup and counted
        against retention, with one shared definition of what a sidecar is.

        Args:
            names (list[str]): The discovered object names or keys.

        Returns:
            list[str]: The names that are backups rather than sidecars.
        """
        return [name for name in names if not is_sidecar(name)]

    def get_checksum(self, backup: Backup) -> str | None:
        """Return the expected hex SHA-256 for a backup, or None when no usable sidecar exists.

        Fetch the raw sidecar via the backend primitive and parse it here, so
        every backend agrees on the sidecar format. A missing, unreadable, or
        malformed sidecar yields None, so restore verification degrades to
        warn-and-proceed rather than failing.

        Args:
            backup (Backup): The backup to look up.

        Returns:
            str | None: The stored digest, or None when unavailable.
        """
        content = self._read_sidecar(backup)
        return parse_sidecar(content) if content is not None else None

    def _store_sidecar(self, *, backup: Backup, checksum: str | None) -> None:
        """Write the checksum sidecar for a freshly stored archive, when enabled.

        A None checksum means checksums are disabled, so do nothing. The backend
        primitive swallows write failures, since the archive is intact and only
        its later verifiability is lost.

        Args:
            backup (Backup): The just-created backup the sidecar belongs to.
            checksum (str | None): The archive's hex SHA-256, or None to skip.
        """
        if checksum is None:
            return
        self._write_sidecar(
            backup=backup,
            content=format_sidecar(digest=checksum, archive_name=backup.name),
        )

    @abstractmethod
    def index(self) -> list[StorageLocation]:
        """Discover existing backups and organize them into storage locations."""

    @abstractmethod
    def write(
        self, *, tmp_backup: Path, storage_location: StorageLocation, checksum: str | None
    ) -> Backup:
        """Store the staged archive under a freshly generated name and return its Backup.

        Implementations store the archive, build its Backup, then call
        `self._store_sidecar(backup=..., checksum=checksum)` so the sidecar
        policy stays shared.

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

    @abstractmethod
    def _write_sidecar(self, *, backup: Backup, content: str) -> None:
        """Persist sidecar `content` alongside `backup`'s archive.

        Best-effort: warn and swallow any storage error, never raise. The
        archive is already written, so only later verifiability is at stake.
        """

    @abstractmethod
    def _read_sidecar(self, backup: Backup) -> str | None:
        """Return the raw sidecar content for `backup`, or None if absent or unreadable.

        Never raise: a missing or unreadable sidecar must degrade to None so
        restore verification warns and proceeds.
        """

    @abstractmethod
    def _remove_sidecar(self, backup: Backup) -> None:
        """Delete `backup`'s sidecar, best-effort and idempotent for an absent one.

        Never raise: warn and swallow any storage error.
        """
