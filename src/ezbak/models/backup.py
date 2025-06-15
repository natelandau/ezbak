"""The existing backup model."""

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

        logger.info(f"Restored backup to {destination}")
        return True
