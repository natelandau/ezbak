"""Grammar for ezbak backup filenames.

Single source of truth for how backup filenames are composed and disambiguated, so the naming convention does not drift across the code that creates them.
"""

import re
from dataclasses import dataclass

from loguru import logger
from nclutils.utils import new_uid
from whenever import PlainDateTime

from ezbak.constants import BACKUP_EXTENSION, DEFAULT_DATE_PATTERN, TIMESTAMP_REGEX

# Labels that releases before plain "{name}-{timestamp}" names put after the timestamp. A fixed
# list, because it describes archives already in storage, not the current retention periods.
_LEGACY_PERIOD_LABELS = ("yearly", "monthly", "weekly", "daily", "hourly", "minutely")

# "{set name}-{timestamp}[-{uid}].{ext}", plus the legacy period label and mixed-case uid that
# older archives carry. The greedy set name takes the last timestamp-shaped run, so a set name
# that itself contains a timestamp-shaped run still parses.
_BACKUP_FILENAME_REGEX = re.compile(
    rf"(?P<set_name>.+)-(?P<timestamp>{TIMESTAMP_REGEX.pattern})"
    rf"(?:-(?:{'|'.join(_LEGACY_PERIOD_LABELS)}))?(?:-[0-9A-Za-z]+)?"
    rf"\.{re.escape(BACKUP_EXTENSION)}",
    flags=re.DOTALL,
)


@dataclass(frozen=True)
class BackupFilename:
    """The parts of a backup filename that decide which set it belongs to and when it was made."""

    set_name: str
    timestamp: PlainDateTime


def parse_backup_filename(name: str) -> BackupFilename | None:
    """Split a backup filename into set name and timestamp, so it can be grouped and ordered.

    A storage location can hold other sets' backups and files ezbak did not write, such as a
    hand-named copy. Only a name that follows the full backup filename grammar, with a real
    calendar date and time, is a backup.

    Args:
        name (str): The filename or object key, relative to the storage location.

    Returns:
        BackupFilename | None: The parsed parts, or None when the name is not a backup filename
            or its timestamp is not a real date and time, such as a month of 13.

    Examples:
        >>> parse_backup_filename("app-db-20250101T000000.tgz").set_name
        'app-db'
        >>> parse_backup_filename("app-20250101T000000-k3x9q.tgz").set_name
        'app'
        >>> parse_backup_filename("app-20251399T000000.tgz") is None
        True
    """
    match = _BACKUP_FILENAME_REGEX.fullmatch(name)
    if match is None:
        return None
    raw = match["timestamp"]
    try:
        timestamp = PlainDateTime.parse(raw, pattern=DEFAULT_DATE_PATTERN)
    except ValueError as e:
        logger.debug(
            f"Timestamp '{raw}' in '{name}' is not a real YYYYMMDDTHHMMSS date and time: {e}"
        )
        return None
    # whenever reads a leap second (60) as 59, so a round trip catches what parse lets through
    if timestamp.format(DEFAULT_DATE_PATTERN) != raw:
        logger.debug(f"Timestamp '{raw}' in '{name}' is not a real YYYYMMDDTHHMMSS date and time")
        return None
    return BackupFilename(set_name=match["set_name"], timestamp=timestamp)


def new_staging_filename() -> str:
    """Generate a unique temporary archive filename for staging a backup.

    Use to name a tmp-dir file while building or downloading an archive, before it is given its final backup name.

    Returns:
        str: A "{uid}.{ext}" name carrying no set name or timestamp.
    """
    return f"{new_uid(bits=24)}.{BACKUP_EXTENSION}"


def build_backup_name(*, name: str, timestamp: str) -> str:
    """Compose a backup filename from its parts.

    Use to produce consistent, sortable names that the retention logic later
    groups by timestamp.

    Args:
        name (str): The backup set name.
        timestamp (str): The formatted timestamp.

    Returns:
        str: "{name}-{timestamp}.{ext}".
    """
    return f"{name}-{timestamp}.{BACKUP_EXTENSION}"


def add_uid_suffix(name: str) -> str:
    """Append a short unique id before the extension to disambiguate a colliding filename.

    Args:
        name (str): The backup filename that collides with an existing one.

    Returns:
        str: The filename with "-{uid}" inserted before the extension.
    """
    return f"{name.removesuffix(f'.{BACKUP_EXTENSION}')}-{new_uid(bits=24)}.{BACKUP_EXTENSION}"
