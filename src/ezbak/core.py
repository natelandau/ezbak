"""Core EZBak class: create, list, prune, and restore backups."""

from __future__ import annotations

import tarfile
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Literal, assert_never

from loguru import logger
from nclutils.fs import clean_directory
from pydantic import ValidationError
from whenever import PlainDateTime

from ezbak.config import BackupConfig
from ezbak.constants import (
    DEFAULT_DATE_PATTERN,
    RESTORE_DATE_REGEX,
    RetentionPolicyType,
    StorageType,
)
from ezbak.exceptions import (
    BackendNotFoundError,
    BackupFailedError,
    ConfigurationError,
    RestoreFailedError,
    StorageDeleteError,
    StorageInitError,
    StorageWriteError,
)
from ezbak.filters import (
    chown_files,
    should_include_file,
    validate_source_paths,
    validate_storage_paths,
)
from ezbak.logging import instantiate_logger
from ezbak.naming import new_staging_filename
from ezbak.storage import LocalBackend, S3Backend, StorageBackend
from ezbak.storage.aws import AWSService

if TYPE_CHECKING:
    from ezbak.backup import Backup, StorageLocation

PeriodUnit = Literal["years", "months", "days", "hours", "minutes", "seconds"]


def ezbak(**kwargs: object) -> EZBak:
    """Build an ``EZBak`` from keyword options without importing ``BackupConfig``.

    Convenience for quick scripts. Validates via ``BackupConfig`` and returns a ready core. Prefer ``EZBak(BackupConfig(...))`` when you want an explicit, reusable config object.

    Returns:
        EZBak: A configured backup core.

    Raises:
        ValidationError: If provided settings are invalid.
    """
    try:
        config = BackupConfig(**kwargs)  # type: ignore[arg-type]
    except ValidationError as e:
        for error in e.errors():
            logger.error(error["msg"])
        raise

    return EZBak(config)


class EZBak:
    """Manage and control backup operations for specified sources and storage_paths."""

    def __init__(self, config: BackupConfig) -> None:
        """Initialize the backup core with a validated `BackupConfig` and prepare logging, staging, and storage backends.

        Args:
            config (BackupConfig): Application configuration. Prefer using `ezbak()` to construct a validated configuration.
        """
        self.settings = config
        instantiate_logger(
            log_level=self.settings.log_level,
            log_file=self.settings.log_file,
            prefix=self.settings.log_prefix,
        )

        self.aws_service: AWSService | None = None
        self._storage_locations: list[StorageLocation] = []
        self.rebuild_storage_locations = False
        self._failed_destinations: list[str] = []

        # TemporaryDirectory registers its own finalizer, so the staging dir is removed
        # when this EZBak is garbage-collected or the process exits. Registering an extra
        # atexit callback bound to self would instead pin the whole EZBak (and its boto3
        # client and cached indexes) in memory until exit.
        self._tmp_dir_handle = TemporaryDirectory()
        self.tmp_dir = Path(self._tmp_dir_handle.name)

        self.backends: list[StorageBackend] = []
        if self.settings.storage_paths:
            try:
                # Create/validate the local directories up front so an unusable path
                # (read-only mount, permission denied) is recorded as a failed
                # destination and create_backup fails loudly, instead of a raw OSError
                # escaping later from the write loop's lazy index() call.
                validate_storage_paths(self.settings.storage_paths, create_if_missing=True)
            except OSError as e:
                logger.error(f"Cannot use local storage path(s): {e}")
                self._failed_destinations.append("local storage paths")
            else:
                self.backends.append(LocalBackend(self.settings))

        if self.settings.aws_s3_bucket_name:
            try:
                self.aws_service = AWSService(
                    aws_access_key=self.settings.aws_access_key,
                    aws_secret_key=self.settings.aws_secret_key,
                    bucket_name=self.settings.aws_s3_bucket_name,
                    bucket_path=self.settings.aws_s3_bucket_path,
                )
            except StorageInitError:
                # AWSService already logged the failure at the raise site; just record
                # the destination so create_backup fails loudly for it.
                self._failed_destinations.append(f"S3 bucket '{self.settings.aws_s3_bucket_name}'")

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
            ConfigurationError: If no source paths are configured or a source path is neither a file nor a directory.
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
            msg = "No source paths provided"
            logger.error(msg)
            raise ConfigurationError(msg)

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
                raise ConfigurationError(msg)

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

    def _backend_for_type(self, storage_type: StorageType) -> StorageBackend:
        """Return the backend for a storage type, or fail with a clear message.

        Args:
            storage_type (StorageType): The storage type to route.

        Returns:
            StorageBackend: The matching backend.

        Raises:
            BackendNotFoundError: If no configured backend handles the storage type.
        """
        backend = self._backend_by_type.get(storage_type)
        if backend is None:
            msg = f"No configured backend for storage type: {storage_type.value}"
            raise BackendNotFoundError(msg)
        return backend

    def _backend_for(self, backup: Backup) -> StorageBackend:
        """Return the backend that owns a backup, or fail with a clear message.

        Args:
            backup (Backup): The backup to route.

        Returns:
            StorageBackend: The matching backend.
        """
        return self._backend_for_type(backup.storage_type)

    def _delete_backup(self, backup: Backup) -> None:
        """Delete a backup file from the storage locations.

        Remove a specific backup file from its storage location, whether local filesystem or cloud storage. Use this to clean up individual backup files during pruning operations or manual cleanup.

        Args:
            backup (Backup): The backup object containing information about the file to delete.
        """
        self._backend_for(backup).delete(backup)

    def _do_restore(self, backup: Backup, destination: Path) -> bool:
        """Restore a backup file to the storage locations.

        Extract and decompress a backup archive to a specified destination directory, optionally changing file ownership. Use this to recover files from a backup archive for disaster recovery or data migration.

        Args:
            backup (Backup): The backup to restore.
            destination (Path): The destination path to restore the backup to.

        Returns:
            bool: True if the backup was successfully restored.

        Raises:
            RestoreFailedError: If the archive is missing from storage or cannot be extracted.
        """
        logger.debug(f"Restoring backup: {backup.name} ({backup.storage_type.value})")
        tarfile_path = self._backend_for(backup).prepare_for_restore(backup)
        if tarfile_path is None:
            msg = f"Backup archive is missing from storage: {backup.name}"
            logger.error(msg)
            raise RestoreFailedError(msg)

        logger.trace(f"Attempting to extract backup to '{destination}'")
        # Catch OSError alongside TarError so a missing or unreadable local archive
        # fails loudly instead of escaping as a raw error, which matters most after a
        # clean-before-restore has already emptied the destination.
        try:
            with tarfile.open(tarfile_path) as archive:
                archive.extractall(path=destination, filter="data")
        except (tarfile.TarError, OSError) as e:
            msg = f"Failed to extract backup archive: {tarfile_path}: {e}"
            logger.error(msg)
            raise RestoreFailedError(msg) from e

        # Compare against None, not truthiness: uid/gid 0 (root) is a valid target
        # and must not be treated as "unset".
        if self.settings.chown_uid is not None and self.settings.chown_gid is not None:
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

    def create_backup(self) -> list[Backup]:
        """Create compressed backup archives of all configured sources and distribute them to all storage_paths.

        Generate new backup files by compressing all source files and directories into tar.gz archives, then copy these archives to each configured destination directory. Use this to perform the core backup operation that preserves your data with configurable compression and multi-destination redundancy.

        Returns:
            list[Backup]: A list of Backup objects which were created.

        Raises:
            BackupFailedError: If the backup archive could not be created or any configured destination could not be written.
        """
        validate_source_paths(source_paths=self.settings.source_paths)

        logger.trace("Creating new backup")
        tmp_backup = self._create_tmp_backup_file()
        if tmp_backup is None:
            logger.error("Backup creation aborted: temporary archive was not created")
            raise BackupFailedError(["backup archive could not be created"])

        created_backups, write_failures = self._write_to_backends(tmp_backup)

        try:
            tmp_backup.unlink()
        except FileNotFoundError:
            logger.warning(f"FileNotFoundError attempting to unlink: {tmp_backup}")
        else:
            logger.debug(f"Deleted tmp backup: {tmp_backup}")

        logger.trace("Require storage location re-index on next call")
        self.rebuild_storage_locations = True

        # A destination that was requested but unusable (bad creds) or that failed
        # mid-write must fail the run loudly. Raise only after writing to healthy
        # destinations so their backups are preserved.
        failed_destinations = self._failed_destinations + write_failures
        if failed_destinations:
            raise BackupFailedError(failed_destinations)

        # Clean sources only on a fully successful run: for an S3-only run with bad
        # credentials this guard prevents deleting the only copy of the data.
        if self.settings.delete_src_after_backup:
            self._clean_source_paths()

        return created_backups

    def _write_to_backends(self, tmp_backup: Path) -> tuple[list[Backup], list[str]]:
        """Write the staged archive to every configured destination, tolerating per-backend failures.

        Use this to attempt every destination independently so one unhealthy backend
        does not block backups from being written to the others.

        Args:
            tmp_backup (Path): The staged tar.gz archive to distribute.

        Returns:
            tuple[list[Backup], list[str]]: The backups successfully written, and the
                logging names of storage locations that failed to write.
        """
        created_backups: list[Backup] = []
        write_failures: list[str] = []

        for storage_location in self.storage_locations:
            backend = self._backend_for_type(storage_location.storage_type)
            try:
                created_backups.append(
                    backend.write(tmp_backup=tmp_backup, storage_location=storage_location)
                )
            except StorageWriteError:
                # The backend already logged the failure with its destination context;
                # just record it so create_backup fails loudly after the loop.
                write_failures.append(str(storage_location.logging_name))

        return created_backups, write_failures

    def _clean_source_paths(self) -> None:
        """Remove source files and directories after a fully successful backup.

        Use this once every configured destination has confirmed a successful write,
        so source data is never deleted before it is safely backed up.
        """
        logger.debug("Clean source paths after backup")

        if not self.settings.source_paths:
            return

        for source in self.settings.source_paths:
            if source.is_dir():
                clean_directory(source)
                logger.info(f"Cleaned source: {source}")
            else:
                source.unlink()
                logger.info(f"Deleted source: {source}")

    def get_latest_backup(self) -> Backup | None:
        """Get the latest backup from the storage locations.

        Find the most recent backup across all configured storage locations based on timestamp. Use this to identify the newest backup for restoration operations or to determine if new backups are needed.

        Returns:
            Backup | None: The latest backup, or None if no backups exist.
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

    @staticmethod
    def _add_one_unit(period_start: PlainDateTime, unit: PeriodUnit) -> PlainDateTime:
        """Advance a plain date/time by one calendar or exact unit.

        Dispatched on the literal ``unit`` string (rather than **kwargs-unpacked) so
        each ``add()`` call has a literal keyword mypy can match against its
        overloads.

        Args:
            period_start (PlainDateTime): The start-of-period instant to advance.
            unit (PeriodUnit): One of "years", "months", "days", "hours", "minutes", "seconds".

        Returns:
            PlainDateTime: `period_start` advanced by one unit.
        """
        # naive_arithmetic_ok: hour/minute/second are exact units; adding them to a
        # plain datetime is intentional here because the caller converts the result
        # to a zoned instant immediately.
        match unit:
            case "years":
                return period_start.add(years=1, naive_arithmetic_ok=True)
            case "months":
                return period_start.add(months=1, naive_arithmetic_ok=True)
            case "days":
                return period_start.add(days=1, naive_arithmetic_ok=True)
            case "hours":
                return period_start.add(hours=1, naive_arithmetic_ok=True)
            case "minutes":
                return period_start.add(minutes=1, naive_arithmetic_ok=True)
            case "seconds":
                return period_start.add(seconds=1, naive_arithmetic_ok=True)
            case _:
                assert_never(unit)

    def _resolve_upper_boundary(self, point_in_time: str) -> float:
        """Convert a partial date/time to an exclusive upper-boundary timestamp.

        Pad the value to the start of its period, then advance by one unit of that
        period so an "at or before" match is a strict "less than" the returned
        instant. The "+1 unit" boundary avoids end-of-month, leap-year, and
        59-second edge cases.

        Args:
            point_in_time (str): A no-dash date/time, e.g. "202506" or "20250701T0330".

        Returns:
            float: POSIX timestamp of the exclusive upper boundary.

        Raises:
            ConfigurationError: If the value is not a recognized date/time shape, has an out-of-range field, or overflows the boundary calculation.
            AssertionError: If the value's length doesn't match one of the six recognized shapes (unreachable given the regex check above).
        """
        if not RESTORE_DATE_REGEX.match(point_in_time):
            msg = f"Invalid restore date: {point_in_time!r} (expected YYYY[MM[DD[THH[MM[SS]]]]])"
            raise ConfigurationError(msg)

        # Single source of truth: shape length determines both the start-of-period
        # padding and the unit to advance by one for the exclusive boundary.
        suffix: str
        unit: PeriodUnit
        match len(point_in_time):
            case 4:
                suffix, unit = "0101T000000", "years"
            case 6:
                suffix, unit = "01T000000", "months"
            case 8:
                suffix, unit = "T000000", "days"
            case 11:
                suffix, unit = "0000", "hours"
            case 13:
                suffix, unit = "00", "minutes"
            case 15:
                suffix, unit = "", "seconds"
            case _:
                # Unreachable: RESTORE_DATE_REGEX above only matches these six
                # lengths, so no other length can reach this branch. `len()`
                # returns plain `int`, not a Literal, so mypy can't prove this
                # via `assert_never`; fail loudly instead of a silent fallback.
                msg = f"Unhandled restore date length: {len(point_in_time)}"
                raise AssertionError(msg)

        try:
            period_start = PlainDateTime.parse(point_in_time + suffix, format=DEFAULT_DATE_PATTERN)
            # Advance inside the try: an out-of-range field (e.g. month 13) fails the
            # parse, and a boundary past PlainDateTime's max year (e.g. "9999" + 1 year)
            # fails the add. Both must surface as a clean ConfigurationError, not a raw
            # ValueError that escapes the CLI/container EZBakError handlers.
            boundary_plain = self._add_one_unit(period_start=period_start, unit=unit)
        except ValueError as e:
            msg = f"Invalid restore date: {point_in_time!r}"
            raise ConfigurationError(msg) from e

        boundary = (
            boundary_plain.assume_tz(self.settings.tz)
            if self.settings.tz
            else boundary_plain.assume_system_tz()
        )
        return boundary.timestamp()

    def get_backup_as_of(self, point_in_time: str) -> Backup | None:
        """Find the newest backup at or before a point in time for point-in-time recovery.

        Select the most recent backup whose timestamp falls at or before the end of the
        period named by ``point_in_time``, so an older backup can be restored instead of
        only the latest. Accepts the no-dash filename timestamp shape at any granularity:
        ``YYYY``, ``YYYYMM``, ``YYYYMMDD``, ``YYYYMMDDTHH``, ``YYYYMMDDTHHMM``, or
        ``YYYYMMDDTHHMMSS``.

        Args:
            point_in_time (str): The moment to restore as of, e.g. "20250102".

        Returns:
            Backup | None: The newest qualifying backup, or None when none is old enough.
        """
        boundary = self._resolve_upper_boundary(point_in_time)

        candidates = [
            backup for backup in self.list_backups() if backup.zoned_datetime.timestamp() < boundary
        ]
        if not candidates:
            logger.error(f"No backup at or before {point_in_time}")
            return None

        selected = max(candidates, key=lambda b: b.zoned_datetime.timestamp())
        logger.debug(f"Selected backup as of {point_in_time}: {selected.name}")
        return selected

    def prune_backups(self, *, dry_run: bool = False) -> list[Backup]:
        """Remove old backup files according to configured retention policies to manage storage usage.

        Delete excess backup files while preserving the most important backups based on the retention policy configuration. Use this to automatically clean up old backups and prevent unlimited storage growth while maintaining appropriate historical coverage.

        Args:
            dry_run (bool): Report the backups that would be deleted without removing anything. Use this to preview the impact of a prune before running it for real. Defaults to False.

        Returns:
            list[Backup]: A list of backup objects targeted for deletion by the retention policy.
        """
        logger.trace("Pruning backups")
        backups_to_delete = self._identify_backups_to_delete()
        logger.debug(
            f"Prune targets ({len(backups_to_delete)}): {[x.name for x in backups_to_delete]}"
        )

        # Dry run stops here: the caller inspects the returned targets (the CLI lists
        # them per-file) and nothing is deleted.
        if dry_run:
            logger.info(
                f"Dry run: would prune {len(backups_to_delete)} backups across {len(self.storage_locations)} storage locations"
            )
            return backups_to_delete

        total_deleted = 0
        for backend in self.backends:
            targets = [x for x in backups_to_delete if x.storage_type == backend.storage_type]
            try:
                total_deleted += backend.delete_many(targets)
            except StorageDeleteError as e:
                # Tolerate a failing backend so one unhealthy destination does not
                # block pruning the others; the backend already logged the details.
                logger.error(f"Pruning failed for {backend.storage_type.value}: {e}")

        logger.info(
            f"Pruned {total_deleted} backups across {len(self.storage_locations)} storage locations"
        )

        logger.trace("Require storage location re-index on next call")
        self.rebuild_storage_locations = True
        return backups_to_delete

    def restore_backup(
        self,
        restore_path: Path | str | None = None,
        *,
        clean_before_restore: bool = False,
        backup: Backup | None = None,
    ) -> bool:
        """Restore the latest or specified backup to `restore_path`.

        Decompress and extract the latest backup archive to recover files and directories to their original structure. Use this for disaster recovery, file restoration, or migrating backup contents to a new location.

        Args:
            restore_path (Path | str | None): Target directory to restore into. When None, restore the latest backup to its original path or default target. Defaults to None.
            clean_before_restore (bool): Remove existing contents at the target before restoring. Defaults to False.
            backup (Backup | None): Restore this specific backup instead of selecting one. When None, use the configured restore_date if set, else the latest backup. Defaults to None.

        Returns:
            bool: True when a backup is successfully restored; False when there is no
                backup to restore.

        Raises:
            ConfigurationError: If the destination is not provided and no restore directory is configured, or the destination does not exist or is not a directory.
        """
        destination = restore_path or self.settings.restore_path

        try:
            dest = Path(destination).expanduser().absolute()
        except (TypeError, ValueError, OSError, RuntimeError) as e:
            # Covers a None/bad-type destination (TypeError), an unresolvable ~ home
            # (RuntimeError from expanduser), and a removed cwd for a relative path
            # (OSError from absolute()), all of which mean the destination is unusable.
            msg = f"Invalid destination: {destination}"
            raise ConfigurationError(msg) from e

        if not dest:
            msg = "No destination provided and no restore directory configured"
            raise ConfigurationError(msg)

        if not dest.exists() or not dest.is_dir():
            msg = f"Restore destination does not exist: {dest}"
            raise ConfigurationError(msg)

        # A blank restore_date (empty or whitespace, e.g. an unset EZBAK_RESTORE_DATE
        # templated to "") means no point in time was requested: fall through to the
        # latest backup, consistently for "" and "  ", rather than one silently
        # restoring latest while the other raises on the whitespace.
        restore_date = (self.settings.restore_date or "").strip()

        # Precedence: an explicit Backup wins; else a configured restore_date selects a
        # point in time; else the latest. Confirm a target before cleaning, so
        # clean_before_restore never empties the destination with nothing to restore.
        if backup is not None:
            target = backup
        elif restore_date:
            target = self.get_backup_as_of(restore_date)
            # get_backup_as_of already logged the miss. Fail rather than silently
            # falling back to the newest backup, which would restore the wrong data.
            if target is None:
                return False
        else:
            target = self.get_latest_backup()
            if target is None:
                logger.error("No backup found to restore")
                return False

        if clean_before_restore or self.settings.clean_before_restore:
            clean_directory(dest)
            logger.info("Cleaned all files in backup destination before restore")

        return self._do_restore(backup=target, destination=dest)
