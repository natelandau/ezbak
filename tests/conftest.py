"""Configuration for pytest."""

import os
from pathlib import Path

import pytest
from nclutils import logger
from nclutils.pytest_fixtures import clean_stderr, clean_stdout, debug  # noqa: F401


@pytest.fixture
def filesystem(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a test filesystem with source and destination directories.

    Creates a source directory with test files and subdirectories, plus two
    destination directories for backup testing.

    Returns:
        tuple[Path, Path, Path]: Source directory, destination1, destination2
    """
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    Path(src_dir / "foo.txt").touch()
    Path(src_dir / "bar.txt").touch()
    Path(src_dir / "baz.txt").touch()
    Path(src_dir / "dir1").mkdir(parents=True, exist_ok=True)
    Path(src_dir / "dir1" / "foo.txt").touch()
    Path(src_dir / "dir1" / "bar.txt").touch()
    Path(src_dir / "dir1" / "baz.txt").touch()

    dest1 = tmp_path / "dest1"
    dest2 = tmp_path / "dest2"
    dest1.mkdir(parents=True, exist_ok=True)
    dest2.mkdir(parents=True, exist_ok=True)

    return src_dir, dest1, dest2


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Mock environment variables for testing."""
    for k in os.environ:
        if k.startswith("EZBAK_"):
            monkeypatch.delenv(k, raising=False)

    monkeypatch.setenv("EZBAK_TZ", "Etc/UTC")


@pytest.fixture(autouse=True)
def reset_logger():
    """Reset logger handlers between tests to prevent closed file handle issues.

    The logger from nclutils persists across test runs as a singleton. When tests
    configure logging with file handlers, these handlers can point to closed files
    in subsequent test runs, causing ValueError: I/O operation on closed file.
    This fixture removes all handlers after each test to ensure clean state.
    """
    yield
    logger.remove()
