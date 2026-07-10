"""SHA-256 sidecar helpers for verifying backup archive integrity.

Single source of truth for how a backup's checksum sidecar is named, formatted,
parsed, and computed, so the create and restore paths never disagree on the shape.
"""

from typing import IO, Protocol

from ezbak.constants import CHECKSUM_EXTENSION

# Read archives in 64 KiB blocks so a multi-gigabyte file is never held in memory.
_CHUNK_SIZE = 65536
_DIGEST_LEN = 64


class _Hasher(Protocol):
    """The slice of a hashlib object the tee wrappers depend on."""

    def update(self, data: bytes, /) -> None: ...


class HashingWriter:
    """Write-through file wrapper that digests bytes as they are written.

    Wrap an archive's output file so its digest is computed during the single
    write pass tarfile already makes, instead of re-reading the finished archive.
    Dropping that second full read keeps a multi-gigabyte backup from inflating
    the container's page-cache footprint (the checksum re-read was OOM-killing it).

    Only ``write`` carries data; ``tell``/``flush`` exist because the gzip layer
    tarfile wraps around this object may call them.
    """

    def __init__(self, fileobj: IO[bytes], hasher: _Hasher) -> None:
        self._fileobj = fileobj
        self._hasher = hasher

    def write(self, data: bytes) -> int:
        """Digest `data`, then write it through to the wrapped file.

        Returns:
            int: The number of bytes written.
        """
        self._hasher.update(data)
        return self._fileobj.write(data)

    def tell(self) -> int:
        """Return the wrapped file's current position."""
        return self._fileobj.tell()

    def flush(self) -> None:
        """Flush the wrapped file."""
        self._fileobj.flush()


class HashingReader:
    """Read-through file wrapper that digests bytes as they are read.

    Wrap an archive's input file so its digest is computed during extraction's
    single read pass, instead of a separate verify-then-extract double read. Call
    ``drain`` once the consumer stops (tarfile stops at the tar end-of-archive and
    leaves the gzip trailer, or everything past a corrupt header, unread) so the
    digest still covers every byte of the file.
    """

    def __init__(self, fileobj: IO[bytes], hasher: _Hasher) -> None:
        self._fileobj = fileobj
        self._hasher = hasher

    def read(self, size: int = -1) -> bytes:
        """Read up to `size` bytes from the wrapped file, digesting them first.

        Returns:
            bytes: The bytes read (empty at EOF).
        """
        data = self._fileobj.read(size)
        self._hasher.update(data)
        return data

    def drain(self) -> None:
        """Feed any bytes the consumer left unread through the hash, up to EOF."""
        for chunk in iter(lambda: self._fileobj.read(_CHUNK_SIZE), b""):
            self._hasher.update(chunk)


def sidecar_name(archive_name: str) -> str:
    """Return the sidecar filename for an archive filename.

    Args:
        archive_name (str): The archive's final filename.

    Returns:
        str: The archive name with the checksum extension appended.
    """
    return f"{archive_name}.{CHECKSUM_EXTENSION}"


def is_sidecar(name: str) -> bool:
    """Report whether a filename or object key is a checksum sidecar.

    Use so backends that discover objects by listing (rather than by a typed
    glob) share one definition of "this is a sidecar, not a backup" instead of
    re-encoding the extension check.

    Args:
        name (str): The filename or object key to test.

    Returns:
        bool: True if the name is a checksum sidecar.
    """
    return name.endswith(f".{CHECKSUM_EXTENSION}")


def format_sidecar(digest: str, archive_name: str) -> str:
    """Return sha256sum-compatible sidecar content for an archive.

    The two-space separator is the coreutils text-mode format, so an operator can
    verify a backup with the standard ``sha256sum -c`` tool.

    Args:
        digest (str): The lowercase hex digest.
        archive_name (str): The archive's final filename.

    Returns:
        str: A single sha256sum line ending in a newline.
    """
    return f"{digest}  {archive_name}\n"


def parse_sidecar(content: str) -> str | None:
    """Return the hex digest from sidecar content, or None when it is unusable.

    A truncated or garbled sidecar should degrade to "no usable checksum" so the
    restore warns and proceeds rather than crashing on a bad file.

    Args:
        content (str): The raw sidecar file content.

    Returns:
        str | None: The lowercase 64-char hex digest, or None if malformed.
    """
    parts = content.split()
    token = parts[0].lower() if parts else ""
    if len(token) == _DIGEST_LEN and all(c in "0123456789abcdef" for c in token):
        return token
    return None
