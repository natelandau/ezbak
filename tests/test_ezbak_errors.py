"""Test EZBak errors."""

import pytest

from ezbak import ezbak


def test_no_name(filesystem):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem
    with pytest.raises(ValueError, match="No backup name provided"):
        ezbak(
            # name="test",
            sources=[src_dir],
            destinations=[dest1],
        )


def test_sources(filesystem):
    """Test EZBak errors."""
    _, dest1, _ = filesystem
    with pytest.raises(ValueError, match="No source paths provided"):
        ezbak(
            name="test",
            sources=[],
            destinations=[dest1],
        )


def test_sources_not_found(filesystem):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem
    with pytest.raises(FileNotFoundError, match="Source does not exist"):
        ezbak(
            name="test",
            sources=[src_dir / "not_found"],
            destinations=[dest1],
        )


def test_destinations(filesystem):
    """Test EZBak errors."""
    src_dir, _, _ = filesystem
    with pytest.raises(ValueError, match="No destination paths provided"):
        ezbak(
            name="test",
            sources=[src_dir],
            destinations=[],
        )


def test_create_destination_dir(filesystem):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem

    new_dest = dest1 / "new_dir"
    assert not new_dest.exists()

    ezbak(
        name="test",
        sources=[src_dir],
        destinations=[new_dest],
    )

    assert new_dest.exists()
    assert new_dest.is_dir()


def test_restore_no_dest(filesystem, tmp_path, debug, clean_stderr):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem

    backup_manager = ezbak(
        name="test",
        sources=[src_dir],
        destinations=[dest1],
    )
    backup_manager.create_backup()
    assert not backup_manager.restore_latest_backup(tmp_path / "new_dest")
    output = clean_stderr()
    # debug(output)
    assert "ERROR    | Restore destination does not exist:" in output


def test_restore_dest_not_dir(filesystem, tmp_path, debug, clean_stderr):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem

    new_dest = dest1 / "file.txt"
    new_dest.touch()

    backup_manager = ezbak(
        name="test",
        sources=[src_dir],
        destinations=[dest1],
    )
    backup_manager.create_backup()
    assert not backup_manager.restore_latest_backup(new_dest)
    output = clean_stderr()
    # debug(output)
    assert "ERROR    | Restore destination is not a directory" in output


def test_restore_no_backup(filesystem, tmp_path, debug, clean_stderr):
    """Test EZBak errors."""
    src_dir, dest1, _ = filesystem

    backup_manager = ezbak(
        name="test",
        sources=[src_dir],
        destinations=[dest1],
    )
    # backup_manager.create_backup()
    assert not backup_manager.restore_latest_backup(tmp_path)
    output = clean_stderr()
    # debug(output)
    assert "ERROR    | No backup found to restore" in output
