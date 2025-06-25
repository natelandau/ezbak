"""The prune command for the EZBak CLI."""

from __future__ import annotations

from nclutils import logger
from rich.prompt import Confirm

from ezbak import ezbak
from ezbak.models import settings


def main() -> None:
    """The main function for the prune command."""
    backup_manager = ezbak()
    policy = settings.retention_policy.get_full_policy()

    if not policy:
        logger.info("No retention policy configured. Skipping...")
        return

    policy_str = "\n   - ".join([f"{key}: {value}" for key, value in policy.items()])

    logger.info(f"Retention Policy:\n   - {policy_str}")

    if not Confirm.ask("Purge backups using the above policy?"):
        logger.info("Aborting...")
        return

    deleted_files = backup_manager.prune_backups()
    if deleted_files:
        print_backups = "\n  - ".join([str(x.path) for x in deleted_files])
        logger.info(f"Deleted {len(deleted_files)} backups:\n   - {print_backups}")
    else:
        logger.info("No backups deleted")
