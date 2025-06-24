"""Backup model for managing individual backup archives and restoration operations."""

from pathlib import Path

from nclutils import logger
from whenever import PlainDateTime, TimeZoneNotFoundError

from ezbak.constants import DEFAULT_DATE_FORMAT, TIMESTAMP_REGEX, StorageType

from .settings import settings


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
    ) -> None:
        self.name = name

        self.storage_type = storage_type
        self.storage_path = (
            settings.aws_s3_bucket_path if storage_type == StorageType.AWS else storage_path
        )

        # Used for local backups
        self.path = path

        try:
            self.timestamp = TIMESTAMP_REGEX.search(name).group(0)
        except AttributeError:
            logger.warning(f"Could not parse timestamp: {name}")
            raise

        plain_dt = PlainDateTime.parse_strptime(self.timestamp, format=DEFAULT_DATE_FORMAT)
        try:
            self.zoned_datetime = (
                plain_dt.assume_tz(settings.tz) if settings.tz else plain_dt.assume_system_tz()
            )
        except TimeZoneNotFoundError as e:
            logger.error(e)
            raise

        self.year = str(self.zoned_datetime.year)
        self.month = str(self.zoned_datetime.month)
        self.week = str(self.zoned_datetime.py_datetime().strftime("%W"))
        self.day = str(self.zoned_datetime.day)
        self.hour = str(self.zoned_datetime.hour)
        self.minute = str(self.zoned_datetime.minute)

    def __repr__(self) -> str:
        """Return a string representation of the backup."""
        return f"<Backup: {self.name} ({self.storage_type.name}) {self.storage_path}>"
