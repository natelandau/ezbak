"""Grammar for ezbak backup filenames.

Single source of truth for how backup filenames are composed and disambiguated, so the naming convention does not drift across the code that creates them.
"""

from nclutils.utils import new_uid

from ezbak.constants import BACKUP_EXTENSION


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
