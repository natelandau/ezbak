"""Direct AWSService tests backed by moto's in-memory S3."""

from pathlib import Path

import boto3
import pytest

from ezbak import ezbak
from ezbak.exceptions import RestoreFailedError
from ezbak.storage.aws import AWSService


def test_s3_bucket_fixture_smoke(s3_bucket: str) -> None:
    """Verify the s3_bucket fixture yields a bucket that exists in moto."""
    # Given: the moto-backed bucket fixture
    # When: listing buckets through a fresh client inside the mock context
    client = boto3.client("s3", region_name="us-east-1")
    names = [b["Name"] for b in client.list_buckets()["Buckets"]]
    # Then: the fixture's bucket exists
    assert s3_bucket in names


def test_create_uploads_s3_sidecar(s3_bucket: str, filesystem) -> None:
    """Verify create_backup uploads a .sha256 sidecar alongside the S3 archive."""
    # Given: A backup manager configured with an S3 destination
    src_dir, _, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        aws_s3_bucket_name=s3_bucket,
        aws_access_key="k",
        aws_secret_key="s",
    )

    # When: Creating a backup
    backups = app.create_backup()

    # Then: A sidecar object exists in the bucket with a 64-char hex digest
    client = boto3.client("s3", region_name="us-east-1")
    keys = [o["Key"] for o in client.list_objects_v2(Bucket=s3_bucket).get("Contents", [])]
    sidecar_key = backups[0].name + ".sha256"
    assert sidecar_key in keys
    body = client.get_object(Bucket=s3_bucket, Key=sidecar_key)["Body"].read().decode()
    assert len(body.split()[0]) == 64


def test_s3_restore_rejects_corrupt_archive(s3_bucket: str, filesystem, tmp_path) -> None:
    """Verify an S3-stored archive corrupted after upload fails checksum verification."""
    src_dir, _, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        aws_s3_bucket_name=s3_bucket,
        aws_access_key="k",
        aws_secret_key="s",
    )
    backup = app.create_backup()[0]

    # Overwrite the stored object with bytes that will not match the sidecar.
    client = boto3.client("s3", region_name="us-east-1")
    client.put_object(Bucket=s3_bucket, Key=backup.name, Body=b"corrupted")

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    with pytest.raises(RestoreFailedError, match="Checksum mismatch"):
        app.restore_backup(restore_path=restore_dir)


def test_s3_index_excludes_sidecars(s3_bucket: str, filesystem) -> None:
    """Verify list_backups does not count the .sha256 sidecar as a backup."""
    src_dir, _, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        aws_s3_bucket_name=s3_bucket,
        aws_access_key="k",
        aws_secret_key="s",
    )
    app.create_backup()  # writes one archive + one sidecar

    # The sidecar object must not be indexed as a backup.
    assert len(app.list_backups()) == 1


def test_s3_prune_deletes_sidecars(s3_bucket: str, filesystem) -> None:
    """Verify prune removes each pruned archive's sidecar, leaving none orphaned."""
    src_dir, _, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        aws_s3_bucket_name=s3_bucket,
        aws_access_key="k",
        aws_secret_key="s",
        max_backups=1,
    )
    app.create_backup()
    app.create_backup()  # two archives + two sidecars; retention keeps 1
    app.prune_backups()

    client = boto3.client("s3", region_name="us-east-1")
    keys = [o["Key"] for o in client.list_objects_v2(Bucket=s3_bucket).get("Contents", [])]
    # One archive and its one sidecar remain; no orphaned .sha256.
    assert sum(k.endswith(".sha256") for k in keys) == 1
    assert sum(k.endswith(".tgz") for k in keys) == 1


def _service(bucket: str, prefix: str | None = None) -> AWSService:
    return AWSService(
        aws_access_key="k", aws_secret_key="s", bucket_name=bucket, bucket_path=prefix
    )


def test_build_full_key_with_and_without_prefix(s3_bucket: str) -> None:
    """Verify build_full_key prepends the bucket path only when configured and avoids double-prefixing."""
    # Given: a service with no prefix and one with a prefix
    # When: building keys for a plain name and an already-prefixed name
    # Then: the prefix is applied once, or not at all when unset
    assert _service(s3_bucket).build_full_key("a.tgz") == "a.tgz"
    svc = _service(s3_bucket, prefix="team/")
    assert svc.build_full_key("a.tgz") == "team/a.tgz"
    assert svc.build_full_key("team/a.tgz") == "team/a.tgz"  # already-prefixed is not doubled


def test_upload_object_exists_and_get(s3_bucket: str, tmp_path: Path) -> None:
    """Verify upload_object stores a file that object_exists and get_object can retrieve."""
    # Given: a service and a local file
    svc = _service(s3_bucket)
    src = tmp_path / "a.txt"
    src.write_text("hello")

    # When: uploading the file
    svc.upload_object(file=src, name="a.txt")

    # Then: the object exists in the bucket, missing keys do not, and the bytes round-trip
    assert svc.object_exists("a.txt") is True
    assert svc.object_exists("missing.txt") is False

    dest = tmp_path / "out.txt"
    svc.get_object(key="a.txt", destination=dest)
    assert dest.read_text() == "hello"


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
    assert svc.object_exists("a.txt") is False


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
