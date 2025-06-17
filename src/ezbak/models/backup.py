"""The existing backup model."""

import os
import tarfile
from dataclasses import dataclass
from pathlib import Path

from nclutils import logger
from whenever import SystemDateTime, ZonedDateTime


@dataclass
class Backup:
    """Model for a backup."""

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
        """Recursively change the ownership of all files in a directory.

        Args:
            directory (Path | str): The directory to recursively change the ownership of.
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
        """Delete the backup.

        Returns:
            Path: The path to the deleted backup.
        """
        logger.debug(f"Delete: {self.path.name}")
        self.path.unlink()
        return self.path

    def restore(self, destination: Path) -> bool:
        """Restore the backup to the destination.

        Returns:
            bool: True if the backup was restored successfully, False otherwise.
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
