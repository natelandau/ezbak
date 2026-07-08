"""The CLI command for restoring a backup."""

import cappa

from ezbak.cli import EZBakCLI, build_config
from ezbak.core import EZBak


def main(cmd: EZBakCLI) -> None:
    """Restores the latest backup to the destination path.

    Raises:
        cappa.Exit: If the restore fails.
    """
    app = EZBak(build_config(cmd))
    if not app.restore_backup():
        raise cappa.Exit(code=1)
