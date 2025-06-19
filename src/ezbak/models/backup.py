"""Backup model for managing individual backup archives and restoration operations."""

import os
import tarfile
from dataclasses import dataclass
from pathlib import Path

from nclutils import logger
from whenever import SystemDateTime, ZonedDateTime


@dataclass
class Backup:
    """Represent a single backup archive with metadata and restoration capabilities.

    Encapsulates a backup archive file with its timestamp information, ownership settings,
    and methods for restoration and deletion. Provides time-based categorization for
    retention policy management and safe restoration with ownership preservation.
    """

    path: Path
    timestamp: str
    year: str
    month: str
    week: str
    day: str
    hour: str
    minute: str
    zoned_datetime: ZonedDateTime | SystemDateTime
    chown_user: int | None
    chown_group: int | None

    def _chown_all_files(self, directory: Path | str) -> None:
        """Recursively change ownership of all files in a directory to backup settings.

        Updates file ownership for all files and subdirectories in the specified directory
        to match the backup's configured user and group IDs. Used during restoration to
        preserve original file ownership from the backup.

        Args:
            directory (Path | str): Directory path to recursively update file ownership.
        """
        if isinstance(directory, str):
            directory = Path(directory)

        uid = int(self.chown_user)
        gid = int(self.chown_group)

        os.chown(directory.resolve(), uid, gid)

        for file in directory.rglob("*"):
            try:
                os.chown(file.resolve(), uid, gid)
            except OSError as e:  # noqa: PERF203
                logger.warning(f"Failed to chown {file}: {e}")

        logger.info(f"Changed ownership of all restored files in {directory} to {uid}:{gid}")

    def delete(self) -> Path:
        """Remove the backup archive file from the filesystem.

        Permanently deletes the backup archive file and returns the path of the deleted file.
        Used by retention policies to clean up old backups when storage limits are exceeded.

        Returns:
            Path: Path to the deleted backup archive file.
        """
        logger.debug(f"Delete: {self.path.name}")
        self.path.unlink()
        return self.path

    def restore(self, destination: Path) -> bool:
        """Extract backup archive contents to the specified destination directory.

        Extracts all files from the backup archive to the destination path while preserving
        file structure. Optionally restores original file ownership if chown settings are
        configured. Used for disaster recovery and backup verification.

        Args:
            destination (Path): Directory path where backup contents will be extracted.

        Returns:
            bool: True if restoration completed successfully, False if extraction failed.
        """
        logger.debug(f"Restoring backup: {self.path.name}")
        try:
            with tarfile.open(self.path) as archive:
                archive.extractall(path=destination, filter="data")
        except tarfile.TarError as e:
            logger.error(f"Failed to restore backup: {e}")
            return False

        if self.chown_user and self.chown_group:
            self._chown_all_files(destination)

        logger.info(f"Restored backup to {destination}")
        return True
