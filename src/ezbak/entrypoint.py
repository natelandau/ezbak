"""Entrypoint for ezbak from docker. Relies entirely on environment variables for configuration."""

import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from nclutils import logger

from ezbak.app import EZBakApp
from ezbak.constants import Action, __version__
from ezbak.models import Settings


def do_backup(app: EZBakApp, scheduler: BackgroundScheduler | None = None) -> None:
    """Create a backup of the service data directory and manage retention.

    Performs a complete backup operation including creating the backup, pruning old backups based on retention policy, and optionally renaming backup files for better organization.
    """
    app.create_backup()
    app.prune_backups()

    if app.settings.rename_files:
        app.rename_backups()

    if scheduler:  # pragma: no cover
        job = scheduler.get_job(job_id="backup")
        if job and job.next_run_time:
            logger.info(f"Next scheduled run: {job.next_run_time}")


def do_restore(app: EZBakApp, scheduler: BackgroundScheduler | None = None) -> None:
    """Restore a backup of the service data directory from the specified path.

    Restores data from a previously created backup to recover from data loss or system failures. Requires RESTORE_DIR environment variable to be set.
    """
    app.restore_backup()

    if scheduler:  # pragma: no cover
        job = scheduler.get_job(job_id="restore")
        if job and job.next_run_time:
            logger.info(f"Next scheduled run: {job.next_run_time}")


def log_debug_info(app: EZBakApp) -> None:
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


def main() -> None:
    """Initialize and run the ezbak backup system with configuration validation.

    Sets up logging, validates configuration settings, and either runs a one-time backup/restore operation or starts a scheduled backup service based on cron configuration.
    """
    app = EZBakApp(config=Settings())

    log_debug_info(app)

    if app.settings.cron:
        scheduler = BackgroundScheduler()

        job = scheduler.add_job(
            func=do_backup if app.settings.entrypoint_action == Action.BACKUP else do_restore,
            args=[app, scheduler],
            trigger=CronTrigger.from_crontab(app.settings.cron),
            jitter=600,
            id=app.settings.entrypoint_action.value,
        )
        logger.info(job)
        scheduler.start()

        job = scheduler.get_job(job_id=app.settings.entrypoint_action.value)
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

    elif app.settings.entrypoint_action == Action.BACKUP:
        do_backup(app)
        time.sleep(1)
        logger.info("Backup complete. Exiting.")

    elif app.settings.entrypoint_action == Action.RESTORE:
        do_restore(app)
        time.sleep(1)
        logger.info("Restore complete. Exiting.")


if __name__ == "__main__":
    main()
