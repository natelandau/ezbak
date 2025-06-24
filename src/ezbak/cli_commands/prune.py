"""The prune command for the EZBak CLI."""

from __future__ import annotations

from nclutils import console, logger
from rich.prompt import Confirm

from ezbak import ezbak
from ezbak.models import settings


def main() -> None:
    """The main function for the prune command."""
    backup_manager = ezbak()
    policy = settings.retention_policy.get_full_policy()

    if not policy:
        logger.info("[bold]INFO[/bold]     | No retention policy configured. Skipping...")
        return

    policy_str = f"\n{' ' * 15}- ".join([f"{key}: {value}" for key, value in policy.items()])

    console.print("[bold]INFO[/bold]     | Retention Policy:")
    console.print(f"{' ' * 15}-", policy_str)

    if not Confirm.ask("Purge backups using the above policy?"):
        console.print("[bold]INFO[/bold]     | Aborting...")
        return

    deleted_files = backup_manager.prune_backups()
    if deleted_files:
        console.print(f"[bold]INFO[/bold]     | Deleted {len(deleted_files)} backups:")
        console.print(f"{' ' * 15}-", f"\n{' ' * 15}- ".join([x.name for x in deleted_files]))
    else:
        console.print("[bold]INFO[/bold]     | No backups deleted")
