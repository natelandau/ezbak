"""Backup management controller."""

import atexit
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from nclutils import clean_directory, copy_file, find_files, logger, new_uid

from ezbak.constants import (
    ALWAYS_EXCLUDE_FILENAMES,
    BACKUP_EXTENSION,
    BACKUP_NAME_REGEX,
    RetentionPolicyType,
    StorageType,
)
from ezbak.models import Backup, StorageLocation, settings
from ezbak.utils import chown_files, cleanup_tmp_dir

from .aws import AWSService
from .mongodb import MongoManager


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

        Args:
            settings (Settings): The settings for the backup manager.
        """
        self.aws_service = None
        self.mongo_manager = None
        self._storage_locations: list[StorageLocation] = []
        self.rebuild_storage_locations = False
        self.tmp_dir = Path(settings.tmp_dir.name)

        if settings.mongo_uri and settings.mongo_db_name:
            logger.info("Backup MongoDB database")
            mongo_manager = MongoManager()
            mongo_backup_file = mongo_manager.make_tmp_backup()
            settings.update({"source_paths": [mongo_backup_file]})
            self.source_paths = [mongo_backup_file]
        else:
            self.source_paths = settings.source_paths

        if settings.storage_location in {StorageType.AWS, StorageType.ALL}:
            try:
                self.aws_service = AWSService()
            except ValueError as e:
                logger.error(e)

        atexit.register(cleanup_tmp_dir)

    @property
    def storage_locations(self) -> list[StorageLocation]:
        """Find all existing backups in available storage locations.

        Returns:
            list[StorageLocation]: A list of StorageLocation objects.
        """
        if not self.rebuild_storage_locations and self._storage_locations:
            return self._storage_locations

        match settings.storage_location:
            case StorageType.LOCAL:
                self._storage_locations = self._find_existing_backups_local()

            case StorageType.AWS:
                self._storage_locations = self._find_existing_backups_aws()
            case StorageType.ALL:
                self._storage_locations = (
                    self._find_existing_backups_local() + self._find_existing_backups_aws()
                )
            case _:
                assert_never(settings.storage_location)

        return self._storage_locations

    def _create_tmp_backup_file(self) -> Path:
        """Create a temporary backup file in the temporary directory.

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

        files_to_add = []
        for source in self.source_paths:
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
                        if f.is_file() and self._include_file_in_backup(f)
                    ]
                )
            elif source.is_file() and not source.is_symlink():
                if self._include_file_in_backup(source):
                    files_to_add.extend([FileToAdd(full_path=source, relative_path=source.name)])
            else:
                msg = f"Not a file or directory: {source}"
                logger.error(msg)
                raise ValueError(msg)

        temp_tarfile = self.tmp_dir / f"{new_uid(bits=24)}.{BACKUP_EXTENSION}"
        logger.trace(f"Temp tarfile: {temp_tarfile}")
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

        return temp_tarfile

    def _delete_backup(self, backup: Backup) -> None:
        """Delete a backup file from the storage locations."""
        match backup.storage_type:
            case StorageType.LOCAL:
                backup.path.unlink()
                logger.info(f"Delete: {backup.path}")
            case StorageType.AWS:
                self.aws_service.delete_object(key=backup.name)
            case StorageType.ALL:  # pragma: no cover
                pass
            case _:  # pragma: no cover
                assert_never(backup.storage_type)

    def _do_restore(self, backup: Backup, destination: Path) -> bool:
        """Restore a backup file to the storage locations.

        Args:
            backup (Backup): The backup to restore.
            destination (Path): The destination path to restore the backup to.

        Returns:
            bool: True if the backup was successfully restored, False if restoration failed due to missing backups or invalid destination.
        """
        logger.debug(f"Restoring backup: {backup.name}")
        tarfile_path = None
        match backup.storage_type:
            case StorageType.LOCAL:
                tarfile_path = backup.path

            case StorageType.AWS:
                if not self.aws_service.file_exists(backup.name):
                    logger.error(f"Backup file does not exist in AWS: {backup.name}")
                    return False

                tmp_file = self.tmp_dir / f"{new_uid(bits=24)}.{BACKUP_EXTENSION}"
                self.aws_service.get_object(key=backup.name, destination=tmp_file)

                tarfile_path = tmp_file

            case StorageType.ALL:
                return False
            case _:
                assert_never(backup.storage_type)

        try:
            with tarfile.open(tarfile_path) as archive:
                archive.extractall(path=destination, filter="data")
        except tarfile.TarError as e:
            logger.error(f"Failed to restore backup: {tarfile_path}\n{e}")
            return False

        if settings.chown_uid and settings.chown_gid:
            chown_files(destination)

        logger.info(f"Restored backup to {destination}")
        return True

    @staticmethod
    def _find_existing_backups_local() -> list[StorageLocation]:
        """Find all existing backups in an local storage locations.

        Returns:
            list[StorageLocation]: A list of StorageLocation objects.
        """
        backups_by_storage_path: list[StorageLocation] = []
        for storage_path in settings.storage_paths:
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
            backups_by_storage_path.append(
                StorageLocation(
                    storage_path=storage_path,
                    storage_type=StorageType.LOCAL,
                    backups=found_backups,
                )
            )

        return backups_by_storage_path

    def _find_existing_backups_aws(self) -> list[StorageLocation]:
        """Find all existing backups in AWS storage locations.

        Returns:
            list[StorageLocation]: A list of StorageLocation objects.
        """
        found_backups = self.aws_service.list_objects(prefix=settings.name)

        if settings.aws_s3_bucket_path:
            found_backups = [
                x.replace(f"{settings.aws_s3_bucket_path.rstrip('/')}/", "") for x in found_backups
            ]

        backups = sorted(
            [Backup(storage_type=StorageType.AWS, name=x) for x in found_backups],
            key=lambda x: x.zoned_datetime,
        )
        return [
            StorageLocation(
                storage_path=settings.aws_s3_bucket_path,
                storage_type=StorageType.AWS,
                backups=backups,
            )
        ]

    @staticmethod
    def _include_file_in_backup(path: Path) -> bool:
        """Determine whether a file should be included in the backup based on configured regex filters.

        Apply include and exclude regex patterns to filter files during backup creation. Use this to implement fine-grained control over which files are backed up, such as excluding temporary files or including only specific file types.

        Args:
            path (Path): The file path to evaluate against the configured regex patterns.

        Returns:
            bool: True if the file should be included in the backup, False if it should be excluded.
        """
        if path.is_symlink():
            logger.warning(f"Skip backup of symlink: {path}")
            return False

        if path.name in ALWAYS_EXCLUDE_FILENAMES:
            logger.trace(f"Excluded file: {path.name}")
            return False

        if settings.include_regex and re.search(rf"{settings.include_regex}", str(path)) is None:
            logger.trace(f"Exclude by include regex: {path.name}")
            return False

        if settings.exclude_regex and re.search(rf"{settings.exclude_regex}", str(path)):
            logger.trace(f"Exclude by regex: {path.name}")
            return False

        return True

    def _rename_no_labels(self) -> list[FileForRename]:
        """Rename a backup file without time unit labels.

        Returns:
            list[FileForRename]: A list of FileForRename objects.
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
        """Rename a backup file with time unit labels.

        Returns:
            list[FileForRename]: A list of FileForRename objects.
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
        tmp_backup = self._create_tmp_backup_file()
        created_backups: list[Backup] = []

        for storage_location in self.storage_locations:
            backup_name = storage_location.generate_new_backup_name()

            if storage_location.storage_type == StorageType.LOCAL:
                backup_path = Path(storage_location.storage_path) / backup_name
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
                self.rebuild_storage_locations = True
            elif storage_location.storage_type == StorageType.AWS:
                self.aws_service.upload_file(file=tmp_backup, name=backup_name)
                created_backups.append(Backup(storage_type=StorageType.AWS, name=backup_name))
                logger.info(f"S3 create: {backup_name}")
                self.rebuild_storage_locations = True

        return created_backups

    def get_latest_backup(self) -> Backup:
        """Get the latest backup from the storage locations.

        Returns:
            Backup: The latest backup.
        """
        all_backups = [x for y in self.storage_locations for x in y.backups]
        if not all_backups:
            logger.error("No backups found")
            return None

        return max(all_backups, key=lambda x: x.zoned_datetime.timestamp())

    def list_backups(self) -> list[Backup]:
        """Retrieve file paths of all existing backup files for this backup configuration.

        Get a complete list of backup file paths sorted by creation time to enable backup inventory management, cleanup operations, or user display of available backups. Use this when you need to work with backup file paths directly rather than Backup objects.

        Args:
            path (Path | None, optional): The directory path to search for backups. If None, searches all configured storage_paths. Defaults to None.

        Returns:
            list[Path]: A list of backup file paths sorted by creation time from oldest to newest.
        """
        return [x for y in self.storage_locations for x in y.backups]

    def prune_backups(self) -> list[Backup]:
        """Remove old backup files according to configured retention policies to manage storage usage.

        Delete excess backup files while preserving the most important backups based on the retention policy configuration. Use this to automatically clean up old backups and prevent unlimited storage growth while maintaining appropriate historical coverage.

        Returns:
            list[Backup]: A list of file paths that were successfully deleted during the pruning operation.
        """
        deleted_backups: list[Backup] = []

        for storage_location in self.storage_locations:
            match settings.retention_policy.policy_type:
                case RetentionPolicyType.KEEP_ALL:
                    logger.info("Will not delete backups because no retention policy is set")
                    return deleted_backups

                case RetentionPolicyType.COUNT_BASED:
                    max_keep = settings.retention_policy.get_retention()
                    if len(storage_location.backups) > max_keep:
                        deleted_backups.extend(list(reversed(storage_location.backups))[max_keep:])

                case RetentionPolicyType.TIME_BASED:
                    for backup_type, backups in storage_location.backups_by_time_unit.items():
                        max_keep = settings.retention_policy.get_retention(backup_type)
                        if len(backups) > max_keep:
                            deleted_backups.extend(list(reversed(backups))[max_keep:])

                case _:
                    assert_never(settings.retention_policy.policy_type)

        for backup in [x for x in deleted_backups if x.storage_type == StorageType.LOCAL]:
            self._delete_backup(backup)

        if any(x.storage_type == StorageType.AWS for x in deleted_backups):
            self.aws_service.delete_objects(
                keys=[x.name for x in deleted_backups if x.storage_type == StorageType.AWS]
            )

        logger.info(f"Pruned {len(deleted_backups)} backups")
        return deleted_backups

    def rename_backups(self) -> None:
        """Rename all backups to the configured name."""
        if settings.label_time_units:
            files_for_rename = self._rename_with_labels()
        else:
            files_for_rename = self._rename_no_labels()

        for file in files_for_rename:
            if file.do_rename:
                target_exists = (
                    len([x.new_name for x in files_for_rename if x.new_name == file.new_name]) > 1
                )
                if target_exists:
                    file.new_name = f"{file.new_name.rstrip(f'.{BACKUP_EXTENSION}')}-{new_uid(bits=24)}.{BACKUP_EXTENSION}"

                if file.backup.storage_type == StorageType.LOCAL:
                    file.backup.path.rename(file.backup.path.parent / file.new_name)
                    logger.debug(f"Rename: {file.backup.path} -> {file.new_name}")
                elif file.backup.storage_type == StorageType.AWS:
                    self.aws_service.rename_file(
                        current_name=file.backup.name, new_name=file.new_name
                    )
                    logger.debug(f"S3: Rename {file.backup.name} -> {file.new_name}")

        if len([x for x in files_for_rename if x.do_rename]) > 0:
            self.did_create_backup = True
            logger.info(f"Renamed {len([x for x in files_for_rename if x.do_rename])} backups")
        else:
            logger.info("No backups to rename")

    def restore_backup(
        self, destination: Path | str | None = None, *, clean_before_restore: bool = False
    ) -> bool:
        """Extract and restore the most recent backup to a specified destination directory.

        Decompress and extract the latest backup archive to recover files and directories to their original structure. Use this for disaster recovery, file restoration, or migrating backup contents to a new location.

        Args:
            destination (Path | str): The directory path where backup contents should be extracted and restored.
            clean_before_restore (bool): Whether to clean the restore path before restoring

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
