"""Helper functions for the ezbak package."""

import atexit

from nclutils import logger

from ezbak.models import settings


def cleanup_tmp_dir() -> None:
    """Clean up the temporary directory to prevent disk space accumulation.

    Removes the temporary directory created during backup operations to free up disk space and maintain system cleanliness.
    """
    if settings._tmp_dir:  # noqa: SLF001
        settings._tmp_dir.cleanup()  # noqa: SLF001
        logger.trace("Temporary directory cleaned up")

    # Ensure that this function is only called once even if it is registered multiple times.
    atexit.unregister(cleanup_tmp_dir)
