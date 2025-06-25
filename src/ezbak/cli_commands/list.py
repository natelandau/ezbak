"""The list command for the EZBak CLI."""

from __future__ import annotations

from nclutils import logger

from ezbak import ezbak
from ezbak.constants import StorageType
from ezbak.models.settings import settings


def main() -> None:
    """The main function for the list command."""
    backup_manager = ezbak()
    backups = backup_manager.list_backups()

    if len(backups) == 0:
        logger.info("No backups found")
        return

    aws_backups = [x for x in backups if x.storage_type == StorageType.AWS]
    local_backups = [x for x in backups if x.storage_type == StorageType.LOCAL]

    if (
        aws_backups and settings.storage_location == StorageType.AWS
    ) or settings.storage_location == StorageType.ALL:
        print_backups = "\n  - ".join([x.name for x in aws_backups])
        logger.info(f"Found {len(aws_backups)} AWS backups\n  - {print_backups}")

    if local_backups and settings.storage_location == StorageType.LOCAL:
        print_backups = "\n  - ".join(
            [str(x.path) for x in sorted(local_backups, key=lambda x: x.path)]
        )
        logger.info(f"Found {len(local_backups)} local backups\n  - {print_backups}")
