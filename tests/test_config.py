"""Tests for BackupConfig and EnvConfig."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from ezbak.config import BackupConfig, coerce_path_list
from ezbak.constants import RetentionPolicyType
from ezbak.env import EnvConfig


def test_backupconfig_requires_name():
    """Verify BackupConfig rejects a missing name."""
    # Given no name and a destination
    # When constructing the config
    # Then validation fails
    with pytest.raises(ValidationError):
        BackupConfig(storage_paths=["/tmp"])  # noqa: S108


def test_backupconfig_does_not_read_env(monkeypatch):
    """Verify BackupConfig ignores EZBAK_ environment variables."""
    # Given an env var that would set the name
    monkeypatch.setenv("EZBAK_NAME", "from-env")

    # When building a plain BackupConfig with an explicit name
    config = BackupConfig(name="explicit", storage_paths=["/tmp"])  # noqa: S108

    # Then the env var is not consulted
    assert config.name == "explicit"


def test_envconfig_reads_env(monkeypatch):
    """Verify EnvConfig loads EZBAK_-prefixed environment variables."""
    # Given EZBAK_ env vars
    monkeypatch.setenv("EZBAK_NAME", "from-env")
    monkeypatch.setenv("EZBAK_STORAGE_PATHS", "/tmp")  # noqa: S108

    # When building an EnvConfig
    config = EnvConfig()

    # Then values come from the environment
    assert config.name == "from-env"


def test_envconfig_is_a_backupconfig():
    """Verify EnvConfig is substitutable for BackupConfig."""
    # Given/When an EnvConfig type
    # Then it is a subclass of BackupConfig (so EZBak(EnvConfig()) works)
    assert issubclass(EnvConfig, BackupConfig)


def test_coerce_path_list_empty_string_returns_empty():
    """Verify an empty string yields no paths instead of a phantom cwd entry."""
    # Given an empty string (e.g. an unset EZBAK_STORAGE_PATHS env var)
    # When coercing, then no paths are produced
    assert coerce_path_list("") == []


def test_envconfig_reads_restore_date(monkeypatch):
    """Verify EnvConfig loads EZBAK_RESTORE_DATE into restore_date."""
    # Given the env var set alongside the required fields
    monkeypatch.setenv("EZBAK_NAME", "from-env")
    monkeypatch.setenv("EZBAK_STORAGE_PATHS", "/tmp")  # noqa: S108
    monkeypatch.setenv("EZBAK_RESTORE_DATE", "20250102")

    # When building an EnvConfig
    config = EnvConfig()

    # Then restore_date is populated from the environment
    assert config.restore_date == "20250102"


def test_coerce_path_list_skips_blank_segments():
    """Verify blank comma segments do not inject a phantom cwd path."""
    # Given a comma list with an empty middle segment
    result = coerce_path_list("/tmp/a,,/tmp/b")  # noqa: S108

    # Then only the two real paths are returned, no cwd entry
    assert result == [Path("/tmp/a"), Path("/tmp/b")]  # noqa: S108


def test_coerce_path_list_strips_whitespace_around_entries():
    """Verify padded comma entries resolve to the real path, not a cwd-relative one."""
    # Given a comma list with spaces around each entry (e.g. "/tmp/a, /tmp/b")
    result = coerce_path_list("/tmp/a, /tmp/b")  # noqa: S108

    # Then each entry is stripped to an absolute path, not Path(" /tmp/b") under cwd
    assert result == [Path("/tmp/a"), Path("/tmp/b")]  # noqa: S108


def test_retention_policy_derivation_count_based():
    """Verify max_backups yields a count-based retention policy."""
    # Given a config with max_backups
    config = BackupConfig(name="x", storage_paths=["/tmp"], max_backups=5)  # noqa: S108

    # When reading the derived policy
    policy = config.retention_policy

    # Then it is count-based
    assert policy.policy_type == RetentionPolicyType.COUNT_BASED


def test_write_checksums_defaults_true():
    """Verify write_checksums defaults to True."""
    # Given a minimal config with no explicit write_checksums
    config = BackupConfig(name="test", storage_paths=["/tmp/x"])  # noqa: S108

    # Then it defaults to writing checksum sidecars
    assert config.write_checksums is True
