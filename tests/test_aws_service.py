"""Direct AWSService tests backed by moto's in-memory S3."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import boto3
import pytest
import time_machine
from botocore.exceptions import ClientError
from pytest_mock import MockerFixture

from ezbak import ezbak
from ezbak.backup import Backup
from ezbak.constants import RestoreOutcome, StorageType
from ezbak.core import EZBak
from ezbak.exceptions import RestoreFailedError, StorageInitError
from ezbak.storage.aws import AWSService
from tests.helpers import access_denied


def _bucket_keys(bucket: str) -> list[str]:
    """List every object key in a moto bucket.

    Returns:
        list[str]: The bucket's object keys.
    """
    contents = boto3.client("s3", region_name="us-east-1").list_objects_v2(Bucket=bucket)
    return [obj["Key"] for obj in contents.get("Contents", [])]


@time_machine.travel(datetime(2025, 6, 9, tzinfo=ZoneInfo("UTC")), tick=False)
def test_create_uploads_archive_and_s3_sidecar(
    s3_bucket: str, make_s3_app: Callable[..., EZBak]
) -> None:
    """Verify create_backup uploads the archive and a .sha256 sidecar holding its digest."""
    # Given: A backup manager configured with an S3 destination
    app = make_s3_app()

    # When: Creating a backup
    app.create_backup()

    # Then: The archive and its sidecar are in the bucket, and the sidecar holds a 64-char digest
    assert set(_bucket_keys(s3_bucket)) == {
        "test-20250609T000000.tgz",
        "test-20250609T000000.tgz.sha256",
    }
    body = (
        boto3.client("s3", region_name="us-east-1")
        .get_object(Bucket=s3_bucket, Key="test-20250609T000000.tgz.sha256")["Body"]
        .read()
        .decode()
    )
    assert len(body.split()[0]) == 64


def test_s3_restore_rejects_corrupt_archive(
    s3_bucket: str, make_s3_app: Callable[..., EZBak], restore_dir: Path
) -> None:
    """Verify an S3-stored archive corrupted after upload fails checksum verification."""
    app = make_s3_app()
    backup = app.create_backup()[0]

    # Overwrite the stored object with bytes that will not match the sidecar.
    client = boto3.client("s3", region_name="us-east-1")
    client.put_object(Bucket=s3_bucket, Key=backup.name, Body=b"corrupted")

    with pytest.raises(RestoreFailedError, match="Checksum mismatch"):
        app.restore_backup(restore_path=restore_dir)


def test_s3_restore_removes_downloaded_archive(
    make_s3_app: Callable[..., EZBak], restore_dir: Path
) -> None:
    """Verify a successful S3 restore removes the downloaded archive from the staging dir."""
    # Given: a backup stored in S3
    app = make_s3_app()
    app.create_backup()

    # When: restoring the backup
    assert app.restore_backup(restore_path=restore_dir) is RestoreOutcome.RESTORED

    # Then: the staging dir is empty again, so a cron restore container does not
    # accumulate one archive-sized file per tick
    assert list(app.tmp_dir.iterdir()) == []


def test_s3_prepare_for_restore_missing_object_returns_none(
    make_s3_app: Callable[..., EZBak],
) -> None:
    """Verify preparing a restore for an object absent from the bucket returns None."""
    # Given: an S3 app and a backup whose object was never uploaded
    app = make_s3_app()
    backend = app.backends[0]
    backup = Backup(name="test-20240609T000000.tgz", storage_type=StorageType.AWS)

    # When: preparing the restore
    # Then: the missing object degrades to None rather than raising
    assert backend.prepare_for_restore(backup) is None


def test_s3_restore_missing_sidecar_warns_and_succeeds(
    s3_bucket: str,
    make_s3_app: Callable[..., EZBak],
    restore_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify an S3 restore proceeds with a warning when the sidecar object is absent."""
    # Given: a stored backup whose sidecar object has been removed
    app = make_s3_app()
    backup = app.create_backup()[0]
    client = boto3.client("s3", region_name="us-east-1")
    client.delete_object(Bucket=s3_bucket, Key=backup.name + ".sha256")

    # When: restoring the backup
    assert app.restore_backup(restore_path=restore_dir) is RestoreOutcome.RESTORED

    # Then: the restore succeeded and warned about the missing sidecar
    output = capsys.readouterr().err
    assert "No checksum sidecar" in output
    assert (restore_dir / "src" / "foo.txt").exists()


def test_s3_restore_cleans_download_when_staging_fails(
    make_s3_app: Callable[..., EZBak], restore_dir: Path, mocker: MockerFixture
) -> None:
    """Verify the downloaded archive is removed when the restore fails before extraction."""
    # Given: a stored backup and a restore whose staging dir cannot be created
    app = make_s3_app()
    app.create_backup()
    mocker.patch("ezbak.core.mkdtemp", autospec=True, side_effect=OSError("no space"))

    # When: restoring fails before the extract begins
    with pytest.raises(RestoreFailedError, match="staging directory"):
        app.restore_backup(restore_path=restore_dir)

    # Then: the downloaded archive was reclaimed, not leaked until process exit
    assert list(app.tmp_dir.iterdir()) == []


def test_local_restore_keeps_stored_archive(
    filesystem: tuple[Path, Path, Path], restore_dir: Path
) -> None:
    """Verify a local restore never deletes the stored archive it restored from."""
    # Given: a backup stored in a local storage path
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    backup = app.create_backup()[0]

    # When: restoring the backup
    assert app.restore_backup(restore_path=restore_dir) is RestoreOutcome.RESTORED

    # Then: the archive is still in storage (prepare_for_restore returned the
    # stored file itself, not a staged copy)
    assert backup.path is not None
    assert backup.path.exists()


def test_s3_index_excludes_sidecars(make_s3_app: Callable[..., EZBak]) -> None:
    """Verify list_backups does not count the .sha256 sidecar as a backup."""
    app = make_s3_app()
    app.create_backup()  # writes one archive + one sidecar

    # The sidecar object must not be indexed as a backup.
    assert len(app.list_backups()) == 1


def test_s3_prune_deletes_sidecars(s3_bucket: str, make_s3_app: Callable[..., EZBak]) -> None:
    """Verify prune removes each pruned archive's sidecar, leaving none orphaned."""
    app = make_s3_app(keep_last=1)
    app.create_backup()
    app.create_backup()  # two archives + two sidecars; retention keeps 1
    app.prune_backups()

    keys = _bucket_keys(s3_bucket)
    # One archive and its one sidecar remain; no orphaned .sha256.
    assert sum(k.endswith(".sha256") for k in keys) == 1
    assert sum(k.endswith(".tgz") for k in keys) == 1


def _service(bucket: str, prefix: str | None = None) -> AWSService:
    return AWSService(bucket_name=bucket, bucket_path=prefix)


@pytest.mark.parametrize(
    ("region", "endpoint_url", "expected_region", "expected_endpoint"),
    [
        pytest.param(
            "eu-west-1",
            "https://minio.example.com",
            "eu-west-1",
            "https://minio.example.com",
            id="explicit",
        ),
        pytest.param(None, None, None, None, id="unset"),
        pytest.param("", "", None, None, id="blank"),
    ],
)
def test_service_forwards_region_and_endpoint(
    patched_boto3_session: tuple[MagicMock, MagicMock],
    region: str | None,
    endpoint_url: str | None,
    expected_region: str | None,
    expected_endpoint: str | None,
) -> None:
    """Verify region reaches the session and endpoint the client, with blanks sent as None."""
    # Given a patched boto3.Session so no real S3 call is made
    session_cls, session = patched_boto3_session

    # When constructing the service
    AWSService(bucket_name="b", region=region, endpoint_url=endpoint_url)

    # Then region reaches the session (it signs requests) and endpoint reaches the client
    # (it only redirects where requests are sent). None defers to boto3's own resolution,
    # so a blank value from a .env template must not reach boto3 as an empty string.
    assert session_cls.call_args.kwargs["region_name"] == expected_region
    assert session.client.call_args.kwargs["endpoint_url"] == expected_endpoint


def test_service_malformed_endpoint_raises_storage_init_error() -> None:
    """Verify a malformed endpoint fails as StorageInitError, not a raw ValueError."""
    # Given an endpoint missing its scheme (boto3 rejects it at client construction)
    # When constructing the service
    # Then the failure surfaces as StorageInitError so core.py records a failed location
    with pytest.raises(StorageInitError):
        AWSService(
            aws_access_key="k",
            aws_secret_key="s",
            bucket_name="b",
            endpoint_url="minio.example.com",
        )


def test_build_full_key_with_and_without_prefix(s3_bucket: str) -> None:
    """Verify build_full_key prepends the bucket path to every name, so each name has one key."""
    # Given: a service with no prefix and one with a prefix
    # When: building keys for a plain name and a name that starts with the prefix
    # Then: the prefix is applied to both, or not at all when unset
    assert _service(s3_bucket).build_full_key("a.tgz") == "a.tgz"
    svc = _service(s3_bucket, prefix="team/")
    assert svc.build_full_key("a.tgz") == "team/a.tgz"
    assert svc.build_full_key("team/a.tgz") == "team/team/a.tgz"


def test_upload_object_round_trips_via_get(s3_bucket: str, tmp_path: Path) -> None:
    """Verify upload_object stores a file that list_objects and get_object can retrieve."""
    # Given: a service and a local file
    svc = _service(s3_bucket)
    src = tmp_path / "a.txt"
    src.write_text("hello")

    # When: uploading the file
    svc.upload_object(file=src, name="a.txt")

    # Then: the object is listed in the bucket and the bytes round-trip
    assert svc.list_objects() == ["a.txt"]

    dest = tmp_path / "out.txt"
    svc.get_object(key="a.txt", destination=dest)
    assert dest.read_text() == "hello"
    assert svc.get_object_content(key="a.txt") == "hello"


def test_delete_object(s3_bucket: str, tmp_path: Path) -> None:
    """Verify delete_object removes an uploaded object from the bucket."""
    # Given: an uploaded object
    svc = _service(s3_bucket)
    src = tmp_path / "a.txt"
    src.write_text("x")
    svc.upload_object(file=src, name="a.txt")

    # When: deleting the object
    # Then: deletion succeeds and the object no longer exists
    assert svc.delete_object(key="a.txt") is True
    assert svc.list_objects() == []


def test_delete_objects_batch(s3_bucket: str, tmp_path: Path) -> None:
    """Verify delete_objects removes a batch of keys and returns the deleted keys."""
    # Given: two uploaded objects
    svc = _service(s3_bucket)
    for name in ("a.txt", "b.txt"):
        f = tmp_path / name
        f.write_text("x")
        svc.upload_object(file=f, name=name)

    # When: deleting both keys in one call
    deleted = svc.delete_objects(keys=["a.txt", "b.txt"])

    # Then: both keys are reported deleted and the bucket is empty
    assert sorted(deleted) == ["a.txt", "b.txt"]
    assert svc.list_objects() == []


def test_delete_objects_empty_returns_empty(s3_bucket: str) -> None:
    """Verify delete_objects returns an empty list when given no keys."""
    # Given: a service and an empty key list
    # When: calling delete_objects
    # Then: no request is made and an empty list is returned
    assert _service(s3_bucket).delete_objects(keys=[]) == []


def test_delete_objects_rejects_over_limit(s3_bucket: str) -> None:
    """Verify delete_objects raises ValueError when given more than 1000 keys."""
    # Given: a service and a batch of 1001 keys
    # When: calling delete_objects
    # Then: a ValueError is raised before any S3 call is made
    with pytest.raises(ValueError, match="more than 1000"):
        _service(s3_bucket).delete_objects(keys=[f"k{i}" for i in range(1001)])


def test_list_objects_prefix_filter(s3_bucket: str, tmp_path: Path) -> None:
    """Verify list_objects filters by prefix and lists all objects when no prefix is given."""
    # Given: three uploaded objects, two sharing a prefix
    svc = _service(s3_bucket)
    for name in ("app-1.tgz", "app-2.tgz", "other.tgz"):
        f = tmp_path / name
        f.write_text("x")
        svc.upload_object(file=f, name=name)

    # When: listing with a prefix filter and without one
    # Then: the prefix filter returns only matching keys, and no prefix returns all
    assert sorted(svc.list_objects(prefix="app-")) == ["app-1.tgz", "app-2.tgz"]
    assert len(svc.list_objects()) == 3


def test_aws_service_uses_ambient_credential_chain(s3_bucket: str) -> None:
    """Verify AWSService initializes with no explicit credentials via the boto3 chain."""
    # Given: moto supplies an ambient credential chain and no keys are passed
    # When: building the service with only a bucket name
    svc = AWSService(bucket_name=s3_bucket)

    # Then: the bucket was reachable and the client is usable
    assert svc.list_objects() == []


@pytest.mark.parametrize(
    ("access", "secret", "expected_missing"),
    [
        pytest.param("k", None, "aws_secret_key", id="access_without_secret"),
        pytest.param(None, "s", "aws_access_key", id="secret_without_access"),
        pytest.param("k", "", "aws_secret_key", id="access_with_blank_secret"),
    ],
)
def test_aws_service_partial_credentials(
    access: str | None, secret: str | None, expected_missing: str
) -> None:
    """Verify a half-set credential pair fails naming the missing ezbak setting."""
    # Given: exactly one of the two credential settings
    # When: building the service
    # Then: init fails naming the missing setting, not boto3's parameter name (the
    # match must pin down "is not" since both setting names appear in every message)
    with pytest.raises(StorageInitError, match=f"{expected_missing} is not"):
        AWSService(bucket_name="test-bucket", aws_access_key=access, aws_secret_key=secret)


def test_aws_service_no_bucket_name() -> None:
    """Verify an empty bucket name fails before any client is built."""
    # Given: a blank bucket name
    # When: building the service
    # Then: init fails on the bucket precondition
    with pytest.raises(StorageInitError, match="No S3 bucket name provided"):
        AWSService(bucket_name="")


def test_blank_credentials_defer_to_ambient_chain(
    make_s3_app: Callable[..., EZBak], capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify blank credential strings defer to the chain instead of signing as empty."""
    # Given: blank credential strings, as a commented-out .env template produces
    # When: building an EZBak against the bucket with debug logging
    app = make_s3_app(log_level="debug", aws_access_key="", aws_secret_key="")

    # Then: the ambient chain resolved rather than an explicit empty credential
    assert app.aws_service is not None
    output = capsys.readouterr().err
    assert "resolved via 'env'" in output
    assert "resolved via 'explicit'" not in output


def test_list_objects_propagates_client_error(s3_bucket: str, mocker) -> None:
    """Verify a failed bucket listing raises instead of reporting an empty bucket."""
    # Given: a service whose paginator raises
    svc = AWSService(bucket_name=s3_bucket)
    mocker.patch.object(svc.s3, "get_paginator", side_effect=access_denied("ListObjectsV2"))

    # When listing objects, then the error surfaces rather than becoming []
    with pytest.raises(ClientError):
        svc.list_objects()
