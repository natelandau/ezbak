"""Tests for the checksum sidecar helpers."""

import hashlib
from pathlib import Path

import pytest

from ezbak.checksums import (
    HashingReader,
    HashingWriter,
    format_sidecar,
    parse_sidecar,
    sha256_file,
    sidecar_name,
)


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    """Verify sha256_file computes the same digest as hashlib."""
    # Given: a file with known bytes
    f = tmp_path / "a.tgz"
    f.write_bytes(b"hello world" * 1000)
    # When/Then: sha256_file equals a one-shot hashlib digest
    assert sha256_file(f) == hashlib.sha256(f.read_bytes()).hexdigest()


def test_hashing_writer_tees_bytes_to_sink_and_digest(tmp_path: Path) -> None:
    """Verify HashingWriter writes bytes through to the sink and digests them in one pass."""
    # Given: a payload written through a HashingWriter in several chunks
    payload = b"the quick brown fox" * 5000
    hasher = hashlib.sha256()
    sink = tmp_path / "out.bin"

    # When: writing chunk by chunk
    with sink.open("wb") as raw:
        writer = HashingWriter(fileobj=raw, hasher=hasher)
        for start in range(0, len(payload), 4096):
            writer.write(payload[start : start + 4096])

    # Then: the sink holds the bytes and the running digest matches a one-shot hash
    assert sink.read_bytes() == payload
    assert hasher.hexdigest() == hashlib.sha256(payload).hexdigest()


def test_hashing_reader_returns_underlying_bytes(tmp_path: Path) -> None:
    """Verify HashingReader.read returns exactly the bytes it reads from the source."""
    # Given: a source file wrapped by a HashingReader
    src = tmp_path / "in.bin"
    src.write_bytes(b"abcdefgh" * 100)

    # When: reading a fixed count
    with src.open("rb") as raw:
        reader = HashingReader(fileobj=raw, hasher=hashlib.sha256())
        chunk = reader.read(8)

    # Then: the bytes are passed through unchanged
    assert chunk == b"abcdefgh"


def test_hashing_reader_digests_whole_file_after_drain(tmp_path: Path) -> None:
    """Verify HashingReader plus drain digests the whole file even when the consumer stops early."""
    # Given: a file whose consumer reads only the head, mimicking tarfile leaving the gzip tail unread
    payload = b"lazy dog jumps" * 5000
    src = tmp_path / "in.bin"
    src.write_bytes(payload)
    hasher = hashlib.sha256()

    # When: reading only the first part, then draining the remainder through the hash
    with src.open("rb") as raw:
        reader = HashingReader(fileobj=raw, hasher=hasher)
        reader.read(1000)
        reader.drain()

    # Then: the digest covers the entire file
    assert hasher.hexdigest() == hashlib.sha256(payload).hexdigest()


def test_sidecar_name_appends_extension() -> None:
    """Verify sidecar_name appends the checksum extension."""
    assert sidecar_name("test-20250609T000000.tgz") == "test-20250609T000000.tgz.sha256"


def test_format_and_parse_round_trip() -> None:
    """Verify format_sidecar and parse_sidecar round-trip correctly."""
    # Given: a digest and archive name
    digest = "a" * 64
    content = format_sidecar(digest, "test.tgz")
    # Then: sha256sum text format, parseable back to the digest
    assert content == f"{digest}  test.tgz\n"
    assert parse_sidecar(content) == digest


@pytest.mark.parametrize("bad", ["", "   ", "nothex " * 8, "abc  file", "z" * 64, "a" * 63])
def test_parse_sidecar_rejects_malformed(bad: str) -> None:
    """Verify parse_sidecar rejects malformed input and returns None."""
    assert parse_sidecar(bad) is None


def test_parse_sidecar_normalizes_uppercase_digest() -> None:
    """Verify parse_sidecar accepts an uppercase digest and lowercases it."""
    # Given: a sha256sum-style line with an uppercase hex digest
    content = format_sidecar("A" * 64, "x.tgz")
    # When/Then: the parsed digest is normalized to lowercase
    assert parse_sidecar(content) == "a" * 64
