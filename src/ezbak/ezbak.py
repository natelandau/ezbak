"""EZBak package for automated backup operations with retention policies and compression."""

from pathlib import Path

from nclutils import logger

from ezbak.controllers import BackupManager
from ezbak.models import settings


def ezbak(  # noqa: PLR0913
    name: str | None = None,
    *,
    storage_location: str | None = None,
    source_paths: list[Path | str] | None = None,
    storage_paths: list[Path | str] | None = None,
    tz: str | None = None,
    log_level: str | None = None,
    log_file: str | Path | None = None,
    log_prefix: str | None = None,
    compression_level: int | None = None,
    max_backups: int | None = None,
    retention_yearly: int | None = None,
    retention_monthly: int | None = None,
    retention_weekly: int | None = None,
    retention_daily: int | None = None,
    retention_hourly: int | None = None,
    retention_minutely: int | None = None,
    strip_source_paths: bool | None = None,
    exclude_regex: str | None = None,
    include_regex: str | None = None,
    chown_uid: int | None = None,
    chown_gid: int | None = None,
    label_time_units: bool | None = None,
    mongo_uri: str | None = None,
    mongo_db_name: str | None = None,
    aws_access_key: str | None = None,
    aws_secret_key: str | None = None,
    aws_s3_bucket_name: str | None = None,
    aws_s3_bucket_path: str | None = None,
) -> BackupManager:
    """Execute automated backups with configurable retention policies and compression.

    Creates timestamped backups of specified source directories/files to destination locations using the BackupManager. Supports flexible retention policies (count-based or time-based), file filtering with regex patterns, compression, and ownership changes. Ideal for automated backup scripts and scheduled backup operations.

    Args:
        name (str | None, optional): Unique identifier for the backup operation. Used for logging and backup labeling. Defaults to None.
        source_paths (list[Path | str] | None, optional): Source paths to backup. Can be files or directories. Defaults to None.
        storage_paths (list[Path | str] | None, optional): Destination paths where backups will be stored. Defaults to None.
        storage_location (str | None, optional): Storage location for backups. Defaults to None.
        strip_source_paths (bool | None, optional): Strip source paths from directory sources. Defaults to None.
        tz (str | None, optional): Timezone for timestamp formatting in backup names. Defaults to None.
        log_level (str, optional): Logging verbosity level. Defaults to "info".
        log_file (str | Path | None, optional): Path to log file. If None, logs to stdout. Defaults to None.
        log_prefix (str | None, optional): Prefix for log messages. Defaults to None.
        compression_level (int | None, optional): Compression level (1-9) for backup archives. Defaults to None.
        max_backups (int | None, optional): Maximum number of backups to retain (count-based retention). Defaults to None.
        retention_yearly (int | None, optional): Number of yearly backups to retain. Defaults to None.
        retention_monthly (int | None, optional): Number of monthly backups to retain. Defaults to None.
        retention_weekly (int | None, optional): Number of weekly backups to retain. Defaults to None.
        retention_daily (int | None, optional): Number of daily backups to retain. Defaults to None.
        retention_hourly (int | None, optional): Number of hourly backups to retain. Defaults to None.
        retention_minutely (int | None, optional): Number of minutely backups to retain. Defaults to None.
        exclude_regex (str | None, optional): Regex pattern to exclude files from backup. Defaults to None.
        include_regex (str | None, optional): Regex pattern to include only matching files. Defaults to None.
        chown_uid (int | None, optional): User ID to set ownership of backup files. Defaults to None.
        chown_gid (int | None, optional): Group ID to set ownership of backup files. Defaults to None.
        label_time_units (bool, optional): Include time units in backup filenames. Defaults to True.
        mongo_uri (str | None, optional): MongoDB URI for backup history. Defaults to None.
        mongo_db_name (str | None, optional): MongoDB database name for backup history. Defaults to None.
        aws_access_key (str | None, optional): AWS access key for S3 backup storage. Defaults to None.
        aws_secret_key (str | None, optional): AWS secret key for S3 backup storage. Defaults to None.
        aws_s3_bucket_name (str | None, optional): AWS S3 bucket name for backup storage. Defaults to None.
        aws_s3_bucket_path (str | None, optional): AWS S3 bucket path for backup storage. Defaults to None.

    Returns:
        BackupManager: Configured backup manager instance ready to execute backup operations.
    """
    source_paths = (
        [Path(source).expanduser().absolute() for source in source_paths] if source_paths else None
    )
    storage_paths = (
        [Path(path).expanduser().absolute() for path in storage_paths] if storage_paths else None
    )

    settings.update(
        {
            "storage_location": storage_location or None,
            "name": name or None,
            "source_paths": source_paths or None,
            "storage_paths": storage_paths or None,
            "strip_source_paths": strip_source_paths or None,
            "tz": tz or None,
            "log_level": log_level or None,
            "log_file": log_file or None,
            "log_prefix": log_prefix or None,
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
            "chown_uid": chown_uid or None,
            "chown_gid": chown_gid or None,
            "mongo_uri": mongo_uri or None,
            "mongo_db_name": mongo_db_name or None,
            "aws_access_key": aws_access_key or None,
            "aws_secret_key": aws_secret_key or None,
            "aws_s3_bucket_name": aws_s3_bucket_name or None,
            "aws_s3_bucket_path": aws_s3_bucket_path or None,
        }
    )

    logger.configure(
        log_level=settings.log_level.value,
        show_source_reference=False,
        log_file=str(settings.log_file) if settings.log_file else None,
        prefix=settings.log_prefix,
    )
    logger.info(f"Run ezbak for '{settings.name}'")

    settings.validate()

    return BackupManager()
