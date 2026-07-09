"""S3 storage backend for backup operations."""

from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from loguru import logger

from ezbak.backup import Backup, StorageLocation
from ezbak.checksums import format_sidecar, sidecar_name
from ezbak.config import BackupConfig
from ezbak.constants import StorageType
from ezbak.exceptions import StorageDeleteError, StorageReadError, StorageWriteError
from ezbak.naming import new_staging_filename
from ezbak.storage.aws import AWSService
from ezbak.storage.base import StorageBackend

# The S3 DeleteObjects API accepts at most 1000 keys per request.
_S3_DELETE_BATCH_LIMIT = 1000


class S3Backend(StorageBackend):
    """Back up to and manage archives in an S3 bucket."""

    storage_type = StorageType.AWS

    def __init__(self, settings: BackupConfig, *, aws_service: AWSService, tmp_dir: Path) -> None:
        """Store the S3 client and staging directory alongside the settings.

        Args:
            settings (BackupConfig): The validated backup configuration.
            aws_service (AWSService): The connected S3 client wrapper.
            tmp_dir (Path): The directory used to stage downloaded archives.
        """
        super().__init__(settings)
        self.aws_service = aws_service
        self.tmp_dir = tmp_dir

    def index(self) -> list[StorageLocation]:
        """List bucket objects matching the configured name into a single storage location.

        Returns:
            list[StorageLocation]: A single-element list with the S3 storage location.
        """
        logger.trace("Indexing S3 storage location")
        found_backups = self.aws_service.list_objects(prefix=self.settings.name)

        if self.settings.aws_s3_bucket_prefix:
            found_backups = [
                x.replace(f"{self.settings.aws_s3_bucket_prefix.rstrip('/')}/", "")
                for x in found_backups
            ]

        location = self._build_storage_location(
            storage_path=self.settings.aws_s3_bucket_prefix,
            backups=[
                Backup(
                    storage_type=StorageType.AWS,
                    name=x,
                    tz=self.settings.tz,
                    storage_path=self.settings.aws_s3_bucket_prefix,
                )
                for x in found_backups
            ],
        )
        logger.debug(f"Indexed {len(location.backups)} existing backups in S3 bucket")
        for backup in location.backups:
            logger.trace(f"Indexed: {backup}")

        return [location]

    def write(
        self, *, tmp_backup: Path, storage_location: StorageLocation, checksum: str | None
    ) -> Backup:
        """Upload the staged archive to the bucket.

        Args:
            tmp_backup (Path): The staged archive to upload.
            storage_location (StorageLocation): The naming context for the new object.
            checksum (str | None): Precomputed hex SHA-256 to store as a sidecar, or
                None to skip sidecar creation.

        Returns:
            Backup: The created backup.

        Raises:
            StorageWriteError: If the upload fails.
        """
        backup_name = storage_location.generate_new_backup_name()
        logger.debug(f"Upload tmp backup to S3: {backup_name}")
        try:
            self.aws_service.upload_object(file=tmp_backup, name=backup_name)
        except (BotoCoreError, ClientError) as e:
            msg = f"S3 upload failed for '{backup_name}': {e}"
            logger.error(msg)
            raise StorageWriteError(msg) from e
        logger.info(f"S3 created: {backup_name}")

        if checksum is not None:
            self._write_sidecar(archive_name=backup_name, checksum=checksum)

        return Backup(
            storage_type=StorageType.AWS,
            name=backup_name,
            tz=self.settings.tz,
            storage_path=self.settings.aws_s3_bucket_prefix,
        )

    def _write_sidecar(self, *, archive_name: str, checksum: str) -> None:
        """Upload the .sha256 sidecar for an archive; a failure warns and is tolerated.

        Stage the tiny sidecar in the backend's temp dir and reuse the same
        upload path as the archive, so the sidecar has no special S3 code path.

        Args:
            archive_name (str): The archive's final object name.
            checksum (str): The precomputed hex SHA-256 of the archive.
        """
        sidecar = sidecar_name(archive_name)
        tmp_sidecar = self.tmp_dir / sidecar
        try:
            tmp_sidecar.write_text(format_sidecar(checksum, archive_name))
            self.aws_service.upload_object(file=tmp_sidecar, name=sidecar)
        except (OSError, BotoCoreError, ClientError) as e:
            logger.warning(f"Failed to write checksum sidecar for '{archive_name}': {e}")
        finally:
            tmp_sidecar.unlink(missing_ok=True)

    def delete(self, backup: Backup) -> bool:
        """Delete a single object from the bucket.

        Args:
            backup (Backup): The backup whose object should be removed.

        Returns:
            bool: True once the delete request has been issued.

        Raises:
            StorageDeleteError: If the delete request fails.
        """
        try:
            self.aws_service.delete_object(key=backup.name)
        except (BotoCoreError, ClientError) as e:
            msg = f"S3 delete failed for '{backup.name}': {e}"
            logger.error(msg)
            raise StorageDeleteError(msg) from e
        logger.info(f"Deleted from S3: {backup.name}")
        return True

    def delete_many(self, backups: list[Backup]) -> list[Backup]:
        """Batch-delete objects from the bucket.

        Args:
            backups (list[Backup]): The backups to remove.

        Returns:
            list[Backup]: The backups the bucket confirmed deleted.

        Raises:
            StorageDeleteError: If the batch delete request fails.
        """
        if not backups:
            return []

        # Map each object's full S3 key back to its Backup so the confirmed-deleted
        # keys the API returns (which carry the bucket prefix) can be reported as the
        # Backup objects that were actually removed, not just the ones targeted.
        backup_by_key = {self.aws_service.build_full_key(x.name): x for x in backups}
        keys = list(backup_by_key)
        logger.debug(f"Deleting {len(keys)} S3 backups")
        deleted: list[Backup] = []
        try:
            # Chunk into requests of at most 1000 keys so a large prune does not exceed
            # the S3 DeleteObjects limit and abort the whole run.
            for start in range(0, len(keys), _S3_DELETE_BATCH_LIMIT):
                confirmed_keys = self.aws_service.delete_objects(
                    keys=keys[start : start + _S3_DELETE_BATCH_LIMIT]
                )
                for key in confirmed_keys:
                    backup = backup_by_key.get(key)
                    if backup is not None:
                        logger.info(f"Deleted from S3: {backup.name}")
                        deleted.append(backup)
        except (BotoCoreError, ClientError) as e:
            msg = f"S3 batch delete failed: {e}"
            logger.error(msg)
            raise StorageDeleteError(msg) from e
        return deleted

    def prepare_for_restore(self, backup: Backup) -> Path | None:
        """Download the backup object to a temporary file for extraction.

        Args:
            backup (Backup): The backup to restore.

        Returns:
            Path | None: The downloaded archive path, or None if the object is missing.

        Raises:
            StorageReadError: If checking existence or downloading the object fails.
        """
        logger.info(f"Restoring backup from S3: {backup.name}")
        try:
            if not self.aws_service.object_exists(backup.name):
                logger.error(f"Backup file does not exist in S3: {backup.name}")
                return None

            logger.trace(f"Downloading backup from S3 to tmp file: {backup.name}")
            tmp_file = self.tmp_dir / new_staging_filename()
            self.aws_service.get_object(key=backup.name, destination=tmp_file)
        except (BotoCoreError, ClientError) as e:
            msg = f"S3 download failed for '{backup.name}': {e}"
            logger.error(msg)
            raise StorageReadError(msg) from e
        return tmp_file
