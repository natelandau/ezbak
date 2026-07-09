"""The prune command for the EZBak CLI."""

from loguru import logger
from rich.prompt import Confirm

from ezbak.cli import EZBakCLI, PruneCommand, build_config
from ezbak.core import EZBak


def main(cmd: EZBakCLI) -> None:
    """The main function for the prune command."""
    app = EZBak(build_config(cmd))
    policy = app.settings.retention_policy

    if not policy.is_active:
        logger.info("No retention policy configured. Skipping...")
        return

    summary = policy.summary()
    policy_str = "\n   - ".join([f"{key}: {value}" for key, value in summary.items()])

    logger.info(f"Retention Policy:\n   - {policy_str}")

    dry_run = isinstance(cmd.command, PruneCommand) and cmd.command.dry_run

    # A dry run makes no destructive change, so skip the confirmation prompt.
    if not dry_run and not Confirm.ask("Purge backups using the above policy?"):
        logger.info("Aborting...")
        return

    deleted_files = app.prune_backups(dry_run=dry_run)
    verb = "Would delete" if dry_run else "Deleted"
    if deleted_files:
        # S3 backups have no local path; fall back to the object name so the output
        # names the key instead of printing "None".
        print_backups = "\n  - ".join([str(x.path) if x.path else x.name for x in deleted_files])
        logger.info(f"{verb} {len(deleted_files)} backups:\n   - {print_backups}")
    else:
        logger.info("No backups would be deleted" if dry_run else "No backups deleted")
