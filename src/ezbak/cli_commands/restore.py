"""The CLI command for restoring a backup."""

import cappa

from ezbak.cli import EZBakCLI
from ezbak.cli_commands import get_app_for_cli


def main(cmd: EZBakCLI) -> None:
    """Restores the latest backup to the destination path.

    Raises:
        cappa.Exit: If the restore fails.
    """
    app = get_app_for_cli(cmd)
    if not app.restore_backup():
        raise cappa.Exit(code=1)
