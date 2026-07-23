"""S3 storage backend for backup operations."""

from pathlib import Path

from boto3.exceptions import Boto3Error
from botocore.exceptions import BotoCoreError, ClientError
from loguru import logger

from ezbak.backup import Backup, StorageLocation
from ezbak.checksums import sidecar_name
from ezbak.config import BackupConfig
from ezbak.constants import StorageType
from ezbak.exceptions import StorageDeleteError, StorageReadError, StorageWriteError
from ezbak.naming import new_staging_filename
from ezbak.storage.aws import AWSService, is_missing_object_error
from ezbak.storage.base import StorageBackend

# The S3 DeleteObjects API accepts at most 1000 keys per request.
_S3_DELETE_BATCH_LIMIT = 1000

# Every failure an AWSService call can surface. Boto3Error is required alongside
# the botocore pair: the managed transfers convert errors (upload_file wraps a
# ClientError into S3UploadFailedError, download_file raises RetriesExceededError),
# and those subclass Boto3Error, not BotoCoreError.
_S3_ERRORS = (Boto3Error, BotoCoreError, ClientError)


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
        # Anchor on "{name}-" so a set does not swallow keys that merely share its
        # prefix (name "gitea" must not match "giteasave-*"). The naming grammar
        # always joins name and timestamp with "-".
        found_backups = self.aws_service.list_objects(prefix=f"{self.settings.name}-")

        # The prefix listing returns sidecars too; drop them so a .sha256 is never
        # parsed as a spurious Backup and counted against retention.
        found_backups = self._exclude_sidecars(found_backups)

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
        except _S3_ERRORS as e:
            msg = f"S3 upload failed for '{backup_name}': {e}"
            logger.error(msg)
            raise StorageWriteError(msg) from e
        logger.info(f"S3 created: {backup_name}")

        backup = Backup(
            storage_type=StorageType.AWS,
            name=backup_name,
            tz=self.settings.tz,
            storage_path=self.settings.aws_s3_bucket_prefix,
        )
        self._store_sidecar(backup=backup, checksum=checksum)
        return backup

    def _write_sidecar(self, *, backup: Backup, content: str) -> None:
        """Upload the .sha256 sidecar for an archive; a failure warns and is tolerated.

        Args:
            backup (Backup): The backup the sidecar belongs to.
            content (str): The sidecar object content.
        """
        try:
            self.aws_service.upload_content(content=content, name=sidecar_name(backup.name))
        except (OSError, *_S3_ERRORS) as e:
            logger.warning(f"Failed to write checksum sidecar for '{backup.name}': {e}")

    def delete(self, backup: Backup) -> bool:
        """Delete a single object from the bucket.

        Also removes the archive's .sha256 sidecar object, best-effort and
        idempotent for a backup with no sidecar.

        Args:
            backup (Backup): The backup whose object should be removed.

        Returns:
            bool: True once the delete request has been issued.

        Raises:
            StorageDeleteError: If the delete request fails.
        """
        try:
            self.aws_service.delete_object(key=backup.name)
        except _S3_ERRORS as e:
            msg = f"S3 delete failed for '{backup.name}': {e}"
            logger.error(msg)
            raise StorageDeleteError(msg) from e
        logger.info(f"Deleted from S3: {backup.name}")
        self._remove_sidecar(backup)
        return True

    def _remove_sidecar(self, backup: Backup) -> None:
        """Delete the sidecar object next to an archive; tolerate its absence.

        S3 DeleteObject is idempotent for absent keys, so a pre-feature backup
        with no sidecar is unaffected.

        Args:
            backup (Backup): The backup whose sidecar object should be removed.
        """
        try:
            self.aws_service.delete_object(key=sidecar_name(backup.name))
        except _S3_ERRORS as e:
            logger.warning(f"Failed to delete checksum sidecar for '{backup.name}': {e}")

    def delete_many(self, backups: list[Backup]) -> list[Backup]:
        """Batch-delete objects from the bucket.

        Also removes each archive's .sha256 sidecar object in the same
        batches, best-effort and idempotent for a backup with no sidecar.

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
        # Delete each archive's sidecar in the same batches. DeleteObjects is
        # idempotent for absent keys, so pre-feature backups are unaffected. Sidecar
        # keys are not mapped back to Backups: only archives count as confirmed deleted.
        sidecar_keys = [self.aws_service.build_full_key(sidecar_name(x.name)) for x in backups]
        keys = list(backup_by_key) + sidecar_keys
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
        except _S3_ERRORS as e:
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
            StorageReadError: If downloading the object fails.
        """
        logger.info(f"Restoring backup from S3: {backup.name}")
        logger.trace(f"Downloading backup from S3 to tmp file: {backup.name}")
        tmp_file = self.tmp_dir / new_staging_filename()
        # Attempt the download directly and treat a missing key as "no backup",
        # rather than paying a HEAD round trip before every GET.
        try:
            self.aws_service.get_object(key=backup.name, destination=tmp_file)
        except _S3_ERRORS as e:
            # A failed transfer can leave a partial file behind; the caller never
            # receives the path on this branch, so reclaim it here.
            tmp_file.unlink(missing_ok=True)
            if isinstance(e, ClientError) and is_missing_object_error(e):
                logger.error(f"Backup file does not exist in S3: {backup.name}")
                return None
            msg = f"S3 download failed for '{backup.name}': {e}"
            logger.error(msg)
            raise StorageReadError(msg) from e
        return tmp_file

    def cleanup_restore_artifact(self, path: Path) -> None:  # ruff:ignore[no-self-use]
        """Delete the downloaded archive copy staged by `prepare_for_restore`.

        The staging dir only vanishes at process exit, so without this a
        scheduled restore leaks one archive-sized file per run.
        """
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"Failed to remove downloaded archive '{path}': {e}")

    def _read_sidecar(self, backup: Backup) -> str | None:
        """Download and return the raw sidecar object for a backup, if one exists.

        A transient S3 error is treated as "no usable checksum" (warn and proceed),
        consistent with verify-if-present, so a flaky network never masks the
        archive restore.

        Args:
            backup (Backup): The backup to look up.

        Returns:
            str | None: The sidecar content, or None if it is absent or unreadable.
        """
        sidecar = sidecar_name(backup.name)
        logger.trace(f"Looking up checksum sidecar object '{sidecar}'")
        # Read the tiny object straight into memory (no disk staging) and treat a
        # missing key as "no sidecar", rather than paying a HEAD round trip before
        # every GET. UnicodeDecodeError is caught alongside the storage errors: a
        # bit-rotted sidecar must warn and proceed, not crash the restore.
        try:
            return self.aws_service.get_object_content(key=sidecar)
        except (UnicodeDecodeError, *_S3_ERRORS) as e:
            if isinstance(e, ClientError) and is_missing_object_error(e):
                logger.trace(f"No checksum sidecar object '{sidecar}'")
            else:
                logger.warning(f"Could not read checksum sidecar for '{backup.name}': {e}")
            return None
