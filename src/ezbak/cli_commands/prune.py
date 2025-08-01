"""The prune command for the EZBak CLI."""

from nclutils import logger
from rich.prompt import Confirm

from ezbak.cli import EZBakCLI
from ezbak.cli_commands import get_app_for_cli


def main(cmd: EZBakCLI) -> None:
    """The main function for the prune command."""
    app = get_app_for_cli(cmd)
    policy = app.settings.retention_policy.get_full_policy()

    if not policy:
        logger.info("No retention policy configured. Skipping...")
        return

    policy_str = "\n   - ".join([f"{key}: {value}" for key, value in policy.items()])

    logger.info(f"Retention Policy:\n   - {policy_str}")

    if not Confirm.ask("Purge backups using the above policy?"):
        logger.info("Aborting...")
        return

    deleted_files = app.prune_backups()
    if deleted_files:
        print_backups = "\n  - ".join([str(x.path) for x in deleted_files])
        logger.info(f"Deleted {len(deleted_files)} backups:\n   - {print_backups}")
    else:
        logger.info("No backups deleted")
