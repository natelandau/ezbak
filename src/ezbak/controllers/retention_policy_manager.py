"""Retention policy manager for controlling backup lifecycle and storage management."""

from nclutils import logger

from ezbak.constants import DEFAULT_RETENTION, BackupType, RetentionPolicyType


class RetentionPolicyManager:
    """Manage backup retention policies for automated storage cleanup and lifecycle management.

    Handles different types of retention policies including count-based limits, time-based categorization (yearly, monthly, weekly, daily, hourly, minutely), and keep-all policies. Provides methods to determine retention limits for backup types and generate policy summaries for configuration and logging purposes.
    """

    def __init__(
        self,
        policy_type: RetentionPolicyType,
        time_based_policy: dict[BackupType, int] | None = None,
        count_based_policy: int | None = None,
    ):
        """Initialize retention policy manager with specified policy configuration.

        Args:
            policy_type (RetentionPolicyType): Type of retention policy to enforce.
            time_based_policy (dict[BackupType, int] | None, optional): Time-based retention limits for each backup type. Defaults to None.
            count_based_policy (int | None, optional): Maximum number of backups to retain. Defaults to None.
        """
        self.policy_type = policy_type
        self._time_based_policy = time_based_policy or {}
        self._count_based_policy = count_based_policy

    def get_retention(self, backup_type: BackupType) -> int:
        """Get retention limit for a specific backup type based on current policy.

        Determines how many backups of the specified type should be retained according to the configured policy. Returns None for keep-all policies, count-based limits for count-based policies, or time-based limits for time-based policies.

        Args:
            backup_type (BackupType): Backup type to get retention limit for.

        Returns:
            int | None: Number of backups to retain for the specified type, or None for keep-all policies.
        """
        if self.policy_type == RetentionPolicyType.KEEP_ALL:
            return None

        if self.policy_type == RetentionPolicyType.COUNT_BASED:
            policy = self._count_based_policy or DEFAULT_RETENTION
            logger.trace(f"Count based policy: {policy}")
            return policy

        policy = self._time_based_policy.get(backup_type) or DEFAULT_RETENTION
        logger.trace(f"Time based policy: {backup_type}: {policy}")
        return policy

    def get_full_policy(self) -> dict[str, int]:
        """Generate complete policy configuration as a dictionary.

        Creates a dictionary representation of the current retention policy for configuration export, logging, or policy validation. Returns count-based policy as max_backups key or time-based policy as individual backup type keys.

        Returns:
            dict[str, int]: Dictionary representation of the retention policy configuration.
        """
        if self.policy_type == RetentionPolicyType.COUNT_BASED:
            return {"max_backups": self._count_based_policy or 10}
        return {
            backup_type.value: retention or DEFAULT_RETENTION
            for backup_type, retention in self._time_based_policy.items()
        }
