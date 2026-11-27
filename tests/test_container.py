"""Test the ezbak container entrypoint."""

from __future__ import annotations

import os
import shutil
import signal
import threading
import urllib.error
from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, TypeAlias
from unittest.mock import call
from zoneinfo import ZoneInfo

import pytest
import time_machine
from apscheduler.events import (
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
    EVENT_JOB_SUBMITTED,
    JobEvent,
    JobExecutionEvent,
    JobSubmissionEvent,
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from ezbak.constants import DEFAULT_DATE_FORMAT, Action, RestoreOutcome
from ezbak.container import (
    _log_skipped_run,
    _ping_healthcheck,
    _run_scheduled,
    _run_shutdown_backup,
    _trigger_now,
    do_backup,
    do_restore,
    log_configured_hooks,
)
from ezbak.container import main as entrypoint
from ezbak.core import EZBak
from ezbak.env import EnvConfig
from ezbak.exceptions import BackupFailedError, HookFailedError
from tests.helpers import FIXTURE_ARCHIVE

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path
    from unittest.mock import MagicMock

    from pytest_mock import MockerFixture

UTC = ZoneInfo("UTC")
frozen_time = datetime(2025, 6, 9, 0, 0, tzinfo=UTC)
frozen_time_str = frozen_time.strftime(DEFAULT_DATE_FORMAT)
HEALTHCHECK_URL = "https://hc-ping.com/abc-123"

SignalBatch: TypeAlias = signal.Signals | list[signal.Signals] | None


@pytest.fixture(autouse=True)  # ruff:ignore[pytest-fixture-autouse]
def restore_signal_handlers() -> Generator[None, None, None]:
    """Restore the process signal handlers that `entrypoint()` replaces, so none outlive a test."""
    saved = {
        signum: signal.getsignal(signum)
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1)
    }
    yield
    for signum, handler in saved.items():
        signal.signal(signum, handler)


@pytest.fixture
def mock_run(mocker: MockerFixture) -> MagicMock:
    """Replace the scheduler and the loop's sleep so a cron entrypoint returns at once.

    Returns:
        MagicMock: The scheduler instance the entrypoint builds, reporting not-running.
    """
    scheduler = mocker.patch("ezbak.container.BackgroundScheduler").return_value
    scheduler.running = False
    mocker.patch("ezbak.container.time", SimpleNamespace(sleep=mocker.Mock(return_value=None)))
    return scheduler


@pytest.fixture
def container_env(
    filesystem: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> Callable[..., tuple[Path, Path]]:
    """Provide a factory that configures a backup container through EZBAK_ env vars.

    Keyword arguments override or add settings as `EZBAK_<KEY>` vars.

    Returns:
        Callable[..., tuple[Path, Path]]: Set the env and return the source and first storage dir.
    """
    src_dir, dest1, _ = filesystem

    def _configure(**overrides: str) -> tuple[Path, Path]:
        settings = {
            "name": "test",
            "action": "backup",
            "source_paths": str(src_dir),
            "storage_paths": str(dest1),
            **overrides,
        }
        for key, value in settings.items():
            monkeypatch.setenv(f"EZBAK_{key.upper()}", value)
        return src_dir, dest1

    return _configure


@pytest.fixture
def restore_env(
    container_env: Callable[..., tuple[Path, Path]], restore_dir: Path
) -> Callable[..., tuple[Path, Path]]:
    """Provide a factory that configures a restore container with an optional archive on disk.

    Returns:
        Callable[..., tuple[Path, Path]]: Set the env and return the storage and restore dirs.
    """

    def _configure(
        archive: Literal["valid", "corrupt", "none"], **overrides: str
    ) -> tuple[Path, Path]:
        _, dest1 = container_env(action="restore", restore_path=str(restore_dir), **overrides)
        backup_path = dest1 / f"test-{frozen_time_str}-yearly.tgz"
        if archive == "valid":
            shutil.copy2(FIXTURE_ARCHIVE, backup_path)
        elif archive == "corrupt":
            backup_path.write_bytes(b"not a tarball")
        return dest1, restore_dir

    return _configure


@pytest.fixture
def make_app(filesystem: tuple[Path, Path, Path]) -> Callable[..., tuple[EZBak, EnvConfig]]:
    """Provide a factory for an app and the container config it was built from.

    Returns:
        Callable[..., tuple[EZBak, EnvConfig]]: Build both from config overrides.
    """
    src_dir, dest1, _ = filesystem

    def _make(**overrides: Any) -> tuple[EZBak, EnvConfig]:  # ruff:ignore[any-type]
        config = EnvConfig(
            name="test",
            source_paths=[src_dir],
            storage_paths=[dest1],
            _env_file=None,
            **overrides,
        )
        return EZBak(config), config

    return _make


@pytest.fixture
def held_run_lock() -> Generator[threading.Lock, None, None]:
    """Provide a run lock already held by another run, released on teardown.

    Yields:
        threading.Lock: The held lock.
    """
    lock = threading.Lock()
    lock.acquire()
    yield lock
    lock.release()


@pytest.fixture
def running_scheduler() -> Generator[BackgroundScheduler, None, None]:
    """Provide a started real scheduler, shut down and joined on teardown.

    Yields:
        BackgroundScheduler: The running scheduler.
    """
    scheduler = BackgroundScheduler()
    scheduler.start()
    yield scheduler
    scheduler.shutdown(wait=True)


def _run_entrypoint() -> int | str | None:
    """Run the container entrypoint and return its exit code, 0 when it returns normally.

    Returns:
        int | str | None: The exit code.
    """
    try:
        entrypoint()
    except SystemExit as exc:
        return exc.code
    return 0


def _run_entrypoint_with_signals(
    mocker: MockerFixture, scheduler: MagicMock, signals: list[SignalBatch]
) -> None:
    """Run the cron entrypoint, delivering signals to the scheduler loop pass by pass.

    Keep the mocked scheduler reporting as running so only the delivered signals end
    the loop, then invoke the handler the entrypoint registered for each one in order.
    Once the list is used up the scheduler stops on its own, as it would if it died.
    This exercises the real handler -> event -> loop-exit path without raising an OS
    signal that could kill the test run if a regression left the handler unregistered;
    a `KeyError` here means the entrypoint never registered it.

    Args:
        mocker: The pytest-mock fixture.
        scheduler: The mocked scheduler instance from `mock_run`.
        signals: One entry per scheduler loop pass, in order. An entry is a single
            signal, a list of signals delivered together in that pass, or None to
            deliver nothing that pass.
    """
    scheduler.running = True
    handlers = {}

    def capture_handler(signum: int, handler: Callable[[int, object], None]) -> None:
        handlers[signum] = handler

    mocker.patch("ezbak.container.signal.signal", side_effect=capture_handler)

    remaining = list(signals)

    def deliver(*_args: object, **_kwargs: object) -> None:
        if not remaining:
            scheduler.running = False
            return
        batch = remaining.pop(0)
        if batch is None:
            return
        for signum in batch if isinstance(batch, list) else [batch]:
            handlers[signum](signum, None)

    mocker.patch("ezbak.container.time", SimpleNamespace(sleep=mocker.Mock(side_effect=deliver)))

    entrypoint()


@time_machine.travel(frozen_time, tick=False)
@pytest.mark.usefixtures("mock_run")
def test_entrypoint_create_backup(container_env, filesystem):
    """Verify a one-shot backup container writes an archive to every storage path."""
    # Given a backup container with two storage paths
    _, dest1, dest2 = filesystem
    container_env(storage_paths=f"{dest1},{dest2}")

    # When the container runs
    entrypoint()

    # Then each storage path holds the archive
    filename = f"test-{frozen_time_str}.tgz"
    assert (dest1 / filename).exists()
    assert (dest2 / filename).exists()


def test_entrypoint_cron_starts_scheduler_and_exits_on_sigterm(container_env, mock_run, mocker):
    """Verify a cron container starts its scheduler and shuts it down on SIGTERM."""
    # Given a cron backup container
    container_env(cron="*/1 * * * *")

    # When a SIGTERM arrives while the scheduler runs
    _run_entrypoint_with_signals(mocker, mock_run, [signal.SIGTERM])

    # Then the scheduler was started and then shut down, and the entrypoint returned
    mock_run.start.assert_called_once()
    mock_run.shutdown.assert_called_once()


@pytest.mark.parametrize(
    ("action", "on_shutdown", "signals", "expect_backup"),
    [
        ("backup", "true", [signal.SIGTERM], True),
        ("backup", "false", [signal.SIGTERM], False),
        ("backup", "true", [], False),
        ("restore", "true", [signal.SIGTERM], False),
    ],
    ids=["opted-in-sigterm", "flag-off", "no-signal", "restore-action"],
)
def test_entrypoint_cron_shutdown_backup(
    container_env, restore_dir, mock_run, mocker, action, on_shutdown, signals, expect_backup
):
    """Verify a cron container takes a final backup only for an opted-in backup on a signal."""
    # Given a cron container
    _, dest1 = container_env(
        action=action,
        cron="*/1 * * * *",
        backup_on_shutdown=on_shutdown,
        restore_path=str(restore_dir),
    )

    # When the loop ends, by a signal or by the scheduler stopping on its own
    _run_entrypoint_with_signals(mocker, mock_run, signals)

    # Then a final archive exists only when the backup was expected
    assert bool(list(dest1.glob("*.tgz"))) is expect_backup


@pytest.mark.parametrize(
    ("action", "signals", "expected_runs"),
    [
        (Action.BACKUP, [signal.SIGUSR1, None, signal.SIGTERM], 1),
        (Action.RESTORE, [signal.SIGUSR1, signal.SIGTERM], 1),
        (Action.BACKUP, [signal.SIGTERM], 0),
    ],
    ids=["backup-one-signal-then-idle-pass", "restore-one-signal", "backup-no-signal"],
)
def test_entrypoint_cron_sigusr1_forces_one_run_per_signal(
    container_env, restore_dir, mock_run, mocker, action, signals, expected_runs
):
    """Verify each SIGUSR1 forces exactly one run of the container's own action."""
    # Given a cron container
    container_env(action=action.value, cron="0 3 * * *", restore_path=str(restore_dir))
    trigger_now = mocker.patch("ezbak.container._trigger_now", autospec=True)

    # When the signals arrive, pass by pass, until SIGTERM ends the loop
    _run_entrypoint_with_signals(mocker, mock_run, signals)

    # Then the configured action was forced once per signal and never repeated
    assert trigger_now.call_args_list == [call(mock_run, action)] * expected_runs


def test_entrypoint_cron_sigusr1_lost_to_shutdown_is_reported(
    container_env, mock_run, mocker, capsys
):
    """Verify a trigger dropped in favor of a simultaneous shutdown is logged."""
    # Given a cron backup container
    container_env(cron="0 3 * * *")
    trigger_now = mocker.patch("ezbak.container._trigger_now", autospec=True)

    # When SIGUSR1 and SIGTERM land in the same loop pass
    _run_entrypoint_with_signals(mocker, mock_run, [[signal.SIGUSR1, signal.SIGTERM]])

    # Then shutdown wins, and the operator is told their trigger was dropped
    trigger_now.assert_not_called()
    assert "pending trigger" in capsys.readouterr().err


@pytest.mark.usefixtures("mock_run")
def test_entrypoint_one_shot_ignores_sigusr1(container_env):
    """Verify a one-shot container ignores SIGUSR1 instead of being killed by it."""
    # Given a one-shot backup container whose pre-backup hook signals the container
    _, dest1 = container_env(pre_backup_hook=f"kill -USR1 {os.getpid()}")

    # Start from the POSIX default (terminate), which is what a container process has
    # before ezbak touches it, so a handler left by other code cannot hide a regression
    signal.signal(signal.SIGUSR1, signal.SIG_DFL)

    # When SIGUSR1 arrives mid-run
    entrypoint()

    # Then the run survived the signal and completed
    assert list(dest1.glob("*.tgz"))


@pytest.mark.parametrize(
    ("event", "cause"),
    [
        (
            JobSubmissionEvent(EVENT_JOB_MAX_INSTANCES, "backup", "default", []),
            "already in progress",
        ),
        (JobExecutionEvent(EVENT_JOB_MISSED, "backup", "default", None), "grace period"),
    ],
    ids=["overlapping-run", "missed-run"],
)
def test_log_skipped_run_reports_the_skip(log_messages, event, cause):
    """Verify a run the scheduler skipped is reported with its job and cause."""
    # When the listener handles a skip event for the backup job
    _log_skipped_run(event)

    # Then the skip is visible with its cause
    assert len(log_messages) == 1
    assert "backup" in log_messages[0]
    assert cause in log_messages[0]


@pytest.mark.usefixtures("mock_run")
def test_entrypoint_restore_backup(restore_env):
    """Verify a one-shot restore container restores the backup into the restore path."""
    # Given a restorable backup and a restore container
    _, restore_dir = restore_env("valid")

    # When the container runs
    entrypoint()

    # Then the archive's files are in the restore path
    assert (restore_dir / "src" / "baz.txt").exists()


@pytest.mark.parametrize(
    "overrides",
    [
        # Blank keys, as an env template with unfilled secrets leaves them
        {"storage_paths": "", "aws_s3_bucket_name": "test-bucket", "aws_access_key": ""},
        {"pre_backup_hook": "exit 1"},
    ],
    ids=["s3-credentials-missing", "pre-hook-fails"],
)
@pytest.mark.usefixtures("mock_run")
def test_entrypoint_backup_failure_exits_nonzero(container_env, overrides):
    """Verify a one-shot backup that fails exits non-zero and stores no archive."""
    # Given a backup container that cannot complete its backup
    _, dest1 = container_env(**overrides)

    # When running the entrypoint, then it exits non-zero
    assert _run_entrypoint() == 1

    # Then no archive was stored
    assert not list(dest1.glob("*.tgz"))


def test_do_backup_prunes_even_when_backup_fails(make_app, mocker):
    """Verify retention still runs when a destination fails so backups don't accumulate."""
    # Given an app whose backup raises for a failed destination
    app, config = make_app()
    mocker.patch.object(app, "create_backup", side_effect=BackupFailedError(["dest1"]))
    prune_spy = mocker.patch.object(app, "prune_backups")

    # When running do_backup, then it re-raises the failure
    with pytest.raises(BackupFailedError):
        do_backup(app, config)

    # Then pruning still ran despite the failed backup
    prune_spy.assert_called_once()


@pytest.mark.usefixtures("mock_run")
def test_entrypoint_invalid_config_exits_cleanly(monkeypatch):
    """Verify the container exits non-zero instead of raising when the config is invalid."""
    # Given no backup name configured
    monkeypatch.setenv("EZBAK_NAME", "")
    monkeypatch.setenv("EZBAK_ACTION", "backup")

    # When running the entrypoint, then it exits non-zero rather than raising a validation error
    assert _run_entrypoint() == 1


@pytest.mark.usefixtures("mock_run")
def test_entrypoint_without_action_exits_nonzero(container_env, monkeypatch):
    """Verify a container with no action configured exits non-zero without backing up."""
    # Given a valid container config with no action
    _, dest1 = container_env()
    monkeypatch.delenv("EZBAK_ACTION", raising=False)

    # When running the entrypoint, then it exits non-zero
    assert _run_entrypoint() == 1

    # Then no archive was written
    assert not list(dest1.glob("*.tgz"))


def test_run_shutdown_backup_skips_while_a_run_is_in_flight(
    make_app, mocker, filesystem, held_run_lock
):
    """Verify the final backup is skipped when a run is already in progress."""
    # Given an opted-in backup container whose run lock is held by an in-flight run
    _, dest1, _ = filesystem
    app, config = make_app(ezbak_action="backup", backup_on_shutdown=True)

    # When shutting down
    _run_shutdown_backup(app, mocker.MagicMock(), config, held_run_lock)

    # Then the redundant final backup is skipped
    assert not list(dest1.glob("*.tgz"))


def test_run_scheduled_holds_the_run_lock_for_the_whole_run(make_app, mocker):
    """Verify a scheduled run holds the run lock so a shutdown backup cannot overlap it."""
    # Given a scheduled run and the container's run lock
    app, config = make_app()
    run_lock = threading.Lock()
    held: list[bool] = []

    def record_lock_state(*_args: object) -> None:
        held.append(run_lock.locked())

    # When the run executes
    _run_scheduled(app, mocker.MagicMock(), config, record_lock_state, run_lock)

    # Then the lock was held during the run and released afterward
    assert held == [True]
    assert not run_lock.locked()


def test_run_scheduled_skips_when_the_run_lock_is_held(make_app, mocker, held_run_lock):
    """Verify a scheduled run is dropped, not queued, while the shutdown backup holds the lock."""
    # Given the container's run lock already held by the shutdown backup
    app, config = make_app()
    run = mocker.MagicMock()

    # When a job the executor already accepted reaches the run
    _run_scheduled(app, mocker.MagicMock(), config, run, held_run_lock)

    # Then it returns immediately instead of blocking, without running
    run.assert_not_called()


@pytest.mark.parametrize(
    ("url", "failed", "expected_url"),
    [
        (HEALTHCHECK_URL, False, HEALTHCHECK_URL),
        (HEALTHCHECK_URL, True, f"{HEALTHCHECK_URL}/fail"),
        (f"{HEALTHCHECK_URL}/", True, f"{HEALTHCHECK_URL}/fail"),
        (None, False, None),
    ],
    ids=["success", "failure", "failure-trailing-slash", "no-url-noop"],
)
def test_ping_healthcheck_fetches_the_outcome_url(mocker, url, failed, expected_url):
    """Verify the healthcheck ping fetches the base or /fail URL, and nothing without a URL."""
    # Given a mocked HTTP call
    mock_urlopen = mocker.patch("ezbak.container.urllib.request.urlopen", autospec=True)

    # When pinging for the run's outcome
    _ping_healthcheck(url, failed=failed)

    # Then exactly the expected URL was fetched, or none at all
    fetched = [c.args[0] for c in mock_urlopen.call_args_list]
    assert fetched == ([expected_url] if expected_url else [])


def test_ping_healthcheck_swallows_errors(mocker):
    """Verify a failed ping never propagates and cannot break a backup run."""
    # Given an HTTP call that raises
    mocker.patch(
        "ezbak.container.urllib.request.urlopen",
        autospec=True,
        side_effect=urllib.error.URLError("boom"),
    )

    # When pinging, then no exception escapes
    _ping_healthcheck(HEALTHCHECK_URL, failed=False)


@pytest.mark.parametrize(
    ("run_error", "failed"),
    [(None, False), (BackupFailedError(["dest1"]), True)],
    ids=["success", "failure"],
)
def test_run_scheduled_pings_the_outcome(make_app, mocker, run_error, failed):
    """Verify a scheduled run pings the healthcheck with whether the run failed."""
    # Given a scheduled run that succeeds or raises
    app, config = make_app(healthcheck_url=HEALTHCHECK_URL)
    run = mocker.MagicMock(side_effect=run_error)
    mock_ping = mocker.patch("ezbak.container._ping_healthcheck", autospec=True)
    scheduler = mocker.MagicMock()

    # When running the scheduled job
    _run_scheduled(app, scheduler, config, run, threading.Lock())

    # Then the run executed and it pinged for its outcome
    run.assert_called_once_with(app, config, scheduler)
    mock_ping.assert_called_once_with(HEALTHCHECK_URL, failed=failed)


@pytest.mark.parametrize(
    ("action", "setup", "failed"),
    [
        (Action.BACKUP, {}, False),
        (Action.BACKUP, {"pre_backup_hook": "exit 1"}, True),
        (Action.RESTORE, {"archive": "valid"}, False),
        (Action.RESTORE, {"archive": "corrupt"}, True),
    ],
    ids=["backup-success", "backup-failure", "restore-success", "restore-failure"],
)
@pytest.mark.usefixtures("mock_run")
def test_entrypoint_one_shot_pings_the_outcome(
    container_env, restore_env, mocker, action, setup, failed
):
    """Verify a one-shot run pings its outcome so post-stop and pre-start tasks are observable."""
    # Given a one-shot container with a healthcheck URL
    if action is Action.RESTORE:
        restore_env(**setup, healthcheck_url=HEALTHCHECK_URL)
    else:
        container_env(**setup, healthcheck_url=HEALTHCHECK_URL)
    mock_ping = mocker.patch("ezbak.container._ping_healthcheck", autospec=True)

    # When running the entrypoint
    exit_code = _run_entrypoint()

    # Then it pings for the outcome and the exit code still reports a failure
    assert exit_code == (1 if failed else 0)
    mock_ping.assert_called_once_with(HEALTHCHECK_URL, failed=failed)


@pytest.mark.usefixtures("mock_run")
def test_entrypoint_restore_fails_when_no_backup_for_date(restore_env):
    """Verify the container exits non-zero when EZBAK_RESTORE_DATE matches no backup."""
    # Given a backup dated 2025 and a restore date before it, which resolves to no match
    _, restore_dir = restore_env("valid", restore_date="2024")

    # When running the entrypoint, then it exits non-zero instead of a silent success
    assert _run_entrypoint() == 1

    # Then nothing was restored
    assert not any(restore_dir.iterdir())


@pytest.mark.usefixtures("mock_run")
def test_entrypoint_skip_if_no_backup_is_noop(restore_env):
    """Verify EZBAK_SKIP_IF_NO_BACKUP exits cleanly when no backup exists yet."""
    # Given an empty storage path, a fresh deployment with no backup to restore
    _, restore_dir = restore_env("none", skip_if_no_backup="true")

    # When running the entrypoint, then it completes without exiting non-zero
    assert _run_entrypoint() == 0

    # Then nothing was restored
    assert not any(restore_dir.iterdir())


@pytest.mark.parametrize("skip_if_no_backup", ["false", "true"], ids=["skip-off", "skip-on"])
@pytest.mark.usefixtures("mock_run")
def test_entrypoint_restore_fails_when_archive_corrupt(restore_env, skip_if_no_backup):
    """Verify a corrupt archive fails the restore, even with EZBAK_SKIP_IF_NO_BACKUP set."""
    # Given a corrupt backup archive, a real error rather than a missing backup
    _, restore_dir = restore_env("corrupt", skip_if_no_backup=skip_if_no_backup)

    # When running the entrypoint, then it exits non-zero
    assert _run_entrypoint() == 1

    # Then nothing was restored
    assert not any(restore_dir.iterdir())


def test_do_backup_runs_hooks_around_backup(make_app, filesystem, tmp_path):
    """Verify pre- and post-backup hooks both run around a successful backup."""
    # Given pre/post hooks that drop marker files and a real backup target
    _, dest1, _ = filesystem
    pre = tmp_path / "pre"
    post = tmp_path / "post"
    app, config = make_app(pre_backup_hook=f"touch {pre}", post_backup_hook=f"touch {post}")

    # When running do_backup
    do_backup(app, config)

    # Then both hooks ran and a backup landed
    assert pre.exists()
    assert post.exists()
    assert list(dest1.glob("*.tgz"))


@pytest.mark.parametrize(
    ("hook", "run", "action_method"),
    [
        ("pre_backup_hook", do_backup, "create_backup"),
        ("pre_restore_hook", do_restore, "restore_backup"),
    ],
    ids=["backup", "restore"],
)
def test_pre_hook_failure_aborts_the_run(make_app, mocker, hook, run, action_method):
    """Verify a failing pre hook raises before the backup or restore runs."""
    # Given a pre hook that exits non-zero
    app, config = make_app(**{hook: "exit 1"})
    action_spy = mocker.patch.object(app, action_method)

    # When running the action, then it raises and never performs it
    with pytest.raises(HookFailedError):
        run(app, config)
    action_spy.assert_not_called()


def test_do_backup_post_hook_failure_keeps_backup(make_app, filesystem):
    """Verify a failing post-backup hook fails the run but keeps the stored backup."""
    # Given a post-backup hook that exits non-zero and a real backup target
    _, dest1, _ = filesystem
    app, config = make_app(post_backup_hook="exit 1")

    # When running do_backup, then it raises but the archive was written first
    with pytest.raises(HookFailedError):
        do_backup(app, config)
    assert list(dest1.glob("*.tgz"))


@pytest.mark.parametrize(
    ("outcome", "hook_runs"),
    [
        (RestoreOutcome.RESTORED, True),
        (RestoreOutcome.NO_BACKUP, False),
        (RestoreOutcome.SKIPPED_POPULATED, False),
    ],
    ids=["restored", "no-backup", "target-populated"],
)
def test_do_restore_post_hook_runs_only_after_a_restore(
    make_app, mocker, tmp_path, outcome, hook_runs
):
    """Verify the post-restore hook runs only when a restore actually wrote files."""
    # Given a restore with a known outcome and a post-restore marker hook
    marker = tmp_path / "post_restore"
    app, config = make_app(skip_if_no_backup=True, post_restore_hook=f"touch {marker}")
    mocker.patch.object(app, "restore_backup", return_value=outcome)

    # When running do_restore, then it returns cleanly and the hook ran only after a restore
    do_restore(app, config)
    assert marker.exists() is hook_runs


def test_do_restore_post_hook_failure_raises(make_app, mocker):
    """Verify a failing post-restore hook fails the restore run."""
    # Given a restore that succeeds and a post-restore hook that exits non-zero
    app, config = make_app(post_restore_hook="exit 1")
    mocker.patch.object(app, "restore_backup", return_value=RestoreOutcome.RESTORED)

    # When running do_restore, then it raises
    with pytest.raises(HookFailedError):
        do_restore(app, config)


@pytest.mark.parametrize(
    ("hooks", "expected_levels"),
    [
        ({"pre_backup_hook": "echo hi"}, ["INFO"]),
        ({"pre_restore_hook": "echo hi"}, ["WARNING"]),
        ({}, []),
    ],
    ids=["active-hook", "action-mismatch", "no-hooks"],
)
def test_log_configured_hooks(make_app, hooks, expected_levels):
    """Verify a backup container announces an active hook and warns about a mismatched one."""
    # Given a backup container's config with the hooks under test
    _, config = make_app(ezbak_action="backup", **hooks)
    levels: list[str] = []
    logger.add(lambda message: levels.append(message.record["level"].name), level="INFO")

    # When logging the hook configuration at boot
    log_configured_hooks(config)

    # Then one line per hook is logged at the expected level, and none without hooks
    assert levels == expected_levels


def test_trigger_now_runs_the_scheduled_job_immediately(running_scheduler):
    """Verify a forced trigger runs the job without waiting for its cron time."""
    # Given a running scheduler holding a job that will not fire on its own for hours
    executed = threading.Event()

    def on_executed(_event: JobEvent) -> None:
        executed.set()

    running_scheduler.add_listener(on_executed, EVENT_JOB_EXECUTED)
    running_scheduler.add_job(
        func=lambda: None, trigger=CronTrigger.from_crontab("0 3 * * *"), id="backup"
    )

    # When the job is triggered out of band
    _trigger_now(running_scheduler, Action.BACKUP)

    # Then it runs promptly
    assert executed.wait(5)


def test_trigger_now_leaves_the_cron_schedule_unchanged(running_scheduler):
    """Verify forcing a run does not shift the job's next scheduled run time."""
    # Given a running scheduler with a known next run time
    submitted = threading.Event()

    # The scheduler stores the recomputed next run time before it dispatches the
    # submission event, so the event marks the point the schedule is final
    def on_submitted(_event: JobEvent) -> None:
        submitted.set()

    running_scheduler.add_listener(on_submitted, EVENT_JOB_SUBMITTED)
    running_scheduler.add_job(
        func=lambda: None, trigger=CronTrigger.from_crontab("0 3 * * *"), id="backup"
    )
    original_next_run = running_scheduler.get_job(job_id="backup").next_run_time

    # When a run is forced
    _trigger_now(running_scheduler, Action.BACKUP)
    assert submitted.wait(5)

    # Then the cron trigger has recomputed the same next run
    assert running_scheduler.get_job(job_id="backup").next_run_time == original_next_run


def test_trigger_now_warns_when_no_job_is_scheduled(running_scheduler, log_messages):
    """Verify a trigger with no matching job warns instead of raising."""
    # When a trigger arrives at a scheduler with no backup job
    _trigger_now(running_scheduler, Action.BACKUP)

    # Then it is reported and does not raise
    assert log_messages
