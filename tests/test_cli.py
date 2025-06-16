"""Test the ezbak CLI."""

from __future__ import annotations

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
def test_create_backup(filesystem, debug, clean_stderr, tmp_path):
    """Verify that a backup is created correctly."""
    # Given: Source and destination directories from fixture
    src_dir, dest1, dest2 = filesystem

    cappa.invoke(
        obj=EZBakCLI,
        argv=[
            "create",
            "--name",
            "test",
            "--sources",
            str(src_dir),
            "--destinations",
            str(dest1),
            "--destinations",
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
def test_create_backup_no_labels(filesystem, debug, clean_stderr, tmp_path):
    """Verify that backups are pruned correctly."""
    # Given: Source and destination directories from fixture
    src_dir, dest1, dest2 = filesystem

    cappa.invoke(
        obj=EZBakCLI,
        argv=[
            "create",
            "-n",
            "test",
            "-d",
            str(dest1),
            "-d",
            str(dest2),
            "-s",
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


def test_prune_backups_max_backups(mocker, debug, clean_stderr, tmp_path):
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
            "-d",
            str(tmp_path),
            "-x",
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


def test_prune_backups_with_policy(mocker, debug, clean_stderr, tmp_path):
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
            "-d",
            str(tmp_path),
            "-Y",
            "1",
            "-M",
            "4",
            "-W",
            "4",
            "-D",
            "4",
            "-H",
            "4",
            "-S",
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


def test_list_backups(debug, clean_stdout, tmp_path):
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
        argv=["list", "--name", "test", "-l", str(tmp_path)],
        deps=[initialize_ezbak],
    )
    output = clean_stdout()
    debug(output)

    assert "test-20250609T101857-hourly.tgz" in output
    assert "test-20250609T095745-minutely.tgz" in output
    assert "test-20250609T095804-minutely.tgz" in output
