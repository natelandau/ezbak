"""Test EZBak errors."""

import gzip
import io
import tarfile
from collections.abc import Callable
from pathlib import Path

import boto3
import pytest
from pydantic import ValidationError
from pytest_mock import MockerFixture

from ezbak import EZBak, ezbak
from ezbak.backup import Backup
from ezbak.constants import RestoreOutcome, StorageType
from ezbak.exceptions import (
    BackendNotFoundError,
    BackupFailedError,
    ConfigurationError,
    RestoreFailedError,
    StorageInitError,
    StorageReadError,
)
from ezbak.storage.aws import AWSService
from tests.helpers import access_denied


def _symlink_source(src_dir: Path) -> list[Path]:
    link = src_dir.parent / "symlink"
    link.symlink_to(src_dir / "foo.txt")
    return [link]


@pytest.mark.parametrize(
    "source_paths_for",
    [
        lambda _: [],
        lambda src_dir: [src_dir / "not_found"],
        _symlink_source,
    ],
    ids=["empty", "missing", "symlink"],
)
def test_create_backup_rejects_unusable_source_paths(
    filesystem, source_paths_for: Callable[[Path], list[Path]]
):
    """Verify a backup with no usable source path fails instead of archiving nothing."""
    # Given a source list that is empty, missing on disk, or a symlink
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=source_paths_for(src_dir), storage_paths=[dest1])

    # When creating a backup, then it fails as a configuration error
    with pytest.raises(ConfigurationError):
        app.create_backup()


@pytest.mark.parametrize(
    "target_name",
    ["new_dest", "file.txt"],
    ids=["missing", "is-a-file"],
)
def test_restore_rejects_unusable_target(filesystem, tmp_path, target_name):
    """Verify a restore target that is not an existing directory fails as a configuration error."""
    # Given a restore target that is missing or a file
    src_dir, dest1, _ = filesystem
    (tmp_path / "file.txt").touch()
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])

    # When restoring, then it is rejected before anything is extracted
    with pytest.raises(ConfigurationError):
        app.restore_backup(tmp_path / target_name)


def test_ezbak_rejects_invalid_config(filesystem):
    """Verify the ezbak shortcut raises on an invalid config instead of building an app."""
    # Given options with no backup name
    src_dir, dest1, _ = filesystem

    # When building through the shortcut, then validation fails
    with pytest.raises(ValidationError):
        ezbak(source_paths=[src_dir], storage_paths=[dest1])


def test_restore_backup_from_unconfigured_backend_raises(filesystem, restore_dir):
    """Verify restoring a backup held by a storage type the app has no backend for fails."""
    # Given a local-only app and an S3 backup handed in by the caller
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    orphan = Backup(name="test-20200101T000000.tgz", storage_type=StorageType.AWS)

    # When restoring it, then the missing backend is reported instead of a raw KeyError
    with pytest.raises(BackendNotFoundError):
        app.restore_backup(restore_dir, backup=orphan)
    assert not list(restore_dir.iterdir())


def test_restore_no_backup(filesystem, restore_dir):
    """Verify restoring with no stored backup reports NO_BACKUP."""
    # Given an empty storage path
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])

    # When restoring, then there is nothing to restore
    assert app.restore_backup(restore_dir) is RestoreOutcome.NO_BACKUP


def test_no_restore_destination(filesystem):
    """Verify restoring with no restore path given or configured fails."""
    # Given an app with no configured restore path
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])

    # When restoring without a path, then it fails as a configuration error
    with pytest.raises(ConfigurationError, match="No restore path provided and none configured"):
        app.restore_backup(None)


def test_create_backup_s3_only_bad_credentials_raises(filesystem):
    """Verify an S3-only run with missing credentials fails instead of a silent success."""
    # Given an S3-only config with no credentials
    src_dir, _, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        aws_s3_bucket_name="test-bucket",
        aws_access_key="",
        aws_secret_key="",
    )

    # When creating a backup, then it raises rather than reporting a silent success
    with pytest.raises(BackupFailedError, match="S3 bucket 'test-bucket'"):
        app.create_backup()


def test_create_backup_partial_failure_attaches_created_backups(filesystem):
    """Verify a partial-destination failure still exposes the backups that succeeded."""
    # Given a healthy local destination alongside an S3 bucket with bad credentials
    src_dir, dest1, _ = filesystem
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        aws_s3_bucket_name="test-bucket",
        aws_access_key="",
        aws_secret_key="",
    )

    # When the backup partially fails
    with pytest.raises(BackupFailedError) as exc:
        app.create_backup()

    # Then only S3 is reported failed and the successful local backup is attached
    assert exc.value.failed_storage_locations == ["S3 bucket 'test-bucket'"]
    assert len(exc.value.created_backups) == 1
    assert exc.value.created_backups[0].storage_type == StorageType.LOCAL


def test_create_backup_keeps_source_when_destination_fails(filesystem):
    """Verify sources are not deleted when the only destination is unusable."""
    # Given an S3-only config with delete_source_after_backup and no credentials
    src_dir, _, _ = filesystem
    marker = src_dir / "keep.txt"
    marker.write_text("important")
    app = ezbak(
        name="test",
        source_paths=[src_dir],
        aws_s3_bucket_name="test-bucket",
        aws_access_key="",
        aws_secret_key="",
        delete_source_after_backup=True,
    )

    # When the backup fails, then the source is left intact
    with pytest.raises(BackupFailedError):
        app.create_backup()
    assert marker.exists()


def test_create_backup_raises_when_archive_creation_fails(filesystem, mocker):
    """Verify create_backup fails loudly and stores nothing when the archive cannot be built."""
    # Given an app with a valid local destination and an archive that cannot be opened
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    mocker.patch("ezbak.core.tarfile.open", side_effect=OSError("No space left on device"))

    # When creating a backup, then it raises instead of returning silently
    with pytest.raises(BackupFailedError):
        app.create_backup()
    assert not list(dest1.glob("*.tgz"))


def _local_app_failing_sidecar_write(
    mocker: MockerFixture, make_s3_app: Callable[..., EZBak], dest: Path
) -> tuple[EZBak, Callable[[], set[str]]]:
    original_write_text = Path.write_text

    # Fail only the sidecar, so the archive reaching disk proves it is not written this way
    def _write_text(self: Path, data: str, encoding: str | None = None) -> int:
        if self.name.endswith(".sha256"):
            msg = f"Read-only file system: {self}"
            raise OSError(msg)
        return original_write_text(self, data, encoding=encoding)

    mocker.patch.object(Path, "write_text", autospec=True, side_effect=_write_text)
    app = make_s3_app(aws_s3_bucket_name=None, storage_paths=[dest])
    return app, lambda: {path.name for path in dest.iterdir()}


def _s3_app_failing_sidecar_write(
    mocker: MockerFixture, make_s3_app: Callable[..., EZBak], _dest: Path
) -> tuple[EZBak, Callable[[], set[str]]]:
    mocker.patch.object(
        AWSService, "upload_content", autospec=True, side_effect=access_denied("PutObject")
    )
    app = make_s3_app()
    bucket = app.settings.aws_s3_bucket_name
    return app, lambda: {
        x["Key"] for x in boto3.client("s3").list_objects_v2(Bucket=bucket).get("Contents", [])
    }


@pytest.mark.parametrize(
    "app_failing_sidecar_write",
    [_local_app_failing_sidecar_write, _s3_app_failing_sidecar_write],
    ids=["local", "s3"],
)
def test_create_backup_succeeds_when_sidecar_write_fails(
    filesystem, make_s3_app, mocker, app_failing_sidecar_write
):
    """Verify a failed checksum sidecar write still stores the archive and reports success."""
    # Given a destination that accepts the archive but not its checksum sidecar
    _, dest1, _ = filesystem
    app, stored_names = app_failing_sidecar_write(mocker, make_s3_app, dest1)

    # When creating a backup
    backups = app.create_backup()

    # Then the archive is stored and no sidecar exists
    assert len(backups) == 1
    names = stored_names()
    assert backups[0].name in names
    assert not [name for name in names if name.endswith(".sha256")]


def test_create_backup_uncreatable_local_path_fails_loudly(filesystem, mocker):
    """Verify an uncreatable local storage path fails cleanly instead of a raw OSError crash."""
    # Given a local destination whose directory cannot be created (e.g. a read-only mount)
    src_dir, dest1, _ = filesystem
    mocker.patch(
        "ezbak.core.validate_storage_paths",
        side_effect=OSError("Read-only file system"),
    )

    # When constructing EZBak, then it does not crash and registers no local backend
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    assert app.backends == []

    # When creating a backup, then it fails loudly instead of raising a raw OSError
    with pytest.raises(BackupFailedError):
        app.create_backup()


def test_restore_backup_raises_after_clean_when_archive_corrupt(filesystem, restore_dir):
    """Verify a failed restore with clean-before-restore fails loudly and leaves the destination untouched."""
    # Given a valid backup corrupted on disk and a restore target that was pre-populated
    src_dir, dest1, _ = filesystem
    app = ezbak(
        name="test", source_paths=[src_dir], storage_paths=[dest1], clean_before_restore=True
    )
    app.create_backup()
    for archive in dest1.glob("test-*.tgz"):
        archive.write_bytes(b"not a tarball")

    existing_file = restore_dir / "existing.txt"
    existing_file.write_text("pre-existing")

    # When restoring, then it raises loudly, and the destination is never touched:
    # the corrupt archive fails to extract into staging before any clean/commit happens.
    with pytest.raises(RestoreFailedError):
        app.restore_backup(restore_dir)
    assert existing_file.exists()


def test_restore_truncated_archive_leaves_destination_untouched(filesystem, restore_dir):
    """Verify an archive cut off mid-member fails the restore before the destination is cleaned."""
    # Given a well-formed gzip stream holding a tar cut off 1 MiB into a 3 MiB member,
    # with no checksum to catch it first
    _, dest1, _ = filesystem
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        member = tarfile.TarInfo("src/big.bin")
        member.size = 3 * 1024 * 1024
        tar.addfile(member, io.BytesIO(bytes(member.size)))
    truncated = raw.getvalue()[: 512 + 1024 * 1024]
    (dest1 / "test-20250101T000000.tgz").write_bytes(gzip.compress(truncated))

    app = ezbak(name="test", storage_paths=[dest1], use_checksums=False)
    (restore_dir / "keep.txt").write_text("important")

    # When restoring with clean_before_restore
    with pytest.raises(RestoreFailedError):
        app.restore_backup(restore_dir, clean_before_restore=True)

    # Then the destination holds exactly what it held before
    assert [path.name for path in restore_dir.iterdir()] == ["keep.txt"]


def test_restore_backup_raises_when_archive_missing_from_storage(filesystem, restore_dir, mocker):
    """Verify a backup that vanished from storage fails the restore loudly."""
    # Given an app whose backend reports the archive is gone from storage
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    app.create_backup()
    mocker.patch(
        "ezbak.storage.local.LocalBackend.prepare_for_restore",
        return_value=None,
    )

    # When restoring, then it raises rather than reporting a silent failure
    with pytest.raises(RestoreFailedError, match="missing from storage"):
        app.restore_backup(restore_dir)


def test_restore_backup_does_not_clean_when_no_backup(filesystem, restore_dir):
    """Verify clean_before_restore does not empty the target when there is no backup to restore."""
    # Given an app with no backups and a pre-populated restore target
    src_dir, dest1, _ = filesystem
    app = ezbak(
        name="test", source_paths=[src_dir], storage_paths=[dest1], clean_before_restore=True
    )
    keep = restore_dir / "keep.txt"
    keep.write_text("important")

    # When restoring with no backup available, then it returns NO_BACKUP without wiping the target
    assert app.restore_backup(restore_dir) is RestoreOutcome.NO_BACKUP
    assert keep.exists()


def test_restore_backup_unresolvable_destination_raises_configuration_error(filesystem):
    """Verify a restore path whose home directory cannot be resolved becomes a ConfigurationError."""
    # Given a restore path under the home directory of a user that does not exist
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])

    # When restoring, then it surfaces a ConfigurationError, not a raw RuntimeError
    with pytest.raises(ConfigurationError, match="Invalid restore path"):
        app.restore_backup("~ezbak-no-such-user/restore")


def test_s3_index_wraps_listing_failure(make_s3_app, break_s3_listing) -> None:
    """Verify a failed listing becomes a StorageReadError at the backend boundary."""
    # Given: an S3-backed app whose listing fails
    app = make_s3_app()
    break_s3_listing()
    backend = app.backends[0]

    # When indexing, then the botocore error is translated to the domain type
    with pytest.raises(StorageReadError):
        backend.index()


def test_create_backup_keeps_local_copy_when_s3_listing_fails(
    s3_bucket: str, filesystem, make_s3_app, break_s3_listing
) -> None:
    """Verify a failed S3 listing still writes every destination and fails the run loudly."""
    # Given: a healthy local destination alongside an S3 bucket that cannot be listed
    _, dest1, _ = filesystem
    app = make_s3_app(storage_paths=[dest1])
    break_s3_listing()

    # When creating a backup
    with pytest.raises(BackupFailedError) as exc:
        app.create_backup()

    # Then S3 is reported failed and both copies were still written
    assert exc.value.failed_storage_locations == [f"S3 bucket '{s3_bucket}'"]
    assert {x.storage_type for x in exc.value.created_backups} == {
        StorageType.LOCAL,
        StorageType.AWS,
    }


def test_local_index_failure_is_recorded(filesystem, mocker) -> None:
    """Verify an unreadable local destination is recorded instead of raising an OSError."""
    # Given: a local-only app whose storage path becomes unusable after construction
    src_dir, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src_dir], storage_paths=[dest1])
    mocker.patch(
        "ezbak.storage.local.validate_storage_paths",
        side_effect=OSError("permission denied"),
    )
    app.rebuild_storage_locations = True

    # When creating a backup, then the failure is reported as a storage location, not a
    # raw OSError escaping the run
    with pytest.raises(BackupFailedError, match="local storage paths"):
        app.create_backup()


@pytest.mark.parametrize(
    ("options", "break_listing_after_backup"),
    [
        ({"skip_if_no_backup": True}, True),
        ({"restore_date": "20250102"}, True),
        ({"aws_s3_bucket_name": "missing-bucket", "skip_if_no_backup": True}, False),
    ],
    ids=[
        "listing-denied-skip-if-no-backup",
        "listing-denied-restore-date",
        "bucket-missing-skip-if-no-backup",
    ],
)
def test_restore_refuses_when_destination_unreadable(
    make_s3_app, break_s3_listing, restore_dir, options, break_listing_after_backup
) -> None:
    """Verify an unreadable destination fails a restore instead of reporting nothing to restore."""
    # Given: a destination that cannot be read, either because its listing is denied after a
    # backup was stored or because the bucket does not exist, plus an option that would
    # otherwise turn a missing backup into a clean no-op or a point-in-time lookup
    app = make_s3_app(**options)
    if break_listing_after_backup:
        app.create_backup()
        break_s3_listing()
        app.rebuild_storage_locations = True

    # When restoring, then it fails
    with pytest.raises(RestoreFailedError, match="Cannot determine available backups"):
        app.restore_backup(restore_path=restore_dir)


def test_restore_explicit_backup_skips_index_check(
    make_s3_app, break_s3_listing, restore_dir
) -> None:
    """Verify an explicitly supplied backup restores without needing a complete index."""
    # Given: a stored backup held by the caller, then a bucket that cannot be listed
    app = make_s3_app()
    backup = app.create_backup()[0]
    break_s3_listing()
    app.rebuild_storage_locations = True

    # When restoring that specific backup, then no index is required
    assert app.restore_backup(restore_path=restore_dir, backup=backup) is RestoreOutcome.RESTORED
    assert (restore_dir / "src" / "foo.txt").exists()


def test_unreadable_locations_exposes_failed_index(
    s3_bucket: str, filesystem, make_s3_app, break_s3_listing
) -> None:
    """Verify a failed index is observable through the public unreadable_locations property."""
    # Given: a local destination alongside a bucket that cannot be listed
    _, dest1, _ = filesystem
    app = make_s3_app(storage_paths=[dest1])
    break_s3_listing()

    # When listing backups
    app.list_backups()

    # Then the unreadable destination is reported, so a caller can tell the list is partial
    assert app.unreadable_locations == [f"S3 bucket '{s3_bucket}'"]


def test_unreadable_locations_empty_when_all_locations_readable(filesystem, make_s3_app) -> None:
    """Verify a healthy index reports no unreadable locations."""
    # Given: a readable bucket and local path
    _, dest1, _ = filesystem
    app = make_s3_app(storage_paths=[dest1])

    # When listing backups
    app.list_backups()

    # Then nothing is reported unreadable
    assert app.unreadable_locations == []


def test_unreadable_locations_returns_a_copy(s3_bucket: str, make_s3_app, break_s3_listing) -> None:
    """Verify mutating the returned list cannot corrupt the manager's internal state."""
    # Given: an app with one unreadable destination
    app = make_s3_app()
    break_s3_listing()

    # When a caller mutates what it was handed
    app.unreadable_locations.clear()

    # Then the manager still knows the destination failed
    assert app.unreadable_locations == [f"S3 bucket '{s3_bucket}'"]


def test_create_backup_uploads_when_s3_listing_fails(
    s3_bucket: str, make_s3_app, break_s3_listing
) -> None:
    """Verify an S3-only run whose listing fails still uploads the archive and fails loudly."""
    # Given: an S3-only destination that can no longer be listed, as an S3-only
    # post-stop final backup with no retry would find it
    app = make_s3_app()
    break_s3_listing()

    # When creating a backup
    with pytest.raises(BackupFailedError) as exc:
        app.create_backup()

    # Then the run names the unreadable destination and the archive still reached it
    assert exc.value.failed_storage_locations == [f"S3 bucket '{s3_bucket}'"]
    assert len(exc.value.created_backups) == 1
    keys = [
        x["Key"] for x in boto3.client("s3").list_objects_v2(Bucket=s3_bucket).get("Contents", [])
    ]
    assert any(key.startswith("test-") and key.endswith(".tgz") for key in keys)


def test_create_backup_names_a_failed_destination_once(
    s3_bucket: str, make_s3_app, break_s3_listing, mocker
) -> None:
    """Verify a destination that fails to index and to write is named once in the error."""
    # Given: a bucket that can neither be listed nor written
    app = make_s3_app()
    break_s3_listing()
    mocker.patch.object(
        AWSService, "upload_object", autospec=True, side_effect=access_denied("PutObject")
    )

    # When creating a backup
    with pytest.raises(BackupFailedError) as exc:
        app.create_backup()

    # Then one destination failing once reads as one failure, not two
    assert exc.value.failed_storage_locations == [f"S3 bucket '{s3_bucket}'"]


def test_unusable_destination_is_rebuilt_after_it_recovers(
    s3_bucket: str, make_s3_app, mocker
) -> None:
    """Verify a destination that could not be constructed is retried on a later pass."""
    # Given: a bucket unreachable for the first two attempts, as a container starting
    # before its network or instance role is ready would see
    recovered = AWSService(bucket_name=s3_bucket)
    mocker.patch(
        "ezbak.core.AWSService",
        side_effect=[StorageInitError("unreachable"), StorageInitError("unreachable"), recovered],
    )
    app = make_s3_app()
    assert app.aws_service is None
    assert app.unreadable_locations == [f"S3 bucket '{s3_bucket}'"]

    # When the destination recovers and a later scheduled run indexes again
    # Then the backend is rebuilt rather than staying broken for the process's lifetime
    assert app.create_backup()
    assert app.unreadable_locations == []


def test_create_backup_does_not_report_a_stale_index_failure(
    s3_bucket: str, filesystem, make_s3_app, break_s3_listing
) -> None:
    """Verify a backup run reports the index failures of its own pass, not a cached one."""
    # Given: an app that indexed while the bucket could not be listed
    _, dest1, _ = filesystem
    app = make_s3_app(storage_paths=[dest1])
    mock_list = break_s3_listing()
    assert app.unreadable_locations == [f"S3 bucket '{s3_bucket}'"]

    # When the bucket recovers before the backup runs
    mock_list.side_effect = None
    mock_list.return_value = []

    # Then the run indexes again and succeeds instead of failing on the cached record
    assert app.create_backup()
