"""Test EZBak errors."""

import pytest
from pydantic import ValidationError

from ezbak import ezbak
from ezbak.constants import StorageType
from ezbak.models import Backup


def test_no_name(filesystem):
    """Verify building an EZBak without a name is rejected."""
    # Given source and destination directories
    src_dir, dest1, _ = filesystem

    # When building without a name, then a validation error is raised
    with pytest.raises(ValidationError, match="No backup name provided"):
        ezbak(
            # name="test",
            source_paths=[src_dir],
            storage_paths=[dest1],
        )


def test_source_paths(filesystem):
    """Test EZBak errors."""
    _, dest1, _ = filesystem
    backup_manager = ezbak(
        name="test",
        source_paths=[],
        storage_paths=[dest1],
    )
    with pytest.raises(ValueError, match="No source paths provided"):
        backup_manager.create_backup()


def test_source_paths_not_found(filesystem):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir / "not_found"],
        storage_paths=[dest1],
    )
    with pytest.raises(ValueError, match="Source does not exist"):
        backup_manager.create_backup()


def test_source_paths_symlink(tmp_path, capsys, debug):
    """Test EZBak errors."""
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "file.txt").touch()
    (src_dir / "symlink").symlink_to(src_dir / "file.txt")

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir / "symlink"],
        storage_paths=[dest_dir],
    )
    with pytest.raises(ValueError, match="Not a file or directory"):
        backup_manager.create_backup()


def test_storage_paths(filesystem):
    """Test EZBak errors."""
    src_dir, _, _ = filesystem
    with pytest.raises(ValueError, match="No destination configured"):
        ezbak(
            name="test",
            source_paths=[src_dir],
            storage_paths=[],
        )


def test_create_storage_path_dir(filesystem):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem

    new_dest = dest1 / "new_dir"
    assert not new_dest.exists()

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[new_dest],
    )
    backup_manager.create_backup()

    assert new_dest.exists()
    assert new_dest.is_dir()


def test_restore_no_dest(filesystem, tmp_path, debug, capsys):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
    )
    with pytest.raises(ValueError, match="Restore destination does not exist"):
        backup_manager.restore_backup(tmp_path / "new_dest")


def test_restore_dest_not_dir(filesystem, tmp_path, debug, capsys):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem

    new_dest = dest1 / "file.txt"
    new_dest.touch()

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
    )
    backup_manager.create_backup()
    with pytest.raises(ValueError, match="Restore destination does not exist"):
        backup_manager.restore_backup(new_dest)


def test_restore_no_backup(filesystem, tmp_path, debug, capsys):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
        log_level="DEBUG",
    )
    # backup_manager.create_backup()
    assert not backup_manager.restore_backup(tmp_path)
    output = capsys.readouterr().err
    # debug(output)
    assert "ERROR    | No backup found to restore" in output


def test_no_restore_destination(filesystem, tmp_path, debug, capsys):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem

    backup_manager = ezbak(
        name="test",
        source_paths=[src_dir],
        storage_paths=[dest1],
    )
    with pytest.raises(ValueError, match="Invalid destination: None"):
        backup_manager.restore_backup(None)


def test_delete_unmapped_backend_raises_clear_error(filesystem):
    """Verify deleting a backup whose backend is not configured fails loudly."""
    # Given an app with only a local backend
    src, dest1, _ = filesystem
    app = ezbak(name="t", source_paths=[src], storage_paths=[dest1])

    # And a backup tagged for a backend that was never built
    orphan = Backup(name="t-20200101T000000-daily.tgz", storage_type=StorageType.AWS)

    # When attempting to delete it, then a clear error names the missing backend
    with pytest.raises(ValueError, match="No configured backend for storage type: aws"):
        app._delete_backup(orphan)


def test_restore_backup_missing_local_storage_path(filesystem, tmp_path):
    """Verify restoring with a missing local storage path fails gracefully and indexes it."""
    # Given an app whose storage path does not exist on disk yet
    src, dest1, _ = filesystem
    missing_storage_path = dest1 / "not_yet_created"
    app = ezbak(name="t", source_paths=[src], storage_paths=[missing_storage_path])

    # When restoring to an existing directory
    result = app.restore_backup(restore_path=tmp_path)

    # Then no backup is found, but the storage path now exists (created during indexing)
    assert result is False
    assert missing_storage_path.exists()
