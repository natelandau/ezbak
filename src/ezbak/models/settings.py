"""Settings model for EZBak backup configuration and management."""

import atexit
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Self

from nclutils import logger
from pydantic import BeforeValidator, Field, PrivateAttr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ezbak.constants import (
    DEFAULT_COMPRESSION_LEVEL,
    ENVAR_PREFIX,
    Action,
    BackupType,
    LogLevel,
    RetentionPolicyType,
    StorageType,
)
from ezbak.controllers.retention_policy_manager import RetentionPolicyManager


def coerce_log_level(value: str | None) -> LogLevel | None:
    """Coerce the log level into a LogLevel enum.

    Args:
        value (str | None): The log level to validate.

    Returns:
        LogLevel: The validated log level.

    Raises:
        ValueError: If the log level is invalid.
    """
    if value is None:
        return None
    if isinstance(value, LogLevel):
        return value
    try:
        return LogLevel(value.upper())
    except ValueError as e:
        msg = f"Invalid log level: must be one of {[x.value for x in LogLevel]}"
        raise ValueError(msg) from e


def coerce_storage_type(value: str | None) -> StorageType | None:
    """Coerce the storage type into a StorageType enum.

    Args:
        value (str | None): The storage type to validate.

    Returns:
        StorageType: The validated storage type.

    Raises:
        ValueError: If the storage location is invalid.
    """
    if value is None:
        return StorageType.LOCAL

    if isinstance(value, StorageType):
        return value
    try:
        return StorageType(value.lower())
    except ValueError as e:
        msg = f"Invalid storage location: must be one of {[x.value for x in StorageType]}"
        raise ValueError(msg) from e


def coerce_action(value: str | None) -> Action | None:
    """Coerce the action into an Action enum.

    Args:
        value (str | None): The action to validate.

    Returns:
        Action: The validated action.

    Raises:
        ValueError: If the action is invalid.
    """
    if value is None:
        return None

    if isinstance(value, Action):
        return value

    try:
        return Action(value.lower())
    except ValueError as e:
        msg = f"Invalid action: must be one of {[x.value for x in Action]}"
        raise ValueError(msg) from e


def coerce_path_list(value: list[str] | str | None) -> list[Path]:
    """Coerce the path list to a list of Path objects.

    Args:
        value (list[str] | str | None): The path list to validate.

    Returns:
        list[Path] | None: The validated path list.
    """
    if value is None:
        return []

    if isinstance(value, str):
        return [Path(x).expanduser().absolute() for x in value.split(",")]

    return [Path(path).expanduser().absolute() for path in value]


class Settings(BaseSettings):
    """Configuration settings for EZBak backup operations."""

    model_config = SettingsConfigDict(
        env_prefix=ENVAR_PREFIX,
        extra="ignore",
        case_sensitive=False,
        env_file=[".env", ".env.secrets"],
        env_file_encoding="utf-8",
    )

    entrypoint_action: Annotated[Action | None, BeforeValidator(coerce_action)] = Field(
        default=None, alias="ezbak_action"
    )
    name: str | None = None
    source_paths: Annotated[list[Path] | None, BeforeValidator(coerce_path_list)] = Field(
        default_factory=list
    )
    storage_paths: Annotated[list[Path] | None, BeforeValidator(coerce_path_list)] = Field(
        default_factory=list
    )

    storage_type: Annotated[StorageType | None, BeforeValidator(coerce_storage_type)] = None

    strip_source_paths: bool = False
    delete_src_after_backup: bool = False
    exclude_regex: str | None = None
    include_regex: str | None = None
    compression_level: int = DEFAULT_COMPRESSION_LEVEL
    label_time_units: bool = True
    rename_files: bool = False

    max_backups: int | None = None
    retention_yearly: int | None = None
    retention_monthly: int | None = None
    retention_weekly: int | None = None
    retention_daily: int | None = None
    retention_hourly: int | None = None
    retention_minutely: int | None = None

    cron: str | None = None
    tz: str | None = None
    log_level: Annotated[LogLevel | None, BeforeValidator(coerce_log_level)] = None
    log_file: str | Path | None = None
    log_prefix: str | None = None

    restore_path: str | Path | None = None
    clean_before_restore: bool = False
    chown_uid: int | None = None
    chown_gid: int | None = None

    aws_access_key: str | None = None
    aws_s3_bucket_name: str | None = None
    aws_s3_bucket_path: str | None = None
    aws_secret_key: str | None = None

    _cached_retention_policy: RetentionPolicyManager | None = PrivateAttr(default=None)
    _cached_tmp_dir: TemporaryDirectory | None = PrivateAttr(default=None)

    @property
    def retention_policy(self) -> RetentionPolicyManager:
        """Get the retention policy manager for this backup configuration."""
        if self._cached_retention_policy:
            return self._cached_retention_policy

        if self.max_backups is not None:
            policy_type = RetentionPolicyType.COUNT_BASED
            self._cached_retention_policy = RetentionPolicyManager(
                policy_type=policy_type, count_based_policy=self.max_backups
            )
        elif any(
            [
                self.retention_yearly,
                self.retention_monthly,
                self.retention_weekly,
                self.retention_daily,
                self.retention_hourly,
                self.retention_minutely,
            ]
        ):
            policy_type = RetentionPolicyType.TIME_BASED
            time_policy = {
                BackupType.MINUTELY: self.retention_minutely,
                BackupType.HOURLY: self.retention_hourly,
                BackupType.DAILY: self.retention_daily,
                BackupType.WEEKLY: self.retention_weekly,
                BackupType.MONTHLY: self.retention_monthly,
                BackupType.YEARLY: self.retention_yearly,
            }
            self._cached_retention_policy = RetentionPolicyManager(
                policy_type=policy_type, time_based_policy=time_policy
            )
        else:
            self._cached_retention_policy = RetentionPolicyManager(
                policy_type=RetentionPolicyType.KEEP_ALL
            )

        return self._cached_retention_policy

    @property
    def tmp_dir(self) -> TemporaryDirectory:
        """Get the temporary directory."""
        if self._cached_tmp_dir is None:
            self._cached_tmp_dir = TemporaryDirectory()
            atexit.register(self.cleanup_tmp_dir)
        return self._cached_tmp_dir

    @model_validator(mode="after")
    def validate_settings(self) -> Self:  # noqa: C901
        """Validate that required settings are provided for backup operations.

        Returns:
            Self: The validated settings.

        Raises:
            ValueError: If the settings are invalid.
        """
        if not self.name:
            msg = "No backup name provided"
            raise ValueError(msg)

        if self.entrypoint_action == Action.BACKUP:
            if not self.source_paths:
                msg = "No source paths provided but are required for backup"
                raise ValueError(msg)

            for source in self.source_paths:
                if not source.exists():
                    msg = f"Source does not exist: {source}"
                    raise ValueError(msg)

            if self.storage_paths:
                for destination in self.storage_paths:
                    if not destination.exists():
                        logger.info(f"Create backup storage dir: {destination}")
                        destination.mkdir(parents=True, exist_ok=True)

        if not self.storage_paths and self.storage_type != StorageType.AWS:
            msg = "No local storage paths provided"
            raise ValueError(msg)

        if self.storage_paths and self.entrypoint_action == Action.RESTORE:
            for destination in self.storage_paths:
                if not destination.exists():
                    msg = f"Backup storage path does not exist: {destination}"
                    raise ValueError(msg)

        return self

    def cleanup_tmp_dir(self) -> None:
        """Clean up the temporary directory."""
        if self._cached_tmp_dir:
            self._cached_tmp_dir.cleanup()
            self._cached_tmp_dir = None
