"""The list command for the EZBak CLI."""

import cappa
from loguru import logger

from ezbak.cli import EZBakCLI, build_config
from ezbak.constants import StorageType
from ezbak.core import EZBak


def main(cmd: EZBakCLI) -> None:
    """List every backup found, exiting non-zero if a destination could not be read.

    Raises:
        cappa.Exit: If any configured storage location could not be enumerated.
    """
    app = EZBak(build_config(cmd))

    backups = app.list_backups()
    unreadable = app.unreadable_locations

    if not backups and not unreadable:
        logger.info("No backups found")
        return

    aws_backups = [x for x in backups if x.storage_type == StorageType.AWS]
    local_backups = [x for x in backups if x.storage_type == StorageType.LOCAL]

    if aws_backups:
        print_backups = "\n  - ".join([x.name for x in aws_backups])
        logger.info(f"Found {len(aws_backups)} AWS backups\n  - {print_backups}")

    if local_backups:
        print_backups = "\n  - ".join(
            [str(x.path) for x in sorted(local_backups, key=lambda x: x.path)]
        )
        logger.info(f"Found {len(local_backups)} local backups\n  - {print_backups}")

    if unreadable:
        logger.error(
            f"Could not read {', '.join(unreadable)}. This list is incomplete; "
            "backups may exist that are not shown."
        )
        raise cappa.Exit(code=1)
