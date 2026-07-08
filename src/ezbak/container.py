"""Entrypoint for ezbak from docker. Relies entirely on environment variables for configuration."""

import sys
import time
import urllib.request
from collections.abc import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from pydantic import ValidationError

from ezbak.constants import Action, __version__
from ezbak.core import EZBak
from ezbak.env import EnvConfig
from ezbak.exceptions import EZBakError, RestoreFailedError
from ezbak.logging import log_validation_errors


def do_backup(app: EZBak, scheduler: BackgroundScheduler | None = None) -> None:
    """Create a backup of the service data directory and manage retention.

    Performs a complete backup operation including creating the backup and pruning old backups based on retention policy.
    """
    try:
        app.create_backup()
    finally:
        # Prune even when a destination failed so retention keeps running and the
        # backups that did succeed do not accumulate unbounded on repeated cron runs.
        app.prune_backups()

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


def do_restore(app: EZBak, scheduler: BackgroundScheduler | None = None) -> None:
    """Restore a backup of the service data directory from the specified path.

    Restores data from a previously created backup to recover from data loss or system failures. Requires RESTORE_DIR environment variable to be set.

    Raises:
        RestoreFailedError: No backup matched the restore criteria.
    """
    if not app.restore_backup():
        # restore_backup() returns False (rather than raising) when no backup matches,
        # so raise here to keep a failed restore from looking like a success.
        msg = "Restore failed: no backup matched the restore criteria"
        raise RestoreFailedError(msg)

    if scheduler:  # pragma: no cover
        job = scheduler.get_job(job_id="restore")
        if job and job.next_run_time:
            logger.info(f"Next scheduled run: {job.next_run_time}")


def _run_scheduled(
    app: EZBak,
    scheduler: BackgroundScheduler,
    healthcheck_url: str | None,
    run: Callable[[EZBak, BackgroundScheduler], None],
) -> None:
    """Run a scheduled backup or restore, signaling the outcome without stopping the scheduler.

    APScheduler routes a job exception through the stdlib logging system, which bypasses
    the loguru sink this app configures, so catch it here and log it via loguru instead,
    then ping the healthcheck monitor with the run's success or failure.
    """
    try:
        run(app, scheduler)
    except EZBakError as e:
        logger.error(e)
        _ping_healthcheck(healthcheck_url, failed=True)
    else:
        _ping_healthcheck(healthcheck_url, failed=False)


def log_debug_info(app: EZBak) -> None:
    """Log debug information about the configuration."""
    logger.debug(f"ezbak v{__version__}")

    for key, value in sorted(app.settings.model_dump().items()):
        if not key.startswith("_") and value is not None:
            if key.endswith("_key"):
                logger.debug(f"env: {key}: **********")
            else:
                logger.debug(f"env: {key}: {value}")
    retention_policy = app.settings.retention_policy.get_full_policy()
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

    log_debug_info(app)

    if config.entrypoint_action is None:
        logger.error("No action configured: set EZBAK_ACTION to 'backup' or 'restore'")
        sys.exit(1)

    if config.cron:
        scheduler = BackgroundScheduler()

        run = do_backup if config.entrypoint_action == Action.BACKUP else do_restore
        job = scheduler.add_job(
            func=_run_scheduled,
            args=[app, scheduler, config.healthcheck_url, run],
            trigger=CronTrigger.from_crontab(config.cron),
            jitter=600,
            id=config.entrypoint_action.value,
        )
        logger.info(job)
        scheduler.start()

        job = scheduler.get_job(job_id=config.entrypoint_action.value)
        if job and job.next_run_time:
            logger.info(f"Next scheduled run: {job.next_run_time}")
        else:
            logger.info("No next scheduled run")

        logger.info("Scheduler started")

        try:
            while scheduler.running:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Exiting...")
            scheduler.shutdown()

    elif config.entrypoint_action == Action.BACKUP:
        try:
            do_backup(app)
        except EZBakError as e:
            logger.error(e)
            sys.exit(1)
        time.sleep(1)
        logger.info("Backup complete. Exiting.")

    elif config.entrypoint_action == Action.RESTORE:
        try:
            do_restore(app)
        except EZBakError as e:
            logger.error(e)
            sys.exit(1)
        time.sleep(1)
        logger.info("Restore complete. Exiting.")


if __name__ == "__main__":
    main()
