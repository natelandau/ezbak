"""Configuration for pytest."""

import os
import shutil
from collections.abc import Generator
from pathlib import Path
from unittest import mock

import pytest
from nclutils.pytest_fixtures import clean_stderr, clean_stdout, debug  # noqa: F401


@pytest.fixture
def filesystem(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a test filesystem with source and destination directories.

    Creates a source directory with test files and subdirectories, plus two
    destination directories for backup testing.

    Yields:
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

    yield src_dir, dest1, dest2
    shutil.rmtree(src_dir)
    shutil.rmtree(dest1)
    shutil.rmtree(dest2)


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Mock environment variables for testing."""
    with mock.patch.dict(os.environ, clear=True):
        monkeypatch.setenv("EZBAK_NAME", "")
        monkeypatch.setenv("EZBAK_DESTINATIONS", "")
        monkeypatch.setenv("EZBAK_EXCLUDE_REGEX", "")
        monkeypatch.setenv("EZBAK_INCLUDE_REGEX", "")
        monkeypatch.setenv("EZBAK_LOG_FILE", "")
        monkeypatch.setenv("EZBAK_LOG_LEVEL", "")
        monkeypatch.setenv("EZBAK_NAME", "")
        monkeypatch.setenv("EZBAK_SOURCES", "")
        monkeypatch.setenv("EZBAK_TIME_BASED_POLICY", "")
        monkeypatch.setenv("EZBAK_TZ", "")
        yield
