"""Constants for ezbak."""

import re
from enum import Enum

__version__ = "0.7.0"

DEFAULT_DATE_FORMAT = "%Y%m%dT%H%M%S"
TIMESTAMP_REGEX = re.compile(r"\d{8}T\d{6}")
BACKUP_NAME_REGEX = re.compile(
    r"(?P<name>.+)-(?P<timestamp>\d{8}T\d{6})(?:-(?P<period>(?:yearly|monthly|weekly|daily|hourly|minutely)))?-?(?P<uuid>[0-9a-z]{5,6})?\.(?P<extension>.+)",
    re.IGNORECASE,
)
DEFAULT_COMPRESSION_LEVEL = 9
DEFAULT_RETENTION = 1
DEFAULT_LABEL_TIME_UNITS = True
ENVAR_PREFIX = "EZBAK_"
BACKUP_EXTENSION = "tgz"
ALWAYS_EXCLUDE_FILENAMES = (
    ".DS_Store",
    "@eaDir",
    ".Trashes",
    "__pycache__",
    "Thumbs.db",
    "IconCache.db",
)


class CLILogLevel(Enum):
    """Define verbosity levels for cli output.

    Use these levels to control the amount of information displayed to users. Higher levels include all information from lower levels plus additional details.
    """

    INFO = 0
    DEBUG = 1
    TRACE = 2


class BackupType(Enum):
    """Backup type."""

    YEARLY = "yearly"
    MONTHLY = "monthly"
    WEEKLY = "weekly"
    DAILY = "daily"
    HOURLY = "hourly"
    MINUTELY = "minutely"
    NO_TYPE = "no_type"


class RetentionPolicyType(Enum):
    """Retention policy type."""

    TIME_BASED = "time_based"  # Uses yearly/monthly/weekly/etc. retention
    COUNT_BASED = "count_based"  # Uses simple max_backups count
    KEEP_ALL = "keep_all"  # Keeps all backups
