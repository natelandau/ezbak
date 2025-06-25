"""MongoDB manager."""

import sys
from pathlib import Path

from nclutils import ShellCommandFailedError, ShellCommandNotFoundError, logger, run_command

from ezbak.models.settings import settings


class MongoManager:
    """MongoDB manager."""

    def __init__(self) -> None:
        """Initialize the MongoDB manager.

        Raises:
            ValueError: If the MongoDB URI or database name is not provided.
        """
        self.mongo_uri = settings.mongo_uri
        self.mongo_db_name = settings.mongo_db_name

        if not self.mongo_uri:
            msg = "MongoDB URI is required"
            logger.error(msg)
            raise ValueError(msg)
        if not self.mongo_db_name:
            msg = "MongoDB database name is required"
            logger.error(msg)
            raise ValueError(msg)

    def make_tmp_backup(self) -> Path:
        """Generate a backup of the MongoDB database in a temporary directory.

        Returns:
            Path: The path to the backup file.
        """
        backup_file = Path(settings.tmp_dir.name) / f"{self.mongo_db_name}.gz"
        logger.trace("Attempting to create tmp backup file")
        try:
            run_command(
                "mongodump",
                args=[
                    f"--uri={self.mongo_uri}/{self.mongo_db_name}",
                    f"--archive={backup_file}",
                    "--gzip",
                ],
            )
        except ShellCommandNotFoundError as e:
            logger.error(e)
            sys.exit(1)
        except ShellCommandFailedError as e:
            logger.error(e.stderr)
            sys.exit(1)
        logger.trace(f"Created tmp backup file: {backup_file}")
        return backup_file
