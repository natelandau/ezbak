"""The list command for the EZBak CLI."""

from nclutils import logger

from ezbak.cli import EZBakCLI
from ezbak.cli_commands import get_app_for_cli
from ezbak.constants import StorageType


def main(cmd: EZBakCLI) -> None:
    """The main function for the list command."""
    app = get_app_for_cli(cmd)

    backups = app.list_backups()

    if len(backups) == 0:
        logger.info("No backups found")
        return

    aws_backups = [x for x in backups if x.storage_type == StorageType.AWS]
    local_backups = [x for x in backups if x.storage_type == StorageType.LOCAL]

    if (
        aws_backups and app.settings.storage_type == StorageType.AWS
    ) or app.settings.storage_type == StorageType.ALL:
        print_backups = "\n  - ".join([x.name for x in aws_backups])
        logger.info(f"Found {len(aws_backups)} AWS backups\n  - {print_backups}")

    if local_backups and app.settings.storage_type == StorageType.LOCAL:
        print_backups = "\n  - ".join(
            [str(x.path) for x in sorted(local_backups, key=lambda x: x.path)]
        )
        logger.info(f"Found {len(local_backups)} local backups\n  - {print_backups}")
