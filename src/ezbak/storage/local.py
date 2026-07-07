"""Local-filesystem storage backend for backup operations."""

from pathlib import Path

from loguru import logger
from nclutils.fs import copy_file, find_files

from ezbak.backup import Backup, StorageLocation
from ezbak.constants import BACKUP_EXTENSION, StorageType
from ezbak.filters import validate_storage_paths
from ezbak.storage.base import StorageBackend


class LocalBackend(StorageBackend):
    """Back up to and manage archives on local filesystem directories."""

    storage_type = StorageType.LOCAL

    def index(self) -> list[StorageLocation]:
        """Scan each configured storage directory for matching backup files.

        Returns:
            list[StorageLocation]: One storage location per configured storage path.
        """
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

    def delete(self, backup: Backup) -> bool:  # noqa: PLR6301
        """Unlink a local backup file, tolerating one already removed elsewhere.

        Args:
            backup (Backup): The backup whose file should be removed.

        Returns:
            bool: True if the file was unlinked, False if it was already gone.
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
            return False
        return True

    def delete_many(self, backups: list[Backup]) -> int:
        """Delete each local backup individually.

        Args:
            backups (list[Backup]): The backups to remove.

        Returns:
            int: The number of backups confirmed removed.
        """
        logger.debug(f"Deleting {len(backups)} local backups")
        return sum(self.delete(backup) for backup in backups)

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
