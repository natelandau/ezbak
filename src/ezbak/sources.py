"""Descriptor-based access to configured backup sources.

The backed-up application can rewrite its own data directory while ezbak runs, so a path
checked once and reused later can point somewhere else by the time it is read or deleted.
Every operation here works on file descriptors opened without following symlinks: a source
is opened one path component at a time, the archive walk opens each entry relative to its
parent, and deletion removes entries through the same descriptors. Type checks, archived
content, and deletions therefore all apply to the inode that was opened.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, cast

from loguru import logger

from ezbak.exceptions import ConfigurationError
from ezbak.no_follow import NO_FOLLOW_READ_FLAGS

if TYPE_CHECKING:
    import tarfile
    from collections.abc import Callable, Generator

    ArchiveNameFilter = Callable[[str], bool]

_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIR_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _CLOEXEC
# The directories above a source are opened for search only, so reaching a source needs
# only execute permission on its parents, as a path lookup does. Linux spells this O_PATH
# and macOS O_SEARCH.
_SEARCH_ONLY = getattr(os, "O_PATH", 0) or getattr(os, "O_SEARCH", 0) or os.O_RDONLY
_TRAVERSE_OPEN_FLAGS = _SEARCH_ONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _CLOEXEC
# Errors an open with O_NOFOLLOW reports when the entry was replaced after it was lstat'd.
_REPLACED_ERRNOS = frozenset({errno.ELOOP, errno.ENOTDIR, errno.ENOENT})


@dataclass(frozen=True)
class SourceHandle:
    """An open configured source, pinned to the inode it named when the run started.

    Hold one for the whole run so archiving and deletion act on the same directory or file,
    whatever the application does to the path meanwhile.
    """

    path: Path
    fd: int
    parent_fd: int
    is_dir: bool


class _DescriptorFile:
    """Expose an open descriptor to `TarFile.gettarinfo` so it fstat()s that inode.

    `open(fd)` cannot stand in because it refuses a directory descriptor.
    """

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self.name = ""

    def fileno(self) -> int:
        return self._fd


def _symlink_error(source: Path, component: Path) -> ConfigurationError:
    msg = (
        f"Source path {source} contains a symlink at {component}. "
        "ezbak does not follow symlinks in a source path; configure the real path instead"
    )
    return ConfigurationError(msg)


def _component_open_flags(
    st: os.stat_result, *, source: Path, component: Path, is_last: bool
) -> int:
    if stat.S_ISLNK(st.st_mode):
        raise _symlink_error(source, component)
    if stat.S_ISDIR(st.st_mode):
        return _DIR_OPEN_FLAGS if is_last else _TRAVERSE_OPEN_FLAGS
    if is_last and stat.S_ISREG(st.st_mode):
        return NO_FOLLOW_READ_FLAGS
    if is_last:
        msg = f"Not a file or directory: {source}"
    else:
        msg = f"Source path {source} has a non-directory at {component}"
    raise ConfigurationError(msg)


def _open_component(name: str, *, dir_fd: int, source: Path, component: Path, is_last: bool) -> int:
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError as e:
        msg = f"Source does not exist: {source}"
        raise ConfigurationError(msg) from e
    except OSError as e:
        msg = f"Cannot open source {source} at {component}: {e}"
        raise ConfigurationError(msg) from e

    flags = _component_open_flags(st, source=source, component=component, is_last=is_last)
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except OSError as e:
        # A component swapped for a symlink after the lstat above fails the open with ELOOP,
        # or with ENOTDIR when O_DIRECTORY is set alongside O_PATH on Linux.
        if e.errno in {errno.ELOOP, errno.ENOTDIR} and _is_symlink_now(name, dir_fd=dir_fd):
            raise _symlink_error(source, component) from e
        msg = f"Cannot open source {source} at {component}: {e}"
        raise ConfigurationError(msg) from e


def _is_symlink_now(name: str, *, dir_fd: int) -> bool:
    try:
        return stat.S_ISLNK(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode)
    except OSError:
        return False


@contextmanager
def open_source(path: Path) -> Generator[SourceHandle]:
    """Open a configured absolute source without following a symlink in any path component.

    Enter this at the start of a run and pass the handle to every later step, so a symlink
    the application plants at the source or at any parent directory cannot redirect the
    backup or the deletion of source files. The descriptors close when the block exits.
    A path that does not exist, contains a symlink, or is neither a regular file nor a
    directory raises ConfigurationError.

    Args:
        path (Path): The configured absolute source path.

    Yields:
        SourceHandle: The open source.
    """
    names = path.parts[1:]
    with ExitStack() as descriptors:
        # The root is its own parent, so a source of "/" still has a parent descriptor.
        root_flags = _TRAVERSE_OPEN_FLAGS if names else _DIR_OPEN_FLAGS
        parent_fd = current_fd = os.open(path.anchor, root_flags)
        descriptors.callback(os.close, current_fd)
        component = Path(path.anchor)
        for index, name in enumerate(names):
            component /= name
            parent_fd, current_fd = (
                current_fd,
                _open_component(
                    name,
                    dir_fd=current_fd,
                    source=path,
                    component=component,
                    is_last=index == len(names) - 1,
                ),
            )
            descriptors.callback(os.close, current_fd)

        yield SourceHandle(
            path=path,
            fd=current_fd,
            parent_fd=parent_fd,
            is_dir=stat.S_ISDIR(os.fstat(current_fd).st_mode),
        )


def _add_from_descriptor(tar: tarfile.TarFile, *, fd: int, arcname: str) -> tarfile.TarInfo | None:
    """Add the inode behind `fd` as one member, with metadata and data from that descriptor.

    Returns:
        tarfile.TarInfo | None: The member added, or None if it was skipped.
    """
    tarinfo = tar.gettarinfo(arcname=arcname, fileobj=cast("IO[bytes]", _DescriptorFile(fd)))
    if tarinfo is None or tarinfo.ischr() or tarinfo.isblk():
        logger.warning(f"Skip backup of special file: {arcname}")
        return None
    logger.trace("Add to tar: {}", arcname)
    if tarinfo.isreg():
        with open(fd, "rb", closefd=False) as fileobj:
            tar.addfile(tarinfo, fileobj)
    else:
        tar.addfile(tarinfo)
    return tarinfo


def add_regular_file(tar: tarfile.TarFile, *, fd: int, arcname: str) -> None:
    """Add an open regular file to the archive, reading metadata and data from its descriptor.

    Args:
        tar (tarfile.TarFile): The open archive.
        fd (int): Descriptor of the regular file.
        arcname (str): The member name in the archive.
    """
    _add_from_descriptor(tar, fd=fd, arcname=arcname)


def add_directory_contents(
    tar: tarfile.TarFile,
    *,
    dir_fd: int,
    dir_path: Path,
    arcname_prefix: str,
    keep_file: ArchiveNameFilter,
) -> None:
    """Recursively add every entry below an open directory, never following a symlink.

    Each entry is opened relative to its parent's descriptor with O_NOFOLLOW, and its
    archive metadata and data come from that descriptor. An entry swapped for a symlink
    mid-walk fails to open and is skipped instead of being read through the link. Every
    directory is walked; a file that `keep_file` rejects is never opened.

    Args:
        tar (tarfile.TarFile): The open archive.
        dir_fd (int): Descriptor of the directory to walk.
        dir_path (Path): The directory's path, used in logs and filter paths.
        arcname_prefix (str): Archive name of the directory, or "" to add entries at the top.
        keep_file (ArchiveNameFilter): Decide by archive name whether to archive a
            non-directory entry.
    """
    # scandir's directory-entry type rules out symlinks and special files without a stat.
    with os.scandir(dir_fd) as listing:
        entries = sorted(listing, key=lambda entry: entry.name)
    for entry in entries:
        arcname = f"{arcname_prefix}/{entry.name}" if arcname_prefix else entry.name
        _add_entry(
            tar,
            dir_fd=dir_fd,
            entry=entry,
            path=dir_path / entry.name,
            arcname=arcname,
            keep_file=keep_file,
        )


def _is_archivable_type(entry: os.DirEntry[str]) -> bool:
    if entry.is_dir(follow_symlinks=False) or entry.is_file(follow_symlinks=False):
        return True
    # Opening a device node can have side effects, and a socket has nothing to archive.
    return stat.S_ISFIFO(entry.stat(follow_symlinks=False).st_mode)


def _open_entry(
    *,
    dir_fd: int,
    entry: os.DirEntry[str],
    path: Path,
    arcname: str,
    keep_file: ArchiveNameFilter,
) -> int | None:
    """Open a walk entry relative to its parent without following a symlink.

    Returns:
        int | None: The open descriptor, or None when the entry is not archived.
    """
    if entry.is_symlink():
        logger.warning(f"Skip backup of symlink: {path}")
        return None
    if not _is_archivable_type(entry):
        logger.warning(f"Skip backup of special file: {path}")
        return None
    if entry.is_dir(follow_symlinks=False):
        return os.open(entry.name, _DIR_OPEN_FLAGS, dir_fd=dir_fd)
    if not keep_file(arcname):
        return None
    return os.open(entry.name, NO_FOLLOW_READ_FLAGS, dir_fd=dir_fd)


def _add_entry(
    tar: tarfile.TarFile,
    *,
    dir_fd: int,
    entry: os.DirEntry[str],
    path: Path,
    arcname: str,
    keep_file: ArchiveNameFilter,
) -> None:
    try:
        fd = _open_entry(
            dir_fd=dir_fd, entry=entry, path=path, arcname=arcname, keep_file=keep_file
        )
    except OSError as e:
        if e.errno in _REPLACED_ERRNOS:
            logger.warning(f"Skip entry replaced during backup: {path}")
            return
        raise
    if fd is None:
        return

    try:
        added = _add_from_descriptor(tar, fd=fd, arcname=arcname)
        if added is not None and added.isdir():
            add_directory_contents(
                tar, dir_fd=fd, dir_path=path, arcname_prefix=arcname, keep_file=keep_file
            )
    finally:
        os.close(fd)


def delete_source_contents(handle: SourceHandle) -> None:
    """Delete an opened source: every entry inside a directory, or the file itself.

    Deletion goes through the descriptors opened at the start of the run, so it acts on the
    directory or file that was archived, whatever now sits at the path. A directory is
    emptied completely, including entries the filters left out of the archive. A failure
    to remove one entry is logged and the rest are still removed, because the backup itself
    already succeeded.

    Args:
        handle (SourceHandle): The source opened for this run.
    """
    if handle.is_dir:
        for name in os.listdir(handle.fd):  # ruff:ignore[os-listdir]
            _delete_entry(dir_fd=handle.fd, name=name, path=handle.path / name)
        logger.info(f"Cleaned source: {handle.path}")
        return

    name = handle.path.name
    try:
        current = os.stat(name, dir_fd=handle.parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        logger.warning(f"Source already removed: {handle.path}")
        return
    if not os.path.samestat(current, os.fstat(handle.fd)):
        logger.error(f"Refuse to delete {handle.path}: it was replaced after the backup")
        return
    try:
        os.unlink(name, dir_fd=handle.parent_fd)
    except OSError as e:
        logger.error(f"Failed to delete source {handle.path}: {e}")
        return
    logger.info(f"Deleted source: {handle.path}")


def _delete_entry(*, dir_fd: int, name: str, path: Path) -> None:
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if stat.S_ISDIR(st.st_mode):
            # Descriptor-based rmtree refuses to descend through a symlink swapped in mid-delete.
            shutil.rmtree(name, dir_fd=dir_fd)
        else:
            os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        return
    except OSError as e:
        logger.error(f"Failed to delete source entry {path}: {e}")
