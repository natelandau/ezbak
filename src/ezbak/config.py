"""Typed backup configuration schema for ezbak.

Single source of truth for every ezbak option. Library callers construct this directly and never trigger environment loading. The CLI and container adapters build ``EnvConfig`` (a subclass in ``ezbak.env``), which populates these same fields from ``EZBAK_``-prefixed environment variables and ``.env`` files.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Self, TypeVar, cast

from pydantic import BaseModel, BeforeValidator, Field, PrivateAttr, model_validator

from ezbak.constants import (
    DEFAULT_COMPRESSION_LEVEL,
    Action,
    BackupType,
    LogLevel,
    RetentionPolicyType,
)

# Deferred to a function-local import in the `retention_policy` property below: importing
# ezbak.models eagerly here would re-enter ezbak.config before this module finishes defining
# BackupConfig, since ezbak.models (via the settings shim) imports BackupConfig back.
if TYPE_CHECKING:
    from collections.abc import Callable

    from ezbak.models.retention_policy import RetentionPolicyManager

E = TypeVar("E", bound=Enum)


def _make_enum_coercer(
    enum_cls: type[E],
    *,
    error_label: str,
    transform: Callable[[str], str] = str.lower,
    default: E | None = None,
) -> Callable[[str | E | None], E | None]:
    """Build a pydantic BeforeValidator that coerces a string into an enum member.

    Centralize the shared coercion shape (pass through None and existing members, normalize case, raise a uniform error) so each enum-typed setting declares only what differs: the enum, the case transform, and the value used when nothing is provided.

    Args:
        enum_cls (type[E]): The enum the value must resolve to.
        error_label (str): Human-readable name used in the error message.
        transform (Callable[[str], str]): Case normalizer applied before lookup. Defaults to str.lower.
        default (E | None): Value returned when the input is None. Defaults to None.

    Returns:
        Callable[[str | E | None], E | None]: A validator for use with pydantic BeforeValidator.
    """

    def coerce(value: str | E | None) -> E | None:
        if value is None:
            return default
        if isinstance(value, enum_cls):
            return value
        # value is a str here; cast tells the type checker so, since it cannot narrow the
        # TypeVar isinstance above on its own. No runtime effect.
        try:
            return enum_cls(transform(cast("str", value)))
        except ValueError as e:
            msg = f"Invalid {error_label}: must be one of {[x.value for x in enum_cls]}"
            raise ValueError(msg) from e

    return coerce


coerce_log_level = _make_enum_coercer(LogLevel, error_label="log level", transform=str.upper)
coerce_action = _make_enum_coercer(Action, error_label="action")


def coerce_path_list(value: list[str] | str | None) -> list[Path]:
    """Coerce the path list to a list of Path objects.

    Args:
        value (list[str] | str | None): The path list to validate.

    Returns:
        list[Path]: The validated path list.
    """
    if value is None:
        return []

    if isinstance(value, str):
        return [Path(x).expanduser().absolute() for x in value.split(",")]

    return [Path(path).expanduser().absolute() for path in value]


class BackupConfig(BaseModel):
    """Validated configuration for a set of ezbak backups.

    Build this to describe what to back up, where to store it, and how to name, retain, and restore it. Pass the instance to ``EZBak``.
    """

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
    log_level: Annotated[LogLevel | None, BeforeValidator(coerce_log_level)] = LogLevel.INFO
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

    @property
    def retention_policy(self) -> RetentionPolicyManager:
        """Retention policy manager for this backup configuration."""
        if self._cached_retention_policy:
            return self._cached_retention_policy

        # Local import: avoids a circular import with ezbak.models (see the module docstring note).
        from ezbak.models.retention_policy import RetentionPolicyManager  # noqa: PLC0415

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
                        destination.mkdir(parents=True, exist_ok=True)

        if not self.storage_paths and not self.aws_s3_bucket_name:
            msg = "No destination configured: set storage_paths and/or aws_s3_bucket_name"
            raise ValueError(msg)

        if self.storage_paths and self.entrypoint_action == Action.RESTORE:
            for destination in self.storage_paths:
                if not destination.exists():
                    msg = f"Backup storage path does not exist: {destination}"
                    raise ValueError(msg)

        return self
