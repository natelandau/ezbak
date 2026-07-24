"""Entrypoint for ezbak from docker. Relies entirely on environment variables for configuration."""

import signal
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime
from types import FrameType

from apscheduler.events import (
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
    JobSubmissionEvent,
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from pydantic import ValidationError

from ezbak.constants import (
    MISFIRE_GRACE_PERIOD_SECONDS,
    Action,
    RestoreOutcome,
    __version__,
)
from ezbak.core import EZBak
from ezbak.env import EnvConfig
from ezbak.exceptions import EZBakError, HookFailedError, RestoreFailedError
from ezbak.hooks import run_hook
from ezbak.logging import log_validation_errors


def do_backup(app: EZBak, config: EnvConfig, scheduler: BackgroundScheduler | None = None) -> None:
    """Create a backup of the service data directory and manage retention.

    Run the pre-backup hook first: a non-zero hook means the source is not in a safe
    state to archive, so abort before creating anything. After a successful backup and
    prune, run the post-backup hook; the archive is already stored, so a failing
    post-backup hook fails the run loudly but keeps the backup.

    Raises:
        HookFailedError: A pre- or post-backup hook failed.
    """
    if not run_hook(config.pre_backup_hook, phase="pre-backup", timeout=config.hook_timeout):
        msg = "pre-backup hook failed; skipping backup"
        raise HookFailedError(msg)

    try:
        app.create_backup()
    finally:
        # Prune even when a destination failed so retention keeps running and the
        # backups that did succeed do not accumulate unbounded on repeated cron runs.
        app.prune_backups()

    # Reached only when create_backup did not raise, so post-backup cleanup never runs
    # on a failed or partial backup.
    if not run_hook(config.post_backup_hook, phase="post-backup", timeout=config.hook_timeout):
        msg = "post-backup hook failed"
        raise HookFailedError(msg)

    if scheduler:  # pragma: no cover
        job = scheduler.get_job(job_id="backup")
        if job and job.next_run_time:
            logger.info(f"Next scheduled run: {job.next_run_time}")


def _ping_healthcheck(url: str | None, *, failed: bool) -> None:
    """Signal an external monitor with the outcome of a scheduled run.

    A silently failed cron backup is a backup tool's worst failure mode, so ping a
    healthcheck monitor (Healthchecks.io convention: the base URL on success, the URL
    plus ``/fail`` on failure). Any ping error is swallowed so monitoring can never
    break or fail a backup run.
    """
    if not url:
        return

    # Strip a trailing slash so a failure ping is `base/fail`, not `base//fail` (which 404s).
    base_url = url.rstrip("/")
    ping_url = f"{base_url}/fail" if failed else base_url
    try:
        # Config-supplied URL; a monitoring ping must never fail the backup it reports on.
        with urllib.request.urlopen(ping_url, timeout=10):  # ruff:ignore[suspicious-url-open-usage]
            pass
    except (OSError, ValueError) as e:
        logger.warning(f"Healthcheck ping failed: {e}")


def do_restore(app: EZBak, config: EnvConfig, scheduler: BackgroundScheduler | None = None) -> None:
    """Restore a backup of the service data directory from the specified path.

    Run the pre-restore hook first; a non-zero hook aborts before restoring. Run the
    post-restore hook only when a restore actually wrote files, so a no-op (no backup
    matched, or the target was already populated) skips it.

    Raises:
        HookFailedError: A pre- or post-restore hook failed.
        RestoreFailedError: No backup matched the restore criteria and skip_if_no_backup is not set.
    """
    if not run_hook(config.pre_restore_hook, phase="pre-restore", timeout=config.hook_timeout):
        msg = "pre-restore hook failed; skipping restore"
        raise HookFailedError(msg)

    outcome = app.restore_backup()

    if outcome is RestoreOutcome.NO_BACKUP:
        # A missing backup is a clean no-op only with skip_if_no_backup (a fresh
        # deployment); otherwise a failed restore must not look like a success.
        if app.settings.skip_if_no_backup:
            logger.info("No backup matched and skip_if_no_backup is set; exiting without error")
            return
        msg = "Restore failed: no backup matched the restore criteria"
        raise RestoreFailedError(msg)

    if outcome is RestoreOutcome.SKIPPED_POPULATED:
        # Target already holds data; nothing was written, so the post-restore hook
        # (which preps freshly restored files) must not run.
        logger.info("Restore target already contains data; skipping restore and post-restore hook")
        return

    # Reached only after an actual restore: both no-op paths above (no backup
    # matched, or the target was already populated) have already returned.
    if not run_hook(config.post_restore_hook, phase="post-restore", timeout=config.hook_timeout):
        msg = "post-restore hook failed"
        raise HookFailedError(msg)

    if scheduler:  # pragma: no cover
        job = scheduler.get_job(job_id="restore")
        if job and job.next_run_time:
            logger.info(f"Next scheduled run: {job.next_run_time}")


def _run_and_report(
    app: EZBak,
    scheduler: BackgroundScheduler,
    config: EnvConfig,
    run: Callable[[EZBak, EnvConfig, BackgroundScheduler], None],
) -> None:
    """Run a backup or restore, signaling the outcome without stopping the scheduler.

    APScheduler routes a job exception through the stdlib logging system, which bypasses
    the loguru sink this app configures, so catch it here and log it via loguru instead,
    then ping the healthcheck monitor with the run's success or failure.

    Args:
        app (EZBak): The configured backup manager.
        scheduler (BackgroundScheduler): The scheduler owning the run.
        config (EnvConfig): The container configuration.
        run (Callable): The action to perform, `do_backup` or `do_restore`.
    """
    try:
        run(app, config, scheduler)
    except EZBakError as e:
        logger.error(e)
        _ping_healthcheck(config.healthcheck_url, failed=True)
    else:
        _ping_healthcheck(config.healthcheck_url, failed=False)


def _run_scheduled(
    app: EZBak,
    scheduler: BackgroundScheduler,
    config: EnvConfig,
    run: Callable[[EZBak, EnvConfig, BackgroundScheduler], None],
    run_lock: threading.Lock,
) -> None:
    """Run a scheduled backup or restore as the container's single in-flight run.

    Hold `run_lock` for the whole run so the shutdown backup, which runs on the main
    thread and therefore outside the scheduler's `max_instances` accounting, can tell
    that a run is already in flight.

    Args:
        app (EZBak): The configured backup manager.
        scheduler (BackgroundScheduler): The scheduler owning the job.
        config (EnvConfig): The container configuration.
        run (Callable): The action to perform, `do_backup` or `do_restore`.
        run_lock (threading.Lock): Guard held for the duration of the run.
    """
    # Non-blocking: the only competing holder is the shutdown backup, so a job the
    # executor accepted just before shutdown must be dropped rather than queued behind
    # the final backup, where it would duplicate it and hold the process open past the
    # orchestrator's kill grace period. max_instances already keeps two scheduled runs
    # from reaching this point.
    if not run_lock.acquire(blocking=False):
        logger.warning("Another run is already in progress; skipping this run")
        return

    try:
        _run_and_report(app, scheduler, config, run)
    finally:
        run_lock.release()


def _trigger_now(scheduler: BackgroundScheduler, action: Action) -> None:
    """Move the scheduled job's next run to now so an operator can force a run.

    Reschedule the existing job rather than running the action directly: a forced run
    then takes the same path as a cron tick (hooks, every destination, retention
    pruning, healthcheck ping) with no second code path to keep in sync, and inherits
    the job's overlap protection. The cron trigger recomputes the next run afterward,
    so forcing a run does not shift the schedule. Jitter does not apply, since it is
    computed by the trigger and an explicit next run time bypasses it.

    Args:
        scheduler (BackgroundScheduler): The running scheduler holding the job.
        action (Action): The action whose job should run now.
    """
    job = scheduler.get_job(job_id=action.value)
    if not job:
        logger.warning("Trigger received but no job is scheduled; ignoring")
        return

    logger.info(f"Trigger received; running {action.value} now")
    job.modify(next_run_time=datetime.now(tz=scheduler.timezone))


def _run_shutdown_backup(
    app: EZBak, scheduler: BackgroundScheduler, config: EnvConfig, run_lock: threading.Lock
) -> None:
    """Take one final backup on shutdown when a cron BACKUP container opted in.

    A no-op unless ``backup_on_shutdown`` is set and the container's action is
    ``backup``: a final backup only makes sense for a backup sidecar, not for a
    restore container. Also a no-op while another run holds ``run_lock``: two runs
    against one ``EZBak`` share its staging directory and its cached storage locations,
    and a same-second finish collides on the destination filename, so the final backup
    is skipped in favor of the run already in flight, which covers the same data.
    Report through ``_run_and_report`` so the final backup gets the same error handling
    and healthcheck ping as any scheduled run.

    Args:
        app (EZBak): The configured backup manager.
        scheduler (BackgroundScheduler): The scheduler being shut down.
        config (EnvConfig): The container configuration.
        run_lock (threading.Lock): Guard a scheduled or forced run holds while running.
    """
    if not config.backup_on_shutdown or config.entrypoint_action != Action.BACKUP:
        return

    # Non-blocking: waiting for the in-flight run would push shutdown past the
    # orchestrator's kill grace period, and the final backup is redundant anyway.
    if not run_lock.acquire(blocking=False):
        logger.info("A backup is already running; skipping the final backup")
        return

    try:
        logger.info("Taking a final backup before shutdown")
        _run_and_report(app, scheduler, config, do_backup)
    finally:
        run_lock.release()


def _log_skipped_run(event: JobExecutionEvent | JobSubmissionEvent) -> None:
    """Report a run the scheduler declined to execute.

    APScheduler reports these through stdlib logging, which bypasses the loguru sink
    this app configures, so a skipped run would otherwise leave no trace at all. Only
    `code` and `job_id` are read: the two event types expose different run-time
    attributes.

    Args:
        event (JobExecutionEvent | JobSubmissionEvent): The skip event from APScheduler.
    """
    if event.code == EVENT_JOB_MAX_INSTANCES:
        logger.warning(f"Skipped {event.job_id}: a run is already in progress")
    else:
        logger.warning(f"Skipped {event.job_id}: missed by more than the grace period")


def _run_cron(app: EZBak, config: EnvConfig, action: Action) -> None:
    """Run the configured action on a cron schedule until a shutdown signal arrives.

    Build the scheduler, register `SIGTERM`/`SIGINT` handlers for a clean shutdown and a
    `SIGUSR1` handler that lets an operator force an immediate run, and block until a
    shutdown signal arrives. On an opted-in backup container, take one final backup
    before stopping (see `_run_shutdown_backup` for the gating).

    Args:
        app (EZBak): The configured backup manager.
        config (EnvConfig): The container configuration.
        action (Action): The resolved entrypoint action to schedule.
    """
    scheduler = BackgroundScheduler()
    scheduler.add_listener(_log_skipped_run, EVENT_JOB_MAX_INSTANCES | EVENT_JOB_MISSED)

    # Serializes every run of this container: the scheduler's own max_instances covers
    # scheduled and forced runs, but not the shutdown backup, which runs on the main
    # thread without going through the scheduler.
    run_lock = threading.Lock()

    run = do_backup if action == Action.BACKUP else do_restore
    job = scheduler.add_job(
        func=_run_scheduled,
        args=[app, scheduler, config, run, run_lock],
        trigger=CronTrigger.from_crontab(config.cron),
        jitter=config.cron_jitter,
        misfire_grace_time=MISFIRE_GRACE_PERIOD_SECONDS,
        id=action.value,
    )
    logger.info(job)

    # An orchestrator tears the container down with SIGTERM, which Python does not
    # convert to an exception, so without a handler the scheduler thread is killed
    # abruptly. Handle both signals to shut down cleanly (and, when opted in, take a
    # final backup). The handler only flags the request; the backup runs back in the
    # main control flow below, never inside the handler, where a full tar/S3 upload
    # would be unsafe.
    shutdown_event = threading.Event()

    def _request_shutdown(_signum: int, _frame: FrameType | None) -> None:
        # Restore the default handlers so a second signal (operator escalation, or the
        # orchestrator's SIGKILL precursor) terminates at once instead of being swallowed
        # while a slow final backup runs. Args are unused but required by signal.signal's
        # handler signature.
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    # A forced run must not happen inside the handler, where a full tar/S3 upload would
    # be unsafe; the handler only flags the request and the main loop below acts on it.
    # Unlike shutdown, the default disposition is not restored: repeated forced runs
    # are legitimate, whereas a second shutdown signal must be free to terminate at once.
    run_now_event = threading.Event()

    def _request_run_now(_signum: int, _frame: FrameType | None) -> None:
        run_now_event.set()

    signal.signal(signal.SIGUSR1, _request_run_now)

    scheduler.start()

    job = scheduler.get_job(job_id=action.value)
    if job and job.next_run_time:
        logger.info(f"Next scheduled run: {job.next_run_time}")
    else:
        logger.info("No next scheduled run")

    logger.info("Scheduler started")

    while scheduler.running and not shutdown_event.is_set():
        if run_now_event.is_set():
            # Clear before triggering so a signal arriving mid-trigger is not lost.
            run_now_event.clear()
            _trigger_now(scheduler, action)
        time.sleep(1)

    if run_now_event.is_set():
        logger.info("Shutting down before the pending trigger ran; it will not run")

    logger.info("Exiting...")
    # Stop submitting new scheduled jobs. Pausing does not wait for a job already
    # running (run_lock covers that); it only keeps a cron tick from starting another.
    # Pausing rather than shutting down keeps the scheduler alive so the final backup's
    # job lookup still works.
    if scheduler.running:
        scheduler.pause()
    try:
        # Only an actual signal is a shutdown request; a loop that ended because the
        # scheduler stopped on its own must not trigger a surprise final backup.
        if shutdown_event.is_set():
            _run_shutdown_backup(app, scheduler, config, run_lock)
    finally:
        # Always stop the scheduler, even if the final backup raised. wait=False: do not
        # block on an in-flight job, so cleanup cannot overrun the kill grace period.
        if scheduler.running:
            scheduler.shutdown(wait=False)


def log_configured_hooks(config: EnvConfig) -> None:
    """Announce configured lifecycle hooks at boot so an operator can confirm they are live.

    Which hooks are active materially changes a run (a failing pre-backup hook aborts the
    backup), so surface it at INFO in default logs rather than burying it in the debug
    config dump. Warn about a hook configured for the action this container is not running:
    it will never fire, which is almost always the cause of a "my hook never ran" report.

    Args:
        config (EnvConfig): The container configuration holding the hook fields.
    """
    hooks = (
        ("pre-backup", config.pre_backup_hook, Action.BACKUP),
        ("post-backup", config.post_backup_hook, Action.BACKUP),
        ("pre-restore", config.pre_restore_hook, Action.RESTORE),
        ("post-restore", config.post_restore_hook, Action.RESTORE),
    )
    timeout_desc = f"{config.hook_timeout}s timeout" if config.hook_timeout else "no timeout"
    for phase, command, action in hooks:
        if not command or not command.strip():
            continue
        if config.entrypoint_action is not None and config.entrypoint_action != action:
            logger.warning(
                f"{phase} hook is configured but EZBAK_ACTION is "
                f"'{config.entrypoint_action.value}', so it will never run"
            )
        else:
            logger.info(f"{phase} hook configured ({timeout_desc}): {command}")


def log_debug_info(app: EZBak) -> None:
    """Log debug information about the configuration."""
    for key, value in sorted(app.settings.model_dump().items()):
        if not key.startswith("_") and value is not None:
            if key.endswith("_key"):
                logger.debug(f"env: {key}: **********")
            else:
                logger.debug(f"env: {key}: {value}")
    retention_policy = app.settings.retention_policy.summary()
    logger.trace(f"retention_policy: {retention_policy}")


def _load_config() -> EnvConfig:
    """Load the container config from the environment, exiting cleanly on a bad config.

    A misconfigured container (missing name or storage) must log a clean message and
    exit non-zero, not dump a raw pydantic traceback to the container logs.

    Returns:
        EnvConfig: The validated container configuration.
    """
    try:
        return EnvConfig()
    except ValidationError as e:
        log_validation_errors(e)
        sys.exit(1)


def main() -> None:
    """Initialize and run the ezbak backup system with configuration validation.

    Sets up logging, validates configuration settings, and either runs a one-time backup/restore operation or starts a scheduled backup service based on cron configuration.
    """
    # SIGUSR1 forces a run in a cron container, but its default disposition terminates
    # the process, which would kill a one-shot run mid-archive and leave the staging
    # directory behind. Neutralize it first thing so it is inert everywhere; `_run_cron`
    # replaces this with the handler that acts on it. A no-op handler rather than
    # SIG_IGN: an ignored disposition survives exec and would silently disable SIGUSR1
    # inside every hook subprocess, while a handled one resets to default on exec.
    signal.signal(signal.SIGUSR1, lambda _signum, _frame: None)

    # Hold the EnvConfig directly: container-only settings (entrypoint_action,
    # healthcheck_url) live here, not on the library-facing BackupConfig that app.settings exposes.
    config = _load_config()
    app = EZBak(config)

    logger.info(f"ezbak v{__version__}")
    log_debug_info(app)
    log_configured_hooks(config)

    if config.entrypoint_action is None:
        logger.error("No action configured: set EZBAK_ACTION to 'backup' or 'restore'")
        sys.exit(1)

    if config.cron:
        _run_cron(app, config, config.entrypoint_action)

    elif config.entrypoint_action == Action.BACKUP:
        try:
            do_backup(app, config)
        except EZBakError as e:
            logger.error(e)
            sys.exit(1)
        logger.info("Backup complete. Exiting.")

    elif config.entrypoint_action == Action.RESTORE:
        try:
            do_restore(app, config)
        except EZBakError as e:
            logger.error(e)
            sys.exit(1)
        logger.info("Restore complete. Exiting.")


if __name__ == "__main__":
    main()
