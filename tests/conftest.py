"""Configuration for pytest."""

import os
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest
from loguru import logger
from moto import mock_aws
from pytest_mock import MockerFixture

from ezbak import ezbak
from ezbak.core import EZBak
from ezbak.env import EnvConfig
from ezbak.storage.aws import AWSService
from tests.helpers import LEGACY_BACKUP_NAMES, access_denied


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


@pytest.fixture
def s3_bucket(monkeypatch: pytest.MonkeyPatch) -> Generator[str, None, None]:
    """Provide a live in-memory S3 bucket via moto and yield its name.

    moto intercepts every boto3 S3 call made while the context is open, so an
    AWSService or EZBak built inside a test that uses this fixture talks to a real,
    empty, in-memory bucket instead of AWS. AWSService builds its boto3 client
    without an explicit region, so AWS_DEFAULT_REGION is set for botocore.

    Yields:
        str: The name of the created in-memory bucket, `"test-bucket"`.
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="test-bucket")
        yield "test-bucket"


@pytest.fixture(autouse=True)  # ruff:ignore[pytest-fixture-autouse]
def mock_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Mock environment variables for testing."""
    for k in [k for k in os.environ if k.startswith("EZBAK_")]:
        monkeypatch.delenv(k, raising=False)

    monkeypatch.setenv("EZBAK_TZ", "Etc/UTC")

    # The suite must resolve no AWS credentials of its own, so no test can reach a real
    # bucket. Deletion precedes the sets below, which are themselves AWS_-prefixed. The
    # s3_bucket fixture re-adds AWS_DEFAULT_REGION, and moto supplies its own fake keys,
    # both after this fixture runs.
    for k in [k for k in os.environ if k.startswith("AWS_")]:
        monkeypatch.delenv(k, raising=False)

    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-aws-credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")

    # EnvConfig reads .env and .env.secrets from the working directory, and a developer's
    # copies hold real settings and credentials
    monkeypatch.setattr(EnvConfig, "model_config", {**EnvConfig.model_config, "env_file": None})


@pytest.fixture
def break_s3_listing(mocker: MockerFixture) -> Callable[[], MagicMock]:
    """Provide a callable that makes every AWSService.list_objects call fail with AccessDenied.

    Breaking the listing on demand rather than at fixture setup lets a test store a
    backup first and make the destination unreadable afterward. The returned mock lets a
    test let the destination recover by clearing its ``side_effect``.

    Returns:
        Callable[[], MagicMock]: Call to patch `list_objects`; returns the patched mock.
    """

    def _break() -> MagicMock:
        return mocker.patch.object(
            AWSService,
            "list_objects",
            autospec=True,
            side_effect=access_denied("ListObjectsV2"),
        )

    return _break


@pytest.fixture(autouse=True)  # ruff:ignore[pytest-fixture-autouse]
def reset_logger() -> Generator[None, None, None]:
    """Start and end every test with no loguru handlers.

    loguru's logger is a process-wide singleton. Without this, a file handler from one test
    points at a closed file in the next, and whether stderr output appears depends on which
    tests ran first.
    """
    logger.remove()
    yield
    logger.remove()


@pytest.fixture
def log_messages() -> Generator[list[str], None, None]:
    """Collect formatted log messages at WARNING and above, for tests that check a warning fires.

    Yields:
        list[str]: Each logged message, without level or timestamp.
    """
    messages: list[str] = []
    handler_id = logger.add(messages.append, level="WARNING", format="{message}")
    yield messages
    logger.remove(handler_id)


@pytest.fixture
def restore_dir(tmp_path: Path) -> Path:
    """Provide an empty directory to restore backups into.

    Returns:
        Path: The created `restore` directory under `tmp_path`.
    """
    path = tmp_path / "restore"
    path.mkdir()
    return path


@pytest.fixture
def legacy_backups(tmp_path: Path) -> Path:
    """Fill a directory with empty files named like legacy-format backups of the set "test".

    Returns:
        Path: The directory holding the files.
    """
    for name in LEGACY_BACKUP_NAMES:
        (tmp_path / name).touch()
    return tmp_path


@pytest.fixture
def make_s3_app(s3_bucket: str, filesystem: tuple[Path, Path, Path]) -> Callable[..., EZBak]:
    """Provide a factory for an app that backs up the `filesystem` source to the moto bucket.

    moto supplies credentials, so the app needs no keys. Keyword arguments override or add
    config options, such as `storage_paths` for a local copy too.

    Returns:
        Callable[..., EZBak]: Build an app from config overrides.
    """

    def _make(**overrides: Any) -> EZBak:  # ruff:ignore[any-type]
        options: dict[str, Any] = {
            "name": "test",
            "source_paths": [filesystem[0]],
            "aws_s3_bucket_name": s3_bucket,
            "tz": "Etc/UTC",
        }
        return ezbak(**{**options, **overrides})

    return _make


@pytest.fixture
def patched_boto3_session(mocker: MockerFixture) -> tuple[MagicMock, MagicMock]:
    """Replace boto3.Session with an autospecced mock, for tests that only construct AWSService.

    autospec catches a misspelled or misplaced argument to Session() or session.client() that a
    bare MagicMock would accept. Set behavior on `session.client.return_value`.

    Returns:
        tuple[MagicMock, MagicMock]: The patched Session class and the session it returns.
    """
    session_cls = mocker.patch("ezbak.storage.aws.boto3.Session", autospec=True)
    session = session_cls.return_value
    session.get_credentials.return_value = mocker.MagicMock(method="explicit")
    return session_cls, session
