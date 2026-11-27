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


@pytest.mark.parametrize(
    ("manager", "expected"),
    [
        pytest.param(RetentionPolicyManager(), False, id="no-rules"),
        pytest.param(RetentionPolicyManager(keep_last=0), True, id="zero-rule"),
    ],
)
def test_is_active_when_any_rule_is_set(manager: RetentionPolicyManager, *, expected: bool) -> None:
    """Verify a policy is active once any rule is set, even to zero."""
    assert manager.is_active is expected


@pytest.mark.parametrize(
    ("keep_last", "expected_kept"),
    [
        pytest.param(2, {"20250103T090000", "20250104T090000"}, id="fewer-than-backups"),
        pytest.param(
            10,
            {"20250101T090000", "20250102T090000", "20250103T090000", "20250104T090000"},
            id="more-than-backups",
        ),
    ],
)
def test_keep_last_keeps_the_newest_backups(
    tmp_path: Path, keep_last: int, expected_kept: set[str]
) -> None:
    """Verify keep_last keeps the N newest backups, or every backup when N exceeds the count."""
    # Given four backups on four days
    backups = [_backup(tmp_path, f"2025010{d}T090000") for d in (1, 2, 3, 4)]
    mgr = RetentionPolicyManager(keep_last=keep_last)

    # When computing the keep-set
    keep = mgr.backups_to_keep(backups)

    # Then only the newest N survive
    assert {b.timestamp for b in keep} == expected_kept


@pytest.mark.parametrize(
    "manager",
    [
        pytest.param(RetentionPolicyManager(keep_last=0), id="keep-last-zero"),
        pytest.param(
            RetentionPolicyManager(keep_last=0, calendar={BackupType.DAILY: 0}), id="all-zero"
        ),
    ],
)
def test_zero_rules_keep_nothing(tmp_path: Path, manager: RetentionPolicyManager) -> None:
    """Verify an active policy whose rules are all zero keeps no backups."""
    # Given backups on two days
    backups = [_backup(tmp_path, f"2025010{d}T090000") for d in (1, 2)]

    # When computing the keep-set
    # Then nothing survives
    assert manager.backups_to_keep(backups) == set()


def test_zero_calendar_rule_beside_a_set_rule_is_ignored(tmp_path: Path) -> None:
    """Verify a calendar rule set to zero neither keeps nor blocks backups when another rule is set."""
    # Given backups on three days and a policy with daily turned off but yearly set
    backups = [_backup(tmp_path, f"2025010{d}T090000") for d in (1, 2, 3)]
    mgr = RetentionPolicyManager(calendar={BackupType.DAILY: 0, BackupType.YEARLY: 1})

    # When computing the keep-set
    keep = mgr.backups_to_keep(backups)

    # Then only the yearly rule applies, keeping the newest backup of the year
    assert {b.timestamp for b in keep} == {"20250103T090000"}


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


@pytest.mark.parametrize(
    ("keep_daily", "expected_kept"),
    [
        (3, {"20250110T090000", "20250215T090000", "20250620T090000"}),
        (10, {"20250101T090000", "20250110T090000", "20250215T090000", "20250620T090000"}),
    ],
    ids=["gaps-do-not-use-slots", "more-slots-than-days"],
)
def test_calendar_rule_counts_only_periods_with_backups(tmp_path, keep_daily, expected_kept):
    """Verify keep_daily counts days that have a backup, not calendar days, so gaps do not consume slots."""
    # Given backups on four widely separated days
    stamps = ("20250101T090000", "20250110T090000", "20250215T090000", "20250620T090000")
    backups = [_backup(tmp_path, s) for s in stamps]
    mgr = RetentionPolicyManager(calendar={BackupType.DAILY: keep_daily})

    # When keeping that many daily periods
    keep = mgr.backups_to_keep(backups)

    # Then the newest days-with-a-backup survive, up to the count, regardless of calendar gaps
    assert {b.timestamp for b in keep} == expected_kept


def test_summary_lists_set_rules_only():
    """Verify summary reports only rules that were set."""
    mgr = RetentionPolicyManager(
        keep_last=5, calendar={BackupType.DAILY: 7, BackupType.YEARLY: None}
    )
    assert mgr.summary() == {"keep_last": 5, "daily": 7}


@pytest.mark.parametrize(
    ("older_stamp", "newer_stamp", "differing_keys"),
    [
        ("20250609T090000", "20260609T090000", ("year", "month", "week", "day", "hour", "minute")),
        ("20250508T090000", "20250608T090000", ("month", "day", "hour", "minute")),
    ],
    ids=["same-month-different-year", "same-day-different-month"],
)
def test_period_keys_include_the_enclosing_periods(
    tmp_path, older_stamp, newer_stamp, differing_keys
):
    """Verify a period key includes its enclosing periods, so June 2025 and June 2026 never merge."""
    # Given two backups whose shorter periods match but whose enclosing periods differ
    older = _backup(tmp_path, older_stamp)
    newer = _backup(tmp_path, newer_stamp)

    # When comparing their period keys
    # Then every key that spans the differing period is distinct
    for key in differing_keys:
        assert getattr(older, key) != getattr(newer, key), key
