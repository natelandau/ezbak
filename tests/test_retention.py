"""Unit tests for the union retention policy manager."""

from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    ("backup_type", "stamps", "expected_kept"),
    [
        (
            BackupType.YEARLY,
            ("20230101T090000", "20240101T090000", "20250101T090000"),
            {"20240101T090000", "20250101T090000"},
        ),
        (
            BackupType.MONTHLY,
            ("20250101T090000", "20250201T090000", "20250301T090000"),
            {"20250201T090000", "20250301T090000"},
        ),
        (
            BackupType.WEEKLY,
            ("20250106T090000", "20250113T090000", "20250120T090000"),
            {"20250113T090000", "20250120T090000"},
        ),
        (
            BackupType.DAILY,
            ("20250101T090000", "20250102T090000", "20250103T090000"),
            {"20250102T090000", "20250103T090000"},
        ),
        (
            BackupType.HOURLY,
            ("20250101T000000", "20250101T010000", "20250101T020000"),
            {"20250101T010000", "20250101T020000"},
        ),
        (
            BackupType.MINUTELY,
            ("20250101T000000", "20250101T000100", "20250101T000200"),
            {"20250101T000100", "20250101T000200"},
        ),
    ],
    ids=["yearly", "monthly", "weekly", "daily", "hourly", "minutely"],
)
def test_calendar_rule_keeps_newest_two_periods(tmp_path, backup_type, stamps, expected_kept):
    """Verify each calendar period type keeps the newest backup of its N most-recent periods."""
    # Given three backups in three distinct periods of the given type
    backups = [_backup(tmp_path, s) for s in stamps]
    mgr = RetentionPolicyManager(calendar={backup_type: 2})

    # When keeping the two most-recent periods
    keep = mgr.backups_to_keep(backups)

    # Then the two newest periods' representatives survive
    assert {b.timestamp for b in keep} == expected_kept


def test_weekly_key_groups_iso_week_across_year_boundary(tmp_path):
    """Verify weekly grouping uses the ISO week, so a week spanning a year boundary is one bucket."""
    # Given two backups in the same ISO week but different calendar years
    # (2025-12-29 Mon and 2026-01-01 Thu are both in ISO week 2026-W01)
    backups = [_backup(tmp_path, "20251229T090000"), _backup(tmp_path, "20260101T090000")]
    mgr = RetentionPolicyManager(calendar={BackupType.WEEKLY: 1})

    # When keeping one weekly representative
    keep = mgr.backups_to_keep(backups)

    # Then they count as one week and only the newest survives; the old %W key split them in two
    assert {b.timestamp for b in keep} == {"20260101T090000"}


def test_calendar_rule_keeps_one_representative_per_period(tmp_path):
    """Verify a calendar rule keeps one backup per period, not every backup in the period."""
    # Given two days that each hold multiple backups
    day1 = [_backup(tmp_path, s) for s in ("20250101T090000", "20250101T100000", "20250101T110000")]
    day2 = [_backup(tmp_path, s) for s in ("20250102T090000", "20250102T100000")]
    mgr = RetentionPolicyManager(calendar={BackupType.DAILY: 2})

    # When keeping two daily periods
    keep = mgr.backups_to_keep(day1 + day2)

    # Then only the newest representative of each day survives, not all five backups
    assert {b.timestamp for b in keep} == {"20250101T110000", "20250102T100000"}


def test_calendar_rule_counts_only_periods_with_backups(tmp_path):
    """Verify keep_daily counts days that have a backup, not calendar days, so gaps do not consume slots."""
    # Given backups on four widely separated days
    stamps = ("20250101T090000", "20250110T090000", "20250215T090000", "20250620T090000")
    backups = [_backup(tmp_path, s) for s in stamps]
    mgr = RetentionPolicyManager(calendar={BackupType.DAILY: 3})

    # When keeping three daily periods
    keep = mgr.backups_to_keep(backups)

    # Then the three most-recent days-with-a-backup survive regardless of the calendar gaps
    assert {b.timestamp for b in keep} == {"20250110T090000", "20250215T090000", "20250620T090000"}


def test_keep_last_exceeding_backup_count_keeps_all(tmp_path):
    """Verify keep_last larger than the backup count keeps every backup without over-reaching."""
    # Given three backups and keep_last greater than three
    backups = [_backup(tmp_path, f"2025010{d}T090000") for d in (1, 2, 3)]
    mgr = RetentionPolicyManager(keep_last=10)

    # When computing the keep-set
    keep = mgr.backups_to_keep(backups)

    # Then all three survive
    assert len(keep) == 3


def test_summary_lists_set_rules_only():
    """Verify summary reports only rules that were set."""
    mgr = RetentionPolicyManager(
        keep_last=5, calendar={BackupType.DAILY: 7, BackupType.YEARLY: None}
    )
    assert mgr.summary() == {"keep_last": 5, "daily": 7}
