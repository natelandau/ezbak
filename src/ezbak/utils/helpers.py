"""Helper functions for the ezbak package."""

import os
import re
from pathlib import Path

from nclutils import logger

from ezbak.constants import ALWAYS_EXCLUDE_FILENAMES


def chown_files(directory: Path | str, uid: int, gid: int) -> None:
    """Recursively change ownership of all files in a directory to the configured user and group IDs.

    Updates file ownership for all files and subdirectories in the specified directory to match the configured user and group IDs. Does not change ownership of the parent directory.

    Args:
        directory (Path | str): Directory path to recursively update file ownership.
        uid (int): User ID to set for the files.
        gid (int): Group ID to set for the files.
    """
    logger.trace(f"Attempting to chown files in '{directory}'")
    if os.getuid() != 0:
        logger.warning("Not running as root, skip chown operations")
        return

    if isinstance(directory, str):
        directory = Path(directory)

    uid = int(uid)
    gid = int(gid)

    for path in directory.rglob("*"):
        try:
            os.chown(path.resolve(), uid, gid)
        except (OSError, PermissionError) as e:
            logger.warning(f"Failed to chown {path}: {e}")
            break

    logger.info(f"chown all restored files to '{uid}:{gid}'")


def should_include_file(
    *, path: Path, include_regex: str | None, exclude_regex: str | None
) -> bool:
    """Determine whether a file should be included in the backup based on configured regex filters.

    Apply include and exclude regex patterns to filter files during backup creation. Use this to implement fine-grained control over which files are backed up, such as excluding temporary files or including only specific file types.

    Args:
        path (Path): The file path to evaluate against the configured regex patterns.
        include_regex (str | None): The regex pattern to include files.
        exclude_regex (str | None): The regex pattern to exclude files.

    Returns:
        bool: True if the file should be included in the backup, False if it should be excluded.
    """
    if path.is_symlink():
        logger.warning(f"Skip backup of symlink: {path}")
        return False

    if path.name in ALWAYS_EXCLUDE_FILENAMES:
        logger.trace(f"Excluded file: {path.name}")
        return False

    if include_regex and re.search(rf"{include_regex}", str(path)) is None:
        logger.trace(f"Exclude by include regex: {path.name}")
        return False

    if exclude_regex and re.search(rf"{exclude_regex}", str(path)):
        logger.trace(f"Exclude by regex: {path.name}")
        return False

    return True
