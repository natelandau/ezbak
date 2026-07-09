"""Unit tests for the union retention policy manager."""

from pathlib import Path

from ezbak.backup import Backup
from ezbak.constants import BackupType, StorageType
from ezbak.retention import RetentionPolicyManager


def _backup(tmp_path: Path, stamp: str) -> Backup:
    """Build a Backup from a synthetic filename with the given timestamp.

    Returns:
        Backup: The constructed backup.
    """
    p = tmp_path / f"test-{stamp}-minutely.tgz"
    p.touch()
    return Backup(path=p, name=p.name, storage_type=StorageType.LOCAL)


def test_is_active_false_when_all_unset():
    """Verify a manager with no rules is inactive."""
    assert RetentionPolicyManager().is_active is False


def test_is_active_true_when_a_rule_is_zero():
    """Verify an explicit zero rule still marks the policy active."""
    assert RetentionPolicyManager(keep_last=0).is_active is True


def test_keep_last_marks_n_newest(tmp_path):
    """Verify keep_last keeps exactly the N most recent backups."""
    # Given five backups across five minutes
    backups = [_backup(tmp_path, f"20250101T0900{i:02d}") for i in range(5)]
    mgr = RetentionPolicyManager(keep_last=2)

    # When computing the keep-set
    keep = mgr.backups_to_keep(backups)

    # Then only the two newest survive
    newest_two = sorted(backups, key=lambda b: b.timestamp, reverse=True)[:2]
    assert keep == set(newest_two)


def test_unset_period_marks_nothing(tmp_path):
    """Verify an unset calendar rule keeps zero of that period."""
    # Given daily backups over three days, only keep_daily set
    backups = [_backup(tmp_path, f"2025010{d}T090000") for d in (1, 2, 3)]
    mgr = RetentionPolicyManager(calendar={BackupType.DAILY: 1})

    # When computing the keep-set
    keep = mgr.backups_to_keep(backups)

    # Then exactly one daily representative survives (the newest day)
    assert len(keep) == 1
    assert next(iter(keep)).timestamp.startswith("20250103")


def test_zero_rule_marks_nothing(tmp_path):
    """Verify a rule set to zero keeps nothing for that rule."""
    backups = [_backup(tmp_path, f"2025010{d}T090000") for d in (1, 2)]
    mgr = RetentionPolicyManager(keep_last=0)
    assert mgr.backups_to_keep(backups) == set()


def test_union_overlap(tmp_path):
    """Verify keep_last and keep_daily union without double-counting overlap."""
    # Given hourly backups on day 1 plus one per prior day (3 days total)
    day1 = [_backup(tmp_path, f"20250103T09{m:02d}00") for m in range(3)]
    day2 = [_backup(tmp_path, "20250102T090000")]
    day3 = [_backup(tmp_path, "20250101T090000")]
    backups = day1 + day2 + day3
    mgr = RetentionPolicyManager(keep_last=3, calendar={BackupType.DAILY: 3})

    # When computing the keep-set
    keep = mgr.backups_to_keep(backups)

    # Then the day-1 daily pick is already in the recent 3, so union is 5 not 6
    assert len(keep) == 5


def test_all_zero_policy_keeps_nothing(tmp_path):
    """Verify an all-zero active policy produces an empty keep-set."""
    backups = [_backup(tmp_path, "20250101T090000")]
    mgr = RetentionPolicyManager(keep_last=0, calendar={BackupType.DAILY: 0})
    assert mgr.is_active is True
    assert mgr.backups_to_keep(backups) == set()


def test_backups_to_delete_is_complement_of_keep(tmp_path):
    """Verify backups_to_delete returns exactly the non-kept backups."""
    # Given five backups across five minutes and a keep_last=2 policy
    backups = [_backup(tmp_path, f"20250101T0900{i:02d}") for i in range(5)]
    mgr = RetentionPolicyManager(keep_last=2)

    # When computing the keep-set and the delete-list
    keep = mgr.backups_to_keep(backups)
    to_delete = mgr.backups_to_delete(backups)

    # Then delete returns the 3 non-kept backups, in input order
    expected = [b for b in backups if b not in keep]
    assert to_delete == expected
    assert len(to_delete) == 3

    # And the two sets never overlap
    assert set(to_delete).isdisjoint(keep)


def test_summary_lists_set_rules_only():
    """Verify summary reports only rules that were set."""
    mgr = RetentionPolicyManager(
        keep_last=5, calendar={BackupType.DAILY: 7, BackupType.YEARLY: None}
    )
    assert mgr.summary() == {"keep_last": 5, "daily": 7}
