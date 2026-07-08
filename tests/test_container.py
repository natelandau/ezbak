"""Test the ezbak CLI."""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import time_machine

from ezbak import ezbak
from ezbak.constants import DEFAULT_COMPRESSION_LEVEL, DEFAULT_DATE_FORMAT
from ezbak.container import do_backup
from ezbak.container import main as entrypoint
from ezbak.exceptions import BackupFailedError

UTC = ZoneInfo("UTC")
frozen_time = datetime(2025, 6, 9, 0, 0, tzinfo=UTC)
frozen_time_str = frozen_time.strftime(DEFAULT_DATE_FORMAT)
fixture_archive_path = Path(__file__).parent / "fixtures" / "archive.tgz"


@pytest.fixture(autouse=True)
def mock_run(mocker):
    """Mock the Run class to prevent infinite loop in scheduler."""
    # Mock the Run class to prevent infinite loop in scheduler
    mock_scheduler = mocker.patch("ezbak.container.BackgroundScheduler")
    mock_scheduler_instance = mock_scheduler.return_value
    mock_scheduler_instance.running = False
    mocker.patch("time.sleep", return_value=None)


@pytest.fixture(autouse=True)
def mock_os_environ(mocker):
    """Override items from .env file."""
    os.environ["EZBAK_AWS_ACCESS_KEY"] = ""
    os.environ["EZBAK_AWS_S3_BUCKET_NAME"] = ""
    os.environ["EZBAK_AWS_SECRET_KEY"] = ""
    os.environ["EZBAK_COMPRESSION_LEVEL"] = str(DEFAULT_COMPRESSION_LEVEL)
    os.environ["EZBAK_CRON"] = ""
    os.environ["EZBAK_EXCLUDE_REGEX"] = ""
    os.environ["EZBAK_INCLUDE_REGEX"] = ""
    os.environ["EZBAK_LOG_FILE"] = ""
    os.environ["EZBAK_LOG_LEVEL"] = ""
    os.environ["EZBAK_LOG_PREFIX"] = ""
    os.environ["EZBAK_TZ"] = "Etc/UTC"


@time_machine.travel(frozen_time, tick=False)
def test_entrypoint_create_backup(filesystem, debug, capsys):
    """Verify that a backup is created correctly."""
    # Given: Source and destination directories from fixture
    src_dir, dest1, dest2 = filesystem

    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1) + "," + str(dest2)
    os.environ["EZBAK_LOG_LEVEL"] = "TRACE"

    entrypoint()

    output = capsys.readouterr().err
    # debug(output)

    filename = f"test-{frozen_time_str}.tgz"
    assert Path(dest1 / filename).exists()
    assert Path(dest2 / filename).exists()
    assert f"INFO     | Created: dest1/{filename}" in output
    assert f"INFO     | Created: dest2/{filename}" in output


@time_machine.travel(frozen_time, tick=True)
def test_entrypoint_create_backup_with_cron(mocker, monkeypatch, filesystem, debug, capsys):
    """Verify that a backup is created correctly."""
    # Given: Source and destination directories from fixture
    src_dir, dest1, dest2 = filesystem

    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1) + "," + str(dest2)
    os.environ["EZBAK_CRON"] = "*/1 * * * *"
    os.environ["EZBAK_LOG_LEVEL"] = "TRACE"

    entrypoint()

    output = capsys.readouterr().err
    # debug(output)
    assert "Scheduler started" in output
    assert "Next scheduled run" in output


def test_entrypoint_restore_backup(filesystem, debug, capsys, tmp_path):
    """Verify that a backup is restored correctly."""
    # Given: Source and destination directories from fixture
    src_dir, dest1, _ = filesystem
    backup_name = f"test-{frozen_time_str}-yearly.tgz"
    backup_path = Path(dest1 / backup_name)
    shutil.copy2(fixture_archive_path, backup_path)

    restore_path = Path(tmp_path / "restore")
    restore_path.mkdir(exist_ok=True)

    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_ACTION"] = "restore"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_RESTORE_PATH"] = str(restore_path)
    os.environ["EZBAK_LOG_LEVEL"] = "TRACE"

    entrypoint()

    output = capsys.readouterr().err
    # debug(output)
    debug(restore_path)

    assert "INFO     | Backup restored to 'restore'" in output
    assert Path(restore_path / "src" / "baz.txt").exists()


def test_entrypoint_backup_fails_when_destination_unusable(filesystem, capsys):
    """Verify the container exits non-zero and does not report success when a destination is unusable."""
    # Given an S3-only config with missing credentials
    src_dir, _, _ = filesystem
    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = ""
    os.environ["EZBAK_AWS_S3_BUCKET_NAME"] = "test-bucket"
    os.environ["EZBAK_AWS_ACCESS_KEY"] = ""
    os.environ["EZBAK_AWS_SECRET_KEY"] = ""
    os.environ["EZBAK_LOG_LEVEL"] = "TRACE"

    # When running the entrypoint, then it exits non-zero
    with pytest.raises(SystemExit) as exc_info:
        entrypoint()
    assert exc_info.value.code == 1

    # Then it does not falsely report completion
    output = capsys.readouterr().err
    assert "Backup complete" not in output


def test_entrypoint_backup_fails_when_archive_creation_fails(filesystem, capsys, mocker):
    """Verify the container exits non-zero when the archive cannot be built."""
    # Given a valid local backup config
    src_dir, dest1, _ = filesystem
    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_LOG_LEVEL"] = "TRACE"

    # Given archive creation fails
    mocker.patch("ezbak.core.EZBak._create_tmp_backup_file", return_value=None)

    # When running the entrypoint, then it exits non-zero without a false success
    with pytest.raises(SystemExit) as exc_info:
        entrypoint()
    assert exc_info.value.code == 1
    output = capsys.readouterr().err
    assert "Backup complete" not in output


def test_do_backup_prunes_even_when_backup_fails(filesystem, mocker):
    """Verify retention still runs when a destination fails so backups don't accumulate."""
    # Given an app whose backup raises for a failed destination
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    mocker.patch.object(app, "create_backup", side_effect=BackupFailedError(["dest1"]))
    prune_spy = mocker.patch.object(app, "prune_backups")

    # When running do_backup, then it re-raises the failure
    with pytest.raises(BackupFailedError):
        do_backup(app)

    # Then pruning still ran despite the failed backup
    prune_spy.assert_called_once()
