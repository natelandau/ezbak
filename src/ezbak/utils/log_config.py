"""Log configuration for ezbak."""

import sys
from functools import partial
from pathlib import Path

from loguru import logger

from ezbak.constants import LogLevel


def _stderr_log_formatter(record: dict, prefix: str | None = None) -> str:
    """Format log records for stderr output with color and metadata.

    Format log records with timestamp, level, message, extra fields, and source location. Prints the raw record for debugging and returns a formatted string with color tags for the level and metadata.

    Args:
        record (Record): The loguru Record object containing log event data.
        prefix (str | None): The prefix to add to the log message.

    Returns:
        str: A formatted log string with color tags and metadata.
    """
    timestamp = "{time:YYYY-MM-DD HH:mm:ss} | "
    level = "<level>{level: <8}</level> | "
    prefix = f"{prefix} | " if prefix else ""
    message = "<level>{message}</level>"
    extras = " | <level>{extra}</level>" if record["extra"] else ""
    exception = "\n{exception}" if record["exception"] else ""

    return f"{timestamp}{level}{prefix}{message}{extras}{exception}\n"


def _log_file_formatter(record: dict, prefix: str | None = None) -> str:
    """Format log records for log file output with color and metadata.

    Format log records with timestamp, level, message, extra fields, and source location. Prints the raw record for debugging and returns a formatted string with color tags for the level and metadata.

    Args:
        record (Record): The loguru Record object containing log event data.
        prefix (str | None): The prefix to add to the log message.

    Returns:
        str: A formatted log string with color tags and metadata.
    """
    timestamp = "{time:YYYY-MM-DD HH:mm:ss} | "
    level = "{level: <8} | "
    prefix = f"{prefix} | " if prefix else ""
    message = "{message}"
    extras = " | {extra}" if record["extra"] else ""
    exception = "\n{exception}" if record["exception"] else ""

    return f"{timestamp}{level}{prefix}{message}{extras}{exception}\n"


def instantiate_logger(
    log_level: LogLevel, log_file: Path | str | None = None, prefix: str | None = None
) -> None:  # pragma: no cover
    """Instantiate the Loguru logger for ezbak.

    Configure the logger with the specified verbosity level, log file path,
    and whether to log to a file.

    Args:
        log_level (LogLevel): The verbosity level for the logger.
        log_file (Path | str | None): The log file path.
        prefix (str | None): The prefix to add to the log message.
    """
    log_level_name = log_level.value

    # Configure Loguru
    logger.remove()
    logger.add(
        sys.stderr,
        level=log_level_name,
        format=partial(_stderr_log_formatter, prefix=prefix),
        colorize=True,
    )
    if log_file:
        path_to_log_file = Path(log_file).expanduser().resolve()

        if not path_to_log_file.parent.exists():
            path_to_log_file.parent.mkdir(parents=True, exist_ok=True)

        logger.add(
            log_file,
            level=log_level_name,
            format=partial(_log_file_formatter, prefix=prefix),
            rotation="10 MB",
            retention=3,
            compression="zip",
        )
