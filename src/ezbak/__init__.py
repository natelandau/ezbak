"""ezbak package."""

from pathlib import Path

from environs import Env
from nclutils import logger

from ezbak.constants import DEFAULT_COMPRESSION_LEVEL, DEFAULT_LABEL_TIME_UNITS, ENVAR_PREFIX
from ezbak.controllers import BackupManager
from ezbak.models import Settings

env = Env(prefix=ENVAR_PREFIX)


def ezbak(  # noqa: PLR0913, PLR0917
    name: str | None = None,
    sources: list[Path | str] | None = None,
    destinations: list[Path | str] | None = None,
    tz: str | None = None,
    log_level: str = "info",
    log_file: str | Path | None = None,
    compression_level: int | None = None,
    time_based_policy: dict[str, int] | None = None,
    max_backups: int | None = None,
    exclude_regex: str | None = None,
    include_regex: str | None = None,
    *,
    label_time_units: bool = DEFAULT_LABEL_TIME_UNITS,
) -> BackupManager:
    """Perform automated backups of specified sources to destination locations.

    Creates a backup of the specified source directories/files to the destination locations with timestamped folders. The backup process is managed by the BackupManager class which handles the actual backup operations.

    Args:
        name (str): Identifier for the backup operation.
        sources (list[Path | str]): List of source paths to backup. Can be either Path objects or strings.
        destinations (list[Path | str]): List of destination paths where backups will be stored. Can be either Path objects or strings.
        exclude_regex (str | None, optional): Regex pattern to exclude files from the backup. Defaults to None.
        include_regex (str | None, optional): Regex pattern to include files in the backup. Defaults to None.
        compression_level (int, optional): The compression level for the backup file.
        label_time_units (bool, optional): Whether to label the time units in the backup filename. Defaults to True.
        time_based_policy (dict[str, int] | None, optional): Time-based retention policy. Defaults to None.
        max_backups (int | None, optional): Maximum number of backups to keep. Defaults to None.
        tz (str, optional): Timezone for timestamp formatting.
        log_level (str, optional): Logging level for the backup operation. Defaults to "info".
        log_file (str | None, optional): Path to log file. If None, logs to stdout. Defaults to None.

    Returns:
        BackupManager: The backup manager instance.
    """
    settings = Settings(
        name=env.str("NAME", None) or name,
        sources=env.list("SOURCES", None) or sources,
        destinations=env.list("DESTINATIONS", None) or destinations,
        tz=env.str("TZ", None) or tz,
        log_level=env.str("LOG_LEVEL", None) or log_level,
        log_file=env.str("LOG_FILE", None) or log_file,
        compression_level=env.int("COMPRESSION_LEVEL", None)
        or compression_level
        or DEFAULT_COMPRESSION_LEVEL,
        time_based_policy=env.dict("TIME_BASED_POLICY", None) or time_based_policy,
        max_backups=env.int("MAX_BACKUPS", None) or max_backups,
        exclude_regex=env.str("EXCLUDE_REGEX", None) or exclude_regex,
        include_regex=env.str("INCLUDE_REGEX", None) or include_regex,
        label_time_units=env.bool("LABEL_TIME_UNITS", None) or label_time_units,
    )

    logger.configure(log_level=log_level, show_source_reference=False, log_file=str(log_file))
    logger.info(f"Starting ezbak for {settings.backup_name}")

    return BackupManager(settings=settings)
