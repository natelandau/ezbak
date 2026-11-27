"""Backup and storage location models for managing backup archives and restoration operations."""

from pathlib import Path

from loguru import logger
from whenever import SYSTEM_TZ, Instant, TimeZoneNotFoundError

from ezbak.constants import (
    DEFAULT_DATE_FORMAT,
    DEFAULT_DATE_PATTERN,
    StorageType,
)
from ezbak.naming import add_uid_suffix, build_backup_name, parse_backup_timestamp


class Backup:
    """Represent a single backup archive with metadata and restoration capabilities.

    Encapsulates a backup archive file with its timestamp information, ownership settings, and methods for restoration and deletion. Provides time-based categorization for retention policy management and safe restoration with ownership preservation.
    """

    def __init__(
        self,
        name: str,
        storage_type: StorageType,
        path: Path | None = None,
        storage_path: Path | str | None = None,
        tz: str | None = None,
    ) -> None:
        self.name = name
        self.tz = tz

        self.storage_type = storage_type
        self.storage_path = storage_path

        # Full path to the backup file, used for local backups
        self.path = path

        plain_dt = parse_backup_timestamp(name)
        if plain_dt is None:
            msg = f"Backup name '{name}' has no valid YYYYMMDDTHHMMSS timestamp"
            logger.warning(msg)
            raise ValueError(msg)
        self.timestamp = plain_dt.format(DEFAULT_DATE_PATTERN)

        try:
            self.zoned_datetime = plain_dt.assume_tz(self.tz or SYSTEM_TZ)
        except TimeZoneNotFoundError as e:
            logger.error(e)
            raise

        # Period keys must be globally unique so retention bucketing never conflates
        # the same sub-field across different periods (e.g. July 2025 vs July 2026).
        dt = self.zoned_datetime
        self.year = str(dt.year)
        self.month = f"{dt.year}-{dt.month}"
        # ISO week (not %W) so a week spanning a year boundary keys to one
        # bucket instead of splitting across two and consuming two keep_weekly slots.
        iso = dt.to_stdlib().isocalendar()
        self.week = f"{iso.year}-{iso.week}"
        self.day = f"{dt.year}-{dt.month}-{dt.day}"
        self.hour = f"{dt.year}-{dt.month}-{dt.day}-{dt.hour}"
        self.minute = f"{dt.year}-{dt.month}-{dt.day}-{dt.hour}-{dt.minute}"

    def __repr__(self) -> str:
        """Return a string representation of the backup."""
        return f"<Backup: {str(self.storage_path) + '/' if self.storage_path else ''}{self.name} ({self.storage_type.name})>"

    def __str__(self) -> str:
        """Return a string representation of the backup."""
        return f"{str(self.storage_path) + '/' if self.storage_path else ''}{self.name} ({self.storage_type.name})"


class StorageLocation:
    """Class to store backups by storage location."""

    def __init__(
        self,
        *,
        storage_path: str | Path | None,
        storage_type: StorageType,
        backups: list[Backup],
        name: str,
        tz: str | None = None,
    ) -> None:
        self.storage_path = storage_path
        self.storage_type = storage_type
        self.backups = backups
        self.name = name
        self.tz = tz

        # This variable is only used for logging purposes.
        self.logging_name = (
            "S3"
            if self.storage_type == StorageType.AWS
            else self.storage_path or self.storage_type.value
        )

    def generate_new_backup_name(self) -> str:
        """Generate a unique, sortable backup filename for the current time.

        Produce a ``{name}-{timestamp}.{extension}`` filename, appending a short
        unique id only when another backup already holds that exact name. Use this
        to give each new archive a consistent, collision-free name.

        Returns:
            str: The generated backup filename.

        Raises:
            TimeZoneNotFoundError: If the configured timezone identifier is invalid.
        """
        logger.trace("Generating new backup name")
        i = Instant.now()

        try:
            now = i.to_tz(self.tz or SYSTEM_TZ)
        except TimeZoneNotFoundError as e:
            logger.error(e)
            raise

        timestamp = now.to_stdlib().strftime(DEFAULT_DATE_FORMAT)
        filename = build_backup_name(name=self.name, timestamp=timestamp)

        if any(x.name == filename for x in self.backups):
            filename = add_uid_suffix(filename)

        logger.trace(f"Backup name: {filename}")
        return filename
