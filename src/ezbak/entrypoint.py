"""Entrypoint for ezbak from docker. Relies entirely on environment variables for configuration."""

import atexit
import socket
import sys
from dataclasses import dataclass

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from nclutils import logger

from ezbak import ezbak
from ezbak.models import settings


@dataclass
class Run:
    """Class to manage the running state of the application."""

    running: bool = True


def cleanup_tmp_dir() -> None:
    """Clean up the temporary directory to prevent disk space accumulation.

    Removes the temporary directory created during backup operations to free up disk space and maintain system cleanliness.
    """
    if settings.tmp_dir:
        settings.tmp_dir.cleanup()


def do_backup() -> None:
    """Create a backup of the service data directory and manage retention.

    Performs a complete backup operation including creating the backup, pruning old backups based on retention policy, and optionally renaming backup files for better organization.
    """
    backup_manager = ezbak()
    backup_manager.create_backup()
    backup_manager.prune_backups()
    if settings.rename_files:
        backup_manager.rename_backups()


def do_restore() -> None:
    """Restore a backup of the service data directory from the specified path.

    Restores data from a previously created backup to recover from data loss or system failures. Requires RESTORE_DIR environment variable to be set.
    """
    if not settings.restore_path:
        msg = "RESTORE_DIR is not set"
        logger.error(msg)
        sys.exit(1)

    backup_manager = ezbak()
    backup_manager.restore_backup()


def main() -> None:
    """Initialize and run the ezbak backup system with configuration validation.

    Sets up logging, validates configuration settings, and either runs a one-time backup/restore operation or starts a scheduled backup service based on cron configuration.
    """
    hostname = settings.hostname or socket.gethostname()
    logger.configure(
        log_level=settings.log_level,
        show_source_reference=False,
        log_file=str(settings.log_file) if settings.log_file else None,
    )
    logger.info(f"EZBak Running on '{hostname}' for '{settings.name}'")

    try:
        settings.validate()
    except (ValueError, FileNotFoundError):
        sys.exit(1)

    for key, value in settings.model_dump().items():
        if not key.startswith("_"):
            logger.debug(f"SETTING: {key}: {value}")

    retention_policy = settings.retention_policy.get_full_policy()
    logger.debug(f"SETTING: retention_policy: {retention_policy}")

    if settings.cron:
        scheduler = BackgroundScheduler()

        scheduler.add_job(
            func=do_backup if settings.action == "backup" else do_restore,
            trigger=CronTrigger.from_crontab(settings.cron),
            jitter=600,
        )
        scheduler.start()
        logger.info("Scheduler started")

        for job in scheduler.get_jobs():
            logger.info(job)
        while Run().running:  # Run the scheduler in a loop
            ...
    elif settings.action == "backup":
        do_backup()
    elif settings.action == "restore":
        do_restore()


if __name__ == "__main__":
    atexit.register(cleanup_tmp_dir)
    main()
