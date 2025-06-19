"""Settings model for EZBak backup configuration and management."""

import sys
from dataclasses import dataclass
from pathlib import Path

from environs import Env, validate
from nclutils import logger
from rich.console import Console

from ezbak.constants import DEFAULT_COMPRESSION_LEVEL, ENVAR_PREFIX, BackupType, RetentionPolicyType
from ezbak.controllers.retention_policy_manager import RetentionPolicyManager

env = Env(prefix=ENVAR_PREFIX)

err_console = Console(stderr=True)


@dataclass
class Settings:
    """Configuration settings for EZBak backup operations.

    Stores all configuration parameters needed for backup operations including source/destination paths,
    retention policies, compression settings, and operational flags. Provides validation and property
    accessors for computed values like resolved paths and retention policy objects.
    """

    action: str | None = None
    name: str | None = None
    sources: list[str | Path] | None = None
    destinations: list[str | Path] | None = None

    max_backups: int | None = None
    retention_yearly: int | None = None
    retention_monthly: int | None = None
    retention_weekly: int | None = None
    retention_daily: int | None = None
    retention_hourly: int | None = None
    retention_minutely: int | None = None

    tz: str | None = None
    log_level: str = "INFO"
    log_file: str | Path | None = None
    compression_level: int = DEFAULT_COMPRESSION_LEVEL

    exclude_regex: str | None = None
    include_regex: str | None = None
    label_time_units: bool = True
    chown_user: int | None = None
    chown_group: int | None = None
    cron: str | None = None
    rename_files: bool = False
    _source_paths: list[Path] | None = None
    _destination_paths: list[Path] | None = None
    _retention_policy: RetentionPolicyManager | None = None
    _backup_name: str | None = None

    def validate(self) -> None:
        """Validate that required settings are provided for backup operations.

        Ensures that a backup name, source paths, and destination paths are specified before
        attempting any backup operations. Raises ValueError if any required settings are missing.

        Raises:
            ValueError: If settings are invalid.
        """
        if not self.name:
            msg = "No backup name provided"
            logger.error(msg)
            raise ValueError(msg)

        if not self.sources:
            msg = "No source paths provided"
            logger.error(msg)
            raise ValueError(msg)

        if not self.destinations:
            msg = "No destination paths provided"
            logger.error(msg)
            raise ValueError(msg)

    @property
    def backup_name(self) -> str:
        """Get the backup name for identifying this backup operation.

        Returns the configured backup name or generates a random name if none is provided.
        Used throughout the backup process to identify and label backup files and logs.

        Returns:
            str: The backup name to use for this operation.

        Raises:
            ValueError: If no backup name is provided and cannot be generated.
        """
        if self._backup_name:
            return self._backup_name

        if not self.name:
            msg = "No backup name provided"
            logger.error(msg)
            raise ValueError(msg)

        return self.name

    @property
    def destination_paths(self) -> list[Path]:
        """Get validated and resolved destination paths for backup storage.

        Converts destination strings to Path objects, resolves them to absolute paths,
        and creates directories if they don't exist. Caches the result for performance.

        Returns:
            list[Path]: List of absolute Path objects representing backup destinations.

        Raises:
            ValueError: If no destination paths are provided.
        """
        if self._destination_paths:
            return self._destination_paths

        if not self.destinations:
            msg = "No destination paths provided"
            logger.error(msg)
            raise ValueError(msg)

        self._destination_paths = list(
            {Path(destination).expanduser().resolve() for destination in self.destinations}
        )

        for destination in self._destination_paths:
            if not destination.exists():
                logger.info(f"Create destination: {destination}")
                destination.mkdir(parents=True, exist_ok=True)

        return self._destination_paths

    @property
    def source_paths(self) -> list[Path]:
        """Get validated and resolved source paths for backup operations.

        Converts source strings to Path objects, resolves them to absolute paths,
        and validates that all paths exist. Caches the result for performance.

        Returns:
            list[Path]: List of absolute Path objects representing backup sources.

        Raises:
            FileNotFoundError: If any of the source paths do not exist.
            ValueError: If no source paths are provided.
        """
        if self._source_paths:
            return self._source_paths

        if not self.sources:
            msg = "No source paths provided"
            logger.error(msg)
            raise ValueError(msg)

        self._source_paths = list({Path(source).expanduser().resolve() for source in self.sources})

        for source in self._source_paths:
            if not isinstance(source, Path) or not source.exists():
                msg = f"Source does not exist: {source}"
                logger.error(msg)
                raise FileNotFoundError(msg)

        return self._source_paths

    @property
    def retention_policy(self) -> RetentionPolicyManager:
        """Get the retention policy manager for this backup configuration.

        Creates and returns a RetentionPolicyManager based on the configured retention settings.
        Supports count-based, time-based, or keep-all policies depending on the settings provided.
        Caches the result for performance.

        Returns:
            RetentionPolicyManager: The retention policy manager for this backup configuration.
        """
        if self._retention_policy:
            return self._retention_policy

        if self.max_backups is not None:
            policy_type = RetentionPolicyType.COUNT_BASED
            self._retention_policy = RetentionPolicyManager(
                policy_type=policy_type, count_based_policy=self.max_backups
            )
        elif not self.max_backups and any(
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
            self._retention_policy = RetentionPolicyManager(
                policy_type=policy_type, time_based_policy=time_policy
            )
        else:
            self._retention_policy = RetentionPolicyManager(
                policy_type=RetentionPolicyType.KEEP_ALL
            )

        return self._retention_policy

    def update(self, updates: dict[str, str | int | Path | bool | list[Path | str]]) -> None:
        """Update settings with provided key-value pairs and reset cached properties.

        Validates that all keys exist as attributes on the settings object before updating.
        Resets cached properties when their underlying data changes to ensure consistency.

        Args:
            updates (dict[str, str | int | Path | bool | list[Path | str]]): Dictionary of setting keys and their new values.
        """
        for key, value in updates.items():
            try:
                getattr(self, key)
            except AttributeError:
                msg = f"'ERROR: {key}' does not exist in settings"
                err_console.print(msg)
                sys.exit(1)

            if value is not None:
                setattr(self, key, value)

        # Reset cached properties
        update_keys = updates.keys()

        if "sources" in update_keys:
            self._source_paths = None
        if "destinations" in update_keys:
            self._destination_paths = None
        if "name" in update_keys:
            self._backup_name = None

        retention_keys = {
            "retention_yearly",
            "retention_monthly",
            "retention_weekly",
            "retention_daily",
            "retention_hourly",
            "retention_minutely",
        }
        if retention_keys & update_keys:
            self._retention_policy = None

    def model_dump(self) -> dict[str, int | str | bool | list[Path | str] | None]:
        """Serialize settings to a dictionary representation.

        Converts all settings attributes to a dictionary format for serialization,
        logging, or configuration export purposes.

        Returns:
            dict[str, int | str | bool | list[Path | str] | None]: Dictionary representation of all settings.
        """
        return self.__dict__


@dataclass
class SettingsManager:
    """Singleton manager for EZBak settings initialization and CLI overrides.

    Provides centralized management of the Settings singleton, handling initialization
    from environment variables and applying CLI argument overrides while maintaining
    the singleton pattern for consistent settings access throughout the application.
    """

    _instance: Settings | None = None

    @classmethod
    def initialize(cls) -> Settings:
        """Initialize settings from environment variables using the singleton pattern.

        Creates a Settings instance from environment variables if not already initialized,
        ensuring consistent settings access throughout the application lifecycle.

        Returns:
            Settings: The initialized settings singleton instance.
        """
        if cls._instance is not None:
            return cls._instance

        settings = Settings(
            name=env.str("NAME", None),
            sources=env.list("SOURCES", None),
            destinations=env.list("DESTINATIONS", None),
            action=env.str(
                "ACTION",
                default=None,
                validate=validate.OneOf(
                    ["backup", "restore", None], error="ACTION must be one of: {choices}"
                ),
            ),
            tz=env.str("TZ", None),
            log_level=env.str(
                "LOG_LEVEL",
                default="INFO",
                validate=validate.OneOf(
                    ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                    error="LOG_LEVEL must be one of: {choices}",
                ),
            ),
            log_file=env.str("LOG_FILE", None),
            compression_level=env.int(
                "COMPRESSION_LEVEL",
                default=DEFAULT_COMPRESSION_LEVEL,
                validate=validate.OneOf(
                    [1, 2, 3, 4, 5, 6, 7, 8, 9],
                    error="COMPRESSION_LEVEL must be one of: {choices}",
                ),
            ),
            cron=env.str("CRON", default=None),
            max_backups=env.int("MAX_BACKUPS", None),
            exclude_regex=env.str("EXCLUDE_REGEX", None),
            include_regex=env.str("INCLUDE_REGEX", None),
            label_time_units=env.bool("LABEL_TIME_UNITS", None),
            chown_user=env.int("CHOWN_USER", None),
            chown_group=env.int("CHOWN_GROUP", None),
            rename_files=env.bool("RENAME_FILES", default=False),
            retention_yearly=env.int("RETENTION_YEARLY", default=None),
            retention_monthly=env.int("RETENTION_MONTHLY", default=None),
            retention_weekly=env.int("RETENTION_WEEKLY", default=None),
            retention_daily=env.int("RETENTION_DAILY", default=None),
            retention_hourly=env.int("RETENTION_HOURLY", default=None),
            retention_minutely=env.int("RETENTION_MINUTELY", default=None),
        )

        cls._instance = settings
        return settings

    @classmethod
    def apply_cli_settings(
        cls, cli_settings: dict[str, str | int | Path | bool | list[Path | str]]
    ) -> None:
        """Override existing settings with non-None values from CLI arguments.

        Updates the settings singleton with any non-None values provided via command line arguments,
        preserving existing values for unspecified settings. Filters out None values to avoid
        overriding existing settings with None.

        Args:
            cli_settings (dict[str, str | int | Path | bool | list[Path | str]]): Dictionary of settings from CLI arguments to apply as overrides.
        """
        settings = cls._instance
        if settings is None:  # pragma: no cover
            msg = "ERROR: Settings not initialized"
            err_console.print(msg)
            sys.exit(1)

        # Filter out None values to avoid overriding with None
        cli_overrides = {k: v for k, v in cli_settings.items() if v is not None}
        settings.update(cli_overrides)


# Initialize settings singleton
settings = SettingsManager.initialize()
