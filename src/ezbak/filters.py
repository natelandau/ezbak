"""File filtering and path validation helpers for the ezbak package."""

import os
import re
from pathlib import Path

from loguru import logger

from ezbak.constants import ALWAYS_EXCLUDE_FILENAMES
from ezbak.exceptions import ConfigurationError


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

    failures = 0
    for path in directory.rglob("*"):
        try:
            # lchown targets the entry itself: a symlink inside the restored tree
            # must never chown whatever it points at (possibly outside the tree).
            os.lchown(path=path, uid=uid, gid=gid)
        except OSError as e:
            failures += 1
            logger.warning(f"Failed to chown {path}: {e}")

    if failures:
        logger.warning(f"chown restored files to '{uid}:{gid}' finished with {failures} failures")
    else:
        logger.info(f"chown all restored files to '{uid}:{gid}'")


def compile_filter_patterns(
    include_regex: str | None, exclude_regex: str | None
) -> tuple[re.Pattern[str] | None, re.Pattern[str] | None]:
    """Compile the configured include/exclude regexes once for a backup run.

    Compile up front so the per-entry filter does not pay a regex-cache lookup per
    file, and so both the directory-walk filter and the single-file source path
    share one compilation step.

    Args:
        include_regex (str | None): The include regex, or None to include all.
        exclude_regex (str | None): The exclude regex, or None to exclude none.

    Returns:
        tuple[re.Pattern[str] | None, re.Pattern[str] | None]: The compiled include and exclude patterns.
    """
    return (
        re.compile(include_regex) if include_regex else None,
        re.compile(exclude_regex) if exclude_regex else None,
    )


def passes_filters(
    *,
    path: Path | str,
    include_pattern: re.Pattern[str] | None,
    exclude_pattern: re.Pattern[str] | None,
) -> bool:
    """Apply the always-exclude and regex backup filters to a path without touching the filesystem.

    The single definition of which files a backup includes, shared by the
    directory add-filter and the single-file source path so the two can never
    diverge. Accepts a plain string so the per-entry hot loop can pass a
    prebuilt path string instead of constructing a Path per file, and trace
    messages use loguru's deferred formatting so no log string is built per
    file when TRACE is off.

    Args:
        path (Path | str): The full file path to evaluate.
        include_pattern (re.Pattern[str] | None): Compiled include pattern, or None to include all.
        exclude_pattern (re.Pattern[str] | None): Compiled exclude pattern, or None to exclude none.

    Returns:
        bool: True if the file should be included in the backup.
    """
    path_str = str(path)
    name = path_str.rpartition("/")[2]
    if name in ALWAYS_EXCLUDE_FILENAMES:
        logger.trace("Excluded file: {}", name)
        return False

    if include_pattern and include_pattern.search(path_str) is None:
        logger.trace("Exclude by include regex: {}", name)
        return False

    if exclude_pattern and exclude_pattern.search(path_str):
        logger.trace("Exclude by regex: {}", name)
        return False

    return True


def validate_source_paths(source_paths: list[Path] | None) -> None:
    """Validate that at least one source path is configured and every one exists.

    Args:
        source_paths (list[Path] | None): The source paths to validate.

    Raises:
        ConfigurationError: If no source paths are provided or a source path does not exist.
    """
    if not source_paths:
        msg = "No source paths provided"
        raise ConfigurationError(msg)

    for source in source_paths:
        if not source.exists():
            msg = f"Source does not exist: {source}"
            raise ConfigurationError(msg)


def validate_storage_paths(
    storage_paths: list[Path] | None, *, create_if_missing: bool = False
) -> None:
    """Validate that at least one storage path is configured and reachable.

    Args:
        storage_paths (list[Path] | None): The storage paths to validate.
        create_if_missing (bool): Whether to create the storage paths if they do not exist.

    Raises:
        ConfigurationError: If no storage paths are provided or a storage path does not exist and create_if_missing is False.
    """
    if not storage_paths:
        msg = "No storage paths provided"
        raise ConfigurationError(msg)

    for storage_path in storage_paths:
        if not storage_path.exists():
            if create_if_missing:
                storage_path.mkdir(parents=True, exist_ok=True)
            else:
                msg = f"Storage path does not exist: {storage_path}"
                raise ConfigurationError(msg)
