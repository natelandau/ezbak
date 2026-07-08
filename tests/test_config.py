"""Tests for BackupConfig and EnvConfig."""

import pytest
from pydantic import ValidationError

from ezbak.config import BackupConfig
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


def test_retention_policy_derivation_count_based():
    """Verify max_backups yields a count-based retention policy."""
    # Given a config with max_backups
    config = BackupConfig(name="x", storage_paths=["/tmp"], max_backups=5)  # noqa: S108

    # When reading the derived policy
    policy = config.retention_policy

    # Then it is count-based
    assert policy.policy_type == RetentionPolicyType.COUNT_BASED
