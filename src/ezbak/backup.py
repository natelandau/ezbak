"""Backup and storage location models for managing backup archives and restoration operations."""

from collections import defaultdict
from pathlib import Path

from loguru import logger
from whenever import Instant, PlainDateTime, TimeZoneNotFoundError

from ezbak.constants import (
    DEFAULT_DATE_FORMAT,
    DEFAULT_DATE_PATTERN,
    TIMESTAMP_REGEX,
    BackupType,
    StorageType,
)
from ezbak.naming import add_uid_suffix, build_backup_name


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

        try:
            self.timestamp = TIMESTAMP_REGEX.search(name).group(0)
        except AttributeError:
            logger.warning(f"Could not parse timestamp: {name}")
            raise

        plain_dt = PlainDateTime.parse(self.timestamp, format=DEFAULT_DATE_PATTERN)
        try:
            self.zoned_datetime = (
                plain_dt.assume_tz(self.tz) if self.tz else plain_dt.assume_system_tz()
            )
        except TimeZoneNotFoundError as e:
            logger.error(e)
            raise

        # Period keys must be globally unique so retention bucketing never conflates
        # the same sub-field across different periods (e.g. July 2025 vs July 2026).
        dt = self.zoned_datetime
        self.year = str(dt.year)
        self.month = f"{dt.year}-{dt.month}"
        # %W stays within dt.year, so pairing it with the calendar year is consistent.
        self.week = f"{dt.year}-{dt.to_stdlib().strftime('%W')}"
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
        storage_path: str | Path,
        storage_type: StorageType,
        backups: list[Backup],
        name: str,
        tz: str | None = None,
    ) -> None:
        self.storage_path = storage_path
        self.storage_type = storage_type
        self.backups = backups
        self.name = name
        self.backups_by_time_unit = self._categorize_backups_by_time_unit()
        self.tz = tz

        # This variable is only used for logging purposes.
        self.logging_name = (
            "S3"
            if self.storage_type == StorageType.AWS
            else self.storage_path or self.storage_type.value
        )

    def _categorize_backups_by_time_unit(
        self,
    ) -> dict[BackupType, list[Backup]]:
        """Categorize backups by time unit for retention policy grouping.

        Returns:
            dict[BackupType, list[Backup]]: Backups grouped by time unit.
        """
        backups_by_type: dict[BackupType, list[Backup]] = defaultdict(list)
        # A set for O(1) membership: existing_dates drives first-match-wins bucketing
        # below and is not needed once categorization is complete.
        existing_dates: dict[BackupType, set[str]] = defaultdict(set)

        period_definitions = [
            (BackupType.YEARLY, "year"),
            (BackupType.MONTHLY, "month"),
            (BackupType.WEEKLY, "week"),
            (BackupType.DAILY, "day"),
            (BackupType.HOURLY, "hour"),
            (BackupType.MINUTELY, "minute"),
        ]
        for backup in self.backups:
            for period_type, date_attr in period_definitions:
                date_value = getattr(backup, date_attr)
                if date_value not in existing_dates[period_type]:
                    existing_dates[period_type].add(date_value)
                    backups_by_type[period_type].append(backup)
                    # First-match-wins: assign each backup to the coarsest period whose slot
                    # for its timestamp is not yet taken, then stop.
                    break

                if period_type == BackupType.MINUTELY:
                    backups_by_type[period_type].append(backup)
                    break

        return backups_by_type

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
            now = i.to_tz(self.tz) if self.tz else i.to_system_tz()
        except TimeZoneNotFoundError as e:
            logger.error(e)
            raise

        timestamp = now.to_stdlib().strftime(DEFAULT_DATE_FORMAT)
        filename = build_backup_name(name=self.name, timestamp=timestamp)

        if filename in [x.name for x in self.backups]:
            filename = add_uid_suffix(filename)

        logger.trace(f"Backup name: {filename}")
        return filename
