"""Open files that another principal can replace, without following a planted link."""

import os
import stat
from pathlib import Path
from typing import BinaryIO

_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
# O_NONBLOCK so a name swapped for a FIFO cannot block the open forever.
NO_FOLLOW_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _CLOEXEC


def open_regular_file(path: Path) -> BinaryIO:
    """Open a regular file for reading, refusing a symlink, FIFO, or device at its name.

    Call this for any file in a directory that another job, host, or service can write to,
    so a planted link or special file cannot make ezbak read something else or hang.

    Args:
        path (Path): The file to open.

    Returns:
        BinaryIO: The open file.

    Raises:
        OSError: If `path` is a symlink, cannot be opened, or is not a regular file.
    """
    f = os.fdopen(fd=os.open(path=path, flags=NO_FOLLOW_READ_FLAGS), mode="rb")
    try:
        mode = os.fstat(f.fileno()).st_mode
    except OSError:
        f.close()
        raise
    if not stat.S_ISREG(mode):
        f.close()
        msg = f"Expected '{path}' to be a regular file, found file type {stat.S_IFMT(mode):#o}"
        raise OSError(msg)
    return f
