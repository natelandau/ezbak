"""SHA-256 sidecar helpers for verifying backup archive integrity.

Single source of truth for how a backup's checksum sidecar is named, formatted,
parsed, and computed, so the create and restore paths never disagree on the shape.
"""

import hashlib
from pathlib import Path

from ezbak.constants import CHECKSUM_EXTENSION

# Read archives in 64 KiB blocks so a multi-gigabyte file is never held in memory.
_CHUNK_SIZE = 65536
_DIGEST_LEN = 64


def sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 of a file, read in fixed-size chunks.

    Use to fingerprint a finished archive at creation and to re-fingerprint it on
    restore, streaming so archive size does not drive memory use.

    Args:
        path (Path): The file to hash.

    Returns:
        str: The lowercase hex digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sidecar_name(archive_name: str) -> str:
    """Return the sidecar filename for an archive filename.

    Args:
        archive_name (str): The archive's final filename.

    Returns:
        str: The archive name with the checksum extension appended.
    """
    return f"{archive_name}.{CHECKSUM_EXTENSION}"


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
