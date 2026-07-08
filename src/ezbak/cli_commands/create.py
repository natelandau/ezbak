"""The create command for the EZBak CLI."""

from __future__ import annotations

import cappa
from loguru import logger

from ezbak.cli import EZBakCLI, build_config
from ezbak.core import EZBak
from ezbak.exceptions import BackupFailedError


def main(cmd: EZBakCLI) -> None:
    """Create a backup and exit non-zero if any destination failed.

    Raises:
        cappa.Exit: If the backup fails for any configured destination.
    """
    app = EZBak(build_config(cmd))
    try:
        app.create_backup()
    except BackupFailedError as e:
        logger.error(e)
        raise cappa.Exit(code=1) from e
