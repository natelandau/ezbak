"""Test the ezbak CLI."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import cappa
import pytest
import time_machine

from ezbak.backup import Backup
from ezbak.cli import CreateCommand, EZBakCLI, RestoreCommand, build_config
from ezbak.cli_commands import list as list_cmd
from ezbak.constants import DEFAULT_DATE_FORMAT, LogLevel, StorageType
from ezbak.logging import instantiate_logger

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


def test_cli_prune_backups_dry_run(mocker, debug, capsys, tmp_path):
    """Verify a dry-run prune previews targets without prompting or deleting."""
    # Given: a confirmation spy that must never be reached in dry-run mode
    confirm = mocker.patch("ezbak.cli_commands.prune.Confirm.ask", return_value=True)

    # Given: 13 backup files on disk
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

    # When: pruning runs with --dry-run
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
            "--dry-run",
        ],
    )
    output = capsys.readouterr().err

    # Then: no confirmation is requested, targets are previewed, and all files remain
    confirm.assert_not_called()
    assert "Would delete 10 backups" in output
    assert "Pruned" not in output
    assert len(list(tmp_path.iterdir())) == 13


def test_cli_prune_backups_dry_run_no_targets(mocker, debug, capsys, tmp_path):
    """Verify a dry run that targets nothing never claims a deletion happened."""
    # Given: a confirmation spy that must never be reached in dry-run mode
    confirm = mocker.patch("ezbak.cli_commands.prune.Confirm.ask", return_value=True)

    # Given: fewer backups than the retention policy keeps, so nothing would be pruned
    for filename in [
        "test-20250609T101857-hourly.tgz",
        "test-20250609T095745-minutely.tgz",
    ]:
        Path(tmp_path / filename).touch()

    # When: pruning runs with --dry-run and a policy that keeps more than exist
    cappa.invoke(
        obj=EZBakCLI,
        argv=[
            "prune",
            "--name",
            "test",
            "--storage",
            str(tmp_path),
            "--max-backups",
            "100",
            "--dry-run",
        ],
    )
    output = capsys.readouterr().err

    # Then: no prompt fires and the output never uses the past-tense "No backups deleted"
    confirm.assert_not_called()
    assert "No backups would be deleted" in output
    assert "No backups deleted" not in output


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
            "--restore-path",
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


def test_s3_only_cli_parses_without_storage(monkeypatch, tmp_path):
    """Verify an S3-only backup parses without the --storage flag."""
    # Given S3 credentials in the environment
    monkeypatch.setenv("EZBAK_AWS_ACCESS_KEY", "AKIA_TEST")
    monkeypatch.setenv("EZBAK_AWS_SECRET_KEY", "secret_test")

    # When parsing a create command with only --s3-bucket (no --storage)
    cli = cappa.parse(
        EZBakCLI,
        argv=["--name", "t", "--s3-bucket", "my-bucket", "create", "--source", str(tmp_path)],
    )
    config = build_config(cli)

    # Then parsing succeeds and the config has no local paths but the S3 bucket
    assert config.storage_paths == []
    assert config.aws_s3_bucket_name == "my-bucket"


def test_cli_invalid_config_exits_cleanly(monkeypatch, capsys):
    """Verify a config with no storage location exits non-zero with a logged message."""
    # Given a logger bound to this test's stderr and no storage configured
    instantiate_logger(LogLevel.INFO)
    for var in ("EZBAK_STORAGE_PATHS", "EZBAK_AWS_S3_BUCKET_NAME"):
        monkeypatch.delenv(var, raising=False)

    # When invoking a command with a name but no storage destination
    with pytest.raises(cappa.Exit) as exc:
        cappa.invoke(obj=EZBakCLI, argv=["list", "--name", "test"])

    # Then it exits non-zero with a helpful message instead of a raw pydantic traceback
    assert exc.value.code == 1
    assert "No storage configured" in capsys.readouterr().err


def test_build_config_maps_restore_date():
    """Verify --restore-date is mapped onto restore_date in the built config."""
    # Given a restore command carrying a date
    cli = EZBakCLI(
        command=RestoreCommand(restore_path=Path("/tmp/restore"), restore_date="20250102"),  # noqa: S108
        name="test",
        storage_paths=[Path("/tmp")],  # noqa: S108
    )

    # When building the config
    config = build_config(cli)

    # Then restore_date is set
    assert config.restore_date == "20250102"


def test_cli_restore_backup_by_date(filesystem, debug, capsys, tmp_path):
    """Verify restore --restore-date restores the point-in-time backup."""
    # Given two backups on different days in one storage location
    _, dest1, _ = filesystem
    for ts in ("20250101T120000", "20250103T090000"):
        shutil.copy2(fixture_archive_path, dest1 / f"test-{ts}.tgz")
    restore_path = Path(tmp_path / "restore")
    restore_path.mkdir(exist_ok=True)

    # When restoring as of 2025-01-02 (only the Jan 1 backup qualifies).
    # -v raises the log level to DEBUG so the selection line is emitted.
    cappa.invoke(
        obj=EZBakCLI,
        argv=[
            "restore",
            "-v",
            "--name",
            "test",
            "--storage",
            str(dest1),
            "--restore-path",
            str(restore_path),
            "--restore-date",
            "20250102",
        ],
    )
    output = capsys.readouterr().err

    # Then a restore happens and the point-in-time backup was selected
    assert "Backup restored to 'restore'" in output
    assert "Selected backup as of 20250102: test-20250101T120000.tgz" in output


def test_cli_restore_by_date_no_match_exits_nonzero(filesystem, tmp_path):
    """Verify restore --restore-date exits non-zero when nothing qualifies."""
    # Given a single 2025 backup
    _, dest1, _ = filesystem
    shutil.copy2(fixture_archive_path, dest1 / "test-20250103T090000.tgz")
    restore_path = Path(tmp_path / "restore")
    restore_path.mkdir(exist_ok=True)

    # When restoring as of a year before it
    # Then the CLI exits non-zero
    with pytest.raises(cappa.Exit) as exc:
        cappa.invoke(
            obj=EZBakCLI,
            argv=[
                "restore",
                "--name",
                "test",
                "--storage",
                str(dest1),
                "--restore-path",
                str(restore_path),
                "--restore-date",
                "2024",
            ],
        )
    assert exc.value.code == 1
