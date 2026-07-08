"""The create command for the EZBak CLI."""

from __future__ import annotations

import cappa
from loguru import logger

from ezbak.cli import EZBakCLI, build_config
from ezbak.core import EZBak
from ezbak.exceptions import EZBakError


def main(cmd: EZBakCLI) -> None:
    """Create a backup and exit non-zero if any storage location failed.

    Raises:
        cappa.Exit: If the backup fails for any configured storage location or the config is invalid.
    """
    app = EZBak(build_config(cmd))
    try:
        app.create_backup()
    except EZBakError as e:
        logger.error(e)
        raise cappa.Exit(code=1) from e
