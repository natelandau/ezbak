"""Settings model."""

from dataclasses import dataclass
from pathlib import Path

from nclutils import logger

from ezbak.constants import BackupType, RetentionPolicyType
from ezbak.controllers.retention_policy_manager import RetentionPolicyManager


@dataclass
class Settings:
    """Settings model for EZBak."""

    name: str
    sources: list[str | Path]
    destinations: list[str | Path]
    tz: str
    log_level: str
    log_file: str | Path | None
    compression_level: int
    time_based_policy: dict[str, int] | None = None
    max_backups: int | None = None
    exclude_regex: str | None = None
    include_regex: str | None = None
    label_time_units: bool = True
    _source_paths: list[Path] | None = None
    _destination_paths: list[Path] | None = None
    _retention_policy: RetentionPolicyManager | None = None
    _backup_name: str | None = None

    @property
    def backup_name(self) -> str:
        """Get the backup name.

        If no backup name is provided, generate a random name.

        Returns:
            str: The backup name.

        Raises:
            ValueError: If no backup name is provided.
        """
        if self._backup_name:
            return self._backup_name

        if not self.name:
            msg = "No backup name provided"
            logger.error(msg)
            raise ValueError(msg)

        return self.name

    @property
    def source_paths(self) -> list[Path]:
        """Validate the source paths.

        Returns:
            list[Path]: The validated source paths.

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
    def destination_paths(self) -> list[Path]:
        """Validate the destination paths.

        Returns:
            list[Path]: The validated destination paths.

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
    def retention_policy(self) -> RetentionPolicyManager:
        """Get the retention policy.

        Returns:
            RetentionPolicyManager: The retention policy.
        """
        if self._retention_policy:
            return self._retention_policy

        if self.max_backups is not None:
            policy_type = RetentionPolicyType.COUNT_BASED
            self._retention_policy = RetentionPolicyManager(
                policy_type=policy_type, count_based_policy=self.max_backups
            )
        elif self.time_based_policy is not None:
            policy_type = RetentionPolicyType.TIME_BASED
            time_policy = {
                BackupType.MINUTELY: self.time_based_policy.get("minutely"),
                BackupType.HOURLY: self.time_based_policy.get("hourly"),
                BackupType.DAILY: self.time_based_policy.get("daily"),
                BackupType.WEEKLY: self.time_based_policy.get("weekly"),
                BackupType.MONTHLY: self.time_based_policy.get("monthly"),
                BackupType.YEARLY: self.time_based_policy.get("yearly"),
            }
            self._retention_policy = RetentionPolicyManager(
                policy_type=policy_type, time_based_policy=time_policy
            )
        else:
            self._retention_policy = RetentionPolicyManager(
                policy_type=RetentionPolicyType.KEEP_ALL
            )

        return self._retention_policy
