"""Backup management controller."""

import re
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from loguru import logger
from nclutils.fs import clean_directory

from ezbak.constants import (
    RetentionPolicyType,
    StorageType,
)
from ezbak.models import Backup, StorageLocation
from ezbak.models.backup_name import (
    add_uid_suffix,
    build_backup_name,
    new_staging_filename,
    parse_backup_name,
)
from ezbak.models.settings import Settings
from ezbak.utils import chown_files, should_include_file, validate_source_paths

from .aws import AWSService
from .backends import LocalBackend, S3Backend, StorageBackend


@dataclass
class FileForRename:
    """Pair a backup with its proposed new filename for the rename pass.

    Carry the target backup, the computed new_name, and do_rename, which the rename loop checks to skip files whose name is already correct.
    """

    backup: Backup
    new_name: str
    do_rename: bool = False


class BackupManager:
    """Manage and control backup operations for specified sources and storage_paths."""

    def __init__(self, config: Settings) -> None:
        """Initialize a backup manager to automate backup creation, management, and cleanup operations.

        Create a backup manager that handles the complete backup lifecycle including file selection, compression, storage across multiple storage_paths, and automated cleanup based on retention policies. Use this when you need reliable, automated backup management with flexible scheduling and retention controls.
        """
        self.settings = config
        self.aws_service: AWSService | None = None
        self._storage_locations: list[StorageLocation] = []
        self.rebuild_storage_locations = False
        self.tmp_dir = Path(self.settings.tmp_dir.name)

        self.backends: list[StorageBackend] = []
        if self.settings.storage_type in {StorageType.LOCAL, StorageType.ALL}:
            self.backends.append(LocalBackend(self.settings))

        if self.settings.storage_type in {StorageType.AWS, StorageType.ALL}:
            try:
                self.aws_service = AWSService(
                    aws_access_key=self.settings.aws_access_key,
                    aws_secret_key=self.settings.aws_secret_key,
                    bucket_name=self.settings.aws_s3_bucket_name,
                    bucket_path=self.settings.aws_s3_bucket_path,
                )
            except ValueError as e:
                logger.error(e)

            if self.aws_service:
                self.backends.append(
                    S3Backend(self.settings, aws_service=self.aws_service, tmp_dir=self.tmp_dir)
                )

        self._backend_by_type: dict[StorageType, StorageBackend] = {
            backend.storage_type: backend for backend in self.backends
        }

    @property
    def storage_locations(self) -> list[StorageLocation]:
        """Find all existing backups in available storage locations.

        Scan configured storage locations to discover existing backup files and organize them by storage type and path. Use this to get an up-to-date inventory of all available backups for management operations like listing, pruning, or restoration.

        Returns:
            list[StorageLocation]: A list of StorageLocation objects containing discovered backups.
        """
        if not self.rebuild_storage_locations and self._storage_locations:
            return self._storage_locations

        logger.trace(f"Indexing storage locations for: {self.settings.name}")
        self._storage_locations = [
            location for backend in self.backends for location in backend.index()
        ]
        logger.trace(f"Indexed {len(self._storage_locations)} storage locations")
        self.rebuild_storage_locations = False
        return self._storage_locations

    def _create_tmp_backup_file(self) -> Path | None:
        """Create a temporary backup file in the temporary directory.

        Compress all configured source files and directories into a single tar.gz archive in the temporary directory. Use this to prepare backup data before distributing it to storage locations.

        Returns:
            Path | None: The path to the temporary backup file, or None if archive creation failed.

        Raises:
            ValueError: If a source path is neither a file nor a directory.
        """

        @dataclass
        class FileToAdd:
            """Pair a source path with the archive name it will be written under.

            full_path is the file on disk; relative_path is the arcname inside the tar, which controls the directory layout of the resulting archive.
            """

            full_path: Path
            relative_path: Path | str
            is_dir: bool = False

        logger.trace("Determining files to add to backup")
        files_to_add = []

        if not self.settings.source_paths:
            logger.error("No source paths provided")
            sys.exit(1)

        for source in self.settings.source_paths:
            if source.is_dir():
                files_to_add.extend(
                    [
                        FileToAdd(
                            full_path=f,
                            relative_path=f"{f.relative_to(source)}"
                            if self.settings.strip_source_paths
                            else f"{source.name}/{f.relative_to(source)}",
                        )
                        for f in source.rglob("*")
                        if (f.is_file() or f.is_dir())
                        and should_include_file(
                            path=f,
                            include_regex=self.settings.include_regex,
                            exclude_regex=self.settings.exclude_regex,
                        )
                    ]
                )
            elif source.is_file() and not source.is_symlink():
                if should_include_file(
                    path=source,
                    include_regex=self.settings.include_regex,
                    exclude_regex=self.settings.exclude_regex,
                ):
                    files_to_add.append(FileToAdd(full_path=source, relative_path=source.name))
            else:
                msg = f"Not a file or directory: {source}"
                logger.error(msg)
                raise ValueError(msg)

        temp_tarfile = self.tmp_dir / new_staging_filename()
        logger.trace(f"Attempting to create tmp tarfile: {temp_tarfile}")
        try:
            with tarfile.open(
                temp_tarfile, "w:gz", compresslevel=self.settings.compression_level
            ) as tar:
                for file in files_to_add:
                    logger.trace(f"Add to tar: {file.relative_path}")
                    tar.add(file.full_path, arcname=file.relative_path)
        except tarfile.TarError as e:
            logger.error(f"Failed to create backup: {e}")
            return None

        logger.trace(f"Created temporary tarfile: {temp_tarfile}")
        return temp_tarfile

    def _delete_backup(self, backup: Backup) -> None:
        """Delete a backup file from the storage locations.

        Remove a specific backup file from its storage location, whether local filesystem or cloud storage. Use this to clean up individual backup files during pruning operations or manual cleanup.

        Args:
            backup (Backup): The backup object containing information about the file to delete.
        """
        self._backend_by_type[backup.storage_type].delete(backup)

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
        tarfile_path = self._backend_by_type[backup.storage_type].prepare_for_restore(backup)
        if tarfile_path is None:
            return False

        logger.trace(f"Attempting to extract backup to '{destination}'")
        try:
            with tarfile.open(tarfile_path) as archive:
                archive.extractall(path=destination, filter="data")
        except tarfile.TarError as e:
            logger.error(f"Failed to restore backup: {tarfile_path}\n{e}")
            return False

        if self.settings.chown_uid and self.settings.chown_gid:
            chown_files(
                directory=destination, uid=self.settings.chown_uid, gid=self.settings.chown_gid
            )

        logger.info(f"Backup restored to '{destination}'")
        return True

    def _identify_backups_to_delete(self) -> list[Backup]:
        """Identify backups to delete based on retention policy configuration.

        Analyze all storage locations and determine which backup files should be removed according to the configured retention policy. For count-based policies, identify excess backups beyond the maximum count. For time-based policies, identify excess backups within each time unit category (hourly, daily, weekly, monthly) beyond their respective retention limits.

        Returns:
            list[Backup]: A list of Backup objects that should be deleted to comply with retention policy.
        """
        logger.trace("Identifying backups to delete")
        backups_to_delete: list[Backup] = []

        for storage_location in self.storage_locations:
            match self.settings.retention_policy.policy_type:
                case RetentionPolicyType.KEEP_ALL:
                    logger.info("Will not delete backups because no retention policy is set")
                    return backups_to_delete

                case RetentionPolicyType.COUNT_BASED:
                    max_keep = self.settings.retention_policy.get_retention()
                    if len(storage_location.backups) > max_keep:
                        logger.trace(
                            f"Found {len(storage_location.backups) - max_keep} backups to prune from '{storage_location.logging_name}'"
                        )
                        backups_to_delete.extend(
                            list(reversed(storage_location.backups))[max_keep:]
                        )

                case RetentionPolicyType.TIME_BASED:
                    for backup_type, backups in storage_location.backups_by_time_unit.items():
                        max_keep = self.settings.retention_policy.get_retention(backup_type)
                        if len(backups) > max_keep:
                            logger.trace(
                                f"Found {len(backups) - max_keep} {backup_type.value} backups to prune from '{storage_location.logging_name}'"
                            )
                            backups_to_delete.extend(list(reversed(backups))[max_keep:])

                case _:
                    assert_never(self.settings.retention_policy.policy_type)

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
                matches = parse_backup_name(backup.name)
                if matches is None:
                    continue

                new_backup_name = backup.name
                found_period = matches.get("period")
                found_uuid = matches.get("uuid")
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
                    matches = parse_backup_name(backup.name)
                    if matches is None:
                        continue

                    found_period = matches.get("period")
                    if found_period and found_period == backup_type.value:
                        files_for_rename.append(
                            FileForRename(
                                backup=backup,
                                new_name=backup.name,
                                do_rename=False,
                            )
                        )
                        continue

                    new_name = build_backup_name(
                        name=str(matches.get("name")),
                        timestamp=str(matches.get("timestamp")),
                        period=backup_type.value,
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
        validate_source_paths(source_paths=self.settings.source_paths)

        logger.trace("Creating new backup")
        tmp_backup = self._create_tmp_backup_file()
        if tmp_backup is None:
            logger.error("Backup creation aborted: temporary archive was not created")
            return []
        created_backups: list[Backup] = []

        for storage_location in self.storage_locations:
            backend = self._backend_by_type[storage_location.storage_type]
            created_backups.append(
                backend.write(tmp_backup=tmp_backup, storage_location=storage_location)
            )

        try:
            tmp_backup.unlink()
        except FileNotFoundError:
            logger.warning(f"FileNotFoundError attempting to unlink: {tmp_backup}")
        else:
            logger.debug(f"Deleted tmp backup: {tmp_backup}")

        if self.settings.delete_src_after_backup:
            logger.debug("Clean source paths after backup")

            if self.settings.source_paths:
                for source in self.settings.source_paths:
                    if source.is_dir():
                        clean_directory(source)
                        logger.info(f"Cleaned source: {source}")
                    else:
                        source.unlink()
                        logger.info(f"Deleted source: {source}")

        logger.trace("Require storage location re-index on next call")
        self.rebuild_storage_locations = True
        return created_backups

    def get_latest_backup(self) -> Backup | None:
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
        logger.debug(
            f"Prune targets ({len(backups_to_delete)}): {[x.name for x in backups_to_delete]}"
        )

        total_deleted = 0
        for backend in self.backends:
            targets = [x for x in backups_to_delete if x.storage_type == backend.storage_type]
            total_deleted += backend.delete_many(targets)

        logger.info(
            f"Pruned {total_deleted} backups across {len(self.storage_locations)} storage locations"
        )

        logger.trace("Require storage location re-index on next call")
        self.rebuild_storage_locations = True
        return backups_to_delete

    def rename_backups(self) -> None:
        """Rename all backups according to the configured naming strategy.

        Apply consistent naming patterns to all existing backups, either adding time unit labels or removing them based on configuration. Use this to standardize backup naming across all storage locations for better organization and retention policy management.
        """
        logger.trace("Begin renaming backups")
        if self.settings.label_time_units:
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
                    file.new_name = add_uid_suffix(file.new_name)

                self._backend_by_type[file.backup.storage_type].rename(
                    backup=file.backup, new_name=file.new_name
                )

        renamed = [x for x in files_for_rename if x.do_rename]
        if len(renamed) > 0:
            self.did_create_backup = True
            logger.info(f"Renamed {len(renamed)} backups")

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

        Raises:
            ValueError: If the destination is not provided and no restore directory is configured.
            ValueError: If the destination does not exist or is not a directory.
        """
        destination = destination or self.settings.restore_path

        try:
            dest = Path(destination).expanduser().absolute()
        except Exception as e:
            msg = f"Invalid destination: {destination}"
            raise ValueError(msg) from e

        if not dest:
            msg = "No destination provided and no restore directory configured"
            raise ValueError(msg)

        if not dest.exists() or not dest.is_dir():
            msg = f"Restore destination does not exist: {dest}"
            raise ValueError(msg)

        if clean_before_restore or self.settings.clean_before_restore:
            clean_directory(dest)
            logger.info("Cleaned all files in backup destination before restore")

        most_recent_backup = self.get_latest_backup()
        if not most_recent_backup:
            logger.error("No backup found to restore")
            return False

        return self._do_restore(backup=most_recent_backup, destination=dest)
