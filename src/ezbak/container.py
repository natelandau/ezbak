"""Entrypoint for ezbak from docker. Relies entirely on environment variables for configuration."""

import signal
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from types import FrameType

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from pydantic import ValidationError

from ezbak.constants import Action, __version__
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
        with urllib.request.urlopen(ping_url, timeout=10):  # noqa: S310
            pass
    except (OSError, ValueError) as e:
        logger.warning(f"Healthcheck ping failed: {e}")


def do_restore(app: EZBak, config: EnvConfig, scheduler: BackgroundScheduler | None = None) -> None:
    """Restore a backup of the service data directory from the specified path.

    Run the pre-restore hook first; a non-zero hook aborts before restoring. Run the
    post-restore hook only when a restore actually happened, so a restore_if_exists
    no-op (no backup matched) skips it.

    Raises:
        HookFailedError: A pre- or post-restore hook failed.
        RestoreFailedError: No backup matched the restore criteria and restore_if_exists is not set.
    """
    if not run_hook(config.pre_restore_hook, phase="pre-restore", timeout=config.hook_timeout):
        msg = "pre-restore hook failed; skipping restore"
        raise HookFailedError(msg)

    if not app.restore_backup():
        # restore_backup() returns False (rather than raising) only when no backup
        # matches; a real download or extract error raises RestoreFailedError from within.
        # With restore_if_exists, a missing backup is a clean no-op (a fresh deployment).
        if app.settings.restore_if_exists:
            logger.info("No backup matched and restore_if_exists is set; exiting without error")
            return
        # Raise here to keep a failed restore from looking like a success.
        msg = "Restore failed: no backup matched the restore criteria"
        raise RestoreFailedError(msg)

    # Reached only after an actual restore, so the post-restore hook is skipped on a
    # restore_if_exists no-op above.
    if not run_hook(config.post_restore_hook, phase="post-restore", timeout=config.hook_timeout):
        msg = "post-restore hook failed"
        raise HookFailedError(msg)

    if scheduler:  # pragma: no cover
        job = scheduler.get_job(job_id="restore")
        if job and job.next_run_time:
            logger.info(f"Next scheduled run: {job.next_run_time}")


def _run_scheduled(
    app: EZBak,
    scheduler: BackgroundScheduler,
    config: EnvConfig,
    run: Callable[[EZBak, EnvConfig, BackgroundScheduler], None],
) -> None:
    """Run a scheduled backup or restore, signaling the outcome without stopping the scheduler.

    APScheduler routes a job exception through the stdlib logging system, which bypasses
    the loguru sink this app configures, so catch it here and log it via loguru instead,
    then ping the healthcheck monitor with the run's success or failure.
    """
    try:
        run(app, config, scheduler)
    except EZBakError as e:
        logger.error(e)
        _ping_healthcheck(config.healthcheck_url, failed=True)
    else:
        _ping_healthcheck(config.healthcheck_url, failed=False)


def _run_shutdown_backup(app: EZBak, scheduler: BackgroundScheduler, config: EnvConfig) -> None:
    """Take one final backup on shutdown when a cron BACKUP container opted in.

    A no-op unless ``backup_on_shutdown`` is set and the container's action is
    ``backup``: a final backup only makes sense for a backup sidecar, not for a
    restore container. Route through ``_run_scheduled`` so the final backup gets the
    same error handling and healthcheck ping as any scheduled run.

    Args:
        app (EZBak): The configured backup manager.
        scheduler (BackgroundScheduler): The scheduler being shut down.
        config (EnvConfig): The container configuration.
    """
    if not config.backup_on_shutdown or config.entrypoint_action != Action.BACKUP:
        return

    logger.info("Taking a final backup before shutdown")
    _run_scheduled(app, scheduler, config, do_backup)


def _run_cron(app: EZBak, config: EnvConfig, action: Action) -> None:
    """Run the configured action on a cron schedule until a shutdown signal arrives.

    Build the scheduler, register `SIGTERM`/`SIGINT` handlers for a clean shutdown, and
    block until one arrives. On an opted-in backup container, take one final backup
    before stopping (see `_run_shutdown_backup` for the gating).

    Args:
        app (EZBak): The configured backup manager.
        config (EnvConfig): The container configuration.
        action (Action): The resolved entrypoint action to schedule.
    """
    scheduler = BackgroundScheduler()

    run = do_backup if action == Action.BACKUP else do_restore
    job = scheduler.add_job(
        func=_run_scheduled,
        args=[app, scheduler, config, run],
        trigger=CronTrigger.from_crontab(config.cron),
        jitter=config.cron_jitter,
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

    scheduler.start()

    job = scheduler.get_job(job_id=action.value)
    if job and job.next_run_time:
        logger.info(f"Next scheduled run: {job.next_run_time}")
    else:
        logger.info("No next scheduled run")

    logger.info("Scheduler started")

    while scheduler.running and not shutdown_event.is_set():
        time.sleep(1)

    logger.info("Exiting...")
    # Stop firing new scheduled jobs so a cron tick cannot launch a second backup that
    # races the final one. Pausing (not shutting down) keeps the scheduler alive so the
    # final backup's job lookup still works.
    if scheduler.running:
        scheduler.pause()
    try:
        # Only an actual signal is a shutdown request; a loop that ended because the
        # scheduler stopped on its own must not trigger a surprise final backup.
        if shutdown_event.is_set():
            _run_shutdown_backup(app, scheduler, config)
    finally:
        # Always stop the scheduler, even if the final backup raised. wait=False: do not
        # block on an in-flight job, so cleanup cannot overrun the kill grace period.
        if scheduler.running:
            scheduler.shutdown(wait=False)


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
    # Hold the EnvConfig directly: container-only settings (entrypoint_action,
    # healthcheck_url) live here, not on the library-facing BackupConfig that app.settings exposes.
    config = _load_config()
    app = EZBak(config)

    logger.info(f"ezbak v{__version__}")
    log_debug_info(app)

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
        time.sleep(1)
        logger.info("Backup complete. Exiting.")

    elif config.entrypoint_action == Action.RESTORE:
        try:
            do_restore(app, config)
        except EZBakError as e:
            logger.error(e)
            sys.exit(1)
        time.sleep(1)
        logger.info("Restore complete. Exiting.")


if __name__ == "__main__":
    main()
