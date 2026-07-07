"""Test the ezbak CLI."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import cappa
import time_machine

from ezbak.cli import CreateCommand, EZBakCLI, build_config
from ezbak.cli_commands import list as list_cmd
from ezbak.constants import DEFAULT_DATE_FORMAT, LogLevel, StorageType
from ezbak.models import Backup
from ezbak.utils.log_config import instantiate_logger

UTC = ZoneInfo("UTC")
frozen_time = datetime(2025, 6, 9, tzinfo=UTC)
frozen_time_str = frozen_time.strftime(DEFAULT_DATE_FORMAT)
fixture_archive_path = Path(__file__).parent / "fixtures" / "archive.tgz"


@time_machine.travel(frozen_time, tick=False)
def test_cli_create_backup(filesystem, debug, capsys, tmp_path):
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
    )
    output = capsys.readouterr().err
    # debug(output)

    filename = f"test-{frozen_time_str}-yearly.tgz"
    assert Path(dest1 / filename).exists()
    assert Path(dest2 / filename).exists()
    assert f"INFO     | Created: dest1/{filename}" in output
    assert f"INFO     | Created: dest2/{filename}" in output


@time_machine.travel(frozen_time, tick=False)
def test_cli_create_backup_no_labels(filesystem, debug, capsys, tmp_path):
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
    )

    output = capsys.readouterr().err
    # debug(output)

    filename = f"test-{frozen_time_str}.tgz"
    assert Path(dest1 / filename).exists()
    assert Path(dest2 / filename).exists()
    assert f"INFO     | Created: dest1/{filename}" in output
    assert f"INFO     | Created: dest2/{filename}" in output


def test_cli_prune_backups_max_backups(mocker, debug, capsys, tmp_path):
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
    )
    output = capsys.readouterr().err
    # debug(output)

    assert "Pruned 10 backups" in output
    existing_files = list(tmp_path.iterdir())
    assert len(existing_files) == 3
    for filename in [
        "test-20250609T101857-hourly.tgz",
        "test-20250609T095804-minutely.tgz",
        "test-20250609T095804-minutely-p2we3r.tgz",
    ]:
        assert Path(tmp_path / filename).exists()


def test_cli_prune_backups_with_policy(mocker, debug, capsys, tmp_path):
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
    )
    output = capsys.readouterr().err
    # debug(output)

    assert "Pruned 3 backups" in output
    existing_files = list(tmp_path.iterdir())
    assert len(existing_files) == 10
    for filename in [
        "test-20240609T090932-yearly.tgz",
        "test-20250609T095745-minutely.tgz",
        "test-20250609T095737-minutely.tgz",
    ]:
        assert not Path(tmp_path / filename).exists()


def test_cli_list_backups(debug, capsys, tmp_path):
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
    )
    output = capsys.readouterr().err
    debug(output)

    assert "test-20250609T101857-hourly.tgz" in output
    assert "test-20250609T095745-minutely.tgz" in output
    assert "test-20250609T095804-minutely.tgz" in output


def test_cli_list_backups_all_storage(mocker, debug, capsys, tmp_path):
    """Verify listing shows both local and S3 backups when both destinations have backups."""
    # Given a logger writing to stderr and an app reporting one local and one AWS backup
    instantiate_logger(LogLevel.INFO)

    local_backup = Backup(
        name="test-20250609T101857-hourly.tgz",
        storage_type=StorageType.LOCAL,
        path=tmp_path / "test-20250609T101857-hourly.tgz",
        storage_path=tmp_path,
    )
    aws_backup = Backup(name="test-20240609T000000-yearly.tgz", storage_type=StorageType.AWS)
    fake_app = SimpleNamespace(
        list_backups=lambda: [local_backup, aws_backup],
    )
    mocker.patch.object(list_cmd, "build_config", return_value=mocker.MagicMock())
    mocker.patch.object(list_cmd, "EZBak", return_value=fake_app)

    # When listing backups
    list_cmd.main(mocker.MagicMock())
    output = capsys.readouterr().err
    # debug(output)

    # Then both the AWS backup and the local backup appear
    assert "Found 1 AWS backups" in output
    assert aws_backup.name in output
    assert "Found 1 local backups" in output
    assert local_backup.name in output


def test_cli_restore_backup(filesystem, debug, capsys, tmp_path):
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
    )
    output = capsys.readouterr().err
    # debug(output)
    # debug(restore_path)

    assert "INFO     | Backup restored to 'restore'" in output
    assert Path(restore_path / "src" / "baz.txt").exists()


def test_build_config_reads_s3_bucket(monkeypatch, filesystem):
    """Verify the CLI builder wires an S3 bucket into the config."""
    # Given credentials in the environment and a bucket flag
    monkeypatch.setenv("EZBAK_AWS_ACCESS_KEY", "AKIA_TEST")
    monkeypatch.setenv("EZBAK_AWS_SECRET_KEY", "secret_test")
    src, dest1, _ = filesystem

    # When building a config for a create command targeting S3
    cli = EZBakCLI(
        command=CreateCommand(sources=[src]),
        name="t",
        storage_paths=[dest1],
        s3_bucket="my-bucket",
    )
    config = build_config(cli)

    # Then the bucket and env-sourced credentials are present
    assert config.aws_s3_bucket_name == "my-bucket"
    assert config.aws_access_key == "AKIA_TEST"
