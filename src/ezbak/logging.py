"""Log configuration for ezbak."""

import sys
from functools import partial
from pathlib import Path

from loguru import logger
from pydantic import ValidationError

from ezbak.constants import LogLevel


def log_validation_errors(error: ValidationError) -> None:
    """Log each config validation message so a bad config reads cleanly at the entry points.

    The CLI builder and the container entry point call this to turn a raw pydantic
    traceback into readable, actionable log lines before exiting non-zero.

    Args:
        error (ValidationError): The validation error raised while building the config.
    """
    for err in error.errors():
        logger.error(err["msg"])


def _stderr_log_formatter(record: dict, prefix: str | None = None) -> str:
    """Build the colorized loguru format template for the stderr sink.

    Assemble the format string loguru applies to terminal output: timestamp, color-tagged level, optional prefix, message, any extra fields, and the exception traceback when present. Use as the `format` callable when adding a stderr sink.

    Args:
        record (dict): The loguru record whose `extra`/`exception` keys decide which optional segments appear.
        prefix (str | None): Optional label inserted after the level. Defaults to None.

    Returns:
        str: The loguru format template string with color tags.
    """
    timestamp = "{time:YYYY-MM-DD HH:mm:ss} | "
    level = "<level>{level: <8}</level> | "
    prefix = f"{prefix} | " if prefix else ""
    message = "<level>{message}</level>"
    extras = " | <level>{extra}</level>" if record["extra"] else ""
    exception = "\n{exception}" if record["exception"] else ""

    return f"{timestamp}{level}{prefix}{message}{extras}{exception}\n"


def _log_file_formatter(record: dict, prefix: str | None = None) -> str:
    """Build the plain-text loguru format template for the log-file sink.

    Assemble the format string loguru applies to file output: timestamp, level, optional prefix, message, any extra fields, and the exception traceback when present. Unlike the stderr formatter this omits color tags so log files stay readable. Use as the `format` callable when adding a file sink.

    Args:
        record (dict): The loguru record whose `extra`/`exception` keys decide which optional segments appear.
        prefix (str | None): Optional label inserted after the level. Defaults to None.

    Returns:
        str: The loguru format template string without color tags.
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
