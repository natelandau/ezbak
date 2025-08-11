"""Provide the high-level application API for creating, listing, pruning, renaming, and restoring backups using validated settings."""

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
    """Create an `EZBakApp` configured for automated backups with retention and compression.

    Validate inputs via `Settings`, wire logging, and return an application object that exposes high-level backup operations. Use as a convenience factory from scripts and CLIs.

    Args:
        name (str): Unique identifier for the backup set used for labeling and logging.
        storage_type (str): Storage backend to use (e.g., "local", "s3").
        source_paths (list[Path | str] | None): Source files or directories to back up. Defaults to None.
        storage_paths (list[Path | str] | None): Destination paths for storing backups. Defaults to None.
        tz (str | None): Timezone for timestamps in backup names. Defaults to None.
        log_level (str | None): Log verbosity. Defaults to None.
        log_file (str | Path | None): File path for log output. Defaults to None.
        log_prefix (str | None): Prefix to include in each log line. Defaults to None.
        compression_level (int): Compression level (1-9). Defaults to DEFAULT_COMPRESSION_LEVEL.
        max_backups (int | None): Maximum number of backups to retain. Defaults to None.
        retention_yearly (int | None): Number of yearly backups to retain. Defaults to None.
        retention_monthly (int | None): Number of monthly backups to retain. Defaults to None.
        retention_weekly (int | None): Number of weekly backups to retain. Defaults to None.
        retention_daily (int | None): Number of daily backups to retain. Defaults to None.
        retention_hourly (int | None): Number of hourly backups to retain. Defaults to None.
        retention_minutely (int | None): Number of minutely backups to retain. Defaults to None.
        strip_source_paths (bool): Remove source path prefix when archiving directories. Defaults to False.
        delete_src_after_backup (bool): Delete source files after a successful backup. Defaults to False.
        exclude_regex (str | None): Regex pattern for paths to exclude. Defaults to None.
        include_regex (str | None): Regex pattern for paths to include. Defaults to None.
        chown_uid (int | None): UID to assign to created files. Defaults to None.
        chown_gid (int | None): GID to assign to created files. Defaults to None.
        label_time_units (bool): Include time units in backup labels. Defaults to True.
        aws_access_key (str | None): AWS access key for S3. Defaults to None.
        aws_secret_key (str | None): AWS secret key for S3. Defaults to None.
        aws_s3_bucket_name (str | None): S3 bucket name. Defaults to None.
        aws_s3_bucket_path (str | None): S3 bucket prefix or path. Defaults to None.

    Returns:
        EZBakApp: Application instance ready to perform backup operations.

    Raises:
        ValidationError: If provided settings are invalid.
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
    """Expose high-level operations to create, list, prune, rename, and restore backups backed by `BackupManager`."""

    def __init__(self, config: Settings | None = None) -> None:
        """Initialize the application with validated `Settings` and prepare logging and the backup manager.

        Args:
            config (Settings | None): Application settings. Prefer using `ezbak()` to construct a validated configuration. Defaults to None.
        """
        self.settings = config
        if self.settings.log_level:
            self._configure_logging()
        self.backup_manager = BackupManager(config=self.settings)

    def _configure_logging(self) -> None:
        """Configure structured logging according to `Settings`."""
        logger.configure(
            log_level=self.settings.log_level.value,
            show_source_reference=False,
            log_file=str(self.settings.log_file) if self.settings.log_file else None,
            prefix=self.settings.log_prefix,
        )
        logger.info(f"Run ezbak for '{self.settings.name}'")

    def create_backup(self) -> None:
        """Create a new backup using the current settings."""
        self.backup_manager.create_backup()

    def restore_backup(
        self, restore_path: Path | str | None = None, *, clean_before_restore: bool = False
    ) -> bool:
        """Restore the latest or specified backup to `restore_path`.

        Args:
            restore_path (Path | str | None): Target directory to restore into. When None, restore the latest backup to its original path or default target. Defaults to None.
            clean_before_restore (bool): Remove existing contents at the target before restoring. Defaults to False.

        Returns:
            bool: True when a backup is successfully restored; otherwise False.
        """
        return self.backup_manager.restore_backup(
            restore_path, clean_before_restore=clean_before_restore
        )

    def prune_backups(self) -> list[Backup]:
        """Apply retention policy and delete expired backups.

        Returns:
            list[Backup]: Backups that were pruned.
        """
        return self.backup_manager.prune_backups()

    def list_backups(self) -> list[Backup]:
        """List all discovered backups across storage locations.

        Returns:
            list[Backup]: Available backups.
        """
        return self.backup_manager.list_backups()

    def rename_backups(self) -> None:
        """Rename backups to match the current labeling configuration."""
        self.backup_manager.rename_backups()

    def get_latest_backup(self) -> Backup:
        """Return the most recent backup across all storage locations.

        Returns:
            Backup: Latest backup object.
        """
        return self.backup_manager.get_latest_backup()
