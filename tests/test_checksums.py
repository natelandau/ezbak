"""Tests for the checksum sidecar helpers."""

import hashlib
from pathlib import Path

import pytest

from ezbak.checksums import format_sidecar, parse_sidecar, sha256_file, sidecar_name


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    """Verify sha256_file computes the same digest as hashlib."""
    # Given: a file with known bytes
    f = tmp_path / "a.tgz"
    f.write_bytes(b"hello world" * 1000)
    # When/Then: sha256_file equals a one-shot hashlib digest
    assert sha256_file(f) == hashlib.sha256(f.read_bytes()).hexdigest()


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


@pytest.mark.parametrize("bad", ["", "   ", "nothex " * 8, "abc  file", "z" * 64])
def test_parse_sidecar_rejects_malformed(bad: str) -> None:
    """Verify parse_sidecar rejects malformed input and returns None."""
    assert parse_sidecar(bad) is None
