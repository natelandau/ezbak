"""Entrypoint for ezbak from docker. Relies entirely on environment variables for configuration."""

import sys
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from nclutils import logger

from ezbak import ezbak
from ezbak.constants import __version__
from ezbak.models import settings


def do_backup(scheduler: BackgroundScheduler | None = None) -> None:
    """Create a backup of the service data directory and manage retention.

    Performs a complete backup operation including creating the backup, pruning old backups based on retention policy, and optionally renaming backup files for better organization.
    """
    backup_manager = ezbak()

    backup_manager.create_backup()
    backup_manager.prune_backups()
    if settings.rename_files:
        backup_manager.rename_backups()

    if scheduler:  # pragma: no cover
        job = scheduler.get_job(job_id="backup")
        if job and job.next_run_time:
            logger.info(f"Next scheduled run: {job.next_run_time}")


def do_restore(scheduler: BackgroundScheduler | None = None) -> None:
    """Restore a backup of the service data directory from the specified path.

    Restores data from a previously created backup to recover from data loss or system failures. Requires RESTORE_DIR environment variable to be set.
    """
    backup_manager = ezbak()
    backup_manager.restore_backup()

    if scheduler:  # pragma: no cover
        job = scheduler.get_job(job_id="restore")
        if job and job.next_run_time:
            logger.info(f"Next scheduled run: {job.next_run_time}")


def log_debug_info() -> None:
    """Log debug information about the configuration."""
    logger.debug(f"ezbak v{__version__}")
    for key, value in settings.model_dump().items():
        if not key.startswith("_") and value is not None:
            logger.debug(f"Config: {key}: {value}")
    retention_policy = settings.retention_policy.get_full_policy()
    logger.debug(f"Config: retention_policy: {retention_policy}")


def main() -> None:
    """Initialize and run the ezbak backup system with configuration validation.

    Sets up logging, validates configuration settings, and either runs a one-time backup/restore operation or starts a scheduled backup service based on cron configuration.
    """
    logger.configure(
        log_level=settings.log_level.value,
        show_source_reference=False,
        log_file=str(settings.log_file) if settings.log_file else None,
        prefix=settings.log_prefix,
    )

    try:
        settings.validate()
    except (ValueError, FileNotFoundError):
        sys.exit(1)

    log_debug_info()

    if settings.cron:
        scheduler = BackgroundScheduler()

        job = scheduler.add_job(
            func=do_backup if settings.action == "backup" else do_restore,
            args=[scheduler],
            trigger=CronTrigger.from_crontab(settings.cron),
            jitter=600,
            id=settings.action,
        )
        logger.info(job)
        scheduler.start()

        job = scheduler.get_job(job_id=settings.action)
        if job and job.next_run_time:
            logger.info(f"Next scheduled run: {job.next_run_time}")
        else:
            logger.info("No next scheduled run")

        logger.info("Scheduler started")

        while scheduler.running:
            time.sleep(1)

    elif settings.action == "backup":
        do_backup()
        time.sleep(2)
        logger.info("Backup complete. Exiting.")
    elif settings.action == "restore":
        do_restore()
        time.sleep(2)
        logger.info("Restore complete. Exiting.")


if __name__ == "__main__":
    main()
