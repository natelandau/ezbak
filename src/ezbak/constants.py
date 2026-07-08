"""Constants for ezbak."""

import re
from enum import Enum

__version__ = "0.12.4"

DEFAULT_DATE_FORMAT = "%Y%m%dT%H%M%S"
# whenever's parse pattern equivalent of DEFAULT_DATE_FORMAT — Y/M/D for date, h/m/s for time, 'T' is a quoted literal
DEFAULT_DATE_PATTERN = "YYYYMMDD'T'hhmmss"
TIMESTAMP_REGEX = re.compile(r"\d{8}T\d{6}")
# Accepts the no-dash filename timestamp shape at any granularity:
# YYYY, YYYYMM, YYYYMMDD, YYYYMMDDTHH, YYYYMMDDTHHMM, YYYYMMDDTHHMMSS
# `\Z` (not `$`) so a trailing newline does not slip through: `$` also matches
# just before a final `\n`, which would pass a value like "20250101\n".
RESTORE_DATE_REGEX = re.compile(r"^\d{4}(\d{2}(\d{2}(T\d{2}(\d{2}(\d{2})?)?)?)?)?\Z")
DEFAULT_COMPRESSION_LEVEL = 9
DEFAULT_RETENTION = 1
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


class RetentionPolicyType(Enum):
    """Retention policy type."""

    TIME_BASED = "time_based"  # Uses yearly/monthly/weekly/etc. retention
    COUNT_BASED = "count_based"  # Uses simple max_backups count
    KEEP_ALL = "keep_all"  # Keeps all backups


class StorageType(Enum):
    """Storage location."""

    LOCAL = "local"
    AWS = "aws"


class LogLevel(Enum):
    """Log level."""

    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Action(Enum):
    """Action."""

    BACKUP = "backup"
    RESTORE = "restore"
