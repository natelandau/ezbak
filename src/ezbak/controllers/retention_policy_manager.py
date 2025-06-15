"""Retention policy manager."""

from nclutils import logger

from ezbak.constants import DEFAULT_RETENTION, BackupType, RetentionPolicyType


class RetentionPolicyManager:
    """Retention policy manager."""

    def __init__(
        self,
        policy_type: RetentionPolicyType,
        time_based_policy: dict[BackupType, int] | None = None,
        count_based_policy: int | None = None,
    ):
        self.policy_type = policy_type
        self._time_based_policy = time_based_policy or {}
        self._count_based_policy = count_based_policy

    def get_retention(self, backup_type: BackupType) -> int:
        """Get the retention for a backup type.

        Args:
            backup_type (BackupType): The backup type to get the retention for.

        Returns:
            int: The retention for the backup type.
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
        """Get the full policy.

        Returns:
            dict[str, int]: The full policy.
        """
        if self.policy_type == RetentionPolicyType.COUNT_BASED:
            return {"max_backups": self._count_based_policy or 10}
        return {
            backup_type.value: retention or DEFAULT_RETENTION
            for backup_type, retention in self._time_based_policy.items()
        }
