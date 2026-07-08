"""The CLI command for restoring a backup."""

import cappa
from loguru import logger

from ezbak.cli import EZBakCLI, build_config
from ezbak.core import EZBak
from ezbak.exceptions import EZBakError


def main(cmd: EZBakCLI) -> None:
    """Restores the latest backup to the destination path.

    Raises:
        cappa.Exit: If the restore fails or no backup is found.
    """
    app = EZBak(build_config(cmd))
    try:
        if not app.restore_backup():
            raise cappa.Exit(code=1)
    except EZBakError as e:
        logger.error(e)
        raise cappa.Exit(code=1) from e
