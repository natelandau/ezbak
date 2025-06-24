"""The list command for the EZBak CLI."""

from __future__ import annotations

from collections import defaultdict

from nclutils import console

from ezbak import ezbak
from ezbak.constants import StorageType


def main() -> None:
    """The main function for the list command."""
    backup_manager = ezbak()
    backups = backup_manager.list_backups()

    if len(backups) == 0:
        console.print("No backups found")
        return

    if any(x.storage_type == StorageType.AWS for x in backups):
        console.rule("AWS Backups")
        for backup in backups:
            console.print(backup.name)

    if any(x.storage_type == StorageType.LOCAL for x in backups):
        backup_by_path = defaultdict(list)
        for backup in backups:
            backup_by_path[backup.path].append(backup.name)

        for path, names in sorted(backup_by_path.items()):
            console.rule(str(path))
            for name in names:
                console.print(name)
