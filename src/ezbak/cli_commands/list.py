"""The list command for the EZBak CLI."""

from loguru import logger

from ezbak.cli import EZBakCLI, build_config
from ezbak.constants import StorageType
from ezbak.core import EZBak


def main(cmd: EZBakCLI) -> None:
    """The main function for the list command."""
    app = EZBak(build_config(cmd))

    backups = app.list_backups()

    if not backups:
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
