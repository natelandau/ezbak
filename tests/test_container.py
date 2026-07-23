"""Test the ezbak CLI."""

from __future__ import annotations

import os
import shutil
import signal
import urllib.error
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import time_machine
from pydantic import ValidationError

from ezbak import ezbak
from ezbak.constants import DEFAULT_COMPRESSION_LEVEL, DEFAULT_DATE_FORMAT, LogLevel, RestoreOutcome
from ezbak.container import (
    _ping_healthcheck,
    _run_scheduled,
    _run_shutdown_backup,
    do_backup,
    do_restore,
    log_configured_hooks,
)
from ezbak.container import main as entrypoint
from ezbak.env import EnvConfig
from ezbak.exceptions import BackupFailedError, HookFailedError
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
    os.environ["EZBAK_BACKUP_ON_SHUTDOWN"] = "false"
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


def _run_entrypoint_with_sigterm(mocker):
    """Run the cron entrypoint and deliver a SIGTERM once the scheduler loop starts.

    Keep the mocked scheduler reporting as running so only the delivered signal ends
    the loop, then invoke the handler the entrypoint registered. This exercises the
    real handler -> event -> loop-exit path without raising an OS signal that could
    kill the test run if a regression left the handler unregistered.
    """
    scheduler = mocker.patch("ezbak.container.BackgroundScheduler").return_value
    scheduler.running = True

    handlers = {}

    def capture_handler(signum, handler):
        handlers[signum] = handler

    mocker.patch("ezbak.container.signal.signal", side_effect=capture_handler)

    # Deliver the signal the first time the loop sleeps. A KeyError here would mean the
    # entrypoint never registered a SIGTERM handler, which fails the test cleanly.
    def deliver_sigterm(*_args: object, **_kwargs: object) -> None:
        handlers[signal.SIGTERM](signal.SIGTERM, None)

    mocker.patch("time.sleep", side_effect=deliver_sigterm)

    entrypoint()


@time_machine.travel(frozen_time, tick=False)
def test_entrypoint_cron_backup_on_shutdown_takes_final_backup(filesystem, capsys, mocker):
    """Verify an opted-in cron backup container takes a final backup on SIGTERM."""
    # Given a cron backup container that opted into backup-on-shutdown
    src_dir, dest1, _ = filesystem
    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_CRON"] = "*/1 * * * *"
    os.environ["EZBAK_BACKUP_ON_SHUTDOWN"] = "true"
    os.environ["EZBAK_LOG_LEVEL"] = "TRACE"

    # When a SIGTERM arrives while the scheduler runs
    _run_entrypoint_with_sigterm(mocker)

    # Then a final backup was written on shutdown
    output = capsys.readouterr().err
    assert "Taking a final backup before shutdown" in output
    assert Path(dest1 / f"test-{frozen_time_str}.tgz").exists()


@time_machine.travel(frozen_time, tick=False)
def test_entrypoint_cron_no_shutdown_backup_when_flag_off(filesystem, capsys, mocker):
    """Verify a cron backup container takes no final backup on SIGTERM by default."""
    # Given a cron backup container that did not opt in
    src_dir, dest1, _ = filesystem
    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_CRON"] = "*/1 * * * *"
    os.environ["EZBAK_BACKUP_ON_SHUTDOWN"] = "false"
    os.environ["EZBAK_LOG_LEVEL"] = "TRACE"

    # When a SIGTERM arrives while the scheduler runs
    _run_entrypoint_with_sigterm(mocker)

    # Then no final backup was written on shutdown
    output = capsys.readouterr().err
    assert "Taking a final backup before shutdown" not in output
    assert not Path(dest1 / f"test-{frozen_time_str}.tgz").exists()


@time_machine.travel(frozen_time, tick=False)
def test_entrypoint_cron_no_shutdown_backup_without_signal(filesystem, capsys):
    """Verify no final backup runs when the loop ends without a shutdown signal."""
    # Given an opted-in cron backup container. The autouse mock_run fixture leaves the
    # scheduler reporting not-running, so the loop exits on its own with no signal
    # delivered and the shutdown event never set.
    src_dir, dest1, _ = filesystem
    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_CRON"] = "*/1 * * * *"
    os.environ["EZBAK_BACKUP_ON_SHUTDOWN"] = "true"
    os.environ["EZBAK_LOG_LEVEL"] = "TRACE"

    entrypoint()

    # Then the shutdown backup is gated on an actual signal and does not run
    output = capsys.readouterr().err
    assert "Taking a final backup before shutdown" not in output
    assert not Path(dest1 / f"test-{frozen_time_str}.tgz").exists()


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
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"
    config = EnvConfig(name="test", source_paths=[src_dir], storage_paths=[dest1], _env_file=None)
    mocker.patch.object(app, "create_backup", side_effect=BackupFailedError(["dest1"]))
    prune_spy = mocker.patch.object(app, "prune_backups")

    # When running do_backup, then it re-raises the failure
    with pytest.raises(BackupFailedError):
        do_backup(app, config)

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


def test_backup_on_shutdown_defaults_off(filesystem):
    """Verify backup_on_shutdown is off when EZBAK_BACKUP_ON_SHUTDOWN is unset."""
    # Given no backup-on-shutdown env var
    src_dir, dest1, _ = filesystem
    os.environ.pop("EZBAK_BACKUP_ON_SHUTDOWN", None)
    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"

    # When loading the container config, then the flag defaults off
    config = EnvConfig(_env_file=None)
    assert config.backup_on_shutdown is False


def test_backup_on_shutdown_parses_true(filesystem):
    """Verify EZBAK_BACKUP_ON_SHUTDOWN=true populates the bool field."""
    # Given the flag set to a truthy string
    src_dir, dest1, _ = filesystem
    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_BACKUP_ON_SHUTDOWN"] = "true"
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"

    # When loading the container config, then the flag is enabled
    config = EnvConfig(_env_file=None)
    assert config.backup_on_shutdown is True


def test_cron_jitter_defaults_to_60(filesystem):
    """Verify cron_jitter defaults to 60 seconds when EZBAK_CRON_JITTER is unset."""
    # Given no jitter env var
    src_dir, dest1, _ = filesystem
    os.environ.pop("EZBAK_CRON_JITTER", None)
    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"

    # When loading the container config, then jitter defaults to 60
    config = EnvConfig(_env_file=None)
    assert config.cron_jitter == 60


def test_cron_jitter_parses_override(filesystem):
    """Verify EZBAK_CRON_JITTER overrides the default jitter."""
    # Given an explicit jitter value
    src_dir, dest1, _ = filesystem
    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_CRON_JITTER"] = "120"
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"

    # When loading the container config, then the override is used
    config = EnvConfig(_env_file=None)
    assert config.cron_jitter == 120


def test_cron_jitter_rejects_negative(filesystem):
    """Verify a negative EZBAK_CRON_JITTER is rejected at load time."""
    # Given a mistyped negative jitter value
    src_dir, dest1, _ = filesystem
    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_CRON_JITTER"] = "-1"
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"

    # When loading the container config, then validation fails
    with pytest.raises(ValidationError):
        EnvConfig(_env_file=None)


def test_hooks_default_to_none(filesystem):
    """Verify the four hook fields default to None and hook_timeout to 300."""
    # Given no hook env vars
    src_dir, dest1, _ = filesystem
    for var in (
        "EZBAK_PRE_BACKUP_HOOK",
        "EZBAK_POST_BACKUP_HOOK",
        "EZBAK_PRE_RESTORE_HOOK",
        "EZBAK_POST_RESTORE_HOOK",
        "EZBAK_HOOK_TIMEOUT",
    ):
        os.environ.pop(var, None)
    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"

    # When loading the container config, then hooks are unset and timeout defaults to 300
    config = EnvConfig(_env_file=None)
    assert config.pre_backup_hook is None
    assert config.post_backup_hook is None
    assert config.pre_restore_hook is None
    assert config.post_restore_hook is None
    assert config.hook_timeout == 300


def test_pre_backup_hook_parses(filesystem):
    """Verify EZBAK_PRE_BACKUP_HOOK populates the hook field verbatim."""
    # Given a pre-backup hook command
    src_dir, dest1, _ = filesystem
    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_PRE_BACKUP_HOOK"] = 'sqlite3 /data/db ".backup /data/db.bak"'
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"

    # When loading the container config, then the command is stored unchanged
    config = EnvConfig(_env_file=None)
    assert config.pre_backup_hook == 'sqlite3 /data/db ".backup /data/db.bak"'


def test_hook_timeout_parses_override(filesystem):
    """Verify EZBAK_HOOK_TIMEOUT overrides the default timeout."""
    # Given an explicit timeout
    src_dir, dest1, _ = filesystem
    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_HOOK_TIMEOUT"] = "30"
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"

    # When loading the container config, then the override is used
    config = EnvConfig(_env_file=None)
    assert config.hook_timeout == 30


def test_hook_timeout_rejects_negative(filesystem):
    """Verify a negative EZBAK_HOOK_TIMEOUT is rejected at load time."""
    # Given a mistyped negative timeout
    src_dir, dest1, _ = filesystem
    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_HOOK_TIMEOUT"] = "-1"
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"

    # When loading the container config, then validation fails
    with pytest.raises(ValidationError):
        EnvConfig(_env_file=None)


def test_run_shutdown_backup_runs_when_opted_in(filesystem, mocker):
    """Verify a cron BACKUP container takes a final backup on shutdown when opted in."""
    # Given a backup container that opted into backup-on-shutdown
    src_dir, dest1, _ = filesystem
    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_BACKUP_ON_SHUTDOWN"] = "true"
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"
    config = EnvConfig(_env_file=None)
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    mock_scheduled = mocker.patch("ezbak.container._run_scheduled", autospec=True)
    scheduler = mocker.MagicMock()

    # When shutting down
    _run_shutdown_backup(app, scheduler, config)

    # Then the final backup runs through the same path as a scheduled run
    mock_scheduled.assert_called_once_with(app, scheduler, config, do_backup)


def test_run_shutdown_backup_skips_when_flag_off(filesystem, mocker):
    """Verify no final backup runs on shutdown when the flag is not set."""
    # Given a backup container that did not opt in
    src_dir, dest1, _ = filesystem
    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_ACTION"] = "backup"
    os.environ["EZBAK_BACKUP_ON_SHUTDOWN"] = "false"
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"
    config = EnvConfig(_env_file=None)
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    mock_scheduled = mocker.patch("ezbak.container._run_scheduled", autospec=True)

    # When shutting down, then no final backup is taken
    _run_shutdown_backup(app, mocker.MagicMock(), config)
    mock_scheduled.assert_not_called()


def test_run_shutdown_backup_skips_for_restore_action(filesystem, mocker):
    """Verify a restore container never takes a backup on shutdown, even if opted in."""
    # Given a restore container with the flag set (a final backup would be meaningless)
    src_dir, dest1, _ = filesystem
    os.environ["EZBAK_NAME"] = "test"
    os.environ["EZBAK_SOURCE_PATHS"] = str(src_dir)
    os.environ["EZBAK_STORAGE_PATHS"] = str(dest1)
    os.environ["EZBAK_ACTION"] = "restore"
    os.environ["EZBAK_BACKUP_ON_SHUTDOWN"] = "true"
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"
    config = EnvConfig(_env_file=None)
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    mock_scheduled = mocker.patch("ezbak.container._run_scheduled", autospec=True)

    # When shutting down, then no backup is taken
    _run_shutdown_backup(app, mocker.MagicMock(), config)
    mock_scheduled.assert_not_called()


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
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"
    config = EnvConfig(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        healthcheck_url="https://hc-ping.com/abc-123",
        _env_file=None,
    )
    run = mocker.MagicMock()
    mock_ping = mocker.patch("ezbak.container._ping_healthcheck", autospec=True)
    scheduler = mocker.MagicMock()

    # When running the scheduled job
    _run_scheduled(app, scheduler, config, run)

    # Then the run executed and it pings for success
    run.assert_called_once_with(app, config, scheduler)
    mock_ping.assert_called_once_with("https://hc-ping.com/abc-123", failed=False)


def test_run_scheduled_pings_failure(filesystem, mocker):
    """Verify a scheduled run pings the failure URL when the run raises."""
    # Given a scheduled run that fails
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"
    config = EnvConfig(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        healthcheck_url="https://hc-ping.com/abc-123",
        _env_file=None,
    )
    run = mocker.MagicMock(side_effect=BackupFailedError(["dest1"]))
    mock_ping = mocker.patch("ezbak.container._ping_healthcheck", autospec=True)
    scheduler = mocker.MagicMock()

    # When running the scheduled job
    _run_scheduled(app, scheduler, config, run)

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


def test_do_backup_runs_hooks_around_backup(filesystem, tmp_path):
    """Verify pre- and post-backup hooks both run around a successful backup."""
    # Given pre/post hooks that drop marker files and a real backup target
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"
    pre = tmp_path / "pre"
    post = tmp_path / "post"
    config = EnvConfig(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        pre_backup_hook=f"touch {pre}",
        post_backup_hook=f"touch {post}",
        _env_file=None,
    )

    # When running do_backup
    do_backup(app, config)

    # Then both hooks ran and a backup landed
    assert pre.exists()
    assert post.exists()
    assert list(dest1.glob("*.tgz"))


def test_do_backup_pre_hook_failure_skips_backup(filesystem, mocker):
    """Verify a failing pre-backup hook aborts before create_backup runs."""
    # Given a pre-backup hook that exits non-zero
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"
    config = EnvConfig(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        pre_backup_hook="exit 1",
        _env_file=None,
    )
    create_spy = mocker.patch.object(app, "create_backup")

    # When running do_backup, then it raises and never creates a backup
    with pytest.raises(HookFailedError):
        do_backup(app, config)
    create_spy.assert_not_called()


def test_do_backup_post_hook_failure_keeps_backup(filesystem):
    """Verify a failing post-backup hook fails the run but keeps the stored backup."""
    # Given a post-backup hook that exits non-zero and a real backup target
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"
    config = EnvConfig(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        post_backup_hook="exit 1",
        _env_file=None,
    )

    # When running do_backup, then it raises but the archive was written first
    with pytest.raises(HookFailedError):
        do_backup(app, config)
    assert list(dest1.glob("*.tgz"))


def test_do_restore_pre_hook_failure_skips_restore(filesystem, mocker):
    """Verify a failing pre-restore hook aborts before restore runs."""
    # Given a pre-restore hook that exits non-zero
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"
    config = EnvConfig(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        pre_restore_hook="exit 1",
        _env_file=None,
    )
    restore_spy = mocker.patch.object(app, "restore_backup")

    # When running do_restore, then it raises and never restores
    with pytest.raises(HookFailedError):
        do_restore(app, config)
    restore_spy.assert_not_called()


def test_do_restore_post_hook_runs_on_successful_restore(filesystem, tmp_path, mocker):
    """Verify the post-restore hook runs after an actual restore."""
    # Given a restore that succeeds and a post-restore marker hook
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"
    mocker.patch.object(app, "restore_backup", return_value=RestoreOutcome.RESTORED)
    marker = tmp_path / "post_restore"
    config = EnvConfig(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        post_restore_hook=f"touch {marker}",
        _env_file=None,
    )

    # When running do_restore, then the post hook ran
    do_restore(app, config)
    assert marker.exists()


def test_do_restore_post_hook_skipped_on_noop(filesystem, tmp_path, mocker):
    """Verify the post-restore hook is skipped when no backup was restored."""
    # Given a restore_if_exists no-op (restore_backup returns NO_BACKUP)
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1], restore_if_exists=True)
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"
    mocker.patch.object(app, "restore_backup", return_value=RestoreOutcome.NO_BACKUP)
    marker = tmp_path / "post_restore"
    config = EnvConfig(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        restore_if_exists=True,
        post_restore_hook=f"touch {marker}",
        _env_file=None,
    )

    # When running do_restore, then it returns cleanly and the post hook did not run
    do_restore(app, config)
    assert not marker.exists()


def test_do_restore_post_hook_skipped_on_populated(filesystem, tmp_path, mocker):
    """Verify the post-restore hook does not run when the target was already populated."""
    # Given a restore that skips because the target already holds data
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1], restore_if_exists=True)
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"
    mocker.patch.object(app, "restore_backup", return_value=RestoreOutcome.SKIPPED_POPULATED)
    marker = tmp_path / "post_restore"
    config = EnvConfig(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        restore_if_exists=True,
        post_restore_hook=f"touch {marker}",
        _env_file=None,
    )

    # When running do_restore, then it returns cleanly and the post hook did not run
    do_restore(app, config)
    assert not marker.exists()


def test_log_configured_hooks_logs_active_hook_at_info(filesystem, capsys):
    """Verify a configured hook matching the action is announced at INFO on boot."""
    # Given a backup container with a pre-backup hook configured
    instantiate_logger(LogLevel.INFO)
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"
    os.environ["EZBAK_ACTION"] = "backup"
    src_dir, dest1, _ = filesystem
    config = EnvConfig(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        pre_backup_hook="echo hi",
        hook_timeout=30,
        _env_file=None,
    )

    # When logging the hook configuration at boot
    log_configured_hooks(config)

    # Then the active hook and its timeout are surfaced at INFO
    output = capsys.readouterr().err
    assert "INFO" in output
    assert "pre-backup hook configured (30s timeout): echo hi" in output


def test_log_configured_hooks_warns_on_action_mismatch(filesystem, capsys):
    """Verify a hook configured for the other action is flagged as never running."""
    # Given a backup container that mistakenly configured a restore hook
    instantiate_logger(LogLevel.INFO)
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"
    os.environ["EZBAK_ACTION"] = "backup"
    src_dir, dest1, _ = filesystem
    config = EnvConfig(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        pre_restore_hook="echo hi",
        _env_file=None,
    )

    # When logging the hook configuration at boot
    log_configured_hooks(config)

    # Then the mismatched hook is warned about instead of announced as active
    output = capsys.readouterr().err
    assert "WARNING" in output
    assert "pre-restore hook is configured but EZBAK_ACTION is 'backup'" in output


def test_log_configured_hooks_silent_when_no_hooks(filesystem, capsys):
    """Verify no hook lines are logged when no hooks are configured."""
    # Given a container with no hooks configured
    instantiate_logger(LogLevel.INFO)
    os.environ["EZBAK_LOG_LEVEL"] = "INFO"
    os.environ["EZBAK_ACTION"] = "backup"
    src_dir, dest1, _ = filesystem
    config = EnvConfig(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        _env_file=None,
    )

    # When logging the hook configuration at boot
    log_configured_hooks(config)

    # Then nothing about hooks is emitted
    output = capsys.readouterr().err
    assert "hook" not in output
