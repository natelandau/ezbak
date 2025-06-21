"""The list command for the EZBak CLI."""

from __future__ import annotations

from nclutils import console

from ezbak import ezbak
from ezbak.models import settings


def main() -> None:
    """The main function for the list command."""
    backup_manager = ezbak()
    backups = backup_manager.list_backups()

    if len(backups) == 0:
        console.print("No backups found")
        return

    for destination in settings.storage_paths:
        files = [str(x) for x in backups if x.parent == destination]
        if len(files) == 0:
            continue
        console.print("\n".join(files))
