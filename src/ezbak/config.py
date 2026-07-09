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
    BackupType,
    LogLevel,
)
from ezbak.retention import RetentionPolicyManager

if TYPE_CHECKING:
    from collections.abc import Callable

E = TypeVar("E", bound=Enum)


def make_enum_coercer(
    enum_cls: type[E],
    *,
    error_label: str,
    transform: Callable[[str], str] = str.lower,
) -> Callable[[str | E | None], E | None]:
    """Build a pydantic BeforeValidator that coerces a string into an enum member.

    Centralize the shared coercion shape (pass through None and existing members, normalize case, raise a uniform error) so each enum-typed setting declares only what differs: the enum and the case transform.

    Args:
        enum_cls (type[E]): The enum the value must resolve to.
        error_label (str): Human-readable name used in the error message.
        transform (Callable[[str], str]): Case normalizer applied before lookup. Defaults to str.lower.

    Returns:
        Callable[[str | E | None], E | None]: A validator for use with pydantic BeforeValidator.
    """

    def coerce(value: str | E | None) -> E | None:
        if value is None:
            return None
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


coerce_log_level = make_enum_coercer(LogLevel, error_label="log level", transform=str.upper)


def coerce_path_list(value: list[str] | str | None) -> list[Path]:
    """Coerce the path list to a list of Path objects.

    Blank or whitespace-only entries are skipped rather than resolved to the current working directory.

    Args:
        value (list[str] | str | None): The path list to validate.

    Returns:
        list[Path]: The validated path list.
    """
    if value is None:
        return []

    if isinstance(value, str):
        return [Path(x.strip()).expanduser().absolute() for x in value.split(",") if x.strip()]

    return [Path(str(path).strip()).expanduser().absolute() for path in value if str(path).strip()]


class BackupConfig(BaseModel):
    """Validated configuration for a set of ezbak backups.

    Build this to describe what to back up, where to store it, and how to name, retain, and restore it. Pass the instance to ``EZBak``.
    """

    name: str | None = None
    source_paths: Annotated[list[Path] | None, BeforeValidator(coerce_path_list)] = Field(
        default_factory=list
    )
    storage_paths: Annotated[list[Path] | None, BeforeValidator(coerce_path_list)] = Field(
        default_factory=list
    )

    strip_source_paths: bool = False
    delete_source_after_backup: bool = False
    exclude_regex: str | None = None
    include_regex: str | None = None
    compression_level: int = DEFAULT_COMPRESSION_LEVEL
    # Write a .sha256 sidecar next to each new backup. Existing sidecars are always
    # verified on restore regardless of this setting; this only gates generation.
    write_checksums: bool = True

    # A keep rule is a non-negative count; 0 keeps nothing, None disables the rule.
    # ge=0 rejects a negative value (e.g. a mistyped EZBAK_KEEP_* env var) at
    # construction rather than letting it silently prune the wrong backups via
    # slice semantics.
    keep_last: int | None = Field(default=None, ge=0)
    keep_minutely: int | None = Field(default=None, ge=0)
    keep_hourly: int | None = Field(default=None, ge=0)
    keep_daily: int | None = Field(default=None, ge=0)
    keep_weekly: int | None = Field(default=None, ge=0)
    keep_monthly: int | None = Field(default=None, ge=0)
    keep_yearly: int | None = Field(default=None, ge=0)

    cron: str | None = None
    tz: str | None = None
    log_level: Annotated[LogLevel | None, BeforeValidator(coerce_log_level)] = LogLevel.INFO
    log_file: str | Path | None = None
    log_prefix: str | None = None

    restore_path: str | Path | None = None
    restore_date: str | None = None
    clean_before_restore: bool = False
    # A pre-start restore on a fresh deployment has no backup yet. When set, the CLI and
    # container treat "no backup matched" as a successful no-op instead of a failure, so an
    # orchestrator can still start the job. A real download or extract failure still fails.
    # Library callers get the same signal from restore_backup()'s False return value.
    restore_if_exists: bool = False
    chown_uid: int | None = None
    chown_gid: int | None = None

    aws_access_key: str | None = None
    aws_s3_bucket_name: str | None = None
    aws_s3_bucket_prefix: str | None = None
    aws_secret_key: str | None = None

    _cached_retention_policy: RetentionPolicyManager | None = PrivateAttr(default=None)

    @property
    def retention_policy(self) -> RetentionPolicyManager:
        """Retention policy manager for this backup configuration."""
        if self._cached_retention_policy:
            return self._cached_retention_policy

        self._cached_retention_policy = RetentionPolicyManager(
            keep_last=self.keep_last,
            calendar={
                BackupType.MINUTELY: self.keep_minutely,
                BackupType.HOURLY: self.keep_hourly,
                BackupType.DAILY: self.keep_daily,
                BackupType.WEEKLY: self.keep_weekly,
                BackupType.MONTHLY: self.keep_monthly,
                BackupType.YEARLY: self.keep_yearly,
            },
        )
        return self._cached_retention_policy

    @model_validator(mode="after")
    def validate_settings(self) -> Self:
        """Validate that required settings are provided for backup operations.

        Returns:
            Self: The validated settings.

        Raises:
            ValueError: If the settings are invalid.
        """
        if not self.name:
            msg = "No backup name provided"
            raise ValueError(msg)

        if not self.storage_paths and not self.aws_s3_bucket_name:
            msg = "No storage configured: set storage_paths and/or aws_s3_bucket_name"
            raise ValueError(msg)

        return self
