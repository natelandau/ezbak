"""Local-filesystem storage backend for backup operations."""

from pathlib import Path

from loguru import logger
from nclutils.fs import copy_file, find_files

from ezbak.backup import Backup, StorageLocation
from ezbak.checksums import is_sidecar, sidecar_name
from ezbak.constants import BACKUP_EXTENSION, StorageType
from ezbak.exceptions import StorageWriteError
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
            # The `.tgz` glob already excludes `.sha256` sidecars, but filter
            # explicitly through the shared definition so the exclusion cannot
            # regress if the glob is ever loosened.
            found_files = [f for f in found_files if not is_sidecar(f.name)]
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

    def write(
        self, *, tmp_backup: Path, storage_location: StorageLocation, checksum: str | None
    ) -> Backup:
        """Copy the staged archive into the storage directory.

        Args:
            tmp_backup (Path): The staged archive to copy.
            storage_location (StorageLocation): The destination directory and naming context.
            checksum (str | None): Precomputed hex SHA-256 to store as a sidecar, or
                None to skip sidecar creation.

        Returns:
            Backup: The created backup.

        Raises:
            StorageWriteError: If the copy fails.
        """
        backup_name = storage_location.generate_new_backup_name()
        backup_path = Path(storage_location.storage_path) / backup_name
        logger.debug(f"Copy tmp backup to local: {backup_path}")
        try:
            copy_file(src=tmp_backup, dst=backup_path)
        except OSError as e:
            msg = f"Local write failed for '{backup_path}': {e}"
            logger.error(msg)
            raise StorageWriteError(msg) from e
        logger.info(f"Created: {backup_path}")

        backup = Backup(
            storage_type=StorageType.LOCAL,
            name=backup_path.name,
            path=backup_path,
            storage_path=storage_location.storage_path,
            tz=self.settings.tz,
        )
        self._store_sidecar(backup=backup, checksum=checksum)
        return backup

    def delete(self, backup: Backup) -> bool:
        """Unlink a local backup file, tolerating one already removed elsewhere.

        Also removes the archive's .sha256 sidecar, best-effort and idempotent
        for a backup with no sidecar.

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
            self._remove_sidecar(backup)
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

    def _write_sidecar(self, *, backup: Backup, content: str) -> None:  # noqa: PLR6301
        """Write the sidecar file next to a local archive; a failure warns and is tolerated.

        Args:
            backup (Backup): The backup the sidecar belongs to.
            content (str): The sidecar file content.
        """
        if backup.path is None:
            return
        sidecar_path = backup.path.parent / sidecar_name(backup.path.name)
        try:
            sidecar_path.write_text(content)
        except OSError as e:
            # Best-effort: the archive is intact, just unverifiable later.
            logger.warning(f"Failed to write checksum sidecar '{sidecar_path}': {e}")

    def _remove_sidecar(self, backup: Backup) -> None:  # noqa: PLR6301
        """Remove the checksum sidecar next to a local archive; tolerate its absence.

        Args:
            backup (Backup): The backup whose sidecar should be removed.
        """
        if backup.path is None:
            return
        sidecar_path = backup.path.parent / sidecar_name(backup.path.name)
        try:
            sidecar_path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"Failed to delete checksum sidecar '{sidecar_path}': {e}")

    def delete_many(self, backups: list[Backup]) -> list[Backup]:
        """Delete each local backup individually.

        Args:
            backups (list[Backup]): The backups to remove.

        Returns:
            list[Backup]: The backups confirmed removed from disk.
        """
        logger.debug(f"Deleting {len(backups)} local backups")
        return [backup for backup in backups if self.delete(backup)]

    def prepare_for_restore(self, backup: Backup) -> Path | None:  # noqa: PLR6301
        """Return the on-disk path of a local backup.

        Args:
            backup (Backup): The backup to restore.

        Returns:
            Path | None: The local archive path.
        """
        logger.info(f"Restoring backup from local: {backup.name}")
        return backup.path

    def _read_sidecar(self, backup: Backup) -> str | None:  # noqa: PLR6301
        """Return the raw sidecar content next to a local archive, or None if unreadable.

        Args:
            backup (Backup): The backup to look up.

        Returns:
            str | None: The sidecar content, or None if it is absent or unreadable.
        """
        if backup.path is None:
            return None
        sidecar_path = backup.path.parent / sidecar_name(backup.path.name)
        logger.trace(f"Reading checksum sidecar '{sidecar_path}'")
        try:
            return sidecar_path.read_text()
        except FileNotFoundError:
            logger.trace(f"No checksum sidecar at '{sidecar_path}'")
            return None
        except (OSError, UnicodeDecodeError) as e:
            # UnicodeDecodeError is a ValueError subclass, not an OSError, so it
            # needs its own clause: a bit-rotted sidecar must warn and proceed,
            # not crash the restore.
            logger.warning(f"Could not read checksum sidecar '{sidecar_path}': {e}")
            return None
