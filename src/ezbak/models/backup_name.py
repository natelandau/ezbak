"""Grammar for ezbak backup filenames.

Single source of truth for how backup filenames are composed and disambiguated, so the naming convention does not drift between the code that creates names and the code that rewrites them.
"""

from nclutils.utils import new_uid

from ezbak.constants import BACKUP_EXTENSION


def build_backup_name(*, name: str, timestamp: str, period: str | None = None) -> str:
    """Compose a backup filename from its parts.

    Use to produce consistent, sortable names that the retention and rename logic can later parse.

    Args:
        name (str): The backup set name.
        timestamp (str): The formatted timestamp.
        period (str | None): The time-unit label (e.g. "daily"). Omit for an unlabeled name. Defaults to None.

    Returns:
        str: "{name}-{timestamp}-{period}.{ext}", or "{name}-{timestamp}.{ext}" when period is None.
    """
    if period:
        return f"{name}-{timestamp}-{period}.{BACKUP_EXTENSION}"
    return f"{name}-{timestamp}.{BACKUP_EXTENSION}"


def add_uid_suffix(name: str) -> str:
    """Append a short unique id before the extension to disambiguate a colliding filename.

    Args:
        name (str): The backup filename that collides with an existing one.

    Returns:
        str: The filename with "-{uid}" inserted before the extension.
    """
    return f"{name.removesuffix(f'.{BACKUP_EXTENSION}')}-{new_uid(bits=24)}.{BACKUP_EXTENSION}"
