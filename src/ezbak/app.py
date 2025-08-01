"""The main EzBak application class."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nclutils import console, logger
from pydantic import ValidationError

from ezbak.constants import DEFAULT_COMPRESSION_LEVEL
from ezbak.controllers import BackupManager
from ezbak.models.settings import Settings

if TYPE_CHECKING:
    from pathlib import Path

    from ezbak.models import Backup


def ezbak(  # noqa: PLR0913
    name: str,
    *,
    storage_type: str = "local",
    source_paths: list[Path | str] | None = None,
    storage_paths: list[Path | str] | None = None,
    tz: str | None = None,
    log_level: str | None = None,
    log_file: str | Path | None = None,
    log_prefix: str | None = None,
    compression_level: int = DEFAULT_COMPRESSION_LEVEL,
    max_backups: int | None = None,
    retention_yearly: int | None = None,
    retention_monthly: int | None = None,
    retention_weekly: int | None = None,
    retention_daily: int | None = None,
    retention_hourly: int | None = None,
    retention_minutely: int | None = None,
    strip_source_paths: bool = False,
    delete_src_after_backup: bool = False,
    exclude_regex: str | None = None,
    include_regex: str | None = None,
    chown_uid: int | None = None,
    chown_gid: int | None = None,
    label_time_units: bool = True,
    aws_access_key: str | None = None,
    aws_secret_key: str | None = None,
    aws_s3_bucket_name: str | None = None,
    aws_s3_bucket_path: str | None = None,
) -> EZBakApp:
    """Execute automated backups with configurable retention policies and compression.

    Creates timestamped backups of specified source directories/files to destination locations using the BackupManager. Supports flexible retention policies (count-based or time-based), file filtering with regex patterns, compression, and ownership changes. Ideal for automated backup scripts and scheduled backup operations.

    Args:
        name (str): Unique identifier for the backup operation. Used for logging and backup labeling.
        source_paths (list[Path | str] | None, optional): Source paths to backup. Can be files or directories. Defaults to None.
        storage_paths (list[Path | str] | None, optional): Destination paths where backups will be stored. Defaults to None.
        storage_type (str | None, optional): Storage location for backups. Defaults to None.
        strip_source_paths (bool | None, optional): Strip source paths from directory sources. Defaults to None.
        delete_src_after_backup (bool | None, optional): Delete source paths after backup. Defaults to None.
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
        aws_access_key (str | None, optional): AWS access key for S3 backup storage. Defaults to None.
        aws_secret_key (str | None, optional): AWS secret key for S3 backup storage. Defaults to None.
        aws_s3_bucket_name (str | None, optional): AWS S3 bucket name for backup storage. Defaults to None.
        aws_s3_bucket_path (str | None, optional): AWS S3 bucket path for backup storage. Defaults to None.

    Returns:
        BackupManager: Configured backup manager instance ready to execute backup operations.

    Raises:
        ValidationError: If the provided settings are invalid.
    """
    func_args = locals()
    settings_kwargs = {key: value for key, value in func_args.items() if value is not None}

    try:
        config = Settings(**settings_kwargs, _env_file="")  # type: ignore [call-arg]
    except ValidationError as e:
        for error in e.errors():
            console.print(f"ERROR: {error['msg']}", style="red")
        raise

    return EZBakApp(config)


class EZBakApp:
    """The main EzBak application class."""

    def __init__(self, config: Settings | None = None) -> None:
        """Initialize the EzBak application.

        Args:
            config (Settings | None, optional): The configuration for the application.
                If not provided, the global settings will be used. Defaults to None.
        """
        self.settings = config
        if self.settings.log_level:
            self._configure_logging()
        self.backup_manager = BackupManager(config=self.settings)

    def _configure_logging(self) -> None:
        """Configure the logger."""
        logger.configure(
            log_level=self.settings.log_level.value,
            show_source_reference=False,
            log_file=str(self.settings.log_file) if self.settings.log_file else None,
            prefix=self.settings.log_prefix,
        )
        logger.info(f"Run ezbak for '{self.settings.name}'")

    def create_backup(self) -> None:
        """Create a backup."""
        self.backup_manager.create_backup()

    def restore_backup(
        self, restore_path: Path | str | None = None, *, clean_before_restore: bool = False
    ) -> bool:
        """Restore a backup.

        Args:
            restore_path (Path | str | None, optional): The path to restore the backup from. If not provided, the latest backup will be used. Defaults to None.
            clean_before_restore (bool, optional): Clean the restore path before restoring. Defaults to False.

        Returns:
            bool: True if the backup was restored successfully, False otherwise.
        """
        return self.backup_manager.restore_backup(
            restore_path, clean_before_restore=clean_before_restore
        )

    def prune_backups(self) -> list[Backup]:
        """Prune backups.

        Returns:
            list[Backup]: The list of pruned backups.
        """
        return self.backup_manager.prune_backups()

    def list_backups(self) -> list[Backup]:
        """List backups.

        Returns:
            list[Backup]: The list of backups.
        """
        return self.backup_manager.list_backups()

    def rename_backups(self) -> None:
        """Rename backups."""
        self.backup_manager.rename_backups()

    def get_latest_backup(self) -> Backup:
        """Get the latest backup from the storage locations.

        Find the most recent backup across all configured storage locations based on timestamp. Use this to identify the newest backup for restoration operations or to determine if new backups are needed.

        Returns:
            Backup: The latest backup, or None if no backups exist.
        """
        return self.backup_manager.get_latest_backup()
