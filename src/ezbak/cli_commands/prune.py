"""The prune command for the EZBak CLI."""

from __future__ import annotations

from nclutils import console
from rich.prompt import Confirm

from ezbak import ezbak


def main() -> None:
    """The main function for the prune command."""
    backup_manager = ezbak()
    policy = backup_manager.retention_policy.get_full_policy()
    policy_str = "\n - ".join([f"{key}: {value}" for key, value in policy.items()])

    console.print("[bold]Retention Policy[/bold]")
    console.print(" -", policy_str)

    if not Confirm.ask("Purge backups using the above policy?"):
        console.print("Aborting...")
        return

    deleted_files = backup_manager.prune_backups()
    if deleted_files:
        console.print("-", "\n - ".join([x.name for x in deleted_files]))
    else:
        console.print("No backups deleted")
