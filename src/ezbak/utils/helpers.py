"""Helper functions for the ezbak package."""

import atexit
import contextlib
import os
from pathlib import Path

from nclutils import logger
from rich.console import Console

from ezbak.constants import LogLevel
from ezbak.models import settings

err_console = Console(stderr=True)


def cleanup_tmp_dir() -> None:
    """Clean up the temporary directory to prevent disk space accumulation.

    Removes the temporary directory created during backup operations to free up disk space and maintain system cleanliness.
    """
    if settings._tmp_dir:  # noqa: SLF001
        settings._tmp_dir.cleanup()  # noqa: SLF001

        # Suppress errors when loguru handlers are closed early during test cleanup.
        with contextlib.suppress(ValueError, OSError):
            if settings.log_level == LogLevel.TRACE:
                log_prefix = f"{settings.log_prefix} | " if settings.log_prefix else ""
                msg = f"TRACE    | {log_prefix}Temporary directory cleaned up"
                err_console.print(msg)

    # Ensure that this function is only called once even if it is registered multiple times.
    atexit.unregister(cleanup_tmp_dir)


def chown_files(directory: Path | str) -> None:
    """Recursively change ownership of all files in a directory to the configured user and group IDs.

    Updates file ownership for all files and subdirectories in the specified directory to match the configured user and group IDs. Does not change ownership of the parent directory.

    Args:
        directory (Path | str): Directory path to recursively update file ownership.
    """
    if os.getuid() != 0:
        logger.warning("Not running as root, skip chown operations")
        return

    if isinstance(directory, str):
        directory = Path(directory)

    uid = int(settings.chown_uid)
    gid = int(settings.chown_gid)

    for path in directory.rglob("*"):
        try:
            os.chown(path.resolve(), uid, gid)
        except (OSError, PermissionError) as e:
            logger.warning(f"Failed to chown {path}: {e}")
            break

        logger.trace(f"chown: {path.resolve()}")

    logger.info(f"chown all restored files to '{uid}:{gid}'")
