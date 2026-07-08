"""The CLI command for restoring a backup."""

import cappa
from loguru import logger

from ezbak.cli import EZBakCLI, build_config
from ezbak.core import EZBak
from ezbak.exceptions import EZBakError


def main(cmd: EZBakCLI) -> None:
    """Restores the latest backup to the restore path.

    Raises:
        cappa.Exit: If the restore fails or no backup is found.
    """
    app = EZBak(build_config(cmd))
    try:
        if not app.restore_backup():
            # restore_backup() returns False only when no backup matches; a real download
            # or extract error raises EZBakError. With --if-exists, a missing backup is a
            # clean no-op so a pre-start restore never blocks a first deployment.
            if app.settings.restore_if_exists:
                logger.info("No backup matched and --if-exists is set; nothing to restore")
                return
            raise cappa.Exit(code=1)
    except EZBakError as e:
        logger.error(e)
        raise cappa.Exit(code=1) from e
