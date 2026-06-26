"""Storage location backups."""

from collections import defaultdict
from pathlib import Path

from loguru import logger
from whenever import Instant, TimeZoneNotFoundError

from ezbak.constants import DEFAULT_DATE_FORMAT, BackupType, StorageType

from .backup import Backup
from .backup_name import add_uid_suffix, build_backup_name


class StorageLocation:
    """Class to store backups by storage location."""

    def __init__(
        self,
        *,
        storage_path: str | Path,
        storage_type: StorageType,
        backups: list[Backup],
        name: str,
        label_time_units: bool,
        tz: str | None = None,
    ) -> None:
        self.storage_path = storage_path
        self.storage_type = storage_type
        self.backups = backups
        self.name = name
        self.backups_by_time_unit, self.dates_in_use = self._categorize_backups_by_time_unit()
        self.tz = tz
        self.label_time_units = label_time_units

        # This variable is only used for logging purposes.
        self.logging_name = (
            "S3"
            if self.storage_type == StorageType.AWS
            else self.storage_path or self.storage_type.value
        )

    def _categorize_backups_by_time_unit(
        self,
    ) -> tuple[dict[BackupType, list[Backup]], dict[BackupType, list[str]]]:
        """Categorize backups by time unit and return a dictionary of backups grouped by time unit and a dictionary of dates in use.

        Returns:
            tuple[dict[BackupType, list[Backup]], dict[BackupType, list[str]]]: A tuple containing a dictionary of backups grouped by time unit and a dictionary of dates in use.
        """
        backups_by_type: dict[BackupType, list[Backup]] = defaultdict(list)
        existing_dates: dict[BackupType, list[str]] = defaultdict(list)

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
                    existing_dates[period_type].append(date_value)
                    backups_by_type[period_type].append(backup)
                    # First-match-wins: assign each backup to the coarsest period whose slot
                    # for its timestamp is not yet taken, then stop.
                    break

                if period_type == BackupType.MINUTELY:
                    backups_by_type[period_type].append(backup)
                    break

        return backups_by_type, existing_dates

    def generate_new_backup_name(self) -> str:
        """Generate a unique backup filename with timestamp and optional time unit classification.

        Create backup filenames that include timestamps and optionally classify backups by time periods (yearly, monthly, daily, etc.) to enable intelligent retention policies. Use this to ensure backup files have consistent, sortable names that support automated cleanup operations.

        Returns:
            str: The generated backup filename in format "{name}-{timestamp}-{period}.{extension}" or "{name}-{timestamp}.{extension}" depending on configuration.

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

        if not self.label_time_units:
            filename = build_backup_name(name=self.name, timestamp=timestamp)
        else:
            period_checks = [
                ("yearly", BackupType.YEARLY, str(now.year)),
                ("monthly", BackupType.MONTHLY, str(now.month)),
                ("weekly", BackupType.WEEKLY, now.to_stdlib().strftime("%W")),
                ("daily", BackupType.DAILY, str(now.day)),
                ("hourly", BackupType.HOURLY, str(now.hour)),
                ("minutely", BackupType.MINUTELY, str(now.minute)),
            ]

            # Fall-through bucket when every coarser period for this timestamp is already taken
            period = "minutely"

            for period_name, backup_type, current_value in period_checks:
                if current_value in self.dates_in_use[backup_type]:
                    continue

                period = period_name
                break

            filename = build_backup_name(name=self.name, timestamp=timestamp, period=period)

        if filename in [x.name for x in self.backups]:
            filename = add_uid_suffix(filename)

        logger.trace(f"Backup name: {filename}")
        return filename
