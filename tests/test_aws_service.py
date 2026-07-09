"""Direct AWSService tests backed by moto's in-memory S3."""

import boto3
import pytest

from ezbak import ezbak
from ezbak.exceptions import RestoreFailedError


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
