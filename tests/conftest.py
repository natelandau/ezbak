"""Configuration for pytest."""

import os
from collections.abc import Callable, Generator
from pathlib import Path
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from loguru import logger
from moto import mock_aws
from pytest_mock import MockerFixture

from ezbak.storage.aws import AWSService


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


@pytest.fixture(autouse=True)
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
            side_effect=ClientError(
                error_response={"Error": {"Code": "AccessDenied", "Message": "denied"}},
                operation_name="ListObjectsV2",
            ),
        )

    return _break


@pytest.fixture(autouse=True)
def reset_logger() -> Generator[None, None, None]:
    """Reset logger handlers between tests to prevent closed file handle issues.

    The logger from loguru persists across test runs as a singleton. When tests
    configure logging with file handlers, these handlers can point to closed files
    in subsequent test runs, causing ValueError: I/O operation on closed file.
    This fixture removes all handlers after each test to ensure clean state.
    """
    yield
    logger.remove()
