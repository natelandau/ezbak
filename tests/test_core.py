"""Tests for the merged EZBak core class."""

import pytest
from pydantic import ValidationError

from ezbak.constants import StorageType
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


def test_backends_local_only_from_storage_paths(filesystem):
    """Verify only a local backend is built when only storage_paths are set."""
    # Given a config with local destinations and no bucket
    src, dest1, _ = filesystem
    app = ezbak(name="t", source_paths=[src], storage_paths=[dest1])

    # When inspecting derived backends
    types = {b.storage_type for b in app.backends}

    # Then only the local backend exists
    assert types == {StorageType.LOCAL}


def test_no_destination_is_rejected(filesystem):
    """Verify a config with neither storage_paths nor a bucket is invalid."""
    src, _, _ = filesystem
    # Given no destination at all
    # When constructing the config
    # Then validation fails
    with pytest.raises(ValidationError):
        ezbak(name="t", source_paths=[src])
