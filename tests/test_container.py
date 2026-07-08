"""Test the ezbak CLI."""

from __future__ import annotations

import os
import shutil
import urllib.error
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import time_machine

from ezbak import ezbak
from ezbak.constants import DEFAULT_COMPRESSION_LEVEL, DEFAULT_DATE_FORMAT, LogLevel
from ezbak.container import _ping_healthcheck, _run_scheduled, do_backup
from ezbak.container import main as entrypoint
from ezbak.exceptions import BackupFailedError
from ezbak.logging import instantiate_logger

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
    os.environ["EZBAK_RESTORE_IF_EXISTS"] = "false"
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


def test_entrypoint_invalid_config_exits_cleanly(capsys):
    """Verify the container exits non-zero with a logged message when the config is invalid."""
    # Given a logger bound to this test's stderr and no backup name configured
    instantiate_logger(LogLevel.INFO)
    os.environ["EZBAK_NAME"] = ""
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_LOG_LEVEL"] = "TRACE"

    # When running the entrypoint, then it exits non-zero
    with pytest.raises(SystemExit) as exc_info:
        entrypoint()
    assert exc_info.value.code == 1

    # Then a clean validation message is logged instead of a raw pydantic traceback
    assert "No backup name provided" in capsys.readouterr().err


def test_ping_healthcheck_success_pings_base_url(mocker):
    """Verify a successful outcome pings the configured base URL unchanged."""
    # Given a mocked HTTP call
    mock_urlopen = mocker.patch("ezbak.container.urllib.request.urlopen", autospec=True)

    # When pinging for a successful run
    _ping_healthcheck("https://hc-ping.com/abc-123", failed=False)

    # Then the base URL is fetched
    assert mock_urlopen.call_args.args[0] == "https://hc-ping.com/abc-123"


def test_ping_healthcheck_failure_pings_fail_url(mocker):
    """Verify a failed outcome pings the base URL with the /fail suffix."""
    # Given a mocked HTTP call
    mock_urlopen = mocker.patch("ezbak.container.urllib.request.urlopen", autospec=True)

    # When pinging for a failed run
    _ping_healthcheck("https://hc-ping.com/abc-123", failed=True)

    # Then the /fail endpoint is fetched
    assert mock_urlopen.call_args.args[0] == "https://hc-ping.com/abc-123/fail"


def test_ping_healthcheck_strips_trailing_slash(mocker):
    """Verify a trailing slash in the URL does not produce a double-slash failure ping."""
    # Given a mocked HTTP call and a URL configured with a trailing slash
    mock_urlopen = mocker.patch("ezbak.container.urllib.request.urlopen", autospec=True)

    # When pinging for a failed run
    _ping_healthcheck("https://hc-ping.com/abc-123/", failed=True)

    # Then the /fail endpoint is fetched without a double slash
    assert mock_urlopen.call_args.args[0] == "https://hc-ping.com/abc-123/fail"


def test_ping_healthcheck_no_url_is_noop(mocker):
    """Verify no HTTP call is made when no healthcheck URL is configured."""
    # Given a mocked HTTP call
    mock_urlopen = mocker.patch("ezbak.container.urllib.request.urlopen", autospec=True)

    # When pinging without a configured URL
    _ping_healthcheck(None, failed=False)

    # Then no request is made
    mock_urlopen.assert_not_called()


def test_ping_healthcheck_swallows_errors(mocker):
    """Verify a failed ping never propagates and cannot break a backup run."""
    # Given an HTTP call that raises
    mocker.patch(
        "ezbak.container.urllib.request.urlopen",
        autospec=True,
        side_effect=urllib.error.URLError("boom"),
    )

    # When pinging, then no exception escapes
    _ping_healthcheck("https://hc-ping.com/abc-123", failed=False)


def test_run_scheduled_pings_success(filesystem, mocker):
    """Verify a scheduled run pings the success URL after the run completes cleanly."""
    # Given a scheduled run that succeeds
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    run = mocker.MagicMock()
    mock_ping = mocker.patch("ezbak.container._ping_healthcheck", autospec=True)
    scheduler = mocker.MagicMock()

    # When running the scheduled job
    _run_scheduled(app, scheduler, "https://hc-ping.com/abc-123", run)

    # Then the run executed and it pings for success
    run.assert_called_once_with(app, scheduler)
    mock_ping.assert_called_once_with("https://hc-ping.com/abc-123", failed=False)


def test_run_scheduled_pings_failure(filesystem, mocker):
    """Verify a scheduled run pings the failure URL when the run raises."""
    # Given a scheduled run that fails
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    run = mocker.MagicMock(side_effect=BackupFailedError(["dest1"]))
    mock_ping = mocker.patch("ezbak.container._ping_healthcheck", autospec=True)
    scheduler = mocker.MagicMock()

    # When running the scheduled job
    _run_scheduled(app, scheduler, "https://hc-ping.com/abc-123", run)

    # Then it pings for failure
    mock_ping.assert_called_once_with("https://hc-ping.com/abc-123", failed=True)


def test_entrypoint_restore_fails_when_no_backup_for_date(filesystem, capsys, tmp_path):
    """Verify the container exits non-zero when EZBAK_RESTORE_DATE matches no backup."""
    # Given a backup archive dated 2025 at the configured storage path
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
    # A restore date before the only backup on disk resolves to no match.
    os.environ["EZBAK_RESTORE_DATE"] = "2024"
    os.environ["EZBAK_LOG_LEVEL"] = "TRACE"

    # When running the entrypoint, then it exits non-zero instead of a silent success
    with pytest.raises(SystemExit) as exc_info:
        entrypoint()
    assert exc_info.value.code == 1
    output = capsys.readouterr().err
    assert "Backup restored" not in output
    assert "Restore complete" not in output


def test_entrypoint_restore_if_exists_no_backup_is_noop(filesystem, capsys, tmp_path):
    """Verify EZBAK_RESTORE_IF_EXISTS exits cleanly when no backup exists yet."""
    # Given an empty storage path, a fresh deployment with no backup to restore
    src_dir, dest1, _ = filesystem

    restore_path = tmp_path / "restore"
    restore_path.mkdir(exist_ok=True)

    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_ACTION"] = "restore"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_RESTORE_PATH"] = str(restore_path)
    os.environ["EZBAK_RESTORE_DATE"] = ""
    os.environ["EZBAK_RESTORE_IF_EXISTS"] = "true"
    os.environ["EZBAK_LOG_LEVEL"] = "TRACE"

    # When running the entrypoint, then it completes without exiting non-zero
    entrypoint()

    # Then it reports the no-op instead of failing the pre-start task
    output = capsys.readouterr().err
    assert "restore_if_exists is set" in output
    assert "Backup restored" not in output


def test_entrypoint_restore_if_exists_still_fails_on_corrupt_archive(filesystem, tmp_path):
    """Verify EZBAK_RESTORE_IF_EXISTS still fails a restore when the archive is corrupt."""
    # Given a corrupt backup archive, a real error rather than a missing backup
    src_dir, dest1, _ = filesystem
    backup_path = dest1 / f"test-{frozen_time_str}-yearly.tgz"
    backup_path.write_bytes(b"not a tarball")

    restore_path = tmp_path / "restore"
    restore_path.mkdir(exist_ok=True)

    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_ACTION"] = "restore"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_RESTORE_PATH"] = str(restore_path)
    os.environ["EZBAK_RESTORE_DATE"] = ""
    os.environ["EZBAK_RESTORE_IF_EXISTS"] = "true"
    os.environ["EZBAK_LOG_LEVEL"] = "TRACE"

    # When running the entrypoint, then a real failure still exits non-zero
    with pytest.raises(SystemExit) as exc_info:
        entrypoint()
    assert exc_info.value.code == 1


def test_entrypoint_restore_fails_when_archive_corrupt(filesystem, capsys, tmp_path):
    """Verify the container exits non-zero when a restore cannot extract the archive."""
    # Given a corrupt backup archive at the configured storage path
    src_dir, dest1, _ = filesystem
    backup_path = dest1 / f"test-{frozen_time_str}-yearly.tgz"
    backup_path.write_bytes(b"not a tarball")

    restore_path = tmp_path / "restore"
    restore_path.mkdir(exist_ok=True)

    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_ACTION"] = "restore"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_RESTORE_PATH"] = str(restore_path)
    os.environ["EZBAK_LOG_LEVEL"] = "TRACE"

    # When running the entrypoint, then it exits non-zero instead of a silent success
    with pytest.raises(SystemExit) as exc_info:
        entrypoint()
    assert exc_info.value.code == 1
    output = capsys.readouterr().err
    assert "Backup restored" not in output
