"""Retention policy manager for the union-based backup lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ezbak.constants import BackupType

if TYPE_CHECKING:
    from ezbak.backup import Backup

# Backup attribute holding each period type's globally-unique key.
_PERIOD_ATTR: dict[BackupType, str] = {
    BackupType.YEARLY: "year",
    BackupType.MONTHLY: "month",
    BackupType.WEEKLY: "week",
    BackupType.DAILY: "day",
    BackupType.HOURLY: "hour",
    BackupType.MINUTELY: "minute",
}


class RetentionPolicyManager:
    """Compute which backups to keep under a union of independent keep rules.

    A backup survives if any rule marks it. ``keep_last`` marks the N most recent
    backups overall; each calendar rule marks the newest backup in each of the N
    most recent periods that actually contain a backup. Unset (``None``) or ``0``
    means a rule marks nothing. This mirrors restic/borg semantics.
    """

    def __init__(
        self,
        *,
        keep_last: int | None = None,
        calendar: dict[BackupType, int | None] | None = None,
    ) -> None:
        """Initialize the manager with a count rule and per-period calendar rules.

        Args:
            keep_last: Number of most-recent backups to keep, or None to disable.
            calendar: Per-period keep counts keyed by BackupType. Defaults to empty.
        """
        self.keep_last = keep_last
        self._calendar: dict[BackupType, int | None] = calendar or {}

    @property
    def is_active(self) -> bool:
        """Whether any rule is set, including an explicit zero.

        An all-unset policy keeps everything; an all-zero policy is active but
        keeps nothing, which the prune path treats as a refuse-loudly condition.
        """
        return self.keep_last is not None or any(v is not None for v in self._calendar.values())

    def backups_to_keep(self, backups: list[Backup]) -> set[Backup]:
        """Return the set of backups any rule marks to keep.

        Args:
            backups: All backups in one storage location.
        """
        ordered = sorted(backups, key=lambda b: (b.timestamp, b.name), reverse=True)
        keep: set[Backup] = set()

        if self.keep_last:
            last = ordered[: self.keep_last]
            keep.update(last)
            logger.trace(f"keep_last={self.keep_last} marked: {[b.name for b in last]}")

        for backup_type, count in self._calendar.items():
            if not count:
                continue
            attr = _PERIOD_ATTR[backup_type]
            seen: set[str] = set()
            for backup in ordered:
                key = getattr(backup, attr)
                if key in seen:
                    continue
                seen.add(key)
                keep.add(backup)
                logger.trace(
                    f"{backup_type.value}={count} marked '{backup.name}' as newest for {attr} {key}"
                )
                if len(seen) >= count:
                    break

        return keep

    def summary(self) -> dict[str, int]:
        """Return the set rules as a display dict, omitting unset ones."""
        out: dict[str, int] = {}
        if self.keep_last is not None:
            out["keep_last"] = self.keep_last
        for backup_type, count in self._calendar.items():
            if count is not None:
                out[backup_type.value] = count
        return out
