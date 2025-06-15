"""The list command for the EZBak CLI."""

from __future__ import annotations

from nclutils import console

from ezbak.cli import EZBakCLI  # noqa: TC001


def main(command: EZBakCLI) -> None:
    """The main function for the list command."""
    backups = command.backup_manager.list_backups()

    if len(backups) == 0:
        console.print("No backups found")
        return

    for destination in command.backup_manager.destinations:
        files = [str(x) for x in backups if x.parent == destination]
        if len(files) == 0:
            continue
        console.print("\n".join(files))
