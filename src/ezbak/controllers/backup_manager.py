"""Backup management controller."""

import re
import tarfile
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from nclutils import clean_directory, copy_file, find_files, logger, new_uid
from whenever import Instant, PlainDateTime, TimeZoneNotFoundError

from ezbak.constants import (
    ALWAYS_EXCLUDE_FILENAMES,
    BACKUP_EXTENSION,
    BACKUP_NAME_REGEX,
    DEFAULT_DATE_FORMAT,
    TIMESTAMP_REGEX,
    BackupType,
    RetentionPolicyType,
)
from ezbak.models import Backup, settings


@dataclass
class FileForRename:
    """Temporary class used for renaming backups."""

    old_path: Path
    new_path: Path
    do_rename: bool = False


class BackupManager:
    """Manage and control backup operations for specified sources and storage_paths."""

    def __init__(self) -> None:
        """Initialize a backup manager to automate backup creation, management, and cleanup operations.

        Create a backup manager that handles the complete backup lifecycle including file selection, compression, storage across multiple storage_paths, and automated cleanup based on retention policies. Use this when you need reliable, automated backup management with flexible scheduling and retention controls.

        Args:
            settings (Settings): The settings for the backup manager.
        """
        self.storage_paths = settings.storage_paths
        self.name = settings.name
        self.source_paths = settings.source_paths
        self.tz = settings.tz
        self.retention_policy = settings.retention_policy
        self.label_time_units = settings.label_time_units
        self.exclude_regex = settings.exclude_regex
        self.include_regex = settings.include_regex

    def _include_file_in_backup(self, path: Path) -> bool:
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

        if self.include_regex and re.search(rf"{self.include_regex}", str(path)) is None:
            logger.trace(f"Exclude by include regex: {path.name}")
            return False

        if self.exclude_regex and re.search(rf"{self.exclude_regex}", str(path)):
            logger.trace(f"Exclude by regex: {path.name}")
            return False

        return True

    def _generate_filename(self, path: Path | None = None, *, with_uuid: bool = False) -> str:
        """Generate a unique backup filename with timestamp and optional time unit classification.

        Create backup filenames that include timestamps and optionally classify backups by time periods (yearly, monthly, daily, etc.) to enable intelligent retention policies. Use this to ensure backup files have consistent, sortable names that support automated cleanup operations.

        Args:
            path (Path | None, optional): The directory path to check for existing backups when determining time unit labels. If None, uses the first configured destination. Defaults to None.
            with_uuid (bool, optional): Whether to append a unique identifier to prevent filename conflicts. Defaults to False.

        Returns:
            str: The generated backup filename in format "{name}-{timestamp}-{period}.{extension}" or "{name}-{timestamp}.{extension}" depending on configuration.

        Raises:
            TimeZoneNotFoundError: If the configured timezone identifier is invalid.
        """
        i = Instant.now()
        try:
            now = i.to_tz(self.tz) if self.tz else i.to_system_tz()
        except TimeZoneNotFoundError as e:
            logger.error(e)
            raise

        uuid = f"-{new_uid(bits=24)}" if with_uuid else ""

        timestamp = now.py_datetime().strftime(DEFAULT_DATE_FORMAT)

        if not self.label_time_units:
            return f"{self.name}-{timestamp}{uuid}.{BACKUP_EXTENSION}"

        _, existing_times = self._group_backups_by_period(path=path)

        period_checks = [
            ("yearly", BackupType.YEARLY, str(now.year)),
            ("monthly", BackupType.MONTHLY, str(now.month)),
            ("weekly", BackupType.WEEKLY, now.py_datetime().strftime("%W")),
            ("daily", BackupType.DAILY, str(now.day)),
            ("hourly", BackupType.HOURLY, str(now.hour)),
            ("minutely", BackupType.MINUTELY, str(now.minute)),
        ]

        period = "minutely"  # Default to minutely
        for period_name, backup_type, current_value in period_checks:
            if current_value not in existing_times[backup_type]:
                period = period_name
                break

        return f"{self.name}-{timestamp}-{period}{uuid}.{BACKUP_EXTENSION}"

    def _group_backups_by_period(
        self, path: Path | None = None
    ) -> tuple[dict[BackupType, list[Backup]], dict[BackupType, list[str]]]:
        """Categorize existing backups into time-based groups for retention policy management.

        Organize backups by time periods (yearly, monthly, weekly, daily, hourly, minutely) to enable selective retention where the oldest backup in each period is preserved. Use this to implement sophisticated retention policies that maintain historical coverage while controlling storage usage.

        Args:
            path (Path | None, optional): The directory path to search for backups. If None, searches all configured storage_paths. Defaults to None.

        Returns:
            tuple[dict[BackupType, list[Backup]], dict[BackupType, list[str]]]: A tuple containing dictionaries of backups grouped by time period and the date values found for each period.
        """
        backups = self._load_all_backups(path=path)

        dates_found: dict[BackupType, list[str]] = defaultdict(list)
        backups_by_type: dict[BackupType, list[Backup]] = defaultdict(list)

        period_definitions = [
            (BackupType.YEARLY, "year"),
            (BackupType.MONTHLY, "month"),
            (BackupType.WEEKLY, "week"),
            (BackupType.DAILY, "day"),
            (BackupType.HOURLY, "hour"),
            (BackupType.MINUTELY, "minute"),
        ]

        for backup in backups:
            if not backup:
                continue

            for period_type, date_attr in period_definitions:
                date_value = getattr(backup, date_attr)
                if date_value not in dates_found[period_type]:
                    dates_found[period_type].append(date_value)
                    backups_by_type[period_type].append(backup)
                    break  # Move to the next backup once it's categorized

                if period_type == BackupType.MINUTELY:
                    backups_by_type[period_type].append(backup)
                    break

        return backups_by_type, dates_found

    def _load_all_backups(self, path: Path | None = None) -> list[Backup]:
        """Discover and load all backup files matching this configuration into structured Backup objects.

        Scan configured storage_paths for backup files and convert them into Backup objects sorted by creation time. Use this to get a complete inventory of existing backups for operations like listing, pruning, or finding the latest backup.

        Args:
            path (Path | None, optional): The directory path to search for backups. If None, searches all configured storage_paths. Defaults to None.

        Returns:
            list[Backup]: A list of Backup objects sorted by creation time from oldest to newest.
        """
        storage_paths = [path] if path else self.storage_paths
        found_backups: list[Path] = []

        for destination in storage_paths:
            found_backups.extend(
                find_files(path=destination, globs=[f"*{self.name}*.{BACKUP_EXTENSION}"])
            )

        return sorted(
            [self._parse_backup_from_path(path=x) for x in found_backups],
            key=lambda x: x.zoned_datetime,
        )

    def _parse_backup_from_path(self, path: Path | None = None) -> Backup:
        """Extract backup metadata from a backup file path to create a structured Backup object.

        Parse backup filenames to extract timestamp information and create Backup objects that enable time-based operations like sorting, grouping, and retention management. Use this to convert file paths into structured data for backup management operations.

        Args:
            path (Path | None, optional): The backup file path to parse. If None, returns None.

        Returns:
            Backup: A Backup object containing parsed timestamp data and file path information, or None if parsing fails.

        Raises:
            TimeZoneNotFoundError: If the configured timezone identifier is invalid when converting timestamps.
        """
        try:
            timestamp = TIMESTAMP_REGEX.search(path.name).group(0)
        except AttributeError:
            logger.warning(f"Could not parse timestamp: {path}")
            return None
        plain_dt = PlainDateTime.parse_strptime(timestamp, format=DEFAULT_DATE_FORMAT)
        try:
            dt = plain_dt.assume_tz(self.tz) if self.tz else plain_dt.assume_system_tz()
        except TimeZoneNotFoundError as e:
            logger.error(e)
            raise

        return Backup(
            year=str(dt.year),
            month=str(dt.month),
            week=dt.py_datetime().strftime("%W"),
            day=str(dt.day),
            hour=str(dt.hour),
            minute=str(dt.minute),
            path=path,
            timestamp=timestamp,
            zoned_datetime=dt,
        )

    def _rename_no_labels(self, path: Path) -> list[FileForRename]:
        """Rename a backup file without time unit labels.

        Args:
            path (Path): The path to rename.

        Returns:
            list[FileForRename]: A list of FileForRename objects.
        """
        backups = self._load_all_backups(path=path)
        files_for_rename: list[FileForRename] = []
        for backup in backups:
            new_backup_name = backup.path.name
            name_parts = BACKUP_NAME_REGEX.finditer(backup.path.name)
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
                    old_path=backup.path,
                    new_path=backup.path.with_name(new_backup_name),
                    do_rename=backup.path.with_name(new_backup_name) != backup.path,
                )
            )

        return files_for_rename

    def _rename_with_labels(self, path: Path) -> list[FileForRename]:
        """Rename a backup file with time unit labels.

        Args:
            path (Path): The path to rename.

        Returns:
            list[FileForRename]: A list of FileForRename objects.
        """
        backup_dict, _ = self._group_backups_by_period(path=path)

        files_for_rename: list[FileForRename] = []
        for backup_type, backups in backup_dict.items():
            for backup in backups:
                name_parts = BACKUP_NAME_REGEX.finditer(backup.path.name)
                for match in name_parts:
                    matches = match.groupdict()
                    found_period = matches.get("period", None)
                if found_period and found_period == backup_type.value:
                    files_for_rename.append(
                        FileForRename(old_path=backup.path, new_path=backup.path, do_rename=False)
                    )
                    continue

                new_name = BACKUP_NAME_REGEX.sub(
                    repl=f"{matches.get('name')}-{matches.get('timestamp')}-{backup_type.value}.{BACKUP_EXTENSION}",
                    string=backup.path.name,
                )
                files_for_rename.append(
                    FileForRename(
                        old_path=backup.path,
                        new_path=backup.path.with_name(new_name),
                        do_rename=True,
                    )
                )

        return files_for_rename

    def get_latest_backup(self) -> Path:
        """Find the most recently created backup file for restoration or verification purposes.

        Locate the newest backup file based on creation time to enable quick access to the most current backup for restoration operations or backup verification. Use this when you need to restore from or examine the latest backup without manually sorting through all available backups.

        Returns:
            Path: The file path of the most recently created backup.
        """
        if not self.list_backups():
            logger.error("No backups found")
            return None

        backups: list[Path] = self.list_backups()
        return max(backups, key=lambda x: x.stat().st_ctime)

    def create_backup(self) -> list[Path]:
        """Create compressed backup archives of all configured sources and distribute them to all storage_paths.

        Generate new backup files by compressing all source files and directories into tar.gz archives, then copy these archives to each configured destination directory. Use this to perform the core backup operation that preserves your data with configurable compression and multi-destination redundancy.

        Returns:
            list[Path]: A list of paths to the newly created backup files, one for each destination.

        Raises:
            ValueError: If a source path is neither a file nor a directory.
        """

        @dataclass
        class FileToAdd:
            full_path: Path
            relative_path: Path | str

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

        with tempfile.TemporaryDirectory() as temp_dir_path:
            temp_tarfile = Path(temp_dir_path) / f"{new_uid(bits=24)}.{BACKUP_EXTENSION}"
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

            created_files = []
            for destination_dir in self.storage_paths:
                backup_path = destination_dir / self._generate_filename(path=destination_dir)
                if backup_path.exists():
                    backup_path = destination_dir / self._generate_filename(with_uuid=True)

                logger.trace(f"New backup name: {backup_path}")
                copy_file(src=temp_tarfile, dst=backup_path)
                created_files.append(backup_path)
                logger.info(f"Created: {backup_path}")

            return created_files

    def list_backups(self, path: Path | None = None) -> list[Path]:
        """Retrieve file paths of all existing backup files for this backup configuration.

        Get a complete list of backup file paths sorted by creation time to enable backup inventory management, cleanup operations, or user display of available backups. Use this when you need to work with backup file paths directly rather than Backup objects.

        Args:
            path (Path | None, optional): The directory path to search for backups. If None, searches all configured storage_paths. Defaults to None.

        Returns:
            list[Path]: A list of backup file paths sorted by creation time from oldest to newest.
        """
        return [x.path for x in self._load_all_backups(path=path)]

    def prune_backups(self) -> list[Path]:
        """Remove old backup files according to configured retention policies to manage storage usage.

        Delete excess backup files while preserving the most important backups based on the retention policy configuration. Use this to automatically clean up old backups and prevent unlimited storage growth while maintaining appropriate historical coverage.

        Returns:
            list[Path]: A list of file paths that were successfully deleted during the pruning operation.
        """
        deleted_files: list[Path] = []
        if self.retention_policy.policy_type == RetentionPolicyType.KEEP_ALL:
            logger.info("Will not delete backups because no retention policy is set")
            return deleted_files

        if self.retention_policy.policy_type == RetentionPolicyType.COUNT_BASED:
            for path in self.storage_paths:
                backups = self._load_all_backups(path=path)
                sorted_backups = sorted(backups, key=lambda x: x.zoned_datetime, reverse=True)
                max_keep = self.retention_policy.get_retention(BackupType.NO_TYPE)
                if len(sorted_backups) > max_keep:
                    deleted_files.extend(
                        backup.delete()
                        for backup in sorted_backups[max_keep:]
                        if isinstance(backup, Backup)
                    )
        else:
            for path in self.storage_paths:
                backups_by_type, _ = self._group_backups_by_period(path=path)
                for backup_type, backups in backups_by_type.items():
                    sorted_backups = sorted(backups, key=lambda x: x.zoned_datetime, reverse=True)
                    max_keep = self.retention_policy.get_retention(backup_type)
                    if len(sorted_backups) > max_keep:
                        deleted_files.extend(
                            backup.delete()
                            for backup in sorted_backups[max_keep:]
                            if isinstance(backup, Backup)
                        )

        logger.info(f"Deleted {len(deleted_files)} old backups")
        return deleted_files

    def rename_backups(self, path: Path | None = None) -> None:
        """Update backup filenames to match the current naming convention and time unit labeling configuration.

        Rename existing backup files to ensure consistent filename formats when configuration changes or to apply time unit labels to previously unlabeled backups. Use this to maintain filename consistency across your backup collection after changing naming conventions.

        Args:
            path (Path | None, optional): The directory path containing backups to rename. If None, processes all configured storage_paths. Defaults to None.
        """
        if self.label_time_units:
            files_for_rename = self._rename_with_labels(path=path)
        else:
            files_for_rename = self._rename_no_labels(path=path)

        for file in files_for_rename:
            if file.do_rename:
                target_exists = (
                    len([x.new_path for x in files_for_rename if x.new_path == file.new_path]) > 1
                )
                if target_exists:
                    file.new_path = file.new_path.with_name(
                        f"{file.new_path.stem}-{new_uid(bits=24)}.{BACKUP_EXTENSION}"
                    )
                file.old_path.rename(file.new_path)
                logger.debug(f"Renamed: {file.old_path.name} -> {file.new_path.name}")

        if len([x for x in files_for_rename if x.do_rename]) > 0:
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

        backup = self._parse_backup_from_path(path=most_recent_backup)
        return backup.restore(destination=dest)
