"""Configuration for pytest."""

import os
import shutil
from collections.abc import Generator
from pathlib import Path

import pytest
from nclutils.pytest_fixtures import clean_stderr, clean_stdout, debug  # noqa: F401

from ezbak.constants import DEFAULT_COMPRESSION_LEVEL
from ezbak.models.settings import settings


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
def reset_settings() -> None:
    """Reset the settings singleton."""
    for key in settings.__dict__:
        if key == "compression_level":
            setattr(settings, key, DEFAULT_COMPRESSION_LEVEL)
        elif key == "label_time_units":
            setattr(settings, key, True)
        elif key == "rename_files":
            setattr(settings, key, False)
        elif key == "log_level":
            setattr(settings, key, "INFO")
        else:
            setattr(settings, key, None)


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Mock environment variables for testing."""
    for k in os.environ:
        if k.startswith("EZBAK_"):
            monkeypatch.delenv(k, raising=False)
