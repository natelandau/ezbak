"""Test S3 backups through the ezbak app and its S3 backend."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import boto3
import pytest
import time_machine
from boto3.exceptions import RetriesExceededError, S3UploadFailedError
from botocore.exceptions import EndpointConnectionError
from pytest_mock import MockerFixture

from ezbak.backup import Backup
from ezbak.checksums import sidecar_name
from ezbak.constants import RestoreOutcome, StorageType
from ezbak.core import EZBak
from ezbak.exceptions import (
    BackupFailedError,
    RestoreFailedError,
    StorageDeleteError,
    StorageInitError,
    StorageReadError,
    StorageWriteError,
)
from ezbak.storage.aws import AWSService
from tests.helpers import SEEDED_MARKER, access_denied, relative_tree, s3_backend, seed_backups

UTC = ZoneInfo("UTC")


def _bucket_keys(bucket: str) -> set[str]:
    """List every object key in a moto bucket.

    Returns:
        set[str]: The bucket's object keys.
    """
    contents = boto3.client("s3", region_name="us-east-1").list_objects_v2(Bucket=bucket)
    return {obj["Key"] for obj in contents.get("Contents", [])}


def test_s3_restore_invalid_tarball_raises(
    s3_bucket: str, make_s3_app: Callable[..., EZBak], restore_dir: Path
) -> None:
    """Verify restoring an S3 object that is not a valid tarball fails loudly."""
    # Given a backup object in the bucket whose content is not a valid tarball
    boto3.client("s3", region_name="us-east-1").put_object(
        Bucket=s3_bucket, Key="test-20240609T000000-yearly.tgz", Body=b"not a valid tarball"
    )
    app = make_s3_app()

    # When restoring the latest backup
    # Then the download succeeds but the extract fails
    with pytest.raises(RestoreFailedError, match="Failed to extract backup archive"):
        app.restore_backup(restore_dir)


def test_delete_objects_returns_only_confirmed_keys(
    patched_boto3_session: tuple[MagicMock, MagicMock],
) -> None:
    """Verify a batch delete reports only the keys S3 confirmed, not those it rejected."""
    # Given an S3 client whose batch delete confirms one key and rejects another. moto
    # always confirms every delete, so this partial response needs a patched client.
    _, session = patched_boto3_session
    session.client.return_value.delete_objects.return_value = {
        "Deleted": [{"Key": "test-20240609T000000.tgz"}],
        "Errors": [{"Key": "test-20240610T000000.tgz", "Code": "404", "Message": "Not Found"}],
    }
    aws_service = AWSService(bucket_name="test-bucket")

    # When deleting both keys
    deleted = aws_service.delete_objects(["test-20240609T000000.tgz", "test-20240610T000000.tgz"])

    # Then only the confirmed key is returned
    assert deleted == ["test-20240609T000000.tgz"]


@pytest.mark.parametrize(
    ("bucket_override", "expected_types"),
    [
        pytest.param(None, {StorageType.LOCAL, StorageType.AWS}, id="local-and-s3"),
        pytest.param("missing-bucket", {StorageType.LOCAL}, id="s3-unusable-keeps-local"),
    ],
)
def test_backends_follow_destinations(
    make_s3_app: Callable[..., EZBak],
    filesystem: tuple[Path, Path, Path],
    bucket_override: str | None,
    expected_types: set[StorageType],
) -> None:
    """Verify an app builds one backend per usable destination and keeps local when S3 fails."""
    # Given a local destination plus an S3 bucket that exists or cannot be reached
    _, dest1, _ = filesystem
    overrides = {"aws_s3_bucket_name": bucket_override} if bucket_override else {}

    # When constructing the app
    app = make_s3_app(storage_paths=[dest1], **overrides)

    # Then the backends match the usable destinations
    assert {backend.storage_type for backend in app.backends} == expected_types


def test_aws_service_no_resolvable_credentials() -> None:
    """Verify AWSService fails init when no credentials resolve from anywhere."""
    # Given no explicit credentials and, via the mock_env fixture, no ambient chain
    assert boto3.Session().get_credentials() is None

    # When constructing the service, then it degrades to a StorageInitError
    with pytest.raises(StorageInitError, match="Cannot access S3 bucket"):
        AWSService(bucket_name="test-bucket")


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(access_denied("HeadBucket"), id="access-denied"),
        pytest.param(
            EndpointConnectionError(endpoint_url="https://s3.amazonaws.com"), id="network"
        ),
    ],
)
def test_aws_service_unreachable_bucket_raises_storage_init_error(
    patched_boto3_session: tuple[MagicMock, MagicMock], error: Exception
) -> None:
    """Verify a bucket lookup failure at init becomes a StorageInitError, not a raw crash."""
    # Given an S3 client whose bucket lookup fails
    _, session = patched_boto3_session
    session.client.return_value.head_bucket.side_effect = error

    # When constructing the service, then a StorageInitError is raised
    with pytest.raises(StorageInitError, match="Cannot access S3 bucket"):
        AWSService(bucket_name="test-bucket")


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(
            EndpointConnectionError(endpoint_url="https://s3.amazonaws.com"), id="network"
        ),
        pytest.param(access_denied("PutObject"), id="client-error"),
        pytest.param(S3UploadFailedError("denied"), id="managed-transfer"),
    ],
)
def test_s3_write_failure_raises_storage_write_error(
    make_s3_app: Callable[..., EZBak], mocker: MockerFixture, error: Exception
) -> None:
    """Verify every kind of upload failure becomes a StorageWriteError, not a raw boto error."""
    # Given an S3-only app with a staged archive whose upload fails
    app = make_s3_app()
    backend = s3_backend(app)
    tmp_backup = app.tmp_dir / "staged.tgz"
    tmp_backup.write_bytes(b"data")
    mocker.patch.object(backend.aws_service, "upload_object", side_effect=error)

    # When writing, then a StorageWriteError is raised
    with pytest.raises(StorageWriteError):
        backend.write(
            tmp_backup=tmp_backup, storage_location=app.storage_locations[0], checksum=None
        )


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(
            EndpointConnectionError(endpoint_url="https://s3.amazonaws.com"), id="network"
        ),
        pytest.param(access_denied("GetObject"), id="client-error"),
        pytest.param(RetriesExceededError(OSError("connection reset")), id="retries-exceeded"),
    ],
)
def test_s3_download_failure_raises_storage_read_error(
    make_s3_app: Callable[..., EZBak], mocker: MockerFixture, error: Exception
) -> None:
    """Verify a failed download becomes a StorageReadError and leaves no partial file behind."""
    # Given an S3-only app whose download writes a partial file and then fails
    app = make_s3_app()
    backend = s3_backend(app)
    backup = Backup(name="test-20240609T000000.tgz", storage_type=StorageType.AWS)

    def _partial_download(*, key: str, destination: Path) -> Path:
        destination.write_bytes(b"partial")
        raise error

    mocker.patch.object(backend.aws_service, "get_object", side_effect=_partial_download)

    # When preparing the restore, then a StorageReadError is raised
    with pytest.raises(StorageReadError):
        backend.prepare_for_restore(backup)

    # Then the partial download was removed from the staging dir
    assert list(app.tmp_dir.iterdir()) == []


def test_create_backup_writes_local_copy_when_s3_upload_fails(
    make_s3_app: Callable[..., EZBak],
    filesystem: tuple[Path, Path, Path],
    mocker: MockerFixture,
) -> None:
    """Verify a failed S3 upload still writes the local copy and then fails the run."""
    # Given local and S3 destinations whose S3 upload is denied
    _, dest1, _ = filesystem
    app = make_s3_app(storage_paths=[dest1])
    mocker.patch.object(
        AWSService, "upload_object", autospec=True, side_effect=access_denied("PutObject")
    )

    # When creating a backup, then the run fails naming one destination
    with pytest.raises(BackupFailedError) as exc_info:
        app.create_backup()
    assert len(exc_info.value.failed_storage_locations) == 1

    # Then the local archive was still written and is the only one reported created
    assert [backup.storage_type for backup in exc_info.value.created_backups] == [StorageType.LOCAL]
    assert len(list(dest1.glob("test-*.tgz"))) == 1


def test_s3_delete_removes_archive_and_sidecar(
    s3_bucket: str, make_s3_app: Callable[..., EZBak]
) -> None:
    """Verify deleting one S3 backup removes both its archive and its checksum sidecar."""
    # Given a stored S3 backup with its sidecar
    app = make_s3_app()
    backup = app.create_backup()[0]

    # When deleting it through the backend
    assert s3_backend(app).delete(backup) is True

    # Then the bucket is empty
    assert _bucket_keys(s3_bucket) == set()


def test_s3_delete_client_error_raises_storage_delete_error(
    make_s3_app: Callable[..., EZBak], mocker: MockerFixture
) -> None:
    """Verify a delete ClientError becomes a StorageDeleteError instead of leaking botocore."""
    # Given an S3-only app whose single-object delete is denied
    backend = s3_backend(make_s3_app())
    backup = Backup(name="test-20240609T000000-yearly.tgz", storage_type=StorageType.AWS)
    mocker.patch.object(
        backend.aws_service, "delete_object", side_effect=access_denied("DeleteObject")
    )

    # When deleting, then a StorageDeleteError is raised
    with pytest.raises(StorageDeleteError, match="S3 delete failed"):
        backend.delete(backup)


def test_s3_delete_many_network_error_raises_storage_delete_error(
    make_s3_app: Callable[..., EZBak], mocker: MockerFixture
) -> None:
    """Verify a batch-delete connectivity failure becomes a StorageDeleteError, not a raw crash."""
    # Given an S3-only app whose batch delete hits a connectivity error (a BotoCoreError)
    backend = s3_backend(make_s3_app())
    backup = Backup(name="test-20240609T000000-yearly.tgz", storage_type=StorageType.AWS)
    mocker.patch.object(
        backend.aws_service,
        "delete_objects",
        side_effect=EndpointConnectionError(endpoint_url="https://s3.amazonaws.com"),
    )

    # When batch-deleting, then a StorageDeleteError is raised
    with pytest.raises(StorageDeleteError, match="S3 batch delete failed"):
        backend.delete_many([backup])


def test_prune_backups_tolerates_s3_delete_failure(
    s3_bucket: str, make_s3_app: Callable[..., EZBak], mocker: MockerFixture
) -> None:
    """Verify pruning tolerates a failing S3 batch delete and leaves every archive in place."""
    # Given two S3 backups at distinct times and a policy that keeps only the newest
    app = make_s3_app(keep_last=1)
    for day in (1, 2):
        with time_machine.travel(datetime(2025, 1, day, tzinfo=UTC), tick=False):
            app.create_backup()

    # Given the batch delete fails
    mocker.patch.object(
        AWSService, "delete_objects", autospec=True, side_effect=access_denied("DeleteObjects")
    )

    # When pruning, then nothing is reported deleted
    assert app.prune_backups() == []

    # Then both archives remain in the bucket
    assert {key for key in _bucket_keys(s3_bucket) if key.endswith(".tgz")} == {
        "test-20250101T000000.tgz",
        "test-20250102T000000.tgz",
    }


def test_s3_delete_many_chunks_large_batches(
    make_s3_app: Callable[..., EZBak], mocker: MockerFixture
) -> None:
    """Verify deleting more than 1000 backups is chunked instead of exceeding the S3 limit."""
    # Given an S3-only app and more than 1000 backups to delete
    backend = s3_backend(make_s3_app())
    backups = [
        Backup(name=f"test-20240609T000000-{i:05d}.tgz", storage_type=StorageType.AWS)
        for i in range(1500)
    ]

    # Given delete_objects echoes back the keys it received
    delete_spy = mocker.patch.object(
        backend.aws_service, "delete_objects", side_effect=lambda keys: keys
    )

    # When deleting, then each archive's sidecar key rides along in the same batches,
    # doubling the key count to 3000 (1500 archives + 1500 sidecars), chunked into
    # three requests of 1000 keys each. Only archives count as confirmed deleted.
    deleted = backend.delete_many(backups)
    assert deleted == backups
    assert [len(call.kwargs["keys"]) for call in delete_spy.call_args_list] == [1000, 1000, 1000]


def test_s3_prefix_round_trip_scopes_list_restore_and_prune(
    s3_bucket: str,
    make_s3_app: Callable[..., EZBak],
    filesystem: tuple[Path, Path, Path],
    restore_dir: Path,
) -> None:
    """Verify a bucket prefix scopes listing, restoring, and pruning to keys under it."""
    # Given a decoy backup at the bucket root and three backups under the "team/" prefix
    boto3.client("s3", region_name="us-east-1").put_object(
        Bucket=s3_bucket, Key="test-20200101T000000.tgz", Body=b"decoy"
    )
    app = make_s3_app(aws_s3_bucket_prefix="team/")
    for day in (1, 2, 3):
        with time_machine.travel(datetime(2025, 1, day, tzinfo=UTC), tick=False):
            app.create_backup()

    # When listing, then only the prefixed backups appear
    assert sorted(backup.name for backup in app.list_backups()) == [
        "test-20250101T000000.tgz",
        "test-20250102T000000.tgz",
        "test-20250103T000000.tgz",
    ]

    # When restoring, then the newest prefixed backup restores the source tree
    assert app.restore_backup(restore_dir) is RestoreOutcome.RESTORED
    assert relative_tree(restore_dir / "src") == relative_tree(filesystem[0])

    # When pruning to one backup under the same prefix
    make_s3_app(aws_s3_bucket_prefix="team/", keep_last=1).prune_backups()

    # Then only the newest prefixed archive, its sidecar, and the root decoy remain
    assert _bucket_keys(s3_bucket) == {
        "team/test-20250103T000000.tgz",
        "team/test-20250103T000000.tgz.sha256",
        "test-20200101T000000.tgz",
    }


def _full_key(*, bucket_prefix: str | None, name: str) -> str:
    """Return the object key a name is stored under below an optional bucket prefix."""
    return f"{bucket_prefix}/{name}" if bucket_prefix else name


def _seed_bucket(
    *, bucket: str, staging: Path, names: list[str], bucket_prefix: str | None
) -> None:
    """Upload a seeded backup and its checksum sidecar under each name, below the bucket prefix."""
    seed_backups(directory=staging, names=names, with_sidecars=True)
    client = boto3.client("s3", region_name="us-east-1")
    for name in names:
        for object_name in (name, sidecar_name(name)):
            client.put_object(
                Bucket=bucket,
                Key=_full_key(bucket_prefix=bucket_prefix, name=object_name),
                Body=(staging / object_name).read_bytes(),
            )


@pytest.mark.parametrize(
    ("set_name", "other_set_name", "bucket_prefix"),
    [
        ("app", "app-db", None),
        ("app-db", "app", None),
        ("app", "app-db", "pfx"),
        ("team/app", "team/app-db", None),
        ("pfx/app", "app", "pfx"),
        ("app", "pfx/app", "pfx"),
    ],
    ids=[
        "shorter-name",
        "longer-name",
        "under-bucket-prefix",
        "nested-name",
        "name-repeats-bucket-prefix",
        "other-name-repeats-bucket-prefix",
    ],
)
def test_s3_sets_sharing_a_bucket_never_touch_each_other(
    s3_bucket: str,
    make_s3_app: Callable[..., EZBak],
    tmp_path: Path,
    restore_dir: Path,
    set_name: str,
    other_set_name: str,
    bucket_prefix: str | None,
) -> None:
    """Verify list, restore, and prune for one set leave another set's objects and sidecars alone."""
    # Given two backups of the set, older than two backups of another set, all with sidecars
    own = [f"{set_name}-20250101T000000.tgz", f"{set_name}-20250102T000000.tgz"]
    other = [f"{other_set_name}-20250103T000000.tgz", f"{other_set_name}-20250104T000000.tgz"]
    _seed_bucket(
        bucket=s3_bucket, staging=tmp_path / "seed", names=own + other, bucket_prefix=bucket_prefix
    )
    app = make_s3_app(name=set_name, aws_s3_bucket_prefix=bucket_prefix, keep_last=1)

    # When listing, then only the set's own backups appear
    assert sorted(backup.name for backup in app.list_backups()) == own

    # When restoring, then the set's newest backup restores, not the other set's newer one
    assert app.restore_backup(restore_dir) is RestoreOutcome.RESTORED
    assert (restore_dir / SEEDED_MARKER).read_text() == own[-1]

    # When pruning to one backup, then only the set's older archive and its sidecar go
    app.prune_backups()
    kept = [own[-1], *other]
    assert _bucket_keys(s3_bucket) == {
        _full_key(bucket_prefix=bucket_prefix, name=object_name)
        for name in kept
        for object_name in (name, sidecar_name(name))
    }


@pytest.mark.parametrize(
    ("bucket_prefix", "nested_key"),
    [(None, "app-x/20250101T000000.tgz"), ("pfx", "pfx/app-pfx/20250101T000000.tgz")],
    ids=["under-another-set-name", "repeats-bucket-prefix"],
)
def test_s3_index_skips_keys_nested_below_the_set_name(
    s3_bucket: str,
    make_s3_app: Callable[..., EZBak],
    tmp_path: Path,
    bucket_prefix: str | None,
    nested_key: str,
) -> None:
    """Verify a nested key is never indexed as, or pruned in place of, one of the set's backups."""
    # Given one backup of the set and a nested key that starts with the set's name and prefix
    _seed_bucket(
        bucket=s3_bucket,
        staging=tmp_path / "seed",
        names=["app-20250101T000000.tgz"],
        bucket_prefix=bucket_prefix,
    )
    boto3.client("s3", region_name="us-east-1").put_object(
        Bucket=s3_bucket, Key=nested_key, Body=b"foreign"
    )
    keys_before = _bucket_keys(s3_bucket)
    app = make_s3_app(name="app", aws_s3_bucket_prefix=bucket_prefix, keep_last=1)

    # When listing, then only the set's backup appears
    assert [backup.name for backup in app.list_backups()] == ["app-20250101T000000.tgz"]

    # When pruning to one backup, then nothing is deleted
    app.prune_backups()
    assert _bucket_keys(s3_bucket) == keys_before


def test_s3_restore_proceeds_when_sidecar_read_fails(
    make_s3_app: Callable[..., EZBak],
    mocker: MockerFixture,
    filesystem: tuple[Path, Path, Path],
    restore_dir: Path,
) -> None:
    """Verify an unreadable checksum sidecar does not block an S3 restore."""
    # Given a stored S3 backup whose sidecar read is denied
    app = make_s3_app()
    app.create_backup()
    mocker.patch.object(
        AWSService, "get_object_content", autospec=True, side_effect=access_denied("GetObject")
    )

    # When restoring, then the restore proceeds without verification
    assert app.restore_backup(restore_dir) is RestoreOutcome.RESTORED
    assert relative_tree(restore_dir / "src") == relative_tree(filesystem[0])
