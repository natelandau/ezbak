"""Grammar for ezbak backup filenames.

Single source of truth for how backup filenames are composed and disambiguated, so the naming convention does not drift across the code that creates them.
"""

from nclutils.utils import new_uid

from ezbak.constants import BACKUP_EXTENSION, TIMESTAMP_REGEX


def is_backup_filename(name: str) -> bool:
    """Report whether a filename carries a backup timestamp, so indexing can skip foreign files.

    A storage location can hold files that match a set's glob or prefix but were not written by
    ezbak, such as a hand-named copy. Those have no timestamp to order or restore by.

    Args:
        name (str): The filename or object key to check.

    Returns:
        bool: True if the name contains a backup timestamp.
    """
    return TIMESTAMP_REGEX.search(name) is not None


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
