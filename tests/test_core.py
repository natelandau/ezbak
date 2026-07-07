"""Tests for the merged EZBak core class."""

from ezbak.core import EZBak, ezbak


def test_ezbak_factory_returns_core(filesystem):
    """Verify the ezbak() convenience returns an EZBak instance."""
    # Given source and destination directories
    src, dest1, _ = filesystem

    # When building via the convenience factory
    app = ezbak(name="test", source_paths=[src], storage_paths=[dest1])

    # Then an EZBak core is returned with the config attached
    assert isinstance(app, EZBak)
    assert app.settings.name == "test"


def test_ezbak_create_backup_writes_archive(filesystem):
    """Verify create_backup produces a discoverable backup."""
    # Given a configured EZBak
    src, dest1, _ = filesystem
    app = ezbak(name="test", source_paths=[src], storage_paths=[dest1])

    # When a backup is created
    app.create_backup()

    # Then it appears in the listing
    assert len(app.list_backups()) == 1
