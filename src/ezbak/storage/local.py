"""Local-filesystem storage backend for backup operations."""

import glob
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from loguru import logger
from nclutils.utils import new_uid

from ezbak.backup import Backup, StorageLocation
from ezbak.checksums import sidecar_name
from ezbak.constants import (
    BACKUP_EXTENSION,
    COPY_CHUNK_SIZE,
    COPY_FSYNC_INTERVAL,
    StorageType,
)
from ezbak.exceptions import ConfigurationError, StorageDeleteError, StorageWriteError
from ezbak.filters import validate_storage_paths
from ezbak.no_follow import open_regular_file
from ezbak.storage.base import StorageBackend

# O_EXCL refuses any existing name, a planted symlink included, and
# O_NOFOLLOW guards the same case on filesystems with weak O_EXCL semantics.
_CREATE_NEW_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW


def _publish_new_file(*, dst: Path, write: Callable[[BinaryIO], object], mode: int) -> None:
    """Write a fresh regular file and atomically publish it as `dst`, never following a link.

    Use this for every write into a storage directory, which other principals can write to.
    A failed or killed write never leaves a truncated file under the final name.

    Args:
        dst (Path): The final path to publish.
        write (Callable[[BinaryIO], object]): Writes the content into the open staging file.
        mode (int): Permission bits for the published file.

    Raises:
        OSError: If the write or rename fails, or if `dst` is not the staged file after the
            rename; the staging file and any link or special file at `dst` are removed
            before raising.
    """
    # Unique per writer so concurrent backups to the same destination never clobber each
    # other's staging file; the leading dot keeps it out of the `{name}-*.tgz` index glob.
    tmp_dst = dst.with_name(f".{dst.name}.{new_uid(bits=64)}.partial")
    try:
        written = _write_staging_file(path=tmp_dst, write=write, mode=mode)
        tmp_dst.replace(dst)
        _require_published_file(dst=dst, written=written)
    except OSError:
        tmp_dst.unlink(missing_ok=True)
        raise


def _write_staging_file(
    *, path: Path, write: Callable[[BinaryIO], object], mode: int
) -> os.stat_result:
    """Create `path` exclusively, fill it, and set its mode through the open descriptor.

    Args:
        path (Path): The staging path, which must not exist.
        write (Callable[[BinaryIO], object]): Writes the content into the open file.
        mode (int): Permission bits to set.

    Returns:
        os.stat_result: The fstat of the written file, to check the published name against.
    """
    # 0o600 keeps the content private until fchmod sets the final mode.
    fd = os.open(path=path, flags=_CREATE_NEW_FILE_FLAGS, mode=0o600)
    with os.fdopen(fd=fd, mode="wb") as f:
        write(f)
        os.fchmod(fd=f.fileno(), mode=mode)
        return os.fstat(f.fileno())


def _require_published_file(*, dst: Path, written: os.stat_result) -> None:
    """Raise unless `dst` is the file ezbak just wrote, removing it if it is not a regular file.

    A storage-directory writer can swap the staging file for a link between the
    write and the rename. Checking after the rename, not before, leaves no window
    in which the swapped name stays published.

    Args:
        dst (Path): The just-published path.
        written (os.stat_result): The fstat of the file ezbak wrote.

    Raises:
        OSError: If `dst` is a different file than the one ezbak wrote.
    """
    published = dst.lstat()
    # Positional: Python 3.15 makes samestat's parameters positional-only.
    if os.path.samestat(published, written):
        return
    # A regular file here is a concurrent writer's complete copy under the same
    # name, so leave it published; only a link or special file is removed.
    if not stat.S_ISREG(published.st_mode):
        dst.unlink(missing_ok=True)
    msg = (
        f"Refusing to publish '{dst}': it is not the file ezbak wrote "
        f"(expected device {written.st_dev} inode {written.st_ino}, "
        f"found device {published.st_dev} inode {published.st_ino})"
    )
    raise OSError(msg)


def copy_with_periodic_fsync(
    *,
    src: Path,
    dst: Path,
    fsync_interval: int = COPY_FSYNC_INTERVAL,
    chunk_size: int = COPY_CHUNK_SIZE,
) -> None:
    """Copy `src` to `dst`, forcing dirty pages to storage every `fsync_interval` bytes.

    NFS has no per-cgroup writeback accounting, so a memory-limited container
    copying a large archive to an NFS destination accumulates dirty page cache
    charged to its cgroup until the kernel OOM-kills it; periodic fsync caps the
    dirty footprint at the interval size regardless of archive size. The final
    fsync also guarantees the archive is durable before the copy is reported
    successful.

    The copy is published with `_publish_new_file`, so a truncated archive never
    appears under the final backup name, where the next index would treat it as
    the newest backup and hand it to a restore.

    Args:
        src (Path): Source file to copy.
        dst (Path): Destination path for the copy.
        fsync_interval (int): Bytes written between forced flushes.
        chunk_size (int): Bytes read per iteration.
    """
    with src.open("rb") as fsrc:
        mode = stat.S_IMODE(os.fstat(fsrc.fileno()).st_mode)
        _publish_new_file(
            dst=dst,
            write=lambda fdst: _write_chunks_with_fsync(
                src=fsrc, dst=fdst, fsync_interval=fsync_interval, chunk_size=chunk_size
            ),
            mode=mode,
        )


def _write_chunks_with_fsync(
    *, src: BinaryIO, dst: BinaryIO, fsync_interval: int, chunk_size: int
) -> None:
    """Stream `src` into `dst` in chunks, fsyncing every `fsync_interval` bytes.

    Args:
        src (BinaryIO): Open source file.
        dst (BinaryIO): Open destination file.
        fsync_interval (int): Bytes written between forced flushes.
        chunk_size (int): Bytes read per iteration.
    """
    unsynced = 0
    while chunk := src.read(chunk_size):
        dst.write(chunk)
        unsynced += len(chunk)
        if unsynced >= fsync_interval:
            dst.flush()
            os.fsync(dst.fileno())
            unsynced = 0
    dst.flush()
    os.fsync(dst.fileno())


class LocalBackend(StorageBackend):
    """Back up to and manage archives on local filesystem directories."""

    storage_type = StorageType.LOCAL

    @property
    def _storage_paths(self) -> list[Path]:
        """Configured storage directories, which every local operation needs.

        Returns:
            list[Path]: The configured storage directories.

        Raises:
            ConfigurationError: If no storage paths are configured.
        """
        if not self.settings.storage_paths:
            msg = "Local storage needs at least one storage path, got none"
            raise ConfigurationError(msg)
        return self.settings.storage_paths

    def index(self) -> list[StorageLocation]:
        """Scan each configured storage directory for matching backup files.

        Returns:
            list[StorageLocation]: One storage location per configured storage path.
        """
        validate_storage_paths(storage_paths=self._storage_paths, create_if_missing=True)

        # A set name containing "/" lives in a subdirectory, so filter on the name as the set
        # spells it, not the bare filename. Take the directory from the set name rather than the
        # found path, which the glob normalizes ("./app" matches as "app").
        set_directory = self._set_name.rpartition("/")[0]
        name_prefix = f"{set_directory}/" if set_directory else ""
        locations: list[StorageLocation] = []
        for storage_path in self._storage_paths:
            logger.trace(f"Indexing: {storage_path}")
            # The glob only narrows the scan; `_backup_names` decides which files belong to the
            # set. A plain glob, not a filter that follows links, so every symlink at a backup
            # name, dangling or not, reaches the warning in `_is_regular_file`.
            found_files = list(
                storage_path.glob(f"{glob.escape(self._set_name)}-*.{BACKUP_EXTENSION}")
            )
            files_by_name = {f"{name_prefix}{f.name}": f for f in found_files}
            found_files = [
                files_by_name[n]
                for n in self._backup_names(list(files_by_name))
                if _is_regular_file(files_by_name[n])
            ]
            location = self._build_storage_location(
                storage_path=storage_path,
                backups=[
                    Backup(
                        storage_type=StorageType.LOCAL,
                        name=x.name,
                        path=x,
                        storage_path=storage_path,
                        tz=self.settings.tz,
                    )
                    for x in found_files
                ],
            )
            logger.debug(f"Indexed {len(location.backups)} existing backups in '{storage_path}'")
            for backup in location.backups:
                logger.trace(f"Indexed: {backup}")
            locations.append(location)

        return locations

    def write_locations(self) -> list[StorageLocation]:
        """Return one destination per configured storage directory, without scanning them.

        Returns:
            list[StorageLocation]: One naming context per configured storage path.
        """
        return [
            self._build_storage_location(storage_path=storage_path, backups=[])
            for storage_path in self._storage_paths
        ]

    def write(
        self, *, tmp_backup: Path, storage_location: StorageLocation, checksum: str | None
    ) -> Backup:
        """Copy the staged archive into the storage directory.

        Args:
            tmp_backup (Path): The staged archive to copy.
            storage_location (StorageLocation): The destination directory and naming context.
            checksum (str | None): Precomputed hex SHA-256 to store as a sidecar, or
                None to skip sidecar creation.

        Returns:
            Backup: The created backup.

        Raises:
            StorageWriteError: If the storage location has no directory or the copy fails.
        """
        if storage_location.storage_path is None:
            msg = f"Local write needs a storage directory, got none for '{storage_location.name}'"
            raise StorageWriteError(msg)
        backup_name = storage_location.generate_new_backup_name()
        backup_path = Path(storage_location.storage_path) / backup_name
        logger.debug(f"Copy tmp backup to local: {backup_path}")
        try:
            copy_with_periodic_fsync(src=tmp_backup, dst=backup_path)
        except OSError as e:
            msg = f"Local write failed for '{backup_path}': {e}"
            logger.error(msg)
            raise StorageWriteError(msg) from e
        logger.info(f"Created: {backup_path}")

        backup = Backup(
            storage_type=StorageType.LOCAL,
            name=backup_path.name,
            path=backup_path,
            storage_path=storage_location.storage_path,
            tz=self.settings.tz,
        )
        self._store_sidecar(backup=backup, checksum=checksum)
        return backup

    def delete(self, backup: Backup) -> bool:
        """Unlink a local backup file, tolerating one already removed elsewhere.

        Also removes the archive's .sha256 sidecar, best-effort and idempotent
        for a backup with no sidecar.

        Args:
            backup (Backup): The backup whose file should be removed.

        Returns:
            bool: True if the file was unlinked, False if it was already gone.

        Raises:
            StorageDeleteError: If the backup has no file path.
        """
        path = backup.path
        if path is None:
            msg = f"Local delete needs a file path, got none for backup '{backup.name}'"
            raise StorageDeleteError(msg)
        # Catch instead of missing_ok so the whole job does not abort when another
        # process already pruned this file from a shared storage location. Catch the
        # broader OSError (not just FileNotFoundError) so an NFS stale handle (ESTALE,
        # errno 116) degrades to a warning instead of aborting the job.
        try:
            path.unlink()
            logger.info(f"Deleted: {path}")
            self._remove_sidecar(backup)
        except OSError as e:
            logger.warning(f"Missing, not deleted: {path} (errno={e.errno}: {e.strerror})")
            # Forensics for shared-storage cache skew: if the directory listing still
            # shows this file after the unlink proved it gone, the client mount cache
            # is serving a stale 'present' view rather than racing a live delete.
            try:
                parent = path.parent
                still_listed = path.name in {p.name for p in parent.iterdir()}
                logger.debug(
                    f"Post-failure check: exists()={path.exists()}, "
                    f"present_in_parent_listing={still_listed}, parent={parent}"
                )
            except OSError as check_error:
                logger.debug(f"Post-failure check failed: {check_error}")
            return False
        return True

    def _write_sidecar(self, *, backup: Backup, content: str) -> None:  # ruff:ignore[no-self-use]
        """Write the sidecar file next to a local archive; a failure warns and is tolerated.

        Args:
            backup (Backup): The backup the sidecar belongs to.
            content (str): The sidecar file content.
        """
        if backup.path is None:
            return
        sidecar_path = backup.path.parent / sidecar_name(backup.path.name)
        try:
            # Match the archive's mode so the sidecar is never more readable than it. A
            # link swapped in at the archive name reports 0o777, so fall back to private.
            archive_st_mode = backup.path.lstat().st_mode
            archive_mode = stat.S_IMODE(archive_st_mode) if stat.S_ISREG(archive_st_mode) else 0o600
            _publish_new_file(
                dst=sidecar_path, write=lambda f: f.write(content.encode()), mode=archive_mode
            )
        except OSError as e:
            # Best-effort: the archive is intact, just unverifiable later.
            logger.warning(f"Failed to write checksum sidecar '{sidecar_path}': {e}")

    def _remove_sidecar(self, backup: Backup) -> None:  # ruff:ignore[no-self-use]
        """Remove the checksum sidecar next to a local archive; tolerate its absence.

        Args:
            backup (Backup): The backup whose sidecar should be removed.
        """
        if backup.path is None:
            return
        sidecar_path = backup.path.parent / sidecar_name(backup.path.name)
        try:
            sidecar_path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"Failed to delete checksum sidecar '{sidecar_path}': {e}")

    def delete_many(self, backups: list[Backup]) -> list[Backup]:
        """Delete each local backup individually.

        Args:
            backups (list[Backup]): The backups to remove.

        Returns:
            list[Backup]: The backups confirmed removed from disk.
        """
        logger.debug(f"Deleting {len(backups)} local backups")
        return [backup for backup in backups if self.delete(backup)]

    def prepare_for_restore(self, backup: Backup) -> Path | None:  # ruff:ignore[no-self-use]
        """Return the on-disk path of a local backup.

        Args:
            backup (Backup): The backup to restore.

        Returns:
            Path | None: The local archive path.
        """
        logger.info(f"Restoring backup from local: {backup.name}")
        return backup.path

    def _read_sidecar(self, backup: Backup) -> str | None:  # ruff:ignore[no-self-use]
        """Return the raw sidecar content next to a local archive, or None if unreadable.

        Args:
            backup (Backup): The backup to look up.

        Returns:
            str | None: The sidecar content, or None if it is absent or unreadable.
        """
        if backup.path is None:
            return None
        sidecar_path = backup.path.parent / sidecar_name(backup.path.name)
        logger.trace(f"Reading checksum sidecar '{sidecar_path}'")
        try:
            with open_regular_file(sidecar_path) as f:
                return f.read().decode()
        except FileNotFoundError:
            logger.trace(f"No checksum sidecar at '{sidecar_path}'")
            return None
        except (OSError, UnicodeDecodeError) as e:
            # UnicodeDecodeError is a ValueError subclass, not an OSError, so it
            # needs its own clause: a bit-rotted sidecar must warn and proceed,
            # not crash the restore.
            logger.warning(f"Could not read checksum sidecar '{sidecar_path}': {e}")
            return None


def _is_regular_file(path: Path) -> bool:
    """Return True if `path` is a regular file itself, warning about anything else.

    A link in a storage directory could point a restore at any file the container can read,
    so only a regular file at the name is a backup.

    Args:
        path (Path): A file found by the index glob.

    Returns:
        bool: True if `path` is a regular file and not a link to one.
    """
    try:
        mode = path.lstat().st_mode
    except OSError as e:
        logger.warning(f"Skipping '{path}': cannot read its file type: {e}")
        return False
    if stat.S_ISREG(mode):
        return True
    logger.warning(f"Skipping '{path}': expected a regular file, not a symlink or special file")
    return False
