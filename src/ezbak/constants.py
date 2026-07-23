"""Constants for ezbak."""

import re
from enum import Enum

__version__ = "1.2.1"

DEFAULT_DATE_FORMAT = "%Y%m%dT%H%M%S"
# whenever's parse pattern equivalent of DEFAULT_DATE_FORMAT, Y/M/D for date, h/m/s for time, 'T' is a quoted literal
DEFAULT_DATE_PATTERN = "YYYYMMDD'T'hhmmss"
TIMESTAMP_REGEX = re.compile(r"\d{8}T\d{6}")
# Accepts the no-dash filename timestamp shape at any granularity:
# YYYY, YYYYMM, YYYYMMDD, YYYYMMDDTHH, YYYYMMDDTHHMM, YYYYMMDDTHHMMSS
# `\Z` (not `$`) so a trailing newline does not slip through: `$` also matches
# just before a final `\n`, which would pass a value like "20250101\n".
RESTORE_DATE_REGEX = re.compile(r"^\d{4}(\d{2}(\d{2}(T\d{2}(\d{2}(\d{2})?)?)?)?)?\Z")
DEFAULT_COMPRESSION_LEVEL = 6
ENVAR_PREFIX = "EZBAK_"
BACKUP_EXTENSION = "tgz"
CHECKSUM_EXTENSION = "sha256"
ALWAYS_EXCLUDE_FILENAMES = (
    ".DS_Store",
    "@eaDir",
    ".Trashes",
    "__pycache__",
    "Thumbs.db",
    "IconCache.db",
)
# Entries that do not count as "data" when skip_restore_if_populated guards a restore.
# Reuses the OS cruft we already refuse to back up, plus lost+found (shipped on every
# fresh ext mount). Extend this tuple to teach the guard about more benign noise.
RESTORE_POPULATED_IGNORE_FILENAMES = (*ALWAYS_EXCLUDE_FILENAMES, "lost+found")


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


class RestoreOutcome(Enum):
    """Result of a restore attempt.

    Distinguishes an actual restore from a no-op so callers can react correctly: the
    container suppresses its post-restore hook on a skip and treats a missing backup as a
    failure unless skip_if_no_backup is set.
    """

    RESTORED = "restored"  # a backup was extracted into the target
    NO_BACKUP = "no_backup"  # nothing matched the restore criteria
    SKIPPED_POPULATED = "skipped"  # guard tripped; target already held data
