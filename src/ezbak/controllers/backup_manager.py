"""Backup management controller."""

import atexit
import re
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from nclutils import clean_directory, copy_file, find_files, logger, new_uid

from ezbak.constants import (
    BACKUP_EXTENSION,
    BACKUP_NAME_REGEX,
    RetentionPolicyType,
    StorageType,
)
from ezbak.models import Backup, StorageLocation, settings
from ezbak.utils import chown_files, cleanup_tmp_dir, should_include_file

from .aws import AWSService


@dataclass
class FileForRename:
    """Temporary class used for renaming backups."""

    backup: Backup
    new_name: str
    do_rename: bool = False


class BackupManager:
    """Manage and control backup operations for specified sources and storage_paths."""

    def __init__(self) -> None:
        """Initialize a backup manager to automate backup creation, management, and cleanup operations.

        Create a backup manager that handles the complete backup lifecycle including file selection, compression, storage across multiple storage_paths, and automated cleanup based on retention policies. Use this when you need reliable, automated backup management with flexible scheduling and retention controls.
        """
        self.aws_service = None
        self._storage_locations: list[StorageLocation] = []
        self.rebuild_storage_locations = False
        self.tmp_dir = Path(settings.tmp_dir.name)

        if settings.storage_location in {StorageType.AWS, StorageType.ALL}:
            try:
                self.aws_service = AWSService()
            except ValueError as e:
                logger.error(e)

        atexit.register(cleanup_tmp_dir)

    @property
    def storage_locations(self) -> list[StorageLocation]:
        """Find all existing backups in available storage locations.

        Scan configured storage locations to discover existing backup files and organize them by storage type and path. Use this to get an up-to-date inventory of all available backups for management operations like listing, pruning, or restoration.

        Returns:
            list[StorageLocation]: A list of StorageLocation objects containing discovered backups.
        """
        if not self.rebuild_storage_locations and self._storage_locations:
            return self._storage_locations

        logger.trace(f"Indexing storage locations for: {settings.name}")
        match settings.storage_location:
            case StorageType.LOCAL:
                self._storage_locations = self._find_existing_backups_local()

            case StorageType.AWS:
                self._storage_locations = self._find_existing_backups_s3()
            case StorageType.ALL:
                self._storage_locations = (
                    self._find_existing_backups_local() + self._find_existing_backups_s3()
                )
            case _:
                assert_never(settings.storage_location)

        logger.trace(f"Indexed {len(self._storage_locations)} storage locations")
        self.rebuild_storage_locations = False
        return self._storage_locations

    def _create_tmp_backup_file(self) -> Path:
        """Create a temporary backup file in the temporary directory.

        Compress all configured source files and directories into a single tar.gz archive in the temporary directory. Use this to prepare backup data before distributing it to storage locations.

        Returns:
            Path: The path to the temporary backup file.

        Raises:
            ValueError: If a source path is neither a file nor a directory.
        """

        @dataclass
        class FileToAdd:
            """Class to store file information for the backup."""

            full_path: Path
            relative_path: Path | str
            is_dir: bool = False

        logger.trace("Determining files to add to backup")
        files_to_add = []

        if not settings.source_paths:
            logger.error("No source paths provided")
            sys.exit(1)

        for source in settings.source_paths:
            if source.is_dir():
                files_to_add.extend(
                    [
                        FileToAdd(
                            full_path=f,
                            relative_path=f"{f.relative_to(source)}"
                            if settings.strip_source_paths
                            else f"{source.name}/{f.relative_to(source)}",
                        )
                        for f in source.rglob("*")
                        if (f.is_file() or f.is_dir()) and should_include_file(f)
                    ]
                )
            elif source.is_file() and not source.is_symlink():
                if should_include_file(source):
                    files_to_add.extend([FileToAdd(full_path=source, relative_path=source.name)])
            else:
                msg = f"Not a file or directory: {source}"
                logger.error(msg)
                raise ValueError(msg)

        temp_tarfile = self.tmp_dir / f"{new_uid(bits=24)}.{BACKUP_EXTENSION}"
        logger.trace(f"Attempting to create tmp tarfile: {temp_tarfile}")
        try:
            with tarfile.open(
                temp_tarfile, "w:gz", compresslevel=settings.compression_level
            ) as tar:
                for file in files_to_add:
                    logger.trace(f"Add to tar: {file.relative_path}")
                    tar.add(file.full_path, arcname=file.relative_path)
        except tarfile.TarError as e:
            logger.error(f"Failed to create backup: {e}")
            return None

        del files_to_add
        logger.trace(f"Created temporary tarfile: {temp_tarfile}")
        return temp_tarfile

    def _delete_backup(self, backup: Backup) -> None:
        """Delete a backup file from the storage locations.

        Remove a specific backup file from its storage location, whether local filesystem or cloud storage. Use this to clean up individual backup files during pruning operations or manual cleanup.

        Args:
            backup (Backup): The backup object containing information about the file to delete.
        """
        match backup.storage_type:
            case StorageType.LOCAL:
                backup.path.unlink()
                logger.info(f"Deleted: {backup.path}")
            case StorageType.AWS:
                self.aws_service.delete_object(key=backup.name)
                logger.info(f"Deleted from S3: {backup.name}")
            case StorageType.ALL:  # pragma: no cover
                pass
            case _:  # pragma: no cover
                assert_never(backup.storage_type)

    def _do_restore(self, backup: Backup, destination: Path) -> bool:
        """Restore a backup file to the storage locations.

        Extract and decompress a backup archive to a specified destination directory, optionally changing file ownership. Use this to recover files from a backup archive for disaster recovery or data migration.

        Args:
            backup (Backup): The backup to restore.
            destination (Path): The destination path to restore the backup to.

        Returns:
            bool: True if the backup was successfully restored, False if restoration failed due to missing backups or invalid destination.
        """
        logger.debug(f"Restoring backup: {backup.name} ({backup.storage_type.value})")
        tarfile_path = None
        match backup.storage_type:
            case StorageType.LOCAL:
                logger.info(f"Restoring backup from local: {backup.name}")
                tarfile_path = backup.path

            case StorageType.AWS:
                logger.info(f"Restoring backup from S3: {backup.name}")
                if not self.aws_service.object_exists(backup.name):
                    logger.error(f"Backup file does not exist in S3: {backup.name}")
                    return False

                logger.trace(f"Downloading backup from S3 to tmp file: {backup.name}")
                tmp_file = self.tmp_dir / f"{new_uid(bits=24)}.{BACKUP_EXTENSION}"
                self.aws_service.get_object(key=backup.name, destination=tmp_file)

                tarfile_path = tmp_file

            case StorageType.ALL:
                return False
            case _:
                assert_never(backup.storage_type)

        logger.trace(f"Attempting to extract backup to '{destination}'")
        try:
            with tarfile.open(tarfile_path) as archive:
                archive.extractall(path=destination, filter="data")
        except tarfile.TarError as e:
            logger.error(f"Failed to restore backup: {tarfile_path}\n{e}")
            return False

        if settings.chown_uid and settings.chown_gid:
            chown_files(destination)

        logger.info(f"Backup restored to '{destination}'")
        return True

    @staticmethod
    def _find_existing_backups_local() -> list[StorageLocation]:
        """Find all existing backups in local storage locations.

        Scan local filesystem directories for backup files matching the configured naming pattern. Use this to discover existing local backups for inventory management and cleanup operations.

        Returns:
            list[StorageLocation]: A list of StorageLocation objects containing local backup files.
        """
        backups_by_storage_path: list[StorageLocation] = []
        for storage_path in settings.storage_paths:
            logger.trace(f"Indexing: {storage_path}")
            found_backups: list[Backup] = []
            found_files = find_files(
                path=storage_path, globs=[f"*{settings.name}*.{BACKUP_EXTENSION}"]
            )
            found_backups = sorted(
                [
                    Backup(
                        storage_type=StorageType.LOCAL,
                        name=x.name,
                        path=x,
                        storage_path=storage_path,
                    )
                    for x in found_files
                ],
                key=lambda x: x.zoned_datetime,
            )

            logger.debug(f"Found existing backups: {len(found_backups)} in '{storage_path}'")
            backups_by_storage_path.append(
                StorageLocation(
                    storage_path=storage_path,
                    storage_type=StorageType.LOCAL,
                    backups=found_backups,
                )
            )

        return backups_by_storage_path

    def _find_existing_backups_s3(self) -> list[StorageLocation]:
        """Find all existing backups in an S3 bucket.

        Query AWS S3 bucket for backup objects matching the configured naming pattern. Use this to discover existing cloud backups for inventory management and cleanup operations.

        Returns:
            list[StorageLocation]: A list of StorageLocation objects containing S3 backup objects.
        """
        if not self.aws_service:
            return []

        logger.trace("Indexing S3 storage location")
        found_backups = self.aws_service.list_objects(prefix=settings.name)

        if settings.aws_s3_bucket_path:
            found_backups = [
                x.replace(f"{settings.aws_s3_bucket_path.rstrip('/')}/", "") for x in found_backups
            ]

        backups = sorted(
            [Backup(storage_type=StorageType.AWS, name=x) for x in found_backups],
            key=lambda x: x.zoned_datetime,
        )
        logger.debug(f"Found existing backups: {len(backups)} in S3 bucket")
        return [
            StorageLocation(
                storage_path=settings.aws_s3_bucket_path,
                storage_type=StorageType.AWS,
                backups=backups,
            )
        ]

    def _identify_backups_to_delete(self) -> list[Backup]:
        """Identify backups to delete based on retention policy configuration.

        Analyze all storage locations and determine which backup files should be removed according to the configured retention policy. For count-based policies, identify excess backups beyond the maximum count. For time-based policies, identify excess backups within each time unit category (hourly, daily, weekly, monthly) beyond their respective retention limits.

        Returns:
            list[Backup]: A list of Backup objects that should be deleted to comply with retention policy.
        """
        logger.trace("Identifying backups to delete")
        backups_to_delete: list[Backup] = []

        for storage_location in self.storage_locations:
            match settings.retention_policy.policy_type:
                case RetentionPolicyType.KEEP_ALL:
                    logger.info("Will not delete backups because no retention policy is set")
                    return backups_to_delete

                case RetentionPolicyType.COUNT_BASED:
                    max_keep = settings.retention_policy.get_retention()
                    if len(storage_location.backups) > max_keep:
                        logger.trace(
                            f"Found {len(storage_location.backups) - max_keep} backups to prune from '{storage_location.logging_name}'"
                        )
                        backups_to_delete.extend(
                            list(reversed(storage_location.backups))[max_keep:]
                        )

                case RetentionPolicyType.TIME_BASED:
                    for backup_type, backups in storage_location.backups_by_time_unit.items():
                        max_keep = settings.retention_policy.get_retention(backup_type)
                        if len(backups) > max_keep:
                            logger.trace(
                                f"Found {len(backups) - max_keep} {backup_type.value} backups to prune from '{storage_location.logging_name}'"
                            )
                            backups_to_delete.extend(list(reversed(backups))[max_keep:])

                case _:
                    assert_never(settings.retention_policy.policy_type)

        logger.trace(
            f"Identified {len(backups_to_delete)} backups to delete across {len(self.storage_locations)} storage locations"
        )
        return backups_to_delete

    def _rename_no_labels(self) -> list[FileForRename]:
        """Rename backup files to remove time unit labels.

        Generate rename operations to strip time unit labels and UUIDs from backup filenames. Use this to simplify backup naming when detailed time unit labeling is not required.

        Returns:
            list[FileForRename]: A list of FileForRename objects containing rename operations.
        """
        files_for_rename: list[FileForRename] = []
        for storage_location in self.storage_locations:
            for backup in storage_location.backups:
                new_backup_name = backup.name
                name_parts = BACKUP_NAME_REGEX.finditer(backup.name)
                for match in name_parts:
                    matches = match.groupdict()
                    found_period = matches.get("period", None)
                    found_uuid = matches.get("uuid", None)
                if found_period:
                    new_backup_name = re.sub(rf"-{found_period}", "", new_backup_name)
                if found_uuid:
                    new_backup_name = re.sub(rf"-{found_uuid}", "", new_backup_name)

                files_for_rename.append(
                    FileForRename(
                        backup=backup,
                        new_name=new_backup_name,
                        do_rename=backup.name != new_backup_name,
                    )
                )

        return files_for_rename

    def _rename_with_labels(self) -> list[FileForRename]:
        """Rename backup files to include time unit labels.

        Generate rename operations to add or update time unit labels in backup filenames. Use this to organize backups by time periods (hourly, daily, weekly, monthly) for better retention policy management.

        Returns:
            list[FileForRename]: A list of FileForRename objects containing rename operations.
        """
        files_for_rename: list[FileForRename] = []
        for storage_location in self.storage_locations:
            for backup_type, backups in storage_location.backups_by_time_unit.items():
                for backup in backups:
                    name_parts = BACKUP_NAME_REGEX.finditer(backup.name)
                    for match in name_parts:
                        matches = match.groupdict()
                        found_period = matches.get("period", None)
                    if found_period and found_period == backup_type.value:
                        files_for_rename.append(
                            FileForRename(
                                backup=backup,
                                new_name=backup.name,
                                do_rename=False,
                            )
                        )
                        continue

                    new_name = BACKUP_NAME_REGEX.sub(
                        repl=f"{matches.get('name')}-{matches.get('timestamp')}-{backup_type.value}.{BACKUP_EXTENSION}",
                        string=backup.name,
                    )
                    files_for_rename.append(
                        FileForRename(
                            backup=backup,
                            new_name=new_name,
                            do_rename=True,
                        )
                    )

        return files_for_rename

    def create_backup(self) -> list[Backup]:
        """Create compressed backup archives of all configured sources and distribute them to all storage_paths.

        Generate new backup files by compressing all source files and directories into tar.gz archives, then copy these archives to each configured destination directory. Use this to perform the core backup operation that preserves your data with configurable compression and multi-destination redundancy.

        Returns:
            list[Backup]: A list of Backup objects which were created.
        """
        logger.trace("Creating new backup")
        tmp_backup = self._create_tmp_backup_file()
        created_backups: list[Backup] = []

        for storage_location in self.storage_locations:
            backup_name = storage_location.generate_new_backup_name()

            if storage_location.storage_type == StorageType.LOCAL:
                backup_path = Path(storage_location.storage_path) / backup_name
                logger.debug(f"Copy tmp backup to local: {backup_path}")
                copy_file(src=tmp_backup, dst=backup_path)
                logger.info(f"Created: {backup_path}")
                created_backups.append(
                    Backup(
                        storage_type=StorageType.LOCAL,
                        name=backup_path.name,
                        path=backup_path,
                        storage_path=storage_location.storage_path,
                    )
                )

            elif storage_location.storage_type == StorageType.AWS:
                logger.debug(f"Upload tmp backup to S3: {backup_name}")
                self.aws_service.upload_object(file=tmp_backup, name=backup_name)
                created_backups.append(Backup(storage_type=StorageType.AWS, name=backup_name))
                logger.info(f"S3 created: {backup_name}")

        if settings.delete_src_after_backup:
            logger.debug("Clean source paths after backup")

            if settings.source_paths:
                for source in settings.source_paths:
                    if source.is_dir():
                        clean_directory(source)
                        logger.info(f"Cleaned source: {source}")
                    else:
                        source.unlink()
                        logger.info(f"Deleted source: {source}")

        logger.trace("Require storage location re-index on next call")
        self.rebuild_storage_locations = True
        return created_backups

    def get_latest_backup(self) -> Backup:
        """Get the latest backup from the storage locations.

        Find the most recent backup across all configured storage locations based on timestamp. Use this to identify the newest backup for restoration operations or to determine if new backups are needed.

        Returns:
            Backup: The latest backup, or None if no backups exist.
        """
        all_backups = [x for y in self.storage_locations for x in y.backups]
        if not all_backups:
            logger.error("No backups found")
            return None

        latest_backup = max(all_backups, key=lambda x: x.zoned_datetime.timestamp())
        logger.debug(
            f"Identified latest backup: {latest_backup.name} ({latest_backup.storage_type.value})"
        )
        return latest_backup

    def list_backups(self) -> list[Backup]:
        """Retrieve all existing backup files for this backup configuration.

        Get a complete list of backup objects sorted by creation time to enable backup inventory management, cleanup operations, or user display of available backups. Use this when you need to work with backup metadata rather than just file paths.

        Returns:
            list[Backup]: A list of backup objects sorted by creation time from oldest to newest.
        """
        return [x for y in self.storage_locations for x in y.backups]

    def prune_backups(self) -> list[Backup]:
        """Remove old backup files according to configured retention policies to manage storage usage.

        Delete excess backup files while preserving the most important backups based on the retention policy configuration. Use this to automatically clean up old backups and prevent unlimited storage growth while maintaining appropriate historical coverage.

        Returns:
            list[Backup]: A list of backup objects that were successfully deleted during the pruning operation.
        """
        logger.trace("Pruning backups")
        backups_to_delete = self._identify_backups_to_delete()

        local_backups_to_delete = [
            x for x in backups_to_delete if x.storage_type == StorageType.LOCAL
        ]
        s3_backups_to_delete = [x for x in backups_to_delete if x.storage_type == StorageType.AWS]

        logger.debug(f"Deleting {len(local_backups_to_delete)} local backups")
        for backup in local_backups_to_delete:
            self._delete_backup(backup)

        if s3_backups_to_delete:
            logger.debug(f"Deleting {len(s3_backups_to_delete)} S3 backups")
            deleted_s3_backups = self.aws_service.delete_objects(
                keys=[x.name for x in s3_backups_to_delete]
            )
            for key in deleted_s3_backups:
                logger.info(f"Deleted from S3: {key}")

        total_deleted = (
            local_backups_to_delete + deleted_s3_backups
            if s3_backups_to_delete
            else local_backups_to_delete
        )
        logger.info(
            f"Pruned {len(total_deleted)} backups across {len(self.storage_locations)} storage locations"
        )

        logger.trace("Require storage location re-index on next call")
        self.rebuild_storage_locations = True
        return backups_to_delete

    def rename_backups(self) -> None:
        """Rename all backups according to the configured naming strategy.

        Apply consistent naming patterns to all existing backups, either adding time unit labels or removing them based on configuration. Use this to standardize backup naming across all storage locations for better organization and retention policy management.
        """
        logger.trace("Begin renaming backups")
        if settings.label_time_units:
            files_for_rename = self._rename_with_labels()
        else:
            files_for_rename = self._rename_no_labels()

        for file in files_for_rename:
            if file.do_rename:
                target_exists = (
                    len(
                        [
                            x.new_name
                            for x in files_for_rename
                            if x.new_name == file.new_name
                            and x.backup.storage_type == file.backup.storage_type
                            and x.backup.storage_path == file.backup.storage_path
                        ]
                    )
                    > 1
                )
                if target_exists:
                    logger.trace(
                        f"Attempting to rename backup: {file.backup.name} -> {file.new_name}"
                    )
                    file.new_name = f"{file.new_name.rstrip(f'.{BACKUP_EXTENSION}')}-{new_uid(bits=24)}.{BACKUP_EXTENSION}"

                if file.backup.storage_type == StorageType.LOCAL:
                    file.backup.path.rename(file.backup.path.parent / file.new_name)
                    logger.debug(f"Rename: {file.backup.path} -> {file.new_name}")
                elif file.backup.storage_type == StorageType.AWS:
                    self.aws_service.rename_object(
                        current_name=file.backup.name, new_name=file.new_name
                    )
                    logger.debug(f"S3: Rename {file.backup.name} -> {file.new_name}")

        if len([x for x in files_for_rename if x.do_rename]) > 0:
            self.did_create_backup = True
            logger.info(f"Renamed {len([x for x in files_for_rename if x.do_rename])} backups")

            logger.trace("Require storage location re-index on next call")
            self.rebuild_storage_locations = True

        else:
            logger.info("No backups to rename")

    def restore_backup(
        self, destination: Path | str | None = None, *, clean_before_restore: bool = False
    ) -> bool:
        """Extract and restore the most recent backup to a specified destination directory.

        Decompress and extract the latest backup archive to recover files and directories to their original structure. Use this for disaster recovery, file restoration, or migrating backup contents to a new location.

        Args:
            destination (Path | str | None, optional): The directory path where backup contents should be extracted and restored. If None, uses the configured restore path. Defaults to None.
            clean_before_restore (bool): Whether to clean the restore path before restoring. Defaults to False.

        Returns:
            bool: True if the backup was successfully restored, False if restoration failed due to missing backups or invalid destination.
        """
        destination = destination or settings.restore_path
        if not destination:
            logger.error("No destination provided and no restore directory configured")
            return False

        dest = Path(destination).expanduser().absolute()

        if not dest.exists():
            logger.error(f"Restore destination does not exist: {dest}")
            return False

        if not dest.is_dir():
            logger.error(f"Restore destination is not a directory: {dest}")
            return False

        if clean_before_restore or settings.clean_before_restore:
            clean_directory(dest)
            logger.info("Cleaned all files in backup destination before restore")

        most_recent_backup = self.get_latest_backup()
        if not most_recent_backup:
            logger.error("No backup found to restore")
            return False

        return self._do_restore(backup=most_recent_backup, destination=dest)
