"""The CLI command for restoring a backup."""

from __future__ import annotations

import cappa

from ezbak import ezbak


def main() -> None:
    """Restores the latest backup to the destination path.

    Raises:
        cappa.Exit: If the restore fails.
    """
    backup_manager = ezbak()
    if not backup_manager.restore_backup():
        raise cappa.Exit(code=1)
