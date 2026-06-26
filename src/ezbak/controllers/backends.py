"""Storage backends abstracting local-filesystem and S3 backup operations.

Each backend owns how a single storage kind (local directories or an S3 bucket) indexes, writes, deletes, downloads, and renames backups, so the manager can drive any configured storage uniformly instead of branching on storage type at every call site.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from loguru import logger
from nclutils.fs import copy_file, find_files
from nclutils.utils import new_uid

from ezbak.constants import BACKUP_EXTENSION, StorageType
from ezbak.models import Backup, StorageLocation
from ezbak.models.settings import Settings
from ezbak.utils import validate_source_paths, validate_storage_paths

from .aws import AWSService


class StorageBackend(ABC):
    """Common interface for a single backup storage kind."""

    storage_type: ClassVar[StorageType]

    def __init__(self, settings: Settings) -> None:
        """Store the settings shared by every storage operation.

        Args:
            settings (Settings): The validated backup configuration.
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
            label_time_units=self.settings.label_time_units,
            tz=self.settings.tz,
            storage_path=storage_path,
            storage_type=self.storage_type,
            backups=sorted(backups, key=lambda x: x.zoned_datetime),
        )

    @abstractmethod
    def index(self) -> list[StorageLocation]:
        """Discover existing backups and organize them into storage locations."""

    @abstractmethod
    def write(self, *, tmp_backup: Path, storage_location: StorageLocation) -> Backup:
        """Store the staged archive under a freshly generated name and return its Backup."""

    @abstractmethod
    def delete(self, backup: Backup) -> None:
        """Delete a single backup."""

    @abstractmethod
    def delete_many(self, backups: list[Backup]) -> int:
        """Delete several backups and return how many were removed."""

    @abstractmethod
    def prepare_for_restore(self, backup: Backup) -> Path | None:
        """Return a local path to the backup archive, or None if it cannot be retrieved."""

    @abstractmethod
    def rename(self, backup: Backup, new_name: str) -> None:
        """Rename a single backup in place."""


class LocalBackend(StorageBackend):
    """Back up to and manage archives on local filesystem directories."""

    storage_type = StorageType.LOCAL

    def index(self) -> list[StorageLocation]:
        """Scan each configured storage directory for matching backup files.

        Returns:
            list[StorageLocation]: One storage location per configured storage path.
        """
        validate_source_paths(source_paths=self.settings.source_paths)
        validate_storage_paths(storage_paths=self.settings.storage_paths, create_if_missing=True)

        locations: list[StorageLocation] = []
        for storage_path in self.settings.storage_paths:
            logger.trace(f"Indexing: {storage_path}")
            found_files = find_files(
                path=storage_path, globs=[f"*{self.settings.name}*.{BACKUP_EXTENSION}"]
            )
            location = self._build_storage_location(
                storage_path=storage_path,
                backups=[
                    Backup(
                        storage_type=StorageType.LOCAL,
                        name=x.name,
                        path=x,
                        storage_path=storage_path,
                        tz=self.settings.tz,
                    )
                    for x in found_files
                ],
            )
            logger.debug(f"Indexed {len(location.backups)} existing backups in '{storage_path}'")
            for backup in location.backups:
                logger.trace(f"Indexed: {backup}")
            locations.append(location)

        return locations

    def write(self, *, tmp_backup: Path, storage_location: StorageLocation) -> Backup:
        """Copy the staged archive into the storage directory.

        Args:
            tmp_backup (Path): The staged archive to copy.
            storage_location (StorageLocation): The destination directory and naming context.

        Returns:
            Backup: The created backup.
        """
        backup_name = storage_location.generate_new_backup_name()
        backup_path = Path(storage_location.storage_path) / backup_name
        logger.debug(f"Copy tmp backup to local: {backup_path}")
        copy_file(src=tmp_backup, dst=backup_path)
        logger.info(f"Created: {backup_path}")
        return Backup(
            storage_type=StorageType.LOCAL,
            name=backup_path.name,
            path=backup_path,
            storage_path=storage_location.storage_path,
            tz=self.settings.tz,
        )

    def delete(self, backup: Backup) -> None:  # noqa: PLR6301
        """Unlink a local backup file, tolerating one already removed elsewhere.

        Args:
            backup (Backup): The backup whose file should be removed.
        """
        # Catch instead of missing_ok so the whole job does not abort when another
        # process already pruned this file from a shared storage location. Catch the
        # broader OSError (not just FileNotFoundError) so an NFS stale handle (ESTALE,
        # errno 116) degrades to a warning instead of aborting the job.
        try:
            backup.path.unlink()
            logger.info(f"Deleted: {backup.path}")
        except OSError as e:
            logger.warning(f"Missing, not deleted: {backup.path} (errno={e.errno}: {e.strerror})")
            # Forensics for shared-storage cache skew: if the directory listing still
            # shows this file after the unlink proved it gone, the client mount cache
            # is serving a stale 'present' view rather than racing a live delete.
            try:
                parent = backup.path.parent
                still_listed = backup.path.name in {p.name for p in parent.iterdir()}
                logger.debug(
                    f"Post-failure check: exists()={backup.path.exists()}, "
                    f"present_in_parent_listing={still_listed}, parent={parent}"
                )
            except OSError as check_error:
                logger.debug(f"Post-failure check failed: {check_error}")

    def delete_many(self, backups: list[Backup]) -> int:
        """Delete each local backup individually.

        Args:
            backups (list[Backup]): The backups to remove.

        Returns:
            int: The number of backups attempted (a missing file is logged, not retried).
        """
        logger.debug(f"Deleting {len(backups)} local backups")
        for backup in backups:
            self.delete(backup)
        return len(backups)

    def prepare_for_restore(self, backup: Backup) -> Path | None:  # noqa: PLR6301
        """Return the on-disk path of a local backup.

        Args:
            backup (Backup): The backup to restore.

        Returns:
            Path | None: The local archive path.
        """
        logger.info(f"Restoring backup from local: {backup.name}")
        return backup.path

    def rename(self, backup: Backup, new_name: str) -> None:  # noqa: PLR6301
        """Rename a local backup file within its directory.

        Args:
            backup (Backup): The backup to rename.
            new_name (str): The new filename.
        """
        backup.path.rename(backup.path.parent / new_name)
        logger.debug(f"Rename: {backup.path} -> {new_name}")


class S3Backend(StorageBackend):
    """Back up to and manage archives in an S3 bucket."""

    storage_type = StorageType.AWS

    def __init__(self, settings: Settings, *, aws_service: AWSService, tmp_dir: Path) -> None:
        """Store the S3 client and staging directory alongside the settings.

        Args:
            settings (Settings): The validated backup configuration.
            aws_service (AWSService): The connected S3 client wrapper.
            tmp_dir (Path): The directory used to stage downloaded archives.
        """
        super().__init__(settings)
        self.aws_service = aws_service
        self.tmp_dir = tmp_dir

    def index(self) -> list[StorageLocation]:
        """List bucket objects matching the configured name into a single storage location.

        Returns:
            list[StorageLocation]: A single-element list with the S3 storage location.
        """
        logger.trace("Indexing S3 storage location")
        found_backups = self.aws_service.list_objects(prefix=self.settings.name)

        if self.settings.aws_s3_bucket_path:
            found_backups = [
                x.replace(f"{self.settings.aws_s3_bucket_path.rstrip('/')}/", "")
                for x in found_backups
            ]

        location = self._build_storage_location(
            storage_path=self.settings.aws_s3_bucket_path,
            backups=[
                Backup(
                    storage_type=StorageType.AWS,
                    name=x,
                    tz=self.settings.tz,
                    storage_path=self.settings.aws_s3_bucket_path,
                )
                for x in found_backups
            ],
        )
        logger.debug(f"Indexed {len(location.backups)} existing backups in S3 bucket")
        for backup in location.backups:
            logger.trace(f"Indexed: {backup}")

        return [location]

    def write(self, *, tmp_backup: Path, storage_location: StorageLocation) -> Backup:
        """Upload the staged archive to the bucket.

        Args:
            tmp_backup (Path): The staged archive to upload.
            storage_location (StorageLocation): The naming context for the new object.

        Returns:
            Backup: The created backup.
        """
        backup_name = storage_location.generate_new_backup_name()
        logger.debug(f"Upload tmp backup to S3: {backup_name}")
        self.aws_service.upload_object(file=tmp_backup, name=backup_name)
        logger.info(f"S3 created: {backup_name}")
        return Backup(
            storage_type=StorageType.AWS,
            name=backup_name,
            tz=self.settings.tz,
            storage_path=self.settings.aws_s3_bucket_path,
        )

    def delete(self, backup: Backup) -> None:
        """Delete a single object from the bucket.

        Args:
            backup (Backup): The backup whose object should be removed.
        """
        self.aws_service.delete_object(key=backup.name)
        logger.info(f"Deleted from S3: {backup.name}")

    def delete_many(self, backups: list[Backup]) -> int:
        """Batch-delete objects from the bucket.

        Args:
            backups (list[Backup]): The backups to remove.

        Returns:
            int: The number of objects the bucket confirmed deleted.
        """
        if not backups:
            return 0

        logger.debug(f"Deleting {len(backups)} S3 backups")
        deleted = self.aws_service.delete_objects(keys=[x.name for x in backups])
        for key in deleted:
            logger.info(f"Deleted from S3: {key}")
        return len(deleted)

    def prepare_for_restore(self, backup: Backup) -> Path | None:
        """Download the backup object to a temporary file for extraction.

        Args:
            backup (Backup): The backup to restore.

        Returns:
            Path | None: The downloaded archive path, or None if the object is missing.
        """
        logger.info(f"Restoring backup from S3: {backup.name}")
        if not self.aws_service.object_exists(backup.name):
            logger.error(f"Backup file does not exist in S3: {backup.name}")
            return None

        logger.trace(f"Downloading backup from S3 to tmp file: {backup.name}")
        tmp_file = self.tmp_dir / f"{new_uid(bits=24)}.{BACKUP_EXTENSION}"
        self.aws_service.get_object(key=backup.name, destination=tmp_file)
        return tmp_file

    def rename(self, backup: Backup, new_name: str) -> None:
        """Rename an object in the bucket via copy-and-delete.

        Args:
            backup (Backup): The backup to rename.
            new_name (str): The new object name.
        """
        self.aws_service.rename_object(current_name=backup.name, new_name=new_name)
        logger.debug(f"S3: Rename {backup.name} -> {new_name}")
