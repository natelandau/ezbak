"""Test the ezbak CLI."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import cappa
import time_machine

from ezbak.cli import EZBakCLI, initialize_ezbak
from ezbak.constants import DEFAULT_DATE_FORMAT

UTC = ZoneInfo("UTC")
frozen_time = datetime(2025, 6, 9, tzinfo=UTC)
frozen_time_str = frozen_time.strftime(DEFAULT_DATE_FORMAT)
fixture_archive_path = Path(__file__).parent / "fixtures" / "archive.tgz"


@time_machine.travel(frozen_time, tick=False)
def test_cli_create_backup(filesystem, debug, clean_stderr, tmp_path):
    """Verify that a backup is created correctly."""
    # Given: Source and destination directories from fixture
    src_dir, dest1, dest2 = filesystem

    cappa.invoke(
        obj=EZBakCLI,
        argv=[
            "create",
            "--name",
            "test",
            "--source",
            str(src_dir),
            "--storage",
            str(dest1),
            "--storage",
            str(dest2),
        ],
        deps=[initialize_ezbak],
    )
    output = clean_stderr()
    # debug(output)

    filename = f"test-{frozen_time_str}-yearly.tgz"
    assert Path(dest1 / filename).exists()
    assert Path(dest2 / filename).exists()
    assert f"INFO     | Created: …/dest1/{filename}" in output
    assert f"INFO     | Created: …/dest2/{filename}" in output


@time_machine.travel(frozen_time, tick=False)
def test_cli_create_backup_no_labels(filesystem, debug, clean_stderr, tmp_path):
    """Verify that backups are pruned correctly."""
    # Given: Source and destination directories from fixture
    src_dir, dest1, dest2 = filesystem

    cappa.invoke(
        obj=EZBakCLI,
        argv=[
            "create",
            "-n",
            "test",
            "--storage",
            str(dest1),
            "--storage",
            str(dest2),
            "--source",
            str(src_dir),
            "--no-label",
        ],
        deps=[initialize_ezbak],
    )

    output = clean_stderr()
    # debug(output)

    filename = f"test-{frozen_time_str}.tgz"
    assert Path(dest1 / filename).exists()
    assert Path(dest2 / filename).exists()
    assert f"INFO     | Created: …/dest1/{filename}" in output
    assert f"INFO     | Created: …/dest2/{filename}" in output


def test_cli_prune_backups_max_backups(mocker, debug, clean_stderr, tmp_path):
    """Verify pruning backups with max backup set."""
    mocker.patch("ezbak.cli_commands.prune.Confirm.ask", return_value=True)
    # Given: A backup manager configured with test parameters
    filenames = [
        "test-20250609T101857-hourly.tgz",
        "test-20250609T095745-minutely.tgz",
        "test-20250609T095804-minutely.tgz",
        "test-20250609T095730-weekly-k6lop.tgz",
        "test-20250609T095730-daily.tgz",
        "test-20250609T095751-minutely.tgz",
        "test-20250609T095749-minutely.tgz",
        "test-20250609T090932-yearly.tgz",
        "test-20250609T095737-minutely.tgz",
        "test-20250609T095804-minutely-p2we3r.tgz",
        "test-20240609T090932-yearly.tgz",
        "test-20250609T095625-monthly.tgz",
        "test-20250609T095737-minutely-6klf7.tgz",
    ]
    for filename in filenames:
        Path(tmp_path / filename).touch()

    cappa.invoke(
        obj=EZBakCLI,
        argv=[
            "prune",
            "--name",
            "test",
            "--storage",
            str(tmp_path),
            "--max-backups",
            "3",
        ],
        deps=[initialize_ezbak],
    )
    output = clean_stderr()
    # debug(output)

    assert "Deleted 10 old backups" in output
    existing_files = list(tmp_path.iterdir())
    assert len(existing_files) == 3
    for filename in [
        "test-20250609T101857-hourly.tgz",
        "test-20250609T095804-minutely.tgz",
        "test-20250609T095804-minutely-p2we3r.tgz",
    ]:
        assert Path(tmp_path / filename).exists()


def test_cli_prune_backups_with_policy(mocker, debug, clean_stderr, tmp_path):
    """Verify pruning backups with a policy."""
    mocker.patch("ezbak.cli_commands.prune.Confirm.ask", return_value=True)
    # Given: A backup manager configured with test parameters
    filenames = [
        "test-20250609T101857-hourly.tgz",
        "test-20250609T095745-minutely.tgz",
        "test-20250609T095804-minutely.tgz",
        "test-20250609T095730-weekly-k6lop.tgz",
        "test-20250609T095730-daily.tgz",
        "test-20250609T095751-minutely.tgz",
        "test-20250609T095749-minutely.tgz",
        "test-20250609T090932-yearly.tgz",
        "test-20250609T095737-minutely.tgz",
        "test-20250609T095804-minutely-p2we3r.tgz",
        "test-20240609T090932-yearly.tgz",
        "test-20250609T095625-monthly.tgz",
        "test-20250609T095737-minutely-6klf7.tgz",
    ]
    for filename in filenames:
        Path(tmp_path / filename).touch()

    cappa.invoke(
        obj=EZBakCLI,
        argv=[
            "prune",
            "--name",
            "test",
            "--storage",
            str(tmp_path),
            "--yearly",
            "1",
            "--monthly",
            "4",
            "--weekly",
            "4",
            "--daily",
            "4",
            "--hourly",
            "4",
            "--minutely",
            "4",
        ],
        deps=[initialize_ezbak],
    )
    output = clean_stderr()
    # debug(output)

    assert "Deleted 3 old backups" in output
    existing_files = list(tmp_path.iterdir())
    assert len(existing_files) == 10
    for filename in [
        "test-20240609T090932-yearly.tgz",
        "test-20250609T095745-minutely.tgz",
        "test-20250609T095737-minutely.tgz",
    ]:
        assert not Path(tmp_path / filename).exists()


def test_cli_list_backups(debug, clean_stdout, tmp_path):
    """Verify listing backups."""
    # Given: A backup manager configured with test parameters
    filenames = [
        "test-20250609T101857-hourly.tgz",
        "test-20250609T095745-minutely.tgz",
        "test-20250609T095804-minutely.tgz",
    ]
    for filename in filenames:
        Path(tmp_path / filename).touch()

    cappa.invoke(
        obj=EZBakCLI,
        argv=["list", "--name", "test", "--storage", str(tmp_path)],
        deps=[initialize_ezbak],
    )
    output = clean_stdout()
    debug(output)

    assert "test-20250609T101857-hourly.tgz" in output
    assert "test-20250609T095745-minutely.tgz" in output
    assert "test-20250609T095804-minutely.tgz" in output


def test_cli_restore_backup(filesystem, debug, clean_stderr, tmp_path):
    """Verify that a backup is restored correctly."""
    # Given: Source and destination directories from fixture
    _, dest1, _ = filesystem
    backup_name = f"test-{frozen_time_str}-yearly.tgz"
    backup_path = Path(dest1 / backup_name)
    shutil.copy2(fixture_archive_path, backup_path)

    restore_path = Path(tmp_path / "restore")
    restore_path.mkdir(exist_ok=True)

    cappa.invoke(
        obj=EZBakCLI,
        argv=[
            "restore",
            "--name",
            "test",
            "--storage",
            str(dest1),
            "--destination",
            str(restore_path),
        ],
        deps=[initialize_ezbak],
    )
    output = clean_stderr()
    debug(output)
    # debug(restore_path)

    assert "INFO     | Restored backup to …/restore" in output
    assert Path(restore_path / "src" / "baz.txt").exists()
