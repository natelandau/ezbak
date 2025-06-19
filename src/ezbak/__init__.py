"""ezbak package."""

from pathlib import Path

from nclutils import logger

from ezbak.constants import DEFAULT_LABEL_TIME_UNITS
from ezbak.controllers import BackupManager
from ezbak.models import settings


def ezbak(  # noqa: PLR0913
    name: str | None = None,
    *,
    sources: list[Path | str] | None = None,
    destinations: list[Path | str] | None = None,
    tz: str | None = None,
    log_level: str = "info",
    log_file: str | Path | None = None,
    compression_level: int | None = None,
    max_backups: int | None = None,
    retention_yearly: int | None = None,
    retention_monthly: int | None = None,
    retention_weekly: int | None = None,
    retention_daily: int | None = None,
    retention_hourly: int | None = None,
    retention_minutely: int | None = None,
    exclude_regex: str | None = None,
    include_regex: str | None = None,
    chown_user: int | None = None,
    chown_group: int | None = None,
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
        max_backups (int | None, optional): Maximum number of backups to keep. Defaults to None.
        retention_yearly (int | None, optional): Maximum number of yearly backups to keep. Defaults to None.
        retention_monthly (int | None, optional): Maximum number of monthly backups to keep. Defaults to None.
        retention_weekly (int | None, optional): Maximum number of weekly backups to keep. Defaults to None.
        retention_daily (int | None, optional): Maximum number of daily backups to keep. Defaults to None.
        retention_hourly (int | None, optional): Maximum number of hourly backups to keep. Defaults to None.
        retention_minutely (int | None, optional): Maximum number of minutely backups to keep. Defaults to None.
        tz (str, optional): Timezone for timestamp formatting.
        log_level (str, optional): Logging level for the backup operation. Defaults to "info".
        log_file (str | None, optional): Path to log file. If None, logs to stdout. Defaults to None.
        chown_user (int | None, optional): User ID to change the ownership of the files to. Defaults to None.
        chown_group (int | None, optional): Group ID to change the ownership of the files to. Defaults to None.

    Returns:
        BackupManager: The backup manager instance.
    """
    settings.update(
        {
            "name": name or None,
            "sources": sources or None,
            "destinations": destinations or None,
            "tz": tz or None,
            "log_level": log_level or None,
            "log_file": log_file or None,
            "compression_level": compression_level or None,
            "max_backups": max_backups or None,
            "retention_yearly": retention_yearly or None,
            "retention_monthly": retention_monthly or None,
            "retention_weekly": retention_weekly or None,
            "retention_daily": retention_daily or None,
            "retention_hourly": retention_hourly or None,
            "retention_minutely": retention_minutely or None,
            "exclude_regex": exclude_regex or None,
            "include_regex": include_regex or None,
            "label_time_units": label_time_units if label_time_units is not None else None,
            "chown_user": chown_user or None,
            "chown_group": chown_group or None,
        }
    )

    logger.configure(
        log_level=log_level,
        show_source_reference=False,
        log_file=str(log_file) if log_file else None,
    )
    logger.info(f"Starting ezbak for {settings.backup_name}")

    settings.validate()

    return BackupManager()
